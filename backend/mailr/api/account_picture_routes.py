"""The account picture (core/account_picture.py) and the publishing helpers
(core/picture_checks.py).

Reading:
- GET /me/picture and GET /users/me/avatar: the caller's own account picture. A session, or an
  API token with `read` or `profile`. `profile` is the least a token needs: it reads the user's
  name, address and picture and nothing else (the Speakr integration uses it);
- GET /pictures/local (`read`): hashed addresses of this instance's users that have a picture,
  limited to the caller's own identities and correspondents, so clients ask /avatars/{email}
  only for those (avatar_routes serves them);
- GET /me/picture/providers (`read`): the caller's identities with their provider.

Changing (a signed-in user, never an API token): PUT and DELETE /me/picture,
POST /me/picture/use-sign-in.

Helpers (signed-in user): POST /me/picture/publish-check (Gravatar and Libravatar per identity,
or for the identities named in the body; an explicit button press because it tells those services
about the addresses) and POST /me/picture/bimi-check (one identity's domain; admins only).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import account_picture as ap
from mailr.core.auth import Principal, current, scope
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import Connector, Identity

router = APIRouter(tags=["avatars"])


def _serve(data: bytes, ctype: str) -> Response:
    return Response(data, media_type=ctype, headers={
        "Cache-Control": "private, max-age=86400",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    })


def _user_only(p: Principal) -> None:
    if p.is_token:
        raise ApiError(403, "pictures.changed_by_user")


def _may_read_own(p: Principal) -> None:
    if p.is_token and not (p.has("read") or p.has("profile")):
        raise HTTPException(403, "missing scope: profile")  # i18n: internal


@router.get("/me/picture")
async def my_picture(p: Principal = Depends(current)):
    _may_read_own(p)
    got = ap.current(p.user_id)
    if got is None:
        raise ApiError(404, "pictures.no_picture")
    return _serve(*got)


@router.get("/users/me/avatar")
async def my_avatar(p: Principal = Depends(current)):
    """Stable address for integrations (Speakr): the account picture of the token's user."""
    return await my_picture(p)


@router.get("/me/picture/info")
async def my_picture_info(p: Principal = Depends(current)):
    _may_read_own(p)
    return ap.info(p.user_id)


# ACT settings.picture_upload @api
@router.put("/me/picture")
async def upload_picture(file: UploadFile = File(...), p: Principal = Depends(current)):
    _user_only(p)
    data = await file.read(ap.MAX_UPLOAD + 1)
    try:
        png, ctype = await ap.reencode_async(data)
    except ap.BadPicture as e:
        raise HTTPException(413 if len(data) > ap.MAX_UPLOAD else 400, str(e)) from None
    ap.store(p.user_id, png, ctype, "upload")
    return ap.info(p.user_id)


# ACT settings.picture_remove @api
@router.delete("/me/picture")
async def remove_picture(p: Principal = Depends(current)):
    _user_only(p)
    ap.remove(p.user_id)
    return ap.info(p.user_id)


# ACT settings.picture_use_sign_in @api
@router.post("/me/picture/use-sign-in")
async def use_sign_in_picture(p: Principal = Depends(current)):
    """Replace the picture with the sign-in provider's (the URL from the last sign-in)."""
    _user_only(p)
    url = ap.read_meta(p.user_id).get("idp_url")
    if not url:
        raise ApiError(404, "pictures.no_provider_picture")
    if ap.picture_url_kind(url) is None:
        raise ApiError(409, "pictures.provider_host_blocked")
    outcome = await ap.refresh_from_idp(p.user_id, {"picture": url}, force=True)
    if outcome != "stored":
        raise ApiError(502, "pictures.provider_fetch_failed")
    return ap.info(p.user_id)


@router.get("/pictures/local")
async def local_pictures(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    return {"keys": await ap.local_map(db, p.user_id)}


# ---------------------------------------------------------------- publishing helpers


async def _egress(db: AsyncSession) -> str:
    from mailr.core import egress as content

    try:
        return await content.gate(await content.configured(db))
    except content.EgressUnavailable as e:
        raise HTTPException(503, str(e)) from None


class PublishCheckIn(BaseModel):
    identity_ids: list[int] | None = None


# ACT settings.picture_publish_check @api
@router.post("/me/picture/publish-check")
async def publish_check(body: PublishCheckIn | None = None, p: Principal = Depends(current),
                        db: AsyncSession = Depends(get_db)):
    """Per identity: Gravatar/Libravatar found or not, and the provider whose account photo
    other people see (Google, Microsoft). `identity_ids` limits the check to those identities
    (the web asks only about addresses that send through neither Google nor Microsoft)."""
    import asyncio

    from mailr.core import picture_checks as pc

    _user_only(p)
    egress = await _egress(db)
    q = (select(Identity, Connector).outerjoin(Connector, Connector.id == Identity.connector_id)
         .where(Identity.user_id == p.user_id).order_by(Identity.id))
    if body is not None and body.identity_ids is not None:
        q = q.where(Identity.id.in_(body.identity_ids))
    rows = (await db.execute(q)).all()
    results = await asyncio.gather(*(pc.gravatar_status(i.email, egress) for i, _ in rows))
    return {"items": [{"identity_id": i.id, "email": i.email, **res, "provider": pc.provider_for(i.email, c),
                       "shared_domain": pc.domain_of(i.email) in pc.SHARED_DOMAINS}
                      for (i, c), res in zip(rows, results, strict=True)]}


@router.get("/me/picture/providers")
async def providers(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Per identity, without asking anyone: the provider (google, microsoft or null) and whether
    the domain belongs to a shared mailbox provider. This lists sending addresses and
    accounts, so a token needs `read` (not just `profile`), and a token limited to some accounts
    or without local-only access sees only the identities of the accounts it may see."""
    from mailr.core import picture_checks as pc

    if p.is_token and not p.has("read"):
        raise HTTPException(403, "missing scope: read")  # i18n: internal
    rows = (await db.execute(select(Identity, Connector).outerjoin(Connector, Connector.id == Identity.connector_id)
                             .where(Identity.user_id == p.user_id).order_by(Identity.id))).all()
    if p.is_token:
        rows = [(i, c) for i, c in rows
                if (p.can_see_connector(c) if c is not None else not p.token.connector_ids)]
    return {"items": [{"identity_id": i.id, "email": i.email, "provider": pc.provider_for(i.email, c),
                       "domain": pc.domain_of(i.email), "shared_domain": pc.domain_of(i.email) in pc.SHARED_DOMAINS}
                      for i, c in rows]}


class BimiIn(BaseModel):
    identity_id: int


# ACT settings.picture_bimi_check @api
@router.post("/me/picture/bimi-check")
async def bimi_check(body: BimiIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Admins only: BIMI is set up by whoever runs the domain's DNS, and the check makes DNS and
    logo lookups from the server."""
    from mailr.core import picture_checks as pc

    _user_only(p)
    if p.user.role != "admin":
        raise ApiError(403, "auth.admin_only")
    i = await db.get(Identity, body.identity_id)
    if i is None or i.user_id != p.user_id:
        raise ApiError(404, "identities.identity_not_found")
    domain = pc.domain_of(i.email)
    if domain in pc.SHARED_DOMAINS:
        return await pc.bimi_check(domain, "")  # answers "not applicable" without a lookup
    return await pc.bimi_check(domain, await _egress(db))
