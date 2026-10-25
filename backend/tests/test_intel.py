"""Intelligence layer tests. Embeddings come from a deterministic fake endpoint
(tests/embedfake.py) and any LLM is mocked; the opt-in live test (MAILR_LIVE=1) talks to the
real local model."""

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import mail_date
from embedfake import embedding_endpoint
from sqlalchemy import delete, select

from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.jobs import Retry
from mailr.core.store import user_store
from mailr.db import sessionmaker
from mailr.intel import categories, classify, features, importance, llm
from mailr.models import Message, ModelEndpoint, User


@pytest.fixture(autouse=True)
async def _embeddings(monkeypatch, app):
    async with embedding_endpoint(monkeypatch):
        yield


def _now_hdr():
    return datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")


async def _ingest(user, data, **kw):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        store = await user_store(db, u)
        res = await ingest_raw(db, store, data, IngestOptions(**kw))
        await db.commit()
        return res.message.id, res.message.conversation_id


async def _process(mid, allow_llm=False):
    from mailr.intel.tasks import process_message

    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        u = await db.get(User, m.user_id)
        await process_message(db, m, u, allow_llm=allow_llm)
        await db.commit()
        return m


@asynccontextmanager
async def endpoints(*eps):
    names = [e["name"] for e in eps]
    async with sessionmaker()() as db:
        for e in eps:
            db.add(ModelEndpoint(**e))
        await db.commit()
    try:
        yield
    finally:
        async with sessionmaker()() as db:
            await db.execute(delete(ModelEndpoint).where(ModelEndpoint.name.in_(names)))
            await db.commit()


def _msg(**kw):
    base = dict(id=1, user_id=1, subject="", from_email="x@example.org", from_name="", to=[], cc=[],
                list_id=None, list_unsubscribe=None, body_text="", sent_by_me=False, attachments=[],
                auth_results={}, delivered_to=[], has_attachments=False, date=datetime.now(UTC),
                conversation_id=None, in_reply_to=None, embedding=None)
    base.update(kw)
    return Message(**base)


def _sig(m, hdr=None, sent=0, received=0, participated=False):
    from mailr.models import Person

    p = Person(email=m.from_email, name=m.from_name, sent_count=sent, received_count=received) if (sent or received) else None
    return features._signals(m, p, (2, participated) if participated else None, {"me@example.com"}, hdr or {})


def test_category_heuristics():
    news = _msg(subject="Autumn sale: 40% off everything", from_email="news@shop.example",
                list_unsubscribe="<mailto:u@shop.example>", body_text="Shop now, limited time.")
    assert categories.heuristic(news, _sig(news, {"x-mailchimp-id": "1"})).category == "promotions"
    rec = _msg(subject="Your order #1234 has shipped", from_email="no-reply@store.example",
               body_text="Tracking number 1Z999")
    assert categories.heuristic(rec, _sig(rec)).category == "purchases"
    soc = _msg(subject="You appeared in 5 searches", from_email="notifications-noreply@linkedin.com")
    assert categories.heuristic(soc, _sig(soc)).category == "social"
    gh = _msg(subject="[org/repo] Fix bug (#12)", from_email="notifications@github.com",
              list_id="repo.org.github.com")
    assert categories.heuristic(gh, _sig(gh)).category == "updates"
    forum = _msg(subject="[stats-list] Question about priors", from_email="bob@uni.example",
                 list_id="stats-list.googlegroups.com")
    assert categories.heuristic(forum, _sig(forum, {"list-post": "<mailto:x>"})).category == "forums"
    personal = _msg(subject="Dinner Saturday?", from_email="alice@example.org",
                    to=[{"email": "me@example.com", "name": ""}], body_text="Are you free?")
    g = categories.heuristic(personal, _sig(personal, received=4))
    assert g.category == "primary" and g.confidence >= 0.8


