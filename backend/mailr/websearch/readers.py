"""Page readers: how `read_page` gets a page's text.

The chain. The built-in reader (pages.py: no JavaScript, through the egress proxy) is always
first. When it fails (an HTTP error, a block page, a timeout, not HTML) or finds almost no text (a
page that needs JavaScript, under THIN_CHARS characters), the readers an admin registered are tried
in order (Settings → Admin → Web search → Page readers; rows of web_search_providers with purpose
"reader"). The first that returns text wins. When they all fail, a thin built-in result is still
returned. A page with no text at all (a script-only page, or a bot check such as Reddit's for plain
requests) is an error, so the assistant never summarises an empty page.

A page address is checked before any reader sees it: https only, no IP literal outside the public
internet, no local-only name, the standard port (egress.check_url_offline). A reader that fetches
from this network (browserless on its direct route, Firecrawl or Jina "On my network") also has the
host looked up (public addresses only), and the address it ends on is checked the same way. So no
reader is ever asked for a page on the admin's network.

Kinds (one class each, like the search adapters):
- browserless  a self-hosted (or hosted) headless Chrome. POST /content renders the page and
               returns its HTML, which goes through the same extract() as the built-in reader. Its
               route (the row's `use_egress`, "Route" in the admin form) says where the browser's
               own page fetches go out:
               - through the egress proxy (the default): passed per request as Chrome's
                 --proxy-server launch argument, at the address in MAILR_BROWSER_EGRESS_PROXY when
                 the browser reaches the proxy at another address. The proxy's password is never
                 given to the browser (Puppeteer's `authenticate` answers any web site's 401 with
                 it): a proxy that asks for one is reached through the
                 browser gate, below. When the egress is required and cannot be used, the reader
                 refuses; it never falls back to the browser's own address. Without any egress
                 proxy (and not required) it fetches directly, as below.
               - directly from this network: the admin's explicit choice for this reader, allowed
                 even while MAILR_REQUIRE_EGRESS is on (a narrow exception:
                 sites see the server's home address; nothing else changes). The
                 page host must resolve to public addresses only, and the browser is pinned to the
                 checked address (--host-resolver-rules).
               On both routes: Chrome gets no implicit loopback bypass (--proxy-bypass-list=<-loopback>)
               and no WebRTC outside the proxy, the browser drops requests to private literals, local
               names and non-standard ports (rejectRequestPattern), and the address it ends on is
               checked again (netguard on the direct route, the offline rules on the egress route).
               The server's own browser (MAILR_BROWSER_URL) goes further when MAILR_BROWSER_GATE is
               set: its container has no network but the one it shares with the browser gate
               (websearch/gate.py), its --proxy-server is the gate's port for the route, and the gate
               applies netguard to every connection the browser makes. URL patterns cannot see
               names that resolve privately, WebSockets, WebRTC or service workers; the gate sees every connection.
               When the browser cannot load the page (its own error page, no answer from the site),
               a short second call (POST /function) asks the browser for its error, so the admin
               reads "the browser could not reach the egress proxy (egress:8888)", "the page
               answered HTTP 403" or "timed out", never "no text".
               The server's environment can set up one browserless reader (MAILR_BROWSER_URL and
               MAILR_BROWSER_TOKEN, "Browser (server)", service.sync_env_reader): its token is
               used from the environment only, never stored or shown, and removed from any error
               text a reader sends back.
- firecrawl    Firecrawl (cloud, or self-hosted): POST /v1/scrape for the page as Markdown.
- jina         Jina Reader (r.jina.ai, a key is optional): POST / with the page address.

Readers "on the internet" are third parties: the page address goes to them, and they fetch the
page from their own address. The request to them follows the search providers' route (through the
egress proxy when one is set, never directly while MAILR_REQUIRE_EGRESS is on). read_page already
refuses in a chat that has read local-only content, so no local-only text reaches them. A reader
"on my network" other than browserless fetches pages from the admin's own address, so it is not
used while MAILR_REQUIRE_EGRESS is on.

A Playwright server is not a kind: its run-server speaks Playwright's own websocket protocol, which
needs a Playwright client in mailr; browserless gives the same browser over plain HTTP.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, quote_plus, unquote, urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import egress, netguard, outbound
from mailr.models import WebSearchProvider
from mailr.websearch import pages, service
from mailr.websearch.adapters import site_of
from mailr.websearch.service import ProviderError

log = logging.getLogger("mailr.websearch.readers")

THIN_CHARS = 200
BUILTIN = "Built-in"
# Requests a browser without an egress proxy must not make: private and loopback literals, and
# names that only exist on a local network (DevTools URL patterns, matched against the whole URL).
_PRIVATE_PATTERNS = [
    r"^[a-z]+://(?:[^/@]*@)?(?:127\.|10\.|192\.168\.|169\.254\.|172\.(?:1[6-9]|2\d|3[01])\.|0\.|\[)",
    # CGNAT (Tailscale, 100.64.0.0/10) and benchmark (198.18.0.0/15) literals, private to netguard too.
    r"^[a-z]+://(?:[^/@]*@)?(?:100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.|198\.1[89]\.)",
    r"^[a-z]+://(?:[^/@]*@)?localhost(?:[:/]|$)",
    r"^[a-z]+://(?:[^/@]*@)?[^/:]*\.(?:localhost|local|internal|lan|home|arpa|localdomain|intranet|corp|private)(?:[:/]|$)",
    r"^[a-z]+://(?:[^/@]*@)?[^./:]+(?:[:/]|$)",
    # A port other than the standard ones (a service on a public host's other ports).
    r"^[a-z]+://(?:[^/@]*@)?[^/?#@:]+:(?!(?:80|443)(?:[/?#]|$))\d",
]
# The browser's network errors (net::ERR_*), grouped by what the admin should be told.
_PROXY_DOWN = {"ERR_PROXY_CONNECTION_FAILED", "ERR_NO_SUPPORTED_PROXIES", "ERR_MANDATORY_PROXY_CONFIGURATION_FAILED",
               "ERR_PROXY_CERTIFICATE_INVALID", "ERR_SOCKS_CONNECTION_FAILED", "ERR_PROXY_HTTP_1_1_REQUIRED"}
_PROXY_AUTH = {"ERR_PROXY_AUTH_UNSUPPORTED", "ERR_PROXY_AUTH_REQUESTED", "ERR_INVALID_AUTH_CREDENTIALS",
               "ERR_PROXY_AUTH_REQUESTED_WITH_NO_CONNECTION"}
_NO_NAME = {"ERR_NAME_NOT_RESOLVED", "ERR_NAME_RESOLUTION_FAILED", "ERR_SOCKS_CONNECTION_HOST_UNREACHABLE"}
_NO_CONNECT = {"ERR_CONNECTION_REFUSED", "ERR_CONNECTION_RESET", "ERR_CONNECTION_CLOSED", "ERR_CONNECTION_FAILED",
               "ERR_ADDRESS_UNREACHABLE", "ERR_INTERNET_DISCONNECTED", "ERR_NETWORK_CHANGED", "ERR_EMPTY_RESPONSE",
               "ERR_NETWORK_ACCESS_DENIED", "ERR_ADDRESS_INVALID"}
_TIMED_OUT = {"ERR_TIMED_OUT", "ERR_CONNECTION_TIMED_OUT"}
# Asks the browser what happened when /content brought back no page. The navigation is the only
# request let through (sub-resources and private or local addresses are aborted; service workers
# are bypassed), and only an error code or a status comes back. No proxy sign-in is ever given.
_DIAGNOSE_JS = """export default async function ({ page, context }) {
  const blocked = (context.patterns || []).map((p) => new RegExp(p));
  if (page.setBypassServiceWorker) await page.setBypassServiceWorker(true);
  await page.setRequestInterception(true);
  page.on('request', (r) => {
    if (r.isInterceptResolutionHandled && r.isInterceptResolutionHandled()) return;
    const doc = r.isNavigationRequest() && r.frame() === page.mainFrame();
    if (!doc || blocked.some((b) => b.test(r.url()))) r.abort('blockedbyclient'); else r.continue();
  });
  try {
    const res = await page.goto(context.url, { waitUntil: 'domcontentloaded', timeout: context.ms });
    return { ok: true, status: res ? res.status() : null };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e).slice(0, 300) };
  }
}"""


@dataclass
class Request:
    method: str
    path: str
    params: dict[str, Any] = field(default_factory=dict)
    json: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class BrowserProxy:
    server: str  # scheme://host:port, as Chrome's --proxy-server takes it
    # The proxy's sign-in, as configured. Never sent to the browser; kept to say why a
    # proxy that asks for it refuses the browser.
    username: str = ""
    password: str = ""
    name: str = "egress proxy"  # "browser gate" for websearch/gate.py

    @property
    def where(self) -> str:
        return self.server.split("://", 1)[-1]


# Chrome switches on every browser read: no WebRTC outside the proxy (STUN over UDP would show the
# server's own address and reach the LAN). With a proxy, loopback goes through it too
# (Chrome's implicit bypass would reach the browser container's own services).
_WEBRTC_ARGS = ("--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                "--webrtc-ip-handling-policy=disable_non_proxied_udp")
_PROXY_ARGS = ("--proxy-bypass-list=<-loopback>",)


class Reader:
    kind = ""
    label = ""
    needs_key = False
    default_url = ""
    default_network = "internet"
    renders = False  # runs the page's JavaScript

    def request(self, url: str, key: str | None, timeout_s: int, proxy: BrowserProxy | None,
                block_private: bool) -> Request:
        raise NotImplementedError

    def parse(self, r: httpx.Response, body: bytes, url: str) -> dict[str, Any]:
        """{title, description, published, text, final_url} or ProviderError."""
        raise NotImplementedError

    def meta(self) -> dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "needs_key": self.needs_key, "default_url": self.default_url,
                "default_network": self.default_network, "renders": self.renders}


def _json(body: bytes, label: str) -> Any:
    try:
        return json.loads(body)
    except ValueError:
        raise ProviderError(f"{label} did not answer with JSON") from None


class Browserless(Reader):
    kind, label, default_network, renders = "browserless", "browserless", "local", True

    def request(self, url, key, timeout_s, proxy, block_private, pin=None):
        """`pin` (host, address): the page host resolves only to the address mailr checked (the
        direct route without the gate; a rebinding name cannot move the page to the LAN)."""
        params: dict[str, Any] = {}
        if key:
            params["token"] = key
        body: dict[str, Any] = {
            "url": url,
            "gotoOptions": {"waitUntil": "networkidle2", "timeout": max(1, timeout_s - 2) * 1000},
            "bestAttempt": True,
            "rejectResourceTypes": ["image", "media", "font"],
        }
        args: list[str] = []
        if proxy is not None:
            args += [f"--proxy-server={proxy.server}", *_PROXY_ARGS]
        args += _WEBRTC_ARGS
        if pin is not None:
            host, ip = pin
            args.append(f"--host-resolver-rules=MAP {host} {f'[{ip}]' if ':' in ip else ip}")
        params["launch"] = json.dumps({"args": args})
        # Never `authenticate`: Puppeteer would answer a web site's 401 with the proxy's password.
        if block_private:
            body["rejectRequestPattern"] = list(_PRIVATE_PATTERNS)
        return Request("POST", "/content", params=params, json=body,
                       headers={"Content-Type": "application/json", "Accept": "text/html"})

    def parse(self, r, body, url):
        code = r.headers.get("x-response-code")
        if code and code.isdigit() and int(code) != 200:
            raise ProviderError(f"the page answered HTTP {code}")
        final = r.headers.get("x-response-url") or url
        html = body.decode(r.charset_encoding or "utf-8", errors="replace")
        return {**pages.extract(html), "final_url": final, "status": int(code) if code and code.isdigit() else None}


class Firecrawl(Reader):
    kind, label, default_url, renders = "firecrawl", "Firecrawl", "https://api.firecrawl.dev", True

    def request(self, url, key, timeout_s, proxy, block_private):
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        return Request("POST", "/v1/scrape", json={"url": url, "formats": ["markdown"], "onlyMainContent": True,
                                                    "blockAds": True, "timeout": max(1, timeout_s - 2) * 1000},
                       headers={"Accept": "application/json", **headers})

    def parse(self, r, body, url):
        data = _json(body, self.label)
        if not isinstance(data, dict) or data.get("success") is False or not isinstance(data.get("data"), dict):
            err = data.get("error") if isinstance(data, dict) else None
            raise ProviderError(f"{self.label} could not read the page" + (f": {str(err)[:200]}" if err else ""))
        d = data["data"]
        meta = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
        status = meta.get("statusCode")
        if isinstance(status, int) and status != 200:
            raise ProviderError(f"the page answered HTTP {status} ({self.label})")
        return {"title": str(meta.get("title") or meta.get("ogTitle") or "")[:300],
                "description": str(meta.get("description") or "")[:500],
                "published": meta.get("publishedTime") or meta.get("article:published_time") or None,
                "text": pages._tidy(str(d.get("markdown") or "")),
                "final_url": str(meta.get("url") or meta.get("sourceURL") or url),
                "status": status if isinstance(status, int) else None}


class JinaReader(Reader):
    kind, label, default_url = "jina", "Jina Reader", "https://r.jina.ai"
    renders = True

    def request(self, url, key, timeout_s, proxy, block_private):
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        return Request("POST", "/", json={"url": url},
                       headers={"Accept": "application/json", "X-Timeout": str(max(1, timeout_s - 2)), **headers})

    def parse(self, r, body, url):
        data = _json(body, self.label)
        d = data.get("data") if isinstance(data, dict) else None
        if not isinstance(d, dict):
            msg = data.get("readableMessage") or data.get("message") if isinstance(data, dict) else None
            raise ProviderError(f"{self.label} could not read the page" + (f": {str(msg)[:200]}" if msg else ""))
        return {"title": str(d.get("title") or "")[:300], "description": str(d.get("description") or "")[:500],
                "published": d.get("publishedTime") or None, "text": pages._tidy(str(d.get("content") or "")),
                "final_url": str(d.get("url") or url)}


READERS: dict[str, Reader] = {x.kind: x for x in (Browserless(), Firecrawl(), JinaReader())}


# ---------------------------------------------------------------- the browser's proxy


def browser_proxy(eg: str) -> BrowserProxy:
    """The egress proxy as Chrome's --proxy-server takes it (ProviderError when Chrome cannot use it)."""
    from mailr.config import get_settings

    raw = (get_settings().browser_egress_proxy or "").strip() or eg
    u = urlsplit(raw)
    scheme = (u.scheme or "http").lower()
    if scheme not in ("http", "https", "socks5", "socks4") or not u.hostname:
        raise ProviderError("the egress proxy address cannot be given to the browser")
    if u.username and scheme.startswith("socks"):
        raise ProviderError("the browser cannot sign in to a SOCKS proxy; use the egress proxy's HTTP port")
    host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
    port = u.port or {"http": 80, "https": 443}.get(scheme, 1080)
    return BrowserProxy(f"{scheme}://{host}:{port}", unquote(u.username or ""), unquote(u.password or ""))


