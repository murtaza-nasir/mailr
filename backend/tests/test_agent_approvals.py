"""The built-in assistant and the permission engine: approval cards, resuming, session grants."""

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
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import Label, Message


async def _in_trash(user_id, mid):
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        trash = (await db.execute(select(Label.id).where(Label.user_id == user_id, Label.role == "trash"))).scalar()
        return trash in (m.label_ids or [])


async def test_trash_needs_approval_then_resumes(client, user, raw, fake_llm):
    _, me = await _setup_identity(user)
    mid, conv = await _ingest(user.id, raw("Old promo", frm="Shop <deals@shop.example>", to=me, body="Sale!"))
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [
            _tool_turn("modify_conversations", {"conversation_ids": [conv], "action": "trash"}),
            _text_turn("I asked for your approval to move it to Trash."),
        ]
        r = await client.post("/api/v1/agent/chat", json={"message": "Trash the old promo"})
        evs = _events(r.text)
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        card = next(e for e in evs if e["type"] == "approval_required")
        assert card["action"] == "mail.trash" and card["title"] == "Move 1 message to Trash"
        tool = next(e for e in evs if e["type"] == "tool")
        assert tool["summary"] == "waiting for your approval"
        assert not await _in_trash(user.id, mid)  # nothing happened yet
        # The model was told it is pending (and not to retry).
        told = fake_llm.requests[-1]["body"]["messages"][-1]
        assert told["role"] == "tool" and "pending_approval" in told["content"]

        await _decide(user.id, card["approval_id"])
        assert await _in_trash(user.id, mid)

        fake_llm.script = [_text_turn("Done: it is in Trash now.")]
        r2 = await client.post("/api/v1/agent/chat", json={"session_id": sid,
                                                           "resume_approval_id": card["approval_id"]})
        evs2 = _events(r2.text)
        assert evs2[-1]["type"] == "done", evs2
        note = fake_llm.requests[-1]["body"]["messages"][-1]
        assert note["role"] == "user" and "approved and done" in note["content"] and "[mailr:" in note["content"]
    finally:
        await _drop_endpoint(ep)


async def test_session_grant_allows_later_requests_in_that_chat(client, user, raw, fake_llm):
    _, me = await _setup_identity(user)
    m1, c1 = await _ingest(user.id, raw("Promo A", frm="a@shop.example", to=me, body="A"))
    m2, c2 = await _ingest(user.id, raw("Promo B", frm="b@shop.example", to=me, body="B"))
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [_tool_turn("modify_conversations", {"conversation_ids": [c1], "action": "trash"}),
                           _text_turn("Waiting for approval.")]
        evs = _events((await client.post("/api/v1/agent/chat", json={"message": "trash A"})).text)
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        card = next(e for e in evs if e["type"] == "approval_required")
        await _decide(user.id, card["approval_id"], scope="session")
        fake_llm.script = [_tool_turn("modify_conversations", {"conversation_ids": [c2], "action": "trash"}, "c9"),
                           _text_turn("Trashed B too.")]
        evs2 = _events((await client.post("/api/v1/agent/chat", json={"message": "and B", "session_id": sid})).text)
        assert not [e for e in evs2 if e["type"] == "approval_required"]
        assert await _in_trash(user.id, m2)
        # A different chat is not covered by that grant.
        fake_llm.script = [_tool_turn("modify_conversations", {"conversation_ids": [c2], "action": "untrash"}),
                           _text_turn("Asked.")]
        evs3 = _events((await client.post("/api/v1/agent/chat", json={"message": "restore B"})).text)
        assert [e for e in evs3 if e["type"] == "approval_required"]
    finally:
        await _drop_endpoint(ep)
