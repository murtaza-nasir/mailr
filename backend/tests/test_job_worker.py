"""The job worker (core/jobs.py worker_loop, worker.py) on the real queue: a loop claims due jobs
of its kinds, runs them up to its concurrency, records success and failure, retries with backoff,
honours run_at and Retry, wakes on NOTIFY, keeps running jobs' heartbeat, hands running jobs back
at shutdown and requeues the jobs of a worker that died.

Each test uses job kinds of its own, so the loop never claims the jobs other tests left queued in
the worker database (no worker runs in tests)."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text, update

from mailr.core import jobs
from mailr.db import session_scope, sessionmaker
from mailr.models import Job

WAIT = 30.0  # the longest a test waits for the loop; the loop polls every 2 s without a NOTIFY


_KINDS: list[str] = []


def _kind(name: str) -> str:
    k = f"test.{name}.{uuid.uuid4().hex[:8]}"
    _KINDS.append(k)
    return k


@pytest.fixture(autouse=True)
async def _drop_test_jobs():
    """The jobs and handlers of a test's own kinds go afterwards."""
    yield
    async with session_scope() as db:
        await db.execute(delete(Job).where(Job.kind.in_(_KINDS)))
    for k in _KINDS:
        jobs._HANDLERS.pop(k, None)
    _KINDS.clear()


@contextlib.asynccontextmanager
async def _worker(kinds: list[str], concurrency: int = 4, background: bool = False):
    """A worker loop for these kinds, stopped (with the tasks it started) at the end."""
    before = set(jobs._TASKS)
    loop = asyncio.create_task(jobs.worker_loop(concurrency, kinds=kinds, background=background))
    try:
        yield loop
    finally:
        started = (set(jobs._TASKS) - before) | {loop}
        for t in started:
            t.cancel()
        await asyncio.gather(*started, return_exceptions=True)


async def _enqueue(kind: str, payload: dict | None = None, **kw) -> int:
    async with session_scope() as db:
        return await jobs.enqueue_returning(db, kind, payload or {}, **kw)


async def _job(jid: int) -> Job:
    async with sessionmaker()() as db:
        return await db.get(Job, jid)


async def _until(jid: int, pred, what: str) -> Job:
    deadline = asyncio.get_running_loop().time() + WAIT
    while True:
        j = await _job(jid)
        if pred(j):
            return j
        assert asyncio.get_running_loop().time() < deadline, f"job {jid} never {what}: {j.status} {j.error[:200]}"
        await asyncio.sleep(0.05)


async def _wake() -> None:
    async with session_scope() as db:
        await db.execute(text("NOTIFY mailr_jobs"))


async def test_the_loop_runs_jobs_records_failures_and_retries_with_backoff(app):
    ok, flaky, broken, later = _kind("ok"), _kind("flaky"), _kind("broken"), _kind("later")
    seen: list[tuple[str, int, dict]] = []

    @jobs.handler(ok)
    async def run_ok(payload, ctx):
        seen.append((ctx.kind, ctx.attempt, payload))

    @jobs.handler(flaky)
    async def run_flaky(payload, ctx):
        seen.append((ctx.kind, ctx.attempt, payload))
        if ctx.attempt == 1:
            raise ValueError("the server said no")

    @jobs.handler(broken)
    async def run_broken(payload, ctx):
        raise RuntimeError("always broken")

    @jobs.handler(later)
    async def run_later(payload, ctx):
        seen.append((ctx.kind, ctx.attempt, payload))

    a = await _enqueue(ok, {"n": 1})
    b = await _enqueue(flaky, {"n": 2}, max_attempts=3)
    c = await _enqueue(broken, max_attempts=1)
    d = await _enqueue(later, run_at=datetime.now(UTC) + timedelta(hours=1))
    async with _worker([ok, flaky, broken, later], background=True):  # with the NOTIFY listener
        done = await _until(a, lambda j: j.status == "done", "finished")
        assert done.attempts == 1 and done.locked_at is None and done.error == ""
        # A failure goes back in the queue with its error and an exponential backoff: 15 s × 2^attempt.
        retried = await _until(b, lambda j: j.attempts == 1 and j.status == "queued", "was requeued")
        assert "ValueError: the server said no" in retried.error
        wait = (retried.run_at - datetime.now(UTC)).total_seconds()
        assert 20 < wait <= 30, wait
        # The last allowed attempt that fails marks the job failed, with the traceback.
        failed = await _until(c, lambda j: j.status == "failed", "failed")
        assert failed.attempts == 1 and "RuntimeError: always broken" in failed.error and "Traceback" in failed.error
        # The retry runs when it is due (brought forward here) and succeeds on attempt 2.
        async with session_scope() as db:
            await db.execute(update(Job).where(Job.id == b).values(run_at=datetime.now(UTC)))
        await _wake()
        assert (await _until(b, lambda j: j.status == "done", "finished")).attempts == 2
        # A job due in an hour is not touched.
        assert (await _job(d)).status == "queued" and (await _job(d)).attempts == 0
    assert seen == [(ok, 1, {"n": 1}), (flaky, 1, {"n": 2}), (flaky, 2, {"n": 2})]


async def test_retry_reschedules_without_using_an_attempt(app):
    kind = _kind("retry")
    calls = []

    @jobs.handler(kind)
    async def run(payload, ctx):
        calls.append(ctx.attempt)
        raise jobs.Retry(delay=600)

    jid = await _enqueue(kind, max_attempts=1)
    async with _worker([kind]):
        j = await _until(jid, lambda j: j.status == "queued" and calls, "was rescheduled")
    assert j.attempts == 0 and j.error == "" and calls == [1]
    assert 500 < (j.run_at - datetime.now(UTC)).total_seconds() <= 600