def gate_for(p: WebSearchProvider) -> str:
    """The browser gate's host for this reader ("" = none): only the server's own browser
    (MAILR_BROWSER_URL), which the compose stack puts behind the gate (websearch/gate.py)."""
    from mailr.config import get_settings

    if not (p.from_env and p.kind == "browserless"):
        return ""
    return (get_settings().browser_gate or "").strip()


def gate_proxy(gate: str, route: str) -> BrowserProxy:
    from mailr.websearch.gate import DIRECT_PORT, EGRESS_PORT

    host = f"[{gate}]" if ":" in gate else gate
    return BrowserProxy(f"http://{host}:{EGRESS_PORT if route == 'egress' else DIRECT_PORT}", name="browser gate")


# ---------------------------------------------------------------- one reader


def browser_error(err: str, proxy: BrowserProxy | None) -> str:
    """What the browser's own error means, in words (never the page address or a secret)."""
    m = re.search(r"\b(ERR_[A-Z0-9_]{2,60})\b", err or "")
    code = m.group(1) if m else ""
    tag = f" (net::{code})" if code else ""
    where = proxy.where if proxy else ""
    what = proxy.name if proxy else ""
    if code in _PROXY_DOWN:
        return f"the browser could not reach the {what} ({where}){tag}" if proxy else \
            f"the browser could not reach a proxy{tag}"
    if code in _PROXY_AUTH:
        if proxy is None:
            return f"a proxy asked the browser to sign in{tag}"
        hint = ("; Mailr never gives the browser the proxy's password: use the browser gate "
                "(MAILR_BROWSER_GATE) or a proxy address without a password (MAILR_BROWSER_EGRESS_PROXY)"
                if proxy.name == "egress proxy" else "")
        return f"the {what} ({where}) refused the browser's sign-in{tag}{hint}"
    if code == "ERR_TUNNEL_CONNECTION_FAILED":
        if proxy is not None and proxy.name == "browser gate":
            return (f"the browser gate ({where}) did not open the site: the address is not a public https "
                    f"address, or the site could not be reached{tag}")
        return f"the egress proxy ({where}) could not connect to the site{tag}" if proxy else \
            f"the browser could not connect to the site{tag}"
    if code in _NO_NAME:
        return f"the site's name could not be found{tag}"
    if code in _NO_CONNECT:
        return f"the browser could not connect to the site{tag}"
    if code in _TIMED_OUT or (not code and re.search(r"time(?:d)? ?out|timeout", err or "", re.IGNORECASE)):
        return f"the page timed out{tag}"
    if code.startswith(("ERR_CERT_", "ERR_SSL_", "ERR_BAD_SSL")):
        return f"the site's secure connection failed{tag}"
    if code == "ERR_TOO_MANY_REDIRECTS":
        return f"the page redirected too many times{tag}"
    if code == "ERR_BLOCKED_BY_CLIENT":
        return f"the page leads to a private or local address, which the browser does not open{tag}"
    if code:
        return f"the browser could not load the page{tag}"
    return ""


