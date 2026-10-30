"""The calendar mirror: upsert and soft delete of CalDAV resources, the API shapes
(section 2) and the read queries.

Every read goes through `ensure_synced` first: a connector that never finished a first sync is
synced inline (15 s budget), and a connector not checked for READ_REFRESH seconds gets an inline
incremental sync (3 s budget). Reads never depend on the worker having run.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from icalendar import Calendar
from sqlalchemy import and_, delete, func, insert, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from mailr.calendar import expand, ical, phonecal
from mailr.calendar import tz as tzmod
from mailr.calendar.describe import calendar_name
from mailr.config import get_settings
from mailr.models import (
    CalCollection,
    CalObject,
    CalOccurrence,
    Connector,
    Identity,
    ReminderSchedule,
    User,
)

log = logging.getLogger("mailr.calendar")

INITIAL_BUDGET_S = 15.0
REFRESH_BUDGET_S = 3.0
TOMBSTONE_DAYS = 60
LIST_DESCRIPTION_MAX = 2000


def read_refresh_seconds() -> float:
    """How old a connector's last check may be before a read syncs it inline. 0 in dev and tests,
    so a change made by another client is seen by the next read."""
    return 0.0 if get_settings().dev else 20.0


def _restricted(p: Any) -> bool:
    return p is not None and getattr(p, "token", None) is not None


# ---------------- who and where ----------------


async def visible_connectors(db: AsyncSession, user_id: int, p: Any = None, *, subscriptions: bool = False
                             ) -> list[Connector]:
    """The calendar accounts p may see. `subscriptions`: also the read-only calendar links
    (calendar/webcal.py, kind "webcal") and the phone calendars' busy times (calendar/phonecal.py,
    kind "phone"), for reading; writes and DAV syncs leave it off."""
    kinds = ("caldav", "webcal", "phone") if subscriptions else ("caldav",)
    rows = (await db.execute(select(Connector).where(
        Connector.user_id == user_id, Connector.kind.in_(kinds), Connector.enabled.is_(True))
        .order_by(Connector.id))).scalars().all()
    if any(r.builtin for r in rows):
        from mailr.calendar import builtin

        if not await builtin.shown_for(db, user_id):  # switched off, or not for this user: kept, not shown
            rows = [r for r in rows if not r.builtin]
    if _restricted(p):
        rows = [r for r in rows if p.can_see_connector(r)]
    return list(rows)


async def visible_connector_ids(db: AsyncSession, user_id: int, p: Any = None) -> list[int]:
    """For reads: the calendar accounts and the subscribed calendar links."""
    return [c.id for c in await visible_connectors(db, user_id, p, subscriptions=True)]


async def my_addresses(db: AsyncSession, user_id: int) -> set[str]:
    user = await db.get(User, user_id)
    rows = (await db.execute(select(Identity.email).where(Identity.user_id == user_id))).scalars()
    return {e.lower() for e in rows if e} | ({user.email.lower()} if user is not None else set())


async def collections_of(db: AsyncSession, connector_ids: list[int], *, include_deleted: bool = False
                         ) -> list[CalCollection]:
    if not connector_ids:
        return []
    q = select(CalCollection).where(CalCollection.connector_id.in_(connector_ids))
    if not include_deleted:
        q = q.where(CalCollection.deleted_at.is_(None))
    return list((await db.execute(q.order_by(CalCollection.sort_order, CalCollection.server_order.nulls_last(),
                                              CalCollection.id))).scalars())


def kinds_of(coll: CalCollection, overrides: dict[str, Any]) -> tuple[bool, bool]:
    comps = [c.upper() for c in coll.components or []] or ["VEVENT", "VTODO", "VJOURNAL"]
    ev, tk = "VEVENT" in comps, "VTODO" in comps
    ov = overrides.get(coll.href) or {}
    return bool(ov.get("events", ev)), bool(ov.get("tasks", tk))


def _parent(href: str) -> str:
    return href.rstrip("/").rsplit("/", 1)[0]


async def builtin_events_only(db: AsyncSession, user_id: int) -> list[str]:
    """The stock calendar of a built-in home is listed for events only once that home has a
    VTODO-only list (calendar/builtin.py makes "Tasks"): the store accepts tasks in it too, which
    would show "Calendar" beside "Tasks" as a task list. A stock calendar that already holds tasks
    stays a task list, so nothing disappears."""
    from mailr.calendar.builtin import CALENDAR_NAME, STOCK_NAME

    cols = list((await db.execute(select(CalCollection).join(Connector, Connector.id == CalCollection.connector_id)
                                  .where(Connector.user_id == user_id, Connector.builtin.is_(True),
                                         CalCollection.deleted_at.is_(None)))).scalars())
    todo_homes = {(c.connector_id, _parent(c.href)) for c in cols
                  if [x.upper() for x in c.components or []] == ["VTODO"]}
    if not todo_homes:
        return []
    stock = []
    for c in cols:
        comps = [x.upper() for x in c.components or []] or ["VEVENT", "VTODO", "VJOURNAL"]
        is_stock = c.href.rstrip("/").endswith("/default") or c.displayname == CALENDAR_NAME \
            or bool(STOCK_NAME.match(c.displayname or ""))
        if is_stock and "VEVENT" in comps and "VTODO" in comps and (c.connector_id, _parent(c.href)) in todo_homes:
            stock.append(c)
    if not stock:
        return []
    with_tasks = set((await db.execute(select(CalObject.collection_id).where(
        CalObject.collection_id.in_([c.id for c in stock]), CalObject.kind == "task",
        CalObject.deleted_at.is_(None)).distinct())).scalars())
    return [c.href for c in stock if c.id not in with_tasks]


async def kind_overrides(db: AsyncSession, user_id: int) -> dict[str, Any]:
    """Per collection URL: the user's kind overrides (`events`, `tasks`) and the mailr-side
    preferences of calendar/routes_collections.py (`color_local`, `default_reminders`)."""
    user = await db.get(User, user_id)
    s = (user.settings if user else None) or {}
    out = {k: dict(v) for k, v in (s.get("calendar_kinds") or {}).items()}
    for href in await builtin_events_only(db, user_id):  # the user's own choice wins
        out.setdefault(href, {}).setdefault("tasks", False)
    for href, prefs in (s.get("collection_prefs") or {}).items():
        out.setdefault(href, {}).update({k: v for k, v in prefs.items() if k in ("color_local", "default_reminders")})
    from mailr.calendar import groups

    for href, extra in (await groups.overrides_for(db, user_id)).items():  # group calendars: "shared"
        out.setdefault(href, {}).update(extra)
    from mailr.calendar import sharing

    for href, extra in (await sharing.overrides_for(db, user_id)).items():  # people's shares: "shared", "sharing"
        out.setdefault(href, {}).update(extra)
    return out


# ---------------- writing the mirror ----------------


# The fields of a row that come from its resource (ical.parse_resource fills every one).
MIRROR_FIELDS = ("kind", "uid", "summary", "description", "location", "status", "class_", "transp", "color", "url",
              "tzid", "end_tzid", "floating", "all_day", "start_at", "end_at", "start_date", "end_date", "due_at",
              "due_date", "completed_at", "percent", "priority", "sort_order", "categories", "parent_uid",
              "repeat_from", "rrule", "recurring", "has_overrides", "organizer", "organizer_name", "attendees",
              "attendee_count", "sequence", "dtstamp", "last_modified", "created", "alarms", "no_reminder")


def _apply_fields(obj: CalObject, f: dict[str, Any], me: set[str]) -> None:
    for k in MIRROR_FIELDS:
        if k in f:
            setattr(obj, k, f[k])
    obj.is_organizer = bool(obj.organizer) and obj.organizer.lower() in me
    mine = next((a for a in obj.attendees or [] if a["email"].lower() in me), None)
    obj.my_partstat = mine["partstat"] if mine else None
    obj.tags_text = " ".join(obj.categories or [])


async def _plan(db: AsyncSession, obj: CalObject) -> None:
    """The reminder planner (calendar/planner.py) re-plans the object, when it is installed."""
    try:
        from mailr.calendar import planner  # type: ignore[attr-defined]
    except ImportError:
        return
    fn = getattr(planner, "plan_object", None)
    if fn is not None:
        try:
            await fn(db, obj)
        except Exception:  # planning never blocks a write or a sync
            log.exception("reminder planning failed for object %s", obj.id)


def _mark_written(db: AsyncSession) -> None:
    """Note that this transaction has changed mirror rows (see ensure_synced)."""
    db.info["calendar_wrote_tx"] = db.sync_session.get_transaction()


def wrote_in_transaction(db: AsyncSession) -> bool:
    tx = db.info.get("calendar_wrote_tx")
    return tx is not None and tx is db.sync_session.get_transaction() and tx.is_active


async def upsert_object(db: AsyncSession, coll: CalCollection, href: str, etag: str | None, ics: str, *,
                        user_tz: tzinfo, me: set[str], force: bool = False,
                        skip_if_updated_after: datetime | None = None) -> CalObject | None:
    """Parse one resource and write it to the mirror (row, occurrences, reminder plan). Returns
    the row, or None when nothing changed (same ETag) or the resource holds no event or task."""
    from mailr.calendar.dav import norm_href

    href = norm_href(href)
    existing = (await db.execute(select(CalObject).where(
        CalObject.collection_id == coll.id, CalObject.href == href))).scalar_one_or_none()
    if existing is not None and not force and existing.deleted_at is None and etag and existing.etag == etag:
        return None
    if existing is not None and skip_if_updated_after is not None and existing.updated_at \
            and existing.updated_at > skip_if_updated_after:
        return None
    try:
        parsed = ical.parse_resource(ics, user_tz)
    except Exception as e:  # noqa: BLE001  unreadable data: keep the old row, if any
        log.warning("cannot parse %s: %s", href, e)
        return None
    if parsed.kind not in ("event", "task"):
        return None
    now = datetime.now(UTC)
    obj = existing
    if obj is None:
        obj = CalObject(user_id=coll.user_id, connector_id=coll.connector_id, collection_id=coll.id, href=href,
                        kind=parsed.kind)
        db.add(obj)
    _apply_fields(obj, parsed.fields, me)
    obj.etag, obj.ics, obj.deleted_at, obj.fetched_at, obj.updated_at = etag, ics, None, now, now
    obj.rev = text("nextval('cal_rev_seq')")
    _mark_written(db)
    await db.flush()
    await db.refresh(obj, ["rev"])
    await expand.materialize(db, obj, parsed.cal, user_tz, parsed.fields)
    await db.flush()
    await _plan(db, obj)
    return obj


UPSERT_CHUNK = 500
# The columns upsert_many writes to a row it updates: what upsert_object sets on one.
_WRITTEN = ("id", *MIRROR_FIELDS, "is_organizer", "my_partstat", "tags_text", "etag", "ics", "deleted_at",
            "fetched_at", "updated_at", "rev", "range_start", "range_end", "expanded_from", "expanded_until")


def _prepare_many(items: list[tuple[str, str | None, str]], user_tz: tzinfo) -> list[tuple[Any, ...]]:
    """In a thread: parse each resource and, for a single event, its one occurrence.
    [(href, etag, ics, parsed, insts)]; insts is None for a series or a task (upsert_object
    expands those, under its deadline). Unreadable resources and other kinds are left out."""
    out = []
    lo, hi = expand.expansion_window(False)
    for href, etag, ics in items:
        try:
            parsed = ical.parse_resource(ics, user_tz)
        except Exception as e:  # noqa: BLE001  unreadable data: keep the old row, if any
            log.warning("cannot parse %s: %s", href, e)
            continue
        if parsed.kind not in ("event", "task"):
            continue
        insts = None
        if parsed.kind == "event" and not parsed.fields.get("recurring"):
            insts = expand.instances(parsed.cal, lo, hi, user_tz)[0]
        out.append((href, etag, ics, parsed, insts))
    return out


async def upsert_many(db: AsyncSession, coll: CalCollection, items: list[tuple[str, str | None, str]], *,
                      user_tz: tzinfo, me: set[str]) -> list[int]:
    """`upsert_object` for many resources of one collection (a calendar link's file): the same rows,
    occurrences and reminder plan, written in bulk. Resources are parsed in a thread, UPSERT_CHUNK
    at a time; each chunk's rows go in with one flush and one occurrence insert, in place of a
    dozen statements per resource. A series goes through `upsert_object`, which expands it under
    its deadline. Returns the ids of the rows written; unchanged resources (same ETag) are skipped."""
    from mailr.calendar.dav import norm_href

    items = [(norm_href(h), e, ics) for h, e, ics in items]
    known: dict[str, tuple[int, str | None, datetime | None]] = {}
    hrefs = [h for h, _, _ in items]
    for k in range(0, len(hrefs), 5000):
        for oid, href, etag, deleted_at in (await db.execute(select(
                CalObject.id, CalObject.href, CalObject.etag, CalObject.deleted_at).where(
                CalObject.collection_id == coll.id, CalObject.href.in_(hrefs[k:k + 5000])))).all():
            known[href] = (oid, etag, deleted_at)
    todo = [(h, e, ics) for h, e, ics in items
            if not (h in known and known[h][2] is None and e and known[h][1] == e)]
    lo, hi = expand.expansion_window(False)
    written: list[int] = []
    plan_ctx = None
    for k in range(0, len(todo), UPSERT_CHUNK):
        prepared = await asyncio.to_thread(_prepare_many, todo[k:k + UPSERT_CHUNK], user_tz)
        single = [p for p in prepared if p[4] is not None]
        # The revs come from the sequence as upsert_object's do, fetched in one statement.
        revs = (await db.execute(text("SELECT nextval('cal_rev_seq') FROM generate_series(1, :n)"),
                                 {"n": len(single)})).scalars().all() if single else []
        now = datetime.now(UTC)
        objs, updates, old_ids = [], [], []
        for (href, etag, ics, parsed, _), rev in zip(single, revs, strict=True):
            # Every mirror field comes from the parse (parse_resource fills them all), so a row
            # written here equals one upsert_object writes, new or not.
            obj = CalObject(user_id=coll.user_id, connector_id=coll.connector_id, collection_id=coll.id,
                            href=href, kind=parsed.kind)
            _apply_fields(obj, parsed.fields, me)
            obj.etag, obj.ics, obj.deleted_at, obj.fetched_at, obj.updated_at = etag, ics, None, now, now
            obj.rev = rev
            expand.set_range(obj, [], lo, hi, False, parsed.fields)
            if href in known:
                obj.id = known[href][0]
                old_ids.append(obj.id)
                updates.append({k: getattr(obj, k) for k in _WRITTEN})
            else:
                db.add(obj)
            objs.append(obj)
        for href, etag, ics, _, insts in prepared:
            if insts is None:  # a series or a task: upsert_object expands it under its deadline
                one = await upsert_object(db, coll, href, etag, ics, user_tz=user_tz, me=me)
                if one is not None:
                    written.append(one.id)
        if not objs:
            continue
        _mark_written(db)
        await db.flush()
        if updates:
            # One executemany (ORM bulk UPDATE by primary key); the ORM's unit of work sends one
            # UPDATE per row with this driver.
            await db.execute(update(CalObject), updates)
            await db.execute(delete(CalOccurrence).where(CalOccurrence.object_id.in_(old_ids)))
        rows = [r for obj, p in zip(objs, single, strict=True) for r in expand.occurrence_rows(obj, p[4], p[3].fields)]
        for i in range(0, len(rows), 1000):
            await db.execute(insert(CalOccurrence), rows[i:i + 1000])
        if coll.notify:
            from mailr.calendar import planner

            plan_ctx = plan_ctx or await planner._context(db, coll.user_id)
            ids = [o.id for o in objs]
            for obj in (await db.execute(select(CalObject).where(CalObject.id.in_(ids))
                                         .execution_options(populate_existing=True))).scalars():
                try:
                    await planner.plan_object(db, obj, ctx=plan_ctx)
                except Exception:  # planning never blocks a write or a sync
                    log.exception("reminder planning failed for object %s", obj.id)
        elif old_ids:
            # What plan_object does in a collection that does not remind: the unfired planned rows go.
            await db.execute(delete(ReminderSchedule).where(
                ReminderSchedule.user_id == coll.user_id, ReminderSchedule.object_id.in_(old_ids),
                ReminderSchedule.fired_at.is_(None), ReminderSchedule.source.in_(("valarm", "default"))))
        written.extend(o.id for o in objs)
    return written


async def soft_delete_many(db: AsyncSession, ids: list[int]) -> None:
    """`soft_delete` for many rows (by id), a few statements per 5,000."""
    now = datetime.now(UTC)
    for k in range(0, len(ids), 5000):
        part = ids[k:k + 5000]
        _mark_written(db)
        await db.execute(text(
            "UPDATE cal_objects SET deleted_at = :now, updated_at = :now, rev = nextval('cal_rev_seq') "
            "WHERE id = ANY(:ids)"), {"now": now, "ids": part})
        await db.execute(delete(CalOccurrence).where(CalOccurrence.object_id.in_(part)))
        await db.execute(delete(ReminderSchedule).where(ReminderSchedule.object_id.in_(part),
                                                        ReminderSchedule.fired_at.is_(None)))


async def soft_delete(db: AsyncSession, obj: CalObject) -> None:
    """A tombstone (kept TOMBSTONE_DAYS for /calendar/delta); occurrences and unfired
    reminders go."""
    obj.deleted_at = obj.updated_at = datetime.now(UTC)
    obj.rev = text("nextval('cal_rev_seq')")
    _mark_written(db)
    await db.execute(delete(CalOccurrence).where(CalOccurrence.object_id == obj.id))
    await db.execute(delete(ReminderSchedule).where(ReminderSchedule.object_id == obj.id,
                                                    ReminderSchedule.fired_at.is_(None)))
    await db.flush()


async def soft_delete_collection(db: AsyncSession, coll: CalCollection) -> list[tuple[str, int]]:
    """The collection and everything in it become tombstones; returns [(kind, id)] removed."""
    objs = (await db.execute(select(CalObject).where(CalObject.collection_id == coll.id,
                                                     CalObject.deleted_at.is_(None)))).scalars().all()
    out = []
    for o in objs:
        await soft_delete(db, o)
        out.append((o.kind, o.id))
    coll.deleted_at = coll.updated_at = datetime.now(UTC)
    coll.rev = text("nextval('cal_rev_seq')")
    await db.flush()
    return out


async def purge_tombstones(db: AsyncSession, days: int = TOMBSTONE_DAYS) -> None:
    """Drop old tombstones. Each user keeps the highest purged rev (`calendar_purged_rev`), so a
    delta cursor older than it gets `reset`."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = (await db.execute(text(
        "SELECT user_id, max(rev) FROM (SELECT user_id, rev FROM cal_objects WHERE deleted_at < :c "
        "UNION ALL SELECT user_id, rev FROM cal_collections WHERE deleted_at < :c) t GROUP BY user_id"),
        {"c": cutoff})).all()
    from mailr.core import usersettings

    for uid, rev in rows:
        if await db.get(User, uid) is not None:
            purged = int((await usersettings.current(db, uid)).get("calendar_purged_rev") or 0)
            await usersettings.patch(db, uid, {"calendar_purged_rev": max(purged, int(rev))})
    await db.execute(delete(CalObject).where(CalObject.deleted_at < cutoff))
    await db.execute(delete(CalCollection).where(CalCollection.deleted_at < cutoff))


