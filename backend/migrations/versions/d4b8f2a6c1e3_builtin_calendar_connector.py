"""Built-in calendars: connectors.builtin

Revision ID: d4b8f2a6c1e3
Revises: c7e2a4f9b1d3
Create Date: 2026-10-05 10:00:00

The built-in calendars on Stalwart. A user's built-in
calendar is a CalDAV connector with builtin = true (calendar/builtin.py). At most one per user:
the partial unique index ux_connectors_builtin makes the backfill idempotent when the API and the
worker run it at the same time.

Re-runnable: the column is added only when missing; the index is built CONCURRENTLY outside the
migration transaction, and an INVALID index left by a failed build is dropped and rebuilt.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd4b8f2a6c1e3'
down_revision: str | Sequence[str] | None = 'c7e2a4f9b1d3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ux_connectors_builtin"


def upgrade() -> None:
    op.execute("ALTER TABLE connectors ADD COLUMN IF NOT EXISTS builtin BOOLEAN NOT NULL DEFAULT false")
    with op.get_context().autocommit_block():
        valid = op.get_bind().execute(sa.text(
            "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n"),
            {"n": INDEX}).scalar()
        if valid is False:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute(f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} ON connectors (user_id) WHERE builtin")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
    op.execute("ALTER TABLE connectors DROP COLUMN IF EXISTS builtin")
