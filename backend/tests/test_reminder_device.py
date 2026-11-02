"""Reminder actions record the kind of device that made them: the phone's session as "android",
the browser's as "web" (before, both were recorded as "session")."""
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from mailr.core.auth import create_session
from mailr.db import sessionmaker
from mailr.models import ReminderState, User


@pytest_asyncio.fixture
async def phone(app, user):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        raw, s = await create_session(db, u, "mailr-android/0.40", "10.0.0.2", device="android")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.cookies.set("mailr_session", raw)
        c.headers["X-CSRF-Token"] = s.csrf
        yield c


async def _device(user, key_prefix: str) -> str:
    async with sessionmaker()() as db:
        rows = (await db.execute(select(ReminderState).where(ReminderState.user_id == user.id))).scalars().all()
    return next(r.device for r in rows if r.key.startswith(key_prefix))


async def test_a_snooze_from_the_phone_is_recorded_as_android(phone, user):
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    r = await phone.post("/api/v1/reminders/snooze", json={"key": "t|phone-task|2026-11-02T15:00:00Z|0", "until": until})
    assert r.status_code == 200, r.text
    assert await _device(user, "t|phone-task") == "android"


async def test_a_snooze_from_the_browser_is_recorded_as_web(client, user):
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    r = await client.post("/api/v1/reminders/snooze", json={"key": "t|web-task|2026-11-02T15:00:00Z|0", "until": until})
    assert r.status_code == 200, r.text
    assert await _device(user, "t|web-task") == "web"
