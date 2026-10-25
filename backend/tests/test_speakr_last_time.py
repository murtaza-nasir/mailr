# ACT-SURFACES: api assistant
"""Meeting prep, "Last time" (event.last_recording) on the event detail and the
event reminder: the latest earlier recording of the same series, else of an event with the same title
and a shared guest, else a recording with the same title and people; nothing for a past event; the
summary line fetched on demand; the assistant's recordings_for_event(previous=true). Needs the dev
Radicale."""

from datetime import UTC, datetime, timedelta

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.calendar import _connect
from helpers.speakr_matching import _event
from helpers.speakr_tools import _ctx
from speakr_fake import connect, speakr  # noqa: F401

from mailr.agent.tools import run_tool
from mailr.db import sessionmaker

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


async def _last(client, uid, occurrence="", excerpt=True):
    r = await client.get("/api/v1/speakr/events/last-time",
                         params={"uid": uid, "occurrence": occurrence, "excerpt": str(excerpt).lower()})
    assert r.status_code == 200, r.text
    return r.json()


async def test_last_time_series_title_and_guests_and_past(client, user, speakr):
    await _connect(client)
    # Weekly from two weeks ago, so the third occurrence is two hours from now (upcoming at any hour).
    first = (datetime.now(UTC) - timedelta(days=14) + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    series = await _event(client, "Lab meeting", first, recurrence="FREQ=WEEKLY;COUNT=5")
    speakr.add(1, "Lab meeting", meeting_date=first + timedelta(minutes=1), duration=55 * 60,
               summary="## Summary\n- Data collection is behind by a week")
    speakr.add(2, "Lab meeting", meeting_date=first + timedelta(days=7, minutes=1), duration=50 * 60,
               summary="## Summary\n- **Pilot** results are in")

    past = (datetime.now(UTC) - timedelta(days=10)).replace(hour=10, minute=0, second=0, microsecond=0)
    await _event(client, "Budget review", past, attendees=["evan@guest.example"])
    speakr.add(3, "Budget review", meeting_date=past, duration=45 * 60, participants="Evan")
    soon = (datetime.now(UTC) + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    upcoming = await _event(client, "Budget review", soon, attendees=["evan@guest.example"])
    lone = await _event(client, "Budget review", soon + timedelta(days=1), attendees=["zoe@other.example"])
    conn = await connect(client, speakr)

    # Same series: the latest recording before the next occurrence.
    nxt = (first + timedelta(days=14)).isoformat()
    d = await _last(client, series["uid"], nxt)
    assert d["upcoming"] and d["item"]["recording_id"] == 2 and d["item"]["why"] == "series"
    assert d["item"]["summary_line"] == "Pilot results are in"
    assert "summary_line" not in (await _last(client, series["uid"], nxt, excerpt=False))["item"]
    # The second occurrence (already past): nothing.
    assert (await _last(client, series["uid"], (first + timedelta(days=7)).isoformat()))["item"] is None

    # Another event with the same title and a shared guest.
    d = await _last(client, upcoming["uid"])
    assert d["item"]["recording_id"] == 3 and d["item"]["why"] in ("same title and guests", "same title and people")
    # Same title, other people: no "Last time".
    assert (await _last(client, lone["uid"]))["item"] is None

    async with sessionmaker()() as db:
        res = await run_tool(await _ctx(db, user.id), "recordings_for_event",
                             {"uid": series["uid"], "occurrence": nxt, "previous": True})
        assert res.data["previous"]["recording_id"] == 2 and res.data["previous"]["title"]["untrusted"]
        assert res.sensitive  # the connector is local-only
    assert conn["data_class"] == "local_only"
    r = await client.get("/api/v1/speakr/events/last-time", params={"uid": "nope"})
    assert r.status_code == 404


async def test_last_time_without_speakr(client, user):
    r = await client.get("/api/v1/speakr/events/last-time", params={"uid": "x"})
    assert r.json() == {"connected": False, "item": None}
