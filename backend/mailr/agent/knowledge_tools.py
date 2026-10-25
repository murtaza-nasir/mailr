"""Agent tools for skills and memory.

Reads need `read`; writes need the `memory` scope and pass the permission engine:
`memory.write` (remember, forget, upsert_entity: allowed for the built-in assistant, asked for
tokens) and `skills.write` (create_skill, update_skill: asked by default).
"""

from __future__ import annotations

from typing import Any

from mailr.agent.tools import ToolCtx, ToolError, ToolResult, _audit, _obj, _u, pending_result, tool
from mailr.core import live
from mailr.core.live import LiveConflict
from mailr.knowledge import memory, ops, skills
from mailr.models import Entity

_MEM_KINDS = list(memory.MEMORY_KINDS)
_ENT_KINDS = list(memory.ENTITY_KINDS)


def _touched_sensitive(target, res: dict[str, Any]) -> bool:
    """True when the row the write touched (before or after) holds knowledge from local-only mail."""
    if target is not None and getattr(target, "sensitive", False):
        return True
    for k in ("memory", "entity"):
        if isinstance(res.get(k), dict) and res[k].get("sensitive"):
            return True
    return False


async def _write(ctx: ToolCtx, op: str, args: dict[str, Any], refs: list[dict[str, Any]] | None = None) -> ToolResult:
    sensitive = bool(ctx.session_sensitive)
    if refs and not sensitive:
        sensitive = await memory.refs_sensitive(ctx.db, ctx.p.user_id, refs)
    if ctx.chat_session_id:
        refs = [*(refs or []), {"chat_session_id": ctx.chat_session_id}]
    try:
        target = await ops.target_of(ctx.db, ctx.p, op, ops.agent_args(op, args))
    except (LookupError, ValueError) as e:
        raise ToolError(str(e)) from None
    if target is not None and getattr(target, "sensitive", False) and not ctx.session_sensitive \
            and op in ("remember", "forget", "upsert_entity"):
        # Its text would reach a model that may be hosted; the user can change it in Settings.
        from mailr.knowledge import profile

        name = await profile.name_of(ctx.db, ctx.p.user_id)
        raise ToolError("that note holds knowledge from a local-only account and cannot be changed from this "
                        f"chat; the user can edit it in Settings → {name} → Memory")
    try:
        from mailr.agent.agentwrite import local_copy_reason

        # A skill has no local-only mark: written in a chat that read local-only content, it asks.
        state, res = await ops.gated(ctx.db, ctx.p, op, args, actor=ctx.actor, source="agent", sensitive=sensitive,
                                     refs=refs, untrusted=ctx.untrusted, target=target,
                                     force_ask=local_copy_reason(ctx) if op in ops.SKILL_OPS else None)
    except PermissionError as e:
        raise ToolError(str(e)) from None
    except LookupError as e:
        raise ToolError(str(e)) from None
    except ValueError as e:
        raise ToolError(str(e)) from None
    except LiveConflict as e:
        # Refused before anything was written; the session stays usable (no rollback).
        raise ToolError(e.body()["detail"] + " Nothing changed. Read the note again and redo the change, or ask "
                        "the user.") from None
    if state == "pending":
        await _audit(ctx, f"agent.{op}", op, args, {"pending": res.id})
        await ctx.db.commit()
        return pending_result(res)
    await _audit(ctx, f"agent.{op}", op, args, {k: v for k, v in res.items() if k != "memory"})
    from mailr.core.events import notify

    await notify(ctx.db, ctx.p.user_id, {"type": "changes", "entity": "skill" if op in ops.SKILL_OPS else "memory"})
    touched = _touched_sensitive(target, res)
    eid = (res.get("memory") or {}).get("entity_id") if isinstance(res.get("memory"), dict) else None
    if not touched and eid:
        ent = await ctx.db.get(Entity, int(eid))
        touched = bool(ent is not None and ent.sensitive)
    await ctx.db.commit()
    refs = []
    mem = res.get("memory") if isinstance(res, dict) else None
    if isinstance(mem, dict) and mem.get("version") is not None:
        refs = [{**live.ref("memory", mem["id"], mem["version"]), "title": str(mem.get("text") or "")[:80]}]
    return ToolResult(res, sensitive=sensitive or touched, refs=refs)


