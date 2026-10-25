"""Admin → Access: access policies per identity-provider group, the groups seen, and each user's
groups and effective access (core/access.py, site/docs/admin/access.md)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr import i18n
from mailr.api.admin_routes import _audit_admin_change
from mailr.core import access
from mailr.core.audit import audit
from mailr.core.auth import Principal, admin
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.intel import llm
from mailr.knowledge import profile
from mailr.models import AccessPolicy, IdpGroupSeen, ModelEndpoint, User

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(_audit_admin_change)])


async def _eps(db: AsyncSession) -> list[ModelEndpoint]:
    return list((await db.execute(select(ModelEndpoint).order_by(ModelEndpoint.id))).scalars())


def _entry_out(v: Any, by_id: dict[int, ModelEndpoint]) -> dict[str, Any] | None:
    ent = access.clean_entry(v)
    if ent is None:
        return None
    e = by_id.get(ent[0])
    return {"endpoint_id": ent[0], "endpoint": e.name if e else None, "model": ent[1],
            "locality": e.locality if e else None, "missing": e is None}


def policy_out(p: AccessPolicy, eps: list[ModelEndpoint], members: int = 0) -> dict[str, Any]:
    by_id = {e.id: e for e in eps}
    models = {}
    for t in access.POLICY_TASKS:
        spec = (p.models or {}).get(t) or {}
        models[t] = {"any": bool(spec.get("any")),
                     "allowed": [x for x in (_entry_out(v, by_id) for v in spec.get("allowed") or []) if x],
                     "default": _entry_out(spec.get("default"), by_id)}
    builtin = p.group_name == access.EVERYONE
    return {"id": p.id, "group": p.group_name, "builtin": builtin,
            "label": access.EVERYONE_LABEL if builtin else p.group_name, "description": p.description, "models": models,
            "features": {k: bool((p.features or {}).get(k, False)) for k in access.FEATURES},
            "needs_endpoints": dict(p.needs or {}), "members": members,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None}


async def _calendar_policy_changed(db: AsyncSession) -> None:
    """"Members get a mailr calendar" changed: make the calendars that are now wanted (the others
    are hidden at once, calendar/builtin.shown_for)."""
    from mailr.calendar import builtin

    if await builtin.instance_enabled(db):
        from mailr.core.jobs import enqueue

        await enqueue(db, "calendar.builtin_backfill", {}, dedupe_key="calendar.builtin_backfill")


async def _members(db: AsyncSession) -> dict[str, int]:
    """Users per group name (the effective groups: the provider's, else the assigned ones). Under
    EVERYONE: the users who are not admins and are in no group with a policy of its own."""
    counts: dict[str, int] = {}
    with_policy = set((await db.execute(select(AccessPolicy.group_name))).scalars())
    for u in (await db.execute(select(User).where(User.disabled.is_(False)))).scalars():
        gs = access.effective_groups(u)
        for g in gs:
            counts[g] = counts.get(g, 0) + 1
        if not access.is_admin(u) and not with_policy.intersection(gs):
            counts[access.EVERYONE] = counts.get(access.EVERYONE, 0) + 1
    return counts


@router.get("/access")
async def access_overview(p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """Policies, the identity-provider groups seen at sign-ins, and what can go into a policy."""
    eps = await _eps(db)
    members = await _members(db)
    name = await profile.name_of(db, p.user_id)  # the admin's own name for the assistant
    pols = list((await db.execute(select(AccessPolicy).order_by(AccessPolicy.group_name))).scalars())
    seen = list((await db.execute(select(IdpGroupSeen).order_by(IdpGroupSeen.name))).scalars())
    names = {pol.group_name for pol in pols}
    groups = [{"name": g.name, "source": g.source, "first_seen_at": g.first_seen_at.isoformat(),
               "last_seen_at": g.last_seen_at.isoformat(), "users": members.get(g.name, 0),
               "has_policy": g.name in names} for g in seen]
    assigned_only = sorted(set(members) - {g.name for g in seen})
    groups += [{"name": n, "source": "assigned", "first_seen_at": None, "last_seen_at": None,
                "users": members[n], "has_policy": n in names} for n in assigned_only]
    return {
        "policies": [policy_out(pol, eps, members.get(pol.group_name, 0)) for pol in pols],
        "groups_seen": groups,
        "features": [{"key": k, **v} for k, v in access.features_named(name).items()],
        "tasks": [{"task": t, **llm.TASKS[t], "label": llm.task_words(t, name=name)["label"],
                   "options": [{"endpoint_id": e.id, "endpoint": e.name, "models": list(e.models or []),
                                "default_model": (e.defaults or {}).get(t), "locality": e.locality,
                                "adapter": e.adapter, "enabled": e.enabled}
                               for e in eps if llm.can(e, t)]}
                  for t in access.POLICY_TASKS],
        "admin_group_note": "Admins are never restricted.",
    }


class EntryIn(BaseModel):
    endpoint: str = Field(max_length=100)
    model: str | None = Field(None, max_length=200)


class TaskIn(BaseModel):
    any: bool = False
    allowed: list[EntryIn] = Field(default_factory=list, max_length=100)
    default: EntryIn | None = None


class PolicyIn(BaseModel):
    group: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=500)
    models: dict[str, TaskIn] = Field(default_factory=dict)
    features: dict[str, bool] = Field(default_factory=dict)


def _clean(body: PolicyIn, eps: list[ModelEndpoint], old_needs: dict[str, Any] | None,
           name: str | None = None) -> tuple[dict, dict, dict]:
    by_name = {e.name: e for e in eps}
    unknown = set(body.models) - set(access.POLICY_TASKS)
    if unknown:
        raise HTTPException(400, f"unknown task: {sorted(unknown)[0]} (embeddings stay instance-wide)")  # i18n: internal
    bad = set(body.features) - set(access.FEATURES)
    if bad:
        raise HTTPException(400, f"unknown switch: {sorted(bad)[0]}")  # i18n: internal

    def entry(task: str, e: EntryIn) -> dict[str, Any]:
        ep = by_name.get(e.endpoint)
        if ep is None:
            raise HTTPException(400, f"no endpoint named {e.endpoint!r}")  # i18n: internal
        if not llm.can(ep, task):
            raise ApiError(400, "access.endpoint_cannot_do",
                endpoint=ep.name,
                task=llm.task_words(task, name=name)["label"].lower())
        if e.model and ep.models and e.model not in ep.models:
            raise HTTPException(400, f"{ep.name} does not list the model {e.model!r}")  # i18n: internal
        return {"endpoint_id": ep.id, "model": e.model or None}

    models: dict[str, Any] = {}
    needs = dict(old_needs or {})
    for t in access.POLICY_TASKS:
        ti = body.models.get(t) or TaskIn()
        allowed = [entry(t, e) for e in ti.allowed]
        default = entry(t, ti.default) if ti.default else None
        if default is not None and not ti.any and not any(
                a["endpoint_id"] == default["endpoint_id"] and (a["model"] is None or a["model"] == default["model"])
                for a in allowed):
            raise ApiError(400, "access.default_not_allowed", task=llm.task_words(t, name=name)["label"].lower())
        models[t] = {"any": ti.any, "allowed": allowed, "default": default}
        if ti.any or allowed:
            needs.pop(t, None)  # an admin picked the endpoints the seed could not find
    features = {k: bool(body.features.get(k, v["default"])) for k, v in access.FEATURES.items()}
    return models, features, needs


@router.post("/access/policies")
async def add_policy(body: PolicyIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    eps = await _eps(db)
    group = body.group.strip()
    if group == access.EVERYONE:
        raise ApiError(400, "access.everyone_built_in")
    if (await db.execute(select(AccessPolicy.id).where(func.lower(AccessPolicy.group_name) == group.lower()))).first():
        raise ApiError(409, "access.group_has_policy")
    models, features, needs = _clean(body, eps, None, await profile.name_of(db, p.user_id))
    pol = AccessPolicy(group_name=group, description=body.description.strip(), models=models, features=features,
                       needs=needs)
    db.add(pol)
    await db.flush()
    await audit(db, p, "admin.access_policy.add", group, {"features": features}, always=True)
    await _notify_members(db, p, group, "policy", i18n.msg("activity.admin.policy_added", group=group))
    await _calendar_policy_changed(db)
    await db.commit()
    await db.refresh(pol)
    return policy_out(pol, eps)


@router.put("/access/policies/{pid}")
async def update_policy(pid: int, body: PolicyIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    pol = await db.get(AccessPolicy, pid)
    if pol is None:
        raise ApiError(404, "access.policy_not_found")
    eps = await _eps(db)
    group = body.group.strip()
    if (pol.group_name == access.EVERYONE) != (group == access.EVERYONE):
        raise ApiError(400, "access.everyone_keeps_name")
    clash = (await db.execute(select(AccessPolicy.id).where(func.lower(AccessPolicy.group_name) == group.lower(),
                                                          AccessPolicy.id != pid))).first()
    if clash:
        raise ApiError(409, "access.group_has_policy")
    models, features, needs = _clean(body, eps, pol.needs, await profile.name_of(db, p.user_id))
    before = {"group": pol.group_name, "features": pol.features}
    pol.group_name, pol.description = group, body.description.strip()
    pol.models, pol.features, pol.needs = models, features, needs
    await audit(db, p, "admin.access_policy.update", group, {"before": before, "features": features}, always=True)
    await _notify_members(db, p, group, "policy",
                          i18n.msg("activity.admin.policy_changed", group=group))
    if bool((before["features"] or {}).get("calendar")) != bool(features.get("calendar")):
        await _calendar_policy_changed(db)
    await db.commit()
    await db.refresh(pol)
    return policy_out(pol, eps, (await _members(db)).get(pol.group_name, 0))


@router.delete("/access/policies/{pid}")
async def delete_policy(pid: int, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    pol = await db.get(AccessPolicy, pid)
    if pol is None:
        raise ApiError(404, "access.policy_not_found")
    if pol.group_name == access.EVERYONE:
        raise ApiError(400, "access.everyone_cannot_delete")
    await audit(db, p, "admin.access_policy.delete", pol.group_name, always=True)
    await _notify_members(db, p, pol.group_name, "policy",
                          i18n.msg("activity.admin.policy_removed", group=pol.group_name))
    if (pol.features or {}).get("calendar"):
        await _calendar_policy_changed(db)
    await db.delete(pol)
    await db.commit()
    return {"ok": True}


async def _notify_members(db: AsyncSession, p: Principal, group: str, kind: str, description: dict[str, Any]) -> None:
    """A policy change, in the own activity of each person it covers."""
    from mailr.core.audit import notice

    g = group.lower()
    for u in (await db.execute(select(User).where(User.disabled.is_(False)))).scalars():
        if g in {x.lower() for x in access.effective_groups(u)}:
            await notice(db, p, u.id, kind, description, {"group": group})


async def user_access_out(db: AsyncSession, u: User) -> dict[str, Any]:
    eps = await _eps(db)
    acc = await access.for_user(db, u, eps)
    return {"user_id": u.id, "role": u.role, "groups": access.effective_groups(u),
            "source": access.group_source(u), "idp_groups": u.idp_groups, "idp_source": u.idp_groups_source,
            "idp_groups_at": u.idp_groups_at.isoformat() if u.idp_groups_at else None,
            "assigned_groups": list(u.assigned_groups or []),
            "can_assign": access.group_source(u) != "idp",
            "access": acc.as_dict(eps, await profile.name_of(db, u.id))}


@router.get("/users/{uid}/access")
async def user_access(uid: int, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """A user's groups (from the provider, or assigned) and their effective access."""
    u = await db.get(User, uid)
    if u is None:
        raise ApiError(404, "admin.user_not_found")
    return await user_access_out(db, u)


class GroupsIn(BaseModel):
    groups: list[str] = Field(default_factory=list, max_length=50)


@router.put("/users/{uid}/groups")
async def assign_groups(uid: int, body: GroupsIn, p: Principal = Depends(admin), db: AsyncSession = Depends(get_db)):
    """Assign policy groups to an account the identity provider does not manage (password-only).
    A provider-managed account takes its groups from the provider at each sign-in (409)."""
    u = await db.get(User, uid)
    if u is None:
        raise ApiError(404, "admin.user_not_found")
    if access.group_source(u) == "idp":
        raise ApiError(409, "access.groups_from_idp")
    before = access.effective_groups(u)
    u.assigned_groups = access.normalise_groups(body.groups)
    from mailr.calendar import groups as calendar_groups

    await calendar_groups.groups_changed(db, before, access.effective_groups(u))
    await audit(db, p, "admin.user_groups", str(uid), {"groups": u.assigned_groups}, always=True)
    after = access.effective_groups(u)
    if sorted(before) != sorted(after):
        from mailr.core.audit import notice

        await notice(db, p, uid, "groups", i18n.msg("activity.admin.groups", groups=", ".join(after))
                     if after else i18n.msg("activity.admin.groups_none"),
                     {"groups": after})
    await db.commit()
    return await user_access_out(db, u)
