"""Tasks beyond the plain write path.

- Repeating tasks advance in place: `advance` moves DUE (and DTSTART) to the next date of the
  rule, decrements COUNT and keeps the task open; the last occurrence completes it. A completed
  history copy (new UID, RELATED-TO the series, no RRULE, X-MAILR-OCCURRENCE = the base due) is
  written unless `tasks.keep_completed_copy` is off. A completion that names its base due
  changes nothing when the task has already moved past that base (two devices, a replay).
  X-MAILR-UNDO on the series holds what the last advance changed, so a reopen restores it and
  deletes the copy.
- Manual order is X-APPLE-SORT-ORDER: a move takes the midpoint between its neighbours; when the
  gap is used up the siblings are renumbered (inline for short lists, else the
  `calendar.reorder` job).
- Subtasks are one level deep in mailr (RELATED-TO;RELTYPE=PARENT); deeper trees from other
  clients are kept. Moving a task to another list takes its subtasks along; a cascade delete
  removes them leaves first.
- Tags are CATEGORIES; their colour and order live in user settings (`task_tags`).
- The task list queries: smart lists, filters, sorts and keyset paging (`list_tasks`), the
  sidebar counts and the tag list.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from dateutil.rrule import rrulestr
from icalendar import Alarm, Calendar, vDDDTypes, vRecur
from sqlalchemy import and_, case, func, literal, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import ical, mirror, writer
from mailr.calendar import tz as tzmod
from mailr.calendar.search import conditions, is_done, parse, text_condition
from mailr.core.jobs import enqueue, handler
from mailr.models import CalCollection, CalObject, EventLink, User

log = logging.getLogger("mailr.calendar")

STEP = 1 << 16  # spacing of X-APPLE-SORT-ORDER values after a renumber
INLINE_RENUMBER = 40  # siblings renumbered inside the request; more go to the job
# Tasks retagged inside the request; more go to the job. Each retag holds the task's lock across a
# CalDAV round trip until the request's transaction ends, so the request keeps the number small.
INLINE_RETAG = 25
FAR = datetime(9999, 1, 1, tzinfo=UTC)
BIG = 1 << 62
SMART = ("today", "overdue", "upcoming", "scheduled", "nodate", "no_date", "high", "email", "all", "completed")


def _now() -> datetime:
    """Now (tests replace this to pin the clock)."""
    return datetime.now(UTC)


class NoChange(Exception):
    """The task already moved past the occurrence the caller completed: nothing is written."""


# ---------------- settings ----------------


def keep_completed_copy(user: User | None) -> bool:
    s = (user.settings if user else None) or {}
    v = (s.get("tasks") or {}).get("keep_completed_copy") if isinstance(s.get("tasks"), dict) else None
    if v is None:
        v = s.get("tasks.keep_completed_copy")
    return True if v is None else bool(v)


def tag_meta(user: User | None) -> dict[str, dict[str, Any]]:
    return dict(((user.settings if user else None) or {}).get("task_tags") or {})


# ---------------- repeat rules ----------------


def _rule_of(comp: Any) -> dict[str, list[Any]] | None:
    r = comp.get("rrule")
    if isinstance(r, list):
        r = r[0] if r else None
    return {k.upper(): list(v) for k, v in dict(r).items()} if r is not None else None


def normalize_rule(rule: str, anchor: date | datetime | None) -> str:
    """A rule as mailr writes it for a new repeat: monthly on day 29-31 becomes
    BYMONTHDAY=28,...,<day>;BYSETPOS=-1 (the last day of shorter months) and yearly on 29 Feb
    becomes BYMONTH=2;BYMONTHDAY=28,29;BYSETPOS=-1. Other rules are returned as given."""
    rule = str(rule).removeprefix("RRULE:")
    if anchor is None:
        return rule
    d = anchor.date() if isinstance(anchor, datetime) else anchor
    r = {k.upper(): list(v) for k, v in dict(vRecur.from_ical(rule)).items()}
    freq = str((r.get("FREQ") or [""])[0]).upper()
    other = any(k in r for k in ("BYDAY", "BYSETPOS", "BYYEARDAY", "BYWEEKNO", "BYHOUR", "BYMINUTE"))
    days = [int(x) for x in r.get("BYMONTHDAY") or []]
    if freq == "MONTHLY" and d.day >= 29 and not other and "BYMONTH" not in r and days in ([], [d.day]):
        r["BYMONTHDAY"] = list(range(28, d.day + 1))
        r["BYSETPOS"] = [-1]
    elif (freq == "YEARLY" and (d.month, d.day) == (2, 29) and not other and days in ([], [29])
          and [int(x) for x in r.get("BYMONTH") or []] in ([], [2])):
        r["BYMONTH"], r["BYMONTHDAY"], r["BYSETPOS"] = [2], [28, 29], [-1]
    else:
        return rule
    return vRecur(r).to_ical().decode()


def _naive(v: datetime | date) -> datetime:
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    return datetime(v.year, v.month, v.day)


def _rebuild(naive: datetime, like: Any, floating: bool) -> datetime | date:
    """A computed wall-clock value written the way `like` (the old value) was."""
    if not isinstance(like, datetime):
        return naive.date()
    if floating or like.tzinfo is None:
        return naive
    return naive.replace(tzinfo=like.tzinfo)


def _dump(prop: Any) -> dict[str, Any] | None:
    if prop is None:
        return None
    return {"v": prop.to_ical().decode() if hasattr(prop, "to_ical") else str(prop),
            "tz": str(getattr(prop, "params", {}).get("TZID") or "") or None}


def _load(d: dict[str, Any] | None, cal: Any, user_tz: tzinfo) -> datetime | date | None:
    if not d:
        return None
    v = vDDDTypes.from_ical(d["v"])
    if isinstance(v, datetime) and v.tzinfo is None and d.get("tz"):
        v = tzmod.localize(v, d["tz"], cal, user_tz)
    return v


def _shift_absolute_alarms(comp: Any, delta: timedelta) -> None:
    for al in comp.subcomponents:
        if getattr(al, "name", "") != "VALARM":
            continue
        trig = al.get("trigger")
        v = getattr(trig, "dt", None)
        if isinstance(v, datetime):
            al.pop("trigger", None)
            al.add("trigger", (v if v.tzinfo else v.replace(tzinfo=UTC)).astimezone(UTC) + delta,
                   parameters={"VALUE": "DATE-TIME"})


def _touch(comp: Any) -> None:
    now = datetime.now(UTC)
    for k in ("dtstamp", "last-modified"):
        comp.pop(k, None)
        comp.add(k, now)
    seq = int(comp.get("sequence", 0) or 0) + 1
    comp.pop("sequence", None)
    comp.add("sequence", seq)


def base_of(comp: Any, cal: Any, user_tz: tzinfo) -> str:
    """The canonical occurrence string of a task: its due (else its start)."""
    prop = comp.get("due") if comp.get("due") is not None else comp.get("dtstart")
    return ical.canonical_occurrence(ical.value(prop, cal, user_tz)[0]) if prop is not None else ""


def history_uid(series_uid: str, base: str) -> str:
    return f"done-{hashlib.sha256(f'{series_uid}|{base}'.encode()).hexdigest()[:24]}@mailr"


def advance(comp: Any, cal: Any, *, when: datetime, user_tz: tzinfo, copy_uid: str | None = None) -> dict[str, Any]:
    """Move a repeating VTODO to its next occurrence in place (or complete it when the rule has
    ended). Returns {base, next (canonical or None), ended}."""
    key = "due" if comp.get("due") is not None else "dtstart"
    prop = comp.get(key)
    if prop is None:
        raise ValueError("a repeating task needs a due date or a start")
    rule = _rule_of(comp)
    if rule is None:
        raise ValueError("this task does not repeat")
    av, _, floating = ical.value(prop, cal, user_tz)
    raw = prop.dt
    base = ical.canonical_occurrence(av)
    count = int(rule.pop("COUNT")[0]) if rule.get("COUNT") else None
    until = rule.pop("UNTIL")[0] if rule.get("UNTIL") else None
    rule_text = vRecur(rule).to_ical().decode()
    anchor = _naive(av if isinstance(raw, datetime) else raw)
    if text_of_repeat(comp) == "completion":
        zone = av.tzinfo if isinstance(av, datetime) else user_tz
        local_done = when.astimezone(zone or user_tz)
        start = datetime.combine(local_done.date(), anchor.time())
    else:
        start = anchor
    try:
        nxt = rrulestr(rule_text, dtstart=start).after(start, inc=False)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"the repeat rule cannot be expanded: {e}") from None
    ended = nxt is None or (count is not None and count <= 1)
    if not ended and until is not None:
        if isinstance(raw, datetime):
            u = until if isinstance(until, datetime) else datetime(until.year, until.month, until.day, 23, 59, 59,
                                                                   tzinfo=av.tzinfo)
            u = u if u.tzinfo else u.replace(tzinfo=av.tzinfo or UTC)
            n_at = _rebuild(nxt, av, False)
            ended = n_at > u
        else:
            u = until.astimezone(user_tz).date() if isinstance(until, datetime) else until
            ended = nxt.date() > u
    undo = {"base": base, "due": _dump(comp.get("due")), "start": _dump(comp.get("dtstart")), "count": count,
            "ended": ended, "copy": copy_uid, "status": ical.text_of(comp, "status").upper() or "NEEDS-ACTION"}
    if ended:
        ical.complete(comp, True, when)
        out_next = None
    else:
        new = _rebuild(nxt, raw, floating)
        delta_wall = nxt - anchor
        old_instant = ical.as_utc(av, user_tz)
        comp.pop(key, None)
        comp.add(key, new)
        if key == "due" and comp.get("dtstart") is not None:
            sraw = comp["dtstart"].dt
            s_floating = isinstance(sraw, datetime) and sraw.tzinfo is None
            comp.pop("dtstart", None)
            comp.add("dtstart", _rebuild(_naive(sraw) + delta_wall, sraw, s_floating))
        new_instant = ical.as_utc(ical.value(comp.get(key), cal, user_tz)[0], user_tz)
        _shift_absolute_alarms(comp, new_instant - old_instant)
        if count is not None:
            r = _rule_of(comp)
            r["COUNT"] = [count - 1]
            comp.pop("rrule", None)
            comp.add("rrule", r)
        if ical.text_of(comp, "status").upper() == "COMPLETED":
            ical.complete(comp, False)
        out_next = ical.canonical_occurrence(ical.value(comp.get(key), cal, user_tz)[0])
    comp.pop("x-mailr-undo", None)
    comp.add("x-mailr-undo", json.dumps(undo, separators=(",", ":")))
    _touch(comp)
    return {"base": base, "next": out_next, "ended": ended}


def text_of_repeat(comp: Any) -> str:
    return "completion" if ical.text_of(comp, "x-mailr-repeat-from").upper() == "COMPLETION" else "due"


def unadvance(comp: Any, cal: Any, user_tz: tzinfo) -> dict[str, Any] | None:
    """Undo the last advance: the due, start and COUNT come back and the task is
    open again. Returns the stored undo record (with `copy`, the history UID to delete)."""
    raw = comp.get("x-mailr-undo")
    if raw is None:
        return None
    try:
        info = json.loads(str(raw))
    except ValueError:
        comp.pop("x-mailr-undo", None)
        return None
    key = "due" if info.get("due") else "dtstart"
    cur = ical.value(comp.get(key), cal, user_tz)[0]
    for k, field in (("due", "due"), ("dtstart", "start")):
        v = _load(info.get(field), cal, user_tz)
        comp.pop(k, None)
        if v is not None:
            comp.add(k, v)
    new = ical.value(comp.get(key), cal, user_tz)[0]
    if cur is not None and new is not None:
        _shift_absolute_alarms(comp, ical.as_utc(new, user_tz) - ical.as_utc(cur, user_tz))
    r = _rule_of(comp)
    if r is not None:
        r.pop("COUNT", None)
        if info.get("count") is not None:
            r["COUNT"] = [int(info["count"])]
        comp.pop("rrule", None)
        comp.add("rrule", r)
    ical.complete(comp, info.get("status") == "COMPLETED")
    comp.pop("x-mailr-undo", None)
    _touch(comp)
    return info


def history_copy(snapshot: Any, series_uid: str, base: str, when: datetime) -> Calendar:
    """The completed record of one occurrence: a copy without the repeat, RELATED-TO the
    series (RELTYPE=SIBLING, so it is never counted as a subtask)."""
    c = copy.deepcopy(snapshot)
    for k in ("rrule", "rdate", "exdate", "x-mailr-undo", "x-mailr-repeat-from", "x-apple-sort-order", "related-to",
              "uid", "dtstamp", "created", "sequence"):
        c.pop(k, None)
    c.subcomponents = [x for x in c.subcomponents if getattr(x, "name", "") != "VALARM"]
    c.add("uid", history_uid(series_uid, base))
    c.add("dtstamp", datetime.now(UTC))
    c.add("created", datetime.now(UTC))
    c.add("related-to", series_uid, parameters={"RELTYPE": "SIBLING"})
    c.add("x-mailr-occurrence", base)
    ical.complete(c, True, when)
    out = ical.new_calendar()
    out.add_component(c)
    return out


def set_absolute_alarms(comp: Any, moments: list[datetime] | None) -> None:
    """Replace the absolute reminders (TRIGGER;VALUE=DATE-TIME) with these moments; relative
    ones stay."""
    if moments is None:
        return
    comp.subcomponents = [x for x in comp.subcomponents if not (
        getattr(x, "name", "") == "VALARM" and isinstance(getattr(x.get("trigger"), "dt", None), datetime))]
    for m in sorted(set(moments))[:10]:
        al = Alarm()
        al.add("action", "DISPLAY")
        al.add("description", str(comp.get("summary", "") or "Reminder"))
        al.add("trigger", m.astimezone(UTC), parameters={"VALUE": "DATE-TIME"})
        comp.add_component(al)


def split_alarms(ics: str, minutes: list[int]) -> tuple[list[int], list[str]]:
    """(relative reminders in minutes, absolute reminders as UTC instants) of a task: the API's
    `reminders` and `reminders_at`. The mirror counts an absolute trigger as minutes before the
    due as well; here it is only in `reminders_at`."""
    if "VALUE=DATE-TIME" not in ics:
        return list(minutes or []), []
    try:
        cal = Calendar.from_ical(ics)
    except Exception:  # noqa: BLE001
        return list(minutes or []), []
    rel, out = set(), []
    for comp in cal.walk("VTODO"):
        for al in comp.subcomponents:
            v = getattr(al.get("trigger"), "dt", None) if getattr(al, "name", "") == "VALARM" else None
            if isinstance(v, datetime):
                out.append(ical.canonical_occurrence(v))
            elif isinstance(v, timedelta):
                rel.add(int(-v.total_seconds() // 60))
        break
    return sorted(rel), sorted(set(out))


def with_alarms(t: dict[str, Any], ics: str) -> dict[str, Any]:
    t["reminders"], t["reminders_at"] = split_alarms(ics, t.get("reminders") or [])
    return t


# ---------------- output ----------------


async def task_dict(db: AsyncSession, obj: CalObject, user_tz: tzinfo | None = None) -> dict[str, Any]:
    return with_alarms(await mirror.object_out(db, obj, user_tz), obj.ics)


async def _find(db: AsyncSession, user_id: int, uid: str, p: Any) -> CalObject:
    obj = await writer.find(db, user_id, uid, "task", p)
    if obj is None:
        raise LookupError("task not found")
    return obj


def _master(cal: Any) -> Any:
    return ical.master_and_overrides(cal, "VTODO")[0]


# ---------------- complete, skip, reopen ----------------


async def complete(db: AsyncSession, user: User, p: Any, uid: str, *, done: bool = True, base: str | None = None,
                   etag: str | None = None, when: datetime | None = None, skip: bool = False) -> dict[str, Any]:
    """task.complete / task.reopen / task.skip. A repeating task advances (skip: without a
    history copy); a reopen of a repeating task undoes its last advance."""
    obj = await _find(db, user.id, uid, p)
    user_tz = await tzmod.user_zone(db, user.id)
    when = when or _now()
    if skip and not obj.recurring:
        raise ValueError("only a repeating task can skip an occurrence")
    if not obj.recurring and not skip:
        if done and base is not None and (obj.status == "COMPLETED" or obj.completed_at is not None):
            return await task_dict(db, obj, user_tz)  # already done (a replay)

        def plain(cal: Calendar) -> None:
            ical.complete(_master(cal), done, when)
            _touch(_master(cal))

        obj, _ = await writer.rmw(db, user.id, p, obj, plain, client_etag=etag)
        return await task_dict(db, obj, user_tz)

    if not done:
        holder: dict[str, Any] = {}

        def undo(cal: Calendar) -> None:
            comp = _master(cal)
            info = unadvance(comp, cal, user_tz)
            if info is None:
                if ical.text_of(comp, "status").upper() != "COMPLETED":
                    raise NoChange()
                ical.complete(comp, False)
                _touch(comp)
            holder["info"] = info

        try:
            obj, _ = await writer.rmw(db, user.id, p, obj, undo, client_etag=etag)
        except NoChange:
            return await task_dict(db, obj, user_tz)
        info = holder.get("info") or {}
        out = await task_dict(db, obj, user_tz)
        if info.get("copy"):
            copy_obj = await mirror.find_by_uid(db, user.id, info["copy"], "task", p)
            if copy_obj is not None:
                try:
                    await writer.delete(db, user.id, copy_obj)
                except writer.Gone:
                    pass
                out["deleted_history_uid"] = info["copy"]
        out["restored"] = info.get("base")
        return out

    want_copy = not skip and keep_completed_copy(user)
    holder = {}

    def step(cal: Calendar) -> None:
        comp = _master(cal)
        cur = base_of(comp, cal, user_tz)
        if base is not None and ical.normalize_occurrence(base, user_tz) != cur:
            raise NoChange()
        if ical.text_of(comp, "status").upper() == "COMPLETED":
            raise NoChange()
        snapshot = copy.deepcopy(comp)
        hist = history_uid(str(comp.get("uid")), cur) if want_copy else None
        res = advance(comp, cal, when=when, user_tz=user_tz, copy_uid=hist)
        holder.update(res)
        if hist:
            holder["copy"] = history_copy(snapshot, str(comp.get("uid")), cur, when)
            holder["hist"] = hist

    try:
        obj, _ = await writer.rmw(db, user.id, p, obj, step, client_etag=etag)
    except NoChange:
        out = await task_dict(db, obj, user_tz)
        out["next"] = out.get("due") or out.get("start")
        out["history_uid"] = None
        out["unchanged"] = True
        return out
    out = await task_dict(db, obj, user_tz)
    out["next"] = holder.get("next")
    out["history_uid"] = None
    if holder.get("copy") is not None:
        coll = await db.get(CalCollection, obj.collection_id)
        try:
            await writer.create(db, user.id, coll, holder["copy"], holder["hist"])
        except writer.Conflict as c:
            if c.error != "exists":
                raise
        out["history_uid"] = holder["hist"]
    return out


# ---------------- create and update ----------------


def _anchor_of(payload: dict[str, Any], comp: Any, cal: Any, user_tz: tzinfo) -> date | datetime | None:
    for k in ("due", "start"):
        if payload.get(k):
            v = ical.parse_when(payload[k], user_tz)
            return v.astimezone(user_tz) if isinstance(v, datetime) else v
    if comp is not None:
        for k in ("due", "dtstart"):
            if comp.get(k) is not None:
                return ical.value(comp.get(k), cal, user_tz)[0]
    return None


async def create(db: AsyncSession, user: User, p: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """POST /tasks: the task fields of the calendar REST API plus absolute reminders
    (`reminders_at`) and a monthly-on-the-31st repeat."""
    coll = await writer.target_collection(db, user.id, p, payload.get("list"), "task")
    user_tz = await tzmod.user_zone(db, user.id)
    payload = dict(payload)
    if payload.get("recurrence"):
        payload["recurrence"] = normalize_rule(payload["recurrence"], _anchor_of(payload, None, None, user_tz))
    if payload.get("parent"):
        parent = await writer.find(db, user.id, payload["parent"], "task", p)
        if parent is None:
            raise LookupError("parent task not found")
        if parent.parent_uid:  # one level: a subtask of a subtask joins its parent
            payload["parent"] = parent.parent_uid
    cal = ical.build_todo(payload, user_tz)
    comp = _master(cal)
    if payload.get("reminders_at"):
        set_absolute_alarms(comp, [ical.parse_when(x, user_tz) for x in payload["reminders_at"]])
    obj, existed = await writer.create_once(db, user.id, coll, cal, str(comp["uid"]), payload, "task", p)
    out = await task_dict(db, obj, user_tz)
    return {**out, "existing": True} if existed else out


async def update(db: AsyncSession, user: User, p: Any, uid: str, changes: dict[str, Any], *,
                 etag: str | None = None, occurrence: str | None = None,
                 base_version: int | None = None) -> dict[str, Any]:
    """PATCH /tasks/{uid}. `completed`/`status` go through `complete` (repeating tasks
    advance); everything else is the writer's change, plus `reminders_at`. Made from an older
    `base_version`, the changes merge with what changed since (calendar/live_items.py)."""
    if base_version is not None:
        from mailr.calendar import live_items

        changes, _ = await live_items.resolve_patch(db, await _find(db, user.id, uid, p), dict(changes), base_version)
        etag = None  # the merge replaces the ETag check
    changes = dict(changes)
    status = changes.pop("status", None)
    if status is not None and not changes:
        return await complete(db, user, p, uid, done=status == "completed", base=occurrence, etag=etag)
    obj = await _find(db, user.id, uid, p)
    user_tz = await tzmod.user_zone(db, user.id)
    move_to = changes.pop("list", None)
    reminders_at = changes.pop("reminders_at", None)
    if changes.get("parent"):
        parent = await writer.find(db, user.id, changes["parent"], "task", p)
        if parent is None:
            raise LookupError("parent task not found")
        if parent.uid == obj.uid:
            raise ValueError("a task cannot be its own subtask")
        changes["parent"] = parent.parent_uid or parent.uid

    def change(cal: Calendar) -> None:
        comp = _master(cal)
        if changes.get("recurrence"):
            changes["recurrence"] = normalize_rule(changes["recurrence"], _anchor_of(changes, comp, cal, user_tz))
        absolute = [copy.deepcopy(x) for x in comp.subcomponents if getattr(x, "name", "") == "VALARM"
                    and isinstance(getattr(x.get("trigger"), "dt", None), datetime)]
        if changes:
            ical.apply_task_changes(comp, changes, user_tz)
        if "reminders" in changes and reminders_at is None:
            comp.subcomponents.extend(absolute)  # a new list of relative reminders keeps the absolute ones
        if reminders_at is not None:
            set_absolute_alarms(comp, [ical.parse_when(x, user_tz) for x in reminders_at])
        if any(k in changes for k in ("due", "start", "recurrence")):
            comp.pop("x-mailr-undo", None)  # an edited date ends what an undo could restore
        if status is not None:
            ical.complete(comp, status == "completed")
        if not changes:
            _touch(comp)

    target = None
    if move_to:
        target = await mirror.collection_by_url(db, user.id, move_to, p)
        if target is None:
            raise ValueError("unknown task list")
        if target.id == obj.collection_id:
            target = None
    if target is not None:
        has_change = bool(changes) or reminders_at is not None or status is not None
        obj, _ = await writer.move(db, user.id, obj, target, change if has_change else None, client_etag=etag)
        await _move_descendants(db, user.id, p, obj.uid, target)
    elif changes or reminders_at is not None or status is not None:
        obj, _ = await writer.rmw(db, user.id, p, obj, change, client_etag=etag)
    return await task_dict(db, obj, user_tz)


# ---------------- subtasks ----------------


async def descendants(db: AsyncSession, user_id: int, uid: str, connector_ids: list[int]) -> list[tuple[str, int]]:
    """[(uid, depth)] of the task's subtasks at any depth, deepest first."""
    if not connector_ids:
        return []
    rows = (await db.execute(text(
        "WITH RECURSIVE d(uid, depth) AS ("
        " SELECT uid, 1 FROM cal_objects WHERE user_id=:u AND kind='task' AND deleted_at IS NULL"
        "   AND parent_uid=:root AND connector_id = ANY(:c)"
        " UNION SELECT c.uid, d.depth + 1 FROM cal_objects c JOIN d ON c.parent_uid = d.uid"
        "   WHERE c.user_id=:u AND c.kind='task' AND c.deleted_at IS NULL AND c.connector_id = ANY(:c)"
        "   AND d.depth < 20)"
        " SELECT uid, max(depth) FROM d WHERE uid <> :root GROUP BY uid ORDER BY max(depth) DESC, uid"),
        {"u": user_id, "root": uid, "c": connector_ids})).all()
    return [(r[0], int(r[1])) for r in rows]


