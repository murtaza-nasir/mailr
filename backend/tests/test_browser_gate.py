"""The browser gate (websearch/gate.py).

- The compose network table: the browser's only network is internal and holds nothing but the browser
  and the gate; the gate is not on the stores' network; mailr reaches the browser only through it.
- The gate itself, on real sockets: only CONNECT to port 443; the offline rules and netguard's lookup
  (public addresses, the connection pinned to the checked address); the egress port signs in to the
  egress proxy itself, so the browser never holds the password; no upstream, no tunnel.
- The server's reader with MAILR_BROWSER_GATE: Chrome's proxy is the gate's port for the route.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
from functools import partial
from pathlib import Path

import httpx
import pytest
import yaml
from helpers.outbound_guard_hardening import dns  # noqa: F401 - fixture
from helpers.own_links_readers import PAGE, WEBRTC, _lan_ok, _reader
from helpers.websearch import _clean, _egress, _mock  # noqa: F401 - fixtures

from mailr.config import get_settings
from mailr.core import netguard
from mailr.db import sessionmaker
from mailr.models import WebSearchProvider
from mailr.websearch import gate, readers, service

REPO = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO / "deploy" / "docker-compose.yml"
COMPOSE = yaml.safe_load(COMPOSE_FILE.read_text())
SERVICES = COMPOSE["services"]


def _nets(name: str) -> set[str]:
    n = SERVICES[name].get("networks") or ["default"]
    return set(n if isinstance(n, list) else n.keys())


# ---------------------------------------------------------------- the network table


def test_the_gate_reaches_neither_store_and_carries_no_secrets():
    g = SERVICES["browser-gate"]
    assert not _nets("browser-gate") & (_nets("postgres") | _nets("stalwart") | _nets("resolver")
                                         | _nets("dav-gateway"))
    assert _nets("browser-gate") == {"browser-net", "egress", "browser-out"}
    assert g["profiles"] == ["browser"] and g["command"] == ["mailr", "browser-gate"]
    assert "ports" not in g and "env_file" not in g and "volumes" not in g
    assert g["cap_drop"] == ["ALL"] and "no-new-privileges:true" in g["security_opt"] and g["read_only"] is True
    assert set(g["environment"]) == {"MAILR_BROWSER_GATE_UPSTREAM", "MAILR_BROWSER_GATE_TARGET"}
    # mailr's MAILR_BROWSER_URL (http://browser:3000) names the gate on the app's side, and the gate
    # relays to the browser by its name on browser-net.
    assert g["networks"]["egress"] == {"aliases": ["browser"]}
    assert SERVICES["browser"]["networks"]["browser-net"] == {"aliases": ["chrome"]}
    assert g["environment"]["MAILR_BROWSER_GATE_TARGET"] == "chrome:3000"
    for s in ("app", "worker"):
        env = SERVICES[s].get("environment") or {}
        assert env.get("MAILR_BROWSER_GATE") == "${MAILR_BROWSER_GATE-browser-gate}", s
    # Only the gate is on browser-out (the direct route's way out).
    assert {s for s in SERVICES if "browser-out" in _nets(s)} == {"browser-gate"}


@pytest.mark.skipif(not shutil.which("docker"), reason="docker is not installed")
def test_the_resolved_compose_file_keeps_the_table(tmp_path):
    """`docker compose config` with the vpn and browser profiles: the same table after interpolation
    and merging."""
    deploy = tmp_path / "deploy"
    shutil.copytree(REPO / "deploy", deploy, ignore=shutil.ignore_patterns(".env", "backup"))
    (deploy / ".env").write_text((REPO / "deploy" / ".env.example").read_text())
    r = subprocess.run(["docker", "compose", "-f", str(deploy / "docker-compose.yml"), "config", "--format", "json"],
                       capture_output=True, text=True, timeout=60,
                       env={**{k: v for k, v in os.environ.items() if not k.startswith(("MAILR_", "COMPOSE_"))},
                            "COMPOSE_PROFILES": "vpn,browser"})
    if r.returncode != 0 and "unknown" in r.stderr and "compose" in r.stderr:
        pytest.skip("docker compose is not available")
    assert r.returncode == 0, r.stderr
    cfg = json.loads(r.stdout)
    svc = cfg["services"]
    assert set(svc["browser"]["networks"]) == {"browser-net"}
    assert cfg["networks"]["browser-net"]["internal"] is True
    members = {name for name, s in svc.items() if "browser-net" in (s.get("networks") or {})}
    assert members == {"browser", "browser-gate"}
    for store in ("postgres", "stalwart", "app", "worker", "egress"):
        assert not set(svc[store].get("networks") or {"default": None}) & {"browser-net"}, store


# ---------------------------------------------------------------- the gate's rules


@pytest.mark.parametrize(("head", "status"), [
    (b"GET http://www.example.org/ HTTP/1.1\r\nHost: www.example.org\r\n\r\n", 403),
    (b"CONNECT www.example.org:80 HTTP/1.1\r\n\r\n", 403),
    (b"CONNECT www.example.org:8443 HTTP/1.1\r\n\r\n", 403),
    (b"CONNECT www.example.org HTTP/1.1\r\n\r\n", 400),
    (b"CONNECT [::1 HTTP/1.1\r\n\r\n", 400),
    (b"hello\r\n\r\n", 400),
])
def test_only_connect_to_443(head, status):
    with pytest.raises(gate.Refused) as e:
        gate.parse_request(head)
    assert e.value.status == status


def test_targets_parse():
    assert gate.parse_request(b"CONNECT WWW.Example.org.:443 HTTP/1.1\r\n\r\n") == ("www.example.org", 443)
    assert gate.parse_request(b"CONNECT [2606:2800::1]:443 HTTP/1.1\r\n\r\n") == ("2606:2800::1", 443)


@pytest.mark.parametrize("host", ["10.0.0.1", "192.168.1.40", "172.20.0.3", "100.100.100.100", "127.1", "2130706433",
                                  "::1", "fd00::1", "app", "browser-gate", "egress", "router.lan", "printer.local",
                                  "localhost", "x.home.arpa"])
def test_private_literals_and_local_names_are_refused_before_any_lookup(host):
    with pytest.raises(gate.Refused) as e:
        gate.check_host(host)
    assert e.value.status == 403


async def test_direct_route_resolves_once_and_connects_to_the_checked_address(dns, monkeypatch):
    calls: list[tuple[str, int]] = []

    async def connect(host, port):
        calls.append((host, port))
        raise OSError("not in tests")

    monkeypatch.setattr(gate, "_connect", connect)
    with pytest.raises(gate.Refused) as e:
        await gate.open_direct("lan.example.org", 443)  # a public name for a LAN address
    assert e.value.status == 403 and calls == []
    with pytest.raises(gate.Refused):
        await gate.open_direct("mixed.example.org", 443)  # any private answer refuses the name
    assert calls == []
    # Rebinding: the first answer is checked and used; the name is never resolved again.
    answers = iter([["93.184.216.34"], ["192.168.1.1"]])

    def resolve(host, port, *, public_only=False, private_ok=None):
        assert public_only
        ips = next(answers)
        for ip in ips:
            netguard._check_ip(ip, host, public_only=True)
        return ips

    monkeypatch.setattr(netguard, "resolve_checked", resolve)
    with pytest.raises(gate.Refused) as e:
        await gate.open_direct("rebind.example.org", 443)
    assert e.value.status == 502 and calls == [("93.184.216.34", 443)]


# ---------------------------------------------------------------- the gate on real sockets


async def _server(handler) -> tuple[asyncio.Server, int]:
    srv = await asyncio.start_server(handler, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


async def _echo(reader, writer):
    while data := await reader.read(1024):
        writer.write(data)
        await writer.drain()
    writer.close()


async def _ask(port: int, head: bytes, then: bytes = b"") -> tuple[bytes, bytes]:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(head)
    await w.drain()
    answer = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 5)
    more = b""
    if then and answer.startswith(b"HTTP/1.1 200"):
        w.write(then)
        await w.drain()
        more = await asyncio.wait_for(r.readexactly(len(then)), 5)
    w.close()
    return answer, more


async def test_egress_port_signs_in_upstream_and_the_browser_never_holds_the_password(monkeypatch):
    seen: list[bytes] = []

    async def upstream(reader, writer):
        seen.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await _echo(reader, writer)

    up, up_port = await _server(upstream)
    g = gate.Gate(gate.parse_upstream(f"http://mailr:egress-pass-123@127.0.0.1:{up_port}"), ("127.0.0.1", 1))
    srv, port = await _server(partial(g.tunnel, route="egress"))
    try:
        answer, echoed = await _ask(port, b"CONNECT www.example.org:443 HTTP/1.1\r\nHost: www.example.org:443\r\n\r\n",
                                    b"tls-bytes")
        assert answer.startswith(b"HTTP/1.1 200") and echoed == b"tls-bytes"
        assert b"egress-pass-123" not in answer
        head = seen[-1].decode()
        assert head.startswith("CONNECT www.example.org:443 HTTP/1.1\r\n")
        assert f"Proxy-Authorization: Basic {base64.b64encode(b'mailr:egress-pass-123').decode()}" in head
        # A private literal or a stack name never reaches the egress proxy (gluetun would reach its
        # own Docker network).
        n = len(seen)
        for target in (b"172.20.0.3:443", b"app:443", b"10.0.0.1:443", b"www.example.org:8025"):
            answer, _ = await _ask(port, b"CONNECT " + target + b" HTTP/1.1\r\n\r\n")
            assert answer.startswith(b"HTTP/1.1 403"), target
        assert len(seen) == n
    finally:
        srv.close()
        up.close()


async def test_egress_port_without_an_upstream_refuses():
    g = gate.Gate(None, ("127.0.0.1", 1))
    srv, port = await _server(partial(g.tunnel, route="egress"))
    try:
        answer, _ = await _ask(port, b"CONNECT www.example.org:443 HTTP/1.1\r\n\r\n")
        assert answer.startswith(b"HTTP/1.1 502")
    finally:
        srv.close()


async def test_direct_port_tunnels_to_a_checked_public_address(dns, monkeypatch):
    echo, echo_port = await _server(_echo)
    real = asyncio.open_connection
    asked: list[tuple[str, int]] = []

    async def connect(host, port):
        asked.append((host, port))
        return await real("127.0.0.1", echo_port)  # stands in for 93.184.216.34:443

    monkeypatch.setattr(gate, "_connect", connect)
    g = gate.Gate(None, ("127.0.0.1", 1))
    srv, port = await _server(partial(g.tunnel, route="direct"))
    try:
        answer, echoed = await _ask(port, b"CONNECT public.example.org:443 HTTP/1.1\r\n\r\n", b"hello")
        assert answer.startswith(b"HTTP/1.1 200") and echoed == b"hello"
        assert asked == [("93.184.216.34", 443)]
        for target in (b"lan.example.org:443", b"cgnat.example.org:443", b"meta.example.org:443",
                       b"localhost:443", b"192.168.1.40:443"):
            answer, _ = await _ask(port, b"CONNECT " + target + b" HTTP/1.1\r\n\r\n")
            assert answer.startswith(b"HTTP/1.1 403"), target
        assert asked == [("93.184.216.34", 443)]
    finally:
        srv.close()
        echo.close()


async def test_relay_passes_mailrs_requests_to_browserless():
    echo, echo_port = await _server(_echo)
    g = gate.Gate(None, ("127.0.0.1", echo_port))
    srv, port = await _server(g.relay)
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"POST /content?token=t HTTP/1.1\r\n\r\n")
        await w.drain()
        assert await asyncio.wait_for(r.readexactly(34), 5) == b"POST /content?token=t HTTP/1.1\r\n\r\n"
        w.close()
    finally:
        srv.close()
        echo.close()


def test_upstream_and_target_settings():
    assert gate.parse_upstream("") is None
    up = gate.parse_upstream("http://mailr:p%40ss@egress:8888")
    assert (up.host, up.port, up.username, up.password) == ("egress", 8888, "mailr", "p@ss")
    with pytest.raises(ValueError):
        gate.parse_upstream("socks5://egress:1080")
    assert gate.parse_target("chrome:3000") == ("chrome", 3000)


# ---------------------------------------------------------------- the server's reader behind the gate


@pytest.fixture
def behind_gate(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "browser_url", "http://browser:3000")
    monkeypatch.setattr(s, "browser_token", "srv-token-XYZ")
    monkeypatch.setattr(s, "browser_gate", "browser-gate")


async def _env_reader(use_egress: bool) -> int:
    return await _reader(base_url="http://browser:3000", from_env=True, use_egress=use_egress)


async def _read(pid: int, url: str):
    async with sessionmaker()() as db:
        p = await db.get(WebSearchProvider, pid)
        return await readers.read_with(db, p, url, count_usage=False)


def _ok(monkeypatch, final=None):
    def handler(req):
        h = {"content-type": "text/html", "x-response-code": "200"}
        h["x-response-url"] = final or json.loads(req.content)["url"]
        return httpx.Response(200, headers=h, text=PAGE)

    return _mock(monkeypatch, handler)


async def test_server_reader_egress_route_goes_through_the_gate(behind_gate, monkeypatch):
    _egress(monkeypatch, "http://mailr:egress-pass-123@egress:8888")
    _lan_ok(monkeypatch)
    pid = await _env_reader(True)
    seen = _ok(monkeypatch)
    out = await _read(pid, "https://www.example.org/")
    assert out["route"] == "egress"
    args = json.loads(seen[-1].url.params["launch"])["args"]
    assert args == ["--proxy-server=http://browser-gate:3129", "--proxy-bypass-list=<-loopback>", *WEBRTC]
    body = seen[-1].content.decode()
    assert "egress-pass-123" not in body and "authenticate" not in body
    assert json.loads(body)["rejectRequestPattern"]


async def test_server_reader_direct_route_goes_through_the_gate(behind_gate, dns, monkeypatch):
    _egress(monkeypatch, "")
    _lan_ok(monkeypatch)
    pid = await _env_reader(False)
    seen = _ok(monkeypatch)
    out = await _read(pid, "https://public.example.org/")
    assert out["route"] == "direct"
    args = json.loads(seen[-1].url.params["launch"])["args"]
    assert args == ["--proxy-server=http://browser-gate:3128", "--proxy-bypass-list=<-loopback>", *WEBRTC]
    # mailr's own check still runs first, and the address the browser ended on is checked.
    n = len(seen)
    with pytest.raises(service.ProviderError, match="^not read"):
        await _read(pid, "https://lan.example.org/")
    assert len(seen) == n
    _ok(monkeypatch, final="https://lan.example.org/x")
    with pytest.raises(service.ProviderError, match="moved to an address that is not allowed"):
        await _read(pid, "https://public.example.org/")


def test_gate_errors_name_the_gate():
    proxy = readers.gate_proxy("browser-gate", "direct")
    assert readers.browser_error("net::ERR_TUNNEL_CONNECTION_FAILED", proxy).startswith(
        "the browser gate (browser-gate:3128) did not open the site")
    assert readers.browser_error("net::ERR_PROXY_CONNECTION_FAILED", proxy).startswith(
        "the browser could not reach the browser gate (browser-gate:3128)")
