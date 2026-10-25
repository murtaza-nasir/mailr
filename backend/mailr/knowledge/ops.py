"""Write operations on the assistant's knowledge, shared by the REST routes, the agent tools and
the approval executors, so an action the user approves later runs exactly the same code.

Permission classes (core/permissions.py): `memory.write` (remember, forget, entities; the
built-in assistant is allowed by default, tokens are asked) and `skills.write` (create or change
skills; asked by default for every agent, because a skill changes future prompts).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import permissions
from mailr.knowledge import memory, skills
from mailr.models import Skill, User

SKILL_OPS = {"create_skill", "update_skill"}
MEMORY_OPS = {"remember", "forget", "upsert_entity", "delete_entity"}


# A memory note's fields as a live-edited item (core/live.py).
MEMORY_FIELDS = ("text", "kind", "pinned", "expires_at", "entity_id")


def memory_snapshot(m: Any) -> dict[str, Any]:
    return {"text": m.text, "kind": m.kind, "pinned": bool(m.pinned),
            "expires_at": m.expires_at.isoformat() if m.expires_at else None, "entity_id": m.entity_id}


def _same(a: Any, b: Any) -> bool:
    from mailr.core.merge3 import same

    return same(a, b)


def action_class(op: str) -> str:
    return "skills.write" if op in SKILL_OPS else "memory.write"


async def run(db: AsyncSession, p, op: str, args: dict[str, Any], *, source: str = "user",
              sensitive: bool = False, refs: list[dict[str, Any]] | None = None,
              status: str = "active") -> dict[str, Any]:
    """Carry out one operation now. Raises ValueError (bad input) or LookupError (not found)."""
    if op == "remember":
        from mailr.core import live

        mid = args.get("memory_id")
        args = dict(args)
        base_version = args.pop("base_version", None)
        if mid not in (None, "") and base_version is not None:
            # Made from an older version of the note: merged with what changed since (core/live.py).
            cur = await memory.find_memory(db, p, int(mid))
            if cur is not None:
                await live.lock(db, p.user_id, "memory", cur.id, cur)  # and read cur again under it
                now = memory_snapshot(cur)
                mine = {**now, **{k: args[k] for k in MEMORY_FIELDS if k in args}}
                merged, _ = await live.resolve(db, p.user_id, "memory", cur.id, now, mine, int(base_version))
                args.update({k: v for k, v in merged.items() if k in args or not _same(v, now.get(k))})
        m, created = await memory.remember(db, p, args, source=source, sensitive=sensitive, refs=refs,
                                           memory_id=int(mid) if mid not in (None, "") else None,
                                           status=status)
        version = await live.commit(db, p.user_id, "memory", m.id, memory_snapshot(m))
        return {"memory": {**(await memory.with_entities(db, [m], p))[0], "version": version}, "created": created}
    if op == "forget":
        from mailr.core import live

        m = await memory.forget(db, p, int(args.get("memory_id") or 0))
        await live.forget(db, p.user_id, "memory", m.id)
        return {"forgotten": m.id, "text": m.text}
    if op == "upsert_entity":
        eid = args.get("entity_id")
        e, created = await memory.upsert_entity(db, p, args, source=source, sensitive=sensitive,
                                                entity_id=int(eid) if eid not in (None, "") else None)
        return {"entity": memory.entity_out(e), "created": created}
    if op == "delete_entity":
        e = await memory.get_entity(db, p, int(args.get("entity_id") or 0))
        if e is None:
            raise LookupError("entity not found")
        if e.is_self:
            raise ValueError("this is you: delete the notes about you one by one")
        await db.delete(e)
        await db.flush()
        return {"deleted": e.id, "name": e.name}
    if op == "create_skill":
        d = skills.clean(args)
        if await skills.by_name(db, p.user_id, d["name"]) is not None:
            raise ValueError(f"a skill named /{d['name']} already exists; choose another name or update it")
        s = Skill(user_id=p.user_id, builtin=False, source=source, enabled=d.pop("enabled", True),
                  applies_to=d.pop("applies_to", []), auto=d.pop("auto", False), **d)
        db.add(s)
        await db.flush()
        return {"skill": skills.out(s)}
    if op == "update_skill":
        s = None
        if args.get("skill_id"):
            s = await db.get(Skill, int(args["skill_id"]))
            s = s if s is not None and s.user_id == p.user_id else None
        elif args.get("name"):
            s = await skills.by_name(db, p.user_id, str(args["name"]))
        if s is None:
            raise LookupError("skill not found")
        changes = {k: v for k, v in args.items() if k not in ("skill_id", "name")}
        if args.get("new_name"):
            changes["name"] = args["new_name"]
        d = skills.clean(changes, partial=True)
        if "name" in d and d["name"] != s.name and await skills.by_name(db, p.user_id, d["name"]) is not None:
            raise ValueError(f"a skill named /{d['name']} already exists")
        for k, v in d.items():
            setattr(s, k, v)
        s.updated_at = memory.now()  # a server-side onupdate would be expired (lazy load) after flush
        await db.flush()
        return {"skill": skills.out(s)}
    raise ValueError(f"unknown operation {op}")


def preview(op: str, args: dict[str, Any], target: Any = None, *, status: str = "active") -> dict[str, Any]:
    """What the user sees on the approval card. For a change to an existing note or entity it
    shows what is stored now, so the user knows what is being replaced or deleted."""
    if op == "create_skill" or op == "update_skill":
        name = args.get("new_name") or args.get("name") or args.get("skill_id")
        lines = [x for x in (args.get("title"), args.get("description")) if x]
        if args.get("instructions"):
            lines.append(str(args["instructions"]))  # in full: the card must show everything that will run
        for k in ("auto", "enabled", "applies_to"):
            if k in args and args[k] is not None:
                v = args[k]
                cur = getattr(target, k, None) if target is not None else None
                shown = ", ".join(v) if isinstance(v, list) else ("on" if v is True else "off" if v is False else v)
                was = (", ".join(cur) if isinstance(cur, list) else ("on" if cur is True else "off" if cur is False
                                                                     else cur)) if target is not None else None
                label = {"auto": "Applies automatically", "enabled": "Enabled", "applies_to": "Used for"}[k]
                lines.append(f"{label}: {shown}" + (f" (now: {was or '—'})" if target is not None else ""))
        return {"title": f"{'Create' if op == 'create_skill' else 'Change'} skill /{name}", "kind": "skill",
                "lines": lines or ["(no changes shown)"]}
    if op == "remember":
        about = args.get("about")
        about_s = about.get("name") if isinstance(about, dict) else about
        lines = [str(args.get("text") or "")] + ([f"about {about_s}"] if about_s else [])
        if args.get("pinned"):
            lines.append("pinned: always included in the assistant's context")
        if target is not None:
            lines = ([f"Now: {target.text}"] + [f"New: {x}" if i == 0 else x for i, x in enumerate(lines)]
                     if args.get("text") else [f"Note: {target.text}"] + lines[1:])
        if status == "suggested":
            lines.append("saved as a suggestion for you to review")
        return {"title": "Remember" if target is None else "Update a memory", "kind": "memory", "lines": lines}
    if op == "forget":
        text = target.text if target is not None else f"memory {args.get('memory_id')}"
        return {"title": "Forget a memory", "kind": "memory", "danger": True, "lines": [text]}
    if op == "delete_entity":
        name = target.name if target is not None else f"entity {args.get('entity_id')}"
        return {"title": "Delete a person or thing and its notes", "kind": "memory", "danger": True,
                "lines": [name]}
    lines = [x for x in (args.get("summary"), ", ".join(args.get("emails") or []),
                         ", ".join(args.get("domains") or [])) if x]
    if target is not None:
        addrs = ", ".join(list(target.emails or []) + list(target.domains or [])) or "no addresses"
        lines.insert(0, f"Changes {target.name} ({addrs})")
    return {"title": f"Save {args.get('kind') or 'entity'} “{args.get('name') or args.get('entity_id')}”",
            "kind": "memory", "lines": lines}


def agent_args(op: str, args: dict[str, Any]) -> dict[str, Any]:
    """Agents (tokens, the assistant) may raise `sensitive` but never clear it."""
    a = dict(args)
    if not a.get("sensitive"):
        a.pop("sensitive", None)
    return a


async def target_of(db: AsyncSession, p, op: str, args: dict[str, Any]) -> Any:
    """The existing row the operation changes, looked up with the caller's own filters (a token
    that cannot see a note cannot change or delete it). None when it creates something.
    Raises LookupError when the target is not visible to the caller."""
    if op in ("remember", "forget") and (op == "forget" or args.get("memory_id") not in (None, "")):
        m = await memory.find_memory(db, p, int(args.get("memory_id") or 0))
        if m is None:
            raise LookupError("memory not found")
        return m
    if op == "upsert_entity":
        eid = args.get("entity_id")
        return await memory.merge_target(db, p, args, int(eid) if eid not in (None, "") else None)
    if op == "delete_entity":
        e = await memory.get_entity(db, p, int(args.get("entity_id") or 0))
        if e is None:
            raise LookupError("entity not found")
        return e
    if op == "update_skill":
        if args.get("skill_id"):
            s = await db.get(Skill, int(args["skill_id"]))
            return s if s is not None and s.user_id == p.user_id else None
        if args.get("name"):
            return await skills.by_name(db, p.user_id, str(args["name"]))
    return None


def provenance(op: str, args: dict[str, Any], target: Any) -> tuple[str, str | None]:
    """For a turn whose tool results carried untrusted mail content: (status for new notes,
    reason to ask instead of allow). Mail can contain instructions aimed at the assistant, so
    what it writes then is either a suggestion the user reviews or waits for a click."""
    if op == "remember":
        if args.get("pinned"):
            return "active", "pinning a note after reading mail needs your approval"
        if target is not None and getattr(target, "source", "agent") != "agent":
            return "active", "changing a note you wrote, after reading mail, needs your approval"
        return ("suggested" if target is None else "active"), None
    if op == "forget" and target is not None and getattr(target, "source", "agent") != "agent":
        return "active", "forgetting a note you wrote, after reading mail, needs your approval"
    if op == "upsert_entity":
        if target is None:
            return "active", "saving a new person or thing after reading mail needs your approval"
        new_emails = {str(x).lower() for x in args.get("emails") or []} - {x.lower() for x in target.emails or []}
        new_domains = {str(x).lower().lstrip("@") for x in args.get("domains") or []} - set(target.domains or [])
        if new_emails or new_domains:
            return "active", "adding addresses to a person or thing after reading mail needs your approval"
    return "active", None


async def gated(db: AsyncSession, p, op: str, args: dict[str, Any], *, actor: permissions.Actor,
                source: str, sensitive: bool = False, refs: list[dict[str, Any]] | None = None,
                untrusted: bool = False, target: Any = ..., force_ask: str | None = None) -> tuple[str, Any]:
    """Ask the permission engine, then run. ("done", result) | ("pending", PendingAction);
    raises PermissionError on deny and LookupError when the target is not visible to the caller.
    The target is resolved with the caller's filters before any approval is created. `force_ask`:
    a reason that asks even when a rule allows."""
    action = action_class(op)
    args = agent_args(op, args)
    if target is ...:
        target = await target_of(db, p, op, args)
    status, reason = provenance(op, args, target) if untrusted else ("active", None)
    decision = await permissions.check(db, actor, action, target=op, ctx={},
                                       payload={"op": op, "args": args, "source": source, "sensitive": sensitive,
                                                "refs": refs or [], "status": status},
                                       preview=preview(op, args, target, status=status), force_ask=reason or force_ask)
    if isinstance(decision, permissions.Denied):
        raise PermissionError(decision.reason)
    if isinstance(decision, permissions.Pending):
        return "pending", decision.action
    return "done", await run(db, p, op, args, source=source, sensitive=sensitive, refs=refs, status=status)


async def _execute(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    # An approved request runs with the requester's restrictions (a token's hidden notes stay
    # out of reach), and agents never clear `sensitive`.
    try:
        p = await permissions.requester(db, user, payload)
        op = payload["op"]
        return await run(db, p, op, agent_args(op, payload.get("args") or {}),
                         source=payload.get("source") or "agent", sensitive=bool(payload.get("sensitive")),
                         refs=payload.get("refs") or [], status=payload.get("status") or "active")
    except (ValueError, LookupError) as e:
        return {"error": str(e)}


permissions.executor("memory.write")(_execute)
permissions.executor("skills.write")(_execute)
