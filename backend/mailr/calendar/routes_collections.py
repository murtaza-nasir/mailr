"""Calendar collections, moving events between them, search, free/busy and find-a-time
(sections 7 and 8).

Collections: MKCALENDAR (events, tasks or both) on one of the caller's accounts; PROPPATCH of
the name and colour (a colour the server refuses is kept in mailr, `color_local`); `hidden`,
`busy`, `notify`, `sort_order` and the default reminders are mailr-only. A delete needs
`confirm=<name>`, refuses the saved default and the last collection of its kind, and can move
the tasks of a list to another list first. Token writes use the classes `calendar.manage`
(calendars) and `tasks.lists` (task lists; create is allowed, rename and delete ask).
"""

from __future__ import annotations

import builtins
import re
import uuid
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import client as cal
from mailr.calendar import dav, describe, expand, freebusy, ical, mirror, search, sync, writer
from mailr.calendar import tz as tzmod
from mailr.calendar.describe import calendar_name
from mailr.calendar.routes import (
    EXECUTORS,
    _collection_visible,
    _etag,
    _gated,
    _need_calendar,
    _range,
    where_ctx,
    where_is,
)
from mailr.calendar.routes_tasks import gated
from mailr.core import permissions
from mailr.core.auth import Principal, scope
from mailr.core.events import record_change
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import CalCollection, CalObject, Connector, User

router = APIRouter(tags=["calendar"])

_COLOR = r"^(#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?|)$"
COMPONENTS = {"events": ["VEVENT"], "tasks": ["VTODO"], "both": ["VEVENT", "VTODO"]}


# ---------------- helpers ----------------


async def _visible(db: AsyncSession, user_id: int, p: Any, calendar_id: int) -> tuple[CalCollection, Connector]:
    coll = await db.get(CalCollection, calendar_id)
    if coll is None or coll.user_id != user_id or coll.deleted_at is not None:
        raise LookupError("calendar not found")
    conn = await db.get(Connector, coll.connector_id)
    if conn is None or coll.connector_id not in await mirror.visible_connector_ids(db, user_id, p):
        raise LookupError("calendar not found")
    return coll, conn


async def collection_dict(db: AsyncSession, user_id: int, coll: CalCollection, conn: Connector) -> dict[str, Any]:
    return mirror.collection_out(coll, conn, await mirror.kind_overrides(db, user_id))


def _prefs(user: User) -> dict[str, dict[str, Any]]:
    return {k: dict(v) for k, v in ((user.settings or {}).get("collection_prefs") or {}).items()}


async def _set_prefs(db: AsyncSession, user_id: int, href: str, **kw: Any) -> None:
    """Change this calendar's preferences only (core/usersettings.py): a change to another
    calendar, or to another setting, at the same time keeps its own."""
    from mailr.core import usersettings

    cur = dict(((await usersettings.current(db, user_id)).get("collection_prefs") or {}).get(href) or {})
    for k, v in kw.items():
        if v is None:
            cur.pop(k, None)
        else:
            cur[k] = v
    if cur:
        await usersettings.patch_in(db, user_id, "collection_prefs", {href: cur})
    else:
        await usersettings.patch_in(db, user_id, "collection_prefs", remove=[href])


def _class_for(coll_or_kind: Any) -> str:
    """`tasks.lists` for a collection that holds only tasks, else `calendar.manage`."""
    if isinstance(coll_or_kind, str):
        return "tasks.lists" if coll_or_kind == "tasks" else "calendar.manage"
    comps = [c.upper() for c in coll_or_kind.components or []]
    return "tasks.lists" if comps and "VEVENT" not in comps else "calendar.manage"


async def _group_calendar(db: AsyncSession, user_id: int, coll: CalCollection) -> dict[str, Any] | None:
    """The `shared` mark of a group calendar (calendar/groups.py) or of a calendar someone shares
    with the user (calendar/sharing.py, kind "user"), or None."""
    return ((await mirror.kind_overrides(db, user_id)).get(coll.href) or {}).get("shared")


