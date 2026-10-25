"""iMIP: meeting invitations in email (RFC 6047).

`find_calendar_part` pulls a text/calendar part out of a raw message and `summarize` reads it;
`build_reply` produces the METHOD:REPLY iCalendar object the organizer's calendar expects (sent
through the SendGate's server-only `itip` payload, core/compose.py). Organizer mail is in
calendar/organizer.py; incoming REPLY, REQUEST and CANCEL are applied by calendar/inbound.py.
"""

from __future__ import annotations

import email
import email.policy
from datetime import UTC, datetime
from typing import Any

from icalendar import Calendar, Event, vCalAddress

PARTSTAT = {"accepted": "ACCEPTED", "declined": "DECLINED", "tentative": "TENTATIVE"}
VERB = {"accepted": "Accepted", "declined": "Declined", "tentative": "Tentatively accepted"}


def find_calendar_part(raw: bytes, *, forwarded: bool = False) -> bytes | None:
    """The message's own invitation (a text/calendar part, an .ics file). An invitation inside an
    attached email is not this message's, except with `forwarded`: the user forwarded the
    invitation from one of their trusted addresses (core/trusted.py), and Outlook attaches the
    original as an item. Its own parts are looked at first, then those of attached emails."""
    return _find(raw, depth=3 if forwarded else 0)


def _find(raw: bytes, depth: int) -> bytes | None:
    from mailr.core.mime import attached_email_bytes, is_attached_email, own_parts

    msg = email.message_from_bytes(raw, policy=email.policy.default)
    found = _calendar_part(own_parts(msg))
    if found is not None or depth <= 0:
        return found
    # An attached email read from its bytes (attached_email_bytes also decodes the base64 or
    # quoted-printable body some mailers give a message/rfc822 part), then its own parts.
    for _idx, part, inside in own_parts(msg):
        if inside or not is_attached_email(part):
            continue
        try:
            inner = attached_email_bytes(part)
        except Exception:  # noqa: BLE001  an unreadable attached email holds no invitation we can use
            continue
        found = _find(inner, depth - 1)
        if found is not None:
            return found
    return None


def _calendar_part(parts) -> bytes | None:
    fallback = None
    for _idx, part, inside in parts:
        if inside or part.is_multipart():
            continue  # an invitation inside an attached email is not this message's
        ctype = part.get_content_type()
        fname = (part.get_filename() or "").lower()
        if ctype == "text/calendar" or fname.endswith(".ics") or ctype == "application/ics":
            data = part.get_payload(decode=True) or b""
            if b"BEGIN:VCALENDAR" not in data:
                continue
            # Prefer the part that carries a METHOD (the iMIP part, not a plain .ics attachment).
            if part.get_param("method") or b"METHOD:" in data:
                return data
            fallback = fallback or data
    return fallback


def _when(v: Any) -> str | None:
    if v is None:
        return None
    d = v.dt
    if isinstance(d, datetime) and d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.isoformat()


def _occurrence(v: Any) -> str | None:
    if v is None:
        return None
    from mailr.calendar.ical import canonical_occurrence

    return canonical_occurrence(v.dt) or None


def _mail(v: Any) -> str:
    """The address of an ORGANIZER or ATTENDEE, lower case, display-safe (ical.addr)."""
    from mailr.calendar.ical import addr

    return addr(str(v).split(":", 1)[-1]).lower() if v else ""


def _cn(v: Any) -> str:
    from mailr.calendar.ical import cn

    return cn(v)


