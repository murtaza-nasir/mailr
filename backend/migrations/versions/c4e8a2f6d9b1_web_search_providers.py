"""Web search providers

Revision ID: c4e8a2f6d9b1
Revises: b2d7e4f9a1c6
Create Date: 2026-10-05 12:00:00

The assistant's web search (websearch/). An admin registers one or
more search services: an API service (Exa, Brave, Tavily) or a self-hosted one (degoog, SearXNG).
The API key is sealed like a model endpoint's key (core/crypto, context
web_search_provider:<id>:api_key). A new table only: no lock on existing tables.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c4e8a2f6d9b1'
down_revision: str | Sequence[str] | None = 'b2d7e4f9a1c6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_search_providers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("api_key_enc", sa.LargeBinary(), nullable=True),
        sa.Column("network", sa.String(10), nullable=False, server_default="internet"),
        sa.Column("use_egress", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("max_results", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("timeout_s", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("per_minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("per_day", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("usage_day", sa.String(10), nullable=False, server_default=""),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("web_search_providers")
