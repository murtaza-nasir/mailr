"""Outbox claim time and idempotency key (13 Oct 2026)

- outbox.claimed_at: when the send worker claimed the row (`queued` → `sending`). The stuck-send
  sweep (core/sendgate.py `sweep_stuck`) keys on it; it used send_at, so after a queue backlog a
  send still in flight was marked "may have been sent" and Retry could send it twice.
- outbox.idem_key: a key for sends the server makes on its own (a filter's forward:
  `fwd:<message id>:<address>`). Unique per user, so a job that runs again after a worker crash
  finds its Outbox row instead of adding a second one.

Safe to run again: IF NOT EXISTS throughout.

Revision ID: d7a3e9c5b2f4
Revises: a6d2f9c4e1b8
Create Date: 2026-10-13 12:00:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = 'd7a3e9c5b2f4'
down_revision: str | Sequence[str] | None = 'a6d2f9c4e1b8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE outbox ADD COLUMN IF NOT EXISTS claimed_at timestamptz")
    op.execute("ALTER TABLE outbox ADD COLUMN IF NOT EXISTS idem_key varchar(200)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_outbox_idem_key ON outbox (user_id, idem_key) "
               "WHERE idem_key IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_outbox_idem_key")
    op.execute("ALTER TABLE outbox DROP COLUMN IF EXISTS idem_key")
    op.execute("ALTER TABLE outbox DROP COLUMN IF EXISTS claimed_at")
