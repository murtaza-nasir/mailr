"""Templates: fixed text the user inserts in compose.

A template is a name, an optional `;shortcut`, an optional subject and a sanitized HTML body. On
insert, these variables are filled:

    {first_name} {name} {email}   the first recipient (To, else Cc, else Bcc)
    {my_name}                     the sending identity's name, else the user's name
    {date}                        today in the user's time zone, "September 30, 2026"

A variable that is unknown, or known but without a value (no recipient yet, a recipient without a
name), stays as typed, so the gap shows in the draft.
"""

from __future__ import annotations

import html as htmllib
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from mailr.core.compose import clean_html

SHORTCUT = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}")
VARIABLES = ("first_name", "name", "email", "my_name", "date")
_VAR = re.compile(r"\{([a-z_]+)\}")


def clean_shortcut(s: str) -> str:
    """'' or a valid shortcut (a leading ';' is dropped); ValueError otherwise."""
    s = (s or "").strip().lower().lstrip(";")
    if s and not SHORTCUT.fullmatch(s):
        raise ValueError("a shortcut uses a-z, 0-9, - and _ (up to 48 characters)")
    return s


def today_text(tz: str | None, now: datetime | None = None) -> str:
    try:
        zone = ZoneInfo(tz or "UTC")
    except Exception:  # noqa: BLE001  (an unknown zone name in the settings)
        zone = UTC
    d = (now or datetime.now(UTC)).astimezone(zone)
    return f"{d:%B} {d.day}, {d.year}"


def first_name(name: str) -> str:
    """The given name of a display name: "Dana Tester" → Dana, "Tester, Dana" → Dana."""
    name = name.strip().strip('"').strip()
    if "," in name:
        name = name.split(",", 1)[1].strip()
    return name.split()[0] if name.split() else ""


def values(recipient: dict | None, my_name: str, tz: str | None, now: datetime | None = None) -> dict[str, str]:
    """The variable values for one insert ('' = no value; the variable stays as typed)."""
    r = recipient or {}
    name = (r.get("name") or "").strip()
    return {"first_name": first_name(name), "name": name, "email": (r.get("email") or "").strip(),
            "my_name": my_name.strip(), "date": today_text(tz, now)}


def fill(text: str, vals: dict[str, str], *, escape: bool) -> str:
    def sub(m: re.Match) -> str:
        v = vals.get(m.group(1), "")
        if not v:
            return m.group(0)
        return htmllib.escape(v, quote=False) if escape else v

    return _VAR.sub(sub, text)


def expand(subject: str, body_html: str, vals: dict[str, str]) -> dict[str, str]:
    """The template with its variables filled: a plain subject and sanitized HTML."""
    return {"subject": fill(subject, vals, escape=False), "body_html": clean_html(fill(body_html, vals, escape=True))}
