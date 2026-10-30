"""Connectors: presets, discovery, test, CRUD, sync controls, upstream-deletion arming, uploads."""

from __future__ import annotations

import os
import re
import secrets as pysecrets
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr import i18n
from mailr.api.admin_routes import (
    connector_matches_domains,
    connector_matches_domains_db,
    instance_local_only_domains,
    set_connector_data_class,
)
from mailr.config import get_settings
from mailr.connectors import sentcopy
from mailr.connectors.base import REGISTRY
from mailr.connectors.discover import discover
from mailr.connectors.loader import _load_all, set_secrets
from mailr.connectors.mbox import imports_root
from mailr.connectors.presets import PRESETS, public_presets
from mailr.core import accounthealth
from mailr.core.auth import Principal, current, scope
from mailr.core.backup import store_mode_available
from mailr.core.jobs import enqueue
from mailr.db import get_db, get_existing
from mailr.i18n import ApiError
from mailr.models import Connector, Identity, Job, Message, User

router = APIRouter(prefix="/connectors", tags=["connectors"])

MAIL_KINDS = ("imap", "gmail", "file")
SECRET_KEYS = ("password", "smtp_password", "oauth")


def _max_import_bytes() -> int:
    from mailr.connectors.mbox import max_import_bytes

    return max_import_bytes()


def _backfill_out(c: Connector, st: dict[str, Any]) -> dict[str, Any]:
    """The older-mail import's progress (connectors/imap.py backfill): total and imported
    messages (null until counted), done."""
    bf = st.get("backfill") or {}
    return {"done": bool(bf.get("done")), "total": bf.get("total"), "imported": int(bf.get("imported") or 0),
            "since": (c.config or {}).get("since"), "until": (c.config or {}).get("backfill_since")}


def connector_out(c: Connector, lang: str = i18n.DEFAULT, unsynced: int = 0, unsaved_sent: int = 0) -> dict[str, Any]:
    _load_all()
    cls = REGISTRY.get(c.kind)
    st = c.state or {}
    folders = {k: {"last_uid": v.get("last_uid")} for k, v in (st.get("folders") or {}).items()}
    if c.kind == "speakr":
        # Recording counts and sync health for the Speakr card (the token is never part of it).
        from mailr.integrations.speakr import store as speakr_store
        from mailr.integrations.speakr.connect import scope_notes
        from mailr.integrations.speakr.webhook import path_for

        scopes = speakr_store.token_scopes(c)
        extra = {"speakr": {"count": st.get("count"), "processing": st.get("processing"),
                            "auth_failed": bool(st.get("auth_failed")), "breaker_until": st.get("breaker_until"),
                            "webhook_path": path_for(c),
                            # What the token may do and what Speakr supports; None for an older Speakr.
                            "scopes": scopes, "scope_notes": scope_notes(scopes, c.config or {}, lang),
                            "changes_feed": speakr_store.feature(c, "changes_feed"),
                            "internal_sharing": speakr_store.feature(c, "internal_sharing"),
                            "webhook_v2": speakr_store.feature(c, "webhook_signature_v2"),
                            "speakr_model_ok": speakr_store.speakr_model_ok(c)}}
    else:
        extra = {}
    return extra | {
        "id": c.id, "kind": c.kind, "name": c.name, "preset": c.preset, "mode": c.mode, "builtin": bool(c.builtin),
        "data_class": c.data_class, "config": {k: v for k, v in (c.config or {}).items() if k not in SECRET_KEYS},
        "has_password": bool(c.secrets_enc), "retention_days": c.retention_days,
        # An administrator connected it for this person (core/managed.py).
        "managed_by_admin": bool(c.managed_by_admin),
        "delete_upstream": c.delete_upstream, "enabled": c.enabled, "status": c.status,
        "status_detail": c.status_detail, "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
        # The status in plain words for the owner (core/accounthealth.py).
        "health": accounthealth.of(c, lang=lang, unsynced=unsynced, unsaved_sent=unsaved_sent),
        # Removals upstream that mailr did not apply because the listing was suspect, per folder
        # (connectors/imap.py `_guarded_gone`), and local changes the provider has not confirmed.
        "sync_warnings": [{"folder": f, **w} for f, w in sorted((st.get("sync_warnings") or {}).items())],
        "unsynced_changes": unsynced,
        # Mail sent from mailr saved in the provider's Sent folder (mirror-mode IMAP accounts only,
        # connectors/sentcopy.py): the setting, whether it saves now, and the copies that failed.
        "sent_copies": sentcopy.out(c, unsaved_sent),
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "progress": {"initial_done": bool(st.get("initial_done") or st.get("done")), "folders": folders,
                     **({"backfill": _backfill_out(c, st)} if (c.config or {}).get("backfill") else {})},
        "capabilities": {"modes": list(cls.capabilities.modes), "send": cls.capabilities.send,
                         "write_back": cls.capabilities.write_back} if cls else None,
    }


