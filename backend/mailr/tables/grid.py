"""The viewer's manual order of the Notes grid, across notes and tables (the
Notes view holds both). Each card's key is `note_viewer_state.order` for (user, kind, item); a move
puts the card between two neighbours named by their public ids (`n…` or `t…`).
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import User
from mailr.notes import access as naccess
from mailr.notes import order
from mailr.notes.models import Note, NoteViewerState
from mailr.tables import access as taccess
from mailr.tables.models import DataTable


async def key(db: AsyncSession, user: User, item: tuple[str, int], *, before: str | None, after: str | None) -> str:
    """A grid order key for `item` (kind, id) next to the card `before` or `after` names."""
    notes = (await db.execute(select(Note.id, Note.pid).where(naccess.visible(user), Note.deleted_at.is_(None)))).all()
    tables = (await db.execute(select(DataTable.id, DataTable.pid).where(taccess.visible(user),
                                                                         DataTable.deleted_at.is_(None)))).all()
    pid_of = {("note", r.id): r.pid for r in notes} | {("table", r.id): r.pid for r in tables}
    states = {(s.item_kind, s.item_id): s for s in (await db.execute(select(NoteViewerState).where(
        NoteViewerState.user_id == user.id))).scalars() if (s.item_kind, s.item_id) in pid_of}
    missing = [k for k in pid_of if k not in states or states[k].order is None]
    if missing:
        keyed = sorted((s for s in states.values() if s.order is not None), key=lambda s: s.order)
        for k in missing:
            if k not in states:
                states[k] = NoteViewerState(user_id=user.id, item_kind=k[0], item_id=k[1], labels=[])
                db.add(states[k])
        allrows = [states[k] for k in missing] + keyed
        for s, nk in zip(allrows, order.spread(len(allrows)), strict=False):
            s.order = nk
        await db.flush()
    rows = sorted((s for k, s in states.items() if k != item), key=lambda s: (s.order, s.item_kind, s.item_id))
    pids = [pid_of.get((s.item_kind, s.item_id)) for s in rows]
    target = before or after
    if target not in pids:
        raise HTTPException(404, "note not found")
    k = pids.index(target)
    if before:
        lo, hi = (rows[k - 1].order if k > 0 else None), rows[k].order
    else:
        lo, hi = rows[k].order, (rows[k + 1].order if k + 1 < len(rows) else None)
    new = order.between(lo, hi)
    if order.too_long(new):
        for s, nk in zip(rows, order.spread(len(rows)), strict=False):
            s.order = nk
        await db.flush()
        return await key(db, user, item, before=before, after=after)
    return new
