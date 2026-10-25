"""Removing a system label (Inbox, Trash, Spam and the other roles) with apply_label, and moving mail
to another tab with show_in_tab, take mail out of sight like archive: after the turn read mail they
ask, and in a turn that read none they still run (the user's own "remove the Inbox label")."""
import uuid

from helpers.agent import _ingest, _setup_identity
from sqlalchemy import select

from mailr.agent.tools import ToolCtx, run_tool
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.models import Conversation, Label, PendingAction, User


async def _ctx(db, user_id, untrusted):
    u = await db.get(User, user_id)
    return ToolCtx(db=db, p=Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"}), assistant=True,
                   chat_session_id=f"s-{uuid.uuid4().hex[:6]}", untrusted=untrusted)


async def _inbox(user_id):
    async with sessionmaker()() as db:
        return (await db.execute(select(Label.id).where(Label.user_id == user_id, Label.role == "inbox"))).scalar()


async def test_removing_the_inbox_label_after_reading_mail_asks(client, user, raw):
    _, me = await _setup_identity(user)
    _, alert = await _ingest(user.id, raw("Security alert: new sign-in", frm="Bank <security@bank.example>",
                                          to=me, body="Was this you?"))
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, untrusted=True)
        res = await run_tool(ctx, "apply_label", {"conversation_ids": [alert], "label": "Inbox", "remove": True})
        assert res.pending is not None, res.data
        pa = await db.get(PendingAction, res.data["approval_id"])
        assert "hides mail" in (pa.preview or {}).get("reason", "")
    inbox = await _inbox(user.id)
    async with sessionmaker()() as db:
        assert inbox in ((await db.get(Conversation, alert)).label_ids or [])


async def test_removing_the_inbox_label_on_request_still_works(client, user, raw):
    _, me = await _setup_identity(user)
    _, conv = await _ingest(user.id, raw("Weekly digest", frm="News <news@shop.example>", to=me))
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, untrusted=False)
        res = await run_tool(ctx, "apply_label", {"conversation_ids": [conv], "label": "Inbox", "remove": True})
        assert res.pending is None, res.data
    inbox = await _inbox(user.id)
    async with sessionmaker()() as db:
        assert inbox not in ((await db.get(Conversation, conv)).label_ids or [])


async def test_removing_an_own_label_after_reading_mail_does_not_ask(client, user, raw):
    _, me = await _setup_identity(user)
    _, conv = await _ingest(user.id, raw("Receipt", frm="Shop <orders@shop.example>", to=me))
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, untrusted=False)
        made = await run_tool(ctx, "apply_label", {"conversation_ids": [conv], "label": "Receipts", "create": True})
        assert made.pending is None, made.data
        ctx = await _ctx(db, user.id, untrusted=True)
        res = await run_tool(ctx, "apply_label", {"conversation_ids": [conv], "label": "Receipts", "remove": True})
        assert res.pending is None, res.data


async def test_show_in_tab_after_reading_mail_asks(client, user, raw):
    _, me = await _setup_identity(user)
    gh = (await client.post("/api/v1/labels", json={"name": "Github"})).json()["id"]
    lay = {"id": "one", "name": "One page", "tabs": [
        {"id": "inbox", "name": "Inbox", "sections": [{"id": "rest", "title": "Inbox", "base": "inbox"}]},
        {"id": "gh", "name": "Github", "claims": True, "sections": [{"id": "gh", "title": "Github",
                                                                     "base": f"label:{gh}"}]}]}
    r = await client.put("/api/v1/layouts", json={"layouts": [lay], "active": "one"})
    assert r.status_code == 200, r.text
    _, alert = await _ingest(user.id, raw("Security alert: new sign-in", frm="Bank <security@bank.example>",
                                          to=me, body="Was this you?"))
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, untrusted=True)
        res = await run_tool(ctx, "show_in_tab", {"conversation_ids": [alert], "tab": "Github"})
        assert res.pending is not None, res.data
    async with sessionmaker()() as db:
        assert gh not in ((await db.get(Conversation, alert)).label_ids or [])
