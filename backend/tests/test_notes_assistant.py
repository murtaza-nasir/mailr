# ACT-SURFACES: assistant
"""The assistant on notes and checklists.

Covered, with their action ids (the `assistant` cells of the note, checkitem, checksection and
notelabel families): note.create, note.add_items, note.from_email, note.edit, note.color,
note.to_checklist, note.to_text, note.pin, note.label, note.archive, note.link, note.delete,
note.restore, note.history, note.restore_version, note.search, note.clear_checked,
note.uncheck_all, checkitem.add, checkitem.check, checkitem.uncheck, checkitem.edit,
checkitem.move, checkitem.delete, checkitem.to_task, checkitem.usual_add, checksection.create,
checksection.rename, checksection.reorder, checksection.order_mode, checksection.delete,
notelabel.create, notelabel.rename, notelabel.color, notelabel.delete, and agent.ask_about (the
note editor's "Ask assistant" kept on the chat).

Also: the permission classes and their defaults, approvals and their executor, a tainted turn that
asks for free text and not for a check, hiding in a tainted turn, errands on shared notes,
names that match two lists, local-only notes hidden from a hosted model, API
tokens with the `notes` scope, [[note:…]] citations, the screen context, and a full chat turn.
"""

import json
import uuid

import pytest
from helpers.notes_assistant import _ctx
from sqlalchemy import select

from mailr.agent import screen
from mailr.agent.tools import ToolError, run_tool
from mailr.core import permissions
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.models import PendingAction, PermissionRule, User
from mailr.notes.models import Note, NoteItem, NoteRevision

API = "/api/v1"


async def new(client, **kw):
    r = await client.post(f"{API}/notes", json=kw)
    assert r.status_code == 200, r.text
    return r.json()["note"]


async def get(client, pid):
    r = await client.get(f"{API}/notes/{pid}")
    assert r.status_code == 200, r.text
    return r.json()


def texts(note, checked=None):
    return [i["text"] for i in note["items"] if checked is None or i["checked"] == checked]


async def tool(user, name, args, **kw):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        res = await run_tool(_ctx(db, u, **kw), name, args)
        await db.commit()
        return res


async def _decide(user_id, pid, decision="approve"):
    async with sessionmaker()() as db:
        await permissions.decide(db, await db.get(User, user_id), pid, decision, "once")
        await db.commit()
    if decision == "approve":
        await permissions.execute_job({"pending_id": pid}, None)
    async with sessionmaker()() as db:
        return await db.get(PendingAction, pid)


async def _rule(user_id, action, effect, constraints=None, subject="assistant"):
    async with sessionmaker()() as db:
        db.add(PermissionRule(user_id=user_id, subject=subject, action=action, effect=effect,
                              constraints=constraints or {}))
        await db.commit()


# ---------------------------------------------------------------- the clean path


async def test_add_to_shopping_list_under_costco_is_one_clean_call(client, user):
    """note.add_items / checkitem.add: "add milk and 2 eggs to my shopping list under Costco" finds the
    list by name on the server, makes the section, parses the quantity, asks nothing, gives an undo
    for the chat, and keeps a version by the assistant."""
    lst = await new(client, kind="checklist", title="Shopping", shopping=True, lines="bread")
    res = await tool(user, "checklist_items", {"note": "my shopping list", "ops": [
        {"op": "add", "lines": "milk, 2 eggs", "section": "Costco"}]})
    assert res.pending is None, res.data
    r = res.data["results"][0]
    assert r["ok"] is True and [a["text"] for a in r["added"]] == ["milk", "eggs"]
    assert r["added"][1]["qty"] == "2" and r["added"][0]["section"] == "Costco"
    assert r["new_sections"] == ["Costco"]
    assert res.data["note"]["id"] == lst["id"] and res.data["note"]["cite"] == f"[[note:{lst['id']}]]"
    assert res.events and res.events[0]["type"] == "config" and res.events[0]["undo"]["path"].startswith("/notes/")
    assert res.refs and res.refs[0]["kind"] == "note" and res.refs[0]["id"] == lst["id"]
    note = await get(client, lst["id"])
    costco = next(s for s in note["sections"] if s["name"] == "Costco")
    assert {i["text"]: i["section_id"] for i in note["items"]} == {"bread": None, "milk": costco["id"],
                                                                   "eggs": costco["id"]}
    assert note["updated_by"]["kind"] == "assistant"
    revs = (await client.get(f"{API}/notes/{lst['id']}/revisions")).json()["revisions"]
    assert revs[0]["source"] == "assistant" and revs[0]["by"]["kind"] == "assistant"
    # The chat's Undo is the action's own undo request.
    u = res.events[0]["undo"]
    assert (await client.request(u["method"], API + u["path"], json=u["body"])).status_code == 200
    assert texts(await get(client, lst["id"])) == ["bread"]


