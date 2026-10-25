"""The setup guide (core/onboarding.py): progress, the quick chat that fills the "About you"
profile (knowledge/interview.py), saving what the user approved, and the admin's presets
("Prepare a new user", Admin → Users).

Only the signed-in user reads or changes their own guide; API tokens get 403. The quick chat uses
the user's chat model under their access policy and saves nothing; POST /onboarding/about saves
what the user approved, and nothing else does.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.config import get_settings
from mailr.core import onboarding, phone_apps
from mailr.core.audit import audit
from mailr.core.auth import Principal, admin, current
from mailr.db import get_db, get_existing
from mailr.i18n import ApiError
from mailr.models import Connector, Identity, User

log = logging.getLogger("mailr.onboarding")
router = APIRouter(tags=["onboarding"])

def apk_url() -> str:
    """Where phones get the mailr Android app (core/phone_apps.py): MAILR_ANDROID_APK_URL, else
    the APK published to this server, else the latest release."""
    from mailr.core import phone_apps

    return phone_apps.url("mailr")


FALLBACK = "The assistant can't chat right now. You can fill in a few fields instead."


def _user_only(p: Principal) -> None:
    if p.is_token:
        raise ApiError(403, "onboarding.user_only")


async def _has_accounts(db: AsyncSession, uid: int) -> bool:
    n = (await db.execute(select(func.count()).select_from(Connector).where(
        Connector.user_id == uid, Connector.kind.in_(("imap", "gmail", "m365", "file"))))).scalar_one()
    return n > 0


async def _assistant(db: AsyncSession, user: User) -> dict[str, Any]:
    """Whether the quick chat can run: the policy allows the assistant and a chat model resolves."""
    from mailr.core import access
    from mailr.intel import llm
    from mailr.knowledge import profile

    acc = await access.for_user(db, user)
    allowed = acc.feature("assistant")
    model = allowed and await llm.available(db, user, "chat", sensitive=False)
    row = await profile.get(db, user.id, create=False)
    name = profile.assistant_name(row)
    return {"allowed": allowed, "available": bool(model), "name": name,
            "message": None if allowed else access.no_assistant_message(name)}


async def _out(db: AsyncSession, user: User) -> dict[str, Any]:
    st = onboarding.state(user)
    s = get_settings()
    return {**onboarding.public(user, has_accounts=await _has_accounts(db, user.id)),
            "offer": {"assistant": True, "notes": True, **(st.get("offer") or {})},
            "assistant": await _assistant(db, user),
            "phone": {"server": s.base_url.rstrip("/"), "apk_url": apk_url(),
                      "get_app": s.base_url.rstrip("/") + "/get-app", "apps": phone_apps.releases()}}


async def _locked(db: AsyncSession, uid: int) -> User:
    """The user row, locked until commit: two quick moves (a step marked, then the next one shown)
    must not overwrite each other's progress."""
    return (await db.execute(select(User).where(User.id == uid).with_for_update()
                             .execution_options(populate_existing=True))).scalar_one()


