# ACT-SURFACES: api assistant
"""Phone calendars (calendar/phonecal.py; docs/design/device-calendars.md): busy times that the
Android app sends from calendars on the phone.

Covers (api): ACT calendar.phone_sync, ACT calendar.phone_list, ACT calendar.phone_remove: the strict
schema (title and place only with details; guests, description and unknown fields 422), the limits,
replace-in-window, idempotency, owner only, DELETE; the blocks in the agenda, free/busy and
find-a-time as "Busy", or with details as their title and place; switching back to busy times
leaves no title or place stored; edit, delete, cancel, move and RSVP refused; not in household
free/busy, not shareable, never written to the calendar server.
Covers (assistant): the calendar tools read a busy-only block as "Busy (<calendar>)" with the time
only, a block with details as its title and place from a phone calendar, and refuse to change them."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import CalCollection, CalObject, Connector, User

pytestmark = pytest.mark.db_user
API = "/api/v1/calendar/phone-calendars"


def _key() -> str:
    return uuid.uuid4().hex


def _bid(n: int) -> str:
    return f"{n:024x}"


def _day(n: int, hour: int = 0) -> datetime:
    """A day n days from today (UTC midnight), plus `hour` hours: tests never use a fixed date."""
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return today + timedelta(days=n, hours=hour)


def _iso(v: datetime) -> str:
    return v.strftime("%Y-%m-%dT%H:%M:%SZ")


def _block(n: int, start: datetime, hours: float = 1.0, all_day: bool = False) -> dict:
    end = start + (timedelta(days=1) if all_day else timedelta(hours=hours))
    return {"id": _bid(n), "start": _iso(start), "end": _iso(end), "all_day": all_day}


def _body(busy: list[dict], *, ws: datetime | None = None, we: datetime | None = None, **kw) -> dict:
    return {"name": kw.pop("name", "Calendar (me@work.example)"), "color": kw.pop("color", "#0078d4"),
            "device": kw.pop("device", "Pixel 9 Pro Fold"),
            "window_start": _iso(ws or _day(-30)), "window_end": _iso(we or _day(180)), "busy": busy, **kw}


async def _put(client, key: str, body: dict, status: int = 200) -> dict:
    r = await client.put(f"{API}/{key}", json=body)
    assert r.status_code == status, r.text
    return r.json()


async def _objects(key: str) -> list[CalObject]:
    async with sessionmaker()() as db:
        rows = (await db.execute(
            select(CalObject).join(CalCollection, CalCollection.id == CalObject.collection_id)
            .where(CalCollection.href == f"phone:{key}", CalObject.deleted_at.is_(None)))).scalars().all()
        return list(rows)


async def _agenda(client, frm: datetime, to: datetime) -> list[dict]:
    r = await client.get("/api/v1/calendar/agenda", params={"from": _iso(frm), "to": _iso(to)})
    assert r.status_code == 200, r.text
    return r.json()["events"]


# ---------------- the schema ----------------


@pytest.mark.parametrize("extra", [{"title": "Budget meeting"}, {"location": "Room 101"},
                                   {"attendees": ["a@example.edu"]}, {"description": "notes"}])
async def test_schema_refuses_content_fields(client, extra):
    """ACT calendar.phone_sync: busy times only by default: a title or place needs details, and
    unknown fields are refused (422)."""
    key = _key()
    await _put(client, key, _body([_block(1, _day(2, 15))], **extra), 422)
    blk = {**_block(1, _day(2, 15)), **extra}
    await _put(client, key, _body([blk]), 422)
    await _put(client, key, _body([blk], details=False), 422)
    assert (await client.get(API)).json() == []


@pytest.mark.parametrize("extra", [{"attendees": ["a@example.edu"]}, {"description": "notes"},
                                   {"notes": "notes"}, {"organizer": "a@example.edu"}, {"url": "https://example.com"},
                                   {"event_id": "42"}, {"guests": 3}])
async def test_details_still_refuse_everything_else(client, extra):
    """With details only the title and place are accepted: guests, descriptions, notes, the
    organizer and event ids are still refused (422)."""
    key = _key()
    blk = {**_block(1, _day(2, 15)), "title": "Planning", **extra}
    await _put(client, key, _body([blk], details=True), 422)
    await _put(client, key, _body([_block(1, _day(2, 15))], details=True, **extra), 422)
    assert (await client.get(API)).json() == []


async def test_details_text_limits(client):
    key = _key()
    blk = _block(1, _day(2, 15))
    await _put(client, key, _body([{**blk, "title": "x" * 301}], details=True), 422)
    await _put(client, key, _body([{**blk, "location": "y" * 301}], details=True), 422)
    await _put(client, key, _body([{**blk, "title": 7}], details=True), 422)
    await _put(client, key, _body([{**blk, "title": "x"}], details="sometimes"), 422)
    assert (await client.get(API)).json() == []
    # 300 characters fit; line breaks and runs of spaces fold into one space first.
    await _put(client, key, _body([{**blk, "title": "x" * 300, "location": "  Hall\n  B  " + "y" * 292}], details=True))
    [o] = await _objects(key)
    assert o.summary == "x" * 300 and o.location == "Hall B " + "y" * 292
    # A title of white space only is no title: allowed without details, stored as "Busy".
    await _put(client, key, _body([{**blk, "title": " \n ", "location": ""}]))
    [o] = await _objects(key)
    assert o.summary == "Busy" and not o.location


async def test_schema_limits(client):
    key = _key()
    await _put(client, "NOT-A-KEY", _body([]), 422)
    await _put(client, key.upper(), _body([]), 422)  # hex, lowercase
    await _put(client, key[:31], _body([]), 422)
    await _put(client, key, _body([{**_block(1, _day(2, 15)), "id": "xyz"}]), 422)
    await _put(client, key, _body([], ws=_day(0), we=_day(401)), 422)  # window over 400 days
    await _put(client, key, _body([], ws=_day(5), we=_day(1)), 422)
    await _put(client, key, _body([_block(1, _day(300, 10))], ws=_day(0), we=_day(10)), 422)  # outside the window
    await _put(client, key, _body([_block(1, _day(2, 15)), _block(1, _day(3, 15))]), 422)  # same id twice
    await _put(client, key, _body([{**_block(1, _day(2, 15)), "all_day": True}]), 422)  # all-day not at midnight
    await _put(client, key, _body([_block(i, _day(1) + timedelta(minutes=i)) for i in range(5001)]), 422)
    await _put(client, key, _body([{"id": _bid(1), "start": "2026-10-30T17:00:00", "end": "2026-10-30T18:00:00"}]),
               422)  # no offset
    assert (await client.get(API)).json() == []


async def test_five_thousand_blocks_fit(client):
    key = _key()
    out = await _put(client, key, _body([_block(i, _day(1) + timedelta(minutes=30 * i), 0.25) for i in range(5000)]))
    assert out["stored"] == 5000
    [cal] = (await client.get(API)).json()
    assert cal["count"] == 5000


# ---------------- storage ----------------


async def test_put_list_and_shape(client, user):
    """ACT calendar.phone_list: name, colour, device, synced_at, count; no content stored."""
    key = _key()
    out = await _put(client, key, _body([_block(1, _day(2, 15), 1.5), _block(2, _day(3), all_day=True)]))
    assert out["stored"] == 2 and out["synced_at"]
    [cal] = (await client.get(API)).json()
    assert cal["key"] == key and cal["name"] == "Calendar (me@work.example)" and cal["color"] == "#0078d4"
    assert cal["device"] == "Pixel 9 Pro Fold" and cal["count"] == 2 and cal["stale"] is False
    assert datetime.fromisoformat(cal["synced_at"]) > datetime.now(UTC) - timedelta(minutes=1)
    objs = await _objects(key)
    assert {o.summary for o in objs} == {"Busy"}
    for o in objs:
        assert not o.location and not o.description and not o.attendees and not o.organizer and not o.url
        assert o.no_reminder and (o.transp or "OPAQUE").upper() == "OPAQUE"
        for word in ("LOCATION", "ATTENDEE", "ORGANIZER", "DESCRIPTION", "VALARM", "URL"):
            assert word not in o.ics
    async with sessionmaker()() as db:
        coll = (await db.execute(select(CalCollection).where(
            CalCollection.href == f"phone:{key}"))).scalar_one()
        assert coll.read_only and coll.busy and not coll.notify
        conn = await db.get(Connector, coll.connector_id)
        assert conn.kind == "phone" and conn.user_id == user.id and conn.data_class == "normal"

    # The calendar list: read-only, with the device and freshness; not offered for sharing.
    cols = (await client.get("/api/v1/calendar/calendars")).json()
    [c] = [c for c in cols if c.get("phone")]
    assert c["read_only"] is True and c["shareable"] is False and c["name"] == "Calendar (me@work.example)"
    assert c["phone"]["device"] == "Pixel 9 Pro Fold" and c["phone"]["stale"] is False
    # Not listed as a mail or calendar account.
    assert all(a.get("kind") != "phone" for a in (await client.get("/api/v1/connectors")).json())


async def test_replace_inside_the_window_keeps_blocks_outside(client):
    key = _key()
    await _put(client, key, _body([_block(1, _day(-20, 9)), _block(2, _day(5, 9)), _block(3, _day(50, 9))],
                                  ws=_day(-30), we=_day(180)))
    # A narrower window: block 1 (before it) stays, block 2 is replaced by 4, block 3 (inside, absent) goes.
    out = await _put(client, key, _body([_block(4, _day(6, 9))], ws=_day(0), we=_day(60)))
    assert out["stored"] == 1
    got = sorted((o.start_at, o.href.rsplit("/", 1)[-1]) for o in await _objects(key))
    assert [h for _, h in got] == [f"{_bid(1)}.ics", f"{_bid(4)}.ics"]
    # An empty snapshot clears the window and nothing else.
    await _put(client, key, _body([], ws=_day(0), we=_day(60)))
    assert [o.href.rsplit("/", 1)[-1] for o in await _objects(key)] == [f"{_bid(1)}.ics"]
    [cal] = (await client.get(API)).json()
    assert cal["count"] == 1


async def test_same_snapshot_twice_is_the_same_state(client):
    key = _key()
    body = _body([_block(1, _day(2, 15)), _block(2, _day(4), all_day=True)])
    await _put(client, key, body)
    first = {(o.id, o.etag, o.rev) for o in await _objects(key)}
    await _put(client, key, body)
    second = {(o.id, o.etag, o.rev) for o in await _objects(key)}
    assert first == second  # nothing rewritten
    # A moved block is rewritten in place (same id, new time).
    moved = _body([_block(1, _day(2, 16)), _block(2, _day(4), all_day=True)])
    await _put(client, key, moved)
    after = {o.href: o for o in await _objects(key)}
    assert len(after) == 2
    b1 = next(o for h, o in after.items() if h.endswith(f"{_bid(1)}.ics"))
    assert b1.start_at == _day(2, 16)


async def test_name_colour_and_device_follow_the_phone(client):
    key = _key()
    await _put(client, key, _body([_block(1, _day(2, 15))]))
    await _put(client, key, _body([_block(1, _day(2, 15))], name="Work", color="#112233", device="Pixel 10"))
    [cal] = (await client.get(API)).json()
    assert (cal["name"], cal["color"], cal["device"]) == ("Work", "#112233", "Pixel 10")


async def test_delete_removes_everything(client):
    """ACT calendar.phone_remove: the calendar and every block go; a second DELETE is no error."""
    key = _key()
    await _put(client, key, _body([_block(i, _day(2, 8 + i)) for i in range(3)]))
    assert len(await _agenda(client, _day(2), _day(3))) == 3
    r = await client.delete(f"{API}/{key}")
    assert r.status_code == 200 and r.json() == {"ok": True, "removed": True}
    assert (await client.get(API)).json() == []
    assert await _objects(key) == []
    assert await _agenda(client, _day(2), _day(3)) == []
    assert not [c for c in (await client.get("/api/v1/calendar/calendars")).json() if c.get("phone")]
    async with sessionmaker()() as db:
        left = (await db.execute(select(CalObject).join(
            CalCollection, CalCollection.id == CalObject.collection_id).where(
            CalCollection.href == f"phone:{key}"))).scalars().all()
        assert left and all(o.deleted_at and not o.ics and o.start_at is None for o in left)  # tombstones, emptied
    r = await client.delete(f"{API}/{key}")
    assert r.status_code == 200 and r.json()["removed"] is False
    # The same key can send again: a new calendar.
    await _put(client, key, _body([_block(9, _day(2, 9))]))
    [cal] = (await client.get(API)).json()
    assert cal["count"] == 1


async def test_other_users_cannot_read_or_write(app, client, user):
    from conftest import make_db_user

    key = _key()
    await _put(client, key, _body([_block(1, _day(2, 15))]))
    other = await make_db_user()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as oc:
        r = await oc.post("/api/v1/auth/login", json={"email": other.email, "password": "test-password-123"})
        oc.headers["X-CSRF-Token"] = r.json()["csrf"]
        assert (await oc.get(API)).json() == []
        assert await _agenda(oc, _day(2), _day(3)) == []
        # The same key from another user is that user's own calendar; the first is untouched.
        r = await oc.delete(f"{API}/{key}")
        assert r.status_code == 200 and r.json()["removed"] is False
        await _put(oc, key, _body([], name="Theirs"))
        uid = (await _agenda(client, _day(2), _day(3)))[0]["uid"]
        r = await oc.get(f"/api/v1/calendar/events/{uid}")
        assert r.status_code == 404
    [mine] = (await client.get(API)).json()
    assert mine["name"] == "Calendar (me@work.example)" and mine["count"] == 1
    anon = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    async with anon:
        assert (await anon.get(API)).status_code == 401
        assert (await anon.put(f"{API}/{key}", json=_body([]))).status_code in (401, 403)


async def test_token_needs_the_calendar_scope(client):
    from helpers.calendar import _token_client

    key = _key()
    reader = await _token_client(client, ["read"])
    async with reader:
        assert (await reader.put(f"{API}/{key}", json=_body([_block(1, _day(2, 15))]))).status_code == 403
        assert (await reader.get(API)).status_code == 200
    writer = await _token_client(client, ["read", "calendar"])
    async with writer:
        r = await writer.put(f"{API}/{key}", json=_body([_block(1, _day(2, 15))]))
        assert r.status_code == 200, r.text  # calendar.phone_sync: allowed by default
        assert (await writer.delete(f"{API}/{key}")).json()["removed"] is True


# ---------------- what reads them ----------------


async def test_agenda_free_busy_and_find_time_include_the_blocks(client):
    key = _key()
    day = _day(14 - _day(0).weekday())  # a Monday ahead
    await _put(client, key, _body([_block(1, day + timedelta(hours=9), 8), _block(2, day + timedelta(days=1), all_day=True)]))
    evs = await _agenda(client, day, day + timedelta(days=2))
    assert {e["summary"] for e in evs} == {"Busy"}
    timed = next(e for e in evs if not e["all_day"])
    assert timed["phone_busy"] is True and timed["no_reminders"] is True and timed["attendee_list"] == []
    assert not timed["location"] and not timed["description"] and not timed["url"]
    allday = next(e for e in evs if e["all_day"])
    assert allday["start"] == (day + timedelta(days=1)).date().isoformat()

    fb = (await client.get("/api/v1/calendar/freebusy", params={"from": _iso(day), "to": _iso(day + timedelta(days=2))})).json()
    assert {"start": _iso(day + timedelta(hours=9)).replace("Z", "+00:00"),
            "end": _iso(day + timedelta(hours=17)).replace("Z", "+00:00")} in [
        {k: v.replace("Z", "+00:00") for k, v in i.items()} for i in fb["busy"]], fb
    r = await client.post("/api/v1/calendar/find-time", json={
        "duration": 60, "from": _iso(day), "to": _iso(day + timedelta(days=1)), "tz": "UTC",
        "working_hours": {"start": "08:00", "end": "18:00", "days": [1, 2, 3, 4, 5]}})
    assert r.status_code == 200, r.text
    slots = r.json()["slots"]
    assert slots
    for s in slots:
        a, b = datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"])
        assert b <= day + timedelta(hours=9) or a >= day + timedelta(hours=17), s


async def test_edit_delete_cancel_move_and_rsvp_are_refused(client):
    key = _key()
    await _put(client, key, _body([_block(1, _day(2, 15))]))
    [ev] = await _agenda(client, _day(2), _day(3))
    uid = ev["uid"]
    calls = [("PATCH", f"/api/v1/calendar/events/{uid}", {"summary": "Mine now"}),
             ("PATCH", f"/api/v1/calendar/events/{uid}", {"start": _iso(_day(2, 16)), "end": _iso(_day(2, 17))}),
             ("DELETE", f"/api/v1/calendar/events/{uid}", None),
             ("POST", f"/api/v1/calendar/events/{uid}/cancel", {}),
             ("POST", f"/api/v1/calendar/events/{uid}/rsvp", {"response": "accepted"}),
             ("POST", f"/api/v1/calendar/events/{uid}/move", {"calendar": "/dav/cal/x/other/"})]
    for method, url, body in calls:
        r = await client.request(method, url, json=body)
        assert r.status_code == 409, (method, url, r.text)
        assert r.json()["code"] == "calendar.phone_read_only", r.text
        assert "busy times from your phone" in r.json()["detail"]
    assert [o.summary for o in await _objects(key)] == ["Busy"]
    assert (await _agenda(client, _day(2), _day(3)))[0]["start"] == ev["start"]


async def test_writer_refuses_the_collection(client, user):
    """The writer's own check (the backstop when a route did not refuse first)."""
    from fastapi import HTTPException

    from mailr.calendar import writer

    key = _key()
    await _put(client, key, _body([_block(1, _day(2, 15))]))
    [obj] = await _objects(key)
    async with sessionmaker()() as db:
        obj = await db.get(CalObject, obj.id)
        with pytest.raises(HTTPException) as e:
            await writer.delete(db, user.id, obj)
        assert e.value.status_code == 409