async def test_check_edit_move_delete_and_sections(client, user):
    """checkitem.check, .uncheck, .edit, .move, .delete by the item's text; checksection.create,
    .rename, .reorder, .order_mode, .delete; note.clear_checked asks, note.uncheck_all does not."""
    lst = await new(client, kind="checklist", title="Packing", lines="socks\nshirts\nToiletries:\ntoothbrush")
    res = await tool(user, "checklist_items", {"note": "Packing", "ops": [
        {"op": "check", "item": "socks"}, {"op": "edit", "item": "shirts", "text": "3 shirts", "extra": "the blue ones"},
        {"op": "section_create", "name": "Shoes"}, {"op": "section_rename", "section": "Toil", "name": "Bathroom"},
        {"op": "section_move", "section": "Shoes", "direction": "up"},
        {"op": "section_order", "section": "Bathroom", "mode": "learned"}]})
    assert all(r["ok"] for r in res.data["results"]), res.data
    n = await get(client, lst["id"])
    assert [s["name"] for s in n["sections"]] == ["Shoes", "Bathroom"]
    assert n["sections"][1]["order_mode"] == "learned"
    shirts = next(i for i in n["items"] if i["text"] == "shirts")
    assert shirts["qty"]["amount"] == "3" and shirts["extra"] == "the blue ones"
    assert next(i for i in n["items"] if i["text"] == "socks")["checked"] is True
    res = await tool(user, "checklist_items", {"note": lst["id"], "ops": [
        {"op": "move", "item": "shirts", "section": "Shoes"}, {"op": "uncheck", "item": "socks"},
        {"op": "delete", "item": "toothbrush"}, {"op": "section_delete", "section": "Bathroom"},
        {"op": "uncheck_all"}]})
    assert all(r["ok"] for r in res.data["results"]), res.data
    n = await get(client, lst["id"])
    assert texts(n) == ["socks", "shirts"] and [s["name"] for s in n["sections"]] == ["Shoes"]
    assert next(i for i in n["items"] if i["text"] == "shirts")["section_id"] == n["sections"][0]["id"]
    await tool(user, "checklist_items", {"note": lst["id"], "ops": [{"op": "check", "item": "socks"}]})
    res = await tool(user, "checklist_items", {"note": lst["id"], "ops": [{"op": "clear_checked"}]})
    assert res.pending is not None and res.pending["action"] == "notes.delete"
    assert res.data["results"][0]["ok"] is None and res.data["status"] == "pending_approval"
    assert texts(await get(client, lst["id"]), checked=True) == ["socks"]
    pa = await _decide(user.id, res.pending["id"])
    assert pa.status == "executed", pa.result
    assert texts(await get(client, lst["id"])) == ["shirts"]


