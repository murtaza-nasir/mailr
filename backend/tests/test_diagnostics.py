"""Admin → Diagnostics, the endpoint Test button and per-task last error, and
account health for members: api/admin_routes.diagnostics and test_endpoint,
intel/modelhealth.py, core/healthlog.py, core/accounthealth.py."""

import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from mailr.config import get_settings
from mailr.core import accounthealth, health, healthlog
from mailr.db import sessionmaker
from mailr.models import Connector, ModelEndpoint


@pytest.fixture(autouse=True)
def _fresh():
    health.clear_cache()
    healthlog.reset()
    yield
    health.clear_cache()
    healthlog.reset()


# ---------------------------------------------------------------- diagnostics


async def test_diagnostics_lists_every_check_with_a_plain_name(admin_client):
    r = await admin_client.get("/api/v1/admin/diagnostics", params={"refresh": "true"})
    assert r.status_code == 200, r.text
    d = r.json()
    names = [c["name"] for c in d["checks"]]
    assert names == list(health.CHECKS)
    by = {c["name"]: c for c in d["checks"]}
    assert by["db"]["label"] == "Database" and by["db"]["ok"] is True
    assert set(d) >= {"backup", "push", "queue", "connectors", "models", "proxy", "checked_at"}
    assert {"queued", "running", "due", "oldest_due_seconds", "failed_last_day"} <= set(d["queue"])
    assert d["proxy"]["trusted"][0] == "127.0.0.1"


async def test_a_check_added_later_shows_under_its_own_name(admin_client, monkeypatch):
    async def extra():
        return False, "something is off"

    monkeypatch.setattr(health, "CHECKS", (*health.CHECKS, "db_backup"))
    monkeypatch.setitem(health.CHECK_FUNCS, "db_backup", extra)
    d = (await admin_client.get("/api/v1/admin/diagnostics", params={"refresh": "true"})).json()
    row = next(c for c in d["checks"] if c["name"] == "db_backup")
    assert row == {"name": "db_backup", "label": "Db backup", "ok": False, "detail": "something is off", "required": True}
    assert d["ok"] is False


async def test_unmounted_backup_share_is_a_failure_not_silence(admin_client, monkeypatch, tmp_path, caplog):
    share = tmp_path / "nas"
    share.mkdir()  # the mount point exists, the share is not mounted: no marker file
    monkeypatch.setattr(get_settings(), "backup_target", str(share))
    d = (await admin_client.get("/api/v1/admin/diagnostics", params={"refresh": "true"})).json()
    row = next(c for c in d["checks"] if c["name"] == "backup_target")
    assert row["ok"] is False and "not mounted" in row["detail"]
    assert d["backup"]["target_configured"] is True and "not mounted" in d["backup"]["target_problem"]
    assert d["backup"]["marker"] == ".mailr-backup-target"
    # The export run says so in the log too, once an hour at most.
    from mailr.core import backup

    monkeypatch.setattr(backup, "_target_warned", 0.0)
    with caplog.at_level("WARNING", logger="mailr.backup"):
        assert backup._ready_target(1) is None
        assert backup._ready_target(2) is None
    assert sum("MAILR_BACKUP_TARGET" in r.message for r in caplog.records) == 1


async def test_members_cannot_open_diagnostics(client):
    assert (await client.get("/api/v1/admin/diagnostics")).status_code == 403


async def test_diagnostics_shows_push_and_account_health(admin_client, user):
    async with sessionmaker()() as db:
        db.add(Connector(user_id=user.id, kind="imap", name=f"Work {uuid.uuid4().hex[:4]}", mode="mirror",
                         enabled=True, status="error",
                         status_detail="IMAPClientError: b'[AUTHENTICATIONFAILED] Invalid credentials'",
                         config={}))
        await db.commit()
    from mailr.core import push

    await healthlog.note(push.PUSH_HEALTH, ["transport:webpush"], False, "the push server answered 503")
    d = (await admin_client.get("/api/v1/admin/diagnostics")).json()
    mine = [c for c in d["connectors"] if c["user"] == user.email]
    assert mine and mine[0]["health"] == {"state": "signin", "text": "Password required", "code": "account.signin",
                                          "params": {}}
    wp = d["push"]["transports"]["webpush"]
    assert wp["ok"] is False and wp["error"] == "the push server answered 503"


