# ACT-SURFACES: assistant api
"""The bulk review (agent/review.py): review_mail freezes a
scope and sets aside what must stay without a model, the job judges the rest in batches and reads an
email only when unsure, and the result is one review card that always asks and can be approved with
conversations left out. Stop and Continue keep what was decided.

Covers agent.review_mail."""

import json
import types

import pytest
from helpers.core_flow import _ingest
from sqlalchemy import select, update

from mailr.db import sessionmaker
from mailr.models import (
    ChatSession,
    Conversation,
    Job,
    Label,
    MailReview,
    PendingAction,
    Person,
    User,
)


async def _ctx(db, user_id, ui=None, session="rv1"):
    from mailr.agent import screen
    from mailr.agent.tools import ToolCtx
    from mailr.core.auth import ALL_SCOPES, Principal

    if await db.get(ChatSession, session) is None:
        db.add(ChatSession(id=session, user_id=user_id, title="t", sensitive=False, context={}))
        await db.flush()
    p = Principal(user=await db.get(User, user_id), scopes=set(ALL_SCOPES) - {"admin"})
    return ToolCtx(db=db, p=p, assistant=True, chat_session_id=session, ui=screen.sanitize(ui or {}))


def _fake_model(monkeypatch, decide):
    """llm.chat answering each batch with decide(row) -> (action, reason); records the calls."""
    from mailr.intel import llm

    calls = []

    async def chat(db, user, messages, **kw):
        text = messages[0]["content"]
        rows = [json.loads(x) for x in text.split("<rows>\n")[1].split("\n</rows>")[0].splitlines()]
        calls.append({"rows": rows, "sensitive": kw.get("sensitive")})
        return {"content": json.dumps({"decisions": [{"id": r["id"], "action": decide(r)[0], "reason": decide(r)[1]}
                                                     for r in rows]}), "_model": "fake-model"}

    monkeypatch.setattr(llm, "chat", chat)
    return calls


async def _run(rid):
    from mailr.agent import review

    await review.review_job({"review_id": rid}, types.SimpleNamespace(job_id=0, user_id=None))
    async with sessionmaker()() as db:
        return await db.get(MailReview, rid)


async def _mail(user, raw):
    ids = {}
    for name, frm in (("stridewell1", "Stridewell <news@stridewell.example>"), ("stridewell2", "Stridewell <news@stridewell.example>"),
                      ("receipt", "Shop <orders@shop.example>"), ("odd", "Odd <odd@odd.example>"),
                      ("starred", "Deals <deals@x.example>"), ("friend", "Dana <dana@example.org>")):
        _, ids[name] = await _ingest(user, raw(f"Sweep {name}", frm=frm))
    async with sessionmaker()() as db:
        await db.execute(update(Conversation).where(Conversation.id == ids["starred"]).values(starred=True))
        await db.execute(update(Person).where(Person.user_id == user.id, Person.email == "dana@example.org")
                         .values(sent_count=2))
        await db.commit()
    return ids


