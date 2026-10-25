"""Attached emails: their own text and search vector (9 Oct 2026)

An email attached to another (message/rfc822, a "Forward as attachment" or a digest) is now an
attachment of the outer message, not part of its body (core/mime.py). Its text goes to
messages.attached_text, and the search trigger writes it to messages.attached_tsv at weight D.
Search matches it too and ranks it below the outer message's own text; the body, the summaries,
the embeddings and the assistant read only the outer message.

The trigger function keeps every weight it had and also sets attached_tsv; the trigger fires on
attached_text too. Existing mail is untouched until `mailr reparse-attached-emails` runs.

Safe to run again: ADD COLUMN IF NOT EXISTS (nullable, no default: no table rewrite), CREATE OR
REPLACE FUNCTION, DROP/CREATE TRIGGER, and the partial GIN index built CONCURRENTLY outside the
transaction (an INVALID leftover is dropped first). The index holds only messages with attached
emails, so it builds in moments.

Revision ID: c4e8a2f6d1b3
Revises: e3f8b1c6a2d9
Create Date: 2026-10-09 20:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c4e8a2f6d1b3'
down_revision: str | Sequence[str] | None = 'e3f8b1c6a2d9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTION = """
CREATE OR REPLACE FUNCTION mailr_messages_tsv() RETURNS trigger AS $$
BEGIN
  NEW.tsv :=
    setweight(to_tsvector('simple', coalesce(NEW.subject, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(NEW.from_name, '') || ' ' || coalesce(NEW.from_email, '')), 'B') ||
    setweight(to_tsvector('simple', coalesce((SELECT string_agg(coalesce(a->>'name','') || ' ' || coalesce(a->>'email',''), ' ')
                                               FROM jsonb_array_elements(coalesce(NEW."to",'[]'::jsonb) || coalesce(NEW.cc,'[]'::jsonb)) a), '')), 'C') ||
    setweight(to_tsvector('simple', coalesce((SELECT string_agg(a->>'filename', ' ') FROM jsonb_array_elements(coalesce(NEW.attachments,'[]'::jsonb)) a), '')), 'C') ||
    setweight(to_tsvector('simple', left(coalesce(NEW.body_text, ''), 60000)), 'D');
  NEW.attached_tsv := CASE WHEN coalesce(NEW.attached_text, '') = '' THEN NULL
                           ELSE setweight(to_tsvector('simple', left(NEW.attached_text, 30000)), 'D') END;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
"""

OLD_FUNCTION = """
CREATE OR REPLACE FUNCTION mailr_messages_tsv() RETURNS trigger AS $$
BEGIN
  NEW.tsv :=
    setweight(to_tsvector('simple', coalesce(NEW.subject, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(NEW.from_name, '') || ' ' || coalesce(NEW.from_email, '')), 'B') ||
    setweight(to_tsvector('simple', coalesce((SELECT string_agg(coalesce(a->>'name','') || ' ' || coalesce(a->>'email',''), ' ')
                                               FROM jsonb_array_elements(coalesce(NEW."to",'[]'::jsonb) || coalesce(NEW.cc,'[]'::jsonb)) a), '')), 'C') ||
    setweight(to_tsvector('simple', coalesce((SELECT string_agg(a->>'filename', ' ') FROM jsonb_array_elements(coalesce(NEW.attachments,'[]'::jsonb)) a), '')), 'C') ||
    setweight(to_tsvector('simple', left(coalesce(NEW.body_text, ''), 60000)), 'D');
  RETURN NEW;
END $$ LANGUAGE plpgsql;
"""

TRIGGER = """
CREATE TRIGGER messages_tsv BEFORE INSERT OR UPDATE OF subject, from_name, from_email, "to", cc, attachments,
  body_text{extra} ON messages FOR EACH ROW EXECUTE FUNCTION mailr_messages_tsv();
"""

INDEX = "ix_msg_attached_tsv"


def upgrade() -> None:
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS attached_text text")
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS attached_tsv tsvector")
    op.execute(FUNCTION)
    op.execute("DROP TRIGGER IF EXISTS messages_tsv ON messages")
    op.execute(TRIGGER.format(extra=", attached_text"))
    with op.get_context().autocommit_block():
        valid = op.get_bind().execute(sa.text(
            "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n"),
            {"n": INDEX}).scalar()
        if valid is False:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} ON messages USING gin (attached_tsv) "
                   "WHERE attached_tsv IS NOT NULL")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
    op.execute("DROP TRIGGER IF EXISTS messages_tsv ON messages")
    op.execute(OLD_FUNCTION)
    op.execute(TRIGGER.format(extra=""))
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS attached_tsv")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS attached_text")
