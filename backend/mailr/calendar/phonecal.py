"""Phone calendars: busy times, and for some calendars titles and places, that the Android app
sends from calendars on the phone (docs/design/device-calendars.md, Phase 2 and details mode).

Some calendars cannot reach mailr directly (a work Microsoft 365 calendar that blocks third-party
sign-in), but the phone already syncs them. Per phone calendar the user picks one of four choices
on the phone: Off, Show on this phone, Show and send busy times, Show and send details.

- "Show and send busy times" (`details` false or absent, the default): the app sends start, end
  and all-day for each block, nothing else. An entry with a title or place is refused (422), so the
  busy-only promise holds in the schema itself.
- "Show and send details" (`details: true`, approved by the owner 28 Oct 2026): each entry may also
  carry its `title` and `location` (each at most MAX_TEXT characters). Guests, descriptions, notes,
  the organizer and the phone's event ids are never accepted: the schema has no field for them and
  refuses unknown fields (422).

Storage reuses the calendar mirror, as calendar links do (calendar/webcal.py):

- One Connector of kind "phone" per phone calendar (config: `key`, `device`, `details`), with one
  read-only CalCollection (href `phone:<key>`; busy on, reminders off). Each block is one CalObject:
  a single VEVENT, opaque, marked "no reminders", with no guests, description or link. A busy-only
  block is titled "Busy" in the user's language. A block with details has the phone's title as its
  SUMMARY ("Busy" when the title is empty) and the phone's place as its LOCATION, and its UID ends
  in DETAILS_SUFFIX, so every reader can tell it apart from a busy-only block without another
  lookup (`has_details`). The agenda, free/busy, find-a-time, search, the calendar views and
  Ambrose's calendar tools read them as they read any calendar (mirror.visible_connectors with
  `subscriptions=True` takes kind "phone"); nothing writes to them (the collection is read-only and
  the writer refuses it with `calendar.phone_read_only`) and no DAV sync touches them (the sync
  takes kind "caldav" only).
- They exist only in mailr's database. They are never written to the mail store's calendar server,
  so the DAV gateway does not serve them to phone or desktop calendar apps; the calendar backup
  copies the "caldav" accounts only; household free/busy reads the built-in account only; and
  sharing is offered for the built-in account's calendars only.
- Switching a calendar back to busy times: a snapshot with `details` false rewrites every stored
  block of the key that still carries a title or place, outside the window too, as a busy-only
  block; the tombstones of removed blocks keep no text at any time. After that PUT no title or
  place of that calendar is left in mailr's database.

Outbound data policy (docs/design/outbound-data-policy.md): the connector's data class is
"normal". A busy-only block carries no content. A block with details carries the title and place
of one of the owner's own calendar entries, the same kind of text as the events of the owner's
other calendars, which are "normal" unless their account is set to local-only; the policy gives
phone calendars no class of their own. The person controls what leaves the phone per calendar:
"Show and send busy times" keeps titles and places on the phone. So the hosted-model rules for
ordinary calendar events apply: Ambrose reads a block with details like any read-only event (its
title, place and time, marked as from a phone calendar), and a busy-only block as
"Busy (<calendar>)" and its time.

PUT is a snapshot inside a window: the stored blocks of that calendar that overlap the window are
replaced by the given ones; blocks outside it are kept, up to KEEP_HISTORY_DAYS before the window
(older ones are dropped, so a calendar does not grow without end). The same body twice leaves the
same state (each block's text, and so its ETag, depends only on the body).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr import i18n
from mailr.core.auth import Principal, scope
from mailr.core.events import record_change
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import CalCollection, CalObject, Connector, User

log = logging.getLogger("mailr.calendar.phonecal")
router = APIRouter(tags=["calendar"])

KIND = "phone"
HREF_PREFIX = "phone:"
MAX_ENTRIES = 5000
MAX_WINDOW_DAYS = 400
MAX_PER_USER = 50
# After this long without a snapshot a phone calendar is marked stale (still shown).
STALE_AFTER = timedelta(hours=24)
# Blocks that ended this long before the window's start are dropped at the next snapshot.
KEEP_HISTORY_DAYS = 366
KEY_PATTERN = r"^[0-9a-f]{32}$"
BUSY_ID_PATTERN = r"^[0-9a-f]{24}$"
# The longest title or place a block with details may carry (characters, after spaces are folded).
MAX_TEXT = 300
# The UID of a block with a title or place ends in this; a busy-only block's ends in "@mailr".
DETAILS_SUFFIX = "-d@mailr"
# The permission class of a token's writes (core/permissions.py): allowed by default, with the
# calendar scope. Times leave the phone, plus titles and places for a calendar set to send
# details, and the app sends them every 30 minutes.
ACTION = "calendar.phone_sync"


def coll_href(key: str) -> str:
    return f"{HREF_PREFIX}{key}"


def is_phone_href(href: str | None) -> bool:
    return bool(href) and str(href).startswith(HREF_PREFIX)


def is_phone(coll: CalCollection | None) -> bool:
    return coll is not None and is_phone_href(coll.href)


def has_details(uid: str | None) -> bool:
    """Whether a stored block carries the phone's title or place (its UID says so; see `_uid`)."""
    return bool(uid) and str(uid).startswith("phone-") and str(uid).endswith(DETAILS_SUFFIX)


