"""The memory relevance gate (knowledge/relevance.py): which notes reach
Ambrose's prompt, the decision model's check, pinned notes, the index of what exists, the recall
tool, "Used memories" and the background features. No real model is called."""

import asyncio
import uuid

import pytest
from helpers.agent import _drop_endpoint, _endpoint, _text_turn, _tool_turn
from helpers.assistant_knowledge import _all_events, fake  # noqa: F401
from sqlalchemy import delete, select

from mailr.db import sessionmaker
from mailr.models import ChatMessage, ModelEndpoint, User


async def _principal(user_id):
    from mailr.core.auth import ALL_SCOPES, Principal

    db = sessionmaker()()
    u = await db.get(User, user_id)
    return db, Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"})


async def _notes(user_id):
    """A few notes: the user's own, a person's, a loose one and a pinned one. Returns key → id."""
    from mailr.knowledge import memory

    db, p = await _principal(user_id)
    async with db:
        me = await memory.self_entity(db, p, create=True)
        sam, _ = await memory.upsert_entity(db, p, {"kind": "person", "name": "Sam Ortiz", "aliases": ["Sam"],
                                                     "emails": ["sam@example.net"]})
        club, _ = await memory.upsert_entity(db, p, {"kind": "organization", "name": "Student AI club"})
        ids = {}
        for key, data in {
            "teach": {"text": "You teach Statistics on Tuesdays.", "entity_id": me.id},
            "club": {"text": "You advise the student AI club.", "entity_id": club.id},
            "sam": {"text": "Sam Ortiz is your accountant.", "entity_id": sam.id},
            "hoa": {"text": "The homeowners' association meets monthly."},
            "pin": {"text": "You sign emails with your first name.", "pinned": True, "kind": "preference"},
        }.items():
            m, _ = await memory.remember(db, p, data)
            ids[key] = m.id
        await db.commit()
    return ids


@pytest.fixture
async def jev(monkeypatch):
    """A local Jev-protocol endpoint whose answers a test scripts: p(yes) by note text."""
    from mailr.intel import jev as jev_mod

    name = f"test-decider-{uuid.uuid4().hex[:6]}"
    async with sessionmaker()() as db:
        db.add(ModelEndpoint(name=name, base_url=f"http://{name}.invalid/v1", adapter="jev", locality="local",
                             models=["decider"], defaults={"classify": "decider"}, enabled=True))
        await db.commit()

    class H:
        answers: dict[str, float] = {}
        delay = 0.0
        fail = False
        asked: list[dict] = []

    async def decide(r, state, questions, timeout=10):
        H.asked.append(state)
        if H.delay:
            await asyncio.sleep(H.delay)
        if H.fail:
            return None
        text = state["note"]["text"]
        return {"helps": {"noul": H.answers.get(text, 0.9)}}

    monkeypatch.setattr(jev_mod, "decide", decide)
    try:
        yield H
    finally:
        async with sessionmaker()() as db:
            await db.execute(delete(ModelEndpoint).where(ModelEndpoint.name == name))
            await db.commit()


def test_plain_commands():
    from mailr.knowledge.relevance import plain_command

    for q in ("Can you also add razors", "archive this", "Please snooze this until Monday", "label this as Receipts",
              "ok, delete the newsletter", "Could you mark everything as read?", "Remind me to call Sam",
              "Ambrose, create a task to pay the bill", "move this to Travel"):
        assert plain_command(q), q
    for q in ("What do I teach?", "Draft a reply to Sam", "Who is my accountant?", "When is the meeting?",
              "Find a time for lunch", "addresses of my clients", ""):
        assert not plain_command(q), q


async def test_screenshot_case_gets_no_notes(client, user):
    """"Can you also add razors": nothing about the student club, the HOA, a person, not even the
    pinned note (a plain command)."""
    from mailr.knowledge import memory

    await _notes(user.id)
    db, p = await _principal(user.id)
    async with db:
        text, info = await memory.context_block(db, p, query="Can you also add razors", query_sensitive=False)
    assert info["memory_ids"] == [] and info["relevance"]["skipped"] == "command"
    assert "[mem:" not in text


async def test_command_naming_a_person_with_notes_uses_them(client, user):
    from mailr.knowledge import memory

    ids = await _notes(user.id)
    db, p = await _principal(user.id)
    async with db:
        _, info = await memory.context_block(db, p, query="Remind me to send Sam the receipts", query_sensitive=False)
    assert ids["sam"] in info["memory_ids"] and info["relevance"]["skipped"] is None
    assert info["why"][ids["sam"]].startswith("about Sam Ortiz")
    # Pinned notes are "always" notes, counted apart.
    assert info["pinned_ids"] == [ids["pin"]] and ids["pin"] in info["memory_ids"]


