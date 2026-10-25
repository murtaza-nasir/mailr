"""Spam and triage: mail in Spam or Trash shows no triage signal anywhere and comes
back with its signals when it leaves; no model reads likely spam; the user's spam marks teach
mailr a sender domain quickly."""

from datetime import UTC, datetime, timedelta

import pytest
from conftest import mail_date
from helpers.core_flow import _ingest
from sqlalchemy import delete, select

from mailr.db import sessionmaker
from mailr.intel import jev, llm
from mailr.models import Job, LabelDecision, Message, ModelEndpoint, User

TODAY = datetime.now(UTC).date()
SOON = (TODAY + timedelta(days=3)).isoformat()


@pytest.fixture(autouse=True)
async def _no_endpoints(app):
    """Endpoints are server-wide: each test here starts and ends without any."""
    async def clear():
        async with sessionmaker()() as db:
            await db.execute(delete(ModelEndpoint))
            await db.commit()

    await clear()
    yield
    await clear()


async def _roles(client):
    return {lb["role"]: lb["id"] for lb in (await client.get("/api/v1/labels")).json() if lb["role"]}


async def _signal(mid, user, target="deadline", ref=SOON):
    async with sessionmaker()() as db:
        db.add(LabelDecision(user_id=user.id, message_id=mid, kind="smart", target=target, value=True,
                             source="decision_model", source_ref=ref if target == "deadline" else "decider",
                             reason="Submit by then", status="applied"))
        await db.commit()


async def _act(client, cid, action, **extra):
    r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": action, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _sigs(items, cid):
    return next(c for c in items if c["id"] == cid)["signals"]


# ---------------------------------------------------------------- signals in Spam and Trash


async def test_spam_hides_signals_everywhere_and_not_spam_brings_them_back(client, user, raw):
    mid, cid = await _ingest(user, raw("Publish in our Scopus journal", frm="Editor <editor@journal-cfp.example>",
                                       to=user.email), roles=["inbox"])
    await _signal(mid, user, "deadline")
    await _signal(mid, user, "needs_reply")
    lst = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"]
    assert {s["name"] for s in _sigs(lst, cid)} == {"deadline", "needs_reply"}

    await _act(client, cid, "spam")
    spam = (await client.get("/api/v1/conversations", params={"view": "spam"})).json()["items"]
    assert _sigs(spam, cid) == []
    reader = (await client.get(f"/api/v1/conversations/{cid}")).json()
    assert reader["conversation"]["signals"] == []
    for name in ("deadline", "needs_reply"):
        ids = [c["id"] for c in (await client.get(f"/api/v1/smart/{name}")).json()["items"]]
        assert cid not in ids, name
    # Search leaves Spam out unless asked; asked, it finds the stored signal.
    found = lambda q: client.get("/api/v1/conversations", params={"view": "all", "q": q})
    assert cid not in [c["id"] for c in (await found("has:deadline")).json()["items"]]
    assert cid in [c["id"] for c in (await found("in:spam has:deadline")).json()["items"]]
    # The decisions stay stored.
    async with sessionmaker()() as db:
        n = len((await db.execute(select(LabelDecision).where(
            LabelDecision.message_id == mid, LabelDecision.kind == "smart"))).scalars().all())
    assert n == 2

    await _act(client, cid, "not_spam")
    lst = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"]
    assert {s["name"] for s in _sigs(lst, cid)} == {"deadline", "needs_reply"}
    reader = (await client.get(f"/api/v1/conversations/{cid}")).json()
    assert {s["name"] for s in reader["conversation"]["signals"]} == {"deadline", "needs_reply"}
    assert cid in [c["id"] for c in (await client.get("/api/v1/smart/deadline")).json()["items"]]


async def test_trash_hides_signals_and_the_newest_live_message_decides(client, user, raw):
    m1, cid = await _ingest(user, raw("Form due", frm="Registrar <reg@uni.example>", msgid="<t1@x.org>",
                                      to=user.email), roles=["inbox"])
    m2, cid2 = await _ingest(user, raw("Re: Form due", frm="Registrar <reg@uni.example>", msgid="<t2@x.org>",
                                       irt="<t1@x.org>", date=mail_date(1), to=user.email),
                             roles=["inbox"])
    assert cid == cid2
    await _signal(m1, user, "deadline")
    await _signal(m2, user, "needs_reply")
    # Trash the newest message only: the older message's deadline is the conversation's now.
    roles = await _roles(client)
    async with sessionmaker()() as db:
        (await db.get(Message, m2)).label_ids = [roles["trash"]]
        await db.commit()
    lst = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"]
    assert [s["name"] for s in _sigs(lst, cid)] == ["deadline"]
    await _act(client, cid, "trash")
    trash = (await client.get("/api/v1/conversations", params={"view": "trash"})).json()["items"]
    assert _sigs(trash, cid) == []


