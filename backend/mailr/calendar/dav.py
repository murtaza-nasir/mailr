"""An async CalDAV client on httpx for the mirror's hot path (sync, multiget, conditional writes).

Every request and every redirect hop is checked with `netguard.check_url` first, and the
connection is pinned to the checked address; redirects are followed by hand (at most MAX_HOPS),
so a server can never bounce mailr to loopback, cloud metadata or mailr's own services. Hrefs
in and out are absolute URLs.

Credentials (Basic auth) go only to the account's own origin, or over https to a host in the
account's own domain (iCloud keeps the calendar home on pNN-caldav.icloud.com). They never go
to another site, never over plain http to another origin, and never after a redirect from
https to http (such a redirect is refused). Plain http with credentials is accepted only for a
server on a private network. Response bodies are capped at MAX_RESPONSE_BYTES.

An internal client (`internal=True`, only for the built-in calendars on mailr's own Stalwart,
calendar/builtin.py) talks to exactly one origin, the store's, which the network guard would
refuse as mailr's own infrastructure. Every request and every redirect hop must stay on that
origin; anything else is refused. Such a client can take a `limiter` (a semaphore that caps the
requests in flight) and retries the store's "too many concurrent requests" refusal with backoff.
"""

from __future__ import annotations

import asyncio
import contextlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import httpx

from mailr.core import netguard

MAX_HOPS = 5
MULTIGET_BATCH = 100
TIMEOUT = 20.0
MAX_RESPONSE_BYTES = 64 * 1024 * 1024

NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav", "cs": "http://calendarserver.org/ns/",
      "ic": "http://apple.com/ns/ical/"}
for _p, _u in NS.items():
    ET.register_namespace(_p, _u)


def norm_href(url: str) -> str:
    """One spelling per resource URL: the path percent-encoded the same way whatever the server
    or the caller wrote ('@' as %40, as Radicale does)."""
    u = urlsplit(url)
    return urlunsplit((u.scheme.lower(), u.netloc.lower(), quote(unquote(u.path), safe="/-._~!$&'()*+,;=:"),
                       u.query, ""))


class DavError(Exception):
    status: int = 0

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class PreconditionFailed(DavError):  # 412: If-Match / If-None-Match did not hold
    pass


class Unauthorized(DavError):  # 401 / 403 on sign-in
    pass


class NotFound(DavError):  # 404 / 410
    pass


class InvalidSyncToken(DavError):  # 403/409 with DAV:valid-sync-token
    pass


class ServerError(DavError):
    pass


class Truncated(DavError):  # a listing the server cut short (507 number-of-matches-within-limits)
    pass


# Stalwart refuses an account's request past `maxConcurrentRequests` (default 4, DAV included)
# with HTTP 400 and a JMAP problem body. The request was not processed, so it is safe to repeat.
BUSY_RETRIES = (0.1, 0.25, 0.5, 1.0, 2.0, 4.0)


def _busy_refusal(status: int, body: bytes) -> bool:
    return status == 400 and b"maxConcurrentRequests" in body[:2000]


def _truncated(ms: ET.Element) -> bool:
    """True when a multistatus carries a 507 response: the server listed only part of the
    collection. Stalwart adds one for its own request URI once `maxResults` is reached."""
    return any(" 507" in _text(r.find("d:status", NS)) for r in ms.findall("d:response", NS))


@dataclass
class CollectionInfo:
    href: str
    displayname: str = ""
    description: str = ""
    color: str | None = None
    order: int | None = None
    components: list[str] = field(default_factory=list)
    ctag: str | None = None
    sync_token: str | None = None
    read_only: bool = False
    calendar_tz: str | None = None


def _q(tag: str) -> str:
    p, _, local = tag.partition(":")
    return f"{{{NS[p]}}}{local}"


