"""The search providers an admin registered, and one search across them.

Order and fallback. Enabled providers are tried by `priority` (lowest first, then id). A provider
that fails (timeout, refused key, rate limit, an answer that is not JSON, its own limits reached)
is skipped and the next one is tried. A provider that answers with no results counts as an answer;
the next provider is tried, and when none has results the search returns an empty list.

Routes. A query can quote what the assistant read, so it travels
like content from mail:
- `internet` providers (an API service such as Exa) use https only. They go through the egress
  proxy when one is set and `use_egress` is on (the default). With MAILR_REQUIRE_EGRESS they never
  go out directly: no usable egress proxy means the provider is skipped. Without an egress proxy
  they connect directly, to public addresses only (netguard, pinned).
- `local` providers (degoog, SearXNG on the admin's own network) are reached directly, never
  through the egress (the VPN container has no route to the LAN). This needs the server to allow
  private addresses for what admins set up (MAILR_ALLOW_PRIVATE_HOSTS is not `none`), and every
  address of the host must be private when the search runs: a name that now resolves to a public
  address is refused, so a local provider can never become a direct path to the internet.

Limits. `per_minute` (rate_events, shared by all workers) and `per_day` (a counter on the row,
reset at midnight UTC); 0 means no limit. `timeout_s` bounds each request; `max_results` caps
what one provider returns. Responses are read with a 2 MB cap.

The API key is sealed for its row (core/crypto web_search_provider_context) and is only ever sent
to the URL it was saved with (routes.py clears it when the URL changes).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import egress, netguard, outbound
from mailr.models import WebSearchProvider
from mailr.websearch.adapters import ADAPTERS, AdapterError, Result

log = logging.getLogger("mailr.websearch")

MAX_BODY = 2 * 1024 * 1024
MAX_COUNT = 20
DEFAULT_COUNT = 8
USER_AGENT = "mailr-websearch/1"
# Tests replace the network with an httpx transport (the route is still decided and reported).
_TRANSPORT: httpx.AsyncBaseTransport | None = None


class SearchUnavailable(Exception):
    """No provider could answer. The message is safe to show to users and models."""

    def __init__(self, message: str, tried: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.tried = tried or []


class ProviderError(Exception):
    """One provider failed. The message is safe to show (it never holds the key)."""


@dataclass
class Route:
    via: str  # egress | direct | lan
    egress: str = ""
    # The server's own reader (MAILR_BROWSER_URL): an address the operator set, on the stack's own
    # network, which the checks for addresses admins enter refuse (MAILR_INTERNAL_HOSTS).
    trusted: bool = False

    def describe(self) -> str:
        return {"egress": "through the egress proxy", "direct": "directly", "lan": "on the local network"}[self.via]


@dataclass
class Outcome:
    query: str
    results: list[Result]
    provider: str | None
    via: str | None
    tried: list[dict[str, str]] = field(default_factory=list)


def key_of(p: WebSearchProvider) -> str | None:
    from mailr.core.crypto import decrypt_instance, web_search_provider_context

    if p.from_env:  # the server's reader: its token lives only in the environment
        from mailr.config import get_settings

        return (get_settings().browser_token or "").strip() or None
    return decrypt_instance(p.api_key_enc, context=web_search_provider_context(p.id)) if p.api_key_enc else None


ENV_READER = "Browser (server)"


def env_reader_url() -> str:
    """MAILR_BROWSER_URL without a trailing slash ("" when the server sets up no reader)."""
    from mailr.config import get_settings

    return (get_settings().browser_url or "").strip().rstrip("/")


async def sync_env_reader() -> None:
    """The row of the page reader the server's environment sets up (MAILR_BROWSER_URL): made the
    first time, with MAILR_BROWSER_ROUTE as its route, and its URL kept equal to the environment's.
    An admin's later choices (route, order, limits, on or off) are kept. Its token is never stored."""
    from sqlalchemy.exc import IntegrityError

    from mailr.config import get_settings
    from mailr.db import session_scope

    url = env_reader_url()
    if not url:
        return
    try:
        async with session_scope() as db:
            row = (await db.execute(select(WebSearchProvider).where(WebSearchProvider.from_env.is_(True))
                                    .order_by(WebSearchProvider.id).limit(1))).scalars().first()
            if row is None:
                names = set((await db.execute(select(WebSearchProvider.name))).scalars())
                name, n = ENV_READER, 2
                while name in names:
                    name, n = f"{ENV_READER} {n}", n + 1
                direct = (get_settings().browser_route or "").strip().lower() == "direct"
                db.add(WebSearchProvider(name=name, purpose="reader", kind="browserless", base_url=url,
                                         network="local", use_egress=not direct, from_env=True, priority=5,
                                         enabled=True, max_results=10, timeout_s=30))
            elif row.base_url != url:
                row.base_url = url
    except IntegrityError:
        pass  # another process made it at the same moment


