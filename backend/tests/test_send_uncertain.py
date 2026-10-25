"""A send whose connection dropped after the message was handed over (core/sendgate.py): the row
becomes `uncertain` and is never resent on its own. outbox.verify_sent counts it as sent when its
Message-ID turns up (the store, or the Sent folder of a provider that keeps its own copy), and
otherwise leaves the decision to the user, who can Retry or Discard it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from helpers.agent import _setup_identity
from sqlalchemy import select, text

from mailr.connectors.imap import SendUncertainError
from mailr.core import sendgate
from mailr.db import session_scope, sessionmaker
from mailr.models import Connector, Identity, Job, Message, Outbox

API = "/api/v1"


async def _outbox(user) -> int:
    async with session_scope() as db:
        ident = (await db.execute(select(Identity).where(Identity.user_id == user.id))).scalars().first()
        ob = Outbox(user_id=user.id, identity_id=ident.id, payload={
            "identity_id": ident.id, "to": [{"email": "lena.hart@example.org", "name": "Lena Hart"}],
            "subject": "Quarterly figures", "text": "Attached as promised."},
            recipients=["lena.hart@example.org"], send_at=datetime.now(UTC) - timedelta(seconds=1))
        db.add(ob)
        await db.flush()
        return ob.id


async def _row(oid: int) -> Outbox:
    async with sessionmaker()() as db:
        return await db.get(Outbox, oid)


async def _jobs(user, kind: str) -> list[dict]:
    async with sessionmaker()() as db:
        return [j.payload for j in (await db.execute(select(Job).where(
            Job.user_id == user.id, Job.kind == kind, Job.status == "queued").order_by(Job.id))).scalars()]


def _dropping(monkeypatch, calls: list[int]) -> None:
    class Dropping:
        async def send(self, raw, mail_from, rcpts):
            calls.append(1)
            raise SendUncertainError("the connection ended after the message was handed over")

    async def fake_connector_for(db, row):
        return Dropping()

    monkeypatch.setattr("mailr.connectors.loader.connector_for", fake_connector_for)


async def test_an_uncertain_send_is_not_retried(app, user, monkeypatch):
    await _setup_identity(user)
    oid = await _outbox(user)
    calls: list[int] = []
    _dropping(monkeypatch, calls)
    await sendgate.send_job({"outbox_id": oid}, None)
    ob = await _row(oid)
    assert ob.status == "uncertain" and "may have been sent" in ob.error
    assert await _jobs(user, "outbox.send") == []  # no blind resend
    assert [p["outbox_id"] for p in await _jobs(user, "outbox.verify_sent")] == [oid]
    # The job running again (a worker restart) does not send either: the row is no longer queued.
    await sendgate.send_job({"outbox_id": oid}, None)
    assert calls == [1]


async def test_a_copy_in_the_store_settles_it_as_sent(app, user, monkeypatch):
    await _setup_identity(user)
    oid = await _outbox(user)
    _dropping(monkeypatch, [])
    await sendgate.send_job({"outbox_id": oid}, None)
    ob = await _row(oid)
    # The provider's own sent copy arrived through sync.
    async with session_scope() as db:
        db.add(Message(user_id=user.id, message_id_hdr=ob.mime_message_id, subject="Quarterly figures",
                       date=datetime.now(UTC)))
    await sendgate.verify_sent_job({"outbox_id": oid, "parent_id": None}, None)
    ob = await _row(oid)
    assert ob.status == "sent" and ob.sent_message_id == ob.mime_message_id and ob.error == ""
    assert [p["outbox_id"] for p in await _jobs(user, "outbox.store_sent")] == [oid]


async def test_without_a_way_to_check_the_user_decides(app, client, user, monkeypatch):
    await _setup_identity(user)
    oid = await _outbox(user)
    _dropping(monkeypatch, [])
    await sendgate.send_job({"outbox_id": oid}, None)
    # This account's provider keeps no copy of what is sent: nothing can show it went out.
    await sendgate.verify_sent_job({"outbox_id": oid, "parent_id": None}, None)
    ob = await _row(oid)
    assert ob.status == "uncertain"
    assert [p["outbox_id"] for p in await _jobs(user, "push.send_failed")] == [oid]
    assert await _jobs(user, "outbox.send") == []
    rows = {o["id"]: o for o in (await client.get(f"{API}/outbox", params={"active": "true"})).json()}
    assert rows[oid]["status"] == "uncertain"
    # The user decides it did not leave: Retry queues it again, with the same Message-ID.
    r = await client.post(f"{API}/outbox/{oid}/retry")
    assert r.status_code == 200, r.text
    again = await _row(oid)
    assert again.status == "queued" and again.mime_message_id == ob.mime_message_id


async def test_a_provider_that_keeps_sent_mail_is_asked_again_later(app, user, monkeypatch):
    _cid, _ = await _setup_identity(user)
    async with session_scope() as db:
        await db.execute(text("UPDATE connectors SET config = :c WHERE user_id = :u"),
                         {"c": '{"imap": {"host": "imap.gmail.com"}}', "u": user.id})
    oid = await _outbox(user)
    _dropping(monkeypatch, [])
    await sendgate.send_job({"outbox_id": oid}, None)
    looked: list[str] = []

    class Gmail:
        async def sent_folder(self):
            return "[Gmail]/Sent Mail"

        async def find_message_id(self, folder, msgid, size):
            looked.append(folder)
            return 7, (42 if len(looked) > 1 else None)

        async def _close(self):
            return None

    async def fake_connector_for(db, row):
        assert isinstance(row, Connector)
        return Gmail()

    monkeypatch.setattr("mailr.connectors.loader.connector_for", fake_connector_for)
    await sendgate.verify_sent_job({"outbox_id": oid, "parent_id": None}, None)
    assert (await _row(oid)).status == "uncertain"
    nxt = await _jobs(user, "outbox.verify_sent")
    assert [p.get("n") for p in nxt] == [None, 1]
    await sendgate.verify_sent_job(nxt[-1], None)
    assert (await _row(oid)).status == "sent"
    assert looked == ["[Gmail]/Sent Mail", "[Gmail]/Sent Mail"]
