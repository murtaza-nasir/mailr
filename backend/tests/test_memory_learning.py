"""Learning that works and is visible: notes to self are passed over, a
failed model call keeps its mail for the next pass (at most three tries), every pass is recorded
for the Memory page, notes are written to the user and the ones about the user go to "About you",
chats propose memories, and an answer keeps the memories it used.

No real model is called: completions come from scripted fakes."""

import json
from datetime import UTC, datetime

from helpers.agent import _drop_endpoint, _endpoint, _ingest, _setup_identity
from helpers.assistant_knowledge import fake  # noqa: F401  (fixture)
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import AssistantProfile, ChatMessage, ChatSession, Entity, Memory


def _now_hdr() -> str:
    return datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")


async def _state(user_id):
    async with sessionmaker()() as db:
        return dict((await db.get(AssistantProfile, user_id)).state or {})


async def test_notes_to_self_are_passed_over_and_the_pass_is_recorded(client, user, raw, fake):
    from mailr.knowledge import tasks

    _, me = await _setup_identity(user)
    for i in range(3):
        await _ingest(user.id, raw(f"Screenshot {i}", frm=f"Me <{me}>", to=f"Me <{me}>", body="menu too long",
                                   date=_now_hdr()), source="send", roles=["sent"])
    ep = await _endpoint(user.id, "local")
    try:
        res = await tasks.extract_for_user(user.id, force=True)
        assert res == {"messages": 0, "suggested": 0, "skipped": 3}, res
        assert fake.posts == []  # nothing to ask a model about
        st = await _state(user.id)
        assert st["extract_since"] and st["learn_runs"][0]["skipped"] == 3 and st["learn_runs"][0]["error"] is None
        r = await client.get("/api/v1/assistant/memories/learning")
        assert r.status_code == 200 and r.json()["last"]["messages"] == 0 and r.json()["enabled"] is True
    finally:
        await _drop_endpoint(ep)


async def test_a_failed_model_call_keeps_the_mail_for_the_next_pass(client, user, raw, monkeypatch):
    from mailr.intel import llm
    from mailr.knowledge import tasks

    _, me = await _setup_identity(user)
    await _ingest(user.id, raw("Quote", frm=f"Me <{me}>", to="Sam <sam@acme.example>", body="I will send it Friday.",
                               date=_now_hdr()), source="send", roles=["sent"])
    calls = {"n": 0}

    async def broken(*a, **k):
        calls["n"] += 1
        raise RuntimeError("model endpoint returned 502")

    monkeypatch.setattr(llm, "chat", broken)
    ep = await _endpoint(user.id, "local")
    try:
        for attempt in (1, 2):
            res = await tasks.extract_for_user(user.id, force=True)
            assert res["error"] == "model_failed" and res["messages"] == 1, res
            st = await _state(user.id)
            assert "extract_since" not in st and st["learn_tries"] == attempt
            # A failed pass may run again in an hour, not the next day.
            assert datetime.fromisoformat(st["extract_at"]) < datetime.now(UTC) - tasks.MIN_INTERVAL + tasks.RETRY_AFTER
        # The third failure passes the batch, so one bad message cannot stop learning for good.
        res = await tasks.extract_for_user(user.id, force=True)
        st = await _state(user.id)
        assert res["error"] == "gave_up" and st["extract_since"] and "learn_tries" not in st
        assert st["learn_runs"][0]["cause"] == "model_failed" and calls["n"] == 3
        # After it was passed over, the next scheduled pass waits the usual day.
        assert await tasks.extract_for_user(user.id) == {"skipped": "ran recently"}
    finally:
        await _drop_endpoint(ep)


async def test_an_unreadable_answer_is_a_failure_not_an_empty_pass(client, user, raw, fake):
    from mailr.knowledge import tasks

    _, me = await _setup_identity(user)
    await _ingest(user.id, raw("Hi", frm=f"Me <{me}>", to="sam@acme.example", body="See you Tuesday.",
                               date=_now_hdr()), source="send", roles=["sent"])
    ep = await _endpoint(user.id, "local")
    try:
        fake.replies = ["Sure! Here are some memories: Sam"]
        res = await tasks.extract_for_user(user.id, force=True)
        assert res["error"] == "unreadable" and "extract_since" not in await _state(user.id)
        fake.replies = [json.dumps({"memories": []})]
        res = await tasks.extract_for_user(user.id, force=True)
        assert "error" not in res and (await _state(user.id))["extract_since"]
    finally:
        await _drop_endpoint(ep)


