# ACT-SURFACES: api
"""Per-row history of tables: each row change
with its author and time, a row restored to a change, a cell's earlier values, deleted rows and
columns, the table's history in sessions, undo by batch, restore to a point in time against a
scripted change log across a snapshot boundary, and the nightly cleanup.

Covers: table.history, table.restore_point, row.history, row.restore_version, row.restore,
column.restore."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

# These tests never touch the mail store: database-only users (conftest.make_db_user).
pytestmark = pytest.mark.db_user

API = "/api/v1"


async def new_table(client, **kw):
    r = await client.post(f"{API}/tables", json=kw)
    assert r.status_code == 200, r.text
    return r.json()["table"]


def col(t, name):
    return next(c for c in t["columns"] if c["name"] == name)["id"]


async def rows(client, pid, **params):
    r = await client.get(f"{API}/tables/{pid}/rows", params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def do_undo(client, undo):
    r = await client.request(undo["method"], API + undo["path"], json=undo["body"])
    assert r.status_code == 200, r.text
    return r.json()


async def test_row_history_and_earlier_values(client):
    """row.history: each change with author and time, "Weight 80 → 82"; a cell's earlier values."""
    t = await new_table(client, name="Log", columns=[{"name": "Item", "type": "text"}, {"name": "Weight", "type": "number"}])
    item, w = col(t, "Item"), col(t, "Weight")
    row = (await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {item: "Squat", w: "80"}})).json()["rows"][0]
    await client.patch(f"{API}/tables/{t['id']}/rows/{row['id']}", json={"cells": {w: "82"}})
    await client.patch(f"{API}/tables/{t['id']}/rows/{row['id']}", json={"cells": {w: "85"}})
    h = (await client.get(f"{API}/tables/{t['id']}/rows/{row['id']}/changes")).json()
    assert [c["summary"] for c in h["changes"]] == ["Weight 82 → 85", "Weight 80 → 82", "Added"]
    assert h["changes"][0]["who"] == "Test User" and h["changes"][0]["at"]
    assert [v["text"] for v in h["values"][w]] == ["85", "82", "80"]
    one = (await client.get(f"{API}/tables/{t['id']}/rows/{row['id']}/changes", params={"col": w})).json()
    assert list(one["values"]) == [w]


async def test_row_restore_version_and_one_cell(client):
    """row.restore_version: the row as it was at a change; one cell back to an earlier value;
    each with an undo."""
    t = await new_table(client, name="Log", columns=[{"name": "Item", "type": "text"}, {"name": "Weight", "type": "number"}])
    item, w = col(t, "Item"), col(t, "Weight")
    row = (await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {item: "Squat", w: "80"}})).json()["rows"][0]
    await client.patch(f"{API}/tables/{t['id']}/rows/{row['id']}", json={"cells": {item: "Front squat", w: "70"}})
    h = (await client.get(f"{API}/tables/{t['id']}/rows/{row['id']}/changes")).json()
    added = h["changes"][-1]["seq"]
    r = await client.post(f"{API}/tables/{t['id']}/rows/{row['id']}/restore", json={"seq": added})
    assert r.status_code == 200, r.text
    assert r.json()["row"]["cells"] == {item: "Squat", w: "80"}
    await do_undo(client, r.json()["undo"])
    assert (await rows(client, t["id"]))["rows"][0]["cells"] == {item: "Front squat", w: "70"}
    r = await client.post(f"{API}/tables/{t['id']}/rows/{row['id']}/restore", json={"seq": added, "col": w})
    assert r.json()["row"]["cells"] == {item: "Front squat", w: "80"}
    h = (await client.get(f"{API}/tables/{t['id']}/rows/{row['id']}/changes")).json()
    assert h["changes"][0]["who"] == "Test User" and h["changes"][0]["source"] == "restore"
    assert h["changes"][0]["summary"] == "Weight 70 → 80"


