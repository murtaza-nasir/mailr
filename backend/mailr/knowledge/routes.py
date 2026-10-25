"""REST API for the assistant's knowledge: profile, skills, memories, entities.

Reads need the `read` scope (sensitive rows are hidden from tokens that are not local_only).
Writes by a signed-in user run at once; writes by an API token need the `memory` scope and go
through the permission engine (`memory.write`, `skills.write`), which may answer 202 with a
pending approval. The profile is changed only by the signed-in user.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import permissions
from mailr.core.audit import audit
from mailr.core.auth import Principal, scope
from mailr.core.events import notify
from mailr.core.live import LiveConflict
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.knowledge import memory, ops, profile, skills
from mailr.models import Memory, Skill

router = APIRouter(prefix="/assistant", tags=["assistant"])


def _session_only(p: Principal, what: str) -> None:
    if p.is_token:
        raise HTTPException(403, f"{what} can only be changed by the signed-in user")  # i18n: internal


async def _changed(db: AsyncSession, p: Principal, entity: str) -> None:
    await notify(db, p.user_id, {"type": "changes", "entity": entity})


async def _write(db: AsyncSession, p: Principal, op: str, args: dict[str, Any], *, entity: str,
                 status_code: int = 200):
    """Run a knowledge write: now for the user, through the permission engine for tokens."""
    try:
        if p.is_token:
            p.require("memory")
            state, res = await ops.gated(db, p, op, args, actor=permissions.Actor.from_principal(p), source="agent")
            if state == "pending":
                await audit(db, p, f"assistant.{op}", op, args, {"pending": res.id})
                await db.commit()
                return JSONResponse(permissions.pending_response(res), status_code=202)
        else:
            res = await ops.run(db, p, op, args, source="user")
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except LiveConflict as e:
        await db.rollback()
        return JSONResponse(e.body(), status_code=409)
    await audit(db, p, f"assistant.{op}", str(args.get("memory_id") or args.get("entity_id") or args.get("name") or ""),
                json.loads(json.dumps(args, default=str)))
    await _changed(db, p, entity)
    await db.commit()
    return JSONResponse(json.loads(json.dumps(res, default=str)), status_code=status_code)


# ---------------------------------------------------------------- profile


class ProfileIn(BaseModel):
    about: str | None = Field(None, max_length=4000)
    preferred_name: str | None = Field(None, max_length=200)
    signoff_name: str | None = Field(None, max_length=200)
    role: str | None = Field(None, max_length=400)
    timezone: str | None = Field(None, max_length=64)
    languages: str | None = Field(None, max_length=400)
    writing: str | None = Field(None, max_length=2000)
    options: dict[str, bool | str] | None = None


@router.get("/profile")
async def get_profile(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    row = await profile.get(db, p.user_id)
    await db.commit()
    return profile.out(row, p.user)


@router.put("/profile")
async def put_profile(body: ProfileIn, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    _session_only(p, "the profile")
    row = await profile.get(db, p.user_id)
    try:
        data = profile.clean(body.model_dump(exclude_none=True, exclude={"options"}))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if "timezone" in data:  # an alias of the account setting (Settings → You → Region)
        await profile.set_zone(db, p.user_id, data.pop("timezone"))
    row.data = {k: v for k, v in {**(row.data or {}), **data}.items() if k != "timezone"}
    if body.options:
        try:
            opts = profile.clean_options(body.options)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        row.options = {**(row.options or {}), **opts}
    await audit(db, p, "assistant.profile", "", {"fields": sorted(data), "options": body.options or {}})
    await _changed(db, p, "assistant_profile")
    await db.commit()
    await db.refresh(row)  # updated_at is set by the database on update
    from mailr.models import User

    return profile.out(row, await db.get(User, p.user_id) or p.user)


# ---------------------------------------------------------------- skills


class SkillIn(BaseModel):
    name: str | None = Field(None, max_length=60)
    title: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=1000)
    instructions: str | None = Field(None, max_length=skills.MAX_INSTRUCTIONS)
    applies_to: list[str] | None = None
    auto: bool | None = None
    enabled: bool | None = None


@router.get("/skills")
async def list_skills(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    rows = await skills.all_for(db, p.user_id)
    await db.commit()
    return {"items": [skills.out(s) for s in rows], "contexts": list(skills.CONTEXTS),
            "builtins": list(skills.BUILTINS)}


async def _skill(db: AsyncSession, p: Principal, sid: int) -> Skill:
    s = await db.get(Skill, sid)
    if s is None or s.user_id != p.user_id:
        raise ApiError(404, "knowledge.skill_not_found")
    return s


@router.get("/skills/resolve")
async def resolve_skills(text: str = "", p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """What /name tokens in a prompt resolve to (the UI checks before sending)."""
    inv = await skills.resolve(db, p.user_id, text)
    await db.commit()
    return {"text": inv.text, "skills": [{"name": s.name, "title": s.title} for s in inv.skills],
            "problems": inv.problems}


@router.get("/skills/{sid}")
async def get_skill(sid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    return skills.out(await _skill(db, p, sid))


@router.post("/skills")
async def create_skill(body: SkillIn, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "create_skill", body.model_dump(exclude_none=True), entity="skill", status_code=201)


@router.patch("/skills/{sid}")
async def update_skill(sid: int, body: SkillIn, p: Principal = Depends(scope("read")),
                       db: AsyncSession = Depends(get_db)):
    await _skill(db, p, sid)
    args = body.model_dump(exclude_none=True)
    if "name" in args:
        args["new_name"] = args.pop("name")
    return await _write(db, p, "update_skill", {"skill_id": sid, **args}, entity="skill")


@router.delete("/skills/{sid}")
async def delete_skill(sid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    _session_only(p, "skills")
    s = await _skill(db, p, sid)
    await db.delete(s)
    await audit(db, p, "assistant.delete_skill", s.name)
    await _changed(db, p, "skill")
    await db.commit()
    return {"ok": True}


@router.post("/skills/{sid}/reset")
async def reset_skill(sid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """Restore a built-in skill's shipped text."""
    _session_only(p, "skills")
    s = await _skill(db, p, sid)
    if s.name not in skills.BUILTINS:
        raise ApiError(400, "knowledge.only_builtin_reset")
    for k, v in skills.reset_fields(s.name).items():
        setattr(s, k, v)
    s.builtin, s.source = True, "builtin"
    await audit(db, p, "assistant.reset_skill", s.name)
    await _changed(db, p, "skill")
    await db.commit()
    return skills.out(s)