def summarize(ics: bytes, my_addresses: set[str]) -> dict[str, Any] | None:
    """The invitation card's reading of a calendar part. Synchronous and CPU-bound: the API calls
    it through zones.off_loop. A part over zones.MAX_ITIP_BYTES is not read; a VTIMEZONE that
    would cost too much is read as its fixed offset and named in `zones_replaced`."""
    from mailr.calendar import zones

    if len(ics) > zones.MAX_ITIP_BYTES:
        return None
    try:
        cal = Calendar.from_ical(ics)
    except Exception:  # noqa: BLE001
        return None
    method = str(cal.get("method", "") or "").upper()
    events = list(cal.walk("VEVENT"))
    if not events:
        return None
    # The master event (no RECURRENCE-ID) of the first event's UID: what Accept stores.
    uid0 = str(events[0].get("uid", "") or "")
    ev = next((e for e in events if e.get("recurrence-id") is None and str(e.get("uid", "") or "") == uid0), events[0])
    atts = ev.get("attendee") or []
    if not isinstance(atts, list):
        atts = [atts]
    attendees = []
    my_status = None
    me = None
    for a in atts:
        addr = _mail(a)
        st = str(a.params.get("PARTSTAT", "NEEDS-ACTION"))
        attendees.append({"email": addr, "name": _cn(a), "status": st,
                          "role": str(a.params.get("ROLE", ""))})
        if addr in my_addresses:
            my_status, me = st, addr
    org = ev.get("organizer")
    rid = ev.get("recurrence-id")
    from mailr.agent.agentwrite import strip_markdown_images
    from mailr.calendar.ical import value as _value

    try:  # the zone the organizer wrote the start in (null for all-day and floating times)
        _, org_tz, floating = _value(ev.get("dtstart"), cal)
        # An IANA name or none: Outlook writes Windows names ("Central Standard Time"), which a
        # browser's date formatter refuses, and the whole message failed to open (9 Oct 2026).
        from mailr.calendar.tz import iana

        tzid = getattr(ev.get("dtstart"), "params", {}).get("TZID")
        org_tz = None if floating else iana(str(tzid)) if tzid else iana(org_tz)
    except Exception:  # noqa: BLE001
        org_tz = None
    out = {
        "method": method, "uid": str(ev.get("uid", "")), "sequence": int(ev.get("sequence", 0) or 0),
        "recurrence_id": _occurrence(rid), "dtstamp": _when(ev.get("dtstamp")),
        "comment": str(ev.get("comment", "") or "")[:2000],
        "summary": str(ev.get("summary", "")), "start": _when(ev.get("dtstart")), "end": _when(ev.get("dtend")),
        "tz": org_tz,
        # The organizer's text without images: Accept stores what the card shows.
        "location": strip_markdown_images(str(ev.get("location", "") or "")),
        "description": strip_markdown_images(str(ev.get("description", "") or ""))[:4000],
        "organizer": {"email": _mail(org), "name": _cn(org) if org is not None else ""},
        "attendees": attendees[:100], "my_status": my_status, "my_address": me,
        "recurring": bool(ev.get("rrule")), "cancelled": method == "CANCEL" or str(ev.get("status", "")).upper() == "CANCELLED",
    }
    try:
        replaced = zones.problems(cal)
    except Exception:  # noqa: BLE001
        replaced = []
    if replaced:
        # The sender's own zone was not used: the times are read with its fixed offset.
        out["zones_replaced"] = replaced[:5]
    return out


def strip_schedule_params(ics: str) -> str:
    """Drop the RFC 6638 SCHEDULE-* parameters (SCHEDULE-AGENT, SCHEDULE-STATUS,
    SCHEDULE-FORCE-SEND) from every property. They are for the user's CalDAV server, never for
    the people who get the mail; the stored copy carries SCHEDULE-AGENT=CLIENT on a server that
    schedules by itself (organizer.stamp). Returns the text unchanged when there is none."""
    if "SCHEDULE-" not in ics.upper():
        return ics
    try:
        cal = Calendar.from_ical(ics)
    except Exception:  # noqa: BLE001  not ours to repair; send it as it is
        return ics
    changed = False
    for comp in cal.walk():
        for _name, val in comp.items():
            for v in val if isinstance(val, list) else [val]:
                params = getattr(v, "params", None)
                if not params:
                    continue
                for key in [k for k in params if str(k).upper().startswith("SCHEDULE-")]:
                    del params[key]
                    changed = True
    return cal.to_ical().decode() if changed else ics


def build_reply(ics: bytes, attendee: str, response: str, attendee_name: str = "", *,
                occurrence: str | None = None, this_and_future: bool = False, user_tz: Any = None) -> bytes:
    """METHOD:REPLY with only my ATTENDEE line and the new PARTSTAT. With `occurrence` (the
    canonical string) the reply is about that occurrence of a series: it carries its
    RECURRENCE-ID (with RANGE=THISANDFUTURE for an answer to it and the ones after)."""
    from mailr.calendar import ical as icalmod

    src = Calendar.from_ical(ics)
    ev = next(iter(src.walk("VEVENT")))
    if occurrence:
        tz = user_tz or UTC
        occ = icalmod.parse_when(occurrence, tz)
        master, overrides = icalmod.master_and_overrides(src)
        ev = next((o for o in overrides if icalmod.same_instant(o.get("recurrence-id"), occ)), None) \
            or icalmod.make_override(master, occ, tz)
        if this_and_future:
            ev["recurrence-id"].params["RANGE"] = "THISANDFUTURE"
    out = Calendar()
    out.add("prodid", "-//mailr//EN")
    out.add("version", "2.0")
    out.add("method", "REPLY")
    rep = Event()
    for key in ("uid", "sequence", "dtstart", "dtend", "summary", "recurrence-id", "organizer", "location"):
        if ev.get(key) is not None:
            rep.add(key, ev.get(key), encode=0)
    rep.add("dtstamp", datetime.now(UTC))
    a = vCalAddress(f"mailto:{attendee}")
    a.params["PARTSTAT"] = PARTSTAT[response]
    if attendee_name:
        a.params["CN"] = attendee_name
    rep.add("attendee", a, encode=0)
    out.add_component(rep)
    for tz in src.walk("VTIMEZONE"):
        out.add_component(tz)
    return out.to_ical()
