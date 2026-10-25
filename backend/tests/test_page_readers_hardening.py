"""The page readers (websearch/readers.py) and the server's own
browser reader (MAILR_BROWSER_URL / MAILR_BROWSER_TOKEN).

Each test passes while the protection holds. A gap is xfail(strict=True)."""

from __future__ import annotations

import json
import re

import httpx
import pytest
from helpers.outbound_guard_hardening import dns  # noqa: F401 - fixture
from helpers.own_links_readers import PAGE, _lan_ok, _reader, _web_on
from helpers.websearch import _clean, _ctx, _egress, _mock, _page_client  # noqa: F401 - fixtures

from mailr.db import sessionmaker
from mailr.models import WebSearchProvider
from mailr.websearch import pages, readers, service

EGRESS = "http://mailr:egress-pass-123@egress:8888"
HTML = {"content-type": "text/html", "x-response-code": "200"}


async def _row(pid: int) -> WebSearchProvider:
    async with sessionmaker()() as db:
        return await db.get(WebSearchProvider, pid)


async def _read_with(pid: int, url: str):
    async with sessionmaker()() as db:
        p = await db.get(WebSearchProvider, pid)
        return await readers.read_with(db, p, url, count_usage=False)


def _browser_ok(monkeypatch, final: str | None = None):
    def handler(req):
        h = dict(HTML)
        h["x-response-url"] = final or json.loads(req.content)["url"]
        return httpx.Response(200, headers=h, text=PAGE)

    return _mock(monkeypatch, handler)


# ---------------------------------------------------------------- the page address


@pytest.mark.parametrize("kind,network", [("browserless", "local"), ("firecrawl", "local"), ("jina", "internet")])
@pytest.mark.parametrize("url", ["https://10.0.0.5/", "https://[::ffff:192.168.1.1]/", "https://2130706433/",
                                 "https://router.lan/", "https://nas/", "https://www.example.org:8443/",
                                 "http://www.example.org/", "https://u:p@www.example.org/"])
async def test_no_reader_is_asked_for_a_local_address(monkeypatch, kind, network, url):
    _egress(monkeypatch)
    _lan_ok(monkeypatch)
    base = "https://r.jina.ai" if network == "internet" else "http://reader.lan:3000"
    pid = await _reader(kind=kind, base_url=base, network=network)
    seen = _browser_ok(monkeypatch)
    with pytest.raises(service.ProviderError, match="not read"):
        await _read_with(pid, url)
    assert seen == []


async def test_direct_browser_looks_up_the_page_host_first(dns, monkeypatch):
    _egress(monkeypatch, "")
    _lan_ok(monkeypatch)
    pid = await _reader(use_egress=False)
    seen = _browser_ok(monkeypatch)
    # A name that resolves to the LAN is refused before the browser hears of it.
    with pytest.raises(service.ProviderError, match="^not read"):
        await _read_with(pid, "https://lan.example.org/")
    assert seen == []


@pytest.mark.parametrize("final", ["https://lan.example.org/x", "http://public.example.org/", "https://10.0.0.1/"])
async def test_direct_browser_checks_where_the_page_ended(dns, monkeypatch, final):
    _egress(monkeypatch, "")
    _lan_ok(monkeypatch)
    pid = await _reader(use_egress=False)
    _browser_ok(monkeypatch, final=final)
    with pytest.raises(service.ProviderError, match="moved to an address that is not allowed"):
        await _read_with(pid, "https://public.example.org/")


async def test_direct_browser_is_told_to_drop_private_requests(dns, monkeypatch):
    _egress(monkeypatch, "")
    _lan_ok(monkeypatch)
    pid = await _reader(use_egress=False)
    seen = _browser_ok(monkeypatch)
    await _read_with(pid, "https://public.example.org/")
    body = json.loads(seen[-1].content)
    # No proxy on this route; the page host is pinned to the address mailr checked.
    args = json.loads(seen[-1].url.params["launch"])["args"]
    assert not [a for a in args if a.startswith("--proxy-server")] and "authenticate" not in body
    assert "--host-resolver-rules=MAP public.example.org 93.184.216.34" in args
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args
    pats = [re.compile(p) for p in body["rejectRequestPattern"]]
    # URLs as Chrome reports them (hosts lower-cased, numeric IPv4 forms normalised, default ports dropped).
    for bad in ("http://127.0.0.1/", "http://10.1.2.3/", "http://172.20.0.3:8025/", "http://192.168.1.40/",
                "http://169.254.169.254/latest", "http://0.0.0.0/", "http://[::1]/", "http://[fd00::1]/",
                "ws://127.0.0.1:9222/devtools", "http://app:8025/", "http://egress:8888/", "http://router.lan/",
                "http://printer.local/", "http://x.home.arpa/", "http://localhost/", "https://www.example.org:8443/",
                "http://u@10.0.0.1/", "http://100.100.100.100/"):
        assert any(p.search(bad) for p in pats), bad
    for good in ("https://www.example.org/", "https://cdn.example.org/app.js", "http://www.example.org/x"):
        assert not any(p.search(good) for p in pats), good