def _session_only(p: Principal) -> None:
    if p.is_token:
        raise ApiError(403, "accounts.user_only")


async def _owned(db: AsyncSession, p: Principal, cid: int) -> Connector:
    c = await db.get(Connector, cid)
    if c is None or c.user_id != p.user_id:
        raise ApiError(404, "accounts.account_not_found")
    if c.builtin:
        from mailr.calendar import builtin

        if not await builtin.active(db, c):  # built-in calendars are switched off for the instance
            raise ApiError(404, "accounts.account_not_found")
    return c


# ---------------- discovery and testing ----------------


@router.get("/presets")
async def presets(p: Principal = Depends(current)):
    return public_presets()


class DiscoverIn(BaseModel):
    email: str


@router.post("/discover")
async def discover_route(body: DiscoverIn, p: Principal = Depends(current)):
    from mailr.core.netguard import private_ok_for

    _session_only(p)
    return await discover(body.email, private_ok_for(p.user))


class ConnectorDraft(BaseModel):
    kind: str = "imap"
    name: str = ""
    preset: str | None = None
    mode: str | None = None
    data_class: str = Field("normal", pattern="^(normal|local_only)$")
    config: dict[str, Any] = {}
    secrets: dict[str, Any] = {}
    retention_days: int | None = Field(None, ge=0, le=3650)
    skip_test: bool = False
    # Send-as addresses to create for this account (defaults to the login address).
    identities: list[str] | None = None


def _normalize(body: ConnectorDraft) -> tuple[dict[str, Any], str]:
    _load_all()
    if body.kind not in MAIL_KINDS or body.kind not in REGISTRY:
        raise HTTPException(400, f"unknown account type {body.kind}")  # i18n: internal
    if body.preset and body.preset not in PRESETS:
        raise HTTPException(400, f"unknown provider preset {body.preset}")  # i18n: internal
    if body.preset and PRESETS[body.preset].get("supported") is False:
        if PRESETS[body.preset].get("notes"):
            raise HTTPException(400, PRESETS[body.preset]["notes"])
        raise ApiError(400, "accounts.provider_unsupported")
    cfg = dict(body.config)
    for k in SECRET_KEYS:
        cfg.pop(k, None)
    if cfg.get("email"):
        cfg["email"] = str(cfg["email"]).strip().lower()
    preset = PRESETS.get(body.preset or "") or {}
    for k in ("imap", "smtp"):
        # Accept the UI's "security" as an alias of "tls".
        if isinstance(cfg.get(k), dict) and "security" in cfg[k]:
            cfg[k] = {**cfg[k], "tls": cfg[k].get("tls") or cfg[k]["security"]}
            cfg[k].pop("security", None)
    if cfg.get("skip_folders"):
        folders = dict(cfg.get("folders") or {})
        folders["exclude"] = sorted(set(folders.get("exclude") or []) | set(cfg.pop("skip_folders")))
        cfg["folders"] = folders
    else:
        cfg.pop("skip_folders", None)
    if not cfg.get("since"):
        cfg.pop("since", None)
    # Recent mail first (since), then the older mail in the background (backfill, back to
    # backfill_since or everything): the setup guide's history choice (connectors/imap.py).
    if cfg.get("since") and cfg.get("backfill"):
        cfg["backfill"] = True
        if not cfg.get("backfill_since"):
            cfg.pop("backfill_since", None)
    else:
        cfg.pop("backfill", None)
        cfg.pop("backfill_since", None)
    if body.kind in ("imap", "gmail"):
        imap = {**preset.get("imap", {}), **(cfg.get("imap") or {})}
        if not imap.get("host"):
            raise ApiError(400, "accounts.imap_required")
        if not imap.get("username"):
            imap["username"] = cfg.get("username") or cfg.get("email")
        cfg["imap"] = imap
        smtp = {**(preset.get("smtp") or {}), **(cfg.get("smtp") or {})}
        if smtp.get("host"):
            smtp.setdefault("username", imap.get("username"))
            cfg["smtp"] = smtp
        else:
            cfg["smtp"] = None
        for k in ("imap", "smtp"):
            if cfg.get(k) and cfg[k].get("tls") not in ("ssl", "starttls", "none"):
                raise HTTPException(400, f"{k} tls must be ssl, starttls, or none")  # i18n: internal
    cls = REGISTRY[body.kind]
    mode = body.mode or ("import" if body.kind in ("gmail", "file") else "store" if store_mode_available() else "mirror")
    if mode not in cls.capabilities.modes:
        raise HTTPException(400, f"{body.kind} accounts support modes: {', '.join(cls.capabilities.modes)}")  # i18n: internal
    return cfg, mode


