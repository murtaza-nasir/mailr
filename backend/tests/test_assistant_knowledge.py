"""Skills, profile, memory and entities, draft awareness, the tool budget, the chat timeline,
re-runs and interrupted turns, and the configuration tools.

No real model is called: chat streams and completions come from scripted fakes."""

import json
from datetime import UTC, datetime

import pytest
from conftest import mail_date
from helpers.agent import (
    _drop_endpoint,
    _endpoint,
    _ingest,
    _setup_identity,
    _text_turn,
    _token_client,
    _tool_turn,
)
from helpers.assistant_knowledge import FakeLLM, _all_events, _Handle, fake  # noqa: F401
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import ChatMessage, ChatSession, Entity, Label, Memory, Rule, Skill, User


def _text_then_tool(text, name, args, call_id="c1"):
    return [{"choices": [{"delta": {"content": text}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": call_id, "function": {
                "name": name, "arguments": json.dumps(args)}}]}, "finish_reason": "tool_calls"}]}]


async def _principal(user_id):
    from mailr.core.auth import ALL_SCOPES, Principal

    db = sessionmaker()()
    u = await db.get(User, user_id)
    return db, Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"})


async def _decide(user_id, pid):
    from mailr.core import permissions

    async with sessionmaker()() as db:
        u = await db.get(User, user_id)
        await permissions.decide(db, u, pid, "approve", "once")
        await db.commit()
    await permissions.execute_job({"pending_id": pid}, None)


# ---------------------------------------------------------------- skills


async def test_skills_seed_crud_and_resolve(client, user):
    r = await client.get("/api/v1/assistant/skills")
    assert r.status_code == 200, r.text
    names = {s["name"] for s in r.json()["items"]}
    assert {"formal", "friendly", "concise", "polite-decline", "follow-up", "create-skill", "email-basics",
            "configure-mailr"} <= names
    basics = next(s for s in r.json()["items"] if s["name"] == "email-basics")
    assert basics["auto"] and basics["builtin"] and "[Your name]" in basics["instructions"]

    r = await client.post("/api/v1/assistant/skills", json={"name": "pirate", "description": "Talk like a pirate",
                                                             "instructions": "- Say arr.", "applies_to": ["chat"]})
    assert r.status_code == 201, r.text
    assert (await client.post("/api/v1/assistant/skills", json={"name": "Bad Name!", "instructions": "x"})).status_code == 400
    assert (await client.post("/api/v1/assistant/skills", json={"name": "pirate", "instructions": "x"})).status_code == 400

    r = (await client.get("/api/v1/assistant/skills/resolve",
                          params={"text": "//formal and //concise please, see https://x.org//y"})).json()
    assert [s["name"] for s in r["skills"]] == ["formal", "concise"] and not r["problems"]
    assert r["text"] == "and please, see https://x.org//y"
    r = (await client.get("/api/v1/assistant/skills/resolve", params={"text": "//formall hi"})).json()
    assert r["problems"] and "Did you mean /formal?" in r["problems"][0]

    items = {s["name"]: s for s in (await client.get("/api/v1/assistant/skills")).json()["items"]}
    formal = items["formal"]
    r = await client.patch(f"/api/v1/assistant/skills/{formal['id']}", json={"instructions": "- Be stiff."})
    assert r.status_code == 200 and r.json()["skill"]["modified"] is True
    r = await client.post(f"/api/v1/assistant/skills/{formal['id']}/reset")
    assert r.json()["modified"] is False and "Kind regards" in r.json()["instructions"]
    dup = (await client.post(f"/api/v1/assistant/skills/{formal['id']}/duplicate")).json()
    assert dup["name"] == "formal-copy" and not dup["builtin"]
    # A deleted built-in is not seeded again, until restored.
    assert (await client.delete(f"/api/v1/assistant/skills/{items['friendly']['id']}")).status_code == 200
    assert "friendly" not in {s["name"] for s in (await client.get("/api/v1/assistant/skills")).json()["items"]}
    assert (await client.post("/api/v1/assistant/skills/restore-builtins")).json()["added"] == ["friendly"]


# ---------------------------------------------------------------- profile and prompt assembly


async def test_profile_and_prompt_assembly(client, user):
    from mailr.agent import loop

    r = await client.put("/api/v1/assistant/profile", json={"preferred_name": "Rowan", "signoff_name": "Rowan",
                                                             "timezone": "America/Chicago", "role": "Professor",
                                                             "options": {"learn_from_mail": False}})
    assert r.status_code == 200, r.text
    assert r.json()["signoff_name"] == "Rowan" and r.json()["options"]["learn_from_mail"] is False
    assert (await client.put("/api/v1/assistant/profile", json={"timezone": "Mars/Base"})).status_code == 400

    db, p = await _principal(user.id)
    async with db:
        from mailr.knowledge import skills

        s = ChatSession(id=f"t-{user.id}", user_id=user.id, title="t", sensitive=False, context={})
        db.add(s)
        await db.flush()
        inv = await skills.resolve(db, user.id, "/concise say hi")
        ui = {"compose": [{"id": "c1", "focused": True, "body": "Hi Bob,\n\nBest,\n[Your name]", "subject": "Hi",
                           "to": ["bob@example.org"]}]}
        from mailr.agent import screen

        system, info = await loop.build_system(db, p, s, inv.text, screen.sanitize(ui), inv.skills)
    assert "signs emails as: Rowan" in system and "America/Chicago" in system and "Role: Professor" in system
    assert "- /formal:" in system  # the catalogue the model chooses from
    assert "### /concise" in system and info["skills"][0] == "concise"
    assert "window c1 [focused]" in system and "[Your name]" in system
    assert len(system) < 12_000  # compact enough for a mid-size local model


