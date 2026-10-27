"""A small, durable job queue on Postgres.

Handlers register with @handler("kind"). The worker claims jobs with FOR UPDATE SKIP LOCKED,
retries with exponential backoff, and marks jobs failed after max_attempts.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.db import session_scope
from mailr.models import Job

log = logging.getLogger("mailr.jobs")

Handler = Callable[[dict[str, Any], "JobContext"], Awaitable[None]]
_HANDLERS: dict[str, Handler] = {}


class JobContext:
    def __init__(self, job_id: int, kind: str, user_id: int | None, attempt: int):
        self.job_id = job_id
        self.kind = kind
        self.user_id = user_id
        self.attempt = attempt


class Retry(Exception):
    """Raise to reschedule without counting as an error, e.g. Retry(delay=30)."""

    def __init__(self, delay: float = 30):
        self.delay = delay


def handler(kind: str):
    def deco(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn

    return deco


async def enqueue(
    db: AsyncSession,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    user_id: int | None = None,
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
) -> None:
    """Add a job in the caller's transaction. With dedupe_key, a queued duplicate is skipped."""
    stmt = insert(Job).values(
        kind=kind,
        lane=lane_of(kind),
        payload=payload or {},
        user_id=user_id,
        run_at=run_at or datetime.now(UTC),
        dedupe_key=dedupe_key,
        max_attempts=max_attempts,
    )
    if dedupe_key:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["dedupe_key"], index_where=text("status in ('queued','running')")
        )
    await db.execute(stmt)
    await wake(db, kind)


async def wake(db: AsyncSession, kind: str | None = None) -> None:
    """Wake the lane that runs `kind` (every lane without one) when the transaction commits.
    Postgres sends identical notifications of a transaction once, so a batch of enqueues of one
    kind wakes its lane once, and the other lanes not at all."""
    await db.execute(text("SELECT pg_notify('mailr_jobs', :k)"), {"k": kind or ""})


async def enqueue_returning(db: AsyncSession, kind: str, payload: dict[str, Any] | None = None, *,
                            user_id: int | None = None, run_at: datetime | None = None,
                            max_attempts: int = 3) -> int:
    """Like enqueue (no dedupe) but returns the job id, for jobs the caller reports progress on."""
    jid = (await db.execute(insert(Job).values(
        kind=kind, lane=lane_of(kind), payload=payload or {}, user_id=user_id, run_at=run_at or datetime.now(UTC),
        max_attempts=max_attempts).returning(Job.id))).scalar_one()
    await wake(db, kind)
    return jid


# Jobs that talk to the users' mail servers run in a lane of their own (one at a time per worker):
# a slow or hung server must never take the slots triage, pushes and standing orders need.
UPSTREAM_KINDS = ["connector.write_back", "connector.folder_op", "connector.retention", "connector.delete_forever",
                  "sent.provider_copy", "outbox.verify_sent"]
# CalDAV sync and calendar upkeep get a lane too: a slow calendar server must not hold up
# mail or reminders (reminders.fire stays in the main lane).
CALDAV_KINDS = ["calendar.sync", "calendar.check", "calendar.itip_incoming", "calendar.rematerialize",
                "calendar.reorder", "calendar.webcal_refresh"]
# Bulk reviews (agent/review.py) call the chat model batch after batch for minutes: one at a time,
# in a lane of their own, so they never hold the slots sends and reminders need.
REVIEW_KINDS = ["assistant.review"]
# Image proxy fetches (core/imageproxy.py) wait on senders' web servers: a lane of their own.
IMAGE_KINDS = ["images.prefetch"]
# Scheduled upkeep (exports, retraining, rescoring, the database dump) runs in a lane of its own,
# two at a time: after a deploy these jobs used to fill the main lane's slots ahead of sends,
# reminders and snooze wake-ups.
# A copy of past sent mail to a provider (connectors/sentcopy.py) runs there too: it may take hours.
MAINTENANCE_KINDS = ["export.user", "export.calendars", "export.notes", "export.group_calendars",
                     "intel.train_all", "people.rescore", "contacts.suggest", "contacts.signatures",
                     "kb.extract", "db.dump", "sent.backfill", "mail.tally_check"]