# ---------------- lookups ----------------


async def find_by_uid(db: AsyncSession, user_id: int, uid: str, kind: str | None = None, p: Any = None
                      ) -> CalObject | None:
    """The live mirror row for a UID among the connectors p may see (events before tasks)."""
    cids = await visible_connector_ids(db, user_id, p)
    if not cids:
        return None
    q = select(CalObject).join(CalCollection, CalCollection.id == CalObject.collection_id).where(
        CalObject.user_id == user_id, CalObject.uid == uid, CalObject.deleted_at.is_(None),
        CalCollection.deleted_at.is_(None), CalObject.connector_id.in_(cids))
    if kind:
        q = q.where(CalObject.kind == kind)
    return (await db.execute(q.order_by(CalObject.kind, CalObject.id).limit(1))).scalar_one_or_none()


async def collection_by_url(db: AsyncSession, user_id: int, url: str, p: Any = None) -> CalCollection | None:
    cids = await visible_connector_ids(db, user_id, p)
    if not cids or not url:
        return None
    rows = (await db.execute(select(CalCollection).where(
        CalCollection.user_id == user_id, CalCollection.connector_id.in_(cids),
        CalCollection.deleted_at.is_(None)))).scalars().all()
    from mailr.calendar.dav import norm_href

    norm = norm_href(url).rstrip("/")
    return next((c for c in rows if norm_href(c.href).rstrip("/") == norm), None)


