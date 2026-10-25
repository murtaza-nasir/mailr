"""Notes: the shapes clients see, the change events, the display order and the revisions.

The actions themselves are in notes/actions.py; the routes in notes/routes.py.
"""

from __future__ import annotations

import difflib
import json
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import live
from mailr.models import ItemVersion, User
from mailr.notes import access, acl, learn, localonly, repeat
from mailr.notes.itemadd import fmt_amount
from mailr.notes.models import (
    Note,
    NoteItem,
    NoteLink,
    NoteRevision,
    NoteSection,
    NoteSectionViewer,
    NoteViewerState,
)
from mailr.notes.reminders import next_at, of_notes

COLORS = ("tomato", "flamingo", "tangerine", "banana", "sage", "basil", "peacock", "blueberry", "lavender", "grape",
          "graphite", "cocoa")
LWW_FIELDS = ("color", "shopping", "checked_at_bottom", "suggest_usual", "local_only", "top_order_mode")
NOTE_LIVE = ("kind", "title", "body") + LWW_FIELDS
ITEM_LIVE = ("text", "qty", "extra", "checked", "section_id")

LIMITS = {"title": 500, "body": 20_000, "item": 1_000, "items": 1_000, "sections": 50, "labels": 200,
          "label": 50, "section": 100}
REVISION_WINDOW = timedelta(minutes=2)
REVISION_DAYS = 30
TRASH_DAYS = 30
PREVIEW_ITEMS = 8

_PID = "23456789abcdefghjkmnpqrstuvwxyz"


def new_pid() -> str:
    return "n" + "".join(secrets.choice(_PID) for _ in range(11))


def uuid7() -> str:
    """A time-ordered UUID (v7), as clients make for items."""
    ms = int(time.time() * 1000)
    rnd = secrets.token_bytes(10)
    b = ms.to_bytes(6, "big") + bytes([0x70 | (rnd[0] & 0x0F), rnd[1], 0x80 | (rnd[2] & 0x3F)]) + rnd[3:]
    return str(uuid.UUID(bytes=b))


def valid_uuid(v: Any) -> str | None:
    try:
        return str(uuid.UUID(str(v)))
    except (ValueError, TypeError, AttributeError):
        return None


def by_of(user: User) -> dict[str, Any]:
    """`updated_by` for a write made now by `user` (the live `by` says through which client)."""
    w = live.who()
    if w.startswith("agent:"):
        return {"kind": "agent", "id": user.id, "name": user.name or user.email, "agent": w[6:]}
    kind = w if w in ("user", "device", "assistant") else "user"
    return {"kind": kind, "id": user.id, "name": user.name or user.email}


def iso(v: datetime | None) -> str | None:
    return v.isoformat() if v is not None else None


def qty_out(item: NoteItem) -> dict[str, Any] | None:
    if item.qty_amount is None:
        return None
    return {"amount": fmt_amount(item.qty_amount), "unit": item.qty_unit, "text": item.qty_text or ""}


# ---------------------------------------------------------------- live versions


def note_live(note: Note) -> dict[str, Any]:
    return {k: getattr(note, k) for k in NOTE_LIVE}


def item_live(item: NoteItem) -> dict[str, Any]:
    q = qty_out(item)
    return {"text": item.text, "qty": q and {"amount": q["amount"], "unit": q["unit"]}, "extra": item.extra,
            "checked": bool(item.checked), "section_id": item.section_id}


async def audience(db: AsyncSession, note: Note) -> list[int]:
    return await access.audience(db, note)


async def commit_note(db: AsyncSession, note: Note) -> int:
    return await live.commit(db, access.live_uid(note), "note", note.pid, note_live(note), extra={"note": note.pid},
                             audience=await audience(db, note))


async def commit_item(db: AsyncSession, note: Note, item: NoteItem, aud: list[int] | None = None) -> int:
    return await live.commit(db, access.live_uid(note), "checkitem", item.id, item_live(item),
                             extra={"note": note.pid}, audience=aud if aud is not None else await audience(db, note))


async def note_version(db: AsyncSession, note: Note) -> int:
    return await live.current(db, access.live_uid(note), "note", note.pid, live.loader(db, note, note_live))


