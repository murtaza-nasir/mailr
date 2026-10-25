"""Household invites (core/invites.py): the admin makes, lists and withdraws them; the member's
invite page reads one and accepts it.

- POST /admin/invites {email, name?, days?, defaults?}: the link (shown once), the phone link and
  the expiry. GET /admin/invites lists them without tokens; DELETE withdraws one.
- GET /invites/{token}: public. What the invite page needs: the address, the name, whether the
  page may set a password, how to sign in, and the phone links. An unknown token answers 404.
- POST /invites/{token}/accept {password?}: signed in, the invite applies to the signed-in
  account when the address matches. Signed out, a password makes or finishes the account where
  core/invites.may_set_password allows it, and signs in.

The phone link carries the invite's phone form (core/invites.app_token). It is accepted
only signed in, for the invited address; the invite page and the password path refuse it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.api.schemas import user_out
from mailr.config import get_settings
from mailr.core import invites
from mailr.core.audit import audit
from mailr.core.auth import Principal, admin, create_session, hash_password, optional_principal
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import HouseholdInvite, User

router = APIRouter(tags=["invites"])


class InviteIn(BaseModel):
    email: str = Field(max_length=320)
    name: str = Field("", max_length=200)
    days: int | None = None
    defaults: dict[str, Any] | None = None


# ACT household.invite @api
@router.post("/admin/invites")
async def create_invite(body: InviteIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    try:
        inv, token = await invites.create(db, p.user, body.email, body.name, body.days, body.defaults)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    await audit(db, p, "admin.invite", inv.email, {"days": body.days, "defaults": sorted((inv.defaults or {}).keys())},
                always=True)
    await db.commit()
    existing = await invites.account_for(db, inv)
    url = invites.join_url(token)
    # `mail`: the subject and text for sending the link by mail, in the new member's language.
    return {**invites.out(inv, made_by=p.user.name or p.user.email), "url": url,
            "app_url": invites.app_url(token), "has_account": existing is not None,
            "mail": invites.invite_mail(inv, url, p.user)}


@router.get("/admin/invites")
async def list_invites(p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(HouseholdInvite, User.name, User.email).outerjoin(
        User, User.id == HouseholdInvite.created_by).order_by(HouseholdInvite.id.desc()).limit(200))).all()
    return {"items": [invites.out(inv, made_by=n or e) for inv, n, e in rows]}


# ACT household.invite_revoke @api
@router.delete("/admin/invites/{iid}")
async def revoke_invite(iid: int, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    from datetime import UTC, datetime

    inv = await db.get(HouseholdInvite, iid)
    if inv is None:
        raise ApiError(404, "invites.invite_not_found")
    if inv.used_at is None and inv.revoked_at is None:
        inv.revoked_at = datetime.now(UTC)
    await db.commit()
    return invites.out(inv)


def _sign_in_info() -> dict[str, Any]:
    from mailr.api.auth_routes import _oidc_name

    s = get_settings()
    oidc = bool(s.oidc_issuer and s.oidc_client_id)
    return {"oidc": oidc, "oidc_name": _oidc_name() if oidc else "", "local_login": s.local_login}


@router.get("/invites/{token}")
async def read_invite(token: str, db: AsyncSession = Depends(get_db),
                      p: Annotated[Principal | None, Depends(optional_principal)] = None):
    """Public: the invite page's facts. Never which other accounts exist."""
    inv = await invites.by_token(db, token)
    if inv is None:
        raise ApiError(404, "invites.link_not_valid")
    acct = await invites.account_for(db, inv)
    from mailr.api.onboarding_routes import apk_url

    signed_in = None
    if p is not None and not p.is_token:
        signed_in = {"email": p.user.email, "matches": p.user.email.lower() == inv.email.lower()}
    return {"state": invites.state(inv), "email": inv.email, "name": inv.name, "server": invites.base_url(),
            "has_account": acct is not None, "set_password": invites.may_set_password(acct),
            "sign_in": _sign_in_info(), "signed_in": signed_in,
            "expires_at": inv.expires_at.isoformat(),
            "phone": {"app_url": invites.app_url(token), "apk_url": apk_url(),
                      "get_app": invites.base_url() + "/get-app"}}


class AcceptIn(BaseModel):
    password: str | None = Field(None, max_length=500)


# ACT household.join @api
@router.post("/invites/{token}/accept")
async def accept_invite(token: str, body: AcceptIn, request: Request, response: Response,
                        db: AsyncSession = Depends(get_db),
                        p: Annotated[Principal | None, Depends(optional_principal)] = None):
    from mailr.core import throttle

    ip = request.client.host if request.client else ""
    buckets = [(throttle.SIGNUP_PER_IP, (ip,))]
    if invites.is_app_token(token):
        # The phone form: only for a signed-in account with the invited address, and one
        # whose address is verified: with sign-up on, anyone can make an account for an address
        # they do not own. The full token proves the mailbox by itself.
        signed_in = p is not None and not p.is_token and bool(getattr(p.user, "email_verified", True))
        inv = await invites.by_app_token(db, token, p.user.email, lock=True) if signed_in else None
    else:
        inv = await invites.by_token(db, token, lock=True)
    if inv is None:
        await throttle.attempt(db, buckets, buckets, "too many tries; wait a while")
        raise ApiError(404, "invites.link_not_valid")
    st = invites.state(inv)
    if st != "ok":
        raise ApiError(410, f"invites.{st}", fallback="This invite cannot be used. Ask for a new one.")
    if p is not None and not p.is_token:
        if p.user.email.lower() != inv.email.lower():
            raise ApiError(403, "invites.wrong_account", invited=inv.email, current=p.user.email)
        user = await db.get(User, p.user_id)
        result = await invites.accept(db, inv, user)
        await audit(db, p, "invite.accept", inv.email, always=True)
        await db.commit()
        return {**result, "user": user_out(user)}
    # Signed out: only the password path works here; single sign-on comes back signed in.
    acct = await invites.account_for(db, inv)
    if not body.password:
        raise ApiError(401, "invites.sign_in_first")
    if not invites.may_set_password(acct):
        raise ApiError(409, "invites.signs_in_another_way")
    if len(body.password) < 10:
        raise ApiError(400, "invites.password_min_10")
    if acct is None:
        from mailr.core import onboarding
        from mailr.core.users import create_user

        acct = await create_user(db, inv.email, inv.name, password=body.password)
        onboarding.mark_new(acct)
    else:
        acct.password_hash = hash_password(body.password)
    result = await invites.accept(db, inv, acct)
    await db.commit()
    device = request.headers.get("x-mailr-device")
    raw, sess = await create_session(db, acct, request.headers.get("user-agent", ""), ip, device=device or "web")
    from mailr.api.auth_routes import _cookie

    _cookie(response, raw, acct)
    return {**result, "user": user_out(acct), "csrf": sess.csrf, "session_token": raw if device else None}
