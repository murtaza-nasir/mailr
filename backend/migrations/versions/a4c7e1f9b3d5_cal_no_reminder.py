"""Calendar mirror: cal_objects.no_reminder (an event marked "no reminders")

Revision ID: a4c7e1f9b3d5
Revises: c8e2a4f6b9d1
Create Date: 2026-10-02 18:00:00

An event the user or the assistant gave no reminders on purpose carries
X-MAILR-NO-REMINDER:TRUE (calendar/ical.py NO_REMINDER). The mirror keeps it as a column so the
event API can say `no_reminders` and the phone's planner skips the fallback default too. Only
mailr writes the marker and it is new, so existing rows are false.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a4c7e1f9b3d5'
down_revision: Union[str, Sequence[str], None] = 'c8e2a4f6b9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cal_objects", sa.Column("no_reminder", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("cal_objects", "no_reminder")
