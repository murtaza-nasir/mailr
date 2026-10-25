"""Incoming iTIP mail: replies to my invitations, and updates and cancellations
of events I was invited to.

`core/ingest.ingest_raw` queues `calendar.itip_incoming` for inbound mail that is not junk and
carries a calendar part, when the user has a calendar account. `handle` decides and applies;
the outcome goes in `upstream["itip"] = {applied, detail, method, …}` of the message, which
GET /messages/{id}/invite returns as `itip` (InviteCard: "Dana accepted — calendar updated").

Safety rules:
- Nothing is applied when DMARC or DKIM failed, or when the message names more than one sender
  (two From addresses or two From headers).
- Changes from others are applied on their own only when the sender is verified: DMARC passed,
  or a passing DKIM signature is from the sender's own domain (the organizer's, for an update or
  a cancel; the guest's, for a reply). No result, "none" or a missing header is not a pass.
  A DMARC pass counts only when the receiver evaluated it for the From domain (`header.from`).
  An unverified change that would otherwise apply is kept as `pending`; the card offers to
  apply it (POST /messages/{id}/itip/apply, the signed-in user only).
- Every VEVENT in a REQUEST or CANCEL carries the event's UID and the organizer's address as
  ORGANIZER, and there is at most one master: a message cannot make me the organizer, drop the
  organizer, or bring along components of another event.
- A repeat rule mailr would not write itself (expand.rule_problem: sub-daily, or too many
  occurrences to expand) is refused for updates and new invitations.
- REPLY: only to an event I organize, only when `From` is the single ATTENDEE of the reply, that
  guest is already on the event (a forwarded invitation is never added), the SEQUENCE is not
  lower than mine, and the DTSTAMP is newer than the last reply applied for that guest
  (kept as the ATTENDEE parameter X-MAILR-REPLY-STAMP).
- REQUEST and CANCEL from others: only to a copy I already have, when its organizer, the
  organizer in the message and `From` are the same address, and the SEQUENCE is higher (or
  equal with a newer DTSTAMP). My alarms are kept; my PARTSTAT is kept unless the time
  changed; a cancel marks the event cancelled and never deletes it.
- New invitations are never added, unless the setting `calendar_add_new_invites` is
  "needs_action": then they are added as Needs reply (never accepted).
- COUNTER is recorded for the reader (POST /calendar/events/{uid}/counter/accept applies it), with
  `verified` as for a REPLY: an unverified proposal is shown as such, with the sender's address,
  and accepting it needs the user's explicit "trust".
- Nothing from the sender's copy decides the user's reminders: its VALARMs and any X-MAILR-*
  property or parameter are dropped, and an update keeps the stored copy's.
- Calendar parts over zones.MAX_ITIP_BYTES are not read; parsing runs off the event loop, and a
  sender's VTIMEZONE is never trusted for a known zone name or when it is costly.
"""

from __future__ import annotations

import copy
import email.parser
import email.policy
import logging
from datetime import UTC, datetime
from typing import Any

from icalendar import Calendar
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import ical, itip, tz
from mailr.core.jobs import handler
from mailr.models import Message, User

log = logging.getLogger("mailr.calendar.inbound")

VERB = {"ACCEPTED": "accepted", "DECLINED": "declined", "TENTATIVE": "tentatively accepted",
        "DELEGATED": "delegated", "NEEDS-ACTION": "has not answered"}
STAMP_PARAM = "X-MAILR-REPLY-STAMP"


def _auth_failed(auth: dict[str, str]) -> bool:
    from mailr.core import authres

    return authres.failed(auth)


def _senders(raw: bytes) -> list[str]:
    """Every address in every From header of the message: two or more when one header lists several
    or there are several headers (core/fromline, the one strict reading of From)."""
    from email.utils import getaddresses

    from mailr.core import fromline

    head = email.parser.BytesHeaderParser(policy=email.policy.compat32).parsebytes(raw)
    vals = fromline.raw_values(head, "From")
    found = [fromline.address(a) for _, a in getaddresses(vals, strict=False) if a and a.strip()]
    if fromline.read(vals).problem == "several" and len(found) < 2:
        found = found + [""]  # several From headers, one of them unreadable: still more than one sender
    return found


