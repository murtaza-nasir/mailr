"""Every write on notes as a named operation with JSON arguments.

The routes (notes/routes.py), the assistant's tools (agent/note_tools.py) and the executor of an
approved request (notes/gate.py) all run a write through `run(db, p, op, note_id, args)`, so a
request that waits for the user's approval runs exactly the code it would have run at once. Each
operation names its permission class and the access it needs on the note; notes/gate.py decides
from that whether an agent may run it now.

`p` is the caller as a principal (`.user`, `.token`): the signed-in user, an API token, or the
user's own rights for the assistant and for an approved request (permissions.requester).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from mailr.i18n import ApiError
from mailr.notes import access, actions, core, itemadd, learn
from mailr.notes.models import Note, NoteLink, NoteSection

Fn = Callable[[Any, Any, Note | None, dict[str, Any]], Awaitable[dict[str, Any]]]


def _never(_args: dict[str, Any]) -> bool:
    return False


def _always(_args: dict[str, Any]) -> bool:
    return True


@dataclass(frozen=True)
class Op:
    fn: Fn
    cls: str  # the permission class (core/permissions.py CLASSES)
    need: str | None  # the access the note needs (access.get), or None: no note
    trashed: bool = False  # may act on a note in Trash
    # Free text an agent writes (a title, a body, an item, a section name): in a tainted turn, and
    # for an API token, it asks unless a rule allows it (agent/agentwrite.py).
    text: Callable[[dict[str, Any]], bool] = _never
    # Hides something from the user (archive, delete items): asks in a tainted turn, as hiding mail does.
    hides: Callable[[dict[str, Any]], bool] = _never
    # Asks unless a rule allows it (a label delete), as tasks.lists asks for a rename.
    soft: str | None = None
    # Always asks, even under an allow rule (no undo).
    force: str | None = None


OPS: dict[str, Op] = {}


def op(name: str, cls: str, need: str | None, **kw: Any):
    def deco(fn: Fn) -> Fn:
        OPS[name] = Op(fn=fn, cls=cls, need=need, **kw)
        return fn
    return deco


def _pick(args: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: args[k] for k in keys if k in args}


async def note_for(db, p, pid: str, spec: Op) -> Note:
    """The note an operation acts on, as the caller may use it (404, 403 or 409 otherwise)."""
    return await access.get(db, p.user, str(pid), need=spec.need or "view", trashed=spec.trashed, p=p)


async def run(db, p, name: str, note_id: str | None, args: dict[str, Any] | None) -> dict[str, Any]:
    """Run one operation. The caller commits (or rolls back on an error)."""
    spec = OPS.get(name)
    if spec is None:
        raise HTTPException(422, f"unknown notes operation {name!r}")
    note = await note_for(db, p, note_id, spec) if spec.need is not None else None
    if note is not None and _agent_write():
        # A revision checkpoint before an assistant's or an agent's write: the editing session ends,
        # so the state before the write stays a version of its own.
        await core.close_session(db, note)
    args = dict(args or {})
    res = await spec.fn(db, p, note, args)
    # Settings → Activity (core/activity.py): the change, by whoever made it (live.who).
    from mailr.core import activity

    await activity.record_note(db, p, name, note, args, res)
    return res


def _agent_write() -> bool:
    from mailr.core import live

    w = live.who()
    return w == "assistant" or w.startswith("agent:")


async def _tz(db, user):
    from mailr.calendar import tz as tzmod

    return await tzmod.user_zone(db, user)


# ---------------------------------------------------------------- notes


NOTE_FIELDS = ("kind", "title", "body", "lines", "items", "color", "labels", "shopping", "pinned", "local_only")


@op("note.create", "notes.create", None, text=_always)
async def _create(db, p, _note, args):
    group = args.get("group") or None
    if group and getattr(p, "is_token", False):
        # A group list is shared with the group: only the signed-in user shares.
        raise ApiError(403, "notes.group_list_owner_only")
    fields = _pick(args, *NOTE_FIELDS)
    if args.get("conversation_id") is not None:
        # Checked before the note exists: a note from local-only mail is local-only too.
        conv = await _own_conversation(db, p, int(args["conversation_id"]))
        if await _local_mail(db, p.user.id, conv.id):
            fields["local_only"] = True
    made = await actions.create_note(db, p.user, group=group, **fields)
    if args.get("conversation_id") is not None:
        # A note made from an email (the assistant's "make a checklist from this email") links to it.
        note = await access.get(db, p.user, made["note"]["id"], p=p)
        await actions.link(db, p.user, note, "conversation", {"conversation_id": int(args["conversation_id"])})
        made["note"] = await core.note_full(db, p.user, note)
    return made


async def _own_conversation(db, p, cid: int):
    from mailr.core.visibility import conversation_visible
    from mailr.models import Conversation

    conv = await db.get(Conversation, int(cid))
    if conv is None or conv.user_id != p.user.id or not await conversation_visible(db, p, conv.id):
        raise ApiError(404, "mail.conversation_not_found")
    return conv


async def _local_mail(db, user_id: int, conversation_id: int) -> bool:
    from mailr.core.visibility import touches_local_only

    return await touches_local_only(db, user_id, conversation_ids=[conversation_id])


@op("note.from_email", "notes.create", None, text=_always)
async def _from_email(db, p, _note, args):
    """A note from a conversation: the subject as title, a link to the conversation, the selected
    text as body; or a checklist whose items are the selected lines. A note from local-only mail is
    local-only, as a note from a local-only recording is."""
    conv = await _own_conversation(db, p, int(args["conversation_id"]))
    sel = (args.get("selection") or "").strip()
    local = await _local_mail(db, p.user.id, conv.id)
    if args.get("kind") == "checklist":
        made = await actions.create_note(db, p.user, kind="checklist", title=(conv.subject or "")[:500],
                                         lines=sel or None, local_only=local)
    else:
        made = await actions.create_note(db, p.user, kind="text", title=(conv.subject or "")[:500], body=sel,
                                         local_only=local)
    note = await access.get(db, p.user, made["note"]["id"], p=p)
    await actions.link(db, p.user, note, "conversation", {"conversation_id": conv.id})
    made["note"] = await core.note_full(db, p.user, note)
    return made


@op("note.from_recording", "notes.create", None, text=_always)
async def _from_recording(db, p, _note, args):
    """A text note from a recording: its title, date, people, summary and action items, linked to it."""
    from mailr.notes import from_recording

    return await from_recording.make(db, p, args.get("connector_id"), args.get("recording_id"))


async def capture_lists(db, p) -> list[dict[str, Any]]:
    """The checklists a capture line may name: those the caller can edit, with their sections."""
    user = p.user
    lists = list((await db.execute(select(Note).where(access.visible_to(p), Note.deleted_at.is_(None),
                                                      Note.kind == "checklist"))).scalars())
    secs: dict[int, list[str]] = {}
    for s in (await db.execute(select(NoteSection).where(NoteSection.note_id.in_([n.id for n in lists] or [0]),
                                                          NoteSection.deleted_at.is_(None))
                               .order_by(NoteSection.order))).scalars():
        secs.setdefault(s.note_id, []).append(s.name)
    return [{"id": n.pid, "name": n.title or "Untitled list", "sections": secs.get(n.id, [])} for n in lists
            if access.at_least(access.level(user, n), "edit")]


def parse_capture(args: dict[str, Any], lists: list[dict[str, Any]]) -> itemadd.Parse:
    res = itemadd.parse(str(args.get("text") or ""), "capture", lists=lists, default_list=args.get("default_list"),
                        keep=args.get("keep"))
    if res.list is None:
        raise HTTPException(422, "Name a list with #List, or choose one")
    if not res.items:
        raise ApiError(422, "notes.nothing_to_add")
    return res


@op("note.capture", "notes.edit", None, text=_always)
async def _capture(db, p, _note, args):
    """The capture line (palette, side panel): the item grammar in capture mode. `#List` picks
    a checklist the user may edit, else `default_list`; an unknown `#Name` makes a new list.
    Items without a section token go to the section their key was last in on that list."""
    user = p.user
    res = parse_capture(args, await capture_lists(db, p))
    created = False
    if res.list["create"]:
        made = await actions.create_note(db, user, kind="checklist", title=res.list["name"])
        note = await access.get(db, user, made["note"]["id"], need="edit", trashed=False, p=p)
        created = True
    else:
        note = await access.get(db, user, res.list["id"], need="edit", trashed=False, p=p)
    by = {s.name: s.id for s in await core.sections_of(db, note)}
    cands = [actions.Cand(text=i.text, qty=i.qty and actions.norm_qty(i.qty), checked=i.checked,
                          section=by.get(i.section, i.section) if i.section else actions.REMEMBER)
             for i in res.items]
    new_secs = [s for s in res.new_sections if s not in by]
    out = await actions.add_items(db, user, note, cands, new_sections=new_secs,
                                  label=f"Added to {note.title or 'the list'}")
    out["note"] = {"id": note.pid, "title": note.title}
    out["created"] = created
    out["tokens"] = [t.out() for t in res.tokens]
    if created:
        out["undo"] = actions.undo("DELETE", f"/notes/{note.pid}", None, f"Made the list {note.title}")
    return out


PATCH_FIELDS = ("title", "body", "color", "shopping", "checked_at_bottom", "suggest_usual", "local_only",
                "top_order_mode", "repeat")


@op("note.edit", "notes.edit", "edit", text=lambda a: bool(a.get("title") or a.get("body")))
async def _edit(db, _p, note, args):
    changes = {k: v for k, v in _pick(args, *PATCH_FIELDS).items() if v is not None or k in ("color", "repeat")}
    return await actions.patch_note(db, _p.user, note, changes, args.get("base_version"))


@op("note.trash", "notes.delete", "delete", trashed=True)
async def _trash(db, p, note, _args):
    return await actions.trash(db, p.user, note)


@op("note.purge", "notes.delete", "delete", trashed=True, force="hard rule: deleting forever cannot be undone")
async def _purge(db, p, note, _args):
    return await actions.purge(db, p.user, note)


@op("note.empty_trash", "notes.delete", None, force="hard rule: deleting forever cannot be undone")
async def _empty(db, p, _note, _args):
    return await actions.empty_trash(db, p.user)


@op("note.restore", "notes.edit", "delete", trashed=True)
async def _restore(db, p, note, _args):
    return await actions.restore(db, p.user, note)


@op("note.duplicate", "notes.create", "view")
async def _duplicate(db, p, note, _args):
    return await actions.duplicate(db, p.user, note)


@op("note.convert", "notes.edit", "edit")
async def _convert(db, p, note, args):
    return await actions.convert(db, p.user, note, str(args.get("to")), args.get("base_version"),
                                 restore_items=args.get("restore_items"), restore_sections=args.get("restore_sections"),
                                 restore_body=args.get("restore_body"), drop=args.get("drop"))


STATE_FIELDS = ("pinned", "archived", "labels", "add_labels", "remove_labels", "before", "after", "seen_version",
                "seen", "top_collapsed")


@op("note.state", "notes.edit", "view", trashed=True, hides=lambda a: a.get("archived") is True)
async def _state(db, p, note, args):
    """The viewer's own view of the note: a view-only sharee pins and labels it for themselves."""
    data = _pick(args, *STATE_FIELDS, "order_key")
    if "order_key" in data:  # the undo of a move puts the old key back
        st = await core.state_of(db, p.user, note, create=True)
        st.order = data.pop("order_key")
    return await actions.set_state(db, p.user, note, data)