async def _move_descendants(db: AsyncSession, user_id: int, p: Any, uid: str, target: CalCollection) -> list[str]:
    moved = []
    cids = await mirror.visible_connector_ids(db, user_id, p)
    for child_uid, _ in reversed(await descendants(db, user_id, uid, cids)):
        child = await mirror.find_by_uid(db, user_id, child_uid, "task", p)
        if child is None or child.collection_id == target.id:
            continue
        await writer.move(db, user_id, child, target)
        moved.append(child_uid)
    return moved


async def delete(db: AsyncSession, user: User, p: Any, uid: str, *, cascade: bool = False,
                 etag: str | None = None) -> dict[str, Any]:
    """task.delete: with `cascade` the subtasks go too (leaves first); without it they are
    detached (RELATED-TO removed) and stay."""
    obj = await _find(db, user.id, uid, p)
    cids = await mirror.visible_connector_ids(db, user.id, p)
    kids = await descendants(db, user.id, uid, cids)
    deleted, detached = [], []
    if etag and obj.etag and etag != obj.etag:
        fresh = await writer.refresh(db, obj)
        if fresh is None:
            return {"ok": True, "uid": uid, "deleted": [], "detached": []}
        if fresh.etag != etag:
            raise writer.Conflict("task", await mirror.object_out(db, fresh))
        obj = fresh
    for child_uid, depth in kids:
        child = await mirror.find_by_uid(db, user.id, child_uid, "task", p)
        if child is None:
            continue
        if cascade:
            try:
                await writer.delete(db, user.id, child)
            except writer.Gone:
                pass
            deleted.append(child_uid)
        elif depth == 1:
            def detach(cal: Calendar) -> None:
                comp = _master(cal)
                ical._set_related_parent(comp, None)
                _touch(comp)

            try:
                await writer.rmw(db, user.id, p, child, detach)
                detached.append(child_uid)
            except writer.Gone:
                pass
    try:
        await writer.delete(db, user.id, obj, client_etag=etag)
    except writer.Gone:
        pass
    return {"ok": True, "uid": uid, "deleted": deleted, "detached": detached}


