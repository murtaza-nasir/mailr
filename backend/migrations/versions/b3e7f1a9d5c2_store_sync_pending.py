"""The store sync's pending ids as rows: store_sync_pending, from store_sync_state.pending

Revision ID: b3e7f1a9d5c2
Revises: a8d4e2f6c9b3
Create Date: 2026-10-23 21:00:00

The ids core/storesync.py has seen in the mail store and not yet indexed were one JSON document
per user, read and written whole on every tick: after a lost cursor on a 500,000-message store,
tens of megabytes every 15 seconds. Each id is now a row (next_at: when it is due, unix time; n and
error: failed attempts), and the document's entries move into it. A first-seen time becomes due
after the 45-second grace period.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b3e7f1a9d5c2'
down_revision: str | Sequence[str] | None = 'a8d4e2f6c9b3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRACE_SECONDS = 45


def upgrade() -> None:
    op.create_table(
        "store_sync_pending",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("store_sync_state.user_id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("jmap_id", sa.String(100), primary_key=True),
        sa.Column("next_at", sa.Float, nullable=False),
        sa.Column("n", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text, nullable=False, server_default=""),
    )
    op.create_index("ix_store_sync_pending_due", "store_sync_pending", ["user_id", "next_at"])
    op.execute(f"""
        INSERT INTO store_sync_pending (user_id, jmap_id, next_at, n, error)
        SELECT s.user_id, left(e.key, 100),
               CASE WHEN jsonb_typeof(e.value) = 'object' THEN coalesce((e.value->>'next')::float8, 0)
                    ELSE e.value::text::float8 + {GRACE_SECONDS} END,
               CASE WHEN jsonb_typeof(e.value) = 'object' THEN coalesce((e.value->>'n')::int, 0) ELSE 0 END,
               CASE WHEN jsonb_typeof(e.value) = 'object' THEN coalesce(e.value->>'error', '') ELSE '' END
        FROM store_sync_state s, jsonb_each(s.pending) e
        WHERE jsonb_typeof(e.value) IN ('object', 'number')
        ON CONFLICT DO NOTHING
    """)
    op.drop_column("store_sync_state", "pending")


def downgrade() -> None:
    op.add_column("store_sync_state", sa.Column("pending", postgresql.JSONB, nullable=False, server_default="{}"))
    op.execute(f"""
        UPDATE store_sync_state s SET pending = p.doc FROM (
            SELECT user_id, jsonb_object_agg(jmap_id, CASE WHEN n > 0
                THEN jsonb_build_object('n', n, 'next', next_at, 'error', error)
                ELSE to_jsonb(next_at - {GRACE_SECONDS}) END) AS doc
            FROM store_sync_pending GROUP BY user_id) p
        WHERE p.user_id = s.user_id
    """)
    op.drop_table("store_sync_pending")
