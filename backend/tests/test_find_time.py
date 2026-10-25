# ACT-SURFACES: api
"""Free/busy and find-a-time (ACT event.find_time) against the dev Radicale: merged busy
intervals, what does not count (transparent, declined, cancelled, a collection with busy off),
tentative and all-day apart, and the slot ranking, buffer and a DST day."""

import uuid
from datetime import UTC, date, datetime, timedelta

import caldav
import pytest
from dev_services import RADICALE
from dev_services import radicale_up as _radicale_up

from mailr.calendar import freebusy
from mailr.db import sessionmaker
from mailr.models import User

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")

API = "/api/v1"


def _future_monday() -> date:
    """A Monday one to two weeks ahead: find-time never offers a slot in the past."""
    today = datetime.now(UTC).date()
    return today + timedelta(days=14 - today.weekday())


def _dst_end_saturday() -> date:
    """The Saturday before the next US fall-back Sunday (the first Sunday of November) that is
    still ahead of us."""
    today = datetime.now(UTC).date()
    year = today.year
    while True:
        nov1 = date(year, 11, 1)
        sat = nov1 + timedelta(days=(6 - nov1.weekday()) % 7) - timedelta(days=1)
        if sat > today + timedelta(days=1):
            return sat
        year += 1


MON = _future_monday()


def _d(n: int) -> str:
    """The date n days after MON, as YYYY-MM-DD."""
    return (MON + timedelta(days=n)).isoformat()


def _t(n: int, hm: str) -> str:
    return f"{_d(n)}T{hm}:00Z"


async def _connect(client, *names: str):
    username = f"u{uuid.uuid4().hex[:8]}"
    pr = caldav.DAVClient(url=RADICALE, username=username, password="x").principal()
    cals = {n: pr.make_calendar(name=n, supported_calendar_component_set=["VEVENT", "VTODO"])
            for n in names or ("Personal",)}
    r = await client.post(f"{API}/calendar/connect", json={"url": RADICALE, "username": username, "password": "x"})
    assert r.status_code == 200, r.text
    cols = {c["name"]: c for c in (await client.get(f"{API}/calendar/calendars")).json()}
    return cals, cols


async def _tz(user_id: int, name: str) -> None:
    async with sessionmaker()() as db:
        u = await db.get(User, user_id)
        u.settings = {**(u.settings or {}), "timezone": name}
        await db.commit()


