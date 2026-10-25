# ACT-SURFACES: api
"""Notes and checklists through the API: every
note.*, checkitem.*, checksection.* and notelabel.* action with its undo, the access
check, Trash, revisions, merges, duplicates, search and export."""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest

# These tests never touch the mail store: database-only users (conftest.make_db_user).
pytestmark = pytest.mark.db_user

API = "/api/v1"


async def new(client, **kw):
    r = await client.post(f"{API}/notes", json=kw)
    assert r.status_code == 200, r.text
    return r.json()["note"]


async def get(client, pid):
    r = await client.get(f"{API}/notes/{pid}")
    assert r.status_code == 200, r.text
    return r.json()


async def do_undo(client, undo):
    """Send an action's undo descriptor as the client does."""
    assert undo, "expected an undo"
    r = await client.request(undo["method"], API + undo["path"], json=undo["body"])
    assert r.status_code == 200, r.text
    return r.json()


def texts(note, checked=None):
    return [i["text"] for i in note["items"] if checked is None or i["checked"] == checked]


# ---------------------------------------------------------------- note.*


async def test_note_create_text_and_list(client):
    """note.create: a text note, a checklist from lines (the grammar), labels, colour; undo
    moves it to Trash."""
    n = await new(client, kind="text", title="Ideas", body="first line\nsecond", color="sage", labels=["Home"])
    assert n["id"].startswith("n") and n["kind"] == "text" and n["body"] == "first line\nsecond"
    assert n["color"] == "sage" and n["state"]["labels"] == ["Home"] and n["level"] == "owner"
    lst = await new(client, kind="checklist", title="Shopping", lines="2 milk, eggs\nCostco:\npaper towels")
    assert texts(lst) == ["milk", "eggs", "paper towels"]
    assert [s["name"] for s in lst["sections"]] == ["Costco"]
    milk = lst["items"][0]
    assert milk["qty"]["amount"] == "2" and milk["key"] == "milk"
    r = await client.post(f"{API}/notes", json={"kind": "text", "title": "x", "color": "neon"})
    assert r.status_code == 422
    r = await client.get(f"{API}/notes")
    ids = [c["id"] for c in r.json()["notes"]]
    assert lst["id"] in ids and n["id"] in ids
    assert any(lb["name"] == "Home" and lb["count"] == 1 for lb in r.json()["labels"])


async def test_new_list_prefix_makes_section(client):
    """note.create: in a new list, "Costco: 2 milk" makes the Costco section and keeps the quantity,
    as the composer's hint promises. The add line of an existing list keeps an unknown prefix
    as text."""
    lst = await new(client, kind="checklist", title="Groceries", lines="Costco: 2 milk, eggs, towels\nbread")
    assert [s["name"] for s in lst["sections"]] == ["Costco"]
    costco = lst["sections"][0]["id"]
    by = {i["text"]: i for i in lst["items"]}
    assert set(by) == {"milk", "eggs", "towels", "bread"}
    assert by["milk"]["qty"]["amount"] == "2"
    assert all(by[t]["section_id"] == costco for t in ("milk", "eggs", "towels"))
    assert by["bread"]["section_id"] != costco
    r = await client.post(f"{API}/notes/{lst['id']}/items", json={"lines": "Note: ripe ones"})
    assert r.status_code == 200, r.text
    got = await get(client, lst["id"])
    assert "Note: ripe ones" in texts(got["note"] if "note" in got else got)


async def test_note_create_undo_goes_to_trash(client):
    r = await client.post(f"{API}/notes", json={"kind": "text", "title": "Oops"})
    undo = r.json()["undo"]
    assert undo["method"] == "DELETE"
    await do_undo(client, undo)
    r = await client.get(f"{API}/notes", params={"view": "trash"})
    assert [c["title"] for c in r.json()["notes"]] == ["Oops"]


async def test_note_edit_and_color_with_undo(client):
    """note.edit and note.color: changed fields only; the undo puts the old values back."""
    n = await new(client, kind="text", title="Plan", body="a\nb")
    r = await client.patch(f"{API}/notes/{n['id']}", json={"title": "Plan B", "base_version": n["version"]})
    assert r.status_code == 200, r.text
    assert r.json()["note"]["title"] == "Plan B"
    await do_undo(client, r.json()["undo"])
    assert (await get(client, n["id"]))["title"] == "Plan"
    r = await client.patch(f"{API}/notes/{n['id']}", json={"color": "tomato"})
    assert r.json()["note"]["color"] == "tomato" and r.json()["undo"]["label"] == "Colour changed"
    await do_undo(client, r.json()["undo"])
    assert (await get(client, n["id"]))["color"] is None


async def test_note_edit_merges_body_and_refuses_title_clash(client):
    """Live editing: two writers from one base version. Body lines merge; the title changed both
    ways is a 409 with both copies; the colour takes the later write."""
    n = await new(client, kind="text", title="T", body="one\ntwo\nthree\n")
    v0 = n["version"]
    r = await client.patch(f"{API}/notes/{n['id']}", json={"body": "ONE\ntwo\nthree\n", "base_version": v0})
    assert r.status_code == 200
    r = await client.patch(f"{API}/notes/{n['id']}", json={"body": "one\ntwo\nTHREE\n", "base_version": v0})
    assert r.status_code == 200 and r.json()["merged"] is True
    assert r.json()["note"]["body"] == "ONE\ntwo\nTHREE\n"
    v1 = r.json()["note"]["version"]
    assert (await client.patch(f"{API}/notes/{n['id']}", json={"title": "Mine", "base_version": v1})).status_code == 200
    r = await client.patch(f"{API}/notes/{n['id']}", json={"title": "Theirs", "base_version": v1})
    assert r.status_code == 409 and r.json()["fields"] == ["title"]
    assert (await client.patch(f"{API}/notes/{n['id']}", json={"color": "sage", "base_version": v1})).status_code == 200
    r = await client.patch(f"{API}/notes/{n['id']}", json={"color": "grape", "base_version": v1})
    assert r.status_code == 200 and r.json()["note"]["color"] == "grape"


