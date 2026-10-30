"""Write-through to CalDAV (section 3).

Every change is a read-modify-write on the mirror's copy: take the per-UID lock, change the
iCalendar object, PUT it with If-Match, then update the mirror from the returned ETag and record
the change, all in the caller's transaction (the caller commits). On 412 the fresh copy is
fetched: a client that sent a version gets a 409 Conflict with the server copy; without a
version the change is applied again once.

Helpers other modules build on:
- `rmw(db, user_id, p, obj, mutate, client_etag=None)`: the read-modify-write itself;
  `mutate(cal)` changes the icalendar.Calendar in place and may return a value.
- `create(db, user_id, coll, cal, uid)`, `move(db, user_id, p, obj, target, mutate=None)`,
  `delete(db, user_id, p, obj, client_etag=None)`, `refresh(db, obj)`.
- The operations behind the existing routes: `create_event`, `update_event`, `delete_event`,
  `create_task`, `update_task`, `delete_task`, `set_partstat`.
"""

from __future__ import annotations

import copy
import hashlib
import re
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from typing import Any

from icalendar import Calendar
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import dav, ical, mirror
from mailr.calendar import tz as tzmod
from mailr.calendar.sync import dav_client, uid_lock
from mailr.core.events import record_change
from mailr.models import CalCollection, CalObject, Connector, User


class CalendarNotConnected(Exception):
    pass


class Conflict(Exception):
    """409: the object changed on the server (`error="conflict"`) or the UID exists
    (`error="exists"`). `server` is the current copy in the API shape."""

    def __init__(self, kind: str, server: dict[str, Any] | None, error: str = "conflict", detail: str = ""):
        super().__init__(detail or error)
        self.kind, self.server, self.error = kind, server, error
        self.detail = detail or (f"The {kind} changed on another device." if error == "conflict"
                                 else f"An {kind} with this UID already exists.")

    def body(self) -> dict[str, Any]:
        return {"error": self.error, "detail": self.detail, "kind": self.kind, "server": self.server}


class Gone(Exception):
    """The object no longer exists on the server (its mirror row has been removed)."""


# Set by calendar/organizer.py around an organizer write: `before_write(uid, old_ics, cal,
# connector_id)` sees every changed copy (cal is None for a DELETE) just before it is sent, may
# adjust it (ORGANIZER, the guests' PARTSTAT) and records it for the iTIP mail.
WRITE_HOOK: ContextVar[Any] = ContextVar("calendar_write_hook", default=None)


def _before_write(uid: str, old_ics: str | None, cal: Calendar | None, connector_id: int) -> None:
    hook = WRITE_HOOK.get()
    if hook is not None:
        hook.before_write(uid, old_ics, cal, connector_id)


async def _client_scheduling(db: AsyncSession, user_id: int, connector_id: int, cal: Calendar) -> None:
    """On a server that schedules by itself (RFC 6638 calendar-auto-schedule), mark the copy so it
    sends no invitation or reply mail of its own: Mailr sends that mail (ical.mark_client_scheduling).
    The organizer write path marks its guests the same way (organizer.stamp)."""
    from mailr.calendar.organizer import auto_schedule

    conn = await db.get(Connector, connector_id)
    if conn is not None and await auto_schedule(db, conn):
        ical.mark_client_scheduling(cal, await mirror.my_addresses(db, user_id))


def _write_failed(uid: str) -> None:
    """The server refused the write that `_before_write` announced (412); a retry follows."""
    hook = WRITE_HOOK.get()
    if hook is not None and hasattr(hook, "write_failed"):
        hook.write_failed(uid)


# ---------------- plumbing ----------------


async def _dav(db: AsyncSession, connector_id: int) -> dav.DavClient:
    conn = await db.get(Connector, connector_id)
    user = await db.get(User, conn.user_id)
    return dav_client(conn, user)


async def _ctx(db: AsyncSession, user_id: int) -> tuple[Any, set[str]]:
    return await tzmod.user_zone(db, user_id), await mirror.my_addresses(db, user_id)


