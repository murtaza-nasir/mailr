"""SCHEDULE-AGENT=CLIENT (RFC 6638 7.1) on the copies Mailr writes to a server that schedules by
itself: Mailr sends the invitation and reply mail, so the server must not (Davis without a mail
server failed the whole write with HTTP 500, and one with a mail server sent a duplicate)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from dev_services import radicale_up
from helpers.calendar import _connect, _invite
from icalendar import Calendar

from mailr.calendar import ical
from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.store import user_store
from mailr.db import sessionmaker
from mailr.models import Connector, Identity, User

ME = "me@example.com"


def _event(organizer: str) -> Calendar:
    return Calendar.from_ical(f"""BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//t//EN\r
BEGIN:VEVENT\r
UID:x1\r
DTSTAMP:20261027T000000Z\r
DTSTART:20261030T170000Z\r
SUMMARY:Lunch\r
ORGANIZER:mailto:{organizer}\r
ATTENDEE;PARTSTAT=ACCEPTED:mailto:{ME}\r
ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:guest@example.net\r
END:VEVENT\r
END:VCALENDAR\r
""".encode())


def _params(cal: Calendar) -> dict[str, str]:
    ev = next(iter(cal.walk("VEVENT")))
    out = {"organizer": ev["organizer"].params.get("SCHEDULE-AGENT", "")}
    for a in ical.as_list(ev.get("attendee")):
        out[ical.addr(a)] = a.params.get("SCHEDULE-AGENT", "")
    return out


def test_a_guest_copy_marks_the_organizer_and_an_own_event_marks_the_guests():
    guest = _event("boss@example.org")
    ical.mark_client_scheduling(guest, {ME})
    assert _params(guest) == {"organizer": "CLIENT", ME: "", "guest@example.net": ""}
    own = _event(ME)
    ical.mark_client_scheduling(own, {ME.upper()})
    assert _params(own) == {"organizer": "", ME: "", "guest@example.net": "CLIENT"}
    plain = Calendar.from_ical(b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\nBEGIN:VEVENT\r\nUID:x2\r\n"
                               b"DTSTAMP:20261027T000000Z\r\nDTSTART:20261030T170000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    ical.mark_client_scheduling(plain, {ME})
    assert b"SCHEDULE-AGENT" not in plain.to_ical()


@pytest.mark.skipif(not radicale_up(), reason="Radicale dev server not running on :15232")
async def test_accepting_an_invitation_on_a_self_scheduling_server_marks_the_organizer(client, user, monkeypatch):
    await _connect(client)
    async with sessionmaker()() as db:
        c = Connector(user_id=user.id, kind="imap", name="out", mode="mirror", enabled=False, status="paused", config={})
        db.add(c)
        await db.flush()
        me = f"me-{c.id}@example.com"
        db.add(Identity(user_id=user.id, connector_id=c.id, email=me, name="Me", is_default=True, receives=[]))
        await db.commit()
    uid = f"inv-{uuid.uuid4().hex[:10]}"
    start = (datetime.now(UTC) + timedelta(days=3)).replace(hour=14, minute=0, second=0, microsecond=0)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        mid = (await ingest_raw(db, await user_store(db, u), _invite(uid, me, start), IngestOptions())).message.id
        await db.commit()

    from mailr.calendar import organizer

    async def yes(db, conn):
        return True

    monkeypatch.setattr(organizer, "auto_schedule", yes)
    r = await client.post("/api/v1/calendar/invite-reply", json={"message_id": mid, "response": "accepted"})
    assert r.status_code == 200 and r.json()["calendar"] == "added", r.text
    assert (await client.post(f"/api/v1/outbox/{r.json()['outbox']['id']}/cancel")).status_code == 200
    from mailr.calendar import writer

    async with sessionmaker()() as db:
        obj = await writer.find(db, user.id, uid, "event", None)
        stored = Calendar.from_ical(obj.ics)
    ev = next(iter(stored.walk("VEVENT")))
    assert ev["organizer"].params.get("SCHEDULE-AGENT") == "CLIENT"
