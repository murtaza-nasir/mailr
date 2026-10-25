"""index messages on the provider thread key

Revision ID: 7c3e9d1a4f20
Revises: 45e2351582ad
Create Date: 2026-09-29 07:00:00

"""
from typing import Sequence, Union

from alembic import op


revision: str = '7c3e9d1a4f20'
down_revision: Union[str, Sequence[str], None] = '45e2351582ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # threading.assign looks up the provider thread (Gmail X-GM-THRID) for every ingested
    # message; without this index each lookup scanned all of the user's messages.
    op.execute("CREATE INDEX IF NOT EXISTS ix_messages_thread_key ON messages "
               "(user_id, (upstream->>'thread_key')) WHERE upstream->>'thread_key' IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_thread_key")
