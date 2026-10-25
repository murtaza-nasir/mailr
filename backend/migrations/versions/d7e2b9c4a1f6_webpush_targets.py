"""Web Push targets: push_targets gains p256dh and auth (kind "webpush")

Revision ID: d7e2b9c4a1f6
Revises: a9c3d5e7f1b2
Create Date: 2026-10-01 23:30:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd7e2b9c4a1f6'
down_revision: Union[str, Sequence[str], None] = 'a9c3d5e7f1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('push_targets', sa.Column('p256dh', sa.Text(), nullable=True))
    op.add_column('push_targets', sa.Column('auth', sa.Text(), nullable=True))


def downgrade() -> None:
    op.execute("DELETE FROM push_targets WHERE kind = 'webpush'")
    op.drop_column('push_targets', 'auth')
    op.drop_column('push_targets', 'p256dh')