async def test_due_counts_leave_out_spam(client, user, raw):
    fin = (await client.post("/api/v1/labels", json={"name": "financial"})).json()["id"]
    mid, cid = await _ingest(user, raw("Invoice", frm="Billing <billing@vendor.example>", to=user.email),
                             roles=["inbox"])
    await _act(client, cid, "add_labels", label_ids=[fin])
    await _signal(mid, user, "deadline")
    lay = {"name": "Money", "tabs": [{"name": "Financial", "icon": "briefcase", "indicators": ["due"],
                                      "sections": [{"title": "All", "base": f"label:{fin}"}]}]}
    assert (await client.put("/api/v1/layouts", json={"layouts": [lay], "active": "money"})).status_code == 200

    async def due():
        return (await client.get("/api/v1/layouts/money/counts", params={"detail": "true"})).json()["tabs"]["financial"]

    assert (await due())["due_count"] == 1
    roles = await _roles(client)
    async with sessionmaker()() as db:  # a filter labelled it, then it went to Spam
        (await db.get(Message, mid)).label_ids = [fin, roles["junk"]]
        await db.commit()
    # A direct write records no change, which the cached tab counts follow (api/layout_routes.py).
    from mailr.api import layout_routes

    layout_routes._TAB_COUNTS.clear()
    got = await due()
    assert got["due_count"] == 0 and got["earliest_due"] is None


