"""Per-user turns in the job lanes (core/jobs.py Lane): the users with due jobs take turns, one user
holds at most half of a lane's slots, the jobs without a user take a turn of their own, sends and
reminders go first, and the writes to the mail servers run one at a time per mail account, also
across workers. Two workers on one queue run each job once, and a scheduled task once.

The claims run against the test database. Jobs other tests left queued are parked in the far
future for the test and put back afterwards, so a lane can only claim the test's own jobs."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from mailr.core import jobs
from mailr.db import session_scope

pytestmark = pytest.mark.serial


@pytest.fixture
async def parked(app):
    async with session_scope() as db:
        top = (await db.execute(text("SELECT coalesce(max(id), 0) FROM jobs"))).scalar_one()
        saved = [(r.id, r.run_at) for r in await db.execute(text(
            "UPDATE jobs j SET run_at = '2999-01-01' FROM jobs o WHERE j.id = o.id AND j.status = 'queued' "
            "RETURNING j.id, o.run_at"))]
    yield
    async with session_scope() as db:
        await db.execute(text("DELETE FROM jobs WHERE id > :t"), {"t": top})
        for jid, at in saved:
            await db.execute(text("UPDATE jobs SET run_at = :a WHERE id = :i AND status = 'queued'"),
                             {"a": at, "i": jid})


def _kind() -> str:
    return f"test.turns.{uuid.uuid4().hex[:8]}"


async def _add(kind: str, user_id: int | None, ago_s: float, payload: dict | None = None) -> int:
    async with session_scope() as db:
        return await jobs.enqueue_returning(db, kind, payload or {}, user_id=user_id,
                                            run_at=datetime.now(UTC) - timedelta(seconds=ago_s))


async def _set(jid: int, status: str) -> None:
    async with session_scope() as db:
        await db.execute(text("UPDATE jobs SET status = :s WHERE id = :i"), {"s": status, "i": jid})


async def test_users_take_turns_and_jobs_without_a_user_take_one_too(parked):
    k = _kind()
    for i in range(5):
        await _add(k, 1, 600 - i)
    for i in range(2):
        await _add(k, 2, 300 - i)
    await _add(k, None, 120)
    await _add(k, 3, 60)
    lane = jobs.Lane([k])
    order = []
    while (j := await jobs._claim([k], lane=lane)) is not None:
        order.append(j.user_id)
    # First the users never served, earliest due first; then the one served longest ago.
    assert order == [1, 2, None, 3, 1, 2, 1, 1, 1]


async def test_one_user_holds_half_of_the_lanes_slots_while_others_wait(parked):
    k = _kind()
    for i in range(6):
        await _add(k, 1, 600 - i)
    for i in range(2):
        await _add(k, 2, 300 - i)
    lane = jobs.Lane([k], slots=4)
    assert (lane.cap, lane.spare) == (2, 3)

    async def take():
        j = await jobs._claim([k], lane=lane)
        if j is not None:
            lane.started(j)
        return j

    got = [await take() for _ in range(4)]
    assert [j.user_id for j in got] == [1, 2, 1, 2]  # two each: user 1 waits while user 2 has jobs
    for j in got[1::2]:
        lane.ended(j)
    # Nobody else has a due job: user 1 may use all slots but one, which stays free for others.
    assert (await take()).user_id == 1
    assert await take() is None
    await _add(k, 3, 1)
    assert (await take()).user_id == 3
    assert jobs.Lane([k], slots=1).cap == 1 and jobs.Lane([k], slots=1).spare == 1


async def test_sends_and_reminders_go_first_past_turns_and_caps(parked):
    k = _kind()
    main = jobs.Lane(None, [k2 for ks in jobs.LANE_KINDS.values() for k2 in ks], slots=4)
    assert main.name == "main" and not main.filter and "outbox.send" in main.priority
    for i in range(2):
        await _add(k, 1, 600 - i)
    for _ in range(2):
        main.started(await jobs._claim(None, lane=main))
    await _add(k, 2, 500)  # due before the send below
    send = await _add("outbox.send", 1, 5, {"outbox_id": 0})
    remind = await _add("reminders.fire", 1, 1, {})
    first = await jobs._claim(None, lane=main)
    second = await jobs._claim(None, lane=main)
    third = await jobs._claim(None, lane=main)
    assert [first.id, second.id] == [send, remind]  # user 1 is at its cap; these still go
    assert third.user_id == 2 and third.kind == k


async def test_one_write_at_a_time_per_mail_account(parked, monkeypatch):
    import asyncpg

    from mailr.config import get_settings

    a1 = [await _add("connector.write_back", 1, 60 - i, {"connector_id": 9101, "message_ids": []})
          for i in range(2)]
    a2 = await _add("connector.write_back", 2, 30, {"connector_id": 9102, "message_ids": []})
    a3 = await _add("connector.folder_op", 3, 20, {"connector_id": 9103})
    # A write of account 9103 that holds the account's lock somewhere else (connectors/tasks.py).
    conn = await asyncpg.connect(get_settings().database_url.replace("postgresql+asyncpg", "postgresql"))
    try:
        assert await conn.fetchval("SELECT pg_try_advisory_lock(727200, 9103)")
        up = jobs.Lane(jobs.UPSTREAM_KINDS, slots=4)
        assert up.name == "upstream" and not up.filter
        c1 = await jobs._claim(jobs.UPSTREAM_KINDS, lane=up)
        c2 = await jobs._claim(jobs.UPSTREAM_KINDS, lane=up)
        assert (c1.id, c2.id) == (a1[0], a2)
        # Account 9101 has a write running and 9103's lock is taken: nothing for this slot.
        assert await jobs._claim(jobs.UPSTREAM_KINDS, lane=up) is None
        # Another worker sees the same.
        assert await jobs._claim(jobs.UPSTREAM_KINDS, lane=jobs.Lane(jobs.UPSTREAM_KINDS, slots=4)) is None
    finally:
        await conn.close()
    c3 = await jobs._claim(jobs.UPSTREAM_KINDS, lane=up)
    assert c3.id == a3
    await _set(c1.id, "done")
    c4 = await jobs._claim(jobs.UPSTREAM_KINDS, lane=up)
    assert c4.id == a1[1]


async def test_two_workers_never_claim_two_writes_of_one_account(parked):
    for i in range(4):
        await _add("connector.write_back", 1, 60 - i, {"connector_id": 9201, "message_ids": []})
    lanes = [jobs.Lane(jobs.UPSTREAM_KINDS, slots=4) for _ in range(4)]
    got = await asyncio.gather(*(jobs._claim(jobs.UPSTREAM_KINDS, lane=la) for la in lanes))
    assert sum(j is not None for j in got) == 1


async def test_two_worker_loops_run_each_job_once(parked):
    """Two workers' main lanes on one queue (as two worker containers): every job runs exactly once."""
    k = _kind()
    ran: list[int] = []

    @jobs.handler(k)
    async def run(payload, ctx):
        ran.append(ctx.job_id)
        await asyncio.sleep(0.01)

    ids = [await _add(k, 1 + i % 5, 30) for i in range(40)]
    loops = [asyncio.create_task(jobs.worker_loop(4, kinds=[k], background=False)) for _ in range(2)]
    try:
        deadline = asyncio.get_running_loop().time() + 30
        while len(ran) < len(ids):
            assert asyncio.get_running_loop().time() < deadline, f"{len(ran)} of {len(ids)} ran"
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
    finally:
        for t in loops:
            t.cancel()
        await asyncio.gather(*loops, return_exceptions=True)
        jobs._HANDLERS.pop(k, None)
    assert sorted(ran) == sorted(ids)


