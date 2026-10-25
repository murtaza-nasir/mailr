# ACT-SURFACES: api
"""Tasks v2 against the dev Radicale (:15232): smart lists and their day boundaries in the user's
zone, keyset paging, counts, manual order (midpoint and renumber), subtasks (reparent, list
moves, cascade delete), bulk actions with partial failure, repeating tasks and
tags.

Actions covered: task.search, task.create, task.subtask_add, task.complete, task.reopen,
task.reschedule, task.set_repeat, task.set_start, task.set_priority, task.tag, task.remind_at,
task.skip, task.move_list, task.reorder, task.reparent, task.delete, task.bulk_complete,
task.bulk_reschedule, task.bulk_move, task.bulk_tag, task.bulk_priority, task.bulk_delete,
task.clear_completed, tag.rename, tag.color, tag.delete.
"""

import uuid
from datetime import datetime

import caldav
import pytest
from dev_services import radicale_up as _radicale_up
from helpers.tasks_v2 import API, _connect

from mailr.calendar import todo
from mailr.db import sessionmaker
from mailr.models import User

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


async def _settings(user_id: int, **kw) -> None:
    async with sessionmaker()() as db:
        u = await db.get(User, user_id)
        u.settings = {**(u.settings or {}), **kw}
        await db.commit()


def _pin(monkeypatch, iso: str) -> None:
    monkeypatch.setattr(todo, "_now", lambda: datetime.fromisoformat(iso))


