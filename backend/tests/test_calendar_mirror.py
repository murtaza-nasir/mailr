"""Calendar mirror units (no CalDAV server): iCalendar round trips (Windows TZIDs, floating
times), DST in weekly series, canonical occurrence strings and reminder keys (with the
migration's merge), expansion caps and the materialized window."""

import importlib.util
import pathlib
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar

from mailr.calendar import expand, ical
from mailr.calendar import tz as tzmod

LONDON, NEW_YORK = ZoneInfo("Europe/London"), ZoneInfo("America/New_York")


def _cal(body: str) -> str:
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\n" + body + "END:VCALENDAR\r\n"


# ---------------- canonical occurrence strings and keys ----------------


def test_canonical_occurrence_forms():
    assert ical.canonical_occurrence(datetime(2026, 10, 2, 11, 0, tzinfo=NEW_YORK)) == "2026-10-02T15:00:00+00:00"
    assert ical.canonical_occurrence(datetime(2026, 10, 2, 15, 0, 0, 123, tzinfo=UTC)) == "2026-10-02T15:00:00+00:00"
    assert ical.canonical_occurrence(date(2026, 10, 2)) == "2026-10-02"
    for old in ("20261002T150000Z", "2026-10-02T11:00:00-04:00", "2026-10-02T15:00:00Z", "2026-10-02T15:00:00+00:00",
                "2026-10-02T15:00:00"):
        assert ical.normalize_occurrence(old) == "2026-10-02T15:00:00+00:00", old
    assert ical.normalize_occurrence("20261002") == "2026-10-02"
    assert ical.normalize_occurrence("") == ""
    assert ical.normalize_occurrence("2026-10-02T11:00:00", NEW_YORK) == "2026-10-02T15:00:00+00:00"
    assert ical.normalize_key("e|ev-1|20261002T150000Z|10") == "e|ev-1|2026-10-02T15:00:00+00:00|10"
    assert ical.normalize_key("t|task-1|2026-10-02|0") == "t|task-1|2026-10-02|0"


