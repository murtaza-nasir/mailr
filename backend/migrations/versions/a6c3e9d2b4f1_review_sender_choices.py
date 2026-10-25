"""Remembered review choices: review_sender_choices

Revision ID: a6c3e9d2b4f1
Revises: e5b2c8d4a1f7
Create Date: 2026-10-21 12:00:00

- review_sender_choices: per user and sender address, the move the user chose on a review card
  where it differed from the review's proposal (agent/review.py learn). Later reviews give that
  sender's conversations the move without the model.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a6c3e9d2b4f1'
down_revision: str | Sequence[str] | None = 'e5b2c8d4a1f7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_sender_choices",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender", sa.String(320), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("label_id", sa.Integer(), sa.ForeignKey("labels.id", ondelete="CASCADE")),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "sender", name="uq_review_sender_choices_user_sender"),
    )
    op.create_index("ix_review_sender_choices_user_id", "review_sender_choices", ["user_id"])


def downgrade() -> None:
    op.drop_table("review_sender_choices")
