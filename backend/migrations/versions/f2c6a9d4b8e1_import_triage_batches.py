"""Imported mail is triaged in batches: messages.intel_state 'backfill' and its indexes

Revision ID: f2c6a9d4b8e1
Revises: e5b1c8d3a7f2
Create Date: 2026-10-23 19:00:00

- A history import marks its mail intel_state = 'backfill' and queues one intel.backfill job per
  user (core/ingest.py queue_intel) instead of one intel.message job per message in the main lane.
  The intel.message jobs still queued for mail older than three days become that: the messages are
  marked 'backfill', the jobs go, and each of their users gets an intel.backfill job.
- ix_msg_intel_todo: the mail the backfill takes, newest first (intel/tasks.py TODO_WHERE).
- ix_passage_message: a message's passages by its id alone. Building passages, re-embedding and the
  cascade from a deleted message looked them up through (user_id, message_id) without the user.

The indexes are built CONCURRENTLY outside the migration transaction; an INVALID index left by a
failed build is dropped and rebuilt by the next upgrade.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f2c6a9d4b8e1'
down_revision: str | Sequence[str] | None = 'e5b1c8d3a7f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = {
    "ix_msg_intel_todo": "messages (user_id, date DESC NULLS LAST) "
                         "WHERE intel_state IN ('backfill', 'pending') AND deleted IS false",
    "ix_passage_message": "message_passages (message_id)",
}


def _create(name: str, spec: str) -> None:
    valid = op.get_bind().execute(sa.text(
        "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n"),
        {"n": name}).scalar()
    if valid is False:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {spec}")


def upgrade() -> None:
    op.execute("""
        WITH moved AS (
            DELETE FROM jobs j USING messages m
            WHERE j.kind = 'intel.message' AND j.status = 'queued'
              AND m.id = (j.payload->>'message_id')::bigint AND m.intel_state = 'pending'
              AND coalesce(m.received_at, m.date) < now() - interval '3 days'
            RETURNING m.id, m.user_id),
        marked AS (
            UPDATE messages SET intel_state = 'backfill' WHERE id IN (SELECT id FROM moved)
            RETURNING user_id)
        INSERT INTO jobs (kind, payload, user_id, dedupe_key, run_at, status, attempts, max_attempts, error,
                          created_at)
        SELECT 'intel.backfill', jsonb_build_object('user_id', u), u, 'intel.backfill:' || u, now(), 'queued',
               0, 5, '', now()
        FROM (SELECT DISTINCT user_id AS u FROM marked) s
        ON CONFLICT (dedupe_key) WHERE status IN ('queued', 'running') DO NOTHING
    """)
    with op.get_context().autocommit_block():
        for name, spec in INDEXES.items():
            _create(name, spec)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in reversed(INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.execute("UPDATE messages SET intel_state = 'pending' WHERE intel_state = 'backfill'")
