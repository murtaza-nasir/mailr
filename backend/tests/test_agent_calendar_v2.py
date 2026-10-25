# ACT-SURFACES: assistant
"""The assistant's calendar and task tools against the dev Radicale.

Nothing is sent: invitations and cancellations only become outbox rows. Covered, with their action
IDs (the `assistant` cells of the catalogue):
- events: event.create, event.edit, event.set_color, event.add_video, event.move_calendar,
  event.invite and event.uninvite (one calendar.invite approval covers the write and the mail; a
  new recipient always asks), event.cancel and event.delete (calendar.cancel), event.find_time,
  event.search, task.search;
- tasks: task.create, task.subtask_add, task.set_start, task.set_repeat, task.set_priority, task.tag,
  task.remind_at, task.complete (a repeating task advances and the answer has `next`), task.skip,
  task.move_list, task.reorder, task.reparent, task.bulk_complete, task.bulk_reschedule, task.bulk_move,
  task.bulk_tag, task.bulk_priority, task.bulk_delete (asks);
- collections: list.create (allowed), list.rename and list.delete (ask), calendar.create,
  calendar.rename, calendar.color, calendar.delete (calendar.manage asks);
- screen context ("this meeting", "these tasks"), local-only calendars hidden from a hosted model,
  and [[event:…]] / [[task:…]] citations that resolve or are dropped.
"""

import uuid
from datetime import UTC, datetime, timedelta

import caldav
import pytest
from dev_services import RADICALE
from dev_services import radicale_up as _radicale_up
from helpers.agent_calendar import _connect_direct
from helpers.agent_calendar_v2 import _allow, _ctx, _identity
from helpers.calendar import _connect
from sqlalchemy import select

from mailr.agent import screen
from mailr.agent.tools import ToolError, run_tool
from mailr.core import permissions
from mailr.db import sessionmaker
from mailr.intel import citations
from mailr.models import CalObject, Connector, EventLink, Outbox, PendingAction, User

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")

DANA, EVAN = "dana@guest.example", "evan@guest.example"


async def _decide(user_id, pid):
    async with sessionmaker()() as db:
        await permissions.decide(db, await db.get(User, user_id), pid, "approve", "once")
        await db.commit()
    await permissions.execute_job({"pending_id": pid}, None)
    async with sessionmaker()() as db:
        return await db.get(PendingAction, pid)


async def _outbox(user) -> list[Outbox]:
    async with sessionmaker()() as db:
        return list((await db.execute(select(Outbox).where(Outbox.user_id == user.id).order_by(Outbox.id))).scalars())


