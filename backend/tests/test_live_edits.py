"""Live editing (core/live.py): versions and live events per kind, the
three-way merge on write, the 409 conflict, the assistant revising an item in place through the
chat's working set, the editors' snapshot (flush-before-ask), the "Assistant is editing" notice,
and the migration re-run. Actions: draft.save, draft.live_update, draft.resolve_conflict (the 409),
event.live_update, task.live_update, agent.wait_for_typing.

# ACT-SURFACES: api assistant
"""

import importlib.util
import json
from pathlib import Path

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.agent import (
    _drop_endpoint,
    _endpoint,
    _events,
    _seed_thread,
    _setup_identity,
    _text_turn,
    _token_client,
    _tool_turn,
)
from helpers.calendar import _connect as _connect_calendar
from sqlalchemy import func, select, text

from mailr.core import live
from mailr.db import sessionmaker
from mailr.models import ChatSession, ItemVersion, Message

API = "/api/v1"


def P(*xs):
    return "".join(f"<p>{x}</p>" for x in xs)


@pytest.fixture
def notes(monkeypatch):
    """The live events sent to the user's apps (core/events.notify)."""
    from mailr.core import events

    got: list[dict] = []
    real = events.notify

    async def record(db, user_id, event):
        got.append(event)
        await real(db, user_id, event)

    monkeypatch.setattr(events, "notify", record)
    return got


def _items(notes, kind=None):
    return [n for n in notes if n.get("type") == "item" and (kind is None or n["kind"] == kind)]


async def _draft(client, me, html=None, **kw):
    payload = {"identity_id": None, "to": [{"email": "alice@acme.example", "name": "Alice"}], "cc": [], "bcc": [],
               "subject": kw.pop("subject", "Plan"), "html": html if html is not None else P("Hi Alice,", "Body.", "Best,"),
               "attachments": [], **kw}
    r = await client.post(f"{API}/drafts", json={"payload": payload})
    assert r.status_code == 200, r.text
    return r.json(), payload


# ---------------------------------------------------------------- drafts


async def test_draft_versions_and_events(client, user, notes):
    """ACT draft.save"""
    _, me = await _setup_identity(user)
    client.headers["X-Mailr-Client"] = "tab-a"
    made, payload = await _draft(client, me)
    did = made["draft"]["id"]
    assert made["version"] == 1 and made["merged"] is False
    ev = _items(notes, "draft")[-1]
    assert ev == {"type": "item", "kind": "draft", "id": did, "version": 1, "by": "user", "origin": "tab-a"}
    r = await client.put(f"{API}/drafts/{did}", json={"payload": {**payload, "subject": "Plan v2"}, "base_version": 1})
    assert r.status_code == 200 and r.json()["version"] == 2
    got = (await client.get(f"{API}/drafts/{did}")).json()
    assert got["version"] == 2 and got["payload"]["subject"] == "Plan v2"
    li = (await client.get(f"{API}/live/draft/{did}")).json()
    assert li["version"] == 2 and li["content"]["subject"] == "Plan v2" and li["by"] == "user"
    # Saving the same content again keeps the version.
    r = await client.put(f"{API}/drafts/{did}", json={"payload": {**payload, "subject": "Plan v2"}, "base_version": 2})
    assert r.json()["version"] == 2
    # Discarding it tells the open apps.
    assert (await client.delete(f"{API}/drafts/{did}")).status_code == 200
    assert _items(notes, "draft")[-1]["deleted"] is True


async def test_draft_without_versions_gets_one_when_read(client, user):
    """A draft saved before live editing (no version rows) gets version 1 on its first read."""
    _, me = await _setup_identity(user)
    made, _ = await _draft(client, me)
    did = made["draft"]["id"]
    async with sessionmaker()() as db:
        await db.execute(text("DELETE FROM item_versions WHERE user_id = :u"), {"u": user.id})
        await db.commit()
    assert (await client.get(f"{API}/drafts/{did}")).json()["version"] == 1


