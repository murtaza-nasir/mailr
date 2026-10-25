# ACT-SURFACES: api
"""Standing orders and routines over Speakr recordings: the
`recording` trigger runs once per recording that a sync sees completed, through its filter and its
question; the first sync and old recordings start nothing; the chain guard skips a recording an
errand uploaded and a later edit does not start the order again; a routine's recording source
selects today's recordings for the errand's prompt."""

from datetime import UTC, datetime, timedelta

import pytest
from speakr_fake import connect, speakr  # noqa: F401
from sqlalchemy import select

from mailr.assistant import errands
from mailr.assistant import orders as ord_
from mailr.db import sessionmaker
from mailr.integrations.speakr.sync import sync_connector
from mailr.intel import llm
from mailr.models import AssistantOrder, AssistantRun, Job, RecordingLink, User


async def _order(client, **o):
    r = await client.post("/api/v1/assistant/orders", json=o)
    assert r.status_code == 200, r.text
    return r.json()


async def _runs(order_id):
    async with sessionmaker()() as db:
        return list((await db.execute(select(AssistantRun).where(AssistantRun.order_id == order_id)
                                      .order_by(AssistantRun.id))).scalars())


async def test_recording_order_validation(client, user):
    bad = [({"trigger": "recording", "errand": "x", "query": "in:inbox"}, "no mail search"),
           ({"trigger": "recording", "errand": "x", "actions": {"archive": True}}, "only instant action is notify"),
           ({"trigger": "recording", "recordings": {"q": "advising"}}, "needs instructions"),
           ({"trigger": "message", "query": "from:x", "errand": "y", "recordings": {"q": "z"}}, "no recording filter"),
           ({"trigger": "recording", "errand": "x", "recordings": {"colour": "red"}}, "unknown recording filter")]
    for body, msg in bad:
        r = await client.post("/api/v1/assistant/orders", json=body)
        assert r.status_code == 400 and msg in r.json()["detail"], (body, r.text)
    o = await _order(client, trigger="recording", errand="Propose tasks", recordings={"q": "advising", "days": 3})
    assert o["recordings"] == {"q": "advising"} and o["deliver"] == ["notify"] and o["schedule"] == ""
    r = await client.post(f"/api/v1/assistant/orders/{o['id']}/run", json={})
    assert r.status_code == 400


async def test_recording_trigger_runs_once_filter_question_and_chain_guard(client, user, speakr, monkeypatch):
    speakr.add(1, "Advising: old")  # already there at the first sync: starts nothing
    conn = await connect(client, speakr)
    o = await _order(client, name="Advising follow-ups", trigger="recording", recordings={"q": "advising"},
                     errand="Draft the follow-up and propose tasks.")
    asked = await _order(client, name="Only budget meetings", trigger="recording", question="Is this about the budget?",
                         errand="Summarise the budget decisions.")
    seen = []

    async def fake_chat(db, u, messages, *, sensitive, **kw):
        seen.append(sensitive)
        return {"content": '{"answer": false, "confidence": 0.9, "reason": "not about money"}'}

    monkeypatch.setattr(llm, "chat", fake_chat)
    speakr.add(2, "Advising: Dana", meeting_date=datetime.now(UTC) - timedelta(hours=2))
    speakr.add(3, "Weekly sync", meeting_date=datetime.now(UTC) - timedelta(hours=1))
    speakr.add(4, "Advising: Evan", status="PROCESSING")
    out = await sync_connector(conn["id"])
    assert sorted(out["completed"]) == [2, 3]
    runs = await _runs(o["id"])
    assert [(r.slot, r.status, r.trigger) for r in runs] == [(f"r{conn['id']}:2", "queued", "recording")]
    assert runs[0].condition["recording"]["recording_id"] == 2
    q = await _runs(asked["id"])
    assert sorted(r.condition["recording"]["recording_id"] for r in q) == [2, 3]
    assert {r.status for r in q} == {"skipped"} and seen == [True, True]  # local-only: a local model
    async with sessionmaker()() as db:
        jobs = [j for j in (await db.execute(select(Job).where(Job.kind == "assistant.errand", Job.user_id == user.id))).scalars()]
        assert [j.payload["run_id"] for j in jobs] == [runs[0].id]
        prompt = await errands._prompt(db, await db.get(User, user.id), await db.get(AssistantOrder, o["id"]), runs[0], [])
    assert "[[r:2]]" in prompt and "get_summary" in prompt and "never creates share links" in prompt
    assert "selected no conversations" not in prompt

    # Nothing again: another sync, an edit of the title in Speakr, the processing one completes once.
    speakr.recordings[2]["title"] = "Advising: Dana (renamed)"
    speakr.recordings[4]["status"] = "COMPLETED"
    out = await sync_connector(conn["id"])
    assert out["completed"] == [4]
    assert [r.slot for r in await _runs(o["id"])] == [f"r{conn['id']}:2", f"r{conn['id']}:4"]
    await sync_connector(conn["id"])
    assert len(await _runs(o["id"])) == 2

    # Chain guard: a recording an errand uploaded starts no order.
    speakr.add(5, "Advising: uploaded", status="PROCESSING")
    await sync_connector(conn["id"])
    async with sessionmaker()() as db:
        db.add(RecordingLink(user_id=user.id, connector_id=conn["id"], recording_id=5, kind="conversation",
                             target="1", state="confirmed", source="order"))
        await db.commit()
    speakr.recordings[5]["status"] = "COMPLETED"
    await sync_connector(conn["id"])
    last = (await _runs(o["id"]))[-1]
    assert last.slot == f"r{conn['id']}:5" and last.status == "skipped" and "chain guard" in last.error

    # A recording completed long ago (an old one Speakr lists late) starts nothing.
    speakr.add(6, "Advising: archive", status="PROCESSING",
               meeting_date=datetime.now(UTC) - timedelta(days=30))
    speakr.recordings[6]["completed_at"] = (datetime.now(UTC) - timedelta(days=20)).replace(tzinfo=None).isoformat()
    speakr.recordings[6]["created_at"] = (datetime.now(UTC) - timedelta(days=20)).replace(tzinfo=None).isoformat()
    await sync_connector(conn["id"])
    speakr.recordings[6]["status"] = "COMPLETED"
    await sync_connector(conn["id"])
    assert len(await _runs(o["id"])) == 3