# ---------------------------------------------------------------- endpoint Test button


class _Model(BaseHTTPRequestHandler):
    status = 200

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        type(self).last = body
        self.send_response(self.status)
        self.send_header("content-type", "application/json")
        self.end_headers()
        if self.status == 200:
            self.wfile.write(json.dumps({"choices": [{"message": {"role": "assistant", "content": "OK"}}]}).encode())
        else:
            self.wfile.write(b'{"error": "bad key"}')

    def log_message(self, *a):
        pass


@pytest.fixture
def model_server():
    handler = type("H", (_Model,), {"status": 200})
    srv = HTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, handler
    srv.shutdown()


async def _endpoint(base: str, **kw) -> int:
    async with sessionmaker()() as db:
        e = ModelEndpoint(name=f"ep-{uuid.uuid4().hex[:6]}", base_url=base, locality="local", adapter="openai",
                          models=["tiny"], defaults=kw.get("defaults", {"chat": "tiny"}), enabled=True)
        db.add(e)
        await db.commit()
        return e.id


@pytest.fixture
def lan_ok(monkeypatch):
    from mailr.api import admin_routes

    async def ok(url):
        return url.rstrip("/")

    monkeypatch.setattr(admin_routes, "_safe_base", ok)


async def test_test_button_sends_a_tiny_prompt_and_reports_latency(admin_client, model_server, lan_ok):
    srv, handler = model_server
    eid = await _endpoint(f"http://127.0.0.1:{srv.server_port}/v1")
    r = await admin_client.post(f"/api/v1/admin/models/{eid}/test", json={})
    d = r.json()
    assert d["ok"] is True and d["kind"] == "chat" and d["model"] == "tiny" and d["reply"] == "OK"
    assert isinstance(d["latency_ms"], int)
    assert handler.last["max_tokens"] == 5 and handler.last["model"] == "tiny"
    eps = {e["id"]: e for e in (await admin_client.get("/api/v1/admin/models")).json()}
    assert eps[eid]["health"]["ok"] is True and eps[eid]["health"]["ok_at"]


async def test_test_button_says_what_is_wrong_in_plain_words(admin_client, model_server, lan_ok):
    srv, handler = model_server
    handler.status = 401
    eid = await _endpoint(f"http://127.0.0.1:{srv.server_port}/v1")
    d = (await admin_client.post(f"/api/v1/admin/models/{eid}/test", json={})).json()
    assert d["ok"] is False and d["error"] == "The API key was refused."
    # Nothing listens on port 9 (discard): the server did not answer.
    gone = await _endpoint("http://127.0.0.1:9/v1")
    d = (await admin_client.post(f"/api/v1/admin/models/{gone}/test", json={})).json()
    assert d["ok"] is False and d["error"].startswith("No answer")
    eps = {e["id"]: e for e in (await admin_client.get("/api/v1/admin/models")).json()}
    assert eps[eid]["health"]["ok"] is False and eps[eid]["health"]["error"] == "The API key was refused."


async def test_test_button_without_a_model_name(admin_client, lan_ok):
    async with sessionmaker()() as db:
        e = ModelEndpoint(name=f"bare-{uuid.uuid4().hex[:6]}", base_url="http://127.0.0.1:9/v1", locality="local",
                          adapter="openai", models=[], defaults={}, enabled=True)
        db.add(e)
        await db.commit()
        eid = e.id
    d = (await admin_client.post(f"/api/v1/admin/models/{eid}/test", json={})).json()
    assert d["ok"] is False and "No model name" in d["error"]


async def test_each_task_shows_its_last_error(admin_client, model_server, user):
    """A failing chat call is noted for the task; Models per task shows it in plain words."""
    from mailr.intel import llm

    srv, handler = model_server
    handler.status = 404
    from sqlalchemy import delete

    async with sessionmaker()() as db:  # endpoints are server-wide: only this one may answer
        await db.execute(delete(ModelEndpoint))
        await db.commit()
    await _endpoint(f"http://127.0.0.1:{srv.server_port}/v1")
    from conftest import grant_model_access

    await grant_model_access(user.id)
    async with sessionmaker()() as db:
        u = await db.get(type(user), user.id)
        with pytest.raises(RuntimeError):
            await llm.chat(db, u, [{"role": "user", "content": "hi"}], sensitive=False)
    rows = {t["task"]: t for t in (await admin_client.get("/api/v1/admin/models/defaults")).json()["tasks"]}
    h = rows["chat"]["health"]
    assert h["ok"] is False and h["error"].startswith("Wrong address")
    handler.status = 200
    async with sessionmaker()() as db:
        u = await db.get(type(user), user.id)
        await llm.chat(db, u, [{"role": "user", "content": "hi"}], sensitive=False)
    rows = {t["task"]: t for t in (await admin_client.get("/api/v1/admin/models/defaults")).json()["tasks"]}
    assert rows["chat"]["health"]["ok"] is True and rows["chat"]["health"]["error"]  # the last error stays visible


