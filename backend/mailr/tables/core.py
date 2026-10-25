"""Tables: the shapes clients see, live versions and change events, the change log, link titles
and the reader's settings. The actions are in tables/actions.py, history in tables/history.py.
"""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import live
from mailr.models import User
from mailr.notes import acl, localonly
from mailr.notes.core import (  # noqa: F401  (re-exported)
    by_of,
    iso,
    names,
    person,
    uuid7,
    valid_uuid,
)
from mailr.tables import access
from mailr.tables import types as ty
from mailr.tables.models import (
    DataTable,
    TableColumn,
    TableRow,
    TableRowChange,
    TableRowRef,
    TableSchemaChange,
    TableView,
)
from mailr.tables.types import Col, Ctx

TRASH_DAYS = 30
HISTORY_DAYS = 30
PREVIEW_ROWS = 4
LWW_FIELDS = ("color", "local_only", "primary_column")
_PID = "23456789abcdefghjkmnpqrstuvwxyz"


def new_pid() -> str:
    return "t" + "".join(secrets.choice(_PID) for _ in range(11))


def new_batch() -> str:
    return str(uuid.uuid4())


async def reader(db: AsyncSession, user: User) -> Ctx:
    """The user's settings for reading text: today in their zone, the date order, the zone and
    the default currency (user settings `date_order` and `currency`)."""
    from mailr.calendar import tz as tzmod

    z = await tzmod.user_zone(db, user)
    s = user.settings or {}
    return Ctx(today=datetime.now(z).date(), order="dmy" if s.get("date_order") == "dmy" else "mdy",
               tz=getattr(z, "key", "UTC") or "UTC", currency=str(s.get("currency") or "USD").upper()[:3])


# ---------------------------------------------------------------- loading


async def columns_of(db: AsyncSession, t: DataTable, *, deleted: bool = False) -> list[TableColumn]:
    q = select(TableColumn).where(TableColumn.table_id == t.id)
    if not deleted:
        q = q.where(TableColumn.deleted_at.is_(None))
    # populate_existing: a session kept across an approval (the assistant's turn) reads what the
    # approved write changed in another session.
    return list((await db.execute(q.order_by(TableColumn.order, TableColumn.col_id)
                                  .execution_options(populate_existing=True))).scalars())


def cols_map(columns: list[TableColumn]) -> dict[str, Col]:
    return {c.col_id: ty.col_of(c) for c in columns if c.deleted_at is None}


async def views_of(db: AsyncSession, t: DataTable) -> list[TableView]:
    return list((await db.execute(select(TableView).where(TableView.table_id == t.id)
                                  .order_by(TableView.is_default.desc(), TableView.order, TableView.view_id)
                                  .execution_options(populate_existing=True))).scalars())


async def rows_of(db: AsyncSession, t: DataTable, *, deleted: bool = False) -> list[TableRow]:
    q = select(TableRow).where(TableRow.table_id == t.id)
    q = q.where(TableRow.deleted_at.is_not(None) if deleted else TableRow.deleted_at.is_(None))
    return list((await db.execute(q.execution_options(populate_existing=True))).scalars())


async def row_count(db: AsyncSession, t: DataTable) -> int:
    return int((await db.execute(select(func.count()).select_from(TableRow).where(
        TableRow.table_id == t.id, TableRow.deleted_at.is_(None)))).scalar_one())


async def get_row(db: AsyncSession, t: DataTable, row_id: str, *, deleted_ok: bool = False) -> TableRow:
    rid = valid_uuid(row_id)
    row = await db.get(TableRow, rid) if rid else None
    if row is None or row.table_id != t.id:
        raise _http(404, "row not found")
    if row.deleted_at is not None and not deleted_ok:
        nm = await names(db, {row.deleted_by})
        who_ = nm.get(row.deleted_by or 0) or "Someone"
        raise _http(409, {"error": "deleted", "deleted": True, "id": row.id, "deleted_by": who_,
                          "detail": f"{who_} deleted this row. Restore it to change it."})
    return row


async def get_column(db: AsyncSession, t: DataTable, col_id: str, *, deleted_ok: bool = False) -> TableColumn:
    c = await db.get(TableColumn, (t.id, str(col_id)))
    if c is None or (c.deleted_at is not None and not deleted_ok):
        raise _http(404, "column not found")
    return c


