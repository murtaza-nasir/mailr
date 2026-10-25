"""A calendar link with 9,000 events (the most a link may have is MAX_EVENTS, 10,000): subscribing
and refreshing take seconds in one request, and the event loop stays free meanwhile.

Measured 13 Oct 2026 on the workstation: subscribe 63 s before (one row at a time, 13 statements
per event; the test audit saw about 97 s under load), about 10 s after (the file's parse in a thread,
then rows in bulk, 500 at a time). `pytest -m scale -s` prints the numbers.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import func, select

from mailr.calendar import webcal
from mailr.db import sessionmaker
from mailr.models import CalCollection, CalObject, CalOccurrence
from tests.scale_fixture import counting

pytestmark = [pytest.mark.scale, pytest.mark.serial]

N = 9000
BUDGET_SUBSCRIBE_S = 40.0
BUDGET_SQL = 200


def _feed(stamp: str, n: int = N) -> bytes:
    filler = "".join(f"BEGIN:VEVENT\r\nUID:e{i}\r\nDTSTAMP:{stamp}\r\nDTSTART:20261101T100000Z\r\n"
                     f"DTEND:20261101T110000Z\r\nSUMMARY:{'x' * 200}\r\nEND:VEVENT\r\n" for i in range(n))
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\n" + filler + "END:VCALENDAR\r\n").encode()


async def test_a_9000_event_calendar_link(client, user, monkeypatch):
    from helpers.security_calendar_limits import _LagProbe

    body = {"v": _feed("20261001T000000Z")}

    async def fake_fetch(url, **kw):
        return 200, body["v"], {}

    monkeypatch.setattr(webcal, "fetch", fake_fetch)
    t = time.monotonic()
    async with _LagProbe() as probe:
        with counting() as c:
            r = await client.post("/api/v1/calendar/subscriptions", json={"url": "https://feeds.example.com/big.ics"})
    took = time.monotonic() - t
    assert r.status_code == 200, r.text
    sub = r.json()
    print(f"\nsubscribe {N}: {took:.1f} s, {c.sql} statements, loop lag {probe.lag:.2f} s")
    assert sub["events"] == N
    async with sessionmaker()() as db:
        coll_id = (await db.execute(select(CalCollection.id).where(
            CalCollection.connector_id == sub["connector_id"]))).scalar_one()
        assert await db.scalar(select(func.count()).select_from(CalObject).where(
            CalObject.collection_id == coll_id, CalObject.deleted_at.is_(None))) == N
        assert await db.scalar(select(func.count()).select_from(CalOccurrence).where(
            CalOccurrence.collection_id == coll_id)) == N
    assert took < BUDGET_SUBSCRIBE_S, took
    assert c.sql < BUDGET_SQL, c.sql
    assert probe.lag < 1.0, probe.lag

    # Every event changed (a new DTSTAMP) and a third gone.
    body["v"] = _feed("20261002T000000Z", n=6000)
    t = time.monotonic()
    with counting() as c:
        out = (await client.post(f"/api/v1/calendar/subscriptions/{sub['connector_id']}/refresh")).json()
    took = time.monotonic() - t
    print(f"refresh, 6,000 changed and 3,000 gone: {took:.1f} s, {c.sql} statements")
    assert out["changed"] == 6000 and out["removed"] == 3000, out
    assert took < BUDGET_SUBSCRIBE_S, took
    assert c.sql < BUDGET_SQL, c.sql

    # Unchanged: nothing written.
    t = time.monotonic()
    out = (await client.post(f"/api/v1/calendar/subscriptions/{sub['connector_id']}/refresh")).json()
    print(f"refresh, unchanged: {time.monotonic() - t:.1f} s")
    assert out["changed"] == 0 and out["removed"] == 0, out
