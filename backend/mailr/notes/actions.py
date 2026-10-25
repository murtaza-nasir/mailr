"""The note, checkitem, checksection and notelabel actions.

Every write takes the note as notes/access.py returned it, changes as little as it can (an item is
the unit of change), records live versions and the change events, keeps a revision, and returns
what it changed with an `undo`: the request that takes it back. Undos of item and section changes
go through `revert`, which applies a list of ops and returns their inverse (redo).

The routes in notes/routes.py are thin: they check access and the body, call one function here and
commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from sqlalchemy import any_, delete, func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import live, merge3
from mailr.i18n import ApiError
from mailr.models import ItemVersion, User
from mailr.notes import access, core, itemadd, learn, localonly, order, repeat
from mailr.notes.core import LIMITS
from mailr.notes.models import (
    CheckitemCheck,
    Note,
    NoteItem,
    NoteLabel,
    NoteLink,
    NoteRevision,
    NoteSection,
    NoteSectionViewer,
    NoteViewerState,
)

UNSET: Any = object()
REMEMBER = "remember"


def undo(method: str, path: str, body: Any, label: str) -> dict[str, Any]:
    return {"method": method, "path": path, "body": body, "label": label}


def revert_undo(note: Note, ops: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
    ops = [o for o in ops if o.get("ids") or o.get("items") or o.get("ranks") is not None or o.get("fields")
           or o.get("sections") or o.get("check_id") or o.get("saved") is not None]
    if not ops:
        return None
    return undo("POST", f"/notes/{note.pid}/revert", {"ops": ops}, label)


def too_many_items() -> HTTPException:
    return HTTPException(422, f"This list has {LIMITS['items']:,} items, the most a list can hold")


def need_list(note: Note) -> None:
    if note.kind != "checklist":
        raise HTTPException(409, "This note is not a list. Make it a list first.")


# ---------------------------------------------------------------- quantities


def norm_qty(q: Any) -> dict[str, Any] | None:
    """A quantity from a client: {amount, unit} with a known unit, or None."""
    if q is None or q == {}:
        return None
    if not isinstance(q, dict):
        raise HTTPException(422, "qty must be {amount, unit}")
    try:
        amount = Decimal(str(q.get("amount")))
    except (InvalidOperation, ValueError):
        raise HTTPException(422, "qty.amount must be a number") from None
    if amount <= 0 or amount >= Decimal("1e10"):
        raise HTTPException(422, "qty.amount must be above 0")
    unit = q.get("unit")
    if unit is not None:
        unit = itemadd.UNITS.get(str(unit).lower())
        if unit is None:
            raise HTTPException(422, f"unknown unit {q.get('unit')!r}")
    amt = itemadd.fmt_amount(amount)
    return {"amount": amt, "unit": unit, "text": str(q.get("text") or itemadd.qty_text({"amount": amt, "unit": unit}))[:40]}


def set_qty(item: NoteItem, q: dict[str, Any] | None) -> None:
    if q is None:
        item.qty_amount = item.qty_unit = item.qty_text = None
    else:
        item.qty_amount = Decimal(q["amount"])
        item.qty_unit = q.get("unit")
        item.qty_text = (q.get("text") or itemadd.qty_text(q))[:40]


# ---------------------------------------------------------------- item snapshots (undo)

SNAP_FIELDS = ("text", "key", "qty", "extra", "checked", "checked_at", "checked_by", "section_id", "order", "task_uid")


def snap(i: NoteItem, fields: tuple[str, ...] = SNAP_FIELDS) -> dict[str, Any]:
    out: dict[str, Any] = {"id": i.id}
    for f in fields:
        if f == "qty":
            q = core.qty_out(i)
            out["qty"] = q
        elif f == "checked_at":
            out["checked_at"] = core.iso(i.checked_at)
        else:
            out[f] = getattr(i, f)
    return out


def apply_snap(i: NoteItem, s: dict[str, Any], sections: set[str]) -> None:
    for f, v in s.items():
        if f == "id":
            continue
        if f == "qty":
            set_qty(i, norm_qty(v) if v else None)
        elif f == "checked_at":
            i.checked_at = datetime.fromisoformat(v) if v else None
        elif f == "section_id":
            i.section_id = v if v in sections else None
        elif f == "text":
            i.text = str(v)[:LIMITS["item"]]
            i.key = itemadd.item_key(i.text)
        elif f in ("key", "extra", "checked", "checked_by", "order", "task_uid"):
            if f == "key":
                continue  # follows the text
            setattr(i, f, v)


# ---------------------------------------------------------------- loading


async def get_item(db: AsyncSession, note: Note, item_id: str, *, deleted_ok: bool = False) -> NoteItem:
    iid = core.valid_uuid(item_id)
    item = await db.get(NoteItem, iid) if iid else None
    if item is None or item.note_id != note.id:
        raise HTTPException(404, "item not found")
    if item.deleted_at is not None and not deleted_ok:
        raise HTTPException(409, {"error": "deleted", "deleted": True, "id": item.id,
                                  "detail": "This item was deleted. Restore it to change it."})
    return item


async def get_section(db: AsyncSession, note: Note, sid: str | None, *, deleted_ok: bool = False) -> NoteSection:
    s = core.valid_uuid(sid)
    sec = await db.get(NoteSection, s) if s else None
    if sec is None or sec.note_id != note.id or (sec.deleted_at is not None and not deleted_ok):
        raise ApiError(404, "notes.section_not_found")
    return sec


async def _section_items(db: AsyncSession, note: Note, sid: str | None) -> list[NoteItem]:
    q = select(NoteItem).where(NoteItem.note_id == note.id, NoteItem.deleted_at.is_(None))
    q = q.where(NoteItem.section_id.is_(None) if sid is None else NoteItem.section_id == sid)
    return list((await db.execute(q.order_by(NoteItem.order, NoteItem.id))).scalars())


async def _renumber_items(db: AsyncSession, note: Note, sid: str | None) -> None:
    rows = await _section_items(db, note, sid)
    for i, k in zip(rows, order.spread(len(rows)), strict=False):
        i.order = k
    await db.flush()


async def place(db: AsyncSession, note: Note, sid: str | None, n: int, *, after: str | None = None,
                before: str | None = None, exclude: str | None = None) -> list[str]:
    """n order keys in a section: after the item `after`, before the item `before`, or at the end."""
    for attempt in range(2):
        rows = [i for i in await _section_items(db, note, sid) if i.id != exclude]
        keys = [i.order for i in rows]
        ids = [i.id for i in rows]
        if after in ids:
            k = ids.index(after)
            lo, hi = keys[k], keys[k + 1] if k + 1 < len(keys) else None
        elif before in ids:
            k = ids.index(before)
            lo, hi = keys[k - 1] if k > 0 else None, keys[k]
        else:
            lo, hi = (keys[-1] if keys else None), None
        out = [order.after(lo)] if n == 1 and hi is None else order.keys_between(lo, hi, n)
        if not any(order.too_long(k) for k in out):
            return out
        if attempt == 0:
            await _renumber_items(db, note, sid)
    raise HTTPException(409, "could not place the item; try again")


# ---------------------------------------------------------------- labels


async def labels_of(db: AsyncSession, user: User) -> list[NoteLabel]:
    return list((await db.execute(select(NoteLabel).where(NoteLabel.user_id == user.id)
                                  .order_by(NoteLabel.order, NoteLabel.id))).scalars())


def check_color(c: Any) -> str | None:
    if c in (None, "", "default"):
        return None
    c = str(c).lower()
    if c not in core.COLORS:
        raise HTTPException(422, f"unknown colour {c!r}; the colours are {', '.join(core.COLORS)}")
    return c


async def ensure_labels(db: AsyncSession, user: User, names: list[str]) -> tuple[list[str], list[str]]:
    """The labels named, as stored (case kept from the first spelling); unknown names are made.
    Returns (names, created)."""
    have = await labels_of(db, user)
    by = {lb.name.lower(): lb for lb in have}
    out: list[str] = []
    created: list[str] = []
    last = have[-1].order if have else None
    for n in names:
        n = " ".join(str(n).split())[:LIMITS["label"]]
        if not n:
            continue
        lb = by.get(n.lower())
        if lb is None:
            if len(by) >= LIMITS["labels"]:
                raise ApiError(422, "labels.limit", max=LIMITS["labels"])
            last = order.after(last)
            lb = NoteLabel(user_id=user.id, name=n, order=last)
            db.add(lb)
            by[n.lower()] = lb
            created.append(n)
        if lb.name not in out:
            out.append(lb.name)
    await db.flush()
    return out, created


# ---------------------------------------------------------------- notes


async def _first_order(db: AsyncSession, user: User) -> str | None:
    return (await db.execute(select(func.min(NoteViewerState.order)).where(
        NoteViewerState.user_id == user.id, NoteViewerState.item_kind == "note"))).scalar_one_or_none()


async def create_note(db: AsyncSession, user: User, *, kind: str = "text", title: str = "", body: str = "",
                      lines: str | None = None, items: list[dict[str, Any]] | None = None, color: Any = None,
                      labels: list[str] | None = None, shopping: bool = False, pinned: bool = False,
                      local_only: bool = False, source: str = "edit", group: str | None = None) -> dict[str, Any]:
    """ACT note.create: a text note or a checklist; the lines of a checklist go through the grammar.
    With `group`, the note belongs to that group of the user's ("New list in Household"): every
    member edits it, and only its creator or an admin deletes it."""
    if kind not in ("text", "checklist"):
        raise HTTPException(422, "kind must be text or checklist")
    check_text(title=title, body=body)
    from mailr.notes.sharing import check_group_create

    await check_group_create(db, user, group)
    from mailr.intel import locality

    # New items of a person with "Local models only for me" are local-only too, so other people's
    # Ambrose keeps them local after a share.
    if not local_only and await locality.household(db) == "per_user" and locality.user_wants(user):
        local_only = True
    note = Note(pid=core.new_pid(), owner_user_id=None if group else user.id, owner_group=group or None,
                created_by=user.id, kind=kind, title=title or "",
                body=(body or "") if kind == "text" else "", color=check_color(color), shopping=bool(shopping),
                suggest_usual=bool(shopping), local_only=bool(local_only),
                top_order_mode="learned" if shopping else "manual", updated_by=core.by_of(user))
    db.add(note)
    await db.flush()
    names, _ = await ensure_labels(db, user, labels or [])
    first = await _first_order(db, user)
    db.add(NoteViewerState(user_id=user.id, item_kind="note", item_id=note.id, labels=names, pinned=bool(pinned),
                           order=order.before(first) if first else order.FIRST))
    await db.flush()
    if kind == "checklist" and (lines or items):
        # A new list: "Costco: 2 milk" makes the Costco section, as the composer's hint says.
        cands, new_secs = await from_lines(db, note, lines or "", default=None, prefix_creates=True) if lines else ([], [])
        cands += await from_items(db, note, items or [], default=None)
        await add_items(db, user, note, cands, new_sections=new_secs, notify=False, revision=False)
    await core.commit_note(db, note)
    await core.snapshot(db, note, user, source)
    if labels:
        await core.notify_user(db, user.id, "labels")
    return {"note": await core.note_full(db, user, note),
            "undo": undo("DELETE", f"/notes/{note.pid}", None, "Note created")}


def check_text(*, title: str | None = None, body: str | None = None) -> None:
    if title is not None and len(title) > LIMITS["title"]:
        raise HTTPException(422, f"A title can be at most {LIMITS['title']} characters")
    if body is not None and len(body) > LIMITS["body"]:
        raise HTTPException(422, f"A note can be at most {LIMITS['body']:,} characters")


async def patch_note(db: AsyncSession, user: User, note: Note, changes: dict[str, Any], base_version: int | None
                     ) -> dict[str, Any]:
    """ACT note.edit, note.color: changed fields only. From an older version: the body merges by
    lines, the title conflicts when both changed it, the switches and the colour take the later
    write."""
    allowed = {"title", "body", "repeat", *core.LWW_FIELDS}
    changes = {k: v for k, v in changes.items() if k in allowed}
    if not changes:
        raise ApiError(400, "common.nothing_to_change")
    # The repeat of a list (notes/repeat.py) is set on its own: the later write wins.
    repeat_set = "repeat" in changes
    repeat_rule = changes.pop("repeat", None)
    if repeat_set and not changes:
        return await _set_repeat(db, user, note, repeat_rule)
    if "color" in changes:
        changes["color"] = check_color(changes["color"])
    if "top_order_mode" in changes and changes["top_order_mode"] not in ("learned", "manual"):
        raise HTTPException(422, "order mode must be learned or manual")
    for k in ("shopping", "checked_at_bottom", "suggest_usual", "local_only"):
        if k in changes:
            changes[k] = bool(changes[k])
    if "body" in changes and note.kind != "text":
        changes.pop("body")
    check_text(title=changes.get("title"), body=changes.get("body"))
    localonly.check(user, note, changes)  # only the owner turns it off
    uid = access.live_uid(note)
    await live.lock(db, uid, "note", note.pid, note)  # and read the note again under it
    now = core.note_live(note)
    head = await live.current(db, uid, "note", note.pid, now)
    merged = False
    if base_version is not None and int(base_version) < head:
        base = await live.at(db, uid, "note", note.pid, int(base_version))
        out = dict(changes)
        conflicts: list[str] = []
        texts: dict[str, Any] = {}
        if base is None:
            # The base version was pruned: no merge is possible, so a changed title or body that
            # differs from the stored copy is refused (the body used to be stored over it).
            conflicts = live.unknown_base_conflicts(changes, now, core.LWW_FIELDS)
        for f, val in (changes.items() if base is not None else ()):
            if merge3.same(base.get(f), now.get(f)) or f in core.LWW_FIELDS or merge3.same(val, now.get(f)):
                continue
            if f == "body":
                r = merge3.merge_text(base.get(f), val, now.get(f), "lines")
                if r.ok:
                    out[f] = r.text
                    continue
                texts[f] = r.conflicts
            conflicts.append(f)
        if conflicts:
            row = await live.latest(db, uid, "note", note.pid)
            raise live.LiveConflict("note", note.pid, base, int(base_version) if base is not None else None, now,
                                    head, changes, conflicts, texts, by=row.by if row is not None else None)
        changes, merged = out, True
    before = {k: getattr(note, k) for k in changes}
    shopping_on = changes.get("shopping") is True and not note.shopping
    for k, v in changes.items():
        setattr(note, k, v)
    if shopping_on and "suggest_usual" not in changes:
        # The Shopping list switch turns learned order and usual items on.
        before.setdefault("suggest_usual", note.suggest_usual)
        before.setdefault("top_order_mode", note.top_order_mode)
        note.suggest_usual = True
        note.top_order_mode = "learned"
    core.stamp(note, user)
    v = await core.commit_note(db, note)
    await core.snapshot(db, note, user)
    if {"title", "local_only"} & set(changes):  # reminders carry the title, and local-only hides it
        from mailr.notes.reminders import replan_note

        await replan_note(db, note)
    if "local_only" in changes and bool(before["local_only"]) != bool(note.local_only):
        await localonly.tell_owner(db, "note", note, user, bool(note.local_only))
    if repeat_set:
        before["repeat"] = note.repeat_rule
        await repeat.set_rule(db, user, note, repeat_rule)
    label = "Colour changed" if set(changes) == {"color"} else "Note changed"
    return {"note": await core.note_full(db, user, note), "merged": merged,
            "undo": undo("PATCH", f"/notes/{note.pid}", {**before, "base_version": v}, label)}


async def _set_repeat(db: AsyncSession, user: User, note: Note, rule: str | None) -> dict[str, Any]:
    """ACT note.repeat: a list repeats weekly on a day or every N days, or stops repeating."""
    before = note.repeat_rule
    await repeat.set_rule(db, user, note, rule)
    core.stamp(note, user)
    v = await core.commit_note(db, note)
    await core.touch(db, note)
    label = "Repeat off" if not note.repeat_rule else "Repeat set"
    return {"note": await core.note_full(db, user, note), "merged": False,
            "undo": undo("PATCH", f"/notes/{note.pid}", {"repeat": before, "base_version": v}, label)}


async def trash(db: AsyncSession, user: User, note: Note) -> dict[str, Any]:
    """ACT note.delete: to Trash for 30 days."""
    if note.deleted_at is None:
        note.deleted_at = datetime.now(UTC)
        note.deleted_by = user.id
        await core.touch(db, note, deleted=True)
    return {"ok": True, "undo": undo("POST", f"/notes/{note.pid}/restore", None, "Moved to Trash")}


async def restore(db: AsyncSession, user: User, note: Note) -> dict[str, Any]:
    """ACT note.restore: back from Trash with its items, sections, labels and links."""
    if note.deleted_at is not None:
        note.deleted_at = None
        note.deleted_by = None
        await core.touch(db, note)
    return {"note": await core.note_full(db, user, note),
            "undo": undo("DELETE", f"/notes/{note.pid}", None, "Restored")}


async def purge(db: AsyncSession, user: User, note: Note) -> dict[str, Any]:
    """ACT note.purge: delete forever, from Trash only. No undo."""
    if note.deleted_at is None:
        raise HTTPException(409, "Move the note to Trash first")
    await _purge(db, note)
    return {"ok": True, "undo": None}


async def _purge(db: AsyncSession, note: Note) -> None:
    uid = access.live_uid(note)
    ids = [r[0] for r in (await db.execute(select(NoteItem.id).where(NoteItem.note_id == note.id))).all()]
    aud = await access.audience(db, note)
    await db.execute(delete(ItemVersion).where(ItemVersion.user_id == uid, ItemVersion.kind == "note",
                                               ItemVersion.item_id == note.pid))
    if ids:
        await db.execute(delete(ItemVersion).where(ItemVersion.user_id == uid, ItemVersion.kind == "checkitem",
                                                   ItemVersion.item_id.in_(ids)))
    await db.execute(delete(NoteViewerState).where(NoteViewerState.item_kind == "note",
                                                   NoteViewerState.item_id == note.id))
    from mailr.notes.reminders import drop_note
    from mailr.notes.sharing import drop_item

    await drop_item(db, "note", note.id)
    await drop_note(db, note)
    pid = note.pid
    await db.delete(note)
    await db.flush()
    from mailr.core.events import notify

    for u in aud:
        await notify(db, u, {"type": "item", "kind": "note", "id": pid, "note": pid, "version": None, "deleted": True,
                             "purged": True, "by": live.who(), "origin": live.origin_var.get()})


async def empty_trash(db: AsyncSession, user: User) -> dict[str, Any]:
    """ACT note.purge ("Empty Trash")."""
    rows = (await db.execute(select(Note).where(access.visible(user), Note.deleted_at.is_not(None)))).scalars().all()
    n = 0
    for note in rows:
        if access.can_delete(user, note):
            await _purge(db, note)
            n += 1
    # Trash holds tables too (tables/actions.py).
    from mailr.tables import access as taccess
    from mailr.tables.actions import purge_now
    from mailr.tables.models import DataTable

    for t in (await db.execute(select(DataTable).where(taccess.visible(user), DataTable.deleted_at.is_not(None)))
              ).scalars().all():
        if taccess.can_delete(user, t):
            await purge_now(db, t)
            n += 1
    return {"purged": n, "undo": None}


async def duplicate(db: AsyncSession, user: User, note: Note) -> dict[str, Any]:
    """ACT note.duplicate: same kind, text, items, sections, colour and labels; no links or history."""
    st = await core.state_of(db, user, note)
    copy = Note(pid=core.new_pid(), owner_user_id=user.id, created_by=user.id, kind=note.kind, title=note.title,
                body=note.body, color=note.color, shopping=note.shopping, checked_at_bottom=note.checked_at_bottom,
                suggest_usual=note.suggest_usual, local_only=note.local_only, top_order_mode=note.top_order_mode,
                updated_by=core.by_of(user))
    db.add(copy)
    await db.flush()
    first = await _first_order(db, user)
    db.add(NoteViewerState(user_id=user.id, item_kind="note", item_id=copy.id, labels=list(st.labels or []) if st else [],
                           order=order.before(first) if first else order.FIRST))
    secmap: dict[str, str] = {}
    for s in await core.sections_of(db, note):
        nid = core.uuid7()
        secmap[s.id] = nid
        db.add(NoteSection(id=nid, note_id=copy.id, name=s.name, order=s.order, order_mode=s.order_mode))
    await db.flush()
    for i in await core.items_of(db, note):
        it = NoteItem(id=core.uuid7(), note_id=copy.id, section_id=secmap.get(i.section_id), text=i.text, key=i.key,
                      qty_amount=i.qty_amount, qty_unit=i.qty_unit, qty_text=i.qty_text, extra=i.extra,
                      checked=i.checked, checked_at=i.checked_at, checked_by=i.checked_by, order=i.order,
                      added_by=user.id, updated_by=core.by_of(user))
        db.add(it)
    await db.flush()
    for i in await core.items_of(db, copy):
        await core.commit_item(db, copy, i)
    await core.commit_note(db, copy)
    await core.snapshot(db, copy, user)
    return {"note": await core.note_full(db, user, copy),
            "undo": undo("DELETE", f"/notes/{copy.pid}", None, "Copy created")}


async def convert(db: AsyncSession, user: User, note: Note, to: str, base_version: int | None, *,
                  restore_items: list[str] | None = None, restore_sections: list[str] | None = None,
                  restore_body: str | None = None, drop: list[str] | None = None) -> dict[str, Any]:
    """ACT note.to_checklist, note.to_text: text lines become items through the grammar (`Name:`
    and `## Name` lines become sections); items become `- [ ]` lines and sections `Name:` lines.
    Switching back (the undo) brings back the same items, with their ids, history and order."""
    if to not in ("checklist", "text"):
        raise HTTPException(422, "to must be checklist or text")
    uid = access.live_uid(note)
    await live.lock(db, uid, "note", note.pid, note)  # and read the note again under it
    head = await core.note_version(db, note)
    if base_version is not None and int(base_version) < head:
        raise HTTPException(409, {"error": "stale", "detail": "This note changed since you opened it. Try again.",
                                  "version": head})
    if note.kind == to:
        return {"note": await core.note_full(db, user, note), "undo": None}
    now = datetime.now(UTC)
    if to == "checklist":
        old_body = note.body or ""
        created: list[str] = []
        if restore_items is not None or restore_sections is not None:
            for s in (await db.execute(select(NoteSection).where(NoteSection.note_id == note.id,
                                                                 NoteSection.id.in_(restore_sections or [])))).scalars():
                s.deleted_at = None
            for i in (await db.execute(select(NoteItem).where(NoteItem.note_id == note.id,
                                                              NoteItem.id.in_(restore_items or [])))).scalars():
                i.deleted_at = None
        note.kind = "checklist"
        note.body = ""
        await db.flush()
        if restore_items is None and old_body.strip():
            cands, new_secs = await from_lines(db, note, old_body, default=None)
            res = await add_items(db, user, note, cands, new_sections=new_secs, force=True, notify=False,
                                  revision=False)
            created = [i["id"] for i in res["items"]]
        back = {"to": "text", "restore_body": old_body, "drop": created}
        label = "Made a list"
    else:
        items = await core.items_of(db, note)
        secs = await core.sections_of(db, note)
        ranks = await learn.ranks_of(db, note)
        lines: list[str] = []
        ordered = core.display(note, secs, items, ranks, fold=False)
        cur: Any = UNSET
        names = {s.id: s.name for s in secs}
        for i in ordered:
            if i.section_id != cur:
                if i.section_id is not None:
                    lines.append(f"{names[i.section_id]}:")
                cur = i.section_id
            lines.append(("- [x] " if i.checked else "- [ ] ") + itemadd.line_for(i.text, core.qty_out(i)))
        empty_secs = [s for s in secs if not any(i.section_id == s.id for i in items)]
        for s in empty_secs:
            lines.append(f"{s.name}:")
        note.body = restore_body if restore_body is not None else "\n".join(lines)
        check_text(body=note.body)
        note.kind = "text"
        for i in items:
            i.deleted_at = now
        for s in secs:
            s.deleted_at = now
        await db.flush()
        await drop_items(db, note, [x for x in (core.valid_uuid(i) for i in drop or []) if x])
        items = [i for i in items if i.id not in set(drop or [])]
        back = {"to": "checklist", "restore_items": [i.id for i in items], "restore_sections": [s.id for s in secs]}
        label = "Made text"
    core.stamp(note, user)
    v = await core.commit_note(db, note)
    await core.snapshot(db, note, user, "convert")
    from mailr.notes.reminders import replan_note

    await replan_note(db, note)
    await core.touch(db, note)
    return {"note": await core.note_full(db, user, note),
            "undo": undo("POST", f"/notes/{note.pid}/convert", {**back, "base_version": v}, label)}


async def drop_items(db: AsyncSession, note: Note, ids: list[str]) -> None:
    """The items a convert made, removed again when the convert is undone."""
    if ids:
        await db.execute(delete(NoteItem).where(NoteItem.note_id == note.id, NoteItem.id.in_(ids)))


async def set_state(db: AsyncSession, user: User, note: Note, body: dict[str, Any]) -> dict[str, Any]:
    """ACT note.pin, note.archive, note.label, note.reorder: the viewer's own view of the note."""
    st = await core.state_of(db, user, note, create=True)
    before: dict[str, Any] = {}
    created: list[str] = []
    label = "Changed"
    if "pinned" in body and body["pinned"] is not None:
        before["pinned"] = st.pinned
        st.pinned = bool(body["pinned"])
        label = "Pinned" if st.pinned else "Unpinned"
        if st.pinned and st.archived:  # pinning an archived note unarchives it
            before["archived"] = True
            st.archived = False
    if "archived" in body and body["archived"] is not None:
        before.setdefault("archived", st.archived)
        st.archived = bool(body["archived"])
        label = "Archived" if st.archived else "Unarchived"
        if st.archived and st.pinned:
            before.setdefault("pinned", True)
            st.pinned = False
    if body.get("labels") is not None or body.get("add_labels") or body.get("remove_labels"):
        before["labels"] = list(st.labels or [])
        cur = list(st.labels or [])
        if body.get("labels") is not None:
            cur, created = await ensure_labels(db, user, list(body["labels"]))
        else:
            add, created = await ensure_labels(db, user, list(body.get("add_labels") or []))
            rm = {str(x).lower() for x in body.get("remove_labels") or []}
            cur = [x for x in cur if x.lower() not in rm] + [x for x in add if x not in cur]
        st.labels = cur
        label = "Labels changed"
    if body.get("before") or body.get("after"):
        before["order"] = st.order
        from mailr.tables import grid  # the grid holds notes and tables (tables/grid.py)

        st.order = await grid.key(db, user, ("note", note.id), before=body.get("before"), after=body.get("after"))
        label = "Moved"
    if body.get("seen_version") is not None:
        st.seen_version = int(body["seen_version"])
    if body.get("seen"):  # opened now: the "changed since you looked" dot goes
        st.seen_at = datetime.now(UTC)
    if "top_collapsed" in body and body["top_collapsed"] is not None:
        st.top_collapsed = bool(body["top_collapsed"])
    st.updated_at = datetime.now(UTC)
    await db.flush()
    await core.notify_user(db, user.id, "state")
    if created:
        await core.notify_user(db, user.id, "labels")
    back = dict(before)
    if "order" in back:
        back = {k: v for k, v in back.items() if k != "order"}
        back["order_key"] = before["order"]
    return {"state": core.state_out(st), "created_labels": created,
            "undo": undo("PUT", f"/notes/{note.pid}/state", back, label) if before else None}


