"""The web app's error reports (core/errors.py).

- GET /errors/config: the browser's DSN, release and environment, or {"dsn": null} while error
  reporting is off, with `tunnel`: the address the page posts its reports to. No sign-in: errors
  on the sign-in page count too. The DSN's key is public by design (it only allows sending
  reports). The tunnel carries a signed key of its own for each page load, bound to the caller's
  address (an IPv6 address by its /64) for TUNNEL_KEY_HOURS, with a quota per key (PER_KEY).
- POST /errors/envelope: the Sentry SDK's `tunnel`. The browser posts its report here, on its own
  origin (the page's Content-Security-Policy allows no other). It needs a signed-in session or the
  key from /errors/config. The app keeps only the event fields a report needs, within size and
  depth limits, scrubs every event as it scrubs its own (no query strings, no headers, no
  addresses, no breadcrumbs), sets the user id from the session and the release itself, drops
  every item that is not an event, and passes the envelope to the server named in
  MAILR_SENTRY_BROWSER_DSN (or MAILR_SENTRY_DSN). An envelope for any other DSN is refused, so
  the relay cannot be used to reach another server. At most ENVELOPE_MAX bytes; limits per
  address, per user, per key and for the whole server (core/throttle.py). Reports without a
  session share a smaller server budget of their own (PER_SERVER_SIGNED_OUT), and one address may
  use only a share of either budget (PER_IP_HOUR), so no single client can use up the reports of
  signed-in users.

The relay's connection goes only to the DSN's host, never through a proxy, and follows no redirect.
Its address is resolved once and checked (_relay_addresses): never link-local (cloud metadata),
multicast or unspecified; a loopback or private address only when the DSN names one, as an address,
as localhost or by a local name (a compose service such as glitchtip, *.internal, *.lan, *.local);
a public name gets public addresses only.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
import socket
from datetime import timedelta
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from mailr.config import get_settings
from mailr.core import errors, netguard, throttle

router = APIRouter()
log = logging.getLogger("mailr.errors")

ENVELOPE_MAX = 64 * 1024
# The event fields a report needs; anything else is dropped.
EVENT_KEYS = frozenset({"event_id", "timestamp", "platform", "level", "logger", "message", "logentry", "exception",
                        "tags", "contexts", "request", "sdk", "transaction"})
CONTEXT_KEYS = ("browser", "os", "device")
MAX_DEPTH = 24
MAX_ITEMS = 5
MAX_EXCEPTIONS = 10
MAX_FRAMES = 100
MAX_TAGS = 20
TUNNEL_KEY_HOURS = 6
PER_IP = throttle.Limit("errors-ip", 30, timedelta(minutes=10))
# One address's share of the server's hour: a tenth of the signed-in budget.
PER_IP_HOUR = throttle.Limit("errors-ip-hour", 60, timedelta(hours=1))
PER_USER = throttle.Limit("errors-user", 60, timedelta(minutes=10))
PER_KEY = throttle.Limit("errors-key", 20, timedelta(minutes=10))
PER_SERVER = throttle.Limit("errors-all", 600, timedelta(hours=1))
PER_SERVER_SIGNED_OUT = throttle.Limit("errors-anon", 200, timedelta(hours=1))


def browser_dsn() -> str:
    s = get_settings()
    return s.sentry_browser_dsn or s.sentry_dsn


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _keys() -> URLSafeTimedSerializer:
    s = get_settings()
    return URLSafeTimedSerializer(s.session_secret or s.secret_key, salt="mailr-error-tunnel")


def _ip_tag(ip: str) -> str:
    return hashlib.sha256(throttle.client_net(ip).encode()).hexdigest()[:16]


def tunnel_key(ip: str) -> str:
    """A key for the envelope tunnel, for this address only, with an id of its own (one per page
    load) that its quota counts by."""
    import secrets

    return _keys().dumps({"ip": _ip_tag(ip), "id": secrets.token_hex(8)})


def tunnel_key_id(key: str, ip: str) -> str | None:
    """The key's id when it is valid for this address, else None."""
    try:
        data = _keys().loads(key, max_age=TUNNEL_KEY_HOURS * 3600)
    except BadSignature:
        return None
    if not isinstance(data, dict) or data.get("ip") != _ip_tag(ip) or not isinstance(data.get("id"), str):
        return None
    return data["id"]


def tunnel_key_ok(key: str, ip: str) -> bool:
    return tunnel_key_id(key, ip) is not None


@router.get("/errors/config")
async def error_config(request: Request):
    dsn = browser_dsn()
    if not dsn or _target(dsn) is None:
        return {"dsn": None}
    s = get_settings()
    return {"dsn": dsn, "release": errors.release(), "environment": s.sentry_environment or None,
            "tunnel": "/api/v1/errors/envelope?k=" + quote(tunnel_key(_ip(request)))}


