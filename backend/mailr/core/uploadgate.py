"""Uploads are authenticated before their body is read, and only a few run at once.

FastAPI parses a multipart body (File, Form) before the route's dependencies run, and Starlette
spools every file part to TMPDIR (the data volume) on the way. Without this gate anyone could make
the server write up to a route's body limit to disk per request without signing in, and a
signed-in user could pass the import quota and the disk reserve while the spool was written.

UploadGate sits just inside BodySizeLimit. For a multipart request, and for the large upload routes
whatever their content type, it:

- resolves the caller from the headers (core/auth.resolve_principal: an API token, or a session with
  its CSRF token) and answers 401 or 403 without reading the body; `/api/v1/admin/` uploads need a
  signed-in admin, as the routes do;
- takes a slot: at most UPLOADS_PER_USER uploads of one user and UPLOADS_AT_ONCE in all are read at
  the same time. A request waits up to SLOT_WAIT seconds for one (its body is not read meanwhile),
  then gets 429.

The large uploads (imports, recordings, phone apps) do not use UploadFile at all: they stream to
their destination and check the quota and the free disk while they write (core/streamupload.py).
So what Starlette spools is bounded by the slots times MAILR_MAX_REQUEST_BYTES (or the attachment
limit).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from mailr.i18n import ApiError

UPLOADS_PER_USER = 4
UPLOADS_AT_ONCE = 24
SLOT_WAIT = 60.0

# The routes that take large uploads (main._upload_limits): gated whatever the content type.
LARGE_UPLOADS = ("/api/v1/connectors/uploads", "/api/v1/speakr/recordings", "/api/v1/admin/phone-apps/")


def gated(scope) -> bool:
    if scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
        return False
    path = scope.get("path", "")
    if not path.startswith("/api/"):
        return False
    if path.rstrip("/").startswith(LARGE_UPLOADS[:2]) or path.startswith(LARGE_UPLOADS[2]):
        return True
    ctype = dict(scope.get("headers") or []).get(b"content-type", b"")
    return ctype.lower().lstrip().startswith(b"multipart/")


class _Slots:
    """Upload slots per user and in all, in this process."""

    def __init__(self) -> None:
        self.users: dict[int, int] = defaultdict(int)
        self.total = 0
        self.changed = asyncio.Condition()

    def _free(self, uid: int) -> bool:
        return self.total < UPLOADS_AT_ONCE and self.users[uid] < UPLOADS_PER_USER

    async def take(self, uid: int, wait: float) -> bool:
        async with self.changed:
            try:
                await asyncio.wait_for(self.changed.wait_for(lambda: self._free(uid)), wait)
            except TimeoutError:
                if not self.users[uid]:
                    self.users.pop(uid, None)
                return False
            self.users[uid] += 1
            self.total += 1
            return True

    async def give(self, uid: int) -> None:
        async with self.changed:
            self.total -= 1
            self.users[uid] -= 1
            if self.users[uid] <= 0:
                self.users.pop(uid, None)
            self.changed.notify_all()


_slots: _Slots | None = None


def slots() -> _Slots:
    global _slots
    if _slots is None:
        _slots = _Slots()
    return _slots


async def _answer(scope, receive, send, status: int, body: dict) -> None:
    from mailr.core import webheaders

    # Connection: close: the body that was not read is not drained from the connection.
    resp = JSONResponse(body, status_code=status,
                        headers={**webheaders.all_responses(), "Cache-Control": "no-store", "Connection": "close"})
    await resp(scope, receive, send)


class UploadGate:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if not gated(scope):
            await self.app(scope, receive, send)
            return
        from mailr.core.auth import resolve_principal
        from mailr.db import sessionmaker

        request = Request(scope)  # headers only: no receive, so the body cannot be read here
        try:
            async with sessionmaker()() as db:
                p = await resolve_principal(request, db)
                uid = p.user_id if p is not None else None
                is_admin = p is not None and p.user.role == "admin" and not p.is_token
        except HTTPException as e:
            await _answer(scope, receive, send, e.status_code, {"detail": e.detail})
            return
        if uid is None:
            await _answer(scope, receive, send, 401, {"detail": "not signed in"})
            return
        if scope.get("path", "").startswith("/api/v1/admin/") and not is_admin:
            await _answer(scope, receive, send, 403, ApiError(403, "auth.admin_only").body())
            return
        s = slots()
        if not await s.take(uid, SLOT_WAIT):
            await _answer(scope, receive, send, 429, ApiError(429, "common.uploads_busy").body())
            return
        try:
            await self.app(scope, receive, send)
        finally:
            await s.give(uid)