async def item_versions(db: AsyncSession, note: Note, items: list[NoteItem]) -> dict[str, int]:
    """The live version of each item, in one query (an item changed by a path that recorded none
    gets its version here)."""
    if not items:
        return {}
    uid = access.live_uid(note)
    rows = (await db.execute(
        select(ItemVersion.item_id, ItemVersion.version, ItemVersion.content)
        .where(ItemVersion.user_id == uid, ItemVersion.kind == "checkitem",
               ItemVersion.item_id.in_([i.id for i in items]))
        .ext(distinct_on(ItemVersion.item_id)).order_by(ItemVersion.item_id, ItemVersion.version.desc()))).all()
    have = {r.item_id: (r.version, r.content) for r in rows}
    out: dict[str, int] = {}
    for i in items:
        v = have.get(i.id)
        if v is not None and live._canon(v[1]) == live._canon(item_live(i)):
            out[i.id] = v[0]
        else:
            out[i.id] = await live.current(db, uid, "checkitem", i.id, live.loader(db, i, item_live))
    return out


async def touch(db: AsyncSession, note: Note, *, deleted: bool = False) -> None:
    """Tell the note's viewers it changed in a way its version does not hold (sections, links,
    usual items, Trash)."""
    from mailr.core.events import notify

    ev = {"type": "item", "kind": "note", "id": note.pid, "note": note.pid,
          "version": None if deleted else await note_version(db, note), "by": live.who(), "origin": live.origin_var.get()}
    if live.author():
        ev["author"] = live.author()
    if deleted:
        ev["deleted"] = True
    for uid in await audience(db, note):
        await notify(db, uid, ev)


async def notify_user(db: AsyncSession, user_id: int, what: str) -> None:
    from mailr.core.events import notify

    await notify(db, user_id, {"type": "notes", "what": what, "origin": live.origin_var.get()})


def stamp(note: Note, user: User) -> None:
    note.updated_at = datetime.now(UTC)
    note.updated_by = by_of(user)


# ---------------------------------------------------------------- loading


async def sections_of(db: AsyncSession, note: Note, *, deleted: bool = False) -> list[NoteSection]:
    q = select(NoteSection).where(NoteSection.note_id == note.id)
    if not deleted:
        q = q.where(NoteSection.deleted_at.is_(None))
    return list((await db.execute(q.order_by(NoteSection.order, NoteSection.id))).scalars())


async def items_of(db: AsyncSession, note: Note) -> list[NoteItem]:
    return list((await db.execute(select(NoteItem).where(
        NoteItem.note_id == note.id, NoteItem.deleted_at.is_(None)))).scalars())


async def state_of(db: AsyncSession, user: User, note: Note, *, create: bool = False) -> NoteViewerState | None:
    st = await db.get(NoteViewerState, (user.id, "note", note.id))
    if st is None and create:
        st = NoteViewerState(user_id=user.id, item_kind="note", item_id=note.id, labels=[])
        db.add(st)
        await db.flush()
    return st


def state_out(st: NoteViewerState | None) -> dict[str, Any]:
    if st is None:
        return {"pinned": False, "archived": False, "labels": [], "order": None, "seen_version": None, "seen_at": None,
                "calendar_column": None}
    # calendar_column: a table's date column this viewer shows on their calendar (tables/calendar.py).
    return {"pinned": bool(st.pinned), "archived": bool(st.archived), "labels": list(st.labels or []),
            "order": st.order, "seen_version": st.seen_version, "seen_at": iso(getattr(st, "seen_at", None)),
            "calendar_column": getattr(st, "calendar_column", None)}


# ---------------------------------------------------------------- display order


def group_mode(note: Note, sec: NoteSection | None) -> str:
    return (note.top_order_mode if sec is None else sec.order_mode) or "manual"


