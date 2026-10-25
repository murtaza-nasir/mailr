"""Notes and checklists API.

The signed-in user and API tokens with the `notes` scope call these routes. Every write runs one
operation of notes/ops.py through notes/gate.py, which asks the permission engine first for a
token (202 with the pending approval when it asks, 403 on deny). Every route finds the note through
notes/access.py, so a note the caller cannot see is a 404; a token that is not local-only does not
see local-only notes.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import any_, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.api import contract as C
from mailr.core import webheaders
from mailr.core.auth import Principal, scope
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.notes import access, actions, core, exporting, gate, learn, search
from mailr.notes.models import Note, NoteItem, NoteLink, NoteRevision, NoteSection, NoteViewerState

router = APIRouter(tags=["notes"])

# Reads and writes of notes need the `notes` scope (a session has every scope).
notes_user = scope("notes")


async def _write(db: AsyncSession, p: Principal, op: str, note_id: str | None, args: dict[str, Any]) -> Any:
    """One write: notes/ops.py through the permission gate (notes/gate.py)."""
    return await gate.route(db, p, op, note_id, args)


async def _note(db: AsyncSession, p: Principal, note_id: str, **kw: Any) -> Note:
    return await access.get(db, p.user, note_id, p=p, **kw)


# ---------------------------------------------------------------- the grid and search


VIEWS = ("all", "lists", "tables", "archive", "trash", "label", "shared", "reminders")
SORTS = ("manual", "edited", "created", "title")


def sort_cards(cards: list[dict[str, Any]], sort: str, view: str) -> list[dict[str, Any]]:
    if view == "reminders":  # the Reminders view: next reminder first
        return sorted(cards, key=lambda c: c.get("next_reminder") or "")
    if view == "trash":
        return sorted(cards, key=lambda c: c["deleted_at"] or "", reverse=True)
    if view == "archive" or sort == "edited":
        key = lambda c: c["updated_at"] or ""  # noqa: E731
        rows = sorted(cards, key=key, reverse=True)
    elif sort == "created":
        rows = sorted(cards, key=lambda c: c["created_at"] or "", reverse=True)
    elif sort == "title":
        rows = sorted(cards, key=lambda c: ((c["title"] or "").lower(), c["created_at"] or ""))
    else:  # manual: the viewer's order keys, newest first for notes without one
        rows = sorted(cards, key=lambda c: c["created_at"] or "", reverse=True)
        rows = sorted(rows, key=lambda c: (c["state"]["order"] is not None, c["state"]["order"] or ""))
    if view in ("archive", "trash"):
        return rows
    return [c for c in rows if c["state"]["pinned"]] + [c for c in rows if not c["state"]["pinned"]]


def by_owner(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shared with me: by owner (a person's name, or the group's), then the sort already made."""
    return sorted(cards, key=lambda c: ((c["owner"].get("name") or "").lower()))


