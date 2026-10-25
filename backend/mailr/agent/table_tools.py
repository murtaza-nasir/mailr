"""The assistant's table tools.

The assistant has exactly the user's own access on each table (tables/access.py), through the same
actions as the web (tables/actions.py) and the permission engine:

- `tables.rows` (allow): add, edit, duplicate and move rows; bring back deleted rows; put a row or
  a cell back to an earlier value. In a tainted turn a write that puts free text, a new select
  option or a link in a cell asks by default; numbers, dates, checkboxes and existing options do not.
- `tables.rows_delete` (ask): delete rows, bulk included.
- `tables.schema`: create a table, add a column, save a view (allow); rename, retype, reorder,
  delete or restore columns, change options or settings, delete views, restore the table to a point
  in time (ask by default).
- A table to Trash is `notes.delete` (ask). Sharing is never a tool.

Every read tool is in UNTRUSTED_TOOLS: a table may hold other people's rows. Tables are found on
the server by id or by name; a name that matches two tables is an error that lists no titles.
Local-only tables never reach a hosted model. Errands and standing orders ask before writing to a
table someone else owns. Rows are cited as [[table:<id>#<row_id>]], tables as [[table:<id>]].
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from mailr.agent.tools import ToolCtx, ToolError, ToolResult, _obj, _u, gate, local_ok, tool
from mailr.tables import (  # noqa: F401  (executors register on import)
    access,
    core,
    executors,
    query,
)
from mailr.tables import types as ty
from mailr.tables import views as vw
from mailr.tables.models import DataTable
from mailr.tables.types import Col

READ_TOOLS = ("list_tables", "get_table", "table_stats", "table_history")
CITE_HELP = "Cite a table as [[table:<id>]] and a row as [[table:<id>#<row_id>]]."
HIDDEN = "This table is local-only: it is hidden from this model."
ROW_LIMIT = 200

def cite(pid: str, row: str | None = None) -> str:
    return f"[[table:{pid}#{row}]]" if row else f"[[table:{pid}]]"


# ---------------------------------------------------------------- finding tables, columns and rows


async def find_table(ctx: ToolCtx, ref: Any, *, need: str = "view", trashed: bool = False) -> DataTable:
    """A table by its id (t…) or its name (case ignored); the one on screen when none is given."""
    ref = str(ref or "").strip() or str(((ctx.ui or {}).get("table") or {}).get("id") or "")
    if not ref:
        raise ToolError("say which table: its id or its name")
    user = ctx.p.user
    t = None
    if ref.startswith("t") and len(ref) == 12:
        t = (await ctx.db.execute(select(DataTable).where(DataTable.pid == ref)
                                  .execution_options(populate_existing=True))).scalar_one_or_none()
    if t is None:
        rows = (await ctx.db.execute(select(DataTable).where(access.visible(user), DataTable.deleted_at.is_(None),
                                                              func.lower(DataTable.name) == ref.lower())
                                          .execution_options(populate_existing=True))).scalars().all()
        if len(rows) > 1:
            raise ToolError(f"{len(rows)} tables have that name; use the table id (list_tables)")
        t = rows[0] if rows else None
    if t is None or access.level(user, t) == "none":
        raise ToolError("table not found")
    lv = access.level(user, t)
    if need == "delete":
        if not access.can_delete(user, t):
            raise ToolError("only the owner can delete this table")
    elif not access.at_least(lv, need):
        raise ToolError(await view_only(ctx, t))
    if not trashed and t.deleted_at is not None:
        raise ToolError("this table is in Trash")
    if t.local_only and (_token_hidden(ctx) or not await local_ok(ctx)):
        raise ToolError(HIDDEN)
    return t


async def view_only(ctx: ToolCtx, t: DataTable) -> str:
    """"Dana shared this table with view access only" (the user's own reach, no more)."""
    from mailr.models import User

    if t.owner_user_id is None:
        return f"The group {t.owner_group} shared this table with view access only"
    u = await ctx.db.get(User, t.owner_user_id)
    return f"{(u.name or u.email) if u else 'Its owner'} shared this table with view access only"


def _token_hidden(ctx: ToolCtx) -> bool:
    """Local-only tables are hidden from an API token that is not local-only, as local-only notes
    are (notes/access.hidden_from)."""
    from mailr.notes.access import hidden_from

    return hidden_from(ctx.p)


def column(cols: dict[str, Col], ref: Any) -> Col:
    """A column by id or by name (case ignored)."""
    r = str(ref or "").strip()
    if r in cols:
        return cols[r]
    for c in cols.values():
        if c.name.lower() == r.lower():
            return c
    raise ToolError(f"unknown column {r!r}; the columns are {', '.join(c.name for c in cols.values())}")


def schema_out(t: DataTable, cols: list[Col]) -> list[dict[str, Any]]:
    out = []
    for c in cols:
        d: dict[str, Any] = {"id": c.col_id, "name": _u(c.name), "type": c.type}
        cfg = c.config or {}
        if c.type in ("select", "multi_select"):
            d["options"] = _u([o["name"] for o in c.options])
        if c.type == "currency":
            d["currency"] = cfg.get("currency")
        if c.type == "link":
            d["links_to"] = cfg.get("target") + (f" rows of {cfg.get('table')}" if cfg.get("table") else "")
            d["multiple"] = bool(cfg.get("multiple"))
        if c.col_id == t.primary_column:
            d["primary"] = True
        out.append(d)
    return out


def _spec_from(cols: dict[str, Col], args: dict[str, Any]) -> dict[str, Any]:
    """Ad hoc filters and sort from the model: columns and options by name or id."""
    spec: dict[str, Any] = {}
    if args.get("filters"):
        fs = []
        for f in args["filters"]:
            if not isinstance(f, dict):
                raise ToolError("a filter is {column, op, value}")
            c = column(cols, f.get("column") or f.get("col"))
            item = {"col": c.col_id, "op": f.get("op")}
            if "value" in f:
                item["value"] = f["value"]
            fs.append(item)
        spec["filters"] = fs
    if args.get("sort"):
        spec["sort"] = [{"col": column(cols, s.get("column") or s.get("col")).col_id, "dir": s.get("dir", "asc")}
                        for s in args["sort"][:3]]
    try:
        vw.clean_spec(spec, cols, None)
    except vw.ViewError as e:
        raise ToolError(str(e)) from None
    return spec


async def _rows_by(ctx: ToolCtx, t: DataTable, cols: dict[str, Col], items: list[Any]) -> list[str]:
    """Row ids from ids, or from primary values matched on the server."""
    rows = None
    out = []
    for it in items:
        if isinstance(it, dict):
            rid, match = it.get("row_id"), it.get("match")
        else:
            rid, match = it, None
        if rid:
            if core.valid_uuid(rid) is None:
                raise ToolError(f"{rid!r} is not a row id")
            out.append(core.valid_uuid(rid))
            continue
        if not match:
            raise ToolError("a row is given by row_id or by match (its primary value)")
        if rows is None:
            rows = await core.rows_of(ctx.db, t)
        hits = [r for r in rows if core.row_title(t, cols, r).strip().lower() == str(match).strip().lower()]
        if not hits:
            raise ToolError(f"no row of this table is named {str(match)[:80]!r}")
        if len(hits) > 1:
            raise ToolError(f"{len(hits)} rows have that primary value; use row ids (get_table)")
        out.append(hits[0].id)
    return out


def _screen_rows(ctx: ToolCtx, t: DataTable) -> list[str]:
    """"This row" and "these rows": the selection on screen, else the focused row."""
    tb = (ctx.ui or {}).get("table") or {}
    if tb.get("id") != t.pid:
        return []
    sel = [x for x in (tb.get("selected_rows") or []) if core.valid_uuid(x)]
    return sel or ([tb["focused_row"]] if core.valid_uuid(tb.get("focused_row")) else [])


# ---------------------------------------------------------------- typed values


class Free:
    """What a write puts in cells: whether any of it is free text (tainted turns ask)."""

    def __init__(self):
        self.text = False


def coerce(col: Col, v: Any, rctx: ty.Ctx, free: Free) -> Any:
    """A value from the model in the column's type. A value that does not fit is a tool error
    naming the column and the type expected."""
    if v is None or v == "" or v == []:
        return None
    t = col.type
    try:
        if t == "text":
            free.text = True
            return ty.check(col, v if isinstance(v, str) else str(v))
        if t in ("number", "currency"):
            if isinstance(v, str):
                d, _ = ty.parse_number(v)
                if d is None:
                    raise ty.CellError(col.name, t, v)
                return ty.norm_decimal(d)
            return ty.check(col, v)
        if t == "date":
            if isinstance(v, str):
                d = ty.parse_date(v, rctx)
                if d is None:
                    raise ty.CellError(col.name, t, v)
                return d.isoformat()
            raise ty.CellError(col.name, t, v)
        if t == "datetime":
            if isinstance(v, str):
                dt = ty.parse_datetime(v, rctx)
                if dt is None:
                    raise ty.CellError(col.name, t, v)
                return dt
            return ty.check(col, v)
        if t == "checkbox":
            if isinstance(v, str):
                b = ty.parse_bool(v)
                if b is None:
                    raise ty.CellError(col.name, t, v)
                return True if b else None
            return ty.check(col, v)
        if t in ("select", "multi_select"):
            names = v if isinstance(v, list) else [v]
            ids = []
            for n in names:
                o = next((o for o in col.options if o["id"] == n), None) or ty.option_by_name(col, str(n))
                if o is None:
                    o = ty.add_option(col, str(n))  # a new option: free text
                    free.text = True
                ids.append(o["id"])
            return ty.check(col, ids if t == "multi_select" else ids[0])
        if t == "link":
            free.text = True
            return ty.check(col, v)
    except ty.CellError as e:
        raise ToolError(str(e)) from None
    raise ToolError(f"unknown column type {t!r}")


def _cells(cols: dict[str, Col], values: Any, rctx: ty.Ctx, free: Free) -> dict[str, Any]:
    if not isinstance(values, dict) or not values:
        raise ToolError("values must be an object of column names to values")
    return {column(cols, k).col_id: coerce(column(cols, k), v, rctx, free) for k, v in values.items()}


async def _new_options(ctx: ToolCtx, t: DataTable, cols: dict[str, Col]) -> list[dict[str, Any]]:
    """Option lists the coercion grew, to save on the columns before the rows."""
    out = []
    for c in await core.columns_of(ctx.db, t):
        if c.type in ("select", "multi_select") and len(cols[c.col_id].options) != len((c.config or {}).get("options") or []):
            out.append({"col": c.col_id, "options": cols[c.col_id].options})
    return out


def _payload(ctx: ToolCtx, t: DataTable | None, op: str, **kw: Any) -> dict[str, Any]:
    from mailr.core import live

    out = {"op": op, "_by": live.who(ctx.p, assistant=ctx.assistant), **kw}
    if t is not None:
        out["table"] = t.pid
    return out


def _errand_force(ctx: ToolCtx, t: DataTable) -> str | None:
    """Errands and standing orders ask before writing to a table other people see: someone else's,
    a group's, or the user's own shared with others."""
    from mailr.notes import acl

    if ctx.actor.errand and (t.owner_user_id != ctx.p.user.id or acl.is_shared(t)):
        return "an errand writes to a shared table only after you approve it"
    return None


async def _run(ctx: ToolCtx, action: str, payload: dict[str, Any], t: DataTable | None, preview: dict[str, Any], *,
               ask: str | None = None) -> ToolResult:
    from mailr.agent.tools import _audit
    from mailr.core import permissions

    force = _errand_force(ctx, t) if t is not None else None
    pending = await gate(ctx, action, target=t.pid if t else "new table", cvals={"notes": [t.pid]} if t else {},
                         payload=payload, preview=preview, ask_by_default=ask, force_ask=force)
    if pending is not None:
        return pending
    fn = permissions.executor_for(action, payload)
    try:
        res = await fn(ctx.db, ctx.p.user, permissions.stamp(payload, ctx.p))
    except Exception as e:
        from fastapi import HTTPException

        from mailr.core.live import LiveConflict

        await ctx.db.rollback()
        if isinstance(e, HTTPException):
            d = e.detail
            raise ToolError(d.get("detail") if isinstance(d, dict) else str(d)) from None
        if isinstance(e, LiveConflict):
            raise ToolError(e.body()["detail"] + " Nothing changed. Read the row again (get_table) and retry.") from None
        raise
    await _audit(ctx, f"agent.{action}", payload.get("table") or "", payload, res)
    await ctx.db.commit()
    if res.get("table"):
        res["cite"] = cite(res["table"])
    return ToolResult(res, refs=[{"kind": "table", "id": res["table"], "version": None}] if res.get("table") else [])


# ---------------------------------------------------------------- reads


async def _semantic(ctx: ToolCtx, q: str, limit: int = 10) -> list[str]:
    """Tables whose embedding (name, columns and row text; tables/embed.py) is near the query.
    A local-only table is embedded only by a local endpoint, and only a query that may go there
    finds it."""
    from mailr.agent.tools import query_sensitive
    from mailr.intel import embed as emb

    sensitive = query_sensitive(ctx)
    try:
        got = await emb.embed_query(ctx.db, q, sensitive=sensitive, user_id=ctx.p.user_id)
    except Exception:  # noqa: BLE001  no embedding endpoint: words only
        return []
    if got is None:
        return []
    vec, key = got
    rows = (await ctx.db.execute(select(DataTable.pid).where(
        access.visible(ctx.p.user), DataTable.deleted_at.is_(None), DataTable.embedding.is_not(None),
        DataTable.embed_model == key).order_by(DataTable.embedding.cosine_distance(vec)).limit(limit))).scalars().all()
    return list(rows)


# ACT table.search @assistant
@tool("list_tables",
      "The user's tables (and tables shared with them): ids, names, row counts, columns and labels. "
      "`query` finds words in names and row text. " + CITE_HELP,
      _obj({"query": {"type": "string"}, "label": {"type": "string"},
            "archived": {"type": "boolean", "description": "list archived tables instead"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}), scope="notes")
async def list_tables(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.notes.models import NoteViewerState

    user = ctx.p.user
    q = select(DataTable).where(access.visible(user), DataTable.deleted_at.is_(None))
    if args.get("query"):
        from mailr.notes.search import search_tables, split

        ops, words = split(str(args["query"]))
        cards = await search_tables(ctx.db, user, ops, words, set(), limit=int(args.get("limit") or 50))
        ids = [c["id"] for c in cards]
        ids += [x for x in await _semantic(ctx, str(args["query"])) if x not in ids]
        tables = list((await ctx.db.execute(select(DataTable).where(DataTable.pid.in_(ids or [""])))).scalars())
        tables.sort(key=lambda t: ids.index(t.pid))
    else:
        tables = list((await ctx.db.execute(q.order_by(DataTable.updated_at.desc()).limit(500))).scalars())
    states = {s.item_id: s for s in (await ctx.db.execute(select(NoteViewerState).where(
        NoteViewerState.user_id == user.id, NoteViewerState.item_kind == "table"))).scalars()}
    want_archived = bool(args.get("archived"))
    tables = [t for t in tables if bool(states.get(t.id) and states[t.id].archived) == want_archived]
    if args.get("label"):
        lb = str(args["label"]).lower()
        tables = [t for t in tables if states.get(t.id) and lb in [x.lower() for x in states[t.id].labels or []]]
    hidden = 0
    sensitive = False
    if any(t.local_only for t in tables):
        if not _token_hidden(ctx) and await local_ok(ctx):
            sensitive = True
        else:
            hidden = sum(1 for t in tables if t.local_only)
            tables = [t for t in tables if not t.local_only]
    tables = tables[:int(args.get("limit") or 50)]
    items = []
    for t in tables:
        cols = list(core.cols_map(await core.columns_of(ctx.db, t)).values())
        st = states.get(t.id)
        items.append({"id": t.pid, "name": _u(t.name), "description": _u(t.description) if t.description else None,
                      "rows": await core.row_count(ctx.db, t), "columns": schema_out(t, cols),
                      "labels": list(st.labels or []) if st else [], "pinned": bool(st and st.pinned),
                      "level": access.level(user, t), "updated_at": core.iso(t.updated_at), "cite": cite(t.pid)})
    out: dict[str, Any] = {"items": items}
    if hidden:
        out["note"] = f"{hidden} local-only table{'s are' if hidden != 1 else ' is'} hidden from this model."
    return ToolResult(out, sensitive=sensitive)


# ACT table.search @assistant, ACT table.view_edit
@tool("get_table",
      "One table: its columns (with option names), its views, and a page of rows (up to 200) with row ids and "
      "typed values by column name, in a view's order, or with ad hoc `filters` "
      "([{column, op, value}]; ops by type: text contains/is/starts_with/…, number eq/ne/lt/lte/gt/gte/between, "
      "date is/before/after/between/last_days/next_days, checkbox checked/not_checked, select is/is_not/any_of, "
      "multi_select has_any/has_all/has_none, any empty/not_empty) and `sort` ([{column, dir}]). `query` finds "
      "words in the rows. Without `table`, the table on screen. " + CITE_HELP,
      _obj({"table": {"type": "string", "description": "id (t…) or name"}, "view": {"type": "string"},
            "filters": {"type": "array", "items": {"type": "object"}}, "sort": {"type": "array", "items": {"type": "object"}},
            "query": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": ROW_LIMIT}}), scope="notes")
async def get_table(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    t = await find_table(ctx, args.get("table"))
    cols = core.cols_map(await core.columns_of(ctx.db, t))
    views = await core.views_of(ctx.db, t)
    view = None
    if args.get("view"):
        v = next((x for x in views if x.view_id == args["view"] or x.name.lower() == str(args["view"]).lower()), None)
        if v is None:
            raise ToolError("unknown view")
        view = v.view_id
    spec = _spec_from(cols, args)
    res = await query.run(ctx.db, ctx.p.user, t, view=view, spec=spec or None, q=args.get("query"))
    off, lim = int(args.get("offset") or 0), int(args.get("limit") or 50)
    page = res.rows[off:off + lim]
    out = {"id": t.pid, "name": _u(t.name), "description": _u(t.description) if t.description else None,
           "level": access.level(ctx.p.user, t), "columns": schema_out(t, res.ordered),
           "views": [{"id": v.view_id, "name": _u(v.name), "default": v.is_default} for v in views],
           "count": len(res.rows), "total": res.total, "offset": off,
           "rows": [{"row_id": r.id, "cite": cite(t.pid, r.id), "values": _u(query.typed_out(res, r))} for r in page],
           "cite": cite(t.pid)}
    if off + lim < len(res.rows):
        out["more"] = f"{len(res.rows) - off - lim} more rows: ask again with offset {off + lim}"
    return ToolResult(out, sensitive=bool(t.local_only))


# ACT table.stats @assistant
@tool("table_stats",
      "Totals and counts for a table: per column sum, average, median, min, max, count (numbers), earliest, "
      "latest, range (dates), checked share (checkboxes), counts per option (selects), filled, empty and unique, "
      "for all rows or `filters` (as get_table), optionally per group (`group_by` a select, checkbox, date or "
      "link column, with `by` day/week/month/year for dates).",
      _obj({"table": {"type": "string"}, "view": {"type": "string"},
            "filters": {"type": "array", "items": {"type": "object"}}, "columns": {"type": "array", "items": {"type": "string"}},
            "group_by": {"type": "string"}, "by": {"type": "string", "enum": list(vw.DATE_BUCKETS)},
            "query": {"type": "string"}}), scope="notes")
async def table_stats(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    t = await find_table(ctx, args.get("table"))
    cols = core.cols_map(await core.columns_of(ctx.db, t))
    spec = _spec_from(cols, args)
    view = None
    if args.get("view"):
        v = next((x for x in await core.views_of(ctx.db, t)
                  if x.view_id == args["view"] or x.name.lower() == str(args["view"]).lower()), None)
        view = v.view_id if v else None
    res = await query.run(ctx.db, ctx.p.user, t, view=view, spec=spec or None, q=args.get("query"))
    want = [column(cols, c).col_id for c in args.get("columns") or []]
    g = None
    if args.get("group_by"):
        g = {"col": column(cols, args["group_by"]).col_id, "by": args.get("by") or "day"}
    out = query.stats(res, want or None, None, g)
    by_name = {}
    for k, v in out["columns"].items():
        by_name[cols[k].name] = {a: x for a, x in v.items() if a not in ("name", "type")}
    result: dict[str, Any] = {"id": t.pid, "count": out["count"], "total": out["total"], "columns": _u(by_name),
                              "cite": cite(t.pid)}
    if out.get("groups"):
        result["groups"] = _u([{"group": gr["label"], "count": gr["count"],
                                "columns": {cols[k].name: {a: x for a, x in v.items() if a not in ("name", "type")}
                                            for k, v in gr["columns"].items()}} for gr in out["groups"]])
    return ToolResult(result, sensitive=bool(t.local_only))


# ACT table.history @assistant, ACT row.history @assistant, ACT row.restore @assistant, ACT row.restore_version @assistant,
# ACT column.restore @assistant, ACT table.restore_point @assistant
@tool("table_history",
      "A table's history and its restores. action `table` (default): the changes in sessions; `row`: one row's "
      "changes and each cell's earlier values; `restore_rows`: bring back deleted rows (`rows`); `deleted_rows`: "
      "list them; `restore_row`: put a row (or one `column`) back as it was after change `seq`; `restore_column`: "
      "bring back a deleted `column`; `restore_point`: the whole table as it was at `at` (an ISO time in the last "
      "30 days) or after change `seq` (asks the user first).",
      _obj({"table": {"type": "string"},
            "action": {"type": "string", "enum": ["table", "row", "deleted_rows", "restore_rows", "restore_row",
                                                  "restore_column", "restore_point"]},
            "row_id": {"type": "string"}, "rows": {"type": "array", "items": {"type": "string"}},
            "column": {"type": "string"}, "seq": {"type": "integer"}, "at": {"type": "string"}}), scope="notes")
async def table_history(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from datetime import datetime

    from mailr.tables import actions, history

    action = args.get("action") or "table"
    need = "view" if action in ("table", "row", "deleted_rows") else "edit"
    t = await find_table(ctx, args.get("table"), need=need)
    if action == "table":
        h = await history.table_history(ctx.db, t, limit=20)
        sessions = [{"start": s["start"], "who": s["who"], "summary": _u(s["summary"]), "count": s["count"],
                     "from_seq": s["from_seq"], "to_seq": s["to_seq"],
                     "changes": _u([{"seq": c["seq"], "at": c["at"], "summary": c["summary"], "row_id": c.get("row")}
                                    for c in s["changes"][:20]])} for s in h["sessions"]]
        return ToolResult({"id": t.pid, "sessions": sessions, "oldest": h["oldest"]}, sensitive=bool(t.local_only))
    if action == "row":
        rid = args.get("row_id") or next(iter(_screen_rows(ctx, t)), None)
        if not rid:
            raise ToolError("'row_id' is required")
        h = await history.row_history(ctx.db, t, rid)
        cols = core.cols_map(await core.columns_of(ctx.db, t, deleted=True))
        vals = {(cols[k].name if k in cols else k): [{"text": v["text"], "at": v["at"], "who": v["who"], "seq": v["seq"]}
                                                     for v in vs[:10]] for k, vs in h["values"].items()}
        return ToolResult({"id": t.pid, "row_id": rid, "deleted": h["deleted"], "cite": cite(t.pid, rid),
                           "changes": _u([{"seq": c["seq"], "at": c["at"], "who": c["who"], "summary": c["summary"]}
                                          for c in h["changes"][:30]]), "earlier_values": _u(vals)},
                          sensitive=bool(t.local_only))
    if action == "deleted_rows":
        d = await actions.deleted_rows(ctx.db, ctx.p.user, t)
        return ToolResult({"id": t.pid, "total": d["total"],
                           "rows": _u([{"row_id": r["id"], "title": r["title"], "deleted_at": r["deleted_at"],
                                        "deleted_by": (r["deleted_by"] or {}).get("name")} for r in d["rows"][:100]])},
                          sensitive=bool(t.local_only))
    if action == "restore_rows":
        ids = [x for x in (core.valid_uuid(i) for i in args.get("rows") or []) if x]
        if not ids:
            raise ToolError("'rows' (deleted row ids) is required")
        return await _run(ctx, "tables.rows", _payload(ctx, t, "restore_rows", rows=ids), t,
                          {"title": f"Bring back {len(ids)} deleted row{'s' if len(ids) != 1 else ''}"})
    if action == "restore_row":
        rid = args.get("row_id")
        if not rid or args.get("seq") is None:
            raise ToolError("'row_id' and 'seq' are required")
        cols = core.cols_map(await core.columns_of(ctx.db, t))
        col = column(cols, args["column"]).col_id if args.get("column") else None
        return await _run(ctx, "tables.rows", _payload(ctx, t, "restore_version", row=rid, seq=int(args["seq"]), col=col),
                          t, {"title": "Put a cell back" if col else "Put a row back"})
    if action == "restore_column":
        cols = {c.col_id: ty.col_of(c) for c in await core.columns_of(ctx.db, t, deleted=True) if c.deleted_at}
        c = column(cols, args.get("column"))
        return await _run(ctx, "tables.schema", _payload(ctx, t, "restore_column", col=c.col_id), t,
                          {"title": f"Bring back the column {c.name}"},
                          ask="restoring a column asks by default")
    if action == "restore_point":
        at = args.get("at")
        if at:
            try:
                when = datetime.fromisoformat(str(at))
            except ValueError:
                raise ToolError("'at' must be an ISO time") from None
        else:
            when = None
        if when is None and args.get("seq") is None:
            raise ToolError("'at' or 'seq' is required")
        pv = await history.restore_point(ctx.db, ctx.p.user, t, at=when, seq=args.get("seq"), preview=True)
        return await _run(ctx, "tables.schema", _payload(ctx, t, "restore_point", at=at, seq=args.get("seq")), t,
                          {"title": f"Restore {t.name} to an earlier point", "lines": [pv["summary"]]},
                          ask="restoring a table to a point in time asks by default")
    raise ToolError("unknown action")


# ---------------------------------------------------------------- writes


# ACT row.add @assistant, ACT row.edit @assistant, ACT row.duplicate @assistant, ACT row.delete @assistant,
# ACT row.bulk_edit @assistant, ACT row.bulk_delete @assistant
@tool("table_rows",
      "Add, change, duplicate or delete rows of a table. action `add`: rows [{values: {column name: value}}]; "
      "`edit`: rows [{row_id or match (the row's primary value), values}]; `duplicate` and `delete`: rows "
      "[row_id or {match}]; `bulk_edit`: set `column` to `value` on rows. Values are typed: text, numbers "
      "(\"82.5\"), dates (\"2026-10-05\", \"today\", \"fri\"), date-times, true/false, option names (a new name "
      "adds an option), and links ({kind: \"row\", table, row} | {kind: \"conversation\", conversation_id} | "
      "{kind: \"event\", uid} | {kind: \"task\", uid} | {kind: \"note\", id}). Without rows, edit and delete use "
      "the rows selected on screen. Deleting asks the user first. " + CITE_HELP,
      _obj({"table": {"type": "string"},
            "action": {"type": "string", "enum": ["add", "edit", "duplicate", "delete", "bulk_edit"]},
            "rows": {"type": "array", "items": {}}, "column": {"type": "string"}, "value": {}},
           ["action"]), scope="notes")
async def table_rows(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.agent import agentwrite

    t = await find_table(ctx, args.get("table"), need="edit")
    action = args["action"]
    cols = core.cols_map(await core.columns_of(ctx.db, t))
    rctx = await core.reader(ctx.db, ctx.p.user)
    free = Free()
    items = list(args.get("rows") or [])
    name = t.name
    if action == "add":
        if not items:
            raise ToolError("'rows' is required")
        if len(items) > 500:
            raise ToolError("add at most 500 rows at a time")
        rows = [{"cells": _cells(cols, (it or {}).get("values") if isinstance(it, dict) else None, rctx, free)}
                for it in items]
        rows = [{"cells": {k: v for k, v in r["cells"].items() if v is not None}} for r in rows]
        payload = _payload(ctx, t, "add", rows=rows, options=await _new_options(ctx, t, cols))
        return await _run(ctx, "tables.rows", payload, t,
                          {"title": f"Add {len(rows)} row{'s' if len(rows) != 1 else ''} to {name}"},
                          ask=agentwrite.taint_reason(ctx) if free.text else None)
    ids = await _rows_by(ctx, t, cols, items) if items else _screen_rows(ctx, t)
    if not ids:
        raise ToolError("'rows' is required (no rows are selected on screen)")
    if action == "edit":
        edits = []
        by_id = {}
        for it, rid in zip(items or [{}] * len(ids), ids, strict=False):
            vals = it.get("values") if isinstance(it, dict) else None
            by_id[rid] = _cells(cols, vals, rctx, free)
        for rid, cells in by_id.items():
            edits.append({"id": rid, "cells": cells})
        return await _run(ctx, "tables.rows", _payload(ctx, t, "edit", rows=edits,
                                                      options=await _new_options(ctx, t, cols)), t,
                          {"title": f"Change {len(edits)} row{'s' if len(edits) != 1 else ''} of {name}"},
                          ask=agentwrite.taint_reason(ctx) if free.text else None)
    if action == "bulk_edit":
        c = column(cols, args.get("column"))
        val = coerce(c, args.get("value"), rctx, free)
        return await _run(ctx, "tables.rows", _payload(ctx, t, "bulk_edit", rows=ids, col=c.col_id, value=val,
                                                      options=await _new_options(ctx, t, cols)), t,
                          {"title": f"Set {c.name} on {len(ids)} rows of {name}"},
                          ask=agentwrite.taint_reason(ctx) if free.text else None)
    if action == "duplicate":
        return await _run(ctx, "tables.rows", _payload(ctx, t, "duplicate", rows=ids), t,
                          {"title": f"Duplicate {len(ids)} row{'s' if len(ids) != 1 else ''} of {name}"})
    if action == "delete":
        return await _run(ctx, "tables.rows_delete", _payload(ctx, t, "delete", rows=ids), t,
                          {"title": f"Delete {len(ids)} row{'s' if len(ids) != 1 else ''} of {name}",
                           "lines": [f"{len(ids)} of {await core.row_count(ctx.db, t)} rows"]})
    raise ToolError("unknown action")


# ACT table.color @assistant, ACT table.pin @assistant, ACT table.label @assistant, ACT table.archive @assistant,
# ACT table.restore @assistant, ACT table.link @assistant
@tool("table_state",
      "A table's colour, pin, labels and archive (the user's own view), a link to a conversation, event, task "
      "or recording, or its way back from Trash. action `color` (color: tomato, flamingo, tangerine, banana, "
      "sage, basil, peacock, blueberry, lavender, grape, graphite, cocoa, or default); `pin`, `unpin`, "
      "`archive`, `unarchive`; `labels` (add, remove: label names); `link` (target_kind, target_ref "
      "{conversation_id} | {uid} | {recording_id}); `restore` (from Trash).",
      _obj({"table": {"type": "string"},
            "action": {"type": "string", "enum": ["color", "pin", "unpin", "archive", "unarchive", "labels", "link",
                                                  "restore"]},
            "color": {"type": "string"}, "add": {"type": "array", "items": {"type": "string"}},
            "remove": {"type": "array", "items": {"type": "string"}},
            "target_kind": {"type": "string", "enum": ["conversation", "event", "task", "recording"]},
            "target_ref": {"type": "object"}}, ["action"]), scope="notes")
async def table_state(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    action = args["action"]
    t = await find_table(ctx, args.get("table"), need="view" if action != "restore" else "delete",
                         trashed=action == "restore")
    if action == "restore":
        if t.deleted_at is None:
            return ToolResult({"ok": True, "table": t.pid, "note": "the table is not in Trash", "cite": cite(t.pid)})
        payload = _payload(ctx, t, "restore", _variant="table")
        return await _run(ctx, "notes.edit", payload, t, {"title": f"Restore the table {t.name} from Trash"})
    if action == "color":
        if access.level(ctx.p.user, t) == "view":
            raise ToolError(await view_only(ctx, t))
        payload = _payload(ctx, t, "color", color=None if args.get("color") in (None, "default") else args.get("color"),
                           _variant="table")
        return await _run(ctx, "notes.edit", payload, t, {"title": f"Colour the table {t.name}"})
    if action == "link":
        if not args.get("target_kind") or not isinstance(args.get("target_ref"), dict):
            raise ToolError("'target_kind' and 'target_ref' are required")
        payload = _payload(ctx, t, "link", target_kind=args["target_kind"], target_ref=args["target_ref"], _variant="table")
        return await _run(ctx, "notes.edit", payload, t, {"title": f"Link the table {t.name}"})
    state = {"pin": {"pinned": True}, "unpin": {"pinned": False}, "archive": {"archived": True},
             "unarchive": {"archived": False}}.get(action)
    if action == "labels":
        state = {"add_labels": list(args.get("add") or []), "remove_labels": list(args.get("remove") or [])}
    if state is None:
        raise ToolError("unknown action")
    payload = _payload(ctx, t, "state", state=state, _variant="table")
    return await _run(ctx, "notes.edit", payload, t, {"title": f"{action.capitalize()} the table {t.name}"})


# ACT table.create @assistant, ACT table.rename @assistant, ACT column.add @assistant, ACT column.rename @assistant,
# ACT column.retype @assistant, ACT column.options @assistant, ACT column.config @assistant, ACT column.reorder @assistant,
# ACT column.set_primary @assistant, ACT column.delete @assistant, ACT table.view_save @assistant, ACT table.view_delete @assistant
@tool("table_schema",
      "Create a table or change its columns and views. action `create`: name, columns [{name, type, options?, "
      "currency?, links_to?}] (types: text, number, currency, date, datetime, checkbox, select, multi_select, link), "
      "optional rows [{values}]; `rename`: name, description; `add_column`: column {name, type, …}; "
      "`rename_column`: column, name; `retype_column`: column, type (values that do not convert are kept as "
      "text); `options`: column, options [names in order]; `config`: column, decimals/format/currency/long/"
      "multiple/required; `move_column`: column, before or after; `set_primary`: column; `delete_column`: column; "
      "`save_view`: name, filters, sort, group_by, hidden; `delete_view`: view. Creating a table, adding a column "
      "and saving a view go ahead; the other changes ask the user first.",
      _obj({"table": {"type": "string"},
            "action": {"type": "string", "enum": ["create", "rename", "add_column", "rename_column", "retype_column",
                                                  "options", "config", "move_column", "set_primary", "delete_column",
                                                  "save_view", "delete_view"]},
            "name": {"type": "string"}, "description": {"type": "string"},
            "columns": {"type": "array", "items": {"type": "object"}}, "rows": {"type": "array", "items": {"type": "object"}},
            "column": {}, "type": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}},
            "decimals": {"type": "integer"}, "format": {"type": "string"}, "currency": {"type": "string"},
            "long": {"type": "boolean"}, "multiple": {"type": "boolean"}, "required": {"type": "boolean"},
            "before": {"type": "string"}, "after": {"type": "string"}, "view": {"type": "string"},
            "filters": {"type": "array", "items": {"type": "object"}}, "sort": {"type": "array", "items": {"type": "object"}},
            "group_by": {"type": "string"}, "hidden": {"type": "array", "items": {"type": "string"}},
            "local_only": {"type": "boolean"}}, ["action"]), scope="notes")
async def table_schema(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.agent import agentwrite

    action = args["action"]
    taint = agentwrite.taint_reason(ctx)
    if action == "create":
        name = agentwrite.clean_text(ctx, str(args.get("name") or "").strip())
        if not name:
            raise ToolError("'name' is required")
        specs = [_column_spec(c) for c in args.get("columns") or [{"name": "Name", "type": "text"}]]
        rows = None
        if args.get("rows"):
            cols = {f"c{i}": Col(f"c{i}", s["name"], s["type"], s.get("config") or {}) for i, s in enumerate(specs)}
            rctx = await core.reader(ctx.db, ctx.p.user)
            free = Free()
            rows = [{"cells": _cells(cols, r.get("values"), rctx, free)} for r in args["rows"][:500]]
            for i, s in enumerate(specs):
                if s["type"] in ("select", "multi_select"):
                    s["options"] = cols[f"c{i}"].options
            rows = [{"cells": {specs[int(k[1:])]["name"]: v for k, v in r["cells"].items()}} for r in rows]
        payload = _payload(ctx, None, "create", name=name, description=str(args.get("description") or ""),
                           columns=specs,
                           # A table made in a chat that read local-only content is local-only.
                           local_only=bool(args.get("local_only")) or agentwrite.local_copy_reason(ctx) is not None)
        if rows:
            payload["rows_by_name"] = rows
        return await _run_create(ctx, payload, taint)
    t = await find_table(ctx, args.get("table"), need="edit")
    cols = core.cols_map(await core.columns_of(ctx.db, t))
    if action == "rename":
        change = {k: agentwrite.clean_text(ctx, str(args[k])) for k in ("name", "description") if args.get(k) is not None}
        if not change:
            raise ToolError("'name' or 'description' is required")
        return await _run(ctx, "tables.schema", _payload(ctx, t, "rename", **change), t,
                          {"title": f"Rename the table {t.name}", "lines": [change.get("name") or ""]},
                          ask="renaming a table asks by default")
    if action == "add_column":
        spec = _column_spec(args.get("column") if isinstance(args.get("column"), dict) else
                            {"name": args.get("name") or args.get("column"), "type": args.get("type") or "text",
                             "options": args.get("options"), "currency": args.get("currency")})
        if spec["type"] == "link" and (spec.get("config") or {}).get("target") == "table":
            tbl = (spec["config"].get("table") or "self")
            if tbl != "self":
                other = await find_table(ctx, tbl)
                spec["config"]["table"] = other.pid
        return await _run(ctx, "tables.schema", _payload(ctx, t, "add_column", column=spec, before=_colid(cols, args.get("before")),
                                                        after=_colid(cols, args.get("after"))), t,
                          {"title": f"Add the column {spec['name']} to {t.name}"}, ask=taint)
    if action == "save_view":
        spec = _spec_from(cols, args)
        if args.get("group_by"):
            spec["group_by"] = {"col": column(cols, args["group_by"]).col_id}
        if args.get("hidden"):
            spec["hidden"] = [column(cols, h).col_id for h in args["hidden"]]
        name = agentwrite.clean_text(ctx, str(args.get("name") or "").strip())
        if not name:
            raise ToolError("'name' is required")
        return await _run(ctx, "tables.schema", _payload(ctx, t, "save_view", name=name, spec=spec), t,
                          {"title": f"Save the view {name} of {t.name}"}, ask=taint)
    if action == "delete_view":
        views = await core.views_of(ctx.db, t)
        v = next((x for x in views if x.view_id == args.get("view") or x.name.lower() == str(args.get("view") or "").lower()),
                 None)
        if v is None:
            raise ToolError("unknown view")
        return await _run(ctx, "tables.schema", _payload(ctx, t, "delete_view", view=v.view_id), t,
                          {"title": f"Delete the view {v.name} of {t.name}"}, ask="deleting a view asks by default")
    c = column(cols, args.get("column"))
    if action == "rename_column":
        new = agentwrite.clean_text(ctx, str(args.get("name") or "").strip())
        return await _run(ctx, "tables.schema", _payload(ctx, t, "rename_column", col=c.col_id,
                                                        change={"name": new, "base_name": c.name}), t,
                          {"title": f"Rename the column {c.name} to {new}"}, ask="renaming a column asks by default")
    if action == "retype_column":
        typ = str(args.get("type") or "")
        if typ not in ty.TYPES:
            raise ToolError(f"type must be one of {', '.join(ty.TYPES)}")
        from mailr.tables import actions as tact

        pv = await tact.patch_column(ctx.db, ctx.p.user, t, c.col_id, {"type": typ, "preview": True})
        p = pv["preview"]
        return await _run(ctx, "tables.schema", _payload(ctx, t, "retype_column", col=c.col_id, change={"type": typ}), t,
                          {"title": f"Change {c.name} to {ty.LABELS[typ].lower()}",
                           "lines": [f"{p['convert']} cells convert, {p['fail']} do not (kept as text)"]},
                          ask="changing a column's type asks by default")
    if action == "options":
        names = [agentwrite.clean_text(ctx, str(n)) for n in args.get("options") or []]
        have = {o["name"].lower(): o for o in c.options}
        opts = [have.get(n.lower(), {"name": n}) for n in names]
        return await _run(ctx, "tables.schema", _payload(ctx, t, "column_options", col=c.col_id, change={"options": opts}), t,
                          {"title": f"Change the options of {c.name}", "lines": [", ".join(names)]},
                          ask="changing options asks by default")
    if action == "config":
        cfg = {k: args[k] for k in ("decimals", "format", "currency", "long", "multiple") if args.get(k) is not None}
        change: dict[str, Any] = {"config": cfg}
        if args.get("required") is not None:
            change["required"] = bool(args["required"])
        return await _run(ctx, "tables.schema", _payload(ctx, t, "column_config", col=c.col_id, change=change), t,
                          {"title": f"Change the settings of {c.name}"}, ask="changing a column asks by default")
    if action == "move_column":
        change = {"before": _colid(cols, args.get("before")), "after": _colid(cols, args.get("after"))}
        return await _run(ctx, "tables.schema", _payload(ctx, t, "reorder_column", col=c.col_id,
                                                        change={k: v for k, v in change.items() if v}), t,
                          {"title": f"Move the column {c.name}"}, ask="moving a column asks by default")
    if action == "set_primary":
        return await _run(ctx, "tables.schema", _payload(ctx, t, "set_primary", col=c.col_id), t,
                          {"title": f"Name rows by {c.name}"}, ask="changing the primary column asks by default")
    if action == "delete_column":
        n = sum(1 for r in await core.rows_of(ctx.db, t) if not ty.is_empty((r.cells or {}).get(c.col_id)))
        return await _run(ctx, "tables.schema", _payload(ctx, t, "delete_column", col=c.col_id), t,
                          {"title": f"Delete column {c.name}: {n} values"}, ask="deleting a column asks by default")
    raise ToolError("unknown action")


def _colid(cols: dict[str, Col], ref: Any) -> str | None:
    return column(cols, ref).col_id if ref else None


def _column_spec(c: Any) -> dict[str, Any]:
    if not isinstance(c, dict) or not c.get("name"):
        raise ToolError("a column is {name, type}")
    typ = c.get("type") or "text"
    if typ not in ty.TYPES:
        raise ToolError(f"type must be one of {', '.join(ty.TYPES)}")
    cfg: dict[str, Any] = dict(c.get("config") or {})
    if typ in ("select", "multi_select") and c.get("options"):
        cfg["options"] = [{"id": ty.new_id("o"), "name": str(o)} if not isinstance(o, dict) else
                          {"id": o.get("id") or ty.new_id("o"), **o} for o in c["options"]]
    if typ == "currency" and c.get("currency"):
        cfg["currency"] = c["currency"]
    if typ == "link":
        target = c.get("links_to") or cfg.get("target") or "note"
        if target not in ty.LINK_TARGETS:
            raise ToolError(f"links_to must be one of {', '.join(ty.LINK_TARGETS)}")
        cfg["target"] = target
        if target == "table":
            cfg["table"] = c.get("table") or cfg.get("table") or "self"
        if c.get("multiple"):
            cfg["multiple"] = True
    return {"name": str(c["name"])[:100], "type": typ, "config": cfg}


async def _run_create(ctx: ToolCtx, payload: dict[str, Any], taint: str | None) -> ToolResult:
    """A new table: its rows arrive by column name and become ids once the columns exist."""
    rows = payload.pop("rows_by_name", None)
    if rows:
        payload["rows_named"] = rows
    res = await _run(ctx, "tables.schema", payload, None,
                     {"title": f"Create the table {payload['name']}",
                      "lines": [", ".join(c["name"] for c in payload["columns"])]}, ask=taint)
    return res


# ACT table.delete @assistant
@tool("delete_table", "Move a table to Trash (30 days; the user can restore it). Asks the user first.",
      _obj({"table": {"type": "string"}}, ["table"]), scope="notes")
async def delete_table(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    t = await find_table(ctx, args.get("table"), need="delete")
    n = await core.row_count(ctx.db, t)
    payload = _payload(ctx, t, "trash", _variant="table")
    return await _run(ctx, "notes.delete", payload, t, {"title": f"Move the table {t.name} to Trash",
                                                         "lines": [f"{n:,} rows; restorable for 30 days"]})
