"""Events made from mail keep the invitation's identity.

A forwarded Outlook invitation used to become an event with a fresh UID and the title
"Fw: Team Meeting". The invitation card on that email then offered "Add to calendar" again and
warned of a clash with the event itself, and the organizer's later updates could never reach it.

- `clean_title`: forward and reply prefixes (Fw:, Fwd:, FW:, WG:, TR:, RE:, AW:, SV:, RV:, ENC:,
  and the like, also in Chinese and Russian) come off the title of an event made from mail.
- `invite_in_conversation`: the invitation an email of the conversation carries (the newest), when
  an event is created from it: the event then takes the invitation's UID, SEQUENCE, ORGANIZER and
  attendees (`adopt_identity`), so later updates and cancellations from the organizer apply to it.
  Only a verified organizer's invitation, or one the user forwarded from a trusted address, and
  only when every invitation in the conversation names the same organizer.
- `match`: the event in the calendar that IS this invitation: the same UID; or an EventLink from
  the conversation; or the same start and end with the same title after prefix-stripping. The card
  then says "In your calendar" and never counts the event as a conflict with itself.
- `link` / `unlink`: a match that is not by UID can adopt the invitation's identity in place (the
  same calendar, the user's reminders kept): the old copy is replaced by one with the invitation's
  UID, and Undo puts the old copy back. POST /messages/{id}/invite/link allows it only for a
  verified organizer (or the user's trusted forward), and for a match by time and title only after
  the user chose that organizer, shown with its address.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from icalendar import Calendar
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import EventLink, Message

# Forward and reply prefixes, any number of them ("Fw: RE: Fwd:"), with Outlook's "[2]" counters.
_PREFIX = re.compile(
    r"^\s*(?:(?:re|fw|fwd|wg|aw|tr|sv|vs|rv|enc|antw|odp|res|fs|vb|ynt|ref|doorst|回复|回覆|答复|转发|轉寄|"
    r"ответ|отв|пересл|переслать)\s*(?:\[\d+\]|\(\d+\))?\s*[:：]\s*)+",
    re.IGNORECASE)
_CANDIDATE_TYPES = ("text/calendar", "application/ics", "message/rfc822", "message/global")


def clean_title(title: str | None) -> str:
    """The title without forward and reply prefixes; the title as it was when nothing is left."""
    t = str(title or "")
    out = _PREFIX.sub("", t).strip()
    return out or t.strip()


def same_title(a: str | None, b: str | None) -> bool:
    return bool(a or b) and clean_title(a).casefold() == clean_title(b).casefold()


def _maybe_invite(m: Message) -> bool:
    return any(a.get("type") in _CANDIDATE_TYPES or str(a.get("filename") or "").lower().endswith((".ics", ".eml"))
               for a in m.attachments or [])


async def invite_in_conversation(db: AsyncSession, p: Any, conversation_id: int | None,
                                 message_id: int | None = None) -> tuple[Message, bytes, dict[str, Any]] | None:
    """(message, iCalendar, summary) of the newest invitation in the conversation (or in the one
    message), a request the user can add; None when there is none.

    An event made from the conversation takes this invitation's identity, organizer included, so
    only an invitation that is safe to adopt is returned: its
    sender is its verified organizer, or the user forwarded it from a trusted address; and every
    invitation in the conversation names the same organizer (a stranger who adds an invitation to
    someone else's conversation makes it ambiguous, and then none is adopted)."""
    from fastapi import HTTPException

    from mailr.calendar.routes import _invite_full

    if message_id:
        ids = [int(message_id)]
    elif conversation_id:
        ids = list((await db.execute(select(Message.id).where(
            Message.user_id == p.user_id, Message.conversation_id == int(conversation_id),
            Message.deleted.is_(False), Message.draft.is_(False), Message.sent_by_me.is_(False))
            .order_by(Message.date.desc().nulls_last(), Message.id.desc()).limit(10))).scalars())
    else:
        return None
    found: list[tuple[Message, bytes, dict[str, Any]]] = []
    for mid in ids:
        m = await db.get(Message, mid)
        if m is None or not _maybe_invite(m):
            continue
        try:
            m, _raw, ics, info = await _invite_full(db, p, mid)
        except HTTPException:
            continue
        if info.get("uid") and info.get("method") in ("REQUEST", "") and not info.get("cancelled"):
            found.append((m, ics, info))
    if not found:
        return None
    orgs = {str((i.get("organizer") or {}).get("email") or "").lower() for _m, _ics, i in found}
    newest = found[0]
    if len(orgs) != 1 or "" in orgs or newest[2].get("sender") not in ("organizer", "forwarded"):
        return None
    return newest


def adopt_identity(cal: Calendar, invite_ics: bytes, me: str | None) -> None:
    """Give the stored event the invitation's UID, SEQUENCE, ORGANIZER and attendees (in place).
    My attendee line keeps the status it has, else ACCEPTED."""
    from mailr.calendar import ical

    inv = Calendar.from_ical(invite_ics)
    src = next(iter(inv.walk("VEVENT")), None)
    if src is None:
        return
    for ev in cal.walk("VEVENT"):
        mine = None
        for a in (ev.get("attendee") if isinstance(ev.get("attendee"), list) else [ev.get("attendee")]):
            if a is not None and me and ical.addr(a).lower() == me.lower():
                mine = str(a.params.get("PARTSTAT", "") or "") or None
        for k in ("uid", "sequence", "organizer", "attendee"):
            while k in ev:
                del ev[k]
        ev.add("uid", str(src.get("uid")))
        ev.add("sequence", int(src.get("sequence", 0) or 0))
        if src.get("organizer") is not None:
            ev.add("organizer", src.get("organizer"), encode=0)
        atts = src.get("attendee") or []
        for a in atts if isinstance(atts, list) else [atts]:
            ev.add("attendee", a, encode=0)
        if me:
            ical.set_attendee(ev, me, mine or "ACCEPTED")


async def create_with_identity(db: AsyncSession, user_id: int, p: Any, payload: dict[str, Any], ics: bytes,
                               info: dict[str, Any]) -> dict[str, Any]:
    """The event the payload describes (title, times, place, reminders, calendar), stored with the
    invitation's identity. No guest mail: the organizer is someone else."""
    from mailr.calendar import client as cal
    from mailr.calendar import ical, mirror, writer
    from mailr.calendar import tz as tzmod

    if cal._restricted(p) and payload.get("calendar"):
        await cal._check_url_visible(db, user_id, p, payload["calendar"])
    coll = await writer.target_collection(db, user_id, p, payload.get("calendar"), "event")
    zone = await tzmod.user_zone(db, user_id)
    c = ical.build_event({**payload, "summary": clean_title(payload.get("summary"))}, zone)
    adopt_identity(c, ics, info.get("my_address"))
    obj = await writer.create(db, user_id, coll, c, info["uid"])
    return await mirror.object_out(db, obj, zone)


async def match(db: AsyncSession, user_id: int, info: dict[str, Any], m: Message, events: list[dict[str, Any]],
                span) -> tuple[dict[str, Any], str] | None:
    """(the event, how it matched: uid | link | time_title) for the invitation among `events`."""
    uid = info.get("uid") or ""
    hit = next((e for e in events if uid and e.get("uid") == uid), None)
    if hit is not None:
        return hit, "uid"
    linked = set((await db.execute(select(EventLink.uid).where(
        EventLink.user_id == user_id, EventLink.conversation_id == m.conversation_id,
        EventLink.kind == "event"))).scalars()) if m.conversation_id else set()
    hit = next((e for e in events if e.get("uid") in linked), None)
    if hit is not None:
        return hit, "link"
    if not info.get("start"):
        return None
    s, e_ = span(info["start"], info.get("end"))
    for e in events:
        if e.get("all_day") or not e.get("start"):
            continue
        es, ee = span(e["start"], e.get("end"))
        if es == s and ee == e_ and (same_title(e.get("summary"), info.get("summary"))
                                     or same_title(e.get("summary"), m.subject)):
            return e, "time_title"
    return None


LINK_KEY = "invite_link"


async def link(db: AsyncSession, p: Any, m: Message, ics: bytes, info: dict[str, Any], old_uid: str,
               me: str | None) -> dict[str, Any]:
    """Replace the event `old_uid` by a copy with the invitation's identity, in the same calendar,
    keeping its reminders. The old copy is kept on the message for Undo."""
    from mailr.calendar import mirror, writer
    from mailr.models import CalCollection

    obj = await writer.find(db, p.user_id, old_uid, "event", p)
    if obj is None:
        raise LookupError("that event is not in your calendar")
    if obj.is_organizer:
        raise ValueError("you organise that event; it cannot take another organiser's invitation")
    new_uid = info["uid"]
    if old_uid == new_uid:
        raise ValueError("the event already belongs to this invitation")
    if await writer.find(db, p.user_id, new_uid, "event", p) is not None:
        raise ValueError("the invitation is in your calendar already")
    coll = await db.get(CalCollection, obj.collection_id)
    cal = Calendar.from_ical(obj.ics)
    adopt_identity(cal, ics, me)
    new = await writer.create(db, p.user_id, coll, cal, new_uid)
    old_ics = obj.ics
    await writer.delete(db, p.user_id, obj)
    for el in (await db.execute(select(EventLink).where(EventLink.user_id == p.user_id, EventLink.uid == old_uid))).scalars():
        el.uid, el.href = new_uid, new.href
    added = None
    if m.conversation_id and not (await db.execute(select(EventLink.id).where(
            EventLink.user_id == p.user_id, EventLink.conversation_id == m.conversation_id,
            EventLink.uid == new_uid))).first():
        el = EventLink(user_id=p.user_id, conversation_id=m.conversation_id, connector_id=coll.connector_id,
                       kind="event", href=new.href, uid=new_uid, summary=clean_title(info.get("summary")))
        db.add(el)
        await db.flush()
        added = el.id
    m.upstream = {**(m.upstream or {}), LINK_KEY: {"old_uid": old_uid, "old_ics": old_ics[:200_000],
                                                   "collection_id": coll.id, "new_uid": new_uid, "added_link": added,
                                                   "at": datetime.now(UTC).isoformat()}}
    await db.flush()
    zone = await mirror_zone(db, p.user_id)
    return await mirror.object_out(db, new, zone)


async def unlink(db: AsyncSession, p: Any, m: Message) -> dict[str, Any]:
    """Undo of `link`: the old copy back (its own UID), the invitation's copy removed."""
    from mailr.calendar import mirror, writer
    from mailr.models import CalCollection

    rec = (m.upstream or {}).get(LINK_KEY)
    if not rec:
        raise LookupError("nothing to undo")
    coll = await db.get(CalCollection, int(rec["collection_id"]))
    if coll is None or coll.user_id != p.user_id:
        raise LookupError("that calendar is gone")
    new = await writer.find(db, p.user_id, rec["new_uid"], "event", p)
    restored = await writer.create(db, p.user_id, coll, Calendar.from_ical(rec["old_ics"]), rec["old_uid"])
    if new is not None:
        await writer.delete(db, p.user_id, new)
    for el in (await db.execute(select(EventLink).where(EventLink.user_id == p.user_id,
                                                        EventLink.uid == rec["new_uid"]))).scalars():
        if el.id == rec.get("added_link"):
            await db.delete(el)  # the link `link` added: gone with it
        else:
            el.uid, el.href = rec["old_uid"], restored.href
    m.upstream = {k: v for k, v in (m.upstream or {}).items() if k != LINK_KEY}
    await db.flush()
    return await mirror.object_out(db, restored, await mirror_zone(db, p.user_id))


async def mirror_zone(db: AsyncSession, user_id: int):
    from mailr.calendar import tz as tzmod

    return await tzmod.user_zone(db, user_id)