def _http(status: int, detail: Any):
    from fastapi import HTTPException

    return HTTPException(status, detail)


# ---------------------------------------------------------------- the change log


async def next_seq(db: AsyncSession, t: DataTable, n: int = 1) -> int:
    """Reserve n change numbers; returns the first. Locks the table's row until commit, so the
    changes of one table are numbered in the order they are written."""
    last = (await db.execute(text("UPDATE tables SET seq = seq + :n WHERE id = :id RETURNING seq"),
                             {"n": n, "id": t.id})).scalar_one()
    t.seq = last
    return last - n + 1


class Log:
    """The changes of one action: one batch id, numbered in order, one author and source."""

    def __init__(self, db: AsyncSession, t: DataTable, user: User | None, source: str = "edit", *,
                 by: dict[str, Any] | None = None, batch: str | None = None, at: datetime | None = None):
        self.db, self.t, self.source = db, t, source
        self.by = by or (by_of(user) if user is not None else {"kind": source})
        if source in ("import", "restore") and "source" not in self.by:
            self.by = {**self.by, "source": source}
        self.batch = batch or new_batch()
        self.at = at
        self.count = 0

    async def _first(self) -> None:
        """The first row change of an action queues the table's embedding (debounced)."""
        if self.count == 0 and not getattr(self, "_queued", False):
            self._queued = True
            from mailr.tables.embed import queue

            await queue(self.db, self.t.owner_user_id, self.t.owner_group)

    async def row(self, row_id: str, op: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> None:
        await self._first()
        seq = await next_seq(self.db, self.t)
        ch = TableRowChange(table_id=self.t.id, row_id=row_id, seq=seq, by=self.by, source=self.source, op=op,
                            before=before, after=after, batch=self.batch)
        if self.at is not None:
            ch.at = self.at
        self.db.add(ch)
        self.count += 1

    async def rows(self, items: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]]) -> None:
        """Many row changes at once (an import, a bulk edit): one sequence reservation."""
        if not items:
            return
        await self._first()
        first = await next_seq(self.db, self.t, len(items))
        for i, (row_id, op, before, after) in enumerate(items):
            ch = TableRowChange(table_id=self.t.id, row_id=row_id, seq=first + i, by=self.by, source=self.source,
                                op=op, before=before, after=after, batch=self.batch)
            if self.at is not None:
                ch.at = self.at
            self.db.add(ch)
        self.count += len(items)

    async def schema(self, op: str, *, col_id: str | None = None, view_id: str | None = None,
                     before: dict[str, Any] | None = None, after: dict[str, Any] | None = None,
                     values: dict[str, Any] | None = None) -> None:
        seq = await next_seq(self.db, self.t)
        ch = TableSchemaChange(table_id=self.t.id, col_id=col_id, view_id=view_id, seq=seq, by=self.by,
                               source=self.source, op=op, before=before, after=after, values=values, batch=self.batch)
        if self.at is not None:
            ch.at = self.at
        self.db.add(ch)
        self.count += 1


def row_state(r: TableRow) -> dict[str, Any]:
    """A row's full content, as a change and a snapshot hold it."""
    return {"cells": dict(r.cells or {}), "raw": dict(r.raw or {}), "order": r.order}


def column_def(c: TableColumn) -> dict[str, Any]:
    return {"col_id": c.col_id, "name": c.name, "type": c.type, "config": dict(c.config or {}),
            "required": bool(c.required), "width": c.width, "order": c.order,
            "deleted": c.deleted_at is not None}


def view_def(v: TableView) -> dict[str, Any]:
    return {"view_id": v.view_id, "name": v.name, "spec": dict(v.spec or {}), "is_default": bool(v.is_default),
            "order": v.order}


def fields_of(t: DataTable) -> dict[str, Any]:
    return {"name": t.name, "description": t.description, "color": t.color, "primary_column": t.primary_column}


# ---------------------------------------------------------------- live versions and events


def row_live(r: TableRow) -> dict[str, Any]:
    return {"cells": dict(r.cells or {}), "raw": dict(r.raw or {}), "order": r.order}


