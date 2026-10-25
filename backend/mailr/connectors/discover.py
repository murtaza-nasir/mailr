"""Find IMAP/SMTP settings for an email address.

Order: known domain → MX host match → Thunderbird ISPDB → autoconfig.<domain> →
RFC 6186 SRV records. Every network step has a short timeout, and the whole lookup has a
deadline (DEADLINE): the steps add up, and the first outbound check after a start also waits for
the infrastructure names (core/netguard.py _infra_ips; 31 s where those names do not resolve, 13 Oct
2026). Discovery never fails hard: it returns what it found, or the usual imap./smtp. guess, and
the UI falls back to manual entry.
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Any

import dns.asyncresolver
import dns.exception
import httpx

from mailr.connectors.presets import PRESETS, preset_for_domain, preset_for_mx

log = logging.getLogger("mailr.discover")
TIMEOUT = 4.0
DEADLINE = 12.0


async def _mx(domain: str) -> list[str]:
    try:
        ans = await dns.asyncresolver.resolve(domain, "MX", lifetime=TIMEOUT)
        return [str(r.exchange).rstrip(".").lower() for r in sorted(ans, key=lambda r: r.preference)]
    except (dns.exception.DNSException, OSError):
        return []


async def _srv(name: str) -> tuple[str, int] | None:
    try:
        ans = await dns.asyncresolver.resolve(name, "SRV", lifetime=TIMEOUT)
        best = sorted(ans, key=lambda r: (r.priority, -r.weight))[0]
        target = str(best.target).rstrip(".")
        if not target:
            return None
        return target, int(best.port)
    except (dns.exception.DNSException, OSError, IndexError):
        return None


def _parse_autoconfig(xml: str, email: str) -> dict[str, Any] | None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    prov = root.find("emailProvider")
    if prov is None:
        return None
    local, _, domain = email.partition("@")

    def sub(v: str) -> str:
        return (v or "").replace("%EMAILADDRESS%", email).replace("%EMAILLOCALPART%", local) \
            .replace("%EMAILDOMAIN%", domain)

    def server(tag: str, want: str) -> dict[str, Any] | None:
        best = None
        for s in prov.findall(tag):
            if s.get("type") != want:
                continue
            sock = (s.findtext("socketType") or "").upper()
            tls = {"SSL": "ssl", "STARTTLS": "starttls"}.get(sock, "none")
            cand = {"host": sub(s.findtext("hostname") or ""), "port": int(s.findtext("port") or 0),
                    "tls": tls, "username": sub(s.findtext("username") or email)}
            if best is None or (best["tls"] == "none" and tls != "none") or (best["tls"] == "starttls" and tls == "ssl"):
                best = cand
        return best

    imap = server("incomingServer", "imap")
    smtp = server("outgoingServer", "smtp")
    if not imap:
        return None
    return {"imap": imap, "smtp": smtp}


MAX_DOC_BYTES = 512 * 1024


async def _fetch(url: str, _hops: int = 0, private_ok: bool | None = None) -> str | None:
    """GET an autoconfig document. Redirects are followed manually (max 3) so every hop is
    checked by the outbound guard (the connection is pinned to the checked address); the domain
    in the URL comes from user input. At most MAX_DOC_BYTES are read."""
    from urllib.parse import urljoin

    from mailr.core.netguard import TooLarge, UnsafeTarget, check_url, fetch_capped, guarded_client

    try:
        await check_url(url, private_ok=private_ok)
        async with guarded_client(private_ok=private_ok, timeout=TIMEOUT) as c:
            r, body = await fetch_capped(c, "GET", url, limit=MAX_DOC_BYTES, deadline=TIMEOUT * 2)
        if r.is_redirect and _hops < 3 and r.headers.get("location"):
            return await _fetch(urljoin(url, r.headers["location"]), _hops + 1, private_ok)
        text = body.decode("utf-8", "replace")
        if r.status_code == 200 and "<clientConfig" in text[:200_000]:
            return text[:200_000]
    except (httpx.HTTPError, UnsafeTarget, TooLarge, TimeoutError):
        return None
    return None


def _from_preset(pid: str, email: str, source: str) -> dict[str, Any]:
    p = PRESETS[pid]
    return {
        "source": source, "preset": pid, "kind": p.get("kind", "imap"),
        "supported": p.get("supported", True), "notes": p.get("notes", ""),
        "imap": {**p["imap"], "username": email}, "smtp": {**p["smtp"], "username": email},
        "tls_verify": p.get("tls_verify", True),
    }


def _guess(email: str, domain: str, mx: list[str]) -> dict[str, Any]:
    return {"source": None, "preset": None, "kind": "imap", "supported": True, "mx": mx[:3],
            "notes": "Couldn't find settings automatically. Enter them from your provider's help page.",
            "imap": {"host": f"imap.{domain}", "port": 993, "tls": "ssl", "username": email},
            "smtp": {"host": f"smtp.{domain}", "port": 465, "tls": "ssl", "username": email},
            "tls_verify": True}


async def discover(email: str, private_ok: bool | None = None) -> dict[str, Any]:
    email = email.strip().lower()
    if "@" not in email:
        return {"source": None, "error": "not an email address"}
    domain = email.split("@", 1)[1]
    try:
        return await asyncio.wait_for(_discover(email, domain, private_ok), DEADLINE)
    except TimeoutError:
        log.info("discovery for %s took longer than %.0f s; offering the usual guess", domain, DEADLINE)
        return _guess(email, domain, [])


async def _discover(email: str, domain: str, private_ok: bool | None) -> dict[str, Any]:
    pid = preset_for_domain(domain)
    if pid:
        return _from_preset(pid, email, "preset")

    mx = await _mx(domain)
    pid = preset_for_mx(mx)
    if pid:
        out = _from_preset(pid, email, "mx")
        out["mx"] = mx[:3]
        return out

    for url in (f"https://autoconfig.thunderbird.net/v1.1/{domain}",
                f"https://autoconfig.{domain}/mail/config-v1.1.xml?emailaddress={email}",
                f"https://{domain}/.well-known/autoconfig/mail/config-v1.1.xml"):
        xml = await _fetch(url, private_ok=private_ok)
        if xml:
            cfg = _parse_autoconfig(xml, email)
            if cfg:
                return {"source": "autoconfig", "preset": None, "kind": "imap", "supported": True,
                        "notes": "", "tls_verify": True, **cfg, "mx": mx[:3]}

    imaps, sub = await asyncio.gather(_srv(f"_imaps._tcp.{domain}"), _srv(f"_submission._tcp.{domain}"))
    if imaps:
        imap = {"host": imaps[0], "port": imaps[1], "tls": "ssl", "username": email}
        smtp = {"host": sub[0], "port": sub[1], "tls": "ssl" if sub[1] == 465 else "starttls",
                "username": email} if sub else None
        return {"source": "srv", "preset": None, "kind": "imap", "supported": True, "notes": "",
                "tls_verify": True, "imap": imap, "smtp": smtp, "mx": mx[:3]}

    return _guess(email, domain, mx)
