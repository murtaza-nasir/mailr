# ACT-SURFACES: assistant
"""The assistant's dates and times are the user's (agent/when.py).

The case: asked at 15:49 on Sunday 4 October 2026 in America/Chicago (CDT, UTC-05:00)
"What's on my calendar tomorrow?", the first calendar call failed ("the range must be positive"),
and the answer listed an event at 23:00 on Sunday (04:00Z Monday) as "12:00–12:30 AM" on Monday.
These tests fix the clock at that moment, and at the end of daylight saving time (Sun 1 Nov 2026).
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from mailr.agent import prompts, when

CHI = ZoneInfo("America/Chicago")
ASKED = datetime(2026, 10, 4, 15, 49, tzinfo=CHI)  # 20:49Z
BEFORE_DST_END = datetime(2026, 10, 31, 22, 0, tzinfo=CHI)  # Saturday night, still CDT


@pytest.fixture
def clock(monkeypatch):
    def at(moment: datetime) -> None:
        monkeypatch.setattr(when, "now", lambda: moment.astimezone(UTC))
    at(ASKED)
    return at


def test_now_line_states_local_date_time_weekday_zone_and_offset(clock):
    assert when.now_line(CHI) == ("Sunday 4 October 2026, 15:49 in America/Chicago (CDT, UTC-05:00). "
                                  "Tomorrow is Monday 5 October 2026")


def test_tomorrow_is_the_local_day(clock):
    start, end = when.local_range(None, None, CHI, when="tomorrow")
    assert (start.isoformat(), end.isoformat()) == ("2026-10-05T05:00:00+00:00", "2026-10-06T05:00:00+00:00")
    event = datetime(2026, 10, 4, 23, 0, tzinfo=CHI)  # the Homelab event: Sunday night, 04:00Z Monday
    assert not start <= event < end
    # The model's first call: the same date as from and to. It is that whole day, not an error.
    assert when.local_range("2026-10-05", "2026-10-05", CHI) == (start, end)
    # A time without an offset is local.
    assert when.local_range("2026-10-05T00:00", "2026-10-06T00:00", CHI) == (start, end)
    s2, e2 = when.local_range(None, None, CHI, when="today")
    assert s2 <= event < e2


def test_a_backwards_range_says_what_to_send(clock):
    with pytest.raises(when.RangeError) as e:
        when.local_range("2026-10-06", "2026-10-05", CHI)
    msg = str(e.value)
    assert "from=2026-10-05 and to=2026-10-05 is Monday 5 October 2026" in msg and 'when="tomorrow"' in msg
    with pytest.raises(when.RangeError, match="`when` takes"):
        when.local_range(None, None, CHI, when="someday")


def test_results_carry_offset_times_and_weekdays():
    assert when.local_iso("2026-10-05T04:00:00+00:00", CHI) == "2026-10-04T23:00:00-05:00"
    assert when.describe("2026-10-05T04:00:00+00:00", "2026-10-05T04:30:00+00:00", False, CHI) == \
        "Sun 4 Oct 2026, 23:00–23:30 CDT"
    assert when.describe("2026-10-05", "2026-10-06", True, CHI) == "Mon 5 Oct 2026, all day"
    assert when.local_day("2026-10-05T04:00:00+00:00", CHI) == "2026-10-04"


def test_dst_boundary(clock):
    """DST ends at 02:00 on Sunday 1 November 2026: that day has 25 hours, and times after it are
    CST (UTC-06:00). No fixed offset is carried from "now" to another date."""
    clock(BEFORE_DST_END)
    assert when.now_line(CHI).endswith("(CDT, UTC-05:00). Tomorrow is Sunday 1 November 2026")
    start, end = when.local_range(None, None, CHI, when="tomorrow")
    assert (start.isoformat(), end.isoformat()) == ("2026-11-01T05:00:00+00:00", "2026-11-02T06:00:00+00:00")
    late = datetime(2026, 11, 1, 23, 0, tzinfo=CHI)  # 05:00Z Monday
    assert start <= late < end
    assert when.local_iso("2026-11-02T05:00:00+00:00", CHI) == "2026-11-01T23:00:00-06:00"
    assert when.describe("2026-11-02T05:00:00+00:00", "2026-11-02T05:30:00+00:00", False, CHI) == \
        "Sun 1 Nov 2026, 23:00–23:30 CST"
    clock(datetime(2026, 11, 1, 9, 0, tzinfo=CHI))
    assert "(CST, UTC-06:00)" in when.now_line(CHI)


class _User:
    name = "Rowan"
    settings = {"timezone": "America/Chicago"}


def test_prompt_now_uses_the_settings_zone_without_a_profile_zone(clock):
    """The prompt read only the assistant profile's zone; with only Settings → zone set it said UTC."""
    system = prompts.assistant_system(_User(), {})
    assert "Now: Sunday 4 October 2026, 15:49 in America/Chicago (CDT, UTC-05:00). Tomorrow is Monday 5 October 2026." \
        in system
    assert "never convert them" in system