# ---------------- manual order and moves ----------------


def _order_key() -> list[Any]:
    return [func.coalesce(CalObject.sort_order, BIG), func.coalesce(CalObject.created, CalObject.created_at),
            CalObject.id]


async def siblings(db: AsyncSession, user_id: int, coll_id: int, parent: str | None, exclude: int | None = None
                   ) -> list[CalObject]:
    """Open tasks at one level of a list, in manual order."""
    q = select(CalObject).where(CalObject.user_id == user_id, CalObject.kind == "task",
                                CalObject.deleted_at.is_(None), CalObject.collection_id == coll_id, ~is_done(),
                                CalObject.parent_uid == parent if parent else CalObject.parent_uid.is_(None))
    if exclude is not None:
        q = q.where(CalObject.id != exclude)
    return list((await db.execute(q.order_by(*_order_key()))).scalars())


def slot_value(sibs: list[CalObject], idx: int) -> int | None:
    """The sort order for a task placed at `idx` among `sibs`, or None when the neighbours
    leave no room (or are unnumbered in the middle) and the level must be renumbered."""
    lo = sibs[idx - 1] if idx > 0 else None
    hi = sibs[idx] if idx < len(sibs) else None
    if lo is not None and lo.sort_order is None:
        return None
    lv = lo.sort_order if lo is not None else None
    hv = hi.sort_order if hi is not None else None
    if hv is None:  # the end of the numbered tasks (unnumbered ones sort last)
        return lv + STEP if lv is not None else 0
    if lv is None:
        return hv - STEP
    if hv - lv >= 2:
        return (lv + hv) // 2
    return None


