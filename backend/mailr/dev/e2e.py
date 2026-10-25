"""Fixtures for the browser end-to-end tests (frontend/e2e). Development only.

`mailr e2e-seed` creates a fresh user with a random password and a known mailbox (one email per
action under test, two labels, a sending identity, an email with a primary action, an email in Spam,
a brief on an email, a standing order, and two of the assistant's requests waiting for approval) and
prints them as JSON for the test run. The identity's connector is disabled: a send can only queue. When the dev Radicale runs, it also gives the user a calendar of their own
with an invitation from Dana in it; the test connects it. It refuses to run against anything but a
local server.

`mailr e2e-seed --extras` (or MAILR_E2E_EXTRAS=1) adds more, under the key `extras`, for tests of the
calendar, the tasks app and follow-ups: a conversation waiting on a reply (follow_up_at set) and
one already nudged, a second task list "E2E Errands" (a weekly task, a task with a subtask, a
task with tags), and events across the current week in the Personal calendar (a weekday
standup that repeats, a meeting I organize with guests, a tentative lunch and an all-day
conference over three days), a second calendar "E2E Work" and a read-only calendar "E2E Holidays"
(read-only through sync.DEV_READ_ONLY_MARK, since the dev Radicale grants every right), and
contacts with distinct names (Priya Natarajan, Marco Rossi, Lena Fischer, Sam Guest, Omar Haddad)
for guest suggestions. The extras also hold "E2E Images" (a newsletter with a remote image that is
already in the image proxy's cache, two tracking pixels and a wrapped link) and "E2E Images in
spam" (the same in Spam), for mail.show_images, and three Ambrose chats (Chats search and rename).
They are opt-in because tests that count rows
(Today, Waiting, calendars) expect the plain seed.

`mailr e2e-seed --serve` answers seed requests on stdin, one per line, for as long as the browser test
worker that started it runs (frontend/e2e/fixtures.ts): the same seeds without a new process each.
It also takes a message into a user's inbox (`{"ingest": …}`; one-shot: `mailr e2e-seed --ingest EMAIL`).

`mailr e2e-seed --screener` turns the sender screener on and adds three first-time senders waiting in
it (key `screener`: {"senders": {key: id}}), for the Screener specs.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import uuid
from typing import Any
from urllib.parse import urlparse

from mailr.config import get_settings

SUBJECTS = ["E2E Archive me", "E2E Delete me", "E2E Spam me", "E2E Read me", "E2E Star me", "E2E Snooze me",
            "E2E Label me", "E2E Move me", "E2E Mute me", "E2E Important me", "E2E Reply to me", "E2E Unread me"]
# A day older than SUBJECTS, so the list order of SUBJECTS (which the list tests rely on) stays the same.
OLDER = ["E2E Forward me", "E2E Brief me", "E2E Task me", "E2E Order me"]


RADICALE = "http://localhost:15232/"
TRACK = {"kind": "track", "label": "Track package", "url": "https://example.org/track/1Z999", "source": "rules",
         "fact": "Arriving today 1:30 PM - 4:30 PM"}


def _calendar(email: str) -> dict | None:
    """A Radicale calendar for this user (dev Radicale takes any password) with one invitation."""
    import datetime as dt

    import caldav
    import httpx

    try:
        httpx.get(RADICALE, timeout=2)
    except httpx.HTTPError:
        return None
    username = f"e2e{uuid.uuid4().hex[:8]}"
    cal = caldav.DAVClient(url=RADICALE, username=username, password="x").principal().make_calendar(
        name="Personal", supported_calendar_component_set=["VEVENT", "VTODO"])
    start = (dt.datetime.now(dt.UTC) + dt.timedelta(days=3)).replace(hour=16, minute=0, second=0, microsecond=0)
    f = "%Y%m%dT%H%M%SZ"
    cal.save_event("\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//mailr e2e//EN", "BEGIN:VEVENT", f"UID:{uuid.uuid4()}@e2e",
        f"DTSTAMP:{start.strftime(f)}", f"DTSTART:{start.strftime(f)}", f"DTEND:{(start + dt.timedelta(hours=1)).strftime(f)}",
        "SUMMARY:E2E Invitation", "ORGANIZER;CN=Dana Tester:mailto:dana-0@example.org",
        f"ATTENDEE;CN=E2E Tester;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{email}", "END:VEVENT", "END:VCALENDAR", ""]))
    return {"url": RADICALE, "username": username}


def _ics(*lines: str) -> str:
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//mailr e2e//EN", *lines, "END:VCALENDAR", ""])


def _calendar_extras(caldav_info: dict, email: str) -> dict:
    """The calendar half of the extras (see the module text): events in Personal, the list
    "E2E Errands" with its tasks. Returns the uids and the collection URLs (the API ids)."""
    import datetime as dt

    import caldav

    pr = caldav.DAVClient(url=RADICALE, username=caldav_info["username"], password="x").principal()
    personal = next(c for c in pr.calendars() if (c.get_display_name() or "") == "Personal")
    errands = pr.make_calendar(name="E2E Errands", supported_calendar_component_set=["VTODO"])
    work = pr.make_calendar(name="E2E Work", supported_calendar_component_set=["VEVENT"])
    holidays = pr.make_calendar(name="E2E Holidays", supported_calendar_component_set=["VEVENT"])
    from caldav.elements import cdav

    from mailr.calendar.sync import DEV_READ_ONLY_MARK

    holidays.set_properties([cdav.CalendarDescription(f"{DEV_READ_ONLY_MARK} (seeded public holidays)")])
    f, fd = "%Y%m%dT%H%M%SZ", "%Y%m%d"
    now = dt.datetime.now(dt.UTC)
    monday = (now - dt.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    stamp = f"DTSTAMP:{now.strftime(f)}"

    def at(day: int, hour: int) -> dt.datetime:
        return monday + dt.timedelta(days=day, hours=hour)

    ev: dict[str, str] = {}

    def event(key: str, *lines: str) -> None:
        uid = f"{uuid.uuid4()}@e2e"
        personal.save_event(_ics("BEGIN:VEVENT", f"UID:{uid}", stamp, *lines, "END:VEVENT"))
        ev[key] = uid

    event("E2E Standup", f"DTSTART:{at(0, 9).strftime(f)}", f"DTEND:{(at(0, 9) + dt.timedelta(minutes=15)).strftime(f)}",
          "RRULE:FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR;COUNT=10", "SUMMARY:E2E Standup")
    event("E2E Planning", f"DTSTART:{at(1, 14).strftime(f)}", f"DTEND:{at(1, 15).strftime(f)}", "SUMMARY:E2E Planning",
          "LOCATION:Room 4", f"ORGANIZER;CN=E2E Tester:mailto:{email}",
          f"ATTENDEE;CN=E2E Tester;PARTSTAT=ACCEPTED;ROLE=CHAIR:mailto:{email}",
          "ATTENDEE;CN=Dana Tester;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:dana-0@example.org",
          "ATTENDEE;CN=Sam Guest;PARTSTAT=ACCEPTED:mailto:sam@example.org")
    event("E2E Maybe lunch", f"DTSTART:{at(2, 12).strftime(f)}", f"DTEND:{at(2, 13).strftime(f)}",
          "SUMMARY:E2E Maybe lunch", "STATUS:TENTATIVE")
    event("E2E Conference", f"DTSTART;VALUE=DATE:{at(3, 0).strftime(fd)}", f"DTEND;VALUE=DATE:{at(6, 0).strftime(fd)}",
          "SUMMARY:E2E Conference", "TRANSP:TRANSPARENT")

    for cal_, key, day in ((work, "E2E Client call", 1), (holidays, "E2E Holiday", 4)):
        uid = f"{uuid.uuid4()}@e2e"
        lines = ([f"DTSTART:{at(day, 16).strftime(f)}", f"DTEND:{at(day, 17).strftime(f)}"] if cal_ is work else
                 [f"DTSTART;VALUE=DATE:{at(day, 0).strftime(fd)}", f"DTEND;VALUE=DATE:{at(day + 1, 0).strftime(fd)}",
                  "TRANSP:TRANSPARENT"])
        cal_.save_event(_ics("BEGIN:VEVENT", f"UID:{uid}", stamp, f"SUMMARY:{key}", *lines, "END:VEVENT"))
        ev[key] = uid

    tasks: dict[str, str] = {}

    def task(key: str, *lines: str) -> str:
        uid = f"{uuid.uuid4()}@e2e"
        errands.save_todo(_ics("BEGIN:VTODO", f"UID:{uid}", stamp, f"SUMMARY:{key}", "STATUS:NEEDS-ACTION", *lines,
                               "END:VTODO"))
        tasks[key] = uid
        return uid

    today = now.date()
    task("E2E Water plants", f"DUE;VALUE=DATE:{(today + dt.timedelta(days=2)).strftime(fd)}", "RRULE:FREQ=WEEKLY")
    parent = task("E2E Groceries", f"DUE;VALUE=DATE:{(today + dt.timedelta(days=1)).strftime(fd)}")
    task("E2E Buy milk", f"RELATED-TO;RELTYPE=PARENT:{parent}")
    task("E2E Dry cleaning", "CATEGORIES:home,errands", "PRIORITY:1")
    return {"calendar": str(personal.url), "task_list": {"name": "E2E Errands", "url": str(errands.url)},
            # A second calendar, and one mailr treats as read-only (no writes, a lock in the list).
            "calendars": {"E2E Work": str(work.url), "E2E Holidays": str(holidays.url)},
            "read_only_calendar": str(holidays.url),
            "events": ev, "tasks": tasks,
            # The first occurrence of the standup (the canonical occurrence string).
            "occurrences": {"E2E Standup": at(0, 9).isoformat()}}


PEOPLE = [("Priya Natarajan", "priya@example.org", "Northwind"), ("Marco Rossi", "marco@example.org", "Contoso"),
          ("Lena Fischer", "lena@example.org", "Fabrikam"), ("Sam Guest", "sam@example.org", ""),
          ("Omar Haddad", "omar@example.net", "Northwind")]


async def _people_extras(db, user) -> dict[str, str]:
    """Contacts with distinct names for guest suggestions (the seeded mail is all from "Dana Tester")."""
    import datetime as dt

    from sqlalchemy import select

    from mailr.models import Person

    now = dt.datetime.now(dt.UTC)
    for i, (name, addr, org) in enumerate(PEOPLE):
        p = (await db.execute(select(Person).where(Person.user_id == user.id, Person.email == addr))).scalar_one_or_none()
        if p is None:  # the follow-up mail may have added the address already
            p = Person(user_id=user.id, email=addr)
            db.add(p)
        p.name, p.organization, p.received_count, p.sent_count = name, org, 5 - i, 3
        p.first_seen, p.last_seen, p.last_sent = (now - dt.timedelta(days=90), now - dt.timedelta(days=i),
                                                  now - dt.timedelta(days=i + 1))
        p.strength = float(len(PEOPLE) - i)  # the order of the list above
    # One suggested group, a postal address and two details found in a signature.
    from mailr.models import PersonGroup, PersonGroupMember, PersonSuggestion

    g = PersonGroup(user_id=user.id, name="", source="suggested", state="suggested", name_hint="E2E Reading group",
                    score=6.0, evidence={"messages": 6, "last": now.isoformat(), "subjects": ["Reading group"]})
    db.add(g)
    await db.flush()
    for addr in ("priya@example.org", "marco@example.org", "lena@example.org"):
        db.add(PersonGroupMember(group_id=g.id, email=addr))
    g2 = PersonGroup(user_id=user.id, name="", source="suggested", state="suggested", name_hint="Northwind",
                     score=3.0, evidence={"messages": 3, "last": now.isoformat(), "subjects": ["Quarterly numbers"]})
    db.add(g2)
    await db.flush()
    for addr in ("omar@example.net", "sam@example.org"):
        db.add(PersonGroupMember(group_id=g2.id, email=addr))
    lena = (await db.execute(select(Person).where(Person.user_id == user.id, Person.email == "lena@example.org"))).scalar_one()
    lena.addresses = [{"text": "12 Lindenstrasse, 10115 Berlin", "source": "signature"}]
    for field, value in (("title", "Head of Research"), ("phone", "+49 30 1234 5678")):
        db.add(PersonSuggestion(user_id=user.id, email="priya@example.org", field=field, value=value, norm=value.lower(),
                                source="signature", method="rules", state="suggested"))
    await db.commit()
    return {name: addr for name, addr, _ in PEOPLE}


async def _memory_extras(db, user) -> dict[str, Any]:
    """Ambrose's memory as the Memory page shows it: notes about you,
    about a contact (Priya, linked to her People entry), about a person without mail (Jordan) and a
    topic (Northwind), two suggestions to review, and the last learning pass."""
    import datetime as dt

    from sqlalchemy import select

    from mailr.models import AssistantProfile, ChatMessage, ChatSession, Entity, Memory, Person

    def ent(kind: str, name: str, emails: list[str] | None = None, **kw: Any) -> Entity:
        e = Entity(user_id=user.id, kind=kind, name=name, aliases=[], emails=emails or [],
                   domains=kw.pop("domains", []), conversation_ids=[], summary=kw.pop("summary", ""),
                   sensitive=False, source="user", **kw)
        db.add(e)
        return e

    priya = (await db.execute(select(Person).where(Person.user_id == user.id,
                                                   Person.email == "priya@example.org"))).scalar_one()
    me = ent("person", "E2E Tester", is_self=True)
    pe = ent("person", "Priya Natarajan", ["priya@example.org"], person_id=priya.id)
    sam = ent("person", "Jordan Lee")
    nw = ent("organization", "Northwind", domains=["northwind.example"], summary="Client; quarterly reporting")
    await db.flush()

    def note(e: Entity | None, text: str, kind: str, **kw: Any) -> Memory:
        m = Memory(user_id=user.id, entity_id=e.id if e else None, text=text, kind=kind, source=kw.pop("source", "user"),
                   status=kw.pop("status", "active"), refs=kw.pop("refs", []), sensitive=False, pinned=False, use_count=0,
                   **kw)
        db.add(m)
        return m

    ids = {}
    for key, e, text, kind in (("me-1", me, "You teach on Tuesday evenings", "routine"),
                               ("me-2", me, "You prefer short replies", "preference"),
                               ("priya-1", pe, "Priya runs the reading group", "fact"),
                               ("priya-2", pe, "You promised Priya the chapter notes by Friday", "commitment"),
                               ("jordan-1", sam, "Jordan is your accountant", "fact"),
                               ("nw-1", nw, "Northwind's quarterly numbers are due in November", "fact")):
        ids[key] = note(e, text, kind)
    # The chat the suggestion came from: an earlier turn, then the one that stated it ("From a chat"
    # opens this chat at that message).
    chat = ChatSession(id=f"e2e-mem-{uuid.uuid4().hex[:10]}", user_id=user.id, title="Dinner plans", context={},
                       sensitive=False)
    db.add(chat)
    await db.flush()
    for role, text in (("user", "Find a restaurant near the office for Friday."),
                       ("assistant", "Northwind Bistro is two blocks away and has a table at 7 PM."),
                       ("user", "I'm vegetarian, so somewhere with good vegetarian dishes."),
                       ("assistant", "Green Table is close by and fully vegetarian.")):
        row = ChatMessage(session_id=chat.id, role=role, content=text, status="done" if role == "user" else None)
        db.add(row)
        await db.flush()
        if role == "user":
            stated = row.id
    ids["sugg-chat"] = note(None, "You are vegetarian", "preference", status="suggested", source="chat",
                            confidence=0.9, refs=[{"about": {"self": True}, "chat_session_id": chat.id,
                                                   "chat_message_id": stated}])
    ids["sugg-mail"] = note(None, "Priya prefers calls in the morning", "preference", status="suggested",
                            source="extracted", confidence=0.7,
                            refs=[{"about": {"name": "Priya Natarajan", "emails": ["priya@example.org"], "kind": "person"}}])
    prof = await db.get(AssistantProfile, user.id)
    if prof is None:
        prof = AssistantProfile(user_id=user.id, data={}, options={}, state={})
        db.add(prof)
    at = (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=40)).replace(microsecond=0)
    prof.state = {**(prof.state or {}), "extract_since": at.isoformat(),
                  "learn_runs": [{"at": at.isoformat(), "messages": 5, "skipped": 1, "suggested": 1, "error": None}]}
    await db.commit()
    return {"entities": {"me": me.id, "priya": pe.id, "jordan": sam.id, "northwind": nw.id},
            "memories": {k: m.id for k, m in ids.items()}, "chat": {"session_id": chat.id, "chat_message_id": stated}}


async def _chat_extras(db, user) -> dict[str, str]:
    """Two more Ambrose chats besides the memory's "Dinner plans", for Chats search and rename
    (assistant-chats-search.spec.ts): an older one about invoices and a newer one about an offsite."""
    import datetime as dt

    from mailr.models import ChatMessage, ChatSession

    now = dt.datetime.now(dt.UTC)
    out = {}
    for key, minutes, turns in (
            ("invoices", 60 * 26, (("Which invoices are still unpaid?",
                                    "Two: **Northwind Hosting** for September and the Acme print run [1](mailr-cite:1)."),)),
            ("offsite", 30, (("Plan the team offsite", "The harbour venue holds 40 people and is free on a Friday in "
                                                       "November."),
                             ("Who should I invite?", "Everyone on the design team, and Priya for the second day."))),
    ):
        at = now - dt.timedelta(minutes=minutes)
        chat = ChatSession(id=f"e2e-chat-{key}-{uuid.uuid4().hex[:8]}", user_id=user.id, title=turns[0][0], context={},
                           sensitive=False, created_at=at, updated_at=at)
        db.add(chat)
        await db.flush()
        for i, (q, a) in enumerate(turns):
            when = at - dt.timedelta(minutes=len(turns) - i)
            db.add(ChatMessage(session_id=chat.id, role="user", content=q, status="done", created_at=when))
            db.add(ChatMessage(session_id=chat.id, role="assistant", content=a, created_at=when))
        out[key] = chat.id
    await db.commit()
    return out


async def _follow_up_extras(db, store, user) -> dict[str, int]:
    """mail.follow_up: "E2E Waiting on Dana" waits for a reply (follow_up_at in two days) and
    "E2E No reply from Sam" came back nudged. Dated Sun 27 Sep, below the other seeded mail."""
    import datetime as dt

    from sqlalchemy import select

    from mailr.core import threading
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.models import Conversation, Message

    out = {}
    for subject, who in (("E2E Waiting on Dana", "dana@example.org"), ("E2E No reply from Sam", "sam@example.org")):
        mine = (f"From: E2E Tester <{user.email}>\r\nTo: {who}\r\nSubject: {subject}\r\n"
                f"Message-ID: <fu-{uuid.uuid4().hex}@e2e.example.org>\r\nDate: Sun, 27 Sep 2026 10:00:00 +0000\r\n"
                "Content-Type: text/plain\r\n\r\nCould you confirm by Tuesday?\r\n")
        res = await ingest_raw(db, store, mine.encode(), IngestOptions(roles=["sent"]))
        out[subject] = res.message.conversation_id
    now = dt.datetime.now(dt.UTC)
    sent = dt.datetime(2026, 9, 27, 10, tzinfo=dt.UTC)
    waiting = await db.get(Conversation, out["E2E Waiting on Dana"])
    waiting.follow_up_at, waiting.follow_up_since = now + dt.timedelta(days=2), sent
    nudged = await db.get(Conversation, out["E2E No reply from Sam"])
    nudged.nudged_at = now
    inbox = await store.role_label(db, "inbox")  # a nudged conversation is back in the inbox
    msgs = list((await db.execute(select(Message).where(Message.conversation_id == nudged.id))).scalars())
    await store.set_labels(msgs, add=[inbox.id], remove=[], db=db)
    await threading.refresh(db, user.id, list(out.values()))
    await db.commit()
    return out


async def _deadline_extras(db, store, user) -> dict[str, int]:
    """mail.dismiss_deadline: "E2E Due form" and "E2E Due report" from one sender, each with a
    deadline decision (Due Dec 3 and Due Dec 10, 2027). Dated Sat 26 Sep, below the other mail."""
    from mailr.core import threading
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.models import LabelDecision

    out = {}
    for subject, day in (("E2E Due form", "2027-12-03"), ("E2E Due report", "2027-12-10")):
        raw = (f"From: Rita Forms <rita@forms.e2e.example.org>\r\nTo: {user.email}\r\nSubject: {subject}\r\n"
               f"Message-ID: <due-{uuid.uuid4().hex}@e2e.example.org>\r\nDate: Sat, 26 Sep 2026 09:00:00 +0000\r\n"
               f"Content-Type: text/plain\r\n\r\nPlease send it back by {day}.\r\n")
        res = await ingest_raw(db, store, raw.encode(), IngestOptions(roles=["inbox"]))
        db.add(LabelDecision(user_id=user.id, message_id=res.message.id, kind="smart", target="deadline", value=True,
                             source="heuristic", source_ref=day, confidence=0.6, reason=f"Deadline: {day}",
                             status="applied"))
        out[subject] = res.message.conversation_id
    await db.flush()
    await threading.refresh(db, user.id, list(out.values()))
    await db.commit()
    return out


IMAGE_URL = "https://images.e2e.example.org/hero.png"


def _png(w: int, h: int) -> bytes:
    import struct
    import zlib

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + b"\x1a\x73\xe8" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


async def _image_extras(db, store, user) -> dict[str, int]:
    """mail.show_images: a newsletter whose hero image is already in the proxy cache (the reader
    shows it from mailr, with nothing fetched from the made-up host), plus two trackers and a
    Google-wrapped link; and the same message in Spam."""
    from mailr.core import imageproxy
    from mailr.core.ingest import IngestOptions, ingest_raw

    imageproxy.put(IMAGE_URL, _png(120, 40))
    html = (f'<p>This week at the shop.</p><img src="{IMAGE_URL}" alt="Hero" width="120" height="40">'
            '<img src="https://e2e.us1.list-manage.com/track/open.php?u=1&amp;id=2">'
            '<img src="https://pixel.e2e.example.net/o.gif" width="1" height="1">'
            '<p><a href="https://www.google.com/url?q=https://shop.e2e.example.org/sale">See the sale</a></p>')
    out = {}
    for subject, role in (("E2E Images", "inbox"), ("E2E Images in spam", "junk")):
        raw = (f"From: Shop News <news@shop.e2e.example.org>\r\nTo: {user.email}\r\nSubject: {subject}\r\n"
               f"Message-ID: <img-{uuid.uuid4().hex}@e2e.example.org>\r\nDate: Sat, 26 Sep 2026 10:00:00 +0000\r\n"
               f"MIME-Version: 1.0\r\nContent-Type: text/html; charset=utf-8\r\n\r\n{html}\r\n")
        res = await ingest_raw(db, store, raw.encode(), IngestOptions(roles=[role], source="import"))
        out[subject] = res.message.conversation_id
    await db.commit()
    return out


LEAK_URL = "https://evil.test/leak-forward-reply.png"


async def _hostile_extras(db, store, user) -> dict[str, int]:
    """Hostile mail for the security specs (MAILR_E2E_HOSTILE=1, with --extras): a forward bait
    with a remote image, a bidi-override display name (as ingested, and as older mail stored it
    before names were cleaned), a display name that is an address, an RTLO attachment name, and a
    message whose stylesheet tries to show its trimmed quote."""
    from sqlalchemy import update

    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.models import Conversation, Message

    rlo = "\u202e"
    rlo_name = "Bob " + rlo + "moc.lapyap@ecivres"
    hdr = ("To: {to}\r\nSubject: {subject}\r\nMessage-ID: <sec-{mid}@evil.test>\r\n"
           "Date: Sat, 26 Sep 2026 09:00:00 +0000\r\nMIME-Version: 1.0\r\n")
    html = "Content-Type: text/html; charset=utf-8\r\n\r\n"
    msgs = {
        "SEC forward leak": ("From: Eve Sender <eve@evil.test>\r\n", html +
                             "<p>Quarterly numbers attached, please forward to finance.</p>"
                             f'<img src="{LEAK_URL}" width="320" height="200" alt="chart">'),
        "SEC bidi name": ("From: =?utf-8?b?Qm9iIOKArm1vYy5sYXB5YXBAZWNpdnJlcw==?= <bob@evil.test>\r\n",
                          "Content-Type: text/plain\r\n\r\nhello"),
        "SEC bidi legacy": ("From: Bob Legacy <bob@evil.test>\r\n", "Content-Type: text/plain\r\n\r\nhello"),
        "SEC address name": ('From: "ceo@hale.example" <x@evil.test>\r\n', "Content-Type: text/plain\r\n\r\nwire it"),
        "SEC rtlo attachment": ("From: Eve Sender <eve@evil.test>\r\n",
                                ('Content-Type: multipart/mixed; boundary="b"\r\n\r\n--b\r\n'
                                "Content-Type: text/plain\r\n\r\nthe invoice\r\n--b\r\n"
                                "Content-Type: application/pdf\r\nContent-Disposition: attachment; "
                                "filename*=utf-8''invoice%E2%80%AEfdp.exe\r\nContent-Transfer-Encoding: base64\r\n\r\n"
                                "TVqQAAMAAAAEAAAA\r\n--b--")),
        "SEC quote unhide": ("From: Eve Sender <eve@evil.test>\r\n", html +
                             "<style>div{display:block!important;visibility:visible!important}"
                             "div:not(#a):not(#b){display:block!important}.mailr-quoted{display:block!important}"
                             ".x{position:fixed;top:0;left:0;width:100%;height:100%;background:red}</style>"
                             '<p class="x">Thanks for the update, see below.</p>'
                             '<div class="gmail_quote">On Fri someone wrote:<blockquote>SECRET-QUOTE-TEXT</blockquote></div>'),
    }
    out = {}
    for subject, (frm, body) in msgs.items():
        raw = frm + hdr.format(to=user.email, subject=subject, mid=uuid.uuid4().hex) + body + "\r\n"
        res = await ingest_raw(db, store, raw.encode(), IngestOptions(roles=["inbox"], source="import"))
        out[subject] = res.message.conversation_id
        if subject == "SEC bidi legacy":
            # Mail stored before names were cleaned at ingest still carries the override.
            await db.execute(update(Message).where(Message.id == res.message.id).values(from_name=rlo_name))
            conv = await db.get(Conversation, res.message.conversation_id)
            conv.participants = [{**p, "name": rlo_name} if p.get("email") == "bob@evil.test" else p
                                 for p in (conv.participants or [])]
    await db.commit()
    return out


def _local() -> bool:
    host = urlparse(get_settings().base_url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost") or host.endswith(".test")


def _raw(subject: str, to: str, i: int, day: str = "Wed, 30 Sep") -> bytes:
    h = [f"From: Dana Tester <dana-{i}@example.org>", f"To: {to}", f"Subject: {subject}",
         f"Message-ID: <{uuid.uuid4().hex}@e2e.example.org>", f"Date: {day} 2026 1{i % 10}:00:00 +0000",
         "MIME-Version: 1.0", "Content-Type: text/plain; charset=utf-8"]
    return ("\r\n".join(h) + f"\r\n\r\nBody of {subject}.\r\n").encode()


WORK_ADDRESS = "work@accounts.e2e.example"


async def _account_extras(db, store, user, home) -> dict:
    """`--accounts`: a second mail account (Work, work@accounts.e2e.example, with its own send-as
    identity) beside the e2e account (Home, the user's address). Mail arrives through each: two in
    Home's Inbox, one in Work's Inbox and one in Work's Spam (core/mailaccounts.py)."""
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.models import Connector, Identity

    work = Connector(user_id=user.id, kind="imap", name="Work", mode="mirror", enabled=False, status="paused",
                     config={"email": WORK_ADDRESS, "smtp": {"host": "smtp.accounts.e2e.example"}})
    db.add(work)
    await db.flush()
    work_ident = Identity(user_id=user.id, connector_id=work.id, email=WORK_ADDRESS, name="E2E Tester", is_default=False,
                          receives=[])
    db.add(work_ident)
    await db.commit()

    def raw(to: str, subject: str, hour: int) -> bytes:
        h = ["From: Pat Example <pat@colleagues.e2e.example>", f"To: {to}", f"Subject: {subject}",
             f"Message-ID: <{uuid.uuid4().hex}@e2e.example.org>", f"Date: Thu, 01 Oct 2026 {hour:02d}:00:00 +0000",
             "MIME-Version: 1.0", "Content-Type: text/plain; charset=utf-8"]
        return ("\r\n".join(h) + f"\r\n\r\nAbout {subject}.\r\n").encode()

    convs = {}
    for conn, to, subject, role, hour in ((home, user.email, "E2E Home letter", "inbox", 8),
                                          (home, user.email, "E2E Home bill", "inbox", 9),
                                          (work, WORK_ADDRESS, "E2E Work report", "inbox", 10),
                                          (work, WORK_ADDRESS, "E2E Work junk", "junk", 11)):
        res = await ingest_raw(db, store, raw(to, subject, hour),
                               IngestOptions(connector=conn, roles=[role], run_intel=False, notify=False))
        convs[subject] = res.message.conversation_id
    await db.commit()
    return {"home": home.id, "work": work.id, "home_address": user.email, "work_address": WORK_ADDRESS,
            "work_identity": work_ident.id, "conversations": convs}


async def _screener_extras(db, store, user, connector) -> dict:
    """`--screener`: the sender screener on, and three first-time senders waiting in it, received
    through the e2e account: Nora at brightpitch.example (a company domain, two messages), Leo at
    gmail.com (a shared provider: by address) and a newsletter (weeklyletters.example)."""
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.screener import SETTING

    user.settings = {**(user.settings or {}), SETTING: True}
    await db.commit()

    def raw(frm: str, subject: str, body: str, hour: int, extra: str = "") -> bytes:
        h = [f"From: {frm}", f"To: {user.email}", f"Subject: {subject}", f"Message-ID: <{uuid.uuid4().hex}@e2e.example.org>",
             f"Date: Thu, 01 Oct 2026 {hour:02d}:00:00 +0000", "MIME-Version: 1.0", "Content-Type: text/plain; charset=utf-8"]
        if extra:
            h.append(extra)
        return ("\r\n".join(h) + f"\r\n\r\n{body}\r\n").encode()

    mails = [
        ("Nora Pitch <nora@brightpitch.example>", "E2E Screener pitch", "We would love to show you our product.", 9, ""),
        ("Nora Pitch <nora@brightpitch.example>", "E2E Screener follow-up", "Just following up on my note.", 10, ""),
        ("Leo Martins <leo.martins@gmail.com>", "E2E Screener hello", "We met at the conference last week.", 11, ""),
        ("Weekly Letters <hello@weeklyletters.example>", "E2E Screener issue 1", "This week in letters.", 12,
         "List-Id: Weekly Letters <weekly.weeklyletters.example>"),
    ]
    for frm, subject, body, hour, extra in mails:
        await ingest_raw(db, store, raw(frm, subject, body, hour, extra),
                         IngestOptions(connector=connector, roles=["inbox"], run_intel=False, notify=False))
    await db.commit()
    from sqlalchemy import select

    from mailr.models import ScreenerSender

    rows = (await db.execute(select(ScreenerSender).where(ScreenerSender.user_id == user.id))).scalars()
    return {"senders": {r.key: r.id for r in rows}}


async def _subscription_extras(db, store, user) -> dict[str, Any]:
    """`--subscriptions`: mail for the Subscriptions and Clean up pages (frontend/e2e/subscriptions.spec.ts).
    "E2E Digest" (a list with one-click unsubscribe, three issues in the Inbox), "E2E Deals" (a
    sender with an unsubscribe address, two in the Inbox), and old archived mail from
    "E2E Old News": three plain conversations and a starred one (left alone by a cleanup)."""
    from sqlalchemy import select

    from mailr.core import threading
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.models import Conversation, Label, Message

    def raw(subject: str, frm: str, day: str, extra: str) -> bytes:
        h = [f"From: {frm}", f"To: {user.email}", f"Subject: {subject}", f"Message-ID: <{uuid.uuid4().hex}@e2e.example.org>",
             f"Date: {day} +0000", "MIME-Version: 1.0", "Content-Type: text/plain; charset=utf-8", extra]
        return ("\r\n".join(h) + f"\r\n\r\nBody of {subject}.\r\n").encode()

    digest = ("List-Id: E2E Digest <digest.e2e.example>\r\nList-Unsubscribe: <https://digest.e2e.example/u/1>\r\n"
              "List-Unsubscribe-Post: List-Unsubscribe=One-Click")
    deals = "List-Unsubscribe: <mailto:leave@deals.e2e.example>"
    convs: dict[str, int] = {}
    for i, day in enumerate(("Thu, 1 Oct 2026 08:00:00", "Thu, 24 Sep 2026 08:00:00", "Thu, 17 Sep 2026 08:00:00")):
        res = await ingest_raw(db, store, raw(f"E2E Digest issue {i + 1}", "E2E Digest <news@digest.e2e.example>", day, digest),
                               IngestOptions(roles=["inbox"]))
        convs[f"E2E Digest issue {i + 1}"] = res.message.conversation_id
    for i, day in enumerate(("Fri, 2 Oct 2026 09:00:00", "Fri, 25 Sep 2026 09:00:00")):
        res = await ingest_raw(db, store, raw(f"E2E Deals {i + 1}", "E2E Deals <offers@deals.e2e.example>", day, deals),
                               IngestOptions(roles=["inbox"]))
        convs[f"E2E Deals {i + 1}"] = res.message.conversation_id
    roles = {r: i for i, r in (await db.execute(select(Label.id, Label.role).where(
        Label.user_id == user.id, Label.role.in_(("archive", "inbox"))))).all()}
    old = []
    for i in range(4):
        res = await ingest_raw(db, store, raw(f"E2E Old News {i + 1}", "E2E Old News <bulletin@oldnews.e2e.example>",
                                              f"Mon, {10 + i} Mar 2025 07:00:00", ""), IngestOptions(roles=["inbox"]))
        convs[f"E2E Old News {i + 1}"] = res.message.conversation_id
        old.append(res.message)
    # Archived (not in the Inbox), as old mail kept for the record; the fourth is starred.
    await store.set_labels(old, add=[roles["archive"]] if roles.get("archive") else [], remove=[roles["inbox"]], db=db)
    await db.flush()
    await threading.refresh(db, user.id, [m.conversation_id for m in old])
    star = await db.get(Conversation, old[3].conversation_id)
    star.starred = True
    (await db.get(Message, old[3].id)).flagged = True
    await db.commit()
    return convs


async def seed(extras: bool = False, bulk: int = 0, subscriptions: bool = False, screener: bool = False,
               timezone: str | None = None, *, accounts: bool = False) -> dict:
    if not _local():
        raise SystemExit("e2e-seed only runs against a local development server (MAILR_BASE_URL)")
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.store import user_store
    from mailr.core.users import create_user
    from mailr.db import sessionmaker
    from mailr.models import Connector, Identity

    email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
    password = secrets.token_urlsafe(16)
    async with sessionmaker()() as db:
        user = await create_user(db, email, "E2E Tester", password=password)
        # The longest undo window: a test that sends cancels well before the worker could pick it up.
        # Advanced settings on, and a group whose access policy allows everything (core/access.py),
        # so the specs see every part of Settings and the assistant as before.
        user.settings = {**(user.settings or {}), "undo_send_seconds": 30, "advanced_settings": True}
        # The browser's zone (the test sends it; MAILR_E2E_TIMEZONE for the one-shot command). Without
        # it the server's "today" is UTC's, and from 7 pm in Chicago a task due tomorrow showed in
        # Today while the page and the test counted days in the browser's zone.
        timezone = timezone or os.environ.get("MAILR_E2E_TIMEZONE") or None
        from mailr.calendar import tz as tzmod

        if timezone and tzmod.zone(timezone) is not None:
            user.settings = {**user.settings, "timezone": timezone}
        user.assigned_groups = [await _everything_policy(db)]
        await db.commit()
        store = await user_store(db, user)
        convs = {}
        for i, subject in enumerate(SUBJECTS):
            res = await ingest_raw(db, store, _raw(subject, email, i), IngestOptions(roles=["inbox"]))
            convs[subject] = res.message.conversation_id
        for i, subject in enumerate(OLDER):
            res = await ingest_raw(db, store, _raw(subject, email, i, "Tue, 29 Sep"), IngestOptions(roles=["inbox"]))
            convs[subject] = res.message.conversation_id
        res = await ingest_raw(db, store, _raw("E2E Not spam me", email, 0, "Tue, 29 Sep"), IngestOptions(roles=["junk"]))
        convs["E2E Not spam me"] = res.message.conversation_id
        # One email whose triage found a main action (mail.primary_action).
        from mailr.core import threading
        from mailr.models import Message

        res = await ingest_raw(db, store, _raw("E2E Track me", email, 12), IngestOptions(roles=["inbox"]))
        m = await db.get(Message, res.message.id)
        m.primary_action = TRACK
        convs["E2E Track me"] = m.conversation_id
        res = await ingest_raw(db, store, _raw("E2E Code me", email, 13), IngestOptions(roles=["inbox"]))
        c = await db.get(Message, res.message.id)
        c.primary_action = {"kind": "code", "label": "Copy code", "code": "480204", "source": "rules", "fact": None}
        convs["E2E Code me"] = c.conversation_id
        await db.flush()
        await threading.refresh(db, user.id, [m.conversation_id, c.conversation_id])
        await db.commit()
        # `--bulk N`: N more inbox conversations ("E2E Bulk 0" …), older than the rest, for lists
        # longer than a page or a section (the section header's actions, web.section).
        for i in range(bulk):
            await ingest_raw(db, store, _raw(f"E2E Bulk {i}", email, i, "Mon, 28 Sep"), IngestOptions(roles=["inbox"]))
        if bulk:
            await db.commit()
        labels = {}
        for name in ("E2E Label", "E2E Other"):
            labels[name] = (await store.create_label(db, name)).id
        c = Connector(user_id=user.id, kind="imap", name="e2e", mode="mirror", enabled=False, status="paused",
                      config={"email": email})
        db.add(c)
        await db.flush()
        db.add(Identity(user_id=user.id, connector_id=c.id, email=email, name="E2E Tester", is_default=True, receives=[]))
        await db.commit()
        screened = await _screener_extras(db, store, user, c) if screener else None
        second = await _account_extras(db, store, user, c) if accounts else None
        convs.update(await _follow_ups_and_files(db, store, user))
        orders, approvals, approval_links = await _assistant(db, user, convs)
        more = await _follow_up_extras(db, store, user) if extras else None
        people = await _people_extras(db, user) if extras else None
        memory = await _memory_extras(db, user) if extras else None
        chats = await _chat_extras(db, user) if extras else None
        images = await _image_extras(db, store, user) if extras else None
        hostile = await _hostile_extras(db, store, user) \
            if extras and os.environ.get("MAILR_E2E_HOSTILE", "") in ("1", "true") else None
        if extras:
            more = {**(more or {}), **await _deadline_extras(db, store, user)}
        subs = await _subscription_extras(db, store, user) if subscriptions else None
    import asyncio

    # The calendar is made over HTTP with a blocking client: in a thread, so a seeder serving other
    # requests side by side (an ingest, `serve`) is not held up by it.
    caldav_info = await asyncio.to_thread(_calendar, email)
    out = {"email": email, "password": password, "conversations": convs, "labels": labels, "orders": orders,
           "approvals": approvals, **approval_links, "caldav": caldav_info}
    if screened is not None:
        out["screener"] = screened
    if second is not None:
        out["accounts"] = second
    if subs is not None:
        out["subscriptions"] = subs
    if extras:
        cal = await asyncio.to_thread(_calendar_extras, caldav_info, email) if caldav_info else {}
        out["extras"] = {"conversations": more, "people": people, "images": images, "hostile": hostile, "memory": memory,
                         "chats": chats, **cal}
    return out


E2E_EVERYTHING = "e2e-everything"


async def _everything_policy(db) -> str:
    """The access policy of the seeded users: every model, every switch on."""
    from sqlalchemy.dialects.postgresql import insert

    from mailr.core.access import FEATURES
    from mailr.models import AccessPolicy

    await db.execute(insert(AccessPolicy).values(
        group_name=E2E_EVERYTHING, description="Browser tests: everything",
        models={t: {"any": True, "allowed": [], "default": None} for t in ("chat", "classify")},
        features={k: True for k in FEATURES}, needs={}).on_conflict_do_nothing(index_elements=["group_name"]))
    return E2E_EVERYTHING


async def seed_access() -> dict:
    """`mailr e2e-seed --access`: an admin, and a user in a fresh group whose policy allows one local
    endpoint for chat and for sorting, with model choice off (the "family" pattern), for the
    Admin → Access and read-only Models specs. The endpoint has no server defaults, so other
    users' models do not change."""
    from mailr.core.users import create_user
    from mailr.db import sessionmaker
    from mailr.models import AccessPolicy, ModelEndpoint

    tag = uuid.uuid4().hex[:8]
    out: dict[str, Any] = {"group": f"e2e-family-{tag}", "endpoint": f"e2e-local-{tag}"}
    async with sessionmaker()() as db:
        ep = ModelEndpoint(name=out["endpoint"], base_url="http://127.0.0.1:9/v1", locality="local", adapter="openai",
                           models=["e2e-small"], defaults={}, enabled=True)
        db.add(ep)
        await db.flush()
        entry = {"endpoint_id": ep.id, "model": "e2e-small"}
        db.add(AccessPolicy(group_name=out["group"], description="Browser tests: the family pattern",
                            models={t: {"any": False, "allowed": [entry], "default": entry} for t in ("chat", "classify")},
                            features={"assistant": True, "choose_models": False, "lab": True, "api_tokens": False,
                                      "local_only_tokens": False, "hosted_models": False}, needs={}))
        for role in ("admin", "family"):
            email, password = f"e2e-{role}-{tag}@example.com", secrets.token_urlsafe(16)
            u = await create_user(db, email, f"E2E {role.title()}", password=password,
                                  role="admin" if role == "admin" else "user")
            if role == "family":
                u.assigned_groups = [out["group"]]
            out[role] = {"email": email, "password": password, "id": u.id}
        await db.commit()
    return out


async def seed_onboarding(model_url: str) -> dict:
    """`mailr e2e-seed --onboarding [--model-url URL]`: a new "family" user who has not set up yet
    (the setup guide opens by itself) with no mail account, a few messages already in the store
    (so the inbox-style step previews her own mail), and a policy whose one chat model is the
    scripted model at URL (the spec serves it). Also a mailbox on the dev upstream for the
    Connect your email step, when the upstream is running."""
    from mailr.core import onboarding
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.store import user_store
    from mailr.core.users import create_user
    from mailr.db import sessionmaker
    from mailr.models import AccessPolicy, ModelEndpoint

    tag = uuid.uuid4().hex[:8]
    out: dict[str, Any] = {"group": f"e2e-family-{tag}", "endpoint": f"e2e-chat-{tag}"}
    async with sessionmaker()() as db:
        ep = ModelEndpoint(name=out["endpoint"], base_url=model_url, locality="local", adapter="openai",
                           models=["e2e-small"], defaults={}, enabled=True)
        db.add(ep)
        await db.flush()
        entry = {"endpoint_id": ep.id, "model": "e2e-small"}
        db.add(AccessPolicy(group_name=out["group"], description="Browser tests: the family pattern",
                            models={t: {"any": False, "allowed": [entry], "default": entry} for t in ("chat", "classify")},
                            features={"assistant": True, "choose_models": False, "lab": True, "api_tokens": False,
                                      "local_only_tokens": False, "hosted_models": False}, needs={}))
        email, password = f"e2e-rosa-{tag}@example.com", secrets.token_urlsafe(16)
        u = await create_user(db, email, "Rosa Lind", password=password)
        u.assigned_groups = [out["group"]]
        onboarding.mark_new(u)
        await db.commit()
        store = await user_store(db, u)
        for i, subject in enumerate(("School trip on Friday", "Your library books are due", "Dinner on Sunday?")):
            await ingest_raw(db, store, _raw(subject, email, i), IngestOptions(roles=["inbox"]))
        await db.commit()
        out["user"] = {"email": email, "password": password, "id": u.id}
    out["upstream"] = _upstream_mailbox()
    return out


def _upstream_mailbox() -> dict | None:
    """A fresh mailbox on the dev upstream Stalwart (deploy/dev, IMAP :12143), or None when it is not
    running."""
    import httpx

    auth = ("up-svc@upstream.test", "up-svc-pass")
    using = ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"]
    try:
        q = httpx.post("http://localhost:18091/jmap/", auth=auth, timeout=10, json={
            "using": using, "methodCalls": [["x:Domain/query", {"filter": {"name": "upstream.test"}}, "q"]]})
        dom = q.json()["methodResponses"][0][1]["ids"][0]
        local, pw = f"rosa{uuid.uuid4().hex[:6]}", "Upstream-pass-123"
        r = httpx.post("http://localhost:18091/jmap/", auth=auth, timeout=10, json={"using": using, "methodCalls": [
            ["x:Account/set", {"create": {"a": {"@type": "User", "name": local, "domainId": dom,
                                                "credentials": {"0": {"@type": "Password", "secret": pw}}}}}, "c"]]})
        if "a" not in r.json()["methodResponses"][0][1].get("created", {}):
            return None
    except Exception:  # noqa: BLE001  the upstream is optional
        return None
    return {"email": f"{local}@upstream.test", "password": pw, "imap": {"host": "localhost", "port": 12143}}


async def _follow_ups_and_files(db, store, user) -> dict[str, int]:
    """mail.follow_up: "E2E Follow me" (Dana asked, I answered) and "E2E Nudged me" (the same, already
    back with no reply). The attachments view: "E2E Files" with a PDF and a spreadsheet. All dated
    Mon 28 Sep, below the other seeded mail."""
    import datetime as dt

    from mailr.core import threading
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.models import Conversation

    out = {}
    for subject in ("E2E Follow me", "E2E Nudged me"):
        ask = f"<ask-{uuid.uuid4().hex}@e2e.example.org>"
        q = (f"From: Dana Tester <dana@example.org>\r\nTo: {user.email}\r\nSubject: {subject}\r\nMessage-ID: {ask}\r\n"
             "Date: Mon, 28 Sep 2026 09:00:00 +0000\r\nContent-Type: text/plain\r\n\r\nCan you send the contract?\r\n")
        a = (f"From: E2E Tester <{user.email}>\r\nTo: dana@example.org\r\nSubject: Re: {subject}\r\n"
             f"Message-ID: <ans-{uuid.uuid4().hex}@e2e.example.org>\r\nIn-Reply-To: {ask}\r\nReferences: {ask}\r\n"
             "Date: Mon, 28 Sep 2026 10:00:00 +0000\r\nContent-Type: text/plain\r\n\r\nHere it is. Can you sign by Friday?\r\n")
        res = await ingest_raw(db, store, q.encode(), IngestOptions(roles=["inbox"]))
        await ingest_raw(db, store, a.encode(), IngestOptions(roles=["sent"]))
        out[subject] = res.message.conversation_id
    conv = await db.get(Conversation, out["E2E Nudged me"])
    conv.nudged_at = dt.datetime.now(dt.UTC)
    b = "e2eb"
    files = "".join(f"--{b}\r\nContent-Type: {t}; name=\"{n}\"\r\nContent-Disposition: attachment; filename=\"{n}\"\r\n"
                    "Content-Transfer-Encoding: base64\r\n\r\naGVsbG8=\r\n"
                    for n, t in (("e2e-contract.pdf", "application/pdf"), ("e2e-budget.xlsx", "application/vnd.ms-excel")))
    raw = (f"From: Dana Tester <dana@example.org>\r\nTo: {user.email}\r\nSubject: E2E Files\r\n"
           f"Message-ID: <files-{uuid.uuid4().hex}@e2e.example.org>\r\nDate: Mon, 28 Sep 2026 11:00:00 +0000\r\n"
           f"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"{b}\"\r\n\r\n"
           f"--{b}\r\nContent-Type: text/plain\r\n\r\nBoth files attached.\r\n{files}--{b}--\r\n")
    res = await ingest_raw(db, store, raw.encode(), IngestOptions(roles=["inbox"]))
    out["E2E Files"] = res.message.conversation_id
    await threading.refresh(db, user.id, list(out.values()))
    await db.commit()
    return out


async def _assistant(db, user, convs: dict[str, int]) -> tuple[dict[str, int], dict[str, int], dict[str, Any]]:
    """A brief on "E2E Brief me", a standing order that stars "E2E Order me", and two standing
    orders the assistant asked to create, waiting for the user's approval."""
    import datetime as dt

    from sqlalchemy import select

    from mailr.agent.tools import ToolCtx, run_tool
    from mailr.assistant import orders as ord_
    from mailr.core import publicid
    from mailr.models import AssistantRun, Message

    cid = convs["E2E Brief me"]
    mid = (await db.execute(select(Message.id).where(Message.conversation_id == cid))).scalars().first()
    db.add(AssistantRun(user_id=user.id, trigger="message", slot=f"m{mid}", message_id=mid, conversation_id=cid,
                        status="done", output="E2E brief: the invoice is due on Friday.", delivered=["brief"],
                        finished_at=dt.datetime.now(dt.UTC)))
    order = await ord_.apply_order_op(db, user, {"op": "create", "source": "user", "order": {
        "name": "E2E Order", "query": 'subject:"E2E Order me"', "actions": {"star": True}}})
    await db.commit()
    approvals: dict[str, int] = {}
    links: dict[str, dict[str, Any]] = {"approval_links": {}, "approval_legacy_ids": {}}
    for name in ("E2E Approve me", "E2E Deny me"):
        ctx = ToolCtx(db=db, p=ord_.principal_for(user), assistant=True, chat_session_id=f"e2e-{uuid.uuid4().hex[:6]}")
        res = await run_tool(ctx, "manage_order", {"action": "create", "name": name, "question": f"Is this {name}?",
                                                   "star": True})
        # The id the web app and the API show (public with MAILR_PUBLIC_IDS, core/publicid.py).
        approvals[name] = publicid.encode("approval", res.data["approval_id"])
        # The link agents and notifications hand out (its path), and the id old links named.
        links["approval_links"][name] = "/approvals/" + res.data["approval_url"].rsplit("/approvals/", 1)[1]
        links["approval_legacy_ids"][name] = res.data["approval_id"]
    return {"E2E Order": order["id"]}, approvals, links


SERVE_MARK = "@@seed "


async def ingest_for(email: str, raw: str, roles: list[str] | None = None) -> dict:
    """One raw message into the store of the user `email` (an e2e user), as the ingest job takes it in:
    `{"message_id", "conversation_id"}`. For specs that add mail in the middle of a test (an
    invitation reply, an attached email); they used to start Python for every message."""
    from sqlalchemy import select

    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.store import user_store
    from mailr.db import sessionmaker
    from mailr.models import User

    if not _local():
        raise SystemExit("e2e-seed only runs against a local development server (MAILR_BASE_URL)")
    async with sessionmaker()() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        store = await user_store(db, user)
        res = await ingest_raw(db, store, raw.encode(), IngestOptions(roles=roles or ["inbox"]))
        await db.commit()
        return {"message_id": res.message.id, "conversation_id": res.message.conversation_id}


def serve() -> None:
    """`mailr e2e-seed --serve`: one long-lived seeder for a Playwright worker. Each line on stdin is
    a request: a seed (`{"extras": bool, "bulk": int, "subscriptions": bool, "screener": bool}`, or
    `{"access": true}`, each with `"env"`: the worker's MAILR_E2E_* variables at that moment), or
    `{"ingest": {"email", "raw"}}` (ingest_for). A request may carry an `"id"`; its answer then
    carries the same id. Each answer is one line on stdout, `@@seed ` and then `{"ok": …}` or
    `{"error": text}`. The same seeds as the one-shot command, without starting Python, importing
    mailr and connecting to the database again for every test (about 3 s each). Anything else the
    seed prints goes to stderr, so it cannot be mistaken for an answer.

    Seeds run one at a time (each sets the MAILR_E2E_* variables it was sent). An ingest runs beside
    them, so a spec's ingest does not wait for the user the worker seeds ahead for its next test
    (frontend/e2e/fixtures.ts)."""
    import asyncio
    import traceback

    if not _local():
        raise SystemExit("e2e-seed only runs against a local development server (MAILR_BASE_URL)")
    out = sys.stdout
    sys.stdout = sys.stderr
    # The worker's users in the dev Radicale go when the worker is done (stdin closed): every e2e
    # user used to stay there for good (mailr/dev/radicale.py). MAILR_E2E_KEEP_RADICALE=1 keeps them.
    from mailr.dev import radicale

    stop = radicale.track()

    def answer(req_id: Any, reply: dict) -> None:
        if req_id is not None:
            reply["id"] = req_id
        out.write(SERVE_MARK + json.dumps(reply) + "\n")
        out.flush()

    async def one(req: dict, seeds: asyncio.Lock) -> None:
        try:
            if req.get("ingest"):
                ing = req["ingest"]
                res: dict = await ingest_for(ing["email"], ing["raw"], ing.get("roles"))
            else:
                async with seeds:
                    # The worker's environment at the time of the request (a spec can set it in beforeAll).
                    for k, v in (req.get("env") or {}).items():
                        if k.startswith("MAILR_E2E_"):
                            if v is None:
                                os.environ.pop(k, None)
                            else:
                                os.environ[k] = str(v)
                    if req.get("access"):
                        res = await seed_access()
                    else:
                        res = await seed(bool(req.get("extras")) or os.environ.get("MAILR_E2E_EXTRAS", "") in ("1", "true"),
                                         int(req.get("bulk") or 0), bool(req.get("subscriptions")),
                                         bool(req.get("screener")), req.get("timezone"),
                                         accounts=bool(req.get("accounts")))
            reply = {"ok": res}
        except Exception as e:  # noqa: BLE001  the test reports it; the seeder keeps serving
            traceback.print_exc()
            reply = {"error": f"{type(e).__name__}: {e}"}
        answer(req.get("id"), reply)

    async def loop() -> None:
        seeds = asyncio.Lock()
        running: set[asyncio.Task] = set()
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            if not line.strip():
                continue
            try:
                req = json.loads(line)
            except ValueError as e:
                answer(None, {"error": f"bad request: {e}"})
                continue
            t = asyncio.create_task(one(req, seeds))
            running.add(t)
            t.add_done_callback(running.discard)
        if running:
            await asyncio.gather(*running, return_exceptions=True)

    try:
        asyncio.run(loop())
    finally:
        sys.stdout = out
        made = stop()
        if made and os.environ.get("MAILR_E2E_KEEP_RADICALE", "") not in ("1", "true"):
            try:
                print(f"e2e-seed: deleted {radicale.delete_users(made)} dev Radicale users", file=sys.stderr)
            except Exception as e:  # noqa: BLE001  cleanup never fails the run
                print(f"e2e-seed: could not delete dev Radicale users: {e}", file=sys.stderr)


def main() -> None:
    import asyncio

    args = sys.argv[1:]
    if "--serve" in args:
        serve()
        return
    if "--ingest" in args[:-1]:
        # `mailr e2e-seed --ingest EMAIL < message.eml`: one message into that user's inbox (ingest_for).
        print(json.dumps(asyncio.run(ingest_for(args[args.index("--ingest") + 1], sys.stdin.read()))))
        return
    extras = "--extras" in args or os.environ.get("MAILR_E2E_EXTRAS", "") in ("1", "true")
    bulk = int(args[args.index("--bulk") + 1]) if "--bulk" in args[:-1] else 0
    if "--access" in args:
        if not _local():
            raise SystemExit("e2e-seed only runs against a local development server (MAILR_BASE_URL)")
        print(json.dumps(asyncio.run(seed_access())))
        return
    if "--onboarding" in args:
        if not _local():
            raise SystemExit("e2e-seed only runs against a local development server (MAILR_BASE_URL)")
        url = args[args.index("--model-url") + 1] if "--model-url" in args[:-1] else "http://127.0.0.1:9/v1"
        print(json.dumps(asyncio.run(seed_onboarding(url))))
        return
    print(json.dumps(asyncio.run(seed(extras, bulk, "--subscriptions" in args, "--screener" in args,
                                      accounts="--accounts" in args))))
