"""Persistent schedule, pg_stat_statements, autovacuum settings for the busiest tables

Revision ID: b9e4c2a7d5f1
Revises: a6d2f9c4e1b8
Create Date: 2026-10-14 10:00:00

- schedule_runs: when each periodic task of the worker's scheduler (core/tasks.py) last ran. The
  times were kept in memory, so every worker start ran the 12-hour and daily tasks again (export,
  retraining and rescoring ran 36 times in 3 days instead of 6). A task is claimed with one
  conditional upsert, so two workers (an overlapping deploy) never run it twice.
- pg_stat_statements: statement statistics for the slow-query work. The library must be in
  shared_preload_libraries (deploy/docker-compose.yml) for the view to answer; creating the
  extension works without it. It is not a trusted extension, so a role without superuser rights
  (a development server) skips it with a notice instead of failing the upgrade.
- autovacuum: messages, message_passages, label_decisions and jobs change a small part of a large
  table every day; the default thresholds (20 % dead rows) let label_decisions reach 13.7 % dead
  rows and leave the planner's statistics days old. 5 % (vacuum) and 2 % (analyse) keep them fresh.
  The jobs table turns over completely every few days and gets 1 %.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9e4c2a7d5f1"
down_revision: str | Sequence[str] | None = "a6d2f9c4e1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUTOVACUUM = {
    "messages": (0.05, 0.02),
    "message_passages": (0.05, 0.02),
    "label_decisions": (0.05, 0.02),
    "jobs": (0.01, 0.01),
}


def upgrade() -> None:
    op.create_table(
        "schedule_runs",
        sa.Column("name", sa.String(100), primary_key=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_stat_statements') THEN
            CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
          END IF;
        EXCEPTION WHEN insufficient_privilege THEN
          RAISE NOTICE 'pg_stat_statements not created: the role is not a superuser';
        END
        $$""")
    for table, (vac, ana) in AUTOVACUUM.items():
        op.execute(f"ALTER TABLE {table} SET (autovacuum_vacuum_scale_factor = {vac}, "
                   f"autovacuum_analyze_scale_factor = {ana})")


def downgrade() -> None:
    for table in AUTOVACUUM:
        op.execute(f"ALTER TABLE {table} RESET (autovacuum_vacuum_scale_factor, autovacuum_analyze_scale_factor)")
    op.drop_table("schedule_runs")