async def _task(client, **kw) -> dict:
    r = await client.post(f"{API}/tasks", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


async def _titles(client, **params) -> list[str]:
    r = await client.get(f"{API}/tasks", params=params)
    assert r.status_code == 200, r.text
    return [t["summary"] for t in r.json()["items"]]


def _raw(cal: caldav.Calendar, uid: str) -> str:
    return cal.todo_by_uid(uid).data


# ---------------- smart lists, counts, paging ----------------


async def test_smart_lists_follow_the_users_day(client, user, monkeypatch):
    """Now is Thu 1 Oct 23:30 in New York, already 2 Oct in UTC. ACT task.search"""
    await _connect(client)
    await _settings(user.id, timezone="America/New_York")
    _pin(monkeypatch, "2026-10-02T03:30:00+00:00")
    await _task(client, summary="Date today", due="2026-10-01")
    await _task(client, summary="Timed tonight", due="2026-10-02T02:00:00Z")  # 22:00 NY, passed
    await _task(client, summary="After midnight NY", due="2026-10-02T05:00:00Z")  # 01:00 NY on 2 Oct
    await _task(client, summary="Old", due="2026-09-28")
    await _task(client, summary="Started", start="2026-09-30")
    await _task(client, summary="Deferred", start="2026-10-05")
    await _task(client, summary="Loose", priority=1)
    done = await _task(client, summary="Finished", due="2026-09-30")
    r = await client.patch(f"{API}/tasks/{done['uid']}", json={"completed": True})
    assert r.status_code == 200, r.text

    r = await client.get(f"{API}/tasks", params={"smart": "today"})
    today = {t["summary"]: t["section"] for t in r.json()["items"]}
    assert today == {"Old": "overdue", "Timed tonight": "overdue", "Date today": "today", "Started": "started"}
    assert r.json()["sections"] == {"overdue": 2, "today": 1, "started": 1}
    up = (await client.get(f"{API}/tasks", params={"smart": "upcoming"})).json()["items"]
    assert [(t["summary"], t["section"]) for t in up] == [("After midnight NY", "2026-10-02"), ("Deferred", "2026-10-05")]
    assert set(await _titles(client, smart="overdue")) == {"Old", "Timed tonight"}
    assert await _titles(client, smart="nodate") == ["Loose"]
    assert await _titles(client, smart="no_date") == ["Loose"]
    assert await _titles(client, smart="high") == ["Loose"]
    assert set(await _titles(client, smart="scheduled")) == {"Date today", "Timed tonight", "After midnight NY", "Old"}
    assert "Deferred" not in await _titles(client, smart="all")
    assert "Deferred" in await _titles(client, smart="all", include_future=1)
    assert await _titles(client, smart="completed") == ["Finished"]

    c = (await client.get(f"{API}/tasks/counts")).json()
    assert c["smart"]["today"] == 4 and c["overdue"] == 2 and c["smart"]["upcoming"] == 2
    assert c["smart"]["nodate"] == c["smart"]["no_date"] == 1 and c["smart"]["all"] == 6
    assert list(c["lists"].values()) == [7]

    # The same tasks an hour earlier in Los Angeles: 1 Oct 20:30, so 01:00 NY (22:00 LA) is today.
    await _settings(user.id, timezone="America/Los_Angeles")
    today = {t["summary"] for t in (await client.get(f"{API}/tasks", params={"smart": "today"})).json()["items"]}
    assert "After midnight NY" in today


async def test_keyset_paging_and_sorts(client, user):
    await _connect(client)
    for i, name in enumerate(["delta", "alpha", "echo", "charlie", "bravo", "golf", "foxtrot"]):
        await _task(client, summary=name, priority=(i % 3) * 4 + 1, due=f"2026-11-{10 + i:02d}")
    seen, cursor = [], None
    while True:
        params = {"sort": "title", "limit": 3, **({"cursor": cursor} if cursor else {})}
        body = (await client.get(f"{API}/tasks", params=params)).json()
        seen += [t["summary"] for t in body["items"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert seen == sorted(seen) and len(seen) == 7
    due = await _titles(client, sort="due")
    assert due == ["delta", "alpha", "echo", "charlie", "bravo", "golf", "foxtrot"]
    pri = (await client.get(f"{API}/tasks", params={"sort": "priority"})).json()["items"]
    assert [t["priority"] for t in pri] == sorted(t["priority"] for t in pri)
    assert (await client.get(f"{API}/tasks", params={"cursor": "garbage"})).status_code == 400
    assert await _titles(client, q="list:Personal alp") == ["alpha"]
    assert await _titles(client, q="pri:high", sort="title") == ["charlie", "delta", "foxtrot"]


# ---------------- order, subtasks, delete ----------------


async def test_reorder_takes_the_midpoint_and_renumbers_when_full(client, user):
    """ACT task.reorder"""
    await _connect(client)
    a = await _task(client, summary="A", sort_order=0)
    b = await _task(client, summary="B", sort_order=100)
    c = await _task(client, summary="C", sort_order=200)
    r = await client.post(f"{API}/tasks/{c['uid']}/move", json={"after": a["uid"]})
    assert r.status_code == 200, r.text
    assert r.json()["sort_order"] == 50 and r.json()["renumbered"] == 0
    assert await _titles(client, sort="manual") == ["A", "C", "B"]
    # No room between 0 and 1: the level is renumbered in the new order.
    await client.patch(f"{API}/tasks/{c['uid']}", json={"sort_order": 1})
    r = await client.post(f"{API}/tasks/{b['uid']}/move", json={"before": c["uid"]})
    assert r.status_code == 200, r.text
    assert r.json()["renumbered"] >= 2
    items = (await client.get(f"{API}/tasks", params={"sort": "manual"})).json()["items"]
    assert [t["summary"] for t in items] == ["A", "B", "C"]
    assert [t["sort_order"] for t in items] == [todo.STEP, 2 * todo.STEP, 3 * todo.STEP]
    # Tasks without an order (another client) are numbered when one is placed among them.
    d = await _task(client, summary="D")
    e = await _task(client, summary="E")
    r = await client.post(f"{API}/tasks/{a['uid']}/move", json={"after": d["uid"]})
    assert r.status_code == 200, r.text
    assert r.json()["renumbered"] >= 3
    assert await _titles(client, sort="manual") == ["B", "C", "D", "A", "E"]
    # The job renumbers a long level in the order the move chose.
    await todo.reorder_job({"user_id": user.id, "order": [e["uid"], d["uid"], c["uid"], b["uid"], a["uid"]]}, None)
    assert await _titles(client, sort="manual") == ["E", "D", "C", "B", "A"]
    assert (await client.post(f"{API}/tasks/{a['uid']}/move", json={"after": "nope-not-here"})).status_code == 400


async def test_reparent_and_subtasks_follow_a_list_move(client, user):
    """ACT task.reparent, ACT task.move_list, ACT task.subtask_add"""
    cals, cols = await _connect(client, "Home", "Work")
    home, work = cols["Home"]["url"], cols["Work"]["url"]
    p = await _task(client, summary="Parent", list=home)
    kid = await _task(client, summary="Kid", list=home, parent=p["uid"])
    x = await _task(client, summary="X", list=home)
    # Under a subtask: one level only, so X joins the parent.
    r = await client.post(f"{API}/tasks/{x['uid']}/move", json={"parent": kid["uid"]})
    assert r.status_code == 200, r.text
    assert r.json()["parent"] == p["uid"]
    sub = (await client.get(f"{API}/tasks", params={"parent": p["uid"]})).json()["items"]
    assert {t["summary"] for t in sub} == {"Kid", "X"}
    # A task with subtasks cannot become a subtask.
    other = await _task(client, summary="Other", list=home)
    assert (await client.post(f"{API}/tasks/{p['uid']}/move", json={"parent": other["uid"]})).status_code == 400
    # Moving the parent to another list takes the subtasks along.
    r = await client.post(f"{API}/tasks/{p['uid']}/move", json={"list": work})
    assert r.status_code == 200, r.text
    assert set(r.json()["moved_subtasks"]) == {kid["uid"], x["uid"]}
    in_work = {t["summary"] for t in (await client.get(f"{API}/tasks", params={"list": work})).json()["items"]}
    assert in_work == {"Parent", "Kid", "X"}
    assert cals["Work"].todo_by_uid(kid["uid"]) is not None
    # Back to the top level.
    r = await client.post(f"{API}/tasks/{x['uid']}/move", json={"parent": ""})
    assert r.json()["parent"] is None
    assert (await client.get(f"{API}/tasks", params={"list": work})).json()["items"][0]["list"] == work


async def test_cascade_delete_and_detach(client, user):
    """ACT task.delete"""
    cals, _ = await _connect(client)
    p = await _task(client, summary="Parent")
    k1 = await _task(client, summary="Kid 1", parent=p["uid"])
    k2 = await _task(client, summary="Kid 2", parent=p["uid"])
    # A grandchild written by another client (mailr itself keeps one level).
    g_uid = f"g-{uuid.uuid4().hex[:8]}"
    cals["Personal"].save_todo("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\nBEGIN:VTODO\r\n"
                               f"UID:{g_uid}\r\nDTSTAMP:20260101T000000Z\r\nSUMMARY:Grandkid\r\n"
                               f"RELATED-TO;RELTYPE=PARENT:{k1['uid']}\r\nEND:VTODO\r\nEND:VCALENDAR\r\n")
    r = await client.delete(f"{API}/tasks/{p['uid']}", params={"cascade": "true"})
    assert r.status_code == 200, r.text
    assert r.json()["deleted"][0] == g_uid and set(r.json()["deleted"]) == {g_uid, k1["uid"], k2["uid"]}
    assert await _titles(client) == []
    # Without cascade the subtasks stay, detached.
    p = await _task(client, summary="Parent 2")
    k = await _task(client, summary="Kid 3", parent=p["uid"])
    r = await client.delete(f"{API}/tasks/{p['uid']}")
    assert r.json()["detached"] == [k["uid"]]
    [left] = (await client.get(f"{API}/tasks")).json()["items"]
    assert left["summary"] == "Kid 3" and left["parent"] is None
    assert "RELATED-TO" not in _raw(cals["Personal"], k["uid"])


# ---------------- bulk ----------------


async def test_bulk_actions_report_partial_failure(client, user):
    """ACT task.bulk_complete, ACT task.bulk_reschedule, ACT task.bulk_move, ACT task.bulk_tag,
    ACT task.bulk_priority, ACT task.bulk_delete, ACT task.clear_completed"""
    cals, cols = await _connect(client, "Home", "Work")
    await _settings(user.id, timezone="Europe/London")
    ts = [await _task(client, summary=f"T{i}", list=cols["Home"]["url"], due="2026-10-26T09:00:00",
                      categories=["errand"]) for i in range(3)]
    uids = [t["uid"] for t in ts]
    # Shift by a week keeps 9:00 London across the end of BST (25 Oct).
    r = await client.post(f"{API}/tasks/bulk", json={"uids": uids, "op": "reschedule", "args": {"shift_days": -7}})
    assert r.status_code == 200 and r.json()["failed"] == [], r.text
    assert {t["due"] for t in r.json()["items"]} == {"2026-10-19T08:00:00+00:00"}
    # T1 changes on another device; the batch goes on and reports it.
    other = cals["Home"].todo_by_uid(uids[1])
    other.icalendar_component["summary"] = "T1 (changed elsewhere)"
    other.save()
    r = await client.post(f"{API}/tasks/bulk", json={
        "uids": uids + ["missing-uid-123"], "op": "priority",
        "args": {"priority": 1, "etags": {uids[1]: ts[1]["etag"]}}})
    body = r.json()
    assert body["ok"] == [uids[0], uids[2]]
    assert {f["uid"]: f["error"] for f in body["failed"]} == {uids[1]: "conflict", "missing-uid-123": "not_found"}
    assert next(f for f in body["failed"] if f["uid"] == uids[1])["server"]["summary"] == "T1 (changed elsewhere)"
    r = await client.post(f"{API}/tasks/bulk", json={"uids": uids, "op": "tag",
                                                     "args": {"add": ["@phone"], "remove": ["errand"]}})
    assert r.json()["failed"] == []
    assert all(t["categories"] == ["@phone"] for t in r.json()["items"])
    r = await client.post(f"{API}/tasks/bulk", json={"uids": uids[:2], "op": "move", "args": {"list": cols["Work"]["url"]}})
    assert r.json()["ok"] == uids[:2]
    r = await client.post(f"{API}/tasks/bulk", json={"uids": uids, "op": "complete"})
    assert len(r.json()["ok"]) == 3
    assert await _titles(client) == []
    # Clear completed = a bulk delete of the completed tasks.
    done = [t["uid"] for t in (await client.get(f"{API}/tasks", params={"smart": "completed"})).json()["items"]]
    r = await client.post(f"{API}/tasks/bulk", json={"uids": done, "op": "delete"})
    assert sorted(r.json()["ok"]) == sorted(done)
    assert (await client.get(f"{API}/tasks", params={"status": "all"})).json()["items"] == []


async def test_bulk_delete_by_a_token_asks(client, user):
    from helpers.calendar import _token_client

    await _connect(client)
    t = await _task(client, summary="Keep me")
    agent = await _token_client(client, ["read", "calendar"])
    r = await agent.post(f"{API}/tasks/bulk", json={"uids": [t["uid"]], "op": "delete"})
    assert r.status_code == 202, r.text
    r = await agent.post(f"{API}/tasks/bulk", json={"uids": [t["uid"]], "op": "priority", "args": {"priority": 5}})
    assert r.status_code == 200 and r.json()["ok"] == [t["uid"]]
    await agent.aclose()


# ---------------- repeating tasks ----------------


async def test_weekly_task_advances_with_a_history_copy_and_count(client, user, monkeypatch):
    """ACT task.complete, ACT task.set_repeat"""
    cals, _ = await _connect(client)
    _pin(monkeypatch, "2026-10-01T15:00:00+00:00")
    t = await _task(client, summary="Water plants", due="2026-10-01", recurrence="FREQ=WEEKLY;COUNT=3")
    assert t["recurring"] and t["rrule"] == "FREQ=WEEKLY;COUNT=3"
    r = await client.patch(f"{API}/tasks/{t['uid']}", json={"completed": True, "occurrence": "2026-10-01"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["due"] == "2026-10-08" and got["next"] == "2026-10-08" and not got["completed"]
    assert "COUNT=2" in got["rrule"]
    hist = got["history_uid"]
    copy = cals["Personal"].todo_by_uid(hist).icalendar_component
    assert str(copy["status"]) == "COMPLETED" and copy.get("rrule") is None
    assert str(copy["x-mailr-occurrence"]) == "2026-10-01" and str(copy["related-to"]) == t["uid"]
    assert [x["summary"] for x in (await client.get(f"{API}/tasks", params={"smart": "completed"})).json()["items"]] \
        == ["Water plants"]
    # The same completion again (another device, a replay): nothing changes, no second copy.
    again = (await client.patch(f"{API}/tasks/{t['uid']}", json={"completed": True, "occurrence": "2026-10-01"})).json()
    assert again["due"] == "2026-10-08" and again.get("unchanged")
    assert len((await client.get(f"{API}/tasks", params={"smart": "completed"})).json()["items"]) == 1
    # COUNT=2 then COUNT=1: the last completion completes the series.
    r = await client.patch(f"{API}/tasks/{t['uid']}", json={"completed": True})
    assert r.json()["due"] == "2026-10-15" and "COUNT=1" in r.json()["rrule"]
    r = await client.patch(f"{API}/tasks/{t['uid']}", json={"completed": True})
    assert r.json()["completed"] and r.json()["next"] is None and r.json()["due"] == "2026-10-15"


async def test_until_ends_and_copy_setting_off(client, user):
    await _connect(client)
    await _settings(user.id, tasks={"keep_completed_copy": False})
    t = await _task(client, summary="Report", due="2026-10-01", recurrence="FREQ=DAILY;UNTIL=20261002")
    r = (await client.patch(f"{API}/tasks/{t['uid']}", json={"completed": True})).json()
    assert r["due"] == "2026-10-02" and r["history_uid"] is None
    r = (await client.patch(f"{API}/tasks/{t['uid']}", json={"completed": True})).json()
    assert r["completed"] and r["next"] is None
    assert len((await client.get(f"{API}/tasks", params={"status": "all"})).json()["items"]) == 1


async def test_repeat_from_completion_done_late(client, user, monkeypatch):
    await _connect(client)
    await _settings(user.id, timezone="America/New_York")
    t = await _task(client, summary="Water", due="2026-10-01T09:00:00", recurrence="FREQ=DAILY;INTERVAL=3",
                    repeat_from="completion")
    assert t["repeat_from"] == "completion"
    _pin(monkeypatch, "2026-10-04T20:00:00+00:00")  # three days late, 16:00 New York
    r = (await client.patch(f"{API}/tasks/{t['uid']}", json={"completed": True})).json()
    assert r["due"] == "2026-10-07T13:00:00+00:00"  # completion + 3 days, at the task's 9:00


async def test_monthly_on_the_31st_created_in_mailr_and_by_another_client(client, user):
    cals, _ = await _connect(client)
    t = await _task(client, summary="Rent", due="2027-01-31", rrule="FREQ=MONTHLY")
    assert "BYMONTHDAY=28,29,30,31" in t["rrule"] and "BYSETPOS=-1" in t["rrule"]
    r = (await client.patch(f"{API}/tasks/{t['uid']}", json={"completed": True})).json()
    assert r["due"] == "2027-02-28"
    r = (await client.patch(f"{API}/tasks/{t['uid']}", json={"completed": True})).json()
    assert r["due"] == "2027-03-31"
    uid = f"o-{uuid.uuid4().hex[:8]}"
    cals["Personal"].save_todo("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\nBEGIN:VTODO\r\n"
                               f"UID:{uid}\r\nDTSTAMP:20260101T000000Z\r\nSUMMARY:Other rent\r\n"
                               "DUE;VALUE=DATE:20270131\r\nRRULE:FREQ=MONTHLY;BYMONTHDAY=31\r\nEND:VTODO\r\nEND:VCALENDAR\r\n")
    r = await client.patch(f"{API}/tasks/{uid}", json={"completed": True})
    assert r.status_code == 200, r.text
    assert r.json()["due"] == "2027-03-31"  # RFC 5545 skips February


async def test_skip_and_undo(client, user):
    """ACT task.skip, ACT task.reopen"""
    cals, _ = await _connect(client)
    await _settings(user.id, timezone="America/New_York")
    t = await _task(client, summary="Standup notes", due="2026-10-29T09:00:00", recurrence="FREQ=WEEKLY;COUNT=5",
                    reminders_at=["2026-10-29T08:00:00"])
    assert t["reminders_at"] == ["2026-10-29T12:00:00+00:00"]
    r = await client.post(f"{API}/tasks/{t['uid']}/skip", json={})
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    # 9:00 New York on both sides of the end of DST (1 Nov); no copy for a skip.
    assert r.json()["next"] == "2026-11-05T14:00:00+00:00" and task["history_uid"] is None
    assert "COUNT=4" in task["rrule"] and task["reminders_at"] == ["2026-11-05T13:00:00+00:00"]
    # Undo (a reopen): the due and COUNT come back.
    r = (await client.patch(f"{API}/tasks/{t['uid']}", json={"completed": False})).json()
    assert r["due"] == "2026-10-29T13:00:00+00:00" and "COUNT=5" in r["rrule"] and r["restored"]
    assert r["reminders_at"] == ["2026-10-29T12:00:00+00:00"]
    # Complete, then undo: the copy goes too.
    done = (await client.patch(f"{API}/tasks/{t['uid']}", json={"completed": True})).json()
    assert done["history_uid"]
    r = (await client.patch(f"{API}/tasks/{t['uid']}", json={"completed": False})).json()
    assert r["deleted_history_uid"] == done["history_uid"] and r["due"] == "2026-10-29T13:00:00+00:00"
    with pytest.raises(caldav.error.NotFoundError):
        cals["Personal"].todo_by_uid(done["history_uid"])
    plain = await _task(client, summary="Once")
    assert (await client.post(f"{API}/tasks/{plain['uid']}/skip", json={})).status_code == 400


# ---------------- fields and tags ----------------


async def test_new_fields_round_trip(client, user):
    """ACT task.set_start, ACT task.set_priority, ACT task.tag, ACT task.remind_at, ACT task.reschedule"""
    cals, _ = await _connect(client)
    t = await _task(client, summary="Plan", due="2026-10-20T15:00:00Z", start="2026-10-18", tags=["work"],
                    sort_order=7, reminders=[30], reminders_at=["2026-10-19T09:00:00Z"], priority=5)
    assert t["start"] == "2026-10-18" and t["categories"] == ["work"] and t["sort_order"] == 7
    assert t["reminders"] == [30] and t["reminders_at"] == ["2026-10-19T09:00:00+00:00"]
    r = await client.patch(f"{API}/tasks/{t['uid']}", json={"reminders": [10], "categories": ["work", "q4"],
                                                             "due": "2026-10-21T15:00:00Z", "priority": 1})
    got = r.json()
    assert got["reminders"] == [10] and got["reminders_at"] == ["2026-10-19T09:00:00+00:00"]
    assert got["categories"] == ["work", "q4"] and got["priority"] == 1
    raw = _raw(cals["Personal"], t["uid"])
    assert "X-APPLE-SORT-ORDER:7" in raw and "CATEGORIES:work,q4" in raw
    r = await client.patch(f"{API}/tasks/{t['uid']}", json={"clear_start": True, "reminders_at": []})
    assert r.json()["start"] is None and r.json()["reminders_at"] == []


async def test_tag_rename_colour_and_delete(client, user):
    """ACT tag.rename, ACT tag.color, ACT tag.delete"""
    cals, _ = await _connect(client)
    a = await _task(client, summary="A", categories=["Errand", "home"])
    b = await _task(client, summary="B", categories=["errand"])
    await _task(client, summary="C", categories=["home"])
    tags = {t["name"]: t["count"] for t in (await client.get(f"{API}/tasks/tags")).json()}
    assert tags == {"Errand": 2, "home": 2}
    r = await client.patch(f"{API}/tasks/tags/errand", json={"name": "Errands", "color": "#ff8800", "order": 1})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 2 and r.json()["tag"] == {"name": "Errands", "count": 2, "color": "#ff8800",
                                                            "order": 1, "pinned": False}
    for uid in (a["uid"], b["uid"]):
        assert "Errands" in _raw(cals["Personal"], uid)
    assert (await client.get(f"{API}/tasks/tags")).json()[0]["name"] == "Errands"
    assert await _titles(client, tag="errands", sort="title") == ["A", "B"]
    assert (await client.get(f"{API}/tasks/counts")).json()["tags"] == {"Errands": 2, "home": 2}
    r = await client.delete(f"{API}/tasks/tags/home")
    assert r.status_code == 200 and r.json()["updated"] == 2
    assert [t["name"] for t in (await client.get(f"{API}/tasks/tags")).json()] == ["Errands"]


async def test_the_tag_rename_job_commits_each_task(client, user):
    """calendar.retag (more tasks than INLINE_RETAG): each task is committed on its own, so its lock
    is held for its own round trip only; the tag's colour moves to the new name."""
    from mailr.calendar import todo

    cals, _ = await _connect(client)
    made = [await _task(client, summary=f"Pick up {n}", categories=["shop"]) for n in ("bread", "stamps")]
    await client.patch(f"{API}/tasks/tags/shop", json={"color": "#3366cc"})
    await todo.retag_job({"user_id": user.id, "name": "shop", "new_name": "Errands"}, None)
    for t in made:
        assert "Errands" in _raw(cals["Personal"], t["uid"])
    tags = {t["name"]: t for t in (await client.get(f"{API}/tasks/tags")).json()}
    assert tags["Errands"]["count"] == 2 and tags["Errands"]["color"] == "#3366cc" and "shop" not in tags
