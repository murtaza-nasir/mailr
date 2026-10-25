# ACT-SURFACES: web.notification api
"""The server reminder planner: parity with Android's ReminderPlan.kt, wall-clock
all-day reminders across DST, the firing job (reminder.fire), SSE, opt-in push, snoozes,
handled and missed reminders, the tray and the settings. No CalDAV server: rows go straight
into the mirror with mirror.upsert_object."""

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from mailr.calendar import mirror, planner
from mailr.db import sessionmaker
from mailr.models import (
    CalCollection,
    CalObject,
    Connector,
    Job,
    PushTarget,
    ReminderSchedule,
    ReminderState,
)

CHICAGO = ZoneInfo("America/Chicago")
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=CHICAGO)
CFG = {**planner.DEFAULTS, "default_timed_event": 10, "default_all_day_event": None}


def _cal(body: str) -> str:
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\n" + body + "END:VCALENDAR\r\n"


def _z(d: datetime) -> str:
    return d.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _alarms(mins: list[int]) -> str:
    return "".join(f"BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:x\r\nTRIGGER:-PT{m}M\r\nEND:VALARM\r\n"
                   if m >= 0 else f"BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:x\r\nTRIGGER:PT{-m}M\r\nEND:VALARM\r\n"
                   for m in mins)


def _event(uid: str, start: datetime, minutes: int = 60, alarms: list[int] = (), extra: str = "",
           summary: str | None = None) -> str:
    return _cal(f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20260901T000000Z\r\nDTSTART:{_z(start)}\r\n"
                f"DTEND:{_z(start + timedelta(minutes=minutes))}\r\nSUMMARY:{summary or 'Event ' + uid}\r\n{extra}"
                f"{_alarms(list(alarms))}END:VEVENT\r\n")


def _allday(uid: str, day: date, alarms: list[int] = ()) -> str:
    return _cal(f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20260901T000000Z\r\nDTSTART;VALUE=DATE:{day:%Y%m%d}\r\n"
                f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}\r\nSUMMARY:Holiday\r\n{_alarms(list(alarms))}"
                "END:VEVENT\r\n")


def _keys(planned) -> set[str]:
    return {p.key for p in planned}


def _utc(d: datetime) -> str:
    return d.astimezone(UTC).isoformat()


# ---------------- parity with ReminderPlan.kt (ReminderPlanTest.kt cases) ----------------


def test_planner_parity_events_use_their_own_reminders_else_the_default():
    a = planner.plan_event(_event("a", NOW + timedelta(hours=2), alarms=[30, 5]), "a", CFG, NOW, CHICAGO, set())
    b = planner.plan_event(_event("b", NOW + timedelta(hours=3)), "b", CFG, NOW, CHICAGO, set())
    assert _keys(a) | _keys(b) == {f"e|a|{_utc(NOW + timedelta(hours=2))}|30", f"e|a|{_utc(NOW + timedelta(hours=2))}|5",
                                   f"e|b|{_utc(NOW + timedelta(hours=3))}|10"}
    a30 = next(p for p in a if p.minutes == 30)
    assert a30.fire_at == NOW + timedelta(hours=2, minutes=-30) and a30.source == "valarm"
    assert next(iter(b)).source == "default"
    assert next(p for p in a if p.minutes == 5).detail == "14:00 – 15:00"


def test_planner_details_follow_the_users_clock():
    """The sheet, the tray and the push show these texts: a 12-hour user reads "2:00 – 3:00 PM"
    (the reminder sheet showed "16:15 – 16:45" under a 12-hour Region setting)."""
    twelve = {**CFG, "clock24": False}
    a = planner.plan_event(_event("a", NOW + timedelta(hours=2), alarms=[5]), "a", twelve, NOW, CHICAGO, set())
    assert a[0].detail == "2:00 – 3:00 PM"
    late = planner.plan_event(_event("b", NOW - timedelta(minutes=30), minutes=90, alarms=[0]), "b", twelve,
                              NOW + timedelta(minutes=-29), CHICAGO, set())
    assert late[0].detail == "11:30 AM – 1:00 PM"
    assert planner.task_detail(NOW + timedelta(hours=4), CHICAGO, NOW, False) == "Due today, 4:00 PM"
    assert planner.task_detail(NOW + timedelta(hours=4), CHICAGO, NOW, True) == "Due today, 16:00"


