# ACT-SURFACES: api
"""Step 4 of docs/design/memory-quality.md: topics under another wording are one entity (A3), an
agent's `remember` does not add what a note already says (D), and the weekly tidy pass proposes
merges and retirements of saved notes that change nothing until kept (B7; memory.tidy).

No real model is called: completions come from scripted fakes."""

import json
import uuid
from datetime import UTC, datetime

from helpers.agent import _drop_endpoint, _endpoint
from helpers.assistant_knowledge import fake  # noqa: F401  (fixture)
from sqlalchemy import select

from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.models import AssistantProfile, Entity, Memory, User


async def _p(db, user_id):
    return Principal(user=await db.get(User, user_id), scopes=set(ALL_SCOPES) - {"admin"})


def test_topic_wordings():
    from mailr.knowledge.memory import topic_key

    assert topic_key("2027 PDC Conference") == topic_key("PDC 2027") == topic_key("the PDC meeting")
    assert topic_key("AI Skills project") == topic_key("ai skills")
    assert topic_key("Plains Decision Conference") != topic_key("PDC")
    assert topic_key("2027") == frozenset() and topic_key("AI") == frozenset()


async def test_a_topic_under_another_wording_is_the_same_entity(client, user):
    from mailr.knowledge import memory

    async with sessionmaker()() as db:
        p = await _p(db, user.id)
        e, _ = await memory.upsert_entity(db, p, {"kind": "topic", "name": "PDC 2027"})
        m, _ = await memory.remember(db, p, {"text": "You review for the AI track",
                                             "about": {"name": "2027 PDC Conference", "kind": "project"}})
        assert m.entity_id == e.id and "2027 PDC Conference" in (await db.get(Entity, e.id)).aliases
        # People are matched exactly.
        a, _ = await memory.upsert_entity(db, p, {"kind": "person", "name": "Ana Ruiz"})
        n, _ = await memory.remember(db, p, {"text": "Ana chairs risk", "about": {"name": "Ruiz Ana", "kind": "person"}})
        assert n.entity_id != a.id
        await db.commit()


async def test_an_agents_remember_does_not_add_what_a_note_says(client, user, monkeypatch):
    from embedfake import embedding_endpoint

    from mailr.agent import knowledge_tools
    from mailr.agent.tools import ToolCtx
    from mailr.intel import jev, llm
    from mailr.knowledge import memory, relevance
    from mailr.models import ModelEndpoint

    decider = llm.Resolved(ModelEndpoint(name="decider", base_url="http://decider.invalid/v1", adapter="jev", locality="local",
                                      models=["decider"]), "decider", "decider")

    async def decide(r, state, questions, timeout=10, **k):
        return {"same": {"noul": 0.7 if "accountant" in state["new"]["text"] else 0.01}}

    async def endpoint(db, u):
        return decider

    monkeypatch.setattr(jev, "decide", decide)
    monkeypatch.setattr(relevance, "check_endpoint", endpoint)
    async with embedding_endpoint(monkeypatch):
        async with sessionmaker()() as db:
            p = await _p(db, user.id)
            saved, _ = await memory.remember(db, p, {"text": "Sam Ortiz is your accountant"})
            await memory.embed_pending(db, user.id)
            await db.commit()
            ctx = ToolCtx(db=db, p=p, assistant=True, chat_session_id=f"s-{uuid.uuid4().hex[:6]}", session_sensitive=False)
            res = await knowledge_tools.remember(ctx, {"text": "Sam Ortiz is your accountant now"})
            assert res.data["status"] == "already_known" and res.data["memory"]["id"] == saved.id
            n = len((await db.execute(select(Memory).where(Memory.user_id == user.id))).scalars().all())
            assert n == 1


def _tidy(merges=(), retire=()):
    return json.dumps({"merges": list(merges), "retire": list(retire)})