# ACT note.search @api (the grid's views)
@router.get("/notes", response_model=C.notes.NoteList, **C.TYPED)
async def list_notes(view: str = "all", label: str | None = None, sort: str = "manual",
                     p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    """The grid: Notes (not archived), Lists, Archive, Trash, Reminders or one label's notes."""
    if view not in VIEWS:
        raise HTTPException(400, f"view must be one of {', '.join(VIEWS)}")  # i18n: internal
    if sort not in SORTS:
        raise HTTPException(400, f"sort must be one of {', '.join(SORTS)}")  # i18n: internal
    user = p.user
    q = select(Note).where(access.visible_to(p))
    q = q.where(Note.deleted_at.is_not(None) if view == "trash" else Note.deleted_at.is_(None))
    if view == "lists":
        q = q.where(Note.kind == "checklist")
    if view == "shared":  # owned by someone else or by a group (the Shared view)
        q = q.where(or_(Note.owner_user_id.is_(None), Note.owner_user_id != user.id))
    st = select(NoteViewerState.item_id).where(NoteViewerState.user_id == user.id,
                                                NoteViewerState.item_kind == "note")
    if view == "archive":
        q = q.where(Note.id.in_(st.where(NoteViewerState.archived.is_(True))))
    elif view in ("all", "lists", "label", "shared"):
        q = q.where(Note.id.not_in(st.where(NoteViewerState.archived.is_(True))))
    if view == "reminders":  # notes with a reminder of this viewer's that is not handled, archived ones too
        from mailr.notes import reminders

        q = q.where(Note.id.in_(await reminders.view_ids(db, user) or [0]))
    if view == "label":
        if not label:
            raise HTTPException(400, "label is required")  # i18n: internal
        lb = await actions.get_label(db, user, label)
        q = q.where(Note.id.in_(st.where(literal(lb.name) == any_(NoteViewerState.labels))))
    notes = list((await db.execute(q.limit(2000))).scalars()) if view != "tables" else []
    if view == "trash":  # Trash holds what the viewer may restore; sharees lose a deleted note
        notes = [n for n in notes if access.can_delete(user, n)]
    cards = sort_cards(await core.note_cards(db, user, notes), sort, view)
    if view == "shared":
        cards = by_owner(cards)
    # The Notes views hold tables too; Lists does not.
    from mailr.tables.routes import cards_for

    tables = (sort_cards(await cards_for(db, user, view, label, hide_local=access.hidden_from(p)), sort, view)
              if view not in ("lists", "reminders") else [])
    if view == "shared":
        tables = by_owner(tables)
    counts = await actions.label_counts(db, user)
    labels = [actions.label_out(lb, counts.get(lb.name.lower(), 0)) for lb in await actions.labels_of(db, user)]
    await db.commit()
    # The caller's groups, for "New list in <group>".
    return {"notes": cards, "tables": tables, "labels": labels, "groups": access.groups(user)}


# ACT note.search @api
@router.get("/notes/search")
async def search_notes(q: str = "", limit: int = Query(50, ge=1, le=200), p: Principal = Depends(notes_user),
                       db: AsyncSession = Depends(get_db)):
    """Full text over titles, bodies, items, extra lines and section names, with the search
    operators of the notes API. Item matches name their item."""
    res = await search.run(db, p.user, q, limit=limit, p=p)
    await db.commit()
    return res


# ---------------------------------------------------------------- labels


class LabelIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    color: str | None = None


class LabelPatch(BaseModel):
    name: str | None = Field(None, max_length=200)
    color: str | None = None
    before: str | None = None
    after: str | None = None
    order_key: str | None = Field(None, max_length=64)


class LabelRestore(BaseModel):
    label: dict[str, Any]
    notes: list[str] = Field(default_factory=list, max_length=5000)


@router.get("/notes/labels")
async def get_labels(p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    counts = await actions.label_counts(db, p.user)
    return {"labels": [actions.label_out(lb, counts.get(lb.name.lower(), 0))
                       for lb in await actions.labels_of(db, p.user)]}


# ACT notelabel.create @api
@router.post("/notes/labels")
async def create_label(body: LabelIn, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "label.create", None, body.model_dump())


# (the undo of notelabel.delete)
@router.post("/notes/labels/restore")
async def restore_label(body: LabelRestore, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "label.restore", None, body.model_dump())


# ACT notelabel.rename @api, ACT notelabel.color @api, ACT notelabel.reorder @api
@router.patch("/notes/labels/{name}")
async def patch_label(name: str, body: LabelPatch, p: Principal = Depends(notes_user),
                      db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "label.patch", None, {**body.model_dump(exclude_unset=True), "label": name})


# ACT notelabel.delete @api
@router.delete("/notes/labels/{name}")
async def delete_label(name: str, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "label.delete", None, {"label": name})


# ---------------------------------------------------------------- export, sync, Trash, capture, email


# ACT note.export @api
@router.get("/notes/export")
async def export_all(p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    """A zip of every note: one Markdown file each, and notes.json with everything."""
    notes = list((await db.execute(select(Note).where(access.visible_to(p), Note.deleted_at.is_(None))
                                   .order_by(Note.created_at))).scalars())
    buf = io.BytesIO()
    full = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        used: set[str] = set()
        for n in notes:
            data = await core.note_full(db, p.user, n)
            full.append(data)
            z.writestr(exporting.filename(n, used), exporting.markdown(data))
        z.writestr("notes.json", json.dumps({"exported_at": datetime.now(UTC).isoformat(), "notes": full},
                                            indent=1, default=str))
    await db.commit()
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="notes-{stamp}.zip"'})


def _since(since: str | None) -> datetime | None:
    if not since:
        return None
    try:
        t = datetime.fromisoformat(since)
    except ValueError:
        raise HTTPException(400, "since must be an ISO 8601 time") from None  # i18n: internal
    return t if t.tzinfo else t.replace(tzinfo=UTC)


@router.get("/notes/sync")
async def sync(since: str | None = None, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    """The phones' delta: notes changed since a time (their own fields, items, sections or
    this viewer's state) and notes shared with the caller since then, with items; notes in Trash by
    id; and `visible`, every note the caller sees now (Trash left out) with the caller's level, so a
    phone drops what was unshared, left or purged and notices a changed level.
    → {notes, trashed, visible, now}."""
    from mailr.notes import acl

    now = datetime.now(UTC)
    t = _since(since)
    q = select(Note).where(access.visible_to(p))
    if t is not None:
        changed_state = select(NoteViewerState.item_id).where(NoteViewerState.user_id == p.user.id,
                                                              NoteViewerState.item_kind == "note",
                                                              NoteViewerState.updated_at > t)
        q = q.where((Note.updated_at > t) | (Note.deleted_at > t) | Note.id.in_(changed_state)
                    | Note.id.in_(acl.shared_since(p.user, "note", t)))
    notes = list((await db.execute(q.limit(5000))).scalars())
    out = [await core.note_full(db, p.user, n) for n in notes if n.deleted_at is None]
    trashed = [n.pid for n in notes if n.deleted_at is not None]
    live = (await db.execute(select(Note).where(access.visible_to(p), Note.deleted_at.is_(None)).limit(5000))).scalars()
    visible = {n.pid: access.level(p.user, n) for n in live}
    await db.commit()
    return {"notes": out, "trashed": trashed, "visible": visible, "now": now.isoformat()}


# ACT note.purge @api ("Empty Trash")
@router.post("/notes/trash/empty")
async def empty_trash(p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "note.empty_trash", None, {})


class CaptureIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    default_list: str | None = None
    keep: list[str] | None = None


# ACT note.add_items @api (capture mode: a `#List` token picks the list)
@router.post("/notes/capture")
async def capture(body: CaptureIn, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    """The capture line (palette, side panel): the item grammar in capture mode. `#List` picks
    a checklist the user may edit, else `default_list`; an unknown `#Name` makes a new list.
    Items without a section token go to the section their key was last in on that list."""
    return await _write(db, p, "note.capture", None, body.model_dump())


class QuickIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


# ACT note.quick @api
@router.post("/notes/quick")
async def quick(body: QuickIn, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    """"New note…" from anywhere: text that starts with `#List` adds items to that list
    as the capture line does (`#Shopping Costco: milk`); anything else is a text note, its first
    line the title. The answer is the capture's or the new note's, with `kind`."""
    from mailr.notes import itemadd

    q = itemadd.quick(body.text)
    if q["kind"] == "items":
        out = await _write(db, p, "note.capture", None, {"text": body.text})
    else:
        out = await _write(db, p, "note.create", None, {"kind": "text", "title": q["title"], "body": q["body"]})
    if isinstance(out, dict):
        out = {**out, "kind": q["kind"]}
    return out


class FromEmailIn(BaseModel):
    conversation_id: int
    selection: str | None = Field(None, max_length=20_000)
    kind: str = Field("text", pattern="^(text|checklist)$")


# ACT note.from_email @api
@router.post("/notes/from-email")
async def from_email(body: FromEmailIn, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    """A note from a conversation: the subject as title, a link to the conversation, the selected
    text as body; or a checklist whose items are the selected lines."""
    return await _write(db, p, "note.from_email", None, body.model_dump())


class FromRecordingIn(BaseModel):
    connector_id: int
    recording_id: int


# ACT note.from_recording @api
@router.post("/notes/from-recording")
async def from_recording(body: FromRecordingIn, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    """A text note from a Speakr recording: its title, date, people, summary and action items, linked
    to the recording. Local-only when the recording is."""
    from mailr.notes import from_recording as fr

    await fr.check(db, p, body.connector_id, body.recording_id)  # before an agent's request waits for approval
    return await _write(db, p, "note.from_recording", None, body.model_dump())


@router.get("/notes/linked")
async def linked(target_kind: str, ref: str | None = None, conversation_id: int | None = None,
                 p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    """The notes linked to a conversation, an event, a task or a recording (its links panel)."""
    if target_kind not in actions.TARGET_KINDS:
        raise HTTPException(400, "unknown target_kind")  # i18n: internal
    key_id = conversation_id if target_kind == "conversation" else ref
    if key_id in (None, ""):
        raise HTTPException(400, "ref is required")  # i18n: internal
    rows = (await db.execute(select(Note, NoteLink).join(NoteLink, NoteLink.note_id == Note.id).where(
        NoteLink.target_key == f"{target_kind}:{key_id}", access.visible_to(p), Note.deleted_at.is_(None))
        .order_by(Note.updated_at.desc()))).all()
    seen: set[str] = set()
    out = []
    for n, ln in rows:
        if n.pid in seen:
            continue
        seen.add(n.pid)
        out.append({"id": n.pid, "title": n.title, "kind": n.kind, "link_id": ln.id, "item_id": ln.item_id})
    return {"notes": out}


# ---------------------------------------------------------------- one note


class NoteIn(BaseModel):
    kind: str = Field("text", pattern="^(text|checklist)$")
    title: str = Field("", max_length=500)
    body: str = Field("", max_length=20_000)
    lines: str | None = Field(None, max_length=50_000)
    items: list[dict[str, Any]] | None = Field(None, max_length=1000)
    color: str | None = None
    labels: list[str] | None = Field(None, max_length=200)
    shopping: bool = False
    pinned: bool = False
    local_only: bool = False
    group: str | None = Field(None, max_length=200)


# ACT note.create @api
@router.post("/notes")
async def create_note(body: NoteIn, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    if body.group and p.is_token:  # a group list is shared with the group: only the signed-in user shares
        raise ApiError(403, "notes.group_list_owner_only")
    return await _write(db, p, "note.create", None, body.model_dump())


@router.get("/notes/{note_id}", response_model=C.notes.Note, **C.TYPED)
async def get_note(note_id: str, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    note = await _note(db, p, note_id)
    out = await core.note_full(db, p.user, note)
    await db.commit()
    return out


class NotePatch(BaseModel):
    base_version: int | None = None
    title: str | None = Field(None, max_length=500)
    body: str | None = Field(None, max_length=20_000)
    color: str | None = None
    shopping: bool | None = None
    checked_at_bottom: bool | None = None
    suggest_usual: bool | None = None
    local_only: bool | None = None
    top_order_mode: str | None = Field(None, pattern="^(learned|manual)$")
    # A list's repeat (notes/repeat.py): FREQ=WEEKLY;BYDAY=SU or FREQ=DAILY;INTERVAL=3; null stops it.
    repeat: str | None = Field(None, max_length=200)


# ACT note.edit @api, ACT note.color @api, ACT note.repeat @api, ACT note.local_only @api
@router.patch("/notes/{note_id}")
async def patch_note(note_id: str, body: NotePatch, p: Principal = Depends(notes_user),
                     db: AsyncSession = Depends(get_db)):
    changes = body.model_dump(exclude_unset=True)
    changes = {k: v for k, v in changes.items() if v is not None or k in ("color", "base_version", "repeat")}
    return await _write(db, p, "note.edit", note_id, changes)


# ACT note.delete @api, ACT note.purge @api
@router.delete("/notes/{note_id}")
async def delete_note(note_id: str, forever: bool = False, p: Principal = Depends(notes_user),
                      db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "note.purge" if forever else "note.trash", note_id, {})


# ACT note.restore @api
@router.post("/notes/{note_id}/restore")
async def restore_note(note_id: str, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "note.restore", note_id, {})


# ACT note.duplicate @api
@router.post("/notes/{note_id}/duplicate")
async def duplicate_note(note_id: str, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "note.duplicate", note_id, {})


class ConvertIn(BaseModel):
    to: str = Field(pattern="^(checklist|text)$")
    base_version: int | None = None
    restore_items: list[str] | None = Field(None, max_length=1000)
    restore_sections: list[str] | None = Field(None, max_length=50)
    restore_body: str | None = Field(None, max_length=20_000)
    drop: list[str] | None = Field(None, max_length=1000)


# ACT note.to_checklist @api, ACT note.to_text @api
@router.post("/notes/{note_id}/convert")
async def convert_note(note_id: str, body: ConvertIn, p: Principal = Depends(notes_user),
                       db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "note.convert", note_id, body.model_dump())


class StateIn(BaseModel):
    pinned: bool | None = None
    archived: bool | None = None
    labels: list[str] | None = Field(None, max_length=200)
    add_labels: list[str] | None = Field(None, max_length=200)
    remove_labels: list[str] | None = Field(None, max_length=200)
    before: str | None = None
    after: str | None = None
    order_key: str | None = Field(None, max_length=64)
    seen_version: int | None = None
    seen: bool | None = None
    top_collapsed: bool | None = None


# ACT note.pin @api, ACT note.archive @api, ACT note.label @api, ACT note.reorder @api
@router.put("/notes/{note_id}/state")
async def put_state(note_id: str, body: StateIn, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "note.state", note_id, body.model_dump(exclude_unset=True))


class RemindIn(BaseModel):
    at: list[str] = Field(default_factory=list, max_length=10)


# ACT note.remind @api
@router.put("/notes/{note_id}/reminders")
async def put_reminders(note_id: str, body: RemindIn, p: Principal = Depends(notes_user),
                        db: AsyncSession = Depends(get_db)):
    """The caller's reminders on the note: ISO times with a zone, none to remove them. Each is a key
    `n|<id>|abs|<instant>` on the shared reminder contract; snooze and dismiss through /reminders."""
    return await _write(db, p, "note.remind", note_id, body.model_dump())


# ACT note.export @api
@router.get("/notes/{note_id}/export")
async def export_note(note_id: str, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    note = await _note(db, p, note_id)
    data = await core.note_full(db, p.user, note)
    await db.commit()
    name = exporting.filename(note, set())
    return Response(exporting.markdown(data), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": webheaders.attachment(name)})


# ---------------------------------------------------------------- items


class AddIn(BaseModel):
    lines: str | None = Field(None, max_length=50_000)
    items: list[dict[str, Any]] | None = Field(None, max_length=1000)
    usual: list[str] | None = Field(None, max_length=50)
    section_id: str | None = None
    after: str | None = None
    force: bool = False
    keep: list[str] | None = None


# ACT checkitem.add @api, ACT note.add_items @api, ACT checkitem.usual_add @api
@router.post("/notes/{note_id}/items")
async def add_items(note_id: str, body: AddIn, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    """Items from lines (the grammar), from item objects, or usual items by key. Items without
    a section token go to `section_id` (the add line's section) when it is given."""
    return await _write(db, p, "items.add", note_id, body.model_dump(exclude_unset=True))


class RevertIn(BaseModel):
    ops: list[dict[str, Any]] = Field(max_length=20)


# (the undo of item, section and order changes)
@router.post("/notes/{note_id}/revert")
async def revert(note_id: str, body: RevertIn, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "note.revert", note_id, body.model_dump())


# ACT note.clear_checked @api
@router.post("/notes/{note_id}/items/clear-checked")
async def clear_checked(note_id: str, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "items.clear_checked", note_id, {})


# ACT note.uncheck_all @api
@router.post("/notes/{note_id}/items/uncheck-all")
async def uncheck_all(note_id: str, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "items.uncheck_all", note_id, {})


class ItemPatch(BaseModel):
    base_version: int | None = None
    checked: bool | None = None
    device_at: str | None = None
    undo: bool = False  # the undo of a check: applies whatever the device times say
    text: str | None = Field(None, max_length=1000)
    parse: bool = False
    keep: list[str] | None = None
    qty: dict[str, Any] | None = None
    extra: str | None = Field(None, max_length=1000)


# ACT checkitem.check @api, ACT checkitem.uncheck @api, ACT checkitem.edit @api
@router.patch("/notes/{note_id}/items/{item_id}")
async def patch_item(note_id: str, item_id: str, body: ItemPatch, p: Principal = Depends(notes_user),
                     db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "item.patch", note_id, {**body.model_dump(exclude_unset=True), "item_id": item_id})


# ACT checkitem.delete @api
@router.delete("/notes/{note_id}/items/{item_id}")
async def delete_item(note_id: str, item_id: str, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "item.delete", note_id, {"item_id": item_id})


class MoveIn(BaseModel):
    before: str | None = None
    after: str | None = None
    section_id: str | None = None
    direction: str | None = Field(None, pattern="^(up|down|prev_section|next_section)$")


# ACT checkitem.move @api
@router.post("/notes/{note_id}/items/{item_id}/move")
async def move_item(note_id: str, item_id: str, body: MoveIn, p: Principal = Depends(notes_user),
                    db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "item.move", note_id, {**body.model_dump(exclude_unset=True), "item_id": item_id})


class ToTaskIn(BaseModel):
    list: str | None = None


# ACT checkitem.to_task @api
@router.post("/notes/{note_id}/items/{item_id}/to-task")
async def to_task(note_id: str, item_id: str, body: ToTaskIn | None = None,
                  p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    """A task from the item: its text with the quantity, linked back to the note."""
    return await _write(db, p, "item.to_task", note_id, {"item_id": item_id, "list": body.list if body else None})


# (the undo of checkitem.to_task)
@router.delete("/notes/{note_id}/items/{item_id}/task")
async def untask(note_id: str, item_id: str, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "item.untask", note_id, {"item_id": item_id})


# ---------------------------------------------------------------- sections


class SectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    after: str | None = None


# ACT checksection.create @api
@router.post("/notes/{note_id}/sections")
async def create_section(note_id: str, body: SectionIn, p: Principal = Depends(notes_user),
                         db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "section.create", note_id, body.model_dump())


class SectionPatch(BaseModel):
    name: str | None = Field(None, max_length=200)
    base_name: str | None = Field(None, max_length=200)
    before: str | None = None
    after: str | None = None
    direction: str | None = Field(None, pattern="^(up|down)$")
    order_mode: str | None = Field(None, pattern="^(learned|manual)$")
    collapsed: bool | None = None
    reset_learned: bool = False


# ACT checksection.rename @api, ACT checksection.reorder @api, ACT checksection.order_mode @api
@router.patch("/notes/{note_id}/sections/{section_id}")
async def patch_section(note_id: str, section_id: str, body: SectionPatch, p: Principal = Depends(notes_user),
                        db: AsyncSession = Depends(get_db)):
    data = body.model_dump(exclude_unset=True)
    if set(data) <= {"collapsed"}:  # the viewer's own collapse: any level that sees the note
        return await _write(db, p, "section.collapse", note_id, {"section_id": section_id, **data})
    return await _write(db, p, "section.patch", note_id, {**data, "section_id": section_id})


# ACT checksection.delete @api
@router.delete("/notes/{note_id}/sections/{section_id}")
async def delete_section(note_id: str, section_id: str, items: str = "keep", p: Principal = Depends(notes_user),
                         db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "section.delete", note_id, {"section_id": section_id, "items": items})


# ---------------------------------------------------------------- usual items


@router.get("/notes/{note_id}/usual")
async def usual(note_id: str, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    note = await _note(db, p, note_id)
    if note.kind != "checklist":
        return {"items": [], "hidden": [], "enabled": False}
    from mailr.calendar import tz as tzmod

    tz = await tzmod.user_zone(db, p.user)
    items, hidden = await learn.suggestions(db, note, p.user.id, tz)
    names = {s.id: s.name for s in await core.sections_of(db, note)}
    await db.commit()
    return {"items": [u.out(names) for u in items] if note.suggest_usual else [], "hidden": hidden,
            "enabled": bool(note.suggest_usual)}


class DismissIn(BaseModel):
    for_: str = Field("interval", alias="for", pattern="^(interval|never)$")


# ACT checkitem.usual_dismiss @api
@router.post("/notes/{note_id}/usual/{key}/dismiss")
async def dismiss(note_id: str, key: str, body: DismissIn | None = None, p: Principal = Depends(notes_user),
                  db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "usual.dismiss", note_id, {"key": key, "for": body.for_ if body else "interval"})


@router.delete("/notes/{note_id}/usual/{key}/dismiss")
async def undismiss(note_id: str, key: str, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "usual.undismiss", note_id, {"key": key})


# ---------------------------------------------------------------- revisions


# ACT note.history @api
@router.get("/notes/{note_id}/revisions")
async def revisions(note_id: str, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    note = await _note(db, p, note_id)
    rows = (await db.execute(select(NoteRevision).where(NoteRevision.note_id == note.id)
                             .order_by(NoteRevision.rev.desc()).limit(500))).scalars()
    return {"revisions": [core.revision_out(r) for r in rows]}


# ACT note.history @api
@router.get("/notes/{note_id}/revisions/{rev}")
async def revision(note_id: str, rev: int, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    note = await _note(db, p, note_id)
    r = (await db.execute(select(NoteRevision).where(NoteRevision.note_id == note.id, NoteRevision.rev == rev))
         ).scalar_one_or_none()
    if r is None:
        raise ApiError(404, "notes.version_not_found")
    return core.revision_out(r, content=True)


# ACT note.restore_version @api
@router.post("/notes/{note_id}/revisions/{rev}/restore")
async def restore_version(note_id: str, rev: int, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "revision.restore", note_id, {"rev": rev})


@router.post("/notes/{note_id}/revisions/checkpoint")
async def checkpoint(note_id: str, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    """The editor closed: the editing session ends, so the next edit starts a new version."""
    note = await _note(db, p, note_id)
    rev = await core.close_session(db, note)
    await db.commit()
    return {"rev": rev}


# ---------------------------------------------------------------- links


class LinkIn(BaseModel):
    target_kind: str
    target_ref: dict[str, Any]


# ACT note.link @api
@router.post("/notes/{note_id}/links")
async def add_link(note_id: str, body: LinkIn, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    # A conversation id in target_ref arrives decoded (core/publicid_http.py walks the body).
    return await _write(db, p, "link.add", note_id, {"target_kind": body.target_kind,
                                                     "target_ref": dict(body.target_ref)})


# ACT note.unlink @api
@router.delete("/notes/{note_id}/links/{link_id}")
async def remove_link(note_id: str, link_id: int, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    return await _write(db, p, "link.remove", note_id, {"link_id": link_id})


# Keep the import list honest for tools that read it.
_ = (NoteItem, NoteSection)
