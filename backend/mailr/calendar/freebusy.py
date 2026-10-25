"""Free/busy and "find a time" over the mirror (section 8).

`busy` reads cal_occurrences (GiST range query) for occurrences that count as busy (opaque, not
cancelled, not declined; the `busy` flag the mirror sets) in collections that count toward
free/busy (`CalCollection.busy`), expands series whose materialized window does not cover the
range, and merges the intervals. Tentative occurrences are reported apart, and so are all-day
ones (an all-day "Holiday" or "Conference" does not block every slot unless asked).

`find_time` proposes slots inside the working hours (from the request, else the user's settings,
else 9:00-17:00 Monday to Friday) in the user's zone, so a DST day keeps its wall-clock hours.
Ranking: the earliest working day first; within a day, slots that leave no unusable gap next to
another meeting or the edge of the day come first (less fragmentation), then earlier slots. The
chosen slots never overlap each other.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import expand, ical, mirror
from mailr.calendar import tz as tzmod
from mailr.models import CalObject, CalOccurrence, User

DEFAULT_HOURS = {"start": "09:00", "end": "17:00", "days": [1, 2, 3, 4, 5]}
MAX_RANGE = timedelta(days=62)

Interval = tuple[datetime, datetime]


def merge(intervals: list[Interval]) -> list[Interval]:
    """Sorted, overlapping or touching intervals joined."""
    out: list[list[datetime]] = []
    for s, e in sorted(intervals):
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


@dataclass
class Busy:
    busy: list[Interval]
    tentative: list[Interval]
    all_day: list[tuple[date, date]]  # [start, end) dates of busy all-day occurrences


async def busy(db: AsyncSession, user_id: int, p: Any, start: datetime, end: datetime,
               calendars: list[str] | None = None, *, sync: bool = True) -> Busy:
    """`sync=False` reads the mirror as it is (another person's free/busy, calendar/household.py:
    their calendars are not synced on someone else's request)."""
    if sync:
        await mirror.ensure_synced(db, user_id, p)
    cols = [c for c in await mirror.event_collections(db, user_id, p, calendars) if c.busy]
    out = Busy([], [], [])
    if not cols:
        return out
    ids = [c.id for c in cols]
    user_tz = await tzmod.user_zone(db, user_id)
    uncovered = (await db.execute(select(CalObject).where(
        CalObject.user_id == user_id, CalObject.kind == "event", CalObject.deleted_at.is_(None),
        CalObject.recurring.is_(True), CalObject.collection_id.in_(ids),
        or_(CalObject.expanded_from.is_(None), CalObject.expanded_from > start,
            CalObject.expanded_until.is_(None), CalObject.expanded_until < end),
        or_(CalObject.range_end.is_(None), CalObject.range_end >= start),
        or_(CalObject.range_start.is_(None), CalObject.range_start < end)))).scalars().all()
    skip = {o.id for o in uncovered}
    rows = (await db.execute(
        select(CalOccurrence, CalObject.status).join(CalObject, CalObject.id == CalOccurrence.object_id).where(
            CalOccurrence.user_id == user_id, CalOccurrence.collection_id.in_(ids), CalOccurrence.busy.is_(True),
            CalOccurrence.cancelled.is_(False), CalObject.deleted_at.is_(None),
            text("tstzrange(cal_occurrences.start_at, cal_occurrences.end_at, '[)') && tstzrange(:a, :b, '[)')"))
        .params(a=start, b=end))).all()
    for occ, master_status in rows:
        if occ.object_id in skip:
            continue
        status = (occ.status or master_status or "").upper()
        _add(out, occ.start_at, occ.end_at, occ.all_day, occ.occurrence, status, user_tz)
    todo = [o for o in uncovered
            if (o.transp or "").upper() != "TRANSPARENT" and (o.my_partstat or "") != "DECLINED"]
    # Parsing and expanding run off the event loop, all objects in one hop.
    expanded = await asyncio.to_thread(expand.parse_and_expand_many, [o.ics for o in todo], start, end,
                                       user_tz) if todo else []
    for obj, (cal, insts) in zip(todo, expanded, strict=True):
        if cal is None:
            continue
        for i in insts:
            if i.cancelled or ical.text_of(i.comp, "transp").upper() == "TRANSPARENT":
                continue
            status = (ical.text_of(i.comp, "status") or obj.status or "").upper()
            _add(out, i.start_at, i.end_at, i.all_day, i.occurrence, status, user_tz)
    out.busy = [(max(s, start), min(e, end)) for s, e in merge(out.busy)]
    out.tentative = [(max(s, start), min(e, end)) for s, e in merge(out.tentative)]
    out.all_day = sorted(set(out.all_day))
    return out


def _add(out: Busy, s: datetime, e: datetime, all_day: bool, occurrence: str, status: str, user_tz: tzinfo) -> None:
    if all_day:
        d0 = date.fromisoformat(occurrence) if len(occurrence) == 10 else s.astimezone(user_tz).date()
        d1 = max(d0 + timedelta(days=1), (e.astimezone(user_tz).date()))
        out.all_day.append((d0, d1))
        return
    (out.tentative if status == "TENTATIVE" else out.busy).append((s, e))


# ---------------- find a time ----------------


def _hm(v: str) -> time:
    h, m = (v or "0:0").split(":")[:2]
    return time(int(h) % 24, int(m) % 60)


async def working_hours(db: AsyncSession, user_id: int, given: dict[str, Any] | None) -> dict[str, Any]:
    """The request's working hours, else the user's (`settings.calendar_working_hours`, which
    Settings → Calendar writes, `settings.calendar.working_hours` or `settings.working_hours`),
    else 9:00-17:00 Monday to Friday."""
    if given:
        return {**DEFAULT_HOURS, **{k: v for k, v in given.items() if v is not None}}
    user = await db.get(User, user_id)
    s = (user.settings if user else None) or {}
    saved = (s.get("calendar") or {}).get("working_hours") if isinstance(s.get("calendar"), dict) else None
    saved = s.get("calendar_working_hours") or saved or s.get("working_hours")
    return {**DEFAULT_HOURS, **saved} if isinstance(saved, dict) else dict(DEFAULT_HOURS)


def _local(d: date, t: time, zone: tzinfo) -> datetime:
    """A wall-clock time on a day in `zone`, as UTC (a time skipped by DST moves forward)."""
    naive = datetime.combine(d, t)
    aware = naive.replace(tzinfo=zone)
    back = aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    if back != naive:  # inside a spring-forward gap
        aware = (naive + timedelta(hours=1)).replace(tzinfo=zone)
    return aware.astimezone(UTC)


def find_slots(blocks: Busy, *, start: datetime, end: datetime, zone: tzinfo, duration: timedelta,
               hours: dict[str, Any], buffer: timedelta, granularity: timedelta, limit: int,
               avoid_all_day: bool) -> list[dict[str, Any]]:
    days = {int(d) for d in hours.get("days") or [1, 2, 3, 4, 5]}
    h0, h1 = _hm(hours["start"]), _hm(hours["end"])
    taken = merge([(s - buffer, e + buffer) for s, e in blocks.busy + blocks.tentative])
    blocked_days: set[date] = set()
    if avoid_all_day:
        for d0, d1 in blocks.all_day:
            d = d0
            while d < d1:
                blocked_days.add(d)
                d += timedelta(days=1)
    cands = []
    day = start.astimezone(zone).date()
    last = end.astimezone(zone).date()
    work_index = 0
    while day <= last:
        if day.isoweekday() not in days or day in blocked_days:
            day += timedelta(days=1)
            continue
        w0, w1 = _local(day, h0, zone), _local(day, h1, zone)
        if w1 <= w0:
            day += timedelta(days=1)
            continue
        # Free blocks of this working day.
        free, cur = [], w0
        for s, e in taken:
            if e <= cur or s >= w1:
                continue
            if s > cur:
                free.append((cur, min(s, w1)))
            cur = max(cur, e)
        if cur < w1:
            free.append((cur, w1))
        found = False
        for fs, fe in free:
            # Slots start on the granularity grid of the day (wall clock), at or after `start`.
            step = granularity
            t = w0
            while t < fs:
                t += step
            while t < max(start, fs):
                t += step
            while t + duration <= fe and t + duration <= end:
                before = (t - fs).total_seconds() / 60
                after = (fe - (t + duration)).total_seconds() / 60
                dur_m = duration.total_seconds() / 60
                frag = sum(0 if g <= 0 else (3 if g < dur_m else 1) for g in (before, after))
                pos = (t - w0).total_seconds() / max(1.0, (w1 - w0).total_seconds())
                score = round(100 - 10 * work_index - frag - pos, 3)
                cands.append((score, t, t + duration))
                found = True
                t += step
        if found or free:
            work_index += 1
        day += timedelta(days=1)
    cands.sort(key=lambda c: (-c[0], c[1]))
    chosen: list[tuple[float, datetime, datetime]] = []
    for c in cands:
        if any(c[1] < x[2] and x[1] < c[2] for x in chosen):
            continue
        chosen.append(c)
        if len(chosen) >= limit:
            break
    return [{"start": s.isoformat(), "end": e.isoformat(), "score": sc} for sc, s, e in chosen]
