"""Fetches of URLs taken from mail content, and the egress proxy they go through.

Every fetch of an address that a sender chose goes through this module: remote images (the
image proxy, at arrival and when a message is opened), sender logos and pictures (BIMI,
Gravatar, Libravatar) and one-click unsubscribe. Callers do three things, in order:

1. `egress = await gate(configured)`: the egress proxy to use, "" for a direct fetch, or
   EgressUnavailable when the fetch must not run at all;
2. `client(egress, ...)`: the httpx client, through the proxy or pinned to checked public
   addresses (netguard). It refuses to build a direct client while MAILR_REQUIRE_EGRESS is on;
3. `await check_url(url, egress)` before every hop (redirects are followed by hand).

Fail closed (MAILR_REQUIRE_EGRESS, off by default): without a usable egress proxy nothing is
fetched. "Usable" means configured, and not reported down or leaking by the last egress check
(below). There is never a fallback to a direct connection; a proxy that is down makes the fetch
fail.

No local DNS. Through the proxy, mailr never resolves the sender's host name: the proxy does
(an HTTPS fetch is a CONNECT host:443). A tracker can give every recipient a unique host name,
so a lookup through the server's resolver would tell the sender's DNS server the server's
network and the time of the fetch. `check_url` therefore checks such URLs without resolving
them (scheme, credentials, IP literals, local-only names). SSRF protection for these fetches
comes from two other places: a VPN egress container with its firewall on and no outbound LAN
subnets (gluetun: FIREWALL_OUTBOUND_SUBNETS empty) has no route to the LAN, so a name that
resolves to a private address goes nowhere; and the fetches are https-only, so the far end must
complete a TLS handshake with a certificate for that name before any request is sent, which a
plain-HTTP service on the proxy's own loopback cannot do. Without a proxy, the netguard checks
apply as before: resolve once, check every address, connect to a checked one.

The BIMI record (a DNS TXT lookup of the sender's domain) is read through the egress proxy as
DNS over HTTPS (`txt_records`), for the same reason.

The egress check (`run_check`, every CHECK_EVERY seconds from the worker, CHECK_RETRY while it
fails) asks a public IP-echo service through the proxy for the exit address. It fails when the
proxy cannot be reached, or when the exit address is the server's own public address
(MAILR_EGRESS_FORBIDDEN_IPS, or learned with one direct request to the same echo service and
kept for DIRECT_TTL). The result is stored in instance_settings (STATE_KEY) and shown in
/api/v1/health/ready as the optional `egress` check (no addresses: that endpoint needs no
sign-in), in /api/v1/health/egress for keyword monitors (200 or 503, no addresses), and to
admins with the exit address (Settings > Admin > Image proxy).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import time
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from mailr.config import get_settings
from mailr.core import netguard

log = logging.getLogger("mailr.egress")

# Plain-text IPv4 echo services, tried in order (both directions use the same family).
ECHO_URLS = ("https://api.ipify.org", "https://ipv4.icanhazip.com", "https://checkip.amazonaws.com")
# DNS over HTTPS (JSON API), tried in order, for the BIMI record through the egress proxy.
DOH_URLS = ("https://cloudflare-dns.com/dns-query", "https://dns.google/resolve")

CHECK_EVERY = 300
CHECK_RETRY = 60
CHECK_TIMEOUT = 10
DIRECT_TTL = 6 * 3600
# The worker's result counts for pausing fetches and for the health endpoints while it is
# younger than this (a stopped worker must not keep a verdict alive forever).
STATE_FRESH = 15 * 60
STATE_KEY = "egress_health"
STATE_CACHE_S = 20

# Host names that never leave the local network; refused without a lookup.
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".home.arpa", ".localdomain",
                   ".intranet", ".corp", ".private")


class EgressUnavailable(netguard.UnsafeTarget):
    """The fetch is not run: the egress proxy is required but not usable. The message is safe to
    show to users (it names no address)."""

    def __init__(self, state: str, message: str):
        super().__init__(message)
        self.state = state


def required() -> bool:
    return bool(get_settings().require_egress)


# ---------------------------------------------------------------- the gate


async def configured(db=None) -> str:
    """The egress proxy URL from the environment or the admin setting ("" = none)."""
    from mailr.core import imageproxy

    env = imageproxy.egress_from_env()
    if env or db is None:
        return env
    _, egress = await imageproxy.instance_config(db)
    return egress


async def gate(egress: str | None) -> str:
    """The egress proxy a content fetch uses ("" = direct), or EgressUnavailable."""
    egress = (egress or "").strip()
    if not required():
        return egress
    if not egress:
        raise EgressUnavailable("unconfigured", "Remote content is paused: no VPN egress proxy is configured.")
    st = await state()
    if st and _fresh(st) and st.get("state") in ("down", "leak"):
        if st["state"] == "leak":
            raise EgressUnavailable("leak", "Remote content is paused: the VPN egress is not hiding the "
                                            "server's address.")
        raise EgressUnavailable("down", "Remote content is paused: the VPN egress is not reachable.")
    return egress


def client(egress: str, *, public_only: bool = True, **kw: Any) -> httpx.AsyncClient:
    """The client for a content fetch. `egress` comes from gate(). public_only=False is for the
    tests' local servers only."""
    kw.setdefault("follow_redirects", False)
    if egress:
        # The proxy resolves names (no local DNS for the sender's host); see the module doc.
        return httpx.AsyncClient(proxy=egress, trust_env=False, **kw)
    if required():
        # The last line: whatever a caller did, no direct connection while the egress is required.
        raise EgressUnavailable("unconfigured", "Remote content is paused: no VPN egress proxy is configured.")
    return netguard.guarded_client(public_only=public_only, **kw)