# ---------------- output shapes ----------------


def _when_out(at: datetime | None, d: date | None, all_day: bool) -> str | None:
    if all_day and d is not None:
        return d.isoformat()
    return ical.out_when(at)


def _zone_out(obj: CalObject, user_tz: tzinfo) -> str | None:
    if obj.all_day and obj.kind == "event":
        return None
    if obj.floating:
        return tzmod.zone_name(user_tz)
    return tzmod.iana(obj.tzid)


def _shareable(coll: CalCollection, connector: Connector | None, pref: dict[str, Any], comps: list[str]) -> bool:
    """Offer "Share…": an own calendar of the built-in account, or one shared with the share level."""
    shared = pref.get("shared")
    if shared:
        return shared.get("kind") == "user" and bool(shared.get("can_share"))
    return bool(connector and connector.builtin) and "VEVENT" in comps


def collection_out(coll: CalCollection, connector: Connector | None, overrides: dict[str, Any]) -> dict[str, Any]:
    ev, tk = kinds_of(coll, overrides)
    comps = [c.upper() for c in coll.components or []] or ["VEVENT", "VTODO", "VJOURNAL"]
    pref = overrides.get(coll.href) or {}
    return {"id": coll.href, "url": coll.href, "calendar_id": coll.id, "connector_id": coll.connector_id,
            "name": calendar_name(coll.displayname, coll.href), "description": coll.description or "",
            "color": pref.get("color_local") or coll.color, "color_local": bool(pref.get("color_local")),
            "default_reminders": pref.get("default_reminders"),
            "events": ev, "tasks": tk, "components": comps, "read_only": coll.read_only,
            "tz": tzmod.iana(coll.calendar_tz), "hidden": coll.hidden, "busy": coll.busy, "notify": coll.notify,
            "sort_order": coll.sort_order, "local_only": bool(connector and connector.data_class == "local_only"),
            "shared": pref.get("shared"),  # ACT calendar.group_shared @api (a group calendar, calendar/groups.py)
            # ACT calendar.user_shared @api: a calendar someone shares with this user ("shared", kind
            # "user"), or this user's own calendar shared with others ("sharing"; calendar/sharing.py)
            "sharing": pref.get("sharing"),
            "shareable": _shareable(coll, connector, pref, comps),
            # ACT calendar.subscribe @api: a calendar link the user subscribed to (calendar/webcal.py), read-only.
            "subscription": ({"url": (connector.config or {}).get("url"),
                              "refresh_hours": (connector.config or {}).get("refresh_hours")}
                             if connector is not None and connector.kind == "webcal" else None),
            # ACT calendar.phone_list @api: busy times sent from a calendar on the user's phone
            # (calendar/phonecal.py): read-only, with the device and when it last sent them.
            "phone": phonecal.info(coll, connector) if phonecal.is_phone(coll) else None,
            "sync": {"state": coll.sync_status if coll.last_full_sync_at else "initial",
                     "last_sync_at": coll.last_sync_at.isoformat() if coll.last_sync_at else None,
                     "error": coll.sync_error or None}}


