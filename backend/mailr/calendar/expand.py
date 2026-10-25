"""Recurrence expansion (recurring-ical-events) and the materialized occurrence window.

`cal_occurrences` holds every event instance from WINDOW_BACK before now to WINDOW_AHEAD after
it, rewritten per object whenever the object changes. Ranges outside that window are expanded
on read with the same `instances()` function. Expansion stops at CAP instances per object.

A rule that would generate more than MAX_GENERATED occurrences between its DTSTART and the start
of the asked range (a sub-daily rule that started long ago, say) is not expanded at all: the
library walks every occurrence from DTSTART, before the range too, so such a rule could hold
the worker for hours. The series then shows its first occurrence only (`costly`). Inside the
range CAP stops the walk. Invitations and imports refuse such rules outright (`rule_problem`).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

import recurring_ical_events
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import ical

log = logging.getLogger("mailr.calendar")

WINDOW_BACK = timedelta(days=365)
WINDOW_AHEAD = timedelta(days=730)
CAP = 3000
# The most occurrences one expansion may generate, counted from DTSTART (not from the window).
MAX_GENERATED = 50_000
# Past this many occurrences inside the asked range a rule is expanded only over a slice of it.
DENSE = 20_000
# The longest one materialisation may take off the event loop before the master alone is kept.
EXPAND_DEADLINE = 20.0

_PERIOD = {"SECONDLY": 1, "MINUTELY": 60, "HOURLY": 3600, "DAILY": 86400, "WEEKLY": 7 * 86400,
           "MONTHLY": 28 * 86400, "YEARLY": 365 * 86400}
_RANK = ["SECONDLY", "MINUTELY", "HOURLY", "DAILY", "WEEKLY", "MONTHLY", "YEARLY"]
# BYxxx parts that add occurrences within one period: (part, the FREQs it expands, the most
# occurrences one entry can add per period). For other FREQs a part only filters.
_EXPANDS = (("BYSECOND", {"MINUTELY", "HOURLY", "DAILY", "WEEKLY", "MONTHLY", "YEARLY"}, 1),
            ("BYMINUTE", {"HOURLY", "DAILY", "WEEKLY", "MONTHLY", "YEARLY"}, 1),
            ("BYHOUR", {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}, 1),
            ("BYDAY", {"WEEKLY"}, 1), ("BYDAY", {"MONTHLY"}, 5), ("BYDAY", {"YEARLY"}, 53),
            ("BYMONTHDAY", {"MONTHLY"}, 1), ("BYMONTHDAY", {"YEARLY"}, 12),
            ("BYYEARDAY", {"YEARLY"}, 1), ("BYWEEKNO", {"YEARLY"}, 7), ("BYMONTH", {"YEARLY"}, 1))


@dataclass
class Instance:
    occurrence: str
    start_at: datetime
    end_at: datetime
    all_day: bool
    start: datetime | date
    end: datetime | date
    is_override: bool
    cancelled: bool
    comp: Any


def _bounds(master: Any, start: datetime, end: datetime, user_tz: tzinfo) -> tuple[Any, Any]:
    """Window bounds in the form the master uses (naive local times for floating events)."""
    v = getattr(master.get("dtstart"), "dt", None)
    if isinstance(v, datetime) and v.tzinfo is None:
        return start.astimezone(user_tz).replace(tzinfo=None), end.astimezone(user_tz).replace(tzinfo=None)
    return start, end


def _instance(comp: Any, cal: Any, user_tz: tzinfo, override_rids: set[str]) -> Instance | None:
    f_start, _, _ = ical.value(comp.get("dtstart"), cal, user_tz)
    if f_start is None:
        return None
    all_day = isinstance(f_start, date) and not isinstance(f_start, datetime)
    f_end, _, _ = ical.value(comp.get("dtend"), cal, user_tz)
    if f_end is None and comp.get("duration") is not None:
        dur = getattr(comp.get("duration"), "dt", None)
        if isinstance(dur, timedelta):
            f_end = f_start + dur
    if f_end is None:
        f_end = f_start + timedelta(days=1) if all_day else f_start
    if all_day and isinstance(f_end, datetime):
        f_end = f_end.date()
    rid = comp.get("recurrence-id")
    rv = ical.value(rid, cal, user_tz)[0] if rid is not None else f_start
    occ = ical.canonical_occurrence(rv)
    start_at = ical.as_utc(f_start, user_tz)
    end_at = ical.as_utc(f_end, user_tz)
    end_at = max(end_at, start_at)
    return Instance(occurrence=occ, start_at=start_at, end_at=end_at, all_day=all_day, start=f_start, end=f_end,
                    is_override=occ in override_rids, cancelled=ical.text_of(comp, "status").upper() == "CANCELLED",
                    comp=comp)


def _overlaps(i: Instance, start: datetime, end: datetime) -> bool:
    """[start_at, end_at) meets [start, end); a zero-length instance counts at its start."""
    if i.start_at == i.end_at:
        return start <= i.start_at < end
    return i.start_at < end and i.end_at > start


def instances(cal: Any, start: datetime, end: datetime, user_tz: tzinfo, *, cap: int = CAP,
              component: str = "VEVENT") -> tuple[list[Instance], bool]:
    """Instances of the resource's series overlapping [start, end), in start order, and whether
    the cap cut the list short."""
    comps = list(cal.walk(component))
    if not comps:
        return [], False
    master = next((c for c in comps if c.get("recurrence-id") is None), comps[0])
    override_rids = set()
    for c in comps:
        if c.get("recurrence-id") is not None:
            override_rids.add(ical.canonical_occurrence(ical.value(c.get("recurrence-id"), cal, user_tz)[0]))
    recurring = master.get("rrule") is not None or master.get("rdate") is not None
    out: list[Instance] = []
    if not recurring:
        for c in comps:
            inst = _instance(c, cal, user_tz, set())
            if inst and _overlaps(inst, start, end):
                out.append(inst)
        return out[:1], False
    lo, _ = _bounds(master, start, end, user_tz)
    truncated = False
    # The library walks every occurrence from DTSTART up to `start` before the first one it
    # yields; past `start` the cap stops it.
    if costly(cal, start, user_tz, component):
        log.warning("not expanding %s: its rule generates too many occurrences", ical.text_of(master, "uid"))
        inst = _instance(master, cal, user_tz, set())
        return ([inst] if inst and _overlaps(inst, start, end) else []), False
    try:
        dense = _estimate(master, end, user_tz) - _estimate(master, start, user_tz)
    except Exception:  # noqa: BLE001
        dense = 0.0
    try:
        query = recurring_ical_events.of(cal, components=[component])
        if dense > DENSE:
            # A dense rule (sub-daily, say): `after()` walks in growing time slices and one slice
            # can hold millions of occurrences, so only the part of the range that holds about
            # enough occurrences for the cap is expanded, in one bounded call.
            target = min(max(2 * cap, 1000), MAX_GENERATED)
            short_end = start + (end - start) * (target / dense)
            short_end = max(short_end, start + timedelta(minutes=1))
            s_lo, s_hi = _bounds(master, start, short_end, user_tz)
            got = [i for i in (_instance(c, cal, user_tz, override_rids) for c in query.between(s_lo, s_hi))
                   if i is not None and _overlaps(i, start, end)]
            got.sort(key=lambda i: i.start_at)
            return got[:cap], True
        for comp in query.after(lo):
            inst = _instance(comp, cal, user_tz, override_rids)
            if inst is None:
                continue
            if inst.start_at >= end:
                break
            if not _overlaps(inst, start, end):
                continue
            out.append(inst)
            if len(out) >= cap:
                truncated = True
                break
    except Exception as e:  # noqa: BLE001  a rule the library cannot expand: the master alone
        log.warning("cannot expand %s: %s", ical.text_of(master, "uid"), e)
        inst = _instance(master, cal, user_tz, set())
        out = [inst] if inst and _overlaps(inst, start, end) else []
    return out, truncated


def _estimate(master: Any, end: datetime, user_tz: tzinfo) -> float:
    """An upper estimate of the occurrences the master's rule generates from DTSTART to `end`."""
    rule = master.get("rrule")
    total = float(len([d for p in ical.as_list(master.get("rdate")) for d in (getattr(p, "dts", None) or [p])]))
    if rule is None:
        return total
    for r in ical.as_list(rule):
        freq = str((r.get("FREQ") or [""])[0]).upper()
        if freq not in _PERIOD:
            return float("inf")
        try:
            interval = max(1, int((r.get("INTERVAL") or [1])[0]))
        except (TypeError, ValueError):
            interval = 1
        start = ical.value(master.get("dtstart"), None, user_tz)[0]
        if start is None:
            continue
        start_utc = ical.as_utc(start, user_tz)
        stop = end
        for u in r.get("UNTIL") or []:
            u_utc = ical.as_utc(u, user_tz)
            if u_utc is not None:
                stop = min(stop, u_utc)
        span = max(0.0, (stop - start_utc).total_seconds())
        n = span / (_PERIOD[freq] * interval) + 1
        for part, freqs, per in _EXPANDS:
            if freq in freqs and r.get(part):
                n *= len(r.get(part)) * per
        count = r.get("COUNT")
        if count:
            try:
                n = min(n, float(int(count[0])))
            except (TypeError, ValueError):
                pass
        total += n
    return total