# ---------------------------------------------------------------- the tools, against the dev Radicale

try:
    from dev_services import radicale_up as _radicale_up
    from helpers.calendar import _connect

    _RADICALE = _radicale_up()
except Exception:  # noqa: BLE001
    _RADICALE = False

radicale = pytest.mark.skipif(not _RADICALE, reason="Radicale dev server not running on :15232")


async def _settings_zone(user_id: int, name: str) -> None:
    from mailr.db import sessionmaker
    from mailr.models import User

    async with sessionmaker()() as db:
        u = await db.get(User, user_id)
        u.settings = {**(u.settings or {}), "timezone": name}
        await db.commit()


@radicale
async def test_calendar_tools_answer_tomorrow_in_the_users_zone(client, user, clock):
    """list_calendar / search_events: the case above, end to end. The Sunday 23:00 event is
    today's, never tomorrow's, and it comes back as 23:00 with its offset and weekday."""
    import uuid

    from mailr.agent import context
    from mailr.agent.tools import ToolCtx, ToolError, run_tool
    from mailr.core.auth import ALL_SCOPES, Principal
    from mailr.db import sessionmaker
    from mailr.models import User

    await _connect(client)
    await _settings_zone(user.id, "America/Chicago")
    for summary, start, end in (
        ("Homelab: re-enable encrypted backups", "2026-10-04T23:00:00", "2026-10-04T23:30:00"),
        ("DATA 520", "2026-10-05T10:00:00", "2026-10-05T11:00:00"),
        ("Late check", "2026-11-01T23:00:00", "2026-11-01T23:30:00"),
    ):
        r = await client.post("/api/v1/calendar/events", json={"summary": summary, "start": start, "end": end,
                                                               "tz": "America/Chicago"})
        assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        ctx = ToolCtx(db=db, p=Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"}), assistant=True,
                      chat_session_id=f"s-{uuid.uuid4().hex[:6]}")

        tomorrow = await run_tool(ctx, "list_calendar", {"when": "tomorrow"})
        assert [e["summary"] for e in tomorrow.data["items"]] == ["DATA 520"]
        course = tomorrow.data["items"][0]
        assert course["start"] == "2026-10-05T10:00:00-05:00" and course["when"] == "Mon 5 Oct 2026, 10:00–11:00 CDT"
        assert tomorrow.data["from"] == "2026-10-05T00:00:00-05:00" and tomorrow.data["tz"] == "America/Chicago"
        # The model's failed first call: from and to the same date now means that day.
        same = await run_tool(ctx, "list_calendar", {"from": "2026-10-05", "to": "2026-10-05"})
        assert [e["summary"] for e in same.data["items"]] == ["DATA 520"]

        today = await run_tool(ctx, "search_events", {"when": "today"})
        home = next(e for e in today.data["items"] if e["summary"].startswith("Homelab"))
        assert home["start"] == "2026-10-04T23:00:00-05:00" and home["end"] == "2026-10-04T23:30:00-05:00"
        assert home["when"] == "Sun 4 Oct 2026, 23:00–23:30 CDT" and "event_tz" not in home

        with pytest.raises(ToolError, match='when="tomorrow"'):
            await run_tool(ctx, "list_calendar", {"from": "2026-10-06", "to": "2026-10-05"})

        # The context bundle groups by the user's day: the Sunday event is Sunday's.
        bundle = await context._calendar(db, ctx.p, ["what is on tomorrow?"])
        assert [b["summary"] for b in bundle["busy"]["2026-10-04"]] == ["Homelab: re-enable encrypted backups"]
        assert [b["summary"] for b in bundle["busy"]["2026-10-05"]] == ["DATA 520"]

        # Across the end of DST: on Saturday night, tomorrow holds Sunday 23:00 CST.
        clock(BEFORE_DST_END)
        ctx.user_zone = None
        dst = await run_tool(ctx, "list_calendar", {"when": "tomorrow"})
        late = next(e for e in dst.data["items"] if e["summary"] == "Late check")
        assert late["start"] == "2026-11-01T23:00:00-06:00" and late["when"] == "Sun 1 Nov 2026, 23:00–23:30 CST"
