"""Speakr auto-connect at sign-in: per-user state

- users.speakr_auto: server-owned state of connecting the instance's Speakr at sign-in
  (integrations/speakr/auto.py): {"url", "state", "reason", "at"}. NULL: never tried.

Re-runnable: the column is added only when it is missing.

Revision ID: c5d8e2a9f3b7
Revises: a7c3e9d2b4f1
Create Date: 2026-11-13 07:00:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = 'c5d8e2a9f3b7'
down_revision: str | Sequence[str] | None = 'a7c3e9d2b4f1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS speakr_auto JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS speakr_auto")