def busy_title(user: Any) -> str:
    """"Busy" in the user's language (the setting, else the browser's while they ask, else English)."""
    return i18n.t("calendar_phone.busy", i18n.user_language(user))


def model_title(calendar: str | None) -> str:
    """What Ambrose reads for a block: "Busy (Calendar name)"."""
    return f"Busy ({calendar})" if calendar else "Busy"


def stale(synced_at: datetime | None, now: datetime | None = None) -> bool:
    return synced_at is None or (now or datetime.now(UTC)) - synced_at > STALE_AFTER


def info(coll: CalCollection, conn: Connector | None) -> dict[str, Any]:
    """The `phone` field of a calendar in the API (mirror.collection_out)."""
    cfg = (conn.config or {}) if conn is not None else {}
    synced = coll.last_sync_at
    return {"key": cfg.get("key") or coll.href[len(HREF_PREFIX):], "device": cfg.get("device") or "",
            "synced_at": synced.isoformat() if synced else None, "stale": stale(synced),
            "details": bool(cfg.get("details"))}


# ---------------- the request ----------------


def _utc(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError("times need a zone offset (UTC, ending in Z)")
    return v.astimezone(UTC)


def _midnight(v: datetime) -> bool:
    return v.time() == time(0, 0)


def _fold(v: str | None) -> str:
    """One line of plain text: control characters and runs of white space become one space."""
    return " ".join("".join(c if c.isprintable() else " " for c in str(v or "")).split())


class BusyIn(BaseModel):
    """One busy block: its id and times, and with details its title and place."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=BUSY_ID_PATTERN)
    start: datetime
    end: datetime
    all_day: bool = False
    # Only with `details: true` on the snapshot (SnapshotIn refuses them otherwise).
    title: str | None = None
    location: str | None = None

    @field_validator("title", "location")
    @classmethod
    def _text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = _fold(v)
        if len(v) > MAX_TEXT:
            raise ValueError(f"at most {MAX_TEXT} characters")
        return v

    @model_validator(mode="after")
    def _times(self) -> BusyIn:
        self.start, self.end = _utc(self.start), _utc(self.end)
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if self.all_day and not (_midnight(self.start) and _midnight(self.end)):
            raise ValueError("an all-day block starts and ends at midnight UTC")
        if self.end - self.start > timedelta(days=MAX_WINDOW_DAYS):
            raise ValueError(f"a block is at most {MAX_WINDOW_DAYS} days long")
        return self


class SnapshotIn(BaseModel):
    """PUT /calendar/phone-calendars/{key}. A block's title and place are accepted only with
    `details: true`; there is no field for guests, a description, notes, the organizer or the
    phone's event id, and unknown fields are refused (422)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    device: str = Field("", max_length=100)
    window_start: datetime
    window_end: datetime
    busy: list[BusyIn] = Field(default_factory=list, max_length=MAX_ENTRIES)
    # "Show and send details" on the phone: blocks may carry their title and place.
    details: bool = False

    @field_validator("name", "device")
    @classmethod
    def _plain(cls, v: str) -> str:
        return " ".join(str(v).split())

    @model_validator(mode="after")
    def _window(self) -> SnapshotIn:
        self.window_start, self.window_end = _utc(self.window_start), _utc(self.window_end)
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        if self.window_end - self.window_start > timedelta(days=MAX_WINDOW_DAYS):
            raise ValueError(f"the window is at most {MAX_WINDOW_DAYS} days")
        ids = [b.id for b in self.busy]
        if len(set(ids)) != len(ids):
            raise ValueError("busy ids must be unique")
        for b in self.busy:
            if not (b.start < self.window_end and b.end > self.window_start):
                raise ValueError("every busy block must overlap the window")
            if not self.details and (b.title or b.location):
                raise ValueError("a title or place needs details: true")
        return self


# ---------------- storage ----------------


def _uid(key: str, busy_id: str, details: bool = False) -> str:
    """A busy-only block's UID ends in "@mailr", a block with a title or place in DETAILS_SUFFIX."""
    return f"phone-{key}-{busy_id}{DETAILS_SUFFIX if details else '@mailr'}"


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", " ")


def _ics(key: str, busy_id: str, start: datetime, end: datetime, all_day: bool, busy: str,
         title: str = "", location: str = "") -> str:
    """The block as an iCalendar resource. DTSTAMP is the start, so the text (and its ETag)
    depends on the block alone and a repeated snapshot changes nothing. `busy` is the title of a
    block without one ("Busy" in the user's language)."""
    def utc(v: datetime) -> str:
        return v.strftime("%Y%m%dT%H%M%SZ")

    if all_day:
        when = [f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{end.strftime('%Y%m%d')}"]
    else:
        when = [f"DTSTART:{utc(start)}", f"DTEND:{utc(end)}"]
    details = bool(title or location)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//mailr//phone busy times//EN", "BEGIN:VEVENT",
             f"UID:{_uid(key, busy_id, details)}", f"DTSTAMP:{utc(start)}", *when, f"SUMMARY:{_esc(title or busy)}",
             *([f"LOCATION:{_esc(location)}"] if location else []),
             "TRANSP:OPAQUE", "X-MAILR-NO-REMINDER:TRUE", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def _item(coll: CalCollection, ics: str, busy_id: str) -> tuple[str, str, str]:
    from mailr.calendar.dav import norm_href

    return (norm_href(f"{coll.href}/{busy_id}.ics"), hashlib.sha256(ics.encode()).hexdigest()[:32], ics)


def _span(o: Any) -> tuple[datetime, datetime] | None:
    """A stored block's [start, end) in UTC; an all-day block's dates read as UTC midnights, as the
    phone sends them."""
    if o.all_day and o.start_date is not None:
        end = o.end_date or o.start_date + timedelta(days=1)
        return (datetime.combine(o.start_date, time(0), UTC), datetime.combine(end, time(0), UTC))
    if o.start_at is None:
        return None
    return o.start_at, o.end_at or o.start_at


async def _lock(db: AsyncSession, user_id: int, key: str) -> None:
    """One snapshot of a calendar at a time (the app may retry while one is still running)."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"phonecal:{user_id}:{key}"})


async def find(db: AsyncSession, user_id: int, key: str) -> tuple[Connector, CalCollection] | None:
    conn = (await db.execute(select(Connector).where(
        Connector.user_id == user_id, Connector.kind == KIND, Connector.enabled.is_(True),
        Connector.config["key"].astext == key).order_by(Connector.id.desc()).limit(1))).scalar_one_or_none()
    if conn is None:
        return None
    coll = (await db.execute(select(CalCollection).where(
        CalCollection.connector_id == conn.id, CalCollection.deleted_at.is_(None)))).scalar_one_or_none()
    return (conn, coll) if coll is not None else None


async def _create(db: AsyncSession, user: User, key: str, body: SnapshotIn) -> tuple[Connector, CalCollection]:
    n = len((await db.execute(select(Connector.id).where(
        Connector.user_id == user.id, Connector.kind == KIND, Connector.enabled.is_(True)))).all())
    if n >= MAX_PER_USER:
        raise ApiError(400, "calendar.phone_limit", max=MAX_PER_USER)
    conn = Connector(user_id=user.id, kind=KIND, name=body.name, mode="import", data_class="normal",
                     config={"key": key, "device": body.device}, state={}, status="idle")
    db.add(conn)
    await db.flush()
    # Reminders off (notify) and counted as busy; read-only: only the phone changes it.
    coll = CalCollection(user_id=user.id, connector_id=conn.id, href=coll_href(key), displayname=body.name,
                         description="", color=body.color, components=["VEVENT"], read_only=True, busy=True,
                         notify=False)
    db.add(coll)
    await db.flush()
    await record_change(db, user.id, "calendar", coll.id)
    return conn, coll


async def store_snapshot(db: AsyncSession, user: User, key: str, body: SnapshotIn) -> dict[str, Any]:
    from mailr.calendar import mirror
    from mailr.calendar.writer import _ctx

    await _lock(db, user.id, key)
    got = await find(db, user.id, key)
    conn, coll = got if got is not None else await _create(db, user, key, body)
    busy = busy_title(user)
    items = []
    for b in body.busy:
        ics = _ics(key, b.id, b.start, b.end, b.all_day, busy, b.title or "", b.location or "")
        items.append(_item(coll, ics, b.id))
    wanted = {h for h, _, _ in items}
    rows = (await db.execute(select(
        CalObject.id, CalObject.href, CalObject.uid, CalObject.location, CalObject.all_day, CalObject.start_at,
        CalObject.end_at, CalObject.start_date, CalObject.end_date).where(
        CalObject.collection_id == coll.id, CalObject.deleted_at.is_(None)))).all()
    oldest = body.window_start - timedelta(days=KEEP_HISTORY_DAYS)
    gone, scrubbed = [], 0
    for r in rows:
        if r.href in wanted:
            continue
        span = _span(r)
        if span is None or (span[0] < body.window_end and span[1] > body.window_start) or span[1] < oldest:
            gone.append(r.id)
        elif not body.details and (has_details(r.uid) or r.location):
            # Busy times only now: a kept block outside the window loses its title and place.
            busy_id = r.href.rsplit("/", 1)[-1].removesuffix(".ics")
            items.append(_item(coll, _ics(key, busy_id, span[0], span[1], bool(r.all_day), busy), busy_id))
            scrubbed += 1
    user_tz, me = await _ctx(db, user.id)
    changed = await mirror.upsert_many(db, coll, items, user_tz=user_tz, me=me) if items else []
    if gone:
        await mirror.soft_delete_many(db, gone)
        await record_change(db, user.id, "event", gone, op="delete")
    # A tombstone keeps no text (the delta feed needs its id and href only), so a title or place
    # sent earlier does not stay behind in a removed block.
    await db.execute(text(
        "UPDATE cal_objects SET ics = '', summary = '', location = '', description = '' "
        "WHERE collection_id = :c AND deleted_at IS NOT NULL "
        "AND (ics <> '' OR summary <> '' OR location <> '' OR description <> '')"), {"c": coll.id})
    if changed:
        await record_change(db, user.id, "event", changed)
    now = datetime.now(UTC)
    cfg = conn.config or {}
    meta_changed = (coll.displayname != body.name or coll.color != body.color or cfg.get("device") != body.device
                    or bool(cfg.get("details")) != body.details)
    coll.displayname, coll.color = body.name, body.color
    conn.name = body.name
    conn.config = {**cfg, "key": key, "device": body.device, "details": body.details}
    coll.last_sync_at = coll.last_full_sync_at = conn.last_sync_at = now
    coll.sync_status, coll.sync_error = "idle", ""
    conn.status, conn.status_detail = "idle", ""
    # The calendar's revision moves on every snapshot: its freshness (synced_at) is part of it.
    await mirror.mark_collection_rev(db, coll)
    await record_change(db, user.id, "calendar", coll.id)
    log.info("phone calendar %s of user %s: %d blocks, %d changed, %d removed, %d cleared of details%s%s",
             coll.id, user.id, len(body.busy), len(changed), len(gone), scrubbed,
             " (details)" if body.details else "", " (name, colour, device or details changed)" if meta_changed else "")
    return {"stored": len(body.busy), "synced_at": now.isoformat()}


async def remove(db: AsyncSession, user_id: int, key: str) -> bool:
    """The calendar and its blocks leave mailr. The rows become tombstones for the delta feed
    (calendar/mirror.py), with their text and times cleared; the tombstones are purged later."""
    from mailr.calendar import mirror

    await _lock(db, user_id, key)
    got = await find(db, user_id, key)
    if got is None:
        return False
    conn, coll = got
    removed = await mirror.soft_delete_collection(db, coll)
    ids = [i for _, i in removed]
    if ids:
        await db.execute(text(
            "UPDATE cal_objects SET ics = '', summary = '', location = '', description = '', start_at = NULL, "
            "end_at = NULL, start_date = NULL, end_date = NULL WHERE id = ANY(:ids)"), {"ids": ids})
        await record_change(db, user_id, "event", ids, op="delete")
    await record_change(db, user_id, "calendar", coll.id, op="delete")
    conn.enabled = False
    conn.config = {"removed_at": datetime.now(UTC).isoformat()}
    from mailr.core import usersettings

    await usersettings.patch_in(db, user_id, "collection_prefs", remove=[coll.href])
    return True


async def list_for(db: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    from sqlalchemy import func

    rows = (await db.execute(select(Connector, CalCollection).join(
        CalCollection, CalCollection.connector_id == Connector.id).where(
        Connector.user_id == user_id, Connector.kind == KIND, Connector.enabled.is_(True),
        CalCollection.deleted_at.is_(None)).order_by(Connector.id))).all()
    counts = dict((await db.execute(select(CalObject.collection_id, func.count()).where(
        CalObject.collection_id.in_([c.id for _, c in rows]), CalObject.deleted_at.is_(None))
        .group_by(CalObject.collection_id))).all()) if rows else {}
    out = []
    for conn, coll in rows:
        meta = info(coll, conn)
        out.append({"key": meta["key"], "name": coll.displayname, "color": coll.color, "device": meta["device"],
                    "synced_at": meta["synced_at"], "count": int(counts.get(coll.id, 0)), "stale": meta["stale"],
                    "details": meta["details"], "calendar_id": coll.id, "url": coll.href})
    return out


async def refuse_uid(db: AsyncSession, user_id: int, uid: str) -> None:
    """Refuse a change to a busy block before it is gated or queued for approval (ApiError 409)."""
    if not uid or not uid.startswith("phone-"):
        return
    row = (await db.execute(select(CalCollection).join(CalObject, CalObject.collection_id == CalCollection.id).where(
        CalObject.user_id == user_id, CalObject.uid == uid, CalObject.deleted_at.is_(None)).limit(1))
    ).scalar_one_or_none()
    if row is not None and is_phone(row):
        raise read_only_error(row)


def read_only_error(coll: CalCollection) -> ApiError:
    return ApiError(409, "calendar.phone_read_only", name=coll.displayname or "")


# ---------------- routes ----------------


Key = Path(pattern=KEY_PATTERN, description="The phone calendar's key: 32 lowercase hex characters.")


async def _gate(db: AsyncSession, p: Principal, key: str, payload: dict[str, Any]) -> None:
    """A token's write passes the permission class (allowed by default; a rule may ask or deny)."""
    if not p.is_token:
        return
    from mailr.core import permissions

    decision = await permissions.check(db, permissions.Actor.from_principal(p), ACTION, target=key, ctx={},
                                       payload=payload, preview={"title": "Phone calendar busy times"
                                                                 + (", titles and places" if payload.get("details") else ""),
                                                                 "kind": "calendar", "lines": [payload.get("name") or key]})
    permissions.raise_for(decision)
    if isinstance(decision, permissions.Pending):
        raise ApiError(403, "calendar.phone_needs_allow")


# ACT calendar.phone_sync @api
@router.put("/calendar/phone-calendars/{key}")
async def put_phone_calendar(body: SnapshotIn, key: str = Key, p: Principal = Depends(scope("calendar")),
                             db: AsyncSession = Depends(get_db)):
    """Replace a phone calendar's busy times inside the window (a snapshot); blocks outside it stay.
    With `details` false, no title or place of this calendar is kept, outside the window either."""
    await _gate(db, p, key, {"name": body.name, "count": len(body.busy), "details": body.details})
    out = await store_snapshot(db, p.user, key, body)
    if p.is_token:
        from mailr.core.audit import audit

        await audit(db, p, ACTION, key, {"name": body.name, "count": len(body.busy), "details": body.details}, out)
    await db.commit()
    return out


# ACT calendar.phone_list @api
@router.get("/calendar/phone-calendars")
async def list_phone_calendars(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """The user's phone calendars: key, name, colour, device, synced_at, count, stale and details
    (true when the phone sends titles and places too)."""
    return await list_for(db, p.user_id)


# ACT calendar.phone_remove @api
@router.delete("/calendar/phone-calendars/{key}")
async def delete_phone_calendar(key: str = Key, p: Principal = Depends(scope("calendar")),
                                db: AsyncSession = Depends(get_db)):
    """Remove a phone calendar and all its busy times. Removing one that is not there is not an error."""
    await _gate(db, p, key, {"op": "delete"})
    removed = await remove(db, p.user_id, key)
    if p.is_token:
        from mailr.core.audit import audit

        await audit(db, p, ACTION, key, {"op": "delete"}, {"removed": removed})
    await db.commit()
    return {"ok": True, "removed": removed}