# A DSN as GlitchTip and Sentry write it: scheme://key@host[:port][/prefix]/project. One "@", a
# plain key and host, digits for the port and the project: no second reading of where it points.
_DSN = re.compile(r"^(https?)://([A-Za-z0-9]{1,64})(?::[A-Za-z0-9]{0,64})?@([A-Za-z0-9.-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\])"
                  r"(?::(\d{1,5}))?((?:/[A-Za-z0-9._~-]+)*)/(\d{1,12})/?$")
# Names only a local network resolves: a single label (a compose service) or a local suffix.
_LOCAL_SUFFIXES = (".internal", ".lan", ".local", ".localhost", ".home.arpa")


def _target(dsn: str) -> tuple[str, str] | None:
    """(envelope URL, public key) of a DSN, or None when it is not one."""
    m = _DSN.match(dsn or "")
    if m is None or (m[4] and not 0 < int(m[4]) < 65536):
        return None
    scheme, key, host, port, prefix, project = m.groups()
    host = host.lower()
    return f"{scheme}://{host}{f':{port}' if port else ''}{prefix}/api/{project}/envelope/", key


def _relay_addresses(host: str, port: int) -> list[str]:
    """The addresses the relay may connect to for the DSN's host; the connection is pinned to them,
    so a changed DNS answer cannot move it. Raises netguard.UnsafeTarget."""
    name = host.strip("[]").lower().rstrip(".")
    try:
        literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None = ipaddress.ip_address(name)
    except ValueError:
        literal = None
    localhost = name == "localhost" or name.endswith(".localhost")
    local_name = literal is None and ("." not in name or name.endswith(_LOCAL_SUFFIXES))
    try:
        infos = socket.getaddrinfo(name, port, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        raise netguard.UnsafeTarget(f"cannot resolve {name}") from None
    ips: list[str] = []
    for info in infos:
        ip = str(info[4][0]).split("%", 1)[0]
        addr = ipaddress.ip_address(ip)
        if isinstance(addr, ipaddress.IPv6Address) and (inner := netguard._inner_v4(addr)) is not None:
            addr = inner
        if addr.is_link_local or addr.is_multicast or addr.is_unspecified or addr.is_reserved:
            raise netguard.UnsafeTarget(f"{name} resolves to a link-local or reserved address")
        if addr.is_loopback:
            ok = localhost or (literal is not None and literal.is_loopback)
        else:
            ok = netguard._is_public(addr) or literal is not None or local_name
        if not ok:
            raise netguard.UnsafeTarget(f"{name} resolves to an address the DSN does not name")
        if ip not in ips:
            ips.append(ip)
    if not ips:
        raise netguard.UnsafeTarget(f"cannot resolve {name}")
    return ips


def parse_envelope(body: bytes) -> tuple[dict[str, Any], list[tuple[dict[str, Any], bytes]]]:
    """(header, [(item header, payload)]) of a Sentry envelope; ValueError when it is not one."""
    head, _, rest = body.partition(b"\n")
    header = json.loads(head or b"{}")
    if not isinstance(header, dict):
        raise ValueError("envelope header")
    items: list[tuple[dict[str, Any], bytes]] = []
    while rest.strip():
        line, _, rest = rest.partition(b"\n")
        if not line.strip():
            continue
        ih = json.loads(line)
        if not isinstance(ih, dict):
            raise ValueError("item header")
        n = ih.get("length")
        if isinstance(n, int) and n >= 0:
            payload, rest = rest[:n], rest[n:]
            if rest.startswith(b"\n"):
                rest = rest[1:]
        else:
            payload, _, rest = rest.partition(b"\n")
        items.append((ih, payload))
    return header, items


def build_envelope(header: dict[str, Any], items: list[tuple[dict[str, Any], bytes]]) -> bytes:
    out = [json.dumps(header).encode()]
    for ih, payload in items:
        out.append(json.dumps({**ih, "length": len(payload)}).encode())
        out.append(payload)
    return b"\n".join(out) + b"\n"


def _client(dsn: str) -> httpx.AsyncClient:
    from mailr.core import outbound

    return outbound.client(errors.dsn_service(dsn), resolve=_relay_addresses, timeout=5,
                           follow_redirects=False)


def _depth(v: Any, limit: int = MAX_DEPTH) -> int:
    """How deeply a parsed JSON value nests, counting no further than `limit` + 1."""
    stack, deepest = [(v, 1)], 0
    while stack:
        node, d = stack.pop()
        deepest = max(deepest, d)
        if deepest > limit:
            break
        if isinstance(node, dict):
            stack.extend((x, d + 1) for x in node.values())
        elif isinstance(node, list):
            stack.extend((x, d + 1) for x in node)
    return deepest


def shape(event: dict[str, Any]) -> dict[str, Any]:
    """Only the fields a web report needs, each in the shape the error server expects and within its
    limits; a field in another shape is dropped."""
    out = {k: v for k, v in event.items() if k in EVENT_KEYS}
    for k in ("event_id", "platform", "level", "logger", "transaction", "message"):
        if k in out and not isinstance(out[k], str):
            out.pop(k)
        elif k in out:
            out[k] = out[k][:errors.MAX_TEXT]
    if not isinstance(out.get("timestamp"), (int, float, str)):
        out.pop("timestamp", None)
    tags = out.get("tags")
    out["tags"] = {str(k)[:32]: v[:200] for k, v in list(tags.items())[:MAX_TAGS]
                   if isinstance(v, str)} if isinstance(tags, dict) else {}
    ctx = out.get("contexts")
    out["contexts"] = {k: {kk: vv for kk, vv in ctx[k].items() if isinstance(vv, (str, int, float, bool))}
                       for k in CONTEXT_KEYS if isinstance(ctx, dict) and isinstance(ctx.get(k), dict)}
    for k in ("logentry", "request", "sdk"):
        if k in out and not isinstance(out[k], dict):
            out.pop(k)
    exc = out.get("exception")
    values = exc.get("values") if isinstance(exc, dict) else None
    if isinstance(values, list):
        kept = []
        for v in values[:MAX_EXCEPTIONS]:
            if not isinstance(v, dict):
                continue
            v = {k: v[k] for k in ("type", "value", "module", "mechanism", "stacktrace") if k in v}
            st = v.get("stacktrace")
            frames = st.get("frames") if isinstance(st, dict) else None
            if isinstance(frames, list):
                v["stacktrace"] = {"frames": [f for f in frames[-MAX_FRAMES:] if isinstance(f, dict)]}
            else:
                v.pop("stacktrace", None)
            kept.append(v)
        out["exception"] = {"values": kept}
    else:
        out.pop("exception", None)
    return out


async def _reporter(request: Request) -> tuple[int | None, str | None, bool]:
    """(the signed-in user's id or None, the tunnel key's id or None, whether the caller may
    report): a live session, or a tunnel key of this address."""
    from mailr.core.auth import SESSION_COOKIE, _session_principal
    from mailr.db import session_scope

    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        async with session_scope() as db:
            p = await _session_principal(db, raw)
        if p is not None:
            return p.user_id, None, True
    kid = tunnel_key_id(request.query_params.get("k") or "", _ip(request))
    return None, kid, kid is not None


async def _rate_ok(ip: str, user_id: int | None, key_id: str | None = None) -> bool:
    """Count one report against the address, the user or the key, and the server's budget for
    signed-in or signed-out reports; False when one is full. Checked and counted in one step
    (throttle.attempt)."""
    from fastapi import HTTPException

    from mailr.db import sessionmaker

    buckets: list[tuple[throttle.Limit, tuple[object, ...]]] = [(PER_IP, (ip,)), (PER_IP_HOUR, (ip,))]
    if user_id is not None:
        buckets += [(PER_USER, (user_id,)), (PER_SERVER, ())]
    else:
        buckets += [(PER_KEY, (key_id or "",)), (PER_SERVER_SIGNED_OUT, ())]
    async with sessionmaker()() as db:
        try:
            await throttle.attempt(db, buckets, buckets)
        except HTTPException:
            return False
    return True


@router.post("/errors/envelope")
async def error_envelope(request: Request):
    dsn = browser_dsn()
    target = _target(dsn) if dsn else None
    if target is None:
        return Response(status_code=404)
    user_id, key_id, allowed = await _reporter(request)
    if not allowed:
        return Response(status_code=401)
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > ENVELOPE_MAX:
            return Response(status_code=413)
    try:
        header, items = parse_envelope(body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return Response(status_code=400)
    if header.get("dsn") not in (None, dsn):
        return Response(status_code=403)
    events: list[dict[str, Any]] = []
    for ih, payload in items[:MAX_ITEMS]:
        if ih.get("type") != "event":
            continue
        try:
            event = json.loads(payload)
        except (ValueError, RecursionError):
            return Response(status_code=400)
        if not isinstance(event, dict) or _depth(event) > MAX_DEPTH:
            return Response(status_code=400)
        events.append(event)
    if not events:
        return JSONResponse({})
    if not await _rate_ok(_ip(request), user_id, key_id):
        return Response(status_code=429)
    s = get_settings()
    kept: list[tuple[dict[str, Any], bytes]] = []
    for event in events:
        event = errors.scrub(shape(event), dsn=dsn) or {}
        # Who and which release come from the server, never from the page.
        event["user"] = {"id": str(user_id)} if user_id is not None else {}
        for k, v in (("release", errors.release()), ("environment", s.sentry_environment)):
            event.pop(k, None)
            if v:
                event[k] = v
        event["tags"]["process"] = "web"
        kept.append(({"type": "event"}, json.dumps(event).encode()))
    header = {k: v for k, v in header.items() if k in ("event_id", "sent_at", "sdk")}
    header["dsn"] = dsn
    url, key = target
    try:
        async with _client(dsn) as c:
            r = await c.post(url, content=build_envelope(header, kept),
                             params={"sentry_key": key, "sentry_version": "7"},
                             headers={"Content-Type": "application/x-sentry-envelope"})
        if r.status_code >= 400:
            log.warning("the error server refused a web report: %s", r.status_code)
    except httpx.HTTPError as e:
        log.warning("the error server is unreachable: %s", type(e).__name__)
    return JSONResponse({})
