"""Regression tests for outbound connections: each test is an attack that must fail."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpcore
import httpx
import pytest

from mailr.core import netguard


@pytest.fixture
def strict(monkeypatch):
    """Production-like guard: no test allowances, the default private policy ("admins")."""
    monkeypatch.setattr(netguard.get_settings(), "outbound_allow", "")
    monkeypatch.setattr(netguard.get_settings(), "allow_private_hosts", "admins")
    netguard._allow_list.cache_clear()
    yield netguard
    netguard._allow_list.cache_clear()


def _fake_dns(monkeypatch, table: dict[str, list[str]]):
    real = socket.getaddrinfo
    calls: list[str] = []

    def fake(host, port=None, *a, **kw):
        if host in table:
            calls.append(host)
            answers = table[host]
            ip = answers.pop(0) if len(answers) > 1 else answers[0]
            fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
            return [(fam, socket.SOCK_STREAM, 6, "", (ip, port or 0))]
        return real(host, port, *a, **kw)

    monkeypatch.setattr(netguard.socket, "getaddrinfo", fake)
    return calls


# ---------------- Address classes ----------------

@pytest.mark.parametrize("url", [
    "https://100.64.0.1/", "https://100.100.100.100/x.png", "https://100.127.255.254/",
    "https://[fec0::1]/", "https://192.88.99.1/", "https://[fd00:ec2::254]/", "https://198.18.0.1/",
    "https://[::ffff:10.0.0.1]/", "https://[64:ff9b::a00:1]/", "https://[2002:a00:1::1]/",
    "https://0.1.2.3/", "https://240.0.0.1/",
])
async def test_mail_urls_refuse_every_non_global_address(strict, url):
    with pytest.raises(netguard.UnsafeTarget):
        await netguard.check_untrusted_url(url)


@pytest.mark.parametrize("ip", ["100.64.0.1", "100.100.100.100", "fd00:ec2::254", "fec0::1", "192.88.99.1"])
def test_cgnat_and_metadata_are_not_public_even_with_private_hosts_allowed(strict, ip):
    # CGNAT/Tailscale counts as private: refused unless the account may reach private hosts.
    with pytest.raises(netguard.UnsafeTarget):
        netguard.check_host_sync(ip, public_only=True)
    if ip in ("fd00:ec2::254", "fec0::1", "192.88.99.1"):
        with pytest.raises(netguard.UnsafeTarget):  # always refused
            netguard.check_host_sync(ip, private_ok=True)


async def test_public_addresses_still_pass(strict):
    await netguard.check_host("8.8.8.8", public_only=True)
    await netguard.check_host("64:ff9b::808:808", public_only=True)  # NAT64 of a public address


# ---------------- Private (LAN) policy ----------------

class _U:
    def __init__(self, role):
        self.role, self.disabled = role, False


@pytest.mark.parametrize("ip", ["192.168.1.42", "10.253.53.2", "172.20.0.5", "fd00::1"])
def test_private_hosts_need_an_admin_owner_by_default(strict, ip):
    assert netguard.private_mode() == "admins"
    with pytest.raises(netguard.UnsafeTarget):
        netguard.check_host_sync(ip)  # no owner known: refused
    with pytest.raises(netguard.UnsafeTarget):
        netguard.check_host_sync(ip, private_ok=netguard.private_ok_for(_U("user")))
    netguard.check_host_sync(ip, private_ok=netguard.private_ok_for(_U("admin")))


def test_private_policy_modes(strict, monkeypatch):
    for value, mode in (("true", "all"), ("all", "all"), ("false", "none"), ("none", "none"), ("", "admins")):
        monkeypatch.setattr(netguard.get_settings(), "allow_private_hosts", value)
        assert netguard.private_mode() == mode
    monkeypatch.setattr(netguard.get_settings(), "allow_private_hosts", "none")
    assert netguard.private_ok_for(_U("admin")) is False
    monkeypatch.setattr(netguard.get_settings(), "allow_private_hosts", "all")
    assert netguard.private_ok_for(_U("user")) is True


def test_outbound_allow_opens_a_lan_host_for_everyone(strict, monkeypatch):
    monkeypatch.setattr(netguard.get_settings(), "outbound_allow", "10.9.0.0/16,cal.home.example")
    netguard._allow_list.cache_clear()
    _fake_dns(monkeypatch, {"cal.home.example": ["192.168.1.7"]})
    netguard.check_host_sync("10.9.1.1", private_ok=False)
    netguard.check_host_sync("cal.home.example", private_ok=False)
    with pytest.raises(netguard.UnsafeTarget):
        netguard.check_host_sync("10.8.1.1", private_ok=False)


@pytest.mark.parametrize("name", ["egress", "resolver", "app", "worker", "stalwart", "postgres"])
def test_compose_services_are_infrastructure(strict, name):
    with pytest.raises(netguard.UnsafeTarget, match="infrastructure"):
        netguard.check_host_sync(name, private_ok=True)


def test_the_default_gateway_is_infrastructure(strict, monkeypatch):
    monkeypatch.setattr(netguard, "_gateways", lambda: {"172.18.0.1"})
    monkeypatch.setattr(netguard, "_INFRA_CACHE", None)
    with pytest.raises(netguard.UnsafeTarget, match="infrastructure"):
        netguard.check_host_sync("172.18.0.1", private_ok=True)
    _fake_dns(monkeypatch, {"host.example": ["172.18.0.1"]})
    with pytest.raises(netguard.UnsafeTarget, match="infrastructure"):
        netguard.check_host_sync("host.example", private_ok=True)
    monkeypatch.setattr(netguard, "_INFRA_CACHE", None)


def test_the_addresses_of_internal_names_are_infrastructure(strict, monkeypatch):
    """refresh_infra_cache looks the internal names up and refuses their addresses. In tests the
    names fail at once (conftest._no_reserved_dns); here the fake resolver maps two of them, as
    Docker's DNS does inside the stack."""
    monkeypatch.setattr(netguard, "_gateways", lambda: set())
    monkeypatch.setattr(netguard, "_INFRA_CACHE", None)
    calls = _fake_dns(monkeypatch, {"stalwart": ["172.30.0.5"], "postgres": ["fd00:30::6"],
                                    "calendar.home.example": ["172.30.0.5"]})
    infra = netguard.refresh_infra_cache()
    assert {"172.30.0.5", "fd00:30::6"} <= infra and {"stalwart", "postgres"} <= set(calls)
    for target in ("172.30.0.5", "fd00:30::6", "calendar.home.example"):
        with pytest.raises(netguard.UnsafeTarget, match="infrastructure"):
            netguard.check_host_sync(target, private_ok=True)
    monkeypatch.setattr(netguard, "_INFRA_CACHE", None)