async def _ev(client, summary, start, end, **kw):
    r = await client.post(f"{API}/calendar/events", json={"summary": summary, "start": start, "end": end, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _raw_event(cal, summary, start, end, extra=""):
    cal.save_event("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\nBEGIN:VEVENT\r\n"
                   f"UID:{uuid.uuid4().hex}\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:{start}\r\nDTEND:{end}\r\n"
                   f"SUMMARY:{summary}\r\n{extra}END:VEVENT\r\nEND:VCALENDAR\r\n")


def test_merge_joins_overlapping_and_touching_intervals():
    def t(h, m=0):
        return datetime(2026, 11, 2, h, m, tzinfo=UTC)

    assert freebusy.merge([(t(9), t(10)), (t(9, 30), t(11)), (t(11), t(12)), (t(14), t(15)), (t(13), t(13))]) == [
        (t(9), t(12)), (t(14), t(15))]


async def test_freebusy_merges_and_leaves_out_what_is_free(client, user):
    cals, cols = await _connect(client, "Work", "Side")
    await _tz(user.id, "UTC")
    work, side = cols["Work"]["url"], cols["Side"]["url"]
    await _ev(client, "A", _t(0, "09:00"), _t(0, "10:00"), calendar=work)
    await _ev(client, "B", _t(0, "09:30"), _t(0, "11:00"), calendar=work)
    await _ev(client, "Free", _t(0, "12:00"), _t(0, "13:00"), calendar=work, transparent=True)
    await _ev(client, "Maybe", _t(0, "14:00"), _t(0, "15:00"), calendar=work, status="TENTATIVE")
    await _ev(client, "Off", _t(0, "16:00"), _t(0, "17:00"), calendar=work, status="CANCELLED")
    await _ev(client, "Side job", _t(0, "18:00"), _t(0, "19:00"), calendar=side)
    await _ev(client, "Holiday", _d(1), _d(2), calendar=work, all_day=True)
    _raw_event(cals["Work"], "Declined", f"{MON:%Y%m%d}T200000Z", f"{MON:%Y%m%d}T210000Z",
               f"ORGANIZER:mailto:x@acme.example\r\nATTENDEE;PARTSTAT=DECLINED:mailto:{user.email}\r\n")
    params = {"from": _d(0), "to": _d(3)}
    fb = (await client.get(f"{API}/calendar/freebusy", params=params)).json()
    assert fb["busy"] == [{"start": f"{_d(0)}T09:00:00+00:00", "end": f"{_d(0)}T11:00:00+00:00"},
                          {"start": f"{_d(0)}T18:00:00+00:00", "end": f"{_d(0)}T19:00:00+00:00"}]
    assert fb["tentative"] == [{"start": f"{_d(0)}T14:00:00+00:00", "end": f"{_d(0)}T15:00:00+00:00"}]
    assert fb["all_day"] == [{"start": _d(1), "end": _d(2)}]
    # A collection that does not count toward free/busy.
    r = await client.patch(f"{API}/calendar/collections/{cols['Side']['calendar_id']}", json={"busy": False})
    assert r.status_code == 200, r.text
    fb = (await client.get(f"{API}/calendar/freebusy", params=params)).json()
    assert fb["busy"] == [{"start": f"{_d(0)}T09:00:00+00:00", "end": f"{_d(0)}T11:00:00+00:00"}]
    only = (await client.get(f"{API}/calendar/freebusy", params={**params, "calendar": work})).json()
    assert only["busy"] == fb["busy"]


async def test_find_time_ranks_and_respects_buffer(client, user):
    await _connect(client)
    await _tz(user.id, "UTC")
    await _ev(client, "Meeting", _t(0, "10:00"), _t(0, "11:00"))
    body = {"duration": 30, "from": _d(0), "to": _d(1), "limit": 4,
            "working_hours": {"start": "09:00", "end": "17:00", "days": [1, 2, 3, 4, 5]}}
    r = await client.post(f"{API}/calendar/find-time", json=body)
    assert r.status_code == 200, r.text
    slots = [s["start"][11:16] for s in r.json()["slots"]]
    # Slots that leave no small gap beside the meeting or the day's edges come first.
    assert slots[:3] == ["09:00", "09:30", "11:00"] and "09:15" not in slots
    assert r.json()["tz"] == "UTC"
    r = await client.post(f"{API}/calendar/find-time", json={**body, "buffer_minutes": 15, "limit": 20})
    assert r.json()["slots"]
    for s in r.json()["slots"]:  # nothing within 15 minutes of the meeting
        assert not (s["start"][11:16] < "11:15" and s["end"][11:16] > "09:45"), s
    # Busy all-day events skip the day unless asked otherwise.
    await _ev(client, "Offsite", _d(2), _d(3), all_day=True)
    r = await client.post(f"{API}/calendar/find-time", json={**body, "from": _d(2), "to": _d(3)})
    assert r.json()["slots"] == []
    r = await client.post(f"{API}/calendar/find-time", json={**body, "from": _d(2), "to": _d(3),
                                                              "avoid_all_day": False})
    assert r.json()["slots"][0]["start"] == f"{_d(2)}T09:00:00+00:00"
    assert (await client.post(f"{API}/calendar/find-time", json={**body, "to": _d(120)})).status_code == 400
    # The asking device's clock decides "now": a page whose clock reads two days on gets no slot
    # before it (the web sends it, so Insert times agrees with the days the page shows).
    later = datetime.combine(MON + timedelta(days=1), datetime.min.time(), UTC).replace(hour=12)
    r = await client.post(f"{API}/calendar/find-time", json={**body, "to": _d(3), "now": later.isoformat()})
    assert r.json()["slots"] and all(s["start"] >= later.isoformat() for s in r.json()["slots"])


async def test_find_time_keeps_wall_clock_hours_on_a_dst_day(client, user):
    await _connect(client)
    await _tz(user.id, "America/New_York")
    # Saturday (EDT) to Monday (EST); the Sunday between is the 25-hour day.
    sat = _dst_end_saturday()
    day = [(sat + timedelta(days=n)).isoformat() for n in range(4)]
    body = {"duration": 60, "from": day[0], "to": day[3], "limit": 50, "granularity": 60,
            "working_hours": {"start": "09:00", "end": "12:00", "days": [1, 2, 3, 4, 5, 6, 7]}}
    slots = (await client.post(f"{API}/calendar/find-time", json=body)).json()
    starts = {s["start"] for s in slots["slots"]}
    assert {f"{day[0]}T13:00:00+00:00", f"{day[1]}T14:00:00+00:00", f"{day[2]}T14:00:00+00:00"} <= starts
    assert len(starts) == 9 and slots["tz"] == "America/New_York"
    # The earliest day ranks first.
    assert slots["slots"][0]["start"] == f"{day[0]}T13:00:00+00:00"
    # Working hours from the user's settings.
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.settings = {**u.settings, "calendar": {"working_hours": {"start": "13:00", "end": "14:00", "days": [1]}}}
        await db.commit()
    got = (await client.post(f"{API}/calendar/find-time", json={"duration": 60, "from": day[0],
                                                                  "to": day[3]})).json()
    assert [s["start"] for s in got["slots"]] == [f"{day[2]}T18:00:00+00:00"]
    assert got["working_hours"]["days"] == [1]
