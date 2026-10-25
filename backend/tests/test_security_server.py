"""Regression tests for the server and its deployment
(request sizes, headers, streams, crypto, file modes, deploy configuration)."""

from __future__ import annotations

import asyncio
import base64
import os
import stat
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

REPO = Path(__file__).resolve().parents[2]


async def _asgi_post(app, path: str, headers: dict[str, str], chunks: list[bytes]) -> tuple[int, int]:
    """POST through the raw ASGI interface: (status, number of body chunks the app pulled)."""
    pulled = 0
    sent = list(chunks)
    status = 0

    async def receive():
        nonlocal pulled
        if sent:
            pulled += 1
            body = sent.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(sent)}
        await asyncio.sleep(3600)

    async def send(msg):
        nonlocal status
        if msg["type"] == "http.response.start":
            status = msg["status"]

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
             "client": ("127.0.0.1", 1), "server": ("test", 80)}
    await asyncio.wait_for(app(scope, receive, send), 30)
    return status, pulled


# ---------------- Request sizes ----------------

async def test_speakr_webhook_refuses_a_large_content_length_before_reading(app):
    status, pulled = await _asgi_post(app, "/api/v1/integrations/speakr/abc/webhook",
                                      {"content-length": "10000000", "content-type": "application/json"},
                                      [b"x" * 1024])
    assert status == 413
    assert pulled == 0


async def test_speakr_webhook_stops_reading_a_chunked_body_past_the_cap(app):
    from mailr.integrations.speakr.webhook import MAX_BODY

    chunk = b"x" * 16384
    chunks = [chunk] * 64  # 1 MB, chunked (no Content-Length)
    status, pulled = await _asgi_post(app, "/api/v1/integrations/speakr/abc/webhook",
                                      {"content-type": "application/json", "transfer-encoding": "chunked"}, chunks)
    assert status == 413
    assert pulled * len(chunk) <= MAX_BODY + len(chunk)


async def test_global_request_limit_refuses_oversized_bodies(app, monkeypatch):
    from mailr.config import get_settings

    monkeypatch.setattr(get_settings(), "max_request_bytes", 1000)
    status, pulled = await _asgi_post(app, "/api/v1/auth/login",
                                      {"content-length": "5000", "content-type": "application/json"}, [b"{}"])
    assert status == 413 and pulled == 0
    # Chunked: refused once the count passes the limit.
    status, pulled = await _asgi_post(app, "/api/v1/auth/login",
                                      {"content-type": "application/json"}, [b" " * 600] * 10)
    assert status == 413 and pulled <= 2


def test_upload_routes_keep_their_larger_limits(monkeypatch):
    from mailr.main import body_limit

    monkeypatch.setenv("MAILR_MAX_IMPORT_BYTES", str(5 * 1024**3))
    monkeypatch.setenv("MAILR_SPEAKR_MAX_UPLOAD_MB", "300")
    assert body_limit("POST", "/api/v1/connectors/uploads") > 5 * 1024**3
    assert body_limit("POST", "/api/v1/speakr/recordings") > 300 * 1024**2
    # An attachment: MAILR_MAX_ATTACHMENT_BYTES and the multipart framing.
    assert body_limit("POST", "/api/v1/uploads") == 36 * 1024 * 1024
    assert body_limit("PUT", "/api/v1/connectors/uploads") == 50 * 1024 * 1024


async def test_gmail_filter_import_is_capped(client):
    big = b"<feed>" + b" " * (2 * 1024 * 1024 + 10) + b"</feed>"
    r = await client.post("/api/v1/rules/import-gmail", files={"file": ("f.xml", big, "text/xml")})
    assert r.status_code == 413


# ---------------- Page headers ----------------

