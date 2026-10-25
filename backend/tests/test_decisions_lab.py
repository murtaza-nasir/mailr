"""The decisions lab: rules and each sorting model side by side, nothing stored; local-only mail
never reaches a hosted model."""

import pytest
from conftest import mail_date
from helpers.core_flow import _ingest

from mailr.db import sessionmaker
from mailr.intel import llm
from mailr.models import LabelDecision, Message, ModelEndpoint


@pytest.fixture(autouse=True)
async def _no_endpoints(app):
    """Endpoints are server-wide: each test here starts and ends without any."""
    from sqlalchemy import delete

    async def clear():
        async with sessionmaker()() as db:
            await db.execute(delete(ModelEndpoint))
            await db.commit()

    await clear()
    yield
    await clear()

async def _endpoint(name, locality):
    async with sessionmaker()() as db:
        db.add(ModelEndpoint(name=name, base_url=f"http://{name}.invalid/v1", locality=locality, adapter="openai",
                             models=["m"], defaults={"classify": "m"}, enabled=True))
        await db.commit()


async def test_lab_runs_each_model_and_stores_nothing(client, user, raw, monkeypatch):
    seen = []

    async def fake_chat(db, u, messages, *, task="chat", sensitive=False, **kw):
        r = await llm.resolve(db, u, task, sensitive=sensitive)
        seen.append(r.endpoint.name)
        body = messages[-1]["content"]
        if "inbox tab" in body:
            return {"content": '{"category": "updates"}', "_model": "m"}
        if "needs_reply" in body:
            return {"content": '{"needs_reply": true, "asks_question": true, "request": "send the slides", '
                               '"deadline": null, "deadline_date": null}', "_model": "m"}
        return {"content": '{"answer": true, "confidence": 0.9, "reason": "a bill"}', "_model": "m"}

    monkeypatch.setattr(llm, "chat", fake_chat)
    await _endpoint("lab-local", "local")
    await _endpoint("lab-hosted", "hosted")
    mid, _ = await _ingest(user, raw("Slides for Friday?", frm="Dana <dana@example.org>", to=user.email,
                                     body="Could you send the slides before Friday?"), roles=["inbox"], source="connector")
    r = await client.post("/api/v1/intel/lab", json={"message_ids": [mid], "endpoints": ["lab-local", "lab-hosted"],
                                                     "question": "Is this a bill?"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert [e["name"] for e in out["endpoints"]] == ["lab-local", "lab-hosted"]
    item = out["items"][0]
    assert item["message"]["subject"] == "Slides for Friday?" and item["rules"]["category"]["value"]
    for name in ("lab-local", "lab-hosted"):
        res = item["models"][name]
        assert res["category"]["value"] == "updates" and res["signals"]["needs_reply"] is True
        assert res["question"]["answer"] is True and res["question"]["ms"] >= 0
    assert set(seen) == {"lab-local", "lab-hosted"}
    async with sessionmaker()() as db:  # nothing stored
        from sqlalchemy import select

        assert (await db.execute(select(LabelDecision).where(LabelDecision.message_id == mid,
                                                             LabelDecision.source == "llm"))).first() is None

    # Local-only mail: the hosted endpoint is not called.
    async with sessionmaker()() as db:
        (await db.get(Message, mid)).local_only = True
        await db.commit()
    seen.clear()
    item = (await client.post("/api/v1/intel/lab", json={"message_ids": [mid], "endpoints": ["lab-local", "lab-hosted"]})).json()["items"][0]
    assert seen and set(seen) == {"lab-local"}
    assert "local-only" in item["models"]["lab-hosted"]["category"]["error"]


async def test_retriage_inbox_reruns_signals_with_the_model(client, user, raw, monkeypatch):
    from sqlalchemy import select

    from mailr.intel.tasks import retriage_job

    async def fake_chat(db, u, messages, *, task="chat", sensitive=False, **kw):
        return {"content": '{"needs_reply": true, "asks_question": true, "request": "send the slides", '
                           '"deadline": null, "deadline_date": null}', "_model": "m"}

    async def yes(*a, **kw):
        return True

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(llm, "available", yes)
    await _endpoint("rt-local", "local")
    # An older message to me: triage would not ask the model (outside the 3-day window).
    mid, _ = await _ingest(user, raw("Slides?", frm="Dana <dana@example.org>", to=user.email, body="Thanks for today.",
                                     date=mail_date(-14)), roles=["inbox"], source="connector")
    r = (await client.post("/api/v1/intel/retriage", json={"view": "inbox"})).json()
    assert r["queued"] >= 1
    await retriage_job({"user_id": user.id, "message_ids": [mid]}, None)
    async with sessionmaker()() as db:
        got = (await db.execute(select(LabelDecision.target, LabelDecision.source).where(
            LabelDecision.message_id == mid, LabelDecision.kind == "smart"))).all()
    assert ("needs_reply", "llm") in got
    hits = (await client.get("/api/v1/conversations", params={"view": "all", "q": "is:needs_reply"})).json()["items"]
    row = next(c for c in hits if c["subject"] == "Slides?")
    assert [x["name"] for x in row["signals"]] == ["needs_reply"]  # what the list shows as a chip


async def test_sample_emails_run_without_storing_anything(client, user):
    from sqlalchemy import func, select

    from mailr.intel.lab_samples import SAMPLES

    async with sessionmaker()() as db:
        before = (await db.execute(select(func.count()).select_from(Message))).scalar()
    out = (await client.post("/api/v1/intel/lab", json={"samples": True})).json()
    assert [i["sample"]["id"] for i in out["items"]] == [s["id"] for s in SAMPLES]
    by = {i["sample"]["id"]: i for i in out["items"]}
    assert by["card-bill"]["message"]["bulk"] and by["card-bill"]["rules"]["actions"][0]["kind"] == "pay"
    assert by["package"]["rules"]["actions"][0]["kind"] == "track"
    assert by["verification-code"]["rules"]["actions"][0]["label"] == "Copy code 480204"
    assert by["review-by-thursday"]["message"]["to_me"] and not by["review-by-thursday"]["message"]["bulk"]
    async with sessionmaker()() as db:
        assert (await db.execute(select(func.count()).select_from(Message))).scalar() == before