async def test_not_in_household_free_busy_and_not_shareable(client, user):
    from mailr.calendar import household

    key = _key()
    await _put(client, key, _body([_block(1, _day(2, 15))]))
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        assert await household.calendars_of(db, user.id) == []
        b = await household.busy_of(db, u, _day(1), _day(4))
        assert b.busy == [] and b.tentative == [] and b.all_day == []
        coll_id = (await db.execute(select(CalCollection.id).where(
            CalCollection.href == f"phone:{key}"))).scalar_one()
    r = await client.put(f"/api/v1/calendar/collections/{coll_id}/shares/{user.id + 1}", json={"level": "view"})
    assert r.status_code == 400 and "Only calendars of your Mailr calendar account" in r.text, r.text


async def test_collection_settings_keep_reminders_off(client):
    key = _key()
    await _put(client, key, _body([_block(1, _day(2, 15))]))
    [c] = [c for c in (await client.get("/api/v1/calendar/calendars")).json() if c.get("phone")]
    r = await client.patch(f"/api/v1/calendar/collections/{c['calendar_id']}", json={"notify": True, "hidden": True})
    assert r.status_code == 200, r.text
    [c] = [c for c in (await client.get("/api/v1/calendar/calendars")).json() if c.get("phone")]
    assert c["notify"] is False and c["hidden"] is True
    r = await client.patch(f"/api/v1/calendar/collections/{c['calendar_id']}", json={"name": "Renamed"})
    assert r.status_code >= 400
    # A colour chosen here stays in mailr; the next snapshot keeps it.
    r = await client.patch(f"/api/v1/calendar/collections/{c['calendar_id']}", json={"color": "#33aa55"})
    assert r.status_code == 200, r.text
    await _put(client, key, _body([_block(1, _day(2, 15))]))
    [c] = [c for c in (await client.get("/api/v1/calendar/calendars")).json() if c.get("phone")]
    assert c["color"] == "#33aa55" and c["color_local"] is True