def test_shell_csp_blocks_framing_forms_and_remote_images():
    from mailr.main import shell_csp

    csp = shell_csp(False)
    assert "frame-ancestors 'none'" in csp and "form-action 'self'" in csp
    img = csp.split("img-src ", 1)[1].split(";", 1)[0]
    assert "https:" not in img and "'self'" in img
    # With the image proxy off, the reader loads allowed images directly.
    assert "https:" in shell_csp(True).split("img-src ", 1)[1].split(";", 1)[0]


# ---------------- Live-update streams ----------------

def test_live_streams_per_user_are_capped(monkeypatch):
    from mailr.config import get_settings
    from mailr.main import _STREAM_CLOSE, _STREAMS, _close_stream, _open_stream

    monkeypatch.setattr(get_settings(), "sse_max_per_user", 3)
    qs = [asyncio.Queue() for _ in range(5)]
    for q in qs:
        _open_stream(4242, q)
    assert len(_STREAMS[4242]) == 3
    # The two oldest were told to end; the newest keep running.
    assert qs[0].get_nowait() is _STREAM_CLOSE and qs[1].get_nowait() is _STREAM_CLOSE
    assert qs[4].empty()
    for q in qs:
        _close_stream(4242, q)
    assert 4242 not in _STREAMS


# ---------------- Crypto ----------------

def test_weak_instance_keys_are_refused(monkeypatch):
    from mailr.core.crypto import WeakInstanceKey, check_instance_key

    monkeypatch.delenv("MAILR_ALLOW_WEAK_SECRET_KEY", raising=False)
    for weak in ["hunter2", "correct horse battery staple", base64.b64encode(b"short").decode(),
                 base64.b64encode(b"a" * 40).decode()]:
        with pytest.raises(WeakInstanceKey):
            check_instance_key(weak)
    check_instance_key(base64.b64encode(os.urandom(32)).decode())
    # The documented escape for an existing install: a warning instead of a refusal.
    monkeypatch.setenv("MAILR_ALLOW_WEAK_SECRET_KEY", "true")
    check_instance_key("hunter2")


def test_connector_secrets_are_bound_to_their_connector():
    from mailr.core.crypto import (
        connector_context,
        decrypt_for_user,
        encrypt_for_user,
        is_legacy,
        new_wrapped_data_key,
    )

    wrapped = new_wrapped_data_key(7)
    a = encrypt_for_user(7, wrapped, {"password": "for-A"}, context=connector_context(1))
    assert not is_legacy(a)
    assert decrypt_for_user(7, wrapped, a, context=connector_context(1)) == {"password": "for-A"}
    # Moved onto another connector, it no longer opens.
    with pytest.raises(ValueError):
        decrypt_for_user(7, wrapped, a, context=connector_context(2))
    # A rewritten context byte breaks the authentication tag.
    forged = bytearray(a)
    forged[2 + len(connector_context(1)) - 1] = ord("2")
    with pytest.raises(Exception):  # noqa: B017
        decrypt_for_user(7, wrapped, bytes(forged), context=connector_context(2))
    # Values written before contexts existed are read only without a context (the startup
    # upgrade); where a context is required they are refused.
    old = encrypt_for_user(7, wrapped, {"password": "old"})
    assert is_legacy(old) and decrypt_for_user(7, wrapped, old) == {"password": "old"}
    with pytest.raises(ValueError):
        decrypt_for_user(7, wrapped, old, context=connector_context(1))


async def test_legacy_connector_secrets_are_upgraded(app, user):
    from mailr.connectors.loader import connector_for
    from mailr.core.crypto import encrypt_for_user, is_legacy, upgrade_ciphertexts
    from mailr.db import sessionmaker
    from mailr.models import Connector

    async with sessionmaker()() as db:
        row = Connector(user_id=user.id, kind="imap", name="old", mode="mirror", data_class="normal",
                        status="idle", config={"imap": {"host": "imap.example.org"}})
        db.add(row)
        await db.flush()
        row.secrets_enc = encrypt_for_user(user.id, user.data_key_wrapped, {"password": "pw"})
        await db.commit()
        cid = row.id
    async with sessionmaker()() as db:
        assert await upgrade_ciphertexts(db) >= 1
    async with sessionmaker()() as db:
        row = await db.get(Connector, cid)
        assert not is_legacy(row.secrets_enc)
        assert (await connector_for(db, row)).secrets == {"password": "pw"}
        await db.delete(row)
        await db.commit()


