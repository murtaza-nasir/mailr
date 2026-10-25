"""The table, column and row actions.

Every write takes the table as tables/access.py returned it, changes as little as it can (a row is
the unit of change), records live versions, the change events and the per-row history
(`core.Log`), and returns what it changed with an `undo`: the request that takes it back. Most
undos are `POST /tables/{id}/revert {batch}`, which reverses the changes of one batch
(tables/history.py) and returns the redo.

The routes in tables/routes.py are thin: they check access and the body, call one function here
and commit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import live, merge3
from mailr.i18n import ApiError
from mailr.models import ItemVersion, User
from mailr.notes import localonly, order
from mailr.notes.actions import check_color, ensure_labels, undo
from mailr.notes.models import NoteLink, NoteViewerState
from mailr.tables import access, core
from mailr.tables import types as ty
from mailr.tables import views as vw
from mailr.tables.core import Log
from mailr.tables.models import (
    DataTable,
    TableColumn,
    TableRow,
    TableRowRef,
    TableView,
)
from mailr.tables.types import CellError, Col, Ctx

LIMITS = ty.LIMITS
TEMPLATES = {
    "log": [{"name": "Date", "type": "date"}, {"name": "Item", "type": "text"}, {"name": "Amount", "type": "number"}],
    "inventory": [{"name": "Item", "type": "text"}, {"name": "Qty", "type": "number"},
                  {"name": "Location", "type": "text"}],
}
BIG_BATCH = 200  # rows; above this a batch sends one "rows changed" event, not one per row


def revert_undo(t: DataTable, batch: str, label: str) -> dict[str, Any]:
    return undo("POST", f"/tables/{t.pid}/revert", {"batch": batch}, label)


def too_many_rows() -> HTTPException:
    return HTTPException(422, f"This table has {LIMITS['rows']:,} rows, the most a table can hold. "
                              "Export it to a spreadsheet.")


def cell_error(e: CellError) -> HTTPException:
    return HTTPException(422, {"error": "cell", "column": e.column, "type": e.ctype, "detail": str(e)})


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


# ---------------------------------------------------------------- columns


def check_name(name: Any, *, what: str = "column", limit: int = LIMITS["column_name"]) -> str:
    n = " ".join(str(name or "").split())
    if not n:
        raise HTTPException(422, f"A {what} needs a name")
    if len(n) > limit:
        raise HTTPException(422, f"A {what} name can be at most {limit} characters")
    return n


def _unique_name(name: str, taken: list[str]) -> str:
    low = {x.lower() for x in taken}
    if name.lower() not in low:
        return name
    i = 2
    while f"{name} {i}".lower() in low:
        i += 1
    return f"{name} {i}"


async def _link_table(db: AsyncSession, user: User, t: DataTable, cfg: dict[str, Any]) -> dict[str, Any]:
    """A link column to rows names a table the user can see; "self" is this table."""
    if cfg.get("target") == "table":
        tbl = str(cfg.get("table") or "self")
        if tbl in ("self", t.pid):
            cfg = {**cfg, "table": t.pid}
        else:
            other = (await db.execute(select(DataTable).where(DataTable.pid == tbl))).scalar_one_or_none()
            if other is None or access.level(user, other) == "none":
                raise HTTPException(404, "the table to link to was not found")
            cfg = {**cfg, "table": other.pid}
    return cfg


async def _make_column(db: AsyncSession, user: User, t: DataTable, spec: dict[str, Any], taken: list[str],
                       ctx: Ctx, order_key: str) -> TableColumn:
    name = check_name(spec.get("name"))
    if name.lower() in {x.lower() for x in taken}:
        raise HTTPException(409, f"This table has a column called {name} already")
    ctype = spec.get("type") or "text"
    if ctype not in ty.TYPES:
        raise HTTPException(422, f"type must be one of {', '.join(ty.TYPES)}")
    cfg = await _link_table(db, user, t, dict(spec.get("config") or {}))
    if ctype in ("select", "multi_select") and spec.get("options") and not cfg.get("options"):
        cfg["options"] = spec["options"]
    try:
        cfg = ty.clean_config(Col("", name, ctype, cfg), user_currency=ctx.currency)
    except CellError as e:
        raise HTTPException(422, str(e)) from None
    width = spec.get("width")
    return TableColumn(table_id=t.id, col_id=ty.new_id("c"), name=name, type=ctype, config=cfg,
                       required=bool(spec.get("required")), width=int(width) if isinstance(width, int) else None,
                       order=order_key)


def _default_primary(cols: list[TableColumn]) -> str | None:
    live_cols = [c for c in cols if c.deleted_at is None]
    first_text = next((c for c in live_cols if c.type == "text"), None)
    return (first_text or (live_cols[0] if live_cols else None)).col_id if live_cols else None


# ---------------------------------------------------------------- tables


async def _first_grid_order(db: AsyncSession, user: User) -> str | None:
    return (await db.execute(select(func.min(NoteViewerState.order)).where(NoteViewerState.user_id == user.id))
            ).scalar_one_or_none()


async def create_table(db: AsyncSession, user: User, *, name: str = "", description: str = "",
                       columns: list[dict[str, Any]] | None = None, template: str | None = None, color: Any = None,
                       labels: list[str] | None = None, pinned: bool = False, local_only: bool = False,
                       rows: list[dict[str, Any]] | None = None, parse: bool = False, source: str = "edit",
                       label: str | None = None, by: dict[str, Any] | None = None,
                       group: str | None = None) -> dict[str, Any]:
    """ACT table.create: a table with its columns (a builder, a template, or one text column
    "Name"), a default view "All", and optional first rows. `by` names another author for the
    change log (an import: History shows "Import"). With `group`, it belongs to that group of the
    user's: every member edits it; its creator or an admin deletes it."""
    from mailr.notes.sharing import check_group_create

    await check_group_create(db, user, group)
    from mailr.intel import locality

    # New items of a person with "Local models only for me" are local-only too, so other people's
    # Ambrose keeps them local after a share.
    if not local_only and await locality.household(db) == "per_user" and locality.user_wants(user):
        local_only = True
    name = " ".join(str(name or "").split())
    if len(name) > LIMITS["name"]:
        raise HTTPException(422, f"A table name can be at most {LIMITS['name']} characters")
    if len(description or "") > LIMITS["description"]:
        raise HTTPException(422, f"A description can be at most {LIMITS['description']:,} characters")
    if template and template not in TEMPLATES:
        raise HTTPException(422, f"template must be one of {', '.join(TEMPLATES)}")
    specs = list(columns or TEMPLATES.get(template or "", None) or [{"name": "Name", "type": "text"}])
    if len(specs) > LIMITS["columns"]:
        raise HTTPException(422, f"A table can have at most {LIMITS['columns']} columns")
    ctx = await core.reader(db, user)
    t = DataTable(pid=core.new_pid(), owner_user_id=None if group else user.id, owner_group=group or None,
                  created_by=user.id, name=name or "Untitled table",
                  description=description or "", color=check_color(color), local_only=bool(local_only),
                  updated_by=by or core.by_of(user))
    db.add(t)
    await db.flush()
    made: list[TableColumn] = []
    keys = order.keys_between(None, None, len(specs))
    for spec, k in zip(specs, keys, strict=True):
        made.append(await _make_column(db, user, t, spec, [c.name for c in made], ctx, k))
    for c in made:
        db.add(c)
    t.primary_column = _default_primary(made)
    view = TableView(table_id=t.id, view_id=ty.new_id("v"), name="All", spec=dict(vw.EMPTY_SPEC), is_default=True,
                     order=order.FIRST)
    db.add(view)
    names_, _ = await ensure_labels(db, user, labels or [])
    first = await _first_grid_order(db, user)
    db.add(NoteViewerState(user_id=user.id, item_kind="table", item_id=t.id, labels=names_, pinned=bool(pinned),
                           order=order.before(first) if first else order.FIRST))
    await db.flush()
    log = Log(db, t, user, source, by=by)
    await log.schema("table", before=None, after=core.fields_of(t))
    for c in made:
        await log.schema("add", col_id=c.col_id, after=core.column_def(c))
    await log.schema("view_save", view_id=view.view_id, after=core.view_def(view))
    out_rows: list[dict[str, Any]] = []
    if rows:
        res = await add_rows(db, user, t, rows, parse=parse, source=source, log=log, defaults=False, notify=False)
        out_rows = res["rows"]
    await core.commit_table(db, t)
    if labels:
        from mailr.notes.core import notify_user

        await notify_user(db, user.id, "labels")
    from mailr.tables.embed import queue

    await queue(db, t.owner_user_id, t.owner_group)
    out = {"table": await core.table_full(db, user, t),
           "undo": undo("DELETE", f"/tables/{t.pid}", None, label or "Table created")}
    if rows:
        out["rows"] = out_rows
    return out