async def test_a_review_ends_in_one_card_approved_with_exceptions(client, user, raw, monkeypatch):
    from mailr.agent.tools import run_tool

    ids = await _mail(user, raw)

    def decide(row):
        if "stridewell" in row["subject"]:
            return "trash", "old promotion"
        if "receipt" in row["subject"]:
            return "archive", "receipt to keep"
        return ("keep", "looks personal") if "text" in row else ("unsure", "cannot tell")

    calls = _fake_model(monkeypatch, decide)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, {"view": "inbox", "list": {"view": "inbox", "q": "subject:sweep", "total": 6}})
        res = await run_tool(ctx, "review_mail", {"scope": "screen", "instructions": "keep anything personal"})
        assert res.data["status"] == "review_started" and res.events == [{"type": "review", "review_id": res.data["review_id"]}]
        again = await run_tool(ctx, "review_mail", {"scope": "screen"})
        assert again.data["status"] == "already_running"
        assert (await db.execute(select(Job).where(Job.kind == "assistant.review",
                                                   Job.payload["review_id"].as_integer() == res.data["review_id"]))).scalar()
    r = await _run(res.data["review_id"])
    assert r.status == "done" and r.kept == {str(ids["starred"]): "starred", str(ids["friend"]): "known"}
    # The first pass saw only the four others, as short rows; the unsure one was read in full once.
    assert sorted(x["id"] for x in calls[0]["rows"]) == sorted([ids["stridewell1"], ids["stridewell2"], ids["receipt"], ids["odd"]])
    assert "text" not in calls[0]["rows"][0] and [x["id"] for x in calls[1]["rows"]] == [ids["odd"]]
    assert "text" in calls[1]["rows"][0] and calls[0]["sensitive"] is False
    assert r.decisions[str(ids["odd"])]["a"] == "keep"
    async with sessionmaker()() as db:
        pa = await db.get(PendingAction, r.approval_id)
    assert pa.status == "pending" and pa.action == "mail.trash" and pa.payload["op"] == "review"
    card = pa.preview["review"]
    assert [g["move"] for g in card["groups"]] == ["trash", "archive"]
    first = card["groups"][0]["senders"][0]
    assert first == {"from": "Stridewell", "count": 2, "email": "news@stridewell.example",
                     "conversation_ids": first["conversation_ids"], "subjects": first["subjects"],
                     "conversations": first["conversations"]}
    # Every conversation of a sender, for the card's per-conversation move menu.
    assert [c["conversation_id"] for c in first["conversations"]] == first["conversation_ids"]
    assert {c["subject"] for c in first["conversations"]} == {"Sweep stridewell1", "Sweep stridewell2"}
    assert all(c["date"] for c in first["conversations"])
    assert card["kept"][0]["conversation_id"] == ids["odd"] and card["kept"][0]["date"]
    # Two conversations from one sender are not enough for a filter suggestion (FILTER_FROM is 3).
    assert "suggest_filter" not in first
    assert card["set_aside"] == {"starred": 1, "known": 1} and card["kept_count"] == 1
    assert pa.preview["title"] == 'Make 3 changes from the review of Inbox (all tabs) matching "subject:sweep"'

    # Approve, leaving one Stridewell mail out: only the rest is moved.
    r2 = await client.post(f"/api/v1/approvals/{pa.id}/decide", json={"decision": "approve", "except_ids": [ids["stridewell2"]]})
    assert r2.status_code == 200, r2.text
    from mailr.core import permissions

    await permissions.execute_job({"pending_id": pa.id}, None)  # the approval.execute job
    async with sessionmaker()() as db:
        pa = await db.get(PendingAction, pa.id)
        jobs = (await db.execute(select(Job).where(Job.user_id == user.id, Job.kind == "bulk.conversations")
                                 .order_by(Job.id))).scalars().all()
    plan = {g["move"]: g["conversation_ids"] for g in pa.payload["plan"]}
    assert plan == {"trash": [ids["stridewell1"]], "archive": [ids["receipt"]]} and pa.preview["left_out"] == 1
    from mailr.core import tasks

    for j in jobs[-2:]:
        await tasks.bulk_conversations(dict(j.payload), types.SimpleNamespace(job_id=j.id, user_id=user.id))
    async with sessionmaker()() as db:
        roles = {lb.role: lb.id for lb in (await db.execute(select(Label).where(Label.user_id == user.id))).scalars()}
        convs = {c.id: c for c in (await db.execute(select(Conversation).where(Conversation.id.in_(ids.values())))).scalars()}
        assert (await db.get(MailReview, r.id)).status == "applied"
    assert roles["trash"] in convs[ids["stridewell1"]].label_ids and roles["trash"] not in convs[ids["stridewell2"]].label_ids
    assert roles["inbox"] not in convs[ids["receipt"]].label_ids and roles["inbox"] in convs[ids["odd"]].label_ids


