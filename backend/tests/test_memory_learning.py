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


async def test_weak_and_past_proposals_are_left_out_and_a_due_date_ends_the_note(client, user, raw, fake):
    """docs/design/memory-quality.md step 1: the keep test and skip list are in the prompt with
    today's date, proposals under MIN_CONFIDENCE and commitments past their date are not offered,
    and a dated commitment expires DUE_GRACE after its date."""
    from datetime import timedelta

    from mailr.knowledge import tasks

    _, me = await _setup_identity(user)
    mid, _ = await _ingest(user.id, raw("Review", frm=f"Me <{me}>", to="Chair <chair@conf.example>",
                                        body="Happy to review one paper. I will send it by next Friday.",
                                        date=_now_hdr()), source="send", roles=["sent"])
    soon = (datetime.now(UTC) + timedelta(days=5)).date()
    past = (datetime.now(UTC) - timedelta(days=2)).date()
    ep = await _endpoint(user.id, "local")
    try:
        def item(text, kind="fact", conf=0.9, due=""):
            return {"text": text, "kind": kind, "about_name": "", "about_email": "", "about_kind": "self",
                    "message_id": mid, "confidence": conf, "due": due}
        fake.replies = [json.dumps({"memories": [
            item("You use a spreadsheet for reviews", conf=0.4),
            item("You promised the chair a slide by last week", kind="commitment", due=past.isoformat()),
            item("You will send the chair your review", kind="commitment", due=soon.isoformat())]})]
        res = await tasks.extract_for_user(user.id, force=True)
        assert res["suggested"] == 1, res
        system = fake.posts[-1]["body"]["messages"][0]["content"]
        assert "Today is" in system and "never a project" in system and "would change an answer" in system
        assert '"due"' in json.dumps(fake.posts[-1]["body"]["response_format"])
        assert ", sent " in fake.posts[-1]["body"]["messages"][1]["content"]
        async with sessionmaker()() as db:
            (m,) = (await db.execute(select(Memory).where(Memory.user_id == user.id))).scalars().all()
            assert m.text == "You will send the chair your review" and m.status == "suggested"
            assert {"due": soon.isoformat()}.items() <= m.refs[0].items()
            assert m.expires_at is not None and m.expires_at.date() >= soon + tasks.DUE_GRACE - timedelta(days=1)
    finally:
        await _drop_endpoint(ep)


async def test_old_suggestions_are_dropped_and_learning_waits_while_fifteen_wait(client, user, raw, fake):
    from datetime import timedelta

    from mailr.knowledge import tasks

    _, me = await _setup_identity(user)
    await _ingest(user.id, raw("Hi", frm=f"Me <{me}>", to="sam@acme.example", body="Thanks, Sam.",
                               date=_now_hdr()), source="send", roles=["sent"])
    now = datetime.now(UTC)

    def row(text, **kw):
        return Memory(user_id=user.id, kind="fact", text=text, source="extracted", status="suggested", refs=[],
                      sensitive=False, pinned=False, use_count=0, **kw)

    async with sessionmaker()() as db:
        db.add_all([row("You were unreviewed for a month", updated_at=now - timedelta(days=31)),
                    row("You had a date that passed", expires_at=now - timedelta(hours=1)),
                    Memory(user_id=user.id, kind="fact", text="You keep an old accepted note", source="extracted",
                           status="active", refs=[], sensitive=False, pinned=False, use_count=0,
                           updated_at=now - timedelta(days=90)),
                    *[row(f"You have waiting note {i}") for i in range(tasks.MAX_WAITING)]])
        await db.commit()
    ep = await _endpoint(user.id, "local")
    try:
        res = await tasks.extract_for_user(user.id, force=True)
        assert res == {"messages": 0, "suggested": 0, "full": True}, res
        assert fake.posts == []  # nothing is asked while the review queue is full
        st = await _state(user.id)
        assert "extract_since" not in st  # the mail is read once there is room
        assert st["learn_runs"][0]["full"] is True and st["learn_runs"][0]["expired"] == 2
        async with sessionmaker()() as db:
            texts = {m.text for m in (await db.execute(select(Memory).where(Memory.user_id == user.id))).scalars()}
        assert "You were unreviewed for a month" not in texts and "You had a date that passed" not in texts
        assert "You keep an old accepted note" in texts  # accepted notes are never dropped this way
        r = await client.get("/api/v1/assistant/memories/learning")
        assert r.json()["last"]["full"] is True
        # Reviewing one makes room: the next pass reads the mail, asking for at most the room left.
        async with sessionmaker()() as db:
            one = (await db.execute(select(Memory).where(Memory.user_id == user.id, Memory.status == "suggested")
                                    .limit(1))).scalar_one()
        assert (await client.post(f"/api/v1/assistant/memories/{one.id}/reject")).status_code == 200
        fake.replies = [json.dumps({"memories": []})]
        res = await tasks.extract_for_user(user.id, force=True)
        assert res["messages"] == 1 and "at most 1 short memories" in fake.posts[-1]["body"]["messages"][0]["content"]
    finally:
        await _drop_endpoint(ep)