async def test_table_history_sessions(client):
    """table.history: changes grouped by session ("Test User edited 2 rows"), each expandable."""
    t = await new_table(client, name="H", columns=[{"name": "Item", "type": "text"}])
    item = col(t, "Item")
    a = (await client.post(f"{API}/tables/{t['id']}/rows", json={"rows": [{"cells": {item: "a"}}, {"cells": {item: "b"}}]}
                           )).json()["rows"]
    for r in a:
        await client.patch(f"{API}/tables/{t['id']}/rows/{r['id']}", json={"cells": {item: r["cells"][item] + "!"}})
    h = (await client.get(f"{API}/tables/{t['id']}/changes")).json()
    s = h["sessions"]
    # One author's run of changes with no gap over 2 minutes is one session.
    assert s[0]["summary"] == "Test User created the table, added 2 rows, edited 2 rows"
    assert [c["op"] for c in s[0]["changes"]][:2] == ["edit", "edit"]



async def test_restore_point_across_snapshot_boundary(client, user):
    """table.restore_point: the state at a moment is the newest snapshot at or before it, then
    the changes after it up to that moment, in order. The replay equals the state recorded at
    each moment, before and after the nightly snapshot; the restore is one undo and itself in
    History."""
    from mailr.db import sessionmaker
    from mailr.tables import history
    from mailr.tables.models import DataTable, TableRowChange, TableSchemaChange, TableSnapshot

    t = await new_table(client, name="Inventory", columns=[{"name": "Item", "type": "text"},
                                                           {"name": "Qty", "type": "number"}])
    item, qty = col(t, "Item"), col(t, "Qty")
    marks: list[tuple[datetime, dict]] = []

    async def mark():
        async with sessionmaker()() as db:
            tb = (await db.execute(select(DataTable).where(DataTable.pid == t["id"]))).scalar_one()
            s = await history.current_state(db, tb)
            marks.append((datetime.now(UTC), {"columns": {k: (c["name"], c["type"], c["deleted"])
                                                          for k, c in s.columns.items()},
                                              "rows": {k: (v["cells"], v["raw"]) for k, v in s.rows.items()},
                                              "name": s.fields["name"]}))

    # A scripted change log: adds, edits, a delete, a column added and renamed, a retype.
    made = (await client.post(f"{API}/tables/{t['id']}/rows", json={"rows": [
        {"cells": {item: "Bolts", qty: "100"}}, {"cells": {item: "Nuts", qty: "250"}}, {"cells": {item: "Washers"}}]}
    )).json()["rows"]
    await mark()
    await client.patch(f"{API}/tables/{t['id']}/rows/{made[0]['id']}", json={"cells": {qty: "90"}})
    await client.delete(f"{API}/tables/{t['id']}/rows/{made[2]['id']}")
    loc = (await client.post(f"{API}/tables/{t['id']}/columns", json={"name": "Where", "type": "text"})).json()["column"]["id"]
    await client.patch(f"{API}/tables/{t['id']}/rows/{made[1]['id']}", json={"cells": {loc: "Shed"}})
    await mark()
    # The nightly snapshot.
    async with sessionmaker()() as db:
        tb = (await db.execute(select(DataTable).where(DataTable.pid == t["id"]))).scalar_one()
        await history.take_snapshot(db, tb)
        await db.commit()
    await client.patch(f"{API}/tables/{t['id']}/columns/{loc}", json={"name": "Location"})
    await client.patch(f"{API}/tables/{t['id']}/columns/{qty}", json={"type": "text"})
    await client.patch(f"{API}/tables/{t['id']}", json={"name": "Stock"})
    await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {item: "Screws"}})
    await mark()
    await client.delete(f"{API}/tables/{t['id']}/columns/{loc}")
    await client.post(f"{API}/tables/{t['id']}/rows/restore", json={"rows": [made[2]["id"]]})
    await mark()

    # Space the marks apart: every change made before a mark happened at least a second earlier.
    async with sessionmaker()() as db:
        tb = (await db.execute(select(DataTable).where(DataTable.pid == t["id"]))).scalar_one()
        base = datetime.now(UTC) - timedelta(hours=10)
        bounds = [m[0] for m in marks]
        for model in (TableRowChange, TableSchemaChange):
            for ch in (await db.execute(select(model).where(model.table_id == tb.id))).scalars():
                k = sum(1 for b in bounds if ch.at > b)
                ch.at = base + timedelta(hours=k * 2, seconds=ch.seq)
        snap = (await db.execute(select(TableSnapshot).where(TableSnapshot.table_id == tb.id))).scalar_one()
        snap.taken_at = base + timedelta(hours=3)
        await db.commit()
        moments = [base + timedelta(hours=k * 2 + 1) for k in range(len(marks))]
        for when, (_, want) in zip(moments, marks, strict=True):
            s = await history.state_at(db, tb, at=when)
            got = {"columns": {k: (c["name"], c["type"], c.get("deleted", False)) for k, c in s.columns.items()},
                   "rows": {k: (v.get("cells") or {}, v.get("raw") or {}) for k, v in s.rows.items()},
                   "name": s.fields["name"]}
            assert got == want, when

    # Restore to the second mark (before the snapshot): preview, then the real thing.
    at = moments[1].isoformat()
    r = await client.post(f"{API}/tables/{t['id']}/restore-point", json={"at": at, "preview": True})
    assert r.status_code == 200, r.text
    pv = r.json()
    assert pv["counts"]["rows_back"] == 0 and pv["counts"]["rows_gone"] == 2 and pv["counts"]["columns_back"] == 1
    assert pv["summary"].startswith("Going back to") and "1 column comes back" in pv["summary"]
    r = await client.post(f"{API}/tables/{t['id']}/restore-point", json={"at": at})
    assert r.status_code == 200, r.text
    after = await rows(client, t["id"])
    assert sorted(x["cells"][item] for x in after["rows"]) == ["Bolts", "Nuts"]
    t2 = (await client.get(f"{API}/tables/{t['id']}")).json()
    assert t2["name"] == "Inventory" and [c["name"] for c in t2["columns"]] == ["Item", "Qty", "Where"]
    assert next(c for c in t2["columns"] if c["id"] == qty)["type"] == "number"
    assert next(x for x in after["rows"] if x["cells"][item] == "Bolts")["cells"][qty] == "90"
    h = (await client.get(f"{API}/tables/{t['id']}/changes")).json()
    assert h["sessions"][0]["who"] == "Restore"
    # One undo takes it all back.
    await do_undo(client, r.json()["undo"])
    back = await rows(client, t["id"])
    assert sorted(x["cells"][item] for x in back["rows"]) == ["Bolts", "Nuts", "Screws", "Washers"]
    assert (await client.get(f"{API}/tables/{t['id']}")).json()["name"] == "Stock"


