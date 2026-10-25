"""The Android apps this server hands out (core/phone_apps.py): the downloads, their versions for
the setup guide and the Get the app page, and Settings → Admin → Phone apps.

The downloads and the version list are public: an APK holds no secrets, and a phone that opens
the link from the setup guide's QR code is not signed in yet. Publishing needs a signed-in admin
(or the `mailr app-publish` command on the server); API tokens cannot publish.

Each app has an oldest version the API still answers (core/appversion.py): the mailr app's comes from
MAILR_ANDROID_MIN_VERSION, and the Notes app's is the instance setting `phone_apps.notes_min_version`,
set here. Older builds get 426, pause their queue and offer the update.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.config import get_settings
from mailr.core import appversion, phone_apps
from mailr.core.audit import audit
from mailr.core.auth import Principal, admin
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import InstanceSetting

router = APIRouter(tags=["phone apps"])


@router.get("/app/releases")
async def app_releases():
    """Both apps: where to download each, with the version, size and SHA-256 of the APK this
    server holds (null when it holds none)."""
    return {"apps": phone_apps.releases()}


@router.get("/app/{app}.apk", include_in_schema=False)
async def download(app: str):
    """The current APK of `mailr` or `notes`; the previous one when the current file is missing."""
    if app not in phone_apps.APPS:
        raise ApiError(404, "phone.no_such_app")
    hit = phone_apps.served(app)
    if hit is None:
        raise ApiError(404, "phone.app_not_on_server", app=phone_apps.APPS[app])
    f, meta = hit
    headers = {"Cache-Control": "no-cache"}
    if meta.get("sha256"):
        headers["X-Checksum-SHA256"] = meta["sha256"]
    return FileResponse(f, media_type="application/vnd.android.package-archive",
                        filename=phone_apps.download_name(app, meta), headers=headers)


async def _floors(db: AsyncSession) -> dict[str, dict]:
    """The oldest version each app may be: {min_version, source} ("env" for mailr, "setting" for Notes)."""
    row = await db.get(InstanceSetting, appversion.NOTES_KEY)
    notes = row.value if row is not None and isinstance(row.value, str) else ""
    return {"mailr": {"min_version": get_settings().android_min_version or "", "source": "env"},
            "notes": {"min_version": notes, "source": "setting"}}


async def _admin_out(db: AsyncSession) -> dict:
    floors = await _floors(db)
    return {"apps": [{**phone_apps.release(a), "previous": phone_apps.previous(a), "min_version": floors[a]}
                     for a in phone_apps.APPS],
            "max_bytes": phone_apps.MAX_BYTES}


@router.get("/admin/phone-apps")
async def admin_list(p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    return await _admin_out(db)


class FloorIn(BaseModel):
    min_version: str = Field("", max_length=20)


# ACT settings.notes_min_version @api
@router.put("/admin/phone-apps/notes/min-version")
async def set_notes_floor(body: FloorIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """The oldest Notes app version the API answers: "1.2" or "1.2.3", or "" for none. Older Notes
    builds then get 426, keep their changes queued and offer the update."""
    v = body.min_version.strip()
    if not appversion.valid(v):
        raise ApiError(400, "phone.version_format")
    row = await db.get(InstanceSetting, appversion.NOTES_KEY)
    before = row.value if row is not None else ""
    if row is None:
        db.add(InstanceSetting(key=appversion.NOTES_KEY, value=v))
    else:
        row.value = v
    await audit(db, p, "admin.app_min_version", "notes", {"min_version": v}, {"before": before}, always=True)
    await db.commit()
    appversion.clear_cache()
    return await _admin_out(db)


def _known(app: str) -> None:
    if app not in phone_apps.APPS:
        raise ApiError(404, "phone.no_such_app")


# ACT settings.phone_apps @api
@router.post("/admin/phone-apps/{app}", openapi_extra={"requestBody": {"required": True, "content": {
    "multipart/form-data": {"schema": {"type": "object", "required": ["file"], "properties": {
        "file": {"type": "string", "format": "binary"}, "allow_older": {"type": "boolean"}}}}}}})
async def admin_publish(app: str, request: Request, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """Publish an APK (multipart field `file`, optional `allow_older`). 400 with the reason when it
    is refused; the upload stops at 300 MB and before the disk falls under MAILR_DISK_FAIL_PERCENT
    free."""
    # Streamed to a temporary file (core/streamupload.py), removed afterwards.
    import secrets
    import tempfile
    from pathlib import Path

    from mailr.core import streamupload

    _known(app)
    tmp = Path(tempfile.gettempdir())
    disk = streamupload.disk_reserve(tmp, get_settings().disk_fail_percent)

    def check(size: int, chunk: int) -> None:
        if size > phone_apps.MAX_BYTES:
            raise HTTPException(400, f"the APK is larger than {phone_apps.MAX_BYTES // (1024 * 1024)} MB")  # i18n: internal
        disk(size, chunk)

    got = await streamupload.receive(request, lambda _n, _c: tmp / f"apk-{secrets.token_hex(12)}", check)
    try:
        data = await asyncio.to_thread(got.path.read_bytes)
    finally:
        got.path.unlink(missing_ok=True)
    allow_older = got.fields.get("allow_older", "").strip().lower() in ("1", "true", "on", "yes")
    try:
        out = await asyncio.to_thread(phone_apps.publish, app, data, by=p.user.email or f"user {p.user_id}",
                                      allow_older=allow_older)
    except phone_apps.PublishError as e:
        await audit(db, p, "admin.app_publish", app, {"size": len(data)}, {"error": str(e)}, ok=False, always=True)
        await db.commit()
        raise HTTPException(400, str(e)) from None
    r = out["release"]
    await audit(db, p, "admin.app_publish", app, {"size": len(data)},
                {"version_name": r["version_name"], "version_code": r["version_code"], "sha256": r["sha256"],
                 "unchanged": out["unchanged"]}, always=True)
    await db.commit()
    return {**await _admin_out(db), "published": app, "unchanged": out["unchanged"]}


@router.post("/admin/phone-apps/{app}/rollback")
async def admin_rollback(app: str, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """Make the previous version current again (and the current one the previous)."""
    _known(app)
    try:
        out = await asyncio.to_thread(phone_apps.rollback, app, by=p.user.email or f"user {p.user_id}")
    except phone_apps.PublishError as e:
        raise HTTPException(400, str(e)) from None
    r = out["release"]
    await audit(db, p, "admin.app_rollback", app, {}, {"version_name": r["version_name"],
                                                         "version_code": r["version_code"]}, always=True)
    await db.commit()
    return await _admin_out(db)
