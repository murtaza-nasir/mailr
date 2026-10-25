"""Templates of notes, checklists and tables (`note.save_template`,
`note.from_template`).

A template is a text note (title and text), a checklist (its sections with their order mode, and
its items with their quantities and extra lines, all unchecked) or a table (its columns, with no
rows). The user saves their own from a note or a table (`note_templates`); a few built-in starter
templates come with mailr and hold no personal data. "New from template" makes an ordinary note
or table: nothing links it back to the template, so changing one never changes the other.

A table template keeps no link columns: a link column points at one table's rows, and a new
table from the template would point at the old table.

A template saved from a local-only note, list or table stays local-only (`content.local_only`):
an API token that may not see local-only notes does not see it, the chat leaves it out on a
hosted model, and what is made from it is local-only too.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import User
from mailr.notes import actions, core
from mailr.notes.models import NoteTemplate

KINDS = ("text", "checklist", "table")
MAX_TEMPLATES = 200
NAME_MAX = 200


def _items(section: str | None, *texts: str) -> list[dict[str, Any]]:
    return [{"text": t, "section": section} for t in texts]


# The built-in starter templates. Generic content only: no names, places or
# anything personal. Their ids start with "b-" so they never clash with a user's own.
BUILTIN: dict[str, dict[str, Any]] = {
    "b-shopping": {
        "kind": "checklist", "name": "Shopping list",
        "content": {"title": "Shopping", "shopping": True,
                    "sections": [{"name": n, "order_mode": "learned"} for n in
                                 ("Produce", "Bakery", "Dairy", "Meat and fish", "Pantry", "Frozen", "Household")],
                    "items": _items("Produce", "bananas", "apples") + _items("Dairy", "milk", "eggs")
                    + _items("Bakery", "bread") + _items("Pantry", "coffee") + _items("Household", "dish soap")},
    },
    "b-packing": {
        "kind": "checklist", "name": "Packing list",
        "content": {"title": "Packing", "shopping": False,
                    "sections": [{"name": n, "order_mode": "manual"} for n in
                                 ("Documents", "Clothes", "Toiletries", "Electronics")],
                    "items": _items("Documents", "passport or ID", "tickets", "wallet")
                    + _items("Clothes", "shirts", "trousers", "socks", "jacket")
                    + _items("Toiletries", "toothbrush", "toothpaste", "medicine")
                    + _items("Electronics", "phone charger", "headphones")},
    },
    "b-meeting": {
        "kind": "text", "name": "Meeting notes",
        "content": {"title": "Meeting notes", "body": "Agenda:\n- \n\nNotes:\n- \n\nActions:\n- "},
    },
    "b-expenses": {
        "kind": "table", "name": "Expenses",
        "content": {"name": "Expenses", "description": "",
                    "columns": [{"name": "Date", "type": "date"}, {"name": "Item", "type": "text"},
                                {"name": "Category", "type": "select",
                                 "options": ["Food", "Travel", "Home", "Bills", "Other"]},
                                {"name": "Amount", "type": "currency"}, {"name": "Paid", "type": "checkbox"}]},
    },
}


def _summary(kind: str, content: dict[str, Any]) -> str:
    """One line for the gallery: "4 sections, 12 items", "3 lines", "5 columns"."""
    if kind == "checklist":
        s, i = len(content.get("sections") or []), len(content.get("items") or [])
        return ", ".join(x for x in (f"{s} section{'s' if s != 1 else ''}" if s else "",
                                     f"{i} item{'s' if i != 1 else ''}") if x)
    if kind == "table":
        cols = content.get("columns") or []
        return ", ".join(c.get("name", "") for c in cols[:6]) + (" …" if len(cols) > 6 else "")
    lines = [x for x in str(content.get("body") or "").splitlines() if x.strip()]
    return f"{len(lines)} line{'s' if len(lines) != 1 else ''}"


def is_local(content: dict[str, Any] | None) -> bool:
    return bool((content or {}).get("local_only"))


def out(tid: str, kind: str, name: str, content: dict[str, Any], *, builtin: bool,
        created_at: datetime | None = None) -> dict[str, Any]:
    return {"id": tid, "kind": kind, "name": name, "builtin": builtin, "summary": _summary(kind, content),
            "content": content, "local_only": is_local(content),
            "created_at": created_at.isoformat() if created_at else None}


async def list_templates(db: AsyncSession, user: User, *, hide_local: bool = False) -> list[dict[str, Any]]:
    """The built-in templates, then the user's own (newest first). `hide_local`: without the
    local-only ones (an API token that may not see them, or a chat on a hosted model)."""
    own = (await db.execute(select(NoteTemplate).where(NoteTemplate.user_id == user.id)
                            .order_by(NoteTemplate.created_at.desc(), NoteTemplate.id.desc()))).scalars()
    return [out(k, v["kind"], v["name"], v["content"], builtin=True) for k, v in BUILTIN.items()] + \
        [out(f"u{t.id}", t.kind, t.name, t.content, builtin=False, created_at=t.created_at) for t in own
         if not (hide_local and is_local(t.content))]


async def get_template(db: AsyncSession, user: User, tid: str, *, hide_local: bool = False
                       ) -> tuple[str, str, dict[str, Any], NoteTemplate | None]:
    """(kind, name, content, row) of a template the user may use: a built-in or their own (a
    local-only one is not found when `hide_local`)."""
    tid = str(tid or "")
    if tid in BUILTIN:
        b = BUILTIN[tid]
        return b["kind"], b["name"], b["content"], None
    if tid.startswith("u") and tid[1:].isdigit():
        t = await db.get(NoteTemplate, int(tid[1:]))
        if t is not None and t.user_id == user.id and not (hide_local and is_local(t.content)):
            return t.kind, t.name, t.content, t
    raise HTTPException(404, "template not found")


def _name(v: Any, fallback: str) -> str:
    name = " ".join(str(v or "").split())[:NAME_MAX]
    return name or fallback or "Untitled template"


def clean_content(kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    """A template's content as stored: only the fields a new note or table takes, within the limits."""
    raw = raw if isinstance(raw, dict) else {}
    if kind == "text":
        actions.check_text(title=str(raw.get("title") or ""), body=str(raw.get("body") or ""))
        return {"title": str(raw.get("title") or ""), "body": str(raw.get("body") or "")}
    if kind == "checklist":
        actions.check_text(title=str(raw.get("title") or ""))
        secs, names = [], set()
        for s in (raw.get("sections") or [])[:core.LIMITS["sections"]]:
            name = " ".join(str((s or {}).get("name") or "").split())[:core.LIMITS["section"]]
            if name and name.lower() not in names:
                names.add(name.lower())
                secs.append({"name": name, "order_mode": "learned" if s.get("order_mode") == "learned" else "manual"})
        items = []
        for i in (raw.get("items") or [])[:core.LIMITS["items"]]:
            text = " ".join(str((i or {}).get("text") or "").split())[:core.LIMITS["item"]]
            if not text:
                continue
            sec = (i or {}).get("section")
            items.append({"text": text, "qty": actions.norm_qty(i.get("qty")),
                          "extra": str(i["extra"])[:core.LIMITS["item"]] if i.get("extra") else None,
                          "section": sec if isinstance(sec, str) and sec.lower() in names else None})
        return {"title": str(raw.get("title") or ""), "shopping": bool(raw.get("shopping")),
                "sections": secs, "items": items}
    if kind == "table":
        from mailr.tables import types as ty

        cols = []
        for c in (raw.get("columns") or [])[:ty.LIMITS["columns"]]:
            c = c or {}
            if c.get("type") not in ty.TYPES or c.get("type") == "link" or not str(c.get("name") or "").strip():
                continue
            spec = {"name": str(c["name"])[:ty.LIMITS["column_name"]], "type": c["type"]}
            for k in ("config", "options", "required", "width"):
                if c.get(k) not in (None, "", [], {}):
                    spec[k] = c[k]
            cols.append(spec)
        if not cols:
            raise HTTPException(422, "A table template needs at least one column")
        return {"name": str(raw.get("name") or "")[:ty.LIMITS["name"]],
                "description": str(raw.get("description") or "")[:ty.LIMITS["description"]], "columns": cols}
    raise HTTPException(422, f"kind must be one of {', '.join(KINDS)}")