async def test_stale_after_a_day(client):
    key = _key()
    await _put(client, key, _body([_block(1, _day(2, 15))]))
    async with sessionmaker()() as db:
        coll = (await db.execute(select(CalCollection).where(
            CalCollection.href == f"phone:{key}"))).scalar_one()
        coll.last_sync_at = datetime.now(UTC) - timedelta(hours=25)
        await db.commit()
    [cal] = (await client.get(API)).json()
    assert cal["stale"] is True
    [c] = [c for c in (await client.get("/api/v1/calendar/calendars")).json() if c.get("phone")]
    assert c["phone"]["stale"] is True


# ---------------- Ambrose ----------------


async def test_assistant_reads_busy_with_the_time_only(client, user):
    """The calendar tools: "Busy (<calendar>)" and the time; free/busy and find_time count the
    blocks; update, delete, cancel and rsvp are refused."""
    from helpers.agent_calendar_v2 import _ctx

    from mailr.agent.tools import ToolError, run_tool

    key = _key()
    day = _day(14 - _day(0).weekday())
    await _put(client, key, _body([_block(1, day + timedelta(hours=9), 8)], name="Work"))
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = await run_tool(ctx, "list_calendar", {"from": _iso(day), "to": _iso(day + timedelta(days=1))})
        [item] = res.data["items"]
        assert item["summary"] == "Busy (Work)"
        assert set(item) <= {"uid", "occurrence", "summary", "start", "end", "all_day", "read_only", "when", "cite",
                             "event_tz"}
        got = await run_tool(ctx, "get_event", {"uid": item["uid"]})
        assert got.data["summary"] == "Busy (Work)" and "attendee_list" not in got.data and "calendar" not in got.data
        found = await run_tool(ctx, "search_calendar", {"q": "busy", "kind": "event"})
        assert [i["summary"] for i in found.data["items"]] == ["Busy (Work)"]
        fb = await run_tool(ctx, "get_free_busy", {"from": _iso(day), "to": _iso(day + timedelta(days=1))})
        assert len(fb.data["busy"]) == 1
        ft = await run_tool(ctx, "find_time", {"duration": 60, "from": _iso(day), "to": _iso(day + timedelta(days=1)),
                                               "timezone": "UTC",
                                               "working_hours": {"start": "08:00", "end": "18:00", "days": [1, 2, 3, 4, 5]}})
        for s in ft.data["slots"]:
            a, b = datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"])
            assert b <= day + timedelta(hours=9) or a >= day + timedelta(hours=17), s
        for tool, args in (("update_event", {"uid": item["uid"], "summary": "x"}), ("delete_event", {"uid": item["uid"]}),
                           ("cancel_event", {"uid": item["uid"]}),
                           ("rsvp", {"event_uid": item["uid"], "response": "accepted"})):
            with pytest.raises(ToolError) as e:
                await run_tool(ctx, tool, args)
            assert "phone" in str(e.value), (tool, str(e.value))
        await db.rollback()
    [o] = await _objects(key)
    assert o.summary == "Busy" and o.start_at == day + timedelta(hours=9)


