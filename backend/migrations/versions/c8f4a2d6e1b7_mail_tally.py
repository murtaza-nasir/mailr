"""The mail tally: the grouped mail of the Subscriptions and Clean up pages, kept up to date

Revision ID: c8f4a2d6e1b7
Revises: d7e3a1c9f5b4
Create Date: 2026-10-24 12:00:00

See core/tally.py.

- mail_tally: per user, one row per sender, list, category, place (Inbox, Trash), age band,
  List-Unsubscribe, data class and protection of the conversation, with the counts and dates the
  pages show. The pages read these rows instead of grouping every message on each request.
- mail_tally_units: each conversation's share of those rows, so a change to a conversation
  replaces its share exactly, and a conversation with mail in several rows is counted once.
- mail_tally_convs: the row the upkeep locks per conversation.
- mail_tally_users: per user, whether the tally is ready and the date its age bands count from.

The tables start empty. The worker counts each user's mail a batch at a time after the upgrade
(the job mail.tally_build); until a user's count is ready, the pages group the mail as before.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c8f4a2d6e1b7'
down_revision: str | Sequence[str] | None = 'd7e3a1c9f5b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_tally",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_email", sa.String(320), nullable=False),
        sa.Column("list_id", sa.String(400), nullable=True),
        sa.Column("category", sa.String(20), nullable=True),
        sa.Column("loc", sa.SmallInteger(), nullable=False),
        sa.Column("band", sa.SmallInteger(), nullable=False),
        sa.Column("unsub", sa.Boolean(), nullable=False),
        sa.Column("local_only", sa.Boolean(), nullable=False),
        sa.Column("prot", sa.SmallInteger(), nullable=False),
        sa.Column("messages", sa.Integer(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("unread", sa.Integer(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("solo", sa.Integer(), nullable=False),
        sa.Column("oldest", sa.DateTime(timezone=True), nullable=True),
        sa.Column("newest", sa.DateTime(timezone=True), nullable=True),
        sa.Column("newest_read", sa.DateTime(timezone=True), nullable=True),
        sa.Column("newest_id", sa.BigInteger(), nullable=True),
    )
    op.create_index("uq_mail_tally", "mail_tally",
                    ["user_id", "from_email", "list_id", "category", "loc", "band", "unsub", "local_only", "prot"],
                    unique=True, postgresql_nulls_not_distinct=True)
    op.create_table(
        "mail_tally_units",
        sa.Column("conv", sa.BigInteger(), primary_key=True),
        sa.Column("cell_id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("messages", sa.Integer(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("unread", sa.Integer(), nullable=False),
        sa.Column("oldest", sa.DateTime(timezone=True), nullable=True),
        sa.Column("newest", sa.DateTime(timezone=True), nullable=True),
        sa.Column("newest_read", sa.DateTime(timezone=True), nullable=True),
        sa.Column("newest_id", sa.BigInteger(), nullable=False),
        sa.Column("spread", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_mail_tally_units_cell", "mail_tally_units", ["cell_id"])
    op.create_index("ix_mail_tally_units_user", "mail_tally_units", ["user_id"])
    op.create_index("ix_mail_tally_units_spread", "mail_tally_units", ["user_id"],
                    postgresql_where=sa.text("spread"))
    op.create_table(
        "mail_tally_convs",
        sa.Column("conv", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    )
    op.create_index("ix_mail_tally_convs_user_id", "mail_tally_convs", ["user_id"])
    op.create_table(
        "mail_tally_users",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("state", sa.String(10), nullable=False),
        sa.Column("anchor", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cursor", sa.BigInteger(), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("drifted", sa.Integer(), nullable=False),
        sa.Column("drifted_total", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("mail_tally_users")
    op.drop_table("mail_tally_convs")
    op.drop_table("mail_tally_units")
    op.drop_table("mail_tally")
