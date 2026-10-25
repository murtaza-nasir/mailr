# ACT-SURFACES: api
"""Importance from the user's own judgment: training leaves out the
provider's verdicts, and the review queue (ACT mail.review_importance) records the user's
choices and schedules a retrain."""

import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from helpers.public_ids import pub  # noqa: F401
from sqlalchemy import select, text, update

from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.store import user_store
from mailr.db import sessionmaker
from mailr.intel import importance
from mailr.models import EMBED_DIM, ClassifierModel, Job, LabelDecision, Message, User


def _now_hdr(delta=timedelta()):
    return (datetime.now(UTC) - delta).strftime("%a, %d %b %Y %H:%M:%S +0000")


async def _ingest(user, data, **kw):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        store = await user_store(db, u)
        res = await ingest_raw(db, store, data, IngestOptions(**kw))
        await db.commit()
        return res.message.id


async def _decide(uid, mid, source, value, confidence=1.0, reason=""):
    async with sessionmaker()() as db:
        db.add(LabelDecision(user_id=uid, message_id=mid, kind="importance", target="important", value=value,
                             source=source, confidence=confidence, reason=reason))
        await db.commit()


async def _set(mid, **cols):
    async with sessionmaker()() as db:
        await db.execute(update(Message).where(Message.id == mid).values(**cols))
        await db.commit()


async def test_training_uses_own_choices_and_behaviour_not_provider(user, raw):
    to = user.email
    starred, archived, provider, mine = [], [], [], []
    for i in range(22):
        mid = await _ingest(user, raw(f"Starred {i}", frm=f"friend{i}@example.org", to=to, body=f"Plans {i}"))
        await _set(mid, flagged=True, seen=True)
        starred.append(mid)
    for i in range(22):
        # Archived (no Inbox label), unread and older than three days.
        mid = await _ingest(user, raw(f"Archived {i}", frm=f"news{i}@shop.example", to=to, body=f"Sale {i}"), roles=[])
        archived.append(mid)
    for i in range(6):
        mid = await _ingest(user, raw(f"Gmail said {i}", frm=f"g{i}@example.org", to=to, body=f"Gmail {i}"))
        await _set(mid, seen=True)
        await _decide(user.id, mid, "provider", True)
        provider.append(mid)
    for i in range(3):
        mid = await _ingest(user, raw(f"Mine {i}", frm=f"m{i}@example.org", to=to, body=f"Mine {i}"))
        await _set(mid, seen=True)
        await _decide(user.id, mid, "user", True)
        mine.append(mid)
    # The provider called this one not important; the user's own toggle says important.
    mid = await _ingest(user, raw("Both", frm="both@example.org", to=to, body="Both"))
    await _set(mid, seen=True)
    await _decide(user.id, mid, "provider", False)
    await _decide(user.id, mid, "user", True)
    mine.append(mid)

    async with sessionmaker()() as db:
        pos, neg, explicit = await importance.training_examples(db, user.id)
    assert not set(provider) & (set(pos) | set(neg)), "provider verdicts must not be training data"
    assert set(mine) <= set(pos) and explicit == set(mine)
    assert set(starred) <= set(pos), "replies and stars stay positive examples"
    assert set(archived) <= set(neg), "mail archived unread stays a negative example"

    rng = random.Random(3)
    async with sessionmaker()() as db:
        for m in starred + archived + provider + mine:
            await db.execute(update(Message).where(Message.id == m).values(
                embedding=[rng.random() for _ in range(EMBED_DIM)]))
        await db.commit()
    async with sessionmaker()() as db:
        metrics = await importance.retrain(db, user.id)
        await db.commit()
    assert metrics is not None
    assert metrics["version"] == importance.VERSION
    assert metrics["sources"] == {"user": 4, "replied_or_starred": 22, "archived_unread": 22, "provider_excluded": 7}


async def test_a_provider_trained_model_goes_when_there_is_too_little_data(user):
    from mailr.intel import learn

    async with sessionmaker()() as db:
        db.add(ClassifierModel(user_id=user.id, target="importance", blob=learn.dumps(None), metrics={"n": 40},
                               auto_apply=False, trained_at=datetime.now(UTC)))
        await db.commit()
    async with sessionmaker()() as db:
        assert await importance.retrain(db, user.id) is None
        await db.commit()
    async with sessionmaker()() as db:
        assert (await db.execute(select(ClassifierModel).where(
            ClassifierModel.user_id == user.id, ClassifierModel.target == "importance"))).first() is None


async def _review_set(user, raw):
    to, now = user.email, _now_hdr()
    ids = {}
    for name in ("judged", "border", "low", "provider", "rule", "mine", "old"):
        ids[name] = await _ingest(user, raw(f"Review {name}", frm=f"{name}@example.org", to=to, date=now,
                                            body=f"About {name}"))
    await _decide(user.id, ids["judged"], "classifier", True, 0.72, "You often write to Judged; Sent directly to you")
    await _decide(user.id, ids["border"], "heuristic", False, 0.40, "Addressed to you; Sent to many people")
    await _decide(user.id, ids["low"], "heuristic", False, 0.10, "Mailing list")
    await _decide(user.id, ids["provider"], "provider", True)
    await _decide(user.id, ids["rule"], "rule", True)
    await _decide(user.id, ids["mine"], "heuristic", True, 0.6)
    await _decide(user.id, ids["mine"], "user", False)
    await _decide(user.id, ids["old"], "heuristic", True, 0.8)
    await _set(ids["old"], date=datetime.now(UTC) - timedelta(days=5), received_at=datetime.now(UTC) - timedelta(days=5))
    await _set(ids["judged"], important=True)
    return ids