async def test_stop_keeps_what_was_decided_and_continue_resumes(client, user, raw, monkeypatch):
    from mailr.agent.tools import run_tool

    await _mail(user, raw)
    _fake_model(monkeypatch, lambda row: ("archive", "done with it"))
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, session="rv2")
        res = await run_tool(ctx, "review_mail", {"scope": {"view": "inbox", "q": "subject:sweep"}})
    rid = res.data["review_id"]
    stop = await client.post(f"/api/v1/agent/reviews/{rid}/stop")
    assert stop.status_code == 200 and stop.json()["status"] == "stopping"
    r = await _run(rid)
    assert r.status == "stopped" and r.approval_id is None
    assert (await client.post(f"/api/v1/agent/reviews/{rid}/stop")).status_code == 409
    cont = await client.post(f"/api/v1/agent/reviews/{rid}/resume")
    assert cont.status_code == 200 and cont.json()["status"] == "running"
    r = await _run(rid)
    got = (await client.get(f"/api/v1/agent/reviews/{rid}")).json()
    assert r.status == "done" and got["approval_id"] and got["decided"] == {"archive": 4} and got["set_aside"] == 2


async def test_sweep_command_and_errors(client, user, raw):
    from mailr.agent import commands
    from mailr.agent.tools import ToolCtx, ToolError, run_tool
    from mailr.core.auth import ALL_SCOPES, Principal

    cmd, text = commands.parse("/cleanup keep the running club")
    assert cmd.name == "sweep" and "review_mail" in commands.request_for(cmd, text)
    assert "keep the running club" in commands.request_for(cmd, text)
    async with sessionmaker()() as db:
        p = Principal(user=await db.get(User, user.id), scopes=set(ALL_SCOPES) - {"admin"})
        import pytest

        with pytest.raises(ToolError, match="assistant's chat"):
            await run_tool(ToolCtx(db=db, p=p), "review_mail", {"scope": {"view": "inbox", "q": "x"}})
        ctx = await _ctx(db, user.id, session="rv3")
        with pytest.raises(ToolError, match="no label named"):
            await run_tool(ctx, "review_mail", {"scope": {"view": "inbox", "q": "x"}, "labels": ["Nope"]})
    assert (await client.get("/api/v1/agent/reviews/999999")).status_code == 404


def test_the_card_suggests_a_filter_for_a_sender_with_several_of_one_move():
    from mailr.agent import review
    from mailr.models import Conversation

    convs = {i: Conversation(id=i, user_id=1, subject=f"Sale {i}",
                             participants=[{"email": "Deals@Shop.example", "name": "Shop"}]) for i in (1, 2, 3, 4)}
    r = MailReview(id=9, user_id=1, ids=[1, 2, 3, 4], kept={}, scope={"label": "Promotions"}, labels=["Shops"],
                   decisions={"1": {"a": "trash"}, "2": {"a": "trash"}, "3": {"a": "trash"},
                              "4": {"a": "label", "l": "Shops"}})
    plan, preview = review.card(r, convs, {"Shops": 77})
    trash, label = preview["review"]["groups"]
    assert trash["senders"][0]["email"] == "deals@shop.example" and trash["senders"][0]["suggest_filter"] is True
    assert label["label_id"] == 77 and "suggest_filter" not in label["senders"][0]


