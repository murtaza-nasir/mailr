"""messages.lang and messages.lang_confidence (language identification at ingest)

Revision ID: c4e8a2f6d913
Revises: b7d2e94c1f60
Create Date: 2026-10-03 15:00:00

The language of the message's visible body text, from fastText lid.176 (core/langid.py): an
ISO 639-1 code where one exists and the model's probability for it. Both null = not checked
yet (mail ingested before this revision; `mailr langid-backfill` fills recent mail). lang null
with lang_confidence 0 = checked, but the body is too short to tell.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c4e8a2f6d913'
down_revision: Union[str, Sequence[str], None] = 'b7d2e94c1f60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("lang", sa.String(length=16), nullable=True))
    op.add_column("messages", sa.Column("lang_confidence", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "lang_confidence")
    op.drop_column("messages", "lang")
