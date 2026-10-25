"""Server-owned sign-in state moves out of users.settings

Revision ID: b5d1e8a3c7f2
Revises: c8f4a2d6e1b7
Create Date: 2026-10-24 18:00:00

- users.store_signin_locked: set while a disabled user's store password is replaced
  (core/users.lock_store_signin). It was the settings key `store_signin_locked`.
- users.signin_epoch: the counter in the known-client cookie stamp (api/auth_routes.signin_stamp),
  raised by Sign out everywhere and password resets. It was the settings key `signin_epoch`.

users.settings is what PATCH /me/settings writes, so neither belongs there. The stored values are
copied, then the keys are removed from settings.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b5d1e8a3c7f2'
down_revision: str | Sequence[str] | None = 'c8f4a2d6e1b7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The stored values, copied to the columns and removed from settings (also run by a test).
MOVE = (
    "UPDATE users SET store_signin_locked = true WHERE settings ->> 'store_signin_locked' IN ('true', '1')",
    "UPDATE users SET signin_epoch = (settings ->> 'signin_epoch')::int "
    "WHERE settings ->> 'signin_epoch' ~ '^[0-9]{1,9}$'",
    "UPDATE users SET settings = settings - 'store_signin_locked' - 'signin_epoch' "
    "WHERE settings ?| array['store_signin_locked', 'signin_epoch']",
)


def upgrade() -> None:
    op.add_column("users", sa.Column("store_signin_locked", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("signin_epoch", sa.Integer(), nullable=False, server_default="0"))
    for sql in MOVE:
        op.execute(sql)


def downgrade() -> None:
    op.execute("UPDATE users SET settings = coalesce(settings, '{}'::jsonb) "
               "|| jsonb_build_object('signin_epoch', signin_epoch) "
               "|| CASE WHEN store_signin_locked THEN '{\"store_signin_locked\": true}'::jsonb ELSE '{}'::jsonb END")
    op.drop_column("users", "signin_epoch")
    op.drop_column("users", "store_signin_locked")
