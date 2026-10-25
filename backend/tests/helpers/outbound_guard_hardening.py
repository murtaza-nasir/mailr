"""Shared helpers, from test_outbound_guard_hardening.py."""

from __future__ import annotations

import socket

import pytest

from mailr.core import netguard

PUBLIC = "93.184.216.34"


# A fake resolver: names in the table answer from it; IP literals and the numeric IPv4 forms are
# parsed locally (AI_NUMERICHOST, no lookup); every other name fails.
NAMES = {
    "public.example.org": [PUBLIC],
    "www.example.org": [PUBLIC],
    "lan.example.org": ["192.168.1.10"],
    "mixed.example.org": [PUBLIC, "10.0.0.7"],
    "mapped.example.org": ["::ffff:127.0.0.1"],
    "cgnat.example.org": ["100.100.100.100"],
    "meta.example.org": ["169.254.169.254"],
    "ula.example.org": ["fd00:ec2::254"],
    "localhost": ["127.0.0.1", "::1"],
}


_REAL_GETADDRINFO = socket.getaddrinfo


def _resolver(table: dict[str, list[str]]):
    real = _REAL_GETADDRINFO

    def fake(host, port, *args, **kw):
        h = (host.decode() if isinstance(host, bytes) else host or "").lower().rstrip(".")
        if h in table:
            out = []
            for ip in table[h]:
                fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
                out.append((fam, socket.SOCK_STREAM, 6, "", (ip, port or 0)))
            return out
        try:
            return real(h, port, proto=socket.IPPROTO_TCP, flags=socket.AI_NUMERICHOST)
        except socket.gaierror:
            raise socket.gaierror(socket.EAI_NONAME, f"{h}: not in the test resolver") from None

    return fake


@pytest.fixture
def dns(monkeypatch):
    """The fake resolver, and production's allow-list (the test env opens loopback for its local servers)."""
    from mailr.config import get_settings

    table = dict(NAMES)
    monkeypatch.setattr(socket, "getaddrinfo", _resolver(table))
    monkeypatch.setattr(get_settings(), "outbound_allow", "")
    netguard._allow_list.cache_clear()
    netguard._INFRA_CACHE = None
    yield table
    netguard._allow_list.cache_clear()
    netguard._INFRA_CACHE = None
