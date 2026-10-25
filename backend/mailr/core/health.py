"""Readiness checks and outbound heartbeats for uptime monitors.

`GET /api/v1/health/ready` (main.py) runs the checks below, caches the result for
CACHE_SECONDS, and answers 503 when a required check fails. The body carries no secrets and
no personal data: details are short plain phrases with counts, never account names or
addresses.

Checks:
- db: a trivial query.
- store: Stalwart answers its liveness probe.
- worker: the scheduler loop (core/tasks.py) records a heartbeat every tick; it must be under
  WORKER_STALE old, and no queued job may be overdue by more than its lane allows (jobs
  scheduled for later are not overdue): JOBS_OVERDUE_MINUTES, or lane_overdue_minutes() for the
  lanes whose jobs wait their turn for long by design (the maintenance lane takes every user's
  daily exports at once, two at a time).
- sync: enabled mail connectors in store or mirror mode are not in error and synced within
  SYNC_STALE (or twice their poll interval, if longer). A first sync in progress counts as fine,
  and so does an error while the last good sync is under SYNC_ERROR_GRACE (plus the poll
  interval) old: the connector is retrying a provider's short outage.
- export: every enabled user with mail has a Maildir export run within EXPORT_STALE
  (core/backup.py writes `.last_run` when a run catches up). With MAILR_MAIL_EXPORT=false the
  check is "off": it passes and the result carries `"off": true`.
- backup_target: with MAILR_BACKUP_TARGET set, the target exists, takes a write, and holds a
  run record within EXPORT_STALE for every user with mail. With the mail export off only the
  target itself is checked; the calendar and notes copies are in their own checks.
- calendar_export: every enabled user with calendars or address books in the mirror, and the
  group calendars when there are any, have a calendar export run (calendar/backup.py) within
  EXPORT_STALE; with a usable backup target, its copy too. (An unusable target fails
  backup_target, not this check.)
- notes_export: every enabled user who owns a note, a list or a table has a notes export run
  (notes/backup.py: Markdown, CSV, xlsx and JSON) within EXPORT_STALE; with a usable backup
  target, its copy too. The Admin diagnostics read it from this result like every check.
- The export checks above (export, backup_target's copies, calendar_export, notes_export) count
  an owner that has never been exported as waiting, not failing, while its data is younger than
  EXPORT_STALE (the daily interval plus a grace): a new install, account, first note or calendar
  is not "not exported" before an export could have run. The worker exports such owners within
  minutes anyway (core/tasks.py, _first_exports). Once an owner has a run, a run older than
  EXPORT_STALE fails as before, and an owner whose data is older than EXPORT_STALE with no run
  at all fails too.
- push: the ntfy server answers (info only).
- egress: the last egress check of the worker (core/egress.py): the VPN egress proxy answers and
  its exit address is not the server's own (info only: images failing never make mail
  unhealthy; no addresses here, since this endpoint needs no sign-in).
- proxy: no request in the last hour carried X-Forwarded-For from an untrusted peer
  (core/proxies.py; info only, and it reflects the process that answers).
- db_dump: with nightly dumps on (MAILR_DB_DUMP, core/dbdump.py), the last dump succeeded and the
  last good one is under EXPORT_STALE old.
- disk: free space of the data folder, the backup target and MAILR_DISK_CHECK_PATHS. Fails below
  MAILR_DISK_FAIL_PERCENT free, warns (still ok, `warn` set) below MAILR_DISK_WARN_PERCENT. The
  data volume shares its file system with Docker's other volumes (Postgres, the mail store) in
  the default setup, so it stands for them too.
- jobs_failed: no job failed for good in the last hour (info only: a warning, never a failure).
- store_sync: no message in the mail store has failed to be indexed INDEX_ATTEMPTS times
  (core/storesync.py; info only: a warning, never a failure).

Liveness (`GET /api/v1/health/live`, the app container's Docker healthcheck): only the db and
store checks (`live`). Readiness is for monitors: a backup target that is not mounted or one
account's expired password must not take the web app down, and a proxy such as Traefik stops
routing to a container that Docker reports unhealthy.

A check may return a third value, True for a warning: the result then carries "warn": true; OFF
for a check with nothing to watch because its feature is turned off: it passes and the result
carries "off": true.

Admin → Diagnostics (api/admin_routes.diagnostics) renders every check in CHECKS with its LABELS
name, so a new check needs no screen change. A check's detail is an i18n msg (health.ready.*); the
result carries it in English as `detail` (this endpoint, the heartbeats) and as `detail_msg`, which
Diagnostics renders in the admin's language with the check's name (health.check.<name>).

Heartbeats (worker only), each off while its URL is empty: MAILR_HEARTBEAT_URL gets
`status=up&msg=OK` or `status=down&msg=<failing checks>` every HEARTBEAT_EVERY from the scheduler; MAILR_BACKUP_HEARTBEAT_URL
gets `status=up` after an export run (mail or calendars) that leaves the export, target and
calendar export checks passing, at most once an hour. Both go through the outbound guard, time out quickly, run beside the
scheduler (never inside it), and log a failure at most once an hour.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from sqlalchemy import text

from mailr import i18n
from mailr.config import get_settings
from mailr.db import session_scope

log = logging.getLogger("mailr.health")

CHECKS = ("db", "store", "worker", "sync", "export", "backup_target", "calendar_export", "notes_export", "db_dump",
          "disk", "push", "egress", "proxy", "jobs_failed", "store_sync")
BACKUP_CHECKS = ("export", "backup_target", "calendar_export", "notes_export", "db_dump")
LIVE_CHECKS = ("db", "store")
ALWAYS_OPTIONAL = {"push", "egress", "proxy", "jobs_failed", "store_sync"}
# Plain names for Admin → Diagnostics (api/admin_routes.diagnostics), in English; the catalogue has
# them as health.check.<name> (label()). A check without one shows under its own name, so a check
# added here appears there with nothing else to change.
LABELS = {
    "db": "Database",
    "store": "Mail store",
    "worker": "Background worker",
    "sync": "Mail accounts syncing",
    "export": "Daily mail export",
    "backup_target": "Backup copy",
    "calendar_export": "Calendar and contacts export",
    "notes_export": "Notes and tables export",
    "push": "Push server (ntfy)",
    "egress": "Image egress (VPN)",
    "proxy": "Reverse proxy headers",
    "db_dump": "Database dump",
    "disk": "Free disk space",
    "jobs_failed": "Failed jobs",
    "store_sync": "Mail store indexing",
}

CACHE_SECONDS = 20
CHECK_TIMEOUT = 5
WORKER_STALE = 180
JOBS_OVERDUE_MINUTES = 15
SYNC_STALE = 30 * 60
SYNC_ERROR_GRACE = 15 * 60
EXPORT_STALE = 26 * 3600
HEARTBEAT_EVERY = 300
BACKUP_HEARTBEAT_EVERY = 3600
HTTP_TIMEOUT = 5
LOG_EVERY = 3600
MSG_MAX = 100

HEARTBEAT_KEY = "worker_heartbeat"
MAIL_KINDS = ("imap", "gmail")
PROBE = ".mailr-health-probe"

# (ok, detail) or (ok, detail, warn | OFF): the detail is English, an i18n.Text with its msg (health.ready.*)
OFF = "off"
Result = tuple[bool, str] | tuple[bool, str, bool] | tuple[bool, str, str]
M = i18n.text


def label(name: str, lang: str = i18n.DEFAULT) -> str:
    """A check's plain name in `lang`."""
    if i18n.has(f"health.check.{name}"):
        return i18n.t(f"health.check.{name}", lang)
    return LABELS.get(name, name.replace("_", " ").capitalize())


