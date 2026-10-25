# ACT-SURFACES: api
"""Events made from mail keep the invitation's identity (calendar/from_mail.py).

- titles of events made from mail lose "Fw:", "RE:" and the like;
- an event created from an email that carries an invitation takes its UID, SEQUENCE and organizer,
  is linked to the conversation, is not made twice, and the organizer's later update reaches it;
- the invitation card recognises its copy by UID, by an EventLink from the conversation, and by the same
  times and title without the prefix (the case that prompted it: a fresh-UUID "Fw: Team Meeting"); the copy is
  "In your calendar" and never a conflict with itself;
- invite.link_existing: that copy takes the invitation's identity in place, and its Undo restores it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.agent import _ingest
from helpers.calendar import _connect
from sqlalchemy import select

from mailr.calendar.from_mail import clean_title
from mailr.db import sessionmaker
from mailr.models import CalObject, EventLink, Message, User

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")

ORG = "chair@acme.example"


def test_clean_title_strips_forward_and_reply_prefixes():
    assert clean_title("Fw: Ops Review") == "Ops Review"
    assert clean_title("FW: RE: Fwd: Ops Review") == "Ops Review"
    assert clean_title("WG: AW: Projekt") == "Projekt"
    assert clean_title("TR: Réunion") == "Réunion"
    assert clean_title("RE[2]: Budget") == "Budget"
    assert clean_title("转发: 会议") == "会议"
    assert clean_title("Fw:") == "Fw:"  # nothing left: the title stays
    assert clean_title("Fwd budget review") == "Fwd budget review"  # no colon, no prefix


def _ics(uid: str, me: str, start: datetime, *, seq: int = 0, summary: str = "Ops Review") -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nSEQUENCE:{seq}\r\nDTSTAMP:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}\r\n"
            f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}\r\nDTEND:{(start + timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}\r\n"
            f"SUMMARY:{summary}\r\nORGANIZER;CN=Chair:mailto:{ORG}\r\n"
            f"ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{me}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


def _mail(uid: str, me: str, start: datetime, *, subject: str = "Fw: Ops Review", seq: int = 0) -> bytes:
    return (f"Authentication-Results: mx.example; dkim=pass header.d=acme.example; dmarc=pass header.from=acme.example\r\n"
            f"From: Chair <{ORG}>\r\nTo: {me}\r\nSubject: {subject}\r\n"
            f"Message-ID: <{uuid.uuid4().hex}@acme.example>\r\nDate: {datetime.now(UTC).strftime('%a, %d %b %Y %H:%M:%S +0000')}\r\n"
            "MIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary=\"b1\"\r\n\r\n"
            "--b1\r\nContent-Type: text/plain\r\n\r\nThe Ops meets.\r\n"
            "--b1\r\nContent-Type: text/calendar; charset=utf-8; method=REQUEST\r\n\r\n"
            + _ics(uid, me, start, seq=seq) + "--b1--\r\n").encode()


async def _setup(client, user):
    await _connect(client)
    async with sessionmaker()() as db:
        me = (await db.get(User, user.id)).email
    start = (datetime.now(UTC) + timedelta(days=5)).replace(hour=18, minute=0, second=0, microsecond=0)
    uid = f"ops-{uuid.uuid4().hex[:10]}"
    mid, cid = await _ingest(user.id, _mail(uid, me, start))
    return me, start, uid, mid, cid


async def _event(client, start: datetime, summary: str, **kw) -> dict:
    r = await client.post("/api/v1/calendar/events", json={"summary": summary, "start": start.isoformat(),
                                                           "end": (start + timedelta(hours=1)).isoformat(), **kw})
    assert r.status_code == 200, r.text
    return r.json()


async def test_the_owners_case_a_fresh_uuid_fw_copy_is_in_the_calendar_not_a_conflict(client, user):
    """The case: an event "Fw: Ops Review" with its own UUID at the invitation's times."""
    me, start, uid, mid, _ = await _setup(client, user)
    mine = await _event(client, start, "Fw: Ops Review")
    assert mine["uid"] != uid
    inv = (await client.get(f"/api/v1/messages/{mid}/invite")).json()
    assert inv["in_calendar"] is True
    assert inv["calendar_match"]["uid"] == mine["uid"] and inv["calendar_match"]["by"] == "time_title"
    assert inv["conflicts"] == []
    # A real clash at the same time still shows.
    await _event(client, start, "Dentist")
    inv = (await client.get(f"/api/v1/messages/{mid}/invite")).json()
    assert [c["summary"] for c in inv["conflicts"]] == ["Dentist"]


