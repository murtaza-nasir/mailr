"""GET /today at production scale (once 6.4 s cold, 215 ms warm, 102 and 65 statements on
a large account, while the other main routes take 3–30 ms).

One user gets the scale mailbox (tests/scale_fixture.py) and a calendar mix shaped like a heavy user's:
two CalDAV accounts whose last check is older than the read refresh age, a calendar with a year
of events (one-off, weekly series, all-day, cancelled, declined) and reminders on them, a task list
with overdue, due-today, future, undated and completed tasks, fired and missed reminders, follow-ups
and approvals. The test counts what one request does: SQL statements, inline calendar syncs and
outbound HTTP. Counts, not wall-clock limits.

Marked `scale` (skipped by the gate; `pytest -m scale` or MAILR_TEST_SCALE=1 runs it).
MAILR_SCALE_CONVERSATIONS=45000 builds the production-sized mailbox, MAILR_SCALE_REPORT=1 measures
without asserting, MAILR_SCALE_HIST=1 prints the most frequent and slowest statements and the
EXPLAIN ANALYZE of the slowest, MAILR_SCALE_PROFILE=1 a profile of the warm request.
"""

from __future__ import annotations

import os
import time
import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from conftest import make_db_user
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text, update

from tests import scale_fixture as sf

pytestmark = [pytest.mark.scale, pytest.mark.serial]

ZONE = "America/Chicago"
# Statements per /today request, at any number of reminders, tasks and events [measured 14 Oct 2026
# at 45k conversations with the mix below: 31 cold and warm; before: 213 cold with two inline
# calendar syncs (3 s budget each), 209 warm, of which 185 read the fired reminders one by one].
BUDGET_TODAY_SQL = 35
REPORT_ONLY = os.environ.get("MAILR_SCALE_REPORT") == "1"


def budget(ok: bool, detail) -> None:
    if not REPORT_ONLY:
        assert ok, detail


def _stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _event(uid: str, summary: str, start: datetime, minutes: int = 60, *, rrule: str = "", status: str = "",
           attendee: str = "", alarm: bool = True) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//scale//EN", "BEGIN:VEVENT", f"UID:{uid}",
             f"DTSTAMP:{_stamp(start)}", f"DTSTART:{_stamp(start)}", f"DTEND:{_stamp(start + timedelta(minutes=minutes))}",
             f"SUMMARY:{summary}"]
    if rrule:
        lines.append(f"RRULE:{rrule}")
    if status:
        lines.append(f"STATUS:{status}")
    if attendee:
        lines += ["ORGANIZER:mailto:boss@northwind.example", f"ATTENDEE;PARTSTAT={attendee}:mailto:{ME}",
                  "ATTENDEE;PARTSTAT=ACCEPTED:mailto:boss@northwind.example"]
    if alarm:
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Reminder", "TRIGGER:-PT15M", "END:VALARM"]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def _all_day(uid: str, summary: str, day: date) -> str:
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//scale//EN", "BEGIN:VEVENT", f"UID:{uid}",
                        "DTSTAMP:20260101T000000Z", f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
                        f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}", f"SUMMARY:{summary}",
                        "END:VEVENT", "END:VCALENDAR"]) + "\r\n"


