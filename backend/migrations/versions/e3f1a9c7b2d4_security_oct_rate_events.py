"""Security fixes (Oct 2026): shared rate-limit events and users.email_verified

Revision ID: e3f1a9c7b2d4
Revises: a4c7e1f9b3d5
Create Date: 2026-10-03 04:00:00

rate_events holds one row per counted event (failed sign-in, sign-up, a token's model-backed
call) so every API worker shares one limit (core/throttle.py). users.email_verified is false
only for self-service sign-up accounts, which single sign-on then never links to by email.
Existing users were created by an administrator, the bootstrap or single sign-on: true.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e3f1a9c7b2d4'
down_revision: Union[str, Sequence[str], None] = 'a4c7e1f9b3d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rate_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rate_events_key_created", "rate_events", ["key", "created_at"])
    op.create_index("ix_rate_events_created", "rate_events", ["created_at"])
    op.add_column("users", sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("users", "email_verified")
    op.drop_index("ix_rate_events_created", table_name="rate_events")
    op.drop_index("ix_rate_events_key_created", table_name="rate_events")
    op.drop_table("rate_events")