def _propfind_body(props: list[str]) -> bytes:
    root = ET.Element(_q("d:propfind"))
    prop = ET.SubElement(root, _q("d:prop"))
    for p in props:
        ET.SubElement(prop, _q(p))
    return ET.tostring(root, xml_declaration=True, encoding="utf-8")


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def _ok_props(resp: ET.Element) -> ET.Element:
    """The merged <prop> of a response's 200 propstats."""
    merged = ET.Element(_q("d:prop"))
    for ps in resp.findall("d:propstat", NS):
        if " 200" in _text(ps.find("d:status", NS)) or ps.find("d:status", NS) is None:
            for p in ps.findall("d:prop", NS):
                merged.extend(list(p))
    return merged


def _tz_from_vcal(text: str) -> str | None:
    for line in (text or "").splitlines():
        if line.upper().startswith("TZID:"):
            # Stored as an IANA key or not at all (a Windows name from Exchange maps; 9 Oct 2026).
            from mailr.calendar.tz import iana

            return iana(line.split(":", 1)[1].strip())
    return None


class DavClient:
    def __init__(self, base_url: str, username: str = "", password: str = "", *,
                 http: httpx.AsyncClient | None = None, timeout: float = TIMEOUT,
                 private_ok: bool | None = None, internal: bool = False,
                 limiter: asyncio.Semaphore | None = None):
        self.base = base_url if base_url.endswith("/") else base_url + "/"
        self.auth = httpx.BasicAuth(username, password) if username or password else None
        self.private_ok = private_ok
        self.internal = internal
        self.limiter = limiter
        self._own = http is None
        if http is None and internal:
            http = httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=timeout)
        self.http = http or netguard.guarded_client(private_ok=private_ok, timeout=timeout)

    async def __aenter__(self) -> DavClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._own:
            await self.http.aclose()

    def abs(self, href: str) -> str:
        return norm_href(urljoin(self.base, href))

    def same_resource(self, a: str, b: str) -> bool:
        ua, ub = urlsplit(self.abs(a)), urlsplit(self.abs(b))
        return (unquote(ua.path).rstrip("/") == unquote(ub.path).rstrip("/")
                and netguard.same_origin(self.abs(a), self.abs(b)))

    # ---------------- transport ----------------

    def _credentials_for(self, url: str) -> httpx.BasicAuth | None:
        """The account's credentials, only for its own origin or (https) its own domain."""
        if self.auth is None:
            return None
        if netguard.same_origin(url, self.base):
            return self.auth
        u, b = urlsplit(url), urlsplit(self.base)
        if u.scheme != "https" or b.scheme != "https" or (u.port or 443) != (b.port or 443):
            return None
        host, base_host = (u.hostname or "").lower(), (b.hostname or "").lower()
        if not host or not base_host or netguard._is_ip(host) or netguard._is_ip(base_host):
            return None
        labels = base_host.split(".")
        site = ".".join(labels[1:]) if len(labels) >= 3 else base_host
        return self.auth if host == site or host.endswith("." + site) else None

    async def _send(self, method: str, url: str, body: bytes | str | None, headers: dict[str, str] | None,
                    auth: httpx.BasicAuth | None) -> tuple[httpx.Response, bytes, httpx.Request]:
        """One hop: the response (streamed, capped) and its body. An internal client waits for
        a free slot of its limiter and repeats a "too many concurrent requests" refusal."""
        for attempt in range(len(BUSY_RETRIES) + 1):
            try:
                req = self.http.build_request(method, url, content=body, headers=headers or {})
                async with self.limiter or contextlib.nullcontext():
                    resp = await self.http.send(req, auth=auth or httpx.USE_CLIENT_DEFAULT, stream=True)
                    try:
                        data = await netguard.read_capped(resp, MAX_RESPONSE_BYTES)
                    finally:
                        await resp.aclose()
            except netguard.TooLarge:
                raise ServerError(f"{method} {url}: the response is too large") from None
            except httpx.HTTPError as e:
                raise ServerError(f"{method} {url}: {type(e).__name__}") from None
            if self.internal and _busy_refusal(resp.status_code, data) and attempt < len(BUSY_RETRIES):
                await asyncio.sleep(BUSY_RETRIES[attempt])
                continue
            return resp, data, req
        raise AssertionError("unreachable")

    async def request(self, method: str, url: str, *, body: bytes | str | None = None,
                      headers: dict[str, str] | None = None) -> httpx.Response:
        url = self.abs(url)
        for _ in range(MAX_HOPS + 1):
            u = urlsplit(url)
            if self.internal:
                # Only the store's own origin, never a hop elsewhere (the credentials are the
                # service account's).
                if not netguard.same_origin(url, self.base):
                    raise DavError("the calendar store sent Mailr to another address; refused")
                auth = self.auth
            else:
                try:
                    await netguard.check_url(url, private_ok=self.private_ok)
                except netguard.UnsafeTarget as e:
                    raise DavError(str(e)) from None
                auth = self._credentials_for(url)
                if auth is not None and u.scheme == "http" and not await asyncio.to_thread(
                        netguard.is_private_target, u.hostname or ""):
                    raise DavError("use https:// for a server on the internet; Mailr does not send the password "
                                   "over plain http")
            resp, data, req = await self._send(method, url, body, headers, auth)
            # The body is decoded already: drop the encoding headers that described the wire form.
            hdrs = [(k, v) for k, v in resp.headers.multi_items()
                    if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
            resp = httpx.Response(resp.status_code, headers=hdrs, content=data, request=req)
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                nxt = urljoin(url, resp.headers["location"])
                if u.scheme == "https" and urlsplit(nxt).scheme != "https":
                    raise ServerError(f"{method} {url}: the server redirected from https to http; refused")
                url = nxt
                if resp.status_code == 303:
                    method, body = "GET", None
                continue
            return resp
        raise ServerError(f"too many redirects for {url}")

    @staticmethod
    def _raise(resp: httpx.Response, what: str) -> None:
        s = resp.status_code
        if s < 300:
            return
        if s == 412:
            raise PreconditionFailed(f"{what}: precondition failed", s)
        if s in (404, 410):
            raise NotFound(f"{what}: not found", s)
        if s == 401:
            raise Unauthorized(f"{what}: sign-in failed", s)
        if s in (403, 409) and b"valid-sync-token" in resp.content:
            raise InvalidSyncToken(f"{what}: the sync token is no longer valid", s)
        if s == 403:
            raise DavError(f"{what}: forbidden", s)
        if s == 507:
            raise Truncated(f"{what}: the server listed only part of the collection", s)
        raise ServerError(f"{what}: HTTP {s}", s)

    async def _multistatus(self, method: str, url: str, body: bytes, depth: str) -> ET.Element:
        resp = await self.request(method, url, body=body, headers={
            "Depth": depth, "Content-Type": "application/xml; charset=utf-8"})
        self._raise(resp, f"{method} {url}")
        try:
            return ET.fromstring(resp.content)
        except ET.ParseError as e:
            raise ServerError(f"{method} {url}: unreadable XML ({e})") from None

    # ---------------- discovery ----------------

    async def options(self, url: str | None = None) -> set[str]:
        resp = await self.request("OPTIONS", url or self.base)
        if resp.status_code == 401:
            raise Unauthorized("sign-in failed", 401)
        return {x.strip().lower() for x in resp.headers.get("dav", "").split(",") if x.strip()}

    async def _href_prop(self, url: str, prop: str) -> str | None:
        ms = await self._multistatus("PROPFIND", url, _propfind_body([prop]), "0")
        for resp in ms.findall("d:response", NS):
            el = _ok_props(resp).find(f"{prop.split(':')[0]}:{prop.split(':')[1]}/d:href", NS)
            if el is not None and _text(el):
                return self.abs(_text(el))
        return None

    async def home(self) -> str:
        """The calendar home set (falls back to the principal, then the base URL)."""
        principal = await self._href_prop(self.base, "d:current-user-principal") or self.base
        try:
            return await self._href_prop(principal, "c:calendar-home-set") or principal
        except NotFound:
            return principal

    async def collections(self, home: str) -> list[CollectionInfo]:
        """Every calendar collection under the home set, with what the mirror stores about it,
        in one PROPFIND (it doubles as the "has anything changed?" probe)."""
        props = ["d:resourcetype", "d:displayname", "c:calendar-description", "c:supported-calendar-component-set",
                 "cs:getctag", "d:sync-token", "ic:calendar-color", "ic:calendar-order",
                 "d:current-user-privilege-set", "c:calendar-timezone"]
        ms = await self._multistatus("PROPFIND", home, _propfind_body(props), "1")
        if _truncated(ms):  # a missing collection would read as deleted
            raise Truncated(f"PROPFIND {home}: the server listed only part of the calendars", 507)
        out = []
        for resp in ms.findall("d:response", NS):
            href = self.abs(_text(resp.find("d:href", NS)))
            p = _ok_props(resp)
            rt = p.find("d:resourcetype", NS)
            if rt is None or rt.find("c:calendar", NS) is None:
                continue
            comps = [c.get("name", "").upper() for c in p.findall("c:supported-calendar-component-set/c:comp", NS)]
            privs = {x.tag.split("}")[-1] for x in p.findall("d:current-user-privilege-set/d:privilege/*", NS)}
            order = _text(p.find("ic:calendar-order", NS))
            color = _text(p.find("ic:calendar-color", NS))
            out.append(CollectionInfo(
                href=href, displayname=_text(p.find("d:displayname", NS)),
                description=_text(p.find("c:calendar-description", NS)),
                color=color[:7] if color.startswith("#") and len(color) >= 7 else None,
                order=int(order) if order.lstrip("-").isdigit() else None,
                components=[c for c in comps if c] or ["VEVENT", "VTODO", "VJOURNAL"],
                ctag=_text(p.find("cs:getctag", NS)) or None, sync_token=_text(p.find("d:sync-token", NS)) or None,
                read_only=bool(privs) and not ({"write", "write-content", "all"} & privs),
                calendar_tz=_tz_from_vcal(_text(p.find("c:calendar-timezone", NS)))))
        return out

    # ---------------- reading ----------------

    async def sync_collection(self, href: str, token: str | None
                              ) -> tuple[list[tuple[str, str | None]], list[str], str | None, bool]:
        """RFC 6578: (changed [(href, etag)], removed [href], new token, truncated).

        A truncated answer is never a complete listing. Stalwart returns its current token with
        it (not a continuation token), so the items past its limit never come in a later delta;
        callers fall back to an ETag listing."""
        root = ET.Element(_q("d:sync-collection"))
        t = ET.SubElement(root, _q("d:sync-token"))
        t.text = token or None
        ET.SubElement(root, _q("d:sync-level")).text = "1"
        ET.SubElement(ET.SubElement(root, _q("d:prop")), _q("d:getetag"))
        ms = await self._multistatus("REPORT", href, ET.tostring(root, xml_declaration=True, encoding="utf-8"), "0")
        changed, removed, truncated = [], [], False
        for resp in ms.findall("d:response", NS):
            h = self.abs(_text(resp.find("d:href", NS)))
            status = _text(resp.find("d:status", NS))
            if " 404" in status or " 410" in status:
                removed.append(h)
                continue
            if " 507" in status:
                truncated = True
                continue
            if self.same_resource(h, href):
                continue
            changed.append((h, _text(_ok_props(resp).find("d:getetag", NS)) or None))
        return changed, removed, _text(ms.find("d:sync-token", NS)) or None, truncated

    async def etags(self, href: str) -> dict[str, str | None]:
        """Every resource in the collection with its ETag (depth-1 PROPFIND). Raises Truncated
        when the server listed only part of it, because callers compute removals from this."""
        ms = await self._multistatus("PROPFIND", href, _propfind_body(["d:getetag", "d:resourcetype"]), "1")
        if _truncated(ms):
            raise Truncated(f"PROPFIND {href}: the server listed only part of the collection", 507)
        out = {}
        for resp in ms.findall("d:response", NS):
            h = self.abs(_text(resp.find("d:href", NS)))
            if self.same_resource(h, href):
                continue
            p = _ok_props(resp)
            rt = p.find("d:resourcetype", NS)
            if rt is not None and len(rt):
                continue  # a sub-collection
            out[h] = _text(p.find("d:getetag", NS)) or None
        return out

    async def multiget(self, href: str, hrefs: list[str]) -> list[tuple[str, str | None, str]]:
        """calendar-multiget in batches: [(href, etag, ics)] for the resources that exist."""
        out = []
        for i in range(0, len(hrefs), MULTIGET_BATCH):
            root = ET.Element(_q("c:calendar-multiget"))
            prop = ET.SubElement(root, _q("d:prop"))
            ET.SubElement(prop, _q("d:getetag"))
            ET.SubElement(prop, _q("c:calendar-data"))
            for h in hrefs[i:i + MULTIGET_BATCH]:
                ET.SubElement(root, _q("d:href")).text = urlsplit(self.abs(h)).path
            ms = await self._multistatus("REPORT", href, ET.tostring(root, xml_declaration=True, encoding="utf-8"), "1")
            for resp in ms.findall("d:response", NS):
                p = _ok_props(resp)
                data = p.find("c:calendar-data", NS)
                if data is None or not (data.text or "").strip():
                    continue
                out.append((self.abs(_text(resp.find("d:href", NS))), _text(p.find("d:getetag", NS)) or None,
                            data.text))
        return out

    async def get(self, href: str) -> tuple[str, str | None]:
        resp = await self.request("GET", href)
        self._raise(resp, f"GET {href}")
        return resp.text, resp.headers.get("etag")

    # ---------------- writing ----------------

    async def put(self, href: str, ics: str, *, if_match: str | None = None, if_none_match: bool = False) -> str | None:
        """PUT a resource; returns the new ETag (None when the server sent none)."""
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        if if_match:
            headers["If-Match"] = if_match
        if if_none_match:
            headers["If-None-Match"] = "*"
        resp = await self.request("PUT", href, body=ics.encode(), headers=headers)
        self._raise(resp, f"PUT {href}")
        return resp.headers.get("etag")

    async def delete(self, href: str, *, if_match: str | None = None) -> None:
        resp = await self.request("DELETE", href, headers={"If-Match": if_match} if if_match else {})
        self._raise(resp, f"DELETE {href}")

    async def mkcalendar(self, home: str, slug: str, name: str, components: list[str], color: str | None = None) -> str:
        href = self.abs(urljoin(home if home.endswith("/") else home + "/", slug.strip("/") + "/"))
        root = ET.Element(_q("c:mkcalendar"))
        prop = ET.SubElement(ET.SubElement(root, _q("d:set")), _q("d:prop"))
        ET.SubElement(prop, _q("d:displayname")).text = name
        comp_set = ET.SubElement(prop, _q("c:supported-calendar-component-set"))
        for c in components:
            ET.SubElement(comp_set, _q("c:comp"), {"name": c})
        if color:
            ET.SubElement(prop, _q("ic:calendar-color")).text = color
        resp = await self.request("MKCALENDAR", href, body=ET.tostring(root, xml_declaration=True, encoding="utf-8"),
                                  headers={"Content-Type": "application/xml; charset=utf-8"})
        self._raise(resp, f"MKCALENDAR {href}")
        return href

    async def proppatch(self, href: str, *, displayname: str | None = None, color: str | None = None) -> bool:
        """Set the name and/or colour; False when the server refused a property."""
        root = ET.Element(_q("d:propertyupdate"))
        prop = ET.SubElement(ET.SubElement(root, _q("d:set")), _q("d:prop"))
        if displayname is not None:
            ET.SubElement(prop, _q("d:displayname")).text = displayname
        if color is not None:
            ET.SubElement(prop, _q("ic:calendar-color")).text = color
        resp = await self.request("PROPPATCH", href, body=ET.tostring(root, xml_declaration=True, encoding="utf-8"),
                                  headers={"Content-Type": "application/xml; charset=utf-8"})
        self._raise(resp, f"PROPPATCH {href}")
        if resp.status_code == 207:
            try:
                ms = ET.fromstring(resp.content)
            except ET.ParseError:
                return True
            return all(" 200" in _text(s) for s in ms.findall(".//d:propstat/d:status", NS))
        return True

    async def delete_collection(self, href: str) -> None:
        resp = await self.request("DELETE", href)
        self._raise(resp, f"DELETE {href}")
