"""Follow my forwarded notes becomes a built-in standing order; from_me (9 Oct 2026)

Replaces the agent.forwarded_instructions switch. assistant/orders.py, assistant/forwards.py.

- assistant_orders.builtin: "" for the user's own orders; "forwarded_notes" for the built-in
  order "Follow my forwarded notes" that mailr seeds per user. A partial unique index keeps one per
  user.
- assistant_orders.from_me: a message order's condition "only from me or my trusted addresses".
  Added off for every existing order: no existing order changes.
- One built-in order per existing user. Its state comes from the old switch, the assistant-level
  rule for agent.forwarded_instructions: deny (Off) → the order is turned off and the deny rule is
  removed (the order's switch holds Off now); allow → on, the allow rule stays (it is the order's
  Allow); ask or no rule → on, Ask me.

Safe to run again: ADD COLUMN IF NOT EXISTS with constant defaults (no table rewrite); the index is
built CONCURRENTLY outside the migration transaction (an INVALID leftover is dropped first); the
seed skips users who have the order (ON CONFLICT DO NOTHING); a deny rule is removed only for users
whose order was seeded off.

Revision ID: f4c9a2e7b1d6
Revises: e7b2d4f8a1c3
Create Date: 2026-10-09 23:55:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f4c9a2e7b1d6'
down_revision: str | Sequence[str] | None = 'e7b2d4f8a1c3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "uq_assistant_orders_builtin"
CLASS = "agent.forwarded_instructions"
# A plain (unconstrained, unexpired) deny rule for the class at the assistant level or for every agent.
DENY = (f"SELECT 1 FROM permission_rules r WHERE r.user_id = u.id AND r.action = '{CLASS}' "
        "AND r.effect = 'deny' AND r.subject IN ('assistant', '*') "
        "AND (r.constraints IS NULL OR r.constraints = '{}'::jsonb) "
        "AND (r.expires_at IS NULL OR r.expires_at > now())")


def upgrade() -> None:
    op.execute("ALTER TABLE assistant_orders ADD COLUMN IF NOT EXISTS from_me boolean NOT NULL DEFAULT false")
    op.execute("ALTER TABLE assistant_orders ADD COLUMN IF NOT EXISTS builtin varchar(40) NOT NULL DEFAULT ''")
    with op.get_context().autocommit_block():
        valid = op.get_bind().execute(sa.text(
            "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n"),
            {"n": INDEX}).scalar()
        if valid is False:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute(f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} ON assistant_orders (user_id, builtin) "
                   "WHERE builtin <> ''")
    seed(op.get_bind())


def seed(bind) -> list[tuple[int, bool]]:
    """One built-in order per user who has none, its state from the old switch; returns (user, on)."""
    seeded = bind.execute(sa.text(f"""
        INSERT INTO assistant_orders (user_id, name, trigger, schedule, timezone, query, question, choices,
                                      threshold, actions, errand, recordings, from_trusted, from_me, builtin,
                                      deliver, enabled, position, source, runs, created_at, updated_at)
        SELECT u.id, 'Follow my forwarded notes', 'message', '', '', '', '', '[]'::jsonb, 0.7, '{{}}'::jsonb, '',
               '{{}}'::jsonb, true, false, 'forwarded_notes', ARRAY['brief', 'notify']::varchar[],
               NOT EXISTS ({DENY}), 0, 'builtin', 0, now(), now()
        FROM users u
        ON CONFLICT (user_id, builtin) WHERE builtin <> '' DO NOTHING
        RETURNING user_id, enabled""")).all()
    off = [r[0] for r in seeded if not r[1]]
    if off:
        bind.execute(sa.text(
            "DELETE FROM permission_rules WHERE user_id = ANY(:ids) AND action = :a AND effect = 'deny' "
            "AND subject IN ('assistant', '*') AND (constraints IS NULL OR constraints = '{}'::jsonb)"),
            {"ids": off, "a": CLASS})
    return [(r[0], bool(r[1])) for r in seeded]


def downgrade() -> None:
    # Users whose order was off get the old switch's Off back.
    op.execute(f"""
        INSERT INTO permission_rules (user_id, subject, action, effect, constraints, source, created_at)
        SELECT o.user_id, 'assistant', '{CLASS}', 'deny', '{{}}'::jsonb, 'migrated', now()
        FROM assistant_orders o WHERE o.builtin = 'forwarded_notes' AND NOT o.enabled""")
    op.execute("DELETE FROM assistant_orders WHERE builtin <> ''")
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
    op.execute("ALTER TABLE assistant_orders DROP COLUMN IF EXISTS builtin")
    op.execute("ALTER TABLE assistant_orders DROP COLUMN IF EXISTS from_me")
