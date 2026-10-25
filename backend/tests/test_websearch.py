# ACT-SURFACES: assistant web.settings
"""Web search for the assistant (websearch/, agent/web_tools.py).

- Adapters: each kind's request (key header, freshness) and answer mapping (Exa, Brave, Tavily,
  degoog, SearXNG); answers without a result list are errors.
- The service: providers tried by priority with fallback on errors, timeouts, refused keys, rate
  limits and empty answers; per-minute and per-day limits; results de-duplicated and capped.
- Routes: internet providers through the egress proxy when set, never directly under
  MAILR_REQUIRE_EGRESS; local providers directly, only while their host is private and the server
  allows private addresses.
- Pages (read_page): egress rules, https only, HTML to text, size and type limits.
- Tools agent.web_search and agent.read_page: local-only refusal, the access policy feature, the
  permission class web.search (ask, then run after approval), read_page only for result pages or the
  user's own links, citations [[web:<url>]] resolved to title and site.
- Admin (settings.web_search): CRUD, the key is write-only and cleared on a URL change, tests of a
  draft and of a saved provider, admins only.
- Opt-in live test against a real degoog (MAILR_TEST_DEGOOG_URL).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import uuid

import httpcore
import httpx
import pytest
from helpers.websearch import _clean, _ctx, _egress, _mock, _page_client, _require  # noqa: F401
from sqlalchemy import delete, select

from mailr.config import get_settings
from mailr.core import access, netguard
from mailr.db import sessionmaker
from mailr.models import AccessPolicy, AuditLog, PendingAction, User, WebSearchProvider
from mailr.websearch import adapters, pages, service

PW = "test-password-123"


async def _provider(name="P", kind="exa", base_url="https://api.exa.example", key="k-test", **kw) -> int:
    from mailr.core.crypto import encrypt_instance, web_search_provider_context

    async with sessionmaker()() as db:
        p = WebSearchProvider(name=f"{name}-{uuid.uuid4().hex[:6]}", kind=kind, base_url=base_url,
                              network=kw.pop("network", "internet"), **{"priority": 100, "max_results": 10,
                                                                         "timeout_s": 5, **kw})
        db.add(p)
        await db.flush()
        if key:
            p.api_key_enc = encrypt_instance(key, context=web_search_provider_context(p.id))
        await db.commit()
        return p.id


EXA_OK = {"results": [
    {"title": "The Linux Kernel Archives", "url": "https://www.kernel.org/", "publishedDate": "2026-10-03T00:00:00.000Z",
     "highlights": ["stable: 7.2.9 2026-10-03"]},
    {"title": "Kernel releases", "url": "https://kernel.org", "highlights": ["duplicate of the first"]},
    {"title": "No URL"},
]}


# ---------------------------------------------------------------- adapters


def test_exa_request_and_mapping():
    a = adapters.ADAPTERS["exa"]
    req = a.request("linux kernel", 5, "week", "sk-x")
    assert req.method == "POST" and req.path == "/search" and req.headers["x-api-key"] == "sk-x"
    assert req.json["numResults"] == 5 and req.json["query"] == "linux kernel" and "startPublishedDate" in req.json
    rs = a.parse(EXA_OK)
    assert [r.url for r in rs] == ["https://www.kernel.org/", "https://kernel.org"]
    assert rs[0].snippet == "stable: 7.2.9 2026-10-03" and rs[0].published.startswith("2026-10-03")
    assert rs[0].site == "kernel.org"


def test_brave_request_and_mapping():
    a = adapters.ADAPTERS["brave"]
    req = a.request("q", 30, "day", "bk")
    assert req.method == "GET" and req.params == {"q": "q", "count": 20, "freshness": "pd"}
    assert req.headers["X-Subscription-Token"] == "bk"
    rs = a.parse({"web": {"results": [{"title": "<strong>A</strong> &amp; B", "url": "https://a.example/x",
                                       "description": "d", "profile": {"name": "A site"}}]}})
    assert rs[0].title == "A & B" and rs[0].source == "A site" and rs[0].snippet == "d"
    assert a.parse({"query": {}}) == []  # no web results at all


def test_tavily_request_and_mapping():
    a = adapters.ADAPTERS["tavily"]
    req = a.request("q", 3, "month", "tk")
    assert req.headers["Authorization"] == "Bearer tk" and req.json == {"query": "q", "max_results": 3,
                                                                       "time_range": "month"}
    rs = a.parse({"results": [{"title": "T", "url": "https://t.example", "content": "c", "published_date": "2026-09-01"}]})
    assert rs[0].snippet == "c" and rs[0].published == "2026-09-01"


def test_degoog_request_and_mapping():
    a = adapters.ADAPTERS["degoog"]
    req = a.request("q", 5, "day", None)
    # The JSON API is /api/search (/search?format=json returns HTML); degoog has no date filter.
    assert req.path == "/api/search" and req.params == {"q": "q"} and "Authorization" not in req.headers
    assert a.freshness is False and a.default_network == "local" and not a.needs_key
    rs = a.parse({"results": [{"title": "K", "url": "https://www.kernel.org", "snippet": "s", "source": "Bing",
                               "sources": ["Bing", "Google CSE"]}], "totalTime": 1})
    assert rs[0].source == "Bing" and rs[0].snippet == "s"


def test_searxng_request_and_mapping():
    a = adapters.ADAPTERS["searxng"]
    req = a.request("q", 5, "year", None)
    assert req.path == "/search" and req.params == {"q": "q", "format": "json", "time_range": "year"}
    rs = a.parse({"results": [{"title": "S", "url": "https://s.example", "content": "c", "engines": ["google cse", "bing"],
                               "publishedDate": None}]})
    assert rs[0].source == "google cse, bing" and rs[0].published is None


def test_adapter_rejects_answers_without_results():
    with pytest.raises(adapters.AdapterError):
        adapters.ADAPTERS["exa"].parse({"results": "nope"})
    with pytest.raises(adapters.AdapterError):
        adapters.ADAPTERS["brave"].parse(["not", "an", "object"])
    # Links that are not http(s) are dropped.
    assert adapters.ADAPTERS["degoog"].parse({"results": [{"title": "x", "url": "javascript:alert(1)"}]}) == []


# ---------------------------------------------------------------- the service


async def test_fallback_order_and_errors(monkeypatch):
    first = await _provider("first", priority=10, base_url="https://one.example")
    await _provider("second", priority=20, base_url="https://two.example")

    def handler(req):
        if req.url.host == "one.example":
            return httpx.Response(401, json={"error": "bad key"})
        assert req.headers["x-api-key"] == "k-test"
        return httpx.Response(200, json=EXA_OK)

    seen = _mock(monkeypatch, handler)
    async with sessionmaker()() as db:
        out = await service.search(db, "linux   kernel", count=5)
    assert [r.host for r in (x.url for x in seen)] == ["one.example", "two.example"]
    assert out.provider.startswith("second") and out.via == "direct"
    assert [r.url for r in out.results] == ["https://www.kernel.org/"]  # de-duplicated, no-URL dropped
    assert out.tried and "refused the API key" in out.tried[0]["error"]
    assert json.loads(seen[1].content)["query"] == "linux kernel"
    async with sessionmaker()() as db:
        assert "refused the API key" in (await db.get(WebSearchProvider, first)).last_error


async def test_empty_answer_falls_through_and_all_failing_raises(monkeypatch):
    await _provider("a", priority=1, base_url="https://a.example")
    await _provider("b", priority=2, base_url="https://b.example")
    _mock(monkeypatch, lambda req: httpx.Response(200, json={"results": []}) if req.url.host == "a.example"
          else httpx.Response(200, json=EXA_OK))
    async with sessionmaker()() as db:
        out = await service.search(db, "q")
    assert out.provider.startswith("b") and out.tried[0]["error"] == "no results"

    _mock(monkeypatch, lambda req: httpx.Response(200, text="<html>not json</html>"))
    async with sessionmaker()() as db:
        with pytest.raises(service.SearchUnavailable) as ex:
            await service.search(db, "q")
    assert "did not answer with JSON" in str(ex.value) and len(ex.value.tried) == 2


async def test_timeout_and_rate_limit(monkeypatch):
    await _provider("slow", priority=1, base_url="https://slow.example", timeout_s=1)
    await _provider("limited", priority=2, base_url="https://limited.example")

    async def handler(req):
        if req.url.host == "slow.example":
            await asyncio.sleep(3)
        return httpx.Response(429)

    _mock(monkeypatch, handler)
    async with sessionmaker()() as db:
        with pytest.raises(service.SearchUnavailable) as ex:
            await service.search(db, "q")
    msg = str(ex.value)
    assert "did not answer within 1 s" in msg and "rate limiting" in msg


async def test_no_provider_and_bad_input():
    async with sessionmaker()() as db:
        with pytest.raises(service.SearchUnavailable, match="not set up"):
            await service.search(db, "q")
        with pytest.raises(service.SearchUnavailable, match="empty"):
            await service.search(db, "   ")
        with pytest.raises(service.SearchUnavailable, match="freshness"):
            await service.search(db, "q", freshness="decade")


async def test_daily_and_minute_limits(monkeypatch):
    await _provider("daily", priority=1, base_url="https://daily.example", per_day=1)
    await _provider("minute", priority=2, base_url="https://minute.example", per_minute=1)
    await _provider("spare", priority=3, base_url="https://spare.example")
    seen = _mock(monkeypatch, lambda req: httpx.Response(200, json={"results": []}) if req.url.host != "spare.example"
                 else httpx.Response(200, json=EXA_OK))
    async with sessionmaker()() as db:
        await service.search(db, "one")
        out = await service.search(db, "two")
    hosts = [r.url.host for r in seen]
    assert hosts == ["daily.example", "minute.example", "spare.example", "spare.example"]
    errors = " ".join(t["error"] for t in out.tried)
    assert "1 searches a day" in errors and "1 searches a minute" in errors


async def test_missing_key_and_unknown_kind(monkeypatch):
    await _provider("nokey", key=None)
    _mock(monkeypatch, lambda req: httpx.Response(200, json=EXA_OK))
    async with sessionmaker()() as db:
        with pytest.raises(service.SearchUnavailable, match="needs an API key"):
            await service.search(db, "q")


# ---------------------------------------------------------------- routes (egress, LAN)


async def test_internet_provider_uses_egress_when_set(monkeypatch):
    pid = await _provider("exa")
    _egress(monkeypatch)
    async with sessionmaker()() as db:
        p = await db.get(WebSearchProvider, pid)
        assert (await service.route_for(db, p)).via == "egress"
        p.use_egress = False
        assert (await service.route_for(db, p)).via == "direct"
        _require(monkeypatch)
        # Required: the provider's own switch cannot take it off the egress.
        assert (await service.route_for(db, p)).via == "egress"


async def test_required_egress_without_proxy_never_connects(monkeypatch):
    calls: list[str] = []

    def gai(host, *a, **k):
        calls.append(host)
        raise socket.gaierror("off")

    async def tcp(self, host, port, *a, **k):
        calls.append(f"{host}:{port}")
        raise httpcore.ConnectError("off")

    await _provider("exa")
    _require(monkeypatch)
    monkeypatch.setattr(socket, "getaddrinfo", gai)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", tcp)
    async with sessionmaker()() as db:
        with pytest.raises(service.SearchUnavailable) as ex:
            await service.search(db, "q")
    # (The test database is on localhost; only the provider's host matters here.)
    assert "paused" in str(ex.value) and not [c for c in calls if "exa" in c]


async def test_egress_request_goes_through_the_proxy(monkeypatch):
    """A real client: the search request is a CONNECT to the provider through the proxy, and the
    provider's name is never looked up locally."""
    from helpers.egress import _Proxy

    proxy = _Proxy()
    looked: list[str] = []
    real = socket.getaddrinfo

    def gai(host, *a, **k):
        looked.append(host)
        return real(host, *a, **k)

    try:
        await _provider("exa", base_url="https://api.exa.example")
        _egress(monkeypatch, proxy.url, state={"state": "ok", "ok": True, "at": __import__("time").time()})
        monkeypatch.setattr(socket, "getaddrinfo", gai)
        async with sessionmaker()() as db:
            with pytest.raises(service.SearchUnavailable) as ex:
                await service.search(db, "secret words")
        assert "through the egress proxy" in str(ex.value)
        assert proxy.seen == ["CONNECT api.exa.example:443"]
        assert "api.exa.example" not in looked
    finally:
        proxy.close()