def _migration():
    path = next(pathlib.Path(__file__).parents[1].glob("migrations/versions/*_calendar_mirror.py"))
    spec = importlib.util.spec_from_file_location("calendar_mirror_migration", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_key_migration_merges_collisions(user):
    """Old key forms are rewritten; rows that collide merge (handled > snoozed > pending, then newest)."""
    from sqlalchemy import select, text

    from mailr.db import engine, sessionmaker
    from mailr.models import ReminderState

    mig = _migration()
    now = datetime.now(UTC)
    rows = [  # (key, occurrence, state, age in minutes)
        ("e|m1|20261002T150000Z|10", "20261002T150000Z", "pending", 1),
        ("e|m1|2026-10-02T11:00:00-04:00|10", "2026-10-02T11:00:00-04:00", "handled", 30),
        ("e|m1|2026-10-02T15:00:00+00:00|10", "2026-10-02T15:00:00+00:00", "snoozed", 0),
        ("e|m2|20261003T090000Z|5", "20261003T090000Z", "snoozed", 10),
        ("e|m2|2026-10-03T09:00:00Z|5", "2026-10-03T09:00:00Z", "snoozed", 2),
        ("t|m3|2026-10-04|0", "2026-10-04", "pending", 0),
    ]
    async with sessionmaker()() as db:
        for key, occ, st, age in rows:
            db.add(ReminderState(user_id=user.id, key=key, kind=key[0], uid=key.split("|")[1], occurrence=occ,
                                 minutes=int(key.rsplit("|", 1)[1]), state=st, device=f"age{age}",
                                 updated_at=now - timedelta(minutes=age)))
        await db.commit()
    async with engine().begin() as conn:
        await conn.run_sync(lambda c: mig.normalise_reminder_keys(c))
    async with sessionmaker()() as db:
        got = {r.key: (r.state, r.device, r.occurrence, r.anchor_at) for r in (await db.execute(
            select(ReminderState).where(ReminderState.user_id == user.id))).scalars()}
        await db.execute(text("DELETE FROM reminder_states WHERE user_id=:u"), {"u": user.id})
        await db.commit()
    assert set(got) == {"e|m1|2026-10-02T15:00:00+00:00|10", "e|m2|2026-10-03T09:00:00+00:00|5", "t|m3|2026-10-04|0"}
    assert got["e|m1|2026-10-02T15:00:00+00:00|10"][:2] == ("handled", "age30")
    assert got["e|m2|2026-10-03T09:00:00+00:00|5"][:2] == ("snoozed", "age2")
    assert got["e|m1|2026-10-02T15:00:00+00:00|10"][3] == datetime(2026, 10, 2, 15, tzinfo=UTC)


# ---------------- time zones ----------------


def test_windows_tzid_and_floating_times():
    ics = _cal("BEGIN:VEVENT\r\nUID:w\r\nDTSTAMP:20260101T000000Z\r\n"
               "DTSTART;TZID=Eastern Standard Time:20261102T090000\r\n"
               "DTEND;TZID=Eastern Standard Time:20261102T100000\r\nSUMMARY:Outlook\r\nEND:VEVENT\r\n")
    p = ical.parse_resource(ics, LONDON)
    assert p.fields["start_at"] == datetime(2026, 11, 2, 14, 0, tzinfo=UTC)  # EST after the switch
    assert p.fields["tzid"] == "America/New_York" and not p.fields["floating"]

    floating = _cal("BEGIN:VEVENT\r\nUID:f\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:20260715T090000\r\n"
                    "DTEND:20260715T100000\r\nSUMMARY:Floating\r\nEND:VEVENT\r\n")
    p = ical.parse_resource(floating, LONDON)
    assert p.fields["floating"] and p.fields["tzid"] is None
    assert p.fields["start_at"] == datetime(2026, 7, 15, 8, 0, tzinfo=UTC)  # 09:00 in London (BST)
    p = ical.parse_resource(floating, NEW_YORK)
    assert p.fields["start_at"] == datetime(2026, 7, 15, 13, 0, tzinfo=UTC)

    unknown = _cal("BEGIN:VEVENT\r\nUID:u\r\nDTSTAMP:20260101T000000Z\r\nDTSTART;TZID=Nowhere/Special:20260715T090000\r\n"
                   "SUMMARY:x\r\nEND:VEVENT\r\n")
    assert ical.parse_resource(unknown, NEW_YORK).fields["start_at"] == datetime(2026, 7, 15, 13, 0, tzinfo=UTC)

    embedded = _cal("BEGIN:VTIMEZONE\r\nTZID:Custom Zone\r\nBEGIN:STANDARD\r\nDTSTART:19700101T000000\r\n"
                    "TZOFFSETFROM:+0300\r\nTZOFFSETTO:+0300\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\n"
                    "BEGIN:VEVENT\r\nUID:e\r\nDTSTAMP:20260101T000000Z\r\nDTSTART;TZID=Custom Zone:20260715T090000\r\n"
                    "SUMMARY:x\r\nEND:VEVENT\r\n")
    assert ical.parse_resource(embedded, NEW_YORK).fields["start_at"] == datetime(2026, 7, 15, 6, 0, tzinfo=UTC)


def test_written_events_carry_tzid_and_vtimezone_and_round_trip():
    cal = ical.build_event({"summary": "Standup", "start": "2026-10-19T09:00:00+01:00",
                            "end": "2026-10-19T09:15:00+01:00", "recurrence": "FREQ=WEEKLY;COUNT=3"}, LONDON)
    text = ical.serialize(cal)
    assert "DTSTART;TZID=Europe/London:20261019T090000" in text and "BEGIN:VTIMEZONE" in text
    p = ical.parse_resource(text, UTC)
    assert p.fields["tzid"] == "Europe/London" and p.fields["start_at"] == datetime(2026, 10, 19, 8, tzinfo=UTC)
    # An explicit zone wins; a naive time is read in the user's zone; UTC users get UTC values.
    cal = ical.build_event({"summary": "x", "start": "2026-10-19T09:00:00", "tz": "America/New_York"}, LONDON)
    assert "DTSTART;TZID=America/New_York:20261019T040000" in ical.serialize(cal)
    cal = ical.build_event({"summary": "x", "start": "2026-10-19T09:00:00"}, UTC)
    assert "DTSTART:20261019T090000Z" in ical.serialize(cal)
    cal = ical.build_event({"summary": "x", "start": "2026-10-19", "end": "2026-10-20"}, LONDON)
    assert "DTSTART;VALUE=DATE:20261019" in ical.serialize(cal)
    with pytest.raises(ValueError):
        ical.build_event({"summary": "x", "start": "2026-10-19T09:00:00Z", "tz": "Mars/Olympus"}, UTC)


def test_editing_keeps_the_events_zone():
    cal = ical.build_event({"summary": "Call", "start": "2026-11-02T09:00:00-05:00", "tz": "America/New_York"}, UTC)
    ev = next(iter(cal.walk("VEVENT")))
    ical.apply_changes(ev, {"start": "2026-11-03T15:00:00Z"}, LONDON)
    text = ical.serialize(cal)
    assert "DTSTART;TZID=America/New_York:20261103T100000" in text
    assert "DTEND;TZID=America/New_York:20261103T110000" in text  # the duration is kept


def _weekly(start_local: str, zone: str) -> Calendar:
    return Calendar.from_ical(_cal(
        f"BEGIN:VEVENT\r\nUID:wk\r\nDTSTAMP:20260101T000000Z\r\nDTSTART;TZID={zone}:{start_local}\r\n"
        "DURATION:PT1H\r\nRRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:Weekly\r\nEND:VEVENT\r\n"))


def test_weekly_nine_am_london_across_the_october_switch():
    """09:00 stays 09:00 local when the clocks go back on 25 Oct 2026."""
    insts, _ = expand.instances(_weekly("20261012T090000", "Europe/London"), datetime(2026, 10, 1, tzinfo=UTC),
                                datetime(2026, 12, 1, tzinfo=UTC), UTC)
    assert [i.start_at.astimezone(LONDON).hour for i in insts] == [9, 9, 9, 9]
    assert [i.occurrence for i in insts] == ["2026-10-12T08:00:00+00:00", "2026-10-19T08:00:00+00:00",
                                             "2026-10-26T09:00:00+00:00", "2026-11-02T09:00:00+00:00"]


def test_weekly_nine_am_new_york_across_the_november_switch():
    insts, _ = expand.instances(_weekly("20261019T090000", "America/New_York"), datetime(2026, 10, 1, tzinfo=UTC),
                                datetime(2026, 12, 1, tzinfo=UTC), UTC)
    assert [i.start_at.astimezone(NEW_YORK).hour for i in insts] == [9, 9, 9, 9]
    assert [i.start_at.hour for i in insts] == [13, 13, 14, 14]


def test_floating_series_expands_in_the_users_zone():
    cal = Calendar.from_ical(_cal("BEGIN:VEVENT\r\nUID:fl\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:20261019T090000\r\n"
                                  "DURATION:PT1H\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\nSUMMARY:F\r\nEND:VEVENT\r\n"))
    insts, _ = expand.instances(cal, datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC), LONDON)
    assert [i.start_at.astimezone(LONDON).hour for i in insts] == [9, 9, 9]