async def test_no_note_without_a_reason(client, user):
    """Without a link, a content word or a close meaning, no note qualifies (only the pinned one)."""
    from mailr.knowledge import memory

    ids = await _notes(user.id)
    db, p = await _principal(user.id)
    async with db:
        _, info = await memory.context_block(db, p, query="What's the weather tomorrow?", query_sensitive=False)
        assert info["memory_ids"] == [ids["pin"]]
        _, info = await memory.context_block(db, p, query="What do I teach?", query_sensitive=False)
        assert ids["teach"] in info["memory_ids"] and "words: teach" in info["why"][ids["teach"]]
        # A plural finds the singular ("clubs" → "club").
        _, info = await memory.context_block(db, p, query="Which clubs do I advise?", query_sensitive=False)
        assert ids["club"] in info["memory_ids"]


async def test_vector_threshold(client, user, monkeypatch):
    """Notes found only by meaning qualify under the embedding model's distance threshold."""
    from embedfake import embedding_endpoint

    from mailr.config import get_settings
    from mailr.knowledge import memory, relevance

    async with embedding_endpoint(monkeypatch):
        db, p = await _principal(user.id)
        async with db:
            m, _ = await memory.remember(db, p, {"text": "The quarterly revenue forecast is due in May"})
            await db.commit()
            await memory.embed_pending(db, user.id)
            await db.commit()
            hits = await memory.recall_hits(db, p, "profit projections", gated=True, query_sensitive=False)
            assert [h.memory.id for h in hits] == [m.id] and "similar in meaning" in hits[0].why()
            assert hits[0].distance is not None and not hits[0].linked
            monkeypatch.setattr(get_settings(), "memory_max_distance", 0.01)
            assert relevance.max_distance("fake-embed:512") == 0.01
            assert await memory.recall_hits(db, p, "profit projections", gated=True, query_sensitive=False) == []
            # The Memory page's search stays ungated.
            assert [x.id for x in await memory.recall(db, p, "profit projections", query_sensitive=False)] == [m.id]


async def test_check_drops_unhelpful_notes_and_skips_linked_ones(client, user, jev):
    from mailr.knowledge import memory

    ids = await _notes(user.id)
    jev.answers = {"You teach Statistics on Tuesdays.": 0.05}
    db, p = await _principal(user.id)
    async with db:
        _, info = await memory.context_block(db, p, query="Sam asked what I teach on Tuesdays", query_sensitive=False)
    # The user's own note matched on words but the check said no; Sam's note is linked, never asked.
    assert ids["teach"] not in info["memory_ids"] and ids["sam"] in info["memory_ids"]
    assert [s["note"]["text"] for s in jev.asked] == ["You teach Statistics on Tuesdays."]
    assert jev.asked[0]["request"] == "Sam asked what I teach on Tuesdays"
    rel = info["relevance"]
    assert rel["checked"] == 1 and rel["answered"] == 1 and rel["dropped"] == 1 and rel["ms"] >= 0
    # The pinned note never goes to the check.
    assert ids["pin"] in info["pinned_ids"]


async def test_check_failure_or_timeout_keeps_the_notes(client, user, jev, monkeypatch):
    from mailr.config import get_settings
    from mailr.knowledge import memory

    ids = await _notes(user.id)
    jev.answers = {"You teach Statistics on Tuesdays.": 0.05}
    jev.fail = True
    db, p = await _principal(user.id)
    async with db:
        _, info = await memory.context_block(db, p, query="What do I teach?", query_sensitive=False)
        assert ids["teach"] in info["memory_ids"] and info["relevance"]["answered"] == 0
        # A deadline test: the check stalls for 30 s and the budget is 100 ms, so the bound below
        # has seconds of room on a loaded machine.
        jev.fail, jev.delay = False, 30.0
        monkeypatch.setattr(get_settings(), "memory_check_ms", 100)
        _, info = await memory.context_block(db, p, query="What do I teach?", query_sensitive=False)
        assert ids["teach"] in info["memory_ids"] and info["relevance"]["timed_out"] == 1
        assert info["relevance"]["ms"] < 5000
        # "off" turns the check off.
        monkeypatch.setattr(get_settings(), "memory_check_endpoint", "off")
        jev.asked.clear()
        _, info = await memory.context_block(db, p, query="What do I teach?", query_sensitive=False)
        assert jev.asked == [] and "checked" not in info["relevance"]


