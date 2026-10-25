"""Indexes for the attachments page and searches that read every message

Revision ID: a8d4e2f6c9b3
Revises: f2c6a9d4b8e1
Create Date: 2026-10-23 20:00:00

- ix_msg_attachments: the messages with attachments, newest first (GET /attachments,
  api/mail_read.py list_attachments). The page sorted every such message before it could stop.
- Trigram indexes on the text of messages."to", cc, bcc and attachments, and on the list id: the
  substring searches to:, cc:, bcc:, deliveredto:, filename: and list: (core/search.py) read every
  message's JSON before. The GIN jsonb_path_ops indexes on "to" and cc serve containment only.
- ix_msg_from_lower: the sender in lower case, as a dozen lookups compare it (contact groups for
  tokens, trusted senders, deadlines, the account index, reviews); ix_msg_from cannot serve
  lower(from_email), so each was a scan of the user's mail.

The indexes are built CONCURRENTLY outside the migration transaction; an INVALID index left by a
failed build is dropped and rebuilt by the next upgrade.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a8d4e2f6c9b3'
down_revision: str | Sequence[str] | None = 'f2c6a9d4b8e1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = {
    "ix_msg_attachments": "messages (user_id, coalesce(date, '-infinity'::timestamptz) DESC, id DESC) "
                          "WHERE has_attachments IS true AND deleted IS false AND draft IS false",
    "ix_msg_to_trgm": 'messages USING gin (lower("to"::text) gin_trgm_ops)',
    "ix_msg_cc_trgm": "messages USING gin (lower(cc::text) gin_trgm_ops)",
    "ix_msg_bcc_trgm": "messages USING gin (lower(bcc::text) gin_trgm_ops)",
    "ix_msg_attachments_trgm": "messages USING gin (lower(attachments::text) gin_trgm_ops)",
    "ix_msg_list_trgm": "messages USING gin (lower(coalesce(list_id, '')) gin_trgm_ops)",
    "ix_msg_from_lower": "messages (user_id, lower(from_email))",
}


def _create(name: str, spec: str) -> None:
    valid = op.get_bind().execute(sa.text(
        "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n"),
        {"n": name}).scalar()
    if valid is False:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {spec}")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, spec in INDEXES.items():
            _create(name, spec)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in reversed(INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
