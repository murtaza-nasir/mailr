"""Tracking in mail bodies: open-tracking images and wrapped links.

- Tracker images are recognised by the rules in trackers.txt (known pixel hosts and paths) and
  by their shape (1×1, zero-sized or hidden tiny images). They are never fetched; render.py
  removes them and the reader shows how many were blocked.
- Wrapped links ("safe links", redirectors, click trackers that carry the real address in the
  URL) are rewritten to the address they carry. Only known formats are decoded, and nothing is
  ever fetched to find out where a link goes. The original URL stays on the link
  (data-mailr-tracked) for anyone who wants it.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

_RULES_FILE = Path(__file__).with_name("trackers.txt")


@lru_cache
def _rules() -> list[tuple[str, re.Pattern | None]]:
    rules = []
    for line in _RULES_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        host, _, path = line.partition(" ")
        path = path.strip()
        rules.append((host.lower(), re.compile(path, re.IGNORECASE) if path else None))
    return rules


def _host_matches(host: str, rule: str) -> bool:
    return rule == "*" or host == rule or host.endswith("." + rule)


def is_tracker_url(url: str) -> bool:
    """True when the URL is a known open-tracking image."""
    try:
        u = urlsplit(url.strip())
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if not host:
        return False
    target = (u.path or "/") + (f"?{u.query}" if u.query else "")
    for rule_host, path in _rules():
        if _host_matches(host, rule_host) and (path is None or path.match(target)):
            return True
    return False


_PX = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(px)?\s*$", re.IGNORECASE)


def _size(v: str | None) -> float | None:
    m = _PX.match(v or "")
    return float(m.group(1)) if m else None


def _style_map(style: str) -> dict[str, str]:
    out = {}
    for decl in (style or "").split(";"):
        k, _, v = decl.partition(":")
        if v:
            out[k.strip().lower()] = v.strip().lower()
    return out


def looks_like_pixel(attrs: dict) -> bool:
    """A 1×1, zero-sized or hidden tiny image: the shape of an open-tracking pixel."""
    st = _style_map(attrs.get("style") or "")
    w = _size(attrs.get("width")) if attrs.get("width") is not None else _size(st.get("width"))
    h = _size(attrs.get("height")) if attrs.get("height") is not None else _size(st.get("height"))
    if w == 0 or h == 0:
        return True
    if w is not None and h is not None and w <= 3 and h <= 3:
        return True
    hidden = (st.get("display") == "none" or st.get("visibility") == "hidden"
              or st.get("opacity") in ("0", "0.0") or st.get("max-height") in ("0", "0px"))
    # A hidden image with a real size is usually the mobile variant of a responsive layout.
    return hidden and (w is None or w <= 3) and (h is None or h <= 3)


def tiny(width: int | None, height: int | None) -> bool:
    """A fetched image this small carries no content (checked after the proxy fetched it)."""
    return width is not None and height is not None and width <= 2 and height <= 2


# ---------------- wrapped links ----------------

_GOOGLE = re.compile(r"^(www\.)?google\.[a-z]{2,3}(\.[a-z]{2})?$")
# host (exact or suffix), path regex, query parameters that carry the target.
_PARAM_WRAPPERS: list[tuple[str, str, tuple[str, ...]]] = [
    ("safelinks.protection.outlook.com", r"/", ("url",)),
    ("l.facebook.com", r"/l\.php", ("u",)),
    ("lm.facebook.com", r"/l\.php", ("u",)),
    ("l.messenger.com", r"/l\.php", ("u",)),
    ("l.instagram.com", r"/", ("u",)),
    ("www.youtube.com", r"/redirect", ("q",)),
    ("slack-redir.net", r"/link", ("url",)),
    ("www.linkedin.com", r"/redir/redirect", ("url",)),
    ("out.reddit.com", r"/", ("url",)),
    ("steamcommunity.com", r"/linkfilter/", ("url", "u")),
    ("urldefense.proofpoint.com", r"/v2/url", ("u",)),
    ("mandrillapp.com", r"/track/click", ("p",)),
]
_CONVERTKIT = re.compile(r"^(click\.)?convertkit-mail\d*\.com$")


def _web_url(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    if not re.match(r"(?i)https?://[^/\s]", s) or re.search(r"[\x00-\x20\x7f]", s):
        return None
    return s


def _b64(s: str) -> bytes | None:
    s = s.strip().replace("-", "+").replace("_", "/")
    try:
        return base64.b64decode(s + "=" * (-len(s) % 4), validate=True)
    except (binascii.Error, ValueError):
        return None


def _mandrill(p: str) -> str | None:
    raw = _b64(p)
    try:
        outer = json.loads(raw or b"")
        inner = outer.get("p") if isinstance(outer, dict) else None
        inner = json.loads(inner) if isinstance(inner, str) else inner
        return inner.get("url") if isinstance(inner, dict) else None
    except (ValueError, AttributeError):
        return None


def _unwrap_once(url: str) -> str | None:
    try:
        u = urlsplit(url)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if not host:
        return None
    qs = parse_qs(u.query)
    if _GOOGLE.match(host) and u.path == "/url":
        return _web_url((qs.get("q") or qs.get("url") or [None])[0])
    for wh, path, params in _PARAM_WRAPPERS:
        if (host == wh or host.endswith("." + wh)) and re.match(path, u.path or "/"):
            for name in params:
                v = (qs.get(name) or [None])[0]
                if not v:
                    continue
                if wh == "urldefense.proofpoint.com":
                    return _web_url(unquote(v.translate(str.maketrans("-_", "%/"))))
                if wh == "mandrillapp.com":
                    return _web_url(_mandrill(v))
                return _web_url(v)
            return None
    if host == "urldefense.com" and u.path.startswith("/v3/__"):
        # Proofpoint v3: the target sits between "__" markers. Targets with "*" use a
        # substitution table that is not decoded here.
        m = re.match(r"/v3/__(.+?)__;", url[url.index("/v3/__"):])
        target = m.group(1) if m else None
        return None if target is None or "*" in target else _web_url(target)
    if host.endswith(".awstrack.me") and u.path.startswith("/L0/"):
        # Amazon SES click tracking: /L0/<percent-encoded target>/<version>/<token>
        seg = u.path[4:].split("/", 1)[0]
        return _web_url(unquote(seg))
    if host == "substack.com" and u.path.startswith("/redirect/"):
        # /redirect/2/<base64 JSON with the target in "e">.<signature>
        part = u.path.rsplit("/", 1)[-1].split(".", 1)[0]
        try:
            data = json.loads(_b64(part) or b"")
            return _web_url(data.get("e")) if isinstance(data, dict) else None
        except ValueError:
            return None
    if _CONVERTKIT.match(host):
        last = u.path.rstrip("/").rsplit("/", 1)[-1]
        raw = _b64(last)
        try:
            return _web_url(raw.decode()) if raw else None
        except UnicodeDecodeError:
            return None
    return None


def unwrap_link(url: str) -> str | None:
    """The address a wrapped link carries, or None when the URL is not a known wrapper.
    Nested wrappers (a safe link around a Proofpoint link) are unwrapped up to three levels."""
    out = None
    cur = url
    for _ in range(3):
        nxt = _unwrap_once(cur)
        if not nxt or nxt == cur:
            break
        out = cur = nxt
    return out
