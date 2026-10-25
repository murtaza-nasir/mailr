# ACT-SURFACES: api
"""Zone names are IANA keys only (9 Oct 2026). Outlook writes Windows names ("Central Standard
Time"); an event added from an Outlook invite was stored and served with that name, and the
browser's date formatter threw. Covered here: parsing (Exchange, Google, Apple, unknown), the
writer's output (IANA TZID + VTIMEZONE, UID and SEQUENCE kept), iTIP summaries, the API contract
(a sweep of the routes over an old record stored with a raw name), the migration and the repair
command `mailr calendar-fix-zones`."""

import importlib.util
import pathlib
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar
from sqlalchemy import select, text

from mailr.calendar import ical, itip
from mailr.calendar import tz as tzmod

WINDOWS = {
    "Central Standard Time": ("America/Chicago", "-0600", "-0500"),
    "Eastern Standard Time": ("America/New_York", "-0500", "-0400"),
    "Pacific Standard Time": ("America/Los_Angeles", "-0800", "-0700"),
    "W. Europe Standard Time": ("Europe/Berlin", "+0100", "+0200"),
    "India Standard Time": ("Asia/Calcutta", "+0530", "+0530"),
}
ME = "dana@northwind.example"
ZONE_KEYS = {"tz", "end_tz", "timezone", "organizer_tz", "event_tz", "zone", "calendar_tz", "tzid"}


def _outlook(name: str, uid: str = "040000008200E00074C5B7101A82E008", start: str = "20261015T100000") -> str:
    _, std, dst = WINDOWS[name]
    daylight = (f"BEGIN:DAYLIGHT\r\nDTSTART:16010101T020000\r\nTZOFFSETFROM:{std}\r\nTZOFFSETTO:{dst}\r\n"
                "RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=2SU;BYMONTH=3\r\nEND:DAYLIGHT\r\n") if std != dst else ""
    return ("BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nPRODID:Microsoft Exchange Server 2010\r\nVERSION:2.0\r\n"
            f"BEGIN:VTIMEZONE\r\nTZID:{name}\r\nBEGIN:STANDARD\r\nDTSTART:16010101T020000\r\n"
            f"TZOFFSETFROM:{dst}\r\nTZOFFSETTO:{std}\r\nRRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=1SU;BYMONTH=11\r\n"
            f"END:STANDARD\r\n{daylight}END:VTIMEZONE\r\n"
            f"BEGIN:VEVENT\r\nORGANIZER;CN=Lee Park:mailto:lee@contoso.example\r\n"
            f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{ME}\r\n"
            f"SUMMARY;LANGUAGE=en-US:Budget review\r\nDTSTART;TZID={name}:{start}\r\n"
            f"DTEND;TZID={name}:{start[:9]}113000\r\nUID:{uid}\r\nSEQUENCE:3\r\n"
            "DTSTAMP:20261009T150000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


GOOGLE = ("BEGIN:VCALENDAR\r\nPRODID:-//Google Inc//Google Calendar 70.9054//EN\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\n"
          "BEGIN:VTIMEZONE\r\nTZID:America/Chicago\r\nX-LIC-LOCATION:America/Chicago\r\nBEGIN:DAYLIGHT\r\n"
          "TZOFFSETFROM:-0600\r\nTZOFFSETTO:-0500\r\nTZNAME:CDT\r\nDTSTART:19700308T020000\r\n"
          "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\r\nEND:DAYLIGHT\r\nBEGIN:STANDARD\r\nTZOFFSETFROM:-0500\r\n"
          "TZOFFSETTO:-0600\r\nTZNAME:CST\r\nDTSTART:19701101T020000\r\nRRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\r\n"
          "END:STANDARD\r\nEND:VTIMEZONE\r\nBEGIN:VEVENT\r\nDTSTART;TZID=America/Chicago:20261015T100000\r\n"
          "DTEND;TZID=America/Chicago:20261015T110000\r\nDTSTAMP:20261009T150000Z\r\n"
          "ORGANIZER;CN=Lee Park:mailto:lee@contoso.example\r\nUID:g1@google.com\r\n"
          f"ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{ME}\r\nSEQUENCE:0\r\nSUMMARY:Sync\r\n"
          "END:VEVENT\r\nEND:VCALENDAR\r\n")
APPLE = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Apple Inc.//macOS 15.0//EN\r\nMETHOD:REQUEST\r\n"
         "BEGIN:VEVENT\r\nUID:A1B2C3D4-apple\r\nDTSTAMP:20261009T150000Z\r\n"
         "DTSTART;TZID=America/Los_Angeles:20261015T080000\r\nDTEND;TZID=America/Los_Angeles:20261015T090000\r\n"
         "ORGANIZER;CN=Lee Park:mailto:lee@contoso.example\r\n"
         f"ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:{ME}\r\nSUMMARY:Coffee\r\nSEQUENCE:1\r\n"
         "END:VEVENT\r\nEND:VCALENDAR\r\n")