# Triage of imported mail and the embedding catch-up work through a mailbox a batch at a time,
# for hours after a big import or a model change: a lane of their own, so live mail, sends,
# reminders and snooze wake-ups never wait behind them.
BACKFILL_KINDS = ["intel.backfill", "intel.embed_pending", "mail.tally_build"]

# The lanes other than the main one, by the name enqueue writes to jobs.lane. Every other kind runs
# in the main lane ("main").
LANE_KINDS: dict[str, list[str]] = {"upstream": UPSTREAM_KINDS, "caldav": CALDAV_KINDS, "review": REVIEW_KINDS,
                                    "images": IMAGE_KINDS, "maintenance": MAINTENANCE_KINDS,
                                    "backfill": BACKFILL_KINDS}
_LANE_OF = {k: lane for lane, ks in LANE_KINDS.items() for k in ks}


def lane_of(kind: str) -> str:
    return _LANE_OF.get(kind, "main")


# Jobs the main lane takes before any user's turn and past the per-user cap: a send, a reminder, a
# snooze wake-up or a push never waits for fairness.
PRIORITY_KINDS = ["outbox.send", "outbox.store_sent", "reminders.fire", "snooze.wake", "followup.check",
                  "push.new_mail", "push.approval", "push.approval_decided", "push.send_failed"]
# One user (and the instance's own upkeep, the jobs without a user) holds at most this share of a
# lane's slots in a worker process, and at least one slot.
USER_SHARE = 0.5
# The writes to the mail servers take turns by mail account: the account's id in the payload.
ACCOUNT = "(payload->>'connector_id')"
# The per-account lock of those writes (connectors/tasks.py _ConnectorLock), and the one a claim
# holds for its own transaction while it checks that no write of the account runs.
ACCOUNT_LOCK, ACCOUNT_CLAIM_LOCK = 727200, 727201

_RETURNING = "RETURNING id, kind, user_id, payload, attempts, max_attempts"


async def relane() -> None:
    """Queued jobs whose kind moved to another lane since they were queued go with it (worker start)."""
    async with session_scope() as db:
        for name, kinds in LANE_KINDS.items():
            await db.execute(text("UPDATE jobs SET lane = :l WHERE status = 'queued' AND kind = ANY(:k) "
                                  "AND lane <> :l"), {"l": name, "k": kinds})
        await db.execute(text("UPDATE jobs SET lane = 'main' WHERE status = 'queued' AND lane <> 'main' "
                              "AND NOT (kind = ANY(:k))"), {"k": list(_LANE_OF)})


def claim_sql(kinds: list[str] | None, exclude: list[str] | None = None) -> str:
    """The earliest due job of these kinds (or of all kinds but `exclude`), whoever it is for: the
    main lane's sends and reminders (PRIORITY_KINDS), and a lane of kinds from several lanes. A
    lane of named kinds takes the first due job of each kind from ix_jobs_queued_kind and the
    earliest of those: with `kind = ANY(…)` the planner walked ix_jobs_queued in run_at order
    through another lane's backlog instead (its statistics cannot tell that few of the lane's rows
    are queued)."""
    if kinds:
        return """SELECT j.id FROM unnest(CAST(:kinds AS text[])) AS lane(kind)
                  CROSS JOIN LATERAL (
                    SELECT id, run_at FROM jobs
                    WHERE status='queued' AND kind = lane.kind AND run_at <= now()
                    ORDER BY run_at, id
                    FOR UPDATE SKIP LOCKED LIMIT 1) j
                  ORDER BY j.run_at, j.id LIMIT 1"""
    kind_filter = "AND NOT (kind = ANY(:excl))" if exclude else ""
    return f"""SELECT id FROM jobs
                  WHERE status='queued' AND run_at <= now() {kind_filter}
                  ORDER BY run_at, id
                  FOR UPDATE SKIP LOCKED LIMIT 1"""


