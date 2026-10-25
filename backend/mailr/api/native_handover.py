"""Sign-in hand-over between the native apps: "Continue as <name>" in the Notes app.

When the mailr app is installed and signed in, the Notes app can sign in without a second trip
through the identity provider:

1. The Notes app makes a random verifier and sends its SHA-256 (the challenge) to the mailr app,
   through an Activity that only an app signed with the same certificate may start.
2. The mailr app asks this server for a hand-over token with its own device session:
   `POST /auth/handover` {app, challenge, device_name}. The token is single-use, lives two minutes,
   is stored only as a hash, and is bound to the challenge and to the session that asked for it.
3. The mailr app hands the token to the Notes app, which redeems it with the verifier:
   `POST /auth/handover/redeem` {token, verifier, device_name} → a device session of its own,
   exactly like a native sign-in (`{session_token, csrf, user, expires_at}`).

Only a signed-in phone (a device session from the Android app) may ask; API tokens and browser
sessions may not. A token whose minting session has ended since (signed out, revoked) is refused.
Both steps are written to the audit log. The new session is an ordinary device session: it is
listed with the others and revoked like them.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.api.schemas import user_out
from mailr.core.audit import audit
from mailr.core.auth import Principal, create_session, current
from mailr.core.crypto import random_token, sha256_hex
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import AuditLog, Session, User

router = APIRouter(tags=["auth"])

TTL_SECONDS = 120
APPS = {"notes": "Notes"}
_KEY = "handover:"
_B64URL43 = r"^[A-Za-z0-9_-]{43}$"


def challenge_of(verifier: str) -> str:
    """PKCE's S256: base64url(sha256(verifier)) without padding."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _key(token: str) -> str:
    return _KEY + sha256_hex(token)[:80]


class MintIn(BaseModel):
    app: str = Field(max_length=20)
    challenge: str = Field(pattern=_B64URL43)
    device_name: str = Field("Android", max_length=100)


@router.post("/auth/handover")
async def mint(body: MintIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """A hand-over token for another of the user's apps on this phone, redeemed with
    POST /auth/handover/redeem."""
    if p.is_token or p.session is None or p.session.device != "android":
        raise ApiError(403, "auth.handover_app_only")
    if body.app not in APPS:
        raise HTTPException(422, "unknown app")  # i18n: internal
    token = random_token(32)
    await db.execute(text(
        "INSERT INTO idempotency_keys (key, user_id, status, status_code, response) "
        "VALUES (:k, :u, 'ready', 200, CAST(:r AS jsonb))"),
        {"k": _key(token), "u": p.user_id, "r": _json({
            "app": body.app, "challenge": body.challenge, "minted_by": p.session.id,
            "device_name": body.device_name})})
    await audit(db, p, "auth.handover.mint", target=body.app, inputs={"device_name": body.device_name}, always=True)
    await db.commit()
    return {"token": token, "expires_in": TTL_SECONDS}


class RedeemIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    verifier: str = Field(min_length=43, max_length=128)
    device_name: str = Field("Android", max_length=100)


@router.post("/auth/handover/redeem")
async def redeem(body: RedeemIn, request: Request, db: AsyncSession = Depends(get_db)):
    """The Notes app's side: the token and the verifier for a device session of its own."""
    from mailr.core import throttle

    ip = request.client.host if request.client else ""
    buckets = [(throttle.NATIVE_PER_IP, (ip,))]
    taken = await throttle.attempt(db, buckets, buckets)  # counted first: parallel tries share the limit

    async def refuse(code: str):
        raise ApiError(401, code)

    # Single use: the row turns 'used' in the same statement that reads it.
    row = (await db.execute(text(
        "UPDATE idempotency_keys SET status='used' WHERE key=:k AND status='ready' "
        f"AND created_at > now() - interval '{int(TTL_SECONDS)} seconds' RETURNING user_id, response"),
        {"k": _key(body.token)})).first()
    await db.commit()
    if row is None:
        await refuse("auth.handover_expired")
    uid, info = row[0], row[1] or {}
    if not secrets.compare_digest(challenge_of(body.verifier), str(info.get("challenge", ""))):
        await _record(db, uid, "auth.handover.redeem", info, ok=False, why="verifier")
        await db.commit()
        await refuse("auth.handover_other_app")
    minted_by = await db.get(Session, str(info.get("minted_by", "")))
    u = await db.get(User, uid)
    if minted_by is None or minted_by.user_id != uid or minted_by.expires_at < datetime.now(UTC) or u is None or u.disabled:
        await _record(db, uid, "auth.handover.redeem", info, ok=False, why="session ended")
        await db.commit()
        await refuse("auth.handover_signed_out")
    # As a native sign-in: the app names itself ("Notes on Pixel 9"), and the sessions list shows it.
    raw, sess = await create_session(
        db, u, user_agent=(request.headers.get("user-agent", "") + f" [{body.device_name}]")[:400],
        ip=ip, device="android")
    await _record(db, uid, "auth.handover.redeem", info, ok=True, session=sess.id)
    await db.commit()
    await throttle.forgive(db, taken)
    return {"session_token": raw, "csrf": sess.csrf, "user": user_out(u), "expires_at": sess.expires_at.isoformat()}


async def _record(db: AsyncSession, uid: int, action: str, info: dict, *, ok: bool, why: str = "",
                  session: str = "") -> None:
    """The redeem has no principal yet: its audit row names the user directly."""
    db.add(AuditLog(user_id=uid, actor="user", action=action, target=str(info.get("app", ""))[:200],
                    inputs={"device_name": str(info.get("device_name", ""))[:100]},
                    result={"why": why} if why else {"session": session[:12]}, ok=ok))


def _json(v: dict) -> str:
    import json

    return json.dumps(v)