async def test_a_new_clock_setting_rewrites_the_planned_details(client, user):
    """Settings → Region → Time format replans, so the stored texts change with it; unset reads as
    12 hours, as the server's other texts."""
    assert (await client.patch("/api/v1/me/settings", json={"timezone": "America/Chicago"})).status_code == 200
    _, coll = await _setup(user)
    start = (datetime.now(UTC) + timedelta(days=1)).astimezone(CHICAGO).replace(hour=16, minute=15, second=0,
                                                                                microsecond=0)
    await _put(user, coll, _event("clock-1", start, minutes=30, alarms=[10]), "clock-1")
    [row] = (await _rows(user)).values()
    assert row.detail.startswith("4:15 – 4:45 PM")
    assert (await client.patch("/api/v1/me/settings", json={"time_format": "24h"})).status_code == 200
    [row] = (await _rows(user)).values()
    assert row.detail.startswith("16:15 – 16:45")


def test_planner_parity_skips_cancelled_declined_ended_and_far_events():
    me = {"me@example.com"}
    cases = [
        _event("x", NOW + timedelta(hours=1), extra="STATUS:CANCELLED\r\n"),
        _event("d", NOW + timedelta(hours=1), extra="ORGANIZER:mailto:o@x.org\r\n"
                                                    "ATTENDEE;PARTSTAT=DECLINED:mailto:me@example.com\r\n"),
        _event("old", NOW - timedelta(hours=3)),
        _event("far", NOW + timedelta(days=20)),
    ]
    for ics in cases:
        assert planner.plan_event(ics, "u", CFG, NOW, CHICAGO, me) == []


def test_planner_parity_all_day_events_anchor_at_local_midnight():
    p = {x.minutes: x for x in planner.plan_event(_allday("h", date(2026, 9, 30), [-540, 900]), "h", CFG, NOW, CHICAGO,
                                                  set())}
    assert p[-540].fire_at == datetime(2026, 9, 30, 9, 0, tzinfo=CHICAGO)
    assert p[900].fire_at == datetime(2026, 9, 29, 9, 0, tzinfo=CHICAGO)
    assert p[900].key == "e|h|2026-09-30|900" and p[900].detail == "All day"
    # No default for all-day events without their own reminder.
    assert planner.plan_event(_allday("h", date(2026, 9, 30)), "h", CFG, NOW, CHICAGO, set()) == []


def _task(uid: str, due: datetime | date | None, *, status: str = "NEEDS-ACTION", alarms: list[int] = ()) -> CalObject:
    o = CalObject(uid=uid, kind="task", summary=f"Task {uid}", status=status, alarms=list(alarms), completed_at=None)
    o.due_date = due if isinstance(due, date) and not isinstance(due, datetime) else None
    o.due_at = due.astimezone(UTC) if isinstance(due, datetime) else None
    return o


def test_planner_parity_tasks_remind_at_due_time_or_morning_for_date_only():
    timed = planner.plan_task(_task("t1", NOW + timedelta(hours=5)), CFG, NOW, CHICAGO)
    dated = planner.plan_task(_task("t2", date(2026, 10, 2)), CFG, NOW, CHICAGO)
    assert planner.plan_task(_task("t3", NOW + timedelta(hours=1), status="COMPLETED"), CFG, NOW, CHICAGO) == []
    assert planner.plan_task(_task("t4", None), CFG, NOW, CHICAGO) == []
    assert [p.fire_at for p in timed] == [NOW + timedelta(hours=5)]
    assert [p.key for p in timed] == [f"t|t1|{_utc(NOW + timedelta(hours=5))}|0"]
    assert [(p.key, p.fire_at) for p in dated] == [("t|t2|2026-10-02|-540", datetime(2026, 10, 2, 9, tzinfo=CHICAGO))]
    assert dated[0].detail == "Due Fri, Oct 2"
    assert planner.plan_task(_task("t1", NOW + timedelta(hours=5)), {**CFG, "tasks": False}, NOW, CHICAGO) == []
    assert planner.plan_task(_task("t1", NOW + timedelta(hours=5)), {**CFG, "default_task": False}, NOW, CHICAGO) == []