def safe_name(uid: str) -> str:
    """A resource name for a UID: the UID itself when it is plain, else a readable stem plus a hash."""
    if re.fullmatch(r"[A-Za-z0-9@._\-]{1,200}", uid):
        return uid
    stem = re.sub(r"[^A-Za-z0-9._\-]", "_", uid)[:60]
    return f"{stem}-{hashlib.sha256(uid.encode()).hexdigest()[:12]}"


async def _store(db: AsyncSession, coll: CalCollection, href: str, etag: str | None, ics: str,
                 client: dav.DavClient) -> CalObject:
    """Write the accepted version to the mirror (GET it when the server sent no ETag)."""
    if not etag:
        ics, etag = await client.get(href)
    user_tz, me = await _ctx(db, coll.user_id)
    obj = await mirror.upsert_object(db, coll, href, etag, ics, user_tz=user_tz, me=me, force=True)
    if obj is None:
        raise ValueError("the server stored something that is not an event or a task")
    await record_change(db, coll.user_id, obj.kind, obj.id)
    # Live editing (core/live.py): the item's new version, and the live event to open editors.
    from mailr.calendar import live_items

    await live_items.record(db, obj)
    return obj


async def refresh(db: AsyncSession, obj: CalObject, client: dav.DavClient | None = None) -> CalObject | None:
    """Fetch the server copy into the mirror; None (and a tombstone) when it is gone."""
    own = client is None
    client = client or await _dav(db, obj.connector_id)
    try:
        try:
            ics, etag = await client.get(obj.href)
        except dav.NotFound:
            await mirror.soft_delete(db, obj)
            await record_change(db, obj.user_id, obj.kind, obj.id, op="delete")
            return None
        coll = await db.get(CalCollection, obj.collection_id)
        user_tz, me = await _ctx(db, obj.user_id)
        got = await mirror.upsert_object(db, coll, obj.href, etag, ics, user_tz=user_tz, me=me, force=True)
        if got is not None:
            await record_change(db, obj.user_id, got.kind, got.id)
        return got or obj
    finally:
        if own:
            await client.aclose()


async def _out(db: AsyncSession, obj: CalObject | None) -> dict[str, Any] | None:
    return await mirror.object_out(db, obj) if obj is not None else None


def refuse_read_only(coll: CalCollection | None) -> None:
    """Nothing is written to a read-only calendar. A phone calendar's busy times (calendar/phonecal.py)
    get their own error: they change only on the phone."""
    if coll is None or not coll.read_only:
        return
    from mailr.calendar import phonecal

    if phonecal.is_phone(coll):
        raise phonecal.read_only_error(coll)
    raise ValueError(read_only_message(coll))


def read_only_message(coll: CalCollection) -> str:
    """Why nothing was written: the calendar is read-only for this user (a read-only share, or a
    group calendar only its editors change)."""
    name = coll.displayname or "this calendar"
    return f"“{name}” is read-only for you, so nothing was changed. Choose another calendar."


def _refused(e: dav.DavError, coll: CalCollection | None) -> None:
    """A 403 on a write: the server says the calendar is read-only for this user (a share
    narrowed since the last sync). The caller gets a plain message; the next sync reads the
    new rights."""
    if e.status == 403 and coll is not None and not isinstance(e, dav.Unauthorized):
        raise ValueError(read_only_message(coll)) from None


# ---------------- the read-modify-write ----------------


