"""lists: repeating checklists and table dates on the calendar

- notes.repeat_rule: an RRULE of the list's repeat, one of two shapes (notes/repeat.py):
  `FREQ=WEEKLY;BYDAY=SU` (weekly on a day) or `FREQ=DAILY;INTERVAL=3` (every N days). NULL: the
  list does not repeat.
- notes.repeat_zone: the time zone of the person who set it; the reset happens at 04:00 there.
- notes.repeat_next: when the list resets next (its ticked items are unticked, history kept).
- notes.repeat_last: when it last reset ("Reset Sunday").
- note_viewer_state.calendar_column: the date column of a table that this viewer shows on their
  calendar as all-day chips. NULL: not on the calendar. Per viewer, so one person's choice never
  changes another person's calendar.
- ix_notes_repeat_next: the lists that repeat (few rows), for the per-minute reset scan.

Re-runnable: every step checks first. The index is built CONCURRENTLY outside the migration
transaction; an INVALID index left by a failed build is dropped and rebuilt by the next upgrade.

Revision ID: d7a2c9e4f1b8
Revises: d7a3f9c1e2b4
Create Date: 2026-10-07 01:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd7a2c9e4f1b8'
down_revision: str | Sequence[str] | None = 'd7a3f9c1e2b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = {
    "ix_notes_repeat_next": "notes (repeat_next) WHERE repeat_next IS NOT NULL",
}


def upgrade() -> None:
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS repeat_rule VARCHAR(200)")
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS repeat_zone VARCHAR(64)")
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS repeat_next TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS repeat_last TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE note_viewer_state ADD COLUMN IF NOT EXISTS calendar_column VARCHAR(16)")
    with op.get_context().autocommit_block():
        for name, spec in INDEXES.items():
            valid = op.get_bind().execute(sa.text(
                "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n"),
                {"n": name}).scalar()
            if valid is False:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {spec}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.execute("ALTER TABLE note_viewer_state DROP COLUMN IF EXISTS calendar_column")
    for col in ("repeat_last", "repeat_next", "repeat_zone", "repeat_rule"):
        op.execute(f"ALTER TABLE notes DROP COLUMN IF EXISTS {col}")
