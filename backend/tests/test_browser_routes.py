# ACT-SURFACES: web.settings
"""The browser page reader's two routes, the server's own reader and honest errors.

- Route "Directly from this network" (use_egress off on a local browserless reader): no --proxy-server,
  allowed under MAILR_REQUIRE_EGRESS, private targets still refused (the page, the address the browser
  ends on, private literals, local names and other ports in the browser); the route "Through the egress
  proxy" is unchanged.
- The reader set up by MAILR_BROWSER_URL / MAILR_BROWSER_TOKEN / MAILR_BROWSER_ROUTE ("Browser (server)"):
  listed by itself, its token from the environment only, an admin changes its route (audit-logged),
  never its URL, and cannot delete it.
- Honest errors: a browser that brought back no page says why (the egress proxy unreachable, HTTP 403,
  timed out), from a fake browserless's /function answer; never a secret.
- `mailr read-page`.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import httpx
import pytest
from helpers.browser_routes import _browserless
from helpers.own_links_readers import PAGE, WEBRTC, _lan_ok, _reader
from helpers.websearch import (  # noqa: F401 - fixtures
    _clean,
    _egress,
    _mock,
    _page_client,
    _require,
)
from sqlalchemy import select

from mailr.config import get_settings
from mailr.db import sessionmaker
from mailr.models import AuditLog, WebSearchProvider
from mailr.websearch import pages, readers, service

EGRESS = "http://vpnuser:s3cret-pw@egress:8888"


def _fn(data):
    return lambda req: httpx.Response(200, json=data)


def _public(monkeypatch):
    """Page hosts resolve to public addresses, except the ones named private here."""
    from mailr.core import netguard

    async def check(url):
        if "intranet" in url or "10.0.0.5" in url:
            raise netguard.UnsafeTarget("resolves to a private address")

    async def addresses(url):
        await check(url)
        return urlsplit(url).hostname, ["93.184.216.34"]

    monkeypatch.setattr(netguard, "check_untrusted_url", check)
    monkeypatch.setattr(netguard, "untrusted_url_addresses", addresses)


async def _read_one(pid: int, url: str = "https://app.example.com/start") -> dict:
    async with sessionmaker()() as db:
        p = await db.get(WebSearchProvider, pid)
        return await readers.read_with(db, p, url, count_usage=False)


# ---------------------------------------------------------------- honest errors


async def test_unreachable_egress_proxy_is_named_not_no_text(monkeypatch):
    _egress(monkeypatch, EGRESS)
    _lan_ok(monkeypatch)
    pid = await _reader(key="bl-token")
    seen = _browserless(monkeypatch, lambda req: httpx.Response(200, headers={"content-type": "text/html"}, text="\n"),
                        _fn({"data": {"ok": False, "error": "net::ERR_PROXY_CONNECTION_FAILED at https://app.example.com/start"},
                             "type": "application/json"}))
    with pytest.raises(service.ProviderError) as e:
        await _read_one(pid)
    msg = str(e.value)
    assert msg == "the browser could not reach the egress proxy (egress:8888) (net::ERR_PROXY_CONNECTION_FAILED)"
    assert "s3cret" not in msg and "bl-token" not in msg and "app.example.com" not in msg
    # The diagnosis navigates with the same proxy and sign-in, and lets only the page itself through.
    fn = seen[-1]
    assert fn.url.path == "/function" and fn.url.params["token"] == "bl-token"
    assert json.loads(fn.url.params["launch"]) == {"args": ["--proxy-server=http://egress:8888",
                                                             "--proxy-bypass-list=<-loopback>", *WEBRTC]}
    body = json.loads(fn.content)
    assert body["context"]["url"] == "https://app.example.com/start" and body["context"]["patterns"]
    # The proxy's password is never handed to the browser.
    assert "auth" not in body["context"] and "s3cret" not in fn.content.decode()
    assert "setRequestInterception" in body["code"]


async def test_chrome_error_page_and_no_function_still_say_what_happened(monkeypatch):
    _egress(monkeypatch, EGRESS)
    _lan_ok(monkeypatch)
    pid = await _reader()
    _browserless(monkeypatch, lambda req: httpx.Response(
        200, headers={"content-type": "text/html", "x-response-url": "chrome-error://chromewebdata/"},
        text='<html><body class="neterror"><div id="main-frame-error"><p>This site can’t be reached</p></div></body></html>'))
    with pytest.raises(service.ProviderError) as e:
        await _read_one(pid)
    assert str(e.value) == ("the browser got no answer for the page: check that the browser can reach the egress "
                            "proxy at egress:8888")
    # The same without an egress proxy (direct): no proxy hint.
    _egress(monkeypatch, "")
    _public(monkeypatch)
    with pytest.raises(service.ProviderError, match=r"^the browser got no answer for the page \(the site could not"):
        await _read_one(pid)


@pytest.mark.parametrize(("error", "expected"), [
    ("net::ERR_TUNNEL_CONNECTION_FAILED at https://x/", "the egress proxy (egress:8888) could not connect to the site"),
    ("net::ERR_PROXY_AUTH_UNSUPPORTED at https://x/", "the egress proxy (egress:8888) refused the browser's sign-in"),
    ("net::ERR_NAME_NOT_RESOLVED at https://x/", "the site's name could not be found"),
    ("net::ERR_CONNECTION_REFUSED at https://x/", "the browser could not connect to the site"),
    ("Navigation timeout of 13000 ms exceeded", "the page timed out"),
    ("net::ERR_CERT_DATE_INVALID at https://x/", "the site's secure connection failed"),
    ("net::ERR_BLOCKED_BY_CLIENT at https://x/", "the page leads to a private or local address"),
    ("net::ERR_SOMETHING_NEW at https://x/", "the browser could not load the page (net::ERR_SOMETHING_NEW)"),
])
def test_browser_error_words(error, expected):
    assert readers.browser_error(error, readers.BrowserProxy("http://egress:8888")).startswith(expected)
    assert readers.browser_error("", None) == ""


async def test_status_timeout_and_browserless_failures(monkeypatch):
    _egress(monkeypatch, EGRESS)
    _lan_ok(monkeypatch)
    pid = await _reader()
    _browserless(monkeypatch, lambda req: httpx.Response(200, headers={"x-response-code": "403"}, text="<p>denied</p>"))
    with pytest.raises(service.ProviderError, match=r"^the page answered HTTP 403$"):
        await _read_one(pid)
    # browserless answered 500 (no bestAttempt): the diagnosis says it timed out.
    _browserless(monkeypatch, lambda req: httpx.Response(500, text="Internal Server Error"),
                 _fn({"ok": False, "error": "Navigation timeout of 13000 ms exceeded"}))
    with pytest.raises(service.ProviderError, match=r"^the page timed out$"):
        await _read_one(pid)
    # The diagnosis sees an HTTP status where /content brought nothing back.
    _browserless(monkeypatch, lambda req: httpx.Response(200, text=""), _fn({"ok": True, "status": 429}))
    with pytest.raises(service.ProviderError, match=r"^the page answered HTTP 429$"):
        await _read_one(pid)
    # browserless gave up (408) and an error text naming a net:: code.
    _browserless(monkeypatch, lambda req: httpx.Response(408, text="Timed out"))
    with pytest.raises(service.ProviderError, match=r"^the page timed out: the browser gave up"):
        await _read_one(pid)
    _browserless(monkeypatch, lambda req: httpx.Response(400, text="net::ERR_PROXY_CONNECTION_FAILED at https://a/"))
    with pytest.raises(service.ProviderError, match=r"could not reach the egress proxy \(egress:8888\)"):
        await _read_one(pid)
    # A page that loaded (X-Response-Code 200) with no text keeps its plain message.
    _browserless(monkeypatch, lambda req: httpx.Response(200, headers={"x-response-code": "200"}, text="<div></div>"))
    with pytest.raises(service.ProviderError, match="found no text on the page"):
        await _read_one(pid)


# ---------------------------------------------------------------- routes


async def test_direct_route_sends_no_proxy_and_is_allowed_under_required_egress(monkeypatch):
    _require(monkeypatch)
    _egress(monkeypatch, EGRESS)
    _lan_ok(monkeypatch)
    _public(monkeypatch)
    pid = await _reader(use_egress=False)
    seen = _browserless(monkeypatch, lambda req: httpx.Response(
        200, headers={"content-type": "text/html", "x-response-code": "200", "x-response-url": "https://app.example.com/end"},
        text=PAGE))
    out = await _read_one(pid)
    assert out["route"] == "direct" and out["status"] == 200 and out["final_url"] == "https://app.example.com/end"
    req = seen[-1]
    body = json.loads(req.content)
    assert json.loads(req.url.params["launch"]) == {
        "args": [*WEBRTC, "--host-resolver-rules=MAP app.example.com 93.184.216.34"]}
    assert "authenticate" not in body
    pats = [re.compile(p) for p in body["rejectRequestPattern"]]
    for bad in ("http://192.168.1.1/admin", "http://10.1.2.3/", "http://router.lan/", "http://localhost:8080/",
                "http://browser:3000/", "http://nas/", "https://[::1]/", "https://cdn.example.com:8443/x.js",
                "http://203.0.113.9:22/"):
        assert any(p.search(bad) for p in pats), bad
    for good in ("https://www.reddit.com/r/x", "https://cdn.example.com/app.js", "https://cdn.example.com:443/a",
                 "http://example.com:80/", "https://example.com/a?x=1:2"):
        assert not any(p.search(good) for p in pats), good
    # Private targets stay refused: the page itself, and the address the browser ends on.
    with pytest.raises(service.ProviderError, match="^not read"):
        await _read_one(pid, "https://intranet.example.com/")
    _browserless(monkeypatch, lambda req: httpx.Response(
        200, headers={"x-response-code": "200", "x-response-url": "https://intranet.example.com/"}, text=PAGE))
    with pytest.raises(service.ProviderError, match="moved to an address that is not allowed"):
        await _read_one(pid)
    with pytest.raises(service.ProviderError, match="^not read"):
        await _read_one(pid, "https://example.com:8443/")


async def test_egress_route_is_unchanged(monkeypatch):
    _require(monkeypatch)
    _egress(monkeypatch, EGRESS)
    _lan_ok(monkeypatch)
    pid = await _reader(use_egress=True)
    seen = _browserless(monkeypatch, lambda req: httpx.Response(200, headers={"x-response-code": "200"}, text=PAGE))
    out = await _read_one(pid)
    assert out["route"] == "egress"
    assert json.loads(seen[-1].url.params["launch"]) == {"args": ["--proxy-server=http://egress:8888",
                                                                  "--proxy-bypass-list=<-loopback>", *WEBRTC]}
    # The request filter goes on this route too.
    assert json.loads(seen[-1].content)["rejectRequestPattern"]
    # Required and unusable: still paused, never direct.
    _egress(monkeypatch, "")
    with pytest.raises(service.ProviderError, match="Reading pages with a browser is paused"):
        await _read_one(pid)


# ---------------------------------------------------------------- the server's reader


def _env(monkeypatch, url="http://browser:3000", token="env-token-123", route="direct"):
    s = get_settings()
    monkeypatch.setattr(s, "browser_url", url)
    monkeypatch.setattr(s, "browser_token", token)
    monkeypatch.setattr(s, "browser_route", route)


async def test_server_reader_from_the_environment(admin_client, monkeypatch):
    _env(monkeypatch)
    _egress(monkeypatch, EGRESS)
    _public(monkeypatch)
    ov = (await admin_client.get("/api/v1/admin/websearch")).json()
    (row,) = [r for r in ov["readers"] if r["from_env"]]
    assert row["name"] == "Browser (server)" and row["base_url"] == "http://browser:3000"
    assert row["kind"] == "browserless" and row["network"] == "local" and row["use_egress"] is False
    assert row["has_api_key"] is True and "env-token" not in json.dumps(ov)
    async with sessionmaker()() as db:
        stored = await db.get(WebSearchProvider, row["id"])
        assert stored.api_key_enc is None
    # The chain uses it, with the token from the environment, through a client that may reach the
    # stack's own `browser` name (no check_target), and with the route chosen by MAILR_BROWSER_ROUTE.
    _page_client(monkeypatch, lambda req: httpx.Response(403, text="blocked"))
    seen = _browserless(monkeypatch, lambda req: httpx.Response(200, headers={"x-response-code": "200"}, text=PAGE))
    async with sessionmaker()() as db:
        out = await readers.read(db, "https://app.example.com/")
    assert out["reader"] == "Browser (server)" and out["route"] == "direct"
    assert str(seen[-1].url).startswith("http://browser:3000/content") and seen[-1].url.params["token"] == "env-token-123"
    # An admin changes the route (audited), not the URL, name or key; it cannot be deleted.
    r = await admin_client.patch(f"/api/v1/admin/websearch/providers/{row['id']}", json={
        "name": "Mine", "kind": "browserless", "base_url": "http://elsewhere.lan:3000", "network": "local",
        "use_egress": True, "priority": 1, "enabled": True, "timeout_s": 30, "api_key": "typed-key"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["name"] == "Browser (server)" and got["base_url"] == "http://browser:3000"
    assert got["use_egress"] is True and got["priority"] == 1
    async with sessionmaker()() as db:
        assert (await db.get(WebSearchProvider, row["id"])).api_key_enc is None
        a = (await db.execute(select(AuditLog).where(AuditLog.action == "admin.web_search.update")
                              .order_by(AuditLog.id.desc()))).scalars().first()
        detail = json.dumps([a.inputs, a.result], default=str)
        assert '"route": "direct"' in detail and '"route": "egress"' in detail
    assert (await admin_client.delete(f"/api/v1/admin/websearch/providers/{row['id']}")).status_code == 409
    # The test of the stored reader uses the environment's token.
    t = (await admin_client.post(f"/api/v1/admin/websearch/providers/{row['id']}/test", json={})).json()
    assert t["ok"] is True and t["route"] == "egress" and seen[-1].url.params["token"] == "env-token-123"
    # The environment's URL changes: the row follows. Unset: the reader is not listed or used.
    _env(monkeypatch, url="http://browser2:3000/")
    ov = (await admin_client.get("/api/v1/admin/websearch")).json()
    assert [r["base_url"] for r in ov["readers"] if r["from_env"]] == ["http://browser2:3000"]
    _env(monkeypatch, url="")
    ov = (await admin_client.get("/api/v1/admin/websearch")).json()
    assert not [r for r in ov["readers"] if r["from_env"]]


async def test_direct_route_choice_is_audited(admin_client, monkeypatch):
    _lan_ok(monkeypatch)
    r = await admin_client.post("/api/v1/admin/websearch/providers", json={
        "purpose": "reader", "name": "bl", "kind": "browserless", "base_url": "http://browserless.lan:3000",
        "network": "local", "use_egress": False, "priority": 10, "enabled": True, "timeout_s": 30})
    assert r.status_code == 200, r.text
    assert r.json()["use_egress"] is False
    async with sessionmaker()() as db:
        a = (await db.execute(select(AuditLog).where(AuditLog.action == "admin.web_search.add")
                              .order_by(AuditLog.id.desc()))).scalars().first()
        assert '"route": "direct"' in json.dumps([a.inputs, a.result], default=str)


# ---------------------------------------------------------------- mailr read-page


async def test_cli_read_page(monkeypatch, capsys):
    from mailr import cli

    _egress(monkeypatch, EGRESS)
    _lan_ok(monkeypatch)
    pid = await _reader(key="cli-token")
    async with sessionmaker()() as db:
        name = (await db.get(WebSearchProvider, pid)).name
    _browserless(monkeypatch, lambda req: httpx.Response(
        200, headers={"x-response-code": "200"}, text=PAGE.replace("Self-hosted Photoshop", "A \x1b[31mtitle")))
    assert await cli._read_page("https://app.example.com/", name) == 0
    out = capsys.readouterr().out
    assert f"reader: {name}" in out and "route:  through the egress proxy" in out and "status: 200" in out
    assert "title:  A [31mtitle" in out and "\x1b" not in out and "chars:" in out and "cli-token" not in out
    _browserless(monkeypatch, lambda req: httpx.Response(200, text=""),
                 _fn({"ok": False, "error": "net::ERR_PROXY_CONNECTION_FAILED at https://app.example.com/"}))
    assert await cli._read_page("https://app.example.com/", name) == 1
    assert capsys.readouterr().out.strip() == ("error: the browser could not reach the egress proxy (egress:8888) "
                                               "(net::ERR_PROXY_CONNECTION_FAILED)")
    assert await cli._read_page("https://app.example.com/", "nope") == 1
    assert "no page reader named 'nope'" in capsys.readouterr().out
    # The whole chain, as Ambrose reads: the built-in reader first.
    _page_client(monkeypatch, lambda req: httpx.Response(200, headers={"content-type": "text/html"}, text=PAGE))
    assert await cli._read_page("https://docs.example.org/a", None) == 0
    out = capsys.readouterr().out
    assert "reader: Built-in" in out and "route:  through the egress proxy" in out and "status: 200" in out


async def test_pages_read_reports_route_and_status(monkeypatch):
    _egress(monkeypatch, EGRESS)
    _page_client(monkeypatch, lambda req: httpx.Response(200, headers={"content-type": "text/html"}, text=PAGE))
    async with sessionmaker()() as db:
        out = await pages.read(db, "https://docs.example.org/a")
    assert out["route"] == "egress" and out["status"] == 200
