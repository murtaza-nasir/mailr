"""follow-up nudges (mail.follow_up)

Revision ID: a3f1c9d27e40
Revises: 761012de8951
Create Date: 2026-09-30 19:40:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a3f1c9d27e40'
down_revision: Union[str, Sequence[str], None] = '761012de8951'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('conversations', sa.Column('follow_up_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('conversations', sa.Column('follow_up_since', sa.DateTime(timezone=True), nullable=True))
    op.add_column('conversations', sa.Column('nudged_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_conv_follow_up', 'conversations', ['follow_up_at'], unique=False,
                    postgresql_where=sa.text('follow_up_at IS NOT NULL'))


def downgrade() -> None:
    op.drop_index('ix_conv_follow_up', table_name='conversations', postgresql_where=sa.text('follow_up_at IS NOT NULL'))
    op.drop_column('conversations', 'nudged_at')
    op.drop_column('conversations', 'follow_up_since')
    op.drop_column('conversations', 'follow_up_at')
