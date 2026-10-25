"""Calendar subscriptions (calendar.subscribe): a calendar link that mailr reads on a
schedule and shows read-only, such as public holidays or a school's term dates.

A subscription is a Connector of kind "webcal" with one read-only CalCollection (href
`webcal:<connector id>`). Its events live in the same mirror tables as CalDAV events, so every
view, search and reminder reads them as it reads any calendar; nothing writes to them (writer
refuses a read-only collection) and no DAV sync touches them (the sync takes kind "caldav" only).

- The address is an https link (webcal:// is read as https://). Plain http:// only when the admin
  allows private hosts for this user (MAILR_ALLOW_PRIVATE_HOSTS) and every address of the host is
  private: a LAN feed. It passes the outbound guard (core/netguard.py) on every fetch, the body is
  capped (MAX_BYTES), and redirects are checked hop by hop under the same rules; an https link
  never redirects to http.
- The file is parsed in a thread within PARSE_BUDGET seconds, at most MAX_EVENTS events, and
  each event's VTIMEZONEs pass the limits of calendar/zones.py. The feed's own VALARMs and
  X-MAILR-* properties are dropped: the user's default reminders apply.
- A link that fails (an error, an unreadable file) waits longer before the next try: its
  interval doubles with each failure in a row, up to BACKOFF_MAX_HOURS.
- It is fetched when added, then every `refresh_hours` (6 to 168, default 12) by the worker, and
  on "Refresh now". A fetch sends If-None-Match / If-Modified-Since when the server gave an
  ETag or Last-Modified; an unchanged file costs nothing.
- Each UID becomes one resource. A UID that left the file becomes a tombstone, like a deleted
  event, so phones drop it.
- "Remove" tombstones the calendar and its events and turns the link off.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.auth import Principal, current
from mailr.core.events import record_change
from mailr.core.jobs import enqueue, handler
from mailr.db import get_db, session_scope
from mailr.i18n import ApiError
from mailr.models import CalCollection, CalObject, Connector, User

log = logging.getLogger("mailr.calendar.webcal")
router = APIRouter(tags=["calendar"])

KIND = "webcal"
MAX_BYTES = 5 * 1024 * 1024
MAX_EVENTS = 10_000
# The longest parsing one file may take, off the event loop (seconds).
PARSE_BUDGET = 30.0
BACKOFF_MAX_HOURS = 7 * 24
DEFAULT_HOURS = 12
MIN_HOURS, MAX_HOURS = 6, 168
MAX_PER_USER = 30
REDIRECTS = 5


def normalize(url: str) -> str:
    """webcal:// and webcals:// read as https://. ValueError for anything else than http(s).
    Plain http:// is checked by `check_scheme` (it needs the admin's permission and a LAN host)."""
    u = (url or "").strip()
    low = u.lower()
    if low.startswith(("webcal://", "webcals://")):
        u = "https://" + u.split("://", 1)[1]
    if not u.lower().startswith(("https://", "http://")) or len(u) > 2000:
        raise ValueError("Use a calendar link that starts with https:// or webcal://.")
    if not urlsplit(u).hostname:
        raise ValueError("That link has no server name.")
    return u


async def check_scheme(url: str, *, private_ok: bool, came_from: str | None = None) -> None:
    """https always; plain http only for a LAN feed the admin allows (private_ok and every address
    of the host private), and never as the target of a redirect from https."""
    import asyncio

    from mailr.core.netguard import UnsafeTarget, is_private_target

    parts = urlsplit(url)
    if parts.scheme.lower() == "https":
        return
    if came_from is not None and urlsplit(came_from).scheme.lower() == "https":
        raise UnsafeTarget("The calendar link redirects from https:// to plain http://.")
    if not private_ok or not await asyncio.to_thread(is_private_target, parts.hostname or ""):
        raise UnsafeTarget("Use a calendar link that starts with https:// or webcal://.")


