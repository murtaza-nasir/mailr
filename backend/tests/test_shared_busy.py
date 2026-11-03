# ACT-SURFACES: api assistant
"""Calendars shared as free/busy only (calendar/shared_busy.py), without a calendar server: the
VFREEBUSY answer Stalwart 1.0.1 gives (copied from a real server), the busy blocks stored from it
(titled "Busy", tentative apart, all-day as dates, nothing else), a snapshot that removes
everything else and clears its text, the blocks in the agenda, free/busy and Ambrose's tools as
"Busy (<calendar>)", writes refused, and the calendar left out of the sharee's household free/busy.

The end-to-end run against Stalwart is tests/test_calendar_sharing_stalwart.py.
Actions covered: calendar.user_shared, calendar.share."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mailr.calendar import shared_busy
from mailr.db import sessionmaker
from mailr.models import CalCollection, CalObject, Connector

pytestmark = pytest.mark.db_user
BASE = "http://stalwart:8080"

# Stalwart 1.0.1's answer to a free-busy-query by a sharee with only read-free-busy (2 Nov 2026):
# a weekly event (three times), a Chicago-time event, an all-day event, an opaque one and a private
# one as BUSY, a tentative one apart; a transparent and a cancelled one left out.
ANSWER = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Stalwart Labs LLC//Stalwart Server//EN\r\nBEGIN:VFREEBUSY\r\n"
    "DTSTART:20261101T000000Z\r\nDTEND:20261201T000000Z\r\nDTSTAMP:20261102T045558Z\r\n"
    "FREEBUSY;FBTYPE=BUSY:20261102T090000Z/20261102T100000Z,20261109T090000Z/202\r\n"
    " 61109T100000Z,20261112T140000Z/20261112T153000Z,20261116T090000Z/20261116T\r\n"
    " 100000Z,20261120T000000Z/20261121T000000Z,20261124T150000Z/20261124T160000\r\n"
    " Z,20261128T150000Z/20261128T160000Z\r\n"
    "FREEBUSY;FBTYPE=BUSY-TENTATIVE:20261126T150000Z/20261126T160000Z\r\n"
    "END:VFREEBUSY\r\nEND:VCALENDAR\r\n")


def _t(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def test_parse_reads_busy_and_tentative_periods():
    got = shared_busy.parse(ANSWER)
    assert [(p.start, p.end, p.tentative) for p in got] == [
        (_t(2, 9), _t(2, 10), False), (_t(9, 9), _t(9, 10), False), (_t(12, 14), _t(12, 15, 30), False),
        (_t(16, 9), _t(16, 10), False), (_t(20), _t(21), False), (_t(24, 15), _t(24, 16), False),
        (_t(26, 15), _t(26, 16), True), (_t(28, 15), _t(28, 16), False)]


def test_parse_takes_durations_and_skips_free_and_bad_periods():
    ics = ("BEGIN:VFREEBUSY\r\nFREEBUSY:20261103T090000Z/PT1H30M\r\nFREEBUSY;FBTYPE=FREE:20261103T120000Z/PT1H\r\n"
           "FREEBUSY;FBTYPE=BUSY-UNAVAILABLE:20261104T080000Z/P1D\r\nFREEBUSY:20261105T090000/20261105T100000\r\n"
           "FREEBUSY:20261106T100000Z/20261106T090000Z\r\nSUMMARY:not a period\r\nEND:VFREEBUSY\r\n")
    assert [(p.start, p.end, p.tentative) for p in shared_busy.parse(ics)] == [
        (_t(3, 9), _t(3, 10, 30), False), (_t(4, 8), _t(5, 8), False)]


def test_a_block_carries_the_time_and_nothing_else():
    timed = shared_busy.ics_of(shared_busy.Period(_t(24, 15), _t(24, 16), True), "Busy")
    assert "DTSTART:20261124T150000Z" in timed and "STATUS:TENTATIVE" in timed and "SUMMARY:Busy" in timed
    assert "X-MAILR-NO-REMINDER:TRUE" in timed and "TRANSP:OPAQUE" in timed
    assert not any(k in timed for k in ("LOCATION", "DESCRIPTION", "ATTENDEE", "ORGANIZER", "URL"))
    day = shared_busy.ics_of(shared_busy.Period(_t(20), _t(22)), "Beschäftigt")
    assert "DTSTART;VALUE=DATE:20261120" in day and "DTEND;VALUE=DATE:20261122" in day
    uid = next(x[4:] for x in day.split("\r\n") if x.startswith("UID:"))
    assert shared_busy.is_block(uid) and not shared_busy.is_block("x@mailr") and not shared_busy.is_block(None)
    # The same period gives the same text, so a repeated answer changes nothing.
    assert shared_busy.ics_of(shared_busy.Period(_t(20), _t(22)), "Busy") == \
        shared_busy.ics_of(shared_busy.Period(_t(20), _t(22)), "Busy")


# ---------------- stored in the sharee's mirror ----------------


def _day(n: int, hour: int = 0) -> datetime:
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return today + timedelta(days=n, hours=hour)


def _iso(v: datetime) -> str:
    return v.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _calendar(user_id: int) -> tuple[int, str]:
    """The sharee's built-in connector, marked synced, with a calendar Ann shares as free/busy only."""
    path = f"/dav/cal/ann-{uuid.uuid4().hex[:6]}%40example.test/rota/"
    async with sessionmaker()() as db:
        future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        conn = Connector(user_id=user_id, kind="caldav", name="Mailr calendar", mode="mirror", builtin=True,
                         config={"account": f"u{user_id}@example.test"}, enabled=True, status="idle",
                         state={"caldav": {"initial_done": True, "checked_at": future, "builtin_ready": True,
                                           "shared_in": {path: {"owner_id": None, "level": "freebusy",
                                                                "can_share": False}}}})
        db.add(conn)
        await db.flush()
        coll = CalCollection(user_id=user_id, connector_id=conn.id, href=BASE + path, displayname="Rota",
                             components=["VEVENT"], read_only=True)
        db.add(coll)
        await db.commit()
        return coll.id, path


