"""Messages: local_change_at

Revision ID: b6d1f4e8a2c7
Revises: a3c9e5f71b24
Create Date: 2026-10-01 23:30:00

The time a user, rule or agent last changed a message in mailr. A mirror account's label and
flag state is applied over mailr's only when the upstream changed after this time, so a mirror
copy no longer undoes a delete, an archive or a filter's label.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b6d1f4e8a2c7'
down_revision: Union[str, Sequence[str], None] = 'a3c9e5f71b24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("local_change_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "local_change_at")
