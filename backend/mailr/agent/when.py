"""Dates and times for the assistant, in the user's own zone.

Why: a calendar tool that reads a date as UTC midnight answers "What's on my calendar tomorrow?",
asked on a Sunday afternoon in UTC-05:00, with Sunday 19:00 to Monday 19:00 local; and a model
given times in UTC only turns 04:00Z into "12:00 AM Monday" for an event at 23:00 on Sunday.

Here, every date and time the assistant sees or sends is in the user's zone:
- `now_line` is the prompt's "Now": local date, time, weekday, IANA zone, abbreviation, UTC offset,
  and tomorrow's date.
- `local_range` reads a tool's `from`/`to` in the user's zone (a date is a whole day, `to` included)
  or a `when` word ("tomorrow", "tonight", "next week", a weekday), and explains a bad range.
- `local_iso` / `describe` give the times tool results carry: offset ISO times and a line with the
  weekday in the user's clock ("Sun 4 Oct 2026, 23:00–23:30 CDT" or "…, 11:00–11:30 PM CDT").
- `span_text` / `repeat_text` give the words a person reads on approval cards and citations
  ("Tue 6 Oct, 10:00–11:00 AM", "Weekly on Monday"), in the user's zone and clock.

`now()` is the one clock, so tests can fix it.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WHEN_WORDS = ("today", "tomorrow", "yesterday", "tonight", "this week", "next week", "this weekend",
              "next weekend", "a weekday (\"friday\", \"next monday\")", "or a date (YYYY-MM-DD)")
MAX_DAYS = 366


class RangeError(ValueError):
    """A range the tools cannot use; the message says what to send instead."""


def now() -> datetime:
    """The current time (UTC). Tests replace this function."""
    return datetime.now(UTC)


def _name(zone: tzinfo) -> str:
    return getattr(zone, "key", None) or str(zone)


def offset_text(dt: datetime) -> str:
    """"UTC-05:00", "UTC+05:30", "UTC"."""
    off = dt.utcoffset() or timedelta(0)
    if not off:
        return "UTC"
    sign = "-" if off < timedelta(0) else "+"
    mins = abs(int(off.total_seconds())) // 60
    return f"UTC{sign}{mins // 60:02d}:{mins % 60:02d}"


def long_day(d: date) -> str:
    """"Monday 5 October 2026"."""
    return f"{d:%A} {d.day} {d:%B %Y}"


def now_line(zone: tzinfo, fmt: str = "24h") -> str:
    """The prompt's "Now": "Sunday 4 October 2026, 15:49 in America/Chicago (CDT, UTC-05:00). Tomorrow
    is Monday 5 October 2026." The offset is the one in force now; another date may differ (DST).
    `fmt` is the user's clock (calendar/tz.py user_clock): "3:49 PM" in 12h."""
    n = now().astimezone(zone)
    return (f"{long_day(n.date())}, {clock(n, fmt)} in {_name(zone)} ({n.tzname()}, {offset_text(n)}). "
            f"Tomorrow is {long_day(n.date() + timedelta(days=1))}")


def day_start(d: date, zone: tzinfo) -> datetime:
    """Local midnight of `d` (its offset is that day's, so a DST change is respected)."""
    return datetime.combine(d, time(0), zone)


def _point(v: str, zone: tzinfo, *, end: bool) -> datetime:
    """A `from` / `to` value: a date is local midnight (for `to`, the end of that day); a time
    without an offset is local; one with an offset is taken as written."""
    s = (v or "").strip()
    if len(s) == 10:
        d = date.fromisoformat(s)
        return day_start(d + timedelta(days=1) if end else d, zone)
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=zone) if dt.tzinfo is None else dt


