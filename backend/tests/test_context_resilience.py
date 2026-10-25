"""Failures that used to vanish or poison the session.

A failed vector query in the assistant's context build ran inside the caller's transaction
with no savepoint, so the next query failed with InFailedSQLTransaction and the real cause was
logged at debug level. It now runs in a savepoint and logs a warning."""

import logging

from helpers.core_flow import _ingest
from sqlalchemy import select

from mailr.agent import context
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.models import Message, User


async def test_failed_vector_query_leaves_the_transaction_usable(user, raw, monkeypatch, caplog):
    await _ingest(user, raw("Quarterly numbers", frm="Dana <dana@example.org>", body="The numbers for the quarter"),
                  roles=["inbox"])

    async def wrong_dimension(db, q, *, sensitive=False):
        return [0.1, 0.2, 0.3], "fake"  # the column holds 512 dimensions: the query fails in Postgres

    import mailr.intel.embed as embed
    monkeypatch.setattr(embed, "embed_query", wrong_dimension)
    async with sessionmaker()() as db:
        # A vector exists, so the semantic query really runs.
        m = (await db.execute(select(Message).where(Message.user_id == user.id))).scalars().first()
        m.embedding, m.embed_model = [0.0] * 512, "fake"  # the query's model, so the row is compared
        await db.flush()
        p = Principal(user=await db.get(User, user.id), scopes=set(ALL_SCOPES) - {"admin"})
        with caplog.at_level(logging.WARNING, logger=context.log.name):
            got = await context._related(db, p, ["dana@example.org"], None, "quarter numbers")
        assert len(got) == 1 and "Quarterly numbers" in str(got[0]["subject"])
        assert any("semantic neighbours failed" in r.getMessage() for r in caplog.records)
        # The session still works after the failure.
        assert (await db.execute(select(User.id).where(User.id == user.id))).scalar_one() == user.id


def test_unparsable_event_is_logged_with_its_uid(caplog):
    """An iCal parse error meant the event got no reminders, without a trace."""
    from datetime import UTC, datetime

    from mailr.calendar import planner

    with caplog.at_level(logging.WARNING, logger=planner.log.name):
        out = planner.plan_event("BEGIN:VCALENDAR\r\nnot ical at all", "uid-broken-1", {"events": True},
                                 datetime.now(UTC), UTC, set())
    assert out == []
    assert any("uid-broken-1" in r.getMessage() for r in caplog.records)


def test_unloadable_label_model_is_logged(caplog):
    """A joblib load error meant the label model stopped applying, without a trace."""
    from mailr.intel import learn

    with caplog.at_level(logging.WARNING, logger=learn.log.name):
        assert learn.loads(b"not a joblib blob", "user 7, label:42") is None
    assert any("label:42" in r.getMessage() for r in caplog.records)


async def test_a_long_thread_reads_its_newest_messages_and_fits_the_budget(user, raw, monkeypatch):
    """A thread longer than THREAD_MAX: the newest messages are read (the older ones listed by id
    only), the bundle is cut to its budget, and the bundle is serialised a few times, not twice
    for every message dropped."""
    import uuid

    monkeypatch.setattr(context, "THREAD_MAX", 8)
    root = f"<long-{uuid.uuid4().hex[:8]}@example.org>"
    ids = []
    for i in range(12):
        mid, conv = await _ingest(user, raw(f"Re: Long thread {i}", frm="Dana <dana@example.org>", body="word " * 400,
                                            msgid=root if i == 0 else None, irt=None if i == 0 else root,
                                            date=f"Mon, {10 + i} Mar 2025 09:00:00 +0000"), roles=["inbox"])
        ids.append(mid)
    calls = {"n": 0}
    real = context._size

    def counted(obj):
        calls["n"] += 1
        return real(obj)

    monkeypatch.setattr(context, "_size", counted)
    async with sessionmaker()() as db:
        p = Principal(user=await db.get(User, user.id), scopes=set(ALL_SCOPES) - {"admin"})
        b = await context.build(db, p, conversation_id=conv, budget_tokens=1500)
    shown = [m["message_id"] for m in b["thread"]["messages"]]
    assert b["truncated"] and shown and shown[-1] == ids[-1]
    assert b["thread"]["omitted_message_ids"][:4] == ids[:4]  # older than the newest eight
    assert set(shown) | set(b["thread"]["omitted_message_ids"]) == set(ids)
    assert real(b) <= 1500 * context.CHARS_PER_TOKEN
    assert calls["n"] < 30, calls