async def test_index_of_what_exists(client, user):
    from mailr.knowledge import memory

    await _notes(user.id)
    db, p = await _principal(user.id)
    async with db:
        e, _ = await memory.upsert_entity(db, p, {"kind": "person", "name": "Pat Secret"}, sensitive=True)
        await memory.remember(db, p, {"text": "Pat has an extension", "entity_id": e.id})
        await db.commit()
        text, info = await memory.context_block(db, p, query="What's the weather tomorrow?", index=True, query_sensitive=False)
        assert "exist about: you (" in text and "Sam Ortiz (person, 1)" in text and "call recall" in text
        assert "Pat Secret" not in text and not info["sensitive"]
        text, info = await memory.context_block(db, p, query="What's the weather tomorrow?", index=True,
                                                index_sensitive=True, query_sensitive=False)
        assert "Pat Secret (person, 1)" in text and info["sensitive"]
        # Drafting has no recall tool: no index.
        text, _ = await memory.context_block(db, p, query="What's the weather tomorrow?", query_sensitive=False)
        assert "exist about" not in text


async def test_recall_tool_gate_why_and_used_memories(client, user, fake):
    """The recall tool's notes say why they matched, and they join the turn's Used memories."""
    ids = await _notes(user.id)
    ep = await _endpoint(user.id, "local")
    try:
        fake.script = [_tool_turn("recall", {"about": "Sam"}), _text_turn("Sam is your accountant.")]
        evs = _all_events((await client.post("/api/v1/agent/chat", json={"message": "What's new?"})).text)
        sess = next(e for e in evs if e["type"] == "session")
        assert ids["sam"] not in sess["memory_ids"]  # not in the prompt
        done = evs[-1]
        assert done["type"] == "done" and ids["sam"] in done["memory_ids"]
        tool_msg = fake.requests[1]["body"]["messages"][-1]
        assert tool_msg["role"] == "tool" and '"why": "about Sam Ortiz' in tool_msg["content"]
        # The index in the prompt lists what exists.
        assert "exist about:" in fake.requests[0]["body"]["messages"][0]["content"]
        async with sessionmaker()() as db:
            row = (await db.execute(select(ChatMessage).where(ChatMessage.id == sess["user_message_id"]))).scalar_one()
        assert ids["sam"] in row.meta["memory_ids"]
        r = (await client.get(f"/api/v1/agent/sessions/{sess['session_id']}")).json()
        turn = next(t for t in r["turns"] if t.get("user_message_id") == sess["user_message_id"])
        assert ids["sam"] in turn["memory_ids"]
    finally:
        await _drop_endpoint(ep)


async def test_dropped_notes_are_not_used(client, user, fake, jev):
    """A note the check drops is neither in the prompt nor in Used memories."""
    ids = await _notes(user.id)
    jev.answers = {"You teach Statistics on Tuesdays.": 0.05}
    ep = await _endpoint(user.id, "local")
    try:
        fake.script = [_text_turn("Sunny.")]
        evs = _all_events((await client.post("/api/v1/agent/chat",
                                             json={"message": "Is it sunny on Tuesdays where I teach?"})).text)
        sess = next(e for e in evs if e["type"] == "session")
        assert ids["teach"] not in sess["memory_ids"] and ids["pin"] in sess["memory_ids"]
        assert f"[mem:{ids['teach']}]" not in fake.requests[0]["body"]["messages"][0]["content"]
        async with sessionmaker()() as db:
            row = (await db.execute(select(ChatMessage).where(ChatMessage.id == sess["user_message_id"]))).scalar_one()
        assert row.meta["memory_check"]["dropped"] == 1 and "answers" not in row.meta["memory_check"]
    finally:
        await _drop_endpoint(ep)


async def test_background_notes_need_a_link(client, user):
    """Triage, standing orders, reply suggestions: only notes about the people in the email, and
    pinned preferences."""
    from mailr.knowledge import memory

    ids = await _notes(user.id)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        _, got = await memory.background_block(db, u, emails=["stranger@example.org"], local=True)
        assert got == [ids["pin"]]
        _, got = await memory.background_block(db, u, emails=["sam@example.net"], local=True)
        assert set(got) == {ids["sam"], ids["pin"]}
