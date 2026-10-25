"""One small adapter per kind of search service.

An adapter turns a query into one HTTP request and the service's JSON answer into `Result`s. It
knows nothing about routing, limits, keys at rest or fallbacks (service.py does those). Adding a
kind is one class here and one line in ADAPTERS.

Kinds:
- exa      Exa (api.exa.ai): POST /search, key in `x-api-key`.
- brave    Brave Search API: GET /res/v1/web/search, key in `X-Subscription-Token`.
- tavily   Tavily: POST /search, key as a bearer token.
- degoog   degoog (self-hosted): GET /api/search?q=… (JSON; `/search?format=json` returns HTML).
- searxng  SearXNG (self-hosted): GET /search?q=…&format=json (JSON output must be enabled in its
           settings.yml, `search.formats`).
- jina     Jina Search (s.jina.ai): GET /?q=…, key as a bearer token, `X-Respond-With: no-content`
           so only titles, links and descriptions come back (no page contents).
- firecrawl Firecrawl (cloud api.firecrawl.dev or self-hosted): POST /v1/search, key as a bearer
           token (the cloud needs one; a self-hosted server may not).

`freshness` is one of day, week, month, year (or None). A service without such a filter ignores it.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

FRESHNESS = ("day", "week", "month", "year")
_DAYS = {"day": 1, "week": 7, "month": 31, "year": 366}


class AdapterError(Exception):
    """The service answered, but not with something usable. The message is safe to show."""


@dataclass
class Result:
    title: str
    url: str
    snippet: str = ""
    source: str = ""  # the engine or index the service names (Bing, Google CSE…), if any
    published: str | None = None  # ISO date or instant, if the service gives one
    provider: str = ""  # the mailr provider's name, filled in by service.py

    @property
    def site(self) -> str:
        return site_of(self.url)

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "url": self.url, "site": self.site, "snippet": self.snippet,
                "source": self.source, "published": self.published, "provider": self.provider}


def site_of(url: str) -> str:
    host = (urlsplit(url or "").hostname or "").lower()
    return host.removeprefix("www.")


@dataclass
class Request:
    method: str
    path: str
    params: dict[str, Any] = field(default_factory=dict)
    json: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)


_TAG = re.compile(r"<[^>]{0,200}>")


def _clean(s: Any, n: int = 600) -> str:
    """Plain one-line text: tags some services leave in snippets (<strong>) and entities removed."""
    t = str(s or "")
    if "<" in t or "&" in t:
        t = html.unescape(_TAG.sub("", t))
    return " ".join(t.split())[:n]


def _date(v: Any) -> str | None:
    """An ISO date or instant from what a service sends, or None (relative ages are dropped)."""
    if not isinstance(v, str) or not v.strip():
        return None
    v = v.strip()
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.date().isoformat() if d.time() == datetime.min.time() and d.tzinfo is None else d.isoformat()


def _since(freshness: str | None) -> datetime | None:
    return datetime.now(UTC) - timedelta(days=_DAYS[freshness]) if freshness in _DAYS else None


def _http_url(u: Any) -> str | None:
    s = str(u or "").strip()
    return s if s.lower().startswith(("https://", "http://")) and len(s) <= 2000 else None


class Adapter:
    kind = ""
    label = ""
    needs_key = False
    default_url = ""
    default_network = "internet"
    freshness = True  # the service can filter by date

    def request(self, query: str, count: int, freshness: str | None, key: str | None) -> Request:
        raise NotImplementedError

    def parse(self, data: Any) -> list[Result]:
        raise NotImplementedError

    def _items(self, data: Any, *path: str) -> list[dict[str, Any]]:
        cur = data
        for p in path:
            if cur is None and p != path[0]:
                return []  # a section the service leaves out when it has nothing (Brave's "web")
            if not isinstance(cur, dict):
                raise AdapterError(f"{self.label} sent an answer without {'.'.join(path)}")
            cur = cur.get(p)
        if cur is None:
            return []
        if not isinstance(cur, list):
            raise AdapterError(f"{self.label} sent an answer without a result list")
        return [x for x in cur if isinstance(x, dict)]

    def meta(self) -> dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "needs_key": self.needs_key, "default_url": self.default_url,
                "default_network": self.default_network, "freshness": self.freshness}


class Exa(Adapter):
    kind, label, needs_key, default_url = "exa", "Exa", True, "https://api.exa.ai"

    def request(self, query, count, freshness, key):
        body: dict[str, Any] = {"query": query, "numResults": count, "type": "auto",
                                "contents": {"highlights": {"numSentences": 3, "highlightsPerUrl": 1}}}
        since = _since(freshness)
        if since:
            body["startPublishedDate"] = since.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return Request("POST", "/search", json=body, headers={"x-api-key": key or ""})

    def parse(self, data):
        out = []
        for x in self._items(data, "results"):
            url = _http_url(x.get("url"))
            if not url:
                continue
            hl = x.get("highlights")
            snippet = (hl[0] if isinstance(hl, list) and hl and isinstance(hl[0], str) else None) \
                or x.get("summary") or x.get("text") or ""
            out.append(Result(_clean(x.get("title"), 300) or site_of(url), url, _clean(snippet),
                              published=_date(x.get("publishedDate"))))
        return out


class Brave(Adapter):
    kind, label, needs_key, default_url = "brave", "Brave Search", True, "https://api.search.brave.com"

    def request(self, query, count, freshness, key):
        params: dict[str, Any] = {"q": query, "count": min(count, 20)}
        if freshness in _DAYS:
            params["freshness"] = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}[freshness]
        return Request("GET", "/res/v1/web/search", params=params,
                       headers={"X-Subscription-Token": key or "", "Accept": "application/json"})

    def parse(self, data):
        out = []
        for x in self._items(data, "web", "results"):
            url = _http_url(x.get("url"))
            if url:
                out.append(Result(_clean(x.get("title"), 300) or site_of(url), url, _clean(x.get("description")),
                                  source=_clean((x.get("profile") or {}).get("name"), 60)
                                  if isinstance(x.get("profile"), dict) else "",
                                  published=_date(x.get("page_age"))))
        return out


class Tavily(Adapter):
    kind, label, needs_key, default_url = "tavily", "Tavily", True, "https://api.tavily.com"

    def request(self, query, count, freshness, key):
        body: dict[str, Any] = {"query": query, "max_results": min(count, 20)}
        if freshness in _DAYS:
            body["time_range"] = freshness
        return Request("POST", "/search", json=body, headers={"Authorization": f"Bearer {key or ''}"})

    def parse(self, data):
        out = []
        for x in self._items(data, "results"):
            url = _http_url(x.get("url"))
            if url:
                out.append(Result(_clean(x.get("title"), 300) or site_of(url), url, _clean(x.get("content")),
                                  published=_date(x.get("published_date"))))
        return out


class Degoog(Adapter):
    kind, label, default_network, freshness = "degoog", "degoog", "local", False

    def request(self, query, count, freshness, key):
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        return Request("GET", "/api/search", params={"q": query}, headers={"Accept": "application/json", **headers})

    def parse(self, data):
        out = []
        for x in self._items(data, "results"):
            url = _http_url(x.get("url"))
            if url:
                out.append(Result(_clean(x.get("title"), 300) or site_of(url), url,
                                  _clean(x.get("snippet") or x.get("content")), source=_clean(x.get("source"), 60)))
        return out


class SearXNG(Adapter):
    kind, label, default_network = "searxng", "SearXNG", "local"

    def request(self, query, count, freshness, key):
        params: dict[str, Any] = {"q": query, "format": "json"}
        if freshness in _DAYS:
            params["time_range"] = freshness
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        return Request("GET", "/search", params=params, headers={"Accept": "application/json", **headers})

    def parse(self, data):
        out = []
        for x in self._items(data, "results"):
            url = _http_url(x.get("url"))
            if url:
                engines = x.get("engines") if isinstance(x.get("engines"), list) else []
                out.append(Result(_clean(x.get("title"), 300) or site_of(url), url, _clean(x.get("content")),
                                  source=_clean(", ".join(str(e) for e in engines[:3]), 60),
                                  published=_date(x.get("publishedDate"))))
        return out


class JinaSearch(Adapter):
    kind, label, needs_key, default_url, freshness = "jina", "Jina Search", True, "https://s.jina.ai", False

    def request(self, query, count, freshness, key):
        return Request("GET", "/", params={"q": query},
                       headers={"Authorization": f"Bearer {key or ''}", "Accept": "application/json",
                                "X-Respond-With": "no-content"})

    def parse(self, data):
        out = []
        for x in self._items(data, "data"):
            url = _http_url(x.get("url"))
            if url:
                out.append(Result(_clean(x.get("title"), 300) or site_of(url), url,
                                  _clean(x.get("description") or x.get("content")),
                                  published=_date(x.get("date") or x.get("publishedTime"))))
        return out


class FirecrawlSearch(Adapter):
    kind, label, default_url = "firecrawl", "Firecrawl", "https://api.firecrawl.dev"

    def request(self, query, count, freshness, key):
        body: dict[str, Any] = {"query": query, "limit": min(count, 20)}
        if freshness in _DAYS:
            body["tbs"] = "qdr:" + freshness[0]  # Google's qdr:d / w / m / y
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        return Request("POST", "/v1/search", json=body, headers=headers)

    def parse(self, data):
        if isinstance(data, dict) and data.get("success") is False:
            raise AdapterError(f"{self.label}: {_clean(data.get('error'), 200) or 'the search failed'}")
        out = []
        for x in self._items(data, "data"):
            url = _http_url(x.get("url"))
            if url:
                out.append(Result(_clean(x.get("title"), 300) or site_of(url), url, _clean(x.get("description"))))
        return out


ADAPTERS: dict[str, Adapter] = {a.kind: a for a in (Exa(), Brave(), Tavily(), Degoog(), SearXNG(), JinaSearch(),
                                                    FirecrawlSearch())}