def display(note: Note, sections: list[NoteSection], items: list[NoteItem],
            ranks: dict[tuple[str, str], tuple[float, int]], *, fold: bool | None = None) -> list[NoteItem]:
    """Items in display order. `fold=False` keeps checked
    items in their place (the text a list becomes)."""
    order = [None] + [s.id for s in sorted(sections, key=lambda s: (s.order, s.id))]
    by_sec: dict[str | None, list[NoteItem]] = {k: [] for k in order}
    for i in items:
        by_sec.setdefault(i.section_id if i.section_id in by_sec else None, []).append(i)
    secmap = {s.id: s for s in sections}
    fold = bool(note.checked_at_bottom) if fold is None else fold
    out: list[NoteItem] = []
    checked: list[NoteItem] = []
    for sid in order:
        grp = by_sec.get(sid) or []
        mode = group_mode(note, secmap.get(sid) if sid else None)
        live_rows = [i for i in grp if not (fold and i.checked)]
        checked.extend(i for i in grp if fold and i.checked)
        if mode == "learned":
            skey = learn.section_key(sid)
            ranked = sorted((i for i in live_rows if (skey, i.key) in ranks),
                            key=lambda i: (ranks[(skey, i.key)][0], i.order, i.id))
            rest = sorted((i for i in live_rows if (skey, i.key) not in ranks),
                          key=lambda i: (i.added_at, i.order, i.id))
            out.extend(ranked + rest)
        else:
            out.extend(sorted(live_rows, key=lambda i: (i.order, i.id)))
    pos = {sid: n for n, sid in enumerate(order)}
    checked.sort(key=lambda i: (pos.get(i.section_id, 0), -(i.checked_at or i.updated_at).timestamp()))
    return out + checked


# ---------------------------------------------------------------- shapes


async def names(db: AsyncSession, ids: set[int | None]) -> dict[int, str]:
    ids_ = {i for i in ids if i}
    if not ids_:
        return {}
    return {u.id: (u.name or u.email) for u in (await db.execute(select(User).where(User.id.in_(ids_)))).scalars()}


def _by_id(by: dict[str, Any] | None) -> int | None:
    v = (by or {}).get("id")
    return v if isinstance(v, int) else None


def person(uid: int | None, nm: dict[int, str]) -> dict[str, Any] | None:
    return {"id": uid, "name": nm.get(uid, "")} if uid else None


def item_out(i: NoteItem, nm: dict[int, str], version: int | None,
             ranks: dict[tuple[str, str], tuple[float, int]] | None = None) -> dict[str, Any]:
    r = (ranks or {}).get((learn.section_key(i.section_id), i.key))
    return {"id": i.id, "section_id": i.section_id, "text": i.text, "key": i.key, "qty": qty_out(i),
            "extra": i.extra, "checked": bool(i.checked), "checked_at": iso(i.checked_at) if i.checked else None,
            "checked_by": person(i.checked_by, nm) if i.checked else None, "order": i.order,
            "rank": round(r[0], 4) if r else None, "added_by": person(i.added_by, nm), "added_at": iso(i.added_at),
            "updated_by": i.updated_by or None, "updated_at": iso(i.updated_at), "version": version,
            "task_uid": i.task_uid}


def section_out(s: NoteSection, collapsed: bool, unchecked: int) -> dict[str, Any]:
    return {"id": s.id, "name": s.name, "order": s.order, "order_mode": s.order_mode, "collapsed": collapsed,
            "trips": s.trips or 0, "unchecked": unchecked}


def owner_out(note: Note, nm: dict[int, str]) -> dict[str, Any]:
    if note.owner_user_id is not None:
        return {"kind": "user", "id": note.owner_user_id, "name": nm.get(note.owner_user_id, "")}
    return {"kind": "group", "name": note.owner_group}


async def links_of(db: AsyncSession, note_ids: list[int], user: User | None = None) -> dict[int, list[dict[str, Any]]]:
    """The links of these notes as `user` sees them (link titles: link_seen)."""
    if not note_ids:
        return {}
    lns = list((await db.execute(select(NoteLink).where(NoteLink.note_id.in_(note_ids), NoteLink.item_id.is_(None))
                                 .order_by(NoteLink.id))).scalars())
    seen = await link_seen(db, user, lns)
    out: dict[int, list[dict[str, Any]]] = {}
    for ln in lns:
        out.setdefault(ln.note_id, []).append(link_out(ln, ln.id in seen))
    return out


HIDDEN_LINK = "Not shared with you"


