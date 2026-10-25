"""audit fixes + permissions: permission rules, message data class and content hash, outbox Message-ID, pending actions,
forwarding addresses, persisted store-sync state, idempotency status, upstream ref index

Revision ID: a7c4e2f19d30
Revises: 3189f22854b0
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7c4e2f19d30"
down_revision: str | Sequence[str] | None = "3189f22854b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("local_only", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("messages", sa.Column("content_hash", sa.String(length=64), nullable=True))
    # Backfill the data class from the connectors that delivered the mail.
    op.execute("""
        UPDATE messages m SET local_only = true FROM connectors c
        WHERE m.connector_id = c.id AND c.data_class = 'local_only'
    """)
    op.add_column("outbox", sa.Column("mime_message_id", sa.String(length=998), nullable=True))
    op.add_column("outbox", sa.Column("subject", sa.String(length=80), server_default="user", nullable=False))
    op.execute("UPDATE outbox SET subject = 'token:' || token_id WHERE token_id IS NOT NULL")
    op.add_column("idempotency_keys", sa.Column("status", sa.String(length=10), server_default="done", nullable=False))
    op.add_column("idempotency_keys", sa.Column("status_code", sa.Integer(), server_default="200", nullable=False))
    op.alter_column("idempotency_keys", "response", existing_type=postgresql.JSONB(), nullable=True)

    op.create_table(
        "permission_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=80), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("effect", sa.String(length=10), nullable=False),
        sa.Column("constraints", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_permission_rules_user_id"), "permission_rules", ["user_id"])
    op.create_index("ix_perm_user_subject", "permission_rules", ["user_id", "subject"])
    # Existing per-token send policies become permission rules.
    op.execute("""
        INSERT INTO permission_rules (user_id, subject, action, effect, constraints, source)
        SELECT user_id, 'token:' || id, 'mail.send',
               CASE WHEN coalesce(send_policy->>'mode', 'approve_all') = 'policy' THEN 'allow' ELSE 'ask' END,
               CASE WHEN coalesce(send_policy->>'mode', 'approve_all') = 'policy'
                    THEN jsonb_strip_nulls(jsonb_build_object(
                        'replies_only', (send_policy->'replies_only'),
                        'known_only', (send_policy->'known_only'),
                        'domains', CASE WHEN jsonb_array_length(coalesce(send_policy->'domains','[]'::jsonb)) > 0
                                        THEN send_policy->'domains' END,
                        'identities', CASE WHEN jsonb_array_length(coalesce(send_policy->'identities','[]'::jsonb)) > 0
                                           THEN send_policy->'identities' END,
                        'max_per_hour', send_policy->'max_per_hour',
                        'max_per_day', send_policy->'max_per_day'))
                    ELSE '{}'::jsonb END,
               'migrated'
        FROM api_tokens WHERE NOT revoked AND 'send' = ANY(scopes)
    """)

    op.create_table(
        "pending_actions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_id", sa.Integer(), nullable=True),
        sa.Column("chat_session_id", sa.String(length=64), nullable=True),
        sa.Column("subject", sa.String(length=80), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("ctx", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("preview", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("decided_by", sa.String(length=50), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["token_id"], ["api_tokens.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pending_actions_user_id"), "pending_actions", ["user_id"])
    op.create_index(op.f("ix_pending_actions_status"), "pending_actions", ["status"])

    op.create_table(
        "forward_addresses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "email"),
    )
    op.create_index(op.f("ix_forward_addresses_user_id"), "forward_addresses", ["user_id"])

    op.create_table(
        "store_sync_state",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("email_state", sa.String(length=100), nullable=True),
        sa.Column("mailbox_state", sa.String(length=100), nullable=True),
        sa.Column("pending", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    # Hot-path lookup of a connector folder's known UIDs (IDLE catch-up) on the primary ref.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_msg_upstream_ref ON messages
        ((upstream->>'connector'), (upstream->>'folder'), ((upstream->>'uid')::bigint))
        WHERE upstream ? 'uid'
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_msg_content_hash ON messages (user_id, content_hash) "
               "WHERE content_hash IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_outbox_sending ON outbox (status, send_at) "
               "WHERE status IN ('queued','sending')")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_outbox_sending")
    op.execute("DROP INDEX IF EXISTS ix_msg_content_hash")
    op.execute("DROP INDEX IF EXISTS ix_msg_upstream_ref")
    op.drop_table("store_sync_state")
    op.drop_index(op.f("ix_forward_addresses_user_id"), table_name="forward_addresses")
    op.drop_table("forward_addresses")
    op.drop_index(op.f("ix_pending_actions_status"), table_name="pending_actions")
    op.drop_index(op.f("ix_pending_actions_user_id"), table_name="pending_actions")
    op.drop_table("pending_actions")
    op.drop_index("ix_perm_user_subject", table_name="permission_rules")
    op.drop_index(op.f("ix_permission_rules_user_id"), table_name="permission_rules")
    op.drop_table("permission_rules")
    op.alter_column("idempotency_keys", "response", existing_type=postgresql.JSONB(), nullable=False)
    op.drop_column("idempotency_keys", "status_code")
    op.drop_column("idempotency_keys", "status")
    op.drop_column("outbox", "subject")
    op.drop_column("outbox", "mime_message_id")
    op.drop_column("messages", "content_hash")
    op.drop_column("messages", "local_only")
