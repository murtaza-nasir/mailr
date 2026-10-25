# ACT-SURFACES: api
"""Deadlines are the reader's own obligations (intel/deadlines.py): marketing
dates never become deadlines whatever the model answers, the false ones are named in the model
questions, the deciding model is named on each model row, and "Not a deadline" on the chip
(mail.dismiss_deadline) is stored as a rejection and can mute a sender."""

import json
from datetime import UTC, datetime

import pytest
from helpers.core_flow import _ingest
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.intel import deadlines, jev, llm
from mailr.models import LabelDecision, Message, ModelEndpoint, User

# Every date in these mails lies ahead: next year's, whatever today is.
Y = datetime.now(UTC).year + 1
PROMO = (".sup {\nvertical-align: 6px !important;\nfont-size: 60% !important;\n}\n\n96\n\nChoice Hotels\n\n"
         f"Get 30% off points. Purchase now through October 22, {Y}. Transactions must be completed between "
         f"Sep 7, {Y} and Oct 22, {Y} to be eligible for this discount offer.")
BULK = "List-Unsubscribe: <https://lists.example/u>"


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


async def _jev(user, monkeypatch, pick="d0", seen=None):
    """A Jev-protocol endpoint that always picks `pick` as the deadline."""
    async def fake_decide(r, state, questions, timeout=10):
        if seen is not None:
            seen.update(questions=questions, state=state)
        return {"needs_reply": {"noul": 0.1}, "asks_question": {"noul": 0.1},
                "deadline": {"choice": pick, "probabilities": {pick: 0.9, "none": 0.1}}}

    monkeypatch.setattr(jev, "decide", fake_decide)
    async with sessionmaker()() as db:
        db.add(ModelEndpoint(name="jev-test", base_url="http://jev.invalid/v1", locality="local", adapter="jev",
                             models=["jev-latest"], defaults={"classify": "jev-latest"}, enabled=True))
        u = await db.get(User, user.id)
        u.settings = {**(u.settings or {}), "models": {"classify": {"endpoint": "jev-test", "model": "jev-latest"}}}
        await db.commit()


async def _triage(mid, user):
    from mailr.intel.tasks import retriage_message

    async with sessionmaker()() as db:
        await retriage_message(db, await db.get(Message, mid), await db.get(User, user.id))
        await db.commit()
    async with sessionmaker()() as db:
        return (await db.execute(select(LabelDecision).where(
            LabelDecision.message_id == mid, LabelDecision.kind == "smart"))).scalars().all()


async def test_promotion_gets_no_deadline_whatever_the_model_answers(user, raw, monkeypatch):
    seen = {}
    await _jev(user, monkeypatch, seen=seen)
    mid, _ = await _ingest(user, raw("Account Summary: Discounts, points and experiences await",
                                     frm="Choice Privileges <choiceprivileges_us@members.choicehotels.example>",
                                     to=user.email, extra=BULK, body=PROMO), roles=["inbox"], source="connector")
    rows = await _triage(mid, user)
    assert all(d.target != "deadline" for d in rows)
    # The question names the false deadlines and carries the triage context.
    q = seen["questions"]["deadline"]["instructions"]
    assert "personally" in q and "points promotion" in q and "only attend" in q and "bulk mail" in q
    assert "bulk mail" in seen["state"]["email"]["context"]


async def test_bill_keeps_its_deadline_and_names_the_decision_model(user, raw, monkeypatch):
    await _jev(user, monkeypatch)
    mid, _ = await _ingest(user, raw(f"Your credit card payment is due on 11/04/{Y}",
                                     frm="Card Services <alerts@notify.bank.example>", to=user.email, extra=BULK,
                                     body=f"Payment due date: 11/04/{Y}. Minimum payment due: $35.00. Statement closed Oct 12."),
                           roles=["inbox"], source="connector")
    rows = await _triage(mid, user)
    d = next(d for d in rows if d.target == "deadline")
    assert d.source == "decision_model" and d.source_ref == f"{Y}-11-04"
    assert d.reason.endswith("from jev-test (jev-latest)")


async def test_two_way_sender_and_confirmed_sender_keep_marketing_dates(user, raw):
    from mailr.intel.features import compute

    mid, _ = await _ingest(user, raw("Our sale ends October 22", frm="Shop <deals@shop.example>", to=user.email,
                                     extra=BULK, body=f"40% off everything, sale ends October 22, {Y}."),
                           roles=["inbox"], source="connector")
    async with sessionmaker()() as db:
        u, m = await db.get(User, user.id), await db.get(Message, mid)
        s = await compute(db, m)
        assert await deadlines.blocked(db, u, m, s) == "Marketing mail"
        s.values["i_wrote_to_sender"] = 1.0  # someone the user writes to
        assert await deadlines.blocked(db, u, m, s) is None
        s.values["i_wrote_to_sender"] = 0.0
        db.add(LabelDecision(user_id=u.id, message_id=m.id, kind="smart", target="deadline", value=True,
                             source="heuristic", status="accepted", reason="confirmed"))
        await db.flush()
        assert await deadlines.blocked(db, u, m, s) is None


