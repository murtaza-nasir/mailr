"""Organizer changes the user forwards from a trusted address.

A work account that is not connected to mailr (Outlook, for example) can forward meeting
requests, updates and cancellations to the user's mailr address with a rule. The forward itself passes the trust rule
(core/trusted.py: a confirmed address, verified by its domain, sent by the user alone). The organizer's
message inside it cannot be checked: after the rule forwards it, nothing shows who wrote it, and an
auto-forward rule forwards an outsider's forged "update" just the same. So a forwarded change never
applies by itself, whatever the calendar.update rule says: it always waits for the user's yes, and
the card says who forwarded it and whom it claims to come from ("Forwarded by you from <address>;
claims to be from <organizer address>"). A forged or unverified forward is ordinary mail and
changes nothing.

- `vouched`: the trusted address when the message passes the trust rule.
- `handle` (from calendar/inbound.handle): the invitation in the forward or in an attached email
  is matched to the user's event by its UID. The usual rules apply (inbound.organizer_problem: the
  organizer of the user's copy, one event, a SEQUENCE or DTSTAMP newer than that copy). Then an approval
  card (the calendar.update class, asked always: permissions.check force_ask) with before and after;
  Deny leaves the calendar alone. Approving applies it (the user's alarms, answer when the time stays,
  colour, categories and X- properties kept), with a notification and an activity entry with
  Undo (POST /calendar/events/{uid}/revert). A cancellation marks the event cancelled, never
  deletes it; the card offers Remove from calendar with Undo. A new invitation in a forward is
  never added by itself either: the card offers to add it (POST /messages/{id}/itip/apply).
- `diff`: what changes, field by field: time, place, title, description, guests, meeting link.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from icalendar import Calendar
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import ical, itip
from mailr.core import permissions
from mailr.models import Message, User

log = logging.getLogger("mailr.calendar.forwarded")

BEFORE_KEY = "itip_before"
REMOVED_KEY = "itip_removed"
VARIANT = "forwarded_itip"
_LINK = re.compile(r"https://[^\s<>\"']*(?:teams\.microsoft\.com|teams\.live\.com|zoom\.us|meet\.google\.com|"
                   r"webex\.com|whereby\.com|gotomeeting\.com|meet\.jit\.si)[^\s<>\"']*", re.IGNORECASE)


async def vouched(db: AsyncSession, user: User, m: Message, raw: bytes) -> str | None:
    from mailr.core import trusted

    if trusted.normalize(m.from_email) not in await trusted.confirmed(db, user.id):
        return None
    v = await trusted.check(db, user, m, raw)
    return v.address if v.ok else None


def _master(cal: Calendar, comps: list[Any] | None = None) -> Any:
    comps = comps if comps is not None else list(cal.walk("VEVENT"))
    return next((c for c in comps if c.get("recurrence-id") is None), comps[0] if comps else None)


def _addrs(c: Any) -> set[str]:
    return {ical.addr(a).lower() for a in ical.as_list(c.get("attendee")) if ical.addr(a)}


def meeting_link(c: Any) -> str:
    """The conference link of an event: CONFERENCE, Teams' X- property, URL, or a known meeting
    host in the place or the description."""
    for k in ("conference", "x-microsoft-skypeteamsmeetingurl", "x-google-conference", "url"):
        v = c.get(k)
        if v is not None:
            vals = v if isinstance(v, list) else [v]
            for x in vals:
                if str(x).startswith("http"):
                    return str(x)
    found = _LINK.search(f"{c.get('location') or ''} {c.get('description') or ''}")
    return found.group(0) if found else ""


def _short(v: Any, n: int = 160) -> str:
    t = " ".join(str(v or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _clock(user: User) -> str:
    return "24h" if (user.settings or {}).get("time_format") == "24h" else "12h"


def diff(old_ics: str, inc: Calendar, comps: list[Any], zone: Any, method: str, clock: str = "12h") -> list[dict[str, Any]]:
    """The changes, one dict per field: {"field", "before", "after"} (and start/end for the time)."""
    from mailr.agent.when import span_text

    if method == "CANCEL":
        return [{"field": "status", "before": "", "after": "cancelled"}]
    old = Calendar.from_ical(old_ics)
    om, nm = _master(old), _master(inc, comps)
    if om is None or nm is None:
        return []
    out: list[dict[str, Any]] = []
    os_, _, _ = ical.value(om.get("dtstart"), old, zone)
    oe, _, _ = ical.value(om.get("dtend"), old, zone)
    ns, _, _ = ical.value(nm.get("dtstart"), inc, zone)
    ne, _, _ = ical.value(nm.get("dtend"), inc, zone)
    if (os_, oe) != (ns, ne):
        day = not isinstance(ns, datetime)
        out.append({"field": "when", "before": span_text(os_, oe, day, zone, fmt=clock) or "",
                    "after": span_text(ns, ne, day, zone, fmt=clock) or "",
                    "start": ns.isoformat() if ns is not None else None, "end": ne.isoformat() if ne is not None else None,
                    "old_start": os_.isoformat() if os_ is not None else None})
    for k, field in (("summary", "title"), ("location", "place")):
        if str(om.get(k) or "").strip() != str(nm.get(k) or "").strip():
            out.append({"field": field, "before": _short(om.get(k)), "after": _short(nm.get(k))})
    if " ".join(str(om.get("description") or "").split()) != " ".join(str(nm.get("description") or "").split()):
        out.append({"field": "description", "before": _short(om.get("description"), 80),
                    "after": _short(nm.get("description"), 80)})
    oa, na = _addrs(om), _addrs(nm)
    if oa != na:
        out.append({"field": "guests", "before": ", ".join(sorted(oa - na)), "after": ", ".join(sorted(na - oa))})
    ol, nl = meeting_link(om), meeting_link(nm)
    if ol != nl:
        out.append({"field": "link", "before": ol, "after": nl})
    return out


_LABEL = {"when": "When", "title": "Title", "place": "Where", "description": "Description", "guests": "Guests",
          "link": "Meeting link", "status": "Status"}


# Why a forwarded change always asks (permissions.check force_ask).
FORCE_ASK = "hard rule: a forwarded organizer change is not verified"


def claimed(org: str, org_name: str = "") -> str:
    """The organizer as the forwarded message names it: the address always, with its name."""
    name = " ".join(str(org_name or "").split())[:80]
    return f"{name} <{org}>" if name and name.lower() != org.lower() else org


def preview_of(summary: str, changes: list[dict[str, Any]], method: str, org: str, address: str,
               org_name: str = "") -> dict[str, Any]:
    """The approval card. It never says "the organizer": nothing verified who wrote the message inside
    the forward, so it says who forwarded it and whom it claims to come from, with the address."""
    lines = []
    for c in changes:
        if c["field"] == "guests":
            parts = ([f"+ {c['after']}"] if c["after"] else []) + ([f"− {c['before']}"] if c["before"] else [])
            lines.append(f"Guests: {'; '.join(parts)}")
        elif c["field"] == "status":
            lines.append("Status: cancelled")
        else:
            lines.append(f"{_LABEL[c['field']]}: {c['before'] or '—'} → {c['after'] or '—'}")
    lines.append(f"Forwarded by you from {address}; claims to be from {claimed(org, org_name)}")
    lines.append("Not verified: anyone can write a message that names this organiser")
    verb = "Mark cancelled" if method == "CANCEL" else "Update"
    return {"title": f"{verb}: “{summary[:80]}”"[:120], "lines": lines, "claimed_organizer": org,
            "forwarded_by": address, "reason": FORCE_ASK}


def sentence(user: User, summary: str, changes: list[dict[str, Any]], method: str, org: str) -> str:
    """The notification: "Ops Review moved to Wed 28 Oct, 1–3 PM (was Wed 14 Oct)"."""
    from mailr import i18n

    lang = i18n.user_language(user)
    if method == "CANCEL":
        return i18n.t("calendar_forward.cancelled", lang, title=summary, who=org)
    when = next((c for c in changes if c["field"] == "when"), None)
    if when is not None:
        was = when["before"]
        if when.get("old_start") and when.get("start") and when["old_start"][:10] != when["start"][:10]:
            was = was.split(",")[0]
        return i18n.t("calendar_forward.moved", lang, title=summary, when=when["after"], was=was)
    what = ", ".join(i18n.t(f"calendar_forward.what.{c['field']}", lang) for c in changes) or \
        i18n.t("calendar_forward.what.details", lang)
    return i18n.t("calendar_forward.changed", lang, title=summary, what=what)


# ACT invite.forwarded_update @assistant
async def handle(db: AsyncSession, user: User, m: Message, raw: bytes, address: str, *,
                 approved: bool = False, confirmed: bool = False) -> dict[str, Any]:
    """approved: the user approved the change's card; confirmed: the user chose to add a forwarded
    new invitation (POST /messages/{id}/itip/apply). Nothing else applies a forwarded change."""
    from mailr.calendar import inbound, mirror, writer
    from mailr.calendar import tz as tzmod
    from mailr.models import CalCollection

    R = inbound._result
    ics = itip.find_calendar_part(raw, forwarded=True)
    if ics is None:
        return R(False, "No calendar data", "", forwarded=True)
    inc = await inbound.parse_incoming(ics)  # size cap, off the event loop
    if inc is None:
        return R(False, "Couldn't read the calendar data", "", forwarded=True)
    method = str(inc.get("method", "") or "").upper()
    comps = list(inc.walk("VEVENT"))
    if not comps:
        return R(False, "No event in the calendar data", method, forwarded=True)
    uid = str(comps[0].get("uid", "") or "")
    if method not in ("REQUEST", "CANCEL"):
        return R(False, f"A forwarded {method or 'message'} is not applied", method, uid, forwarded=True)
    org = next((ical.addr(c.get("organizer")).lower() for c in comps if c.get("organizer") is not None), "")
    if not org:
        return R(False, "The invitation names no organiser", method, uid, forwarded=True)
    me = await mirror.my_addresses(db, user.id)
    user_tz = await tzmod.user_zone(db, user)
    obj = await writer.find(db, user.id, uid, "event") if uid else None
    if obj is None:
        if method == "REQUEST":
            # Never added by itself: the organizer inside the forward is not verified.
            return {**await inbound._maybe_add(db, user, inc, org, me, uid, verified=confirmed or approved),
                    "forwarded": True, "claimed_organizer": org}
        return R(False, "Not in your calendar", method, uid, forwarded=True)
    problem = inbound.organizer_problem(obj, inc, comps, org, me, user_tz, method, uid)
    if problem is not None:
        return {**problem, "forwarded": True}
    changes = diff(obj.ics, inc, comps, user_tz, method, _clock(user))
    summary = str(_master(inc, comps).get("summary") or obj.summary or "")
    org_name = next((ical.cn(c.get("organizer")) for c in comps if c.get("organizer") is not None), "")
    if not approved:
        # Always a card (force_ask): an Allow rule for calendar.update never applies a change nobody
        # verified. Deny still wins.
        coll = await db.get(CalCollection, obj.collection_id)
        decision = await permissions.check(
            db, permissions.Actor(user=user, kind="assistant"), "calendar.update", target=uid,
            ctx={"calendar": coll.href if coll is not None else None},
            payload={"message_id": m.id, "uid": uid, "_variant": VARIANT},
            preview=preview_of(summary, changes, method, org, address, org_name), force_ask=FORCE_ASK)
        if isinstance(decision, permissions.Denied):
            from mailr.knowledge import profile

            name = await profile.name_of(db, user.id)
            return R(False, f"Calendar changes by {name} are off; your calendar was not changed", method, uid,
                     forwarded=True, changes=changes, claimed_organizer=org)
        if isinstance(decision, permissions.Pending):
            from mailr.core import publicid

            return R(False, "Waiting for your approval", method, uid, forwarded=True, changes=changes,
                     claimed_organizer=org, approval_id=publicid.encode("approval", decision.action.id))
        return R(False, "Waiting for your approval", method, uid, forwarded=True, changes=changes,
                 claimed_organizer=org)  # never reached: a forced ask is never allowed
    before = obj.ics
    out = await inbound._from_organizer(db, user, obj, inc, comps, org, me, user_tz, method, uid, verified=True,
                                        keep_own=True)
    out = {**out, "forwarded": True, "changes": changes, "claimed_organizer": org}
    if out.get("applied"):
        m.upstream = {**(m.upstream or {}), BEFORE_KEY: {"uid": uid, "ics": before[:200_000]}}
        await _record(db, user, m, uid, summary, changes, method, org_name or org)
    return out


async def _record(db: AsyncSession, user: User, m: Message, uid: str, summary: str, changes: list[dict[str, Any]],
                  method: str, org: str) -> None:
    """The notification and the activity entry (with Undo) of an applied forwarded change."""
    from mailr import i18n
    from mailr.assistant import notify
    from mailr.core import activity
    from mailr.knowledge import profile

    key = "cancelled" if method == "CANCEL" else "moved" if any(c["field"] == "when" for c in changes) else "changed"
    token = activity.VIA.set({"kind": "conversation", "id": m.conversation_id, "title": (m.subject or "")[:200]}) \
        if m.conversation_id else None
    try:
        act = activity.entry("calendar", i18n.msg(f"activity.calendar.{key}", what=activity.quoted(summary)),
                             undo=activity.http("POST", f"/calendar/events/{uid}/revert", {"message_id": m.id}),
                             kind="event", ref=uid, title=summary)
    finally:
        if token is not None:
            activity.VIA.reset(token)
    await activity.add(db, user.id, "agent", "calendar.forwarded_update", uid, act, inputs={"message_id": m.id})
    try:
        prof = await profile.get(db, user.id)
        await notify.send(db, user, title=f"{profile.assistant_name(prof)} · {summary}"[:120],
                          body=sentence(user, summary, changes, method, org),
                          data={"conversation_id": m.conversation_id, "message_id": m.id, "event_uid": uid},
                          private=bool(m.local_only))
    except Exception as e:  # noqa: BLE001  a notification never undoes the change
        log.info("forwarded change notification failed: %s", e)


@permissions.executor(f"calendar.update:{VARIANT}")
async def _approved(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    """The user approved a forwarded organizer change: check everything again, then apply."""
    from mailr.calendar import inbound
    from mailr.core.store import user_store

    m = await db.get(Message, int(payload.get("message_id") or 0))
    if m is None or m.user_id != user.id or m.deleted:
        return {"error": "the forwarded email is gone"}
    raw = await (await user_store(db, user)).get_raw(m)
    out = await inbound.handle(db, user, m, raw, approved=True)
    m.upstream = {**(m.upstream or {}), "itip": out}
    return out


async def revert(db: AsyncSession, p: Any, m: Message, uid: str) -> dict[str, Any]:
    """Undo of an applied forwarded change: my copy as it was before it."""
    from mailr.calendar import mirror, writer
    from mailr.calendar import tz as tzmod

    rec = (m.upstream or {}).get(BEFORE_KEY)
    if not rec or rec.get("uid") != uid:
        raise LookupError("nothing to undo")
    obj = await writer.find(db, p.user_id, uid, "event", p)
    if obj is None:
        raise LookupError("the event is gone")
    old = Calendar.from_ical(rec["ics"])

    def back(cal: Calendar) -> None:
        cal.subcomponents = list(old.subcomponents)

    obj, _ = await writer.rmw(db, p.user_id, p, obj, back)
    up = {k: v for k, v in (m.upstream or {}).items() if k != BEFORE_KEY}
    if isinstance(up.get("itip"), dict):
        up["itip"] = {**up["itip"], "reverted": True}
    m.upstream = up
    await db.flush()
    return await mirror.object_out(db, obj, await tzmod.user_zone(db, p.user_id))


async def remove_cancelled(db: AsyncSession, p: Any, m: Message, uid: str) -> None:
    """Remove from calendar, for an event its organizer cancelled (the invitation card offers it
    then): the copy is kept on the message for Undo (restore_removed). Never for my own event."""
    from mailr.calendar import writer

    obj = await writer.find(db, p.user_id, uid, "event", p)
    if obj is None:
        raise LookupError("the event is not in your calendar")
    if obj.is_organizer:
        raise ValueError("you organise this event; cancel it from the calendar")
    m.upstream = {**(m.upstream or {}), REMOVED_KEY: {"uid": uid, "ics": obj.ics[:200_000],
                                                      "collection_id": obj.collection_id}}
    await writer.delete(db, p.user_id, obj)
    await db.flush()


async def restore_removed(db: AsyncSession, p: Any, m: Message) -> dict[str, Any]:
    from mailr.calendar import mirror, writer
    from mailr.calendar import tz as tzmod
    from mailr.models import CalCollection

    rec = (m.upstream or {}).get(REMOVED_KEY)
    if not rec:
        raise LookupError("nothing to undo")
    coll = await db.get(CalCollection, int(rec["collection_id"]))
    if coll is None or coll.user_id != p.user_id:
        raise LookupError("that calendar is gone")
    obj = await writer.create(db, p.user_id, coll, Calendar.from_ical(rec["ics"]), rec["uid"])
    m.upstream = {k: v for k, v in (m.upstream or {}).items() if k != REMOVED_KEY}
    await db.flush()
    return await mirror.object_out(db, obj, await tzmod.user_zone(db, p.user_id))
