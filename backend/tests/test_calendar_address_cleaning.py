# ACT-SURFACES: api
"""Calendar ORGANIZER and ATTENDEE
addresses and names lose bidi, invisible and control characters wherever they are read, and
`mailr address-clean-backfill` cleans rows stored before (idempotent).
"""

from __future__ import annotations

import uuid

from helpers.agent import _ingest
from sqlalchemy import select, update

from mailr.calendar import ical, itip
from mailr.core import address_backfill
from mailr.db import sessionmaker
from mailr.models import CalCollection, CalObject, Connector, Conversation, Message

RLO = "‮"  # noqa: PLE2502
ZW = "\u200b"


def _ics() -> bytes:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:u1\r\n"
            "DTSTAMP:20261001T000000Z\r\nDTSTART:20261102T150000Z\r\nDTEND:20261102T160000Z\r\nSUMMARY:Board\r\n"
            f"ORGANIZER;CN=C{RLO}EO:mailto:ceo@exa{RLO}mple.com{ZW}\r\n"
            f"ATTENDEE;CN=Me;PARTSTAT=NEEDS-ACTION:MAILTO:me@example.org\r\n"
            f"ATTENDEE;CN=B{RLO}ob:mailto:bob@ex⁦ample.org\r\n"  # noqa: PLE2502
            "END:VEVENT\r\nEND:VCALENDAR\r\n").encode()


def test_invite_card_addresses_and_names_are_clean():
    card = itip.summarize(_ics(), {"me@example.org"})
    assert card["organizer"] == {"email": "ceo@example.com", "name": "CEO"}
    assert [a["email"] for a in card["attendees"]] == ["me@example.org", "bob@example.org"]
    assert card["attendees"][1]["name"] == "Bob"
    assert card["my_status"] == "NEEDS-ACTION"


def test_mirror_fields_are_clean():
    from datetime import UTC

    from icalendar import Calendar

    cal = Calendar.from_ical(_ics())
    comp = next(iter(cal.walk("VEVENT")))
    f = ical.component_fields(comp, cal, UTC)
    assert f["organizer"] == "ceo@example.com" and f["organizer_name"] == "CEO"
    assert [a["email"] for a in f["attendees"]] == ["me@example.org", "bob@example.org"]
    assert f["attendees"][1]["name"] == "Bob"
    assert ical.addr("MAILTO:Ann@Example.com") == "Ann@Example.com"


async def test_backfill_cleans_old_rows_once(user):
    raw = (f"From: Old <old{uuid.uuid4().hex[:6]}@example.com>\r\nTo: me@example.com\r\nSubject: s\r\n"
           "Content-Type: text/plain\r\n\r\nhi\r\n").encode()
    mid, cid = await _ingest(user.id, raw, roles=["inbox"])
    async with sessionmaker()() as db:
        # As a row stored before address cleaning looked.
        await db.execute(update(Message).where(Message.id == mid).values(
            from_email=f"ceo@exa{RLO}mple.com", from_name=f"C{RLO}EO",
            to=[{"email": f"me{ZW}@example.com", "name": f"M{RLO}e"}], cc=[], bcc=[], reply_to=[]))
        await db.execute(update(Conversation).where(Conversation.id == cid).values(
            participants=[{"email": f"ceo@exa{RLO}mple.com", "name": "CEO\u0007"}]))
        conn = Connector(user_id=user.id, kind="caldav", name="cal", builtin=False, mode="mirror", config={},
                         state={}, enabled=True, status="idle")
        db.add(conn)
        await db.flush()
        coll = CalCollection(user_id=user.id, connector_id=conn.id, href=f"/c{conn.id}/", displayname="P",
                             components=["VEVENT"])
        db.add(coll)
        await db.flush()
        obj = CalObject(user_id=user.id, connector_id=conn.id, collection_id=coll.id, href="a.ics", uid=uuid.uuid4().hex,
                        kind="event", summary="Board", organizer=f"ceo@exa{RLO}mple.com", organizer_name=f"C{RLO}EO",
                        attendees=[{"email": "bob@ex⁦ample.org", "name": "Bob", "partstat": "ACCEPTED"}])  # noqa: PLE2502
        db.add(obj)
        await db.commit()
        oid = obj.id

    async with sessionmaker()() as db:
        dry = await address_backfill.clean_user(db, user.id, dry_run=True)
    assert dry == {"messages": 1, "conversations": 1, "calendar": 1}
    async with sessionmaker()() as db:
        assert (await db.get(Message, mid)).from_email == f"ceo@exa{RLO}mple.com"  # a dry run changes nothing
        st = await address_backfill.clean_user(db, user.id)
    assert st == dry
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        assert (m.from_email, m.from_name, m.to) == ("ceo@example.com", "CEO", [{"email": "me@example.com", "name": "Me"}])
        c = await db.get(Conversation, cid)
        assert c.participants == [{"email": "ceo@example.com", "name": "CEO"}]
        o = await db.get(CalObject, oid)
        assert (o.organizer, o.organizer_name) == ("ceo@example.com", "CEO")
        assert o.attendees == [{"email": "bob@example.org", "name": "Bob", "partstat": "ACCEPTED"}]
        again = await address_backfill.clean_user(db, user.id)
    assert again == {"messages": 0, "conversations": 0, "calendar": 0}


async def test_backfill_leaves_clean_and_unicode_rows_alone(user):
    raw = ("From: =?utf-8?q?J=C3=BCrgen?= <j@bücher.example>\r\nTo: me@example.com\r\nSubject: s\r\n"
           "Content-Type: text/plain\r\n\r\nhi\r\n").encode()
    mid, _ = await _ingest(user.id, raw, roles=["inbox"])
    async with sessionmaker()() as db:
        before = (await db.execute(select(Message.from_email, Message.from_name).where(Message.id == mid))).one()
        st = await address_backfill.clean_user(db, user.id)
        after = (await db.execute(select(Message.from_email, Message.from_name).where(Message.id == mid))).one()
    assert st["messages"] == 0 and before == after
