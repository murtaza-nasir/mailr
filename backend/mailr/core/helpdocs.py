"""The user guide inside mailr, at /help/, for signed-in people only.

The image builds the documentation site (site/, MkDocs) into mailr/help with site/build_help.py:
relative links, so the pages work under /help/, and no third-party requests (no web fonts, no
GitHub facts, Mermaid served from /help/). A development checkout builds it with
tools/build-help. Without the folder, /help/ says that this install has no help pages.

- Access: a browser session (the mailr_session cookie). Anyone else is sent to the sign-in page,
  which comes back here afterwards. Nothing under /help/ is public, and every response carries
  X-Robots-Tag: noindex.
- Policy: the docs theme runs a few small inline scripts. Their SHA-256 hashes are read from the
  built pages once (help_csp), so the help pages allow exactly those scripts and no others. The
  web app's own policy is unchanged (webheaders.json "csp").
- Caching: private everywhere (the pages sit behind sign-in). Files with a content hash in the
  name are kept for a year; the rest are checked again on each use (ETag)."""

from __future__ import annotations

import base64
import hashlib
import re
from functools import cache
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

DIR = Path(__file__).resolve().parents[1] / "help"

ROBOTS = "noindex, nofollow, noarchive"
_INLINE = re.compile(r"<script(?![^>]*\bsrc=)([^>]*)>(.*?)</script>", re.S | re.I)
_HASHED = re.compile(r"\.[0-9a-f]{8}\.min\.(?:js|css)$")


def help_dir() -> Path:
    return DIR


@cache
def _script_hashes(root: str, stamp: float) -> tuple[str, ...]:
    """The hashes of every inline script in the built pages (JSON data blocks are not scripts)."""
    out: set[str] = set()
    for f in Path(root).rglob("*.html"):
        for m in _INLINE.finditer(f.read_text(encoding="utf-8", errors="replace")):
            if "application/json" in m.group(1).lower():
                continue
            digest = hashlib.sha256(m.group(2).encode()).digest()
            out.add("'sha256-" + base64.b64encode(digest).decode() + "'")
    return tuple(sorted(out))


def help_csp() -> str:
    """The Content-Security-Policy of the help pages (webheaders.json "help_csp", plus the
    hashes of the built pages' inline scripts in script-src)."""
    from mailr.core import webheaders

    root = help_dir()
    index = root / "index.html"
    hashes = _script_hashes(str(root), index.stat().st_mtime) if index.is_file() else ()
    parts = []
    for name, value in webheaders.help_csp().items():
        if name == "script-src" and hashes:
            value = value + " " + " ".join(hashes)
        parts.append(f"{name} {value}")
    return "; ".join(parts)


def _headers(cache_control: str) -> dict[str, str]:
    return {"Cache-Control": cache_control, "X-Robots-Tag": ROBOTS, "Content-Security-Policy": help_csp()}


async def _signed_in(request: Request) -> bool:
    from mailr.core.auth import SESSION_COOKIE, _session_principal
    from mailr.db import session_scope

    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return False
    async with session_scope() as db:
        return await _session_principal(db, raw) is not None


MAX_PATH = 1024  # no help page has a longer path
MAX_NAME = 255  # the longest file name a file system takes


def _too_long(path: str) -> bool:
    """A path no help page has, and the file system would refuse (ENAMETOOLONG): a 404, never a
    500."""
    return len(path) > MAX_PATH or any(len(part.encode("utf-8", "surrogatepass")) > MAX_NAME
                                       for part in path.split("/"))


def _is_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except (OSError, ValueError):
        return False


def _resolve(path: str) -> Path | None:
    """The file for /help/<path>: a folder means its index.html (MkDocs directory URLs).
    Nothing outside the help folder, no hidden files, and a path the file system cannot take (a
    NUL byte) is simply not found."""
    root = help_dir().resolve()
    if "\x00" in path or any(part.startswith(".") for part in Path(path).parts) or _too_long(path):
        return None
    try:
        f = (root / path).resolve()
        if f != root and root not in f.parents:
            return None
        if f.is_dir():
            f = f / "index.html"
        return f if f.is_file() else None
    except (OSError, ValueError):
        return None


async def serve(path: str, request: Request) -> Response:
    """GET /help/<path>."""
    if not await _signed_in(request):
        target = "/help/" + path + (f"?{request.url.query}" if request.url.query else "")
        from urllib.parse import quote

        return RedirectResponse("/login?next=" + quote(target, safe=""), status_code=302,
                                headers={"Cache-Control": "no-store", "X-Robots-Tag": ROBOTS})
    root = help_dir()
    if not (root / "index.html").is_file():
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>Help</title>"
            "<p>This install has no help pages. The user guide is built into the Mailr image; "
            "in a development checkout, run tools/build-help.</p>",
            status_code=404, headers=_headers("no-store"))
    # A folder without its slash: the page's relative links need it.
    if path and not path.endswith("/") and not _too_long(path) and _is_dir(root / path) and _resolve(path):
        return RedirectResponse("/help/" + path + "/", status_code=308, headers={"X-Robots-Tag": ROBOTS})
    f = _resolve(path)
    if f is None:
        missing = root / "404.html"
        return FileResponse(missing, status_code=404, headers=_headers("private, no-cache"), media_type="text/html") \
            if missing.is_file() else HTMLResponse("not found", status_code=404, headers=_headers("no-store"))
    immutable = _HASHED.search(f.name) is not None
    return FileResponse(f, headers=_headers("private, max-age=31536000, immutable" if immutable else "private, no-cache"))
