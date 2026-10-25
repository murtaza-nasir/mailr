"""One time zone (7 Oct 2026): the account setting `timezone` (Settings → You → Region).

`timezone` on GET and PUT /assistant/profile is an alias of it, and migration d1e5a9c3f7b2 moves a
zone stored in the profile into the setting.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import text

from mailr.db import sessionmaker
from mailr.models import AssistantProfile, User

API = "/api/v1"


async def test_profile_zone_is_the_setting(client, user):
    r = await client.put(f"{API}/assistant/profile", json={"timezone": "America/Chicago", "role": "Teacher"})
    assert r.status_code == 200, r.text
    assert r.json()["timezone"] == "America/Chicago"
    me = (await client.get(f"{API}/me")).json()
    assert me["settings"]["timezone"] == "America/Chicago"
    async with sessionmaker()() as db:
        row = await db.get(AssistantProfile, user.id)
        assert "timezone" not in (row.data or {}) and row.data["role"] == "Teacher"

    # Region changes it; the profile shows the same value.
    assert (await client.patch(f"{API}/me/settings", json={"timezone": "Europe/London"})).status_code == 200
    assert (await client.get(f"{API}/assistant/profile")).json()["timezone"] == "Europe/London"

    # Clearing through the alias clears the setting.
    assert (await client.put(f"{API}/assistant/profile", json={"timezone": ""})).status_code == 200
    assert "timezone" not in (await client.get(f"{API}/me")).json()["settings"]
    assert (await client.put(f"{API}/assistant/profile", json={"timezone": "Mars/Base"})).status_code == 400


async def test_migration_moves_the_profile_zone(user):
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "d1e5a9c3f7b2_one_time_zone.py"  # noqa: ASYNC240
    spec = importlib.util.spec_from_file_location("mig_one_time_zone", path)
    assert spec and spec.loader
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)

    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.settings = {**(u.settings or {}), "timezone": "Europe/Paris"}
        db.add(AssistantProfile(user_id=user.id, data={"timezone": "Asia/Tokyo", "role": "Chef"}, options={}, state={}))
        await db.commit()
    async with sessionmaker()() as db:
        conn = await db.connection()
        await conn.run_sync(mig.move_zones)
        await conn.run_sync(mig.move_zones)  # safe to run again
        await db.commit()
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        row = await db.get(AssistantProfile, user.id)
        # The profile's zone won before the change, so it is the one kept.
        assert u.settings["timezone"] == "Asia/Tokyo"
        assert row.data == {"role": "Chef"}
        n = (await db.execute(text("SELECT count(*) FROM assistant_profiles WHERE data ? 'timezone'"))).scalar()
        assert n == 0
