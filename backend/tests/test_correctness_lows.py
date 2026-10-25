"""Smaller correctness fixes: a chat turn keeps approvals a review added meanwhile, a second Send to a
busy chat is refused at once, a job's outcome is written only by the run that owns it, a rule edited
while it is applied is applied again, the browser's zone replans later in a job, a model's yes and
date are read strictly, one bad message does not stop a backfill, a review's malformed answer fails
its batch, an order whose model call failed runs again instead of counting as "no", a model's error
names no endpoint, reminder details follow the user's language, and a stalled model stream stops at
the turn's time limit."""

import asyncio
import time
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from helpers.agent import _drop_endpoint, _endpoint
from helpers.core_flow import _ingest
from sqlalchemy import select, text, update

from mailr.core import jobs
from mailr.core.jobs import Retry
from mailr.db import session_scope, sessionmaker
from mailr.models import AssistantRun, ChatSession, Job, Message, Rule, User


async def test_a_turn_keeps_an_approval_a_review_added_meanwhile(user):
    from mailr.agent.loop import merge_context

    async with sessionmaker()() as db:
        db.add(ChatSession(id=f"lc1-{user.id}", user_id=user.id, title="t", sensitive=False,
                           context={"pending": [1], "told": [7]}))
        await db.commit()
    async with sessionmaker()() as turn:
        s = await turn.get(ChatSession, f"lc1-{user.id}")
        await turn.commit()
        async with sessionmaker()() as review:  # the review job's card waits in the chat
            rs = await review.get(ChatSession, s.id)
            rs.context = {**rs.context, "pending": [1, 5, 7]}
            await review.commit()
        s.context = {**(s.context or {}), "skills": ["formal"]}
        await merge_context(turn, s)
        await turn.commit()
    async with sessionmaker()() as db:
        ctx = (await db.get(ChatSession, f"lc1-{user.id}")).context
    assert ctx["pending"] == [1, 5] and ctx["skills"] == ["formal"]  # 7 was told already


async def test_a_second_send_to_a_busy_chat_is_refused(client, user, monkeypatch):
    from mailr.intel import llm

    ep = await _endpoint(user.id, "local")
    gate = asyncio.Event()

    async def slow(*a, **kw):
        await gate.wait()
        yield {"content": "done"}

    monkeypatch.setattr(llm, "chat_stream", slow)
    async with sessionmaker()() as db:
        db.add(ChatSession(id=f"lc2-{user.id}", user_id=user.id, title="t", sensitive=False, context={}))
        await db.commit()

    async def release():
        await asyncio.sleep(1.5)
        gate.set()

    try:
        body = {"message": "hi", "session_id": f"lc2-{user.id}"}
        a, b, _ = await asyncio.gather(client.post("/api/v1/agent/chat", json=body),
                                       client.post("/api/v1/agent/chat", json=body), release())
        assert sorted([a.status_code, b.status_code]) == [200, 409]
    finally:
        await _drop_endpoint(ep)


async def test_a_job_outcome_is_written_only_by_its_own_run(app):
    async with session_scope() as db:
        jid = (await db.execute(text(
            "INSERT INTO jobs (kind, payload, status, attempts, max_attempts, run_at, created_at, error) "
            "VALUES ('test.owned', '{}', 'running', 2, 5, now(), now(), '') RETURNING id"))).scalar_one()
    stale = jobs.Job(id=jid, kind="test.owned", user_id=None, payload={}, attempts=1, max_attempts=5)
    await jobs._finish(stale, "done")
    mine = jobs.Job(id=jid, kind="test.owned", user_id=None, payload={}, attempts=2, max_attempts=5)
    async with sessionmaker()() as db:
        assert (await db.get(Job, jid)).status == "running"
    await jobs._finish(mine, "queued", delay=60, refund=True)
    async with sessionmaker()() as db:
        row = await db.get(Job, jid)
        assert row.status == "queued" and row.attempts == 1


async def test_a_rule_edited_while_it_is_applied_runs_again(user, raw, monkeypatch):
    from mailr.core import rules, tasks

    async with sessionmaker()() as db:
        r = Rule(user_id=user.id, name="r", query="from:dana@example.org", actions={"star": True}, enabled=True)
        db.add(r)
        await db.commit()
        rid = r.id
    real = rules.matching_message_ids

    async def edit_meanwhile(db, uid, query, **kw):
        async with sessionmaker()() as other:
            (await other.get(Rule, rid)).actions = {"star": True, "mark_read": True}
            await other.commit()
        return await real(db, uid, query, **kw)

    monkeypatch.setattr(rules, "matching_message_ids", edit_meanwhile)
    with pytest.raises(jobs.Retry):
        await tasks.rules_apply({"rule_id": rid}, None)
    monkeypatch.setattr(rules, "matching_message_ids", real)
    await tasks.rules_apply({"rule_id": rid}, None)  # unchanged since: done


async def test_the_browsers_zone_replans_in_a_later_job(user):
    from mailr.calendar import tz

    async with session_scope() as db:
        assert await tz.note_device_zone(db, await db.get(User, user.id), "Europe/Lisbon")
    async with session_scope() as db:
        u = await db.get(User, user.id)
        u.settings = {**u.settings, "device_timezone": "Asia/Tokyo"}  # as if the phone had sent it
    async with session_scope() as db:
        assert await tz.note_device_zone(db, await db.get(User, user.id), "Europe/Lisbon")
    async with sessionmaker()() as db:
        rows = (await db.execute(select(Job).where(Job.user_id == user.id, Job.kind == "calendar.replan_zone",
                                                   Job.status == "queued"))).scalars().all()
    assert len(rows) == 1 and rows[0].run_at > datetime.now(UTC)