def _start(days=3, hour=15):
    return (datetime.now(UTC) + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


async def test_event_tools_fields_search_find_time_and_citations(client, user):
    """event.create with the new fields (zone, repeat, reminders, colour, show as free, private,
    a video link: event.add_video), event.set_color on one occurrence, event.search and
    task.search through search_calendar, event.find_time and free/busy, and citations."""
    await _connect(client)
    await _allow(user.id, "calendar.create", "calendar.update")
    start = _start()
    # find_time below looks for working-hours slots over two days: start on Monday to Thursday so
    # those days are never a weekend (the test failed every Friday evening and weekend, US time).
    while start.weekday() >= 4:
        start += timedelta(days=1)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = await run_tool(ctx, "create_event", {
            "summary": "Standup", "start": start.isoformat(), "end": (start + timedelta(minutes=30)).isoformat(),
            "timezone": "Europe/London", "rrule": "FREQ=DAILY;COUNT=3", "reminders": [10], "color": "#ff0000",
            "transparency": "free", "visibility": "private", "conference": True})
        ev = res.data
        assert ev["rrule"] == "FREQ=DAILY;COUNT=3" and ev["tz"] == "Europe/London" and ev["reminders"] == [10]
        assert ev["transparent"] and ev["class"] == "PRIVATE" and ev["color"] == "#ff0000"
        assert ev["url"].startswith("https://meet.jit.si/mailr-")
        assert ev["cite"] == f"[[event:{ev['uid']}@{ev['occurrence']}]]"
        busy = await run_tool(ctx, "create_event", {"summary": "Budget review", "start": (start + timedelta(hours=2)).isoformat(),
                                                    "end": (start + timedelta(hours=3)).isoformat()})

        listed = await run_tool(ctx, "list_calendar", {"from": start.date().isoformat(),
                                                       "to": (start + timedelta(days=4)).date().isoformat()})
        stand = [e for e in listed.data["items"] if e["summary"] == "Standup"]
        assert len(stand) == 3 and listed.data["tz"]
        second = stand[1]
        assert second["cite"] == f"[[event:{ev['uid']}@{second['occurrence']}]]"

        # event.set_color on one occurrence (scope this).
        upd = await run_tool(ctx, "update_event", {"uid": ev["uid"], "occurrence": second["occurrence"], "scope": "this",
                                                   "color": "#00ff00"})
        assert upd.data["uid"] == ev["uid"]

        found = await run_tool(ctx, "search_calendar", {"q": "budget", "kind": "event"})
        assert [i["summary"] for i in found.data["items"]] == ["Budget review"]
        assert found.data["items"][0]["cite"].startswith(f"[[event:{busy.data['uid']}@")

        # Free/busy: the opaque event is busy, "show as free" is not.
        fb = await run_tool(ctx, "get_free_busy", {"from": start.date().isoformat(),
                                                   "to": (start + timedelta(days=1)).date().isoformat()})
        assert fb.data["busy"] and all(b["start"] != start.isoformat() for b in fb.data["busy"])
        slots = await run_tool(ctx, "find_time", {"duration": 45, "from": start.date().isoformat(),
                                                  "to": (start + timedelta(days=2)).date().isoformat()})
        assert slots.data["slots"] and slots.data["tz"]
        for s in slots.data["slots"]:  # never over the busy hour
            assert not (s["start"] < (start + timedelta(hours=3)).isoformat() and s["end"] > (start + timedelta(hours=2)).isoformat())

        # Citations: an event occurrence and a task resolve to chips; unknown ids are dropped.
        t = await run_tool(ctx, "create_task", {"summary": "Send the agenda", "due": start.date().isoformat()})
        text = (f"Standup {second['cite']} and the agenda {t.data['cite']}; not [[event:nope-{uuid.uuid4().hex}@2026-10-01]] "
                f"nor [[task:missing-uid-123]]. Old {second['cite']}.")
        out, cites = await citations.resolve(db, ctx.p, text)
        assert [c["kind"] for c in cites] == ["event", "task"]
        ce, ct = cites
        assert ce["uid"] == ev["uid"] and ce["occurrence"] == second["occurrence"] and ce["title"] == "Standup"
        assert ce["starts_at"] == second["start"] and ce["color"] == "#ff0000"
        assert ce["url"].endswith(f"/calendar/event/{ev['uid'].replace('@', '%40')}/{second['occurrence'].replace(':', '%3A').replace('+', '%2B')}")
        assert ct["title"] == "Send the agenda" and ct["url"].endswith("/tasks/task/" + t.data["uid"].replace("@", "%40"))
        assert out.count("(mailr-cite:1)") == 2 and "(mailr-cite:2)" in out and "nope-" not in out and "missing" not in out
        # A mangled occurrence still cites the series, at a real occurrence.
        _, c2 = await citations.resolve(db, ctx.p, f"[[event:{ev['uid']}@2001-01-01T00:00:00Z]]")
        assert c2[0]["occurrence"] in {e["occurrence"] for e in stand}


async def test_invite_gating_one_approval_writes_and_queues_the_invitation(client, user):
    """event.invite: an assistant event with guests is one calendar.invite approval; approving it
    saves the event and queues the iTIP REQUEST. A new recipient asks even when every class is
    allowed (event.uninvite and a new guest on update ask the same way)."""
    await _connect(client)
    me = await _identity(user)
    start = _start(4)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = await run_tool(ctx, "create_event", {"summary": "Planning", "start": start.isoformat(),
                                                   "attendees": [DANA, {"email": EVAN, "name": "Evan"}]})
        assert res.data["status"] == "pending_approval" and res.pending["action"] == "calendar.invite"
        lines = res.pending["preview"]["lines"]  # the readable fallback (agent/present.py)
        assert any(ln.startswith("Invitations to: ") and DANA in ln and EVAN in ln for ln in lines)
        assert f"From: {me}" in lines
        assert await _outbox(user) == []
        assert (await run_tool(ctx, "search_events", {"text": "planning", "from": start.date().isoformat(),
                                                      "to": (start + timedelta(days=1)).date().isoformat()})).data["items"] == []
        pid = res.data["approval_id"]
    pa = await _decide(user.id, pid)
    assert pa.status == "executed", pa.result
    [ob] = await _outbox(user)
    assert ob.status == "queued" and sorted(ob.recipients) == [DANA, EVAN]
    assert ob.payload["subject"] == "Invitation: Planning"
    uid = pa.result["uid"]

    # With calendar.* allowed, a first message to a new address still asks (hard rule).
    await _allow(user.id, "calendar.*")
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        add = await run_tool(ctx, "update_event", {"uid": uid, "attendees": [DANA, EVAN, "fay@guest.example"]})
        assert add.pending["action"] == "calendar.invite"
        assert add.pending["preview"]["reason"].startswith("hard rule")
        # Without guest mail (send_updates false) the allowed write runs at once.
        quiet = await run_tool(ctx, "update_event", {"uid": uid, "location": "Room 2", "send_updates": False})
        assert quiet.data["location"] == "Room 2" and "invites" not in quiet.data
    assert len(await _outbox(user)) == 1


async def test_cancel_and_delete_with_guests_ask_calendar_cancel(client, user):
    """event.cancel: a cancel of an event the user organizes, with guests, is a calendar.cancel
    approval; executing it marks the event cancelled and queues CANCEL. event.delete with guests is
    gated the same way. A guest cannot cancel."""
    await _connect(client)
    await _identity(user)
    start = _start(5)
    r = await client.post("/api/v1/calendar/events", json={"summary": "Retro", "start": start.isoformat(),
                                                           "attendees": [DANA]})
    assert r.status_code == 200, r.text
    uid = r.json()["uid"]
    r2 = await client.post("/api/v1/calendar/events", json={"summary": "Offsite", "start": (start + timedelta(days=1)).isoformat(),
                                                            "attendees": [EVAN]})
    uid2 = r2.json()["uid"]
    n = len(await _outbox(user))
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = await run_tool(ctx, "cancel_event", {"uid": uid, "message": "**Moving** this to email."})
        assert res.pending["action"] == "calendar.cancel"
        assert "Cancellation to: " + DANA in res.pending["preview"]["lines"]
        dl = await run_tool(ctx, "delete_event", {"uid": uid2, "summary": "Offsite"})
        assert dl.pending["action"] == "calendar.cancel"
        # Without a summary from the agent, the approval names the event, not its uid.
        dl2 = await run_tool(ctx, "delete_event", {"uid": uid2})
        assert "Offsite" in dl2.pending["preview"]["title"] and uid2 not in dl2.pending["preview"]["title"]
        pid = res.data["approval_id"]
    assert len(await _outbox(user)) == n
    pa = await _decide(user.id, pid)
    assert pa.status == "executed", pa.result
    assert pa.result["status"] == "CANCELLED" and pa.result["invites"]["recipients"] == [DANA]
    cancel = (await _outbox(user))[-1]
    assert cancel.payload["subject"] == "Cancelled: Retro" and "Moving this to email." in cancel.payload["text"]
    assert "**" not in cancel.payload["text"]  # the message is plain text: no Markdown marks

    # An event someone else organizes: the assistant cannot cancel it.
    username = await _connect(client)
    other = caldav.DAVClient(url=RADICALE, username=username, password="x").principal().calendars()[0]
    s = start + timedelta(days=2)
    other.save_event(f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\nBEGIN:VEVENT\r\nUID:theirs-{uuid.uuid4().hex}\r\n"
                     f"DTSTAMP:20260920T100000Z\r\nDTSTART:{s.strftime('%Y%m%dT%H%M%SZ')}\r\nSUMMARY:Their meeting\r\n"
                     "ORGANIZER:mailto:alice@acme.example\r\nATTENDEE:mailto:someone@acme.example\r\nEND:VEVENT\r\n"
                     "END:VCALENDAR\r\n")
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        theirs = await run_tool(ctx, "search_events", {"text": "their", "from": s.date().isoformat(),
                                                       "to": (s + timedelta(days=1)).date().isoformat()})
        [item] = theirs.data["items"]
        with pytest.raises(ToolError, match="only the organiser"):
            await run_tool(ctx, "cancel_event", {"uid": item["uid"]})


async def test_task_tools_repeat_moves_bulk_and_lists(client, user, raw):
    """task.create (start, repeat, tags, priority, relative and absolute reminders: task.set_start,
    task.set_repeat, task.tag, task.set_priority, task.remind_at), list_tasks, task.complete on a
    repeating task (it advances; task.skip moves it on too), task.subtask_add, task.reparent,
    task.move_list into a new list (list.create is allowed), bulk complete / reschedule / tag /
    priority / move, and the asking parts: list.rename, list.delete, task.bulk_delete. A standing
    order's errand links the task to its email (mail.to_task)."""
    from helpers.core_flow import _ingest

    await _connect(client)
    due = (datetime.now(UTC) + timedelta(days=1)).date()
    remind = (datetime.now(UTC) + timedelta(hours=20)).replace(microsecond=0)
    _, cid = await _ingest(user, raw("Water bill due", frm="City <bills@city.example>"))
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        t = (await run_tool(ctx, "create_task", {
            "summary": "Water the plants", "due": due.isoformat(), "start": (due - timedelta(days=1)).isoformat(),
            "rrule": "FREQ=DAILY;COUNT=5", "tags": ["home"], "priority": 1,
            "reminders": [30, remind.isoformat()], "conversation_id": cid})).data
        assert t["recurring"] and t["categories"] == ["home"] and t["priority"] == 1 and t["reminders"] == [30]
        assert len(t["reminders_at"]) == 1 and t["cite"] == f"[[task:{t['uid']}]]"
        link = (await db.execute(select(EventLink).where(EventLink.user_id == user.id, EventLink.uid == t["uid"]))).scalar_one()
        assert link.conversation_id == cid and link.kind == "task"

        tagged = await run_tool(ctx, "list_tasks", {"tag": "home"})
        assert [x["uid"] for x in tagged.data["items"]] == [t["uid"]] and tagged.data["items"][0]["cite"]
        today = await run_tool(ctx, "list_tasks", {"smart": "today"})
        assert t["uid"] in [x["uid"] for x in today.data["items"]]

        # task.complete on a repeating task advances it: through the executor.
        done = await run_tool(ctx, "complete_task", {"uid": t["uid"]})
        assert done.data["repeating"] and done.data["next"] == (due + timedelta(days=1)).isoformat()
        assert done.data["due"] == (due + timedelta(days=1)).isoformat() and not done.data["completed"]
        skipped = await run_tool(ctx, "complete_task", {"uid": t["uid"], "skip": True})
        assert skipped.data["due"] == (due + timedelta(days=2)).isoformat()

        # Subtasks, moves, a new list (allowed), and bulk actions.
        a = (await run_tool(ctx, "create_task", {"summary": "Buy soil"})).data
        b = (await run_tool(ctx, "create_task", {"summary": "Repot", "parent": a["uid"]})).data
        assert b["parent"] == a["uid"]
        top = await run_tool(ctx, "move_task", {"uid": b["uid"], "parent": ""})
        assert not top.data.get("parent")
        # task.reorder: before a sibling in the manual order.
        c = (await run_tool(ctx, "create_task", {"summary": "Water the beds"})).data
        placed = await run_tool(ctx, "move_task", {"uid": c["uid"], "before": a["uid"]})
        assert placed.data["uid"] == c["uid"]
        manual = [t["summary"] for t in (await client.get("/api/v1/tasks", params={"sort": "manual"})).json()["items"]]
        assert manual.index("Water the beds") < manual.index("Buy soil"), manual
        lst = await run_tool(ctx, "manage_lists", {"action": "create", "name": "Garden"})
        assert lst.data["name"] == "Garden" and lst.data["tasks"] and "url" not in lst.data
        # Lists go by name, in and out (no URL reaches the model: calendar/describe.py).
        moved = await run_tool(ctx, "move_task", {"uid": a["uid"], "list": "Garden"})
        assert moved.data["list"] == "Garden"
        bulk = await run_tool(ctx, "bulk_tasks", {"op": "priority", "uids": [a["uid"], b["uid"]], "args": {"priority": 5}})
        assert sorted(bulk.data["ok"]) == sorted([a["uid"], b["uid"]])
        for op, args in (("tag", {"tag": "garden"}), ("reschedule", {"due": due.isoformat()}),
                         ("move", {"list": "garden"}), ("complete", {})):
            r = await run_tool(ctx, "bulk_tasks", {"op": op, "uids": [b["uid"]], "args": args})
            assert r.data["ok"] == [b["uid"]], (op, r.data)

        # The parts that ask: renaming or deleting a list, a bulk delete.
        ren = await run_tool(ctx, "manage_lists", {"action": "rename", "list": "Garden", "name": "Yard"})
        assert ren.pending["action"] == "tasks.lists" and ren.pending["preview"]["reason"].startswith("renaming")
        rm = await run_tool(ctx, "manage_lists", {"action": "delete", "list": "Garden"})
        assert rm.pending["action"] == "tasks.lists" and rm.pending["preview"]["danger"]
        bd = await run_tool(ctx, "bulk_tasks", {"op": "delete", "uids": [a["uid"], b["uid"]]})
        assert bd.pending["action"] == "tasks.bulk" and bd.pending["preview"]["reason"] == "a bulk delete asks by default"
        assert len((await run_tool(ctx, "list_tasks", {"list": "Garden", "include_completed": True})).data["items"]) == 2

        # Clearing a due date; reminders are read only.
        cleared = await run_tool(ctx, "update_task", {"uid": a["uid"], "due": "", "priority": 0})
        assert cleared.data["due"] is None and cleared.data["priority"] == 0
        rem = await run_tool(ctx, "list_reminders", {"hours": 168})
        assert {"active", "upcoming"} <= set(rem.data)
        assert any(x["uid"] == t["uid"] and x["kind"] == "task" for x in rem.data["upcoming"])


async def test_manage_calendars_asks_and_runs_after_approval(client, user):
    """calendar.create, calendar.rename, calendar.color and calendar.delete are calendar.manage,
    which asks; the approved create makes the calendar on the server."""
    await _connect(client)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = await run_tool(ctx, "manage_calendars", {"action": "create", "name": "Teaching", "color": "#3366ff"})
        assert res.pending["action"] == "calendar.manage"
        pid = res.data["approval_id"]
    pa = await _decide(user.id, pid)
    assert pa.status == "executed" and pa.result["name"] == "Teaching" and pa.result["events"]
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        for args in ({"action": "rename", "calendar": "Teaching", "name": "Classes"},
                     {"action": "color", "calendar": pa.result["url"], "color": "#ff9900"},
                     {"action": "delete", "calendar": str(pa.result["calendar_id"])}):
            r = await run_tool(ctx, "manage_calendars", args)
            assert r.pending["action"] == "calendar.manage", args
        with pytest.raises(ToolError, match="no calendars match"):
            await run_tool(ctx, "manage_calendars", {"action": "rename", "calendar": "Nope", "name": "x"})


def test_screen_context_is_bounded_and_validated():
    ui = screen.sanitize({
        "view": "calendar",
        "calendar": {"view": "week", "date": "2026-10-01", "ndays": 99, "tz": "Europe/London; DROP",
                     "visible": ["https://dav/a/"] * 3 + ["x" * 400], "focused_event": {
                         "uid": "abc@x", "occurrence": "2026-10-02T15:00:00+00:00", "title": "T" * 500,
                         "start": "not a time"}, "selected_slot": {"start": "2026-10-02T09:00:00Z"}, "bogus": 1},
        "tasks": {"smart": "today", "sort": "due", "selected": [f"u{i}" for i in range(80)] + ["bad\x00uid"],
                  "focused": "u3", "tag": "phone"}})
    cal, tk = ui["calendar"], ui["tasks"]
    assert cal["view"] == "week" and "ndays" not in cal and "tz" not in cal and cal["visible"] == ["https://dav/a/"]
    assert cal["focused_event"] == {"uid": "abc@x", "occurrence": "2026-10-02T15:00:00+00:00", "title": "T" * 200,
                                    "start": None}
    assert cal["selected_slot"]["start"] == "2026-10-02T09:00:00Z" and "bogus" not in cal
    assert len(tk["selected"]) == 50 and tk["focused"] == "u3" and tk["smart"] == "today" and tk["tag"] == "phone"
    assert screen.sanitize({"calendar": {"view": "nonsense"}, "tasks": "x"}) == {}


async def test_screen_context_resolves_this_meeting_and_these_tasks(client, user):
    """"This meeting" is the focused event and "these tasks" the selection: the prompt lines name
    them from the mirror, and the tools act on them when no uid is given."""
    await _connect(client)
    await _allow(user.id, "calendar.update")
    start = _start(2, 10)
    ev = (await client.post("/api/v1/calendar/events", json={"summary": "Design review", "start": start.isoformat(),
                                                             "end": (start + timedelta(hours=1)).isoformat()})).json()
    t1 = (await client.post("/api/v1/tasks", json={"summary": "Draft slides"})).json()
    t2 = (await client.post("/api/v1/tasks", json={"summary": "Book room"})).json()
    ui = {"calendar": {"view": "week", "date": start.date().isoformat(),
                       "focused_event": {"uid": ev["uid"], "occurrence": ev["occurrence"]}},
          "tasks": {"smart": "all", "selected": [t1["uid"], t2["uid"]], "focused": t1["uid"]}}
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, ui=ui)
        lines = await screen.describe(db, ctx.p, ctx.ui)
        assert "Calendar on screen: week view" in lines and "zone" in lines
        assert f"'Design review', starts {ev['occurrence']}" in lines and f"[[event:{ev['uid']}@{ev['occurrence']}]]" in lines
        # Selected tasks by uid only (agent/screen.py): their titles are read with search_tasks.
        assert t1["uid"] in lines and t2["uid"] in lines and '"these tasks", 2' in lines
        assert "'Draft slides'" in lines and "Book room" not in lines  # the focused task keeps its title
        moved = await run_tool(ctx, "update_event", {"start": (start + timedelta(hours=1)).isoformat(),
                                                     "end": (start + timedelta(hours=2)).isoformat()})
        assert moved.data["uid"] == ev["uid"] and moved.data["start"] == (start + timedelta(hours=1)).isoformat()
        got = await run_tool(ctx, "get_event", {})
        assert got.data["uid"] == ev["uid"]
        done = await run_tool(ctx, "bulk_tasks", {"op": "complete"})
        assert sorted(done.data["ok"]) == sorted([t1["uid"], t2["uid"]])
        with pytest.raises(ToolError, match="no event is focused"):
            await run_tool(await _ctx(db, user.id), "get_event", {})