async def test_draft_merges_changes_to_different_paragraphs(client, user):
    _, me = await _setup_identity(user)
    made, payload = await _draft(client, me, html=P("Hi Alice,", "The numbers are ready.", "Best,"))
    did = made["draft"]["id"]
    # The assistant (another writer) changes the last paragraph from version 1 ...
    r = await client.put(f"{API}/drafts/{did}", json={
        "payload": {**payload, "html": P("Hi Alice,", "The numbers are ready.", "Best regards,")}, "base_version": 1})
    assert r.json()["version"] == 2
    # ... while the editor, still on version 1, saves the user's change to the second.
    r = await client.put(f"{API}/drafts/{did}", json={
        "payload": {**payload, "html": P("Hi Alice,", "The revised numbers are ready.", "Best,")}, "base_version": 1})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["merged"] is True and out["version"] == 3
    assert out["payload"]["html"] == P("Hi Alice,", "The revised numbers are ready.", "Best regards,")
    assert (await client.get(f"{API}/drafts/{did}")).json()["payload"]["html"] == out["payload"]["html"]


async def test_draft_merges_fields(client, user):
    _, me = await _setup_identity(user)
    made, payload = await _draft(client, me)
    did = made["draft"]["id"]
    await client.put(f"{API}/drafts/{did}", json={"payload": {**payload, "subject": "New subject"}, "base_version": 1})
    r = await client.put(f"{API}/drafts/{did}", json={
        "payload": {**payload, "cc": [{"email": "bob@acme.example", "name": ""}]}, "base_version": 1})
    out = r.json()
    assert out["merged"] and out["payload"]["subject"] == "New subject"
    assert [a["email"] for a in out["payload"]["cc"]] == ["bob@acme.example"]


async def test_draft_overlapping_change_is_a_409(client, user):
    _, me = await _setup_identity(user)
    made, payload = await _draft(client, me, html=P("Hi Alice,", "See you Thursday.", "Best,"))
    did = made["draft"]["id"]
    theirs = P("Hi Alice,", "See you on Friday.", "Best,")
    await client.put(f"{API}/drafts/{did}", json={"payload": {**payload, "html": theirs}, "base_version": 1})
    mine = P("Hi Alice,", "See you Thursday at 3.", "Best,")
    r = await client.put(f"{API}/drafts/{did}", json={"payload": {**payload, "html": mine}, "base_version": 1})
    assert r.status_code == 409, r.text
    c = r.json()
    assert c["conflict"] is True and c["fields"] == ["html"] and c["id"] == did
    assert c["base"]["version"] == 1 and c["base"]["content"]["html"] == payload["html"]
    assert c["theirs"]["version"] == 2 and c["theirs"]["content"]["html"] == theirs
    assert c["mine"]["content"]["html"] == mine
    assert c["texts"]["html"] == [{"base": "<p>See you Thursday.</p>", "mine": "<p>See you Thursday at 3.</p>",
                                   "theirs": "<p>See you on Friday.</p>"}]
    # Nothing was overwritten.
    assert (await client.get(f"{API}/drafts/{did}")).json()["payload"]["html"] == theirs
    # "Use mine": the same text from the latest version replaces it.
    r = await client.put(f"{API}/drafts/{did}", json={"payload": {**payload, "html": mine}, "base_version": 2})
    assert r.status_code == 200 and r.json()["version"] == 3