async def test_local_provider_rules(monkeypatch):
    from mailr.core import outbound

    pid = await _provider("degoog", kind="degoog", base_url="http://degoog.internal", key=None, network="local")
    _egress(monkeypatch)  # an egress proxy never carries LAN providers
    monkeypatch.setattr(outbound, "host_is_local_now", lambda url: True)

    async def ok(url, public_only=False, private_ok=None):
        return None

    monkeypatch.setattr(netguard, "check_url", ok)
    async with sessionmaker()() as db:
        p = await db.get(WebSearchProvider, pid)
        assert (await service.route_for(db, p)).via == "lan"
        # A name that now resolves to a public address is refused: no direct path to the internet.
        monkeypatch.setattr(outbound, "host_is_local_now", lambda url: False)
        with pytest.raises(service.ProviderError, match="does not resolve to your local network"):
            await service.route_for(db, p)
        monkeypatch.setattr(outbound, "host_is_local_now", lambda url: True)
        monkeypatch.setattr(get_settings(), "allow_private_hosts", "none")
        with pytest.raises(service.ProviderError, match="private network addresses"):
            await service.route_for(db, p)


def test_base_url_checks():
    assert service.check_base("exa", "internet", "https://api.exa.ai/") == "https://api.exa.ai"
    for bad, why in (("http://api.exa.ai", "https"), ("https://degoog.lan", "public host"),
                     ("https://10.0.0.5", "private"), ("https://u:p@api.exa.ai", "API key"),
                     ("https://api.exa.ai?x=1", "query")):
        with pytest.raises(service.ProviderError, match=why):
            service.check_base("exa", "internet", bad)
    assert service.check_base("degoog", "local", "http://degoog.lan") == "http://degoog.lan"