def _set_order(value: int | None) -> Callable[[Calendar], None]:
    def mutate(cal: Calendar) -> None:
        comp = _master(cal)
        comp.pop("x-apple-sort-order", None)
        if value is not None:
            comp.add("x-apple-sort-order", str(int(value)))
        _touch(comp)
    return mutate


async def renumber(db: AsyncSession, user_id: int, p: Any, uids: list[str]) -> int:
    """Write STEP-spaced sort orders in this order; returns how many tasks changed."""
    n = 0
    for i, u in enumerate(uids):
        obj = await mirror.find_by_uid(db, user_id, u, "task", p)
        want = (i + 1) * STEP
        if obj is None or obj.sort_order == want:
            continue
        try:
            await writer.rmw(db, user_id, p, obj, _set_order(want))
            n += 1
        except (writer.Gone, writer.Conflict):
            continue
    return n


@handler("calendar.reorder")
async def reorder_job(payload: dict[str, Any], ctx) -> None:
    """Renumber a list level in the order the move asked for."""
    from mailr.db import session_scope

    async with session_scope() as db:
        await renumber(db, int(payload["user_id"]), None, [str(u) for u in payload.get("order") or []])


async def move(db: AsyncSession, user: User, p: Any, uid: str, *, list_url: str | None = None,
               before: str | None = None, after: str | None = None, parent: str | None = None,
               etag: str | None = None) -> dict[str, Any]:
    """POST /tasks/{uid}/move: another list (subtasks follow), a new parent ("" for the top
    level; one level deep), and a place among the siblings (`before` / `after` a uid)."""
    obj = await _find(db, user.id, uid, p)
    user_tz = await tzmod.user_zone(db, user.id)
    target = await db.get(CalCollection, obj.collection_id)
    if list_url:
        target = await mirror.collection_by_url(db, user.id, list_url, p)
        if target is None:
            raise ValueError("unknown task list")
    new_parent = obj.parent_uid
    if parent is not None:
        new_parent = None
        if parent:
            if parent == uid:
                raise ValueError("a task cannot be its own subtask")
            pobj = await writer.find(db, user.id, parent, "task", p)
            if pobj is None:
                raise LookupError("parent task not found")
            new_parent = pobj.parent_uid or pobj.uid
            if new_parent == uid:
                raise ValueError("a task cannot be its own subtask")
            if await mirror.subtask_counts(db, user.id, [uid]):
                raise ValueError("a task with subtasks cannot become a subtask (one level only)")
            pcoll = await db.get(CalCollection, pobj.collection_id)
            if list_url and pcoll.id != target.id:
                raise ValueError("a subtask stays in its parent's list")
            target = pcoll
    order: int | None | str = "keep"
    plan: list[str] | None = None
    if before or after:
        sibs = await siblings(db, user.id, target.id, new_parent, exclude=obj.id)
        uids = [s.uid for s in sibs]
        ref = after or before
        if ref not in uids:
            raise ValueError(f"{ref} is not in the same list and level")
        idx = uids.index(after) + 1 if after else uids.index(before)
        order = slot_value(sibs, idx)
        if order is None:
            plan = uids[:idx] + [uid] + uids[idx:]
            order = (idx + 1) * STEP

    def mutate(cal: Calendar) -> None:
        comp = _master(cal)
        if parent is not None:
            ical._set_related_parent(comp, new_parent)
        if order != "keep":
            comp.pop("x-apple-sort-order", None)
            comp.add("x-apple-sort-order", str(int(order)))
        _touch(comp)

    moved_kids: list[str] = []
    if target.id != obj.collection_id:
        obj, _ = await writer.move(db, user.id, obj, target, mutate, client_etag=etag)
        moved_kids = await _move_descendants(db, user.id, p, obj.uid, target)
    elif parent is not None or order != "keep":
        obj, _ = await writer.rmw(db, user.id, p, obj, mutate, client_etag=etag)
    renumbered = 0
    queued = False
    if plan is not None:
        if len(plan) <= INLINE_RENUMBER:
            renumbered = await renumber(db, user.id, p, plan)
            obj = await mirror.find_by_uid(db, user.id, uid, "task", p) or obj
        else:
            await enqueue(db, "calendar.reorder", {"user_id": user.id, "order": plan}, user_id=user.id)
            queued = True
    out = await task_dict(db, obj, user_tz)
    out["moved_subtasks"] = moved_kids
    out["renumbered"] = renumbered
    out["renumber_queued"] = queued
    return out


