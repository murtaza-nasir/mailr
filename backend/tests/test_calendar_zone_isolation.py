# ACT-SURFACES: api
"""Calendar zones.

- An unknown TZID's VTIMEZONE defines that zone for its own calendar's
  parse only. Two messages naming the same unknown TZID with different definitions each get their
  own; a message without a VTIMEZONE never gets another message's; parallel parses never share.
- The zone shapes real clients write are used as written (Lotus Notes and Domino
  `BYMINUTE=0;BYHOUR=2`, the Windows and Exchange `BYSETPOS` form), still within the bounds.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from helpers.work import rrule_periods
from icalendar import Calendar

import mailr.calendar  # noqa: F401  installs the rules
from mailr.calendar import zones


def _ics(tzid: str, off: str | None, uid: str, when: str = "20261102T100000") -> str:
    z = (f"BEGIN:VTIMEZONE\r\nTZID:{tzid}\r\nBEGIN:STANDARD\r\nDTSTART:19700101T000000\r\n"
         f"TZOFFSETFROM:{off}\r\nTZOFFSETTO:{off}\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\n") if off else ""
    return (f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\n{z}BEGIN:VEVENT\r\nUID:{uid}\r\n"
            f"DTSTAMP:20261001T000000Z\r\nDTSTART;TZID={tzid}:{when}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


def _offset(text: str) -> timedelta | None:
    dt = next(iter(Calendar.from_ical(text).walk("VEVENT")))["dtstart"].dt
    return dt.utcoffset() if dt.tzinfo else None


def test_same_unknown_tzid_two_definitions_each_its_own():
    tzid = f"Customized Time Zone {uuid.uuid4().hex[:6]}"
    a = _ics(tzid, "+0500", "a")
    b = _ics(tzid, "-0300", "b")
    assert _offset(a) == timedelta(hours=5)
    assert _offset(b) == timedelta(hours=-3)
    assert _offset(a) == timedelta(hours=5)  # b never redefined it for a later parse of a


def test_a_message_without_the_vtimezone_never_gets_anothers():
    tzid = f"Org Standard Time {uuid.uuid4().hex[:6]}"
    assert _offset(_ics(tzid, "+1400", "a")) == timedelta(hours=14)
    assert _offset(_ics(tzid, None, "b")) is None  # no definition: floating, not the first sender's


def test_nothing_stays_in_icalendars_process_cache():
    from icalendar.timezone import tzp

    tzid = f"Mallory Time {uuid.uuid4().hex[:6]}"
    Calendar.from_ical(_ics(tzid, "+1400", "a"))
    cache = getattr(tzp, "_TZP__tz_cache", {})
    assert tzid not in cache and tzp.timezone(tzid) is None


def test_parallel_parses_do_not_share():
    tzid = f"Race Zone {uuid.uuid4().hex[:6]}"
    texts = [(_ics(tzid, f"+{h:02d}00", f"u{h}"), timedelta(hours=h)) for h in range(1, 13)] * 8

    def one(item):
        text, want = item
        return _offset(text) == want

    with ThreadPoolExecutor(8) as ex:
        assert all(ex.map(one, texts))


def test_known_names_still_ignore_the_sender():
    assert _offset(_ics("Central Standard Time", "+1400", "x")) == timedelta(hours=-6)


# ---------------------------------------------------------------- Real zone shapes

# Lotus Notes / Domino 9 (a real invitation's VTIMEZONE, TZID as Notes writes it).
NOTES = """BEGIN:VTIMEZONE
TZID:Eastern{u}
BEGIN:STANDARD
DTSTART:19501105T020000
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
RRULE:FREQ=YEARLY;BYMINUTE=0;BYHOUR=2;BYDAY=1SU;BYMONTH=11
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19500312T020000
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
RRULE:FREQ=YEARLY;BYMINUTE=0;BYHOUR=2;BYDAY=2SU;BYMONTH=3
END:DAYLIGHT
END:VTIMEZONE
"""
# The Windows / Exchange BYSETPOS form (Windows Live Calendar, older Exchange web services).
SETPOS = """BEGIN:VTIMEZONE
TZID:Custom Europe{u}
BEGIN:STANDARD
DTSTART:16010101T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
RRULE:FREQ=YEARLY;BYDAY=SU;BYMONTH=10;BYSETPOS=-1
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:16010101T020000
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
RRULE:FREQ=YEARLY;BYDAY=SU;BYMONTH=3;BYSETPOS=-1
END:DAYLIGHT
END:VTIMEZONE
"""
SETPOS_US = """BEGIN:VTIMEZONE
TZID:Custom US{u}
BEGIN:STANDARD
DTSTART:16010101T020000
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=SU;BYMONTH=11;BYSETPOS=1
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:16010101T020000
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=SU;BYMONTH=3;BYSETPOS=2
END:DAYLIGHT
END:VTIMEZONE
"""


def _with(vtz: str, tzid: str, when: str) -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\n" + vtz.replace("\n", "\r\n")
            + f"BEGIN:VEVENT\r\nUID:x\r\nDTSTAMP:20261001T000000Z\r\nDTSTART;TZID={tzid}:{when}\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n")


@pytest.mark.parametrize(("vtz", "name", "summer", "winter"), [
    (NOTES, "Eastern", -4, -5),
    (SETPOS, "Custom Europe", 2, 1),
    (SETPOS_US, "Custom US", -7, -8),
])
def test_real_zone_shapes_keep_summer_time(vtz, name, summer, winter):
    u = uuid.uuid4().hex[:6]
    text = vtz.format(u=u)
    tzid = f"{name}{u}"
    vt = next(iter(Calendar.from_ical(_with(text, tzid, "20260701T100000")).walk("VTIMEZONE")))
    assert zones.zone_problem(vt) is None
    assert _offset(_with(text, tzid, "20260701T100000")) == timedelta(hours=summer)
    assert _offset(_with(text, tzid, "20260115T100000")) == timedelta(hours=winter)
    cal = Calendar.from_ical(_with(text, tzid, "20260701T100000"))
    assert zones.clean(cal) == []  # stored as written, not a fixed offset


def test_us_setpos_dates_are_right():
    u = uuid.uuid4().hex[:6]
    tz = zones.build(next(iter(Calendar.from_ical(_with(SETPOS_US.format(u=u), f"Custom US{u}", "20260701T100000"))
                                .walk("VTIMEZONE"))))
    assert datetime(2026, 3, 8, 3, tzinfo=tz).utcoffset() == timedelta(hours=-7)  # second Sunday of March
    assert datetime(2026, 3, 7, 12, tzinfo=tz).utcoffset() == timedelta(hours=-8)
    assert datetime(2026, 11, 1, 3, tzinfo=tz).utcoffset() == timedelta(hours=-8)  # first Sunday of November


def _part(rule: str) -> str:
    return (f"BEGIN:STANDARD\r\nDTSTART:19700101T000000\r\nTZOFFSETFROM:+0200\r\nTZOFFSETTO:+0100\r\n"
            f"RRULE:{rule}\r\nEND:STANDARD\r\n")


@pytest.mark.parametrize("rule", [
    "FREQ=YEARLY;BYHOUR=1,2,3", "FREQ=YEARLY;BYMINUTE=0,30", "FREQ=YEARLY;BYHOUR=24", "FREQ=YEARLY;BYSECOND=61",
    "FREQ=YEARLY;BYDAY=SU;BYSETPOS=1,2", "FREQ=YEARLY;BYDAY=SU;BYSETPOS=6", "FREQ=YEARLY;BYDAY=2SU;BYSETPOS=1",
    "FREQ=YEARLY;BYDAY=MO,TU,WE,TH,FR,SA,SU;BYSETPOS=-1", "FREQ=YEARLY;BYSETPOS=1",
    "FREQ=YEARLY;BYMONTHDAY=1,2,3,4,5,6,7;BYDAY=SU;BYSETPOS=1", "FREQ=YEARLY;BYYEARDAY=1", "FREQ=YEARLY;BYWEEKNO=1",
    "FREQ=DAILY;BYHOUR=2", "FREQ=YEARLY;BYDAY=SU;BYSETPOS=-1;BYMONTH=2;COUNT=100000",
])
def test_bounds_still_hold(rule):
    cal = Calendar.from_ical("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\nBEGIN:VTIMEZONE\r\nTZID:Z\r\n"
                             f"{_part(rule)}END:VTIMEZONE\r\nEND:VCALENDAR\r\n")
    vtz = next(iter(cal.walk("VTIMEZONE")))
    assert zones.zone_problem(vtz) is not None
    # Refused, so a fixed offset: a lookup walks no rule (a real zone from 1601 scans about 900
    # periods; these rules scan millions, or to the year 9999 without a match).
    with rrule_periods(limit=5_000):
        tz = zones.build(vtz)
        assert datetime(2026, 11, 2, 10, tzinfo=tz).utcoffset() is not None