async def test_discarding_a_review_card_resumes_the_chat_without_an_error(client, user, raw, monkeypatch, fake_llm):
    """Discard on a review card (deny): the web resumes the chat with the card's id. The card comes
    from the review job, not from a tool call of the turn, and was not among the chat's waiting
    approvals, so the resume answered "That approval is still waiting." (sweep)."""
    from helpers.agent import _drop_endpoint, _endpoint, _events, _text_turn

    from mailr.agent.tools import run_tool
    from mailr.intel import llm

    await _mail(user, raw)
    real_chat = llm.chat
    _fake_model(monkeypatch, lambda row: ("archive", "done with it"))
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, session="rv-discard")
        res = await run_tool(ctx, "review_mail", {"scope": {"view": "inbox", "q": "subject:sweep"}})
    r = await _run(res.data["review_id"])
    monkeypatch.setattr(llm, "chat", real_chat)
    async with sessionmaker()() as db:
        s = await db.get(ChatSession, "rv-discard")
        assert r.approval_id in s.context["pending"]  # the card waits in its chat like any approval
    d = await client.post(f"/api/v1/approvals/{r.approval_id}/decide", json={"decision": "deny"})
    assert d.status_code == 200, d.text
    assert d.json()["status"] == "denied"
    assert d.json()["outcome"]["text"] == "Discarded · nothing changed"
    async with sessionmaker()() as db:
        assert (await db.get(MailReview, r.id)).status == "done"  # nothing was applied
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [_text_turn("Discarded; nothing changed.")]
        evs = _events((await client.post("/api/v1/agent/chat", json={"session_id": "rv-discard",
                                                                      "resume_approval_id": r.approval_id})).text)
        assert not [e for e in evs if e["type"] == "error"], evs
        assert evs[-1]["type"] == "done", evs
        update_ = next(e for e in evs if e["type"] == "approval_update")["approval"]
        assert update_["status"] == "denied" and update_["outcome"]["text"] == "Discarded · nothing changed"
        note = fake_llm.requests[-1]["body"]["messages"][-1]["content"]
        assert f"Approval {r.approval_id}" in note and "denied by the user" in note
        # Told once: a second resume for the same card has nothing new and starts no model turn.
        n = len(fake_llm.requests)
        again = _events((await client.post("/api/v1/agent/chat", json={"session_id": "rv-discard",
                                                                        "resume_approval_id": r.approval_id})).text)
        assert len(fake_llm.requests) == n and [e["type"] for e in again] == ["error"]
    finally:
        await _drop_endpoint(ep)


async def test_applying_a_review_card_resumes_the_chat_with_its_outcome(client, user, raw, monkeypatch, fake_llm):
    """Apply on a review card: the same resume as Discard, with the "approved and done" note."""
    from helpers.agent import _drop_endpoint, _endpoint, _events, _text_turn

    from mailr.agent.tools import run_tool
    from mailr.core import permissions
    from mailr.intel import llm

    await _mail(user, raw)
    real_chat = llm.chat
    _fake_model(monkeypatch, lambda row: ("archive", "done with it"))
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, session="rv-apply")
        # Another key of the chat's context stays as it was.
        (await db.get(ChatSession, "rv-apply")).context = {"skills": ["tidy"]}
        await db.commit()
        res = await run_tool(ctx, "review_mail", {"scope": {"view": "inbox", "q": "subject:sweep"}})
    r = await _run(res.data["review_id"])
    monkeypatch.setattr(llm, "chat", real_chat)
    async with sessionmaker()() as db:
        s = await db.get(ChatSession, "rv-apply")
        assert s.context["pending"] == [r.approval_id] and s.context["skills"] == ["tidy"]
    d = await client.post(f"/api/v1/approvals/{r.approval_id}/decide", json={"decision": "approve"})
    assert d.status_code == 200, d.text
    await permissions.execute_job({"pending_id": r.approval_id}, None)  # the approval.execute job
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [_text_turn("Applied: 4 conversations archived.")]
        evs = _events((await client.post("/api/v1/agent/chat", json={"session_id": "rv-apply",
                                                                      "resume_approval_id": r.approval_id})).text)
        assert not [e for e in evs if e["type"] == "error"], evs
        assert evs[-1]["type"] == "done", evs
        assert next(e for e in evs if e["type"] == "approval_update")["approval"]["status"] == "executed"
        note = fake_llm.requests[-1]["body"]["messages"][-1]["content"]
        assert f"Approval {r.approval_id}" in note and "approved and done" in note
    finally:
        await _drop_endpoint(ep)


