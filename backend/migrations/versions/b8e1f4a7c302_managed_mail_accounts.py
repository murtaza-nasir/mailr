"""managed mail accounts: an account an administrator connected for a member

- connectors.managed_by_admin: true when an administrator connected the account for its owner
  (Admin → Users, or an email preset at the first sign-in). The member sees "Connected by your
  administrator" and may disconnect it.
- connectors.managed_by: the administrator who connected it (SET NULL when that account goes).

Safe to run again: each column is added only when it is missing.

Revision ID: b8e1f4a7c302
Revises: a4c7e2b9d815
Create Date: 2026-10-07 22:00:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = "b8e1f4a7c302"
down_revision: str | Sequence[str] | None = "a4c7e2b9d815"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE connectors ADD COLUMN IF NOT EXISTS managed_by_admin BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE connectors ADD COLUMN IF NOT EXISTS managed_by INTEGER "
               "REFERENCES users(id) ON DELETE SET NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE connectors DROP COLUMN IF EXISTS managed_by")
    op.execute("ALTER TABLE connectors DROP COLUMN IF EXISTS managed_by_admin")