async def _rows(coll_id: int, deleted: bool = False) -> list[CalObject]:
    async with sessionmaker()() as db:
        q = select(CalObject).where(CalObject.collection_id == coll_id)
        q = q.where(CalObject.deleted_at.is_not(None) if deleted else CalObject.deleted_at.is_(None))
        return list((await db.execute(q)).scalars().all())


async def test_store_replaces_everything_and_keeps_no_text(user, app):
    """A calendar shared at View before keeps no title or place once only busy times are shared."""
    from mailr.calendar import mirror
    from mailr.calendar.writer import _ctx

    coll_id, _path = await _calendar(user.id)
    secret = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\nBEGIN:VEVENT\r\nUID:secret-1\r\n"
              "DTSTAMP:20261004T120000Z\r\nDTSTART:20261110T100000Z\r\nDTEND:20261110T110000Z\r\n"
              "SUMMARY:Salary review\r\nLOCATION:Room 4\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    async with sessionmaker()() as db:
        coll = await db.get(CalCollection, coll_id)
        tz, me = await _ctx(db, user.id)
        await mirror.upsert_many(db, coll, [(BASE + "/x/secret-1.ics", "e1", secret)], user_tz=tz, me=me)
        await db.commit()
    periods = [shared_busy.Period(_day(2, 15), _day(2, 16)), shared_busy.Period(_day(3, 9), _day(3, 10), True),
               shared_busy.Period(_day(5), _day(6))]
    out = await shared_busy.store(coll_id, periods)
    assert out == {"stored": 3, "changed": 3, "removed": 1}
    live = await _rows(coll_id)
    assert sorted(o.summary for o in live) == ["Busy", "Busy", "Busy"]
    assert all(shared_busy.is_block(o.uid) and not o.location and not o.description for o in live)
    assert sorted((o.status or "") for o in live) == ["", "", "TENTATIVE"]
    assert [o.all_day for o in live if o.start_date] == [True]
    [gone] = await _rows(coll_id, deleted=True)
    assert (gone.summary, gone.location, gone.ics) == ("", "", "")
    # The same answer again changes nothing; a shorter one removes the block that went.
    assert (await shared_busy.store(coll_id, periods))["changed"] == 0
    assert (await shared_busy.store(coll_id, periods[:1]))["removed"] == 2
    async with sessionmaker()() as db:
        c = await db.get(CalCollection, coll_id)
        assert c.last_full_sync_at is not None and c.sync_status == "idle"


