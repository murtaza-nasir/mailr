"""Learned order within sections, and usual items.

Both are counts over the list's own check log (`checkitem_checks`); no model is involved.

Learned order, per list and section:
1. Trips: consecutive checks of the section at most TRIP_GAP apart (device time of the tap). A trip
   with fewer than MIN_CHECKS checks is ignored. An uncheck within the trip removes that check.
2. In a trip of n checks the k-th item gets the position p = (k − 1) / (n − 1).
3. Each (list, section, key) keeps a rank r: the first trip sets r = p, each later trip sets
   r = r + STEP × (p − r).
4. A trip is learned once it has closed (TRIP_GAP after its last check), in order; the section
   remembers the last check it learned (`learned_through`). Reads apply closed trips first, and
   the worker does it TRIP_GAP after a check.

Usual items, per list and key: the distinct days it was checked (365 days); m = the median gap
between the last (up to USUAL_DAYS) days; a key is due when it was checked on at least
USUAL_MIN_DAYS days, is not on the list unchecked, is not dismissed, and at least
max(2, USUAL_FRACTION × m) days have passed. At most USUAL_MAX are shown, by days since / m.
"""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.notes.models import (
    CheckitemCheck,
    CheckitemRank,
    CheckitemUsualDismissed,
    Note,
    NoteItem,
    NoteSection,
)

TRIP_GAP = timedelta(minutes=45)
MIN_CHECKS = 3
STEP = 0.3
DRAG_END_STEP = 0.05
USUAL_MIN_DAYS = 3
USUAL_FRACTION = 0.8
USUAL_DAYS = 9
USUAL_MAX = 8
LOG_DAYS = 365


def section_key(section_id: str | None) -> str:
    return section_id or ""


@dataclass
class Check:
    key: str
    at: datetime


def trips(checks: list[Check]) -> list[list[Check]]:
    """Checks (sorted by time) cut into trips at gaps longer than TRIP_GAP."""
    out: list[list[Check]] = []
    for c in checks:
        if out and c.at - out[-1][-1].at <= TRIP_GAP:
            out[-1].append(c)
        else:
            out.append([c])
    return out


def positions(trip: list[Check]) -> dict[str, float]:
    """Each key's position in a trip, 0 for the first check and 1 for the last (a key checked
    twice keeps its first place)."""
    keys: list[str] = []
    for c in trip:
        if c.key not in keys:
            keys.append(c.key)
    n = len(keys)
    if n < 2:
        return {k: 0.0 for k in keys}
    return {k: i / (n - 1) for i, k in enumerate(keys)}


def apply_trip(ranks: dict[str, tuple[float, int]], trip: list[Check]) -> dict[str, tuple[float, int]]:
    """The ranks after one trip: r = p for a new key, else r + STEP × (p − r)."""
    out = dict(ranks)
    for k, p in positions(trip).items():
        if k in out:
            r, c = out[k]
            out[k] = (r + STEP * (p - r), c + 1)
        else:
            out[k] = (p, 1)
    return out


async def _group(db: AsyncSession, note: Note, sid: str | None) -> tuple[Any, datetime | None]:
    if sid is None:
        return note, note.top_learned_through
    sec = await db.get(NoteSection, sid)
    return sec, sec.learned_through if sec is not None else None