def _auth_of(raw: bytes) -> dict[str, str]:
    """The trusted Authentication-Results of the message, read from its header alone (no body
    parse on the event loop)."""
    from mailr.core.mime import _auth_results

    head = email.parser.BytesHeaderParser(policy=email.policy.default).parsebytes(raw)
    return _auth_results(head)


def _verified(auth: dict[str, str], sender: str) -> bool:
    """A positive result for the sender's domain (core/authres.verified_for): DMARC pass for that
    domain, or a passing DKIM signature from it (or a parent of it). Absent results never count."""
    from mailr.core import authres

    dom = sender.rsplit("@", 1)[-1].lower() if "@" in sender else ""
    return authres.verified_for(auth, dom)


UNVERIFIED = {
    "REPLY": "An answer from an unverified sender; your calendar was not changed",
    "REQUEST": "An update from an unverified sender; your calendar was not changed",
    "CANCEL": "A cancellation from an unverified sender; your calendar was not changed",
    "ADD": "An invitation from an unverified sender; it was not added to your calendar",
}


def _pending(method: str, uid: str, **kw: Any) -> dict[str, Any]:
    return _result(False, UNVERIFIED["ADD" if kw.pop("add", False) else method], method, uid, pending=True,
                   verified=False, **kw)


def _stamp(comp: Any) -> datetime | None:
    v = comp.get("dtstamp")
    d = getattr(v, "dt", None)
    if isinstance(d, datetime):
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    return None


def _rid_key(comp: Any, cal: Any, tz: Any) -> str:
    rid = comp.get("recurrence-id")
    return ical.canonical_occurrence(ical.value(rid, cal, tz)[0]) if rid is not None else ""


def _find_comp(cal: Calendar, rid: str, tz: Any) -> Any:
    return next((c for c in cal.walk("VEVENT") if _rid_key(c, cal, tz) == rid), None)


def _times(comp: Any, cal: Any, tz: Any) -> tuple[Any, ...]:
    return tuple(ical.canonical_occurrence(ical.value(comp.get(k), cal, tz)[0]) if comp.get(k) is not None else None
                 for k in ("dtstart", "dtend")) + (ical.rrule_str(comp),)


def _result(applied: bool, detail: str, method: str, uid: str = "", **kw: Any) -> dict[str, Any]:
    return {"applied": applied, "detail": detail, "method": method, "uid": uid,
            "at": datetime.now(UTC).isoformat(), **kw}


async def handle(db: AsyncSession, user: User, m: Message, raw: bytes, *, confirmed: bool = False,
                 approved: bool = False) -> dict[str, Any]:
    """Apply one incoming iTIP message, if the safety rules allow it; returns the outcome.
    confirmed=True: the user chose to apply a change from an unverified sender. approved=True: the
    user approved a forwarded organizer change (calendar/forwarded.py)."""
    from mailr.calendar import forwarded, mirror, writer
    from mailr.calendar import tz as tzmod

    fwd = await forwarded.vouched(db, user, m, raw)
    if fwd is not None:
        # The user forwarded it from a trusted address, verified. The organizer's message inside
        # cannot be checked after Outlook forwards it, so its changes always wait for the user's yes
        # (calendar/forwarded.py).
        return await forwarded.handle(db, user, m, raw, fwd, approved=approved, confirmed=confirmed)
    ics = itip.find_calendar_part(raw)
    if ics is None:
        return _result(False, "No calendar data", "")
    inc = await parse_incoming(ics)
    if inc is None:
        return _result(False, "Couldn't read the calendar data", "")
    method = str(inc.get("method", "") or "").upper()
    comps = list(inc.walk("VEVENT"))
    if not comps:
        return _result(False, "No event in the calendar data", method)
    uid = str(comps[0].get("uid", "") or "")
    auth = _auth_of(raw)
    if _auth_failed(auth):
        return _result(False, "Couldn't verify the sender; the calendar was not changed", method, uid)
    if len(_senders(raw)) > 1:
        return _result(False, "The message names more than one sender; the calendar was not changed", method, uid)
    sender = (m.from_email or "").lower()
    verified = confirmed or _verified(auth, sender)
    me = await mirror.my_addresses(db, user.id)
    user_tz = await tzmod.user_zone(db, user)
    obj = await writer.find(db, user.id, uid, "event") if uid else None

    if method == "REPLY":
        return await _reply(db, user, obj, inc, comps[0], sender, me, user_tz, uid, verified)
    if method in ("REQUEST", "CANCEL"):
        if obj is None:
            if method == "REQUEST":
                return await _maybe_add(db, user, inc, sender, me, uid, verified)
            return _result(False, "Not in your calendar", method, uid)
        return await _from_organizer(db, user, obj, inc, comps, sender, me, user_tz, method, uid, verified)
    if method == "COUNTER":
        return _counter(obj, inc, comps[0], sender, user_tz, uid, verified)
    return _result(False, f"{method or 'This message'} is not applied", method, uid)