def test_gateway_parsing_reads_proc_net_route():
    gws = netguard._gateways()
    assert all(netguard._is_ip(g) for g in gws)


# ---------------- Pinned connections (DNS rebinding) ----------------
# The connection goes to the address checked: tests/test_outbound_guard_hardening.py
# (test_the_address_connected_to_is_the_address_checked).

async def test_rebinding_name_cannot_reach_loopback(strict, monkeypatch):
    """The check sees a public address; a second lookup would answer 127.0.0.1. The connection
    goes to the checked address, never to whatever the name resolves to later."""
    calls = _fake_dns(monkeypatch, {"rebind.example": ["93.184.216.34", "127.0.0.1"]})
    seen: list[str] = []

    async def fake_connect(self, host, port, **kw):
        seen.append(host)
        raise httpcore.ConnectError("test: no network")

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", fake_connect)
    await netguard.check_url("http://rebind.example/", private_ok=True)  # first answer: public
    async with netguard.guarded_client(private_ok=True) as c:
        with pytest.raises(httpx.ConnectError):
            await c.get("http://rebind.example/")
    # The client resolved once more (the second answer, loopback) and refused to connect.
    assert seen == [] and calls.count("rebind.example") == 2


class _Srv:
    def __init__(self, routes):
        outer = self
        self.hits: list[dict] = []

        class H(BaseHTTPRequestHandler):
            def _do(self):
                n = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(n) if n else b""
                outer.hits.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                   "body": body})
                status, headers, payload = routes.get(self.path, (404, {}, b""))
                if callable(payload):
                    payload = payload()
                self.send_response(status)
                for k, v in headers.items():
                    if k != "X-No-Length":
                        self.send_header(k, v)
                if "Content-Length" not in headers and "X-No-Length" not in headers:
                    self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)

            do_GET = do_POST = do_PROPFIND = do_PUT = do_REPORT = do_OPTIONS = do_CONNECT = _do

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


async def test_pinned_client_keeps_the_host_header():
    srv = _Srv({"/": (200, {}, b"ok")})
    try:
        async with netguard.guarded_client(private_ok=True) as c:  # localhost is allowed in tests
            r = await c.get(f"http://localhost:{srv.port}/")
        assert r.text == "ok" and srv.hits[0]["headers"]["Host"] == f"localhost:{srv.port}"
    finally:
        srv.close()


