"""Invitations from events I organize.

`plan(old_ics, new_ics, me)` is a pure function: from the copy before and after a write it lists
the iTIP messages (RFC 5546) the change needs.
- A create with guests sends REQUEST to every guest.
- A visible change sends REQUEST to the current guests; a guest added later gets an invitation.
- Removed guests get CANCEL.
- An occurrence deleted from a series (a new EXDATE, or an override set to CANCELLED) sends
  CANCEL with RECURRENCE-ID; a whole event deleted or cancelled sends CANCEL to every guest.
- A "following" split is two writes (the shortened series and the new UID), so it sends the
  REQUESTs of both.

`stamp` adjusts the copy before it is written: ORGANIZER is the sending identity (with CN), I am
an ATTENDEE with PARTSTAT=ACCEPTED, guests get RSVP=TRUE, `SCHEDULE-AGENT=CLIENT` is added when
the server schedules by itself (RFC 6638 `calendar-auto-schedule`), and a significant change
(time, duration, rule, dates, location, status) bumps SEQUENCE and resets the guests' PARTSTAT.

`session()` wraps a write: writer.WRITE_HOOK calls `before_write` for every changed copy, and
`finish()` sends the planned messages through the SendGate with a server-only `itip` payload
(core/compose.py builds the text/calendar alternative and the invite.ics attachment). The undo
window of the user applies. Route gating (calendar.invite / calendar.cancel) happens before the
write, in calendar/routes.py, with `invite_context`.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from icalendar import Calendar, Event, vCalAddress
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr import i18n
from mailr.calendar import ical
from mailr.calendar import tz as tzmod
from mailr.models import Connector, Identity, User

log = logging.getLogger("mailr.calendar.organizer")

SIGNIFICANT = ("dtstart", "dtend", "duration", "rrule", "rdate", "exdate", "location", "status")
VISIBLE = SIGNIFICANT + ("summary", "description", "url")
_DATES = ("dtstart", "dtend", "recurrence-id")
# Changes that never reach the guests (my alarms, my colour and tags, where I keep it).
PRIVATE_FIELDS = {"reminders", "color", "categories", "transparent", "class", "calendar"}


@dataclass
class ItipMsg:
    method: str  # REQUEST | CANCEL | DECLINECOUNTER
    kind: str  # invitation | update | cancel | decline_counter
    uid: str
    summary: str
    to: list[dict[str, str]]
    ics: str
    recurrence_id: str | None = None
    comp: Any = field(default=None, repr=False)  # the VEVENT the mail text describes


# ---------------- reading ----------------


def _load(ics: str | bytes | Calendar | None) -> Calendar | None:
    if ics is None or ics == "":
        return None
    if isinstance(ics, Calendar):
        return ics
    try:
        return Calendar.from_ical(ics)
    except Exception:  # noqa: BLE001
        return None


def _events(cal: Calendar | None) -> list[Any]:
    return list(cal.walk("VEVENT")) if cal is not None else []


def _rid(comp: Any, cal: Any) -> str:
    """"" for the master, else the canonical RECURRENCE-ID."""
    rid = comp.get("recurrence-id")
    if rid is None:
        return ""
    return ical.canonical_occurrence(ical.value(rid, cal, UTC)[0])


def _by_rid(cal: Calendar | None) -> dict[str, Any]:
    return {_rid(c, cal): c for c in _events(cal)}


def _master(cal: Calendar | None) -> Any:
    comps = _events(cal)
    return next((c for c in comps if c.get("recurrence-id") is None), comps[0] if comps else None)


def _addr(v: Any) -> str:
    return ical.addr(v).lower()


def organizer_of(cal: Calendar | None) -> str:
    m = _master(cal)
    return _addr(m.get("organizer")) if m is not None and m.get("organizer") is not None else ""


def guests(cal: Calendar | None, me: set[str]) -> dict[str, dict[str, str]]:
    """{email: {email, name}} of every ATTENDEE in the resource except me and the organizer."""
    org = organizer_of(cal)
    out: dict[str, dict[str, str]] = {}
    for c in _events(cal):
        for a in ical.as_list(c.get("attendee")):
            e = _addr(a)
            if e and e not in me and e != org and e not in out:
                out[e] = {"email": e, "name": ical.cn(a)}
    return out


def _value(comp: Any, key: str) -> Any:
    """A comparable form of one property: instants for dates, sets for date lists, text else."""
    v = comp.get(key)
    if v is None:
        return None
    if key in _DATES:
        return ical.canonical_occurrence(ical.value(v, None, UTC)[0])
    if key in ("exdate", "rdate"):
        return _date_set(comp, key)
    if key == "duration":
        return str(getattr(v, "dt", v))
    vals = ical.as_list(v)
    out = []
    for x in vals:
        try:
            out.append(x.to_ical().decode() if hasattr(x, "to_ical") else str(x))
        except Exception:  # noqa: BLE001
            out.append(str(x))
    return "\n".join(out)


def _date_set(comp: Any, key: str) -> frozenset[str]:
    out: set[str] = set()
    for p in ical.as_list(comp.get(key)):
        for d in getattr(p, "dts", []) or []:
            out.add(ical.canonical_occurrence(d.dt if d.dt.tzinfo or not isinstance(d.dt, datetime)
                                              else d.dt.replace(tzinfo=UTC)))
    return frozenset(out)


def significant(old: Any, new: Any, *, exdate: bool = True) -> bool:
    """A change the guests must answer again: time, duration, rule, dates, location, status."""
    keys = SIGNIFICANT if exdate else tuple(k for k in SIGNIFICANT if k != "exdate")
    return any(_value(old, k) != _value(new, k) for k in keys)


def _visible_change(old: Any, new: Any) -> bool:
    """A change the guests see (a change of the guest list alone mails only the added and the
    removed guests)."""
    if any(_value(old, k) != _value(new, k) for k in VISIBLE if k != "exdate"):
        return True
    o, n = _date_set(old, "exdate"), _date_set(new, "exdate")
    return not o <= n  # an occurrence came back


def _cancelled(comp: Any) -> bool:
    return ical.text_of(comp, "status").upper() == "CANCELLED"


# ---------------- planning ----------------


def plan(old_ics: str | bytes | Calendar | None, new_ics: str | bytes | Calendar | None, me: set[str],
         tz: tzinfo | None = None) -> list[ItipMsg]:
    """The iTIP messages one write needs (old None = created, new None = deleted)."""
    me = {x.lower() for x in me}
    old, new = _load(old_ics), _load(new_ics)
    ref = new if new is not None else old
    master = _master(ref)
    if master is None:
        return []
    org = organizer_of(ref)
    if not org or org not in me:
        return []  # not mine to send (I am a guest, or nobody organizes it)
    uid = str(master.get("uid", ""))
    summary = ical.text_of(master, "summary")
    new_g = guests(new, me)
    old_g = guests(old, me) if old is not None else {}
    msgs: list[ItipMsg] = []

    def to(emails: Any, pool: dict[str, dict[str, str]]) -> list[dict[str, str]]:
        return [pool[e] for e in sorted(emails)]

    if new is None:  # deleted: every guest is told
        if old_g:
            msgs.append(_cancel(old, to(old_g, old_g), uid, summary))
        return msgs
    removed = set(old_g) - set(new_g)
    if removed:
        msgs.append(_cancel(old, to(removed, old_g), uid, summary, only=removed, master_only=True,
                            sequence=int(_master(new).get("sequence", 0) or 0)))
    if not new_g:
        return msgs
    if old is None:
        msgs.append(_request(new, to(new_g, new_g), uid, summary, "invitation"))
        return msgs
    old_m, new_m = _master(old), _master(new)
    if _cancelled(new_m) and not _cancelled(old_m):  # the whole event was cancelled
        msgs.append(_cancel(new, to(new_g, new_g), uid, summary))
        return msgs
    old_by, new_by = _by_rid(old), _by_rid(new)
    # Occurrences that went away: new EXDATEs, and overrides newly set to CANCELLED.
    gone = set(_date_set(new_m, "exdate") - _date_set(old_m, "exdate"))
    gone |= {r for r, c in new_by.items() if r and _cancelled(c) and not (r in old_by and _cancelled(old_by[r]))}
    for r in sorted(gone):
        msgs.append(_cancel_occurrence(new, r, to(new_g, new_g), uid, summary, tz))
    changed = False
    for r in set(old_by) | set(new_by):
        if r in gone:
            continue
        o, n = old_by.get(r), new_by.get(r)
        if o is None or n is None:
            if r and r in gone:
                continue
            changed = True
            break
        if _visible_change(o, n):
            changed = True
            break
    kept = set(new_g) & set(old_g)
    added = set(new_g) - set(old_g)
    if changed and kept:
        msgs.append(_request(new, to(kept, new_g), uid, summary, "update"))
    if added:
        msgs.append(_request(new, to(added, new_g), uid, summary, "invitation"))
    return msgs


def _out_cal(method: str) -> Calendar:
    out = ical.new_calendar()
    out.add("method", method)
    return out


def _strip(comp: Any) -> Any:
    c = copy.deepcopy(comp)
    c.subcomponents = [s for s in c.subcomponents if getattr(s, "name", "") != "VALARM"]
    if c.get("dtstamp") is None:
        c.add("dtstamp", datetime.now(UTC))
    return c


def _timezones(src: Calendar, out: Calendar) -> None:
    for tzc in src.walk("VTIMEZONE"):
        out.add_component(copy.deepcopy(tzc))


def _request(cal: Calendar, rcpts: list[dict[str, str]], uid: str, summary: str, kind: str) -> ItipMsg:
    out = _out_cal("REQUEST")
    _timezones(cal, out)
    for c in _events(cal):
        out.add_component(_strip(c))
    return ItipMsg("REQUEST", kind, uid, summary, rcpts, ical.serialize(out), comp=_master(cal))


def _set_attendees(comp: Any, keep: set[str]) -> None:
    atts = [a for a in ical.as_list(comp.get("attendee")) if _addr(a) in keep]
    comp.pop("attendee", None)
    for a in atts:
        comp.add("attendee", a, encode=0)


def _cancel(cal: Calendar, rcpts: list[dict[str, str]], uid: str, summary: str, *, only: set[str] | None = None,
            master_only: bool = False, sequence: int | None = None) -> ItipMsg:
    """CANCEL of the whole event (to every guest), or to removed guests only (`only`, from the
    copy before the change, at the new `sequence`)."""
    out = _out_cal("CANCEL")
    _timezones(cal, out)
    comps = [_master(cal)] if master_only else _events(cal)
    for c in comps:
        x = _strip(c)
        x.pop("status", None)
        x.add("status", "CANCELLED")
        seq = int(x.get("sequence", 0) or 0)
        if only is None:  # the event itself ends: a new version
            x.pop("sequence", None)
            x.add("sequence", seq + 1)
        else:
            org = organizer_of(cal)
            _set_attendees(x, set(only) | {org})
            if sequence is not None:
                x.pop("sequence", None)
                x.add("sequence", sequence)
        out.add_component(x)
    return ItipMsg("CANCEL", "cancel", uid, summary, rcpts, ical.serialize(out), comp=_master(cal))


def _cancel_occurrence(cal: Calendar, rid: str, rcpts: list[dict[str, str]], uid: str, summary: str,
                       tz: tzinfo | None) -> ItipMsg:
    """CANCEL with RECURRENCE-ID: one occurrence of the series is gone."""
    master = _master(cal)
    out = _out_cal("CANCEL")
    _timezones(cal, out)
    ov = _by_rid(cal).get(rid)
    if ov is not None:
        x = _strip(ov)
    else:
        when = ical.parse_when(rid, tz or UTC)
        rv = ical.in_zone_of(master.get("dtstart"), when, tz or UTC)
        x = Event()
        for key in ("uid", "organizer", "summary", "location"):
            if master.get(key) is not None:
                x.add(key, master.get(key), encode=0)
        for a in ical.as_list(master.get("attendee")):
            x.add("attendee", a, encode=0)
        x.add("dtstamp", datetime.now(UTC))
        x.add("recurrence-id", rv)
        x.add("dtstart", rv)
    x.pop("status", None)
    x.add("status", "CANCELLED")
    x.pop("sequence", None)
    x.add("sequence", int(master.get("sequence", 0) or 0))
    out.add_component(x)
    return ItipMsg("CANCEL", "cancel", uid, summary, rcpts, ical.serialize(out), recurrence_id=rid, comp=x)


# ---------------- stamping the written copy ----------------


def stamp(cal: Calendar, old: Calendar | None, ident_email: str, ident_name: str, me: set[str],
          auto_schedule: bool = False) -> None:
    """Organizer fields on an event with guests (see the module docstring). Events organized by
    someone else are left alone."""
    me = {x.lower() for x in me}
    comps = _events(cal)
    if not comps:
        return
    if not any(c.get("attendee") is not None for c in comps):
        return
    org = organizer_of(cal)
    if org and org not in me:
        return
    old_by = _by_rid(old)
    old_master = _master(old)
    for c in comps:
        if c.get("organizer") is None or not _addr(c.get("organizer")):
            o = vCalAddress(f"mailto:{ident_email}")
            if ident_name:
                o.params["CN"] = ident_name
            c.pop("organizer", None)
            c.add("organizer", o, encode=0)
        mine = ({_addr(c.get("organizer"))} | me)
        atts = ical.as_list(c.get("attendee"))
        if atts and not any(_addr(a) in mine for a in atts):
            a = vCalAddress(f"mailto:{_addr(c.get('organizer')) or ident_email}")
            a.params["PARTSTAT"] = "ACCEPTED"
            if ident_name:
                a.params["CN"] = ident_name
            atts.append(a)
        rid = _rid(c, cal)
        prev = old_by.get(rid)
        if prev is None and rid and old_master is not None:
            prev = _as_occurrence(old_master, c)
        reset = prev is not None and significant(prev, c, exdate=False)
        for a in atts:
            if _addr(a) in mine:
                if a.params.get("PARTSTAT") in (None, "", "NEEDS-ACTION"):
                    a.params["PARTSTAT"] = "ACCEPTED"
                a.params.pop("RSVP", None)
            else:
                if reset:
                    a.params["PARTSTAT"] = "NEEDS-ACTION"
                if reset or "PARTSTAT" not in a.params or a.params.get("PARTSTAT") == "NEEDS-ACTION":
                    a.params["RSVP"] = "TRUE"
            if auto_schedule:
                a.params["SCHEDULE-AGENT"] = "CLIENT"
        c.pop("attendee", None)
        for a in atts:
            c.add("attendee", a, encode=0)
        if auto_schedule:
            c["organizer"].params["SCHEDULE-AGENT"] = "CLIENT"
        if reset and prev is not None:
            old_seq = int(prev.get("sequence", 0) or 0)
            if int(c.get("sequence", 0) or 0) <= old_seq:
                c.pop("sequence", None)
                c.add("sequence", old_seq + 1)


def _as_occurrence(master: Any, override: Any) -> Any:
    """The occurrence a new override replaces, as the master described it (its time at the
    RECURRENCE-ID, the master's location and status)."""
    x = copy.deepcopy(master)
    rid = override.get("recurrence-id")
    start = getattr(rid, "dt", None)
    if start is None:
        return x
    dur = None
    if master.get("dtend") is not None and master.get("dtstart") is not None:
        dur = master["dtend"].dt - master["dtstart"].dt
    for k in ("rrule", "rdate", "exdate", "dtstart", "dtend", "duration"):
        x.pop(k, None)
    x.add("dtstart", start)
    if dur is not None:
        x.add("dtend", start + dur)
    for k in ("rrule", "rdate", "exdate"):
        if override.get(k) is not None:
            x.add(k, override.get(k), encode=0)
    return x


# ---------------- who sends, from where ----------------


async def sending_identity(db: AsyncSession, user_id: int, identity_id: int | None = None) -> Identity | None:
    """The identity that becomes ORGANIZER: the one named, else the default identity with an
    outgoing connector."""
    if identity_id:
        ident = await db.get(Identity, int(identity_id))
        if ident is None or ident.user_id != user_id:
            raise ValueError("unknown sending identity")
        if ident.connector_id is None:
            raise ValueError(f"{ident.email} has no outgoing mail server")
        return ident
    return (await db.execute(select(Identity).where(Identity.user_id == user_id, Identity.connector_id.is_not(None))
                             .order_by(Identity.is_default.desc(), Identity.id))).scalars().first()


async def auto_schedule(db: AsyncSession, conn: Connector) -> bool:
    """Does the CalDAV server schedule by itself (RFC 6638)? Asked once with OPTIONS and kept
    in the connector state."""
    st = (conn.state or {}).get("caldav") or {}
    if "auto_schedule" in st:
        return bool(st["auto_schedule"])
    from mailr.calendar import dav
    from mailr.calendar.sync import dav_client

    user = await db.get(User, conn.user_id)
    found = False
    try:
        async with dav_client(conn, user) as client:
            found = "calendar-auto-schedule" in await client.options()
    except Exception as e:  # noqa: BLE001  unknown: assume a plain server
        log.info("OPTIONS on connector %s failed: %s", conn.id, e)
        if isinstance(e, dav.DavError):
            return False
        return False
    conn.state = {**(conn.state or {}), "caldav": {**st, "auto_schedule": found}}
    return found


async def invite_context(db: AsyncSession, p: Any, action: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Before an event write: does it mail guests? None when it does not; else the permission
    class (calendar.invite or calendar.cancel), the recipients, the sending identity and the
    send facts (`send_context`) the class is checked against. ValueError when guests would be
    mailed but there is no sending identity."""
    from mailr.calendar import mirror
    from mailr.core.sendgate import send_context

    payload.pop("_itip_to", None)
    if payload.get("send_updates") is False:
        return None
    me = await mirror.my_addresses(db, p.user_id)
    cls = "calendar.invite"
    if action == "calendar.create":
        rcpts = [a.get("email") if isinstance(a, dict) else str(a) for a in payload.get("attendees") or []]
        rcpts = [r.lower() for r in rcpts if r and r.lower() not in me]
    else:
        obj = await mirror.find_by_uid(db, p.user_id, payload.get("uid") or "", "event", p)
        if obj is None or (obj.organizer and obj.organizer.lower() not in me):
            return None
        # Every guest of every component, uncapped: the same list `plan` mails (the mirror's
        # `attendees` names the master's guests only, and at most 100).
        stored = _load(obj.ics)
        if stored is not None:
            old = list(guests(stored, me))
        else:
            old = [a["email"].lower() for a in obj.attendees or [] if a.get("email")]
        if action == "calendar.delete":
            if not obj.organizer:
                return None
            rcpts = old
            cls = "calendar.cancel"
        else:
            changes = payload.get("changes") or {}
            if not set(changes) - PRIVATE_FIELDS:
                return None
            new = old
            if changes.get("attendees") is not None:
                new = [(a.get("email") if isinstance(a, dict) else str(a)).lower() for a in changes["attendees"]]
            if not obj.organizer and changes.get("attendees") is None:
                return None
            rcpts = list(dict.fromkeys(new + old))
            if str(changes.get("status") or "").upper() == "CANCELLED":
                cls = "calendar.cancel"
        rcpts = [r for r in rcpts if r and r not in me and r != (obj.organizer or "").lower()]
    if not rcpts:
        return None
    ident = await sending_identity(db, p.user_id, payload.get("identity_id"))
    if ident is None:
        raise ValueError("add a sending address with an outgoing mail server before inviting guests "
                         "(or save with send_updates=false)")
    ctx = await send_context(db, p.user_id, {}, rcpts, ident, None)
    # The write carries the checked list; Session.finish mails no one outside it.
    payload["_itip_to"] = list(rcpts)
    return {"cls": cls, "recipients": rcpts, "identity": ident, "ctx": ctx}


# ---------------- the write session ----------------


class Session:
    """Collects the writes of one request (writer.WRITE_HOOK) and sends their mail afterwards."""

    def __init__(self, user: User, ident: Identity | None, me: set[str], auto: dict[int, bool], send: bool,
                 message: str = "", tz: tzinfo | None = None, allowed: list[str] | None = None):
        self.user, self.ident, self.me, self.auto, self.send = user, ident, me, auto, send
        self.message, self.tz = message, tz
        # The recipients the request was checked (and approved) against; None: not recorded.
        self.allowed = {a.lower() for a in allowed} if allowed is not None else None
        self.withheld: set[str] = set()
        self.writes: dict[str, tuple[str | None, Calendar | None]] = {}
        self._undo: dict[str, tuple[str | None, Calendar | None] | None] = {}

    def before_write(self, uid: str, old_ics: str | None, cal: Calendar | None, connector_id: int) -> None:
        if cal is not None and not _events(cal):
            return  # a task
        if cal is not None and self.ident is not None:
            stamp(cal, _load(old_ics), self.ident.email, self.ident.name or self.user.name or "", self.me,
                  self.auto.get(connector_id, False))
        first_old = self.writes[uid][0] if uid in self.writes else old_ics
        self._undo[uid] = self.writes.get(uid)
        self.writes[uid] = (first_old, cal)

    def write_failed(self, uid: str) -> None:
        """The PUT was refused (412): forget it, so the retry on the server's fresh copy is
        planned against that copy and not against the stale one."""
        if uid not in self._undo:
            return
        prev = self._undo.pop(uid)
        if prev is None:
            self.writes.pop(uid, None)
        else:
            self.writes[uid] = prev

    def planned(self) -> list[ItipMsg]:
        out: list[ItipMsg] = []
        for old, new in self.writes.values():
            out += plan(old, new, self.me, self.tz)
        return out

    async def finish(self, db: AsyncSession, result: Any = None) -> Any:
        """Send what the writes need; `invites: {outbox_ids, recipients}` is added to a dict result."""
        if not self.send or self.ident is None:
            return result
        msgs = self.planned()
        if self.allowed is not None:
            for m in msgs:
                extra = [t for t in m.to if t["email"].lower() not in self.allowed]
                if extra:
                    self.withheld |= {t["email"].lower() for t in extra}
                    m.to = [t for t in m.to if t["email"].lower() in self.allowed]
            if self.withheld:
                log.warning("guest mail withheld from %d address(es) the request was not checked against",
                            len(self.withheld))
        if not msgs:
            return result
        sent = await send(db, self.user, msgs, self.ident, message=self.message, tz=self.tz)
        if self.withheld:
            sent = {**sent, "withheld": sorted(self.withheld)}
        if isinstance(result, dict) and (sent["outbox_ids"] or self.withheld):
            result = {**result, "invites": sent}
        return result


class _NoSession:
    async def finish(self, db: AsyncSession, result: Any = None) -> Any:
        return result


@asynccontextmanager
async def session(db: AsyncSession, user: User, payload: dict[str, Any]) -> AsyncIterator[Any]:
    """Around an event write whose route cleared it for guest mail (`_itip: "ok"`, set by
    calendar/routes.py after the calendar.invite / calendar.cancel check)."""
    from mailr.calendar import mirror, writer

    if payload.get("_itip") != "ok":
        yield _NoSession()
        return
    try:
        ident = await sending_identity(db, user.id, payload.get("identity_id"))
    except ValueError:
        ident = None
    me = await mirror.my_addresses(db, user.id)
    auto = {}
    for c in await mirror.visible_connectors(db, user.id):
        auto[c.id] = await auto_schedule(db, c)
    s = Session(user, ident, me, auto, payload.get("send_updates") is not False,
                message=str(payload.get("message") or ""), tz=await tzmod.user_zone(db, user),
                allowed=payload.get("_itip_to"))
    token = writer.WRITE_HOOK.set(s)
    try:
        yield s
    finally:
        writer.WRITE_HOOK.reset(token)


def decline_counter(stored: Calendar, guest: dict[str, str], recurrence_id: str | None, comment: str,
                    tz: tzinfo) -> ItipMsg:
    """METHOD:DECLINECOUNTER to the guest who proposed a new time (RFC 5546 3.2.8): the event's
    current UID, SEQUENCE, ORGANIZER and times, the guest as ATTENDEE, the RECURRENCE-ID the
    proposal was about, and my COMMENT."""
    master = _master(stored)
    ref = master
    if recurrence_id:
        occ = ical.parse_when(recurrence_id, tz)
        ref = next((c for c in _events(stored) if c.get("recurrence-id") is not None
                    and ical.same_instant(c.get("recurrence-id"), occ)), None) or ical.make_override(master, occ, tz)
    out = _out_cal("DECLINECOUNTER")
    _timezones(stored, out)
    ev = Event()
    for k in ("uid", "sequence", "organizer", "dtstart", "dtend", "summary", "recurrence-id"):
        if ref.get(k) is not None:
            ev.add(k, ref.get(k), encode=0)
    ev.add("dtstamp", datetime.now(UTC))
    a = vCalAddress(f"mailto:{guest['email']}")
    if guest.get("name"):
        a.params["CN"] = guest["name"]
    ev.add("attendee", a, encode=0)
    if comment:
        ev.add("comment", comment[:2000])
    out.add_component(ev)
    uid = ical.text_of(master, "uid")
    return ItipMsg("DECLINECOUNTER", "decline_counter", uid, ical.text_of(master, "summary"), [guest],
                   ical.serialize(out), recurrence_id=recurrence_id, comp=ref)


# ---------------- sending ----------------

# The mail's words are in the organizer's language (mail.itip.*): the guests are often outside the
# server, and the organizer is the one person whose language the server knows.


def subject_text(kind: str, summary: str | None, lang: str | None = None) -> str:
    return i18n.t(f"mail.itip.subject.{kind}", lang, title=summary or i18n.t("mail.itip.untitled", lang))


def _when_text(comp: Any, tz: tzinfo | None, lang: str | None = None) -> str:
    if comp is None:
        return ""
    start, _, _ = ical.value(comp.get("dtstart"), None, tz or UTC)
    end, _, _ = ical.value(comp.get("dtend"), None, tz or UTC)
    if start is None:
        return ""
    day = lambda d: i18n.day_text(d, lang)  # noqa: E731
    at = lambda d: i18n.t("mail.date.day_time", lang, date=day(d), time=i18n.time_text(d, lang))  # noqa: E731
    if isinstance(start, date) and not isinstance(start, datetime):
        last = (end - timedelta(days=1)) if isinstance(end, date) and not isinstance(end, datetime) and end > start else start
        return day(start) if last == start else i18n.t("mail.date.range", lang, start=day(start), end=day(last))
    zone = start.tzinfo if tzmod.zone_name(start.tzinfo) not in (None, "UTC") else (tz or UTC)
    s = start.astimezone(zone)
    text = at(s)
    if isinstance(end, datetime):
        e = end.astimezone(zone)
        text = i18n.t("mail.date.range", lang, start=text, end=i18n.time_text(e, lang) if e.date() == s.date() else at(e))
    name = tzmod.zone_name(zone)
    return i18n.t("mail.date.zone", lang, when=text, zone=name) if name else text


def body_text(msg: ItipMsg, organizer: str, tz: tzinfo | None, message: str = "", lang: str | None = None) -> str:
    comp = msg.comp
    kind = "cancel_occurrence" if msg.kind == "cancel" and msg.recurrence_id else msg.kind
    if kind not in ("decline_counter", "cancel", "cancel_occurrence", "update"):
        kind = "invitation"
    lines = [i18n.t(f"mail.itip.body.{kind}", lang, organizer=organizer), ""]
    lines.append(msg.summary or i18n.t("mail.itip.untitled", lang))
    when = _when_text(comp, tz, lang)
    if when:
        lines.append(i18n.t("mail.itip.when", lang, when=when))
    loc = ical.text_of(comp, "location") if comp is not None else ""
    if loc:
        lines.append(i18n.t("mail.itip.where", lang, where=loc))
    if message:
        lines += ["", message]
    desc = ical.text_of(comp, "description") if comp is not None else ""
    if desc and msg.kind != "cancel":
        lines += ["", desc[:4000]]
    return "\n".join(lines)


async def send(db: AsyncSession, user: User, msgs: list[ItipMsg], ident: Identity, *, message: str = "",
               tz: tzinfo | None = None) -> dict[str, Any]:
    """Queue the messages through the SendGate as the user (the undo window applies)."""
    from mailr.core.sendgate import SendGate, _SystemPrincipal

    ids: list[int] = []
    rcpts: list[str] = []
    who = ident.name or user.name or ident.email
    lang = i18n.user_language(user)
    for m in msgs:
        if not m.to:
            continue
        payload = {"identity_id": ident.id, "to": m.to, "cc": [], "bcc": [],
                   "subject": subject_text(m.kind, m.summary, lang),
                   "text": body_text(m, who, tz, message, lang)}
        ob = await SendGate(db, _SystemPrincipal(user)).submit(payload, system={"itip": {"method": m.method,
                                                                                         "blob": m.ics}})
        ids.append(ob.id)
        rcpts += [t["email"] for t in m.to]
    return {"outbox_ids": ids, "recipients": sorted(set(rcpts))}
