"""Agent calendar/task tools against the dev Radicale: reads, defaults, and permission gating."""

from datetime import UTC, datetime, timedelta

import caldav
import pytest
from dev_services import RADICALE
from dev_services import radicale_up as _radicale_up
from helpers.agent_calendar import _connect_direct, _ctx

from mailr.agent.tools import ToolError, run_tool
from mailr.core import permissions
from mailr.db import sessionmaker

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


async def test_calendar_tools_read_defaults_and_gate(user):
    username, href = await _connect_direct(user.id)
    start = (datetime.now(UTC) + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    cal = caldav.DAVClient(url=RADICALE, username=username, password="x").calendar(url=href)
    cal.save_event(dtstart=start, dtend=start + timedelta(hours=1), summary="Budget review with Alice")
    cal.save_event(dtstart=start + timedelta(hours=3), dtend=start + timedelta(hours=4), summary="Gym")

    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        d = await run_tool(ctx, "get_calendar_defaults", {})
        # By the name the user knows it by, never its URL (calendar/describe.py).
        assert d.data["connected"] and d.data["defaults"]["calendar"] == "Personal"
        found = await run_tool(ctx, "search_events", {"text": "budget"})
        assert [e["summary"] for e in found.data["items"]] == ["Budget review with Alice"]
        allx = await run_tool(ctx, "search_events", {})
        assert {e["summary"] for e in allx.data["items"]} == {"Budget review with Alice", "Gym"}

        # calendar.create is `ask` by default: an approval card, nothing created yet.
        res = await run_tool(ctx, "create_event", {"summary": "Call with Bob", "start": start.isoformat()})
        assert res.data["status"] == "pending_approval" and res.pending["action"] == "calendar.create"
        assert res.pending["preview"]["kind"] == "event"
        from mailr.models import PendingAction

        pa = await db.get(PendingAction, res.data["approval_id"])
        assert pa.payload["calendar"] == href and pa.ctx["calendar"] == href  # the default calendar was used
        assert len((await run_tool(ctx, "search_events", {"text": "bob"})).data["items"]) == 0

        # Deleting a task always asks; invalid dates are rejected before anything is stored.
        with pytest.raises(ToolError):
            await run_tool(ctx, "create_task", {"summary": "x", "due": "not a date"})
        # (The task must exist where the caller can see it: constraints use its real list.)
        old = cal.save_todo(summary="Old")
        dt = await run_tool(ctx, "delete_task", {"uid": str(old.icalendar_component["uid"]), "summary": "Old"})
        assert dt.data["status"] == "pending_approval" and dt.pending["preview"]["danger"]
        assert (await db.get(PendingAction, dt.data["approval_id"])).ctx["list"] == href
        with pytest.raises(ToolError):
            await run_tool(ctx, "delete_task", {"uid": "abc", "summary": "Missing"})

        # tasks.create is `allow` by default: runs now through the registered executor.
        permissions._load_executors()
        assert "tasks.create" in permissions._EXECUTORS  # the executor is registered, never skipped
        # A title is plain text: the model's Markdown marks are left out.
        t = await run_tool(ctx, "create_task", {"summary": "**Send** the slides", "due": start.date().isoformat()})
        assert "status" not in t.data or t.data["status"] != "pending_approval"
        tasks = await run_tool(ctx, "search_tasks", {"text": "slides"})
        assert [x["summary"] for x in tasks.data["items"]] == ["Send the slides"]
