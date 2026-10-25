# ACT-SURFACES: api assistant
""""No reminders" is silent (event.create, event.edit).

An event given no reminders on purpose (`reminders: []` on a create or an edit, from the REST API,
the editors or the agent tools) carries X-MAILR-NO-REMINDER:TRUE (ical.NO_REMINDER). The planner
then skips the user's fallback default, which an event without VALARMs otherwise gets; the event
API says `no_reminders`, so the phone's planner skips it too. Adding reminders drops the marker,
other edits keep it, and it survives CalDAV (Radicale) and edits made in other apps. Events
without alarms and without the marker keep the fallback default.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import caldav
import pytest
from dev_services import RADICALE
from dev_services import radicale_up as _radicale_up
from helpers.agent_calendar_v2 import _allow, _ctx
from helpers.calendar import _connect
from icalendar import Calendar
from sqlalchemy import select

from mailr.agent.tools import run_tool
from mailr.calendar import ical, planner, sync
from mailr.db import sessionmaker
from mailr.models import CalObject, Connector, ReminderSchedule

API = "/api/v1"
CHICAGO = ZoneInfo("America/Chicago")
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=CHICAGO)
CFG = {**planner.DEFAULTS, "default_timed_event": 10, "default_all_day_event": 900}
radicale = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


def _z(d: datetime) -> str:
    return d.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _ics(uid: str, start: datetime | date, extra: str = "", override: str = "") -> str:
    if isinstance(start, datetime):
        when = f"DTSTART:{_z(start)}\r\nDTEND:{_z(start + timedelta(hours=1))}\r\n"
    else:
        when = f"DTSTART;VALUE=DATE:{start:%Y%m%d}\r\nDTEND;VALUE=DATE:{start + timedelta(days=1):%Y%m%d}\r\n"
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:20260901T000000Z\r\n{when}SUMMARY:Event {uid}\r\n{extra}END:VEVENT\r\n"
            f"{override}END:VCALENDAR\r\n")


# ---------------- the planner ----------------


def test_planner_skips_the_fallback_for_a_marked_event():
    start = NOW + timedelta(hours=3)
    plain = planner.plan_event(_ics("a", start), "a", CFG, NOW, CHICAGO, set())
    assert [(p.minutes, p.source) for p in plain] == [(10, "default")]
    assert planner.plan_event(_ics("b", start, "X-MAILR-NO-REMINDER:TRUE\r\n"), "b", CFG, NOW, CHICAGO, set()) == []
    # All-day too.
    day = (NOW + timedelta(days=2)).date()
    assert [p.minutes for p in planner.plan_event(_ics("c", day), "c", CFG, NOW, CHICAGO, set())] == [900]
    assert planner.plan_event(_ics("d", day, "X-MAILR-NO-REMINDER:TRUE\r\n"), "d", CFG, NOW, CHICAGO, set()) == []
    # A VALARM another app added wins over a marker it left behind.
    alarm = "X-MAILR-NO-REMINDER:TRUE\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:x\r\nTRIGGER:-PT30M\r\nEND:VALARM\r\n"
    assert [p.minutes for p in planner.plan_event(_ics("e", start, alarm), "e", CFG, NOW, CHICAGO, set())] == [30]


def test_planner_marker_on_one_occurrence_only():
    start = NOW + timedelta(hours=3)
    second = start + timedelta(days=1)
    ov = (f"BEGIN:VEVENT\r\nUID:r\r\nDTSTAMP:20260901T000000Z\r\nRECURRENCE-ID:{_z(second)}\r\n"
          f"DTSTART:{_z(second)}\r\nDTEND:{_z(second + timedelta(hours=1))}\r\nSUMMARY:Quiet one\r\n"
          "X-MAILR-NO-REMINDER:TRUE\r\nEND:VEVENT\r\n")
    got = planner.plan_event(_ics("r", start, "RRULE:FREQ=DAILY;COUNT=3\r\n", ov), "r", CFG, NOW, CHICAGO, set())
    occ = {p.occurrence for p in got}
    assert ical.canonical_occurrence(start) in occ and ical.canonical_occurrence(second) not in occ
    assert len(got) == 2


def test_set_event_reminders_writes_and_drops_the_marker():
    cal = Calendar.from_ical(_ics("m", NOW + timedelta(hours=3)))
    ev = next(iter(cal.walk("VEVENT")))
    ical.set_event_reminders(ev, [], "x")
    assert ical.no_reminder(ev) and "X-MAILR-NO-REMINDER:TRUE" in cal.to_ical().decode()
    ical.set_event_reminders(ev, [], "x")  # once only
    assert cal.to_ical().decode().count("X-MAILR-NO-REMINDER") == 1
    ical.set_event_reminders(ev, [15], "x")
    assert not ical.no_reminder(ev) and ical.alarm_minutes(ev) == [15]
    assert "X-MAILR-NO-REMINDER" not in cal.to_ical().decode()
    f = ical.component_fields(ev, cal, CHICAGO)
    assert f["no_reminder"] is False
    ical.set_event_reminders(ev, [], "x")
    assert ical.component_fields(ev, cal, CHICAGO)["no_reminder"] is True
    # build_event: [] marks, left out does not.
    built = ical.build_event({"summary": "s", "start": "2026-10-05T10:00:00+00:00", "reminders": []}, CHICAGO)
    assert "X-MAILR-NO-REMINDER:TRUE" in built.to_ical().decode()
    plain = ical.build_event({"summary": "s", "start": "2026-10-05T10:00:00+00:00"}, CHICAGO)
    assert "X-MAILR-NO-REMINDER" not in plain.to_ical().decode()


# ---------------- REST, tools, CalDAV ----------------


def _start(days=3, hour=15):
    return (datetime.now(UTC) + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


async def _obj(uid: str) -> CalObject:
    async with sessionmaker()() as db:
        return (await db.execute(select(CalObject).where(CalObject.uid == uid))).scalars().first()


async def _planned(user_id: int, uid: str) -> list[int]:
    async with sessionmaker()() as db:
        rows = (await db.execute(select(ReminderSchedule).where(ReminderSchedule.user_id == user_id,
                                                                ReminderSchedule.uid == uid))).scalars()
        return sorted(r.minutes for r in rows if r.source in ("valarm", "default"))


def _radicale_event(username: str, uid: str):
    cal = caldav.DAVClient(url=RADICALE, username=username, password="x").principal().calendars()[0]
    return cal.event_by_uid(uid)


async def _resync(user_id: int) -> None:
    async with sessionmaker()() as db:
        cid = (await db.execute(select(Connector.id).where(Connector.user_id == user_id,
                                                           Connector.kind == "caldav"))).scalars().first()
    await sync.sync_connector(cid, full=True)


@radicale
async def test_rest_create_and_edit_round_trip(client, user):
    username = await _connect(client)
    r = await client.put(f"{API}/reminders/settings", json={"default_timed_event": 10})
    assert r.status_code == 200
    start = _start(days=1, hour=(datetime.now(UTC).hour + 3) % 24)
    if start < datetime.now(UTC) + timedelta(hours=1):
        start += timedelta(days=1)

    quiet = (await client.post(f"{API}/calendar/events", json={"summary": "Quiet", "start": start.isoformat(),
                                                               "reminders": []})).json()
    assert quiet["reminders"] == [] and quiet["no_reminders"] is True
    obj = await _obj(quiet["uid"])
    assert obj.no_reminder and "X-MAILR-NO-REMINDER:TRUE" in obj.ics and "VALARM" not in obj.ics
    assert await _planned(user.id, quiet["uid"]) == []
    # Stored in Radicale as written.
    assert "X-MAILR-NO-REMINDER:TRUE" in _radicale_event(username, quiet["uid"]).data

    # The default (left out) writes a VALARM and no marker.
    plain = (await client.post(f"{API}/calendar/events", json={"summary": "Plain", "start": start.isoformat()})).json()
    assert plain["reminders"] == [10] and plain["no_reminders"] is False
    assert "X-MAILR-NO-REMINDER" not in (await _obj(plain["uid"])).ics

    # An edit of something else keeps the marker.
    r = await client.patch(f"{API}/calendar/events/{quiet['uid']}", json={"summary": "Quiet, renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["no_reminders"] is True and (await _obj(quiet["uid"])).no_reminder

    # Adding a reminder drops it; the reminder is planned.
    r = await client.patch(f"{API}/calendar/events/{quiet['uid']}", json={"reminders": [30]})
    assert r.json()["reminders"] == [30] and r.json()["no_reminders"] is False
    obj = await _obj(quiet["uid"])
    assert not obj.no_reminder and "X-MAILR-NO-REMINDER" not in obj.ics
    assert await _planned(user.id, quiet["uid"]) == [30]

    # Clearing them again marks it, and nothing is planned (not even the default 10).
    r = await client.patch(f"{API}/calendar/events/{quiet['uid']}", json={"reminders": []})
    assert r.json()["no_reminders"] is True
    assert await _planned(user.id, quiet["uid"]) == []

    # The agenda carries the flag.
    got = (await client.get(f"{API}/calendar/agenda", params={
        "from": (start - timedelta(days=1)).isoformat(), "to": (start + timedelta(days=1)).isoformat()})).json()
    rows = got if isinstance(got, list) else got.get("events", got.get("items", []))
    flags = {e["summary"]: e["no_reminders"] for e in rows}
    assert flags["Quiet, renamed"] is True and flags["Plain"] is False

    # Another app edits the event (keeps unknown properties, as RFC 5545 asks): still silent.
    ev = _radicale_event(username, quiet["uid"])
    ev.data = ev.data.replace("SUMMARY:Quiet\\, renamed", "SUMMARY:Quiet elsewhere")
    assert "Quiet elsewhere" in ev.data
    ev.save()
    await _resync(user.id)
    obj = await _obj(quiet["uid"])
    assert obj.summary == "Quiet elsewhere" and obj.no_reminder
    assert await _planned(user.id, quiet["uid"]) == []

    # Another app adds an alarm but leaves the marker: the alarm counts.
    ev = _radicale_event(username, quiet["uid"])
    ev.data = ev.data.replace("END:VEVENT", "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:x\r\n"
                                            "TRIGGER:-PT20M\r\nEND:VALARM\r\nEND:VEVENT")
    ev.save()
    await _resync(user.id)
    obj = await _obj(quiet["uid"])
    assert obj.alarms == [20] and not obj.no_reminder
    assert await _planned(user.id, quiet["uid"]) == [20]


@radicale
async def test_calendar_default_of_none_marks_and_user_none_does_not(client, user):
    await _connect(client)
    await client.put(f"{API}/reminders/settings", json={"default_timed_event": None})
    start = _start()
    bare = (await client.post(f"{API}/calendar/events", json={"summary": "Bare", "start": start.isoformat()})).json()
    # The user's default is none: no alarm, but no marker either (a later default applies).
    assert bare["no_reminders"] is False and "X-MAILR-NO-REMINDER" not in (await _obj(bare["uid"])).ics
    quiet = (await client.post(f"{API}/calendar/collections", json={
        "name": "Quiet", "kind": "events", "default_reminders": {"timed": []}})).json()
    ev = (await client.post(f"{API}/calendar/events", json={"summary": "In quiet", "start": start.isoformat(),
                                                            "calendar": quiet["url"]})).json()
    assert ev["reminders"] == [] and ev["no_reminders"] is True


@radicale
async def test_tools_write_the_marker(client, user):
    await _connect(client)
    await _allow(user.id, "calendar.create")
    await _allow(user.id, "calendar.update")
    start = _start()
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = (await run_tool(ctx, "create_event", {"summary": "No alarm", "start": start.isoformat(),
                                                    "reminders": []})).data
        assert res["reminders_source"] == "explicit" and res["reminders_text"] == "none"
        assert (await _obj(res["uid"])).no_reminder
        plain = (await run_tool(ctx, "create_event", {"summary": "Default", "start": start.isoformat()})).data
        assert not (await _obj(plain["uid"])).no_reminder
        await run_tool(ctx, "update_event", {"uid": plain["uid"], "reminders": []})
        assert (await _obj(plain["uid"])).no_reminder
        await run_tool(ctx, "update_event", {"uid": plain["uid"], "reminders": [60]})
        obj = await _obj(plain["uid"])
        assert not obj.no_reminder and obj.alarms == [60]
