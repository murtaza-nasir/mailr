# ACT-SURFACES: api assistant
"""Organizer changes forwarded from a trusted address (calendar/forwarded.py; owner, 9 Oct 2026).

invite.forwarded_update: a verified forward carries the organizer's update inside it (attached or at the
top). Nobody can verify who wrote that update, so it always makes an approval card with before and after,
even when calendar.update is Allow; approving applies it with a notification
and an activity entry whose Undo reverts it. An older SEQUENCE and a forged forward change nothing.
invite.remove_cancelled: an approved forwarded cancellation marks the event cancelled, and Remove from
calendar has Undo.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.agent import _ingest
from helpers.forwarded_updates import ORG, _handle, _ics, _setup, _stored  # noqa: F401
from helpers.trusted_forwards import _forward
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import PendingAction, User

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


async def _approve(user) -> dict:
    """Approve the newest waiting forwarded change, as the card's Approve does."""
    from mailr.core import permissions

    async with sessionmaker()() as db:
        pa = (await db.execute(select(PendingAction).where(
            PendingAction.user_id == user.id, PendingAction.action == "calendar.update",
            PendingAction.status == "pending").order_by(PendingAction.id.desc()))).scalars().first()
        assert pa is not None
        res = await permissions.executor_for("calendar.update", pa.payload)(db, await db.get(User, user.id), pa.payload)
        pa.status = "approved"
        await db.commit()
        return res


async def test_ask_me_makes_a_card_and_approving_applies_with_undo(client, user):
    addr, me, start, uid = await _setup(client, user)
    later = start + timedelta(days=14)
    mid, _ = await _ingest(user.id, _forward(addr, me, note="", invite=_ics(uid, addr, later, seq=1, place="Room 9")))
    out = await _handle(user, mid)
    assert out["forwarded"] and not out["applied"] and out["detail"] == "Waiting for your approval"
    assert {c["field"] for c in out["changes"]} == {"when", "place"}
    assert later.strftime("%Y%m%dT%H%M%SZ") not in await _stored(uid)
    async with sessionmaker()() as db:
        pa = (await db.execute(select(PendingAction).where(PendingAction.user_id == user.id,
                                                           PendingAction.action == "calendar.update"))).scalar_one()
        assert pa.payload["_variant"] == "forwarded_itip"
        text = pa.preview["display"]["text"]
        assert "When:" in text and "→" in text and "Room 1 → Room 9" in text
        from mailr.core import permissions

        res = await permissions.executor_for("calendar.update", pa.payload)(db, await db.get(User, user.id), pa.payload)
        await db.commit()
    assert res["applied"], res
    ics = await _stored(uid)
    assert later.strftime("%Y%m%dT%H%M%SZ") in ics and "Room 9" in ics and "VALARM" in ics
    feed = (await client.get("/api/v1/activity")).json()["items"]
    entry = next(e for e in feed if e.get("target") and e["target"]["id"] == uid)
    assert entry["undo"]["path"] == f"/calendar/events/{uid}/revert" and entry["via"]["title"] == "Fw: Ops Review"
    r = await client.post(entry["undo"]["path"].replace("/", "/api/v1/", 1), json=entry["undo"]["body"])
    assert r.status_code == 200, r.text
    ics = await _stored(uid)
    assert start.strftime("%Y%m%dT%H%M%SZ") in ics and "Room 1" in ics


async def test_allow_still_asks_and_approving_notifies(client, user, monkeypatch):
    from mailr.assistant import notify

    sent = []

    async def fake_send(db, u, **kw):
        sent.append(kw)
        return "sent"

    monkeypatch.setattr(notify, "send", fake_send)
    addr, me, start, uid = await _setup(client, user)
    assert (await client.post("/api/v1/permissions", json={"subject": "assistant", "action": "calendar.update",
                                                           "effect": "allow", "constraints": {}})).status_code == 200
    later = start + timedelta(days=14)
    # The update at the top of the forward this time (Outlook's own forward of a meeting).
    raw = _forward(addr, me, note="", style="plain")
    from email import message_from_bytes, policy

    msg = message_from_bytes(raw, policy=policy.default)
    msg.add_alternative(_ics(uid, addr, later, seq=1), maintype="text", subtype="calendar", params={"method": "REQUEST"})
    mid, _ = await _ingest(user.id, msg.as_bytes())
    out = await _handle(user, mid)
    assert not out["applied"] and out["forwarded"] and out["detail"] == "Waiting for your approval", out
    assert later.strftime("%Y%m%dT%H%M%SZ") not in await _stored(uid) and not sent
    assert (await _approve(user))["applied"]
    assert later.strftime("%Y%m%dT%H%M%SZ") in await _stored(uid)
    assert len(sent) == 1 and sent[0]["body"].startswith("Ops Review moved to ") and "(was " in sent[0]["body"]


async def test_an_older_sequence_and_a_forged_forward_change_nothing(client, user):
    addr, me, start, uid = await _setup(client, user)
    await client.post("/api/v1/permissions", json={"subject": "assistant", "action": "calendar.update",
                                                   "effect": "allow", "constraints": {}})
    later = start + timedelta(days=14)
    newer, _ = await _ingest(user.id, _forward(addr, me, note="", invite=_ics(uid, addr, later, seq=2)))
    assert not (await _handle(user, newer))["applied"]
    assert (await _approve(user))["applied"]
    older, _ = await _ingest(user.id, _forward(addr, me, note="", invite=_ics(uid, addr, start, seq=1)))
    out = await _handle(user, older)
    assert not out["applied"] and out["detail"] == "Outdated: your copy is newer"
    forged, _ = await _ingest(user.id, _forward(addr, me, note="", ar=None,
                                                invite=_ics(uid, addr, start + timedelta(days=30), seq=3)))
    out = await _handle(user, forged)
    assert not out["applied"] and not out.get("forwarded")
    assert later.strftime("%Y%m%dT%H%M%SZ") in await _stored(uid)


async def test_a_forwarded_cancellation_marks_it_and_remove_has_undo(client, user):
    addr, me, start, uid = await _setup(client, user)
    await client.post("/api/v1/permissions", json={"subject": "assistant", "action": "calendar.update",
                                                   "effect": "allow", "constraints": {}})
    mid, _ = await _ingest(user.id, _forward(addr, me, note="", invite=_ics(uid, addr, start, seq=1, method="CANCEL")))
    out = await _handle(user, mid)
    assert not out["applied"] and out["method"] == "CANCEL"
    inv = (await client.get(f"/api/v1/messages/{mid}/invite")).json()
    assert inv["cancelled"] and inv["in_calendar"] and not inv["cancel_verified"]
    assert (await client.post(f"/api/v1/messages/{mid}/itip/remove")).status_code == 409  # not before the yes
    out = await _approve(user)
    assert out["applied"] and out["method"] == "CANCEL"
    assert "STATUS:CANCELLED" in await _stored(uid)  # marked, never deleted
    inv = (await client.get(f"/api/v1/messages/{mid}/invite")).json()
    assert inv["cancelled"] and inv["in_calendar"] and inv["cancel_verified"]
    r = await client.post(f"/api/v1/messages/{mid}/itip/remove")
    assert r.status_code == 200 and r.json()["undo"]["path"] == f"/messages/{mid}/itip/restore"
    assert await _stored(uid) == ""
    r = await client.post(f"/api/v1/messages/{mid}/itip/restore")
    assert r.status_code == 200 and "STATUS:CANCELLED" in await _stored(uid)
