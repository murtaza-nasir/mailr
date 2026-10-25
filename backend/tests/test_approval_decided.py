# ACT-SURFACES: api
"""approval.sync: an approval that leaves pending, by any path, sends one `approval.decided
{ids, outcome}` event (SSE) and one push job for the devices, so a notification answered on one
device goes on the others. Paths: a decision on its card, a grant that decides other waiting
requests, Allow all (decide-many), expiry, the legacy outbox approve, and a send withdrawn
because the user sent or cancelled the draft. The push itself is generic: no title, no body."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from mailr.core import permissions, publicid, push
from mailr.db import session_scope, sessionmaker
from mailr.models import Connector, Identity, Job, Outbox, PendingAction, PushTarget

API = "/api/v1"


@pytest.fixture
def events(monkeypatch):
    """The approval.decided events permissions sends (other events pass through)."""
    seen: list[dict] = []
    real = permissions.notify

    async def record(db, user_id, event):
        if event.get("type") == "approval.decided":
            seen.append({"user": user_id, **event})
        await real(db, user_id, event)

    monkeypatch.setattr(permissions, "notify", record)
    return seen


async def _token(client, scopes, name="agent"):
    r = await client.post(f"{API}/tokens", json={"name": name, "scopes": scopes})
    assert r.status_code == 200, r.text
    a = AsyncClient(transport=client._transport, base_url="http://test")
    a.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return a


async def _ask_label(agent, name) -> str:
    r = await agent.post(f"{API}/labels", json={"name": name})
    assert r.status_code == 202, r.text
    return r.json()["approval"]["id"]


def _internal(aid) -> int:
    return publicid.decode("approval", aid)


def _public(ids) -> list:
    return [publicid.encode("approval", _internal(i)) for i in ids]


async def _jobs(user_id) -> list[dict]:
    async with sessionmaker()() as db:
        rows = (await db.execute(select(Job).where(Job.kind == "push.approval_decided", Job.user_id == user_id)
                                 .order_by(Job.id))).scalars().all()
        return [j.payload for j in rows]


async def test_a_decision_on_its_card_sends_one_event_and_one_push(client, user, events):
    agent = await _token(client, ["read", "label"])
    aid = await _ask_label(agent, "Receipts")
    r = await client.post(f"{API}/approvals/{aid}/decide", json={"decision": "deny"})
    assert r.status_code == 200, r.text
    assert events == [{"user": user.id, "type": "approval.decided", "outcome": "denied", "ids": _public([aid])}]
    assert await _jobs(user.id) == [{"user_id": user.id, "ids": [_internal(aid)], "outcome": "denied"}]
    await agent.aclose()


async def test_a_grant_that_decides_others_sends_them_all_in_one_event(client, user, events):
    agent = await _token(client, ["read", "label"])
    ids = [await _ask_label(agent, n) for n in ("One", "Two", "Three")]
    r = await client.post(f"{API}/approvals/{ids[0]}/decide", json={"decision": "approve", "scope": "always"})
    assert r.status_code == 200, r.text
    assert r.json()["also_decided"]["count"] == 2
    assert len(events) == 1
    assert events[0]["outcome"] == "approved"
    assert sorted(events[0]["ids"]) == sorted(_public(ids))
    jobs = await _jobs(user.id)
    assert len(jobs) == 1 and sorted(jobs[0]["ids"]) == sorted(_internal(i) for i in ids)
    await agent.aclose()


async def test_allow_all_sends_one_event_for_the_batch(client, user, events):
    agent = await _token(client, ["read", "label"])
    ids = [await _ask_label(agent, n) for n in ("Receipts", "Travel", "School")]
    # One is decided on its own card first: its event came then, and the batch leaves it out.
    await client.post(f"{API}/approvals/{ids[2]}/decide", json={"decision": "deny"})
    events.clear()
    r = await client.post(f"{API}/approvals/decide-many", json={"ids": ids, "decision": "approve"})
    assert r.status_code == 200, r.text
    assert events == [{"user": user.id, "type": "approval.decided", "outcome": "approved", "ids": _public(ids[:2])}]
    jobs = await _jobs(user.id)
    assert jobs[-1] == {"user_id": user.id, "ids": [_internal(i) for i in ids[:2]], "outcome": "approved"}
    assert len(jobs) == 2  # the single denial, then the batch
    await agent.aclose()


async def test_expiry_sends_the_expired_ids(client, user, events):
    agent = await _token(client, ["read", "label"])
    ids = [await _ask_label(agent, n) for n in ("Old", "Older")]
    async with session_scope() as db:
        await db.execute(update(PendingAction).where(PendingAction.id.in_([_internal(i) for i in ids]))
                         .values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    await permissions.expire_pending()
    mine = [e for e in events if e["user"] == user.id]
    assert len(mine) == 1 and mine[0]["outcome"] == "expired"
    assert sorted(mine[0]["ids"]) == sorted(_public(ids))
    assert (await _jobs(user.id))[-1]["outcome"] == "expired"
    await agent.aclose()


async def _ident(user) -> int:
    async with session_scope() as db:
        c = Connector(user_id=user.id, kind="imap", name="acct", mode="mirror", config={"smtp": {"host": "x"}},
                      state={}, enabled=False)
        db.add(c)
        await db.flush()
        i = Identity(user_id=user.id, email="me@example.com", name="Me", connector_id=c.id, is_default=True)
        db.add(i)
        await db.flush()
        return i.id


async def _asked_to_send(user, ident, draft_id=None) -> tuple[int, int]:
    """Ambrose's request to send, waiting for approval: (outbox id, approval id)."""
    async with session_scope() as db:
        ob = Outbox(user_id=user.id, identity_id=ident, subject="assistant",
                    draft_jmap_id=str(draft_id) if draft_id else None, payload={"subject": "Re: Quote"},
                    recipients=["marco@example.org"], send_at=datetime.now(UTC), status="awaiting_approval")
        db.add(ob)
        await db.flush()
        pa = PendingAction(user_id=user.id, subject="assistant", action="mail.send", payload={"outbox_id": ob.id},
                           expires_at=datetime.now(UTC) + timedelta(days=1))
        db.add(pa)
        await db.flush()
        return ob.id, pa.id


