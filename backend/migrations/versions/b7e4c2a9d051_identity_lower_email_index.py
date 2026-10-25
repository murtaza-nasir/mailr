"""identities: an index on lower(email)

account_picture.proven_identities asks for the identities with given addresses (an avatar, the
viewer's correspondents) instead of reading every identity of the instance.

Revision ID: b7e4c2a9d051
Revises: 9d3b6e1f4a27
Create Date: 2026-10-05 22:00:00

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b7e4c2a9d051'
down_revision: Union[str, Sequence[str], None] = '9d3b6e1f4a27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_identities_lower_email ON identities (lower(email))")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_identities_lower_email")
