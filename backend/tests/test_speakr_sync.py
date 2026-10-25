# ACT-SURFACES: api
"""The Speakr mirror sync (speakr.sync): pagination, upserts, tombstones, Retry-After on 429, the
breaker after repeated 5xx, and a 401 that marks the connector and stops polling until Sync now."""

from datetime import UTC, datetime, timedelta

import pytest
from speakr_fake import connect, speakr  # noqa: F401
from sqlalchemy import select

from mailr.core.jobs import Retry
from mailr.db import sessionmaker
from mailr.integrations.speakr.sync import due_syncs, sync_connector
from mailr.models import Connector, Job, SpeakrRecording


async def _rows(cid):
    async with sessionmaker()() as db:
        return {r.recording_id: r for r in (await db.execute(select(SpeakrRecording).where(
            SpeakrRecording.connector_id == cid))).scalars()}


async def _conn(cid):
    async with sessionmaker()() as db:
        return await db.get(Connector, cid)


async def test_speakr_sync_pages_upserts_and_tombstones(client, user, speakr, monkeypatch):
    # Three pages: 25 recordings at 10 a page (production asks for 100 a page; 250 recordings
    # took over a minute here).
    from mailr.integrations.speakr import client as speakr_client

    monkeypatch.setattr(speakr_client, "PER_PAGE", 10)
    for i in range(1, 26):
        speakr.add(i, f"Meeting {i}", participants="Ann, Bob")
    conn = await connect(client, speakr)
    rows = await _rows(conn["id"])
    assert len(rows) == 25
    assert [c for c in speakr.calls if c == "/api/v1/recordings"] == ["/api/v1/recordings"] * 3
    r = rows[7]
    assert r.title == "Meeting 7" and r.participants == ["Ann", "Bob"] and r.status == "completed"
    assert r.duration_s == 1800 and r.meeting_date.tzinfo is not None and r.tags[0]["name"] == "Work"
    c = await _conn(conn["id"])
    assert c.status == "idle" and c.status_detail == "25 recordings" and c.last_sync_at

    speakr.recordings[7]["title"] = "Renamed"
    speakr.delete(9)
    out = await sync_connector(conn["id"])
    assert out["changed"] == 1 and out["removed"] == 1 and out["added"] == 0
    rows = await _rows(conn["id"])
    assert rows[7].title == "Renamed" and rows[9].deleted_at is not None
    speakr.add(9, "Meeting 9 again")
    await sync_connector(conn["id"])
    assert (await _rows(conn["id"]))[9].deleted_at is None
    listed = (await client.get("/api/v1/speakr/recordings", params={"q": "again"})).json()["items"]
    assert [x["recording_id"] for x in listed] == [9]


async def test_speakr_sync_honours_retry_after(client, user, speakr):
    speakr.add(1)
    conn = await connect(client, speakr)
    speakr.status_for = {r"^/api/v1/recordings$": 429}
    speakr.retry_after = "7"
    with pytest.raises(Retry) as e:
        await sync_connector(conn["id"])
    assert e.value.delay == 7


async def test_speakr_sync_breaker_after_repeated_5xx(client, user, speakr):
    speakr.add(1)
    conn = await connect(client, speakr)
    speakr.status = 500
    for _ in range(3):
        assert (await sync_connector(conn["id"]))["error"] == "unavailable"
    c = await _conn(conn["id"])
    assert c.status == "error" and "not responding" in c.status_detail
    until = datetime.fromisoformat(c.state["breaker_until"])
    now = datetime.now(UTC)
    assert timedelta(minutes=14) < until - now <= timedelta(minutes=15)
    assert conn["id"] not in await due_syncs(now + timedelta(minutes=11))
    speakr.status = None
    assert conn["id"] in await due_syncs(now + timedelta(minutes=16))
    await sync_connector(conn["id"])
    c = await _conn(conn["id"])
    assert c.status == "idle" and "breaker_until" not in c.state and "fails" not in c.state


async def test_speakr_sync_401_marks_error_and_stops_polling(client, user, speakr):
    speakr.add(1)
    conn = await connect(client, speakr)
    speakr.token = "revoked"
    assert (await sync_connector(conn["id"]))["error"] == "auth"
    c = await _conn(conn["id"])
    assert c.status == "error" and "Token expired or revoked" in c.status_detail and c.state["auth_failed"]
    assert conn["id"] not in await due_syncs(datetime.now(UTC) + timedelta(hours=2))
    shown = next(x for x in (await client.get("/api/v1/connectors")).json() if x["id"] == conn["id"])
    assert shown["speakr"]["auth_failed"] is True
    # Sync now clears the flag and queues a pass.
    assert (await client.post(f"/api/v1/connectors/{conn['id']}/sync")).status_code == 200
    async with sessionmaker()() as db:
        assert not (await db.get(Connector, conn["id"])).state.get("auth_failed")
        jobs = (await db.execute(select(Job).where(Job.kind == "speakr.sync", Job.status == "queued"))).scalars().all()
        assert any(j.payload["connector_id"] == conn["id"] for j in jobs)
