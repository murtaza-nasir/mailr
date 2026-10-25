"""A change of the user's zone (Region, or the browser's zone while Region is Automatic) expands
all-day and floating events again in the new zone (calendar.rematerialize)."""

from __future__ import annotations

from sqlalchemy import select

from mailr.db import session_scope, sessionmaker
from mailr.models import Job, User


async def _queued(user) -> list[dict]:
    async with sessionmaker()() as db:
        return [j.payload for j in (await db.execute(select(Job).where(
            Job.user_id == user.id, Job.kind == "calendar.rematerialize", Job.status == "queued"))).scalars()]


async def test_a_region_change_expands_events_again(client, user):
    r = await client.patch("/api/v1/me/settings", json={"timezone": "Asia/Tokyo"})
    assert r.status_code == 200, r.text
    assert await _queued(user) == [{"user_id": user.id}]
    # Another setting alone changes nothing about the events.
    async with session_scope() as db:
        await db.execute(Job.__table__.delete().where(Job.user_id == user.id))
    await client.patch("/api/v1/me/settings", json={"timezone": "Asia/Tokyo", "undo_send_seconds": 20})
    assert await _queued(user) == []


async def test_the_browsers_zone_on_automatic_expands_events_again(app, user):
    from mailr.calendar import tz

    async with session_scope() as db:
        u = await db.get(User, user.id)
        assert await tz.note_device_zone(db, u, "Europe/Lisbon")
    assert await _queued(user) == [{"user_id": user.id}]