async def test_a_scheduled_task_runs_once_across_workers(app):
    from mailr.core import tasks

    name = f"test_once_{uuid.uuid4().hex[:8]}"
    got = await asyncio.gather(*(tasks.claim(name, timedelta(hours=24)) for _ in range(6)))
    assert sum(got) == 1
    assert not await tasks.claim(name, timedelta(hours=24))


async def test_relane_moves_queued_jobs_to_their_kinds_lane(parked):
    jid = await _add("export.user", 1, 10, {"user_id": 1})
    async with session_scope() as db:
        assert (await db.execute(text("SELECT lane FROM jobs WHERE id = :i"), {"i": jid})).scalar() == "maintenance"
        await db.execute(text("UPDATE jobs SET lane = 'main' WHERE id = :i"), {"i": jid})
    await jobs.relane()
    async with session_scope() as db:
        assert (await db.execute(text("SELECT lane FROM jobs WHERE id = :i"), {"i": jid})).scalar() == "maintenance"


async def test_store_sync_leaves_a_user_to_the_worker_that_has_it(app, monkeypatch):
    """Every worker runs the store sync loop for every user; a tick whose user another worker's
    tick holds does nothing."""
    from mailr.core import storesync

    seen: list[int] = []

    async def store(db, user):
        seen.append(user.id)
        raise RuntimeError("no mail store in this test")

    from conftest import make_db_user

    monkeypatch.setattr(storesync, "user_store", store)
    uid = (await make_db_user()).id
    async with session_scope() as db:
        await db.execute(text("UPDATE users SET stalwart_account_id = 'x' WHERE id = :u"), {"u": uid})
    async with session_scope() as other:
        assert await storesync._turn(other, uid)  # another worker's tick
        await storesync._UserSync(uid).tick()
        assert seen == []
    with pytest.raises(RuntimeError):
        await storesync._UserSync(uid).tick()
    assert seen == [uid]


