"""Note reminders and templates

Revision ID: 9d3b6e1f4a27
Revises: 7c4e2a9d51b3
Create Date: 2026-10-06 09:00:00

Reminders and templates of notes and tables (`note.save_template`): `note_reminders` holds the time-based reminders a viewer set on a note
(their states live in `reminder_states` with the event and task reminders); `note_templates` holds a
user's own templates of text notes, checklists and tables.

Two new tables, so nothing rewrites or locks an existing table. Re-runnable: each part is made only
when it is missing.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '9d3b6e1f4a27'
down_revision: str | Sequence[str] | None = '7c4e2a9d51b3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    have = set(sa.inspect(op.get_bind()).get_table_names())
    if "note_reminders" not in have:
        op.create_table(
            "note_reminders",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("note_id", sa.BigInteger(), sa.ForeignKey("notes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "note_id", "at", name="uq_note_reminders"),
        )
    op.create_index("ix_note_reminders_user_at", "note_reminders", ["user_id", "at"], if_not_exists=True)
    op.create_index("ix_note_reminders_note", "note_reminders", ["note_id"], if_not_exists=True)
    if "note_templates" not in have:
        op.create_table(
            "note_templates",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(10), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("content", postgresql.JSONB(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    op.create_index("ix_note_templates_user", "note_templates", ["user_id"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_note_templates_user", table_name="note_templates", if_exists=True)
    op.execute("DROP TABLE IF EXISTS note_templates")
    op.drop_index("ix_note_reminders_note", table_name="note_reminders", if_exists=True)
    op.drop_index("ix_note_reminders_user_at", table_name="note_reminders", if_exists=True)
    op.execute("DROP TABLE IF EXISTS note_reminders")
