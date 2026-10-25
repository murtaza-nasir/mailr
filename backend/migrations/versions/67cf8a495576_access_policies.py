"""Access policies keyed by identity-provider groups

Revision ID: 67cf8a495576
Revises: f3a8c1d6b2e9
Create Date: 2026-10-04 12:00:00

core/access.py, site/docs/admin/access.md.

- users.idp_groups, idp_groups_at, idp_groups_source: the groups the identity provider reported
  at the last sign-in (NULL until one did). users.assigned_groups: groups an admin assigns to an
  account the provider does not manage (password-only).
- access_policies: one row per external group name: allowed models per task with a default,
  and the feature switches.
- idp_groups_seen: every group name a provider has reported (Admin → Access).

No seed. Until 20 Oct 2026 this migration also created an example policy for the group
"family"; installations that ran it keep that policy, and new installations start with none.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '67cf8a495576'
down_revision: str | Sequence[str] | None = 'f3a8c1d6b2e9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('idp_groups', postgresql.ARRAY(sa.String(length=200)), nullable=True))
    op.add_column('users', sa.Column('idp_groups_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('idp_groups_source', sa.String(length=20), nullable=True))
    op.add_column('users', sa.Column('assigned_groups', postgresql.ARRAY(sa.String(length=200)),
                                     server_default='{}', nullable=False))
    op.create_table(
        'access_policies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('group_name', sa.String(length=200), nullable=False, unique=True),
        sa.Column('description', sa.String(length=500), server_default='', nullable=False),
        sa.Column('models', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('features', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('needs', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_table(
        'idp_groups_seen',
        sa.Column('name', sa.String(length=200), primary_key=True),
        sa.Column('source', sa.String(length=20), server_default='oidc', nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('idp_groups_seen')
    op.drop_table('access_policies')
    op.drop_column('users', 'assigned_groups')
    op.drop_column('users', 'idp_groups_source')
    op.drop_column('users', 'idp_groups_at')
    op.drop_column('users', 'idp_groups')