def test_planner_parity_is_deterministic_and_keys_are_canonical():
    ics = _event("a", NOW + timedelta(hours=2))
    assert planner.plan_event(ics, "a", CFG, NOW, CHICAGO, set()) == planner.plan_event(ics, "a", CFG, NOW, CHICAGO,
                                                                                         set())
    weekly = _cal("BEGIN:VEVENT\r\nUID:w\r\nDTSTAMP:20260901T000000Z\r\nDTSTART;TZID=America/Chicago:20260928T090000\r\n"
                  "DTEND;TZID=America/Chicago:20260928T093000\r\nRRULE:FREQ=DAILY;COUNT=20\r\nSUMMARY:Standup\r\n"
                  "END:VEVENT\r\n")
    keys = sorted(_keys(planner.plan_event(weekly, "w", CFG, NOW, CHICAGO, set())))
    # Tomorrow through the 8-day horizon; today's 9:00 has ended.
    assert keys[0] == "e|w|2026-09-30T14:00:00+00:00|10" and len(keys) == 8


# ---------------- wall-clock reminders across DST ----------------


def test_all_day_and_date_only_reminders_keep_the_wall_clock_across_dst():
    """US DST ends on 1 Nov 2026. "2 days before at 9:00" (2340 minutes before the day) of an
    all-day event on 2 Nov fires at 9:00 CDT on 31 Oct, not at 10:00 (an exact duration)."""
    now = datetime(2026, 10, 28, 12, 0, tzinfo=CHICAGO)
    [p] = planner.plan_event(_allday("dst", date(2026, 11, 2), [2340]), "dst", CFG, now, CHICAGO, set())
    assert p.fire_at.astimezone(CHICAGO).replace(tzinfo=None) == datetime(2026, 10, 31, 9, 0)
    [t] = planner.plan_task(_task("dt", date(2026, 11, 2), alarms=[2340]), CFG, now, CHICAGO)
    assert t.fire_at.astimezone(CHICAGO).replace(tzinfo=None) == datetime(2026, 10, 31, 9, 0)
    # The morning default of a date-only task after the change: 9:00 CST.
    [m] = planner.plan_task(_task("dm", date(2026, 11, 2)), CFG, now, CHICAGO)
    assert m.fire_at.astimezone(CHICAGO).replace(tzinfo=None) == datetime(2026, 11, 2, 9, 0)
    # A timed event: an exact duration. 2 days before 9:00 CST on 2 Nov is 10:00 CDT on 31 Oct.
    start = datetime(2026, 11, 2, 9, 0, tzinfo=CHICAGO)
    [e] = planner.plan_event(_event("te", start, alarms=[2880]), "te", CFG, now, CHICAGO, set())
    assert e.fire_at == start.astimezone(UTC) - timedelta(hours=48)
    assert e.fire_at.astimezone(CHICAGO).replace(tzinfo=None) == datetime(2026, 10, 31, 10, 0)


# ---------------- the schedule and the firing job ----------------


async def _setup(user, *, notify: bool = True, local_only: bool = False):
    async with sessionmaker()() as db:
        c = Connector(user_id=user.id, kind="caldav", name="cal", mode="mirror", enabled=True, status="idle",
                      config={"url": "http://localhost:1/"}, data_class="local_only" if local_only else "normal")
        db.add(c)
        await db.flush()
        coll = CalCollection(user_id=user.id, connector_id=c.id, href=f"http://localhost:1/{uuid.uuid4().hex}/",
                             displayname="Personal", components=["VEVENT", "VTODO"], notify=notify)
        db.add(coll)
        await db.commit()
        return c.id, coll.id


async def _put(user, coll_id: int, ics: str, uid: str) -> int:
    async with sessionmaker()() as db:
        coll = await db.get(CalCollection, coll_id)
        obj = await mirror.upsert_object(db, coll, f"{coll.href}{uid}.ics", f'"{uuid.uuid4().hex}"', ics,
                                         user_tz=ZoneInfo("UTC"), me={user.email.lower()})
        await db.commit()
        return obj.id


async def _rows(user) -> dict[str, ReminderSchedule]:
    async with sessionmaker()() as db:
        return {r.key: r for r in (await db.execute(select(ReminderSchedule).where(
            ReminderSchedule.user_id == user.id))).scalars()}


@pytest.fixture
def captured(monkeypatch):
    """SSE notifications and push deliveries of the firing job."""
    from mailr.core import events, push

    got = {"sse": [], "push": []}
    real = events.notify

    async def notify(db, user_id, event):
        if event.get("type") == "notification":
            got["sse"].append(event)
        await real(db, user_id, event)

    async def deliver(target, title, body, data, priority="default"):
        got["push"].append({"kind": target.kind, "events": list(target.events or []), "title": title, "body": body,
                            "data": data, "priority": priority})
        return True

    monkeypatch.setattr(events, "notify", notify)
    monkeypatch.setattr(push, "deliver", deliver)
    return got