def coll_href(conn_id: int) -> str:
    return f"webcal:{conn_id}"


async def fetch(url: str, *, private_ok: bool, etag: str | None = None, modified: str | None = None
                ) -> tuple[int, bytes, dict[str, str]]:
    """GET the link through the outbound guard. (status, body, headers); 304 has no body."""
    from mailr.core.netguard import check_url, fetch_capped, guarded_client

    headers = {"Accept": "text/calendar, */*;q=0.5", "User-Agent": "mailr calendar subscription"}
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        headers["If-Modified-Since"] = modified
    current_url = url
    previous: str | None = None
    async with guarded_client(private_ok=private_ok, timeout=20) as client:
        for _ in range(REDIRECTS + 1):
            await check_scheme(current_url, private_ok=private_ok, came_from=previous)
            await check_url(current_url, private_ok=private_ok)
            r, body = await fetch_capped(client, "GET", current_url, limit=MAX_BYTES, deadline=40, headers=headers)
            if r.is_redirect and r.headers.get("location"):
                previous, current_url = current_url, normalize(urljoin(current_url, r.headers["location"]))
                continue
            return r.status_code, body, {k.lower(): v for k, v in r.headers.items()}
    raise ValueError("the link redirects too often")


def parse(body: bytes) -> tuple[str, list[tuple[str, Any]]]:
    """(calendar name, [(uid, resource text)]). ValueError when it is not an iCalendar file.
    Synchronous and CPU-bound: callers use `parse_off_loop`. Each resource is serialized here
    (zones.clean, the feed's VALARMs and X-MAILR-* dropped), so storing it costs no parse."""
    from icalendar import Calendar

    from mailr.calendar import ical
    from mailr.calendar import routes as _routes  # noqa: F401 - routes_collections imports it first
    from mailr.calendar.routes_collections import split_resources

    if len(body) > MAX_BYTES:
        raise ValueError("The calendar file is too large.")
    text_ = body.decode("utf-8", "replace")
    if "BEGIN:VCALENDAR" not in text_.upper():
        raise ValueError("The link does not lead to a calendar file (.ics).")
    if text_.upper().count("BEGIN:VEVENT") > MAX_EVENTS:
        raise ValueError(f"The calendar has more than {MAX_EVENTS} events.")
    try:
        src = Calendar.from_ical(text_)
        name = str(src.get("x-wr-calname") or "")
        resources = split_resources(text_, src)
    except Exception:  # noqa: BLE001
        raise ValueError("The calendar file could not be read.") from None
    if len(resources) > MAX_EVENTS:
        raise ValueError(f"The calendar has more than {MAX_EVENTS} events.")
    out = []
    for uid, cal_ in resources:
        for comp in cal_.walk("VEVENT"):
            ical.strip_sender_extras(comp)
        out.append((uid, ical.serialize(cal_)))
    return name, out


async def parse_off_loop(body: bytes) -> tuple[str, list[tuple[str, Any]]]:
    """`parse` in a thread within PARSE_BUDGET: adding or refreshing a link never holds the
    event loop of the API or the worker."""
    from mailr.calendar import zones

    try:
        return await zones.off_loop(parse, body, budget=PARSE_BUDGET)
    except TimeoutError:
        raise ValueError("The calendar file took too long to read.") from None