# ACT note.remind: the viewer's own reminders on the note (notes/reminders.py). A view-only sharee
# sets reminders for themselves, as they pin and label.
@op("note.remind", "notes.edit", "view")
async def _remind(db, p, note, args):
    from mailr.notes import reminders

    return await reminders.set_reminders(db, p.user, note, list(args.get("at") or []))


# ---------------------------------------------------------------- items


ADD_FIELDS = ("lines", "items", "usual", "section_id", "after", "force", "keep")


@op("items.add", "notes.edit", "edit", text=lambda a: bool(a.get("lines") or a.get("items")))
async def _add(db, p, note, args):
    """Items from lines (the grammar), from item objects, or usual items by key. Items without
    a section token go to `section_id` (the add line's section) when it is given."""
    actions.need_list(note)
    # The assistant's add without a section: where the item was last time, else the top.
    default: Any = args["section_id"] if "section_id" in args else (actions.REMEMBER if args.get("remember") else None)
    if default is not None and default != actions.REMEMBER:
        await actions.get_section(db, note, default)
    cands: list[actions.Cand] = []
    new_secs: list[str] = []
    if args.get("lines"):
        c, new_secs = await actions.from_lines(db, note, str(args["lines"]), default=default, keep=args.get("keep"))
        cands += c
    if args.get("items"):
        cands += await actions.from_items(db, note, list(args["items"]), default=default)
    label = None
    if args.get("usual"):
        hist = await learn.history(db, note, await _tz(db, p.user))
        secs = {s.id for s in await core.sections_of(db, note)}
        for key in args["usual"]:
            h = hist.get(key)
            if h is None:
                continue
            section_id = h.get("section_id") if h.get("section_id") in secs else None
            cands.append(actions.Cand(text=h["text"], qty=h.get("qty") and actions.norm_qty(h["qty"]),
                                      section=section_id))
        n = len(cands)
        label = f"Added {cands[0].text}" if n == 1 else f"Added {n} usual items"
    for name in args.get("new_sections") or []:  # sections named by the assistant ("under Costco")
        if name not in new_secs:
            new_secs.append(name)
    if not cands and not new_secs:
        raise ApiError(422, "notes.nothing_to_add")
    return await actions.add_items(db, p.user, note, cands, new_sections=new_secs, after=args.get("after"),
                                   force=bool(args.get("force")), label=label)


