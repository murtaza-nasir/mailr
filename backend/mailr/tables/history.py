"""Per-row history, undo by batch, restore to a point in time and the nightly snapshots.

Every change of a table is a numbered entry (one sequence per table, `tables.seq`):
`table_row_changes` for rows (add, edit, delete, restore, move: the changed fields before and
after; a delete keeps the whole row before, a restore the whole row after) and
`table_column_changes` for columns, views and the table's own fields. Both are kept 30 days.

- `revert(batch)` reverses the changes of one action (an undo; the redo is the revert of the
  revert). A cell changed by someone else since is left as it is.
- `state_at(T)` rebuilds the table at moment T: the newest snapshot taken at or before T (or an
  empty table at its creation), then every change after the snapshot up to T, in sequence order.
- `restore_point(T)` compares that state with the current table and writes the difference as
  ordinary changes by "Restore", so one undo, and the history, take it back.
- `nightly()` snapshots every table that changed since its last snapshot (zlib JSON), and prunes:
  changes older than 30 days that the newest snapshot older than 30 days holds, older snapshots
  (the newest always kept), rows and columns deleted more than 30 days ago, and tables in Trash.
"""

from __future__ import annotations

import copy
import json
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import merge3
from mailr.i18n import ApiError
from mailr.models import User
from mailr.notes import localonly
from mailr.tables import actions, core
from mailr.tables import types as ty
from mailr.tables.core import Log
from mailr.tables.models import (
    DataTable,
    TableColumn,
    TableRow,
    TableRowChange,
    TableSchemaChange,
    TableSnapshot,
    TableView,
)

SESSION_GAP = timedelta(minutes=2)


# ---------------------------------------------------------------- state


@dataclass
class State:
    seq: int = 0
    fields: dict[str, Any] = field(default_factory=dict)
    columns: dict[str, dict[str, Any]] = field(default_factory=dict)
    views: dict[str, dict[str, Any]] = field(default_factory=dict)
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)

    def out(self) -> dict[str, Any]:
        return {"seq": self.seq, "fields": self.fields, "columns": self.columns, "views": self.views, "rows": self.rows}


async def current_state(db: AsyncSession, t: DataTable) -> State:
    cols = await core.columns_of(db, t, deleted=True)
    return State(seq=t.seq, fields=core.fields_of(t),
                 columns={c.col_id: core.column_def(c) for c in cols},
                 views={v.view_id: core.view_def(v) for v in await core.views_of(db, t)},
                 rows={r.id: core.row_state(r) for r in await core.rows_of(db, t)})


def _pack(s: State) -> bytes:
    return zlib.compress(json.dumps(s.out(), separators=(",", ":"), default=str).encode(), 6)


def _unpack(b: bytes) -> State:
    d = json.loads(zlib.decompress(b))
    return State(seq=d["seq"], fields=d["fields"], columns=d["columns"], views=d["views"], rows=d["rows"])


def _set_partial(target: dict[str, Any], part: dict[str, Any]) -> None:
    for k in ("cells", "raw"):
        if k in part:
            d = dict(target.get(k) or {})
            for col, v in (part[k] or {}).items():
                if v is None or v == "":
                    d.pop(col, None)
                else:
                    d[col] = v
            target[k] = d
    if part.get("order") is not None:
        target["order"] = part["order"]


def apply_row_change(s: State, ch: TableRowChange) -> None:
    if ch.op in ("add", "restore"):
        s.rows[ch.row_id] = copy.deepcopy(ch.after or {})
    elif ch.op == "delete":
        s.rows.pop(ch.row_id, None)
    elif ch.op in ("edit", "move"):
        r = s.rows.get(ch.row_id)
        if r is not None:
            _set_partial(r, ch.after or {})


def apply_schema_change(s: State, ch: TableSchemaChange) -> None:
    a = ch.after or {}
    op = ch.op
    if op == "table":
        s.fields.update(a)
    elif op == "primary":
        s.fields["primary_column"] = a.get("primary_column")
    elif op in ("add", "define", "restore") and ch.col_id:
        s.columns[ch.col_id] = {**s.columns.get(ch.col_id, {}), **copy.deepcopy(a), "deleted": False}
    elif op == "retype" and ch.col_id:
        s.columns[ch.col_id] = {**s.columns.get(ch.col_id, {}), **copy.deepcopy(a)}
        for rid, (v, raw) in ((ch.values or {}).get("after") or {}).items():
            r = s.rows.get(rid)
            if r is not None:
                _set_partial(r, {"cells": {ch.col_id: v}, "raw": {ch.col_id: raw}})
    elif op == "rename" and ch.col_id in s.columns:
        s.columns[ch.col_id]["name"] = a.get("name")
    elif op in ("config", "options") and ch.col_id in s.columns:
        s.columns[ch.col_id].update(copy.deepcopy(a))
    elif op == "reorder" and ch.col_id in s.columns:
        s.columns[ch.col_id]["order"] = a.get("order")
    elif op == "delete" and ch.col_id in s.columns:
        s.columns[ch.col_id]["deleted"] = True
    elif op == "view_save" and ch.view_id:
        s.views[ch.view_id] = copy.deepcopy(a)
    elif op == "view_delete" and ch.view_id:
        s.views.pop(ch.view_id, None)


