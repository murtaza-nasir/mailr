"""Shared helpers, from test_calendar_organizer.py."""

import email
import email.policy
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from dev_services import RADICALE
from dev_services import radicale_up as _radicale_up
from sqlalchemy import select

from helpers.calendar import _connect
from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.store import user_store
from mailr.db import sessionmaker
from mailr.models import CalCollection, CalObject, Connector, Identity, Job, Message, Outbox, User

ME = "me@example.com"


DANA, EVAN = "dana@guest.example", "evan@guest.example"


radicale = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


def _cal(body: str, method: str = "") -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\n" + (f"METHOD:{method}\r\n" if method else "") + body
            + "END:VCALENDAR\r\n")


def _vevent(uid: str = "ev-1", start: str = "20261005T090000Z", end: str = "20261005T100000Z", seq: int = 0,
            guests: tuple[str, ...] = (DANA, EVAN), organizer: str = ME, extra: str = "", partstat: str = "NEEDS-ACTION",
            alarm: bool = True) -> str:
    atts = "".join(f"ATTENDEE;CN={g.split('@')[0].title()};PARTSTAT={partstat};RSVP=TRUE:mailto:{g}\r\n" for g in guests)
    al = "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:x\r\nTRIGGER:-PT10M\r\nEND:VALARM\r\n" if alarm else ""
    return (f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20260920T100000Z\r\nDTSTART:{start}\r\nDTEND:{end}\r\n"
            f"SEQUENCE:{seq}\r\nSUMMARY:Budget review\r\nLOCATION:Room 4\r\nORGANIZER;CN=Me:mailto:{organizer}\r\n"
            f"ATTENDEE;PARTSTAT=ACCEPTED:mailto:{organizer}\r\n{atts}{extra}{al}END:VEVENT\r\n")


async def _setup(client, user) -> tuple[str, str]:
    """A calendar account and a sending identity; returns (my address, the Radicale username)."""
    username = await _connect(client)
    async with sessionmaker()() as db:
        c = Connector(user_id=user.id, kind="imap", name="out", mode="mirror", enabled=False, status="paused", config={})
        db.add(c)
        await db.flush()
        me = f"me-{c.id}@example.com"
        db.add(Identity(user_id=user.id, connector_id=c.id, email=me, name="Me", is_default=True, receives=[]))
        await db.commit()
    return me, username


async def _outbox(user) -> list[Outbox]:
    async with sessionmaker()() as db:
        return list((await db.execute(select(Outbox).where(Outbox.user_id == user.id).order_by(Outbox.id))).scalars())


async def _mime(user, ob: Outbox) -> email.message.EmailMessage:
    from mailr.core.drafts import build_for_payload
    from mailr.core.sendgate import _SystemPrincipal

    async with sessionmaker()() as db:
        raw, _, _ = await build_for_payload(db, _SystemPrincipal(await db.get(User, user.id)), ob.payload)
    return email.message_from_bytes(raw, policy=email.policy.default)


def _cal_part(msg) -> tuple[str, str]:
    part = next(p for p in msg.walk() if p.get_content_type() == "text/calendar")
    return part.get_param("method"), part.get_content()


async def _stored(user, uid: str) -> CalObject:
    async with sessionmaker()() as db:
        return (await db.execute(select(CalObject).where(CalObject.user_id == user.id, CalObject.uid == uid,
                                                         CalObject.deleted_at.is_(None)))).scalar_one()


def _itip_mail(ics: str, frm: str, to: str, *, auth: str | None = None, method: str = "REPLY") -> bytes:
    if auth is None:  # a DMARC pass names its domain (core/authres.verified_for)
        auth = "dmarc=pass header.from=" + frm.rsplit("@", 1)[-1].strip(">").lower()
    msg = (f"From: {frm}\r\nTo: {to}\r\nSubject: {method.title()}: Budget review\r\n"
           f"Message-ID: <{uuid.uuid4().hex}@guest.example>\r\nDate: Thu, 01 Oct 2026 12:00:00 +0000\r\n"
           f"Authentication-Results: mx.example.com; spf=pass; dkim=pass header.d=guest.example; {auth}\r\n"
           'MIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary="b1"\r\n\r\n'
           "--b1\r\nContent-Type: text/plain\r\n\r\nSee the invitation.\r\n"
           f"--b1\r\nContent-Type: text/calendar; charset=utf-8; method={method}\r\n\r\n{ics}--b1--\r\n")
    return msg.encode()


async def _ingest(user, raw: bytes) -> int:
    """Ingest like the connectors do, then run the queued calendar.itip_incoming job."""
    from mailr.calendar.inbound import itip_incoming

    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        res = await ingest_raw(db, await user_store(db, u), raw, IngestOptions())
        await db.commit()
        mid = res.message.id
        jobs = (await db.execute(select(Job).where(Job.kind == "calendar.itip_incoming", Job.user_id == user.id,
                                                   Job.dedupe_key == f"itip:{mid}"))).scalars().all()
    assert len(jobs) == 1
    await itip_incoming({"message_id": mid}, None)
    return mid


async def _itip(user, mid: int) -> dict:
    async with sessionmaker()() as db:
        return ((await db.get(Message, mid)).upstream or {}).get("itip") or {}


def _start(days: int = 4, hour: int = 15) -> datetime:
    return (datetime.now(UTC) + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


async def _put_invitation(user, username: str, uid: str, me: str, *, seq: int = 0) -> CalObject:
    """An invitation from Alice already in my calendar, with my alarm and my answer."""
    from mailr.calendar import mirror, writer
    from mailr.calendar.dav import DavClient

    async with sessionmaker()() as db:
        await mirror.ensure_synced(db, user.id)
        coll = (await db.execute(select(CalCollection).where(CalCollection.user_id == user.id))).scalars().first()
    ics = _cal(_vevent(uid=uid, seq=seq, organizer="alice@acme.example", guests=(me,), partstat="ACCEPTED"))
    async with DavClient(RADICALE, username, "x") as d:
        await d.put(coll.href + uid + ".ics", ics, if_none_match=True)
    async with sessionmaker()() as db:
        assert await writer.find(db, user.id, uid, "event") is not None
        await db.commit()
    return await _stored(user, uid)