async def _fire(row: ReminderSchedule) -> None:
    await planner.fire({"id": row.id, "fire_at": row.fire_at.isoformat()}, None)


async def test_reminder_fire_sends_sse_and_push_only_to_opted_in_targets(user, captured):
    """reminder.fire: SSE to open tabs; push only to targets that list "reminder" (a UnifiedPush
    phone with an empty event list receives nothing)."""
    _, coll = await _setup(user)
    now = datetime.now(UTC).replace(microsecond=0)
    start = now + timedelta(minutes=10)
    await _put(user, coll, _event("fire-1", start, alarms=[10], extra="LOCATION:Room 4\r\n", summary="Budget review"),
               "fire-1")
    async with sessionmaker()() as db:
        for kind, events in (("unifiedpush", []), ("unifiedpush", ["new_mail"]), ("ntfy", ["reminder"]),
                             ("webpush", ["reminder"])):
            db.add(PushTarget(user_id=user.id, kind=kind, endpoint=f"https://push.invalid/{uuid.uuid4().hex}",
                              events=events, p256dh="k" * 30 if kind == "webpush" else None,
                              auth="a" * 10 if kind == "webpush" else None))
        await db.commit()
    key = f"e|fire-1|{start.isoformat()}|10"
    rows = await _rows(user)
    assert set(rows) == {key} and rows[key].fire_at == start - timedelta(minutes=10)
    await _fire(rows[key])
    [sse] = captured["sse"]
    assert sse["kind"] == "reminder" and sse["key"] == key and sse["title"] == "Budget review"
    assert sse["body"].endswith(" · Room 4") and sse["start"] == start.isoformat() and not sse["missed"]
    assert sse["click"] == f"/calendar/event/fire-1/{start.isoformat()}"
    assert sse["actions"] == ["snooze", "done", "dismiss"]
    assert sorted(p["kind"] for p in captured["push"]) == ["ntfy", "webpush"]
    web = next(p for p in captured["push"] if p["kind"] == "webpush")
    assert web["data"]["tokens"]["snooze"] and web["data"]["key"] == key and web["priority"] == "high"
    assert "tokens" not in next(p for p in captured["push"] if p["kind"] == "ntfy")["data"]
    assert (await _rows(user))[key].fired_at is not None
    # Fired once: the same job again does nothing.
    await _fire(rows[key])
    assert len(captured["sse"]) == 1
    # The tray lists it until it is handled.
    from mailr.core import push

    assert not push.wants(PushTarget(kind="unifiedpush", events=[]), "reminder")
    assert push.wants(PushTarget(kind="unifiedpush", events=[]), "new_mail")


async def test_reminder_tray_lists_fired_until_handled(client, user, captured):
    _, coll = await _setup(user)
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)
    await _put(user, coll, _event("tray-1", start, alarms=[10]), "tray-1")
    [row] = (await _rows(user)).values()
    assert (await client.get("/api/v1/reminders/active")).json()["items"] == []
    await _fire(row)
    items = (await client.get("/api/v1/reminders/active")).json()["items"]
    assert [(i["key"], i["state"], i["kind"], i["missed"]) for i in items] == [(row.key, "fired", "event", False)]
    r = await client.post("/api/v1/reminders/handle", json={"keys": [row.key]})
    assert r.status_code == 200
    assert (await client.get("/api/v1/reminders/active")).json()["items"] == []


async def test_reminder_tray_judges_by_the_pages_clock(client, user, captured):
    """GET /reminders/active?now=: the page's clock decides which snoozes have come due (the docs
    shots pin the page's clock to the demo's day, not the server's)."""
    _, coll = await _setup(user)
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)
    await _put(user, coll, _event("tray-now", start, minutes=120, alarms=[10]), "tray-now")
    [row] = (await _rows(user)).values()
    await _fire(row)
    until = datetime.now(UTC) + timedelta(minutes=30)
    assert (await client.post("/api/v1/reminders/snooze", json={"key": row.key, "until": until.isoformat()})).status_code == 200
    assert (await client.get("/api/v1/reminders/active")).json()["items"] == []
    later = (until + timedelta(minutes=5)).isoformat()
    items = (await client.get("/api/v1/reminders/active", params={"now": later})).json()["items"]
    assert [i["key"] for i in items] == [row.key]


