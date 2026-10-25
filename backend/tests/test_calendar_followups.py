# ACT-SURFACES: api assistant
"""Calendar and tasks follow-ups against the dev Radicale (:15232): RSVP to one occurrence or
to the ones after it, task reminders in /calendar/delta, one task by uid, .ics export, and a bulk
move that stays cheap in CalDAV requests."""

import email
import email.policy
import uuid
from datetime import UTC, datetime, timedelta

import caldav
import pytest
from dev_services import RADICALE
from dev_services import radicale_up as _radicale_up
from icalendar import Calendar
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import Connector, Identity, Outbox, User

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")

API = "/api/v1"


async def _connect(client, *names: str) -> tuple[dict[str, caldav.Calendar], dict[str, dict], str]:
    username = f"u{uuid.uuid4().hex[:8]}"
    pr = caldav.DAVClient(url=RADICALE, username=username, password="x").principal()
    cals = {n: pr.make_calendar(name=n, supported_calendar_component_set=["VEVENT", "VTODO"])
            for n in names or ("Personal",)}
    r = await client.post(f"{API}/calendar/connect", json={"url": RADICALE, "username": username, "password": "x"})
    assert r.status_code == 200, r.text
    cols = {c["name"]: c for c in (await client.get(f"{API}/calendar/calendars")).json()}
    return cals, cols, username


async def _identity(user) -> str:
    async with sessionmaker()() as db:
        conn = Connector(user_id=user.id, kind="imap", name="out", mode="mirror", enabled=False, status="paused",
                         config={})
        db.add(conn)
        await db.flush()
        me = f"me-{conn.id}@example.com"
        db.add(Identity(user_id=user.id, connector_id=conn.id, email=me, name="Me", is_default=True, receives=[]))
        await db.commit()
    return me


def _stamp(d: datetime) -> str:
    return d.strftime("%Y%m%dT%H%M%SZ")


async def _reply_part(user, outbox_id: int) -> Calendar:
    from mailr.core.drafts import build_for_payload
    from mailr.core.sendgate import _SystemPrincipal

    async with sessionmaker()() as db:
        ob = await db.get(Outbox, outbox_id)
        raw, _, _ = await build_for_payload(db, _SystemPrincipal(await db.get(User, user.id)), ob.payload)
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    part = next(p for p in msg.walk() if p.get_content_type() == "text/calendar")
    assert part.get_param("method") == "REPLY"
    return Calendar.from_ical(part.get_content())


async def _statuses(client, start: datetime, days: int) -> dict[str, str]:
    r = await client.get(f"{API}/calendar/agenda", params={
        "from": start.date().isoformat(), "to": (start + timedelta(days=days)).date().isoformat()})
    assert r.status_code == 200, r.text
    return {e["occurrence"]: e["my_status"] for e in r.json()["events"]}


# ---------------- RSVP scope ----------------


