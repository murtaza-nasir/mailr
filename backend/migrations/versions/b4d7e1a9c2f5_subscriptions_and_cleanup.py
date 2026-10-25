"""Subscriptions manager and bulk cleanup: the unsubscribe method, group states, group indexes

Revision ID: b4d7e1a9c2f5
Revises: 67cf8a495576
Create Date: 2026-10-04 18:00:00

See core/mailgroups.py.

- messages.unsub_method: how a message's List-Unsubscribe can be used, decided at ingest
  (core/unsubscribe.method_of): one_click (an https URL and List-Unsubscribe-Post), mailto (an
  address in the sender's or the list's domain), link (a web page only), none (a header nothing
  can use). NULL: no List-Unsubscribe header. Mail stored before this column gets a first value
  from its indexed header here; List-Unsubscribe-Post was never indexed, so such mail with an
  https URL says "unknown" until the subscriptions page checks its newest message in the store
  (the subscriptions.probe job) or an unsubscribe reads it.
- mail_group_states: per user and group (a subscription, a sender, a domain, a list or a
  category), what the user decided (kept, unsubscribed, the auto-archive filter) and what a
  model suggested (a category, applied only when the user confirms).
- ix_msg_groups: the grouping columns of every message in one covering index, so the
  subscriptions page and the cleanup groups aggregate about 90k messages with an index-only scan
  instead of reading the wide message rows.
- ix_msg_sent_conv: the conversations with the user's own messages (cleanup leaves them alone).
- ix_conv_protected: the starred or important conversations (cleanup leaves them alone).
- messages autovacuum at 2 % instead of 20 %: an index-only scan skips the table only on pages the
  visibility map marks all-visible, and label and read changes keep clearing those marks.

The index predicates are written as SQLAlchemy writes the queries' conditions (`deleted IS
false`, `starred IS true`), so the planner can prove that a query's rows are in the partial index.

The indexes are built CONCURRENTLY outside the migration transaction (as in f3a8c1d6b2e9); an
INVALID index left by a failed build is dropped and rebuilt by the next upgrade.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b4d7e1a9c2f5'
down_revision: str | Sequence[str] | None = '67cf8a495576'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = {
    "ix_msg_groups": "messages (user_id, list_id, from_email) INCLUDE (unsub_method, conversation_id, date, size, "
                     "seen, category, sent_by_me, label_ids, connector_id, local_only) WHERE deleted IS false",
    "ix_msg_sent_conv": "messages (user_id, conversation_id) WHERE sent_by_me IS true AND deleted IS false",
    "ix_conv_protected": "conversations (user_id, id) WHERE starred IS true OR important IS true",
}


def upgrade() -> None:
    # Re-runnable: the index block below commits what came before it, so a build that was
    # interrupted (4 Oct 2026) left the column and the table in place without the version row.
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS unsub_method VARCHAR(10)")
    # A first value from the indexed header (the mailto domain check is approximated: an address
    # whose domain is not the sender's is "mailto" here until the probe or an unsubscribe reads
    # the message; neither ever sends to it, core/unsubscribe.py checks again before any send).
    op.execute("""
        UPDATE messages SET unsub_method = CASE
            WHEN list_unsubscribe ~* '<\\s*mailto:' THEN 'mailto'
            WHEN list_unsubscribe ~* '<\\s*https?://' THEN 'unknown'
            ELSE 'none' END
        WHERE list_unsubscribe IS NOT NULL AND list_unsubscribe <> '' AND unsub_method IS NULL
    """)
    if not sa.inspect(op.get_bind()).has_table("mail_group_states"):
        op.create_table(
            "mail_group_states",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(10), nullable=False),
            sa.Column("key", sa.String(400), nullable=False),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("unsubscribe_method", sa.String(20), nullable=True),
            sa.Column("rule_id", sa.Integer(), sa.ForeignKey("rules.id", ondelete="SET NULL"), nullable=True),
            sa.Column("suggested_category", sa.String(20), nullable=True),
            sa.Column("suggestion_confidence", sa.Float(), nullable=True),
            sa.Column("suggestion_model", sa.String(200), nullable=True),
            sa.Column("suggestion_samples", sa.Integer(), nullable=True),
            sa.Column("suggested_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("user_id", "kind", "key", name="uq_mail_group_states"),
        )
    # Vacuum the messages sooner (2 % of the rows changed or inserted, not 20 %): the covering
    # index answers without reading the table only for pages the visibility map marks all-visible.
    op.execute("ALTER TABLE messages SET (autovacuum_vacuum_scale_factor = 0.02, "
               "autovacuum_vacuum_insert_scale_factor = 0.02)")
    with op.get_context().autocommit_block():
        for name, spec in INDEXES.items():
            valid = op.get_bind().execute(sa.text(
                "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n"),
                {"n": name}).scalar()
            if valid is False:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {spec}")


def downgrade() -> None:
    op.execute("ALTER TABLE messages RESET (autovacuum_vacuum_scale_factor, autovacuum_vacuum_insert_scale_factor)")
    with op.get_context().autocommit_block():
        for name in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.drop_table("mail_group_states")
    op.drop_column("messages", "unsub_method")
