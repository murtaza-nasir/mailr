"""Note and table shares

Revision ID: 7c4e2a9d51b3
Revises: 260bc64180d2
Create Date: 2026-10-06 03:00:00

Sharing notes and tables: `note_shares`
holds who a note or a table is shared with (a mailr user or a mailr group) and at which level; a
user's row with `left_at` set hides the item from them after they leave it. `note_viewer_state`
gains `seen_at`, the time this viewer last opened the item, for the "changed since you looked" dot.

A new table and a nullable column without a default, so nothing rewrites or locks an existing
table for long. Re-runnable: each part is made only when it is missing.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '7c4e2a9d51b3'
down_revision: str | Sequence[str] | None = '260bc64180d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    have = set(sa.inspect(op.get_bind()).get_table_names())
    if "note_shares" not in have:
        op.create_table(
            "note_shares",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("item_kind", sa.String(8), nullable=False),
            sa.Column("item_id", sa.BigInteger(), nullable=False),
            sa.Column("principal_kind", sa.String(8), nullable=False),
            sa.Column("principal", sa.String(200), nullable=False),
            sa.Column("level", sa.String(8), nullable=False),
            sa.Column("created_by", sa.Integer()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("left_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("item_kind", "item_id", "principal_kind", "principal", name="uq_note_shares"),
        )
    op.create_index("ix_note_shares_principal", "note_shares", ["principal_kind", "principal"], if_not_exists=True)
    op.execute("ALTER TABLE note_viewer_state ADD COLUMN IF NOT EXISTS seen_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE note_viewer_state DROP COLUMN IF EXISTS seen_at")
    op.drop_index("ix_note_shares_principal", table_name="note_shares", if_exists=True)
    op.execute("DROP TABLE IF EXISTS note_shares")