def test_overrides_and_exdates():
    cal = Calendar.from_ical(_cal(
        "BEGIN:VEVENT\r\nUID:o\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:20261005T090000Z\r\nDURATION:PT30M\r\n"
        "RRULE:FREQ=DAILY;COUNT=4\r\nEXDATE:20261007T090000Z\r\nSUMMARY:Daily\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:o\r\nDTSTAMP:20260101T000000Z\r\nRECURRENCE-ID:20261006T090000Z\r\n"
        "DTSTART:20261006T140000Z\r\nDTEND:20261006T143000Z\r\nSUMMARY:Moved\r\nEND:VEVENT\r\n"))
    insts, _ = expand.instances(cal, datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC), UTC)
    assert [(i.occurrence, i.start_at.hour, i.is_override) for i in insts] == [
        ("2026-10-05T09:00:00+00:00", 9, False), ("2026-10-06T09:00:00+00:00", 14, True),
        ("2026-10-08T09:00:00+00:00", 9, False)]


def test_all_day_instances():
    cal = Calendar.from_ical(_cal("BEGIN:VEVENT\r\nUID:ad\r\nDTSTAMP:20260101T000000Z\r\nDTSTART;VALUE=DATE:20261005\r\n"
                                  "DTEND;VALUE=DATE:20261006\r\nRRULE:FREQ=WEEKLY;COUNT=2\r\nSUMMARY:AD\r\nEND:VEVENT\r\n"))
    insts, _ = expand.instances(cal, datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC), NEW_YORK)
    assert [i.occurrence for i in insts] == ["2026-10-05", "2026-10-12"]
    assert insts[0].all_day and insts[0].start_at == datetime(2026, 10, 5, 4, tzinfo=UTC)  # midnight in New York