def test_a_models_yes_and_date_are_read_strictly():
    from mailr.intel.classify import _iso_day, _yes

    assert _yes(True) and _yes("true") and _yes(" Yes ")
    assert not _yes("false") and not _yes(1) and not _yes(None)
    assert _iso_day("2026-11-02") == "2026-11-02" and _iso_day("next Friday") is None and _iso_day(None) is None


async def test_one_bad_message_does_not_stop_the_backfill(user, raw, monkeypatch):
    from embedfake import embedding_endpoint

    from mailr.intel import tasks as intel_tasks

    async with embedding_endpoint(monkeypatch):
        bad, _ = await _ingest(user, raw("Broken one"), roles=["inbox"])
        good, _ = await _ingest(user, raw("Fine one"), roles=["inbox"])
        real = intel_tasks.process_message

        async def flaky(db, m, u, **kw):
            if m.id == bad:
                raise ValueError("bad model output")
            return await real(db, m, u, **kw)

        monkeypatch.setattr(intel_tasks, "process_message", flaky)
        async with sessionmaker()() as db:  # imported mail, which the backfill triages
            await db.execute(update(Message).where(Message.id.in_([bad, good])).values(intel_state="backfill"))
            await db.commit()
        with pytest.raises(Retry):  # the next batch: the same job again
            await intel_tasks.intel_backfill({"user_id": user.id}, None)
    async with sessionmaker()() as db:
        assert (await db.get(Message, bad)).intel_state == "skipped"
        assert (await db.get(Message, good)).intel_state == "done"


async def test_a_reviews_malformed_answer_fails_the_batch(user, monkeypatch):
    from mailr.agent import review
    from mailr.intel import llm

    answers = iter([{"content": '[{"id": 1, "action": "trash", "reason": "old"}]'},
                    {"content": '{"decisions": [{"id": 1, "act', "_finish": "length"}])

    async def chat(*a, **kw):
        return next(answers)

    monkeypatch.setattr(llm, "chat", chat)
    got, _ = await review.judge(None, None, [{"id": 1}], "", [], False)
    assert got["1"]["a"] == "trash"  # a bare list of decisions is read
    with pytest.raises(ValueError, match="cut off"):
        await review.judge(None, None, [{"id": 1}], "", [], False)


async def test_an_orders_failed_model_call_runs_again(client, user, raw, monkeypatch):
    from mailr.assistant import decide, orders

    calls = []

    async def down(db, u, m, q):
        calls.append(1)
        return decide.Answer(False, 0.0, "the model call failed: timeout", "error")

    monkeypatch.setattr(decide, "decide", down)
    oid = (await client.post("/api/v1/assistant/orders", json={
        "name": "Invoices", "question": "Is this an invoice?", "actions": {"star": True}})).json()["id"]
    mid, _ = await _ingest(user, raw("Invoice 42", frm="Billing <billing@example.org>"))
    await orders.on_message({"message_id": mid}, None)
    async with sessionmaker()() as db:
        assert (await db.execute(select(AssistantRun).where(AssistantRun.order_id == oid))).scalars().all() == []
        again = (await db.execute(select(Job).where(Job.kind == "assistant.orders", Job.user_id == user.id,
                                                    Job.status == "queued"))).scalars().all()
    assert [j.payload for j in again if j.payload.get("tries")] == [{"message_id": mid, "tries": 1}]
    # The last pass records the failure instead of a silent "no".
    await orders.on_message({"message_id": mid, "tries": len(orders.ORDER_RETRIES)}, None)
    async with sessionmaker()() as db:
        runs = (await db.execute(select(AssistantRun).where(AssistantRun.order_id == oid))).scalars().all()
    assert [r.status for r in runs] == ["failed"] and "model call failed" in runs[0].error


def test_a_model_error_names_no_endpoint():
    from mailr.intel.llm import ModelError

    e = ModelError("gpu-box.internal", 400, '{"error": "maximum context length is 8192 tokens"}')
    assert str(e) == "the model endpoint returned 400" and "gpu-box" in e.detail
    from mailr.agent.loop import context_overflow

    assert context_overflow(e)


def test_reminder_details_follow_the_language():
    from mailr.calendar import planner

    zone = ZoneInfo("Europe/Berlin")
    now = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
    assert planner.task_detail(now + timedelta(hours=6), zone, now, True, "de") == "Fällig heute, 15:00"
    assert planner.task_detail(date(2026, 11, 6), zone, now, True, "de") == "Fällig Fr, 6. Nov"
    assert planner.task_detail(date(2026, 11, 6), zone, now, True) == "Due Fri, Nov 6"
    start = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    assert planner.event_detail(start, start + timedelta(days=1), True, "Raum 4", zone, True, "de") == "Ganztägig · Raum 4"


async def test_a_stalled_model_stream_stops_at_the_deadline():
    from mailr.agent.loop import bounded

    async def stalls():
        yield {"content": "a"}
        await asyncio.sleep(30)
        yield {"content": "never"}

    late: dict = {}
    t0 = time.monotonic()
    got = [ev async for ev in bounded(stalls(), time.monotonic() + 0.3, late)]
    assert got == [{"content": "a"}] and late == {"late": True} and time.monotonic() - t0 < 5