async def link_seen(db: AsyncSession, user: User | None, lns: list[NoteLink]) -> set[int]:
    """The ids of the links whose stored title `user` may read.

    A link's title is the target's subject or summary, copied when it was made. It is shown only to
    a viewer who can see the target: their own conversation, event, task or recording, and for a
    restricted API token only one inside its limits (core/visibility.py: local-only and other
    accounts' mail stay hidden). Everyone else, sharees included, gets the "Not shared with you"
    placeholder, as table link cells do (tables/core.titles). A target that is gone keeps its title
    for the person who made the link."""
    from mailr.core import visibility
    from mailr.models import CalObject, Conversation, SpeakrRecording

    if user is None or not lns:
        return set()
    restricted = visibility.restricted_viewer(user.id) is not None
    convs = [int((ln.target_ref or {}).get("conversation_id") or 0) for ln in lns if ln.target_kind == "conversation"]
    conv_ok = await visibility.visible_conversations(db, user.id, convs)
    conv_have = set((await db.execute(select(Conversation.id).where(Conversation.id.in_(convs or [0]))))
                    .scalars()) if convs else set()
    uids = [str((ln.target_ref or {}).get("uid") or "") for ln in lns if ln.target_kind in ("event", "task")]
    cal: dict[str, set[int]] = {}
    if uids:
        for uid, conn in (await db.execute(select(CalObject.uid, CalObject.connector_id).where(
                CalObject.user_id == user.id, CalObject.uid.in_(uids)))).all():
            cal.setdefault(uid, set()).add(conn)
    recs: dict[tuple[int, int], int] = {}
    want = [ln.target_ref or {} for ln in lns if ln.target_kind == "recording"]
    if want:
        rids = [int(r.get("recording_id") or 0) for r in want]
        for row in (await db.execute(select(SpeakrRecording).where(SpeakrRecording.user_id == user.id,
                                                                   SpeakrRecording.recording_id.in_(rids)))).scalars():
            recs[(row.connector_id, row.recording_id)] = row.connector_id
    conns = await visibility.visible_connector_set(db, user.id, {c for cs in cal.values() for c in cs}
                                                   | set(recs.values()))
    out: set[int] = set()
    for ln in lns:
        ref = ln.target_ref or {}
        mine = ln.created_by == user.id and not restricted
        if ln.target_kind == "conversation":
            cid = int(ref.get("conversation_id") or 0)
            ok = cid in conv_ok or (cid not in conv_have and mine)
        elif ln.target_kind in ("event", "task"):
            got = cal.get(str(ref.get("uid") or ""))
            ok = bool(got & conns) if got else mine
        elif ln.target_kind == "recording":
            try:
                key = (int(ref.get("connector_id")), int(ref.get("recording_id")))
            except (TypeError, ValueError):
                key = None
            ok = recs[key] in conns if key in recs else mine
        else:
            ok = False
        if ok:
            out.add(ln.id)
    return out


def link_out(ln: NoteLink, seen: bool = True) -> dict[str, Any]:
    """A link as the API gives it. `seen` False: the viewer cannot see the target, so its stored
    title is replaced by the placeholder (link_seen)."""
    from mailr.core.publicid import ConvId

    ref = dict(ln.target_ref or {})
    if "conversation_id" in ref and ref["conversation_id"] is not None:
        ref["conversation_id"] = ConvId(int(ref["conversation_id"]))
    if not seen:
        return {"id": ln.id, "target_kind": ln.target_kind, "target_ref": ref, "title": HIDDEN_LINK,
                "state": "hidden"}
    return {"id": ln.id, "target_kind": ln.target_kind, "target_ref": ref, "title": ln.title or "", "state": "ok"}


async def counts(db: AsyncSession, note_ids: list[int]) -> dict[int, dict[str, int]]:
    if not note_ids:
        return {}
    rows = (await db.execute(select(NoteItem.note_id, NoteItem.checked, func.count()).where(
        NoteItem.note_id.in_(note_ids), NoteItem.deleted_at.is_(None)).group_by(NoteItem.note_id, NoteItem.checked))
    ).all()
    out: dict[int, dict[str, int]] = {}
    for nid, chk, n in rows:
        c = out.setdefault(nid, {"items": 0, "unchecked": 0, "checked": 0, "sections": 0})
        c["items"] += n
        c["checked" if chk else "unchecked"] += n
    for nid, n in (await db.execute(select(NoteSection.note_id, func.count()).where(
            NoteSection.note_id.in_(note_ids), NoteSection.deleted_at.is_(None)).group_by(NoteSection.note_id))).all():
        out.setdefault(nid, {"items": 0, "unchecked": 0, "checked": 0, "sections": 0})["sections"] = n
    return out


