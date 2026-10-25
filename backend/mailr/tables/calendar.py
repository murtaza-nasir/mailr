"""A table's dates on the calendar.

A viewer may show one date column of a table on their own calendar (`note_viewer_state.
calendar_column`, set with PUT /tables/{id}/state). Each row with a date in that column shows on
that day as an all-day chip with the row's title; a tap opens the row. The choice is per viewer:
turning it on never changes anyone else's calendar. A date-and-time column shows by its date.

The chips are read-only on the calendar (no drag): the date is changed in the table.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.auth import Principal, scope
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.notes.models import NoteViewerState
from mailr.tables import access, core
from mailr.tables import types as ty
from mailr.tables.models import DataTable, TableColumn, TableRow

router = APIRouter(tags=["tables"])
tables_user = scope("notes")

DATE_TYPES = ("date", "datetime")
MAX_DAYS = 62
MAX_ITEMS = 500


def _day(s: str, name: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise HTTPException(422, f"{name} is a date (YYYY-MM-DD)") from e  # i18n: internal


async def check_column(db: AsyncSession, t: DataTable, col_id: str | None) -> str | None:
    """The column to show on the calendar, checked: a date or date-and-time column of the table."""
    if not col_id:
        return None
    c = await db.get(TableColumn, (t.id, str(col_id)))
    if c is None or c.deleted_at is not None:
        raise ApiError(404, "tables.no_such_column")
    if c.type not in DATE_TYPES:
        raise ApiError(422, "tables.calendar_needs_date")
    return c.col_id


def _cell_day(c: TableColumn, v: Any) -> str | None:
    if c.type == "date" and isinstance(v, str):
        return v[:10]
    if c.type == "datetime" and isinstance(v, dict) and isinstance(v.get("at"), str):
        return v["at"][:10]
    return None


# ACT table.on_calendar @api
@router.get("/tables/calendar")
async def table_dates(start: str = Query(...), end: str = Query(...), p: Principal = Depends(tables_user),
                      db: AsyncSession = Depends(get_db)):
    """The rows of the viewer's calendar tables dated from `start` to `end` (both included)."""
    d0, d1 = _day(start, "start"), _day(end, "end")
    if d1 < d0 or (d1 - d0) > timedelta(days=MAX_DAYS):
        raise ApiError(422, "tables.max_days", max=MAX_DAYS)
    user = p.user
    pairs = (await db.execute(
        select(DataTable, NoteViewerState.calendar_column)
        .join(NoteViewerState, and_(NoteViewerState.item_kind == "table", NoteViewerState.item_id == DataTable.id,
                                    NoteViewerState.user_id == user.id))
        .where(NoteViewerState.calendar_column.is_not(None), DataTable.deleted_at.is_(None),
               access.visible(user)))).all()
    items: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    for t, col_id in pairs:
        if access.level(user, t) == "none":
            continue
        cols = {c.col_id: c for c in await core.columns_of(db, t)}
        c = cols.get(col_id)
        if c is None or c.type not in DATE_TYPES:
            continue
        tables.append({"id": t.pid, "name": t.name, "color": t.color, "column": c.col_id, "column_name": c.name})
        cell = TableRow.cells[c.col_id]
        val = cell.astext if c.type == "date" else cell["at"].astext
        rows = (await db.execute(select(TableRow).where(
            TableRow.table_id == t.id, TableRow.deleted_at.is_(None),
            val >= d0.isoformat(), val < (d1 + timedelta(days=1)).isoformat()).limit(MAX_ITEMS))).scalars().all()
        cmap = {k: ty.col_of(v) for k, v in cols.items()}
        for r in rows:
            day = _cell_day(c, (r.cells or {}).get(c.col_id))
            if day is None:
                continue
            items.append({"table": t.pid, "table_name": t.name, "color": t.color, "row": r.id,
                          "title": core.row_title(t, cmap, r), "date": day, "column_name": c.name})
    items.sort(key=lambda x: (x["date"], x["table_name"].lower(), x["title"].lower()))
    return {"items": items[:MAX_ITEMS], "tables": tables}
