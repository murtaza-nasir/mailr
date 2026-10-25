"""settings: "Only mail in the Inbox" on label sections of inbox tabs (8 Oct 2026)

A layout section on a label (`label:<id>`) listed every conversation with that label, archived or
not. In a tab that is read as part of the Inbox (one that also holds Inbox sections, or one that
claims its mail), an archived conversation stayed in its label section, and its Archive button
then found nothing to archive. Sections now have `inbox_only` (core/layouts.py). This turns it on
for every label section in such a tab, for every user, and leaves a tab made only of label
sections without claims (a "Later" tab) as it was.

Safe to run again: a section that already has `inbox_only` (true or false) is never changed.

Revision ID: b7d4e1a9c3f6
Revises: c7e3a9f1d548
Create Date: 2026-10-08 09:00:00

"""
import json
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'b7d4e1a9c3f6'
down_revision: Union[str, Sequence[str], None] = 'c7e3a9f1d548'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    n = mark_inbox_only(op.get_bind())
    log.info("inbox_only turned on for %d layout sections", n)


def mark_inbox_only(bind) -> int:
    """Apply migrate_inbox_only to every user's custom layouts; return how many sections changed
    (tests/test_layout_inbox_only.py)."""
    rows = bind.execute(sa.text(
        "SELECT id, settings->'inbox_layouts' FROM users "
        "WHERE jsonb_typeof(settings->'inbox_layouts') = 'array'")).all()
    total = 0
    for uid, lays in rows:
        if isinstance(lays, str):
            lays = json.loads(lays)
        new, n = migrate_inbox_only(lays)
        if not n:
            continue
        total += n
        bind.execute(sa.text(
            "UPDATE users SET settings = jsonb_set(settings, '{inbox_layouts}', CAST(:v AS jsonb)) WHERE id = :id"),
            {"v": json.dumps(new), "id": uid})
    return total


def migrate_inbox_only(lays):
    """Turn `inbox_only` on for a label section that sits in a tab with `claims` or in a tab
    that also holds Inbox sections.
    Such a section is read as part of the Inbox, so archived mail should leave it. A tab made
    only of label sections without claims (a "Later" tab) is left alone. A section that already
    has the key (the user chose) is never changed, so running it again changes nothing.
    Returns the layouts and the number of sections changed."""
    if not isinstance(lays, list):
        return lays, 0
    n = 0
    out = []
    for lay in lays:
        if not isinstance(lay, dict) or not isinstance(lay.get("tabs"), list):
            out.append(lay)
            continue
        tabs = []
        for tab in lay["tabs"]:
            secs = tab.get("sections") if isinstance(tab, dict) else None
            if not isinstance(secs, list):
                tabs.append(tab)
                continue
            inboxy = bool(tab.get("claims")) or any(
                isinstance(s, dict) and (s.get("base") or "inbox") == "inbox" for s in secs)
            new = []
            for s in secs:
                if (inboxy and isinstance(s, dict) and "inbox_only" not in s
                        and str(s.get("base") or "").startswith("label:")):
                    s = {**s, "inbox_only": True}
                    n += 1
                new.append(s)
            tabs.append({**tab, "sections": new})
        out.append({**lay, "tabs": tabs})
    return out, n


def downgrade() -> None:
    # The key is harmless to older code (it ignores it); nothing to undo.
    pass