async def test_local_only_calendars_are_hidden_from_a_hosted_model(client, user):
    """A chat on a hosted model (no local model) does not see records of a local-only account and
    is told so; a session already on a local model sees them (and stays sensitive); the signed-in
    user's own calls see everything. The screen lines never carry a local-only title."""
    await _connect(client)
    start = _start(2, 14)
    await client.post("/api/v1/calendar/events", json={"summary": "Lunch", "start": start.isoformat()})
    username, href = await _connect_direct(user.id)
    caldav.DAVClient(url=RADICALE, username=username, password="x").calendar(url=href).save_event(
        dtstart=start + timedelta(hours=2), dtend=start + timedelta(hours=3), summary="Faculty senate")
    async with sessionmaker()() as db:
        conn = (await db.execute(select(Connector).where(Connector.user_id == user.id, Connector.kind == "caldav")
                                 .order_by(Connector.id.desc()))).scalars().first()
        conn.data_class = "local_only"
        await db.commit()
    rng = {"from": start.date().isoformat(), "to": (start + timedelta(days=1)).date().isoformat()}
    async with sessionmaker()() as db:
        hosted = await _ctx(db, user.id)
        res = await run_tool(hosted, "list_calendar", rng)
        assert [e["summary"] for e in res.data["items"]] == ["Lunch"] and "hidden from this model" in res.data["note"]
        assert not res.sensitive
        local = await _ctx(db, user.id, sensitive=True)
        res = await run_tool(local, "list_calendar", rng)
        assert {e["summary"] for e in res.data["items"]} == {"Lunch", "Faculty senate"} and res.sensitive
        senate = next(e for e in res.data["items"] if e["summary"] == "Faculty senate")
        with pytest.raises(ToolError, match="hidden from this model"):
            await run_tool(hosted, "get_event", {"uid": senate["uid"]})
        mine = await _ctx(db, user.id, assistant=False)
        assert len((await run_tool(mine, "search_events", rng)).data["items"]) == 2
        bundle = await run_tool(hosted, "build_context", {"query": "senate"})
        assert "Faculty senate" not in str(bundle.data["calendar"]) and bundle.data["calendar"]["note"]
        assert not bundle.sensitive
        lines = await screen.describe(db, hosted.p, screen.sanitize({"calendar": {"view": "day", "focused_event": {
            "uid": senate["uid"], "occurrence": senate["occurrence"]}}}))
        assert "Faculty senate" not in lines and "local-only calendar" in lines
        obj = (await db.execute(select(CalObject).where(CalObject.uid == senate["uid"]))).scalar_one()
        assert obj.connector_id == conn.id


