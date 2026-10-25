"""The web app's files: precompressed and cached (14 Oct 2026).

The image build writes a Brotli (`.br`) and a gzip (`.gz`) copy beside every compressible file of
the web build (frontend/scripts/precompress.mjs, run in the Dockerfile). A request that accepts
one of them gets that copy with `Content-Encoding`, Brotli first; any other gets the file itself.
Nothing is compressed per request.

Caching:

- `/assets/*` carry a content hash in their names, so a browser may keep them for a year without
  asking again (`immutable`). A new build has new names.
- `index.html`, `sw.js` and the web manifest are asked for again every time (`no-cache`: the
  browser revalidates with the ETag and usually gets a 304). That is how a new build reaches the
  browser, and how the service worker notices it (public/sw.js).
- The other files at the top (icons, start-up images) are not hashed and rarely change: a day.

Development (vite) and a build without the compressed copies serve the plain files, with the same
cache headers.
"""

from __future__ import annotations

import os
import stat
from email.utils import parsedate
from mimetypes import guess_type
from pathlib import Path

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles
from starlette.types import Scope

IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"
DAY = "public, max-age=86400"
# Brotli first: about 15-20 % smaller than gzip on the app's scripts and locale tables.
ENCODINGS = (("br", ".br"), ("gzip", ".gz"))
_ALWAYS_FRESH = {"index.html", "sw.js", "manifest.webmanifest"}


def accepted(header: str) -> set[str]:
    """The content codings an Accept-Encoding header allows (q=0 refuses one)."""
    out: set[str] = set()
    for part in header.split(","):
        name, _, params = part.strip().partition(";")
        name = name.strip().lower()
        if not name:
            continue
        q = 1.0
        for p in params.split(";"):
            k, _, v = p.strip().partition("=")
            if k.strip().lower() == "q":
                try:
                    q = float(v)
                except ValueError:
                    q = 0.0
        if q > 0:
            out.add(name)
    if "*" in out:
        out |= {e for e, _ in ENCODINGS}
    return out


def cache_control_for(name: str, hashed: bool) -> str:
    if hashed:
        return IMMUTABLE
    return REVALIDATE if name in _ALWAYS_FRESH else DAY


def file_response(full_path: str | os.PathLike[str], stat_result: os.stat_result | None, request_headers: Headers,
                  cache_control: str, status_code: int = 200) -> Response:
    """The file, or its precompressed copy when the request accepts one, with these cache headers."""
    path = os.fspath(full_path)
    media_type = guess_type(path)[0] or "text/plain"
    headers = {"Cache-Control": cache_control}
    want = accepted(request_headers.get("accept-encoding", ""))
    variant: tuple[str, os.stat_result, str] | None = None
    has_variants = False
    for enc, ext in ENCODINGS:
        try:
            st = os.stat(path + ext)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        has_variants = True
        if variant is None and enc in want:
            variant = (path + ext, st, enc)
    if has_variants:
        headers["Vary"] = "Accept-Encoding"
    if variant is not None:
        headers["Content-Encoding"] = variant[2]
        response: Response = FileResponse(variant[0], status_code=status_code, stat_result=variant[1],
                                          media_type=media_type, headers=headers)
    else:
        if stat_result is None:
            # FileResponse sets the ETag from the stat result; without one it is set only when the
            # response is sent, too late for the If-None-Match comparison below.
            try:
                stat_result = os.stat(path)
            except OSError:
                pass
        response = FileResponse(path, status_code=status_code, stat_result=stat_result, media_type=media_type,
                                headers=headers)
    if _not_modified(response.headers, request_headers):
        return NotModifiedResponse(response.headers)
    return response


def _not_modified(response_headers: Headers, request_headers: Headers) -> bool:
    """Starlette's StaticFiles rule: the ETag (per coding, as each copy has its own) or the date."""
    if if_none_match := request_headers.get("if-none-match"):
        if if_none_match.strip() == "*":
            return True
        etag = response_headers.get("etag")
        return bool(etag) and etag in [tag.strip().removeprefix("W/") for tag in if_none_match.split(",")]
    since = parsedate(request_headers.get("if-modified-since", ""))
    modified = parsedate(response_headers.get("last-modified", ""))
    return since is not None and modified is not None and since >= modified


class HashedAssets(StaticFiles):
    """`/assets`: hashed names, precompressed copies, kept for a year."""

    def file_response(self, full_path, stat_result: os.stat_result, scope: Scope, status_code: int = 200) -> Response:
        return file_response(full_path, stat_result, Headers(scope=scope), IMMUTABLE, status_code)


def top_level(path: Path, request: Request) -> Response:
    """A file at the top of the build (index.html, sw.js, icons)."""
    return file_response(path, None, request.headers, cache_control_for(path.name, hashed=False))
