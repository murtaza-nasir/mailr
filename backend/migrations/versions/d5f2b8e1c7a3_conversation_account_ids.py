"""conversations.account_ids and users.account_index: the account index for the counts

Revision ID: d5f2b8e1c7a3
Revises: c4e1a7d9f3b5
Create Date: 2026-10-08 23:30:00

- conversations.account_ids: the mail accounts each conversation belongs to (core/mailaccounts.py).
  /counts and an account's folder read it instead of a sub-query per conversation and account
  (8 Oct 2026: with five accounts /counts took about two minutes on production and jammed it).
- users.account_index: a hash of the user's accounts and addresses when the column was last
  rebuilt for the whole mailbox. NULL here, so the first /counts after the upgrade rebuilds it
  (inline for up to 5,000 conversations, else the job `mail.account_index`, in batches of 5,000);
  until then lists use the sub-query and the per-account numbers wait.

Prod-safe and re-runnable: two nullable columns without a default (no table rewrite, a brief
lock); no backfill here.
"""
from collections.abc import Sequence

from alembic import op

revision: str = 'd5f2b8e1c7a3'
down_revision: str | Sequence[str] | None = 'c4e1a7d9f3b5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS account_ids INTEGER[]")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS account_index VARCHAR(64)")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS account_index")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS account_ids")