def turns_sql(lane: str) -> str:
    """Whose turn it may be: each user (in the upstream lane each mail account) with queued jobs in
    the lane, with the earliest one's run_at, and the jobs without one as one more. A skip scan of
    ix_jobs_turns (ix_jobs_upstream_account): one index probe per user, however long the queue.
    Only the ones with a due job are returned; the upstream lane's rows carry the user too."""
    key = ACCOUNT if lane == "upstream" else "user_id"
    more = ", user_id" if lane == "upstream" else ""
    where = f"status = 'queued' AND lane = '{lane}'"
    return f"""WITH RECURSIVE t AS (
                 (SELECT {key} AS k, run_at{more} FROM jobs WHERE {where} AND {key} IS NOT NULL
                  ORDER BY {key}, run_at, id LIMIT 1)
                 UNION ALL
                 SELECT n.* FROM t CROSS JOIN LATERAL (
                   SELECT {key} AS k, run_at{more} FROM jobs WHERE {where} AND {key} > t.k
                   ORDER BY {key}, run_at, id LIMIT 1) n)
               SELECT k, run_at{more} FROM t WHERE run_at <= now()
               UNION ALL
               (SELECT NULL, run_at{more} FROM jobs WHERE {where} AND {key} IS NULL AND run_at <= now()
                ORDER BY {key}, run_at, id LIMIT 1)"""


def turn_sql(lane: str, nobody: bool, kind_filter: str = "") -> str:
    """Claim the earliest due job of one user (account) in the lane; `nobody`: of the jobs without one.
    (Ordered by the key too, so the planner reads ix_jobs_turns, not ix_jobs_queued's due backlog.)"""
    key = ACCOUNT if lane == "upstream" else "user_id"
    who = f"{key} IS NULL" if nobody else f"{key} = :k"
    return f"""UPDATE jobs SET status='running', locked_at=now(), attempts=attempts+1
               WHERE id = (SELECT id FROM jobs
                           WHERE status = 'queued' AND lane = '{lane}' AND {who} AND run_at <= now() {kind_filter}
                           ORDER BY {key}, run_at, id FOR UPDATE SKIP LOCKED LIMIT 1)
               {_RETURNING}"""


def _job(row) -> Job:
    return Job(id=row.id, kind=row.kind, user_id=row.user_id, payload=row.payload, attempts=row.attempts,
               max_attempts=row.max_attempts)


