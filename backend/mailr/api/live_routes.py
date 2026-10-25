"""Live editing routes (core/live.py).

GET  /live/{kind}/{id}   an item's current version and editable content, for an editor that
                         opens it or hears that it changed ({"type": "item"} on /events)
POST /live/typing        an editor reports typing, so an agent's write that waits for the user
                         to pause ("Let me finish typing first") holds until they do
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import live, publicid
from mailr.core.auth import Principal, scope
from mailr.db import get_db
from mailr.i18n import ApiError

router = APIRouter(tags=["live"])


def _draft_id(v: str) -> int:
    try:
        return publicid.decode("message", v)
    except publicid.InvalidId:
        raise ApiError(404, "common.draft_not_found") from None


async def item_state(db: AsyncSession, p: Principal, kind: str, item: str) -> tuple[int, dict[str, Any]]:
    """(version, content) of an item the caller may see; 404 otherwise."""
    if kind == "draft":
        from mailr.core.drafts import live_loader, load_draft

        m = await load_draft(db, p, _draft_id(item))
        return await live.state(db, p.user_id, "draft", m.id, live_loader(db, p, m))
    if kind in ("event", "task"):
        from mailr.calendar import live_items, writer

        obj = await writer.find(db, p.user_id, item, kind, p)
        if obj is None:
            raise ApiError(404, f"common.{kind}_not_found", fallback=f"{kind} not found")
        return await live_items.current(db, obj)
    if kind == "memory":
        from mailr.knowledge import memory
        from mailr.knowledge.ops import memory_snapshot

        try:
            m = await memory.find_memory(db, p, int(item))
        except ValueError:
            m = None
        if m is None:
            raise ApiError(404, "common.note_not_found")
        return await live.state(db, p.user_id, "memory", m.id, live.loader(db, m, memory_snapshot))
    if kind == "rule":
        from mailr.api.org_routes import rule_snapshot
        from mailr.models import Rule

        try:
            r = await db.get(Rule, int(item))
        except ValueError:
            r = None
        if r is None or r.user_id != p.user_id:
            raise ApiError(404, "live.filter_not_found")
        return await live.state(db, p.user_id, "rule", r.id, live.loader(db, r, rule_snapshot))
    raise HTTPException(404, "unknown kind")  # i18n: internal


# ACT draft.live_update @api, ACT event.live_update @api, ACT task.live_update @api
@router.get("/live/{kind}/{item}")
async def get_item(kind: str, item: str, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    version, content = await item_state(db, p, kind, item)
    iid = _draft_id(item) if kind == "draft" else item
    row = await live.latest(db, p.user_id, kind, str(iid) if kind != "draft" else str(iid))
    await db.commit()
    return {"kind": kind, "id": live.ref(kind, iid, version)["id"], "version": version, "content": content,
            "by": row.by if row is not None else None}


class TypingIn(BaseModel):
    kind: str = Field(pattern="^(draft|event|task|memory|rule)$")
    id: str | int = Field(...)


# ACT agent.wait_for_typing @api
@router.post("/live/typing")
async def typing(body: TypingIn, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """The user is typing in this item's editor (sent at most about once a second while typing)."""
    from mailr.core.events import notify

    if p.is_token:
        raise ApiError(403, "live.typing_apps_only")
    iid: Any = body.id
    if body.kind == "draft":
        iid = live.public_id("draft", _draft_id(str(body.id)))
    await notify(db, p.user_id, {"type": "typing", "kind": body.kind, "id": str(iid)})
    await db.commit()
    return {"ok": True}