async def rmw(db: AsyncSession, user_id: int, p: Any, obj: CalObject, mutate: Callable[[Calendar], Any], *,
              client_etag: str | None = None) -> tuple[CalObject, Any]:
    """PUT a changed copy of `obj` with If-Match. Raises Conflict (409) or Gone."""
    await uid_lock(db, user_id, obj.uid)
    await db.refresh(obj)
    if obj.deleted_at is not None:
        raise Gone(obj.uid)
    coll = await db.get(CalCollection, obj.collection_id)
    refuse_read_only(coll)
    client = await _dav(db, obj.connector_id)
    async with client:
        if client_etag and obj.etag and client_etag != obj.etag:
            fresh = await refresh(db, obj, client)
            if fresh is None:
                raise Gone(obj.uid)
            obj = fresh
            if client_etag != obj.etag:
                raise Conflict(obj.kind, await _out(db, obj))
        for attempt in (0, 1):
            cal = Calendar.from_ical(obj.ics)
            result = mutate(cal)
            _before_write(obj.uid, obj.ics, cal, obj.connector_id)
            await _client_scheduling(db, user_id, obj.connector_id, cal)
            ics = ical.serialize(cal)
            try:
                etag = await client.put(obj.href, ics, if_match=obj.etag)
            except dav.PreconditionFailed:
                _write_failed(obj.uid)
                fresh = await refresh(db, obj, client)
                if fresh is None:
                    raise Gone(obj.uid) from None
                obj = fresh
                if client_etag or attempt == 1:
                    raise Conflict(obj.kind, await _out(db, obj)) from None
                continue
            except dav.NotFound:
                await mirror.soft_delete(db, obj)
                await record_change(db, user_id, obj.kind, obj.id, op="delete")
                raise Gone(obj.uid) from None
            except dav.DavError as e:
                _refused(e, coll)
                raise
            obj = await _store(db, coll, obj.href, etag, ics, client)
            return obj, result
    raise Conflict(obj.kind, await _out(db, obj))  # not reached


async def create(db: AsyncSession, user_id: int, coll: CalCollection, cal: Calendar, uid: str) -> CalObject:
    """PUT a new resource with If-None-Match: *. A UID already there is a Conflict("exists")."""
    refuse_read_only(coll)
    await uid_lock(db, user_id, uid)
    existing = await mirror.find_by_uid(db, user_id, uid)
    kind = "task" if list(cal.walk("VTODO")) else "event"
    if existing is not None:
        raise Conflict(kind, await _out(db, existing), error="exists")
    href = dav.norm_href(coll.href.rstrip("/") + "/" + safe_name(uid) + ".ics")
    _before_write(uid, None, cal, coll.connector_id)
    await _client_scheduling(db, user_id, coll.connector_id, cal)
    ics = ical.serialize(cal)
    client = await _dav(db, coll.connector_id)
    async with client:
        try:
            etag = await client.put(href, ics, if_none_match=True)
        except dav.PreconditionFailed:
            raise Conflict(kind, None, error="exists") from None
        except dav.DavError as e:
            _refused(e, coll)
            raise
        return await _store(db, coll, href, etag, ics, client)


async def move(db: AsyncSession, user_id: int, obj: CalObject, target: CalCollection,
               mutate: Callable[[Calendar], Any] | None = None, *, client_etag: str | None = None
               ) -> tuple[CalObject, Any]:
    """Move to another collection: PUT the new resource (If-None-Match), then DELETE the old one
    (If-Match). When the DELETE fails, the new copy is removed again."""
    await uid_lock(db, user_id, obj.uid)
    await db.refresh(obj)
    if client_etag and obj.etag and client_etag != obj.etag:
        raise Conflict(obj.kind, await _out(db, obj))
    refuse_read_only(await db.get(CalCollection, obj.collection_id))
    refuse_read_only(target)
    if target.connector_id != obj.connector_id:
        raise ValueError("an item can only move between calendars of the same account")
    cal = Calendar.from_ical(obj.ics)
    result = mutate(cal) if mutate else None
    _before_write(obj.uid, obj.ics, cal, obj.connector_id)
    await _client_scheduling(db, user_id, obj.connector_id, cal)
    ics = ical.serialize(cal)
    new_href = dav.norm_href(target.href.rstrip("/") + "/" + safe_name(obj.uid) + ".ics")
    client = await _dav(db, obj.connector_id)
    async with client:
        try:
            etag = await client.put(new_href, ics, if_none_match=True)
        except dav.PreconditionFailed:
            raise Conflict(obj.kind, None, error="exists", detail="The other calendar already has this item.") from None
        try:
            await client.delete(obj.href, if_match=obj.etag)
        except dav.NotFound:
            pass
        except dav.DavError:
            try:
                await client.delete(new_href)
            except dav.DavError:
                pass
            fresh = await refresh(db, obj, client)
            raise Conflict(obj.kind, await _out(db, fresh)) from None
        await mirror.soft_delete(db, obj)
        await record_change(db, user_id, obj.kind, obj.id, op="delete")
        return await _store(db, target, new_href, etag, ics, client), result


