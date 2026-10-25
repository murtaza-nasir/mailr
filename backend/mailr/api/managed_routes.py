"""Admin → Users: mail accounts an administrator connects for a member (core/managed.py).

Everything here needs a signed-in administrator (never an API token), and every change but the
setting itself needs the instance setting `accounts.admin_connect` on. Off (the default), members
manage their own accounts only, as before.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr import i18n
from mailr.api.admin_routes import _audit_admin_change, _set_instance
from mailr.api.connector_routes import ConnectorDraft, _normalize, _transient, create_mail_account
from mailr.connectors.base import REGISTRY
from mailr.core import managed
from mailr.core.audit import audit, notice
from mailr.core.auth import Principal, admin
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import Connector, User

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(_audit_admin_change)])


def _signed_in(p: Principal) -> None:
    if p.is_token:
        raise ApiError(403, "accounts.admin_connects_not_token")


async def _on(db: AsyncSession) -> None:
    if not await managed.enabled(db):
        raise ApiError(403, "accounts.managed_off")


async def _member(db: AsyncSession, p: Principal, uid: int) -> User:
    u = await db.get(User, uid)
    if u is None:
        raise ApiError(404, "admin.user_not_found")
    if uid == p.user_id:
        raise ApiError(400, "accounts.add_own_accounts")
    if u.disabled:
        raise ApiError(400, "accounts.turned_off")
    return u


# ACT admin.managed_accounts_setting @api
@router.get("/managed-accounts")
async def overview(p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """The setting, and every account an administrator connected (no passwords, no mail)."""
    rows = (await db.execute(select(Connector).where(Connector.managed_by_admin.is_(True))
                             .order_by(Connector.user_id, Connector.id))).scalars().all()
    owners = {u.id: u for u in (await db.execute(select(User).where(
        User.id.in_({c.user_id for c in rows})))).scalars()} if rows else {}
    return {"enabled": await managed.enabled(db), "items": [managed.out(c, owners.get(c.user_id)) for c in rows]}


class SettingIn(BaseModel):
    enabled: bool


@router.put("/managed-accounts")
async def set_setting(body: SettingIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """Turning it off stops new connections; the accounts already connected stay with their members."""
    _signed_in(p)
    before = await managed.enabled(db)
    await _set_instance(db, managed.SETTING, bool(body.enabled))
    await audit(db, p, "admin.accounts.admin_connect", "", {"before": before, "after": bool(body.enabled)}, always=True)
    await db.commit()
    return await overview(p, db)


class ManagedIn(BaseModel):
    """An IMAP/SMTP account for a member (the setup wizard's fields)."""

    name: str = ""
    preset: str | None = None
    mode: str = Field("mirror", pattern="^(mirror|store)$")
    data_class: str = Field("normal", pattern="^(normal|local_only)$")
    config: dict[str, Any] = {}
    secrets: dict[str, Any] = {}
    identities: list[str] | None = None
    skip_test: bool = False
    # The member disconnected this mailbox before: connect it again anyway (they are told again).
    reconnect: bool = False


def _draft(body: ManagedIn) -> ConnectorDraft:
    return ConnectorDraft(kind="imap", name=body.name, preset=body.preset, mode=body.mode, data_class=body.data_class,
                          config=body.config, secrets=body.secrets, identities=body.identities, skip_test=body.skip_test)


# ACT admin.managed_account_list @api
@router.get("/users/{uid}/accounts")
async def list_managed(uid: int, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """The accounts an administrator connected for this member, and the ones they disconnected.
    The member's own accounts are not listed."""
    u = await db.get(User, uid)
    if u is None:
        raise ApiError(404, "admin.user_not_found")
    rows = (await db.execute(select(Connector).where(Connector.user_id == uid, Connector.managed_by_admin.is_(True))
                             .order_by(Connector.id))).scalars().all()
    return {"enabled": await managed.enabled(db), "items": [managed.out(c, u) for c in rows],
            "disconnected": [{"address": d.get("address"), "at": d.get("at")} for d in managed.declined(u)]}


# ACT admin.managed_account_test @api
@router.post("/users/{uid}/accounts/test")
async def test_managed(uid: int, body: ManagedIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """Sign in to the servers as the member's account would (the member's own network rules)."""
    from mailr.core.netguard import private_ok_for

    _signed_in(p)
    await _on(db)
    u = await _member(db, p, uid)
    draft = _draft(body)
    cfg, mode = _normalize(draft)
    conn = REGISTRY["imap"](_transient(u, draft, cfg, mode), body.secrets)
    conn.private_ok = private_ok_for(u)
    res = await conn.test()
    return {"ok": res.ok, "detail": res.detail, "folders": res.folders, "identities": res.identities}


# ACT admin.managed_account_connect @api
@router.post("/users/{uid}/accounts")
async def connect_managed(uid: int, body: ManagedIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    from mailr.core.netguard import private_ok_for

    _signed_in(p)
    await _on(db)
    u = await _member(db, p, uid)
    if not (body.secrets or {}).get("password"):
        raise ApiError(400, "accounts.enter_password")
    draft = _draft(body)
    cfg, _ = _normalize(draft)
    key = managed.mailbox_key(cfg)
    gone = managed.declined_for(u, key)
    if gone and not body.reconnect:
        raise ApiError(409, "accounts.disconnected_by_person", person=u.name or u.email)
    taken = (await db.execute(select(Connector).where(Connector.user_id == uid, Connector.kind == "imap"))).scalars().all()
    if any(managed.mailbox_key(c.config or {}) == key for c in taken):
        raise ApiError(409, "accounts.person_has_account")
    c = await create_mail_account(db, u, draft, private_ok=private_ok_for(u), managed_by=p.user)
    if gone:
        await managed.forget_declined(db, u, key)
    addr = managed.address(c)
    await notice(db, p, uid, "account_connected",
                 i18n.msg("activity.admin.account_reconnected" if gone else "activity.admin.account_connected",
                          address=addr),
                 {"connector_id": c.id, "address": addr})
    await audit(db, p, "admin.accounts.connect", str(c.id),
                {"user_id": uid, "address": addr, "mode": c.mode, "data_class": c.data_class, "reconnect": bool(gone)},
                always=True)
    await db.commit()
    return managed.out(c, u)


# ACT admin.managed_account_remove @api
@router.delete("/users/{uid}/accounts/{cid}")
async def remove_managed(uid: int, cid: int, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """Remove an account an administrator connected (only those). The mail it brought in stays
    with the member."""
    from mailr.core.jobs import enqueue

    _signed_in(p)
    await _on(db)
    c = await db.get(Connector, cid)
    if c is None or c.user_id != uid or not c.managed_by_admin:
        raise ApiError(404, "accounts.account_not_found")
    addr = managed.address(c)
    await enqueue(db, "connector.forget_refs", {"connector_id": c.id, "user_id": c.user_id}, user_id=c.user_id)
    await notice(db, p, uid, "account_removed",
                 i18n.msg("activity.admin.account_removed", address=addr),
                 {"address": addr})
    await audit(db, p, "admin.accounts.remove", str(cid), {"user_id": uid, "address": addr}, always=True)
    await db.delete(c)
    await db.commit()
    return {"ok": True}