# (the undo of item, section and order changes)
@op("note.revert", "notes.edit", "edit")
async def _revert(db, p, note, args):
    return await actions.revert(db, p.user, note, list(args.get("ops") or []))


@op("items.clear_checked", "notes.delete", "edit")
async def _clear(db, p, note, _args):
    return await actions.clear_checked(db, p.user, note)


@op("items.uncheck_all", "notes.edit", "edit")
async def _uncheck_all(db, p, note, _args):
    return await actions.uncheck_all(db, p.user, note)


ITEM_FIELDS = ("base_version", "checked", "device_at", "undo", "text", "parse", "keep", "qty", "extra")


@op("item.patch", "notes.edit", "edit", text=lambda a: bool(a.get("text") or a.get("extra")))
async def _item_patch(db, p, note, args):
    return await actions.patch_item(db, p.user, note, str(args.get("item_id")), _pick(args, *ITEM_FIELDS))


@op("item.delete", "notes.edit", "edit", hides=_always)
async def _item_delete(db, p, note, args):
    return await actions.delete_item(db, p.user, note, str(args.get("item_id")))


@op("item.move", "notes.edit", "edit")
async def _item_move(db, p, note, args):
    return await actions.move_item(db, p.user, note, str(args.get("item_id")),
                                   _pick(args, "before", "after", "section_id", "direction"))