async def test_note_convert_both_ways_keeps_items(client):
    """note.to_checklist and note.to_text: lines become items and `Name:` sections; back to text
    gives `- [ ]` lines; the undo brings the same items back, ids and all."""
    n = await new(client, kind="text", title="Pack", body="Clothes:\nsocks x3\nshirts\n## Toiletries\n- [x] toothbrush")
    r = await client.post(f"{API}/notes/{n['id']}/convert", json={"to": "checklist", "base_version": n["version"]})
    assert r.status_code == 200, r.text
    lst = r.json()["note"]
    assert lst["kind"] == "checklist" and [s["name"] for s in lst["sections"]] == ["Clothes", "Toiletries"]
    assert texts(lst, checked=False) == ["socks", "shirts"] and texts(lst, checked=True) == ["toothbrush"]
    ids = {i["id"] for i in lst["items"]}
    r = await client.post(f"{API}/notes/{n['id']}/convert", json={"to": "text", "base_version": lst["version"]})
    txt = r.json()["note"]
    assert txt["kind"] == "text"
    assert txt["body"] == "Clothes:\n- [ ] socks ×3\n- [ ] shirts\nToiletries:\n- [x] toothbrush"
    back = (await do_undo(client, r.json()["undo"]))["note"]
    assert back["kind"] == "checklist" and {i["id"] for i in back["items"]} == ids
    # An old base version: 409, the client offers "Try again".
    r = await client.post(f"{API}/notes/{n['id']}/convert", json={"to": "text", "base_version": n["version"]})
    assert r.status_code == 409
    # Undo of to_checklist: the same text again.
    t2 = await new(client, kind="text", title="Two", body="a, b")
    r = await client.post(f"{API}/notes/{t2['id']}/convert", json={"to": "checklist"})
    assert texts(r.json()["note"]) == ["a", "b"]
    back = (await do_undo(client, r.json()["undo"]))["note"]
    assert back["kind"] == "text" and back["body"] == "a, b"


async def test_note_pin_archive_label_reorder(client):
    """note.pin, note.archive, note.label, note.reorder: the viewer's own state, each with undo."""
    a = await new(client, kind="text", title="A")
    b = await new(client, kind="text", title="B")
    c = await new(client, kind="text", title="C")
    r = await client.put(f"{API}/notes/{a['id']}/state", json={"pinned": True})
    assert r.json()["state"]["pinned"] is True
    grid = (await client.get(f"{API}/notes")).json()["notes"]
    assert grid[0]["id"] == a["id"]
    await do_undo(client, r.json()["undo"])
    r = await client.put(f"{API}/notes/{b['id']}/state", json={"archived": True})
    grid = [x["id"] for x in (await client.get(f"{API}/notes")).json()["notes"]]
    assert b["id"] not in grid
    assert [x["id"] for x in (await client.get(f"{API}/notes", params={"view": "archive"})).json()["notes"]] == [b["id"]]
    # Pinning an archived note unarchives it.
    r = await client.put(f"{API}/notes/{b['id']}/state", json={"pinned": True})
    assert r.json()["state"] == {**r.json()["state"], "pinned": True, "archived": False}
    r = await client.put(f"{API}/notes/{c['id']}/state", json={"add_labels": ["Work", "home"]})
    assert r.json()["state"]["labels"] == ["Work", "home"] and set(r.json()["created_labels"]) == {"Work", "home"}
    await do_undo(client, r.json()["undo"])
    assert (await get(client, c["id"]))["state"]["labels"] == []
    # Manual order: newest first, then a move.
    await client.put(f"{API}/notes/{b['id']}/state", json={"pinned": False})
    order0 = [x["title"] for x in (await client.get(f"{API}/notes")).json()["notes"]]
    assert order0 == ["C", "B", "A"]
    r = await client.put(f"{API}/notes/{c['id']}/state", json={"after": a["id"]})
    assert r.status_code == 200, r.text
    assert [x["title"] for x in (await client.get(f"{API}/notes")).json()["notes"]] == ["B", "A", "C"]
    await do_undo(client, r.json()["undo"])
    assert [x["title"] for x in (await client.get(f"{API}/notes")).json()["notes"]] == ["C", "B", "A"]


async def test_note_duplicate(client):
    """note.duplicate: same kind, items, sections, colour and labels; new ids."""
    lst = await new(client, kind="checklist", title="Trip", lines="Clothes:\nsocks\n- [x] hat", color="sage",
                    labels=["Travel"])
    r = await client.post(f"{API}/notes/{lst['id']}/duplicate")
    cp = r.json()["note"]
    assert cp["id"] != lst["id"] and cp["title"] == "Trip" and cp["color"] == "sage"
    assert texts(cp) == texts(lst) and cp["state"]["labels"] == ["Travel"]
    assert not {i["id"] for i in cp["items"]} & {i["id"] for i in lst["items"]}
    await do_undo(client, r.json()["undo"])
    assert (await get(client, cp["id"]))["deleted_at"] is not None