UNKNOWN = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\nBEGIN:VTIMEZONE\r\nTZID:Customized Time Zone\r\n"
           "BEGIN:STANDARD\r\nDTSTART:16010101T000000\r\nTZOFFSETFROM:+0300\r\nTZOFFSETTO:+0300\r\nEND:STANDARD\r\n"
           "END:VTIMEZONE\r\nBEGIN:VEVENT\r\nUID:unk-1\r\nDTSTAMP:20261009T150000Z\r\n"
           "DTSTART;TZID=Customized Time Zone:20261015T090000\r\nSUMMARY:Odd\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


def _valid_or_none(name) -> bool:
    if name is None:
        return True
    try:
        ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return False
    return tzmod.iana(name) == name


def zone_fields(obj, path="$"):
    """(path, value) of every zone-named field in a JSON document."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ZONE_KEYS and (v is None or isinstance(v, str)):
                yield f"{path}.{k}", v
            yield from zone_fields(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from zone_fields(v, f"{path}[{i}]")


def assert_iana(doc) -> int:
    bad = [(p, v) for p, v in zone_fields(doc) if not _valid_or_none(v)]
    assert not bad, bad
    return sum(1 for _ in zone_fields(doc))


# ---------------- helpers ----------------


def test_iana_helper():
    assert tzmod.iana("Central Standard Time") == "America/Chicago"
    assert tzmod.iana("America/Chicago") == "America/Chicago"
    assert tzmod.iana("/mozilla.org/20050126_1/Europe/London") == "Europe/London"
    assert tzmod.iana("Etc/UTC") == tzmod.iana("Z") == "UTC"
    assert tzmod.iana("Customized Time Zone") is None and tzmod.iana("") is None and tzmod.iana(None) is None
    assert tzmod.iana("UTC-05:00") is None
    assert tzmod.is_iana("Europe/Berlin") and not tzmod.is_iana("W. Europe Standard Time")


# ---------------- parsing ----------------


@pytest.mark.parametrize("name", list(WINDOWS))
def test_exchange_invites_store_the_iana_name(name):
    key = WINDOWS[name][0]
    p = ical.parse_resource(_outlook(name), ZoneInfo("Europe/London"))
    assert p.fields["tzid"] == key and not p.fields["floating"]
    local = datetime(2026, 10, 15, 10, 0, tzinfo=ZoneInfo(key))
    assert p.fields["start_at"] == local.astimezone(UTC)
    s = itip.summarize(_outlook(name).encode(), {ME})
    assert s["tz"] == key


def test_google_and_apple_invites_keep_their_names():
    assert ical.parse_resource(GOOGLE, UTC).fields["tzid"] == "America/Chicago"
    assert ical.parse_resource(APPLE, UTC).fields["tzid"] == "America/Los_Angeles"
    assert itip.summarize(GOOGLE.encode(), {ME})["tz"] == "America/Chicago"
    assert itip.summarize(APPLE.encode(), {ME})["tz"] == "America/Los_Angeles"


def test_unknown_zone_is_null_and_keeps_the_instant():
    p = ical.parse_resource(UNKNOWN, ZoneInfo("America/New_York"))
    assert p.fields["tzid"] is None and not p.fields["floating"]
    assert p.fields["start_at"] == datetime(2026, 10, 15, 6, 0, tzinfo=UTC)  # 09:00 at +03:00
    assert itip.summarize(UNKNOWN.encode(), set()) is None or itip.summarize(UNKNOWN.encode(), set())["tz"] is None


# ---------------- writing ----------------


@pytest.mark.parametrize("name", list(WINDOWS))
def test_written_copy_has_the_iana_tzid_and_vtimezone(name):
    key = WINDOWS[name][0]
    cal = Calendar.from_ical(_outlook(name))
    out = ical.serialize(cal)
    flat = out.replace("\r\n ", "")
    assert name not in flat
    assert f"DTSTART;TZID={key}:20261015T100000" in flat and f"DTEND;TZID={key}:20261015T113000" in flat
    assert f"TZID:{key}" in flat and flat.count("BEGIN:VTIMEZONE") == 1
    assert "UID:040000008200E00074C5B7101A82E008" in flat and "SEQUENCE:3" in flat
    assert "Lee Park" in flat and ":mailto:lee@contoso.example" in flat
    again = ical.parse_resource(out, UTC).fields
    assert again["tzid"] == key
    assert again["start_at"] == ical.parse_resource(_outlook(name), UTC).fields["start_at"]


def test_writing_leaves_iana_and_unknown_zones_alone():
    flat = ical.serialize(Calendar.from_ical(GOOGLE)).replace("\r\n ", "")
    assert "DTSTART;TZID=America/Chicago:20261015T100000" in flat
    flat = ical.serialize(Calendar.from_ical(UNKNOWN)).replace("\r\n ", "")
    assert "DTSTART;TZID=Customized Time Zone:20261015T090000" in flat and "TZID:Customized Time Zone" in flat


def test_exdates_and_overrides_are_renamed_too():
    ics = _outlook("Eastern Standard Time").replace(
        "SEQUENCE:3\r\n", "SEQUENCE:3\r\nRRULE:FREQ=WEEKLY;COUNT=4\r\n"
                          "EXDATE;TZID=Eastern Standard Time:20261022T100000\r\n")
    flat = ical.serialize(Calendar.from_ical(ics)).replace("\r\n ", "")
    assert "EXDATE;TZID=America/New_York:20261022T100000" in flat and "Eastern Standard Time" not in flat


# ---------------- the API contract, an old record, the repair ----------------


def _migration():
    path = pathlib.Path(__file__).parents[1] / "migrations/versions/e6b8d4f2a9c1_iana_zones_only.py"
    spec = importlib.util.spec_from_file_location("iana_zones_only", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_mapping_matches_the_helper():
    m = _migration()
    for name in [*WINDOWS, "America/Chicago", "UTC", "Etc/UTC", "Customized Time Zone",
                 "/mozilla.org/20050126_1/Europe/London", "UTC-05:00"]:
        assert m._iana(name) == tzmod.iana(name), name


async def _seed_old_record(client, user):
    """An event added from an Outlook invite before the fix: the server copy and the mirror carry
    the raw Windows name (the mirror columns are set back by hand, as the old code stored them)."""
    from helpers.calendar_sync import _connect

    from mailr.db import sessionmaker
    from mailr.models import CalCollection, CalObject

    _, cals, _ = await _connect(client)
    start = (datetime.now(UTC) + timedelta(days=3)).strftime("%Y%m%dT100000")
    uid = f"outlook-{uuid.uuid4().hex[:8]}"
    cals["Personal"].save_event(_outlook("Central Standard Time", uid=uid, start=start).replace("METHOD:REQUEST\r\n", ""))
    day = datetime.strptime(start, "%Y%m%dT%H%M%S").date()
    r = await client.get("/api/v1/calendar/agenda", params={"from": day.isoformat(),
                                                           "to": (day + timedelta(days=1)).isoformat()})
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        obj = (await db.execute(select(CalObject).where(CalObject.uid == uid))).scalar_one()
        assert obj.tzid == "America/Chicago"  # what the mirror stores now
        await db.execute(text("UPDATE cal_objects SET tzid='Central Standard Time', end_tzid='Central Standard Time' "
                              "WHERE id=:i"), {"i": obj.id})
        await db.execute(text("UPDATE cal_collections SET calendar_tz='Central Standard Time' WHERE id=:c"),
                         {"c": obj.collection_id})
        await db.execute(text("UPDATE users SET settings = jsonb_set(coalesce(settings, '{}'::jsonb), '{timezone}', "
                              "'\"Central Standard Time\"') WHERE id=:u"), {"u": user.id})
        await db.commit()
        coll = await db.get(CalCollection, obj.collection_id)
        assert coll is not None
    return uid, day, cals


async def test_routes_send_only_iana_names_for_an_old_record(client, user):
    from dev_services import radicale_up as _radicale_up

    if not _radicale_up():
        pytest.skip("Radicale dev server not running on :15232")
    uid, day, _ = await _seed_old_record(client, user)
    frm, to = (day - timedelta(days=1)).isoformat(), (day + timedelta(days=2)).isoformat()
    t = await client.post("/api/v1/tasks", json={"summary": "Follow up", "due": f"{day.isoformat()}T15:00:00",
                                                 "tz": "Central Standard Time"})
    assert t.status_code == 200, t.text
    checked = 0
    for url, params in [("/api/v1/calendar/agenda", {"from": frm, "to": to}),
                        (f"/api/v1/calendar/events/{uid}", {}),
                        ("/api/v1/calendar/events/search", {"q": "Budget"}),
                        ("/api/v1/calendar/search", {"q": "Budget"}),
                        ("/api/v1/calendar/delta", {"cursor": 0}),
                        ("/api/v1/calendar/calendars", {}),
                        ("/api/v1/tasks", {}),
                        (f"/api/v1/tasks/{t.json()['uid']}", {}),
                        ("/api/v1/today", {"date": day.isoformat()}),
                        ("/api/v1/me", {})]:
        r = await client.get(url, params=params)
        assert r.status_code == 200, (url, r.text)
        checked += assert_iana(r.json())
    assert checked >= 6
    ev = (await client.get(f"/api/v1/calendar/events/{uid}")).json()
    ev = ev.get("event", ev)
    assert ev["tz"] == "America/Chicago" and ev.get("end_tz") in (None, "America/Chicago")


async def test_migration_maps_stored_names(client, user):
    from dev_services import radicale_up as _radicale_up

    if not _radicale_up():
        pytest.skip("Radicale dev server not running on :15232")
    from mailr.db import sessionmaker
    from mailr.models import CalObject, User

    uid, _, _ = await _seed_old_record(client, user)
    m = _migration()
    async with sessionmaker()() as db:
        for _ in (0, 1):  # safe to run again
            await db.run_sync(lambda s: _run_upgrade(m, s.connection()))
        await db.commit()
    async with sessionmaker()() as db:
        obj = (await db.execute(select(CalObject).where(CalObject.uid == uid))).scalar_one()
        assert (obj.tzid, obj.end_tzid) == ("America/Chicago", "America/Chicago")
        u = await db.get(User, user.id)
        assert u.settings["timezone"] == "America/Chicago"
        assert (await db.execute(text("SELECT count(*) FROM cal_collections WHERE calendar_tz='Central Standard Time'"))
                ).scalar_one() == 0


def _run_upgrade(m, conn):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with Operations.context(MigrationContext.configure(conn)):
        m.upgrade()


async def test_fix_zones_rewrites_the_server_copy(client, user):
    from dev_services import radicale_up as _radicale_up

    if not _radicale_up():
        pytest.skip("Radicale dev server not running on :15232")
    from mailr.calendar import fix_zones
    from mailr.db import sessionmaker
    from mailr.models import CalObject

    uid, _, cals = await _seed_old_record(client, user)
    async with sessionmaker()() as db:
        st = await fix_zones.fix_user(db, user.id, dry_run=True)
    assert st["affected"] == 1 and st["fixed"] == 0
    assert "Central Standard Time" in cals["Personal"].event_by_uid(uid).data
    seq = [x for x in cals["Personal"].event_by_uid(uid).data.splitlines() if x.startswith("SEQUENCE:")]
    async with sessionmaker()() as db:
        st = await fix_zones.fix_user(db, user.id)
    assert st["fixed"] == 1 and st["failed"] == 0
    server = cals["Personal"].event_by_uid(uid).data.replace("\r\n ", "").replace("\n ", "")
    assert "Central Standard Time" not in server and "TZID=America/Chicago" in server
    assert f"UID:{uid}" in server, server
    assert seq and seq[0] in server.splitlines(), server  # unchanged (the caldav library set it on save)
    async with sessionmaker()() as db:
        obj = (await db.execute(select(CalObject).where(CalObject.uid == uid))).scalar_one()
        assert obj.tzid == "America/Chicago" and "Central Standard Time" not in obj.ics
        st = await fix_zones.fix_user(db, user.id)
    assert st["affected"] == 0  # nothing left to do
    assert "would fix" in fix_zones.line("x@example.com", st, True)