async def table_live(db: AsyncSession, t: DataTable) -> dict[str, Any]:
    cols = await columns_of(db, t, deleted=True)
    views = await views_of(db, t)
    return {**fields_of(t), "local_only": bool(t.local_only),
            "columns": {c.col_id: {k: v for k, v in column_def(c).items() if k != "col_id"} for c in cols},
            "views": {v.view_id: {k: x for k, x in view_def(v).items() if k != "view_id"} for v in views}}


async def commit_table(db: AsyncSession, t: DataTable, aud: list[int] | None = None) -> int:
    await db.flush()
    return await live.commit(db, access.live_uid(t), "table", t.pid, await table_live(db, t), extra={"table": t.pid},
                             audience=aud if aud is not None else await access.audience(db, t))


async def commit_row(db: AsyncSession, t: DataTable, r: TableRow, aud: list[int] | None = None) -> int:
    return await live.commit(db, access.live_uid(t), "row", r.id, row_live(r), extra={"table": t.pid},
                             audience=aud if aud is not None else await access.audience(db, t))


async def table_version(db: AsyncSession, t: DataTable) -> int:
    return await live.current(db, access.live_uid(t), "table", t.pid, live.loader(db, t, lambda t: table_live(db, t)))


async def row_versions(db: AsyncSession, t: DataTable, rows: list[TableRow]) -> dict[str, int]:
    """The live version of each row, in one query (rows changed with no version get one)."""
    from sqlalchemy.dialects.postgresql import distinct_on

    from mailr.models import ItemVersion

    if not rows:
        return {}
    uid = access.live_uid(t)
    out: dict[str, int] = {}
    ids = [r.id for r in rows]
    have: dict[str, tuple[int, Any]] = {}
    for i in range(0, len(ids), 2000):
        part = ids[i:i + 2000]
        for rid, v, content in (await db.execute(
                select(ItemVersion.item_id, ItemVersion.version, ItemVersion.content)
                .where(ItemVersion.user_id == uid, ItemVersion.kind == "row", ItemVersion.item_id.in_(part))
                .ext(distinct_on(ItemVersion.item_id)).order_by(ItemVersion.item_id, ItemVersion.version.desc()))).all():
            have[rid] = (v, content)
    stale = [r for r in rows if have.get(r.id) is None or live._canon(have[r.id][1]) != live._canon(row_live(r))]
    stale_ids = {r.id for r in stale}
    for r in rows:
        if r.id not in stale_ids:
            out[r.id] = have[r.id][0]
    if not stale:
        return out
    # Changed by a batch that recorded no version (an import, a retype, a revert), or by an edit
    # that committed after these rows were loaded: read them again, after the versions, so a copy
    # older than its newest version is never recorded (core/live.py `state`).
    for i in range(0, len(stale), 2000):
        await db.execute(select(TableRow).where(TableRow.id.in_([r.id for r in stale[i:i + 2000]]))
                         .execution_options(populate_existing=True))
    seed: list[dict[str, Any]] = []
    for r in stale:
        v = have.get(r.id)
        content = row_live(r)
        if v is not None and live._canon(v[1]) == live._canon(content):
            out[r.id] = v[0]
        else:
            # Recorded here, for all such rows in one statement.
            out[r.id] = (v[0] + 1) if v is not None else 1
            seed.append({"user_id": uid, "kind": "row", "item_id": r.id, "version": out[r.id],
                         "content": json.loads(live._canon(content)), "by": "sync" if v is not None else "seed"})
    if seed:
        from sqlalchemy.dialects.postgresql import insert

        made: set[str] = set()
        for i in range(0, len(seed), 1000):
            made |= set((await db.execute(insert(ItemVersion).values(seed[i:i + 1000]).on_conflict_do_nothing(
                constraint="uq_item_versions").returning(ItemVersion.item_id))).scalars())
        # A version recorded meanwhile by an edit: this row's version under its lock.
        by_id = {r.id: r for r in stale}
        for s in seed:
            if s["item_id"] not in made:
                r = by_id[s["item_id"]]
                out[r.id] = await live.current(db, uid, "row", r.id, live.loader(db, r, row_live))
    return out


