"""Double-send windows: the stuck-send sweep keys on the
claim time, not on send_at, and a filter's forward queues one Outbox row however often its job runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from helpers.agent import _setup_identity
from sqlalchemy import select, text

from mailr.core import sendgate
from mailr.db import session_scope, sessionmaker
from mailr.models import Identity, Message, Outbox, User


async def _outbox(user, send_at: datetime, status: str = "queued") -> int:
    async with session_scope() as db:
        ident = (await db.execute(select(Identity).where(Identity.user_id == user.id))).scalars().first()
        ob = Outbox(user_id=user.id, identity_id=ident.id, payload={
            "identity_id": ident.id, "to": [{"email": "bob@example.org", "name": "Bob"}],
            "subject": "Late", "text": "Hello"}, recipients=["bob@example.org"], send_at=send_at, status=status)
        db.add(ob)
        await db.flush()
        return ob.id


async def _status(oid: int) -> str:
    async with sessionmaker()() as db:
        return (await db.get(Outbox, oid)).status


async def test_a_send_claimed_late_is_not_swept_while_in_flight(app, user, monkeypatch):
    """Due an hour ago (a backlog, a worker restart), claimed now: the sweep that runs while the
    SMTP conversation is still going must leave it alone."""
    await _setup_identity(user)
    oid = await _outbox(user, datetime.now(UTC) - timedelta(hours=1))
    seen = {}

    class SlowSmtp:
        async def send(self, raw, mail_from, rcpts):
            seen["swept"] = await sendgate.sweep_stuck()
            seen["status"] = await _status(oid)
            return "250 queued"

    async def fake_connector_for(db, row):
        return SlowSmtp()

    monkeypatch.setattr("mailr.connectors.loader.connector_for", fake_connector_for)
    await sendgate.send_job({"outbox_id": oid}, None)
    assert seen == {"swept": 0, "status": "sending"}
    assert await _status(oid) == "sent"


async def test_a_send_whose_worker_died_is_still_swept(app, user):
    await _setup_identity(user)
    oid = await _outbox(user, datetime.now(UTC) - timedelta(hours=1), status="sending")
    async with session_scope() as db:
        await db.execute(text("UPDATE outbox SET claimed_at = now() - interval '20 minutes' WHERE id = :i"), {"i": oid})
    assert await sendgate.sweep_stuck() == 1
    assert await _status(oid) == "failed"
    # A row claimed before claimed_at existed: send_at decides, as before.
    old = await _outbox(user, datetime.now(UTC) - timedelta(hours=1), status="sending")
    assert await sendgate.sweep_stuck() == 1
    assert await _status(old) == "failed"


async def test_a_forward_job_that_runs_again_queues_one_copy(app, user, raw):
    """The worker died after the forward's transaction committed; the reaper runs the job again."""
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.store import user_store
    from mailr.core.tasks import rules_forward
    from mailr.models import ForwardAddress

    await _setup_identity(user)
    async with session_scope() as db:
        db.add(ForwardAddress(user_id=user.id, email="me@elsewhere.org", verified_at=datetime.now(UTC)))
        u = await db.get(User, user.id)
        res = await ingest_raw(db, await user_store(db, u), raw("Invoice 7", frm="Vendor <ap@vendor.example>"),
                               IngestOptions())
        mid = res.message.id
    for _ in range(2):
        await rules_forward({"message_id": mid, "to": "me@elsewhere.org"}, None)
    async with sessionmaker()() as db:
        rows = (await db.execute(select(Outbox).where(Outbox.user_id == user.id))).scalars().all()
        assert (await db.get(Message, mid)) is not None
    assert len(rows) == 1 and "Invoice 7" in rows[0].payload["subject"]