# ---------------- File modes ----------------

def test_process_umask_and_existing_directories(tmp_path, monkeypatch):
    import mailr.cli as cli
    from mailr.config import get_settings

    d = tmp_path / "data"
    (d / "exports" / "user-1").mkdir(parents=True)
    for p in (d, d / "exports", d / "exports" / "user-1"):
        p.chmod(0o755)
    monkeypatch.setattr(get_settings(), "data_dir", str(d))
    monkeypatch.setattr(get_settings(), "backup_target", "")
    monkeypatch.delenv("TMPDIR", raising=False)
    old = os.umask(0o022)
    try:
        cli._prepare_process()
        assert os.umask(0o022) == 0o027
    finally:
        os.umask(old)
    assert stat.S_IMODE((d / "exports").stat().st_mode) & 0o007 == 0
    assert stat.S_IMODE(d.stat().st_mode) & 0o007 == 0
    assert (d / "tmp").is_dir() and stat.S_IMODE((d / "tmp").stat().st_mode) == 0o700
    assert os.environ.get("TMPDIR") == str(d / "tmp")
    monkeypatch.delenv("TMPDIR", raising=False)
    import tempfile

    tempfile.tempdir = None


# ---------------- Deploy configuration ----------------

def _compose() -> dict:
    import yaml

    return yaml.safe_load((REPO / "deploy" / "docker-compose.yml").read_text())


def test_compose_app_and_worker_are_hardened():
    c = _compose()
    for name in ("app", "worker"):
        sv = c["services"][name]
        assert sv["read_only"] is True and any(t.startswith("/tmp:") for t in sv["tmpfs"])
        assert sv["cap_drop"] == ["ALL"]
        env = sv["environment"]
        # Other containers' secrets are blanked over the shared .env.
        for secret in ("POSTGRES_PASSWORD", "STALWART_ADMIN_PASSWORD", "MAILR_SVC_PASSWORD",
                       "MAILR_VPN_WIREGUARD_KEY", "MAILR_EGRESS_PASSWORD"):
            assert env[secret] == "", (name, secret)
    worker = c["services"]["worker"]["environment"]
    assert worker["MAILR_OIDC_CLIENT_SECRET"] == "" and worker["MAILR_BOOTSTRAP_ADMIN_PASSWORD"] == ""
    # The web port is published on loopback unless the operator names an interface.
    assert c["services"]["app"]["ports"] == ["${MAILR_HTTP_BIND:-127.0.0.1}:${MAILR_HTTP_PORT:-8025}:8025"]


def test_compose_stalwart_allow_list_names_only_app_and_worker():
    import ipaddress

    c = _compose()
    net = ipaddress.ip_network(c["networks"]["default"]["ipam"]["config"][0]["subnet"].split(":-")[1].rstrip("}"))
    trusted = ipaddress.ip_network(c["services"]["stalwart"]["environment"]["MAILR_TRUSTED_NET"].split(":-")[1].rstrip("}"))
    app_ip = ipaddress.ip_address(c["services"]["app"]["networks"]["default"]["ipv4_address"].split(":-")[1].rstrip("}"))
    worker_ip = ipaddress.ip_address(c["services"]["worker"]["networks"]["default"]["ipv4_address"].split(":-")[1].rstrip("}"))
    gateway = next(net.hosts())
    assert app_ip in trusted and worker_ip in trusted and trusted.num_addresses == 2
    assert gateway not in trusted and trusted.subnet_of(net)
    ports = c["services"]["stalwart"]["ports"]
    assert any(p.startswith("${MAILR_IMAP_BIND:-127.0.0.1}:") and p.endswith(":143") for p in ports)
    entry = (REPO / "deploy" / "stalwart" / "entrypoint.sh").read_text()
    assert "reconcile_allowlist" in entry.split('echo "[mailr-init] already provisioned"', 1)[1]


