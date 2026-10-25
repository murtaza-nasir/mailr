"""Which model does each task (Settings → Ambrose → Models).

Admins register endpoints (Settings → Admin → Models → Model endpoints). Each user picks, per task, the
endpoint and model their mail goes to; without a choice the first endpoint whose defaults name
the task is used (intel/llm.resolve). The choice lives in the user setting `models`:
{"chat": {"endpoint": "<name>", "model": "<model>"}, "classify": {...}}.

Local-only mail never follows a hosted choice: llm.resolve falls back to a local endpoint for
the same task, or refuses. `embed` is instance-wide (the search index is built with one model)
and is shown read-only.

Access policies (core/access.py): a user who is not an admin sees only the models the policy
allows; `locked` is true when the policy does not let them choose (shown read-only, "Set by your
administrator"), and PUT accepts only allowed models.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.auth import Principal, current
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.intel import llm
from mailr.models import ModelEndpoint, User

router = APIRouter(tags=["models"])

TASKS = {k: v for k, v in llm.TASKS.items() if k != "embed"}  # embed is server-wide


def _can(ep: ModelEndpoint, task: str) -> bool:
    return llm.can(ep, task)


def _opt(ep: ModelEndpoint, task: str) -> dict[str, Any]:
    return {"endpoint": ep.name, "models": list(ep.models or []), "default_model": (ep.defaults or {}).get(task),
            "locality": ep.locality, "adapter": ep.adapter}


async def _effective(db: AsyncSession, user: User, task: str, sensitive: bool) -> dict[str, Any] | None:
    try:
        r = await llm.resolve(db, user, task, sensitive=sensitive, probe=True)
    except (llm.NoModel, llm.LocalOnlyViolation):
        return None
    return {"endpoint": r.endpoint.name, "model": r.model, "locality": r.endpoint.locality}


@router.get("/me/models")
async def my_models(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "models.choices_in_app")
    from mailr.core import access

    eps = list((await db.execute(llm.enabled_endpoints())).scalars())
    user = await db.get(User, p.user_id)
    acc = await access.for_user(db, user)
    locked = not acc.feature("choose_models")
    chosen = (user.settings or {}).get("models") or {}
    from mailr.knowledge import profile

    name = await profile.name_of(db, user.id)  # "Ambrose and writing" with the user's own name
    tasks = []
    for task, meta in TASKS.items():
        opts = []
        for e in eps:
            if not _can(e, task) or not (acc.unrestricted or e.id in acc.allowed.get(task, {})):
                continue
            o = _opt(e, task)
            allowed = None if acc.unrestricted else acc.allowed[task][e.id]
            if allowed is not None:
                o["models"] = [m for m in o["models"] if m in allowed] or sorted(allowed)
            opts.append(o)
        tasks.append({"task": task, **meta, "label": llm.task_words(task, name=name)["label"], "choice": None if locked else chosen.get(task),
                      "options": opts, "locked": locked,
                      "effective": await _effective(db, user, task, False),
                      "effective_local_only": await _effective(db, user, task, True)})
    embed = await llm.resolve_embed(db, sensitive=False, user_id=user.id)
    return {"tasks": tasks, "locked": locked, "restricted": not acc.unrestricted,
            "message": None if acc.feature("assistant") else access.no_assistant_message(name),
            "embed": {"endpoint": embed.endpoint.name, "model": embed.model, "locality": embed.endpoint.locality}
            if embed else None}


class ChoiceIn(BaseModel):
    endpoint: str | None = Field(None, max_length=120)  # None: back to the default
    model: str | None = Field(None, max_length=200)


@router.put("/me/models/{task}")
async def set_model(task: str, body: ChoiceIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "models.choices_in_app")
    if task not in TASKS:
        raise HTTPException(404, "unknown task")  # i18n: internal
    from mailr.core import access

    user = await db.get(User, p.user_id)
    acc = await access.for_user(db, user)
    if not acc.feature("choose_models"):
        await access.audit_refusal(user.id, f"model_choice:{task}", "choosing models is off in the access policy")
        raise HTTPException(403, access.NO_FEATURE_MESSAGES["choose_models"])
    from mailr.core import usersettings

    if body.endpoint is None:
        await usersettings.patch_in(db, p.user_id, "models", remove=[task])
    else:
        ep = (await db.execute(select(ModelEndpoint).where(ModelEndpoint.name == body.endpoint,
                                                           ModelEndpoint.enabled.is_(True)))).scalar_one_or_none()
        if ep is None:
            raise ApiError(400, "models.no_such_endpoint")
        if not _can(ep, task):
            raise ApiError(400, "models.endpoint_cannot_do_task", endpoint=ep.name)
        model = body.model or (ep.defaults or {}).get(task) or (ep.models[0] if ep.models else "")
        if ep.models and model not in ep.models:
            raise ApiError(400, "models.model_not_listed")
        if not acc.allows(task, ep, model):
            await access.audit_refusal(user.id, f"model_choice:{task}", "model not allowed",
                                       detail={"endpoint": ep.name, "model": model})
            raise ApiError(403, "models.model_not_allowed")
        # Only this task's choice: another task changed at the same time keeps its own.
        await usersettings.patch_in(db, p.user_id, "models", {task: {"endpoint": ep.name, "model": model}})
    await db.commit()
    return await my_models(p, db)


# ---------------------------------------------------------------- local models only, model use


async def _locality_out(db: AsyncSession, user: User) -> dict[str, Any]:
    from mailr.intel import locality

    st = await locality.state(db, user)
    async def can(task: str, sensitive: bool) -> bool:
        if task == "embed":
            return (await llm._resolve_embed(db, sensitive=sensitive)) is not None
        try:  # the router without the rule: is there a model at all, and a local one?
            await llm._resolve(db, user, task, sensitive=sensitive, probe=True)
            return True
        except (llm.NoModel, llm.LocalOnlyViolation):
            return False

    # What stops working while the rule holds: work that has a model, but no local one.
    from mailr.knowledge import profile

    name = await profile.name_of(db, user.id)
    unavailable = [{"task": t, "label": llm.task_words(t, name=name)["label"]} for t in llm.TASKS
                   if await can(t, False) and not await can(t, True)]
    return {**st, "no_local": unavailable}


# ACT settings.local_models_me @api
@router.get("/me/local-models")
async def my_local_models(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """"Local models only for me": the household rule, this person's choice, the result, and
    whether the switch is locked (and why)."""
    return await _locality_out(db, await db.get(User, p.user_id))


class LocalIn(BaseModel):
    on: bool


@router.put("/me/local-models")
async def set_my_local_models(body: LocalIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "models.switch_in_app")
    from mailr.core.audit import audit
    from mailr.intel import locality

    if await locality.household(db) == "local_all":  # the only lock: forced on
        raise HTTPException(409, locality.REASONS["local_all"])
    user = await db.get(User, p.user_id, with_for_update=True, populate_existing=True)
    user.settings = {**(user.settings or {}), locality.USER_KEY: bool(body.on)}
    await audit(db, p, "settings.local_models_only", "", {"on": bool(body.on)}, always=True)
    await db.commit()
    return await _locality_out(db, user)


# ACT settings.model_use @api
@router.get("/me/model-use")
async def my_model_use(days: int = 30, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Settings → Ambrose → Model use: per feature, which model did the work, local or hosted, and
    how often in the last `days` days (at most 90)."""
    from mailr.intel import usage

    days = max(1, min(int(days), usage.KEEP_DAYS))
    return {"days": days, "rows": await usage.summary(db, p.user_id, days)}
