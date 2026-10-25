# ACT-SURFACES: assistant
"""Deadlines next week: a search that finds nothing must say why.

Asked about deadlines next week on Monday 5 October 2026, Ambrose ran "Searching your tasks" until the
step limit, each repeat answered "already done above", and then said there were no open tasks due in the
next week and no events "next week (Oct 12–19)". Three tasks were due that week, and the 885 exam was on
Monday 19 October. The causes, each covered here:

- words in a task search ("deadline") match titles and notes only, so the search came back empty with no
  reason given; an operator the search cannot read ("due:next-week") matched nothing, silently;
- the calendar range "next week" was described as ending "Mon 19 Oct 2026, 00:00", retold as "Oct 12–19";
- a repeated call got only "already done above" (tests/test_assistant_knowledge.py covers the repeat cap).
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import caldav
import pytest
from dev_services import RADICALE
from dev_services import radicale_up as _radicale_up
from helpers.agent_calendar import _connect_direct, _ctx

from mailr.agent import when
from mailr.agent.tools import ToolError, run_tool
from mailr.calendar import tz as tzmod
from mailr.db import sessionmaker

CHI = ZoneInfo("America/Chicago")
ASKED = datetime(2026, 10, 5, 13, 2, tzinfo=CHI)  # the case this test reproduces


def test_next_week_reads_as_the_days_it_covers(monkeypatch):
    monkeypatch.setattr(when, "now", lambda: ASKED.astimezone(UTC))
    a, b = when.window("next week", CHI)
    assert when.range_text(a, b, CHI) == "Mon 12 Oct 2026 – Sun 18 Oct 2026, whole days"
    a, b = when.window("tomorrow", CHI)
    assert when.range_text(a, b, CHI) == "Tue 6 Oct 2026, whole day"
    a, b = when.local_range("2026-10-12", "2026-10-19", CHI)
    assert when.range_text(a, b, CHI) == "Mon 12 Oct 2026 – Mon 19 Oct 2026, whole days"
    a, b = when.window("tonight", CHI)  # not whole days: times
    assert when.range_text(a, b, CHI) == "Mon 5 Oct 2026, 17:00 CDT – Tue 6 Oct 2026, 06:00 CDT"


@pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")
async def test_task_searches_say_why_they_are_empty(user):
    username, href = await _connect_direct(user.id)
    cal = caldav.DAVClient(url=RADICALE, username=username, password="x").calendar(url=href)
    now = datetime.now(UTC).replace(microsecond=0)
    for d, s in ((-1, "Email the students"), (1, "Look at options"), (5, "Record the class"), (7, "Pause in convos"),
                 (40, "Renew the domain")):
        cal.save_todo(summary=s, due=now + timedelta(days=d))
    week = ["Email the students", "Look at options", "Record the class", "Pause in convos"]
    before = (now + timedelta(days=8)).date().isoformat()
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        # A due range alone finds them.
        r = await run_tool(ctx, "search_tasks", {"due_before": before})
        assert [x["summary"] for x in r.data["items"]] == week
        # With a word no title holds: empty, but the model is told why and sees what the filters find.
        r = await run_tool(ctx, "search_tasks", {"text": "deadline", "due_before": before})
        assert r.data["items"] == []
        assert "'deadline'" in r.data["note"] and "titles and notes only" in r.data["note"]
        assert [x["summary"] for x in r.data["without_words"]] == week
        r = await run_tool(ctx, "search_tasks", {"text": "zzqx"})
        assert r.data["items"] == [] and "without_words" in r.data
        # An operator the search cannot read is refused, with the forms it takes.
        for bad in ("due:next-week", "due:<+7d", "is:pending", "before:soon"):
            with pytest.raises(ToolError) as e:
                await run_tool(ctx, "search_tasks", {"text": bad})
            assert bad in str(e.value) and "`when`" in str(e.value)
        with pytest.raises(ToolError) as e:
            await run_tool(ctx, "list_tasks", {"q": "due:later"})
        assert "due:later" in str(e.value)
        with pytest.raises(ToolError) as e:
            await run_tool(ctx, "search_tasks", {"due_before": "next week"})
        assert "`when`" in str(e.value)
        # Readable operators still work.
        r = await run_tool(ctx, "search_tasks", {"text": f"due:<{before}"})
        assert [x["summary"] for x in r.data["items"]] == week
        # `when` gives the tasks due in that range, in the user's zone.
        a, b = when.window("next week", await tzmod.user_zone(db, user.id))
        expect = [s for d, s in ((-1, week[0]), (1, week[1]), (5, week[2]), (7, week[3]), (40, "Renew the domain"))
                  if a <= now + timedelta(days=d) < b]
        r = await run_tool(ctx, "search_tasks", {"when": "next week"})
        assert [x["summary"] for x in r.data["items"]] == expect
        with pytest.raises(ToolError):
            await run_tool(ctx, "search_tasks", {"when": "someday"})
        # Calendar searches refuse unreadable operators too.
        for tool, arg in (("search_events", "text"), ("search_calendar", "q"), ("list_calendar", "q")):
            with pytest.raises(ToolError) as e:
                await run_tool(ctx, tool, {arg: "due:next-week"})
            assert "due:next-week" in str(e.value)
        # A week of events is described by its days.
        r = await run_tool(ctx, "search_events", {"when": "next week"})
        assert r.data["range"].endswith("whole days")


@pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")
async def test_an_empty_today_is_not_an_empty_task_list(user):
    """/today answered "Nothing due today… Task list is empty." while a task was due on Friday: an
    empty selection names the next task due, and only a list with no open task reads as empty."""
    from mailr.agent import commands

    assert "call my task list empty only when it has no open tasks" in commands.request_for(
        commands._BY_NAME["today"], "")
    username, href = await _connect_direct(user.id)
    cal = caldav.DAVClient(url=RADICALE, username=username, password="x").calendar(url=href)
    now = datetime.now(UTC).replace(microsecond=0)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        r = await run_tool(ctx, "list_tasks", {"smart": "today"})
        assert r.data["items"] == [] and r.data["note"] == "The task list has no open tasks."
        cal.save_todo(summary="Send the quarterly figures", due=now + timedelta(days=3))
        cal.save_todo(summary="Renew the domain", due=now + timedelta(days=40))
        for args in ({"smart": "today"}, {"smart": "overdue"}):
            r = await run_tool(ctx, "list_tasks", args)
            assert r.data["items"] == [] and "not empty" in r.data["note"], args
            assert r.data["next_due"]["summary"] == "Send the quarterly figures"
        r = await run_tool(ctx, "search_tasks", {"when": "today"})
        assert r.data["items"] == [] and r.data["next_due"]["summary"] == "Send the quarterly figures"
        # A selection that finds tasks, or a list with words, adds nothing.
        r = await run_tool(ctx, "list_tasks", {"smart": "upcoming"})
        assert len(r.data["items"]) == 2 and "next_due" not in r.data and "note" not in r.data
        r = await run_tool(ctx, "list_tasks", {"smart": "today", "q": "zzqx"})
        assert "next_due" not in r.data