def test_marketing_and_obligation_wording():
    from mailr.intel.features import _signals
    from mailr.models import Message

    def reason(subject, body, category=None, bulk=True):
        m = Message(subject=subject, body_text=body, from_email="x@example.org", to=[], cc=[], date=None,
                    list_id="l.example" if bulk else None, attachments=[], auth_results={}, delivered_to=[])
        return deadlines.marketing_reason(m, _signals(m, None, None, set(), {}), category)

    assert reason("Get 30% off points", PROMO, "promotions") == "Promotions mail"
    assert reason("Your miles expire November 30", "Earn or redeem miles before then.", "updates") == "Marketing mail"
    assert reason("Statement ready", "Payment due date: November 3. Offer ends October 31.", "updates") is None
    assert reason("Call for papers", "Paper submissions are due November 1.", "promotions") is None
    assert reason("Draft review", "Could you review it by Thursday?", None, bulk=False) is None


async def test_chat_model_question_names_the_false_deadlines(user, raw, monkeypatch):
    from mailr.intel.classify import llm_smart

    sent = {}

    async def fake_chat(db, u, messages, *, task="chat", sensitive=False, **kw):
        sent["prompt"] = messages[-1]["content"]
        answer = {"needs_reply": False, "asks_question": False, "request": "", "deadline": f"October 22, {Y}",
                  "deadline_date": f"{Y}-10-22"}
        return {"content": json.dumps(answer), "_model": "m"}

    monkeypatch.setattr(llm, "chat", fake_chat)
    async with sessionmaker()() as db:
        db.add(ModelEndpoint(name="chat-local", base_url="http://chat.invalid/v1", locality="local", adapter="openai",
                             models=["m"], defaults={"classify": "m"}, enabled=True))
        await db.commit()
    mid, _ = await _ingest(user, raw("Points sale", frm="Rewards <r@hotel.example>", to=user.email, extra=BULK, body=PROMO),
                           roles=["inbox"], source="connector")
    async with sessionmaker()() as db:
        got = await llm_smart(db, await db.get(User, user.id), await db.get(Message, mid), sensitive=False,
                              category="promotions", bulk=True)
    p = sent["prompt"]
    assert "needs_reply" in p and "pay a bill" in p and "points promotion ending" in p and "already past" in p
    assert "bulk mail" in p and "Promotions" in p
    assert got["_source"] == "llm" and got["_label"] == "chat-local (m)"
    rows = await _triage(mid, user)  # the rules drop the model's marketing date
    assert all(d.target != "deadline" for d in rows)


def test_deadline_question_with_context():
    q = jev.deadline_question([{"date": f"October 22, {Y}", "context": f"purchase now through October 22, {Y}"}],
                              "bulk mail (a mailing list or an automated sender), filed under Promotions")
    assert "sale, offer, discount or points promotion ending" in q["instructions"]
    assert q["instructions"].endswith("filed under Promotions.") and set(q["criteria"]) == {"d0", "none"}


async def test_lab_samples_have_expected_answers_and_rules_drop_marketing_dates(client):
    from mailr.intel.lab_samples import SAMPLES

    ids = {s["id"] for s in SAMPLES}
    assert {"points-sale", "points-expire", "card-statement", "conference-register", "form-by-friday"} <= ids
    for s in SAMPLES:
        assert {"category", "needs_reply", "deadline"} <= set(s["expected"]), s["id"]
    out = (await client.post("/api/v1/intel/lab", json={"samples": True})).json()
    by = {i["sample"]["id"]: i for i in out["items"]}
    assert by["points-sale"]["rules"]["signals"]["deadline"] is None
    assert by["form-by-friday"]["rules"]["signals"]["deadline"]  # a colleague's date stays