async def parse_incoming(ics: bytes) -> Calendar | None:
    """A calendar part from mail, parsed off the event loop within zones.PARSE_BUDGET; None when it
    is too large, unreadable or too slow."""
    from mailr.calendar import zones

    if len(ics) > zones.MAX_ITIP_BYTES:
        return None
    try:
        return await zones.off_loop(Calendar.from_ical, ics)
    except Exception:  # noqa: BLE001  TimeoutError included
        return None


def _counter(obj: Any, inc: Calendar, c: Any, sender: str, user_tz: Any, uid: str, verified: bool) -> dict[str, Any]:
    """A guest's proposal of a new time (COUNTER): recorded for the card, never applied here.
    Like a REPLY, it counts as the guest's only when the sender's domain is verified; otherwise the card says so, with the address, and accepting it needs
    the user's explicit yes (POST /calendar/events/{uid}/counter/accept with `trust`)."""
    start, _, _ = ical.value(c.get("dtstart"), inc, user_tz)
    end, _, _ = ical.value(c.get("dtend"), inc, user_tz)
    ok = obj is not None and obj.is_organizer
    proposed = {"start": ical.out_when(start), "end": ical.out_when(end),
                "recurrence_id": itip._occurrence(c.get("recurrence-id"))}
    if not ok:
        return _result(False, "A proposal for an event you do not organise", "COUNTER", uid, proposed=proposed,
                       counter=False, verified=verified)
    if not verified:
        return _result(False, f"A proposal from an unverified sender ({sender})", "COUNTER", uid,
                       proposed=proposed, counter=True, verified=False, sender=sender)
    return _result(False, "Proposed a new time", "COUNTER", uid, proposed=proposed, counter=True, verified=True,
                   sender=sender)


def _structure_problem(comps: list[Any], uid: str, organizer: str, me: set[str]) -> str | None:
    """A REQUEST or CANCEL from an organizer must describe one event of theirs: every component
    has the UID, names the organizer as ORGANIZER (never one of my addresses), and there is at
    most one master."""
    if organizer in me:
        return "The organiser is one of your own addresses"
    masters = 0
    for c in comps:
        if str(c.get("uid", "") or "") != uid:
            return "The message mixes components of different events"
        if c.get("organizer") is None or ical.addr(c.get("organizer")).lower() != organizer:
            return "Every part of the event must name the same organiser"
        if c.get("recurrence-id") is None:
            masters += 1
    if masters > 1:
        return "The message describes the event twice"
    return None


def _rule_problem(inc: Calendar, user_tz: Any = UTC) -> str | None:
    from mailr.calendar import expand

    return expand.rule_problem(inc, user_tz)