async def test_sending_the_draft_yourself_withdraws_the_request(client, user, events):
    ident = await _ident(user)
    r = await client.post(f"{API}/drafts", json={"payload": {
        "identity_id": ident, "to": [{"email": "marco@example.org"}], "subject": "Re: Quote", "html": "<p>Yes</p>"}})
    assert r.status_code == 200, r.text
    draft_id = r.json()["draft"]["id"] if "draft" in r.json() else r.json()["id"]
    _, pid = await _asked_to_send(user, ident, draft_id)
    r = await client.post(f"{API}/send", json={"payload": {
        "identity_id": ident, "to": [{"email": "marco@example.org", "name": "Marco"}], "subject": "Re: Quote",
        "html": "<p>Yes</p>"}, "draft_id": int(draft_id)})
    assert r.status_code == 200, r.text
    assert events == [{"user": user.id, "type": "approval.decided", "outcome": "withdrawn",
                       "ids": [publicid.encode("approval", pid)]}]
    assert (await _jobs(user.id)) == [{"user_id": user.id, "ids": [pid], "outcome": "withdrawn"}]


async def test_cancelling_the_send_withdraws_the_request(client, user, events):
    ident = await _ident(user)
    oid, pid = await _asked_to_send(user, ident)
    r = await client.post(f"{API}/outbox/{oid}/cancel")
    assert r.status_code == 200, r.text
    assert [e["outcome"] for e in events] == ["withdrawn"]
    assert events[0]["ids"] == [publicid.encode("approval", pid)]


async def test_the_legacy_outbox_approve_sends_the_event(client, user, events):
    ident = await _ident(user)
    oid, pid = await _asked_to_send(user, ident)
    r = await client.post(f"{API}/outbox/{oid}/approve")
    assert r.status_code == 200, r.text
    assert [(e["outcome"], e["ids"]) for e in events] == [("approved", [publicid.encode("approval", pid)])]


async def test_an_undecided_request_sends_nothing(client, user, events):
    agent = await _token(client, ["read", "label"])
    await _ask_label(agent, "Waiting")
    assert events == [] and await _jobs(user.id) == []
    await agent.aclose()


# ---------- the push itself ----------


@pytest.fixture
def sent(monkeypatch):
    out: list[tuple[str, str, str, dict]] = []

    async def deliver(target, title, body, data, priority="default"):
        out.append((target.kind, title, body, data))
        return True

    monkeypatch.setattr(push, "deliver", deliver)
    return out


async def _target(user, kind, endpoint, events=None) -> None:
    async with session_scope() as db:
        db.add(PushTarget(user_id=user.id, kind=kind, endpoint=endpoint, device="d", events=events or []))


async def test_the_push_is_generic_and_reaches_only_devices_that_can_clear(user, sent):
    await _target(user, "unifiedpush", "https://ntfy.example.org/upAbc")
    await _target(user, "webpush", "https://fcm.googleapis.com/fcm/send/abc")
    await _target(user, "webpush", "https://web.push.apple.com/QAbc")   # Safari ends silent subscriptions
    await _target(user, "ntfy", "https://ntfy.example.org/mailr-topic")   # shows every message as text
    await _target(user, "unifiedpush", "https://ntfy.example.org/upMail", events=["new_mail"])  # no approvals here
    await push.push_approval_decided({"user_id": user.id, "ids": [7, 8], "outcome": "approved"}, None)
    assert sorted(k for k, *_ in sent) == ["unifiedpush", "webpush"]
    for _, title, body, data in sent:
        assert title == "" and body == ""
        assert data == {"type": "approval.decided", "outcome": "approved",
                        "ids": [publicid.encode("approval", 7), publicid.encode("approval", 8)]}
    # What the phone receives: the type, the ids and the outcome; no text, nothing of the request.
    doc = json.loads(push.up_payload("", "", sent[0][3]))
    assert set(doc) == {"title", "body", "type", "outcome", "ids"} and doc["title"] == doc["body"] == ""


async def test_many_ids_go_in_chunks(client, user, events):
    ids = list(range(1, 251))
    async with session_scope() as db:
        await permissions.announce_left(db, user.id, ids, "expired")
    jobs = await _jobs(user.id)
    assert [len(j["ids"]) for j in jobs] == [100, 100, 50]
    assert len(events) == 1 and len(events[0]["ids"]) == 250
    # A chunk of 100 public ids fits a UnifiedPush message.
    doc = push.up_payload("", "", {"type": "approval.decided", "outcome": "expired",
                                   "ids": [publicid.encode("approval", i) for i in jobs[0]["ids"]]})
    assert len(json.loads(doc)["ids"]) == 100
