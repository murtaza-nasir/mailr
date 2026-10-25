"""Indexes for large mailboxes and a long job queue

Revision ID: e5b1c8d3a7f2
Revises: c1e7a4b9d2f3
Create Date: 2026-10-23 18:00:00

- jobs: partial indexes of the queued jobs for the lanes' claims (core/jobs.py _claim), in run_at
  order for the main lane and by kind for the others, and (status, created_at) for pruning
  finished jobs. A backlog of one kind no longer makes every other lane read it on each claim.

Every index is built CONCURRENTLY outside the migration transaction, so writes go on while it is
built; an INVALID index left by a failed build is dropped and rebuilt by the next upgrade.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e5b1c8d3a7f2'
down_revision: str | Sequence[str] | None = 'c1e7a4b9d2f3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = {
    "ix_jobs_queued": "jobs (run_at, id) WHERE status = 'queued'",
    "ix_jobs_queued_kind": "jobs (kind, run_at, id) WHERE status = 'queued'",
    "ix_jobs_status_created": "jobs (status, created_at)",
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