async def test_the_card_takes_the_users_own_moves(client, user, raw, monkeypatch):
    """Apply with `changes`: any conversation the card shows (a plan group, a kept or an unsure row) can get
    another move (trash, archive, read, label:<id>, keep); except_ids are keeps. Anything else is refused."""
    from mailr.agent.tools import run_tool
    from mailr.core import permissions

    ids = await _mail(user, raw)

    def decide(row):
        if "stridewell" in row["subject"]:
            return "trash", "old promotion"
        if "receipt" in row["subject"]:
            return "archive", "receipt to keep"
        return ("keep", "looks personal") if "text" in row else ("unsure", "cannot tell")

    _fake_model(monkeypatch, decide)
    shops = await client.post("/api/v1/labels", json={"name": "Shops"})
    assert shops.status_code in (200, 201), shops.text
    shops_id = shops.json()["id"]
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, session="rv-changes")
        res = await run_tool(ctx, "review_mail", {"scope": {"view": "inbox", "q": "subject:sweep"}})
        inbox_id = (await db.execute(select(Label.id).where(Label.user_id == user.id, Label.role == "inbox"))).scalar()
    r = await _run(res.data["review_id"])
    url = f"/api/v1/approvals/{r.approval_id}/decide"

    async def refused(changes, **kw):
        got = await client.post(url, json={"decision": "approve", "changes": {str(k): v for k, v in changes.items()}, **kw})
        assert got.status_code == 400, got.text
        return got.json()["detail"]

    # Set aside without a model (starred): not a row of the card.
    assert "not on this review card" in await refused({ids["starred"]: "trash"})
    assert "not on this review card" in await refused({999_999_999: "trash"})
    assert "unknown move" in await refused({ids["odd"]: "spam"})
    assert "label you do not have" in await refused({ids["odd"]: f"label:{inbox_id}"})
    assert "label you do not have" in await refused({ids["odd"]: "label:999999"})
    async with sessionmaker()() as db:
        assert (await db.get(PendingAction, r.approval_id)).status == "pending"

    ok = await client.post(url, json={"decision": "approve", "except_ids": [ids["stridewell1"]], "changes": {
        str(ids["stridewell2"]): "archive", str(ids["odd"]): "trash", str(ids["receipt"]): f"label:{shops_id}"}})
    assert ok.status_code == 200, ok.text
    await permissions.execute_job({"pending_id": r.approval_id}, None)
    async with sessionmaker()() as db:
        pa = await db.get(PendingAction, r.approval_id)
    plan = {g["move"]: g["conversation_ids"] for g in pa.payload["plan"]}
    assert plan == {"trash": [ids["odd"]], "archive": [ids["stridewell2"]], "label": [ids["receipt"]]}
    lab = next(g for g in pa.payload["plan"] if g["move"] == "label")
    assert lab["action"] == "add_labels" and lab["args"] == {"label_ids": [shops_id]} and lab["label"] == "Shops"
    card = pa.preview["review"]
    assert pa.preview["changed"] == 4 and card["changed"] == 4 and pa.preview["left_out"] == 1
    assert pa.preview["title"] == 'Make 3 changes from the review of Inbox (all tabs) matching "subject:sweep"'
    assert "4 changed by you" in pa.preview["lines"]
    assert [g["move"] for g in card["groups"]] == ["trash", "archive", "label"]
    assert card["kept"] == [{"conversation_id": ids["stridewell1"], "from": "Stridewell", "email": "news@stridewell.example",
                             "subject": "Sweep stridewell1", "date": card["kept"][0]["date"], "reason": "",
                             "by_you": True}]
    assert card["kept_count"] == 1 and card["unsure_count"] == 0
    assert pa.status in ("approved", "executing", "executed")


def test_apply_changes_keep_is_except_and_ignores_unchanged():
    from mailr.agent import review
    from mailr.models import Conversation

    convs = {i: Conversation(id=i, user_id=1, subject=f"Sale {i}",
                             participants=[{"email": "deals@shop.example", "name": "Shop"}]) for i in (1, 2, 3)}
    r = MailReview(id=9, user_id=1, ids=[1, 2, 3], kept={}, scope={"label": "Promotions"}, labels=[],
                   decisions={"1": {"a": "trash"}, "2": {"a": "trash"}, "3": {"a": "archive"}})
    plan, preview = review.card(r, convs, {})
    by_keep, pv = review.apply_changes({"op": "review", "plan": plan}, preview, {2: "keep", 1: "trash"}, {})
    by_except, pv2 = review.narrow({"op": "review", "plan": plan}, preview, [2])
    assert by_keep["plan"] == by_except["plan"]
    assert pv["changed"] == 1 and pv["left_out"] == 1 and pv["title"] == "Make 2 changes from the review of Promotions"