def event_out(obj: CalObject, coll: CalCollection | None, user_tz: tzinfo, *, local_only: bool = False,
              full_description: bool = False) -> dict[str, Any]:
    """The master (or a single event) in the API shape."""
    start = _when_out(obj.start_at, obj.start_date, obj.all_day)
    end = _when_out(obj.end_at, obj.end_date, obj.all_day)
    occ = ical.canonical_occurrence(obj.start_date if obj.all_day else obj.start_at)
    desc = obj.description or ""
    return {
        "id": obj.id, "uid": obj.uid, "occurrence": occ, "recurrence_id": occ if obj.recurring else None,
        "summary": obj.summary or "", "location": obj.location or "",
        "description": desc if full_description else desc[:LIST_DESCRIPTION_MAX],
        "start": start, "end": end, "all_day": obj.all_day, "tz": _zone_out(obj, user_tz), "end_tz": tzmod.iana(obj.end_tzid),
        "status": obj.status or "", "transparent": (obj.transp or "").upper() == "TRANSPARENT",
        "class": obj.class_ or "", "color": obj.color, "calendar_color": coll.color if coll else None,
        "categories": list(obj.categories or []), "url": obj.url, "sequence": obj.sequence or 0, "etag": obj.etag,
        "recurring": obj.recurring, "is_override": False, "rrule": obj.rrule,
        "organizer": obj.organizer, "organizer_name": obj.organizer_name or "",
        "attendees": obj.attendee_count or 0, "attendee_list": list(obj.attendees or []),
        "reminders": list(obj.alarms or []),
        # Marked "no reminders" (ical.NO_REMINDER): no fallback default either, here or on the phone.
        "no_reminders": bool(obj.no_reminder),
        "calendar": coll.href if coll else None,
        "calendar_id": obj.collection_id, "connector_id": obj.connector_id, "href": obj.href,
        "local_only": local_only,
        # A block from a phone calendar (calendar/phonecal.py): read-only; times only unless
        # phone_details (the calendar sends titles and places, and this block has one).
        "phone_busy": phonecal.is_phone(coll),
        "phone_details": phonecal.is_phone(coll) and phonecal.has_details(obj.uid),
    }


