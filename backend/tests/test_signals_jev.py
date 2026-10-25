"""Triage signals with a Jev-style decision model: tighter reply questions, deadlines chosen
among the email's dates, and bulk mail keeping its deadline but not its reply signals."""

from email.utils import parsedate_to_datetime

import pytest
from conftest import mail_date
from helpers.core_flow import _ingest

from mailr.db import sessionmaker
from mailr.intel import jev
from mailr.intel.classify import date_candidates
from mailr.models import LabelDecision, Message, ModelEndpoint, User


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

def test_date_candidates_list_each_date_with_context():
    got = date_candidates("Your payment is due on 10/04/2026. Statement closed Sep 12. Due on 10/04/2026.")
    assert [c["date"] for c in got] == ["10/04/2026", "Sep 12"]
    assert "payment is due on 10/04/2026" in got[0]["context"]


async def _jev_endpoint():
    async with sessionmaker()() as db:
        db.add(ModelEndpoint(name="jev-test", base_url="http://jev.invalid/v1", locality="local", adapter="jev",
                             models=["jev-latest"], defaults={"classify": "jev-latest"}, enabled=True))
        await db.commit()


async def test_bill_gets_its_deadline_but_no_reply_signal(user, raw, monkeypatch):
    sent = {}

    async def fake_decide(r, state, questions, timeout=10):
        sent.update(questions)
        return {"needs_reply": {"noul": 0.9}, "asks_question": {"noul": 0.9},
                "deadline": {"choice": "d0", "probabilities": {"d0": 0.93, "d1": 0.04, "none": 0.03}}}

    monkeypatch.setattr(jev, "decide", fake_decide)
    await _jev_endpoint()
    # Not "Wells Fargo" from another domain: brand impersonation goes to Spam, and no model reads Spam.
    # Due 13 days after the mail (yesterday): an upcoming date when it came.
    due = parsedate_to_datetime(mail_date(13)).date()
    us = f"{due:%m/%d/%Y}"
    mid, _ = await _ingest(user, raw(f"Your credit card payment is due on {us}", frm="Card Services <alerts@notify.wf.example>",
                                     to=user.email, extra="List-Unsubscribe: <https://wf.example/u>",
                                     body=f"Your payment is due on {us}. Statement closed Sep 12."),
                           roles=["inbox"], source="connector")
    from mailr.intel.tasks import retriage_message

    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.settings = {**(u.settings or {}), "models": {"classify": {"endpoint": "jev-test", "model": "jev-latest"}}}
        await db.commit()
    async with sessionmaker()() as db:
        await retriage_message(db, await db.get(Message, mid), await db.get(User, user.id))
        await db.commit()
    assert "personally" in sent["needs_reply"]["instructions"]  # the tighter question
    crit = sent["deadline"]["criteria"]
    assert crit["d0"].startswith(us) and "none" in crit
    async with sessionmaker()() as db:
        from sqlalchemy import select

        rows = (await db.execute(select(LabelDecision.target, LabelDecision.source_ref).where(
            LabelDecision.message_id == mid, LabelDecision.kind == "smart"))).all()
    # Bulk (it has List-Unsubscribe): the deadline counts, the reply signal does not.
    assert ("deadline", due.isoformat()) in rows and all(t != "needs_reply" for t, _ in rows)