async def delete(db: AsyncSession, user_id: int, obj: CalObject, *, client_etag: str | None = None) -> None:
    """DELETE with If-Match. A resource already gone counts as deleted."""
    await uid_lock(db, user_id, obj.uid)
    await db.refresh(obj)
    if obj.deleted_at is not None:
        return
    coll = await db.get(CalCollection, obj.collection_id)
    refuse_read_only(coll)
    client = await _dav(db, obj.connector_id)
    async with client:
        if client_etag and obj.etag and client_etag != obj.etag:
            fresh = await refresh(db, obj, client)
            if fresh is None:
                return
            if client_etag != fresh.etag:
                raise Conflict(fresh.kind, await _out(db, fresh))
            obj = fresh
        for attempt in (0, 1):
            _before_write(obj.uid, obj.ics, None, obj.connector_id)
            try:
                await client.delete(obj.href, if_match=obj.etag)
                break
            except dav.NotFound:
                break
            except dav.PreconditionFailed:
                _write_failed(obj.uid)
                fresh = await refresh(db, obj, client)
                if fresh is None:
                    return
                obj = fresh
                if client_etag or attempt == 1:
                    raise Conflict(obj.kind, await _out(db, obj)) from None
    await mirror.soft_delete(db, obj)
    await record_change(db, user_id, obj.kind, obj.id, op="delete")
    from mailr.core import live

    await live.forget(db, user_id, obj.kind, obj.uid)


# ---------------- where things go ----------------


async def find(db: AsyncSession, user_id: int, uid: str, kind: str, p: Any = None) -> CalObject | None:
    """The mirror row for a UID the principal may see; on a miss the mirror is synced and asked again."""
    await mirror.ensure_synced(db, user_id, p)
    obj = await mirror.find_by_uid(db, user_id, uid, kind, p)
    if obj is None:
        await mirror.ensure_synced(db, user_id, p, force=True)
        obj = await mirror.find_by_uid(db, user_id, uid, kind, p)
    return obj


async def target_collection(db: AsyncSession, user_id: int, p: Any, url: str | None, kind: str) -> CalCollection:
    """The collection to create in: `url`, else the default calendar / task list."""
    from mailr.calendar import client as facade

    await mirror.ensure_synced(db, user_id, p)
    if not await mirror.visible_connectors(db, user_id, p):
        raise CalendarNotConnected("no calendar account is visible to this token" if mirror._restricted(p)
                                   else "no calendar account is connected")
    if not url:
        d = await facade.defaults(db, user_id, p=p)
        url = d["task_list" if kind == "task" else "calendar"]
    coll = await mirror.collection_by_url(db, user_id, url, p) if url else None
    if coll is None and url:
        raise ValueError("unknown calendar")
    if coll is None:
        raise CalendarNotConnected("the calendar account has no calendars")
    return coll


# ---------------- events ----------------


def _occurrence_value(occurrence: str | None, user_tz: Any) -> datetime | date | None:
    return ical.parse_when(occurrence, user_tz) if occurrence else None


async def created_before(db: AsyncSession, user_id: int, payload: dict[str, Any], kind: str,
                         p: Any = None) -> CalObject | None:
    """An agent's create names the UIDs an earlier attempt of the same create was made under
    (`_same_as`, agent/calendar_common.py `stable_uids`): the item that attempt made, if any."""
    uids = [u for u in payload.get("_same_as") or [] if isinstance(u, str)]
    if not uids:
        return None
    await mirror.ensure_synced(db, user_id, p)
    for uid in uids:
        if (obj := await mirror.find_by_uid(db, user_id, uid, kind, p)) is not None:
            return obj
    return None


async def create_once(db: AsyncSession, user_id: int, coll: CalCollection, cal: Calendar, uid: str,
                      payload: dict[str, Any], kind: str, p: Any = None) -> tuple[CalObject, bool]:
    """`create`, except that a create an agent repeats (`_same_as`) gets the item its first
    attempt made, also when that attempt's answer was lost after the server stored it. Returns
    the item and whether it existed already."""
    if (obj := await created_before(db, user_id, payload, kind, p)) is not None:
        return obj, True
    try:
        return await create(db, user_id, coll, cal, uid), False
    except Conflict as e:
        if e.error != "exists" or uid not in (payload.get("_same_as") or []):
            raise
        obj = await find(db, user_id, uid, kind, p)
        if obj is None:
            raise
        return obj, True