@op("item.to_task", "notes.edit", "edit", text=_always)
async def _to_task(db, p, note, args):
    """A task from the item: its text with the quantity, linked back to the note. The item's text
    becomes the task's title, so an agent's request asks in a tainted turn (it is free text)."""
    from pydantic import ValidationError

    from mailr.calendar.routes_tasks import TaskIn, _x_tasks_create
    from mailr.config import get_settings
    from mailr.core import permissions

    if getattr(p, "token", None) is not None and "calendar" not in (p.token.scopes or []):
        raise HTTPException(403, "this token lacks the 'calendar' scope needed to make a task")
    item = await actions.get_item(db, note, str(args.get("item_id")))
    q = core.qty_out(item)
    title = f"{item.text} ({itemadd.qty_text(q)})" if q else item.text
    url = f"{get_settings().base_url.rstrip('/')}/notes/n/{note.pid}#i-{item.id}"
    try:
        # A Markdown link: the task's Notes show the list's name as the link, not the long URL.
        name = re.sub(r"([\\\[\]])", r"\\\1", note.title or "Untitled")
        body = TaskIn(summary=title[:500], notes=f"From the list [{name}]({url})", list=args.get("list"))
    except ValidationError as e:
        raise HTTPException(422, str(e)[:300]) from None
    payload = {k: v for k, v in body.model_dump().items() if v not in (None, "")}
    try:
        res = await _x_tasks_create(db, p.user, permissions.stamp(payload, p))
    except LookupError as e:
        raise HTTPException(404, str(e).strip("'")) from None
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001  no calendar connected, the server refused
        from mailr.calendar.routes import _need_calendar

        raise _need_calendar(e) from None
    uid = res.get("uid")
    item.task_uid = uid
    db.add(NoteLink(note_id=note.id, item_id=item.id, target_kind="task", target_ref={"uid": uid},
                    target_key=f"task:{uid}", title=title[:300], created_by=p.user.id))
    await db.flush()
    v = await core.commit_item(db, note, item)
    return {"task_uid": uid, "task": res, "item": core.item_out(item, {}, v),
            "undo": actions.undo("DELETE", f"/notes/{note.pid}/items/{item.id}/task", None, "Task created")}