@router.get("/onboarding")
async def get_onboarding(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """The guide's progress and what it needs (assistant availability, the phone links). A new
    account takes its admin preset here the first time."""
    _user_only(p)
    u = await get_existing(db, User, p.user_id)
    if onboarding.preset_due(u):
        # Locked and read again: the user's copy from sign-in may be older than what /me (the
        # browser's zone) or a settings save wrote since.
        u = await _locked(db, p.user_id)
        await onboarding.apply_preset(db, u)
        await db.commit()
    return await _out(db, u)


class ProgressIn(BaseModel):
    step: str | None = None
    mark: dict[str, str] | None = None
    status: str | None = None


@router.put("/onboarding")
async def put_onboarding(body: ProgressIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Move to a step, mark steps done or skipped, finish (status done) or close (dismissed)."""
    _user_only(p)
    u = await _locked(db, p.user_id)
    try:
        onboarding.update(u, step=body.step, mark=body.mark, status=body.status)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    await db.commit()
    return await _out(db, u)


@router.post("/onboarding/restart")
async def restart(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Settings → You → Setup guide."""
    _user_only(p)
    u = await _locked(db, p.user_id)
    onboarding.restart(u)
    await db.commit()
    return await _out(db, u)


# ---------------------------------------------------------------- the quick chat


class ChatIn(BaseModel):
    messages: list[dict[str, Any]] = Field(default_factory=list, max_length=40)


async def _chat_ready(db: AsyncSession, p: Principal) -> tuple[User, str, str]:
    """(user, first name, assistant name) once the policy allows the assistant."""
    from mailr.core import access
    from mailr.knowledge import profile

    _user_only(p)
    u = await db.get(User, p.user_id)
    await access.require_feature(db, u, "assistant")
    row = await profile.get(db, u.id, create=False)
    first, _ = profile.names(row.data or {}, u)
    return u, ("" if first == "the user" else first), profile.assistant_name(row)


def _unavailable(e: Exception) -> JSONResponse:
    log.info("onboarding chat unavailable: %s: %s", type(e).__name__, str(e)[:200])
    return JSONResponse({"detail": FALLBACK, "fallback": True}, status_code=503)


@router.post("/onboarding/chat")
async def chat(body: ChatIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """The next turn of "Have a quick chat": {reply, done}. With no messages, the opening
    question (no model call, but the model must be available). Nothing is saved."""
    from mailr.intel import llm
    from mailr.knowledge import interview

    u, first, name = await _chat_ready(db, p)
    try:
        msgs = interview.clean_messages(body.messages)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if not msgs:
        if not await llm.available(db, u, "chat", sensitive=False):
            return _unavailable(llm.NoModel("chat"))
        return {"reply": interview.opener(first, name), "done": False}
    if msgs[-1]["role"] != "user":
        raise HTTPException(400, "the last message must be the user's answer")  # i18n: internal
    prompt = [{"role": "system", "content": interview.chat_prompt(first, name)}, *msgs]
    try:
        # Her own words, no mail: not sensitive. The model is her policy's chat model (llm.resolve).
        out = await llm.chat(db, u, prompt, task="chat", feature="setup", sensitive=False, temperature=0.5, max_tokens=300,
                             timeout=60)
    except (llm.NoModel, llm.LocalOnlyViolation, RuntimeError, OSError) as e:
        return _unavailable(e)
    except Exception as e:  # noqa: BLE001  a timeout or a malformed answer: the fields instead
        return _unavailable(e)
    reply, done = interview.parse_reply(out.get("content") or "")
    users = sum(1 for m in msgs if m["role"] == "user")
    if users >= 7:
        done = True  # long enough: on to the summary
    return {"reply": reply or "Thank you! That's everything I need.", "done": done}


@router.post("/onboarding/chat/summary")
async def chat_summary(body: ChatIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """What the chat learned, as a proposal: {profile, signature, memories}. Nothing is saved
    until the user approves it (POST /onboarding/about)."""
    from mailr.intel import llm
    from mailr.knowledge import interview

    u, first, name = await _chat_ready(db, p)
    try:
        msgs = interview.clean_messages(body.messages)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if not any(m["role"] == "user" for m in msgs):
        raise ApiError(400, "onboarding.nothing_to_summarise")
    prompt = [{"role": "system", "content": interview.summary_prompt(first, name)},
              {"role": "user", "content": interview.transcript(msgs, first, name)}]
    try:
        out = await llm.chat(db, u, prompt, task="chat", feature="setup", sensitive=False, temperature=0.1, max_tokens=900,
                             timeout=90)
    except Exception as e:  # noqa: BLE001  any failure: the fields instead
        return _unavailable(e)
    return interview.clean_summary(out.get("content") or "")


class MemoryIn(BaseModel):
    text: str = Field(max_length=1000)
    kind: str = "preference"


class AboutIn(BaseModel):
    profile: dict[str, str] = Field(default_factory=dict)
    memories: list[MemoryIn] = Field(default_factory=list, max_length=12)
    # A plain-text sign-off for the signature of the identities named (all of them when None).
    signature: str | None = Field(None, max_length=1000)
    identity_ids: list[int] | None = None
    # "fields" or "chat": how the user filled it in (for the audit log).
    source: str = Field("fields", pattern="^(fields|chat)$")


@router.post("/onboarding/about")
async def save_about(body: AboutIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Save what the user approved: profile fields, notes to remember (their own words, so they
    are trusted, source "user") and the signature. The one write of the About you step."""
    from mailr.core.events import notify
    from mailr.knowledge import interview, memory, profile

    _user_only(p)
    row = await profile.get(db, p.user_id)
    try:
        data = profile.clean({k: v for k, v in body.profile.items() if k in profile.FIELDS})
        mems = [memory.clean_memory({"text": m.text, "kind": m.kind}) for m in body.memories if m.text.strip()]
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if data:
        row.data = {**(row.data or {}), **data}
    saved = []
    for m in mems:
        mm, _ = await memory.remember(db, p, {**m, "about": {"self": True}}, source="user")  # About you
        saved.append(mm.id)
    sig_ids: list[int] = []
    if body.signature is not None and body.signature.strip():
        html = interview.signature_html(body.signature)
        q = select(Identity).where(Identity.user_id == p.user_id)
        if body.identity_ids is not None:
            q = q.where(Identity.id.in_(body.identity_ids))
        for ident in (await db.execute(q)).scalars():
            ident.signature_html = html
            sig_ids.append(ident.id)
    # In the guide, the step counts as done once saved, so a return to it shows "Saved", not the
    # chooser again (the Continue that follows marks it too).
    u = await _locked(db, p.user_id)
    if onboarding.state(u).get("status") == "active":
        onboarding.update(u, mark={"about": "done"})
    await audit(db, p, "onboarding.about", body.source,
                {"fields": sorted(data), "memories": len(saved), "signatures": len(sig_ids)}, always=True)
    await notify(db, p.user_id, {"type": "changes", "entity": "assistant_profile"})
    await db.commit()
    return {"profile": sorted(data), "memories": saved, "signatures": sig_ids}


# ---------------------------------------------------------------- admin: Prepare a new user


class PresetIn(BaseModel):
    match: dict[str, str]
    defaults: dict[str, Any] = Field(default_factory=dict)
    note: str = ""
    # An IMAP/SMTP account to connect at the first sign-in (core/managed.py; email presets only,
    # with accounts.admin_connect on). {} removes it; omitted keeps it. Its `secrets` are sealed
    # for this preset; on a change, an omitted password keeps the saved one for the same servers.
    mail_account: dict[str, Any] | None = None


def _preset_out(item: dict[str, Any]) -> dict[str, Any]:
    from mailr.core import managed

    return {**{k: item.get(k) for k in ("id", "match", "defaults", "note", "used_by", "used_at", "applied", "created_at")},
            "mail_account": managed.preset_account_out(item.get("mail_account"))}


async def _preset_account(db: AsyncSession, p: Principal, item: dict[str, Any], body: PresetIn) -> None:
    """Put the body's mail account on the preset `item` (which has its id), sealed."""
    from mailr.api.connector_routes import ConnectorDraft, _hosts, _normalize, check_store_allowed
    from mailr.core import managed

    if body.mail_account is None:
        return
    acct = body.mail_account
    if not acct:
        item.pop("mail_account", None)
        return
    if p.is_token:
        raise ApiError(403, "onboarding.prepare_not_by_token")
    if not await managed.enabled(db):
        raise ApiError(403, "onboarding.admins_may_not_connect")
    if not item["match"].get("email"):
        raise ApiError(400, "onboarding.prepare_one_person")
    old = item.get("mail_account") or {}
    if old.get("connected_at"):
        raise ApiError(400, "onboarding.already_connected")
    secrets = {k: v for k, v in (acct.get("secrets") or {}).items() if k in ("password", "smtp_password") and v}
    draft = ConnectorDraft(kind="imap", name=str(acct.get("name") or ""), mode=str(acct.get("mode") or "mirror"),
                           data_class=str(acct.get("data_class") or "normal"), config=dict(acct.get("config") or {}),
                           identities=acct.get("identities"))
    if draft.mode not in ("mirror", "store"):
        raise HTTPException(400, "mode must be mirror or store")  # i18n: internal
    cfg, mode = _normalize(draft)
    check_store_allowed(mode)
    if secrets:
        sealed = managed.seal_preset_secrets(item["id"], secrets)
    elif old.get("secrets_enc") and _hosts(cfg) == _hosts(old.get("config") or {}):
        sealed = old["secrets_enc"]
    else:
        raise ApiError(400, "accounts.enter_password")
    item["mail_account"] = {"name": draft.name, "mode": mode, "data_class": draft.data_class, "config": cfg,
                            "identities": draft.identities, "by": p.user_id, "secrets_enc": sealed}


@router.get("/admin/onboarding/presets")
async def list_presets(p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    items = await onboarding.presets(db)
    return {"items": [_preset_out(x) for x in items],
            "options": {k: list(v) if v else None for k, v in onboarding.PRESET_KEYS.items()}}


@router.post("/admin/onboarding/presets")
async def add_preset(body: PresetIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    try:
        item = onboarding.clean_preset(body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    items = await onboarding.presets(db)
    if len(items) >= onboarding.MAX_PRESETS:
        raise ApiError(400, "onboarding.too_many_presets")
    if any(x.get("match") == item["match"] and not x.get("used_by") for x in items):
        raise ApiError(409, "onboarding.preset_exists")
    item.update(id=onboarding.new_preset_id(), created_at=onboarding.now_iso())
    await _preset_account(db, p, item, body)
    items.append(item)
    await onboarding.save_presets(db, items)
    await audit(db, p, "admin.onboarding_preset", item["id"],
                {"match": item["match"], "mail_account": bool(item.get("mail_account"))}, always=True)
    await db.commit()
    return _preset_out(item)


@router.put("/admin/onboarding/presets/{pid}")
async def put_preset(pid: str, body: PresetIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    try:
        clean = onboarding.clean_preset(body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    items = await onboarding.presets(db)
    item = next((x for x in items if x["id"] == pid), None)
    if item is None:
        raise ApiError(404, "onboarding.preset_not_found")
    item.update(clean)
    if item.get("mail_account") and not item["match"].get("email"):
        item.pop("mail_account")  # a group preset cannot carry one
    await _preset_account(db, p, item, body)
    await onboarding.save_presets(db, items)
    await audit(db, p, "admin.onboarding_preset", pid,
                {"match": item["match"], "mail_account": bool(item.get("mail_account"))}, always=True)
    await db.commit()
    return _preset_out(item)


@router.delete("/admin/onboarding/presets/{pid}")
async def delete_preset(pid: str, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    items = await onboarding.presets(db)
    if not any(x["id"] == pid for x in items):
        raise ApiError(404, "onboarding.preset_not_found")
    await onboarding.save_presets(db, [x for x in items if x["id"] != pid])
    await audit(db, p, "admin.onboarding_preset_delete", pid, always=True)
    await db.commit()
    return {"ok": True}