def test_importance_prior_ordering():
    to_me = [{"email": "me@example.com", "name": ""}]
    friend = _msg(from_email="alice@example.org", from_name="Alice", to=to_me, body_text="Can you review this?")
    stranger = _msg(from_email="new@example.net", to=to_me, body_text="Hello")
    news = _msg(from_email="news@shop.example", to=to_me, list_unsubscribe="<mailto:u>", list_id="news.shop")
    a = importance.prior(friend, _sig(friend, sent=6, received=10))
    b = importance.prior(stranger, _sig(stranger))
    c = importance.prior(news, _sig(news))
    assert a[0] > b[0] > c[0]
    assert a[0] >= importance.THRESHOLD > c[0]
    assert any("Alice" in r for r in a[1])
    assert any("Mailing list" in r for r in c[1])


def test_smart_heuristics_and_deadlines():
    to_me = [{"email": "me@example.com", "name": ""}]
    m = _msg(from_email="alice@example.org", to=to_me, date=datetime(2026, 9, 21, 10, tzinfo=UTC),
             body_text="Could you send me the slides by Friday? Thanks!")
    h = classify.heuristic_smart(m, _sig(m, received=3))
    assert h["needs_reply"] and h["deadline"] and h["deadline_date"] == "2026-09-25"
    assert classify.parse_deadline("October 3", datetime(2026, 9, 21, tzinfo=UTC)) == "2026-10-03"
    mine = _msg(from_email="me@example.com", sent_by_me=True, body_text="Any update on the contract?")
    assert classify.heuristic_smart(mine, _sig(mine))["waiting_on"]


async def test_local_only_guard_never_sends(monkeypatch, user):
    calls = []

    async def fail_post(self, url, *a, **kw):
        calls.append(url)
        raise AssertionError("no request may be made")

    monkeypatch.setattr(httpx.AsyncClient, "post", fail_post)
    name = f"hosted-{uuid.uuid4().hex[:6]}"
    async with endpoints(dict(name=name, base_url="https://api.hosted.example/v1", locality="hosted",
                              defaults={"chat": "big", "classify": "big"})):
        async with sessionmaker()() as db:
            u = await db.get(User, user.id)
            with pytest.raises(llm.LocalOnlyViolation):
                await llm.chat(db, u, [{"role": "user", "content": "secret student record"}], sensitive=True)
            # Non-sensitive content may use it (request attempted → our stub raises).
            with pytest.raises(AssertionError):
                await llm.chat(db, u, [{"role": "user", "content": "hi"}], sensitive=False)
    assert calls == ["https://api.hosted.example/v1/chat/completions"]

    local = f"local-{uuid.uuid4().hex[:6]}"
    async with endpoints(dict(name=f"h2-{local}", base_url="https://api.hosted.example/v1", locality="hosted",
                              defaults={"chat": "big"}),
                         dict(name=local, base_url="http://gpu.lan/v1", locality="local", defaults={"chat": "small"})):
        async with sessionmaker()() as db:
            r = await llm.resolve(db, None, "chat", sensitive=True)
            assert r.endpoint.name == local and r.local


async def test_summary_local_only_and_mocked_llm(monkeypatch, client, user, raw):
    _, conv = await _ingest(user, raw("Grant report", msgid="<gr1@x.org>",
                                      body="Please send the final grant report by October 3."))
    await _ingest(user, raw("Re: Grant report", frm="Bob <bob@example.org>", irt="<gr1@x.org>",
                            date=mail_date(0, "11:00"), body="Seconding this."))
    seen = {}

    async def fake_post(self, url, *a, **kw):
        seen["url"], seen["body"] = url, kw.get("json")
        content = {"summary": ["Alice asks for the final grant report."],
                   "action_items": ["Send the grant report"], "dates": [{"what": "report due", "when": "October 3"}]}
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": json.dumps(content)},
                                                      "finish_reason": "stop"}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    name = f"mock-{uuid.uuid4().hex[:6]}"
    async with endpoints(dict(name=name, base_url="http://mock.local/v1", locality="local", defaults={"chat": "m"})):
        r = (await client.get(f"/api/v1/conversations/{conv}/summary", params={"wait": "true"})).json()
    assert r["status"] == "ready" and r["available"] and r["action_items"] == ["Send the grant report"]
    msgs = seen["body"]["messages"]
    assert "<conversation>" in msgs[1]["content"] and "untrusted" in msgs[0]["content"]
    # Cached: a second call makes no request.
    seen.clear()
    r2 = (await client.get(f"/api/v1/conversations/{conv}/summary")).json()
    assert r2["summary"] == r["summary"] and not seen