class Lane:
    """What one lane of a worker process claims, and its turns.

    `name` is the jobs.lane value it reads ("main", or a LANE_KINDS name). Kinds that are not
    exactly a lane's (tests run lanes of their own kinds) narrow it with a filter on the kind; kinds
    of several lanes, or all kinds, are claimed in run_at order alone (claim_sql).

    Turns: among the users with due jobs, the one this process served longest ago (never served
    first, then the earliest due) goes next, with that user's earliest due job. With `slots`, a user
    holds at most USER_SHARE of them while another user has a due job, and all but one while none
    has; the jobs without a user take turns as one more user. In the
    upstream lane the turns are by mail account, and an account with a write running (here or in
    another worker) or its lock taken is passed over. The main lane's PRIORITY_KINDS go first."""

    def __init__(self, kinds: list[str] | None = None, exclude: list[str] | None = None, slots: int = 0):
        self.kinds, self.exclude = kinds, exclude
        if kinds:
            names = {lane_of(k) for k in kinds}
        else:
            names = {"main"} if exclude else set()
        self.name: str | None = names.pop() if len(names) == 1 else None
        if self.name is None:
            self.filter, self.params = "", {}
        elif kinds and sorted(kinds) != sorted(LANE_KINDS.get(self.name, ())):
            self.filter, self.params = "AND kind = ANY(:kinds)", {"kinds": kinds}
        elif not kinds and sorted(set(exclude or ())) != sorted(_LANE_OF):
            self.filter, self.params = "AND NOT (kind = ANY(:excl))", {"excl": exclude}
        else:
            self.filter, self.params = "", {}
        self.priority = [k for k in PRIORITY_KINDS if self.runs(k)] if self.name == "main" else []
        self.cap = max(1, int(slots * USER_SHARE)) if slots else None
        # While no other user has a due job in the lane, a user may go past the cap up to all
        # slots but one: the lane keeps working for them, and the next user's job finds a slot.
        self.spare = max(self.cap, slots - 1) if self.cap is not None else None
        self.running: dict[int | None, int] = {}  # user → jobs running in this lane here
        self.served: dict[Any, int] = {}  # user (account) → the claim count when last served
        self.count = 0
        self.users: dict[str, int | None] = {}  # upstream: account → its user, as claims found it

    def runs(self, kind: str) -> bool:
        return _runs(kind, self.kinds, self.exclude)

    def started(self, job: Job) -> None:
        self.running[job.user_id] = self.running.get(job.user_id, 0) + 1

    def ended(self, job: Job) -> None:
        n = self.running.get(job.user_id, 0) - 1
        if n > 0:
            self.running[job.user_id] = n
        else:
            self.running.pop(job.user_id, None)

    def _capped(self, user_id: int | None, limit: int | None = None) -> bool:
        limit = self.cap if limit is None else limit
        return limit is not None and self.running.get(user_id, 0) >= limit

    async def claim(self, db: AsyncSession) -> Job | None:
        if self.name is None:
            params = {"kinds": self.kinds} if self.kinds else ({"excl": self.exclude} if self.exclude else {})
            row = (await db.execute(text(
                f"UPDATE jobs SET status='running', locked_at=now(), attempts=attempts+1 "
                f"WHERE id = ({claim_sql(self.kinds, self.exclude)}) {_RETURNING}"), params)).first()
            return _job(row) if row else None
        if self.priority:
            row = (await db.execute(text(
                f"UPDATE jobs SET status='running', locked_at=now(), attempts=attempts+1 "
                f"WHERE id = ({claim_sql(self.priority)}) {_RETURNING}"), {"kinds": self.priority})).first()
            if row:
                return _job(row)
        upstream = self.name == "upstream"
        rows = list((await db.execute(text(turns_sql(self.name)))).all())
        if upstream and rows:
            busy = set((await db.execute(text(
                "SELECT payload->>'connector_id' FROM jobs WHERE status = 'running' AND lane = 'upstream' "
                "UNION SELECT objid::text FROM pg_locks WHERE locktype = 'advisory' AND classid = :l "
                "AND objsubid = 2 AND granted"), {"l": ACCOUNT_LOCK})).scalars())
            for r in rows:
                if r.k is not None:
                    self.users[r.k] = r.user_id
            rows = [r for r in rows if r.k is None or r.k not in busy]
        rows.sort(key=lambda r: (self.served.get(r.k, -1), r.run_at))

        def user(r):  # the cap is per user; an account's user is the one its jobs carry
            return self.users.get(r.k) if upstream else r.k

        free = [r for r in rows if not self._capped(user(r)) or (upstream and r.k is None)]
        spare = [r for r in rows if r not in free and not self._capped(user(r), self.spare)]
        for turn in (free, spare):
            for r in turn:
                if upstream and r.k is not None and not await self._account_free(db, r.k):
                    continue
                row = (await db.execute(text(turn_sql(self.name, r.k is None, self.filter)),
                                        {**self.params, **({} if r.k is None else {"k": r.k})})).first()
                if row:
                    self.count += 1
                    self.served[r.k] = self.count
                    return _job(row)
        return None

    async def _account_free(self, db: AsyncSession, account: str) -> bool:
        """No write of this account runs: checked under a lock this transaction holds until the
        claim commits, so two workers never claim writes of one account at once."""
        try:
            aid = int(account)
        except ValueError:
            return True
        if not (await db.execute(text("SELECT pg_try_advisory_xact_lock(:l, :a)"),
                                 {"l": ACCOUNT_CLAIM_LOCK, "a": aid})).scalar():
            return False
        return not (await db.execute(text(
            "SELECT EXISTS (SELECT 1 FROM jobs WHERE status = 'running' AND lane = 'upstream' "
            "AND payload->>'connector_id' = :k) OR EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
            "AND classid = :l AND objid = CAST(:a AS oid) AND objsubid = 2 AND granted)"),
            {"k": account, "l": ACCOUNT_LOCK, "a": aid})).scalar()


