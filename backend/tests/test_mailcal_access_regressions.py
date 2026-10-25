# ACT-SURFACES: api
"""Mail and calendar access: regression tests. Each one is an attack that must fail.

- POST /messages/{mid}/itip/remove deletes an event only for a CANCEL from the verified organizer
  of the stored copy, or when the copy is cancelled already; GET /invite says so (cancel_verified),
  so the card never calls a stranger's CANCEL the organizer's.
- POST /messages/{mid}/event-change needs the calendar scope.
"""

from __future__ import annotations

import uuid
from email.message import EmailMessage

import pytest
from conftest import mail_date
from helpers.agent import _ingest, _token_client

from mailr.db import sessionmaker
from mailr.models import Message


async def _tok(client, scopes, **kw):
    r = await client.post("/api/v1/tokens", json={"name": f"t-{uuid.uuid4().hex[:4]}", "scopes": scopes, **kw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _cancel(uid: str, me: str, start, *, frm: str, organizer: str, ar: str | None = None) -> bytes:
    from datetime import timedelta

    ics = (f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Evil//EN\r\nMETHOD:CANCEL\r\nBEGIN:VEVENT\r\n"
           f"UID:{uid}\r\nSEQUENCE:99\r\nDTSTAMP:20991231T000000Z\r\nSTATUS:CANCELLED\r\n"
           f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}\r\n"
           f"DTEND:{(start + timedelta(hours=2)).strftime('%Y%m%dT%H%M%SZ')}\r\n"
           f"SUMMARY:Ops Review\r\nORGANIZER;CN=X:mailto:{organizer}\r\n"
           f"ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:{me}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n").encode()
    m = EmailMessage()
    if ar:
        m["Authentication-Results"] = ar
    m["From"] = frm
    m["To"] = me
    m["Subject"] = "Cancelled: Ops Review"
    m["Message-ID"] = f"<{uuid.uuid4().hex}@x.example>"
    m["Date"] = mail_date(13)
    m.set_content("This meeting is cancelled.")
    m.add_alternative(ics, maintype="text", subtype="calendar", params={"method": "CANCEL"})
    return m.as_bytes()


async def test_m1_a_strangers_cancel_never_removes_the_event(client, user):
    from dev_services import radicale_up as _radicale_up
    from helpers.forwarded_updates import _setup, _stored

    if not _radicale_up():
        pytest.skip("Radicale dev server not running")
    _addr, me, start, uid = await _setup(client, user)
    mid, _ = await _ingest(user.id, _cancel(uid, me, start, frm="Mallory <mallory@evil.example>",
                                            organizer="mallory@evil.example"))
    inv = (await client.get(f"/api/v1/messages/{mid}/invite")).json()
    assert inv["cancelled"] and inv["in_calendar"] and inv["cancel_verified"] is False
    r = await client.post(f"/api/v1/messages/{mid}/itip/remove")
    assert r.status_code == 409 and r.json()["code"] == "calendar.cancel_not_verified", r.text
    assert "Ops Review" in await _stored(uid)


async def test_m1_an_unverified_cancel_naming_the_real_organizer_never_removes_it(client, user):
    from dev_services import radicale_up as _radicale_up
    from helpers.forwarded_updates import ORG, _setup, _stored

    if not _radicale_up():
        pytest.skip("Radicale dev server not running")
    _addr, me, start, uid = await _setup(client, user)
    # A forged From: the organizer's address, no authentication results.
    mid, _ = await _ingest(user.id, _cancel(uid, me, start, frm=f"Chair <{ORG}>", organizer=ORG))
    assert (await client.get(f"/api/v1/messages/{mid}/invite")).json()["cancel_verified"] is False
    assert (await client.post(f"/api/v1/messages/{mid}/itip/remove")).status_code == 409
    assert "Ops Review" in await _stored(uid)


async def test_m1_the_verified_organizers_cancel_still_removes_with_undo(client, user):
    from dev_services import radicale_up as _radicale_up
    from helpers.forwarded_updates import ORG, _setup, _stored

    if not _radicale_up():
        pytest.skip("Radicale dev server not running")
    _addr, me, start, uid = await _setup(client, user)
    ar = "mx.example; dkim=pass header.d=work.example; dmarc=pass header.from=work.example"
    mid, _ = await _ingest(user.id, _cancel(uid, me, start, frm=f"Chair <{ORG}>", organizer=ORG, ar=ar))
    assert (await client.get(f"/api/v1/messages/{mid}/invite")).json()["cancel_verified"] is True
    r = await client.post(f"/api/v1/messages/{mid}/itip/remove")
    assert r.status_code == 200, r.text
    assert await _stored(uid) == ""
    assert (await client.post(f"/api/v1/messages/{mid}/itip/restore")).status_code == 200


async def test_m2_event_change_needs_the_calendar_scope(client, user, raw):
    mid, _ = await _ingest(user.id, raw("Moving our call", frm="Bob <bob@x.example>", body="Can we move to 3pm?"))
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        m.upstream = {**(m.upstream or {}), "event_change": {"status": "open", "uid": "abc"}}
        await db.commit()
    async with _token_client(client, await _tok(client, [])) as a:
        r = await a.post(f"/api/v1/messages/{mid}/event-change", json={"status": "dismissed"})
        assert r.status_code == 403, r.text
    async with _token_client(client, await _tok(client, ["read"])) as a:
        assert (await a.post(f"/api/v1/messages/{mid}/event-change", json={"status": "dismissed"})).status_code == 403
    async with sessionmaker()() as db:
        assert (await db.get(Message, mid)).upstream["event_change"]["status"] == "open"
    async with _token_client(client, await _tok(client, ["read", "calendar"])) as a:
        assert (await a.post(f"/api/v1/messages/{mid}/event-change", json={"status": "dismissed"})).status_code == 200