async def _store(db: AsyncSession, user: User, kind: str, name: str, content: dict[str, Any]) -> dict[str, Any]:
    n = len((await db.execute(select(NoteTemplate.id).where(NoteTemplate.user_id == user.id))).all())
    if n >= MAX_TEMPLATES:
        raise HTTPException(422, f"You have {MAX_TEMPLATES} templates, the most you can keep. Delete one first.")
    t = NoteTemplate(user_id=user.id, kind=kind, name=name, content=content)
    db.add(t)
    await db.flush()
    await core.notify_user(db, user.id, "templates")
    res = out(f"u{t.id}", kind, name, content, builtin=False, created_at=t.created_at)
    return {"template": res, "undo": actions.undo("DELETE", f"/notes/templates/u{t.id}", None,
                                                  f"Template {name} saved")}


def _with_local(content: dict[str, Any], local_only: bool) -> dict[str, Any]:
    return {**content, "local_only": True} if local_only else content


async def create(db: AsyncSession, user: User, kind: str, name: str, content: dict[str, Any]) -> dict[str, Any]:
    """A template from its content (the API; the undo of a delete, which keeps local-only)."""
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(KINDS)}")
    return await _store(db, user, kind, _name(name, ""),
                        _with_local(clean_content(kind, content), is_local(content if isinstance(content, dict) else {})))


