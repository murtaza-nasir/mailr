"""Push targets belong to a session: push_targets.session_id

Revision ID: c1e7a4b9d2f3
Revises: a6c3e9d2b4f1
Create Date: 2026-10-23 12:00:00

- push_targets.session_id: the sign-in session that registered the target (ON DELETE CASCADE). Signing
  out, revoking the session, Sign out everywhere, a password change and an admin's reset end the
  sessions, and their targets go with them, so a former intruder's ntfy topic or browser stops
  receiving notifications. Targets made before this column have none; the recovery steps remove those
  (api/auth_routes.py drop_push_targets).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c1e7a4b9d2f3'
down_revision: str | Sequence[str] | None = 'a6c3e9d2b4f1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("push_targets", sa.Column("session_id", sa.String(64),
                                            sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_push_targets_session_id", "push_targets", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_push_targets_session_id", table_name="push_targets")
    op.drop_column("push_targets", "session_id")