def window(word: str, zone: tzinfo) -> tuple[datetime, datetime]:
    """The local range a `when` word names."""
    w = " ".join((word or "").lower().split())
    n = now().astimezone(zone)
    today = n.date()

    def days(a: date, b: date) -> tuple[datetime, datetime]:
        return day_start(a, zone), day_start(b, zone)

    if w == "today":
        return days(today, today + timedelta(days=1))
    if w == "tomorrow":
        return days(today + timedelta(days=1), today + timedelta(days=2))
    if w == "yesterday":
        return days(today - timedelta(days=1), today)
    if w == "tonight":
        return datetime.combine(today, time(17), zone), datetime.combine(today + timedelta(days=1), time(6), zone)
    monday = today - timedelta(days=today.weekday())
    if w == "this week":
        return days(today, monday + timedelta(days=7))
    if w == "next week":
        return days(monday + timedelta(days=7), monday + timedelta(days=14))
    if w in ("this weekend", "weekend", "next weekend"):
        # The coming Saturday and Sunday; on a weekend day, this one (from today).
        sat = monday + timedelta(days=5)
        if w == "next weekend":
            sat += timedelta(days=7)
        start = max(day_start(sat, zone), day_start(today, zone))
        return start, day_start(sat + timedelta(days=2), zone)
    nxt = w.startswith("next ")
    name = w[5:] if nxt else w
    if name in WEEKDAYS:
        ahead = (WEEKDAYS.index(name) - today.weekday()) % 7
        if nxt and ahead == 0:
            ahead = 7
        d = today + timedelta(days=ahead)
        return days(d, d + timedelta(days=1))
    try:
        d = date.fromisoformat(w)
    except ValueError:
        raise RangeError(f"`when` takes {', '.join(WHEN_WORDS)}; got {word!r}") from None
    return days(d, d + timedelta(days=1))


def local_range(frm: Any, to: Any, zone: tzinfo, *, when: Any = None, default_days: int = 8
                ) -> tuple[datetime, datetime]:
    """(start, end) in UTC for a tool call. `when` wins over `from`/`to`. A date is a whole local
    day (`to` included); a time without an offset is local. With neither, today and the next
    `default_days - 1` days."""
    if when:
        a, b = window(str(when), zone)
    else:
        today = now().astimezone(zone).date()
        try:
            a = _point(str(frm), zone, end=False) if frm else day_start(today, zone)
            if to:
                b = _point(str(to), zone, end=True)
            elif frm:
                b = a + timedelta(days=default_days)
            else:
                b = day_start(today + timedelta(days=default_days), zone)
        except ValueError:
            raise RangeError("`from` and `to` take a date (YYYY-MM-DD, the whole day in the user's zone) or a time "
                             "(YYYY-MM-DDTHH:MM, in the user's zone unless it has an offset)") from None
        if b <= a:
            t = today + timedelta(days=1)
            raise RangeError(
                f"`to` ({to}) is not after `from` ({frm}). A date means the whole day in the user's zone and `to` "
                f"is included, so from={t.isoformat()} and to={t.isoformat()} is {long_day(t)}. "
                f"Or pass when=\"tomorrow\" (also today, tonight, this week, next week, a weekday).")
    if b - a > timedelta(days=MAX_DAYS):
        raise RangeError("the range is longer than a year; ask for a shorter one")
    return a.astimezone(UTC), b.astimezone(UTC)


def local_iso(v: Any, zone: tzinfo) -> Any:
    """An instant as an ISO time with the user's offset ("2026-10-04T23:00:00-05:00"); a date or
    anything else unchanged."""
    if not isinstance(v, str) or len(v) <= 10 or "T" not in v:
        return v
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return v
    if dt.tzinfo is None:
        return v
    return dt.astimezone(zone).replace(microsecond=0).isoformat()


_INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})")


def localize_times(data: Any, zone: tzinfo) -> Any:
    """A tool result with every value that is exactly an instant (an ISO time with an offset, as
    mail dates are stored in UTC) given in the user's zone: the model quoted "20:09" for a 15:09
    Chicago mail when it read UTC. Text that merely contains a time is left alone. A new
    structure; the input is unchanged."""
    if isinstance(data, str):
        return local_iso(data, zone) if _INSTANT.fullmatch(data) else data
    if isinstance(data, dict):
        return {k: localize_times(v, zone) for k, v in data.items()}
    if isinstance(data, list | tuple):
        return [localize_times(v, zone) for v in data]
    return data


