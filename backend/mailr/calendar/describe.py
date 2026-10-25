"""Calendars and events as a person reads them: on approval cards, event citations and in what the
assistant is told.

An approval card for a change to one occurrence of a recurring event says which occurrence, and a
calendar is never named by an internal identifier. Here:

- `calendar_label` is the calendar's name as the calendar list shows it, with "shared by Rosa" or
  "the Family group" for a calendar someone else owns. Never a collection URL, an id, the internal
  store account (…@mailr.internal) or Stalwart's stock name "Calendar (alice@…)".
- `event_snapshot` is the event as it is now (the occurrence, when one is named): title, place,
  times, zone, rule and calendar, for the "before" side of a change.
- `for_model` takes calendar identifiers out of a written event before the assistant sees it, so
  it never repeats one to the user.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr import i18n

_UUIDISH = re.compile(r"^[0-9a-f-]{16,}$", re.IGNORECASE)
_ADDRESS_PART = re.compile(r"\s*\([^()]*@[^()]*\)\s*")


def looks_internal(v: Any) -> bool:
    """A value no person should read as a calendar's name: a URL or path, a number, an address."""
    s = str(v or "").strip()
    return (not s or "/" in s or "@" in s or s.isdigit() or s.lower().startswith(("http:", "https:"))
            or bool(_UUIDISH.match(s)))


def calendar_name(displayname: str | None, href: str | None, fallback: str = "Calendar") -> str:
    """The calendar's own name, cleaned: Stalwart's stock "Calendar (alice@…)" reads "Calendar", an
    address in brackets is dropped, and a calendar without a name gets its last path segment only
    when that is a word ("work"), else "Calendar"."""
    from mailr.calendar.builtin import STOCK_NAME

    if STOCK_NAME.match(str(displayname or "").strip()):
        return "Calendar"
    name = _ADDRESS_PART.sub(" ", str(displayname or "")).strip()
    if name and "@" in name:
        name = ""
    if not name:
        slug = str(href or "").rstrip("/").rsplit("/", 1)[-1]
        name = slug.replace("-", " ").replace("_", " ").strip().capitalize() \
            if re.fullmatch(r"[A-Za-z][A-Za-z _-]{0,40}", slug or "") else ""
    return name or fallback


def calendar_label(displayname: str | None, href: str | None, shared: dict[str, Any] | None = None) -> str:
    """"Work", "Family (the Family group)", "Rosa's calendar (shared by Rosa)": the name and, for a
    calendar someone else owns, whose it is (the calendar list's sharedLabel)."""
    if shared and shared.get("kind") == "group" and shared.get("group"):
        group = str(shared["group"])
        name = calendar_name(displayname, None, fallback="")  # a group account's name is no name
        return group if name.lower() in ("", group.lower(), "calendar") else f"{name} (the {group} group)"
    name = calendar_name(displayname, href)
    if not shared:
        return name
    if shared.get("kind") == "user":
        return f"{name} (shared by {shared.get('owner') or 'someone'})"
    return name


async def labels(db: AsyncSession, user_id: int, p: Any = None) -> dict[str, str]:
    """{collection URL: label} for every calendar and task list the caller can see."""
    from mailr.calendar import mirror

    ov = await mirror.kind_overrides(db, user_id)
    cols = await mirror.collections_of(db, await mirror.visible_connector_ids(db, user_id, p))
    return {c.href: calendar_label(c.displayname, c.href, (ov.get(c.href) or {}).get("shared")) for c in cols}


async def label_of(db: AsyncSession, user_id: int, ref: Any, p: Any = None) -> str | None:
    """The label of a calendar named by its URL (or id); None when the caller cannot see it."""
    if not ref:
        return None
    from mailr.calendar.dav import norm_href
    from mailr.models import CalCollection

    got = await labels(db, user_id, p)
    s = str(ref).strip()
    if s in got:
        return got[s]
    norm = norm_href(s).rstrip("/")
    for href, label in got.items():
        if norm_href(href).rstrip("/") == norm:
            return label
    if s.isdigit():
        c = await db.get(CalCollection, int(s))
        if c is not None and c.href in got:
            return got[c.href]
    return None


async def event_snapshot(db: AsyncSession, p: Any, uid: str, occurrence: str | None = None) -> dict[str, Any] | None:
    """The event as it is now, for an approval card: the named occurrence of a series (its own
    times, and an override's own title or place), else the event itself. Times are canonical
    (UTC ISO, or YYYY-MM-DD for all-day). None when the caller cannot see the event."""
    from mailr.calendar import ical, mirror
    from mailr.calendar import tz as tzmod
    from mailr.models import CalCollection, CalOccurrence

    try:
        obj = await mirror.find_by_uid(db, p.user_id, uid, "event", p)
    except Exception:  # noqa: BLE001  no calendar
        return None
    if obj is None:
        return None
    coll = await db.get(CalCollection, obj.collection_id)
    shared = ((await mirror.kind_overrides(db, p.user_id)).get(coll.href) or {}).get("shared") if coll else None
    all_day = bool(obj.all_day)
    start, end = (obj.start_date, obj.end_date) if all_day else (obj.start_at, obj.end_at)
    out: dict[str, Any] = {"summary": obj.summary or "", "location": obj.location or "", "all_day": all_day,
                           "tz": tzmod.iana(obj.tzid), "rrule": obj.rrule or None, "recurring": bool(obj.recurring),
                           "first": ical.out_when(start),
                           "calendar": calendar_label(coll.displayname, coll.href, shared) if coll else None}
    if occurrence and obj.recurring:
        zone = await tzmod.user_zone(db, p.user_id)
        want = ical.normalize_occurrence(occurrence, zone)
        row = (await db.execute(select(CalOccurrence).where(CalOccurrence.object_id == obj.id,
                                                            CalOccurrence.occurrence == want))).scalar_one_or_none()
        if row is not None:
            all_day = bool(row.all_day)
            start, end = (row.start_at.date(), row.end_at.date()) if all_day else (row.start_at, row.end_at)
            out.update(all_day=all_day, summary=row.summary or out["summary"], location=row.location or out["location"])
        else:
            # Outside the materialized window: the occurrence's start, with the series' length.
            s = ical.parse_when(want, zone)
            if s is not None and start is not None and end is not None and type(s) is type(start):
                start, end = s, s + (end - start)
            elif s is not None:
                start, end = s, s + (timedelta(days=1) if all_day else timedelta(hours=1))
        out["occurrence"] = want
    out["start"], out["end"] = ical.out_when(start), ical.out_when(end)
    return out