async def test_notes_are_written_to_you_and_about_you_goes_to_about_you(client, user, raw, fake):
    from mailr.knowledge import tasks

    _, me = await _setup_identity(user)
    mid, _ = await _ingest(user.id, raw("Class", frm=f"Me <{me}>", to="Dana <dana@acme.example>",
                                        body="I teach on Tuesday evenings, so Wednesday works.", date=_now_hdr()),
                           source="send", roles=["sent"])
    ep = await _endpoint(user.id, "local")
    try:
        fake.replies = [json.dumps({"memories": [
            {"text": "The user teaches on Tuesday evenings", "kind": "routine", "about_name": "", "about_email": "",
             "about_kind": "self", "message_id": mid, "confidence": 0.9},
            {"text": "Dana is the user's accountant", "kind": "fact", "about_name": "Dana",
             "about_email": "dana@acme.example", "about_kind": "person", "message_id": mid, "confidence": 0.8}]})]
        res = await tasks.extract_for_user(user.id, force=True)
        assert res["suggested"] == 2, res
        prompt = json.dumps(fake.posts[-1]["body"])
        assert "second person" in prompt and '"routine"' in prompt and "habit" not in prompt
        r = await client.get("/api/v1/assistant/memories", params={"status": "suggested"})
        items = {m["text"]: m for m in r.json()["items"]}
        assert set(items) == {"You teach on Tuesday evenings", "Dana is your accountant"}
        for m in items.values():
            assert (await client.post(f"/api/v1/assistant/memories/{m['id']}/accept")).status_code == 200
        async with sessionmaker()() as db:
            me_ent = (await db.execute(select(Entity).where(Entity.user_id == user.id, Entity.is_self))).scalar_one()
            dana = (await db.execute(select(Entity).where(Entity.user_id == user.id, Entity.name == "Dana"))).scalar_one()
            mems = {m.text: m for m in (await db.execute(select(Memory).where(Memory.user_id == user.id))).scalars()}
            assert mems["You teach on Tuesday evenings"].entity_id == me_ent.id
            assert mems["Dana is your accountant"].entity_id == dana.id and dana.person_id is not None
        # "About you" cannot be deleted as a whole.
        r = await client.delete(f"/api/v1/assistant/entities/{me_ent.id}")
        assert r.status_code == 400
    finally:
        await _drop_endpoint(ep)


def test_what_looks_like_a_statement():
    from mailr.knowledge.tasks import looks_like_statement

    for t in ("I'm vegetarian", "Dana is my accountant", "I teach on Tuesday evenings", "Remember that I fly on Fridays",
              "My partner is Sam"):
        assert looks_like_statement(t), t
    for t in ("What did Dana send me?", "Find the invoice", "Summarize this thread", ""):
        assert not looks_like_statement(t), t


async def test_a_chat_statement_becomes_a_suggestion(client, user, fake):
    from mailr.knowledge import tasks

    ep = await _endpoint(user.id, "local")
    try:
        async with sessionmaker()() as db:
            s = ChatSession(id="s-learn-1", user_id=user.id, title="", context={}, sensitive=False)
            db.add(s)
            await db.flush()
            row = ChatMessage(session_id=s.id, role="user", content="I'm vegetarian, and Dana is my accountant.")
            db.add(row)
            await db.commit()
            rid = row.id
        fake.replies = [json.dumps({"memories": [
            {"text": "You are vegetarian", "kind": "preference", "about_name": "", "about_email": "",
             "about_kind": "self", "message_id": 0, "confidence": 0.9}]})]
        assert (await tasks.suggest_from_chat(user.id, "s-learn-1", rid)) == {"suggested": 1}
        r = await client.get("/api/v1/assistant/memories", params={"status": "suggested"})
        m = r.json()["items"][0]
        assert m["text"] == "You are vegetarian" and m["source"] == "chat"
        assert m["refs"][0]["chat_session_id"] == "s-learn-1" and m["refs"][0]["chat_message_id"] == rid
        assert m["chat"] == {"session_id": "s-learn-1", "chat_message_id": rid}
        # Off in Settings: nothing is asked.
        await client.put("/api/v1/assistant/profile", json={"options": {"learn_from_chats": False}})
        n = len(fake.posts)
        assert (await tasks.suggest_from_chat(user.id, "s-learn-1", rid)) == {"skipped": "off"}
        assert len(fake.posts) == n
    finally:
        await _drop_endpoint(ep)


