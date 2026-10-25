"""Shared helpers, from test_tasks_v2.py."""

import uuid

import caldav
from dev_services import RADICALE

API = "/api/v1"


async def _connect(client, *names: str) -> tuple[dict[str, caldav.Calendar], dict[str, dict]]:
    username = f"u{uuid.uuid4().hex[:8]}"
    pr = caldav.DAVClient(url=RADICALE, username=username, password="x").principal()
    cals = {n: pr.make_calendar(name=n, supported_calendar_component_set=["VEVENT", "VTODO"])
            for n in names or ("Personal",)}
    r = await client.post(f"{API}/calendar/connect", json={"url": RADICALE, "username": username, "password": "x"})
    assert r.status_code == 200, r.text
    cols = {c["name"]: c for c in (await client.get(f"{API}/calendar/calendars")).json()}
    return cals, cols
