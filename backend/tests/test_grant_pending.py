# ACT-SURFACES: api
"""A grant on one approval card decides the other waiting requests it covers:
"Allow for 12 hours" and "Always allow" approve every other pending request of the same
agent and class that the new rule lets through, a deny grant denies them, hard rules still ask, the
cards say how many others a grant would cover (also_covers), and Undo (approval.undo_grant) puts the
ones not yet run back to waiting."""

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select, update

from mailr.core import permissions
from mailr.db import session_scope, sessionmaker
from mailr.models import ApiToken, Connector, Identity, Label, PendingAction, PermissionRule, User


async def _token(client, scopes, name="claude-code"):
    r = await client.post("/api/v1/tokens", json={"name": name, "scopes": scopes})
    assert r.status_code == 200, r.text
    a = AsyncClient(transport=client._transport, base_url="http://test")
    a.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return a, r.json()["item"]["id"]


async def _create_label(agent, name):
    r = await agent.post("/api/v1/labels", json={"name": name})
    assert r.status_code == 202, r.text
    return r.json()["approval"]["id"]


async def _status(client, aid):
    return (await client.get(f"/api/v1/approvals/{aid}")).json()


async def _labels(user_id):
    async with sessionmaker()() as db:
        return set((await db.execute(select(Label.name).where(Label.user_id == user_id))).scalars())


async def _internal(user, aid) -> int:
    """The internal id behind a card id (public or not)."""
    async with sessionmaker()() as db:
        return (await permissions.approval_from_ref(db, user.id, str(aid))).id