async def patch_table(db: AsyncSession, user: User, t: DataTable, changes: dict[str, Any], base_version: int | None
                      ) -> dict[str, Any]:
    """ACT table.rename, table.color, column.set_primary: name and description conflict when both
    sides changed them since `base_version`; colour, local-only and the primary column take the
    later write."""
    allowed = {"name", "description", *core.LWW_FIELDS}
    changes = {k: v for k, v in changes.items() if k in allowed}
    if not changes:
        raise ApiError(400, "common.nothing_to_change")
    if "name" in changes:
        changes["name"] = check_name(changes["name"], what="table", limit=LIMITS["name"])
    if "description" in changes:
        changes["description"] = str(changes["description"] or "")
        if len(changes["description"]) > LIMITS["description"]:
            raise HTTPException(422, f"A description can be at most {LIMITS['description']:,} characters")
    if "color" in changes:
        changes["color"] = check_color(changes["color"])
    if "local_only" in changes:
        changes["local_only"] = bool(changes["local_only"])
        localonly.check(user, t, changes)  # only the owner turns it off
    if "primary_column" in changes:
        c = await core.get_column(db, t, changes["primary_column"])
        changes["primary_column"] = c.col_id
    uid = access.live_uid(t)
    await live.lock(db, uid, "table", t.pid, t)  # and read the table again under it
    now = await core.table_live(db, t)
    head = await live.current(db, uid, "table", t.pid, now)
    merged = False
    if base_version is not None and int(base_version) < head:
        base = await live.at(db, uid, "table", t.pid, int(base_version))
        if base is None:  # pruned: no base to compare with (core/live.py unknown_base_conflicts)
            conflicts = live.unknown_base_conflicts({f: v for f, v in changes.items() if f in ("name", "description")},
                                                    now)
        else:
            conflicts = [f for f, v in changes.items() if f in ("name", "description")
                         and not merge3.same(base.get(f), now.get(f)) and not merge3.same(v, now.get(f))]
        if conflicts:
            row = await live.latest(db, uid, "table", t.pid)
            raise live.LiveConflict("table", t.pid, base, int(base_version), now, head, changes, conflicts,
                                    by=row.by if row is not None else None)
        merged = True
    before = {k: getattr(t, k) for k in changes}
    for k, v in changes.items():
        setattr(t, k, v)
    log = Log(db, t, user)
    if "primary_column" in changes and before["primary_column"] != changes["primary_column"]:
        await log.schema("primary", before={"primary_column": before["primary_column"]},
                         after={"primary_column": changes["primary_column"]})
    rest = {k: v for k, v in changes.items() if k != "primary_column" and before[k] != v}
    if rest:
        await log.schema("table", before={k: before[k] for k in rest}, after=rest)
    core.stamp(t, user)
    await core.commit_table(db, t)
    if "local_only" in changes:
        from mailr.tables.embed import queue

        await queue(db, t.owner_user_id, t.owner_group)
        if bool(before["local_only"]) != bool(t.local_only):
            await localonly.tell_owner(db, "table", t, user, bool(t.local_only))
    label = ("Colour changed" if set(changes) == {"color"} else "Primary column changed"
             if set(changes) == {"primary_column"} else "Table changed")
    return {"table": await core.table_full(db, user, t), "merged": merged,
            "undo": revert_undo(t, log.batch, label) if log.count else None}


async def trash(db: AsyncSession, user: User, t: DataTable) -> dict[str, Any]:
    """ACT table.delete: to Trash for 30 days, with its rows, views, shares and links."""
    if t.deleted_at is None:
        t.deleted_at = datetime.now(UTC)
        t.deleted_by = user.id
        await db.flush()
        await core.touch(db, t, deleted=True)
    return {"ok": True, "undo": undo("POST", f"/tables/{t.pid}/restore", None, "Moved to Trash")}


async def restore(db: AsyncSession, user: User, t: DataTable) -> dict[str, Any]:
    """ACT table.restore"""
    if t.deleted_at is not None:
        t.deleted_at = None
        t.deleted_by = None
        await db.flush()
        await core.touch(db, t)
    return {"table": await core.table_full(db, user, t), "undo": undo("DELETE", f"/tables/{t.pid}", None, "Restored")}


async def purge(db: AsyncSession, user: User, t: DataTable) -> dict[str, Any]:
    """ACT table.purge: delete forever, from Trash only. No undo."""
    if t.deleted_at is None:
        raise HTTPException(409, "Move the table to Trash first")
    await purge_now(db, t)
    return {"ok": True, "undo": None}


async def purge_now(db: AsyncSession, t: DataTable) -> None:
    uid = access.live_uid(t)
    aud = await access.audience(db, t)
    ids = [r[0] for r in (await db.execute(select(TableRow.id).where(TableRow.table_id == t.id))).all()]
    await db.execute(delete(ItemVersion).where(ItemVersion.user_id == uid, ItemVersion.kind == "table",
                                               ItemVersion.item_id == t.pid))
    for i in range(0, len(ids), 5000):
        await db.execute(delete(ItemVersion).where(ItemVersion.user_id == uid, ItemVersion.kind == "row",
                                                   ItemVersion.item_id.in_(ids[i:i + 5000])))
    await db.execute(delete(NoteViewerState).where(NoteViewerState.item_kind == "table", NoteViewerState.item_id == t.id))
    await db.execute(delete(NoteLink).where(NoteLink.table_id == t.id))
    from mailr.notes.sharing import drop_item

    await drop_item(db, "table", t.id)
    pid = t.pid
    await db.delete(t)
    await db.flush()
    from mailr.core.events import notify

    for u in aud:
        await notify(db, u, {"type": "item", "kind": "table", "id": pid, "table": pid, "version": None,
                             "deleted": True, "purged": True, "by": live.who(), "origin": live.origin_var.get()})


async def duplicate(db: AsyncSession, user: User, t: DataTable, *, with_rows: bool = True) -> dict[str, Any]:
    """ACT table.duplicate: the schema and views, with or without rows; links to rows of this same
    table point at the copy's rows."""
    st = await core.state_of(db, user, t)
    copy = DataTable(pid=core.new_pid(), owner_user_id=user.id, created_by=user.id, name=f"{t.name} (copy)"[:200],
                     description=t.description, color=t.color, local_only=t.local_only, primary_column=t.primary_column,
                     updated_by=core.by_of(user))
    db.add(copy)
    await db.flush()
    cols = await core.columns_of(db, t)
    for c in cols:
        cfg = dict(c.config or {})
        if cfg.get("target") == "table" and cfg.get("table") == t.pid:
            cfg["table"] = copy.pid
        db.add(TableColumn(table_id=copy.id, col_id=c.col_id, name=c.name, type=c.type, config=cfg,
                           required=c.required, width=c.width, order=c.order))
    for v in await core.views_of(db, t):
        db.add(TableView(table_id=copy.id, view_id=v.view_id, name=v.name, spec=dict(v.spec or {}),
                         is_default=v.is_default, order=v.order))
    first = await _first_grid_order(db, user)
    db.add(NoteViewerState(user_id=user.id, item_kind="table", item_id=copy.id,
                           labels=list(st.labels or []) if st else [], order=order.before(first) if first else order.FIRST))
    await db.flush()
    newcols = await core.columns_of(db, copy)
    log = Log(db, copy, user)
    await log.schema("table", after=core.fields_of(copy))
    for c in newcols:
        await log.schema("add", col_id=c.col_id, after=core.column_def(c))
    for v in await core.views_of(db, copy):
        await log.schema("view_save", view_id=v.view_id, after=core.view_def(v))
    if with_rows:
        rows = await core.rows_of(db, t)
        idmap = {r.id: core.uuid7() for r in rows}
        cmap = core.cols_map(newcols)
        made = []
        for r in rows:
            cells = dict(r.cells or {})
            for k, v in list(cells.items()):
                c = cmap.get(k)
                if c is not None and c.type == "link" and (c.config or {}).get("table") == copy.pid:
                    cells[k] = [{**x, "table": copy.pid, "row": idmap.get(x.get("row"), x.get("row"))} for x in v]
            nr = TableRow(id=idmap[r.id], table_id=copy.id, cells=cells, raw=dict(r.raw or {}), order=r.order,
                          created_by=user.id, updated_by=core.by_of(user))
            await core.derive(db, copy, nr, cmap)
            db.add(nr)
            made.append(nr)
        await db.flush()
        await log.rows([(r.id, "add", None, core.row_state(r)) for r in made])
    await core.commit_table(db, copy)
    return {"table": await core.table_full(db, user, copy), "undo": undo("DELETE", f"/tables/{copy.pid}", None,
                                                                          "Copy created")}