# ---------------------------------------------------------------- pages


def test_extract_html_to_text():
    html = """<html><head><title>T</title><meta property="article:published_time" content="2026-10-01">
    <script>alert('x')</script><style>p{}</style></head><body><nav>Home | About</nav>
    <main><h1>Release notes</h1><p>Version 7.2 is stable.<br>It came out in October.</p>""" + "<p>more text</p>" * 30 + \
        """</main><footer>Cookie banner</footer></body></html>"""
    d = pages.extract(html)
    assert d["title"] == "T" and d["published"] == "2026-10-01"
    assert "Version 7.2 is stable." in d["text"] and "It came out in October." in d["text"]
    assert "alert" not in d["text"] and "Home | About" not in d["text"] and "Cookie banner" not in d["text"]


async def test_read_page_through_egress(monkeypatch):
    _egress(monkeypatch)
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if req.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                              text="<title>New page</title><p>" + "word " * 6000 + "</p>")

    _page_client(monkeypatch, handler)
    async with sessionmaker()() as db:
        out = await pages.read(db, "https://docs.example.org/old", max_chars=1000)
    assert seen == ["https://docs.example.org/old", "https://docs.example.org/new"]
    assert out["final_url"] == "https://docs.example.org/new" and out["via"] == "egress"
    assert out["truncated"] and len(out["text"]) <= 1002 and out["title"] == "New page"


