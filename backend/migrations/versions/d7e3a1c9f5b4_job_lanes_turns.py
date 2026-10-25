"""Job lanes as a column, for per-user turns in each lane

Revision ID: d7e3a1c9f5b4
Revises: b3e7f1a9d5c2
Create Date: 2026-10-24 09:00:00

- jobs.lane: the worker lane that runs the job (core/jobs.py LANE_KINDS), written by enqueue. The
  queued and running jobs of the lanes' kinds get theirs here; the worker sets it again at every
  start (core/jobs.relane), so a kind that moves to another lane takes its queued jobs along.
- ix_jobs_turns (lane, user_id, run_at, id) of the queued jobs: a claim finds the users with due
  jobs in its lane one index probe per user, and the earliest due job of the user whose turn it is.
- ix_jobs_upstream_account: the same for the writes to the mail servers, by mail account
  (payload connector_id), so a claim skips an account that has a write running.

The indexes are built CONCURRENTLY outside the migration transaction (see e5b1c8d3a7f2).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd7e3a1c9f5b4'
down_revision: str | Sequence[str] | None = 'b3e7f1a9d5c2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The lanes as they were when this migration was written (core/jobs.py LANE_KINDS).
LANES = {
    "upstream": ["connector.write_back", "connector.folder_op", "connector.retention", "connector.delete_forever",
                 "sent.provider_copy", "outbox.verify_sent"],
    "caldav": ["calendar.sync", "calendar.check", "calendar.itip_incoming", "calendar.rematerialize",
               "calendar.reorder", "calendar.webcal_refresh"],
    "review": ["assistant.review"],
    "images": ["images.prefetch"],
    "maintenance": ["export.user", "export.calendars", "export.notes", "export.group_calendars",
                    "intel.train_all", "people.rescore", "contacts.suggest", "contacts.signatures",
                    "kb.extract", "db.dump", "sent.backfill"],
    "backfill": ["intel.backfill", "intel.embed_pending"],
}

INDEXES = {
    "ix_jobs_turns": "jobs (lane, user_id, run_at, id) WHERE status = 'queued'",
    "ix_jobs_upstream_account": "jobs ((payload->>'connector_id'), run_at, id) "
                                "WHERE status = 'queued' AND lane = 'upstream'",
}


def _create(name: str, spec: str) -> None:
    valid = op.get_bind().execute(sa.text(
        "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n"),
        {"n": name}).scalar()
    if valid is False:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {spec}")


def upgrade() -> None:
    op.add_column("jobs", sa.Column("lane", sa.String(20), nullable=False, server_default="main"))
    for lane, kinds in LANES.items():
        op.get_bind().execute(sa.text(
            "UPDATE jobs SET lane = :l WHERE status IN ('queued', 'running') AND kind = ANY(:k)"),
            {"l": lane, "k": kinds})
    with op.get_context().autocommit_block():
        for name, spec in INDEXES.items():
            _create(name, spec)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in reversed(INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.drop_column("jobs", "lane")
