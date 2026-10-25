"""The messages of one mail account: an index for account folders and the account: operator

Revision ID: a3d7c9e2f415
Revises: 7c2e9b4d1a60
Create Date: 2026-10-08 23:00:00

- ix_msg_user_connector: messages (user_id, connector_id, conversation_id). The `account:` search
  operator selects the messages of one account (core/search.py), and each account's own folders
  and their counts ask whether a conversation holds one (core/mailaccounts.py). Without the index
  `account:` read every message row of the user; with it the account's messages are a range of
  the index, and their conversations come from the index alone.

Re-runnable: the index is built CONCURRENTLY outside the migration transaction; an INVALID index
left by a failed build is dropped and rebuilt by the next upgrade.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a3d7c9e2f415'
down_revision: str | Sequence[str] | None = '7c2e9b4d1a60'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = {
    "ix_msg_user_connector": "messages (user_id, connector_id, conversation_id)",
}


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, spec in INDEXES.items():
            valid = op.get_bind().execute(sa.text(
                "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n"),
                {"n": name}).scalar()
            if valid is False:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {spec}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
