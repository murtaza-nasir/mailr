"""Views: filters, sort, grouping, hidden columns and footer aggregates. A table holds at most 10,000 rows, so a view is evaluated here over the rows of one
query: the grid, the export, the assistant's `get_table` and `table_stats` all use these
functions, and give the same answer.

A view spec:

    {"sort": [{"col": "c1", "dir": "asc"}],                       up to 3 keys; [] = manual order
     "filters": [{"col": "c2", "op": "is", "value": "o1"}],       all must match
     "group_by": {"col": "c3", "by": "month"} | null,             by: day | week | month | year (dates)
     "hidden": ["c4"], "footers": {"c5": "sum"}, "phone_columns": ["c2"]}

Filter values for dates are dates ("2026-10-05") or quick-add date words ("today", "fri"), read
when the view is read, so "in the last 7 days" stays relative.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from mailr.tables import types as ty
from mailr.tables.types import Col, Ctx

OPS: dict[str, tuple[str, ...]] = {
    "text": ("contains", "not_contains", "is", "is_not", "starts_with", "ends_with", "empty", "not_empty"),
    "number": ("eq", "ne", "lt", "lte", "gt", "gte", "between", "empty", "not_empty"),
    "date": ("is", "before", "after", "on_or_before", "on_or_after", "between", "last_days", "next_days", "empty",
             "not_empty"),
    "checkbox": ("checked", "not_checked"),
    "select": ("is", "is_not", "any_of", "none_of", "empty", "not_empty"),
    "multi_select": ("has_any", "has_all", "has_none", "empty", "not_empty"),
    "link": ("links_to", "not_links_to", "empty", "not_empty"),
}
OPS["currency"] = OPS["number"]
OPS["datetime"] = OPS["date"]
# Other apps' operator spellings, per type family.
ALIASES = {
    "text": {"eq": "is", "ne": "is_not", "startswith": "starts_with", "endswith": "ends_with", "=": "is",
             "!=": "is_not"},
    "number": {"=": "eq", "!=": "ne", "<": "lt", "<=": "lte", ">": "gt", ">=": "gte", "is": "eq", "is_not": "ne"},
    "date": {"eq": "is", "gt": "after", "gte": "on_or_after", "lt": "before", "lte": "on_or_before", "=": "is"},
    "select": {"eq": "is", "ne": "is_not", "in": "any_of", "not_in": "none_of"},
    "multi_select": {"contains": "has_any", "is": "has_any", "any_of": "has_any", "none_of": "has_none"},
    "checkbox": {"is": "checked", "eq": "checked"},
    "link": {"is": "links_to", "contains": "links_to"},
}
FOOTERS: dict[str, tuple[str, ...]] = {
    "number": ("sum", "avg", "median", "min", "max", "count"),
    "date": ("earliest", "latest", "range"),
    "checkbox": ("checked", "unchecked", "percent_checked"),
    "select": ("per_option",),
    "multi_select": ("per_option",),
}
FOOTERS["currency"] = FOOTERS["number"]
FOOTERS["datetime"] = FOOTERS["date"]
COMMON_FOOTERS = ("filled", "empty", "unique")
GROUP_TYPES = ("select", "multi_select", "checkbox", "date", "datetime", "link")
DATE_BUCKETS = ("day", "week", "month", "year")
EMPTY_SPEC: dict[str, Any] = {"sort": [], "filters": [], "group_by": None, "hidden": [], "footers": {},
                              "phone_columns": []}


class ViewError(ValueError):
    pass


def family(t: str) -> str:
    return "number" if t in ("number", "currency") else "date" if t in ("date", "datetime") else t


def op_of(col: Col, op: str) -> str:
    op = str(op or "").strip().lower().replace(" ", "_")
    op = ALIASES.get(family(col.type), {}).get(op, op)
    if op not in OPS.get(col.type, ()):
        raise ViewError(f"{op!r} is not a filter for {ty.LABELS.get(col.type, col.type).lower()} columns "
                        f"(column {col.name!r}); use {', '.join(OPS.get(col.type, ()))}")
    return op


def clean_spec(spec: dict[str, Any] | None, cols: dict[str, Col], primary: str | None) -> dict[str, Any]:
    """A view spec with only known columns and valid operators. Raises ViewError."""
    s = dict(EMPTY_SPEC)
    spec = spec or {}
    sort = []
    for k in (spec.get("sort") or [])[:3]:
        c = cols.get(str(k.get("col")))
        if c is None:
            raise ViewError(f"unknown column {k.get('col')!r} in sort")
        sort.append({"col": c.col_id, "dir": "desc" if str(k.get("dir", "asc")).lower() == "desc" else "asc"})
    s["sort"] = sort
    filters = []
    for f in (spec.get("filters") or [])[:30]:
        c = cols.get(str(f.get("col")))
        if c is None:
            raise ViewError(f"unknown column {f.get('col')!r} in a filter")
        op = op_of(c, f.get("op"))
        out = {"col": c.col_id, "op": op}
        if op not in ("empty", "not_empty", "checked", "not_checked"):
            if "value" not in f or f["value"] in (None, ""):
                raise ViewError(f"the filter on {c.name!r} needs a value")
            out["value"] = f["value"]
        filters.append(out)
    s["filters"] = filters
    g = spec.get("group_by")
    if g:
        if isinstance(g, str):
            g = {"col": g}
        c = cols.get(str(g.get("col")))
        if c is None or c.type not in GROUP_TYPES or (c.type == "link" and (c.config or {}).get("multiple")):
            raise ViewError("group by a select, multi-select, checkbox, date or single link column")
        out = {"col": c.col_id}
        if c.type in ("date", "datetime"):
            out["by"] = g.get("by") if g.get("by") in DATE_BUCKETS else "day"
        s["group_by"] = out
    s["hidden"] = [h for h in dict.fromkeys(spec.get("hidden") or []) if h in cols and h != primary]
    foot = {}
    for k, agg in (spec.get("footers") or {}).items():
        c = cols.get(k)
        if c is None or not agg:
            continue
        if agg not in FOOTERS.get(c.type, ()) + COMMON_FOOTERS:
            raise ViewError(f"{agg!r} is not a footer for column {c.name!r}")
        foot[k] = agg
    s["footers"] = foot
    s["phone_columns"] = [p for p in dict.fromkeys(spec.get("phone_columns") or []) if p in cols and p != primary][:2]
    return s


# ---------------------------------------------------------------- values for comparing


def instant(v: dict[str, Any]) -> datetime:
    at = datetime.fromisoformat(v["at"])
    try:
        return at.replace(tzinfo=ZoneInfo(v.get("tz") or "UTC")).astimezone(UTC)
    except Exception:  # noqa: BLE001
        return at.replace(tzinfo=UTC)


def day_of(col: Col, v: Any, tz: str) -> date | None:
    """The date of a date or datetime cell (a datetime in the viewer's zone)."""
    if ty.is_empty(v):
        return None
    if col.type == "date":
        return date.fromisoformat(v)
    try:
        return instant(v).astimezone(ZoneInfo(tz)).date()
    except Exception:  # noqa: BLE001
        return datetime.fromisoformat(v["at"]).date()


def sort_key(col: Col, v: Any, titles: dict[str, str]) -> Any:
    t = col.type
    if t == "text":
        return v.casefold()
    if t in ("number", "currency"):
        return Decimal(v)
    if t == "date":
        return v
    if t == "datetime":
        return instant(v).timestamp()
    if t == "checkbox":
        return 1 if v else 0
    if t in ("select", "multi_select"):
        idx = {o["id"]: i for i, o in enumerate(col.options)}
        ids = v if isinstance(v, list) else [v]
        return tuple(idx.get(i, 999) for i in ids)
    if t == "link":
        return tuple((titles.get(ty.ref_key(r)) or "").casefold() for r in v)
    return str(v)


class _Rev:
    """Reverses the order of any comparable value (for descending keys)."""

    __slots__ = ("v",)

    def __init__(self, v: Any):
        self.v = v

    def __lt__(self, o: _Rev) -> bool:
        return o.v < self.v

    def __eq__(self, o: object) -> bool:
        return isinstance(o, _Rev) and o.v == self.v


def sort_rows(rows: list[Any], sort: list[dict[str, Any]], cols: dict[str, Col], titles: dict[str, str]) -> list[Any]:
    """Rows in the view's order: its keys (empty cells last whatever the direction), then the
    manual order. No keys: the manual order."""
    def key(r: Any) -> tuple:
        parts: list[Any] = []
        for k in sort:
            c = cols.get(k["col"])
            if c is None:
                continue
            v = (r.cells or {}).get(c.col_id)
            if ty.is_empty(v):
                parts.append((1, 0))
                continue
            sk = sort_key(c, v, titles)
            parts.append((0, _Rev(sk) if k["dir"] == "desc" else sk))
        parts.append((r.order, r.id))
        return tuple(parts)

    return sorted(rows, key=key)


# ---------------------------------------------------------------- filters


def _date_value(v: Any, ctx: Ctx) -> date:
    if isinstance(v, date):
        return v
    d = ty.parse_date(str(v), ctx)
    if d is None:
        raise ViewError(f"{v!r} is not a date")
    return d


def _num(v: Any) -> Decimal:
    try:
        return ty.to_decimal(v)
    except Exception:  # noqa: BLE001
        d, _ = ty.parse_number(str(v))
        if d is None:
            raise ViewError(f"{v!r} is not a number") from None
        return d


def _ids(col: Col, v: Any) -> set[str]:
    """Option ids from a filter value: ids or names, one or a list."""
    vals = v if isinstance(v, list) else [v]
    out = set()
    for x in vals:
        o = next((o for o in col.options if o["id"] == x), None) or ty.option_by_name(col, str(x))
        out.add(o["id"] if o else str(x))
    return out


def _refkeys(v: Any) -> set[str]:
    vals = v if isinstance(v, list) else [v]
    return {ty.ref_key(x) if isinstance(x, dict) else str(x) for x in vals}


def match(col: Col, f: dict[str, Any], v: Any, ctx: Ctx) -> bool:
    op, want = f["op"], f.get("value")
    empty = ty.is_empty(v)
    if op == "empty":
        return empty
    if op == "not_empty":
        return not empty
    t = col.type
    if t == "checkbox":
        return bool(v) if op == "checked" else not v
    if t == "text":
        s, w = ("" if empty else v).casefold(), str(want).casefold()
        return {"contains": w in s, "not_contains": w not in s, "is": s == w, "is_not": s != w,
                "starts_with": s.startswith(w), "ends_with": s.endswith(w)}[op]
    if t in ("select", "multi_select"):
        have = set() if empty else set(v if isinstance(v, list) else [v])
        ids = _ids(col, want)
        if op in ("is", "any_of", "has_any"):
            return bool(have & ids)
        if op in ("is_not", "none_of", "has_none"):
            return not (have & ids)
        if op == "has_all":
            return ids <= have
    if t == "link":
        have = set() if empty else {ty.ref_key(r) for r in v}
        keys = _refkeys(want)
        return bool(have & keys) if op == "links_to" else not (have & keys)
    if empty:
        return op in ("ne", "is_not")
    if t in ("number", "currency"):
        x = Decimal(v)
        if op == "between":
            lo, hi = (want if isinstance(want, list) else [want, want])[:2]
            return _num(lo) <= x <= _num(hi)
        w = _num(want)
        return {"eq": x == w, "ne": x != w, "lt": x < w, "lte": x <= w, "gt": x > w, "gte": x >= w}[op]
    if t in ("date", "datetime"):
        d = day_of(col, v, ctx.tz)
        if op in ("last_days", "next_days"):
            n = int(_num(want))
            return ctx.today - timedelta(days=n) <= d <= ctx.today if op == "last_days" \
                else ctx.today <= d <= ctx.today + timedelta(days=n)
        if op == "between":
            lo, hi = (want if isinstance(want, list) else [want, want])[:2]
            return _date_value(lo, ctx) <= d <= _date_value(hi, ctx)
        w = _date_value(want, ctx)
        return {"is": d == w, "before": d < w, "after": d > w, "on_or_before": d <= w, "on_or_after": d >= w}[op]
    return True


def apply_filters(rows: list[Any], filters: list[dict[str, Any]], cols: dict[str, Col], ctx: Ctx) -> list[Any]:
    fs = [(cols[f["col"]], f) for f in filters if f.get("col") in cols]
    if not fs:
        return list(rows)
    return [r for r in rows if all(match(c, f, (r.cells or {}).get(c.col_id), ctx) for c, f in fs)]


def search(rows: list[Any], q: str) -> list[Any]:
    """Rows whose text holds every word of `q` (the table's own search, `/`)."""
    words = [w.casefold() for w in (q or "").split() if w]
    if not words:
        return list(rows)
    return [r for r in rows if all(w in (r.search_text or "").casefold() for w in words)]


def filled_by_filters(filters: list[dict[str, Any]], cols: dict[str, Col], ctx: Ctx) -> dict[str, Any]:
    """The cells a new row gets so it stays in view (row.add): "Exercise is Squat"
    fills Squat; a checked filter checks the box; a date "is" fills that date."""
    out: dict[str, Any] = {}
    for f in filters:
        c = cols.get(f.get("col"))
        if c is None:
            continue
        op, v = f["op"], f.get("value")
        try:
            if c.type == "text" and op == "is":
                out[c.col_id] = str(v)
            elif c.type in ("select",) and op in ("is", "any_of"):
                ids = _ids(c, v)
                if len(ids) == 1:
                    out[c.col_id] = next(iter(ids))
            elif c.type == "multi_select" and op in ("has_any", "has_all"):
                out[c.col_id] = [i for i in (o["id"] for o in c.options) if i in _ids(c, v)]
            elif c.type == "checkbox" and op == "checked":
                out[c.col_id] = True
            elif c.type in ("number", "currency") and op == "eq":
                out[c.col_id] = ty.norm_decimal(_num(v))
            elif c.type == "date" and op == "is":
                out[c.col_id] = _date_value(v, ctx).isoformat()
            elif c.type == "link" and op == "links_to" and isinstance(v, dict):
                out[c.col_id] = [v]
        except ViewError:
            continue
    return out


# ---------------------------------------------------------------- aggregates


def _ukey(v: Any) -> Any:
    if isinstance(v, list):
        return tuple(ty.ref_key(x) if isinstance(x, dict) else x for x in v)
    if isinstance(v, dict):
        return tuple(sorted(v.items()))
    return v


def aggregate(col: Col, agg: str, values: list[Any], ctx: Ctx) -> Any:
    """One footer value over a column's cells (empty cells included in `values` as None)."""
    filled = [v for v in values if not ty.is_empty(v)]
    if agg == "filled":
        return len(filled)
    if agg == "empty":
        return len(values) - len(filled)
    if agg == "unique":
        return len({_ukey(v) for v in filled})
    t = col.type
    if t in ("number", "currency"):
        nums = [Decimal(v) for v in filled]
        if agg == "count":
            return len(nums)
        if not nums:
            return None
        if agg == "sum":
            return ty.norm_decimal(sum(nums, Decimal(0)))
        if agg == "avg":
            return ty.norm_decimal((sum(nums, Decimal(0)) / len(nums)).quantize(Decimal("1e-6")))
        if agg == "median":
            return ty.norm_decimal(Decimal(statistics.median(nums)))
        if agg == "min":
            return ty.norm_decimal(min(nums))
        if agg == "max":
            return ty.norm_decimal(max(nums))
    if t in ("date", "datetime"):
        days = sorted(day_of(col, v, ctx.tz) for v in filled)
        if not days:
            return None
        if agg == "earliest":
            return days[0].isoformat()
        if agg == "latest":
            return days[-1].isoformat()
        if agg == "range":
            return (days[-1] - days[0]).days
    if t == "checkbox":
        n = len(values)
        c = len(filled)
        if agg == "checked":
            return c
        if agg == "unchecked":
            return n - c
        if agg == "percent_checked":
            return round(100 * c / n, 1) if n else None
    if t in ("select", "multi_select") and agg == "per_option":
        counts: dict[str, int] = {}
        for v in filled:
            for i in v if isinstance(v, list) else [v]:
                counts[i] = counts.get(i, 0) + 1
        names = {o["id"]: o["name"] for o in col.options}
        top = sorted(counts.items(), key=lambda kv: (-kv[1], names.get(kv[0], "")))
        return {"top": [{"option": k, "name": names.get(k, ""), "count": n} for k, n in top[:3]],
                "more": max(0, len(top) - 3)}
    raise ViewError(f"{agg!r} is not a footer for column {col.name!r}")


def footers(rows: list[Any], spec_footers: dict[str, str], cols: dict[str, Col], ctx: Ctx) -> dict[str, Any]:
    out = {}
    for k, agg in (spec_footers or {}).items():
        c = cols.get(k)
        if c is None:
            continue
        out[k] = {"agg": agg, "value": aggregate(c, agg, [(r.cells or {}).get(k) for r in rows], ctx)}
    return out


def default_aggs(col: Col) -> tuple[str, ...]:
    return FOOTERS.get(col.type, ()) + COMMON_FOOTERS


# ---------------------------------------------------------------- grouping


@dataclass
class Group:
    key: str
    label: str
    rows: list[Any] = field(default_factory=list)
    sort: Any = None


def _bucket(d: date, by: str) -> tuple[str, str, Any]:
    if by == "year":
        return str(d.year), str(d.year), d.year
    if by == "month":
        return f"{d.year}-{d.month:02d}", d.strftime("%B %Y"), (d.year, d.month)
    if by == "week":
        start = d - timedelta(days=d.weekday())
        return start.isoformat(), f"Week of {start.strftime('%-d %b %Y')}", start
    return d.isoformat(), d.strftime("%a %-d %b %Y"), d


def group_rows(rows: list[Any], g: dict[str, Any], cols: dict[str, Col], ctx: Ctx, titles: dict[str, str]
               ) -> list[Group]:
    """Groups in their order (options in option order, dates ascending, empty last). A
    multi-select row shows in each of its groups."""
    c = cols.get(g.get("col"))
    if c is None:
        return [Group("", "", list(rows))]
    groups: dict[str, Group] = {}
    empty = Group("", "Empty", [], (1,))
    for r in rows:
        v = (r.cells or {}).get(c.col_id)
        if ty.is_empty(v):
            if c.type == "checkbox":
                gr = groups.setdefault("false", Group("false", "Not checked", [], (0, 1)))
                gr.rows.append(r)
            else:
                empty.rows.append(r)
            continue
        if c.type == "checkbox":
            groups.setdefault("true", Group("true", "Checked", [], (0, 0))).rows.append(r)
        elif c.type in ("select", "multi_select"):
            idx = {o["id"]: (i, o["name"]) for i, o in enumerate(c.options)}
            for oid in v if isinstance(v, list) else [v]:
                i, name = idx.get(oid, (999, ""))
                groups.setdefault(oid, Group(oid, name, [], (0, i))).rows.append(r)
        elif c.type in ("date", "datetime"):
            k, label, s = _bucket(day_of(c, v, ctx.tz), g.get("by") or "day")
            groups.setdefault(k, Group(k, label, [], (0, s))).rows.append(r)
        elif c.type == "link":
            k = ty.ref_key(v[0])
            groups.setdefault(k, Group(k, titles.get(k) or k, [], (0, (titles.get(k) or k).casefold()))).rows.append(r)
    out = sorted(groups.values(), key=lambda gr: gr.sort)
    if empty.rows:
        out.append(empty)
    return out