# ---------------- bulk ----------------


def _shift(prop: Any, days: int, cal: Any, user_tz: tzinfo) -> datetime | date:
    raw = prop.dt
    floating = isinstance(raw, datetime) and raw.tzinfo is None
    return _rebuild(_naive(raw) + timedelta(days=days), raw, floating)


async def bulk_one(db: AsyncSession, user: User, p: Any, uid: str, op: str, args: dict[str, Any]) -> dict[str, Any]:
    etag = (args.get("etags") or {}).get(uid)
    if op == "complete":
        return await complete(db, user, p, uid, done=True, etag=etag)
    if op == "reopen":
        return await complete(db, user, p, uid, done=False, etag=etag)
    if op == "delete":
        return await delete(db, user, p, uid, cascade=bool(args.get("cascade", True)), etag=etag)
    if op == "move":
        if not args.get("list"):
            raise ValueError("move needs a list")
        return await update(db, user, p, uid, {"list": args["list"]}, etag=etag)
    if op == "priority":
        return await update(db, user, p, uid, {"priority": int(args.get("priority") or 0)}, etag=etag)
    if op in ("tag", "untag"):
        add = list(args.get("add") or []) + ([args["tag"]] if op == "tag" and args.get("tag") else [])
        remove = list(args.get("remove") or []) + ([args["tag"]] if op == "untag" and args.get("tag") else [])
        return await retag_one(db, user.id, p, uid, add=add, remove=remove, etag=etag)
    if op == "reschedule":
        user_tz = await tzmod.user_zone(db, user.id)
        per = (args.get("dues") or {})
        if uid in per or "due" in args:
            due = per.get(uid, args.get("due"))
            return await update(db, user, p, uid, {"due": due} if due else {"due": None}, etag=etag)
        days = int(args.get("shift_days") or 0)
        if not days:
            raise ValueError("reschedule needs a due or shift_days")
        obj = await _find(db, user.id, uid, p)

        def shift(cal: Calendar) -> None:
            comp = _master(cal)
            if comp.get("due") is None:
                raise ValueError("this task has no due date to shift")
            new = _shift(comp["due"], days, cal, user_tz)
            comp.pop("due", None)
            comp.add("due", new)
            comp.pop("x-mailr-undo", None)
            _touch(comp)

        obj, _ = await writer.rmw(db, user.id, p, obj, shift, client_etag=etag)
        return await task_dict(db, obj, user_tz)
    raise ValueError(f"unknown bulk operation {op}")


