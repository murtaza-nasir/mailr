"""Content fetches and the egress proxy (core/egress.py).

- MAILR_REQUIRE_EGRESS fails closed: without an egress proxy no fetch of a URL from mail runs and
  no direct connection is attempted (image proxy, prefetch, sender pictures, one-click
  unsubscribe); a proxy that is down makes the fetch fail, never fall back.
- Through the egress proxy the sender's host name is never resolved locally (a unique host per
  recipient would otherwise reach the sender's DNS server through the server's resolver),
  redirects included.
- The egress check: ok, down, leak; /health/ready shows it as an optional check and
  /health/egress answers 200/503, neither with addresses; admins see the exit address.
"""

from __future__ import annotations

import ipaddress
import socket
import struct
import time
import zlib

import httpcore
import pytest
from helpers.egress import _Proxy
from httpx import ASGITransport, AsyncClient

from mailr.config import get_settings
from mailr.core import egress, health, imageproxy, netguard


def png(salt: bytes = b"") -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * 4 for _ in range(3))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 3, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw + salt)) + chunk(b"IEND", b""))


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    egress.clear_cache()
    monkeypatch.setattr(imageproxy, "_root", lambda: tmp_path / "imgcache")
    yield
    egress.clear_cache()


def _require(monkeypatch, on: bool = True):
    monkeypatch.setattr(get_settings(), "require_egress", on)


def _no_state(monkeypatch, st=None):
    async def state(use_cache=True):
        return st
    monkeypatch.setattr(egress, "state", state)


@pytest.fixture
def no_network(monkeypatch):
    """Records every name lookup and outbound TCP connect, and lets none of them happen."""
    calls: list[str] = []

    def getaddrinfo(host, *a, **k):
        calls.append(f"dns {host}")
        raise socket.gaierror("lookups are off in this test")

    async def connect_tcp(self, host, port, *a, **k):
        calls.append(f"tcp {host}:{port}")
        raise httpcore.ConnectError("connections are off in this test")

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect_tcp)
    return calls


# ---------------------------------------------------------------- fail closed


async def test_required_without_egress_attempts_no_direct_connection(monkeypatch, no_network):
    from mailr.api import avatar_routes
    from mailr.core import unsubscribe

    _require(monkeypatch)
    _no_state(monkeypatch)

    async def config(db):
        return True, ""

    monkeypatch.setattr(imageproxy, "instance_config", config)

    with pytest.raises(egress.EgressUnavailable) as ex:
        await imageproxy.fetch("https://img.sender.example/a.png", egress=None)
    assert ex.value.state == "unconfigured" and "paused" in str(ex.value)
    # Nothing was cached: once the egress is back, the image is fetched.
    assert imageproxy.lookup(imageproxy.url_hash("https://img.sender.example/a.png")) is None
    with pytest.raises(egress.EgressUnavailable):
        await imageproxy.fetch_all(["https://img.sender.example/a.png"], None)
    # The client itself refuses to go direct, whatever a caller did.
    with pytest.raises(egress.EgressUnavailable):
        egress.client("")
    out = await unsubscribe.execute(None, None, None, {"method": "one_click", "url": "https://list.example/u/1"})
    assert out["ok"] is False and "paused" in out["error"]
    assert await avatar_routes.lookup_remote("someone@sender.example", "") is None
    assert not (avatar_routes._root() / "remote" / avatar_routes._key("someone@sender.example")).with_suffix(".json").exists()
    assert no_network == []


async def test_not_required_without_egress_still_fetches_directly(monkeypatch):
    _require(monkeypatch, False)
    assert await egress.gate("") == ""
    c = egress.client("")
    assert c._transport is not None  # the pinned netguard transport
    await c.aclose()


async def test_an_egress_that_is_down_fails_without_fallback(monkeypatch):
    """The proxy's port is closed: the fetch fails, and no direct connection is made."""
    _require(monkeypatch)
    _no_state(monkeypatch)
    direct: list[str] = []
    real = netguard.guarded_client

    def guarded(**kw):
        direct.append("guarded_client")
        return real(**kw)

    monkeypatch.setattr(netguard, "guarded_client", guarded)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    e = await imageproxy.fetch("https://img.sender.example/down.png", egress=f"http://127.0.0.1:{port}")
    assert e.status == "failed"
    assert direct == []


