# ACT-SURFACES: api assistant
"""Recordings matched to calendar events (recording.link, recording.unlink)
against the dev Radicale: overlap windows, a DST change day, one occurrence of a repeating event,
two equal candidates (no automatic link), a confirmed link that is never replaced and a dismissed
suggestion that never returns; the event detail's Recording section reads the links."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.calendar import _connect
from helpers.speakr_matching import _event
from speakr_fake import connect, speakr  # noqa: F401
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.integrations.speakr.matching import match_connector
from mailr.integrations.speakr.sync import sync_connector
from mailr.models import RecordingLink

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


def _next_fall_back() -> date:
    """The next US fall-back Sunday (the first Sunday of November) after today."""
    today = datetime.now(UTC).date()
    year = today.year
    while True:
        nov1 = date(year, 11, 1)
        sunday = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
        if sunday > today:
            return sunday
        year += 1


async def _links(cid, rid=None):
    async with sessionmaker()() as db:
        q = select(RecordingLink).where(RecordingLink.connector_id == cid)
        if rid is not None:
            q = q.where(RecordingLink.recording_id == rid)
        return list((await db.execute(q.order_by(RecordingLink.id))).scalars())


async def test_recording_link_suggestions_by_time_title_and_people(client, user, speakr):
    await _connect(client)
    t = (datetime.now(UTC) - timedelta(days=3)).replace(hour=15, minute=0, second=0, microsecond=0)
    budget = await _event(client, "Budget review", t, attendees=["evan@guest.example", {"email": "dana@guest.example",
                                                                                        "name": "Dana"}])
    speakr.add(1, "Budget review", meeting_date=t + timedelta(minutes=2), duration=50 * 60, participants="Dana, Evan")
    speakr.add(2, "Hallway chat", meeting_date=t + timedelta(minutes=65), duration=20 * 60)  # just after it
    speakr.add(3, "Far away", meeting_date=t + timedelta(hours=3), duration=600)
    conn = await connect(client, speakr)
    links = await _links(conn["id"])
    assert [(lk.recording_id, lk.target, lk.state, lk.source) for lk in links] == \
        [(1, budget["uid"], "suggested", "auto")]
    assert links[0].occurrence == budget["occurrence"] and links[0].score > 0.8

    # The event detail's Recording section, then confirm (recording.link).
    sec = (await client.get("/api/v1/speakr/events/recordings", params={"uid": budget["uid"]})).json()
    assert sec["connected"] and [x["recording_id"] for x in sec["items"]] == [1]
    assert sec["items"][0]["link"]["state"] == "suggested" and "Ship the budget" in sec["items"][0]["summary_excerpt"]
    r = await client.post("/api/v1/speakr/links", json={"connector_id": conn["id"], "recording_id": 1, "kind": "event",
                                                        "target": budget["uid"], "occurrence": budget["occurrence"]})
    assert r.status_code == 200 and r.json()["state"] == "confirmed"
    # A confirmed link is never replaced: a better-titled twin does not move it.
    await _event(client, "Budget review", t, attendees=["evan@guest.example", "dana@guest.example"])
    await match_connector(conn["id"])
    assert [(lk.target, lk.state) for lk in await _links(conn["id"], 1)] == [(budget["uid"], "confirmed")]


async def test_recording_link_dst_day_and_one_occurrence_of_a_series(client, user, speakr):
    await _connect(client)
    # The next first Sunday of November: US clocks go back at 02:00. 09:00 in Chicago is then
    # 15:00 UTC (CST).
    day = _next_fall_back()
    dst = await _event(client, "Advising", datetime(day.year, day.month, day.day, 9, 0, tzinfo=ZoneInfo("America/Chicago")),
                       minutes=30, tz="America/Chicago")
    assert dst["start"].endswith("-06:00") or dst["start"].startswith(f"{day.isoformat()}T15:00")
    speakr.add(10, "Advising", meeting_date=datetime(day.year, day.month, day.day, 15, 2, tzinfo=UTC), duration=20 * 60)
    first = (datetime.now(UTC) - timedelta(days=28)).replace(hour=16, minute=0, second=0, microsecond=0)
    series = await _event(client, "Lab meeting", first, recurrence="FREQ=WEEKLY;COUNT=5")
    third = first + timedelta(days=14)
    speakr.add(11, "Lab meeting", meeting_date=third + timedelta(minutes=1), duration=55 * 60)
    conn = await connect(client, speakr)
    by_rec = {lk.recording_id: lk for lk in await _links(conn["id"])}
    assert by_rec[10].target == dst["uid"]
    assert by_rec[11].target == series["uid"] and by_rec[11].occurrence == third.isoformat()
    sec = (await client.get("/api/v1/speakr/events/recordings",
                            params={"uid": series["uid"], "occurrence": (first + timedelta(days=7)).isoformat()})).json()
    assert sec["items"] == []  # another occurrence of the series has no recording


async def test_recording_link_two_candidates_and_dismissed_never_returns(client, user, speakr):
    await _connect(client)
    t = (datetime.now(UTC) - timedelta(days=2)).replace(hour=13, minute=0, second=0, microsecond=0)
    await _event(client, "Planning A", t)
    await _event(client, "Planning B", t)
    speakr.add(20, "Standup", meeting_date=t, duration=30 * 60)
    t2 = t + timedelta(hours=5)
    retro = await _event(client, "Retro", t2)
    speakr.add(21, "Retro", meeting_date=t2, duration=40 * 60)
    conn = await connect(client, speakr)
    assert [lk.recording_id for lk in await _links(conn["id"])] == [21]  # two equal candidates: no link for 20
    rec = (await client.get(f"/api/v1/speakr/recordings/{conn['id']}/20", params={"include": "links"})).json()
    assert sorted(c["summary"] for c in rec["candidates"]) == ["Planning A", "Planning B"]

    link = (await _links(conn["id"], 21))[0]
    r = await client.delete(f"/api/v1/speakr/links/{link.id}")  # recording.unlink: dismiss the suggestion
    assert r.status_code == 200 and r.json()["state"] == "dismissed"
    await sync_connector(conn["id"])
    assert [(lk.target, lk.state) for lk in await _links(conn["id"], 21)] == [(retro["uid"], "dismissed")]
    sec = (await client.get("/api/v1/speakr/events/recordings", params={"uid": retro["uid"]})).json()
    assert sec["items"] == []
    # The user can still link it by hand.
    r = await client.post("/api/v1/speakr/links", json={"recording_id": 21, "kind": "event", "target": retro["uid"]})
    assert r.json()["state"] == "confirmed"
    r = await client.post("/api/v1/speakr/links", json={"recording_id": 21, "kind": "event", "target": "nope"})
    assert r.status_code == 404


async def test_recording_link_tool_is_gated_by_speakr_link(client, user, speakr):
    """The assistant's link_recording (recording.link): allowed by default; a rule can make it ask."""
    from helpers.speakr_tools import _ctx

    from mailr.agent.tools import run_tool
    from mailr.models import PermissionRule

    await _connect(client)
    t = (datetime.now(UTC) - timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    ev = await _event(client, "One to one", t)
    speakr.add(30, "Untitled", meeting_date=t - timedelta(hours=6), duration=600)
    conn = await connect(client, speakr)
    async with sessionmaker()() as db:
        res = await run_tool(await _ctx(db, user.id), "link_recording",
                             {"recording_id": 30, "event_uid": ev["uid"], "occurrence": ev["occurrence"]})
        assert res.data["link"]["state"] == "confirmed" and res.data["link"]["source"] == "assistant"
        db.add(PermissionRule(user_id=user.id, subject="assistant", action="speakr.link", effect="ask", constraints={}))
        await db.commit()
        res = await run_tool(await _ctx(db, user.id), "link_recording", {"recording_id": 30, "task_uid": "nope"})
        assert res.data["status"] == "pending_approval" and res.pending["action"] == "speakr.link"
        pid = res.data["approval_id"]
        task = (await client.post("/api/v1/tasks", json={"summary": "Follow up with Evan"})).json()
        res = await run_tool(await _ctx(db, user.id), "link_recording", {"recording_id": 30, "task_uid": task["uid"]})
        pid2 = res.data["approval_id"]
    assert [lk.kind for lk in await _links(conn["id"], 30)] == ["event"]
    from mailr.core import permissions
    from mailr.models import PendingAction, User

    for p_id in (pid, pid2):
        async with sessionmaker()() as db:
            await permissions.decide(db, await db.get(User, user.id), p_id, "approve", "once")
            await db.commit()
        await permissions.execute_job({"pending_id": p_id}, None)
    async with sessionmaker()() as db:
        assert (await db.get(PendingAction, pid)).status == "failed"  # the task does not exist
        assert (await db.get(PendingAction, pid2)).status == "executed"
    assert [(lk.kind, lk.state) for lk in await _links(conn["id"], 30)] == [("event", "confirmed"), ("task", "confirmed")]


async def test_recording_link_uses_confirmed_speakers_and_people(client, user, speakr):
    """recording.link: participants whose names share nothing with the guests' still count as
    guests when a confirmed speaker mapping or a unique correspondent in People gives their address."""
    from mailr.models import Person

    await _connect(client)
    t = (datetime.now(UTC) - timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    team = await _event(client, "Planning", t, attendees=["ds@guest.example", "el@guest.example"])
    await _event(client, "Planning room", t)
    speakr.add(40, "Planning", meeting_date=t, duration=45 * 60, participants="D. Smith, E. Lee")
    conn = await connect(client, speakr)
    assert await _links(conn["id"], 40) == []  # names alone: too close to call

    r = await client.post(f"/api/v1/speakr/speakers/{conn['id']}/person",
                          json={"key": "name:d. smith", "speaker_name": "D. Smith", "email": "ds@guest.example"})
    assert r.status_code == 200
    async with sessionmaker()() as db:
        db.add_all([Person(user_id=user.id, email="el@guest.example", name="E. Lee"),
                    Person(user_id=user.id, email="other@x.org", name="Someone Else")])
        await db.commit()
    await match_connector(conn["id"])
    links = await _links(conn["id"], 40)
    assert [(lk.target, lk.state) for lk in links] == [(team["uid"], "suggested")]

    from mailr.integrations.speakr.matching import participant_emails
    from mailr.models import SpeakrRecording

    async with sessionmaker()() as db:
        row = (await db.execute(select(SpeakrRecording).where(SpeakrRecording.connector_id == conn["id"],
                                                               SpeakrRecording.recording_id == 40))).scalar_one()
        assert await participant_emails(db, user.id, row) == {"ds@guest.example", "el@guest.example"}
        # Two correspondents with the same full name give no address: the mapping alone stays.
        db.add(Person(user_id=user.id, email="el2@guest.example", name="E. Lee"))
        await db.commit()
        assert await participant_emails(db, user.id, row) == {"ds@guest.example"}
