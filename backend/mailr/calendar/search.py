"""Search over the calendar mirror.

`parse` splits a query into free words and operators; `conditions` turns the operators into
filters on cal_objects; `tsquery` builds the full-text query (websearch syntax on the `tsv`
column with the 'simple' configuration, the last word as a prefix, as in core/search.py).
`search` serves GET /calendar/search: events come back as their next upcoming occurrence (else
the most recent one), tasks as tasks, ranked by ts_rank and then by nearness to now.

Operators: `with:` (a guest or the organizer), `cal:` / `list:` (a collection by name), `tag:` or
`@tag`, `after:` / `before:` (dates), `due:<date` / `due:>date`, `is:recurring`, `is:invite`,
`is:done`, `is:overdue`, `has:guests`, `has:video`, `has:email`, `loc:`, `pri:high|medium|low`.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from sqlalchemy import Text, and_, cast, false, func, literal_column, or_, select, true
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import expand, ical, mirror
from mailr.calendar import tz as tzmod
from mailr.models import CalCollection, CalObject, CalOccurrence, EventLink

OPS = {"with", "cal", "list", "tag", "after", "before", "due", "is", "has", "loc", "pri"}
_TOKEN = re.compile(r'-?[A-Za-z]+:"[^"]*"|-?[A-Za-z]+:\S+|"[^"]*"|\S+')
# Links that make an event a video call.
VIDEO = ("zoom.us/", "meet.google.com/", "teams.microsoft.com/", "teams.live.com/", "webex.com/", "whereby.com/",
         "meet.jit.si/", "jitsi", "gotomeeting.com/", "chime.aws/", "facetime.apple.com/")
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


@dataclass
class Query:
    words: list[str] = field(default_factory=list)  # free words; quoted phrases keep their quotes
    ops: list[tuple[str, str, bool]] = field(default_factory=list)  # (operator, value, negated)

    def has(self, op: str, value: str | None = None) -> bool:
        return any(o == op and (value is None or v == value) and not neg for o, v, neg in self.ops)


def parse(q: str | None) -> Query:
    out = Query()
    for tok in _TOKEN.findall(q or ""):
        neg = tok.startswith("-") and len(tok) > 1
        body = tok[1:] if neg else tok
        if body.startswith("@") and len(body) > 1:
            out.ops.append(("tag", body[1:].strip('"'), neg))
            continue
        if ":" in body and not body.startswith('"'):
            op, val = body.split(":", 1)
            op = op.lower()
            if op in OPS and val:
                out.ops.append((op, val.strip('"'), neg))
                continue
        out.words.append(tok)
    return out


# ---------------- full text ----------------


def _lexemes(word: str) -> list[str]:
    return [w for w in re.split(r"[^\w]+", word.lower()) if w]


def tsquery(words: list[str]) -> Any | None:
    """websearch_to_tsquery('simple') for every word but the last, which is a prefix (`:*`)."""
    if not words:
        return None
    head, last = words[:-1], words[-1]
    parts = []
    if head:
        parts.append(func.websearch_to_tsquery("simple", " ".join(head)))
    if last.startswith('"') or last.startswith("-"):
        parts.append(func.websearch_to_tsquery("simple", last))
    else:
        lex = _lexemes(last)
        if lex:
            # Only word characters reach to_tsquery, so the quoting below cannot break out.
            text_q = " & ".join(f"'{x}'" for x in lex[:-1])
            text_q = (text_q + " & " if text_q else "") + f"'{lex[-1]}':*"
            parts.append(func.to_tsquery("simple", text_q))
    if not parts:
        return None
    q = parts[0]
    for x in parts[1:]:
        q = q.op("&&")(x)
    return q


def text_condition(words: list[str]) -> tuple[Any, Any]:
    """(condition, rank expression) for the free words."""
    tq = tsquery(words)
    plain = " ".join(w.strip('"') for w in words if not w.startswith("-")).strip()
    if tq is None:
        return (true(), literal_column("0.0")) if not plain else (
            CalObject.summary.ilike(f"%{_like(plain)}%"), literal_column("0.0"))
    cond = CalObject.tsv.op("@@")(tq)
    if plain:
        cond = or_(cond, CalObject.summary.ilike(f"%{_like(plain)}%"))
    return cond, func.ts_rank(CalObject.tsv, tq)


def _like(s: str) -> str:
    return s.replace("\\", "").replace("%", "").replace("_", "")


# ---------------- operators ----------------


def parse_day(val: str, now: datetime, user_tz: tzinfo, *, forward: bool = True) -> date | None:
    """A date in a query: ISO, today / tomorrow / yesterday, or a weekday (the next one; the
    last one when `forward` is false)."""
    v = val.strip().lower()
    today = now.astimezone(user_tz).date()
    if v in ("today", "now"):
        return today
    if v == "tomorrow":
        return today + timedelta(days=1)
    if v == "yesterday":
        return today - timedelta(days=1)
    if v[:3] in WEEKDAYS and v.isalpha():
        wd = WEEKDAYS[v[:3]]
        if forward:
            return today + timedelta(days=(wd - today.weekday()) % 7 or 7)
        return today - timedelta(days=(today.weekday() - wd) % 7 or 7)
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return None


def _day_start(d: date, user_tz: tzinfo) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=user_tz).astimezone(UTC)


def _cats_have(tag: str) -> Any:
    """The object carries this tag (CATEGORIES), compared without case."""
    t = tag.lower()
    return func.lower(func.array_to_string(CalObject.categories, "\x1f")).op("~")(
        f"(^|\x1f){re.escape(t)}(\x1f|$)")


def is_done() -> Any:
    return or_(CalObject.status == "COMPLETED", CalObject.completed_at.is_not(None))


IS_VALUES = ("recurring", "invite", "done", "overdue", "open", "todo")
HAS_VALUES = ("guests", "video", "email", "reminder")
PRI_VALUES = ("high", "medium", "med", "low", "none")


def unreadable(qr: Query, *, now: datetime, user_tz: tzinfo) -> list[str]:
    """The operators of a query that `conditions` cannot read, as written ("due:next-week").
    `conditions` makes each of them match nothing; the assistant's tools refuse them instead, so
    the model learns why a search came back empty."""
    bad = []
    for op, val, neg in qr.ops:
        v = val.lower()
        ok = True
        if op in ("after", "before"):
            ok = parse_day(val, now, user_tz) is not None
        elif op == "due":
            m = re.match(r"^(<=|>=|<|>|=)?(.+)$", val)
            ok = bool(m) and parse_day(m[2], now, user_tz) is not None
        elif op == "is":
            ok = v in IS_VALUES
        elif op == "has":
            ok = v in HAS_VALUES
        elif op == "pri":
            ok = v in PRI_VALUES or v.isdigit()
        if not ok:
            bad.append(f"{'-' if neg else ''}{op}:{val}")
    return bad


def conditions(qr: Query, *, now: datetime, user_tz: tzinfo, collections: list[CalCollection], p: Any) -> list[Any]:
    """Filters for the operators of a query (unknown values match nothing)."""
    from mailr.core.visibility import visible_conversation_ids

    out = []
    for op, val, neg in qr.ops:
        v = val.lower()
        c: Any = None
        if op == "with":
            like = f"%{_like(v)}%"
            c = or_(func.lower(cast(CalObject.attendees, Text)).like(like),
                    func.lower(func.coalesce(CalObject.organizer, "")).like(like),
                    func.lower(func.coalesce(CalObject.organizer_name, "")).like(like))
        elif op in ("cal", "list"):
            ids = [x.id for x in collections if v in (x.displayname or x.href.rstrip("/").split("/")[-1]).lower()
                   or v == str(x.id) or v.rstrip("/") == x.href.lower().rstrip("/")]
            c = CalObject.collection_id.in_(ids) if ids else false()
        elif op == "tag":
            c = _cats_have(val)
        elif op in ("after", "before"):
            d = parse_day(val, now, user_tz, forward=op == "after")
            if d is None:
                c = false()
            else:
                at = _day_start(d + timedelta(days=1), user_tz) if op == "after" else _day_start(d, user_tz)
                when = func.coalesce(CalObject.due_at, CalObject.start_at)
                if op == "after":  # after the day: from the next midnight on (series that run on count too)
                    c = or_(when >= at, and_(CalObject.recurring.is_(True),
                                             or_(CalObject.range_end.is_(None), CalObject.range_end >= at)))
                else:
                    c = when < at
        elif op == "due":
            m = re.match(r"^(<=|>=|<|>|=)?(.+)$", val)
            cmp_, rest = (m[1] or "="), m[2]
            d = parse_day(rest, now, user_tz)
            if d is None:
                c = false()
            else:
                due_day = func.coalesce(CalObject.due_date, func.date(func.timezone(tzmod.zone_name(user_tz) or "UTC",
                                                                                    CalObject.due_at)))
                c = {"<": due_day < d, "<=": due_day <= d, ">": due_day > d, ">=": due_day >= d, "=": due_day == d}[cmp_]
                c = and_(CalObject.due_at.is_not(None), c)
        elif op == "is":
            if v == "recurring":
                c = CalObject.recurring.is_(True)
            elif v == "invite":
                c = and_(CalObject.kind == "event", CalObject.my_partstat.is_not(None), CalObject.is_organizer.is_(False))
            elif v == "done":
                c = is_done()
            elif v == "overdue":
                today = now.astimezone(user_tz).date()
                c = and_(~is_done(), or_(and_(CalObject.due_date.is_not(None), CalObject.due_date < today),
                                         and_(CalObject.due_date.is_(None), CalObject.due_at < now)))
            elif v in ("open", "todo"):
                c = ~is_done()
            else:
                c = false()
        elif op == "has":
            if v == "guests":
                c = CalObject.attendee_count > 0
            elif v == "video":
                hay = func.lower(func.concat(CalObject.location, " ", CalObject.description, " ",
                                             func.coalesce(CalObject.url, "")))
                c = or_(*[hay.like(f"%{x}%") for x in VIDEO], CalObject.ics.ilike("%X-GOOGLE-CONFERENCE%"),
                        CalObject.ics.ilike("%CONFERENCE;%"), CalObject.ics.ilike("%CONFERENCE:%"))
            elif v == "email":
                c = CalObject.uid.in_(select(EventLink.uid).where(
                    EventLink.user_id == CalObject.user_id, EventLink.conversation_id.in_(visible_conversation_ids(p))))
            elif v == "reminder":
                c = func.cardinality(CalObject.alarms) > 0
            else:
                c = false()
        elif op == "loc":
            c = CalObject.location.ilike(f"%{_like(val)}%")
        elif op == "pri":
            rng = {"high": (1, 4), "medium": (5, 5), "med": (5, 5), "low": (6, 9), "none": (0, 0)}.get(v)
            if rng is None and v.isdigit():
                rng = (int(v), int(v))
            c = func.coalesce(CalObject.priority, 0).between(*rng) if rng else false()
        if c is not None:
            out.append(~c if neg else c)
    return out


# ---------------- GET /calendar/search ----------------


CANDIDATES = 500


async def search(db: AsyncSession, user_id: int, p: Any, q: str, *, kind: str = "all",
                 frm: datetime | None = None, to: datetime | None = None, calendars: list[str] | None = None,
                 limit: int = 50, offset: int = 0, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    user_tz = await tzmod.user_zone(db, user_id)
    await mirror.ensure_synced(db, user_id, p)
    qr = parse(q)
    ev_cols = await mirror.event_collections(db, user_id, p, calendars) if kind in ("event", "all") else []
    tk_cols = await mirror.task_collections(db, user_id, p, calendars) if kind in ("task", "all") else []
    cols = {c.id: c for c in ev_cols + tk_cols}
    if not cols:
        return {"items": [], "next_cursor": None}
    text_c, rank = text_condition(qr.words)
    kinds = []
    if ev_cols:
        kinds.append(and_(CalObject.kind == "event", CalObject.collection_id.in_([c.id for c in ev_cols])))
    if tk_cols:
        kinds.append(and_(CalObject.kind == "task", CalObject.collection_id.in_([c.id for c in tk_cols])))
    conds = [CalObject.user_id == user_id, CalObject.deleted_at.is_(None), or_(*kinds), text_c,
             *conditions(qr, now=now, user_tz=user_tz, collections=list(cols.values()), p=p)]
    if frm is not None or to is not None:
        lo = frm or datetime(1, 1, 2, tzinfo=UTC)
        hi = to or datetime(9999, 12, 30, tzinfo=UTC)
        # Tasks by their due; events by an occurrence in the range (series by their extent).
        in_range = or_(
            and_(CalObject.kind == "task", CalObject.due_at >= lo, CalObject.due_at < hi),
            and_(CalObject.kind == "event", CalObject.recurring.is_(False), CalObject.start_at < hi,
                 func.coalesce(CalObject.end_at, CalObject.start_at) >= lo),
            and_(CalObject.kind == "event", CalObject.recurring.is_(True),
                 or_(CalObject.range_start.is_(None), CalObject.range_start < hi),
                 or_(CalObject.range_end.is_(None), CalObject.range_end >= lo)))
        conds.append(in_range)
    order = [rank.desc(), CalObject.id.desc()] if qr.words else [CalObject.id.desc()]
    rows = (await db.execute(select(CalObject, rank.label("rank")).where(*conds)
                             .order_by(*order).limit(CANDIDATES))).all()
    local = {c.connector_id for c in cols.values()}
    from mailr.models import Connector

    lo_conns = set((await db.execute(select(Connector.id).where(
        Connector.id.in_(list(local)), Connector.data_class == "local_only"))).scalars())
    events = [o for o, _ in rows if o.kind == "event"]
    occ = await representative_occurrences(db, events, now, user_tz, frm, to)
    tasks = [o for o, _ in rows if o.kind == "task"]
    counts = await mirror.subtask_counts(db, user_id, [t.uid for t in tasks])
    items = []
    for obj, r in rows:
        coll = cols.get(obj.collection_id)
        lo_flag = obj.connector_id in lo_conns
        if obj.kind == "event":
            got = occ.get(obj.id)
            if got is None:
                if frm is not None or to is not None:
                    continue  # no occurrence inside the range
                out = mirror.event_out(obj, coll, user_tz, local_only=lo_flag)
                when = obj.start_at
            else:
                o_str, s, e, all_day, is_ov, ov = got
                out = mirror.instance_out(obj, coll, user_tz, occurrence=o_str, start_at=s, end_at=e, all_day=all_day,
                                          is_override=is_ov, ov=ov, local_only=lo_flag)
                when = s
        else:
            out = mirror.task_out(obj, coll, user_tz, subtasks=counts.get(obj.uid, (0, 0)), local_only=lo_flag)
            when = obj.due_at
        near = abs((when - now).total_seconds()) if when else 1e12
        items.append(({"kind": obj.kind, "rank": round(float(r or 0), 6), **out}, near, when is not None and when >= now))
    # Rank first (in steps, so tiny differences do not beat time), then upcoming before past,
    # then nearness to now.
    items.sort(key=lambda x: (-round(x[0]["rank"], 2), not x[2], x[1]))
    page = [i[0] for i in items[offset:offset + limit]]
    nxt = offset + limit if len(items) > offset + limit else None
    return {"items": page, "next_cursor": str(nxt) if nxt is not None else None}


async def representative_occurrences(db: AsyncSession, events: list[CalObject], now: datetime, user_tz: tzinfo,
                                     frm: datetime | None = None, to: datetime | None = None
                                     ) -> dict[int, tuple[str, datetime, datetime, bool, bool, Any]]:
    """{object id: (occurrence, start, end, all_day, is_override, override fields)}: the next
    occurrence from now (or from `frm`) inside the range, else the latest one before it."""
    out: dict[int, Any] = {}
    if not events:
        return out
    lo = frm or datetime(1, 1, 2, tzinfo=UTC)
    hi = to or datetime(9999, 12, 30, tzinfo=UTC)
    pivot = min(max(now, lo), hi)
    ids = [e.id for e in events]
    nxt = (await db.execute(
        select(CalOccurrence).ext(distinct_on(CalOccurrence.object_id)).where(
            CalOccurrence.object_id.in_(ids), CalOccurrence.end_at > pivot, CalOccurrence.start_at < hi,
            CalOccurrence.cancelled.is_(False))
        .order_by(CalOccurrence.object_id, CalOccurrence.start_at))).scalars().all()
    prev = (await db.execute(
        select(CalOccurrence).ext(distinct_on(CalOccurrence.object_id)).where(
            CalOccurrence.object_id.in_(ids), CalOccurrence.end_at <= pivot, CalOccurrence.end_at >= lo,
            CalOccurrence.cancelled.is_(False))
        .order_by(CalOccurrence.object_id, CalOccurrence.start_at.desc()))).scalars().all()
    by = {o.id: o for o in events}
    cache: dict[int, Any] = {}
    for row in list(prev) + list(nxt):  # the next one wins
        obj = by[row.object_id]
        ov = mirror.override_fields(obj, user_tz, cache).get(row.occurrence) if row.is_override else None
        out[row.object_id] = (row.occurrence, row.start_at, row.end_at, row.all_day, row.is_override, ov)
    # Series whose next occurrence lies past the materialized window: expanded on read.
    for obj in events:
        if not obj.recurring or (obj.expanded_until and obj.expanded_until >= hi):
            continue
        if obj.id in out and out[obj.id][1] >= pivot:
            continue
        start = max(pivot, obj.expanded_until or pivot)
        try:  # off the event loop
            [(cal, insts)] = await asyncio.to_thread(expand.parse_and_expand_many, [obj.ics], start,
                                                     min(hi, start + timedelta(days=3660)), user_tz, 1)
        except Exception:  # noqa: BLE001
            continue
        if cal is None:
            continue
        if insts:
            i = insts[0]
            ov = ical.component_fields(i.comp, cal, user_tz) if i.is_override else None
            out[obj.id] = (i.occurrence, i.start_at, i.end_at, i.all_day, i.is_override, ov)
    return out


async def matching_event_uids(db: AsyncSession, user_id: int, p: Any, text: str,
                              calendars: list[str] | None = None) -> set[str] | None:
    """UIDs of events matching `text` (with operators); None when the text is empty."""
    qr = parse(text)
    if not qr.words and not qr.ops:
        return None
    cols = await mirror.event_collections(db, user_id, p, calendars)
    if not cols:
        return set()
    user_tz = await tzmod.user_zone(db, user_id)
    cond, _ = text_condition(qr.words)
    rows = (await db.execute(select(CalObject.uid).where(
        CalObject.user_id == user_id, CalObject.kind == "event", CalObject.deleted_at.is_(None),
        CalObject.collection_id.in_([c.id for c in cols]), cond,
        *conditions(qr, now=datetime.now(UTC), user_tz=user_tz, collections=cols, p=p)))).scalars()
    return set(rows)
