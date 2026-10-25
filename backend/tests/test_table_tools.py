# ACT-SURFACES: assistant
"""The assistant's table tools: the user's own access,
typed values with errors that name the column, the permission classes tables.rows,
tables.rows_delete and tables.schema (and notes.delete for Trash), tainted turns, local-only
tables, the screen context, untrusted reads and citations.

Covers: table.search, table.stats, table.create, table.rename, table.color, table.pin, table.label,
table.archive, table.restore, table.link, table.delete, table.view_save, table.view_delete,
table.history, table.restore_point, column.add, column.rename, column.retype, column.options,
column.config, column.reorder, column.set_primary, column.delete, column.restore, row.add, row.edit,
row.duplicate, row.delete, row.bulk_edit, row.bulk_delete, row.history, row.restore,
row.restore_version."""

from __future__ import annotations

import uuid

import pytest

from mailr.agent.tools import UNTRUSTED_TOOLS, ToolCtx, ToolError, run_tool
from mailr.core import permissions
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.models import PendingAction, User

# These tests never touch the mail store: database-only users (conftest.make_db_user).
pytestmark = pytest.mark.db_user

API = "/api/v1"


async def _ctx(db, user_id, *, assistant=True, untrusted=False, ui=None, scopes=None, errand=False):
    u = await db.get(User, user_id)
    p = Principal(user=u, scopes=set(scopes if scopes is not None else ALL_SCOPES) - {"admin"})
    ctx = ToolCtx(db=db, p=p, assistant=assistant, chat_session_id=f"s-{uuid.uuid4().hex[:6]}", ui=ui or {},
                  untrusted=untrusted, chat_context={"errand": True} if errand else None)
    ctx.local_model = False
    return ctx


async def _approve(db, user_id: int, approval_id: int) -> PendingAction:
    u = await db.get(User, user_id)
    await permissions.decide(db, u, approval_id, "approve")
    await db.commit()
    await permissions.execute_job({"pending_id": approval_id}, None)
    async with sessionmaker()() as d2:
        return await d2.get(PendingAction, approval_id)


async def _workout(client):
    r = await client.post(f"{API}/tables", json={"name": "Workouts", "columns": [
        {"name": "Exercise", "type": "select", "options": ["Squat", "Bench"]},
        {"name": "Date", "type": "date"}, {"name": "Weight", "type": "number"}, {"name": "Note", "type": "text"}]})
    assert r.status_code == 200, r.text
    return r.json()["table"]


def test_read_tools_are_untrusted():
    assert {"list_tables", "get_table", "table_stats", "table_history"} <= UNTRUSTED_TOOLS


def test_classes_and_scope():
    assert permissions.CLASSES["tables.rows"] == ("allow", "notes")
    assert permissions.CLASSES["tables.rows_delete"] == ("ask", "notes")
    assert permissions.CLASSES["tables.schema"] == ("allow", "notes")
    assert permissions.CLASSES["notes.delete"][0] == "ask"
    assert "notes" in ALL_SCOPES


async def test_list_get_and_cite(client, user):
    """list_tables and get_table (table.search): ids, columns with option names, typed values by
    column name, row ids and citations."""
    t = await _workout(client)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = await run_tool(ctx, "table_rows", {"table": "workouts", "action": "add", "rows": [
            {"values": {"Exercise": "Squat", "Date": "2026-10-03", "Weight": 82.5}},
            {"values": {"Exercise": "Bench", "Date": "2026-10-04", "Weight": "60"}}]})
        assert res.data["ok"] and res.data["added"] == 2, res.data
        lst = await run_tool(ctx, "list_tables", {})
        item = next(i for i in lst.data["items"] if i["id"] == t["id"])
        assert item["rows"] == 2 and item["cite"] == f"[[table:{t['id']}]]"
        assert item["columns"][0]["options"] == {"untrusted": True, "content": ["Squat", "Bench"]}
        got = await run_tool(ctx, "get_table", {"table": t["id"], "filters": [{"column": "Exercise", "op": "is", "value": "Squat"}]})
        assert got.data["count"] == 1
        row = got.data["rows"][0]
        assert row["values"]["content"] == {"Exercise": "Squat", "Date": "2026-10-03", "Weight": "82.5"}
        assert row["cite"] == f"[[table:{t['id']}#{row['row_id']}]]"
        with pytest.raises(ToolError, match="not a filter for number"):
            await run_tool(ctx, "get_table", {"table": t["id"], "filters": [{"column": "Weight", "op": "contains", "value": "8"}]})


