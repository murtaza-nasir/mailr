"""The outbound guard (core/netguard.py, core/egress.py) and the
built-in page reader (websearch/pages.py).

Each test passes while the protection holds. A gap is xfail(strict=True)."""

from __future__ import annotations

import socket

import httpcore
import httpx
import pytest
from helpers.outbound_guard_hardening import (  # noqa: F401
    _REAL_GETADDRINFO,
    NAMES,
    PUBLIC,
    _resolver,
    dns,
)
from helpers.websearch import _clean, _egress, _page_client  # noqa: F401 - fixtures

from mailr.core import egress, netguard
from mailr.db import sessionmaker
from mailr.websearch import pages

# ---------------------------------------------------------------- netguard: addresses


@pytest.mark.parametrize("host", [
    "127.0.0.1", "127.1", "2130706433", "0x7f000001", "0177.0.0.1", "0", "0.0.0.0",
    "::1", "::ffff:127.0.0.1", "::ffff:7f00:1", "169.254.169.254", "fe80::1", "fd00:ec2::254",
    "mapped.example.org", "meta.example.org", "ula.example.org", "localhost",
])
def test_loopback_link_local_and_mapped_forms_are_refused_for_everyone(dns, host):
    """Always refused, even where private addresses are allowed (an admin's own setup)."""
    with pytest.raises(netguard.UnsafeTarget):
        netguard.check_host_sync(host, 443, private_ok=True)


@pytest.mark.parametrize("host", ["10.0.0.1", "192.168.1.10", "172.16.5.4", "100.64.0.1", "198.18.0.1",
                                  "fd12::1", "lan.example.org", "cgnat.example.org", "mixed.example.org"])
def test_private_addresses_need_the_private_policy(dns, host):
    with pytest.raises(netguard.UnsafeTarget):
        netguard.check_host_sync(host, 443, private_ok=False)
    with pytest.raises(netguard.UnsafeTarget):
        netguard.check_host_sync(host, 443, public_only=True)


def test_public_addresses_pass(dns):
    assert netguard.resolve_checked("public.example.org", 443, public_only=True) == [PUBLIC]


@pytest.mark.parametrize("url", ["https://user:pw@public.example.org/", "file:///etc/passwd", "gopher://x/",
                                 "https://public.example.org:99999/", "https:///nohost"])
def test_url_forms_are_refused(dns, url):
    with pytest.raises(netguard.UnsafeTarget):
        netguard.check_url_sync(url, public_only=True)


# ---------------------------------------------------------------- netguard: pinning