async def test_assistant_context_lists_busy_blocks(client, user):
    """The agenda in Ambrose's reply context (agent/context.py) reads "Busy (<calendar>)"."""
    from mailr.agent import context
    from mailr.core.auth import ALL_SCOPES, Principal

    key = _key()
    await _put(client, key, _body([_block(1, _day(3, 15))], name="Work"))
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        p = Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"})
        out = await context._calendar(db, p, [], "")
    briefs = [b for day in out["busy"].values() for b in day] + out["upcoming"]
    assert briefs and all(b["summary"] == "Busy (Work)" for b in briefs)


# ---------------- details: titles and places ----------------


def _titled(n: int, start: datetime, title: str, location: str = "", hours: float = 1.0, all_day: bool = False) -> dict:
    return {**_block(n, start, hours, all_day), "title": title, "location": location}


async def _all_rows(key: str) -> list[CalObject]:
    """Every row of the calendar, tombstones included."""
    async with sessionmaker()() as db:
        return list((await db.execute(select(CalObject).join(
            CalCollection, CalCollection.id == CalObject.collection_id).where(
            CalCollection.href == f"phone:{key}"))).scalars().all())


async def _occurrence_texts(key: str) -> list[tuple[str | None, str | None]]:
    from mailr.models import CalOccurrence

    async with sessionmaker()() as db:
        return [(r.summary, r.location) for r in (await db.execute(select(CalOccurrence).join(
            CalCollection, CalCollection.id == CalOccurrence.collection_id).where(
            CalCollection.href == f"phone:{key}"))).scalars().all()]