def _short_day(d: date) -> str:
    return f"{d:%a} {d.day} {d:%b %Y}"


def describe(start: Any, end: Any, all_day: bool, zone: tzinfo, fmt: str = "24h") -> str | None:
    """"Sun 4 Oct 2026, 23:00–23:30 CDT", "Mon 5 Oct 2026, all day", "Sat 10 Oct 2026 – Sun 11 Oct
    2026, all day", "Sun 4 Oct 2026, 23:00 CDT – Mon 5 Oct 2026, 01:00 CDT". `fmt` is the user's
    clock: "Sun 4 Oct 2026, 11:00–11:30 PM CDT" in 12h, the form the model quotes."""
    try:
        if all_day or (isinstance(start, str) and len(start) == 10):
            a = date.fromisoformat(str(start)[:10])
            b = date.fromisoformat(str(end)[:10]) - timedelta(days=1) if end else a
            return f"{_short_day(a)}, all day" if b <= a else f"{_short_day(a)} – {_short_day(b)}, all day"
        a = datetime.fromisoformat(str(start)).astimezone(zone)
        if not end:
            return f"{_short_day(a.date())}, {clock(a, fmt)} {a.tzname()}"
        b = datetime.fromisoformat(str(end)).astimezone(zone)
        if a.date() == b.date() or (b - a <= timedelta(hours=24) and b.time() == time(0) and b.date() == a.date() + timedelta(days=1)):
            return f"{_short_day(a.date())}, {_clock_range(a, b, fmt)} {b.tzname()}"
        return (f"{_short_day(a.date())}, {clock(a, fmt)} {a.tzname()} – {_short_day(b.date())}, "
                f"{clock(b, fmt)} {b.tzname()}")
    except (ValueError, TypeError):
        return None


def range_text(start: datetime, end: datetime, zone: tzinfo, fmt: str = "24h") -> str | None:
    """A searched range in words. Whole local days read as days, with the last day the range
    includes: "next week" from Monday 5 October is "Mon 12 Oct 2026 – Sun 18 Oct 2026, whole days".
    The old line ("… – Mon 19 Oct 2026, 00:00 CDT") was retold as "Oct 12–19", and a reader took
    the 19th as searched."""
    a, b = start.astimezone(zone), end.astimezone(zone)
    if a.time() == time(0) and b.time() == time(0) and b.date() > a.date():
        last = b.date() - timedelta(days=1)
        if last == a.date():
            return f"{_short_day(a.date())}, whole day"
        return f"{_short_day(a.date())} – {_short_day(last)}, whole days"
    return describe(start.isoformat(), end.isoformat(), False, zone, fmt)


def local_day(start: Any, zone: tzinfo) -> str | None:
    """The local date an item starts on (YYYY-MM-DD), for grouping by day."""
    if not isinstance(start, str) or not start:
        return None
    if len(start) == 10:
        return start
    try:
        return datetime.fromisoformat(start).astimezone(zone).date().isoformat()
    except ValueError:
        return start[:10]


# ---------------------------------------------------------------- for people (approval cards, citations)
#
# The words a person reads: the user's zone and clock, the weekday, no year unless it is not this
# year, no ISO strings, no UTC, no RRULE. `fmt` is the user's clock, "24h" or "12h"
# (calendar/tz.py user_clock: Automatic is the clock the device last sent, else 12h here, as the
# daily digest reads it).

def clock(dt: datetime, fmt: str = "24h") -> str:
    """"14:00" or "2:00 PM"."""
    if fmt == "24h":
        return f"{dt:%H:%M}"
    h = dt.hour % 12 or 12
    return f"{h}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'}"


