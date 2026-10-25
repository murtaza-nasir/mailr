# ACT-SURFACES: api
"""approval.approve_many: "Allow all n from this chat" decides several waiting requests of one
requester, each as if its own card had been approved once. No rule is stored; a request already
decided is skipped; requests from different agents are refused together."""

from httpx import AsyncClient
from sqlalchemy import func, select

from mailr.db import sessionmaker
from mailr.models import PermissionRule


async def _token(client, scopes, name="claude-code"):
    r = await client.post("/api/v1/tokens", json={"name": name, "scopes": scopes})
    assert r.status_code == 200, r.text
    a = AsyncClient(transport=client._transport, base_url="http://test")
    a.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return a


async def _create_label(agent, name):
    r = await agent.post("/api/v1/labels", json={"name": name})
    assert r.status_code == 202, r.text
    return r.json()["approval"]["id"]


async def _rules(user_id) -> int:
    async with sessionmaker()() as db:
        return (await db.execute(select(func.count()).select_from(PermissionRule)
                                 .where(PermissionRule.user_id == user_id, PermissionRule.source == "grant"))).scalar()


async def test_approve_many_decides_each_once(client, user):
    agent = await _token(client, ["read", "label"])
    ids = [await _create_label(agent, n) for n in ("Receipts", "Travel", "School")]
    # One is decided on its own card first: the batch skips it.
    r = await client.post(f"/api/v1/approvals/{ids[2]}/decide", json={"decision": "deny"})
    assert r.status_code == 200, r.text
    r = await client.post("/api/v1/approvals/decide-many", json={"ids": ids, "decision": "approve"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    assert [c["id"] for c in body["decided"]] == ids[:2]
    assert all(c["status"] == "approved" and c["decided_by"] == "user" for c in body["decided"])
    assert body["skipped"] == [{"id": ids[2], "status": "denied"}]
    # Approve once: no standing rule.
    assert await _rules(user.id) == 0
    # A later request of the same kind still asks.
    later = await _create_label(agent, "Later")
    assert (await client.get(f"/api/v1/approvals/{later}")).json()["status"] == "pending"
    await agent.aclose()


async def test_approve_many_refuses_mixed_requesters_and_agents(client, user):
    a = await _token(client, ["read", "label"])
    b = await _token(client, ["read", "label"], name="hermes")
    x = await _create_label(a, "One")
    y = await _create_label(b, "Two")
    r = await client.post("/api/v1/approvals/decide-many", json={"ids": [x, y]})
    assert r.status_code == 400
    for aid in (x, y):
        assert (await client.get(f"/api/v1/approvals/{aid}")).json()["status"] == "pending"
    # An agent token cannot decide its own requests.
    r = await a.post("/api/v1/approvals/decide-many", json={"ids": [x]})
    assert r.status_code == 403
    await a.aclose()
    await b.aclose()