def test_image_runs_with_restrictive_modes():
    df = (REPO / "Dockerfile").read_text()
    assert "chmod 0750 /data" in df and "/data/tmp" in df


async def test_page_headers_come_from_webheaders_json(app):
    """core/webheaders.json is the one list: the dev and test servers (vite.config.ts pageHeaders)
    read it too, so every header in it must be what a page gets."""
    import json

    import mailr.main as main
    from mailr.core import webheaders

    main._IMG_DIRECT = (0.0, False)
    table = json.loads(webheaders.FILE.read_text())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:8025") as c:
        page = await c.get("/some/page")
        api = await c.get("/api/v1/health")
    for k, v in {**table["all"], **table["pages"]}.items():
        assert page.headers[k] == v
    for k, v in table["all"].items():
        assert api.headers[k] == v
    assert "x-frame-options" not in api.headers and "content-security-policy" not in api.headers
    assert page.headers["content-security-policy"] == "; ".join(f"{k} {v}" for k, v in table["csp"].items())


# ---------------- Error intake ----------------


def _envelope(event) -> bytes:
    import json

    payload = event if isinstance(event, bytes) else json.dumps(event).encode()
    return b"\n".join([b"{}", json.dumps({"type": "event", "length": len(payload)}).encode(), payload]) + b"\n"


@pytest.fixture
def relay(monkeypatch):
    """Error reporting on, with the error server replaced by a list, and limits of their own."""
    import uuid

    from mailr.api import error_routes
    from mailr.config import get_settings
    from mailr.core import throttle

    monkeypatch.setattr(get_settings(), "sentry_browser_dsn", "http://pubkey@glitchtip:8000/3")
    monkeypatch.setattr(get_settings(), "release", "9.9.9")
    tag = uuid.uuid4().hex[:6]
    from datetime import timedelta

    monkeypatch.setattr(error_routes, "PER_IP", throttle.Limit(f"eip{tag}", 3, timedelta(minutes=10)))
    monkeypatch.setattr(error_routes, "PER_USER", throttle.Limit(f"eus{tag}", 2, timedelta(minutes=10)))
    monkeypatch.setattr(error_routes, "PER_SERVER", throttle.Limit(f"eal{tag}", 100, timedelta(hours=1)))
    monkeypatch.setattr(error_routes, "PER_IP_HOUR", throttle.Limit(f"eih{tag}", 100, timedelta(hours=1)))
    monkeypatch.setattr(error_routes, "PER_KEY", throttle.Limit(f"eky{tag}", 100, timedelta(minutes=10)))
    monkeypatch.setattr(error_routes, "PER_SERVER_SIGNED_OUT", throttle.Limit(f"ean{tag}", 100, timedelta(hours=1)))
    sent: list[bytes] = []

    class FakeClient:
        def __init__(self, *a):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, content=None, params=None, headers=None):
            sent.append(content)

            class R:
                status_code = 200
            return R()

    monkeypatch.setattr(error_routes, "_client", FakeClient)
    return sent


