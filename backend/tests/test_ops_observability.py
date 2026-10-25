"""Operations (audit of 13 Oct 2026): the persistent schedule, the access line, JSON logs, error
report scrubbing and the web app's error relay, the disk and failed-job readiness checks."""

import asyncio
import json
import logging
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from mailr.config import get_settings
from mailr.core import errors, health, logsetup, tasks
from mailr.db import session_scope

# ---------------------------------------------------------------- persistent schedule


async def test_schedule_claim_survives_a_restart_and_runs_once(app):
    name = "test-claim-task"
    async with session_scope() as db:
        await db.execute(text("DELETE FROM schedule_runs WHERE name = :n"), {"n": name})
    every = timedelta(hours=24)
    assert await tasks.claim(name, every) is True  # never ran: due
    # A restart keeps nothing in memory: the database still says it ran.
    assert await tasks.claim(name, every) is False
    # Two workers at once (an overlapping deploy): one of them gets it.
    async with session_scope() as db:
        await db.execute(text("UPDATE schedule_runs SET last_run_at = now() - interval '25 hours' WHERE name = :n"),
                         {"n": name})
    got = await asyncio.gather(*(tasks.claim(name, every) for _ in range(4)))
    assert sorted(got) == [False, False, False, True]
    # A tick a few seconds early still counts (SLACK_SECONDS).
    async with session_scope() as db:
        await db.execute(text("UPDATE schedule_runs SET last_run_at = now() - interval '58 seconds' WHERE name = :n"),
                         {"n": name})
    assert await tasks.claim(name, timedelta(minutes=1)) is True
    async with session_scope() as db:
        await db.execute(text("DELETE FROM schedule_runs WHERE name = :n"), {"n": name})


def test_every_periodic_task_has_a_unique_name():
    names = [fn.__name__ for _, fn in tasks.PERIODIC]
    assert len(names) == len(set(names))
    assert "_db_dump_due" in names


def test_maintenance_kinds_have_their_own_lane():
    from mailr.core import jobs

    for k in ("export.user", "intel.train_all", "people.rescore", "db.dump"):
        assert k in jobs.MAINTENANCE_KINDS
    for k in ("outbox.send", "reminders.fire", "snooze.wake"):
        assert k not in jobs.MAINTENANCE_KINDS


# ---------------------------------------------------------------- access line and JSON logs


async def test_access_line_has_the_route_template_and_never_the_query(app, db_user, caplog):
    caplog.set_level(logging.DEBUG, logger="mailr.access")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/login", json={"email": db_user.email, "password": "test-password-123"})
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        r = await c.get("/api/v1/conversations", params={"q": "secret-search-term from:alice@example.com"})
        assert r.status_code == 200
        await c.get("/api/v1/health")
    lines = [r for r in caplog.records if r.name == "mailr.access"]
    conv = [r for r in lines if getattr(r, "route", "") == "/api/v1/conversations"]
    assert conv, [r.getMessage() for r in lines]
    rec = conv[-1]
    assert rec.method == "GET" and rec.status == 200 and rec.user == db_user.id and rec.ms >= 0
    joined = " ".join(r.getMessage() + json.dumps({k: str(v) for k, v in vars(r).items()}) for r in lines)
    assert "secret-search-term" not in joined and "alice@example.com" not in joined
    health_lines = [r for r in lines if getattr(r, "route", "") == "/api/v1/health"]
    assert all(r.levelno == logging.DEBUG for r in health_lines)


def test_json_formatter_carries_extra_fields():
    rec = logging.LogRecord("mailr.access", logging.INFO, __file__, 1, "%s %s", ("GET", "/x"), None)
    rec.route = "/x"
    rec.status = 200
    out = json.loads(logsetup.JsonFormatter().format(rec))
    assert out["msg"] == "GET /x" and out["route"] == "/x" and out["status"] == 200 and out["level"] == "INFO"


