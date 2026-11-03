"""Calendar sync failures in classes the clients can explain (Settings → Calendar & tasks, the
calendar's "isn't syncing" banner on the web and on the phone).

A failure is stored twice: the technical text (`Connector.status_detail`, `CalCollection.sync_error`),
which Admin → Diagnostics and the clients' "Details" show, and its kind, which the clients turn
into a translated sentence with what to do. The kinds:

- `auth`: the server refused the user name or password (401, or 403 on a read).
- `not_found`: nothing at the address (404, 410; 405 where a calendar was expected).
- `tls`: the certificate or the TLS handshake failed.
- `unreachable`: no answer: the name does not resolve, the connection is refused or reset, or it
  timed out.
- `server`: the server failed (5xx, an unreadable or oversized reply, a redirect loop).
- `blocked`: the address is not allowed from this server (a private or local address, plain http
  with a password, a redirect from https to http).
- `partial`: the server listed only part of a calendar (507); nothing was removed.
- `unknown`: anything else.

A sync classifies the exception it caught (`classify`) and keeps the kind in the connector's
state. Text stored before kinds existed, and per-calendar errors, are classified from the text
(`classify_text`): the messages come from this package (calendar/dav.py, sync.py, webcal.py), so
their shapes are known.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

KINDS = ("auth", "not_found", "tls", "unreachable", "server", "blocked", "partial", "unknown")


def classify(e: BaseException) -> str:
    """The kind of a failure raised while syncing a calendar account."""
    from mailr.calendar import dav
    from mailr.core.netguard import UnsafeTarget

    if isinstance(e, UnsafeTarget):
        return "unreachable" if str(e).startswith("cannot resolve") else "blocked"
    if isinstance(e, dav.DavError):
        if e.kind in KINDS:
            return e.kind
        if isinstance(e, dav.Unauthorized):
            return "auth"
        if isinstance(e, dav.NotFound):
            return "not_found"
        if isinstance(e, dav.Truncated):
            return "partial"
        return _status_kind(e.status) or classify_text(str(e)) or "unknown"
    return classify_text(str(e)) or "unknown"


def _status_kind(status: int) -> str | None:
    if status in (401, 403):
        return "auth"
    if status in (404, 405, 410):
        return "not_found"
    if status == 507:
        return "partial"
    if 500 <= status < 600:
        return "server"
    return None


_HTTP = re.compile(r"\bHTTP (\d{3})\b")
# Checked in this order: a TLS failure is also a ConnectError, an http-to-https refusal says "refused".
_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("partial", ("listed only part",)),
    ("auth", ("sign-in failed", "Sign-in failed", ": forbidden", "user name or password")),
    ("not_found", (": not found", "no calendar service at this address")),
    ("tls", ("CERTIFICATE_VERIFY_FAILED", "certificate verify failed", "[SSL", "SSLError", "certificate")),
    ("blocked", ("private address", "local or reserved", "infrastructure", "use https://",
                 "from https to http", "another address; refused", "only http:// and https://")),
    ("unreachable", ("cannot resolve", "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
                     "PoolTimeout", "TimeoutException", "NetworkError", "ReadError", "WriteError",
                     "could not be reached", "Name or service not known", "Connection refused")),
    ("server", ("unreadable XML", "response is too large", "too many redirects", "RemoteProtocolError",
                "DecodingError")),
]


def classify_text(text: str | None) -> str | None:
    """The kind of a stored failure text; None for an empty text."""
    text = (text or "").strip()
    if not text:
        return None
    for kind, marks in _PATTERNS:
        if any(m in text for m in marks):
            return kind
    m = _HTTP.search(text)
    if m:
        return _status_kind(int(m.group(1))) or "unknown"
    return "unknown"


def host_of(url: str | None) -> str | None:
    """The host of an account's address (cal.example.com), without a user name or port."""
    try:
        return (urlsplit(url or "").hostname or "").lower() or None
    except ValueError:
        return None


def connector_host(conn: Any) -> str | None:
    """Where the account's calendars live: its address's host; for the built-in calendar, this
    server's own host (the calendar store sits behind it)."""
    if getattr(conn, "builtin", False):
        from mailr.config import get_settings

        return host_of(get_settings().base_url)
    return host_of((conn.config or {}).get("url"))


def connector_problem(conn: Any) -> dict[str, str | None] | None:
    """{kind, host} of a calendar account in error, else None."""
    if conn.status != "error":
        return None
    cst = (conn.state or {}).get("caldav") or {}
    kind = ("auth" if cst.get("auth_failed") else None) or cst.get("error_kind") or classify_text(conn.status_detail)
    return {"kind": kind if kind in KINDS else "unknown", "host": connector_host(conn)}


def text_problem(text: str | None, conn: Any = None) -> dict[str, str | None] | None:
    """{kind, host} of a stored failure text (a calendar's sync error), else None."""
    kind = classify_text(text)
    if kind is None:
        return None
    return {"kind": kind, "host": connector_host(conn) if conn is not None else None}