def test_pinned_socket_refuses_a_rebound_name(strict, monkeypatch):
    _fake_dns(monkeypatch, {"imap.rebind.example": ["127.0.0.1"]})
    with pytest.raises(netguard.UnsafeTarget):
        netguard.connect_pinned("imap.rebind.example", 993, 2, private_ok=True)


# ---------------- Capped reads ----------------

async def test_read_capped_stops_at_the_limit():
    sent = {"n": 0}

    def endless():
        sent["n"] += 1
        return b"x" * 2_000_000

    srv = _Srv({"/declared": (200, {"Content-Length": "2000000"}, endless),
                "/undeclared": (200, {"X-No-Length": "1"}, endless),
                "/chunked": (200, {}, b"y" * 1000)})
    try:
        async with netguard.guarded_client(private_ok=True) as c:
            with pytest.raises(netguard.TooLarge):  # refused on the header, before reading
                await netguard.fetch_capped(c, "GET", f"http://localhost:{srv.port}/declared", limit=1000)
            with pytest.raises(netguard.TooLarge):  # refused while streaming
                await netguard.fetch_capped(c, "GET", f"http://localhost:{srv.port}/undeclared", limit=1000)
            r, body = await netguard.fetch_capped(c, "GET", f"http://localhost:{srv.port}/chunked", limit=1000)
            assert body == b"y" * 1000
    finally:
        srv.close()