async def learn(db: AsyncSession, note: Note, sid: str | None, now: datetime | None = None) -> int:
    """Apply the closed trips of one section that are not learned yet. Returns how many."""
    now = now or datetime.now(UTC)
    grp, through = await _group(db, note, sid)
    if grp is None:
        return 0
    q = select(CheckitemCheck.key, CheckitemCheck.device_at).where(
        CheckitemCheck.note_id == note.id, CheckitemCheck.undone.is_(False),
        CheckitemCheck.section_id.is_(None) if sid is None else CheckitemCheck.section_id == sid)
    if through is not None:
        q = q.where(CheckitemCheck.device_at > through)
    rows = (await db.execute(q.order_by(CheckitemCheck.device_at, CheckitemCheck.id))).all()
    if not rows:
        return 0
    cut = trips([Check(r.key, r.device_at) for r in rows])
    closed = [t for t in cut if now - t[-1].at > TRIP_GAP]
    if not closed:
        return 0
    skey = section_key(sid)
    have = {r.key: (r.rank, r.trips) for r in (await db.execute(select(CheckitemRank).where(
        CheckitemRank.note_id == note.id, CheckitemRank.section_key == skey))).scalars()}
    ranks = dict(have)
    learned = 0
    for t in closed:
        if len({c.key for c in t}) >= MIN_CHECKS:
            ranks = apply_trip(ranks, t)
            learned += 1
    for k, (r, c) in ranks.items():
        if have.get(k) != (r, c):
            await db.execute(insert(CheckitemRank).values(note_id=note.id, section_key=skey, key=k, rank=r, trips=c)
                             .on_conflict_do_update(index_elements=["note_id", "section_key", "key"],
                                                    set_={"rank": r, "trips": c}))
    last = closed[-1][-1].at
    if sid is None:
        note.top_learned_through = last
        note.top_trips = (note.top_trips or 0) + learned
    else:
        grp.learned_through = last
        grp.trips = (grp.trips or 0) + learned
    return learned


async def learn_note(db: AsyncSession, note: Note, now: datetime | None = None) -> int:
    """Apply the closed trips of every section of a list (cheap when there are none)."""
    sids = {r[0] for r in (await db.execute(select(CheckitemCheck.section_id).where(
        CheckitemCheck.note_id == note.id).distinct())).all()}
    n = 0
    for sid in sids:
        if sid is not None:
            sec = await db.get(NoteSection, sid)
            if sec is None or sec.note_id != note.id:
                continue
        n += await learn(db, note, sid, now)
    return n


async def ranks_of(db: AsyncSession, note: Note) -> dict[tuple[str, str], tuple[float, int]]:
    """(section key, item key) → (rank, trips)."""
    rows = (await db.execute(select(CheckitemRank).where(CheckitemRank.note_id == note.id))).scalars()
    return {(r.section_key, r.key): (r.rank, r.trips) for r in rows}


async def drag(db: AsyncSession, note: Note, sid: str | None, key: str, prev: float | None, nxt: float | None,
               ) -> tuple[float, int] | None:
    """A move by hand in a learned section: the rank goes between the new neighbours' ranks (or
    the last rank + DRAG_END_STEP at the end) and counts as a trip. Returns the old (rank, trips)."""
    skey = section_key(sid)
    old = (await db.execute(select(CheckitemRank).where(
        CheckitemRank.note_id == note.id, CheckitemRank.section_key == skey, CheckitemRank.key == key))
    ).scalar_one_or_none()
    before = (old.rank, old.trips) if old is not None else None
    if prev is not None and nxt is not None:
        r = (prev + nxt) / 2
    elif prev is not None:
        r = prev + DRAG_END_STEP
    elif nxt is not None:
        r = nxt - DRAG_END_STEP
    else:
        r = 0.5
    c = (old.trips if old is not None else 0) + 1
    await db.execute(insert(CheckitemRank).values(note_id=note.id, section_key=skey, key=key, rank=r, trips=c)
                     .on_conflict_do_update(index_elements=["note_id", "section_key", "key"],
                                            set_={"rank": r, "trips": c}))
    return before


async def set_ranks(db: AsyncSession, note: Note, sid: str | None, ranks: dict[str, tuple[float, int] | None]) -> None:
    """Put ranks back (undo): None deletes a key's rank."""
    skey = section_key(sid)
    for k, v in ranks.items():
        if v is None:
            await db.execute(delete(CheckitemRank).where(CheckitemRank.note_id == note.id,
                                                         CheckitemRank.section_key == skey, CheckitemRank.key == k))
        else:
            await db.execute(insert(CheckitemRank).values(note_id=note.id, section_key=skey, key=k, rank=v[0],
                                                          trips=v[1])
                             .on_conflict_do_update(index_elements=["note_id", "section_key", "key"],
                                                    set_={"rank": v[0], "trips": v[1]}))