# ---------------------------------------------------------------- memory and entities


async def test_memory_entities_recall_and_people(client, user, raw):
    from mailr.knowledge import memory

    _, me = await _setup_identity(user)
    await _ingest(user.id, raw("Dinner?", frm="Cleo Adams <cleo@family.example>", to=me,
                               body="Shall we try the new Thai place on Friday?"))
    db, p = await _principal(user.id)
    async with db:
        text, info = await memory.context_block(db, p, query="where did Cleo want to go for dinner", query_sensitive=False)
    # No entity yet: the correspondent is found by name, so the model can search from:her address.
    assert "Cleo: Cleo Adams <cleo@family.example>" in text

    e = (await client.post("/api/v1/assistant/entities", json={
        "kind": "person", "name": "Cleo Adams", "aliases": ["Cleo"], "emails": ["cleo@family.example"],
        "summary": "the user's partner"})).json()["entity"]
    m = (await client.post("/api/v1/assistant/memories", json={"text": "Cleo loves Thai food", "kind": "preference",
                                                                "entity_id": e["id"]})).json()["memory"]
    assert m["entity"]["name"] == "Cleo Adams" and m["source"] == "user"
    # The same text again refreshes the note instead of duplicating it.
    again = (await client.post("/api/v1/assistant/memories", json={"text": "Cleo loves Thai food.",
                                                                    "entity_id": e["id"]})).json()
    assert again["memory"]["id"] == m["id"] and again["created"] is False

    r = (await client.get("/api/v1/assistant/recall", params={"q": "where did Cleo want to go for dinner"})).json()
    assert f"[e:{e['id']}] Cleo Adams (person)" in r["block"] and f"[mem:{m['id']}]" in r["block"]
    assert r["entity_ids"] == [e["id"]] and m["id"] in r["memory_ids"]
    # Also found through the addresses of an open conversation's people.
    r = (await client.get("/api/v1/assistant/recall", params={"emails": "cleo@family.example"})).json()
    assert m["id"] in r["memory_ids"]

    hits = (await client.get("/api/v1/assistant/memories", params={"q": "thai"})).json()["items"]
    assert [h["id"] for h in hits] == [m["id"]]
    detail = (await client.get(f"/api/v1/assistant/entities/{e['id']}")).json()
    assert detail["memories"][0]["id"] == m["id"] and detail["contacts"][0]["email"] == "cleo@family.example"

    # Deleting an entity deletes its notes.
    assert (await client.delete(f"/api/v1/assistant/entities/{e['id']}")).status_code == 200
    assert not (await client.get("/api/v1/assistant/memories")).json()["items"]


async def test_memory_embeddings(client, user, monkeypatch):
    from embedfake import embedding_endpoint

    from mailr.knowledge import memory

    async with embedding_endpoint(monkeypatch):
        await client.post("/api/v1/assistant/memories", json={"text": "The quarterly revenue forecast is due in May"})
        await client.post("/api/v1/assistant/memories", json={"text": "Cleo prefers window seats"})
        async with sessionmaker()() as db:
            assert await memory.embed_pending(db, user.id) == 2
            await db.commit()
        db, p = await _principal(user.id)
        async with db:
            got = await memory.recall(db, p, "profit projections", k=3, query_sensitive=False)
        # "profit projections" shares no word with the note; the vectors find it.
        assert got and "revenue forecast" in got[0].text


async def test_sensitive_memory_hidden_from_hosted_tokens(client, user):
    await client.post("/api/v1/assistant/memories", json={"text": "Student X has an extension", "sensitive": True})
    await client.post("/api/v1/assistant/memories", json={"text": "Ordinary note"})
    hosted = (await client.post("/api/v1/tokens", json={"name": "h", "scopes": ["read"]})).json()["token"]
    local = (await client.post("/api/v1/tokens", json={"name": "l", "scopes": ["read"], "local_only": True})).json()["token"]
    async with _token_client(client, hosted) as a:
        texts = [m["text"] for m in (await a.get("/api/v1/assistant/memories")).json()["items"]]
        assert texts == ["Ordinary note"]
    async with _token_client(client, local) as a:
        assert len((await a.get("/api/v1/assistant/memories")).json()["items"]) == 2