async def test_api_and_mcp_writes_with_a_stale_base_version(client, user, raw):
    """An agent with a token writes from an old version: what the user saved since is merged in,
    an overlapping change is refused, never silently overwritten (REST and the MCP tool path)."""
    _, me = await _setup_identity(user)
    m1, _conv = await _seed_thread(user, raw, me)
    payload = {"to": [{"email": "alice@acme.example", "name": "Alice"}], "cc": [], "bcc": [], "subject": "Re: Q4",
               "html": P("Hi Alice,", "Numbers on Friday.", "Best,"), "attachments": [],
               "in_reply_to_message_id": m1}
    did = (await client.post(f"{API}/drafts", json={"payload": payload})).json()["draft"]["id"]
    # The user saves version 2 in the editor.
    user_html = P("Hi Alice,", "Numbers on Friday.", "Best, Alex")
    await client.put(f"{API}/drafts/{did}", json={"payload": {**payload, "html": user_html}, "base_version": 1})
    tok = (await client.post(f"{API}/tokens", json={"name": "writer", "scopes": ["read", "draft"]})).json()["token"]
    async with _token_client(client, tok) as a:
        r = await a.put(f"{API}/drafts/{did}", json={
            "payload": {**payload, "html": P("Hello Alice,", "Numbers on Friday.", "Best,")}, "base_version": 1})
        assert r.status_code == 200, r.text
        assert r.json()["payload"]["html"] == P("Hello Alice,", "Numbers on Friday.", "Best, Alex")
        r = await a.put(f"{API}/drafts/{did}", json={
            "payload": {**payload, "html": P("Hi Alice,", "Numbers on Friday.", "Cheers,")}, "base_version": 1})
        assert r.status_code == 409 and r.json()["fields"] == ["html"]
        # The tool API (what MCP calls): body from version 1 overlaps the signature line too.
        r = await a.post(f"{API}/agent/tools/update_draft", json={
            "draft_id": did, "base_version": 1, "body": "Hi Alice,\n\nNumbers on Friday.\n\nCheers,"})
        assert r.status_code == 400 and "changed by" in r.json()["error"]
        # From the current version it goes through, by the agent.
        cur = (await a.get(f"{API}/live/draft/{did}")).json()
        r = await a.post(f"{API}/agent/tools/update_draft", json={
            "draft_id": did, "base_version": cur["version"], "edits": [{"find": "Friday", "replace": "Thursday"}]})
        assert r.status_code == 200, r.text
        assert r.json()["result"]["version"] == cur["version"] + 1
    async with sessionmaker()() as db:
        row = (await db.execute(select(ItemVersion).where(ItemVersion.user_id == user.id, ItemVersion.kind == "draft")
                                .order_by(ItemVersion.version.desc()).limit(1))).scalar_one()
        assert row.by == "agent:writer" and "Thursday" in row.content["html"] and "Best, Alex" in row.content["html"]


# ---------------------------------------------------------------- the assistant


async def _drafts_of(user_id):
    async with sessionmaker()() as db:
        return list((await db.execute(select(Message).where(Message.user_id == user_id, Message.draft.is_(True),
                                                            Message.deleted.is_(False)))).scalars())


async def test_assistant_revises_the_same_draft_through_its_working_set(client, user, raw, fake_llm, notes):
    """ACT draft.save @assistant: "make it shorter" after the assistant drafted a reply updates that
    draft in place (same id, a new version), never a second draft."""
    _, me = await _setup_identity(user)
    m1, conv = await _seed_thread(user, raw, me)
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [
            _tool_turn("create_draft", {"reply_to_message_id": m1, "mode": "reply",
                                        "body": "Thanks Alice. I will send the revised numbers by Friday, as agreed."}),
            _text_turn("Drafted."),
        ]
        r = await client.post(f"{API}/agent/chat", json={"messages": [{"role": "user", "content": "Draft a reply"}],
                                                        "context": {"conversation_id": conv}})
        evs = _events(r.text)
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        made = next(e for e in evs if e["type"] == "draft")
        did = made["draft_id"]
        assert made["ref"] == {"kind": "draft", "id": did, "version": 1}
        assert [e for e in evs if e["type"] == "ref"][0]["id"] == did
        # Turn 2: "make it shorter". The model names no draft: the working set says which.
        fake_llm.script = [
            _tool_turn("update_draft", {"edits": [{"find": ", as agreed", "replace": ""}]}, "c2"),
            _text_turn("Shortened."),
        ]
        r = await client.post(f"{API}/agent/chat", json={"messages": [{"role": "user", "content": "Make it shorter"}],
                                                        "session_id": sid, "context": {"conversation_id": conv}})
        evs = _events(r.text)
        tool = next(e for e in evs if e["type"] == "tool")
        assert tool["ok"], tool
        upd = next(e for e in evs if e["type"] == "draft")
        assert upd["draft_id"] == did and upd["ref"]["version"] == 2
        system = fake_llm.requests[-2]["body"]["messages"][0]["content"]
        assert "Working set" in system and f"draft_id {did}, version 1" in system
        assert "Never create a new draft" in system
        drafts = await _drafts_of(user.id)
        assert [d.id for d in drafts] == [did]
        payload = (await client.get(f"{API}/drafts/{did}")).json()["payload"]
        assert "by Friday.</" in payload["html"] and "as agreed" not in payload["html"]
        async with sessionmaker()() as db:
            s = await db.get(ChatSession, sid)
            ws = s.context["working_set"]
            assert [(w["kind"], w["id"], w["version"]) for w in ws] == [("draft", str(did), 2)]
        # The assistant's write was announced: editing start/end and the new version, by the assistant.
        ed = [n for n in notes if n.get("type") == "editing"]
        assert [(n["kind"], n["id"], n["state"], n["by"]) for n in ed] == [("draft", did, "start", "assistant"),
                                                                           ("draft", did, "end", "assistant")]
        assert _items(notes, "draft")[-1]["by"] == "assistant" and _items(notes, "draft")[-1]["version"] == 2
    finally:
        await _drop_endpoint(ep)


