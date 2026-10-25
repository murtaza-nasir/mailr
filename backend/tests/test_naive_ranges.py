"""Dates and times without an offset in a search or a range are in the user's zone, as in
test_naive_times.py: search's after: and before:, the importance review's `since`, a list
item's device time, and calendar ranges and searches."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from helpers.core_flow import _ingest

from mailr.calendar.routes import _range
from mailr.calendar.routes_collections import _when
from mailr.core import search
from mailr.db import sessionmaker
from mailr.models import User
from mailr.notes.actions import _parse_device_at

CHICAGO = ZoneInfo("America/Chicago")


async def _zone(user, name="America/Chicago"):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.settings = {**(u.settings or {}), "timezone": name}
        await db.commit()


def test_parse_date_reads_the_given_zone():
    assert search._parse_date("2026/10/20", CHICAGO) == datetime(2026, 10, 20, tzinfo=CHICAGO)
    assert search._parse_date("2026/10/20") == datetime(2026, 10, 20, tzinfo=UTC)
    assert search._parse_date("2026-10-20T08:00:00+02:00", CHICAGO).utcoffset().total_seconds() == 7200


async def test_after_and_before_count_from_local_midnight(client, user, raw):
    await _zone(user)
    # 22:00 on the 19th in Chicago (03:00 UTC on the 20th), and 10:00 on the 20th.
    await _ingest(user, raw("Late on the nineteenth", date="Tue, 20 Oct 2026 03:00:00 +0000"), roles=["inbox"])
    await _ingest(user, raw("Morning of the twentieth", date="Tue, 20 Oct 2026 15:00:00 +0000"), roles=["inbox"])

    async def subjects(q):
        r = await client.get("/api/v1/conversations", params={"view": "all", "q": q})
        return {c["subject"] for c in r.json()["items"]}

    assert await subjects("after:2026/10/20") == {"Morning of the twentieth"}
    assert await subjects("before:2026/10/20") == {"Late on the nineteenth"}
    async with sessionmaker()() as db:
        assert await search.query_zone(db, user.id, "from:dana") is None
        assert await search.query_zone(db, user.id, "after:2026/10/20") == CHICAGO


async def test_device_time_and_calendar_ranges(user):
    await _zone(user)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        got = await _parse_device_at(db, u, "2026-10-20T09:30:00")
    assert got == datetime(2026, 10, 20, 9, 30, tzinfo=CHICAGO)
    start, end = _range("2026-10-20", "2026-10-21T08:00:00", zone=CHICAGO)
    assert start == datetime(2026, 10, 20, tzinfo=CHICAGO) and end == datetime(2026, 10, 21, 8, tzinfo=CHICAGO)
    assert _when("2026-11-02", CHICAGO) == datetime(2026, 11, 2, tzinfo=CHICAGO)
    assert _when("2026-11-02T10:00:00Z", CHICAGO) == datetime(2026, 11, 2, 10, tzinfo=UTC)


async def test_importance_review_since_is_local(client, user, monkeypatch):
    """`since` without an offset reaches the query in the user's zone."""
    from mailr.calendar import tz as tzmod

    await _zone(user)
    seen = []
    real = tzmod.aware

    async def spy(db, who, v):
        out = await real(db, who, v)
        seen.append(out)
        return out

    monkeypatch.setattr(tzmod, "aware", spy)
    r = await client.get("/api/v1/intel/importance/review", params={"since": "2099-01-01T22:00:00"})
    assert r.status_code == 200
    assert seen == [datetime(2099, 1, 1, 22, tzinfo=CHICAGO)]
