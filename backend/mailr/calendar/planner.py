"""The server's reminder plan: `reminder_schedule` rows, and the firing job.

Planning reproduces Android's `ReminderPlan.plan` (data/reminders/ReminderPlan.kt):
- the horizon is now − 24 h to now + 8 days;
- cancelled and declined events, completed tasks and collections with `notify` off are skipped,
  and an event that has ended is not planned;
- offsets are the item's VALARMs, else the defaults of GET /reminders/settings (timed events
  `default_timed_event`, all-day events `default_all_day_event`, tasks at the due time, or at
  `morning_hour` on the day for a date-only due); an event marked "no reminders"
  (X-MAILR-NO-REMINDER, ical.NO_REMINDER) gets no default;
- keys are `e|<uid>|<occurrence>|<minutes>` and `t|<uid>|<due>|<minutes>` with the canonical
  strings (section 1).

Fire times: a timed event or task fires an exact duration before its start or due. An all-day
event or a date-only task fires at a wall-clock time in the user's zone: "1 day before at 9:00"
(1440 − 540 = 900 minutes before the day) is 9:00 local on the day before, also across a DST
change.

`plan_object` runs on every mirror upsert (mirror.upsert_object). `replan_horizon` (hourly)
plans the occurrences that enter the horizon. Snoozes and one-off reminders ("remind before")
are rows with source `snooze` / `oneoff` (`sync_states`). `due_reminders` (every minute)
queues `reminders.fire` at each row's exact time; the handler checks the state again
(handled or snoozed later: skipped), delivers SSE to open tabs and Web Push / opted-in push
targets, and sends a reminder more than 2 minutes late as quiet "missed".
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any

from icalendar import Calendar
from sqlalchemy import delete, or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import expand, ical
from mailr.core.jobs import handler
from mailr.models import CalCollection, CalObject, Connector, ReminderSchedule, ReminderState, User

log = logging.getLogger("mailr.calendar.planner")

HORIZON = timedelta(days=8)
LOOKBACK = timedelta(hours=24)
GRACE = timedelta(minutes=2)
MAX_OFFSET = timedelta(days=28)
KEEP_FIRED = timedelta(days=7)

DEFAULTS: dict[str, Any] = {"events": True, "tasks": True, "default_timed_event": 10, "default_all_day_event": None,
                            "default_task": True, "morning_hour": 9, "web": {"desktop": True, "push": False},
                            # A reminder that fires late (missed) is shown; a sound plays with a reminder.
                            "missed": True, "sound": False,
                            # Remind again: off by default; every 5, 10, 15 or 30 minutes,
                            # at most 3 times.
                            "repeat": {"enabled": False, "every_min": 10, "max": 3}}
REPEAT_EVERY = (5, 10, 15, 30)
REPEAT_MAX = 3
# An event stops repeating this long after its start (Android's FULL_SCREEN_WINDOW_MS).
REPEAT_EVENT_WINDOW = timedelta(minutes=15)


# ---------------- settings ----------------


def _offset(v: Any, default: int | None) -> int | None:
    if v is None:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return n if -1440 <= n <= 60 * 24 * 28 else default


def repeat_of(raw: Any) -> dict[str, Any]:
    """The `repeat` setting with defaults: every_min one of 5/10/15/30, max 1 to 3."""
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS["repeat"])
    out["enabled"] = bool(raw.get("enabled", False))
    try:
        every = int(raw.get("every_min", out["every_min"]))
    except (TypeError, ValueError):
        every = out["every_min"]
    out["every_min"] = every if every in REPEAT_EVERY else DEFAULTS["repeat"]["every_min"]
    try:
        out["max"] = min(REPEAT_MAX, max(1, int(raw.get("max", REPEAT_MAX))))
    except (TypeError, ValueError):
        out["max"] = REPEAT_MAX
    return out


def settings_of(user: User | None) -> dict[str, Any]:
    """The user's reminder settings (GET /reminders/settings), defaults filled in."""
    settings = (user.settings if user else None) or {}
    raw = settings.get("reminders") or {}
    web = {**DEFAULTS["web"], **{k: bool(v) for k, v in (raw.get("web") or {}).items() if k in DEFAULTS["web"]}}
    # The reminder settings' own hour, else the flat `morning_hour` of Settings → Calendar.
    hour = raw.get("morning_hour", settings.get("morning_hour", DEFAULTS["morning_hour"]))
    try:
        hour = min(23, max(0, int(hour)))
    except (TypeError, ValueError):
        hour = DEFAULTS["morning_hour"]
    return {"events": bool(raw.get("events", True)), "tasks": bool(raw.get("tasks", True)),
            "default_timed_event": _offset(raw.get("default_timed_event", 10), 10),
            "default_all_day_event": _offset(raw.get("default_all_day_event"), None),
            "default_task": bool(raw.get("default_task", True)), "morning_hour": hour, "web": web,
            "missed": bool(raw.get("missed", True)), "sound": bool(raw.get("sound", False)),
            "repeat": repeat_of(raw.get("repeat"))}


def _clock(minutes: int) -> str:
    return f"{minutes // 60}:{minutes % 60:02d}"


