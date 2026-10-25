"""Label classifier guards (29 Sep 2026: the classifier stamped Job Applications, eBay, Ultibaat
and Call log together on ~33k old SMS Backup+ texts and call logs from the Gmail mirror).

Auto-apply needs held-out precision and enough hand-given examples, classifier-applied labels are
never training data, one message gets at most a few labels automatically, phone imports are only
labelled when the user labelled such messages by hand, and history imports get suggestions only.

Recency (2 Oct 2026, the Ultibaat case: a label last used for 2011–2017 Twitter notifications
auto-applied to a Reddit reply notification): with the `label_recency` setting on, examples
fade with a half-life and the auto-apply minimum counts the effective (decayed) total. The
user's own corrections weigh 10, a filter's 2, an imported label 1."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from embedfake import embedding_endpoint
from sklearn.linear_model import LogisticRegression
from sqlalchemy import select

from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.store import user_store
from mailr.db import sessionmaker
from mailr.intel import classify, features, learn
from mailr.models import EMBED_DIM, ClassifierModel, Job, Label, LabelDecision, Message, User


@pytest.fixture(autouse=True)
async def _embeddings(monkeypatch, app):
    async with embedding_endpoint(monkeypatch):
        yield


@pytest.fixture(autouse=True)
def _any_sender(monkeypatch):
    """These tests are about the model's own bars; the sender check (a label applies itself only
    to mail from a sender or list among its examples) has its tests in test_spam_labels.py."""
    async def familiar(db, user_id, label_id, m):
        return True

    monkeypatch.setattr(classify, "familiar_sender", familiar)


def _now_hdr():
    return datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")


async def _ingest(user, data, **kw):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        store = await user_store(db, u)
        res = await ingest_raw(db, store, data, IngestOptions(**kw))
        await db.commit()
        return res.message.id


async def _process(mid):
    from mailr.intel.tasks import process_message

    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        u = await db.get(User, m.user_id)
        await process_message(db, m, u, allow_llm=False)
        await db.commit()


async def _labels(user, *names):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        store = await user_store(db, u)
        out = []
        for n in names:
            out.append((await store.create_label(db, n)).id)
        await db.commit()
        return out


def _always(p: float) -> LogisticRegression:
    """A real (picklable) model that answers p for every message."""
    width = EMBED_DIM + len(features.FEATURES)
    m = LogisticRegression().fit(np.vstack([np.zeros(width), np.ones(width)]), [0, 1])
    m.coef_ = np.zeros_like(m.coef_)
    m.intercept_ = np.asarray([np.log(p / (1 - p))])
    return m


GOOD = {"version": classify.LABEL_MODEL_VERSION, "precision": 1.0, "positives": 60, "hand_positives": 60,
        "effective_positives": 60.0, "recency": {"enabled": True, "half_life_months": 12}, "held_out_positives": 15,
        "newest_positive": datetime.now(UTC).isoformat()}


async def _model(user, lid, p=0.95, auto=True, **metrics):
    async with sessionmaker()() as db:
        t = learn.Trained(_always(p), {**GOOD, **metrics})
        await learn.save(db, user.id, f"label:{lid}", t, auto_apply=auto)
        await db.commit()


async def _predict(user, mid, **kw):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        m = await db.get(Message, mid)
        s = await features.compute(db, m, None)
        return {d["label_id"]: d["auto"] for d in await classify.predict_labels(db, u, m, s, **kw)}


async def _settings(user, **kw):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.settings = {**(u.settings or {}), **kw}
        await db.commit()


async def test_auto_apply_needs_hand_examples_held_out_and_current_version(user, raw):
    a, b, c, d = await _labels(user, "Alpha", "Beta", "Gamma", "Delta")
    mid = await _ingest(user, raw("Quarterly invoice", frm="billing@acme.example", to=user.email, date=_now_hdr(),
                                  body="Invoice for hosting services this quarter"))
    await _process(mid)
    await _model(user, a)                                    # meets every bar
    await _model(user, b, hand_positives=12, positives=12, effective_positives=12.0)   # too few hand-given examples
    await _model(user, c, held_out_positives=3)              # held-out precision on too few positives
    await _model(user, d, version=None)                      # trained under the old rules
    got = await _predict(user, mid)
    assert got == {a: True, b: False, c: False, d: False}
    # A model that is not sure enough suggests only, and so does every model when auto is off.
    assert await _predict(user, mid, allow_auto=False) == {a: False, b: False, c: False, d: False}


async def test_cap_on_automatic_labels_per_message(user, raw):
    ids = await _labels(user, "Jobs", "eBay stuff", "Ultibaat", "Calls")
    mid = await _ingest(user, raw("Hello", frm="someone@example.org", to=user.email, date=_now_hdr(),
                                  body="Plain message about nothing in particular"))
    await _process(mid)
    for lid in ids[:2]:
        await _model(user, lid)
    assert set((await _predict(user, mid)).values()) == {True}   # two qualify: both apply (cap 2)
    for lid in ids[2:]:
        await _model(user, lid)
    # Four unrelated models all sure of one message: nothing is applied, all are suggestions.
    got = await _predict(user, mid)
    assert len(got) == 4 and not any(got.values())
    await _settings(user, auto_label_max_per_message=4)
    assert all((await _predict(user, mid)).values())


async def test_phone_imports_only_labelled_after_hand_labelling(user, raw):
    (lid,) = await _labels(user, "Family")
    sms = await _ingest(user, raw("SMS with +15551234", frm="+15551234 <15551234@unknown.email>", to=user.email,
                                  msgid="<0f3a9c1e2b@sms-backup-plus.local>", date=_now_hdr(), body="See you at six"))
    await _process(sms)
    async with sessionmaker()() as db:
        assert classify.is_phone_import(await db.get(Message, sms))
    await _model(user, lid)
    assert await _predict(user, sms) == {}                     # no suggestion, no auto-label
    await _model(user, lid, phone_by_hand=3)
    assert await _predict(user, sms) == {lid: True}
    mail = await _ingest(user, raw("Dinner", frm="sis@example.org", to=user.email, date=_now_hdr(), body="Dinner?"))
    async with sessionmaker()() as db:
        assert not classify.is_phone_import(await db.get(Message, mail))


async def test_history_mail_gets_suggestions_only(user, raw):
    (lid,) = await _labels(user, "Receipts")
    await _model(user, lid)
    mid = await _ingest(user, raw("Old receipt", frm="shop@example.org", to=user.email,
                                  date="Tue, 14 Apr 2015 10:00:00 +0000", body="Your receipt from 2015"),
                        received_at=datetime(2015, 4, 14, 10, tzinfo=UTC))
    await _process(mid)
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        dec = (await db.execute(select(LabelDecision).where(LabelDecision.message_id == mid,
                                                            LabelDecision.kind == "label"))).scalars().all()
    assert lid not in (m.label_ids or [])
    assert [(x.target, x.status) for x in dec] == [(str(lid), "suggested")]


async def test_retrain_ignores_classifier_applied_labels(user, raw):
    to = user.email
    for i in range(12):
        mid = await _ingest(user, raw(f"Acme invoice {i}", frm="billing@acme.example", to=to,
                                      body=f"Invoice INV-{i} from Acme Hosting: amount due for server hosting"),
                            label_names=["Receipts"], roles=[])
        await _process(mid)
    for i in range(16):
        mid = await _ingest(user, raw(f"Note {i}", frm=f"friend{i % 4}@example.org", to=to,
                                      body=["Lunch next week at the Thai place?", "Hiking photos are online",
                                            "Reading group meets Tuesday", "Pick up the kids after practice"][i % 4]))
        await _process(mid)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        lb = (await db.execute(select(Label).where(Label.user_id == u.id, Label.name == "Receipts"))).scalar_one()
    # The classifier stamped Receipts on ten texts; one of them the user then confirmed by hand.
    machine = []
    for i in range(10):
        mid = await _ingest(user, raw(f"SMS {i}", frm=f"{i}@unknown.email", to=to,
                                      msgid=f"<sms{i}x{lb.id}@sms-backup-plus.local>", body=f"ok see you {i}"))
        await _process(mid)
        machine.append(mid)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        store = await user_store(db, u)
        msgs = list((await db.execute(select(Message).where(Message.id.in_(machine)))).scalars())
        await store.set_labels(msgs, add=[lb.id], remove=[], db=db)
        for m in msgs:
            db.add(LabelDecision(user_id=u.id, message_id=m.id, kind="label", target=str(lb.id), value=True,
                                 source="classifier", confidence=0.9, reason="test", status="applied"))
        db.add(LabelDecision(user_id=u.id, message_id=machine[0], kind="label", target=str(lb.id), value=True,
                             source="user", confidence=1.0, reason="user action", status="accepted"))
        await db.commit()
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        metrics = await classify.retrain_label(db, u, lb.id)
        await db.commit()
        row = (await db.execute(select(ClassifierModel).where(
            ClassifierModel.user_id == u.id, ClassifierModel.target == f"label:{lb.id}"))).scalar_one()
    assert metrics["hand_positives"] == 13            # 12 invoices + the text the user confirmed
    assert metrics["excluded_classifier"] == 9
    assert metrics["phone_by_hand"] == 1
    assert metrics["version"] == classify.LABEL_MODEL_VERSION
    assert row.auto_apply is False                    # 13 hand-given examples are below 30


async def test_retrain_drops_model_without_hand_examples(user, raw):
    (lid,) = await _labels(user, "Ultibaat")
    await _model(user, lid)
    mids = []
    for i in range(8):
        mid = await _ingest(user, raw(f"Text {i}", frm=f"{i}@unknown.email", to=user.email, body=f"call me {i}"))
        await _process(mid)
        mids.append(mid)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        store = await user_store(db, u)
        msgs = list((await db.execute(select(Message).where(Message.id.in_(mids)))).scalars())
        await store.set_labels(msgs, add=[lid], remove=[], db=db)
        for m in msgs:
            db.add(LabelDecision(user_id=u.id, message_id=m.id, kind="label", target=str(lid), value=True,
                                 source="classifier", confidence=0.9, reason="test", status="applied"))
        await db.commit()
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        assert await classify.retrain_label(db, u, lid) is None
        await db.commit()
        left = (await db.execute(select(ClassifierModel).where(
            ClassifierModel.user_id == u.id, ClassifierModel.target == f"label:{lid}"))).scalar_one_or_none()
    assert left is None


async def test_deleted_label_model_is_ignored(user, raw):
    (lid,) = await _labels(user, "Gone")
    mid = await _ingest(user, raw("Hi", frm="x@example.org", to=user.email, date=_now_hdr(), body="hello friend"))
    await _process(mid)
    await _model(user, lid + 100000)                  # a model whose label no longer exists
    assert await _predict(user, mid) == {}


# ---------------------------------------------------------------- recency


def _hdr(when: datetime) -> str:
    return when.strftime("%a, %d %b %Y %H:%M:%S +0000")


TWEET = ["{} replied to your Tweet about the weekend concert", "{} replied to your Tweet: great photos",
         "{} mentioned you in a Tweet about football", "{} replied to your Tweet on the election"]
INVOICE = ["Invoice INV-{} from Acme Hosting: amount payable for server hosting",
           "Acme Hosting invoice {}: payment received for server hosting, thanks",
           "Your Acme Hosting invoice {} is ready: server hosting charges"]
NOTE = ["Lunch next week at the Thai place?", "Hiking photos are online", "Reading group meets Tuesday",
        "Pick up the kids after practice", "Garden planting schedule for spring"]


async def _bulk(user, n, subject, body, label, when):
    """n processed messages; `label` is given as the provider gave it (no decision)."""
    out = []
    for i in range(n):
        at = when(i)
        kw = {"label_names": [label], "roles": []} if label else {}
        mid = await _ingest(user, raw_msg(subject.format(i), body[i % len(body)].format(f"user{i}"), at),
                            received_at=at, **kw)
        await _process(mid)
        out.append(mid)
    return out


def raw_msg(subject, body, at):
    import uuid

    return ("\r\n".join([f"From: Sender {subject[:3]} <s{uuid.uuid4().hex[:6]}@example.org>", "To: me@example.com",
                          f"Subject: {subject}", f"Message-ID: <{uuid.uuid4().hex}@example.org>", f"Date: {_hdr(at)}",
                          "MIME-Version: 1.0", "Content-Type: text/plain; charset=utf-8"])
            + "\r\n\r\n" + body + "\r\n").encode()


async def _prob(user, lid, mid) -> float:
    """The label model's probability for one message."""
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        s = await features.compute(db, m, None)
        got = await learn.load(db, user.id, f"label:{lid}")
        return float(got[0].predict_proba(learn.row_vector(m.embedding, features.learned_vector(s)).reshape(1, -1))[0][1])


