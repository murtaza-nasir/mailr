"""The interactive API reference (/api/v1/docs) and the OpenAPI document (/api/v1/openapi.json).

They map the whole API, so they are served only to a signed-in person (a browser session, not an
agent token) who may make API tokens: an admin, or a member whose access policy has "Agents &
API tokens" on (core/access.py, the check POST /tokens makes). Everyone else, signed out
included, gets the same 404 as a path that does not exist.

Swagger UI is served from this server, never a CDN, so the page keeps a strict policy: its two
files (swagger-ui-dist, Apache-2.0) are pinned by checksum. The image copies them into
/app/vendor/swagger-ui (Dockerfile); a development checkout downloads them once into
<data dir>/vendor/, as site/build_help.py does with Mermaid. The page's start script is a file too, so
no inline script is needed.
"""

from __future__ import annotations

import hashlib
import shutil
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.config import get_settings
from mailr.core.auth import Principal, optional_principal
from mailr.db import get_db
from mailr.i18n import ApiError

router = APIRouter()

SWAGGER_VERSION = "5.17.14"
SWAGGER_FILES = {
    "swagger-ui-bundle.js": "c2e4a9ef08144839ff47c14202063ecfe4e59e70a4e7154a26bd50d880c88ba1",
    "swagger-ui.css": "40170f0ee859d17f92131ba707329a88a070e4f66874d11365e9a77d232f6117",
}
VENDOR = Path("/app/vendor/swagger-ui")

# Swagger UI sets inline style attributes; scripts and everything else come from this origin only.
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mailr API</title><link rel="stylesheet" href="/api/v1/docs/swagger-ui.css"></head>
<body><div id="swagger-ui"></div>
<script src="/api/v1/docs/swagger-ui-bundle.js"></script><script src="/api/v1/docs/start.js"></script>
</body></html>
"""

START = """window.ui = SwaggerUIBundle({
  url: '/api/v1/openapi.json', dom_id: '#swagger-ui', layout: 'BaseLayout', deepLinking: true,
  presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
})
"""

_checked: dict[str, Path] = {}


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def swagger_file(name: str) -> Path | None:
    """The vendored file, checked against its pin; None when it is not there (a development
    checkout downloads it once)."""
    if name in _checked:
        return _checked[name]
    want = SWAGGER_FILES[name]
    cache = Path(get_settings().data_dir) / "vendor" / f"swagger-ui-{SWAGGER_VERSION}"
    for d in (VENDOR, cache):
        p = d / name
        if p.is_file() and _sha256(p) == want:
            _checked[name] = p
            return p
    if not get_settings().dev:
        return None
    url = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_VERSION}/{name}"
    cache.mkdir(parents=True, exist_ok=True)
    tmp = cache / f"{name}.part"
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
    except OSError:
        return None
    if _sha256(tmp) != want:
        tmp.unlink(missing_ok=True)
        return None
    p = cache / name
    tmp.replace(p)
    _checked[name] = p
    return p


async def may_read_api_docs(db: AsyncSession, p: Principal | None) -> bool:
    """Signed in with a session, and allowed to make API tokens."""
    if p is None or p.is_token or p.user is None:
        return False
    from mailr.core import access

    return (await access.for_user(db, p.user)).feature("api_tokens")


async def _guard(p: Principal | None = Depends(optional_principal), db: AsyncSession = Depends(get_db)) -> None:
    if not await may_read_api_docs(db, p):
        raise ApiError(404, "common.not_found")


@router.get("/docs", include_in_schema=False, dependencies=[Depends(_guard)])
async def api_docs() -> HTMLResponse:
    return HTMLResponse(PAGE, headers={"Content-Security-Policy": CSP, "Cache-Control": "no-store"})


@router.get("/docs/start.js", include_in_schema=False, dependencies=[Depends(_guard)])
async def api_docs_start() -> Response:
    return Response(START, media_type="text/javascript")


@router.get("/docs/{name}", include_in_schema=False, dependencies=[Depends(_guard)])
async def api_docs_file(name: str) -> FileResponse:
    if name not in SWAGGER_FILES or (p := swagger_file(name)) is None:
        raise ApiError(404, "common.not_found")
    return FileResponse(p, media_type="text/javascript" if name.endswith(".js") else "text/css")


@router.get("/openapi.json", include_in_schema=False, dependencies=[Depends(_guard)])
async def openapi_document(request: Request) -> JSONResponse:
    return JSONResponse(request.app.openapi())