async def test_sync_now_reaches_every_worker(app, monkeypatch):
    """The account syncs in the worker that holds its lease: the job notifies every worker's listener."""
    import asyncpg

    from mailr.config import get_settings
    from mailr.connectors import runner, tasks

    assert jobs.CHANNELS[tasks.SYNC_CHANNEL] is tasks._sync_requested
    got: list[str] = []
    conn = await asyncpg.connect(get_settings().database_url.replace("postgresql+asyncpg", "postgresql"))
    try:
        await conn.add_listener(tasks.SYNC_CHANNEL, lambda *a: got.append(a[3]))
        await tasks.sync_now({"connector_id": 4242}, None)
        for _ in range(100):
            if got:
                break
            await asyncio.sleep(0.02)
    finally:
        await conn.close()
    assert got == ["4242"]
    restarted: list[int] = []
    monkeypatch.setattr(runner, "request_restart", restarted.append)
    tasks._sync_requested(None, 0, tasks.SYNC_CHANNEL, "4242")
    assert restarted == [4242]


def test_each_users_daily_time_is_their_own_and_spread_over_the_day():
    from mailr.core import tasks

    day = timedelta(hours=24)
    offs = sorted(tasks.user_offset(u, day) for u in range(1, 101))
    assert all(0 <= o < day.total_seconds() for o in offs)
    assert tasks.user_offset(7, day) == tasks.user_offset(7, day)
    gaps = [b - a for a, b in zip(offs, offs[1:], strict=False)] + [offs[0] + day.total_seconds() - offs[-1]]
    assert max(gaps) < day.total_seconds() / 25  # no hour-long gap: 100 users, about 15 minutes apart
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    slot = tasks.latest_slot(7, day, now)
    assert now - day < slot <= now
    assert abs((slot.timestamp() % day.total_seconds()) - tasks.user_offset(7, day)) < 1e-3