# (the undo of checkitem.to_task)
@op("item.untask", "notes.edit", "edit")
async def _untask(db, p, note, args):
    from mailr.calendar.routes_tasks import _x_tasks_delete
    from mailr.core import permissions

    item = await actions.get_item(db, note, str(args.get("item_id")))
    uid = item.task_uid
    if not uid:
        return {"ok": True}
    item.task_uid = None
    await db.execute(NoteLink.__table__.delete().where(NoteLink.note_id == note.id, NoteLink.item_id == item.id,
                                                       NoteLink.target_key == f"task:{uid}"))
    await core.commit_item(db, note, item)
    await db.flush()
    try:
        await _x_tasks_delete(db, p.user, permissions.stamp({"uid": uid}, p))
    except (LookupError, HTTPException) as e:
        if isinstance(e, HTTPException) and e.status_code != 404:
            raise
    return {"ok": True}


# ---------------------------------------------------------------- sections


@op("section.create", "notes.edit", "edit", text=_always)
async def _section_create(db, p, note, args):
    return await actions.create_section(db, p.user, note, str(args.get("name") or ""), args.get("after"))


SECTION_FIELDS = ("name", "base_name", "before", "after", "direction", "order_mode", "collapsed", "reset_learned")


@op("section.patch", "notes.edit", "edit", text=lambda a: bool(a.get("name")))
async def _section_patch(db, p, note, args):
    return await actions.patch_section(db, p.user, note, str(args.get("section_id")), _pick(args, *SECTION_FIELDS))


# The viewer's collapse of a section (any level that sees the note).
@op("section.collapse", "notes.edit", "view")
async def _section_collapse(db, p, note, args):
    return await actions.patch_section(db, p.user, note, str(args.get("section_id")),
                                       {"collapsed": bool(args.get("collapsed"))})


@op("section.delete", "notes.edit", "edit", hides=lambda a: a.get("items") == "delete")
async def _section_delete(db, p, note, args):
    return await actions.delete_section(db, p.user, note, str(args.get("section_id")), str(args.get("items") or "keep"))


# ---------------------------------------------------------------- usual items, revisions, links


