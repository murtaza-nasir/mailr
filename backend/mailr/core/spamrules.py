"""Deterministic spam checks that run at ingest, before any model.

- Brand impersonation: the display name claims a well-known brand ("Facebook", "PayPal",
  "Microsoft 365 Security") but the address is not on one of that brand's domains, and the
  user has never corresponded with the sender. Phishing's most common trick.
- Replies into spam: a message that continues a conversation whose messages are all in
  Spam goes to Spam too (Gmail behaviour).
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import Person

# brand keyword -> domains the brand really sends from (suffix match)
BRANDS: dict[str, tuple[str, ...]] = {
    "facebook": ("facebook.com", "facebookmail.com", "meta.com", "fb.com"),
    "instagram": ("instagram.com", "facebookmail.com", "meta.com"),
    "whatsapp": ("whatsapp.com", "whatsapp.net", "meta.com"),
    "meta": ("meta.com", "facebookmail.com", "facebook.com"),
    "paypal": ("paypal.com", "paypal.co.uk", "paypal.de", "e.paypal.com"),
    "apple": ("apple.com", "icloud.com", "email.apple.com", "insideapple.apple.com"),
    "icloud": ("apple.com", "icloud.com"),
    "microsoft": ("microsoft.com", "microsoftonline.com", "office.com", "outlook.com", "live.com",
                  "office365.com", "email.microsoft.com", "accountprotection.microsoft.com"),
    "outlook": ("microsoft.com", "outlook.com", "live.com", "office.com"),
    "office 365": ("microsoft.com", "office.com", "office365.com", "microsoftonline.com"),
    "google": ("google.com", "gmail.com", "youtube.com", "googlemail.com", "accounts.google.com"),
    "gmail": ("google.com", "gmail.com", "googlemail.com"),
    "amazon": ("amazon.com", "amazon.co.uk", "amazon.de", "amazon.ca", "amazonses.com", "marketplace.amazon.com"),
    "netflix": ("netflix.com", "mailer.netflix.com"),
    "linkedin": ("linkedin.com",),
    "docusign": ("docusign.com", "docusign.net"),
    "dropbox": ("dropbox.com", "dropboxmail.com"),
    "adobe": ("adobe.com", "mail.adobe.com"),
    "coinbase": ("coinbase.com",),
    "metamask": ("metamask.io", "consensys.io", "consensys.net"),
    "chase": ("chase.com", "jpmorgan.com"),
    "wells fargo": ("wellsfargo.com",),
    "bank of america": ("bankofamerica.com", "bofa.com"),
    "fedex": ("fedex.com",),
    "dhl": ("dhl.com", "dhl.de"),
    "usps": ("usps.com", "usps.gov"),
    "ups": ("ups.com",),
    "geek squad": ("bestbuy.com", "geeksquad.com"),
    "norton": ("norton.com", "nortonlifelock.com", "gen.digital"),
    "mcafee": ("mcafee.com",),
}
_WORD = {b: re.compile(rf"(?<![a-z]){re.escape(b)}(?![a-z])", re.I) for b in BRANDS}


def claimed_brand(from_name: str, from_email: str) -> str | None:
    """The brand the display name claims but the address does not back up, if any. The name is
    matched in its confusable skeleton (textsafe.skeleton): look-alike Cyrillic or Greek letters,
    zero-width and bidi characters do not hide a brand ("PаyPal", "Pay<ZWSP>Pal")."""
    import unicodedata

    from mailr.core import textsafe

    raw = unicodedata.normalize("NFKC", textsafe.clean(from_name or ""))
    # Look-alikes are folded to Latin when a word mixes them with Latin letters, or when the name
    # has no other script; a name written in Cyrillic or Greek words is not read as Latin.
    other_script = any(c.isalpha() and ord(c) > 0x24F for c in raw)
    name = textsafe.skeleton(raw) if textsafe.mixed_script(raw) or not other_script \
        else re.sub(f"[{textsafe.JOINERS}]", "", raw).lower()
    domain = from_email.rsplit("@", 1)[-1].lower() if "@" in (from_email or "") else ""
    if not name or not domain:
        return None
    for brand, doms in BRANDS.items():
        if _WORD[brand].search(name):
            if not any(domain == d or domain.endswith("." + d) for d in doms):
                return brand
    return None


async def known_sender(db: AsyncSession, user_id: int, email: str) -> bool:
    """Someone the user has written to, or has received several messages from before."""
    p = (await db.execute(select(Person).where(Person.user_id == user_id, Person.email == email.lower()))).scalar_one_or_none()
    return bool(p and (p.sent_count > 0 or p.received_count >= 3))


_SA_SCORE = re.compile(r"score=(-?\d+(?:\.\d+)?)", re.I)
DEFAULT_UPSTREAM_UNKNOWN_THRESHOLD = 4.0


def upstream_score(headers: dict[str, str]) -> float | None:
    """The provider's SpamAssassin score from X-Spam-Status, if present."""
    m = _SA_SCORE.search(headers.get("X-Spam-Status", "") or "")
    return float(m.group(1)) if m else None


def upstream_unknown_threshold(settings: dict) -> float | None:
    """Stricter threshold for senders the user has never dealt with (None = off)."""
    v = (settings or {}).get("spam_upstream_unknown_threshold", DEFAULT_UPSTREAM_UNKNOWN_THRESHOLD)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return DEFAULT_UPSTREAM_UNKNOWN_THRESHOLD
    return v if v > 0 else None