async def set_state(db: AsyncSession, user: User, t: DataTable, body: dict[str, Any]) -> dict[str, Any]:
    """ACT table.pin, table.archive, table.label: the viewer's own view of the table (and its
    place in the Notes grid)."""
    from mailr.notes.core import notify_user, state_out
    from mailr.tables import grid

    st = await core.state_of(db, user, t, create=True)
    before: dict[str, Any] = {}
    created: list[str] = []
    label = "Changed"
    if body.get("pinned") is not None:
        before["pinned"] = st.pinned
        st.pinned = bool(body["pinned"])
        label = "Pinned" if st.pinned else "Unpinned"
        if st.pinned and st.archived:
            before["archived"] = True
            st.archived = False
    if body.get("archived") is not None:
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
    if body.get("order_key"):
        before["order_key"] = st.order
        st.order = str(body["order_key"])[:64]
        label = "Moved"
    elif body.get("before") or body.get("after"):
        before["order_key"] = st.order
        st.order = await grid.key(db, user, ("table", t.id), before=body.get("before"), after=body.get("after"))
        label = "Moved"
    if "calendar_column" in body:  # ACT table.on_calendar: this viewer's calendar shows the column's dates
        from mailr.tables.calendar import check_column

        before["calendar_column"] = st.calendar_column
        st.calendar_column = await check_column(db, t, body["calendar_column"])
        label = "Dates on your calendar" if st.calendar_column else "Dates off your calendar"
        await notify_user(db, user.id, "calendar")
    if body.get("seen_version") is not None:
        st.seen_version = int(body["seen_version"])
    if body.get("seen"):  # opened now: the "changed since you looked" dot goes
        st.seen_at = datetime.now(UTC)
    st.updated_at = datetime.now(UTC)
    await db.flush()
    await notify_user(db, user.id, "state")
    if created:
        await notify_user(db, user.id, "labels")
    return {"state": state_out(st), "created_labels": created,
            "undo": undo("PUT", f"/tables/{t.pid}/state", before, label) if before else None}


# ---------------------------------------------------------------- links (table.link)


async def link(db: AsyncSession, user: User, t: DataTable, target_kind: str, ref: dict[str, Any]) -> dict[str, Any]:
    """ACT table.link: to a conversation, an event, a task or a recording the user can see."""
    from mailr.notes.actions import TARGET_KINDS, _target
    from mailr.notes.core import link_out

    if target_kind not in TARGET_KINDS:
        raise HTTPException(422, f"target_kind must be one of {', '.join(TARGET_KINDS)}")
    key, title, clean = await _target(db, user, target_kind, ref)
    dup = (await db.execute(select(NoteLink).where(NoteLink.table_id == t.id, NoteLink.target_key == key))
           ).scalar_one_or_none()
    if dup is not None:
        return {"link": link_out(dup), "undo": None}
    ln = NoteLink(table_id=t.id, target_kind=target_kind, target_ref=clean, target_key=key, title=title[:300],
                  created_by=user.id)
    db.add(ln)
    await db.flush()
    await core.touch(db, t)
    return {"link": link_out(ln), "undo": undo("DELETE", f"/tables/{t.pid}/links/{ln.id}", None, "Linked")}


async def unlink(db: AsyncSession, user: User, t: DataTable, link_id: int) -> dict[str, Any]:
    """ACT table.unlink"""
    ln = await db.get(NoteLink, int(link_id))
    if ln is None or ln.table_id != t.id:
        raise HTTPException(404, "link not found")
    body = {"target_kind": ln.target_kind, "target_ref": ln.target_ref}
    await db.delete(ln)
    await db.flush()
    await core.touch(db, t)
    return {"ok": True, "undo": undo("POST", f"/tables/{t.pid}/links", body, "Link removed")}


# ---------------------------------------------------------------- columns (column.*)


async def add_column(db: AsyncSession, user: User, t: DataTable, spec: dict[str, Any], *, before: str | None = None,
                     after: str | None = None, source: str = "edit") -> dict[str, Any]:
    """ACT column.add: at the end, or left (`before`) or right (`after`) of a column."""
    cols = await core.columns_of(db, t)
    if len(cols) >= LIMITS["columns"]:
        raise ApiError(422, "tables.column_limit", max=LIMITS["columns"])
    keys = [c.order for c in cols]
    ids = [c.col_id for c in cols]
    if after in ids:
        k = ids.index(after)
        lo, hi = keys[k], keys[k + 1] if k + 1 < len(keys) else None
    elif before in ids:
        k = ids.index(before)
        lo, hi = keys[k - 1] if k > 0 else None, keys[k]
    else:
        lo, hi = (keys[-1] if keys else None), None
    key = order.after(lo) if hi is None else order.between(lo, hi)
    ctx = await core.reader(db, user)
    deleted_names = [c.name for c in await core.columns_of(db, t, deleted=True) if c.deleted_at is not None]
    c = await _make_column(db, user, t, spec, [x.name for x in cols], ctx, key)
    if c.name.lower() in {n.lower() for n in deleted_names}:
        # A deleted column keeps its name until it is purged; the new one stays distinct.
        c.name = _unique_name(c.name, [x.name for x in cols] + deleted_names)
    db.add(c)
    await db.flush()
    if t.primary_column is None:
        t.primary_column = c.col_id
    log = Log(db, t, user, source)
    await log.schema("add", col_id=c.col_id, after=core.column_def(c))
    core.stamp(t, user)
    await core.commit_table(db, t)
    return {"column": core.column_out(c), "undo": revert_undo(t, log.batch, f"Added column {c.name}")}


async def _all_rows(db: AsyncSession, t: DataTable) -> list[TableRow]:
    return list((await db.execute(select(TableRow).where(TableRow.table_id == t.id)
                                  .order_by(TableRow.order, TableRow.id))).scalars())