async def test_details_store_title_and_place(client, user):
    """ACT calendar.phone_sync with details: the title and place are stored and read back in the
    agenda, the event detail and the lists; nothing else is."""
    key = _key()
    out = await _put(client, key, _body([_titled(1, _day(2, 15), "Planning review", "Hall B", 1.5),
                                         _titled(2, _day(3), "Field day", "", all_day=True),
                                         _titled(3, _day(4, 9), "", "")], details=True, name="Work"))
    assert out["stored"] == 3
    [cal] = (await client.get(API)).json()
    assert cal["details"] is True and cal["count"] == 3
    [c] = [c for c in (await client.get("/api/v1/calendar/calendars")).json() if c.get("phone")]
    assert c["phone"]["details"] is True and c["read_only"] is True and c["shareable"] is False
    objs = {o.href.rsplit("/", 1)[-1]: o for o in await _objects(key)}
    o1, o2, o3 = (objs[f"{_bid(i)}.ics"] for i in (1, 2, 3))
    assert (o1.summary, o1.location) == ("Planning review", "Hall B")
    assert (o2.summary, o2.location) == ("Field day", "")
    assert (o3.summary, o3.location) == ("Busy", "")  # no title: "Busy", and no details mark
    assert o1.uid.endswith("-d@mailr") and o2.uid.endswith("-d@mailr") and o3.uid.endswith(f"{_bid(3)}@mailr")
    for o in (o1, o2, o3):
        assert not o.description and not o.attendees and not o.organizer and not o.url and not o.alarms
        assert o.no_reminder and (o.transp or "OPAQUE").upper() == "OPAQUE"
        for word in ("ATTENDEE", "ORGANIZER", "DESCRIPTION", "VALARM", "URL"):
            assert word not in o.ics
    evs = {e["uid"]: e for e in await _agenda(client, _day(2), _day(5))}
    e1 = evs[o1.uid]
    assert (e1["summary"], e1["location"]) == ("Planning review", "Hall B")
    assert e1["phone_busy"] is True and e1["phone_details"] is True and e1["no_reminders"] is True
    assert e1["attendee_list"] == [] and not e1["description"] and not e1["url"]
    assert evs[o3.uid]["phone_details"] is False and evs[o3.uid]["summary"] == "Busy"
    r = await client.get(f"/api/v1/calendar/events/{o1.uid}")
    assert r.status_code == 200, r.text
    got = r.json()
    got = got.get("event", got)
    assert (got["summary"], got["location"], got["phone_details"]) == ("Planning review", "Hall B", True)
    # Search finds it by its title and its place; changes are still refused.
    for q in ("Planning", "Hall"):
        r = await client.get("/api/v1/calendar/search", params={"q": q})
        assert r.status_code == 200 and "Planning review" in r.text, r.text
    r = await client.patch(f"/api/v1/calendar/events/{o1.uid}", json={"summary": "Mine now"})
    assert r.status_code == 409 and r.json()["code"] == "calendar.phone_read_only"
    # The same snapshot twice changes nothing.
    first = {(o.id, o.etag, o.rev) for o in await _objects(key)}
    await _put(client, key, _body([_titled(1, _day(2, 15), "Planning review", "Hall B", 1.5),
                                   _titled(2, _day(3), "Field day", "", all_day=True),
                                   _titled(3, _day(4, 9), "", "")], details=True, name="Work"))
    assert {(o.id, o.etag, o.rev) for o in await _objects(key)} == first