async def create_event(db: AsyncSession, user_id: int, payload: dict[str, Any], p: Any = None) -> dict[str, Any]:
    coll = await target_collection(db, user_id, p, payload.get("calendar"), "event")
    user_tz = await tzmod.user_zone(db, user_id)
    cal = ical.build_event(payload, user_tz)
    uid = str(next(iter(cal.walk("VEVENT")))["uid"])
    obj, existed = await create_once(db, user_id, coll, cal, uid, payload, "event", p)
    out = await mirror.object_out(db, obj, user_tz)
    return {**out, "existing": True} if existed else out


async def update_event(db: AsyncSession, user_id: int, uid: str, changes: dict[str, Any], scope: str = "all",
                       occurrence: str | None = None, p: Any = None, *, etag: str | None = None,
                       base_version: int | None = None) -> dict[str, Any] | None:
    obj = await find(db, user_id, uid, "event", p)
    if obj is None:
        return None
    from mailr.calendar import live_items

    # Made from an older version: merged with what changed since (raises live.LiveConflict).
    changes, _ = await live_items.resolve_patch(db, obj, dict(changes), base_version)
    if base_version is not None:
        etag = None  # the merge replaces the ETag check
    changes = dict(changes)
    move_to = changes.pop("calendar", None)
    target = None
    if move_to:
        target = await mirror.collection_by_url(db, user_id, move_to, p)
        if target is None:
            raise ValueError("unknown calendar")
        if target.id == obj.collection_id:
            target = None
    user_tz = await tzmod.user_zone(db, user_id)
    occ = _occurrence_value(occurrence, user_tz)
    coll = await db.get(CalCollection, obj.collection_id)

    if obj.recurring and occ is not None and scope == "this":
        def this(cal: Calendar) -> str:
            master, overrides = ical.master_and_overrides(cal)
            ov = next((o for o in overrides if ical.same_instant(o.get("recurrence-id"), occ)), None)
            if ov is None:
                ov = ical.make_override(master, occ, user_tz)
                cal.add_component(ov)
            ical.apply_changes(ov, changes, user_tz, master=master)
            return ical.canonical_occurrence(ical.value(ov.get("recurrence-id"), cal, user_tz)[0])

        obj, rid = await rmw(db, user_id, p, obj, this, client_etag=etag)
        return await _instance(db, obj, rid, user_tz)

    if obj.recurring and occ is not None and scope == "following":
        new_uid = f"{uuid.uuid4()}@mailr"
        holder: dict[str, Any] = {}

        def split(cal: Calendar) -> None:
            master, overrides = ical.master_and_overrides(cal)
            new = copy.deepcopy(master)
            ical.truncate_series(master, occ)
            for o in overrides:  # exceptions after the split move to the new series
                rid = getattr(o.get("recurrence-id"), "dt", None)
                if rid is not None and ical.as_utc(rid, user_tz) >= ical.as_utc(occ, user_tz):
                    cal.subcomponents.remove(o)
            new.pop("uid", None)
            new.add("uid", new_uid)
            start = ical.in_zone_of(master.get("dtstart"), occ, user_tz)
            dur = None
            if new.get("dtend") is not None:
                dur = new["dtend"].dt - new["dtstart"].dt
            new.pop("dtstart", None)
            new.add("dtstart", start)
            if dur is not None:
                new.pop("dtend", None)
                new.add("dtend", start + dur)
            rule = dict(new.get("rrule") or {})
            rule.pop("COUNT", None)
            new.pop("rrule", None)
            if rule:
                new.add("rrule", rule)
            ical.apply_changes(new, changes, user_tz, bump=False)
            out = ical.new_calendar()
            out.add_component(new)
            for tz in cal.walk("VTIMEZONE"):
                out.add_component(tz)
            holder["cal"] = out

        if ical.as_utc(occ, user_tz) <= (obj.start_at or ical.as_utc(occ, user_tz)):
            scope = "all"  # "following" from the first occurrence is the whole series
        else:
            await rmw(db, user_id, p, obj, split, client_etag=etag)
            new_obj = await create(db, user_id, target or coll, holder["cal"], new_uid)
            return await mirror.object_out(db, new_obj, user_tz)

    def whole(cal: Calendar) -> None:
        master, _ = ical.master_and_overrides(cal)
        if changes:
            ical.apply_changes(master, changes, user_tz)

    if target is not None:
        obj, _ = await move(db, user_id, obj, target, whole, client_etag=etag)
    elif changes:
        obj, _ = await rmw(db, user_id, p, obj, whole, client_etag=etag)
    return await mirror.object_out(db, obj, user_tz)


