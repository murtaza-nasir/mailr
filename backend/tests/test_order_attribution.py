# ACT-SURFACES: assistant
"""A standing order's actions are the order's decisions, not the user's (3 Nov 2026).

An order that files mail by a decision model's pick ran its actions through the user's actions,
so each label it gave was recorded as source "user", reason "user action", and queued a retrain:
the label classifier learned the model's guesses with the weight of the user's own labelling. Now
they are "order" decisions that name the order and say why it acted, and nothing trains on them.
"""

from __future__ import annotations

from helpers.core_flow import _ingest
from sqlalchemy import select, text

from mailr.db import sessionmaker
from mailr.models import LabelDecision


async def _label(client, name):
    r = await client.post("/api/v1/labels", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _decisions(mid):
    async with sessionmaker()() as db:
        return (await db.execute(select(LabelDecision).where(LabelDecision.message_id == mid)
                                 .order_by(LabelDecision.id))).scalars().all()


async def _retrain_jobs(user_id):
    async with sessionmaker()() as db:
        return (await db.execute(text("SELECT count(*) FROM jobs WHERE kind='intel.retrain' AND user_id=:u"),
                                 {"u": user_id})).scalar_one()


async def test_a_choice_orders_label_names_the_order_and_the_model(client, user, raw, monkeypatch):
    from mailr.assistant import decide, orders

    async def fake(db, u, m, q, options):
        return decide.ChoiceAnswer("projects", 0.89, "classifier-v1 p(projects)=0.89", "jev")

    monkeypatch.setattr(decide, "decide_choice", fake)
    projects, travel = await _label(client, "Projects"), await _label(client, "Travel")
    r = await client.post("/api/v1/assistant/orders", json={
        "name": "File by topic", "question": "Which topic is this?",
        "choices": [{"name": "Projects", "description": "research and project work",
                     "actions": {"add_labels": [projects]}},
                    {"name": "Travel", "description": "trips and bookings", "actions": {"add_labels": [travel]}}]})
    assert r.status_code == 200, r.text
    mid, _cid = await _ingest(user, raw("Call for papers", frm="Editor <editor@example.org>"))
    before = await _retrain_jobs(user.id)
    await orders.on_message({"message_id": mid}, None)

    d = [x for x in await _decisions(mid) if x.kind == "label" and x.target == str(projects)]
    assert len(d) == 1
    assert (d[0].source, d[0].source_ref, d[0].status) == ("order", "File by topic", "applied")
    assert d[0].reason == "standing order: chose projects (classifier-v1 p(projects)=0.89)"
    assert abs(d[0].confidence - 0.89) < 1e-6
    assert not [x for x in await _decisions(mid) if x.source == "user"]
    assert await _retrain_jobs(user.id) == before  # nothing to learn from


async def test_a_search_orders_actions_say_the_search_matched(client, user, raw):
    from mailr.assistant import orders

    lid = await _label(client, "Bills")
    r = await client.post("/api/v1/assistant/orders", json={
        "name": "Bills", "query": 'subject:"payment due"', "actions": {"add_labels": [lid], "important": True}})
    assert r.status_code == 200, r.text
    mid, _ = await _ingest(user, raw("Your payment due on Friday", frm="Bank <alerts@bank.example>"))
    await orders.on_message({"message_id": mid}, None)
    got = {(x.kind, x.source, x.source_ref, x.reason) for x in await _decisions(mid) if x.source == "order"}
    assert got == {("label", "order", "Bills", "standing order: the order's search matched"),
                   ("importance", "order", "Bills", "standing order: the order's search matched")}


async def test_the_users_own_label_is_still_a_user_decision(client, user, raw):
    lid = await _label(client, "Receipts")
    mid, cid = await _ingest(user, raw("Order confirmation", frm="Shop <shop@example.net>"))
    r = await client.post("/api/v1/conversations/actions",
                          json={"ids": [cid], "action": "add_labels", "label_ids": [lid]})
    assert r.status_code == 200, r.text
    d = [x for x in await _decisions(mid) if x.kind == "label"]
    assert [(x.source, x.reason, x.status) for x in d] == [("user", "user action", "accepted")]