async def _claim(kinds: list[str] | None, exclude: list[str] | None = None, lane: Lane | None = None) -> Job | None:
    lane = lane or Lane(kinds, exclude)
    async with session_scope() as db:
        return await lane.claim(db)


async def _finish(job: Job, status: str, error: str = "", delay: float | None = None, *,
                  refund: bool = False) -> None:
    """Record a run's outcome, only while the row is still this run's: a run the reaper put back
    and another worker took again is that run's to finish. `refund`: the attempt does not count."""
    mine = "WHERE id=:id AND status='running' AND attempts=:a"
    back = "attempts=attempts-1, " if refund else ""
    async with session_scope() as db:
        if delay is not None:
            await db.execute(
                text(f"UPDATE jobs SET {back}status='queued', locked_at=NULL, run_at=:t, error=:e {mine}"),
                {"t": datetime.now(UTC) + timedelta(seconds=delay), "e": error[:4000], "id": job.id, "a": job.attempts},
            )
        else:
            await db.execute(
                text(f"UPDATE jobs SET {back}status=:s, locked_at=NULL, error=:e {mine}"),
                {"s": status, "e": error[:4000], "id": job.id, "a": job.attempts},
            )


DEFAULT_JOB_TIMEOUT_S = 15 * 60
# Kinds that legitimately run longer (imports, retraining, bulk rules).
JOB_TIMEOUT_S: dict[str, int] = {"connector.sync_now": 6 * 3600, "intel.backfill": 6 * 3600, "export.user": 2 * 3600,
                                 "intel.train_all": 3600, "intel.retrain": 3600, "intel.embed_pending": 3600,
                                 "rules.apply": 3600, "bulk.conversations": 3600, "connector.retention": 3600,
                                 "kb.extract": 3600, "db.dump": 2 * 3600, "mail.purge_deleted": 3600,
                                 "calendar.migrate": 6 * 3600, "sent.backfill": 6 * 3600}


async def run_one(job: Job) -> None:
    fn = _HANDLERS.get(job.kind)
    if fn is None:
        await _finish(job, "failed", f"no handler for {job.kind}")
        return
    ctx = JobContext(job.id, job.kind, job.user_id, job.attempts)
    from mailr.core import outbound

    await outbound.refresh_classes()
    # Each job runs in a taint scope of its own (core/outbound.py): local-only rows it reads make
    # its later model and service calls local-only, and nothing carries over to the next job.
    with outbound.scope():
        await _run_in_scope(job, fn, ctx)


async def _run_in_scope(job: Job, fn: Handler, ctx: JobContext) -> None:
    try:
        # Safety net: no job may hold a worker slot forever (hung network calls).
        await asyncio.wait_for(fn(job.payload, ctx), JOB_TIMEOUT_S.get(job.kind, DEFAULT_JOB_TIMEOUT_S))
        await _finish(job, "done")
    except Retry as r:
        # A Retry does not consume an attempt.
        await _finish(job, "queued", "", delay=r.delay, refund=True)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=8)}"
        log.warning("job %s %s failed (attempt %s): %s", job.id, job.kind, job.attempts, e)
        if job.attempts >= job.max_attempts:
            from mailr.core import errors

            errors.capture(e, job_kind=job.kind)  # the error tracker, when one is set up (core/errors.py)
            await _finish(job, "failed", err)
        else:
            await _finish(job, "queued", err, delay=min(3600, 15 * 2 ** job.attempts))


