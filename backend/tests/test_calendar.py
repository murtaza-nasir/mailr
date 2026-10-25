"""Calendar, tasks, and invitations against the dev Radicale (container mailr-dev-radicale, :15232)."""

import uuid
from datetime import UTC, datetime, timedelta, timezone

import caldav
import pytest
from dev_services import RADICALE
from dev_services import radicale_up as _radicale_up  # other modules import it from here
from helpers.calendar import _connect, _invite, _make_calendar, _token_client  # noqa: F401

from mailr.calendar import itip
from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.store import user_store
from mailr.db import sessionmaker
from mailr.models import Connector, Identity, User

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


async def test_agenda_and_tasks(client, user):
    await _connect(client)
    start = (datetime.now(UTC) + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)
    r = await client.post("/api/v1/calendar/events", json={"summary": "Budget review", "start": start.isoformat(),
                                                           "end": (start + timedelta(hours=1)).isoformat(),
                                                           "location": "Room 4"})
    assert r.status_code == 200, r.text
    day = start.date().isoformat()
    ag = (await client.get("/api/v1/calendar/agenda", params={"from": day, "to": (start + timedelta(days=1)).date().isoformat()})).json()
    assert ag["connected"] and [e["summary"] for e in ag["events"]] == ["Budget review"]
    assert ag["events"][0]["location"] == "Room 4"

    t = (await client.post("/api/v1/tasks", json={"summary": "Send numbers", "due": day})).json()
    open_tasks = (await client.get("/api/v1/tasks")).json()["items"]
    assert [x["summary"] for x in open_tasks] == ["Send numbers"]
    done = await client.patch(f"/api/v1/tasks/{t['uid']}", json={"completed": True})
    assert done.status_code == 200 and done.json()["completed"]
    assert (await client.get("/api/v1/tasks")).json()["items"] == []
    assert len((await client.get("/api/v1/tasks", params={"status": "completed"})).json()["items"]) == 1