async def test_note_trash_restore_purge(client):
    """note.delete, note.restore, note.purge: Trash for 30 days; Delete forever has no undo."""
    n = await new(client, kind="checklist", title="Old", lines="x, y")
    r = await client.delete(f"{API}/notes/{n['id']}")
    assert r.json()["undo"]["path"].endswith("/restore")
    t = (await client.get(f"{API}/notes", params={"view": "trash"})).json()["notes"]
    assert t[0]["id"] == n["id"] and t[0]["purge_at"]
    r = await client.patch(f"{API}/notes/{n['id']}", json={"title": "x"})
    assert r.status_code == 409  # read-only in Trash
    r = await client.post(f"{API}/notes/{n['id']}/restore")
    assert texts(r.json()["note"]) == ["x", "y"]
    assert (await client.delete(f"{API}/notes/{n['id']}", params={"forever": "true"})).status_code == 409
    await client.delete(f"{API}/notes/{n['id']}")
    r = await client.delete(f"{API}/notes/{n['id']}", params={"forever": "true"})
    assert r.status_code == 200 and r.json()["undo"] is None
    assert (await client.get(f"{API}/notes/{n['id']}")).status_code == 404
    a = await new(client, kind="text", title="1")
    b = await new(client, kind="text", title="2")
    await client.delete(f"{API}/notes/{a['id']}")
    await client.delete(f"{API}/notes/{b['id']}")
    r = await client.post(f"{API}/notes/trash/empty")
    assert r.json()["purged"] == 2


async def test_trash_purge_after_30_days(client):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from mailr.db import sessionmaker
    from mailr.notes.models import Note
    from mailr.notes.tasks import prune

    n = await new(client, kind="text", title="Gone soon")
    await client.delete(f"{API}/notes/{n['id']}")
    async with sessionmaker()() as db:
        await db.execute(update(Note).where(Note.pid == n["id"]).values(
            deleted_at=datetime.now(UTC) - timedelta(days=31)))
        await prune(db)
        await db.commit()
    assert (await client.get(f"{API}/notes/{n['id']}")).status_code == 404


async def test_access_other_user_gets_404(client, app):
    """The access check: another user's note is a 404 on every route, and never listed."""
    from httpx import ASGITransport, AsyncClient

    from mailr.core.users import create_user
    from mailr.db import sessionmaker

    n = await new(client, kind="checklist", title="Mine", lines="milk")
    item = n["items"][0]["id"]
    async with sessionmaker()() as db:
        other = await create_user(db, f"o-{uuid.uuid4().hex[:8]}@example.com", "Other", password="test-password-123")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c2:
        r = await c2.post(f"{API}/auth/login", json={"email": other.email, "password": "test-password-123"})
        c2.headers["X-CSRF-Token"] = r.json()["csrf"]
        for method, path, body in (("GET", f"/notes/{n['id']}", None), ("PATCH", f"/notes/{n['id']}", {"title": "x"}),
                                   ("DELETE", f"/notes/{n['id']}", None),
                                   ("PATCH", f"/notes/{n['id']}/items/{item}", {"checked": True}),
                                   ("POST", f"/notes/{n['id']}/items", {"lines": "eggs"}),
                                   ("PUT", f"/notes/{n['id']}/state", {"pinned": True}),
                                   ("GET", f"/notes/{n['id']}/revisions", None)):
            r = await c2.request(method, API + path, json=body)
            assert r.status_code == 404, (method, path, r.text)
        assert (await c2.get(f"{API}/notes")).json()["notes"] == []
        assert (await c2.get(f"{API}/notes/search", params={"q": "milk"})).json()["notes"] == []


async def test_access_level_function():
    """notes/access.level: owner for the owner, edit for a member of the owning group, none
    otherwise; deleting a group note is for its creator or an admin."""
    from types import SimpleNamespace

    from mailr.notes import access

    owner = SimpleNamespace(id=1, role="user", idp_groups=None, assigned_groups=[], idp_groups_source=None,
                            idp_groups_at=None)
    member = SimpleNamespace(id=2, role="user", idp_groups=None, assigned_groups=["Household"],
                             idp_groups_source=None, idp_groups_at=None)
    admin = SimpleNamespace(id=3, role="admin", idp_groups=None, assigned_groups=[], idp_groups_source=None,
                            idp_groups_at=None)
    mine = SimpleNamespace(owner_user_id=1, owner_group=None, created_by=1)
    group = SimpleNamespace(owner_user_id=None, owner_group="Household", created_by=1)
    assert access.level(owner, mine) == "owner" and access.level(member, mine) == "none"
    assert access.level(member, group) == "edit" and access.level(admin, group) == "none"
    assert access.can_delete(owner, mine) and not access.can_delete(member, mine)
    assert not access.can_delete(member, group) and access.can_delete(admin, group)


async def test_tokens_are_refused(client, user):
    from mailr.core.auth import create_token
    from mailr.db import sessionmaker

    async with sessionmaker()() as db:
        raw, _ = await create_token(db, user, "agent", ["read", "calendar"])
        await db.commit()
    r = await client.get(f"{API}/notes", headers={"Authorization": f"Bearer {raw}", "X-CSRF-Token": ""})
    assert r.status_code == 403


# ---------------------------------------------------------------- checkitem.*