def _not_yours(coll: CalCollection, shared: dict[str, Any], what: str) -> str:
    """Why a member or a sharee cannot rename or delete a calendar."""
    if shared.get("kind") == "user":
        tail = "renames it" if what == "rename" else "can delete it; you can hide it"
        return f"“{_name(coll)}” is shared with you by {shared.get('owner') or 'its owner'}; only the owner {tail}"
    if what == "rename":
        return f"“{_name(coll)}” belongs to the {shared['group']} group; an administrator renames it"
    return f"“{_name(coll)}” belongs to the {shared['group']} group; you can hide it, and an administrator can delete it"


def _name(coll: CalCollection) -> str:
    return calendar_name(coll.displayname, coll.href)


# ---------------- operations (the executors call these) ----------------


async def create_collection(db: AsyncSession, user: User, p: Any, payload: dict[str, Any]) -> dict[str, Any]:
    conns = await mirror.visible_connectors(db, user.id, p)
    if not conns:
        raise writer.CalendarNotConnected("no calendar account is connected")
    conn = None
    if payload.get("connector_id") is not None:
        conn = next((c for c in conns if c.id == int(payload["connector_id"])), None)
        if conn is None:
            raise LookupError("calendar account not found")
    if conn is None:
        d = await cal.defaults(db, user.id, p=p)
        conn = next((c for c in conns if c.id == d.get("connector_id")), conns[0])
    name = str(payload["name"]).strip()
    comps = COMPONENTS[payload.get("kind") or "events"]
    color = payload.get("color") or None
    slug = (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "calendar") + "-" + uuid.uuid4().hex[:6]
    client = sync.dav_client(conn, user)
    async with client:
        home = ((conn.state or {}).get("caldav") or {}).get("home") or await client.home()
        href = await client.mkcalendar(home, slug, name, comps, color)
    await sync.run_inline(conn.id, budget=8)  # in its own session; the new row is read below
    coll = await mirror.collection_by_url(db, user.id, href, p)
    if coll is None:  # the sync is busy elsewhere: the row is added here and filled by the next sync
        coll = CalCollection(user_id=user.id, connector_id=conn.id, href=dav.norm_href(href), displayname=name,
                             color=color[:7] if color else None, components=comps)
        db.add(coll)
        await db.flush()
        await record_change(db, user.id, "calendar", coll.id)
    if payload.get("default_reminders") is not None:
        await _set_prefs(db, user.id, coll.href, default_reminders=payload["default_reminders"])
    return await collection_dict(db, user.id, coll, conn)


async def update_collection(db: AsyncSession, user: User, p: Any, calendar_id: int, payload: dict[str, Any]
                            ) -> dict[str, Any]:
    coll, conn = await _visible(db, user.id, p, calendar_id)
    name, color = payload.get("name"), payload.get("color")
    shared = await _group_calendar(db, user.id, coll)
    if shared and name is not None and name != coll.displayname:
        raise PermissionError(_not_yours(coll, shared, "rename"))
    from mailr.calendar import phonecal

    if (shared or phonecal.is_phone(coll)) and color is not None:
        # A group or shared calendar's colour is its owner's for everyone; this user's choice stays in mailr.
        # A phone calendar's colour comes from the phone; a choice made here stays in mailr too.
        await _set_prefs(db, user.id, coll.href, color_local=color[:7] if color else None)
        name = color = None
    if name is not None or color is not None:
        if coll.read_only:
            raise ValueError("this calendar is read-only")
        client = sync.dav_client(conn, user)
        async with client:
            if name is not None:
                try:
                    ok = await client.proppatch(coll.href, displayname=name)
                except dav.DavError:
                    ok = False
                if not ok:
                    raise ValueError("the calendar server did not accept the new name")
                coll.displayname = name
            if color is not None:
                try:
                    ok = await client.proppatch(coll.href, color=color) if color else False
                except dav.DavError:
                    ok = False
                if ok:
                    coll.color = color[:7]
                    await _set_prefs(db, user.id, coll.href, color_local=None)
                elif color:  # refused: kept in mailr ("only in mailr")
                    await _set_prefs(db, user.id, coll.href, color_local=color[:7])
                else:
                    await _set_prefs(db, user.id, coll.href, color_local=None)
    for k in ("hidden", "busy", "notify", "sort_order"):
        if k == "notify" and phonecal.is_phone(coll):
            continue  # busy times from a phone never remind: the phone's own calendar app does
        if payload.get(k) is not None:
            setattr(coll, k, payload[k])
    if "default_reminders" in payload:
        await _set_prefs(db, user.id, coll.href, default_reminders=payload["default_reminders"])
    await mirror.mark_collection_rev(db, coll)
    await record_change(db, user.id, "calendar", coll.id)
    return await collection_dict(db, user.id, coll, conn)