async def touch(db: AsyncSession, t: DataTable, *, deleted: bool = False, purged: bool = False,
                rows: bool = False) -> None:
    """Tell the table's viewers it changed in a way a version does not hold (Trash, links, a batch
    of rows: `rows` asks open grids to reload the rows)."""
    from mailr.core.events import notify

    ev: dict[str, Any] = {"type": "item", "kind": "table", "id": t.pid, "table": t.pid,
                          "version": None if deleted else await table_version(db, t), "by": live.who(),
                          "origin": live.origin_var.get()}
    if live.author():
        ev["author"] = live.author()
    if deleted:
        ev["deleted"] = True
    if purged:
        ev["purged"] = True
    if rows:
        ev["rows"] = True
    for uid in await access.audience(db, t):
        await notify(db, uid, ev)


def stamp(t: DataTable, user: User | None) -> None:
    t.updated_at = datetime.now(UTC)
    if user is not None:
        t.updated_by = by_of(user)


# ---------------------------------------------------------------- derived row data


def search_text(cols: dict[str, Col], cells: dict[str, Any], raw: dict[str, Any]) -> str:
    """What finds a row: every value but links, as plain text, and the text kept in `raw`."""
    parts = []
    for k, v in (cells or {}).items():
        c = cols.get(k)
        if c is not None and c.type != "link":
            parts.append(ty.plain(c, v))
    parts.extend(str(v) for v in (raw or {}).values())
    return " ".join(p for p in parts if p)[:100_000]


async def set_refs(db: AsyncSession, t: DataTable, r: TableRow, cols: dict[str, Col]) -> None:
    """Rebuild the link index of one row."""
    await db.execute(delete(TableRowRef).where(TableRowRef.table_id == t.id, TableRowRef.row_id == r.id))
    if r.deleted_at is not None:
        return
    seen = set()
    for k, v in (r.cells or {}).items():
        c = cols.get(k)
        if c is None or c.type != "link" or not isinstance(v, list):
            continue
        for ref in v:
            key = ty.ref_key(ref)
            if (k, key) in seen:
                continue
            seen.add((k, key))
            db.add(TableRowRef(table_id=t.id, row_id=r.id, col_id=k, target_key=key[:400], target_kind=ref["kind"]))


async def derive(db: AsyncSession, t: DataTable, r: TableRow, cols: dict[str, Col]) -> None:
    r.search_text = search_text(cols, r.cells or {}, r.raw or {})
    if any(c.type == "link" for c in cols.values()) or r.deleted_at is not None:
        await set_refs(db, t, r, cols)


# ---------------------------------------------------------------- link titles