def test_plain_errors():
    import httpx

    from mailr.intel.modelhealth import plain

    assert plain(httpx.ConnectError("x")).startswith("No answer")
    assert plain(httpx.ReadTimeout("x")) == "The server timed out."
    assert plain(None, 401) == "The API key was refused."
    assert plain(None, 404, '{"error": "model not found"}') == "Unknown model name."
    assert plain(RuntimeError("model endpoint x returned 503: busy")) == "Server error (503)."
    assert plain(None, 429) == "The server is busy or out of quota (429)."


# ---------------------------------------------------------------- account health


def _c(**kw):
    base = dict(enabled=True, status="idle", status_detail="", last_sync_at=datetime.now(UTC), mode="mirror", config={})
    return SimpleNamespace(**{**base, **kw})


def test_account_health_states():
    now = datetime.now(UTC)
    assert accounthealth.of(_c(), now)["state"] == "ok"
    assert accounthealth.of(_c(enabled=False), now)["text"] == "Paused"
    assert accounthealth.of(_c(status="error", status_detail="LoginError: Invalid credentials"), now)["state"] == "signin"
    assert accounthealth.of(_c(status="error", status_detail="OSError: timed out"), now)["state"] == "error"
    assert accounthealth.of(_c(status="new", last_sync_at=None), now)["text"] == "Syncing for the first time"
    stale = accounthealth.of(_c(last_sync_at=now - timedelta(hours=5)), now)
    assert stale == {"state": "stale", "text": "Not synced for 5 hours", "code": "account.stale_hours", "params": {"count": 5}}
    rows = [{"name": "Home", "health": {"state": "ok", "text": "Up to date"}},
            {"name": "Work", "health": {"state": "signin", "text": "Password required"}}]
    assert accounthealth.summary(rows)["text"] == "Work: Password required."
    assert accounthealth.summary(rows[:1])["text"] == "Your account is syncing."


async def test_members_see_their_accounts_health(client, user):
    async with sessionmaker()() as db:
        db.add(Connector(user_id=user.id, kind="imap", name="Home", mode="mirror", enabled=True, status="idle",
                         last_sync_at=datetime.now(UTC), config={}))
        await db.commit()
    rows = (await client.get("/api/v1/connectors")).json()
    home = next(c for c in (rows["items"] if isinstance(rows, dict) else rows) if c["name"] == "Home")
    assert home["health"] == {"state": "ok", "text": "Up to date", "code": "account.ok", "params": {}}


async def test_password_needed_notifies_the_owner_once(user, monkeypatch):
    from mailr.connectors.runner import RunnerContext
    from mailr.core import push

    sent = []

    async def fan_out(db, user_id, event, title, body, data, priority="default", **kw):
        sent.append((event, title, kw.get("private")))
        return 1

    monkeypatch.setattr(push, "fan_out", fan_out)
    async with sessionmaker()() as db:
        c = Connector(user_id=user.id, kind="imap", name="Work mail", mode="mirror", enabled=True, status="idle",
                      config={})
        db.add(c)
        await db.commit()
        cid = c.id
    ctx = RunnerContext(cid, user.id, {})
    await ctx.set_status("error", "LoginError: b'[AUTHENTICATIONFAILED] Invalid credentials'")
    await ctx.set_status("error", "LoginError: b'[AUTHENTICATIONFAILED] Invalid credentials'")  # the retry
    assert sent == [("account", "Work mail: password required", False)]
    await ctx.set_status("idle", "", synced=True)
    await ctx.set_status("error", "LoginError: invalid credentials")
    assert len(sent) == 2
