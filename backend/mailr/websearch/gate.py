"""The browser gate: the only way out of the stack's page-reading browser (run as `mailr browser-gate`, the compose service `browser-gate`).

The browser (browserless, compose service `browser`) is on one network only, `browser-net`. That
network is `internal` (Docker gives it no route out) and holds nothing but the browser and this gate.
So the browser cannot reach the LAN, the Docker host, the app, Postgres, Stalwart or the egress proxy
by itself. websearch/readers.py launches Chrome with --proxy-server pointing here, without the
implicit loopback bypass, and with WebRTC limited to proxied traffic. Every connection a page makes
(the page, its redirects, sub-resources, WebSockets, service-worker fetches) arrives here as an
HTTP CONNECT, and the gate decides with netguard's rules:

- DIRECT_PORT, the route "Directly from this network": the host gets the offline check
  (egress.check_url_offline: no IP literal outside the public internet, no numeric, local or stack
  name), then it is resolved once. Every address must be public (netguard.resolve_checked,
  public_only), and the gate connects to a checked address. A name that rebinds after the check
  (DNS rebinding) reaches nothing the check refused.
- EGRESS_PORT, the route "Through the egress proxy": the same offline check, then the CONNECT goes on
  to the egress proxy (MAILR_BROWSER_GATE_UPSTREAM) with the proxy's sign-in added here. The browser
  never holds that password, so no web site's 401 can collect it. The VPN container resolves the
  name; its DNS drops private answers. Without an upstream the port refuses everything: there is
  no fallback to the direct route.
- Only CONNECT to port 443 (https and wss). Plain http, other ports and other methods get 403.
- RELAY_PORT: mailr's own control connection to browserless (POST /content, /function), relayed
  byte for byte to MAILR_BROWSER_GATE_TARGET. The gate carries the network alias `browser` on the
  app's network, so MAILR_BROWSER_URL stays http://browser:3000. The browser can reach this port
  too; it only leads back to the browser itself.

The gate holds no database access and no secret besides the egress proxy's password. Its own
container has the app's least-privilege settings (compose).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from dataclasses import dataclass
from functools import partial
from urllib.parse import unquote, urlsplit

from mailr.core import egress, netguard

log = logging.getLogger("mailr.browser_gate")

DIRECT_PORT = 3128
EGRESS_PORT = 3129
RELAY_PORT = 3000
HEAD_LIMIT = 8192
HEAD_TIMEOUT = 10.0
CONNECT_TIMEOUT = 10.0
IDLE_TIMEOUT = 90.0
TUNNEL_LIFETIME = 300.0
RELAY_IDLE_TIMEOUT = 150.0  # browserless renders up to a minute before it answers
RELAY_LIFETIME = 600.0
MAX_TUNNELS = 256
# How the gate opens its outgoing connections (a test replaces it).
_connect = asyncio.open_connection
_REASONS = {400: "Bad Request", 403: "Forbidden", 405: "Method Not Allowed", 431: "Request Header Fields Too Large",
            502: "Bad Gateway", 503: "Service Unavailable", 504: "Gateway Timeout"}


class Refused(Exception):
    """The gate answers this status and closes the connection. `why` is for the log only."""

    def __init__(self, status: int, why: str):
        super().__init__(why)
        self.status = status
        self.why = why


@dataclass(frozen=True)
class Upstream:
    host: str
    port: int
    username: str = ""
    password: str = ""

    def authorization(self) -> str:
        if not self.username and not self.password:
            return ""
        token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return f"Proxy-Authorization: Basic {token}\r\n"


def parse_upstream(raw: str) -> Upstream | None:
    """The egress proxy the egress port forwards to (an http:// proxy URL), or None."""
    raw = (raw or "").strip()
    if not raw:
        return None
    u = urlsplit(raw if "://" in raw else "http://" + raw)
    if u.scheme != "http" or not u.hostname:
        raise ValueError("MAILR_BROWSER_GATE_UPSTREAM must be an http:// proxy address")
    return Upstream(u.hostname, u.port or 80, unquote(u.username or ""), unquote(u.password or ""))


def parse_target(raw: str) -> tuple[str, int]:
    u = urlsplit("//" + (raw or "").strip().removeprefix("http://"))
    if not u.hostname:
        raise ValueError("MAILR_BROWSER_GATE_TARGET must be host:port")
    return u.hostname, u.port or 3000


# ---------------------------------------------------------------- the decision


def split_authority(target: str) -> tuple[str, int]:
    """host and port of a CONNECT target (host:port or [v6]:port)."""
    if target.startswith("["):
        end = target.find("]")
        if end < 0 or target[end + 1:end + 2] != ":":
            raise Refused(400, "malformed target")
        host, port_s = target[1:end], target[end + 2:]
    else:
        host, sep, port_s = target.rpartition(":")
        if not sep or ":" in host:
            raise Refused(400, "malformed target")
    # ASCII digits only ("44³" passes str.isdigit and then breaks int()), and a host of plain
    # characters only: the raw target goes upstream as it is.
    if not (port_s.isascii() and port_s.isdigit() and 1 <= len(port_s) <= 5) or not host \
            or not netguard.plain_host(host):
        raise Refused(400, "malformed target")
    return host.lower().rstrip("."), int(port_s)


def parse_request(head: bytes) -> tuple[str, int]:
    """The CONNECT target of a request head, or Refused."""
    line = head.split(b"\r\n", 1)[0].decode("latin-1")
    parts = line.split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        raise Refused(400, "not an HTTP/1 request")
    method, target, _ = parts
    if method != "CONNECT":
        host = urlsplit(target).hostname or "?"
        raise Refused(403, f"{method[:16]} http://{host[:80]} is not allowed (only CONNECT to port 443)")
    host, port = split_authority(target)
    if port != 443:
        raise Refused(403, f"port {port} is not allowed")
    return host, port


def check_host(host: str) -> None:
    """The offline rules for a page address (egress.check_url_offline), for every route."""
    shown = f"[{host}]" if ":" in host else host
    try:
        egress.check_url_offline(f"https://{shown}/")
    except netguard.UnsafeTarget as e:
        raise Refused(403, str(e)) from None


# ---------------------------------------------------------------- connections


async def _read_head(reader: asyncio.StreamReader) -> bytes:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT)
    except asyncio.LimitOverrunError:
        raise Refused(431, "request head too large") from None
    except asyncio.IncompleteReadError:
        raise Refused(400, "connection closed before the request head") from None
    except TimeoutError:
        raise Refused(400, "no request head in time") from None
    if len(head) > HEAD_LIMIT:
        raise Refused(431, "request head too large")
    return head


