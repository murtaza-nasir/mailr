"""Sharing a calendar with people on this server (calendar/sharing.py, site/docs/user-guide/calendar.md).

The signed-in user only: API tokens and the assistant cannot share calendars. The calendar is one of the caller's own
collections (`calendar_id` from GET /calendar/calendars): their own built-in calendar, or one shared
with them at the share level.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import sharing
from mailr.core.audit import audit
from mailr.core.auth import Principal, scope
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import CalCollection, User

router = APIRouter(tags=["calendar"])


def _session_only(p: Principal) -> None:
    if p.is_token:
        raise ApiError(403, "calendar.share_not_by_token")


async def _own(db: AsyncSession, p: Principal, calendar_id: int) -> tuple[User, CalCollection]:
    from mailr.calendar.routes_collections import _visible

    try:
        coll, _ = await _visible(db, p.user_id, p, calendar_id)
    except LookupError:
        raise ApiError(404, "calendar.calendar_not_found") from None
    user = await db.get(User, p.user_id)
    return user, coll


def _fail(e: Exception) -> HTTPException:
    if isinstance(e, sharing.SharingError):
        return HTTPException(e.status, str(e))
    return ApiError(502, "calendar.server_unexpected", error=str(e)[:252])


# ACT calendar.share @api
@router.get("/calendar/collections/{calendar_id}/shares")
async def list_shares(calendar_id: int, p: Principal = Depends(scope("calendar")), db: AsyncSession = Depends(get_db)):
    """Who the calendar is shared with, at which level, and who else may be added."""
    _session_only(p)
    user, coll = await _own(db, p, calendar_id)
    try:
        return await sharing.describe(db, user, coll)
    except Exception as e:  # noqa: BLE001
        raise _fail(e) from None


class ShareIn(BaseModel):
    level: str = Field(pattern="^(view|edit|share)$")


@router.put("/calendar/collections/{calendar_id}/shares/{user_id}")
async def put_share(calendar_id: int, user_id: int, body: ShareIn, p: Principal = Depends(scope("calendar")),
                    db: AsyncSession = Depends(get_db)):
    """Share with a person, or change their level."""
    _session_only(p)
    user, coll = await _own(db, p, calendar_id)
    try:
        out = await sharing.set_level(db, user, coll, user_id, body.level)
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        raise _fail(e) from None
    await audit(db, p, "calendar.share", coll.href, {"user_id": user_id, "level": body.level}, always=True)
    await db.commit()
    return out


@router.delete("/calendar/collections/{calendar_id}/shares/{user_id}")
async def delete_share(calendar_id: int, user_id: int, p: Principal = Depends(scope("calendar")),
                       db: AsyncSession = Depends(get_db)):
    """Stop sharing with a person. Their copy goes at their next sync (queued now)."""
    _session_only(p)
    user, coll = await _own(db, p, calendar_id)
    try:
        out = await sharing.set_level(db, user, coll, user_id, None)
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        raise _fail(e) from None
    await audit(db, p, "calendar.unshare", coll.href, {"user_id": user_id}, always=True)
    await db.commit()
    return out


# ACT calendar.share_notices @api
@router.get("/calendar/share-notices")
async def share_notices(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """Calendars people shared with the caller since the last dismissal."""
    return {"notices": await sharing.notices(db, p.user_id)}


@router.delete("/calendar/share-notices")
async def dismiss_share_notices(p: Principal = Depends(scope("calendar")), db: AsyncSession = Depends(get_db)):
    await sharing.dismiss(db, p.user_id)
    await db.commit()
    return {"ok": True}