async def bulk(db: AsyncSession, user: User, p: Any, uids: list[str], op: str, args: dict[str, Any]) -> dict[str, Any]:
    """Each task in its own savepoint: one failure (a conflict, a task deleted elsewhere) does
    not undo the others. → {ok, failed: [{uid, detail, error?, server?}], items}."""
    ok, failed, items = [], [], []
    for uid in dict.fromkeys(uids):
        sp = await db.begin_nested()
        try:
            res = await bulk_one(db, user, p, uid, op, args)
            await sp.commit()
            ok.append(uid)
            if isinstance(res, dict) and res.get("uid") and "summary" in res:
                items.append(res)
        except writer.Conflict as c:
            await sp.rollback()
            failed.append({"uid": uid, "error": c.error, "detail": c.detail, "server": c.server})
        except writer.Gone:
            await sp.rollback()
            failed.append({"uid": uid, "error": "gone", "detail": "it was deleted on the calendar server"})
        except LookupError as e:
            await sp.rollback()
            failed.append({"uid": uid, "error": "not_found", "detail": str(e).strip("'")})
        except Exception as e:  # noqa: BLE001
            await sp.rollback()
            failed.append({"uid": uid, "error": "failed", "detail": str(e)[:300]})
    return {"op": op, "ok": ok, "failed": failed, "items": items}


# ---------------- tags ----------------


async def retag_one(db: AsyncSession, user_id: int, p: Any, uid: str, *, add: list[str] = (),
                    remove: list[str] = (), rename: dict[str, str] | None = None,
                    etag: str | None = None) -> dict[str, Any]:
    obj = await _find(db, user_id, uid, p)
    gone = {r.lower() for r in remove}
    ren = {k.lower(): v for k, v in (rename or {}).items()}

    def mutate(cal: Calendar) -> None:
        comp = _master(cal)
        cats = []
        for c in ical.categories(comp):
            c = ren.get(c.lower(), c)
            if c.lower() not in gone and c.lower() not in {x.lower() for x in cats}:
                cats.append(c)
        for a in add:
            a = a.strip()
            if a and a.lower() not in {x.lower() for x in cats}:
                cats.append(a[:100])
        comp.pop("categories", None)
        if cats:
            comp.add("categories", cats)
        _touch(comp)

    obj, _ = await writer.rmw(db, user_id, p, obj, mutate, client_etag=etag)
    return await task_dict(db, obj)


async def tagged(db: AsyncSession, user_id: int, p: Any, name: str) -> list[CalObject]:
    cols = await mirror.task_collections(db, user_id, p, None)
    cols += [c for c in await mirror.collections_of(db, await mirror.visible_connector_ids(db, user_id, p))
             if c.hidden and c not in cols]
    if not cols:
        return []
    from mailr.calendar.search import _cats_have

    return list((await db.execute(select(CalObject).where(
        CalObject.user_id == user_id, CalObject.kind == "task", CalObject.deleted_at.is_(None),
        CalObject.collection_id.in_([c.id for c in cols]), _cats_have(name)))).scalars())


async def retag_all(db: AsyncSession, user: User, p: Any, name: str, new_name: str | None, *,
                    commit_each: bool = False) -> dict[str, Any]:
    """tag.rename (new_name) or tag.delete (None) on every task that has the tag. `commit_each` (the
    job): each task is committed on its own, so its lock (calendar/sync.py uid_lock) is held for its
    own round trip only, and edits elsewhere and calendar sync wait for one task, not the whole run."""
    uids = [obj.uid for obj in await tagged(db, user.id, p, name)]
    uid_ = user.id
    ok, failed = 0, []
    for uid in uids:
        sp = None if commit_each else await db.begin_nested()
        try:
            if new_name:
                await retag_one(db, uid_, p, uid, rename={name: new_name})
            else:
                await retag_one(db, uid_, p, uid, remove=[name])
            await (db.commit() if sp is None else sp.commit())
            ok += 1
        except Exception as e:  # noqa: BLE001
            await (db.rollback() if sp is None else sp.rollback())
            failed.append({"uid": uid, "detail": str(e)[:200]})
    # Read again after the CalDAV round trips (a job's copy of the user is from its start), and
    # change only this tag's entry (core/usersettings.py).
    from mailr.core import usersettings

    meta = dict((await usersettings.current(db, uid_)).get("task_tags") or {})
    key = next((k for k in meta if k.lower() == name.lower()), None)
    if key is not None:
        await usersettings.patch_in(db, uid_, "task_tags", {new_name: meta[key]} if new_name else None,
                                    remove=[key])
    return {"updated": ok, "failed": failed}


@handler("calendar.retag")
async def retag_job(payload: dict[str, Any], ctx) -> None:
    """A tag rename or delete over many tasks (progress through the `task` changes)."""
    from mailr.db import session_scope

    async with session_scope() as db:
        user = await db.get(User, int(payload["user_id"]))
        if user is not None:
            await retag_all(db, user, None, str(payload["name"]), payload.get("new_name"), commit_each=True)


def fold_tags(rows: list[tuple[str, int, int]]) -> dict[str, tuple[str, int]]:
    """{lower-case tag: (spelling, open count)} from (spelling, tasks, open tasks) rows: tags
    compare without case and show their most used spelling (ties: the first in sort order)."""
    by: dict[str, list[tuple[str, int, int]]] = {}
    for t, n, o in rows:
        by.setdefault(t.lower(), []).append((t, n, o))
    return {k: (sorted(v, key=lambda x: (-x[1], x[0]))[0][0], sum(x[2] for x in v)) for k, v in by.items()}


