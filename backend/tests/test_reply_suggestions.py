# ACT-SURFACES: api
"""mail.reply_suggestions: GET /messages/{mid}/reply-suggestions.
ACT mail.reply_suggestions"""

from helpers.core_flow import _ingest

from mailr.db import sessionmaker
from mailr.intel import llm
from mailr.intel.routes import parse_replies
from mailr.models import Message


def test_parse_replies():
    assert parse_replies('{"replies": ["Sounds good, thanks!", "Can we do Friday?", "sounds good, thanks!"]}') \
        == ["Sounds good, thanks!", "Can we do Friday?"]
    assert parse_replies('<think>x</think>```json\n{"replies": ["Yes", "Dear [Name]", "No"]}\n```') == ["Yes", "No"]
    assert parse_replies("not json") == []


async def test_suggestions_for_personal_mail_only_and_kept(client, user, raw, monkeypatch):
    calls = []

    systems = []

    async def fake_chat(db, u, messages, *, sensitive, **kw):
        calls.append(sensitive)
        systems.append(messages[0]["content"])
        return {"content": '{"replies": ["Sounds good, see you then.", "Can we move it to Friday?", "Let me check and get back to you."]}'}

    async def yes(*a, **kw):
        return True

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(llm, "available", yes)
    mid, _ = await _ingest(user, raw("Lunch Thursday?", frm="Dana <dana@example.org>", body="Are you free Thursday?"),
                           roles=["inbox"])
    # Ambrose's note about Dana goes with the request, and the answer names it (Used memories).
    note = (await client.post("/api/v1/assistant/memories", json={
        "text": "Dana is your running partner", "about": {"name": "Dana", "emails": ["dana@example.org"]}})).json()
    got = (await client.get(f"/api/v1/messages/{mid}/reply-suggestions")).json()
    assert got == {"status": "ready", "replies": ["Sounds good, see you then.", "Can we move it to Friday?",
                                                  "Let me check and get back to you."],
                   "memory_ids": [note["memory"]["id"]]}
    assert "Dana is your running partner" in systems[0]
    assert (await client.get(f"/api/v1/messages/{mid}/reply-suggestions")).json() == got
    assert calls == [False]  # the second answer came from the cache

    news, _ = await _ingest(user, raw("Weekly digest", frm="News <noreply@news.example>"), roles=["inbox"])
    assert (await client.get(f"/api/v1/messages/{news}/reply-suggestions")).json()["status"] == "none"
    lst, _ = await _ingest(user, raw("List post", frm="Bob <bob@example.org>", extra="List-Id: <dev.lists.example>"),
                           roles=["inbox"])
    assert (await client.get(f"/api/v1/messages/{lst}/reply-suggestions")).json()["status"] == "none"

    local, _ = await _ingest(user, raw("Grades", frm="Dean <dean@example.edu>"), roles=["inbox"])
    async with sessionmaker()() as db:
        (await db.get(Message, local)).local_only = True
        await db.commit()
    await client.get(f"/api/v1/messages/{local}/reply-suggestions")
    assert calls[-1] is True
