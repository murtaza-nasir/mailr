"""merge ops and data-safety heads

Revision ID: 6028ca0a3e59
Revises: b9e4c2a7d5f1, d7a3e9c5b2f4
Create Date: 2026-10-13 23:51:00.604081

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy


# revision identifiers, used by Alembic.
revision: str = '6028ca0a3e59'
down_revision: Union[str, Sequence[str], None] = ('b9e4c2a7d5f1', 'd7a3e9c5b2f4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
