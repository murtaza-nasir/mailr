"""Time zones from outside: the VTIMEZONEs of invitations, feeds, imports and DAV objects.

A VTIMEZONE is a small program: every UTC-offset lookup in a zone built from it (dateutil's
tzical) walks its rules from their DTSTART to the asked time. A sender who writes
`RRULE:FREQ=MINUTELY` from 1970 makes one lookup cost minutes of CPU and gigabytes of memory.
icalendar also builds such a zone while it parses, and keeps it in one cache for the whole
process, keyed by the TZID: the first message that names "Central Standard Time" or "Customized
Time Zone" defined that zone for every later message and every user.

The rules here:
- A TZID that names a known zone (`tz.zone`: IANA, Windows, aliases, prefixed ids) is always that
  zone. The sender's VTIMEZONE for it is never used and never stored.
- An unknown TZID keeps its own VTIMEZONE only when it is cheap (`zone_problem`): at most
  MAX_PARTS STANDARD/DAYLIGHT parts, DTSTART from MIN_YEAR, rules no finer than MONTHLY, no
  BYYEARDAY/BYWEEKNO, at most one value each in BYHOUR/BYMINUTE/BYSECOND (Lotus Notes and
  Domino write `BYMINUTE=0;BYHOUR=2`), BYSETPOS only as one position of one plain weekday (the
  Windows and Exchange form `BYDAY=SU;BYSETPOS=-1`), COUNT at most MAX_COUNT, at most
  MAX_TRANSITIONS changes up to TRANSITIONS_UNTIL, at most MAX_RDATES dates. Otherwise it becomes a
  fixed offset (`fixed_component`): the offset its newest part names, or UTC.
- At most MAX_ZONES VTIMEZONEs per object; the rest become fixed offsets too.
- `install` replaces icalendar's process-wide cache with these rules. A known name is never cached
  from a sender. An unknown one is the calendar's own, bounded definition, and it lives only for
  the parse of that calendar (a context variable): one message's "Customized Time Zone" never
  defines another message's, for any user, and parses in parallel threads never share one.
- `clean` applies them to a calendar before it is stored (`ical.serialize`).
- `off_loop` runs a parse in a thread with a time budget, so one message never holds the event loop.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any, TypeVar

from mailr.calendar import tz as tzmod

log = logging.getLogger("mailr.calendar.zones")

MAX_ZONES = 20
MAX_PARTS = 12
MAX_RDATES = 60
MAX_COUNT = 500
MAX_TRANSITIONS = 2500
MIN_YEAR = 1600  # Outlook writes DTSTART:16010101T000000 for its own zones
TRANSITIONS_UNTIL = datetime(2100, 1, 1)
ZONE_FREQS = ("YEARLY", "MONTHLY")
_REFUSED_PARTS = ("BYYEARDAY", "BYWEEKNO")
_ALLOWED_PARTS = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYMONTH", "BYDAY", "BYMONTHDAY", "WKST",
                  "BYHOUR", "BYMINUTE", "BYSECOND", "BYSETPOS"}
# A zone part changes once per period, so a time-of-day part names one value.
_TIME_PARTS = {"BYHOUR": 23, "BYMINUTE": 59, "BYSECOND": 59}
MAX_OFFSET = timedelta(hours=18)
# The longest a parse of one calendar from outside may take off the event loop.
PARSE_BUDGET = 15.0
# The largest calendar part of one email that is read at all (invitation, update, reply).
MAX_ITIP_BYTES = 2 * 1024 * 1024

T = TypeVar("T")


def _list(v: Any) -> list[Any]:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _offset(part: Any, key: str = "tzoffsetto") -> timedelta | None:
    v = part.get(key)
    td = getattr(v, "td", None)
    if not isinstance(td, timedelta) or abs(td) > MAX_OFFSET:
        return None
    return td


def _rule_problem(rule: Any, start: datetime) -> str | None:
    """Why one RRULE of a zone part is refused, or None."""
    from dateutil.rrule import rrulestr

    keys = {str(k).upper() for k in rule}
    freq = str((_list(rule.get("FREQ")) or [""])[0]).upper()
    if freq not in ZONE_FREQS:
        return f"a zone rule repeats {freq.lower() or 'without a frequency'}"
    if keys & set(_REFUSED_PARTS) or not keys <= _ALLOWED_PARTS:
        return "a zone rule uses parts no time zone needs"
    try:
        interval = int((_list(rule.get("INTERVAL")) or [1])[0])
        count = int((_list(rule.get("COUNT")) or [0])[0])
        months = [int(m) for m in _list(rule.get("BYMONTH"))]
        days = [int(d) for d in _list(rule.get("BYMONTHDAY"))]
        wdays = [str(d).upper() for d in _list(rule.get("BYDAY"))]
        times = {k: [int(x) for x in _list(rule.get(k))] for k in _TIME_PARTS}
        setpos = [int(x) for x in _list(rule.get("BYSETPOS"))]
    except (TypeError, ValueError):
        return "a zone rule is not readable"
    for k, top in _TIME_PARTS.items():
        if len(times[k]) > 1 or any(not 0 <= x <= top for x in times[k]):
            return "a zone rule is not shaped like a time zone's"
    if not 1 <= interval <= 100 or count < 0 or count > MAX_COUNT:
        return "a zone rule repeats too often"
    # The shapes time zones use: one month; one weekday, with an ordinal ("2SU", "-1SU") or with a
    # run of seven month days ("BYMONTHDAY=8,...,14;BYDAY=SU"); or month days alone. dateutil scans
    # period after period until a rule matches, up to the year 9999, so a rule that can never
    # match ("BYMONTH=2;BYMONTHDAY=30", "BYDAY=6SU") would cost seconds at every first lookup.
    if len(months) > 1 or any(not 1 <= m <= 12 for m in months) or len(wdays) > 1:
        return "a zone rule is not shaped like a time zone's"
    ordinal = wdays[0][:-2] if wdays else ""
    if ordinal and (not ordinal.lstrip("+-").isdigit() or not 1 <= abs(int(ordinal)) <= 5):
        return "a zone rule is not shaped like a time zone's"
    # BYSETPOS: the nth (or nth-last) of one plain weekday in the period, nothing else.
    if setpos and (len(setpos) != 1 or not 1 <= abs(setpos[0]) <= 5 or len(wdays) != 1 or ordinal
                   or _list(rule.get("BYMONTHDAY"))):
        return "a zone rule is not shaped like a time zone's"
    if days:
        if len(days) > 7 or any(not 1 <= abs(d) <= 31 for d in days) or (wdays and ordinal):
            return "a zone rule is not shaped like a time zone's"
        if wdays and (len(days) != 7 or sorted(days) != list(range(min(days), min(days) + 7))
                      or min(days) < 0 < max(days)):
            return "a zone rule is not shaped like a time zone's"
        if not wdays and not any(abs(d) <= 28 for d in days):
            return "a zone rule is not shaped like a time zone's"
    # The changes it makes up to TRANSITIONS_UNTIL, counted with a stop: with the shapes above a
    # MONTHLY or YEARLY rule matches within a few periods, so the count is quick.
    try:
        text = rule.to_ical().decode()
        parts = [p for p in text.split(";") if not p.upper().startswith(("UNTIL=", "COUNT="))]
        until = TRANSITIONS_UNTIL
        u = _list(rule.get("UNTIL"))
        if u:
            uv = u[0]
            uv = uv if isinstance(uv, datetime) else datetime(uv.year, uv.month, uv.day)
            until = min(until, uv.replace(tzinfo=None))
        r = rrulestr(";".join(parts), dtstart=start.replace(tzinfo=None))
        n = 0
        for when in r:
            if when > until:
                break
            n += 1
            if n > MAX_TRANSITIONS:
                return "a zone rule changes the offset too often"
    except Exception:  # noqa: BLE001  a rule dateutil cannot read is not used
        return "a zone rule is not readable"
    return None


_verdicts: dict[bytes, str | None] = {}


def zone_problem(vtz: Any) -> str | None:
    """Why this VTIMEZONE may not be built into a zone (too costly or not a time zone), or None.
    Remembered by the zone's text: the same zone comes with every object that uses it."""
    try:
        key = hashlib.sha256(vtz.to_ical()).digest()
    except Exception:  # noqa: BLE001
        return _zone_problem(vtz)
    if key not in _verdicts:
        if len(_verdicts) > 1024:
            _verdicts.clear()
        _verdicts[key] = _zone_problem(vtz)
    return _verdicts[key]