async def delete_collection(db: AsyncSession, user: User, p: Any, calendar_id: int, confirm: str,
                            move_to: str | None = None) -> dict[str, Any]:
    coll, conn = await _visible(db, user.id, p, calendar_id)
    if confirm != _name(coll):
        raise ValueError("confirm must be the calendar's name")
    shared = await _group_calendar(db, user.id, coll)
    if shared:
        raise PermissionError(_not_yours(coll, shared, "delete"))
    if coll.read_only:
        raise ValueError("this calendar is read-only")
    u = await db.get(User, user.id)
    saved = (u.settings or {}).get("calendar_defaults") or {}
    if coll.href in (saved.get("calendar"), saved.get("task_list")):
        raise PermissionError("the default calendar or task list cannot be deleted; choose another default first")
    ov = await mirror.kind_overrides(db, user.id)
    others = [c for c in await mirror.collections_of(db, await mirror.visible_connector_ids(db, user.id, p))
              if c.id != coll.id]
    ev, tk = mirror.kinds_of(coll, ov)
    if ev and not any(mirror.kinds_of(c, ov)[0] for c in others):
        raise PermissionError("the last calendar cannot be deleted")
    if tk and not ev and not any(mirror.kinds_of(c, ov)[1] for c in others):
        raise PermissionError("the last task list cannot be deleted")
    moved = []
    if move_to:
        target = await mirror.collection_by_url(db, user.id, move_to, p)
        if target is None and str(move_to).isdigit():
            t = await db.get(CalCollection, int(move_to))
            target = t if t is not None and t.user_id == user.id and t.deleted_at is None else None
        if target is None or target.id == coll.id:
            raise ValueError("unknown list to move the tasks to")
        tasks = (await db.execute(select(CalObject).where(
            CalObject.collection_id == coll.id, CalObject.kind == "task", CalObject.deleted_at.is_(None))
            .order_by(CalObject.id))).scalars().all()
        for t in tasks:
            await writer.move(db, user.id, t, target)
            moved.append(t.uid)
    client = sync.dav_client(conn, user)
    async with client:
        try:
            await client.delete_collection(coll.href)
        except dav.NotFound:
            pass
    gone = await mirror.soft_delete_collection(db, coll)
    for kind in ("event", "task"):
        ids = [i for k, i in gone if k == kind]
        if ids:
            await record_change(db, user.id, kind, ids, op="delete")
    await record_change(db, user.id, "calendar", coll.id, op="delete")
    await _set_prefs(db, user.id, coll.href, color_local=None, default_reminders=None)
    return {"ok": True, "calendar_id": coll.id, "url": coll.href, "moved": moved, "deleted": len(gone)}


async def reorder_collections(db: AsyncSession, user: User, p: Any, ids: list[int]) -> list[dict[str, Any]]:
    out = []
    for i, cid in enumerate(ids):
        coll, conn = await _visible(db, user.id, p, int(cid))
        if coll.sort_order != i:
            coll.sort_order = i
            await mirror.mark_collection_rev(db, coll)
            await record_change(db, user.id, "calendar", coll.id)
        out.append(await collection_dict(db, user.id, coll, conn))
    return out


async def _x_collections(db, user, payload):
    p = await permissions.requester(db, user, payload)
    op = payload.get("op")
    try:
        if op == "create":
            return await create_collection(db, user, p, payload)
        if op == "update":
            return await update_collection(db, user, p, int(payload["calendar_id"]), payload)
        if op == "delete":
            return await delete_collection(db, user, p, int(payload["calendar_id"]), str(payload.get("confirm") or ""),
                                           payload.get("move_to"))
        if op == "reorder":
            return {"items": await reorder_collections(db, user, p, list(payload["ids"]))}
    except PermissionError as e:
        raise HTTPException(409, str(e)) from None
    raise ValueError(f"unknown collection operation {op}")


for _a in ("calendar.manage", "tasks.lists"):
    EXECUTORS[_a] = _x_collections
    permissions.executor(_a)(_x_collections)


# ---------------- collection routes ----------------