def detail(c: dict[str, Any], lang: str = i18n.DEFAULT) -> str:
    """A check's detail (from run_checks) in `lang`."""
    return i18n.render(c.get("detail_msg"), lang, fallback=c.get("detail") or "")


def required() -> set[str]:
    extra = {x.strip() for x in (get_settings().health_optional or "").split(",") if x.strip()}
    return set(CHECKS) - ALWAYS_OPTIONAL - extra


# ---------------------------------------------------------------- checks


async def check_db() -> Result:
    try:
        async with session_scope() as db:
            await db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        return False, M("health.ready.db_unreachable")
    return True, M("health.ready.reachable")


async def check_store() -> Result:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(get_settings().stalwart_url.rstrip("/") + "/healthz/live")
    except httpx.HTTPError:
        return False, M("health.ready.store_unreachable")
    if r.status_code >= 500:
        return False, M("health.ready.store_answered", status=r.status_code)
    return True, M("health.ready.reachable")


async def record_tick() -> None:
    """The worker's scheduler heartbeat (one row in instance_settings, DB clock)."""
    async with session_scope() as db:
        await db.execute(text(
            "INSERT INTO instance_settings (key, value, updated_at) VALUES (:k, '{}'::jsonb, now()) "
            "ON CONFLICT (key) DO UPDATE SET updated_at = now()"), {"k": HEARTBEAT_KEY})


