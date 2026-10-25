"""Automated mail threads only by reply headers: job alerts with one
recurring subject were glued into one conversation by the subject fallback."""

from conftest import mail_date
from helpers.core_flow import _ingest
from sqlalchemy import func, select, text, update

from mailr.core import rethread_automated, threading
from mailr.db import sessionmaker
from mailr.intel import llm, summarize
from mailr.models import AuditLog, Conversation, Message, User

ALERT = "8 New Jobs - My Job Alert"
UNSUB = "List-Unsubscribe: <https://jobs.example/unsubscribe>"
DATES = (mail_date(-6, "08:00"), mail_date(-2, "08:00"), mail_date(12, "08:00"))


async def _alerts(user, raw, **kw):
    return [await _ingest(user, raw(ALERT, frm="Job Alerts <alerts@jobs.example>", date=d, extra=UNSUB), **kw)
            for d in DATES]


async def test_recurring_alerts_stay_separate(user, raw):
    convs = [c for _, c in await _alerts(user, raw)]
    assert len(set(convs)) == 3
    # A header-only bulk signal (Precedence) from an ordinary-looking address counts too.
    a = await _ingest(user, raw("Weekly digest", frm="Digest <digest@club.example>", extra="Precedence: bulk"))
    b = await _ingest(user, raw("Weekly digest", frm="Digest <digest@club.example>", extra="Precedence: bulk",
                                date=mail_date(7)))
    assert a[1] != b[1]


async def test_provider_thread_id_does_not_glue_alerts(user, raw):
    one = await _ingest(user, raw(ALERT, frm="Job Alerts <alerts@jobs.example>", msgid="<gm1@jobs.example>",
                                  date=DATES[0], extra=UNSUB), upstream={"thread_key": "gm:42"})
    two = await _ingest(user, raw(ALERT, frm="Job Alerts <alerts@jobs.example>", date=DATES[1], extra=UNSUB),
                        upstream={"thread_key": "gm:42"})
    assert one[1] != two[1]
    # A message with reply headers still joins by the provider thread id.
    three = await _ingest(user, raw("Re: " + ALERT, frm="Bob <bob@example.org>", irt="<missing@elsewhere>",
                                    date=DATES[2]), upstream={"thread_key": "gm:42"})
    assert three[1] in (one[1], two[1])


async def test_reply_to_newsletter_threads(user, raw):
    news = raw("The Friday Letter", frm="Letters <news@paper.example>", msgid="<fri1@paper.example>",
               extra="List-Id: <letters.paper.example>")
    _, c1 = await _ingest(user, news)
    _, c2 = await _ingest(user, raw("Re: The Friday Letter", frm="Bob <bob@example.org>", irt="<fri1@paper.example>",
                                    date=mail_date(1)))
    # A list follow-up that names the original (References) threads as well.
    _, c3 = await _ingest(user, raw("The Friday Letter", frm="Letters <news@paper.example>",
                                    irt="<fri1@paper.example>", extra="List-Id: <letters.paper.example>",
                                    date=mail_date(2)))
    assert c1 == c2 == c3


async def test_person_without_headers_still_threads_by_subject(user, raw):
    _, c1 = await _ingest(user, raw("Field trip forms"))
    _, c2 = await _ingest(user, raw("Field trip forms", date=mail_date(2)))
    assert c1 == c2
    # ...but never onto a run of alerts with the same subject.
    alerts = [c for _, c in await _alerts(user, raw)]
    _, c3 = await _ingest(user, raw(ALERT, frm="Job Alerts <alerts@jobs.example>",
                                    date=mail_date(13, "08:00")))
    assert c3 not in alerts


async def _glue(uid: int, mids: list[int], *, pinned: bool = False) -> int:
    """Put messages into one conversation, as the old subject fallback did."""
    async with sessionmaker()() as db:
        target = (await db.get(Message, mids[0])).conversation_id
        old = set((await db.execute(select(Message.conversation_id).where(Message.id.in_(mids)))).scalars())
        await db.execute(update(Message).where(Message.id.in_(mids)).values(conversation_id=target))
        await db.execute(update(Conversation).where(Conversation.id == target).values(pinned=pinned, muted=True))
        await threading.refresh(db, uid, list(old))
        await db.commit()
        return target