def base_out(note: Note, user: User, nm: dict[int, str], version: int, st: NoteViewerState | None,
             cnt: dict[str, int] | None, links: list[dict[str, Any]],
             rems: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    purge = note.deleted_at + timedelta(days=TRASH_DAYS) if note.deleted_at else None
    return {"id": note.pid, "kind": note.kind, "title": note.title, "body": note.body, "color": note.color,
            "shopping": bool(note.shopping), "checked_at_bottom": bool(note.checked_at_bottom),
            "suggest_usual": bool(note.suggest_usual), "local_only": bool(note.local_only),
            "top_order_mode": note.top_order_mode, "owner": owner_out(note, nm),
            "created_by": person(note.created_by, nm), "created_at": iso(note.created_at),
            "updated_at": iso(note.updated_at), "updated_by": note.updated_by or None,
            "deleted_at": iso(note.deleted_at), "purge_at": iso(purge), "version": version,
            "level": access.level(user, note), "state": state_out(st),
            "counts": cnt or {"items": 0, "unchecked": 0, "checked": 0, "sections": 0}, "links": links,
            "sharing": acl.summary(note, nm, user), "changed": acl.changed(user, note, st),
            "can_delete": access.can_delete(user, note),
            # Local models only: may this viewer turn it off (the owner only, notes/localonly.py)?
            "can_lower_local_only": localonly.can_lower(user, note),
            # A repeating list (notes/repeat.py): {rule, next, last} or null.
            "repeat": repeat.out(note),
            # This viewer's reminders (notes/reminders.py), handled ones left out.
            "reminders": rems or [], "next_reminder": next_at(rems or [])}


async def note_full(db: AsyncSession, user: User, note: Note) -> dict[str, Any]:
    """GET /notes/{id}: the note with its sections and items in display order."""
    if note.kind == "checklist":
        await learn.learn_note(db, note)
    sections = await sections_of(db, note)
    items = await items_of(db, note)
    ranks = await learn.ranks_of(db, note)
    nm = await names(db, {note.owner_user_id, note.created_by} | {i.added_by for i in items}
                     | {i.checked_by for i in items} | acl.user_ids([note]) | {_by_id(note.updated_by)})
    st = await state_of(db, user, note)
    collapsed = {r.section_id for r in (await db.execute(select(NoteSectionViewer).where(
        NoteSectionViewer.user_id == user.id, NoteSectionViewer.section_id.in_([s.id for s in sections] or [""]),
        NoteSectionViewer.collapsed.is_(True)))).scalars()} if sections else set()
    vers = await item_versions(db, note, items)
    ordered = display(note, sections, items, ranks)
    unchecked: dict[str | None, int] = {}
    for i in items:
        if not i.checked:
            unchecked[i.section_id] = unchecked.get(i.section_id, 0) + 1
    out = base_out(note, user, nm, await note_version(db, note), st, (await counts(db, [note.id])).get(note.id),
                   (await links_of(db, [note.id], user)).get(note.id, []), (await of_notes(db, user, [note])).get(note.id))
    out["sections"] = [section_out(s, s.id in collapsed, unchecked.get(s.id, 0)) for s in sections]
    out["top"] = {"order_mode": note.top_order_mode, "collapsed": bool(st.top_collapsed) if st else False,
                  "trips": note.top_trips or 0, "unchecked": unchecked.get(None, 0)}
    out["items"] = [item_out(i, nm, vers.get(i.id), ranks) for i in ordered]
    return out


async def note_cards(db: AsyncSession, user: User, notes: list[Note]) -> list[dict[str, Any]]:
    """Cards for the grid: the note without its items, plus a preview."""
    ids = [n.id for n in notes]
    if not ids:
        return []
    states = {s.item_id: s for s in (await db.execute(select(NoteViewerState).where(
        NoteViewerState.user_id == user.id, NoteViewerState.item_kind == "note",
        NoteViewerState.item_id.in_(ids)))).scalars()}
    cnts = await counts(db, ids)
    lnks = await links_of(db, ids, user)
    lists = [n for n in notes if n.kind == "checklist"]
    items_by: dict[int, list[NoteItem]] = {}
    secs_by: dict[int, list[NoteSection]] = {}
    ranks_by: dict[int, dict[tuple[str, str], tuple[float, int]]] = {}
    if lists:
        lids = [n.id for n in lists]
        for i in (await db.execute(select(NoteItem).where(NoteItem.note_id.in_(lids), NoteItem.deleted_at.is_(None),
                                                          NoteItem.checked.is_(False)))).scalars():
            items_by.setdefault(i.note_id, []).append(i)
        for s in (await db.execute(select(NoteSection).where(NoteSection.note_id.in_(lids),
                                                             NoteSection.deleted_at.is_(None)))).scalars():
            secs_by.setdefault(s.note_id, []).append(s)
        from mailr.notes.models import CheckitemRank

        for r in (await db.execute(select(CheckitemRank).where(CheckitemRank.note_id.in_(lids)))).scalars():
            ranks_by.setdefault(r.note_id, {})[(r.section_key, r.key)] = (r.rank, r.trips)
    nm = await names(db, {n.owner_user_id for n in notes} | {n.created_by for n in notes} | acl.user_ids(notes))
    vers = await _note_versions(db, notes)
    rems = await of_notes(db, user, notes)
    out = []
    for n in notes:
        card = base_out(n, user, nm, vers[n.id], states.get(n.id), cnts.get(n.id), lnks.get(n.id, []), rems.get(n.id))
        if n.kind == "checklist":
            secs = secs_by.get(n.id, [])
            sname = {s.id: s.name for s in secs}
            ordered = display(n, secs, items_by.get(n.id, []), ranks_by.get(n.id, {}))
            c = card["counts"]
            card["preview"] = {"items": [{"id": i.id, "text": i.text, "qty": qty_out(i),
                                          "section": sname.get(i.section_id)} for i in ordered[:PREVIEW_ITEMS]],
                               "more": max(0, c["unchecked"] - PREVIEW_ITEMS), "checked": c["checked"]}
            card["body"] = ""
        else:
            card["body"] = (n.body or "")[:2000]
        out.append(card)
    return out


async def _note_versions(db: AsyncSession, notes: list[Note]) -> dict[int, int]:
    out: dict[int, int] = {}
    by_uid: dict[int, list[Note]] = {}
    for n in notes:
        by_uid.setdefault(access.live_uid(n), []).append(n)
    for uid, ns in by_uid.items():
        rows = (await db.execute(
            select(ItemVersion.item_id, ItemVersion.version, ItemVersion.content)
            .where(ItemVersion.user_id == uid, ItemVersion.kind == "note", ItemVersion.item_id.in_([n.pid for n in ns]))
            .ext(distinct_on(ItemVersion.item_id)).order_by(ItemVersion.item_id, ItemVersion.version.desc()))).all()
        have = {r.item_id: (r.version, r.content) for r in rows}
        for n in ns:
            v = have.get(n.pid)
            out[n.id] = v[0] if v is not None and live._canon(v[1]) == live._canon(note_live(n)) \
                else await note_version(db, n)
    return out


# ---------------------------------------------------------------- revisions


async def content_of(db: AsyncSession, note: Note) -> dict[str, Any]:
    """What a revision holds: the note's text, or its items, sections and checked states."""
    out: dict[str, Any] = {"kind": note.kind, "title": note.title, "body": note.body, "color": note.color,
                           "shopping": bool(note.shopping)}
    if note.local_only:  # only when on, so revisions kept before the flag was recorded still compare equal
        out["local_only"] = True
    if note.kind == "checklist":
        secs = await sections_of(db, note)
        items = await items_of(db, note)
        out["sections"] = [{"id": s.id, "name": s.name, "order": s.order, "order_mode": s.order_mode} for s in secs]
        out["items"] = [{"id": i.id, "section_id": i.section_id, "text": i.text, "qty": qty_out(i), "extra": i.extra,
                         "checked": bool(i.checked), "order": i.order}
                        for i in sorted(items, key=lambda i: (i.section_id or "", i.order, i.id))]
    else:
        out["sections"], out["items"] = [], []
    return out


def summary(old: dict[str, Any] | None, new: dict[str, Any]) -> str:
    """One line: "+3 items, 2 checked", "Body: 4 lines changed", "Title changed"."""
    if old is None:
        return "Created"
    parts: list[str] = []
    if old.get("kind") != new.get("kind"):
        parts.append("Made a list" if new.get("kind") == "checklist" else "Made text")
    if (old.get("title") or "") != (new.get("title") or ""):
        parts.append("Title changed")
    if (old.get("body") or "") != (new.get("body") or "") and new.get("kind") == "text":
        a, b = (old.get("body") or "").splitlines(), (new.get("body") or "").splitlines()
        n = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes()
                if tag != "equal")
        parts.append(f"Body: {n} line{'s' if n != 1 else ''} changed")
    oi = {i["id"]: i for i in old.get("items") or []}
    ni = {i["id"]: i for i in new.get("items") or []}
    added = len(set(ni) - set(oi))
    removed = len(set(oi) - set(ni))
    both = set(ni) & set(oi)
    checked = sum(1 for k in both if ni[k]["checked"] and not oi[k]["checked"])
    unchecked = sum(1 for k in both if oi[k]["checked"] and not ni[k]["checked"])
    edited = sum(1 for k in both if (ni[k]["text"], ni[k]["qty"], ni[k]["extra"])
                 != (oi[k]["text"], oi[k]["qty"], oi[k]["extra"]))
    moved = sum(1 for k in both if (ni[k]["section_id"], ni[k]["order"]) != (oi[k]["section_id"], oi[k]["order"]))
    if added:
        parts.append(f"+{added} item{'s' if added != 1 else ''}")
    if removed:
        parts.append(f"−{removed} item{'s' if removed != 1 else ''}")
    if checked:
        parts.append(f"{checked} checked")
    if unchecked:
        parts.append(f"{unchecked} unchecked")
    if edited:
        parts.append(f"{edited} edited")
    if moved and not (added or removed):
        parts.append(f"{moved} moved")
    os_, ns_ = {s["id"]: s["name"] for s in old.get("sections") or []}, {s["id"]: s["name"] for s in new.get("sections") or []}
    if os_ != ns_:
        parts.append("Sections changed")
    if old.get("color") != new.get("color"):
        parts.append("Colour changed")
    if bool(old.get("local_only")) != bool(new.get("local_only")):
        parts.append("Local models only " + ("on" if new.get("local_only") else "off"))
    return ", ".join(parts) or "No change"