async def test_table_stats_tool(client, user):
    t = await _workout(client)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        await run_tool(ctx, "table_rows", {"table": t["id"], "action": "add", "rows": [
            {"values": {"Exercise": "Squat", "Weight": 80}}, {"values": {"Exercise": "Squat", "Weight": 90}},
            {"values": {"Exercise": "Bench", "Weight": 60}}]})
        st = await run_tool(ctx, "table_stats", {"table": t["id"], "columns": ["Weight"], "group_by": "Exercise"})
        assert st.data["columns"]["content"]["Weight"]["sum"] == "230"
        groups = {g["group"]: g["columns"]["Weight"]["avg"] for g in st.data["groups"]["content"]}
        assert groups == {"Squat": "85", "Bench": "60"}


async def test_typed_value_errors_name_the_column(client, user):
    t = await _workout(client)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        with pytest.raises(ToolError, match="Column 'Weight' takes a number"):
            await run_tool(ctx, "table_rows", {"table": t["id"], "action": "add", "rows": [{"values": {"Weight": "heavy"}}]})
        with pytest.raises(ToolError, match="Column 'Date' takes a date"):
            await run_tool(ctx, "table_rows", {"table": t["id"], "action": "add", "rows": [{"values": {"Date": "someday"}}]})
        with pytest.raises(ToolError, match="unknown column 'Reps'"):
            await run_tool(ctx, "table_rows", {"table": t["id"], "action": "add", "rows": [{"values": {"Reps": 5}}]})


async def test_rows_by_primary_value_and_edit(client, user):
    """Rows by id or by primary value, matched on the server; two matches are an error."""
    r = await client.post(f"{API}/tables", json={"name": "Filament", "columns": [
        {"name": "Colour", "type": "text"}, {"name": "Grams", "type": "number"}]})
    t = r.json()["table"]
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        await run_tool(ctx, "table_rows", {"table": "Filament", "action": "add", "rows": [
            {"values": {"Colour": "Mint", "Grams": 800}}, {"values": {"Colour": "Magenta", "Grams": 500}}]})
        res = await run_tool(ctx, "table_rows", {"table": "Filament", "action": "edit",
                                                 "rows": [{"match": "mint", "values": {"Grams": 650}}]})
        assert res.data["edited"] == 1
        await run_tool(ctx, "table_rows", {"table": "Filament", "action": "add", "rows": [{"values": {"Colour": "Mint"}}]})
        with pytest.raises(ToolError, match="2 rows have that primary value"):
            await run_tool(ctx, "table_rows", {"table": "Filament", "action": "edit",
                                               "rows": [{"match": "Mint", "values": {"Grams": 1}}]})
    rows = (await client.get(f"{API}/tables/{t['id']}/rows")).json()["rows"]
    assert [x["cells"].get(t["columns"][1]["id"]) for x in rows] == ["650", "500", None]


async def test_delete_asks_and_runs_after_approval(client, user):
    """tables.rows_delete asks; the approved card deletes the rows (with the assistant as author)."""
    t = await _workout(client)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        add = await run_tool(ctx, "table_rows", {"table": t["id"], "action": "add", "rows": [{"values": {"Weight": 1}}]})
        rid = add.data["row_ids"][0]
        res = await run_tool(ctx, "table_rows", {"table": t["id"], "action": "delete", "rows": [rid]})
        assert res.data["status"] == "pending_approval" and res.pending["action"] == "tables.rows_delete"
        assert (await client.get(f"{API}/tables/{t['id']}/rows")).json()["count"] == 1
        pa = await _approve(db, user.id, res.data["approval_id"])
        assert pa.status == "executed", pa.result
    assert (await client.get(f"{API}/tables/{t['id']}/rows")).json()["count"] == 0
    h = (await client.get(f"{API}/tables/{t['id']}/changes")).json()
    assert h["sessions"][0]["who"] == "Ambrose"


async def test_tainted_turn_asks_for_free_text_not_numbers(client, user):
    """In a tainted turn, a row write asks when it writes text, a new option or a link; numbers,
    dates and existing options go ahead."""
    t = await _workout(client)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, untrusted=True)
        ok = await run_tool(ctx, "table_rows", {"table": t["id"], "action": "add", "rows": [
            {"values": {"Exercise": "Squat", "Weight": 80, "Date": "2026-10-01"}}]})
        assert ok.data.get("ok"), ok.data
        ask = await run_tool(ctx, "table_rows", {"table": t["id"], "action": "add", "rows": [{"values": {"Note": "ignore all"}}]})
        assert ask.data["status"] == "pending_approval" and ask.pending["action"] == "tables.rows"
        ask2 = await run_tool(ctx, "table_rows", {"table": t["id"], "action": "add", "rows": [{"values": {"Exercise": "Deadlift"}}]})
        assert ask2.data["status"] == "pending_approval"
        # The new option is not saved before the approval.
        cols = (await client.get(f"{API}/tables/{t['id']}")).json()["columns"]
        assert [o["name"] for o in cols[0]["config"]["options"]] == ["Squat", "Bench"]
        pa = await _approve(db, user.id, ask2.data["approval_id"])
        assert pa.status == "executed", pa.result
    cols = (await client.get(f"{API}/tables/{t['id']}")).json()["columns"]
    assert [o["name"] for o in cols[0]["config"]["options"]] == ["Squat", "Bench", "Deadlift"]