async def providers(db: AsyncSession, *, enabled_only: bool = True, purpose: str = "search") -> list[WebSearchProvider]:
    """The search providers (or, with purpose="reader", the page readers) in the order they are tried.
    The server's own reader is listed only while MAILR_BROWSER_URL is set."""
    q = (select(WebSearchProvider).where(WebSearchProvider.purpose == purpose)
         .order_by(WebSearchProvider.priority, WebSearchProvider.id))
    if purpose == "reader":
        if env_reader_url():
            await sync_env_reader()
        else:
            q = q.where(WebSearchProvider.from_env.is_(False))
    if enabled_only:
        q = q.where(WebSearchProvider.enabled.is_(True))
    return list((await db.execute(q)).scalars())


async def available(db: AsyncSession) -> bool:
    """Is any provider enabled? (The tools are offered only then.)"""
    return bool((await db.execute(select(WebSearchProvider.id).where(
        WebSearchProvider.enabled.is_(True), WebSearchProvider.purpose == "search").limit(1))).first())


# ---------------------------------------------------------------- routes


def check_base(kind: str, network: str, base_url: str, kinds: dict[str, Any] | None = None) -> str:
    """The URL checks that need no lookup (admin routes call `check_target` too). Returns the URL
    without a trailing slash. `kinds`: the registry the kind belongs to (ADAPTERS by default,
    readers.READERS for page readers)."""
    base = (base_url or "").strip().rstrip("/")
    if kind not in (ADAPTERS if kinds is None else kinds):
        raise ProviderError(f"unknown kind {kind!r}")
    u = urlsplit(base)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ProviderError("the base URL must start with https:// (or http:// on your own network)")
    if u.username or u.password:
        raise ProviderError("put the API key in its own field, not in the URL")
    if u.query or u.fragment:
        raise ProviderError("the base URL takes no query or fragment")
    if network == "internet":
        if u.scheme != "https":
            raise ProviderError("a service on the internet must use https://")
        try:
            egress.check_url_offline(base + "/")
        except netguard.UnsafeTarget as e:
            raise ProviderError(f"{e} (choose “On my network” for a service on your own network)") from None
    elif network != "local":
        raise ProviderError("network must be internet or local")
    return base


async def check_target(network: str, base: str) -> None:
    """The checks that resolve the host (save and test time)."""
    host = urlsplit(base).hostname or ""
    if network == "local":
        if netguard.private_mode() == "none":
            raise ProviderError("this server does not connect to private network addresses "
                                "(MAILR_ALLOW_PRIVATE_HOSTS=none)")
        if not await asyncio.to_thread(outbound.host_is_local_now, base):
            raise ProviderError(f"{host} does not resolve to your local network; choose “On the internet” "
                                "for a public service")
        try:
            await netguard.check_url(base, private_ok=True)
        except netguard.UnsafeTarget as e:
            raise ProviderError(str(e)) from None


async def route_for(db: AsyncSession, p: WebSearchProvider) -> Route:
    """How this provider is reached now (ProviderError when it must not be reached at all)."""
    if p.from_env:
        if not env_reader_url():
            raise ProviderError(f"{p.name} is set up by MAILR_BROWSER_URL, which is not set")
        return Route("lan", trusted=True)
    if p.network == "local":
        await check_target("local", p.base_url)
        return Route("lan")
    if p.use_egress or egress.required():
        try:
            eg = await egress.gate(await egress.configured(db))
        except egress.EgressUnavailable as e:
            what = "Reading pages through this reader" if p.purpose == "reader" else "Web search through this provider"
            raise ProviderError(str(e).replace("Remote content is paused", f"{what} is paused")) from None
        if eg:
            return Route("egress", eg)
    return Route("direct")


def service_of(p: WebSearchProvider) -> outbound.Service:
    """The provider as an outbound Service: `local` for a provider on the admin's own network
    (checked again at connect time), `hosted` for one on the internet."""
    return outbound.Service(kind="reader" if p.purpose == "reader" else "websearch", name=p.name, base_url=p.base_url,
                            locality=outbound.LOCAL if p.network == "local" else outbound.HOSTED, source="websearch",
                            trusted=bool(p.from_env))


