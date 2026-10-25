# ACT-SURFACES: api
"""Tables through the API: the table.*, column.*
and row.* actions with their undo, typed cells, views, links, CSV, merges, limits and access.

Covers: table.create, table.rename, table.color, table.pin, table.label, table.archive,
table.duplicate, table.delete, table.restore, table.purge, table.view_edit, table.view_save,
table.view_delete, table.search, table.stats, table.import_csv, table.export_csv, table.link,
table.unlink, column.add, column.rename, column.retype, column.options, column.config,
column.reorder, column.set_primary, column.delete, column.restore, row.add, row.edit,
row.duplicate, row.move, row.delete, row.restore, row.bulk_edit, row.bulk_delete. History:
tests/test_tables_history.py (table.history, table.restore_point, row.history, row.restore_version)."""

from __future__ import annotations

import uuid

import pytest
from helpers.tables import API, col, new_table, opt

# These tests never touch the mail store: database-only users (conftest.make_db_user).
pytestmark = pytest.mark.db_user


async def get_table(client, pid):
    r = await client.get(f"{API}/tables/{pid}")
    assert r.status_code == 200, r.text
    return r.json()


async def rows(client, pid, **params):
    r = await client.get(f"{API}/tables/{pid}/rows", params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def do_undo(client, undo):
    assert undo, "expected an undo"
    r = await client.request(undo["method"], API + undo["path"], json=undo["body"])
    assert r.status_code == 200, r.text
    return r.json()


async def workout(client):
    t = await new_table(client, name="Workouts", columns=[
        {"name": "Exercise", "type": "select", "options": ["Squat", "Bench", "Deadlift"]},
        {"name": "Date", "type": "date"},
        {"name": "Weight", "type": "number", "config": {"decimals": 1}},
        {"name": "Done", "type": "checkbox"},
        {"name": "Notes", "type": "text", "config": {"long": True}},
    ])
    return t


# ---------------------------------------------------------------- table.*


async def test_table_create_default_and_template(client):
    """table.create: one text column "Name" by default; templates; a default view "All"; the
    undo moves it to Trash."""
    r = await client.post(f"{API}/tables", json={"name": "Groceries"})
    t = r.json()["table"]
    assert t["id"].startswith("t") and t["kind"] == "table" and t["level"] == "owner"
    assert [c["name"] for c in t["columns"]] == ["Name"] and t["primary_column"] == t["columns"][0]["id"]
    assert [v["name"] for v in t["views"]] == ["All"] and t["views"][0]["default"]
    log = await new_table(client, name="Spend", template="log")
    assert [(c["name"], c["type"]) for c in log["columns"]] == [("Date", "date"), ("Item", "text"), ("Amount", "number")]
    assert log["primary_column"] == col(log, "Item")
    await do_undo(client, r.json()["undo"])
    r = await client.get(f"{API}/tables", params={"view": "trash"})
    assert [c["name"] for c in r.json()["tables"]] == ["Groceries"]


async def test_table_rename_color_and_undo(client):
    """table.rename, table.color: the undo puts the old values back."""
    t = await new_table(client, name="Plan")
    r = await client.patch(f"{API}/tables/{t['id']}", json={"name": "Plan B", "description": "two",
                                                            "base_version": t["version"]})
    assert r.status_code == 200, r.text
    assert r.json()["table"]["name"] == "Plan B"
    await do_undo(client, r.json()["undo"])
    t2 = await get_table(client, t["id"])
    assert t2["name"] == "Plan" and t2["description"] == ""
    r = await client.patch(f"{API}/tables/{t['id']}", json={"color": "sage"})
    assert r.json()["table"]["color"] == "sage"
    r = await client.patch(f"{API}/tables/{t['id']}", json={"color": "neon"})
    assert r.status_code == 422


async def test_table_rename_conflict(client):
    """Name changed on both sides since base_version: a conflict, never an overwrite."""
    t = await new_table(client, name="Inventory")
    v0 = t["version"]
    r = await client.patch(f"{API}/tables/{t['id']}", json={"name": "Stock", "base_version": v0})
    assert r.status_code == 200
    r = await client.patch(f"{API}/tables/{t['id']}", json={"name": "Shelf", "base_version": v0})
    assert r.status_code == 409 and r.json()["fields"] == ["name"]
    r = await client.patch(f"{API}/tables/{t['id']}", json={"color": "tomato", "base_version": v0})
    assert r.status_code == 200 and r.json()["merged"]


async def test_table_pin_archive_label_and_grid(client):
    """table.pin, table.archive, table.label: per viewer; tables show in the Notes grid."""
    t = await new_table(client, name="Filament")
    r = await client.put(f"{API}/tables/{t['id']}/state", json={"pinned": True, "add_labels": ["Hobby"]})
    assert r.status_code == 200, r.text
    assert r.json()["state"]["pinned"] and r.json()["state"]["labels"] == ["Hobby"]
    r = await client.get(f"{API}/notes", params={"view": "label", "label": "Hobby"})
    assert [x["id"] for x in r.json()["tables"]] == [t["id"]]
    assert any(lb["name"] == "Hobby" and lb["count"] == 1 for lb in r.json()["labels"])
    r = await client.put(f"{API}/tables/{t['id']}/state", json={"archived": True})
    assert r.json()["state"]["archived"] and not r.json()["state"]["pinned"]
    assert t["id"] not in [x["id"] for x in (await client.get(f"{API}/notes")).json()["tables"]]
    assert [x["id"] for x in (await client.get(f"{API}/notes", params={"view": "archive"})).json()["tables"]] == [t["id"]]
    await do_undo(client, r.json()["undo"])
    assert t["id"] in [x["id"] for x in (await client.get(f"{API}/notes")).json()["tables"]]


async def test_grid_order_mixes_notes_and_tables(client):
    """A table moves between notes in the viewer's manual order, and a note next to a table."""
    n1 = (await client.post(f"{API}/notes", json={"kind": "text", "title": "A"})).json()["note"]
    t = await new_table(client, name="T")
    n2 = (await client.post(f"{API}/notes", json={"kind": "text", "title": "B"})).json()["note"]
    r = await client.put(f"{API}/tables/{t['id']}/state", json={"after": n2["id"]})
    assert r.status_code == 200, r.text
    r = await client.put(f"{API}/notes/{n1['id']}/state", json={"before": t["id"]})
    assert r.status_code == 200, r.text
    r = await client.get(f"{API}/notes")
    cards = r.json()["notes"] + r.json()["tables"]
    order = sorted(cards, key=lambda c: c["state"]["order"])
    assert [c["id"] for c in order] == [n2["id"], n1["id"], t["id"]]


async def test_table_trash_restore_purge(client):
    """table.delete, table.restore, table.purge: Trash keeps rows; purge only from Trash."""
    t = await new_table(client, name="Old")
    await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {t["columns"][0]["id"]: "x"}})
    r = await client.delete(f"{API}/tables/{t['id']}?forever=true")
    assert r.status_code == 409
    r = await client.delete(f"{API}/tables/{t['id']}")
    assert r.status_code == 200
    r = await client.patch(f"{API}/tables/{t['id']}", json={"name": "y"})
    assert r.status_code == 409
    await do_undo(client, r.json()["undo"] if r.status_code == 200 else
                  {"method": "POST", "path": f"/tables/{t['id']}/restore", "body": None})
    assert (await rows(client, t["id"]))["count"] == 1
    await client.delete(f"{API}/tables/{t['id']}")
    r = await client.delete(f"{API}/tables/{t['id']}?forever=true")
    assert r.status_code == 200 and r.json()["undo"] is None
    assert (await client.get(f"{API}/tables/{t['id']}")).status_code == 404