async def test_dismiss_deadline_rejects_it_and_mutes_the_sender(client, user, raw):
    """mail.dismiss_deadline: "Not a deadline" and "Not a deadline from this sender"."""
    async def one(subject, body):
        mid, cid = await _ingest(user, raw(subject, frm="Dana <dana@example.org>", to=user.email, body=body),
                                 roles=["inbox"], source="connector")
        await _triage(mid, user)
        return mid, cid

    m1, c1 = await one("Forms", f"Please send the forms by December 3, {Y}.")
    _, c2 = await one("Report", f"Please send the report by December 10, {Y}.")
    hits = {c["id"] for c in (await client.get("/api/v1/conversations", params={"view": "all", "q": "has:deadline"})).json()["items"]}
    assert {c1, c2} <= hits
    conv = (await client.get(f"/api/v1/conversations/{c1}")).json()["conversation"]
    assert "deadline" in [s["name"] for s in conv["signals"]]  # the reader's Due chip

    r = await client.post(f"/api/v1/conversations/{c1}/deadline/dismiss", json={})
    assert r.status_code == 200 and r.json()["rejected"] == 1
    hits = {c["id"] for c in (await client.get("/api/v1/conversations", params={"view": "all", "q": "has:deadline"})).json()["items"]}
    assert c1 not in hits and c2 in hits
    assert "deadline" not in [s["name"] for s in (await client.get(f"/api/v1/conversations/{c1}")).json()["conversation"]["signals"]]
    rows = await _triage(m1, user)  # a re-run does not bring it back
    assert not [d for d in rows if d.target == "deadline" and d.status != "rejected"]

    r = (await client.post(f"/api/v1/conversations/{c2}/deadline/dismiss", json={"sender": True})).json()
    assert r["senders"] == ["dana@example.org"] and c2 in r["conversation_ids"]
    async with sessionmaker()() as db:
        assert "dana@example.org" in (await db.get(User, user.id)).settings[deadlines.MUTED_KEY]
    m3, _ = await one("Budget", f"Please send the budget by December 17, {Y}.")
    async with sessionmaker()() as db:
        assert not (await db.execute(select(LabelDecision).where(
            LabelDecision.message_id == m3, LabelDecision.target == "deadline"))).first()
    assert (await client.post("/api/v1/conversations/999999999/deadline/dismiss", json={})).status_code == 404


CASHBACK_SUBJECT = "5% cash back activation confirmed, Rowan: You're earning at Restaurants, Entertainment and Utilities"
CASHBACK = "|    |  |  |  |  Now-Dec. 31, on up to $1,500 in purchases\n---\n\nYou're earning 5% cash back at Restaurants."


async def test_earning_period_is_no_deadline_and_esp_mail_is_not_personal(user, raw, monkeypatch):
    """A card issuer's cash-back confirmation sent through an ESP (Feedback-ID, no List-Unsubscribe),
    from a sender the user hears from often: not personal mail, and "now-Dec. 31" is no deadline."""
    from mailr.intel import categories
    from mailr.intel.features import compute

    await _jev(user, monkeypatch)  # the model picks the only date
    hdr = "Feedback-ID: 80372:40099525:oraclersys"
    for i in range(3):  # a regular sender
        await _ingest(user, raw(f"Statement {i}", frm="Discover <discover@card-e.em.discover.example>", to=user.email,
                                extra=hdr, body="Your statement is ready."), roles=["inbox"], source="connector")
    mid, _ = await _ingest(user, raw(CASHBACK_SUBJECT, frm="Discover <discover@card-e.em.discover.example>",
                                     to=user.email, extra=hdr, body=CASHBACK), roles=["inbox"], source="connector")
    rows = await _triage(mid, user)
    assert all(d.target != "deadline" for d in rows)
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        assert m.category in ("promotions", "updates")
        s = await compute(db, m, raw(CASHBACK_SUBJECT, frm="Discover <discover@card-e.em.discover.example>",
                                     to=user.email, extra=hdr, body=CASHBACK))
        assert categories.heuristic(m, s).category != "primary"
        assert deadlines.period_reason(m, f"{m.date.year}-12-31")  # "now-Dec. 31" in the mail's year
    # An action in the same sentence keeps the date: "Submissions are accepted through November 1".
    m2 = Message(subject="CFP", body_text="Submissions are accepted now through November 1, 2026.", date=None)
    assert deadlines.period_reason(m2, "2026-11-01") is None


def test_esp_mail_without_unsubscribe_is_not_personal():
    from mailr.intel import categories
    from mailr.intel.features import _signals
    from mailr.models import Person

    m = Message(subject="Your benefits this month", body_text="Here is what is new for members.",
                from_email="discover@card-e.em.discover.example", to=[{"email": "me@example.com", "name": ""}],
                cc=[], list_id=None, list_unsubscribe=None, date=None, attachments=[], auth_results={}, delivered_to=[])
    sender = Person(email=m.from_email, name="", sent_count=0, received_count=12)
    g = categories.heuristic(m, _signals(m, sender, None, {"me@example.com"}, {"feedback-id": "1:2:esp"}))
    assert g.category == "updates"
    # Someone the user writes to stays personal even through such a service.
    writes = Person(email=m.from_email, name="", sent_count=3, received_count=12)
    assert categories.heuristic(m, _signals(m, writes, None, {"me@example.com"}, {"feedback-id": "1:2:esp"})).category == "primary"