async def _changes(db: AsyncSession, t: DataTable, after_seq: int, *, upto_at: datetime | None = None,
                   upto_seq: int | None = None, row_id: str | None = None) -> list[Any]:
    rq = select(TableRowChange).where(TableRowChange.table_id == t.id, TableRowChange.seq > after_seq)
    sq = select(TableSchemaChange).where(TableSchemaChange.table_id == t.id, TableSchemaChange.seq > after_seq)
    if upto_at is not None:
        rq, sq = rq.where(TableRowChange.at <= upto_at), sq.where(TableSchemaChange.at <= upto_at)
    if upto_seq is not None:
        rq, sq = rq.where(TableRowChange.seq <= upto_seq), sq.where(TableSchemaChange.seq <= upto_seq)
    if row_id is not None:
        rq = rq.where(TableRowChange.row_id == row_id)
        sq = sq.where(TableSchemaChange.op == "retype")
    out = list((await db.execute(rq)).scalars()) + list((await db.execute(sq)).scalars())
    return sorted(out, key=lambda c: c.seq)


async def _base(db: AsyncSession, t: DataTable, *, at: datetime | None, seq: int | None) -> State:
    q = select(TableSnapshot).where(TableSnapshot.table_id == t.id)
    if at is not None:
        q = q.where(TableSnapshot.taken_at <= at)
    if seq is not None:
        q = q.where(TableSnapshot.seq <= seq)
    snap = (await db.execute(q.order_by(TableSnapshot.seq.desc()).limit(1))).scalar_one_or_none()
    if snap is not None:
        return _unpack(snap.data)
    first = (await db.execute(select(func.min(TableSchemaChange.seq)).where(TableSchemaChange.table_id == t.id))
             ).scalar_one_or_none()
    first_row = (await db.execute(select(func.min(TableRowChange.seq)).where(TableRowChange.table_id == t.id))
                 ).scalar_one_or_none()
    lowest = min(x for x in (first, first_row, t.seq + 1) if x is not None)
    if lowest > 1:
        raise HTTPException(409, "The history of this table does not reach back that far")
    return State()


async def state_at(db: AsyncSession, t: DataTable, *, at: datetime | None = None, seq: int | None = None) -> State:
    """The table as it was at moment `at` (or after change `seq`)."""
    s = await _base(db, t, at=at, seq=seq)
    for ch in await _changes(db, t, s.seq, upto_at=at, upto_seq=seq):
        if isinstance(ch, TableRowChange):
            apply_row_change(s, ch)
        else:
            apply_schema_change(s, ch)
        s.seq = ch.seq
    return s


async def row_state_at(db: AsyncSession, t: DataTable, row_id: str, seq: int) -> dict[str, Any] | None:
    """One row as it was after change `seq` (None: it did not exist or was deleted then)."""
    s = await _base(db, t, at=None, seq=seq)
    s.rows = {row_id: s.rows[row_id]} if row_id in s.rows else {}
    for ch in await _changes(db, t, s.seq, upto_seq=seq, row_id=row_id):
        if isinstance(ch, TableRowChange):
            apply_row_change(s, ch)
        else:
            apply_schema_change(s, ch)
    return s.rows.get(row_id)


# ---------------------------------------------------------------- snapshots


async def take_snapshot(db: AsyncSession, t: DataTable, *, at: datetime | None = None) -> TableSnapshot:
    s = await current_state(db, t)
    snap = TableSnapshot(table_id=t.id, seq=t.seq, data=_pack(s))
    if at is not None:
        snap.taken_at = at
    db.add(snap)
    await db.flush()
    return snap


