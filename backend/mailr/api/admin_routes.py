"""Instance administration: users, model endpoints, policies, status."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr import i18n
from mailr.api.auth_routes import drop_push_targets, revoke_signin_cookies, revoke_tokens
from mailr.api.schemas import user_out
from mailr.config import get_settings
from mailr.core import outbound
from mailr.core.audit import audit, notice
from mailr.core.auth import Principal, admin, current, hash_password, run_hashing, tls_admin
from mailr.core.crypto import encrypt_instance, model_endpoint_context
from mailr.core.users import create_user, delete_store_account
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import Connector, InstanceSetting, Job, Message, ModelEndpoint, Session, User


async def _audit_admin_change(request: Request, p: Principal = Depends(current)) -> None:
    """Every admin change (not reads) is written to the audit log in its own transaction, so
    it is recorded even when the change itself fails."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    from mailr.db import session_scope

    async with session_scope() as adb:
        await audit(adb, p, f"admin.{request.method.lower()}", request.url.path, always=True)


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(_audit_admin_change)])


class UserIn(BaseModel):
    email: str
    name: str = ""
    password: str | None = None
    role: str = Field("user", pattern="^(user|admin)$")


@router.get("/users")
async def users(q: str = "", offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=200)] = 50, p: Principal = Depends(admin),
                db: AsyncSession = Depends(get_db)):
    """Search (email or name) and page: {items, total}. Message counts only for the page."""
    limit = max(1, min(limit, 200))
    stmt = select(User)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(User.email).like(like), func.lower(User.name).like(like)))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(User.id).offset(max(0, offset)).limit(limit))).scalars().all()
    counts = dict((await db.execute(text(
        "SELECT user_id, count(*) FROM messages WHERE NOT deleted AND user_id = ANY(:ids) GROUP BY user_id"),
        {"ids": [u.id for u in rows]})).all()) if rows else {}
    from mailr.core.access import effective_groups, group_source
    from mailr.core.onboarding import summary as setup_summary

    local_tokens = await _local_only_tokens(db, rows)
    return {"items": [{**user_out(u), "messages": counts.get(u.id, 0), "groups": effective_groups(u),
                       "groups_source": group_source(u), "setup": setup_summary(u),
                       "local_only_tokens": local_tokens.get(u.id, [])} for u in rows], "total": total}


async def _local_only_tokens(db: AsyncSession, rows: list[User]) -> dict[int, list[dict[str, Any]]]:
    """Each user's live tokens marked local-only: metadata only (name, created, last used), never
    what the token read. `in_effect` is false when the user's policy no longer allows local-only
    agents; the token then works as an ordinary one (core/auth._token_principal)."""
    from datetime import UTC, datetime

    from mailr.core import access
    from mailr.models import ApiToken

    if not rows:
        return {}
    now = datetime.now(UTC)
    toks = (await db.execute(select(ApiToken).where(
        ApiToken.user_id.in_([u.id for u in rows]), ApiToken.local_only.is_(True), ApiToken.revoked.is_(False),
        or_(ApiToken.expires_at.is_(None), ApiToken.expires_at > now)).order_by(ApiToken.id))).scalars().all()
    by_id = {u.id: u for u in rows}
    allowed: dict[int, bool] = {}
    out: dict[int, list[dict[str, Any]]] = {}
    for t in toks:
        if t.user_id not in allowed:
            allowed[t.user_id] = (await access.for_user(db, by_id[t.user_id])).feature("local_only_tokens")
        out.setdefault(t.user_id, []).append({
            "id": t.id, "name": t.name, "created_at": t.created_at.isoformat(),
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
            "in_effect": allowed[t.user_id]})
    return out