async def _label_id(user, name):
    async with sessionmaker()() as db:
        return (await db.execute(select(Label.id).where(Label.user_id == user.id, Label.name == name))).scalar_one()


async def _retrain(user, lid):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        mt = await classify.retrain_label(db, u, lid)
        await db.commit()
        row = (await db.execute(select(ClassifierModel).where(
            ClassifierModel.user_id == u.id, ClassifierModel.target == f"label:{lid}"))).scalar_one()
        return mt, row.auto_apply


def test_example_weights_and_decay():
    now = datetime.now(UTC)
    year = now - timedelta(days=365.25)
    w = classify.example_weight
    assert w("user", now, now, enabled=True, half_life_months=12) == pytest.approx(10.0)
    assert w("rule", now, now, enabled=True, half_life_months=12) == pytest.approx(2.0)
    assert w(None, now, now, enabled=True, half_life_months=12) == pytest.approx(1.0)
    assert w(None, year, now, enabled=True, half_life_months=12) == pytest.approx(0.5)
    assert w("user", year, now, enabled=True, half_life_months=6) == pytest.approx(2.5)
    assert w(None, now - timedelta(days=365.25 * 10), now, enabled=True, half_life_months=12) < 0.001
    # Off: no decay, but the user's own choice still weighs 10.
    assert w("user", year, now, enabled=False, half_life_months=12) == 10.0
    assert w(None, year, now, enabled=False, half_life_months=12) == 1.0
    assert w(None, None, now, enabled=True, half_life_months=12) == 1.0
    assert classify.recency(None) == (True, 12)


