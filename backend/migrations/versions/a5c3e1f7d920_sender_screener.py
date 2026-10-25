"""Sender screener

Revision ID: a5c3e1f7d920
Revises: b4d7e1a9c2f5
Create Date: 2026-10-04 20:00:00

core/screener.py, site/docs/user-guide/screener.md.

- screener_senders: one row per screened sender key (an address, or a domain for a corporate
  domain) with its status: pending (waiting in the Screener), allowed, blocked or label.
- messages.screened_key: the key of the pending sender whose mail this message is, while it is
  held out of the Inbox. NULL for every other message (adding the nullable column rewrites
  nothing).
- ix_msg_screened: a partial index on the held messages (few rows), built CONCURRENTLY outside the
  migration transaction; an INVALID index left by a failed build is dropped and rebuilt.

Re-runnable: every step checks first, because the index block commits what came before it, so an
interrupted upgrade leaves the table and the column in place without the version row.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a5c3e1f7d920'
down_revision: str | Sequence[str] | None = 'b4d7e1a9c2f5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = ("ix_msg_screened", "messages (user_id, screened_key) WHERE screened_key IS NOT NULL")


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("screener_senders"):
        op.create_table(
            "screener_senders",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("key", sa.String(320), nullable=False),
            sa.Column("email", sa.String(320), nullable=False, server_default=""),
            sa.Column("name", sa.String(300), nullable=False, server_default=""),
            sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
            sa.Column("label_id", sa.Integer(), sa.ForeignKey("labels.id", ondelete="SET NULL")),
            sa.Column("block_to", sa.String(10), nullable=False, server_default="spam"),
            sa.Column("decided_by", sa.String(60), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("decided_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("user_id", "key", name="uq_screener_senders"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_screener_senders_status ON screener_senders (user_id, status)")
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS screened_key VARCHAR(320)")
    name, spec = INDEX
    with op.get_context().autocommit_block():
        valid = op.get_bind().execute(sa.text(
            "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n"),
            {"n": name}).scalar()
        if valid is False:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
        op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {spec}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX[0]}")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS screened_key")
    op.execute("DROP TABLE IF EXISTS screener_senders")
