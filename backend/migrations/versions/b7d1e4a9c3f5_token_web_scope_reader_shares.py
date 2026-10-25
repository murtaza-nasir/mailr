"""The `web` token scope and per-person shares of a web provider's daily use

A token's agent chooses its own pages, so reading the web is an outbound channel. From now on a
token reaches web_search and read_page only with the scope `web`, which a new token does not get
unless the user ticks it. Existing unrestricted tokens keep page reading; restricted and local-only
tokens lose it, and the owner ticks it back where needed. So `web` is
given only to a live token that holds `read` (the old requirement) and is unrestricted: not
local-only, not limited to some connectors (accounts), and not limited to some sending identities.

web_search_providers.usage_users: today's use per person and per token, for their shares of the
provider's daily allowance (websearch/service.shares).

Safe to run again: a token is touched only when it qualifies and has no `web` yet; ADD COLUMN IF
NOT EXISTS with a constant default (no table rewrite).

Revision ID: b7d1e4a9c3f5
Revises: f4c9a2e7b1d6
Create Date: 2026-10-10 18:00:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = 'b7d1e4a9c3f5'
down_revision: str | Sequence[str] | None = 'f4c9a2e7b1d6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The tokens that keep page reading: live, `read`, and no restriction mailr models on a token.
UNRESTRICTED_GRANT = (
    "UPDATE api_tokens SET scopes = array_append(scopes, 'web') "
    "WHERE 'read' = ANY(scopes) AND NOT ('web' = ANY(scopes)) AND NOT revoked "
    "AND NOT coalesce(local_only, false) "
    "AND coalesce(cardinality(connector_ids), 0) = 0 "
    "AND coalesce(jsonb_array_length(CASE WHEN jsonb_typeof(send_policy -> 'identities') = 'array' "
    "THEN send_policy -> 'identities' END), 0) = 0"
)


def upgrade() -> None:
    op.execute("ALTER TABLE web_search_providers ADD COLUMN IF NOT EXISTS usage_users jsonb NOT NULL DEFAULT '{}'")
    op.execute(UNRESTRICTED_GRANT)


def downgrade() -> None:
    op.execute("ALTER TABLE web_search_providers DROP COLUMN IF EXISTS usage_users")
    op.execute("UPDATE api_tokens SET scopes = array_remove(scopes, 'web')")