async def test_checkitem_add_check_uncheck_edit_delete(client):
    lst = await new(client, kind="checklist", title="Shop")
    pid = lst["id"]
    cid = str(uuid.uuid4())
    r = await client.post(f"{API}/notes/{pid}/items", json={"items": [{"id": cid, "text": "bread"}]})
    assert r.status_code == 200 and r.json()["items"][0]["id"] == cid
    # The same client id again adds nothing (an offline replay).
    r = await client.post(f"{API}/notes/{pid}/items", json={"items": [{"id": cid, "text": "bread"}]})
    assert len(texts(await get(client, pid))) == 1
    # checkitem.check, with the device time; the undo unchecks.
    r = await client.patch(f"{API}/notes/{pid}/items/{cid}", json={"checked": True,
                                                                   "device_at": "2026-10-05T10:00:00+00:00"})
    assert r.json()["item"]["checked"] is True
    await do_undo(client, r.json()["undo"])
    assert (await get(client, pid))["items"][0]["checked"] is False
    # checkitem.uncheck with undo.
    await client.patch(f"{API}/notes/{pid}/items/{cid}", json={"checked": True})
    r = await client.patch(f"{API}/notes/{pid}/items/{cid}", json={"checked": False})
    assert r.json()["item"]["checked"] is False
    await do_undo(client, r.json()["undo"])
    assert (await get(client, pid))["items"][0]["checked"] is True
    # checkitem.edit: the grammar in edit mode turns `x2` into the quantity.
    r = await client.patch(f"{API}/notes/{pid}/items/{cid}", json={"text": "sourdough, sliced x2", "parse": True})
    it = r.json()["item"]
    assert it["text"] == "sourdough, sliced" and it["qty"]["amount"] == "2"
    await do_undo(client, r.json()["undo"])
    it = (await get(client, pid))["items"][0]
    assert it["text"] == "bread" and it["qty"] is None
    # checkitem.delete with restore.
    r = await client.delete(f"{API}/notes/{pid}/items/{cid}")
    assert texts(await get(client, pid)) == []
    await do_undo(client, r.json()["undo"])
    assert texts(await get(client, pid)) == ["bread"]


async def test_checkitem_check_later_tap_wins(client):
    lst = await new(client, kind="checklist", title="Shop", lines="milk")
    pid, iid = lst["id"], lst["items"][0]["id"]
    await client.patch(f"{API}/notes/{pid}/items/{iid}", json={"checked": True, "device_at": "2026-10-05T10:05:00+00:00"})
    # An offline uncheck tapped earlier arrives later: it loses.
    r = await client.patch(f"{API}/notes/{pid}/items/{iid}", json={"checked": False,
                                                                   "device_at": "2026-10-05T10:01:00+00:00"})
    assert r.json()["ignored"] is True and r.json()["item"]["checked"] is True


async def test_checkitem_edit_conflict_per_field(client):
    """Items merge field by field: text and quantity changed by two writers merge; the same field
    changed two ways is a 409 on that item."""
    lst = await new(client, kind="checklist", title="Shop", lines="milk")
    pid, it = lst["id"], lst["items"][0]
    v = it["version"]
    r = await client.patch(f"{API}/notes/{pid}/items/{it['id']}", json={"text": "oat milk", "base_version": v})
    assert r.status_code == 200
    r = await client.patch(f"{API}/notes/{pid}/items/{it['id']}", json={"qty": {"amount": "2", "unit": None},
                                                                       "base_version": v})
    assert r.status_code == 200, r.text
    got = r.json()["item"]
    assert got["text"] == "oat milk" and got["qty"]["amount"] == "2"
    r = await client.patch(f"{API}/notes/{pid}/items/{it['id']}", json={"text": "soy milk", "base_version": v})
    assert r.status_code == 409 and r.json()["kind"] == "checkitem" and r.json()["fields"] == ["text"]
    # An edit of a deleted item: "deleted" (the client offers Restore).
    await client.delete(f"{API}/notes/{pid}/items/{it['id']}")
    r = await client.patch(f"{API}/notes/{pid}/items/{it['id']}", json={"text": "x"})
    assert r.status_code == 409 and r.json()["detail"]["deleted"] is True


async def test_duplicates_merge(client):
    """Adding an item already on the list unchecked merges quantities of the same unit; another
    unit is reported (Add anyway = force); a checked one is unchecked with the new quantity."""
    lst = await new(client, kind="checklist", title="Shop", lines="milk\n500 g flour\nCostco:\n- [x] eggs")
    pid = lst["id"]
    r = await client.post(f"{API}/notes/{pid}/items", json={"lines": "2 milk"})
    assert r.json()["merged"][0]["qty"]["amount"] == "3"
    r = await client.post(f"{API}/notes/{pid}/items", json={"lines": "1 kg flour"})
    assert r.json()["duplicates"] == [{"text": "flour", "qty": {"amount": "1", "unit": "kg", "text": "1 kg"},
                                       "section": None, "section_id": None}]
    r = await client.post(f"{API}/notes/{pid}/items", json={"lines": "Eggs x12"})
    assert r.json()["rechecked"]
    n = await get(client, pid)
    eggs = next(i for i in n["items"] if i["key"] == "egg")
    assert eggs["checked"] is False and eggs["qty"]["amount"] == "12" and eggs["section_id"] == n["sections"][0]["id"]
    assert texts(n).count("milk") == 1
    # The undo of a merge puts the quantity back.
    r = await client.post(f"{API}/notes/{pid}/items", json={"lines": "milk"})
    await do_undo(client, r.json()["undo"])
    milk = next(i for i in (await get(client, pid))["items"] if i["key"] == "milk")
    assert milk["qty"]["amount"] == "3"
    r = await client.post(f"{API}/notes/{pid}/items", json={"lines": "1 kg flour", "force": True})
    assert texts(await get(client, pid)).count("flour") == 2


