"""users.oidc_auto_created (account made by single sign-on auto-create)

Revision ID: e7b2c9d4f1a6
Revises: d5f1a7c3e284
Create Date: 2026-10-03 22:00:00

True for accounts that single sign-on created because the user was in
MAILR_OIDC_AUTO_CREATE_GROUP. Such accounts are signed in only while the user is still in that
group. Accounts that existed before this revision stay false: an
admin made them, or they were auto-created earlier and are disabled by hand when needed.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e7b2c9d4f1a6'
down_revision: str | Sequence[str] | None = 'd5f1a7c3e284'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("oidc_auto_created", sa.Boolean(), nullable=False,
                                     server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("users", "oidc_auto_created")