async def _store(db: AsyncSession, coll: CalCollection, resources: list[tuple[str, Any]]) -> dict[str, int]:
    """Write the file's events to the collection: new and changed ones upserted, gone ones tombstoned.
    In bulk (mirror.upsert_many), with one change record for each kind of change: a file of
    9,000 events took about 95 s one row at a time (test audit, 13 Oct 2026)."""
    from mailr.calendar import ical, mirror
    from mailr.calendar.dav import norm_href
    from mailr.calendar.writer import _ctx, safe_name

    user_tz, me = await _ctx(db, coll.user_id)
    existing = dict((await db.execute(select(CalObject.href, CalObject.id).where(
        CalObject.collection_id == coll.id, CalObject.deleted_at.is_(None)))).all())
    items = []
    for uid, ics in resources:
        if not isinstance(ics, str):
            ics = ical.serialize(ics)
        items.append((norm_href(f"{coll.href}/{safe_name(uid)}.ics"), hashlib.sha256(ics.encode()).hexdigest()[:32], ics))
    seen = {h for h, _, _ in items}
    changed = await mirror.upsert_many(db, coll, items, user_tz=user_tz, me=me)
    gone = [i for h, i in existing.items() if h not in seen]
    if gone:
        await mirror.soft_delete_many(db, gone)
    if changed:
        await record_change(db, coll.user_id, "event", changed)
    if gone:
        await record_change(db, coll.user_id, "event", gone, op="delete")
    return {"changed": len(changed), "removed": len(gone)}


async def subscribe(db: AsyncSession, user: User, url: str, name: str = "", color: str | None = None,
                    refresh_hours: int | None = None) -> tuple[Connector, CalCollection, dict[str, int]]:
    from mailr.calendar import mirror
    from mailr.core.netguard import UnsafeTarget, private_ok_for

    url = normalize(url)
    private_ok = private_ok_for(user)
    try:
        await check_scheme(url, private_ok=private_ok)
    except UnsafeTarget as e:
        raise ValueError(str(e)) from None
    hours = DEFAULT_HOURS if refresh_hours is None else int(refresh_hours)
    if not MIN_HOURS <= hours <= MAX_HOURS:
        raise ValueError(f"Refresh every {MIN_HOURS} to {MAX_HOURS} hours.")
    n = len((await db.execute(select(Connector.id).where(Connector.user_id == user.id, Connector.kind == KIND,
                                                         Connector.enabled.is_(True)))).all())
    if n >= MAX_PER_USER:
        raise ValueError(f"At most {MAX_PER_USER} calendar links.")
    try:
        status, body, headers = await fetch(url, private_ok=private_ok)
    except UnsafeTarget as e:
        raise ValueError(str(e)) from None
    except (TimeoutError, OSError) as e:
        raise ValueError("The calendar link could not be reached.") from e
    except Exception as e:  # noqa: BLE001 - httpx errors, too large
        raise ValueError(f"The calendar link could not be read ({type(e).__name__}).") from None
    if status != 200:
        raise ValueError(f"The calendar link answered with an error (HTTP {status}).")
    file_name, resources = await parse_off_loop(body)
    title = (name or file_name or urlsplit(url).hostname or "Calendar").strip()[:200]
    conn = Connector(user_id=user.id, kind=KIND, name=title, mode="import", data_class="normal",
                     config={"url": url, "refresh_hours": hours}, state={}, status="idle")
    db.add(conn)
    await db.flush()
    coll = CalCollection(user_id=user.id, connector_id=conn.id, href=coll_href(conn.id), displayname=title,
                         description="", color=color or None, components=["VEVENT"], read_only=True, busy=False,
                         notify=False)
    db.add(coll)
    await db.flush()
    counts = await _store(db, coll, resources)
    now = datetime.now(UTC)
    coll.last_sync_at = coll.last_full_sync_at = now
    coll.sync_status = "idle"
    conn.last_sync_at = now
    conn.state = {"etag": headers.get("etag"), "modified": headers.get("last-modified")}
    await mirror.mark_collection_rev(db, coll)
    await record_change(db, user.id, "calendar", coll.id)
    return conn, coll, counts


