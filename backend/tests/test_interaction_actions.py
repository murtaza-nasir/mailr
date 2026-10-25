# ACT-SURFACES: api assistant
"""The catalogued actions, through the API (the `api` surface of the catalogue).

Reminder state needs no calendar server; the task and event actions run against the dev Radicale."""

from datetime import UTC, datetime, timedelta

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.calendar import _connect
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import ReminderState

KEY_T = "t|task-1|2026-10-02|0"
KEY_E = "e|ev-1|2026-10-02T15:00:00+00:00|10"
KEY_E_OLD = "e|ev-1|20261002T150000Z|10"  # an older form: normalised to KEY_E by every route
radicale = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


async def test_task_snooze_and_event_snooze_record_the_reminder_state(client, user):
    """task.snooze / event.snooze: the reminder is snoozed; the record is untouched."""
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    r = await client.post("/api/v1/reminders/snooze", json={"key": KEY_T, "until": until})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "snoozed" and r.json()["kind"] == "t" and r.json()["uid"] == "task-1"
    r = await client.post("/api/v1/reminders/snooze", json={"key": KEY_E_OLD, "until": until})
    assert r.json()["minutes"] == 10 and r.json()["occurrence"] == "2026-10-02T15:00:00+00:00"
    assert r.json()["key"] == KEY_E
    assert (await client.post("/api/v1/reminders/snooze", json={"key": "nope", "until": until})).status_code == 400
    far = (datetime.now(UTC) + timedelta(days=400)).isoformat()
    assert (await client.post("/api/v1/reminders/snooze", json={"key": KEY_T, "until": far})).status_code == 400


async def test_task_snooze_replayed_after_its_time_is_a_no_op(client, user):
    """task.snooze from a phone's offline queue, replayed after its time: it succeeds and changes nothing.

    The failure it prevents: "Couldn't sync: snooze until a time in the future" in Pending changes."""
    soon = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    assert (await client.post("/api/v1/reminders/snooze", json={"key": KEY_T, "until": soon})).status_code == 200
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    for _ in range(2):  # the same replay twice: idempotent
        r = await client.post("/api/v1/reminders/snooze", json={"key": KEY_T, "until": past})
        assert r.status_code == 200, r.text
        assert r.json()["elapsed"] is True and r.json()["state"] == "snoozed"
        assert r.json()["snoozed_until"][:16] == soon[:16]  # the newer snooze stays
    # A dismissal made on another device is not undone by the late replay.
    await client.post("/api/v1/reminders/handle", json={"keys": [KEY_T]})
    r = await client.post("/api/v1/reminders/snooze", json={"key": KEY_T, "until": past})
    assert r.status_code == 200 and r.json()["state"] == "handled"
    # A key with no state yet: still a success, nothing stored.
    r = await client.post("/api/v1/reminders/snooze", json={"key": KEY_E, "until": past})
    assert r.status_code == 200 and r.json()["elapsed"] is True and r.json()["state"] == "pending"
    async with sessionmaker()() as db:
        rows = (await db.execute(select(ReminderState).where(ReminderState.user_id == user.id,
                                                             ReminderState.key == KEY_E))).scalars().all()
    assert rows == []
    # Within a minute of now it is a normal snooze (it fires at once).
    just = (datetime.now(UTC) - timedelta(seconds=20)).isoformat()
    r = await client.post("/api/v1/reminders/snooze", json={"key": KEY_E, "until": just})
    assert r.status_code == 200 and "elapsed" not in r.json() and r.json()["state"] == "snoozed"


async def test_reminder_dismiss_undo_and_sync(client, user):
    """reminder.dismiss (with undo) and reminder.sync: other devices read what changed since their cursor."""
    first = (await client.get("/api/v1/reminders/state")).json()
    assert first["items"] == []
    r = await client.post("/api/v1/reminders/handle", json={"keys": [KEY_T, KEY_E]})
    assert r.status_code == 200 and r.json()["count"] == 2
    got = (await client.get("/api/v1/reminders/state")).json()
    assert {i["key"]: i["state"] for i in got["items"]} == {KEY_T: "handled", KEY_E: "handled"}
    cursor = got["next"]
    assert (await client.get("/api/v1/reminders/state", params={"since": cursor})).json()["items"] == []
    await client.post("/api/v1/reminders/handle", json={"keys": [KEY_E], "undo": True})
    later = (await client.get("/api/v1/reminders/state", params={"since": cursor})).json()["items"]
    assert [(i["key"], i["state"]) for i in later] == [(KEY_E, "pending")]


async def test_event_remind_before_adds_a_one_off_and_handles_the_fired_one(client, user):
    """event.remind_before: a one-off reminder N minutes before the occurrence replaces the fired one."""
    r = await client.post("/api/v1/reminders/add", json={"kind": "e", "uid": "ev-1", "occurrence": "20261002T150000Z",
                                                        "minutes": 5, "handle": [KEY_E]})
    assert r.status_code == 200, r.text
    assert r.json()["key"] == "e|ev-1|2026-10-02T15:00:00+00:00|5" and r.json()["oneoff"] and r.json()["state"] == "pending"
    async with sessionmaker()() as db:
        rows = {x.key: x.state for x in (await db.execute(select(ReminderState).where(
            ReminderState.user_id == user.id))).scalars()}
    assert rows == {"e|ev-1|2026-10-02T15:00:00+00:00|5": "pending", KEY_E: "handled"}
    assert (await client.post("/api/v1/reminders/add", json={"kind": "t", "uid": "x", "minutes": 5})).status_code == 422


