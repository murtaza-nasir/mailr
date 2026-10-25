# ACT-SURFACES: api assistant
"""The catalogued mail actions through the API and the assistant's tools.

Covers (api): mail.archive, mail.delete, mail.spam, mail.not_spam, mail.read, mail.unread, mail.star,
mail.important, mail.snooze, mail.label, mail.move, mail.mute, mail.to_task, mail.to_event, draft.save,
draft.discard, draft.send, draft.schedule, order.create, order.edit, order.pause, order.delete, order.test,
order.run, brief.read, approval.approve, approval.deny.
Covers (assistant): mail.archive, mail.delete, mail.label, mail.read, mail.unread, mail.star, mail.important,
mail.snooze, mail.reply, mail.to_task, mail.to_event, draft.save, draft.send, order.edit, order.pause,
order.delete.

Nothing here can send: the identity's connector is disabled and no worker runs the outbox.
"""

from datetime import UTC, datetime, timedelta

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.agent import _setup_identity
from helpers.calendar import _connect
from helpers.core_flow import _ingest
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import AssistantOrder, Conversation, Label, Outbox

radicale = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


async def _roles(user_id):
    from sqlalchemy import select

    async with sessionmaker()() as db:
        return dict((await db.execute(select(Label.role, Label.id).where(Label.user_id == user_id,
                                                                         Label.role.is_not(None)))).all())


async def _conv(cid):
    async with sessionmaker()() as db:
        return await db.get(Conversation, cid)


async def test_api_conversation_actions(client, user, raw):
    roles = await _roles(user.id)
    lb = (await client.post("/api/v1/labels", json={"name": "Clients"})).json()["id"]
    other = (await client.post("/api/v1/labels", json={"name": "Old"})).json()["id"]

    async def act(cid, action, **kw):
        r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": action, **kw})
        assert r.status_code == 200, (action, r.text)
        return await _conv(cid)

    _, cid = await _ingest(user, raw("Quarterly numbers", frm="Dana <dana@example.org>"), roles=["inbox"])
    assert (await act(cid, "read")).unread_count == 0                      # mail.read
    assert (await act(cid, "unread")).unread_count == 1                    # mail.unread
    assert (await act(cid, "star")).starred                                # mail.star
    assert (await act(cid, "important")).important                         # mail.important
    assert lb in (await act(cid, "add_labels", label_ids=[lb])).label_ids  # mail.label
    c = await act(cid, "move", label_ids=[other], from_label_id=lb)        # mail.move
    assert other in c.label_ids and lb not in c.label_ids
    assert (await act(cid, "mute")).muted                                  # mail.mute
    assert roles["inbox"] not in (await act(cid, "archive")).label_ids     # mail.archive
    until = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert (await act(cid, "snooze", until=until)).snoozed_until is not None  # mail.snooze
    assert (await act(cid, "spam")).label_ids == [roles["junk"]]           # mail.spam
    assert roles["inbox"] in (await act(cid, "not_spam")).label_ids        # mail.not_spam
    assert roles["trash"] in (await act(cid, "trash")).label_ids           # mail.delete


async def test_api_drafts(client, user):
    await _setup_identity(user)
    r = await client.post("/api/v1/drafts", json={"payload": {"to": [{"email": "a@example.org"}], "subject": "Hi", "text": "x"}})
    assert r.status_code in (200, 201), r.text                              # draft.save
    did = r.json()["draft"]["id"]
    assert (await client.delete(f"/api/v1/drafts/{did}")).status_code in (200, 204)  # draft.discard
    later = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    r = await client.post("/api/v1/send", json={"payload": {"to": [{"email": "a@example.org"}], "subject": "Later",
                                                             "text": "x"}, "send_at": later})
    assert r.status_code in (200, 202), r.text                              # draft.schedule
    ob = r.json()["outbox"]
    assert ob["status"] in ("queued", "scheduled", "awaiting_approval") and ob.get("send_at"), ob


