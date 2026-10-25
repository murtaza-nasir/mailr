"""label colour format

Revision ID: 45e2351582ad
Revises: 5e1a7c0d2b91
Create Date: 2026-09-28 04:21:56.837170

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy


# revision identifiers, used by Alembic.
revision: str = '45e2351582ad'
down_revision: Union[str, Sequence[str], None] = '5e1a7c0d2b91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Label colours are "#bg" or "#bg,#fg" (lower-case); older clients wrote "#bg|#fg".
    op.execute("UPDATE labels SET color = lower(replace(color, '|', ',')) WHERE color LIKE '%|%'")


def downgrade() -> None:
    """Downgrade schema."""
    pass  # the comma form is valid for every version