def costly(cal: Any, end: datetime | None = None, user_tz: tzinfo = UTC, component: str = "VEVENT") -> bool:
    """Would expanding the resource's rule up to `end` (default: the end of the materialised
    window) generate more than MAX_GENERATED occurrences?"""
    comps = list(cal.walk(component))
    if not comps:
        return False
    master = next((c for c in comps if c.get("recurrence-id") is None), comps[0])
    if master.get("rrule") is None and master.get("rdate") is None:
        return False
    try:
        return _estimate(master, end or window()[1], user_tz) > MAX_GENERATED
    except Exception:  # noqa: BLE001  a rule we cannot reason about is not expanded
        return True


def parse_and_expand_many(icss: list[str | bytes], start: datetime, end: datetime, user_tz: tzinfo,
                          cap: int = CAP) -> list[tuple[Any, list[Instance]]]:
    """(calendar, instances in [start, end)) for each resource; (None, []) for one that does not
    parse. Synchronous: read paths call it through asyncio.to_thread, with every object of one
    request in one call."""
    from icalendar import Calendar

    out: list[tuple[Any, list[Instance]]] = []
    for ics in icss:
        try:
            cal = Calendar.from_ical(ics)
        except Exception:  # noqa: BLE001
            out.append((None, []))
            continue
        out.append((cal, instances(cal, start, end, user_tz, cap=cap)[0]))
    return out