async def test_assistant_uses_the_editor_snapshot_sent_with_the_question(client, user, raw, fake_llm):
    """Flush-before-ask: the panel saves the open editors and sends what they show. The assistant
    reads the window's text (with what the user typed) and revises that same draft."""
    _, me = await _setup_identity(user)
    m1, conv = await _seed_thread(user, raw, me)
    payload = {"to": [{"email": "alice@acme.example", "name": "Alice"}], "cc": [], "bcc": [], "subject": "Re: Q4",
               "html": P("Hi Alice,", "Numbers soon.", "Best,"), "attachments": [], "in_reply_to_message_id": m1}
    made = (await client.post(f"{API}/drafts", json={"payload": payload})).json()
    did = made["draft"]["id"]
    ep = await _endpoint(user.id, "local")
    window = {"id": "c1", "draft_id": did, "base_version": 1, "dirty": True, "focused": True, "mode": "reply",
              "to": ["alice@acme.example"], "cc": [], "bcc": [], "subject": "Re: Q4",
              "body": "Hi Alice,\n\nNumbers soon. I typed this and it is not saved yet.\n\nBest,",
              "in_reply_to_message_id": m1, "conversation_id": conv}
    try:
        fake_llm.script = [
            _tool_turn("update_draft", {"edits": [{"find": "not saved yet", "replace": "saved now"}]}),
            _text_turn("Done."),
        ]
        r = await client.post(f"{API}/agent/chat", json={
            "messages": [{"role": "user", "content": "fix the last sentence"}],
            "context": {"conversation_id": conv, "ui": {"compose": [window]}}})
        evs = _events(r.text)
        assert next(e for e in evs if e["type"] == "tool")["ok"]
        system = fake_llm.requests[0]["body"]["messages"][0]["content"]
        assert f"draft {did}, version 1; unsaved changes" in system and "I typed this" in system
        out = (await client.get(f"{API}/drafts/{did}")).json()
        assert out["version"] == 2 and "I typed this and it is saved now." in out["payload"]["html"]
        assert len(await _drafts_of(user.id)) == 1
    finally:
        await _drop_endpoint(ep)


async def test_two_open_drafts_and_none_focused_asks_which(client, user):
    from mailr.agent.tools import ToolCtx, ToolError, run_tool
    from mailr.core.auth import ALL_SCOPES, Principal
    from mailr.models import User

    _, me = await _setup_identity(user)
    a, _ = await _draft(client, me, subject="One")
    b, _ = await _draft(client, me, subject="Two")
    ui = {"compose": [{"id": "c1", "draft_id": a["draft"]["id"], "focused": False, "mode": "new", "to": [], "cc": [],
                       "bcc": [], "subject": "One", "body": "x", "base_version": 1, "dirty": False},
                      {"id": "c2", "draft_id": b["draft"]["id"], "focused": False, "mode": "new", "to": [], "cc": [],
                       "bcc": [], "subject": "Two", "body": "y", "base_version": 1, "dirty": False}]}
    async with sessionmaker()() as db:
        p = Principal(user=await db.get(User, user.id), scopes=set(ALL_SCOPES) - {"admin"})
        ctx = ToolCtx(db=db, p=p, assistant=True, chat_session_id="s", ui=ui)
        with pytest.raises(ToolError, match="Ask the user which one"):
            await run_tool(ctx, "update_draft", {"body": "shorter"})