# ---------------------------------------------------------------- skills


@tool("list_skills", "List the user's skills (name, title, when to use). Invoke one with use_skill.", _obj({}))
async def list_skills(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    rows = await skills.all_for(ctx.db, ctx.p.user_id)
    return ToolResult({"items": [{"name": s.name, "title": s.title, "description": s.description,
                                  "applies_to": s.applies_to, "enabled": s.enabled, "auto": s.auto,
                                  "builtin": s.builtin} for s in rows]})


@tool("get_skill", "Read one skill in full (its instructions).", _obj({"name": {"type": "string"}}, ["name"]))
async def get_skill(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    s = await skills.by_name(ctx.db, ctx.p.user_id, str(args.get("name") or ""))
    if s is None:
        raise ToolError(f"no skill named {args.get('name')!r}; call list_skills")
    return ToolResult(skills.out(s))


@tool("use_skill",
      "Load a skill's instructions and follow them for the rest of this chat. Use it when a skill in the list "
      "fits the request (e.g. a writing style).",
      _obj({"name": {"type": "string", "description": "skill name without /"}}, ["name"]))
async def use_skill(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    s = await skills.by_name(ctx.db, ctx.p.user_id, str(args.get("name") or ""))
    if s is None or not s.enabled:
        raise ToolError(f"no enabled skill named {args.get('name')!r}; call list_skills")
    if ctx.chat_context is not None:
        active = list(ctx.chat_context.get("skills") or [])
        if s.name not in active:
            ctx.chat_context["skills"] = (active + [s.name])[-4:]
    return ToolResult({"name": s.name, "title": s.title, "instructions": s.instructions,
                       "note": "Follow these instructions for this request."})


_SKILL_PROPS = {
    "name": {"type": "string", "description": "lowercase slug used as /name, e.g. polite-decline"},
    "title": {"type": "string"},
    "description": {"type": "string", "description": "one sentence: when to use this skill"},
    "instructions": {"type": "string", "description": "the prompt: short imperative bullet points"},
    "applies_to": {"type": "array", "items": {"type": "string", "enum": list(skills.CONTEXTS)}},
}


@tool("create_skill",
      "Save a new skill (a reusable instruction set the user invokes as /name). The user approves it in mailr.",
      _obj(_SKILL_PROPS, ["name", "description", "instructions"]), scope="memory")
async def create_skill(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    return await _write(ctx, "create_skill", {k: v for k, v in args.items() if k in _SKILL_PROPS})


@tool("update_skill",
      "Change a skill: its instructions, description, title, applies_to, enabled, or rename it with new_name.",
      _obj({**_SKILL_PROPS, "new_name": {"type": "string"}, "enabled": {"type": "boolean"}}, ["name"]),
      scope="memory")
async def update_skill(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    return await _write(ctx, "update_skill", dict(args))


# ---------------------------------------------------------------- memory


@tool("recall",
      "Search the user's memory: notes about people, organisations, projects, preferences, commitments, and the "
      "people and things they know (names, aliases, email addresses). Use before searching mail when a request "
      "names someone or something, and when it touches a person or topic the prompt lists as having notes. "
      "Each note says why it matched.",
      _obj({"q": {"type": "string", "description": "names or words to look for"},
            "about": {"type": "string", "description": "a name or email address to list everything known about"},
            "kind": {"type": "string", "enum": _MEM_KINDS},
            "k": {"type": "integer", "minimum": 1, "maximum": 20}}))
async def recall(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    q = str(args.get("q") or "").strip()
    about = str(args.get("about") or "").strip()
    if not q and not about:
        raise ToolError("give q or about")
    ents: list = []
    if about:
        e = await memory.find_entity(ctx.db, ctx.p, about)
        if e is not None:
            ents.append(e)
    ents += [e for e in await memory.entities_mentioned(ctx.db, ctx.p, f"{q} {about}") if e not in ents]
    from mailr.agent.tools import query_sensitive

    # The same relevance gate as the memory block (knowledge/relevance.py): notes about the person or
    # topic asked about, notes matching a content word, notes close in meaning; the others only
    # after the decision model's check.
    text = f"{q} {about}".strip()
    hits = await memory.recall_hits(ctx.db, ctx.p, text, k=int(args.get("k") or 8),
                                    entity_ids=[e.id for e in ents], kind=args.get("kind"),
                                    query_sensitive=query_sensitive(ctx), gated=True)
    if hits:
        from mailr.knowledge import relevance

        names = {e.id: ("you" if e.is_self else e.name) for e in await memory.entities_of(ctx.db, hits)}
        check = await relevance.start_check(ctx.db, ctx.p.user, text, hits, names)
        if check is not None:
            answers, _timing = await check.results()
            hits = relevance.apply_check(hits, answers)
    mems = [h.memory for h in hits]
    why = {h.memory.id: h.why() for h in hits}
    await memory.mark_used(ctx.db, [m.id for m in mems])
    names = memory.candidate_names(f"{q} {about}") or ([about] if about and "@" not in about else [])
    known = " ".join(e.name.lower() for e in ents)
    from mailr.agent.people_tools import may_see_local

    people = await memory.people_named(ctx.db, ctx.p, [n for n in names if n.lower() not in known],
                                       local=await may_see_local(ctx)) if names else {}
    if people:
        ctx.untrusted = True  # display names are what the senders wrote in their From headers
    await _audit(ctx, "agent.recall", (q or about)[:200], {"q": q, "about": about}, {"count": len(mems)})
    return ToolResult({
        "entities": [{**memory.entity_out(e), "notes": None} for e in ents[:6]],
        "memories": [{"id": m.id, "text": m.text, "kind": m.kind, "entity_id": m.entity_id, "source": m.source,
                      "pinned": m.pinned, "updated": m.updated_at.date().isoformat() if m.updated_at else None,
                      "why": why.get(m.id) or ""}
                     for m in mems],
        "contacts": {n: [{"email": r.email, "name": _u(r.name), "emails_exchanged": r.emails,
                          "last_seen": r.last_seen.date().isoformat() if r.last_seen else None} for r in rows]
                     for n, rows in people.items()},
        "hint": None if (ents or mems or people) else "Nothing known yet; search mail, then remember what matters.",
    }, sensitive=any(m.sensitive for m in mems) or any(e.sensitive for e in ents)
       or any(r.local_only for rows in people.values() for r in rows))


@tool("remember",
      "Save a durable note: a fact (who someone is, a role, where something stands), preference, routine or "
      "commitment, optionally about a person/organisation/project (`about`, created if new; `about`: \"me\" for "
      "the user). Write it to the user: \"You teach on Tuesdays\", \"Sam is your accountant\". To correct a "
      "note pass its memory_id with the new text. The user can see and edit every note.",
      _obj({"text": {"type": "string", "description": "one short sentence written to the user (second person)"},
            "kind": {"type": "string", "enum": _MEM_KINDS},
            "about": {"type": "string", "description": "who or what it is about: a name or email address"},
            "about_kind": {"type": "string", "enum": _ENT_KINDS},
            "memory_id": {"type": "integer", "description": "update this existing note instead of adding one"},
            "base_version": {"type": "integer", "description": "with memory_id: the version of the note you read"},
            "expires_at": {"type": "string", "description": "ISO date after which the note no longer applies"},
            "pinned": {"type": "boolean", "description": "always include in context (use sparingly)"},
            "source_conversation_id": {"type": "integer"},
            "source_message_id": {"type": "integer"}}, ["text"]), scope="memory")
async def remember(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    a: dict[str, Any] = {k: args[k] for k in ("text", "kind", "memory_id", "expires_at", "pinned") if k in args}
    if a.get("memory_id") is not None:
        from mailr.agent.tools import _base_version

        if (bv := _base_version(ctx, args, "memory", a["memory_id"])) is not None:
            a["base_version"] = bv
    if args.get("about") and memory.is_self_ref(str(args["about"])):
        a["about"] = {"self": True}
    elif args.get("about"):
        about = str(args["about"]).strip()
        a["about"] = ({"emails": [about.lower()], "name": about.split("@")[0], "kind": args.get("about_kind") or "person"}
                      if "@" in about else {"name": about, "kind": args.get("about_kind") or "person"})
    refs = []
    if args.get("source_message_id") or args.get("source_conversation_id"):
        refs.append({k: int(args[f"source_{k}"]) for k in ("message_id", "conversation_id") if args.get(f"source_{k}")})
    return await _write(ctx, "remember", a, refs)


@tool("forget", "Delete one note from memory (when the user says it is wrong or no longer true).",
      _obj({"memory_id": {"type": "integer"}}, ["memory_id"]), scope="memory")
async def forget(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    return await _write(ctx, "forget", {"memory_id": args.get("memory_id")})


@tool("get_entity",
      "Everything known about one person, organisation, project, topic or place: summary, aliases, email "
      "addresses, notes, and contact statistics.",
      _obj({"ref": {"type": "string", "description": "entity id, name, alias or email address"}}, ["ref"]))
async def get_entity(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from sqlalchemy import select

    from mailr.models import Memory

    e = await memory.find_entity(ctx.db, ctx.p, args.get("ref"))
    if e is None:
        raise ToolError("nothing saved under that name; try recall or search mail")
    notes = list((await ctx.db.execute(select(Memory).where(memory.mem_filter(ctx.p), Memory.entity_id == e.id)
                                       .order_by(Memory.pinned.desc(), Memory.updated_at.desc()).limit(30))).scalars())
    contacts = []
    if e.emails:
        contacts = [{"email": x.email, "name": x.name, "organization": x.organization,
                     "emails_exchanged": x.sent_count + x.received_count,
                     "last_seen": x.last_seen.date().isoformat() if x.last_seen else None}
                    for x in await memory.visible_contacts(ctx.db, ctx.p, list(e.emails))]
    await memory.mark_used(ctx.db, [m.id for m in notes])
    return ToolResult({**memory.entity_out(e), "memories": [{"id": m.id, "text": m.text, "kind": m.kind}
                                                           for m in notes], "contacts": contacts},
                      sensitive=e.sensitive or any(m.sensitive for m in notes))


@tool("upsert_entity",
      "Create or update a person, organisation, project, topic or place: name, aliases, email addresses, "
      "domains, a one-line summary (who or what it is), and linked conversation ids. Lists are merged.",
      _obj({"name": {"type": "string"}, "kind": {"type": "string", "enum": _ENT_KINDS},
            "entity_id": {"type": "integer", "description": "update this entity"},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "emails": {"type": "array", "items": {"type": "string"}},
            "domains": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
            "conversation_ids": {"type": "array", "items": {"type": "integer"}}}, ["name"]), scope="memory")
async def upsert_entity(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    a = dict(args)
    if a.get("entity_id"):
        # Tools merge; they never replace the user's lists.
        e = await memory.get_entity(ctx.db, ctx.p, int(a["entity_id"]))
        if e is None:
            raise ToolError("entity not found")
        a.pop("entity_id")
        a["name"] = e.name
        a["kind"] = e.kind
    return await _write(ctx, "upsert_entity", a)