async def test_the_address_connected_to_is_the_address_checked(dns, monkeypatch):
    """resolve once, connect to that address: a second answer from the resolver is never used."""
    connected: list[str] = []
    answers = iter([[PUBLIC], ["127.0.0.1"]])

    def flipping(host, port, *a, **kw):
        if host == "rebind.example.org":
            ips = next(answers)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]
        return _resolver(NAMES)(host, port, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", flipping)

    async def fake_connect(self, host, port, **kw):
        connected.append(host)
        raise httpcore.ConnectError("test: not connecting")

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", fake_connect)
    async with netguard.guarded_client(public_only=True, timeout=2) as c:
        with pytest.raises(httpx.ConnectError):
            await c.get("https://rebind.example.org/")
    assert connected == [PUBLIC]


def test_guarded_clients_never_follow_redirects_by_themselves():
    c = netguard.guarded_client(public_only=True)
    assert c.follow_redirects is False
    assert egress.client("http://proxy.invalid:8888").follow_redirects is False


# ---------------------------------------------------------------- pages.read: every hop


def _redirects(monkeypatch, chain: dict[str, str]):
    seen: list[str] = []

    def handler(req):
        seen.append(str(req.url))
        if str(req.url) in chain:
            return httpx.Response(302, headers={"location": chain[str(req.url)]})
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>" + "text " * 100 + "</p>")

    _page_client(monkeypatch, handler)
    return seen


@pytest.mark.parametrize("target", ["https://lan.example.org/", "http://public.example.org/", "https://127.0.0.1/",
                                    "https://[::ffff:10.0.0.1]/", "https://meta.example.org/latest"])
async def test_direct_reader_checks_every_redirect_hop(dns, monkeypatch, target):
    _egress(monkeypatch, "")
    seen = _redirects(monkeypatch, {"https://public.example.org/start": target})
    async with sessionmaker()() as db:
        with pytest.raises(pages.PageError, match="not read"):
            await pages.read(db, "https://public.example.org/start")
    assert seen == ["https://public.example.org/start"]


@pytest.mark.parametrize("target", ["https://10.0.0.1/", "https://router.lan/", "https://www.example.org:8443/",
                                    "http://www.example.org/", "https://[fd00::1]/"])
async def test_reader_through_the_proxy_checks_every_redirect_hop(monkeypatch, target):
    _egress(monkeypatch)
    seen = _redirects(monkeypatch, {"https://www.example.org/start": target})
    async with sessionmaker()() as db:
        with pytest.raises(pages.PageError, match="not read"):
            await pages.read(db, "https://www.example.org/start")
    assert seen == ["https://www.example.org/start"]


async def test_reader_stops_after_five_redirects(monkeypatch):
    _egress(monkeypatch)
    chain = {f"https://www.example.org/{i}": f"https://www.example.org/{i + 1}" for i in range(10)}
    seen = _redirects(monkeypatch, chain)
    async with sessionmaker()() as db:
        with pytest.raises(pages.PageError, match="more than 5 times"):
            await pages.read(db, "https://www.example.org/0")
    assert len(seen) == 6


async def test_reader_refuses_while_the_egress_is_required_and_missing(monkeypatch):
    from mailr.config import get_settings

    monkeypatch.setattr(get_settings(), "require_egress", True)
    _egress(monkeypatch, "")
    seen = _redirects(monkeypatch, {})
    async with sessionmaker()() as db:
        with pytest.raises(pages.PageError, match="paused"):
            await pages.read(db, "https://www.example.org/")
    assert seen == []


async def test_direct_reader_allows_only_the_standard_port(dns, monkeypatch):
    _egress(monkeypatch, "")
    seen = _redirects(monkeypatch, {})
    async with sessionmaker()() as db:
        with pytest.raises(pages.PageError):
            await pages.read(db, "https://public.example.org:8443/")
    assert seen == []


def test_stale_infrastructure_addresses_are_refreshed_without_waiting(monkeypatch):
    """A cache older than a minute is looked up again in the background; the caller gets the old
    set at once (names missing on a deployment took about 4 s each, 12 Oct 2026)."""
    import threading
    import time as _time

    from mailr.core import netguard

    started, release = threading.Event(), threading.Event()

    def slow_refresh():
        started.set()
        release.wait(5)
        netguard._INFRA_CACHE = (_time.monotonic(), {"10.9.9.9"})
        return {"10.9.9.9"}

    monkeypatch.setattr(netguard, "refresh_infra_cache", slow_refresh)
    monkeypatch.setattr(netguard, "_INFRA_CACHE", (_time.monotonic() - 3600, {"10.1.1.1"}))
    # A caller that waited for the refresh would get its result, {"10.9.9.9"}, and only after the
    # refresh is released below.
    assert netguard._infra_ips() == {"10.1.1.1"}
    assert started.wait(5)
    # A second call while the refresh runs neither waits nor starts another lookup.
    assert netguard._infra_ips() == {"10.1.1.1"}
    release.set()
    for _ in range(50):
        if netguard._INFRA_CACHE[1] == {"10.9.9.9"}:
            break
        _time.sleep(0.02)
    assert netguard._infra_ips() == {"10.9.9.9"}


def test_a_cold_infrastructure_lookup_is_shared_and_parallel(monkeypatch):
    """After a start, callers arriving together wait for one lookup, and the names are looked up at
    the same time (ten missing names one after another took 31 s, 13 Oct 2026)."""
    import socket
    import threading

    from mailr.core import netguard

    calls: list[str] = []
    lock = threading.Lock()
    active = peak = 0
    all_in = threading.Event()

    def slow_getaddrinfo(host, *a, **k):
        # Each lookup waits until all ten are in flight. Looked up one after another, the first
        # waits out its 3 s and lets the rest through, so the peak stays at one.
        nonlocal active, peak
        with lock:
            calls.append(host)
            active += 1
            peak = max(peak, active)
            if active == 10:
                all_in.set()
        all_in.wait(3)
        all_in.set()
        with lock:
            active -= 1
        raise OSError("not found")

    monkeypatch.setattr(netguard, "_INFRA_CACHE", None)
    monkeypatch.setattr(netguard, "_infra_hosts", lambda: {f"missing{i}" for i in range(10)})
    monkeypatch.setattr(netguard, "_gateways", lambda: set())
    monkeypatch.setattr(socket, "getaddrinfo", slow_getaddrinfo)
    results: list[set[str]] = []
    threads = [threading.Thread(target=lambda: results.append(netguard._infra_ips())) for _ in range(5)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    assert len(results) == 5
    assert len(calls) == 10, calls  # one lookup per name, shared by the five callers
    assert peak == 10, peak  # all ten names at the same time