async def _reply(db: AsyncSession, user: User, obj: Any, inc: Calendar, comp: Any, sender: str, me: set[str],
                 user_tz: Any, uid: str, verified: bool = False) -> dict[str, Any]:
    from mailr.calendar import writer

    if obj is None:
        return _result(False, "Not in your calendar", "REPLY", uid)
    if not obj.is_organizer or (obj.organizer or "").lower() not in me:
        return _result(False, "You do not organise this event", "REPLY", uid)
    atts = ical.as_list(comp.get("attendee"))
    if len(atts) != 1:
        return _result(False, "The reply must name exactly one guest", "REPLY", uid)
    who = ical.addr(atts[0]).lower()
    if who != sender:
        return _result(False, "The sender is not the guest named in the reply", "REPLY", uid)
    partstat = str(atts[0].params.get("PARTSTAT", "")).upper()
    if partstat not in VERB:
        return _result(False, "The reply has no answer", "REPLY", uid)
    rid = _rid_key(comp, inc, user_tz)
    stored = Calendar.from_ical(obj.ics)
    target = _find_comp(stored, rid, user_tz) if rid else _find_comp(stored, "", user_tz)
    master = _find_comp(stored, "", user_tz)
    ref = target if target is not None else master
    if ref is None:
        return _result(False, "Not in your calendar", "REPLY", uid)
    guests = {ical.addr(a).lower(): a for a in ical.as_list(ref.get("attendee"))}
    if who not in guests:
        return _result(False, "Not on the guest list", "REPLY", uid, attendee=who)
    if int(comp.get("sequence", 0) or 0) < int(ref.get("sequence", 0) or 0):
        return _result(False, "Outdated: the event changed after this reply", "REPLY", uid, attendee=who)
    stamp = _stamp(comp) or datetime.now(UTC)
    prev = guests[who].params.get(STAMP_PARAM)
    if prev:
        try:
            if stamp <= datetime.strptime(str(prev), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC):
                return _result(False, "An older reply; a newer one was applied", "REPLY", uid, attendee=who)
        except ValueError:
            pass
    comment = ical.text_of(comp, "comment")[:1000]
    name = ical.cn(atts[0]) or ical.cn(guests[who]) or who
    if not verified:
        return _pending("REPLY", uid, attendee=who, partstat=partstat, recurrence_id=rid or None)

    def apply(cal: Calendar) -> None:
        tgt = _find_comp(cal, rid, user_tz) if rid else _find_comp(cal, "", user_tz)
        if tgt is None:
            m_ = _find_comp(cal, "", user_tz)
            when = ical.parse_when(rid, user_tz)
            tgt = ical.make_override(m_, when, user_tz)
            cal.add_component(tgt)
        atts_ = ical.as_list(tgt.get("attendee"))
        for a in atts_:
            if ical.addr(a).lower() == who:
                a.params["PARTSTAT"] = partstat
                a.params.pop("RSVP", None)
                a.params[STAMP_PARAM] = stamp.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
                if comment:
                    a.params["X-RESPONSE-COMMENT"] = comment.replace('"', "'")
                else:
                    a.params.pop("X-RESPONSE-COMMENT", None)
        tgt.pop("attendee", None)
        for a in atts_:
            tgt.add("attendee", a, encode=0)

    await writer.rmw(db, user.id, None, obj, apply)
    return _result(True, f"{name} {VERB[partstat]} — calendar updated", "REPLY", uid, attendee=who,
                   partstat=partstat, comment=comment or None, recurrence_id=rid or None)


def organizer_problem(obj: Any, inc: Calendar, comps: list[Any], sender: str, me: set[str], user_tz: Any,
                      method: str, uid: str) -> dict[str, Any] | None:
    """Why an organizer's REQUEST or CANCEL may not change my copy (the result to record), or None."""
    if obj.is_organizer:
        return _result(False, "You organise this event", method, uid)
    org_in = next((ical.addr(c.get("organizer")).lower() for c in comps if c.get("organizer") is not None), "")
    if not org_in or org_in != (obj.organizer or "").lower() or org_in != sender:
        return _result(False, "Not from the organiser of your copy", method, uid)
    bad = _structure_problem(comps, uid, sender, me)
    if bad:
        return _result(False, bad, method, uid)
    if method == "REQUEST":
        why = _rule_problem(inc, user_tz)
        if why:
            return _result(False, f"Not applied: {why}", method, uid)
    stored = Calendar.from_ical(obj.ics)
    for c in comps:
        rid = _rid_key(c, inc, user_tz)
        ref = _find_comp(stored, rid, user_tz) if rid else None
        ref = ref if ref is not None else _find_comp(stored, "", user_tz)
        seq_in, seq = int(c.get("sequence", 0) or 0), int(ref.get("sequence", 0) or 0)
        st_in, st = _stamp(c), _stamp(ref)
        if seq_in < seq or (seq_in == seq and not (st_in and st and st_in > st)):
            return _result(False, "Outdated: your copy is newer", method, uid)
    return None


