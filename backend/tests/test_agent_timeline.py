"""A turn is an ordered timeline: interim text stays where it
was written, steps follow the text before them, an approval card sits after the steps that asked for
it and changes in place when decided, and the continuation joins the same turn.
ACT approval.approve"""

import json

from helpers.agent import (
    _drop_endpoint,
    _endpoint,
    _events,
    _ingest,
    _setup_identity,
    _text_turn,
    _tool_turn,
)
from helpers.agent_approvals import _decide

from mailr.agent.routes import timeline
from mailr.db import sessionmaker
from mailr.models import ChatMessage, ChatSession


def _text_tool_turn(text, name, args, call_id="c1"):
    return [{"choices": [{"delta": {"content": text}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": call_id, "function": {
                "name": name, "arguments": json.dumps(args)}}]}, "finish_reason": "tool_calls"}]}]


def _kinds(items):
    return [i["type"] for i in items]


async def test_interleaved_text_steps_and_approval_in_place(client, user, raw, fake_llm):
    _, me = await _setup_identity(user)
    _, conv = await _ingest(user.id, raw("Reddit reply", frm="Reddit <noreply@reddit.example>", to=me, body="hi"))
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [
            _text_tool_turn("First I look for the Reddit mail.", "search_conversations", {"q": "from:reddit.example"}, "c1"),
            _text_tool_turn("Found one. I will move it to Trash.", "modify_conversations",
                            {"conversation_ids": [conv], "action": "trash"}, "c2"),
            _text_turn("Approve the card above and it goes to Trash."),
        ]
        r = await client.post("/api/v1/agent/chat", json={"message": "Trash the Reddit replies"})
        evs = _events(r.text)
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        order = [(e["type"], e.get("segment")) for e in evs
                 if e["type"] in ("segment", "tool_start", "approval_required", "final")]
        assert order == [("segment", 0), ("tool_start", 0), ("segment", 1), ("tool_start", 1),
                         ("approval_required", 1), ("final", 2)]
        card = next(e for e in evs if e["type"] == "approval_required")
        assert card["approval"]["display"]["title"] == "Move 1 message to Trash"
        assert card["approval"]["outcome"]["text"] == "Waiting for approval"
        assert next(e for e in evs if e["type"] == "tool_start")["icon"] == "search"

        s = (await client.get(f"/api/v1/agent/sessions/{sid}")).json()
        a = s["turns"][1]
        assert _kinds(a["timeline"]) == ["text", "steps", "text", "steps", "approval", "text"]
        assert a["timeline"][0]["content"] == "First I look for the Reddit mail."
        assert a["timeline"][4]["approval"]["status"] == "pending"
        assert a["timeline"][3]["steps"][0]["icon"] == "label"

        # Decide, then the turn continues in the same timeline.
        await _decide(user.id, card["approval_id"])
        fake_llm.script = [_text_turn("Done — it is in Trash.")]
        r2 = await client.post("/api/v1/agent/chat", json={"session_id": sid, "resume_approval_id": card["approval_id"]})
        evs2 = _events(r2.text)
        sess = next(e for e in evs2 if e["type"] == "session")
        assert sess["continuation"] is True and sess["segment"] == 3
        upd = next(e for e in evs2 if e["type"] == "approval_update")
        o = upd["approval"]["outcome"]
        assert (o["state"], o["text"]) == ("executed", "Approved · moved 1 message to Trash")
        assert o["msg"]["key"] == "approvals.outcome.executed"  # the apps render it in their language
        assert next(e for e in evs2 if e["type"] == "final")["segment"] == 3

        s = (await client.get(f"/api/v1/agent/sessions/{sid}")).json()
        assert [t["role"] for t in s["turns"]] == ["user", "assistant"]  # no bubble for the note
        a = s["turns"][1]
        assert _kinds(a["timeline"]) == ["text", "steps", "text", "steps", "approval", "text", "resume", "text"]
        assert a["timeline"][4]["approval"]["outcome"]["text"] == "Approved · moved 1 message to Trash"
        assert a["timeline"][3]["steps"][0]["summary"] == "approved"
        assert a["timeline"][6]["text"] == "After your decision on “Move 1 message to Trash”"
        assert a["timeline"][7]["content"] == "Done — it is in Trash."
        assert a["content"] == "Done — it is in Trash." and a["status"] == "done"
        assert s["pending_approvals"] == []
    finally:
        await _drop_endpoint(ep)


