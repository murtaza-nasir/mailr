"""Sharing notes and tables with people on this server (notes/sharing.py).

The signed-in user only: API tokens get 403 on every route here, and the assistant has no sharing
tool (one sharing policy covers notes and tables). The person is a user id, a group
(`group:<name>`) or `me` (leave, and the undo of a leave).

Registered before notes/routes.py, so `/notes/share-notices` is not read as a note id.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.audit import audit
from mailr.core.auth import Principal, current
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.notes import sharing

router = APIRouter(tags=["notes"])


async def session_user(p: Principal = Depends(current)) -> Principal:
    if p.is_token:
        raise ApiError(403, "notes.share_not_by_token")
    return p


class ShareIn(BaseModel):
    level: str = Field(pattern="^(view|edit|share)$")
    # The person's address, as a people search showed it: needed for someone outside your groups who
    # is not on the item yet (notes/sharing._visible).
    email: str | None = Field(None, max_length=320)


class MoveIn(BaseModel):
    group: str | None = Field(None, max_length=200)


async def _done(db: AsyncSession, p: Principal, action: str, target: str, inputs: dict[str, Any], out: Any) -> Any:
    await audit(db, p, action, target, inputs, always=True)
    await db.commit()
    return out


# ACT note.share_notices @api
@router.get("/notes/share-notices")
async def share_notices(p: Principal = Depends(session_user), db: AsyncSession = Depends(get_db)):
    """Notes and tables people shared with the caller since the last dismissal."""
    return {"notices": await sharing.notices(db, p.user_id)}


@router.delete("/notes/share-notices")
async def dismiss_share_notices(p: Principal = Depends(session_user), db: AsyncSession = Depends(get_db)):
    await sharing.dismiss(db, p.user_id)
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- one item's people


async def _list(db: AsyncSession, p: Principal, kind: str, item_id: str, q: str | None = None) -> Any:
    obj = await sharing.load(db, p.user, kind, item_id)
    out = await sharing.describe(db, p.user, kind, obj, q=q)
    await db.commit()
    return out


async def _put(db: AsyncSession, p: Principal, kind: str, item_id: str, principal: str, level: str,
               email: str | None = None) -> Any:
    obj = await sharing.load(db, p.user, kind, item_id)
    pk, pv = sharing.parse_principal(principal)
    if pk == "me":
        raise ApiError(400, "notes.own_access")
    out = await sharing.set_share(db, p.user, kind, obj, pk, pv, level, email=email)
    return await _done(db, p, f"{kind}.share", item_id, {"principal": principal, "level": level}, out)


async def _delete(db: AsyncSession, p: Principal, kind: str, item_id: str, principal: str) -> Any:
    obj = await sharing.load(db, p.user, kind, item_id)
    pk, pv = sharing.parse_principal(principal)
    if pk == "me":
        out = await sharing.leave(db, p.user, kind, obj)
        return await _done(db, p, f"{kind}.leave", item_id, {}, out)
    out = await sharing.set_share(db, p.user, kind, obj, pk, pv, None)
    return await _done(db, p, f"{kind}.unshare", item_id, {"principal": principal}, out)


async def _rejoin(db: AsyncSession, p: Principal, kind: str, item_id: str) -> Any:
    out = await sharing.rejoin(db, p.user, kind, item_id)
    return await _done(db, p, f"{kind}.rejoin", item_id, {}, out)


async def _move(db: AsyncSession, p: Principal, kind: str, item_id: str, group: str | None) -> Any:
    obj = await sharing.load(db, p.user, kind, item_id)
    out = await sharing.move_to_group(db, p.user, kind, obj, group)
    return await _done(db, p, f"{kind}.move_group", item_id, {"group": group}, out)


# ACT note.share @api
@router.get("/notes/{note_id}/shares")
async def note_shares(note_id: str, q: Annotated[str | None, Query(max_length=200)] = None,
                      p: Principal = Depends(session_user), db: AsyncSession = Depends(get_db)):
    """The people dialog: who it is shared with, at which level, and who may be added (the people
    in your groups, plus up to eight others matching a search `q` of three or more characters)."""
    return await _list(db, p, "note", note_id, q)


async def _leave(db: AsyncSession, p: Principal, kind: str, item_id: str) -> Any:
    obj = await sharing.load(db, p.user, kind, item_id)
    out = await sharing.leave(db, p.user, kind, obj)
    return await _done(db, p, f"{kind}.leave", item_id, {}, out)


# ACT note.leave @api (declared before …/shares/{principal}, which would take "me" as a person)
@router.delete("/notes/{note_id}/shares/me")
async def note_leave(note_id: str, p: Principal = Depends(session_user), db: AsyncSession = Depends(get_db)):
    """Leave a note shared with you: it goes from your notes only. The answer's undo adds it back."""
    return await _leave(db, p, "note", note_id)