class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field("events", pattern="^(events|tasks|both)$")
    color: str | None = Field(None, pattern=_COLOR)
    connector_id: int | None = None
    # Minutes before; a list, or {"timed": [...], "all_day": [...]} for calendars.
    default_reminders: builtins.list[int] | dict[str, builtins.list[int]] | None = None


# ACT calendar.create @api, ACT list.create @api
@router.post("/calendar/collections")
async def create_collection_route(body: CollectionIn, p: Principal = Depends(scope("calendar")),
                                  db: AsyncSession = Depends(get_db)):
    """A new calendar or task list (MKCALENDAR) on an account the caller may see."""
    payload = {"op": "create", **body.model_dump(exclude_none=True)}
    return await gated(db, p, _class_for(body.kind), payload, target=body.name, ctx={},
                       preview={"title": f"Create {'task list' if body.kind == 'tasks' else 'calendar'} “{body.name}”",
                                "kind": "calendar", "lines": [body.name, f"holds {body.kind}"]})


class CollectionPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    color: str | None = Field(None, pattern=_COLOR)  # "" goes back to the server's colour
    hidden: bool | None = None
    busy: bool | None = None
    notify: bool | None = None
    sort_order: int | None = None
    default_reminders: builtins.list[int] | dict[str, builtins.list[int]] | None = None
    clear_default_reminders: bool = False


# ACT calendar.rename @api, ACT calendar.color @api, ACT calendar.toggle_visibility @api, ACT calendar.default_reminders @api,
# ACT list.rename @api, ACT list.color @api, ACT list.reorder @api
@router.patch("/calendar/collections/{calendar_id}")
async def patch_collection(calendar_id: int, body: CollectionPatch, p: Principal = Depends(scope("calendar")),
                           db: AsyncSession = Depends(get_db)):
    """`name` and `color` go to the server (PROPPATCH; a refused colour is kept in mailr and the
    collection reports `color_local: true`); the rest is mailr-only."""
    try:
        coll, _ = await _visible(db, p.user_id, p, calendar_id)
    except LookupError:
        raise ApiError(404, "calendar.calendar_not_found") from None
    payload: dict[str, Any] = {"op": "update", "calendar_id": calendar_id,
                               **body.model_dump(exclude_none=True, exclude={"clear_default_reminders"})}
    if body.clear_default_reminders:
        payload["default_reminders"] = None
    if len(payload) == 2:
        raise ApiError(400, "common.nothing_to_change")
    cls = _class_for(coll)
    renames = body.name is not None and cls == "tasks.lists"
    return await gated(db, p, cls, payload, target=coll.href, ctx={"calendar": coll.href} if cls != "tasks.lists"
                       else {"list": coll.href}, ask_by_default="renaming a task list asks by default" if renames else None,
                       preview={"title": f"Change “{_name(coll)}”", "kind": "calendar",
                                "lines": [f"{k}: {v}" for k, v in body.model_dump(exclude_none=True).items()]})


# ACT calendar.delete @api, ACT list.delete @api
@router.delete("/calendar/collections/{calendar_id}")
async def delete_collection_route(calendar_id: int, confirm: str = "", move_to: str | None = None,
                                  p: Principal = Depends(scope("calendar")), db: AsyncSession = Depends(get_db)):
    """Delete a collection on the server. `confirm` must equal its name; the saved default and
    the last calendar (or task list) cannot be deleted. `move_to` (a list URL or id) moves the
    tasks there first. There is no undo."""
    try:
        coll, _ = await _visible(db, p.user_id, p, calendar_id)
    except LookupError:
        raise ApiError(404, "calendar.calendar_not_found") from None
    if confirm != _name(coll):
        raise ApiError(400, "calendar.confirm_calendar_name")
    count = len((await db.execute(select(CalObject.id).where(CalObject.collection_id == coll.id,
                                                             CalObject.deleted_at.is_(None)))).all())
    cls = _class_for(coll)
    payload = {"op": "delete", "calendar_id": calendar_id, "confirm": confirm, "move_to": move_to}
    return await gated(db, p, cls, payload, target=coll.href,
                       ctx={"calendar": coll.href} if cls != "tasks.lists" else {"list": coll.href},
                       ask_by_default="deleting a task list asks by default",
                       preview={"title": f"Delete “{_name(coll)}”", "kind": "calendar", "danger": True,
                                "lines": [f"{count} items" + (
                                    f", tasks moved to {await describe.label_of(db, p.user_id, move_to, p) or 'another list'}"
                                    if move_to else "")]})