async def _diagnose(c: httpx.AsyncClient, base: str, req: Request, url: str, proxy: BrowserProxy | None,
                    timeout: int) -> str:
    """Why /content brought back no page: one short /function call that only navigates. "" when the
    browser cannot say (an older browserless, or /function turned off)."""
    ms = max(1, min(timeout, 15) - 2) * 1000
    ctx: dict[str, Any] = {"url": url, "ms": ms, "patterns": list(_PRIVATE_PATTERNS)}
    try:
        r, body = await netguard.fetch_capped(c, "POST", base + "/function", limit=64 * 1024, deadline=ms / 1000 + 5,
                                              params=req.params or None, json={"code": _DIAGNOSE_JS, "context": ctx},
                                              headers={"Content-Type": "application/json", "Accept": "application/json"})
    except (TimeoutError, netguard.TooLarge, httpx.HTTPError, OSError, netguard.UnsafeTarget):
        return ""
    if r.status_code != 200:
        return ""
    try:
        d = json.loads(body)
    except ValueError:
        return ""
    if isinstance(d, dict) and isinstance(d.get("data"), dict):
        d = d["data"]
    if not isinstance(d, dict):
        return ""
    if d.get("ok") is False:
        return browser_error(str(d.get("error") or ""), proxy)
    status = d.get("status")
    if isinstance(status, int) and status != 200:
        return f"the page answered HTTP {status}"
    return ""