def test_moves_that_change_nothing_leave_the_plan():
    """/sweep in All Mail proposed Archive for a conversation already out of the Inbox: Archive is
    only for conversations in the Inbox and Mark read only for unread ones; the others are kept."""
    from mailr.agent import review
    from mailr.models import Conversation

    inbox = 5
    convs = {i: Conversation(id=i, user_id=1, subject=f"Note {i}", label_ids=labels, unread_count=unread,
                             participants=[{"email": "news@paper.example", "name": "Paper"}])
             for i, labels, unread in ((1, [inbox], 0), (2, [], 0), (3, [inbox], 2), (4, [inbox], 0))}
    r = MailReview(id=9, user_id=1, ids=[1, 2, 3, 4], kept={}, scope={"label": "All Mail"}, labels=[],
                   decisions={"1": {"a": "archive", "r": "old"}, "2": {"a": "archive", "r": "old"},
                              "3": {"a": "read", "r": "seen it"}, "4": {"a": "read", "r": "seen it"}})
    plan, preview = review.card(r, convs, {}, inbox)
    assert {p["move"]: p["conversation_ids"] for p in plan} == {"archive": [1], "read": [3]}
    kept = {k["conversation_id"]: k["reason"] for k in preview["review"]["kept"]}
    assert kept == {2: "already archived", 4: "already read"}
    assert preview["title"] == "Make 2 changes from the review of All Mail"
    # The model is told which rows are out of the Inbox.
    assert "in_inbox" in review.prompt([{"id": 2, "in_inbox": False}], "", [])


async def test_the_rows_say_which_conversations_are_out_of_the_inbox(user, raw):
    from mailr.agent import review

    _, a = await _ingest(user, raw("Sweep inbox one", frm="Paper <news@paper.example>"))
    _, b = await _ingest(user, raw("Sweep archived one", frm="Paper <news@paper.example>"))
    async with sessionmaker()() as db:
        await db.execute(update(Conversation).where(Conversation.id == b).values(label_ids=[]))
        await db.commit()
        rows = {x["id"]: x for x in await review._rows(db, [a, b])}
    assert "in_inbox" not in rows[a] and rows[b]["in_inbox"] is False


async def test_a_long_review_works_in_slices_and_an_orphan_can_continue(client, user, raw, monkeypatch):
    """A run works SLICE_S, then puts its job back in the queue (Retry) instead of reaching the job
    timeout. A review left "running" with no job (the worker gave up) shows as failed and continues;
    one left "stopping" shows as stopped."""
    from mailr.agent import review
    from mailr.agent.tools import run_tool
    from mailr.core.jobs import Retry

    await _mail(user, raw)
    _fake_model(monkeypatch, lambda row: ("archive", "done with it"))
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, session="rv5")
        res = await run_tool(ctx, "review_mail", {"scope": {"view": "inbox", "q": "subject:sweep"}})
    rid = res.data["review_id"]
    monkeypatch.setattr(review, "SLICE_S", -1)
    with pytest.raises(Retry):
        await review.review_job({"review_id": rid}, types.SimpleNamespace(job_id=0, user_id=None))
    monkeypatch.setattr(review, "SLICE_S", 8 * 60)
    # The worker gave up on the job: nothing works on the review any more.
    async with sessionmaker()() as db:
        await db.execute(update(Job).where(Job.dedupe_key == f"assistant.review:{rid}").values(status="failed"))
        await db.commit()
    got = (await client.get(f"/api/v1/agent/reviews/{rid}")).json()
    assert got["status"] == "failed"
    cont = await client.post(f"/api/v1/agent/reviews/{rid}/resume")
    assert cont.status_code == 200 and cont.json()["status"] == "running"
    stop = await client.post(f"/api/v1/agent/reviews/{rid}/stop")
    assert stop.json()["status"] == "stopping"
    async with sessionmaker()() as db:
        await db.execute(update(Job).where(Job.dedupe_key == f"assistant.review:{rid}").values(status="failed"))
        await db.commit()
    assert (await client.get(f"/api/v1/agent/reviews/{rid}")).json()["status"] == "stopped"
    assert (await client.post(f"/api/v1/agent/reviews/{rid}/resume")).json()["status"] == "running"
    r = await _run(rid)
    assert r.status == "done"
