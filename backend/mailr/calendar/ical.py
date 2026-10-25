"""iCalendar parsing and building for the calendar mirror and writer.

`component_fields` reads one VEVENT/VTODO into the mirror's columns; `parse_resource` reads a
whole CalDAV resource (master plus overrides). The builders (`build_event`, `build_todo`,
`apply_changes`, `apply_task_changes`) write timed values with a TZID in the zone they are given
(section 3). `canonical_occurrence` is the one occurrence string (section 1).
"""

from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from icalendar import Alarm, Calendar, Event, Todo, vCalAddress, vRecur

from mailr.calendar import tz as tzmod

DESCRIPTION_MAX = 64 * 1024
PRODID = "-//mailr//EN"

# ---------------- canonical occurrence strings ----------------


def canonical_occurrence(v: Any) -> str:
    """The canonical occurrence string: a UTC isoformat() for timed values, YYYY-MM-DD for dates.
    Naive datetimes are taken as UTC (callers localise floating values first)."""
    v = getattr(v, "dt", v)
    if v is None or v == "":
        return ""
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=UTC)
        return v.astimezone(UTC).replace(microsecond=0).isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return normalize_occurrence(str(v))


_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(Z)?)?$")


def normalize_occurrence(s: str | None, tz: tzinfo | None = None) -> str:
    """Any occurrence form clients have sent (20261002T150000Z, offsets, Z, dates) as the
    canonical string. A naive time is read in `tz` (default UTC). Unparseable input is
    returned unchanged."""
    s = (s or "").strip()
    if not s:
        return ""
    m = _COMPACT.match(s)
    try:
        if m:
            if m[4] is None:
                return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
            d = datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), int(m[6]))
            return canonical_occurrence(d.replace(tzinfo=UTC if m[7] or tz is None else tz))
        if len(s) == 10 and s[4] == "-":
            return date.fromisoformat(s).isoformat()
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=tz or UTC)
        return canonical_occurrence(d)
    except ValueError:
        return s


def normalize_key(key: str, tz: tzinfo | None = None) -> str:
    """A reminder key ("e|uid|occurrence|minutes") with its occurrence in canonical form."""
    parts = (key or "").split("|")
    if len(parts) != 4:
        return key
    return "|".join([parts[0], parts[1], normalize_occurrence(parts[2], tz), parts[3]])


