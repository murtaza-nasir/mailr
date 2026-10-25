"""Agent tools, context builder, send gate for agents, local-only guard, chat loop, and MCP."""

import json
import os
import uuid

import httpx
import pytest
from helpers.agent import (  # noqa: F401
    FakeStream,
    _drop_endpoint,
    _endpoint,
    _events,
    _ingest,
    _seed_thread,
    _setup_identity,
    _text_turn,
    _token_client,
    _tool_turn,
)
from sqlalchemy import select, text

from mailr.db import sessionmaker
from mailr.models import ModelEndpoint, Outbox, User


async def test_context_builder(client, user, raw):
    _, me = await _setup_identity(user)
    m1, conv = await _seed_thread(user, raw, me)
    r = await client.post("/api/v1/context", json={"conversation_id": conv})
    assert r.status_code == 200, r.text
    b = r.json()
    assert r.headers["x-mailr-content"] == "untrusted"
    assert b["thread"]["conversation_id"] == conv
    body = b["thread"]["messages"][0]["body"]
    assert body["untrusted"] is True and "revised numbers" in body["content"]
    alice = next(x for x in b["people"] if x["email"] == "alice@acme.example")
    assert alice["emails_from_them"] >= 1 and alice["organization"] == "acme"
    assert any("I will send the draft deck" in c["text"]["content"] for c in b["open_items"]["my_recent_commitments"])
    assert any("revised numbers" in q["question"]["content"] for q in b["open_items"]["unanswered_questions_to_me"])
    assert b["my_past_replies"] and b["my_past_replies"][0]["to"] == ["alice@acme.example"]
    # Budget trimming keeps the bundle small.
    small = (await client.post("/api/v1/context", json={"conversation_id": conv, "budget_tokens": 1000})).json()
    assert len(json.dumps(small)) <= 1000 * 4 + 600


async def test_tool_scopes(client, user, raw):
    await _setup_identity(user)
    tok = (await client.post("/api/v1/tokens", json={"name": "reader", "scopes": ["read"]})).json()["token"]
    async with _token_client(client, tok) as a:
        names = {t["name"] for t in (await a.get("/api/v1/agent/tools")).json()["tools"]}
        assert "search_conversations" in names and "create_draft" not in names and "archive" not in names
        r = await a.post("/api/v1/agent/tools/create_draft", json={"to": ["x@example.org"], "subject": "hi", "body": "yo"})
        assert r.status_code == 400 and "draft" in r.json()["error"]
        r = await a.post("/api/v1/agent/tools/search_conversations", json={"q": ""})
        assert r.status_code == 200 and r.json()["ok"]


async def test_token_send_needs_approval(client, user, raw):
    _, me = await _setup_identity(user)
    m1, conv = await _seed_thread(user, raw, me)
    tok = (await client.post("/api/v1/tokens", json={"name": "writer", "scopes": ["read", "draft", "send"]})).json()["token"]
    async with _token_client(client, tok) as a:
        r = await a.post("/api/v1/agent/tools/create_draft",
                         json={"reply_to_message_id": m1, "mode": "reply", "body": "Numbers attached by Friday."})
        assert r.status_code == 200, r.text
        draft_id = r.json()["result"]["draft_id"]
        r = await a.post("/api/v1/agent/tools/send_draft", json={"draft_id": draft_id})
        # Nothing was sent: 202 with the pending approval.
        assert r.status_code == 202, r.text
        body = r.json()
        res = body["result"]
        assert res["status"] == "pending_approval" and body["approval"]["action"] == "mail.send"
        assert res["approval_url"].endswith(f"/approvals/{res['approval_id']}")
        oid = res["outbox_id"]
    async with sessionmaker()() as db:
        ob = await db.get(Outbox, oid)
        assert ob.status == "awaiting_approval" and ob.sent_at is None
        jobs = (await db.execute(text("SELECT count(*) FROM jobs WHERE kind='outbox.send' AND payload->>'outbox_id'=:o"),
                                 {"o": str(oid)})).scalar()
        assert jobs == 0
    # The draft keeps the quoted history and the reply headers.
    d = (await client.get(f"/api/v1/drafts/{draft_id}")).json()
    assert d["payload"]["in_reply_to_message_id"] == m1 and "mailr_quote" in d["payload"]["html"]
    assert d["payload"]["html"].index("Numbers attached") < d["payload"]["html"].index("mailr_quote")


