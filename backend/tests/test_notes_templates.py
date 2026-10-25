# ACT-SURFACES: api
"""Templates of notes, checklists and tables (notes/templates.py): the built-in starters
hold no personal data, note.save_template keeps sections, items and quantities (unchecked),
note.from_template makes a new note, list or table with an undo, table.save_template keeps the
columns (not link columns, not rows), note.delete_template with its undo, and the limits."""

from __future__ import annotations

import pytest

# These tests never touch the mail store: database-only users (conftest.make_db_user).
pytestmark = pytest.mark.db_user

API = "/api/v1"


async def templates(client):
    r = await client.get(f"{API}/notes/templates")
    assert r.status_code == 200, r.text
    return r.json()["templates"]


async def test_builtin_templates_are_generic(client):
    """The starters: a shopping list with store sections, a packing list, meeting notes and an
    expenses table. Their text names no person, place or address."""
    ts = await templates(client)
    by = {t["id"]: t for t in ts}
    assert {"b-shopping", "b-packing", "b-meeting", "b-expenses"} <= set(by)
    assert all(t["builtin"] for t in ts if t["id"].startswith("b-"))
    assert by["b-shopping"]["kind"] == "checklist" and by["b-expenses"]["kind"] == "table"
    text = repr([t["content"] for t in ts if t["builtin"]]).lower()
    for bad in ("@", "rowan", "costco", "dana", "http"):
        assert bad not in text


async def test_note_from_template_makes_a_list_with_sections_unchecked(client):
    """note.from_template: the shopping list starter becomes a shopping list with its sections in
    order and its items in them, nothing checked; the undo moves it to Trash."""
    r = await client.post(f"{API}/notes/from-template", json={"template": "b-shopping"})
    assert r.status_code == 200, r.text
    n = r.json()["note"]
    assert n["kind"] == "checklist" and n["shopping"] is True and n["title"] == "Shopping"
    assert [s["name"] for s in n["sections"]][:3] == ["Produce", "Bakery", "Dairy"]
    assert all(s["order_mode"] == "learned" for s in n["sections"])
    secs = {s["id"]: s["name"] for s in n["sections"]}
    assert ("milk", "Dairy") in [(i["text"], secs.get(i["section_id"])) for i in n["items"]]
    assert not any(i["checked"] for i in n["items"])
    undo = r.json()["undo"]
    r = await client.request(undo["method"], API + undo["path"], json=undo["body"])
    assert r.status_code == 200
    assert n["id"] in [c["id"] for c in (await client.get(f"{API}/notes", params={"view": "trash"})).json()["notes"]]


async def test_note_from_template_text_and_table(client):
    r = await client.post(f"{API}/notes/from-template", json={"template": "b-meeting", "title": "Budget review",
                                                              "labels": ["Work"]})
    assert r.status_code == 200, r.text
    n = r.json()["note"]
    assert n["kind"] == "text" and n["title"] == "Budget review" and "Agenda:" in n["body"]
    assert n["state"]["labels"] == ["Work"]
    r = await client.post(f"{API}/notes/from-template", json={"template": "b-expenses"})
    assert r.status_code == 200, r.text
    t = r.json()["table"]
    assert [c["name"] for c in t["columns"]] == ["Date", "Item", "Category", "Amount", "Paid"]
    assert [c["type"] for c in t["columns"]] == ["date", "text", "select", "currency", "checkbox"]
    assert r.json()["undo"]["path"] == f"/tables/{t['id']}"
    assert (await client.post(f"{API}/notes/from-template", json={"template": "u999999"})).status_code == 404