async def test_suggestions_accept_and_reject(client, user):
    async with sessionmaker()() as db:
        a = Memory(user_id=user.id, text="Acme Corp is the user's main client", kind="relationship",
                   source="extracted", status="suggested", confidence=0.8,
                   refs=[{"about": {"name": "Acme Corp", "kind": "organization", "emails": []}}])
        b = Memory(user_id=user.id, text="The user likes lunch", kind="habit", source="extracted", status="suggested")
        db.add_all([a, b])
        await db.commit()
    r = (await client.get("/api/v1/assistant/memories", params={"status": "suggested"})).json()
    assert r["counts"]["suggested"] == 2
    # Suggestions are not recalled until accepted.
    assert "Acme" not in (await client.get("/api/v1/assistant/recall", params={"q": "Acme Corp"})).json()["block"]
    acc = (await client.post(f"/api/v1/assistant/memories/{a.id}/accept")).json()
    assert acc["status"] == "active" and acc["entity"]["name"] == "Acme Corp" and acc["entity"]["kind"] == "organization"
    assert (await client.post(f"/api/v1/assistant/memories/{b.id}/reject")).status_code == 200
    async with sessionmaker()() as db:
        assert await db.get(Memory, b.id) is None
        from mailr.knowledge import profile

        assert "the user likes lunch" in (await profile.get(db, user.id)).state["rejected"]