async def test_api_orders_lifecycle(client, user, raw):
    """order.create, order.edit, order.pause, order.test, order.run, order.delete."""
    r = await client.post("/api/v1/assistant/orders", json={"name": "Jobs", "query": "from:jobs@example.org",
                                                            "actions": {"star": True}})
    oid = r.json()["id"]
    assert (await client.patch(f"/api/v1/assistant/orders/{oid}", json={"name": "Jobs!"})).json()["name"] == "Jobs!"
    assert (await client.patch(f"/api/v1/assistant/orders/{oid}", json={"enabled": False})).json()["enabled"] is False
    _, cid = await _ingest(user, raw("Job alert", frm="Jobs <jobs@example.org>"), roles=["inbox"])
    t = (await client.post("/api/v1/assistant/orders/preview", json={"query": "from:jobs@example.org", "scope": "all"})).json()
    assert t["matched"] >= 1
    run = (await client.post(f"/api/v1/assistant/orders/{oid}/run", json={"conversation_ids": [cid]})).json()
    assert run["done"] == 1 and (await _conv(cid)).starred
    assert (await client.delete(f"/api/v1/assistant/orders/{oid}")).status_code == 200
    assert (await client.get(f"/api/v1/conversations/{cid}/brief")).json()["brief"] is None  # brief.read


async def test_assistant_tools_do_the_same(client, user, raw):
    await _setup_identity(user)
    roles = await _roles(user.id)
    lb = (await client.post("/api/v1/labels", json={"name": "Receipts"})).json()["id"]
    _, cid = await _ingest(user, raw("Invoice", frm="Shop <shop@example.org>"), roles=["inbox"])

    async def tool(name, args):
        r = await client.post(f"/api/v1/agent/tools/{name}", json=args)
        assert r.status_code == 200, (name, r.text)
        return r.json()

    for action in ("read", "unread", "star", "important"):
        await tool("modify_conversations", {"conversation_ids": [cid], "action": action})
    c = await _conv(cid)
    assert c.starred and c.important and c.unread_count == 1
    await tool("modify_conversations", {"conversation_ids": [cid], "action": "snooze",
                                        "until": (datetime.now(UTC) + timedelta(hours=3)).isoformat()})
    assert (await _conv(cid)).snoozed_until is not None
    await tool("apply_label", {"conversation_ids": [cid], "label": "Receipts"})
    assert lb in (await _conv(cid)).label_ids
    await tool("archive", {"conversation_ids": [cid]})
    assert roles["inbox"] not in (await _conv(cid)).label_ids
    d = await tool("create_draft", {"to": ["a@example.org"], "subject": "Re: Invoice", "body": "Thanks"})
    assert "draft" in str(d).lower()


async def test_api_draft_send_queues_for_the_undo_window(client, user):
    """draft.send: the message waits in the outbox for the undo window; cancelling it is the undo."""
    await _setup_identity(user)
    r = await client.post("/api/v1/send", json={"payload": {"to": [{"email": "a@example.org"}], "subject": "Now",
                                                             "text": "x"}})
    assert r.status_code == 200, r.text
    ob = r.json()["outbox"]
    assert ob["status"] == "queued" and ob["sent_at"] is None
    assert datetime.fromisoformat(ob["send_at"]) > datetime.now(UTC)
    assert (await client.post(f"/api/v1/outbox/{ob['id']}/cancel")).json()["outbox"]["status"] == "cancelled"


async def _assistant_ctx(db, user_id, session="s1", **kw):
    from mailr.agent.tools import ToolCtx
    from mailr.assistant.orders import principal_for
    from mailr.models import User

    return ToolCtx(db=db, p=principal_for(await db.get(User, user_id)), assistant=True, chat_session_id=session, **kw)


async def _approve(client, aid):
    """The user allows a pending request once, and the worker runs it."""
    from mailr.core import permissions

    r = await client.post(f"/api/v1/approvals/{aid}/decide", json={"decision": "approve"})
    assert r.status_code == 200 and r.json()["status"] == "approved", r.text
    await permissions.execute_job({"pending_id": aid}, None)
    return (await client.get(f"/api/v1/approvals/{aid}")).json()