async def refresh(conn_id: int) -> dict[str, Any]:
    """Fetch one subscription again and apply what changed."""
    from mailr.calendar import mirror
    from mailr.core.netguard import UnsafeTarget, private_ok_for

    async with session_scope() as db:
        conn = await db.get(Connector, conn_id)
        if conn is None or conn.kind != KIND or not conn.enabled:
            return {"skipped": True}
        user = await db.get(User, conn.user_id)
        url = (conn.config or {}).get("url") or ""
        st = dict(conn.state or {})
        coll = (await db.execute(select(CalCollection).where(CalCollection.connector_id == conn.id,
                                                             CalCollection.deleted_at.is_(None)))).scalar_one_or_none()
        if coll is None:
            return {"skipped": True}
        coll_id = coll.id
        private_ok = private_ok_for(user)
    now = datetime.now(UTC)
    try:
        status, body, headers = await fetch(url, private_ok=private_ok, etag=st.get("etag"), modified=st.get("modified"))
        if status == 304:
            result: dict[str, Any] = {"unchanged": True}
        elif status != 200:
            raise ValueError(f"The calendar link answered with an error (HTTP {status}).")
        else:
            _, resources = await parse_off_loop(body)
            result = {"resources": resources, "headers": headers}
        error = ""
    except (UnsafeTarget, ValueError) as e:
        result, error = {}, str(e)[:300]
    except Exception as e:  # noqa: BLE001
        result, error = {}, f"The calendar link could not be reached ({type(e).__name__})."
    async with session_scope() as db:
        conn = await db.get(Connector, conn_id)
        coll = await db.get(CalCollection, coll_id)
        counts: dict[str, int] = {}
        if "resources" in result:
            counts = await _store(db, coll, result["resources"])
            conn.state = {"etag": result["headers"].get("etag"), "modified": result["headers"].get("last-modified")}
        coll.last_sync_at = conn.last_sync_at = now
        if error:
            coll.sync_status, coll.sync_error = "error", error
            coll.fail_count = (coll.fail_count or 0) + 1
            conn.status, conn.status_detail = "error", error
            conn.state = {**(conn.state or {}), "fails": int((conn.state or {}).get("fails") or 0) + 1}
        else:
            coll.sync_status, coll.sync_error, coll.fail_count = "idle", "", 0
            coll.last_full_sync_at = now
            conn.status, conn.status_detail = "idle", ""
            conn.state = {k: v for k, v in (conn.state or {}).items() if k != "fails"}
        await mirror.mark_collection_rev(db, coll)
        await record_change(db, conn.user_id, "calendar", coll.id)
    return {"error": error or None, **counts}


@handler("calendar.webcal_refresh")
async def refresh_job(payload: dict[str, Any], ctx) -> None:
    await refresh(int(payload["connector_id"]))


async def due_refreshes() -> None:
    """Subscriptions not fetched for their refresh interval. A failing one waits longer: at least a
    day, doubled for each failure in a row, up to BACKOFF_MAX_HOURS."""
    now = datetime.now(UTC)
    async with session_scope() as db:
        rows = (await db.execute(select(Connector).where(Connector.kind == KIND, Connector.enabled.is_(True)))
                ).scalars().all()
        for c in rows:
            hours = int((c.config or {}).get("refresh_hours") or DEFAULT_HOURS)
            if c.status == "error":
                fails = max(1, int((c.state or {}).get("fails") or 1))
                hours = min(max(hours, 24) * 2 ** min(fails - 1, 8), BACKOFF_MAX_HOURS)
            if c.last_sync_at is None or c.last_sync_at < now - timedelta(hours=hours):
                await enqueue(db, "calendar.webcal_refresh", {"connector_id": c.id}, user_id=c.user_id,
                              dedupe_key=f"webcal:{c.id}")


async def remove(db: AsyncSession, conn: Connector) -> None:
    from mailr.calendar import mirror

    for coll in (await db.execute(select(CalCollection).where(CalCollection.connector_id == conn.id,
                                                              CalCollection.deleted_at.is_(None)))).scalars().all():
        gone = await mirror.soft_delete_collection(db, coll)
        ids = [i for k, i in gone if k == "event"]
        if ids:
            await record_change(db, conn.user_id, "event", ids, op="delete")
        await record_change(db, conn.user_id, "calendar", coll.id, op="delete")
    conn.enabled = False
    conn.config = {**(conn.config or {}), "removed_at": datetime.now(UTC).isoformat()}


