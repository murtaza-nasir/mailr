"""Readiness checks (/api/v1/health/ready) and outbound heartbeats (core/health.py)."""

import asyncio
import json
import logging
import re
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, parse_qsl, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from mailr.config import get_settings
from mailr.core import backup, health
from mailr.db import session_scope


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    health.clear_cache()
    health._last_logged.clear()
    monkeypatch.setattr(health, "_worker_pacer", health._Pacer(health.HEARTBEAT_EVERY))
    monkeypatch.setattr(health, "_backup_pacer", health._Pacer(health.BACKUP_HEARTBEAT_EVERY))
    yield
    health.clear_cache()


def _fake(ok=True, detail="fine", calls=None, name=None):
    async def f():
        if calls is not None:
            calls.append(name)
        return ok, detail
    return f


def _all_ok(monkeypatch, calls=None, **override):
    funcs = {n: _fake(True, "fine", calls, n) for n in health.CHECKS}
    funcs.update(override)
    monkeypatch.setattr(health, "CHECK_FUNCS", funcs)


@pytest.fixture
async def anon(app, monkeypatch):
    """A monitor with the monitor token (MAILR_HEALTH_TOKEN), which gets the details;
    test_security_misc_paths covers callers without it."""
    monkeypatch.setattr(get_settings(), "health_token", "monitor-token-for-tests")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"X-Health-Token": "monitor-token-for-tests"}) as c:
        yield c


# ---------------------------------------------------------------- endpoint, required set, cache