def _zone_problem(vtz: Any) -> str | None:
    parts = list(getattr(vtz, "subcomponents", []) or [])
    if not parts:
        return "the zone has no rules"
    if len(parts) > MAX_PARTS:
        return "the zone has too many rules"
    rdates = 0
    for part in parts:
        if str(getattr(part, "name", "")).upper() not in ("STANDARD", "DAYLIGHT") or part.subcomponents:
            return "the zone holds something other than its rules"
        start = getattr(part.get("dtstart"), "dt", None)
        if not isinstance(start, datetime) or not MIN_YEAR <= start.year <= TRANSITIONS_UNTIL.year:
            return "a zone rule starts at an impossible time"
        if _offset(part, "tzoffsetto") is None or _offset(part, "tzoffsetfrom") is None:
            return "a zone rule has no valid offset"
        if part.get("exrule") is not None or part.get("exdate") is not None:
            return "a zone rule uses parts no time zone needs"
        rules = _list(part.get("rrule"))
        if len(rules) > 1:
            return "a zone rule repeats in more than one way"
        for rule in rules:
            why = _rule_problem(rule, start)
            if why:
                return why
        for rd in _list(part.get("rdate")):
            rdates += len(getattr(rd, "dts", None) or [rd])
        if rdates > MAX_RDATES:
            return "the zone lists too many dates"
    return None


