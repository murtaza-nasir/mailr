"""Page readers: web_search_providers.purpose (9 Oct 2026)

An admin can now register page readers (browserless, Firecrawl, Jina Reader) beside the search
providers, in the same table: `purpose` is "search" (every existing row) or "reader"
(websearch/readers.py). The keys, limits, order and
health notes work the same way for both.

Safe to run again: ADD COLUMN IF NOT EXISTS with a constant default (no table rewrite).

Revision ID: e3f8b1c6a2d9
Revises: b7d4e1a9c3f6
Create Date: 2026-10-09 09:00:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = 'e3f8b1c6a2d9'
down_revision: str | Sequence[str] | None = 'b7d4e1a9c3f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE web_search_providers ADD COLUMN IF NOT EXISTS purpose varchar(10) "
               "NOT NULL DEFAULT 'search'")


def downgrade() -> None:
    op.execute("ALTER TABLE web_search_providers DROP COLUMN IF EXISTS purpose")