async def test_restore_point_limits(client):
    t = await new_table(client, name="X")
    r = await client.post(f"{API}/tables/{t['id']}/restore-point",
                          json={"at": (datetime.now(UTC) - timedelta(days=31)).isoformat()})
    assert r.status_code == 422
    r = await client.post(f"{API}/tables/{t['id']}/restore-point", json={})
    assert r.status_code == 422


async def test_revert_is_its_own_redo(client):
    """The undo of an undo is a redo; a cell changed since is left alone."""
    t = await new_table(client, name="R", columns=[{"name": "Item", "type": "text"}, {"name": "N", "type": "number"}])
    item, n = col(t, "Item"), col(t, "N")
    row = (await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {item: "a", n: "1"}})).json()["rows"][0]
    r = await client.patch(f"{API}/tables/{t['id']}/rows/{row['id']}", json={"cells": {item: "b", n: "2"}})
    undo = await do_undo(client, r.json()["undo"])
    assert (await rows(client, t["id"]))["rows"][0]["cells"] == {item: "a", n: "1"}
    await do_undo(client, undo["undo"])
    assert (await rows(client, t["id"]))["rows"][0]["cells"] == {item: "b", n: "2"}
    # Someone changes one of the cells; the undo of the edit keeps their value.
    r2 = await client.patch(f"{API}/tables/{t['id']}/rows/{row['id']}", json={"cells": {item: "c", n: "3"}})
    await client.patch(f"{API}/tables/{t['id']}/rows/{row['id']}", json={"cells": {n: "9"}})
    await do_undo(client, r2.json()["undo"])
    assert (await rows(client, t["id"]))["rows"][0]["cells"] == {item: "b", n: "9"}
    r = await client.post(f"{API}/tables/{t['id']}/revert", json={"batch": "00000000-0000-0000-0000-000000000000"})
    assert r.status_code == 404