async def test_rsvp_this_and_following_occurrences(client, user):
    """event.rsvp on one occurrence writes an override with my answer and a REPLY with its
    RECURRENCE-ID; "following" answers the series from there and keeps the earlier answers."""
    cals, _, _ = await _connect(client)
    me = await _identity(user)
    first = (datetime.now(UTC) - timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    uid = f"series-{uuid.uuid4().hex[:10]}"
    cals["Personal"].save_event(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nDTSTAMP:20260920T100000Z\r\nDTSTART:{_stamp(first)}\r\n"
        f"DTEND:{_stamp(first + timedelta(hours=1))}\r\nRRULE:FREQ=DAILY;COUNT=6\r\nSUMMARY:Daily sync\r\n"
        "ORGANIZER;CN=Alice:mailto:alice@acme.example\r\n"
        f"ATTENDEE;CN=Me;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{me}\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n")
    occ = [(first + timedelta(days=i)).isoformat() for i in range(6)]
    assert set((await _statuses(client, first, 7)).values()) == {"NEEDS-ACTION"}

    # One occurrence (sent in an older form: the server normalises it).
    r = await client.post(f"{API}/calendar/events/{uid}/rsvp", json={
        "response": "accepted", "scope": "this", "occurrence": _stamp(first + timedelta(days=2))})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["calendar"] == "updated" and out["scope"] == "this" and out["occurrence"] == occ[2]
    st = await _statuses(client, first, 7)
    assert st[occ[2]] == "ACCEPTED" and [st[o] for o in occ if o != occ[2]] == ["NEEDS-ACTION"] * 5
    reply = next(iter((await _reply_part(user, out["outbox"]["id"])).walk("VEVENT")))
    assert reply["recurrence-id"].dt == first + timedelta(days=2)
    assert "RANGE" not in reply["recurrence-id"].params
    assert reply["attendee"].params["PARTSTAT"] == "ACCEPTED"
    assert (await client.post(f"{API}/outbox/{out['outbox']['id']}/cancel")).status_code == 200

    # This occurrence and the ones after it.
    r = await client.post(f"{API}/calendar/events/{uid}/rsvp", json={
        "response": "declined", "scope": "following", "occurrence": occ[4], "comment": "Away from Friday"})
    assert r.status_code == 200, r.text
    out = r.json()
    st = await _statuses(client, first, 7)
    assert [st[o] for o in occ] == ["NEEDS-ACTION", "NEEDS-ACTION", "ACCEPTED", "NEEDS-ACTION", "DECLINED",
                                    "DECLINED"]
    reply = next(iter((await _reply_part(user, out["outbox"]["id"])).walk("VEVENT")))
    assert reply["recurrence-id"].dt == first + timedelta(days=4)
    assert reply["recurrence-id"].params["RANGE"] == "THISANDFUTURE"
    assert (await client.post(f"{API}/outbox/{out['outbox']['id']}/cancel")).status_code == 200

    # "following" from the first occurrence is the whole series; the reply has no RECURRENCE-ID.
    r = await client.post(f"{API}/calendar/events/{uid}/rsvp", json={
        "response": "tentative", "scope": "following", "occurrence": occ[0], "send": False})
    assert r.status_code == 200 and r.json()["scope"] == "all" and r.json()["outbox"] is None
    assert set((await _statuses(client, first, 7)).values()) == {"TENTATIVE"}

    # A series answer for one occurrence needs the occurrence.
    r = await client.post(f"{API}/calendar/events/{uid}/rsvp", json={"response": "accepted", "scope": "this"})
    assert r.status_code == 400


async def test_assistant_rsvp_one_occurrence(client, user):
    """event.rsvp through the assistant: the rsvp tool takes scope and occurrence, and the
    approval executor answers only that occurrence."""
    from mailr.core import permissions

    cals, _, _ = await _connect(client)
    me = await _identity(user)
    first = (datetime.now(UTC) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    uid = f"series-{uuid.uuid4().hex[:10]}"
    cals["Personal"].save_event(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nDTSTAMP:20260920T100000Z\r\nDTSTART:{_stamp(first)}\r\n"
        f"DTEND:{_stamp(first + timedelta(minutes=30))}\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\nSUMMARY:Weekly 1:1\r\n"
        "ORGANIZER;CN=Alice:mailto:alice@acme.example\r\n"
        f"ATTENDEE;CN=Me;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{me}\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n")
    second = (first + timedelta(days=7)).isoformat()
    r = await client.post(f"{API}/agent/tools/rsvp", json={"event_uid": uid, "response": "declined",
                                                            "scope": "this"})
    assert "needs `occurrence`" in r.text
    r = await client.post(f"{API}/agent/tools/rsvp", json={"event_uid": uid, "response": "declined",
                                                            "scope": "this", "occurrence": second})
    assert r.status_code == 200, r.text
    out = r.json()["result"]
    assert out["occurrence"] == second and out["scope"] == "this", out
    if out.get("outbox"):
        assert (await client.post(f"{API}/outbox/{out['outbox']['id']}/cancel")).status_code == 200
    st = await _statuses(client, first, 15)
    assert st[second] == "DECLINED" and st[first.isoformat()] == "NEEDS-ACTION"

    permissions._load_executors()
    async with sessionmaker()() as db:
        res = await permissions._EXECUTORS["calendar.rsvp"](db, await db.get(User, user.id), {
            "event_uid": uid, "response": "accepted", "send": False, "scope": "this",
            "occurrence": first.isoformat()})
        await db.commit()
    assert res["occurrence"] == first.isoformat()
    st = await _statuses(client, first, 15)
    assert st[first.isoformat()] == "ACCEPTED" and st[second] == "DECLINED"
    assert st[(first + timedelta(days=14)).isoformat()] == "NEEDS-ACTION"


# ---------------- tasks: delta reminders, one task, bulk move ----------------


async def test_delta_tasks_carry_absolute_reminders(client, user):
    """/calendar/delta task objects have the task routes' reminders: `reminders` relative,
    `reminders_at` absolute."""
    await _connect(client)
    due = (datetime.now(UTC) + timedelta(days=3)).replace(hour=17, minute=0, second=0, microsecond=0)
    at = (due - timedelta(hours=5)).isoformat()
    r = await client.post(f"{API}/tasks", json={"summary": "Pay rent", "due": due.isoformat(), "reminders": [30],
                                                "reminders_at": [at]})
    assert r.status_code == 200, r.text
    made = r.json()
    assert made["reminders"] == [30] and made["reminders_at"] == [at]
    d = (await client.get(f"{API}/calendar/delta")).json()
    [t] = [o for o in d["objects"] if o["kind"] == "task" and o["uid"] == made["uid"]]
    assert t["reminders"] == [30] and t["reminders_at"] == [at]
    plain = (await client.post(f"{API}/tasks", json={"summary": "No reminders"})).json()
    d = (await client.get(f"{API}/calendar/delta", params={"cursor": d["cursor"]})).json()
    [t] = [o for o in d["objects"] if o["uid"] == plain["uid"]]
    assert t["reminders"] == [] and t["reminders_at"] == []


async def test_get_one_task_for_deep_links(client, user):
    await _connect(client)
    parent = (await client.post(f"{API}/tasks", json={"summary": "Plan the trip", "notes": "x" * 2500})).json()
    child = (await client.post(f"{API}/tasks", json={"summary": "Book the hotel", "parent": parent["uid"]})).json()
    r = await client.get(f"{API}/tasks/{parent['uid']}")
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["summary"] == "Plan the trip" and t["subtask_count"] == 1 and t["open_subtask_count"] == 1
    assert len(t["notes"]) == 2500 and t["parent"] is None and t["parent_summary"] is None
    assert "reminders_at" in t and t["etag"]
    c = (await client.get(f"{API}/tasks/{child['uid']}")).json()
    assert c["parent"] == parent["uid"] and c["parent_summary"] == "Plan the trip"
    assert (await client.get(f"{API}/tasks/no-such-task")).status_code == 404
    # The fixed paths still win over the uid route.
    assert "smart" in (await client.get(f"{API}/tasks/counts")).json()


async def test_bulk_move_does_not_sync_per_task(client, user, monkeypatch):
    """task.bulk_move across lists: one sync check for the whole request, then one PUT and one
    DELETE per task. (A sync per task used to wait on the request's own row locks: >5 s.)"""
    from collections import Counter

    from mailr.calendar import dav

    _, cols, _ = await _connect(client, "Home", "Errands")
    uids = [(await client.post(f"{API}/tasks", json={"summary": f"Task {i}", "list": cols["Home"]["url"]})).json()["uid"]
            for i in range(4)]
    calls: Counter = Counter()
    orig = dav.DavClient.request

    async def counting(self, method, url, **kw):
        calls[method] += 1
        return await orig(self, method, url, **kw)

    monkeypatch.setattr(dav.DavClient, "request", counting)
    r = await client.post(f"{API}/tasks/bulk", json={"uids": uids, "op": "move",
                                                     "args": {"list": cols["Errands"]["url"]}})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["ok"]) == sorted(uids) and r.json()["failed"] == []
    assert {i["list"] for i in r.json()["items"]} == {cols["Errands"]["url"]}
    assert calls["PUT"] == 4 and calls["DELETE"] == 4
    # One sync check, not one per task: the per-task sync is what waited on the row locks.
    assert calls["PROPFIND"] + calls["REPORT"] <= 2, calls
    titles = {t["summary"] for t in (await client.get(f"{API}/tasks", params={"list": cols["Errands"]["url"]})).json()["items"]}
    assert titles == {f"Task {i}" for i in range(4)}


# ---------------- export ----------------


async def test_export_collection_and_event_ics(client, user):
    """event.export_ics: the whole collection (events and tasks, each VTIMEZONE once) and one
    event as text/calendar attachments; only collections the caller may see."""
    _, cols, _ = await _connect(client, "Work", "Other")
    start = (datetime.now(UTC) + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)
    a = (await client.post(f"{API}/calendar/events", json={
        "summary": "Planning", "start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(),
        "tz": "America/Chicago", "calendar": cols["Work"]["url"], "recurrence": "FREQ=WEEKLY;COUNT=4"})).json()
    b = (await client.post(f"{API}/calendar/events", json={
        "summary": "Review", "start": (start + timedelta(days=1)).isoformat(), "tz": "America/Chicago",
        "calendar": cols["Work"]["url"]})).json()
    await client.post(f"{API}/tasks", json={"summary": "Prepare slides", "list": cols["Work"]["url"]})
    await client.post(f"{API}/calendar/events", json={"summary": "Elsewhere", "start": start.isoformat(),
                                                      "calendar": cols["Other"]["url"]})

    r = await client.get(f"{API}/calendar/collections/{cols['Work']['calendar_id']}/export")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/calendar")
    assert r.headers["content-disposition"].startswith('attachment; filename="Work.ics"')
    got = Calendar.from_ical(r.text)
    assert sorted(str(e["summary"]) for e in got.walk("VEVENT")) == ["Planning", "Review"]
    assert [str(t["summary"]) for t in got.walk("VTODO")] == ["Prepare slides"]
    assert [str(z["tzid"]) for z in got.walk("VTIMEZONE")] == ["America/Chicago"]
    assert str(got.get("x-wr-calname")) == "Work"

    r = await client.get(f"{API}/calendar/events/{a['uid']}/ics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    [ev] = Calendar.from_ical(r.text).walk("VEVENT")
    assert str(ev["uid"]) == a["uid"] and ev.get("rrule") is not None
    assert 'filename="Planning.ics"' in r.headers["content-disposition"]
    assert b["uid"] not in r.text

    assert (await client.get(f"{API}/calendar/events/nope/ics")).status_code == 404
    assert (await client.get(f"{API}/calendar/collections/999999999/export")).status_code == 404


# ---------------- e2e seed extras ----------------


async def test_e2e_seed_extras(app):
    """`mailr e2e-seed --extras`: follow-ups (one waiting, one nudged), the list "E2E Errands" and a
    week of events, all visible through the API once the calendar is connected."""
    from httpx import ASGITransport, AsyncClient

    from mailr.dev import e2e

    out = await e2e.seed(extras=True)
    ex = out["extras"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(f"{API}/auth/login", json={"email": out["email"], "password": out["password"]})
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        assert (await c.post(f"{API}/calendar/connect", json={**out["caldav"], "password": "x"})).status_code == 200
        r = await c.get(f"{API}/conversations", params={"view": "all", "q": "in:anywhere subject:E2E", "limit": "100"})
        convs = {x["subject"]: x for x in r.json()["items"]}
        assert convs["E2E Waiting on Dana"]["follow_up_at"] is not None
        assert convs["E2E Waiting on Dana"]["nudged_at"] is None
        assert convs["E2E No reply from Sam"]["nudged_at"] is not None

        lst = (await c.get(f"{API}/tasks", params={"list": ex["task_list"]["url"], "smart": "all"})).json()["items"]
        by = {t["summary"]: t for t in lst}
        assert by["E2E Water plants"]["rrule"] == "FREQ=WEEKLY"
        assert by["E2E Groceries"]["subtask_count"] == 1 and by["E2E Buy milk"]["parent"] == ex["tasks"]["E2E Groceries"]
        assert sorted(by["E2E Dry cleaning"]["categories"]) == ["errands", "home"]

        mon = datetime.fromisoformat(ex["occurrences"]["E2E Standup"]).date()
        evs = (await c.get(f"{API}/calendar/agenda", params={
            "from": mon.isoformat(), "to": (mon + timedelta(days=7)).isoformat()})).json()["events"]
        names = [e["summary"] for e in evs]
        assert names.count("E2E Standup") == 5
        plan = next(e for e in evs if e["uid"] == ex["events"]["E2E Planning"])
        assert plan["is_organizer"] and plan["attendees"] == 3
        assert next(e for e in evs if e["summary"] == "E2E Maybe lunch")["status"] == "TENTATIVE"
        conf = next(e for e in evs if e["summary"] == "E2E Conference")
        assert conf["all_day"] and (datetime.fromisoformat(conf["end"]) - datetime.fromisoformat(conf["start"])).days == 3
        assert "E2E Client call" in names and "E2E Holiday" in names

        cals = {c["url"]: c for c in (await c.get(f"{API}/calendar/calendars")).json()}
        assert cals[ex["calendars"]["E2E Work"]]["read_only"] is False
        ro = cals[ex["read_only_calendar"]]
        assert ro["read_only"] is True and ro["name"] == "E2E Holidays"
        r = await c.post(f"{API}/calendar/events", json={"summary": "x", "start": mon.isoformat(),
                                                         "calendar": ex["read_only_calendar"]})
        assert r.status_code == 400 and "read-only" in r.text

        people = {p["email"]: p["name"] for p in (await c.get(f"{API}/people", params={"limit": 50})).json()}
        assert people.get("priya@example.org") == "Priya Natarajan" and people.get("omar@example.net") == "Omar Haddad"
        assert len(set(ex["people"])) == 5


# ---------------- assistant: "Ask assistant" from an event or a task ----------------


async def test_ask_assistant_about_an_event_or_task_without_a_conversation(client, user):
    """A chat opened from an event or a task carries `focused_event` / `focused_task` at the top
    of `context`: the session keeps it, later turns resolve "this meeting" / "this task" from
    it, and a screen that focuses another item wins for its turn."""
    from mailr.agent import loop, screen
    from mailr.agent.tools import ToolCtx, run_tool
    from mailr.core.auth import ALL_SCOPES, Principal

    await _connect(client)
    start = (datetime.now(UTC) + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    ev = (await client.post(f"{API}/calendar/events", json={"summary": "Design review", "start": start.isoformat(),
                                                            "end": (start + timedelta(hours=1)).isoformat()})).json()
    task = (await client.post(f"{API}/tasks", json={"summary": "Draft slides"})).json()

    keep, ui = loop.split_context({"focused_event": {"uid": ev["uid"], "occurrence": ev["occurrence"]},
                                   "focused_task": task["uid"]})
    assert keep == {"focused_event": {"uid": ev["uid"], "occurrence": ev["occurrence"]}, "focused_task": task["uid"]}
    assert "conversation_id" not in keep and ui == {}
    # A later turn with no screen state: the session's focus fills it in.
    ui = screen.with_focus({}, keep["focused_event"], keep["focused_task"])
    assert ui["calendar"]["focused_event"]["uid"] == ev["uid"] and ui["tasks"]["focused"] == task["uid"]
    # The screen's own focus wins.
    other = screen.sanitize({"tasks": {"smart": "all", "focused": "other-uid"}})
    assert screen.with_focus(other, None, task["uid"])["tasks"]["focused"] == "other-uid"
    # The same keys inside `ui` are shorthand for calendar.focused_event and tasks.focused.
    short = screen.sanitize({"focused_event": {"uid": ev["uid"]}, "focused_task": {"uid": task["uid"]}})
    assert short["calendar"]["focused_event"]["uid"] == ev["uid"] and short["tasks"]["focused"] == task["uid"]

    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        ctx = ToolCtx(db=db, p=Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"}), assistant=True,
                      chat_session_id="s-ask", ui=ui)
        lines = await screen.describe(db, ctx.p, ui)
        assert "The user asked about one event" in lines and "'Design review'" in lines
        assert "The user asked about one task" in lines and "'Draft slides'" in lines
        assert "Calendar on screen" not in lines and "Tasks on screen" not in lines
        assert (await run_tool(ctx, "get_event", {})).data["uid"] == ev["uid"]


async def test_mcp_read_only_and_step_labels():
    from mailr.agent.loop import step_label
    from mailr.agent.tools import TOOLS
    from mailr.mcp.server import READ_ONLY

    assert {"search_calendar", "find_time", "get_free_busy", "list_reminders", "list_tasks"} <= READ_ONLY
    assert READ_ONLY <= set(TOOLS)
    assert await step_label(None, 0, "find_time", {"duration": 30}) == "Finding a time"
    assert await step_label(None, 0, "search_calendar", {"q": "budget"}) == "Searching the calendar for “budget”"
    assert await step_label(None, 0, "rsvp", {"response": "declined"}) == "Declining an invitation"
    assert await step_label(None, 0, "get_free_busy", {}) == "Checking free/busy"
    assert await step_label(None, 0, "manage_lists", {"action": "create", "name": "Errands"}) == \
        "Creating task list “Errands”"
    # Every calendar and task tool has its own words (not the tool name made into a sentence).
    for name in ("search_events", "get_event", "update_event", "delete_event", "cancel_event", "list_tasks",
                 "create_task", "update_task", "complete_task", "move_task", "delete_task", "bulk_tasks",
                 "list_reminders", "manage_calendars"):
        assert await step_label(None, 0, name, {}) != name.replace("_", " ").capitalize(), name


# ---------------- api cells of the catalogue served by existing routes ----------------


async def test_api_create_duplicate_resize_color_defaults(client, user):
    """event.create, event.duplicate (a create with a new UID), event.resize and event.set_color
    (PATCH end / color), task.duplicate (a create), calendar.set_default and list.set_default
    (PUT /calendar/defaults)."""
    _, cols, _ = await _connect(client, "Home", "Work")
    start = (datetime.now(UTC) + timedelta(days=3)).replace(hour=13, minute=0, second=0, microsecond=0)
    body = {"summary": "Budget", "start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(),
            "calendar": cols["Work"]["url"], "color": "#3366ff"}
    a = (await client.post(f"{API}/calendar/events", json=body)).json()
    b = (await client.post(f"{API}/calendar/events", json={**body, "start": (start + timedelta(days=1)).isoformat(),
                                                           "end": (start + timedelta(days=1, hours=1)).isoformat()})).json()
    assert a["uid"] != b["uid"] and a["color"] == b["color"] == "#3366ff"
    r = await client.patch(f"{API}/calendar/events/{a['uid']}", json={
        "end": (start + timedelta(minutes=90)).isoformat(), "color": "tomato", "etag": a["etag"]})
    assert r.status_code == 200, r.text
    assert r.json()["end"] == (start + timedelta(minutes=90)).isoformat() and r.json()["color"] == "tomato"
    # "" takes the event's own colour away (the detail's "Calendar colour", and the Undo of a colour).
    r = await client.patch(f"{API}/calendar/events/{a['uid']}", json={"color": "", "etag": r.json()["etag"]})
    assert r.status_code == 200, r.text
    assert r.json()["color"] is None
    t = (await client.post(f"{API}/tasks", json={"summary": "Call the bank", "priority": 1})).json()
    t2 = (await client.post(f"{API}/tasks", json={"summary": t["summary"], "priority": t["priority"]})).json()
    assert t2["uid"] != t["uid"] and t2["priority"] == 1
    r = await client.put(f"{API}/calendar/defaults", json={"calendar": cols["Work"]["url"],
                                                           "task_list": cols["Home"]["url"]})
    assert r.status_code == 200, r.text
    assert r.json()["calendar"] == cols["Work"]["url"] and r.json()["task_list"] == cols["Home"]["url"]


# ---------------- calendar settings keys ----------------


async def test_calendar_settings_keys_are_checked(client, user):
    """PATCH /me/settings: the flat calendar keys the web and Android apps read are accepted,
    checked and normalised; null (or "" for the zone, week start and time format) clears one;
    find-time reads calendar_working_hours."""
    good = {"calendar_default_duration": 30, "calendar_snap": 10,
            "calendar_working_hours": {"start": "8:30", "end": "16:00", "days": [1, 2, 3, 4, 0]},
            "calendar_dim_past": True, "calendar_week_numbers": True, "calendar_conflicts": False,
            "calendar_video_template": "https://meet.example.org/{id}", "morning_hour": 8,
            "timezone": "Europe/London", "week_start": "Monday", "time_format": "24h"}
    r = await client.patch(f"{API}/me/settings", json=good)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["calendar_working_hours"] == {"start": "08:30", "end": "16:00", "days": [1, 2, 3, 4, 7]}
    assert s["week_start"] == "monday" and s["calendar_snap"] == 10 and s["morning_hour"] == 8
    for bad in ({"calendar_snap": "ten"}, {"calendar_default_duration": 0}, {"morning_hour": 24},
                {"calendar_dim_past": "yes"}, {"calendar_working_hours": {"start": "17:00", "end": "09:00"}},
                {"calendar_working_hours": {"start": "09:00", "end": "17:00", "days": [8]}},
                {"calendar_video_template": "javascript:alert(1)"}, {"timezone": "Mars/Olympus"},
                {"week_start": 9}, {"time_format": "36h"}):
        r = await client.patch(f"{API}/me/settings", json=bad)
        assert r.status_code == 400, (bad, r.text)
    r = await client.patch(f"{API}/me/settings", json={"timezone": "", "week_start": None, "calendar_snap": None})
    assert r.status_code == 200
    assert not {"timezone", "week_start", "calendar_snap"} & set(r.json())
    assert r.json()["calendar_default_duration"] == 30

    from mailr.calendar.freebusy import working_hours

    async with sessionmaker()() as db:
        assert (await working_hours(db, user.id, None))["start"] == "08:30"


# ---------------- W3 follow-ups: import, decline a proposal, invite outcome, reminders ----------------

LONDON_NY = """BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Other//EN\r
METHOD:PUBLISH\r
BEGIN:VTIMEZONE\r
TZID:Europe/London\r
BEGIN:STANDARD\r
DTSTART:19701025T020000\r
TZOFFSETFROM:+0100\r
TZOFFSETTO:+0000\r
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU\r
END:STANDARD\r
BEGIN:DAYLIGHT\r
DTSTART:19700329T010000\r
TZOFFSETFROM:+0000\r
TZOFFSETTO:+0100\r
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU\r
END:DAYLIGHT\r
END:VTIMEZONE\r
BEGIN:VTIMEZONE\r
TZID:America/New_York\r
BEGIN:STANDARD\r
DTSTART:19701101T020000\r
TZOFFSETFROM:-0400\r
TZOFFSETTO:-0500\r
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\r
END:STANDARD\r
BEGIN:DAYLIGHT\r
DTSTART:19700308T020000\r
TZOFFSETFROM:-0500\r
TZOFFSETTO:-0400\r
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\r
END:DAYLIGHT\r
END:VTIMEZONE\r
BEGIN:VEVENT\r
UID:{a}\r
DTSTAMP:20260920T100000Z\r
DTSTART;TZID=Europe/London:20261106T090000\r
DTEND;TZID=America/New_York:20261106T100000\r
SUMMARY:Flight to New York\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:{b}\r
DTSTAMP:20260920T100000Z\r
DTSTART;TZID=Europe/London:20261109T100000\r
DTEND;TZID=Europe/London:20261109T110000\r
RRULE:FREQ=WEEKLY;COUNT=3\r
SUMMARY:Weekly review\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:{b}\r
DTSTAMP:20260920T100000Z\r
RECURRENCE-ID;TZID=Europe/London:20261116T100000\r
DTSTART;TZID=Europe/London:20261116T140000\r
DTEND;TZID=Europe/London:20261116T150000\r
SUMMARY:Weekly review (moved)\r
END:VEVENT\r
END:VCALENDAR\r
"""


async def test_import_ics_keeps_each_events_zones(client, user):
    """event.import_ics: each UID becomes one resource (a series with its override), the zones
    of the file are kept (an end in another zone too), and a UID already there is skipped."""
    _, cols, _ = await _connect(client, "Home", "Trips")
    a, b = f"flight-{uuid.uuid4().hex[:8]}", f"review-{uuid.uuid4().hex[:8]}"
    ics = LONDON_NY.replace("{a}", a).replace("{b}", b)
    r = await client.post(f"{API}/calendar/import", json={"ics": ics, "calendar": str(cols["Trips"]["calendar_id"])})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["calendar"] == cols["Trips"]["url"] and out["failed"] == [] and out["skipped"] == []
    assert sorted(i["uid"] for i in out["imported"]) == sorted([a, b])
    flight = (await client.get(f"{API}/calendar/events/{a}")).json()
    assert flight["tz"] == "Europe/London" and flight["end_tz"] == "America/New_York"
    assert flight["start"] == "2026-11-06T09:00:00+00:00" and flight["end"] == "2026-11-06T15:00:00+00:00"
    assert flight["calendar"] == cols["Trips"]["url"]
    r = await client.get(f"{API}/calendar/agenda", params={"from": "2026-11-09", "to": "2026-11-24"})
    reviews = [e for e in r.json()["events"] if e["uid"] == b]
    assert [e["summary"] for e in reviews] == ["Weekly review", "Weekly review (moved)", "Weekly review"]
    assert reviews[1]["start"] == "2026-11-16T14:00:00+00:00"
    # Again: both are there already.
    again = (await client.post(f"{API}/calendar/import", json={"ics": ics})).json()
    assert again["imported"] == [] and {s["reason"] for s in again["skipped"]} == {"exists"}
    assert (await client.post(f"{API}/calendar/import", json={"ics": "not a calendar"})).status_code == 400
    assert (await client.post(f"{API}/calendar/import", json={})).status_code == 400


@pytest.fixture
def org():
    import helpers.calendar_organizer as o
    return o


async def test_decline_a_proposed_time_sends_declinecounter(client, user, org):
    """event.decline_proposal: the event stays; the guest who proposed gets METHOD:DECLINECOUNTER."""
    me, _ = await org._setup(client, user)
    start = org._start(6)
    ev = (await client.post(f"{API}/calendar/events", json={"summary": "Budget review", "start": start.isoformat(),
                                                             "attendees": [org.DANA]})).json()
    uid = ev["uid"]
    proposed = start + timedelta(hours=3)
    counter = org._cal(f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261001T120000Z\r\nSEQUENCE:0\r\n"
                       f"DTSTART:{proposed:%Y%m%dT%H%M%SZ}\r\nDTEND:{proposed + timedelta(hours=1):%Y%m%dT%H%M%SZ}\r\n"
                       f"ORGANIZER:mailto:{me}\r\nATTENDEE;PARTSTAT=TENTATIVE:mailto:{org.DANA}\r\nEND:VEVENT\r\n",
                       "COUNTER")
    mid = await org._ingest(user, org._itip_mail(counter, org.DANA, me, method="COUNTER"))
    n = len(await org._outbox(user))
    r = await client.post(f"{API}/calendar/events/{uid}/counter/decline", json={"message_id": mid,
                                                                              "comment": "That slot is taken."})
    assert r.status_code == 200, r.text
    assert r.json()["declined"] == org.DANA and len(r.json()["invites"]["outbox_ids"]) == 1
    [ob] = (await org._outbox(user))[n:]
    assert ob.payload["subject"] == "New time declined: Budget review"
    assert [t["email"] for t in ob.payload["to"]] == [org.DANA]
    method, text = org._cal_part(await org._mime(user, ob))
    assert method == "DECLINECOUNTER" and f"UID:{uid}" in text and "COMMENT:That slot is taken." in text
    assert (await client.get(f"{API}/calendar/events/{uid}")).json()["start"] == start.isoformat()
    # A message that is not a proposal for this event.
    other = (await client.post(f"{API}/calendar/events", json={"summary": "x", "start": start.isoformat()})).json()
    r = await client.post(f"{API}/calendar/events/{other['uid']}/counter/decline", json={"message_id": mid})
    assert r.status_code == 409


async def test_organizer_update_outcome_has_previous_time_and_zone(client, user, org):
    me, username = await org._setup(client, user)
    uid = f"alice-{uuid.uuid4().hex[:8]}"
    await org._put_invitation(user, username, uid, me)
    upd = org._cal(org._vevent(uid=uid, seq=1, organizer="alice@acme.example", guests=(me,), alarm=False)
                   .replace("DTSTART:20261005T090000Z", "DTSTART;TZID=America/New_York:20261005T090000")
                   .replace("DTEND:20261005T100000Z", "DTEND;TZID=America/New_York:20261005T100000"), "REQUEST")
    mid = await org._ingest(user, org._itip_mail(upd, "Alice <alice@acme.example>", me, method="REQUEST"))
    out = await org._itip(user, mid)
    assert out["applied"] and out["time_changed"], out
    assert out["previous"] == {"start": "2026-10-05T09:00:00+00:00", "end": "2026-10-05T10:00:00+00:00",
                               "recurrence_id": None}
    assert out["organizer_tz"] == "America/New_York"
    inv = (await client.get(f"{API}/messages/{mid}/invite")).json()
    assert inv["tz"] == "America/New_York" and inv["itip"]["previous"]["start"] == "2026-10-05T09:00:00+00:00"


async def test_invite_conflicts_are_real_overlaps(client, user, org):
    """The invite summary's conflicts: busy timed events that overlap the invitation's time
    (not free, cancelled or declined ones, not the rest of the day)."""
    me, _ = await org._setup(client, user)
    start = org._start(5, 14)

    async def mk(summary, s, e, **kw):
        r = await client.post(f"{API}/calendar/events", json={"summary": summary, "start": s.isoformat(),
                                                               "end": e.isoformat(), **kw})
        assert r.status_code == 200, r.text

    await mk("Overlaps", start + timedelta(minutes=30), start + timedelta(hours=2))
    await mk("Same morning", start - timedelta(hours=4), start - timedelta(hours=3))
    await mk("Free time", start, start + timedelta(hours=1), transparent=True)
    await mk("Cancelled", start, start + timedelta(hours=1), status="CANCELLED")
    await mk("Ends as it starts", start - timedelta(hours=1), start)
    uid = f"inv-{uuid.uuid4().hex[:8]}"
    req = org._cal(org._vevent(uid=uid, start=f"{start:%Y%m%dT%H%M%SZ}",
                               end=f"{start + timedelta(hours=1):%Y%m%dT%H%M%SZ}", organizer="alice@acme.example",
                               guests=(me,)), "REQUEST")
    mid = await org._ingest(user, org._itip_mail(req, "Alice <alice@acme.example>", me, method="REQUEST"))
    inv = (await client.get(f"{API}/messages/{mid}/invite")).json()
    assert [c["summary"] for c in inv["conflicts"]] == ["Overlaps"]


async def test_reminder_settings_missed_and_sound_and_join_url(client, user, monkeypatch):
    from mailr.calendar import planner
    from mailr.models import ReminderSchedule

    s = (await client.get(f"{API}/reminders/settings")).json()
    assert s["missed"] is True and s["sound"] is False
    r = await client.put(f"{API}/reminders/settings", json={"sound": True})
    assert r.status_code == 200 and r.json()["sound"] is True and r.json()["missed"] is True

    await _connect(client)
    start = (datetime.now(UTC) + timedelta(minutes=3)).replace(second=0, microsecond=0)
    ev = (await client.post(f"{API}/calendar/events", json={
        "summary": "Standup", "start": start.isoformat(), "end": (start + timedelta(minutes=15)).isoformat(),
        "location": "https://zoom.us/j/123456?pwd=x", "reminders": [10]})).json()
    async with sessionmaker()() as db:
        row = (await db.execute(select(ReminderSchedule).where(ReminderSchedule.user_id == user.id,
                                                               ReminderSchedule.uid == ev["uid"]))).scalar_one()
        data = await planner.payload_for(db, row, missed=False, sound=True)
    assert data["join_url"] == "https://zoom.us/j/123456?pwd=x" and data["sound"] is True
    assert planner.join_url(None, "Room 4", "Join: https://meet.google.com/abc-defg-hij today") == \
        "https://meet.google.com/abc-defg-hij"
    assert planner.join_url("https://example.org/agenda", "", "") is None

    # Missed reminders off: a late reminder is not shown (it is marked fired).
    r = await client.put(f"{API}/reminders/settings", json={"missed": False})
    assert r.json()["missed"] is False
    sent = []

    async def notify(db, uid, data):
        sent.append(data)

    from mailr.core import events

    monkeypatch.setattr(events, "notify", notify)
    async with sessionmaker()() as db:
        row = await db.get(ReminderSchedule, row.id)
        row.fire_at = datetime.now(UTC) - timedelta(minutes=5)
        await db.commit()
        rid, at = row.id, row.fire_at.isoformat()
    await planner.fire({"id": rid, "fire_at": at}, None)
    async with sessionmaker()() as db:
        assert (await db.get(ReminderSchedule, rid)).fired_at is not None
    assert sent == []