def heartbeat_file() -> Path:
    """The file the worker's Docker healthcheck reads (`mailr worker-alive`): touched after each
    scheduler tick that reached the database."""
    import os

    # A fixed path in the container's own /tmp (not TMPDIR, which serve and worker point at the
    # shared data volume): the healthcheck runs as a separate process with the image's environment.
    return Path(os.environ.get("MAILR_HEARTBEAT_FILE") or "/tmp/mailr-worker-heartbeat")


def touch_heartbeat_file() -> None:
    try:
        heartbeat_file().touch()
    except OSError:
        pass


def worker_alive(max_age: float = WORKER_STALE) -> bool:
    try:
        return time.time() - heartbeat_file().stat().st_mtime < max_age
    except OSError:
        return False


def store_alive(timeout: float = 5) -> bool:
    """The mail store answers its liveness probe (`mailr worker-alive`, a separate process)."""
    try:
        r = httpx.get(get_settings().stalwart_url.rstrip("/") + "/healthz/live", timeout=timeout)
    except httpx.HTTPError:
        return False
    return r.status_code < 500


def evaluate_worker(age: float | None, overdue: int) -> Result:
    if age is None:
        return False, M("health.ready.worker_none")
    if age > WORKER_STALE:
        return False, M("health.ready.worker_stale", minutes=int(age // 60))
    if overdue:
        return False, M("health.ready.jobs_overdue", count=overdue, minutes=JOBS_OVERDUE_MINUTES)
    return True, M("health.ready.worker_ok", seconds=int(age))


def lane_overdue_minutes() -> list[tuple[list[str], int]]:
    """(job kinds, minutes) of the lanes with a longer wait than JOBS_OVERDUE_MINUTES (core/jobs.py):
    the maintenance lane gets the users' daily exports and retraining (spread over the day,
    core/tasks.py SPREAD) and the dump, and runs two at a time, each up to hours; bulk reviews and the writes to the users' mail servers run one
    at a time."""
    from mailr.core import jobs

    return [(jobs.MAINTENANCE_KINDS, 12 * 60), (jobs.REVIEW_KINDS + jobs.UPSTREAM_KINDS, 60)]


async def check_worker() -> Result:
    lanes = lane_overdue_minutes()
    case = " ".join(f"WHEN kind = ANY(:k{i}) THEN {int(m)}" for i, (_k, m) in enumerate(lanes))
    async with session_scope() as db:
        age = (await db.execute(text(
            "SELECT extract(epoch FROM now() - updated_at) FROM instance_settings WHERE key = :k"),
            {"k": HEARTBEAT_KEY})).scalar()
        overdue = (await db.execute(text(
            "SELECT count(*) FROM jobs WHERE status = 'queued' "
            f"AND run_at < now() - make_interval(mins => CASE {case} ELSE {JOBS_OVERDUE_MINUTES} END)"),
            {f"k{i}": k for i, (k, _m) in enumerate(lanes)})).scalar_one()
    return evaluate_worker(None if age is None else float(age), int(overdue))


def evaluate_sync(rows: list[dict[str, Any]]) -> Result:
    """rows: status, age (seconds since the last successful sync, or None), poll_minutes."""
    if not rows:
        return True, M("health.ready.sync_none")
    bad = first = 0
    for r in rows:
        poll = 60 * int(r.get("poll_minutes") or 0)
        limit = max(SYNC_STALE, 2 * poll)
        if r["status"] == "error":
            # Providers drop connections for a few minutes now and then (some reset them all at
            # once); the connector retries, and only an error that outlasts the grace counts.
            if r["age"] is None or r["age"] > SYNC_ERROR_GRACE + poll:
                bad += 1
        elif r["age"] is None:
            if r["status"] in ("new", "syncing"):
                first += 1
            else:
                bad += 1
        elif r["age"] > limit:
            bad += 1
    if bad:
        return False, M("health.ready.sync_bad", count=bad)
    if first:
        return True, M("health.ready.sync_ok_first", count=len(rows) - first, first=first)
    return True, M("health.ready.sync_ok", count=len(rows) - first)


async def check_sync() -> Result:
    async with session_scope() as db:
        rows = (await db.execute(text(
            "SELECT status, extract(epoch FROM now() - last_sync_at) AS age, "
            "config->>'poll_minutes' AS poll FROM connectors "
            "WHERE enabled AND kind = ANY(:kinds) AND mode IN ('store', 'mirror')"),
            {"kinds": list(MAIL_KINDS)})).all()
    return evaluate_sync([{"status": r.status, "age": None if r.age is None else float(r.age),
                           "poll_minutes": int(r.poll) if (r.poll or "").isdigit() else 0} for r in rows])


async def _mail_users() -> list[int]:
    async with session_scope() as db:
        return list((await db.execute(text(
            "SELECT u.id FROM users u WHERE NOT u.disabled AND EXISTS (SELECT 1 FROM messages m "
            "WHERE m.user_id = u.id AND m.blob_id IS NOT NULL AND NOT m.deleted AND NOT m.draft) "
            "ORDER BY u.id"))).scalars())


def _stale_waiting(runs: list[datetime | None], since: list[datetime | None] | None,
                   now: datetime) -> tuple[int, list[bool]]:
    """The stale count and a waiting flag per owner. Waiting: never run, and the owner's data
    (`since`, parallel to runs; None when unknown) is younger than EXPORT_STALE, so no export
    was due yet. Stale: a run older than EXPORT_STALE, or no run for older (or unknown) data."""
    stale, waiting = 0, []
    for i, t in enumerate(runs):
        s = since[i] if since is not None and i < len(since) else None
        wait = t is None and s is not None and (now - s).total_seconds() <= EXPORT_STALE
        waiting.append(wait)
        if not wait and (t is None or (now - t).total_seconds() > EXPORT_STALE):
            stale += 1
    return stale, waiting


def evaluate_runs(runs: list[datetime | None], now: datetime, what: str,
                  since: list[datetime | None] | None = None) -> Result:
    """runs: the last run per user with mail; since: when each user's mail account appeared
    (see _stale_waiting). `what`: 'exported' or 'copied'."""
    if not runs:
        return True, M("health.ready.no_mail")
    stale, flags = _stale_waiting(runs, since, now)
    waiting = sum(flags)
    hours = EXPORT_STALE // 3600
    if stale:
        return False, M(f"health.ready.{what}_stale", stale=stale, count=len(runs), hours=hours)
    if waiting:
        return True, M(f"health.ready.{what}_waiting", exported=len(runs) - waiting, waiting=waiting,
                       count=len(runs), hours=hours)
    return True, M(f"health.ready.{what}_ok", count=len(runs), hours=hours)


async def _mail_since(users: list[int]) -> list[datetime | None]:
    """When each user's mail could first be there: their oldest mail account, else the user."""
    if not users:
        return []
    async with session_scope() as db:
        rows = dict((await db.execute(text(
            "SELECT u.id, coalesce((SELECT min(c.created_at) FROM connectors c WHERE c.user_id = u.id "
            "AND c.kind = ANY(:kinds)), u.created_at) FROM users u WHERE u.id = ANY(:ids)"),
            {"ids": users, "kinds": list(MAIL_KINDS)})).all())
    return [rows.get(u) for u in users]


async def check_export() -> Result:
    from mailr.core import backup

    if not get_settings().mail_export:
        return True, M("health.ready.mail_export_off"), OFF
    users = await _mail_users()
    runs = await asyncio.to_thread(lambda: [backup.last_run(backup.export_root(u)) for u in users])
    return evaluate_runs(runs, datetime.now(UTC), "exported", await _mail_since(users))


def _probe_target(path: Path) -> str | None:
    """None when the target is mounted (holds its marker file) and takes a write, else what is
    wrong."""
    from mailr.core.backup import target_problem_msg

    if (problem := target_problem_msg(path)) is not None:
        return M(problem["key"], **problem["params"])
    p = path / PROBE
    try:
        p.write_text(str(time.time()))
        p.unlink()
    except OSError:
        return M("health.ready.target_unwritable")
    return None


async def check_backup_target() -> Result:
    from mailr.core import backup

    t = get_settings().backup_target
    if not t:
        return True, M("health.ready.not_configured")
    problem = await asyncio.to_thread(_probe_target, Path(t))
    if problem:
        return False, problem
    if not get_settings().mail_export:
        return True, M("health.ready.target_ok_mail_off")
    users = await _mail_users()
    runs = await asyncio.to_thread(lambda: [backup.last_run(backup.target_root(u)) for u in users])
    return evaluate_runs(runs, datetime.now(UTC), "copied", await _mail_since(users))


def evaluate_calendar_runs(local: list[datetime | None], copied: list[datetime | None] | None,
                           now: datetime, since: list[datetime | None] | None = None) -> Result:
    """local: the last calendar export run per owner (users, and the group calendars); copied:
    the same on the backup target, or None without a usable target; since: when each owner's
    calendar data appeared (see _stale_waiting)."""
    if not local:
        return True, M("health.ready.no_calendars")
    return _runs_result("calendar", local, copied, now, since)


def _runs_result(kind: str, local: list[datetime | None], copied: list[datetime | None] | None,
                 now: datetime, since: list[datetime | None] | None = None) -> Result:
    """The calendar or notes export check: health.ready.<kind>_*."""
    hours = EXPORT_STALE // 3600
    n, flags = _stale_waiting(local, since, now)
    if n:
        return False, M(f"health.ready.{kind}_not_run", stale=n, count=len(local), hours=hours)
    if copied is not None:
        n, copy_flags = _stale_waiting(copied, since, now)
        if n:
            return False, M(f"health.ready.{kind}_not_copied", stale=n, count=len(copied), hours=hours)
        flags = [a or b for a, b in zip(flags, copy_flags, strict=True)]
    # Waiting: an owner whose export or copy is still pending (both count as not done yet).
    if waiting := sum(flags):
        return True, M(f"health.ready.{kind}_waiting", exported=len(local) - waiting, waiting=waiting,
                       count=len(local), hours=hours)
    return True, M(f"health.ready.{kind}_ok_copied" if copied is not None else f"health.ready.{kind}_ok",
                   count=len(local), hours=hours)


async def check_calendar_export() -> Result:
    from mailr.calendar import backup as cal_backup
    from mailr.core.backup import target_problem

    owners = await cal_backup.owners_with_data()
    local = await asyncio.to_thread(cal_backup.runs, owners, False)
    copied = None
    if get_settings().backup_target and await asyncio.to_thread(target_problem) is None:
        copied = await asyncio.to_thread(cal_backup.runs, owners, True)
    return evaluate_calendar_runs(local, copied, datetime.now(UTC), await cal_backup.data_since(owners))


def evaluate_notes_runs(local: list[datetime | None], copied: list[datetime | None] | None, now: datetime,
                        since: list[datetime | None] | None = None) -> Result:
    """local: the last notes export run per user who owns notes or tables; copied: the same on the
    backup target, or None without a usable target; since: when each user's first note or table
    appeared (see _stale_waiting)."""
    if not local:
        return True, M("health.ready.no_notes")
    return _runs_result("notes", local, copied, now, since)


async def check_notes_export() -> Result:
    from mailr.core.backup import target_problem
    from mailr.notes import backup as notes_backup

    owners = await notes_backup.owners_with_data()
    local = await asyncio.to_thread(notes_backup.runs, owners, False)
    copied = None
    if get_settings().backup_target and await asyncio.to_thread(target_problem) is None:
        copied = await asyncio.to_thread(notes_backup.runs, owners, True)
    return evaluate_notes_runs(local, copied, datetime.now(UTC), await notes_backup.data_since(owners))


async def check_push() -> Result:
    url = get_settings().ntfy_url
    if not url:
        return True, M("health.ready.push_none")
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(url.rstrip("/") + "/v1/health")
    except httpx.HTTPError:
        return False, M("health.ready.push_unreachable")
    if r.status_code >= 400:
        return False, M("health.ready.push_answered", status=r.status_code)
    return True, M("health.ready.push_ok")


async def check_egress() -> Result:
    from mailr.core import egress

    ok, _, detail = await egress.view()
    return ok, detail


async def check_proxy() -> Result:
    """Forwarded headers (core/proxies.py): fails while a request in the last hour carried
    X-Forwarded-For from a peer that is not trusted (info only; no addresses here)."""
    from mailr.core import proxies

    seen = proxies.untrusted_seen()
    if seen is not None and time.time() - seen < 3600:
        return False, M("health.ready.proxy_untrusted")
    return True, M("health.ready.proxy_ok")


def evaluate_db_dump(st: dict[str, Any], now: datetime, on: bool) -> Result:
    """st: the dump record (core/dbdump.state)."""
    if not on:
        return True, M("health.ready.dump_off")
    if not st:
        return False, M("health.ready.dump_none")
    ok_at = st.get("last_ok_at")
    age = None
    if ok_at:
        try:
            age = (now - datetime.fromisoformat(ok_at)).total_seconds()
        except ValueError:
            age = None
    if st.get("ok") is False:
        return False, M("health.ready.dump_failed")
    if age is None or age > EXPORT_STALE:
        return False, M("health.ready.dump_stale", hours=EXPORT_STALE // 3600)
    from mailr.core.dbdump import mb

    return True, M("health.ready.dump_ok", hours=int(age // 3600), mb=mb(st.get("last_ok_bytes")))


async def check_db_dump() -> Result:
    from mailr.core import dbdump

    return evaluate_db_dump(await dbdump.state(), datetime.now(UTC), get_settings().db_dump)


def disk_paths() -> list[tuple[str, Path]]:
    s = get_settings()
    out = [("data", Path(s.data_dir))]
    if s.backup_target:
        out.append(("backup", Path(s.backup_target)))
    for i, p in enumerate(x.strip() for x in (s.disk_check_paths or "").split(",") if x.strip()):
        out.append((f"path {i + 1}", Path(p)))
    return out


def evaluate_disk(free: list[tuple[str, float]], warn_at: float, fail_at: float) -> Result:
    """free: (name, percent free) per path that could be read."""
    if not free:
        return False, M("health.ready.disk_unreadable")
    name, pct = min(free, key=lambda x: x[1])
    if pct < fail_at:
        return False, M("health.ready.disk_full", name=name, percent=int(pct))
    if pct < warn_at:
        return True, M("health.ready.disk_low", name=name, percent=int(pct)), True
    return True, M("health.ready.disk_ok", name=name, percent=int(pct))


def _free_percent(paths: list[tuple[str, Path]]) -> list[tuple[str, float]]:
    import os

    out = []
    for name, p in paths:
        try:
            st = os.statvfs(p)
        except OSError:
            continue
        if st.f_blocks:
            out.append((name, 100.0 * st.f_bavail / st.f_blocks))
    return out


async def check_disk() -> Result:
    s = get_settings()
    free = await asyncio.to_thread(_free_percent, disk_paths())
    return evaluate_disk(free, s.disk_warn_percent, s.disk_fail_percent)


async def check_jobs_failed() -> Result:
    """Jobs that failed for good in the last hour (their last run time is when they failed)."""
    async with session_scope() as db:
        n = (await db.execute(text(
            "SELECT count(*) FROM jobs WHERE status = 'failed' AND run_at > now() - interval '1 hour'"))).scalar_one()
    if n:
        return False, M("health.ready.jobs_failed", count=int(n)), True
    return True, M("health.ready.jobs_failed_none")


async def check_store_sync() -> Result:
    """Messages in the mail store that failed to be indexed INDEX_ATTEMPTS times (core/storesync.py);
    they are tried again once a day."""
    from mailr.core import storesync

    n = sum((await storesync.failed_counts()).values())
    if n:
        return False, M("health.ready.store_sync_failed", count=n), True
    return True, M("health.ready.store_sync_ok")


CHECK_FUNCS: dict[str, Callable[[], Awaitable[Result]]] = {
    "db": check_db,
    "store": check_store,
    "worker": check_worker,
    "sync": check_sync,
    "export": check_export,
    "backup_target": check_backup_target,
    "calendar_export": check_calendar_export,
    "notes_export": check_notes_export,
    "push": check_push,
    "egress": check_egress,
    "proxy": check_proxy,
    "db_dump": check_db_dump,
    "disk": check_disk,
    "jobs_failed": check_jobs_failed,
    "store_sync": check_store_sync,
}


async def _one(name: str) -> Result:
    try:
        return await asyncio.wait_for(CHECK_FUNCS[name](), timeout=CHECK_TIMEOUT)
    except TimeoutError:
        return False, M("health.ready.timed_out")
    except Exception as e:  # noqa: BLE001  details of the error may hold names: keep them in the log
        log.warning("health check %s failed: %s: %s", name, type(e).__name__, e)
        return False, M("health.ready.failed")


async def run_checks(names: tuple[str, ...] | None = None) -> dict[str, Any]:
    names = CHECKS if names is None else names
    req = required()
    results = await asyncio.gather(*(_one(n) for n in names))
    checks = {n: {"ok": r[0], "detail": str(r[1]), "required": n in req,
                  **({"warn": True} if len(r) > 2 and r[2] and r[2] != OFF else {}),
                  **({"off": True} if len(r) > 2 and r[2] == OFF else {}),
                  **({"detail_msg": i18n.msg_of(r[1])} if i18n.msg_of(r[1]) else {})} for n, r in zip(names, results)}  # noqa: B905
    return {"ok": all(c["ok"] for c in checks.values() if c["required"]), "checks": checks,
            "checked_at": datetime.now(UTC).replace(microsecond=0).isoformat()}


_cache: tuple[float, dict[str, Any]] | None = None


async def ready() -> dict[str, Any]:
    """The cached readiness result (CACHE_SECONDS), for the HTTP endpoint."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    result = await run_checks()
    _cache = (time.monotonic(), result)
    return result


async def live() -> dict[str, Any]:
    """Liveness (the app container's healthcheck): the database and the mail store answer. Not
    cached: Docker asks every 30 seconds."""
    return await run_checks(LIVE_CHECKS)


def public(result: dict[str, Any]) -> dict[str, Any]:
    """The result for the readiness endpoint: each check's {ok, detail, required}, English only."""
    return {**result, "checks": {n: {k: v for k, v in c.items() if k != "detail_msg"}
                                 for n, c in result["checks"].items()}}


def clear_cache() -> None:
    global _cache
    _cache = None


# ---------------------------------------------------------------- heartbeats

_last_logged: dict[str, float] = {}


def _warn_hourly(kind: str, msg: str) -> None:
    now = time.monotonic()
    if now - _last_logged.get(kind, -LOG_EVERY) >= LOG_EVERY:
        _last_logged[kind] = now
        log.warning("%s heartbeat failed: %s (logged at most once an hour)", kind, msg)


def _param_name(key: str) -> str:
    """A query key as a bracket- or dot-parsing server (Express qs) reads it: msg[x] and msg.x
    are msg."""
    return re.split(r"[\[.]", key, maxsplit=1)[0].strip().lower()


def plain_msg(msg: Any) -> str:
    """A heartbeat message: one short line of printable ASCII, never empty."""
    text = msg if isinstance(msg, str) else ""
    text = " ".join("".join(ch if " " <= ch <= "~" else " " for ch in text).split())
    return text[:MSG_MAX] or "OK"


def heartbeat_url(url: str, status: str, msg: str) -> str:
    """The URL with status and msg set once each (an Uptime Kuma push URL may already carry
    both). Any other spelling of them (msg[], msg.x) is dropped, since a monitor that parses
    nested query keys would read an object and show "[object Object]"."""
    u = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if _param_name(k) not in ("status", "msg")]
    q += [("status", "up" if status == "up" else "down"), ("msg", plain_msg(msg))]
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), u.fragment))


async def send(kind: str, url: str, status: str, msg: str) -> bool:
    """One heartbeat GET. Never raises; failures are logged at most once an hour."""
    from mailr.core import netguard

    try:
        # Set by the admin in the environment: a LAN monitor is fine (unless private hosts are
        # refused for everyone). Pinned to the checked address; only the status is read.
        private_ok = netguard.private_mode() != "none"
        await netguard.check_url(url, private_ok=private_ok)
        async with netguard.guarded_client(private_ok=private_ok, timeout=HTTP_TIMEOUT) as c:
            r, _ = await netguard.fetch_capped(c, "GET", heartbeat_url(url, status, msg), limit=64 * 1024,
                                               deadline=HTTP_TIMEOUT * 2)
        if r.status_code >= 400:
            _warn_hourly(kind, f"the monitor answered {r.status_code}")
            return False
        if r.is_redirect:  # redirects are not followed, so the push did not arrive
            _warn_hourly(kind, f"the monitor redirected ({r.status_code}); set the final URL")
            return False
        return True
    except netguard.UnsafeTarget as e:
        _warn_hourly(kind, str(e))
    except Exception as e:  # noqa: BLE001  the URL holds the push token: never log it
        _warn_hourly(kind, type(e).__name__)
    return False


def summary(result: dict[str, Any]) -> tuple[str, str]:
    """(status, msg) for a push monitor from a readiness result: "OK", or the names of the
    failing required checks (e.g. "sync, export"; the details stay in /health/ready)."""
    if result["ok"]:
        return "up", "OK"
    failing = [n for n, c in result["checks"].items() if c["required"] and not c["ok"]]
    return "down", ", ".join(failing) or "not ready"


async def worker_heartbeat() -> bool:
    url = get_settings().heartbeat_url
    if not url:
        return False
    status, msg = summary(await run_checks())
    return await send("worker", url, status, msg)


async def backup_heartbeat() -> bool:
    """After an export run (mail, calendars or notes): up when the export, target, calendar export
    and notes export checks pass. Nothing otherwise, so the push monitor goes down when the heartbeats stop."""
    url = get_settings().backup_heartbeat_url
    if not url:
        return False
    result = await run_checks(BACKUP_CHECKS)
    if not all(c["ok"] for c in result["checks"].values()):
        return False
    return await send("backup", url, "up", "export ok" + (" and copied" if get_settings().backup_target else ""))


class _Pacer:
    """Starts a coroutine in the background at most every `every` seconds, one at a time."""

    def __init__(self, every: float):
        self.every = every
        self.last = -float("inf")
        self.task: asyncio.Task | None = None

    def maybe(self, factory: Callable[[], Awaitable[Any]], name: str) -> asyncio.Task | None:
        now = time.monotonic()
        if (self.task is not None and not self.task.done()) or now - self.last < self.every:
            return None
        self.last = now
        self.task = asyncio.create_task(asyncio.wait_for(factory(), timeout=60), name=name)
        self.task.add_done_callback(_swallow)
        return self.task


def _swallow(t: asyncio.Task) -> None:
    if not t.cancelled() and t.exception() is not None:
        _warn_hourly(t.get_name(), type(t.exception()).__name__)


_worker_pacer = _Pacer(HEARTBEAT_EVERY)
_backup_pacer = _Pacer(BACKUP_HEARTBEAT_EVERY)


async def on_scheduler_tick() -> None:
    """Called by the scheduler every tick: the heartbeat row, then (in the background) the
    outbound heartbeat when it is due."""
    try:
        await record_tick()
        touch_heartbeat_file()
    except Exception as e:  # noqa: BLE001
        log.warning("could not record the worker heartbeat: %s", type(e).__name__)
    if get_settings().heartbeat_url:
        _worker_pacer.maybe(worker_heartbeat, "worker")
    from mailr.core import egress

    egress.on_scheduler_tick()


def after_export() -> None:
    """Called when a user's export run caught up."""
    if get_settings().backup_heartbeat_url:
        _backup_pacer.maybe(backup_heartbeat, "backup")