def offset_label(minutes: int, all_day: bool = False) -> str:
    """A reminder offset in words: "1 day before", "30 minutes before"; for an all-day item the
    wall-clock time too: "1 day before at 9:00", "on the day at 9:00"."""
    if all_day:
        days = -(-minutes // 1440)  # the day it falls on, counted back from the event's day
        at = _clock(days * 1440 - minutes)
        return f"on the day at {at}" if days <= 0 else f"{days} day{'s' if days != 1 else ''} before at {at}"
    if minutes == 0:
        return "at the start"
    n, after = abs(minutes), minutes < 0
    if n % 1440 == 0:
        txt = f"{n // 1440} day{'s' if n != 1440 else ''}"
    elif n % 60 == 0:
        txt = f"{n // 60} hour{'s' if n != 60 else ''}"
    else:
        txt = f"{n} minute{'s' if n != 1 else ''}"
    return f"{txt} {'after' if after else 'before'}"


def event_defaults(user: User | None) -> dict[str, Any]:
    """The reminders a new event gets when its create names none and its calendar has no
    `default_reminders` of its own: the user's `default_timed_event`
    and `default_all_day_event` as lists of minutes, with words for each. A default of none,
    or event reminders turned off, gives []."""
    cfg = settings_of(user)
    on = cfg["events"]
    timed = [cfg["default_timed_event"]] if on and cfg["default_timed_event"] is not None else []
    all_day = [cfg["default_all_day_event"]] if on and cfg["default_all_day_event"] is not None else []
    return {"timed": timed, "all_day": all_day,
            "timed_text": ", ".join(offset_label(m) for m in timed) or "none",
            "all_day_text": ", ".join(offset_label(m, True) for m in all_day) or "none"}


# ---------------- the plan ----------------


@dataclass
class Planned:
    key: str
    kind: str  # e | t
    uid: str
    occurrence: str
    minutes: int
    fire_at: datetime
    anchor_at: datetime
    end_at: datetime | None
    title: str
    detail: str
    source: str  # valarm | default


def fire_time(anchor: datetime | date, minutes: int, zone: tzinfo) -> datetime:
    """When a reminder `minutes` before `anchor` fires. A date anchors at local midnight and the
    offset is counted on the wall clock in `zone`; a timed anchor is an exact duration."""
    if isinstance(anchor, datetime):
        a = anchor if anchor.tzinfo else anchor.replace(tzinfo=UTC)
        return (a - timedelta(minutes=minutes)).astimezone(UTC)
    local = datetime.combine(anchor, time(0)) - timedelta(minutes=minutes)
    return local.replace(tzinfo=zone).astimezone(UTC)


def _midnight(d: date, zone: tzinfo) -> datetime:
    return datetime.combine(d, time(0)).replace(tzinfo=zone).astimezone(UTC)


def clock24(user: User | None) -> bool:
    """The user's clock (Settings → You → Region): 24-hour only when set so, as the other texts the
    server writes (agent/present.py, meeting_prep.digest_line)."""
    return ((user.settings if user else None) or {}).get("time_format") == "24h"


def _hm(d: datetime, zone: tzinfo, h24: bool = True) -> str:
    t = d.astimezone(zone)
    return t.strftime("%H:%M") if h24 else f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def event_detail(start: datetime, end: datetime, all_day: bool, location: str, zone: tzinfo,
                 h24: bool = True, lang: str | None = None) -> str:
    """"10:00 – 11:00 · Room 4", "10:00 – 11:00 AM · Room 4", "All day · Room 4" (Android's eventDetail),
    in the user's language."""
    from mailr import i18n

    where = f" · {location}" if location.strip() else ""
    if all_day:
        return i18n.t("reminders.detail.all_day", lang) + where
    s, e = start.astimezone(zone), end.astimezone(zone)
    if e.date() != s.date():
        return _hm(s, zone, h24) + where
    a, b = _hm(s, zone, h24), _hm(e, zone, h24)
    if not h24 and a[-2:] == b[-2:]:
        a = a[:-3]  # "4:15 – 4:45 PM", as the web's timeSpan
    return f"{a} – {b}" + where


def day_word(d: date, today: date, lang: str | None = None) -> str:
    from mailr import i18n

    for n, word in ((0, "today"), (1, "tomorrow"), (-1, "yesterday")):
        if d == today + timedelta(days=n):
            return i18n.t(f"reminders.detail.{word}", lang)
    return i18n.t("reminders.detail.date", lang, weekday=i18n.t(f"mail.date.weekday.{d.weekday()}", lang),
                  month=i18n.t(f"mail.date.month.{d.month}", lang), day=d.day)


def task_detail(due: datetime | date, zone: tzinfo, now: datetime, h24: bool = True, lang: str | None = None) -> str:
    from mailr import i18n

    today = now.astimezone(zone).date()
    if not isinstance(due, datetime):
        return i18n.t("reminders.detail.due", lang, when=day_word(due, today, lang))
    d = due.astimezone(zone)
    return i18n.t("reminders.detail.due_at", lang, when=day_word(d.date(), today, lang), time=_hm(d, zone, h24))


def _in_window(fire: datetime, now: datetime) -> bool:
    return now - LOOKBACK <= fire <= now + HORIZON


def plan_event(ics: str, uid: str, cfg: dict[str, Any], now: datetime, zone: tzinfo, me: set[str]) -> list[Planned]:
    """Reminders of one event resource (every occurrence near the horizon)."""
    if not cfg["events"]:
        return []
    try:
        cal = Calendar.from_ical(ics)
    except Exception as e:  # noqa: BLE001
        # The event gets no reminders: say so, with the uid, rather than drop them silently.
        log.warning("event %s: calendar data does not parse, no reminders planned: %s", uid, e)
        return []
    insts, _ = expand.instances(cal, now - LOOKBACK - timedelta(days=1), now + HORIZON + MAX_OFFSET, zone)
    out: list[Planned] = []
    for i in insts:
        comp = i.comp
        if i.cancelled:
            continue
        mine = next((a for a in ical.as_list(comp.get("attendee")) if ical.addr(a).lower() in me), None)
        if mine is not None and str(mine.params.get("PARTSTAT", "")).upper() == "DECLINED":
            continue
        start = _midnight(i.start, zone) if i.all_day else i.start_at
        if i.all_day:
            last = i.end if isinstance(i.end, date) and i.end > i.start else i.start + timedelta(days=1)
            end = _midnight(last, zone)
        else:
            end = i.end_at if i.end_at > i.start_at else i.start_at + timedelta(hours=1)
        if end <= now:
            continue
        alarms = ical.alarm_minutes(comp, getattr(comp.get("dtstart"), "dt", None), getattr(comp.get("dtend"), "dt", None))
        source = "valarm"
        # Marked "no reminders" (ical.NO_REMINDER): the user chose none, so no fallback either.
        if not alarms and not ical.no_reminder(comp):
            d = cfg["default_all_day_event"] if i.all_day else cfg["default_timed_event"]
            alarms, source = ([d] if d is not None else []), "default"
        title = ical.text_of(comp, "summary").strip() or "(no title)"
        detail = event_detail(start, end, i.all_day, ical.text_of(comp, "location"), zone, cfg.get("clock24", True),
                              cfg.get("lang"))
        for m in dict.fromkeys(alarms):
            fire = fire_time(i.start if i.all_day else i.start_at, m, zone)
            if not _in_window(fire, now):
                continue
            out.append(Planned(f"e|{uid}|{i.occurrence}|{m}", "e", uid, i.occurrence, m, fire, start, end, title,
                               detail, source))
    return out


def absolute_minutes(ics: str | None, due: datetime | date, zone: tzinfo) -> list[int]:
    """A task's absolute reminders (TRIGGER;VALUE=DATE-TIME) as minutes before its due, with no
    bound (Android's ReminderPlan.absoluteMinutes; a date due counts from local midnight). The
    mirror's `alarms` (ical.alarm_minutes) keeps only offsets from a day after the due to 28 days
    before it, so "remind me on Wednesday at 6:00" on a task due last Friday was stored and never
    fired (chat of 10 Oct 2026)."""
    anchor = _midnight(due, zone) if not isinstance(due, datetime) else (
        due if due.tzinfo else due.replace(tzinfo=UTC))
    # Truncated, as Kotlin's Long division.
    return [int((anchor - t).total_seconds() / 60) for t in _absolute_instants(ics)]


def _absolute_instants(ics: str | None) -> list[datetime]:
    """The TRIGGER;VALUE=DATE-TIME moments of a task's VALARMs (naive ones read as UTC)."""
    if not ics or "VALUE=DATE-TIME" not in ics:
        return []
    try:
        cal = Calendar.from_ical(ics)
    except Exception:  # noqa: BLE001  the mirror's own offsets still apply
        return []
    out: list[datetime] = []
    for comp in cal.walk("VTODO"):
        for al in comp.subcomponents:
            v = getattr(al.get("trigger"), "dt", None) if getattr(al, "name", "") == "VALARM" else None
            if isinstance(v, datetime):
                out.append(v if v.tzinfo else v.replace(tzinfo=UTC))
        break
    return out


def task_offsets(obj: CalObject, cfg: dict[str, Any], zone: tzinfo) -> tuple[datetime | date | None, list[int], str]:
    """(due, minutes before the due of each reminder, source) of an open task: its VALARMs,
    relative and absolute, else the default reminder at the due (9 am for a date due)."""
    if not cfg["tasks"] or (obj.status or "").upper() == "COMPLETED" or obj.completed_at is not None:
        return None, [], "valarm"
    due: datetime | date | None = obj.due_date if obj.due_date is not None else obj.due_at
    if due is None:
        return None, [], "valarm"
    alarms = list(dict.fromkeys([*(obj.alarms or []), *absolute_minutes(obj.ics, due, zone)]))
    if alarms:
        return due, alarms, "valarm"
    date_only = not isinstance(due, datetime)
    return due, ([-cfg["morning_hour"] * 60 if date_only else 0] if cfg["default_task"] else []), "default"


def task_fire_times(obj: CalObject, cfg: dict[str, Any], zone: tzinfo, now: datetime) -> list[tuple[datetime, int, str]]:
    """Every reminder of a task still to fire (fire time, minutes, source), soonest first, with no
    horizon: what the assistant's task tools report as saved (agent/task_tools.py)."""
    due, offsets, source = task_offsets(obj, cfg, zone)
    if due is None:
        return []
    exact = _absolute_instants(obj.ics)
    out = []
    for m in offsets:
        f = fire_time(due, m, zone)
        # Offsets are whole minutes: a due with seconds moves an absolute reminder by less than a
        # minute. The report gives the time the user asked for.
        f = next((x for x in exact if abs((x - f).total_seconds()) < 60), f)
        if f > now:
            out.append((f, m, source))
    return sorted(out)


def plan_task(obj: CalObject, cfg: dict[str, Any], now: datetime, zone: tzinfo) -> list[Planned]:
    due, alarms, source = task_offsets(obj, cfg, zone)
    if due is None:
        return []
    date_only = not isinstance(due, datetime)
    anchor = _midnight(due, zone) if date_only else due.astimezone(UTC)
    due_key = ical.canonical_occurrence(due)
    title = (obj.summary or "").strip() or "(untitled task)"
    out = []
    for m in alarms:
        fire = fire_time(due, m, zone)
        if not _in_window(fire, now):
            continue
        out.append(Planned(f"t|{obj.uid}|{due_key}|{m}", "t", obj.uid, due_key, m, fire, anchor, None, title,
                           task_detail(due, zone, now, cfg.get("clock24", True), cfg.get("lang")), source))
    return out


async def _context(db: AsyncSession, user_id: int) -> tuple[dict[str, Any], tzinfo, set[str]]:
    from mailr.calendar import mirror
    from mailr.calendar import tz as tzmod

    user = await db.get(User, user_id)
    # The details are written in the user's clock (12 or 24 hours); a change of it replans.
    from mailr import i18n

    cfg = {**settings_of(user), "clock24": clock24(user), "lang": i18n.user_language(user) if user else None}
    return cfg, await tzmod.user_zone(db, user), await mirror.my_addresses(db, user_id)


async def plan_object(db: AsyncSession, obj: CalObject, *, now: datetime | None = None,
                      ctx: tuple[dict[str, Any], tzinfo, set[str]] | None = None) -> list[Planned]:
    """Re-plan one mirror row: upsert its rows in reminder_schedule and drop its unfired planned
    rows that are no longer in the plan. Snooze and one-off rows are left alone."""
    now = now or datetime.now(UTC)
    cfg, zone, me = ctx or await _context(db, obj.user_id)
    coll = await db.get(CalCollection, obj.collection_id)
    planned: list[Planned] = []
    if obj.deleted_at is None and coll is not None and coll.notify and coll.deleted_at is None:
        if obj.kind == "event":
            # Expanding the rule runs off the event loop.
            planned = await asyncio.to_thread(plan_event, obj.ics, obj.uid, cfg, now, zone, me)
        elif obj.kind == "task":
            planned = plan_task(obj, cfg, now, zone)
    conn = await db.get(Connector, obj.connector_id)
    local_only = bool(conn and conn.data_class == "local_only")
    keys = [p.key for p in planned]
    rows = {r.key: r for r in (await db.execute(select(ReminderSchedule).where(
        ReminderSchedule.user_id == obj.user_id,
        or_(ReminderSchedule.object_id == obj.id, ReminderSchedule.key.in_(keys or [""]))))).scalars()}
    for p in planned:
        r = rows.get(p.key)
        if r is None:
            db.add(ReminderSchedule(user_id=obj.user_id, key=p.key, kind=p.kind, object_id=obj.id, uid=p.uid,
                                    occurrence=p.occurrence, minutes=p.minutes, anchor_at=p.anchor_at,
                                    fire_at=p.fire_at, title=p.title, detail=p.detail, source=p.source,
                                    local_only=local_only, planned_at=now))
            continue
        r.object_id, r.title, r.detail, r.anchor_at, r.local_only = obj.id, p.title, p.detail, p.anchor_at, local_only
        if r.source in ("snooze", "oneoff"):
            continue
        if r.fire_at != p.fire_at:
            # The time moved (an override, or new settings): fires again unless it already
            # fired for the new time.
            r.fire_at, r.fired_at = p.fire_at, None
        r.source, r.planned_at = p.source, now
    gone = [k for k, r in rows.items() if k not in set(keys) and r.object_id == obj.id and r.fired_at is None
            and r.source in ("valarm", "default")]
    if gone:
        await db.execute(delete(ReminderSchedule).where(ReminderSchedule.user_id == obj.user_id,
                                                        ReminderSchedule.key.in_(gone)))
    await db.flush()
    return planned


async def replan_user(db: AsyncSession, user_id: int, *, now: datetime | None = None) -> int:
    """Plan every live event and task of the user that may remind inside the horizon."""
    now = now or datetime.now(UTC)
    lo, hi = now - LOOKBACK - timedelta(days=2), now + HORIZON + MAX_OFFSET
    ids = (await db.execute(text(
        "SELECT o.id FROM cal_objects o WHERE o.user_id=:u AND o.deleted_at IS NULL AND ("
        " (o.kind='task' AND (o.due_at BETWEEN :lo AND :hi))"
        # An absolute reminder may fire long after (or before) the due: such tasks are planned too.
        " OR (o.kind='task' AND o.completed_at IS NULL AND o.ics LIKE '%VALUE=DATE-TIME%'"
        "     AND (o.due_at IS NOT NULL OR o.due_date IS NOT NULL))"
        " OR (o.kind='event' AND EXISTS (SELECT 1 FROM cal_occurrences c WHERE c.object_id=o.id"
        "      AND c.start_at BETWEEN :lo AND :hi))"
        " OR (o.kind='event' AND o.recurring AND (o.expanded_until IS NULL OR o.expanded_until < :hi)))"),
        {"u": user_id, "lo": lo, "hi": hi})).scalars().all()
    ctx = await _context(db, user_id)
    for oid in ids:
        obj = await db.get(CalObject, oid)
        if obj is not None:
            await plan_object(db, obj, now=now, ctx=ctx)
    return len(ids)


@handler("calendar.replan_zone")
async def replan_zone(payload: dict[str, Any], ctx) -> None:
    """The browser's zone changed while Region is Automatic (calendar/tz.py note_device_zone)."""
    from mailr.db import session_scope

    async with session_scope() as db:
        await replan_user(db, int(payload["user_id"]))


async def replan_horizon() -> None:
    """Periodic (hourly): plan the occurrences entering the horizon; drop old fired rows."""
    from mailr.db import session_scope

    async with session_scope() as db:
        users = (await db.execute(text(
            "SELECT DISTINCT user_id FROM cal_objects WHERE deleted_at IS NULL"))).scalars().all()
        await db.execute(delete(ReminderSchedule).where(
            ReminderSchedule.fire_at < datetime.now(UTC) - KEEP_FIRED))
    for uid in users:
        try:
            async with session_scope() as db:
                await replan_user(db, uid)
        except Exception:  # one user's broken data must not stop the others
            log.exception("reminder replan failed for user %s", uid)


# ---------------- snoozes and one-off reminders ----------------


async def sync_states(db: AsyncSession, user_id: int, rows: list[ReminderState]) -> None:
    """Schedule rows for reminder states that just changed: a snooze fires at its time, a one-off
    "remind before" fires `minutes` before the occurrence; an undo plans the item again."""
    from mailr.calendar import mirror
    from mailr.calendar import tz as tzmod

    zone = await tzmod.user_zone(db, user_id)
    replan: set[int] = set()
    for st in rows:
        r = (await db.execute(select(ReminderSchedule).where(ReminderSchedule.user_id == user_id,
                                                              ReminderSchedule.key == st.key))).scalar_one_or_none()
        if st.kind == "n":
            await _sync_note_state(db, user_id, st, r)
            continue
        obj = await mirror.find_by_uid(db, user_id, st.uid, "event" if st.kind == "e" else "task")
        fire: datetime | None = None
        source = None
        if st.state == "snoozed" and st.snoozed_until is not None:
            fire, source = st.snoozed_until, "snooze"
        elif st.state == "pending" and st.oneoff:
            anchor = ical.parse_when(st.occurrence, zone) if st.occurrence else None
            if anchor is not None:
                fire, source = fire_time(anchor, st.minutes, zone), "oneoff"
        elif st.state == "pending" and r is not None and r.source == "snooze" and obj is not None:
            replan.add(obj.id)  # an undone snooze: back to the planned time
            await db.delete(r)
            continue
        if fire is None:
            continue
        if r is None:
            r = ReminderSchedule(user_id=user_id, key=st.key, kind=st.kind, uid=st.uid, occurrence=st.occurrence,
                                 minutes=st.minutes)
            db.add(r)
        if obj is not None:
            r.object_id = obj.id
            if not r.title:
                r.title = obj.summary or ("(no title)" if st.kind == "e" else "(untitled task)")
            conn = await db.get(Connector, obj.connector_id)
            r.local_only = bool(conn and conn.data_class == "local_only")
        if r.anchor_at is None and st.occurrence:
            a = ical.parse_when(st.occurrence, zone)
            r.anchor_at = a if isinstance(a, datetime) else _midnight(a, zone)
        r.fire_at, r.fired_at, r.source, r.planned_at = fire, None, source, datetime.now(UTC)
    await db.flush()
    for oid in replan:
        obj = await db.get(CalObject, oid)
        if obj is not None:
            await plan_object(db, obj)


async def _sync_note_state(db: AsyncSession, user_id: int, st: ReminderState, r: ReminderSchedule | None) -> None:
    """A note reminder's state changed (notes/reminders.py): a snooze fires at its time; an undone
    snooze goes back to the reminder's own time. Handled needs no row change: the firing job and
    the tray read the state."""
    at = ical.parse_when(st.occurrence)
    if not isinstance(at, datetime):
        return
    if st.state == "snoozed" and st.snoozed_until is not None:
        fire, source = st.snoozed_until, "snooze"
    elif st.state == "pending" and r is not None and r.source == "snooze":
        fire, source = at, "oneoff"
    else:
        return
    if r is None:
        from mailr.notes.models import Note
        from mailr.notes.reminders import DETAIL, title_of

        note = (await db.execute(select(Note).where(Note.pid == st.uid))).scalar_one_or_none()
        if note is None:
            return
        r = ReminderSchedule(user_id=user_id, key=st.key, kind="n", uid=st.uid, occurrence=st.occurrence, minutes=0,
                             anchor_at=at, title=title_of(note), detail=DETAIL.get(note.kind, "Note reminder"),
                             local_only=bool(note.local_only))
        db.add(r)
    r.fire_at, r.fired_at, r.source, r.planned_at = fire, None, source, datetime.now(UTC)
    await db.flush()


# ---------------- firing ----------------


async def due_reminders() -> int:
    """Periodic (every minute): queue `reminders.fire` for each row due within two minutes, at
    its exact time."""
    from mailr.core.jobs import enqueue
    from mailr.db import session_scope

    now = datetime.now(UTC)
    async with session_scope() as db:
        rows = (await db.execute(select(ReminderSchedule).where(
            ReminderSchedule.fired_at.is_(None), ReminderSchedule.fire_at <= now + GRACE))).scalars().all()
        for r in rows:
            await enqueue(db, "reminders.fire", {"id": r.id, "fire_at": r.fire_at.isoformat()}, user_id=r.user_id,
                          run_at=max(r.fire_at, now - timedelta(seconds=1)),
                          dedupe_key=f"rem:{r.user_id}:{r.key}:{int(r.fire_at.timestamp())}")
        return len(rows)


def click_url(kind: str, uid: str, occurrence: str) -> str:
    if kind == "n":
        return f"/notes/n/{uid}"
    return f"/calendar/event/{uid}/{occurrence}" if kind == "e" else f"/tasks/task/{uid}"


def actions_for(kind: str) -> list[str]:
    # A note reminder has nothing to complete: Snooze and Dismiss.
    return ["snooze", "dismiss"] if kind == "n" else ["snooze", "done", "dismiss"]


ITEM_KIND = {"e": "event", "t": "task", "n": "note"}
# The title of a local-only reminder's push: catalogue push.reminder.generic.<kind> (mailr/i18n/en.json).
GENERIC_TITLE = {"e": "push.reminder.generic.event", "t": "push.reminder.generic.task", "n": "push.reminder.generic.note"}
GENERIC_CLICK = {"e": "/calendar", "t": "/tasks", "n": "/notes"}


# Meeting providers: a meeting link is an https URL whose host is one of these or a subdomain of
# one ("us02web.zoom.us"). The name anywhere else in the URL does not count, so
# "https://zoom.us.example.net/j/1" is not one (the same rule as eventInfo.ts and Android's JoinLink).
CONFERENCE_HOSTS = ("meet.google.com", "zoom.us", "teams.microsoft.com", "teams.live.com", "meet.jit.si",
                    "whereby.com", "webex.com", "bluejeans.com", "gotomeeting.com")
_HTTPS_URL = re.compile(r"https://[^\s<>\"]+", re.IGNORECASE)


def meeting_provider(url: str) -> str | None:
    """The provider domain of an https meeting link, else None."""
    from urllib.parse import urlsplit

    try:
        u = urlsplit(url.strip())
        host = (u.hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if u.scheme.lower() != "https" or not host:
        return None
    return next((d for d in CONFERENCE_HOSTS if host == d or host.endswith("." + d)), None)


def join_url(url: str | None, location: str | None, description: str | None) -> str | None:
    """The conference link, as the web finds it (eventInfo.ts): the URL property when it is a
    meeting link, else the first meeting link in the location or the description."""
    if url and meeting_provider(url):
        return url.strip()
    for text_ in (location, description):
        for m in _HTTPS_URL.finditer(text_ or ""):
            if meeting_provider(m.group(0)):
                return m.group(0)
    return None


async def payload_for(db: AsyncSession, r: ReminderSchedule, *, missed: bool, sound: bool = False
                      ) -> dict[str, Any]:
    """The notification (section 9), as SSE sends it."""
    out = {"type": "notification", "kind": "reminder", "key": r.key, "title": r.title, "body": r.detail,
           "start": ical.out_when(r.anchor_at),
           "item": {"kind": ITEM_KIND.get(r.kind, "task"), "uid": r.uid, "occurrence": r.occurrence},
           "click": click_url(r.kind, r.uid, r.occurrence), "missed": missed, "actions": actions_for(r.kind),
           "sound": sound}
    obj = await db.get(CalObject, r.object_id) if r.kind == "e" and r.object_id else None
    if obj is not None:
        from mailr.calendar import mirror

        f = (mirror.override_fields(obj, UTC, {}).get(r.occurrence) or {}) if obj.recurring else {}
        link = join_url(f.get("url") or obj.url, f.get("location") or obj.location,
                        f.get("description") or obj.description)
        if link:
            out["join_url"] = link
        # ACT event.prep @web.notification: an event with guests offers its meeting prep pack (Prep).
        if obj.attendee_count:
            from mailr.calendar.meeting_prep import prep_path

            out["prep"] = prep_path(r.uid, r.occurrence)
    return out


@dataclass
class _Loaded:
    """The objects, collections and occurrences a list of schedule rows points at, read in three
    statements (active() would otherwise read them row by row: 185 statements for 60 fired
    reminders, measured 14 Oct 2026)."""
    objects: dict[int, CalObject]
    collections: dict[int, CalCollection]
    occurrences: dict[tuple[int, str], Any]


async def _load(db: AsyncSession, rows: list[ReminderSchedule]) -> _Loaded:
    from mailr.models import CalOccurrence

    ids = list({r.object_id for r in rows if r.object_id and r.kind != "n"})
    objects = {o.id: o for o in (await db.execute(select(CalObject).where(CalObject.id.in_(ids)))).scalars()} \
        if ids else {}
    cids = list({o.collection_id for o in objects.values()})
    collections = {c.id: c for c in (await db.execute(select(CalCollection).where(
        CalCollection.id.in_(cids)))).scalars()} if cids else {}
    pairs = {(r.object_id, r.occurrence) for r in rows if r.object_id in objects and objects[r.object_id].kind != "task"}
    occurrences: dict[tuple[int, str], Any] = {}
    if pairs:
        for occ in (await db.execute(select(CalOccurrence).where(
                tuple_(CalOccurrence.object_id, CalOccurrence.occurrence).in_(list(pairs))))).scalars():
            occurrences[(occ.object_id, occ.occurrence)] = occ
    return _Loaded(objects, collections, occurrences)


async def _still_relevant(db: AsyncSession, r: ReminderSchedule, cfg: dict[str, Any], now: datetime,
                          loaded: _Loaded | None = None) -> bool:
    """The item still exists, still reminds, and (an event) has not ended. `loaded`: the rows
    read ahead by _load (no statement per reminder)."""
    if r.kind == "n":  # a note reminder (notes/reminders.py): the per-kind switches are for events and tasks
        from mailr.notes.reminders import relevant

        return await relevant(db, r)
    if (r.kind == "e" and not cfg["events"]) or (r.kind == "t" and not cfg["tasks"]):
        return False
    from mailr.models import CalOccurrence

    if loaded is not None:
        obj = loaded.objects.get(r.object_id) if r.object_id else None
    else:
        obj = await db.get(CalObject, r.object_id) if r.object_id else None
    if obj is None or obj.deleted_at is not None:
        return r.source == "oneoff" and r.object_id is None
    coll = loaded.collections.get(obj.collection_id) if loaded is not None else \
        await db.get(CalCollection, obj.collection_id)
    if coll is None or not coll.notify:
        return False
    if obj.kind == "task":
        return (obj.status or "").upper() != "COMPLETED" and obj.completed_at is None
    occ = loaded.occurrences.get((obj.id, r.occurrence)) if loaded is not None else \
        await db.get(CalOccurrence, (obj.id, r.occurrence))
    if occ is not None:
        if occ.cancelled:
            return False
        end = occ.end_at if occ.end_at > occ.start_at else occ.start_at + timedelta(hours=1)
        if occ.all_day:
            end = max(end, occ.start_at + timedelta(days=1))
        return end > now
    return (r.anchor_at or r.fire_at) + timedelta(hours=1) > now


@handler("reminders.fire")
async def fire(payload: dict[str, Any], ctx) -> None:
    """ACT reminder.fire @web.notification: deliver one due reminder (SSE to open tabs, Web Push and opted-in push
    targets). A reminder more than GRACE late goes out as quiet "missed". A reminder on time
    queues its first repeat when Remind again is on."""
    from mailr.db import session_scope

    async with session_scope() as db:
        r = await db.get(ReminderSchedule, payload["id"])
        if r is None or r.fired_at is not None or r.fire_at.isoformat() != payload.get("fire_at"):
            return  # gone, fired, or moved (a newer job carries the new time)
        now = datetime.now(UTC)
        st = (await db.execute(select(ReminderState).where(ReminderState.user_id == r.user_id,
                                                           ReminderState.key == r.key))).scalar_one_or_none()
        if st is not None and st.state == "handled":
            r.fired_at = now
            return
        if st is not None and st.state == "snoozed" and st.snoozed_until and st.snoozed_until > now + GRACE \
                and r.source != "snooze":
            return  # the snooze row fires at its own time
        user = await db.get(User, r.user_id)
        cfg = settings_of(user)
        if not await _still_relevant(db, r, cfg, now):
            r.fired_at = now
            return
        missed = now - r.fire_at > GRACE
        if missed and not cfg["missed"]:  # the user does not want late reminders shown
            r.fired_at = now
            if st is not None:
                st.fired_at = now
            return
        await _deliver(db, r, cfg, missed=missed)
        r.fired_at = now
        if st is not None:
            st.fired_at = now
        if not missed:
            await _queue_repeat(db, r, cfg, n=1, now=now)


async def _deliver(db: AsyncSession, r: ReminderSchedule, cfg: dict[str, Any], *, missed: bool,
                   again: bool = False) -> None:
    """SSE to open tabs (when desktop notifications are on), and push to the targets that opted
    in to the `reminder` event: Web Push and ntfy, never a UnifiedPush phone."""
    from mailr.calendar import reminders
    from mailr.core import push
    from mailr.core.events import notify

    data = await payload_for(db, r, missed=missed, sound=cfg["sound"])
    if again:
        data["again"] = True
    if cfg["web"]["desktop"]:
        await notify(db, r.user_id, data)
    from mailr import i18n
    from mailr.models import User

    lang = i18n.user_language(await db.get(User, r.user_id))
    if r.local_only:  # nothing of a local-only record leaves the server
        title, body = i18n.t(GENERIC_TITLE.get(r.kind, "push.reminder.generic.other"), lang), ""
    else:
        title, body = (i18n.t("push.reminder.missed", lang, title=r.title) if missed else r.title), r.detail
    push_data = {k: v for k, v in data.items() if k not in ("type", "title", "body")}
    if r.local_only:
        push_data.pop("join_url", None)
        push_data.pop("prep", None)
        push_data["item"] = {"kind": push_data["item"]["kind"]}
        push_data["click"] = GENERIC_CLICK.get(r.kind, "/")
        push_data["key"] = r.key  # the tag; it names no content
    try:
        await push.fan_out(db, r.user_id, "reminder", title, body, push_data,
                           "low" if missed else "high",
                           webpush_extra={"tokens": reminders.act_tokens(r.user_id, r.key)})
    except Exception as e:  # noqa: BLE001  a push outage must not lose the SSE notification
        log.info("reminder push failed: %s", e)


# ---------------- Remind again ----------------


async def _queue_repeat(db: AsyncSession, r: ReminderSchedule, cfg: dict[str, Any], *, n: int,
                        now: datetime) -> None:
    """Queue repeat `n` of a reminder that just alerted, when Remind again is on."""
    from mailr.core.jobs import enqueue

    rep = cfg["repeat"]
    if not rep["enabled"] or n > rep["max"]:
        return
    at = now + timedelta(minutes=rep["every_min"])
    await enqueue(db, "reminders.repeat", {"id": r.id, "fired_at": r.fired_at.isoformat(), "n": n,
                                           "at": at.isoformat()},
                  user_id=r.user_id, run_at=at, dedupe_key=f"remrep:{r.user_id}:{r.key}:{int(now.timestamp())}:{n}")


def may_repeat(r: ReminderSchedule, st: ReminderState | None, now: datetime) -> bool:
    """A fired reminder may alert again while nobody has acted on it: not handled or snoozed on
    any device, not put back by an undo since it fired, and (an event) less than 15 minutes past
    its start. Missed reminders never get here."""
    if st is not None:
        if st.state == "handled":
            return False
        if st.state == "snoozed" and st.snoozed_until and st.snoozed_until > now:
            return False  # snoozed again: the snooze row fires (and repeats) at its own time
        if st.updated_at and r.fired_at and st.updated_at > r.fired_at:
            return False  # acted on since it fired (an undo puts it back as pending)
    if r.kind == "e":
        return (r.anchor_at or r.fire_at) + REPEAT_EVENT_WINDOW > now
    return True


@handler("reminders.repeat")
async def repeat(payload: dict[str, Any], ctx) -> None:
    # ACT reminder.repeat @web.notification
    """ACT reminder.repeat: alert again about a reminder nobody acted on, every few minutes, at
    most `max` times (Remind again in GET /reminders/settings). Same key, so the notification
    replaces itself. Only the web gets these (SSE and opted-in push targets); the phone repeats
    on its own alarm. A repeat that runs late is dropped: missed reminders never repeat."""
    from mailr.db import session_scope

    async with session_scope() as db:
        r = await db.get(ReminderSchedule, payload["id"])
        if r is None or r.fired_at is None or r.fired_at.isoformat() != payload.get("fired_at"):
            return  # gone, or moved and fired again (a new chain started)
        now = datetime.now(UTC)
        try:
            due = datetime.fromisoformat(payload["at"])
        except (KeyError, TypeError, ValueError):
            due = now
        if now - due > GRACE:
            return  # the server was down: a late repeat is not sent
        cfg = settings_of(await db.get(User, r.user_id))
        n = int(payload.get("n") or 1)
        if not cfg["repeat"]["enabled"] or n > cfg["repeat"]["max"]:
            return
        st = (await db.execute(select(ReminderState).where(ReminderState.user_id == r.user_id,
                                                           ReminderState.key == r.key))).scalar_one_or_none()
        if not may_repeat(r, st, now) or not await _still_relevant(db, r, cfg, now):
            return
        await _deliver(db, r, cfg, missed=False, again=True)
        await _queue_repeat(db, r, cfg, n=n + 1, now=now)


# ---------------- the web tray ----------------


async def active(db: AsyncSession, user_id: int, p: Any = None, now: datetime | None = None) -> list[dict[str, Any]]:
    """GET /reminders/active: fired reminders not handled, and snoozes that have come due."""
    now = now or datetime.now(UTC)
    rows = (await db.execute(
        select(ReminderSchedule, ReminderState)
        .outerjoin(ReminderState, (ReminderState.user_id == ReminderSchedule.user_id)
                   & (ReminderState.key == ReminderSchedule.key))
        .where(ReminderSchedule.user_id == user_id,
               or_(ReminderSchedule.fired_at.is_not(None),
                   (ReminderState.state == "snoozed") & (ReminderState.snoozed_until <= now)))
        .order_by(ReminderSchedule.fire_at))).all()
    cfg = settings_of(await db.get(User, user_id))
    visible: set[int] | None = None
    if p is not None and getattr(p, "token", None) is not None:
        from mailr.calendar import mirror

        visible = set(await mirror.visible_connector_ids(db, user_id, p))
    out = []
    rows = [(r, st) for r, st in rows
            if not (st is not None and (st.state == "handled" or (st.state == "snoozed" and st.snoozed_until
                                                                   and st.snoozed_until > now)))]
    loaded = await _load(db, [r for r, _ in rows])
    for r, st in rows:
        # A reminder put back by an undo after it fired (state pending, changed after the firing) is
        # listed again: the undo restores the tray. It does not alert again (may_repeat).
        if not await _still_relevant(db, r, cfg, now, loaded) and not (r.kind == "t"):
            continue
        if r.kind == "n":
            from mailr.notes.reminders import visible_to

            if p is not None and getattr(p, "token", None) is not None and not await visible_to(db, p, r.uid):
                continue
            obj = None
        else:
            obj = loaded.objects.get(r.object_id) if r.object_id else None
            if visible is not None and (obj is None or obj.connector_id not in visible):
                continue
        extra = {}
        if r.kind == "e" and obj is not None and obj.attendee_count:
            # ACT event.prep @web.sheet: the sheet's Prep link (an event with guests).
            from mailr.calendar.meeting_prep import prep_path

            extra["prep"] = prep_path(r.uid, r.occurrence)
        out.append({**extra, "key": r.key, "kind": ITEM_KIND.get(r.kind, "task"), "uid": r.uid,
                    "occurrence": r.occurrence, "minutes": r.minutes, "title": r.title, "detail": r.detail,
                    "start": ical.out_when(r.anchor_at), "fire_at": r.fire_at.isoformat(),
                    "fired_at": r.fired_at.isoformat() if r.fired_at else None,
                    "missed": bool(r.fired_at and r.fired_at - r.fire_at > GRACE),
                    "state": "snoozed" if st is not None and st.state == "snoozed" else "fired",
                    "actions": actions_for(r.kind)})
    return out