async def test_a_reported_down_or_leaking_egress_pauses_fetches(monkeypatch, no_network):
    _require(monkeypatch)
    for name in ("down", "leak"):
        _no_state(monkeypatch, {"state": name, "ok": False, "at": time.time()})
        with pytest.raises(egress.EgressUnavailable) as ex:
            await imageproxy.fetch(f"https://img.sender.example/{name}.png", egress="http://egress:8888")
        assert ex.value.state == name
    # A verdict the worker has not refreshed for a while no longer pauses anything.
    _no_state(monkeypatch, {"state": "down", "ok": False, "at": time.time() - egress.STATE_FRESH - 5})
    assert await egress.gate("http://egress:8888") == "http://egress:8888"
    # Not required: the verdict is shown, but fetches are not paused by it.
    _require(monkeypatch, False)
    _no_state(monkeypatch, {"state": "leak", "ok": False, "at": time.time()})
    assert await egress.gate("http://egress:8888") == "http://egress:8888"
    assert no_network == []


async def test_the_image_proxy_is_always_on_while_the_egress_is_required(app, monkeypatch):
    from mailr.db import session_scope

    async with session_scope() as db:
        from mailr.api.admin_routes import _set_instance

        await _set_instance(db, "image_proxy", False)
        await db.commit()
        assert (await imageproxy.instance_config(db))[0] is False
        _require(monkeypatch)
        assert (await imageproxy.instance_config(db))[0] is True
        await _set_instance(db, "image_proxy", True)
        await db.commit()


async def test_the_reader_shows_why_images_are_paused(monkeypatch):
    from mailr.api import mail_read

    _require(monkeypatch)
    _no_state(monkeypatch)

    async def config(db):
        return True, ""

    async def not_junk(db, m):
        return False

    monkeypatch.setattr(imageproxy, "instance_config", config)
    monkeypatch.setattr(imageproxy, "is_junk", not_junk)

    class M:
        id = 7

    raw = (b"From: a@sender.example\r\nTo: b@example.com\r\nSubject: hi\r\nContent-Type: text/html\r\n\r\n"
           b'<p>Hello</p><img src="https://img.sender.example/hero.png" width="300" height="200">')
    out = await mail_read.render_for_reader(None, M(), raw, True)
    assert "paused" in out["images_paused"] and out["remote_images"] is True
    assert "/images/" not in out["html"] and "img.sender.example" not in out["html"].split("data-mailr-src")[0]
    # Images not asked for: the ordinary "not displayed" state, no paused notice yet.
    out = await mail_read.render_for_reader(None, M(), raw, False)
    assert "images_paused" not in out


# ---------------------------------------------------------------- no local DNS


