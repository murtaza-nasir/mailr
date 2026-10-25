"""Admin → Calendars → "Phone access check": is the public DAV address right?

It asks the public address (calendars.phone_url) what a phone and an attacker would ask, without
signing in, and checks the answers against the gateway's rules (deploy/dav-gateway, docs
site/docs/admin/calendars.md):

- /.well-known/caldav and /.well-known/carddav redirect to /dav/cal/ and /dav/card/;
- /dav/cal/ asks for a password (401, Basic);
- the ACL method, the schedule outbox (/dav/itip/…/outbox/) and WebDAV files (/dav/file/) are refused
  before they reach the mail server (403, 404 or 405, never 401);
- the mail server's other paths (/jmap, /.well-known/oauth-*, /admin, /api) do not answer.

The fetches follow the egress rules of remote content (core/egress.py): through the VPN egress
proxy when one is configured, never directly while MAILR_REQUIRE_EGRESS is on, and only to a
public https address. When the rules forbid the fetch (no usable egress, or the name resolves to a
private address here), the check does not run; the answer says why and gives curl commands to run
from outside instead. No credentials are sent.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import egress, netguard

log = logging.getLogger("mailr.calendar.phone_check")

TIMEOUT = 10.0
BLOCKED_PATHS = ("/jmap/session", "/.well-known/jmap", "/.well-known/oauth-protected-resource",
                 "/.well-known/oauth-authorization-server", "/admin/", "/api/")
REFUSED = (403, 404, 405)


def commands(url: str) -> list[str]:
    """The same checks as curl commands, to run from a network outside the server's."""
    return [
        f"curl -s -o /dev/null -w '%{{http_code}} %{{redirect_url}}\\n' {url}/.well-known/caldav    # 301 or 307, then /dav/cal",
        f"curl -s -o /dev/null -w '%{{http_code}}\\n' -X PROPFIND {url}/dav/cal/    # 401",
        f"curl -s -o /dev/null -w '%{{http_code}}\\n' -X ACL {url}/dav/cal/x/    # 405 (never 401)",
        f"curl -s -o /dev/null -w '%{{http_code}}\\n' -X POST {url}/dav/itip/x/outbox/    # 403 (never 401)",
        f"curl -s -o /dev/null -w '%{{http_code}}\\n' {url}/jmap/session    # 404",
    ]


def _not_run(url: str, reason: str) -> dict[str, Any]:
    return {"ran": False, "ok": False, "url": url, "reason": reason, "checks": [], "commands": commands(url)}


def _check(cid: str, label: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"id": cid, "label": label, "ok": ok, "detail": detail}


def _redirect_ok(resp: httpx.Response, url: str, want: str) -> tuple[bool, str]:
    if resp.status_code not in (301, 302, 307, 308):
        return False, f"answered {resp.status_code}, not a redirect to {want}"
    loc = resp.headers.get("location", "")
    u = urlsplit(loc)
    if u.netloc and u.netloc.lower() != urlsplit(url).netloc.lower():
        return False, f"redirects to another host: {loc}"
    if not (u.path or "").rstrip("/").endswith(want.rstrip("/")):
        return False, f"redirects to {loc}, not {want}"
    return True, f"redirects to {u.path}"


def _stalwart_like(resp: httpx.Response) -> bool:
    """Did this answer come from the mail server (not from the proxy or mailr)?"""
    if resp.status_code == 401:
        return True
    ctype = resp.headers.get("content-type", "").lower()
    return "json" in ctype or resp.status_code in (301, 302, 307, 308) and "/admin" in resp.headers.get("location", "")


