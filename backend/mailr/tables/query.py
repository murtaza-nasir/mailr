"""Reading a table through a view: the grid, the export, `table.stats` and the assistant's
`get_table` and `table_stats` share this one function (tables/views.py has the rules)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.i18n import ApiError
from mailr.models import User
from mailr.tables import core
from mailr.tables import types as ty
from mailr.tables import views as vw
from mailr.tables.models import DataTable, TableRow
from mailr.tables.types import Col, Ctx


@dataclass
class Result:
    cols: dict[str, Col]
    ordered: list[Col]
    spec: dict[str, Any]
    view_id: str | None
    rows: list[TableRow]
    total: int
    titles: dict[str, dict[str, Any]]
    ctx: Ctx


async def run(db: AsyncSession, user: User, t: DataTable, *, view: str | None = None,
              spec: dict[str, Any] | None = None, q: str | None = None) -> Result:
    """Every row of the view (`view`, or the default), with `spec` keys replacing the view's (a
    viewer's unsaved sort and filters), and the table's own search `q`."""
    columns = await core.columns_of(db, t)
    cols = core.cols_map(columns)
    views = await core.views_of(db, t)
    v = next((x for x in views if x.view_id == view), None) if view else next((x for x in views if x.is_default),
                                                                              views[0] if views else None)
    if view and v is None:
        raise ApiError(404, "tables.view_not_found")
    base = dict(v.spec or {}) if v is not None else {}
    lenient = {k: val for k, val in base.items()}
    # A saved view may name a column deleted since: those parts are left out.
    lenient["sort"] = [s for s in base.get("sort") or [] if s.get("col") in cols]
    lenient["filters"] = [f for f in base.get("filters") or [] if f.get("col") in cols]
    if (base.get("group_by") or {}).get("col") not in cols:
        lenient["group_by"] = None
    for k, val in (spec or {}).items():
        if k in vw.EMPTY_SPEC:
            lenient[k] = val
    try:
        clean = vw.clean_spec(lenient, cols, t.primary_column)
    except vw.ViewError as e:
        raise HTTPException(422, str(e)) from None
    ctx = await core.reader(db, user)
    rows = await core.rows_of(db, t)
    total = len(rows)
    rows = vw.search(rows, q or "")
    try:
        rows = vw.apply_filters(rows, clean["filters"], cols, ctx)
    except vw.ViewError as e:
        raise HTTPException(422, str(e)) from None
    refs = core.all_refs(rows, cols)
    titles = await core.titles(db, user, refs, owner_view=access_owner(user, t)) if refs else {}
    plain_titles = {k: v.get("title", "") for k, v in titles.items()}
    rows = vw.sort_rows(rows, clean["sort"], cols, plain_titles)
    ordered = [cols[c.col_id] for c in columns if c.col_id in cols]
    return Result(cols, ordered, clean, v.view_id if v else None, rows, total, titles, ctx)


def access_owner(user: User, t: DataTable) -> bool:
    return t.owner_user_id == user.id


def visible_columns(res: Result) -> list[Col]:
    hidden = set(res.spec.get("hidden") or [])
    return [c for c in res.ordered if c.col_id not in hidden]


def groups_out(res: Result, rows: list[TableRow] | None = None) -> list[dict[str, Any]] | None:
    g = res.spec.get("group_by")
    if not g:
        return None
    plain_titles = {k: v.get("title", "") for k, v in res.titles.items()}
    out = []
    for gr in vw.group_rows(rows if rows is not None else res.rows, g, res.cols, res.ctx, plain_titles):
        out.append({"key": gr.key, "label": gr.label, "count": len(gr.rows), "rows": [r.id for r in gr.rows],
                    "footers": vw.footers(gr.rows, res.spec.get("footers") or {}, res.cols, res.ctx)})
    return out


def stats(res: Result, columns: list[str] | None = None, aggs: list[str] | None = None,
          group_by: dict[str, Any] | None = None) -> dict[str, Any]:
    """Aggregates per column over the view's rows (and per group): every aggregate the column's
    type has unless `aggs` names some."""
    cols = [res.cols[c] for c in (columns or []) if c in res.cols] or \
        [c for c in res.ordered if c.type in ("number", "currency")] or res.ordered[:1]

    def one(rows: list[TableRow]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for c in cols:
            names = [a for a in (aggs or vw.default_aggs(c)) if a in vw.default_aggs(c)]
            vals = [(r.cells or {}).get(c.col_id) for r in rows]
            out[c.col_id] = {"name": c.name, "type": c.type,
                             **{a: vw.aggregate(c, a, vals, res.ctx) for a in names}}
        return out

    result: dict[str, Any] = {"count": len(res.rows), "total": res.total, "columns": one(res.rows)}
    g = group_by if group_by is not None else res.spec.get("group_by")
    if g:
        try:
            g = vw.clean_spec({"group_by": g}, res.cols, None)["group_by"]
        except vw.ViewError as e:
            raise HTTPException(422, str(e)) from None
        plain_titles = {k: v.get("title", "") for k, v in res.titles.items()}
        result["groups"] = [{"key": gr.key, "label": gr.label, "count": len(gr.rows), "columns": one(gr.rows)}
                            for gr in vw.group_rows(res.rows, g, res.cols, res.ctx, plain_titles)]
    return result


def typed_out(res: Result, r: TableRow, cols: list[Col] | None = None) -> dict[str, Any]:
    """A row for the assistant: column names to plain values (links as titles), and kept text."""
    tl = {k: v.get("title", "") for k, v in res.titles.items()}
    out: dict[str, Any] = {}
    for c in cols or res.ordered:
        v = (r.cells or {}).get(c.col_id)
        raw = (r.raw or {}).get(c.col_id)
        if raw and ty.is_empty(v):
            out[c.name] = {"kept_as_text": raw}
        elif not ty.is_empty(v):
            out[c.name] = ty.plain(c, v, titles=tl) if c.type in ("select", "multi_select", "link") else v
    return out
