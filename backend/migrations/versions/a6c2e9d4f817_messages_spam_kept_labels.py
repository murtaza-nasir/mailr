"""messages.spam_kept_labels: the user's labels a message had when it was reported as spam

Revision ID: a6c2e9d4f817
Revises: d5f2b8e1c7a3
Create Date: 2026-10-08 23:50:00

Report spam keeps the user's own labels (given by the user or a filter) and drops the ones the
classifier applied. The kept label ids are held here while the message is in Spam, and "Not spam"
puts them back (core/actions.py). The labels are not kept as mailboxes beside Spam: the store and a
mirror account are readable over IMAP, and other mail apps would show the spam in those folders.

Prod-safe and re-runnable: one nullable column without a default (no table rewrite, a brief lock).
"""
from collections.abc import Sequence

from alembic import op

revision: str = 'a6c2e9d4f817'
down_revision: str | Sequence[str] | None = 'd5f2b8e1c7a3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS spam_kept_labels INTEGER[]")


def downgrade() -> None:
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS spam_kept_labels")