HEARTBEAT_SECONDS = 60
STALE_MINUTES = 5  # no heartbeat for this long = the worker running it died

_INFLIGHT: set[int] = set()
# Set at shutdown before running jobs are handed back: lanes claim nothing more, so a job handed
# back is not claimed again by the stopping worker.
_STOPPING = False
# Claims under way: the shutdown waits for them before it hands jobs back, so a claim that began
# just before the stop cannot take a handed-back job after the hand-back.
_CLAIMING = 0
_TASKS: set[asyncio.Task] = set()  # strong refs: asyncio only keeps weak ones


async def _heartbeat() -> None:
    """Running jobs renew locked_at, so the reaper never re-runs a long job that is alive."""
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        if _INFLIGHT:
            try:
                async with session_scope() as db:
                    await db.execute(text("UPDATE jobs SET locked_at=now() WHERE id = ANY(:ids) AND status='running'"),
                                     {"ids": list(_INFLIGHT)})
            except Exception as e:  # noqa: BLE001
                log.warning("job heartbeat failed: %s", e)


async def _counted_claim(kinds, exclude, turns):
    global _CLAIMING
    _CLAIMING += 1
    try:
        return await _claim(kinds, exclude, lane=turns)
    finally:
        _CLAIMING -= 1


async def requeue_inflight() -> None:
    """On shutdown: hand running jobs back to the queue (their handlers are idempotent)."""
    global _STOPPING
    _STOPPING = True
    for _ in range(40):  # at most 2 s
        if not _CLAIMING:
            break
        await asyncio.sleep(0.05)
    if not _INFLIGHT:
        return
    async with session_scope() as db:
        await db.execute(text("UPDATE jobs SET status='queued', locked_at=NULL, attempts=greatest(attempts-1, 0) "
                              "WHERE id = ANY(:ids) AND status='running'"), {"ids": list(_INFLIGHT)})
    log.info("requeued %d running job(s) at shutdown", len(_INFLIGHT))


async def reap_stale(minutes: int = STALE_MINUTES) -> None:
    """Requeue jobs whose worker died mid-run (no heartbeat). A reaped run counts as an
    attempt, so a job that kills its worker every time ends up failed, not in a loop."""
    async with session_scope() as db:
        await db.execute(
            text(
                "UPDATE jobs SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END, "
                "locked_at=NULL, error='worker stopped while running this job' "
                "WHERE status='running' AND locked_at < now() - make_interval(mins => :m)"
            ),
            {"m": minutes},
        )
        await db.execute(
            text("DELETE FROM jobs WHERE status='done' AND created_at < now() - interval '3 days'")
        )
        # Failed jobs stay a month for inspection, then go too (they were never pruned).
        await db.execute(
            text("DELETE FROM jobs WHERE status='failed' AND created_at < now() - interval '30 days'")
        )


LANE_BACKOFF_FIRST = 1.0  # seconds after the first failed claim; doubles up to LANE_BACKOFF_MAX
LANE_BACKOFF_MAX = 30.0


def lane_name(kinds: list[str] | None, exclude: list[str] | None) -> str:
    return ",".join(kinds) if kinds else "main"


# The lanes of this process: (kinds, exclude, wake-up). The background lane's listener wakes the
# lane a notification names (its payload is a job kind; an empty one wakes every lane).
_LANES: list[tuple[list[str] | None, list[str] | None, asyncio.Event]] = []


