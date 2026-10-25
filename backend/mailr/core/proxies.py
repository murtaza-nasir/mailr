"""Which peers may set X-Forwarded-For and -Proto.

MAILR_TRUSTED_PROXIES, when set, is the list. When it is empty, the list is loopback plus the
gateway of the container's own network: a reverse proxy on the Docker host reaches the app from
that gateway (the published port goes through it), so without it every request seemed to come
from one address, and the whole household shared one sign-in throttle and one IP in the
session list and the audit log.

The gateway is the first address of MAILR_NET_SUBNET (the compose file passes the stack
network's subnet to the app), else, inside a container, the default route's gateway from
/proc/net/route. Outside a container (a development checkout) only loopback is trusted.

`ForwardedWatch` logs once per process when a request carries X-Forwarded-For from a peer that
is not trusted, and remembers it for Admin → Diagnostics (core/health.check_proxy).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import struct
import time
from pathlib import Path

from mailr.config import get_settings

log = logging.getLogger("mailr.proxies")

LOOPBACK = ("127.0.0.1", "::1")


def _gateway_from_subnet(subnet: str) -> str | None:
    try:
        net = ipaddress.ip_network(subnet.strip(), strict=False)
    except ValueError:
        return None
    if net.num_addresses < 4:
        return None
    return str(net.network_address + 1)


def _in_container() -> bool:
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    try:
        return any(x in Path("/proc/1/cgroup").read_text() for x in ("docker", "containerd", "kubepods", "libpod"))
    except OSError:
        return False


def _default_gateway(route_file: str = "/proc/net/route") -> str | None:
    """The default route's gateway (IPv4), or None."""
    try:
        lines = Path(route_file).read_text().splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        f = line.split()
        if len(f) >= 3 and f[1] == "00000000" and f[2] != "00000000":
            try:
                return socket.inet_ntoa(struct.pack("<L", int(f[2], 16)))
            except (ValueError, struct.error):
                return None
    return None


def derived(subnet: str | None = None, in_container: bool | None = None, route_file: str = "/proc/net/route") -> list[str]:
    """The default list when MAILR_TRUSTED_PROXIES is empty."""
    out = list(LOOPBACK)
    subnet = get_settings().net_subnet if subnet is None else subnet
    gw = _gateway_from_subnet(subnet) if subnet else None
    if gw is None and (_in_container() if in_container is None else in_container):
        gw = _default_gateway(route_file)
    if gw and gw not in out:
        out.append(gw)
    return out


def trusted() -> list[str]:
    raw = (get_settings().trusted_proxies or "").strip()
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    return derived()


def source() -> str:
    """'set' when MAILR_TRUSTED_PROXIES is set, else 'derived'."""
    return "set" if (get_settings().trusted_proxies or "").strip() else "derived"


def forwarded_allow_ips() -> str:
    """The value for uvicorn's forwarded_allow_ips."""
    return ",".join(trusted())


# ------------------------------------------------- untrusted forwarded headers

_untrusted_seen: float | None = None


def untrusted_seen() -> float | None:
    """When (time.time) a forwarded header last came from an untrusted peer in this process."""
    return _untrusted_seen


def note_untrusted() -> None:
    global _untrusted_seen
    first = _untrusted_seen is None
    _untrusted_seen = time.time()
    if first:
        log.warning("a request carried X-Forwarded-For from a peer that is not in MAILR_TRUSTED_PROXIES; "
                     "the header was ignored. If a reverse proxy sits in front of mailr, add its address "
                     "(logged once per process).")


def reset() -> None:
    global _untrusted_seen
    _untrusted_seen = None


class ForwardedWatch:
    """ASGI middleware. uvicorn's proxy-headers layer runs before the app: from a trusted peer
    it replaces the client address with one taken from X-Forwarded-For, so the client then
    appears in that header. A client address missing from the header means the peer was not
    trusted and the header was ignored."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and (_untrusted_seen is None or time.time() - _untrusted_seen > 60):
            xff = None
            for k, v in scope.get("headers") or []:
                if k == b"x-forwarded-for":
                    xff = v.decode("latin-1")
                    break
            client = (scope.get("client") or (None,))[0]
            if xff and client and client not in {x.strip() for x in xff.split(",")}:
                note_untrusted()
        await self.app(scope, receive, send)


def describe() -> str:
    """One plain line for Admin → Diagnostics (addresses are fine there: admins only)."""
    return ", ".join(trusted())