@router.post("/users")
async def add_user(body: UserIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    if body.password is not None and len(body.password) < 10:
        raise ApiError(400, "auth.password_too_short", min=10)
    u = await create_user(db, body.email, body.name, password=body.password, role=body.role)
    from mailr.core import onboarding

    onboarding.mark_new(u)  # the setup guide opens at the first sign-in
    await db.commit()
    return user_out(u)


class UserPatch(BaseModel):
    name: str | None = None
    role: str | None = Field(None, pattern="^(user|admin)$")
    disabled: bool | None = None
    password: str | None = None
    # Mark a sign-up account's address as verified (single sign-on may then link to it by email).
    email_verified: bool | None = None


@router.patch("/users/{uid}")
async def patch_user(uid: int, body: UserPatch, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    u = await db.get(User, uid)
    if not u:
        raise ApiError(404, "admin.user_not_found")
    if uid == p.user_id and (body.role == "user" or body.disabled):
        raise ApiError(400, "admin.cannot_demote_self")
    renamed = body.name is not None and body.name != u.name
    was_disabled = bool(u.disabled)
    before_role, before_verified = u.role, bool(u.email_verified)
    if body.name is not None:
        u.name = body.name
    if body.role is not None:
        u.role = body.role
    if body.disabled is not None:
        if body.disabled != u.disabled:  # group calendars follow (calendar/groups.py)
            from mailr.calendar import groups as calendar_groups

            await calendar_groups.request_reconcile(db)
        u.disabled = body.disabled
        if body.disabled:
            await db.execute(text("DELETE FROM sessions WHERE user_id=:u"), {"u": uid})
            await db.execute(text("UPDATE api_tokens SET revoked=true WHERE user_id=:u"), {"u": uid})
            await drop_push_targets(db, uid)
    if body.password:
        if p.is_token:
            raise ApiError(403, "admin.password_not_by_token")
        if len(body.password) < 10:
            raise ApiError(400, "auth.password_too_short", min=10)
        u.password_hash = await run_hashing(hash_password, body.password)
        revoke_signin_cookies(u)
        ended = (await db.execute(text("DELETE FROM sessions WHERE user_id=:u RETURNING created_at"),
                                  {"u": uid})).scalars().all()
        await revoke_tokens(db, uid)
        await drop_push_targets(db, uid)
        # A reset is account recovery, as POST /users/{uid}/password: the IMAP password and the DAV
        # app passwords stop working too, and the channels set up meanwhile are reset.
        from mailr.core import recovery
        from mailr.core.users import sign_out_mail_apps

        await sign_out_mail_apps(u)
        await recovery.reset_channels(db, u, recovery.window_start(list(ended)))
    if body.email_verified is not None:
        u.email_verified = body.email_verified
    toggled = body.disabled is not None and body.disabled != was_disabled
    # What the person can read in their own activity.
    if renamed:
        await notice(db, p, uid, "name", i18n.msg("activity.admin.name", name=u.name))
    if body.role is not None and body.role != before_role:
        await notice(db, p, uid, "role", i18n.msg("activity.admin.role_added") if body.role == "admin"
                     else i18n.msg("activity.admin.role_removed"), {"role": body.role})
    if toggled:
        await notice(db, p, uid, "disabled", i18n.msg("activity.admin.disabled") if u.disabled
                     else i18n.msg("activity.admin.enabled"), {"disabled": bool(u.disabled)})
    if body.password:
        await notice(db, p, uid, "password", i18n.msg("activity.admin.password_set"))
    if body.email_verified is not None and body.email_verified != before_verified:
        await notice(db, p, uid, "email_verified", i18n.msg("activity.admin.email_verified")
                     if body.email_verified else i18n.msg("activity.admin.email_unverified"))
    if toggled:
        # A disabled user cannot sign in to the store either: the IMAP password stops working and
        # the phone app passwords pause (calendar/phones.py), so calendars shared with them are
        # inert on DAV too. Enabling undoes both.
        from mailr.calendar import phones
        from mailr.core.users import lock_store_signin, unlock_store_signin

        try:
            await (lock_store_signin(db, u) if u.disabled else unlock_store_signin(db, u))
        except Exception as e:  # noqa: BLE001  the next start repeats it (users.store_signin_follow)
            logging.getLogger("mailr.admin").warning("could not change the store sign-in of user %s: %s", uid, e)
        await phones.request_reconcile(db)
    await db.commit()
    if toggled:
        await phones.user_changed(u)
    if renamed:
        from mailr.calendar import builtin

        await builtin.user_renamed(u)  # the store account's description (shown to calendar sharees)
    return user_out(u)


class PasswordIn(BaseModel):
    password: str


@router.post("/users/{uid}/password")
async def set_user_password(uid: int, body: PasswordIn, p: Principal = Depends(admin),
                            db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "admin.password_not_by_token")
    u = await db.get(User, uid)
    if not u:
        raise ApiError(404, "admin.user_not_found")
    if len(body.password) < 10:
        raise ApiError(400, "auth.password_too_short", min=10)
    u.password_hash = await run_hashing(hash_password, body.password)
    revoke_signin_cookies(u)  # the known-client cookies stop counting
    ended = (await db.execute(text("DELETE FROM sessions WHERE user_id=:u RETURNING created_at"),
                              {"u": uid})).scalars().all()
    # A reset is account recovery: sessions and API tokens minted before it stop working, and so do
    # the store's IMAP password and DAV app passwords. The channels set up meanwhile are reset for
    # the user to review at their next sign-in (core/recovery.py).
    revoked = await revoke_tokens(db, uid)
    await drop_push_targets(db, uid)
    from mailr.core import recovery
    from mailr.core.users import sign_out_mail_apps

    apps = await sign_out_mail_apps(u)
    review = await recovery.reset_channels(db, u, recovery.window_start(list(ended)))
    await notice(db, p, uid, "password", i18n.msg("activity.admin.password_reset_tokens", count=revoked) if revoked
                 else i18n.msg("activity.admin.password_reset"))
    await db.commit()
    return {"ok": True, "revoked_tokens": revoked, **apps, "reset_channels": bool(review)}


@router.delete("/users/{uid}")
async def remove_user(uid: int, confirm: str = "", p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    u = await db.get(User, uid)
    if not u:
        raise ApiError(404, "admin.user_not_found")
    if uid == p.user_id:
        raise ApiError(400, "admin.cannot_delete_self")
    if confirm != u.email:
        raise HTTPException(400, "pass ?confirm=<the user's email> to delete the user and all their mail")  # i18n: internal
    from mailr.calendar import phones, sharing

    if u.stalwart_account_id:  # phones lose access first, whatever happens next
        try:
            await phones.revoke_all(u.stalwart_account_id)
        except Exception as e:  # noqa: BLE001  destroying the store account removes them too
            logging.getLogger("mailr.admin").warning("could not delete the app passwords of user %s: %s", uid, e)
    try:  # their grants on other people's calendars go first (calendar/sharing.py)
        await sharing.user_removed(db, u)
    except Exception as e:  # noqa: BLE001  the cleanup job removes what is left
        logging.getLogger("mailr.admin").warning("could not remove the calendar grants of user %s: %s", uid, e)
    from mailr.notes import sharing as note_sharing

    await note_sharing.user_removed(db, u)  # their note and table shares (notes/sharing.py)
    from sqlalchemy import text as _text

    await db.execute(_text("DELETE FROM model_usage WHERE user_id = :u"), {"u": uid})  # no foreign key
    await db.delete(u)
    # Their mail export goes too, here and on the backup target (core/purge.py).
    from mailr.core.jobs import enqueue

    await enqueue(db, "mail.purge_user_export", {"user_id": uid})
    await db.commit()
    # The mail store account goes once the user's rows are gone: a delete that fails above leaves
    # both, never a user whose mail is gone. A store that cannot be reached now is tried again.
    try:
        await delete_store_account(u)
    except Exception as e:  # noqa: BLE001
        logging.getLogger("mailr.admin").warning("could not delete the mail store account of user %s: %s", uid, e)
        await enqueue(db, "users.delete_store_account", {"account_id": u.stalwart_account_id})
        await db.commit()
    return {"ok": True}


# ---------------- model endpoints ----------------


class EndpointIn(BaseModel):
    name: str
    base_url: str
    api_key: str | None = None
    locality: str = Field("local", pattern="^(local|hosted)$")
    adapter: str = Field("openai", pattern="^(openai|jev)$")
    models: list[str] = []
    # Omitted: keep. Task defaults are normally set with PUT /admin/models/defaults/{task}.
    defaults: dict[str, str] | None = None
    enabled: bool = True


def ep_out(e: ModelEndpoint) -> dict[str, Any]:
    return {"id": e.id, "name": e.name, "base_url": e.base_url, "has_api_key": bool(e.api_key_enc),
            "locality": e.locality, "adapter": e.adapter, "models": e.models, "defaults": e.defaults,
            "enabled": e.enabled, "from_env": bool(e.from_env)}


async def _locality_check(e: ModelEndpoint) -> dict[str, Any] | None:
    """For a local endpoint, the connect-time check run now (core/outbound.py): {"ok", "error"}.
    Admin → Models shows "local (checked)", or the reason the endpoint is not used for local work.
    None for a hosted endpoint."""
    if not outbound.local(e):
        return None
    error = await outbound.check(outbound.endpoint_service(e, "chat"))
    return {"ok": error is None, "error": error}


@router.get("/models")
async def endpoints(p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """The endpoints, each with `health`: its last success and last error, and `locality_check`:
    for a local endpoint, whether every address of its host is private now."""
    from mailr.agent.transcribe import env_base, sync_env_asr
    from mailr.intel import modelhealth

    await sync_env_asr()
    health = await modelhealth.view(db)
    lang = i18n.user_language(p.user)
    q = select(ModelEndpoint).order_by(ModelEndpoint.id)
    if not env_base():
        q = q.where(ModelEndpoint.from_env.is_(False))
    eps = list((await db.execute(q)).scalars())
    checks = await asyncio.gather(*(_locality_check(e) for e in eps))
    return [{**ep_out(e), "health": modelhealth.of(health, "endpoint", e.name, lang), "locality_check": chk}
            for e, chk in zip(eps, checks, strict=True)]


async def _safe_base(url: str) -> str:
    """A model endpoint URL (admin routes only: an admin may use the LAN unless the instance
    refuses private hosts for everyone)."""
    from mailr.core.netguard import UnsafeTarget, check_url, private_mode

    base = url.rstrip("/")
    try:
        await check_url(base, private_ok=private_mode() != "none")
    except UnsafeTarget as ex:
        raise HTTPException(400, str(ex)) from None
    return base


async def _check_local(base: str, locality: str) -> None:
    """An endpoint is "local" only when every address of its host is private or loopback (an IP
    address or CIDR on the operator's MAILR_OUTBOUND_ALLOW list counts too; a host name there does
    not). Local-only mail and notes go to local endpoints, so the label is checked, not trusted:
    here when it is saved, and again on every connection (core/outbound.py)."""
    from urllib.parse import urlsplit

    if not outbound.local(locality):
        return
    host = urlsplit(base).hostname or ""
    if not host:
        raise ApiError(400, "admin.local_public_no_host")
    svc = outbound.Service(kind="chat", name=host, base_url=base, locality=locality, source="model_endpoint")
    if await outbound.check(svc) is not None:
        raise ApiError(400, "admin.local_public", host=host)


@router.post("/models")
async def add_endpoint(body: EndpointIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    await _check_local(await _safe_base(body.base_url), body.locality)
    e = ModelEndpoint(name=body.name, base_url=body.base_url.rstrip("/"),
                      locality=body.locality, adapter=body.adapter, models=body.models,
                      defaults=body.defaults or {}, enabled=body.enabled)
    db.add(e)
    await db.flush()
    if body.api_key:  # sealed for this row, so it needs the id
        e.api_key_enc = encrypt_instance(body.api_key, context=model_endpoint_context(e.id))
    await audit(db, p, "admin.model_endpoint.add", str(e.id), {"base_url": e.base_url, "locality": e.locality},
                always=True)
    await db.commit()
    return ep_out(e)


@router.patch("/models/{eid}")
async def patch_endpoint(eid: int, body: EndpointIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    e = await db.get(ModelEndpoint, eid)
    if not e:
        raise ApiError(404, "admin.endpoint_not_found")
    if e.from_env and (body.base_url.rstrip("/") != e.base_url.rstrip("/") or body.locality != e.locality
                       or body.adapter != e.adapter):
        raise ApiError(400, "dictation.env_endpoint")  # MAILR_ASR_URL sets its address
    new_base = e.base_url if e.from_env else await _safe_base(body.base_url)
    if not e.from_env:
        await _check_local(new_base, body.locality)
    before = {"base_url": e.base_url, "locality": e.locality}
    if new_base != e.base_url.rstrip("/"):
        await _forget_embed_checks(db, e.id)  # another server: its models are checked again
        if body.api_key is None:
            # A stored key is only ever sent to the URL it was saved for.
            e.api_key_enc = None
    e.name, e.base_url, e.locality, e.adapter = body.name, new_base, body.locality, body.adapter
    e.models, e.enabled = body.models, body.enabled
    if body.defaults is not None:
        e.defaults = body.defaults
    if body.api_key is not None:
        e.api_key_enc = (encrypt_instance(body.api_key, context=model_endpoint_context(e.id))
                         if body.api_key else None)
    await audit(db, p, "admin.model_endpoint.update", str(e.id), {"before": before,
                "after": {"base_url": e.base_url, "locality": e.locality}}, always=True)
    await db.commit()
    return ep_out(e)


class LocalPolicyIn(BaseModel):
    policy: str = Field(pattern="^(per_user|local_all|hosted)$")  # hosted: an alias of per_user for one release


# ACT settings.local_models_household @api
@router.get("/models/local-policy")
async def local_policy(p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """The household rule for hosted models: each person chooses, or local
    models only for everyone."""
    from mailr.intel import locality

    return {"policy": await locality.household(db), "choices": list(locality.POLICIES)}


@router.put("/models/local-policy")
async def set_local_policy(body: LocalPolicyIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    from mailr.intel import locality

    before = await locality.household(db)
    await locality.set_household(db, body.policy)
    after = await locality.household(db)
    await audit(db, p, "admin.models.local_policy", "", {"before": before, "after": after}, always=True)
    await db.commit()
    return {"policy": after, "choices": list(locality.POLICIES)}


def _task_row(eps: list[ModelEndpoint], task: str, health: dict[str, Any] | None = None,
              lang: str = i18n.DEFAULT, name: str | None = None,
              checked: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """`name`: the admin's own name for the assistant ("Ambrose and writing"). `checked`: per
    endpoint id, the models that made a search embedding, and those that failed the check
    (EMBED_CHECKED; the embed task only)."""
    from mailr.intel import llm, modelhealth

    def pick(key: str, local_only: bool) -> dict[str, str] | None:
        e = next((x for x in eps if x.enabled and x.defaults.get(key) and outbound.allowed(local_only, x)), None)
        return {"endpoint": e.name, "model": e.defaults[key]} if e else None

    return {"task": task, **llm.TASKS[task], **llm.task_words(task, lang, name),
            "default": pick(task, False), "local": pick(task + llm.LOCAL, True),
            "options": [{"endpoint": e.name, "models": list(e.models or []), "locality": e.locality, "adapter": e.adapter,
                         **({"checked": list((checked or {}).get(str(e.id)) or []),
                             "failed": list((checked or {}).get(FAILED + str(e.id)) or [])} if task == "embed" else {})}
                        for e in eps if e.enabled and llm.can(e, task)],
            # The last success and the last error of this kind of work.
            "health": modelhealth.of(health or {}, "task", task, lang)}


@router.get("/models/defaults")
async def model_defaults(p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """Models per task, server-wide: per task the default and the model for local-only mail."""
    from mailr.intel import llm, modelhealth

    eps = list((await db.execute(select(ModelEndpoint).order_by(ModelEndpoint.id))).scalars())
    health = await modelhealth.view(db)
    from mailr.knowledge import profile

    name = await profile.name_of(db, p.user_id)
    checked = await get_instance_setting(db, EMBED_CHECKED, {}) or {}
    return {"tasks": [_task_row(eps, t, health, i18n.user_language(p.user), name, checked) for t in llm.TASKS]}


class DefaultIn(BaseModel):
    endpoint: str | None = None  # None: no server default
    model: str | None = None
    local_endpoint: str | None = None  # None: local-only mail falls back to any local default, else none
    local_model: str | None = None


@router.put("/models/defaults/{task}")
async def set_model_default(task: str, body: DefaultIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """Exactly one server default and one local-only model per task (no hidden list order)."""
    from mailr.intel import llm

    if task not in llm.TASKS:
        raise HTTPException(404, "unknown task")  # i18n: internal
    eps = list((await db.execute(select(ModelEndpoint).order_by(ModelEndpoint.id))).scalars())
    checked = dict(await get_instance_setting(db, EMBED_CHECKED, {}) or {})
    if task == "embed":
        for key, name, model in ((task, body.endpoint, body.model), (task + llm.LOCAL, body.local_endpoint, body.local_model)):
            try:
                grew = await _embed_unchecked(eps, checked, key, name, model)
            except ApiError:
                # The failure is kept although the request is refused: the model is then listed as
                # one that makes no embeddings.
                await _set_instance(db, EMBED_CHECKED, dict(checked))
                await db.commit()
                raise
            if grew:
                await _set_instance(db, EMBED_CHECKED, checked)

    def assign(key: str, name: str | None, model: str | None, local_only: bool) -> None:
        target = None
        if name:
            target = next((e for e in eps if e.name == name), None)
            if target is None or not llm.can(target, task):
                raise ApiError(400, "models.endpoint_cannot_do_task", endpoint=name)
            if not outbound.allowed(local_only, target):
                raise ApiError(400, "admin.local_only_needs_local")
            model = model or (target.models[0] if target.models else "")
            if target.models and model not in target.models:
                raise ApiError(400, "models.model_not_listed")
        for e in eps:
            d = dict(e.defaults or {})
            d.pop(key, None)
            if e is target:
                d[key] = model
            if d != (e.defaults or {}):
                e.defaults = d

    assign(task, body.endpoint, body.model, False)
    assign(task + llm.LOCAL, body.local_endpoint, body.local_model, True)
    await audit(db, p, "admin.model_default", task, body.model_dump(), always=True)
    await db.commit()
    from mailr.intel import modelhealth
    from mailr.knowledge import profile

    return _task_row(eps, task, await modelhealth.view(db), i18n.user_language(p.user),
                     await profile.name_of(db, p.user_id), checked)


# InstanceSetting: {"<endpoint id>": [models]}, the models that made a search embedding when an
# admin chose them for the search index, and {"failed:<endpoint id>": [models]}, those that did not.
# Admin → Models per task offers the first for Search index and lists the second as disabled.
EMBED_CHECKED = "models.embed_checked"
FAILED = "failed:"


async def _embed_unchecked(eps: list[ModelEndpoint], checked: dict[str, list[str]], key: str,
                           name: str | None, model: str | None) -> bool:
    """Before a model becomes the search index's: one tiny /embeddings call, the indexer's own
    (llm.embed_request, its size and timeout). A chat-only model is refused with a plain message.
    The current choice and models already checked are not asked again. True when `checked` grew;
    a failure is recorded in `checked` too, before the refusal. An unknown endpoint or model is
    left to set_model_default's own checks."""
    from mailr.intel import llm
    from mailr.models import EMBED_DIM

    e = next((x for x in eps if x.name == name), None) if name else None
    if e is None or not llm.can(e, "embed"):
        return False
    model = model or (e.models[0] if e.models else "")
    if not model or (e.models and model not in e.models):
        return False
    if (e.defaults or {}).get(key) == model or model in checked.get(str(e.id), []):
        return False
    await _safe_base(e.base_url)
    failed = [m for m in checked.get(FAILED + str(e.id), []) if m != model]
    try:
        await llm.embed_request(llm.Resolved(e, model, llm.endpoint_key(e)), ["test"], EMBED_DIM, record=False)
    except Exception:  # noqa: BLE001
        checked[FAILED + str(e.id)] = [*failed, model]
        raise ApiError(400, "models.not_embedding_model") from None
    checked[str(e.id)] = [*checked.get(str(e.id), []), model]
    if failed or FAILED + str(e.id) in checked:  # it works now (a model was loaded on the server since)
        checked[FAILED + str(e.id)] = failed
    return True


@router.delete("/models/{eid}")
async def delete_endpoint(eid: int, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    e = await db.get(ModelEndpoint, eid)
    if not e:
        raise ApiError(404, "admin.endpoint_not_found")
    if e.from_env:
        raise ApiError(400, "dictation.env_endpoint")  # it comes back from MAILR_ASR_URL; turn it off instead
    await db.delete(e)
    await _forget_embed_checks(db, eid)
    await db.commit()
    return {"ok": True}


async def _forget_embed_checks(db: AsyncSession, eid: int) -> None:
    checked = dict(await get_instance_setting(db, EMBED_CHECKED, {}) or {})
    passed, failed = checked.pop(str(eid), None), checked.pop(FAILED + str(eid), None)
    if passed is not None or failed is not None:
        await _set_instance(db, EMBED_CHECKED, checked)


class ProbeIn(BaseModel):
    base_url: str = ""
    api_key: str | None = None
    id: int | None = None


@router.post("/models/probe")
async def probe_draft(body: ProbeIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """List models an endpoint serves, before or after saving it (id reuses the stored key)."""
    base, key = body.base_url.rstrip("/"), body.api_key
    if body.id:
        e = await db.get(ModelEndpoint, body.id)
        if not e:
            raise ApiError(404, "admin.endpoint_not_found")
        if key is None and e.api_key_enc:
            # A stored key is only ever sent to the endpoint it was saved for.
            if base and base != e.base_url.rstrip("/"):
                raise ApiError(400, "admin.api_key_again")
            from mailr.intel import llm

            key = llm.endpoint_key(e)
        base = base or e.base_url
    from mailr.core.netguard import UnsafeTarget, check_url, private_mode

    try:
        await check_url(base, private_ok=private_mode() != "none")
    except UnsafeTarget as ex:
        raise HTTPException(400, str(ex)) from None
    try:
        # Only the key and a GET go out: no user data, so the service counts as hosted here.
        svc = outbound.Service(kind="chat", name="(new endpoint)", base_url=base, locality=outbound.HOSTED,
                               source="model_endpoint")
        async with outbound.client(svc, timeout=10) as c:
            r = await c.get(base + "/models", headers={"Authorization": f"Bearer {key}"} if key else {})
        r.raise_for_status()
        return {"ok": True, "models": [m["id"] for m in r.json().get("data", [])]}
    except Exception as ex:  # noqa: BLE001
        raise ApiError(502, "admin.list_models_failed", error=str(ex)[:277]) from None


@router.post("/models/{eid}/probe")
async def probe_endpoint(eid: int, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """List models the endpoint serves (GET /models) to help fill in the form."""
    e = await db.get(ModelEndpoint, eid)
    if not e:
        raise ApiError(404, "admin.endpoint_not_found")
    await _safe_base(e.base_url)
    from mailr.intel import llm

    key = llm.endpoint_key(e)
    try:
        async with outbound.client(outbound.endpoint_service(e, "chat"), timeout=10) as c:
            r = await c.get(e.base_url + "/models", headers={"Authorization": f"Bearer {key}"} if key else {})
        r.raise_for_status()
        return {"ok": True, "models": [m["id"] for m in r.json().get("data", [])]}
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "error": str(ex)[:300]}


class TestIn(BaseModel):
    model: str | None = None


def _test_model(e: ModelEndpoint, wanted: str | None) -> tuple[str, str]:
    """(kind, model) for an endpoint's test: a decision for a Jev endpoint, an embedding for an
    endpoint used only for the search index, else one short chat."""
    d = e.defaults or {}
    if e.adapter == "jev":
        return "decide", wanted or d.get("classify") or d.get("classify.local") or (e.models or [""])[0]
    if wanted:
        only_embed = wanted in (d.get("embed"), d.get("embed.local")) and wanted not in [
            v for k, v in d.items() if not k.startswith("embed")]
        return ("embed" if only_embed else "chat"), wanted
    for key in ("chat", "chat.local", "classify", "classify.local"):
        if d.get(key):
            return "chat", d[key]
    if d.get("embed") or d.get("embed.local"):
        return "embed", d.get("embed") or d["embed.local"]
    if d.get("transcribe") or d.get("transcribe.local"):
        return "transcribe", d.get("transcribe") or d["transcribe.local"]
    return "chat", (e.models or [""])[0]


@router.post("/models/{eid}/test")
async def test_endpoint(eid: int, body: TestIn | None = None, p: Principal = Depends(admin),
                        db: AsyncSession = Depends(get_db)):
    """Admin → Model endpoints → Test: one tiny call (a five-token chat, a one-word
    embedding, or one yes/no decision for a Jev endpoint). Answers {ok, kind, model, latency_ms,
    reply | error}, the error in plain words; never the key. The result counts as the endpoint's
    last success or error."""
    import time

    from mailr.intel import llm, modelhealth

    e = await db.get(ModelEndpoint, eid)
    if not e:
        raise ApiError(404, "admin.endpoint_not_found")
    await _safe_base(e.base_url)
    kind, model = _test_model(e, (body.model if body else None) or None)
    out: dict[str, Any] = {"kind": kind, "model": model}
    if not model:
        out |= {"ok": False, "latency_ms": None, "error": "No model name yet. Add one in Edit, or use Discover."}
        return out
    r = llm.Resolved(e, model, llm.endpoint_key(e))
    t0 = time.monotonic()
    error: str | None = None
    reply = ""
    try:
        if kind == "decide":
            from mailr.intel import jev

            ans = await jev.decide(r, {"subject": "Test", "from": "mailr"}, {"ok": jev.noul("Is this a test?")},
                                   timeout=20)
            if ans is None:
                error = i18n.text("health.model.no_decision")
            else:
                reply = "decision received"
        elif kind == "embed":
            vec = await llm.embed_request(r, ["test"], 8, timeout=20)
            reply = f"{len(vec[0])} numbers"
        elif kind == "transcribe":
            # A speech server answers GET /models, or at least answers: no clip is sent.
            async with outbound.client(outbound.endpoint_service(e, "transcribe"), timeout=20) as c:
                resp = await c.get(llm._url(r, "/models"), headers=llm._headers(r))
            if resp.status_code >= 500:
                error = modelhealth.plain(None, resp.status_code, resp.text[:300])
            else:
                reply = "the speech server answers"
        else:
            async with llm._http(r, "chat", local_only=False, timeout=30) as c:
                resp = await c.post(llm._url(r, "/chat/completions"), headers=llm._headers(r), json={
                    "model": model, "messages": [{"role": "user", "content": "Reply with the word OK."}],
                    "max_tokens": 5, "temperature": 0})
            if resp.status_code >= 400:
                error = modelhealth.plain(None, resp.status_code, resp.text[:300])
            else:
                try:
                    reply = str(resp.json()["choices"][0]["message"].get("content") or "").strip()[:60] or "(empty reply)"
                except (ValueError, KeyError, IndexError, TypeError):
                    error = i18n.text("health.model.not_openai")
    except Exception as ex:  # noqa: BLE001
        error = modelhealth.plain(ex)
    out["latency_ms"] = int((time.monotonic() - t0) * 1000)
    if kind in ("chat", "transcribe"):  # embed and decide note their own outcome (llm.embed_request, jev.decide)
        await modelhealth.record(None, e.name, model, error is None, error)
    shown = i18n.render(i18n.msg_of(error), i18n.user_language(p.user), fallback=error) if error else None
    out |= {"ok": error is None, **({"error": shown} if error else {"reply": reply})}
    return out


# ---------------- diagnostics ----------------


@router.get("/diagnostics")
async def diagnostics(refresh: bool = False, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """Admin → Diagnostics: every readiness check with its plain name,
    the backup, push, the worker's queue, each mail account's last sync, the model tasks and
    endpoints, and the trusted proxies. `refresh` runs the checks now instead of using the
    20-second cache. Admins only; account names and owners are shown (it is the admin's own
    instance), never mail content or secrets."""
    from datetime import UTC, datetime

    from mailr.core import accounthealth, backup, health, healthlog, proxies, push
    from mailr.intel import llm, modelhealth

    if refresh:
        health.clear_cache()
    ready = await health.ready()
    lang = i18n.user_language(p.user)
    checks = [{"name": n, "label": health.label(n, lang),
               **{k: v for k, v in c.items() if k != "detail_msg"}, "detail": health.detail(c, lang)}
              for n, c in ready["checks"].items()]

    s = get_settings()
    target = s.backup_target or ""
    users = list((await db.execute(text(
        "SELECT u.id, u.email FROM users u WHERE NOT u.disabled AND EXISTS (SELECT 1 FROM messages m "
        "WHERE m.user_id = u.id AND m.blob_id IS NOT NULL AND NOT m.deleted AND NOT m.draft) ORDER BY u.id"))).all())

    def runs() -> list[dict[str, Any]]:
        out = []
        for uid, email in users:
            local = backup.last_run(backup.export_root(uid))
            copy = backup.last_run(backup.target_root(uid)) if target else None
            out.append({"user": email, "exported_at": local.isoformat() if local else None,
                        "copied_at": copy.isoformat() if copy else None})
        return out

    backup_runs = await asyncio.to_thread(runs) if s.mail_export else []
    problem_m = await asyncio.to_thread(backup.target_problem_msg) if target else None
    problem = i18n.render(problem_m, lang) if problem_m else None
    from mailr.core import purge

    held = await purge.held_sweeps(db)  # deleted mail the daily sweep held back (core/purge.py)

    jobs = dict((await db.execute(select(Job.status, func.count()).group_by(Job.status))).all())
    due = (await db.execute(text(
        "SELECT count(*), extract(epoch FROM now() - min(run_at)) FROM jobs "
        "WHERE status = 'queued' AND run_at <= now()"))).one()
    failed_day = (await db.execute(text(
        "SELECT count(*) FROM jobs WHERE status = 'failed' AND created_at > now() - interval '1 day'"))).scalar_one()

    conns = (await db.execute(select(Connector, User.email).join(User, User.id == Connector.user_id)
                              .order_by(Connector.user_id, Connector.id))).all()
    now = datetime.now(UTC)
    unsynced = await accounthealth.unsynced_changes(db, [c.id for c, _e in conns if c.mode == "mirror"])
    from mailr.connectors import sentcopy

    unsaved = await sentcopy.unsaved(db, [c.id for c, _e in conns if sentcopy.active(c)])

    devices = dict((await db.execute(text("SELECT kind, count(*) FROM push_targets GROUP BY kind"))).all())
    push_log = await healthlog.read(db, push.PUSH_HEALTH)
    mh = await modelhealth.view(db)
    eps = list((await db.execute(select(ModelEndpoint).order_by(ModelEndpoint.id))).scalars())
    from mailr.knowledge import profile

    aname = await profile.name_of(db, p.user_id)  # the admin's own name for the assistant

    return {
        "ok": ready["ok"], "checked_at": ready.get("checked_at"), "checks": checks,
        "backup": {"mail_export": s.mail_export, "store_backup_check": s.store_backup_check,
                   "target_configured": bool(target), "target_problem": problem, "purge_held": held,
                   "marker": backup.TARGET_MARKER, "users": backup_runs},
        "push": {"ntfy_configured": bool(s.ntfy_url),
                 "devices": {k: int(v) for k, v in devices.items()},
                 "transports": {k: healthlog.entry(push_log, f"transport:{k}", lang) for k in ("webpush", "unifiedpush", "ntfy")}},
        "queue": {"queued": int(jobs.get("queued", 0)), "running": int(jobs.get("running", 0)),
                  "due": int(due[0] or 0), "oldest_due_seconds": int(due[1]) if due[1] is not None else None,
                  "failed_last_day": int(failed_day)},
        "connectors": [{"id": c.id, "name": c.name, "user": email, "kind": c.kind, "mode": c.mode,
                        "status": c.status, "detail": (c.status_detail or "")[:200],
                        "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
                        "health": accounthealth.of(c, now, lang, unsynced.get(c.id, 0),
                                                   unsaved.get(c.id, 0))} for c, email in conns],
        "models": {"tasks": [{"task": t, "label": llm.task_words(t, lang, aname)["label"], "health": modelhealth.of(mh, "task", t, lang)}
                             for t in llm.TASKS],
                   "endpoints": [{"id": e.id, "name": e.name, "enabled": e.enabled,
                                  "health": modelhealth.of(mh, "endpoint", e.name, lang)} for e in eps]},
        "proxy": {"trusted": proxies.trusted(), "source": proxies.source(),
                  "untrusted_seen_at": datetime.fromtimestamp(proxies.untrusted_seen(), UTC).isoformat()
                  if proxies.untrusted_seen() else None},
    }


# ---------------- policies ----------------


async def get_instance_setting(db: AsyncSession, key: str, default: Any = None) -> Any:
    row = await db.get(InstanceSetting, key)
    return row.value if row else default


async def instance_local_only_domains(db: AsyncSession) -> list[str]:
    return await get_instance_setting(db, "local_only_domains", []) or []


async def _set_instance(db: AsyncSession, key: str, value: Any) -> None:
    row = await db.get(InstanceSetting, key)
    if row is None:
        db.add(InstanceSetting(key=key, value=value))
    else:
        row.value = value


async def _image_policy(db: AsyncSession) -> dict:
    from mailr.core import egress as egress_mod
    from mailr.core.imageproxy import egress_from_env, instance_config, mask_egress

    on, egress = await instance_config(db)
    return {"image_proxy": on, "image_proxy_egress": mask_egress(egress), "image_proxy_egress_env": bool(egress_from_env()),
            # MAILR_REQUIRE_EGRESS: the proxy is always on and nothing is fetched without the egress.
            "require_egress": egress_mod.required(),
            # The worker's last egress check, with the exit address (admins only).
            "egress_check": egress_mod.admin_view(await egress_mod.state(), bool(egress))}


@router.get("/policies")
async def policies(p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    return {"local_only_domains": await instance_local_only_domains(db), **await _image_policy(db)}


class PolicyIn(BaseModel):
    local_only_domains: list[str] | None = None
    # The image proxy (core/imageproxy.py) and the HTTP proxy its fetches go through ("" = direct).
    image_proxy: bool | None = None
    image_proxy_egress: str | None = None


@router.put("/policies")
async def set_policies(body: PolicyIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    from mailr.core.imageproxy import check_egress, mask_egress, open_egress, seal_egress

    if body.image_proxy is not None:
        await _set_instance(db, "image_proxy", body.image_proxy)
    if body.image_proxy_egress is not None:
        from mailr.core.imageproxy import egress_from_env

        if egress_from_env():
            raise ApiError(409, "admin.egress_from_env")
        cur = open_egress(await get_instance_setting(db, "image_proxy_egress", "") or "")
        # The masked value from GET means "unchanged".
        if body.image_proxy_egress != mask_egress(cur):
            try:
                egress = await asyncio.to_thread(check_egress, body.image_proxy_egress)
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
            await _set_instance(db, "image_proxy_egress", seal_egress(egress))
    if body.image_proxy is not None or body.image_proxy_egress is not None:
        await audit(db, p, "admin.image_proxy", "", {"image_proxy": body.image_proxy,
                                                       "egress_set": bool(body.image_proxy_egress)})
    if body.local_only_domains is None:
        await db.commit()
        return {"local_only_domains": await instance_local_only_domains(db), **await _image_policy(db)}
    doms = sorted({d.strip().lower().lstrip("@") for d in body.local_only_domains if d.strip()})
    row = await db.get(InstanceSetting, "local_only_domains")
    if row is None:
        db.add(InstanceSetting(key="local_only_domains", value=doms))
    else:
        row.value = doms
    # Existing connectors for those domains become local_only right away (and their mail).
    for c in (await db.execute(select(Connector))).scalars():
        if await connector_matches_domains_db(db, c, doms):
            if c.data_class != "local_only":
                await notice(db, p, c.user_id, "account_local_only",
                             i18n.msg("activity.admin.account_local_only", name=c.name), {"connector_id": c.id})
            await set_connector_data_class(db, c, "local_only")
    await db.commit()
    return {"local_only_domains": doms, **await _image_policy(db)}


# ---------------- built-in calendars ----------------


class CalendarsIn(BaseModel):
    builtin: bool
    # "everyone", or "groups": members of groups whose access policy says "Members get a mailr calendar"
    who: str | None = Field(None, pattern="^(everyone|groups)$")
    # Who users may share their calendars with (calendar/sharing.py): anyone, groups (people who share a
    # group with them) or nobody. Omitted: unchanged.
    sharing: str | None = Field(None, pattern="^(anyone|groups|nobody)$")
    # Phones and desktop apps (calendar/phones.py): the switch and the public https address. Omitted: unchanged.
    phone_access: bool | None = None
    phone_url: str | None = Field(None, max_length=300)


# ACT settings.builtin_calendars @api
@router.get("/calendars")
async def calendars_setting(p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """The instance setting `calendars.builtin`, with how many users have the connector."""
    from mailr.calendar import builtin, phones

    return {**await builtin.status(db), **await phones.status(db)}


@router.put("/calendars")
async def set_calendars_setting(body: CalendarsIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """On: every user gets the built-in calendar (a backfill job; new users at creation), or with
    `who: groups` only admins and members of groups whose access policy turns on "Members get a
    mailr calendar". Off: the built-in calendars are no longer listed or synced; nothing is
    deleted."""
    from mailr.calendar import builtin, phones

    await builtin.set_instance_enabled(db, body.builtin, body.who)
    if body.sharing is not None:  # ACT settings.calendar_sharing @api
        from mailr.calendar import sharing

        await sharing.set_policy(db, body.sharing)
    phone_on = body.phone_access
    if not body.builtin and await phones.instance_enabled(db):
        phone_on = False  # phones reach the built-in calendars only
    if phone_on is not None or body.phone_url is not None:  # ACT settings.phone_access @api
        try:
            await phones.set_instance(db, phone_on, body.phone_url)
        except phones.PhoneAccessError as e:
            await db.rollback()
            raise HTTPException(e.status, str(e)) from None
    await phones.request_reconcile(db)  # built-in calendars or the policy may have changed
    await audit(db, p, "admin.calendars.builtin", "", {"builtin": body.builtin, "who": body.who,
                                                      "sharing": body.sharing, "phone_access": phone_on,
                                                      "phone_url": body.phone_url}, always=True)
    await db.commit()
    if phone_on is not None:
        try:
            await phones.apply_store_settings()
        except Exception as e:  # noqa: BLE001  the next start applies it
            logging.getLogger("mailr.admin").warning("could not apply Stalwart's phone access setting: %s", e)
    return {**await builtin.status(db), **await phones.status(db)}


# ACT settings.phone_access_check @api
@router.post("/calendars/phone-check")
async def phone_access_check(p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """Is the public DAV address reachable and restricted as the docs say? Fetched through the
    same egress rules as remote content; when they forbid it, the answer says
    why and lists commands to run from outside instead."""
    from mailr.calendar import phone_check, phones

    url = await phones.public_url(db)
    if not url:
        raise ApiError(409, "admin.public_address_first")
    return await phone_check.run(db, url)


def _connector_addresses(c: Connector) -> list[str]:
    cfg = c.config or {}
    addrs = [cfg.get("email"), cfg.get("username"), (cfg.get("imap") or {}).get("username"),
             (cfg.get("smtp") or {}).get("username"), cfg.get("url")]
    addrs += list(cfg.get("addresses") or []) + list(cfg.get("identities") or [])
    return [str(a).strip().lower() for a in addrs if a]


def connector_matches_domains(c: Connector, doms: list[str]) -> bool:
    """Every address the connector is configured with (login names, identities, CalDAV URL
    host) is checked, so the policy cannot be sidestepped by leaving `email` empty."""
    from urllib.parse import urlsplit

    for a in _connector_addresses(c):
        host = urlsplit(a).hostname if "://" in a else a.rsplit("@", 1)[-1]
        if not host:
            continue
        if any(host == d or host.endswith("." + d) for d in doms):
            return True
    return False


async def connector_matches_domains_db(db: AsyncSession, c: Connector, doms: list[str]) -> bool:
    if not doms:
        return False
    if connector_matches_domains(c, doms):
        return True
    if c.id is None:
        return False
    from mailr.models import Identity

    idents = (await db.execute(select(Identity.email).where(Identity.connector_id == c.id))).scalars().all()
    return any(e.rsplit("@", 1)[-1] == d or e.rsplit("@", 1)[-1].endswith("." + d) for e in idents for d in doms)


async def set_connector_data_class(db: AsyncSession, c: Connector, value: str) -> None:
    """Change a connector's data class and restamp the messages it delivered. Becoming local-only
    also marks what was learned from them (knowledge/reclass.py)."""
    from sqlalchemy import update

    was = c.data_class
    c.data_class = value
    if c.id is not None:
        await db.execute(update(Message).where(Message.connector_id == c.id)
                         .values(local_only=(value == "local_only")))
        if value == "local_only" and was != "local_only":
            from mailr.knowledge.reclass import mark_learned_local_only

            await mark_learned_local_only(db, c)


# ---------------- status ----------------


@router.get("/status")
async def status(p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    jobs = dict((await db.execute(select(Job.status, func.count()).group_by(Job.status))).all())
    failed = (await db.execute(select(Job.kind, Job.error, Job.created_at).where(Job.status == "failed")
                               .order_by(Job.id.desc()).limit(10))).all()
    conns = (await db.execute(select(Connector.id, Connector.name, Connector.user_id, Connector.status,
                                     Connector.status_detail, Connector.last_sync_at))).all()
    from mailr.core.health import check_store

    store_ok = (await check_store())[0]
    return {
        "store": {"ok": store_ok},
        "users": (await db.execute(select(func.count()).select_from(User))).scalar_one(),
        "messages": (await db.execute(select(func.count()).select_from(Message).where(Message.deleted.is_(False)))).scalar_one(),
        "sessions": (await db.execute(select(func.count()).select_from(Session))).scalar_one(),
        "jobs": jobs,
        "failed_jobs": [{"kind": k, "error": e[:300], "at": t.isoformat()} for k, e, t in failed],
        "connectors": [{"id": i, "name": n, "user_id": u, "status": s, "detail": d[:200],
                        "last_sync_at": ls.isoformat() if ls else None} for i, n, u, s, d, ls in conns],
    }


# ---------------- IMAP TLS certificate ----------------


class TlsIn(BaseModel):
    certificate: str
    private_key: str


@router.get("/tls")
async def tls_status(p: Principal = Depends(tls_admin)):
    from mailr.core import tls

    return {"installed": await tls.status(), "hostname": get_settings().imap_hostname}


@router.put("/tls")
async def tls_install(body: TlsIn, p: Principal = Depends(tls_admin)):
    from mailr.core import tls

    try:
        return await tls.install(body.certificate, body.private_key)
    except tls.TlsError as e:
        raise HTTPException(400, str(e)) from None
