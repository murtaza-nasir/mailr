# ACT-SURFACES: api assistant
"""Extras of notes and tables: the assistant's
note.remind (update_note remind_at) and note.from_template (create_note template, list_notes
templates), links to recordings by their Speakr ids with the links panel lookup, table.link and
note.link on events and tasks with /notes/linked and /tables/linked, checkitem.to_task's round trip
(the task's links panel names the list and the item), the owner's people dialog listing people who
left, and group-owned notes and tables embedded for semantic search under the local-only rules."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from mailr.agent import screen
from mailr.agent.tools import ToolCtx, run_tool
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.models import User

# These tests never touch the mail store: database-only users (conftest.make_db_user).
pytestmark = pytest.mark.db_user

API = "/api/v1"
PW = "extras-password-123"


async def tool(user, name, args):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        ctx = ToolCtx(db=db, p=Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"}), assistant=True,
                      chat_session_id=f"s-{uuid.uuid4().hex[:6]}", ui=screen.sanitize({}), session_sensitive=False,
                      untrusted=False, local_model=True, chat_context={})
        res = await run_tool(ctx, name, args)
        await db.commit()
        return res


@pytest.fixture
async def people(app):
    made: list[AsyncClient] = []

    async def make(name: str, groups: tuple[str, ...] = ()) -> AsyncClient:
        from mailr.core.users import create_user

        async with sessionmaker()() as db:
            u = await create_user(db, f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.com", name, password=PW)
            u = await db.get(User, u.id)
            u.assigned_groups = list(groups)
            await db.commit()
            uid, email = u.id, u.email
        c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        r = await c.post(f"{API}/auth/login", json={"email": email, "password": PW})
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        c.uid = uid  # type: ignore[attr-defined]
        c.email = email  # type: ignore[attr-defined]
        made.append(c)
        return c

    yield make
    for c in made:
        await c.aclose()


# ---------------------------------------------------------------- the assistant


async def test_assistant_note_remind_sets_and_clears_the_users_reminder(client, user):
    """note.remind on the assistant: update_note remind_at sets the user's reminder (an undo in the
    chat); [] removes it. The tool has no snooze or dismiss."""
    r = await client.post(f"{API}/notes", json={"kind": "text", "title": "Renew passport"})
    pid = r.json()["note"]["id"]
    at = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    res = await tool(user, "update_note", {"note": pid, "remind_at": [at.isoformat()]})
    assert res.data["results"][0]["ok"] is True, res.data
    assert any(e.get("undo") for e in res.events)
    got = (await client.get(f"{API}/notes/{pid}")).json()
    assert [x["key"] for x in got["reminders"]] == [f"n|{pid}|abs|{at.isoformat()}"]
    res = await tool(user, "update_note", {"note": pid, "remind_at": []})
    assert res.data["results"][0]["ok"] is True
    assert (await client.get(f"{API}/notes/{pid}")).json()["reminders"] == []
    from mailr.agent.tools import TOOLS

    assert not any("snooze" in n or "dismiss" in n for n in TOOLS)


async def test_assistant_note_from_template_by_name_and_list_templates(client, user):
    """note.from_template on the assistant: create_note with a template name; list_notes templates."""
    res = await tool(user, "list_notes", {"templates": True})
    names = {t["name"] for t in res.data["templates"]}
    assert {"Shopping list", "Packing list", "Meeting notes", "Expenses"} <= names
    res = await tool(user, "create_note", {"template": "packing list", "title": "Lisbon trip"})
    [r] = res.data["results"]
    assert r["ok"] is True and r["kind"] == "checklist" and r["items"] > 5, res.data
    n = (await client.get(f"{API}/notes/{r['id']}")).json()
    assert n["title"] == "Lisbon trip" and "Documents" in [s["name"] for s in n["sections"]]
    res = await tool(user, "create_note", {"template": "Expenses"})
    [r] = res.data["results"]
    assert r["ok"] is True and r["id"].startswith("t") and r["kind"] == "table"
    from mailr.agent.tools import ToolError

    with pytest.raises(ToolError):
        await tool(user, "create_note", {"template": "No such template"})


# ---------------------------------------------------------------- links


async def _recording(user) -> tuple[int, int]:
    from mailr.models import Connector, SpeakrRecording

    async with sessionmaker()() as db:
        c = Connector(user_id=user.id, kind="speakr", name="Speakr", mode="mirror", data_class="normal",
                      config={"base_url": "https://speakr.invalid"}, state={}, enabled=False, status="paused",
                      status_detail="Paused")
        db.add(c)
        await db.flush()
        db.add(SpeakrRecording(user_id=user.id, connector_id=c.id, recording_id=42, title="Weekly sync",
                               participants=[], status="completed"))
        await db.commit()
        return c.id, 42


async def test_note_link_to_a_recording_by_its_speakr_ids(client, user):
    """note.link and table.link to a recording: the recording's own address (connector and Speakr
    id); the link opens it and its Links tab finds the note and the table (web.recording)."""
    cid, rid = await _recording(user)
    n = (await client.post(f"{API}/notes", json={"kind": "text", "title": "Sync notes"})).json()["note"]
    r = await client.post(f"{API}/notes/{n['id']}/links", json={"target_kind": "recording",
                                                                "target_ref": {"connector_id": cid, "recording_id": rid}})
    assert r.status_code == 200, r.text
    link = r.json()["link"]
    assert link["title"] == "Weekly sync" and link["target_ref"] == {"connector_id": cid, "recording_id": rid}
    t = (await client.post(f"{API}/tables", json={"name": "Actions"})).json()["table"]
    r = await client.post(f"{API}/tables/{t['id']}/links", json={"target_kind": "recording",
                                                                 "target_ref": {"connector_id": cid, "recording_id": rid}})
    assert r.status_code == 200, r.text
    got = (await client.get(f"{API}/notes/linked", params={"target_kind": "recording", "ref": f"{cid}:{rid}"})).json()
    assert [x["id"] for x in got["notes"]] == [n["id"]]
    got = (await client.get(f"{API}/tables/linked", params={"target_kind": "recording", "ref": f"{cid}:{rid}"})).json()
    assert [x["id"] for x in got["tables"]] == [t["id"]]
    r = await client.post(f"{API}/notes/{n['id']}/links", json={"target_kind": "recording",
                                                                "target_ref": {"connector_id": cid, "recording_id": 999}})
    assert r.status_code == 404
    # note.unlink with its undo
    r = await client.delete(f"{API}/notes/{n['id']}/links/{link['id']}")
    assert r.status_code == 200
    assert (await client.get(f"{API}/notes/linked", params={"target_kind": "recording", "ref": f"{cid}:{rid}"})).json()["notes"] == []


async def test_checkitem_to_task_round_trip_names_the_list_on_the_task(client, user, monkeypatch):
    """checkitem.to_task: the task's links panel (GET /notes/linked?target_kind=task) lists the list
    with the item, and the undo removes the task and the link."""
    from mailr.calendar import routes_tasks

    made: dict[str, dict] = {}

    async def create(db, u, payload):
        uid = f"task-{uuid.uuid4().hex[:8]}"
        made[uid] = payload
        return {"uid": uid, "summary": payload["summary"]}

    async def delete(db, u, payload):
        made.pop(payload["uid"], None)
        return {"ok": True}

    monkeypatch.setattr(routes_tasks, "_x_tasks_create", create)
    monkeypatch.setattr(routes_tasks, "_x_tasks_delete", delete)
    n = (await client.post(f"{API}/notes", json={"kind": "checklist", "title": "Garden", "lines": "3 bags of soil"})).json()["note"]
    item = n["items"][0]
    r = await client.post(f"{API}/notes/{n['id']}/items/{item['id']}/to-task", json={})
    assert r.status_code == 200, r.text
    uid = r.json()["task_uid"]
    assert made[uid]["summary"] == "soil (3 bag)" and f"/notes/n/{n['id']}#i-{item['id']}" in made[uid]["notes"]
    got = (await client.get(f"{API}/notes/linked", params={"target_kind": "task", "ref": uid})).json()["notes"]
    assert [(x["id"], x["item_id"], x["title"]) for x in got] == [(n["id"], item["id"], "Garden")]
    assert (await client.get(f"{API}/notes/{n['id']}")).json()["items"][0]["task_uid"] == uid
    undo = r.json()["undo"]
    assert (await client.request(undo["method"], API + undo["path"])).status_code == 200
    assert uid not in made
    assert (await client.get(f"{API}/notes/linked", params={"target_kind": "task", "ref": uid})).json()["notes"] == []


# ---------------------------------------------------------------- the people dialog


async def test_people_dialog_lists_who_left(people):
    """The owner's people dialog lists the people who removed a shared note from their notes, with
    when; a new share brings them back and takes them off that list."""
    owner = await people("Owner")
    dana = await people("Dana")
    n = (await owner.post(f"{API}/notes", json={"kind": "text", "title": "Plans"})).json()["note"]
    assert (await owner.put(f"{API}/notes/{n['id']}/shares/{dana.uid}", json={"level": "edit", "email": dana.email})).status_code == 200
    assert (await dana.delete(f"{API}/notes/{n['id']}/shares/me")).status_code == 200
    s = (await owner.get(f"{API}/notes/{n['id']}/shares")).json()
    assert s["people"] == [] and [(x["name"], x["via_group"]) for x in s["left"]] == [("Dana", False)]
    assert s["left"][0]["left_at"]
    assert (await owner.put(f"{API}/notes/{n['id']}/shares/{dana.uid}", json={"level": "view"})).status_code == 200
    s = (await owner.get(f"{API}/notes/{n['id']}/shares")).json()
    assert [p["name"] for p in s["people"]] == ["Dana"] and s["left"] == []


# ---------------------------------------------------------------- group notes embedded


async def test_group_notes_and_tables_are_embedded_with_the_local_only_rules(people, monkeypatch):
    """Group-owned notes and tables are embedded for semantic search: a write
    queues the group's job; a local-only one waits for a local endpoint."""
    from mailr.intel import embed as emb
    from mailr.models import Job
    from mailr.notes.embed import embed_pending
    from mailr.notes.models import Note
    from mailr.tables.embed import embed_pending as table_embed
    from mailr.tables.models import DataTable

    hosted = SimpleNamespace(endpoint=SimpleNamespace(locality="hosted"))

    async def current(db, *, sensitive, **_kw):
        return None if sensitive else (hosted, "hosted-model")

    async def embed_texts(r, texts):
        return [[0.1] * 512 for _ in texts]

    monkeypatch.setattr(emb, "current", current)
    monkeypatch.setattr(emb, "embed_texts", embed_texts)
    g = f"House-{uuid.uuid4().hex[:6]}"
    a = await people("Ann", (g,))
    long = (await a.post(f"{API}/notes", json={"kind": "text", "title": "House rules", "body": "word " * 80,
                                               "group": g})).json()["note"]
    secret = (await a.post(f"{API}/notes", json={"kind": "text", "title": "Alarm code", "body": "word " * 80,
                                                 "group": g, "local_only": True})).json()["note"]
    t = (await a.post(f"{API}/tables", json={"name": "Chores", "group": g, "columns": [{"name": "Task"}],
                                             "description": "who does which chore " * 20})).json()["table"]
    async with sessionmaker()() as db:
        jobs = (await db.execute(select(Job.kind, Job.payload).where(Job.kind.in_(["notes.embed", "tables.embed"]))
                                 )).all()
        assert ("notes.embed", {"group": g}) in [(k, p) for k, p in jobs]
        assert ("tables.embed", {"group": g}) in [(k, p) for k, p in jobs]
        assert await embed_pending(db, None, group=g) == 1
        assert await table_embed(db, None, group=g) == 1
        await db.commit()
        rows = {n.pid: n for n in (await db.execute(select(Note).where(Note.owner_group == g))).scalars()}
        tbl = (await db.execute(select(DataTable).where(DataTable.pid == t["id"]))).scalar_one()
    assert rows[long["id"]].embedding is not None and rows[secret["id"]].embedding is None
    assert tbl.embedding is not None