async def _instance(db: AsyncSession, obj: CalObject, occurrence: str, user_tz: Any) -> dict[str, Any]:
    from mailr.models import CalOccurrence

    coll = await db.get(CalCollection, obj.collection_id)
    row = await db.get(CalOccurrence, (obj.id, occurrence))
    cal = Calendar.from_ical(obj.ics)
    comp = next((c for c in cal.walk("VEVENT") if c.get("recurrence-id") is not None and ical.canonical_occurrence(
        ical.value(c.get("recurrence-id"), cal, user_tz)[0]) == occurrence), None)
    ov = ical.component_fields(comp, cal, user_tz) if comp is not None else None
    if row is not None:
        start_at, end_at, all_day = row.start_at, row.end_at, row.all_day
    elif ov is not None:
        start_at, end_at, all_day = ov["start_at"], ov["end_at"], ov["all_day"]
    else:
        return await mirror.object_out(db, obj, user_tz)
    return mirror.instance_out(obj, coll, user_tz, occurrence=occurrence, start_at=start_at, end_at=end_at,
                               all_day=all_day, is_override=comp is not None, ov=ov)


async def delete_event(db: AsyncSession, user_id: int, uid: str, scope: str = "all", occurrence: str | None = None,
                       p: Any = None, *, etag: str | None = None) -> bool:
    obj = await find(db, user_id, uid, "event", p)
    if obj is None:
        return False
    user_tz = await tzmod.user_zone(db, user_id)
    occ = _occurrence_value(occurrence, user_tz)
    if obj.recurring and occ is not None and scope in ("this", "following") and not (
            scope == "following" and obj.start_at is not None and ical.as_utc(occ, user_tz) <= obj.start_at):
        def drop(cal: Calendar) -> None:
            master, overrides = ical.master_and_overrides(cal)
            for o in overrides:
                rid = o.get("recurrence-id")
                if (scope == "this" and ical.same_instant(rid, occ)) or (
                        scope == "following" and ical.as_utc(getattr(rid, "dt", rid), user_tz) >= ical.as_utc(occ, user_tz)):
                    cal.subcomponents.remove(o)
            if scope == "this":
                master.add("exdate", ical.in_zone_of(master.get("dtstart"), occ, user_tz))
            else:
                ical.truncate_series(master, occ)
            master.pop("dtstamp", None)
            master.add("dtstamp", datetime.now(UTC))

        await rmw(db, user_id, p, obj, drop, client_etag=etag)
        return True
    try:
        await delete(db, user_id, obj, client_etag=etag)
    except Gone:
        pass
    return True


# ---------------- tasks ----------------


async def create_task(db: AsyncSession, user_id: int, payload: dict[str, Any], p: Any = None) -> dict[str, Any]:
    coll = await target_collection(db, user_id, p, payload.get("list"), "task")
    user_tz = await tzmod.user_zone(db, user_id)
    cal = ical.build_todo(payload, user_tz)
    uid = str(next(iter(cal.walk("VTODO")))["uid"])
    obj = await create(db, user_id, coll, cal, uid)
    return await mirror.object_out(db, obj, user_tz)


