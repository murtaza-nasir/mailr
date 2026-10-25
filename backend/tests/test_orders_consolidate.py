# ACT-SURFACES: api assistant web.settings android.settings
"""One orders pipeline (assistant/orders.py, assistant/forwards.py).

- the migration: the old switch (agent.forwarded_instructions) becomes the built-in order's state;
- the built-in order "Follow my forwarded notes": listed with its permission, never deleted or edited,
  turned off and on (order.pause), its permission set (order.permission), moved (order.move); the Permissions
  page leaves its class out;
- duplicates: two orders that add the same invitation make one approval card; one notification per email;
  the run log names the order that acted;
- from_me: "only from me or my trusted addresses", verified;
- addresses another assistant handles (the setting handled_elsewhere): orders leave mail sent only to them
  alone unless they name the address, and the built-in order records why;
- the orders job is queued for trusted senders only when every enabled order needs a trusted forward;
- an errand that creates the event an email invites to gets the invitation's identity.
"""

from __future__ import annotations

import importlib.util
import pathlib
import uuid
from datetime import UTC, datetime, timedelta

from helpers.agent import _setup_identity
from helpers.trusted_forwards import (
    GOOD_AR,
    WORK,
    _addr,
    _arrive,
    _builtin,
    _forward,
    _ics,
    _mine,
    _pass,
    _trust,
)
from sqlalchemy import select

from mailr.core import trusted
from mailr.db import sessionmaker
from mailr.models import AssistantOrder, AssistantRun, Identity, PendingAction, PermissionRule, User