async def test_error_intake_needs_a_session_or_a_key_and_is_limited(app, client, user, relay):
    import json

    from mailr.api import error_routes

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        cfg = (await anon.get("/api/v1/errors/config")).json()
        ok = _envelope({"message": "boom"})
        assert (await anon.post("/api/v1/errors/envelope", content=ok)).status_code == 401
        assert (await anon.post("/api/v1/errors/envelope?k=forged", content=ok)).status_code == 401
        assert not error_routes.tunnel_key_ok(cfg["tunnel"].split("k=")[1].replace("%3D", "="), "198.51.100.7")
        # Malformed shapes and deep nesting are a 400 or are dropped, never a 500.
        bad_tags = _envelope({"message": "x", "tags": ["not", "a", "dict"], "contexts": "x", "exception": 3})
        r = await anon.post(cfg["tunnel"], content=bad_tags)
        assert r.status_code == 200, r.text
        deep = _envelope(b'{"a":' * 5000 + b"1" + b"}" * 5000)
        assert (await anon.post(cfg["tunnel"], content=deep)).status_code == 400
        nested = _envelope({"exception": {"values": [{"value": "x", "mechanism": {"data": [[[[[[[[[[[[[[[[[[[[[[[[[1]]]]]]]]]]]]]]]]]]]]]]]]]}}]}})
        assert (await anon.post(cfg["tunnel"], content=nested)).status_code == 400
        # The address's limit (3 here) holds for signed-out callers.
        for _ in range(2):
            assert (await anon.post(cfg["tunnel"], content=ok)).status_code == 200
        assert (await anon.post(cfg["tunnel"], content=ok)).status_code == 429
    out = json.loads(error_routes.parse_envelope(relay[0])[1][0][1])
    assert out["tags"] == {"process": "web"} and "contexts" in out and "exception" not in out
    assert out["release"] == "mailr@9.9.9" and out["user"] == {}


async def test_error_reports_take_the_user_and_release_from_the_server(app, client, user, relay, monkeypatch):
    import json
    import uuid
    from datetime import timedelta

    from mailr.api import error_routes
    from mailr.core import throttle

    monkeypatch.setattr(error_routes, "PER_IP", throttle.Limit(f"eip{uuid.uuid4().hex[:6]}", 50, timedelta(minutes=10)))
    forged = _envelope({"message": "x", "user": {"id": "999999", "email": "x@example.org"}, "release": "mailr@0.0.1",
                        "extra": {"a": 1}, "server_name": "elsewhere"})
    # A signed-in browser needs no key; its user id comes from the session.
    r = await client.post("/api/v1/errors/envelope", content=forged)
    assert r.status_code == 200, r.text
    out = json.loads(error_routes.parse_envelope(relay[-1])[1][0][1])
    assert out["user"] == {"id": str(user.id)} and out["release"] == "mailr@9.9.9"
    assert "server_name" not in out and "extra" not in out
    assert (await client.post("/api/v1/errors/envelope", content=forged)).status_code == 200
    assert (await client.post("/api/v1/errors/envelope", content=forged)).status_code == 429  # the user's limit