async def test_add_items_paste_with_sections_one_undo(client):
    """note.add_items: a pasted block with a new header section; one undo removes the batch and
    the section it made."""
    lst = await new(client, kind="checklist", title="Shop", lines="Costco:\nmilk")
    pid = lst["id"]
    r = await client.post(f"{API}/notes/{pid}/items",
                          json={"lines": "Farmers market:\napples\npears\ncostco: bread, butter"})
    body = r.json()
    assert [s["name"] for s in body["new_sections"]] == ["Farmers market"]
    n = await get(client, pid)
    sec = {s["name"]: s["id"] for s in n["sections"]}
    by = {i["text"]: i["section_id"] for i in n["items"]}
    assert by["apples"] == sec["Farmers market"] and by["bread"] == sec["Costco"]
    await do_undo(client, body["undo"])
    n = await get(client, pid)
    assert texts(n) == ["milk"] and [s["name"] for s in n["sections"]] == ["Costco"]


async def test_add_after_item_and_section_default(client):
    lst = await new(client, kind="checklist", title="Shop", lines="a\nc\nCostco:\nx")
    pid = lst["id"]
    a = lst["items"][0]["id"]
    costco = lst["sections"][0]["id"]
    await client.post(f"{API}/notes/{pid}/items", json={"lines": "b", "after": a})
    await client.post(f"{API}/notes/{pid}/items", json={"lines": "y", "section_id": costco})
    n = await get(client, pid)
    assert texts(n) == ["a", "b", "c", "x", "y"]


async def test_checkitem_move_and_keys(client):
    """checkitem.move: before an item, a step down, to the next section, to the top group."""
    lst = await new(client, kind="checklist", title="Shop", lines="a\nb\nc\nCostco:\nx")
    pid = lst["id"]
    ids = {i["text"]: i["id"] for i in lst["items"]}
    r = await client.post(f"{API}/notes/{pid}/items/{ids['c']}/move", json={"before": ids["a"]})
    assert texts(await get(client, pid)) == ["c", "a", "b", "x"]
    await do_undo(client, r.json()["undo"])
    assert texts(await get(client, pid)) == ["a", "b", "c", "x"]
    await client.post(f"{API}/notes/{pid}/items/{ids['a']}/move", json={"direction": "down"})
    assert texts(await get(client, pid)) == ["b", "a", "c", "x"]
    await client.post(f"{API}/notes/{pid}/items/{ids['a']}/move", json={"direction": "next_section"})
    n = await get(client, pid)
    assert texts(n) == ["b", "c", "x", "a"] and n["items"][-1]["section_id"] == lst["sections"][0]["id"]
    await client.post(f"{API}/notes/{pid}/items/{ids['x']}/move", json={"section_id": None})
    assert texts(await get(client, pid)) == ["b", "c", "x", "a"]


async def test_clear_checked_and_uncheck_all(client):
    """note.uncheck_all ("Start over") and note.clear_checked, each with one undo for the batch."""
    lst = await new(client, kind="checklist", title="Pack", lines="- [x] a\n- [x] b\nc")
    pid = lst["id"]
    r = await client.post(f"{API}/notes/{pid}/items/uncheck-all")
    assert r.json()["count"] == 2 and texts(await get(client, pid), checked=True) == []
    await do_undo(client, r.json()["undo"])
    assert sorted(texts(await get(client, pid), checked=True)) == ["a", "b"]
    r = await client.post(f"{API}/notes/{pid}/items/clear-checked")
    assert r.json()["count"] == 2 and texts(await get(client, pid)) == ["c"]
    await do_undo(client, r.json()["undo"])
    assert sorted(texts(await get(client, pid))) == ["a", "b", "c"]


async def test_limits(client):
    lst = await new(client, kind="checklist", title="Big")
    r = await client.post(f"{API}/notes/{lst['id']}/items",
                          json={"items": [{"text": f"item {i}"} for i in range(1000)]})
    assert r.status_code == 200
    r = await client.post(f"{API}/notes/{lst['id']}/items", json={"lines": "one more"})
    assert r.status_code == 422 and "1,000 items" in r.json()["detail"]
    r = await client.post(f"{API}/notes", json={"kind": "text", "body": "x" * 20_001})
    assert r.status_code == 422


# ---------------------------------------------------------------- checksection.*