async def test_learning_from_sent_mail(client, user, raw, fake):
    from mailr.knowledge import tasks

    conn, me = await _setup_identity(user)
    mid, _ = await _ingest(user.id, raw("Report", frm=f"Me <{me}>", to="Dana <dana@acme.example>",
                                        body="Hi Dana, I will send the Q3 report by Friday.",
                                        date=datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")),
                           source="send", roles=["sent"])
    ep = await _endpoint(user.id, "local")
    try:
        fake.replies = [json.dumps({"memories": [{
            "text": "The user promised Dana the Q3 report by Friday", "kind": "commitment", "about_name": "Dana",
            "about_email": "dana@acme.example", "about_kind": "person", "message_id": mid, "confidence": 0.9}]})]
        res = await tasks.extract_for_user(user.id, force=True)
        assert res == {"messages": 1, "suggested": 1}, res
        sent = json.dumps(fake.posts[-1]["body"])
        assert "Q3 report by Friday" in sent and "untrusted" in sent
        async with sessionmaker()() as db:
            m = (await db.execute(select(Memory).where(Memory.user_id == user.id))).scalar_one()
            assert m.status == "suggested" and m.source == "extracted" and m.refs[0]["message_id"] == mid
            assert m.refs[0]["about"]["emails"] == ["dana@acme.example"]
        # The cursor moved: nothing new to read.
        assert (await tasks.extract_for_user(user.id, force=True))["messages"] == 0
    finally:
        await _drop_endpoint(ep)


async def test_learning_skips_old_sent_mail(client, user, raw, fake):
    from datetime import timedelta

    from mailr.knowledge import tasks

    conn, me = await _setup_identity(user)
    old = (datetime.now(UTC) - timedelta(days=400)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    await _ingest(user.id, raw("Old", frm=f"Me <{me}>", to="x@acme.example", body="Imported years ago", date=old),
                  source="send", roles=["sent"])
    ep = await _endpoint(user.id, "local")
    try:
        assert (await tasks.extract_for_user(user.id, force=True))["messages"] == 0
        assert all("Imported years ago" not in json.dumps(p["body"]) for p in fake.posts)
    finally:
        await _drop_endpoint(ep)


async def test_learning_never_sends_local_only_mail_to_hosted(client, user, raw, fake):
    from mailr.knowledge import tasks

    conn, me = await _setup_identity(user, data_class="local_only")
    await _ingest(user.id, raw("Grades", frm=f"Me <{me}>", to="s@uni.example", body="SECRET-GRADE-B-PLUS",
                               date=datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")),
                  connector_id=conn, source="send", roles=["sent"])
    ep = await _endpoint(user.id, "hosted")
    try:
        res = await tasks.extract_for_user(user.id, force=True)
        assert res["suggested"] == 0
        assert all("SECRET-GRADE" not in json.dumps(p["body"]) for p in fake.posts)
    finally:
        await _drop_endpoint(ep)


async def test_learning_sends_local_only_mail_to_a_local_model_only(client, user, raw, fake):
    """The person chose a hosted chat model, and another enabled local endpoint has a chat default
    (the state a leftover test endpoint once made by accident). Local-only mail goes to the local
    endpoint, never to the hosted one; ordinary mail still goes to the hosted choice."""
    from mailr.knowledge import tasks

    conn, me = await _setup_identity(user, data_class="local_only")
    now = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")
    await _ingest(user.id, raw("Grades", frm=f"Me <{me}>", to="s@uni.example", body="SECRET-GRADE-A-MINUS", date=now),
                  connector_id=conn, source="send", roles=["sent"])
    await _ingest(user.id, raw("Lunch", frm=f"Me <{me}>", to="dana@acme.example", body="PLAIN-LUNCH-NOTE", date=now),
                  source="send", roles=["sent"])
    hosted = await _endpoint(user.id, "hosted")
    stray = await _endpoint(user.id, "local")
    async with sessionmaker()() as db:  # _endpoint also made it the user's choice: put the hosted one back
        u = await db.get(User, user.id)
        u.settings = {**u.settings, "models": {"chat": {"endpoint": hosted, "model": "m"}}}
        await db.commit()
    fake.replies = ['{"memories": []}', '{"memories": []}']
    try:
        await tasks.extract_for_user(user.id, force=True)
        to_hosted = [json.dumps(p["body"]) for p in fake.posts if f"//{hosted}." in p["url"]]
        to_local = [json.dumps(p["body"]) for p in fake.posts if f"//{stray}." in p["url"]]
        assert len(to_hosted) + len(to_local) == len(fake.posts)
        assert to_hosted and all("SECRET-GRADE" not in b for b in to_hosted)
        assert any("PLAIN-LUNCH-NOTE" in b for b in to_hosted)
        assert any("SECRET-GRADE" in b for b in to_local) and all("PLAIN-LUNCH-NOTE" not in b for b in to_local)
    finally:
        await _drop_endpoint(hosted)
        await _drop_endpoint(stray)


# ---------------------------------------------------------------- chat: skills, budget, timeline


async def test_chat_skill_invocation_and_unknown_skill(client, user, fake):
    ep = await _endpoint(user.id, "local")
    try:
        fake.script = [_text_turn("Dear Alice, …")]
        evs = _all_events((await client.post("/api/v1/agent/chat", json={"message": "/formal write to Alice"})).text)
        sess = next(e for e in evs if e["type"] == "session")
        assert "formal" in sess["skills"] and evs[-1]["type"] == "done"
        msgs = fake.requests[0]["body"]["messages"]
        assert "### /formal" in msgs[0]["content"] and msgs[-1] == {"role": "user", "content": "write to Alice"}
        # The skill sticks to the chat.
        fake.script = [_text_turn("Shorter.")]
        await client.post("/api/v1/agent/chat", json={"message": "shorter", "session_id": sess["session_id"]})
        assert "### /formal" in fake.requests[-1]["body"]["messages"][0]["content"]

        n = len(fake.requests)
        evs = _all_events((await client.post("/api/v1/agent/chat", json={"message": "//formall hi"})).text)
        assert evs[-1]["type"] == "error" and "Did you mean /formal?" in evs[-1]["message"]
        assert len(fake.requests) == n  # no model call
    finally:
        await _drop_endpoint(ep)


async def test_budget_ends_with_an_answer_not_an_error(client, user, fake, monkeypatch):
    from mailr.agent import loop

    monkeypatch.setattr(loop, "MAX_ROUNDS", 3)
    ep = await _endpoint(user.id, "local")
    try:
        fake.script = [_tool_turn("search_conversations", {"q": f"cleo {i}"}, f"c{i}") for i in range(3)] + \
                      [_text_turn("I could not find it; try her address.")]
        evs = _all_events((await client.post("/api/v1/agent/chat", json={"message": "where is cleo"})).text)
        kinds = [e["type"] for e in evs]
        assert "error" not in kinds, evs
        final = next(e for e in evs if e["type"] == "final")
        assert final["content"].startswith("I could not find it") and final["stop_reason"] == "rounds"
        assert evs[-1]["type"] == "done" and evs[-1]["stop_reason"] == "rounds"
        last = fake.requests[-1]["body"]
        assert "tools" not in last and "no more tool calls" in last["messages"][-1]["content"]
    finally:
        await _drop_endpoint(ep)


async def test_repeated_call_is_not_run_twice(client, user, fake):
    """A repeat is not run again: the model gets the earlier result back with a plain note, and the
    step points at the first one (clients fold the two into one line)."""
    ep = await _endpoint(user.id, "local")
    try:
        fake.script = [_tool_turn("list_labels", {}, "a"), _tool_turn("list_labels", {}, "b"), _text_turn("ok")]
        evs = _all_events((await client.post("/api/v1/agent/chat", json={"message": "labels?"})).text)
        tools = [e for e in evs if e["type"] == "tool"]
        assert tools[1]["summary"] == "same result as before"
        assert tools[1]["summary_msg"]["key"] == "approvals.result.same_again"
        assert tools[1]["repeat_of"] == tools[0]["id"] and "repeat_of" not in tools[0]
        msgs = [m for m in fake.requests[-1]["body"]["messages"] if m["role"] == "tool"]
        told = json.loads(msgs[-1]["content"])
        assert told["repeated_call"] is True and "same as before" in told["note"]
        assert "different tool" in told["note"]
        # The earlier result itself comes back, so the model need not look for it.
        assert told["same_result_as_before"] == json.loads(msgs[0]["content"])
    finally:
        await _drop_endpoint(ep)


async def test_repeats_are_capped(client, user, fake):
    """One search once ran 13 times, each "already done above", until the step
    limit. Now the second repeat ends tool use and the model answers from what it has."""
    ep = await _endpoint(user.id, "local")
    try:
        args = {"text": "deadline", "due_before": "2026-10-19"}
        fake.script = [_tool_turn("search_tasks", args, f"c{i}") for i in range(3)] + [_text_turn("No tasks matched.")]
        evs = _all_events((await client.post("/api/v1/agent/chat", json={"message": "deadlines next week?"})).text)
        tools = [e for e in evs if e["type"] == "tool"]
        assert len(tools) == 3  # the call, one repeat with a note, and the last repeat
        assert all(t["repeat_of"] == tools[0]["id"] for t in tools[1:])
        told = [json.loads(m["content"]) for m in fake.requests[-1]["body"]["messages"] if m["role"] == "tool"]
        assert "Do not call it again" in told[1]["note"] and "last repeat" in told[2]["note"]
        last = fake.requests[-1]["body"]
        assert "tools" not in last and "same thing again" in last["messages"][-1]["content"]
        done = evs[-1]
        assert done["type"] == "done" and done["stop_reason"] == "repeats"
        final = next(e for e in evs if e["type"] == "final")
        assert final["content"] == "No tasks matched." and final["stop_reason"] == "repeats"
    finally:
        await _drop_endpoint(ep)


async def test_timeline_segments_stream_and_replay(client, user, fake):
    from mailr.agent.routes import timeline

    ep = await _endpoint(user.id, "local")
    try:
        fake.script = [_text_then_tool("Let me check your labels.", "list_labels", {}), _text_turn("You have labels.")]
        evs = _all_events((await client.post("/api/v1/agent/chat", json={"message": "labels?"})).text)
        order = [(e["type"], e.get("segment")) for e in evs if e["type"] in ("delta", "segment", "tool_start",
                                                                              "tool", "final")]
        assert order == [("delta", 0), ("segment", 0), ("tool_start", 0), ("tool", 0), ("delta", 1), ("final", 1)]
        seg = next(e for e in evs if e["type"] == "segment")
        assert seg["content"] == "Let me check your labels."
        seqs = [e["seq"] for e in evs]
        assert seqs == sorted(seqs)
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        turns = (await client.get(f"/api/v1/agent/sessions/{sid}")).json()["turns"]
        a = turns[1]
        assert [t["type"] for t in a["timeline"]] == ["text", "steps", "text"]
        assert a["timeline"][0]["content"] == "Let me check your labels." and a["content"] == "You have labels."
        assert a["status"] == "done" and turns[0]["status"] == "done" and turns[0]["id"]
    finally:
        await _drop_endpoint(ep)

    # Older rows (every step on the final answer) still replay: steps, then the answer.
    old = [ChatMessage(id=1, role="user", content="q", created_at=datetime.now(UTC)),
           ChatMessage(id=2, role="assistant", content="A", steps=[{"id": "x", "name": "list_labels"}],
                       created_at=datetime.now(UTC))]
    assert [t["type"] for t in timeline(old)[1]["timeline"]] == ["steps", "text"]


# ---------------------------------------------------------------- drafts on screen


async def test_open_draft_edit_goes_to_the_window(client, user, fake):
    ep = await _endpoint(user.id, "local")
    ui = {"view": "inbox", "compose": [
        {"id": "c1", "draft_id": None, "focused": True, "mode": "new", "to": ["bob@example.org"], "subject": "Friday",
         "body": "Hi Bob,\n\nSee you Friday.\n\nBest,\n[Your name]"},
        {"id": "c2", "draft_id": None, "focused": False, "mode": "new", "to": [], "subject": "Other", "body": "x"}]}
    try:
        fake.script = [_tool_turn("update_draft", {"edits": [{"find": "[Your name]", "replace": "Rowan"}]}),
                       _tool_turn("update_draft", {"window": "c1", "edits": [{"find": "Tuesday", "replace": "x"}]}, "c2"),
                       _text_turn("Changed your name in the draft.")]
        evs = _all_events((await client.post("/api/v1/agent/chat", json={"message": "can you change my name to rowan.",
                                                                      "context": {"ui": ui}})).text)
        system = fake.requests[0]["body"]["messages"][0]["content"]
        assert "window c1 [focused]" in system and "window c2" in system and "[Your name]" in system
        edit = next(e for e in evs if e["type"] == "draft_edit")
        assert edit["window"] == "c1" and edit["changes"]["body_text"].endswith("Best,\nRowan")
        assert edit["changes"]["edits"] == [{"find": "[Your name]", "replace": "Rowan", "replace_html": "Rowan"}]
        assert edit["changes"]["body_html"].endswith("Best,<br>Rowan</div>")
        bad = [e for e in evs if e["type"] == "tool"][1]
        # The error goes in `error`; the step line (summary) stays empty.
        assert bad["ok"] is False and "not found in the draft" in bad.get("error", "")
        assert bad["summary"] == ""
        # The screen state is per turn: it is not stored on the session.
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        async with sessionmaker()() as db:
            assert "compose" not in json.dumps((await db.get(ChatSession, sid)).context)
    finally:
        await _drop_endpoint(ep)


async def test_saved_draft_edits_keep_quote_and_signature(client, user, raw):
    from mailr.agent.tools import ToolCtx, run_tool

    _, me = await _setup_identity(user)
    m1, _ = await _ingest(user.id, raw("Lunch", frm="Alice <alice@acme.example>", to=me, body="Lunch Tuesday?"))
    db, p = await _principal(user.id)
    async with db:
        ctx = ToolCtx(db=db, p=p, assistant=True)
        d = await run_tool(ctx, "create_draft", {"reply_to_message_id": m1, "body": "Sounds good.\n\nBest,\n[Your name]"})
        did = d.data["draft_id"]
        got = await run_tool(ctx, "get_draft", {"draft_id": did})
        assert got.data["open"] is False and got.data["body"].endswith("[Your name]") and got.data["has_quoted_history"]
        await run_tool(ctx, "update_draft", {"draft_id": did, "edits": [{"find": "[Your name]", "replace": "Sam"}]})
    d = (await client.get(f"/api/v1/drafts/{did}")).json()["payload"]["html"]
    assert "Sam" in d and "[Your name]" not in d and "mailr_quote" in d and d.index("Sam") < d.index("mailr_quote")


async def test_help_me_write_edits_the_current_draft(client, user, fake):
    await _setup_identity(user)
    await client.put("/api/v1/assistant/profile", json={"preferred_name": "Rowan"})
    ep = await _endpoint(user.id, "local")
    try:
        fake.replies = [json.dumps({"subject": "Friday", "body": "Hi Bob,\n\nSee you Friday.\n\nBest,\nRowan"})]
        r = await client.post("/api/v1/agent/draft", json={
            "mode": "new", "instructions": "can you change my name to rowan.",
            "current": {"subject": "Friday", "to": [{"email": "bob@example.org"}],
                        "html": "<p>Hi Bob,</p><p>See you Friday.</p><p>Best,<br>[Your name]</p>"}})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["edit"] is True and {"old": "[Your name]", "new": "Rowan"} in out["changes"]
        assert "email-basics" in out["skills"]
        body = fake.posts[-1]["body"]["messages"]
        assert "EDIT of" in body[0]["content"] and "Never write placeholders" in body[0]["content"]
        assert "Current draft:" in body[1]["content"] and "[Your name]" in body[1]["content"]
        # An empty draft is written from scratch; /skills apply; unknown skills are a 400.
        fake.replies = [json.dumps({"subject": "Hello", "body": "Dear Bob, …"})]
        r = await client.post("/api/v1/agent/draft", json={"mode": "new", "instructions": "/formal say hello",
                                                           "to": ["bob@example.org"], "current": {"html": ""}})
        assert r.json()["edit"] is False and "formal" in r.json()["skills"]
        assert "### /formal" in fake.posts[-1]["body"]["messages"][0]["content"]
        r = await client.post("/api/v1/agent/draft", json={"mode": "new", "instructions": "//nosuch hi"})
        assert r.status_code == 400 and "no skill /nosuch" in r.json()["detail"]
    finally:
        await _drop_endpoint(ep)


# ---------------------------------------------------------------- re-run, edit and interrupted turns


async def test_rerun_and_edit_truncate_history(client, user, fake):
    ep = await _endpoint(user.id, "local")
    try:
        fake.script = [_text_turn("A1")]
        evs = _all_events((await client.post("/api/v1/agent/chat", json={"message": "first"})).text)
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        first = next(e for e in evs if e["type"] == "session")["user_message_id"]
        fake.script = [_text_turn("B1")]
        await client.post("/api/v1/agent/chat", json={"message": "second", "session_id": sid})

        fake.script = [_text_turn("A2")]
        evs = _all_events((await client.post("/api/v1/agent/chat", json={"session_id": sid, "rerun_from": first})).text)
        assert next(e for e in evs if e["type"] == "final")["content"] == "A2"
        # The model saw no trace of the dropped turn.
        assert "second" not in json.dumps(fake.requests[-1]["body"]["messages"])
        turns = (await client.get(f"/api/v1/agent/sessions/{sid}")).json()["turns"]
        assert [(t["role"], t["content"]) for t in turns] == [("user", "first"), ("assistant", "A2")]

        fake.script = [_text_turn("C1")]
        await client.post("/api/v1/agent/chat", json={"session_id": sid, "edit_from": first, "message": "changed"})
        turns = (await client.get(f"/api/v1/agent/sessions/{sid}")).json()["turns"]
        assert [(t["role"], t["content"]) for t in turns] == [("user", "changed"), ("assistant", "C1")]
        bad = _all_events((await client.post("/api/v1/agent/chat", json={"session_id": sid, "rerun_from": 1})).text)
        assert bad[-1]["type"] == "error"
    finally:
        await _drop_endpoint(ep)


async def test_interrupted_turns_are_swept_and_shown(client, user):
    from mailr.agent.loop import sweep_interrupted

    async with sessionmaker()() as db:
        s = ChatSession(id=f"int-{user.id}", user_id=user.id, title="jokes", sensitive=False, context={})
        db.add(s)
        await db.flush()
        db.add(ChatMessage(session_id=s.id, role="user", content="can you find any emails that have jokes?",
                           status="running"))
        await db.commit()
        assert await sweep_interrupted(db) >= 1
        row = (await db.execute(select(ChatMessage).where(ChatMessage.session_id == s.id))).scalar_one()
        assert row.status == "interrupted"
    turns = (await client.get(f"/api/v1/agent/sessions/int-{user.id}")).json()["turns"]
    assert turns[-1]["role"] == "assistant" and turns[-1]["status"] == "interrupted" and not turns[-1]["content"]


async def test_model_failure_marks_the_turn(client, user, fake, monkeypatch):
    from mailr.intel import llm

    ep = await _endpoint(user.id, "local")

    async def boom(*a, **kw):
        raise RuntimeError("down")
        yield  # pragma: no cover

    monkeypatch.setattr(llm, "chat_stream", boom)
    try:
        evs = _all_events((await client.post("/api/v1/agent/chat", json={"message": "hi"})).text)
        assert evs[-1]["type"] == "error"
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        turns = (await client.get(f"/api/v1/agent/sessions/{sid}")).json()["turns"]
        assert turns[0]["status"] == "error"
    finally:
        await _drop_endpoint(ep)


# ---------------------------------------------------------------- permissions for knowledge writes


async def test_memory_and_skill_writes_by_agents(client, user):
    from mailr.agent.tools import ToolCtx, run_tool

    db, p = await _principal(user.id)
    async with db:
        ctx = ToolCtx(db=db, p=p, assistant=True, chat_session_id="s-mem")
        r = await run_tool(ctx, "remember", {"text": "Cleo is the user's partner", "kind": "relationship",
                                             "about": "Cleo"})
        assert r.pending is None and r.data["memory"]["source"] == "agent"
        # Notes are written to the user, and the old kinds map to the four new ones.
        assert r.data["memory"]["text"] == "Cleo is your partner" and r.data["memory"]["kind"] == "fact"
        ent = (await db.execute(select(Entity).where(Entity.user_id == user.id))).scalar_one()
        assert ent.name == "Cleo" and r.data["memory"]["entity_id"] == ent.id
        got = await run_tool(ctx, "recall", {"q": "who is Cleo"})
        assert got.data["memories"][0]["text"] == "Cleo is your partner"
        # A skill changes future prompts: the user approves it first.
        r = await run_tool(ctx, "create_skill", {"name": "haiku", "description": "Reply in haiku",
                                                 "instructions": "- Three lines, 5-7-5."})
        assert r.pending is not None and r.pending["action"] == "skills.write"
        assert (await db.execute(select(Skill).where(Skill.user_id == user.id, Skill.name == "haiku"))).first() is None
        pid = r.pending["id"]
    await _decide(user.id, pid)
    async with sessionmaker()() as db:
        sk = (await db.execute(select(Skill).where(Skill.user_id == user.id, Skill.name == "haiku"))).scalar_one()
        assert sk.source == "agent"

    reader = (await client.post("/api/v1/tokens", json={"name": "r", "scopes": ["read"]})).json()["token"]
    writer = (await client.post("/api/v1/tokens", json={"name": "w", "scopes": ["read", "memory"]})).json()["token"]
    async with _token_client(client, reader) as a:
        tools = {t["name"] for t in (await a.get("/api/v1/agent/tools")).json()["tools"]}
        assert "recall" in tools and "remember" not in tools
        assert (await a.post("/api/v1/assistant/memories", json={"text": "x"})).status_code == 403
    async with _token_client(client, writer) as a:
        r = await a.post("/api/v1/assistant/memories", json={"text": "From a token"})
        assert r.status_code == 202 and r.json()["approval"]["action"] == "memory.write"
        r = await a.post("/api/v1/agent/tools/remember", json={"text": "Also from a token"})
        assert r.status_code == 202
        assert (await a.put("/api/v1/assistant/profile", json={"role": "x"})).status_code == 403


# ---------------------------------------------------------------- configuration tools


async def test_configuration_tools(client, user):
    from mailr.agent.tools import ToolCtx, ToolError, run_tool

    await _setup_identity(user)
    db, p = await _principal(user.id)
    async with db:
        ctx = ToolCtx(db=db, p=p, assistant=True, chat_session_id="s-cfg")
        conf = await run_tool(ctx, "get_configuration", {})
        assert {"settings", "labels", "filters", "layouts", "identities", "skills"} <= set(conf.data)
        # Labels: the assistant may create and rename at once (with an undo); deleting is asked.
        r = await run_tool(ctx, "manage_label", {"action": "create", "label": "Acme", "parent": "Clients"})
        assert r.pending is None and r.events[0]["type"] == "config"
        lb = (await db.execute(select(Label).where(Label.user_id == user.id, Label.name == "Clients/Acme"))).scalar_one()
        assert r.events[0]["undo"] == {"method": "DELETE", "path": f"/labels/{lb.id}", "body": None}
        r = await run_tool(ctx, "manage_label", {"action": "delete", "label": "Clients/Acme"})
        assert r.pending is not None and r.pending["action"] == "labels.delete"
        # Settings: allow-listed keys only, and asked.
        with pytest.raises(ToolError):
            await run_tool(ctx, "update_settings", {"changes": {"sender_pictures": True}})
        with pytest.raises(ToolError):
            await run_tool(ctx, "update_settings", {"changes": {"density": "tiny"}})
        r = await run_tool(ctx, "update_settings", {"changes": {"density": "compact"}})
        assert r.pending is not None and r.pending["action"] == "prefs.write"
        settings_pid = r.pending["id"]
        # Filters: label names are resolved; the filter waits for approval.
        r = await run_tool(ctx, "manage_filter", {"action": "create", "query": "from:acme.example",
                                                  "add_labels": ["Clients/Acme"], "archive": True})
        assert r.pending is not None and r.pending["action"] == "rules.write"
        filter_pid = r.pending["id"]
    await _decide(user.id, settings_pid)
    await _decide(user.id, filter_pid)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        assert u.settings["density"] == "compact"
        rule = (await db.execute(select(Rule).where(Rule.user_id == user.id))).scalar_one()
        assert rule.query == "from:acme.example" and rule.actions["add_labels"] == [lb.id] and rule.source == "agent"
    me = (await client.get("/api/v1/me")).json()
    assert me["settings"]["density"] == "compact"

    tok = (await client.post("/api/v1/tokens", json={"name": "cfg", "scopes": ["read", "label"]})).json()["token"]
    async with _token_client(client, tok) as a:
        r = await a.post("/api/v1/agent/tools/manage_label", json={"action": "create", "label": "FromToken"})
        assert r.status_code == 202 and r.json()["approval"]["action"] == "labels.write"
        r = await a.post("/api/v1/agent/tools/update_settings", json={"changes": {"theme": "dark"}})
        assert r.status_code == 400  # needs the rules scope


async def test_stream_can_be_reattached_after_a_drop(client, user, fake):
    ep = await _endpoint(user.id, "local")
    try:
        fake.script = [_text_then_tool("Checking.", "list_labels", {}), _text_turn("Done.")]
        evs = _all_events((await client.post("/api/v1/agent/chat", json={"message": "labels?"})).text)
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        # A client that lost the stream after event 3 asks for the rest (the run is kept briefly).
        r = await client.get(f"/api/v1/agent/chat/{sid}/stream", params={"after": 3})
        rest = _all_events(r.text)
        assert rest and min(e["seq"] for e in rest) == 4 and rest[-1]["type"] == "done"
        assert (await client.get("/api/v1/agent/chat/nope/stream")).status_code == 404
    finally:
        await _drop_endpoint(ep)


async def test_summaries_know_the_reader(client, user, raw, fake):
    from mailr.intel import summarize

    _, me = await _setup_identity(user)
    _, conv = await _ingest(user.id, raw("Hello", frm="Ann <ann@example.org>", to=me, msgid="<ann1@x.org>",
                                         body="Can you review this?"))
    await _ingest(user.id, raw("Re: Hello", frm="Ann <ann@example.org>", to=me, irt="<ann1@x.org>",
                               date=mail_date(0, "11:00"), body="Also the appendix."))
    await client.put("/api/v1/assistant/profile", json={"preferred_name": "Sam", "role": "Editor"})
    await client.post("/api/v1/assistant/skills", json={"name": "bullets", "instructions": "- Use at most 3 bullets.",
                                                         "applies_to": ["summarize"], "auto": True})
    ep = await _endpoint(user.id, "local")
    try:
        fake.replies = [json.dumps({"summary": ["Ann asks for a review."], "action_items": [], "dates": []})]
        async with sessionmaker()() as db:
            u = await db.get(User, user.id)
            await summarize.summarize(db, u, conv, force=True)
        system = fake.posts[-1]["body"]["messages"][0]["content"]
        assert "goes by Sam" in system and "Role: Editor" in system and "Use at most 3 bullets" in system
    finally:
        await _drop_endpoint(ep)


def test_slash_skill_tokens_ignore_ordinary_text():
    from mailr.knowledge.skills import _tokens

    assert _tokens("/formal reply to Sam") == [("formal", True)]
    assert _tokens("draft this /formal please") == [("formal", False)]
    assert _tokens("//formal hi") == [("formal", True)]
    for text in ["and/or", "on 9/29", "see /etc/hosts", "https://x.example/a", "open /file.txt", "a (/b/c)"]:
        assert _tokens(text) == [], text
