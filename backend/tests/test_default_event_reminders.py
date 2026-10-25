"""Default reminders are written into new events, against the dev Radicale.

A create that leaves out `reminders` gets the calendar's `default_reminders`, else the user's
default of /reminders/settings (timed: `default_timed_event`; all-day: `default_all_day_event`,
minutes before the day). They become VALARMs in the event, so other calendar apps alert too. An
explicit list wins; an explicit [] means no reminder (and marks the event silent,
test_no_reminder_marker.py). Covered for the REST create
(POST /calendar/events) and the agent tool create_event, whose result says what was applied, and
for get_calendar_defaults and the per-user create_event schema.
"""

from datetime import UTC, datetime, timedelta

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.agent_calendar_v2 import _allow, _ctx
from helpers.calendar import _connect
from sqlalchemy import select

from mailr.agent.tools import TOOLS, run_tool
from mailr.calendar import planner
from mailr.db import sessionmaker
from mailr.models import CalObject, User

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")

API = "/api/v1"


def _start(days=3, hour=15):
    return (datetime.now(UTC) + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


async def _settings(client):
    r = await client.put(f"{API}/reminders/settings", json={"default_timed_event": 1440, "default_all_day_event": 900})
    assert r.status_code == 200, r.text


async def _ics(uid: str) -> str:
    async with sessionmaker()() as db:
        obj = (await db.execute(select(CalObject).where(CalObject.uid == uid))).scalars().first()
        return obj.ics


def _triggers(ics: str) -> list[str]:
    return [line.strip() for line in ics.splitlines() if line.startswith("TRIGGER")]


async def test_rest_create_writes_defaults(client, user):
    await _connect(client)
    await _settings(client)
    start = _start()
    timed = await client.post(f"{API}/calendar/events", json={"summary": "Proposal discussion",
                                                              "start": start.isoformat(),
                                                              "end": (start + timedelta(hours=1)).isoformat()})
    assert timed.status_code == 200, timed.text
    assert timed.json()["reminders"] == [1440]
    assert _triggers(await _ics(timed.json()["uid"])) == ["TRIGGER:-P1D"]

    day = (start + timedelta(days=1)).date().isoformat()
    allday = (await client.post(f"{API}/calendar/events", json={"summary": "Offsite", "start": day})).json()
    assert allday["all_day"] and allday["reminders"] == [900]
    assert _triggers(await _ics(allday["uid"])) == ["TRIGGER:-PT15H"]

    none = (await client.post(f"{API}/calendar/events", json={"summary": "Quiet", "start": start.isoformat(),
                                                              "reminders": []})).json()
    assert none["reminders"] == [] and "VALARM" not in await _ics(none["uid"])
    own = (await client.post(f"{API}/calendar/events", json={"summary": "Own", "start": start.isoformat(),
                                                             "reminders": [15]})).json()
    assert own["reminders"] == [15]

    # A default of none writes nothing.
    await client.put(f"{API}/reminders/settings", json={"default_timed_event": None})
    bare = (await client.post(f"{API}/calendar/events", json={"summary": "Bare", "start": start.isoformat()})).json()
    assert bare["reminders"] == [] and "VALARM" not in await _ics(bare["uid"])


async def test_calendar_default_beats_user_default(client, user):
    await _connect(client)
    await _settings(client)
    team = (await client.post(f"{API}/calendar/collections", json={
        "name": "Team", "kind": "events", "default_reminders": {"timed": [30], "all_day": [-540]}})).json()
    flat = (await client.post(f"{API}/calendar/collections", json={
        "name": "Flat", "kind": "events", "default_reminders": [5, 60]})).json()
    start = _start()
    ev = (await client.post(f"{API}/calendar/events", json={"summary": "Sync", "start": start.isoformat(),
                                                            "calendar": team["url"]})).json()
    assert ev["reminders"] == [30]
    day = (await client.post(f"{API}/calendar/events", json={"summary": "Day", "start": start.date().isoformat(),
                                                             "calendar": team["url"]})).json()
    assert day["reminders"] == [-540]
    two = (await client.post(f"{API}/calendar/events", json={"summary": "Two", "start": start.isoformat(),
                                                             "calendar": flat["url"]})).json()
    assert sorted(two["reminders"]) == [5, 60]


async def test_tool_create_reports_reminders_source(client, user):
    await _connect(client)
    await _settings(client)
    await _allow(user.id, "calendar.create")
    start = _start()
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = (await run_tool(ctx, "create_event", {"summary": "Proposal Discussion Session",
                                                    "start": start.isoformat()})).data
        assert res["reminders"] == [1440] and res["reminders_source"] == "default"
        assert res["reminders_text"] == "1 day before"
        assert _triggers(await _ics(res["uid"])) == ["TRIGGER:-P1D"]

        day = (await run_tool(ctx, "create_event", {"summary": "Away", "start": start.date().isoformat()})).data
        assert day["reminders"] == [900] and day["reminders_text"] == "1 day before at 9:00"

        none = (await run_tool(ctx, "create_event", {"summary": "No alarm", "start": start.isoformat(),
                                                     "reminders": []})).data
        assert none["reminders"] == [] and none["reminders_source"] == "explicit"
        assert "VALARM" not in await _ics(none["uid"])

        own = (await run_tool(ctx, "create_event", {"summary": "Own", "start": start.isoformat(),
                                                    "reminders": [10, 60]})).data
        assert own["reminders"] == [10, 60] and own["reminders_source"] == "explicit"

    team = (await client.post(f"{API}/calendar/collections", json={
        "name": "Team", "kind": "events", "default_reminders": {"timed": [30]}})).json()
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = (await run_tool(ctx, "create_event", {"summary": "Team sync", "start": start.isoformat(),
                                                    "calendar": team["url"]})).data
        assert res["reminders"] == [30] and res["reminders_source"] == "calendar_default"
        # No all-day entry in the calendar's defaults: the user's default applies.
        day = (await run_tool(ctx, "create_event", {"summary": "Team day", "start": start.date().isoformat(),
                                                    "calendar": team["url"]})).data
        assert day["reminders"] == [900] and day["reminders_source"] == "default"


async def test_calendar_defaults_tool_and_schema(client, user):
    await _connect(client)
    await _settings(client)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        d = (await run_tool(ctx, "get_calendar_defaults", {})).data
        er = d["event_reminders"]
        assert er["timed"] == [1440] and er["all_day"] == [900]
        assert er["timed_text"] == "1 day before" and er["all_day_text"] == "1 day before at 9:00"
        assert "at the due time" in er["tasks"]
        assert all("default_reminders" in c for c in d["calendars"])

        u = await db.get(User, user.id)
        fn = TOOLS["create_event"].schema(u)["function"]
        assert "currently: timed events 1 day before; all-day events 1 day before at 9:00" in fn["description"]
        assert "pass [] only when the user wants no reminder" in fn["parameters"]["properties"]["reminders"]["description"]
        assert "1 day before" in fn["parameters"]["properties"]["reminders"]["description"]
        # The static schema (no user) carries no per-user text.
        assert "currently" not in TOOLS["create_event"].schema()["function"]["description"]

    listed = (await client.get(f"{API}/agent/tools")).json()["tools"]
    ce = next(t for t in listed if t["name"] == "create_event")
    assert "timed events 1 day before" in ce["description"]


def test_offset_labels():
    assert planner.offset_label(1440) == "1 day before"
    assert planner.offset_label(30) == "30 minutes before"
    assert planner.offset_label(120) == "2 hours before"
    assert planner.offset_label(0) == "at the start"
    assert planner.offset_label(900, True) == "1 day before at 9:00"
    assert planner.offset_label(-540, True) == "on the day at 9:00"
    assert planner.offset_label(2400, True) == "2 days before at 8:00"
    off = planner.event_defaults(User(settings={"reminders": {"events": False}}))
    assert off["timed"] == [] and off["timed_text"] == "none"