async def test_sections_create_rename_reorder_mode_delete(client):
    """checksection.create, checksection.rename, checksection.reorder, checksection.order_mode,
    checksection.delete."""
    lst = await new(client, kind="checklist", title="Shop", lines="milk")
    pid = lst["id"]
    r = await client.post(f"{API}/notes/{pid}/sections", json={"name": "Costco"})
    costco = r.json()["section"]
    assert (await client.post(f"{API}/notes/{pid}/sections", json={"name": "costco"})).status_code == 409
    r = await client.post(f"{API}/notes/{pid}/sections", json={"name": "Spice shop"})
    spice = r.json()["section"]
    # checksection.rename with a stale base name: conflict.
    r = await client.patch(f"{API}/notes/{pid}/sections/{costco['id']}", json={"name": "Costco Wholesale",
                                                                              "base_name": "Costco"})
    assert r.status_code == 200
    await do_undo(client, r.json()["undo"])
    await client.patch(f"{API}/notes/{pid}/sections/{costco['id']}", json={"name": "Big box", "base_name": "Costco"})
    r = await client.patch(f"{API}/notes/{pid}/sections/{costco['id']}", json={"name": "Warehouse",
                                                                              "base_name": "Costco"})
    assert r.status_code == 409
    # checksection.reorder
    r = await client.patch(f"{API}/notes/{pid}/sections/{spice['id']}", json={"direction": "up"})
    assert [s["name"] for s in (await get(client, pid))["sections"]] == ["Spice shop", "Big box"]
    await do_undo(client, r.json()["undo"])
    assert [s["name"] for s in (await get(client, pid))["sections"]] == ["Big box", "Spice shop"]
    # checksection.order_mode
    r = await client.patch(f"{API}/notes/{pid}/sections/{spice['id']}", json={"order_mode": "learned"})
    assert r.json()["section"]["order_mode"] == "learned"
    await do_undo(client, r.json()["undo"])
    # collapsed is per viewer
    r = await client.patch(f"{API}/notes/{pid}/sections/{spice['id']}", json={"collapsed": True})
    assert r.json()["section"]["collapsed"] is True
    # checksection.delete: keep the items (to the top group), undo puts them back.
    await client.post(f"{API}/notes/{pid}/items", json={"lines": "cumin", "section_id": spice["id"]})
    r = await client.delete(f"{API}/notes/{pid}/sections/{spice['id']}")
    n = await get(client, pid)
    assert [s["name"] for s in n["sections"]] == ["Big box"] and all(i["section_id"] is None for i in n["items"])
    await do_undo(client, r.json()["undo"])
    n = await get(client, pid)
    assert next(i for i in n["items"] if i["text"] == "cumin")["section_id"] == spice["id"]
    r = await client.delete(f"{API}/notes/{pid}/sections/{spice['id']}", params={"items": "delete"})
    assert texts(await get(client, pid)) == ["milk"]
    await do_undo(client, r.json()["undo"])
    assert sorted(texts(await get(client, pid))) == ["cumin", "milk"]


# ---------------------------------------------------------------- notelabel.*


async def test_labels_create_rename_color_reorder_delete(client):
    """notelabel.create, notelabel.rename, notelabel.color, notelabel.reorder, notelabel.delete."""
    r = await client.post(f"{API}/notes/labels", json={"name": "Home", "color": "sage"})
    assert r.json()["label"]["name"] == "Home"
    assert (await client.post(f"{API}/notes/labels", json={"name": "home"})).status_code == 409
    await client.post(f"{API}/notes/labels", json={"name": "Work"})
    n = await new(client, kind="text", title="x", labels=["Home"])
    r = await client.patch(f"{API}/notes/labels/Home", json={"name": "House"})
    assert (await get(client, n["id"]))["state"]["labels"] == ["House"]
    await do_undo(client, r.json()["undo"])
    assert (await get(client, n["id"]))["state"]["labels"] == ["Home"]
    r = await client.patch(f"{API}/notes/labels/Home", json={"color": "grape"})
    assert r.json()["label"]["color"] == "grape"
    r = await client.patch(f"{API}/notes/labels/Work", json={"before": "Home"})
    assert [lb["name"] for lb in (await client.get(f"{API}/notes/labels")).json()["labels"]] == ["Work", "Home"]
    r = await client.delete(f"{API}/notes/labels/Home")
    assert (await get(client, n["id"]))["state"]["labels"] == []
    await do_undo(client, r.json()["undo"])
    assert (await get(client, n["id"]))["state"]["labels"] == ["Home"]
    r = await client.get(f"{API}/notes", params={"view": "label", "label": "home"})
    assert [c["id"] for c in r.json()["notes"]] == [n["id"]]


# ---------------------------------------------------------------- revisions


async def test_revisions_session_and_restore(client):
    """note.history and note.restore_version: edits by one author within 2 minutes share a
    revision; a restore is a revision of its own and its undo goes back."""
    n = await new(client, kind="text", title="Draft", body="v1")
    pid = n["id"]
    for b in ("v2", "v3"):
        await client.patch(f"{API}/notes/{pid}", json={"body": b})
    revs = (await client.get(f"{API}/notes/{pid}/revisions")).json()["revisions"]
    assert len(revs) == 1 and revs[0]["source"] == "edit"
    await client.post(f"{API}/notes/{pid}/revisions/checkpoint")
    await client.patch(f"{API}/notes/{pid}", json={"body": "v4"})
    revs = (await client.get(f"{API}/notes/{pid}/revisions")).json()["revisions"]
    assert [r["rev"] for r in revs] == [2, 1] and revs[0]["summary"] == "Body: 1 line changed"
    one = (await client.get(f"{API}/notes/{pid}/revisions/1")).json()
    assert one["content"]["body"] == "v3"
    r = await client.post(f"{API}/notes/{pid}/revisions/1/restore")
    assert r.json()["note"]["body"] == "v3"
    revs = (await client.get(f"{API}/notes/{pid}/revisions")).json()["revisions"]
    assert revs[0]["source"] == "restore"
    await do_undo(client, r.json()["undo"])
    assert (await get(client, pid))["body"] == "v4"


async def test_revision_restores_deleted_items(client):
    lst = await new(client, kind="checklist", title="Shop", lines="milk, eggs")
    pid = lst["id"]
    await client.post(f"{API}/notes/{pid}/revisions/checkpoint")
    eggs = lst["items"][1]["id"]
    await client.delete(f"{API}/notes/{pid}/items/{eggs}")
    revs = (await client.get(f"{API}/notes/{pid}/revisions")).json()["revisions"]
    assert revs[0]["summary"] == "−1 item"
    await client.post(f"{API}/notes/{pid}/revisions/1/restore")
    n = await get(client, pid)
    assert texts(n) == ["milk", "eggs"] and n["items"][1]["id"] == eggs


