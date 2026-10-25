# ACT-SURFACES: api assistant
"""Calendar/task fields the Android app relies on: client-chosen UIDs, reminders (VALARM),
RRULE round trips, subtasks (RELATED-TO), guests with my answer, and RSVP by event UID.
Runs against the dev Radicale (container mailr-dev-radicale, :15232)."""

import uuid
from datetime import UTC, datetime, timedelta

import caldav
import httpx
import pytest

from mailr.db import sessionmaker
from mailr.models import Connector, Identity

RADICALE = "http://localhost:15232/"


def _radicale_up() -> bool:
    try:
        return httpx.get(RADICALE, timeout=2).status_code < 500
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


async def _connect(client) -> tuple[str, caldav.Calendar]:
    username = f"u{uuid.uuid4().hex[:8]}"
    cal = caldav.DAVClient(url=RADICALE, username=username, password="x").principal().make_calendar(
        name="Personal", supported_calendar_component_set=["VEVENT", "VTODO"])
    r = await client.post("/api/v1/calendar/connect", json={"url": RADICALE, "username": username, "password": "x"})
    assert r.status_code == 200, r.text
    return username, cal


def _day(days: int, hour: int) -> datetime:
    return (datetime.now(UTC) + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


async def _agenda(client, start: datetime, days: int = 1) -> list[dict]:
    r = await client.get("/api/v1/calendar/agenda", params={
        "from": start.date().isoformat(), "to": (start + timedelta(days=days)).date().isoformat()})
    assert r.status_code == 200, r.text
    return r.json()["events"]


async def test_event_uid_reminders_and_rrule(client, user):
    await _connect(client)
    start = _day(2, 15)
    uid = f"{uuid.uuid4()}@mailr-android"
    r = await client.post("/api/v1/calendar/events", json={
        "uid": uid, "summary": "Standup", "start": start.isoformat(), "end": (start + timedelta(minutes=30)).isoformat(),
        "reminders": [10, 1440, 10], "recurrence": "FREQ=DAILY;COUNT=3"})
    assert r.status_code == 200, r.text
    assert r.json()["uid"] == uid and r.json()["reminders"] == [10, 1440]

    evs = await _agenda(client, start, days=3)
    assert [e["uid"] for e in evs] == [uid] * 3
    assert all(e["reminders"] == [10, 1440] for e in evs)

    got = (await client.get(f"/api/v1/calendar/events/{uid}")).json()
    assert got["rrule"] == "FREQ=DAILY;COUNT=3" and got["reminders"] == [10, 1440]
    assert got["my_status"] is None and got["is_organizer"] is False

    # Change the reminders and drop the recurrence.
    r = await client.patch(f"/api/v1/calendar/events/{uid}", json={"reminders": [5], "recurrence": ""})
    assert r.status_code == 200, r.text
    got = (await client.get(f"/api/v1/calendar/events/{uid}")).json()
    assert got["rrule"] is None and got["reminders"] == [5]
    assert len(await _agenda(client, start, days=3)) == 1

    bad = await client.post("/api/v1/calendar/events", json={"summary": "x", "start": start.isoformat(),
                                                              "recurrence": "FREQ=SOMETIMES"})
    assert bad.status_code == 400
    assert (await client.post("/api/v1/calendar/events", json={
        "summary": "x", "start": start.isoformat(), "uid": "bad uid with spaces"})).status_code == 422
    assert (await client.get("/api/v1/calendar/events/nope-nope-nope")).status_code == 404


async def test_all_day_event_and_reminders_cleared(client, user):
    await _connect(client)
    day = _day(4, 0).date()
    r = await client.post("/api/v1/calendar/events", json={
        "summary": "Holiday", "start": day.isoformat(), "end": (day + timedelta(days=1)).isoformat(), "reminders": [-540, 900]})
    assert r.status_code == 200, r.text
    ev = r.json()
    # 9 am on the day, and 9 am the day before.
    assert ev["all_day"] is True and ev["reminders"] == [-540, 900]
    r = await client.patch(f"/api/v1/calendar/events/{ev['uid']}", json={"reminders": []})
    assert r.status_code == 200 and r.json()["reminders"] == []


async def test_subtasks_reminders_and_uid(client, user):
    await _connect(client)
    due = _day(1, 17).isoformat()
    parent_uid = f"{uuid.uuid4()}@mailr-android"
    p = await client.post("/api/v1/tasks", json={"uid": parent_uid, "summary": "Trip", "due": due,
                                                 "priority": 1, "reminders": [60]})
    assert p.status_code == 200, p.text
    assert p.json()["uid"] == parent_uid and p.json()["priority"] == 1 and p.json()["reminders"] == [60]

    c = await client.post("/api/v1/tasks", json={"summary": "Book hotel", "parent": parent_uid})
    assert c.status_code == 200, c.text
    child = c.json()
    assert child["parent"] == parent_uid and child["reminders"] == []

    items = {t["uid"]: t for t in (await client.get("/api/v1/tasks")).json()["items"]}
    assert items[child["uid"]]["parent"] == parent_uid and items[parent_uid]["parent"] is None

    r = await client.patch(f"/api/v1/tasks/{child['uid']}", json={"parent": ""})
    assert r.status_code == 200 and r.json()["parent"] is None
    r = await client.patch(f"/api/v1/tasks/{parent_uid}", json={"reminders": [0, 15]})
    assert r.status_code == 200 and r.json()["reminders"] == [0, 15]
    # Without a due date there is nothing to remind about.
    r = await client.patch(f"/api/v1/tasks/{parent_uid}", json={"clear_due": True, "reminders": [15]})
    assert r.status_code == 200 and r.json()["due"] is None and r.json()["reminders"] == []


async def test_rsvp_by_event_uid(client, user):
    """event.rsvp: answer by event uid."""
    _, cal = await _connect(client)
    async with sessionmaker()() as db:
        conn = Connector(user_id=user.id, kind="imap", name="out", mode="mirror", enabled=False, status="paused", config={})
        db.add(conn)
        await db.flush()
        me = f"me-{conn.id}@example.com"
        db.add(Identity(user_id=user.id, connector_id=conn.id, email=me, name="Me", is_default=True, receives=[]))
        await db.commit()
    uid = f"inv-{uuid.uuid4().hex[:10]}"
    start = _day(3, 14)
    cal.save_event(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nDTSTAMP:20260920T100000Z\r\nDTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}\r\n"
        f"DTEND:{(start + timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}\r\nSUMMARY:Project sync\r\n"
        "ORGANIZER;CN=Alice:mailto:alice@acme.example\r\n"
        f"ATTENDEE;CN=Me;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{me}\r\n"
        "ATTENDEE;PARTSTAT=ACCEPTED:mailto:alice@acme.example\r\n"
        "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:x\r\nTRIGGER:-PT15M\r\nEND:VALARM\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n")

    [ev] = await _agenda(client, start)
    assert ev["my_status"] == "NEEDS-ACTION" and ev["organizer"] == "alice@acme.example"
    assert ev["organizer_name"] == "Alice" and ev["reminders"] == [15]
    assert {a["email"]: a["partstat"] for a in ev["attendee_list"]} == {me: "NEEDS-ACTION", "alice@acme.example": "ACCEPTED"}

    r = await client.post(f"/api/v1/calendar/events/{uid}/rsvp", json={"response": "tentative"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["calendar"] == "updated" and out["outbox"]["status"] == "queued"
    assert (await client.post(f"/api/v1/outbox/{out['outbox']['id']}/cancel")).status_code == 200
    assert (await client.get(f"/api/v1/calendar/events/{uid}")).json()["my_status"] == "TENTATIVE"

    # No reply mail when asked not to send.
    r = await client.post(f"/api/v1/calendar/events/{uid}/rsvp", json={"response": "accepted", "send": False})
    assert r.status_code == 200 and r.json()["outbox"] is None
    assert (await client.get(f"/api/v1/calendar/events/{uid}")).json()["my_status"] == "ACCEPTED"


async def test_rsvp_needs_guest(client, user):
    await _connect(client)
    start = _day(2, 9)
    ev = (await client.post("/api/v1/calendar/events", json={"summary": "Solo", "start": start.isoformat()})).json()
    r = await client.post(f"/api/v1/calendar/events/{ev['uid']}/rsvp", json={"response": "accepted"})
    assert r.status_code == 409


async def test_assistant_rsvp_by_event_uid(client, user):
    """event.rsvp on the assistant surface: the rsvp tool answers an invitation already in the
    calendar by its uid; a reply to an organizer never written to waits for approval."""
    from mailr.core import permissions
    from mailr.models import User

    _, cal = await _connect(client)
    async with sessionmaker()() as db:
        conn = Connector(user_id=user.id, kind="imap", name="out", mode="mirror", enabled=False, status="paused", config={})
        db.add(conn)
        await db.flush()
        me = f"me-{conn.id}@example.com"
        db.add(Identity(user_id=user.id, connector_id=conn.id, email=me, name="Me", is_default=True, receives=[]))
        await db.commit()
    uid = f"inv-{uuid.uuid4().hex[:10]}"
    start = _day(4, 10)
    cal.save_event(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nDTSTAMP:20260920T100000Z\r\nDTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}\r\n"
        f"DTEND:{(start + timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}\r\nSUMMARY:Budget review\r\n"
        "ORGANIZER;CN=Alice:mailto:alice@acme.example\r\n"
        f"ATTENDEE;CN=Me;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{me}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")

    r = await client.post("/api/v1/agent/tools/rsvp", json={"event_uid": uid, "response": "tentative"})
    assert r.status_code == 200, r.text
    out = r.json()["result"]
    assert out["calendar"] == "updated" and out["outbox"]["status"] == "queued", out
    assert (await client.post(f"/api/v1/outbox/{out['outbox']['id']}/cancel")).status_code == 200
    assert (await client.get(f"/api/v1/calendar/events/{uid}")).json()["my_status"] == "TENTATIVE"

    # What an approved card runs when the assistant asked (calendar.rsvp with an event uid).
    permissions._load_executors()
    async with sessionmaker()() as db:
        res = await permissions._EXECUTORS["calendar.rsvp"](db, await db.get(User, user.id),
                                                            {"event_uid": uid, "response": "accepted", "send": False})
        await db.commit()
    assert res["calendar"] == "updated" and res["outbox"] is None
    assert (await client.get(f"/api/v1/calendar/events/{uid}")).json()["my_status"] == "ACCEPTED"

    r = await client.post("/api/v1/agent/tools/rsvp", json={"event_uid": "nope", "response": "accepted"})
    assert "not found" in r.text