def _migration():
    path = pathlib.Path(__file__).parents[1] / "migrations/versions/f4c9a2e7b1d6_orders_consolidate.py"
    spec = importlib.util.spec_from_file_location("orders_consolidate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _new_user(rule: str | None) -> int:
    async with sessionmaker()() as db:
        u = User(email=f"mig-{uuid.uuid4().hex[:8]}@example.com", name="M", settings={})
        db.add(u)
        await db.flush()
        if rule:
            db.add(PermissionRule(user_id=u.id, subject="assistant", action=trusted.ACTION, effect=rule,
                                  constraints={}, source="user"))
        await db.commit()
        return u.id


async def test_migration_maps_the_old_switch():
    """deny (Off) → the order off and the deny rule gone; allow → on, the allow rule kept; ask or no rule → on."""
    users = {k: await _new_user(k) for k in ("deny", "allow", "ask")}
    users["none"] = await _new_user(None)
    m = _migration()
    async with sessionmaker()() as db:
        first = await db.run_sync(lambda s: m.seed(s.connection()))
        again = await db.run_sync(lambda s: m.seed(s.connection()))  # safe to run again: nothing new
        await db.commit()
    got = dict(first)
    assert {k: got[uid] for k, uid in users.items()} == {"deny": False, "allow": True, "ask": True, "none": True}
    assert not set(dict(again)) & set(users.values())
    async with sessionmaker()() as db:
        for k, uid in users.items():
            o = (await db.execute(select(AssistantOrder).where(AssistantOrder.user_id == uid))).scalar_one()
            assert (o.builtin, o.from_trusted, o.trigger, o.position, o.deliver) == (
                "forwarded_notes", True, "message", 0, ["brief", "notify"])
            rules = (await db.execute(select(PermissionRule.effect).where(
                PermissionRule.user_id == uid, PermissionRule.action == trusted.ACTION))).scalars().all()
            assert rules == {"deny": [], "allow": ["allow"], "ask": ["ask"], "none": []}[k], k


async def test_builtin_order_is_listed_and_kept(client, user):
    r = await client.post("/api/v1/assistant/orders", json={"name": "Bills", "query": "subject:invoice",
                                                             "actions": {"star": True}})
    assert r.status_code == 200, r.text
    items = (await client.get("/api/v1/assistant/orders")).json()["items"]
    b = next(o for o in items if o["builtin"])
    assert b["builtin"] == "forwarded_notes" and b["from_trusted"] is True and b["enabled"] is True
    assert b["permission"] == "ask" and b["permission_source"] == "default"
    assert [o["builtin"] for o in items if o["trigger"] == "message"] == ["forwarded_notes", None]  # on top
    assert next(o for o in items if not o["builtin"])["from_me"] is False
    # Never deleted, never edited; turned off and on; its permission set.
    assert (await client.delete(f"/api/v1/assistant/orders/{b['id']}")).status_code == 400
    assert (await client.patch(f"/api/v1/assistant/orders/{b['id']}", json={"query": "from:x"})).status_code == 400
    r = await client.patch(f"/api/v1/assistant/orders/{b['id']}", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    r = await client.patch(f"/api/v1/assistant/orders/{b['id']}", json={"enabled": True, "permission": "allow"})
    assert r.status_code == 200 and r.json()["permission"] == "allow"
    async with sessionmaker()() as db:
        assert (await db.execute(select(PermissionRule.effect).where(
            PermissionRule.user_id == user.id, PermissionRule.action == trusted.ACTION))).scalars().all() == ["allow"]
    assert (await client.patch(f"/api/v1/assistant/orders/{b['id']}", json={"permission": "deny"})).status_code == 422
    # The Permissions page leaves the class out (the order shows it).
    perms = (await client.get("/api/v1/permissions")).json()
    assert trusted.ACTION not in {c["action"] for c in perms["classes"]}
    eff = (await client.get("/api/v1/permissions/effective", params={"subject": "assistant"}))
    if eff.status_code == 200:
        assert trusted.ACTION not in {e["action"] for e in eff.json().get("items", eff.json())}


async def test_orders_move_up_and_down(client, user):
    a = (await client.post("/api/v1/assistant/orders", json={"name": "A", "query": "subject:a",
                                                              "actions": {"star": True}})).json()["id"]
    b = (await client.post("/api/v1/assistant/orders", json={"name": "B", "query": "subject:b",
                                                              "actions": {"star": True}})).json()["id"]
    names = lambda items: [o["name"] for o in items if o["trigger"] == "message"]
    items = (await client.get("/api/v1/assistant/orders")).json()["items"]
    fw = next(o["id"] for o in items if o["builtin"])
    assert names(items) == ["Follow my forwarded notes", "A", "B"]
    assert (await client.post(f"/api/v1/assistant/orders/{fw}/move", json={"direction": "down"})).status_code == 200
    assert (await client.post(f"/api/v1/assistant/orders/{b}/move", json={"direction": "up"})).status_code == 200
    assert names((await client.get("/api/v1/assistant/orders")).json()["items"]) == ["A", "B", "Follow my forwarded notes"]
    assert (await client.post(f"/api/v1/assistant/orders/{a}/move", json={"direction": "up"})).status_code == 200
    assert (await client.post(f"/api/v1/assistant/orders/{a}/move", json={"direction": "sideways"})).status_code == 422


async def test_two_orders_add_one_invitation_once(client, user):
    """Duplicates: the top order asks to add the invitation; the second is logged as a duplicate of it."""
    addr = _addr()
    await _trust(user, addr)
    await _setup_identity(user)
    first = (await client.post("/api/v1/assistant/orders", json={
        "name": "Work invites", "from_trusted": True, "actions": {"add_to_calendar": True}})).json()["id"]
    second = (await client.post("/api/v1/assistant/orders", json={
        "name": "Ops", "query": "subject:Ops", "actions": {"add_to_calendar": True}})).json()["id"]
    start = (datetime.now(UTC) + timedelta(days=4)).replace(hour=15, minute=0, second=0, microsecond=0)
    uid = f"ops-{uuid.uuid4().hex[:8]}"
    mid = await _arrive(user, _forward(addr, await _mine(user), note="", invite=_ics(uid, addr, start).encode()))
    await _pass(mid)
    async with sessionmaker()() as db:
        runs = {r.order_id: r for r in (await db.execute(select(AssistantRun).where(
            AssistantRun.message_id == mid))).scalars()}
        assert runs[first].actions[0]["result"] == "pending"
        assert runs[second].actions[0] == {"action": "add_to_calendar", "result": "duplicate",
                                           "by": {"order_id": first, "name": "Work invites"}}
        cards = (await db.execute(select(PendingAction).where(PendingAction.user_id == user.id,
                                                              PendingAction.action == "calendar.create"))).scalars().all()
        assert len(cards) == 1
    # The same invitation forwarded again: the waiting card counts, no second one.
    again = await _arrive(user, _forward(addr, await _mine(user), note="", subject="Fw: Ops Review (again)",
                                         invite=_ics(uid, addr, start).encode()))
    await _pass(again)
    async with sessionmaker()() as db:
        run = (await db.execute(select(AssistantRun).where(AssistantRun.message_id == again,
                                                           AssistantRun.order_id == first))).scalar_one()
        assert run.actions[0]["result"] == "duplicate" and run.actions[0]["approval_id"]
        assert len((await db.execute(select(PendingAction).where(
            PendingAction.user_id == user.id, PendingAction.action == "calendar.create"))).scalars().all()) == 1
    log = (await client.get("/api/v1/assistant/runs", params={"order_id": second})).json()["items"]
    assert log[0]["actions"][0]["by"]["name"] == "Work invites"


async def test_one_notification_per_email(client, user, raw, monkeypatch):
    from helpers.agent import _ingest

    from mailr.assistant import notify

    sent = []

    async def fake_send(db, u, **kw):
        sent.append(kw["title"])
        return "sent"

    monkeypatch.setattr(notify, "send", fake_send)
    a = (await client.post("/api/v1/assistant/orders", json={"name": "A", "query": "subject:quarterly",
                                                              "actions": {"notify": True}})).json()["id"]
    b = (await client.post("/api/v1/assistant/orders", json={"name": "B", "query": "from:alice@acme.example",
                                                              "actions": {"notify": True, "star": True}})).json()["id"]
    mid, _ = await _ingest(user.id, raw("Quarterly numbers", frm="Alice <alice@acme.example>", to=user.email))
    await _pass(mid)
    assert len(sent) == 1 and sent[0].endswith("A")
    async with sessionmaker()() as db:
        rb = (await db.execute(select(AssistantRun).where(AssistantRun.order_id == b))).scalar_one()
        assert {"action": "notify", "result": "duplicate", "by": {"order_id": a, "name": "A"}} in rb.actions
        assert any(x["action"] == "star" for x in rb.actions)  # its other actions still run


async def test_from_me_needs_my_or_a_trusted_verified_address(client, user):
    addr = _addr()
    await _trust(user, addr)
    oid = (await client.post("/api/v1/assistant/orders", json={
        "name": "Memory", "query": "to:memory@example.net", "from_me": True, "actions": {"star": True}})).json()
    assert oid["from_me"] is True
    r = await client.post("/api/v1/assistant/orders", json={
        "name": "R", "trigger": "schedule", "schedule": "0 7 * * *", "from_me": True, "errand": "x"})
    assert r.status_code == 400
    good = await _arrive(user, _forward(addr, "memory@example.net", note="Remember this"))
    spoof = await _arrive(user, _forward(addr, "memory@example.net", ar=None))
    stranger = await _arrive(user, _forward(f"chair@{WORK}", "memory@example.net"))
    listed = await _arrive(user, _forward(addr, "memory@example.net",
                                          extra=["List-Id: <faculty.work.example>"]))
    for m in (good, spoof, stranger, listed):
        await _pass(m)
    async with sessionmaker()() as db:
        runs = (await db.execute(select(AssistantRun).where(AssistantRun.order_id == oid["id"]))).scalars().all()
        assert [r.message_id for r in runs] == [good] and runs[0].condition["from_me"] is True
        # One of the user's own addresses counts too, verified by its domain.
        u = await db.get(User, user.id)
        from mailr.models import Message

        m = await db.get(Message, good)
        m.from_email = u.email
        m.auth_results = {"dmarc": "pass", "dmarc_from": u.email.split("@")[1]}
        assert (await trusted.from_user(db, u, m, raw=f"From: {u.email}\r\n\r\n".encode())).ok
        m.auth_results = {}
        assert (await trusted.from_user(db, u, m, raw=b"From: x\r\n\r\n")).reason == "not_verified"
        await db.rollback()


async def test_addresses_another_assistant_handles(client, user):
    """Mail only to memory@ (handled by another program) is left alone, unless an order names the address;
    the built-in order records why it did not act."""
    addr = _addr()
    await _trust(user, addr)
    me = await _mine(user)
    memory = f"memory-{uuid.uuid4().hex[:4]}@example.com"
    async with sessionmaker()() as db:
        db.add(Identity(user_id=user.id, connector_id=None, email=memory, name="Memory", is_default=False,
                        receives=[]))
        await db.commit()
    assert (await client.patch("/api/v1/me/settings", json={"handled_elsewhere": ["not an address"]})).status_code == 400
    r = await client.patch("/api/v1/me/settings", json={"handled_elsewhere": [memory.upper(), memory]})
    assert r.status_code == 200
    assert (await client.get("/api/v1/assistant/trusted-addresses")).json()["handled_elsewhere"] == [memory]
    fw = await _builtin(user, permission="allow")
    named = (await client.post("/api/v1/assistant/orders", json={
        "name": "Named", "query": f"to:{memory}", "actions": {"star": True}})).json()["id"]
    other = (await client.post("/api/v1/assistant/orders", json={
        "name": "Other", "query": "subject:Ops", "actions": {"star": True}})).json()["id"]
    only_memory = await _arrive(user, _forward(addr, memory))
    both = await _arrive(user, _forward(addr, f"{memory}, {me}", subject="Fw: Ops Review 2"))
    await _pass(only_memory)
    await _pass(both)
    async with sessionmaker()() as db:
        runs = (await db.execute(select(AssistantRun).where(AssistantRun.user_id == user.id))).scalars().all()
        by = {(r.order_id, r.message_id): r for r in runs}
    assert by[(fw, only_memory)].status == "skipped" and memory in by[(fw, only_memory)].error
    assert (named, only_memory) in by and (other, only_memory) not in by
    # Sent to an address of mine as well: mailr acts as usual.
    assert by[(fw, both)].status == "queued" and (other, both) in by


async def test_orders_job_only_for_mail_an_order_can_match(client, user, raw):
    from helpers.agent import _ingest

    from mailr.assistant import orders
    from mailr.models import Job, Message

    addr = _addr()
    await _trust(user, addr)
    await _builtin(user)

    async def queued(mid: int) -> bool:
        async with sessionmaker()() as db:
            await orders.maybe_enqueue_for_message(db, await db.get(Message, mid))
            await db.commit()
            return (await db.execute(select(Job.id).where(Job.dedupe_key == f"assistant.orders:{mid}"))).first() is not None

    stranger, _ = await _ingest(user.id, raw("Hello", frm="Bob <bob@example.org>", to=user.email,
                                             date=(datetime.now(UTC) - timedelta(hours=1)).strftime("%a, %d %b %Y %H:%M:%S +0000")))
    forward = await _arrive(user, _forward(addr, await _mine(user)))
    async with sessionmaker()() as db:  # fresh mail for the live window
        for mid in (forward,):
            (await db.get(Message, mid)).date = datetime.now(UTC)
        await db.commit()
    assert not await queued(stranger)
    assert await queued(forward)
    await client.post("/api/v1/assistant/orders", json={"name": "Any", "query": "subject:hello", "actions": {"star": True}})
    assert await queued(stranger)


async def test_errand_event_from_an_invitation_takes_its_identity(user):
    """An errand on a forwarded invitation that calls create_event without conversation_id: the event is
    linked to the email anyway (same title), so calendar.create adopts the invitation's UID."""
    from helpers.websearch import _ctx

    from mailr.agent import calendar_tools

    addr = _addr()
    await _trust(user, addr)
    start = (datetime.now(UTC) + timedelta(days=4)).replace(hour=15, minute=0, second=0, microsecond=0)
    uid = f"ops-{uuid.uuid4().hex[:8]}"
    mid = await _arrive(user, _forward(addr, await _mine(user), note="Add this",
                                       invite=_ics(uid, addr, start).encode()))
    async with sessionmaker()() as db:
        from mailr.models import Message

        cid = (await db.get(Message, mid)).conversation_id
    ctx = await _ctx(user.id)
    try:
        ctx.chat_context = {"conversation_id": cid, "errand": 1}
        args = {"summary": "Ops Review", "start": start.strftime("%Y-%m-%dT%H:%M")}
        assert await calendar_tools._errand_invite_conversation(ctx, args, "Fw: Ops Review") == cid
        assert await calendar_tools._errand_invite_conversation(ctx, {"start": "2030-01-01T09:00"}, "Lunch") is None
        ctx.chat_context = {"conversation_id": cid}  # a chat the user is in: the model decides
        assert await calendar_tools._errand_invite_conversation(ctx, args, "Ops Review") is None
    finally:
        await ctx.db.close()


def test_handled_elsewhere_rule():
    from mailr.models import Message

    u = User(email="me@example.com", settings={"handled_elsewhere": ["memory@example.com"]})
    mine = {"me@example.com", "memory@example.com"}
    msg = lambda to, cc=(): Message(to=[{"email": a} for a in to], cc=[{"email": a} for a in cc])
    assert trusted.handled_elsewhere(msg(["memory@example.com"]), u, mine) == ["memory@example.com"]
    assert trusted.handled_elsewhere(msg(["Memory@Example.com"], ["boss@work.example"]), u, mine) == ["memory@example.com"]
    assert trusted.handled_elsewhere(msg(["memory@example.com", "me@example.com"]), u, mine) == []
    assert trusted.handled_elsewhere(msg(["me@example.com"]), u, mine) == []
    assert trusted.names_address("to:memory@example.com is:unread", ["memory@example.com"])
    assert not trusted.names_address("to:me@example.com", ["memory@example.com"])
    assert GOOD_AR  # the forward helper's authentication line
