"""Settings → Activity: a partial index over the audit rows that are activity entries

Revision ID: d7a3f9c1e2b4
Revises: d1e5a9c3f7b2
Create Date: 2026-10-07 01:00:00

The activity index (core/activity.py). An activity entry is an audit_log row whose result carries an
`activity` object, or an administrator's notice (action admin_notice.*). GET /activity reads a
person's entries newest first; the rest of the audit log (reads by agents, every agenda request)
is far larger, so the entries get an index of their own. The predicate is the exact text the
query uses (core/activity.WHERE), so the planner can prove a row is in the index.

Built CONCURRENTLY outside the migration transaction; an INVALID index left by a failed build is
dropped and rebuilt by the next upgrade.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd7a3f9c1e2b4'
down_revision: str | Sequence[str] | None = 'd1e5a9c3f7b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = "ix_audit_activity"
SPEC = "audit_log (user_id, id) WHERE (result ? 'activity' OR action LIKE 'admin_notice.%')"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        valid = op.get_bind().execute(sa.text(
            "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n"),
            {"n": NAME}).scalar()
        if valid is False:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {NAME}")
        op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {NAME} ON {SPEC}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {NAME}")