async def titles(db: AsyncSession, user: User, refs: list[dict[str, Any]], *, owner_view: bool = False
                 ) -> dict[str, dict[str, Any]]:
    """What each link chip shows, for this viewer: {key: {title, kind, state, …}}. `state` is
    "ok", "hidden" ("Not shared with you") or "deleted". A viewer sees a title only when they can
    see the target, and a restricted API token only inside its limits (core/visibility.py): no
    local-only or other-account mail, no local-only calendar, table or note."""
    from mailr.core import visibility
    from mailr.models import CalObject, Conversation
    from mailr.notes import access as naccess
    from mailr.notes.models import Note

    tok = visibility.restricted_viewer(user.id)
    no_local = naccess.hidden_from(tok)

    out: dict[str, dict[str, Any]] = {}
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for r in refs:
        by_kind.setdefault(r.get("kind"), []).append(r)
    hidden = {"title": "Not shared with you", "state": "hidden"}
    gone = {"title": "Deleted", "state": "deleted"}
    rows = by_kind.get("row") or []
    if rows:
        tpids = {r["table"] for r in rows}
        tables = {x.pid: x for x in (await db.execute(select(DataTable).where(DataTable.pid.in_(tpids)))).scalars()}
        rids = [r["row"] for r in rows]
        found = {x.id: x for x in (await db.execute(select(TableRow).where(TableRow.id.in_(rids)))).scalars()}
        prim: dict[int, Col | None] = {}
        for x in tables.values():
            if x.primary_column:
                c = await db.get(TableColumn, (x.id, x.primary_column))
                prim[x.id] = ty.col_of(c) if c is not None else None
        for r in rows:
            key = ty.ref_key(r)
            tb = tables.get(r["table"])
            row = found.get(r["row"])
            if tb is None or row is None or row.table_id != tb.id:
                out[key] = {"kind": "row", **gone}
            elif access.level(user, tb) == "none" or (tb.local_only and no_local):
                out[key] = {"kind": "row", **hidden}
            elif tb.deleted_at is not None or row.deleted_at is not None:
                out[key] = {"kind": "row", **gone, "table": tb.pid, "row": row.id}
            else:
                pc = prim.get(tb.id)
                title = ty.plain(pc, (row.cells or {}).get(pc.col_id)) if pc else ""
                out[key] = {"kind": "row", "state": "ok", "title": title or "Untitled row", "table": tb.pid,
                            "table_name": tb.name, "row": row.id}
    convs = by_kind.get("conversation") or []
    if convs:
        ids = [int(r["conversation_id"]) for r in convs]
        have = {c.id: c for c in (await db.execute(select(Conversation).where(Conversation.id.in_(ids)))).scalars()}
        seen = await visibility.visible_conversations(db, user.id, ids)
        for r in convs:
            key = ty.ref_key(r)
            c = have.get(int(r["conversation_id"]))
            if c is None:
                out[key] = {"kind": "conversation", **gone}
            elif c.user_id != user.id or c.id not in seen:
                out[key] = {"kind": "conversation", **hidden}
            else:
                from mailr.core.publicid import ConvId

                out[key] = {"kind": "conversation", "state": "ok", "title": c.subject or "(no subject)",
                            "conversation_id": ConvId(c.id)}
    for kind in ("event", "task"):
        items = by_kind.get(kind) or []
        if not items:
            continue
        uids = [r["uid"] for r in items]
        have = {}
        found = list((await db.execute(select(CalObject).where(CalObject.user_id == user.id,
                                                                CalObject.uid.in_(uids)))).scalars())
        conns = await visibility.visible_connector_set(db, user.id, {o.connector_id for o in found})
        for o in found:
            if o.connector_id in conns:
                have.setdefault(o.uid, o)
        for r in items:
            key = ty.ref_key(r)
            o = have.get(r["uid"])
            if o is None:
                kept = any(f.uid == r["uid"] for f in found)  # there, outside the caller's limits
                out[key] = {"kind": kind, **(gone if owner_view and not kept else hidden)}
                continue
            info: dict[str, Any] = {"kind": kind, "state": "ok", "title": o.summary or "(no title)", "uid": o.uid}
            if kind == "event":
                s = o.start_date if o.all_day else o.start_at
                info["date"] = s.isoformat() if s else None
                if r.get("occurrence"):
                    info["occurrence"] = r["occurrence"]
            else:
                info["status"] = "done" if (o.status == "COMPLETED" or o.completed_at is not None) else "open"
            out[key] = info
    notes = by_kind.get("note") or []
    if notes:
        npids = [r["id"] for r in notes if str(r["id"]).startswith("n")]
        tpids = [r["id"] for r in notes if str(r["id"]).startswith("t")]
        nh = {n.pid: n for n in (await db.execute(select(Note).where(Note.pid.in_(npids or [""])))).scalars()}
        th = {x.pid: x for x in (await db.execute(select(DataTable).where(DataTable.pid.in_(tpids or [""])))).scalars()}
        for r in notes:
            key = ty.ref_key(r)
            pid = r["id"]
            if pid.startswith("n"):
                n = nh.get(pid)
                if n is None:
                    out[key] = {"kind": "note", **gone}
                elif naccess.level(user, n) == "none" or (n.local_only and no_local):
                    out[key] = {"kind": "note", **hidden}
                elif n.deleted_at is not None:
                    out[key] = {"kind": "note", **gone, "id": pid}
                else:
                    out[key] = {"kind": "note", "state": "ok", "title": n.title or "Untitled", "id": pid,
                                "note_kind": n.kind}
            else:
                x = th.get(pid)
                if x is None:
                    out[key] = {"kind": "note", **gone}
                elif access.level(user, x) == "none" or (x.local_only and no_local):
                    out[key] = {"kind": "note", **hidden}
                elif x.deleted_at is not None:
                    out[key] = {"kind": "note", **gone, "id": pid}
                else:
                    out[key] = {"kind": "note", "state": "ok", "title": x.name or "Untitled table", "id": pid,
                                "note_kind": "table"}
    return out


