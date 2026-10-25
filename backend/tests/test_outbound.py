"""core/outbound.py: the one locality rule, the connect-time check, the taint scope and the one way out."""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from mailr.core import netguard, outbound


def _fake_dns(monkeypatch, table: dict[str, list[str]]):
    real = socket.getaddrinfo

    def fake(host, port=None, *a, **kw):
        if host in table:
            return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))
                    for ip in table[host]]
        return real(host, port, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.fixture
def allow(monkeypatch):
    def set_(value: str) -> None:
        monkeypatch.setattr(netguard.get_settings(), "outbound_allow", value)
        netguard._allow_list.cache_clear()
    yield set_
    netguard._allow_list.cache_clear()


def svc(url: str, locality: str = "local", kind: str = "chat") -> outbound.Service:
    return outbound.Service(kind=kind, name="t", base_url=url, locality=locality, source="test")


def test_local_and_allowed_are_the_one_comparison():
    assert outbound.local("local") and not outbound.local("hosted")
    assert outbound.local(svc("http://x.test", "local")) and not outbound.local(svc("http://x.test", "hosted"))
    assert outbound.allowed(False, svc("http://x.test", "hosted"))
    assert not outbound.allowed(True, svc("http://x.test", "hosted"))
    assert outbound.allowed(True, svc("http://x.test", "local"))


def test_private_and_loopback_addresses_are_local(allow):
    allow("")
    outbound.verify_local("a", ["10.0.0.5", "192.168.1.2", "127.0.0.1", "::1", "fd00::1", "100.64.0.9"])
    with pytest.raises(outbound.NotLocal):
        outbound.verify_local("a", ["10.0.0.5", "8.8.8.8"])
    with pytest.raises(outbound.NotLocal):
        outbound.verify_local("a", [])


def test_an_allow_list_name_is_never_local_by_its_name(allow, monkeypatch):
    """Gap 11: MAILR_OUTBOUND_ALLOW names count as local only through their addresses; CIDRs and IP
    literals count as given."""
    allow("models.example.test,9.9.9.0/24")
    _fake_dns(monkeypatch, {"models.example.test": ["93.184.216.34"], "lab.example.test": ["9.9.9.9"]})
    assert not outbound.host_is_local_now("http://models.example.test/v1")
    assert outbound.host_is_local_now("http://lab.example.test/v1")


async def test_admin_check_refuses_an_allow_listed_public_name(allow, monkeypatch):
    from mailr.api import admin_routes
    from mailr.i18n import ApiError

    allow("models.example.test")
    _fake_dns(monkeypatch, {"models.example.test": ["93.184.216.34"]})
    with pytest.raises(ApiError) as e:
        await admin_routes._check_local("http://models.example.test/v1", "local")
    assert e.value.code == "admin.local_public"
    await admin_routes._check_local("http://models.example.test/v1", "hosted")  # hosted: no check


async def test_a_local_only_call_to_a_hosted_service_is_refused():
    with pytest.raises(outbound.Refused):
        outbound.client(svc("https://api.example.test/v1", "hosted"), local_only=True)
    with outbound.scope():
        outbound.mark_local_only("test")
        with pytest.raises(outbound.Refused):
            outbound.client(svc("https://api.example.test/v1", "hosted"))
        # A person's own account (the Speakr API) is not a policy kind.
        outbound.client(svc("https://speakr.example.test", "hosted", kind="account"))


async def test_the_taint_is_checked_again_when_the_request_is_sent():
    seen = []
    t = httpx.MockTransport(lambda r: (seen.append(r), httpx.Response(200, json={}))[1])
    with outbound.scope():
        async with outbound.client(svc("https://api.example.test/v1", "hosted"), transport=t) as c:
            await c.get("https://api.example.test/v1/a")
            outbound.mark_local_only("loaded later")
            with pytest.raises(outbound.Refused):
                await c.get("https://api.example.test/v1/b")
    assert len(seen) == 1


async def test_item_scope_starts_clean_and_holds_the_items_class():
    with outbound.scope():
        outbound.mark_local_only("the batch")
        with outbound.item_scope():
            assert not outbound.tainted()
        with outbound.item_scope(local_only=True):
            assert outbound.tainted()
        assert outbound.tainted()
    assert not outbound.tainted()


async def test_a_local_service_resolving_to_a_public_address_is_refused_at_connect(allow, monkeypatch):
    allow("")
    _fake_dns(monkeypatch, {"llm.lan.example.test": ["93.184.216.35"]})
    async with outbound.client(svc("http://llm.lan.example.test:8000/v1")) as c:
        with pytest.raises(httpx.ConnectError, match="public address"):
            await c.get("http://llm.lan.example.test:8000/v1/models")
    assert ("chat", "t") in outbound.refusals()
    assert await outbound.check(svc("http://llm.lan.example.test:8000/v1")) is not None


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


async def test_a_local_service_on_loopback_connects_pinned(allow):
    allow("127.0.0.1")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Ok)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        async with outbound.client(svc(url)) as c:
            assert (await c.get(url + "/x")).json() == {"ok": True}
            assert c._trust_env is False
    finally:
        srv.shutdown()