def _client(route: Route, timeout: float, p: WebSearchProvider) -> httpx.AsyncClient:
    """The client for one request to the provider (core/outbound.py), on the route route_for chose."""
    kw: dict[str, Any] = {"timeout": timeout, "follow_redirects": False,
                          "headers": {"User-Agent": USER_AGENT}}
    svc = service_of(p)
    if _TRANSPORT is not None:
        return outbound.client(svc, transport=_TRANSPORT, **kw)
    if route.via == "egress":
        return outbound.proxied_client(svc, route.egress, **kw)
    if route.trusted or route.via == "lan":
        return outbound.client(svc, private_ok=True, **kw)
    return outbound.client(svc, public_only=True, **kw)


# ---------------------------------------------------------------- limits


def _minute_limit(p: WebSearchProvider):
    from mailr.core import throttle

    return throttle.Limit("websearch-min", int(p.per_minute), timedelta(minutes=1))


# Shares of a provider's daily allowance: one person (their chats and
# tokens together) uses at most half of it, one API token at most a quarter, so nobody spends the
# whole day's allowance for everyone else. Counted in web_search_providers.usage_users, reset with
# usage_day. Calls with no person behind them (an admin's test) count against the total only.
USER_SHARE = 0.5
TOKEN_SHARE = 0.25


def shares(p: WebSearchProvider, who: Any) -> list[tuple[str, int]]:
    """[(key, most a day)] for the caller `who` (a Principal, or None)."""
    if who is None or not p.per_day or p.per_day <= 0:
        return []
    out = [(f"u{who.user_id}", max(1, math.ceil(p.per_day * USER_SHARE)))]
    if getattr(who, "token", None) is not None:
        out.append((f"t{who.token.id}", max(1, math.ceil(p.per_day * TOKEN_SHARE))))
    return out


async def _take(p: WebSearchProvider) -> None:
    """Count one request against the provider's limits and the caller's share of its daily
    allowance, or ProviderError when one is reached."""
    what = "page reads" if p.purpose == "reader" else "searches"
    from mailr.core import throttle
    from mailr.core.visibility import viewer_var
    from mailr.db import session_scope

    if p.per_minute and p.per_minute > 0:
        async with session_scope() as db:
            if await throttle.exceeded(db, [(_minute_limit(p), (p.id,))]):
                raise ProviderError(f"{p.name} reached its limit of {p.per_minute} {what} a minute")
            await throttle.record(db, [(_minute_limit(p), (p.id,))])
    day = datetime.now(UTC).date().isoformat()
    mine = shares(p, viewer_var.get())
    params: dict[str, Any] = {"d": day, "id": p.id}
    bump, room = [], []
    for i, (key, most) in enumerate(mine):
        params[f"k{i}"], params[f"m{i}"] = key, most
        bump.append(f":k{i}, COALESCE((CASE WHEN usage_day = :d THEN usage_users ->> :k{i} END)::int, 0) + 1")
        room.append(f"COALESCE((usage_users ->> :k{i})::int, 0) < :m{i}")
    users = ("(CASE WHEN usage_day = :d THEN usage_users ELSE '{}'::jsonb END)"
             + (f" || jsonb_build_object({', '.join(bump)})" if bump else ""))
    async with session_scope() as db:
        got = (await db.execute(text(
            "UPDATE web_search_providers SET usage_count = CASE WHEN usage_day = :d THEN usage_count + 1 ELSE 1 END,"
            f" usage_users = {users}, usage_day = :d WHERE id = :id AND (per_day <= 0 OR usage_day <> :d OR"
            f" (usage_count < per_day{''.join(' AND ' + r for r in room)})) RETURNING id"), params)).first()
        if got is None and mine:
            row = (await db.execute(text("SELECT usage_count FROM web_search_providers WHERE id = :id"),
                                    {"id": p.id})).first()
            if row is not None and row[0] < p.per_day:
                raise ProviderError(f"you reached your share of {p.name}'s {what} for today; it starts again "
                                    "tomorrow (UTC)")
    if got is None:
        raise ProviderError(f"{p.name} reached its limit of {p.per_day} {what} a day")


async def _note(pid: int, error: str | None) -> None:
    from mailr.db import session_scope

    try:
        async with session_scope() as db:
            row = await db.get(WebSearchProvider, pid)
            if row is None:
                return
            if error is None:
                row.last_ok_at, row.last_error = datetime.now(UTC), ""
            else:
                row.last_error = error[:500]
    except Exception:
        log.debug("could not record the status of web search provider %s", pid, exc_info=True)


# ---------------------------------------------------------------- one provider


def _status_error(p: WebSearchProvider, status: int) -> str:
    if status in (401, 403):
        return f"{p.name} refused the API key (HTTP {status})"
    if status == 429:
        return f"{p.name} is rate limiting Mailr (HTTP 429)"
    if 300 <= status < 400:
        return f"{p.name} answered with a redirect; check the base URL"
    return f"{p.name} answered HTTP {status}"


