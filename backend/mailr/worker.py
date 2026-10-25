"""The worker process: job queue, connector sync loops, Stalwart change sync, schedulers."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Awaitable, Callable

from mailr.config import get_settings

log = logging.getLogger("mailr.worker")


def _register_handlers() -> None:
    # Importing modules registers their @handler functions.
    import mailr.contacts.tasks  # People; also the contacts.write executor
    import mailr.core.approval_executors
    import mailr.core.permissions
    import mailr.core.purge
    import mailr.core.sendgate
    import mailr.core.tasks
    import mailr.core.users  # noqa: F401

    for mod in ("mailr.intel.tasks", "mailr.connectors.tasks", "mailr.connectors.sentcopy", "mailr.calendar.tasks", "mailr.calendar.sync", "mailr.calendar.webcal", "mailr.core.push", "mailr.knowledge.tasks",
                "mailr.assistant.orders", "mailr.assistant.errands", "mailr.agent.review", "mailr.assistant.forwards", "mailr.assistant.tools", "mailr.core.vacation", "mailr.core.mailgroup_tasks", "mailr.core.tally", "mailr.core.imageproxy", "mailr.core.screener",
                "mailr.integrations.speakr.sync", "mailr.integrations.speakr.mail", "mailr.notes.tasks",
                "mailr.tables.tasks"):
        try:
            __import__(mod)
        except ModuleNotFoundError as e:
            if e.name != mod:
                raise
            log.info("optional task module %s not present", mod)


RESTART_SECONDS = 5.0


async def supervised(name: str, start: Callable[[], Awaitable[None]], restart_after: float | None = None) -> None:
    """Run a worker loop forever: when it returns or raises (it should never), log it and start it
    again after a pause. Only cancellation (shutdown) ends it. A Postgres restart once ended every
    job lane for good while the scheduler kept the heartbeat fresh (13 Oct 2026)."""
    pause = RESTART_SECONDS if restart_after is None else restart_after
    while True:
        try:
            await start()
            log.error("worker loop %s ended unexpectedly; restarting in %.0fs", name, pause)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("worker loop %s crashed; restarting in %.0fs", name, pause)
        await asyncio.sleep(pause)


async def main() -> None:
    from mailr.core import netguard

    # As in the API (main.py): the outbound guard's infrastructure addresses, warmed in the background.
    asyncio.get_running_loop().run_in_executor(None, netguard.refresh_infra_cache)
    s = get_settings()
    from mailr.core import errors, logsetup

    logsetup.configure("worker")
    errors.init("worker")
    from mailr.core.jobs import (
        BACKFILL_KINDS,
        CALDAV_KINDS,
        IMAGE_KINDS,
        MAINTENANCE_KINDS,
        REVIEW_KINDS,
        UPSTREAM_KINDS,
        relane,
        worker_loop,
    )
    from mailr.core.users import bootstrap

    await bootstrap(run_migrations=False)
    _register_handlers()
    await relane()
    # Seven lanes: everything else, writes to the users' mail servers (MAILR_UPSTREAM_CONCURRENCY
    # at a time, one per mail account), CalDAV sync two at a time, image proxy fetches two at a
    # time, scheduled upkeep two at a time, bulk reviews one at a time, and the triage of imported
    # mail with the embedding catch-up two at a time. In each lane the users take turns
    # (core/jobs.py Lane). Each runs under `supervised`: a lane that ends for any reason but
    # shutdown starts again.
    tasks = [asyncio.create_task(supervised("jobs", lambda: worker_loop(
                 s.worker_concurrency, exclude=UPSTREAM_KINDS + CALDAV_KINDS + IMAGE_KINDS + MAINTENANCE_KINDS + REVIEW_KINDS
                 + BACKFILL_KINDS)),
                                 name="jobs"),
             asyncio.create_task(supervised("jobs-review", lambda: worker_loop(1, kinds=REVIEW_KINDS, background=False)),
                                 name="jobs-review"),
             asyncio.create_task(supervised("jobs-maintenance", lambda: worker_loop(2, kinds=MAINTENANCE_KINDS, background=False)),
                                 name="jobs-maintenance"),
             asyncio.create_task(supervised("jobs-upstream", lambda: worker_loop(
                 max(1, s.upstream_concurrency), kinds=UPSTREAM_KINDS, background=False)),
                                 name="jobs-upstream"),
             asyncio.create_task(supervised("jobs-caldav", lambda: worker_loop(2, kinds=CALDAV_KINDS, background=False)),
                                 name="jobs-caldav"),
             asyncio.create_task(supervised("jobs-images", lambda: worker_loop(2, kinds=IMAGE_KINDS, background=False)),
                                 name="jobs-images"),
             asyncio.create_task(supervised("jobs-backfill", lambda: worker_loop(2, kinds=BACKFILL_KINDS, background=False)),
                                 name="jobs-backfill")]
    try:
        from mailr.connectors.runner import supervise

        tasks.append(asyncio.create_task(supervise(), name="connectors"))
    except ModuleNotFoundError as e:
        if e.name != "mailr.connectors.runner":
            raise
    from mailr.core.storesync import supervise as store_supervise
    from mailr.core.tasks import scheduler

    tasks.append(asyncio.create_task(store_supervise(), name="storesync"))
    tasks.append(asyncio.create_task(scheduler(), name="scheduler"))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    log.info("worker stopping")
    from mailr.core.jobs import requeue_inflight

    try:
        await asyncio.wait_for(requeue_inflight(), timeout=5)
    except Exception as e:  # noqa: BLE001
        log.warning("could not requeue running jobs: %s", e)
    for t in tasks:
        t.cancel()
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=8)
    except TimeoutError:
        log.warning("worker tasks did not stop in time")
    # Blocking IMAP IDLE calls run in executor threads that cannot be interrupted; do not
    # wait for them at interpreter shutdown. Jobs are durable, so nothing is lost.
    logging.shutdown()
    os._exit(0)


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
