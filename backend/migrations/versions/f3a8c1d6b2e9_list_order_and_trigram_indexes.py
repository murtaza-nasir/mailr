"""List-order index, pg_trgm search indexes, bulk job progress and undo parts

Revision ID: f3a8c1d6b2e9
Revises: e7b2c9d4f1a6
Create Date: 2026-10-03 23:30:00

- ix_conv_user_order: conversations in list order, (user_id, coalesce(last_date, '-infinity')
  DESC, id DESC). Every list view orders by this expression and pages with a row comparison
  on it, so a page is an index range scan instead of a sort of the whole view.
- pg_trgm and GIN trigram indexes on messages.subject and on the sender (from_name || ' ' ||
  from_email, one index for both): the substring arms of a plain-word, from: and subject: search
  (ILIKE '%w%') become bitmap index scans. Two indexes, not three: every non-HOT update of a
  message (a label change) writes each GIN index.
  pg_trgm ships with the Postgres contrib modules in pgvector/pgvector:pg17 and is a trusted
  extension, so the database owner can create it without superuser rights.
- jobs.progress and job_undo_parts: a bulk job writes its progress and each chunk's undo record
  as small rows, instead of rewriting its whole JSONB payload after every chunk.

The indexes are built CONCURRENTLY outside the migration transaction, so reads and writes go on
while they build (about 90k messages: a few seconds each). A build that fails leaves an INVALID
index; the next upgrade drops and rebuilds it. The two table changes take only brief locks
(a nullable column without a default, and a new table).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f3a8c1d6b2e9'
down_revision: str | Sequence[str] | None = 'e7b2c9d4f1a6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = {
    "ix_conv_user_order": "conversations (user_id, coalesce(last_date, '-infinity'::timestamptz) DESC, id DESC)",
    "ix_msg_subject_trgm": "messages USING gin (subject gin_trgm_ops)",
    "ix_msg_sender_trgm": "messages USING gin ((from_name || ' ' || from_email) gin_trgm_ops)",
}


def upgrade() -> None:
    op.add_column("jobs", sa.Column("progress", sa.Integer(), nullable=True))
    op.create_table(
        "job_undo_parts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_job_undo_parts_job", "job_undo_parts", ["job_id", "kind"])
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
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
    op.drop_index("ix_job_undo_parts_job", table_name="job_undo_parts")
    op.drop_table("job_undo_parts")
    op.drop_column("jobs", "progress")
