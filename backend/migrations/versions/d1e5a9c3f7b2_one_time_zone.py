"""settings: one time zone

The time zone lived in two places: the account settings (`timezone`, set under Calendar & Tasks)
and the assistant profile (`assistant_profiles.data.timezone`, set under Assistant → About you).
The two could disagree, and the profile won wherever both were read. Now the account setting is
the only one (Settings → You → Region). An account whose profile has a zone gets it as its
setting (the profile won before, so nothing it sees changes), and the profile's copy is removed.
`timezone` on GET and PUT /assistant/profile stays as an alias of the setting.

Safe to run again: a profile without a zone is left alone.

Revision ID: d1e5a9c3f7b2
Revises: c4d9e2f7a610
Create Date: 2026-10-07 01:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'd1e5a9c3f7b2'
down_revision: Union[str, Sequence[str], None] = 'c4d9e2f7a610'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    move_zones(op.get_bind())


def move_zones(bind) -> None:
    """The profile's zone becomes the setting where it has one (tests/test_one_time_zone.py)."""
    if not sa.inspect(bind).has_table("assistant_profiles"):
        return
    bind.execute(sa.text(
        "UPDATE users u SET settings = coalesce(u.settings, '{}'::jsonb) "
        "  || jsonb_build_object('timezone', p.data->>'timezone') "
        "FROM assistant_profiles p "
        "WHERE p.user_id = u.id AND coalesce(p.data->>'timezone', '') <> ''"))
    bind.execute(sa.text(
        "UPDATE assistant_profiles SET data = data - 'timezone' WHERE data ? 'timezone'"))


def downgrade() -> None:
    # The profile gets a copy of the setting again; the setting stays.
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("assistant_profiles"):
        return
    bind.execute(sa.text(
        "UPDATE assistant_profiles p SET data = coalesce(p.data, '{}'::jsonb) "
        "  || jsonb_build_object('timezone', u.settings->>'timezone') "
        "FROM users u WHERE p.user_id = u.id AND coalesce(u.settings->>'timezone', '') <> ''"))