async def test_create_update_convert_label_link_and_history(client, user, raw):
    """note.create (and note.from_email with a conversation), note.edit with find-and-replace,
    note.color, note.pin, note.label, note.archive, note.link, note.to_checklist, note.to_text,
    note.history and note.restore_version; notelabel.create, .rename, .color, .delete."""
    from helpers.agent import _ingest

    _m, conv = await _ingest(user.id, raw("Party supplies", body="- cups\n- plates"))
    res = await tool(user, "create_note", {"kind": "checklist", "title": "Party", "lines": ["cups", "2 packs plates"],
                                           "conversation_id": conv, "labels": ["Home"]})
    assert res.pending is None, res.data
    pid = res.data["results"][0]["id"]
    n = await get(client, pid)
    assert texts(n) == ["cups", "plates"] and n["items"][1]["qty"]["unit"] == "pack"
    assert n["links"][0]["target_kind"] == "conversation" and n["state"]["labels"] == ["Home"]
    t = await tool(user, "create_note", {"title": "Plan", "body": "Call the caterer\nBook the hall"})
    tid = t.data["results"][0]["id"]
    res = await tool(user, "update_note", {"note": "Plan", "edits": [{"find": "Book the hall", "replace": "Book the park"}],
                                           "color": "sage", "pinned": True, "add_labels": ["Events"]})
    assert all(r["ok"] for r in res.data["results"]), res.data
    n = await get(client, tid)
    assert n["body"] == "Call the caterer\nBook the park" and n["color"] == "sage"
    assert n["state"]["pinned"] is True and n["state"]["labels"] == ["Events"]
    res = await tool(user, "update_note", {"note": tid, "to": "checklist", "archived": True})
    assert all(r["ok"] for r in res.data["results"])
    n = await get(client, tid)
    assert n["kind"] == "checklist" and texts(n) == ["Call the caterer", "Book the park"] and n["state"]["archived"]
    await tool(user, "update_note", {"note": tid, "to": "text", "link_conversation_id": conv})
    hist = await tool(user, "note_history", {"note": tid})
    assert [r["by"] for r in hist.data["items"]][0] == "the assistant"
    first = hist.data["items"][-1]["rev"]
    one = await tool(user, "note_history", {"note": tid, "rev": first})
    assert one.data["body"] == "Call the caterer\nBook the hall"
    res = await tool(user, "note_history", {"note": tid, "rev": first, "restore": True})
    assert res.data["results"][0]["ok"] is True
    assert (await get(client, tid))["body"] == "Call the caterer\nBook the hall"
    # Labels: create, rename and recolour without a card; a delete asks.
    for args in ({"action": "create", "name": "Trips", "color": "peacock"},
                 {"action": "rename", "name": "Trips", "new_name": "Travel"},
                 {"action": "color", "name": "Travel", "color": "grape"}):
        r = await tool(user, "manage_note_labels", args)
        assert r.pending is None and r.data["results"][0]["ok"], r.data
    labels = (await tool(user, "manage_note_labels", {"action": "list"})).data["items"]
    assert {"name": "Travel", "color": "grape", "notes": 0} in labels
    r = await tool(user, "manage_note_labels", {"action": "delete", "name": "Travel"})
    assert r.pending is not None and r.pending["action"] == "notes.labels"


async def test_reads_and_search(client, user):
    """list_notes, get_note (items with ids, sections, authors, usual) and search_notes (note.search)
    with operators and item matches."""
    a = await new(client, kind="checklist", title="Hardware", lines="screws\nCostco:\n2 hinges")
    await new(client, kind="text", title="Garden ideas", body="tomatoes by the fence")
    ls = (await tool(user, "list_notes", {"kind": "checklist"})).data
    assert [x["id"] for x in ls["items"]] == [a["id"]] and ls["items"][0]["items"] == 2
    g = (await tool(user, "get_note", {"note": "hardware", "usual": True})).data
    assert g["sections"] == ["Costco"] and g["items"][1] == {
        "item_id": g["items"][1]["item_id"], "text": "hinges", "qty": "2", "section": "Costco",
        "cite": f"[[note:{a['id']}#{g['items'][1]['item_id']}]]"}
    assert g["usual"] == []
    s = (await tool(user, "search_notes", {"q": "hinges"})).data
    assert s["items"][0]["id"] == a["id"] and s["items"][0]["matches"][0]["text"] == "hinges"
    s = (await tool(user, "search_notes", {"q": "is:text tomatoes"})).data
    assert [x["title"] for x in s["items"]] == ["Garden ideas"]


# ---------------------------------------------------------------- taint and the permission engine