def rule_problem(cal: Any, user_tz: tzinfo = UTC) -> str | None:
    """Why mailr refuses to take this resource's repeat rule from outside (an invitation, an
    import): a rule mailr would not write itself (ical.check_rrule), or one too costly to expand.
    None when it is fine."""
    for comp in cal.walk("VEVENT"):
        for r in ical.as_list(comp.get("rrule")):
            try:
                ical.check_rrule(r.to_ical().decode() if hasattr(r, "to_ical") else str(r))
            except Exception as e:  # noqa: BLE001
                return f"the repeat rule is not supported ({e})"
    if costly(cal, user_tz=user_tz):
        return "the repeat rule repeats too often to show"
    return None


def window(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(UTC)
    return now - WINDOW_BACK, now + WINDOW_AHEAD


async def materialize(db: AsyncSession, obj: Any, cal: Any, user_tz: tzinfo, master_fields: dict[str, Any]) -> None:
    """Rewrite the object's rows in cal_occurrences (events only)."""
    from mailr.models import CalOccurrence

    await db.execute(delete(CalOccurrence).where(CalOccurrence.object_id == obj.id))
    if obj.kind != "event" or obj.deleted_at is not None:
        return
    lo, hi = expansion_window(obj.recurring)
    # Off the event loop: the other lanes of the worker (and the API) keep running meanwhile.
    try:
        insts, truncated = await asyncio.wait_for(asyncio.to_thread(instances, cal, lo, hi, user_tz),
                                                  EXPAND_DEADLINE)
    except TimeoutError:
        log.warning("expanding %s took too long; the first occurrence alone is kept", obj.uid)
        comps = list(cal.walk("VEVENT"))
        master = next((c for c in comps if c.get("recurrence-id") is None), comps[0] if comps else None)
        first = _instance(master, cal, user_tz, set()) if master is not None else None
        insts, truncated = ([first] if first else []), True
    rows = occurrence_rows(obj, insts, master_fields)
    for k in range(0, len(rows), 1000):
        await db.execute(insert(CalOccurrence), rows[k:k + 1000])
    set_range(obj, rows, lo, hi, truncated, master_fields)


def expansion_window(recurring: bool) -> tuple[datetime, datetime]:
    """The span materialize expands: the rolling window for a series, all time for one event."""
    if recurring:
        return window()
    return datetime(1, 1, 2, tzinfo=UTC), datetime(9999, 12, 30, tzinfo=UTC)


def occurrence_rows(obj: Any, insts: list[Instance], master_fields: dict[str, Any]) -> list[dict[str, Any]]:
    """The cal_occurrences rows of the object's instances (obj.id must be set)."""
    master_busy = (obj.transp or "").upper() != "TRANSPARENT" and (obj.my_partstat or "") != "DECLINED"
    rows = []
    seen: set[str] = set()
    for i in insts:
        if i.occurrence in seen:
            continue
        seen.add(i.occurrence)
        summary = location = status = None
        transp = obj.transp
        if i.is_override:
            s, loc, st = ical.text_of(i.comp, "summary"), ical.text_of(i.comp, "location"), ical.text_of(i.comp, "status").upper()
            summary = s if s != (master_fields.get("summary") or "") else None
            location = loc if loc != (master_fields.get("location") or "") else None
            status = st if st != (master_fields.get("status") or "") else None
            transp = ical.text_of(i.comp, "transp").upper() or transp
        busy = master_busy and not i.cancelled and (transp or "").upper() != "TRANSPARENT"
        rows.append({"object_id": obj.id, "user_id": obj.user_id, "collection_id": obj.collection_id,
                     "occurrence": i.occurrence[:40], "start_at": i.start_at, "end_at": i.end_at, "all_day": i.all_day,
                     "is_override": i.is_override, "cancelled": i.cancelled, "busy": busy,
                     "summary": summary, "location": location, "status": status})
    return rows


def set_range(obj: Any, rows: list[dict[str, Any]], lo: datetime, hi: datetime, truncated: bool,
              master_fields: dict[str, Any]) -> None:
    """The object's expanded span and the range its occurrences cover."""
    if obj.recurring:
        obj.expanded_from, obj.expanded_until = lo, hi
        rule = (obj.rrule or "").upper()
        finite = "COUNT=" in rule or "UNTIL=" in rule
        obj.range_start = master_fields.get("start_at")
        obj.range_end = max((r["end_at"] for r in rows), default=None) if finite and not truncated and (
            not rows or max(r["end_at"] for r in rows) < hi) else None
    else:
        obj.expanded_from = obj.expanded_until = None
        obj.range_start = master_fields.get("start_at")
        obj.range_end = master_fields.get("end_at")