async def test_switching_back_to_busy_clears_every_title_and_place(client):
    """A PUT with details false leaves no title or place of the calendar anywhere: blocks inside
    the window are replaced, blocks outside it are rewritten as "Busy", and tombstones are empty."""
    key = _key()
    await _put(client, key, _body([_titled(1, _day(-20, 9), "Old planning", "Hall A"),
                                   _titled(2, _day(5, 9), "Review", "Hall B"),
                                   _titled(3, _day(50, 9), "Offsite", "North campus"),
                                   _titled(4, _day(120, 0), "Retreat", "Lake house", all_day=True),
                                   _titled(5, _day(6, 9), "Gone soon", "Hall C")],
                                  details=True, ws=_day(-30), we=_day(180)))
    # Block 5 leaves while details are on: its tombstone keeps no text either.
    await _put(client, key, _body([_titled(1, _day(-20, 9), "Old planning", "Hall A"),
                                   _titled(2, _day(5, 9), "Review", "Hall B"),
                                   _titled(3, _day(50, 9), "Offsite", "North campus"),
                                   _titled(4, _day(120, 0), "Retreat", "Lake house", all_day=True)],
                                  details=True, ws=_day(-30), we=_day(180)))
    # Busy times only, in a narrower window: block 1 (before it) and blocks 3 and 4 (after it) stay as
    # busy times; block 2 is replaced by a busy block.
    await _put(client, key, _body([_block(2, _day(5, 9))], ws=_day(0), we=_day(30)))
    [cal] = (await client.get(API)).json()
    assert cal["details"] is False and cal["count"] == 4
    words = ("Old planning", "Hall A", "Review", "Hall B", "Offsite", "North campus", "Retreat", "Lake house",
             "Gone soon", "Hall C")
    rows = await _all_rows(key)
    assert len(rows) == 5
    for o in rows:
        assert not o.location and o.summary in ("Busy", ""), (o.summary, o.location)
        assert o.deleted_at is not None or not o.uid.endswith("-d@mailr")  # a live block has no details mark
        assert not any(w in (o.ics or "") for w in words) and "LOCATION" not in (o.ics or "")
    live = {o.href.rsplit("/", 1)[-1]: o for o in await _objects(key)}
    assert set(live) == {f"{_bid(i)}.ics" for i in (1, 2, 3, 4)}
    assert live[f"{_bid(1)}.ics"].start_at == _day(-20, 9) and live[f"{_bid(3)}.ics"].start_at == _day(50, 9)
    assert live[f"{_bid(4)}.ics"].all_day and live[f"{_bid(4)}.ics"].start_date == _day(120).date()
    for summary, location in await _occurrence_texts(key):
        assert summary in ("Busy", None, "") and not location
    evs = await _agenda(client, _day(-21), _day(130))
    assert len(evs) == 4 and {e["summary"] for e in evs} == {"Busy"} and not any(e["phone_details"] for e in evs)
    async with sessionmaker()() as db:
        coll = (await db.execute(select(CalCollection).where(CalCollection.href == f"phone:{key}"))).scalar_one()
        conn = await db.get(Connector, coll.connector_id)
        assert conn.config["details"] is False