def _task(uid: str, summary: str, due: datetime | date | None, *, done: bool = False, alarm: bool = False) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//scale//EN", "BEGIN:VTODO", f"UID:{uid}",
             "DTSTAMP:20260101T000000Z", f"SUMMARY:{summary}"]
    if isinstance(due, datetime):
        lines.append(f"DUE:{_stamp(due)}")
    elif isinstance(due, date):
        lines.append(f"DUE;VALUE=DATE:{due:%Y%m%d}")
    lines.append("STATUS:COMPLETED" if done else "STATUS:NEEDS-ACTION")
    if done:
        lines.append("COMPLETED:20260101T000000Z")
    if alarm and due is not None:
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Reminder", "TRIGGER:-PT1H", "END:VALARM"]
    lines += ["END:VTODO", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


ME = "scale-today@example.com"


async def _calendar_mix(user_id: int) -> dict:
    """Two stale CalDAV accounts with a year of events and a task list, fired reminders, follow-ups
    and approvals. Returns what Today must list."""
    from mailr.calendar import mirror
    from mailr.db import sessionmaker
    from mailr.models import (
        CalCollection,
        Connector,
        Conversation,
        PendingAction,
        ReminderSchedule,
        User,
    )

    zone = ZoneInfo(ZONE)
    now = datetime.now(UTC)
    today = now.astimezone(zone).date()
    day0 = datetime(today.year, today.month, today.day, tzinfo=zone)
    stale = (now - timedelta(hours=1)).isoformat()
    expect = {"events": set(), "tasks": set()}
    async with sessionmaker()() as db:
        u = await db.get(User, user_id)
        u.settings = {**(u.settings or {}), "timezone": ZONE}
        conns, colls = [], []
        for i, name in enumerate(("Work", "Home")):
            c = Connector(user_id=user_id, kind="caldav", name=name, mode="mirror", enabled=True, status="idle",
                          config={"url": f"https://cal{i}.invalid/dav/"},
                          state={"caldav": {"initial_done": True, "checked_at": stale, "listed_at": stale}})
            db.add(c)
            await db.flush()
            conns.append(c)
            coll = CalCollection(user_id=user_id, connector_id=c.id, href=f"https://cal{i}.invalid/dav/{name.lower()}/",
                                 displayname=name, components=["VEVENT", "VTODO"], notify=True)
            db.add(coll)
            await db.flush()
            colls.append(coll)
        await db.commit()

        me = {ME}
        for k, coll in enumerate(colls):
            items = []
            # A year back and three months ahead, about four events a day over the two calendars.
            for n in range(-365 * 2, 90 * 2):
                at = day0 + timedelta(days=n // 2, hours=8 + (n % 2) * 3 + k * 5)
                uid = f"ev{k}-{n}-{uuid.uuid4().hex[:6]}"
                items.append((f"{coll.href}{uid}.ics", f'"{uid}"', _event(uid, f"Meeting {n}", at)))
                if n // 2 == 0:
                    expect["events"].add(uid)
            # Today's edge cases: all-day, cancelled, declined, accepted invitation.
            uid = f"allday{k}"
            items.append((f"{coll.href}{uid}.ics", '"a"', _all_day(uid, "Conference", today)))
            expect["events"].add(uid)
            for uid, kw in ((f"cancel{k}", {"status": "CANCELLED"}), (f"decl{k}", {"attendee": "DECLINED"}),
                            (f"acc{k}", {"attendee": "ACCEPTED"})):
                items.append((f"{coll.href}{uid}.ics", '"x"', _event(uid, "Invite", day0 + timedelta(hours=16), **kw)))
                if uid.startswith("acc"):
                    expect["events"].add(uid)
            # Weekly series, started half a year ago: one in seven falls on today.
            for s in range(14):
                uid = f"series{k}-{s}"
                start = day0 - timedelta(weeks=26) + timedelta(days=s % 7, hours=13, minutes=s)
                items.append((f"{coll.href}{uid}.ics", '"s"', _event(uid, "Weekly", start, rrule="FREQ=WEEKLY")))
                if s % 7 == (today - (day0 - timedelta(weeks=26)).date()).days % 7:
                    expect["events"].add(uid)
            # Tasks: overdue, due today (dated and timed), future, undated, completed.
            for t in range(150):
                uid = f"task{k}-{t}"
                kind = t % 6
                due = (today - timedelta(days=1 + t) if kind == 0 else today if kind == 1
                       else day0 + timedelta(hours=9 + t % 8) if kind == 2
                       else today + timedelta(days=3 + t) if kind == 3 else None)
                items.append((f"{coll.href}{uid}.ics", f'"{uid}"', _task(uid, f"Task {t}", due, done=kind == 5,
                                                                          alarm=kind in (0, 2))))
                if kind in (0, 1, 2):
                    expect["tasks"].add(uid)
            await mirror.upsert_many(db, coll, items, user_tz=zone, me=me)
            await db.commit()

        # Reminders: the last week's are planned as they were a week ago and have fired (the
        # hourly replan keeps fired rows for KEEP_FIRED, 7 days); a tenth fired more than the grace
        # late (missed). Then the plan from now.
        from mailr.calendar import planner

        await planner.replan_user(db, user_id, now=now - timedelta(days=7))
        await db.execute(update(ReminderSchedule).where(ReminderSchedule.user_id == user_id,
                                                        ReminderSchedule.fire_at <= now)
                         .values(fired_at=ReminderSchedule.fire_at + timedelta(seconds=30)))
        await db.execute(text(
            "UPDATE reminder_schedule SET fired_at = fire_at + interval '1 hour' "
            "WHERE user_id = :u AND fired_at IS NOT NULL AND (kind = 't' OR id % 10 = 0)"), {"u": user_id})
        await planner.replan_user(db, user_id, now=now)
        # Follow-ups: 25 nudged, 40 due today or earlier, 300 later.
        ids = (await db.execute(select(Conversation.id).where(Conversation.user_id == user_id)
                                .order_by(Conversation.id).limit(365))).scalars().all()
        for i, cid in enumerate(ids):
            vals = ({"nudged_at": now - timedelta(hours=i), "follow_up_since": now - timedelta(days=3)} if i < 25 else
                    {"follow_up_at": now - timedelta(hours=i - 24), "follow_up_since": now - timedelta(days=5)} if i < 65
                    else {"follow_up_at": now + timedelta(days=i)})
            await db.execute(update(Conversation).where(Conversation.id == cid).values(**vals))
        for i in range(12):
            db.add(PendingAction(user_id=user_id, subject="agent", action="send_draft", preview={"title": f"Send {i}"},
                                 expires_at=now + timedelta(days=1)))
        await db.commit()
        n_sched, n_fired = (await db.execute(text(
            "SELECT count(*), count(fired_at) FROM reminder_schedule WHERE user_id = :u"), {"u": user_id})).one()
    from mailr.db import engine

    async with engine().connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for t in ("cal_objects", "cal_occurrences", "reminder_schedule", "conversations"):
            await conn.execute(text(f"VACUUM ANALYZE {t}"))
    expect["reminders_planned"], expect["reminders_fired"] = n_sched, n_fired
    expect["connectors"] = [c.id for c in conns]
    return expect


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def today_scale():
    from mailr.db import sessionmaker
    from mailr.main import create_app

    app = create_app()
    u = await make_db_user(ME, "Scale Today", password="scale-password-123")
    async with sessionmaker()() as db:
        built = await sf.build(db, u.id)
    t0 = time.perf_counter()
    expect = await _calendar_mix(u.id)
    print(f"\nscale fixture: {built.conversations} conversations, {built.messages} messages; calendar mix "
          f"{time.perf_counter() - t0:.1f}s, {expect['reminders_planned']} reminders "
          f"({expect['reminders_fired']} fired)")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/login", json={"email": u.email, "password": "scale-password-123"})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        yield built, expect, c


@pytest.fixture
def production_sync(monkeypatch):
    """The production read path: a connector checked more than 20 s ago is synced inline (tests use
    0 s). The CalDAV servers are not called; each inline run is recorded, writes its check time as a
    real run does, and costs nothing (on a real server it can cost up to the 3 s budget each)."""
    from mailr.calendar import mirror, sync
    from mailr.db import session_scope
    from mailr.models import Connector

    monkeypatch.setattr(mirror, "read_refresh_seconds", lambda: 20.0)
    runs: list[tuple[int, float]] = []
    background: list[int] = []

    async def run_inline(connector_id: int, *, budget: float, full: bool = False):
        runs.append((connector_id, budget))
        async with session_scope() as db:
            conn = await db.get(Connector, connector_id)
            st = dict((conn.state or {}).get("caldav") or {})
            st["checked_at"] = st["listed_at"] = datetime.now(UTC).isoformat()
            conn.state = {**(conn.state or {}), "caldav": st}

    def kick(connector_id: int) -> None:
        background.append(connector_id)

    monkeypatch.setattr(sync, "run_inline", run_inline)
    if hasattr(sync, "refresh_in_background"):
        monkeypatch.setattr(sync, "refresh_in_background", kick)
    return runs, background


async def _stale(connector_ids: list[int]) -> None:
    from mailr.db import session_scope
    from mailr.models import Connector

    async with session_scope() as db:
        for cid in connector_ids:
            conn = await db.get(Connector, cid)
            st = dict((conn.state or {}).get("caldav") or {})
            st["checked_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
            conn.state = {**(conn.state or {}), "caldav": st}


def _report(name: str, m: sf.Counter, runs: list, background: list) -> None:
    print(f"\n  {name:<14} {m.seconds * 1000:8.1f} ms  {m.sql:4d} SQL  inline syncs {len(runs)} "
          f"(budget {sum(b for _, b in runs):.0f} s)  background refreshes {len(background)}")
    if os.environ.get("MAILR_SCALE_HIST") == "all":
        for st in m.statements:
            print(f"      {' '.join(st.split())[:150]}")
    elif os.environ.get("MAILR_SCALE_HIST"):
        for st, k in Counter(" ".join(x.split())[:120] for x in m.statements).most_common(8):
            print(f"      {k:5d}  {st}")
        for d, st in sorted(m.durations, reverse=True)[:5]:
            print(f"      {d * 1000:7.1f} ms  {' '.join(st.split())[-300:]}")


async def _explain_slowest(m: sf.Counter, n: int = 3) -> None:
    from mailr.db import engine

    slow = sorted(range(len(m.durations)), key=lambda i: m.durations[i][0], reverse=True)[:n]
    for i in slow:
        stmt, params = m.statements[i], m.params[i]
        if not stmt.lstrip().upper().startswith("SELECT"):
            continue
        async with engine().connect() as conn:
            raw = (await conn.get_raw_connection()).driver_connection
            tr = raw.transaction()
            await tr.start()
            try:
                plan = await raw.fetch("EXPLAIN (ANALYZE, BUFFERS) " + stmt, *(params or ()))
            finally:
                await tr.rollback()
        print(f"\n  EXPLAIN ANALYZE ({m.durations[i][0] * 1000:.1f} ms): {' '.join(stmt.split())[:200]}")
        for row in plan[:25]:
            print("      " + row[0])


async def test_today_at_scale(today_scale, production_sync):
    _built, expect, c = today_scale
    runs, background = production_sync
    out = {}
    for name in ("cold", "warm"):
        if name == "cold":
            await _stale(expect["connectors"])
        runs.clear()
        background.clear()
        prof = None
        if os.environ.get("MAILR_SCALE_PROFILE") in (name, "1" if name == "warm" else ""):
            import cProfile

            prof = cProfile.Profile()
            prof.enable()
        with sf.counting() as m:
            r = await c.get("/api/v1/today")
        if prof is not None:
            import pstats

            prof.disable()
            pstats.Stats(prof).sort_stats(os.environ.get("MAILR_SCALE_PROFILE_SORT", "cumulative")).print_stats(40)
        assert r.status_code == 200, r.text
        out[name] = (m, list(runs), list(background), r.json())
        _report(f"/today {name}", m, runs, background)
        if os.environ.get("MAILR_SCALE_HIST"):
            await _explain_slowest(m)

    body = out["warm"][3]
    assert out["cold"][3]["counts"] == body["counts"]
    # The same items as before the change: today's events without the cancelled and declined ones,
    # the open tasks due today or overdue (at most 50), the missed reminders, follow-ups, approvals.
    assert {e["uid"] for e in body["events"]} == expect["events"], body["events"]
    assert len(body["tasks"]) == 50 and {t["uid"] for t in body["tasks"]} <= expect["tasks"]
    assert body["counts"]["followups"] == 30 and body["counts"]["approvals"] == 12
    assert body["counts"]["reminders"] > 0 and all(x["missed"] for x in body["reminders"])
    assert "errors" not in body, body.get("errors")

    for name in ("cold", "warm"):
        m, inline, _bg, _ = out[name]
        # No CalDAV round trip waits inside the request: a stale account is refreshed in the background.
        budget(not inline, f"{name}: inline syncs {inline}")
        budget(m.sql <= BUDGET_TODAY_SQL, f"{name}: {m.sql} statements\n" + "\n".join(
            " ".join(s.split())[:160] for s in m.statements))
    budget(sorted(out["cold"][2]) == sorted(expect["connectors"]), out["cold"][2])
    # The follow-ups come from two partial indexes (ix_conv_nudged, ix_conv_follow_up), not from
    # a scan of every conversation of the user.
    m = out["warm"][0]
    fup = [(st, pr) for st, pr in zip(m.statements, m.params, strict=True)
           if "FROM conversations" in st and "nudged_at IS NOT NULL" in st]
    assert len(fup) == 1, m.statements
    plan = await _plan(*fup[0])
    budget("Seq Scan on conversations" not in plan, plan)


async def _plan(stmt: str, params) -> str:
    from mailr.db import engine

    async with engine().connect() as conn:
        raw = (await conn.get_raw_connection()).driver_connection
        rows = await raw.fetch("EXPLAIN " + stmt, *(params or ()))
    return "\n".join(r[0] for r in rows)