def override_fields(obj: CalObject, user_tz: tzinfo, cache: dict[int, Any]) -> dict[str, dict[str, Any]]:
    """{canonical recurrence id: component fields} of the object's overrides (parsed once)."""
    if obj.id in cache:
        return cache[obj.id]
    out: dict[str, dict[str, Any]] = {}
    try:
        cal = Calendar.from_ical(obj.ics)
        for c in cal.walk("VEVENT"):
            if c.get("recurrence-id") is not None:
                f = ical.component_fields(c, cal, user_tz)
                out[f["recurrence_id"]] = f
    except Exception:  # noqa: BLE001, S110
        pass
    cache[obj.id] = out
    return out


def instance_out(obj: CalObject, coll: CalCollection | None, user_tz: tzinfo, *, occurrence: str,
                 start_at: datetime, end_at: datetime, all_day: bool, is_override: bool,
                 ov: dict[str, Any] | None = None, local_only: bool = False) -> dict[str, Any]:
    """One occurrence in the API shape (master fields, with an override's own values)."""
    e = event_out(obj, coll, user_tz, local_only=local_only)
    e["occurrence"] = occurrence
    e["recurrence_id"] = occurrence if obj.recurring else None
    e["is_override"] = is_override
    if all_day:
        if ov is not None and ov.get("start_date"):
            s, en = ov["start_date"], ov.get("end_date") or ov["start_date"] + timedelta(days=1)
        else:
            s = date.fromisoformat(occurrence) if len(occurrence) == 10 else start_at.astimezone(user_tz).date()
            span = (obj.end_date - obj.start_date) if obj.end_date and obj.start_date else timedelta(days=1)
            en = s + span
        e["start"], e["end"] = s.isoformat(), en.isoformat()
    else:
        e["start"], e["end"] = ical.out_when(start_at), ical.out_when(end_at)
    e["all_day"] = all_day
    if ov is not None:
        for k in ("summary", "location", "status", "url", "color"):
            if ov.get(k) is not None:
                e[k] = ov[k]
        e["description"] = (ov.get("description") or "")[:LIST_DESCRIPTION_MAX]
        e["transparent"] = (ov.get("transp") or "").upper() == "TRANSPARENT"
        e["class"] = ov.get("class_") or ""
        e["categories"] = ov.get("categories") or []
        e["sequence"] = ov.get("sequence") or 0
        e["attendee_list"] = ov.get("attendees") or []
        e["attendees"] = len(e["attendee_list"])
        e["reminders"] = ov.get("alarms") or []
        e["no_reminders"] = bool(ov.get("no_reminder"))
        if ov.get("organizer"):
            e["organizer"], e["organizer_name"] = ov["organizer"], ov.get("organizer_name") or ""
        if tzmod.iana(ov.get("tzid")):
            e["tz"] = tzmod.iana(ov["tzid"])
    return e


def task_out(obj: CalObject, coll: CalCollection | None, user_tz: tzinfo, *, subtasks: tuple[int, int] = (0, 0),
             local_only: bool = False) -> dict[str, Any]:
    status = (obj.status or "NEEDS-ACTION").upper()
    completed = status == "COMPLETED" or obj.completed_at is not None
    return {
        "id": obj.id, "uid": obj.uid, "summary": obj.summary or "",
        "due": _when_out(obj.due_at, obj.due_date, obj.due_date is not None), "due_all_day": obj.due_date is not None,
        "start": _when_out(obj.start_at, obj.start_date, obj.all_day), "start_all_day": obj.all_day,
        "tz": tzmod.zone_name(user_tz) if obj.floating else tzmod.iana(obj.tzid),
        "status": status.lower(), "completed": completed, "completed_at": ical.out_when(obj.completed_at),
        "notes": (obj.description or "")[:LIST_DESCRIPTION_MAX], "priority": obj.priority or 0,
        "percent": obj.percent, "recurring": obj.recurring, "rrule": obj.rrule,
        "repeat_from": obj.repeat_from or "due", "categories": list(obj.categories or []),
        "sort_order": obj.sort_order, "parent": obj.parent_uid,
        "subtask_count": subtasks[0], "open_subtask_count": subtasks[1],
        "reminders": list(obj.alarms or []), "list": coll.href if coll else None, "list_id": obj.collection_id,
        "connector_id": obj.connector_id, "href": obj.href, "etag": obj.etag,
        "created": ical.out_when(obj.created or obj.created_at), "last_modified": ical.out_when(obj.last_modified),
        "local_only": local_only,
    }