async def test_reminder_dismiss_all_clears_the_tray_in_one_request_and_undo_restores_it(client, user, captured):
    """reminder.dismiss_all: the tray's Clear all sends every key in one /reminders/handle call.
    It is idempotent, a snooze still in the future is neither listed nor cleared, and one undo
    puts every reminder back in the tray."""
    _, coll = await _setup(user)
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)
    for uid in ("all-1", "all-2", "all-3"):
        await _put(user, coll, _event(uid, start, alarms=[10]), uid)
    rows = await _rows(user)
    for r in rows.values():
        await _fire(r)
    later = next(r for r in rows.values() if "all-3" in r.key)
    until = (datetime.now(UTC) + timedelta(hours=1)).replace(microsecond=0)
    assert (await client.post("/api/v1/reminders/snooze", json={"key": later.key, "until": until.isoformat()})).status_code == 200
    tray = [i["key"] for i in (await client.get("/api/v1/reminders/active")).json()["items"]]
    assert sorted(tray) == sorted(k for k in rows if "all-3" not in k)

    r = await client.post("/api/v1/reminders/handle", json={"keys": tray})
    assert r.status_code == 200 and r.json()["count"] == 2
    assert (await client.get("/api/v1/reminders/active")).json()["items"] == []
    # Twice is the same as once (a retry, or the phone clearing the same reminders).
    assert (await client.post("/api/v1/reminders/handle", json={"keys": tray})).json()["count"] == 2
    # The phone reads the same states: handled anywhere is gone everywhere.
    states = {s["key"]: s["state"] for s in (await client.get("/api/v1/reminders/state")).json()["items"]}
    assert states == {**{k: "handled" for k in tray}, later.key: "snoozed"}

    r = await client.post("/api/v1/reminders/handle", json={"keys": tray, "undo": True})
    assert r.status_code == 200 and r.json()["count"] == 2
    back = [i["key"] for i in (await client.get("/api/v1/reminders/active")).json()["items"]]
    assert sorted(back) == sorted(tray)
    states = {s["key"]: s["state"] for s in (await client.get("/api/v1/reminders/state")).json()["items"]}
    assert states[later.key] == "snoozed"


async def test_reminder_dismiss_all_touches_only_the_callers_reminders(client, user, captured):
    """reminder.dismiss_all: a key is read under the caller's account; another user's reminder with
    the same key stays in their tray."""
    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        other = await create_user(db, f"t-{uuid.uuid4().hex[:8]}@example.com", "Other", password="test-password-123")
    _, coll = await _setup(other)
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)
    await _put(other, coll, _event("theirs", start, alarms=[10]), "theirs")
    [row] = (await _rows(other)).values()
    await _fire(row)
    assert (await client.post("/api/v1/reminders/handle", json={"keys": [row.key]})).status_code == 200
    async with sessionmaker()() as db:
        assert [i["key"] for i in await planner.active(db, other.id)] == [row.key]


async def test_handled_reminder_is_not_fired(user, captured):
    _, coll = await _setup(user)
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)
    await _put(user, coll, _event("h-1", start, alarms=[10]), "h-1")
    [row] = (await _rows(user)).values()
    async with sessionmaker()() as db:
        db.add(ReminderState(user_id=user.id, key=row.key, kind="e", uid="h-1", occurrence=row.occurrence, minutes=10,
                             state="handled"))
        await db.commit()
    await _fire(row)
    assert captured["sse"] == [] and captured["push"] == []
    assert (await _rows(user))[row.key].fired_at is not None


async def test_snoozed_reminder_fires_at_its_until(client, user, captured):
    _, coll = await _setup(user)
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)
    await _put(user, coll, _event("s-1", start, alarms=[10]), "s-1")
    [row] = (await _rows(user)).values()
    until = (datetime.now(UTC) + timedelta(minutes=3)).replace(microsecond=0)
    r = await client.post("/api/v1/reminders/snooze", json={"key": row.key, "until": until.isoformat()})
    assert r.status_code == 200, r.text
    snoozed = (await _rows(user))[row.key]
    assert snoozed.fire_at == until and snoozed.source == "snooze" and snoozed.fired_at is None
    await _fire(row)  # the job planned for the old time: moved, so nothing
    assert captured["sse"] == []
    await _fire(snoozed)
    assert [e["key"] for e in captured["sse"]] == [row.key]
    # Re-planning the event (a sync) keeps the snooze time.
    async with sessionmaker()() as db:
        await planner.plan_object(db, await db.get(CalObject, snoozed.object_id))
        await db.commit()
    assert (await _rows(user))[row.key].fire_at == until