async def test_repair_splits_only_glued_automated_conversations(client, user, raw, monkeypatch):
    labels = {lb["role"]: lb["id"] for lb in (await client.get("/api/v1/labels")).json() if lb["role"]}
    alerts = [m for m, _ in await _alerts(user, raw)]
    glued = await _glue(user.id, alerts)
    await client.post("/api/v1/messages/actions", json={"ids": [alerts[1]], "action": "trash"})
    # Controls: a newsletter with a reply, a person's thread, and a pinned (manual) merge of alerts.
    _, news = await _ingest(user, raw("The Friday Letter", frm="Letters <news@paper.example>",
                                      msgid="<fri9@paper.example>", extra="List-Id: <letters.paper.example>"))
    await _ingest(user, raw("The Friday Letter", frm="Letters <news@paper.example>", irt="<fri9@paper.example>",
                            extra="List-Id: <letters.paper.example>", date=mail_date(2)))
    person = [m for m, _ in [await _ingest(user, raw("Budget", frm="Ann <ann@example.org>")),
                             await _ingest(user, raw("Budget", frm="Ann <ann@example.org>",
                                                     date=mail_date(2)))]]
    person_conv = await _glue(user.id, person)
    pinned_alerts = [m for m, _ in [
        await _ingest(user, raw("Price drop", frm="Shop <noreply@shop.example>")),
        await _ingest(user, raw("Price drop", frm="Shop <noreply@shop.example>", date=mail_date(2)))]]
    pinned = await _glue(user.id, pinned_alerts, pinned=True)

    # The glued alerts get no summary (no model call).
    async def no_chat(*a, **kw):
        raise AssertionError("no model call for a run of alerts")
    monkeypatch.setattr(llm, "chat", no_chat)
    async with sessionmaker()() as db:
        out = await summarize.summarize(db, await db.get(User, user.id), glued, force=True)
    assert out["available"] is False

    dry = await rethread_automated.run(user.id, dry_run=True)
    assert [f.conversation_id for f in dry.found] == [glued]
    assert "would split 1 conversation" in rethread_automated.describe(dry, dry_run=True)
    async with sessionmaker()() as db:
        assert len(set((await db.execute(select(Message.conversation_id).where(
            Message.id.in_(alerts)))).scalars())) == 1

    async with sessionmaker()() as db:
        cursor = (await db.execute(text("SELECT coalesce(max(id),0) FROM change_log WHERE user_id=:u"),
                                   {"u": user.id})).scalar_one()
    rep = await rethread_automated.run(user.id, dry_run=False)
    assert rep.new_conversations == 2 and rep.messages_moved == 2
    async with sessionmaker()() as db:
        msgs = {m.id: m for m in (await db.execute(select(Message).where(Message.id.in_(alerts)))).scalars()}
        convs = {m.conversation_id for m in msgs.values()}
        assert len(convs) == 3 and msgs[alerts[0]].conversation_id == glued
        assert labels["trash"] in msgs[alerts[1]].label_ids and labels["inbox"] in msgs[alerts[2]].label_ids
        for c in (await db.execute(select(Conversation).where(Conversation.id.in_(convs)))).scalars():
            assert c.message_count == 1 and c.muted and not c.pinned
        others = set((await db.execute(select(Message.conversation_id).where(
            Message.id.in_(person + pinned_alerts)))).scalars())
        assert others == {person_conv, pinned}
        assert (await db.get(Conversation, news)).message_count == 2
        changes = (await db.execute(text("SELECT entity, entity_id FROM change_log WHERE user_id=:u AND id>:c"),
                                    {"u": user.id, "c": cursor})).all()
        assert {("message", alerts[1]), ("message", alerts[2])} <= set(changes)
        assert ("conversation", glued) in set(changes)
        audits = (await db.execute(select(func.count()).select_from(AuditLog).where(
            AuditLog.user_id == user.id, AuditLog.action == "threading.rethread_automated"))).scalar_one()
        assert audits == 1

    # Idempotent: nothing left to do, no second audit row.
    again = await rethread_automated.run(user.id, dry_run=False)
    assert not again.found and again.messages_moved == 0
    async with sessionmaker()() as db:
        assert (await db.execute(select(func.count()).select_from(AuditLog).where(
            AuditLog.user_id == user.id, AuditLog.action == "threading.rethread_automated"))).scalar_one() == 1


async def test_single_message_gets_no_summary(client, user, raw, monkeypatch):
    async def no_chat(*a, **kw):
        raise AssertionError("no model call for one message")
    monkeypatch.setattr(llm, "chat", no_chat)
    monkeypatch.setattr(llm, "available", lambda *a, **kw: _true())
    _, conv = await _ingest(user, raw("Lunch on Friday?", frm="Ann <ann@example.org>"))
    r = (await client.get(f"/api/v1/conversations/{conv}/summary", params={"wait": "true"})).json()
    assert r["status"] == "unavailable"
    r = (await client.get(f"/api/v1/conversations/{conv}/summary")).json()
    assert r["status"] == "unavailable"


async def _true():
    return True
