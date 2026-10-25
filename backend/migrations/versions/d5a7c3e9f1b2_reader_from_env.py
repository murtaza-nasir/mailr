"""Page readers: web_search_providers.from_env (9 Oct 2026)

The page reader set up by the server's environment (MAILR_BROWSER_URL and MAILR_BROWSER_TOKEN,
websearch/readers.py "Browser (server)") is a row like the others, so an admin's choices (route,
order, limits, on or off) are kept; `from_env` marks it. Its URL follows the environment and its
token is never stored.

Safe to run again: ADD COLUMN IF NOT EXISTS with a constant default (no table rewrite).

Revision ID: d5a7c3e9f1b2
Revises: c4e8a2f6d1b3
Create Date: 2026-10-09 22:00:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = 'd5a7c3e9f1b2'
down_revision: str | Sequence[str] | None = 'c4e8a2f6d1b3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE web_search_providers ADD COLUMN IF NOT EXISTS from_env boolean NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE web_search_providers DROP COLUMN IF EXISTS from_env")