async def test_label_recency_setting_validates_and_retrains(client, user):
    r = await client.get("/api/v1/me")
    assert r.json()["settings"]["label_recency"] == {"enabled": True, "half_life_months": 12}
    for bad in ({"enabled": "yes"}, {"half_life_months": 9}, {"half_life_months": True}, {"other": 1}, [1], True):
        r = await client.patch("/api/v1/me/settings", json={"label_recency": bad})
        assert r.status_code == 400, bad
    key = f"intel.train_all:{user.id}"
    async with sessionmaker()() as db:
        assert (await db.execute(select(Job).where(Job.dedupe_key == key, Job.status == "queued"))).first() is None
    r = await client.patch("/api/v1/me/settings", json={"label_recency": {"enabled": False}})
    assert r.status_code == 200, r.text
    assert r.json()["label_recency"] == {"enabled": False, "half_life_months": 12}
    async with sessionmaker()() as db:
        job = (await db.execute(select(Job).where(Job.dedupe_key == key, Job.status == "queued"))).scalar_one()
        assert job.kind == "intel.train_all"
        await db.delete(job)
        await db.commit()
    # The same value again changes nothing and queues nothing.
    r = await client.patch("/api/v1/me/settings", json={"label_recency": {"enabled": False, "half_life_months": 12}})
    assert r.status_code == 200
    async with sessionmaker()() as db:
        assert (await db.execute(select(Job).where(Job.dedupe_key == key, Job.status == "queued"))).first() is None