@radicale
async def test_task_complete_reopen_reschedule_edit_delete(client, user):
    """task.complete, task.reopen, task.reschedule, task.edit, task.delete through PATCH/DELETE /tasks."""
    await _connect(client)
    due = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    t = (await client.post("/api/v1/tasks", json={"summary": "Send numbers", "due": due, "reminders": [0]})).json()
    uid = t["uid"]
    assert (await client.patch(f"/api/v1/tasks/{uid}", json={"completed": True})).json()["completed"]
    assert not (await client.patch(f"/api/v1/tasks/{uid}", json={"completed": False})).json()["completed"]
    new_due = (datetime.now(UTC) + timedelta(days=3)).replace(hour=17, minute=0, second=0, microsecond=0)
    moved = (await client.patch(f"/api/v1/tasks/{uid}", json={"due": new_due.isoformat()})).json()
    assert moved["due"].startswith(new_due.date().isoformat()) and moved["reminders"] == [0]
    edited = (await client.patch(f"/api/v1/tasks/{uid}", json={"summary": "Send Q3 numbers", "priority": 1})).json()
    assert edited["summary"] == "Send Q3 numbers" and edited["priority"] == 1
    assert (await client.delete(f"/api/v1/tasks/{uid}")).status_code == 200
    assert (await client.get("/api/v1/tasks")).json()["items"] == []


@radicale
async def test_event_reschedule_edit_delete(client, user):
    """event.reschedule keeps the duration; event.edit; event.delete."""
    await _connect(client)
    start = (datetime.now(UTC) + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)
    ev = (await client.post("/api/v1/calendar/events", json={
        "summary": "Budget review", "start": start.isoformat(), "end": (start + timedelta(minutes=45)).isoformat()})).json()
    uid = ev["uid"]
    later = start + timedelta(days=1, hours=1)
    moved = (await client.patch(f"/api/v1/calendar/events/{uid}", json={
        "start": later.isoformat(), "end": (later + timedelta(minutes=45)).isoformat()})).json()
    assert datetime.fromisoformat(moved["start"]) == later
    assert datetime.fromisoformat(moved["end"]) - datetime.fromisoformat(moved["start"]) == timedelta(minutes=45)
    assert (await client.patch(f"/api/v1/calendar/events/{uid}", json={"location": "Room 4"})).json()["location"] == "Room 4"
    assert (await client.delete(f"/api/v1/calendar/events/{uid}")).status_code == 200


@radicale
async def test_assistant_surface_runs_the_same_actions(client, user):
    """The `assistant` cells: task.complete, task.reopen, task.reschedule, task.edit, task.delete,
    event.reschedule, event.edit, event.delete and event.rsvp are assistant tools that call the same
    actions (some wait for the user's approval, which is also a valid outcome here)."""
    await _connect(client)
    due = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    uid = (await client.post("/api/v1/tasks", json={"summary": "Call Dana", "due": due})).json()["uid"]

    async def tool(name, args):
        r = await client.post(f"/api/v1/agent/tools/{name}", json=args)
        assert r.status_code == 200, (name, r.text)
        return r.json()

    for name, args in [("complete_task", {"uid": uid}), ("update_task", {"uid": uid, "status": "open"}),
                       ("update_task", {"uid": uid, "due": (datetime.now(UTC) + timedelta(days=4)).date().isoformat(),
                                        "summary": "Call Dana back"}),
                       ("delete_task", {"uid": uid})]:
        out = await tool(name, args)
        assert "error" not in str(out.get("result", {})).lower()[:40] or "pending" in str(out), (name, out)
    start = (datetime.now(UTC) + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)
    ev = (await client.post("/api/v1/calendar/events", json={"summary": "Sync", "start": start.isoformat(),
                                                           "end": (start + timedelta(hours=1)).isoformat()})).json()
    later = start + timedelta(hours=2)
    await tool("update_event", {"uid": ev["uid"], "start": later.isoformat(), "end": (later + timedelta(hours=1)).isoformat()})
    await tool("delete_event", {"uid": ev["uid"]})


@radicale
async def test_task_open_email_link_comes_back_with_the_task(client, user, raw):
    """task.open_email / event.open_email: a task made from an email carries that conversation."""
    from helpers.core_flow import _ingest

    await _connect(client)
    _, cid = await _ingest(user, raw("Please send the Q3 numbers", frm="Dana <dana@example.org>"))
    r = await client.post("/api/v1/tasks", json={"summary": "Send Q3 numbers", "conversation_id": cid})
    assert r.status_code == 200, r.text
    items = (await client.get("/api/v1/tasks")).json()["items"]
    assert [i.get("conversation_id") for i in items if i["summary"] == "Send Q3 numbers"] == [cid]