async def test_tainted_turn_asks_for_new_text_but_not_for_a_check(client, user):
    """A turn that read other people's text: a new item's text waits on a card (notes.edit,
    ask_by_default); a check runs at once; deleting an item (hiding) asks too. Approving runs the
    stored operation exactly once."""
    lst = await new(client, kind="checklist", title="Errands", lines="stamps")
    res = await tool(user, "checklist_items", {"note": "Errands", "ops": [
        {"op": "add", "lines": "batteries"}, {"op": "check", "item": "stamps"}]}, untrusted=True)
    rs = res.data["results"]
    assert rs[0]["ok"] is None and rs[0]["waiting"] == "notes.edit" and rs[1]["ok"] is True
    assert res.pending["action"] == "notes.edit" and res.data["status"] == "partly_waiting"
    async with sessionmaker()() as db:
        pa = await db.get(PendingAction, res.pending["id"])
        assert "Item: batteries" in json.dumps(pa.preview) and pa.preview["reason"].startswith("hard rule")
    assert texts(await get(client, lst["id"])) == ["stamps"]
    pa = await _decide(user.id, res.pending["id"])
    assert pa.status == "executed" and pa.result["results"][0]["items"] == 1
    n = await get(client, lst["id"])
    assert texts(n) == ["batteries", "stamps"] and n["updated_by"]["kind"] == "assistant"  # checked ones fold
    res = await tool(user, "checklist_items", {"note": lst["id"], "ops": [{"op": "delete", "item": "batteries"}]},
                     untrusted=True)
    assert res.pending is not None
    # A user's own allow rule lets the tainted free text through (ask_by_default, not a hard ask).
    await _rule(user.id, "notes.edit", "allow")
    res = await tool(user, "checklist_items", {"note": lst["id"], "ops": [{"op": "add", "lines": "tape"}]},
                     untrusted=True)
    assert res.pending is None and res.data["results"][0]["ok"] is True


async def test_classes_rules_and_constraints(client, user):
    """notes.delete asks (note.delete into Trash, then note.restore), a deny rule refuses, an allow
    rule constrained to one note's id covers only that note; the classes are offered with their
    defaults for the assistant and for tokens."""
    a = await new(client, kind="checklist", title="Alpha", lines="x")
    b = await new(client, kind="checklist", title="Beta", lines="y")
    res = await tool(user, "delete_note", {"note": "Alpha"})
    assert res.pending["action"] == "notes.delete"
    assert (await _decide(user.id, res.pending["id"])).status == "executed"
    assert (await get(client, a["id"]))["deleted_at"] is not None
    res = await tool(user, "delete_note", {"note": "Alpha", "restore": True})
    assert res.data["results"][0]["ok"] is True and (await get(client, a["id"]))["deleted_at"] is None
    await _rule(user.id, "notes.delete", "allow", {"notes": [b["id"]]})
    assert (await tool(user, "delete_note", {"note": "Alpha"})).pending is not None
    assert (await tool(user, "delete_note", {"note": "Beta"})).data["results"][0]["ok"] is True
    await _rule(user.id, "notes.edit", "deny")
    res = await tool(user, "checklist_items", {"note": "Alpha", "ops": [{"op": "check", "item": "x"}]})
    assert res.data["results"][0]["ok"] is False and "not allowed" in res.data["results"][0]["error"]
    assert permissions.CLASSES["notes.labels"] == ("ask", "notes")
    assert permissions.default_effect("notes.labels", "assistant") == "allow"
    assert permissions.default_effect("notes.edit", "token") == "allow"
    r = (await client.get(f"{API}/permissions/effective", params={"subject": "assistant"})).json()
    rows = {x["action"]: x for x in r["items"]}
    assert rows["notes.delete"]["default"] == "ask" and rows["notes.create"]["default"] == "allow"


