"""Tables API.

Only a signed-in user calls these routes, as for notes: API tokens reach tables through the
assistant's tools (agent/table_tools.py) under the `notes` scope and the permission classes
`tables.rows`, `tables.rows_delete` and `tables.schema`. Every route finds the table through
tables/access.py, so a table the caller cannot see is a 404.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import any_, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import live, webheaders
from mailr.core.auth import Principal, current
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.notes.models import NoteLink, NoteViewerState
from mailr.tables import access, actions, core, csvio, history, query, xlsxio
from mailr.tables import types as ty
from mailr.tables.models import DataTable, TableColumn

router = APIRouter(tags=["tables"])


async def tables_user(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)) -> Principal:
    if p.is_token:
        from mailr.knowledge import profile

        raise ApiError(403, "tables.tokens_use_tools", name=await profile.name_of(db, p.user_id))
    return p


async def _run(db: AsyncSession, coro) -> Any:
    try:
        res = await coro
    except live.LiveConflict as c:
        await db.rollback()
        return JSONResponse(c.body(), status_code=409)
    except HTTPException:
        await db.rollback()
        raise
    await db.commit()
    return res


def _spec(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        v = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "spec must be JSON") from None  # i18n: internal
    if not isinstance(v, dict):
        raise HTTPException(400, "spec must be an object")  # i18n: internal
    return v


# ---------------------------------------------------------------- the list


VIEWS = ("all", "tables", "archive", "trash", "label", "shared")


# ACT table.search @api (the Notes grid's Tables view)
@router.get("/tables")
async def list_tables(view: str = "tables", label: str | None = None, p: Principal = Depends(tables_user),
                      db: AsyncSession = Depends(get_db)):
    """Table cards for the Notes grid: Tables (not archived), Archive, Trash, or one label."""
    if view not in VIEWS:
        raise HTTPException(400, f"view must be one of {', '.join(VIEWS)}")  # i18n: internal
    cards = await cards_for(db, p.user, view, label)
    await db.commit()
    return {"tables": cards}


@router.get("/tables/sync")
async def sync(since: str | None = None, p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    """The phones' delta for tables, as `GET /notes/sync`: tables whose fields, columns,
    views or this viewer's state changed since a time, or that were shared with the caller since
    then (with columns and views); rows added, changed, deleted or restored since then (every live
    row of a table that is new to the caller); tables in Trash by id; and `visible`, every table the
    caller sees now (Trash left out) with the caller's level.
    → {tables, rows, deleted_rows, trashed, visible, now}."""
    from mailr.notes import acl
    from mailr.notes.routes import _since
    from mailr.tables.models import TableRow, TableRowChange, TableSchemaChange, TableView

    now = datetime.now(UTC)
    t = _since(since)
    every = list((await db.execute(select(DataTable).where(access.visible(p.user)).limit(1000))).scalars())
    ids = [x.id for x in every]
    if t is None or not ids:
        changed, fresh = list(every), {x.id for x in every}
    else:
        touched: set[int] = set()
        for q in (select(NoteViewerState.item_id).where(NoteViewerState.user_id == p.user.id,
                                                       NoteViewerState.item_kind == "table",
                                                       NoteViewerState.updated_at > t),
                  select(TableSchemaChange.table_id).where(TableSchemaChange.table_id.in_(ids), TableSchemaChange.at > t),
                  select(TableView.table_id).where(TableView.table_id.in_(ids), TableView.updated_at > t)):
            touched |= set((await db.execute(q)).scalars())
        fresh = set((await db.execute(acl.shared_since(p.user, "table", t))).scalars())
        fresh |= {x.id for x in every if x.created_at and x.created_at > t}
        changed = [x for x in every if x.id in touched or x.id in fresh or x.updated_at > t
                   or (x.deleted_at is not None and x.deleted_at > t)]
    live = [x for x in every if x.deleted_at is None]
    by_id = {x.id: x for x in live}
    rows: list[TableRow] = []
    if live:
        live_ids = list(by_id)
        cond = TableRow.table_id.in_(live_ids)
        if t is not None:
            moved = select(TableRowChange.row_id).where(TableRowChange.table_id.in_(live_ids), TableRowChange.at > t)
            cond = cond & (TableRow.table_id.in_([i for i in live_ids if i in fresh]) | (TableRow.updated_at > t)
                           | (TableRow.deleted_at > t) | TableRow.id.in_(moved))
        else:
            cond = cond & TableRow.deleted_at.is_(None)
        rows = list((await db.execute(select(TableRow).where(cond).order_by(TableRow.table_id, TableRow.order)
                                      .limit(50_000))).scalars())
    nm = await core.names(db, {r.created_by for r in rows})
    out_rows, gone = [], []
    for tid in {r.table_id for r in rows}:
        part = [r for r in rows if r.table_id == tid]
        vers = await core.row_versions(db, by_id[tid], [r for r in part if r.deleted_at is None])
        for r in part:
            if r.deleted_at is not None:
                gone.append({"table": by_id[tid].pid, "id": r.id})
            else:
                out_rows.append({**core.row_out(r, nm, vers.get(r.id)), "table": by_id[tid].pid})
    tables = [await core.table_full(db, p.user, x) for x in changed if x.deleted_at is None]
    trashed = [x.pid for x in changed if x.deleted_at is not None]
    visible = {x.pid: access.level(p.user, x) for x in live}
    await db.commit()
    return {"tables": tables, "rows": out_rows, "deleted_rows": gone, "trashed": trashed, "visible": visible,
            "now": now.isoformat()}


async def cards_for(db: AsyncSession, user, view: str, label: str | None, *, hide_local: bool = False
                    ) -> list[dict[str, Any]]:
    """The table cards of a Notes view (the Notes routes add them to their own answer).
    `hide_local`: leave out local-only tables, for an API token that may not see them
    (notes/access.hidden_from)."""
    from mailr.notes.actions import get_label

    q = select(DataTable).where(access.visible(user))
    if hide_local:
        q = q.where(DataTable.local_only.is_(False))
    q = q.where(DataTable.deleted_at.is_not(None) if view == "trash" else DataTable.deleted_at.is_(None))
    st = select(NoteViewerState.item_id).where(NoteViewerState.user_id == user.id, NoteViewerState.item_kind == "table")
    if view == "archive":
        q = q.where(DataTable.id.in_(st.where(NoteViewerState.archived.is_(True))))
    elif view in ("all", "tables", "label", "shared"):
        q = q.where(DataTable.id.not_in(st.where(NoteViewerState.archived.is_(True))))
    if view == "shared":  # owned by someone else or by a group
        q = q.where(or_(DataTable.owner_user_id.is_(None), DataTable.owner_user_id != user.id))
    if view == "label":
        if not label:
            raise HTTPException(400, "label is required")  # i18n: internal
        lb = await get_label(db, user, label)
        q = q.where(DataTable.id.in_(st.where(literal(lb.name) == any_(NoteViewerState.labels))))
    tables = list((await db.execute(q.limit(1000))).scalars())
    if view == "trash":  # what the viewer may restore
        tables = [t for t in tables if access.can_delete(user, t)]
    return await core.table_cards(db, user, tables)


class ColumnIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str = "text"
    config: dict[str, Any] | None = None
    options: list[Any] | None = Field(None, max_length=200)
    required: bool = False
    width: int | None = None


class TableIn(BaseModel):
    name: str = Field("", max_length=200)
    description: str = Field("", max_length=2000)
    columns: list[ColumnIn] | None = Field(None, max_length=50)
    template: str | None = Field(None, pattern="^(log|inventory)$")
    color: str | None = None
    labels: list[str] | None = Field(None, max_length=200)
    pinned: bool = False
    local_only: bool = False
    rows: list[dict[str, Any]] | None = Field(None, max_length=10_000)
    parse: bool = False
    group: str | None = Field(None, max_length=200)


# ACT table.create @api
@router.post("/tables")
async def create_table(body: TableIn, p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    return await _run(db, actions.create_table(db, p.user, **body.model_dump()))


@router.get("/tables/linked")
async def linked(target_kind: str, ref: str | None = None, conversation_id: int | None = None,
                 p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    """What links to a conversation, an event, a task or a note: tables linked to it
    and rows whose link cells name it. For the links panels."""
    if target_kind not in ("conversation", "event", "task", "recording", "note", "row"):
        raise HTTPException(400, "unknown target_kind")  # i18n: internal
    key_id = conversation_id if target_kind == "conversation" else ref
    if key_id in (None, ""):
        raise HTTPException(400, "ref is required")  # i18n: internal
    key = f"{target_kind}:{key_id}"
    tables = []
    if target_kind in ("conversation", "event", "task", "recording"):
        rows = (await db.execute(select(DataTable, NoteLink).join(NoteLink, NoteLink.table_id == DataTable.id).where(
            NoteLink.target_key == key, access.visible(p.user), DataTable.deleted_at.is_(None)))).all()
        tables = [{"id": t.pid, "name": t.name, "link_id": ln.id} for t, ln in rows]
    rows_out = await actions.linked_from(db, p.user, key)
    return {"tables": tables, "rows": rows_out}


# ---------------------------------------------------------------- CSV import


class ImportIn(BaseModel):
    text: str | None = Field(None, max_length=6_000_000)
    base64: str | None = Field(None, max_length=7_500_000)
    delimiter: str | None = None
    header: bool | None = None
    name: str | None = Field(None, max_length=200)
    # Per CSV column: {index, name?, type?, skip?, currency?} (new table) or
    # {index, col?: <column id>, new?: {name, type}, skip?} (append).
    columns: list[dict[str, Any]] | None = Field(None, max_length=50)
    add_unmatched: bool = True
    preview: bool = False


def _data(body: ImportIn) -> bytes:
    if body.base64:
        try:
            return base64.b64decode(body.base64)
        except ValueError:
            raise HTTPException(400, "base64 is not valid") from None  # i18n: internal
    if body.text is not None:
        return body.text.encode("utf-8")
    raise HTTPException(400, "send the CSV as text or base64")  # i18n: internal


def _cells_from(csv_rows: list[list[str]], mapping: list[tuple[int, str]]) -> list[dict[str, Any]]:
    out = []
    for r in csv_rows:
        cells = {col: r[i] for i, col in mapping if i < len(r) and r[i].strip()}
        out.append({"cells": cells})
    return out


# ACT table.import_csv @api
@router.post("/tables/import")
async def import_new(body: ImportIn, p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    """A CSV file into a new table (`preview: true`: the first 20 rows and the detected types)."""
    ctx = await core.reader(db, p.user)
    data = _data(body)
    if body.preview:
        return csvio.preview(data, delimiter=body.delimiter, header=body.header, ctx=ctx)
    text, _ = csvio.decode(data)
    rows, _d = csvio.parse(text, body.delimiter)
    hdr = csvio.header_guess(rows) if body.header is None else body.header
    detected = {c["index"]: c for c in csvio.columns_of(rows, hdr, ctx)}
    chosen = body.columns or list(detected.values())
    specs, mapping = [], []
    for c in chosen:
        i = int(c.get("index", -1))
        if i not in detected or c.get("skip"):
            continue
        d = detected[i]
        t = c.get("type") or d["type"]
        cfg: dict[str, Any] = {}
        if t == "currency":
            cfg["currency"] = c.get("currency") or d.get("currency") or ctx.currency
        specs.append({"name": c.get("name") or d["name"], "type": t, "config": cfg})
        mapping.append(i)
    if not specs:
        raise ApiError(422, "tables.choose_import_column")
    body_rows = rows[1:] if hdr else rows

    async def go():
        made = await actions.create_table(db, p.user, name=body.name or "Imported table", columns=specs,
                                          source="import", label="Table imported")
        t = await access.get(db, p.user, made["table"]["id"])
        cols = [c["id"] for c in made["table"]["columns"]]
        res = await actions.add_rows(db, p.user, t, _cells_from(body_rows, list(zip(mapping, cols, strict=True))),
                                     parse=True, source="import", defaults=False, notify=False)
        await core.touch(db, t, rows=True)
        return {"table": await core.table_full(db, p.user, t), "imported": res["added"],
                "kept_as_text": res["kept_as_text"],
                "summary": f"Imported {actions._plural(res['added'], 'row')} · "
                           f"{actions._plural(res['kept_as_text'], 'cell')} kept as text",
                "undo": made["undo"]}

    return await _run(db, go())


# ---------------------------------------------------------------- one table


# ACT table.export_xlsx
@router.get("/tables/export.xlsx")
async def export_xlsx_many(ids: str = Query(..., description="table ids, comma-separated"),
                           p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    """Several whole tables in one workbook, one sheet each, in the order given (at most 20)."""
    want = [x for x in dict.fromkeys(i.strip() for i in ids.split(",")) if x]
    if not want or len(want) > 20:
        raise HTTPException(400, "name 1 to 20 tables")  # i18n: internal
    sheets = [await _xlsx_sheet(db, p.user, await access.get(db, p.user, i), "all") for i in want]
    data = xlsxio.workbook(sheets)
    await db.commit()
    name = _file_name(sheets[0][0]) if len(sheets) == 1 else "tables"
    return Response(data, media_type=XLSX, headers={"Content-Disposition": webheaders.attachment(f"{name}.xlsx")})


@router.get("/tables/{table_id}")
async def get_table(table_id: str, p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id)
    out = await core.table_full(db, p.user, t)
    await db.commit()
    return out


class TablePatch(BaseModel):
    base_version: int | None = None
    name: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=2000)
    color: str | None = None
    local_only: bool | None = None
    primary_column: str | None = None


# ACT table.rename @api, ACT table.color @api, ACT column.set_primary @api, ACT table.local_only @api
@router.patch("/tables/{table_id}")
async def patch_table(table_id: str, body: TablePatch, p: Principal = Depends(tables_user),
                      db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    changes = body.model_dump(exclude_unset=True)
    base = changes.pop("base_version", None)
    changes = {k: v for k, v in changes.items() if v is not None or k == "color"}
    return await _run(db, actions.patch_table(db, p.user, t, changes, base))


# ACT table.delete @api, ACT table.purge @api
@router.delete("/tables/{table_id}")
async def delete_table(table_id: str, forever: bool = False, p: Principal = Depends(tables_user),
                       db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="delete")
    if forever:
        return await _run(db, actions.purge(db, p.user, t))
    return await _run(db, actions.trash(db, p.user, t))


# ACT table.restore @api
@router.post("/tables/{table_id}/restore")
async def restore_table(table_id: str, p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="delete")
    return await _run(db, actions.restore(db, p.user, t))


class DuplicateIn(BaseModel):
    rows: bool = True


# ACT table.duplicate @api
@router.post("/tables/{table_id}/duplicate")
async def duplicate_table(table_id: str, body: DuplicateIn | None = None, p: Principal = Depends(tables_user),
                          db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, trashed=False)
    return await _run(db, actions.duplicate(db, p.user, t, with_rows=body.rows if body else True))


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
    # A date column to show on this viewer's calendar, or null (tables/calendar.py).
    calendar_column: str | None = Field(None, max_length=16)


# ACT table.pin @api, ACT table.archive @api, ACT table.label @api
@router.put("/tables/{table_id}/state")
async def put_state(table_id: str, body: StateIn, p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id)
    return await _run(db, actions.set_state(db, p.user, t, body.model_dump(exclude_unset=True)))


# ---------------------------------------------------------------- rows


# ACT table.search @api, ACT table.view_edit @api (a viewer's unsaved sort and filters)
@router.get("/tables/{table_id}/rows")
async def get_rows(table_id: str, view: str | None = None, q: str | None = None, spec: str | None = None,
                   offset: int = Query(0, ge=0), limit: int = Query(10_000, ge=1, le=10_000),
                   p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    """The rows of a view, in its order, with its groups and footers. `spec` (JSON) replaces parts
    of the view for this read only; `q` finds text."""
    t = await access.get(db, p.user, table_id)
    res = await query.run(db, p.user, t, view=view, spec=_spec(spec), q=q)
    page = res.rows[offset:offset + limit]
    vers = await core.row_versions(db, t, page)
    nm = await core.names(db, {r.created_by for r in page})
    from mailr.tables import views as vw

    out = {"rows": [core.row_out(r, nm, vers.get(r.id)) for r in page], "count": len(res.rows), "total": res.total,
           "offset": offset, "view": res.view_id, "spec": res.spec,
           "footers": vw.footers(res.rows, res.spec.get("footers") or {}, res.cols, res.ctx),
           "groups": query.groups_out(res), "refs": res.titles}
    await db.commit()
    return out


class RowIn(BaseModel):
    id: str | None = None
    cells: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] | None = None


class RowsIn(BaseModel):
    rows: list[RowIn] | None = Field(None, max_length=10_000)
    id: str | None = None
    cells: dict[str, Any] | None = None
    after: str | None = None
    before: str | None = None
    parse: bool = False
    view_filters: list[dict[str, Any]] | None = Field(None, max_length=30)


# ACT row.add @api
@router.post("/tables/{table_id}/rows")
async def add_rows(table_id: str, body: RowsIn, p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    """One row (`cells`, with a client-made `id`) or many (`rows`). `view_filters`: the active
    filters, whose values fill the new row so it stays in view."""
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    items = [r.model_dump() for r in body.rows] if body.rows else [{"id": body.id, "cells": body.cells or {}}]
    return await _run(db, actions.add_rows(db, p.user, t, items, after=body.after, before=body.before,
                                           parse=body.parse, view_filters=body.view_filters,
                                           defaults=not body.rows))


class BulkIn(BaseModel):
    op: str = Field(pattern="^(edit|set|paste|delete)$")
    rows: list[Any] = Field(default_factory=list, max_length=10_000)
    col: str | None = None
    value: Any = None
    parse: bool = False


# ACT row.bulk_edit @api, ACT row.bulk_delete @api
@router.post("/tables/{table_id}/rows/bulk")
async def bulk(table_id: str, body: BulkIn, p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    return await _run(db, actions.bulk(db, p.user, t, body.model_dump()))


class RestoreRowsIn(BaseModel):
    rows: list[str] = Field(max_length=10_000)


# ACT row.restore @api
@router.post("/tables/{table_id}/rows/restore")
async def restore_rows(table_id: str, body: RestoreRowsIn, p: Principal = Depends(tables_user),
                       db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    return await _run(db, actions.restore_rows(db, p.user, t, body.rows))


@router.get("/tables/{table_id}/rows/{row_id}")
async def get_row(table_id: str, row_id: str, p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    """One row with its link titles and "Linked from"."""
    t = await access.get(db, p.user, table_id)
    r = await core.get_row(db, t, row_id, deleted_ok=True)
    cols = core.cols_map(await core.columns_of(db, t))
    refs = core.all_refs([r], cols)
    vers = await core.row_versions(db, t, [r])
    nm = await core.names(db, {r.created_by, r.deleted_by})
    out = core.row_out(r, nm, vers.get(r.id))
    out["title"] = core.row_title(t, cols, r)
    out["deleted_by"] = core.person(r.deleted_by, nm)
    out["refs"] = await core.titles(db, p.user, refs, owner_view=t.owner_user_id == p.user.id) if refs else {}
    out["linked_from"] = await actions.linked_from(db, p.user, f"row:{r.id}")
    await db.commit()
    return out


class RowPatch(BaseModel):
    base_version: int | None = None
    cells: dict[str, Any]
    parse: bool = False
    restore: bool = False


# ACT row.edit @api
@router.patch("/tables/{table_id}/rows/{row_id}")
async def patch_row(table_id: str, row_id: str, body: RowPatch, p: Principal = Depends(tables_user),
                    db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    return await _run(db, actions.edit_row(db, p.user, t, row_id, body.cells, body.base_version, parse=body.parse,
                                           restore=body.restore))


# ACT row.delete @api
@router.delete("/tables/{table_id}/rows/{row_id}")
async def delete_row(table_id: str, row_id: str, p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    await core.get_row(db, t, row_id)
    return await _run(db, actions.delete_rows(db, p.user, t, [row_id]))


# ACT row.duplicate @api
@router.post("/tables/{table_id}/rows/{row_id}/duplicate")
async def duplicate_row(table_id: str, row_id: str, p: Principal = Depends(tables_user),
                        db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    return await _run(db, actions.duplicate_row(db, p.user, t, row_id))


class MoveIn(BaseModel):
    before: str | None = None
    after: str | None = None
    direction: str | None = Field(None, pattern="^(up|down)$")


# ACT row.move @api
@router.post("/tables/{table_id}/rows/{row_id}/move")
async def move_row(table_id: str, row_id: str, body: MoveIn, p: Principal = Depends(tables_user),
                   db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    return await _run(db, actions.move_row(db, p.user, t, row_id, body.model_dump(exclude_unset=True)))


# ACT row.history @api
@router.get("/tables/{table_id}/rows/{row_id}/changes")
async def row_changes(table_id: str, row_id: str, col: str | None = None, p: Principal = Depends(tables_user),
                      db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id)
    return await history.row_history(db, t, row_id, col=col)


class RowRestoreIn(BaseModel):
    seq: int
    col: str | None = None


# ACT row.restore_version @api
@router.post("/tables/{table_id}/rows/{row_id}/restore")
async def restore_row_version(table_id: str, row_id: str, body: RowRestoreIn, p: Principal = Depends(tables_user),
                              db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    return await _run(db, history.restore_version(db, p.user, t, row_id, body.seq, col=body.col))


# ACT row.restore @api ("Deleted rows")
@router.get("/tables/{table_id}/deleted-rows")
async def deleted_rows(table_id: str, q: str | None = None, p: Principal = Depends(tables_user),
                       db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id)
    return await actions.deleted_rows(db, p.user, t, q)


# ---------------------------------------------------------------- columns


class ColumnAdd(ColumnIn):
    before: str | None = None
    after: str | None = None


# ACT column.add @api
@router.post("/tables/{table_id}/columns")
async def add_column(table_id: str, body: ColumnAdd, p: Principal = Depends(tables_user),
                     db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    spec = body.model_dump(exclude={"before", "after"})
    return await _run(db, actions.add_column(db, p.user, t, spec, before=body.before, after=body.after))


class ColumnPatch(BaseModel):
    name: str | None = Field(None, max_length=200)
    base_name: str | None = Field(None, max_length=200)
    type: str | None = None
    config: dict[str, Any] | None = None
    options: list[Any] | None = Field(None, max_length=200)
    merge: dict[str, str] | None = None
    required: bool | None = None
    width: int | None = None
    before: str | None = None
    after: str | None = None
    direction: str | None = Field(None, pattern="^(left|right)$")
    preview: bool = False


# ACT column.rename @api, ACT column.retype @api, ACT column.options @api, ACT column.config @api, ACT column.reorder @api
@router.patch("/tables/{table_id}/columns/{col_id}")
async def patch_column(table_id: str, col_id: str, body: ColumnPatch, p: Principal = Depends(tables_user),
                       db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    return await _run(db, actions.patch_column(db, p.user, t, col_id, body.model_dump(exclude_unset=True)))


# ACT column.delete @api
@router.delete("/tables/{table_id}/columns/{col_id}")
async def delete_column(table_id: str, col_id: str, p: Principal = Depends(tables_user),
                        db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    return await _run(db, actions.delete_column(db, p.user, t, col_id))


# ACT column.restore @api
@router.post("/tables/{table_id}/columns/{col_id}/restore")
async def restore_column(table_id: str, col_id: str, p: Principal = Depends(tables_user),
                         db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    return await _run(db, actions.restore_column(db, p.user, t, col_id))


@router.get("/tables/{table_id}/deleted-columns")
async def deleted_columns(table_id: str, p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id)
    cols = [c for c in await core.columns_of(db, t, deleted=True) if c.deleted_at is not None]
    rows = await core.rows_of(db, t)
    out = []
    for c in sorted(cols, key=lambda c: c.deleted_at, reverse=True):
        d = core.column_out(c)
        d["values"] = sum(1 for r in rows if not ty.is_empty((r.cells or {}).get(c.col_id)))
        out.append(d)
    return {"columns": out}


@router.get("/tables/{table_id}/columns/{col_id}/targets")
async def link_targets(table_id: str, col_id: str, q: str = "", p: Principal = Depends(tables_user),
                       db: AsyncSession = Depends(get_db)):
    """Candidates for a link cell (the link picker): rows of the target table by their primary
    value, conversations by subject, events and tasks by title, notes and tables by title."""
    t = await access.get(db, p.user, table_id)
    c = await core.get_column(db, t, col_id)
    if c.type != "link":
        raise ApiError(422, "tables.not_link_column")
    from mailr.tables import links as lk

    return {"items": await lk.candidates(db, p.user, ty.col_of(c), q)}


# ---------------------------------------------------------------- views


class ViewIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    spec: dict[str, Any] | None = None
    view_id: str | None = None
    default: bool | None = None


# ACT table.view_save @api
@router.post("/tables/{table_id}/views")
async def save_view(table_id: str, body: ViewIn, p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    return await _run(db, actions.save_view(db, p.user, t, name=body.name, spec=body.spec, view_id=body.view_id,
                                            default=body.default))


class ViewPatch(BaseModel):
    name: str | None = Field(None, max_length=200)
    spec: dict[str, Any] | None = None
    default: bool | None = None


# ACT table.view_edit @api
@router.patch("/tables/{table_id}/views/{view_id}")
async def edit_view(table_id: str, view_id: str, body: ViewPatch, p: Principal = Depends(tables_user),
                    db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    return await _run(db, actions.edit_view(db, p.user, t, view_id, body.model_dump(exclude_unset=True)))


# ACT table.view_delete @api
@router.delete("/tables/{table_id}/views/{view_id}")
async def delete_view(table_id: str, view_id: str, p: Principal = Depends(tables_user),
                      db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    return await _run(db, actions.delete_view(db, p.user, t, view_id))


# ---------------------------------------------------------------- stats, export, history


class StatsIn(BaseModel):
    view: str | None = None
    spec: dict[str, Any] | None = None
    q: str | None = None
    group_by: dict[str, Any] | str | None = None
    columns: list[str] | None = Field(None, max_length=50)
    aggregates: list[str] | None = Field(None, max_length=20)


# ACT table.stats @api
@router.post("/tables/{table_id}/stats")
async def stats(table_id: str, body: StatsIn, p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id)
    res = await query.run(db, p.user, t, view=body.view, spec=body.spec, q=body.q)
    g = {"col": body.group_by} if isinstance(body.group_by, str) else body.group_by
    out = query.stats(res, body.columns, body.aggregates, g)
    await db.commit()
    return out


# ACT table.export_csv @api
@router.get("/tables/{table_id}/export.csv")
async def export_csv(table_id: str, scope: str = Query("view", pattern="^(view|all)$"), view: str | None = None,
                     spec: str | None = None, bom: bool = False, p: Principal = Depends(tables_user),
                     db: AsyncSession = Depends(get_db)):
    """The current view (its filters, sort and visible columns) or the whole table."""
    t = await access.get(db, p.user, table_id)
    if scope == "all":
        res = await query.run(db, p.user, t, spec={"filters": [], "sort": [], "hidden": []})
        cols = res.ordered
    else:
        res = await query.run(db, p.user, t, view=view, spec=_spec(spec))
        cols = query.visible_columns(res)
    text = csvio.export(cols, res.rows, {k: v.get("title", "") for k, v in res.titles.items()}, bom=bom)
    await db.commit()
    return Response(text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": webheaders.attachment(f"{_file_name(t.name)}.csv")})


def _file_name(name: str | None) -> str:
    return "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in (name or "table")).strip() or "table"


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def _xlsx_sheet(db: AsyncSession, user, t, scope: str, view: str | None = None, spec: str | None = None):
    if scope == "all":
        res = await query.run(db, user, t, spec={"filters": [], "sort": [], "hidden": []})
        cols = res.ordered
    else:
        res = await query.run(db, user, t, view=view, spec=_spec(spec))
        cols = query.visible_columns(res)
    return t.name or "Table", cols, res.rows, {k: v.get("title", "") for k, v in res.titles.items()}


# ACT table.export_xlsx @api
@router.get("/tables/{table_id}/export.xlsx")
async def export_xlsx(table_id: str, scope: str = Query("all", pattern="^(view|all)$"), view: str | None = None,
                      spec: str | None = None, p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    """The whole table (default) or the current view (`scope=view`: its filters, sort and visible
    columns), as one sheet with typed cells."""
    t = await access.get(db, p.user, table_id)
    data = xlsxio.workbook([await _xlsx_sheet(db, p.user, t, scope, view, spec)])
    await db.commit()
    return Response(data, media_type=XLSX,
                    headers={"Content-Disposition": webheaders.attachment(f"{_file_name(t.name)}.xlsx")})


# ACT table.history @api
@router.get("/tables/{table_id}/changes")
async def changes(table_id: str, before: int | None = None, p: Principal = Depends(tables_user),
                  db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id)
    return await history.table_history(db, t, before_seq=before)


class RestorePointIn(BaseModel):
    at: datetime | None = None
    seq: int | None = None
    preview: bool = False
    label: str | None = Field(None, max_length=100)


# ACT table.restore_point @api
@router.post("/tables/{table_id}/restore-point")
async def restore_point(table_id: str, body: RestorePointIn, p: Principal = Depends(tables_user),
                        db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    if body.preview:
        res = await history.restore_point(db, p.user, t, at=body.at, seq=body.seq, preview=True, when_text=body.label)
        await db.rollback()
        return res
    return await _run(db, history.restore_point(db, p.user, t, at=body.at, seq=body.seq, when_text=body.label))


class RevertIn(BaseModel):
    batch: str = Field(min_length=8, max_length=36)


# (the undo of every table, column and row change)
@router.post("/tables/{table_id}/revert")
async def revert(table_id: str, body: RevertIn, p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    return await _run(db, history.revert(db, p.user, t, body.batch))


# ---------------------------------------------------------------- CSV append, links


# ACT table.import_csv @api (rows appended to this table)
@router.post("/tables/{table_id}/import")
async def import_append(table_id: str, body: ImportIn, p: Principal = Depends(tables_user),
                        db: AsyncSession = Depends(get_db)):
    """Rows appended from a CSV file. Columns map by name (case ignored) unless `columns` says
    otherwise; unmatched columns are added (`add_unmatched`) or skipped."""
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    ctx = await core.reader(db, p.user)
    data = _data(body)
    text, _ = csvio.decode(data)
    rows, _d = csvio.parse(text, body.delimiter)
    hdr = csvio.header_guess(rows) if body.header is None else body.header
    detected = {c["index"]: c for c in csvio.columns_of(rows, hdr, ctx)}
    cols = await core.columns_of(db, t)
    by_name = {c.name.lower(): c.col_id for c in cols}
    plan = []
    chosen = {int(c.get("index", -1)): c for c in body.columns or []}
    for i, d in detected.items():
        c = chosen.get(i, {})
        if c.get("skip"):
            plan.append({"index": i, "name": d["name"], "action": "skip"})
        elif c.get("col") and any(x.col_id == c["col"] for x in cols):
            plan.append({"index": i, "name": d["name"], "action": "map", "col": c["col"]})
        elif d["name"].lower() in by_name and not c.get("new"):
            plan.append({"index": i, "name": d["name"], "action": "map", "col": by_name[d["name"].lower()]})
        elif body.add_unmatched or c.get("new"):
            new = c.get("new") or {}
            plan.append({"index": i, "name": new.get("name") or d["name"], "action": "add",
                         "type": new.get("type") or d["type"], "currency": d.get("currency")})
        else:
            plan.append({"index": i, "name": d["name"], "action": "skip"})
    if body.preview:
        pv = csvio.preview(data, delimiter=body.delimiter, header=hdr, ctx=ctx)
        return {**pv, "plan": plan}

    async def go():
        log = core.Log(db, t, p.user, "import")
        mapping = []
        for item in plan:
            if item["action"] == "map":
                mapping.append((item["index"], item["col"]))
            elif item["action"] == "add":
                cfg = {"currency": item["currency"] or ctx.currency} if item["type"] == "currency" else {}
                res = await actions.add_column(db, p.user, t, {"name": item["name"], "type": item["type"],
                                                               "config": cfg}, source="import")
                # The column's own batch joins the import's, so one undo takes both back.
                await _rebatch(db, t, res["undo"]["body"]["batch"], log.batch)
                mapping.append((item["index"], res["column"]["id"]))
        if not mapping:
            raise ApiError(422, "tables.no_matching_column")
        res = await actions.add_rows(db, p.user, t, _cells_from(rows[1:] if hdr else rows, mapping), parse=True,
                                     source="import", log=log, defaults=False, notify=False)
        await core.touch(db, t, rows=True)
        return {"imported": res["added"], "kept_as_text": res["kept_as_text"],
                "summary": f"Imported {actions._plural(res['added'], 'row')} · "
                           f"{actions._plural(res['kept_as_text'], 'cell')} kept as text",
                "undo": actions.revert_undo(t, log.batch, "Import")}

    return await _run(db, go())


async def _rebatch(db: AsyncSession, t: DataTable, old: str, new: str) -> None:
    from sqlalchemy import update

    from mailr.tables.models import TableSchemaChange

    await db.execute(update(TableSchemaChange).where(TableSchemaChange.table_id == t.id, TableSchemaChange.batch == old)
                     .values(batch=new))


class LinkIn(BaseModel):
    target_kind: str
    target_ref: dict[str, Any]


# ACT table.link @api
@router.post("/tables/{table_id}/links")
async def add_link(table_id: str, body: LinkIn, p: Principal = Depends(tables_user), db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    return await _run(db, actions.link(db, p.user, t, body.target_kind, dict(body.target_ref)))


# ACT table.unlink @api
@router.delete("/tables/{table_id}/links/{link_id}")
async def remove_link(table_id: str, link_id: int, p: Principal = Depends(tables_user),
                      db: AsyncSession = Depends(get_db)):
    t = await access.get(db, p.user, table_id, need="edit", trashed=False)
    return await _run(db, actions.unlink(db, p.user, t, link_id))


class SaveTemplateIn(BaseModel):
    name: str | None = Field(None, max_length=200)


# ACT table.save_template @api
@router.post("/tables/{table_id}/save-template")
async def save_template(table_id: str, body: SaveTemplateIn | None = None, p: Principal = Depends(tables_user),
                        db: AsyncSession = Depends(get_db)):
    """Save the table's columns as a template; "New from template" in Notes
    makes a new table from it with no rows."""
    from mailr.notes import templates

    t = await access.get(db, p.user, table_id)
    return await _run(db, templates.save_table(db, p.user, t, body.name if body else None))


_ = (TableColumn, UTC)
