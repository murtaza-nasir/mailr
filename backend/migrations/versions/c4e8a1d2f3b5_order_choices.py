"""choice orders

Revision ID: c4e8a1d2f3b5
Revises: b7d2e4f19a60
Create Date: 2026-10-01 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'c4e8a1d2f3b5'
down_revision: Union[str, Sequence[str], None] = 'b7d2e4f19a60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('assistant_orders', sa.Column('choices', postgresql.JSONB(astext_type=sa.Text()),
                                                server_default='[]', nullable=False))


def downgrade() -> None:
    op.drop_column('assistant_orders', 'choices')