# ---------------------------------------------------------------- adding items


@dataclass
class Cand:
    text: str
    qty: dict[str, Any] | None = None
    section: Any = None  # a section id, None (top group) or REMEMBER
    checked: bool = False
    extra: str | None = None
    id: str | None = None
    force: bool = False


async def from_lines(db: AsyncSession, note: Note, lines: str, *, default: Any, keep: list[str] | None = None,
                     prefix_creates: bool = False) -> tuple[list[Cand], list[str]]:
    """Lines through the grammar (list mode): items and the sections they make. `prefix_creates`: an
    unknown `Name:` prefix makes that section too (a new list)."""
    secs = await core.sections_of(db, note)
    res = itemadd.parse(lines, "list", sections=[s.name for s in secs], keep=keep, prefix_creates=prefix_creates)
    by = {s.name: s.id for s in secs}
    cands = [Cand(text=i.text, qty=i.qty and norm_qty(i.qty), checked=i.checked,
                  section=by.get(i.section, i.section) if i.section else default) for i in res.items]
    return cands, res.new_sections


async def from_items(db: AsyncSession, note: Note, items: list[dict[str, Any]], *, default: Any) -> list[Cand]:
    secs = {s.id for s in await core.sections_of(db, note)}
    out = []
    for it in items:
        text = " ".join(str(it.get("text") or "").split())
        if not text:
            continue
        if len(text) > LIMITS["item"]:
            raise HTTPException(422, f"An item can be at most {LIMITS['item']:,} characters")
        sid: Any = default
        if "section_id" in it:
            sid = it["section_id"] if it["section_id"] in secs else None
        cid = core.valid_uuid(it.get("id")) if it.get("id") else None
        if it.get("id") and not cid:
            raise HTTPException(422, "an item id must be a UUID")
        out.append(Cand(text=text, qty=norm_qty(it.get("qty")), section=sid, checked=bool(it.get("checked")),
                        extra=(str(it["extra"])[:LIMITS["item"]] if it.get("extra") else None), id=cid))
    return out