async def save_note(db: AsyncSession, user: User, note: Any, name: str | None) -> dict[str, Any]:
    """ACT note.save_template: a text note's title and text, or a checklist's sections and items
    (unchecked). Quantities and extra lines are kept; who added what, checks and history are not."""
    if note.kind == "checklist":
        secs = await core.sections_of(db, note)
        sname = {s.id: s.name for s in secs}
        items = core.display(note, secs, await core.items_of(db, note), {}, fold=False)
        content = {"title": note.title, "shopping": bool(note.shopping),
                   "sections": [{"name": s.name, "order_mode": s.order_mode}
                                for s in sorted(secs, key=lambda s: (s.order, s.id))],
                   "items": [{"text": i.text, "qty": core.qty_out(i) and {"amount": core.qty_out(i)["amount"],
                                                                           "unit": core.qty_out(i)["unit"]},
                              "extra": i.extra, "section": sname.get(i.section_id)} for i in items]}
    else:
        content = {"title": note.title, "body": note.body}
    kind = note.kind
    return await _store(db, user, kind, _name(name, note.title),
                        _with_local(clean_content(kind, content), bool(note.local_only)))


async def save_table(db: AsyncSession, user: User, t: Any, name: str | None) -> dict[str, Any]:
    """ACT table.save_template: the table's columns in their order, with no rows."""
    from mailr.tables import core as tcore

    cols = [c for c in await tcore.columns_of(db, t) if c.deleted_at is None]
    content = {"name": t.name, "description": t.description or "",
               "columns": [{"name": c.name, "type": c.type, "config": dict(c.config or {}), "required": bool(c.required),
                            "width": c.width} for c in sorted(cols, key=lambda c: c.order)]}
    if not any(c.type != "link" for c in cols):
        raise HTTPException(422, "This table has only link columns, which a template cannot keep")
    return await _store(db, user, "table", _name(name, t.name),
                        _with_local(clean_content("table", content), bool(t.local_only)))


async def delete(db: AsyncSession, user: User, tid: str, *, hide_local: bool = False) -> dict[str, Any]:
    """ACT note.delete_template: the user's own template; the undo makes it again. With
    `hide_local` a local-only template is not found (404), so its content never reaches the undo."""
    if str(tid) in BUILTIN:
        raise HTTPException(403, "Built-in templates cannot be deleted")
    kind, name, content, row = await get_template(db, user, tid, hide_local=hide_local)
    await db.delete(row)
    await db.flush()
    await core.notify_user(db, user.id, "templates")
    return {"ok": True, "undo": actions.undo("POST", "/notes/templates", {"kind": kind, "name": name,
                                                                          "content": content},
                                             f"Template {name} deleted")}


async def use(db: AsyncSession, user: User, tid: str, *, title: str | None = None, labels: list[str] | None = None,
              group: str | None = None, color: Any = None, hide_local: bool = False,
              local_only: bool = False) -> dict[str, Any]:
    """ACT note.from_template: a new note, list or table from a template (all items unchecked). The
    answer holds `note` or `table`, and an undo that moves it to Trash. A local-only template (or
    `local_only`) makes a local-only item."""
    kind, name, content, _ = await get_template(db, user, tid, hide_local=hide_local)
    local = local_only or is_local(content)
    if kind == "table":
        from mailr.tables import actions as tactions

        made = await tactions.create_table(db, user, name=title or content.get("name") or name,
                                           description=content.get("description") or "",
                                           columns=content.get("columns"), labels=labels, group=group, color=color,
                                           local_only=local)
        made["undo"] = actions.undo("DELETE", f"/tables/{made['table']['id']}", None, f"Table from {name}")
        return made
    if kind == "text":
        made = await actions.create_note(db, user, kind="text", title=title or content.get("title") or "",
                                         body=content.get("body") or "", labels=labels, group=group, color=color,
                                         local_only=local)
        made["undo"] = actions.undo("DELETE", f"/notes/{made['note']['id']}", None, f"Note from {name}")
        return made
    shopping = bool(content.get("shopping"))
    made = await actions.create_note(db, user, kind="checklist", title=title or content.get("title") or name,
                                     labels=labels, group=group, color=color, shopping=shopping, local_only=local)
    from mailr.notes import access

    note = await access.get(db, user, made["note"]["id"], need="edit")
    secs: list[Any] = []
    by_name: dict[str, str] = {}
    for s in content.get("sections") or []:
        sec = await actions._new_section(db, note, s["name"], secs)
        sec.order_mode = s.get("order_mode") or sec.order_mode
        secs.append(sec)
        by_name[s["name"].lower()] = sec.id
    cands = [actions.Cand(text=i["text"], qty=actions.norm_qty(i.get("qty")), extra=i.get("extra"),
                          section=by_name.get(str(i.get("section") or "").lower()))
             for i in content.get("items") or []]
    if cands:
        # force: a template may hold the same item in two sections (a packing list's "socks" twice)
        await actions.add_items(db, user, note, cands, force=True, notify=False, revision=False)
    core.stamp(note, user)
    await core.commit_note(db, note)
    await core.snapshot(db, note, user)
    return {"note": await core.note_full(db, user, note),
            "undo": actions.undo("DELETE", f"/notes/{note.pid}", None, f"List from {name}")}
