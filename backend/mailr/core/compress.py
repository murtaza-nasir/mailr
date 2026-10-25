"""gzip for the API's JSON (14 Oct 2026).

A list page is 25-30 kB of JSON and a people page about 30 kB; gzip makes them 4-6 times smaller.
Only text the API makes per request is compressed: JSON, plain text, HTML, XML, calendar files.
Attachments, images and other files go as they are (most are compressed already, and compressing a
large one would only cost the server time). The web app's own files come precompressed
(core/webstatic.py) and carry their Content-Encoding, which Starlette leaves alone.

Left out:

- `/api/v1/auth/*`: the session and sign-in answers carry the CSRF token next to text a page can
  make the server reflect, which is what length-based attacks on compression (BREACH) need.
- event streams (Starlette's own rule), partial responses (206) and bodies under 1 kB.
"""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipResponder
from starlette.types import ASGIApp, Message, Receive, Scope, Send

COMPRESSIBLE = frozenset({
    "application/json", "application/problem+json", "application/manifest+json", "application/xml",
    "text/plain", "text/html", "text/xml", "text/csv", "text/calendar", "text/markdown",
})
MINIMUM_SIZE = 1024
EXCLUDED_PREFIXES = ("/api/v1/auth/",)


class _Responder(GZipResponder):
    """Starlette's responder, compressing only the types above."""

    async def send_with_compression(self, message: Message) -> None:
        await super().send_with_compression(message)
        if message["type"] == "http.response.start":
            media = Headers(raw=message["headers"]).get("content-type", "").partition(";")[0].strip().lower()
            if media not in COMPRESSIBLE:
                self.content_type_is_excluded = True


class ApiGZip:
    def __init__(self, app: ASGIApp, compresslevel: int = 6) -> None:
        self.app = app
        self.compresslevel = compresslevel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/") or scope["path"].startswith(EXCLUDED_PREFIXES):
            await self.app(scope, receive, send)
            return
        if "gzip" not in Headers(scope=scope).get("accept-encoding", ""):
            await self.app(scope, receive, send)
            return
        responder = _Responder(self.app, MINIMUM_SIZE, compresslevel=self.compresslevel)
        await responder(scope, receive, send)
