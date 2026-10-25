"""What the assistant's and agents' table writes run, now or after the user approves them
(core/permissions executors; agent/table_tools.py builds the payloads).

Each payload names the table by its public id and one op with its arguments, already checked
and typed by the tool. The executor finds the table again through tables/access.py (the user's
access may have changed while the card waited) and calls the same action as the web.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import live, permissions
from mailr.models import User
from mailr.tables import access, actions, history

ROW_OPS = ("add", "edit", "duplicate", "move", "restore_rows", "restore_version", "bulk_edit")
DELETE_OPS = ("delete",)
SCHEMA_OPS = ("create", "rename", "add_column", "rename_column", "retype_column", "column_options",
              "column_config", "reorder_column", "delete_column", "restore_column", "set_primary", "save_view",
              "delete_view", "restore_point")


@contextmanager
def _as(payload: dict[str, Any]):
    """Writes carry the agent that asked (history and live events say "Ambrose")."""
    by = payload.get("_by")
    tok = live.by_var.set(by) if by else None
    try:
        yield
    finally:
        if tok is not None:
            live.by_var.reset(tok)


def _source(payload: dict[str, Any]) -> str:
    by = str(payload.get("_by") or "")
    return "assistant" if by == "assistant" else "agent" if by.startswith("agent:") else "edit"


async def _table(db: AsyncSession, user: User, payload: dict[str, Any], need: str = "edit"):
    return await access.get(db, user, payload["table"], need=need, trashed=False)


@permissions.executor("tables.rows")
async def run_rows(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    op = payload.get("op")
    if op not in ROW_OPS:
        raise HTTPException(422, f"unknown op {op!r}")
    src = _source(payload)
    with _as(payload):
        t = await _table(db, user, payload)
        if payload.get("options"):
            await _save_options(db, user, t, payload["options"], src)
        if op == "add":
            res = await actions.add_rows(db, user, t, payload["rows"], source=src, defaults=False)
            out = {"added": res["added"], "row_ids": [r["id"] for r in res["rows"]]}
        elif op == "edit":
            n = 0
            log = None
            for it in payload["rows"]:
                res = await actions.edit_row(db, user, t, it["id"], it["cells"], it.get("base_version"), source=src,
                                             log=log)
                if res.get("undo"):
                    n += 1
                    log = log or actions.Log(db, t, user, src, batch=res["undo"]["body"]["batch"])
            out = {"edited": n, "row_ids": [it["id"] for it in payload["rows"]]}
        elif op == "bulk_edit":
            res = await actions.bulk(db, user, t, {"op": "edit", "rows": payload["rows"], "col": payload["col"],
                                                   "value": payload["value"]}, source=src)
            out = {"edited": res["edited"]}
        elif op == "duplicate":
            ids = []
            for rid in payload["rows"]:
                res = await actions.duplicate_row(db, user, t, rid, source=src)
                ids.append(res["row"]["id"])
            out = {"added": len(ids), "row_ids": ids}
        elif op == "move":
            res = await actions.move_row(db, user, t, payload["row"], payload["where"])
            out = {"moved": bool(res.get("row"))}
        elif op == "restore_rows":
            res = await actions.restore_rows(db, user, t, payload["rows"], source=src)
            out = {"restored": res["restored"]}
        else:  # restore_version
            res = await history.restore_version(db, user, t, payload["row"], int(payload["seq"]), col=payload.get("col"))
            out = {"restored": True, "row_id": payload["row"]}
    return {"ok": True, "table": t.pid, **out}


async def _save_options(db: AsyncSession, user: User, t, options: list[dict[str, Any]], src: str) -> None:
    """New select options the write names (an option name the column did not have)."""
    from mailr.tables.core import Log, commit_table
    from mailr.tables.models import TableColumn

    log = Log(db, t, user, src)
    for o in options:
        c = await db.get(TableColumn, (t.id, o["col"]))
        if c is None:
            continue
        have = {x["id"] for x in (c.config or {}).get("options") or []}
        new = [x for x in o["options"] if x["id"] not in have]
        if new:
            before = dict(c.config or {})
            c.config = {**before, "options": list(before.get("options") or []) + new}
            await log.schema("options", col_id=c.col_id, before={"config": before}, after={"config": c.config})
    await db.flush()
    await commit_table(db, t)


@permissions.executor("tables.rows_delete")
async def run_delete(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    with _as(payload):
        t = await _table(db, user, payload)
        res = await actions.delete_rows(db, user, t, payload["rows"], source=_source(payload))
    return {"ok": True, "table": t.pid, "deleted": res["deleted"], "row_ids": res["ids"]}


@permissions.executor("tables.schema")
async def run_schema(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    op = payload.get("op")
    if op not in SCHEMA_OPS:
        raise HTTPException(422, f"unknown op {op!r}")
    src = _source(payload)
    with _as(payload):
        if op == "create":
            res = await actions.create_table(db, user, name=payload["name"], description=payload.get("description") or "",
                                             columns=payload["columns"], local_only=bool(payload.get("local_only")),
                                             source=src)
            t = res["table"]
            if payload.get("rows_named"):
                ids = {c["name"]: c["id"] for c in t["columns"]}
                tb = await access.get(db, user, t["id"], need="edit")
                made = await actions.add_rows(db, user, tb, [{"cells": {ids[k]: v for k, v in r["cells"].items() if k in ids}}
                                                             for r in payload["rows_named"]], source=src, defaults=False)
                res["rows"] = made["rows"]
            return {"ok": True, "table": t["id"], "name": t["name"],
                    "columns": [{"id": c["id"], "name": c["name"], "type": c["type"]} for c in t["columns"]],
                    "rows_added": len(res.get("rows") or [])}
        t = await _table(db, user, payload)
        out: dict[str, Any] = {}
        if op == "rename":
            await actions.patch_table(db, user, t, {k: payload[k] for k in ("name", "description") if k in payload}, None)
        elif op == "set_primary":
            await actions.patch_table(db, user, t, {"primary_column": payload["col"]}, None)
        elif op == "add_column":
            res = await actions.add_column(db, user, t, payload["column"], before=payload.get("before"),
                                           after=payload.get("after"), source=src)
            out["column"] = {"id": res["column"]["id"], "name": res["column"]["name"], "type": res["column"]["type"]}
        elif op in ("rename_column", "retype_column", "column_options", "column_config", "reorder_column"):
            res = await actions.patch_column(db, user, t, payload["col"], payload["change"], source=src)
            if "converted" in res:
                out.update(converted=res["converted"], kept_as_text=res["kept_as_text"])
        elif op == "delete_column":
            res = await actions.delete_column(db, user, t, payload["col"], source=src)
            out["values"] = res["values"]
        elif op == "restore_column":
            await actions.restore_column(db, user, t, payload["col"], source=src)
        elif op == "save_view":
            res = await actions.save_view(db, user, t, name=payload["name"], spec=payload.get("spec"),
                                          view_id=payload.get("view_id"), source=src)
            out["view"] = res["view"]["id"]
        elif op == "delete_view":
            await actions.delete_view(db, user, t, payload["view"], source=src)
        else:  # restore_point
            at = datetime.fromisoformat(payload["at"]) if payload.get("at") else None
            res = await history.restore_point(db, user, t, at=at, seq=payload.get("seq"))
            out["summary"] = res["summary"]
    return {"ok": True, "table": t.pid, "op": op, **out}


@permissions.executor("notes.delete:table")
async def run_trash(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    with _as(payload):
        t = await access.get(db, user, payload["table"], need="delete")
        await actions.trash(db, user, t)
    return {"ok": True, "table": t.pid, "trashed": True}


@permissions.executor("notes.edit:table")
async def run_edit(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    """A table's colour, pin, labels, archive, links, and its way back from Trash."""
    op = payload.get("op")
    with _as(payload):
        if op == "restore":
            t = await access.get(db, user, payload["table"], need="delete")
            await actions.restore(db, user, t)
            return {"ok": True, "table": t.pid, "restored": True}
        t = await access.get(db, user, payload["table"], need="view" if op == "state" else "edit", trashed=False)
        if op == "color":
            await actions.patch_table(db, user, t, {"color": payload.get("color")}, None)
        elif op == "state":
            res = await actions.set_state(db, user, t, payload["state"])
            return {"ok": True, "table": t.pid, "state": res["state"]}
        elif op == "link":
            res = await actions.link(db, user, t, payload["target_kind"], payload["target_ref"])
            return {"ok": True, "table": t.pid, "link": res["link"]["id"]}
        else:
            raise HTTPException(422, f"unknown op {op!r}")
    return {"ok": True, "table": t.pid}
