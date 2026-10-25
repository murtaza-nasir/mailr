"""Recipient, snooze and log-pruning indexes; the model of each message vector

Revision ID: e8b3d5f1a7c4
Revises: a5c3e1f7d920
Create Date: 2026-10-04 22:00:00

- ix_msg_to, ix_msg_cc: GIN jsonb_path_ops on messages."to" and messages.cc, for the containment
  test `"to" @> '[{"email": …}]'` of the person page (api/org_routes.py) and the assistant's
  context (agent/context.py). Without them each person view and each context build read every
  message row. jsonb_path_ops answers @> only, which is the one operator these queries use, and
  is about a third of the size of the default jsonb_ops.
- ix_conv_snoozed: the snoozed conversations of every user (few rows), for the per-minute
  `_wake_overdue_snoozes` scan (core/tasks.py); ix_conv_snooze leads with user_id and cannot
  serve it.
- ix_change_log_created, ix_audit_log_created: the daily pruning by age in `_cleanup_db`.
- messages.embed_model: the embedding model key (intel/llm.embed_model_key) of messages.embedding,
  as message_passages.embed_model already records for passages. Vector queries compare only
  vectors of the current model. NULL: no vector, or a vector stored before this column. Such
  vectors are given the current model's key by `mailr embed-model-backfill`, which assumes that
  they all came from the instance's current embedding model (see that command). The migration
  does not fill the column: the current model is an instance setting read through the
  application's model router, and rewriting every embedded message row (each also an HNSW index
  insert) would hold the app's start for minutes at production size. Adding the nullable column
  without a default rewrites nothing.

Re-runnable: every step checks first, because the index block commits what came before it, so an
interrupted upgrade leaves the column in place without the version row. The indexes are built
CONCURRENTLY outside the migration transaction; an INVALID index left by a failed build is dropped
and rebuilt by the next upgrade.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e8b3d5f1a7c4'
down_revision: str | Sequence[str] | None = 'a5c3e1f7d920'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = {
    "ix_msg_to": 'messages USING gin ("to" jsonb_path_ops)',
    "ix_msg_cc": "messages USING gin (cc jsonb_path_ops)",
    "ix_conv_snoozed": "conversations (snoozed_until) WHERE snoozed_until IS NOT NULL",
    "ix_change_log_created": "change_log (created_at)",
    "ix_audit_log_created": "audit_log (created_at)",
}


def upgrade() -> None:
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS embed_model VARCHAR(200)")
    with op.get_context().autocommit_block():
        for name, spec in INDEXES.items():
            valid = op.get_bind().execute(sa.text(
                "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n"),
                {"n": name}).scalar()
            if valid is False:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {spec}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS embed_model")