async def test_routines_and_signal_tool_leave_out_spam(client, user, raw):
    from mailr.assistant.orders import routine_conversations
    from mailr.assistant.tools import get_signals
    from mailr.core.auth import Principal

    mid, cid = await _ingest(user, raw("Grant deadline", frm="Office <office@funder.example>", to=user.email,
                                       date=datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")),
                             roles=["inbox"])
    await _signal(mid, user, "deadline")
    await _act(client, cid, "spam")
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        assert cid not in await routine_conversations(db, u, "in:all")
        assert cid in await routine_conversations(db, u, "in:spam")

        class Ctx:
            p = Principal(user=u)
            ui = chat_context = None

        Ctx.db = db
        res = await get_signals(Ctx(), {"signal": "deadline"})
        assert all(i["conversation_id"] != cid for i in res.data["items"])


# ---------------------------------------------------------------- no model reads likely spam


async def _endpoints(monkeypatch, calls):
    async def fake_chat(db, u, messages, *, task="chat", sensitive=False, **kw):
        calls.append("chat")
        return {"content": '{"needs_reply": true, "asks_question": true, "request": "x", "deadline": null, '
                           '"deadline_date": null, "category": "primary"}', "_model": "m"}

    async def fake_decide(r, state, questions, timeout=10):
        calls.append("jev")
        return None

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(jev, "decide", fake_decide)
    async with sessionmaker()() as db:
        db.add(ModelEndpoint(name="chat-local", base_url="http://chat.invalid/v1", locality="local", adapter="openai",
                             models=["m"], defaults={"classify": "m"}, enabled=True))
        await db.commit()


async def _triage(mid, user):
    from mailr.intel.tasks import process_message

    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        await process_message(db, m, await db.get(User, user.id), allow_llm=True)
        await db.commit()
    async with sessionmaker()() as db:
        return (await db.execute(select(LabelDecision).where(
            LabelDecision.message_id == mid, LabelDecision.kind == "triage"))).scalars().all()


SCORE = "X-Spam-Status: No, score={} required=8 tests=HTML_MESSAGE"


async def test_suspect_spam_score_gets_rules_only_triage(client, user, raw, monkeypatch):
    calls = []
    await _endpoints(monkeypatch, calls)
    # The provider score alone moves nothing here (by default 4+ from a new sender goes to Spam).
    assert (await client.patch("/api/v1/me/settings", json={"spam_upstream_unknown_threshold": 0})).status_code == 200
    mid, cid = await _ingest(user, raw("Publication opportunity", frm="CFP <cfp@scopus-fast.example>", to=user.email,
                                       extra=SCORE.format("6.2"), body="Could you submit your paper by Friday?"),
                             roles=["inbox"], source="connector")
    rows = await _triage(mid, user)
    assert calls == []
    assert len(rows) == 1 and rows[0].reason.startswith("Not checked by a model: likely spam")
    assert "provider spam score 6.2" in rows[0].reason
    dec = (await client.get(f"/api/v1/messages/{mid}/decisions")).json()["decisions"]
    assert any(d["kind"] == "triage" and "likely spam" in d["reason"] for d in dec)
    # Rules still ran: the category is decided.
    async with sessionmaker()() as db:
        assert (await db.get(Message, mid)).intel_state == "done"

    # A low score goes to the model as before.
    mid2, _ = await _ingest(user, raw("Review request", frm="Colleague <c@uni.example>", to=user.email,
                                      extra=SCORE.format("0.4"), body="Could you review my draft by Friday?"),
                            roles=["inbox"], source="connector")
    assert await _triage(mid2, user) == []
    assert "chat" in calls

    # Stalwart's score counts the same way.
    calls.clear()
    mid3, _ = await _ingest(user, raw("Journal invitation", frm="Desk <desk@ijsr-online.example>", to=user.email,
                                      body="Could you send your manuscript by Friday?"), roles=["inbox"], source="connector")
    async with sessionmaker()() as db:
        (await db.get(Message, mid3)).spam_score = 7.5
        await db.commit()
    rows = await _triage(mid3, user)
    assert calls == [] and "(spam score 7.5, at least 5)" in rows[0].reason


async def test_spam_folder_and_blocked_senders_get_no_model(client, user, raw, monkeypatch):
    calls = []
    await _endpoints(monkeypatch, calls)
    mid, cid = await _ingest(user, raw("Win big", frm="Promo <win@lottery.example>", to=user.email,
                                       body="Could you claim your prize?"), roles=["junk"])
    rows = await _triage(mid, user)
    assert calls == [] and "(in Spam)" in rows[0].reason
    assert (await client.post("/api/v1/people/pest@nuisance.example/block")).status_code == 200
    mid2, _ = await _ingest(user, raw("Hello", frm="Pest <pest@nuisance.example>", to=user.email,
                                      body="Could you answer?"), roles=["inbox"])
    rows = await _triage(mid2, user)
    assert calls == [] and rows and "likely spam" in rows[0].reason


async def test_suspect_threshold_is_a_setting_and_not_spam_lifts_it(client, user, raw, monkeypatch):
    calls = []
    await _endpoints(monkeypatch, calls)
    assert (await client.patch("/api/v1/me/settings", json={"spam_suspect_score": 0,
                                                             "spam_upstream_unknown_threshold": 0})).status_code == 200
    mid, cid = await _ingest(user, raw("Call for papers", frm="CFP <cfp@journal-one.example>", to=user.email,
                                       extra=SCORE.format("6.5"), body="Could you submit by Friday?"),
                             roles=["inbox"], source="connector")
    assert await _triage(mid, user) == [] and calls  # 0 turns the check off

    assert (await client.patch("/api/v1/me/settings", json={"spam_suspect_score": 5})).status_code == 200
    calls.clear()
    mid2, cid2 = await _ingest(user, raw("Call for papers 2", frm="CFP <cfp@journal-two.example>", to=user.email,
                                         extra=SCORE.format("6.5"), body="Could you submit by Friday?"),
                               roles=["inbox"], source="connector")
    assert await _triage(mid2, user) and calls == []
    # "Not spam" (after a Report spam) lifts the check: the message is triaged again, models allowed.
    await _act(client, cid2, "spam")
    await _act(client, cid2, "not_spam")
    async with sessionmaker()() as db:
        job = (await db.execute(select(Job).where(Job.kind == "intel.retriage", Job.user_id == user.id))).scalars().all()
    assert any(mid2 in j.payload["message_ids"] for j in job)
    from mailr.intel.tasks import retriage_message

    async with sessionmaker()() as db:
        await retriage_message(db, await db.get(Message, mid2), await db.get(User, user.id))
        await db.commit()
        left = (await db.execute(select(LabelDecision).where(
            LabelDecision.message_id == mid2, LabelDecision.kind == "triage"))).scalars().all()
    assert left == [] and calls


# ---------------------------------------------------------------- learning from spam marks


CFP = ("Dear Researcher, we invite you to publish your paper in our Scopus indexed journal. "
       "Fast publication within 2 weeks. Submit your manuscript today.")


async def test_two_spam_marks_send_the_domain_to_spam(client, user, raw):
    roles = await _roles(client)
    cids = []
    for i, who in enumerate(("editor", "office")):
        _, c = await _ingest(user, raw(f"Scopus journal publication opportunity {i}", frm=f"Journal <{who}@ijrp-pub.example>",
                                       to=user.email, body=CFP), roles=["inbox"], source="connector")
        cids.append(c)
    await _act(client, cids[0], "spam")
    assert (await client.get("/api/v1/spam/domains")).json()["items"] == []  # one mark is not enough
    await _act(client, cids[1], "spam")
    # Each mark trains Stalwart's filter at once (spam.train, due now).
    async with sessionmaker()() as db:
        jobs = (await db.execute(select(Job).where(Job.kind == "spam.train", Job.user_id == user.id))).scalars().all()
    assert len(jobs) == 2 and all(j.payload["spam"] and j.run_at <= datetime.now(UTC) for j in jobs)
    items = (await client.get("/api/v1/spam/domains")).json()["items"]
    assert [i["key"] for i in items] == ["ijrp-pub.example"] and items[0]["marks"] == 2

    mid, _ = await _ingest(user, raw("Call for papers: Scopus journal", frm="Desk <cfp@ijrp-pub.example>", to=user.email,
                                     body=CFP), roles=["inbox"], source="connector")
    m = (await client.get(f"/api/v1/messages/{mid}")).json()
    assert m["label_ids"] == [roles["junk"]]
    dec = (await client.get(f"/api/v1/messages/{mid}/decisions")).json()["decisions"]
    assert any("you reported mail from ijrp-pub.example as spam" in d["reason"] for d in dec)

    # Remove (Settings → Spam): later mail stays in the inbox; only new marks count again.
    assert (await client.delete("/api/v1/spam/domains/ijrp-pub.example")).status_code == 200
    assert (await client.get("/api/v1/spam/domains")).json()["items"] == []
    mid, _ = await _ingest(user, raw("Another invitation", frm="Desk <cfp@ijrp-pub.example>", to=user.email, body=CFP),
                           roles=["inbox"], source="connector")
    assert roles["inbox"] in (await client.get(f"/api/v1/messages/{mid}")).json()["label_ids"]
    assert (await client.delete("/api/v1/spam/domains/ijrp-pub.example")).status_code == 404


async def test_not_spam_and_undo_take_the_domain_off(client, user, raw):
    cids = []
    for i in range(3):
        _, c = await _ingest(user, raw(f"Journal offer {i}", frm=f"J <j{i}@fastjournal.example>", to=user.email, body=CFP),
                             roles=["inbox"], source="connector")
        cids.append(c)
    await _act(client, cids[0], "spam")
    undo = (await _act(client, cids[1], "spam"))["undo"]
    assert [i["key"] for i in (await client.get("/api/v1/spam/domains")).json()["items"]] == ["fastjournal.example"]
    # Undo of the second mark: one mark left, the domain goes.
    r = await client.post("/api/v1/conversations/actions",
                          json={"ids": undo["ids"], "action": undo["action"], "labels": undo["labels"]})
    assert r.status_code == 200, r.text
    assert (await client.get("/api/v1/spam/domains")).json()["items"] == []
    await _act(client, cids[2], "spam")
    assert [i["key"] for i in (await client.get("/api/v1/spam/domains")).json()["items"]] == ["fastjournal.example"]
    # "Not spam" on mail from it takes it off at once.
    await _act(client, cids[2], "not_spam")
    assert (await client.get("/api/v1/spam/domains")).json()["items"] == []


async def test_shared_providers_and_correspondents_are_not_learned_by_domain(client, user, raw):
    roles = await _roles(client)
    for i in range(2):
        _, c = await _ingest(user, raw(f"Paper offer {i}", frm="Spammer <scopus.editor@gmail.com>", to=user.email,
                                       body=CFP), roles=["inbox"], source="connector")
        await _act(client, c, "spam")
    keys = [i["key"] for i in (await client.get("/api/v1/spam/domains")).json()["items"]]
    assert keys == ["scopus.editor@gmail.com"]
    mid, _ = await _ingest(user, raw("Hi", frm="Friend <friend.person@gmail.com>", to=user.email),
                           roles=["inbox"], source="connector")
    assert roles["inbox"] in (await client.get(f"/api/v1/messages/{mid}")).json()["label_ids"]

    # A domain where the user writes to someone is never learned.
    async with sessionmaker()() as db:
        from sqlalchemy import text

        await db.execute(text("INSERT INTO people (user_id, email, name, organization, received_count, sent_count, "
                              "notes, blocked) VALUES (:u, 'dean@myuni.example', '', '', 0, 3, '', false)"),
                         {"u": user.id})
        await db.commit()
    for i in range(2):
        _, c = await _ingest(user, raw(f"Mass mail {i}", frm=f"List <bulk{i}@myuni.example>", to=user.email),
                             roles=["inbox"], source="connector")
        await _act(client, c, "spam")
    assert "myuni.example" not in [i["key"] for i in (await client.get("/api/v1/spam/domains")).json()["items"]]
