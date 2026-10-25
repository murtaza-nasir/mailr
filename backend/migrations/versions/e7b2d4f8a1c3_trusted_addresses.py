"""Addresses that speak for you: trusted_addresses, assistant_orders.from_trusted (9 Oct 2026)

core/trusted.py, site/docs/user-guide/forwarded-notes.md.

- trusted_addresses: an address of the user's own that mailr is not connected to (a work account).
  It is trusted once the one-time code mailr mails to it is confirmed.
- assistant_orders.from_trusted: a message order's condition "mail I forward from my trusted
  addresses".

Safe to run again: the table is created only when missing, the column with ADD COLUMN IF NOT EXISTS
and a constant default (no table rewrite).

Revision ID: e7b2d4f8a1c3
Revises: e6b8d4f2a9c1
Create Date: 2026-10-09 23:30:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e7b2d4f8a1c3'
down_revision: str | Sequence[str] | None = 'e6b8d4f2a9c1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("trusted_addresses"):
        op.create_table(
            "trusted_addresses",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("code_hash", sa.String(64), nullable=False, server_default=""),
            sa.Column("code_expires_at", sa.DateTime(timezone=True)),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("confirmed_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("user_id", "email", name="uq_trusted_addresses"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_trusted_addresses_user_id ON trusted_addresses (user_id)")
    op.execute("ALTER TABLE assistant_orders ADD COLUMN IF NOT EXISTS from_trusted boolean NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE assistant_orders DROP COLUMN IF EXISTS from_trusted")
    op.execute("DROP TABLE IF EXISTS trusted_addresses")
