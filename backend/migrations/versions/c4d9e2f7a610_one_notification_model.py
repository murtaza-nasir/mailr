"""settings: one notification model

The web-only `desktop_notifications` becomes `mail_notifications`, which every device follows
(core/notifyprefs.py). Each account gets the most inclusive level it chose anywhere before: its
desktop setting (unless off) and the "New mail" choice of each browser's Web Push (All new mail
or Important mail only). An account that chose nothing gets "important", the old default of the
Android app. The old key is removed. Safe to run again: accounts that already have the new key
are left alone.

Revision ID: c4d9e2f7a610
Revises: c3a9e6f1b2d4
Create Date: 2026-10-06 23:30:00

"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'c4d9e2f7a610'
down_revision: Union[str, Sequence[str], None] = 'c3a9e6f1b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ORDER = ("off", "vip", "important", "primary", "all")


def upgrade() -> None:
    bind = op.get_bind()
    users = bind.execute(sa.text(
        "SELECT id, settings FROM users WHERE NOT (coalesce(settings, '{}'::jsonb) ? 'mail_notifications')")).all()
    for uid, settings in users:
        settings = dict(settings or {})
        levels = []
        old = settings.pop("desktop_notifications", None)
        if old in ORDER and old != "off":
            levels.append(old)
        for (events,) in bind.execute(sa.text(
                "SELECT events FROM push_targets WHERE user_id = :u AND kind = 'webpush'"), {"u": uid}).all():
            ev = set(events or [])
            if "new_mail" in ev:
                levels.append("all")
            elif "important_mail" in ev:
                levels.append("important")
        settings["mail_notifications"] = max(levels, key=ORDER.index) if levels else "important"
        bind.execute(sa.text("UPDATE users SET settings = CAST(:s AS jsonb) WHERE id = :u"),
                     {"s": json.dumps(settings), "u": uid})


def downgrade() -> None:
    op.execute(
        "UPDATE users SET settings = (settings - 'mail_notifications') || jsonb_build_object("
        "'desktop_notifications', CASE WHEN settings->>'mail_notifications' = 'primary' THEN 'important' "
        "ELSE settings->>'mail_notifications' END) WHERE settings ? 'mail_notifications'")