async def test_revision_retention(client):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select, update

    from mailr.db import sessionmaker
    from mailr.notes.models import Note, NoteRevision
    from mailr.notes.tasks import prune

    n = await new(client, kind="text", title="Old", body="a")
    await client.post(f"{API}/notes/{n['id']}/revisions/checkpoint")
    await client.patch(f"{API}/notes/{n['id']}", json={"body": "b"})
    async with sessionmaker()() as db:
        nid = (await db.execute(select(Note.id).where(Note.pid == n["id"]))).scalar_one()
        await db.execute(update(NoteRevision).where(NoteRevision.note_id == nid).values(
            at=datetime.now(UTC) - timedelta(days=40)))
        await prune(db)
        await db.commit()
        left = (await db.execute(select(func.count()).where(NoteRevision.note_id == nid))).scalar_one()
    assert left == 1  # the newest is always kept


# ---------------------------------------------------------------- search, export, events


async def test_search_operators(client):
    """note.search: full text and the operators."""
    a = await new(client, kind="checklist", title="Groceries", lines="Costco:\noat milk\n- [x] eggs", color="sage",
                  labels=["Home"])
    b = await new(client, kind="text", title="Milk thoughts", body="cold brew")
    async def ids(q):
        return [c["id"] for c in (await client.get(f"{API}/notes/search", params={"q": q})).json()["notes"]]
    assert set(await ids("milk")) == {a["id"], b["id"]}
    assert await ids("milk is:list") == [a["id"]]
    assert await ids("milk is:text") == [b["id"]]
    assert await ids("label:home") == [a["id"]]
    assert await ids("color:sage") == [a["id"]]
    assert await ids("section:cost") == [a["id"]]
    assert await ids("eggs is:checked") == [a["id"]]
    assert await ids("eggs is:unchecked") == []
    assert await ids("is:table") == [] and await ids("is:shared") == []
    assert set(await ids("by:me")) == {a["id"], b["id"]} and await ids("by:nobody-at-all") == []
    r = (await client.get(f"{API}/notes/search", params={"q": "oat"})).json()["notes"][0]
    assert r["matches"][0]["text"] == "oat milk"
    await client.delete(f"{API}/notes/{b['id']}")
    assert await ids("brew") == [] and await ids("brew in:trash") == [b["id"]]


async def test_export_markdown_parses_back(client):
    """note.export: Markdown whose `- [ ]` lines parse back to the same items; the zip of all."""
    from mailr.notes import itemadd

    lst = await new(client, kind="checklist", title="Shop", lines="2 milk\nCostco:\n500 g flour\n- [x] eggs")
    r = await client.get(f"{API}/notes/{lst['id']}/export")
    md = r.text
    assert md == "# Shop\n\n- [ ] milk ×2\n\n## Costco\n\n- [ ] 500 g flour\n- [x] eggs\n"
    body = md.split("\n", 2)[2]
    got = itemadd.parse(body, "list", sections=[])
    assert [(i.text, i.section, i.checked, i.qty and i.qty["amount"]) for i in got.items] == [
        ("milk", None, False, "2"), ("flour", "Costco", False, "500"), ("eggs", "Costco", True, None)]
    r = await client.get(f"{API}/notes/export")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "Shop.md" in z.namelist() and "notes.json" in z.namelist()


async def test_change_events_name_the_note(client, user):
    """Writes send {"type": "item", "kind": "checkitem", "id", "note", ...} to the viewers."""
    from unittest import mock

    sent = []

    async def fake(db, uid, ev):
        sent.append((uid, ev))

    with mock.patch("mailr.core.events.notify", fake):
        lst = await new(client, kind="checklist", title="Shop", lines="milk")
        await client.patch(f"{API}/notes/{lst['id']}/items/{lst['items'][0]['id']}", json={"checked": True})
    kinds = {(e["kind"], e.get("note")) for uid, e in sent if e.get("type") == "item"}
    assert ("checkitem", lst["id"]) in kinds and ("note", lst["id"]) in kinds
    assert all(uid == user.id for uid, _ in sent)


async def test_capture_line(client):
    """note.add_items in capture mode: `#List` picks the list, an unknown one is made, and an item
    goes to the section its key was last in."""
    lst = await new(client, kind="checklist", title="Shopping", lines="Costco:\nmilk")
    milk = lst["items"][0]["id"]
    await client.patch(f"{API}/notes/{lst['id']}/items/{milk}", json={"checked": True})
    await client.post(f"{API}/notes/{lst['id']}/items/clear-checked")
    r = await client.post(f"{API}/notes/capture", json={"text": "#shop milk, bread"})
    assert r.status_code == 200, r.text
    n = await get(client, lst["id"])
    by = {i["text"]: i["section_id"] for i in n["items"]}
    assert by["milk"] == n["sections"][0]["id"] and by["bread"] is None
    r = await client.post(f"{API}/notes/capture", json={"text": "#Hardware nails"})
    assert r.json()["created"] is True and r.json()["note"]["title"] == "Hardware"
    r = await client.post(f"{API}/notes/capture", json={"text": "eggs", "default_list": lst["id"]})
    assert "eggs" in texts(await get(client, lst["id"]))
    assert (await client.post(f"{API}/notes/capture", json={"text": "eggs"})).status_code == 422