# Properties of my copy that are mine, kept when the organizer's copy replaces it (forwarded
# updates, calendar/forwarded.py): its colour, categories, show-as and X- properties.
OWN_PROPS = ("color", "categories", "transp", "class")


async def _from_organizer(db: AsyncSession, user: User, obj: Any, inc: Calendar, comps: list[Any], sender: str,
                          me: set[str], user_tz: Any, method: str, uid: str, verified: bool = False,
                          keep_own: bool = False) -> dict[str, Any]:
    from mailr.calendar import writer

    problem = organizer_problem(obj, inc, comps, sender, me, user_tz, method, uid)
    if problem is not None:
        return problem
    Calendar.from_ical(obj.ics)  # the stored object must parse before anything changes
    if not verified:
        return _pending(method, uid, recurrence_id=_rid_key(comps[0], inc, user_tz) or None)
    if method == "CANCEL":
        def cancel(cal: Calendar) -> None:
            for c in comps:
                rid = _rid_key(c, inc, user_tz)
                tgt = _find_comp(cal, rid, user_tz)
                if tgt is None and rid:
                    tgt = ical.make_override(_find_comp(cal, "", user_tz), ical.parse_when(rid, user_tz), user_tz)
                    cal.add_component(tgt)
                for k, v in (("status", "CANCELLED"), ("sequence", int(c.get("sequence", 0) or 0))):
                    tgt.pop(k, None)
                    tgt.add(k, v)
                if c.get("dtstamp") is not None:
                    tgt.pop("dtstamp", None)
                    tgt.add("dtstamp", c.get("dtstamp").dt)

        await writer.rmw(db, user.id, None, obj, cancel)
        rid = _rid_key(comps[0], inc, user_tz)
        return _result(True, "Cancelled by the organiser — calendar updated", method, uid, recurrence_id=rid or None)

    time_changed = False
    previous: dict[str, Any] | None = None

    def update(cal: Calendar) -> None:
        nonlocal time_changed, previous
        whole = any(c.get("recurrence-id") is None for c in comps)
        old = {_rid_key(c, cal, user_tz): c for c in cal.walk("VEVENT")}
        if whole:  # the organizer's full copy replaces mine (my alarms and answer kept)
            keep_tz = list(cal.walk("VTIMEZONE"))
            cal.subcomponents = []
            for tzc in inc.walk("VTIMEZONE"):
                cal.add_component(copy.deepcopy(tzc))
            have = {str(t.get("tzid")) for t in cal.walk("VTIMEZONE")}
            for tzc in keep_tz:
                if str(tzc.get("tzid")) not in have:
                    cal.add_component(tzc)
        for c in comps:
            rid = _rid_key(c, inc, user_tz)
            new = ical.clean_sender_text(copy.deepcopy(c))
            # The organizer's alarms and any X-MAILR-* they wrote never reach my copy.
            ical.strip_sender_extras(new)
            mine = old.get(rid)
            if mine is not None:
                before = _times(mine, cal, user_tz)
                if before != _times(new, inc, user_tz):
                    time_changed = True
                    if previous is None:  # where it was (the first changed component)
                        previous = {"start": before[0], "end": before[1], "recurrence_id": rid or None}
                # My alarms and mailr's own X-MAILR-* properties stay mine.
                for al in mine.subcomponents:
                    if getattr(al, "name", "") == "VALARM":
                        new.add_component(al)
                for k in list(mine.keys()):
                    if k.upper().startswith("X-MAILR-"):
                        new.add(k, mine.get(k), encode=0)
                if keep_own:
                    for k in list(mine.keys()):
                        lk = k.lower()
                        if (lk in OWN_PROPS or lk.startswith("x-")) and k not in new:
                            new.add(k, mine.get(k), encode=0)
                if not time_changed:
                    my_old = {ical.addr(a).lower(): a.params.get("PARTSTAT") for a in ical.as_list(mine.get("attendee"))
                              if ical.addr(a).lower() in me}
                    atts = ical.as_list(new.get("attendee"))
                    for a in atts:
                        if ical.addr(a).lower() in my_old and my_old[ical.addr(a).lower()]:
                            a.params["PARTSTAT"] = my_old[ical.addr(a).lower()]
                            a.params.pop("RSVP", None)
                    new.pop("attendee", None)
                    for a in atts:
                        new.add("attendee", a, encode=0)
            if not whole and mine is not None:
                cal.subcomponents = [x for x in cal.subcomponents if x is not mine]
            cal.add_component(new)

    # The organizer's zone: the one their start is written in (null for all-day and floating times).
    _, org_tz, floating = ical.value(comps[0].get("dtstart"), inc, user_tz)
    await writer.rmw(db, user.id, None, obj, update)
    detail = "Updated by the organiser — calendar updated"
    if time_changed:
        detail = "The organiser changed the time — calendar updated"
    return _result(True, detail, method, uid, time_changed=time_changed, previous=previous,
                   organizer_tz=None if floating else tz.iana(org_tz))