async def tags(db: AsyncSession, user: User, p: Any) -> list[dict[str, Any]]:
    """[{name, count (open tasks), color, order, pinned}]: every tag on a task the caller may
    see, plus pinned ones; settings order first, then by name."""
    cols = await mirror.task_collections(db, user.id, p, None)
    meta = tag_meta(user)
    rows: list[tuple[str, int, int]] = []
    if cols:
        rows = [(t, int(n), int(o)) for t, n, o in (await db.execute(
            select(func.unnest(CalObject.categories).label("t"), func.count(), func.count().filter(~is_done())).where(
                CalObject.user_id == user.id, CalObject.kind == "task", CalObject.deleted_at.is_(None),
                CalObject.collection_id.in_([c.id for c in cols])).group_by(text("1")))).all()]
    folded = fold_tags(rows)
    for name, m in meta.items():
        if m.get("pinned") and name.lower() not in folded:
            folded[name.lower()] = (name, 0)
    out = []
    for key, (name, n) in folded.items():
        m = next((v for k, v in meta.items() if k.lower() == key), {})
        out.append({"name": name, "count": n, "color": m.get("color"), "order": m.get("order"),
                    "pinned": bool(m.get("pinned"))})
    out.sort(key=lambda t: (t["order"] is None, t["order"] if t["order"] is not None else 0, t["name"].lower()))
    return out


# ---------------- the task list queries ----------------


class Window:
    """Day boundaries in the user's zone for one request."""

    def __init__(self, now: datetime, zone: tzinfo):
        self.now, self.zone = now, zone
        self.today = now.astimezone(zone).date()
        self.tomorrow = self.day_start(self.today + timedelta(days=1))
        self.day8 = self.day_start(self.today + timedelta(days=8))

    def day_start(self, d: date) -> datetime:
        return datetime(d.year, d.month, d.day, tzinfo=self.zone).astimezone(UTC)


def _conds(w: Window) -> dict[str, Any]:
    o = CalObject
    open_ = and_(~is_done(), func.coalesce(o.status, "") != "CANCELLED")
    has_due = o.due_at.is_not(None)
    due_by_today = or_(and_(o.due_date.is_not(None), o.due_date <= w.today),
                       and_(o.due_date.is_(None), o.due_at < w.tomorrow))
    overdue = or_(and_(o.due_date.is_not(None), o.due_date < w.today), and_(o.due_date.is_(None), o.due_at < w.now))
    within7 = or_(and_(o.due_date.is_not(None), o.due_date <= w.today + timedelta(days=7)),
                  and_(o.due_date.is_(None), o.due_at < w.day8))
    started = or_(and_(o.start_date.is_not(None), o.start_date <= w.today),
                  and_(o.start_date.is_(None), o.start_at <= w.now))
    # Never NULL (a task without a start is not deferred), so `~deferred` keeps it.
    deferred = and_(o.start_at.is_not(None), or_(and_(o.start_date.is_not(None), o.start_date > w.today),
                                                 and_(o.start_date.is_(None), o.start_at > w.now)))
    return {"open": open_, "has_due": has_due, "due_by_today": due_by_today, "overdue": overdue,
            "within7": within7, "started": started, "deferred": deferred}


def smart_condition(smart: str, c: dict[str, Any], email_uids: Any) -> Any:
    o = CalObject
    return {
        "today": and_(c["open"], ~c["deferred"], or_(c["due_by_today"],
                                                    and_(c["started"], or_(~c["has_due"], c["within7"])))),
        "overdue": and_(c["open"], c["overdue"]),
        "upcoming": and_(c["open"], or_(and_(c["has_due"], ~c["due_by_today"]), c["deferred"])),
        "scheduled": and_(c["open"], c["has_due"]),
        "nodate": and_(c["open"], ~c["has_due"], o.start_at.is_(None)),
        "high": and_(c["open"], func.coalesce(o.priority, 0).between(1, 4)),
        "email": and_(c["open"], o.uid.in_(email_uids)),
        "all": c["open"],
        "completed": is_done(),
    }[smart]


_PRI = case((func.coalesce(CalObject.priority, 0).between(1, 9), CalObject.priority), else_=10)


def sort_keys(sort: str) -> list[tuple[Any, bool, str]]:
    """[(expression, descending, type)] for keyset paging; every key is non-null."""
    o = CalObject
    created = func.coalesce(o.created, o.created_at)
    due = func.coalesce(o.due_at, literal(FAR))
    manual = func.coalesce(o.sort_order, BIG)
    return {
        "manual": [(manual, False, "int"), (created, False, "ts"), (o.id, False, "int")],
        "due": [(due, False, "ts"), (_PRI, False, "int"), (manual, False, "int"), (o.id, False, "int")],
        "date": [(func.coalesce(o.due_at, o.start_at, literal(FAR)), False, "ts"), (_PRI, False, "int"),
                 (o.id, False, "int")],
        "priority": [(_PRI, False, "int"), (due, False, "ts"), (o.id, False, "int")],
        "created": [(created, True, "ts"), (o.id, True, "int")],
        "title": [(func.lower(o.summary), False, "str"), (o.id, False, "int")],
        "completed": [(func.coalesce(o.completed_at, o.last_modified, o.updated_at), True, "ts"), (o.id, True, "int")],
    }[sort]


DEFAULT_SORT = {"today": "due", "overdue": "due", "upcoming": "date", "scheduled": "due", "nodate": "manual",
                "high": "due", "email": "due", "all": "manual", "completed": "completed"}


def encode_cursor(vals: list[Any]) -> str:
    raw = json.dumps([v.isoformat() if isinstance(v, datetime) else v for v in vals], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cur: str, keys: list[tuple[Any, bool, str]]) -> list[Any]:
    try:
        raw = json.loads(base64.urlsafe_b64decode(cur + "=" * (-len(cur) % 4)))
        if len(raw) != len(keys):
            raise ValueError
        return [datetime.fromisoformat(v) if t == "ts" else (int(v) if t == "int" else str(v))
                for v, (_, _, t) in zip(raw, keys, strict=True)]
    except Exception:  # noqa: BLE001
        raise ValueError("invalid cursor") from None


def keyset_after(keys: list[tuple[Any, bool, str]], vals: list[Any]) -> Any:
    ors = []
    for i, (expr, desc, _) in enumerate(keys):
        eq = [k[0] == v for k, v in zip(keys[:i], vals[:i], strict=True)]
        ors.append(and_(*eq, expr < vals[i] if desc else expr > vals[i]))
    return or_(*ors)


def section_of(t: dict[str, Any], smart: str | None, w: Window) -> str | None:
    """The group a row belongs to in a smart list: Today has overdue / today / started;
    Upcoming groups by the local day of its due (or start)."""
    def day(v: str | None) -> date | None:
        if not v:
            return None
        if len(v) == 10:
            return date.fromisoformat(v)
        return datetime.fromisoformat(v).astimezone(w.zone).date()

    due = t.get("due")
    if smart == "today":
        if due and (len(due) == 10 and day(due) < w.today or len(due) > 10 and datetime.fromisoformat(due) < w.now):
            return "overdue"
        if due and day(due) <= w.today:
            return "today"
        return "started"
    if smart == "upcoming":
        d = day(due) if due and day(due) > w.today else day(t.get("start"))
        return d.isoformat() if d else None
    return None


