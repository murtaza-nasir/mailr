# ACT-SURFACES: assistant
"""Standing orders, routines and errands."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers.core_flow import _ingest
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import AssistantOrder, AssistantRun, Conversation, LabelDecision, Message


async def _label(client, name):
    r = await client.post("/api/v1/labels", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_message_order_acts_once_per_message(client, user, raw):
    from mailr.assistant import orders

    lid = await _label(client, "BILL DUE")
    r = await client.post("/api/v1/assistant/orders", json={
        "name": "Bills", "query": 'subject:"payment due"', "actions": {"add_labels": [lid], "star": True}})
    assert r.status_code == 200, r.text
    oid = r.json()["id"]
    mid, cid = await _ingest(user, raw("Your payment due on Friday", frm="Bank <alerts@bank.example>"))
    other, _ = await _ingest(user, raw("Lunch?", frm="Carol <carol@example.org>"))
    await orders.on_message({"message_id": mid}, None)
    await orders.on_message({"message_id": other}, None)
    await orders.on_message({"message_id": mid}, None)  # a retry of the same job
    async with sessionmaker()() as db:
        runs = (await db.execute(select(AssistantRun).where(AssistantRun.order_id == oid))).scalars().all()
        assert len(runs) == 1 and runs[0].status == "done" and runs[0].conversation_id == cid
        assert {a["action"] for a in runs[0].actions} == {"add_labels", "star"}
        conv = await db.get(Conversation, cid)
        assert conv.starred and lid in conv.label_ids
    act = (await client.get("/api/v1/assistant/runs", params={"order_id": oid})).json()["items"]
    assert act[0]["order_name"] == "Bills"


async def test_question_below_threshold_skips(client, user, raw, monkeypatch):
    from mailr.assistant import decide, orders

    async def fake(db, u, m, q):
        return decide.Answer(True, 0.55, "maybe", "llm") if "maybe" in m.subject else decide.Answer(True, 0.9, "yes", "llm")

    monkeypatch.setattr(decide, "decide", fake)
    oid = (await client.post("/api/v1/assistant/orders", json={
        "name": "Job alerts", "question": "Is this a job listing?", "actions": {"important": True}})).json()["id"]
    sure, _ = await _ingest(user, raw("Jobs for you", frm="Jobs <jobs@example.org>"))
    unsure, _ = await _ingest(user, raw("maybe jobs", frm="Jobs <jobs@example.org>"))
    await orders.on_message({"message_id": sure}, None)
    await orders.on_message({"message_id": unsure}, None)
    async with sessionmaker()() as db:
        runs = {r.message_id: r for r in (await db.execute(select(AssistantRun).where(AssistantRun.order_id == oid))).scalars()}
        assert runs[sure].status == "done" and runs[sure].condition["confidence"] == 0.9
        assert runs[unsure].status == "skipped" and runs[unsure].condition["threshold"] == 0.7


async def test_errand_delivers_a_brief_and_a_notification(client, user, raw, monkeypatch):
    from mailr.assistant import errands, notify, orders

    async def fake_chat(db, p, text, *, context=None, errand=None, **kw):
        assert "list the analyst jobs" in text and errand is not None
        yield {"type": "session", "session_id": "errand-session"}
        yield {"type": "final", "content": "Two analyst jobs match.\n\n- Northwind [1](mailr-cite:1)",
               "citations": [{"n": 1, "conversation_id": context["conversation_id"]}]}

    import mailr.agent.loop as loop

    monkeypatch.setattr(loop, "run_chat", fake_chat)
    sent = []

    async def fake_send(db, u, **kw):
        sent.append(kw)
        return "sent"

    monkeypatch.setattr(notify, "send", fake_send)
    oid = (await client.post("/api/v1/assistant/orders", json={
        "name": "Remote jobs", "query": "from:jobalert@example.org", "errand": "list the analyst jobs that allow remote work",
        "deliver": ["brief", "notify"]})).json()["id"]
    mid, cid = await _ingest(user, raw("19 New Jobs", frm="Alert <jobalert@example.org>"))
    await orders.on_message({"message_id": mid}, None)
    async with sessionmaker()() as db:
        run = (await db.execute(select(AssistantRun).where(AssistantRun.order_id == oid))).scalar_one()
        assert run.status == "queued"
        rid = run.id
    await errands.run_errand(rid)
    brief = (await client.get(f"/api/v1/conversations/{cid}/brief")).json()["brief"]
    assert brief["output"].startswith("Two analyst jobs") and brief["delivered"] == ["brief", "notify"]
    assert brief["session_id"] == "errand-session"
    assert sent and sent[0]["body"] == "Two analyst jobs match." and "Remote jobs" in sent[0]["title"]


async def test_routine_runs_once_per_slot(client, user, raw, monkeypatch):
    from mailr.assistant import orders

    queued = []

    async def fake_enqueue(db, kind, payload=None, **kw):
        queued.append((kind, payload))

    monkeypatch.setattr(orders, "enqueue", fake_enqueue)
    await _ingest(user, raw("Please review the draft", frm="Dana <dana@example.org>"))
    r = await client.post("/api/v1/assistant/orders", json={
        "name": "Morning briefing", "trigger": "schedule", "schedule": "0 7 * * *", "timezone": "America/Chicago",
        "query": "in:inbox", "errand": "Summarise what needs a reply today."})
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["deliver"] == ["digest"] and o["next_run_at"]
    async with sessionmaker()() as db:
        row = await db.get(AssistantOrder, o["id"])
        row.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
        await db.commit()
    before = datetime.now(UTC)
    assert await orders.due_routines() == 1
    assert await orders.due_routines() == 0  # the next slot is still ahead
    after = datetime.now(UTC)
    async with sessionmaker()() as db:
        row = await db.get(AssistantOrder, o["id"])
        # The next 07:00 in Chicago after the run, whatever the time of day the test runs at.
        assert row.next_run_at > after
        assert orders.next_slot("0 7 * * *", "America/Chicago", before) <= row.next_run_at \
            <= orders.next_slot("0 7 * * *", "America/Chicago", after)
        run = (await db.execute(select(AssistantRun).where(AssistantRun.order_id == o["id"]))).scalar_one()
        assert run.status == "queued" and run.condition["conversations"] >= 1
    assert queued and queued[0][0] == "assistant.errand" and queued[0][1]["conversation_ids"]


@pytest.mark.parametrize("body,err", [
    ({"trigger": "schedule", "schedule": "*/5 * * * *", "errand": "x"}, "every 30 minutes"),
    ({"trigger": "schedule", "schedule": "not cron", "errand": "x"}, "not a valid schedule"),
    ({"query": "from:x", "actions": {"forward_to": "a@b.c"}}, "unknown actions"),
    ({"actions": {"star": True}}, "needs a search, a question"),
    ({"query": "from:x"}, "needs actions or instructions"),
])
async def test_order_validation(client, user, body, err):
    r = await client.post("/api/v1/assistant/orders", json=body)
    assert r.status_code == 400 and err in r.text, r.text


async def test_assistant_created_orders_need_approval_and_errands_cannot(client, user):
    """order.create by the assistant waits for approval."""
    from mailr.agent.tools import ToolCtx, ToolError, run_tool
    from mailr.assistant.orders import principal_for
    from mailr.models import User

    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        ctx = ToolCtx(db=db, p=principal_for(u), assistant=True, chat_session_id="s1")
        res = await run_tool(ctx, "manage_order", {"action": "create", "name": "Bills", "question": "Is this a bill?",
                                                   "star": True})
        assert res.pending is not None
        assert (await db.execute(select(AssistantOrder).where(AssistantOrder.user_id == user.id))).first() is None
        ctx2 = ToolCtx(db=db, p=principal_for(u), assistant=True, chat_session_id="s2", chat_context={"errand": 1})
        with pytest.raises(ToolError):
            await run_tool(ctx2, "manage_order", {"action": "create", "question": "x?", "star": True})


async def test_signals_are_searchable(client, user, raw):
    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime

    # get_signals looks back 14 days: a recent date keeps this test from expiring with the calendar.
    recent = format_datetime(datetime.now(UTC) - timedelta(days=1))
    mid, cid = await _ingest(user, raw("Can you send the slides?", frm="Eve <eve@example.org>", date=recent))
    async with sessionmaker()() as db:
        db.add(LabelDecision(user_id=user.id, message_id=mid, kind="smart", target="needs_reply", value=True,
                             source="heuristic", confidence=0.8, reason="asks", status="applied"))
        db.add(LabelDecision(user_id=user.id, message_id=mid, kind="smart", target="deadline", value=True,
                             source="heuristic", source_ref="2026-10-03", confidence=0.8, reason="by Friday",
                             status="applied"))
        await db.commit()
    for q in ("is:needs_reply", "has:deadline"):
        items = (await client.get("/api/v1/conversations", params={"view": "all", "q": q})).json()["items"]
        assert cid in [c["id"] for c in items], q
    t = (await client.post("/api/v1/agent/tools/get_signals", json={"signal": "all"})).json()
    assert {i["signal"] for i in t["result"]["items"]} == {"needs_reply", "deadline"}
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        m.answered = True
        await db.commit()
    items = (await client.get("/api/v1/conversations", params={"view": "all", "q": "is:needs_reply"})).json()["items"]
    assert cid not in [c["id"] for c in items]


async def test_away_message_replies_once_per_sender_and_never_to_lists(client, user, raw, monkeypatch):
    from datetime import UTC, datetime

    from mailr.core import vacation
    from mailr.core.sendgate import SendGate

    sent = []

    async def fake_submit(self, payload, draft_id=None, send_at=None, *, system=None):
        sent.append((payload, system))

    monkeypatch.setattr(SendGate, "submit", fake_submit)
    r = await client.patch("/api/v1/me/settings", json={"vacation": {"enabled": True, "message": "Away until Monday."}})
    assert r.status_code == 200, r.text
    assert (await client.patch("/api/v1/me/settings", json={"vacation": {"enabled": True, "message": ""}})).status_code == 400
    now = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")
    # The away message answers only a proven sender: a trusted DMARC pass here.
    ok = "Authentication-Results: mx.example; spf=pass; dkim=pass header.d=example.org; dmarc=pass"
    a, _ = await _ingest(user, raw("Question", frm="Ann <ann@example.org>", date=now, extra=ok))
    b, _ = await _ingest(user, raw("Follow-up", frm="Ann <ann@example.org>", date=now, extra=ok))
    c, _ = await _ingest(user, raw("Deals", frm="No Reply <noreply@shop.example>", date=now))
    async with sessionmaker()() as db:
        from mailr.models import User as U

        u = await db.get(U, user.id)
        for mid in (a, b, c):
            m = await db.get(Message, mid)
            if vacation.eligible(m, bulk=False, auto_submitted=False):
                await vacation.vacation_reply({"message_id": mid}, None)
        assert vacation.active(u) is not None
    assert len(sent) == 1, sent  # once per sender; never to no-reply
    payload, system = sent[0]
    assert payload["to"][0]["email"] == "ann@example.org" and payload["text"] == "Away until Monday."
    assert system == {"auto_reply": True}


def test_auto_reply_header_is_server_only():
    from mailr.core.compose import build_message

    m = build_message({"to": [{"email": "a@b.c"}], "subject": "s", "text": "x", "_system": {"auto_reply": True}},
                      from_addr={"email": "me@example.org", "name": ""})
    assert m["Auto-Submitted"] == "auto-replied"
    m2 = build_message({"to": [{"email": "a@b.c"}], "subject": "s", "text": "x", "headers": {"Auto-Submitted": "no"}},
                       from_addr={"email": "me@example.org", "name": ""})
    assert m2["Auto-Submitted"] is None  # payload headers are limited to X-*


async def test_preview_tests_a_draft_on_recent_mail_and_changes_nothing(client, user, raw, monkeypatch):
    from mailr.assistant import decide

    async def fake(db, u, m, q):
        return decide.Answer("Data Analyst" in (m.subject or ""), 0.9, "listing", "llm")

    monkeypatch.setattr(decide, "decide", fake)
    _, hit = await _ingest(user, raw("Data Analyst (remote)", frm="Jobs <jobalert@example.org>"))
    _, miss = await _ingest(user, raw("Dean of Students", frm="Jobs <jobalert@example.org>"))
    await _ingest(user, raw("Lunch?", frm="Carol <carol@example.org>"))
    r = await client.post("/api/v1/assistant/orders/preview", json={
        "query": "from:jobalert@example.org", "question": "Is this a remote analyst job?"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["searched"] == 2 and out["matched"] == 1
    by = {i["conversation_id"]: i for i in out["items"]}
    assert by[hit]["matched"] and by[hit]["answer"] is True and not by[miss]["matched"]
    async with sessionmaker()() as db:
        runs = (await db.execute(select(AssistantRun).where(AssistantRun.user_id == user.id))).scalars().all()
        assert runs == [], [(r.order_id, r.trigger, r.status) for r in runs]
    assert (await client.post("/api/v1/assistant/orders/preview", json={})).status_code == 400


async def test_a_second_order_for_the_same_mail_is_refused(client, user):
    from mailr.agent.tools import ToolCtx, ToolError, run_tool
    from mailr.assistant.orders import principal_for
    from mailr.assistant.tools import _order_executor
    from mailr.models import User

    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        ctx = ToolCtx(db=db, p=principal_for(u), assistant=True, chat_session_id="s1")
        args = {"action": "create", "name": "Remote jobs", "query": "from:jobalert@example.org", "errand": "list them"}
        first = await run_tool(ctx, "manage_order", args)
        assert first.pending is not None
        with pytest.raises(ToolError, match="already waiting"):  # the user re-sent the request
            await run_tool(ctx, "manage_order", {**args, "name": "Remote jobs again"})
    r = await client.post("/api/v1/assistant/orders", json={"name": "Remote jobs", "query": "from:jobalert@example.org",
                                                            "errand": "list them"})
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        res = await _order_executor(db, u, {"op": "create", "order": {"trigger": "message",
                                                                        "query": "FROM:jobalert@example.org "}})
        assert "already covers" in res["error"]
        ctx = ToolCtx(db=db, p=principal_for(u), assistant=True, chat_session_id="s2")
        with pytest.raises(ToolError, match="already covers"):
            await run_tool(ctx, "manage_order", {"action": "create", "query": "from:jobalert@example.org", "star": True})


async def test_run_on_selected_conversations(client, user, raw):
    lid = await _label(client, "Jobs")
    oid = (await client.post("/api/v1/assistant/orders", json={
        "name": "Jobs", "query": "from:jobalert@example.org", "actions": {"add_labels": [lid]}})).json()["id"]
    _, a = await _ingest(user, raw("Job alert 1", frm="Jobs <jobalert@example.org>"))
    _, b = await _ingest(user, raw("Job alert 2", frm="Jobs <jobalert@example.org>"))
    _, c = await _ingest(user, raw("Lunch?", frm="Carol <carol@example.org>"))
    r = (await client.post(f"/api/v1/assistant/orders/{oid}/run", json={"conversation_ids": [a, b, c]})).json()
    assert (r["done"], r["no_match"]) == (2, 1), r
    again = (await client.post(f"/api/v1/assistant/orders/{oid}/run", json={"conversation_ids": [a]})).json()
    assert again["done"] == 1  # a manual run can be repeated
    async with sessionmaker()() as db:
        assert lid in (await db.get(Conversation, a)).label_ids and lid not in (await db.get(Conversation, c)).label_ids


async def test_chat_can_read_rewrite_and_restore_a_brief(client, user, raw):
    """brief.read, brief.rewrite, brief.restore through the assistant tools."""
    from mailr.agent.tools import ToolCtx, ToolError, run_tool
    from mailr.assistant.orders import principal_for
    from mailr.models import User

    oid = (await client.post("/api/v1/assistant/orders", json={
        "name": "Remote jobs", "query": "from:jobalert@example.org", "errand": "list them", "deliver": ["brief"]})).json()["id"]
    mid, cid = await _ingest(user, raw("19 New Jobs", frm="Alert <jobalert@example.org>"))
    async with sessionmaker()() as db:
        db.add(AssistantRun(user_id=user.id, order_id=oid, trigger="message", slot=f"m{mid}", message_id=mid,
                            conversation_id=cid, status="done", output="Two jobs match [1](mailr-cite:1).",
                            citations=[{"n": 1, "message_id": mid, "conversation_id": cid}], delivered=["brief"]))
        await db.commit()
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        ctx = ToolCtx(db=db, p=principal_for(u), assistant=True, chat_session_id="s1",
                      chat_context={"conversation_id": cid})
        got = await run_tool(ctx, "get_brief", {})
        # The model gets real markers for the brief's sources, never the display links.
        assert got.data["text"] == f"Two jobs match [[m:{mid}]]." and got.data["message_id"] == mid
        await run_tool(ctx, "update_brief", {"text": f'Well, two jobs fit the bill [[m:{mid} "19 New Jobs"]].'})
    brief = (await client.get(f"/api/v1/conversations/{cid}/brief")).json()["brief"]
    assert brief["output"] == "Well, two jobs fit the bill [1](mailr-cite:1)."
    assert brief["citations"][0]["message_id"] == mid
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        ctx = ToolCtx(db=db, p=principal_for(u), assistant=True, chat_session_id="s1")
        await run_tool(ctx, "update_brief", {"conversation_id": cid, "restore": True})
        with pytest.raises(ToolError, match="no brief"):
            await run_tool(ctx, "get_brief", {"conversation_id": (await _ingest(user, raw("x")))[1]})
    brief = (await client.get(f"/api/v1/conversations/{cid}/brief")).json()["brief"]
    assert brief["output"].startswith("Two jobs match")


async def test_brief_tools_default_to_the_email_on_screen(client, user, raw):
    from mailr.agent.tools import ToolCtx, run_tool
    from mailr.assistant.orders import principal_for
    from mailr.models import User

    mid, cid = await _ingest(user, raw("19 New Jobs", frm="Alert <jobalert@example.org>"))
    _, other = await _ingest(user, raw("Lunch?", frm="Carol <carol@example.org>"))
    async with sessionmaker()() as db:
        db.add(AssistantRun(user_id=user.id, order_id=None, trigger="message", slot=f"m{mid}", message_id=mid,
                            conversation_id=cid, status="done", output="Two jobs match.", delivered=["brief"]))
        await db.commit()
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        # The chat started on another email; the user has since opened the job alert.
        ctx = ToolCtx(db=db, p=principal_for(u), assistant=True, chat_session_id="s1",
                      ui={"conversation_id": cid}, chat_context={"conversation_id": other})
        assert (await run_tool(ctx, "get_brief", {})).data["text"] == "Two jobs match."


async def test_screen_context_lists_tabs_selection_and_rows_from_the_database(client, user, raw):
    from mailr.agent import screen
    from mailr.assistant.orders import principal_for
    from mailr.models import User

    _, a = await _ingest(user, raw("Budget review", frm="Dana <dana@example.org>"))
    _, b = await _ingest(user, raw("Lunch?", frm="Carol <carol@example.org>"))
    ui = screen.sanitize({"conversation_id": a, "open_conversation_ids": [b, 999999], "selected_conversation_ids": [a, b],
                          "list_conversation_ids": "nope", "selected_all_matching": True})
    assert "list_conversation_ids" not in ui and ui["open_conversation_ids"] == [b, 999999]
    async with sessionmaker()() as db:
        text = await screen.describe(db, principal_for(await db.get(User, user.id)), ui)
    # By id only: subjects and senders are read with read_screen (tests/test_screen_by_id.py).
    assert f"Other conversations open in tabs: 1 conversation, ids {b}" in text
    assert "Lunch?" not in text and "Carol" not in text
    assert "999999" not in text  # not the user's: dropped
    assert "every conversation matching the view" in text


async def test_choice_order_files_mail_by_the_picked_option(client, user, raw, monkeypatch):
    """A choice order: one question, several options; the model's pick runs that option's actions,
    'none' (or an unsure pick) runs nothing."""
    from mailr.assistant import decide, orders

    async def fake(db, u, m, q, options):
        assert q == "Which project is this about?" and [o["key"] for o in options] == ["grant-renewal", "chem-101"]
        if "renewal" in m.subject:
            return decide.ChoiceAnswer("grant-renewal", 0.92, "about the renewal", "jev")
        if "Module" in m.subject:
            return decide.ChoiceAnswer("chem-101", 0.55, "unsure", "jev")
        return decide.ChoiceAnswer(None, 0.9, "none of these", "jev")

    monkeypatch.setattr(decide, "decide_choice", fake)
    grant, course = await _label(client, "Grant renewal"), await _label(client, "CHEM 101")
    r = await client.post("/api/v1/assistant/orders", json={
        "name": "Projects", "question": "Which project is this about?", "actions": {"important": True},
        "choices": [{"name": "Grant renewal", "description": "the renewal application and budget", "actions": {"add_labels": [grant]}},
                    {"name": "CHEM 101", "description": "the course", "actions": {"add_labels": [course], "star": True}}]})
    assert r.status_code == 200, r.text
    o = r.json()
    assert [c["key"] for c in o["choices"]] == ["grant-renewal", "chem-101"] and o["choices"][0]["actions"] == {"add_labels": [grant]}
    a, ca = await _ingest(user, raw("Your renewal is with the panel", frm="Dean <dean@example.edu>"))
    b, cb = await _ingest(user, raw("Module 3 questions", frm="Student <s@example.edu>"))
    c, cc = await _ingest(user, raw("Lunch?", frm="Carol <carol@example.org>"))
    for mid in (a, b, c):
        await orders.on_message({"message_id": mid}, None)
    async with sessionmaker()() as db:
        runs = {x.message_id: x for x in (await db.execute(select(AssistantRun).where(AssistantRun.order_id == o["id"]))).scalars()}
        assert runs[a].status == "done" and runs[a].condition["choice"] == "grant-renewal"
        assert {x["action"] for x in runs[a].actions} == {"add_labels", "important"}  # the choice's and the order's
        conv = await db.get(Conversation, ca)
        assert grant in conv.label_ids and course not in conv.label_ids and conv.important
        assert runs[b].status == "skipped" and runs[b].condition["confidence"] == 0.55  # below the 0.7 threshold
        assert runs[c].status == "skipped" and runs[c].condition["choice"] is None
        assert course not in (await db.get(Conversation, cb)).label_ids


async def test_choice_order_jev_sends_the_options(user, raw, monkeypatch):
    from mailr.assistant import decide
    from mailr.intel import jev, llm

    sent = {}

    async def fake_resolve(db, u, task, *, sensitive, **_kw):
        from types import SimpleNamespace

        return SimpleNamespace(endpoint=SimpleNamespace(adapter="jev", name="jev"), model="jev-latest", api_key=None)

    async def fake_decide(r, state, questions, timeout=10):
        sent.update(questions)
        return {"q": {"choice": "chem-101", "probabilities": {"grant-renewal": 0.1, "chem-101": 0.85, "none": 0.05}}}

    monkeypatch.setattr(llm, "resolve", fake_resolve)
    monkeypatch.setattr(jev, "decide", fake_decide)
    mid, _ = await _ingest(user, raw("Module 3 questions", frm="Student <s@example.edu>"))
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        ans = await decide.decide_choice(db, None, m, "Which project is this about?", [
            {"key": "grant-renewal", "name": "Grant renewal", "description": "the renewal"}, {"key": "chem-101", "name": "CHEM 101", "description": ""}])
    crit = sent["q"]["criteria"]
    assert sent["q"]["type"] == "choice" and crit == {"grant-renewal": "Grant renewal: the renewal", "chem-101": "CHEM 101", "none": "none of these"}
    assert ans.choice == "chem-101" and ans.confidence == 0.85 and ans.probabilities["none"] == 0.05


@pytest.mark.parametrize("body,err", [
    ({"name": "x", "question": "Which?", "choices": [{"name": "Only one", "actions": {"star": True}}]}, "2 to 8"),
    ({"name": "x", "choices": [{"name": "A", "actions": {"star": True}}, {"name": "B"}]}, "needs its question"),
    ({"name": "x", "question": "Which?", "choices": [{"name": "A", "actions": {"star": True}}, {"name": "a"}]}, "same name"),
    ({"name": "x", "question": "Which?", "choices": [{"name": "A", "actions": {"fly": True}}, {"name": "B"}]}, "unknown actions"),
])
async def test_choice_order_validation(client, user, body, err):
    r = await client.post("/api/v1/assistant/orders", json=body)
    assert r.status_code == 400 and err in r.text, r.text


async def test_assistant_creates_a_choice_order(client, user):
    from mailr.agent.tools import ToolCtx
    from mailr.assistant import orders as ord_
    from mailr.assistant.tools import manage_order
    from mailr.models import User

    await _label(client, "Grant renewal")
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        ctx = ToolCtx(db=db, p=ord_.principal_for(u), assistant=True, chat_session_id="t-choice")
        res = await manage_order(ctx, {"action": "create", "name": "Projects", "question": "Which project is this about?",
                                       "choices": [{"name": "Grant renewal", "add_labels": ["Grant renewal"]}, {"name": "Teaching", "star": True}]})
        await db.commit()
    prev = res.data if hasattr(res, "data") else res
    assert "pending" in str(prev).lower() or "approval" in str(prev).lower(), prev


async def test_choice_order_preview_names_the_pick(client, user, raw, monkeypatch):
    from mailr.assistant import decide

    async def fake(db, u, m, q, options):
        return decide.ChoiceAnswer("teaching" if "Module" in m.subject else None, 0.9, "", "jev")

    monkeypatch.setattr(decide, "decide_choice", fake)
    await _ingest(user, raw("Module 3 questions", frm="Student <s@example.edu>"), roles=["inbox"])
    await _ingest(user, raw("Lunch?", frm="Carol <carol@example.org>"), roles=["inbox"])
    r = (await client.post("/api/v1/assistant/orders/preview", json={
        "question": "Which project is this about?", "scope": "inbox",
        "choices": [{"name": "Grant renewal"}, {"name": "Teaching"}]})).json()
    got = {i["subject"]: (i["matched"], i["choice"]) for i in r["items"]}
    assert got["Module 3 questions"] == (True, "Teaching") and got["Lunch?"] == (False, None)


async def test_a_failing_order_is_recorded_and_the_others_run(client, user, raw, monkeypatch):
    """One order whose model call breaks: it shows as a failed run; the orders before and after it
    keep their runs and effects."""
    from mailr.assistant import decide, orders

    async def broken(db, u, m, q):
        raise RuntimeError("the model endpoint closed the connection")

    monkeypatch.setattr(decide, "decide", broken)
    lid = await _label(client, "Invoices")
    first = (await client.post("/api/v1/assistant/orders", json={
        "name": "Label invoices", "query": "subject:invoice", "actions": {"add_labels": [lid]}})).json()["id"]
    middle = (await client.post("/api/v1/assistant/orders", json={
        "name": "Ask first", "question": "Is this overdue?", "actions": {"important": True}})).json()["id"]
    last = (await client.post("/api/v1/assistant/orders", json={
        "name": "Star invoices", "query": "subject:invoice", "actions": {"star": True}})).json()["id"]
    mid, cid = await _ingest(user, raw("Invoice 2207", frm="Billing <billing@supplier.example>"))
    await orders.on_message({"message_id": mid}, None)
    async with sessionmaker()() as db:
        runs = {r.order_id: r for r in (await db.execute(select(AssistantRun).where(
            AssistantRun.message_id == mid))).scalars()}
        assert runs[first].status == "done" and runs[last].status == "done"
        assert runs[middle].status == "failed" and "closed the connection" in runs[middle].error
        conv = await db.get(Conversation, cid)
        assert conv.starred and lid in conv.label_ids


async def test_an_errand_interrupted_after_a_change_is_not_run_again(client, user, raw, monkeypatch):
    """A worker restart puts a running errand's job back in the queue. A run that already changed
    something (a tool that writes) is not started over, so nothing is made twice; one that had only
    read runs again."""
    from mailr.assistant import errands, orders
    from mailr.models import ChatMessage, ChatSession

    calls = []

    async def fake_chat(db, p, text, *, context=None, errand=None, **kw):
        calls.append(errand)
        yield {"type": "session", "session_id": f"errand-{errand}"}
        yield {"type": "final", "content": "Done."}

    import mailr.agent.loop as loop

    monkeypatch.setattr(loop, "run_chat", fake_chat)
    oid = (await client.post("/api/v1/assistant/orders", json={
        "name": "Seminars", "query": "from:seminars@example.org", "errand": "add the seminar to my calendar",
        "deliver": ["brief"]})).json()["id"]
    mid, _cid = await _ingest(user, raw("Seminar on Friday", frm="Seminars <seminars@example.org>"))
    other, _ = await _ingest(user, raw("Seminar next week", frm="Seminars <seminars@example.org>"))
    await orders.on_message({"message_id": mid}, None)
    await orders.on_message({"message_id": other}, None)
    async with sessionmaker()() as db:
        wrote, read = [r.id for r in (await db.execute(select(AssistantRun).where(
            AssistantRun.order_id == oid).order_by(AssistantRun.id))).scalars()]
        for rid, tool in ((wrote, "create_event"), (read, "search_conversations")):
            db.add(ChatSession(id=f"errand-{rid}", user_id=user.id, title="errand", context={}))
            await db.flush()
            db.add(ChatMessage(session_id=f"errand-{rid}", role="tool", name=tool, content="{}"))
            run = await db.get(AssistantRun, rid)
            run.status, run.session_id = "running", f"errand-{rid}"
        await db.commit()
    first = await errands.run_errand(wrote)
    assert first.status == "failed" and "not run again" in first.error and calls == []
    second = await errands.run_errand(read)
    assert second.status == "done" and calls == [read]
