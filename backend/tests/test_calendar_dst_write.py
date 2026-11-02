"""Timed values in the repeated hour of the autumn change keep their instant when written.

A wall time with a TZID names the first pass of a repeated hour (RFC 5545 3.3.5), so a value in the
second pass is written in UTC. Fixed dates: nothing here is compared with now.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar

from mailr.calendar import ical
from mailr.calendar import tz as tzmod

CHICAGO = ZoneInfo("America/Chicago")
LONDON = ZoneInfo("Europe/London")


def _read_back(start: str, end: str, zone: ZoneInfo) -> tuple[list[str], datetime, datetime]:
    text = ical.build_event({"summary": "x", "start": start, "end": end}, zone).to_ical().decode()
    lines = [ln for ln in text.splitlines() if ln.startswith(("DTSTART", "DTEND"))]
    ev = next(iter(Calendar.from_ical(text).walk("VEVENT")))
    s = ical.value(ev.get("dtstart"), None, zone)[0]
    e = ical.value(ev.get("dtend"), None, zone)[0]
    assert isinstance(s, datetime)
    assert isinstance(e, datetime)
    return lines, s.astimezone(UTC), e.astimezone(UTC)


def test_end_in_the_second_pass_keeps_its_instant() -> None:
    # 01:42 CDT to 01:27 CST on 1 Nov 2026.
    lines, s, e = _read_back("2026-11-01T06:42:00Z", "2026-11-01T07:27:00Z", CHICAGO)
    assert lines == ["DTSTART;TZID=America/Chicago:20261101T014200", "DTEND:20261101T072700Z"]
    assert s == datetime(2026, 11, 1, 6, 42, tzinfo=UTC)
    assert e == datetime(2026, 11, 1, 7, 27, tzinfo=UTC)


def test_event_inside_the_second_pass_is_not_an_hour_early() -> None:
    lines, s, e = _read_back("2026-11-01T07:10:00Z", "2026-11-01T07:50:00Z", CHICAGO)
    assert lines == ["DTSTART:20261101T071000Z", "DTEND:20261101T075000Z"]
    assert (s, e) == (datetime(2026, 11, 1, 7, 10, tzinfo=UTC), datetime(2026, 11, 1, 7, 50, tzinfo=UTC))


@pytest.mark.parametrize(("start", "end", "zone", "want"), [
    # The first pass of the repeated hour keeps its TZID.
    ("2026-11-01T06:10:00Z", "2026-11-01T06:50:00Z", CHICAGO,
     ["DTSTART;TZID=America/Chicago:20261101T011000", "DTEND;TZID=America/Chicago:20261101T015000"]),
    # Across the spring gap (8 Mar 2026): 01:30 CST to 03:30 CDT.
    ("2026-03-08T07:30:00Z", "2026-03-08T08:30:00Z", CHICAGO,
     ["DTSTART;TZID=America/Chicago:20260308T013000", "DTEND;TZID=America/Chicago:20260308T033000"]),
    # A normal day.
    ("2026-10-14T14:00:00Z", "2026-10-14T15:00:00Z", CHICAGO,
     ["DTSTART;TZID=America/Chicago:20261014T090000", "DTEND;TZID=America/Chicago:20261014T100000"]),
])
def test_other_values_keep_their_tzid(start: str, end: str, zone: ZoneInfo, want: list[str]) -> None:
    lines, s, e = _read_back(start, end, zone)
    assert lines == want
    assert s == datetime.fromisoformat(start)
    assert e == datetime.fromisoformat(end)


def test_london_second_pass() -> None:
    # 25 Oct 2026: 01:00–02:00 BST is followed by 01:00–02:00 GMT. 01:20 GMT is 01:20Z.
    assert tzmod.to_zone(datetime(2026, 10, 25, 1, 20, tzinfo=UTC), LONDON) == datetime(2026, 10, 25, 1, 20, tzinfo=UTC)
    first = tzmod.to_zone(datetime(2026, 10, 25, 0, 20, tzinfo=UTC), LONDON)
    assert isinstance(first, datetime)
    assert first.tzinfo is LONDON
    assert (first.hour, first.minute) == (1, 20)
