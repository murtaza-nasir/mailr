# ACT-SURFACES: api assistant
"""The assistant's Speakr read tools: schema and scope, offered only with a
visible connector, results wrapped as untrusted, transcript ranges and truncation, both transcript
key sets and plain text, keyword search over metadata and content, and the REST routes they share.
Covers recording.view and recording.search."""

import uuid

import pytest
from helpers.speakr_tools import _ctx
from speakr_fake import connect, speakr  # noqa: F401

from mailr.agent import speakr_tools
from mailr.agent.tools import TOOLS, UNTRUSTED_TOOLS, ToolError, available_tools, run_tool
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.models import ApiToken, User


def _long(n=60):
    return [(i * 10.0, "Dana" if i % 2 else "Evan", f"Point number {i} about the course redesign.") for i in range(n)]


def test_speakr_tool_schemas_and_scope():
    for name in speakr_tools.ALL_TOOLS:
        t = TOOLS[name]
        assert t.scope == "recordings", name
        assert t.parameters["type"] == "object" and t.parameters["additionalProperties"] is False
    assert set(speakr_tools.READ_TOOLS) <= UNTRUSTED_TOOLS
    no_rec = Principal(user=User(id=0, email="x@y"), scopes=set(ALL_SCOPES) - {"recordings", "admin"})
    assert not {t.name for t in available_tools(no_rec)} & set(speakr_tools.ALL_TOOLS)


async def test_speakr_tools_hidden_without_connector_and_scope(client, user, speakr):
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        assert await speakr_tools.hidden_tools(db, ctx.p) == set(speakr_tools.ALL_TOOLS)
        with pytest.raises(ToolError, match="no Speakr account"):
            await run_tool(ctx, "list_recordings", {})
    speakr.add(1)
    await connect(client, speakr)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        # ask_recording stays hidden: an older Speakr has no chat sources, and the account is local-only.
        assert await speakr_tools.hidden_tools(db, ctx.p) == {"ask_recording"}
        ctx = await _ctx(db, user.id, scopes={"read"})
        with pytest.raises(ToolError, match="recordings"):
            await run_tool(ctx, "list_recordings", {})


async def test_speakr_tools_results_untrusted_ranges_and_truncation(client, user, speakr):
    speakr.add(1, "Course redesign", segments=_long())
    speakr.add(2, "Old keys", key_set="old")
    speakr.add(3, "Dictation", plain="First paragraph of a memo.\nSecond paragraph about Friday.")
    await connect(client, speakr)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        lst = await run_tool(ctx, "list_recordings", {"q": "redesign"})
        assert [x["recording_id"] for x in lst.data["items"]] == [1]
        assert lst.data["items"][0]["title"] == {"untrusted": True, "content": "Course redesign"}
        assert lst.data["items"][0]["cite"] == "[[r:1]]"

        got = await run_tool(ctx, "get_recording", {"recording_id": 1, "include": ["summary", "notes", "events", "links"]})
        assert got.data["summary"]["untrusted"] and "Ship the budget" in got.data["summary"]["content"]
        assert got.data["events"]["untrusted"] and got.data["links"] == []
        assert got.data["url"] == "https://speakr.test/recordings/1"

        tr = await run_tool(ctx, "get_transcript", {"recording_id": 1, "start": 100, "end": 200})
        segs = tr.data["segments"]["content"]
        assert tr.data["segments"]["untrusted"] is True
        assert [s["t"] for s in segs][0] == 90.0 and segs[-1]["t"] == 200.0  # 90 ends at 100: in range
        assert not tr.data["truncated"]
        cut = await run_tool(ctx, "get_transcript", {"recording_id": 1, "max_chars": 500})
        assert cut.data["truncated"] and cut.data["next_start"] is not None
        more = await run_tool(ctx, "get_transcript", {"recording_id": 1, "start": cut.data["next_start"], "max_chars": 500})
        assert more.data["segments"]["content"][0]["t"] >= cut.data["next_start"] - 10
        spk = await run_tool(ctx, "get_transcript", {"recording_id": 1, "speaker": "dana", "q": "number 37"})
        assert [s["text"] for s in spk.data["segments"]["content"]] == ["Point number 37 about the course redesign."]

        new = (await run_tool(ctx, "get_transcript", {"recording_id": 2})).data["segments"]["content"]
        assert new[2] == {"t": 754.0, "end": 790.0, "speaker": "Dana", "text": "We agreed to ship the budget on Friday."}
        plain = (await run_tool(ctx, "get_transcript", {"recording_id": 3})).data
        assert [s["text"] for s in plain["segments"]["content"]] == ["First paragraph of a memo.",
                                                                     "Second paragraph about Friday."]
        assert plain["timed"] is False

        s = await run_tool(ctx, "search_recordings", {"q": "budget Friday"})
        hits = s.data["hits"]["content"]
        assert any(h["recording_id"] == 2 and h["t"] == 754.0 and h["cite"] == "[[r:2@754]]" for h in hits)
        summary = await run_tool(ctx, "get_summary", {"recording_id": 1})
        assert summary.data["has_summary"] and summary.data["summary"]["untrusted"]
        with pytest.raises(ToolError, match="not found"):
            await run_tool(ctx, "get_recording", {"recording_id": 99})
    r = await client.get("/api/v1/speakr/recordings/0/1/transcript", params={"start": 100, "end": 120})
    assert r.status_code == 404  # wrong connector id
    conn_id = (await client.get("/api/v1/speakr/status")).json()["connectors"][0]["id"]
    r = await client.get(f"/api/v1/speakr/recordings/{conn_id}/1/transcript", params={"start": 100, "end": 120})
    assert [s["t"] for s in r.json()["segments"]] == [90.0, 100.0, 110.0, 120.0]
    r = await client.get(f"/api/v1/speakr/recordings/{conn_id}/1", params={"include": "summary,notes,speakers,links"})
    assert r.json()["speakers"][0]["name"] == "Dana" and r.json()["candidates"] == []
    assert (await client.get("/api/v1/speakr/search", params={"q": "memo"})).json()["hits"][0]["recording_id"] == 3


async def test_speakr_token_limited_to_other_connectors_gets_not_found(client, user, speakr):
    speakr.add(1)
    conn = await connect(client, speakr)
    async with sessionmaker()() as db:
        tok = ApiToken(user_id=user.id, name="limited", prefix="mlr_x", token_hash=uuid.uuid4().hex,
                       scopes=["read", "recordings"], connector_ids=[conn["id"] + 10_000], local_only=True)
        db.add(tok)
        await db.flush()
        ctx = await _ctx(db, user.id, scopes={"read", "recordings"}, token=tok)
        assert await speakr_tools.hidden_tools(db, ctx.p) == set(speakr_tools.ALL_TOOLS)
        with pytest.raises(ToolError, match="no Speakr account"):
            await run_tool(ctx, "get_recording", {"recording_id": 1})
        tok.connector_ids = [conn["id"]]
        await db.flush()
        ok = await run_tool(ctx, "get_recording", {"recording_id": 1, "include": []})
        assert ok.data["recording_id"] == 1
