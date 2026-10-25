"""A card that a hard rule held where the rules would allow (a write after the turn read mail,
hiding mail after reading it) is decided once: it takes no "Allow for this chat" or "Always allow",
the card says so (`grants`), and a grant made on another card does not decide it. Cards that ask
only because their class asks by default still take grants."""
import uuid

import pytest
from fastapi import HTTPException

from mailr.agent.present import approval_card
from mailr.agent.tools import ToolCtx, run_tool
from mailr.core import permissions
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.models import PendingAction, PermissionRule, User


async def _ctx(db, user_id, chat, untrusted):
    u = await db.get(User, user_id)
    return ToolCtx(db=db, p=Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"}), assistant=True,
                   chat_session_id=chat, untrusted=untrusted)


async def test_card_held_after_reading_mail_takes_no_grant(client, user):
    chat = f"s-{uuid.uuid4().hex[:6]}"
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, chat, untrusted=True)
        first = await run_tool(ctx, "create_note", {"title": "From the email", "body": "text the email dictated"})
        assert first.pending is not None
        pa = await db.get(PendingAction, first.data["approval_id"])
        assert "after reading" in (pa.preview or {}).get("reason", "")
        assert (await approval_card(db, pa))["grants"] is False
        u = await db.get(User, user.id)
        for scope in ("always", "session"):
            with pytest.raises(HTTPException) as e:
                await permissions.decide_full(db, u, pa.id, "approve", scope)
            assert e.value.status_code == 400
        res = await permissions.decide_full(db, u, pa.id, "approve", "once")
        await db.commit()
        assert res.rule_id is None
        again = await run_tool(ctx, "create_note", {"title": "Second", "body": "more dictated text"})
        assert again.pending is not None  # the next write after reading mail still asks


async def test_grant_on_another_card_does_not_decide_a_held_one(client, user):
    chat = f"s-{uuid.uuid4().hex[:6]}"
    async with sessionmaker()() as db:
        held = await run_tool(await _ctx(db, user.id, chat, untrusted=True), "create_note",
                              {"title": "Injected", "body": "text the email dictated"})
        assert held.pending is not None
        # The user's own rule makes the assistant's new notes ask: that card takes a grant.
        db.add(PermissionRule(user_id=user.id, subject="assistant", action="notes.create", effect="ask",
                              constraints={}))
        await db.commit()
        ask = await run_tool(await _ctx(db, user.id, chat, untrusted=False), "create_note", {"title": "Groceries"})
        assert ask.pending is not None
        pa = await db.get(PendingAction, ask.data["approval_id"])
        assert (await approval_card(db, pa))["grants"] is True
        u = await db.get(User, user.id)
        res = await permissions.decide_full(db, u, pa.id, "approve", "session")
        await db.commit()
        assert res.rule_id is not None and not res.also
        still = await db.get(PendingAction, held.data["approval_id"])
        await db.refresh(still)
        assert still.status == "pending"


async def test_card_that_asks_by_class_default_takes_grants(client, user):
    chat = f"s-{uuid.uuid4().hex[:6]}"
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, chat, untrusted=False)
        made = await run_tool(ctx, "table_schema", {"action": "create", "name": "Trips",
                                                    "columns": [{"name": "Place", "type": "text"}]})
        ask = await run_tool(ctx, "table_schema", {"table": made.data["table"], "action": "rename", "name": "Holidays"})
        assert ask.pending is not None
        pa = await db.get(PendingAction, ask.data["approval_id"])
        assert (await approval_card(db, pa))["grants"] is True


async def test_token_text_write_card_keeps_its_grants(client, user):
    from helpers.agent import _token_client

    tok = (await client.post("/api/v1/tokens", json={"name": "notes-bot", "scopes": ["read", "notes"]})).json()["token"]
    async with _token_client(client, tok) as a:
        r = await a.post("/api/v1/agent/tools/create_note", json={"title": "From the bot", "body": "Some text"})
        assert r.status_code == 202, r.text
        aid = r.json()["result"]["approval_id"]
    async with sessionmaker()() as db:
        pa = await db.get(PendingAction, int(aid))
        assert (await approval_card(db, pa))["grants"] is True