# ---------------- caps and the window ----------------


def test_expansion_is_capped_and_bad_rules_are_refused_on_write():
    cal = Calendar.from_ical(_cal("BEGIN:VEVENT\r\nUID:m\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:20261001T000000Z\r\n"
                                  "RRULE:FREQ=MINUTELY\r\nSUMMARY:Spam\r\nEND:VEVENT\r\n"))
    insts, truncated = expand.instances(cal, datetime(2026, 10, 1, tzinfo=UTC), datetime(2027, 10, 1, tzinfo=UTC), UTC)
    assert len(insts) == expand.CAP and truncated
    for rule in ("FREQ=MINUTELY", "FREQ=SECONDLY;COUNT=5", "FREQ=HOURLY", "FREQ=SOMETIMES", "COUNT=3"):
        with pytest.raises(ValueError):
            ical.check_rrule(rule)
    ical.check_rrule("FREQ=HOURLY;COUNT=8")
    ical.check_rrule("RRULE:FREQ=WEEKLY;BYDAY=MO,WE")


def test_window_bounds():
    lo, hi = expand.window(datetime(2026, 10, 1, tzinfo=UTC))
    assert lo == datetime(2025, 10, 1, tzinfo=UTC) and hi == datetime(2028, 9, 30, tzinfo=UTC)


async def test_ranges_outside_the_window_expand_on_read(client, user):
    """A daily series has rows inside the window only; an agenda three years out still lists it."""
    from helpers.calendar_sync import _connect
    from sqlalchemy import func, select

    from mailr.db import sessionmaker
    from mailr.models import CalObject, CalOccurrence

    await _connect(client)
    start = datetime.now(UTC).replace(hour=7, minute=0, second=0, microsecond=0) + timedelta(days=1)
    r = await client.post("/api/v1/calendar/events", json={"summary": "Daily", "start": start.isoformat(),
                                                           "recurrence": "FREQ=DAILY"})
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        obj = (await db.execute(select(CalObject).where(CalObject.uid == r.json()["uid"]))).scalar_one()
        n = (await db.execute(select(func.count()).select_from(CalOccurrence)
                              .where(CalOccurrence.object_id == obj.id))).scalar_one()
        assert 700 <= n <= 732 and obj.range_end is None and obj.expanded_until
    far = start + timedelta(days=365 * 3)
    ag = (await client.get("/api/v1/calendar/agenda", params={"from": far.date().isoformat(),
                                                              "to": (far + timedelta(days=3)).date().isoformat()})).json()
    assert [e["summary"] for e in ag["events"]] == ["Daily"] * 3
    assert len({e["occurrence"] for e in ag["events"]}) == 3


def test_zone_helpers():
    assert tzmod.zone("Eastern Standard Time") == NEW_YORK
    assert tzmod.zone("/mozilla.org/20050126_1/Europe/London") == LONDON
    assert tzmod.zone("nope") is None
    assert tzmod.zone_name(UTC) == "UTC" and tzmod.zone_name(LONDON) == "Europe/London"


async def test_master_counts_its_changed_occurrences(client, user):
    """GET /calendar/events/{uid} carries override_count: the changed occurrences of a series
    (the series question's "All events": "2 changed occurrences will keep their own time")."""
    from helpers.calendar_sync import _connect

    await _connect(client)
    start = datetime.now(UTC).replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
    r = await client.post("/api/v1/calendar/events", json={"summary": "Standup", "start": start.isoformat(),
                                                           "end": (start + timedelta(minutes=15)).isoformat(),
                                                           "recurrence": "FREQ=DAILY;COUNT=10"})
    assert r.status_code == 200, r.text
    uid = r.json()["uid"]
    assert (await client.get(f"/api/v1/calendar/events/{uid}")).json()["override_count"] == 0
    for day in (2, 4):
        occ = ical.canonical_occurrence(start + timedelta(days=day))
        moved = start + timedelta(days=day, hours=1)
        r = await client.patch(f"/api/v1/calendar/events/{uid}", json={
            "start": moved.isoformat(), "end": (moved + timedelta(minutes=15)).isoformat(), "scope": "this", "occurrence": occ})
        assert r.status_code == 200, r.text
    assert (await client.get(f"/api/v1/calendar/events/{uid}")).json()["override_count"] == 2