async def test_dormant_label_suggests_only_and_recent_label_auto_applies(user):
    now = datetime.now(UTC)
    old = datetime(2014, 3, 1, 12, tzinfo=UTC)
    await _bulk(user, 40, "Twitter notification {}", TWEET, "Ultibaat", lambda i: old + timedelta(days=30 * i))
    await _bulk(user, 40, "Acme invoice {}", INVOICE, "Receipts", lambda i: now - timedelta(days=2 + i))
    await _bulk(user, 45, "Note {}", NOTE, None, lambda i: now - timedelta(days=i * 20))
    ulti, rec = await _label_id(user, "Ultibaat"), await _label_id(user, "Receipts")

    # Decay on (the default): forty examples from 2014–2017 add up to almost nothing.
    mt, auto = await _retrain(user, ulti)
    assert mt["hand_positives"] == 40 and mt["precision"] >= 0.9 and mt["held_out_positives"] >= 8
    assert mt["effective_positives"] < 1 and mt["recency"] == {"enabled": True, "half_life_months": 12}
    assert mt["auto_apply_threshold"]["counts"] == "effective"
    assert mt["version"] == classify.LABEL_MODEL_VERSION == 3
    assert auto is False
    # A label in use reaches the minimum on its effective total.
    mt, auto = await _retrain(user, rec)
    assert 30 <= mt["effective_positives"] < 40 and auto is True

    # A new notification like the old ones (the Ultibaat case was a Reddit reply notification).
    noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
    noon -= timedelta(days=1) if noon > now else timedelta()
    reddit = await _ingest(user, raw_msg("Twitter notification 0", TWEET[0].format("user0"), noon), received_at=noon)
    await _process(reddit)
    async with sessionmaker()() as db:
        m = await db.get(Message, reddit)
        d = (await db.execute(select(LabelDecision).where(LabelDecision.message_id == reddit,
                                                          LabelDecision.kind == "label"))).scalars().all()
    assert ulti not in (m.label_ids or [])
    assert [(x.target, x.status) for x in d] == [(str(ulti), "suggested")]

    # Decay off: the raw count decides again, as before.
    await _settings(user, label_recency={"enabled": False, "half_life_months": 12})
    mt, auto = await _retrain(user, ulti)
    assert mt["auto_apply_threshold"]["counts"] == "raw" and auto is True
    assert mt["effective_positives"] < 1          # still stored, for a later switch back
    assert (await _predict(user, reddit)).get(ulti) is True
    # Back on: the stored model is checked on its effective total at once, before any retrain.
    await _settings(user, label_recency={"enabled": True, "half_life_months": 12})
    assert (await _predict(user, reddit)).get(ulti) is False