def parse_when(value: Any, tz: tzinfo | None = None) -> datetime | date | None:
    """An ISO date or datetime. A naive time is read in `tz` (default UTC); a bare offset is
    turned into UTC (icalendar would write it as TZID="UTC-05:00" with no VTIMEZONE)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        d = value
    elif isinstance(value, date):
        return value
    else:
        v = str(value).strip()
        if len(v) == 10 and v[4] == "-":
            return date.fromisoformat(v)
        m = _COMPACT.match(v)
        if m and m[4] is None:
            return date(int(m[1]), int(m[2]), int(m[3]))
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if d.tzinfo is None:
        return d.replace(tzinfo=tz or UTC)
    return utc_if_fixed(d)


def utc_if_fixed(d: datetime) -> datetime:
    """A time with a bare UTC offset (-05:00) is stored in UTC."""
    return d.astimezone(UTC) if isinstance(d.tzinfo, timezone) and d.utcoffset() is not None else d


# ---------------- reading values ----------------

_OFFSET_TZID = re.compile(r"^UTC([+-])(\d{2}):?(\d{2})$")


def value(prop: Any, cal: Any = None, fallback: tzinfo | None = None) -> tuple[datetime | date | None, str | None, bool]:
    """(aware datetime or date, zone name, floating) for a DTSTART-like property."""
    if prop is None:
        return None, None, False
    tzid = str(getattr(prop, "params", {}).get("TZID") or "")
    v = getattr(prop, "dt", prop)
    if isinstance(v, datetime):
        floating = False
        if v.tzinfo is None:
            if m := _OFFSET_TZID.match(tzid):
                # Written by older mailr versions: TZID="UTC-05:00" with no VTIMEZONE is that offset.
                off = timedelta(hours=int(m[2]), minutes=int(m[3]))
                v = v.replace(tzinfo=timezone(off if m[1] == "+" else -off))
            elif tzid:
                v = tzmod.localize(v, tzid, cal, fallback)
            else:
                floating = True
                v = v.replace(tzinfo=fallback or UTC)
        # Only an IANA name or None leaves here: a VTIMEZONE-built zone named "Central Standard
        # Time" (Outlook) is the IANA zone it maps to; an unknown name keeps the instant but no name.
        name = tzmod.iana(tzid) if tzid and not _OFFSET_TZID.match(tzid) else tzmod.zone_name(v.tzinfo)
        if name and not isinstance(v.tzinfo, (ZoneInfo, timezone)):
            v = v.astimezone(ZoneInfo(name))
        return v, name, floating
    if isinstance(v, date):
        return v, None, False
    return None, None, False


def as_utc(v: datetime | date | None, fallback: tzinfo | None = None) -> datetime | None:
    """The instant of a value; a date is midnight in `fallback` (the user's zone)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=fallback or UTC)).astimezone(UTC)
    return datetime(v.year, v.month, v.day, tzinfo=fallback or UTC).astimezone(UTC)


def out_when(v: datetime | date | None) -> str | None:
    """API form of a value: UTC isoformat for timed values, YYYY-MM-DD for dates."""
    if v is None:
        return None
    return canonical_occurrence(v)


def addr(a: Any) -> str:
    """A calendar address (ORGANIZER, ATTENDEE) without its mailto: and without bidi, invisible or
    control characters or white space: a sender's "‮" never makes it read as another address."""  # noqa: PLE2502
    from mailr.core.textsafe import clean_address

    s = str(a).strip()
    if s[:7].lower() == "mailto:":
        s = s[7:]
    return clean_address(s)


def cn(a: Any) -> str:
    """The CN of an ORGANIZER or ATTENDEE, display-safe (core/textsafe.clean)."""
    from mailr.core.textsafe import clean

    return clean(str(getattr(a, "params", {}).get("CN", "") or "")).strip()


def clean_sender_text(comp: Any) -> Any:
    """An organizer's DESCRIPTION and LOCATION lose Markdown and HTML images (they become their alt
    text), as agent-written text does (agent/agentwrite.py): a sender's text never makes a
    renderer load an address it chose. In place; returns `comp`."""
    from mailr.agent.agentwrite import strip_markdown_images

    for k in ("description", "location"):
        v = comp.get(k)
        if v is None or isinstance(v, list):
            continue
        text = str(v)
        clean = strip_markdown_images(text)
        if clean != text:
            comp.pop(k, None)
            comp.add(k, clean)
    return comp


def as_list(v: Any) -> list[Any]:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def text_of(comp: Any, key: str) -> str:
    v = comp.get(key)
    if v is None:
        return ""
    if isinstance(v, list):
        v = v[0]
    return str(v)


def alarm_minutes(comp: Any, anchor: Any = None, end: Any = None) -> list[int]:
    """Minutes before the anchor (DTSTART for events, DUE for tasks) of each VALARM.
    Relative triggers are read as they are (RELATED=END is folded into the event's duration);
    absolute triggers are measured against the anchor. Negative values are after the anchor,
    down to a day (all-day items use -540 for "9 am that day", as Thunderbird does)."""
    out: set[int] = set()
    anchor = getattr(anchor, "dt", anchor)
    end = getattr(end, "dt", end)
    for al in comp.subcomponents:
        if getattr(al, "name", "") != "VALARM" or al.get("trigger") is None:
            continue
        trig = al.get("trigger")
        v = getattr(trig, "dt", trig)
        mins: int | None = None
        if isinstance(v, timedelta):
            mins = int(-v.total_seconds() // 60)
            related = str(getattr(trig, "params", {}).get("RELATED", "START")).upper()
            if (related == "END" and comp.name == "VEVENT" and isinstance(anchor, (date, datetime))
                    and isinstance(end, (date, datetime)) and type(anchor) is type(end)):
                mins -= int((end - anchor).total_seconds() // 60)
        elif isinstance(v, datetime) and isinstance(anchor, datetime):
            a = anchor if anchor.tzinfo else anchor.replace(tzinfo=UTC)
            t = v if v.tzinfo else v.replace(tzinfo=UTC)
            mins = int((a - t).total_seconds() // 60)
        if mins is not None and -1440 <= mins <= 60 * 24 * 28:
            out.add(mins)
    return sorted(out)


def set_alarms(comp: Any, minutes: list[int] | None, summary: str, *, related_end: bool = False) -> None:
    """Replace the component's VALARMs with one DISPLAY alarm per offset (minutes before;
    negative = after the anchor, at most a day)."""
    comp.subcomponents = [c for c in comp.subcomponents if getattr(c, "name", "") != "VALARM"]
    for m in sorted({int(x) for x in (minutes or []) if -1440 <= int(x) <= 60 * 24 * 28}):
        al = Alarm()
        al.add("action", "DISPLAY")
        al.add("description", summary or "Reminder")
        al.add("trigger", timedelta(minutes=-m), parameters={"RELATED": "END"} if related_end else None)
        comp.add_component(al)


# "No reminders" on an event. An event the user (or the assistant) gave
# no reminders on purpose carries X-MAILR-NO-REMINDER:TRUE, so mailr's planner and the phone's
# leave out the fallback default (default_timed_event / default_all_day_event) that an event
# without VALARMs otherwise gets. Why an X-property and not RFC 9074's VALARM with ACTION:NONE:
# that is still a VALARM, and clients that do not know ACTION:NONE read it by their own rules —
# KCalendarCore keeps the type of an alarm whose ACTION it does not recognise as Display, and
# Thunderbird has shown Apple's ACTION:NONE "default alarm" VALARMs as real reminders — so the
# event could start alerting in other apps, the opposite of the point. Every VALARM reader in
# mailr (`alarm_minutes`, the mirror, the phone) would also need to skip it. RFC 5545 has clients
# keep and ignore X-properties they do not know, and Radicale stores them as written, so the
# marker round-trips through CalDAV and other apps' edits and changes nothing for them. It lives
# on the VEVENT (master and overrides alike); `set_event_reminders` writes and removes it.
NO_REMINDER = "X-MAILR-NO-REMINDER"


def no_reminder(comp: Any) -> bool:
    """The component carries the "no reminders" marker (and no VALARM overrides it)."""
    return text_of(comp, NO_REMINDER).strip().upper() == "TRUE"


def strip_sender_extras(comp: Any) -> None:
    """A copy of an event that came from someone else (an invitation, an organizer's update, a
    calendar link), before it is stored: the sender's VALARMs go, and so does every X-MAILR-*
    property and parameter, which only mailr writes for its user.
    The user's own default reminders then apply (calendar/planner.py), and an update keeps the
    stored copy's alarms and X-MAILR-* properties (calendar/inbound.py)."""
    comp.subcomponents = [s for s in comp.subcomponents if getattr(s, "name", "") != "VALARM"]
    for key in [k for k in comp if str(k).upper().startswith("X-MAILR-")]:
        comp.pop(key, None)
    for _name, val in comp.property_items(recursive=False, sorted=False):
        params = getattr(val, "params", None)
        if params:
            for key in [k for k in params if str(k).upper().startswith("X-MAILR-")]:
                del params[key]


def set_event_reminders(comp: Any, minutes: list[int], summary: str) -> None:
    """An event's reminders as chosen by the user: one VALARM per offset; [] writes none and
    marks the event "no reminders" (NO_REMINDER), any reminder drops the marker."""
    set_alarms(comp, minutes, summary)
    comp.pop(NO_REMINDER, None)
    if not any(getattr(c, "name", "") == "VALARM" for c in comp.subcomponents):
        comp.add(NO_REMINDER, "TRUE")


def rrule_str(comp: Any) -> str | None:
    r = comp.get("rrule")
    if r is None:
        return None
    if isinstance(r, list):
        r = r[0]
    try:
        return r.to_ical().decode()
    except Exception:  # noqa: BLE001
        return None


def attendee_list(comp: Any) -> list[dict[str, Any]]:
    out = []
    for a in as_list(comp.get("attendee"))[:100]:
        params = getattr(a, "params", {})
        out.append({"email": addr(a), "name": cn(a),
                    "partstat": str(params.get("PARTSTAT", "NEEDS-ACTION")).upper(),
                    "role": str(params.get("ROLE", "REQ-PARTICIPANT")).upper(),
                    "rsvp": str(params.get("RSVP", "")).upper() == "TRUE"})
    return out


def parent_uid(comp: Any) -> str | None:
    for r in as_list(comp.get("related-to")):
        if str(getattr(r, "params", {}).get("RELTYPE", "PARENT")).upper() == "PARENT":
            return str(r)
    return None


def categories(comp: Any) -> list[str]:
    out: list[str] = []
    for c in as_list(comp.get("categories")):
        cats = getattr(c, "cats", None)
        if cats is None:
            cats = [x for x in str(c).split(",")]
        for x in cats:
            x = str(x).strip()
            if x and x not in out:
                out.append(x[:100])
    return out[:50]


def _int(v: Any, default: int | None = None) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


# ---------------- one component → mirror fields ----------------


def component_fields(comp: Any, cal: Any, fallback: tzinfo) -> dict[str, Any]:
    """The mirror's columns for one VEVENT or VTODO (times as aware UTC datetimes or dates)."""
    kind = "task" if comp.name == "VTODO" else "event"
    start, start_tz, floating = value(comp.get("dtstart"), cal, fallback)
    all_day = isinstance(start, date) and not isinstance(start, datetime)
    f: dict[str, Any] = {
        "kind": kind, "uid": text_of(comp, "uid")[:300], "summary": text_of(comp, "summary"),
        "description": text_of(comp, "description")[:DESCRIPTION_MAX], "location": text_of(comp, "location"),
        "status": text_of(comp, "status").upper()[:20], "class_": text_of(comp, "class").upper()[:12],
        "transp": text_of(comp, "transp").upper()[:12], "color": text_of(comp, "color")[:40] or None,
        "url": text_of(comp, "url")[:2000] or None, "categories": categories(comp),
        "tzid": None if all_day or floating else start_tz, "floating": floating,
        "all_day": all_day, "start_at": None, "end_at": None, "start_date": None, "end_date": None,
        "end_tzid": None, "rrule": rrule_str(comp), "sequence": _int(comp.get("sequence"), 0) or 0,
        "organizer": None, "organizer_name": "", "attendees": attendee_list(comp),
        "recurrence_id": None,
    }
    if isinstance(start, datetime):
        f["start_at"] = start.astimezone(UTC)
    elif isinstance(start, date):
        f["start_date"] = start
        f["start_at"] = as_utc(start, fallback)
    rid = comp.get("recurrence-id")
    if rid is not None:
        rv, _, _ = value(rid, cal, fallback)
        f["recurrence_id"] = canonical_occurrence(rv)
    org = comp.get("organizer")
    if org is not None:
        f["organizer"] = addr(org)[:320] or None
        f["organizer_name"] = cn(org)
    f["attendee_count"] = len(f["attendees"])
    for key in ("dtstamp", "last-modified", "created"):
        v, _, _ = value(comp.get(key), cal, UTC)
        f[key.replace("-", "_")] = v.astimezone(UTC) if isinstance(v, datetime) else None
    if kind == "event":
        end, end_tz, _ = value(comp.get("dtend"), cal, fallback)
        if end is None and comp.get("duration") is not None and start is not None:
            dur = getattr(comp.get("duration"), "dt", None)
            if isinstance(dur, timedelta):
                end = start + dur
        if end is None and start is not None:
            end = start + timedelta(days=1) if all_day else start
        if isinstance(end, datetime) and not isinstance(start, datetime) and start is not None:
            end = end.date()  # a DATE start with a DATE-TIME end: keep it all-day
        if all_day and isinstance(end, date) and not isinstance(end, datetime):
            f["end_date"] = end
            f["end_at"] = as_utc(end, fallback)
        elif isinstance(end, datetime):
            f["end_at"] = end.astimezone(UTC)
            if end_tz and end_tz != start_tz:
                f["end_tzid"] = end_tz
        f["alarms"] = alarm_minutes(comp, start, end)
        f["no_reminder"] = not f["alarms"] and no_reminder(comp)
        f["due_at"] = f["due_date"] = f["completed_at"] = None
        f["percent"] = f["priority"] = f["sort_order"] = f["parent_uid"] = None
        f["repeat_from"] = None
    else:
        due, due_tz, due_floating = value(comp.get("due"), cal, fallback)
        f["due_at"] = due.astimezone(UTC) if isinstance(due, datetime) else as_utc(due, fallback)
        f["due_date"] = due if isinstance(due, date) and not isinstance(due, datetime) else None
        if f["tzid"] is None and isinstance(due, datetime) and not due_floating:
            f["tzid"] = due_tz
        f["due_all_day"] = f["due_date"] is not None
        f["start_all_day"] = all_day
        comp_v, _, _ = value(comp.get("completed"), cal, UTC)
        f["completed_at"] = comp_v.astimezone(UTC) if isinstance(comp_v, datetime) else as_utc(comp_v, UTC)
        f["percent"] = _int(comp.get("percent-complete"))
        f["priority"] = _int(comp.get("priority"), 0) or 0
        f["sort_order"] = _int(comp.get("x-apple-sort-order"))
        f["parent_uid"] = (parent_uid(comp) or "")[:300] or None
        f["repeat_from"] = "completion" if text_of(comp, "x-mailr-repeat-from").upper() == "COMPLETION" else "due"
        f["alarms"] = alarm_minutes(comp, due)
        f["no_reminder"] = False
        if not f["status"]:
            f["status"] = "COMPLETED" if f["completed_at"] else "NEEDS-ACTION"
    return f


@dataclass
class Parsed:
    kind: str  # event | task | journal | other
    cal: Any
    master: Any
    overrides: list[Any] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)


def parse_resource(ics: str | bytes, fallback: tzinfo) -> Parsed:
    """One CalDAV resource: its master component (the one without RECURRENCE-ID), the
    overrides, and the mirror fields of the master."""
    cal = Calendar.from_ical(ics)
    for name, kind in (("VEVENT", "event"), ("VTODO", "task"), ("VJOURNAL", "journal")):
        comps = list(cal.walk(name))
        if not comps:
            continue
        master = next((c for c in comps if c.get("recurrence-id") is None), comps[0])
        overrides = [c for c in comps if c is not master and c.get("recurrence-id") is not None]
        if kind == "journal":
            return Parsed("journal", cal, master, overrides, {"uid": text_of(master, "uid")[:300], "kind": "journal"})
        f = component_fields(master, cal, fallback)
        f["recurring"] = f["rrule"] is not None or master.get("rdate") is not None
        f["has_overrides"] = bool(overrides)
        return Parsed(kind, cal, master, overrides, f)
    return Parsed("other", cal, None)


def master_and_overrides(cal: Any, name: str = "VEVENT") -> tuple[Any, list[Any]]:
    comps = list(cal.walk(name))
    master = next((c for c in comps if c.get("recurrence-id") is None), comps[0])
    return master, [c for c in comps if c is not master and c.get("recurrence-id") is not None]


# ---------------- recurrence rules on write ----------------


def check_rrule(rule: str) -> vRecur:
    """A rule mailr may write: valid, and never SECONDLY/MINUTELY or an endless HOURLY rule
    (expansion is capped on read, but mailr does not create such rules)."""
    rule = str(rule).removeprefix("RRULE:")
    r = vRecur.from_ical(rule)
    freq = str((r.get("FREQ") or [""])[0]).upper()
    if not freq:
        raise ValueError("the rule has no FREQ")
    if freq in ("SECONDLY", "MINUTELY") or (freq == "HOURLY" and not (r.get("COUNT") or r.get("UNTIL"))):
        raise ValueError(f"{freq.lower()} repeats are not supported")
    return r


def until(occurrence: datetime | date) -> datetime | date:
    if isinstance(occurrence, datetime):
        o = occurrence if occurrence.tzinfo else occurrence.replace(tzinfo=UTC)
        return (o - timedelta(seconds=1)).astimezone(UTC)
    return occurrence - timedelta(days=1)


def truncate_series(master: Any, occurrence: datetime | date) -> None:
    rule = master.get("rrule")
    if rule is None:
        return
    rule = dict(rule)
    rule.pop("COUNT", None)
    start = getattr(master.get("dtstart"), "dt", None)
    u = until(occurrence)
    if isinstance(start, date) and not isinstance(start, datetime) and isinstance(u, datetime):
        u = u.date()
    elif isinstance(start, datetime) and not isinstance(u, datetime):
        u = datetime(u.year, u.month, u.day, 23, 59, 59, tzinfo=UTC)
    rule["UNTIL"] = [u]
    master.pop("rrule", None)
    master.add("rrule", rule)


def same_instant(a: Any, b: datetime | date) -> bool:
    a = getattr(a, "dt", a)
    if isinstance(a, datetime) and isinstance(b, datetime):
        a = a if a.tzinfo else a.replace(tzinfo=UTC)
        b = b if b.tzinfo else b.replace(tzinfo=UTC)
        return a == b
    return (a.date() if isinstance(a, datetime) else a) == (b.date() if isinstance(b, datetime) else b)


def in_zone_of(prop: Any, v: datetime | date, fallback: tzinfo | None = None) -> datetime | date:
    """`v` written the way `prop` (a master's DTSTART) is: same zone, DATE when it is a date.
    Used for RECURRENCE-ID and EXDATE, which must match the master's form."""
    ref = getattr(prop, "dt", prop)
    if isinstance(ref, datetime):
        if not isinstance(v, datetime):
            v = datetime(v.year, v.month, v.day, ref.hour, ref.minute, ref.second, tzinfo=ref.tzinfo or fallback or UTC)
        if ref.tzinfo is None:  # floating master: floating values in the user's zone
            return v.astimezone(fallback or UTC).replace(tzinfo=None)
        return v.astimezone(ref.tzinfo)
    if isinstance(ref, date) and isinstance(v, datetime):
        return v.astimezone(fallback or UTC).date() if v.tzinfo else v.date()
    return v


def zone_of(prop: Any) -> tzinfo | None:
    """The zone a DTSTART carries (None for dates, floating values and UTC)."""
    v = getattr(prop, "dt", None)
    if isinstance(v, datetime) and v.tzinfo is not None and tzmod.zone_name(v.tzinfo) != "UTC":
        # A VTIMEZONE-built zone with a Windows name is written as its IANA zone.
        name = tzmod.zone_name(v.tzinfo)
        return ZoneInfo(name) if name and not isinstance(v.tzinfo, ZoneInfo) else v.tzinfo
    return None


# ---------------- building and changing events ----------------


def new_calendar() -> Calendar:
    cal = Calendar()
    cal.add("prodid", PRODID)
    cal.add("version", "2.0")
    return cal


def _replace(comp: Any, key: str, v: Any, **kw: Any) -> None:
    comp.pop(key, None)
    if v not in (None, "", []):
        comp.add(key, v, **kw)


def _set_attendees(comp: Any, attendees: list[Any]) -> None:
    comp.pop("attendee", None)
    for a in attendees or []:
        if isinstance(a, dict):
            email, name, role = a.get("email"), a.get("name"), a.get("role")
            partstat, rsvp = a.get("partstat"), a.get("rsvp", True)
        else:
            email, name, role, partstat, rsvp = str(a), "", None, None, True
        if not email:
            continue
        v = vCalAddress(f"mailto:{email}")
        v.params["PARTSTAT"] = str(partstat or "NEEDS-ACTION").upper()
        if name:
            v.params["CN"] = name
        if role:
            v.params["ROLE"] = str(role).upper()
        if rsvp:
            v.params["RSVP"] = "TRUE"
        comp.add("attendee", v, encode=0)


def _merge_attendees(comp: Any, attendees: list[Any]) -> None:
    """Keep the stored PARTSTAT (and other parameters) of guests that stay on the list."""
    old = {addr(a).lower(): a for a in as_list(comp.get("attendee"))}
    comp.pop("attendee", None)
    for a in attendees or []:
        email = (a.get("email") if isinstance(a, dict) else str(a)) or ""
        prev = old.get(email.lower())
        if prev is not None:
            if isinstance(a, dict) and a.get("name"):
                prev.params["CN"] = a["name"]
            if isinstance(a, dict) and a.get("role"):
                prev.params["ROLE"] = str(a["role"]).upper()
            comp.add("attendee", prev, encode=0)
        else:
            tmp = Event()
            _set_attendees(tmp, [a])
            for x in as_list(tmp.get("attendee")):
                comp.add("attendee", x, encode=0)


def _common_event_fields(comp: Any, payload: dict[str, Any]) -> None:
    if "transparent" in payload and payload["transparent"] is not None:
        _replace(comp, "transp", "TRANSPARENT" if payload["transparent"] else "OPAQUE")
    if "class" in payload and payload["class"] is not None:
        _replace(comp, "class", str(payload["class"]).upper())
    if "color" in payload and payload["color"] is not None:
        # "" means no colour of its own (the calendar's colour shows).
        if payload["color"]:
            _replace(comp, "color", str(payload["color"]))
        else:
            comp.pop("color", None)
    if "url" in payload and payload["url"] is not None:
        _replace(comp, "url", str(payload["url"]))
    if "status" in payload and payload["status"] is not None:
        _replace(comp, "status", str(payload["status"]).upper())
    if "categories" in payload and payload["categories"] is not None:
        comp.pop("categories", None)
        cats = [str(c) for c in payload["categories"] if str(c).strip()]
        if cats:
            comp.add("categories", cats)


def _event_times(payload: dict[str, Any], zone: tzinfo | None, user_tz: tzinfo
                 ) -> tuple[datetime | date, datetime | date | None]:
    start = parse_when(payload.get("start"), user_tz)
    if start is None:
        raise ValueError("an event needs a start")
    end = parse_when(payload.get("end"), user_tz)
    if payload.get("all_day"):
        if isinstance(start, datetime):
            start = start.astimezone(zone or user_tz).date()
        if isinstance(end, datetime):
            end = end.astimezone(zone or user_tz).date()
            if end <= start:
                end = start + timedelta(days=1)
    if end is None:
        end = start + (timedelta(days=1) if not isinstance(start, datetime) else timedelta(hours=1))
    return tzmod.to_zone(start, zone), tzmod.to_zone(end, zone)


def write_zone(payload: dict[str, Any], existing: Any, user_tz: tzinfo) -> tzinfo:
    """The zone timed values are written in: the payload's `tz`, else the event's own TZID,
    else the user's zone."""
    z = tzmod.zone(payload.get("tz")) if payload.get("tz") else None
    if payload.get("tz") and z is None:
        raise ValueError(f"unknown time zone {payload.get('tz')!r}")
    return z or zone_of(existing) or user_tz


def build_event(payload: dict[str, Any], user_tz: tzinfo, uid: str | None = None) -> Calendar:
    zone = write_zone(payload, None, user_tz)
    start, end = _event_times(payload, zone, user_tz)
    cal = new_calendar()
    ev = Event()
    ev.add("uid", uid or payload.get("uid") or f"{uuid.uuid4()}@mailr")
    ev.add("dtstamp", datetime.now(UTC))
    ev.add("created", datetime.now(UTC))
    ev.add("dtstart", start)
    ev.add("dtend", end)
    ev.add("summary", str(payload.get("summary") or "(no title)"))
    ev.add("sequence", 0)
    if payload.get("location"):
        ev.add("location", payload["location"])
    if payload.get("description"):
        ev.add("description", payload["description"])
    _common_event_fields(ev, payload)
    if payload.get("reminders") is not None:
        # [] is the user's "no reminders" (the marker); left out, the event has none of its own.
        set_event_reminders(ev, payload["reminders"], str(payload.get("summary") or ""))
    if payload.get("recurrence"):
        ev.add("rrule", check_rrule(payload["recurrence"]))
    if payload.get("attendees"):
        _set_attendees(ev, payload["attendees"])
    cal.add_component(ev)
    return cal


def apply_changes(comp: Any, changes: dict[str, Any], user_tz: tzinfo, *, master: Any = None,
                  bump: bool = True) -> None:
    """Apply an event PATCH to one VEVENT (a master or an override)."""
    for key in ("summary", "location", "description"):
        if key in changes:
            _replace(comp, key, changes[key] or None)
    _common_event_fields(comp, changes)
    if any(k in changes for k in ("start", "end", "all_day", "tz")):
        zone = write_zone(changes, comp.get("dtstart") if master is None else (
            comp.get("dtstart") if zone_of(comp.get("dtstart")) else master.get("dtstart")), user_tz)
        old_start = getattr(comp.get("dtstart"), "dt", None)
        old_end = getattr(comp.get("dtend"), "dt", None)
        new_start = parse_when(changes.get("start"), user_tz) if changes.get("start") else old_start
        if changes.get("end"):
            new_end = parse_when(changes["end"], user_tz)
        elif old_start is not None and old_end is not None and new_start is not None \
                and type(old_start) is type(new_start) and type(old_end) is type(old_start):
            new_end = new_start + (old_end - old_start)
        elif new_start is not None:
            new_end = new_start + (timedelta(days=1) if not isinstance(new_start, datetime) else timedelta(hours=1))
        else:
            new_end = None
        if changes.get("all_day"):
            if isinstance(new_start, datetime):
                new_start = (new_start if new_start.tzinfo else new_start.replace(tzinfo=zone)).astimezone(zone).date()
            if isinstance(new_end, datetime):
                new_end = (new_end if new_end.tzinfo else new_end.replace(tzinfo=zone)).astimezone(zone).date()
            if new_end is None or new_end <= new_start:
                new_end = new_start + timedelta(days=1)
        if new_start is not None:
            if isinstance(new_start, datetime) and new_start.tzinfo is None:
                new_start = new_start.replace(tzinfo=user_tz)
            comp.pop("dtstart", None)
            comp.add("dtstart", tzmod.to_zone(new_start, zone))
        if new_end is not None:
            if isinstance(new_end, datetime) and new_end.tzinfo is None:
                new_end = new_end.replace(tzinfo=user_tz)
            comp.pop("dtend", None)
            comp.pop("duration", None)
            comp.add("dtend", tzmod.to_zone(new_end, zone))
    if "reminders" in changes and changes["reminders"] is not None:
        set_event_reminders(comp, changes["reminders"], str(comp.get("summary", "") or ""))
    if "recurrence" in changes and comp.get("recurrence-id") is None:
        comp.pop("rrule", None)
        if changes["recurrence"]:
            comp.add("rrule", check_rrule(changes["recurrence"]))
    if "attendees" in changes and changes["attendees"] is not None:
        _merge_attendees(comp, changes["attendees"])
    _replace(comp, "dtstamp", datetime.now(UTC))
    _replace(comp, "last-modified", datetime.now(UTC))
    if bump:
        seq = int(comp.get("sequence", 0) or 0) + 1
        comp.pop("sequence", None)
        comp.add("sequence", seq)


def make_override(master: Any, occurrence: datetime | date, user_tz: tzinfo) -> Any:
    """A new override VEVENT for one occurrence of the series (a copy of the master)."""
    ov = copy.deepcopy(master)
    for k in ("rrule", "rdate", "exdate", "recurrence-id"):
        ov.pop(k, None)
    rid = in_zone_of(master.get("dtstart"), occurrence, user_tz)
    dur = None
    if master.get("dtend") is not None:
        dur = master["dtend"].dt - master["dtstart"].dt
    elif master.get("duration") is not None:
        dur = master["duration"].dt
    ov.pop("dtstart", None)
    ov.add("dtstart", rid)
    ov.pop("dtend", None)
    ov.pop("duration", None)
    if dur is not None:
        ov.add("dtend", rid + dur)
    ov.add("recurrence-id", rid)
    return ov


def set_attendee(comp: Any, email: str, partstat: str) -> None:
    atts = as_list(comp.get("attendee"))
    found = False
    for a in atts:
        if addr(a).lower() == email.lower():
            a.params["PARTSTAT"] = partstat
            a.params.pop("RSVP", None)
            found = True
    if not found:
        a = vCalAddress(f"mailto:{email}")
        a.params["PARTSTAT"] = partstat
        atts.append(a)
    comp.pop("attendee", None)
    for a in atts:
        comp.add("attendee", a, encode=0)


# ---------------- tasks ----------------


def _set_related_parent(comp: Any, parent: str | None) -> None:
    keep = [r for r in as_list(comp.get("related-to"))
            if str(getattr(r, "params", {}).get("RELTYPE", "PARENT")).upper() != "PARENT"]
    comp.pop("related-to", None)
    for r in keep:
        comp.add("related-to", r)
    if parent:
        comp.add("related-to", str(parent), parameters={"RELTYPE": "PARENT"})


def _task_when(v: Any, zone: tzinfo | None, user_tz: tzinfo) -> datetime | date | None:
    w = parse_when(v, user_tz)
    return tzmod.to_zone(w, zone) if w is not None else None


def build_todo(payload: dict[str, Any], user_tz: tzinfo, uid: str | None = None) -> Calendar:
    zone = write_zone(payload, None, user_tz)
    cal = new_calendar()
    t = Todo()
    t.add("uid", uid or payload.get("uid") or f"{uuid.uuid4()}@mailr")
    t.add("dtstamp", datetime.now(UTC))
    t.add("created", datetime.now(UTC))
    t.add("summary", str(payload.get("summary") or "(no title)"))
    t.add("status", "NEEDS-ACTION")
    due = _task_when(payload.get("due"), zone, user_tz)
    if due is not None:
        t.add("due", due)
    start = _task_when(payload.get("start"), zone, user_tz)
    if start is not None:
        t.add("dtstart", start)
    if payload.get("notes"):
        t.add("description", payload["notes"])
    if payload.get("priority"):
        t.add("priority", int(payload["priority"]))
    if payload.get("parent"):
        _set_related_parent(t, payload["parent"])
    if payload.get("categories"):
        t.add("categories", [str(c) for c in payload["categories"] if str(c).strip()])
    if payload.get("sort_order") is not None:
        t.add("x-apple-sort-order", str(int(payload["sort_order"])))
    if payload.get("recurrence"):
        t.add("rrule", check_rrule(payload["recurrence"]))
    if payload.get("repeat_from") == "completion":
        t.add("x-mailr-repeat-from", "COMPLETION")
    if payload.get("reminders") and due is not None:
        set_alarms(t, payload["reminders"], str(payload.get("summary") or ""), related_end=True)
    cal.add_component(t)
    return cal


def complete(comp: Any, done: bool, when: datetime | None = None) -> None:
    """Mark a VTODO completed (STATUS, COMPLETED, PERCENT-COMPLETE) or open again."""
    comp.pop("status", None)
    comp.pop("completed", None)
    if done:
        comp.add("status", "COMPLETED")
        comp.add("completed", (when or datetime.now(UTC)).astimezone(UTC))
        if comp.get("percent-complete") is not None:
            _replace(comp, "percent-complete", 100)
    else:
        comp.add("status", "NEEDS-ACTION")
        if _int(comp.get("percent-complete")) == 100:
            comp.pop("percent-complete", None)


def apply_task_changes(comp: Any, changes: dict[str, Any], user_tz: tzinfo) -> None:
    """Apply a task PATCH to one VTODO."""
    status = changes.get("status")
    if status == "completed" or changes.get("completed") is True:
        complete(comp, True)
    elif status == "open" or changes.get("completed") is False:
        complete(comp, False)
    zone = write_zone(changes, comp.get("due") or comp.get("dtstart"), user_tz)
    if changes.get("summary"):
        _replace(comp, "summary", changes["summary"])
    if "notes" in changes:
        _replace(comp, "description", changes["notes"] or None)
    if "due" in changes:
        _replace(comp, "due", _task_when(changes["due"], zone, user_tz))
    if "start" in changes:
        _replace(comp, "dtstart", _task_when(changes["start"], zone, user_tz))
    if "priority" in changes:
        _replace(comp, "priority", int(changes["priority"]) if changes["priority"] else None)
    if "parent" in changes:
        _set_related_parent(comp, changes["parent"] or None)
    if "categories" in changes and changes["categories"] is not None:
        comp.pop("categories", None)
        cats = [str(c) for c in changes["categories"] if str(c).strip()]
        if cats:
            comp.add("categories", cats)
    if "sort_order" in changes:
        _replace(comp, "x-apple-sort-order",
                 str(int(changes["sort_order"])) if changes["sort_order"] is not None else None)
    if "recurrence" in changes:
        comp.pop("rrule", None)
        if changes["recurrence"]:
            comp.add("rrule", check_rrule(changes["recurrence"]))
    if "repeat_from" in changes:
        _replace(comp, "x-mailr-repeat-from", "COMPLETION" if changes["repeat_from"] == "completion" else None)
    if "reminders" in changes and changes["reminders"] is not None:
        set_alarms(comp, changes["reminders"] if comp.get("due") is not None else [],
                   str(comp.get("summary", "") or ""), related_end=True)
    _replace(comp, "dtstamp", datetime.now(UTC))
    _replace(comp, "last-modified", datetime.now(UTC))
    seq = int(comp.get("sequence", 0) or 0) + 1
    comp.pop("sequence", None)
    comp.add("sequence", seq)


def normalize_zones(cal: Any) -> dict[str, str]:
    """Rename every TZID that is not an IANA key but maps to one (Outlook's "Central Standard
    Time", aliases, prefixed ids) to that key, and drop the old VTIMEZONEs so the standard ones
    go in. UID, SEQUENCE and every other property stay. Returns {old: new}; unknown names stay
    with their VTIMEZONE."""
    renames: dict[str, str] = {}
    for _name, prop in cal.property_items(sorted=False):
        params = getattr(prop, "params", None)
        tzid = params.get("TZID") if params is not None else None
        if not tzid:
            continue
        tzid = str(tzid)
        if tzmod.is_iana(tzid) or _OFFSET_TZID.match(tzid):
            continue
        new = tzmod.iana(tzid)
        if not new:
            continue
        renames[tzid] = new
        z = ZoneInfo(new)
        params["TZID"] = new
        for item in (getattr(prop, "dts", None) or [prop]):
            dt = getattr(item, "dt", None)
            if isinstance(dt, datetime) and dt.tzinfo is not None:
                try:
                    item.dt = dt.astimezone(z)
                except Exception:  # noqa: BLE001, S110
                    pass
    if renames:
        cal.subcomponents = [c for c in cal.subcomponents
                             if not (c.name == "VTIMEZONE" and str(c.get("tzid", "")) in renames)]
    return renames


def serialize(cal: Any) -> str:
    """The resource text: IANA TZIDs only (`normalize_zones`), with a VTIMEZONE for each. A
    sender's VTIMEZONE for a known name never stays, and a costly one for an unknown name becomes
    its fixed offset (zones.clean)."""
    from mailr.calendar import zones

    zones.clean(cal)
    try:
        normalize_zones(cal)
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        cal.add_missing_timezones()
    except Exception:  # noqa: BLE001, S110
        pass
    return cal.to_ical().decode()