def _canon(v: Any) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)


async def snapshot(db: AsyncSession, note: Note, user: User | None, source: str = "edit", *,
                   by: dict[str, Any] | None = None, now: datetime | None = None) -> int | None:
    """Keep a revision of the note as it is now (after a write). Edits by one author within
    REVISION_WINDOW of the revision's start update that revision, so a long editing session
    leaves a few revisions; any other author, source or a closed session starts a new one."""
    now = now or datetime.now(UTC)
    by = by or (by_of(user) if user is not None else {"kind": source})
    if source == "edit" and by.get("kind") in ("assistant", "agent"):
        source = by["kind"]  # the assistant's and agents' writes are versions of their own
    content = await content_of(db, note)
    latest = (await db.execute(select(NoteRevision).where(NoteRevision.note_id == note.id)
                               .order_by(NoteRevision.rev.desc()).limit(1))).scalar_one_or_none()
    if latest is not None and _canon(latest.content) == _canon(content):
        return latest.rev
    from mailr.notes.embed import queue

    await queue(db, note.owner_user_id, note.owner_group)
    same_session = (latest is not None and source == "edit" and latest.source == "edit" and not latest.closed
                    and (latest.by or {}).get("id") == by.get("id") and (latest.by or {}).get("kind") == by.get("kind")
                    and now - latest.opened_at < REVISION_WINDOW)
    if same_session:
        prev = (await db.execute(select(NoteRevision.content).where(
            NoteRevision.note_id == note.id, NoteRevision.rev < latest.rev).order_by(NoteRevision.rev.desc()).limit(1))
        ).scalar_one_or_none()
        latest.content = content
        latest.at = now
        latest.summary = summary(prev, content)[:300]
        return latest.rev
    rev = NoteRevision(note_id=note.id, rev=(latest.rev + 1) if latest else 1, opened_at=now, at=now, by=by,
                       source=source, content=content,
                       summary=summary(latest.content if latest else None, content)[:300])
    db.add(rev)
    await db.flush()
    return rev.rev


async def close_session(db: AsyncSession, note: Note) -> int | None:
    latest = (await db.execute(select(NoteRevision).where(NoteRevision.note_id == note.id)
                               .order_by(NoteRevision.rev.desc()).limit(1))).scalar_one_or_none()
    if latest is None:
        return None
    latest.closed = True
    return latest.rev


def revision_out(r: NoteRevision, *, content: bool = False) -> dict[str, Any]:
    out = {"rev": r.rev, "at": iso(r.at), "by": r.by or {}, "source": r.source, "summary": r.summary}
    if content:
        out["content"] = r.content
    return out