async def test_fetch_capped_has_an_overall_deadline():
    async def slow_handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n")
        for _ in range(200):
            writer.write(b"z")
            await writer.drain()
            await asyncio.sleep(0.05)
        writer.close()

    server = await asyncio.start_server(slow_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with netguard.guarded_client(private_ok=True, timeout=5) as c:
            with pytest.raises(TimeoutError):
                await netguard.fetch_capped(c, "GET", f"http://localhost:{port}/", limit=1_000_000, deadline=0.5)
    finally:
        server.close()


# ---------------- DAV credentials stay with the account ----------------

async def test_dav_redirect_to_another_origin_gets_no_credentials():
    other = _Srv({"/x": (207, {"Content-Type": "application/xml"}, b"<d:multistatus xmlns:d='DAV:'/>")})
    first = _Srv({"/": (302, {"Location": f"http://127.0.0.1:{other.port}/x"}, b"")})
    try:
        from mailr.calendar import dav

        async with dav.DavClient(f"http://localhost:{first.port}/", "u", "p", private_ok=True) as c:
            r = await c.request("PROPFIND", "")
        assert r.status_code == 207
        assert "Authorization" in first.hits[0]["headers"]  # the account's own server
        assert "Authorization" not in other.hits[0]["headers"]  # another origin: never
    finally:
        first.close()
        other.close()


async def test_dav_absolute_href_elsewhere_gets_no_credentials():
    other = _Srv({"/cal/": (207, {"Content-Type": "application/xml"}, b"<d:multistatus xmlns:d='DAV:'/>")})
    try:
        from mailr.calendar import dav

        async with dav.DavClient("http://localhost:1/", "u", "p", private_ok=True) as c:
            await c.request("PROPFIND", f"http://127.0.0.1:{other.port}/cal/")
        assert "Authorization" not in other.hits[0]["headers"]
    finally:
        other.close()


async def test_dav_refuses_a_downgrade_to_http(monkeypatch):
    from mailr.calendar import dav

    seen = []

    def handler(req: httpx.Request):
        seen.append((str(req.url), req.headers.get("authorization")))
        return httpx.Response(302, headers={"Location": "http://dav.example.org/moved/"})

    async def no_check(*a, **kw):
        return None

    monkeypatch.setattr(netguard, "check_url", no_check)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    c = dav.DavClient("https://dav.example.org/", "u", "p", http=http)
    with pytest.raises(dav.ServerError, match="https to http"):
        await c.request("PROPFIND", "")
    assert len(seen) == 1 and seen[0][1]  # one request, to the https origin only
    await http.aclose()


def test_dav_credentials_scope():
    from mailr.calendar import dav

    c = dav.DavClient("https://caldav.icloud.com/", "u", "p")
    assert c._credentials_for("https://caldav.icloud.com/123/") is not None
    assert c._credentials_for("https://p52-caldav.icloud.com/123/calendars/") is not None  # same domain, https
    for url in ("http://caldav.icloud.com/x", "https://caldav.icloud.com.evil.net/x", "https://evil.example/x",
                "https://caldav.icloud.com:8443/x", "https://93.184.216.34/x", "http://p52-caldav.icloud.com/x"):
        assert c._credentials_for(url) is None, url
    plain = dav.DavClient("http://cal.example.org:5232/", "u", "p")
    assert plain._credentials_for("http://cal.example.org:5232/u/") is not None
    assert plain._credentials_for("http://other.example.org:5232/u/") is None


async def test_dav_password_is_not_sent_over_plain_http_to_the_internet(strict, monkeypatch):
    from mailr.calendar import dav

    _fake_dns(monkeypatch, {"cal.public.example": ["93.184.216.34"]})
    async with dav.DavClient("http://cal.public.example/", "u", "p") as c:
        with pytest.raises(dav.DavError, match="https://"):
            await c.request("PROPFIND", "")


async def test_dav_client_honours_the_private_policy(strict, monkeypatch):
    from mailr.calendar import dav

    _fake_dns(monkeypatch, {"cal.home.example": ["192.168.1.43"]})
    async with dav.DavClient("https://cal.home.example/", "u", "p", private_ok=False) as c:
        with pytest.raises(dav.DavError, match="private address"):
            await c.request("PROPFIND", "")


async def test_calendar_connect_error_does_not_echo_the_server(client):
    srv = _Srv({"/": (500, {"Content-Type": "text/html"}, b"<h1>SECRET-INTERNAL-PAGE admin panel</h1>")})
    try:
        r = await client.post("/api/v1/calendar/connect",
                              json={"url": f"http://localhost:{srv.port}/", "username": "u", "password": "p"})
        assert r.status_code == 400
        assert "SECRET" not in r.text and "admin panel" not in r.text
        assert "HTTP 500" in r.json()["detail"]
    finally:
        srv.close()


async def test_calendar_connect_refuses_lan_for_a_plain_user(client, strict):
    r = await client.post("/api/v1/calendar/connect",
                          json={"url": "http://192.168.1.42:5232/", "username": "u", "password": "p"})
    assert r.status_code == 400 and "private address" in r.json()["detail"]


# ---------------- iTIP from others needs a positive, aligned result ----------------

@pytest.mark.parametrize("auth,ok", [
    ({}, False), ({"dmarc": "none"}, False), ({"dkim": "none"}, False), ({"spf": "pass"}, False),
    ({"dkim": "pass", "dkim_domains": "evil.example"}, False),
    ({"dkim": "pass", "dkim_domains": "acme.example.evil"}, False),
    # A DMARC pass counts only for its own header.from domain.
    ({"dmarc": "pass"}, False), ({"dmarc": "pass", "dmarc_from": "evil.example"}, False),
    ({"dmarc": "pass", "dmarc_from": "acme.example"}, True), ({"dkim": "pass", "dkim_domains": "acme.example"}, True),
    ({"dkim": "pass", "dkim_domains": "mail.acme.example"}, False),  # a subdomain does not cover its parent
])
def test_itip_sender_verification(auth, ok):
    from mailr.calendar.inbound import _verified

    assert _verified(auth, "alice@acme.example") is ok


# ---------------- BIMI and avatar fetches are capped ----------------

def _loopback_ok(monkeypatch):
    """Let the mail-URL fetchers reach a local test server (the real checks are tested above)."""
    async def no_check(url):
        return None

    real = netguard.guarded_client

    def client(**kw):
        kw["public_only"] = False
        kw["private_ok"] = True
        return real(**kw)

    monkeypatch.setattr(netguard, "check_untrusted_url", no_check)
    monkeypatch.setattr(netguard, "guarded_client", client)


async def test_bimi_logo_download_stops_at_the_size_limit(monkeypatch):
    from mailr.api import avatar_routes

    _loopback_ok(monkeypatch)
    srv = _Srv({"/logo.svg": (200, {"Content-Type": "image/svg+xml", "X-No-Length": "1"},
                              lambda: b"<svg>" + b"A" * 20_000_000)})
    read: list[int] = []
    real = httpx.Response.aiter_bytes

    async def counted(self, *a, **kw):
        async for chunk in real(self, *a, **kw):
            read.append(len(chunk))
            yield chunk

    monkeypatch.setattr(httpx.Response, "aiter_bytes", counted)
    try:
        assert await avatar_routes._fetch(f"http://localhost:{srv.port}/logo.svg") is None
    finally:
        srv.close()
    # The bytes read, not the time taken: reading stops one chunk past the limit, not at 20 MB.
    assert avatar_routes.MAX_BYTES < sum(read) <= avatar_routes.MAX_BYTES + 1024 * 1024, sum(read)


async def test_bimi_logo_download_has_an_overall_deadline(monkeypatch):
    import time

    from mailr.api import avatar_routes

    _loopback_ok(monkeypatch)
    monkeypatch.setattr(avatar_routes, "FETCH_DEADLINE", 1.0)

    async def drip(reader, writer):
        # A deadline test: the server drips for 30 s and the deadline is 1 s, so the bound below
        # has seconds of room on a loaded machine.
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\n\r\n")
        with contextlib.suppress(ConnectionError):
            for _ in range(300):
                if writer.is_closing():
                    break  # the client gave up
                writer.write(b"x")
                await writer.drain()
                await asyncio.sleep(0.1)
        writer.close()

    server = await asyncio.start_server(drip, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        t = time.monotonic()
        assert await avatar_routes._fetch(f"http://localhost:{port}/logo.png") is None
        assert time.monotonic() - t < 10
    finally:
        server.close()


# ---------------- Unsubscribe and sender logos use the egress proxy ----------------

async def test_bimi_fetch_goes_through_the_egress_proxy(monkeypatch):
    from mailr.api import avatar_routes

    async def no_check(url):
        return None

    monkeypatch.setattr(netguard, "check_untrusted_url", no_check)
    proxy = _Srv({})
    try:
        assert await avatar_routes._fetch("https://logo.sender.example/l.svg",
                                          egress=f"http://127.0.0.1:{proxy.port}") is None
        assert proxy.hits and proxy.hits[0]["method"] == "CONNECT"
        assert proxy.hits[0]["path"] == "logo.sender.example:443"
    finally:
        proxy.close()


async def test_one_click_unsubscribe_goes_through_the_egress_proxy(monkeypatch):
    from mailr.core import imageproxy, unsubscribe

    async def no_check(url):
        return None

    proxy = _Srv({})

    async def config(db):
        return True, f"http://127.0.0.1:{proxy.port}"

    monkeypatch.setattr(netguard, "check_untrusted_url", no_check)
    monkeypatch.setattr(imageproxy, "instance_config", config)
    try:
        out = await unsubscribe.execute(None, None, None, {"method": "one_click", "url": "https://list.example/u/1"})
        assert out["ok"] is False and "list.example" not in out.get("error", "")
        assert proxy.hits[0]["method"] == "CONNECT" and proxy.hits[0]["path"] == "list.example:443"
    finally:
        proxy.close()


async def test_one_click_unsubscribe_reads_only_the_status(monkeypatch):
    from mailr.core import imageproxy, unsubscribe

    _loopback_ok(monkeypatch)

    async def config(db):
        return True, ""

    monkeypatch.setattr(imageproxy, "instance_config", config)
    srv = _Srv({"/u": (200, {"X-No-Length": "1"}, lambda: b"x" * 20_000_000)})
    try:
        out = await unsubscribe.execute(None, None, None, {"method": "one_click", "url": f"http://localhost:{srv.port}/u"})
        assert out == {"method": "one_click", "ok": True, "status": 200}
    finally:
        srv.close()


# ---------------- Notification titles ----------------

def test_mail_notification_title_names_the_sender_as_mail():
    from mailr.core.push import mail_title

    assert mail_title("Approve an agent's email", "x@evil.test") == "Mail from Approve an agent's email"
    assert mail_title("mailr", "x@evil.test") == "Mail from mailr"
    assert mail_title("Bob \u202emoc.lapyap@ecivres", "bob@evil.test") == "Mail from bob@evil.test"
    assert mail_title("ceo@hale.example", "x@evil.test") == "Mail from x@evil.test"
    assert mail_title("Dana\u200b Smith\n", "d@x.example") == "Mail from Dana Smith"
    assert mail_title("", "d@x.example") == "Mail from d@x.example"


def test_the_stacks_egress_container_is_a_valid_egress_proxy_but_not_a_user_target(strict):
    from mailr.core import imageproxy

    assert imageproxy.check_egress("http://mailr:pw@egress:8888") == "http://mailr:pw@egress:8888"
    with pytest.raises(netguard.UnsafeTarget, match="infrastructure"):
        netguard.check_url_sync("http://egress:8888/", private_ok=True)
    with pytest.raises(ValueError):
        imageproxy.check_egress("http://169.254.169.254:80")