async def patch_column(db: AsyncSession, user: User, t: DataTable, col_id: str, body: dict[str, Any], *,
                       source: str = "edit") -> dict[str, Any]:
    """ACT column.rename, column.retype, column.options, column.config, column.reorder (and the
    width, which is the grid's own). One change per request; a retype with `preview: true` only
    counts the cells that convert."""
    c = await core.get_column(db, t, col_id)
    cols = await core.columns_of(db, t)
    log = Log(db, t, user, source)
    label = "Column changed"
    result: dict[str, Any] = {}
    if body.get("type") and body["type"] != c.type:
        res = await _retype(db, user, t, c, body["type"], body.get("config"), log, preview=bool(body.get("preview")))
        if body.get("preview"):
            return res
        result.update(res)
        label = f"Changed {c.name} to {ty.LABELS[c.type].lower()}"
    elif body.get("preview"):
        return {"preview": {"convert": 0, "fail": 0, "examples": []}}
    if body.get("name") is not None:
        new = check_name(body["name"])
        if body.get("base_name") is not None and body["base_name"] != c.name and new != c.name:
            raise HTTPException(409, {"error": "conflict", "conflict": True, "kind": "column", "fields": ["name"],
                                      "detail": f"This column was renamed to {c.name} while you edited it.",
                                      "theirs": {"content": {"name": c.name}}, "mine": {"content": {"name": new}}})
        if new != c.name:
            others = [x.name for x in await core.columns_of(db, t, deleted=True) if x.col_id != c.col_id]
            if new.lower() in {x.lower() for x in others}:
                raise HTTPException(409, f"This table has a column called {new} already")
            await log.schema("rename", col_id=c.col_id, before={"name": c.name}, after={"name": new})
            c.name = new
            label = f"Renamed to {new}"
    if "options" in body and body["options"] is not None and "type" not in body:
        await _options(db, user, t, c, body["options"], body.get("merge") or {}, log)
        label = f"Options of {c.name} changed"
    cfg_keys = {"decimals", "format", "currency", "long", "multiple"}
    if (body.get("config") is not None and "type" not in body) or "required" in body:
        before = {"config": dict(c.config or {}), "required": bool(c.required)}
        if body.get("config") is not None and "type" not in body:
            new_cfg = {**(c.config or {}), **{k: v for k, v in body["config"].items() if k in cfg_keys}}
            if c.type == "link" and (c.config or {}).get("multiple") and not new_cfg.get("multiple"):
                many = sum(1 for r in await core.rows_of(db, t) if len((r.cells or {}).get(c.col_id) or []) > 1)
                if many:
                    raise HTTPException(409, f"{_plural(many, 'cell')} of {c.name} hold several links. "
                                             "Remove the extra links first.")
            try:
                c.config = ty.clean_config(Col(c.col_id, c.name, c.type, new_cfg),
                                           user_currency=(await core.reader(db, user)).currency)
            except CellError as e:
                raise HTTPException(422, str(e)) from None
        if "required" in body and body["required"] is not None:
            c.required = bool(body["required"])
        after = {"config": dict(c.config or {}), "required": bool(c.required)}
        if after != before:
            await log.schema("config", col_id=c.col_id, before=before, after=after)
            label = f"Settings of {c.name} changed"
    if body.get("before") or body.get("after") or body.get("direction"):
        rest = [x for x in cols if x.col_id != c.col_id]
        ids = [x.col_id for x in rest]
        tgt_before, tgt_after = body.get("before"), body.get("after")
        if body.get("direction") in ("left", "right"):
            k = [x.col_id for x in cols].index(c.col_id)
            if body["direction"] == "left" and k > 0:
                tgt_before = cols[k - 1].col_id
            elif body["direction"] == "right" and k + 1 < len(cols):
                tgt_after = cols[k + 1].col_id
            else:
                return {"column": core.column_out(c), "undo": None}
        if tgt_after in ids:
            k = ids.index(tgt_after)
            lo, hi = rest[k].order, rest[k + 1].order if k + 1 < len(rest) else None
        elif tgt_before in ids:
            k = ids.index(tgt_before)
            lo, hi = rest[k - 1].order if k > 0 else None, rest[k].order
        else:
            raise HTTPException(404, "column not found")
        new_key = order.after(lo) if hi is None else order.between(lo, hi)
        if order.too_long(new_key):
            for x, nk in zip(rest, order.spread(len(rest) + 1), strict=False):
                x.order = nk
            return await patch_column(db, user, t, col_id, body, source=source)
        await log.schema("reorder", col_id=c.col_id, before={"order": c.order}, after={"order": new_key})
        c.order = new_key
        label = f"Moved {c.name}"
    if "width" in body and body["width"] is not None:
        c.width = max(40, min(1200, int(body["width"])))
    await db.flush()
    core.stamp(t, user)
    await core.commit_table(db, t)
    return {**result, "column": core.column_out(c),
            "undo": revert_undo(t, log.batch, label) if log.count else None}


async def _options(db: AsyncSession, user: User, t: DataTable, c: TableColumn, options: list[Any],
                   merge: dict[str, str], log: Log) -> None:
    """ACT column.options: the new option list (add, rename, recolour, reorder). An option left
    out is deleted: its cells become empty (or the option it is merged into), kept in history."""
    if c.type not in ("select", "multi_select"):
        raise HTTPException(422, "only select and multi-select columns have options")
    before = dict(c.config or {})
    try:
        cfg = ty.clean_config(Col(c.col_id, c.name, c.type, {**before, "options": options}))
    except CellError as e:
        raise HTTPException(422, str(e)) from None
    keep = {o["id"] for o in cfg["options"]}
    gone = {o["id"] for o in before.get("options") or []} - keep
    mapping = {k: v for k, v in (merge or {}).items() if k in gone and v in keep}
    c.config = cfg
    await log.schema("options", col_id=c.col_id, before={"config": before}, after={"config": cfg})
    if gone:
        cmap = core.cols_map(await core.columns_of(db, t))
        changes = []
        for r in await core.rows_of(db, t):
            v = (r.cells or {}).get(c.col_id)
            if ty.is_empty(v):
                continue
            ids = v if isinstance(v, list) else [v]
            if not set(ids) & gone:
                continue
            new_ids = []
            for i in ids:
                j = mapping.get(i, None if i in gone else i)
                if j is not None and j not in new_ids:
                    new_ids.append(j)
            new_v: Any = ([x for x in (o["id"] for o in cfg["options"]) if x in new_ids] or None) \
                if c.type == "multi_select" else (new_ids[0] if new_ids else None)
            cells = dict(r.cells or {})
            if new_v is None:
                cells.pop(c.col_id, None)
            else:
                cells[c.col_id] = new_v
            r.cells = cells
            await core.derive(db, t, r, cmap)
            changes.append((r.id, "edit", {"cells": {c.col_id: v}}, {"cells": {c.col_id: new_v}}))
        await log.rows(changes)
        await db.flush()
        await _rows_changed(db, t, [x[0] for x in changes])
    else:
        # Renamed options change the rows' search text.
        cmap = core.cols_map(await core.columns_of(db, t))
        for r in await core.rows_of(db, t):
            if not ty.is_empty((r.cells or {}).get(c.col_id)):
                r.search_text = core.search_text(cmap, r.cells or {}, r.raw or {})


async def _retype(db: AsyncSession, user: User, t: DataTable, c: TableColumn, new_type: str,
                  config: dict[str, Any] | None, log: Log, *, preview: bool) -> dict[str, Any]:
    """ACT column.retype: every cell converted; a value that does
    not convert moves to `raw` and stays visible with a warning mark. The change keeps every
    value before and after, so the undo puts the old type and values back."""
    if new_type not in ty.TYPES:
        raise HTTPException(422, f"type must be one of {', '.join(ty.TYPES)}")
    if c.type == "link" or new_type == "link":
        raise HTTPException(422, "A link column cannot change its type, and a column cannot become a link column")
    ctx = await core.reader(db, user)
    src = ty.col_of(c)
    try:
        cfg = ty.retype_config(src, new_type, config)
    except CellError as e:
        raise HTTPException(422, str(e)) from None
    dst = Col(c.col_id, c.name, new_type, cfg)
    new_opts: list[dict[str, Any]] = []
    rows = await _all_rows(db, t)
    conv, fail, examples = 0, 0, []
    out: dict[str, tuple[Any, str | None]] = {}
    for r in rows:
        v = (r.cells or {}).get(c.col_id)
        raw = (r.raw or {}).get(c.col_id)
        if ty.is_empty(v) and not raw:
            continue
        res = ty.convert(src, dst, v, raw, ctx, new_opts)
        out[r.id] = (res.value, res.raw)
        if res.raw:
            fail += 1
            if len(examples) < 5:
                examples.append(res.raw)
        elif r.deleted_at is None:
            conv += 1
    if preview:
        return {"preview": {"convert": conv, "fail": fail, "examples": examples, "type": new_type,
                            "new_options": [o["name"] for o in new_opts]}}
    before_def = core.column_def(c)
    c.type, c.config = new_type, dst.config
    values: dict[str, dict[str, Any]] = {"before": {}, "after": {}}
    cmap = core.cols_map(await core.columns_of(db, t))
    cmap[c.col_id] = dst
    for r in rows:
        if r.id not in out:
            continue
        nv, nraw = out[r.id]
        values["before"][r.id] = [(r.cells or {}).get(c.col_id), (r.raw or {}).get(c.col_id)]
        values["after"][r.id] = [nv, nraw]
        cells, rawd = dict(r.cells or {}), dict(r.raw or {})
        cells.pop(c.col_id, None)
        rawd.pop(c.col_id, None)
        if nv is not None:
            cells[c.col_id] = nv
        if nraw:
            rawd[c.col_id] = nraw
        r.cells, r.raw = cells, rawd
        r.search_text = core.search_text(cmap, cells, rawd)
    await log.schema("retype", col_id=c.col_id, before=before_def, after=core.column_def(c), values=values)
    await db.flush()
    await _rows_changed(db, t, list(out))
    return {"converted": conv, "kept_as_text": fail}