async def _maybe_add(db: AsyncSession, user: User, inc: Calendar, sender: str, me: set[str],
                     uid: str, verified: bool = False) -> dict[str, Any]:
    """A new invitation: added only when the user turned `calendar_add_new_invites` on, as Needs
    reply, and only when it comes from its organizer."""
    from mailr.calendar import writer

    if (user.settings or {}).get("calendar_add_new_invites", "off") != "needs_action":
        return _result(False, "Not in your calendar", "REQUEST", uid)
    comps = list(inc.walk("VEVENT"))
    org = next((ical.addr(c.get("organizer")).lower() for c in comps if c.get("organizer") is not None), "")
    if not org or org != sender:
        return _result(False, "Not from the organiser", "REQUEST", uid)
    bad = _structure_problem(comps, uid, sender, me)
    if bad:
        return _result(False, bad, "REQUEST", uid)
    why = _rule_problem(inc)
    if why:
        return _result(False, f"Not added: {why}", "REQUEST", uid)
    mine = next((ical.addr(a) for c in comps for a in ical.as_list(c.get("attendee")) if ical.addr(a).lower() in me),
                None)
    if mine is None:
        return _result(False, "You are not on the guest list", "REQUEST", uid)
    if not verified:
        return _pending("REQUEST", uid, add=True)
    out = ical.new_calendar()
    for tzc in inc.walk("VTIMEZONE"):
        out.add_component(copy.deepcopy(tzc))
    for c in comps:
        x = ical.clean_sender_text(copy.deepcopy(c))
        ical.strip_sender_extras(x)  # my default reminders apply, never the sender's
        ical.set_attendee(x, mine, "NEEDS-ACTION")
        out.add_component(x)
    coll = await writer.target_collection(db, user.id, None, None, "event")
    await writer.create(db, user.id, coll, out, uid)
    return _result(True, "Added to your calendar as Needs reply", "REQUEST", uid)


@handler("calendar.itip_incoming")
async def itip_incoming(payload: dict[str, Any], ctx) -> None:
    from mailr.core.store import user_store
    from mailr.db import session_scope

    async with session_scope() as db:
        m = await db.get(Message, payload["message_id"])
        if m is None or m.deleted:
            return
        user = await db.get(User, m.user_id)
        raw = await (await user_store(db, user)).get_raw(m)
    async with session_scope() as db:
        m = await db.get(Message, payload["message_id"])
        user = await db.get(User, m.user_id)
        try:
            out = await handle(db, user, m, raw)
        except Exception as e:  # noqa: BLE001  a CalDAV failure: recorded, not retried forever
            log.warning("iTIP message %s not applied: %s", payload["message_id"], e)
            await db.rollback()
            m = await db.get(Message, payload["message_id"])
            out = _result(False, "The calendar server did not accept the change", "")
        m.upstream = {**(m.upstream or {}), "itip": out}