async def test_link_adopts_the_invitations_identity_and_undo_restores(client, user):
    """invite.link_existing and its Undo."""
    me, start, uid, mid, cid = await _setup(client, user)
    mine = await _event(client, start, "Fw: Ops Review", reminders=[15])
    # A match by time and title: the user must choose the organizer, shown with its address.
    r = await client.post(f"/api/v1/messages/{mid}/invite/link", json={"uid": mine["uid"]})
    assert r.status_code == 409 and r.json()["code"] == "calendar.link_needs_confirmation"
    r = await client.post(f"/api/v1/messages/{mid}/invite/link", json={"uid": mine["uid"], "organizer": ORG.upper()})
    assert r.status_code == 200, r.text
    assert r.json()["event"]["uid"] == uid and r.json()["undo"]["path"] == f"/messages/{mid}/invite/unlink"
    inv = (await client.get(f"/api/v1/messages/{mid}/invite")).json()
    assert inv["calendar_match"]["by"] == "uid" and inv["in_calendar"] is True
    async with sessionmaker()() as db:
        objs = {o.uid: o for o in (await db.execute(select(CalObject).where(
            CalObject.user_id == user.id, CalObject.deleted_at.is_(None)))).scalars()}
        assert mine["uid"] not in objs and uid in objs
        assert f"mailto:{ORG}" in objs[uid].ics and "VALARM" in objs[uid].ics  # organizer in, reminder kept
        assert (await db.execute(select(EventLink).where(EventLink.conversation_id == cid, EventLink.uid == uid))).first()
    # Linking again is refused: it is the invitation's copy now.
    assert (await client.post(f"/api/v1/messages/{mid}/invite/link", json={"uid": uid})).status_code == 409
    r = await client.post(f"/api/v1/messages/{mid}/invite/unlink")
    assert r.status_code == 200 and r.json()["event"]["uid"] == mine["uid"]
    inv = (await client.get(f"/api/v1/messages/{mid}/invite")).json()
    assert inv["calendar_match"]["uid"] == mine["uid"] and inv["calendar_match"]["by"] == "time_title"


async def test_an_event_linked_from_the_conversation_matches(client, user):
    me, start, uid, mid, cid = await _setup(client, user)
    other = await _event(client, start + timedelta(hours=2), "Prep for the committee")
    async with sessionmaker()() as db:
        o = (await db.execute(select(CalObject).where(CalObject.uid == other["uid"]))).scalar_one()
        db.add(EventLink(user_id=user.id, conversation_id=cid, connector_id=o.connector_id, kind="event", href=o.href,
                         uid=o.uid, summary=o.summary))
        await db.commit()
    inv = (await client.get(f"/api/v1/messages/{mid}/invite")).json()
    assert inv["calendar_match"]["uid"] == other["uid"] and inv["calendar_match"]["by"] == "link"


async def test_an_event_made_from_the_email_takes_the_invitation_and_its_updates(client, user):
    me, start, uid, mid, cid = await _setup(client, user)
    ev = await _event(client, start, "Fw: Ops Review", conversation_id=cid)
    assert ev["uid"] == uid and ev["summary"] == "Ops Review"
    again = await _event(client, start, "Fw: Ops Review", conversation_id=cid)
    assert again["uid"] == uid  # not made twice
    async with sessionmaker()() as db:
        objs = (await db.execute(select(CalObject).where(CalObject.user_id == user.id, CalObject.uid == uid,
                                                         CalObject.deleted_at.is_(None)))).scalars().all()
        assert len(objs) == 1 and f"mailto:{ORG}" in objs[0].ics
        assert (await db.execute(select(EventLink).where(EventLink.conversation_id == cid, EventLink.uid == uid))).first()
    inv = (await client.get(f"/api/v1/messages/{mid}/invite")).json()
    assert inv["calendar_match"]["by"] == "uid" and inv["conflicts"] == []
    # The organizer moves it an hour later: the update reaches the event.
    later = start + timedelta(hours=1)
    mid2, _ = await _ingest(user.id, _mail(uid, me, later, subject="Updated: Ops Review", seq=1))
    from mailr.calendar import inbound
    from mailr.core.store import user_store

    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        m = await db.get(Message, mid2)
        raw = await (await user_store(db, u)).get_raw(m)
        out = await inbound.handle(db, u, m, raw)
        await db.commit()
    assert out["applied"], out
    async with sessionmaker()() as db:
        o = (await db.execute(select(CalObject).where(CalObject.uid == uid, CalObject.deleted_at.is_(None)))).scalar_one()
        assert later.strftime("%Y%m%dT%H%M%SZ") in o.ics or o.start_at == later