async def ask(db: AsyncSession, p: WebSearchProvider, query: str, count: int, freshness: str | None,
              *, key: str | None = None, count_usage: bool = True) -> tuple[list[Result], Route]:
    """One provider, no fallback. `key` overrides the stored key (the admin's test of a draft)."""
    adapter = ADAPTERS.get(p.kind)
    if adapter is None:
        raise ProviderError(f"{p.name} has an unknown kind {p.kind!r}")
    if key is None:
        key = key_of(p)
    if adapter.needs_key and not key:
        raise ProviderError(f"{p.name} needs an API key")
    route = await route_for(db, p)
    if count_usage:
        await _take(p)
    n = max(1, min(int(count), int(p.max_results or MAX_COUNT), MAX_COUNT))
    req = adapter.request(query, n, freshness if adapter.freshness else None, key)
    url = p.base_url.rstrip("/") + req.path
    if route.via == "egress":
        try:
            egress.check_url_offline(url)
        except netguard.UnsafeTarget as e:
            raise ProviderError(str(e)) from None
    timeout = float(max(1, min(int(p.timeout_s or 10), 60)))
    try:
        async with _client(route, timeout, p) as c:
            r, body = await netguard.fetch_capped(c, req.method, url, limit=MAX_BODY, deadline=timeout,
                                                  params=req.params or None, json=req.json,
                                                  headers={"Accept": "application/json", **req.headers})
    except TimeoutError:
        raise ProviderError(f"{p.name} did not answer within {int(timeout)} s") from None
    except netguard.TooLarge:
        raise ProviderError(f"{p.name} sent more than 2 MB") from None
    except outbound.Refused as e:
        raise ProviderError(str(e)) from None
    except (httpx.HTTPError, OSError, netguard.UnsafeTarget) as e:
        raise ProviderError(f"{p.name} could not be reached {route.describe()}: {type(e).__name__}"
                            + (f" ({e})" if isinstance(e, netguard.UnsafeTarget | httpx.ConnectError) else "")) from None
    if r.status_code != 200:
        raise ProviderError(_status_error(p, r.status_code))
    try:
        data = json.loads(body)
    except ValueError:
        hint = " (SearXNG: turn on the json format in its settings.yml)" if p.kind == "searxng" else ""
        raise ProviderError(f"{p.name} did not answer with JSON{hint}") from None
    try:
        results = adapter.parse(data)
    except AdapterError as e:
        raise ProviderError(str(e)) from None
    for x in results:
        x.provider = p.name
    return _dedupe(results)[:n], route


def _norm(url: str) -> str:
    u = urlsplit(url)
    host = (u.hostname or "").lower().removeprefix("www.")
    return f"{host}{u.path.rstrip('/')}?{u.query}"


def _dedupe(results: list[Result]) -> list[Result]:
    seen: set[str] = set()
    out = []
    for x in results:
        k = _norm(x.url)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


# ---------------------------------------------------------------- the search


async def search(db: AsyncSession, query: str, *, count: int | None = None,
                 freshness: str | None = None) -> Outcome:
    """Search with the first provider that answers (SearchUnavailable when none can)."""
    query = " ".join((query or "").split())[:400]
    if not query:
        raise SearchUnavailable("the query is empty")
    if freshness not in (None, "", "day", "week", "month", "year"):
        raise SearchUnavailable("freshness must be day, week, month or year")
    rows = await providers(db)
    if not rows:
        raise SearchUnavailable("web search is not set up on this server (Settings → Admin → Web search)")
    n = max(1, min(int(count or DEFAULT_COUNT), MAX_COUNT))
    tried: list[dict[str, str]] = []
    empty: Outcome | None = None
    for p in rows:
        t0 = time.monotonic()
        try:
            results, route = await ask(db, p, query, n, freshness or None)
        except ProviderError as e:
            tried.append({"provider": p.name, "error": str(e)})
            await _note(p.id, str(e))
            continue
        await _note(p.id, None)
        log.info("web search via %s (%s): %d results in %d ms", p.name, route.via, len(results),
                 int((time.monotonic() - t0) * 1000))
        if results:
            return Outcome(query, results, p.name, route.via, tried)
        tried.append({"provider": p.name, "error": "no results"})
        empty = empty or Outcome(query, [], p.name, route.via, [])
    if empty is not None:
        empty.tried = tried
        return empty
    raise SearchUnavailable("no web search provider could answer: "
                            + "; ".join(t["error"] for t in tried), tried)