async def read_with(db: AsyncSession, p: WebSearchProvider, url: str, *, key: str | None = None,
                    count_usage: bool = True, max_chars: int = pages.MAX_CHARS) -> dict[str, Any]:
    """One reader, no fallback (ProviderError). `key` overrides the stored key (an admin's test).
    No error text carries the reader's key or a proxy password: a reader that echoes its
    request line would otherwise put `?token=` into the chat and into Admin."""
    if key is None and p.kind in READERS:
        key = service.key_of(p)
    hidden: list[str] = [key or ""]
    try:
        return await _read_with(db, p, url, key, hidden, count_usage=count_usage, max_chars=max_chars)
    except ProviderError as e:
        msg = scrub(str(e), hidden)
        if msg != str(e):
            raise ProviderError(msg) from None
        raise


def scrub(text: str, secrets: list[str]) -> str:
    """text without the given secrets (raw and URL-encoded) and without any token-like query value."""
    for sec in secrets:
        if sec and len(sec) >= 4:
            for form in {sec, quote(sec, safe=""), quote_plus(sec), quote(sec)}:
                text = text.replace(form, "…")
    return re.sub(r"(?i)([?&](?:token|key|api_key|apikey|access_token|password)=)[^&\s\"'<>]+", r"\1…", text)


async def _public_addresses(url: str) -> tuple[str, list[str]]:
    try:
        return await netguard.untrusted_url_addresses(url)
    except netguard.UnsafeTarget as e:
        raise ProviderError(f"not read: {e}") from None


