"""Settings → Activity: GET /activity and POST /activity/{id}/undone.

core/activity.py says what an entry is, who made it, and when it offers an Undo. The client sends
the entry's `undo` request itself (with the session's own rights), then marks the entry undone.
Only the signed-in user reads their activity; an agent token cannot.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from mailr import i18n
from mailr.core import activity
from mailr.core.auth import Principal, current
from mailr.db import get_db
from mailr.i18n import ApiError

router = APIRouter(tags=["activity"])


def _session_only(p: Principal) -> None:
    if p.is_token:
        raise ApiError(403, "activity.signed_in_only")


# ACT activity.list @api
@router.get("/activity")
async def list_activity(cursor: str | None = None, limit: int = Query(50, ge=1, le=100),
                        who: str | None = Query(None, pattern="^(all|you|ambrose|agents|admin)$"),
                        app: str | None = Query(None, pattern="^(mail|calendar|tasks|notes|tables|settings|account)$"),
                        p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """What you, Ambrose and your agents changed, newest first, and your administrator's changes
    to your account. → {items: [{id, at, who, by, app, text, target, count, undo, undone, ok}], next_cursor}."""
    _session_only(p)
    try:
        cur = int(cursor) if cursor else None
    except ValueError:
        raise HTTPException(400, "bad cursor") from None  # i18n: internal
    return await activity.feed(db, p.user_id, cursor=cur, limit=limit, who=None if who == "all" else who, app=app,
                               lang=i18n.user_language(p.user))


# ACT activity.undo @api (the client sends the entry's undo request, then marks it here)
@router.post("/activity/{entry_id}/undone")
async def mark_undone(entry_id: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    if not await activity.mark_undone(db, p.user_id, entry_id):
        raise ApiError(404, "activity.entry_not_found")
    await db.commit()
    return {"ok": True}
