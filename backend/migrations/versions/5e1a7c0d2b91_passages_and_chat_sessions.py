"""passages, chat sessions, 512-d embeddings

Revision ID: 5e1a7c0d2b91
Revises: a7c4e2f19d30
Create Date: 2026-09-28 03:00:00

Embeddings now come from an OpenAI-compatible endpoint at 512 dimensions (was a built-in
384-d CPU model). Existing message vectors are dropped; the worker rebuilds them from the
new passage index.
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5e1a7c0d2b91"
down_revision: str | Sequence[str] | None = "a7c4e2f19d30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_msg_embedding")
    op.drop_column("messages", "embedding")
    op.add_column("messages", sa.Column("embedding", pgvector.sqlalchemy.Vector(512), nullable=True))
    op.execute("CREATE INDEX ix_msg_embedding ON messages USING hnsw (embedding vector_cosine_ops)")

    op.create_table(
        "message_passages",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=True),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(40), nullable=False),
        sa.Column("tsv", postgresql.TSVECTOR(), sa.Computed("to_tsvector('simple', text)", persisted=True)),
        sa.Column("embedding", pgvector.sqlalchemy.HALFVEC(512), nullable=True),
        sa.Column("embed_model", sa.String(200), nullable=True),
    )
    op.create_index("ix_passage_user_msg", "message_passages", ["user_id", "message_id"])
    op.create_index("ix_passage_tsv", "message_passages", ["tsv"], postgresql_using="gin")
    op.create_index("ix_passage_hash", "message_passages", ["content_hash", "embed_model"])
    op.create_index("ix_passage_pending", "message_passages", ["user_id"], postgresql_where=sa.text("embedding IS NULL"))
    op.execute("CREATE INDEX ix_passage_embedding ON message_passages USING hnsw (embedding halfvec_cosine_ops)")

    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("sensitive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("context", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("session_id", sa.String(40), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=True),
        sa.Column("tool_call_id", sa.String(100), nullable=True),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("citations", postgresql.JSONB(), nullable=True),
        sa.Column("steps", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])
    # Learned models were trained on 384-d vectors; they retrain on the new ones.
    op.execute("DELETE FROM classifier_models")


def downgrade() -> None:
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("message_passages")
    op.execute("DROP INDEX IF EXISTS ix_msg_embedding")
    op.drop_column("messages", "embedding")
    op.add_column("messages", sa.Column("embedding", pgvector.sqlalchemy.Vector(384), nullable=True))
    op.execute("CREATE INDEX ix_msg_embedding ON messages USING hnsw (embedding vector_cosine_ops)")
