"""Settings → Ambrose → Trusted addresses (core/trusted.py).

Only the signed-in user manages these: an API token can neither add nor confirm an address,
because a trusted address lets mail give Ambrose instructions. Every change is in the audit log.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import trusted
from mailr.core.audit import audit
from mailr.core.auth import Principal, current
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.knowledge import profile

router = APIRouter(tags=["assistant"])
BASE = "/assistant/trusted-addresses"


class AddressIn(BaseModel):
    email: str = Field(max_length=320)


class CodeIn(BaseModel):
    code: str = Field(max_length=20)


class ModeIn(BaseModel):
    mode: str = Field(pattern="^(allow|ask|off)$")


async def _session_only(p: Principal, db: AsyncSession) -> None:
    if p.is_token:
        raise ApiError(403, "trusted.user_managed", name=await profile.name_of(db, p.user_id))


def _raise(e: trusted.TrustedError) -> None:
    raise ApiError(e.status, e.code, **e.params) from None


# ACT trusted.list @web.settings @android.settings
@router.get(BASE)
async def list_trusted(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    await _session_only(p, db)
    return await trusted.listing(db, p.user)


# ACT trusted.add @web.settings @android.settings
@router.post(BASE)
async def add_trusted(body: AddressIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Add an address of yours and mail it a one-time code (the address counts once it is confirmed)."""
    await _session_only(p, db)
    try:
        t = await trusted.add(db, p, body.email)
    except trusted.TrustedError as e:
        await db.rollback()
        _raise(e)
    await audit(db, p, "trusted.add", t.email, always=True)
    await db.commit()
    return trusted.out(t)


# ACT trusted.resend @web.settings @android.settings
@router.post(BASE + "/{tid}/resend")
async def resend_code(tid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    await _session_only(p, db)
    try:
        t = await trusted.get(db, p.user, tid)
        if t.confirmed_at is not None:
            raise trusted.TrustedError(409, "trusted.already_confirmed")
        t = await trusted.add(db, p, t.email)
    except trusted.TrustedError as e:
        await db.rollback()
        _raise(e)
    await audit(db, p, "trusted.resend", t.email, always=True)
    await db.commit()
    return trusted.out(t)


# ACT trusted.confirm @web.settings @android.settings
@router.post(BASE + "/{tid}/confirm")
async def confirm_code(tid: int, body: CodeIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    await _session_only(p, db)
    try:
        t = await trusted.confirm(db, p.user, tid, body.code)
    except trusted.TrustedError as e:
        # A wrong code counts: keep the attempt, then refuse.
        await audit(db, p, "trusted.confirm_failed", str(tid), {"reason": e.code}, always=True)
        await db.commit()
        _raise(e)
    await audit(db, p, "trusted.confirm", t.email, always=True)
    await db.commit()
    return trusted.out(t)


# ACT trusted.remove @web.settings @android.settings
@router.delete(BASE + "/{tid}")
async def remove_trusted(tid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    await _session_only(p, db)
    try:
        addr = await trusted.remove(db, p.user, tid)
    except trusted.TrustedError as e:
        _raise(e)
    await audit(db, p, "trusted.remove", addr, always=True)
    await db.commit()
    return {"ok": True}


# For clients before 0.32 (their Allow, Ask me, Off): the built-in order "Follow my forwarded notes"
# holds this now (Standing orders).
# ACT order.pause @api, ACT order.permission @api
@router.put(BASE + "/mode")
async def set_mode(body: ModeIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Off turns the built-in order off; Ask me and Allow turn it on with that permission."""
    await _session_only(p, db)
    await trusted.set_mode(db, p.user, body.mode)
    await audit(db, p, "trusted.mode", body.mode, always=True)
    await db.commit()
    m, source = await trusted.mode(db, p.user)
    return {"mode": m, "mode_source": "default" if source == "default" else "rule"}