async def test_details_owner_only(app, client):
    from conftest import make_db_user

    key = _key()
    await _put(client, key, _body([_titled(1, _day(2, 15), "Planning review", "Hall B")], details=True))
    uid = (await _agenda(client, _day(2), _day(3)))[0]["uid"]
    other = await make_db_user()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as oc:
        r = await oc.post("/api/v1/auth/login", json={"email": other.email, "password": "test-password-123"})
        oc.headers["X-CSRF-Token"] = r.json()["csrf"]
        assert (await oc.get(API)).json() == []
        assert await _agenda(oc, _day(2), _day(3)) == []
        assert (await oc.get(f"/api/v1/calendar/events/{uid}")).status_code == 404
        r = await oc.get("/api/v1/calendar/search", params={"q": "Planning"})
        assert "Planning review" not in r.text


async def test_details_not_in_household_sharing_or_the_calendar_server(client, user, monkeypatch):
    """Blocks with details stay out of household free/busy and sharing, and nothing is sent to the
    mail store's calendar server (so the DAV gateway never serves them)."""
    from mailr.calendar import dav, household

    async def no_dav(*a, **kw):
        raise AssertionError("a phone calendar wrote to a calendar server")

    monkeypatch.setattr(dav.DavClient, "_send", no_dav)
    key = _key()
    await _put(client, key, _body([_titled(1, _day(2, 15), "Planning review", "Hall B")], details=True))
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        assert await household.calendars_of(db, user.id) == []
        b = await household.busy_of(db, u, _day(1), _day(4))
        assert b.busy == [] and b.tentative == [] and b.all_day == []
        coll = (await db.execute(select(CalCollection).where(CalCollection.href == f"phone:{key}"))).scalar_one()
        assert (await db.get(Connector, coll.connector_id)).kind == "phone"
    r = await client.put(f"/api/v1/calendar/collections/{coll.id}/shares/{user.id + 1}", json={"level": "view"})
    assert r.status_code == 400, r.text