class ReorderIn(BaseModel):
    ids: builtins.list[int] = Field(min_length=1, max_length=200)


# ACT list.reorder
@router.post("/calendar/collections/reorder")
async def reorder_route(body: ReorderIn, p: Principal = Depends(scope("calendar")), db: AsyncSession = Depends(get_db)):
    """Set `sort_order` of these collections to their position in `ids` (mailr-only)."""
    return await gated(db, p, "tasks.lists", {"op": "reorder", "ids": body.ids}, target="order", ctx={},
                       preview={"title": "Reorder lists", "kind": "calendar", "lines": [str(i) for i in body.ids]})


# ---------------- moving an event ----------------


class EventMoveIn(BaseModel):
    calendar: str
    etag: str | None = None


# ACT event.move_calendar @api
@router.post("/calendar/events/{uid}/move")
async def move_event(uid: str, body: EventMoveIn, p: Principal = Depends(scope("calendar")),
                     db: AsyncSession = Depends(get_db), if_match: str | None = Header(None)):
    """Move a whole series into another calendar of the same account (UID kept): written to the
    target, then deleted from the source; if the delete fails, the new copy is removed."""
    await _collection_visible(db, p, body.calendar)
    from mailr.calendar import phonecal

    await phonecal.refuse_uid(db, p.user_id, uid)
    payload: dict[str, Any] = {"uid": uid, "changes": {"calendar": body.calendar}, "scope": "all"}
    if etag := _etag(if_match, body.etag):
        payload["etag"] = etag
    wctx = where_ctx(await where_is(db, p, uid), body.calendar) if p.is_token else {}
    # The card says which event and from where to where, by the calendars' names (never a URL).
    preview = await describe.event_preview(db, p, "update", uid, None, "all", {"calendar": body.calendar})
    return await _gated(db, p, "calendar.update", payload, target=uid, ctx=wctx, preview=preview)


# ---------------- export (.ics) ----------------


def _ics_response(text: str, name: str) -> Response:
    from urllib.parse import quote

    base = re.sub(r"[^A-Za-z0-9._ -]+", "", name).strip()[:80] or "calendar"
    return Response(text, media_type="text/calendar; charset=utf-8", headers={
        "Content-Disposition": f"attachment; filename=\"{base}.ics\"; filename*=UTF-8''{quote(name[:80] or base)}.ics",
        "Cache-Control": "no-store"})


def merge_ics(texts: list[str], name: str = "") -> str:
    """One VCALENDAR holding the components of every resource (events, tasks, journals), with
    each VTIMEZONE once."""
    from icalendar import Calendar

    out = ical.new_calendar()
    if name:
        out.add("x-wr-calname", name)
    zones: set[str] = set()
    for text in texts:
        try:
            c = Calendar.from_ical(text)
        except Exception:  # noqa: BLE001  a resource the parser refuses stays out
            continue
        for comp in c.subcomponents:
            if comp.name == "VTIMEZONE":
                tzid = str(comp.get("tzid", ""))
                if tzid in zones:
                    continue
                zones.add(tzid)
            if comp.name in ("VTIMEZONE", "VEVENT", "VTODO", "VJOURNAL"):
                out.add_component(comp)
    return out.to_ical().decode()


# ACT event.export_ics @api
@router.get("/calendar/collections/{calendar_id}/export")
async def export_collection(calendar_id: int, p: Principal = Depends(scope("read")),
                            db: AsyncSession = Depends(get_db)):
    """The whole collection (events and tasks) as one .ics file, for a copy before a delete or
    to import elsewhere. Only collections of accounts the caller may see."""
    try:
        coll, _ = await _visible(db, p.user_id, p, calendar_id)
    except LookupError:
        raise ApiError(404, "calendar.calendar_not_found") from None
    texts = (await db.execute(select(CalObject.ics).where(
        CalObject.collection_id == coll.id, CalObject.user_id == p.user_id, CalObject.deleted_at.is_(None))
        .order_by(CalObject.id))).scalars().all()
    name = _name(coll)
    return _ics_response(merge_ics([t for t in texts if t], name), name)


