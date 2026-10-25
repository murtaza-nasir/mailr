"""Spam and Trash bring no user labels to their conversations

Revision ID: c4e1a7d9f3b5
Revises: b8e4f2c6d1a9
Create Date: 2026-10-08 22:00:00

A conversation's labels (conversations.label_ids) now leave out the user labels of its messages in
Spam or Trash; those messages bring only their folder labels (core/threading.py _LABELS_SQL). A
label's list, its count and the inbox tabs built on it then leave spam out (8 Oct 2026: a
promotion in Spam carried the "GitHub" label and showed in the GitHub tab).

This migration recomputes the conversations that hold such a message, the same way, and withdraws
the label classifier's open suggestions on mail in Spam or Trash (status "withdrawn"). Labels on
the messages themselves stay as they are: the mail store holds them, and a migration does not
write to it.

Prod-safe and re-runnable: two UPDATEs limited to the affected rows (messages in Spam or Trash
that carry a user label), found through the label index; a second run changes nothing.
"""
from collections.abc import Sequence

from alembic import op

revision: str = 'c4e1a7d9f3b5'
down_revision: str | Sequence[str] | None = 'b8e4f2c6d1a9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        WITH hid AS (
            SELECT user_id, array_agg(id) FILTER (WHERE role IN ('junk', 'trash')) AS h, array_agg(id) AS r
            FROM labels WHERE role IS NOT NULL GROUP BY user_id
        ), aff AS (
            SELECT DISTINCT m.conversation_id AS cid
            FROM messages m JOIN hid ON hid.user_id = m.user_id
            WHERE NOT m.deleted AND m.conversation_id IS NOT NULL
              AND m.label_ids && hid.h AND NOT (m.label_ids <@ hid.r)
        ), fresh AS (
            SELECT a.cid, coalesce((
                SELECT array_agg(DISTINCT l ORDER BY l)
                FROM messages m2 JOIN hid h2 ON h2.user_id = m2.user_id, unnest(m2.label_ids) l
                WHERE m2.conversation_id = a.cid AND NOT m2.deleted
                  AND (NOT (m2.label_ids && h2.h) OR l = ANY(h2.r))), '{}'::int[]) AS labels
            FROM aff a
        )
        UPDATE conversations c SET label_ids = fresh.labels
        FROM fresh WHERE c.id = fresh.cid AND c.label_ids IS DISTINCT FROM fresh.labels
    """)
    op.execute("""
        UPDATE label_decisions d SET status = 'withdrawn'
        FROM messages m
        WHERE d.message_id = m.id AND d.kind = 'label' AND d.source = 'classifier' AND d.status = 'suggested'
          AND EXISTS (SELECT 1 FROM labels hl WHERE hl.id = ANY(m.label_ids) AND hl.role IN ('junk', 'trash'))
    """)


def downgrade() -> None:
    # The old label sets come back with the next refresh of each conversation; nothing to undo here.
    pass