async def test_session_grant_approves_other_waiting_label_creates(client, user, raw):
    agent, _ = await _token(client, ["read", "label"])
    other, _ = await _token(client, ["read", "label"], name="hermes")
    a1 = await _create_label(agent, "Receipts")
    a2 = await _create_label(agent, "Travel")
    a3 = await _create_label(other, "Other agent")  # another subject: stays
    # Another class of the same agent: stays.
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.store import user_store

    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        res = await ingest_raw(db, await user_store(db, u), raw("Old newsletter"), IngestOptions())
        await db.commit()
        cid = res.message.conversation_id
    r = await agent.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "trash"})
    assert r.status_code == 202, r.text
    a4 = r.json()["approval"]["id"]

    # Before the tap, each card says how many other waiting requests a grant would cover.
    cards = {c["id"]: c for c in (await client.get("/api/v1/approvals")).json()}
    assert cards[a1]["also_covers"] == {"session": 1, "always": 1, "always_derived": 1}
    assert cards[a2]["also_covers"]["session"] == 1
    assert cards[a3]["also_covers"]["session"] == 0
    assert cards[a4]["also_covers"]["session"] == 0
    # An agent's own list carries no counts (they describe the user's choices).
    assert "also_covers" not in (await agent.get("/api/v1/approvals")).json()[0]

    r = await client.post(f"/api/v1/approvals/{a1}/decide", json={"decision": "approve", "scope": "session"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved" and body["rule_id"]
    assert body["also_decided"] == {"ids": [a2], "count": 1}
    b = await _status(client, a2)
    assert b["status"] == "approved" and b["decided_by"] == f"rule:{body['rule_id']}"
    assert (await _status(client, a3))["status"] == "pending"
    assert (await _status(client, a4))["status"] == "pending"
    # Each approved request has its own execute job: both labels are created.
    for aid in (a1, a2):
        await permissions.execute_job({"pending_id": await _internal(user, aid)}, None)
        assert (await _status(client, aid))["status"] == "executed"
    assert {"Receipts", "Travel"} <= await _labels(user.id)
    assert "Other agent" not in await _labels(user.id)
    await agent.aclose()
    await other.aclose()


async def test_first_message_to_a_new_address_stays_waiting(client, user):
    async with session_scope() as db:
        c = Connector(user_id=user.id, kind="imap", name="acct", mode="mirror", config={"smtp": {"host": "x"}},
                      state={}, enabled=False)
        db.add(c)
        await db.flush()
        db.add(Identity(user_id=user.id, email="me@example.com", name="Me", connector_id=c.id, is_default=True))
    agent, _ = await _token(client, ["read", "draft", "send"])
    ids = []
    for to in ("new@else.org", "other@else.org"):
        r = await agent.post("/api/v1/send", json={"payload": {"to": [{"email": to, "name": ""}],
                                                               "subject": "Hello", "text": "hi"}})
        assert r.status_code == 202, r.text
        ids.append(r.json()["approval"]["id"])
    card = next(c for c in (await client.get("/api/v1/approvals")).json() if c["id"] == ids[0])
    assert card["also_covers"]["session"] == 0
    r = await client.post(f"/api/v1/approvals/{ids[0]}/decide", json={"decision": "approve", "scope": "session"})
    assert r.json()["also_decided"]["count"] == 0
    assert (await _status(client, ids[1]))["status"] == "pending"
    await agent.aclose()


async def _engine_requests(user_id, token_id, specs):
    """Pending requests made through the engine itself: (class, ctx, force_ask) each."""
    out = []
    async with session_scope() as db:
        u = await db.get(User, user_id)
        actor = permissions.Actor(user=u, kind="token", token=await db.get(ApiToken, token_id))
        for action, ctx, force in specs:
            d = await permissions.check(db, actor, action, target="t", ctx=ctx, payload={"x": 1},
                                        preview={"title": action}, force_ask=force)
            assert isinstance(d, permissions.Pending), d
            out.append(d.action.id)
    return out


async def test_derived_constraints_and_forced_asks(client, user):
    _, tid = await _token(client, ["read", "calendar"])
    a, b, c, forced = await _engine_requests(user.id, tid, [
        ("calendar.create", {"calendar": "cal-a"}, None),
        ("calendar.create", {"calendar": "cal-a"}, None),
        ("calendar.create", {"calendar": "cal-b"}, None),
        ("calendar.create", {"calendar": "cal-a"}, "hard rule: a test"),
    ])
    async with session_scope() as db:
        pa = await db.get(PendingAction, a)
        # Narrowed to this calendar: only b. Every calendar: b and c. Never the forced ask.
        assert await permissions.would_cover(db, pa) == {"session": 2, "always": 2, "always_derived": 1}
        u = await db.get(User, user.id)
        res = await permissions.decide_full(db, u, a, "approve", "always", "derived")
        assert res.also == [b]
    async with session_scope() as db:
        assert (await db.get(PendingAction, b)).status == "approved"
        assert (await db.get(PendingAction, c)).status == "pending"
        assert (await db.get(PendingAction, forced)).status == "pending"


async def test_deny_for_session_denies_the_covered_ones(client, user):
    _, tid = await _token(client, ["read", "label"])
    a, b, other = await _engine_requests(user.id, tid, [
        ("mail.trash", {}, None), ("mail.trash", {}, None), ("mail.spam", {}, None)])
    async with session_scope() as db:
        res = await permissions.decide_full(db, await db.get(User, user.id), a, "deny", "session")
        assert res.also == [b]
    async with session_scope() as db:
        pb = await db.get(PendingAction, b)
        assert pb.status == "denied" and pb.decided_by == f"rule:{res.rule_id}"
        assert (await db.get(PendingAction, other)).status == "pending"
        rule = await db.get(PermissionRule, res.rule_id)
        assert rule.effect == "deny" and rule.expires_at > datetime.now(UTC) + timedelta(hours=11)


async def test_undo_reverts_the_ones_not_run(client, user):
    agent, _ = await _token(client, ["read", "label"])
    a1 = await _create_label(agent, "One")
    a2 = await _create_label(agent, "Two")
    a3 = await _create_label(agent, "Three")
    body = (await client.post(f"/api/v1/approvals/{a1}/decide",
                              json={"decision": "approve", "scope": "session"})).json()
    assert body["also_decided"]["count"] == 2
    # One of the others already ran; Undo leaves it and says so.
    await permissions.execute_job({"pending_id": await _internal(user, a2)}, None)
    r = await client.post(f"/api/v1/approvals/grants/{body['rule_id']}/undo")
    assert r.status_code == 200, r.text
    assert r.json()["reverted"] == [a3] and r.json()["kept"] == [a2]
    s3 = await _status(client, a3)
    assert s3["status"] == "pending" and not s3["decided_by"]
    assert (await _status(client, a2))["status"] == "executed"
    assert (await _status(client, a1))["status"] == "approved"  # the card's own decision stays
    async with sessionmaker()() as db:
        assert await db.get(PermissionRule, body["rule_id"]) is None
    # The execute job queued for a3 finds it waiting again and does nothing.
    await permissions.execute_job({"pending_id": await _internal(user, a3)}, None)
    assert (await _status(client, a3))["status"] == "pending"
    assert "Three" not in await _labels(user.id)
    # The next new request asks again: the grant is gone.
    assert (await agent.post("/api/v1/labels", json={"name": "Four"})).status_code == 202
    # Twice, or by an agent: refused.
    assert (await client.post(f"/api/v1/approvals/grants/{body['rule_id']}/undo")).status_code == 404
    assert (await agent.post(f"/api/v1/approvals/grants/{body['rule_id']}/undo")).status_code == 403
    await agent.aclose()


async def test_old_rows_with_a_reason_stay_waiting(client, user):
    """A request stored before mailr recorded how it was gated stays waiting when it gives a reason."""
    _, tid = await _token(client, ["read", "calendar"])
    a, b = await _engine_requests(user.id, tid, [("calendar.create", {}, None), ("calendar.create", {}, None)])
    async with session_scope() as db:
        await db.execute(update(PendingAction).where(PendingAction.id == b)
                         .values(ctx={}, preview={"title": "x", "reason": "something"}))
    async with session_scope() as db:
        res = await permissions.decide_full(db, await db.get(User, user.id), a, "approve", "session")
        assert res.also == []


async def test_assistant_chat_grant_covers_that_chat_only(client, user):
    """"Allow for this chat" on an assistant card is a session:<id> rule: other waiting requests of
    that chat are approved, those of another chat stay."""
    ids = []
    async with session_scope() as db:
        u = await db.get(User, user.id)
        for chat in ("chat-one", "chat-one", "chat-two"):
            d = await permissions.check(db, permissions.Actor.assistant(u, chat), "mail.trash", target="t",
                                        payload={}, preview={"title": "Trash"})
            assert isinstance(d, permissions.Pending)
            ids.append(d.action.id)
    async with session_scope() as db:
        res = await permissions.decide_full(db, await db.get(User, user.id), ids[0], "approve", "session")
        assert res.also == [ids[1]]
        assert (await db.get(PermissionRule, res.rule_id)).subject == "session:chat-one"
        assert (await db.get(PendingAction, ids[2])).status == "pending"


async def test_assistant_explains_what_each_choice_covers(client, user):
    from types import SimpleNamespace

    from mailr.agent.screen import approval_lines

    agent, _ = await _token(client, ["read", "label"])
    a1 = await _create_label(agent, "Alpha")
    await _create_label(agent, "Beta")
    async with sessionmaker()() as db:
        p = SimpleNamespace(is_token=False, user_id=user.id, user=await db.get(User, user.id))
        text = "\n".join(await approval_lines(db, p, await _internal(user, a1)))
    assert "Allow once: this request only." in text
    assert "this agent may create or change labels for 12 hours, with any input" in text
    assert "It would also approve 1 other waiting request of this kind now." in text
    assert "Always allow: a standing rule" in text
    await agent.aclose()
