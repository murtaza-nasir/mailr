"""Calendars shared with the user as "free/busy only" (calendar/sharing.py, level `freebusy`).

What Stalwart 1.0.1 does with a grant of only CalDAV `read-free-busy` (verified 2 November 2026,
docs/stalwart-notes.md section 12):

- The calendar is not in the sharee's calendar home listing. A PROPFIND on it, a calendar-query,
  a GET of an event and a PUT all answer 403. JMAP does not offer the owner's account to the sharee
  (`accountNotFound`), and `Principal/getAvailability` answers `forbidden`.
- A CalDAV free-busy-query REPORT on the calendar's path answers 200 with one VFREEBUSY: the
  busy periods in UTC (`FBTYPE=BUSY`), tentative events apart (`FBTYPE=BUSY-TENTATIVE`), repeating
  events expanded, transparent and cancelled events left out, no titles or places. An all-day
  event comes back as UTC midnight to UTC midnight.
- Stalwart 0.16 answers that REPORT with 403, so the level exists only on 1.x.

So mailr asks the server as the sharee. During each sync of the sharee's built-in account,
for every calendar whose owner's ACL gives the sharee (or a group of theirs) only free/busy, mailr
runs the free-busy-query over WINDOW_BEFORE to WINDOW_AFTER from now, as the sharee (impersonation).
A 200 makes a read-only calendar in the sharee's mirror, at the calendar's own address; any other
answer leaves it out. Each busy period becomes one event:

- titled "Busy" in the sharee's language, opaque, marked "no reminders", with no guests,
  description, place or link; a tentative period carries STATUS:TENTATIVE;
- its UID starts with UID_PREFIX (`is_block`), so every reader can tell it apart without another
  lookup (mirror.event_out `shared_busy`; the web and Ambrose show it as "Busy", as the phone
  calendars' busy-only blocks of calendar/phonecal.py);
- a period from UTC midnight to UTC midnight is stored as all-day dates, as the server sends an
  all-day event. A timed event of whole days from midnight UTC reads as all-day too.

Each run replaces the stored blocks (a snapshot). Every other object in the calendar is removed and
its text cleared, so a calendar that was shared at View before keeps no title or detail. When the
owner raises the level to View or more, the calendar is listed again and the next sync replaces the
blocks with the events (the blocks are not on the server, so the full listing removes them).

The agenda, the calendar views, free/busy, Find a time, search and Ambrose read the blocks like
any event. Nothing writes to them: the calendar is read-only, so the writer refuses it.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select, text

from mailr.calendar import dav
from mailr.models import CalCollection, CalObject, User

log = logging.getLogger("mailr.calendar.shared_busy")

UID_PREFIX = "mailr-busy-"
UID_SUFFIX = "@mailr"
WINDOW_BEFORE = timedelta(days=62)
WINDOW_AFTER = timedelta(days=400)
# A sharee's answer larger than this is not stored (a year of busy periods is a few kilobytes).
MAX_BYTES = 2 * 1024 * 1024
MAX_BLOCKS = 10000

_QUERY = ('<?xml version="1.0" encoding="utf-8"?><c:free-busy-query xmlns:c="urn:ietf:params:xml:ns:caldav">'
          '<c:time-range start="{start}" end="{end}"/></c:free-busy-query>')


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime
    tentative: bool = False


class Refused(Exception):
    """The server refused the free-busy-query for this person (no free/busy grant, or 0.16)."""


def is_block(uid: str | None) -> bool:
    """Whether a stored event is a busy block of a free/busy-only calendar."""
    return bool(uid) and str(uid).startswith(UID_PREFIX) and str(uid).endswith(UID_SUFFIX)


def window(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = (now or datetime.now(UTC)).astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return now - WINDOW_BEFORE, now + WINDOW_AFTER


def _utc(v: datetime) -> str:
    return v.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


# ---------------- the request ----------------


async def query(client: dav.DavClient, href: str, start: datetime, end: datetime) -> list[Period]:
    """The busy periods of a calendar, asked as the client's user. Refused on 403 or 404."""
    r = await client.request("REPORT", href, body=_QUERY.format(start=_utc(start), end=_utc(end)),
                             headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"})
    if r.status_code in (401, 403, 404):
        raise Refused(f"free-busy-query {href}: {r.status_code}")
    client._raise(r, f"free-busy-query {href}")
    body = r.content[:MAX_BYTES + 1]
    if len(body) > MAX_BYTES:
        raise dav.DavError(f"free-busy-query {href}: the answer is larger than {MAX_BYTES} bytes", 507)
    return parse(body.decode("utf-8", "replace"))


_DUR = re.compile(r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def _when(v: str) -> datetime | None:
    v = v.strip()
    try:
        if v.endswith("Z"):
            return datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return None  # FREEBUSY periods are in UTC (RFC 5545 3.8.2.6); anything else is skipped


def _duration(v: str) -> timedelta | None:
    m = _DUR.match(v.strip())
    if not m or m.group(1) == "-":
        return None
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    return timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def parse(ics: str) -> list[Period]:
    """The FREEBUSY periods of a VFREEBUSY answer, BUSY-TENTATIVE apart; FREE periods are
    skipped. Sorted, at most MAX_BLOCKS."""
    lines: list[str] = []
    for raw in ics.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    out: set[Period] = set()
    for line in lines:
        name, sep, value = line.partition(":")
        if not sep:
            continue
        parts = name.split(";")
        if parts[0].upper() != "FREEBUSY":
            continue
        params = {k.upper(): v.upper() for k, _, v in (p.partition("=") for p in parts[1:])}
        kind = params.get("FBTYPE", "BUSY")
        if kind == "FREE":
            continue
        for period in value.split(","):
            a, _, b = period.partition("/")
            start = _when(a)
            if start is None or not b:
                continue
            end = _when(b) if not b.strip().upper().startswith(("P", "+P")) else None
            if end is None:
                d = _duration(b)
                end = start + d if d is not None else None
            if end is None or end <= start:
                continue
            out.add(Period(start, end, kind == "BUSY-TENTATIVE"))
    return sorted(out, key=lambda p: (p.start, p.end, p.tentative))[:MAX_BLOCKS]


# ---------------- storage ----------------


def _whole_days(p: Period) -> bool:
    return (p.start.time() == time(0) and p.end.time() == time(0)
            and (p.end - p.start) >= timedelta(days=1) and (p.end - p.start) % timedelta(days=1) == timedelta(0))


def block_id(p: Period) -> str:
    return hashlib.sha256(f"{_utc(p.start)}/{_utc(p.end)}/{int(p.tentative)}".encode()).hexdigest()[:24]


def ics_of(p: Period, title: str) -> str:
    """The block as an iCalendar resource. DTSTAMP is the start, so the text (and its ETag) depends
    on the period alone and a repeated answer changes nothing."""
    if _whole_days(p):
        d0: date = p.start.date()
        when = [f"DTSTART;VALUE=DATE:{d0.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{p.end.date().strftime('%Y%m%d')}"]
    else:
        when = [f"DTSTART:{_utc(p.start)}", f"DTEND:{_utc(p.end)}"]
    title = title.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", " ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//mailr//shared busy times//EN", "BEGIN:VEVENT",
             f"UID:{UID_PREFIX}{block_id(p)}{UID_SUFFIX}", f"DTSTAMP:{_utc(p.start)}", *when, f"SUMMARY:{title}",
             *(["STATUS:TENTATIVE"] if p.tentative else []),
             "TRANSP:OPAQUE", "X-MAILR-NO-REMINDER:TRUE", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


async def store(coll_id: int, periods: list[Period]) -> dict[str, Any]:
    """Replace the calendar's stored blocks with these periods; everything else in it goes, with
    its text cleared. Marks the calendar synced."""
    from mailr.calendar import mirror, phonecal
    from mailr.calendar.writer import _ctx
    from mailr.core.events import record_change
    from mailr.db import session_scope

    async with session_scope() as db:
        coll = await db.get(CalCollection, coll_id)
        if coll is None or coll.deleted_at is not None:
            return {"stored": 0}
        user = await db.get(User, coll.user_id)
        title = phonecal.busy_title(user)
        base = coll.href.rstrip("/")
        items: list[tuple[str, str | None, str]] = []
        for p in periods:
            ics = ics_of(p, title)
            items.append((dav.norm_href(f"{base}/{UID_PREFIX}{block_id(p)}.ics"),
                          hashlib.sha256(ics.encode()).hexdigest()[:32], ics))
        wanted = {h for h, _, _ in items}
        rows = (await db.execute(select(CalObject.id, CalObject.href).where(
            CalObject.collection_id == coll.id, CalObject.deleted_at.is_(None)))).all()
        gone = [r.id for r in rows if r.href not in wanted]
        user_tz, me = await _ctx(db, coll.user_id)
        changed = await mirror.upsert_many(db, coll, items, user_tz=user_tz, me=me) if items else []
        if gone:
            await mirror.soft_delete_many(db, gone)
            await record_change(db, coll.user_id, "event", gone, op="delete")
        # A tombstone keeps no text: an event seen while the calendar was shared at View does not
        # stay behind once only busy times are shared.
        await db.execute(text(
            "UPDATE cal_objects SET ics = '', summary = '', location = '', description = '' "
            "WHERE collection_id = :c AND deleted_at IS NOT NULL "
            "AND (ics <> '' OR summary <> '' OR location <> '' OR description <> '')"), {"c": coll.id})
        if changed:
            await record_change(db, coll.user_id, "event", changed)
        now = datetime.now(UTC)
        coll.last_sync_at = coll.last_full_sync_at = now
        coll.sync_status, coll.sync_error, coll.fail_count = "idle", "", 0
        coll.sync_token, coll.ctag = None, None
        if changed or gone:
            await mirror.mark_collection_rev(db, coll)
    if changed or gone:
        log.info("free/busy calendar %s: %d blocks, %d changed, %d removed", coll_id, len(periods), len(changed),
                 len(gone))
    return {"stored": len(periods), "changed": len(changed), "removed": len(gone)}
