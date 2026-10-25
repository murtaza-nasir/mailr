"""The permission gate for agents on notes (the notes permission classes).

API tokens (notes/routes.py) and the assistant (agent/note_tools.py) ask the permission engine
before every write, through `decide`. The signed-in user is never gated. A request that waits
for the user's approval stores its operations (notes/ops.py) as the payload; the executor
registered here for each notes class runs exactly those operations once the user approves.

What makes a write ask, beyond the user's rules and the class defaults:
- free text an agent writes (a new note, a body, an item, a section name, a task made from an
  item) asks when the assistant's turn read other people's text, and always for an API token,
  unless a rule allows it (`ask_by_default`, as tasks.create);
- hiding something (archiving a note, deleting items) asks in a tainted turn, as hiding mail does;
- an errand or a standing order writing to a shared or group note always asks;
- deleting forever asks always; deleting a label asks unless a rule allows it.

Constraints are matched against what the request really touches: `notes` (the public id of the
note written) and `labels` (the caller's own labels on it).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import live, permissions
from mailr.notes import access, core, localonly, ops
from mailr.notes.models import Note

CLASSES = ("notes.create", "notes.edit", "notes.delete", "notes.labels")

TAINTED = "hard rule: an agent wrote this text after reading other people's text (or through an API token), so it asks first"
HIDES = "hard rule: the assistant read other people's text in this turn, and this hides something from you"
ERRAND_SHARED = "hard rule: an errand writes to a note other people share"


def class_of(name: str, args: dict[str, Any], *, new_list: bool = False) -> str:
    """The permission class of an operation. A capture line that makes a new list is a create."""
    if name == "note.capture" and new_list:
        return "notes.create"
    return ops.OPS[name].cls


async def ctx_values(db: AsyncSession, user: Any, note: Note | None) -> dict[str, Any]:
    """What constraints are matched against: the note's public id and the caller's labels on it."""
    if note is None:
        return {}
    st = await core.state_of(db, user, note)
    return {"notes": [note.pid], "labels": list(st.labels or []) if st else []}


def shared(user: Any, note: Note | None) -> bool:
    """A note other people can see: group-owned, someone else's, or the user's own shared with
    other people (notes/acl.py)."""
    from mailr.notes import acl

    return note is not None and (note.owner_user_id is None or note.owner_user_id != user.id or acl.is_shared(note))


async def reasons(db: AsyncSession, actor: permissions.Actor, name: str, note: Note | None, args: dict[str, Any], *,
                  tainted: bool) -> tuple[str | None, str | None]:
    """(ask_by_default, force_ask) for one operation of an agent."""
    spec = ops.OPS[name]
    soft = spec.soft
    if soft is None and tainted and spec.text(args):
        soft = TAINTED
    if soft is None and tainted and actor.kind == "assistant" and spec.hides(args):
        soft = HIDES
    force = spec.force
    if force is None and note is not None and "local_only" in args and localonly.lowers(note, args["local_only"]):
        force = localonly.LOWER_ASKS
    if force is None and shared(actor.user, note) and await permissions.is_errand(db, actor):
        force = ERRAND_SHARED
    return soft, force


def payload_of(items: list[dict[str, Any]], by: str) -> dict[str, Any]:
    """The stored request: the operations to run after approval, and who asked (for `updated_by`)."""
    return {"ops": items, "_by": by}


async def decide(db: AsyncSession, actor: permissions.Actor, cls: str, items: list[dict[str, Any]], *,
                 note: Note | None, target: str, preview: dict[str, Any], soft: str | None = None,
                 force: str | None = None) -> permissions.Decision:
    """Ask the engine once for `items` (operations of one class on one note, or none)."""
    cv = await ctx_values(db, actor.user, note)
    if actor.kind == "token":
        by = f"agent:{(actor.token.name or str(actor.token.id))[:60]}"
    else:
        by = "assistant" if actor.kind == "assistant" else "user"
    return await permissions.check(db, actor, cls, target=target[:200], ctx=cv, payload=payload_of(items, by),
                                   preview=preview, force_ask=force, ask_by_default=soft)


# ---------------------------------------------------------------- previews (approval cards)


def note_title(note: Note | None) -> str:
    if note is None:
        return ""
    return note.title or ("Untitled list" if note.kind == "checklist" else "Untitled note")


def _short(v: Any, n: int = 80) -> str:
    s = " ".join(str(v or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def describe(name: str, args: dict[str, Any], note: Note | None) -> tuple[str, list[str], bool]:
    """(title, lines, danger) of one operation for its approval card. Every line is what will run:
    the whole text of an item, a body or a section name, never a summary of it."""
    t = _short(note_title(note), 60)
    a = args
    if name == "note.create":
        kind = "checklist" if a.get("kind") == "checklist" else "note"
        lines = [f"Title: {a['title']}"] if a.get("title") else []
        if a.get("body"):
            lines.append(str(a["body"]))
        if a.get("lines"):
            lines += [f"Item: {x}" for x in str(a["lines"]).splitlines() if x.strip()]
        for it in a.get("items") or []:
            lines.append(f"Item: {it.get('text')}")
        if a.get("labels"):
            lines.append("Labels: " + ", ".join(map(str, a["labels"])))
        return (f"Create a {kind}" + (f" “{_short(a['title'], 60)}”" if a.get("title") else ""), lines, False)
    if name == "note.edit":
        lines = []
        if "title" in a:
            lines.append(f"Title: {a['title']}")
        if "body" in a:
            lines += ["New text:", str(a["body"])]
        for k in ("color", "shopping"):
            if k in a:
                lines.append(f"{k.capitalize()}: {a[k]}")
        if "local_only" in a and a["local_only"] is not None:
            lines.append(localonly.card_line(a["local_only"]))
        return (f"Change the note “{t}”", lines, bool(note is not None and localonly.lowers(note, a.get("local_only"))))
    if name == "note.trash":
        return (f"Move “{t}” to Trash", [f"Note: {t}", "It stays in Trash for 30 days."], True)
    if name == "note.restore":
        return (f"Restore “{t}” from Trash", [f"Note: {t}"], False)
    if name == "note.convert":
        return (f"Make “{t}” a {'list' if a.get('to') == 'checklist' else 'text note'}", [f"Note: {t}"], False)
    if name == "note.state":
        parts = []
        if a.get("archived") is not None:
            parts.append("Archive" if a["archived"] else "Unarchive")
        if a.get("pinned") is not None:
            parts.append("Pin" if a["pinned"] else "Unpin")
        if a.get("labels") is not None or a.get("add_labels") or a.get("remove_labels"):
            parts.append("Change labels")
        lines = [f"Note: {t}"]
        if a.get("add_labels"):
            lines.append("Add labels: " + ", ".join(a["add_labels"]))
        if a.get("remove_labels"):
            lines.append("Remove labels: " + ", ".join(a["remove_labels"]))
        if a.get("labels") is not None:
            lines.append("Labels: " + (", ".join(a["labels"]) or "none"))
        return (f"{' and '.join(parts) or 'Change'} “{t}”", lines, False)
    if name == "items.add":
        lines = [f"List: {t}"]
        if a.get("new_sections"):
            lines.append("New section: " + ", ".join(a["new_sections"]))
        if a.get("lines"):
            lines += [f"Item: {x}" for x in str(a["lines"]).splitlines() if x.strip()]
        for it in a.get("items") or []:
            lines.append(f"Item: {it.get('text')}")
        n = len([x for x in lines if x.startswith("Item: ")])
        return (f"Add {n} item{'s' if n != 1 else ''} to “{t}”" if n else f"Add to “{t}”", lines, False)
    if name == "item.patch":
        lines = [f"List: {t}"]
        if a.get("_text_before"):
            lines.append(f"Item: {a['_text_before']}")
        if "text" in a:
            lines.append(f"New text: {a['text']}")
        if "extra" in a:
            lines.append(f"Extra line: {a['extra']}")
        if "checked" in a:
            lines.append("Check it" if a["checked"] else "Uncheck it")
        return (f"Change an item of “{t}”", lines, False)
    if name == "item.delete":
        return (f"Delete an item of “{t}”", [f"List: {t}"] + ([f"Item: {a['_text_before']}"]
                                                            if a.get("_text_before") else []), True)
    if name == "item.to_task":
        return ("Make a task from an item", [f"List: {t}"] + ([f"Task: {a['_text_before']}"]
                                                             if a.get("_text_before") else []), False)
    if name == "items.clear_checked":
        return (f"Clear the checked items of “{t}”", [f"List: {t}", "Version history keeps them for 30 days."], True)
    if name == "items.uncheck_all":
        return (f"Uncheck every item of “{t}”", [f"List: {t}"], False)
    if name == "section.create":
        return (f"Add a section to “{t}”", [f"List: {t}", f"Section: {a.get('name')}"], False)
    if name == "section.patch":
        lines = [f"List: {t}"] + ([f"New name: {a['name']}"] if a.get("name") else [])
        return (f"Change a section of “{t}”", lines, False)
    if name == "section.delete":
        return (f"Delete a section of “{t}”", [f"List: {t}", "Its items are deleted too" if a.get("items") == "delete"
                                                 else "Its items move to the top of the list"], a.get("items") == "delete")
    if name == "revision.restore":
        return (f"Restore an earlier version of “{t}”", [f"Note: {t}", f"Version: {a.get('rev')}"], False)
    if name in ("link.add", "link.remove"):
        return (f"{'Link' if name == 'link.add' else 'Unlink'} “{t}”", [f"Note: {t}"], False)
    if name == "label.create":
        return (f"Create the note label “{_short(a.get('name'), 40)}”", [f"Label: {a.get('name')}"], False)
    if name == "label.patch":
        lines = [f"Label: {a.get('label')}"] + ([f"New name: {a['name']}"] if a.get("name") else []) + \
            ([f"Colour: {a['color']}"] if "color" in a else [])
        return (f"Change the note label “{_short(a.get('label'), 40)}”", lines, False)
    if name == "label.delete":
        return (f"Delete the note label “{_short(a.get('label'), 40)}”",
                [f"Label: {a.get('label')}", "The notes stay; only the label goes."], True)
    if name == "note.remind":
        times = ", ".join(str(x) for x in a.get("at") or []) or "none"
        return (f"Set a reminder on “{t}”" if a.get("at") else f"Remove the reminders of “{t}”",
                [f"Note: {t}", f"Reminders: {times}"], False)
    if name == "note.save_template":
        return (f"Save “{t}” as a template", [f"Note: {t}"] + ([f"Template: {a['name']}"] if a.get("name") else []),
                False)
    if name == "note.from_template":
        return ("Make a note from a template", [f"Template: {a.get('template')}"]
                + ([f"Title: {a['title']}"] if a.get("title") else []), False)
    if name == "note.from_recording":
        return ("Make a note from a recording", ["A text note with the recording's title, date, summary and "
                                                  "action items, linked to it"], False)
    if name == "template.create":
        return (f"Save the template “{_short(a.get('name'), 40)}”", [f"Template: {a.get('name')}"], False)
    if name == "template.delete":
        return ("Delete a template", [f"Template: {a.get('template')}"], True)
    if name in ("note.purge", "note.empty_trash"):
        return ("Delete forever" + (f" “{t}”" if note is not None else ": empty Trash"), [f"Note: {t}"] if note else [],
                True)
    return (name.replace(".", " ").capitalize() + (f" on “{t}”" if t else ""), [f"Note: {t}"] if t else [], False)


def preview_of(items: list[dict[str, Any]], note: Note | None) -> dict[str, Any]:
    """The card of one request: the first operation names it; every operation adds its lines."""
    titles, lines, danger = [], [], False
    for it in items:
        tt, ll, dd = describe(it["op"], it.get("args") or {}, note)
        titles.append(tt)
        lines += [x for x in ll if x not in lines or x.startswith(("Item: ", "New text"))]
        danger = danger or dd
    title = titles[0] if len(titles) == 1 else f"{titles[0]} (and {len(titles) - 1} more)"
    pv: dict[str, Any] = {"title": title, "kind": "note", "lines": lines, "danger": danger}
    if note is not None:
        pv["note"] = {"id": note.pid, "title": note.title, "kind": note.kind}
    return pv


# ---------------------------------------------------------------- the routes (API tokens)


def _conflict(c: live.LiveConflict) -> JSONResponse:
    return JSONResponse(c.body(), status_code=409)


async def route(db: AsyncSession, p: Any, name: str, note_id: str | None, args: dict[str, Any]) -> Any:
    """One write from notes/routes.py: run now for the signed-in user; for an API token ask the
    permission engine first (403 on deny, 202 with the pending approval on ask). Commits."""
    spec = ops.OPS[name]
    token = None
    try:
        if p.is_token:
            actor = permissions.Actor.from_principal(p)
            note = await ops.note_for(db, p, note_id, spec) if spec.need is not None else None
            new_list = False
            if name == "note.capture":
                res = ops.parse_capture(args, await ops.capture_lists(db, p))
                new_list = bool(res.list and res.list.get("create"))
                if not new_list:
                    note = await access.get(db, p.user, res.list["id"], need="edit", trashed=False, p=p)
            soft, force = await reasons(db, actor, name, note, args, tainted=True)
            item = {"op": name, "note": note.pid if note is not None else None, "args": args}
            decision = await decide(db, actor, class_of(name, args, new_list=new_list), [item], note=note,
                                    target=note_title(note) or name, preview=preview_of([item], note),
                                    soft=soft, force=force)
            permissions.raise_for(decision)
            if isinstance(decision, permissions.Pending):
                from mailr.core.audit import audit

                await audit(db, p, decision.action.action, decision.action.target, args,
                            {"pending": decision.action.id})
                await db.commit()
                return JSONResponse(permissions.pending_response(decision.action), status_code=202)
            token = live.by_var.set(live.who(p))
        res = await ops.run(db, p, name, note_id, args)
    except live.LiveConflict as c:
        await db.rollback()
        return _conflict(c)
    except HTTPException:
        await db.rollback()
        raise
    finally:
        if token is not None:
            live.by_var.reset(token)
    await db.commit()
    return res


# ---------------------------------------------------------------- the executor (after approval)


async def run_payload(db: AsyncSession, user: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Run the stored operations of an approved request, as the requester (a token keeps its
    limits: a local-only note it may not see is a 404 even now)."""
    p = await permissions.requester(db, user, payload)
    by = str(payload.get("_by") or "assistant")
    token = live.by_var.set(by)
    otoken = live.origin_var.set(None)
    out = []
    try:
        for it in payload.get("ops") or []:
            try:
                res = await ops.run(db, p, str(it.get("op")), it.get("note"), dict(it.get("args") or {}))
            except live.LiveConflict as c:
                raise HTTPException(409, c.body().get("detail") or "it changed meanwhile") from None
            out.append({"op": it.get("op"), "ok": True, **summary(res)})
    finally:
        live.by_var.reset(token)
        live.origin_var.reset(otoken)
    return {"ok": True, "results": out}


def summary(res: Any) -> dict[str, Any]:
    """What an executed request reports: counts and ids, never another person's text."""
    if not isinstance(res, dict):
        return {}
    out: dict[str, Any] = {}
    if isinstance(res.get("note"), dict) and res["note"].get("id"):
        out["note"] = res["note"]["id"]
    for k in ("items", "merged", "rechecked", "duplicates"):
        if isinstance(res.get(k), list):
            out[k] = len(res[k])
    if isinstance(res.get("count"), int):
        out["count"] = res["count"]
    if res.get("task_uid"):
        out["task_uid"] = res["task_uid"]
    return out


for _cls in CLASSES:
    permissions.executor(_cls)(run_payload)
