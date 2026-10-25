"""Ambrose speaks the user's time zone when Region is Automatic: the web sends the
browser's zone (X-Mailr-Zone), the server keeps it as `device_timezone`, and the prompt's "Now"
and the times in tool results are given in it (calendar/tz.py, agent/when.py localize_times)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from zoneinfo import ZoneInfo

from helpers.agent import (  # noqa: F401
    _drop_endpoint,
    _endpoint,
    _events,
    _ingest,
    _text_turn,
    _tool_turn,
    fake_llm,
)

from mailr.agent import when
from mailr.calendar import tz as tzmod
from mailr.db import sessionmaker
from mailr.models import User

CHICAGO = ZoneInfo("America/Chicago")


def test_localize_times_rewrites_instants_only():
    data = {"items": [{"last_date": "2026-10-19T20:09:00+00:00", "subject": "Lunch at 2026-10-19T20:09:00Z?",
                       "date": "2026-10-19", "n": 3}], "at": "2026-10-19T20:09:00Z"}
    out = when.localize_times(data, CHICAGO)
    assert out["items"][0]["last_date"] == "2026-10-19T15:09:00-05:00"
    assert out["at"] == "2026-10-19T15:09:00-05:00"
    assert out["items"][0]["subject"] == "Lunch at 2026-10-19T20:09:00Z?"  # text inside text stays
    assert out["items"][0]["date"] == "2026-10-19" and out["items"][0]["n"] == 3
    assert data["items"][0]["last_date"] == "2026-10-19T20:09:00+00:00"  # the input is not changed


async def test_device_zone_is_kept_and_an_explicit_zone_wins(client, user):
    r = await client.get("/api/v1/me", headers={"X-Mailr-Zone": "America/Chicago"})
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        assert (u.settings or {}).get("device_timezone") == "America/Chicago"
        assert tzmod.user_zone_from(u).key == "America/Chicago"
    # A zone the server does not know is ignored.
    r = await client.get("/api/v1/me", headers={"X-Mailr-Zone": "Mars/Olympus_Mons"})
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        assert u.settings["device_timezone"] == "America/Chicago"
        # The Region setting, when chosen, wins over the browser.
        u.settings = {**u.settings, "timezone": "Europe/London"}
        await db.commit()
        assert tzmod.user_zone_from(u).key == "Europe/London"


async def test_automatic_region_answers_in_the_browser_zone(client, user, raw, fake_llm):
    # A mail at 20:09 UTC yesterday (15:09 or 14:09 in Chicago, by the season).
    at = (datetime.now(UTC) - timedelta(days=1)).replace(hour=20, minute=9, second=0, microsecond=0)
    await _ingest(user.id, raw("Zone check", date=format_datetime(at)))
    local = at.astimezone(CHICAGO).replace(microsecond=0).isoformat()
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [_tool_turn("search_conversations", {"query": "subject:\"Zone check\""}),
                           _text_turn("One mail.")]
        r = await client.post("/api/v1/agent/chat", json={"message": "/today"},
                              headers={"X-Mailr-Zone": "America/Chicago"})
        evs = _events(r.text)
        assert not [e for e in evs if e["type"] == "error"], evs
        msgs = fake_llm.requests[-1]["body"]["messages"]
        system = msgs[0]["content"]
        assert "in America/Chicago" in system and "in UTC" not in system
        tool = next(m for m in msgs if m["role"] == "tool")
        body = json.loads(tool["content"]) if tool["content"].startswith("{") else tool["content"]
        text = json.dumps(body)
        assert local in text, text[:2000]
        assert at.isoformat() not in text
    finally:
        await _drop_endpoint(ep)