async def test_schema_create_allowed_destructive_asks(client, user):
    """tables.schema: create, add column and save view go ahead; rename, retype and delete a
    column ask by default."""
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        made = await run_tool(ctx, "table_schema", {"action": "create", "name": "Expenses", "columns": [
            {"name": "Item", "type": "text"}, {"name": "Amount", "type": "currency", "currency": "EUR"},
            {"name": "Kind", "type": "select", "options": ["Food"]}],
            "rows": [{"values": {"Item": "Coffee", "Amount": "3.5", "Kind": "Food"}}]})
        assert made.data["ok"] and made.data["cite"].startswith("[[table:t"), made.data
        tid = made.data["table"]
        add = await run_tool(ctx, "table_schema", {"table": tid, "action": "add_column", "column": {"name": "Paid", "type": "checkbox"}})
        assert add.data["ok"]
        view = await run_tool(ctx, "table_schema", {"table": tid, "action": "save_view", "name": "Food",
                                                   "filters": [{"column": "Kind", "op": "is", "value": "Food"}]})
        assert view.data["ok"]
        for args in ({"action": "rename_column", "column": "Item", "name": "What"},
                     {"action": "retype_column", "column": "Item", "type": "number"},
                     {"action": "delete_column", "column": "Paid"},
                     {"action": "options", "column": "Kind", "options": ["Food", "Travel"]}):
            res = await run_tool(ctx, "table_schema", {"table": tid, **args})
            assert res.data["status"] == "pending_approval", args
            assert res.pending["action"] == "tables.schema"
        retype = await run_tool(ctx, "table_schema", {"table": tid, "action": "retype_column", "column": "Item", "type": "number"})
        assert "0 cells convert, 1 do not" in " ".join(retype.pending["preview"].get("lines") or [])
    rows = (await client.get(f"{API}/tables/{tid}/rows")).json()["rows"]
    assert len(rows) == 1 and "3.5" in rows[0]["cells"].values()


async def test_delete_table_is_notes_delete(client, user):
    t = await _workout(client)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = await run_tool(ctx, "delete_table", {"table": t["id"]})
        assert res.pending["action"] == "notes.delete"
        pa = await _approve(db, user.id, res.data["approval_id"])
        assert pa.status == "executed", pa.result
    r = await client.get(f"{API}/tables", params={"view": "trash"})
    assert [x["id"] for x in r.json()["tables"]] == [t["id"]]


async def test_other_users_table_not_found_and_name_clash(client, user, app):
    from mailr.core.users import create_user

    t = await _workout(client)
    await client.post(f"{API}/tables", json={"name": "Workouts"})
    async with sessionmaker()() as db:
        other = await create_user(db, f"o-{uuid.uuid4().hex[:8]}@example.com", "Other", password="test-password-123")
        await db.commit()
        ctx = await _ctx(db, other.id)
        with pytest.raises(ToolError, match="table not found"):
            await run_tool(ctx, "get_table", {"table": t["id"]})
        mine = await _ctx(db, user.id)
        with pytest.raises(ToolError, match="2 tables have that name"):
            await run_tool(mine, "get_table", {"table": "workouts"})


async def test_local_only_hidden_from_hosted_model(client, user):
    r = await client.post(f"{API}/tables", json={"name": "Private", "local_only": True})
    t = r.json()["table"]
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        lst = await run_tool(ctx, "list_tables", {})
        assert t["id"] not in [i["id"] for i in lst.data["items"]] and "hidden" in lst.data["note"]
        with pytest.raises(ToolError, match="local-only"):
            await run_tool(ctx, "get_table", {"table": t["id"]})
        ctx.local_model = True
        got = await run_tool(ctx, "get_table", {"table": t["id"]})
        assert got.sensitive