async def add_items(db: AsyncSession, user: User, note: Note, cands: list[Cand], *, new_sections: list[str] = (),
                    after: str | None = None, force: bool = False, notify: bool = True, revision: bool = True,
                    label: str | None = None) -> dict[str, Any]:
    """ACT checkitem.add, note.add_items, checkitem.usual_add: add items; duplicates merge:
    an unchecked item with the same key takes the quantity (same unit),
    a checked one is unchecked with the new quantity. `force` adds them anyway."""
    need_list(note)
    secs = await core.sections_of(db, note)
    made_secs: list[NoteSection] = []
    by_name = {s.name: s.id for s in secs}
    for name in new_sections:
        if name in by_name:
            continue
        sec = await _new_section(db, note, name, secs + made_secs)
        made_secs.append(sec)
        by_name[name] = sec.id
    secset = {s.id for s in secs + made_secs}
    now = datetime.now(UTC)
    by = core.by_of(user)
    existing = await core.items_of(db, note)
    by_key: dict[str, list[NoteItem]] = {}
    for i in existing:
        by_key.setdefault(i.key, []).append(i)
    count = len(existing)
    created: list[NoteItem] = []
    changed: dict[str, NoteItem] = {}
    befores: dict[str, dict[str, Any]] = {}
    merged: list[dict[str, Any]] = []
    rechecked: list[str] = []
    duplicates: list[dict[str, Any]] = []
    names = {s.id: s.name for s in secs + made_secs}
    pending: dict[str | None, list[NoteItem]] = {}
    for c in cands:
        # A client id that is already here: the add was sent twice.
        if c.id:
            prev = await db.get(NoteItem, c.id)
            if prev is not None:
                if prev.note_id != note.id:
                    raise HTTPException(409, "that item id belongs to another list")
                changed.setdefault(prev.id, prev)
                continue
        sid = c.section
        if sid == REMEMBER:
            sid = await learn.remembered_section(db, note, itemadd.item_key(c.text))
        elif isinstance(sid, str) and sid not in secset:
            sid = by_name.get(sid)  # a section name from the grammar
        key = itemadd.item_key(c.text)
        same = by_key.get(key) or []
        if same and not (force or c.force):
            if c.checked:
                continue  # a checked duplicate from a paste: it is on the list already
            live_rows = [i for i in same if not i.checked]
            if live_rows:
                e = live_rows[0]
                if (e.qty_unit or None) == ((c.qty or {}).get("unit") or None):
                    befores.setdefault(e.id, snap(e, ("qty",)))
                    a = (e.qty_amount if e.qty_amount is not None else Decimal(1)) + \
                        (Decimal(c.qty["amount"]) if c.qty else Decimal(1))
                    set_qty(e, {"amount": itemadd.fmt_amount(a), "unit": e.qty_unit})
                    e.updated_by, e.updated_at = by, now
                    changed[e.id] = e
                    merged.append({"id": e.id, "text": e.text, "qty": core.qty_out(e)})
                else:
                    duplicates.append({"text": c.text, "qty": c.qty, "section": names.get(e.section_id),
                                       "section_id": sid})
                continue
            e = same[0]
            befores.setdefault(e.id, snap(e, ("qty", "checked", "checked_at", "checked_by")))
            e.checked, e.checked_at, e.checked_by = False, now, None
            if c.qty is not None:
                set_qty(e, c.qty)
            e.updated_by, e.updated_at = by, now
            changed[e.id] = e
            rechecked.append(e.id)
            continue
        if count >= LIMITS["items"]:
            raise too_many_items()
        count += 1
        it = NoteItem(id=c.id or core.uuid7(), note_id=note.id, section_id=sid, text=c.text[:LIMITS["item"]], key=key,
                      extra=c.extra, checked=c.checked, checked_at=now if c.checked else None,
                      checked_server_at=now if c.checked else None, checked_by=user.id if c.checked else None,
                      order="", added_by=user.id, updated_by=by)
        set_qty(it, c.qty)
        pending.setdefault(sid, []).append(it)
        by_key.setdefault(key, []).append(it)
        created.append(it)
    # Order keys: after `after` in its section, else at the end of each section.
    anchor = await db.get(NoteItem, core.valid_uuid(after)) if after and core.valid_uuid(after) else None
    for sid, rows in pending.items():
        keys = await place(db, note, sid, len(rows),
                           after=anchor.id if anchor is not None and anchor.section_id == sid else None)
        for it, k in zip(rows, keys, strict=False):
            it.order = k
            db.add(it)
        await db.flush()
    aud = await core.audience(db, note)
    vers: dict[str, int] = {}
    for it in created + list(changed.values()):
        vers[it.id] = await core.commit_item(db, note, it, aud)
    if created or changed or made_secs:
        core.stamp(note, user)
    if made_secs and notify:
        await core.touch(db, note)
    if revision and (created or changed or made_secs):
        await core.snapshot(db, note, user)
    nm = await core.names(db, {user.id})
    ops: list[dict[str, Any]] = [{"op": "delete_items", "ids": [i.id for i in created]}]
    if befores:
        ops.append({"op": "set_items", "items": list(befores.values())})
    if made_secs:
        ops.append({"op": "delete_sections", "ids": [s.id for s in made_secs]})
    n = len(created) + len(merged) + len(rechecked)
    lbl = label or (f"Added {n} item{'s' if n != 1 else ''}" if n != 1 or not created else f"Added {created[0].text}")
    return {"items": [core.item_out(i, nm, vers.get(i.id)) for i in created + list(changed.values())],
            "merged": merged, "rechecked": rechecked, "duplicates": duplicates,
            "new_sections": [{"id": s.id, "name": s.name} for s in made_secs],
            "undo": revert_undo(note, ops, lbl) if n or made_secs else None}