@op("usual.dismiss", "notes.edit", "view", trashed=True)
async def _dismiss(db, p, note, args):
    key = str(args.get("key") or "")
    never = args.get("for") == "never"
    await learn.dismiss(db, note, p.user.id, key, never=never, tz=await _tz(db, p.user))
    await core.touch(db, note)
    return {"ok": True, "undo": actions.undo("DELETE", f"/notes/{note.pid}/usual/{key}/dismiss", None,
                                             "Won't suggest it again" if never else "Hidden for now")}


@op("usual.undismiss", "notes.edit", "view", trashed=True)
async def _undismiss(db, p, note, args):
    await learn.undismiss(db, note, p.user.id, str(args.get("key") or ""))
    await core.touch(db, note)
    return {"ok": True}


@op("revision.restore", "notes.edit", "edit")
async def _restore_version(db, p, note, args):
    return await actions.restore_version(db, p.user, note, int(args.get("rev") or 0))


@op("link.add", "notes.edit", "edit")
async def _link(db, p, note, args):
    return await actions.link(db, p.user, note, str(args.get("target_kind") or ""), dict(args.get("target_ref") or {}))


@op("link.remove", "notes.edit", "edit")
async def _unlink(db, p, note, args):
    return await actions.unlink(db, p.user, note, int(args.get("link_id") or 0))


# ---------------------------------------------------------------- templates (notes/templates.py)


@op("note.save_template", "notes.create", "view", text=lambda a: bool(a.get("name")))
async def _save_template(db, p, note, args):
    from mailr.notes import templates

    return await templates.save_note(db, p.user, note, args.get("name"))


@op("note.from_template", "notes.create", None, text=lambda a: bool(a.get("title")))
async def _from_template(db, p, _note, args):
    from mailr.notes import templates

    group = args.get("group") or None
    if group and getattr(p, "is_token", False):
        raise ApiError(403, "notes.group_list_owner_only")
    hide = access.hidden_from(p)  # a local-only template is not there for this token
    if getattr(p, "is_token", False):
        kind, *_ = await templates.get_template(db, p.user, str(args.get("template") or ""), hide_local=hide)
        if kind == "table":  # tables are open to API tokens through the assistant's tools only
            from mailr.knowledge import profile

            raise ApiError(403, "tables.tokens_use_tools", name=await profile.name_of(db, p.user_id))
    return await templates.use(db, p.user, str(args.get("template") or ""), title=args.get("title") or None,
                               labels=args.get("labels"), group=group, color=args.get("color"), hide_local=hide,
                               local_only=bool(args.get("local_only")))


@op("template.create", "notes.create", None, text=_always)
async def _template_create(db, p, _note, args):
    from mailr.notes import templates

    return await templates.create(db, p.user, str(args.get("kind") or ""), str(args.get("name") or ""),
                                  dict(args.get("content") or {}))


@op("template.delete", "notes.delete", None)
async def _template_delete(db, p, _note, args):
    from mailr.notes import templates

    # A token that may not see local-only templates cannot delete one (or get its content back in
    # the undo), also when the user approves later.
    return await templates.delete(db, p.user, str(args.get("template") or ""), hide_local=access.hidden_from(p))


# ---------------------------------------------------------------- labels


@op("label.create", "notes.labels", None)
async def _label_create(db, p, _note, args):
    return await actions.create_label(db, p.user, str(args.get("name") or ""), args.get("color"))


@op("label.patch", "notes.labels", None)
async def _label_patch(db, p, _note, args):
    return await actions.patch_label(db, p.user, str(args.get("label") or ""),
                                     _pick(args, "name", "color", "before", "after", "order_key"))


@op("label.delete", "notes.labels", None, soft="deleting a label asks unless a rule allows it")
async def _label_delete(db, p, _note, args):
    return await actions.delete_label(db, p.user, str(args.get("label") or ""))


# (the undo of notelabel.delete)
@op("label.restore", "notes.labels", None)
async def _label_restore(db, p, _note, args):
    return await actions.restore_label(db, p.user, dict(args.get("label") or {}), list(args.get("notes") or []))
