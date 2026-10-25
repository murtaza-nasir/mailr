"""model_endpoints.from_env: the speech server set up by the environment

- model_endpoints.from_env: the endpoint is set up by the server's environment (MAILR_ASR_URL, the
  built-in speech-to-text endpoint, agent/transcribe.sync_env_asr). Its URL comes from there and an
  admin cannot change it; an admin changes its models, task defaults and whether it is on. False for
  every endpoint an admin added.

Re-runnable: the column is added only when it is missing.

Revision ID: e6b3d9a1c4f7
Revises: b5d1e8a3c7f2
Create Date: 2026-10-25 09:00:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = 'e6b3d9a1c4f7'
down_revision: str | Sequence[str] | None = 'b5d1e8a3c7f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE model_endpoints ADD COLUMN IF NOT EXISTS from_env BOOLEAN NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE model_endpoints DROP COLUMN IF EXISTS from_env")
