"""Short titles for the chat list (agent/chat_title.py)."""

from __future__ import annotations

from helpers.agent import _drop_endpoint, _endpoint, _events, _text_turn, fake_llm  # noqa: F401

from mailr.agent import chat_title
from mailr.intel import llm


def test_clean_keeps_a_short_title_only():
    assert chat_title.clean("Fitness log entry") == "Fitness log entry"
    assert chat_title.clean('"weekly groceries."') == "Weekly groceries"
    assert chat_title.clean("Title: Sous-vide pork loin note") == "Sous-vide pork loin note"
    assert chat_title.clean("<think>hmm</think>\nCalendar this week") == "Calendar this week"
    assert chat_title.clean("") is None
    assert chat_title.clean("One\nTwo") is None  # an answer, not a title
    assert chat_title.clean("This is far too long to be a title for a chat in the list") is None


def _titler(monkeypatch, answer="Fitness log entry", fail=False):
    calls = []

    async def fake_chat(db, user, messages, **kw):
        calls.append({"messages": messages, **kw})
        if fail:
            raise llm.NoModel("chat")
        return {"content": answer}

    monkeypatch.setattr(llm, "chat", fake_chat)
    return calls


async def _sessions(client):
    r = await client.get("/api/v1/agent/sessions")
    assert r.status_code == 200, r.text
    return {s["id"]: s for s in r.json()}


async def test_a_new_chat_gets_a_short_title(client, user, fake_llm, monkeypatch):
    calls = _titler(monkeypatch)
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [_text_turn("Done, the entry is in your table.")]
        r = await client.post("/api/v1/agent/chat", json={"message": "Add 75.3 to My Fitness routine table for today"})
        evs = _events(r.text)
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        assert next(e for e in evs if e["type"] == "title")["title"] == "Fitness log entry"
        assert [e["type"] for e in evs][-1] == "done"  # the title comes before the end of the turn
        assert (await _sessions(client))[sid]["title"] == "Fitness log entry"
        # The question was asked with the chat's own task and sensitivity, and no tools.
        assert len(calls) == 1 and calls[0]["task"] == "chat" and calls[0]["sensitive"] is False
        assert "Fitness routine" in calls[0]["messages"][-1]["content"]

        # Later turns keep it; no second title is made.
        fake_llm.script = [_text_turn("Sure.")]
        r = await client.post("/api/v1/agent/chat", json={"message": "And note that my leg hurts", "session_id": sid})
        assert not [e for e in _events(r.text) if e["type"] == "title"]
        assert len(calls) == 1

        # A name the user gives wins; clearing it goes back to the short title, not the question.
        r = await client.patch(f"/api/v1/agent/sessions/{sid}", json={"title": "Leg"})
        assert r.json()["title"] == "Leg"
        r = await client.patch(f"/api/v1/agent/sessions/{sid}", json={"title": ""})
        assert r.json()["title"] == "Fitness log entry" and r.json()["renamed"] is False
    finally:
        await _drop_endpoint(ep)


async def test_without_a_title_the_question_stays(client, user, fake_llm, monkeypatch):
    _titler(monkeypatch, fail=True)
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [_text_turn("It's 5:50 PM.")]
        r = await client.post("/api/v1/agent/chat", json={"message": "What time is it"})
        evs = _events(r.text)
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        assert not [e for e in evs if e["type"] == "title"]
        assert (await _sessions(client))[sid]["title"] == "What time is it"
    finally:
        await _drop_endpoint(ep)


async def test_a_bad_answer_is_not_a_title(client, user, fake_llm, monkeypatch):
    _titler(monkeypatch, answer="Sure! Here is a title for your chat:\nFitness")
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [_text_turn("Done.")]
        r = await client.post("/api/v1/agent/chat", json={"message": "Add cheese to my shopping list"})
        evs = _events(r.text)
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        assert not [e for e in evs if e["type"] == "title"]
        assert (await _sessions(client))[sid]["title"] == "Add cheese to my shopping list"
    finally:
        await _drop_endpoint(ep)