async def test_egress_fetches_never_resolve_the_senders_host(monkeypatch):
    """socket.getaddrinfo fails for every name: the fetch through the proxy still works, the
    redirect to a second unique host included, and no name from the mail was looked up."""
    monkeypatch.setattr(imageproxy, "ALLOW_HTTP", True)
    asked: list[str] = []
    real = socket.getaddrinfo

    def getaddrinfo(host, *a, **k):
        try:
            ipaddress.ip_address(str(host))
        except ValueError:
            asked.append(str(host))
            raise socket.gaierror("no local lookups in this test") from None
        return real(host, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    proxy = _Proxy({
        "r1x9.tracker.example/open.png": (302, {"Location": "http://c7q2.cdn.example/real.png"}, b""),
        "c7q2.cdn.example/real.png": (200, {"Content-Type": "image/png"}, png(b"dns")),
    })
    try:
        e = await imageproxy.fetch("http://r1x9.tracker.example/open.png", egress=proxy.url)
        assert e.status == "ok", e.error
        assert proxy.seen == ["GET http://r1x9.tracker.example/open.png", "GET http://c7q2.cdn.example/real.png"]
        # https goes out as a CONNECT to the proxy, still without a local lookup.
        e = await imageproxy.fetch("https://u5k3.tracker.example/p.png", egress=proxy.url)
        assert e.status == "failed"
        assert proxy.seen[-1] == "CONNECT u5k3.tracker.example:443"
    finally:
        proxy.close()
    assert asked == []


async def test_bimi_record_goes_through_the_egress_as_dns_over_https(monkeypatch):
    from mailr.api import avatar_routes

    def no_dns(*a, **k):
        raise AssertionError("the local resolver must not be asked")

    import dns.asyncresolver

    monkeypatch.setattr(dns.asyncresolver, "resolve", no_dns)
    proxy = _Proxy()
    try:
        assert await avatar_routes._bimi("sender.example", proxy.url) is None
        assert proxy.seen and proxy.seen[0] == "CONNECT cloudflare-dns.com:443"
    finally:
        proxy.close()


# Through the egress proxy the host is never resolved here, so egress.check_url_offline decides on
# the URL alone: local names, numeric and IP-literal hosts in every form, other ports, credentials and
# plain http are refused, ordinary public names pass, and no lookup or connection happens either way.
OFFLINE_REFUSED = [
    # local and internal names
    "https://localhost/a.png", "https://intranet/a.png", "https://nas.lan/a.png", "https://printer.local/a.png",
    "https://x.home.arpa/a.png", "https://x.internal/", "https://router.lan/", "https://nas.local/",
    "https://egress/a.png", "https://stalwart/", "https://postgres/", "https://postgres:5432/",
    # addresses, in every spelling an address can take
    "https://192.168.1.10/a.png", "https://10.1.2.3/", "https://100.64.1.1/", "https://169.254.169.254/latest",
    "https://127.0.0.1/a.png", "https://127.1/", "https://2130706433/", "https://0x7f.1/", "https://0177.0.0.1/",
    "https://0x7f000001/x", "https://10.1/x", "https://[::1]/a.png", "https://[::ffff:127.0.0.1]/",
    "https://[::ffff:a00:1]/",
    # another port, credentials, plain http
    "https://www.example.org:8443/", "https://example.com:8443/x", "https://example.com:25/x",
    "https://u:p@img.sender.example/a.png", "https://u:p@www.example.org/", "http://img.sender.example/a.png",
    "http://www.example.org/",
]
OFFLINE_PASSED = ["https://img.sender.example/a.png", "https://93.184.215.14/a.png", "https://www.example.org/a?b=c",
                  "https://example.com:443/x", "https://1.2.3.4.nip.io/x", "https://cdn.example/a.png"]


@pytest.mark.parametrize("url", OFFLINE_REFUSED)
def test_offline_checks_refuse_local_targets_without_a_lookup(no_network, url):
    with pytest.raises(netguard.UnsafeTarget):
        egress.check_url_offline(url)
    assert no_network == []


@pytest.mark.parametrize("url", OFFLINE_PASSED)
def test_offline_checks_pass_public_names_without_a_lookup(no_network, url):
    egress.check_url_offline(url)
    assert no_network == []


# ---------------------------------------------------------------- the check


async def _run(monkeypatch, *, egress_url="http://egress:8888", exit_addr="149.88.1.2", direct="203.0.113.9",
               forbidden=""):
    async def configured():
        return egress_url

    async def fake_exit(e):
        return exit_addr

    calls: list[str] = []

    async def fake_direct():
        calls.append("direct")
        return direct

    saved: dict = {}

    async def save(st):
        saved.clear()
        saved.update(st)

    async def state(use_cache=True):
        return dict(saved) if saved else None

    monkeypatch.setattr(egress, "_configured_from_db", configured)
    monkeypatch.setattr(egress, "exit_ip", fake_exit)
    monkeypatch.setattr(egress, "direct_ip", fake_direct)
    monkeypatch.setattr(egress, "_save", save)
    monkeypatch.setattr(egress, "state", state)
    monkeypatch.setattr(get_settings(), "egress_forbidden_ips", forbidden)
    return await egress.run_check(), calls, state


async def test_check_ok_down_and_leak(monkeypatch):
    st, calls, _ = await _run(monkeypatch)
    assert st["state"] == "ok" and st["ok"] and st["exit_ip"] == "149.88.1.2" and calls == ["direct"]
    # The direct address is kept for DIRECT_TTL: the next check does not ask again.
    st2 = await egress.run_check()
    assert st2["state"] == "ok" and calls == ["direct"]

    st, _, _ = await _run(monkeypatch, exit_addr=None)
    assert st["state"] == "down" and not st["ok"]

    st, _, _ = await _run(monkeypatch, exit_addr="203.0.113.9")
    assert st["state"] == "leak" and "LEAK" in st["detail"] and "203.0.113.9" not in st["detail"]

    # MAILR_EGRESS_FORBIDDEN_IPS: no direct request at all.
    st, calls, _ = await _run(monkeypatch, exit_addr="198.51.100.7", forbidden="198.51.100.7")
    assert st["state"] == "leak" and calls == []

    st, calls, _ = await _run(monkeypatch, egress_url="")
    assert st["state"] == "off" and st["ok"] and calls == []
    _require(monkeypatch)
    st, _, _ = await _run(monkeypatch, egress_url="")
    assert st["state"] == "unconfigured" and not st["ok"]


async def test_health_shows_egress_as_optional_without_addresses(app, monkeypatch):
    health.clear_cache()
    for name in health.CHECKS:
        if name != "egress":
            async def fine():
                return True, "fine"
            monkeypatch.setitem(health.CHECK_FUNCS, name, fine)

    async def configured():
        return "http://mailr:pw@egress:8888"

    monkeypatch.setattr(egress, "_configured_from_db", configured)
    leak = {"state": "leak", "ok": False, "detail": "LEAK: exit address is the server's own",
            "exit_ip": "203.0.113.9", "direct_ip": "203.0.113.9", "at": time.time()}
    _no_state(monkeypatch, leak)
    # The details need the monitor token; test_security_misc_paths covers anonymous calls.
    monkeypatch.setattr(get_settings(), "health_token", "monitor-token-for-tests")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"X-Health-Token": "monitor-token-for-tests"}) as c:
        r = await c.get("/api/v1/health/ready")
        assert r.status_code == 200  # images failing never make mail unhealthy
        chk = r.json()["checks"]["egress"]
        assert chk == {"ok": False, "detail": "LEAK: exit address is the server's own", "required": False}
        r = await c.get("/api/v1/health/egress")
        assert r.status_code == 503 and r.json()["state"] == "leak" and "203.0.113" not in r.text

        _no_state(monkeypatch, {"state": "ok", "ok": True, "detail": "via egress proxy", "exit_ip": "149.88.1.2",
                                "at": time.time()})
        r = await c.get("/api/v1/health/egress")
        assert r.status_code == 200 and '"state":"ok"' in r.text and "149.88" not in r.text

        # A check the worker has not refreshed: stale, 503.
        _no_state(monkeypatch, {"state": "ok", "ok": True, "detail": "via egress proxy",
                                "at": time.time() - egress.STATE_FRESH - 60})
        r = await c.get("/api/v1/health/egress")
        assert r.status_code == 503 and r.json()["state"] == "stale"
    health.clear_cache()