# ---------------- routes ----------------


class SubscribeIn(BaseModel):
    url: str = Field(max_length=2000)
    name: str = Field("", max_length=200)
    color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    refresh_hours: int | None = None


def _user_only(p: Principal) -> None:
    if p.is_token:
        raise ApiError(403, "calendar.subscribe_user_only")


async def _own(db: AsyncSession, p: Principal, cid: int) -> Connector:
    c = await db.get(Connector, cid)
    if c is None or c.user_id != p.user_id or c.kind != KIND or not c.enabled:
        raise ApiError(404, "calendar.subscription_not_found")
    return c


# ACT calendar.subscribe @api
@router.post("/calendar/subscriptions")
async def add_subscription(body: SubscribeIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Subscribe to a calendar link: fetched now, then on a schedule; shown read-only."""
    _user_only(p)
    from mailr.calendar import mirror
    from mailr.core.audit import audit

    try:
        conn, coll, counts = await subscribe(db, p.user, body.url, body.name, body.color, body.refresh_hours)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    await audit(db, p, "calendar.subscribe", urlsplit(conn.config["url"]).hostname or "", {"name": conn.name})
    await db.commit()
    return {"connector_id": conn.id, "events": counts["changed"],
            "calendar": mirror.collection_out(coll, conn, await mirror.kind_overrides(db, p.user_id))}


@router.get("/calendar/subscriptions")
async def list_subscriptions(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _user_only(p)
    rows = (await db.execute(select(Connector, CalCollection).join(
        CalCollection, CalCollection.connector_id == Connector.id).where(
        Connector.user_id == p.user_id, Connector.kind == KIND, Connector.enabled.is_(True),
        CalCollection.deleted_at.is_(None)).order_by(Connector.id))).all()
    return {"items": [{"connector_id": c.id, "calendar_id": coll.id, "name": coll.displayname, "url": (c.config or {}).get("url"),
                       "refresh_hours": (c.config or {}).get("refresh_hours"),
                       "last_sync_at": coll.last_sync_at.isoformat() if coll.last_sync_at else None,
                       "error": coll.sync_error or None} for c, coll in rows]}


class SubscriptionPatch(BaseModel):
    refresh_hours: int | None = None
    name: str | None = Field(None, max_length=200)


@router.patch("/calendar/subscriptions/{cid}")
async def patch_subscription(cid: int, body: SubscriptionPatch, p: Principal = Depends(current),
                             db: AsyncSession = Depends(get_db)):
    _user_only(p)
    c = await _own(db, p, cid)
    if body.refresh_hours is not None:
        if not MIN_HOURS <= body.refresh_hours <= MAX_HOURS:
            raise ApiError(400, "calendar.refresh_hours", min=MIN_HOURS, max=MAX_HOURS)
        c.config = {**(c.config or {}), "refresh_hours": body.refresh_hours}
    if body.name is not None and body.name.strip():
        c.name = body.name.strip()[:200]
        from mailr.calendar import mirror

        for coll in (await db.execute(select(CalCollection).where(CalCollection.connector_id == c.id))).scalars():
            coll.displayname = c.name
            await mirror.mark_collection_rev(db, coll)
            await record_change(db, p.user_id, "calendar", coll.id)
    await db.commit()
    return {"ok": True}


@router.post("/calendar/subscriptions/{cid}/refresh")
async def refresh_subscription(cid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _user_only(p)
    await _own(db, p, cid)
    await db.commit()
    return await refresh(cid)


# ACT calendar.unsubscribe @api
@router.delete("/calendar/subscriptions/{cid}")
async def remove_subscription(cid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _user_only(p)
    c = await _own(db, p, cid)
    await remove(db, c)
    await db.commit()
    return {"ok": True}
