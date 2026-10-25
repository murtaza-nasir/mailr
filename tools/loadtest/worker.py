#!/usr/bin/env python3
"""A Mailr worker process for the load test (internal; tools/loadtest/run.py starts one per worker).

The real worker (mailr.worker.main: its lanes, claims, scheduler and model calls), with these
differences, so it runs against fictional mailboxes:

- the mail store is a stand-in: JMAP calls succeed and do nothing, a message's raw source is
  missing (triage reads the indexed text);
- a send job made by the load test (payload "loadtest") takes LOADTEST_SEND_SECONDS instead of
  talking to a mail server;
- every finished job of the measured kinds writes a row to loadtest_events (the time it was queued
  for, payload "t0", and the time it finished);
- the scheduler queues nothing (no exports, retraining or upkeep during a run), and the mail store
  is not followed (the users have no store accounts);
- LOADTEST_LEGACY=1: claims as before per-user turns (each lane's earliest due job, one write to
  the mail servers at a time) and no cap on model calls, for before/after numbers.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

MEASURED = {"intel.message", "outbox.send", "intel.backfill"}


def patch() -> None:
    from sqlalchemy import text

    from mailr.core import jobs, store, tasks
    from mailr.core.jmap import JMAPError
    from mailr.db import session_scope

    async def one(scoped, method, args, using=None):
        if method == "Email/set":
            return {"updated": {k: None for k in (args.get("update") or {})},
                    "destroyed": list(args.get("destroy") or [])}
        if method == "Mailbox/get":
            return {"list": [], "state": "s"}
        return {"state": "s", "list": [], "ids": []}

    async def download(self, *a, **kw):
        raise JMAPError("blob", "no mail store in the load test")

    store._Scoped.one = one
    store._Scoped.download = download
    tasks.PERIODIC[:] = []

    async def no_store_sync() -> None:
        await asyncio.Event().wait()

    from mailr.core import storesync

    storesync.supervise = no_store_sync

    send_s = float(os.environ.get("LOADTEST_SEND_SECONDS") or 0.05)
    real_send = jobs._HANDLERS.get("outbox.send")

    async def send(payload, ctx):
        if payload.get("loadtest"):
            await asyncio.sleep(send_s)
            return
        await real_send(payload, ctx)  # type: ignore[misc]

    jobs._HANDLERS["outbox.send"] = send
    real_run = jobs.run_one

    async def run_one(job):
        await real_run(job)
        if job.kind in MEASURED and (job.payload or {}).get("t0"):
            async with session_scope() as db:
                await db.execute(text("INSERT INTO loadtest_events (kind, user_id, t0, t1, worker) "
                                      "VALUES (:k, :u, :t0, :t1, :w)"),
                                 {"k": job.kind, "u": job.user_id, "t0": float(job.payload["t0"]),
                                  "t1": time.time(), "w": os.getpid()})

    jobs.run_one = run_one

    if os.environ.get("LOADTEST_LEGACY") == "1":
        async def claim(self, db):
            # As before: the earliest due job of the lane, whoever it is for.
            if self.name is None or self.name == "main":
                kinds, excl = (self.kinds, self.exclude) if self.name is None else (None, list(jobs._LANE_OF))
            else:
                kinds, excl = jobs.LANE_KINDS[self.name], None
            params = {"kinds": kinds} if kinds else ({"excl": excl} if excl else {})
            row = (await db.execute(text(
                f"UPDATE jobs SET status='running', locked_at=now(), attempts=attempts+1 "
                f"WHERE id = ({jobs.claim_sql(kinds, excl)}) {jobs._RETURNING}"), params)).first()
            return jobs._job(row) if row else None

        jobs.Lane.claim = claim  # type: ignore[method-assign]


async def main() -> None:
    from mailr import worker

    real = worker._register_handlers

    def register() -> None:
        real()
        patch()

    worker._register_handlers = register
    await worker.main()


if __name__ == "__main__":
    asyncio.run(main())