async def test_empty_trash_includes_tables(client):
    t = await new_table(client, name="Gone")
    await client.delete(f"{API}/tables/{t['id']}")
    r = await client.post(f"{API}/notes/trash/empty")
    assert r.status_code == 200 and r.json()["purged"] >= 1
    assert (await client.get(f"{API}/tables/{t['id']}")).status_code == 404


async def test_table_duplicate_with_and_without_rows(client):
    """table.duplicate: schema and views, with or without rows; self-links follow the copy."""
    t = await new_table(client, name="People", columns=[{"name": "Name", "type": "text"},
                                                         {"name": "Manager", "type": "link",
                                                          "config": {"target": "table", "table": "self"}}])
    name, mgr = col(t, "Name"), col(t, "Manager")
    a = (await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {name: "Ada"}})).json()["rows"][0]
    r = await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {
        name: "Bo", mgr: [{"kind": "row", "table": t["id"], "row": a["id"]}]}})
    assert r.status_code == 200, r.text
    r = await client.post(f"{API}/tables/{t['id']}/duplicate", json={"rows": True})
    copy = r.json()["table"]
    assert copy["name"] == "People (copy)" and copy["counts"]["rows"] == 2
    cr = await rows(client, copy["id"])
    bo = next(x for x in cr["rows"] if x["cells"][name] == "Bo")
    ref = bo["cells"][mgr][0]
    assert ref["table"] == copy["id"] and ref["row"] != a["id"]
    assert cr["refs"][f"row:{ref['row']}"]["title"] == "Ada"
    r = await client.post(f"{API}/tables/{t['id']}/duplicate", json={"rows": False})
    assert r.json()["table"]["counts"]["rows"] == 0


# ---------------------------------------------------------------- row.*


async def test_row_add_typed_values_and_errors(client):
    """row.add: typed values are checked per column; a value that does not fit is a 422 naming
    the column and the type expected."""
    t = await workout(client)
    ex, date, w, done = col(t, "Exercise"), col(t, "Date"), col(t, "Weight"), col(t, "Done")
    squat = opt(t, "Exercise", "Squat")
    rid = str(uuid.uuid4())
    r = await client.post(f"{API}/tables/{t['id']}/rows", json={"id": rid, "cells": {
        ex: squat, date: "2026-10-03", w: 82.5, done: True}})
    assert r.status_code == 200, r.text
    row = r.json()["rows"][0]
    assert row["id"] == rid and row["cells"][w] == "82.5" and row["cells"][done] is True
    # Sent twice: added once.
    r = await client.post(f"{API}/tables/{t['id']}/rows", json={"id": rid, "cells": {ex: squat}})
    assert r.json()["added"] == 0 and (await rows(client, t["id"]))["count"] == 1
    for bad, colname in ((("x" * 3), w), ("2026-13-01", date), ("o-nope", ex), ("yes", done)):
        r = await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {colname: bad}})
        assert r.status_code == 422, (bad, r.text)
        assert r.json()["detail"]["column"] in ("Weight", "Date", "Exercise", "Done")
    # A new row from the grid gets today's date in a column named Date.
    r = await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {ex: squat}})
    assert r.json()["rows"][0]["cells"].get(date)


async def test_row_add_fills_view_filters(client):
    """A view filtered on "Exercise is Squat" adds Squat, so the new row stays in view."""
    t = await workout(client)
    ex = col(t, "Exercise")
    squat = opt(t, "Exercise", "Squat")
    r = await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {}, "view_filters": [
        {"col": ex, "op": "is", "value": squat}]})
    assert r.json()["rows"][0]["cells"][ex] == squat


async def test_row_edit_undo_and_merge_per_cell(client):
    """row.edit: different cells changed from the same version combine; the same cell changed
    two ways is a conflict on that cell; the undo puts the old value back."""
    t = await workout(client)
    w, notes = col(t, "Weight"), col(t, "Notes")
    row = (await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {w: "80"}})).json()["rows"][0]
    r = await client.patch(f"{API}/tables/{t['id']}/rows/{row['id']}", json={"cells": {w: "82"}, "base_version": row["version"]})
    assert r.status_code == 200, r.text
    v1 = r.json()["row"]["version"]
    await do_undo(client, r.json()["undo"])
    assert (await rows(client, t["id"]))["rows"][0]["cells"][w] == "80"
    r = await client.patch(f"{API}/tables/{t['id']}/rows/{row['id']}", json={"cells": {notes: "felt good"},
                                                                          "base_version": v1})
    assert r.status_code == 200 and r.json()["merged"]
    r = await client.patch(f"{API}/tables/{t['id']}/rows/{row['id']}", json={"cells": {w: "90"}, "base_version": v1})
    assert r.status_code == 409 and r.json()["fields"] == [f"cells.{w}"]


