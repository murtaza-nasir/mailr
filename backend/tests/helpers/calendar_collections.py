"""Shared helpers, from test_calendar_collections.py."""

import uuid

import caldav
from dev_services import RADICALE

API = "/api/v1"


async def _connect(client, *names: str):
    username = f"u{uuid.uuid4().hex[:8]}"
    pr = caldav.DAVClient(url=RADICALE, username=username, password="x").principal()
    for n in names or ("Personal",):
        pr.make_calendar(name=n, supported_calendar_component_set=["VEVENT", "VTODO"])
    r = await client.post(f"{API}/calendar/connect", json={"url": RADICALE, "username": username, "password": "x"})
    assert r.status_code == 200, r.text
    return pr, r.json()["id"]