async def test_effective_total_fades_without_a_retrain(user, raw):
    (lid,) = await _labels(user, "Fading")
    mid = await _ingest(user, raw("Hello", frm="x@example.org", to=user.email, date=_now_hdr(), body="hello friend"))
    await _process(mid)
    await _model(user, lid, effective_positives=40.0)
    assert await _predict(user, mid) == {lid: True}
    async with sessionmaker()() as db:   # trained a year ago: 40 is now 20, below 30
        row = (await db.execute(select(ClassifierModel).where(ClassifierModel.user_id == user.id,
                                                              ClassifierModel.target == f"label:{lid}"))).scalar_one()
        row.trained_at = datetime.now(UTC) - timedelta(days=366)
        await db.commit()
    assert await _predict(user, mid) == {lid: False}
    await _settings(user, label_recency={"enabled": True, "half_life_months": 24})
    assert await _predict(user, mid) == {lid: False}  # the model's own half-life ages its total
    await _settings(user, label_recency={"enabled": False, "half_life_months": 12})
    assert await _predict(user, mid) == {lid: True}   # off: the raw 60 counts


async def test_old_examples_alone_never_auto_apply(user, raw):
    """The SMS case (2 Oct 2026): 49,023 examples from 2019 and before still add up to 80 after
    decay. With recency on, auto-apply also needs a positive younger than two half-lives."""
    (lid,) = await _labels(user, "SMS")
    mid = await _ingest(user, raw("Hello", frm="x@example.org", to=user.email, date=_now_hdr(), body="hello friend"))
    await _process(mid)
    now = datetime.now(UTC)
    await _model(user, lid, effective_positives=80.0, newest_positive=(now - timedelta(days=365 * 7)).isoformat())
    assert await _predict(user, mid) == {lid: False}
    await _model(user, lid, effective_positives=80.0, newest_positive=(now - timedelta(days=700)).isoformat())
    assert await _predict(user, mid) == {lid: True}       # under 24 months: still recent enough
    await _model(user, lid, effective_positives=80.0, newest_positive=(now - timedelta(days=740)).isoformat())
    assert await _predict(user, mid) == {lid: False}      # just over two half-lives
    await _model(user, lid, effective_positives=80.0, newest_positive=None)
    assert await _predict(user, mid) == {lid: False}      # trained before the rule: suggests until retrained
    # Decay off: the age of the examples does not matter, as before.
    await _settings(user, label_recency={"enabled": False, "half_life_months": 12})
    assert await _predict(user, mid) == {lid: True}


