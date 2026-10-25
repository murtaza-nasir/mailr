"""Dates mail is read on are the user's: a deadline counts from the day the mail was sent on the
user's calendar (intel/classify.py parse_deadline), and a reply's or forward's quote header gives
the time in the user's zone and clock, in their language (core/drafts.py quote_date)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from helpers.agent import _ingest, _setup_identity

from mailr.db import session_scope
from mailr.intel import classify
from mailr.models import User

CHICAGO = ZoneInfo("America/Chicago")


def test_tomorrow_in_evening_mail_is_the_next_local_day():
    # Monday 20:30 CDT is Tuesday 01:30 UTC.
    sent = datetime(2026, 10, 20, 1, 30, tzinfo=UTC)
    assert classify.parse_deadline("tomorrow", sent, CHICAGO) == "2026-10-20"
    assert classify.parse_deadline("tomorrow", sent) == "2026-10-21"  # read in UTC: a day late
    assert classify.parse_deadline("today", sent, CHICAGO) == "2026-10-19"


async def _as(user, **settings) -> None:
    async with session_scope() as db:
        u = await db.get(User, user.id)
        u.settings = {**(u.settings or {}), **settings}


RAW = (b"From: Lena Hart <lena.hart@example.org>\r\nTo: me@example.com\r\nSubject: Site visit\r\n"
       b"Message-ID: <site-visit-1@example.org>\r\nDate: Tue, 20 Oct 2026 01:30:00 +0000\r\n"
       b"Content-Type: text/plain; charset=utf-8\r\n\r\nCan we meet on site?\r\n")


async def test_the_quote_header_is_local_and_in_the_users_language(client, user):
    await _setup_identity(user)
    mid, _ = await _ingest(user.id, RAW)
    await _as(user, timezone="America/Chicago", language="de", time_format="24h")
    rep = (await client.get("/api/v1/compose/reply", params={"message_id": mid, "mode": "reply"})).json()
    assert "20:30" in rep["html"] and "schrieb Lena Hart" in rep["html"] and "wrote:" not in rep["html"]
    await _as(user, language="en", time_format="12h")
    rep = (await client.get("/api/v1/compose/reply", params={"message_id": mid, "mode": "reply"})).json()
    assert "Mon 19 Oct 2026, 8:30 PM" in rep["html"] and "Lena Hart &lt;lena.hart@example.org&gt; wrote:" in rep["html"]
    fwd = (await client.get("/api/v1/compose/reply", params={"message_id": mid, "mode": "forward"})).json()
    assert 'class="mailr_forward"' in fwd["html"] and "Date: Mon 19 Oct 2026, 8:30 PM" in fwd["html"]
