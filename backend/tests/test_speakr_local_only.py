"""Local-only Speakr connectors: their recordings reach the
built-in assistant only on a local model and then make the session sensitive, so the router refuses
a hosted endpoint for the next call; a chat on a hosted model is told recordings are hidden; a
token that is not local-only gets not-found; Speakr's own chat, summarize and Inquire routes are
never called while speakr_models_local is off (the default)."""

import uuid

import pytest
from speakr_fake import connect, speakr  # noqa: F401

from mailr.agent.loop import result_is_sensitive
from mailr.agent.speakr_tools import HIDDEN
from mailr.agent.tools import ToolCtx, ToolError, run_tool
from mailr.core import outbound
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.intel import citations, llm
from mailr.models import ModelEndpoint, User


async def _ctx(db, uid, *, local_model):
    u = await db.get(User, uid)
    return ToolCtx(db=db, p=Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"}), assistant=True,
                   chat_session_id=f"s-{uuid.uuid4().hex[:6]}", local_model=local_model)


async def test_speakr_local_only_session_turns_sensitive_and_hosted_is_refused(client, user, speakr):
    speakr.add(1, "Advising meeting")
    conn = await connect(client, speakr)
    assert conn["data_class"] == "local_only"
    name = f"hosted-{uuid.uuid4().hex[:6]}"
    async with sessionmaker()() as db:
        db.add(ModelEndpoint(name=name, base_url="https://api.example.invalid/v1", locality="hosted",
                             models=["big"], defaults={"chat": "big"}, enabled=True))
        await db.commit()
        # On a local model: readable, and the result is sensitive.
        ctx = await _ctx(db, user.id, local_model=True)
        res = await run_tool(ctx, "get_recording", {"recording_id": 1})
        assert res.sensitive and await result_is_sensitive(db, user.id, res)
        token = llm.FORCE_ENDPOINT.set(name)
        try:
            with pytest.raises(llm.LocalOnlyViolation):
                await llm.resolve(db, ctx.p.user, "chat", sensitive=True)
            # The recording read above taints this context (core/outbound.py); another request,
            # with nothing local-only in it, may use the hosted model.
            with outbound.scope():
                assert (await llm.resolve(db, ctx.p.user, "chat", sensitive=False)).endpoint.name == name
        finally:
            llm.FORCE_ENDPOINT.reset(token)
        # On a hosted model: hidden, and said so.
        ctx = await _ctx(db, user.id, local_model=False)
        lst = await run_tool(ctx, "list_recordings", {})
        assert lst.data["items"] == [] and lst.data["note"] == HIDDEN and not lst.sensitive
        with pytest.raises(ToolError, match="hidden from this model"):
            await run_tool(ctx, "get_transcript", {"recording_id": 1})
        await db.delete(await db.get(ModelEndpoint, (await db.execute(
            ModelEndpoint.__table__.select().where(ModelEndpoint.name == name))).first().id))
        await db.commit()


async def test_speakr_local_only_needs_a_local_only_token(client, user, speakr):
    speakr.add(1)
    await connect(client, speakr)
    plain = (await client.post("/api/v1/tokens", json={"name": "hosted-agent", "scopes": ["read", "recordings"],
                                                       "local_only": False})).json()["token"]
    h = {"Authorization": f"Bearer {plain}"}
    assert (await client.get("/api/v1/speakr/recordings", headers=h)).json()["items"] == []
    assert (await client.get("/api/v1/speakr/status", headers=h)).json()["connected"] is False
    r = await client.get("/api/v1/speakr/search", params={"q": "budget"}, headers=h)
    assert r.json()["hits"] == []
    local = (await client.post("/api/v1/tokens", json={"name": "local-agent", "scopes": ["read", "recordings"],
                                                       "local_only": True})).json()["token"]
    h = {"Authorization": f"Bearer {local}"}
    assert [x["recording_id"] for x in (await client.get("/api/v1/speakr/recordings", headers=h)).json()["items"]] == [1]


async def test_speakr_models_are_never_called_while_models_local_is_off(client, user, speakr):
    speakr.add(1, "Budget call")
    conn = await connect(client, speakr)
    assert conn["config"]["speakr_models_local"] is False
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, local_model=True)
        for name, args in (("list_recordings", {}), ("get_recording", {"recording_id": 1, "include": ["summary", "notes"]}),
                           ("get_transcript", {"recording_id": 1}), ("get_summary", {"recording_id": 1}),
                           ("search_recordings", {"q": "budget"}), ("recordings_for_event", {"uid": "x"})):
            await run_tool(ctx, name, args)
        await citations.resolve(db, ctx.p, 'Friday [[r:1@754 "ship the budget on Friday"]].')
    assert not [c for c in speakr.calls if c.endswith(("/chat", "/summarize")) or "inquire" in c]
