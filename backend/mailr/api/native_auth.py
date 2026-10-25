"""Sign-in for native apps (Android).

The app runs the OIDC authorization-code + PKCE flow itself (AppAuth, in a Custom Tab),
against the same issuer as the web client but with its own public client id. It then hands
the ID token to mailr, which verifies it (signature via the issuer's JWKS, iss, aud, azp, exp,
iat, the nonce's binding to this server) and opens a device session.

The nonce binds the token to this server. The app picks a random seed `r`, asks
the provider for nonce = base64url(SHA-256(origin + "|" + r)) with the origin of the server it is
talking to, and posts `r` with the token. mailr recomputes the nonce with its OWN origin
(MAILR_BASE_URL, plus MAILR_OIDC_NATIVE_ORIGINS) and refuses a mismatch. A token the app obtained
while it talked to another server (a planted setup link, a look-alike host), or one another app
obtained with mailr's public client id, therefore does not sign in here. A sign-in without a
seed comes from an app older than this check: 426 (update the app), unless the admin turned on
MAILR_OIDC_NATIVE_UNBOUND for a short changeover.

The device session is an ordinary mailr session: the app sends it as the `mailr_session`
cookie and echoes `X-CSRF-Token` on mutations, exactly like the browser.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.api.schemas import user_out
from mailr.config import get_settings
from mailr.core.auth import Principal, create_session, current
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import Session, User

router = APIRouter(tags=["auth"])
log = logging.getLogger("mailr.native_auth")

_JWKS: dict[str, tuple[float, dict]] = {}
JWKS_TTL = 3600


def native_client_ids() -> list[str]:
    raw = get_settings().oidc_native_client_id
    return [c.strip() for c in raw.split(",") if c.strip()]


async def _discovery() -> dict:
    # Reuse the web flow's cached discovery document.
    from mailr.api.auth_routes import _discovery as web_discovery

    return await web_discovery()


JWKS_REFRESH_MIN = 60  # seconds between forced refetches (junk tokens must not hammer the IdP)


async def _jwks(uri: str, refresh: bool = False) -> dict:
    now = datetime.now(UTC).timestamp()
    hit = _JWKS.get(uri)
    if hit and (not refresh or now - hit[0] < JWKS_REFRESH_MIN) and now - hit[0] < JWKS_TTL:
        return hit[1]
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(uri)
        r.raise_for_status()
        keys = r.json()
    _JWKS[uri] = (now, keys)
    return keys


async def verify_id_token(id_token: str) -> dict:
    """Verify an ID token issued to one of the configured native clients. Returns claims."""
    from authlib.jose import JsonWebKey, jwt
    from authlib.jose.errors import JoseError

    s = get_settings()
    if not s.oidc_issuer:
        raise ApiError(404, "auth.oidc_not_configured")
    clients = native_client_ids()
    if not clients:
        raise ApiError(404, "auth.native_not_configured")
    d = await _discovery()
    claims = None
    for refresh in (False, True):  # a rotated signing key: refetch the JWKS once
        try:
            keys = JsonWebKey.import_key_set(await _jwks(d["jwks_uri"], refresh=refresh))
            claims = jwt.decode(id_token, keys)
            break
        except HTTPException:
            raise
        except httpx.HTTPError:
            raise ApiError(503, "auth.provider_keys_unavailable") from None
        except Exception:  # noqa: BLE001 - JoseError, ValueError, and the KeyError an HS256 header causes
            if refresh:
                raise ApiError(401, "auth.id_token_signature") from None
    try:
        claims.validate(leeway=60)
    except JoseError as e:
        raise ApiError(401, "auth.id_token_rejected_why", error=str(e.error)) from None
    except Exception:  # noqa: BLE001
        raise ApiError(401, "auth.id_token_rejected") from None
    if claims.get("iss") != d.get("issuer"):
        raise ApiError(401, "auth.id_token_other_provider")
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if not any(a in clients for a in auds):
        raise ApiError(401, "auth.id_token_other_app")
    if len(auds) > 1 and claims.get("azp") not in clients:
        raise ApiError(401, "auth.id_token_azp")
    if "exp" not in claims:
        raise ApiError(401, "auth.id_token_no_expiry")
    iat = claims.get("iat")
    if not isinstance(iat, (int, float)) or datetime.now(UTC).timestamp() - iat > MAX_TOKEN_AGE:
        raise ApiError(401, "auth.id_token_too_old")
    return dict(claims)


MAX_TOKEN_AGE = 600  # seconds: a native ID token is exchanged right after sign-in


def canonical_origin(url: str) -> str:
    """scheme://host[:port][/path] in one spelling: scheme and host in lower case, the default port
    and trailing slashes left out. The apps compute the same (session-core NonceBinding.kt;
    shared cases in backend/tests/fixtures/native_nonce_cases.json)."""
    u = urlsplit((url or "").strip())
    scheme, host = (u.scheme or "").lower(), (u.hostname or "").lower()
    if not scheme or not host:
        return ""
    if ":" in host:
        host = f"[{host}]"
    port = u.port
    netloc = host if port is None or (scheme, port) in (("https", 443), ("http", 80)) else f"{host}:{port}"
    return f"{scheme}://{netloc}{u.path.rstrip('/')}"


def server_origins() -> list[str]:
    """This server's own addresses for the nonce: MAILR_BASE_URL first, then
    MAILR_OIDC_NATIVE_ORIGINS (another address the phones use, such as one on the local network)."""
    s = get_settings()
    out = [canonical_origin(s.base_url)]
    out += [canonical_origin(o) for o in s.oidc_native_origins.split(",") if o.strip()]
    return [o for i, o in enumerate(out) if o and o not in out[:i]]


def bound_nonce(origin: str, seed: str) -> str:
    """The nonce an app asks for when it signs in to `origin` with the random `seed`."""
    digest = hashlib.sha256(f"{canonical_origin(origin)}|{seed}".encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def check_binding(claims: dict, seed: str | None) -> None:
    """The ID token was asked for while the app talked to THIS server."""
    if seed is None:
        if get_settings().oidc_native_unbound:
            log.warning("native sign-in without a server-bound nonce (MAILR_OIDC_NATIVE_UNBOUND is on)")
            return
        raise ApiError(426, "auth.native_update_app")
    nonce = claims.get("nonce")
    if not isinstance(nonce, str) or not any(
            hmac.compare_digest(nonce, bound_nonce(o, seed)) for o in server_origins()):
        raise ApiError(401, "auth.id_token_other_server")


async def _burn(db: AsyncSession, id_token: str) -> None:
    """Each ID token signs in once: a leaked or replayed token cannot mint more sessions.
    Recorded in idempotency_keys (primary key = hash), whose 24-hour cleanup outlives MAX_TOKEN_AGE."""
    import hashlib

    from sqlalchemy import text as sqltext

    key = "idtok:" + hashlib.sha256(id_token.encode()).hexdigest()[:80]
    r = await db.execute(sqltext(
        "INSERT INTO idempotency_keys (key, user_id, status, status_code) VALUES (:k, 0, 'used', 200) "
        "ON CONFLICT (key) DO NOTHING"), {"k": key})
    if r.rowcount == 0:
        raise ApiError(401, "auth.signin_used")


async def user_for_claims(db: AsyncSession, claims: dict, invite: str | None = None) -> User:
    """Same rules as the web flow (core/users.user_for_oidc_claims), with the household invite the
    app was set up from (mailr://setup?…&invite=…)."""
    from mailr.core.users import user_for_oidc_claims

    return await user_for_oidc_claims(db, claims, invite_token=invite)


class NativeIn(BaseModel):
    id_token: str = Field(min_length=20, max_length=16000)
    device_name: str = Field("Android", max_length=100)
    # A household invite (core/invites.py): the app accepts it after sign-in.
    invite: str | None = Field(None, max_length=100)
    # The random seed of the request's nonce (check_binding). Apps before the check send none.
    nonce_seed: str | None = Field(None, min_length=16, max_length=200)


def _oidc_name() -> str:
    from mailr.api.auth_routes import _oidc_name

    return _oidc_name()


def _push_server():
    from mailr.api.push_routes import push_server

    return push_server()


@router.get("/auth/native-config")
async def native_config(app: str = Query("", max_length=20)):
    """The native sign-in settings. `app=notes` (the Notes app) answers its own redirect URI;
    the client id, issuer and scopes are the same for both apps."""
    s = get_settings()
    clients = native_client_ids()
    return {
        "enabled": bool(s.oidc_issuer and clients),
        "issuer": s.oidc_issuer,
        # The provider's name for the sign-in button (MAILR_OIDC_NAME); empty = "single sign-on".
        "provider_name": _oidc_name(),
        "client_id": clients[0] if clients else "",
        "redirect_uri": s.oidc_native_notes_redirect if app == "notes" else s.oidc_native_redirect,
        "scopes": s.oidc_scopes.split(),
        "local_login": s.local_login,
        # The instance's ntfy server, so the phone's push setup needs no typing.
        "push_server": _push_server(),
    }


@router.post("/auth/oidc/native")
async def native_sign_in(body: NativeIn, request: Request, db: AsyncSession = Depends(get_db)):
    from mailr.core import throttle

    buckets = [(throttle.NATIVE_PER_IP, (request.client.host if request.client else "",))]
    taken = await throttle.attempt(db, buckets, buckets)  # counted first: parallel tries share the limit
    try:
        claims = await verify_id_token(body.id_token)
        check_binding(claims, body.nonce_seed)
    except HTTPException as e:
        if e.status_code != 401:
            await throttle.forgive(db, taken)
        raise
    await throttle.forgive(db, taken)
    await _burn(db, body.id_token)
    u = await user_for_claims(db, claims, body.invite)
    await db.commit()
    raw, sess = await create_session(
        db, u, user_agent=(request.headers.get("user-agent", "") + f" [{body.device_name}]")[:400],
        ip=request.client.host if request.client else "", device="android",
    )
    from mailr.api.auth_routes import picture_refresh

    # The provider's picture, after the response (core/account_picture.py). The app hands over
    # only the ID token, so a picture that needs the access token is not fetched here.
    return JSONResponse({"session_token": raw, "csrf": sess.csrf, "user": user_out(u),
                         "expires_at": sess.expires_at.isoformat()},
                        background=picture_refresh(u.id, claims))


@router.post("/auth/native/logout")
async def native_logout(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.session is None:
        raise HTTPException(400, "not a device session")  # i18n: internal
    await db.execute(delete(Session).where(Session.id == p.session.id))
    await db.commit()
    return JSONResponse({"ok": True}, headers={"Clear-Site-Data": '"cache"'})


# "Continue as <name>" in the Notes app (api/native_handover.py), served with the native sign-in.
from mailr.api import native_handover  # noqa: E402

router.include_router(native_handover.router)