def _runs(kind: str, kinds: list[str] | None, exclude: list[str] | None) -> bool:
    if kinds:
        return kind in kinds
    return not (exclude and kind in exclude)


# Other notification channels the worker's listener follows: {channel: callback(conn, pid,
# channel, payload)}, registered by the modules that use them (connectors/tasks.py).
CHANNELS: dict[str, Callable[..., None]] = {}


def _on_notify(_conn, _pid, _channel, payload: str) -> None:
    for kinds, exclude, ev in list(_LANES):
        if not payload or _runs(payload, kinds, exclude):
            ev.set()


async def worker_loop(concurrency: int = 4, kinds: list[str] | None = None, exclude: list[str] | None = None,
                      background: bool = True) -> None:
    global _STOPPING
    _STOPPING = False
    sem = asyncio.Semaphore(concurrency)
    wake = asyncio.Event()
    lane = (kinds, exclude, wake)
    _LANES.append(lane)
    turns = Lane(kinds, exclude, concurrency)

    async def listen():
        import asyncpg

        from mailr.config import get_settings

        dsn = get_settings().database_url.replace("postgresql+asyncpg", "postgresql")
        while True:
            try:
                conn = await asyncpg.connect(dsn)
                await conn.add_listener("mailr_jobs", _on_notify)
                for channel, fn in CHANNELS.items():
                    await conn.add_listener(channel, fn)
                while True:
                    await asyncio.sleep(60)
                    await conn.execute("SELECT 1")
            except Exception as e:  # noqa: BLE001
                log.warning("job listener reconnecting: %s", e)
                await asyncio.sleep(5)

    for coro in ((listen(), _heartbeat()) if background else ()):
        t = asyncio.create_task(coro)
        _TASKS.add(t)
        t.add_done_callback(_TASKS.discard)

    async def run(job: Job):
        _INFLIGHT.add(job.id)
        turns.started(job)
        try:
            await run_one(job)
        finally:
            turns.ended(job)
            _INFLIGHT.discard(job.id)
            sem.release()

    try:
        await _lane(sem, wake, turns, background, run)
    finally:
        _LANES.remove(lane)


async def _lane(sem: asyncio.Semaphore, wake: asyncio.Event, turns: Lane, background: bool, run) -> None:
    kinds, exclude = turns.kinds, turns.exclude
    last_reap = datetime.min.replace(tzinfo=UTC)
    failures = 0
    while True:
        await sem.acquire()
        if _STOPPING:
            sem.release()
            await asyncio.sleep(1)
            continue
        try:
            if background and datetime.now(UTC) - last_reap > timedelta(minutes=5):
                await reap_stale()
                last_reap = datetime.now(UTC)
            job = await _counted_claim(kinds, exclude, turns)
        except asyncio.CancelledError:
            sem.release()
            raise
        except Exception as e:  # noqa: BLE001  a database restart or failover must not end the lane
            sem.release()
            failures += 1
            delay = min(LANE_BACKOFF_MAX, LANE_BACKOFF_FIRST * 2 ** (failures - 1))
            log.warning("job lane %s: claiming failed (%s: %s); retrying in %.0fs", lane_name(kinds, exclude),
                        type(e).__name__, e, delay)
            await asyncio.sleep(delay)
            continue
        failures = 0
        if job is not None and _STOPPING:
            # Claimed while the shutdown handed jobs back: give it back too.
            async with session_scope() as db:
                await db.execute(text("UPDATE jobs SET status='queued', locked_at=NULL, "
                                      "attempts=greatest(attempts-1, 0) WHERE id = :id AND status='running'"),
                                 {"id": job.id})
            job = None
        if job is None:
            sem.release()
            wake.clear()
            try:
                await asyncio.wait_for(wake.wait(), timeout=2.0)
            except TimeoutError:
                pass
            continue
        t = asyncio.create_task(run(job))
        _TASKS.add(t)
        t.add_done_callback(_TASKS.discard)