async def test_unknown_kinds_fail_and_other_lanes_kinds_are_left_alone(app):
    mine, theirs = _kind("nohandler"), _kind("otherlane")
    a = await _enqueue(mine)
    b = await _enqueue(theirs)
    async with _worker([mine]):
        j = await _until(a, lambda j: j.status == "failed", "failed")
        assert f"no handler for {mine}" in j.error
    assert (await _job(b)).status == "queued"


async def test_the_loop_runs_at_most_its_concurrency(app):
    kind = _kind("slot")
    running, peak = 0, 0
    release = asyncio.Event()

    @jobs.handler(kind)
    async def run(payload, ctx):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await release.wait()
        running -= 1

    # Three users: one user would hold at most half of the slots (core/jobs.py USER_SHARE).
    ids = [await _enqueue(kind, {"i": i}, user_id=900_001 + i) for i in range(3)]
    async with _worker([kind], concurrency=2):
        deadline = asyncio.get_running_loop().time() + WAIT
        while running < 2:
            assert asyncio.get_running_loop().time() < deadline, "two jobs never ran at once"
            await asyncio.sleep(0.05)
        states = sorted([(await _job(i)).status for i in ids])
        assert states == ["queued", "running", "running"], states  # the third waits for a slot
        release.set()
        for i in ids:
            await _until(i, lambda j: j.status == "done", "finished")
    assert peak == 2


async def test_heartbeat_shutdown_handback_and_a_dead_workers_jobs(app, monkeypatch):
    """A running job's lock is renewed while it runs; at shutdown it goes back to the queue without
    using an attempt; and the reaper requeues a job whose worker stopped renewing it (or fails it
    when it has no attempts left)."""
    kind = _kind("long")
    started, release = asyncio.Event(), asyncio.Event()

    @jobs.handler(kind)
    async def run(payload, ctx):
        started.set()
        await release.wait()

    monkeypatch.setattr(jobs, "HEARTBEAT_SECONDS", 0.05)
    jid = await _enqueue(kind)
    async with _worker([kind], background=True):
        await asyncio.wait_for(started.wait(), WAIT)
        old = datetime.now(UTC) - timedelta(hours=1)
        async with session_scope() as db:
            await db.execute(update(Job).where(Job.id == jid).values(locked_at=old))
        renewed = await _until(jid, lambda j: j.locked_at and j.locked_at > old + timedelta(minutes=30), "was renewed")
        assert renewed.status == "running" and jid in jobs._INFLIGHT
        # Shutdown (worker.py main): running jobs are handed back, the attempt not counted.
        await jobs.requeue_inflight()
        back = await _job(jid)
        assert back.status == "queued" and back.attempts == 0 and back.locked_at is None
        # The stopping worker's free slots claim nothing more: the job stays queued for the next worker.
        await asyncio.sleep(2.5)
        assert (await _job(jid)).status == "queued"
        release.set()
    assert jid not in jobs._INFLIGHT

    # Jobs left 'running' by a worker that died: requeued, or failed when out of attempts.
    stale = datetime.now(UTC) - timedelta(minutes=jobs.STALE_MINUTES + 5)
    async with session_scope() as db:
        alive = Job(kind=kind, status="running", attempts=1, max_attempts=5, locked_at=datetime.now(UTC))
        dead = Job(kind=kind, status="running", attempts=1, max_attempts=5, locked_at=stale)
        spent = Job(kind=kind, status="running", attempts=5, max_attempts=5, locked_at=stale)
        db.add_all([alive, dead, spent])
        await db.flush()
        ids = alive.id, dead.id, spent.id
    await jobs.reap_stale()
    async with sessionmaker()() as db:
        rows = {j.id: j for j in (await db.execute(select(Job).where(Job.id.in_(ids)))).scalars()}
    assert rows[ids[0]].status == "running"
    assert rows[ids[1]].status == "queued" and rows[ids[1]].locked_at is None
    assert rows[ids[2]].status == "failed" and "worker stopped" in rows[ids[2]].error


def test_the_worker_registers_a_handler_for_every_job_kind():
    """worker.py imports the modules whose @handler functions the queue runs. In a fresh process
    (this one has imported most of them already), every kind the code enqueues by name, and every
    kind of the lanes, must then have a handler: a kind without one fails every job of that kind
    ("no handler for ...")."""
    import json
    import os
    import re
    import subprocess
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parent.parent
    out = subprocess.run(
        [sys.executable, "-c", "import json\nfrom mailr import worker\nworker._register_handlers()\n"
                               "from mailr.core import jobs\nprint(json.dumps(sorted(jobs._HANDLERS)))"],
        cwd=backend, env=os.environ, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    registered = set(json.loads(out.stdout.strip().splitlines()[-1]))
    enqueued = set()
    for f in (backend / "mailr").rglob("*.py"):
        enqueued |= set(re.findall(r'enqueue(?:_returning)?\(\w+, *"([a-z_.]+)"', f.read_text()))
    assert len(enqueued) > 40, sorted(enqueued)  # the scan still finds the calls
    lanes = set(jobs.UPSTREAM_KINDS + jobs.CALDAV_KINDS + jobs.IMAGE_KINDS + jobs.MAINTENANCE_KINDS + jobs.BACKFILL_KINDS)
    assert not (enqueued | lanes) - registered, sorted((enqueued | lanes) - registered)