async def _read_with(db: AsyncSession, p: WebSearchProvider, url: str, key: str | None, hidden: list[str], *,
                     count_usage: bool, max_chars: int) -> dict[str, Any]:
    reader = READERS.get(p.kind)
    if reader is None:
        raise ProviderError(f"{p.name} has an unknown kind {p.kind!r}")
    if reader.needs_key and not key:
        raise ProviderError(f"{p.name} needs an API key")
    try:
        egress.check_url_offline(url)
    except netguard.UnsafeTarget as e:
        raise ProviderError(f"not read: {e}") from None
    proxy: BrowserProxy | None = None
    pin: tuple[str, str] | None = None
    browser = reader.kind == "browserless"
    fetch = "third-party"  # how the page itself is fetched: egress | direct | third-party
    if p.network == "local":
        fetch = "direct"
        if browser:
            gate = gate_for(p)
            eg = ""
            if p.use_egress:
                try:
                    eg = await egress.gate(await egress.configured(db))
                except egress.EgressUnavailable as e:
                    raise ProviderError(str(e).replace("Remote content is paused", "Reading pages with a browser is "
                                                       "paused")) from None
            if eg:
                fetch = "egress"
                if gate:
                    proxy = gate_proxy(gate, "egress")
                else:
                    proxy = browser_proxy(eg)
                    hidden.append(proxy.password)
            else:
                # Directly from this network: the admin chose it for this reader (allowed even under
                # MAILR_REQUIRE_EGRESS), or there is no egress proxy at all. The page's host must be
                # public now. Through the gate every connection is checked again there; without it
                # the browser is pinned to the checked address of the page's host.
                host, ips = await _public_addresses(url)
                if gate:
                    proxy = gate_proxy(gate, "direct")
                else:
                    # The host goes into a Chrome switch: plain characters only.
                    if not netguard.plain_host(host):
                        raise ProviderError("This page address cannot be read directly")
                    pin = (host, ips[0])
        else:
            # Firecrawl or Jina "On my network" fetch from this network's address.
            if egress.required():
                raise ProviderError(f"{p.name} fetches pages from your own network's address, so it is not used "
                                    "while MAILR_REQUIRE_EGRESS is on")
            await _public_addresses(url)
    route = await service.route_for(db, p)
    if count_usage:
        await service._take(p)
    timeout = max(1, min(int(p.timeout_s or 30), 60))
    req = (reader.request(url, key, timeout, proxy, True, pin=pin) if browser
           else reader.request(url, key, timeout, proxy, False))
    base = p.base_url.rstrip("/")
    target = base + req.path
    if route.via == "egress":
        try:
            egress.check_url_offline(target)
        except netguard.UnsafeTarget as e:
            raise ProviderError(str(e)) from None
    try:
        async with service._client(route, float(timeout) + 5, p) as c:
            r, body = await netguard.fetch_capped(c, req.method, target, limit=pages.MAX_BYTES,
                                                  deadline=float(timeout) + 5, params=req.params or None,
                                                  json=req.json, headers=req.headers)
            why = ""
            if browser and r.status_code == 200 and _no_page(r, body):
                why = await _diagnose(c, base, req, url, proxy, timeout) or _no_answer(proxy)
            elif browser and r.status_code == 500:
                why = await _diagnose(c, base, req, url, proxy, timeout)
    except TimeoutError:
        raise ProviderError(f"the page timed out: {p.name} did not answer within {timeout} s") from None
    except netguard.TooLarge:
        raise ProviderError(f"{p.name} sent more than {pages.MAX_BYTES // (1024 * 1024)} MB") from None
    except outbound.Refused as e:
        raise ProviderError(str(e)) from None
    except (httpx.HTTPError, OSError, netguard.UnsafeTarget) as e:
        # The error names the reader's address at most; never the request (it can hold the key).
        raise ProviderError(f"{p.name} could not be reached {route.describe()}: {type(e).__name__}") from None
    if why:
        raise ProviderError(why)
    if browser and r.status_code == 408:
        raise ProviderError(f"the page timed out: the browser gave up after {timeout} s")
    if r.status_code != 200:
        detail = scrub(_error_text(body), hidden)
        if browser and (msg := browser_error(detail, proxy)):
            raise ProviderError(msg)
        raise ProviderError(service._status_error(p, r.status_code) + (f": {detail}" if detail else ""))
    doc = reader.parse(r, body, url)
    final = doc.get("final_url") or url
    # Where the page ended, on every route that fetches from this side: public
    # addresses only on the direct route, the offline rules through the egress proxy.
    try:
        if fetch == "direct":
            await netguard.check_untrusted_url(final)
        elif fetch == "egress":
            egress.check_url_offline(final)
    except netguard.UnsafeTarget as e:
        raise ProviderError(f"not read: the page moved to an address that is not allowed ({e})") from None
    text = doc.get("text") or ""
    if not text.strip():
        raise ProviderError(f"{p.name} found no text on the page")
    return _page(url, final, doc, max_chars, via=route.via, reader=p.name, route=fetch)