async def test_api_approvals_approve_and_deny(client, user):
    """approval.approve runs the waiting action; approval.deny drops it. A decided request stays decided."""
    from mailr.agent.tools import run_tool

    async with sessionmaker()() as db:
        ctx = await _assistant_ctx(db, user.id)
        ids = {}
        for name in ("Yes order", "No order"):
            res = await run_tool(ctx, "manage_order", {"action": "create", "name": name, "question": f"{name}?",
                                                       "star": True})
            ids[name] = res.data["approval_id"]
    assert {a["id"] for a in (await client.get("/api/v1/approvals")).json()} == set(ids.values())
    assert (await _approve(client, ids["Yes order"]))["status"] == "executed"
    r = await client.post(f"/api/v1/approvals/{ids['No order']}/decide", json={"decision": "deny"})
    assert r.status_code == 200 and r.json()["status"] == "denied"
    assert (await client.post(f"/api/v1/approvals/{ids['No order']}/decide", json={"decision": "approve"})).status_code == 409
    names = [o["name"] for o in (await client.get("/api/v1/assistant/orders")).json()["items"] if not o["builtin"]]
    assert names == ["Yes order"]


async def test_assistant_order_edit_pause_delete_wait_for_approval(client, user):
    """order.edit, order.pause, order.delete by the assistant: each waits for the user, then applies."""
    from mailr.agent.tools import run_tool

    oid = (await client.post("/api/v1/assistant/orders", json={"name": "Jobs", "query": "from:jobs@example.org",
                                                               "actions": {"star": True}})).json()["id"]

    async def ask(args):
        async with sessionmaker()() as db:
            res = await run_tool(await _assistant_ctx(db, user.id), "manage_order", {"order_id": oid, **args})
        assert res.pending is not None, res.data
        return res.data["approval_id"]

    async def order():
        async with sessionmaker()() as db:
            return await db.get(AssistantOrder, oid)

    aid = await ask({"action": "update", "name": "Job alerts"})
    assert (await order()).name == "Jobs"  # nothing changes before the user decides
    await _approve(client, aid)
    assert (await order()).name == "Job alerts"
    await _approve(client, await ask({"action": "pause"}))
    assert (await order()).enabled is False
    await _approve(client, await ask({"action": "delete"}))
    assert await order() is None


async def test_assistant_reply_delete_and_send(client, user, raw):
    """mail.reply (a reply draft), mail.delete (to Trash), draft.send (an approval card; nothing queues)."""
    from mailr.agent.tools import run_tool

    await _setup_identity(user)
    roles = await _roles(user.id)
    mid, cid = await _ingest(user, raw("Lunch on Friday?", frm="Carol <carol@example.org>"), roles=["inbox"])
    _, other = await _ingest(user, raw("Old promo", frm="Shop <shop@example.org>"), roles=["inbox"])
    async with sessionmaker()() as db:
        ctx = await _assistant_ctx(db, user.id)
        d = await run_tool(ctx, "create_draft", {"reply_to_message_id": mid, "mode": "reply", "body": "Friday works."})
        assert d.data["to"] == ["carol@example.org"] and d.data["subject"] == "Re: Lunch on Friday?"
        assert d.data["conversation_id"] == cid
        res = await run_tool(ctx, "modify_conversations", {"conversation_ids": [other], "action": "trash"})
        aid = res.data.get("approval_id")
        s = await run_tool(ctx, "send_draft", {"draft_id": d.data["draft_id"]})
        assert s.data["status"] == "pending_approval" and s.pending["action"] == "mail.send"
        obs = (await db.execute(select(Outbox).where(Outbox.user_id == user.id))).scalars().all()
        assert [(o.status, o.sent_at) for o in obs] == [("awaiting_approval", None)]
    if aid:
        await _approve(client, aid)
    assert roles["trash"] in (await _conv(other)).label_ids