async def test_admins_see_the_exit_address(app, client, monkeypatch):
    import uuid

    from mailr.core.users import create_user
    from mailr.db import sessionmaker

    _no_state(monkeypatch, {"state": "ok", "ok": True, "detail": "via egress proxy", "exit_ip": "149.88.1.2",
                            "at": time.time()})
    monkeypatch.setattr(get_settings(), "image_proxy_egress", "http://mailr:pw@127.0.0.1:8888")
    assert (await client.get("/api/v1/admin/policies")).status_code == 403
    async with sessionmaker()() as db:
        adm = await create_user(db, f"a-{uuid.uuid4().hex[:8]}@example.com", "Admin",
                                password="admin-password-123", role="admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/login", json={"email": adm.email, "password": "admin-password-123"})
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        r = await c.get("/api/v1/admin/policies")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["egress_check"]["detail"] == "via egress proxy, exit 149.88.1.2"
    assert body["require_egress"] is False and ":pw@" not in r.text


def test_scheduler_tick_paces_the_check(monkeypatch):
    import asyncio

    runs: list[int] = []

    async def fake():
        runs.append(1)
        return {"ok": True}

    async def go():
        monkeypatch.setattr(egress, "run_check", fake)
        monkeypatch.setattr(egress, "_last_run", -float("inf"))
        monkeypatch.setattr(egress, "_task", None)
        egress.on_scheduler_tick()
        await asyncio.sleep(0.05)
        egress.on_scheduler_tick()  # within CHECK_EVERY: nothing
        await asyncio.sleep(0.05)
        assert runs == [1]
        monkeypatch.setattr(egress, "_last_ok", False)
        monkeypatch.setattr(egress, "_last_run", time.monotonic() - egress.CHECK_RETRY - 1)
        egress.on_scheduler_tick()  # failing: retried after CHECK_RETRY
        await asyncio.sleep(0.05)
        assert runs == [1, 1]

    asyncio.run(go())