async def test_late_reminder_is_missed_and_quiet_and_ended_events_do_not_fire(user, captured):
    _, coll = await _setup(user)
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=5)
    await _put(user, coll, _event("m-1", start, alarms=[10]), "m-1")
    async with sessionmaker()() as db:
        db.add(PushTarget(user_id=user.id, kind="webpush", endpoint=f"https://push.invalid/{uuid.uuid4().hex}",
                          events=["reminder"], p256dh="k" * 30, auth="a" * 10))
        await db.commit()
    [row] = (await _rows(user)).values()
    assert row.fire_at < datetime.now(UTC) - planner.GRACE  # five minutes late
    await _fire(row)
    assert captured["sse"][0]["missed"] is True
    assert captured["push"][0]["priority"] == "low" and captured["push"][0]["title"].startswith("Missed: ")
    # An event that has ended: planned earlier, but not fired.
    ended = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    oid = await _put(user, coll, _event("m-2", datetime.now(UTC) + timedelta(hours=1), alarms=[10]), "m-2")
    async with sessionmaker()() as db:
        db.add(ReminderSchedule(user_id=user.id, key=f"e|m-2|{ended.isoformat()}|10", kind="e", object_id=oid,
                                uid="m-2", occurrence=ended.isoformat(), minutes=10, anchor_at=ended,
                                fire_at=ended - timedelta(minutes=10), title="Old"))
        await db.commit()
    old = (await _rows(user))[f"e|m-2|{ended.isoformat()}|10"]
    await _fire(old)
    assert len(captured["sse"]) == 1


async def test_due_reminders_enqueue_at_the_exact_time_once(user, captured):
    _, coll = await _setup(user)
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=11)
    await _put(user, coll, _event("q-1", start, alarms=[10, 120]), "q-1")
    await planner.due_reminders()
    await planner.due_reminders()  # deduplicated
    async with sessionmaker()() as db:
        jobs = (await db.execute(select(Job).where(Job.kind == "reminders.fire", Job.user_id == user.id))).scalars().all()
    rows = await _rows(user)
    on_time, late = rows[f"e|q-1|{start.isoformat()}|10"], rows[f"e|q-1|{start.isoformat()}|120"]
    # One job per row: the 10-minute one at its exact time, the 120-minute one (its time passed
    # before the event was made; the event is still ahead) at once, to be sent as missed.
    by_id = {j.payload["id"]: j for j in jobs}
    assert len(jobs) == 2 and set(by_id) == {on_time.id, late.id}
    assert by_id[on_time.id].run_at == on_time.fire_at
    assert by_id[late.id].run_at < datetime.now(UTC)


async def test_silent_collection_and_local_only_payload(user, captured):
    _, quiet = await _setup(user, notify=False)
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)
    await _put(user, quiet, _event("n-1", start, alarms=[10]), "n-1")
    assert await _rows(user) == {}
    _, private = await _setup(user, local_only=True)
    await _put(user, private, _event("lo-1", start, alarms=[10], summary="Therapy"), "lo-1")
    async with sessionmaker()() as db:
        db.add(PushTarget(user_id=user.id, kind="ntfy", endpoint=f"https://push.invalid/{uuid.uuid4().hex}",
                          events=["reminder"]))
        await db.commit()
    [row] = (await _rows(user)).values()
    await _fire(row)
    assert captured["sse"][0]["title"] == "Therapy"  # the user's own open tab
    [p] = captured["push"]
    assert p["title"] == "Event reminder" and p["body"] == "" and "Therapy" not in str(p["data"])