def test_browser_patterns_cover_every_private_literal():
    pats = [re.compile(p) for p in readers._PRIVATE_PATTERNS]
    for bad in ("http://100.100.100.100/", "http://100.64.0.1/", "http://198.18.0.1/"):
        assert any(p.search(bad) for p in pats), bad


# ---------------------------------------------------------------- the egress route


async def _egress_browser(monkeypatch, final: str | None = None):
    _egress(monkeypatch, EGRESS)
    _lan_ok(monkeypatch)
    pid = await _reader()
    seen = _browser_ok(monkeypatch, final=final)
    return pid, seen


async def test_egress_browser_gets_the_proxy_address_without_its_password(monkeypatch):
    pid, seen = await _egress_browser(monkeypatch)
    await _read_with(pid, "https://www.example.org/")
    launch = seen[-1].url.params["launch"]
    assert "--proxy-server=http://egress:8888" in launch
    assert "egress-pass-123" not in launch and "mailr:" not in launch  # never in Chrome's command line


async def test_egress_browser_has_no_way_around_the_proxy(monkeypatch):
    pid, seen = await _egress_browser(monkeypatch)
    await _read_with(pid, "https://www.example.org/")
    args = json.loads(seen[-1].url.params["launch"])["args"]
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args
    assert "--proxy-bypass-list=<-loopback>" in args


async def test_egress_password_is_not_given_to_page_authentication(monkeypatch):
    pid, seen = await _egress_browser(monkeypatch)
    await _read_with(pid, "https://www.example.org/")
    assert "egress-pass-123" not in seen[-1].content.decode()
    assert "authenticate" not in json.loads(seen[-1].content)


async def test_egress_browser_is_told_to_drop_private_requests(monkeypatch):
    """The filter goes on the egress route too: a page must not reach the stack's own services."""
    pid, seen = await _egress_browser(monkeypatch)
    await _read_with(pid, "https://www.example.org/")
    pats = [re.compile(p) for p in json.loads(seen[-1].content)["rejectRequestPattern"]]
    for bad in ("http://172.18.0.4:8000/api/v1/internal", "http://192.168.1.40/", "http://egress:8888/"):
        assert any(p.search(bad) for p in pats), bad
    assert not any(p.search("https://www.example.org/x") for p in pats)


@pytest.mark.parametrize("final", ["http://172.20.0.3:8025/", "http://172.18.0.4:8000/api/v1/internal"])
async def test_egress_browser_checks_where_the_page_ended(monkeypatch, final):
    pid, _ = await _egress_browser(monkeypatch, final=final)
    with pytest.raises(service.ProviderError, match="moved to an address that is not allowed"):
        await _read_with(pid, "https://www.example.org/")


# ---------------------------------------------------------------- local readers other than the browser


def _firecrawl(monkeypatch, final: str):
    def handler(req):
        return httpx.Response(200, json={"success": True, "data": {"markdown": "Router admin page " * 20,
                                                                    "metadata": {"statusCode": 200, "sourceURL": final}}})

    return _mock(monkeypatch, handler)


async def test_local_firecrawl_is_not_asked_for_a_name_on_the_lan(dns, monkeypatch):
    _egress(monkeypatch, "")
    _lan_ok(monkeypatch)
    pid = await _reader(kind="firecrawl", base_url="http://firecrawl.lan:3002")
    seen = _firecrawl(monkeypatch, "https://lan.example.org/")
    with pytest.raises(service.ProviderError):
        await _read_with(pid, "https://lan.example.org/")
    assert seen == []


async def test_local_firecrawl_checks_where_the_page_ended(dns, monkeypatch):
    _egress(monkeypatch, "")
    _lan_ok(monkeypatch)
    pid = await _reader(kind="firecrawl", base_url="http://firecrawl.lan:3002")
    _firecrawl(monkeypatch, "http://192.168.1.10/admin")
    with pytest.raises(service.ProviderError):
        await _read_with(pid, "https://public.example.org/")


# ---------------------------------------------------------------- secrets