async def test_agent_loop_search_then_draft(client, user, raw, fake_llm):
    _, me = await _setup_identity(user)
    m1, conv = await _seed_thread(user, raw, me)
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [
            _tool_turn("search_conversations", {"q": "budget"}),
            _tool_turn("create_draft", {"reply_to_message_id": m1, "mode": "reply", "body": "Will do by Friday."}, "c2"),
            _text_turn(f"Drafted a reply [c:{conv}]."),
        ]
        r = await client.post("/api/v1/agent/chat", json={"messages": [{"role": "user", "content": "Reply to Alice"}],
                                                          "context": {"conversation_id": conv}})
        assert r.status_code == 200
        evs = _events(r.text)
        kinds = [e["type"] for e in evs]
        assert kinds.count("tool") == 2 and "draft" in kinds and kinds[-1] == "done", kinds
        tools = [e for e in evs if e["type"] == "tool"]
        assert all(t["ok"] for t in tools), tools
        draft = next(e for e in evs if e["type"] == "draft")
        assert draft["payload"]["in_reply_to_message_id"] == m1
        # The model saw the tool results as tool messages, with untrusted markers.
        last = fake_llm.requests[-1]["body"]["messages"]
        assert any(m["role"] == "tool" and '"untrusted": true' in m["content"] for m in last)
        drafts = (await client.get("/api/v1/conversations", params={"view": "drafts"})).json()
        assert conv in [c["id"] for c in drafts["items"]]
    finally:
        await _drop_endpoint(ep)


async def test_builtin_agent_never_sends(client, user, raw, fake_llm):
    _, me = await _setup_identity(user)
    m1, conv = await _seed_thread(user, raw, me)
    ep = await _endpoint(user.id, "local")
    try:
        async with sessionmaker()() as db:
            from mailr.agent.tools import ToolCtx, run_tool
            from mailr.core.auth import ALL_SCOPES, Principal

            u = await db.get(User, user.id)
            p = Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"})
            ctx = ToolCtx(db=db, p=p, assistant=True, chat_session_id="test-session")
            d = await run_tool(ctx, "create_draft", {"reply_to_message_id": m1, "body": "ok"})
            s = await run_tool(ctx, "send_draft", {"draft_id": d.data["draft_id"]})
            # The assistant's send is an approval card for the user; nothing is sent or queued.
            assert s.data["status"] == "pending_approval" and s.pending["action"] == "mail.send"
            n = (await db.execute(select(Outbox).where(Outbox.user_id == user.id))).scalars().all()
            assert [o.status for o in n] == ["awaiting_approval"] and n[0].sent_at is None
    finally:
        await _drop_endpoint(ep)


async def test_local_only_never_reaches_hosted_model(client, user, raw, fake_llm):
    conn_id, me = await _setup_identity(user, data_class="local_only")
    secret = raw("Student grade appeal", frm="Student <s1@uni.example>", to=me,
                 body="SECRET-FERPA-CONTENT my grade in DATA 520")
    m, conv = await _ingest(user.id, secret, connector_id=conn_id)
    ep = await _endpoint(user.id, "hosted")
    try:
        fake_llm.script = [_tool_turn("get_conversation", {"conversation_id": conv}), _text_turn("Summary.")]
        r = await client.post("/api/v1/agent/chat", json={"messages": [{"role": "user", "content": "What's new?"}]})
        evs = _events(r.text)
        hosted = [q for q in fake_llm.requests if ep in q["url"]]
        # The first call (before any mail was read) may go to the hosted endpoint; nothing after it.
        assert len(hosted) == 1
        assert all("SECRET-FERPA-CONTENT" not in json.dumps(q["body"]) for q in hosted)
        later = fake_llm.requests[1:]
        if later:  # a local endpoint served the sensitive turn
            assert all(ep not in q["url"] for q in later)
        else:
            assert evs[-1]["type"] == "error" and "local-only" in evs[-1]["message"]
    finally:
        await _drop_endpoint(ep)


