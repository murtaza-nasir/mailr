"""The job lanes' claims with a long queue.

A history import used to queue one triage job per message: 500,000 queued jobs in the main lane,
and every other lane's claim read all of them, each time it looked for work. Each claim now reads
partial indexes of the queued jobs (ix_jobs_queued_kind for the main lane's sends and reminders,
ix_jobs_turns for the users' turns), so a backlog costs the lanes that do not run it nothing, and
the lane that runs it one index probe per user.

BACKLOG queued jobs of one main-lane kind (default 300,000; MAILR_SCALE_JOBS sets it), half as
many finished jobs of the other lanes' kinds, and a few queued ones of each. Each lane's claim
(core/jobs.py Lane.claim: claim_sql for the sends and reminders, turns_sql, turn_sql) runs under
EXPLAIN (ANALYZE, BUFFERS), rolled back, and must touch at most CLAIM_BUFFERS shared buffers. The
second test spreads the backlog over USERS users: the turns read one probe per user
(TURN_BUFFERS_PER_USER). MAILR_SCALE_REPORT=1 measures without asserting.
"""

from __future__ import annotations

import json
import os
import time

import pytest
from sqlalchemy import text

from mailr.core import jobs

pytestmark = [pytest.mark.scale, pytest.mark.serial]

BACKLOG = int(os.environ.get("MAILR_SCALE_JOBS") or 300_000)
CLAIM_BUFFERS = 100
USERS = 100
TURN_BUFFERS_PER_USER = 8
REPORT_ONLY = os.environ.get("MAILR_SCALE_REPORT") == "1"
BACKLOG_KIND = "test.scale.backlog"
SCALE_USER = 990_000  # the backlog's user: no row in users is needed