async def open_direct(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """A connection to a checked public address of host (resolved once, pinned)."""
    try:
        ips = await asyncio.to_thread(netguard.resolve_checked, host, port, public_only=True)
    except netguard.UnsafeTarget as e:
        raise Refused(403, str(e)) from None
    for ip in ips:
        try:
            return await asyncio.wait_for(_connect(ip, port), CONNECT_TIMEOUT)
        except (OSError, TimeoutError):
            continue
    raise Refused(502, f"cannot connect to {host}")


async def open_egress(host: str, port: int, upstream: Upstream | None) -> tuple[asyncio.StreamReader,
                                                                                asyncio.StreamWriter]:
    """A tunnel to host:port through the egress proxy, signed in here."""
    if upstream is None:
        raise Refused(502, "no egress proxy is set for the gate (MAILR_BROWSER_GATE_UPSTREAM)")
    try:
        r, w = await asyncio.wait_for(_connect(upstream.host, upstream.port), CONNECT_TIMEOUT)
    except (OSError, TimeoutError):
        raise Refused(502, "the egress proxy cannot be reached") from None
    target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    w.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n{upstream.authorization()}\r\n".encode("latin-1"))
    try:
        await w.drain()
        head = await _read_head(r)
    except (Refused, OSError):
        _close(w)
        raise Refused(502, "the egress proxy did not answer") from None
    status = head.split(b"\r\n", 1)[0].split(b" ")
    if len(status) < 2 or status[1] != b"200":
        _close(w)
        code = status[1].decode("latin-1", "replace")[:3] if len(status) > 1 else "?"
        raise Refused(502, f"the egress proxy answered {code}")
    return r, w


def _close(w: asyncio.StreamWriter) -> None:
    with contextlib.suppress(Exception):
        w.close()


async def _pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter, idle: float) -> None:
    try:
        while True:
            data = await asyncio.wait_for(src.read(65536), idle)
            if not data:
                return
            dst.write(data)
            await dst.drain()
    except (OSError, TimeoutError, asyncio.IncompleteReadError):
        return