async def test_loading_a_local_only_row_taints_the_scope(user):
    from sqlalchemy import select

    from mailr.db import sessionmaker
    from mailr.notes.models import Note

    async with sessionmaker()() as db:
        db.add(Note(pid="n-outbound-1", owner_user_id=user.id, title="plain"))
        db.add(Note(pid="n-outbound-2", owner_user_id=user.id, title="secret", local_only=True))
        await db.commit()
    with outbound.scope():
        async with sessionmaker()() as db:
            await db.execute(select(Note).where(Note.pid == "n-outbound-1"))
            assert not outbound.tainted()
            (await db.execute(select(Note).where(Note.pid == "n-outbound-2"))).scalars().all()
            assert outbound.tainted()
    with outbound.scope():
        async with sessionmaker()() as db:
            n = (await db.execute(select(Note).where(Note.pid == "n-outbound-2"))).scalar_one()
            assert outbound.tainted()
            with outbound.item_scope(n):
                assert outbound.tainted()


# ---------------------------------------------------------------- error reports (gap 8)


def _event():
    return {"request": {"url": "https://m.example/api/v1/people/dana@example.org/notes?q=x"},
            "transaction": "/api/v1/people/dana@example.org",
            "logentry": {"message": "send to %s failed", "params": ["the hidden plan"],
                         "formatted": "send to the hidden plan failed"},
            "exception": {"values": [{"type": "ValueError", "value": "the hidden plan"}]}}


def test_error_reports_keep_only_types_for_a_hosted_dsn(monkeypatch):
    import json

    from mailr.core import errors

    monkeypatch.setattr(errors, "_DSN_LOCAL", {"https://k@errors.example.test/1": False})
    out = errors.scrub(_event(), dsn="https://k@errors.example.test/1")
    blob = json.dumps(out)
    assert "hidden plan" not in blob and "dana@" not in blob
    assert out["exception"]["values"][0] == {"type": "ValueError"}
    assert out["logentry"]["message"] == "send to %s failed"


def test_error_reports_keep_values_for_a_local_dsn_unless_the_scope_is_local_only(monkeypatch):
    import json

    from mailr.core import errors

    monkeypatch.setattr(errors, "_DSN_LOCAL", {"http://k@glitchtip.lan.test/1": True})
    out = errors.scrub(_event(), dsn="http://k@glitchtip.lan.test/1")
    assert out["exception"]["values"][0]["value"] == "the hidden plan"
    assert "dana@" not in json.dumps(out)  # paths and transactions are redacted like other texts
    assert out["transaction"] == "/api/v1/people/<address>"
    with outbound.scope():
        outbound.mark_local_only("test")
        out = errors.scrub(_event(), dsn="http://k@glitchtip.lan.test/1")
    assert "hidden plan" not in json.dumps(out)
    # An exception that left a local-only request is marked on its way out (ScopeMiddleware).
    exc = ValueError("the hidden plan")
    with outbound.scope():
        outbound.mark_local_only("test")
        outbound.note_exception(exc)
    out = errors.scrub(_event(), {"exc_info": (ValueError, exc, None)}, dsn="http://k@glitchtip.lan.test/1")
    assert "hidden plan" not in json.dumps(out)


# ---------------------------------------------------------------- Speakr's own models (gap 9)


def test_speakr_models_count_as_local_only_with_a_private_host_and_follow_the_strict_rule():
    from types import SimpleNamespace

    from mailr.integrations.speakr import store

    def conn(data_class, **caps):
        return SimpleNamespace(data_class=data_class, config={"speakr_capabilities": caps})

    assert store.speakr_model_ok(conn("normal"))
    assert not store.speakr_model_ok(conn("normal"), strict=True)  # local_all or the person's switch
    assert not store.speakr_model_ok(conn("local_only", models_local=True))  # the claim alone is not enough
    assert store.speakr_model_ok(conn("local_only", models_local=True, host_private=True))
    assert store.speakr_model_ok(conn("normal", models_local=True, host_private=True), strict=True)