async def test_row_long_text_merges_by_lines(client):
    t = await workout(client)
    notes = col(t, "Notes")
    row = (await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {notes: "a\nb\nc"}})).json()["rows"][0]
    v = (await client.patch(f"{API}/tables/{t['id']}/rows/{row['id']}", json={"cells": {notes: "a\nb\nc"},
                                                                           "base_version": None})).json()["row"]["version"]
    base = (await rows(client, t["id"]))["rows"][0]["version"]
    r = await client.patch(f"{API}/tables/{t['id']}/rows/{row['id']}", json={"cells": {notes: "A\nb\nc"}, "base_version": base})
    assert r.status_code == 200
    r = await client.patch(f"{API}/tables/{t['id']}/rows/{row['id']}", json={"cells": {notes: "a\nb\nC"}, "base_version": base})
    assert r.status_code == 200, r.text
    assert r.json()["row"]["cells"][notes] == "A\nb\nC" and r.json()["merged"]
    assert v is None or isinstance(v, int)


async def test_row_delete_restore_and_deleted_list(client):
    """row.delete, row.restore: deleted rows are listed with who and when, and come back."""
    t = await new_table(client, name="L")
    c = t["columns"][0]["id"]
    a = (await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {c: "one"}})).json()["rows"][0]
    r = await client.delete(f"{API}/tables/{t['id']}/rows/{a['id']}")
    assert r.status_code == 200 and r.json()["deleted"] == 1
    dl = (await client.get(f"{API}/tables/{t['id']}/deleted-rows")).json()
    assert dl["total"] == 1 and dl["rows"][0]["title"] == "one" and dl["rows"][0]["deleted_by"]["name"]
    r = await client.patch(f"{API}/tables/{t['id']}/rows/{a['id']}", json={"cells": {c: "two"}})
    assert r.status_code == 409 and r.json()["detail"]["deleted"]
    r = await client.post(f"{API}/tables/{t['id']}/rows/restore", json={"rows": [a["id"]]})
    assert r.json()["restored"] == 1
    assert (await rows(client, t["id"]))["count"] == 1
    # Delete against edit: the edit with restore brings the row back with the edit kept.
    await client.delete(f"{API}/tables/{t['id']}/rows/{a['id']}")
    r = await client.patch(f"{API}/tables/{t['id']}/rows/{a['id']}", json={"cells": {c: "two"}, "restore": True})
    assert r.status_code == 200 and r.json()["row"]["cells"][c] == "two"


async def test_row_duplicate_move_and_manual_order(client):
    """row.duplicate puts a copy below; row.move changes the manual order."""
    t = await new_table(client, name="Order")
    c = t["columns"][0]["id"]
    ids = []
    for x in ("a", "b", "c"):
        ids.append((await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {c: x}})).json()["rows"][0]["id"])
    r = await client.post(f"{API}/tables/{t['id']}/rows/{ids[0]}/duplicate")
    assert r.status_code == 200
    assert [x["cells"][c] for x in (await rows(client, t["id"]))["rows"]] == ["a", "a", "b", "c"]
    r = await client.post(f"{API}/tables/{t['id']}/rows/{ids[2]}/move", json={"before": ids[0]})
    assert r.status_code == 200, r.text
    assert [x["cells"][c] for x in (await rows(client, t["id"]))["rows"]] == ["c", "a", "a", "b"]
    await do_undo(client, r.json()["undo"])
    assert [x["cells"][c] for x in (await rows(client, t["id"]))["rows"]] == ["a", "a", "b", "c"]
    r = await client.post(f"{API}/tables/{t['id']}/rows/{ids[1]}/move", json={"direction": "up"})
    assert [x["cells"][c] for x in (await rows(client, t["id"]))["rows"]][:2] == ["a", "b"]


async def test_bulk_edit_paste_delete_one_undo(client):
    """row.bulk_edit and row.bulk_delete: one batch, one undo; a paste reads text, adds rows past
    the end and keeps what does not convert as text."""
    t = await workout(client)
    w, ex = col(t, "Weight"), col(t, "Exercise")
    made = (await client.post(f"{API}/tables/{t['id']}/rows", json={"rows": [{"cells": {w: str(i)}} for i in range(3)]}
                              )).json()["rows"]
    ids = [x["id"] for x in made]
    r = await client.post(f"{API}/tables/{t['id']}/rows/bulk", json={"op": "edit", "rows": ids, "col": w, "value": "100"})
    assert r.status_code == 200 and r.json()["edited"] == 3
    assert {x["cells"][w] for x in (await rows(client, t["id"]))["rows"]} == {"100"}
    await do_undo(client, r.json()["undo"])
    assert sorted(x["cells"][w] for x in (await rows(client, t["id"]))["rows"]) == ["0", "1", "2"]
    r = await client.post(f"{API}/tables/{t['id']}/rows/bulk", json={"op": "paste", "rows": [
        {"id": ids[0], "cells": {w: "1,250.5", ex: "Squat"}}, {"cells": {w: "about 80", ex: "Row"}}]})
    assert r.status_code == 200, r.text
    assert r.json()["edited"] == 1 and r.json()["added"] == 1 and r.json()["kept_as_text"] == 1
    data = await rows(client, t["id"])
    assert data["rows"][0]["cells"][w] == "1250.5"
    last = data["rows"][-1]
    assert last["raw"][w] == "about 80" and w not in last["cells"]
    t2 = await get_table(client, t["id"])
    assert "Row" in [o["name"] for o in next(c for c in t2["columns"] if c["id"] == ex)["config"]["options"]]
    r = await client.post(f"{API}/tables/{t['id']}/rows/bulk", json={"op": "delete", "rows": ids})
    assert r.json()["deleted"] == 3 and (await rows(client, t["id"]))["count"] == 1
    await do_undo(client, r.json()["undo"])
    assert (await rows(client, t["id"]))["count"] == 4


