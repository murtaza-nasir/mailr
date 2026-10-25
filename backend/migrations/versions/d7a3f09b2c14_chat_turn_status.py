"""chat turns: status and timeline metadata

Revision ID: d7a3f09b2c14
Revises: c4b8e2a61f05
Create Date: 2026-09-29 15:00:00

A user row carries its turn's status (running | done | stopped | error | interrupted; runs left
"running" by a crash are marked interrupted when the API starts). Assistant rows carry the
segment they belong to, so a reloaded chat shows the same text / tool-step timeline.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7a3f09b2c14"
down_revision: str | Sequence[str] | None = "c4b8e2a61f05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("status", sa.String(12), nullable=True))
    op.add_column("chat_messages", sa.Column("meta", postgresql.JSONB(), nullable=True))
    op.create_index("ix_chat_messages_running", "chat_messages", ["status"],
                    postgresql_where=sa.text("status = 'running'"))


def downgrade() -> None:
    op.drop_index("ix_chat_messages_running", table_name="chat_messages")
    op.drop_column("chat_messages", "meta")
    op.drop_column("chat_messages", "status")
