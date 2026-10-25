"""The web app's error relay (api/error_routes.py) reaches only the error server named in the DSN:
no redirects, no address the DSN does not name, no second reading of a crafted DSN."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from httpx import ASGITransport, AsyncClient

from mailr.api import error_routes
from mailr.config import get_settings
from mailr.core import netguard


def _answers(*ips):
    def fake(host, port, *a, **kw):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in ips]
    return fake


@pytest.mark.parametrize("dsn", [
    "http://key@evil.example@glitchtip:8000/3",       # two readings of the host
    "http://key@glitchtip:8000\\@evil.example/3",     # a backslash in the authority
    "http://key@glitchtip:99999/3",                   # no such port
    "http://@glitchtip:8000/3",                       # no key
    "http://key@glitchtip:8000/three",                # no project number
    "http://key@glitchtip:8000/3?next=http://169.254.169.254/",
    "http://key@glitchtip:8000/3#@169.254.169.254",
    "gopher://key@glitchtip:8000/3",
    "http://key@glitch tip:8000/3",
    "http://key@glitchtip%2f@evil.example/3",
])
def test_a_crafted_dsn_is_not_a_target(dsn):
    assert error_routes._target(dsn) is None


def test_the_target_is_the_dsn_host():
    assert error_routes._target("http://pubkey@glitchtip:8000/3") == ("http://glitchtip:8000/api/3/envelope/", "pubkey")
    assert error_routes._target("https://abc@Errors.Example.com/sub/7/") == (
        "https://errors.example.com/sub/api/7/envelope/", "abc")
    assert error_routes._target("http://k@[fd00::5]:8000/1") == ("http://[fd00::5]:8000/api/1/envelope/", "k")


@pytest.mark.parametrize(("host", "answers", "ok"), [
    # A public name: public addresses only, whatever its DNS answers later.
    ("errors.example.com", ["93.184.216.34"], True),
    ("errors.example.com", ["169.254.169.254"], False),
    ("errors.example.com", ["127.0.0.1"], False),
    ("errors.example.com", ["10.0.0.5"], False),
    ("errors.example.com", ["93.184.216.34", "10.0.0.5"], False),
    ("errors.example.com", ["::ffff:169.254.169.254"], False),
    ("errors.example.com", ["fd00:ec2::254"], False),
    # A local name (a compose service, *.lan): private addresses too, never metadata or loopback.
    ("glitchtip", ["172.20.0.5"], True),
    ("errors.home.lan", ["192.168.1.20"], True),
    ("glitchtip", ["169.254.169.254"], False),
    ("glitchtip", ["127.0.0.1"], False),
    ("glitchtip", ["0.0.0.0"], False),
    # The DSN names the address itself.
    ("10.1.2.3", ["10.1.2.3"], True),
    ("127.0.0.1", ["127.0.0.1"], True),
    ("localhost", ["127.0.0.1"], True),
    ("169.254.169.254", ["169.254.169.254"], False),
])
def test_relay_addresses(monkeypatch, host, answers, ok):
    monkeypatch.setattr(error_routes.socket, "getaddrinfo", _answers(*answers))
    if ok:
        assert error_routes._relay_addresses(host, 8000) == answers
    else:
        with pytest.raises(netguard.UnsafeTarget):
            error_routes._relay_addresses(host, 8000)


class _Server:
    """A local HTTP server that counts its requests and answers each with `status` and `location`."""

    def __init__(self, status=200, location=""):
        hits = self.hits = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                hits.append(self.path)
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _envelope(dsn: str) -> bytes:
    payload = json.dumps({"event_id": "b" * 32, "message": "boom"}).encode()
    return b"\n".join([json.dumps({"dsn": dsn}).encode(),
                       json.dumps({"type": "event", "length": len(payload)}).encode(), payload]) + b"\n"


async def _relay(app, monkeypatch, dsn: str) -> int:
    monkeypatch.setattr(get_settings(), "sentry_browser_dsn", dsn)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        cfg = (await c.get("/api/v1/errors/config")).json()
        assert cfg["dsn"] == dsn
        return (await c.post(cfg["tunnel"], content=_envelope(dsn))).status_code


async def test_the_relay_follows_no_redirect(app, monkeypatch):
    inner = _Server()
    try:
        for location in ("http://169.254.169.254/latest/meta-data/", f"http://127.0.0.1:{inner.port}/admin",
                         f"http://localhost:{inner.port}/"):
            outer = _Server(302, location)
            try:
                # The DSN names a loopback address itself, so the relay may reach it, and only it.
                assert await _relay(app, monkeypatch, f"http://key@127.0.0.1:{outer.port}/3") == 200
                assert outer.hits == ["/api/3/envelope/?sentry_key=key&sentry_version=7"]
            finally:
                outer.close()
        assert inner.hits == []
    finally:
        inner.close()


async def test_a_public_dsn_name_never_reaches_a_local_address(app, monkeypatch):
    server = _Server()
    try:
        # The name answers with the loopback address (DNS rebinding): nothing is sent.
        monkeypatch.setattr(error_routes.socket, "getaddrinfo", _answers("127.0.0.1"))
        assert await _relay(app, monkeypatch, f"http://key@errors.example.com:{server.port}/3") == 200
        assert server.hits == []
    finally:
        server.close()


async def test_a_crafted_dsn_is_neither_offered_nor_relayed(app, monkeypatch):
    server = _Server()
    try:
        dsn = f"http://key@errors.example.com@127.0.0.1:{server.port}/3"
        monkeypatch.setattr(get_settings(), "sentry_browser_dsn", dsn)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.get("/api/v1/errors/config")).json() == {"dsn": None}
            key = error_routes.tunnel_key("127.0.0.1")
            r = await c.post(f"/api/v1/errors/envelope?k={key}", content=_envelope(dsn))
            assert r.status_code == 404
        assert server.hits == []
    finally:
        server.close()


def test_the_relay_client_ignores_proxy_settings_and_redirects(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    c = error_routes._client("https://k@errors.example/1")
    assert c.follow_redirects is False and c._trust_env is False
    assert isinstance(c._transport._pool._network_backend, netguard._PinnedBackend)