async def test_links_and_from_email(client, user):
    """note.link, note.unlink (the undo) and note.from_email."""
    from mailr.db import sessionmaker
    from mailr.models import Conversation

    async with sessionmaker()() as db:
        conv = Conversation(user_id=user.id, subject="Q4 plan", norm_subject="q4 plan")
        db.add(conv)
        await db.commit()
        cid = conv.id
    n = await new(client, kind="text", title="Notes")
    r = await client.post(f"{API}/notes/{n['id']}/links", json={"target_kind": "conversation",
                                                                 "target_ref": {"conversation_id": cid}})
    assert r.status_code == 200, r.text
    assert r.json()["link"]["title"] == "Q4 plan"
    r2 = await client.get(f"{API}/notes/linked", params={"target_kind": "conversation", "conversation_id": cid})
    assert [x["id"] for x in r2.json()["notes"]] == [n["id"]]
    u = await do_undo(client, r.json()["undo"])
    assert u["ok"] and (await get(client, n["id"]))["links"] == []
    r = await client.post(f"{API}/notes/from-email", json={"conversation_id": cid, "selection": "- bread\n- jam",
                                                            "kind": "checklist"})
    m = r.json()["note"]
    assert m["title"] == "Q4 plan" and texts(m) == ["bread", "jam"] and m["links"][0]["target_kind"] == "conversation"


@pytest.mark.parametrize("bad", [{"target_kind": "web", "target_ref": {}},
                                 {"target_kind": "conversation", "target_ref": {"conversation_id": 999999999}}])
async def test_link_refuses_unknown_targets(client, bad):
    n = await new(client, kind="text", title="x")
    r = await client.post(f"{API}/notes/{n['id']}/links", json=bad)
    assert r.status_code in (404, 422)


async def test_checkitem_to_task_and_undo(client):
    """checkitem.to_task: a task titled with the item and its quantity, linked back; the item
    shows the task; the undo deletes the task."""
    from dev_services import radicale_up as _radicale_up
    from helpers.tasks_v2 import _connect

    if not _radicale_up():
        pytest.skip("Radicale dev server not running")
    await _connect(client, "Personal")
    lst = await new(client, kind="checklist", title="Shop [weekly]", lines="2 milk")
    pid, it = lst["id"], lst["items"][0]
    r = await client.post(f"{API}/notes/{pid}/items/{it['id']}/to-task", json={})
    assert r.status_code == 200, r.text
    uid = r.json()["task_uid"]
    assert r.json()["task"]["summary"] == "milk (2)" and pid in r.json()["task"]["notes"]
    # The list's name is the link text (Markdown), not the long URL.
    assert r.json()["task"]["notes"].startswith("From the list [Shop \\[weekly\\]](http")
    assert r.json()["task"]["notes"].endswith(f"/notes/n/{pid}#i-{it['id']})")
    assert (await get(client, pid))["items"][0]["task_uid"] == uid
    linked = (await client.get(f"{API}/notes/linked", params={"target_kind": "task", "ref": uid})).json()["notes"]
    assert linked[0]["item_id"] == it["id"]
    await do_undo(client, r.json()["undo"])
    assert (await get(client, pid))["items"][0]["task_uid"] is None
    tasks = (await client.get(f"{API}/tasks", params={"status": "all"})).json()
    rows = tasks.get("items", tasks) if isinstance(tasks, dict) else tasks
    assert all(t.get("uid") != uid for t in rows)


async def test_sync_delta(client):
    from datetime import UTC, datetime, timedelta

    a = await new(client, kind="text", title="Old")
    since = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    import asyncio

    await asyncio.sleep(1.2)
    b = await new(client, kind="checklist", title="New", lines="milk")
    await client.delete(f"{API}/notes/{a['id']}")
    r = (await client.get(f"{API}/notes/sync", params={"since": since})).json()
    assert [n["id"] for n in r["notes"]] == [b["id"]] and r["trashed"] == [a["id"]]
    assert r["notes"][0]["items"][0]["text"] == "milk"


async def test_long_notes_are_embedded_local_only_locally(client, user, monkeypatch):
    """The worker embeds notes of 300 characters or more; a local-only note only by a local
    endpoint; a short note keeps no vector."""
    from types import SimpleNamespace

    from sqlalchemy import select

    from mailr.db import sessionmaker
    from mailr.intel import embed as emb
    from mailr.notes.embed import embed_pending
    from mailr.notes.models import Note

    hosted = SimpleNamespace(endpoint=SimpleNamespace(locality="hosted"))

    async def current(db, *, sensitive, **_kw):
        return None if sensitive else (hosted, "hosted-model")

    async def embed_texts(r, texts):
        return [[0.1] * 512 for _ in texts]

    monkeypatch.setattr(emb, "current", current)
    monkeypatch.setattr(emb, "embed_texts", embed_texts)
    long = await new(client, kind="text", title="Long", body="word " * 80)
    secret = await new(client, kind="text", title="Secret", body="word " * 80, local_only=True)
    short = await new(client, kind="text", title="Short", body="hi")
    async with sessionmaker()() as db:
        assert await embed_pending(db, user.id) == 1
        await db.commit()
        rows = {n.pid: n for n in (await db.execute(select(Note).where(Note.owner_user_id == user.id))).scalars()}
    assert rows[long["id"]].embedding is not None
    assert rows[secret["id"]].embedding is None and rows[short["id"]].embedding is None