async def test_retrain_stores_the_newest_positive_and_checks_its_age(user):
    """Old examples that clear a low minimum on volume: no auto-apply with a 12-month half-life
    (newest 26 months old), auto-apply with 24 months (two half-lives are 48)."""
    now = datetime.now(UTC)
    start = now - timedelta(days=30 * 30)
    await _settings(user, auto_label_min_examples=5)
    await _bulk(user, 40, "Acme invoice {}", INVOICE, "Old receipts", lambda i: start + timedelta(days=3 * i))
    await _bulk(user, 45, "Note {}", NOTE, None, lambda i: now - timedelta(days=i * 20))
    lid = await _label_id(user, "Old receipts")
    newest = start + timedelta(days=3 * 39)
    mt, auto = await _retrain(user, lid)
    assert datetime.fromisoformat(mt["newest_positive"]) == pytest.approx(newest, abs=timedelta(seconds=1))
    assert mt["effective_positives"] >= 5 and mt["precision"] >= 0.9 and mt["held_out_positives"] >= 8
    assert mt["auto_apply_threshold"]["newest_within_half_lives"] == 2
    assert auto is False
    await _settings(user, label_recency={"enabled": True, "half_life_months": 24})
    mt, auto = await _retrain(user, lid)
    assert auto is True
    await _settings(user, label_recency={"enabled": False, "half_life_months": 12})
    mt, auto = await _retrain(user, lid)
    assert mt["auto_apply_threshold"]["newest_within_half_lives"] is None and auto is True


async def test_user_removal_is_a_strong_negative(client, user, monkeypatch):
    """ACT mail.label: removing a label by hand records a user decision (value False) that
    weighs 10 and queues a retrain."""
    now = datetime.now(UTC)
    await _settings(user, label_recency={"enabled": False, "half_life_months": 12})
    await _bulk(user, 30, "Twitter notification {}", TWEET, "Social", lambda i: now - timedelta(days=i + 1))
    await _bulk(user, 40, "Note {}", NOTE, None, lambda i: now - timedelta(days=i))
    # Reddit notifications filed under the same label in the provider.
    reddit_body = ["{} replied to your comment in the photography thread", "{} replied to your comment about cameras"]
    reddit = await _bulk(user, 8, "Reddit comment reply {}", reddit_body, "Social",
                         lambda i: now - timedelta(days=i + 1))
    labelled = reddit[:2]
    lid = await _label_id(user, "Social")
    probe = await _ingest(user, raw_msg("Reddit comment reply", "someone replied to your comment in the photography thread", now))
    await _process(probe)

    async def prob() -> float:
        return await _prob(user, lid, probe)

    await _retrain(user, lid)
    assert await prob() > 0.5
    # The user takes the label off two of them.
    async with sessionmaker()() as db:
        cids = [(await db.get(Message, mid)).conversation_id for mid in labelled]
    r = await client.post("/api/v1/conversations/actions", json={"ids": cids, "action": "remove_labels", "label_ids": [lid]})
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        d = (await db.execute(select(LabelDecision).where(
            LabelDecision.message_id.in_(labelled), LabelDecision.kind == "label",
            LabelDecision.source == "user"))).scalars().all()
        assert {(x.value, x.target) for x in d} == {(False, str(lid))} and len(d) == 2
        job = (await db.execute(select(Job).where(
            Job.dedupe_key == f"intel.retrain:{user.id}:label:{lid}", Job.status == "queued"))).scalar_one()
        assert job.run_at <= datetime.now(UTC)
    # At the weight of an imported label two removals against six kept would not be enough ...
    monkeypatch.setattr(classify, "WEIGHT_USER", classify.WEIGHT_IMPORTED)
    mt, _ = await _retrain(user, lid)
    assert mt["explicit"] == {"user": 2, "rule": 0}
    weak = await prob()
    # ... at 10 they flip it. (The weak case sits near 0.5 by design, so it is compared with the
    # strong case, not with 0.5. With the fake embedder the scores are the same on every run:
    # 0.56 and 0.31 on 9 Oct 2026.)
    monkeypatch.setattr(classify, "WEIGHT_USER", 10.0)
    await _retrain(user, lid)
    strong = await prob()
    assert strong < 0.5 and weak - strong > 0.1, (weak, strong)
    # With recency on (the default) the recent removals keep their weight.
    await _settings(user, label_recency={"enabled": True, "half_life_months": 12})
    await _retrain(user, lid)
    assert await prob() < 0.5
    # The queued retrain runs within two minutes: a model trained a moment ago waits out the
    # debounce, then the job runs again.
    from types import SimpleNamespace

    from mailr.core.jobs import Retry
    from mailr.intel.tasks import DEBOUNCE, intel_retrain

    with pytest.raises(Retry) as e:
        await intel_retrain({"kind": "label", "target": str(lid)}, SimpleNamespace(user_id=user.id))
    assert e.value.delay == DEBOUNCE.total_seconds() == 120