async def update_task(db: AsyncSession, user_id: int, uid: str, changes: dict[str, Any], p: Any = None, *,
                      etag: str | None = None, base_version: int | None = None) -> dict[str, Any] | None:
    obj = await find(db, user_id, uid, "task", p)
    if obj is None:
        return None
    from mailr.calendar import live_items

    changes, _ = await live_items.resolve_patch(db, obj, dict(changes), base_version)
    if base_version is not None:
        etag = None  # the merge replaces the ETag check
    changes = dict(changes)
    move_to = changes.pop("list", None)
    user_tz = await tzmod.user_zone(db, user_id)

    def change(cal: Calendar) -> None:
        comp, _ = ical.master_and_overrides(cal, "VTODO")
        ical.apply_task_changes(comp, changes, user_tz)

    target = None
    if move_to:
        target = await mirror.collection_by_url(db, user_id, move_to, p)
        if target is None:
            raise ValueError("unknown calendar")
        if target.id == obj.collection_id:
            target = None
    if target is not None:
        obj, _ = await move(db, user_id, obj, target, change if changes else None, client_etag=etag)
    elif changes:
        obj, _ = await rmw(db, user_id, p, obj, change, client_etag=etag)
    return await mirror.object_out(db, obj, user_tz)


async def delete_task(db: AsyncSession, user_id: int, uid: str, p: Any = None, *, etag: str | None = None) -> bool:
    obj = await find(db, user_id, uid, "task", p)
    if obj is None:
        return False
    try:
        await delete(db, user_id, obj, client_etag=etag)
    except Gone:
        pass
    return True


# ---------------- invitations ----------------


# Occurrences before a "following" answer that keep the earlier answer (as overrides).
FOLLOWING_KEEP_DAYS = 30
FOLLOWING_KEEP_CAP = 100


def _partstat_of(comp: Any, attendee: str) -> str | None:
    for a in ical.as_list(comp.get("attendee")):
        if ical.addr(a).lower() == attendee.lower():
            return str(a.params.get("PARTSTAT", "NEEDS-ACTION"))
    return None


def answer_occurrences(cal: Calendar, attendee: str, partstat: str, scope: str, occ: datetime | date | None,
                       user_tz: Any) -> str | None:
    """Set my PARTSTAT on the series in `cal` for `scope` and return the canonical RECURRENCE-ID
    the answer is about (None for the whole series).

    - `all` (or no occurrence, or a single event): every VEVENT of the resource.
    - `this`: only the occurrence; an override is written when there is none yet.
    - `following`: the master and every override from the occurrence on. The occurrences
      before it (at most FOLLOWING_KEEP_DAYS back, FOLLOWING_KEEP_CAP of them) keep the
      earlier answer as overrides, because an attendee cannot split the organizer's series.
      From the first occurrence it is `all`."""
    master, overrides = ical.master_and_overrides(cal)
    recurring = master is not None and (master.get("rrule") is not None or master.get("rdate") is not None)
    if not recurring or occ is None or scope == "all":
        for comp in cal.walk("VEVENT"):
            ical.set_attendee(comp, attendee, partstat)
        return None
    occ_utc = ical.as_utc(occ, user_tz)
    rid_of = {ical.canonical_occurrence(ical.value(o.get("recurrence-id"), cal, user_tz)[0]): o for o in overrides}
    if scope == "this":
        ov = next((o for o in overrides if ical.same_instant(o.get("recurrence-id"), occ)), None)
        if ov is None:
            ov = ical.make_override(master, occ, user_tz)
            cal.add_component(ov)
        ical.set_attendee(ov, attendee, partstat)
        return ical.canonical_occurrence(ical.value(ov.get("recurrence-id"), cal, user_tz)[0])
    # following
    from mailr.calendar import expand

    first = ical.as_utc(ical.value(master.get("dtstart"), cal, user_tz)[0], user_tz)
    if first is not None and occ_utc <= first:
        for comp in cal.walk("VEVENT"):
            ical.set_attendee(comp, attendee, partstat)
        return None
    before = _partstat_of(master, attendee)
    keep_from = min(occ_utc, datetime.now(UTC)) - timedelta(days=FOLLOWING_KEEP_DAYS)
    earlier, _ = expand.instances(cal, keep_from, occ_utc, user_tz, cap=FOLLOWING_KEEP_CAP)
    for inst in earlier:
        if inst.occurrence in rid_of or inst.start_at >= occ_utc:
            continue
        ov = ical.make_override(master, ical.parse_when(inst.occurrence, user_tz), user_tz)
        if before is not None:
            ical.set_attendee(ov, attendee, before)
        cal.add_component(ov)
    ical.set_attendee(master, attendee, partstat)
    for o in rid_of.values():
        rv = ical.as_utc(getattr(o.get("recurrence-id"), "dt", None), user_tz)
        if rv is not None and rv >= occ_utc:
            ical.set_attendee(o, attendee, partstat)
    return ical.canonical_occurrence(occ)