async def test_screen_context_rows(client, user):
    """"These rows": the selection on screen; "this table": the one on screen."""
    t = await _workout(client)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        add = await run_tool(ctx, "table_rows", {"table": t["id"], "action": "add", "rows": [
            {"values": {"Weight": 1}}, {"values": {"Weight": 2}}, {"values": {"Weight": 3}}]})
        ids = add.data["row_ids"]
        ctx2 = await _ctx(db, user.id, ui={"table": {"id": t["id"], "selected_rows": ids[:2]}})
        res = await run_tool(ctx2, "table_rows", {"action": "bulk_edit", "column": "Weight", "value": 9})
        assert res.data["edited"] == 2
    vals = [x["cells"].get(t["columns"][2]["id"]) for x in (await client.get(f"{API}/tables/{t['id']}/rows")).json()["rows"]]
    assert vals == ["9", "9", "3"]


async def test_tokens_need_the_notes_scope(client, user):
    t = await _workout(client)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, assistant=False, scopes={"read"})
        with pytest.raises(ToolError, match="'notes' scope"):
            await run_tool(ctx, "get_table", {"table": t["id"]})


async def test_history_tool_reads_and_restores(client, user):
    t = await _workout(client)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        add = await run_tool(ctx, "table_rows", {"table": t["id"], "action": "add", "rows": [{"values": {"Weight": 80}}]})
        rid = add.data["row_ids"][0]
        await run_tool(ctx, "table_rows", {"table": t["id"], "action": "edit", "rows": [{"row_id": rid, "values": {"Weight": 85}}]})
        h = await run_tool(ctx, "table_history", {"table": t["id"], "action": "row", "row_id": rid})
        assert [v["text"] for v in h.data["earlier_values"]["content"]["Weight"]] == ["85", "80"]
        seq = h.data["earlier_values"]["content"]["Weight"][1]["seq"]
        res = await run_tool(ctx, "table_history", {"table": t["id"], "action": "restore_row", "row_id": rid, "seq": seq,
                                                    "column": "Weight"})
        assert res.data["ok"]
        sessions = await run_tool(ctx, "table_history", {"table": t["id"]})
        assert sessions.data["sessions"][0]["who"] == "Ambrose"
        added = h.data["changes"]["content"][-1]["seq"]
        pt = await run_tool(ctx, "table_history", {"table": t["id"], "action": "restore_point", "seq": added})
        assert pt.pending["action"] == "tables.schema"
    rows = (await client.get(f"{API}/tables/{t['id']}/rows")).json()["rows"]
    assert rows[0]["cells"][t["columns"][2]["id"]] == "80"


async def test_errand_asks_before_writing_to_a_group_table(client, user):
    from sqlalchemy import update

    from mailr.tables.models import DataTable

    t = await _workout(client)
    group = f"g-{uuid.uuid4().hex[:8]}"  # a group of this test only: other tests' users are not in it
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.assigned_groups = sorted({*(u.assigned_groups or []), group})
        await db.execute(update(DataTable).where(DataTable.pid == t["id"]).values(owner_user_id=None, owner_group=group))
        await db.commit()
        ctx = await _ctx(db, user.id, errand=True)
        res = await run_tool(ctx, "table_rows", {"table": t["id"], "action": "add", "rows": [{"values": {"Weight": 1}}]})
        assert res.data["status"] == "pending_approval"


async def test_table_state_tool(client, user):
    """table_state: colour, pin, labels and archive (notes.edit, allowed), a link, and the way
    back from Trash."""
    t = await _workout(client)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        assert (await run_tool(ctx, "table_state", {"table": t["id"], "action": "color", "color": "sage"})).data["ok"]
        assert (await run_tool(ctx, "table_state", {"table": t["id"], "action": "pin"})).data["state"]["pinned"]
        res = await run_tool(ctx, "table_state", {"table": t["id"], "action": "labels", "add": ["Gym"]})
        assert res.data["state"]["labels"] == ["Gym"]
        res = await run_tool(ctx, "table_state", {"table": t["id"], "action": "archive"})
        assert res.data["state"]["archived"]
    got = (await client.get(f"{API}/tables/{t['id']}")).json()
    assert got["color"] == "sage" and got["state"]["labels"] == ["Gym"]
    await client.delete(f"{API}/tables/{t['id']}")
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = await run_tool(ctx, "table_state", {"table": t["id"], "action": "restore"})
        assert res.data["ok"]
    assert (await client.get(f"{API}/tables/{t['id']}")).json()["deleted_at"] is None


