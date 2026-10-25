"""Speakr: participant aliases (integrations/speakr/participants.py)

Revision ID: a3c9e5f71b24
Revises: d4b7f2a9c1e6
Create Date: 2026-10-01 21:00:00

A recording's participant names are the strings Speakr held when the recording was made, so one
person can appear as "Rowan" in older recordings and "Rowan hale" in newer ones. A row of
`speakr_participant_aliases` says, for one connector, that a name is the same person as another
identity (`alias`), that a suggested merge was dismissed (`dismissed`), or that a name stands on
its own and the user's own first-name rule does not apply to it (`separate`).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a3c9e5f71b24'
down_revision: Union[str, Sequence[str], None] = 'd4b7f2a9c1e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "speakr_participant_aliases",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connector_id", sa.Integer(), sa.ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("target", sa.String(330), nullable=False, server_default=""),
        sa.Column("target_label", sa.String(300), nullable=False, server_default=""),
        sa.Column("state", sa.String(12), nullable=False, server_default="alias"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("connector_id", "name", "state", "target", name="uq_speakr_participant_aliases"),
    )
    op.create_index("ix_speakr_participant_aliases_conn", "speakr_participant_aliases", ["connector_id", "name"])


def downgrade() -> None:
    op.drop_index("ix_speakr_participant_aliases_conn", table_name="speakr_participant_aliases")
    op.drop_table("speakr_participant_aliases")
