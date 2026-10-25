"""Permission engine: evaluation, grants, expiry, exactly-once execution,
and the gated agent routes (mail actions, sends, rules)."""

from datetime import UTC, datetime, timedelta

from helpers.permissions import _token
from sqlalchemy import update

from mailr.core import permissions
from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.store import user_store
from mailr.db import session_scope, sessionmaker
from mailr.models import ApiToken, Identity, PendingAction, PermissionRule, User


async def _ingest(user, data, **kw):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        res = await ingest_raw(db, await user_store(db, u), data, IngestOptions(**kw))
        await db.commit()
        return res.message.id, res.message.conversation_id


async def _actor(user_id, token_id):
    async with sessionmaker()() as db:
        u = await db.get(User, user_id)
        t = await db.get(ApiToken, token_id)
        return permissions.Actor(user=u, kind="token", token=t)


async def test_evaluation_order(client, user):
    _, tid = await _token(client, ["read", "label", "send", "calendar", "rules"])
    actor = await _actor(user.id, tid)
    async with sessionmaker()() as db:
        assert (await permissions.resolve(db, actor, "mail.trash"))[0] == "ask"        # default
        assert (await permissions.resolve(db, actor, "mail.label"))[0] == "allow"      # default
        db.add(PermissionRule(user_id=user.id, subject="*", action="mail.*", effect="allow", constraints={}))
        db.add(PermissionRule(user_id=user.id, subject=f"token:{tid}", action="mail.trash", effect="ask",
                              constraints={}))
        await db.flush()
        # Most specific subject wins: token-level ask beats the general allow.
        assert (await permissions.resolve(db, actor, "mail.trash"))[0] == "ask"
        assert (await permissions.resolve(db, actor, "mail.spam"))[0] == "allow"
        # Any applicable deny wins, at any level.
        db.add(PermissionRule(user_id=user.id, subject="*", action="mail.spam", effect="deny", constraints={}))
        await db.flush()
        assert (await permissions.resolve(db, actor, "mail.spam"))[0] == "deny"
        # Constraints decide applicability.
        db.add(PermissionRule(user_id=user.id, subject=f"token:{tid}", action="calendar.create", effect="allow",
                              constraints={"calendars": ["cal-a"]}))
        await db.flush()
        assert (await permissions.resolve(db, actor, "calendar.create", {"calendar": "cal-a"}))[0] == "allow"
        assert (await permissions.resolve(db, actor, "calendar.create", {"calendar": "cal-b"}))[0] == "ask"
        # Expired rules are ignored.
        db.add(PermissionRule(user_id=user.id, subject=f"token:{tid}", action="rules.write", effect="allow",
                              constraints={}, expires_at=datetime.now(UTC) - timedelta(minutes=1)))
        await db.flush()
        assert (await permissions.resolve(db, actor, "rules.write"))[0] == "ask"
        # Missing scope is a deny regardless of rules.
        actor.token.scopes = ["read"]
        d = await permissions.check(db, actor, "mail.trash")
        assert isinstance(d, permissions.Denied)
        await db.rollback()