def test_configure_writes_rotating_files(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "log_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "log_format", "json")
    logsetup.reset_for_tests()
    try:
        logsetup.configure("worker")
        logging.getLogger("mailr.test").warning("hello %s", "file")
        for h in logging.getLogger().handlers:
            h.flush()
        line = (tmp_path / "worker.log").read_text().strip().splitlines()[-1]
        assert json.loads(line)["msg"] == "hello file"
    finally:
        logsetup.reset_for_tests()


# ---------------------------------------------------------------- error reports


def test_scrub_removes_everything_personal():
    ev = {
        "request": {"method": "POST", "url": "https://mail.example/api/v1/conversations?q=tax+refund",
                    "headers": {"Cookie": "s=1", "Authorization": "Bearer abc"}, "data": {"body": "mail text"},
                    "query_string": "q=tax+refund", "cookies": {"s": "1"}},
        "user": {"id": 7, "email": "a@b.example", "ip_address": "10.0.0.1"},
        "breadcrumbs": {"values": [{"message": "x"}]},
        "extra": {"payload": "mail"},
        "logentry": {"message": "send to %s failed", "params": ["bob@example.org"]},
        "exception": {"values": [{"type": "ValueError", "value": "bad address carol@example.net token=xyz",
                                  "stacktrace": {"frames": [{"function": "f", "vars": {"body": "secret"}}]}}]},
    }
    out = errors.scrub(ev)
    assert out["request"] == {"method": "POST", "url": "https://mail.example/api/v1/conversations"}
    assert out["user"] == {"id": "7"}
    assert "breadcrumbs" not in out and "extra" not in out
    blob = json.dumps(out)
    for leaked in ("tax", "Bearer", "mail text", "bob@", "carol@", "xyz", "secret", "10.0.0.1"):
        assert leaked not in blob, leaked


async def test_error_config_and_relay(app, monkeypatch):
    from mailr.api import error_routes

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        monkeypatch.setattr(get_settings(), "sentry_dsn", "")
        monkeypatch.setattr(get_settings(), "sentry_browser_dsn", "")
        assert (await c.get("/api/v1/errors/config")).json() == {"dsn": None}
        assert (await c.post("/api/v1/errors/envelope", content=b"{}\n")).status_code == 404

        dsn = "http://pubkey@glitchtip:8000/3"
        monkeypatch.setattr(get_settings(), "sentry_browser_dsn", dsn)
        monkeypatch.setattr(get_settings(), "release", "1.2.3")
        cfg = (await c.get("/api/v1/errors/config")).json()
        assert cfg["dsn"] == dsn and cfg["release"] == "mailr@1.2.3"

        sent = []

        class FakeClient:
            def __init__(self, *a):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, content=None, params=None, headers=None):
                sent.append((url, content, params))

                class R:
                    status_code = 200
                return R()

        monkeypatch.setattr(error_routes, "_client", FakeClient)
        event = {"event_id": "a" * 32, "request": {"url": "https://m.example/inbox?q=private"},
                 "exception": {"values": [{"type": "TypeError", "value": "x of dave@example.com"}]},
                 "breadcrumbs": [{"message": "clicked"}]}
        payload = json.dumps(event).encode()
        env = b"\n".join([json.dumps({"dsn": dsn, "event_id": "a" * 32}).encode(),
                          json.dumps({"type": "event", "length": len(payload)}).encode(), payload,
                          json.dumps({"type": "session"}).encode(), b'{"sid": "1"}']) + b"\n"
        assert (await c.post("/api/v1/errors/envelope", content=env)).status_code == 401  # no session, no key
        r = await c.post(cfg["tunnel"], content=env)
        assert r.status_code == 200, r.text
        assert len(sent) == 1
        url, body, params = sent[0]
        assert url == "http://glitchtip:8000/api/3/envelope/" and params["sentry_key"] == "pubkey"
        header, items = error_routes.parse_envelope(body)
        assert header["dsn"] == dsn and [ih["type"] for ih, _ in items] == ["event"]
        out = json.loads(items[0][1])
        assert out["request"]["url"] == "https://m.example/inbox" and "dave@" not in json.dumps(out)
        assert "breadcrumbs" not in out and out["tags"]["process"] == "web"

        # Another DSN in the envelope: refused, nothing relayed.
        other = json.dumps({"dsn": "http://k@evil.example/1"}).encode() + b"\n"
        assert (await c.post(cfg["tunnel"], content=other)).status_code == 403
        too_big = b"{}\n" + b"x" * (error_routes.ENVELOPE_MAX + 1)
        assert (await c.post(cfg["tunnel"], content=too_big)).status_code == 413
        assert len(sent) == 1


# ---------------------------------------------------------------- readiness additions


def test_disk_evaluation():
    assert health.evaluate_disk([("data", 50.0), ("backup", 40.0)], 15, 5)[:2][0] is True
    low = health.evaluate_disk([("data", 50.0), ("backup", 12.0)], 15, 5)
    assert low[0] is True and low[2] is True and "backup" in str(low[1])
    full = health.evaluate_disk([("data", 3.0)], 15, 5)
    assert full[0] is False
    assert health.evaluate_disk([], 15, 5)[0] is False


async def test_disk_check_reads_the_data_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "backup_target", "")
    _, detail, *_ = await health.check_disk()
    assert "data" in str(detail) or "backup" in str(detail)


async def test_warning_shows_in_the_result_and_never_fails_it(monkeypatch):
    async def warn():
        return False, "2 jobs failed in the last hour", True

    async def fine():
        return True, "fine"

    funcs = {n: fine for n in health.CHECKS}
    funcs["jobs_failed"] = warn
    monkeypatch.setattr(health, "CHECK_FUNCS", funcs)
    res = await health.run_checks()
    assert res["ok"] is True
    assert res["checks"]["jobs_failed"] == {"ok": False, "detail": "2 jobs failed in the last hour",
                                            "required": False, "warn": True}


async def test_jobs_failed_counts_the_last_hour(app):
    async with session_scope() as db:
        await db.execute(text("INSERT INTO jobs (kind, payload, status, run_at, attempts, max_attempts, error, created_at) "
                              "VALUES ('test.fail', '{}', 'failed', now(), 5, 5, '', now())"))
    try:
        r = await health.check_jobs_failed()
        assert r[0] is False and r[2] is True
    finally:
        async with session_scope() as db:
            await db.execute(text("DELETE FROM jobs WHERE kind = 'test.fail'"))


def test_worker_heartbeat_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MAILR_HEARTBEAT_FILE", str(tmp_path / "hb"))
    assert health.worker_alive() is False
    health.touch_heartbeat_file()
    assert health.worker_alive() is True


@pytest.mark.parametrize("name", ["db_dump", "disk", "jobs_failed"])
def test_new_checks_have_names(name):
    assert name in health.CHECKS and health.label(name) != name
