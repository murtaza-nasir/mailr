"""Settings → Calendar & tasks → "Phones and other apps" (calendar/phones.py,
site/docs/user-guide/phone-calendar.md).

DAV-only app passwords for the user's own built-in calendars and contacts. The signed-in user
only: an API token cannot list, make or delete them (a token holder must not mint a credential
that outlives the token). The password and the iOS profile are in the create answer only.
"""

from __future__ import annotations

import base64
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import phones
from mailr.core.audit import audit
from mailr.core.auth import Principal, current
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import User

router = APIRouter(tags=["calendar"])


def _session_only(p: Principal) -> None:
    if p.is_token:
        raise ApiError(403, "phone.passwords_not_by_token")


def _fail(e: Exception) -> HTTPException:
    if isinstance(e, phones.PhoneAccessError):
        return HTTPException(e.status, str(e))
    return ApiError(502, "phone.mail_server_no_answer")


# ACT settings.phone_passwords @api
@router.get("/me/dav")
async def dav_info(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """What a phone needs (server, username) and the user's app passwords."""
    _session_only(p)
    user = await db.get(User, p.user_id)
    reason = await phones.why_not(db, user)
    url = await phones.public_url(db)
    instance_on = await phones.instance_enabled(db)
    try:
        items = await phones.list_for(user)
    except Exception as e:  # noqa: BLE001
        raise _fail(e) from None
    return {"available": reason is None, "reason": reason or "", "instance": instance_on,
            "url": url if instance_on else "", "server": url.removeprefix("https://") if instance_on else "",
            "username": user.stalwart_login or user.email, "passwords": items,
            "scheduling_note": phones.SCHEDULING_NOTE, "max": phones.MAX_PER_USER}


class NewPassword(BaseModel):
    name: str = Field(..., max_length=200)


# ACT settings.phone_passwords @api
@router.post("/me/dav/app-passwords")
async def create_password(body: NewPassword, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """A new DAV-only app password, shown once, with an iOS/macOS profile that holds it."""
    _session_only(p)
    user = await db.get(User, p.user_id)
    try:
        made = await phones.create(db, user, body.name)
    except Exception as e:  # noqa: BLE001
        raise _fail(e) from None
    url = await phones.public_url(db)
    username = user.stalwart_login or user.email
    profile = phones.mobileconfig(url=url, username=username, password=made["password"], device=made["name"])
    await audit(db, p, "calendar.phone_password.create", made["id"], {"name": made["name"]}, always=True)
    await db.commit()
    slug = re.sub(r"[^a-z0-9]+", "-", made["name"].lower()).strip("-")[:40] or "device"
    return {**made, "username": username, "url": url, "server": url.removeprefix("https://"),
            "profile": base64.b64encode(profile).decode(), "profile_name": f"mailr-{slug}.mobileconfig",
            "scheduling_note": phones.SCHEDULING_NOTE}


# ACT settings.phone_passwords @api
@router.delete("/me/dav/app-passwords/{pid}")
async def delete_password(pid: str, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    if not re.fullmatch(r"[0-9a-z]{1,16}", pid or ""):
        raise ApiError(404, "phone.no_such_app_password")
    user = await db.get(User, p.user_id)
    try:
        ok = await phones.revoke(user, pid)
    except Exception as e:  # noqa: BLE001
        raise _fail(e) from None
    if not ok:
        raise ApiError(404, "phone.no_such_app_password")
    await audit(db, p, "calendar.phone_password.delete", pid, {}, always=True)
    await db.commit()
    return {"ok": True}
