"""Shared helpers, from test_speakr_matching.py."""

from datetime import timedelta


async def _event(client, summary, start, minutes=60, **extra):
    body = {"summary": summary, "start": start.isoformat(), "end": (start + timedelta(minutes=minutes)).isoformat(),
            "send_updates": False, **extra}
    r = await client.post("/api/v1/calendar/events", json=body)
    assert r.status_code == 200, r.text
    return r.json()