async def name_list(db: AsyncSession, user_id: int, task: dict[str, Any], p: Any = None) -> dict[str, Any]:
    """A task preview with its list by the name the user knows (`list_name`), never only its URL:
    an approval card once read "List: https://…/dav/calendars/…/"."""
    if task.get("list") and not task.get("list_name"):
        task = {**task, "list_name": await label_of(db, user_id, task["list"], p) or "another list"}
    return task


def change_lines(changes: dict[str, Any], list_name: str | None = None) -> list[str]:
    """The fallback "key: value" lines of a task change, with a moved-to list by its name."""
    return [f"{k}: {list_name if k == 'list' and list_name else v}" for k, v in changes.items()]


_INTERNAL_KEYS = ("calendar_id", "connector_id", "href", "collection_id", "calendar_url")


async def for_model(db: AsyncSession, user_id: int, rec: Any, p: Any = None) -> Any:
    """A written event or task without calendar identifiers: `calendar` (and a task's `list`) is
    the label, and ids and URLs are left out. The assistant names calendars by these labels."""
    if not isinstance(rec, dict):
        return rec
    out = {k: v for k, v in rec.items() if k not in _INTERNAL_KEYS}
    for k in ("calendar", "list"):
        if out.get(k) and looks_internal(out[k]):
            out[k] = await label_of(db, user_id, out[k], p) or "a calendar"
    if out.get("rrule") and "repeats" not in out:
        from mailr.agent import when

        out["repeats"] = when.repeat_text(out["rrule"], out.get("start") or out.get("due"))
    for k in ("event", "task"):
        if isinstance(out.get(k), dict):
            out[k] = await for_model(db, user_id, out[k], p)
    return out


_VERB = {"update": "Change", "delete": "Delete", "cancel": "Cancel"}


async def event_preview(db: AsyncSession, p: Any, op: str, uid: str, occurrence: str | None, scope: str | None,
                        changes: dict[str, Any] | None = None, *, summary: str | None = None) -> dict[str, Any]:
    """The preview of a change, delete or cancel of an event (agent/present.py draws it): the event
    as it is now (`before`, the occurrence when one is named), the scope, and the changes, with a
    moved-to calendar as its label. `summary` is the title to use when the event cannot be read."""
    snap = await event_snapshot(db, p, uid, occurrence)
    if snap and snap.get("recurring") and not occurrence and scope in ("this", "following"):
        scope = "all"  # without an occurrence the write changes the whole series: the card says so
    ev: dict[str, Any] = {"uid": uid, **(changes or {})}
    if ev.get("calendar"):
        ev["calendar_name"] = await label_of(db, p.user_id, ev["calendar"], p) or "another calendar"
    name = (snap or {}).get("summary") or summary or ""
    one = bool(snap and snap.get("recurring") and scope == "this")
    verb = _VERB.get(op, "Change")
    title = (f"{verb} one occurrence of “{name[:80]}”" if one else f"{verb} “{name[:80]}”") if name \
        else f"{verb} an event"
    vk = op if op in ("delete", "cancel") else "change"
    title_msg = i18n.msg(f"approvals.event.{vk}_occurrence" if one else f"approvals.event.{vk}",
                         name=i18n.msg("approvals.event.quoted", name=name[:80])) if name \
        else i18n.msg(f"approvals.event.{vk}_unnamed")
    out: dict[str, Any] = {"title": title, "title_msg": title_msg, "kind": "event", "event": ev, "scope": scope or "all",
                           "lines": [name] if name else []}
    if snap is not None:
        out["before"] = snap
        out["occurrence"] = snap.get("occurrence")
    elif summary:
        ev.setdefault("summary", summary)
    if op in ("delete", "cancel"):
        out["danger"] = True
    return out


async def rsvp_lines(db: AsyncSession, p: Any, uid: str, occurrence: str | None, scope: str) -> list[str]:
    """The "Key: value" lines of an answer to an invitation in the calendar: which occurrence and
    how much of the series, in the user's zone and clock (agent/present.py reads them as fields)."""
    from mailr.calendar import tz as tzmod
    from mailr.models import User

    snap = await event_snapshot(db, p, uid, occurrence if scope != "all" else None)
    if snap is None:
        return []
    zone = await tzmod.user_zone(db, p.user_id)
    user = await db.get(User, p.user_id)
    clock = "24h" if ((user.settings or {}) if user else {}).get("time_format") == "24h" else "12h"
    from mailr.agent.present import _applies, en

    got = _applies(snap, scope, zone, clock)
    return [f"{en(got[0])}: {en(got[1])}"] if got else []
