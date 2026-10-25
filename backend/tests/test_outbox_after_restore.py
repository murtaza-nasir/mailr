"""A database restored from a dump taken before a send brings the send back as queued
(core/sendgate.py): its Message-ID is fixed when the row is made, the data volume notes every
Message-ID that went out, and the index holds the sent copy, so the row is marked sent and the
message never goes out twice."""

from __future__ import annotations

from helpers.agent import _setup_identity
from sqlalchemy import select

from mailr.core import sendgate
from mailr.db import session_scope, sessionmaker
from mailr.models import Identity, Outbox

API = "/api/v1"


def _smtp(monkeypatch) -> list[bytes]:
    sent: list[bytes] = []

    class Smtp:
        async def send(self, raw, mail_from, rcpts):
            sent.append(raw)
            return "250 queued"

    async def fake_connector_for(db, row):
        return Smtp()

    monkeypatch.setattr("mailr.connectors.loader.connector_for", fake_connector_for)
    return sent


async def _queue(client, user) -> int:
    async with sessionmaker()() as db:
        ident = (await db.execute(select(Identity).where(Identity.user_id == user.id))).scalars().first()
    r = await client.post(f"{API}/send", json={"payload": {
        "identity_id": ident.id, "to": [{"email": "marco@harbour.example", "name": "Marco"}],
        "subject": "Mooring fees", "text": "The fees are paid."}})
    assert r.status_code in (200, 202), r.text
    return r.json()["outbox"]["id"]


async def _restore_as_queued(oid: int) -> None:
    """The row as the dump held it: queued, never claimed."""
    async with session_scope() as db:
        ob = await db.get(Outbox, oid)
        ob.status, ob.sent_at, ob.sent_message_id, ob.claimed_at, ob.attempts = "queued", None, None, None, 0
        ob.send_at = ob.created_at


async def test_a_send_the_restore_queued_again_is_not_sent_twice(client, user, monkeypatch):
    await _setup_identity(user)
    sent = _smtp(monkeypatch)
    oid = await _queue(client, user)
    async with sessionmaker()() as db:
        mid = (await db.get(Outbox, oid)).mime_message_id
    assert mid and mid.startswith("<")  # fixed when the row is made, so a dump holds it

    await _restore_as_queued(oid)  # due now
    await sendgate.send_job({"outbox_id": oid}, None)
    assert len(sent) == 1 and mid.encode() in sent[0]
    assert sendgate._sent_marker(mid).exists()

    await _restore_as_queued(oid)
    await sendgate.send_job({"outbox_id": oid}, None)
    assert len(sent) == 1
    async with sessionmaker()() as db:
        ob = await db.get(Outbox, oid)
    assert ob.status == "sent" and ob.sent_message_id == mid

    # Another send goes out as usual.
    other = await _queue(client, user)
    await _restore_as_queued(other)
    await sendgate.send_job({"outbox_id": other}, None)
    assert len(sent) == 2


async def test_the_sent_copy_in_the_index_also_stops_a_second_send(client, user, monkeypatch):
    """The data volume was restored too (no note), but the store sync indexed the sent copy."""
    await _setup_identity(user)
    sent = _smtp(monkeypatch)
    oid = await _queue(client, user)
    await _restore_as_queued(oid)
    await sendgate.send_job({"outbox_id": oid}, None)
    await sendgate.store_sent_job({"outbox_id": oid}, None)
    async with sessionmaker()() as db:
        mid = (await db.get(Outbox, oid)).mime_message_id
    sendgate._sent_marker(mid).unlink()

    await _restore_as_queued(oid)
    await sendgate.send_job({"outbox_id": oid}, None)
    assert len(sent) == 1
    async with sessionmaker()() as db:
        assert (await db.get(Outbox, oid)).status == "sent"


async def test_a_failed_attempt_is_retried_with_the_same_message_id(client, user, monkeypatch):
    """Nothing is noted when the server refused the message: the retry sends it."""
    await _setup_identity(user)
    tries: list[bytes] = []

    class Flaky:
        async def send(self, raw, mail_from, rcpts):
            tries.append(raw)
            if len(tries) == 1:
                raise TimeoutError("timed out")
            return "250 queued"

    async def fake_connector_for(db, row):
        return Flaky()

    monkeypatch.setattr("mailr.connectors.loader.connector_for", fake_connector_for)
    oid = await _queue(client, user)
    await _restore_as_queued(oid)
    await sendgate.send_job({"outbox_id": oid}, None)
    async with session_scope() as db:
        ob = await db.get(Outbox, oid)
        assert ob.status == "queued"
        mid = ob.mime_message_id
        ob.send_at = ob.created_at
    assert not sendgate._sent_marker(mid).exists()
    await sendgate.send_job({"outbox_id": oid}, None)
    assert len(tries) == 2 and mid.encode() in tries[1]
    async with sessionmaker()() as db:
        assert (await db.get(Outbox, oid)).status == "sent"
