"""Find a time with household members: the free/busy of people in your groups, from
their built-in calendars, when they have chosen to share it.

- **The grant.** Each person decides: Settings → Calendar → "Share my free/busy with my groups"
  (`users.settings.calendar_share_free_busy`, off by default). Only busy times leave the calendar:
  no titles, places or guests.
- **Who sees it.** Other active people who share at least one mailr group with them
  (core/access.effective_groups). The instance's calendar sharing setting applies: "nobody" turns
  this off for everyone. The built-in calendars must be on for the person (calendar/builtin.shown_for).
- **Which calendars.** Only the person's own calendars on the built-in connector that count toward
  free/busy (CalCollection.busy). Calendars of an account marked local only are left out, and so
  are other accounts (Google, a university server): their copies live on mailr, but the person
  connected them for themselves.
- **Who asks.** The signed-in person only, in Find a time. An agent token is refused, and Ambrose
  has no tool for it.
- The mirror is read as it is: someone else's request never syncs their calendars.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.auth import Principal, current
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import CalCollection, Connector, User

SETTING = "calendar_share_free_busy"
MAX_RANGE = timedelta(days=62)

router = APIRouter(tags=["calendar"])


def shares(user: User) -> bool:
    return (user.settings or {}).get(SETTING) is True


async def people(db: AsyncSession, me: User) -> list[User]:
    """The people whose free/busy `me` may see: in a group with me, sharing, allowed by policy."""
    from mailr.calendar import builtin, sharing
    from mailr.core.access import effective_groups

    if await sharing.policy(db) == "nobody":
        return []
    mine = set(effective_groups(me))
    if not mine:
        return []
    users = (await db.execute(select(User).where(User.disabled.is_(False), User.id != me.id)
                              .order_by(User.name, User.email))).scalars().all()
    out = []
    for u in users:
        if shares(u) and mine & set(effective_groups(u)) and await builtin.shown_for(db, u.id):
            out.append(u)
    return out


async def calendars_of(db: AsyncSession, user_id: int) -> list[str]:
    """The ids of a person's built-in calendars that count toward free/busy."""
    rows = (await db.execute(
        select(CalCollection.id).join(Connector, Connector.id == CalCollection.connector_id).where(
            Connector.user_id == user_id, Connector.builtin.is_(True),
            Connector.data_class != "local_only", CalCollection.deleted_at.is_(None),
            CalCollection.busy.is_(True)))).scalars().all()
    return [str(i) for i in rows]


async def busy_of(db: AsyncSession, user: User, start: datetime, end: datetime):
    from mailr.calendar import freebusy

    cals = await calendars_of(db, user.id)
    if not cals:
        return freebusy.Busy([], [], [])
    return await freebusy.busy(db, user.id, None, start, end, cals, sync=False)


async def merged_busy(db: AsyncSession, me: User, ids: list[int], start: datetime, end: datetime):
    """The busy and tentative intervals of the chosen people (ids not allowed are ignored)."""
    allowed = {u.id: u for u in await people(db, me)}
    busy: list[tuple[datetime, datetime]] = []
    tentative: list[tuple[datetime, datetime]] = []
    for i in ids:
        u = allowed.get(int(i))
        if u is None:
            continue
        b = await busy_of(db, u, start, end)
        busy += b.busy
        tentative += b.tentative
    return busy, tentative


def _person(u: User) -> dict[str, Any]:
    return {"id": u.id, "name": u.name or u.email.split("@")[0], "email": u.email}


# ACT event.find_time_household @api
@router.get("/calendar/household/freebusy")
async def household_freebusy(frm: str | None = Query(None, alias="from"), to: str | None = None,
                             p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """The busy times of the people in my groups who share their free/busy (my own are at
    GET /calendar/freebusy). → {people: [{id, name, email, busy, tentative, all_day}], sharing}."""
    from mailr.calendar.routes import _range

    if p.is_token:
        raise ApiError(403, "calendar.group_freebusy_user_only")
    from mailr.calendar import tz as tzmod

    start, end = _range(frm, to, default_days=7, zone=await tzmod.user_zone(db, p.user_id))
    if end - start > MAX_RANGE:
        raise ApiError(400, "calendar.range_62_days")
    out = []
    for u in await people(db, p.user):
        b = await busy_of(db, u, start, end)
        out.append({**_person(u),
                    "busy": [{"start": s.isoformat(), "end": e.isoformat()} for s, e in b.busy],
                    "tentative": [{"start": s.isoformat(), "end": e.isoformat()} for s, e in b.tentative],
                    "all_day": [{"start": s.isoformat(), "end": e.isoformat()} for s, e in b.all_day]})
    await db.commit()
    return {"from": start.isoformat(), "to": end.isoformat(), "people": out, "sharing": shares(p.user)}
