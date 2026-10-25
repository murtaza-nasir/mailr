"""Notes search (note.search): full text over titles, bodies, item texts and extra lines, and
section names (Postgres `simple` configuration, as the rest of mailr), with operators.

Operators: is:list, is:text, is:pinned, is:archived, in:trash, label:<name>, color:<name>,
section:<name>, has:link, is:checked, is:unchecked, by:<person> (wrote the note or an item; `me`).
Tables are searched too (their names, descriptions and row text): `is:table` lists only tables, and
the operators that name lists, text notes, sections or checked items leave tables out. is:shared
matches what other people see too (shared with someone, shared with the caller, or a group's).
has:reminder matches the notes with a reminder of the caller's that is not handled. Quoted values keep spaces
(`label:"Weekly shop"`). Archived notes are included; notes in Trash only with in:trash.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

from sqlalchemy import and_, exists, func, literal, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import User
from mailr.notes import access, acl, core
from mailr.notes.models import Note, NoteItem, NoteLink, NoteSection, NoteViewerState

NOTE_ONLY = {"list", "text", "checked", "unchecked"}


def split(q: str) -> tuple[dict[str, list[str]], list[str]]:
    """Operators and free words."""
    try:
        parts = shlex.split(q or "")
    except ValueError:
        parts = (q or "").split()
    ops: dict[str, list[str]] = {}
    words: list[str] = []
    for p in parts:
        m = re.match(r"^(is|in|label|color|colour|section|has|by):(.*)$", p, re.I)
        if m and m.group(2):
            k = m.group(1).lower().replace("colour", "color")
            ops.setdefault(k, []).append(m.group(2))
        else:
            words.append(p)
    return ops, words


def tsquery(words: list[str]) -> str | None:
    terms = [t for w in words for t in re.findall(r"\w+", w.lower())]
    if not terms:
        return None
    return " & ".join(f"{t}:*" for t in terms[:12])


async def _people(db: AsyncSession, user: User, who: str) -> list[int]:
    """`by:me`, `by:dana` (a name or an address that starts with it)."""
    w = who.strip().lower()
    if w in ("me", "myself"):
        return [user.id]
    rows = (await db.execute(select(User.id).where(or_(func.lower(User.name).like(f"{w}%"),
                                                       func.lower(User.email).like(f"{w}%"))))).all()
    return [r[0] for r in rows]


async def run(db: AsyncSession, user: User, q: str, *, limit: int = 50, p: Any = None,
              hide_local: bool = False) -> dict[str, Any]:
    """Cards matching `q`. With `p` (the caller), a token sees no local-only notes it may not see
    (access.hidden_from); `hide_local` leaves out every local-only note (a hosted model)."""
    ops, words = split(q)
    flags = {v.lower() for v in ops.get("is", [])} | {f"in:{v.lower()}" for v in ops.get("in", [])} \
        | {f"has:{v.lower()}" for v in ops.get("has", [])}
    hide_tables_local = hide_local or (p is not None and access.hidden_from(p))
    # Reminders are on notes only, so has:reminder leaves tables out.
    tables = [] if (NOTE_ONLY & flags or ops.get("section") or "has:reminder" in flags) else await search_tables(
        db, user, ops, words, flags, limit=limit, hide_local=hide_tables_local)
    if "table" in flags:
        return {"notes": [], "tables": tables, "query": q}
    cond = [access.visible_to(p) if p is not None else access.visible(user)]
    if hide_local:
        cond.append(Note.local_only.is_(False))
    for who in ops.get("by", []):
        ids = await _people(db, user, who)
        cond.append(or_(Note.created_by.in_(ids or [0]), exists().where(
            NoteItem.note_id == Note.id, NoteItem.deleted_at.is_(None), NoteItem.added_by.in_(ids or [0]))))
    cond.append(Note.deleted_at.is_not(None) if "in:trash" in flags else Note.deleted_at.is_(None))
    if "shared" in flags:
        cond.append(acl.shared_cond(user, "note", Note))
    if "list" in flags:
        cond.append(Note.kind == "checklist")
    if "text" in flags:
        cond.append(Note.kind == "text")
    st = select(NoteViewerState.item_id).where(NoteViewerState.user_id == user.id, NoteViewerState.item_kind == "note")
    if "pinned" in flags:
        cond.append(Note.id.in_(st.where(NoteViewerState.pinned.is_(True))))
    if "archived" in flags:
        cond.append(Note.id.in_(st.where(NoteViewerState.archived.is_(True))))
    for lb in ops.get("label", []):
        cond.append(Note.id.in_(st.where(func.lower(func.array_to_string(NoteViewerState.labels, "\x1f"))
                                         .op("~")(rf"(^|\x1f){re.escape(lb.lower())}($|\x1f)"))))
    for c in ops.get("color", []):
        cond.append(func.lower(Note.color) == c.lower())
    for s in ops.get("section", []):
        cond.append(exists().where(NoteSection.note_id == Note.id, NoteSection.deleted_at.is_(None),
                                   func.lower(NoteSection.name).like(f"{s.lower()}%")))
    if "has:link" in flags:
        cond.append(exists().where(NoteLink.note_id == Note.id))
    if "has:reminder" in flags:
        from mailr.notes import reminders

        cond.append(Note.id.in_(await reminders.view_ids(db, user) or [0]))
    item_state = None
    if "checked" in flags:
        item_state = True
    elif "unchecked" in flags:
        item_state = False
    tq = tsquery(words)
    item_cond = [NoteItem.note_id == Note.id, NoteItem.deleted_at.is_(None)]
    if item_state is not None:
        item_cond.append(NoteItem.checked.is_(item_state))
    if tq:
        query = func.to_tsquery("simple", tq)
        like = "%" + "%".join(words).lower() + "%"
        hit_item = exists().where(*item_cond, NoteItem.tsv.op("@@")(query))
        hit_sec = exists().where(NoteSection.note_id == Note.id, NoteSection.deleted_at.is_(None),
                                 func.lower(NoteSection.name).like(like))
        cond.append(or_(Note.tsv.op("@@")(query), hit_item, hit_sec))
        rank = func.ts_rank(Note.tsv, query)
    else:
        if item_state is not None:
            cond.append(exists().where(*item_cond))
        rank = literal(0.0)
    rows = (await db.execute(select(Note, rank.label("r")).where(and_(*cond))
                             .order_by(text("r DESC"), Note.updated_at.desc()).limit(limit))).all()
    notes = [r[0] for r in rows]
    cards = await core.note_cards(db, user, notes)
    if tq or item_state is not None:
        ids = [n.id for n in notes]
        iq = select(NoteItem).where(NoteItem.note_id.in_(ids or [0]), NoteItem.deleted_at.is_(None))
        if item_state is not None:
            iq = iq.where(NoteItem.checked.is_(item_state))
        if tq:
            iq = iq.where(NoteItem.tsv.op("@@")(func.to_tsquery("simple", tq)))
        matches: dict[int, list[dict[str, Any]]] = {}
        for i in (await db.execute(iq.order_by(NoteItem.order))).scalars():
            m = matches.setdefault(i.note_id, [])
            if len(m) < 5:
                m.append({"item_id": i.id, "text": i.text, "checked": bool(i.checked)})
        pid_of = {n.id: n.pid for n in notes}
        by_pid = {pid_of[k]: v for k, v in matches.items()}
        for c in cards:
            c["matches"] = by_pid.get(c["id"], [])
    else:
        for c in cards:
            c["matches"] = []
    return {"notes": cards, "tables": tables, "query": q}


async def search_tables(db: AsyncSession, user: User, ops: dict[str, list[str]], words: list[str], flags: set[str], *,
                        limit: int, hide_local: bool = False) -> list[dict[str, Any]]:
    """Tables by name, description and row text, with the operators that apply to them. A row
    match names its row (`matches`: row id and text)."""
    from mailr.tables import access as taccess
    from mailr.tables import core as tcore
    from mailr.tables.models import DataTable, TableRow

    cond = [taccess.visible(user)]
    if hide_local:
        cond.append(DataTable.local_only.is_(False))
    cond.append(DataTable.deleted_at.is_not(None) if "in:trash" in flags else DataTable.deleted_at.is_(None))
    if "shared" in flags:
        cond.append(acl.shared_cond(user, "table", DataTable))
    st = select(NoteViewerState.item_id).where(NoteViewerState.user_id == user.id, NoteViewerState.item_kind == "table")
    if "pinned" in flags:
        cond.append(DataTable.id.in_(st.where(NoteViewerState.pinned.is_(True))))
    if "archived" in flags:
        cond.append(DataTable.id.in_(st.where(NoteViewerState.archived.is_(True))))
    for lb in ops.get("label", []):
        cond.append(DataTable.id.in_(st.where(func.lower(func.array_to_string(NoteViewerState.labels, "\x1f"))
                                              .op("~")(rf"(^|\x1f){re.escape(lb.lower())}($|\x1f)"))))
    for c in ops.get("color", []):
        cond.append(func.lower(DataTable.color) == c.lower())
    for who in ops.get("by", []):
        ids = await _people(db, user, who)
        cond.append(DataTable.created_by.in_(ids or [0]))
    if "has:link" in flags:
        cond.append(exists().where(NoteLink.table_id == DataTable.id))
    tq = tsquery(words)
    if tq:
        query = func.to_tsquery("simple", tq)
        hit_row = exists().where(TableRow.table_id == DataTable.id, TableRow.deleted_at.is_(None),
                                 TableRow.tsv.op("@@")(query))
        cond.append(or_(DataTable.tsv.op("@@")(query), hit_row))
        rank = func.ts_rank(DataTable.tsv, query)
    else:
        rank = literal(0.0)
    found = [r[0] for r in (await db.execute(select(DataTable, rank.label("r")).where(and_(*cond))
                                             .order_by(text("r DESC"), DataTable.updated_at.desc()).limit(limit))).all()]
    cards = await tcore.table_cards(db, user, found)
    if tq:
        ids = [t.id for t in found]
        matches: dict[int, list[dict[str, Any]]] = {}
        for r in (await db.execute(select(TableRow).where(TableRow.table_id.in_(ids or [0]), TableRow.deleted_at.is_(None),
                                                          TableRow.tsv.op("@@")(func.to_tsquery("simple", tq)))
                                   .order_by(TableRow.order).limit(500))).scalars():
            m = matches.setdefault(r.table_id, [])
            if len(m) < 5:
                m.append({"row_id": r.id, "text": (r.search_text or "")[:160]})
        pid_of = {t.id: t.pid for t in found}
        by_pid = {pid_of[k]: v for k, v in matches.items()}
        for c in cards:
            c["matches"] = by_pid.get(c["id"], [])
    else:
        for c in cards:
            c["matches"] = []
    return cards