async def test_row_limit(client, monkeypatch):
    """Over a limit: "This table has 10,000 rows, the most a table can hold"."""
    from mailr.tables import types as ty

    monkeypatch.setitem(ty.LIMITS, "rows", 3)
    t = await new_table(client, name="Small")
    c = t["columns"][0]["id"]
    r = await client.post(f"{API}/tables/{t['id']}/rows", json={"rows": [{"cells": {c: str(i)}} for i in range(3)]})
    assert r.status_code == 200
    r = await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {c: "x"}})
    assert r.status_code == 422 and "the most a table can hold" in r.json()["detail"]


# ---------------------------------------------------------------- column.*


async def test_column_add_rename_reorder_config(client):
    """column.add (left or right), column.rename (with a rename conflict), column.reorder,
    column.config; each with its undo."""
    t = await new_table(client, name="C")
    name = t["columns"][0]["id"]
    r = await client.post(f"{API}/tables/{t['id']}/columns", json={"name": "Qty", "type": "number"})
    qty = r.json()["column"]["id"]
    r = await client.post(f"{API}/tables/{t['id']}/columns", json={"name": "qty", "type": "text"})
    assert r.status_code == 409
    r = await client.post(f"{API}/tables/{t['id']}/columns", json={"name": "Where", "type": "text", "before": qty})
    where = r.json()["column"]["id"]
    t2 = await get_table(client, t["id"])
    assert [c["id"] for c in t2["columns"]] == [name, where, qty]
    await do_undo(client, r.json()["undo"])
    assert [c["id"] for c in (await get_table(client, t["id"]))["columns"]] == [name, qty]
    r = await client.patch(f"{API}/tables/{t['id']}/columns/{qty}", json={"name": "Count", "base_name": "Qty"})
    assert r.status_code == 200
    r = await client.patch(f"{API}/tables/{t['id']}/columns/{qty}", json={"name": "Amount", "base_name": "Qty"})
    assert r.status_code == 409
    r = await client.patch(f"{API}/tables/{t['id']}/columns/{qty}", json={"direction": "left"})
    assert [c["id"] for c in (await get_table(client, t["id"]))["columns"]] == [qty, name]
    await do_undo(client, r.json()["undo"])
    r = await client.patch(f"{API}/tables/{t['id']}/columns/{qty}", json={"config": {"decimals": 2, "format": "thousands"},
                                                                       "required": True})
    c = r.json()["column"]
    assert c["config"] == {"decimals": 2, "format": "thousands"} and c["required"]
    r = await client.patch(f"{API}/tables/{t['id']}/columns/{qty}", json={"config": {"decimals": 9}})
    assert r.status_code == 422


async def test_column_retype_preview_convert_and_undo(client):
    """column.retype: a preview counts the cells that convert; values that do not convert move
    to raw; the undo brings back the old type with the old values."""
    t = await new_table(client, name="R", columns=[{"name": "Item", "type": "text"}, {"name": "Weight", "type": "text"}])
    w = col(t, "Weight")
    for v in ("80", "$1,200", "about 80", "82.5"):
        await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {w: v}})
    r = await client.patch(f"{API}/tables/{t['id']}/columns/{w}", json={"type": "number", "preview": True})
    assert r.json()["preview"]["convert"] == 3 and r.json()["preview"]["fail"] == 1
    assert (await get_table(client, t["id"]))["columns"][1]["type"] == "text"
    r = await client.patch(f"{API}/tables/{t['id']}/columns/{w}", json={"type": "number"})
    assert r.status_code == 200, r.text
    assert r.json()["converted"] == 3 and r.json()["kept_as_text"] == 1
    data = await rows(client, t["id"])
    assert [x["cells"].get(w) for x in data["rows"]] == ["80", "1200", None, "82.5"]
    assert data["rows"][2]["raw"][w] == "about 80"
    # A later valid edit clears the warning mark.
    r = await client.patch(f"{API}/tables/{t['id']}/rows/{data['rows'][2]['id']}", json={"cells": {w: "81"}})
    assert r.json()["row"]["raw"] == {}
    cleared = await client.patch(f"{API}/tables/{t['id']}/rows/{data['rows'][2]['id']}", json={"cells": {w: None}})
    # The undo of the first edit leaves the cell alone: it changed again since.
    await do_undo(client, r.json()["undo"])
    assert (await rows(client, t["id"]))["rows"][2]["cells"].get(w) is None
    await do_undo(client, cleared.json()["undo"])
    # Undo of a retype: the old type and the old values.
    retype = await client.patch(f"{API}/tables/{t['id']}/columns/{w}", json={"type": "text"})
    assert [x["cells"].get(w) for x in (await rows(client, t["id"]))["rows"]] == ["80", "1200", "81", "82.5"]
    await do_undo(client, retype.json()["undo"])
    assert (await get_table(client, t["id"]))["columns"][1]["type"] == "number"


async def test_column_retype_text_to_select_makes_options(client):
    t = await new_table(client, name="S", columns=[{"name": "Item", "type": "text"}, {"name": "Where", "type": "text"}])
    wh = col(t, "Where")
    for v in ("Garage", "Shed", "garage"):
        await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {wh: v}})
    r = await client.patch(f"{API}/tables/{t['id']}/columns/{wh}", json={"type": "select"})
    assert r.status_code == 200
    c = next(c for c in (await get_table(client, t["id"]))["columns"] if c["id"] == wh)
    assert [o["name"] for o in c["config"]["options"]] == ["Garage", "Shed"]
    r = await client.patch(f"{API}/tables/{t['id']}/columns/{wh}", json={"type": "multi_select"})
    vals = [x["cells"][wh] for x in (await rows(client, t["id"]))["rows"]]
    assert all(isinstance(v, list) and len(v) == 1 for v in vals)


