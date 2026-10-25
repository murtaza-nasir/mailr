"""Helpers for publishing a picture beyond mailr (Settings → You → Your picture).

Other mail apps never see a mailr picture. They show Gravatar or Libravatar (some clients), the
Google account photo (Gmail to Gmail), the Microsoft account photo (Outlook) or a BIMI brand
logo (verified domains). These helpers only look; they change nothing anywhere:

- `gravatar_status`: whether Gravatar and Libravatar have a picture for an address (hash
  lookup with d=404). It tells those services that someone asked about the address, so it runs
  only when the user presses the button;
- `provider_for`: whether an identity sends through Google or Microsoft, for the short
  guidance on setting the account photo there;
- `bimi_check`: the BIMI requirements of an identity's domain, each met or not, with the next
  step. The parsers (`parse_tags`, `evaluate`) are pure functions with fixture tests.

Every lookup is a content fetch (core/egress.py): through the egress proxy when one is
configured (DNS too, as DNS over HTTPS), otherwise pinned to checked public addresses, and not
at all while MAILR_REQUIRE_EGRESS finds no usable egress.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any
from urllib.parse import urljoin

import httpx

# Mailbox providers whose domains users do not control: BIMI is not theirs to set up.
SHARED_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "passport.com",
    "outlook.co.uk", "hotmail.co.uk", "live.co.uk", "yahoo.com", "ymail.com", "rocketmail.com",
    "yahoo.co.uk", "aol.com", "icloud.com", "me.com", "mac.com", "proton.me", "protonmail.com",
    "pm.me", "gmx.com", "gmx.net", "gmx.de", "web.de", "mail.com", "zoho.com", "zohomail.com",
    "yandex.com", "yandex.ru", "fastmail.com", "fastmail.fm", "hey.com", "tutanota.com", "tuta.io",
    "mail.ru", "qq.com", "163.com", "comcast.net", "att.net", "verizon.net", "duck.com",
})
_GOOGLE_DOMAINS = {"gmail.com", "googlemail.com"}
_MS_DOMAINS = {"outlook.com", "hotmail.com", "live.com", "msn.com", "passport.com", "outlook.co.uk",
               "hotmail.co.uk", "live.co.uk"}
LOGO_MAX = 32 * 1024  # the BIMI group's recommended maximum for the SVG
DEADLINE = 8.0


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower().rstrip(".")


def provider_for(email: str, connector: Any | None) -> str | None:
    """"google" or "microsoft" when the address sends through that provider (connector kind,
    preset or server name, or the address's domain), else None."""
    d = domain_of(email)
    kind = (getattr(connector, "kind", "") or "").lower()
    preset = (getattr(connector, "preset", "") or "").lower()
    cfg = getattr(connector, "config", None) or {}
    hosts = " ".join(str((cfg.get(k) or {}).get("host", "")) if isinstance(cfg.get(k), dict) else ""
                     for k in ("imap", "smtp")).lower()
    if kind == "gmail" or "gmail" in preset or "google" in preset or "gmail.com" in hosts or d in _GOOGLE_DOMAINS:
        return "google"
    if (kind == "m365" or any(x in preset for x in ("outlook", "m365", "office365", "microsoft", "hotmail"))
            or "office365.com" in hosts or "outlook.com" in hosts or d in _MS_DOMAINS):
        return "microsoft"
    return None


# ---------------------------------------------------------------- Gravatar / Libravatar


async def _status(url: str, egress: str) -> str:
    """found | not_found | unknown, for one d=404 picture URL (redirects checked per hop)."""
    from mailr.core import egress as content
    from mailr.core.netguard import TooLarge, UnsafeTarget, fetch_capped

    try:
        async with asyncio.timeout(DEADLINE):
            async with content.client(egress, timeout=6, headers={"User-Agent": "mailr picture check"}) as c:
                hops = 0
                while True:
                    await content.check_url(url, egress)
                    r, _ = await fetch_capped(c, "GET", url, limit=512 * 1024, deadline=DEADLINE)
                    if r.is_redirect and r.headers.get("location") and hops < 3:
                        url = urljoin(url, r.headers["location"])
                        hops += 1
                        continue
                    break
    except (httpx.HTTPError, UnsafeTarget, TooLarge, TimeoutError):
        return "unknown"
    if r.status_code == 200 and r.headers.get("content-type", "").lower().startswith("image/"):
        return "found"
    if r.status_code == 404:
        return "not_found"
    return "unknown"


async def gravatar_status(email: str, egress: str) -> dict[str, str]:
    key = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    g, lib = await asyncio.gather(
        _status(f"https://gravatar.com/avatar/{key}?s=80&d=404", egress),
        _status(f"https://seccdn.libravatar.org/avatar/{key}?s=80&d=404", egress))
    return {"gravatar": g, "libravatar": lib}


# ---------------------------------------------------------------- BIMI


def parse_tags(txt: str) -> dict[str, str]:
    """A DMARC or BIMI record as tag → value (tags lower-case, values trimmed)."""
    out: dict[str, str] = {}
    for part in txt.split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip().lower()
        if k and k not in out:
            out[k] = v.strip()
    return out


def pick(records: list[str], version: str) -> str | None:
    """The one record of a version (v=BIMI1, v=DMARC1); None when absent or ambiguous."""
    found = [r.strip() for r in records if re.match(rf"\s*v\s*=\s*{version}\s*(;|$)", r, re.IGNORECASE)]
    return found[0] if len(found) == 1 else None


def org_domain(domain: str) -> str:
    """The registrable domain, roughly (last two labels; three for a two-letter second level
    under a country code such as example.co.uk)."""
    parts = domain.split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "ac", "gov", "edu"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def evaluate(domain: str, bimi_txt: str | None, dmarc_txt: str | None, dmarc_is_org: bool,
             logo: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The BIMI requirements, each {key, label, met (True/False/None = not checked), detail,
    next}. Pure: the DNS answers and the logo fetch result come in."""
    reqs: list[dict[str, Any]] = []
    bimi = parse_tags(bimi_txt) if bimi_txt else None
    reqs.append({
        "key": "record", "label": "BIMI record", "met": bimi is not None,
        "detail": f"default._bimi.{domain}: {bimi_txt}" if bimi_txt else f"No BIMI record at default._bimi.{domain}.",
        "next": "" if bimi is not None else
        f'Add a TXT record at default._bimi.{domain}: "v=BIMI1; l=https://{domain}/logo.svg; a=" '
        "(with your certificate's URL after a= once you have one).",
    })
    dmarc = parse_tags(dmarc_txt) if dmarc_txt else None
    if dmarc is None:
        reqs.append({"key": "dmarc", "label": "DMARC at enforcement", "met": False,
                     "detail": "No DMARC record.",
                     "next": f'Add a TXT record at _dmarc.{domain}: "v=DMARC1; p=quarantine; rua=mailto:…", '
                             "check the reports, then keep p=quarantine or p=reject."})
    else:
        policy = (dmarc.get("sp") if dmarc_is_org and dmarc.get("sp") else dmarc.get("p", "")).lower()
        try:
            pct = int(dmarc.get("pct", "100"))
        except ValueError:
            pct = -1
        enforced = policy in ("quarantine", "reject") and pct == 100
        why = []
        if policy not in ("quarantine", "reject"):
            why.append(f"policy is {policy or 'missing'}")
        if pct != 100:
            why.append(f"pct={dmarc.get('pct')}")
        reqs.append({"key": "dmarc", "label": "DMARC at enforcement", "met": enforced,
                     "detail": f"DMARC: {dmarc_txt}" + (f" ({', '.join(why)})" if why else ""),
                     "next": "" if enforced else
                     "Set p=quarantine or p=reject (and sp= for subdomains) with pct=100 (or no pct tag) "
                     "once your reports show all legitimate mail passing."})
    loc = (bimi or {}).get("l", "")
    if bimi is None:
        reqs.append({"key": "logo_https", "label": "Logo URL (https)", "met": None, "detail": "No record to read.", "next": ""})
        reqs.append({"key": "logo_svg", "label": "Logo is SVG Tiny PS", "met": None, "detail": "No record to read.", "next": ""})
    else:
        https = loc.lower().startswith("https://")
        reqs.append({"key": "logo_https", "label": "Logo URL (https)", "met": https,
                     "detail": loc or "The record has no l= logo URL.",
                     "next": "" if https else "Publish the logo at an https:// address and put it after l=."})
        if not https:
            reqs.append({"key": "logo_svg", "label": "Logo is SVG Tiny PS", "met": None, "detail": "Not checked.", "next": ""})
        elif not logo or logo.get("error"):
            reqs.append({"key": "logo_svg", "label": "Logo is SVG Tiny PS", "met": False,
                         "detail": (logo or {}).get("error") or "Couldn't fetch the logo.",
                         "next": "Make sure the logo URL answers with the SVG file."})
        else:
            problems = []
            if logo.get("type") != "image/svg+xml":
                problems.append(f"served as {logo.get('type') or 'unknown type'}, not image/svg+xml")
            if logo.get("size", 0) > LOGO_MAX:
                problems.append(f"{logo['size'] // 1024} KB, over 32 KB")
            if logo.get("type") == "image/svg+xml" and not logo.get("tiny_ps"):
                problems.append('no baseProfile="tiny-ps"')
            reqs.append({"key": "logo_svg", "label": "Logo is SVG Tiny PS", "met": not problems,
                         "detail": "; ".join(problems) if problems else f"SVG, {max(1, logo.get('size', 0) // 1024)} KB, tiny-ps profile.",
                         "next": "" if not problems else
                         "Export the logo as SVG Tiny Portable/Secure (square, solid background, under 32 KB) "
                         "and serve it as image/svg+xml."})
    cert = (bimi or {}).get("a", "")
    reqs.append({"key": "certificate", "label": "Mark certificate (VMC or CMC)", "met": bool(cert) if bimi else False,
                 "detail": cert if cert else "No a= certificate URL.",
                 "next": "" if cert else
                 "Gmail and Apple Mail show BIMI logos only with a Verified Mark Certificate (registered "
                 "trademark) or a Common Mark Certificate, bought from a certificate authority such as "
                 "DigiCert or Sectigo; put its URL after a=. Some other providers show the logo without one."})
    return reqs


async def _logo(url: str, egress: str) -> dict[str, Any]:
    """Type, size and profile of the logo (at most LOGO_MAX*4 bytes read)."""
    from mailr.core import egress as content
    from mailr.core.netguard import TooLarge, UnsafeTarget, fetch_capped

    try:
        async with asyncio.timeout(DEADLINE):
            async with content.client(egress, timeout=6, headers={"User-Agent": "mailr BIMI check"}) as c:
                await content.check_url(url, egress)
                r, body = await fetch_capped(c, "GET", url, limit=LOGO_MAX * 4, deadline=DEADLINE)
    except TooLarge:
        return {"type": "image/svg+xml", "size": LOGO_MAX * 4 + 1, "tiny_ps": False}
    except UnsafeTarget as e:
        return {"error": str(e)}
    except (httpx.HTTPError, TimeoutError):
        return {"error": "Couldn't fetch the logo."}
    if r.is_redirect:
        return {"error": "The logo URL redirects; BIMI needs the file at that address."}
    if r.status_code != 200:
        return {"error": f"The logo URL answered {r.status_code}."}
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    head = body[:4096].decode("utf-8", "replace")
    return {"type": ctype, "size": len(body), "tiny_ps": bool(re.search(r"baseProfile\s*=\s*['\"]tiny-ps['\"]", head, re.IGNORECASE))}


async def bimi_check(domain: str, egress: str) -> dict[str, Any]:
    """Look up the domain's BIMI and DMARC records and its logo, and evaluate them."""
    from mailr.core.egress import txt_records

    domain = domain.strip().lower().rstrip(".")
    if domain in SHARED_DOMAINS:
        return {"domain": domain, "applicable": False,
                "reason": "Not applicable: you don't control this domain. Its owner decides about BIMI.",
                "requirements": []}
    org = org_domain(domain)
    bimi_txt = pick(await txt_records(f"default._bimi.{domain}", egress), "BIMI1")
    if bimi_txt is None and org != domain:
        bimi_txt = pick(await txt_records(f"default._bimi.{org}", egress), "BIMI1")
    dmarc_txt = pick(await txt_records(f"_dmarc.{domain}", egress), "DMARC1")
    dmarc_is_org = False
    if dmarc_txt is None and org != domain:
        dmarc_txt = pick(await txt_records(f"_dmarc.{org}", egress), "DMARC1")
        dmarc_is_org = dmarc_txt is not None
    logo = None
    loc = parse_tags(bimi_txt).get("l", "") if bimi_txt else ""
    if loc.lower().startswith("https://"):
        logo = await _logo(loc, egress)
    reqs = evaluate(domain, bimi_txt, dmarc_txt, dmarc_is_org, logo)
    return {"domain": domain, "applicable": True, "reason": "", "requirements": reqs,
            "ready": all(r["met"] for r in reqs)}
