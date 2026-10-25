"""The oldest Android app the server still answers.

The Android app names itself in its User-Agent ("mailr-android/0.5.1", "mailr-android/0.5.2-debug").
Builds before 0.5.0 send "Send to self" shares without the X-Mailr-Origin marker, so their shares
would count as the user's own words (assistant context, writing samples, standing orders). The
server refuses API calls from such builds with 426 and a message asking for an update.
MAILR_ANDROID_MIN_VERSION sets the floor ("" turns the check off). Other clients, the health
endpoints and the app downloads (/api/v1/app/: the release list and the APKs, so an old build can
find and fetch its update) are never affected.

The Notes app ("mailr-android-notes/1.1.0") has a floor of its own: the instance setting
`phone_apps.notes_min_version`, set in Settings → Admin → Phone apps ("" or unset: no floor). The
middleware reads it from the database at most every NOTES_TTL seconds, and only for requests from
the Notes app. A change made on this process is seen at once (`clear_cache`); other processes see
it within NOTES_TTL.
"""

from __future__ import annotations

import json
import logging
import re
import time

# Paths every build reaches: health, and the release list and APKs an old build updates from.
OPEN_PATHS = ("/api/v1/health", "/api/v1/app/")
_UA = re.compile(r"\bmailr-android/(\d+)\.(\d+)(?:\.(\d+))?")
_NOTES_UA = re.compile(r"\bmailr-android-notes/(\d+)\.(\d+)(?:\.(\d+))?")
NOTES_KEY = "phone_apps.notes_min_version"
NOTES_TTL = 30.0
log = logging.getLogger("mailr.appversion")


def _version(s: str) -> tuple[int, int, int] | None:
    m = re.fullmatch(r"\s*(\d+)\.(\d+)(?:\.(\d+))?\s*", s or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def valid(minimum: str) -> bool:
    """A floor an admin may set: "" (none) or "1.2" / "1.2.3"."""
    return minimum.strip() == "" or _version(minimum) is not None


def too_old(user_agent: str, minimum: str, app: str = "mailr") -> bool:
    """True when the User-Agent is that Android app ("mailr" or "notes") at a version below `minimum`."""
    floor = _version(minimum)
    m = (_NOTES_UA if app == "notes" else _UA).search(user_agent or "")
    if floor is None or m is None:
        return False
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) < floor


_notes_cache: tuple[float, str] | None = None


def clear_cache() -> None:
    global _notes_cache
    _notes_cache = None


async def notes_min_version() -> str:
    """The Notes app's floor ("" when none), cached for NOTES_TTL seconds. No database: no floor."""
    global _notes_cache
    if _notes_cache is not None and time.monotonic() - _notes_cache[0] < NOTES_TTL:
        return _notes_cache[1]
    try:
        from mailr.db import session_scope
        from mailr.models import InstanceSetting

        async with session_scope() as db:
            row = await db.get(InstanceSetting, NOTES_KEY)
            value = str(row.value or "") if row is not None and isinstance(row.value, str) else ""
    except Exception as e:  # noqa: BLE001  the floor is a courtesy; an unreadable one lets the app through
        log.debug("notes floor unreadable: %s", type(e).__name__)
        value = _notes_cache[1] if _notes_cache else ""
    _notes_cache = (time.monotonic(), value)
    return value


def _refusal(app: str, minimum: str) -> bytes:
    name = "the Notes app" if app == "notes" else "the Mailr app"
    return json.dumps({"detail": f"This version of {name} is no longer supported. "
                                 f"Update it to {minimum} or later."}).encode()


class AndroidMinVersion:
    """Pure ASGI middleware: 426 for /api/ calls from an Android build (mailr or Notes) below its floor."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        path = str(scope.get("path", ""))
        if scope.get("type") == "http" and path.startswith("/api/") and not path.startswith(OPEN_PATHS):
            from mailr.config import get_settings

            ua = next((v.decode("latin-1") for k, v in scope.get("headers") or [] if k == b"user-agent"), "")
            app, minimum = "mailr", get_settings().android_min_version
            if ua and _NOTES_UA.search(ua):
                app, minimum = "notes", await notes_min_version()
            if ua and too_old(ua, minimum, app):
                from mailr.core import webheaders

                body = _refusal(app, minimum)
                # Made outside security_headers (this middleware wraps it): the base headers go on
                # here.
                base = [(k.lower().encode("latin-1"), v.encode("latin-1"))
                        for k, v in webheaders.all_responses().items() if k.lower() != "cache-control"]
                await send({"type": "http.response.start", "status": 426,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(body)).encode()),
                                        (b"cache-control", b"no-store"), *base]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