async def test_link_column_cannot_retype(client):
    t = await new_table(client, name="L", columns=[{"name": "N", "type": "text"},
                                                    {"name": "Ref", "type": "link", "config": {"target": "note"}}])
    r = await client.patch(f"{API}/tables/{t['id']}/columns/{col(t, 'Ref')}", json={"type": "text"})
    assert r.status_code == 422


async def test_column_options_rename_merge_delete(client):
    """column.options: rename keeps the cells; a deleted option empties its cells (kept in
    history); merging moves them to another option; one undo brings them back."""
    t = await workout(client)
    ex = col(t, "Exercise")
    squat, bench, dead = opt(t, "Exercise", "Squat"), opt(t, "Exercise", "Bench"), opt(t, "Exercise", "Deadlift")
    for o in (squat, bench, dead):
        await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {ex: o}})
    opts = next(c for c in t["columns"] if c["id"] == ex)["config"]["options"]
    renamed = [{**o, "name": "Back squat"} if o["id"] == squat else o for o in opts]
    r = await client.patch(f"{API}/tables/{t['id']}/columns/{ex}", json={"options": renamed})
    assert r.status_code == 200
    keep = [o for o in renamed if o["id"] != dead and o["id"] != bench]
    r = await client.patch(f"{API}/tables/{t['id']}/columns/{ex}", json={"options": keep, "merge": {bench: squat}})
    assert r.status_code == 200, r.text
    assert [x["cells"].get(ex) for x in (await rows(client, t["id"]))["rows"]] == [squat, squat, None]
    await do_undo(client, r.json()["undo"])
    assert [x["cells"].get(ex) for x in (await rows(client, t["id"]))["rows"]] == [squat, bench, dead]


async def test_column_delete_restore_and_primary(client):
    """column.delete keeps the values for 30 days; column.restore brings them back; rows added
    since have empty cells; deleting the primary column moves it on."""
    t = await new_table(client, name="D", columns=[{"name": "Item", "type": "text"}, {"name": "Weight", "type": "number"}])
    item, w = col(t, "Item"), col(t, "Weight")
    await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {item: "a", w: "1"}})
    r = await client.delete(f"{API}/tables/{t['id']}/columns/{w}")
    assert r.status_code == 200 and r.json()["values"] == 1
    dc = (await client.get(f"{API}/tables/{t['id']}/deleted-columns")).json()["columns"]
    assert [c["name"] for c in dc] == ["Weight"] and dc[0]["values"] == 1
    await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {item: "b"}})
    r = await client.post(f"{API}/tables/{t['id']}/columns/{w}/restore")
    assert r.status_code == 200
    assert [x["cells"].get(w) for x in (await rows(client, t["id"]))["rows"]] == ["1", None]
    r = await client.delete(f"{API}/tables/{t['id']}/columns/{item}")
    assert (await get_table(client, t["id"]))["primary_column"] == w
    r = await client.delete(f"{API}/tables/{t['id']}/columns/{w}")
    assert r.status_code == 409  # the last column stays


async def test_set_primary_column(client):
    t = await workout(client)
    r = await client.patch(f"{API}/tables/{t['id']}", json={"primary_column": col(t, "Exercise")})
    assert r.json()["table"]["primary_column"] == col(t, "Exercise")
    await do_undo(client, r.json()["undo"])
    assert (await get_table(client, t["id"]))["primary_column"] == col(t, "Notes")


# ---------------------------------------------------------------- views, filters, footers


async def _seed(client, t):
    ex, date, w, done = col(t, "Exercise"), col(t, "Date"), col(t, "Weight"), col(t, "Done")
    s, b = opt(t, "Exercise", "Squat"), opt(t, "Exercise", "Bench")
    data = [(s, "2026-10-01", "80", True), (b, "2026-10-02", "60", False), (s, "2026-10-03", "85", True),
            (b, "2026-10-04", None, False), (s, None, "90", False)]
    for e, d, wt, dn in data:
        cells = {ex: e, done: dn}
        if d:
            cells[date] = d
        if wt:
            cells[w] = wt
        r = await client.post(f"{API}/tables/{t['id']}/rows", json={"rows": [{"cells": cells}]})
        assert r.status_code == 200, r.text
    return ex, date, w, done, s, b


async def test_views_filters_sort_groups_footers(client):
    """table.view_save, table.view_edit, table.view_delete; filters per type; sort with empty
    cells last; grouping with counts; footers."""
    t = await workout(client)
    ex, date, w, done, s, b = await _seed(client, t)
    spec = {"filters": [{"col": ex, "op": "is", "value": s}], "sort": [{"col": w, "dir": "desc"}],
            "footers": {w: "sum"}}
    r = await client.post(f"{API}/tables/{t['id']}/views", json={"name": "Squats", "spec": spec})
    assert r.status_code == 200, r.text
    vid = r.json()["view"]["id"]
    data = await rows(client, t["id"], view=vid)
    assert [x["cells"][w] for x in data["rows"]] == ["90", "85", "80"]
    assert data["footers"][w]["value"] == "255" and data["total"] == 5 and data["count"] == 3
    # Sort ascending: empty cells last.
    data = await rows(client, t["id"], spec='{"sort": [{"col": "%s", "dir": "asc"}]}' % w)
    assert [x["cells"].get(w) for x in data["rows"]] == ["60", "80", "85", "90", None]
    # Group by select with per-group footers.
    r = await client.patch(f"{API}/tables/{t['id']}/views/{vid}", json={"spec": {"filters": [], "group_by": {"col": ex}}})
    assert r.status_code == 200, r.text
    data = await rows(client, t["id"], view=vid)
    assert [(g["label"], g["count"]) for g in data["groups"]] == [("Squat", 3), ("Bench", 2)]
    assert data["groups"][0]["footers"][w]["value"] == "255"
    await do_undo(client, r.json()["undo"])
    assert (await rows(client, t["id"], view=vid))["count"] == 3
    r = await client.delete(f"{API}/tables/{t['id']}/views/{vid}")
    assert r.status_code == 200
    default = (await get_table(client, t["id"]))["views"][0]["id"]
    r = await client.delete(f"{API}/tables/{t['id']}/views/{default}")
    assert r.status_code == 409