async def splice(a: tuple[asyncio.StreamReader, asyncio.StreamWriter],
                 b: tuple[asyncio.StreamReader, asyncio.StreamWriter], *, idle: float, lifetime: float) -> None:
    """Bytes both ways until either side closes, goes idle, or the lifetime ends."""
    tasks = [asyncio.create_task(_pump(a[0], b[1], idle)), asyncio.create_task(_pump(b[0], a[1], idle))]
    try:
        await asyncio.wait(tasks, timeout=lifetime, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        _close(a[1])
        _close(b[1])
        await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------- the servers


class Gate:
    def __init__(self, upstream: Upstream | None, target: tuple[str, int]):
        self.upstream = upstream
        self.target = target
        self.slots = asyncio.Semaphore(MAX_TUNNELS)

    async def tunnel(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, route: str) -> None:
        """One browser connection on the direct or the egress port."""
        if self.slots.locked():
            await _refuse(writer, Refused(503, "too many connections"), route)
            return
        async with self.slots:
            host = "?"
            try:
                host, port = parse_request(await _read_head(reader))
                check_host(host)
                if route == "egress":
                    up = await open_egress(host, port, self.upstream)
                else:
                    up = await open_direct(host, port)
            except Refused as e:
                await _refuse(writer, e, route, host)
                return
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await splice((reader, writer), up, idle=IDLE_TIMEOUT, lifetime=TUNNEL_LIFETIME)

    async def relay(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """mailr's control connection, relayed to browserless as it is."""
        try:
            up = await asyncio.wait_for(_connect(*self.target), CONNECT_TIMEOUT)
        except (OSError, TimeoutError):
            log.warning("browser gate: browserless at %s:%d cannot be reached", *self.target)
            _close(writer)
            return
        await splice((reader, writer), up, idle=RELAY_IDLE_TIMEOUT, lifetime=RELAY_LIFETIME)


async def _refuse(writer: asyncio.StreamWriter, e: Refused, route: str, host: str = "?") -> None:
    log.info("browser gate (%s): refused %s: %s", route, host, e.why)
    with contextlib.suppress(Exception):
        writer.write(f"HTTP/1.1 {e.status} {_REASONS.get(e.status, 'Refused')}\r\nContent-Length: 0\r\n"
                     "Connection: close\r\n\r\n".encode())
        await writer.drain()
    _close(writer)


INFRA_REFRESH = 20.0  # seconds between lookups of the stack's names (netguard keeps them 60 s)


async def _keep_infra_fresh() -> None:
    while True:
        await asyncio.sleep(INFRA_REFRESH)
        try:
            await asyncio.to_thread(netguard.refresh_infra_cache)
        except Exception as e:  # noqa: BLE001  the next round tries again; netguard looks up itself
            log.warning("browser gate: the stack's names could not be looked up: %s", type(e).__name__)


async def serve(host: str = "0.0.0.0", ports: tuple[int, int, int] = (DIRECT_PORT, EGRESS_PORT, RELAY_PORT),
                *, upstream: Upstream | None = None, target: tuple[str, int] = ("chrome", 3000),
                ready: asyncio.Event | None = None) -> None:
    gate = Gate(upstream, target)
    # The stack's own names are looked up before the first connection and then in the background:
    # names that do not exist on the gate's networks can take seconds each to fail.
    await asyncio.to_thread(netguard.refresh_infra_cache)
    keeper = asyncio.create_task(_keep_infra_fresh())
    servers = [
        await asyncio.start_server(partial(gate.tunnel, route="direct"), host, ports[0]),
        await asyncio.start_server(partial(gate.tunnel, route="egress"), host, ports[1]),
        await asyncio.start_server(gate.relay, host, ports[2]),
    ]
    log.info("browser gate: direct :%d, egress :%d (%s), control :%d to %s:%d", ports[0], ports[1],
             "through the egress proxy" if upstream else "no egress proxy: refused", ports[2], *target)
    if ready is not None:
        ready.set()
    try:
        await asyncio.gather(*(s.serve_forever() for s in servers))
    finally:
        keeper.cancel()
        for s in servers:
            s.close()


def run() -> None:
    """`mailr browser-gate`."""
    from mailr.config import get_settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    asyncio.run(serve(upstream=parse_upstream(s.browser_gate_upstream), target=parse_target(s.browser_gate_target)))
