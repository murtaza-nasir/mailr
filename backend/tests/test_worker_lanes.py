"""Job lanes survive database errors: a failed claim or
reap is logged and retried with backoff, and the worker starts a lane again if it ever ends."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import OperationalError

from mailr import worker
from mailr.core import jobs
from mailr.db import session_scope, sessionmaker
from mailr.models import Job

WAIT = 30.0


@pytest.fixture
def kind():
    k = f"test.lane.{uuid.uuid4().hex[:8]}"
    yield k
    jobs._HANDLERS.pop(k, None)


async def _enqueue(kind: str) -> int:
    async with session_scope() as db:
        return await jobs.enqueue_returning(db, kind, {})


async def _until_done(jid: int, loop: asyncio.Task) -> Job:
    deadline = asyncio.get_running_loop().time() + WAIT
    while True:
        async with sessionmaker()() as db:
            j = await db.get(Job, jid)
        if j.status == "done":
            return j
        assert not loop.done(), f"the lane ended: {loop.exception() if not loop.cancelled() else 'cancelled'}"
        assert asyncio.get_running_loop().time() < deadline, f"job {jid} never ran: {j.status}"
        await asyncio.sleep(0.05)


async def _run_lane(kind: str, jid: int) -> Job:
    loop = asyncio.create_task(jobs.worker_loop(1, kinds=[kind], background=False))
    try:
        return await _until_done(jid, loop)
    finally:
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)
        async with session_scope() as db:
            await db.execute(delete(Job).where(Job.kind == kind))


async def test_a_failed_claim_does_not_end_the_lane(app, kind, monkeypatch):
    ran = []

    @jobs.handler(kind)
    async def run(payload, ctx):
        ran.append(ctx.job_id)

    real = jobs._claim
    calls = {"n": 0}

    async def flaky(kinds, exclude=None, lane=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("SELECT 1", {}, Exception("the database system is shutting down"))
        return await real(kinds, exclude, lane)

    monkeypatch.setattr(jobs, "_claim", flaky)
    monkeypatch.setattr(jobs, "LANE_BACKOFF_FIRST", 0.05, raising=False)
    jid = await _enqueue(kind)
    await _run_lane(kind, jid)
    assert ran == [jid] and calls["n"] >= 2


async def test_a_dropped_database_connection_does_not_end_the_lane(app, kind, monkeypatch):
    """A real connection drop: the claim's own backend is terminated by Postgres (as on a restart)."""
    ran = []

    @jobs.handler(kind)
    async def run(payload, ctx):
        ran.append(ctx.job_id)

    real = jobs._claim
    calls = {"n": 0}

    async def dropped(kinds, exclude=None, lane=None):
        calls["n"] += 1
        if calls["n"] == 1:
            async with session_scope() as db:
                await db.execute(text("SELECT pg_terminate_backend(pg_backend_pid())"))
        return await real(kinds, exclude, lane)

    monkeypatch.setattr(jobs, "_claim", dropped)
    monkeypatch.setattr(jobs, "LANE_BACKOFF_FIRST", 0.05, raising=False)
    jid = await _enqueue(kind)
    await _run_lane(kind, jid)
    assert ran == [jid] and calls["n"] >= 2


async def test_a_failed_reap_does_not_end_the_main_lane(app, kind, monkeypatch):
    ran = []

    @jobs.handler(kind)
    async def run(payload, ctx):
        ran.append(ctx.job_id)

    calls = {"n": 0}
    real = jobs.reap_stale

    async def reap(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("UPDATE jobs", {}, Exception("terminating connection due to administrator command"))
        return await real(*a, **kw)

    monkeypatch.setattr(jobs, "reap_stale", reap)
    monkeypatch.setattr(jobs, "LANE_BACKOFF_FIRST", 0.05, raising=False)
    # Swap the listener and heartbeat out: only the reap path is under test.
    async def idle():
        await asyncio.sleep(3600)

    monkeypatch.setattr(jobs, "_heartbeat", idle)
    jid = await _enqueue(kind)
    before = set(jobs._TASKS)
    loop = asyncio.create_task(jobs.worker_loop(1, kinds=[kind], background=True))
    try:
        await _until_done(jid, loop)
    finally:
        started = (set(jobs._TASKS) - before) | {loop}
        for t in started:
            t.cancel()
        await asyncio.gather(*started, return_exceptions=True)
        async with session_scope() as db:
            await db.execute(delete(Job).where(Job.kind == kind))
    assert ran == [jid] and calls["n"] >= 1


async def test_the_worker_restarts_a_lane_that_ends():
    starts = []

    async def lane():
        starts.append(1)
        if len(starts) == 1:
            raise RuntimeError("boom")
        if len(starts) == 2:
            return  # ended without an error
        await asyncio.sleep(3600)

    t = asyncio.create_task(worker.supervised("jobs-test", lane, restart_after=0.01))
    for _ in range(200):
        if len(starts) >= 3:
            break
        await asyncio.sleep(0.01)
    assert len(starts) == 3 and not t.done()
    t.cancel()  # shutdown ends it
    with pytest.raises(asyncio.CancelledError):
        await t


def test_main_runs_every_lane_supervised():
    import inspect

    src = inspect.getsource(worker.main)
    for name in ("jobs", "jobs-upstream", "jobs-caldav", "jobs-images", "jobs-backfill"):
        assert f'supervised("{name}"' in src


def test_a_notification_wakes_only_the_lane_that_runs_the_kind():
    main, images, caldav = asyncio.Event(), asyncio.Event(), asyncio.Event()
    lanes = [(None, jobs.IMAGE_KINDS + jobs.CALDAV_KINDS, main), (jobs.IMAGE_KINDS, None, images),
             (jobs.CALDAV_KINDS, None, caldav)]
    saved = list(jobs._LANES)
    jobs._LANES[:] = lanes
    try:
        jobs._on_notify(None, 0, "mailr_jobs", "images.prefetch")
        assert (main.is_set(), images.is_set(), caldav.is_set()) == (False, True, False)
        images.clear()
        jobs._on_notify(None, 0, "mailr_jobs", "reminders.fire")
        assert (main.is_set(), images.is_set(), caldav.is_set()) == (True, False, False)
        main.clear()
        jobs._on_notify(None, 0, "mailr_jobs", "")  # a bare NOTIFY wakes every lane
        assert main.is_set() and images.is_set() and caldav.is_set()
    finally:
        jobs._LANES[:] = saved


async def test_enqueue_names_its_kind_once_per_transaction(app, kind):
    import asyncpg

    from mailr.config import get_settings

    got: list[str] = []
    conn = await asyncpg.connect(get_settings().database_url.replace("postgresql+asyncpg", "postgresql"))
    try:
        await conn.add_listener("mailr_jobs", lambda *a: got.append(a[3]))
        async with session_scope() as db:
            for _ in range(5):
                await jobs.enqueue(db, kind, {})
        for _ in range(100):
            if got:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.1)
    finally:
        await conn.close()
        async with session_scope() as db:
            await db.execute(delete(Job).where(Job.kind == kind))
    assert got == [kind]