async def delete_column(db: AsyncSession, user: User, t: DataTable, col_id: str, *, source: str = "edit") -> dict[str, Any]:
    """ACT column.delete: restorable for 30 days with its values. The primary column moves to the
    next column; the last column stays."""
    c = await core.get_column(db, t, col_id)
    cols = await core.columns_of(db, t)
    if len(cols) <= 1:
        raise HTTPException(409, "A table needs at least one column")
    rows = await core.rows_of(db, t)
    values = {r.id: [(r.cells or {}).get(c.col_id), (r.raw or {}).get(c.col_id)] for r in rows
              if not ty.is_empty((r.cells or {}).get(c.col_id)) or (r.raw or {}).get(c.col_id)}
    log = Log(db, t, user, source)
    c.deleted_at = datetime.now(UTC)
    await log.schema("delete", col_id=c.col_id, before=core.column_def(c) | {"deleted": False},
                     after={"deleted": True}, values={"before": values})
    if t.primary_column == c.col_id:
        rest = [x for x in cols if x.col_id != c.col_id]
        new = _default_primary(rest)
        await log.schema("primary", before={"primary_column": c.col_id}, after={"primary_column": new})
        t.primary_column = new
    await db.flush()
    core.stamp(t, user)
    await core.commit_table(db, t)
    await core.touch(db, t, rows=True)
    return {"ok": True, "values": len(values),
            "undo": revert_undo(t, log.batch, f"Deleted column {c.name}")}


async def restore_column(db: AsyncSession, user: User, t: DataTable, col_id: str, *, source: str = "edit",
                         log: Log | None = None) -> dict[str, Any]:
    """ACT column.restore: back with its values; rows added since have empty cells."""
    c = await core.get_column(db, t, col_id, deleted_ok=True)
    if c.deleted_at is None:
        return {"column": core.column_out(c), "undo": None}
    cols = await core.columns_of(db, t)
    if len(cols) >= LIMITS["columns"]:
        raise ApiError(422, "tables.column_limit", max=LIMITS["columns"])
    log = log or Log(db, t, user, source)
    taken = [x.name for x in cols]
    if c.name.lower() in {x.lower() for x in taken}:
        old = c.name
        c.name = _unique_name(f"{old} (restored)", taken)
        await log.schema("rename", col_id=c.col_id, before={"name": old}, after={"name": c.name})
    c.deleted_at = None
    await log.schema("restore", col_id=c.col_id, before={"deleted": True}, after=core.column_def(c))
    await db.flush()
    core.stamp(t, user)
    await core.commit_table(db, t)
    await core.touch(db, t, rows=True)
    return {"column": core.column_out(c), "undo": revert_undo(t, log.batch, f"Restored column {c.name}")}


# ---------------------------------------------------------------- views


async def save_view(db: AsyncSession, user: User, t: DataTable, *, name: str, spec: dict[str, Any] | None,
                    view_id: str | None = None, default: bool | None = None, source: str = "edit") -> dict[str, Any]:
    """ACT table.view_save: the current settings as a new view (or over `view_id`)."""
    name = check_name(name, what="view", limit=LIMITS["view_name"])
    cmap = core.cols_map(await core.columns_of(db, t))
    try:
        clean = vw.clean_spec(spec, cmap, t.primary_column)
    except vw.ViewError as e:
        raise HTTPException(422, str(e)) from None
    views = await core.views_of(db, t)
    log = Log(db, t, user, source)
    if view_id:
        v = next((x for x in views if x.view_id == view_id), None)
        if v is None:
            raise ApiError(404, "tables.view_not_found")
        before = core.view_def(v)
        v.name, v.spec, v.updated_at = name, clean, datetime.now(UTC)
        label = f"Saved view {name}"
    else:
        if len(views) >= LIMITS["views"]:
            raise HTTPException(422, f"A table can have at most {LIMITS['views']} views")
        before = None
        v = TableView(table_id=t.id, view_id=ty.new_id("v"), name=name, spec=clean, is_default=not views,
                      order=order.after(max((x.order for x in views), default=None)))
        db.add(v)
        label = f"Made view {name}"
    if default:
        for x in views:
            if x.is_default and x.view_id != v.view_id:
                await log.schema("view_save", view_id=x.view_id, before=core.view_def(x),
                                 after={**core.view_def(x), "is_default": False})
                x.is_default = False
        v.is_default = True
    await db.flush()
    await log.schema("view_save", view_id=v.view_id, before=before, after=core.view_def(v))
    await core.commit_table(db, t)
    return {"view": core.view_out(v), "undo": revert_undo(t, log.batch, label)}


