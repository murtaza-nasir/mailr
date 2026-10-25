"""connector lookup index

Revision ID: 3189f22854b0
Revises: 926d93e15568
Create Date: 2026-09-27 03:41:37.906625

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy


# revision identifiers, used by Alembic.
revision: str = '3189f22854b0'
down_revision: Union[str, Sequence[str], None] = '926d93e15568'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE INDEX IF NOT EXISTS ix_msg_user_connector ON messages (user_id, connector_id) WHERE NOT deleted")
    op.execute("CREATE INDEX IF NOT EXISTS ix_msg_has_copies ON messages (user_id) WHERE upstream ? 'copies'")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS ix_msg_user_connector")
    op.execute("DROP INDEX IF EXISTS ix_msg_has_copies")
