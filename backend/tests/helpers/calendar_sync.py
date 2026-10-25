"""Shared helpers, from test_calendar_sync.py."""

import uuid

import caldav
from dev_services import RADICALE


async def _connect(client, *names: str) -> tuple[str, dict[str, caldav.Calendar], int]:
    username = f"u{uuid.uuid4().hex[:8]}"
    pr = caldav.DAVClient(url=RADICALE, username=username, password="x").principal()
    cals = {n: pr.make_calendar(name=n, supported_calendar_component_set=["VEVENT", "VTODO"])
            for n in names or ("Personal",)}
    r = await client.post("/api/v1/calendar/connect", json={"url": RADICALE, "username": username, "password": "x"})
    assert r.status_code == 200, r.text
    return username, cals, r.json()["id"]