def _row(user_id, text, *, status="suggested", entity_id=None, sensitive=False, refs=None):
    return Memory(user_id=user_id, kind="fact", text=text, source="extracted", status=status, refs=refs or [],
                  sensitive=sensitive, pinned=False, use_count=0, entity_id=entity_id)


async def test_the_model_sees_known_notes_and_updates_one_instead_of_adding_a_copy(client, user, raw, fake):
    """docs/design/memory-quality.md step 2 (A1): the waiting suggestions and the notes about the
    user are shown as <known> with their numbers, a sensitive note is not shown to a call that
    may reach a hosted model, and an update rewrites a suggestion in place."""
    from mailr.knowledge import tasks

    _, me = await _setup_identity(user)
    mid, _ = await _ingest(user.id, raw("Reviews", frm=f"Me <{me}>", to="Chair <chair@conf.example>",
                                        body="I can review a second paper, for the AI track this time.",
                                        date=_now_hdr()), source="send", roles=["sent"])
    async with sessionmaker()() as db:
        wait = _row(user.id, "You review papers for the Plains Conference MIS track",
                    refs=[{"about": {"name": "Plains Conference", "kind": "topic", "emails": []}}])
        secret = _row(user.id, "You see a counsellor on Mondays", sensitive=True)
        db.add_all([wait, secret])
        await db.commit()
        wait_id = wait.id
    ep = await _endpoint(user.id, "hosted")
    try:
        fake.replies = [json.dumps({"memories": [{
            "text": "You review papers for the Plains Conference MIS and AI tracks", "kind": "fact",
            "about_name": "Plains Conference", "about_email": "", "about_kind": "topic", "message_id": mid,
            "confidence": 0.9, "due": "", "action": "update", "update_id": wait_id}]})]
        res = await tasks.extract_for_user(user.id, force=True)
        assert res["suggested"] == 0 and res["updated"] == 1, res
        sent = fake.posts[-1]["body"]["messages"]
        assert "<known>" in sent[1]["content"] and "update_id" in sent[0]["content"]
        assert f"[note {wait_id}] waiting for review, about Plains Conference: You review papers" in sent[1]["content"]
        assert "counsellor" not in sent[1]["content"]  # sensitive: never to a hosted model
        async with sessionmaker()() as db:
            rows = (await db.execute(select(Memory).where(Memory.user_id == user.id,
                                                          Memory.status == "suggested"))).scalars().all()
        m = next(r for r in rows if r.id == wait_id)
        assert m.text.endswith("MIS and AI tracks") and len(rows) == 2
        assert any(r.get("message_id") == mid for r in m.refs) and any(r.get("about") for r in m.refs)
        assert (await _state(user.id))["learn_runs"][0]["updated"] == 1
    finally:
        await _drop_endpoint(ep)