async def reset(db: AsyncSession, note: Note, sid: str | None, now: datetime | None = None) -> dict[str, Any]:
    """"Reset learned order": clear the section's ranks; checks before now teach nothing more.
    Returns what an undo needs."""
    now = now or datetime.now(UTC)
    skey = section_key(sid)
    old = {r.key: [r.rank, r.trips] for r in (await db.execute(select(CheckitemRank).where(
        CheckitemRank.note_id == note.id, CheckitemRank.section_key == skey))).scalars()}
    await db.execute(delete(CheckitemRank).where(CheckitemRank.note_id == note.id, CheckitemRank.section_key == skey))
    grp, through = await _group(db, note, sid)
    trips_before = note.top_trips if sid is None else grp.trips
    if sid is None:
        note.top_learned_through, note.top_trips = now, 0
    else:
        grp.learned_through, grp.trips = now, 0
    return {"ranks": old, "through": through.isoformat() if through else None, "trips": trips_before}


async def unreset(db: AsyncSession, note: Note, sid: str | None, saved: dict[str, Any]) -> None:
    await set_ranks(db, note, sid, {k: (float(v[0]), int(v[1])) for k, v in (saved.get("ranks") or {}).items()})
    through = datetime.fromisoformat(saved["through"]) if saved.get("through") else None
    if sid is None:
        note.top_learned_through, note.top_trips = through, int(saved.get("trips") or 0)
    else:
        grp = await db.get(NoteSection, sid)
        if grp is not None:
            grp.learned_through, grp.trips = through, int(saved.get("trips") or 0)


# ---------------------------------------------------------------- the check log


async def log_check(db: AsyncSession, note: Note, item: NoteItem, user_id: int | None, device_at: datetime) -> int:
    row = CheckitemCheck(note_id=note.id, section_id=item.section_id, item_id=item.id, key=item.key, text=item.text,
                         qty=qty_of(item), user_id=user_id, device_at=device_at)
    db.add(row)
    await db.flush()
    return row.id


async def unlog_check(db: AsyncSession, note: Note, item: NoteItem, device_at: datetime) -> int | None:
    """An uncheck within the trip removes the item's latest check from it. Returns its id."""
    row = (await db.execute(select(CheckitemCheck).where(
        CheckitemCheck.note_id == note.id, CheckitemCheck.item_id == item.id, CheckitemCheck.undone.is_(False),
        CheckitemCheck.device_at >= device_at - TRIP_GAP).order_by(CheckitemCheck.device_at.desc()).limit(1))
    ).scalar_one_or_none()
    if row is None:
        return None
    row.undone = True
    return row.id


def qty_of(item: NoteItem) -> dict[str, Any] | None:
    if item.qty_amount is None:
        return None
    from mailr.notes.itemadd import fmt_amount

    return {"amount": fmt_amount(item.qty_amount), "unit": item.qty_unit, "text": item.qty_text}


# ---------------------------------------------------------------- usual items


def median_gap(days: list[date]) -> float | None:
    """The median gap in days between the last (up to USUAL_DAYS) distinct days."""
    ds = sorted(set(days))[-USUAL_DAYS:]
    if len(ds) < 2:
        return None
    gaps = [(b - a).days for a, b in itertools.pairwise(ds)]
    return float(statistics.median(gaps))


@dataclass
class Usual:
    key: str
    text: str
    qty: dict[str, Any] | None
    section_id: str | None
    days_since: int
    interval: float
    count: int

    def out(self, section_names: dict[str, str]) -> dict[str, Any]:
        return {"key": self.key, "text": self.text, "qty": self.qty, "section_id": self.section_id,
                "section": section_names.get(self.section_id or ""), "days_since": self.days_since,
                "interval": round(self.interval, 2), "count": self.count}


def due(history: dict[str, dict[str, Any]], on_list: set[str], dismissed: set[str], today: date) -> list[Usual]:
    """The usual items to suggest. `history`: key → {days: [date], text, qty, section_id, count}."""
    out: list[Usual] = []
    for key, h in history.items():
        days = sorted(set(h["days"]))
        if len(days) < USUAL_MIN_DAYS or key in on_list or key in dismissed:
            continue
        m = median_gap(days)
        if not m:
            continue
        since = (today - days[-1]).days
        if since < max(2.0, USUAL_FRACTION * m):
            continue
        out.append(Usual(key, h["text"], h.get("qty"), h.get("section_id"), since, m, int(h.get("count") or len(days))))
    out.sort(key=lambda u: (-(u.days_since / u.interval), -u.count, u.text.lower()))
    return out[:USUAL_MAX]