async def test_nightly_snapshots_and_cleanup(client):
    """The nightly job snapshots changed tables only; after 30 days the old changes, snapshots,
    deleted rows and columns and tables in Trash go; the newest snapshot stays."""
    from mailr.db import sessionmaker
    from mailr.tables import history
    from mailr.tables.models import DataTable, TableColumn, TableRow, TableRowChange, TableSnapshot

    t = await new_table(client, name="N", columns=[{"name": "Item", "type": "text"}, {"name": "Old", "type": "text"}])
    item, old = col(t, "Item"), col(t, "Old")
    a = (await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {item: "a", old: "x"}})).json()["rows"][0]
    b = (await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {item: "b"}})).json()["rows"][0]
    await client.delete(f"{API}/tables/{t['id']}/rows/{b['id']}")
    await client.delete(f"{API}/tables/{t['id']}/columns/{old}")
    gone = await new_table(client, name="Gone")
    await client.delete(f"{API}/tables/{gone['id']}")
    now = datetime.now(UTC)
    async with sessionmaker()() as db:
        out = await history.nightly(db, now - timedelta(days=40))
        assert out["snapshots"] >= 2
        out = await history.nightly(db, now - timedelta(days=40))
        tb = (await db.execute(select(DataTable).where(DataTable.pid == t["id"]))).scalar_one()
        assert (await db.execute(select(TableSnapshot).where(TableSnapshot.table_id == tb.id))).scalars().all().__len__() == 1
        # Everything happened 35 days ago.
        old_at = now - timedelta(days=35)
        await db.execute(update(TableRowChange).where(TableRowChange.table_id == tb.id).values(at=old_at))
        await db.execute(update(TableRow).where(TableRow.id == b["id"]).values(deleted_at=old_at))
        await db.execute(update(TableColumn).where(TableColumn.table_id == tb.id, TableColumn.col_id == old)
                         .values(deleted_at=old_at))
        await db.execute(update(DataTable).where(DataTable.pid == gone["id"]).values(deleted_at=old_at))
        await db.commit()
        out = await history.nightly(db, now)
        await db.commit()
        assert out["purged"] >= 1 and out["rows"] >= 1 and out["columns"] >= 1 and out["changes"] >= 1
        assert await db.get(TableRow, b["id"]) is None
        ra = await db.get(TableRow, a["id"])
        await db.refresh(ra)
        assert old not in ra.cells
        assert (await db.execute(select(DataTable).where(DataTable.pid == gone["id"]))).scalar_one_or_none() is None
        assert len((await db.execute(select(TableSnapshot).where(TableSnapshot.table_id == tb.id))).scalars().all()) >= 1


async def test_deleted_columns_values_kept_in_history(client):
    t = await new_table(client, name="C", columns=[{"name": "Item", "type": "text"}, {"name": "Weight", "type": "number"}])
    w = col(t, "Weight")
    await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {col(t, 'Item'): "a", w: "5"}})
    await client.delete(f"{API}/tables/{t['id']}/columns/{w}")
    h = (await client.get(f"{API}/tables/{t['id']}/changes")).json()
    ch = h["sessions"][0]["changes"][0]
    assert ch["summary"] == "Deleted column Weight"
    assert ch["kind"] == "column" and ch["op"] == "delete"