async def test_an_edit_of_a_saved_note_waits_for_review_and_keeps_one_note(client, user, raw, fake):
    """An update of a saved note is a suggestion that names it (`replaces`); the list shows the old
    text, and keeping it changes the saved note instead of adding a second one."""
    from mailr.knowledge import memory, tasks

    _, me = await _setup_identity(user)
    mid, _ = await _ingest(user.id, raw("Survey", frm=f"Me <{me}>", to="Ana <ana@uni.example>",
                                        body="The survey study is now in data collection.",
                                        date=_now_hdr()), source="send", roles=["sent"])
    from mailr.core.auth import ALL_SCOPES, Principal
    from mailr.models import User

    async with sessionmaker()() as db:
        p = Principal(user=await db.get(User, user.id), scopes=set(ALL_SCOPES) - {"admin"})
        me_ent = await memory.self_entity(db, p, create=True)
        saved = _row(user.id, "You are designing the survey study", status="active", entity_id=me_ent.id)
        db.add(saved)
        await db.commit()
        saved_id = saved.id
    ep = await _endpoint(user.id, "local")
    try:
        def edit(text):
            return json.dumps({"memories": [{
                "text": text, "kind": "fact", "about_name": "", "about_email": "", "about_kind": "self",
                "message_id": mid, "confidence": 0.8, "due": "", "action": "update", "update_id": saved_id}]})
        fake.replies = [edit("You are designing the survey study")]  # the same: nothing to propose
        res = await tasks.extract_for_user(user.id, force=True)
        assert res["suggested"] == 0 and "updated" not in res, res
        assert f"[note {saved_id}] saved, about you: You are designing" in fake.posts[-1]["body"]["messages"][1]["content"]
        fake.replies = [edit("You are collecting data for the survey study")]
        async with sessionmaker()() as db:
            (await db.get(AssistantProfile, user.id)).state = {}
            await db.commit()
        res = await tasks.extract_for_user(user.id, force=True)
        assert res["suggested"] == 1, res
        r = await client.get("/api/v1/assistant/memories", params={"status": "suggested"})
        (s,) = r.json()["items"]
        assert s["replaces"] == {"id": saved_id, "text": "You are designing the survey study"}
        r = await client.post(f"/api/v1/assistant/memories/{s['id']}/accept")
        assert r.status_code == 200 and r.json()["id"] == saved_id
        assert r.json()["text"] == "You are collecting data for the survey study" and r.json()["status"] == "active"
        async with sessionmaker()() as db:
            rows = (await db.execute(select(Memory).where(Memory.user_id == user.id))).scalars().all()
        assert [m.id for m in rows] == [saved_id]
        assert any(x.get("message_id") == mid for x in rows[0].refs) and not any("replaces" in x for x in rows[0].refs)
    finally:
        await _drop_endpoint(ep)


async def test_a_new_proposal_covered_by_a_waiting_suggestion_is_folded_in(client, user, raw, fake, monkeypatch):
    """A2: a "new" proposal close in meaning to a waiting suggestion is merged into it when the
    local decision model says it is covered; a different fact nearby is kept as its own."""
    from embedfake import embedding_endpoint

    from mailr.intel import jev
    from mailr.knowledge import memory, relevance, tasks

    _, me = await _setup_identity(user)
    mid, _ = await _ingest(user.id, raw("Reviews", frm=f"Me <{me}>", to="Chair <chair@conf.example>",
                                        body="Yes, I will review for the analytics track.",
                                        date=_now_hdr()), source="send", roles=["sent"])
    asked: list[dict] = []

    async def decide(r, state, questions, timeout=10, **k):
        asked.append(state)
        same = "analytics track" in state["new"]["text"] and "analytics track" in state["saved"]["text"]
        return {"same": {"noul": 0.6 if same else 0.02}}

    async def endpoint(db, u):
        return llm_resolved

    from mailr.intel import llm
    from mailr.models import ModelEndpoint

    llm_resolved = llm.Resolved(ModelEndpoint(name="decider", base_url="http://decider.invalid/v1", adapter="jev",
                                              locality="local", models=["decider"]), "decider", "decider")
    monkeypatch.setattr(jev, "decide", decide)
    monkeypatch.setattr(relevance, "check_endpoint", endpoint)
    async with embedding_endpoint(monkeypatch):
        async with sessionmaker()() as db:
            wait = _row(user.id, "You review papers for the conference analytics track")
            db.add(wait)
            await db.commit()
            wait_id = wait.id
            await memory.embed_pending(db, user.id)
            await db.commit()
        ep = await _endpoint(user.id, "local")
        try:
            def new(text):
                return {"text": text, "kind": "fact", "about_name": "", "about_email": "", "about_kind": "self",
                        "message_id": mid, "confidence": 0.9, "due": "", "action": "new", "update_id": 0}
            fake.replies = [json.dumps({"memories": [
                new("You agreed to review papers for the conference analytics track"),
                new("You review papers for the conference marketing track")]})]
            res = await tasks.extract_for_user(user.id, force=True)
            assert res["suggested"] == 1 and res["updated"] == 1, res
            assert asked  # the decision model was asked
            async with sessionmaker()() as db:
                texts = {m.id: m.text for m in (await db.execute(select(Memory).where(
                    Memory.user_id == user.id))).scalars()}
            assert texts[wait_id] == "You agreed to review papers for the conference analytics track"
            assert "You review papers for the conference marketing track" in texts.values() and len(texts) == 2
        finally:
            await _drop_endpoint(ep)
