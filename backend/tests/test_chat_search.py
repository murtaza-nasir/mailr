# ACT-SURFACES: api
"""Ambrose Chats: search (GET /agent/sessions?q=) and rename (PATCH /agent/sessions/{id}).

ACT agent.chat_search: the title, the user's questions and Ambrose's answers match (tool results
never do), newest match first, each result with a plain snippet whose hits are marked as parts;
the substring fallback; the same chats as the list (own chats only, a token sees only its own,
no errands).
ACT agent.chat_rename: a title the user gives is kept by later turns and an edited first question;
an empty title gives back the automatic one; at most 120 characters; the chat keeps its place.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from helpers.agent import _drop_endpoint, _endpoint, _events, _text_turn
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from mailr.agent.chat_search import parts, plain_line, snippet
from mailr.db import sessionmaker
from mailr.models import AuditLog, ChatMessage, ChatSession

# These tests never touch the mail store: database-only users (conftest.make_db_user).
pytestmark = pytest.mark.db_user


async def _chat(user_id: int, turns: list[tuple[str, str]], *, title: str | None = None, minutes_ago: int = 0,
                token_id: int | None = None, context: dict | None = None, tool: str | None = None) -> str:
    """A stored chat: (question, answer) pairs, its last activity `minutes_ago`."""
    at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    sid = "cs-" + uuid.uuid4().hex[:12]
    async with sessionmaker()() as db:
        db.add(ChatSession(id=sid, user_id=user_id, token_id=token_id, title=title or turns[0][0], sensitive=False,
                           context=context or {}, created_at=at, updated_at=at))
        await db.flush()
        for i, (qn, an) in enumerate(turns):
            when = at - timedelta(seconds=10 * (len(turns) - i))
            db.add(ChatMessage(session_id=sid, role="user", content=qn, status="done", created_at=when))
            if tool:
                db.add(ChatMessage(session_id=sid, role="assistant", content="", created_at=when,
                                   tool_calls=[{"id": "t1", "type": "function", "function": {"name": "x", "arguments": "{}"}}]))
                db.add(ChatMessage(session_id=sid, role="tool", content=tool, tool_call_id="t1", name="x", created_at=when))
            db.add(ChatMessage(session_id=sid, role="assistant", content=an, created_at=when))
        await db.commit()
    return sid


async def _search(client, q: str, **params):
    r = await client.get("/api/v1/agent/sessions", params={"q": q, **params})
    assert r.status_code == 200, r.text
    return r.json()


async def test_search_matches_title_question_and_answer(client, user):
    a = await _chat(user.id, [("Where is the Northwind invoice?", "The invoice from **Northwind** came on Monday [1](mailr-cite:1).")],
                    minutes_ago=30)
    b = await _chat(user.id, [("Plan the offsite", "I found three venues near the harbour.")], minutes_ago=20)
    c = await _chat(user.id, [("Hello", "Hi.")], title="Quarterly budget", minutes_ago=10)
    await _chat(user.id, [("Unrelated", "Nothing here.")], tool="harbour secret in a tool result", minutes_ago=5)

    rows = await _search(client, "northwind")
    assert [r["id"] for r in rows] == [a]
    assert rows[0]["match"] == "answer"  # the newest matching message is the answer
    assert rows[0]["snippet"] == "The invoice from Northwind came on Monday."  # plain: no Markdown, no citation
    assert [p for p in rows[0]["snippet_parts"] if p["hit"]] == [{"text": "Northwind", "hit": True}]

    rows = await _search(client, "harbour")  # an answer; the tool result of the other chat is not searched
    assert [r["id"] for r in rows] == [b] and rows[0]["match"] == "answer"

    rows = await _search(client, "offsite")
    assert [r["id"] for r in rows] == [b] and rows[0]["match"] == "question"

    rows = await _search(client, "quarterly")
    assert [r["id"] for r in rows] == [c]
    assert rows[0]["match"] == "title" and rows[0]["snippet"] is None
    assert rows[0]["title_parts"] == [{"text": "Quarterly", "hit": True}, {"text": " budget", "hit": False}]

    # The last word is a prefix (search as you type); the hit is the whole word.
    rows = await _search(client, "invo")
    assert [r["id"] for r in rows] == [a]
    assert {"text": "invoice", "hit": True} in rows[0]["snippet_parts"]

    # Every word must match.
    assert await _search(client, "northwind harbour") == []
    # The list fields are there too.
    assert rows[0]["title"] == "Where is the Northwind invoice?" and rows[0]["preview"] and rows[0]["updated_at"]


async def test_search_newest_match_first_and_substring_fallback(client, user):
    old = await _chat(user.id, [("Budget for spring", "Spring budget is 4k.")], minutes_ago=300)
    new = await _chat(user.id, [("Budget again", "Still 4k for the budget.")], minutes_ago=3)
    rows = await _search(client, "budget")
    assert [r["id"] for r in rows] == [new, old]

    mail = await _chat(user.id, [("Who wrote?", "dana.reyes@example.com wrote twice.")], minutes_ago=1)
    rows = await _search(client, "reyes")  # part of an address: full text has one token for it
    assert [r["id"] for r in rows] == [mail]
    assert {"text": "reyes", "hit": True} in rows[0]["snippet_parts"]
    assert await _search(client, "zzqx-nothing") == []


async def test_search_sees_what_the_list_sees(app, client, user):
    from mailr.core.users import create_user

    mine = await _chat(user.id, [("Pelican report", "Pelican numbers are up.")], minutes_ago=2)
    await _chat(user.id, [("Pelican errand", "Pelican errand done.")], context={"errand": 1}, minutes_ago=1)
    async with sessionmaker()() as db:
        other = await create_user(db, f"o-{uuid.uuid4().hex[:8]}@example.com", "Other", password="other-password-123")
    await _chat(other.id, [("Pelican elsewhere", "Pelican too.")], minutes_ago=1)

    r = await client.post("/api/v1/tokens", json={"name": "chats", "scopes": ["read"]})
    assert r.status_code == 200, r.text
    raw_tok, tok_id = r.json()["token"], r.json()["api_token"]["id"]
    by_token = await _chat(user.id, [("Pelican by token", "Pelican via the token.")], token_id=tok_id, minutes_ago=1)

    listed = {c["id"] for c in (await client.get("/api/v1/agent/sessions")).json()}
    found = {c["id"] for c in await _search(client, "pelican")}
    assert found == {mine} and mine in listed and by_token not in listed

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {raw_tok}"}) as t:
        r = await t.get("/api/v1/agent/sessions", params={"q": "pelican"})
        assert r.status_code == 200, r.text
        assert [c["id"] for c in r.json()] == [by_token]
        # The token cannot rename the user's own chats, and the user cannot rename the token's.
        assert (await t.patch(f"/api/v1/agent/sessions/{mine}", json={"title": "x"})).status_code == 404
        assert (await t.patch(f"/api/v1/agent/sessions/{by_token}", json={"title": "Token chat"})).status_code == 200
    assert (await client.patch(f"/api/v1/agent/sessions/{by_token}", json={"title": "x"})).status_code == 404


async def test_rename_keeps_title_and_empty_resets(client, user):
    sid = await _chat(user.id, [("What is due this week?", "Two invoices.")], minutes_ago=60)
    later = await _chat(user.id, [("Newer chat", "Yes.")], minutes_ago=1)

    r = await client.patch(f"/api/v1/agent/sessions/{sid}", json={"title": "  Weekly\n  dues  "})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": sid, "title": "Weekly dues", "renamed": True,
                        "previous": {"title": "What is due this week?", "renamed": False}}
    rows = (await client.get("/api/v1/agent/sessions")).json()
    assert [c["id"] for c in rows] == [later, sid]  # the rename does not move the chat up
    assert rows[1]["title"] == "Weekly dues" and rows[1]["renamed"] is True
    assert (await client.get(f"/api/v1/agent/sessions/{sid}")).json()["renamed"] is True
    assert [c["id"] for c in await _search(client, "weekly")] == [sid]

    # Too long: refused, nothing changes.
    r = await client.patch(f"/api/v1/agent/sessions/{sid}", json={"title": "x" * 121})
    assert r.status_code == 422 and r.json()["code"] == "assistant.chat_title_too_long"
    assert (await client.patch(f"/api/v1/agent/sessions/{sid}", json={"title": "y" * 120})).json()["title"] == "y" * 120

    # Empty: back to the automatic title (the first question).
    r = await client.patch(f"/api/v1/agent/sessions/{sid}", json={"title": "   "})
    assert r.json()["title"] == "What is due this week?" and r.json()["renamed"] is False
    assert r.json()["previous"] == {"title": "y" * 120, "renamed": True}

    async with sessionmaker()() as db:
        logged = (await db.execute(select(AuditLog).where(AuditLog.action == "agent.chat_rename",
                                                          AuditLog.target == sid))).scalars().all()
        assert len(logged) == 3

    assert (await client.patch("/api/v1/agent/sessions/nope", json={"title": "x"})).status_code == 404


async def test_renamed_title_survives_later_turns_and_edits(client, user, fake_llm):
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [_text_turn("First.")]
        r = await client.post("/api/v1/agent/chat", json={"message": "Original question"})
        evs = _events(r.text)
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        first = next(e for e in evs if e["type"] == "session")["user_message_id"]

        # Not renamed: editing the first question moves the automatic title with it.
        fake_llm.script = [_text_turn("Edited.")]
        r = await client.post("/api/v1/agent/chat", json={"session_id": sid, "edit_from": first, "message": "Edited question"})
        first = next(e for e in _events(r.text) if e["type"] == "session")["user_message_id"]
        assert (await client.get(f"/api/v1/agent/sessions/{sid}")).json()["title"] == "Edited question"

        assert (await client.patch(f"/api/v1/agent/sessions/{sid}", json={"title": "My name"})).status_code == 200
        fake_llm.script = [_text_turn("Second.")]
        await client.post("/api/v1/agent/chat", json={"session_id": sid, "message": "And another"})
        fake_llm.script = [_text_turn("Third.")]
        await client.post("/api/v1/agent/chat", json={"session_id": sid, "edit_from": first, "message": "Rewritten"})
        s = (await client.get(f"/api/v1/agent/sessions/{sid}")).json()
        assert s["title"] == "My name" and s["renamed"] is True
        # Back to automatic: the first question as it is now.
        r = await client.patch(f"/api/v1/agent/sessions/{sid}", json={"title": ""})
        assert r.json()["title"] == "Rewritten"
    finally:
        await _drop_endpoint(ep)


def test_snippet_cuts_around_the_hit():
    text = ("Intro words " * 20) + "the **Northwind** invoice is due on Friday, and " + ("more text " * 30)
    s = snippet(text, ["northwind"])
    joined = "".join(p["text"] for p in s)
    assert joined.startswith("…") and joined.endswith("…")
    assert len(joined) <= 170 and "Northwind" in joined and "**" not in joined
    assert [p["text"] for p in s if p["hit"]] == ["Northwind"]
    # A short text keeps all of it, no ellipses.
    assert snippet("Northwind paid.", ["north"]) == [{"text": "Northwind", "hit": True}, {"text": " paid.", "hit": False}]
    # Hits start at a word: "paid" is not a hit inside "unpaid".
    assert [p["text"] for p in parts("unpaid, paid", ["paid"]) if p["hit"]] == ["paid"]
    assert plain_line("- one\n- [two](https://x.example) [3](mailr-cite:3)") == "one two"
    assert snippet("", ["x"]) is None