async def test_denied_card_and_normalised_text(client, user, raw, fake_llm):
    _, me = await _setup_identity(user)
    _, conv = await _ingest(user.id, raw("Promo", frm="Shop <deals@shop.example>", to=me, body="Sale"))
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [_tool_turn("modify_conversations", {"conversation_ids": [conv], "action": "trash"}),
                           _text_turn("# Waiting\n\nApprove it above.")]
        r = await client.post("/api/v1/agent/chat", json={"message": "Trash it"})
        evs = _events(r.text)
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        assert next(e for e in evs if e["type"] == "final")["content"] == "### Waiting\n\nApprove it above."
        aid = next(e for e in evs if e["type"] == "approval_required")["approval_id"]
        r = await client.post(f"/api/v1/approvals/{aid}/decide", json={"decision": "deny"})
        assert r.json()["outcome"]["text"] == "Denied" and r.json()["display"]["icon"] == "trash"
        s = (await client.get(f"/api/v1/agent/sessions/{sid}")).json()
        ap = next(i for i in s["turns"][1]["timeline"] if i["type"] == "approval")
        assert ap["approval"]["outcome"]["state"] == "denied"
        listed = (await client.get("/api/v1/approvals", params={"status": "denied"})).json()
        assert listed[0]["display"]["title"] == "Move 1 message to Trash"
    finally:
        await _drop_endpoint(ep)


async def test_old_rows_stay_readable(user):
    """Rows from before segments and continuations: steps first, then the answer; an old resume
    turn joins the turn before it."""
    async with sessionmaker()() as db:
        s = ChatSession(id="old-timeline-1", user_id=user.id, title="old", sensitive=False, context={})
        db.add(s)
        await db.flush()
        rows = [
            ChatMessage(session_id=s.id, role="user", content="Trash the promos"),
            ChatMessage(session_id=s.id, role="assistant", content="", tool_calls=[{"id": "x", "type": "function",
                        "function": {"name": "search_conversations", "arguments": "{}"}}]),
            ChatMessage(session_id=s.id, role="tool", content="{}", tool_call_id="x", name="search_conversations"),
            ChatMessage(session_id=s.id, role="assistant", content="## Asked\nApprove it.",
                        steps=[{"id": "x", "name": "search_conversations", "label": "Searching", "ok": True}]),
            ChatMessage(session_id=s.id, role="user", content="[mailr: approval updates, not written by the user]\n- x"),
            ChatMessage(session_id=s.id, role="assistant", content="Done."),
        ]
        db.add_all(rows)
        await db.flush()
        turns = timeline(rows)
        await db.rollback()
    assert [t["role"] for t in turns] == ["user", "assistant"]
    a = turns[1]
    assert _kinds(a["timeline"]) == ["steps", "text", "resume", "text"]
    assert a["timeline"][0]["steps"][0]["icon"] == "search"
    assert a["timeline"][1]["content"] == "### Asked\nApprove it."
    assert a["content"] == "Done."


def test_chat_preview_is_one_plain_line():
    from mailr.agent.routes import chat_preview

    assert chat_preview("**Priya** is free on *Tuesday* [1](mailr-cite:1).\n\n- See [the invite](https://x.example)") == \
        "Priya is free on Tuesday. See the invite"
    assert chat_preview("[[m:12 \"quoted\"]]   ") is None
    long = "word " * 60
    p = chat_preview(long)
    assert p.endswith("…") and len(p) <= 141 and "  " not in p


async def test_chat_list_has_each_chats_latest_answer(client, user, fake_llm):
    """GET /agent/sessions (the Android list of chats): title, last activity and a preview line,
    the start of the latest final answer (not the text written before a tool call)."""
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [_text_turn("First answer.")]
        r = await client.post("/api/v1/agent/chat", json={"message": "Hello there"})
        sid = next(e for e in _events(r.text) if e["type"] == "session")["session_id"]
        fake_llm.script = [_text_tool_turn("Looking.", "search_conversations", {"q": "invoice"}),
                           _text_turn("The **latest** invoice is from Northwind [1](mailr-cite:1).")]
        await client.post("/api/v1/agent/chat", json={"message": "Find the invoice", "session_id": sid})
        fake_llm.script = [_text_turn("Second chat.")]
        await client.post("/api/v1/agent/chat", json={"message": "Another"})
        rows = (await client.get("/api/v1/agent/sessions")).json()
        assert [c["title"] for c in rows] == ["Another", "Hello there"]
        assert rows[1]["preview"] == "The latest invoice is from Northwind."
        assert rows[0]["preview"] == "Second chat."
        assert rows[0]["updated_at"]
    finally:
        await _drop_endpoint(ep)