async def test_read_page_refusals(monkeypatch):
    _egress(monkeypatch)
    _page_client(monkeypatch, lambda req: httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF"))
    async with sessionmaker()() as db:
        with pytest.raises(pages.PageError, match="only https"):
            await pages.read(db, "http://example.org/")
        with pytest.raises(pages.PageError, match="not a web page"):
            await pages.read(db, "https://example.org/file.pdf")
        # Through the proxy: no private names or numeric hosts, and redirects are checked again.
        with pytest.raises(pages.PageError, match="not a public host"):
            await pages.read(db, "https://router.lan/")
        _page_client(monkeypatch, lambda req: httpx.Response(302, headers={"location": "https://127.1/admin"}))
        with pytest.raises(pages.PageError, match="numeric address"):
            await pages.read(db, "https://example.org/r")
        _page_client(monkeypatch, lambda req: httpx.Response(302, headers={"location": str(req.url) + "x"}))
        with pytest.raises(pages.PageError, match="redirected more than"):
            await pages.read(db, "https://example.org/loop")
    _require(monkeypatch)
    _egress(monkeypatch, "")
    async with sessionmaker()() as db:
        with pytest.raises(pages.PageError, match="Reading web pages is paused"):
            await pages.read(db, "https://example.org/")


# ---------------------------------------------------------------- tools


async def test_web_search_tool_results_and_citations(user, monkeypatch):
    from mailr.agent.present import result_label, step_label
    from mailr.agent.tools import UNTRUSTED_TOOLS, run_tool
    from mailr.intel.citations import Resolver

    await _provider("exa")
    _mock(monkeypatch, lambda req: httpx.Response(200, json=EXA_OK))
    ctx = await _ctx(user.id)
    try:
        res = await run_tool(ctx, "web_search", {"query": "linux kernel stable", "count": 3})
        item = res.data["items"][0]
        assert item["cite"] == "[[web:https://www.kernel.org/]]" and item["title"]["untrusted"] is True
        assert "web_search" in UNTRUSTED_TOOLS and "read_page" in UNTRUSTED_TOOLS
        assert result_label("web_search", res)[0].startswith("1 result from exa-")
        # The step timeline shows the exact query.
        assert await step_label(ctx.db, user.id, "web_search", {"query": "linux  kernel stable"}) == \
            "Searching the web for “linux kernel stable”"
        r = Resolver(ctx.db, ctx.p, known=json.dumps(res.data), web=ctx.web_sources)
        text = await r.resolve("Stable is 7.2.9 [[web:https://www.kernel.org/]]. Made up [[web:https://evil.example/x]].")
        assert text == "Stable is 7.2.9 [1](mailr-cite:1). Made up."
        c = r.cites[0]
        assert c["kind"] == "web" and c["site"] == "kernel.org" and c["title"] == "The Linux Kernel Archives"
        assert c["url"] == "https://www.kernel.org/" and c["date"].startswith("2026-10-03")
        audit = (await ctx.db.execute(select(AuditLog).where(AuditLog.user_id == user.id,
                                                             AuditLog.action == "agent.web_search"))).scalars().all()
        assert audit and audit[-1].target == "linux kernel stable"
    finally:
        await ctx.db.close()


async def test_local_only_turn_refuses(user, monkeypatch):
    from mailr.agent.tools import ToolError, run_tool
    from mailr.agent.web_tools import LOCAL_ONLY_REFUSAL

    await _provider("exa")
    seen = _mock(monkeypatch, lambda req: httpx.Response(200, json=EXA_OK))
    ctx = await _ctx(user.id, sensitive=True)
    try:
        with pytest.raises(ToolError) as ex:
            await run_tool(ctx, "web_search", {"query": "student grade appeal policy"})
        assert str(ex.value) == LOCAL_ONLY_REFUSAL
        ctx.web_sources["https://www.kernel.org/"] = {}
        with pytest.raises(ToolError, match="local-only"):
            await run_tool(ctx, "read_page", {"url": "https://www.kernel.org/"})
        assert seen == []
    finally:
        await ctx.db.close()


async def test_local_only_token_refuses(user, monkeypatch):
    from mailr.agent.tools import ToolError, run_tool
    from mailr.models import ApiToken

    await _provider("exa")
    _mock(monkeypatch, lambda req: httpx.Response(200, json=EXA_OK))
    ctx = await _ctx(user.id, assistant=False, session=None,
                     token=ApiToken(id=0, user_id=user.id, name="local agent", local_only=True, scopes=["read"]))
    try:
        with pytest.raises(ToolError, match="local-only tokens"):
            await run_tool(ctx, "web_search", {"query": "weather"})
    finally:
        await ctx.db.close()


async def test_access_policy_gates_web_search(app, monkeypatch):
    from mailr.agent import web_tools
    from mailr.agent.tools import ToolError, run_tool
    from mailr.core.users import create_user

    await _provider("exa")
    _mock(monkeypatch, lambda req: httpx.Response(200, json=EXA_OK))
    tag = uuid.uuid4().hex[:6]
    async with sessionmaker()() as db:
        feats = {k: k == "assistant" for k in access.FEATURES}
        db.add(AccessPolicy(group_name=f"no-web-{tag}", models={}, features=feats, needs={}))
        db.add(AccessPolicy(group_name=f"web-{tag}", models={}, features={**feats, "web_search": True}, needs={}))
        plain = await create_user(db, f"ws-{tag}@example.com", "Plain", password=PW)
        plain.assigned_groups = [f"no-web-{tag}"]
        boss = await create_user(db, f"wsa-{tag}@example.com", "Admin", password=PW, role="admin")
        await db.commit()
        plain_id, boss_id = plain.id, boss.id
    assert access.FEATURES["web_search"]["default"] is False
    ctx = await _ctx(plain_id)
    try:
        assert await web_tools.hidden_tools(ctx.db, ctx.p) == {"web_search", "read_page"}
        with pytest.raises(ToolError, match="hasn't turned on web search"):
            await run_tool(ctx, "web_search", {"query": "q"})
        u = await ctx.db.get(User, plain_id)
        u.assigned_groups = [f"no-web-{tag}", f"web-{tag}"]
        await ctx.db.commit()
        assert await web_tools.hidden_tools(ctx.db, ctx.p) == set()
        assert (await run_tool(ctx, "web_search", {"query": "q"})).data["items"]
    finally:
        await ctx.db.close()
    ctx = await _ctx(boss_id)
    try:  # admins are never restricted
        assert await web_tools.hidden_tools(ctx.db, ctx.p) == set()
        async with sessionmaker()() as db:
            await db.execute(delete(WebSearchProvider))
            await db.commit()
        assert await web_tools.hidden_tools(ctx.db, ctx.p) == {"web_search", "read_page"}
    finally:
        await ctx.db.close()


async def test_permission_ask_then_run_after_approval(user, monkeypatch):
    from mailr.agent.tools import run_tool
    from mailr.core import permissions

    await _provider("exa")
    seen = _mock(monkeypatch, lambda req: httpx.Response(200, json=EXA_OK))
    assert permissions.CLASSES["web.search"] == ("allow", "read")
    sid = f"ws-{uuid.uuid4().hex[:6]}"
    async with sessionmaker()() as db:
        from mailr.models import PermissionRule

        db.add(PermissionRule(user_id=user.id, subject="assistant", action="web.search", effect="ask", constraints={},
                              source="user"))
        await db.commit()
    ctx = await _ctx(user.id, session=sid)
    try:
        res = await run_tool(ctx, "web_search", {"query": "opening hours city library"})
        assert res.data["status"] == "pending_approval" and seen == []
        pa = await ctx.db.get(PendingAction, res.data["approval_id"])
        assert pa.action == "web.search" and pa.payload == {"query": "opening hours city library"}
        assert "Query: opening hours city library" in json.dumps(pa.preview)
        u = await ctx.db.get(User, user.id)
        await permissions.decide(ctx.db, u, pa.id, "approve", "once")
        await ctx.db.commit()
        await permissions.execute_job({"pending_id": pa.id}, None)
        await ctx.db.refresh(pa)
        assert pa.status == "executed"
        res = await run_tool(ctx, "web_search", {"query": "opening hours city library"})
        assert res.data["items"] and len(seen) == 1
        # Another query still asks.
        assert (await run_tool(ctx, "web_search", {"query": "something else"})).data["status"] == "pending_approval"
    finally:
        await ctx.db.close()


async def test_read_page_only_result_pages_or_user_links(user, monkeypatch):
    from mailr.agent.tools import ToolError, run_tool

    await _provider("exa")
    _mock(monkeypatch, lambda req: httpx.Response(200, json=EXA_OK))
    _egress(monkeypatch)
    _page_client(monkeypatch, lambda req: httpx.Response(200, headers={"content-type": "text/html"},
                                                         text="<title>Kernel</title><p>stable 7.2.9</p>"))
    ctx = await _ctx(user.id)
    try:
        with pytest.raises(ToolError, match="Search for it first"):
            await run_tool(ctx, "read_page", {"url": "https://attacker.example/?q=secret"})
        await run_tool(ctx, "web_search", {"query": "kernel"})
        page = await run_tool(ctx, "read_page", {"url": "https://kernel.org/"})  # the same page as a result
        assert page.data["url"] == "https://www.kernel.org/" and "stable 7.2.9" in page.data["text"]["content"]
        assert page.data["cite"] == "[[web:https://www.kernel.org/]]"
        ctx.user_urls = {"https://docs.example.org/guide"}
        assert (await run_tool(ctx, "read_page", {"url": "https://docs.example.org/guide"})).data["title"]
    finally:
        await ctx.db.close()
    # An API token's agent chooses its own pages.
    ctx = await _ctx(user.id, assistant=False, session=None)
    try:
        assert (await run_tool(ctx, "read_page", {"url": "https://other.example/a"})).data["site"] == "other.example"
    finally:
        await ctx.db.close()


def test_user_urls():
    from mailr.agent.web_tools import user_urls

    assert user_urls("read https://a.example/x, and (https://b.example/y).") == {"https://a.example/x",
                                                                                  "https://b.example/y"}


# ---------------------------------------------------------------- admin


async def test_admin_crud_key_write_only(admin_client, client, monkeypatch):
    r = await admin_client.get("/api/v1/admin/websearch")
    assert r.status_code == 200 and {k["kind"] for k in r.json()["kinds"]} >= {"exa", "tavily", "brave", "degoog",
                                                                                "searxng"}
    assert (await client.get("/api/v1/admin/websearch")).status_code == 403  # not an admin
    r = await admin_client.post("/api/v1/admin/websearch/providers",
                                json={"name": "Exa", "kind": "exa", "api_key": "sk-secret-1", "priority": 10})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["base_url"] == "https://api.exa.ai" and p["has_api_key"] and "sk-secret" not in r.text
    assert "sk-secret" not in (await admin_client.get("/api/v1/admin/websearch")).text
    async with sessionmaker()() as db:
        assert service.key_of(await db.get(WebSearchProvider, p["id"])) == "sk-secret-1"
    body = {"name": "Exa", "kind": "exa", "base_url": "https://api.exa.example", "priority": 10}
    r = await admin_client.patch(f"/api/v1/admin/websearch/providers/{p['id']}", json=body)
    assert r.status_code == 200 and r.json()["has_api_key"] is False  # a new URL never gets the old key
    r = await admin_client.post("/api/v1/admin/websearch/providers",
                                json={"name": "Bad", "kind": "exa", "base_url": "http://api.exa.ai"})
    assert r.status_code == 400 and "https" in r.json()["detail"]
    r = await admin_client.post("/api/v1/admin/websearch/providers", json={"name": "X", "kind": "bing"})
    assert r.status_code == 400
    assert (await admin_client.delete(f"/api/v1/admin/websearch/providers/{p['id']}")).json() == {"ok": True}
    async with sessionmaker()() as db:
        acts = [a.action for a in (await db.execute(select(AuditLog).where(
            AuditLog.action.like("admin.web_search.%")))).scalars()]
    assert {"admin.web_search.add", "admin.web_search.update", "admin.web_search.delete"} <= set(acts)


async def test_admin_test_draft_and_saved(admin_client, monkeypatch):
    seen = _mock(monkeypatch, lambda req: httpx.Response(200, json=EXA_OK))
    r = await admin_client.post("/api/v1/admin/websearch/test", json={"kind": "exa", "api_key": "sk-draft"})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["via"] == "direct", r.text
    assert seen[-1].headers["x-api-key"] == "sk-draft"
    pid = (await admin_client.post("/api/v1/admin/websearch/providers",
                                   json={"name": "E", "kind": "exa", "api_key": "sk-saved"})).json()["id"]
    r = await admin_client.post("/api/v1/admin/websearch/test", json={"id": pid, "kind": "exa"})
    assert r.json()["ok"] and seen[-1].headers["x-api-key"] == "sk-saved"
    r = await admin_client.post("/api/v1/admin/websearch/test",
                                json={"id": pid, "kind": "exa", "base_url": "https://elsewhere.example"})
    assert r.status_code == 400 and "API key again" in r.json()["detail"]
    _mock(monkeypatch, lambda req: httpx.Response(403))
    r = await admin_client.post(f"/api/v1/admin/websearch/providers/{pid}/test", json={})
    assert r.json() == {"ok": False, "error": "E refused the API key (HTTP 403)", "ms": r.json()["ms"]}
    async with sessionmaker()() as db:
        row = await db.get(WebSearchProvider, pid)
        assert row.usage_count == 0 and "refused" in row.last_error  # tests do not count against limits


# ---------------------------------------------------------------- live (opt-in)


@pytest.mark.skipif(not os.environ.get("MAILR_TEST_DEGOOG_URL"), reason="set MAILR_TEST_DEGOOG_URL to test a real degoog")
async def test_live_degoog(monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_private_hosts", "admins")
    base = os.environ["MAILR_TEST_DEGOOG_URL"].rstrip("/")
    await _provider("degoog-live", kind="degoog", base_url=base, key=None, network="local", timeout_s=30)
    async with sessionmaker()() as db:
        out = await service.search(db, "linux kernel", count=5)
    assert out.via == "lan" and 1 <= len(out.results) <= 5, out.tried
    assert all(r.url.startswith(("https://", "http://")) and r.title for r in out.results)


@pytest.mark.skipif(not os.environ.get("MAILR_TEST_SEARXNG_URL"), reason="set MAILR_TEST_SEARXNG_URL to test a real SearXNG")
async def test_live_searxng(monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_private_hosts", "admins")
    base = os.environ["MAILR_TEST_SEARXNG_URL"].rstrip("/")
    await _provider("searxng-live", kind="searxng", base_url=base, key=None, network="local", timeout_s=30)
    async with sessionmaker()() as db:
        out = await service.search(db, "linux kernel", count=5, freshness="year")
    assert out.via == "lan" and 1 <= len(out.results) <= 5, out.tried


async def test_chat_turn_shows_query_and_cites_web(client, user, monkeypatch):
    """The assistant panel's whole turn: the tools and the web note are offered, the step shows the
    exact query, and the answer's [[web:<url>]] becomes a web citation with title and site."""
    import helpers.agent as test_agent
    from helpers.agent import _drop_endpoint, _endpoint, _events, _text_turn, _tool_turn

    from mailr.intel import llm

    monkeypatch.setattr(llm, "_http", lambda r, task, **kw: test_agent.FakeStream(**kw))
    test_agent.FakeStream.requests, test_agent.FakeStream.script = [], []
    await _provider("exa")
    _mock(monkeypatch, lambda req: httpx.Response(200, json=EXA_OK))
    ep = await _endpoint(user.id, "local")
    try:
        test_agent.FakeStream.script = [
            _tool_turn("web_search", {"query": "latest stable linux kernel"}),
            _text_turn("The latest stable kernel is 7.2.9 [[web:https://www.kernel.org/]]."),
        ]
        r = await client.post("/api/v1/agent/chat", json={"messages": [{"role": "user", "content": "Latest kernel?"}]})
        evs = _events(r.text)
        first = test_agent.FakeStream.requests[0]["body"]
        assert {"web_search", "read_page"} <= {t["function"]["name"] for t in first["tools"]}
        assert "[[web:<url>]]" in first["messages"][0]["content"]
        start = next(e for e in evs if e["type"] == "tool_start")
        assert start["label"] == "Searching the web for “latest stable linux kernel”"
        assert start["args"] == {"query": "latest stable linux kernel"} and start["icon"] == "search"
        final = next(e for e in evs if e["type"] == "final")
        assert final["content"] == "The latest stable kernel is 7.2.9 [1](mailr-cite:1)."
        assert final["citations"][0]["kind"] == "web" and final["citations"][0]["site"] == "kernel.org"
    finally:
        await _drop_endpoint(ep)


async def test_cli_stores_key_from_stdin_without_printing(capsys, monkeypatch):
    from mailr import cli

    await cli._web_search_key("Exa CLI", "exa", None, None, 10, 300, "sk-from-stdin")
    out = capsys.readouterr().out
    assert "sk-from-stdin" not in out and "created web search provider 'Exa CLI'" in out and "13 characters" in out
    async with sessionmaker()() as db:
        row = (await db.execute(select(WebSearchProvider).where(WebSearchProvider.name == "Exa CLI"))).scalar_one()
        assert service.key_of(row) == "sk-from-stdin" and row.base_url == "https://api.exa.ai"
        assert (row.priority, row.per_day, row.network, row.use_egress) == (10, 300, "internet", True)
    await cli._web_search_key("Exa CLI", None, None, None, None, None, "sk-rotated")
    assert "updated" in capsys.readouterr().out
    async with sessionmaker()() as db:
        row = (await db.execute(select(WebSearchProvider).where(WebSearchProvider.name == "Exa CLI"))).scalar_one()
        assert service.key_of(row) == "sk-rotated" and row.per_day == 300
    with pytest.raises(SystemExit, match="pass --kind"):
        await cli._web_search_key("Nobody", None, None, None, None, None, "k")


async def test_cli_keyless_kind_needs_no_key_and_keyed_kind_refuses_none(capsys):
    from mailr import cli

    await cli._web_search_key("Local degoog", "degoog", "http://192.168.1.10", "local", 20, None, "")
    assert "no API key" in capsys.readouterr().out
    async with sessionmaker()() as db:
        row = (await db.execute(select(WebSearchProvider).where(WebSearchProvider.name == "Local degoog"))).scalar_one()
        assert (row.kind, row.network, row.priority, row.api_key_enc) == ("degoog", "local", 20, None)
    with pytest.raises(SystemExit, match="needs an API key"):
        await cli._web_search_key("Exa no key", "exa", None, None, None, None, "")
    async with sessionmaker()() as db:
        assert (await db.execute(select(WebSearchProvider).where(WebSearchProvider.name == "Exa no key"))).first() is None