def _no_page(r: httpx.Response, body: bytes) -> bool:
    """browserless answered 200 but the browser has no page: its own error page, or no response
    from the site at all (no X-Response-Code) with nothing to read."""
    if (r.headers.get("x-response-url") or "").startswith("chrome-error:"):
        return True
    if r.headers.get("x-response-code"):
        return False
    head = body[:200_000].decode("utf-8", errors="replace")
    if "chrome-error://" in head or 'id="main-frame-error"' in head:
        return True
    return not pages.extract(head).get("text", "").strip()


def _no_answer(proxy: BrowserProxy | None) -> str:
    if proxy is not None:
        return f"the browser got no answer for the page: check that the browser can reach the {proxy.name} at {proxy.where}"
    return "the browser got no answer for the page (the site could not be reached)"


def _error_text(body: bytes) -> str:
    """A short, safe reason from a reader's error answer (browserless and Jina answer in plain text
    or JSON); never longer than one line."""
    t = body[:400].decode("utf-8", errors="replace")
    try:
        d = json.loads(body[:4000])
        if isinstance(d, dict):
            t = str(d.get("readableMessage") or d.get("error") or d.get("message") or "")
    except ValueError:
        pass
    t = re.sub(r"<[^>]{0,200}>", " ", t)
    return " ".join(t.split())[:200]


