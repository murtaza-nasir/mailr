"""Outbound connection guard (SSRF protection).

Every host a user can point mailr at (IMAP/SMTP servers, CalDAV/CardDAV URLs, push endpoints,
autodiscovery, Speakr, model endpoints) is resolved and checked before mailr connects:

- always refused: loopback, link-local (incl. cloud metadata 169.254.169.254 and
  fd00:ec2::254), multicast, unspecified and reserved addresses, deprecated ranges (fec0::/10,
  192.88.99.0/24), and mailr's own infrastructure: the Stalwart store, Postgres, the compose
  services (MAILR_INTERNAL_HOSTS: egress, resolver, app, worker, ...), their addresses and the
  container's default gateways (the Docker host);
- every other non-global address (RFC 1918, ULA, CGNAT/Tailscale 100.64.0.0/10, benchmark and
  documentation ranges) is "private". Who may reach private addresses is set by
  MAILR_ALLOW_PRIVATE_HOSTS: "admins" (default: what an admin account sets up), "all" or
  "none". A self-hosted single-user install signs in as its admin, so its LAN CalDAV, vLLM and
  mail bridges keep working;
- `MAILR_OUTBOUND_ALLOW` (comma-separated hosts or CIDRs) exempts specific targets for every
  user, e.g. `127.0.0.1` for a Proton Bridge running beside mailr;
- URLs taken from mail content (image proxy, one-click unsubscribe, BIMI logos) are https-only
  and public-only (`is_global`), whatever the settings say.

Connections are pinned: `guarded_client()` (httpx) and `connect_pinned()` (sockets for IMAP and
SMTP) resolve the name once, check every address, and connect to a checked address while TLS
still verifies the original name (SNI and the Host header are unchanged). A name that
re-resolves between the check and the connect (DNS rebinding) therefore cannot reach anything
the check refused. The one exception is a fetch through the admin's egress proxy: the proxy
resolves the name, so those fetches are https-only (the certificate must match the name).

`read_capped()` reads a response body with a byte limit, so no fetch buffers an unbounded body.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

from mailr.config import get_settings

log = logging.getLogger(__name__)


class UnsafeTarget(ValueError):
    """The destination is not allowed. The message is safe to show to users."""


class TooLarge(ValueError):
    """A response body went past its byte limit."""


# Refused for everyone (beyond loopback/link-local/multicast/unspecified/reserved).
_ALWAYS_BLOCKED = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "192.88.99.0/24", "fec0::/10", "fd00:ec2::254/128", "100::/64",
)]
# Not global, though Python does not always call them private.
_PRIVATE_EXTRA = [ipaddress.ip_network(n) for n in ("100.64.0.0/10", "198.18.0.0/15")]
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


# ---------------- policy ----------------

def private_mode() -> str:
    """all | admins | none, from MAILR_ALLOW_PRIVATE_HOSTS (true/false accepted)."""
    v = str(get_settings().allow_private_hosts or "").strip().lower()
    if v in ("all", "true", "1", "yes", "on"):
        return "all"
    if v in ("none", "false", "0", "no", "off"):
        return "none"
    return "admins"


def private_ok_for(user: Any) -> bool:
    """May what this user sets up reach private (LAN) addresses?"""
    mode = private_mode()
    if mode == "all":
        return True
    if mode == "none" or user is None:
        return False
    return getattr(user, "role", None) == "admin" and not getattr(user, "disabled", False)


async def private_ok_for_user_id(db, user_id: int | None) -> bool:
    mode = private_mode()
    if mode != "admins":
        return mode == "all"
    if user_id is None:
        return False
    from mailr.models import User

    return private_ok_for(await db.get(User, user_id))


def _default_private_ok() -> bool:
    return private_mode() == "all"


@lru_cache
def _allow_list() -> tuple[set[str], list[ipaddress._BaseNetwork]]:
    hosts: set[str] = set()
    nets: list[ipaddress._BaseNetwork] = []
    for item in (get_settings().outbound_allow or "").split(","):
        item = item.strip().lower()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            hosts.add(item)
    return hosts, nets


@lru_cache
def _infra_hosts() -> set[str]:
    s = get_settings()
    out = set()
    for url in (s.stalwart_url, s.database_url.replace("postgresql+asyncpg", "postgresql")):
        h = urlsplit(url).hostname
        if h:
            out.add(h.lower())
    for name in (s.internal_hosts or "").split(","):
        name = name.strip().lower()
        if name:
            out.add(name)
    return out


def _gateways() -> set[str]:
    """The default gateways of this host or container (in a container: the Docker host)."""
    out: set[str] = set()
    try:
        with open("/proc/net/route") as f:
            next(f, None)
            for line in f:
                parts = line.split()
                if len(parts) > 3 and parts[1] == "00000000" and parts[2] != "00000000":
                    out.add(socket.inet_ntoa(int(parts[2], 16).to_bytes(4, "little")))
    except (OSError, ValueError):
        pass
    try:
        with open("/proc/net/ipv6_route") as f:
            for line in f:
                parts = line.split()
                if len(parts) > 4 and parts[0] == "0" * 32 and parts[1] == "00" and parts[4] != "0" * 32:
                    out.add(str(ipaddress.IPv6Address(bytes.fromhex(parts[4]))))
    except (OSError, ValueError):
        pass
    return out


_INFRA_CACHE: tuple[float, set[str]] | None = None
_INFRA_TTL = 60.0


_INFRA_REFRESHING = threading.Lock()
_INFRA_FIRST = threading.Lock()


def _infra_ips() -> set[str]:
    """Addresses of the infrastructure names and the default gateways. The first call looks them
    up; later calls get the cached set, and a set older than a minute is looked up again in a
    background thread while the old one is still used. A name that does not exist on this
    deployment can take seconds to fail (about 4 s each with Docker's DNS), and that wait must
    not fall on a request (the calendar panel waited 3 s on every read, 12 Oct 2026). The
    addresses are Mailr's own containers', which change only on a redeploy."""
    cache = _INFRA_CACHE
    if cache is None:
        # The first lookup after a start: concurrent callers wait for one lookup, not one each.
        with _INFRA_FIRST:
            if _INFRA_CACHE is None:
                return refresh_infra_cache()
            return _INFRA_CACHE[1]
    if time.monotonic() - cache[0] >= _INFRA_TTL and _INFRA_REFRESHING.acquire(blocking=False):
        def run() -> None:
            try:
                refresh_infra_cache()
            except Exception:
                log.warning("could not refresh the infrastructure addresses", exc_info=True)
            finally:
                _INFRA_REFRESHING.release()
        threading.Thread(target=run, name="netguard-infra", daemon=True).start()
    return cache[1]


def refresh_infra_cache() -> set[str]:
    """Look the infrastructure names up again and keep the result (blocking). The browser gate
    calls it in the background, so no connection waits for names that do not resolve on its
    networks (each such lookup can take seconds)."""
    global _INFRA_CACHE
    ips: set[str] = {h for h in _infra_hosts() if _is_ip(h)}
    names = [h for h in _infra_hosts() if not _is_ip(h)]

    def lookup(h: str) -> set[str]:
        try:
            return {info[4][0].split("%", 1)[0] for info in socket.getaddrinfo(h, None)}
        except OSError:
            return set()

    # The names are looked up at the same time: one that does not exist takes several seconds to
    # fail, and ten of them one after another took 31 s on a cold start (13 Oct 2026).
    if names:
        with ThreadPoolExecutor(max_workers=min(16, len(names)), thread_name_prefix="netguard-dns") as ex:
            for found in ex.map(lookup, names):
                ips |= found
    ips |= _gateways()
    _INFRA_CACHE = (time.monotonic(), ips)
    return ips


_HOST_CHARS = re.compile(r"[A-Za-z0-9.-]{1,253}")
_V6_CHARS = re.compile(r"[0-9A-Fa-f:.]{2,45}")


def plain_host(host: str) -> bool:
    """A host name made only of ASCII letters, digits, dots and hyphens (IDNs in their xn-- form), or
    an IPv6 literal's characters: nothing that a request line, a proxy or a Chrome switch could read
    as a separator."""
    h = host or ""
    if h.startswith("[") and h.endswith("]"):
        return bool(_V6_CHARS.fullmatch(h[1:-1]))
    return bool(_HOST_CHARS.fullmatch(h)) or (":" in h and bool(_V6_CHARS.fullmatch(h)))


def _is_ip(h: str) -> bool:
    try:
        ipaddress.ip_address(h.strip("[]").split("%", 1)[0])
        return True
    except ValueError:
        return False


# ---------------- address checks ----------------

def _inner_v4(addr: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """IPv4 carried inside IPv6 (mapped, 6to4, Teredo, NAT64)."""
    if addr.ipv4_mapped:
        return addr.ipv4_mapped
    if addr.sixtofour:
        return addr.sixtofour
    if addr.teredo:
        return addr.teredo[1]
    if addr in _NAT64:
        return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return None


def _is_public(addr: ipaddress._BaseAddress) -> bool:
    return addr.is_global and not any(addr in n for n in _PRIVATE_EXTRA)


def _check_ip(ip: str, host: str, public_only: bool = False, private_ok: bool | None = None) -> None:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    if isinstance(addr, ipaddress.IPv6Address):
        inner = _inner_v4(addr)
        if inner is not None:
            _check_ip(str(inner), host, public_only, private_ok)
            return
    if (addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified
            or addr.is_reserved or any(addr in n for n in _ALWAYS_BLOCKED)):
        raise UnsafeTarget(f"{host} resolves to a local or reserved address that Mailr does not connect to")
    if _is_public(addr):
        return
    # Not global: RFC 1918, ULA, CGNAT, benchmark and documentation ranges.
    if public_only:
        raise UnsafeTarget(f"{host} resolves to a private address; this server only connects to public hosts")
    if private_ok is None:
        private_ok = _default_private_ok()
    if not private_ok:
        raise UnsafeTarget(f"{host} resolves to a private address; this server only connects to public hosts "
                           "for this account (an administrator can allow it)")


def resolve_checked(host: str, port: int | None = None, *, public_only: bool = False,
                    private_ok: bool | None = None) -> list[str]:
    """Resolve once, check every address, and return the checked addresses (connect to these)."""
    host = (host or "").strip().lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host:
        raise UnsafeTarget("no host given")
    allowed_hosts, nets = _allow_list()
    host_allowed = host in allowed_hosts and not public_only
    if host in _infra_hosts() and not host_allowed:
        raise UnsafeTarget(f"{host} is part of Mailr's own infrastructure")
    try:
        infos = socket.getaddrinfo(host, port or 0, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError) as e:
        if host_allowed:  # listed by the admin: the connection itself will tell
            return [host]
        raise UnsafeTarget(f"cannot resolve {host}: {e}") from None
    ips: list[str] = []
    for info in infos:
        ip = str(info[4][0]).split("%", 1)[0]
        if ip in ips:
            continue
        if host_allowed or (not public_only and any(ipaddress.ip_address(ip) in n for n in nets)):
            ips.append(ip)
            continue
        if ip in _infra_ips():
            raise UnsafeTarget(f"{host} is part of Mailr's own infrastructure")
        _check_ip(ip, host, public_only, private_ok)
        ips.append(ip)
    if not ips:
        raise UnsafeTarget(f"cannot resolve {host}")
    return ips


def check_host_sync(host: str, port: int | None = None, public_only: bool = False,
                    private_ok: bool | None = None) -> None:
    resolve_checked(host, port, public_only=public_only, private_ok=private_ok)


async def check_host(host: str, port: int | None = None, public_only: bool = False,
                     private_ok: bool | None = None) -> None:
    await asyncio.to_thread(check_host_sync, host, port, public_only, private_ok)


def _split_url(url: str) -> tuple[str, int | None]:
    u = urlsplit((url or "").strip())
    if u.scheme not in ("http", "https"):
        raise UnsafeTarget("only http:// and https:// URLs are allowed")
    if u.username or u.password:
        raise UnsafeTarget("URLs with embedded credentials are not allowed")
    if not u.hostname:
        raise UnsafeTarget("the URL has no host")
    try:
        port = u.port
    except ValueError:
        raise UnsafeTarget("the URL has an invalid port") from None
    return u.hostname, port or (443 if u.scheme == "https" else 80)


def check_url_sync(url: str, public_only: bool = False, private_ok: bool | None = None) -> None:
    host, port = _split_url(url)
    check_host_sync(host, port, public_only, private_ok)


async def check_url(url: str, public_only: bool = False, private_ok: bool | None = None) -> None:
    host, port = _split_url(url)
    await check_host(host, port, public_only, private_ok)


async def check_untrusted_url(url: str) -> None:
    """A URL taken from mail content (unsubscribe, BIMI): https, public addresses only,
    whatever MAILR_ALLOW_PRIVATE_HOSTS says. Fetch it with follow_redirects=False."""
    if urlsplit((url or "").strip()).scheme != "https":
        raise UnsafeTarget("only https:// links from mail are followed")
    await check_url(url, public_only=True)


async def untrusted_url_addresses(url: str) -> tuple[str, list[str]]:
    """check_untrusted_url, returning the host and its checked addresses, for a client that
    connects by itself and must be pinned to them (the page-reading browser's
    --host-resolver-rules, websearch/readers.py)."""
    if urlsplit((url or "").strip()).scheme != "https":
        raise UnsafeTarget("only https:// links from mail are followed")
    host, port = _split_url(url)
    ips = await asyncio.to_thread(resolve_checked, host, port, public_only=True)
    return host.strip().lower().rstrip("."), ips


def is_private_target(host: str) -> bool:
    """True when every address of the host is private (not global), or the host is on the
    admin allow-list. For "plain http:// only on a private network" rules."""
    host = (host or "").strip().lower().rstrip(".")
    hosts, nets = _allow_list()
    if host in hosts:
        return True
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return False
    for info in infos:
        addr = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        if any(addr in n for n in nets):
            continue
        if isinstance(addr, ipaddress.IPv6Address) and (inner := _inner_v4(addr)) is not None:
            addr = inner
        if _is_public(addr):
            return False
    return bool(infos)


def same_origin(url: str, base: str) -> bool:
    """Exact scheme/host/port match (never a string prefix: 'https://a.com.evil.net' must not
    match 'https://a.com')."""
    a, b = urlsplit(url), urlsplit(base)

    def port(u):
        try:
            return u.port or (443 if u.scheme == "https" else 80)
        except ValueError:
            return -1
    return (a.scheme, (a.hostname or "").lower(), port(a)) == (b.scheme, (b.hostname or "").lower(), port(b))


# ---------------- pinned connections ----------------

class _PinnedBackend(httpcore.AsyncNetworkBackend):
    """httpcore network backend that connects only to checked addresses. httpcore passes the
    URL's host here and uses it again for SNI and the certificate check, so pinning the address
    does not weaken TLS."""

    def __init__(self, public_only: bool, private_ok: bool | None,
                 resolve: Callable[[str, int], list[str]] | None = None):
        self.public_only = public_only
        self.private_ok = private_ok
        self.resolve = resolve
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            if self.resolve is not None:
                ips = await asyncio.to_thread(self.resolve, host, port)
            else:
                ips = await asyncio.to_thread(resolve_checked, host, port, public_only=self.public_only,
                                              private_ok=self.private_ok)
        except UnsafeTarget as e:
            raise httpcore.ConnectError(str(e)) from None
        last: Exception | None = None
        for ip in ips:
            try:
                return await self._inner.connect_tcp(ip, port, timeout=timeout, local_address=local_address,
                                                     socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                last = e
        raise last or httpcore.ConnectError(f"cannot connect to {host}")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):  # pragma: no cover
        raise httpcore.ConnectError("unix sockets are not used for outbound fetches")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def pinned_transport(*, public_only: bool = False, private_ok: bool | None = None,
                     verify: Any = True, resolve: Callable[[str, int], list[str]] | None = None
                     ) -> httpx.AsyncHTTPTransport:
    """A transport that connects only to checked addresses. `resolve(host, port)` replaces the
    usual check with a caller's own (it returns the addresses to connect to, or raises UnsafeTarget)."""
    t = httpx.AsyncHTTPTransport(verify=verify, trust_env=False)
    pool = t._pool
    if not hasattr(pool, "_network_backend"):  # pragma: no cover - a test pins this
        raise RuntimeError("httpcore changed: outbound connections cannot be pinned")
    pool._network_backend = _PinnedBackend(public_only, private_ok, resolve)
    return t


def guarded_client(*, public_only: bool = False, private_ok: bool | None = None, proxy: str | None = None,
                   verify: Any = True, **kw: Any) -> httpx.AsyncClient:
    """An httpx client whose connections go only to checked addresses (see the module doc).
    Redirects are not followed automatically: callers check each hop. With an egress proxy
    the proxy resolves names, so such fetches must be https."""
    kw.setdefault("follow_redirects", False)
    if proxy:
        return httpx.AsyncClient(proxy=proxy, trust_env=False, verify=verify, **kw)
    return httpx.AsyncClient(transport=pinned_transport(public_only=public_only, private_ok=private_ok,
                                                        verify=verify), trust_env=False, **kw)


def connect_pinned(host: str, port: int, timeout: float | None = None, *, public_only: bool = False,
                   private_ok: bool | None = None) -> socket.socket:
    """A TCP socket to a checked address of host (blocking; for IMAP and SMTP). The caller wraps
    TLS with server_hostname=host."""
    last: OSError | None = None
    for ip in resolve_checked(host, port, public_only=public_only, private_ok=private_ok):
        try:
            return socket.create_connection((ip, port), timeout=timeout)
        except OSError as e:
            last = e
    raise last or OSError(f"cannot connect to {host}")


async def read_capped(resp: httpx.Response, limit: int) -> bytes:
    """The body of a streamed response, refusing more than limit bytes (TooLarge)."""
    try:
        declared = int(resp.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > limit:
        raise TooLarge(f"the response is larger than {limit} bytes")
    buf = bytearray()
    async for chunk in resp.aiter_bytes():
        buf += chunk
        if len(buf) > limit:
            raise TooLarge(f"the response is larger than {limit} bytes")
    return bytes(buf)


async def send_capped(client: httpx.AsyncClient, request: httpx.Request, limit: int, **kw: Any) -> httpx.Response:
    """client.send with the body read under a byte limit (TooLarge). The result is an ordinary,
    fully read response (the encoding headers that described the wire form are dropped)."""
    resp = await client.send(request, stream=True, **kw)
    try:
        data = await read_capped(resp, limit)
    finally:
        await resp.aclose()
    hdrs = [(k, v) for k, v in resp.headers.multi_items()
            if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
    return httpx.Response(resp.status_code, headers=hdrs, content=data, request=request,
                          extensions={"http_version": resp.extensions.get("http_version", b"HTTP/1.1")})


async def fetch_capped(client: httpx.AsyncClient, method: str, url: str, *, limit: int,
                       deadline: float = 30.0, **kw: Any) -> tuple[httpx.Response, bytes]:
    """One request with a streamed, capped body and an overall deadline (TimeoutError). The
    response is closed when this returns; its body is the bytes returned."""
    async with asyncio.timeout(deadline):
        async with client.stream(method, url, **kw) as r:
            body = b"" if r.is_redirect else await read_capped(r, limit)
            return r, body