@pytest.fixture
async def builtin_on():
    from mailr.calendar import builtin

    async with sessionmaker()() as db:
        await builtin.set_instance_enabled(db, True, "everyone")
        await db.commit()
    yield
    async with sessionmaker()() as db:
        await builtin.set_instance_enabled(db, False, "everyone")
        await db.commit()


async def test_blocks_read_as_busy_everywhere(client, user, builtin_on):
    """The agenda marks them `shared_busy` and read-only; free/busy counts them; Ambrose reads
    "Busy (Rota)" and the time; a write is refused; household free/busy leaves them out."""
    from helpers.agent_calendar_v2 import _ctx

    from mailr.agent.tools import ToolError, run_tool
    from mailr.calendar import household

    coll_id, path = await _calendar(user.id)
    day = _day(14 - _day(0).weekday())
    await shared_busy.store(coll_id, [shared_busy.Period(day + timedelta(hours=9), day + timedelta(hours=17))])
    r = await client.get("/api/v1/calendar/agenda", params={"from": _iso(day), "to": _iso(day + timedelta(days=1))})
    assert r.status_code == 200, r.text
    [e] = [e for e in r.json()["events"] if e["calendar_id"] == coll_id]
    assert e["shared_busy"] is True and e["summary"] == "Busy" and e["phone_busy"] is False
    cals = (await client.get("/api/v1/calendar/calendars")).json()
    [c] = [c for c in cals if c["calendar_id"] == coll_id]
    assert c["read_only"] is True and c["shareable"] is False
    r = await client.post("/api/v1/calendar/events", json={"summary": "x", "start": _iso(day), "end": _iso(day + timedelta(hours=1)),
                                                          "calendar": c["url"]})
    assert r.status_code == 400, r.text
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = await run_tool(ctx, "list_calendar", {"from": _iso(day), "to": _iso(day + timedelta(days=1))})
        [item] = [i for i in res.data["items"] if i["summary"].startswith("Busy")]
        # The calendar's label (calendar/describe.py) names its owner; this test's calendar has none.
        assert item["summary"] == "Busy (Rota (shared by someone))" and "free/busy only" in item["read_only"]
        assert set(item) <= {"uid", "occurrence", "summary", "start", "end", "all_day", "read_only", "when", "cite",
                             "event_tz"}
        got = await run_tool(ctx, "get_event", {"uid": item["uid"]})
        assert got.data["summary"] == item["summary"] and "location" not in got.data
        fb = await run_tool(ctx, "get_free_busy", {"from": _iso(day), "to": _iso(day + timedelta(days=1))})
        assert len(fb.data["busy"]) == 1
        for tool, args in (("update_event", {"uid": item["uid"], "summary": "x"}), ("delete_event", {"uid": item["uid"]}),
                           ("cancel_event", {"uid": item["uid"]})):
            with pytest.raises(ToolError) as e:
                await run_tool(ctx, tool, args)
            assert "free/busy only" in str(e.value), (tool, str(e.value))
        await db.rollback()
        assert str(coll_id) not in await household.calendars_of(db, user.id)
    r = await client.delete(f"/api/v1/calendar/events/{item['uid']}")
    assert r.status_code == 409 and r.json()["code"] == "calendar.shared_busy_read_only", r.text