async def test_errand_writing_to_a_group_note_always_asks(client, user):
    """An errand (a standing order) may write to the user's own notes under the classes; a write to a
    note other people share (here a group's list) always asks, even under an allow rule."""
    from mailr.notes import access

    own = await new(client, kind="checklist", title="Mine", lines="a")
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.assigned_groups = ["Household"]
        n = Note(pid="n" + uuid.uuid4().hex[:11], owner_group="Household", created_by=user.id, kind="checklist",
                 title="House list", updated_by={})
        db.add(n)
        await db.commit()
        house = n.pid
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        # The creator of a group list manages its people; other members edit.
        assert access.level(u, (await db.execute(select(Note).where(Note.pid == house))).scalar_one()) == "share"
    await _rule(user.id, "notes.edit", "allow")
    res = await tool(user, "checklist_items", {"note": "Mine", "ops": [{"op": "check", "item": "a"}]}, errand=True)
    assert res.pending is None and res.data["results"][0]["ok"] is True
    res = await tool(user, "checklist_items", {"note": "House list", "ops": [{"op": "add", "lines": "soap"}]},
                     errand=True)
    assert res.pending is not None
    async with sessionmaker()() as db:
        pa = await db.get(PendingAction, res.pending["id"])
        assert "errand" in pa.preview["reason"]
    # The same write from a chat (not an errand) runs: the user is there to see it.
    res = await tool(user, "checklist_items", {"note": "House list", "ops": [{"op": "add", "lines": "soap"}]})
    assert res.pending is None and res.data["results"][0]["ok"] is True
    assert own["id"] != house


# ---------------------------------------------------------------- reach


async def test_two_lists_match_a_name_is_an_error_without_titles(client, user):
    await new(client, kind="checklist", title="Shop: weekly", lines="a")
    await new(client, kind="checklist", title="Shop: party", lines="b")
    with pytest.raises(ToolError) as e:
        await tool(user, "checklist_items", {"note": "shop", "ops": [{"op": "add", "lines": "milk"}]})
    msg = str(e.value)
    assert "2 notes match" in msg and "weekly" not in msg and "party" not in msg
    with pytest.raises(ToolError, match="no note matches"):
        await tool(user, "get_note", {"note": "nothing like it"})


# View-only parity runs on real shares in tests/test_notes_sharing.py
# (test_assistant_parity_on_real_shares).


async def test_local_only_notes_are_hidden_from_a_hosted_model(client, user):
    """A chat on a hosted model gets no local-only note (list, search, by name, by id) and is
    told some are hidden; a sensitive chat (a local model) sees it, and the result marks it sensitive."""
    secret = await new(client, kind="checklist", title="Grades", lines="DATA 520 appeal", local_only=True)
    await new(client, kind="checklist", title="Groceries", lines="milk")
    ls = await tool(user, "list_notes", {}, local_model=False)
    assert [x["title"] for x in ls.data["items"]] == ["Groceries"] and "hidden" in ls.data["note"]
    s = await tool(user, "search_notes", {"q": "appeal"}, local_model=False)
    assert s.data["items"] == [] and "hidden" in s.data["note"]
    for ref in ("Grades", secret["id"]):
        with pytest.raises(ToolError, match="hidden"):
            await tool(user, "get_note", {"note": ref}, local_model=False)
    g = await tool(user, "get_note", {"note": "Grades"}, local_model=False, sensitive=True)
    assert g.data["items"][0]["text"] == "DATA 520 appeal" and g.sensitive is True


# ---------------------------------------------------------------- tokens


async def test_api_tokens_with_the_notes_scope(client, user):
    """The `notes` token scope opens the routes (403 without it). A token's free text asks (202, a
    pending approval, run by the executor once approved); a check does not; a token that is not
    local-only sees no local-only note."""
    from httpx import ASGITransport, AsyncClient

    from mailr.core.auth import create_token

    secret = await new(client, kind="text", title="Secret", local_only=True)
    lst = await new(client, kind="checklist", title="Shop", lines="milk")
    async with sessionmaker()() as db:
        raw_ok, _ = await create_token(db, await db.get(User, user.id), "notes-agent", ["notes"])
        raw_no, _ = await create_token(db, await db.get(User, user.id), "mail-agent", ["read"])
    app = client._transport.app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {raw_ok}"}) as t:
        assert (await t.get(f"{API}/notes")).status_code == 200
        ids = [c["id"] for c in (await t.get(f"{API}/notes")).json()["notes"]]
        assert lst["id"] in ids and secret["id"] not in ids
        assert (await t.get(f"{API}/notes/{secret['id']}")).status_code == 404
        r = await t.patch(f"{API}/notes/{lst['id']}/items/{lst['items'][0]['id']}", json={"checked": True})
        assert r.status_code == 200, r.text
        r = await t.post(f"{API}/notes/{lst['id']}/items", json={"lines": "eggs"})
        assert r.status_code == 202, r.text
        pid = r.json()["approval"]["id"]
        r = await t.delete(f"{API}/notes/{lst['id']}?forever=true")
        assert r.status_code in (202, 409), r.text
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {raw_no}"}) as t:
        assert (await t.get(f"{API}/notes")).status_code == 403
    async with sessionmaker()() as db:
        row = (await db.execute(select(PendingAction).where(PendingAction.user_id == user.id,
                                                            PendingAction.action == "notes.edit")
                                .order_by(PendingAction.id.desc()))).scalars().first()
    pa = await _decide(user.id, row.id)
    assert pa.status == "executed", pa.result
    n = await get(client, lst["id"])
    assert sorted(texts(n)) == ["eggs", "milk"] and n["updated_by"]["kind"] == "agent"
    assert pid is not None