def evaluate(url: str, answers: dict[str, httpx.Response]) -> list[dict[str, Any]]:
    """The verdicts, from the answers by key (pure, for the tests)."""
    out = []
    for kind, want in (("caldav", "/dav/cal"), ("carddav", "/dav/card")):
        ok, detail = _redirect_ok(answers[f"wk-{kind}"], url, want)
        out.append(_check(f"wellknown_{kind}", f"/.well-known/{kind} leads to the calendars" if kind == "caldav"
                          else "/.well-known/carddav leads to the contacts", ok, detail))
    r = answers["dav"]
    challenge = r.headers.get("www-authenticate", "")
    if r.status_code == 401 and "basic" in challenge.lower():
        detail = "asks for a password"
        if "bearer" in challenge.lower():
            detail += "; it also offers Bearer sign-in, which some apps then try (the gateway removes it)"
        out.append(_check("dav_auth", "/dav/ asks for a password", True, detail))
    else:
        out.append(_check("dav_auth", "/dav/ asks for a password", False,
                          f"answered {r.status_code}{' without a Basic challenge' if r.status_code == 401 else ''}, not 401"))
    for key, label in (("acl", "The ACL method is refused"), ("outbox", "The schedule outbox is refused"),
                       ("file", "WebDAV file storage is refused")):
        r = answers[key]
        ok = r.status_code in REFUSED
        out.append(_check(f"{key}_blocked", label, ok, f"answered {r.status_code}" + (
            "" if ok else ": it reached the mail server" if r.status_code == 401 else ", not 403, 404 or 405")))
    leaks = [f"{p} ({answers[p].status_code})" for p in BLOCKED_PATHS if p in answers and _stalwart_like(answers[p])]
    out.append(_check("other_paths_blocked", "The mail server's other paths do not answer", not leaks,
                      "none answered" if not leaks else "answered: " + ", ".join(leaks)))
    return out


def _blocked_paths(url: str) -> tuple[str, ...]:
    """On mailr's own host (not the recommended setup), /api/ is mailr's API: not asked."""
    from mailr.config import get_settings

    base = urlsplit(get_settings().base_url or "").netloc.lower()
    return tuple(p for p in BLOCKED_PATHS if not (p == "/api/" and base and base == urlsplit(url).netloc.lower()))


async def _precheck(url: str, proxy: str) -> None:
    await egress.check_url(url, proxy)


def _client(proxy: str) -> httpx.AsyncClient:
    return egress.client(proxy, timeout=TIMEOUT, follow_redirects=False)


async def run(db: AsyncSession, url: str) -> dict[str, Any]:
    try:
        proxy = await egress.gate(await egress.configured(db))
    except egress.EgressUnavailable as e:
        return _not_run(url, f"{e} The check reaches the public address the same way Mailr fetches remote "
                             "content, so it cannot run now. Run these commands from outside your network instead.")
    try:
        await _precheck(url + "/.well-known/caldav", proxy)
    except netguard.UnsafeTarget as e:
        return _not_run(url, f"Mailr may not fetch this address from the server ({e}). This is normal when the "
                             "name points to an address inside your network here. Run these commands from outside "
                             "your network instead, for example from a phone with Wi-Fi off.")
    answers: dict[str, httpx.Response] = {}
    try:
        async with _client(proxy) as c:
            answers["wk-caldav"] = await c.get(url + "/.well-known/caldav")
            answers["wk-carddav"] = await c.get(url + "/.well-known/carddav")
            answers["dav"] = await c.request("PROPFIND", url + "/dav/cal/", headers={"Depth": "0"})
            answers["acl"] = await c.request("ACL", url + "/dav/cal/mailr-check/mailr-check/")
            answers["outbox"] = await c.post(url + "/dav/itip/mailr-check/outbox/", content=b"",
                                             headers={"Content-Type": "text/calendar"})
            answers["file"] = await c.request("PROPFIND", url + "/dav/file/", headers={"Depth": "0"})
            for p in _blocked_paths(url):
                answers[p] = await c.get(url + p)
    except (httpx.HTTPError, netguard.UnsafeTarget) as e:
        return _not_run(url, f"The address did not answer ({type(e).__name__}: {str(e)[:200]}). Check the DNS "
                             "record and the reverse proxy, or run these commands from outside your network.")
    checks = evaluate(url, answers)
    via = "through the VPN egress proxy" if proxy else "directly from the server"
    return {"ran": True, "ok": all(c["ok"] for c in checks), "url": url, "via": via, "checks": checks,
            "reason": "", "commands": commands(url)}
