"""`mailr decisions reattribute-orders`: decisions that standing orders made before 0.40.37, stored
as the user's own, become the orders' decisions; the user's own stay theirs.

The old data is made the old way: the order runs with no attribution, as 0.40.36 ran it, so its
actions are recorded as "user action" decisions."""

from __future__ import annotations

from datetime import timedelta

from helpers.core_flow import _ingest
from sqlalchemy import select, text, update

from mailr import cli
from mailr.assistant import reattribute
from mailr.db import sessionmaker
from mailr.models import AuditLog, LabelDecision


async def _label(client, name):
    r = await client.post("/api/v1/labels", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _order(client, **body):
    r = await client.post("/api/v1/assistant/orders", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def _old_run(monkeypatch, mid):
    """The order pass as before 0.40.37: the order's actions run as the user's."""
    from mailr.assistant import orders

    with monkeypatch.context() as mp:
        mp.setattr(orders, "attribution", lambda order, cond=None: None)
        await orders.on_message({"message_id": mid}, None)


async def _decisions(uid):
    async with sessionmaker()() as db:
        return (await db.execute(select(LabelDecision).where(LabelDecision.user_id == uid)
                                 .order_by(LabelDecision.id))).scalars().all()


async def _retrain_jobs(uid):
    async with sessionmaker()() as db:
        return [tuple(r) for r in (await db.execute(text(
            "SELECT payload->>'kind', payload->>'target' FROM jobs WHERE kind='intel.retrain' AND user_id=:u "
            "AND status='queued' ORDER BY id"), {"u": uid})).all()]


async def _clear_retrains(uid):
    async with sessionmaker()() as db:
        await db.execute(text("DELETE FROM jobs WHERE kind='intel.retrain' AND user_id=:u"), {"u": uid})
        await db.commit()


def _snapshot(ds):
    return [(d.id, d.kind, d.target, d.value, d.source, d.source_ref, d.reason, d.status, d.confidence) for d in ds]


async def test_old_order_decisions_become_the_orders_and_the_users_own_stay(client, user, raw, monkeypatch, capsys):
    bills, travel = await _label(client, "Bills"), await _label(client, "Travel")
    await _order(client, name="Bills", query='subject:"payment due"',
                 actions={"add_labels": [bills], "important": True})
    mid, cid = await _ingest(user, raw("Your payment due on Friday", frm="Bank <alerts@bank.example>"))
    late, _ = await _ingest(user, raw("Second payment due notice", frm="Bank <alerts@bank.example>"))
    await _old_run(monkeypatch, mid)
    await _old_run(monkeypatch, late)

    # The user's own label on the same message, at the same moment as the order's.
    r = await client.post("/api/v1/conversations/actions",
                          json={"ids": [cid], "action": "add_labels", "label_ids": [travel]})
    assert r.status_code == 200, r.text
    ds = await _decisions(user.id)
    assert {d.source for d in ds} == {"user"}  # the old way: all of them look like the user's
    order_at = next(d.created_at for d in ds if d.message_id == mid and d.target == str(bills))
    async with sessionmaker()() as db:
        await db.execute(update(LabelDecision).where(LabelDecision.message_id == mid,
                                                     LabelDecision.target == str(travel)).values(created_at=order_at))
        # The second message's label decision lies outside its run's time: it stays the user's.
        await db.execute(update(LabelDecision).where(LabelDecision.message_id == late, LabelDecision.kind == "label")
                         .values(created_at=order_at + timedelta(minutes=10)))
        await db.commit()
    before = _snapshot(await _decisions(user.id))

    # Dry run: counts, no change.
    assert await cli._reattribute_orders(str(user.id), True, 500) == 0
    out = capsys.readouterr().out
    assert _snapshot(await _decisions(user.id)) == before
    assert "5 decisions recorded as the user's, 3 would be a standing order's" in out
    assert "2 stay the user's" in out and "order Bills: 3" in out
    assert "label Bills: 1" in out and "importance important: 2" in out
    assert "dry run, nothing changed" in out

    await _clear_retrains(user.id)
    assert await cli._reattribute_orders(user.email, False, 1) == 0  # one decision per transaction
    ds = {(d.message_id, d.kind, d.target): d for d in await _decisions(user.id)}
    for key in ((mid, "label", str(bills)), (mid, "importance", "important"), (late, "importance", "important")):
        d = ds[key]
        assert (d.source, d.source_ref, d.status) == ("order", "Bills", "applied"), key
        assert d.reason == "standing order: the order's search matched"
        assert d.confidence is None
    for key in ((mid, "label", str(travel)), (late, "label", str(bills))):
        assert (ds[key].source, ds[key].reason, ds[key].status) == ("user", "user action", "accepted"), key
    assert await _retrain_jobs(user.id) == [("label", str(bills)), ("importance", "important")]
    async with sessionmaker()() as db:
        audit = (await db.execute(select(AuditLog).where(
            AuditLog.user_id == user.id, AuditLog.action == "decisions.reattribute_orders"))).scalars().all()
    assert len(audit) == 1 and audit[0].result["reattributed"] == 3 and audit[0].result["kept"] == 2

    # A second run changes nothing and queues nothing.
    after = _snapshot(await _decisions(user.id))
    await _clear_retrains(user.id)
    rep = await reattribute.reattribute(user.id, dry_run=False)
    assert (rep.examined, rep.matched, rep.retrains) == (2, 0, [])
    assert _snapshot(await _decisions(user.id)) == after
    assert await _retrain_jobs(user.id) == []


async def test_a_choice_orders_pick_names_the_model_and_its_whole_conversation(client, user, raw, monkeypatch):
    from mailr.assistant import decide

    async def fake(db, u, m, q, options):
        return decide.ChoiceAnswer("projects", 0.89, "classifier-v1 p(projects)=0.89", "jev")

    monkeypatch.setattr(decide, "decide_choice", fake)
    projects = await _label(client, "Projects")
    await _order(client, name="File by topic", question="Which topic is this?",
                 choices=[{"name": "Projects", "description": "research", "actions": {"add_labels": [projects]}},
                          {"name": "Other", "description": "anything else", "actions": {"category": "updates"}}])
    first = "<first-call@example.org>"
    m1, _ = await _ingest(user, raw("Call for papers", frm="Editor <editor@example.org>", msgid=first))
    m2, _ = await _ingest(user, raw("Re: Call for papers", frm="Editor <editor@example.org>", irt=first))
    await _old_run(monkeypatch, m2)
    labelled = {d.message_id for d in await _decisions(user.id) if d.kind == "label"}
    assert labelled == {m1, m2}  # the order labels the whole conversation

    rep = await reattribute.reattribute(user.id, dry_run=False)
    assert (rep.matched, rep.other_messages) == (2, 1)
    for d in await _decisions(user.id):
        assert (d.source, d.source_ref) == ("order", "File by topic")
        assert d.reason == "standing order: chose projects (classifier-v1 p(projects)=0.89)"
        assert abs(d.confidence - 0.89) < 1e-6


async def test_a_deleted_orders_decisions_are_still_an_orders(client, user, raw, monkeypatch):
    lid = await _label(client, "News")
    o = await _order(client, name="News", query="subject:newsletter", actions={"add_labels": [lid]})
    mid, _ = await _ingest(user, raw("Weekly newsletter", frm="News <news@example.net>"))
    await _old_run(monkeypatch, mid)
    r = await client.delete(f"/api/v1/assistant/orders/{o['id']}")
    assert r.status_code in (200, 204), r.text
    rep = await reattribute.reattribute(user.id, dry_run=False)
    assert rep.matched == 1
    d = [x for x in await _decisions(user.id) if x.kind == "label"][0]
    assert (d.source, d.source_ref, d.reason) == ("order", "(deleted order)", "standing order: the order's search matched")


def test_the_action_must_set_exactly_that_decision():
    m = reattribute.action_matches
    acts = [{"action": "add_labels", "args": {"label_ids": [7, 9]}, "affected": 1},
            {"action": "remove_labels", "args": {"label_ids": [3]}},
            {"action": "important"}, {"action": "category", "args": {"category": "updates"}}]
    assert m("label", "7", True, acts) and m("label", "9", True, acts)
    assert not m("label", "8", True, acts)
    assert not m("label", "7", False, acts)  # a removal is not the add
    assert m("label", "3", False, acts) and not m("label", "3", True, acts)
    assert m("importance", "important", True, acts)
    assert not m("importance", "important", False, acts)
    assert m("category", "updates", True, acts) and not m("category", "social", True, acts)
    assert not m("importance", "important", True, [{"action": "add_labels", "args": {"label_ids": [7]}}])
    assert not m("label", "7", True, [{"action": "label", "args": "junk"}, "junk"])
    assert not m("label", "7", True, None)
