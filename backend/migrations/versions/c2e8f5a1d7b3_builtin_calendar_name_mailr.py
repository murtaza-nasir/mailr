"""The built-in calendar account is named "Mailr calendar" (10 Oct 2026)

The product's name in prose is "Mailr". The built-in calendar's
connector (calendar/builtin.py NAME) was created as "mailr calendar"; existing ones are renamed.
A built-in connector a user renamed keeps its name.

Safe to run again: only rows still named "mailr calendar" change.

Revision ID: c2e8f5a1d7b3
Revises: b7d1e4a9c3f5
Create Date: 2026-10-10 21:00:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = 'c2e8f5a1d7b3'
down_revision: str | Sequence[str] | None = 'b7d1e4a9c3f5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE connectors SET name = 'Mailr calendar' WHERE builtin AND name = 'mailr calendar'")


def downgrade() -> None:
    op.execute("UPDATE connectors SET name = 'mailr calendar' WHERE builtin AND name = 'Mailr calendar'")