async def object_out(db: AsyncSession, obj: CalObject, user_tz: tzinfo | None = None, *,
                     full_description: bool = False) -> dict[str, Any]:
    """The API shape of one mirror row (event master or task)."""
    coll = await db.get(CalCollection, obj.collection_id)
    conn = await db.get(Connector, obj.connector_id)
    user_tz = user_tz or await tzmod.user_zone(db, obj.user_id)
    lo = bool(conn and conn.data_class == "local_only")
    from mailr.calendar import live_items

    if obj.kind == "task":
        counts = await subtask_counts(db, obj.user_id, [obj.uid])
        out = task_out(obj, coll, user_tz, subtasks=counts.get(obj.uid, (0, 0)), local_only=lo)
    else:
        out = event_out(obj, coll, user_tz, local_only=lo, full_description=full_description)
        out["override_count"] = override_count(obj)
    # The live version (core/live.py), for an editor's base_version.
    out["version"] = await live_items.version_of(db, obj)
    return out


def override_count(obj: CalObject) -> int:
    """The series' changed occurrences: overrides (RECURRENCE-ID components) that are not
    cancelled. The "All events" choice of the series question says how many keep their own time."""
    if not obj.recurring or not obj.has_overrides:
        return 0
    try:
        cal = Calendar.from_ical(obj.ics)
    except Exception:  # noqa: BLE001
        return 0
    return sum(1 for c in cal.walk("VEVENT") if c.get("recurrence-id") is not None
               and str(c.get("status", "")).upper() != "CANCELLED")


async def subtask_counts(db: AsyncSession, user_id: int, uids: list[str]) -> dict[str, tuple[int, int]]:
    if not uids:
        return {}
    rows = (await db.execute(select(
        CalObject.parent_uid, func.count(),
        func.count().filter(and_(CalObject.status != "COMPLETED", CalObject.completed_at.is_(None)))).where(
        CalObject.user_id == user_id, CalObject.kind == "task", CalObject.deleted_at.is_(None),
        CalObject.parent_uid.in_(uids)).group_by(CalObject.parent_uid))).all()
    return {r[0]: (int(r[1]), int(r[2])) for r in rows}


# ---------------- reads ----------------


async def _local_only_connectors(db: AsyncSession, ids: list[int]) -> set[int]:
    if not ids:
        return set()
    return set((await db.execute(select(Connector.id).where(Connector.id.in_(ids),
                                                            Connector.data_class == "local_only"))).scalars())


def _wanted(urls: list[str]) -> set[str]:
    from mailr.calendar.dav import norm_href

    return {norm_href(u).rstrip("/") if "://" in u else u for u in urls}


async def event_collections(db: AsyncSession, user_id: int, p: Any, calendars: list[str] | None
                            ) -> list[CalCollection]:
    """Collections that hold events, among those p may see. Hidden ones only when named."""
    ov = await kind_overrides(db, user_id)
    cols = [c for c in await collections_of(db, await visible_connector_ids(db, user_id, p)) if kinds_of(c, ov)[0]]
    if calendars:
        want = _wanted(calendars)
        return [c for c in cols if c.href.rstrip("/") in want or str(c.id) in want]
    return [c for c in cols if not c.hidden]


async def task_collections(db: AsyncSession, user_id: int, p: Any, lists: list[str] | None) -> list[CalCollection]:
    ov = await kind_overrides(db, user_id)
    cols = [c for c in await collections_of(db, await visible_connector_ids(db, user_id, p)) if kinds_of(c, ov)[1]]
    if lists:
        want = _wanted(lists)
        return [c for c in cols if c.href.rstrip("/") in want or str(c.id) in want]
    return [c for c in cols if not c.hidden]


async def agenda(db: AsyncSession, user_id: int, start: datetime, end: datetime, *,
                 calendars: list[str] | None = None, p: Any = None) -> list[dict[str, Any]]:
    """Occurrences in [start, end): indexed rows inside the materialized window, expanded on
    read for series whose window does not cover the range."""
    cols = await event_collections(db, user_id, p, calendars)
    if not cols:
        return []
    by_id = {c.id: c for c in cols}
    user_tz = await tzmod.user_zone(db, user_id)
    local = await _local_only_connectors(db, list({c.connector_id for c in cols}))
    # Series not covered by their materialized window for this range: expanded here.
    uncovered = (await db.execute(select(CalObject).where(
        CalObject.user_id == user_id, CalObject.kind == "event", CalObject.deleted_at.is_(None),
        CalObject.recurring.is_(True), CalObject.collection_id.in_(list(by_id)),
        or_(CalObject.expanded_from.is_(None), CalObject.expanded_from > start,
            CalObject.expanded_until.is_(None), CalObject.expanded_until < end),
        or_(CalObject.range_end.is_(None), CalObject.range_end >= start),
        or_(CalObject.range_start.is_(None), CalObject.range_start < end)))).scalars().all()
    skip = {o.id for o in uncovered}
    rows = (await db.execute(
        select(CalOccurrence, CalObject).join(CalObject, CalObject.id == CalOccurrence.object_id).where(
            CalOccurrence.user_id == user_id, CalOccurrence.collection_id.in_(list(by_id)),
            CalObject.deleted_at.is_(None),
            text("tstzrange(cal_occurrences.start_at, cal_occurrences.end_at, '[]') && tstzrange(:a, :b, '[)')"))
        .params(a=start, b=end))).all()
    out: list[dict[str, Any]] = []
    cache: dict[int, Any] = {}
    for occ, obj in rows:
        if obj.id in skip:
            continue
        if not (start <= occ.start_at < end if occ.start_at == occ.end_at else occ.start_at < end and occ.end_at > start):
            continue
        ov = override_fields(obj, user_tz, cache).get(occ.occurrence) if occ.is_override else None
        out.append(instance_out(obj, by_id.get(obj.collection_id), user_tz, occurrence=occ.occurrence,
                                start_at=occ.start_at, end_at=occ.end_at, all_day=occ.all_day,
                                is_override=occ.is_override, ov=ov, local_only=obj.connector_id in local))
    # Parsing and expanding run off the event loop, all objects in one hop.
    expanded = await asyncio.to_thread(expand.parse_and_expand_many, [o.ics for o in uncovered], start, end,
                                       user_tz) if uncovered else []
    for obj, (cal, insts) in zip(uncovered, expanded, strict=True):
        if cal is None:
            continue
        for i in insts:
            ov = ical.component_fields(i.comp, cal, user_tz) if i.is_override else None
            out.append(instance_out(obj, by_id.get(obj.collection_id), user_tz, occurrence=i.occurrence,
                                    start_at=i.start_at, end_at=i.end_at, all_day=i.all_day,
                                    is_override=i.is_override, ov=ov, local_only=obj.connector_id in local))
    out.sort(key=lambda e: (e["start"] or "", e["summary"]))
    return out


