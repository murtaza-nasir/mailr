"""Two settings saves at once keep both changes: PATCH /me/settings and PUT /reminders/settings
read, merge and write back the whole users.settings document, so each locks the user's row until
it commits. Without the lock the later write carried an older copy and dropped the other's keys
(seen in the browser tests: a settings page saves on every change)."""

import asyncio


async def test_parallel_settings_saves_keep_every_change(client):
    keys = {"week_start": 1, "time_format": "24h", "date_order": "dmy", "currency": "EUR", "images": "never",
            "advanced_settings": True, "inbox_count": "all"}
    rs = await asyncio.gather(
        *(client.patch("/api/v1/me/settings", json={k: v}) for k, v in keys.items()),
        client.put("/api/v1/reminders/settings", json={"default_timed_event": 15}),
    )
    assert all(r.status_code == 200 for r in rs), [r.text for r in rs]
    s = (await client.get("/api/v1/me")).json()["settings"]
    for k, v in keys.items():
        assert s[k] == v, k
    assert (await client.get("/api/v1/reminders/settings")).json()["default_timed_event"] == 15
