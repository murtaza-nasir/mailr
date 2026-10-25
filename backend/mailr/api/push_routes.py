"""Push targets (UnifiedPush endpoints and ntfy topics) for a user's devices.

A UnifiedPush endpoint may come with the phone's Web Push keys (`keys`: p256dh, auth; the
UnifiedPush connector 3 makes them): mailr then encrypts each message for the phone
(core/push.py). Without keys the message is plain JSON, as before.

`vapid: true` says the phone registered the endpoint with mailr's VAPID public key (the
`vapid_public_key` of GET /push/targets, the same key Web Push uses): every message to it then
carries `Authorization: vapid t=…, k=…` (RFC 8292), so the push server refuses messages anyone
else posts to the endpoint. It needs the keys too (the UnifiedPush connector sends both).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.config import get_settings
from mailr.core.auth import Principal, admin, current
from mailr.core.push import EVENTS, deliver_report
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import PushTarget

router = APIRouter(tags=["push"])


class TargetKeys(BaseModel):
    """The phone's Web Push keys (RFC 8291), base64url: a P-256 public key and a 16-byte secret."""

    p256dh: str = Field(min_length=20, max_length=200, pattern=r"^[A-Za-z0-9_\-=]+$")
    auth: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_\-=]+$")


class TargetIn(BaseModel):
    endpoint: str = Field(min_length=8, max_length=2000)
    kind: str = Field("unifiedpush", pattern="^(unifiedpush|ntfy)$")
    device: str = ""
    events: list[str] = []
    keys: TargetKeys | None = None
    vapid: bool = False


def _keys_ok(k: TargetKeys) -> bool:
    import base64

    def raw(v: str) -> bytes:
        return base64.urlsafe_b64decode(v.rstrip("=") + "=" * (-len(v.rstrip("=")) % 4))

    try:
        pub, auth = raw(k.p256dh), raw(k.auth)
    except ValueError:
        return False
    return len(pub) == 65 and pub[0] == 4 and len(auth) == 16


def target_out(t: PushTarget) -> dict:
    return {"id": t.id, "kind": t.kind, "endpoint": t.endpoint, "device": t.device, "events": t.events,
            "encrypted": bool(t.p256dh and t.auth), "vapid": bool(t.vapid), "created_at": t.created_at.isoformat()}


def push_server() -> dict | None:
    """The instance's own ntfy server (MAILR_NTFY_URL), for phones: its address and host, and the
    ntfy deep link that adds it. None when the instance runs none (the phone's push app then uses
    its own default server)."""
    from urllib.parse import urlsplit

    url = (get_settings().ntfy_url or "").strip().rstrip("/")
    if not url.startswith(("https://", "http://")):
        return None
    parts = urlsplit(url)
    return {"url": url, "host": parts.netloc, "secure": parts.scheme == "https", "app": "ntfy"}


def _owner(p: Principal) -> None:
    if p.is_token:
        raise ApiError(403, "push.user_only")


@router.get("/push/targets")
async def list_targets(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _owner(p)
    from mailr.core import webpush

    rows = (await db.execute(select(PushTarget).where(PushTarget.user_id == p.user_id).order_by(PushTarget.id))).scalars()
    out = {"events": list(EVENTS), "items": [target_out(t) for t in rows],
           # The key a phone registers its UnifiedPush endpoint with (vapid: true).
           "vapid_public_key": await webpush.public_key(db),
           # The push server this instance runs, so the phone sets itself up (push_server()).
           "server": push_server()}
    await db.commit()
    return out


@router.post("/push/targets")
async def add_target(body: TargetIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _owner(p)
    from mailr.core.netguard import UnsafeTarget, check_url, private_ok_for

    try:
        await check_url(body.endpoint, private_ok=private_ok_for(p.user))
    except UnsafeTarget as e:
        raise HTTPException(400, str(e)) from None
    bad = set(body.events) - set(EVENTS)
    if bad:
        raise HTTPException(400, f"unknown events: {sorted(bad)}")  # i18n: internal
    if body.keys is not None and (body.kind != "unifiedpush" or not _keys_ok(body.keys)):
        raise HTTPException(400, "keys must be a P-256 public key (65 bytes) and a 16-byte auth secret, for unifiedpush")  # i18n: internal
    if body.vapid and body.keys is None:
        raise HTTPException(400, "vapid needs the endpoint's keys (unifiedpush)")  # i18n: internal
    existing = (await db.execute(select(PushTarget).where(PushTarget.user_id == p.user_id,
                                                          PushTarget.endpoint == body.endpoint))).scalar_one_or_none()
    t = existing or PushTarget(user_id=p.user_id, endpoint=body.endpoint)
    t.kind, t.device, t.events = body.kind, body.device[:200], body.events
    t.p256dh, t.auth = (body.keys.p256dh, body.keys.auth) if body.keys else (None, None)
    t.vapid = bool(body.vapid)
    t.session_id = p.session.id if p.session else None  # it ends with this sign-in
    if existing is None:
        db.add(t)
    await db.commit()
    return target_out(t)


@router.delete("/push/targets/{tid}")
async def delete_target(tid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _owner(p)
    t = await db.get(PushTarget, tid)
    if not t or t.user_id != p.user_id:
        raise ApiError(404, "push.target_not_found")
    await db.delete(t)
    await db.commit()
    return {"ok": True}


@router.post("/push/targets/{tid}/test")
async def test_target(tid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _owner(p)
    t = await db.get(PushTarget, tid)
    if not t or t.user_id != p.user_id:
        raise ApiError(404, "push.target_not_found")
    from mailr import i18n
    from mailr.core.netguard import private_ok_for

    kept, error = await deliver_report(t, "Mailr", i18n.t("push.test.body", i18n.user_language(p.user)), {"type": "test"},
                                       private_ok=private_ok_for(p.user))
    if not kept:
        await db.delete(t)
        await db.commit()
    return {"ok": kept and error is None, "error": error, "removed": not kept}


# ACT admin.push_setup @api
@router.get("/admin/push-setup")
async def push_setup(p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """Admin → Phone apps: what members experience for push, and where phones push through now.
    Counts only: people signed in on an Android app, and their push endpoints by server. No names,
    no addresses."""
    from datetime import UTC, datetime
    from urllib.parse import urlsplit

    from mailr.models import Session

    server = push_server()
    now = datetime.now(UTC)
    phone_users = set((await db.execute(select(Session.user_id).where(
        Session.device == "android", Session.expires_at > now).distinct())).scalars())
    rows = (await db.execute(select(PushTarget.user_id, PushTarget.endpoint).where(
        PushTarget.kind == "unifiedpush"))).all()
    hosts: dict[str, set[int]] = {}
    for uid, endpoint in rows:
        host = (urlsplit(endpoint).netloc or "").lower()
        hosts.setdefault(host, set()).add(uid)
    with_push = set().union(*hosts.values()) if hosts else set()
    own = server["host"].lower() if server else None
    return {
        "server": server,
        # What a new member does, in order (the web page words them).
        "steps": ["install_ntfy"] + (["set_default_server"] if server else []),
        "people": {"phones": len(phone_users), "push": len(with_push & phone_users) if phone_users else len(with_push),
                   "no_push": len(phone_users - with_push)},
        "servers": sorted(({"host": h, "people": len(u), "own": h == own} for h, u in hosts.items()),
                          key=lambda x: (not x["own"], -x["people"], x["host"])),
    }