async def tasks(db: AsyncSession, user_id: int, status: str = "open", *, lists: list[str] | None = None,
                q: str = "", p: Any = None) -> list[dict[str, Any]]:
    cols = await task_collections(db, user_id, p, lists)
    if not cols:
        return []
    by_id = {c.id: c for c in cols}
    # task_out reads the columns, never the iCalendar text: a list of every open task loaded it whole.
    query = select(CalObject).options(defer(CalObject.ics)).where(
        CalObject.user_id == user_id, CalObject.kind == "task", CalObject.deleted_at.is_(None),
        CalObject.collection_id.in_(list(by_id)))
    done = or_(CalObject.status == "COMPLETED", CalObject.completed_at.is_not(None))
    if status == "open":
        query = query.where(~done)
    elif status == "completed":
        query = query.where(done)
    needle = (q or "").strip()
    if needle:
        like = f"%{needle.replace('%', '').replace('_', '')}%"
        query = query.where(or_(CalObject.summary.ilike(like), CalObject.description.ilike(like)))
    rows = (await db.execute(query)).scalars().all()
    user_tz = await tzmod.user_zone(db, user_id)
    counts = await subtask_counts(db, user_id, [r.uid for r in rows])
    local = await _local_only_connectors(db, list({c.connector_id for c in cols}))
    out = [task_out(r, by_id.get(r.collection_id), user_tz, subtasks=counts.get(r.uid, (0, 0)),
                    local_only=r.connector_id in local) for r in rows]
    out.sort(key=lambda t: (t["completed"], t["due"] or "9999", t["summary"]))
    return out


async def sync_state(db: AsyncSession, user_id: int, p: Any = None) -> dict[str, Any]:
    """{state, last_sync_at} across the collections p may see (the `sync` field of reads)."""
    cols = await collections_of(db, await visible_connector_ids(db, user_id, p))
    if not cols:
        return {"state": "idle", "last_sync_at": None}
    if any(c.last_full_sync_at is None for c in cols):
        state = "initial"
    elif any(c.sync_status == "error" for c in cols):
        state = "error"
    elif any(c.sync_status == "backoff" for c in cols):
        state = "backoff"
    elif any(c.sync_status == "syncing" for c in cols):
        state = "syncing"
    else:
        state = "idle"
    last = min((c.last_sync_at for c in cols if c.last_sync_at), default=None)
    return {"state": state, "last_sync_at": last.isoformat() if last else None}


async def ensure_synced(db: AsyncSession, user_id: int, p: Any = None, *, refresh: bool | None = None,
                        force: bool = False, wait: bool = True) -> None:
    """Make the mirror fit to read: a first sync inline for connectors that never finished one,
    and an incremental sync inline when the last check is older than read_refresh_seconds()
    (`force` always syncs). A sync that runs past its budget goes on in the background.

    `wait=False` (GET /today, which reads several parts and must answer at once): the incremental
    syncs start in the background and the read uses the mirror as it is. Only a first sync is
    still waited for, since without it there is nothing to read. The sync's own notifications
    tell open clients when the background run changed something.

    Once this transaction has written mirror rows (a bulk action, a move with its subtasks), no
    inline sync runs: the sync's own session would wait on this transaction's row locks until
    its budget ran out (3 s per item), and this transaction already holds the server's copies of
    what it wrote."""
    from mailr.calendar import sync

    if wrote_in_transaction(db):
        return
    conns = await visible_connectors(db, user_id, p)
    if not conns:
        return
    max_age = read_refresh_seconds()
    now = datetime.now(UTC)
    jobs = []
    for c in conns:
        st = (c.state or {}).get("caldav") or {}
        if st.get("auth_failed") and not force:
            continue
        if not st.get("initial_done"):
            jobs.append((c.id, INITIAL_BUDGET_S))
            continue
        if refresh is False:
            continue
        checked = st.get("checked_at")
        try:
            age = (now - datetime.fromisoformat(checked)).total_seconds() if checked else 1e9
        except ValueError:
            age = 1e9
        if force or age >= max_age:
            if not wait and not force:
                sync.refresh_in_background(c.id)
                continue
            jobs.append((c.id, REFRESH_BUDGET_S))
    for cid, budget in jobs:
        await sync.run_inline(cid, budget=budget)


