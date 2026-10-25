# ACT-SURFACES: api
"""Slash commands in the assistant chat (agent/commands.py).

Covers agent.commands (GET /agent/commands, and commands sent to POST /agent/chat) and
agent.chat_retitle (POST /agent/sessions/{id}/retitle)."""

from __future__ import annotations

from helpers.agent import _drop_endpoint, _endpoint, _events, _text_turn, fake_llm  # noqa: F401
from sqlalchemy import select

from mailr.agent import commands
from mailr.db import sessionmaker
from mailr.intel import llm
from mailr.models import ChatMessage, ChatSession


def test_parse_takes_a_leading_command_only():
    cmd, text = commands.parse("/task call the bank fri 3pm")
    assert cmd.name == "task" and text == "call the bank fri 3pm"
    assert commands.parse("/clear")[0].name == "new"  # an alias
    assert commands.parse("  /Today ")[0].name == "today"
    assert commands.parse("what about /today") is None  # not at the start
    assert commands.parse("//today") is None  # // is a skill
    assert commands.parse("/formal reply to Dana") is None  # a skill, not a command
    assert commands.parse("/todayx") is None


def test_every_command_has_its_texts():
    for c in commands.COMMANDS:
        assert commands.summary(c) != f"assistant.commands.{c.name}"
        if c.arg == "required" and c.kind == "ask":
            assert commands.reply_for(c, "").endswith(f"/{c.name} " + commands.listing()[[x.name for x in commands.COMMANDS].index(c.name)]["example"].split(" ", 1)[1])


async def test_the_list_is_served_in_picker_order(client):
    r = await client.get("/api/v1/agent/commands")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["name"] for i in items] == [c.name for c in commands.COMMANDS]
    new = items[0]
    assert new["aliases"] == ["clear"] and new["kind"] == "app" and new["summary"] == "Start a new chat"
    remember = next(i for i in items if i["name"] == "remember")
    assert "{{" not in remember["summary"] and remember["example"].startswith("/remember ")


async def test_an_ask_command_becomes_a_request(client, user, fake_llm):
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [_text_turn("Here is your day.")]
        r = await client.post("/api/v1/agent/chat", json={"message": "/today"})
        evs = _events(r.text)
        assert not [e for e in evs if e["type"] == "error"], evs
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        asked = fake_llm.requests[-1]["body"]["messages"][-1]["content"]
        assert "overview of today" in asked and "/today" not in asked
        async with sessionmaker()() as db:
            first = (await db.execute(select(ChatMessage.content).where(
                ChatMessage.session_id == sid, ChatMessage.role == "user"))).scalars().first()
        assert first == "/today"  # what the user typed stays in the chat

        # With text: the text goes into the request.
        fake_llm.script = [_text_turn("Added.")]
        r = await client.post("/api/v1/agent/chat", json={"message": "/task call the bank fri 3pm", "session_id": sid})
        assert "Create a task: call the bank fri 3pm" in fake_llm.requests[-1]["body"]["messages"][-1]["content"]
    finally:
        await _drop_endpoint(ep)


async def test_commands_the_server_answers_itself(client, user, fake_llm):
    ep = await _endpoint(user.id, "local")
    try:
        before = len(fake_llm.requests)
        r = await client.post("/api/v1/agent/chat", json={"message": "/task"})
        err = next(e for e in _events(r.text) if e["type"] == "error")
        assert err["code"] == "command" and "/task call the bank" in err["message"]

        r = await client.post("/api/v1/agent/chat", json={"message": "/clear"})
        err = next(e for e in _events(r.text) if e["type"] == "error")
        assert err["code"] == "command" and "/new" in err["message"] and "web" in err["message"]

        r = await client.post("/api/v1/agent/chat", json={"message": "/model"})
        err = next(e for e in _events(r.text) if e["type"] == "error")
        assert err["code"] == "command" and "local model" in err["message"]
        assert len(fake_llm.requests) == before  # no model call, and no chat was made
        async with sessionmaker()() as db:
            assert not (await db.execute(select(ChatSession.id).where(ChatSession.user_id == user.id))).first()
    finally:
        await _drop_endpoint(ep)


async def test_a_skill_cannot_take_a_command_name(client):
    r = await client.post("/api/v1/assistant/skills", json={"name": "today", "title": "Today", "instructions": "x"})
    assert r.status_code in (400, 422), r.text
    assert "chat command" in r.text


async def test_local_only_makes_the_chat_sensitive(client, user, fake_llm):
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [_text_turn("ok")]
        r = await client.post("/api/v1/agent/chat", json={"message": "hello", "local_only": True})
        sid = next(e for e in _events(r.text) if e["type"] == "session")["session_id"]
        async with sessionmaker()() as db:
            assert (await db.get(ChatSession, sid)).sensitive is True
    finally:
        await _drop_endpoint(ep)


async def test_retitle_makes_a_new_short_title(client, user, fake_llm, monkeypatch):
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [_text_turn("Here is your week.")]
        r = await client.post("/api/v1/agent/chat", json={"message": "/week"})
        sid = next(e for e in _events(r.text) if e["type"] == "session")["session_id"]
        await client.patch(f"/api/v1/agent/sessions/{sid}", json={"title": "Mine"})
        asked = []

        async def fake_chat(db, u, messages, **kw):
            asked.append(messages[-1]["content"])
            return {"content": "This week's plan"}

        monkeypatch.setattr(llm, "chat", fake_chat)
        r = await client.post(f"/api/v1/agent/sessions/{sid}/retitle")
        assert r.status_code == 200, r.text
        assert r.json()["title"] == "This week's plan" and r.json()["renamed"] is False
        assert r.json()["previous"] == {"title": "Mine", "renamed": True}
        assert "overview of this week" in asked[0]  # the request, not "/week"

        async def no_model(db, u, messages, **kw):
            raise llm.NoModel("chat")

        monkeypatch.setattr(llm, "chat", no_model)
        r = await client.post(f"/api/v1/agent/sessions/{sid}/retitle")
        assert r.status_code == 503
        async with sessionmaker()() as db:
            assert (await db.get(ChatSession, sid)).title == "This week's plan"
    finally:
        await _drop_endpoint(ep)
