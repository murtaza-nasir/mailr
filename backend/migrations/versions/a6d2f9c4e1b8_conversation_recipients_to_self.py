"""Conversation recipients and to_self (12 Oct 2026)

A sent email to someone else that had no reply yet showed "Note to self" in the Android list: the
list rows knew only the senders of a conversation (`participants`), and a conversation with the user
as its only sender looked like a note. core/threading.py now stores two more aggregates:

- conversations.to_self: the user wrote every message, and each went only to the user's own
  addresses (the account address and its identities).
- conversations.recipients: when the user is the only sender, the people the messages went to
  ([{"email","name"}], at most 12, the user's own addresses left out), so a row reads
  "To: Dana Whitfield" as in Gmail's Sent list. Empty otherwise.

Existing rows are filled here for conversations whose stored participants are all the user (the
only ones either value can be set for); refresh() keeps them up to date afterwards. The SQL follows
threading.solo_summary.

Safe to run again: ADD COLUMN IF NOT EXISTS with a constant default (no table rewrite), and the fill
recomputes the same values.

Revision ID: a6d2f9c4e1b8
Revises: c2e8f5a1d7b3
Create Date: 2026-10-12 10:00:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = 'a6d2f9c4e1b8'
down_revision: str | Sequence[str] | None = 'c2e8f5a1d7b3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FILL = """
WITH solo AS (
  SELECT c.id, c.user_id FROM conversations c
  WHERE jsonb_typeof(c.participants) = 'array' AND jsonb_array_length(c.participants) > 0
    AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(c.participants) p
                    WHERE NOT coalesce((p->>'me')::boolean, false))
),
mine AS (
  SELECT s.id AS cid, lower(u.email) AS a FROM solo s JOIN users u ON u.id = s.user_id
  UNION
  SELECT s.id, lower(i.email) FROM solo s JOIN identities i ON i.user_id = s.user_id
),
msgs AS (
  SELECT m.id, m.conversation_id AS cid, m.sent_by_me, coalesce(m.date, m.received_at) AS d, m."to", m.cc, m.bcc
  FROM messages m JOIN solo s ON s.id = m.conversation_id
  WHERE NOT m.deleted AND (NOT m.draft OR NOT EXISTS (
    SELECT 1 FROM messages x WHERE x.conversation_id = m.conversation_id AND NOT x.deleted AND NOT x.draft))
),
addr AS (
  SELECT m.cid, m.id, m.d, a.ord, a.v
  FROM msgs m, LATERAL jsonb_array_elements(
    coalesce(m."to", '[]'::jsonb) || coalesce(m.cc, '[]'::jsonb) || coalesce(m.bcc, '[]'::jsonb)) WITH ORDINALITY a(v, ord)
  WHERE coalesce(a.v->>'email', '') <> ''
),
others AS (
  SELECT DISTINCT ON (cid, lower(v->>'email')) cid, v, d, id, ord FROM addr
  WHERE lower(v->>'email') NOT IN (SELECT mine.a FROM mine WHERE mine.cid = addr.cid)
  ORDER BY cid, lower(v->>'email'), d NULLS LAST, id, ord
),
rec AS (
  SELECT cid, jsonb_agg(jsonb_build_object('email', v->>'email', 'name', nullif(v->>'name', ''))
                        ORDER BY d NULLS LAST, id, ord) FILTER (WHERE rn <= 12) AS r
  FROM (SELECT *, row_number() OVER (PARTITION BY cid ORDER BY d NULLS LAST, id, ord) AS rn FROM others) o
  GROUP BY cid
)
UPDATE conversations c SET
  recipients = CASE WHEN EXISTS (SELECT 1 FROM msgs WHERE msgs.cid = c.id AND NOT msgs.sent_by_me) THEN '[]'::jsonb
                    ELSE coalesce(rec.r, '[]'::jsonb) END,
  to_self = rec.cid IS NULL
    AND EXISTS (SELECT 1 FROM msgs WHERE msgs.cid = c.id)
    AND NOT EXISTS (SELECT 1 FROM msgs WHERE msgs.cid = c.id AND NOT msgs.sent_by_me)
    AND NOT EXISTS (SELECT 1 FROM msgs WHERE msgs.cid = c.id AND NOT EXISTS (SELECT 1 FROM addr WHERE addr.id = msgs.id))
FROM solo LEFT JOIN rec ON rec.cid = solo.id
WHERE c.id = solo.id
"""


def upgrade() -> None:
    op.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS recipients jsonb")
    op.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS to_self boolean NOT NULL DEFAULT false")
    op.execute(FILL)


def downgrade() -> None:
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS to_self")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS recipients")