async def delta(db: AsyncSession, user_id: int, p: Any, cursor: int, limit: int, window_from: datetime,
                window_to: datetime) -> dict[str, Any]:
    """/calendar/delta (section 5)."""
    cids = await visible_connector_ids(db, user_id, p)
    user_tz = await tzmod.user_zone(db, user_id)
    reset = False
    if cursor > 0:
        # Tombstones older than TOMBSTONE_DAYS are purged; a cursor from before the last purge
        # may have missed some of them.
        user = await db.get(User, user_id)
        purged = int(((user.settings or {}) if user else {}).get("calendar_purged_rev") or 0)
        reset = cursor < purged
    if reset or not cids:
        return {"cursor": 0 if reset else cursor, "more": False, "reset": reset, "collections": [],
                "deleted_collections": [], "objects": [], "deleted": [], "instances": {},
                "window": {"from": window_from.date().isoformat(), "to": window_to.date().isoformat()},
                "server_time": datetime.now(UTC).isoformat()}
    ov = await kind_overrides(db, user_id)
    conns = {c.id: c for c in await visible_connectors(db, user_id, p, subscriptions=True)}
    cols = (await db.execute(select(CalCollection).where(
        CalCollection.user_id == user_id, CalCollection.connector_id.in_(cids), CalCollection.rev > cursor)
        .order_by(CalCollection.rev).limit(limit + 1))).scalars().all()
    objs = (await db.execute(select(CalObject).where(
        CalObject.user_id == user_id, CalObject.connector_id.in_(cids), CalObject.rev > cursor,
        CalObject.kind.in_(["event", "task"])).order_by(CalObject.rev).limit(limit + 1))).scalars().all()
    # One page: the lowest `limit` revisions across both tables.
    merged = sorted([(c.rev, "c", c) for c in cols] + [(o.rev, "o", o) for o in objs], key=lambda x: x[0])
    more = len(merged) > limit
    page = merged[:limit]
    new_cursor = page[-1][0] if page else cursor
    if not page:
        new_cursor = max(cursor, 0)
    all_cols = {c.id: c for c in await collections_of(db, cids, include_deleted=True)}
    out: dict[str, Any] = {"cursor": new_cursor, "more": more, "reset": False, "collections": [],
                           "deleted_collections": [], "objects": [], "deleted": [], "instances": {},
                           "window": {"from": window_from.date().isoformat(), "to": window_to.date().isoformat()},
                           "server_time": datetime.now(UTC).isoformat()}
    live_objs = [x[2] for x in page if x[1] == "o" and x[2].deleted_at is None]
    counts = await subtask_counts(db, user_id, [o.uid for o in live_objs if o.kind == "task"])
    for _, t, row in page:
        if t == "c":
            if row.deleted_at is not None:
                out["deleted_collections"].append({"calendar_id": row.id, "url": row.href})
            else:
                out["collections"].append(collection_out(row, conns.get(row.connector_id), ov))
            continue
        if row.deleted_at is not None or (all_cols.get(row.collection_id) and all_cols[row.collection_id].deleted_at):
            out["deleted"].append({"id": row.id, "uid": row.uid, "kind": row.kind})
            continue
        coll = all_cols.get(row.collection_id)
        lo = bool(conns.get(row.connector_id) and conns[row.connector_id].data_class == "local_only")
        if row.kind == "task":
            from mailr.calendar.todo import with_alarms

            # As the task routes: relative `reminders`, absolute ones in `reminders_at`.
            t = with_alarms(task_out(row, coll, user_tz, subtasks=counts.get(row.uid, (0, 0)), local_only=lo),
                            row.ics or "")
            out["objects"].append({"kind": "task", **t})
            continue
        out["objects"].append({"kind": "event", **event_out(row, coll, user_tz, local_only=lo)})
        insts = await _instances_in(db, row, window_from, window_to, user_tz)
        out["instances"].setdefault(row.uid, []).extend(insts)
    return out


async def _instances_in(db: AsyncSession, obj: CalObject, start: datetime, end: datetime, user_tz: tzinfo
                        ) -> list[dict[str, Any]]:
    covered = not obj.recurring or (obj.expanded_from and obj.expanded_until
                                    and obj.expanded_from <= start and obj.expanded_until >= end)
    out = []
    if covered:
        rows = (await db.execute(select(CalOccurrence).where(
            CalOccurrence.object_id == obj.id,
            text("tstzrange(start_at, end_at, '[]') && tstzrange(:a, :b, '[)')")).params(a=start, b=end)
            .order_by(CalOccurrence.start_at))).scalars().all()
        for r in rows:
            out.append({"occurrence": r.occurrence, "start": _inst_when(r.start_at, r.occurrence, r.all_day, user_tz),
                        "end": _inst_when(r.end_at, None, r.all_day, user_tz), "all_day": r.all_day,
                        "is_override": r.is_override, "cancelled": r.cancelled, "summary": r.summary,
                        "location": r.location, "status": r.status})
        return out
    # Off the event loop.
    [(cal, insts)] = await asyncio.to_thread(expand.parse_and_expand_many, [obj.ics], start, end, user_tz)
    if cal is None:
        return out
    for i in insts:
        summary = location = status = None
        if i.is_override:
            s, loc, st = ical.text_of(i.comp, "summary"), ical.text_of(i.comp, "location"), ical.text_of(i.comp, "status").upper()
            summary = s if s != obj.summary else None
            location = loc if loc != obj.location else None
            status = st if st != obj.status else None
        out.append({"occurrence": i.occurrence, "start": ical.out_when(i.start), "end": ical.out_when(i.end),
                    "all_day": i.all_day, "is_override": i.is_override, "cancelled": i.cancelled,
                    "summary": summary, "location": location, "status": status})
    return out


def _inst_when(at: datetime, occurrence: str | None, all_day: bool, user_tz: tzinfo) -> str:
    if all_day:
        if occurrence and len(occurrence) == 10:
            return occurrence
        return at.astimezone(user_tz).date().isoformat()
    return ical.out_when(at) or ""


async def mark_collection_rev(db: AsyncSession, coll: CalCollection) -> None:
    coll.rev = text("nextval('cal_rev_seq')")
    coll.updated_at = datetime.now(UTC)
    await db.flush()