async def test_wait_for_the_user_to_pause(monkeypatch):
    """On a fake clock: the waits are the subject, and a loaded machine must not stretch them."""
    from types import SimpleNamespace

    clock = SimpleNamespace(now=1000.0, typing=None)

    async def sleep(seconds):
        clock.now += seconds
        if clock.typing:
            live.saw_typing(1, "draft", clock.typing)  # the user types on during the wait

    monkeypatch.setattr(live, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(live, "asyncio", SimpleNamespace(sleep=sleep))

    live.saw_typing(1, "draft", "77")
    assert await live.wait_idle(1, "draft", 77, idle=0.3, timeout=2) is True
    assert 0.3 <= clock.now - 1000.0 < 0.4  # it waited for the pause, and not much longer

    live.saw_typing(1, "draft", "78")
    clock.typing, start = "78", clock.now
    assert await live.wait_idle(1, "draft", 78, idle=0.3, timeout=0.2) is False
    assert 0.2 <= clock.now - start < 0.3  # it gave up at the timeout


async def test_typing_reports_reach_the_hub(client, user, monkeypatch):
    from mailr.core import events

    _, me = await _setup_identity(user)
    made, _ = await _draft(client, me)
    sent = []

    async def rec(db, uid, ev):
        sent.append(ev)

    monkeypatch.setattr(events, "notify", rec)
    r = await client.post(f"{API}/live/typing", json={"kind": "draft", "id": made["draft"]["id"]})
    assert r.status_code == 200
    assert sent == [{"type": "typing", "kind": "draft", "id": str(made["draft"]["id"])}]
    # The hub keeps it for waiting writes and does not pass it to the streams.
    q = events.hub.subscribe(user.id)
    events.hub._dispatch(None, 0, "mailr_changes", json.dumps({"u": user.id, **sent[0]}))
    assert q.empty() and (user.id, "draft", str(made["draft"]["id"])) in live._typing
    events.hub.unsubscribe(user.id, q)


# ---------------------------------------------------------------- memory and filters


async def test_memory_versions_merge_and_conflict(client, user, notes):
    r = await client.post(f"{API}/assistant/memories", json={"text": "Alice prefers mornings."})
    assert r.status_code == 201, r.text
    m = r.json()["memory"]
    assert m["version"] == 1 and _items(notes, "memory")[-1]["version"] == 1
    r = await client.patch(f"{API}/assistant/memories/{m['id']}", json={"text": "Alice prefers early mornings.",
                                                               "base_version": 1})
    assert r.json()["memory"]["version"] == 2
    # From version 1: pinning merges with the text change ...
    r = await client.patch(f"{API}/assistant/memories/{m['id']}", json={"pinned": True, "base_version": 1})
    assert r.status_code == 200 and r.json()["memory"]["text"] == "Alice prefers early mornings."
    assert r.json()["memory"]["pinned"] is True
    # ... a second text change from version 1 is a conflict.
    r = await client.patch(f"{API}/assistant/memories/{m['id']}", json={"text": "Alice prefers afternoons.", "base_version": 1})
    assert r.status_code == 409 and r.json()["fields"] == ["text"]
    li = (await client.get(f"{API}/live/memory/{m['id']}")).json()
    assert li["version"] == 3 and li["content"]["text"] == "Alice prefers early mornings."


async def test_filter_versions_merge_and_conflict(client, user, notes):
    r = await client.post(f"{API}/rules", json={"name": "News", "query": "from:news@x.example",
                                                 "actions": {"archive": True}})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert r.json()["version"] == 1
    base = {"name": "News", "query": "from:news@x.example", "actions": {"archive": True}, "enabled": True,
            "stop": False}
    r = await client.patch(f"{API}/rules/{rid}", json={**base, "name": "Newsletters", "base_version": 1})
    assert r.json()["version"] == 2
    r = await client.patch(f"{API}/rules/{rid}", json={**base, "stop": True, "base_version": 1})
    assert r.status_code == 200 and r.json()["name"] == "Newsletters" and r.json()["stop"] is True
    r = await client.patch(f"{API}/rules/{rid}", json={**base, "name": "Letters", "base_version": 1})
    assert r.status_code == 409 and r.json()["fields"] == ["name"]
    assert _items(notes, "rule")[-1]["version"] == 3


# ---------------------------------------------------------------- events and tasks (Radicale)

needs_radicale = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


@needs_radicale
async def test_event_versions_merge_and_conflict(client, user, notes):
    await _connect_calendar(client)
    r = await client.post(f"{API}/calendar/events", json={
        "summary": "Budget review", "start": "2026-11-02T15:00:00+00:00", "end": "2026-11-02T16:00:00+00:00",
        "description": "Agenda:\n- numbers\n- plan\n"})
    assert r.status_code == 200, r.text
    ev = r.json()
    uid, v1 = ev["uid"], ev["version"]
    assert v1 == 1 and _items(notes, "event")[-1]["id"] == uid
    # The assistant moves it to another room from version 1 ...
    r = await client.patch(f"{API}/calendar/events/{uid}", json={"location": "Room 4", "base_version": v1,
                                                                  "description": "Agenda:\n- numbers\n- plan\n- AOB\n"})
    assert r.status_code == 200, r.text
    # ... while the user, still on version 1, renames it and edits the first agenda line.
    r = await client.patch(f"{API}/calendar/events/{uid}", json={
        "summary": "Q4 budget review", "description": "Agenda:\n- revised numbers\n- plan\n", "base_version": v1})
    assert r.status_code == 200, r.text
    got = (await client.get(f"{API}/live/event/{uid}")).json()
    assert got["version"] == 3
    c = got["content"]
    assert c["summary"] == "Q4 budget review" and c["location"] == "Room 4"
    assert c["description"] == "Agenda:\n- revised numbers\n- plan\n- AOB\n"
    r = await client.patch(f"{API}/calendar/events/{uid}", json={"location": "Room 9", "base_version": v1})
    assert r.status_code == 409 and r.json()["fields"] == ["location"]


@needs_radicale
async def test_task_versions_merge_and_conflict(client, user):
    """ACT task.edit"""
    await _connect_calendar(client)
    t = (await client.post(f"{API}/tasks", json={"summary": "Send numbers", "notes": "one\ntwo\n"})).json()
    uid, v1 = t["uid"], t["version"]
    assert v1 == 1
    r = await client.patch(f"{API}/tasks/{uid}", json={"notes": "one\ntwo\nthree\n", "base_version": v1})
    assert r.status_code == 200 and r.json()["version"] == 2
    r = await client.patch(f"{API}/tasks/{uid}", json={"notes": "ONE\ntwo\n", "priority": 1, "base_version": v1})
    assert r.status_code == 200, r.text
    got = (await client.get(f"{API}/live/task/{uid}")).json()["content"]
    assert got["notes"] == "ONE\ntwo\nthree\n" and got["priority"] == 1
    r = await client.patch(f"{API}/tasks/{uid}", json={"notes": "uno\ntwo\n", "base_version": v1})
    assert r.status_code == 409 and r.json()["fields"] == ["notes"] and r.json()["kind"] == "task"


# ---------------------------------------------------------------- the migration


def _migration():
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "c7e2a4f9b1d3_item_versions.py"
    spec = importlib.util.spec_from_file_location("mig_item_versions", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _upgrade(conn):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with Operations.context(MigrationContext.configure(conn)):
        _migration().upgrade()


async def test_migration_reruns_from_a_partial_state(user):
    """An upgrade stopped after the table but before its constraint and index (and the version row)
    runs again cleanly, and running it once more changes nothing."""
    from mailr.db import engine

    async with engine().begin() as conn:
        await conn.execute(text("ALTER TABLE item_versions DROP CONSTRAINT IF EXISTS uq_item_versions"))
        await conn.execute(text("DROP INDEX IF EXISTS ix_item_versions_created"))
        await conn.run_sync(_upgrade)
        await conn.run_sync(_upgrade)
        n = (await conn.execute(text(
            "SELECT count(*) FROM pg_constraint WHERE conname = 'uq_item_versions'"))).scalar()
        i = (await conn.execute(text("SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_item_versions_created'"))
             ).scalar()
        assert n == 1 and i == 1
    async with sessionmaker()() as db:
        assert (await db.execute(select(func.count()).select_from(ItemVersion))).scalar() >= 0