async def test_pipeline_decisions_smart_and_semantic(client, user, raw):
    to = user.email
    mid1, conv1 = await _ingest(user, raw("Slides for Monday", frm="Alice <alice@example.org>", to=to,
                                          date=_now_hdr(), body="Could you send me the slides by Friday?"))
    mid2, conv2 = await _ingest(user, raw("Weekly deals: 50% off shoes", frm="Deals <news@shop.example>", to=to,
                                          date=_now_hdr(), extra="List-Unsubscribe: <mailto:u@shop.example>",
                                          body="Huge sale this week. Shop now, limited time offer."))
    mid3, conv3 = await _ingest(user, raw("Budget", frm="Carol <carol@example.org>", to=to, date=_now_hdr(),
                                          body="Our profit projections for the next quarter look strong."))
    m1, m2, _ = await _process(mid1), await _process(mid2), await _process(mid3)
    assert m1.intel_state == "done" and m1.embedding is not None
    assert m2.category == "promotions" and not m2.important
    d = (await client.get(f"/api/v1/messages/{mid1}/decisions")).json()
    kinds = {x["kind"] for x in d["decisions"]}
    assert {"category", "importance"} <= kinds
    assert {s["target"] for s in d["smart"]} >= {"needs_reply", "deadline"}
    sm = (await client.get("/api/v1/smart/needs_reply")).json()
    assert conv1 in [c["id"] for c in sm["items"]]
    s = (await client.get("/api/v1/search/semantic", params={"q": "revenue forecast"})).json()
    assert s["items"] and s["items"][0]["id"] == conv3


async def test_label_classifier_suggests(user, raw, monkeypatch):
    to = user.email
    topics = ["Invoice INV-{i} from Acme Hosting: amount due $4{i}.00 for server hosting this month",
              "Receipt for your payment to Acme Hosting, invoice {i}, paid by card"]
    for i in range(14):
        body = topics[i % 2].format(i=i)
        mid, _ = await _ingest(user, raw(f"Acme invoice {i}", frm="billing@acme.example", to=to, body=body),
                               label_names=["Receipts"], roles=[])
        await _process(mid)
    others = ["Want to grab lunch next week at the new Thai place?", "The hiking trip photos are up on the shared album",
              "Reading group discusses chapter four on Tuesday", "Can you pick up the kids after soccer practice?",
              "Draft of the conference paper introduction is attached for comments",
              "The garden tomatoes are finally ripe, come get some"]
    for i in range(18):
        mid, _ = await _ingest(user, raw(f"Note {i}", frm=f"friend{i % 6}@example.org", to=to, body=others[i % 6]))
        await _process(mid)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        from mailr.models import Label
        lb = (await db.execute(select(Label).where(Label.user_id == u.id, Label.name == "Receipts"))).scalar_one()
        # The fit runs in a worker thread, not on the event loop.
        import threading

        from mailr.intel import learn
        threads, real = [], learn.train_binary
        monkeypatch.setattr(learn, "train_binary", lambda *a: threads.append(threading.current_thread()) or real(*a))
        metrics = await classify.retrain_label(db, u, lb.id)
        await db.commit()
    assert threads and threads[0] is not threading.main_thread()
    assert metrics and metrics["positives"] >= 14
    mid, _ = await _ingest(user, raw("Acme hosting invoice", frm="billing@acme.example", to=to,
                                     body="Invoice INV-99 from Acme Hosting: amount due $51.00 for server hosting"))
    await _process(mid)
    async with sessionmaker()() as db:
        from mailr.models import LabelDecision
        sugg = (await db.execute(select(LabelDecision).where(LabelDecision.message_id == mid,
                                                             LabelDecision.kind == "label"))).scalars().all()
    assert sugg and sugg[0].target == str(lb.id) and sugg[0].source == "classifier"


