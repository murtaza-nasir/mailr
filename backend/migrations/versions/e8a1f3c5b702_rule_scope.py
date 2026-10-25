"""rules: the creating token's visibility

Revision ID: e8a1f3c5b702
Revises: d7a3f09b2c14
Create Date: 2026-09-29 20:00:00

A filter created by an API token that cannot see all of the user's mail (connector limits, or
not local_only) records that token's visibility in `scope`; the filter only matches mail the
token could see, on arrival and when applied to existing mail.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e8a1f3c5b702"
down_revision: str | Sequence[str] | None = "d7a3f09b2c14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rules", sa.Column("scope", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("rules", "scope")