@radicale
async def test_api_mail_to_task_and_to_event(client, user, raw):
    """mail.to_task / mail.to_event: a suggestion from the email (nothing saved), then the confirmed
    task or event, linked back to the conversation."""
    await _connect(client)
    mid, cid = await _ingest(user, raw("Budget review", frm="Dana <dana@example.org>",
                                       body="Can we meet on Friday at 3pm to go through the budget?"))
    s = (await client.post("/api/v1/calendar/from-message", json={"message_id": mid, "kind": "task"})).json()
    assert s["conversation_id"] == cid and s["draft"]["title"]
    assert (await client.get("/api/v1/tasks")).json()["items"] == []
    due = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
    r = await client.post("/api/v1/calendar/from-message/confirm", json={"message_id": mid, "kind": "task",
                                                                         "title": "Prepare budget", "due": due,
                                                                         "priority": 1, "reminders": [60]})
    assert r.status_code == 200, r.text
    task = next(t for t in (await client.get("/api/v1/tasks")).json()["items"] if t["summary"] == "Prepare budget")
    assert task["conversation_id"] == cid and "From email: Budget review" in (task.get("notes") or "")
    assert task["priority"] == 1 and task["reminders"] == [60]  # what the editor set is kept

    start = (datetime.now(UTC) + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)
    r = await client.post("/api/v1/calendar/from-message/confirm", json={
        "message_id": mid, "kind": "event", "title": "Budget review", "start": start.isoformat(),
        "end": (start + timedelta(hours=1)).isoformat(), "reminders": [15]})
    assert r.status_code == 200, r.text
    agenda = (await client.get("/api/v1/calendar/agenda", params={
        "from": (start - timedelta(hours=1)).isoformat(), "to": (start + timedelta(hours=2)).isoformat()})).json()
    items = agenda if isinstance(agenda, list) else agenda.get("events") or agenda.get("items")
    assert [e.get("conversation_id") for e in items if e["summary"] == "Budget review"] == [cid]
    assert [e.get("reminders") for e in items if e["summary"] == "Budget review"] == [[15]]
    # An event needs a start.
    assert (await client.post("/api/v1/calendar/from-message/confirm", json={
        "message_id": mid, "kind": "event", "title": "x"})).status_code == 400


@radicale
async def test_assistant_mail_to_task_and_to_event(client, user, raw):
    """mail.to_task / mail.to_event by the assistant: create_task and create_event with the email's
    conversation_id (an event waits for the user's approval first)."""
    from mailr.agent.tools import run_tool

    await _connect(client)
    _, cid = await _ingest(user, raw("Dentist", frm="Clinic <clinic@example.org>"))
    start = (datetime.now(UTC) + timedelta(days=3)).replace(hour=9, minute=0, second=0, microsecond=0)
    async with sessionmaker()() as db:
        ctx = await _assistant_ctx(db, user.id)
        t = await run_tool(ctx, "create_task", {"summary": "Confirm dentist", "conversation_id": cid})
        e = await run_tool(ctx, "create_event", {"summary": "Dentist", "start": start.isoformat(),
                                                 "end": (start + timedelta(minutes=30)).isoformat(),
                                                 "conversation_id": cid})
    for res in (t, e):
        if res.pending is not None:
            assert (await _approve(client, res.data["approval_id"]))["status"] == "executed"
    tasks = (await client.get("/api/v1/tasks")).json()["items"]
    assert [x["conversation_id"] for x in tasks if x["summary"] == "Confirm dentist"] == [cid]
    agenda = (await client.get("/api/v1/calendar/agenda", params={
        "from": start.isoformat(), "to": (start + timedelta(hours=1)).isoformat()})).json()
    items = agenda if isinstance(agenda, list) else agenda.get("events") or agenda.get("items")
    assert [x.get("conversation_id") for x in items if x["summary"] == "Dentist"] == [cid]