async def test_spam_train_sets_keyword(user, raw):
    mid, _ = await _ingest(user, raw("Win a prize", frm="scam@spam.example"))
    from mailr.intel import spam
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        await spam.train(db, m, True)
        u = await db.get(User, user.id)
        store = await user_store(db, u)
        got = await store.jmap.one("Email/get", {"ids": [m.jmap_id], "properties": ["keywords"]})
    assert got["list"][0]["keywords"].get("$junk") is True
    assert spam.envelope_from_received(
        b"Received: from mail-sor-f41.google.com (mail-sor-f41.google.com [209.85.220.41])\r\n"
        b" by mx.mailhost.example with ESMTPS; Mon, 21 Sep 2026\r\nSubject: x\r\n\r\nb") == \
        {"remoteIp": "209.85.220.41", "ehloDomain": "mail-sor-f41.google.com", "isTls": True}


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("MAILR_LIVE") != "1", reason="set MAILR_LIVE=1 to call the local LLM")
async def test_live_local_llm_smart(user, raw):
    name = f"live-{uuid.uuid4().hex[:6]}"
    async with endpoints(dict(name=name, base_url=os.environ.get("MAILR_LIVE_URL", "http://llm-host:8000/v1"),
                              locality="local", defaults={"chat": "qwen3.8-27b", "classify": "qwen3.8-27b"})):
        mid, _ = await _ingest(user, raw("Contract", frm="Dana <dana@example.org>", to=user.email, date=_now_hdr(),
                                         body="Could you sign and return the contract before October 10?"))
        m = await _process(mid, allow_llm=True)
        async with sessionmaker()() as db:
            from mailr.models import LabelDecision
            ds = (await db.execute(select(LabelDecision).where(LabelDecision.message_id == m.id,
                                                               LabelDecision.kind == "smart"))).scalars().all()
        assert any(d.target == "needs_reply" and d.source == "llm" for d in ds)


async def test_backfill_classifies_in_small_commits(user, raw, monkeypatch):
    """The backfill embeds the batch first, then classifies it in commits of BACKFILL_CHUNK
    messages, so row locks are held for a chunk, not for the whole batch."""
    from mailr.intel import tasks as intel_tasks

    ids = []
    for i in range(5):
        mid, _ = await _ingest(user, raw(f"Backfill {i}", msgid=f"<bf{i}-{uuid.uuid4().hex[:6]}@x.org>"))
        ids.append(mid)
    async with sessionmaker()() as db:
        from sqlalchemy import update
        await db.execute(update(Message).where(Message.id.in_(ids)).values(intel_state="backfill"))
        await db.commit()
    sessions: list = []  # the sessions themselves, so no id is reused
    real = intel_tasks.process_message

    async def spy(db, m, u, **kw):
        sessions.append(db)
        return await real(db, m, u, **kw)

    monkeypatch.setattr(intel_tasks, "BACKFILL_CHUNK", 2)
    monkeypatch.setattr(intel_tasks, "process_message", spy)
    with pytest.raises(Retry):  # the job runs again for the next batch
        await intel_tasks.intel_backfill({"user_id": user.id}, None)
    assert len(sessions) == 5 and len({id(s) for s in sessions}) == 3
    async with sessionmaker()() as db:
        states = set((await db.execute(select(Message.intel_state).where(Message.id.in_(ids)))).scalars())
    assert "backfill" not in states


