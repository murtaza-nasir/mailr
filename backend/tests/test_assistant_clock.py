"""Ambrose writes times in the user's clock. A fresh-install test (20 Oct 2026): the web showed
5:08 PM and Ambrose said "today at 17:08". Time format (Settings → You → Region) wins when it says
12h or 24h; on Automatic, the clock the device sent (X-Mailr-Clock) is kept as `device_clock`
(calendar/tz.py); with neither, 24-hour as before. The prompt says which form to write, and the
`when` lines the tools give are in it (agent/when.py)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from helpers.agent import (  # noqa: F401
    _drop_endpoint,
    _endpoint,
    _events,
    _text_turn,
    fake_llm,
)

from mailr.agent import prompts, when
from mailr.calendar import tz as tzmod
from mailr.db import sessionmaker
from mailr.models import User

CHI = ZoneInfo("America/Chicago")


def _u(**settings):
    return SimpleNamespace(name="Rowan", settings=settings)


def test_the_setting_wins_then_the_device_then_24h():
    assert tzmod.user_clock(_u(time_format="12h", device_clock="24h")) == "12h"
    assert tzmod.user_clock(_u(time_format="24h", device_clock="12h")) == "24h"
    assert tzmod.user_clock(_u(time_format="auto", device_clock="12h")) == "12h"
    assert tzmod.user_clock(_u(device_clock="12h")) == "12h"
    assert tzmod.user_clock(_u(time_format="auto")) == "24h"
    assert tzmod.user_clock(_u(time_format="auto"), default="12h") == "12h"
    assert tzmod.user_clock(None) == "24h"


def test_describe_and_now_in_12_hour_form(monkeypatch):
    monkeypatch.setattr(when, "now", lambda: datetime(2026, 10, 20, 17, 8, tzinfo=CHI).astimezone(UTC))
    assert when.describe("2026-10-20T22:08:00+00:00", None, False, CHI, "12h") == "Tue 20 Oct 2026, 5:08 PM CDT"
    assert (when.describe("2026-10-20T15:00:00+00:00", "2026-10-20T16:30:00+00:00", False, CHI, "12h")
            == "Tue 20 Oct 2026, 10:00–11:30 AM CDT")
    assert (when.describe("2026-10-20T16:30:00+00:00", "2026-10-20T18:00:00+00:00", False, CHI, "12h")
            == "Tue 20 Oct 2026, 11:30 AM–1:00 PM CDT")
    assert when.describe("2026-10-20T22:08:00+00:00", None, False, CHI) == "Tue 20 Oct 2026, 17:08 CDT"
    assert when.now_line(CHI, "12h").startswith("Tuesday 20 October 2026, 5:08 PM in America/Chicago")


def test_the_prompt_says_which_form_to_write():
    twelve = prompts.assistant_system(_u(time_format="auto", device_clock="12h"), {})
    assert "Write times in 12-hour form with AM or PM, like 5:08 PM" in twelve
    twenty_four = prompts.assistant_system(_u(time_format="24h", device_clock="12h"), {})
    assert "Write times in 24-hour form, like 17:08" in twenty_four
    assert "Write times in 12-hour form" in prompts.draft_system(_u(time_format="12h"), {})


async def test_the_device_clock_is_kept_and_reaches_the_prompt(client, user, fake_llm):
    r = await client.get("/api/v1/me", headers={"X-Mailr-Clock": "12h"})
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        assert u.settings["device_clock"] == "12h"
    # Anything but 12h or 24h is ignored.
    await client.get("/api/v1/me", headers={"X-Mailr-Clock": "h23"})
    async with sessionmaker()() as db:
        assert (await db.get(User, user.id)).settings["device_clock"] == "12h"
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [_text_turn("Noted.")]
        r = await client.post("/api/v1/agent/chat", json={"message": "What time is it?"},
                              headers={"X-Mailr-Clock": "24h"})
        evs = _events(r.text)
        assert not [e for e in evs if e["type"] == "error"], evs
        system = fake_llm.requests[-1]["body"]["messages"][0]["content"]
        assert "Write times in 24-hour form" in system
        # Time format, when chosen, wins over the device.
        async with sessionmaker()() as db:
            u = await db.get(User, user.id)
            assert u.settings["device_clock"] == "24h"
            u.settings = {**u.settings, "time_format": "12h"}
            await db.commit()
        fake_llm.script = [_text_turn("Noted.")]
        r = await client.post("/api/v1/agent/chat", json={"message": "And now?"},
                              headers={"X-Mailr-Clock": "24h"})
        system = fake_llm.requests[-1]["body"]["messages"][0]["content"]
        assert "Write times in 12-hour form" in system
    finally:
        await _drop_endpoint(ep)