class InviteRefused(ValueError):
    """Accepting a mailed invitation would store something the card did not show."""


async def _invite_to_store(db: AsyncSession, user_id: int, uid: str, invite_ics: bytes) -> tuple[Calendar | None, str | None]:
    """What Accept may add from a mailed invitation: only the VEVENTs with the card's UID (and the
    time zones), under the same rules as an invitation added on arrival (calendar/inbound.py:
    one organizer named on every part, never one of my addresses, at most one master, a repeat
    rule mailr would write itself). Returns (calendar, None), or (None, why it is refused)."""
    from mailr.calendar import expand
    from mailr.calendar.inbound import _structure_problem, parse_incoming

    inv = await parse_incoming(invite_ics)
    if inv is None:
        return None, "The invitation could not be read"
    comps = [c for c in inv.walk("VEVENT") if str(c.get("uid", "") or "") == uid]
    if not uid or not comps:
        return None, "The invitation does not describe this event"
    org = next((ical.addr(c.get("organizer")).lower() for c in comps if c.get("organizer") is not None), "")
    if not org:
        return None, "The invitation names no organiser"
    bad = _structure_problem(comps, uid, org, await mirror.my_addresses(db, user_id))
    if bad:
        return None, bad
    out = ical.new_calendar()
    for tzc in inv.walk("VTIMEZONE"):
        out.add_component(copy.deepcopy(tzc))
    for c in comps:
        x = copy.deepcopy(c)
        ical.strip_sender_extras(x)  # my default reminders apply, never the sender's
        out.add_component(x)
    why = expand.rule_problem(out, await tzmod.user_zone(db, user_id))
    if why:
        return None, f"Not added: {why}"
    return out, None


async def invite_problem(db: AsyncSession, user_id: int, uid: str, invite_ics: bytes | None, partstat: str,
                         p: Any = None) -> str | None:
    """Why answering this mailed invitation with `partstat` would be refused (set_partstat's add
    branch), or None: checked before a reply is mailed."""
    if invite_ics is None or partstat == "DECLINED" or not await mirror.visible_connectors(db, user_id, p):
        return None
    if await find(db, user_id, uid, "event", p) is not None:
        return None
    return (await _invite_to_store(db, user_id, uid, invite_ics))[1]


async def set_partstat(db: AsyncSession, user_id: int, uid: str, attendee: str, partstat: str,
                       invite_ics: bytes | None, p: Any = None, *, scope: str = "all",
                       occurrence: str | None = None) -> str:
    """Set my PARTSTAT on the event (for `scope`, see answer_occurrences); add the event from
    the invite when it is not there yet. Returns 'updated', 'added', 'absent' or 'no-calendar'."""
    if not await mirror.visible_connectors(db, user_id, p):
        return "no-calendar"
    obj = await find(db, user_id, uid, "event", p)
    if obj is not None:
        user_tz = await tzmod.user_zone(db, user_id)
        occ = _occurrence_value(occurrence, user_tz)

        def answer(cal: Calendar) -> None:
            answer_occurrences(cal, attendee, partstat, scope, occ, user_tz)

        await rmw(db, user_id, p, obj, answer)
        return "updated"
    if invite_ics is None or partstat == "DECLINED":
        return "absent"
    out, why = await _invite_to_store(db, user_id, uid, invite_ics)
    if out is None:
        raise InviteRefused(why or "The invitation could not be read")
    for comp in out.walk("VEVENT"):
        ical.set_attendee(comp, attendee, partstat)
    coll = await target_collection(db, user_id, p, None, "event")
    await create(db, user_id, coll, out, uid)
    return "added"