async def check_link_targets(db: AsyncSession, user: User, refs: list[dict[str, Any]], *,
                             keep: set[str] | None = None) -> None:
    """A new link must point at something the writer can see (a reference already in the cell
    is kept as it is, visible or not)."""
    new = [r for r in refs if ty.ref_key(r) not in (keep or set())]
    if not new:
        return
    info = await titles(db, user, new)
    for r in new:
        st = info.get(ty.ref_key(r), {}).get("state")
        if st != "ok":
            raise _http(422, f"You cannot link to that {r.get('kind')}: it was not found")


def all_refs(rows: list[TableRow], cols: dict[str, Col]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        for k, v in (r.cells or {}).items():
            c = cols.get(k)
            if c is not None and c.type == "link" and isinstance(v, list):
                for ref in v:
                    out.setdefault(ty.ref_key(ref), ref)
    return list(out.values())


# ---------------------------------------------------------------- shapes


def column_out(c: TableColumn) -> dict[str, Any]:
    return {"id": c.col_id, "name": c.name, "type": c.type, "config": dict(c.config or {}),
            "required": bool(c.required), "width": c.width, "order": c.order,
            "deleted_at": iso(c.deleted_at)}


def view_out(v: TableView) -> dict[str, Any]:
    from mailr.tables.views import EMPTY_SPEC

    return {"id": v.view_id, "name": v.name, "spec": {**EMPTY_SPEC, **(v.spec or {})}, "default": bool(v.is_default),
            "order": v.order}


def row_out(r: TableRow, nm: dict[int, str], version: int | None) -> dict[str, Any]:
    return {"id": r.id, "cells": dict(r.cells or {}), "raw": dict(r.raw or {}), "order": r.order,
            "created_by": person(r.created_by, nm), "created_at": iso(r.created_at),
            "updated_by": r.updated_by or None, "updated_at": iso(r.updated_at), "version": version,
            "deleted_at": iso(r.deleted_at)}


def owner_out(t: DataTable, nm: dict[int, str]) -> dict[str, Any]:
    if t.owner_user_id is not None:
        return {"kind": "user", "id": t.owner_user_id, "name": nm.get(t.owner_user_id, "")}
    return {"kind": "group", "name": t.owner_group}


async def links_of(db: AsyncSession, table_ids: list[int], user: User | None = None) -> dict[int, list[dict[str, Any]]]:
    """The links of these tables as `user` sees them (notes/core.link_seen)."""
    from mailr.notes.core import link_out, link_seen
    from mailr.notes.models import NoteLink

    if not table_ids:
        return {}
    lns = list((await db.execute(select(NoteLink).where(NoteLink.table_id.in_(table_ids)).order_by(NoteLink.id))
                ).scalars())
    seen = await link_seen(db, user, lns)
    out: dict[int, list[dict[str, Any]]] = {}
    for ln in lns:
        out.setdefault(ln.table_id, []).append(link_out(ln, ln.id in seen))
    return out


async def state_of(db: AsyncSession, user: User, t: DataTable, *, create: bool = False):
    from mailr.notes.models import NoteViewerState

    st = await db.get(NoteViewerState, (user.id, "table", t.id))
    if st is None and create:
        st = NoteViewerState(user_id=user.id, item_kind="table", item_id=t.id, labels=[])
        db.add(st)
        await db.flush()
    return st


def base_out(t: DataTable, user: User, nm: dict[int, str], version: int, st: Any, counts: dict[str, int],
             links: list[dict[str, Any]]) -> dict[str, Any]:
    from mailr.notes.core import state_out

    purge = t.deleted_at + timedelta(days=TRASH_DAYS) if t.deleted_at else None
    return {"id": t.pid, "kind": "table", "name": t.name, "title": t.name, "description": t.description,
            "color": t.color, "local_only": bool(t.local_only), "primary_column": t.primary_column,
            "owner": owner_out(t, nm), "created_by": person(t.created_by, nm), "created_at": iso(t.created_at),
            "updated_at": iso(t.updated_at), "updated_by": t.updated_by or None, "deleted_at": iso(t.deleted_at),
            "purge_at": iso(purge), "version": version, "level": access.level(user, t), "state": state_out(st),
            "counts": counts, "links": links, "limits": {"rows": ty.LIMITS["rows"], "columns": ty.LIMITS["columns"]},
            "sharing": acl.summary(t, nm, user), "changed": acl.changed(user, t, st),
            "can_delete": access.can_delete(user, t),
            # Local models only: may this viewer turn it off (the owner only, notes/localonly.py)?
            "can_lower_local_only": localonly.can_lower(user, t)}


async def table_full(db: AsyncSession, user: User, t: DataTable) -> dict[str, Any]:
    """GET /tables/{id}: the table, its columns and views (rows come from GET …/rows)."""
    cols = await columns_of(db, t)
    views = await views_of(db, t)
    nm = await names(db, {t.owner_user_id, t.created_by} | acl.user_ids([t]))
    st = await state_of(db, user, t)
    n = await row_count(db, t)
    deleted_cols = [c for c in await columns_of(db, t, deleted=True) if c.deleted_at is not None]
    out = base_out(t, user, nm, await table_version(db, t), st,
                   {"rows": n, "columns": len(cols), "deleted_columns": len(deleted_cols)},
                   (await links_of(db, [t.id], user)).get(t.id, []))
    out["columns"] = [column_out(c) for c in cols]
    out["views"] = [view_out(v) for v in views]
    return out


async def table_cards(db: AsyncSession, user: User, tables: list[DataTable]) -> list[dict[str, Any]]:
    """Cards for the Notes grid: the table with its first rows in the default view's order."""
    from mailr.notes.models import NoteViewerState
    from mailr.tables import views as vw

    if not tables:
        return []
    ids = [x.id for x in tables]
    states = {s.item_id: s for s in (await db.execute(select(NoteViewerState).where(
        NoteViewerState.user_id == user.id, NoteViewerState.item_kind == "table",
        NoteViewerState.item_id.in_(ids)))).scalars()}
    counts = dict((await db.execute(select(TableRow.table_id, func.count()).where(
        TableRow.table_id.in_(ids), TableRow.deleted_at.is_(None)).group_by(TableRow.table_id))).all())
    allcols: dict[int, list[TableColumn]] = {}
    for c in (await db.execute(select(TableColumn).where(TableColumn.table_id.in_(ids), TableColumn.deleted_at.is_(None))
                               .order_by(TableColumn.order, TableColumn.col_id))).scalars():
        allcols.setdefault(c.table_id, []).append(c)
    defaults = {v.table_id: v for v in (await db.execute(select(TableView).where(
        TableView.table_id.in_(ids), TableView.is_default.is_(True)))).scalars()}
    lnks = await links_of(db, ids, user)
    nm = await names(db, {x.owner_user_id for x in tables} | {x.created_by for x in tables} | acl.user_ids(tables))
    out = []
    for x in tables:
        cols = allcols.get(x.id, [])
        cmap = cols_map(cols)
        spec = (defaults.get(x.id).spec if defaults.get(x.id) else {}) or {}
        hidden = set(spec.get("hidden") or [])
        shown = [c for c in cols if c.col_id not in hidden][:4]
        if x.primary_column in cmap and all(c.col_id != x.primary_column for c in shown):
            shown = [c for c in cols if c.col_id == x.primary_column] + shown[:3]
        rows = list((await db.execute(select(TableRow).where(TableRow.table_id == x.id, TableRow.deleted_at.is_(None))
                                      .limit(2000))).scalars()) if x.deleted_at is None else []
        rows = vw.sort_rows(vw.apply_filters(rows, spec.get("filters") or [], cmap, Ctx()), spec.get("sort") or [],
                            cmap, {})
        card = base_out(x, user, nm, 0, states.get(x.id), {"rows": int(counts.get(x.id, 0)), "columns": len(cols)},
                        lnks.get(x.id, []))
        card["version"] = None
        card["preview"] = {"columns": [{"id": c.col_id, "name": c.name, "type": c.type} for c in shown],
                           "rows": [[ty.plain(cmap[c.col_id], (r.cells or {}).get(c.col_id)) for c in shown]
                                    for r in rows[:PREVIEW_ROWS]],
                           "more": max(0, len(rows) - PREVIEW_ROWS)}
        out.append(card)
    return out


def primary_col(t: DataTable, cols: dict[str, Col]) -> Col | None:
    return cols.get(t.primary_column or "") or next(iter(cols.values()), None)


def row_title(t: DataTable, cols: dict[str, Col], r: TableRow) -> str:
    pc = primary_col(t, cols)
    return (ty.plain(pc, (r.cells or {}).get(pc.col_id)) if pc else "") or "Untitled row"