def current_offset(vtz: Any) -> timedelta:
    """The offset the zone's newest STANDARD part names (else its newest part), or zero (UTC)."""
    best: tuple[int, datetime, timedelta] | None = None
    for part in list(getattr(vtz, "subcomponents", []) or [])[:200]:
        off = _offset(part)
        start = getattr(part.get("dtstart"), "dt", None)
        if off is None or not isinstance(start, datetime):
            continue
        key = (1 if str(part.name).upper() == "STANDARD" else 0, start.replace(tzinfo=None), off)
        if best is None or key[:2] > best[:2]:
            best = key
    return best[2] if best else timedelta(0)


def fixed(vtz: Any) -> tzinfo:
    off = current_offset(vtz)
    return UTC if not off else timezone(off)


def fixed_component(tzid: str, offset: timedelta) -> Any:
    """A one-part VTIMEZONE for a fixed offset, under the sender's TZID (the events keep it)."""
    from icalendar import Timezone, TimezoneStandard

    tzc = Timezone()
    tzc.add("tzid", tzid)
    std = TimezoneStandard()
    std.add("dtstart", datetime(1970, 1, 1))
    std.add("tzoffsetfrom", offset)
    std.add("tzoffsetto", offset)
    tzc.add_component(std)
    return tzc


def build(vtz: Any) -> tzinfo:
    """The zone a VTIMEZONE stands for: the known zone of its TZID; else the zone it defines when
    that is cheap; else a fixed offset."""
    tzid = str(vtz.get("tzid", "") or "")
    known = tzmod.zone(tzid)
    if known is not None:
        return known
    if zone_problem(vtz) is None:
        try:
            from icalendar.timezone import tzp

            return tzp.create_timezone(vtz)
        except Exception:  # noqa: BLE001, S110
            pass
    return fixed(vtz)


def problems(cal: Any) -> list[str]:
    """The TZIDs of this calendar whose own definition is not used (too costly, or past MAX_ZONES);
    a known name is not listed (its standard zone is used, as for everyone)."""
    out: list[str] = []
    n = 0
    for vtz in cal.walk("VTIMEZONE"):
        tzid = str(vtz.get("tzid", "") or "")
        if not tzid or tzmod.zone(tzid) is not None:
            continue
        n += 1
        if n > MAX_ZONES or zone_problem(vtz) is not None:
            out.append(tzid)
    return out


