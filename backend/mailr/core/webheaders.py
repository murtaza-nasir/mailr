"""The security headers of the web app's pages, read from webheaders.json.

The same file feeds the development and test servers (frontend/vite.config.ts, pageHeaders), so
the browser tests run under the policy production sends."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

FILE = Path(__file__).with_name("webheaders.json")


@cache
def _table() -> dict:
    return json.loads(FILE.read_text())


def all_responses() -> dict[str, str]:
    """Headers for every response, API included."""
    return dict(_table()["all"])


def pages() -> dict[str, str]:
    """Headers for every response outside /api/ besides the CSP."""
    return dict(_table()["pages"])


def shell_csp(remote_images: bool = False) -> str:
    """The web app's Content-Security-Policy. Images come from mailr itself (the image proxy,
    avatars, attachments); remote https: images only when the image proxy is off."""
    t = _table()
    out = []
    for name, value in t["csp"].items():
        extra = t["csp_remote_images"].get(name) if remote_images else None
        out.append(f"{name} {value}" + (f" {extra}" if extra else ""))
    return "; ".join(out)


def help_csp() -> dict[str, str]:
    """The help pages' policy before the inline-script hashes (core/helpdocs.py adds them)."""
    return dict(_table()["help_csp"])


def attachment(name: str) -> str:
    """Content-Disposition for a download named `name`: an ASCII fallback for old clients and the
    UTF-8 name (RFC 6266), so a title in any script never breaks the header (it is latin-1)."""
    from urllib.parse import quote

    plain = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_" for ch in name) or "download"
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name, safe='')}"
