"""Attacks on the page-reading browser that must fail.

- DNS rebinding: when the page ends on the same host name, the name is resolved again and the
  new answer is checked, not the one the browser was pinned to. The pinning itself, the refusal of a
  name that resolves to the LAN and the egress route's filter and end check are in
  tests/test_page_readers_hardening.py.
- A token with `read`: an API token with `read` still chooses its own
  pages (agent/web_tools.py). What changed is what the page can
  reach: the token's read goes through the same reader chain, so a name that resolves to the LAN is
  refused for it like for anyone.
"""

from __future__ import annotations

import socket
import uuid

import httpx
import pytest
from helpers.agent import _token_client
from helpers.browser_routes import _browserless
from helpers.own_links_readers import _lan_ok, _reader
from helpers.websearch import _clean, _egress  # noqa: F401 - fixtures

from mailr.db import sessionmaker
from mailr.models import WebSearchProvider
from mailr.websearch import readers, service

HOST = "rebind.attacker.test"
INTERNAL = ("<html><head><title>Router admin</title></head><body><main><p>" + "WAN PPPoE password hunter2. " * 20
            + "</p></main></body></html>")


def _resolver(monkeypatch, answers: dict[str, list[str]]):
    real = socket.getaddrinfo
    lookups: list[str] = []

    def fake(host, *a, **kw):
        h = host.decode() if isinstance(host, bytes) else host
        if h in answers:
            lookups.append(h)
            ip = answers[h][min(len(lookups), len(answers[h])) - 1]
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
        return real(host, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return lookups


async def _read(pid: int, url: str):
    async with sessionmaker()() as db:
        p = await db.get(WebSearchProvider, pid)
        return await readers.read_with(db, p, url, count_usage=False)


async def test_m3_where_the_page_ended_is_resolved_and_checked_again(monkeypatch):
    """The second answer of a rebinding name is checked when the page ends there."""
    _egress(monkeypatch, "")
    _lan_ok(monkeypatch)
    _resolver(monkeypatch, {HOST: ["93.184.216.34", "192.168.1.1"]})
    pid = await _reader(use_egress=False)
    _browserless(monkeypatch, lambda req: httpx.Response(
        200, headers={"content-type": "text/html", "x-response-code": "200"}, text=INTERNAL))
    with pytest.raises(service.ProviderError, match="moved to an address that is not allowed"):
        await _read(pid, f"https://{HOST}/")


async def test_m3_a_read_token_reaches_only_public_pages(client, user, monkeypatch):
    """The token still opens its own pages; a page on the LAN is refused for it, by the reader chain."""
    _egress(monkeypatch, "")
    _resolver(monkeypatch, {HOST: ["192.168.1.1"]})

    async def yes(db):
        return True

    monkeypatch.setattr(service, "available", yes)
    r = await client.post("/api/v1/tokens", json={"name": f"t-{uuid.uuid4().hex[:4]}", "scopes": ["read", "web"]})
    async with _token_client(client, r.json()["token"]) as a:
        r = await a.post("/api/v1/agent/tools/read_page", json={"url": f"https://{HOST}/admin"})
    assert r.status_code != 200 or "hunter2" not in r.text
    assert "private address" in r.text or "not read" in r.text, r.text