# ---------------------------------------------------------------- citations, screen, ask about


async def test_note_citations_resolve_for_the_caller_only(client, user, app):
    from mailr.intel import citations

    lst = await new(client, kind="checklist", title="Shopping", lines="milk")
    item = lst["items"][0]["id"]
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        p = Principal(user=u, scopes=set(ALL_SCOPES))
        text, cites = await citations.resolve(db, p, f"Milk is on it [[note:{lst['id']}#{item}]] and [[note:{lst['id']}]].")
    assert text == "Milk is on it [1](mailr-cite:1) and [2](mailr-cite:2)."
    assert cites[0]["kind"] == "note" and cites[0]["item_id"] == item and cites[0]["snippet"] == "milk"
    assert cites[0]["url"].endswith(f"/notes/n/{lst['id']}#i-{item}") and cites[1]["item_id"] is None
    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        other = await create_user(db, f"o-{uuid.uuid4().hex[:8]}@example.com", "Other", password="test-password-123")
        p2 = Principal(user=other, scopes=set(ALL_SCOPES))
        text, cites = await citations.resolve(db, p2, f"See [[note:{lst['id']}]].")
    assert text == "See." and cites == []


async def test_screen_context_and_these_items(client, user):
    """Screen context notes{focused, selected_items}: ids only in the prompt (so it does not taint the
    turn); "check these off" takes the selected items; agent.ask_about (focused_note) is kept on the chat."""
    from mailr.agent import loop

    lst = await new(client, kind="checklist", title="Week", lines="a\nb\nc")
    ids = [i["id"] for i in lst["items"]]
    ui = screen.sanitize({"notes": {"view": "lists", "focused": lst["id"], "selected_items": ids[:2] + ["junk"],
                                    "selected": [lst["id"], "bad"]}})
    assert ui["notes"]["selected_items"] == ids[:2] and ui["notes"]["selected"] == [lst["id"]]
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        block = await screen.notes_block(db, Principal(user=u, scopes=set(ALL_SCOPES)), ui)
    assert lst["id"] in block and "Week" not in block and ids[0] in block
    res = await tool(user, "checklist_items", {"ops": [{"op": "check"}]},
                     ui={"notes": {"focused": lst["id"], "selected_items": ids[:2]}})
    assert [r["item_id"] for r in res.data["results"]] == ids[:2]
    assert sorted(texts(await get(client, lst["id"]), checked=True)) == ["a", "b"]
    keep, ui2 = loop.split_context({"focused_note": {"id": lst["id"], "items": [ids[2]]}})
    assert keep["focused_note"] == {"id": lst["id"], "items": [ids[2]]}
    ui3 = screen.with_note({}, keep["focused_note"])
    res = await tool(user, "checklist_items", {"ops": [{"op": "delete"}]}, ui=ui3)
    assert res.data["results"][0]["item_id"] == ids[2]