@router.post("/skills/{sid}/duplicate")
async def duplicate_skill(sid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    _session_only(p, "skills")
    s = await _skill(db, p, sid)
    have = set((await db.execute(select(Skill.name).where(Skill.user_id == p.user_id))).scalars())
    base = (s.name[:40] + "-copy")
    name, n = base, 2
    while name in have:
        name, n = f"{base}-{n}", n + 1
    c = Skill(user_id=p.user_id, name=name, title=f"{s.title} (copy)"[:200], description=s.description,
              instructions=s.instructions, applies_to=list(s.applies_to or []), auto=False, enabled=True,
              builtin=False, source="user")
    db.add(c)
    await db.flush()
    await audit(db, p, "assistant.duplicate_skill", s.name, {"new": name})
    await _changed(db, p, "skill")
    await db.commit()
    return skills.out(c)


@router.post("/skills/restore-builtins")
async def restore_builtins(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    _session_only(p, "skills")
    added = await skills.restore_builtins(db, p.user_id)
    await _changed(db, p, "skill")
    await db.commit()
    return {"added": added}


class SkillTestIn(BaseModel):
    sample: str = Field(min_length=1, max_length=8000)
    skill_id: int | None = None
    # Unsaved edits from the editor (instructions, title) take precedence over the stored skill.
    instructions: str | None = Field(None, max_length=skills.MAX_INSTRUCTIONS)
    title: str | None = None


@router.post("/skills/test")
async def test_skill(body: SkillTestIn, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """Rewrite a sample text with a skill (the "Try it" box in Settings). Neither the sample nor
    the skill has a record of where it came from: the sample may be pasted from local-only mail,
    and a skill the assistant wrote may come from a chat that read local-only content. So the call
    is sensitive (local endpoints only) for a user who has local-only mail or accounts, for a skill
    the assistant wrote, and for a local-only token."""
    from mailr.agent.prompts import skill_test_messages
    from mailr.intel import llm

    s = await _skill(db, p, body.skill_id) if body.skill_id else None
    instructions = body.instructions if body.instructions is not None else (s.instructions if s else "")
    if not instructions.strip():
        raise ApiError(400, "knowledge.skill_no_instructions")
    prof = await profile.get(db, p.user_id)
    msgs = skill_test_messages(p.user, prof.data or {}, body.title or (s.title if s else "Skill"), instructions,
                               body.sample)
    from mailr.core.visibility import token_query_sensitive, user_has_local_only

    sensitive = bool(s is not None and s.source == "agent") or token_query_sensitive(p) or \
        await user_has_local_only(db, p.user_id)
    try:
        out = await llm.chat(db, p.user, msgs, task="chat", feature="memory", sensitive=sensitive, temperature=0.3,
                             max_tokens=1200,
                             extra={"chat_template_kwargs": {"enable_thinking": False}})
    except llm.NoModel:
        raise ApiError(409, "models.none_configured") from None
    except Exception as e:  # noqa: BLE001
        raise ApiError(502, "models.call_failed", error=str(e)[:277]) from None
    await db.commit()
    return {"text": (out.get("content") or "").strip(), "model": out.get("_model")}


# ---------------------------------------------------------------- memories


class MemoryIn(BaseModel):
    text: str | None = Field(None, max_length=1000)
    kind: str | None = None
    entity_id: int | None = None
    about: str | dict[str, Any] | None = None
    pinned: bool | None = None
    sensitive: bool | None = None
    expires_at: str | None = None
    # The version the editor started from (core/live.py): changes saved since then are merged in.
    base_version: int | None = None


@router.get("/memories")
async def list_memories(status: str = "active", q: str = "", entity_id: int | None = None, kind: str | None = None,
                        ids: str = "", limit: Annotated[int, Query(ge=1, le=1000)] = 200,
                        p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """Memories, newest first. `ids` (comma-separated): just those notes, in that order, the ones
    the caller may see (the "Used memories" line of an answer or a draft)."""
    if status not in ("active", "suggested", "all"):
        raise HTTPException(400, "status must be active, suggested or all")  # i18n: internal
    if ids:
        want = [int(x) for x in ids.split(",") if x.strip().isdigit()][:100]
        found = {m.id: m for m in (await db.execute(select(Memory).where(
            Memory.id.in_(want), memory.mem_filter(p, include_suggested=True, include_expired=True)))).scalars()}
        return {"items": await memory.with_entities(db, [found[i] for i in want if i in found], p),
                "missing": len([i for i in want if i not in found])}
    if q and status == "active":
        from mailr.core.visibility import token_query_sensitive

        rows = await memory.recall(db, p, q, k=min(limit, 50), entity_ids=[entity_id] if entity_id else None,
                                   kind=kind, query_sensitive=token_query_sensitive(p))
    else:
        stmt = select(Memory).where(memory.mem_filter(p, include_suggested=True, include_expired=True))
        if status != "all":
            stmt = stmt.where(Memory.status == status)
        if entity_id:
            stmt = stmt.where(Memory.entity_id == entity_id)
        if kind:
            stmt = stmt.where(Memory.kind == kind)
        if q:
            stmt = stmt.where(func.lower(Memory.text).contains(q.lower()))
        rows = list((await db.execute(stmt.order_by(Memory.pinned.desc(), Memory.updated_at.desc())
                                      .limit(min(limit, 500)))).scalars())
    counts = dict((await db.execute(select(Memory.status, func.count()).where(
        memory.mem_filter(p, include_suggested=True, include_expired=True)).group_by(Memory.status))).all())
    return {"items": await memory.with_entities(db, rows, p), "counts": {"active": counts.get("active", 0),
                                                                       "suggested": counts.get("suggested", 0)}}


@router.post("/memories")
async def create_memory(body: MemoryIn, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "remember", body.model_dump(exclude_none=True), entity="memory", status_code=201)


@router.patch("/memories/{mid}")
async def update_memory(mid: int, body: MemoryIn, p: Principal = Depends(scope("read")),
                        db: AsyncSession = Depends(get_db)):
    args = body.model_dump(exclude_unset=True)
    return await _write(db, p, "remember", {**args, "memory_id": mid}, entity="memory")


@router.delete("/memories/{mid}")
async def delete_memory(mid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "forget", {"memory_id": mid}, entity="memory")


@router.post("/memories/{mid}/accept")
async def accept_memory(mid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """Accept a suggested memory: it becomes active, and the person or thing it is about (kept
    in its refs until now) becomes an entity."""
    _session_only(p, "suggested memories")
    m = (await db.execute(select(Memory).where(Memory.id == mid, memory.mem_filter(
        p, include_suggested=True, include_expired=True)))).scalar_one_or_none()
    if m is None:
        raise ApiError(404, "common.memory_not_found")
    about = next((r.get("about") for r in m.refs or [] if isinstance(r, dict) and r.get("about")), None)
    if about and m.entity_id is None and memory.is_self_ref(about):
        m.entity_id = (await memory.self_entity(db, p, create=True)).id
    elif about and m.entity_id is None:
        ent = await memory.find_entity(db, p, about.get("name"), kind=about.get("kind"))
        if ent is None and about.get("emails"):
            ent = await memory.find_entity(db, p, about["emails"][0])
        if ent is None:
            ent, _ = await memory.upsert_entity(db, p, about, source="extracted", sensitive=m.sensitive)
        elif about.get("emails"):
            await memory.upsert_entity(db, p, {"emails": about["emails"]}, source="extracted", entity_id=ent.id)
        m.entity_id = ent.id
    m.status = "active"
    if m.source not in ("extracted", "chat"):
        m.source = "extracted"
    await db.flush()
    await audit(db, p, "assistant.accept_memory", str(mid))
    await _changed(db, p, "memory")
    await db.commit()
    return (await memory.with_entities(db, [m], p))[0]


@router.post("/memories/{mid}/reject")
async def reject_memory(mid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """Dismiss a suggestion; the learner will not propose the same text again."""
    _session_only(p, "suggested memories")
    m = (await db.execute(select(Memory).where(Memory.id == mid, Memory.status == "suggested",
                                               memory.mem_filter(p, include_suggested=True)))).scalar_one_or_none()
    if m is None:
        raise ApiError(404, "knowledge.suggestion_not_found")
    prof = await profile.get(db, p.user_id)
    rejected = list((prof.state or {}).get("rejected") or [])
    rejected = (rejected + [memory._norm(m.text)])[-300:]
    prof.state = {**(prof.state or {}), "rejected": rejected}
    await db.delete(m)
    await audit(db, p, "assistant.reject_memory", str(mid))
    await _changed(db, p, "memory")
    await db.commit()
    return {"ok": True}


@router.post("/memories/learn")
async def learn_now(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """Queue a learning pass over recent sent mail now (results arrive as suggestions)."""
    _session_only(p, "memory learning")
    from mailr.core.jobs import enqueue

    await enqueue(db, "kb.extract", {"user_id": p.user_id, "force": True}, user_id=p.user_id,
                  dedupe_key=f"kb.extract:{p.user_id}", max_attempts=2)
    await db.commit()
    return {"queued": True}


@router.get("/memories/learning")
async def learning(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """How learning from mail went: the last pass (when, messages read, new suggestions, a plain
    error code) and the recent ones."""
    from mailr.knowledge import tasks

    return tasks.learning_status(await profile.get(db, p.user_id))


@router.get("/recall")
async def recall(q: str = "", conversation_id: int | None = None, emails: str = "",
                 p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """What the assistant would be told for this request (the injected memory block)."""
    from mailr.api.org_routes import include_local
    from mailr.core.visibility import token_query_sensitive

    text, info = await memory.context_block(db, p, query=q, emails=[e for e in emails.split(",") if e.strip()],
                                            conversation_id=conversation_id, people_local=include_local(p),
                                            query_sensitive=token_query_sensitive(p))
    await db.commit()
    return {"block": text, **info}


# ---------------------------------------------------------------- entities


class EntityIn(BaseModel):
    kind: str | None = None
    name: str | None = Field(None, max_length=200)
    aliases: list[str] | None = None
    emails: list[str] | None = None
    domains: list[str] | None = None
    conversation_ids: list[int] | None = None
    summary: str | None = Field(None, max_length=600)


@router.get("/entities")
async def list_entities(q: str = "", kind: str | None = None, p: Principal = Depends(scope("read")),
                        db: AsyncSession = Depends(get_db)):
    return {"items": await memory.list_entities(db, p, q=q, kind=kind), "kinds": list(memory.ENTITY_KINDS)}


@router.get("/entities/{eid}")
async def get_entity(eid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    e = await memory.get_entity(db, p, eid)
    if e is None:
        raise ApiError(404, "knowledge.entity_not_found")
    notes = list((await db.execute(select(Memory).where(memory.mem_filter(p, include_expired=True),
                                                        Memory.entity_id == e.id)
                                   .order_by(Memory.pinned.desc(), Memory.updated_at.desc()))).scalars())
    contacts = []
    if e.emails:
        from mailr.api.org_routes import person_out

        # The linked contact first: the person card opens it.
        rows = sorted(await memory.visible_contacts(db, p, list(e.emails)),
                      key=lambda x: (x.id != e.person_id, -(x.sent_count * 5 + x.received_count)))
        contacts = [person_out(x) for x in rows]
    return {**memory.entity_out(e, notes=len(notes)), "memories": await memory.with_entities(db, notes, p),
            "contacts": contacts}


@router.post("/entities")
async def create_entity(body: EntityIn, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "upsert_entity", body.model_dump(exclude_none=True), entity="entity", status_code=201)


@router.patch("/entities/{eid}")
async def update_entity(eid: int, body: EntityIn, p: Principal = Depends(scope("read")),
                        db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "upsert_entity", {**body.model_dump(exclude_none=True), "entity_id": eid},
                        entity="entity")


@router.delete("/entities/{eid}")
async def delete_entity(eid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "delete_entity", {"entity_id": eid}, entity="entity")

