"""model capabilities (vision) and files attached to assistant chats

- model_endpoints.capabilities: per listed model, whether it can view images, as detected from the
  server and as an admin's override (intel/modelcaps.py).
- chat_attachments: files and images a user attaches to a message to the assistant
  (agent/chat_files.py), deleted with the chat.

Re-runnable: the column and the table are added only when they are missing.

Revision ID: a7c3e9d2b4f1
Revises: e6b3d9a1c4f7
Create Date: 2026-11-07 09:00:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = 'a7c3e9d2b4f1'
down_revision: str | Sequence[str] | None = 'e6b3d9a1c4f7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE model_endpoints ADD COLUMN IF NOT EXISTS capabilities JSONB NOT NULL DEFAULT '{}'")
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_attachments (
            id VARCHAR(32) PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            session_id VARCHAR(40) REFERENCES chat_sessions(id) ON DELETE CASCADE,
            message_id BIGINT REFERENCES chat_messages(id) ON DELETE CASCADE,
            filename VARCHAR(200) NOT NULL,
            content_type VARCHAR(120) NOT NULL,
            size INTEGER NOT NULL,
            kind VARCHAR(10) NOT NULL,
            data BYTEA NOT NULL,
            image BYTEA,
            image_type VARCHAR(40),
            thumb BYTEA,
            thumb_type VARCHAR(40),
            text TEXT,
            info JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_attachments_user_id ON chat_attachments (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_attachments_session_id ON chat_attachments (session_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_attachments_message_id ON chat_attachments (message_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_attachments")
    op.execute("ALTER TABLE model_endpoints DROP COLUMN IF EXISTS capabilities")
