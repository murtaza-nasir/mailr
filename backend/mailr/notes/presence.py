"""Who has a shared list, note or table open.

An open editor of a shared item says so every 15 seconds (`state: "here"`) and once more when it
closes (`state: "left"`). Each signal goes as a live event to the other people who can see the
item: {"type": "presence", "kind": "note" | "table", "id", "person": {"id", "name"}, "state",
"origin", "hello"?}. The server keeps no state: a client shows "Cleo is in this list" while her last
"here" is under 40 seconds old. `hello` marks the first signal of a visit, so the people already
there answer at once and the newcomer sees them without waiting for their next beat.

Only the person's own session sends it; an API token never appears present. Nothing is stored.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import events, live
from mailr.core.auth import Principal, scope
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.notes import acl

router = APIRouter(tags=["notes"])
notes_user = scope("notes")

HEARTBEAT_S = 15
TTL_S = 40


class PresenceIn(BaseModel):
    state: Literal["here", "left"] = "here"
    hello: bool = False


async def _announce(db: AsyncSession, p: Principal, kind: str, obj, body: PresenceIn) -> dict:
    if p.is_token:
        raise ApiError(403, "notes.presence_app_only")
    me = p.user
    ev = {"type": "presence", "kind": kind, "id": obj.pid, "person": {"id": me.id, "name": me.name or me.email},
          "state": body.state, "origin": live.origin_var.get()}
    if body.hello:
        ev["hello"] = True
    sent = 0
    for uid in await acl.audience(db, kind, obj):
        if uid != me.id:
            await events.notify(db, uid, ev)
            sent += 1
    await db.commit()
    return {"ok": True, "heartbeat": HEARTBEAT_S, "ttl": TTL_S, "others": sent}


# ACT note.presence @api
@router.post("/notes/{note_id}/presence")
async def note_presence(note_id: str, body: PresenceIn, p: Principal = Depends(notes_user),
                        db: AsyncSession = Depends(get_db)):
    from mailr.notes import access

    note = await access.get(db, p.user, note_id, p=p)
    return await _announce(db, p, "note", note, body)


# ACT table.presence @api
@router.post("/tables/{table_id}/presence")
async def table_presence(table_id: str, body: PresenceIn, p: Principal = Depends(notes_user),
                         db: AsyncSession = Depends(get_db)):
    from mailr.tables import access

    t = await access.get(db, p.user, table_id)
    return await _announce(db, p, "table", t, body)