async def test_ready_200_with_shape(anon, monkeypatch):
    _all_ok(monkeypatch)
    r = await anon.get("/api/v1/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert set(body["checks"]) == set(health.CHECKS)
    assert body["checks"]["db"] == {"ok": True, "detail": "fine", "required": True}
    assert body["checks"]["push"]["required"] is False
    assert r.headers["cache-control"] == "no-store"


async def test_required_failure_is_503_and_optional_is_not(anon, monkeypatch):
    _all_ok(monkeypatch, sync=_fake(False, "1 account not syncing"))
    r = await anon.get("/api/v1/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["sync"] == {"ok": False, "detail": "1 account not syncing", "required": True}

    health.clear_cache()
    _all_ok(monkeypatch, push=_fake(False, "push server unreachable"))
    r = await anon.get("/api/v1/health/ready")
    assert r.status_code == 200 and r.json()["checks"]["push"]["ok"] is False

    # MAILR_HEALTH_OPTIONAL turns a check into information.
    health.clear_cache()
    monkeypatch.setattr(get_settings(), "health_optional", "sync, export")
    _all_ok(monkeypatch, sync=_fake(False, "1 account not syncing"))
    r = await anon.get("/api/v1/health/ready")
    assert r.status_code == 200
    assert r.json()["checks"]["sync"]["required"] is False


async def test_result_is_cached(anon, monkeypatch):
    calls = []
    _all_ok(monkeypatch, calls)
    for _ in range(3):
        assert (await anon.get("/api/v1/health/ready")).status_code == 200
    assert calls.count("db") == 1
    # Past the cache window the checks run again.
    at, res = health._cache
    health._cache = (at - health.CACHE_SECONDS - 1, res)
    await anon.get("/api/v1/health/ready")
    assert calls.count("db") == 2


async def test_a_raising_or_slow_check_fails_without_leaking(anon, monkeypatch):
    async def boom():
        raise RuntimeError("connector alice@example.org exploded")

    async def slow():
        await asyncio.sleep(10)
        return True, "late"

    monkeypatch.setattr(health, "CHECK_TIMEOUT", 0.2)
    _all_ok(monkeypatch, sync=boom, export=slow)
    r = await anon.get("/api/v1/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["sync"]["detail"] == "check failed"
    assert r.json()["checks"]["export"]["detail"] == "check timed out"
    assert "alice" not in r.text


# ---------------------------------------------------------------- db, store


async def test_db_check(monkeypatch):
    assert await health.check_db() == (True, "reachable")

    class Broken:
        async def __aenter__(self):
            raise OSError("connection refused to postgres://secret@db")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(health, "session_scope", lambda: Broken())
    assert await health.check_db() == (False, "database unreachable")


async def test_store_check(monkeypatch):
    assert (await health.check_store())[0] is True  # the dev Stalwart
    monkeypatch.setattr(get_settings(), "stalwart_url", "http://127.0.0.1:9")
    assert await health.check_store() == (False, "mail store unreachable")


# ---------------------------------------------------------------- worker


def test_evaluate_worker():
    assert health.evaluate_worker(None, 0) == (False, "no worker heartbeat yet")
    assert health.evaluate_worker(400, 0) == (False, "no worker heartbeat for 6 min")
    assert health.evaluate_worker(10, 2) == (False, "2 jobs overdue by more than 15 min")
    assert health.evaluate_worker(10, 0) == (True, "heartbeat 10 s ago")


async def _overdue_count() -> int:
    _, detail = await health.check_worker()
    m = re.match(r"(\d+) jobs? overdue", detail)
    return int(m.group(1)) if m else 0


async def test_worker_heartbeat_and_overdue_jobs():
    await health.record_tick()
    assert "no worker heartbeat" not in (await health.check_worker())[1]
    base = await _overdue_count()

    async with session_scope() as db:
        await db.execute(text("UPDATE instance_settings SET updated_at = now() - interval '10 minutes' "
                              "WHERE key = :k"), {"k": health.HEARTBEAT_KEY})
    assert await health.check_worker() == (False, "no worker heartbeat for 10 min")

    await health.record_tick()
    async with session_scope() as db:
        ids = (await db.execute(text(
            "INSERT INTO jobs (kind, payload, run_at, status, attempts, max_attempts, error, created_at) VALUES "
            "('health.test', '{}', now() + interval '2 hours', 'queued', 0, 1, '', now()), "
            "('health.test', '{}', now() - interval '20 minutes', 'queued', 0, 1, '', now()) RETURNING id"))).scalars().all()
    try:
        # The job scheduled for later does not count; the one 20 minutes late does.
        assert await _overdue_count() == base + 1
        ok, detail = await health.check_worker()
        assert ok is False and "overdue by more than 15 min" in detail
    finally:
        async with session_scope() as db:
            await db.execute(text("DELETE FROM jobs WHERE id = ANY(:i)"), {"i": list(ids)})
    assert await _overdue_count() == base


async def test_the_maintenance_lane_waits_longer_before_its_jobs_count_as_overdue():
    """Every user's daily export lands in the maintenance lane at once and runs two at a time: a
    job that has waited an hour there is normal, one that has waited a day is not."""
    await health.record_tick()
    base = await _overdue_count()
    async with session_scope() as db:
        ids = (await db.execute(text(
            "INSERT INTO jobs (kind, payload, run_at, status, attempts, max_attempts, error, created_at) VALUES "
            "('export.user', '{}', now() - interval '3 hours', 'queued', 0, 1, '', now()), "
            "('connector.write_back', '{}', now() - interval '30 minutes', 'queued', 0, 1, '', now()) "
            "RETURNING id"))).scalars().all()
    try:
        assert await _overdue_count() == base
        async with session_scope() as db:
            await db.execute(text("UPDATE jobs SET run_at = now() - interval '13 hours' WHERE id = :i"),
                             {"i": ids[0]})
            await db.execute(text("UPDATE jobs SET run_at = now() - interval '2 hours' WHERE id = :i"),
                             {"i": ids[1]})
        assert await _overdue_count() == base + 2
    finally:
        async with session_scope() as db:
            await db.execute(text("DELETE FROM jobs WHERE id = ANY(:i)"), {"i": list(ids)})


async def test_liveness_checks_only_the_database_and_the_store(anon, monkeypatch):
    """The container's healthcheck: a failing export, sync or backup check never makes it fail."""
    calls: list[str] = []
    _all_ok(monkeypatch, calls, export=_fake(False, "stale", calls, "export"),
            sync=_fake(False, "accounts in error", calls, "sync"))
    r = await anon.get("/api/v1/health/live")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert set(r.json()["checks"]) == {"db", "store"} and sorted(calls) == ["db", "store"]
    assert (await anon.get("/api/v1/health/ready")).status_code == 503
    _all_ok(monkeypatch, store=_fake(False, "mail store unreachable"))
    r = await anon.get("/api/v1/health/live")
    assert r.status_code == 503 and r.json()["checks"]["store"]["ok"] is False


async def test_liveness_without_the_token_is_the_verdict_only(app, monkeypatch):
    _all_ok(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/health/live")
    assert r.status_code == 200 and set(r.json()) == {"ok", "checked_at"}


def test_the_worker_healthcheck_needs_the_store_too(monkeypatch, tmp_path):
    import httpx

    beat = tmp_path / "beat"
    beat.touch()
    monkeypatch.setenv("MAILR_HEARTBEAT_FILE", str(beat))
    assert health.worker_alive()

    def down(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", down)
    assert health.store_alive() is False


async def test_store_sync_warns_about_messages_that_failed_to_index(app, user):
    from mailr.core import storesync
    from mailr.models import StoreSyncState

    assert await health.check_store_sync() == (True, "no message failed to be indexed")
    from sqlalchemy import text

    async with session_scope() as db:
        st = await db.get(StoreSyncState, user.id)
        if st is None:
            db.add(StoreSyncState(user_id=user.id))
            await db.flush()
        p = storesync.Pending()
        p.setdefault("J1", 1.0)
        p["J2"] = {"n": 2, "next": 0}
        p["J3"] = {"n": storesync.INDEX_ATTEMPTS, "next": 0}
        await p.save(db, user.id)
    ok, detail, warn = await health.check_store_sync()
    assert (ok, warn) == (False, True) and detail == "1 message in the mail store could not be indexed"
    assert "store_sync" in health.ALWAYS_OPTIONAL
    async with session_scope() as db:
        await db.execute(text("DELETE FROM store_sync_pending WHERE user_id = :u"), {"u": user.id})


async def test_scheduler_tick_records_heartbeat_and_paces_outbound(monkeypatch):
    sent = []

    async def fake_heartbeat():
        sent.append(1)
        return True

    monkeypatch.setattr(health, "worker_heartbeat", fake_heartbeat)
    async with session_scope() as db:
        await db.execute(text("UPDATE instance_settings SET updated_at = now() - interval '1 hour' "
                              "WHERE key = :k"), {"k": health.HEARTBEAT_KEY})
    monkeypatch.setattr(get_settings(), "heartbeat_url", "")
    await health.on_scheduler_tick()
    assert "no worker heartbeat" not in (await health.check_worker())[1]
    assert health._worker_pacer.task is None  # off without a URL

    monkeypatch.setattr(get_settings(), "heartbeat_url", "http://127.0.0.1:9/push")
    await health.on_scheduler_tick()
    await health._worker_pacer.task
    await health.on_scheduler_tick()  # within five minutes: not again
    await asyncio.sleep(0)
    assert sent == [1]


# ---------------------------------------------------------------- sync


def test_evaluate_sync():
    assert health.evaluate_sync([]) == (True, "no mail accounts to sync")
    fresh = {"status": "idle", "age": 60, "poll_minutes": 0}
    assert health.evaluate_sync([fresh, fresh]) == (True, "2 accounts syncing")
    assert health.evaluate_sync([fresh, {"status": "error", "age": 16 * 60}]) == (False, "1 account not syncing")
    # An error right after a good sync is a retry in progress (a provider's short outage)...
    assert health.evaluate_sync([fresh, {"status": "error", "age": 60}]) == (True, "2 accounts syncing")
    # ...for SYNC_ERROR_GRACE past the poll interval; an account that never synced gets none.
    assert health.evaluate_sync([{"status": "error", "age": 19 * 60, "poll_minutes": 5}])[0] is True
    assert health.evaluate_sync([{"status": "error", "age": 21 * 60, "poll_minutes": 5}])[0] is False
    assert health.evaluate_sync([{"status": "error", "age": None}])[0] is False
    assert health.evaluate_sync([{"status": "idle", "age": 31 * 60}]) == (False, "1 account not syncing")
    # A long poll interval stretches the window.
    assert health.evaluate_sync([{"status": "idle", "age": 50 * 60, "poll_minutes": 30}])[0] is True
    # A first sync in progress is fine; a connector that never synced and is not syncing is not.
    assert health.evaluate_sync([fresh, {"status": "syncing", "age": None}]) == (True, "1 account syncing, 1 on first sync")
    assert health.evaluate_sync([{"status": "idle", "age": None}])[0] is False


def _bad(detail: str) -> int:
    m = re.match(r"(\d+) accounts? not syncing", detail)
    return int(m.group(1)) if m else 0


async def test_sync_counts_connectors_in_error_without_names(app, user, monkeypatch):
    from mailr.models import Connector

    base = _bad((await health.check_sync())[1])
    async with session_scope() as db:
        conns = [
            Connector(user_id=user.id, kind="imap", name=user.email, mode="mirror", enabled=True, status="error",
                      status_detail="auth failed", last_sync_at=datetime.now(UTC) - timedelta(hours=1)),
            # Stale store connector.
            Connector(user_id=user.id, kind="gmail", name=user.email, mode="store", enabled=True, status="idle",
                      last_sync_at=datetime.now(UTC) - timedelta(hours=2)),
            # Not counted: an import, a disabled one, a calendar.
            Connector(user_id=user.id, kind="imap", name=user.email, mode="import", enabled=True, status="error"),
            Connector(user_id=user.id, kind="imap", name=user.email, mode="mirror", enabled=False, status="error"),
            Connector(user_id=user.id, kind="caldav", name=user.email, mode="mirror", enabled=True, status="error"),
        ]
        db.add_all(conns)
        await db.flush()
        ids = [c.id for c in conns]
    try:
        ok, detail = await health.check_sync()
        assert ok is False and _bad(detail) == base + 2

        # The whole response carries counts only (even with the monitor token).
        health.clear_cache()
        monkeypatch.setattr(get_settings(), "health_token", "monitor-token-for-tests")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"X-Health-Token": "monitor-token-for-tests"}) as anon:
            r = await anon.get("/api/v1/health/ready")
        assert r.status_code == 503
        assert user.email not in r.text and user.email.split("@")[0] not in r.text
        assert "auth failed" not in r.text
        for c in r.json()["checks"].values():
            assert set(c) - {"warn"} == {"ok", "detail", "required"} and len(c["detail"]) < 80
    finally:
        async with session_scope() as db:
            await db.execute(text("DELETE FROM connectors WHERE id = ANY(:i)"), {"i": ids})


# ---------------------------------------------------------------- export and backup target


def _mark(root, when):
    root.mkdir(parents=True, exist_ok=True)
    (root / backup.LAST_RUN).write_text(when.isoformat())


async def _old_mail(users):
    """Mail accounts older than a day: no first-export grace (test_export_first_run covers it)."""
    return [datetime.now(UTC) - timedelta(days=3) for _ in users]


async def test_export_check(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    users = [101, 102]

    async def mail_users():
        return users

    monkeypatch.setattr(health, "_mail_users", mail_users)
    monkeypatch.setattr(health, "_mail_since", _old_mail)
    now = datetime.now(UTC)
    _mark(backup.export_root(101), now - timedelta(hours=2))
    # 102 never exported.
    assert await health.check_export() == (False, "1 of 2 accounts not exported in 26 h")
    _mark(backup.export_root(102), now - timedelta(hours=27))
    assert await health.check_export() == (False, "1 of 2 accounts not exported in 26 h")
    _mark(backup.export_root(102), now - timedelta(hours=1))
    assert await health.check_export() == (True, "2 accounts exported in the last 26 h")
    users.clear()
    assert await health.check_export() == (True, "no mail yet")


async def test_backup_target_check(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path / "data"))

    async def mail_users():
        return [101]

    monkeypatch.setattr(health, "_mail_users", mail_users)
    monkeypatch.setattr(health, "_mail_since", _old_mail)
    monkeypatch.setattr(get_settings(), "backup_target", "")
    assert await health.check_backup_target() == (True, "not configured")

    nas = tmp_path / "nas"
    monkeypatch.setattr(get_settings(), "backup_target", str(nas))
    assert await health.check_backup_target() == (False, "backup target missing")
    nas.mkdir()
    assert await health.check_backup_target() == (False, "backup target not mounted (no .mailr-backup-target file)")
    (nas / backup.TARGET_MARKER).write_text("")
    assert await health.check_backup_target() == (False, "1 of 1 account not copied in 26 h")
    _mark(backup.target_root(101), datetime.now(UTC) - timedelta(hours=30))
    assert (await health.check_backup_target())[0] is False
    _mark(backup.target_root(101), datetime.now(UTC))
    assert await health.check_backup_target() == (True, "1 account copied in the last 26 h")
    assert not (nas / health.PROBE).exists()

    nas.chmod(0o555)
    try:
        assert await health.check_backup_target() == (False, "backup target not writable")
    finally:
        nas.chmod(0o755)


async def test_export_run_writes_markers(app, user, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(get_settings(), "backup_target", str(tmp_path / "nas"))
    (tmp_path / "nas").mkdir()
    (tmp_path / "nas" / backup.TARGET_MARKER).write_text("")
    assert backup.last_run(backup.export_root(user.id)) is None
    assert await backup.export_user(user.id) == 0  # no mail: caught up at once
    local = backup.last_run(backup.export_root(user.id))
    assert local and (datetime.now(UTC) - local).total_seconds() < 60
    assert backup.last_run(backup.target_root(user.id)) == local


async def test_mail_users_lists_users_with_mail(app, user, raw):
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.store import user_store
    from mailr.models import User

    assert user.id not in await health._mail_users()
    async with session_scope() as db:
        u = await db.get(User, user.id)
        await ingest_raw(db, await user_store(db, u), raw("Health"), IngestOptions())
    assert user.id in await health._mail_users()


# ---------------------------------------------------------------- outbound heartbeats


class _Monitor:
    """A fake push monitor on 127.0.0.1 that records each request's query."""

    def __init__(self, status=200):
        self.hits: list[dict] = []
        self.status = status
        mon = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                mon.hits.append({"path": urlsplit(self.path).path, **{
                    k: v for k, v in parse_qs(urlsplit(self.path).query, keep_blank_values=True).items()}})
                self.send_response(mon.status)
                self.end_headers()
                self.wfile.write(json.dumps({"ok": mon.status < 400}).encode())

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/push/tok"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def monitor():
    m = _Monitor()
    yield m
    m.close()


def test_heartbeat_url_replaces_status_and_msg():
    u = health.heartbeat_url("https://kuma.example.com/api/push/abc?status=up&msg=OK&ping=", "down", "sync: x")
    q = parse_qs(urlsplit(u).query, keep_blank_values=True)
    assert q == {"ping": [""], "status": ["down"], "msg": ["sync: x"]}
    assert urlsplit(u).path == "/api/push/abc"


def test_heartbeat_msg_is_one_short_plain_string():
    """Uptime Kuma showed "[object Object]": the monitor reads msg with a nested-key query parser,
    so only one plain msg (and status) may reach it, whatever the configured URL carries."""
    u = health.heartbeat_url("https://kuma.example.com/api/push/abc?msg[text]=x&msg.a=b&Status[]=up&ping=&msg=OK",
                             "up", "OK")
    pairs = parse_qsl(urlsplit(u).query, keep_blank_values=True)
    assert pairs == [("ping", ""), ("status", "up"), ("msg", "OK")]
    for odd in ({"a": 1}, None, "", "  \n\t "):
        assert health.plain_msg(odd) == "OK"
    assert health.plain_msg("sync,\n export\u202e") == "sync, export"
    assert len(health.plain_msg("x" * 500)) == health.MSG_MAX
    result = {"ok": False, "checks": {"db": {"ok": True, "required": True}, "sync": {"ok": False, "required": True},
                                      "push": {"ok": False, "required": False},
                                      "export": {"ok": False, "required": True}}}
    assert health.summary(result) == ("down", "sync, export")
    assert health.summary({"ok": True, "checks": {}}) == ("up", "OK")


async def test_heartbeat_redirect_is_a_failure(monkeypatch):
    """Redirects are not followed, so a 3xx answer means the push never arrived."""
    moved = _Monitor(status=301)
    try:
        monkeypatch.setattr(get_settings(), "heartbeat_url", moved.url)
        _all_ok(monkeypatch)
        assert await health.worker_heartbeat() is False
    finally:
        moved.close()


async def test_worker_heartbeat_up_and_down(monitor, monkeypatch):
    monkeypatch.setattr(get_settings(), "heartbeat_url", monitor.url + "?status=up&msg=OK&ping=")
    _all_ok(monkeypatch)
    assert await health.worker_heartbeat() is True
    _all_ok(monkeypatch, sync=_fake(False, "1 account not syncing"), push=_fake(False, "push server unreachable"))
    assert await health.worker_heartbeat() is True
    up, down = monitor.hits
    assert up["status"] == ["up"] and up["msg"] == ["OK"] and up["ping"] == [""]
    assert down["status"] == ["down"] and down["msg"] == ["sync"]  # push is optional
    _all_ok(monkeypatch, sync=_fake(False, "1 account not syncing"), export=_fake(False, "1 of 1 not exported"))
    assert await health.worker_heartbeat() is True
    assert monitor.hits[-1]["msg"] == ["sync, export"]


async def test_backup_heartbeat_only_when_export_passes(monitor, monkeypatch):
    monkeypatch.setattr(get_settings(), "backup_heartbeat_url", monitor.url)
    monkeypatch.setattr(get_settings(), "backup_target", "/backup")
    _all_ok(monkeypatch, export=_fake(False, "1 of 1 account not exported in 26 h"))
    assert await health.backup_heartbeat() is False
    assert monitor.hits == []
    _all_ok(monkeypatch)
    assert await health.backup_heartbeat() is True
    assert monitor.hits[0]["status"] == ["up"] and monitor.hits[0]["msg"] == ["export ok and copied"]

    # after_export starts it in the background, at most once an hour.
    health.after_export()
    await health._backup_pacer.task
    health.after_export()
    assert len(monitor.hits) == 2


async def test_heartbeat_failures_are_logged_once_an_hour(monkeypatch, caplog):
    bad = _Monitor(status=500)
    try:
        caplog.set_level(logging.WARNING, logger="mailr.health")
        assert await health.send("worker", bad.url, "up", "OK") is False
        assert await health.send("worker", bad.url, "up", "OK") is False
        assert len(bad.hits) == 2
    finally:
        bad.close()
    # Unreachable, and refused by the outbound guard: no exception either.
    assert await health.send("worker", "http://127.0.0.1:9/push", "up", "OK") is False
    assert await health.send("backup", "http://169.254.169.254/push", "up", "OK") is False
    msgs = [r.getMessage() for r in caplog.records if r.name == "mailr.health"]
    assert len([m for m in msgs if m.startswith("worker heartbeat failed")]) == 1
    assert len([m for m in msgs if m.startswith("backup heartbeat failed")]) == 1
    assert not any("/push" in m for m in msgs)  # the URL (with its token) is never logged