@pytest.mark.parametrize("days", [1, 7])
async def test_routine_with_recording_source(client, user, speakr, days):
    now = datetime.now(UTC)
    today = now - (now - now.replace(hour=0, minute=0, second=0, microsecond=0)) / 2  # earlier today (UTC)
    speakr.add(1, "Advising: Dana", meeting_date=today)
    speakr.add(2, "Weekly sync", meeting_date=today)
    speakr.add(3, "Advising: last week", meeting_date=now - timedelta(days=5))
    await connect(client, speakr)
    o = await _order(client, name="Today's meetings", trigger="schedule", schedule="0 17 * * 1-5", timezone="UTC",
                     recordings={"enabled": True, "q": "advising", "days": days},
                     errand="For today's recorded meetings, draft follow-ups and propose tasks.")
    assert o["recordings"]["enabled"] and o["query"] == ""
    async with sessionmaker()() as db:
        order = await db.get(AssistantOrder, o["id"])
        u = await db.get(User, user.id)
        run = await ord_.start_routine(db, u, order, "manual:test", trigger="manual")
        await db.commit()
        got = [r["recording_id"] for r in run.condition["recordings"]]
        assert got == ([1] if days == 1 else [1, 3])
        assert run.condition["conversations"] == 0 and run.status == "queued"
        prompt = await errands._prompt(db, u, order, run, [])
    assert "[[r:1]]" in prompt and "[[r:2]]" not in prompt and "selected no conversations" not in prompt


async def test_recording_order_run_now_and_test(client, user, speakr, monkeypatch):
    """order.run and order.test on a recording order: Run now takes the newest completed recording
    the filter matches, once (the chain guard's slot); Test answers filter and question, changes nothing."""
    now = datetime.now(UTC)
    speakr.add(1, "Advising: Dana", meeting_date=now - timedelta(days=9))  # old: Run now may still pick it
    speakr.add(2, "Weekly sync", meeting_date=now - timedelta(days=8))
    speakr.add(3, "Advising: Evan", status="PROCESSING", meeting_date=now - timedelta(days=1))
    conn = await connect(client, speakr)
    o = await _order(client, name="Advising", trigger="recording", recordings={"q": "advising"},
                     errand="Propose tasks.")
    r = await client.post(f"/api/v1/assistant/orders/{o['id']}/run", json={})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["matched"] and not d["already"] and d["recording"]["recording_id"] == 1
    assert d["run"]["trigger"] == "manual" and d["run"]["status"] == "queued"
    assert d["run"]["slot"] == f"r{conn['id']}:1"
    again = (await client.post(f"/api/v1/assistant/orders/{o['id']}/run", json={})).json()
    assert again["already"] and again["run"] is None and len(await _runs(o["id"])) == 1

    calls = []

    async def fake_chat(db, u, messages, *, sensitive, **kw):
        calls.append(sensitive)
        yes = "Dana" in messages[0]["content"]
        return {"content": '{"answer": %s, "confidence": 0.9, "reason": "r"}' % ("true" if yes else "false")}

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = await client.post("/api/v1/assistant/orders/preview",
                          json={"trigger": "recording", "recordings": {"q": "advising"}, "question": "Is Dana there?"})
    assert r.status_code == 200, r.text
    prev = r.json()
    assert prev["scope"] == "recordings" and prev["searched"] == 2 and prev["matched"] == 1
    by = {i["recording_id"]: i for i in prev["items"]}
    assert by[1]["matched"] and by[1]["answer"] and not by[2]["matched"] and "answer" not in by[2]
    assert calls == [True]  # local-only: a local model, and only for the recording the filter picked
    assert len(await _runs(o["id"])) == 1
    bad = await client.post("/api/v1/assistant/orders/preview", json={"trigger": "recording", "recordings": {"x": 1}})
    assert bad.status_code == 400

    # A filter nothing matches: Run now explains.
    none = await _order(client, name="Board", trigger="recording", recordings={"q": "board"}, errand="x")
    r = await client.post(f"/api/v1/assistant/orders/{none['id']}/run", json={})
    assert r.status_code == 400 and "no completed recording matches" in r.json()["detail"]