def _clock_range(a: datetime, b: datetime, fmt: str) -> str:
    """"10:00–11:00", "10:00–11:00 AM", "11:30 AM–12:30 PM"."""
    if fmt != "24h" and (a.hour < 12) == (b.hour < 12):
        return f"{clock(a, fmt)[:-3]}–{clock(b, fmt)}"
    return f"{clock(a, fmt)}–{clock(b, fmt)}"


def day_text(d: date) -> str:
    """"Tue 6 Oct", or "Tue 6 Oct 2027" for another year."""
    return f"{d:%a} {d.day} {d:%b}" + ("" if d.year == now().year else f" {d.year}")


def _parse(v: Any) -> datetime | date | None:
    if isinstance(v, datetime | date):
        return v
    if not isinstance(v, str) or not v:
        return None
    try:
        return date.fromisoformat(v) if len(v) == 10 else datetime.fromisoformat(v)
    except ValueError:
        return None


def _zone_of(name: Any) -> tzinfo | None:
    if not name or not isinstance(name, str):
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except (KeyError, ValueError):
        return None


def span_text(start: Any, end: Any = None, all_day: bool = False, zone: tzinfo | None = None, *,
              fmt: str = "24h", event_zone: Any = None) -> str | None:
    """When an event is, for a person, in `zone` (the user's): "Tue 6 Oct, 10:00–11:00",
    "Tue 6 Oct, all day", "Sat 10 Oct – Sun 11 Oct, all day", "Tue 6 Oct, 23:00 – Wed 7 Oct, 01:00".
    When the event's own zone (`event_zone`, an IANA name) keeps another clock at that time, both
    are shown with their abbreviations: "Tue 6 Oct, 10:00–11:00 CDT (08:00–09:00 PDT)"."""
    s, e = _parse(start), _parse(end)
    if s is None:
        return None
    if all_day or not isinstance(s, datetime):
        a = s.date() if isinstance(s, datetime) else s
        b = (e.date() if isinstance(e, datetime) else e) if e is not None else None
        b = b - timedelta(days=1) if b is not None and b > a else a
        return f"{day_text(a)}, all day" if b <= a else f"{day_text(a)} – {day_text(b)}, all day"
    if s.tzinfo is None:
        s = s.replace(tzinfo=zone or UTC)
    if isinstance(e, datetime) and e.tzinfo is None:
        e = e.replace(tzinfo=zone or UTC)
    z = zone or UTC

    def one(tz: tzinfo, abbr: bool) -> str:
        a = s.astimezone(tz)
        tail = f" {a.tzname()}" if abbr else ""
        if not isinstance(e, datetime):
            return f"{day_text(a.date())}, {clock(a, fmt)}{tail}"
        b = e.astimezone(tz)
        if a.date() == b.date() or (b.time() == time(0) and b.date() == a.date() + timedelta(days=1)
                                    and b - a <= timedelta(hours=24)):
            return f"{day_text(a.date())}, {_clock_range(a, b, fmt)}{tail}"
        return f"{day_text(a.date())}, {clock(a, fmt)} – {day_text(b.date())}, {clock(b, fmt)}{tail}"

    ez = _zone_of(event_zone)
    other = ez is not None and s.astimezone(ez).utcoffset() != s.astimezone(z).utcoffset()
    if not other:
        return one(z, False)
    mine, theirs = one(z, True), one(ez, True)
    their_day, _, their_time = theirs.partition(", ")
    return f"{mine} ({their_time if mine.startswith(their_day + ', ') else theirs})"


_DAY_CODES = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_ORD = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", -1: "last"}


