"""Group calendars: calendar_groups and group_calendars

Revision ID: a6c3e9d1f5b8
Revises: d4b8f2a6c1e3
Create Date: 2026-10-06 10:00:00

Group calendars on Stalwart (calendar/groups.py).

- calendar_groups: one row per mailr group that has group calendars, with the Stalwart Group
  account that stands for it and the "owner" account that holds its read-only calendars.
- group_calendars: the calendars an admin attached to a group (name, colour, members' access,
  editors, the collection's path on the store).

Re-runnable: tables only when missing.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a6c3e9d1f5b8'
down_revision: str | Sequence[str] | None = 'd4b8f2a6c1e3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("calendar_groups"):
        op.create_table(
            "calendar_groups",
            sa.Column("group_name", sa.String(200), primary_key=True),
            sa.Column("store_group_id", sa.String(64)),
            sa.Column("store_group_login", sa.String(320)),
            sa.Column("owner_id", sa.String(64)),
            sa.Column("owner_login", sa.String(320)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    if not insp.has_table("group_calendars"):
        op.create_table(
            "group_calendars",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("group_name", sa.String(200), nullable=False, index=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("color", sa.String(9)),
            sa.Column("access", sa.String(8), nullable=False, server_default="write"),
            sa.Column("editors", postgresql.ARRAY(sa.Integer), nullable=False, server_default="{}"),
            sa.Column("href", sa.Text, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS group_calendars")
    op.execute("DROP TABLE IF EXISTS calendar_groups")