async def test_old_task_routes_are_gone_and_from_message_confirm_still_makes_tasks(client, user, raw):
    """The task routes live in calendar/routes_tasks.py only; the email confirm step still creates
    a task through them (mail.to_task)."""
    from helpers.core_flow import _ingest

    from mailr.calendar import routes as cal_routes
    from mailr.calendar import routes_tasks

    assert not [r for r in cal_routes.router.routes if str(getattr(r, "path", "")).startswith("/tasks")]
    assert {(r.path, m) for r in routes_tasks.router.routes for m in r.methods} >= {
        ("/tasks", "GET"), ("/tasks", "POST"), ("/tasks/{uid}", "PATCH"), ("/tasks/{uid}", "DELETE")}
    assert not hasattr(cal_routes, "TaskPatch") and not hasattr(cal_routes, "_x_tasks_update")
    assert cal_routes.EXECUTORS["tasks.complete"].__module__ == "mailr.calendar.routes_tasks"
    await _connect(client)
    mid, _ = await _ingest(user, raw("Send the report", frm="Dana <dana@example.org>"))
    r = await client.post("/api/v1/calendar/from-message/confirm", json={"message_id": mid, "kind": "task",
                                                                          "title": "Send the report", "priority": 1})
    assert r.status_code == 200, r.text
    assert r.json()["summary"] == "Send the report" and r.json()["priority"] == 1
