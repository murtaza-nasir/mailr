"""A time without an offset, from the assistant or a client, is in the user's zone (calendar/tz.py
`aware`), as the approval card shows it: send_at for the assistant's send_draft and POST /send,
snooze and follow-up `until`, a reminder's snooze, and a memory's expiry."""

from __future__ import annotations

from datetime import UTC, datetime

from helpers.agent import _seed_thread, _setup_identity
from sqlalchemy import select

from mailr.db import session_scope, sessionmaker
from mailr.models import Conversation, Memory, Outbox, User

API = "/api/v1"
# 09:00 in Chicago on a summer day is 14:00 UTC (CDT, UTC-05:00).
NAIVE = "2099-06-05T09:00:00"
UTC_EQ = datetime(2099, 6, 5, 14, 0, tzinfo=UTC)


async def _in_chicago(user) -> None:
    async with session_scope() as db:
        u = await db.get(User, user.id)
        u.settings = {**(u.settings or {}), "timezone": "America/Chicago"}


async def test_the_assistants_send_at_is_local(app, user, raw):
    await _in_chicago(user)
    _, me = await _setup_identity(user)
    m1, _conv = await _seed_thread(user, raw, me)
    from mailr.agent.tools import ToolCtx, run_tool
    from mailr.core.auth import ALL_SCOPES, Principal

    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        ctx = ToolCtx(db=db, p=Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"}), assistant=True,
                      chat_session_id="test-session")
        d = await run_tool(ctx, "create_draft", {"reply_to_message_id": m1, "body": "Friday works."})
        await run_tool(ctx, "send_draft", {"draft_id": d.data["draft_id"], "send_at": NAIVE})
        ob = (await db.execute(select(Outbox).where(Outbox.user_id == user.id))).scalar_one()
        assert ob.send_at == UTC_EQ


async def test_an_api_send_at_without_offset_is_local(client, user):
    await _in_chicago(user)
    await _setup_identity(user)
    ident = (await client.get(f"{API}/identities")).json()[0]
    r = await client.post(f"{API}/send", json={"payload": {
        "identity_id": ident["id"], "to": [{"email": "jon.reyes@example.org", "name": "Jon Reyes"}],
        "subject": "Agenda", "html": "<p>Attached.</p>"}, "send_at": NAIVE})
    assert r.status_code in (200, 202), r.text
    async with sessionmaker()() as db:
        assert (await db.get(Outbox, r.json()["outbox"]["id"])).send_at == UTC_EQ


async def test_a_snooze_until_is_local(client, user, raw):
    await _in_chicago(user)
    _, me = await _setup_identity(user)
    _m1, conv = await _seed_thread(user, raw, me)
    r = await client.post(f"{API}/conversations/actions", json={"ids": [conv], "action": "snooze", "until": NAIVE})
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        assert (await db.get(Conversation, conv)).snoozed_until == UTC_EQ


async def test_a_memorys_expiry_is_local(client, user):
    await _in_chicago(user)
    from mailr.core.auth import ALL_SCOPES, Principal
    from mailr.knowledge import memory

    async with session_scope() as db:
        u = await db.get(User, user.id)
        m, _ = await memory.remember(db, Principal(user=u, scopes=set(ALL_SCOPES)),
                                     {"text": "Prefers calls in the afternoon", "expires_at": "2099-06-05"})
        mid = m.id
    async with sessionmaker()() as db:
        assert (await db.get(Memory, mid)).expires_at == datetime(2099, 6, 5, 5, 0, tzinfo=UTC)