def repeat_text(rrule: Any, start: Any = None) -> str | None:
    """An RRULE in words, as the repeat picker says it (frontend/src/lib/rrule.ts summarizeRRule):
    "Weekly on Monday", "Every 2 weeks on Mon and Thu, until 15 Dec", "Monthly on the first Tuesday",
    "Every weekday (Mon–Fri)", "Yearly on 6 October, 5 times". "Does not repeat" for an empty rule,
    "Custom rule (set in another app)" for one it cannot say. `start` (the first date) fills in what
    the rule leaves out."""
    import re

    raw = str(rrule or "").strip()
    raw = re.sub(r"^RRULE:", "", raw, flags=re.IGNORECASE)
    if not raw:
        return "Does not repeat"
    parts: dict[str, str] = {}
    for p in raw.split(";"):
        k, sep, v = p.partition("=")
        if sep:
            parts[k.strip().upper()] = v.strip().upper()
    freq = parts.get("FREQ")
    custom = "Custom rule (set in another app)"
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return custom
    if set(parts) - {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "BYMONTH", "WKST", "BYSETPOS"}:
        return custom
    n = max(1, int(parts["INTERVAL"])) if parts.get("INTERVAL", "").isdigit() else 1
    sd = _parse(start)
    sd = sd.date() if isinstance(sd, datetime) else sd
    days: list[tuple[int | None, int]] = []
    for tok in [t for t in parts.get("BYDAY", "").split(",") if t]:
        m = re.fullmatch(r"([+-]?\d)?(MO|TU|WE|TH|FR|SA|SU)", tok)
        if not m:
            return custom
        days.append((int(m[1]) if m[1] else None, _DAY_CODES[m[2]]))
    if parts.get("BYSETPOS") and len(days) == 1 and days[0][0] is None and re.fullmatch(r"-?\d", parts["BYSETPOS"]):
        days = [(int(parts["BYSETPOS"]), days[0][1])]
    elif parts.get("BYSETPOS"):
        return custom
    long = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    short = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def every(unit: str, one: str) -> str:
        return one if n == 1 else f"Every {n} {unit}s"

    def join(words: list[str]) -> str:
        return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"

    plain = sorted({d for _, d in days})
    if freq == "DAILY":
        if any(o is not None for o, _ in days):
            return custom
        text = ("Every weekday (Mon–Fri)" if n == 1 and plain == [0, 1, 2, 3, 4]
                else f"{every('day', 'Daily')} on {join([short[d] for d in plain])}" if plain else every("day", "Daily"))
    elif freq == "WEEKLY":
        if any(o is not None for o, _ in days):
            return custom
        plain = plain or ([sd.weekday()] if sd else [])
        if n == 1 and plain == [0, 1, 2, 3, 4]:
            text = "Every weekday (Mon–Fri)"
        elif not plain:
            text = every("week", "Weekly")
        else:
            text = f"{every('week', 'Weekly')} on {long[plain[0]] if len(plain) == 1 else join([short[d] for d in plain])}"
    else:
        if len(days) > 1 or (days and days[0][0] is None):
            return custom
        nth = f"the {_ORD.get(days[0][0], str(days[0][0]) + 'th')} {long[days[0][1]]}" if days else None
        bmd = parts.get("BYMONTHDAY")
        if bmd and not re.fullmatch(r"-?\d{1,2}", bmd):
            return custom
        if freq == "MONTHLY":
            on = nth or ("the last day" if bmd == "-1" else f"day {int(bmd)}" if bmd else f"day {sd.day}" if sd else "")
            text = f"{every('month', 'Monthly')} on {on}" if on else every("month", "Monthly")
        else:
            months = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                      "October", "November", "December"]
            month = int(parts["BYMONTH"]) if parts.get("BYMONTH", "").isdigit() else (sd.month if sd else None)
            day = int(bmd) if bmd else (sd.day if sd else None)
            on = (f"{nth} of {months[month - 1]}" if nth and month else
                  f"{day} {months[month - 1]}" if month and day and day > 0 else "")
            text = f"{every('year', 'Yearly')} on {on}" if on else every("year", "Yearly")
    if parts.get("COUNT", "").isdigit():
        c = int(parts["COUNT"])
        text += ", once" if c == 1 else f", {c} times"
    elif parts.get("UNTIL"):
        u = parts["UNTIL"]
        try:
            ud = date(int(u[:4]), int(u[4:6]), int(u[6:8]))
            text += f", until {ud.day} {ud:%b}" + ("" if sd is None or sd.year == ud.year else f" {ud.year}")
        except ValueError:
            pass
    return text
