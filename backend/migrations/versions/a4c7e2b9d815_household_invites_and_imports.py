"""household invites and import batches

- household_invites: a one-time invite link for a household member (core/invites.py). Only the
  token's SHA-256 is stored.
- import_batches: one row per import (Google Keep, .ics, .vcf) with what it made, so one step
  undoes it (core/imports.py).

Safe to run again: each table is created only when it is missing.

Revision ID: a4c7e2b9d815
Revises: d7a2c9e4f1b8
Create Date: 2026-10-07 02:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4c7e2b9d815"
down_revision: str | Sequence[str] | None = "d7a2c9e4f1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("household_invites"):
        op.create_table(
            "household_invites",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("name", sa.String(200), nullable=False, server_default=""),
            sa.Column("defaults", postgresql.JSONB(), nullable=False, server_default="{}"),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("used_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not insp.has_table("import_batches"):
        op.create_table(
            "import_batches",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(12), nullable=False),
            sa.Column("source", sa.String(300), nullable=False, server_default=""),
            sa.Column("counts", postgresql.JSONB(), nullable=False, server_default="{}"),
            sa.Column("undo", postgresql.JSONB(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_import_batches_user_id", "import_batches", ["user_id"])


def downgrade() -> None:
    op.drop_table("import_batches")
    op.drop_table("household_invites")