async def test_a_history_import_is_triaged_in_batches(user, raw, monkeypatch):
    """An import queues one intel.backfill job for the user, not a job per message in the main
    lane; the backfill embeds a whole batch in one call, newest first, and ends when nothing is
    left. Live mail keeps its own intel.message job."""
    import embedfake
    from sqlalchemy import func, text

    from mailr.intel import tasks as intel_tasks
    from mailr.models import Job

    ids = []
    for i in range(6):
        mid, _ = await _ingest(user, raw(f"Old invoice {i}", msgid=f"<imp{i}-{uuid.uuid4().hex[:6]}@x.org>",
                                         date=f"Mon, {10 + i} Mar 2025 09:00:00 +0000"),
                               roles=["archive"], source="import", notify=False, run_rules=False)
        ids.append(mid)
    live, _ = await _ingest(user, raw("Today", msgid=f"<live-{uuid.uuid4().hex[:6]}@x.org>", date=_now_hdr()))
    async with sessionmaker()() as db:
        states = dict((await db.execute(select(Message.id, Message.intel_state).where(
            Message.id.in_([*ids, live])))).all())
        kinds = dict((await db.execute(select(Job.kind, func.count()).where(
            Job.user_id == user.id, Job.status == "queued",
            Job.kind.in_(("intel.message", "intel.backfill"))).group_by(Job.kind))).all())
        single = (await db.execute(select(func.count()).select_from(Job).where(
            Job.kind == "intel.message", Job.payload["message_id"].as_integer().in_(ids)))).scalar_one()
        # The backfill can read the partial index of the mail still to triage (sequential scans
        # off: this test database is too small for the planner's own choice to say anything).
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        await db.execute(text("SET LOCAL enable_bitmapscan = off"))
        stmt = intel_tasks.backfill_todo(user.id).order_by(Message.date.desc().nulls_last()).limit(128)
        sql = str(stmt.compile(dialect=db.bind.dialect, compile_kwargs={"literal_binds": True}))
        plan = "\n".join(r[0] for r in (await db.execute(text("EXPLAIN " + sql))).all())
    assert {states[i] for i in ids} == {"backfill"} and states[live] == "pending"
    assert kinds == {"intel.backfill": 1, "intel.message": 1} and single == 0
    assert "ix_msg_intel_todo" in plan, plan

    monkeypatch.setattr(intel_tasks, "BACKFILL_BATCH", 4)
    seen: list[int] = []
    real = intel_tasks.process_message

    async def spy(db, m, u, **kw):
        seen.append(m.id)
        return await real(db, m, u, **kw)

    monkeypatch.setattr(intel_tasks, "process_message", spy)
    embedfake.CALLS.clear()
    with pytest.raises(Retry):
        await intel_tasks.intel_backfill({"user_id": user.id}, None)
    assert seen == ids[::-1][:4] and len(embedfake.CALLS) == 1  # newest first, one embedding call
    with pytest.raises(Retry):
        await intel_tasks.intel_backfill({"user_id": user.id}, None)
    assert sorted(seen) == sorted(ids) and len(embedfake.CALLS) == 2
    await intel_tasks.intel_backfill({"user_id": user.id}, None)  # nothing left: the job ends
    assert live not in seen  # live mail waits for its own job


async def test_the_backfill_leaves_mail_whose_own_job_is_queued(user, raw, monkeypatch):
    from mailr.intel import tasks as intel_tasks
    from mailr.models import Job

    mid, _ = await _ingest(user, raw("Queued", msgid=f"<q-{uuid.uuid4().hex[:6]}@x.org>"))
    monkeypatch.setattr(intel_tasks, "BACKFILL_PENDING_AFTER", timedelta(0))
    async with sessionmaker()() as db:
        todo = set((await db.execute(intel_tasks.backfill_todo(user.id).with_only_columns(Message.id))).scalars())
        assert mid not in todo  # its intel.message job is queued
        await db.execute(delete(Job).where(Job.dedupe_key == f"intel.message:{mid}"))
        await db.commit()
        todo = set((await db.execute(intel_tasks.backfill_todo(user.id).with_only_columns(Message.id))).scalars())
    assert mid in todo  # the job is gone (it failed): the backfill takes the message