def check_url_offline(url: str, *, allow_http: bool = False) -> None:
    """The checks for a URL fetched through the egress proxy, without resolving its host:
    https (http only for tests), no credentials, no IP literal outside the public internet, and
    no name that only exists on a local network or in mailr's own stack."""
    u = urlsplit((url or "").strip())
    if u.scheme != "https" and not (allow_http and u.scheme == "http"):
        raise netguard.UnsafeTarget("only https:// links from mail are followed")
    if u.username or u.password:
        raise netguard.UnsafeTarget("URLs with embedded credentials are not allowed")
    host = (u.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise netguard.UnsafeTarget("the URL has no host")
    try:
        _ = u.port
    except ValueError:
        raise netguard.UnsafeTarget("the URL has an invalid port") from None
    if netguard._is_ip(host):
        netguard._check_ip(host.split("%", 1)[0], host, public_only=True)
        return
    # Shortened and numeric IPv4 forms (127.1, 2130706433, 0x7f.1, 0177.0.0.1) are not
    # IP literals to _is_ip, but resolvers and proxies may still read them as addresses. No real
    # top-level domain is numeric or hexadecimal, so such a last label is refused outright.
    last = host.rsplit(".", 1)[-1]
    if re.fullmatch(r"[0-9]+|0x[0-9a-f]*", last):
        raise netguard.UnsafeTarget(f"{host} is a numeric address, not a public host name")
    # Remote content is served on the HTTPS port; another port through the proxy could only
    # reach a service that is not a web site.
    if u.scheme == "https" and u.port not in (None, 443):
        raise netguard.UnsafeTarget("only the standard HTTPS port is followed for links from mail")
    if host in netguard._infra_hosts():
        raise netguard.UnsafeTarget(f"{host} is part of Mailr's own infrastructure")
    if "." not in host or host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        raise netguard.UnsafeTarget(f"{host} is not a public host name")


async def check_url(url: str, egress: str, *, allow_http: bool = False) -> None:
    """One hop of a content fetch: offline checks through the proxy, netguard's resolve-and-check
    without one (https and public addresses only). Both routes take the standard HTTPS port only:
   another port on a public host is not a web page."""
    if egress:
        check_url_offline(url, allow_http=allow_http)
    elif allow_http and url.lower().startswith("http://"):
        await netguard.check_url(url, public_only=True)
    else:
        u = urlsplit((url or "").strip())
        try:
            port = u.port
        except ValueError:
            raise netguard.UnsafeTarget("the URL has an invalid port") from None
        if u.scheme == "https" and port not in (None, 443):
            raise netguard.UnsafeTarget("only the standard HTTPS port is followed for links from mail")
        await netguard.check_untrusted_url(url)


async def txt_records(name: str, egress: str, timeout: float = 3.0) -> list[str]:
    """TXT records of a name. Through the egress proxy as DNS over HTTPS (no local lookup),
    otherwise with the local resolver."""
    if not egress:
        import dns.asyncresolver

        try:
            ans = await dns.asyncresolver.resolve(name, "TXT", lifetime=timeout)
        except Exception:  # noqa: BLE001  NXDOMAIN, timeout, no answer
            return []
        return [b"".join(rr.strings).decode("utf-8", "replace") for rr in ans]
    async with client(egress, timeout=timeout, headers={"Accept": "application/dns-json"}) as c:
        for base in DOH_URLS:
            try:
                r, body = await netguard.fetch_capped(c, "GET", f"{base}?name={quote(name)}&type=TXT",
                                                      limit=64 * 1024, deadline=timeout * 2)
                if r.status_code != 200:
                    continue
                data = json.loads(body)
            except (httpx.HTTPError, netguard.TooLarge, TimeoutError, ValueError):
                continue
            out = []
            for a in data.get("Answer") or []:
                if a.get("type") == 16 and isinstance(a.get("data"), str):
                    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', a["data"])
                    out.append("".join(parts) if parts else a["data"])
            return out
    return []


# ---------------------------------------------------------------- state


_cache: tuple[float, dict[str, Any] | None] | None = None


def _fresh(st: dict[str, Any]) -> bool:
    return time.time() - float(st.get("at") or 0) < STATE_FRESH


async def state(use_cache: bool = True) -> dict[str, Any] | None:
    """The last egress check result (instance_settings), cached for STATE_CACHE_S."""
    global _cache
    now = time.monotonic()
    if use_cache and _cache is not None and now - _cache[0] < STATE_CACHE_S:
        return _cache[1]
    try:
        from mailr.db import session_scope
        from mailr.models import InstanceSetting

        async with session_scope() as db:
            row = await db.get(InstanceSetting, STATE_KEY)
            st = dict(row.value) if row is not None and isinstance(row.value, dict) else None
    except Exception as e:  # noqa: BLE001  no database: no verdict (fetches are not paused by it)
        log.debug("egress state unreadable: %s", type(e).__name__)
        st = None
    _cache = (time.monotonic(), st)
    return st


def clear_cache() -> None:
    global _cache
    _cache = None


async def _save(st: dict[str, Any]) -> None:
    from mailr.db import session_scope
    from mailr.models import InstanceSetting

    async with session_scope() as db:
        row = await db.get(InstanceSetting, STATE_KEY)
        if row is None:
            db.add(InstanceSetting(key=STATE_KEY, value=st))
        else:
            row.value = st
    clear_cache()


# ---------------------------------------------------------------- the check


def _forbidden_from_env() -> set[str]:
    out = set()
    for item in (get_settings().egress_forbidden_ips or "").split(","):
        item = item.strip()
        if item:
            try:
                out.add(str(ipaddress.ip_address(item)))
            except ValueError:
                log.warning("MAILR_EGRESS_FORBIDDEN_IPS holds a value that is not an address; ignored")
    return out


async def _echo(c: httpx.AsyncClient) -> str | None:
    for url in ECHO_URLS:
        try:
            r, body = await netguard.fetch_capped(c, "GET", url, limit=256, deadline=CHECK_TIMEOUT)
            if r.status_code != 200:
                continue
            return str(ipaddress.ip_address(body.decode("ascii", "replace").strip()))
        except (httpx.HTTPError, netguard.TooLarge, TimeoutError, ValueError):
            continue
    return None


async def exit_ip(egress: str) -> str | None:
    """The public address the echo service sees through the egress proxy, or None."""
    async with httpx.AsyncClient(proxy=egress, trust_env=False, timeout=CHECK_TIMEOUT,
                                 follow_redirects=False, headers={"User-Agent": "curl/8"}) as c:
        return await _echo(c)


async def direct_ip() -> str | None:
    """The server's own public address: one direct request to the echo service. It carries
    nothing from mail; it is how the check knows what a leak would look like."""
    async with netguard.guarded_client(public_only=True, timeout=CHECK_TIMEOUT,
                                       headers={"User-Agent": "curl/8"}) as c:
        return await _echo(c)


def evaluate(egress: str, exit_addr: str | None, forbidden: set[str], known: bool) -> dict[str, Any]:
    """The verdict: state ok | down | leak | unconfigured (no addresses in `detail`)."""
    if not egress:
        if required():
            return {"state": "unconfigured", "ok": False, "detail": "no egress configured: remote content paused"}
        return {"state": "off", "ok": True, "detail": "no egress configured (direct fetches)"}
    if exit_addr is None:
        return {"state": "down", "ok": False, "detail": "egress down: images paused" if required()
                else "egress down: image fetches fail"}
    if exit_addr in forbidden:
        return {"state": "leak", "ok": False, "detail": "LEAK: exit address is the server's own"}
    return {"state": "ok", "ok": True, "detail": "via egress proxy" + ("" if known else " (own address unknown)")}


async def run_check() -> dict[str, Any]:
    """One egress check; stores and returns the result (with addresses, for admins)."""
    prev = await state(use_cache=False) or {}
    egress = await _configured_from_db()
    exit_addr = None
    forbidden = _forbidden_from_env()
    direct = prev.get("direct_ip") if time.time() - float(prev.get("direct_at") or 0) < DIRECT_TTL else None
    direct_at = prev.get("direct_at") if direct else None
    if egress:
        try:
            exit_addr = await asyncio.wait_for(exit_ip(egress), timeout=CHECK_TIMEOUT * len(ECHO_URLS))
        except (TimeoutError, httpx.HTTPError, OSError, ValueError):
            exit_addr = None
        if not forbidden and direct is None:
            try:
                direct = await asyncio.wait_for(direct_ip(), timeout=CHECK_TIMEOUT * len(ECHO_URLS))
            except (TimeoutError, httpx.HTTPError, OSError, ValueError, netguard.UnsafeTarget):
                direct = None
            direct_at = time.time() if direct else None
    known = bool(forbidden) or bool(direct)
    st = evaluate(egress, exit_addr, forbidden | ({direct} if direct else set()), known)
    st.update({"at": time.time(), "exit_ip": exit_addr, "direct_ip": direct, "direct_at": direct_at,
               "required": required()})
    if st["state"] != prev.get("state"):
        (log.info if st["ok"] else log.warning)("egress check: %s", st["detail"])
    await _save(st)
    return st


async def _configured_from_db() -> str:
    from mailr.db import session_scope

    async with session_scope() as db:
        return await configured(db)


_last_run = -float("inf")
_last_ok = True
_task: asyncio.Task | None = None


def on_scheduler_tick() -> None:
    """From the worker's scheduler: start a check in the background when one is due."""
    global _last_run, _task
    now = time.monotonic()
    if _task is not None and not _task.done():
        return
    if now - _last_run < (CHECK_EVERY if _last_ok else CHECK_RETRY):
        return
    _last_run = now
    _task = asyncio.create_task(asyncio.wait_for(run_check(), timeout=120), name="egress-check")
    _task.add_done_callback(_done)


def _done(t: asyncio.Task) -> None:
    global _last_ok
    if t.cancelled():
        return
    if t.exception() is not None:
        _last_ok = False
        log.warning("egress check failed: %s", type(t.exception()).__name__)
    else:
        _last_ok = bool(t.result().get("ok"))


# ---------------------------------------------------------------- reporting


def public_view(st: dict[str, Any] | None, has_egress: bool) -> tuple[bool, str, str]:
    """(ok, state, detail) without addresses, for the endpoints that need no sign-in."""
    if not has_egress:
        if required():
            return False, "unconfigured", "no egress configured: remote content paused"
        return True, "off", "no egress configured (direct fetches)"
    if st is None or st.get("state") in ("off", "unconfigured"):
        return False, "unchecked", "egress not checked yet"
    if not _fresh(st):
        mins = int((time.time() - float(st.get("at") or 0)) // 60)
        return False, "stale", f"egress not checked for {mins} min"
    return bool(st.get("ok")), str(st.get("state")), str(st.get("detail"))


async def view(db=None) -> tuple[bool, str, str]:
    return public_view(await state(), bool(await configured(db) if db is not None else await _configured_from_db()))


def admin_view(st: dict[str, Any] | None, has_egress: bool) -> dict[str, Any]:
    """The check for admins, with the exit address."""
    ok, name, detail = public_view(st, has_egress)
    out: dict[str, Any] = {"ok": ok, "state": name, "detail": detail, "required": required(),
                           "checked_at": None, "exit_ip": None}
    if st:
        out["checked_at"] = st.get("at")
        out["exit_ip"] = st.get("exit_ip")
        if name == "ok" and st.get("exit_ip"):
            out["detail"] = f"via egress proxy, exit {st['exit_ip']}"
        elif name == "leak" and st.get("exit_ip"):
            out["detail"] = f"LEAK: exit {st['exit_ip']} is the server's own address"
    return out
