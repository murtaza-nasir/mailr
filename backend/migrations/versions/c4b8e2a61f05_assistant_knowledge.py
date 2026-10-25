"""assistant knowledge: profile, skills, entities, memories

Revision ID: c4b8e2a61f05
Revises: 7c3e9d1a4f20
Create Date: 2026-09-29 12:00:00
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4b8e2a61f05"
down_revision: str | Sequence[str] | None = "7c3e9d1a4f20"
branch_labels = None
depends_on = None


def _ts() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "assistant_profiles",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("options", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("state", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "assistant_skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(48), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("instructions", sa.Text(), nullable=False, server_default=""),
        sa.Column("applies_to", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("auto", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(20), nullable=False, server_default="user"),
        *_ts(),
        sa.UniqueConstraint("user_id", "name"),
    )
    op.create_index("ix_assistant_skills_user_id", "assistant_skills", ["user_id"])

    op.create_table(
        "kb_entities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="person"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("emails", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("domains", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("conversation_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False, server_default="{}"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("sensitive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(20), nullable=False, server_default="user"),
        *_ts(),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_kb_entities_user_id", "kb_entities", ["user_id"])
    op.create_index("ix_kb_entity_emails", "kb_entities", ["emails"], postgresql_using="gin")
    op.create_index("ix_kb_entity_convs", "kb_entities", ["conversation_ids"], postgresql_using="gin")

    op.create_table(
        "kb_memories",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("kb_entities.id", ondelete="CASCADE"), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False, server_default="fact"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="user"),
        sa.Column("status", sa.String(12), nullable=False, server_default="active"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("refs", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("sensitive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tsv", postgresql.TSVECTOR(), sa.Computed("to_tsvector('simple', text)", persisted=True)),
        sa.Column("embedding", pgvector.sqlalchemy.HALFVEC(512), nullable=True),
        sa.Column("embed_model", sa.String(200), nullable=True),
        *_ts(),
    )
    op.create_index("ix_kb_memories_user_id", "kb_memories", ["user_id"])
    op.create_index("ix_kb_memories_entity_id", "kb_memories", ["entity_id"])
    op.create_index("ix_kb_memory_tsv", "kb_memories", ["tsv"], postgresql_using="gin")
    op.create_index("ix_kb_memory_user_status", "kb_memories", ["user_id", "status"])
    op.create_index("ix_kb_memory_refs", "kb_memories", ["refs"], postgresql_using="gin")
    op.execute("CREATE INDEX ix_kb_memory_embedding ON kb_memories USING hnsw (embedding halfvec_cosine_ops)")


def downgrade() -> None:
    op.drop_table("kb_memories")
    op.drop_table("kb_entities")
    op.drop_table("assistant_skills")
    op.drop_table("assistant_profiles")
