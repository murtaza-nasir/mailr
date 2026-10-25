"""Speed: drop indexes no query uses, index the people lookups by address

Revision ID: c4d8e1f2a6b3
Revises: 6028ca0a3e59
Create Date: 2026-10-14 02:00:00

Production evidence (pg_stat_user_indexes, read only, 14 Oct 2026; counters since the cluster was
created on 27 Sep): ix_msg_content_hash 12.2 MB and ix_msg_upstream_thread 4.3 MB had 0 scans;
ix_change_log_user_id (2.5 MB, 27 scans) leads ix_changes_user_id (user_id, id), which serves the
same lookups. No query filters messages on content_hash (it is compared row by row), and the
thread-key lookups name the user, so they use ix_messages_thread_key (user_id, thread key).

People are looked up by lower(email) (Speakr speakers, the person card): until now a sequential
scan of the table each time (people: 29k sequential scans reading 245M rows in production). The
typeahead's lower(email|name|organization) LIKE '%…%' stays a sequential scan: trigram indexes
were measured slower than the scan at 9,000 people (6 ms against 3 ms), so none is added.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'c4d8e1f2a6b3'
down_revision: Union[str, Sequence[str], None] = '6028ca0a3e59'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_msg_content_hash")
    op.execute("DROP INDEX IF EXISTS ix_msg_upstream_thread")
    op.execute("DROP INDEX IF EXISTS ix_change_log_user_id")
    op.execute("CREATE INDEX IF NOT EXISTS ix_people_user_lower_email ON people (user_id, lower(email))")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_people_user_lower_email")
    op.execute("CREATE INDEX IF NOT EXISTS ix_change_log_user_id ON change_log (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_msg_upstream_thread ON messages ((upstream->>'thread_key')) "
               "WHERE upstream ? 'thread_key'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_msg_content_hash ON messages (user_id, content_hash) "
               "WHERE content_hash IS NOT NULL")
