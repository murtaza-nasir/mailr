"""The undo of Mark important / Mark not important (ACT mail.important) restores the decision
record: the decisions the toggle added go, an earlier decision is the latest again, and no
opposite "user" choice is recorded (core/actions.restore_importance). A later toggle in the
other direction is a real choice and is recorded."""

import types
from datetime import UTC, datetime, timedelta

from helpers.public_ids import pub  # noqa: F401
from sqlalchemy import select, update

from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.store import user_store
from mailr.db import session_scope, sessionmaker
from mailr.models import Job, LabelDecision, Message, User


async def _ingest(user, data):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        res = await ingest_raw(db, await user_store(db, u), data, IngestOptions())
        await db.commit()
        return res.message.id, res.message.conversation_id


async def _decide(uid, mid, source, value, confidence=1.0, status="applied"):
    async with sessionmaker()() as db:
        d = LabelDecision(user_id=uid, message_id=mid, kind="importance", target="important", value=value,
                          source=source, confidence=confidence, reason="", status=status)
        db.add(d)
        await db.commit()
        return d.id


async def _state(mid):
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        rows = (await db.execute(select(LabelDecision).where(
            LabelDecision.message_id == mid, LabelDecision.kind == "importance").order_by(LabelDecision.id))).scalars()
        return m.important, m.importance_score, [(d.source, d.value, d.status) for d in rows]


async def _start(user, raw, subject, score=0.3, important=False):
    mid, cid = await _ingest(user, raw(subject, frm=f"{subject.split()[0].lower()}@example.org", to=user.email))
    async with sessionmaker()() as db:
        await db.execute(update(Message).where(Message.id == mid).values(importance_score=score, important=important))
        await db.execute(LabelDecision.__table__.delete().where(LabelDecision.message_id == mid))
        await db.commit()
    return mid, cid


async def _undo(client, path, undo, ids=None):
    body = {**undo}
    if ids is not None:
        body["ids"] = ids
    r = await client.post(path, json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_toggle_then_undo_leaves_no_user_decision(client, user, raw):
    mid, cid = await _start(user, raw, "Alpha note")
    await _decide(user.id, mid, "heuristic", False, 0.3)
    before = await _state(mid)
    r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "important"})
    assert r.status_code == 200, r.text
    undo = r.json()["undo"]
    assert undo["action"] == "restore_importance" and undo["ids"] == [cid]
    imp, score, rows = await _state(mid)
    assert imp and score == 1.0 and ("user", True, "accepted") in rows
    await _undo(client, "/api/v1/conversations/actions", undo)
    assert await _state(mid) == before, "flag, score and decisions are as they were"


async def test_undo_over_an_earlier_choice_restores_it(client, user, raw):
    mid, cid = await _start(user, raw, "Beta note", score=1.0, important=True)
    await _decide(user.id, mid, "user", True)
    # A model decision the toggle contradicts is rejected, and the undo gives its status back.
    await _decide(user.id, mid, "classifier", True, 0.8)
    before = await _state(mid)
    r = await client.post("/api/v1/messages/actions", json={"ids": [mid], "action": "not_important"})
    assert r.status_code == 200, r.text
    imp, _s, rows = await _state(mid)
    assert not imp and rows[-1] == ("user", False, "accepted") and ("classifier", True, "rejected") in rows
    await _undo(client, "/api/v1/messages/actions", r.json()["undo"])
    assert await _state(mid) == before
    async with sessionmaker()() as db:
        from mailr.intel import importance

        pos, neg, _explicit = await importance.training_examples(db, user.id)
    assert mid in pos and mid not in neg


async def test_a_later_opposite_toggle_is_a_real_choice(client, user, raw):
    mid, cid = await _start(user, raw, "Gamma note")
    r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "important"})
    assert r.status_code == 200
    r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "not_important"})
    assert r.status_code == 200
    imp, score, rows = await _state(mid)
    assert not imp and score == 0.0
    assert [(s, v) for s, v, _st in rows] == [("user", True), ("user", False)]


async def test_select_all_toggle_and_undo(client, user, raw, monkeypatch):
    import mailr.api.mail_write as mw
    import mailr.core.tasks as tasks

    made = [await _start(user, raw, f"Bulkimp {i}") for i in range(5)]
    q = {"view": "inbox", "q": "subject:bulkimp"}
    # A list small enough to run at once returns its undo record.
    r = await client.post("/api/v1/conversations/actions", json={"query": q, "action": "important"})
    assert r.status_code == 200 and r.json()["affected"] == 5
    await _undo(client, "/api/v1/conversations/actions", r.json()["undo"])
    for mid, _c in made:
        assert await _state(mid) == (False, 0.3, [])
    # A large list runs as a job; the server keeps the record and GET /bulk/{job} offers the undo.
    monkeypatch.setattr(mw, "BULK_INLINE_MAX", 2)
    r = await client.post("/api/v1/conversations/actions", json={"query": q, "action": "important"})
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    async with session_scope() as db:
        j = await db.get(Job, job)
        payload = dict(j.payload)
        j.status = "running"
    await tasks.bulk_conversations(payload, types.SimpleNamespace(job_id=job, user_id=user.id))
    async with session_scope() as db:
        (await db.get(Job, job)).status = "done"
    for mid, _c in made:
        imp, _s, rows = await _state(mid)
        assert imp and rows == [("user", True, "accepted")]
    st = (await client.get(f"/api/v1/bulk/{job}")).json()
    assert st["undo"] == {"action": "restore_importance", "job": job, "ids": []}
    await _undo(client, "/api/v1/conversations/actions", st["undo"])
    for mid, _c in made:
        assert await _state(mid) == (False, 0.3, [])
    # The record is used once.
    assert (await client.get(f"/api/v1/bulk/{job}")).json()["undo"] is None
    r = await client.post("/api/v1/conversations/actions", json=st["undo"])
    assert r.status_code == 404


async def test_undo_speaks_public_ids(pub, client, user, raw):
    mid, cid = await _start(user, raw, "Delta note")
    pc, pm = pub.encode("conversation", cid), pub.encode("message", mid)
    r = await client.post("/api/v1/conversations/actions", json={"ids": [pc], "action": "not_important"})
    assert r.status_code == 200, r.text
    undo = r.json()["undo"]
    assert undo["ids"] == [pc] and undo["importance"]["message_ids"] == [pm]
    await _undo(client, "/api/v1/conversations/actions", undo)
    assert await _state(mid) == (False, 0.3, [])


async def test_undo_without_its_record_removes_the_recent_choice(client, user, raw):
    """An app that drops the record (the Android app keeps only the fields it knows) sends the
    action and the ids: the latest user decision goes when it is recent."""
    mid, cid = await _start(user, raw, "Epsilon note")
    await _decide(user.id, mid, "heuristic", False, 0.3)
    old = await _start(user, raw, "Zeta note")
    oid = await _decide(user.id, old[0], "user", True)
    async with sessionmaker()() as db:
        await db.execute(update(LabelDecision).where(LabelDecision.id == oid).values(
            created_at=datetime.now(UTC) - timedelta(days=2)))
        await db.commit()
    r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "important"})
    assert r.status_code == 200
    await _undo(client, "/api/v1/conversations/actions", {"action": "restore_importance"}, ids=[cid, old[1]])
    assert await _state(mid) == (False, 0.3, [("heuristic", False, "applied")])
    _imp, _s, rows = await _state(old[0])
    assert rows == [("user", True, "applied")], "an old choice is not an undo's to remove"