# ---------------------------------------------------------------- one item


async def _parse_device_at(db: AsyncSession, user: User, v: Any) -> datetime:
    """When the device saw the tap; a time without an offset is in the user's zone."""
    if not v:
        return datetime.now(UTC)
    try:
        d = datetime.fromisoformat(str(v))
    except ValueError:
        raise HTTPException(422, "device_at must be an ISO 8601 time") from None
    from mailr.calendar import tz as tzmod

    return await tzmod.aware(db, user, d)


async def patch_item(db: AsyncSession, user: User, note: Note, item_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """ACT checkitem.check, checkitem.uncheck, checkitem.edit. A check carries the device time of
    the tap; the later tap wins. Text, quantity and the extra line conflict on the item when both
    sides changed the same field differently since `base_version`."""
    need_list(note)
    item = await get_item(db, note, item_id)
    uid = access.live_uid(note)
    await live.lock(db, uid, "checkitem", item.id, item)  # and read the item again under it
    now = datetime.now(UTC)
    by = core.by_of(user)
    changes: dict[str, Any] = {}
    if "text" in body and body["text"] is not None:
        text = " ".join(str(body["text"]).split())
        if not text:
            raise HTTPException(422, "An item needs some text")
        if len(text) > LIMITS["item"]:
            raise HTTPException(422, f"An item can be at most {LIMITS['item']:,} characters")
        if body.get("parse"):
            p = itemadd.parse(text, "edit", keep=body.get("keep"))
            if p.items:
                text = p.items[0].text
                if p.items[0].qty is not None:
                    changes["qty"] = norm_qty(p.items[0].qty)
        changes["text"] = text
    if "qty" in body:
        changes["qty"] = norm_qty(body["qty"])
    if "extra" in body:
        changes["extra"] = (" ".join(str(body["extra"]).split())[:LIMITS["item"]] or None) if body["extra"] else None
    base_version = body.get("base_version")
    cur = core.item_live(item)
    if base_version is not None and changes:
        head = await live.current(db, uid, "checkitem", item.id, cur)
        if int(base_version) < head:
            base = await live.at(db, uid, "checkitem", item.id, int(base_version))
            conflicts = []
            for f, val in changes.items():
                new = {"amount": val["amount"], "unit": val["unit"]} if f == "qty" and val else val
                if base is not None and merge3.same(base.get(f), cur.get(f)):
                    continue
                if merge3.same(new, cur.get(f)):
                    continue
                conflicts.append(f)
            if conflicts:
                row = await live.latest(db, uid, "checkitem", item.id)
                raise live.LiveConflict("checkitem", item.id, base, int(base_version), cur, head,
                                        {**cur, **changes}, conflicts, by=row.by if row is not None else None)
    ops: list[dict[str, Any]] = []
    label = "Item changed"
    result_undo = None
    if changes:
        ops.append({"op": "set_items", "items": [snap(item, tuple(changes))]})
        if "text" in changes:
            item.text = changes["text"]
            item.key = itemadd.item_key(item.text)
        if "qty" in changes:
            set_qty(item, changes["qty"])
        if "extra" in changes:
            item.extra = changes["extra"]
    ignored = False
    if "checked" in body and body["checked"] is not None:
        want = bool(body["checked"])
        dev = await _parse_device_at(db, user, body.get("device_at"))
        if want != bool(item.checked):
            if item.checked_at is not None and dev < item.checked_at and not body.get("undo"):
                ignored = True  # an older tap: the later one wins (an undo always applies)
            else:
                before = snap(item, ("checked", "checked_at", "checked_by"))
                item.checked = want
                item.checked_at = dev
                item.checked_server_at = now
                item.checked_by = user.id if want else None
                if want:
                    await learn.log_check(db, note, item, user.id, dev)
                    await _queue_learn(db, note, item.section_id, dev)
                    label = f"Checked {item.text}"
                    result_undo = undo("PATCH", f"/notes/{note.pid}/items/{item.id}",
                                       {"checked": False, "undo": True}, label)
                else:
                    cid = await learn.unlog_check(db, note, item, dev)
                    ops.append({"op": "set_items", "items": [before]})
                    if cid:
                        ops.append({"op": "relog", "check_id": cid})
                    label = f"Unchecked {item.text}"
    if not changes and "checked" not in body:
        raise ApiError(400, "common.nothing_to_change")
    item.updated_by, item.updated_at = by, now
    v = await core.commit_item(db, note, item)
    core.stamp(note, user)
    await core.snapshot(db, note, user)
    nm = await core.names(db, {item.added_by, item.checked_by})
    if result_undo is None:
        result_undo = revert_undo(note, ops, label)
    return {"item": core.item_out(item, nm, v), "ignored": ignored, "undo": result_undo}


async def _queue_learn(db: AsyncSession, note: Note, sid: str | None, dev: datetime) -> None:
    """Learn the trip once it has closed (the worker; reads also learn closed trips)."""
    from mailr.core.jobs import enqueue

    when = max(dev, datetime.now(UTC)) + learn.TRIP_GAP
    await enqueue(db, "notes.learn", {"note_id": note.id, "section_id": sid}, user_id=access.live_uid(note) or None,
                  run_at=when, dedupe_key=f"notes.learn:{note.id}:{sid or ''}")


async def delete_item(db: AsyncSession, user: User, note: Note, item_id: str) -> dict[str, Any]:
    """ACT checkitem.delete: the item leaves the list; Version history keeps it 30 days."""
    need_list(note)
    item = await get_item(db, note, item_id)
    item.deleted_at = datetime.now(UTC)
    item.updated_by = core.by_of(user)
    await db.flush()
    await _item_gone(db, note, [item.id])
    core.stamp(note, user)
    await core.snapshot(db, note, user)
    return {"ok": True, "undo": revert_undo(note, [{"op": "restore_items", "ids": [item.id]}], f"Deleted {item.text}")}


async def _item_gone(db: AsyncSession, note: Note, ids: list[str]) -> None:
    from mailr.core.events import notify

    aud = await core.audience(db, note)
    for iid in ids:
        ev = {"type": "item", "kind": "checkitem", "id": iid, "note": note.pid, "version": None, "deleted": True,
              "by": live.who(), "origin": live.origin_var.get()}
        for u in aud:
            await notify(db, u, ev)


async def _display_section(db: AsyncSession, note: Note, sid: str | None, exclude: str) -> list[NoteItem]:
    """The unchecked items of one section in display order, without `exclude`."""
    secs = await core.sections_of(db, note)
    rows = [i for i in await _section_items(db, note, sid) if i.id != exclude and not i.checked]
    return core.display(note, secs, rows, await learn.ranks_of(db, note))


async def move_item(db: AsyncSession, user: User, note: Note, item_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """ACT checkitem.move: before or after an item, into a section, or a step (the keys). In a
    learned section a move by hand sets the item's rank between its new neighbours."""
    need_list(note)
    item = await get_item(db, note, item_id)
    secs = await core.sections_of(db, note)
    secids = [None] + [s.id for s in secs]
    old = snap(item, ("section_id", "order"))
    before_id, after_id = body.get("before"), body.get("after")
    target: Any = item.section_id
    direction = body.get("direction")
    if direction in ("up", "down"):
        rows = await _display_section(db, note, item.section_id, item.id)
        cur = await _display_index(db, note, item)
        if direction == "up":
            if cur <= 0:
                return {"item": core.item_out(item, {}, None), "undo": None}
            before_id = rows[cur - 1].id
        else:
            if cur >= len(rows):
                return {"item": core.item_out(item, {}, None), "undo": None}
            after_id = rows[cur].id
    elif direction in ("prev_section", "next_section"):
        k = secids.index(item.section_id) if item.section_id in secids else 0
        k += -1 if direction == "prev_section" else 1
        if k < 0 or k >= len(secids):
            return {"item": core.item_out(item, {}, None), "undo": None}
        target = secids[k]
    elif direction:
        raise HTTPException(422, "direction must be up, down, prev_section or next_section")
    anchor = None
    if before_id or after_id:
        anchor = await get_item(db, note, before_id or after_id)
        target = anchor.section_id
    elif "section_id" in body and not direction:
        target = body["section_id"]
        if target is not None and target not in secids:
            raise ApiError(404, "notes.section_not_found")
    ops: list[dict[str, Any]] = [{"op": "set_items", "items": [old]}]
    # Learned order: a move by hand within a learned section sets a rank and counts as a trip.
    sec = next((s for s in secs if s.id == target), None)
    if target == item.section_id and core.group_mode(note, sec) == "learned" and not item.checked:
        rows = await _display_section(db, note, target, item.id)
        ranks = await learn.ranks_of(db, note)
        skey = learn.section_key(target)
        ids = [r.id for r in rows]
        if anchor is not None and anchor.id in ids:
            k = ids.index(anchor.id)
            prev_i, next_i = (rows[k - 1] if k > 0 else None, rows[k]) if before_id else (rows[k], rows[k + 1] if k + 1 < len(rows) else None)
        else:
            prev_i, next_i = (rows[-1] if rows else None), None
        rp = ranks.get((skey, prev_i.key))[0] if prev_i is not None and (skey, prev_i.key) in ranks else None
        rn = ranks.get((skey, next_i.key))[0] if next_i is not None and (skey, next_i.key) in ranks else None
        if prev_i is not None and rp is None and next_i is None:
            rp = max((v[0] for (s, _k), v in ranks.items() if s == skey), default=None)
        old_rank = await learn.drag(db, note, target, item.key, rp, rn)
        ops.append({"op": "set_ranks", "section_id": target, "ranks": {item.key: list(old_rank) if old_rank else None}})
    keys = await place(db, note, target, 1, after=after_id if after_id else None, before=before_id if before_id else None,
                       exclude=item.id)
    item.section_id = target
    item.order = keys[0]
    item.updated_by, item.updated_at = core.by_of(user), datetime.now(UTC)
    await db.flush()
    v = await core.commit_item(db, note, item)
    await core.touch(db, note)
    core.stamp(note, user)
    await core.snapshot(db, note, user)
    nm = await core.names(db, {item.added_by})
    return {"item": core.item_out(item, nm, v), "undo": revert_undo(note, ops, f"Moved {item.text}")}


async def _display_index(db: AsyncSession, note: Note, item: NoteItem) -> int:
    secs = await core.sections_of(db, note)
    rows = [i for i in await _section_items(db, note, item.section_id) if not i.checked]
    ordered = core.display(note, secs, rows, await learn.ranks_of(db, note))
    ids = [i.id for i in ordered]
    return ids.index(item.id) if item.id in ids else len(ids)


async def clear_checked(db: AsyncSession, user: User, note: Note) -> dict[str, Any]:
    """ACT note.clear_checked: the checked items leave the list (one undo)."""
    need_list(note)
    rows = [i for i in await core.items_of(db, note) if i.checked]
    now = datetime.now(UTC)
    for i in rows:
        i.deleted_at = now
    await db.flush()
    await _item_gone(db, note, [i.id for i in rows])
    if rows:
        core.stamp(note, user)
        await core.snapshot(db, note, user)
    n = len(rows)
    return {"count": n, "undo": revert_undo(note, [{"op": "restore_items", "ids": [i.id for i in rows]}],
                                            f"Cleared {n} checked item{'s' if n != 1 else ''}")}


async def uncheck_all(db: AsyncSession, user: User, note: Note) -> dict[str, Any]:
    """ACT note.uncheck_all: "Start over" (one undo)."""
    need_list(note)
    rows = [i for i in await core.items_of(db, note) if i.checked]
    befores = [snap(i, ("checked", "checked_at", "checked_by")) for i in rows]
    now = datetime.now(UTC)
    aud = await core.audience(db, note)
    for i in rows:
        i.checked, i.checked_at, i.checked_by = False, now, None
        i.updated_by, i.updated_at = core.by_of(user), now
        await core.commit_item(db, note, i, aud)
    if rows:
        core.stamp(note, user)
        await core.snapshot(db, note, user)
    n = len(rows)
    return {"count": n, "undo": revert_undo(note, [{"op": "set_items", "items": befores}],
                                            f"Unchecked {n} item{'s' if n != 1 else ''}")}


# ---------------------------------------------------------------- sections


async def _new_section(db: AsyncSession, note: Note, name: str, secs: list[NoteSection], *,
                       after: str | None = None) -> NoteSection:
    name = " ".join(name.split())[:LIMITS["section"]]
    if not name:
        raise ApiError(422, "notes.section_name_required")
    if len(secs) >= LIMITS["sections"]:
        raise HTTPException(422, f"This list has {LIMITS['sections']} sections, the most a list can hold")
    fold = itemadd._fold(name)
    if any(itemadd._fold(s.name) == fold for s in secs):
        raise ApiError(409, "notes.section_exists", name=name)
    ordered = sorted(secs, key=lambda s: (s.order, s.id))
    ids = [s.id for s in ordered]
    if after in ids:
        k = ids.index(after)
        key = order.between(ordered[k].order, ordered[k + 1].order if k + 1 < len(ordered) else None)
    else:
        key = order.after(ordered[-1].order if ordered else None)
    sec = NoteSection(id=core.uuid7(), note_id=note.id, name=name, order=key,
                      order_mode="learned" if note.shopping else "manual")
    db.add(sec)
    await db.flush()
    return sec


async def create_section(db: AsyncSession, user: User, note: Note, name: str, after: str | None) -> dict[str, Any]:
    """ACT checksection.create: at the end, or after a section."""
    need_list(note)
    sec = await _new_section(db, note, name, await core.sections_of(db, note), after=after)
    await core.touch(db, note)
    core.stamp(note, user)
    await core.snapshot(db, note, user)
    return {"section": core.section_out(sec, False, 0),
            "undo": revert_undo(note, [{"op": "delete_sections", "ids": [sec.id]}], f"Added section {sec.name}")}


async def patch_section(db: AsyncSession, user: User, note: Note, sid: str, body: dict[str, Any]) -> dict[str, Any]:
    """ACT checksection.rename, checksection.reorder, checksection.order_mode; the viewer's
    collapse; "Reset learned order"."""
    need_list(note)
    top = sid == "top"
    sec = None if top else await get_section(db, note, sid)
    ops: list[dict[str, Any]] = []
    label = "Section changed"
    if top:
        if body.get("order_mode") in ("learned", "manual"):
            ops.append({"op": "set_note", "fields": {"top_order_mode": note.top_order_mode}})
            note.top_order_mode = body["order_mode"]
            label = "Order changed"
        if body.get("collapsed") is not None:
            st = await core.state_of(db, user, note, create=True)
            st.top_collapsed = bool(body["collapsed"])
        if body.get("reset_learned"):
            saved = await learn.reset(db, note, None)
            ops.append({"op": "unreset", "section_id": None, "saved": saved})
            label = "Learned order reset"
        await core.commit_note(db, note)
        await core.touch(db, note)
        return {"section": {"id": "top", "order_mode": note.top_order_mode, "trips": note.top_trips or 0},
                "undo": revert_undo(note, ops, label)}
    if body.get("name") is not None:
        name = " ".join(str(body["name"]).split())[:LIMITS["section"]]
        if not name:
            raise ApiError(422, "notes.section_name_required")
        if body.get("base_name") is not None and body["base_name"] != sec.name and name != sec.name:
            raise HTTPException(409, {"error": "conflict", "conflict": True, "kind": "checksection", "id": sec.id,
                                      "fields": ["name"], "theirs": {"name": sec.name},
                                      "detail": f"This section was renamed to {sec.name} meanwhile."})
        others = [s for s in await core.sections_of(db, note) if s.id != sec.id]
        if any(itemadd._fold(s.name) == itemadd._fold(name) for s in others):
            raise ApiError(409, "notes.section_exists", name=name)
        ops.append({"op": "set_sections", "sections": [{"id": sec.id, "name": sec.name}]})
        sec.name = name
        label = f"Renamed to {name}"
    if body.get("order_mode") is not None:
        if body["order_mode"] not in ("learned", "manual"):
            raise HTTPException(422, "order mode must be learned or manual")
        ops.append({"op": "set_sections", "sections": [{"id": sec.id, "order_mode": sec.order_mode}]})
        sec.order_mode = body["order_mode"]
        label = "Order changed"
    if body.get("before") or body.get("after") or body.get("direction"):
        secs = [s for s in await core.sections_of(db, note) if s.id != sec.id]
        ids = [s.id for s in secs]
        before_id, after_id = body.get("before"), body.get("after")
        if body.get("direction") in ("up", "down"):
            allsecs = await core.sections_of(db, note)
            k = [s.id for s in allsecs].index(sec.id)
            if body["direction"] == "up" and k > 0:
                before_id = allsecs[k - 1].id
            elif body["direction"] == "down" and k + 1 < len(allsecs):
                after_id = allsecs[k + 1].id
            else:
                before_id = after_id = None
        if before_id or after_id:
            ref = before_id or after_id
            if ref not in ids:
                raise ApiError(404, "notes.section_not_found")
            k = ids.index(ref)
            if before_id:
                lo, hi = (secs[k - 1].order if k > 0 else None), secs[k].order
            else:
                lo, hi = secs[k].order, (secs[k + 1].order if k + 1 < len(secs) else None)
            ops.append({"op": "set_sections", "sections": [{"id": sec.id, "order": sec.order}]})
            nk = order.between(lo, hi)
            if order.too_long(nk):
                allrows = sorted(secs + [sec], key=lambda s: (s.order, s.id))
                for s, k2 in zip(allrows, order.spread(len(allrows)), strict=False):
                    s.order = k2
                lo = secs[k - 1].order if before_id and k > 0 else (secs[k].order if after_id else None)
                hi = secs[k].order if before_id else (secs[k + 1].order if k + 1 < len(secs) else None)
                nk = order.between(lo, hi)
            sec.order = nk
            label = f"Moved {sec.name}"
    if body.get("collapsed") is not None:
        row = await db.get(NoteSectionViewer, (user.id, sec.id))
        if row is None:
            db.add(NoteSectionViewer(user_id=user.id, section_id=sec.id, collapsed=bool(body["collapsed"])))
        else:
            row.collapsed = bool(body["collapsed"])
    if body.get("reset_learned"):
        saved = await learn.reset(db, note, sec.id)
        ops.append({"op": "unreset", "section_id": sec.id, "saved": saved})
        label = "Learned order reset"
    await db.flush()
    await core.touch(db, note)
    if any(o["op"] == "set_sections" for o in ops):
        core.stamp(note, user)
        await core.snapshot(db, note, user)
    row = await db.get(NoteSectionViewer, (user.id, sec.id))
    unchecked = sum(1 for i in await _section_items(db, note, sec.id) if not i.checked)
    return {"section": core.section_out(sec, bool(row and row.collapsed), unchecked),
            "undo": revert_undo(note, ops, label)}


async def delete_section(db: AsyncSession, user: User, note: Note, sid: str, items: str) -> dict[str, Any]:
    """ACT checksection.delete: "Keep the items" (to the top group, the default) or "Delete them too"."""
    need_list(note)
    if items not in ("keep", "delete"):
        raise HTTPException(422, "items must be keep or delete")
    sec = await get_section(db, note, sid)
    rows = await _section_items(db, note, sec.id)
    now = datetime.now(UTC)
    ops: list[dict[str, Any]] = [{"op": "restore_sections", "ids": [sec.id]}]
    if items == "delete":
        for i in rows:
            i.deleted_at = now
        ops.append({"op": "restore_items", "ids": [i.id for i in rows]})
        await db.flush()
        await _item_gone(db, note, [i.id for i in rows])
    else:
        ops.append({"op": "set_items", "items": [snap(i, ("section_id", "order")) for i in rows]})
        if rows:
            keys = await place(db, note, None, len(rows))
            for i, k in zip(rows, keys, strict=False):
                i.section_id, i.order = None, k
            await db.flush()
            aud = await core.audience(db, note)
            for i in rows:
                await core.commit_item(db, note, i, aud)
    sec.deleted_at = now
    await db.flush()
    await core.touch(db, note)
    core.stamp(note, user)
    await core.snapshot(db, note, user)
    return {"ok": True, "undo": revert_undo(note, ops, f"Deleted section {sec.name}")}


# ---------------------------------------------------------------- revert (every undo of items and sections)


async def revert(db: AsyncSession, user: User, note: Note, ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply undo ops and return their inverse (the redo)."""
    if not isinstance(ops, list) or len(ops) > 20:
        raise HTTPException(422, "ops must be a list")
    now = datetime.now(UTC)
    inverse: list[dict[str, Any]] = []
    touched: dict[str, NoteItem] = {}
    gone: list[str] = []
    for op in ops:
        kind = op.get("op") if isinstance(op, dict) else None
        if kind in ("delete_items", "restore_items"):
            ids = [x for x in (core.valid_uuid(i) for i in op.get("ids") or []) if x]
            rows = list((await db.execute(select(NoteItem).where(NoteItem.note_id == note.id, NoteItem.id.in_(ids))))
                        .scalars()) if ids else []
            if kind == "restore_items":
                live_count = len(await core.items_of(db, note))
                if live_count + sum(1 for r in rows if r.deleted_at is not None) > LIMITS["items"]:
                    raise too_many_items()
                secs = {s.id for s in await core.sections_of(db, note)}
            for r in rows:
                if kind == "delete_items":
                    r.deleted_at = now
                    gone.append(r.id)
                else:
                    r.deleted_at = None
                    if r.section_id is not None and r.section_id not in secs:
                        r.section_id = None
                    touched[r.id] = r
            inverse.insert(0, {"op": "restore_items" if kind == "delete_items" else "delete_items",
                               "ids": [r.id for r in rows]})
        elif kind == "set_items":
            secs = {s.id for s in await core.sections_of(db, note)}
            back = []
            for s in op.get("items") or []:
                iid = core.valid_uuid(s.get("id"))
                r = await db.get(NoteItem, iid) if iid else None
                if r is None or r.note_id != note.id:
                    continue
                fields = tuple(f for f in s if f != "id" and f in SNAP_FIELDS)
                back.append(snap(r, fields))
                apply_snap(r, {f: s[f] for f in fields}, secs)
                r.updated_at = now
                touched[r.id] = r
            inverse.insert(0, {"op": "set_items", "items": back})
        elif kind in ("delete_sections", "restore_sections"):
            ids = [x for x in (core.valid_uuid(i) for i in op.get("ids") or []) if x]
            rows = list((await db.execute(select(NoteSection).where(NoteSection.note_id == note.id,
                                                                    NoteSection.id.in_(ids)))).scalars()) if ids else []
            for s in rows:
                s.deleted_at = now if kind == "delete_sections" else None
                if kind == "delete_sections":
                    # Items added to it meanwhile go to the top group.
                    for i in await _section_items(db, note, s.id):
                        i.section_id = None
                        touched[i.id] = i
            inverse.insert(0, {"op": "restore_sections" if kind == "delete_sections" else "delete_sections",
                               "ids": [s.id for s in rows]})
        elif kind == "set_sections":
            back = []
            for s in op.get("sections") or []:
                sec = await get_section(db, note, s.get("id"), deleted_ok=True)
                fields = [f for f in ("name", "order", "order_mode") if f in s]
                back.append({"id": sec.id, **{f: getattr(sec, f) for f in fields}})
                for f in fields:
                    setattr(sec, f, s[f])
            inverse.insert(0, {"op": "set_sections", "sections": back})
        elif kind == "set_ranks":
            sid = op.get("section_id")
            ranks = await learn.ranks_of(db, note)
            skey = learn.section_key(sid)
            want = {k: (tuple(v) if v else None) for k, v in (op.get("ranks") or {}).items()}
            inverse.insert(0, {"op": "set_ranks", "section_id": sid,
                               "ranks": {k: list(ranks[(skey, k)]) if (skey, k) in ranks else None for k in want}})
            await learn.set_ranks(db, note, sid, want)
        elif kind == "relog":
            cid = int(op.get("check_id") or 0)
            row = await db.get(CheckitemCheck, cid)
            if row is not None and row.note_id == note.id:
                undone = bool(op.get("undone", False))
                inverse.insert(0, {"op": "relog", "check_id": cid, "undone": row.undone})
                row.undone = undone
        elif kind == "unreset":
            await learn.unreset(db, note, op.get("section_id"), op.get("saved") or {})
            inverse.insert(0, {"op": "reset", "section_id": op.get("section_id"), "saved": {}})
        elif kind == "reset":
            saved = await learn.reset(db, note, op.get("section_id"))
            inverse.insert(0, {"op": "unreset", "section_id": op.get("section_id"), "saved": saved})
        elif kind == "set_note":
            fields = {k: v for k, v in (op.get("fields") or {}).items() if k in ("top_order_mode",)}
            inverse.insert(0, {"op": "set_note", "fields": {k: getattr(note, k) for k in fields}})
            for k, v in fields.items():
                setattr(note, k, v)
            await core.commit_note(db, note)
        else:
            raise HTTPException(422, f"unknown op {kind!r}")
    await db.flush()
    aud = await core.audience(db, note)
    for r in touched.values():
        if r.deleted_at is None:
            await core.commit_item(db, note, r, aud)
    if gone:
        await _item_gone(db, note, gone)
    await core.touch(db, note)
    core.stamp(note, user)
    await core.snapshot(db, note, user)
    return {"note": await core.note_full(db, user, note), "undo": revert_undo(note, inverse, "Redone")}


# ---------------------------------------------------------------- revisions


async def restore_version(db: AsyncSession, user: User, note: Note, rev: int) -> dict[str, Any]:
    """ACT note.restore_version: the revision's text, or its items, sections and checked states,
    become the note again; the restore is itself a revision (and the undo restores the one before)."""
    row = (await db.execute(select(NoteRevision).where(NoteRevision.note_id == note.id, NoteRevision.rev == int(rev)))
           ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "version not found")
    # The current state is a revision too (snapshot keeps it), so the undo has a target.
    current = await core.snapshot(db, note, user)
    c = row.content or {}
    now = datetime.now(UTC)
    note.title = c.get("title") or ""
    note.kind = c.get("kind") or note.kind
    note.body = c.get("body") or "" if note.kind == "text" else ""
    note.color = c.get("color")
    note.shopping = bool(c.get("shopping"))
    want_secs = {s["id"]: s for s in c.get("sections") or []}
    for s in await core.sections_of(db, note, deleted=True):
        if s.id in want_secs:
            w = want_secs.pop(s.id)
            s.deleted_at, s.name, s.order, s.order_mode = None, w["name"], w["order"], w.get("order_mode", s.order_mode)
        elif s.deleted_at is None:
            s.deleted_at = now
    for sid, w in want_secs.items():
        db.add(NoteSection(id=sid, note_id=note.id, name=w["name"], order=w["order"],
                           order_mode=w.get("order_mode") or "manual"))
    await db.flush()
    want_items = {i["id"]: i for i in c.get("items") or []}
    have = {i.id: i for i in (await db.execute(select(NoteItem).where(NoteItem.note_id == note.id))).scalars()}
    gone = []
    touched = []
    for iid, i in have.items():
        if iid in want_items:
            w = want_items.pop(iid)
            i.deleted_at = None
            i.section_id, i.text, i.extra, i.order = w.get("section_id"), w["text"], w.get("extra"), w["order"]
            i.key = itemadd.item_key(i.text)
            set_qty(i, norm_qty(w["qty"]) if w.get("qty") else None)
            if bool(w.get("checked")) != bool(i.checked):
                i.checked, i.checked_at = bool(w.get("checked")), now
            touched.append(i)
        elif i.deleted_at is None:
            i.deleted_at = now
            gone.append(iid)
    for iid, w in want_items.items():
        it = NoteItem(id=iid, note_id=note.id, section_id=w.get("section_id"), text=w["text"],
                      key=itemadd.item_key(w["text"]), extra=w.get("extra"), checked=bool(w.get("checked")),
                      order=w["order"], added_by=user.id, updated_by=core.by_of(user))
        set_qty(it, norm_qty(w["qty"]) if w.get("qty") else None)
        db.add(it)
        touched.append(it)
    await db.flush()
    aud = await core.audience(db, note)
    for i in touched:
        await core.commit_item(db, note, i, aud)
    await _item_gone(db, note, gone)
    core.stamp(note, user)
    note.updated_by = {**core.by_of(user), "restore": int(rev)}
    await core.commit_note(db, note)
    await core.snapshot(db, note, user, "restore")
    await core.touch(db, note)
    return {"note": await core.note_full(db, user, note),
            "undo": undo("POST", f"/notes/{note.pid}/revisions/{current}/restore", None, "Version restored")
            if current else None}


# ---------------------------------------------------------------- labels (notelabel.*)


def label_out(lb: NoteLabel, count: int = 0) -> dict[str, Any]:
    return {"name": lb.name, "color": lb.color, "order": lb.order, "count": count}


async def label_counts(db: AsyncSession, user: User) -> dict[str, int]:
    """Notes per label in the viewer's Notes (not archived, not in Trash)."""
    sub = (select(func.unnest(NoteViewerState.labels).label("lbl"))
           .join(Note, (Note.id == NoteViewerState.item_id) & (NoteViewerState.item_kind == "note"))
           .where(NoteViewerState.user_id == user.id, Note.deleted_at.is_(None),
                  NoteViewerState.archived.is_(False), access.visible(user)).subquery())
    rows = list((await db.execute(select(sub.c.lbl, func.count()).group_by(sub.c.lbl))).all())
    # Tables carry the same labels; they count too.
    from mailr.tables import access as taccess
    from mailr.tables.models import DataTable

    tsub = (select(func.unnest(NoteViewerState.labels).label("lbl"))
            .join(DataTable, (DataTable.id == NoteViewerState.item_id) & (NoteViewerState.item_kind == "table"))
            .where(NoteViewerState.user_id == user.id, DataTable.deleted_at.is_(None),
                   NoteViewerState.archived.is_(False), taccess.visible(user)).subquery())
    rows += list((await db.execute(select(tsub.c.lbl, func.count()).group_by(tsub.c.lbl))).all())
    out: dict[str, int] = {}
    for name, n in rows:
        out[name.lower()] = out.get(name.lower(), 0) + n
    return out


async def get_label(db: AsyncSession, user: User, name: str) -> NoteLabel:
    lb = (await db.execute(select(NoteLabel).where(NoteLabel.user_id == user.id,
                                                   func.lower(NoteLabel.name) == str(name).lower()))
          ).scalar_one_or_none()
    if lb is None:
        raise ApiError(404, "labels.label_not_found")
    return lb


async def create_label(db: AsyncSession, user: User, name: str, color: Any) -> dict[str, Any]:
    """ACT notelabel.create"""
    name = " ".join(str(name or "").split())
    if not name:
        raise ApiError(422, "labels.name_required")
    if len(name) > LIMITS["label"]:
        raise ApiError(422, "labels.name_too_long", max=LIMITS["label"])
    have = await labels_of(db, user)
    if any(lb.name.lower() == name.lower() for lb in have):
        raise HTTPException(409, f"You have a label called {name} already")
    if len(have) >= LIMITS["labels"]:
        raise ApiError(422, "labels.limit", max=LIMITS["labels"])
    lb = NoteLabel(user_id=user.id, name=name, color=check_color(color),
                   order=order.after(have[-1].order if have else None))
    db.add(lb)
    await db.flush()
    await core.notify_user(db, user.id, "labels")
    return {"label": label_out(lb), "undo": undo("DELETE", f"/notes/labels/{name}", None, f"Label {name} created")}


async def patch_label(db: AsyncSession, user: User, name: str, body: dict[str, Any]) -> dict[str, Any]:
    """ACT notelabel.rename, notelabel.color, notelabel.reorder"""
    lb = await get_label(db, user, name)
    back: dict[str, Any] = {}
    label = "Label changed"
    if body.get("name") is not None:
        new = " ".join(str(body["name"]).split())
        if not new:
            raise ApiError(422, "labels.name_required")
        if len(new) > LIMITS["label"]:
            raise ApiError(422, "labels.name_too_long", max=LIMITS["label"])
        if new.lower() != lb.name.lower() and any(x.name.lower() == new.lower() for x in await labels_of(db, user)):
            raise HTTPException(409, f"You have a label called {new} already")
        old = lb.name
        back["name"] = old
        await db.execute(update(NoteViewerState).where(NoteViewerState.user_id == user.id,
                                                       (literal(old) == any_(NoteViewerState.labels)))
                         .values(labels=func.array_replace(NoteViewerState.labels, old, new)))
        lb.name = new
        label = f"Renamed to {new}"
    if "color" in body:
        back["color"] = lb.color
        lb.color = check_color(body["color"])
        label = "Colour changed"
    if body.get("before") or body.get("after") or body.get("order_key"):
        back["order_key"] = lb.order
        if body.get("order_key"):
            lb.order = str(body["order_key"])[:64]
        else:
            rows = [x for x in await labels_of(db, user) if x.id != lb.id]
            names = [x.name.lower() for x in rows]
            ref = str(body.get("before") or body.get("after")).lower()
            if ref not in names:
                raise ApiError(404, "labels.label_not_found")
            k = names.index(ref)
            if body.get("before"):
                lo, hi = (rows[k - 1].order if k > 0 else None), rows[k].order
            else:
                lo, hi = rows[k].order, (rows[k + 1].order if k + 1 < len(rows) else None)
            lb.order = order.between(lo, hi)
        label = "Label moved"
    await db.flush()
    await core.notify_user(db, user.id, "labels")
    return {"label": label_out(lb), "undo": undo("PATCH", f"/notes/labels/{lb.name}", back, label) if back else None}


async def delete_label(db: AsyncSession, user: User, name: str) -> dict[str, Any]:
    """ACT notelabel.delete: off every note of this user; the notes stay."""
    lb = await get_label(db, user, name)
    rows = (await db.execute(select(NoteViewerState).where(NoteViewerState.user_id == user.id,
                                                           (literal(lb.name) == any_(NoteViewerState.labels))))).scalars().all()
    ids = [r.item_id for r in rows if r.item_kind == "note"]
    pids = [r[0] for r in (await db.execute(select(Note.pid).where(Note.id.in_(ids or [0])))).all()]
    from mailr.tables.models import DataTable

    tids = [r.item_id for r in rows if r.item_kind == "table"]
    pids += [r[0] for r in (await db.execute(select(DataTable.pid).where(DataTable.id.in_(tids or [0])))).all()]
    await db.execute(update(NoteViewerState).where(NoteViewerState.user_id == user.id,
                                                   (literal(lb.name) == any_(NoteViewerState.labels)))
                     .values(labels=func.array_remove(NoteViewerState.labels, lb.name)))
    saved = {"name": lb.name, "color": lb.color, "order": lb.order}
    await db.delete(lb)
    await db.flush()
    await core.notify_user(db, user.id, "labels")
    return {"ok": True, "undo": undo("POST", "/notes/labels/restore", {"label": saved, "notes": pids},
                                     f"Deleted label {saved['name']}")}


async def restore_label(db: AsyncSession, user: User, saved: dict[str, Any], pids: list[str]) -> dict[str, Any]:
    name = " ".join(str(saved.get("name") or "").split())[:LIMITS["label"]]
    if not name:
        raise ApiError(422, "labels.name_required")
    existing = (await db.execute(select(NoteLabel).where(NoteLabel.user_id == user.id,
                                                         func.lower(NoteLabel.name) == name.lower()))).scalar_one_or_none()
    if existing is None:
        db.add(NoteLabel(user_id=user.id, name=name, color=check_color(saved.get("color")),
                         order=str(saved.get("order") or order.FIRST)[:64]))
    notes = (await db.execute(select(Note).where(Note.pid.in_(list(pids)[:5000] or [""]), access.visible(user)))
             ).scalars().all()
    for n in notes:
        st = await core.state_of(db, user, n, create=True)
        if name not in (st.labels or []):
            st.labels = list(st.labels or []) + [name]
    from mailr.tables import access as taccess
    from mailr.tables import core as tcore
    from mailr.tables.models import DataTable

    for t in (await db.execute(select(DataTable).where(DataTable.pid.in_(list(pids)[:5000] or [""]),
                                                       taccess.visible(user)))).scalars().all():
        st = await tcore.state_of(db, user, t, create=True)
        if name not in (st.labels or []):
            st.labels = list(st.labels or []) + [name]
    await db.flush()
    await core.notify_user(db, user.id, "labels")
    return {"ok": True, "undo": undo("DELETE", f"/notes/labels/{name}", None, f"Label {name} back")}


# ---------------------------------------------------------------- links


TARGET_KINDS = ("conversation", "event", "task", "recording")


async def link(db: AsyncSession, user: User, note: Note, target_kind: str, ref: dict[str, Any], *,
               item_id: str | None = None) -> dict[str, Any]:
    """ACT note.link: to a conversation, an event, a task or a recording the user can see."""
    if target_kind not in TARGET_KINDS:
        raise HTTPException(422, f"target_kind must be one of {', '.join(TARGET_KINDS)}")
    key, title, clean = await _target(db, user, target_kind, ref)
    dup = (await db.execute(select(NoteLink).where(NoteLink.note_id == note.id, NoteLink.target_key == key,
                                                   NoteLink.item_id.is_(None) if item_id is None
                                                   else NoteLink.item_id == item_id))).scalar_one_or_none()
    if dup is not None:
        return {"link": core.link_out(dup), "undo": None}
    ln = NoteLink(note_id=note.id, item_id=item_id, target_kind=target_kind, target_ref=clean, target_key=key,
                  title=title[:300], created_by=user.id)
    db.add(ln)
    await db.flush()
    await core.touch(db, note)
    return {"link": core.link_out(ln), "undo": undo("DELETE", f"/notes/{note.pid}/links/{ln.id}", None, "Linked")}


async def unlink(db: AsyncSession, user: User, note: Note, link_id: int) -> dict[str, Any]:
    """ACT note.unlink"""
    ln = await db.get(NoteLink, int(link_id))
    if ln is None or ln.note_id != note.id:
        raise HTTPException(404, "link not found")
    body = {"target_kind": ln.target_kind, "target_ref": ln.target_ref}
    await db.delete(ln)
    await db.flush()
    await core.touch(db, note)
    return {"ok": True, "undo": undo("POST", f"/notes/{note.pid}/links", body, "Link removed")}


async def _target(db: AsyncSession, user: User, kind: str, ref: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """The lookup key, a title and the stored ref of a link target the user (and, for an API
    token, the token: core/visibility.py) can see."""
    from mailr.core import visibility
    from mailr.models import CalObject, Conversation, SpeakrRecording

    ref = ref or {}
    if kind == "conversation":
        try:
            cid = int(ref.get("conversation_id"))
        except (TypeError, ValueError):
            raise HTTPException(422, "target_ref needs conversation_id") from None
        conv = await db.get(Conversation, cid)
        # A restricted token links only mail it may read: anything else is not found.
        if conv is None or conv.user_id != user.id or cid not in await visibility.visible_conversations(db, user.id, [cid]):
            raise ApiError(404, "mail.conversation_not_found")
        return f"conversation:{cid}", conv.subject or "(no subject)", {"conversation_id": cid}
    if kind in ("event", "task"):
        uid = str(ref.get("uid") or "")[:300]
        if not uid:
            raise HTTPException(422, "target_ref needs uid")
        obj = (await db.execute(select(CalObject).where(CalObject.user_id == user.id, CalObject.uid == uid)
                                .limit(1))).scalar_one_or_none()
        if obj is None or not await visibility.visible_connector_set(db, user.id, {obj.connector_id}):
            raise ApiError(404, f"common.{kind}_not_found", fallback=f"{kind} not found")
        clean = {"uid": uid}
        if kind == "event" and ref.get("occurrence"):
            clean["occurrence"] = str(ref["occurrence"])[:64]
        return f"{kind}:{uid}", obj.summary or "", clean
    try:
        rid = int(ref.get("recording_id"))
        cid = int(ref["connector_id"]) if ref.get("connector_id") is not None else None
    except (TypeError, ValueError):
        raise HTTPException(422, "target_ref needs connector_id and recording_id") from None
    if cid is not None:  # Speakr's ids, as the recording's address has them (/recordings/<cid>/<rid>)
        rec = (await db.execute(select(SpeakrRecording).where(SpeakrRecording.connector_id == cid,
                                                              SpeakrRecording.recording_id == rid))
               ).scalar_one_or_none()
    else:  # mailr's own row id (older links)
        rec = await db.get(SpeakrRecording, rid)
    if (rec is None or rec.user_id != user.id
            or not await visibility.visible_connector_set(db, user.id, {rec.connector_id})):
        raise HTTPException(404, "recording not found")
    return (f"recording:{rec.connector_id}:{rec.recording_id}", getattr(rec, "title", "") or "",
            {"connector_id": rec.connector_id, "recording_id": rec.recording_id})