async def test_more_schema_actions_after_approval(client, user):
    """Rename a table, reorder and configure columns, set the primary column, delete a view and
    bring back a column: each asks, then runs once approved."""
    t = await _workout(client)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        v = await run_tool(ctx, "table_schema", {"table": t["id"], "action": "save_view", "name": "Heavy",
                                                "filters": [{"column": "Weight", "op": "gt", "value": 100}]})
        steps = [{"action": "rename", "name": "Lifts"},
                 {"action": "move_column", "column": "Note", "before": "Exercise"},
                 {"action": "config", "column": "Weight", "decimals": 1},
                 {"action": "set_primary", "column": "Exercise"},
                 {"action": "delete_view", "view": "Heavy"},
                 {"action": "delete_column", "column": "Date"}]
        for st in steps:
            res = await run_tool(ctx, "table_schema", {"table": t["id"], **st})
            pa = await _approve(db, user.id, res.data["approval_id"])
            assert pa.status == "executed", (st, pa.result)
        res = await run_tool(ctx, "table_history", {"table": t["id"], "action": "restore_column", "column": "Date"})
        pa = await _approve(db, user.id, res.data["approval_id"])
        assert pa.status == "executed", pa.result
        assert v.data["ok"]
    got = (await client.get(f"{API}/tables/{t['id']}")).json()
    assert got["name"] == "Lifts" and [c["name"] for c in got["columns"]][0] == "Note"
    assert got["primary_column"] == next(c["id"] for c in got["columns"] if c["name"] == "Exercise")
    assert [v["name"] for v in got["views"]] == ["All"] and "Date" in [c["name"] for c in got["columns"]]
    assert next(c for c in got["columns"] if c["name"] == "Weight")["config"]["decimals"] == 1


async def test_duplicate_and_restore_rows_tools(client, user):
    t = await _workout(client)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        add = await run_tool(ctx, "table_rows", {"table": t["id"], "action": "add", "rows": [{"values": {"Weight": 5}}]})
        rid = add.data["row_ids"][0]
        dup = await run_tool(ctx, "table_rows", {"table": t["id"], "action": "duplicate", "rows": [rid]})
        assert dup.data["added"] == 1
        res = await run_tool(ctx, "table_rows", {"table": t["id"], "action": "delete", "rows": [rid]})
        await _approve(db, user.id, res.data["approval_id"])
        dl = await run_tool(ctx, "table_history", {"table": t["id"], "action": "deleted_rows"})
        assert dl.data["total"] == 1
        back = await run_tool(ctx, "table_history", {"table": t["id"], "action": "restore_rows", "rows": [rid]})
        assert back.data["restored"] == 1


async def test_citations_resolve_tables_and_rows(client, user, app):
    """[[table:<id>]] and [[table:<id>#<row_id>]] become numbered links with the row's primary
    value; a table the reader cannot see, or a deleted row, drops the marker."""
    from mailr.intel import citations

    t = await _workout(client)
    note_col = t["columns"][3]["id"]
    row = (await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {note_col: "Squat day"}})).json()["rows"][0]
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        p = Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"})
        text, cites = await citations.resolve(db, p, f"Best set [[table:{t['id']}#{row['id']}]] in [[table:{t['id']}]].")
        assert text == "Best set [1](mailr-cite:1) in [2](mailr-cite:2)."
        assert cites[0]["kind"] == "table" and cites[0]["title"] == "Squat day" and cites[0]["row"] == row["id"]
        assert cites[0]["url"].endswith(f"/tables/t/{t['id']}/r/{row['id']}") and cites[1]["title"] == "Workouts"
        from mailr.core.users import create_user

        other = await create_user(db, f"o-{uuid.uuid4().hex[:8]}@example.com", "Other", password="test-password-123")
        await db.commit()
        po = Principal(user=other, scopes=set(ALL_SCOPES) - {"admin"})
        text, cites = await citations.resolve(db, po, f"See [[table:{t['id']}]].")
        assert text == "See." and cites == []
    await client.delete(f"{API}/tables/{t['id']}/rows/{row['id']}")
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        p = Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"})
        text, cites = await citations.resolve(db, p, f"Gone [[table:{t['id']}#{row['id']}]].")
        assert text == "Gone." and cites == []


async def test_token_not_local_only_does_not_see_local_only_tables(client, user):
    """As local-only notes: an API token that is not local-only does not see local-only tables."""
    from types import SimpleNamespace

    r = await client.post(f"{API}/tables", json={"name": "Secret", "local_only": True})
    t = r.json()["table"]
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, assistant=False)
        ctx.p.token = SimpleNamespace(local_only=False, id=0, name="t", scopes=list(ALL_SCOPES))
        with pytest.raises(ToolError, match="local-only"):
            await run_tool(ctx, "get_table", {"table": t["id"]})
        assert t["id"] not in [i["id"] for i in (await run_tool(ctx, "list_tables", {})).data["items"]]
