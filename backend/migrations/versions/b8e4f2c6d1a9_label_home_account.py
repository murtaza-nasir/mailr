"""labels.home_account_id: the mail account a folder was made in

Revision ID: b8e4f2c6d1a9
Revises: a3d7c9e2f415
Create Date: 2026-10-08 12:00:00

- labels.home_account_id: the mail account (connector) a label was created under, from "New
  folder" in that account's group of the mail navigation. NULL for every other label. The label stays one label, shared by all accounts;
  the home only decides where it is listed while it holds no mail of that account. Removing the
  account sets it back to NULL.

Prod-safe and re-runnable: a nullable column with no default (no table rewrite); the foreign key
is added NOT VALID (no long lock) and validated in a second step, each only when missing.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b8e4f2c6d1a9'
down_revision: str | Sequence[str] | None = 'a3d7c9e2f415'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK = "fk_labels_home_account_id"


def upgrade() -> None:
    op.execute("ALTER TABLE labels ADD COLUMN IF NOT EXISTS home_account_id INTEGER")
    state = op.get_bind().execute(sa.text(
        "SELECT convalidated FROM pg_constraint WHERE conname = :n AND conrelid = 'labels'::regclass"),
        {"n": FK}).scalar()
    if state is None:
        op.execute(f"ALTER TABLE labels ADD CONSTRAINT {FK} FOREIGN KEY (home_account_id) "
                   "REFERENCES connectors (id) ON DELETE SET NULL NOT VALID")
    if state is not True:
        op.execute(f"ALTER TABLE labels VALIDATE CONSTRAINT {FK}")


def downgrade() -> None:
    op.execute(f"ALTER TABLE labels DROP CONSTRAINT IF EXISTS {FK}")
    op.execute("ALTER TABLE labels DROP COLUMN IF EXISTS home_account_id")
