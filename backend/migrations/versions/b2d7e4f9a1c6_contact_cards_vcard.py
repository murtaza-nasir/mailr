"""Calendar backups: contact_cards.vcard

Revision ID: b2d7e4f9a1c6
Revises: a6c3e9d1f5b8
Create Date: 2026-10-07 10:00:00

The daily export of calendars and address books (calendar/backup.py).
The address-book mirror kept only the parsed card. The daily export writes each address book as a
.vcf file, so the mirror now keeps the card as the server sent it. Existing rows start empty; the
next address-book sync fetches those cards again.

Re-runnable: the column is added only when missing.
"""
from collections.abc import Sequence

from alembic import op

revision: str = 'b2d7e4f9a1c6'
down_revision: str | Sequence[str] | None = 'a6c3e9d1f5b8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE contact_cards ADD COLUMN IF NOT EXISTS vcard TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE contact_cards DROP COLUMN IF EXISTS vcard")