async def test_reminder_settings_get_put_and_replan(client, user):
    got = (await client.get("/api/v1/reminders/settings")).json()
    assert got == {"events": True, "tasks": True, "default_timed_event": 10, "default_all_day_event": None,
                   "default_task": True, "morning_hour": 9, "web": {"desktop": True, "push": False},
                   "missed": True, "sound": False, "repeat": {"enabled": False, "every_min": 10, "max": 3}}
    _, coll = await _setup(user)
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=3)
    await _put(user, coll, _event("set-1", start), "set-1")
    assert set(await _rows(user)) == {f"e|set-1|{start.isoformat()}|10"}
    r = await client.put("/api/v1/reminders/settings", json={"default_timed_event": 30, "web": {"push": True}})
    assert r.status_code == 200, r.text
    assert r.json()["default_timed_event"] == 30 and r.json()["web"] == {"desktop": True, "push": True}
    assert set(await _rows(user)) == {f"e|set-1|{start.isoformat()}|30"}
    r = await client.put("/api/v1/reminders/settings", json={"default_timed_event": None})
    assert r.json()["default_timed_event"] is None and await _rows(user) == {}
    assert (await client.put("/api/v1/reminders/settings", json={"morning_hour": 30})).status_code == 422


# ---------------- Remind again (reminder.repeat) ----------------


async def _repeat_on(user, every: int = 5, max_: int = 3) -> None:
    async with sessionmaker()() as db:
        from mailr.models import User

        u = await db.get(User, user.id)
        u.settings = {**(u.settings or {}), "reminders": {"repeat": {"enabled": True, "every_min": every, "max": max_}}}
        await db.commit()


async def _repeat_jobs(user) -> list[Job]:
    async with sessionmaker()() as db:
        return list((await db.execute(select(Job).where(Job.kind == "reminders.repeat", Job.user_id == user.id)
                                      .order_by(Job.id))).scalars())


def _clock(monkeypatch, at: datetime) -> None:
    """Freeze the planner's clock at `at`."""
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return at if tz is None else at.astimezone(tz)

    monkeypatch.setattr(planner, "datetime", Fixed)


async def _run_repeat(job: Job) -> None:
    await planner.repeat(job.payload, None)


async def test_reminder_repeat_alerts_again_up_to_three_times_to_opted_in_targets(user, captured):
    """reminder.repeat: with Remind again on, a fired reminder nobody acted on alerts again every
    few minutes with the same key, at most three times; push goes only to opted-in targets (Web
    Push, ntfy), never to a UnifiedPush phone, which repeats on its own alarm."""
    await _repeat_on(user, every=5)
    _, coll = await _setup(user)
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)
    await _put(user, coll, _event("rep-1", start, alarms=[10]), "rep-1")
    async with sessionmaker()() as db:
        for kind, events in (("unifiedpush", []), ("webpush", ["reminder"])):
            db.add(PushTarget(user_id=user.id, kind=kind, endpoint=f"https://push.invalid/{uuid.uuid4().hex}",
                              events=events, p256dh="k" * 30 if kind == "webpush" else None,
                              auth="a" * 10 if kind == "webpush" else None))
        await db.commit()
    [row] = (await _rows(user)).values()
    await _fire(row)
    [job] = await _repeat_jobs(user)
    fired = (await _rows(user))[row.key].fired_at
    assert job.payload["n"] == 1 and job.run_at == fired + timedelta(minutes=5)
    for n in (1, 2, 3):
        jobs = await _repeat_jobs(user)
        assert len(jobs) == n
        job = jobs[-1]
        job.payload["at"] = datetime.now(UTC).isoformat()  # run it now, not five minutes later
        await _run_repeat(job)
        assert len(captured["sse"]) == n + 1
    sse = captured["sse"]
    assert [e["key"] for e in sse] == [row.key] * 4 and [bool(e.get("again")) for e in sse] == [False, True, True, True]
    assert all(not e["missed"] for e in sse)
    assert [p["kind"] for p in captured["push"]] == ["webpush"] * 4
    assert captured["push"][-1]["data"]["again"] is True and captured["push"][-1]["priority"] == "high"
    # The third repeat was the last: no fourth is queued.
    assert len(await _repeat_jobs(user)) == 3


async def test_reminder_repeat_stops_when_handled_or_snoozed_anywhere(client, user, captured):
    await _repeat_on(user, every=5)
    _, coll = await _setup(user)
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)
    await _put(user, coll, _event("rep-h", start, alarms=[10]), "rep-h")
    await _put(user, coll, _event("rep-s", start, alarms=[10]), "rep-s")
    rows = await _rows(user)
    for r in rows.values():
        await _fire(r)
    jobs = {j.payload["id"]: j for j in await _repeat_jobs(user)}
    handled = next(r for r in rows.values() if "rep-h" in r.key)
    snoozed = next(r for r in rows.values() if "rep-s" in r.key)
    # Handled on another device (the phone): no repeat on the web.
    assert (await client.post("/api/v1/reminders/handle", json={"keys": [handled.key]})).status_code == 200
    until = (datetime.now(UTC) + timedelta(minutes=30)).replace(microsecond=0)
    r = await client.post("/api/v1/reminders/snooze", json={"key": snoozed.key, "until": until.isoformat()})
    assert r.status_code == 200, r.text
    sent = len(captured["sse"])
    for j in jobs.values():
        j.payload["at"] = datetime.now(UTC).isoformat()
        await _run_repeat(j)
    assert len(captured["sse"]) == sent and len(await _repeat_jobs(user)) == 2


