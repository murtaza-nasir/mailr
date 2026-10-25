"""Bulk review of existing mail: mail_reviews

Revision ID: e5b2c8d4a1f7
Revises: d9e4b7a1c3f8
Create Date: 2026-10-18 23:00:00

- mail_reviews: one bulk review (agent/review.py). It holds
  the frozen list of conversations, what was set aside without a model, the model's decisions and the
  progress, so a stopped or interrupted run continues, and the approval card it ended in.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e5b2c8d4a1f7'
down_revision: str | Sequence[str] | None = 'd9e4b7a1c3f8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_reviews",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_session_id", sa.String(40)),
        sa.Column("errand", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scope", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("instructions", sa.Text(), nullable=False, server_default=""),
        sa.Column("labels", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("ids", postgresql.ARRAY(sa.BigInteger()), nullable=False, server_default="{}"),
        sa.Column("kept", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("decisions", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("approval_id", sa.BigInteger()),
        sa.Column("model", sa.String(200), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_mail_reviews_user_id", "mail_reviews", ["user_id"])
    op.create_index("ix_mail_reviews_chat_session_id", "mail_reviews", ["chat_session_id"])


def downgrade() -> None:
    op.drop_table("mail_reviews")