async def test_review_lists_judged_and_borderline_with_reasons(client, user, raw):
    ids = await _review_set(user, raw)
    r = await client.get("/api/v1/intel/importance/review")
    assert r.status_code == 200, r.text
    out = r.json()
    got = {it["message_id"]: it for it in out["items"]}
    assert set(got) == {ids["judged"], ids["border"]}, "judged + borderline only; provider, rule, user and low left out"
    j, b = got[ids["judged"]], got[ids["border"]]
    assert j["judged_important"] and not j["borderline"] and j["score"] == 0.72
    assert j["reasons"] == ["You often write to Judged", "Sent directly to you"]
    assert j["subject"] == "Review judged" and j["from"]["email"] == "judged@example.org" and "About judged" in j["snippet"]
    assert not b["judged_important"] and b["borderline"]
    assert out["status"]["learned_from"] == 1 and out["status"]["model"] is None
    # A wider range brings back the five-day-old one.
    since = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    week = (await client.get("/api/v1/intel/importance/review", params={"since": since})).json()
    assert ids["old"] in {it["message_id"] for it in week["items"]}


async def test_review_choice_records_user_decision_flag_and_retrain(client, user, raw):
    ids = await _review_set(user, raw)
    r = await client.post(f"/api/v1/messages/{ids['border']}/importance", json={"important": True})
    assert r.status_code == 200, r.text
    assert r.json()["status"]["learned_from"] == 2
    async with sessionmaker()() as db:
        m = await db.get(Message, ids["border"])
        assert m.important and m.importance_score == 1.0
        d = (await db.execute(select(LabelDecision).where(
            LabelDecision.message_id == ids["border"], LabelDecision.source == "user"))).scalar_one()
        assert d.kind == "importance" and d.value is True
        job = (await db.execute(select(Job).where(
            Job.dedupe_key == f"intel.retrain:{user.id}:importance:important", Job.status == "queued"))).scalar_one()
        assert job.kind == "intel.retrain"
    # Not important, on the one the model called important; the flag follows.
    r = await client.post(f"/api/v1/messages/{ids['judged']}/importance", json={"important": False})
    assert r.status_code == 200
    async with sessionmaker()() as db:
        assert not (await db.get(Message, ids["judged"])).important
    # Both drop out of the queue.
    left = {it["message_id"] for it in (await client.get("/api/v1/intel/importance/review")).json()["items"]}
    assert not left & {ids["judged"], ids["border"]}


async def test_a_batch_of_choices_retrains_inside_the_debounce(client, user, raw):
    from mailr.core.jobs import Retry
    from mailr.intel import learn
    from mailr.intel.tasks import intel_retrain

    async with sessionmaker()() as db:
        db.add(ClassifierModel(user_id=user.id, target="importance", blob=learn.dumps(None),
                               metrics={"n": 40, "version": importance.VERSION}, auto_apply=False,
                               trained_at=datetime.now(UTC)))
        await db.commit()
    ctx = SimpleNamespace(user_id=user.id)
    with pytest.raises(Retry):  # trained a moment ago, no new choices: wait for the debounce
        await intel_retrain({"kind": "importance", "target": "important"}, ctx)
    mids = [await _ingest(user, raw(f"Batch {i}", frm=f"b{i}@example.org", to=user.email, date=_now_hdr(),
                                    body=f"Batch {i}")) for i in range(importance.RETRAIN_AFTER)]
    key = f"intel.retrain:{user.id}:importance:important"
    for i, mid in enumerate(mids):
        await _decide(user.id, mid, "heuristic", True, 0.6)
        r = await client.post(f"/api/v1/messages/{mid}/importance", json={"important": i % 2 == 0})
        assert r.status_code == 200, r.text
        if i == 0:  # the queued retrain waits out its debounce
            async with sessionmaker()() as db:
                await db.execute(text("UPDATE jobs SET run_at = now() + interval '2 minutes' WHERE dedupe_key = :k "
                                      "AND status = 'queued'"), {"k": key})
                await db.commit()
    assert r.json()["status"]["since_training"] == importance.RETRAIN_AFTER
    async with sessionmaker()() as db:
        job = (await db.execute(select(Job).where(Job.dedupe_key == key, Job.status == "queued"))).scalar_one()
        assert job.run_at <= datetime.now(UTC), "the tenth choice brings the retrain forward"
    # The handler runs it now, inside the debounce (too little data here, so no new model).
    await intel_retrain({"kind": "importance", "target": "important"}, ctx)


async def test_review_speaks_public_ids(pub, client, user, raw):
    ids = await _review_set(user, raw)
    out = (await client.get("/api/v1/intel/importance/review")).json()
    pm = pub.encode("message", ids["border"])
    assert pm in {it["message_id"] for it in out["items"]}
    assert all(it["message_id"] >= 2**52 and it["conversation_id"] >= 2**52 for it in out["items"])
    assert (await client.post(f"/api/v1/messages/{ids['border']}/importance", json={"important": True})).status_code == 404
    r = await client.post(f"/api/v1/messages/{pm}/importance", json={"important": True})
    assert r.status_code == 200 and r.json()["message_id"] == pm


async def test_review_is_for_the_signed_in_user(client, user, raw):
    from httpx import AsyncClient

    tok = (await client.post("/api/v1/tokens", json={"name": "agent", "scopes": ["read", "label"]})).json()["token"]
    ids = await _review_set(user, raw)
    async with AsyncClient(transport=client._transport, base_url="http://test",
                           headers={"Authorization": f"Bearer {tok}"}) as a:
        assert (await a.get("/api/v1/intel/importance/review")).status_code == 403
        assert (await a.post(f"/api/v1/messages/{ids['border']}/importance", json={"important": True})).status_code == 403