def clean(cal: Any) -> list[str]:
    """Make a calendar from outside safe to store, in place. VTIMEZONEs of known names go (the
    standard ones are added on write), unused and repeated ones go, a costly one becomes a fixed
    offset, and VTIMEZONEs inside other components go. Returns the TZIDs that became fixed."""
    try:
        used = set(cal.get_used_tzids())
    except Exception:  # noqa: BLE001
        used = set()
    replaced: list[str] = []
    seen: set[str] = set()
    kept: list[Any] = []
    for comp in cal.subcomponents:
        if getattr(comp, "name", "") != "VTIMEZONE":
            _drop_nested(comp)
            kept.append(comp)
            continue
        tzid = str(comp.get("tzid", "") or "")
        if not tzid or tzid in seen or tzid not in used or tzmod.zone(tzid) is not None:
            continue
        seen.add(tzid)
        if len(seen) > MAX_ZONES or zone_problem(comp) is not None:
            kept.append(fixed_component(tzid, current_offset(comp)))
            replaced.append(tzid)
        else:
            kept.append(comp)
    cal.subcomponents = kept
    return replaced


def _drop_nested(comp: Any) -> None:
    subs = getattr(comp, "subcomponents", None)
    if not subs:
        return
    comp.subcomponents = [s for s in subs if getattr(s, "name", "") != "VTIMEZONE"]
    for s in comp.subcomponents:
        _drop_nested(s)


# ---------------- icalendar's zone cache, one per parse ----------------

_installed = False
# The unknown TZIDs one parse has defined (clean id -> zone). None outside a parse.
_parse_zones: contextvars.ContextVar[dict[str, tzinfo] | None] = contextvars.ContextVar(
    "mailr_parse_zones", default=None)


def _cache_component(component: Any) -> None:
    """Stands in for icalendar's TZP.cache_timezone_component (called for every VTIMEZONE it
    parses). A known name is never defined by a sender. An unknown one maps to this calendar's own
    definition, bounded (`build`; past MAX_ZONES a fixed offset), for this parse only."""
    from icalendar.timezone import tzp

    try:
        tzid = str(component["TZID"])
    except Exception:  # noqa: BLE001
        return
    if not tzid or tzmod.zone(tzid) is not None:
        return
    mine = _parse_zones.get()
    if mine is None:
        return  # outside a parse: nothing is kept, mailr resolves the TZID itself
    key = tzp.clean_timezone_id(tzid)
    if key in mine:
        return  # the first definition in a calendar wins, as in clean()
    try:
        mine[key] = build(component) if len(mine) < MAX_ZONES else fixed(component)
    except Exception:  # noqa: BLE001
        mine.pop(key, None)


def _scoped_timezone(original: Callable[[str], tzinfo | None]) -> Callable[[str], tzinfo | None]:
    """TZP.timezone with the parse's own unknown zones first. An unknown TZID that this parse did
    not define is never taken from another parse; known names go to icalendar as before."""

    def timezone_(tz_id: str) -> tzinfo | None:
        mine = _parse_zones.get()
        if mine:
            hit = mine.get(str(tz_id).strip("/"))
            if hit is not None:
                return hit
        return original(tz_id)

    return timezone_


def _scoped_parse(original: Callable[[Any], T]) -> Callable[[Any], T]:
    def parse(self: Any) -> T:
        token = _parse_zones.set({})
        try:
            return original(self)
        finally:
            _parse_zones.reset(token)

    return parse


def install() -> None:
    """Put the rules above into icalendar's parser (once per process)."""
    global _installed
    if _installed:
        return
    from icalendar.parser.ical.component import ComponentIcalParser
    from icalendar.timezone import tzp

    tzp.cache_timezone_component = _cache_component  # type: ignore[method-assign]
    tzp.timezone = _scoped_timezone(tzp.timezone)  # type: ignore[method-assign]
    ComponentIcalParser.parse = _scoped_parse(ComponentIcalParser.parse)  # type: ignore[method-assign]
    # Anything a sender defined before the rules were in place goes.
    cache = getattr(tzp, "_TZP__tz_cache", None)
    if isinstance(cache, dict):
        for k in [k for k in cache if tzmod.zone(str(k)) is None]:
            cache.pop(k, None)
    _installed = True


# ---------------- off the event loop ----------------


async def off_loop(fn: Callable[..., T], *args: Any, budget: float = PARSE_BUDGET, **kw: Any) -> T:
    """Run a synchronous parse in a thread, waiting at most `budget` seconds (TimeoutError).
    A thread cannot be stopped; the limits above keep each parse short, and the budget keeps one
    request from waiting on it for long."""
    return await asyncio.wait_for(asyncio.to_thread(fn, *args, **kw), budget)