async def edit_view(db: AsyncSession, user: User, t: DataTable, view_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """ACT table.view_edit (edit level): sort, filters, group, hidden columns, footers, phone
    columns, the name and the default. Merges into the saved spec key by key."""
    views = await core.views_of(db, t)
    v = next((x for x in views if x.view_id == view_id), None)
    if v is None:
        raise ApiError(404, "tables.view_not_found")
    spec = dict(v.spec or {})
    for k in ("sort", "filters", "group_by", "hidden", "footers", "phone_columns"):
        if k in (body.get("spec") or {}):
            spec[k] = body["spec"][k]
    return await save_view(db, user, t, name=body.get("name") or v.name, spec=spec, view_id=v.view_id,
                           default=body.get("default"))


async def delete_view(db: AsyncSession, user: User, t: DataTable, view_id: str, *, source: str = "edit") -> dict[str, Any]:
    """ACT table.view_delete: not the last view; the next view becomes the default."""
    views = await core.views_of(db, t)
    v = next((x for x in views if x.view_id == view_id), None)
    if v is None:
        raise ApiError(404, "tables.view_not_found")
    if len(views) == 1:
        raise HTTPException(409, "A table keeps at least one view")
    log = Log(db, t, user, source)
    await log.schema("view_delete", view_id=v.view_id, before=core.view_def(v))
    if v.is_default:
        nxt = next(x for x in views if x.view_id != v.view_id)
        await log.schema("view_save", view_id=nxt.view_id, before=core.view_def(nxt),
                         after={**core.view_def(nxt), "is_default": True})
        nxt.is_default = True
    await db.delete(v)
    await db.flush()
    await core.commit_table(db, t)
    return {"ok": True, "undo": revert_undo(t, log.batch, f"Deleted view {v.name}")}


# ---------------------------------------------------------------- rows


def _apply(r: TableRow, col: Col, value: Any, raw: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Set one cell (value, or text kept in raw). Returns (before, after) partials for the log."""
    cells, rawd = dict(r.cells or {}), dict(r.raw or {})
    b = {"cells": {col.col_id: cells.get(col.col_id)}}
    if col.col_id in rawd or raw:
        b["raw"] = {col.col_id: rawd.get(col.col_id)}
    cells.pop(col.col_id, None)
    rawd.pop(col.col_id, None)
    if value is not None:
        cells[col.col_id] = value
    if raw:
        rawd[col.col_id] = raw
    r.cells, r.raw = cells, rawd
    a: dict[str, Any] = {"cells": {col.col_id: value}}
    if "raw" in b:
        a["raw"] = {col.col_id: raw or None}
    return b, a


def _merge_partials(into: dict[str, Any], part: dict[str, Any], *, first: bool) -> None:
    for k in ("cells", "raw"):
        if k in part:
            d = into.setdefault(k, {})
            for col, v in part[k].items():
                if first:
                    d.setdefault(col, v)
                else:
                    d[col] = v


def read_cell(col: Col, value: Any, ctx: Ctx, *, parse: bool, new_options: list[dict[str, Any]] | None = None
              ) -> tuple[Any, str | None]:
    """A cell value from a client: typed (CellError when it does not fit), or text to read when
    `parse` (text that does not convert comes back as raw)."""
    if parse and (isinstance(value, str) or value is None):
        return ty.parse_text(col, value, ctx, new_options=new_options)
    return ty.check(col, value), None


async def _place_rows(db: AsyncSession, t: DataTable, n: int, *, after: str | None = None,
                      before: str | None = None) -> list[str]:
    """n manual-order keys: after or before a row, or at the end."""
    rows = sorted(await core.rows_of(db, t), key=lambda r: (r.order, r.id))
    keys = [r.order for r in rows]
    ids = [r.id for r in rows]
    if after in ids:
        k = ids.index(after)
        lo, hi = keys[k], keys[k + 1] if k + 1 < len(keys) else None
    elif before in ids:
        k = ids.index(before)
        lo, hi = keys[k - 1] if k > 0 else None, keys[k]
    else:
        lo, hi = (keys[-1] if keys else None), None
    out = [order.after(lo)] if n == 1 and hi is None else order.keys_between(lo, hi, n)
    if any(order.too_long(k) for k in out):
        for r, k in zip(rows, order.spread(len(rows)), strict=False):
            r.order = k
        await db.flush()
        return await _place_rows(db, t, n, after=after, before=before)
    return out


async def _rows_changed(db: AsyncSession, t: DataTable, ids: list[str], *, deleted: list[str] = ()) -> None:
    """Versions and events for changed rows: one event per row for a small batch, one "rows"
    event for a big one."""
    from mailr.core.events import notify

    aud = await access.audience(db, t)
    if len(ids) + len(deleted) > BIG_BATCH:
        await core.touch(db, t, rows=True)
        return
    for rid in ids:
        r = await db.get(TableRow, rid)
        if r is None:
            continue
        if r.deleted_at is not None:
            deleted = [*deleted, rid]
            continue
        await core.commit_row(db, t, r, aud)
    for rid in deleted:
        for u in aud:
            await notify(db, u, {"type": "item", "kind": "row", "id": rid, "table": t.pid, "version": None,
                                 "deleted": True, "by": live.who(), "origin": live.origin_var.get()})


def date_defaults(cols: dict[str, Col], ctx: Ctx) -> dict[str, Any]:
    """Date columns named "Date" default to today in a new row (row.add)."""
    return {k: ctx.today.isoformat() for k, c in cols.items() if c.type == "date" and c.name.strip().lower() == "date"}


async def add_rows(db: AsyncSession, user: User, t: DataTable, rows_in: list[dict[str, Any]], *,
                   after: str | None = None, before: str | None = None, parse: bool = False, source: str = "edit",
                   log: Log | None = None, defaults: bool = True, view_filters: list[dict[str, Any]] | None = None,
                   notify: bool = True, label: str | None = None) -> dict[str, Any]:
    """ACT row.add: new rows, typed values checked (or text read when `parse`). A row id the
    client made that is here already was sent twice: it is returned, not added again. The
    active filters' values and today's date fill a new row's empty cells."""
    cols_l = await core.columns_of(db, t)
    cols = core.cols_map(cols_l)
    ctx = await core.reader(db, user)
    count = await core.row_count(db, t)
    log = log or Log(db, t, user, source)
    made: list[TableRow] = []
    existing: list[TableRow] = []
    new_opts: dict[str, list[dict[str, Any]]] = {}
    fills = vw.filled_by_filters(view_filters or [], cols, ctx) if view_filters else {}
    if defaults:
        fills = {**date_defaults(cols, ctx), **fills}
    pending: list[TableRow] = []
    for ri in rows_in:
        rid = None
        if ri.get("id"):
            rid = core.valid_uuid(ri["id"])
            if not rid:
                raise HTTPException(422, "a row id must be a UUID")
            prev = await db.get(TableRow, rid)
            if prev is not None:
                if prev.table_id != t.id:
                    raise HTTPException(409, "that row id belongs to another table")
                existing.append(prev)
                continue
        cells: dict[str, Any] = {}
        raw: dict[str, Any] = {}
        given = ri.get("cells") or {}
        if not isinstance(given, dict):
            raise HTTPException(422, "cells must be an object of column ids to values")
        for k, v in given.items():
            col = cols.get(k)
            if col is None:
                raise HTTPException(422, f"unknown column {k!r}")
            nl = new_opts.setdefault(k, []) if parse and col.type in ("select", "multi_select") else None
            try:
                val, rawtext = read_cell(col, v, ctx, parse=parse, new_options=nl)
            except CellError as e:
                raise cell_error(e) from None
            if val is not None:
                cells[k] = val
            if rawtext:
                raw[k] = rawtext
        for k, v in (ri.get("raw") or {}).items():
            if k in cols and v and k not in cells:
                raw[k] = str(v)[:LIMITS["cell"]]
        for k, v in fills.items():
            if k not in given and k in cols:
                cells.setdefault(k, v)
        links = [ref for k, v in cells.items() if cols[k].type == "link" for ref in v]
        if links:
            await core.check_link_targets(db, user, links)
        if count + len(pending) >= LIMITS["rows"]:
            raise too_many_rows()
        r = TableRow(id=rid or core.uuid7(), table_id=t.id, cells=cells, raw=raw, order="", created_by=user.id,
                     updated_by=core.by_of(user))
        if ri.get("import_key"):
            r.import_key = str(ri["import_key"])[:100]
        pending.append(r)
    if any(new_opts.values()):
        for c in cols_l:
            if new_opts.get(c.col_id):
                before_cfg = dict(c.config or {})
                c.config = cols[c.col_id].config
                await log.schema("options", col_id=c.col_id, before={"config": before_cfg}, after={"config": c.config})
    if pending:
        keys = await _place_rows(db, t, len(pending), after=after, before=before)
        for r, k in zip(pending, keys, strict=True):
            r.order = k
            await core.derive(db, t, r, cols)
            db.add(r)
        await db.flush()
        made = pending
        await log.rows([(r.id, "add", None, core.row_state(r)) for r in made])
    core.stamp(t, user)
    nm = await core.names(db, {user.id})
    if any(new_opts.values()):
        await core.commit_table(db, t)
    vers: dict[str, int] = {}
    if notify and made:
        if len(made) > BIG_BATCH:
            await core.touch(db, t, rows=True)
        else:
            aud = await access.audience(db, t)
            for r in made:
                vers[r.id] = await core.commit_row(db, t, r, aud)
    raw_count = sum(len(r.raw or {}) for r in made)
    n = len(made)
    lbl = label or ("Added a row" if n == 1 else f"Added {_plural(n, 'row')}")
    return {"rows": [core.row_out(r, nm, vers.get(r.id)) for r in made + existing], "added": n,
            "kept_as_text": raw_count, "batch": log.batch,
            "undo": revert_undo(t, log.batch, lbl) if n else None}


def _long(col: Col) -> bool:
    return col.type == "text" and bool((col.config or {}).get("long"))


async def edit_row(db: AsyncSession, user: User, t: DataTable, row_id: str, cells_in: dict[str, Any],
                   base_version: int | None, *, parse: bool = False, restore: bool = False, source: str = "edit",
                   log: Log | None = None, notify: bool = True, cols: dict[str, Col] | None = None,
                   ctx: Ctx | None = None) -> dict[str, Any]:
    """ACT row.edit: changed cells only. From an older version, different cells combine; the same
    cell changed two ways is a conflict on that cell (a long text cell merges by lines). A row
    deleted meanwhile answers 409 "deleted"; `restore: true` brings it back with the edit."""
    if not isinstance(cells_in, dict) or not cells_in:
        raise ApiError(400, "common.nothing_to_change")
    r = await core.get_row(db, t, row_id, deleted_ok=restore)
    cols = cols if cols is not None else core.cols_map(await core.columns_of(db, t))
    ctx = ctx or await core.reader(db, user)
    log = log or Log(db, t, user, source)
    if r.deleted_at is not None:
        await restore_rows(db, user, t, [r.id], log=log, notify=False)
    changes: dict[str, tuple[Any, str | None]] = {}
    new_opts: dict[str, list[dict[str, Any]]] = {}
    for k, v in cells_in.items():
        col = cols.get(k)
        if col is None:
            raise HTTPException(422, f"unknown column {k!r}")
        nl = new_opts.setdefault(k, []) if parse and col.type in ("select", "multi_select") else None
        try:
            changes[k] = read_cell(col, v, ctx, parse=parse, new_options=nl)
        except CellError as e:
            raise cell_error(e) from None
    uid = access.live_uid(t)
    merged = False
    head: int | None = None
    if base_version is not None:
        await live.lock(db, uid, "row", r.id, r)  # and read the row again under it
        now = core.row_live(r)
        head = await live.current(db, uid, "row", r.id, now)
    if base_version is not None and head is not None and 0 < int(base_version) < head:
        base = await live.at(db, uid, "row", r.id, int(base_version))
        if base is None:
            # The base version was pruned: a cell that differs from the stored row is refused,
            # never stored over another writer's newer value (it used to skip the check).
            cur = now.get("cells") or {}
            pruned = [f"cells.{k}" for k in
                      live.unknown_base_conflicts({k: v for k, (v, _) in changes.items()}, cur)]
            if pruned:
                row_v = await live.latest(db, uid, "row", r.id)
                mine = {"cells": {k: v for k, (v, _) in changes.items()}}
                raise live.LiveConflict("row", r.id, None, None, now, head, mine, pruned,
                                        by=row_v.by if row_v is not None else None)
            merged = True
        else:
            conflicts: list[str] = []
            texts: dict[str, Any] = {}
            for k, (val, rawtext) in list(changes.items()):
                b, n = (base.get("cells") or {}).get(k), (now.get("cells") or {}).get(k)
                if merge3.same(b, n) or merge3.same(val, n):
                    continue
                if _long(cols[k]) and isinstance(val, str):
                    m = merge3.merge_text(b or "", val, n or "", "lines")
                    if m.ok:
                        changes[k] = (m.text or None, rawtext)
                        continue
                    texts[f"cells.{k}"] = m.conflicts
                conflicts.append(f"cells.{k}")
            if conflicts:
                row_v = await live.latest(db, uid, "row", r.id)
                mine = {"cells": {k: v for k, (v, _) in changes.items()}}
                raise live.LiveConflict("row", r.id, base, int(base_version), now, head, mine, conflicts, texts,
                                        by=row_v.by if row_v is not None else None)
            merged = True
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    links_new: list[dict[str, Any]] = []
    for k, (val, rawtext) in changes.items():
        col = cols[k]
        if col.type == "link" and val:
            old = {ty.ref_key(x) for x in (r.cells or {}).get(k) or []}
            links_new += [x for x in val if ty.ref_key(x) not in old]
        same = merge3.same((r.cells or {}).get(k), val) and merge3.same((r.raw or {}).get(k), rawtext)
        if same:
            continue
        b, a = _apply(r, col, val, rawtext)
        _merge_partials(before, b, first=True)
        _merge_partials(after, a, first=False)
    if links_new:
        await core.check_link_targets(db, user, links_new)
    if any(new_opts.values()):
        for c in await core.columns_of(db, t):
            if new_opts.get(c.col_id):
                bcfg = dict(c.config or {})
                c.config = cols[c.col_id].config
                await log.schema("options", col_id=c.col_id, before={"config": bcfg}, after={"config": c.config})
        await core.commit_table(db, t)
    nm = await core.names(db, {r.created_by})
    if not after:
        v = head
        return {"row": core.row_out(r, nm, v), "merged": merged, "undo": None}
    r.updated_at = datetime.now(UTC)
    r.updated_by = core.by_of(user)
    await core.derive(db, t, r, cols)
    await db.flush()
    await log.row(r.id, "edit", before, after)
    core.stamp(t, user)
    v = await core.commit_row(db, t, r) if notify else head
    names_ = [cols[k].name for k in after.get("cells", {})]
    label = f"Changed {names_[0]}" if len(names_) == 1 else "Row changed"
    return {"row": core.row_out(r, nm, v), "merged": merged, "kept_as_text": sum(1 for _, rw in changes.values() if rw),
            "undo": revert_undo(t, log.batch, label)}


async def delete_rows(db: AsyncSession, user: User, t: DataTable, ids: list[str], *, source: str = "edit",
                      log: Log | None = None) -> dict[str, Any]:
    """ACT row.delete, row.bulk_delete: restorable for 30 days ("Deleted rows")."""
    ids = [x for x in (core.valid_uuid(i) for i in ids) if x]
    rows = list((await db.execute(select(TableRow).where(TableRow.table_id == t.id, TableRow.id.in_(ids or [""]),
                                                         TableRow.deleted_at.is_(None)))).scalars())
    log = log or Log(db, t, user, source)
    now = datetime.now(UTC)
    for r in rows:
        r.deleted_at, r.deleted_by = now, user.id
        await db.execute(delete(TableRowRef).where(TableRowRef.table_id == t.id, TableRowRef.row_id == r.id))
    await db.flush()
    await log.rows([(r.id, "delete", core.row_state(r), {"deleted": True}) for r in rows])
    core.stamp(t, user)
    await _rows_changed(db, t, [], deleted=[r.id for r in rows])
    n = len(rows)
    return {"deleted": n, "ids": [r.id for r in rows],
            "undo": revert_undo(t, log.batch, "Deleted a row" if n == 1 else f"Deleted {_plural(n, 'row')}") if n else None}


async def restore_rows(db: AsyncSession, user: User, t: DataTable, ids: list[str], *, source: str = "edit",
                       log: Log | None = None, notify: bool = True) -> dict[str, Any]:
    """ACT row.restore: deleted rows come back where they were."""
    ids = [x for x in (core.valid_uuid(i) for i in ids) if x]
    rows = list((await db.execute(select(TableRow).where(TableRow.table_id == t.id, TableRow.id.in_(ids or [""]),
                                                         TableRow.deleted_at.is_not(None)))).scalars())
    if rows and await core.row_count(db, t) + len(rows) > LIMITS["rows"]:
        raise too_many_rows()
    log = log or Log(db, t, user, source)
    cols = core.cols_map(await core.columns_of(db, t))
    for r in rows:
        r.deleted_at, r.deleted_by = None, None
        await core.derive(db, t, r, cols)
    await db.flush()
    await log.rows([(r.id, "restore", {"deleted": True}, core.row_state(r)) for r in rows])
    core.stamp(t, user)
    if notify:
        await _rows_changed(db, t, [r.id for r in rows])
    n = len(rows)
    nm = await core.names(db, {r.created_by for r in rows})
    return {"restored": n, "rows": [core.row_out(r, nm, None) for r in rows],
            "undo": revert_undo(t, log.batch, "Restored a row" if n == 1 else f"Restored {_plural(n, 'row')}") if n else None}


async def duplicate_row(db: AsyncSession, user: User, t: DataTable, row_id: str, *, source: str = "edit") -> dict[str, Any]:
    """ACT row.duplicate: a copy below."""
    r = await core.get_row(db, t, row_id)
    res = await add_rows(db, user, t, [{"cells": dict(r.cells or {}), "raw": dict(r.raw or {})}], after=r.id,
                         source=source, defaults=False, label="Row duplicated")
    return {"row": res["rows"][0], "undo": res["undo"]}


async def move_row(db: AsyncSession, user: User, t: DataTable, row_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """ACT row.move: the manual order (`before` or `after` a row; `direction` up or down)."""
    r = await core.get_row(db, t, row_id)
    rows = sorted(await core.rows_of(db, t), key=lambda x: (x.order, x.id))
    ids = [x.id for x in rows]
    k = ids.index(r.id)
    b, a = body.get("before"), body.get("after")
    if body.get("direction") == "up":
        if k == 0:
            return {"row": None, "undo": None}
        b = ids[k - 1]
    elif body.get("direction") == "down":
        if k + 1 >= len(ids):
            return {"row": None, "undo": None}
        a = ids[k + 1]
    if not (b or a):
        raise HTTPException(422, "say where: before, after or direction")
    old = r.order
    rest = [x for x in rows if x.id != r.id]
    rids = [x.id for x in rest]
    if a in rids:
        i = rids.index(a)
        lo, hi = rest[i].order, rest[i + 1].order if i + 1 < len(rest) else None
    elif b in rids:
        i = rids.index(b)
        lo, hi = rest[i - 1].order if i > 0 else None, rest[i].order
    else:
        raise ApiError(404, "tables.row_not_found")
    key = order.after(lo) if hi is None else order.between(lo, hi)
    if order.too_long(key):
        for x, nk in zip(rows, order.spread(len(rows)), strict=False):
            x.order = nk
        await db.flush()
        return await move_row(db, user, t, row_id, body)
    r.order = key
    await db.flush()
    log = Log(db, t, user)
    await log.row(r.id, "move", {"order": old}, {"order": key})
    v = await core.commit_row(db, t, r)
    return {"row": core.row_out(r, {}, v), "undo": revert_undo(t, log.batch, "Row moved")}


async def bulk(db: AsyncSession, user: User, t: DataTable, body: dict[str, Any], *, source: str = "edit") -> dict[str, Any]:
    """ACT row.bulk_edit, row.bulk_delete: one batch, one undo.

    op "edit": {rows, col, value} sets one column to one value (typed; `parse` reads text);
    op "set": {rows: [{id, cells}]} typed values per row (fill down);
    op "paste": {rows: [{id?, cells: {col: text}}]} pasted text per cell, rows without an id added
    at the end; op "delete": {rows}."""
    op = body.get("op")
    log = Log(db, t, user, "paste" if op == "paste" else source)
    if op == "delete":
        res = await delete_rows(db, user, t, list(body.get("rows") or []), log=log)
        return res
    if op == "edit":
        col = body.get("col")
        items = [{"id": i, "cells": {col: body.get("value")}} for i in body.get("rows") or []]
        parse = bool(body.get("parse"))
    elif op in ("set", "paste"):
        items = list(body.get("rows") or [])
        parse = op == "paste" or bool(body.get("parse"))
    else:
        raise HTTPException(422, "op must be edit, set, paste or delete")
    if len(items) > LIMITS["rows"]:
        raise too_many_rows()
    edited, kept = 0, 0
    new_rows = [it for it in items if not it.get("id")]
    cols = core.cols_map(await core.columns_of(db, t))
    ctx = await core.reader(db, user)
    for it in items:
        if not it.get("id"):
            continue
        res = await edit_row(db, user, t, it["id"], it.get("cells") or {}, None, parse=parse, log=log, notify=False,
                             cols=cols, ctx=ctx)
        if res.get("undo"):
            edited += 1
        kept += res.get("kept_as_text") or 0
    added = 0
    if new_rows:
        res = await add_rows(db, user, t, [{"cells": it.get("cells") or {}} for it in new_rows], parse=parse, log=log,
                             defaults=False, notify=False)
        added = res["added"]
        kept += res["kept_as_text"]
    await db.flush()
    ids = [it["id"] for it in items if it.get("id")]
    rows_now = list((await db.execute(select(TableRow.id).where(TableRow.table_id == t.id, TableRow.id.in_(
        [x for x in (core.valid_uuid(i) for i in ids) if x] or [""])))).scalars())
    await _rows_changed(db, t, rows_now)
    if added:
        await core.touch(db, t, rows=True)
    parts = []
    if edited:
        parts.append(f"changed {_plural(edited, 'row')}")
    if added:
        parts.append(f"added {_plural(added, 'row')}")
    label = (", ".join(parts) or "No change").capitalize()
    return {"edited": edited, "added": added, "kept_as_text": kept, "batch": log.batch,
            "undo": revert_undo(t, log.batch, label) if log.count else None}


async def linked_from(db: AsyncSession, user: User, target_key: str) -> list[dict[str, Any]]:
    """The rows (of tables this user can see) whose link cells point at `target_key`: a row's
    "Linked from", and the links panels of mail, events and tasks."""
    rows = (await db.execute(select(TableRowRef, DataTable, TableRow)
                             .join(DataTable, DataTable.id == TableRowRef.table_id)
                             .join(TableRow, TableRow.id == TableRowRef.row_id)
                             .where(TableRowRef.target_key == target_key, access.visible(user),
                                    DataTable.deleted_at.is_(None), TableRow.deleted_at.is_(None))
                             .limit(200))).all()
    out = []
    cache: dict[int, dict[str, Col]] = {}
    for ref, tb, row in rows:
        if tb.id not in cache:
            cache[tb.id] = core.cols_map(await core.columns_of(db, tb))
        cols = cache[tb.id]
        out.append({"table": tb.pid, "table_name": tb.name, "row": row.id, "title": core.row_title(tb, cols, row),
                    "column": cols[ref.col_id].name if ref.col_id in cols else ""})
    return out


async def deleted_rows(db: AsyncSession, user: User, t: DataTable, q: str | None = None) -> dict[str, Any]:
    """The table's deleted rows, newest first, with who deleted them and when ("Deleted rows")."""
    rows = sorted(await core.rows_of(db, t, deleted=True), key=lambda r: r.deleted_at, reverse=True)
    if q:
        rows = vw.search(rows, q)
    cols = core.cols_map(await core.columns_of(db, t))
    nm = await core.names(db, {r.deleted_by for r in rows} | {r.created_by for r in rows})
    out = []
    for r in rows[:1000]:
        d = core.row_out(r, nm, None)
        d["title"] = core.row_title(t, cols, r)
        d["deleted_by"] = core.person(r.deleted_by, nm)
        out.append(d)
    return {"rows": out, "total": len(rows)}


# ---------------------------------------------------------------- Settings → Activity
# The outermost of these actions in one request makes an Activity entry (core/activity.py), by
# whoever made the change (live.who): a table made with rows is one "Created" entry, not two.

import functools as _functools  # noqa: E402
from contextvars import ContextVar as _ContextVar  # noqa: E402
from types import SimpleNamespace as _NS  # noqa: E402

_ACT_DEPTH: _ContextVar[bool] = _ContextVar("tables_activity", default=False)


def _with_activity(what: str, table_of, count_of=None):
    def deco(fn):
        @_functools.wraps(fn)
        async def run(db, user, *a, **kw):
            if _ACT_DEPTH.get():
                return await fn(db, user, *a, **kw)
            tok = _ACT_DEPTH.set(True)
            try:
                res = await fn(db, user, *a, **kw)
            finally:
                _ACT_DEPTH.reset(tok)
            from mailr.core import activity

            await activity.record_table(db, user.id, live.who(), what, table_of(a, res), res,
                                        n=count_of(res) if count_of else None)
            return res
        return run
    return deco


def _made(_a, res):
    t = (res or {}).get("table") or {}
    return _NS(pid=t.get("id"), name=t.get("name") or "") if t.get("id") else None


def _first(a, _res):
    return a[0] if a else None


create_table = _with_activity("create", _made)(create_table)
duplicate = _with_activity("duplicate", _made)(duplicate)
trash = _with_activity("trash", _first)(trash)
restore = _with_activity("restore", _first)(restore)
add_rows = _with_activity("rows.add", _first, lambda r: r.get("added"))(add_rows)
delete_rows = _with_activity("rows.delete", _first, lambda r: r.get("deleted"))(delete_rows)
restore_rows = _with_activity("rows.restore", _first, lambda r: r.get("restored"))(restore_rows)
edit_row = _with_activity("rows.edit", _first)(edit_row)
delete_column = _with_activity("column.delete", _first)(delete_column)
