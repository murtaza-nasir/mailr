"""Idempotency for every mutating API request.

A client (the web UI, the Android app replaying an offline queue, an agent) sends
`Idempotency-Key: <unique id>` with POST/PUT/PATCH/DELETE. The key is namespaced by the
caller's credential (session cookie or bearer token), the method, and the path, so one caller
can never receive another caller's or another endpoint's response.

Only live, authenticated callers are served: before a key is stored or replayed, the
credential must still be valid (a session that exists and has not expired, with the CSRF header
for a cookie; a token that is not revoked or expired; a user who is not disabled). A refused or
missing credential goes straight to the route, which answers 401/403, and leaves no row.

The key row is inserted BEFORE the request runs (status pending). A concurrent duplicate
waits for the first one to finish and gets its response; a later replay gets the stored
response with `Idempotent-Replayed: true`. Server errors (5xx) release the key so the client
can retry. Only JSON responses are stored, encrypted (AES-256-GCM) with a key derived from the
instance key and the caller's credential, which the database never holds: a stored tool result
or message is unreadable without the cookie or token that asked for it. Rows live 24 hours
(core/tasks.py cleanup; a replay of an older row is refused).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mailr.core.auth import SESSION_COOKIE
from mailr.db import session_scope

log = logging.getLogger("mailr.idempotency")

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
WAIT_SECONDS = 15
TTL = timedelta(hours=24)


def _credential(request: Request) -> tuple[str, str] | None:
    """("bearer" | "cookie", raw credential) or None."""
    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        raw = authz[7:].strip()
        return ("bearer", raw) if raw else None
    raw = request.cookies.get(SESSION_COOKIE, "")
    return ("cookie", raw) if raw else None


def _key(request: Request, client_key: str) -> str | None:
    cred = _credential(request)
    if cred is None:
        return None
    raw = "\x00".join([hashlib.sha256(cred[1].encode()).hexdigest(), request.method, request.url.path, client_key])
    return hashlib.sha256(raw.encode()).hexdigest()


async def _live_user(request: Request) -> int | None:
    """The user id behind a still-valid credential (with a matching CSRF header for a cookie), or
    None. The same checks as core/auth.optional_principal, without side effects."""
    from mailr.core.crypto import sha256_hex
    from mailr.models import ApiToken, Session, User

    cred = _credential(request)
    if cred is None:
        return None
    kind, raw = cred
    now = datetime.now(UTC)
    async with session_scope() as db:
        if kind == "bearer":
            t = (await db.execute(select(ApiToken).where(ApiToken.token_hash == sha256_hex(raw)))).scalar_one_or_none()
            if t is None or t.revoked or (t.expires_at and t.expires_at < now):
                return None
            uid = t.user_id
        else:
            sess = await db.get(Session, sha256_hex(raw))
            if sess is None or sess.expires_at < now:
                return None
            sent = request.headers.get("x-csrf-token", "")
            if not sent or not secrets.compare_digest(sent.encode(), (sess.csrf or "").encode()):
                return None
            uid = sess.user_id
        u = await db.get(User, uid)
        return None if u is None or u.disabled else uid


def _cipher_key(request: Request) -> bytes:
    from mailr.core.crypto import _instance_key

    cred = (_credential(request) or ("", ""))[1]
    return hmac.new(_instance_key(), b"mailr-idempotency\x00" + cred.encode(), hashlib.sha256).digest()


def _seal(request: Request, key: str, data) -> dict:
    nonce = os.urandom(12)
    ct = AESGCM(_cipher_key(request)).encrypt(nonce, json.dumps(data).encode(), key.encode())
    return {"_enc": base64.b64encode(nonce + ct).decode()}


def _open(request: Request, key: str, stored):
    if not isinstance(stored, dict) or "_enc" not in stored:
        raise ValueError("not an encrypted response")
    blob = base64.b64decode(stored["_enc"])
    return json.loads(AESGCM(_cipher_key(request)).decrypt(blob[:12], blob[12:], key.encode()))


_SECRET_PATHS = ("/api/v1/tokens", "/api/v1/me/imap/app-password", "/api/v1/auth/")


def _carries_secret(request: Request) -> bool:
    p = request.url.path
    return request.method == "POST" and any(p == x or (x.endswith("/") and p.startswith(x)) for x in _SECRET_PATHS)


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client_key = request.headers.get("idempotency-key")
        if (request.method not in MUTATING or not client_key or not request.url.path.startswith("/api/")
                or len(client_key) > 200):
            return await call_next(request)
        key = _key(request, client_key)
        if key is None:
            return await call_next(request)
        # A revoked, expired or junk credential (or a cookie without its CSRF header) is never
        # replayed and never stored: the route itself refuses it.
        uid = await _live_user(request)
        if uid is None:
            return await call_next(request)

        async with session_scope() as db:
            inserted = (await db.execute(text(
                "INSERT INTO idempotency_keys (key, user_id, status, status_code, response) "
                "VALUES (:k, :u, 'pending', 0, NULL) ON CONFLICT (key) DO NOTHING RETURNING key"),
                {"k": key, "u": uid})).first()
        if not inserted:
            return await self._replay(request, key)

        request.state.idempotency = True  # creates scope["state"], shared with the route's request
        try:
            response = await call_next(request)
        except Exception:
            await self._release(key)
            raise
        # Not kept: server errors (retryable), refused credentials (no rows for junk callers),
        # non-JSON, and responses that carry a secret (a new API token, an app password, a
        # session) which must not sit in the database in plain text.
        if (response.status_code >= 500 or response.status_code in (401, 403)
                or "application/json" not in response.headers.get("content-type", "")
                or _carries_secret(request)):
            await self._release(key)
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        try:
            data = json.loads(body) if body else None
        except ValueError:
            await self._release(key)
            return Response(body, status_code=response.status_code, headers=dict(response.headers))
        if getattr(request.state, "principal", None) is None:
            # The route did not authenticate the caller (a public endpoint): nothing to keep.
            await self._release(key)
            return Response(body, status_code=response.status_code,
                            headers={k: v for k, v in response.headers.items() if k.lower() != "content-length"})
        async with session_scope() as db:
            await db.execute(text(
                "UPDATE idempotency_keys SET status='done', status_code=:c, response=CAST(:r AS jsonb) WHERE key=:k"),
                {"c": response.status_code, "r": json.dumps(_seal(request, key, data)), "k": key})
        headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length",)}
        return Response(body, status_code=response.status_code, headers=headers)

    async def _replay(self, request: Request, key: str) -> Response:
        for _ in range(WAIT_SECONDS * 4):
            async with session_scope() as db:
                row = (await db.execute(text(
                    "SELECT status, status_code, response, created_at FROM idempotency_keys WHERE key=:k"),
                    {"k": key})).first()
            if row is None:
                return JSONResponse({"detail": "the earlier request with this Idempotency-Key failed; retry"},
                                    status_code=409)
            if row.status == "done":
                if row.created_at < datetime.now(UTC) - TTL:
                    return JSONResponse({"detail": "this Idempotency-Key has expired; use a new key"}, status_code=409)
                try:
                    data = _open(request, key, row.response)
                except Exception:  # noqa: BLE001 - an old plaintext row or a foreign credential
                    return JSONResponse({"detail": "the earlier response with this Idempotency-Key cannot be "
                                                   "replayed; use a new key"}, status_code=409)
                return JSONResponse(data, status_code=row.status_code or 200,
                                    headers={"Idempotent-Replayed": "true"})
            await asyncio.sleep(0.25)
        return JSONResponse({"detail": "a request with this Idempotency-Key is still in progress"}, status_code=409)

    async def _release(self, key: str) -> None:
        try:
            async with session_scope() as db:
                await db.execute(text("DELETE FROM idempotency_keys WHERE key=:k"), {"k": key})
        except Exception:
            log.exception("could not release idempotency key")