async def test_assistant_reads_titles_and_places_with_details(client, user):
    """With details the calendar tools and the reply context read the title and place like an
    ordinary read-only event, marked as from a phone calendar; busy-only blocks stay "Busy"."""
    from helpers.agent_calendar_v2 import _ctx

    from mailr.agent import context
    from mailr.agent.tools import ToolError, run_tool
    from mailr.core.auth import ALL_SCOPES, Principal

    key, busy_key = _key(), _key()
    day = _day(14 - _day(0).weekday())
    await _put(client, key, _body([_titled(1, day + timedelta(hours=9), "Planning review", "Hall B")],
                                  details=True, name="Work"))
    await _put(client, busy_key, _body([_block(1, day + timedelta(hours=13))], name="Second"))
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = await run_tool(ctx, "list_calendar", {"from": _iso(day), "to": _iso(day + timedelta(days=1))})
        items = {i["summary"]: i for i in res.data["items"]}
        assert set(items) == {"Planning review", "Busy (Second)"}
        item = items["Planning review"]
        assert item["location"] == "Hall B" and item["calendar"] == "Work" and "phone" in item["read_only"]
        assert not {"attendees", "description", "attendee_list"} & set(item)
        got = await run_tool(ctx, "get_event", {"uid": item["uid"]})
        assert (got.data["summary"], got.data["location"], got.data["calendar"]) == ("Planning review", "Hall B", "Work")
        assert "phone" in got.data["read_only"] and "attendee_list" not in got.data and "description" not in got.data
        found = await run_tool(ctx, "search_calendar", {"q": "planning", "kind": "event"})
        assert [i["summary"] for i in found.data["items"]] == ["Planning review"]
        fb = await run_tool(ctx, "get_free_busy", {"from": _iso(day), "to": _iso(day + timedelta(days=1))})
        assert len(fb.data["busy"]) == 2
        with pytest.raises(ToolError) as e:
            await run_tool(ctx, "update_event", {"uid": item["uid"], "summary": "x"})
        assert "phone" in str(e.value)
        u = await db.get(User, user.id)
        p = Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"})
        out = await context._calendar(db, p, [], "")
        await db.rollback()
    briefs = [b for d in out["busy"].values() for b in d] + out["upcoming"]
    titled = [b for b in briefs if b["summary"] == "Planning review"]
    assert titled and all(b["location"] == "Hall B" and "phone" in b["read_only"] for b in titled)
    assert {b["summary"] for b in briefs} == {"Planning review", "Busy (Second)"}