async def test_note_save_template_keeps_sections_items_and_quantities(client):
    """note.save_template: a checklist's sections and items with quantities and extra lines, all
    unchecked; a text note's title and text. The undo deletes the template."""
    r = await client.post(f"{API}/notes", json={"kind": "checklist", "title": "Camping",
                                                "lines": "Kitchen:\n2 gas canisters\nstove\nTent:\npegs"})
    lst = r.json()["note"]
    stove = next(i for i in lst["items"] if i["text"] == "stove")
    await client.patch(f"{API}/notes/{lst['id']}/items/{stove['id']}", json={"checked": True})
    r = await client.post(f"{API}/notes/{lst['id']}/save-template", json={"name": "Camping trip"})
    assert r.status_code == 200, r.text
    tpl = r.json()["template"]
    assert tpl["id"].startswith("u") and tpl["kind"] == "checklist" and tpl["name"] == "Camping trip"
    assert tpl["summary"] == "2 sections, 3 items"
    r = await client.post(f"{API}/notes/from-template", json={"template": tpl["id"]})
    n = r.json()["note"]
    secs = {s["id"]: s["name"] for s in n["sections"]}
    got = {(i["text"], secs.get(i["section_id"]), (i["qty"] or {}).get("amount"), i["checked"]) for i in n["items"]}
    assert got == {("gas canisters", "Kitchen", "2", False), ("stove", "Kitchen", None, False),
                   ("pegs", "Tent", None, False)}
    # A text note; no name: the note's title names the template.
    txt = (await client.post(f"{API}/notes", json={"kind": "text", "title": "Weekly report", "body": "Done:\nNext:"})).json()["note"]
    r = await client.post(f"{API}/notes/{txt['id']}/save-template", json={})
    assert r.json()["template"]["name"] == "Weekly report"
    undo = r.json()["undo"]
    assert (await client.request(undo["method"], API + undo["path"])).status_code == 200
    assert "Weekly report" not in [t["name"] for t in await templates(client)]


async def test_table_save_template_keeps_columns_not_rows_or_links(client):
    """table.save_template: the columns in their order; rows stay behind; a link column is left out."""
    r = await client.post(f"{API}/tables", json={"name": "Reading", "columns": [
        {"name": "Title", "type": "text"}, {"name": "Pages", "type": "number"},
        {"name": "Status", "type": "select", "options": ["To read", "Done"]}], "rows": [{"Title": "A book"}]})
    t = r.json()["table"]
    r = await client.post(f"{API}/tables/{t['id']}/save-template", json={"name": "Reading log"})
    assert r.status_code == 200, r.text
    tpl = r.json()["template"]
    assert tpl["kind"] == "table" and [c["name"] for c in tpl["content"]["columns"]] == ["Title", "Pages", "Status"]
    r = await client.post(f"{API}/notes/from-template", json={"template": tpl["id"]})
    new = r.json()["table"]
    assert new["name"] == "Reading" and [c["type"] for c in new["columns"]] == ["text", "number", "select"]
    assert (await client.get(f"{API}/tables/{new['id']}/rows")).json()["rows"] == []


async def test_note_delete_template_and_its_undo(client):
    """note.delete_template: the user's own; the undo makes it again; built-ins cannot go."""
    r = await client.post(f"{API}/notes/templates", json={"kind": "text", "name": "Standup",
                                                          "content": {"title": "Standup", "body": "Yesterday:\nToday:"}})
    assert r.status_code == 200, r.text
    tid = r.json()["template"]["id"]
    r = await client.delete(f"{API}/notes/templates/{tid}")
    assert r.status_code == 200
    assert "Standup" not in [t["name"] for t in await templates(client)]
    undo = r.json()["undo"]
    r = await client.request(undo["method"], API + undo["path"], json=undo["body"])
    assert r.status_code == 200 and r.json()["template"]["name"] == "Standup"
    assert (await client.delete(f"{API}/notes/templates/b-shopping")).status_code == 403
    assert (await client.delete(f"{API}/notes/templates/u0")).status_code == 404
    r = await client.post(f"{API}/notes/templates", json={"kind": "table", "name": "x", "content": {"columns": []}})
    assert r.status_code == 422


async def test_templates_are_the_owners_own(client, app):
    """Another user does not see or use someone's template."""
    import uuid

    from httpx import ASGITransport, AsyncClient

    from mailr.core.users import create_user
    from mailr.db import sessionmaker

    r = await client.post(f"{API}/notes/templates", json={"kind": "text", "name": "Private", "content": {"body": "x"}})
    tid = r.json()["template"]["id"]
    async with sessionmaker()() as db:
        u = await create_user(db, f"olive-{uuid.uuid4().hex[:8]}@example.com", "Olive", password="olive-password-123")
        await db.commit()
        email = u.email
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(f"{API}/auth/login", json={"email": email, "password": "olive-password-123"})
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        assert "Private" not in [t["name"] for t in (await c.get(f"{API}/notes/templates")).json()["templates"]]
        assert (await c.post(f"{API}/notes/from-template", json={"template": tid})).status_code == 404
        assert (await c.delete(f"{API}/notes/templates/{tid}")).status_code == 404
