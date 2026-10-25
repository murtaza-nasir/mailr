"""A create an agent repeats (the model calls the tool again in a turn, or the turn is retried after
an error) finds the first attempt's event or task instead of making a second one."""

from datetime import UTC, datetime, timedelta

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.agent_calendar import _connect_direct
from helpers.agent_calendar_v2 import _allow, _ctx
from sqlalchemy import func, select

from mailr.agent import calendar_common
from mailr.agent.tools import run_tool
from mailr.calendar import writer
from mailr.db import sessionmaker
from mailr.models import CalObject

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


async def _count(user_id, summary):
    async with sessionmaker()() as db:
        return (await db.execute(select(func.count()).select_from(CalObject).where(
            CalObject.user_id == user_id, CalObject.summary == summary))).scalar_one()


async def test_repeated_event_and_task_creates_make_one_each(user):
    await _connect_direct(user.id)
    await _allow(user.id, "calendar.create")
    start = (datetime.now(UTC) + timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
    args = {"summary": "Site visit", "start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat()}
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        first = await run_tool(ctx, "create_event", args)
        await db.commit()
        again = await run_tool(ctx, "create_event", args)
        await db.commit()
        assert again.data["uid"] == first.data["uid"] and again.data.get("existing") is True
        t1 = await run_tool(ctx, "create_task", {"summary": "Book the van", "due": start.date().isoformat()})
        await db.commit()
        t2 = await run_tool(ctx, "create_task", {"summary": "Book the van", "due": start.date().isoformat()})
        await db.commit()
        assert t2.data["uid"] == t1.data["uid"]
        # Another chat asking for the same thing is a new request.
        other = await _ctx(db, user.id)
        third = await run_tool(other, "create_event", args)
        await db.commit()
        assert third.data["uid"] != first.data["uid"]
    assert await _count(user.id, "Site visit") == 2
    assert await _count(user.id, "Book the van") == 1


async def test_a_create_whose_answer_was_lost_is_found(user, monkeypatch):
    """The server stored the first attempt, but the mirror never heard of it (the answer was lost):
    the second attempt's PUT is refused as existing, and the stored item is returned."""
    await _connect_direct(user.id)
    await _allow(user.id, "calendar.create")
    start = (datetime.now(UTC) + timedelta(days=4)).replace(hour=9, minute=0, second=0, microsecond=0)
    args = {"summary": "Inspection", "start": start.isoformat()}
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        first = await run_tool(ctx, "create_event", args)
        await db.commit()
        # Forget it in the mirror only, as if the first answer had never arrived.
        await db.execute(CalObject.__table__.delete().where(CalObject.uid == first.data["uid"]))
        await db.commit()

        async def no_prior(*a, **kw):
            return None

        monkeypatch.setattr(writer, "created_before", no_prior)
        again = await run_tool(ctx, "create_event", args)
        await db.commit()
        assert again.data["uid"] == first.data["uid"]
    assert await _count(user.id, "Inspection") == 1


def test_window_keys(monkeypatch):
    class P:
        user_id, token = 7, None

    class Ctx:
        p, chat_session_id = P(), "chat-1"

    t = [1_000_000.0]
    monkeypatch.setattr("time.time", lambda: t[0])
    a = calendar_common.stable_uids(Ctx(), "event", {"summary": "X", "start": "2026-11-02T10:00"}, "summary", "start")
    t[0] += calendar_common.SAME_AS_WINDOW
    b = calendar_common.stable_uids(Ctx(), "event", {"summary": "X", "start": "2026-11-02T10:00"}, "summary", "start")
    assert a["uid"] in b["_same_as"] and a["uid"] != b["uid"]
    assert calendar_common.stable_uids(Ctx(), "event", {"uid": "given-by-client-1"}, "summary")["uid"] == "given-by-client-1"
