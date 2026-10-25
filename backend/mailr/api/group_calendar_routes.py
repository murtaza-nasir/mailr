"""Admin → Calendars → Group calendars (calendar/groups.py, site/docs/admin/calendars.md).

An admin attaches calendars to mailr's groups: name, colour, what members may do (read or write)
and, for read-only calendars, named editors. The admin sees only the group calendars' metadata and
item counts, never a user's own calendars or events. The .ics export exists for the copy offered
before a delete.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.api.admin_routes import _audit_admin_change
from mailr.calendar import builtin, dav, groups
from mailr.core.audit import audit
from mailr.core.auth import Principal, admin
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import CalendarGroup, GroupCalendar, User

router = APIRouter(prefix="/admin/calendars", tags=["admin"], dependencies=[Depends(_audit_admin_change)])

_COLOR = r"^#[0-9A-Fa-f]{6}$"


def _person(u: User) -> dict[str, Any]:
    return {"id": u.id, "name": u.name or u.email.split("@")[0], "email": u.email}


async def _calendar_out(db: AsyncSession, gc: GroupCalendar) -> dict[str, Any]:
    eds = [await db.get(User, i) for i in gc.editors or []]
    return {"id": gc.id, "group": gc.group_name, "name": gc.name, "color": gc.color, "access": gc.access,
            "editors": [_person(u) for u in eds if u is not None], "ready": bool(gc.href)}


# ACT settings.group_calendars @api
@router.get("/groups")
async def list_group_calendars(p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """Every known group with its members (for the editor picker) and its calendars."""
    names = await groups.known_groups(db)
    gcs = list((await db.execute(select(GroupCalendar).order_by(GroupCalendar.group_name, GroupCalendar.name)))
               .scalars())
    cgs = {cg.group_name: cg for cg in (await db.execute(select(CalendarGroup))).scalars()}
    counts = await asyncio.gather(*(groups.count(gc, cgs.get(gc.group_name)) for gc in gcs))
    by_group: dict[str, list[dict[str, Any]]] = {}
    for gc, n in zip(gcs, counts, strict=True):
        by_group.setdefault(gc.group_name, []).append({**await _calendar_out(db, gc),
                                                       "items": n})
    out = []
    for name in names:
        members = await groups.members(db, name)
        out.append({"name": name, "members": [_person(u) for u in members], "calendars": by_group.get(name, [])})
    return {"enabled": await builtin.instance_enabled(db), "groups": out}


class GroupCalendarIn(BaseModel):
    group: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    color: str | None = Field(None, pattern=_COLOR)
    access: str = Field("write", pattern="^(read|write)$")
    editors: list[int] = Field(default_factory=list, max_length=100)


def _fail(e: Exception) -> HTTPException:
    if isinstance(e, groups.GroupCalendarError):
        return HTTPException(400, str(e))
    if isinstance(e, dav.DavError):
        return ApiError(502, "calendar.server_rejected_change", error=str(e)[:256])
    return ApiError(502, "calendar.store_rejected_change", error=str(e)[:259])


@router.post("/groups")
async def create_group_calendar(body: GroupCalendarIn, p: Principal = Depends(admin),
                                db: AsyncSession = Depends(get_db)):
    try:
        gc = await groups.create(db, body.group, body.name, body.color, body.access, body.editors)
    except Exception as e:  # noqa: BLE001
        raise _fail(e) from None
    await audit(db, p, "admin.group_calendar.add", body.group,
                {"name": gc.name, "access": gc.access, "editors": gc.editors}, always=True)
    await db.commit()
    return await _calendar_out(db, gc)


class GroupCalendarPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    color: str | None = Field(None, pattern=_COLOR)
    access: str | None = Field(None, pattern="^(read|write)$")
    editors: list[int] | None = Field(None, max_length=100)


async def _get(db: AsyncSession, cid: int) -> GroupCalendar:
    gc = await db.get(GroupCalendar, cid)
    if gc is None:
        raise ApiError(404, "calendar.group_calendar_not_found")
    return gc


@router.patch("/groups/{cid}")
async def patch_group_calendar(cid: int, body: GroupCalendarPatch, p: Principal = Depends(admin),
                               db: AsyncSession = Depends(get_db)):
    gc = await _get(db, cid)
    try:
        await groups.update(db, gc, name=body.name, color=body.color, editors=body.editors, access=body.access)
    except Exception as e:  # noqa: BLE001
        raise _fail(e) from None
    await audit(db, p, "admin.group_calendar.update", gc.group_name,
                {"id": cid, **body.model_dump(exclude_none=True)}, always=True)
    await db.commit()
    return await _calendar_out(db, gc)


@router.get("/groups/{cid}/export")
async def export_group_calendar(cid: int, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """The group calendar as one .ics file (offered before a delete)."""
    from mailr.calendar.routes_collections import _ics_response, merge_ics

    gc = await _get(db, cid)
    try:
        texts = await groups.items(gc, await db.get(CalendarGroup, gc.group_name))
    except Exception as e:  # noqa: BLE001
        raise _fail(e) from None
    return _ics_response(merge_ics(texts, gc.name), gc.name)


@router.delete("/groups/{cid}")
async def delete_group_calendar(cid: int, confirm: str = "", p: Principal = Depends(admin),
                                db: AsyncSession = Depends(get_db)):
    """Delete the calendar and everything in it, for every member. `confirm` must equal its name.
    The group's store accounts go with its last calendar."""
    gc = await _get(db, cid)
    if confirm != gc.name:
        raise ApiError(400, "calendar.confirm_calendar_name")
    try:
        await groups.delete(db, gc)
    except Exception as e:  # noqa: BLE001
        raise _fail(e) from None
    await audit(db, p, "admin.group_calendar.delete", gc.group_name, {"id": cid, "name": gc.name}, always=True)
    await db.commit()
    return {"ok": True}