async def test_invitation_parse_reply_and_calendar(client, user):
    await _connect(client)
    async with sessionmaker()() as db:
        c = Connector(user_id=user.id, kind="imap", name="out", mode="mirror", enabled=False, status="paused", config={})
        db.add(c)
        await db.flush()
        me = f"me-{c.id}@example.com"
        db.add(Identity(user_id=user.id, connector_id=c.id, email=me, name="Me", is_default=True, receives=[]))
        await db.commit()
    uid = f"inv-{uuid.uuid4().hex[:10]}"
    start = (datetime.now(UTC) + timedelta(days=3)).replace(hour=14, minute=0, second=0, microsecond=0)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        res = await ingest_raw(db, await user_store(db, u), _invite(uid, me, start), IngestOptions())
        await db.commit()
        mid = res.message.id

    inv = (await client.get(f"/api/v1/messages/{mid}/invite")).json()
    assert inv["method"] == "REQUEST" and inv["uid"] == uid and inv["my_status"] == "NEEDS-ACTION"
    assert inv["organizer"]["email"] == "alice@acme.example" and inv["in_calendar"] is False

    r = await client.post("/api/v1/calendar/invite-reply", json={"message_id": mid, "response": "accepted"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["calendar"] == "added" and out["outbox"]["status"] == "queued"
    # Undo the queued reply so nothing is sent from the test.
    assert (await client.post(f"/api/v1/outbox/{out['outbox']['id']}/cancel")).status_code == 200
    inv = (await client.get(f"/api/v1/messages/{mid}/invite")).json()
    assert inv["in_calendar"] is True

    # The REPLY carries only my attendee line with the new status.
    from mailr.calendar.itip import find_calendar_part

    reply = itip.build_reply(find_calendar_part(_invite(uid, me, start)), me, "tentative", "Me")
    text = reply.decode()
    assert "METHOD:REPLY" in text and f"UID:{uid}" in text and "PARTSTAT=TENTATIVE" in text
    assert "alice@acme.example" in text.split("ORGANIZER")[1].split("\n")[0]
    assert text.count("ATTENDEE") == 1


async def test_event_from_message_heuristic(client, user):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        raw = (b"From: Bob <bob@acme.example>\r\nTo: me@example.com\r\nSubject: Re: Coffee chat\r\n"
               b"Message-ID: <cc1@acme.example>\r\nDate: Mon, 21 Sep 2026 10:00:00 +0000\r\n\r\n"
               b"How about Thursday at 3pm at the Roastery?\r\n")
        res = await ingest_raw(db, await user_store(db, u), raw, IngestOptions())
        await db.commit()
    r = await client.post("/api/v1/calendar/from-message", json={"message_id": res.message.id, "kind": "event"})
    assert r.status_code == 200, r.text
    d = r.json()["draft"]
    assert d["title"] == "Coffee chat"
    if r.json()["source"] == "heuristic":
        assert d["start"].startswith("2026-09-24T15:00")


async def test_collections_components_and_defaults(client, user):
    username = f"u{uuid.uuid4().hex[:8]}"
    pr = caldav.DAVClient(url=RADICALE, username=username, password="x").principal()
    pr.make_calendar(name="Mixed", supported_calendar_component_set=["VEVENT", "VTODO"])
    pr.make_calendar(name="Todo only", supported_calendar_component_set=["VTODO"])
    pr.make_calendar(name="Events only", supported_calendar_component_set=["VEVENT"])
    r = await client.post("/api/v1/calendar/connect", json={"url": RADICALE, "username": username, "password": "x"})
    assert r.status_code == 200, r.text
    cols = {c["name"]: c for c in (await client.get("/api/v1/calendar/calendars", params={"refresh": 1})).json()}
    assert cols["Mixed"]["events"] and cols["Mixed"]["tasks"]
    assert cols["Todo only"]["tasks"] and not cols["Todo only"]["events"]
    assert cols["Events only"]["events"] and not cols["Events only"]["tasks"]

    r = await client.put("/api/v1/calendar/defaults", json={"calendar": cols["Events only"]["url"],
                                                            "task_list": cols["Todo only"]["url"]})
    assert r.status_code == 200, r.text
    d = (await client.get("/api/v1/calendar/defaults")).json()
    assert d["calendar"] == cols["Events only"]["url"] and d["task_list"] == cols["Todo only"]["url"]
    t = (await client.post("/api/v1/tasks", json={"summary": "Lands in the default list"})).json()
    assert t["list"] == cols["Todo only"]["url"]
    # Move between lists, then delete.
    moved = (await client.patch(f"/api/v1/tasks/{t['uid']}", json={"list": cols["Mixed"]["url"]})).json()
    assert moved["list"] == cols["Mixed"]["url"]
    found = (await client.get("/api/v1/tasks", params={"q": "default list"})).json()["items"]
    assert [x["uid"] for x in found] == [t["uid"]]
    assert (await client.delete(f"/api/v1/tasks/{t['uid']}")).status_code == 200
    assert (await client.get("/api/v1/tasks")).json()["items"] == []
    # A misreported collection can be overridden by the user.
    r = await client.put("/api/v1/calendar/defaults", json={"calendar": cols["Events only"]["url"],
                                                            "kinds": {cols["Events only"]["url"]: {"tasks": True}}})
    cols2 = {c["name"]: c for c in (await client.get("/api/v1/calendar/calendars")).json()}
    assert cols2["Events only"]["tasks"]


async def test_recurring_event_scopes(client, user):
    await _connect(client)
    start = (datetime.now(UTC) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    ev = (await client.post("/api/v1/calendar/events", json={
        "summary": "Standup", "start": start.isoformat(), "end": (start + timedelta(minutes=15)).isoformat(),
        "recurrence": "FREQ=DAILY;COUNT=5"})).json()
    uid = ev["uid"]
    rng = {"from": start.date().isoformat(), "to": (start + timedelta(days=6)).date().isoformat()}

    async def titles():
        return [e["summary"] for e in (await client.get("/api/v1/calendar/agenda", params=rng)).json()["events"]]

    assert await titles() == ["Standup"] * 5
    second = (start + timedelta(days=1)).isoformat()
    r = await client.patch(f"/api/v1/calendar/events/{uid}", json={"summary": "Standup (moved)",
                                                                  "scope": "this", "occurrence": second})
    assert r.status_code == 200, r.text
    assert sorted(await titles()) == sorted(["Standup"] * 4 + ["Standup (moved)"])
    third = (start + timedelta(days=2)).isoformat()
    r = await client.delete(f"/api/v1/calendar/events/{uid}", params={"scope": "this", "occurrence": third})
    assert r.status_code == 200, r.text
    assert len(await titles()) == 4
    fourth = (start + timedelta(days=3)).isoformat()
    r = await client.delete(f"/api/v1/calendar/events/{uid}", params={"scope": "following", "occurrence": fourth})
    assert r.status_code == 200, r.text
    assert len(await titles()) == 2
    r = await client.delete(f"/api/v1/calendar/events/{uid}")
    assert r.status_code == 200
    assert await titles() == []
    found = (await client.get("/api/v1/calendar/events/search", params={"q": "standup"})).json()["items"]
    assert found == []


async def test_agent_calendar_gating(client, user):
    await _connect(client)
    agent = await _token_client(client, ["read", "calendar"])
    start = (datetime.now(UTC) + timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
    # calendar.create defaults to ask: the agent gets 202 and nothing is created yet.
    r = await agent.post("/api/v1/calendar/events", json={"summary": "Agent meeting", "start": start.isoformat()})
    assert r.status_code == 202, r.text
    aid = r.json()["approval"]["id"]
    rng = {"from": start.date().isoformat(), "to": (start + timedelta(days=1)).date().isoformat()}
    assert (await client.get("/api/v1/calendar/agenda", params=rng)).json()["events"] == []
    # Approve with "always" and the derived constraint (this calendar): executed by the worker.
    r = await client.post(f"/api/v1/approvals/{aid}/decide", json={"decision": "approve", "scope": "always",
                                                                   "constraint": "derived"})
    assert r.status_code == 200, r.text
    from mailr.core.permissions import execute_job

    await execute_job({"pending_id": aid}, None)
    await execute_job({"pending_id": aid}, None)  # exactly once: the second run is a no-op
    st = (await client.get(f"/api/v1/approvals/{aid}")).json()
    assert st["status"] == "executed", st
    assert [e["summary"] for e in (await client.get("/api/v1/calendar/agenda", params=rng)).json()["events"]] == ["Agent meeting"]
    # The "always" grant covers the next create in the same calendar: runs immediately.
    r = await agent.post("/api/v1/calendar/events", json={"summary": "Second", "start": (start + timedelta(hours=2)).isoformat()})
    assert r.status_code == 200, r.text
    # A token's task text asks by default; a user rule allowing
    # tasks.create for every agent lets it through. tasks.delete still asks.
    t = await agent.post("/api/v1/tasks", json={"summary": "Agent task"})
    assert t.status_code == 202, t.text
    r = await client.post("/api/v1/permissions", json={"subject": "*", "action": "tasks.create", "effect": "allow"})
    assert r.status_code in (200, 201), r.text
    t = await agent.post("/api/v1/tasks", json={"summary": "Agent task"})
    assert t.status_code == 200, t.text
    d = await agent.delete(f"/api/v1/tasks/{t.json()['uid']}")
    assert d.status_code == 202
    await agent.aclose()


async def test_local_times_with_an_offset_keep_their_instant(client, user):
    """Clients send local times with a bare offset (-05:00). Stored as TZID="UTC-05:00" without a
    VTIMEZONE, they read back as UTC and the event moved by the offset on every edit."""
    await _connect(client)
    start = (datetime.now(UTC) + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
    ev = (await client.post("/api/v1/calendar/events", json={"summary": "Offset", "start": start.isoformat(),
                                                             "end": (start + timedelta(hours=1)).isoformat()})).json()
    cdt = timezone(timedelta(hours=-5))
    moved = (start + timedelta(minutes=30)).astimezone(cdt)
    r = await client.patch(f"/api/v1/calendar/events/{ev['uid']}", json={"start": moved.isoformat(),
                                                                         "end": (moved + timedelta(hours=1)).isoformat()})
    assert r.status_code == 200, r.text
    day = start.date().isoformat()
    ag = (await client.get("/api/v1/calendar/agenda", params={"from": day, "to": (start + timedelta(days=1)).date().isoformat()})).json()
    got = next(e for e in ag["events"] if e["summary"] == "Offset")
    assert datetime.fromisoformat(got["start"]) == moved

    t = (await client.post("/api/v1/tasks", json={"summary": "Offset task", "due": moved.isoformat()})).json()
    later = moved + timedelta(hours=2)
    await client.patch(f"/api/v1/tasks/{t['uid']}", json={"due": later.isoformat()})
    got = next(x for x in (await client.get("/api/v1/tasks")).json()["items"] if x["summary"] == "Offset task")
    assert datetime.fromisoformat(got["due"]) == later