async def list_tasks(db: AsyncSession, user: User, p: Any, *, smart: str | None = None, status: str = "open",
                     lists: list[str] | None = None, tags_: list[str] | None = None, q: str = "",
                     priority: str | None = None, parent: str | None = None, has_due: bool | None = None,
                     due_before: str | None = None, due_after: str | None = None, start_before: str | None = None,
                     include_future: bool | None = None, sort: str | None = None, cursor: str | None = None,
                     limit: int = 200, synced: bool = False) -> dict[str, Any]:
    """`synced`: the caller has already run mirror.ensure_synced for this request."""
    from mailr.core.visibility import visible_conversation_ids

    if smart == "no_date":
        smart = "nodate"
    if smart is not None and smart not in SMART:
        raise ValueError(f"unknown smart list {smart}")
    if not synced:
        await mirror.ensure_synced(db, user.id, p)
    cols = await mirror.task_collections(db, user.id, p, lists)
    if not cols:
        return {"items": [], "next_cursor": None}
    by_id = {c.id: c for c in cols}
    zone = await tzmod.user_zone(db, user.id)
    w = Window(_now(), zone)
    c = _conds(w)
    o = CalObject
    email_uids = select(EventLink.uid).where(EventLink.user_id == user.id, EventLink.kind == "task",
                                            EventLink.conversation_id.in_(visible_conversation_ids(p)))
    where = [o.user_id == user.id, o.kind == "task", o.deleted_at.is_(None), o.collection_id.in_(list(by_id))]
    qr = parse(q)
    searching_done = qr.has("is", "done")
    if smart:
        where.append(smart_condition(smart, c, email_uids))
        hide = include_future is False or (include_future is None and smart in ("today", "all"))
        if hide and smart not in ("upcoming", "completed"):
            where.append(~c["deferred"])
    else:
        if status == "open" and not searching_done:
            where.append(~is_done())
        elif status == "completed":
            where.append(is_done())
        elif status not in ("open", "all", "completed"):
            raise ValueError("status must be open, completed, or all")
        if include_future is False:
            where.append(~c["deferred"])
    if tags_:
        from mailr.calendar.search import _cats_have

        where += [_cats_have(t) for t in tags_ if t]
    if priority:
        rng = {"high": (1, 4), "medium": (5, 5), "low": (6, 9), "none": (0, 0)}.get(priority.lower())
        if rng is None:
            if not priority.isdigit():
                raise ValueError("priority must be high, medium, low, none or 0-9")
            rng = (int(priority), int(priority))
        where.append(func.coalesce(o.priority, 0).between(*rng))
    if parent is not None:
        where.append(o.parent_uid.is_(None) if parent in ("", "none") else o.parent_uid == parent)
    if has_due is not None:
        where.append(c["has_due"] if has_due else ~c["has_due"])
    for val, op in ((due_before, "<"), (due_after, ">=")):
        if val:
            v = ical.parse_when(val, zone)
            at = w.day_start(v) if not isinstance(v, datetime) else v
            where.append(o.due_at < at if op == "<" else o.due_at >= at)
    if start_before:
        v = ical.parse_when(start_before, zone)
        where.append(o.start_at < (w.day_start(v) if not isinstance(v, datetime) else v))
    if qr.words or qr.ops:
        cond, _ = text_condition(qr.words)
        where.append(cond)
        where += conditions(qr, now=w.now, user_tz=zone, collections=cols, p=p)
    sort = sort or (DEFAULT_SORT.get(smart) if smart else ("completed" if status == "completed" else "due"))
    if sort not in ("manual", "due", "date", "priority", "created", "title", "completed"):
        raise ValueError("sort must be manual, due, priority, created or title")
    keys = sort_keys(sort)
    if cursor:
        where.append(keyset_after(keys, decode_cursor(cursor, keys)))
    limit = max(1, min(int(limit or 200), 200))
    rows = (await db.execute(select(o, *[k[0] for k in keys]).where(*where)
                             .order_by(*[k[0].desc() if k[1] else k[0].asc() for k in keys])
                             .limit(limit + 1))).all()
    more = len(rows) > limit
    rows = rows[:limit]
    counts = await mirror.subtask_counts(db, user.id, [r[0].uid for r in rows])
    from mailr.models import Connector

    local = set((await db.execute(select(Connector.id).where(
        Connector.id.in_(list({x.connector_id for x in cols})), Connector.data_class == "local_only"))).scalars())
    items = []
    for r in rows:
        t = mirror.task_out(r[0], by_id.get(r[0].collection_id), zone, subtasks=counts.get(r[0].uid, (0, 0)),
                            local_only=r[0].connector_id in local)
        with_alarms(t, r[0].ics)
        sec = section_of(t, smart, w)
        if sec is not None:
            t["section"] = sec
        items.append(t)
    nxt = encode_cursor(list(rows[-1][1:])) if more and rows else None
    return {"items": items, "next_cursor": nxt, "sort": sort}


async def counts(db: AsyncSession, user: User, p: Any) -> dict[str, Any]:
    """Open tasks per smart list, per list and per tag (the sidebar)."""
    from mailr.core.visibility import visible_conversation_ids

    await mirror.ensure_synced(db, user.id, p)
    cols = await mirror.task_collections(db, user.id, p, None)
    zone = await tzmod.user_zone(db, user.id)
    w = Window(_now(), zone)
    c = _conds(w)
    out: dict[str, Any] = {"smart": {k: 0 for k in ("today", "overdue", "upcoming", "scheduled", "nodate", "no_date",
                                                    "high", "email", "all")},
                           "lists": {}, "lists_by_id": {}, "tags": {}, "overdue": 0}
    if not cols:
        return out
    o = CalObject
    email_uids = select(EventLink.uid).where(EventLink.user_id == user.id, EventLink.kind == "task",
                                            EventLink.conversation_id.in_(visible_conversation_ids(p)))
    base = [o.user_id == user.id, o.kind == "task", o.deleted_at.is_(None),
            o.collection_id.in_([x.id for x in cols])]
    names = ["today", "overdue", "upcoming", "scheduled", "nodate", "high", "email", "all"]
    exprs = []
    for n in names:
        cond = smart_condition(n, c, email_uids)
        if n in ("today", "all"):
            cond = and_(cond, ~c["deferred"])
        exprs.append(func.count().filter(cond))
    row = (await db.execute(select(*exprs).where(*base))).one()
    for n, v in zip(names, row, strict=True):
        out["smart"][n] = int(v)
    out["smart"]["no_date"] = out["smart"]["nodate"]
    out["overdue"] = out["smart"]["overdue"]
    by_list = dict((await db.execute(select(o.collection_id, func.count()).where(*base, c["open"])
                                     .group_by(o.collection_id))).all())
    for col in cols:
        out["lists"][col.href] = int(by_list.get(col.id, 0))
        out["lists_by_id"][str(col.id)] = int(by_list.get(col.id, 0))
    sq = select(func.unnest(o.categories).label("t"), c["open"].label("open")).where(*base).subquery()
    rows = (await db.execute(select(sq.c.t, func.count(), func.count().filter(sq.c.open)).group_by(sq.c.t))).all()
    for name, n in fold_tags([(t, int(a), int(b)) for t, a, b in rows]).values():
        if n:
            out["tags"][name] = n
    return out
