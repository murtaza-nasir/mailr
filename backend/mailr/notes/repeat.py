"""Repeating checklists.

A list may repeat: weekly on a day (a chores list on Sunday) or every N days (a packing list for a
weekly trip). On each repeat its ticked items are unticked, history kept: every check stays in
`checkitem_checks` (learned order and usual items read it), and the reset is a version of its own
("Repeat: unticked 6 items") that Version history can restore. Nothing is deleted.

The rule is a plain RRULE, the same text tasks and events use (lib/rrule.ts describes it), in one
of two shapes:

- `FREQ=WEEKLY;BYDAY=SU`, optionally `;INTERVAL=2` (every 2 weeks on Sunday);
- `FREQ=DAILY;INTERVAL=3` (every 3 days).

The reset happens at 04:00 in the time zone of the person who set the repeat (late-night ticks
stay ticked until morning). `repeat_next` is when the next reset is due; the scheduler
(`run_due`, every minute) resets the lists whose time has come. A server that was down for a
while resets once and plans the next one from now: it never replays missed repeats.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.notes.models import Note

log = logging.getLogger("mailr.notes.repeat")

RESET_AT = time(4, 0)
DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")  # Python's weekday() order
BY = {"kind": "repeat", "name": "Repeat"}
MAX_DAYS = 365
MAX_WEEKS = 52


def parse(rule: str | None) -> dict[str, Any] | None:
    """The rule as {"freq": "weekly", "day": "SU", "interval": 1} or {"freq": "daily", "interval": 3};
    None for an empty rule. A rule of another shape raises ValueError."""
    raw = re.sub(r"^RRULE:", "", str(rule or "").strip(), flags=re.IGNORECASE)
    if not raw:
        return None
    parts: dict[str, str] = {}
    for p in raw.split(";"):
        k, sep, v = p.partition("=")
        if not sep:
            raise ValueError("a repeat rule is FREQ=WEEKLY;BYDAY=SU or FREQ=DAILY;INTERVAL=3")
        parts[k.strip().upper()] = v.strip().upper()
    freq = parts.pop("FREQ", "")
    interval_s = parts.pop("INTERVAL", "1")
    if not interval_s.isdigit() or int(interval_s) < 1:
        raise ValueError("the interval is a whole number from 1")
    interval = int(interval_s)
    if freq == "WEEKLY":
        day = parts.pop("BYDAY", "")
        if day not in DAYS:
            raise ValueError("a weekly list repeats on one day (BYDAY=MO … SU)")
        if interval > MAX_WEEKS:
            raise ValueError(f"a weekly list repeats at most every {MAX_WEEKS} weeks")
        out: dict[str, Any] = {"freq": "weekly", "day": day, "interval": interval}
    elif freq == "DAILY":
        if interval > MAX_DAYS:
            raise ValueError(f"a list repeats at most every {MAX_DAYS} days")
        out = {"freq": "daily", "interval": interval}
    else:
        raise ValueError("a list repeats weekly on a day or every N days")
    if parts:
        raise ValueError("a list's repeat takes FREQ, INTERVAL and BYDAY only")
    return out


def canonical(rule: str | None) -> str | None:
    """The rule in its one spelling (`FREQ=WEEKLY;BYDAY=SU`, `FREQ=DAILY;INTERVAL=3`), or None."""
    r = parse(rule)
    if r is None:
        return None
    if r["freq"] == "weekly":
        return f"FREQ=WEEKLY;BYDAY={r['day']}" + (f";INTERVAL={r['interval']}" if r["interval"] > 1 else "")
    return f"FREQ=DAILY;INTERVAL={r['interval']}"


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001  an unknown zone name: UTC
        return ZoneInfo("UTC")


def _at(d: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(d, RESET_AT, tzinfo=zone).astimezone(UTC)


def first(rule: str, zone_name: str | None, now: datetime) -> datetime:
    """The first reset of a rule set at `now`: the next of its weekday at 04:00 (not today), or
    N days from today at 04:00."""
    r = parse(rule)
    assert r is not None
    zone = _zone(zone_name)
    today = now.astimezone(zone).date()
    if r["freq"] == "daily":
        return _at(today + timedelta(days=r["interval"]), zone)
    ahead = (DAYS.index(r["day"]) - today.weekday()) % 7 or 7
    return _at(today + timedelta(days=ahead), zone)


def after_reset(rule: str, zone_name: str | None, due: datetime, now: datetime) -> datetime:
    """The reset after the one due at `due`, done at `now`: the cadence from the due day, moved
    on until it lies after now (missed repeats are not replayed)."""
    r = parse(rule)
    assert r is not None
    zone = _zone(zone_name)
    step = timedelta(days=r["interval"] * (7 if r["freq"] == "weekly" else 1))
    d = due.astimezone(zone).date() + step
    while _at(d, zone) <= now:
        d += step
    return _at(d, zone)


def out(note: Note) -> dict[str, Any] | None:
    """The `repeat` of a note in the API: {rule, next, last, zone} or None (zone: the setter's, where
    the reset happens at 04:00, so a client says the reset's day there)."""
    if not note.repeat_rule:
        return None
    from mailr.notes.core import iso

    return {"rule": note.repeat_rule, "next": iso(note.repeat_next), "last": iso(note.repeat_last),
            "zone": note.repeat_zone}


async def set_rule(db: AsyncSession, user: Any, note: Note, rule: str | None) -> None:
    """A person set (or cleared) the repeat: the rule, their zone and the first reset."""
    from mailr.calendar.tz import user_zone

    if note.kind != "checklist" and rule:
        raise HTTPException(422, "Only a list repeats")
    try:
        rule = canonical(rule)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if rule is None:
        note.repeat_rule = note.repeat_zone = note.repeat_next = None
        return
    zone = await user_zone(db, user)
    note.repeat_rule = rule
    note.repeat_zone = zone.key
    note.repeat_next = first(rule, zone.key, datetime.now(UTC))


async def reset(db: AsyncSession, note: Note, now: datetime | None = None) -> int:
    """One repeat: every ticked item is unticked (history kept), a revision records it, and the
    next reset is planned. Returns how many items were unticked."""
    from mailr.core import live
    from mailr.notes import core

    now = now or datetime.now(UTC)
    rows = [i for i in await core.items_of(db, note) if i.checked]
    token = live.by_var.set("repeat")
    try:
        aud = await core.audience(db, note)
        for i in rows:
            i.checked, i.checked_at, i.checked_by = False, None, None
            i.updated_by, i.updated_at = dict(BY), now
            await core.commit_item(db, note, i, aud)
        if note.repeat_rule and note.repeat_next is not None:
            note.repeat_next = after_reset(note.repeat_rule, note.repeat_zone, note.repeat_next, now)
        note.repeat_last = now
        if rows:
            note.updated_at = now
            note.updated_by = dict(BY)
            await core.snapshot(db, note, None, "repeat", by=dict(BY), now=now)
        await core.touch(db, note)
    finally:
        live.by_var.reset(token)
    return len(rows)


async def run_due(now: datetime | None = None) -> int:
    """The scheduler's minute: reset every list whose time has come. Returns how many lists."""
    from mailr.db import session_scope

    now = now or datetime.now(UTC)
    done = 0
    async with session_scope() as db:
        due = (await db.execute(select(Note).where(
            Note.repeat_next.is_not(None), Note.repeat_next <= now, Note.deleted_at.is_(None),
            Note.kind == "checklist").order_by(Note.repeat_next).limit(200).with_for_update(skip_locked=True))
        ).scalars().all()
        for note in due:
            try:
                async with db.begin_nested():
                    await reset(db, note, now)
                done += 1
            except Exception:
                log.exception("repeat of list %s failed", note.pid)
                note.repeat_next = now + timedelta(hours=1)  # try again later; never every minute
    return done