# ACT event.export_ics
@router.get("/calendar/events/{uid}/ics")
async def export_event(uid: str, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """One event (the whole series with its overrides) as an .ics file."""
    obj = await writer.find(db, p.user_id, uid, "event", p)
    if obj is None:
        raise ApiError(404, "calendar.event_not_found")
    return _ics_response(merge_ics([obj.ics]), obj.summary or "event")


# ---------------- import (.ics) ----------------

IMPORT_MAX_BYTES = 5 * 1024 * 1024
IMPORT_MAX_EVENTS = 1000


class ImportIn(BaseModel):
    calendar: str | None = None  # collection URL or calendar_id; default: the default calendar
    ics: str | None = Field(None, max_length=IMPORT_MAX_BYTES)  # the file's text
    blob: str | None = None  # a file sent to POST /uploads
    message_id: int | None = None  # an email: its calendar part, or the attachment `part`
    part: int | None = None


def split_resources(text: str, src: Any = None) -> list[tuple[str, Any]]:
    """[(uid, VCALENDAR)] of the VEVENTs in an .ics file: one resource per UID (a series with its
    overrides), METHOD dropped, with the VTIMEZONEs the file carries, so every start and end
    keeps its own zone (within the limits of calendar/zones.py, applied when it is written).
    A VEVENT without a UID gets one. `src`: the file already parsed."""
    from icalendar import Calendar

    if src is None:
        src = Calendar.from_ical(text)
    zones = list(src.walk("VTIMEZONE"))
    groups: dict[str, list[Any]] = {}
    for comp in src.walk("VEVENT"):
        uid = str(comp.get("uid") or "").strip()
        if not uid:
            uid = f"{uuid.uuid4()}@mailr"
            comp.add("uid", uid)
        groups.setdefault(uid, []).append(comp)
    out = []
    for uid, comps in groups.items():
        cal_ = ical.new_calendar()
        used = {str(getattr(c.get(k), "params", {}).get("TZID") or "") for c in comps
                for k in ("dtstart", "dtend", "recurrence-id")} - {""}
        for z in zones:
            if str(z.get("tzid")) in used:
                cal_.add_component(z)
        for c in sorted(comps, key=lambda c: c.get("recurrence-id") is not None):
            cal_.add_component(c)
        out.append((uid, cal_))
    return out


# ACT event.import_ics @api
@router.post("/calendar/import")
async def import_ics(body: ImportIn, p: Principal = Depends(scope("calendar")), db: AsyncSession = Depends(get_db)):
    """Import the events of an .ics file into a calendar: each UID becomes one resource, as the
    file wrote it (zones, rules, overrides, guests; no invitation mail is sent). A UID that is
    already in the calendars is skipped. Only the signed-in user imports."""
    if p.is_token:
        raise ApiError(403, "calendar.import_user_only")
    if sum(x is not None for x in (body.ics, body.blob, body.message_id)) != 1:
        raise HTTPException(400, "give one of ics, blob or message_id")  # i18n: internal
    if body.ics is not None:
        text_ = body.ics
    elif body.blob is not None:
        from mailr.api.mail_write import read_upload

        data, _, _ = read_upload(p.user_id, body.blob)
        if len(data) > IMPORT_MAX_BYTES:
            raise ApiError(413, "calendar.file_limit_5mb")
        text_ = data.decode("utf-8", "replace")
    else:
        from mailr.api.mail_read import load_message
        from mailr.calendar import itip
        from mailr.core.mime import get_part
        from mailr.core.store import user_store

        m = await load_message(db, p, int(body.message_id))
        raw = await (await user_store(db, p.user)).get_raw(m)
        if body.part is not None:
            try:
                data, _, _ = get_part(raw, body.part)
            except KeyError:
                raise ApiError(404, "mail.no_such_part") from None
        else:
            data = itip.find_calendar_part(raw)
            if data is None:
                raise ApiError(404, "calendar.message_no_ics")
        text_ = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
    from mailr.calendar import zones

    try:
        # Off the event loop, within a time budget.
        resources = await zones.off_loop(split_resources, text_)
    except Exception:  # noqa: BLE001
        raise ApiError(400, "calendar.not_ics") from None
    if not resources:
        raise ApiError(400, "calendar.file_no_events")
    if len(resources) > IMPORT_MAX_EVENTS:
        raise ApiError(413, "calendar.import_max_events", max=IMPORT_MAX_EVENTS)
    target = body.calendar
    if target and target.isdigit():
        try:
            coll_, _ = await _visible(db, p.user_id, p, int(target))
        except LookupError:
            raise ApiError(404, "calendar.calendar_not_found") from None
        target = coll_.href
    try:
        coll = await writer.target_collection(db, p.user_id, p, target, "event")
    except Exception as e:  # noqa: BLE001
        raise _need_calendar(e) from None
    if coll.read_only:
        raise ApiError(400, "calendar.read_only")
    imported, skipped, failed = [], [], []
    for uid, cal_ in resources:
        summary = str(next(iter(cal_.walk("VEVENT"))).get("summary") or "")
        why = expand.rule_problem(cal_)
        if why:
            failed.append({"uid": uid, "summary": summary, "detail": why})
            continue
        sp = await db.begin_nested()
        try:
            await writer.create(db, p.user_id, coll, cal_, uid)
            await sp.commit()
            imported.append({"uid": uid, "summary": summary})
        except writer.Conflict:
            await sp.rollback()
            skipped.append({"uid": uid, "summary": summary, "reason": "exists"})
        except Exception as e:  # noqa: BLE001
            await sp.rollback()
            failed.append({"uid": uid, "summary": summary, "detail": str(e)[:200]})
    from mailr.core.audit import audit

    result = {"calendar": coll.href, "calendar_id": coll.id, "imported": imported, "skipped": skipped,
              "failed": failed}
    await audit(db, p, "calendar.import", coll.href, {"events": len(resources)},
                {"imported": len(imported), "skipped": len(skipped), "failed": len(failed)})
    await db.commit()
    return result


# ---------------- search, free/busy, find a time ----------------


def _when(v: str | None, zone: tzinfo = UTC) -> datetime | None:
    """A date or time a search or find-time names; without an offset it is in `zone` (the user's)."""
    if not v:
        return None
    try:
        w = ical.parse_when(v, zone)
    except ValueError:
        raise HTTPException(400, f"{v!r} is not an ISO date or datetime") from None  # i18n: internal
    return w if isinstance(w, datetime) else datetime(w.year, w.month, w.day, tzinfo=zone)


# ACT event.search @api
@router.get("/calendar/search")
async def calendar_search(q: str = "", kind: str = Query("all", pattern="^(event|task|all)$"),
                          frm: str | None = Query(None, alias="from"), to: str | None = None,
                          calendar: list[str] | None = Query(None), limit: int = Query(50, ge=1, le=100),
                          cursor: str | None = None, p: Principal = Depends(scope("read")),
                          db: AsyncSession = Depends(get_db)):
    """Full-text and operator search over events and tasks: → {items: [{kind, rank, ...}],
    next_cursor}. An event item is its next upcoming occurrence (else the most recent one)."""
    try:
        offset = int(cursor or 0)
    except ValueError:
        raise ApiError(400, "common.invalid_cursor") from None
    try:
        zone = await tzmod.user_zone(db, p.user_id)
        out = await search.search(db, p.user_id, p, q, kind=kind, frm=_when(frm, zone), to=_when(to, zone),
                                  calendars=calendar,
                                  limit=limit, offset=offset)
    except Exception as e:  # noqa: BLE001
        raise _need_calendar(e) from None
    from mailr.calendar.routes import _with_source_email

    evs = await _with_source_email(db, p, [i for i in out["items"] if i["kind"] == "event"], "event")
    tks = await _with_source_email(db, p, [i for i in out["items"] if i["kind"] == "task"], "task")
    by = {(i["kind"], i["uid"]): i for i in evs + tks}
    out["items"] = [by.get((i["kind"], i["uid"]), i) for i in out["items"]]
    await db.commit()
    return out


@router.get("/calendar/freebusy")
async def calendar_freebusy(frm: str | None = Query(None, alias="from"), to: str | None = None,
                            calendar: list[str] | None = Query(None), p: Principal = Depends(scope("read")),
                            db: AsyncSession = Depends(get_db)):
    """Merged busy intervals (UTC) of the calendars that count toward free/busy. Tentative
    occurrences are in `tentative` and busy all-day ones in `all_day` ([start, end) dates);
    neither is merged into `busy`."""
    start, end = _range(frm, to, default_days=7, zone=await tzmod.user_zone(db, p.user_id))
    try:
        b = await freebusy.busy(db, p.user_id, p, start, end, calendar)
    except Exception as e:  # noqa: BLE001
        raise _need_calendar(e) from None
    await db.commit()
    return {"from": start.isoformat(), "to": end.isoformat(),
            "busy": [{"start": s.isoformat(), "end": e.isoformat()} for s, e in b.busy],
            "tentative": [{"start": s.isoformat(), "end": e.isoformat()} for s, e in b.tentative],
            "all_day": [{"start": s.isoformat(), "end": e.isoformat()} for s, e in b.all_day]}


class WorkingHours(BaseModel):
    start: str = Field("09:00", pattern=r"^\d{1,2}:\d{2}$")
    end: str = Field("17:00", pattern=r"^\d{1,2}:\d{2}$")
    days: builtins.list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])


