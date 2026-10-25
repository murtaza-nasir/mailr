"""Ambrose Chats: search and rename

Revision ID: c7e3a9f1d548
Revises: a6c2e9d4f817
Create Date: 2026-10-08 12:00:00

- chat_sessions.title_custom: the title is one the user gave (PATCH /agent/sessions/{id}); later
  turns, and an edited first question, leave it alone. False keeps the automatic title (the first
  question). A constant default on a new column is stored in the catalogue (Postgres 11+): no
  table rewrite, a brief lock.
- ix_chat_msg_fts: full text ('simple') over the questions and answers of every chat, the rows
  GET /agent/sessions?q= reads. Tool results are left out (they are never searched).
- ix_chat_msg_trgm: trigrams over the same rows, for the fallback when full text finds nothing
  (part of a word, an address, a file name).
- ix_chat_sessions_user_updated: a user's chats newest first (the list and the search's order).

The indexes are built CONCURRENTLY outside the migration transaction. A build that fails leaves
an INVALID index; the next upgrade drops and rebuilds it.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c7e3a9f1d548'
down_revision: str | Sequence[str] | None = 'a6c2e9d4f817'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = {
    "ix_chat_msg_fts": "chat_messages USING gin (to_tsvector('simple'::regconfig, content)) "
                       "WHERE role IN ('user', 'assistant')",
    "ix_chat_msg_trgm": "chat_messages USING gin (content gin_trgm_ops) WHERE role IN ('user', 'assistant')",
    "ix_chat_sessions_user_updated": "chat_sessions (user_id, updated_at DESC)",
}


def upgrade() -> None:
    op.execute("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS title_custom BOOLEAN NOT NULL DEFAULT false")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
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
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS title_custom")