async def test_error_keys_are_per_page_load_with_a_quota_each(app, relay, monkeypatch):
    from datetime import timedelta
    from urllib.parse import unquote

    from mailr.api import error_routes
    from mailr.core import throttle

    monkeypatch.setattr(error_routes, "PER_KEY", throttle.Limit(f"eky{id(relay)}", 2, timedelta(minutes=10)))
    ok = _envelope({"message": "boom"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        first = (await anon.get("/api/v1/errors/config")).json()["tunnel"]
        second = (await anon.get("/api/v1/errors/config")).json()["tunnel"]
        k1, k2 = (unquote(t.split("k=")[1]) for t in (first, second))
        assert error_routes.tunnel_key_id(k1, "127.0.0.1") != error_routes.tunnel_key_id(k2, "127.0.0.1")
        assert [(await anon.post(first, content=ok)).status_code for _ in range(3)] == [200, 200, 429]
        # Another page load's key has a quota of its own.
        assert (await anon.post(second, content=ok)).status_code == 200


async def test_signed_out_reports_cannot_use_up_the_signed_in_budget(app, client, user, relay, monkeypatch):
    from datetime import timedelta

    from mailr.api import error_routes
    from mailr.core import throttle

    monkeypatch.setattr(error_routes, "PER_SERVER_SIGNED_OUT", throttle.Limit(f"ean{id(relay)}", 2, timedelta(hours=1)))
    ok = _envelope({"message": "boom"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        for i in range(3):
            tunnel = (await anon.get("/api/v1/errors/config")).json()["tunnel"]
            assert (await anon.post(tunnel, content=ok)).status_code == (200 if i < 2 else 429)
    # The signed-in browser still reports.
    assert (await client.post("/api/v1/errors/envelope", content=ok)).status_code == 200


async def test_one_address_gets_only_its_share_of_the_hour(app, relay, monkeypatch):
    from datetime import timedelta

    from mailr.api import error_routes
    from mailr.core import throttle

    monkeypatch.setattr(error_routes, "PER_IP_HOUR", throttle.Limit(f"eih{id(relay)}", 2, timedelta(hours=1)))
    ok = _envelope({"message": "boom"})
    codes = []
    tunnel = ""
    # Addresses of one IPv6 /64 share it, and a key works across them.
    for i in range(3):
        transport = ASGITransport(app=app, client=(f"2001:db8:5:5::{i + 1:x}", 40000))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            if i == 0:
                tunnel = (await c.get("/api/v1/errors/config")).json()["tunnel"]
            codes.append((await c.post(tunnel, content=ok)).status_code)
    assert codes == [200, 200, 429]
    transport = ASGITransport(app=app, client=("2001:db8:5:6::1", 40000))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.post(tunnel, content=ok)).status_code == 401  # another network: the key is not its


def test_the_web_apps_catch_all_takes_any_path(tmp_path):
    """A path the file system refuses (ENAMETOOLONG, a NUL byte) is no file of the web app, never
    an OSError (a 500 for anyone, signed in or not)."""
    from mailr.main import _static_file

    (tmp_path / "sw.js").write_text("x")
    assert _static_file(tmp_path, "sw.js") is True
    for path in ("a" * 300, "x/" * 600, "b" * 255 + "/c", "nul\x00.js", "../outside/sw.js"):
        assert _static_file(tmp_path, path) is False, path[:20]


async def test_database_and_validation_errors_carry_no_values(user):
    """Error reports and logs never hold the values of a failed query or a mismatched answer."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from mailr.core.errors import redact, scrub
    from mailr.db import sessionmaker

    async with sessionmaker()() as db:
        with pytest.raises(DBAPIError) as e:
            await db.execute(text("SELECT CAST(:v AS integer)"), {"v": "Grades for the spring cohort"})
    assert "Grades" not in redact(str(e.value)) and "parameters hidden" in str(e.value)
    for pg in ('invalid input syntax for type integer: "Grades"', "Key (subject)=(Grades) already exists."):
        assert "Grades" not in redact(pg)
    sql = "boom\n[SQL: SELECT 1]\n[parameters: ('Grades for the spring cohort',)]\n(Background on this error at: x)"
    assert "Grades" not in redact(sql) and "[SQL: SELECT 1]" in redact(sql)
    pyd = "subject\n  Input should be a valid string [type=string_type, input_value={'a': 'Grades'}, input_type=dict]"
    assert "Grades" not in redact(pyd)
    ev = scrub({"exception": {"values": [{"value": "[{'type': 'x', 'loc': ('body',), 'input': 'Grades', 'ctx': {}}]"}]}})
    assert "Grades" not in str(ev)


def test_log_lines_carry_no_query_values():
    import json
    import logging

    from mailr.core import logsetup

    try:
        raise ValueError("invalid input for query argument $1: 'Grades for Dana' (expected int)\n"
                         "[SQL: SELECT 1]\n[parameters: ('Grades for Dana',)]")
    except ValueError:
        import sys

        rec = logging.LogRecord("mailr", logging.ERROR, __file__, 1, "failed: %s", ("[parameters: ('Grades')]",),
                                sys.exc_info())
    for fmt in (logsetup.JsonFormatter(), logsetup.TextFormatter(logsetup.TEXT_FORMAT)):
        line = fmt.format(rec)
        assert "Grades" not in line and "SELECT 1" in (json.loads(line)["exc"] if line.startswith("{") else line)