def check_store_allowed(mode: str) -> None:
    """Store mode deletes mail at the provider once it is in the daily mail export, so it is
    refused while that export is off (MAILR_MAIL_EXPORT=false), unless the export check is off too
    (MAILR_STORE_BACKUP_CHECK=false)."""
    if mode == "store" and not store_mode_available():
        raise ApiError(400, "accounts.store_needs_mail_export")


def _transient(p: Principal | Any, body: ConnectorDraft, cfg: dict, mode: str) -> Connector:
    """A connector that is never saved, for a test. `p` is the principal, or the owner (a User)."""
    owner_id = p.id if isinstance(p, User) else p.user_id
    return Connector(id=0, user_id=owner_id, kind=body.kind, name=body.name or cfg.get("email") or body.kind,
                     preset=body.preset, mode=mode, data_class=body.data_class, config=cfg, state={})


@router.post("/test")
async def test(body: ConnectorDraft, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    from mailr.core.netguard import private_ok_for

    _session_only(p)
    if body.kind == "caldav":
        return await _caldav_test(body, private_ok_for(p.user))
    if body.kind == "speakr":
        from mailr.integrations.speakr import connect as speakr

        return await speakr.test(body, private_ok=private_ok_for(p.user), lang=i18n.user_language(p.user))
    cfg, mode = _normalize(body)
    if body.kind == "file":
        _check_import_path(p, cfg)
    row = _transient(p, body, cfg, mode)
    secrets_in = body.secrets
    if not secrets_in and body.config.get("connector_id"):
        # Re-test a saved account without re-entering the password.
        from mailr.connectors.loader import connector_for

        saved = await _owned(db, p, int(body.config["connector_id"]))
        if _hosts(cfg) != _hosts(saved.config or {}):
            # A saved password is only ever sent to the server it was saved for.
            raise ApiError(400, "accounts.password_again_test")
        secrets_in = (await connector_for(db, saved)).secrets
    conn = REGISTRY[body.kind](row, secrets_in)
    conn.private_ok = private_ok_for(p.user)
    res = await conn.test()
    return {"ok": res.ok, "detail": res.detail, "folders": res.folders, "identities": res.identities,
            "extensions": res.extensions}


async def _check_running_imports(db: AsyncSession, user_id: int) -> None:
    """At most MAILR_MAX_CONCURRENT_IMPORTS file imports of one user that have not finished."""
    n = (await db.execute(select(func.count()).select_from(Connector).where(
        Connector.user_id == user_id, Connector.kind == "file", Connector.enabled.is_(True),
        func.coalesce(Connector.state["done"].as_boolean(), False).is_(False)))).scalar_one()
    limit = get_settings().max_concurrent_imports
    if n >= limit:
        raise ApiError(429, "accounts.imports_running", max=limit)


def _check_import_path(p: Principal, cfg: dict) -> None:
    path = Path(str(cfg.get("path") or ""))
    root = imports_root(p.user_id).resolve()
    if not path.is_absolute():
        path = root / path
    rp = path.resolve()
    if root not in rp.parents or not rp.exists():
        raise HTTPException(400, "upload the file first (POST /connectors/uploads)")  # i18n: internal
    cfg["path"] = str(rp)


# ---------------- CRUD ----------------


@router.get("")
async def list_connectors(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    from mailr.calendar import builtin

    rows = list((await db.execute(select(Connector).where(Connector.user_id == p.user_id).order_by(Connector.id))).scalars())
    on = await builtin.shown_for(db, p.user_id)
    unsynced = await accounthealth.unsynced_changes(db, [c.id for c in rows if c.mode == "mirror"])
    unsaved = await sentcopy.unsaved(db, [c.id for c in rows if sentcopy.active(c)])
    # Calendar links (calendar/webcal.py) are listed under GET /calendar/subscriptions, and phone
    # calendars (calendar/phonecal.py) under GET /calendar/phone-calendars, not as accounts.
    return [connector_out(c, i18n.user_language(p.user), unsynced.get(c.id, 0), unsaved.get(c.id, 0)) for c in rows
            if p.can_see_connector(c) and (on or not c.builtin) and c.kind not in ("webcal", "phone")]


@router.get("/{cid}")
async def get_connector(cid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    c = await _owned(db, p, cid)
    if not p.can_see_connector(c):
        raise ApiError(404, "accounts.account_not_found")
    return await _out_with_counts(db, p, c)


async def _out_with_counts(db: AsyncSession, p: Principal, c: Connector) -> dict[str, Any]:
    unsynced = await accounthealth.unsynced_changes(db, [c.id] if c.mode == "mirror" else [])
    unsaved = await sentcopy.unsaved(db, [c.id] if sentcopy.active(c) else [])
    return connector_out(c, i18n.user_language(p.user), unsynced.get(c.id, 0), unsaved.get(c.id, 0))


async def _link_identities(db: AsyncSession, p: Principal | User, c: Connector) -> None:
    owner = p if isinstance(p, User) else p.user
    if c.kind not in ("imap", "gmail") or not (c.config or {}).get("smtp"):
        return
    chosen = c.config.get("identities")
    if chosen is not None:
        addrs = [str(a).lower() for a in chosen if "@" in str(a)]
    else:
        addrs = [a.lower() for a in (c.config.get("addresses") or []) if "@" in str(a)]
        login = (c.config.get("email") or c.config["imap"].get("username") or "").lower()
        if "@" in login and login not in addrs:
            addrs.insert(0, login)
    display = str(c.config.get("display_name") or "").strip() or owner.name or ""
    has_default = (await db.execute(select(func.count()).select_from(Identity).where(
        Identity.user_id == owner.id, Identity.is_default.is_(True)))).scalar_one() > 0
    for a in addrs:
        ident = (await db.execute(select(Identity).where(Identity.user_id == owner.id, Identity.email == a))).scalar_one_or_none()
        if ident is None:
            db.add(Identity(user_id=owner.id, connector_id=c.id, email=a, name=display,
                            is_default=not has_default, receives=[]))
            has_default = True
        elif ident.connector_id is None:
            ident.connector_id = c.id


@router.post("")
async def create(body: ConnectorDraft, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    if body.kind == "caldav":
        from mailr.calendar.routes import ConnectIn, connect

        cfg = body.config or {}
        acct = await connect(ConnectIn(url=str(cfg.get("url") or ""), username=str(cfg.get("username") or ""),
                                       password=str((body.secrets or {}).get("password") or ""),
                                       name=body.name), p, db)
        return connector_out(await db.get(Connector, acct["id"]), i18n.user_language(p.user))
    if body.kind == "speakr":
        from mailr.integrations.speakr import connect as speakr

        return connector_out(await speakr.create(db, p, body), i18n.user_language(p.user))
    if body.kind == "file":
        cfg, _ = _normalize(body)
        _check_import_path(p, cfg)
        await _check_running_imports(db, p.user_id)
        body.config = {**body.config, "path": cfg["path"]}
    from mailr.core.netguard import private_ok_for

    c = await create_mail_account(db, p.user, body, private_ok=private_ok_for(p.user))
    await db.commit()
    from mailr.core import sidebar

    sidebar.forget_user(p.user_id)  # the navigation's account list
    return connector_out(c, i18n.user_language(p.user))


async def create_mail_account(db: AsyncSession, owner: User, body: ConnectorDraft, *, private_ok: bool,
                              managed_by: User | None = None) -> Connector:
    """Add a mail account (imap, gmail, file) for `owner`: the instance's local-only domains
    apply, the account is tested unless `skip_test`, its secrets are encrypted under the owner's
    own data key, send-as identities are made, and the first sync is queued. With `managed_by`
    (core/managed.py) an administrator connects it for the owner. The caller commits."""
    if body.identities is not None:
        body.config = {**body.config, "identities": [a.strip().lower() for a in body.identities if "@" in a]}
    cfg, mode = _normalize(body)
    check_store_allowed(mode)
    data_class = body.data_class
    doms = await instance_local_only_domains(db)
    row = _transient(owner, body, cfg, mode)
    if doms and connector_matches_domains(row, doms):
        data_class = "local_only"
    for a in body.identities or []:
        if any(a.lower().rsplit("@", 1)[-1] == d or a.lower().rsplit("@", 1)[-1].endswith("." + d) for d in doms):
            data_class = "local_only"
    if body.kind in ("imap", "gmail") and not body.skip_test:
        conn = REGISTRY[body.kind](row, body.secrets)
        conn.private_ok = private_ok
        res = await conn.test()
        if not res.ok:
            raise HTTPException(400, res.detail)
    c = Connector(user_id=owner.id, kind=body.kind, name=row.name, preset=body.preset, mode=mode,
                  data_class=data_class, config=cfg, state={},
                  retention_days=body.retention_days if body.retention_days is not None
                  else get_settings().default_retention_days,
                  delete_upstream=False, enabled=True, status="new", status_detail="Starting…",
                  managed_by_admin=managed_by is not None, managed_by=managed_by.id if managed_by else None)
    db.add(c)
    await db.flush()
    if body.secrets:
        await set_secrets(db, c, {k: v for k, v in body.secrets.items() if k in SECRET_KEYS and v})
    await _link_identities(db, owner, c)
    await enqueue(db, "connector.sync_now", {"connector_id": c.id}, user_id=owner.id)
    return c


def _mailbox_identity(cfg: dict[str, Any]) -> tuple:
    """Which upstream mailbox a configuration points at (server and account)."""
    imap = cfg.get("imap") or {}
    return (str(imap.get("host") or "").lower(), imap.get("port"), str(imap.get("username") or "").lower(),
            str(cfg.get("username") or "").lower(), str(cfg.get("url") or "").lower())


def _hosts(cfg: dict[str, Any]) -> tuple:
    return tuple(((cfg.get(k) or {}).get("host"), (cfg.get(k) or {}).get("port")) for k in ("imap", "smtp")) + (cfg.get("url"),)


class ConnectorPatch(BaseModel):
    name: str | None = None
    mode: str | None = None
    data_class: str | None = Field(None, pattern="^(normal|local_only)$")
    config: dict[str, Any] | None = None
    secrets: dict[str, Any] | None = None
    retention_days: int | None = Field(None, ge=0, le=3650)
    enabled: bool | None = None
    # A mail account's colour (core/mailaccounts.ACCOUNT_HUES); "" or "auto" goes back to the
    # automatic one.
    color: str | None = None
    # "Save sent mail at the provider" (connectors/sentcopy.py): auto, on or off.
    save_sent: Literal["auto", "on", "off"] | None = None


@router.patch("/{cid}")
async def patch(cid: int, body: ConnectorPatch, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    c = await _owned(db, p, cid)
    if c.kind == "speakr":
        from mailr.integrations.speakr import connect as speakr

        return connector_out(await speakr.patch(db, p, c, body), i18n.user_language(p.user))
    if c.builtin and (body.name is not None and body.name.strip() != c.name or body.mode is not None and body.mode != c.mode
                      or body.config is not None or body.secrets):
        # Only hiding (enabled) and local-only are open: ACT calendar.builtin_hide @api
        raise ApiError(400, "calendar.builtin_fixed")
    _load_all()
    if body.color is not None:
        # ACT mail.account_colour @api
        from mailr.core import mailaccounts, sidebar

        if c.kind not in mailaccounts.MAIL_ACCOUNT_KINDS:
            raise ApiError(400, "accounts.colour_mail_only")
        try:
            hue = mailaccounts.normalize_color(None if body.color == "auto" else body.color)
        except ValueError:
            raise ApiError(400, "accounts.colour_unknown") from None
        cfg = {k: v for k, v in (c.config or {}).items() if k != "color"}
        c.config = {**cfg, "color": hue} if hue else cfg
        sidebar.forget_user(p.user_id)
    if body.save_sent is not None:
        # ACT settings.account_save_sent @api
        if c.kind != "imap":
            raise ApiError(400, "accounts.save_sent_imap_only")
        c.config = {**(c.config or {}), "save_sent": body.save_sent}
    if body.name is not None:
        c.name = body.name.strip()[:200] or c.name
    if body.mode is not None and body.mode != c.mode:
        if body.mode not in REGISTRY[c.kind].capabilities.modes:
            raise ApiError(400, "accounts.mode_not_supported")
        check_store_allowed(body.mode)
        c.mode = body.mode
        if body.mode != "store":
            c.delete_upstream = False
    if body.config is not None:
        merged = {**(c.config or {}), **{k: v for k, v in body.config.items() if k not in SECRET_KEYS}}
        draft = ConnectorDraft(kind=c.kind, preset=c.preset, mode=c.mode, config=merged)
        new_cfg, _ = _normalize(draft)
        if _hosts(new_cfg) != _hosts(c.config or {}) and not (body.secrets or {}).get("password"):
            # Never replay a saved password against a different server.
            raise ApiError(400, "accounts.password_again_change")
        if _mailbox_identity(new_cfg) != _mailbox_identity(c.config or {}):
            # Deleting upstream copies was armed for the old mailbox; never carry that over to
            # another server or account. The user arms it again for the new one.
            c.delete_upstream = False
        c.config = new_cfg
    if body.secrets:
        from mailr.connectors.loader import connector_for

        current_secrets = (await connector_for(db, c)).secrets
        await set_secrets(db, c, {**current_secrets, **{k: v for k, v in body.secrets.items() if k in SECRET_KEYS and v}})
    # The admin policy is checked against the configuration as it is AFTER this change.
    doms = await instance_local_only_domains(db)
    forced = await connector_matches_domains_db(db, c, doms)
    wanted = body.data_class or c.data_class
    if forced and wanted == "normal":
        if body.data_class == "normal":
            raise ApiError(400, "accounts.admin_local_only")
        wanted = "local_only"
    if wanted != c.data_class:
        await set_connector_data_class(db, c, wanted)
    if body.retention_days is not None:
        c.retention_days = body.retention_days
    if body.enabled is not None:
        c.enabled = body.enabled
        if body.enabled and c.status == "paused":
            c.status, c.status_detail = "new", "Resuming…"
    await _link_identities(db, p, c)
    await db.commit()
    return await _out_with_counts(db, p, c)


@router.delete("/{cid}")
async def delete(cid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Removes the account from mailr. Mail it already delivered stays."""
    _session_only(p)
    c = await _owned(db, p, cid)
    if c.builtin:
        raise ApiError(400, "calendar.builtin_cannot_remove")
    if c.kind == "speakr":
        # Removal deletes the mirror and the links (cascade), and the webhook mailr made in Speakr
        # (when Speakr can be reached); no recording in Speakr is touched.
        from mailr.integrations.speakr import store as speakr_store
        from mailr.integrations.speakr import webhook as speakr_webhook

        if (c.config or {}).get("speakr_webhook_id"):
            await speakr_webhook.unregister(db, c)
        speakr_store.forget(c.id)
    if c.managed_by_admin:
        # Remembered, so it is never added again without the administrator saying so.
        from mailr.core import managed

        await managed.on_member_disconnect(db, await db.get(User, p.user_id), c)
    if c.kind in ("imap", "gmail", "file"):
        # Messages stay; the account's refs leave upstream.copies (connector.forget_refs).
        await enqueue(db, "connector.forget_refs", {"connector_id": c.id, "user_id": c.user_id}, user_id=c.user_id)
    await db.delete(c)
    await db.commit()
    from mailr.core import sidebar

    sidebar.forget_user(p.user_id)  # the navigation's account list
    return {"ok": True}


async def _nudge(db: AsyncSession, c: Connector) -> None:
    if c.kind == "speakr":
        from mailr.integrations.speakr.sync import queue_sync

        await queue_sync(db, c, reset=True)
        return
    await enqueue(db, "connector.sync_now", {"connector_id": c.id}, user_id=c.user_id,
                  dedupe_key=f"connector.sync_now:{c.id}")


# ACT speakr.sync @api (a Speakr connector: a full mirror pass now)
@router.post("/{cid}/sync")
async def sync(cid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    c = await _owned(db, p, cid)
    if c.mode == "import" and c.kind in ("imap", "gmail"):
        # Import again: a finished import gets one more pass when the user asks.
        c.state = {**(c.state or {}), "import_again": True}
    await _nudge(db, c)
    await db.commit()
    return {"ok": True}


@router.post("/{cid}/pause")
async def pause(cid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    c = await _owned(db, p, cid)
    c.enabled = False
    c.status, c.status_detail = "paused", "Paused"
    await db.commit()
    return connector_out(c, i18n.user_language(p.user))


@router.post("/{cid}/resume")
async def resume(cid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    c = await _owned(db, p, cid)
    c.enabled = True
    c.status, c.status_detail = "new", "Resuming…"
    await _nudge(db, c)
    await db.commit()
    return connector_out(c, i18n.user_language(p.user))


@router.post("/{cid}/resync")
async def resync(cid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Forget sync cursors and scan again. Messages already stored are recognised and skipped."""
    _session_only(p)
    c = await _owned(db, p, cid)
    c.state = {}
    c.status, c.status_detail = "new", "Re-scanning…"
    await _nudge(db, c)
    await db.commit()
    return connector_out(c, i18n.user_language(p.user))


class ArmIn(BaseModel):
    confirm: str


@router.post("/{cid}/arm-deletion")
async def arm(cid: int, body: ArmIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    c = await _owned(db, p, cid)
    if c.mode != "store":
        raise ApiError(400, "accounts.upstream_delete_store_only")
    if body.confirm.strip() != c.name:
        raise ApiError(400, "accounts.confirm_account_name")
    check_store_allowed(c.mode)
    from mailr.core.backup import exported_manifest

    checked = get_settings().store_backup_check
    # Whether an export exists at all; the restore check below reads a sample from the target.
    # Neither runs with MAILR_STORE_BACKUP_CHECK=false: backups are then the administrator's own.
    if checked and not exported_manifest(p.user_id):
        raise ApiError(409, "accounts.no_backup_yet")
    if not (c.state or {}).get("initial_done"):
        raise ApiError(409, "accounts.first_sync_running")
    # Mail flow: at least one message must have arrived through this account AFTER its first
    # sync (proves new mail reaches the mailbox mailr reads, e.g. after a routing change).
    done_at = (c.state or {}).get("initial_done_at")
    flowing = done_at and (await db.execute(select(Message.id).where(
        Message.connector_id == c.id, Message.created_at > datetime.fromisoformat(done_at)).limit(1))).first()
    if not flowing:
        raise ApiError(409, "accounts.no_new_mail_since_sync")
    from mailr.core import backup

    check: dict[str, Any] = await backup.verify(p.user_id) if checked else {"ok": True}
    if not check["ok"]:
        raise ApiError(409, "accounts.backup_check_failed",
            reason=check.get('reason') or check['failures'][:1])
    c.delete_upstream = True
    await db.commit()
    return connector_out(c, i18n.user_language(p.user))


@router.post("/{cid}/disarm-deletion")
async def disarm(cid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    c = await _owned(db, p, cid)
    c.delete_upstream = False
    await db.commit()
    return connector_out(c, i18n.user_language(p.user))


# ---------------- sent mail at the provider (connectors/sentcopy.py) ----------------


def _sent_copy_account(c: Connector) -> None:
    if not sentcopy.eligible(c):
        raise ApiError(400, "accounts.sent_copy_mirror_only")


# ACT settings.account_sent_retry @api
@router.post("/{cid}/sent-copies/retry")
async def sent_copies_retry(cid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Try again to save the sent messages that could not be saved in the provider's Sent folder."""
    _session_only(p)
    c = await _owned(db, p, cid)
    _sent_copy_account(c)
    n = await sentcopy.retry(db, c)
    await db.commit()
    return {"queued": n}


class SentBackfillIn(BaseModel):
    dry_run: bool = True
    since: date | None = None


def _backfill_jobs(cid: int):
    return select(Job).where(Job.kind == sentcopy.BACKFILL_JOB, Job.payload["connector_id"].astext == str(cid))


# ACT settings.account_sent_backfill @api
@router.post("/{cid}/sent-backfill")
async def start_sent_backfill(cid: int, body: SentBackfillIn, p: Principal = Depends(current),
                              db: AsyncSession = Depends(get_db)):
    """Copy this account's past sent mail to the provider's Sent folder, or count it (dry_run).
    A run already going for this account is returned."""
    from mailr.core.audit import audit

    _session_only(p)
    c = await _owned(db, p, cid)
    _sent_copy_account(c)
    jid = await sentcopy.start_backfill(db, c, since=body.since, dry_run=body.dry_run)
    await audit(db, p, "settings.account_sent_backfill", str(cid),
                {"dry_run": body.dry_run, "since": body.since.isoformat() if body.since else None}, {"job": jid},
                always=not body.dry_run)
    await db.commit()
    return sentcopy.job_out(await get_existing(db, Job, jid))


@router.get("/{cid}/sent-backfill")
async def latest_sent_backfill(cid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """The newest count or copy for this account, or {"status": "none"}."""
    _session_only(p)
    await _owned(db, p, cid)
    j = (await db.execute(_backfill_jobs(cid).where(Job.user_id == p.user_id).order_by(Job.id.desc()).limit(1))).scalar()
    return sentcopy.job_out(j) if j is not None else {"status": "none"}


@router.get("/{cid}/sent-backfill/{job_id}")
async def sent_backfill_status(cid: int, job_id: int, p: Principal = Depends(current),
                               db: AsyncSession = Depends(get_db)):
    _session_only(p)
    await _owned(db, p, cid)
    j = await db.get(Job, job_id)
    if j is None or j.kind != sentcopy.BACKFILL_JOB or j.user_id != p.user_id or (j.payload or {}).get("connector_id") != cid:
        raise ApiError(404, "common.not_found")
    return sentcopy.job_out(j)


# ---------------- file uploads for imports ----------------


@router.post("/uploads", openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {
    "schema": {"type": "object", "required": ["file"], "properties": {"file": {"type": "string", "format": "binary"}}}}}}})
async def upload_import(request: Request, p: Principal = Depends(current)):
    """An .mbox or .zip for a file import (multipart field `file`), written into the user's import
    folder as it arrives; the import space, the daily limit, the size limit and the free disk are
    checked while it is written."""
    # Streamed by core/streamupload.py, never spooled first.
    _session_only(p)
    root = imports_root(p.user_id)
    root.mkdir(parents=True, exist_ok=True)
    limit = _max_import_bytes()
    # The import folder as a whole is capped too: earlier uploads count. So are the bytes uploaded
    # in the last 24 hours (imported files are removed, but their mail stays), and the space left
    # on the server's disk: never below MAILR_DISK_FAIL_PERCENT free.
    from mailr.connectors.mbox import (
        UPLOAD_LEDGER,
        import_bytes_today,
        import_quota_bytes,
        record_import_bytes,
    )
    from mailr.core import streamupload

    s = get_settings()
    quota = import_quota_bytes()
    held = sum(f.stat().st_size for f in root.rglob("*") if f.is_file() and not f.is_symlink()
               and f.name != UPLOAD_LEDGER)
    per_day = s.import_bytes_per_day
    today = import_bytes_today(p.user_id) if per_day else 0
    if per_day and today >= per_day:
        raise ApiError(429, "accounts.import_daily_limit", max=max(1, per_day // 1024**3))
    disk = streamupload.disk_reserve(root, s.disk_fail_percent)
    seen = {"size": 0}

    def open_file(filename: str, _ctype: str):
        name = os.path.basename(filename or "import")
        if not re.search(r"\.(mbox|mbx|zip)$", name, re.IGNORECASE):
            raise ApiError(400, "accounts.upload_mbox")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
        return root / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{pysecrets.token_hex(3)}-{safe}"

    def check(size: int, chunk: int) -> None:
        seen["size"] = size
        if size > limit:
            raise ApiError(413, "accounts.file_too_large_gb", max=limit // 1024**3)
        if held + size > quota:
            raise ApiError(413, "accounts.import_space_full")
        if per_day and today + size > per_day:
            raise ApiError(429, "accounts.import_daily_limit", max=max(1, per_day // 1024**3))
        disk(size, chunk)

    try:
        got = await streamupload.receive(request, open_file, check)
    finally:
        if seen["size"]:
            record_import_bytes(p.user_id, seen["size"])
    return {"path": got.path.name, "size": got.size, "filename": os.path.basename(got.filename or "import")}


async def _caldav_test(body: ConnectorDraft, private_ok: bool | None = None) -> dict[str, Any]:
    from mailr.calendar import client as cal

    cfg = body.config or {}
    url = str(cfg.get("url") or "")
    if not url.lower().startswith(("http://", "https://")):
        return {"ok": False, "detail": "The CalDAV URL must start with http:// or https://", "folders": [],
                "identities": [], "extensions": []}
    try:
        found = await cal.discover_calendars(url, str(cfg.get("username") or ""),
                                             str((body.secrets or {}).get("password") or ""), private_ok=private_ok)
    except Exception as e:  # noqa: BLE001  never the server's own error text
        return {"ok": False, "detail": f"Couldn't sign in: {cal.setup_error(e)}", "folders": [], "identities": [],
                "extensions": []}
    return {"ok": True, "detail": f"{len(found)} calendars and task lists found.",
            "folders": [{"name": c.get("name") or c.get("url"),
                         "role": "tasks" if c.get("tasks") and not c.get("events") else "events"} for c in found],
            "identities": [], "extensions": []}