async def test_token_trash_needs_approval_then_executes(client, user, raw):
    _, cid = await _ingest(user, raw("Old newsletter"))
    agent, _ = await _token(client, ["read", "label"])
    r = await agent.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "trash"})
    assert r.status_code == 202, r.text
    appr = r.json()["approval"]
    assert appr["action"] == "mail.trash" and appr["preview"]["title"] == "Move 1 message to Trash"
    inbox = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()
    assert cid in [c["id"] for c in inbox["items"]]  # nothing happened yet
    # The agent sees only its own requests; the user sees all.
    assert [x["id"] for x in (await agent.get("/api/v1/approvals")).json()] == [appr["id"]]
    assert (await agent.post(f"/api/v1/approvals/{appr['id']}/decide",
                             json={"decision": "approve"})).status_code == 403
    r = await client.post(f"/api/v1/approvals/{appr['id']}/decide", json={"decision": "approve", "scope": "session"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    await permissions.execute_job({"pending_id": appr["id"]}, None)
    assert (await client.get(f"/api/v1/approvals/{appr['id']}")).json()["status"] == "executed"
    trash = (await client.get("/api/v1/conversations", params={"view": "trash"})).json()
    assert cid in [c["id"] for c in trash["items"]]
    # "Allow for this session" created a (12 h) grant: the next trash runs at once.
    _, cid2 = await _ingest(user, raw("Another"))
    r = await agent.post("/api/v1/conversations/actions", json={"ids": [cid2], "action": "trash"})
    assert r.status_code == 200, r.text
    # Deciding twice is refused (exactly once).
    r = await client.post(f"/api/v1/approvals/{appr['id']}/decide", json={"decision": "deny"})
    assert r.status_code == 409
    await agent.aclose()


async def test_pending_expiry_and_denial(client, user, raw):
    _, cid = await _ingest(user, raw("Keep me"))
    agent, _ = await _token(client, ["read", "label"])
    a1 = (await agent.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "spam"})).json()["approval"]
    a2 = (await agent.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "trash"})).json()["approval"]
    r = await client.post(f"/api/v1/approvals/{a1['id']}/decide", json={"decision": "deny", "scope": "always"})
    assert r.json()["status"] == "denied"
    # "Always deny" became a rule: the next spam request is refused outright.
    assert (await agent.post("/api/v1/conversations/actions",
                             json={"ids": [cid], "action": "spam"})).status_code == 403
    async with session_scope() as db:
        await db.execute(update(PendingAction).where(PendingAction.id == a2["id"])
                         .values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    await permissions.expire_pending()
    assert (await client.get(f"/api/v1/approvals/{a2['id']}")).json()["status"] == "expired"
    inbox = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()
    assert cid in [c["id"] for c in inbox["items"]]
    await agent.aclose()


async def test_agent_send_gate_and_first_message_rule(client, user):
    # An identity with an outgoing server so the gate can run (no mail is actually sent here).
    async with session_scope() as db:
        from mailr.models import Connector

        c = Connector(user_id=user.id, kind="imap", name="acct", mode="mirror", config={"smtp": {"host": "x"}},
                      state={}, enabled=False)
        db.add(c)
        await db.flush()
        db.add(Identity(user_id=user.id, email="me@example.com", name="Me", connector_id=c.id, is_default=True))
    agent, tid = await _token(client, ["read", "draft", "send"])
    # Allow sends for this token outright: the first message to a new address still asks.
    await client.post("/api/v1/permissions", json={"subject": f"token:{tid}", "action": "mail.send", "effect": "allow"})
    r = await agent.post("/api/v1/send", json={"payload": {"to": [{"email": "new@else.org", "name": ""}],
                                                           "subject": "Hello", "text": "hi"}})
    assert r.status_code == 202, r.text
    assert "first message" in r.json()["approval"]["preview"].get("reason", "")
    assert r.json()["outbox"]["status"] == "awaiting_approval"
    oid, aid = r.json()["outbox"]["id"], r.json()["approval"]["id"]
    # Legacy approve endpoint decides the same pending action.
    r = await client.post(f"/api/v1/outbox/{oid}/approve")
    assert r.status_code == 200, r.text
    await permissions.execute_job({"pending_id": aid}, None)
    ob = next(o for o in (await client.get("/api/v1/outbox")).json() if o["id"] == oid)
    assert ob["status"] == "queued"
    assert (await client.get(f"/api/v1/approvals/{aid}")).json()["status"] == "executed"
    await agent.aclose()


async def test_rules_gate_and_forwarding(client, user):
    agent, _ = await _token(client, ["read", "rules"])
    r = await agent.post("/api/v1/rules", json={"query": "from:x@y.z", "actions": {"forward_to": "evil@x.org"}})
    assert r.status_code == 403
    r = await agent.post("/api/v1/rules", json={"query": "from:x@y.z", "actions": {"archive": True}})
    assert r.status_code == 202, r.text
    assert (await client.get("/api/v1/rules")).json() == []
    await client.post(f"/api/v1/approvals/{r.json()['approval']['id']}/decide", json={"decision": "approve"})
    await permissions.execute_job({"pending_id": r.json()["approval"]["id"]}, None)
    rules = (await client.get("/api/v1/rules")).json()
    assert [x["query"] for x in rules] == ["from:x@y.z"] and rules[0]["source"] == "agent"
    # The user cannot forward to an unverified address either.
    r = await client.post("/api/v1/rules", json={"query": "from:a@b.c", "actions": {"forward_to": "me@else.org"}})
    assert r.status_code == 400 and "verified" in r.text
    await agent.aclose()


async def test_effective_view(client, user):
    agent, tid = await _token(client, ["read", "label"])
    eff = {x["action"]: x for x in (await client.get("/api/v1/permissions/effective",
                                                      params={"subject": f"token:{tid}"})).json()["items"]}
    assert eff["mail.read"]["effect"] == "allow" and eff["mail.trash"]["effect"] == "ask"
    assert eff["mail.send"]["effect"] == "deny" and "scope" in eff["mail.send"]["source"]
    assert eff["settings.write"]["effect"] == "deny"
    await agent.aclose()


async def test_speakr_classes_tags_constraint_and_share_hard_rule(user):
    """Speakr write classes (integrations/speakr/writes.py): scope `recordings`, ask by default, a `tags`
    constraint on the recording's Speakr tags, and speakr.share asking even under an allow rule."""
    for cls in ("speakr.upload", "speakr.write", "speakr.share"):
        assert permissions.CLASSES[cls] == ("ask", "recordings")
    assert "speakr.share" in permissions.ALWAYS_ASK
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        actor = permissions.Actor.assistant(u, None)
        assert await permissions.constraints_match(db, actor, "speakr.write", {"tags": ["Advising"]},
                                                   {"tags": ["advising", "work"]}, effect="ask")
        assert not await permissions.constraints_match(db, actor, "speakr.write", {"tags": ["Advising"]},
                                                       {"tags": ["advising", "work"]}, effect="allow")
        db.add(PermissionRule(user_id=u.id, subject="assistant", action="speakr.*", effect="allow", constraints={}))
        await db.commit()
        ok = await permissions.check(db, actor, "speakr.write", target="t", ctx={"tags": ["work"]})
        assert isinstance(ok, permissions.Allowed)
        asked = await permissions.check(db, actor, "speakr.share", target="t", ctx={})
        assert isinstance(asked, permissions.Pending)
        assert asked.action.preview["reason"] == "hard rule: a public share link"
        await db.rollback()