@pytest.fixture
def server_browser(monkeypatch):
    from mailr.config import get_settings

    monkeypatch.setattr(get_settings(), "browser_url", "http://browser:3000")
    monkeypatch.setattr(get_settings(), "browser_token", "srv-browser-token-XYZ")
    monkeypatch.setattr(get_settings(), "browser_route", "egress")


def _no_secret(*texts: str):
    for t in texts:
        for s in ("srv-browser-token-XYZ", "egress-pass-123", "reader-key-ABC"):
            assert s not in (t or ""), (s, t)


@pytest.mark.parametrize("answer", [
    lambda req: (_ for _ in ()).throw(httpx.ConnectError("connect failed", request=req)),
    lambda req: httpx.Response(401, text="Bad or missing authentication."),
    lambda req: httpx.Response(500, text="boom"),
    lambda req: httpx.Response(200, headers={"x-response-url": "chrome-error://chromewebdata/"}, text=""),
    lambda req: httpx.Response(200, headers={"content-type": "text/html", "x-response-code": "407"}, text=""),
])
async def test_server_browser_token_and_proxy_password_never_reach_errors(server_browser, monkeypatch, answer):
    _egress(monkeypatch, EGRESS)
    _page_client(monkeypatch, lambda req: httpx.Response(403, text="blocked"))

    def handler(req):
        assert req.url.params.get("token") == "srv-browser-token-XYZ"
        if req.url.path == "/function":
            return httpx.Response(200, json={"ok": False, "error": "net::ERR_PROXY_AUTH_REQUESTED at " + str(req.url)})
        return answer(req)

    _mock(monkeypatch, handler)
    async with sessionmaker()() as db:
        with pytest.raises(pages.PageError) as e:
            await readers.read(db, "https://www.example.org/")
        rows = await service.providers(db, purpose="reader")
    _no_secret(str(e.value), *[r.last_error for r in rows])
    assert all(r.api_key_enc is None for r in rows if r.from_env)  # the token is never stored


async def test_reader_keys_never_reach_errors(monkeypatch):
    _egress(monkeypatch, "")
    _page_client(monkeypatch, lambda req: httpx.Response(403, text="blocked"))
    pid = await _reader(kind="jina", base_url="https://r.jina.ai", network="internet", key="reader-key-ABC")

    def handler(req):
        assert req.headers["authorization"] == "Bearer reader-key-ABC"
        raise httpx.ConnectError("refused", request=req)

    _mock(monkeypatch, handler)
    async with sessionmaker()() as db:
        with pytest.raises(pages.PageError) as e:
            await readers.read(db, "https://www.example.org/")
    _no_secret(str(e.value), (await _row(pid)).last_error)


async def test_an_echoed_request_line_does_not_carry_the_token(server_browser, monkeypatch):
    _egress(monkeypatch, EGRESS)
    _page_client(monkeypatch, lambda req: httpx.Response(403, text="blocked"))
    _mock(monkeypatch, lambda req: httpx.Response(404, text=f"Cannot POST {req.url.raw_path.decode()}"))
    async with sessionmaker()() as db:
        with pytest.raises(pages.PageError) as e:
            await readers.read(db, "https://www.example.org/")
    _no_secret(str(e.value))


def test_browser_error_texts_name_the_proxy_without_its_password():
    proxy = readers.browser_proxy(EGRESS)
    assert proxy.password == "egress-pass-123"
    for code in ("ERR_PROXY_CONNECTION_FAILED", "ERR_PROXY_AUTH_REQUESTED", "ERR_TUNNEL_CONNECTION_FAILED", ""):
        _no_secret(readers.browser_error(f"net::{code}", proxy), readers._no_answer(proxy))


# ---------------------------------------------------------------- local-only content


async def test_read_page_refuses_in_a_chat_that_read_local_only_content(user, monkeypatch):
    from mailr.agent import web_tools
    from mailr.agent.tools import ToolError

    await _web_on()
    await _reader(kind="jina", base_url="https://r.jina.ai", network="internet")
    seen = _mock(monkeypatch, lambda req: httpx.Response(200, json={"data": {"content": "x"}}))
    pseen: list[str] = []
    _page_client(monkeypatch, lambda req: pseen.append(str(req.url)) or httpx.Response(200, text="x"))
    ctx = await _ctx(user.id, sensitive=True)
    ctx.user_urls.add("https://www.example.org/")
    try:
        with pytest.raises(ToolError, match="local-only"):
            await web_tools.read_page(ctx, {"url": "https://www.example.org/"})
    finally:
        await ctx.db.close()
    assert seen == [] and pseen == []
