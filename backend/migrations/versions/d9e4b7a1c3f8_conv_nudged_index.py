"""Conversations that came back with no reply: an index for Today's follow-ups

Revision ID: d9e4b7a1c3f8
Revises: c4d8e1f2a6b3
Create Date: 2026-10-14 12:00:00

- ix_conv_nudged: conversations (user_id) WHERE nudged_at IS NOT NULL. GET /today lists the
  nudged conversations and the follow-ups due by the end of the day. ix_conv_follow_up covers the
  second half only, so the query read every conversation of the user (45,000 rows, 9 ms at the
  production size, measured 14 Oct 2026). With both partial indexes it is a BitmapOr of two
  small ranges.

Re-runnable: the index is built CONCURRENTLY outside the migration transaction; an INVALID index
left by a failed build is dropped and rebuilt by the next upgrade.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd9e4b7a1c3f8'
down_revision: str | Sequence[str] | None = 'c4d8e1f2a6b3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = {
    "ix_conv_nudged": "conversations (user_id) WHERE nudged_at IS NOT NULL",
}


def upgrade() -> None:
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