async def test_reminder_repeat_events_stop_15_minutes_after_start(user, captured, monkeypatch):
    await _repeat_on(user, every=10)
    _, coll = await _setup(user)
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)
    await _put(user, coll, _event("rep-w", start, alarms=[10]), "rep-w")
    [row] = (await _rows(user)).values()
    await _fire(row)
    [job] = await _repeat_jobs(user)
    # 14 minutes after the start: still repeats.
    t = start + timedelta(minutes=14)
    _clock(monkeypatch, t)
    job.payload["at"] = t.isoformat()
    await _run_repeat(job)
    assert [bool(e.get("again")) for e in captured["sse"]] == [False, True]
    job = (await _repeat_jobs(user))[-1]
    # 16 minutes after the start: the event is under way, no more repeats.
    t = start + timedelta(minutes=16)
    _clock(monkeypatch, t)
    job.payload["at"] = t.isoformat()
    await _run_repeat(job)
    assert len(captured["sse"]) == 2 and len(await _repeat_jobs(user)) == 2


async def test_reminder_repeat_never_for_missed_late_or_when_off(user, captured):
    _, coll = await _setup(user)
    # Off (the default): a fired reminder queues no repeat.
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)
    await _put(user, coll, _event("rep-off", start, alarms=[10]), "rep-off")
    [row] = (await _rows(user)).values()
    await _fire(row)
    assert await _repeat_jobs(user) == []
    # On, but missed (five minutes late): never repeats.
    await _repeat_on(user, every=5)
    soon = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=5)
    await _put(user, coll, _event("rep-m", soon, alarms=[10]), "rep-m")
    missed = next(r for k, r in (await _rows(user)).items() if "rep-m" in k)
    await _fire(missed)
    assert captured["sse"][-1]["missed"] is True and await _repeat_jobs(user) == []
    # On and on time, but the repeat runs late (the server was down): dropped.
    await _put(user, coll, _event("rep-l", start, alarms=[10]), "rep-l")
    late = next(r for k, r in (await _rows(user)).items() if "rep-l" in k)
    await _fire(late)
    [job] = await _repeat_jobs(user)
    sent = len(captured["sse"])
    job.payload["at"] = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    await _run_repeat(job)
    assert len(captured["sse"]) == sent and len(await _repeat_jobs(user)) == 1
    # Turned off after it fired: the queued repeat sends nothing.
    async with sessionmaker()() as db:
        from mailr.models import User

        u = await db.get(User, user.id)
        u.settings = {**(u.settings or {}), "reminders": {"repeat": {"enabled": False}}}
        await db.commit()
    job.payload["at"] = datetime.now(UTC).isoformat()
    await _run_repeat(job)
    assert len(captured["sse"]) == sent


async def test_reminder_repeat_setting_get_put_and_validation(client, user):
    r = await client.put("/api/v1/reminders/settings", json={"repeat": {"enabled": True, "every_min": 15}})
    assert r.status_code == 200, r.text
    assert r.json()["repeat"] == {"enabled": True, "every_min": 15, "max": 3}
    r = await client.put("/api/v1/reminders/settings", json={"repeat": {"enabled": False}})
    assert r.json()["repeat"] == {"enabled": False, "every_min": 15, "max": 3}
    assert (await client.get("/api/v1/reminders/settings")).json()["repeat"]["every_min"] == 15
    assert (await client.put("/api/v1/reminders/settings", json={"repeat": {"every_min": 7}})).status_code == 422
    assert (await client.put("/api/v1/reminders/settings", json={"repeat": {"max": 4}})).status_code == 422
    # A stored value out of range reads back as the default.
    assert planner.repeat_of({"enabled": True, "every_min": 3, "max": 9}) == {"enabled": True, "every_min": 10, "max": 3}