async def test_to_task_and_usual_items(client, user):
    """checkitem.to_task (asks in a tainted turn: the item's text becomes a task title) and
    checkitem.usual_add (add_usual by key)."""
    from dev_services import radicale_up as _radicale_up
    from helpers.tasks_v2 import _connect

    lst = await new(client, kind="checklist", title="Weekly", lines="coffee")
    async with sessionmaker()() as db:
        from datetime import UTC, datetime, timedelta

        from mailr.notes.models import CheckitemCheck

        note = (await db.execute(select(Note).where(Note.pid == lst["id"]))).scalar_one()
        for d in (21, 14, 7):
            db.add(CheckitemCheck(note_id=note.id, section_id=None, key="bread", text="bread", user_id=user.id,
                                  device_at=datetime.now(UTC) - timedelta(days=d),
                                  server_at=datetime.now(UTC) - timedelta(days=d)))
        await db.commit()
    g = await tool(user, "get_note", {"note": "Weekly", "usual": True})
    res = await tool(user, "checklist_items", {"note": "Weekly", "ops": [{"op": "add_usual", "keys": ["bread"]}]})
    assert res.data["results"][0]["ok"] is True, res.data
    assert texts(await get(client, lst["id"])) == ["coffee", "bread"]
    assert isinstance(g.data["usual"], list)
    res = await tool(user, "checklist_items", {"note": "Weekly", "ops": [{"op": "to_task", "item": "coffee"}]},
                     untrusted=True)
    assert res.pending is not None and res.pending["action"] == "notes.edit"
    if not _radicale_up():
        pytest.skip("Radicale dev server not running")
    await _connect(client, "Personal")
    res = await tool(user, "checklist_items", {"note": "Weekly", "ops": [{"op": "to_task", "item": "coffee"}]})
    assert res.data["results"][0]["ok"] is True and res.data["results"][0]["task"].startswith("[[task:")


# ---------------------------------------------------------------- a full chat turn


async def test_full_chat_turn_adds_milk_and_eggs_under_costco(client, user, fake_llm):
    """The chat: "add milk and 2 eggs to my shopping list under Costco" is one tool call with no
    approval card; the answer cites the list; the turn gives a config event with Undo and a note card."""
    from helpers.agent import _drop_endpoint, _endpoint, _events, _text_turn, _tool_turn

    lst = await new(client, kind="checklist", title="Shopping", shopping=True)
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [
            _tool_turn("checklist_items", {"note": "shopping list", "ops": [
                {"op": "add", "lines": "milk, 2 eggs", "section": "Costco"}]}),
            _text_turn(f"Added milk and 2 eggs under Costco [[note:{lst['id']}]]."),
        ]
        r = await client.post(f"{API}/agent/chat", json={
            "message": "add milk and 2 eggs to my shopping list under Costco",
            "context": {"ui": {"notes": {"view": "all"}}}})
        assert r.status_code == 200
        evs = _events(r.text)
        kinds = [e["type"] for e in evs]
        assert "approval_required" not in kinds, kinds
        step = next(e for e in evs if e["type"] == "tool")
        assert step["ok"] and step["summary"] == "2 items added"
        assert any(e["type"] == "config" and e.get("undo") for e in evs)
        assert any(e["type"] == "ref" and e["kind"] == "note" and e["id"] == lst["id"] for e in evs)
        final = next(e for e in evs if e["type"] == "final")
        assert final["citations"][0]["kind"] == "note" and final["citations"][0]["note_id"] == lst["id"]
        # The prompt told the model about notes, and the tool result went back as a tool message.
        sys_prompt = fake_llm.requests[0]["body"]["messages"][0]["content"]
        assert "checklist_items" in sys_prompt
    finally:
        await _drop_endpoint(ep)
    n = await get(client, lst["id"])
    costco = next(s for s in n["sections"] if s["name"] == "Costco")
    assert [(i["text"], i["section_id"], (i["qty"] or {}).get("amount")) for i in n["items"]] == [
        ("milk", costco["id"], None), ("eggs", costco["id"], "2")]
    async with sessionmaker()() as db:
        revs = (await db.execute(select(NoteRevision).join(Note, Note.id == NoteRevision.note_id)
                                 .where(Note.pid == lst["id"]).order_by(NoteRevision.rev))).scalars().all()
        assert revs[-1].source == "assistant"
        assert (await db.execute(select(NoteItem).where(NoteItem.text == "milk"))).scalars().first() is not None