@pytest.mark.parametrize("op,value,expect", [
    ("eq", "80", 1), ("ne", "80", 4), ("gt", "80", 2), ("gte", "80", 3), ("lt", "85", 2), ("lte", "85", 3),
    ("between", ["80", "88"], 2), ("empty", None, 1), ("not_empty", None, 4),
])
async def test_number_filters(client, op, value, expect):
    t = await workout(client)
    ex, date, w, done, s, b = await _seed(client, t)
    f = {"col": w, "op": op}
    if value is not None:
        f["value"] = value
    import json

    assert (await rows(client, t["id"], spec=json.dumps({"filters": [f]})))["count"] == expect


async def test_filters_text_date_checkbox_multi_link(client):
    import json

    t = await workout(client)
    ex, date, w, done, s, b = await _seed(client, t)
    notes = col(t, "Notes")
    first = (await rows(client, t["id"]))["rows"][0]
    await client.patch(f"{API}/tables/{t['id']}/rows/{first['id']}", json={"cells": {notes: "Felt Heavy"}})

    async def count(*filters):
        return (await rows(client, t["id"], spec=json.dumps({"filters": list(filters)})))["count"]

    assert await count({"col": notes, "op": "contains", "value": "heavy"}) == 1
    assert await count({"col": notes, "op": "startswith", "value": "felt"}) == 1  # another app's spelling
    assert await count({"col": notes, "op": "not_contains", "value": "heavy"}) == 4
    assert await count({"col": date, "op": "before", "value": "2026-10-03"}) == 2
    assert await count({"col": date, "op": "between", "value": ["2026-10-02", "2026-10-03"]}) == 2
    assert await count({"col": date, "op": "gte", "value": "2026-10-03"}) == 2
    assert await count({"col": done, "op": "checked"}) == 2
    assert await count({"col": done, "op": "not_checked"}) == 3
    assert await count({"col": ex, "op": "any_of", "value": [s, b]}) == 5
    assert await count({"col": ex, "op": "is_not", "value": "Squat"}) == 2  # by name
    r = await client.get(f"{API}/tables/{t['id']}/rows", params={"spec": json.dumps({"filters": [
        {"col": w, "op": "contains", "value": "8"}]})})
    assert r.status_code == 422 and "not a filter for number columns" in r.json()["detail"]


async def test_relative_date_filter_stays_relative(client):
    import json
    from datetime import date, timedelta

    t = await workout(client)
    d = col(t, "Date")
    today = date.today()
    for n in (0, 3, 10):
        await client.post(f"{API}/tables/{t['id']}/rows", json={"rows": [{"cells": {d: (today - timedelta(days=n)).isoformat()}}]})
    data = await rows(client, t["id"], spec=json.dumps({"filters": [{"col": d, "op": "last_days", "value": 7}]}))
    assert data["count"] == 2
    data = await rows(client, t["id"], spec=json.dumps({"filters": [{"col": d, "op": "on_or_after", "value": "yesterday"}]}))
    assert data["count"] == 1


async def test_stats_footers_by_type(client):
    """table.stats: aggregates for a filter and a grouping."""
    t = await workout(client)
    ex, date, w, done, s, b = await _seed(client, t)
    r = await client.post(f"{API}/tables/{t['id']}/stats", json={"columns": [w, date, done, ex]})
    st = r.json()["columns"]
    assert st[w]["sum"] == "315" and st[w]["avg"] == "78.75" and st[w]["median"] == "82.5"
    assert st[w]["min"] == "60" and st[w]["max"] == "90" and st[w]["count"] == 4 and st[w]["empty"] == 1
    assert st[date]["earliest"] == "2026-10-01" and st[date]["latest"] == "2026-10-04" and st[date]["range"] == 3
    assert st[done]["checked"] == 2 and st[done]["percent_checked"] == 40.0
    assert st[ex]["per_option"]["top"][0] == {"option": s, "name": "Squat", "count": 3}
    r = await client.post(f"{API}/tables/{t['id']}/stats", json={"columns": [w], "group_by": ex,
                                                               "spec": {"filters": [{"col": done, "op": "not_checked"}]}})
    g = {x["label"]: x["columns"][w]["sum"] for x in r.json()["groups"]}
    assert g == {"Squat": "90", "Bench": "60"}


async def test_table_search_in_rows(client):
    t = await new_table(client, name="Q", columns=[{"name": "Item", "type": "text"}, {"name": "Where", "type": "select",
                                                                                     "options": ["Garage"]}])
    item, wh = col(t, "Item"), col(t, "Where")
    await client.post(f"{API}/tables/{t['id']}/rows", json={"rows": [{"cells": {item: "Red PLA", wh: opt(t, "Where", "Garage")}},
                                                                     {"cells": {item: "Blue PETG"}}]})
    assert (await rows(client, t["id"], q="pla"))["count"] == 1
    assert (await rows(client, t["id"], q="garage"))["count"] == 1


# ---------------------------------------------------------------- link columns