async def nightly(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """The nightly job: snapshots of changed tables, then the 30-day cleanup."""
    now = now or datetime.now(UTC)
    out = {"snapshots": 0, "purged": 0, "changes": 0, "rows": 0, "columns": 0}
    last = select(TableSnapshot.table_id, func.max(TableSnapshot.seq).label("s")).group_by(TableSnapshot.table_id).subquery()
    rows = (await db.execute(select(DataTable).outerjoin(last, last.c.table_id == DataTable.id).where(
        DataTable.seq > func.coalesce(last.c.s, 0)))).scalars().all()
    for t in rows:
        await take_snapshot(db, t, at=now)
        out["snapshots"] += 1
    cut = now - timedelta(days=core.HISTORY_DAYS)
    for t in (await db.execute(select(DataTable).where(DataTable.deleted_at < now - timedelta(days=core.TRASH_DAYS)))).scalars().all():
        await actions.purge_now(db, t)
        out["purged"] += 1
    # Per table: the newest snapshot older than the cut is the base a restore within 30 days
    # starts from; changes it holds and older snapshots go. The newest snapshot always stays.
    bases = (await db.execute(text(
        "SELECT DISTINCT ON (table_id) table_id, id, seq FROM table_snapshots WHERE taken_at < :cut "
        "ORDER BY table_id, seq DESC"), {"cut": cut})).all()
    for tid, sid, sseq in bases:
        r1 = await db.execute(delete(TableRowChange).where(TableRowChange.table_id == tid, TableRowChange.seq <= sseq,
                                                           TableRowChange.at < cut))
        r2 = await db.execute(delete(TableSchemaChange).where(TableSchemaChange.table_id == tid,
                                                              TableSchemaChange.seq <= sseq, TableSchemaChange.at < cut))
        out["changes"] += (r1.rowcount or 0) + (r2.rowcount or 0)
        await db.execute(delete(TableSnapshot).where(TableSnapshot.table_id == tid, TableSnapshot.id != sid,
                                                     TableSnapshot.taken_at < cut))
    # Rows and columns deleted more than 30 days ago are gone for good.
    old_rows = (await db.execute(select(TableRow.id, TableRow.table_id).where(TableRow.deleted_at < cut))).all()
    if old_rows:
        from mailr.models import ItemVersion

        ids = [r.id for r in old_rows]
        for i in range(0, len(ids), 5000):
            await db.execute(delete(ItemVersion).where(ItemVersion.kind == "row", ItemVersion.item_id.in_(ids[i:i + 5000])))
            await db.execute(delete(TableRow).where(TableRow.id.in_(ids[i:i + 5000])))
        out["rows"] = len(ids)
    for c in (await db.execute(select(TableColumn).where(TableColumn.deleted_at < cut))).scalars().all():
        await db.execute(text("UPDATE table_rows SET cells = cells - :c, raw = raw - :c WHERE table_id = :t "
                              "AND (cells ? :c OR raw ? :c)"), {"c": c.col_id, "t": c.table_id})
        await db.delete(c)
        out["columns"] += 1
    await db.flush()
    return out


# ---------------------------------------------------------------- reading the history


def _by_name(by: dict[str, Any] | None, source: str, names: dict[int, str] | None = None) -> str:
    """Who made a change, in words. `names`: user id → that user's name for the assistant
    (`_assistant_names`), so a change by someone's assistant shows the name they gave it."""
    by = by or {}
    if by.get("kind") == "restore":
        return "Restore"
    if by.get("kind") == "import":
        return "Import"
    if by.get("kind") == "assistant":
        from mailr.knowledge.profile import DEFAULT_NAME

        return (names or {}).get(by.get("id"), DEFAULT_NAME) if isinstance(by.get("id"), int) else DEFAULT_NAME
    if by.get("kind") == "agent":
        return f"Agent {by.get('agent') or ''}".strip()
    return by.get("name") or "Someone"


def _cell_text(col: dict[str, Any] | None, v: Any, raw: Any = None) -> str:
    if raw:
        return str(raw)
    if col is None:
        return "" if v is None else str(v)
    return ty.plain(ty.Col(col.get("col_id", ""), col.get("name", ""), col.get("type", "text"), col.get("config") or {}), v)


def describe_row_change(ch: TableRowChange, cols: dict[str, dict[str, Any]]) -> str:
    if ch.op == "add":
        return "Added"
    if ch.op == "delete":
        return "Deleted"
    if ch.op == "restore":
        return "Restored"
    if ch.op == "move":
        return "Moved"
    parts = []
    b, a = ch.before or {}, ch.after or {}
    keys = list(dict.fromkeys(list((a.get("cells") or {}).keys()) + list((a.get("raw") or {}).keys())))
    for k in keys[:4]:
        col = cols.get(k)
        name = (col or {}).get("name") or "a deleted column"
        old = _cell_text(col, (b.get("cells") or {}).get(k), (b.get("raw") or {}).get(k))
        new = _cell_text(col, (a.get("cells") or {}).get(k), (a.get("raw") or {}).get(k))
        if (col or {}).get("type") == "text" and (len(old) > 40 or len(new) > 40):
            parts.append(f"{name} changed")
        else:
            parts.append(f"{name} {old or '(empty)'} → {new or '(empty)'}")
    if len(keys) > 4:
        parts.append(f"+{len(keys) - 4} more")
    return ", ".join(parts) or "Edited"


def describe_schema_change(ch: TableSchemaChange, cols: dict[str, dict[str, Any]]) -> str:
    b, a = ch.before or {}, ch.after or {}
    name = (cols.get(ch.col_id or "") or a or b).get("name") or "a column"
    return {
        "add": f"Added column {a.get('name') or name}",
        "rename": f"Renamed column {b.get('name')} to {a.get('name')}",
        "retype": f"Changed {name} from {ty.LABELS.get(b.get('type'), b.get('type'))} to "
                  f"{ty.LABELS.get(a.get('type'), a.get('type'))}",
        "define": f"Set column {name} back",
        "config": f"Changed the settings of {name}",
        "options": f"Changed the options of {name}",
        "reorder": f"Moved column {name}",
        "delete": f"Deleted column {name}",
        "restore": f"Restored column {name}",
        "primary": "Changed the primary column",
        "table": "Renamed the table" if "name" in a else (
            ("Turned Local models only " + ("on" if a["local_only"] else "off")) if "local_only" in a
            else "Changed the table"),
        "view_save": f"Saved view {a.get('name') or ''}".strip(),
        "view_delete": f"Deleted view {b.get('name') or ''}".strip(),
    }.get(ch.op, ch.op)


async def _col_defs(db: AsyncSession, t: DataTable) -> dict[str, dict[str, Any]]:
    return {c.col_id: core.column_def(c) for c in await core.columns_of(db, t, deleted=True)}


async def _assistant_names(db: AsyncSession, items: list[Any]) -> dict[int, str]:
    """The assistant's name of each user whose assistant made one of these changes."""
    from mailr.knowledge import profile

    ids = {(c.by or {}).get("id") for c in items if (c.by or {}).get("kind") == "assistant"}
    return {i: await profile.name_of(db, i) for i in ids if isinstance(i, int)}


def change_out(ch: Any, cols: dict[str, dict[str, Any]], names: dict[int, str] | None = None) -> dict[str, Any]:
    if isinstance(ch, TableRowChange):
        return {"seq": ch.seq, "at": core.iso(ch.at), "by": ch.by or {}, "who": _by_name(ch.by, ch.source, names),
                "source": ch.source, "kind": "row", "op": ch.op, "row": ch.row_id, "batch": ch.batch,
                "before": ch.before, "after": ch.after, "summary": describe_row_change(ch, cols)}
    return {"seq": ch.seq, "at": core.iso(ch.at), "by": ch.by or {}, "who": _by_name(ch.by, ch.source, names),
            "source": ch.source, "kind": "column" if ch.col_id else "view" if ch.view_id else "table", "op": ch.op,
            "column": ch.col_id, "view": ch.view_id, "batch": ch.batch, "before": ch.before, "after": ch.after,
            "summary": describe_schema_change(ch, cols)}


def _session_summary(who: str, items: list[Any]) -> str:
    """One line for a session: "Dana edited 12 rows", "Dana imported 480 rows", "Dana created
    the table, added 3 rows", "Dana deleted column Weight"."""
    rows: dict[str, set[str]] = {}
    other: list[str] = []
    created = {ch.batch for ch in items if isinstance(ch, TableSchemaChange) and ch.op == "table" and not ch.before}
    for ch in sorted(items, key=lambda c: c.seq):
        if isinstance(ch, TableRowChange):
            op = "import" if ch.op == "add" and ch.source == "import" else ch.op
            rows.setdefault(op, set()).add(ch.row_id)
        elif ch.batch not in created:
            other.append(describe_schema_change(ch, {}))
    verbs = {"import": "imported", "add": "added", "edit": "edited", "delete": "deleted", "restore": "restored",
             "move": "moved"}
    parts = ["created the table"] if created else []
    parts += [f"{verbs[op]} {actions._plural(len(rows[op]), 'row')}" for op in verbs if op in rows]
    if other:
        parts.append(other[0][0].lower() + other[0][1:] if len(other) == 1 else f"made {len(other)} other changes")
    return f"{who} " + ", ".join(parts) if parts else who


async def table_history(db: AsyncSession, t: DataTable, *, before_seq: int | None = None, limit: int = 50
                        ) -> dict[str, Any]:
    """ACT table.history: the changes in sessions, newest first ("Today 14:02 · Dana edited 12
    rows"). A session is one batch, or one author's run of changes with no gap over 2 minutes."""
    lim = 2000
    rq = select(TableRowChange).where(TableRowChange.table_id == t.id)
    sq = select(TableSchemaChange).where(TableSchemaChange.table_id == t.id)
    if before_seq is not None:
        rq, sq = rq.where(TableRowChange.seq < before_seq), sq.where(TableSchemaChange.seq < before_seq)
    items = list((await db.execute(rq.order_by(TableRowChange.seq.desc()).limit(lim))).scalars()) + \
        list((await db.execute(sq.order_by(TableSchemaChange.seq.desc()).limit(lim))).scalars())
    items.sort(key=lambda c: c.seq, reverse=True)
    items = items[:lim]
    cols = await _col_defs(db, t)
    names = await _assistant_names(db, items)
    sessions: list[dict[str, Any]] = []
    cur: list[Any] = []

    def close() -> None:
        if not cur:
            return
        first, last = cur[-1], cur[0]
        who = _by_name(first.by, first.source, names)
        sessions.append({"id": f"{first.seq}-{last.seq}", "from_seq": first.seq, "to_seq": last.seq,
                         "start": core.iso(first.at), "end": core.iso(last.at), "who": who, "by": first.by or {},
                         "source": first.source, "batches": sorted({c.batch for c in cur if c.batch}),
                         "count": len(cur), "summary": _session_summary(who, cur),
                         "changes": [change_out(c, cols, names) for c in cur[:100]]})

    for ch in items:
        if cur:
            prev = cur[-1]
            same = (ch.batch and ch.batch == prev.batch) or (
                (ch.by or {}).get("id") == (prev.by or {}).get("id") and (ch.by or {}).get("kind") == (prev.by or {}).get("kind")
                and ch.source == prev.source and prev.at - ch.at <= SESSION_GAP)
            if not same:
                close()
                cur = []
                if len(sessions) >= limit:
                    break
        cur.append(ch)
    if len(sessions) < limit:
        close()
    more = bool(sessions) and len(items) >= lim or len(sessions) >= limit
    return {"sessions": sessions[:limit], "more": more,
            "next_before": sessions[-1]["from_seq"] if sessions and more else None,
            "oldest": core.iso(items[-1].at) if items else None}


async def row_history(db: AsyncSession, t: DataTable, row_id: str, *, col: str | None = None) -> dict[str, Any]:
    """ACT row.history: one row's changes, newest first, and each cell's earlier values."""
    rid = core.valid_uuid(row_id)
    if rid is None:
        raise ApiError(404, "tables.row_not_found")
    r = await db.get(TableRow, rid)
    if r is None or r.table_id != t.id:
        raise ApiError(404, "tables.row_not_found")
    changes = list((await db.execute(select(TableRowChange).where(
        TableRowChange.table_id == t.id, TableRowChange.row_id == rid).order_by(TableRowChange.seq))).scalars())
    retypes = [c for c in (await db.execute(select(TableSchemaChange).where(
        TableSchemaChange.table_id == t.id, TableSchemaChange.op == "retype"))).scalars()
        if rid in ((c.values or {}).get("after") or {})]
    cols = await _col_defs(db, t)
    names = await _assistant_names(db, changes + retypes)
    values: dict[str, list[dict[str, Any]]] = {}

    def push(k: str, v: Any, raw: Any, ch: Any) -> None:
        values.setdefault(k, []).append({"value": v, "raw": raw, "text": _cell_text(cols.get(k), v, raw),
                                         "seq": ch.seq, "at": core.iso(ch.at), "who": _by_name(ch.by, ch.source, names),
                                         "by": ch.by or {}})

    for ch in sorted(changes + retypes, key=lambda c: c.seq):
        if isinstance(ch, TableSchemaChange):
            v, raw = ch.values["after"][rid]
            push(ch.col_id, v, raw, ch)
            continue
        a = ch.after or {}
        if ch.op in ("add", "restore"):
            for k, v in (a.get("cells") or {}).items():
                push(k, v, None, ch)
            for k, v in (a.get("raw") or {}).items():
                push(k, None, v, ch)
        elif ch.op == "edit":
            keys = set((a.get("cells") or {}).keys()) | set((a.get("raw") or {}).keys())
            for k in keys:
                push(k, (a.get("cells") or {}).get(k), (a.get("raw") or {}).get(k), ch)
    for k in values:
        values[k].reverse()
    items = sorted(changes + retypes, key=lambda c: c.seq, reverse=True)
    out = {"row": rid, "deleted": r.deleted_at is not None,
           "changes": [change_out(c, cols, names) for c in items if not isinstance(c, TableSchemaChange)]
           + [change_out(c, cols, names) | {"kind": "column"} for c in items if isinstance(c, TableSchemaChange)],
           "values": values if col is None else {col: values.get(col, [])}}
    out["changes"].sort(key=lambda c: c["seq"], reverse=True)
    return out


# ---------------------------------------------------------------- restoring a row or a cell


async def restore_version(db: AsyncSession, user: User, t: DataTable, row_id: str, seq: int, *,
                          col: str | None = None, source: str = "restore") -> dict[str, Any]:
    """ACT row.restore_version: the row as it was after change `seq`, or one cell's value then."""
    rid = core.valid_uuid(row_id)
    r = await db.get(TableRow, rid) if rid else None
    if r is None or r.table_id != t.id:
        raise ApiError(404, "tables.row_not_found")
    target = await row_state_at(db, t, rid, int(seq))
    log = Log(db, t, user, source)
    cols = core.cols_map(await core.columns_of(db, t))
    if target is None:
        if col is not None:
            raise HTTPException(409, "The row did not exist at that point")
        res = await actions.delete_rows(db, user, t, [rid], log=log)
        return {"row": None, "undo": res["undo"]}
    if r.deleted_at is not None:
        await actions.restore_rows(db, user, t, [rid], log=log, notify=False)
    keys = [col] if col is not None else sorted(set(r.cells or {}) | set(r.raw or {}) | set(target["cells"])
                                                 | set(target.get("raw") or {}))
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for k in keys:
        if k not in cols:
            continue
        v, raw = (target.get("cells") or {}).get(k), (target.get("raw") or {}).get(k)
        if merge3.same((r.cells or {}).get(k), v) and merge3.same((r.raw or {}).get(k), raw):
            continue
        b, a = actions._apply(r, cols[k], v, raw)
        actions._merge_partials(before, b, first=True)
        actions._merge_partials(after, a, first=False)
    if after:
        r.updated_at, r.updated_by = datetime.now(UTC), {**core.by_of(user), "restore": int(seq)}
        await core.derive(db, t, r, cols)
        await db.flush()
        await log.row(r.id, "edit", before, after)
    v = await core.commit_row(db, t, r)
    label = "Value restored" if col else "Row restored"
    return {"row": core.row_out(r, {}, v), "undo": actions.revert_undo(t, log.batch, label) if log.count else None}


# ---------------------------------------------------------------- undo (revert a batch)


async def revert(db: AsyncSession, user: User, t: DataTable, batch: str) -> dict[str, Any]:
    """Reverse the changes of one batch, newest first. A cell or a field changed since by
    someone else stays as it is. The answer's undo reverts this revert (redo)."""
    rch = list((await db.execute(select(TableRowChange).where(TableRowChange.table_id == t.id,
                                                              TableRowChange.batch == batch))).scalars())
    sch = list((await db.execute(select(TableSchemaChange).where(TableSchemaChange.table_id == t.id,
                                                                 TableSchemaChange.batch == batch))).scalars())
    if not rch and not sch:
        raise HTTPException(404, "nothing to undo (the change is older than the history keeps)")
    items = sorted(rch + sch, key=lambda c: c.seq, reverse=True)
    log = Log(db, t, user, "undo")
    touched: set[str] = set()
    deleted: list[str] = []
    for ch in items:
        if isinstance(ch, TableRowChange):
            await _revert_row(db, user, t, ch, log, touched, deleted)
        else:
            await _revert_schema(db, user, t, ch, log)
    await db.flush()
    cols = core.cols_map(await core.columns_of(db, t))
    for rid in touched:
        r = await db.get(TableRow, rid)
        if r is not None:
            await core.derive(db, t, r, cols)
    await db.flush()
    core.stamp(t, user)
    await core.commit_table(db, t)
    await actions._rows_changed(db, t, [x for x in touched if x not in deleted], deleted=deleted)
    if any(isinstance(c, TableSchemaChange) for c in items):
        await core.touch(db, t, rows=True)
    return {"ok": True, "reverted": len(items), "batch": log.batch,
            "undo": actions.revert_undo(t, log.batch, "Redone") if log.count else None}


async def _revert_row(db: AsyncSession, user: User, t: DataTable, ch: TableRowChange, log: Log, touched: set[str],
                      deleted: list[str]) -> None:
    r = await db.get(TableRow, ch.row_id)
    now = datetime.now(UTC)
    if ch.op in ("add", "restore"):
        if r is not None and r.deleted_at is None:
            r.deleted_at, r.deleted_by = now, user.id
            await log.row(r.id, "delete", core.row_state(r), {"deleted": True})
            touched.add(r.id)
            deleted.append(r.id)
        return
    if ch.op == "delete":
        full = ch.before or {}
        if r is None:
            r = TableRow(id=ch.row_id, table_id=t.id, cells=full.get("cells") or {}, raw=full.get("raw") or {},
                         order=full.get("order") or "i", created_by=user.id, updated_by=core.by_of(user))
            db.add(r)
        elif r.deleted_at is None:
            return
        else:
            r.deleted_at, r.deleted_by = None, None
        await db.flush()
        await log.row(r.id, "restore", {"deleted": True}, core.row_state(r))
        touched.add(r.id)
        return
    if r is None or r.deleted_at is not None:
        return
    b, a = ch.before or {}, ch.after or {}
    if ch.op == "move":
        if r.order == a.get("order") and b.get("order"):
            await log.row(r.id, "move", {"order": r.order}, {"order": b["order"]})
            r.order = b["order"]
            touched.add(r.id)
        return
    cells, raw = dict(r.cells or {}), dict(r.raw or {})
    nb: dict[str, Any] = {}
    na: dict[str, Any] = {}
    # A cell is its value and its kept text together: both must be as the change left them.
    keys = set((a.get("cells") or {}).keys()) | set((a.get("raw") or {}).keys())
    for k in keys:
        if ("cells" in a and k in a["cells"] and not merge3.same(cells.get(k), a["cells"][k])) or \
                ("raw" in a and k in a["raw"] and not merge3.same(raw.get(k), a["raw"][k])):
            continue  # changed since by someone else
        for part, cur in (("cells", cells), ("raw", raw)):
            if k not in (a.get(part) or {}):
                continue
            old = (b.get(part) or {}).get(k)
            nb.setdefault(part, {})[k] = cur.get(k)
            na.setdefault(part, {})[k] = old
            if old is None or old == "":
                cur.pop(k, None)
            else:
                cur[k] = old
    if na:
        r.cells, r.raw = cells, raw
        r.updated_at, r.updated_by = now, core.by_of(user)
        await log.row(r.id, "edit", nb, na)
        touched.add(r.id)


async def _revert_schema(db: AsyncSession, user: User, t: DataTable, ch: TableSchemaChange, log: Log) -> None:
    b, a = ch.before or {}, ch.after or {}
    op = ch.op
    now = datetime.now(UTC)
    if op == "table":
        if not b:
            return  # the creation of the table: its undo is Trash
        changed = {k: v for k, v in b.items() if getattr(t, k) == a.get(k)}
        if "local_only" in changed and localonly.lowers(t, changed["local_only"]) and not localonly.can_lower(user, t):
            changed.pop("local_only")  # an editor's revert keeps Local models only on
        if changed:
            await log.schema("table", before={k: getattr(t, k) for k in changed}, after=changed)
            for k, v in changed.items():
                setattr(t, k, v)
        return
    if op == "primary":
        if t.primary_column == a.get("primary_column"):
            await log.schema("primary", before={"primary_column": t.primary_column}, after=b)
            t.primary_column = b.get("primary_column")
        return
    if op in ("view_save", "view_delete"):
        v = await db.get(TableView, (t.id, ch.view_id))
        if op == "view_delete" or not b:
            if op == "view_save" and not b:  # a new view: remove it
                if v is not None:
                    await log.schema("view_delete", view_id=v.view_id, before=core.view_def(v))
                    await db.delete(v)
                return
            if v is None:  # a deleted view: put it back
                v = TableView(table_id=t.id, view_id=ch.view_id, name=b["name"], spec=b.get("spec") or {},
                              is_default=bool(b.get("is_default")), order=b.get("order") or "i")
                db.add(v)
                await db.flush()
                await log.schema("view_save", view_id=v.view_id, before=None, after=core.view_def(v))
            return
        if v is None:
            return
        cur = core.view_def(v)
        await log.schema("view_save", view_id=v.view_id, before=cur, after=b)
        v.name, v.spec, v.is_default, v.order, v.updated_at = b["name"], b.get("spec") or {}, bool(b.get("is_default")), \
            b.get("order") or v.order, now
        return
    c = await db.get(TableColumn, (t.id, ch.col_id)) if ch.col_id else None
    if c is None:
        return
    if op == "add":
        if c.deleted_at is None:
            c.deleted_at = now
            await log.schema("delete", col_id=c.col_id, before=core.column_def(c) | {"deleted": False},
                             after={"deleted": True})
            if t.primary_column == c.col_id:
                rest = [x for x in await core.columns_of(db, t) if x.col_id != c.col_id]
                t.primary_column = actions._default_primary(rest)
    elif op == "delete":
        if c.deleted_at is not None:
            c.deleted_at = None
            await log.schema("restore", col_id=c.col_id, before={"deleted": True}, after=core.column_def(c))
    elif op == "restore":
        if c.deleted_at is None:
            c.deleted_at = now
            await log.schema("delete", col_id=c.col_id, before=core.column_def(c) | {"deleted": False},
                             after={"deleted": True})
    elif op == "rename":
        if c.name == a.get("name"):
            await log.schema("rename", col_id=c.col_id, before={"name": c.name}, after={"name": b.get("name")})
            c.name = b.get("name")
    elif op in ("config", "options"):
        cur = {"config": dict(c.config or {}), "required": bool(c.required)}
        await log.schema(op, col_id=c.col_id, before={k: cur[k] for k in b}, after=b)
        if "config" in b:
            c.config = b["config"]
        if "required" in b:
            c.required = bool(b["required"])
    elif op == "reorder":
        if c.order == a.get("order"):
            await log.schema("reorder", col_id=c.col_id, before={"order": c.order}, after={"order": b.get("order")})
            c.order = b.get("order")
    elif op in ("retype", "define"):
        cur_def = core.column_def(c)
        c.type, c.config = b.get("type", c.type), b.get("config", c.config)
        if "name" in b:
            c.name = b["name"]
        vals = ch.values or {}
        back: dict[str, dict[str, Any]] = {"before": {}, "after": {}}
        for rid, (v_after, raw_after) in (vals.get("after") or {}).items():
            r = await db.get(TableRow, rid)
            if r is None:
                continue
            if not (merge3.same((r.cells or {}).get(c.col_id), v_after)
                    and merge3.same((r.raw or {}).get(c.col_id), raw_after)):
                continue
            v_before, raw_before = (vals.get("before") or {}).get(rid, [None, None])
            back["before"][rid] = [v_after, raw_after]
            back["after"][rid] = [v_before, raw_before]
            cells, rawd = dict(r.cells or {}), dict(r.raw or {})
            cells.pop(c.col_id, None)
            rawd.pop(c.col_id, None)
            if v_before is not None:
                cells[c.col_id] = v_before
            if raw_before:
                rawd[c.col_id] = raw_before
            r.cells, r.raw = cells, rawd
        await log.schema(op, col_id=c.col_id, before=cur_def, after=core.column_def(c), values=back)


# ---------------------------------------------------------------- restore to a point in time


def _diff(target: State, cur: State) -> dict[str, Any]:
    t_live = {k for k, c in target.columns.items() if not c.get("deleted")}
    c_live = {k for k, c in cur.columns.items() if not c.get("deleted")}
    cols_back = sorted(t_live - c_live)
    cols_gone = sorted(c_live - t_live)
    cols_changed = sorted(k for k in t_live & c_live if any(
        not merge3.same(target.columns[k].get(f), cur.columns[k].get(f))
        for f in ("name", "type", "config", "required", "order")))
    rows_back = sorted(set(target.rows) - set(cur.rows))
    rows_gone = sorted(set(cur.rows) - set(target.rows))
    rows_changed = sorted(k for k in set(target.rows) & set(cur.rows) if not merge3.same(
        {f: target.rows[k].get(f) or ({} if f != "order" else None) for f in ("cells", "raw", "order")},
        {f: cur.rows[k].get(f) or ({} if f != "order" else None) for f in ("cells", "raw", "order")}))
    views_changed = sorted(k for k in set(target.views) | set(cur.views)
                           if not merge3.same(target.views.get(k), cur.views.get(k)))
    fields_changed = sorted(k for k in ("name", "description", "color", "primary_column")
                            if target.fields.get(k) != cur.fields.get(k))
    return {"columns_back": cols_back, "columns_gone": cols_gone, "columns_changed": cols_changed,
            "rows_back": rows_back, "rows_gone": rows_gone, "rows_changed": rows_changed,
            "views_changed": views_changed, "fields_changed": fields_changed}


def _sentence(when: str, d: dict[str, Any]) -> str:
    parts = []
    if d["rows_back"]:
        parts.append(f"{actions._plural(len(d['rows_back']), 'row')} come{'s' if len(d['rows_back']) == 1 else ''} back")
    if d["rows_changed"]:
        parts.append(f"{actions._plural(len(d['rows_changed']), 'row')} change{'s' if len(d['rows_changed']) == 1 else ''}")
    if d["rows_gone"]:
        n = len(d["rows_gone"])
        parts.append(f"{actions._plural(n, 'row')} {'is' if n == 1 else 'are'} removed")
    if d["columns_back"]:
        n = len(d["columns_back"])
        parts.append(f"{actions._plural(n, 'column')} come{'s' if n == 1 else ''} back")
    if d["columns_gone"]:
        n = len(d["columns_gone"])
        parts.append(f"{actions._plural(n, 'column')} {'is' if n == 1 else 'are'} removed")
    if d["columns_changed"]:
        n = len(d["columns_changed"])
        parts.append(f"{actions._plural(n, 'column')} change{'s' if n == 1 else ''}")
    if d["views_changed"] or d["fields_changed"]:
        parts.append("views and the name are put back" if d["fields_changed"] else "views are put back")
    return f"Going back to {when}: " + (", ".join(parts) if parts else "nothing changes")


async def restore_point(db: AsyncSession, user: User, t: DataTable, *, at: datetime | None = None,
                        seq: int | None = None, preview: bool = False, when_text: str | None = None) -> dict[str, Any]:
    """ACT table.restore_point: the whole table as it was at `at` (within 30 days) or after change
    `seq`. Shares and links are not touched."""
    now = datetime.now(UTC)
    if at is None and seq is None:
        raise HTTPException(422, "say when: at (a time) or seq (a change)")
    if at is not None:
        from mailr.calendar.tz import aware

        at = await aware(db, user, at)
        if at < now - timedelta(days=core.HISTORY_DAYS):
            raise HTTPException(422, "A table can go back at most 30 days")
    target = await state_at(db, t, at=at, seq=seq)
    if not target.columns:
        raise HTTPException(409, "The table did not exist at that moment")
    cur = await current_state(db, t)
    d = _diff(target, cur)
    when = when_text or (at.isoformat(timespec="minutes") if at else f"change {seq}")
    summary = _sentence(when, d)
    counts = {k: len(v) for k, v in d.items()}
    if preview:
        return {"preview": True, "summary": summary, "counts": counts}
    by = {**core.by_of(user), "kind": "restore", "user_kind": core.by_of(user).get("kind")}
    log = Log(db, t, user, "restore", by=by)
    # Columns first, so the rows' values fit them.
    for k in d["columns_back"] + d["columns_changed"]:
        want = target.columns[k]
        c = await db.get(TableColumn, (t.id, k))
        if c is None:
            c = TableColumn(table_id=t.id, col_id=k, name=want["name"], type=want["type"], config=want.get("config") or {},
                            required=bool(want.get("required")), width=want.get("width"), order=want.get("order") or "i")
            db.add(c)
            await db.flush()
            await log.schema("add", col_id=k, after=core.column_def(c))
            continue
        before = core.column_def(c)
        c.deleted_at = None
        c.name, c.type, c.config = want["name"], want["type"], want.get("config") or {}
        c.required, c.order = bool(want.get("required")), want.get("order") or c.order
        await log.schema("define", col_id=k, before=before, after=core.column_def(c))
    for k in d["columns_gone"]:
        c = await db.get(TableColumn, (t.id, k))
        if c is not None and c.deleted_at is None:
            c.deleted_at = now
            await log.schema("delete", col_id=k, before=core.column_def(c) | {"deleted": False}, after={"deleted": True})
    await db.flush()
    cols = core.cols_map(await core.columns_of(db, t))
    changes: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []
    for rid in d["rows_back"]:
        want = target.rows[rid]
        r = await db.get(TableRow, rid)
        if r is None:
            r = TableRow(id=rid, table_id=t.id, cells=want.get("cells") or {}, raw=want.get("raw") or {},
                         order=want.get("order") or "i", created_by=user.id, updated_by=by)
            db.add(r)
        else:
            r.deleted_at, r.deleted_by = None, None
            r.cells, r.raw, r.order = want.get("cells") or {}, want.get("raw") or {}, want.get("order") or r.order
        r.updated_by = by
        await core.derive(db, t, r, cols)
        changes.append((rid, "restore", {"deleted": True}, core.row_state(r)))
    for rid in d["rows_gone"]:
        r = await db.get(TableRow, rid)
        if r is not None and r.deleted_at is None:
            changes.append((rid, "delete", core.row_state(r), {"deleted": True}))
            r.deleted_at, r.deleted_by = now, user.id
            await core.derive(db, t, r, cols)
    for rid in d["rows_changed"]:
        want = target.rows[rid]
        r = await db.get(TableRow, rid)
        if r is None:
            continue
        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        for part in ("cells", "raw"):
            have, need = dict(getattr(r, part) or {}), dict(want.get(part) or {})
            for k in set(have) | set(need):
                if not merge3.same(have.get(k), need.get(k)):
                    before.setdefault(part, {})[k] = have.get(k)
                    after.setdefault(part, {})[k] = need.get(k)
        if (want.get("order") or r.order) != r.order:
            before["order"], after["order"] = r.order, want["order"]
            r.order = want["order"]
        r.cells, r.raw = dict(want.get("cells") or {}), dict(want.get("raw") or {})
        r.updated_at, r.updated_by = now, by
        await core.derive(db, t, r, cols)
        changes.append((rid, "edit", before, after))
    await db.flush()
    await log.rows(changes)
    for k in d["views_changed"]:
        want = target.views.get(k)
        v = await db.get(TableView, (t.id, k))
        if want is None:
            if v is not None:
                await log.schema("view_delete", view_id=k, before=core.view_def(v))
                await db.delete(v)
            continue
        before_v = core.view_def(v) if v is not None else None
        if v is None:
            v = TableView(table_id=t.id, view_id=k, name=want["name"], spec=want.get("spec") or {},
                          is_default=bool(want.get("is_default")), order=want.get("order") or "i")
            db.add(v)
        else:
            v.name, v.spec, v.is_default, v.order = want["name"], want.get("spec") or {}, bool(want.get("is_default")), \
                want.get("order") or v.order
        await db.flush()
        await log.schema("view_save", view_id=k, before=before_v, after=core.view_def(v))
    if d["fields_changed"]:
        fb = {k: getattr(t, k) for k in d["fields_changed"]}
        fa = {k: target.fields.get(k) for k in d["fields_changed"]}
        if "primary_column" in fa and fa["primary_column"] not in cols:
            fa.pop("primary_column")
            fb.pop("primary_column")
        if "name" in fa and not fa["name"]:
            fa.pop("name")
            fb.pop("name")
        for k, v in fa.items():
            setattr(t, k, v)
        if fa:
            await log.schema("table", before=fb, after=fa)
    await db.flush()
    core.stamp(t, user)
    await core.commit_table(db, t)
    await core.touch(db, t, rows=True)
    return {"ok": True, "summary": summary, "counts": counts, "batch": log.batch,
            "undo": actions.revert_undo(t, log.batch, "Table restored") if log.count else None}
