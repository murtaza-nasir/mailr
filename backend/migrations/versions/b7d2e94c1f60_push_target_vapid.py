"""Security fixes (Oct 2026, area 5/6): push_targets.vapid

Revision ID: b7d2e94c1f60
Revises: e3f1a9c7b2d4
Create Date: 2026-10-03 12:00:00

A UnifiedPush endpoint the phone registered with mailr's VAPID key (UnifiedPush connector 3):
mailr then signs every message to it (RFC 8292), and the push server refuses messages that are
not signed with that key. Existing endpoints were registered without one: false.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b7d2e94c1f60'
down_revision: Union[str, Sequence[str], None] = 'e3f1a9c7b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("push_targets", sa.Column("vapid", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column("push_targets", "vapid")
