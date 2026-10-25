"""Shared helpers, from test_forwarded_updates.py."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from helpers.agent import _ingest, _setup_identity
from helpers.calendar import _connect
from helpers.trusted_forwards import _addr, _forward, _mine, _trust
from mailr.calendar import inbound
from mailr.core.store import user_store
from mailr.db import sessionmaker
from mailr.models import CalObject, Message, User

ORG = "chair@work.example"


def _ics(uid: str, me: str, start: datetime, *, seq: int = 0, method: str = "REQUEST", place: str = "Room 1") -> bytes:
    stamp = (datetime.now(UTC) + timedelta(seconds=seq)).strftime("%Y%m%dT%H%M%SZ")
    status = "STATUS:CANCELLED\r\n" if method == "CANCEL" else ""
    return (f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nMETHOD:{method}\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nSEQUENCE:{seq}\r\nDTSTAMP:{stamp}\r\n{status}"
            f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}\r\nDTEND:{(start + timedelta(hours=2)).strftime('%Y%m%dT%H%M%SZ')}\r\n"
            f"SUMMARY:Ops Review\r\nLOCATION:{place}\r\nORGANIZER;CN=Chair:mailto:{ORG}\r\n"
            f"ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{me}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n").encode()


async def _handle(user, mid: int) -> dict:
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        m = await db.get(Message, mid)
        raw = await (await user_store(db, u)).get_raw(m)
        out = await inbound.handle(db, u, m, raw)
        m.upstream = {**(m.upstream or {}), "itip": out}
        await db.commit()
        return out


async def _stored(uid: str) -> str:
    async with sessionmaker()() as db:
        o = (await db.execute(select(CalObject).where(CalObject.uid == uid, CalObject.deleted_at.is_(None)))).scalar_one_or_none()
        return o.ics if o is not None else ""


async def _setup(client, user):
    await _connect(client)
    await _setup_identity(user)
    addr = _addr()
    await _trust(user, addr)
    me = await _mine(user)
    start = (datetime.now(UTC) + timedelta(days=5)).replace(hour=18, minute=0, second=0, microsecond=0)
    uid = f"ops-{uuid.uuid4().hex[:10]}"
    mid, _ = await _ingest(user.id, _forward(addr, me, note="", invite=_ics(uid, addr, start)))
    r = await client.post("/api/v1/calendar/invite-reply", json={"message_id": mid, "response": "accepted", "send": False})
    assert r.status_code == 200 and r.json()["calendar"] == "added", r.text
    # One reminder of the user's own, to check that it is kept.
    from mailr.calendar import writer

    async with sessionmaker()() as db:
        (await db.execute(select(CalObject).where(CalObject.uid == uid, CalObject.deleted_at.is_(None)))).scalar_one()
        await writer.update_event(db, user.id, uid, {"reminders": [30]})
        await db.commit()
    assert "VALARM" in await _stored(uid)
    return addr, me, start, uid