# ACT note.share @api, ACT note.unshare @api
@router.put("/notes/{note_id}/shares/{principal}")
async def note_share(note_id: str, principal: str, body: ShareIn, p: Principal = Depends(session_user),
                     db: AsyncSession = Depends(get_db)):
    """Share with a person (their user id) or a group (`group:<name>`), or change their level. For
    someone outside your groups who is not on the note yet, also give `email`, the address a people
    search showed; without it the id is answered as an unknown person."""
    return await _put(db, p, "note", note_id, principal, body.level, body.email)


# ACT note.unshare @api
@router.delete("/notes/{note_id}/shares/{principal}")
async def note_unshare(note_id: str, principal: str, p: Principal = Depends(session_user),
                       db: AsyncSession = Depends(get_db)):
    """Stop sharing with a person (their user id) or a group (`group:<name>`)."""
    return await _delete(db, p, "note", note_id, principal)


# ACT note.leave @api (its undo)
@router.post("/notes/{note_id}/shares/me")
async def note_rejoin(note_id: str, p: Principal = Depends(session_user), db: AsyncSession = Depends(get_db)):
    """The undo of a leave: the note is back in your notes, at the level it had."""
    return await _rejoin(db, p, "note", note_id)


# ACT note.move_group @api
@router.post("/notes/{note_id}/move-to-group")
async def note_move_group(note_id: str, body: MoveIn, p: Principal = Depends(session_user),
                          db: AsyncSession = Depends(get_db)):
    """Move it into one of your groups (`group`), or out of its group (`group: null`)."""
    return await _move(db, p, "note", note_id, body.group)


# ACT table.share @api
@router.get("/tables/{table_id}/shares")
async def table_shares(table_id: str, q: Annotated[str | None, Query(max_length=200)] = None,
                       p: Principal = Depends(session_user), db: AsyncSession = Depends(get_db)):
    return await _list(db, p, "table", table_id, q)


# ACT table.leave @api
@router.delete("/tables/{table_id}/shares/me")
async def table_leave(table_id: str, p: Principal = Depends(session_user), db: AsyncSession = Depends(get_db)):
    return await _leave(db, p, "table", table_id)


# ACT table.share @api, ACT table.unshare @api
@router.put("/tables/{table_id}/shares/{principal}")
async def table_share(table_id: str, principal: str, body: ShareIn, p: Principal = Depends(session_user),
                      db: AsyncSession = Depends(get_db)):
    return await _put(db, p, "table", table_id, principal, body.level, body.email)


# ACT table.unshare @api
@router.delete("/tables/{table_id}/shares/{principal}")
async def table_unshare(table_id: str, principal: str, p: Principal = Depends(session_user),
                        db: AsyncSession = Depends(get_db)):
    return await _delete(db, p, "table", table_id, principal)


# ACT table.leave @api (its undo)
@router.post("/tables/{table_id}/shares/me")
async def table_rejoin(table_id: str, p: Principal = Depends(session_user), db: AsyncSession = Depends(get_db)):
    return await _rejoin(db, p, "table", table_id)


# ACT table.move_group @api
@router.post("/tables/{table_id}/move-to-group")
async def table_move_group(table_id: str, body: MoveIn, p: Principal = Depends(session_user),
                           db: AsyncSession = Depends(get_db)):
    return await _move(db, p, "table", table_id, body.group)
