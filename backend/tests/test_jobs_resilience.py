"""A hung job or a busy account lock never takes a worker slot for long (30 Sep 2026 stall)."""

import asyncio

import pytest

from mailr.core import jobs


async def test_a_hung_job_times_out_and_is_retried(app, monkeypatch):
    calls = []

    @jobs.handler("test.hang")
    async def hang(payload, ctx):
        calls.append(1)
        await asyncio.sleep(3600)

    monkeypatch.setitem(jobs.JOB_TIMEOUT_S, "test.hang", 0.2)
    finished = []

    async def fake_finish(job, status, error="", delay=None, **kw):
        finished.append((status, delay is not None, error.split(":")[0]))

    monkeypatch.setattr(jobs, "_finish", fake_finish)
    job = jobs.Job(id=1, kind="test.hang", user_id=None, payload={}, attempts=1, max_attempts=5)
    await asyncio.wait_for(jobs.run_one(job), 5)
    assert calls == [1] and finished == [("queued", True, "TimeoutError")]


async def test_a_busy_account_lock_requeues_instead_of_waiting(app, monkeypatch):
    from mailr.connectors import tasks

    monkeypatch.setattr(tasks, "LOCK_WAIT_S", 1)
    async with tasks._ConnectorLock(987654):
        with pytest.raises(jobs.Retry):
            async with tasks._ConnectorLock(987654):
                pass
    async with tasks._ConnectorLock(987654):  # released: free again
        pass



async def test_a_note_to_self_gets_its_filter_when_the_sent_copy_arrives_second(client, user, raw):
    """The inbox copy had no passing authentication (sent_by_me false, filters skipped); the Sent
    copy proves the user sent it, and `from:me to:me` then applies (30 Sep 2026 screenshot)."""
    from helpers.core_flow import _ingest

    from mailr.db import sessionmaker
    from mailr.models import Label, Message

    later = (await client.post("/api/v1/labels", json={"name": "Later"})).json()["id"]
    r = await client.post("/api/v1/rules", json={"name": "Notes to self", "query": "from:me to:me",
                                                  "actions": {"archive": True, "add_labels": [later]}})
    assert r.status_code in (200, 201), r.text
    data = raw("Screenshot", frm=f"Me <{user.email}>", to=user.email, msgid="<shot-1@example.com>")
    mid, cid = await _ingest(user, data, roles=["inbox"], source="connector", run_rules=True)
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        inbox = (await db.execute(__import__("sqlalchemy").select(Label.id).where(
            Label.user_id == user.id, Label.role == "inbox"))).scalar()
        assert not m.sent_by_me and inbox in m.label_ids and later not in m.label_ids
    mid2, _ = await _ingest(user, data, roles=["sent"], source="connector", run_rules=True)
    assert mid2 == mid
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        assert m.sent_by_me and later in m.label_ids and inbox not in m.label_ids


async def test_mail_server_writes_have_their_own_lane(app):
    """A queue full of write-backs cannot starve triage: the main lane never claims them."""
    from sqlalchemy import text

    from mailr.db import sessionmaker

    # Earlier tests in the same database leave jobs queued (no worker runs in tests), and a lane
    # claims the oldest one it may take. Park them in the far future, so each lane can only claim
    # this test's jobs, and put them back afterwards.
    async with sessionmaker()() as db:
        parked = [(r.id, r.run_at) for r in await db.execute(text(
            "UPDATE jobs j SET run_at = '2999-01-01' FROM jobs o WHERE j.id = o.id AND j.status = 'queued' "
            "RETURNING j.id, o.run_at"))]
        for _ in range(3):
            await jobs.enqueue(db, "connector.write_back", {"connector_id": 1, "message_ids": []})
        await jobs.enqueue(db, "intel.message", {"message_id": 1})
        await db.commit()
    try:
        main = await jobs._claim(None, jobs.UPSTREAM_KINDS)
        assert main is not None and main.kind == "intel.message"
        assert await jobs._claim(None, jobs.UPSTREAM_KINDS) is None  # only write-backs are left
        up = await jobs._claim(jobs.UPSTREAM_KINDS)
        assert up is not None and up.kind == "connector.write_back"
    finally:
        async with sessionmaker()() as db:
            # A claimed write-back left running would keep its account busy for the upstream lane.
            await db.execute(text("DELETE FROM jobs WHERE id <> ALL(:p) AND kind IN ('connector.write_back', "
                                  "'intel.message') AND status IN ('queued', 'running')"),
                             {"p": [jid for jid, _at in parked]})
            for jid, at in parked:
                await db.execute(text("UPDATE jobs SET run_at = :t WHERE id = :id"), {"t": at, "id": jid})
            await db.commit()


async def test_explain_shows_why_a_filter_did_not_apply(client, user, raw):
    """The Why panel's filter check: each term judged, with the from:me reason spelled out."""
    from helpers.core_flow import _ingest

    later = (await client.post("/api/v1/labels", json={"name": "Later"})).json()["id"]
    await client.post("/api/v1/rules", json={"name": "Notes to self", "query": "from:me to:me",
                                             "actions": {"archive": True, "add_labels": [later]}})
    await client.post("/api/v1/rules", json={"name": "Budget", "query": "subject:budget", "actions": {"star": True}})
    mid, _ = await _ingest(user, raw("Screenshot", frm=f"Me <{user.email}>", to=user.email), roles=["inbox"],
                           source="connector", run_rules=True)
    out = (await client.get(f"/api/v1/messages/{mid}/explain")).json()
    notes = next(f for f in out["filters"] if f["name"] == "Notes to self")
    assert not notes["matches"] and out["from_you"] and not out["sent_by_me"] and "authentication" in out["note"]
    assert notes["terms"] == [{"term": "from:me", "matches": False, "why": "not counted as sent by you (see above)"},
                              {"term": "to:me", "matches": True}]
    assert next(f for f in out["filters"] if f["name"] == "Budget")["terms"] == [{"term": "subject:budget", "matches": False}]


async def test_reap_prunes_old_failed_jobs():
    """Failed jobs were never pruned; they now go after 30 days, recent ones stay."""
    import uuid
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from mailr.db import session_scope
    from mailr.models import Job

    kind = f"test.prune.{uuid.uuid4().hex[:6]}"
    async with session_scope() as db:
        old = Job(kind=kind, status="failed", created_at=datetime.now(UTC) - timedelta(days=31))
        new = Job(kind=kind, status="failed", created_at=datetime.now(UTC) - timedelta(days=2))
        db.add_all([old, new])
        await db.flush()
        old_id, new_id = old.id, new.id
    await jobs.reap_stale()
    async with session_scope() as db:
        left = set((await db.execute(select(Job.id).where(Job.kind == kind))).scalars())
    assert left == {new_id} and old_id not in left