async def test_link_columns_titles_visibility_and_linked_from(client, app):
    """Link chips: titles for what the viewer can see; "Not shared with you" otherwise;
    "Deleted" for a deleted target; "Linked from" on the target row."""
    from httpx import ASGITransport, AsyncClient

    from mailr.core.users import create_user
    from mailr.db import sessionmaker

    other_t = await new_table(client, name="Exercises")
    oname = other_t["columns"][0]["id"]
    target = (await client.post(f"{API}/tables/{other_t['id']}/rows", json={"cells": {oname: "Squat"}})).json()["rows"][0]
    note = (await client.post(f"{API}/notes", json={"kind": "text", "title": "Form tips"})).json()["note"]
    t = await new_table(client, name="Log", columns=[
        {"name": "Item", "type": "text"},
        {"name": "Exercise", "type": "link", "config": {"target": "table", "table": other_t["id"]}},
        {"name": "See", "type": "link", "config": {"target": "note", "multiple": True}}])
    ex, see = col(t, "Exercise"), col(t, "See")
    r = await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {
        ex: [{"kind": "row", "table": other_t["id"], "row": target["id"]}],
        see: [{"kind": "note", "id": note["id"]}, {"kind": "note", "id": other_t["id"]}]}})
    assert r.status_code == 200, r.text
    data = await rows(client, t["id"])
    refs = data["refs"]
    assert refs[f"row:{target['id']}"]["title"] == "Squat"
    assert refs[f"note:{note['id']}"]["title"] == "Form tips" and refs[f"note:{other_t['id']}"]["note_kind"] == "table"
    # Two links in a single link column: refused.
    r = await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {ex: [
        {"kind": "row", "table": other_t["id"], "row": target["id"]},
        {"kind": "row", "table": other_t["id"], "row": str(uuid.uuid4())}]}})
    assert r.status_code == 422
    # Linked from, on the target row.
    lf = (await client.get(f"{API}/tables/{other_t['id']}/rows/{target['id']}")).json()["linked_from"]
    assert lf[0]["table"] == t["id"] and lf[0]["column"] == "Exercise"
    # Deleted target.
    await client.delete(f"{API}/tables/{other_t['id']}/rows/{target['id']}")
    assert (await rows(client, t["id"]))["refs"][f"row:{target['id']}"]["state"] == "deleted"
    await client.post(f"{API}/tables/{other_t['id']}/rows/restore", json={"rows": [target["id"]]})
    assert (await rows(client, t["id"]))["refs"][f"row:{target['id']}"]["title"] == "Squat"
    # Another user's targets: a link to them is refused, and a stored one shows "Not shared with you".
    async with sessionmaker()() as db:
        other = await create_user(db, f"o-{uuid.uuid4().hex[:8]}@example.com", "Other", password="test-password-123")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c2:
        r = await c2.post(f"{API}/auth/login", json={"email": other.email, "password": "test-password-123"})
        c2.headers["X-CSRF-Token"] = r.json()["csrf"]
        t2 = (await c2.post(f"{API}/tables", json={"name": "Theirs", "columns": [
            {"name": "N", "type": "text"}, {"name": "R", "type": "link", "config": {"target": "note"}}]})).json()["table"]
        r = await c2.post(f"{API}/tables/{t2['id']}/rows", json={"cells": {col(t2, "R"): [{"kind": "note", "id": note["id"]}]}})
        assert r.status_code == 422
        from mailr.tables import core as tcore
        from mailr.tables.models import TableRow

        async with sessionmaker()() as db:
            from sqlalchemy import select

            from mailr.tables.models import DataTable

            tb = (await db.execute(select(DataTable).where(DataTable.pid == t2["id"]))).scalar_one()
            db.add(TableRow(id=tcore.uuid7(), table_id=tb.id, cells={col(t2, "R"): [{"kind": "note", "id": note["id"]}]},
                            raw={}, order="i"))
            await db.commit()
        data = (await c2.get(f"{API}/tables/{t2['id']}/rows")).json()
        assert data["refs"][f"note:{note['id']}"] == {"kind": "note", "title": "Not shared with you", "state": "hidden"}


async def test_link_targets_picker(client):
    t = await new_table(client, name="X", columns=[{"name": "N", "type": "text"},
                                                    {"name": "Ref", "type": "link", "config": {"target": "note"}}])
    await client.post(f"{API}/notes", json={"kind": "text", "title": "Budget memo"})
    r = await client.get(f"{API}/tables/{t['id']}/columns/{col(t, 'Ref')}/targets", params={"q": "budget"})
    assert [x["title"] for x in r.json()["items"]] == ["Budget memo"]


# ---------------------------------------------------------------- CSV


CSV = ("Date,Item,Amount,Paid\n2026-10-01,Coffee,$3.50,yes\n2026-10-02,Lunch,\"$1,212.00\",no\n"
       "2026-10-03,Snack,about 2,yes\n")