async def history(db: AsyncSession, note: Note, tz: Any, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    now = now or datetime.now(UTC)
    rows = (await db.execute(select(CheckitemCheck).where(
        CheckitemCheck.note_id == note.id, CheckitemCheck.undone.is_(False),
        CheckitemCheck.device_at > now - timedelta(days=LOG_DAYS)).order_by(CheckitemCheck.device_at))).scalars()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        h = out.setdefault(r.key, {"days": [], "count": 0})
        h["days"].append(r.device_at.astimezone(tz).date())
        h["count"] += 1
        h["text"], h["qty"], h["section_id"] = r.text, r.qty, r.section_id
    return out


async def suggestions(db: AsyncSession, note: Note, viewer_id: int, tz: Any, now: datetime | None = None,
                      ) -> tuple[list[Usual], list[dict[str, Any]]]:
    """The usual items for this viewer, and the ones they hid."""
    now = now or datetime.now(UTC)
    today = now.astimezone(tz).date()
    hist = await history(db, note, tz, now)
    on_list = {r[0] for r in (await db.execute(select(NoteItem.key).where(
        NoteItem.note_id == note.id, NoteItem.deleted_at.is_(None), NoteItem.checked.is_(False)))).all()}
    hidden_rows = (await db.execute(select(CheckitemUsualDismissed).where(
        CheckitemUsualDismissed.user_id == viewer_id, CheckitemUsualDismissed.note_id == note.id))).scalars().all()
    dismissed = {h.key for h in hidden_rows if h.until is None or h.until > today}
    hidden = [{"key": h.key, "text": h.text, "until": h.until.isoformat() if h.until else None}
              for h in hidden_rows if h.until is None or h.until > today]
    # A key whose section was deleted goes to the top group.
    secs = {s.id for s in (await db.execute(select(NoteSection).where(
        NoteSection.note_id == note.id, NoteSection.deleted_at.is_(None)))).scalars()}
    for h in hist.values():
        if h.get("section_id") and h["section_id"] not in secs:
            h["section_id"] = None
    return due(hist, on_list, dismissed, today), hidden


async def dismiss(db: AsyncSession, note: Note, viewer_id: int, key: str, *, never: bool, tz: Any,
                  now: datetime | None = None) -> dict[str, Any] | None:
    """Hide a suggestion for one interval (or for good). Returns the row it replaced, for undo."""
    now = now or datetime.now(UTC)
    today = now.astimezone(tz).date()
    hist = (await history(db, note, tz, now)).get(key) or {}
    m = median_gap(hist.get("days") or []) or 7
    until = None if never else today + timedelta(days=max(1, round(m)))
    old = await db.get(CheckitemUsualDismissed, (viewer_id, note.id, key))
    before = ({"until": old.until.isoformat() if old.until else None} if old is not None else None)
    await db.execute(insert(CheckitemUsualDismissed).values(
        user_id=viewer_id, note_id=note.id, key=key, text=(hist.get("text") or key)[:1000], until=until)
        .on_conflict_do_update(index_elements=["user_id", "note_id", "key"], set_={"until": until}))
    return before


async def undismiss(db: AsyncSession, note: Note, viewer_id: int, key: str) -> None:
    await db.execute(delete(CheckitemUsualDismissed).where(
        CheckitemUsualDismissed.user_id == viewer_id, CheckitemUsualDismissed.note_id == note.id,
        CheckitemUsualDismissed.key == key))


async def remembered_section(db: AsyncSession, note: Note, key: str) -> str | None:
    """The section a key was last in on this list (its last check, else its newest item)."""
    secs = {s.id for s in (await db.execute(select(NoteSection).where(
        NoteSection.note_id == note.id, NoteSection.deleted_at.is_(None)))).scalars()}
    row = (await db.execute(select(CheckitemCheck.section_id).where(
        CheckitemCheck.note_id == note.id, CheckitemCheck.key == key).order_by(CheckitemCheck.device_at.desc())
        .limit(1))).first()
    if row is not None:
        return row[0] if row[0] in secs else None
    row = (await db.execute(select(NoteItem.section_id).where(NoteItem.note_id == note.id, NoteItem.key == key)
                            .order_by(NoteItem.updated_at.desc()).limit(1))).first()
    if row is not None and row[0] in secs:
        return row[0]
    return None