async def test_mcp_server_in_process(client, user, raw):
    from mcp import Client

    from mailr.mcp.server import build_server

    _, me = await _setup_identity(user)
    await _seed_thread(user, raw, me)
    tok = (await client.post("/api/v1/tokens", json={"name": "mcp", "scopes": ["read"]})).json()["token"]
    server = build_server(url="http://test", token=tok, transport=client._transport)
    async with Client(server) as c:
        tools = (await c.list_tools()).tools
        names = {t.name for t in tools}
        assert "search_conversations" in names and "create_draft" not in names
        assert all("untrusted" in (t.description or "") for t in tools)
        res = await c.call_tool("search_conversations", {"q": "budget"})
        assert not res.is_error if hasattr(res, "is_error") else not res.isError
        data = json.loads(res.content[0].text)
        assert data["items"] and "budget" in data["items"][0]["subject"]["content"].lower()
        bad = await c.call_tool("create_draft", {"to": ["a@b.c"], "body": "x"})
        assert (bad.is_error if hasattr(bad, "is_error") else bad.isError)


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("MAILR_LIVE") != "1", reason="set MAILR_LIVE=1 to call the real model")
async def test_live_draft_with_local_model(client, user, raw):
    _, me = await _setup_identity(user)
    m1, conv = await _seed_thread(user, raw, me)
    name = f"live-{uuid.uuid4().hex[:6]}"
    async with sessionmaker()() as db:
        db.add(ModelEndpoint(name=name, base_url=os.environ.get("MAILR_LIVE_URL", "http://llm-host.internal:5001/v1"), locality="local",
                             models=["qwen3.8-27b"], defaults={"chat": "qwen3.8-27b"}))
        u = await db.get(User, user.id)
        u.settings = {**(u.settings or {}), "models": {"chat": {"endpoint": name, "model": "qwen3.8-27b"}}}
        await db.commit()
    try:
        r = await client.post("/api/v1/agent/draft", json={"conversation_id": conv,
                                                           "instructions": "Say the numbers come Thursday; confirm Oct 12."})
        assert r.status_code == 200, r.text
        assert "Thursday" in r.json()["payload"]["html"]
    finally:
        await _drop_endpoint(name)


async def test_push_new_mail_is_minimal(client, user, raw, monkeypatch):
    from mailr.core import push
    from mailr.models import Message, PushTarget

    sent = []

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, content=None, headers=None):
            sent.append({"url": url, "content": content, "headers": headers})
            return httpx.Response(200)

    from mailr.core import netguard

    async def fetch_capped(client, method, url, *, limit, deadline=30.0, content=None, headers=None, **kw):
        return await FakeClient().post(url, content=content, headers=headers), b""

    monkeypatch.setattr(netguard, "fetch_capped", fetch_capped)
    conn_id, me = await _setup_identity(user, data_class="local_only")
    mid, _ = await _ingest(user.id, raw("Grade question", frm="Student <s2@uni.example>", to=me,
                                        body="PRIVATE-BODY"), connector_id=conn_id)
    nid, _ = await _ingest(user.id, raw("Lunch", frm="Carol Diaz <carol@example.org>", to=me, body="PRIVATE-2"))
    async with sessionmaker()() as db:
        db.add(PushTarget(user_id=user.id, kind="ntfy", endpoint="http://ntfy.invalid/t1", events=[]))
        u = await db.get(type(user), user.id)  # all new mail notifies (core/notifyprefs.py)
        u.settings = {**(u.settings or {}), "mail_notifications": "all"}
        for x in (mid, nid):
            (await db.get(Message, x)).intel_state = "done"
        await db.commit()
    await push.push_new_mail({"message_id": mid}, None)
    await push.push_new_mail({"message_id": nid}, None)
    assert len(sent) == 2
    assert sent[0]["headers"]["Title"] == "Mailr" and sent[0]["content"] == b"New message"
    assert sent[1]["headers"]["Title"] == "Mail from Carol Diaz"
    assert all(b"PRIVATE" not in s["content"] for s in sent)


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("MAILR_LIVE") != "1", reason="set MAILR_LIVE=1 to call the real model")
async def test_live_agent_chat_tool_loop(client, user, raw):
    _, me = await _setup_identity(user)
    m1, conv = await _seed_thread(user, raw, me)
    name = f"live-{uuid.uuid4().hex[:6]}"
    async with sessionmaker()() as db:
        db.add(ModelEndpoint(name=name, base_url=os.environ.get("MAILR_LIVE_URL", "http://llm-host.internal:5001/v1"),
                             locality="local", models=["qwen3.8-27b"], defaults={"chat": "qwen3.8-27b"}))
        u = await db.get(User, user.id)
        u.settings = {**(u.settings or {}), "models": {"chat": {"endpoint": name, "model": "qwen3.8-27b"}}}
        await db.commit()
    try:
        r = await client.post("/api/v1/agent/chat", json={"messages": [{"role": "user", "content":
            "Find Alice's email about the Q4 budget and draft a short reply saying the numbers come Thursday."}]},
            timeout=300)
        evs = _events(r.text)
        print([(e["type"], e.get("name"), e.get("result_summary")) for e in evs if e["type"] != "delta"])
        assert any(e["type"] == "tool" and e["name"] in ("search_conversations", "search_semantic") for e in evs)
        assert any(e["type"] == "draft" for e in evs)
        assert evs[-1]["type"] == "done"
    finally:
        await _drop_endpoint(name)