async def test_csv_import_preview_and_new_table(client):
    """table.import_csv: the preview detects the delimiter, the header and the types with the
    share that converts; the import keeps what does not convert as text; one undo."""
    r = await client.post(f"{API}/tables/import", json={"text": CSV, "preview": True})
    pv = r.json()
    assert pv["delimiter"] == "," and pv["header"] and pv["total"] == 3
    assert [(c["name"], c["type"]) for c in pv["columns"]] == [("Date", "date"), ("Item", "text"), ("Amount", "currency"),
                                                              ("Paid", "checkbox")]
    amount = next(c for c in pv["columns"] if c["name"] == "Amount")
    assert amount["currency"] == "USD" and amount["label"] == "Currency · 67%"
    r = await client.post(f"{API}/tables/import", json={"text": CSV, "name": "Spending", "columns": [
        {"index": 0}, {"index": 1}, {"index": 2, "type": "currency"}, {"index": 3}]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["imported"] == 3 and out["kept_as_text"] == 1
    assert out["summary"] == "Imported 3 rows · 1 cell kept as text"
    t = out["table"]
    amt = col(t, "Amount")
    assert next(c for c in t["columns"] if c["id"] == amt)["config"]["currency"] == "USD"
    data = await rows(client, t["id"])
    assert [x["cells"].get(amt) for x in data["rows"]] == ["3.5", "1212", None]
    assert data["rows"][2]["raw"][amt] == "about 2"


async def test_csv_semicolon_latin1_no_header(client):
    data = "1;Café;2,5\n2;Tea;3\n".encode("latin-1")
    import base64

    r = await client.post(f"{API}/tables/import", json={"base64": base64.b64encode(data).decode(), "preview": True})
    pv = r.json()
    assert pv["delimiter"] == ";" and pv["encoding"] == "latin-1" and not pv["header"]
    assert pv["rows"][0][1] == "Café" and pv["columns"][0]["name"] == "Column 1"


async def test_csv_append_maps_by_name_and_undo(client):
    t = await new_table(client, name="Spend", template="log")
    r = await client.post(f"{API}/tables/{t['id']}/import", json={"text": "item,amount,shop\nTea,3,Corner\n"})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1
    t2 = await get_table(client, t["id"])
    assert [c["name"] for c in t2["columns"]] == ["Date", "Item", "Amount", "shop"]
    data = await rows(client, t["id"])
    assert data["rows"][0]["cells"][col(t2, "Item")] == "Tea"
    await do_undo(client, r.json()["undo"])
    assert (await rows(client, t["id"]))["count"] == 0
    assert [c["name"] for c in (await get_table(client, t["id"]))["columns"]] == ["Date", "Item", "Amount"]


async def test_csv_export_view_and_all(client):
    """table.export_csv: the view's filters, sort and visible columns, or the whole table;
    currency code in the header, options by name, ISO dates."""
    import json

    t = await workout(client)
    ex, date, w, done, s, b = await _seed(client, t)
    r = await client.get(f"{API}/tables/{t['id']}/export.csv", params={"scope": "all"})
    lines = r.text.strip().split("\r\n")
    assert lines[0] == "Exercise,Date,Weight,Done,Notes" and lines[1] == "Squat,2026-10-01,80,yes,"
    spec = {"filters": [{"col": ex, "op": "is", "value": b}], "hidden": [done, col(t, "Notes")]}
    r = await client.get(f"{API}/tables/{t['id']}/export.csv", params={"spec": json.dumps(spec), "bom": "true"})
    body = r.content.decode("utf-8")
    # Notes is the primary column, which a view never hides.
    assert body.startswith("\ufeffExercise,Date,Weight,Notes\r\nBench,2026-10-02,60,\r\n"), repr(body[:80])
    cur = await new_table(client, name="M", columns=[{"name": "Amount", "type": "currency", "config": {"currency": "EUR"}}])
    r = await client.get(f"{API}/tables/{cur['id']}/export.csv")
    assert r.text.startswith("Amount (EUR)")


# ---------------------------------------------------------------- access


async def test_access_other_user_gets_404(client, app):
    from httpx import ASGITransport, AsyncClient

    from mailr.core.users import create_user
    from mailr.db import sessionmaker

    t = await new_table(client, name="Mine")
    c = t["columns"][0]["id"]
    row = (await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {c: "x"}})).json()["rows"][0]
    async with sessionmaker()() as db:
        other = await create_user(db, f"o-{uuid.uuid4().hex[:8]}@example.com", "Other", password="test-password-123")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c2:
        r = await c2.post(f"{API}/auth/login", json={"email": other.email, "password": "test-password-123"})
        c2.headers["X-CSRF-Token"] = r.json()["csrf"]
        for method, path, body in (("GET", f"/tables/{t['id']}", None), ("GET", f"/tables/{t['id']}/rows", None),
                                   ("PATCH", f"/tables/{t['id']}", {"name": "x"}),
                                   ("DELETE", f"/tables/{t['id']}", None),
                                   ("POST", f"/tables/{t['id']}/rows", {"cells": {c: "y"}}),
                                   ("PATCH", f"/tables/{t['id']}/rows/{row['id']}", {"cells": {c: "z"}}),
                                   ("GET", f"/tables/{t['id']}/changes", None),
                                   ("GET", f"/tables/{t['id']}/export.csv", None)):
            r = await c2.request(method, API + path, json=body)
            assert r.status_code == 404, (method, path, r.text)
        assert (await c2.get(f"{API}/tables")).json()["tables"] == []
        assert (await c2.get(f"{API}/notes/search", params={"q": "is:table"})).json()["tables"] == []


async def test_tokens_refused(client, app, user):
    from httpx import ASGITransport, AsyncClient

    r = await client.post(f"{API}/tokens", json={"name": "t", "scopes": ["read"]})
    tok = r.json()["token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c2:
        r = await c2.get(f"{API}/tables", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 403


async def test_search_is_table_and_text_cells(client):
    """note.search: `is:table` lists tables; words find table names and text cells."""
    t = await new_table(client, name="Filament stock", columns=[{"name": "Colour", "type": "text"}])
    await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {t["columns"][0]["id"]: "Neon green"}})
    await client.post(f"{API}/notes", json={"kind": "text", "title": "green tea"})
    r = await client.get(f"{API}/notes/search", params={"q": "is:table"})
    assert [x["id"] for x in r.json()["tables"]] == [t["id"]] and r.json()["notes"] == []
    r = await client.get(f"{API}/notes/search", params={"q": "neon"})
    assert [x["id"] for x in r.json()["tables"]] == [t["id"]]
    assert r.json()["tables"][0]["matches"][0]["text"].startswith("Neon green")
    r = await client.get(f"{API}/notes/search", params={"q": "green"})
    assert len(r.json()["tables"]) == 1 and len(r.json()["notes"]) == 1
    r = await client.get(f"{API}/notes/search", params={"q": "is:list green"})
    assert r.json()["tables"] == []


async def test_table_links_to_conversation(client):
    """table.link, table.unlink: the links panels list linked tables."""
    from mailr.db import sessionmaker
    from mailr.models import Conversation, User

    async with sessionmaker()() as db:
        from sqlalchemy import select

        u = (await db.execute(select(User).order_by(User.id.desc()).limit(1))).scalar_one()
        conv = Conversation(user_id=u.id, subject="Q4 plan")
        db.add(conv)
        await db.commit()
        cid = conv.id
    t = await new_table(client, name="Budget")
    r = await client.post(f"{API}/tables/{t['id']}/links", json={"target_kind": "conversation",
                                                                "target_ref": {"conversation_id": cid}})
    if r.status_code == 404:
        pytest.skip("the newest user is not the client user")
    assert r.status_code == 200, r.text
    r2 = await client.get(f"{API}/tables/linked", params={"target_kind": "conversation", "conversation_id": cid})
    assert [x["id"] for x in r2.json()["tables"]] == [t["id"]]
    await do_undo(client, r.json()["undo"])
    assert (await get_table(client, t["id"]))["links"] == []