async def _analyze(stmt: str, params: list) -> tuple[float, int, list[str]]:
    from mailr.db import engine

    async with engine().connect() as conn:
        raw = (await conn.get_raw_connection()).driver_connection
        tr = raw.transaction()
        await tr.start()
        try:
            plan = await raw.fetchval("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + stmt, *params)
        finally:
            await tr.rollback()
    plan = (json.loads(plan) if isinstance(plan, str) else plan)[0]
    top = plan["Plan"]
    idx: list[str] = []

    def walk(n):
        if n.get("Index Name"):
            idx.append(n["Index Name"])
        for c in n.get("Plans", ()):
            walk(c)

    walk(top)
    return plan["Execution Time"], top.get("Shared Hit Blocks", 0) + top.get("Shared Read Blocks", 0), idx


def _asyncpg(sql: str) -> str:
    """:kinds / :excl / :k → $1 (asyncpg's placeholders)."""
    return sql.replace(":kinds", "$1").replace(":excl", "$1").replace(":k", "$1")


async def _lane_claim(name: str, kinds: list[str] | None, excl: list[str] | None) -> tuple[float, int, list[str]]:
    """The statements of one claim in this lane, summed: the sends and reminders first (the main
    lane), the turns, then the claim of the first turn's earliest job."""
    lane = jobs.Lane(kinds, excl)
    assert lane.name == name and not lane.filter
    parts = []
    if lane.priority:
        parts.append(await _analyze(_asyncpg(jobs.claim_sql(lane.priority)), [lane.priority]))
    parts.append(await _analyze(jobs.turns_sql(name), []))
    from mailr.db import session_scope

    async with session_scope() as db:
        first = (await db.execute(text(jobs.turns_sql(name)))).first()
    if first is not None:
        k = first.k if name != "upstream" or first.k is None else str(first.k)
        parts.append(await _analyze(_asyncpg(jobs.turn_sql(name, k is None)), [] if k is None else [k]))
    return (sum(p[0] for p in parts), sum(p[1] for p in parts), [i for p in parts for i in p[2]])


async def test_lane_claims_do_not_read_another_lanes_backlog(app):
    from mailr.db import engine, session_scope

    lanes = {"upstream": jobs.UPSTREAM_KINDS, "caldav": jobs.CALDAV_KINDS, "images": jobs.IMAGE_KINDS,
             "maintenance": jobs.MAINTENANCE_KINDS, "review": jobs.REVIEW_KINDS, "backfill": jobs.BACKFILL_KINDS}
    main_excl = [k for ks in lanes.values() for k in ks]
    t0 = time.perf_counter()
    async with session_scope() as db:
        await db.execute(text(
            "INSERT INTO jobs (kind, user_id, payload, run_at, status, attempts, max_attempts, error, created_at) "
            "SELECT :k, :u, jsonb_build_object('message_id', g), now() - interval '1 hour' + g * interval '1 ms', "
            "'queued', 0, 5, '', now() FROM generate_series(1, :n) g"),
            {"k": BACKLOG_KIND, "n": BACKLOG, "u": SCALE_USER})
        # Finished jobs of the lanes' kinds, as three days of calendar syncs and write-backs leave.
        await db.execute(text(
            "INSERT INTO jobs (kind, payload, run_at, status, attempts, max_attempts, error, created_at) "
            "SELECT (CAST(:ks AS text[]))[1 + g % cardinality(CAST(:ks AS text[]))], '{}'::jsonb, "
            "now() - interval '2 days', 'done', 1, 5, '', now() - interval '2 days' "
            "FROM generate_series(1, :n) g"), {"ks": main_excl, "n": BACKLOG // 2})
        # A few of each lane's own, due after the backlog's first ones.
        for name, ks in lanes.items():
            await db.execute(text(
                "INSERT INTO jobs (kind, lane, payload, run_at, status, attempts, max_attempts, error, created_at) "
                "SELECT k, :l, '{}'::jsonb, now() - interval '1 minute', 'queued', 0, 5, '', now() "
                "FROM unnest(CAST(:ks AS text[])) k, generate_series(1, 3)"), {"ks": ks, "l": name})
    async with engine().connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("VACUUM ANALYZE jobs"))
    print(f"\njobs: {BACKLOG} queued in {time.perf_counter() - t0:.1f}s")
    try:
        results = {}
        for name, ks in lanes.items():
            results[name] = await _lane_claim(name, ks, None)
        results["main"] = await _lane_claim("main", None, main_excl)
        for name, (ms, bufs, idx) in results.items():
            print(f"  claim {name:<12} {ms:8.2f} ms {bufs:6d} buffers  {', '.join(sorted(set(idx)))}")
        if not REPORT_ONLY:
            for name, (_ms, bufs, idx) in results.items():
                assert bufs <= CLAIM_BUFFERS, (name, bufs, idx)
    finally:
        async with session_scope() as db:
            await db.execute(text("DELETE FROM jobs WHERE kind = :k OR (kind = ANY(:ks) AND payload = '{}'::jsonb "
                                 "AND user_id IS NULL)"),
                             {"k": BACKLOG_KIND, "ks": main_excl})


async def test_turns_read_one_probe_per_user(app):
    """BACKLOG queued jobs spread over USERS users in the main lane, and as many write-backs over as
    many mail accounts: the turns cost about one index probe per user (account), whatever the
    backlog, and the claim of one user's earliest job a few buffers."""
    from mailr.db import engine, session_scope

    per = BACKLOG // USERS
    async with session_scope() as db:
        await db.execute(text(
            "INSERT INTO jobs (kind, user_id, payload, run_at, status, attempts, max_attempts, error, created_at) "
            "SELECT :k, :u0 + g % :users, jsonb_build_object('message_id', g), "
            "now() - interval '1 hour' + g * interval '1 ms', 'queued', 0, 5, '', now() "
            "FROM generate_series(1, :n) g"), {"k": BACKLOG_KIND, "n": per * USERS, "u0": SCALE_USER, "users": USERS})
        await db.execute(text(
            "INSERT INTO jobs (kind, lane, user_id, payload, run_at, status, attempts, max_attempts, error, created_at) "
            "SELECT 'connector.write_back', 'upstream', :u0 + g % :users, "
            "jsonb_build_object('connector_id', :u0 + g % :users, 'message_ids', '[]'::jsonb), "
            "now() - interval '1 hour' + g * interval '1 ms', 'queued', 0, 5, '', now() "
            "FROM generate_series(1, :n) g"), {"n": 20 * USERS, "u0": SCALE_USER, "users": USERS})
    async with engine().connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("VACUUM ANALYZE jobs"))
    try:
        results = {name: await _analyze(jobs.turns_sql(name), []) for name in ("main", "upstream")}
        async with session_scope() as db:
            k = (await db.execute(text(jobs.turns_sql("main")))).first().k
        results["main turn"] = await _analyze(_asyncpg(jobs.turn_sql("main", False)), [k])
        for name, (ms, bufs, idx) in results.items():
            print(f"  {name:<12} {ms:8.2f} ms {bufs:6d} buffers  {', '.join(sorted(set(idx)))}")
        if not REPORT_ONLY:
            for name in ("main", "upstream"):
                assert results[name][1] <= TURN_BUFFERS_PER_USER * USERS + 20, (name, results[name])
            # The claim's UPDATE writes the row's entries in each of the jobs indexes too.
            assert results["main turn"][1] <= 50, results["main turn"]
    finally:
        async with session_scope() as db:
            await db.execute(text("DELETE FROM jobs WHERE kind = :k OR (kind = 'connector.write_back' "
                                  "AND user_id >= :u0)"), {"k": BACKLOG_KIND, "u0": SCALE_USER})
