"""Live editing: versions of drafts, events, tasks, memories and filter rules

Revision ID: c7e2a4f9b1d3
Revises: e8b3d5f1a7c4
Create Date: 2026-10-04 23:30:00

core/live.py. item_versions keeps the recent versions of each item
people and agents edit live: the newest 20, and every version younger than 24 hours (at most
100). A write based on an older version is merged three ways against the stored content of that
version; without the history a server could only overwrite or refuse.

Re-runnable (the lesson of b4d7e1a9c2f5 and e8b3d5f1a7c4): each step checks first, so an upgrade
that stopped after creating the table but before the version row was written runs again cleanly.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = 'c7e2a4f9b1d3'
down_revision: str | Sequence[str] | None = 'e8b3d5f1a7c4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("item_versions"):
        op.create_table(
            "item_versions",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(12), nullable=False),
            sa.Column("item_id", sa.String(300), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("content", JSONB(), nullable=False),
            sa.Column("by", sa.String(80), nullable=False, server_default="user"),
            sa.Column("origin", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_item_versions') THEN
                ALTER TABLE item_versions ADD CONSTRAINT uq_item_versions UNIQUE (user_id, kind, item_id, version);
            END IF;
        END $$
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_item_versions_created ON item_versions (created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS item_versions")