def _page(url: str, final: str, doc: dict[str, Any], max_chars: int, *, via: str, reader: str,
          route: str) -> dict[str, Any]:
    text = doc.get("text") or ""
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars].rsplit(" ", 1)[0] + " …"
    return {"url": url, "final_url": final, "site": site_of(final), "title": doc.get("title") or site_of(final),
            "description": doc.get("description") or "", "published": doc.get("published"), "text": text,
            "truncated": truncated, "chars": len(doc.get("text") or ""), "via": via, "reader": reader,
            "route": route, "status": doc.get("status")}


# ---------------------------------------------------------------- the chain


async def read(db: AsyncSession, url: str, *, max_chars: int = pages.MAX_CHARS) -> dict[str, Any]:
    """The page as text from the first reader that can read it (pages.PageError when none can)."""
    url = (url or "").strip()
    if not url.lower().startswith("https://"):
        raise pages.PageError("only https:// pages are read")
    rows = await service.providers(db, purpose="reader")
    tried: list[str] = []
    thin: dict[str, Any] | None = None
    try:
        page = await pages.read(db, url, max_chars=max_chars)
    except pages.PageError as e:
        if not rows or str(e).startswith("not read:"):
            raise  # an address no reader may open either
        tried.append(f"{BUILTIN}: {e}")
    else:
        page["reader"] = BUILTIN
        if page["chars"] >= THIN_CHARS or (not rows and page["chars"] > 0):
            return page
        thin = page
        tried.append(f"{BUILTIN}: almost no text without JavaScript")
    for p in rows:
        t0 = time.monotonic()
        try:
            out = await read_with(db, p, url, max_chars=max_chars)
        except ProviderError as e:
            tried.append(f"{p.name}: {e}")
            await service._note(p.id, str(e))
            continue
        await service._note(p.id, None)
        log.info("page read via %s (%s): %d chars in %d ms", p.name, out["via"], out["chars"],
                 int((time.monotonic() - t0) * 1000))
        return out
    if thin is not None and thin["chars"] > 0:
        return thin
    if thin is not None and not rows:
        raise pages.PageError("the page shows no text without JavaScript (a script-only page or a bot check); "
                              "an admin can add a page reader that runs a browser")
    raise pages.PageError("the page could not be read: " + "; ".join(tried))
