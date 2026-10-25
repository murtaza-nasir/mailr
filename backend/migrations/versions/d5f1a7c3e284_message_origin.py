"""messages.origin (content another app shared into mailr)

Revision ID: d5f1a7c3e284
Revises: c4e8a2f6d913
Create Date: 2026-10-03 19:00:00

"android-share" for the phone's "Send to self" (X-Mailr-Origin), null otherwise. Such mail is
sent_by_me again (Sent, `from:me`, filters), while the assistant treats its text as untrusted
(Message.own_words). Mail ingested before this revision with the header has a null origin and,
since a change of 3 Oct 2026, sent_by_me false: `mailr share-origin-backfill` repairs it.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd5f1a7c3e284'
down_revision: str | Sequence[str] | None = 'c4e8a2f6d913'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("origin", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "origin")