async def _notes(user_id, texts):
    async with sessionmaker()() as db:
        p = await _p(db, user_id)
        e, _ = await __import__("mailr.knowledge.memory", fromlist=["x"]).upsert_entity(
            db, p, {"kind": "topic", "name": "Plains Conference"})
        rows = [Memory(user_id=user_id, entity_id=e.id, kind="fact", text=t, source="extracted", status="active",
                       refs=[{"message_id": i + 1, "conversation_id": i + 1}], sensitive=False, pinned=False,
                       use_count=0) for i, t in enumerate(texts)]
        db.add_all(rows)
        await db.commit()
        return [r.id for r in rows]


async def test_tidy_proposes_a_merge_and_keeping_it_leaves_one_note_with_every_text(client, user, fake):
    from mailr.knowledge import tasks

    ids = await _notes(user.id, ["You review for the MIS track", "You review for the AI track",
                                 "You agreed to review one paper by 16 November"])
    ep = await _endpoint(user.id, "local")
    try:
        fake.replies = [_tidy(merges=[{"text": "You review for the MIS and AI tracks", "kind": "fact",
                                       "replaces": [ids[0], ids[1]]}],
                              retire=[{"id": ids[2], "reason": "the review was due 16 Nov"}])]
        res = await tasks.tidy_for_user(user.id, force=True)
        assert res["proposed"] == 2, res
        assert "[note " in fake.posts[-1]["body"]["messages"][1]["content"]
        # Nothing changed yet.
        sugg = (await client.get("/api/v1/assistant/memories", params={"status": "suggested"})).json()["items"]
        merge = next(s for s in sugg if s["merges"])
        retire = next(s for s in sugg if s["retires"])
        assert merge["replaces"]["id"] == ids[0] and [x["id"] for x in merge["merges"]] == [ids[1]]
        assert retire["retires"] == {"id": ids[2], "text": "You agreed to review one paper by 16 November",
                                     "reason": "the review was due 16 Nov"}
        # Keep the merge: one note, both earlier texts under it, sources kept.
        out = (await client.post(f"/api/v1/assistant/memories/{merge['id']}/accept")).json()
        assert out["status"] == "active" and out["text"] == "You review for the MIS and AI tracks"
        assert sorted(h["text"] for h in out["merged_from"]) == ["You review for the AI track", "You review for the MIS track"]
        assert {"message_id": 1, "conversation_id": 1} in out["refs"]
        active = {m["id"] for m in (await client.get("/api/v1/assistant/memories")).json()["items"]}
        assert ids[0] not in active and ids[1] not in active and out["id"] in active
        # Keep the retirement: the note stops being used (expired, still listed).
        out = (await client.post(f"/api/v1/assistant/memories/{retire['id']}/accept")).json()
        assert out["id"] == ids[2] and out["expires_at"] is not None
        async with sessionmaker()() as db:
            m = await db.get(Memory, ids[2])
            assert m.expires_at <= datetime.now(UTC)
    finally:
        await _drop_endpoint(ep)


async def test_a_declined_tidy_proposal_is_not_made_again_and_tidying_waits_a_week(client, user, fake):
    from mailr.knowledge import tasks

    ids = await _notes(user.id, ["You teach on Tuesdays", "You teach Chemistry 101", "You teach at the university"])
    ep = await _endpoint(user.id, "local")
    try:
        fake.replies = [_tidy(retire=[{"id": ids[0], "reason": "old"}])]
        assert (await tasks.tidy_for_user(user.id, force=True))["proposed"] == 1
        (s,) = (await client.get("/api/v1/assistant/memories", params={"status": "suggested"})).json()["items"]
        assert (await client.post(f"/api/v1/assistant/memories/{s['id']}/reject")).status_code == 200
        async with sessionmaker()() as db:
            st = (await db.get(AssistantProfile, user.id)).state
            assert st["tidy_declined"] == [f"retire:{ids[0]}"] and not st.get("rejected")
        fake.replies = [_tidy(retire=[{"id": ids[0], "reason": "old"}])]
        assert (await tasks.tidy_for_user(user.id, force=True))["proposed"] == 0
        assert await tasks.tidy_for_user(user.id) == {"skipped": "ran recently"}
        # Tidy now (the Memory page's button) queues a pass.
        r = await client.post("/api/v1/assistant/memories/tidy")
        assert r.status_code == 200 and r.json() == {"queued": True}
    finally:
        await _drop_endpoint(ep)
