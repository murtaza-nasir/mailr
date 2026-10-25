"""search triggers and indexes

Revision ID: bf7948454059
Revises: ba45197fb68b
Create Date: 2026-09-27 03:23:16.292834

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy


# revision identifiers, used by Alembic.
revision: str = 'bf7948454059'
down_revision: Union[str, Sequence[str], None] = 'ba45197fb68b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
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
    """)
    op.execute("""
    CREATE TRIGGER messages_tsv BEFORE INSERT OR UPDATE OF subject, from_name, from_email, "to", cc, attachments, body_text
    ON messages FOR EACH ROW EXECUTE FUNCTION mailr_messages_tsv();
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_msg_embedding ON messages USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_msg_upstream_thread ON messages ((upstream->>'thread_key')) WHERE upstream ? 'thread_key'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_msg_inreplyto ON messages (user_id, in_reply_to)")
    op.execute('CREATE INDEX IF NOT EXISTS ix_msg_refs ON messages USING gin ("references")')



def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS messages_tsv ON messages")
    op.execute("DROP FUNCTION IF EXISTS mailr_messages_tsv()")
    for ix in ("ix_msg_embedding", "ix_msg_upstream_thread", "ix_msg_inreplyto", "ix_msg_refs"):
        op.execute(f"DROP INDEX IF EXISTS {ix}")
