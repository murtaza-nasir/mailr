"""Speakr: the summary of each recording, kept for search

Revision ID: c8e2f4a6b1d3
Revises: a3c9e7f1d5b8
Create Date: 2026-10-02 00:30:00

The column starts empty with summary_at NULL, so the next sync of each connector reads the
summaries (a bounded number per pass) and fills the index.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'c8e2f4a6b1d3'
down_revision: Union[str, Sequence[str], None] = 'a3c9e7f1d5b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = """
ALTER TABLE speakr_recordings ADD COLUMN IF NOT EXISTS summary_text text NOT NULL DEFAULT '';
ALTER TABLE speakr_recordings ADD COLUMN IF NOT EXISTS summary_hash varchar(64) NOT NULL DEFAULT '';
ALTER TABLE speakr_recordings ADD COLUMN IF NOT EXISTS summary_at timestamptz;
ALTER TABLE speakr_recordings ADD COLUMN IF NOT EXISTS tsv tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
    setweight(to_tsvector('simple', left(coalesce(summary_text, ''), 100000)), 'B')) STORED;
CREATE INDEX IF NOT EXISTS ix_speakr_recordings_tsv ON speakr_recordings USING gin (tsv)
"""


def upgrade() -> None:
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_speakr_recordings_tsv")
    for col in ("tsv", "summary_at", "summary_hash", "summary_text"):
        op.execute(f"ALTER TABLE speakr_recordings DROP COLUMN IF EXISTS {col}")
