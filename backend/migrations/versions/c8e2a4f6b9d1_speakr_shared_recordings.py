"""Speakr: recordings shared with the user (is_shared, owner_name, can_edit) and mailr_refs

Revision ID: c8e2a4f6b9d1
Revises: b6d1f4e8a2c7
Create Date: 2026-10-02 12:00:00

With the connector's "Include recordings shared with me" (off by default), the mirror holds
recordings other Speakr users shared with the account. These columns say whose a recording is
and whether its share allows edits. Existing rows are the user's own: not shared, editable.
`mailr_refs` holds mailr's own external references on the recording in Speakr ([{kind, ref}]).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c8e2a4f6b9d1'
down_revision: Union[str, Sequence[str], None] = 'b6d1f4e8a2c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("speakr_recordings", sa.Column("is_shared", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("speakr_recordings", sa.Column("owner_name", sa.String(200), nullable=False, server_default=""))
    op.add_column("speakr_recordings", sa.Column("can_edit", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("speakr_recordings", sa.Column("mailr_refs", postgresql.JSONB(), nullable=False,
                                                 server_default=sa.text("'[]'::jsonb")))


def downgrade() -> None:
    op.drop_column("speakr_recordings", "mailr_refs")
    op.drop_column("speakr_recordings", "can_edit")
    op.drop_column("speakr_recordings", "owner_name")
    op.drop_column("speakr_recordings", "is_shared")