class FindTimeIn(BaseModel):
    duration: int = Field(30, ge=5, le=24 * 60)
    frm: str | None = Field(None, alias="from")
    to: str | None = None
    tz: str | None = Field(None, max_length=64)
    # The asking device's clock: no slot starts before it. The server's clock when absent. The web
    # sends it so the slots agree with the days the page shows (the person's "now", not the server's).
    now: str | None = None
    working_hours: WorkingHours | None = None
    buffer_minutes: int = Field(0, ge=0, le=240)
    granularity: int = Field(15, ge=5, le=240)
    limit: int = Field(10, ge=1, le=50)
    avoid_all_day: bool = True
    calendar: builtins.list[str] | None = None
    # People in my groups who share their free/busy (calendar/household.py): their busy times count too.
    people: builtins.list[int] | None = Field(None, max_length=20)


# ACT event.find_time @api
@router.post("/calendar/find-time")
async def find_time(body: FindTimeIn, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """Free slots in my calendars inside working hours, ranked: the earliest working day first,
    then slots that leave no unusable gap beside other meetings, then the earlier time."""
    zone = tzmod.zone(body.tz) if body.tz else await tzmod.user_zone(db, p.user_id)
    if zone is None:
        raise HTTPException(400, f"unknown time zone {body.tz!r}")  # i18n: internal
    now = _when(body.now, zone) if body.now and len(body.now) > 10 else datetime.now(UTC)
    start = _when(body.frm, zone) or now
    if body.frm and len(body.frm) == 10:  # a date: from that local midnight
        d = ical.parse_when(body.frm, zone)
        start = datetime(d.year, d.month, d.day, tzinfo=zone).astimezone(UTC)
    start = max(start, now)
    gran = timedelta(minutes=body.granularity)
    if body.to:
        end = _when(body.to, zone)
        if len(body.to) == 10:
            d = ical.parse_when(body.to, zone)
            end = datetime(d.year, d.month, d.day, tzinfo=zone).astimezone(UTC)
    else:
        end = start + timedelta(days=7)  # about five working days
    if end <= start or end - start > freebusy.MAX_RANGE:
        raise ApiError(400, "calendar.range_positive_62_days")
    hours = await freebusy.working_hours(db, p.user_id, body.working_hours.model_dump() if body.working_hours else None)
    try:
        b = await freebusy.busy(db, p.user_id, p, start - timedelta(days=1), end + timedelta(days=1), body.calendar)
    except Exception as e:  # noqa: BLE001
        raise _need_calendar(e) from None
    if body.people:
        # The people chosen in Find a time (calendar/household.py).
        from mailr.calendar import household

        if p.is_token:
            raise ApiError(403, "calendar.group_freebusy_user_only")
        more_busy, more_tent = await household.merged_busy(db, p.user, body.people, start - timedelta(days=1),
                                                           end + timedelta(days=1))
        b = freebusy.Busy(freebusy.merge(b.busy + more_busy), freebusy.merge(b.tentative + more_tent), b.all_day)
    slots = freebusy.find_slots(b, start=start, end=end, zone=zone, duration=timedelta(minutes=body.duration),
                                hours=hours, buffer=timedelta(minutes=body.buffer_minutes), granularity=gran,
                                limit=body.limit, avoid_all_day=body.avoid_all_day)
    await db.commit()
    return {"slots": slots, "tz": tzmod.zone_name(zone), "working_hours": hours}