async def test_a_kept_chat_suggestion_links_back_to_the_chat_turn(client, user, fake):
    """Keep on a suggestion from a chat: the memory stays "From a chat" and keeps the turn, so the
    Memory page links to that chat. A deleted chat leaves the source without a link."""
    from mailr.knowledge import tasks

    ep = await _endpoint(user.id, "local")
    try:
        async with sessionmaker()() as db:
            db.add(ChatSession(id="s-keep-1", user_id=user.id, title="Lunch", context={}, sensitive=False))
            await db.flush()
            row = ChatMessage(session_id="s-keep-1", role="user", content="I'm allergic to peanuts.")
            db.add(row)
            await db.commit()
            rid = row.id
        fake.replies = [json.dumps({"memories": [
            {"text": "You are allergic to peanuts", "kind": "fact", "about_name": "", "about_email": "",
             "about_kind": "self", "message_id": 0, "confidence": 0.9}]})]
        assert (await tasks.suggest_from_chat(user.id, "s-keep-1", rid)) == {"suggested": 1}
        sid = (await client.get("/api/v1/assistant/memories", params={"status": "suggested"})).json()["items"][0]["id"]
        r = await client.post(f"/api/v1/assistant/memories/{sid}/accept")
        assert r.status_code == 200, r.text
        kept = r.json()
        assert kept["status"] == "active" and kept["source"] == "chat"
        assert kept["chat"] == {"session_id": "s-keep-1", "chat_message_id": rid}
        listed = {m["id"]: m for m in (await client.get("/api/v1/assistant/memories")).json()["items"]}
        assert listed[sid]["chat"] == {"session_id": "s-keep-1", "chat_message_id": rid}
        # An edit keeps the source.
        r = await client.patch(f"/api/v1/assistant/memories/{sid}", json={"text": "You are allergic to peanuts and cashews"})
        assert r.status_code == 200, r.text
        listed = {m["id"]: m for m in (await client.get("/api/v1/assistant/memories")).json()["items"]}
        assert listed[sid]["chat"] == {"session_id": "s-keep-1", "chat_message_id": rid}
        # The chat is deleted: the memory is still from a chat, without a link.
        assert (await client.delete("/api/v1/agent/sessions/s-keep-1")).status_code == 200
        listed = {m["id"]: m for m in (await client.get("/api/v1/assistant/memories")).json()["items"]}
        assert listed[sid]["source"] == "chat" and listed[sid]["chat"] is None
    finally:
        await _drop_endpoint(ep)


async def test_used_memories_are_listed_by_id(client, user):
    a = (await client.post("/api/v1/assistant/memories", json={"text": "You prefer short replies", "kind": "preference",
                                                                "about": "me"})).json()["memory"]
    b = (await client.post("/api/v1/assistant/memories", json={"text": "Sam is your accountant", "about": "Sam"}
                           )).json()["memory"]
    assert a["entity"]["is_self"] is True and b["entity"]["is_self"] is False
    r = await client.get("/api/v1/assistant/memories", params={"ids": f"{b['id']},{a['id']},999999"})
    assert [m["id"] for m in r.json()["items"]] == [b["id"], a["id"]] and r.json()["missing"] == 1


async def test_background_notes_keep_local_only_notes_off_hosted_models(client, user):
    """Triage, standing orders and reply suggestions get the notes about the people in an email;
    a note learned from local-only mail goes only to a local model."""
    from mailr.knowledge import memory

    await client.post("/api/v1/assistant/memories", json={"text": "Dana is your accountant",
                                                          "about": {"name": "Dana", "emails": ["dana@acme.example"]}})
    async with sessionmaker()() as db:
        dana = (await db.execute(select(Entity).where(Entity.user_id == user.id, Entity.name == "Dana"))).scalar_one()
        db.add(Memory(user_id=user.id, entity_id=dana.id, text="Dana's audit is under review", kind="fact",
                      source="extracted", status="active", refs=[], sensitive=True, pinned=False, use_count=0))
        await db.commit()
        hosted, ids = await memory.background_block(db, user, emails=["Dana@acme.example"], local=False)
        assert "Dana is your accountant" in hosted and "audit" not in hosted and len(ids) == 1
        local, ids = await memory.background_block(db, user, emails=["dana@acme.example"], local=True)
        assert "audit" in local and len(ids) == 2
        assert memory.background_notes(local)[0].startswith("(Dana) ")
        assert await memory.background_block(db, user, emails=["nobody@else.example"], local=True) == ("", [])


async def test_triage_asks_the_decision_model_with_the_senders_notes(client, user, raw, monkeypatch):
    from mailr.intel import classify, jev, llm

    await client.post("/api/v1/assistant/memories", json={"text": "Dana is your accountant",
                                                          "about": {"name": "Dana", "emails": ["dana@acme.example"]}})
    seen = {}

    class R:
        local = True
        model = "decider"
        endpoint = type("E", (), {"adapter": "jev", "name": "decider-local", "locality": "local"})()

    async def resolve(*a, **k):
        return R()

    async def smart(r, m, context="", notes=None):
        seen["notes"] = notes
        return {"needs_reply": 0.9, "asks_question": 0.9}

    monkeypatch.setattr(llm, "resolve", resolve)
    monkeypatch.setattr(jev, "smart", smart)
    mid, _ = await _ingest(user.id, raw("Taxes", frm="Dana <dana@acme.example>", body="Can you send the receipts?"),
                           roles=["inbox"])
    async with sessionmaker()() as db:
        from mailr.models import Message, User

        m, u = await db.get(Message, mid), await db.get(User, user.id)
        await classify.llm_smart(db, u, m, sensitive=False)
    assert seen["notes"] == ["(Dana) Dana is your accountant"]