async def test_spread_daily_work_is_queued_at_the_users_time_once(parked):
    from conftest import make_db_user

    from mailr.core import tasks

    uid = (await make_db_user()).id
    fresh = (await make_db_user()).id

    async def queued(u: int) -> list[str]:
        async with session_scope() as db:
            return sorted((await db.execute(text("SELECT kind FROM jobs WHERE user_id = :u AND status = 'queued'"),
                                            {"u": u})).scalars())

    async with session_scope() as db:
        await db.execute(text("DELETE FROM schedule_runs WHERE name IN ('_retrain', '_exports', '_people_daily', "
                              "'_kb_learning', '_purge_sweep')"))
        # Last run a day and an hour ago: the user's time has come since.
        await db.execute(text("INSERT INTO schedule_runs (name, last_run_at) VALUES (:n, now() - interval '25 hours')"),
                         {"n": f"retrain:{uid}"})
    await asyncio.gather(tasks._spread_daily(), tasks._spread_daily())
    assert await queued(uid) == ["intel.train_all"]
    # A user with no run yet starts counting now: the first run is at their next time.
    assert await queued(fresh) == []
    async with session_scope() as db:
        assert (await db.execute(text("SELECT count(*) FROM schedule_runs WHERE name LIKE :n"),
                                 {"n": f"%:{fresh}"})).scalar() == len(tasks.SPREAD)
    await tasks._spread_daily()
    assert await queued(uid) == ["intel.train_all"]


async def test_the_first_spread_run_counts_from_the_task_it_replaced(parked):
    """After the upgrade a user's first spread run is no more than a period after the old daily run."""
    from conftest import make_db_user

    from mailr.core import tasks

    uid = (await make_db_user()).id
    day = timedelta(hours=24)
    async with session_scope() as db:
        now = (await db.execute(text("SELECT now()"))).scalar_one()
        await db.execute(text("DELETE FROM schedule_runs WHERE name LIKE :n"), {"n": f"%:{uid}"})
        # The old daily task ran just before this user's latest time, and just after.
        await db.execute(text("INSERT INTO schedule_runs (name, last_run_at) VALUES ('_retrain', :t) "
                              "ON CONFLICT (name) DO UPDATE SET last_run_at = excluded.last_run_at"),
                         {"t": tasks.latest_slot(uid, day, now) - timedelta(minutes=1)})
    await tasks._spread_daily()
    async with session_scope() as db:
        n = (await db.execute(text("SELECT count(*) FROM jobs WHERE user_id = :u AND kind = 'intel.train_all' "
                                   "AND status = 'queued'"), {"u": uid})).scalar()
        await db.execute(text("DELETE FROM schedule_runs WHERE name = '_retrain'"))
    assert n == 1


async def test_a_user_whose_time_falls_just_before_the_old_tasks_daily_run_still_comes_due(parked):
    """The replaced task's row may still be written every day just after this user's time (the
    group calendars' export keeps "_exports"); the user's first run must not wait for it forever."""
    from conftest import make_db_user

    from mailr.core import tasks

    uid = (await make_db_user()).id
    day = timedelta(hours=24)
    async with session_scope() as db:
        now = (await db.execute(text("SELECT now()"))).scalar_one()
        await db.execute(text("DELETE FROM schedule_runs WHERE name LIKE :n"), {"n": f"%:{uid}"})
        await db.execute(text("INSERT INTO schedule_runs (name, last_run_at) VALUES ('_retrain', :t) "
                              "ON CONFLICT (name) DO UPDATE SET last_run_at = excluded.last_run_at"),
                         {"t": tasks.latest_slot(uid, day, now) + timedelta(minutes=2)})
    await tasks._spread_daily()
    async with session_scope() as db:
        n = (await db.execute(text("SELECT count(*) FROM jobs WHERE user_id = :u AND kind = 'intel.train_all' "
                                   "AND status = 'queued'"), {"u": uid})).scalar()
        await db.execute(text("DELETE FROM schedule_runs WHERE name = '_retrain'"))
    assert n == 1
