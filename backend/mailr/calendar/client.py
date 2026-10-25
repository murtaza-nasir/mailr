"""Calendar and tasks for a user's CalDAV connectors: the facade the routes, the agent and the
RSVP code call.

A calendar account is a Connector row with kind "caldav": config {url, username,
default_calendar?, default_tasklist?}, secrets {password}. Reads come from the Postgres mirror
(mirror.py, kept current by sync.py); writes go through to CalDAV with If-Match (writer.py).
Accounts are tested and set up with the same pinned httpx client (dav.DavClient) as sync.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import ical, mirror, writer
from mailr.calendar import tz as tzmod
from mailr.calendar.describe import calendar_name
from mailr.calendar.writer import CalendarNotConnected, Conflict  # noqa: F401  (re-exported)
from mailr.core.crypto import connector_context, decrypt_for_user
from mailr.models import Connector, User

log = logging.getLogger("mailr.calendar")


@dataclass
class Account:
    row: Connector
    password: str


async def accounts(db: AsyncSession, user_id: int, p=None) -> list[Account]:
    """Calendar accounts; with a principal, only those it may see (token connector limits,
    local-only accounts hidden from non-local tokens)."""
    rows = await mirror.visible_connectors(db, user_id, p)
    if not rows:
        return []
    user = await db.get(User, user_id)
    out = []
    for r in rows:
        secrets = decrypt_for_user(user.id, user.data_key_wrapped, r.secrets_enc,
                                   context=connector_context(r.id)) or {}
        out.append(Account(r, secrets.get("password", "")))
    return out


async def has_calendar(db: AsyncSession, user_id: int, p=None) -> bool:
    return bool(await mirror.visible_connectors(db, user_id, p))


async def has_any_calendar(db: AsyncSession, user_id: int, p=None) -> bool:
    """For views that only read: a calendar account or a calendar link (calendar/webcal.py)."""
    return bool(await mirror.visible_connectors(db, user_id, p, subscriptions=True))


# ---------------- synchronous helpers (run in threads) ----------------


async def discover_calendars(url: str, username: str, password: str, *, private_ok: bool | None,
                             create_default: bool = False, connector_id: int = 0) -> list[dict[str, Any]]:
    """Sign in and list the calendars (dav.DavClient: pinned, credentials kept to the account's
    own origin). With create_default, an empty account gets one calendar for events and tasks,
    so the agenda and task list work straight away. Raises dav.DavError on failure."""
    from mailr.calendar import dav

    async with dav.DavClient(url, username, password, private_ok=private_ok) as c:
        home = await c.home()
        cols = await c.collections(home)
        if not cols and create_default:
            await c.mkcalendar(home, "mailr", "mailr", ["VEVENT", "VTODO"])
            cols = await c.collections(home)
    return [{"connector_id": connector_id, "url": col.href,
             "name": calendar_name(col.displayname, col.href),
             "events": "VEVENT" in col.components, "tasks": "VTODO" in col.components} for col in cols]


def setup_error(e: BaseException) -> str:
    """A fixed, user-safe description of a failed sign-in (never the server's response text)."""
    from mailr.calendar import dav
    from mailr.core.netguard import UnsafeTarget

    if isinstance(e, UnsafeTarget):
        return str(e)
    if isinstance(e, dav.Unauthorized):
        return "the server refused the user name or password"
    if isinstance(e, dav.NotFound):
        return "no calendar service at this address"
    if isinstance(e, dav.DavError):
        msg = str(e)
        if msg.startswith(("use https://", "only http", "the URL")) or "private address" in msg \
                or "infrastructure" in msg or "local or reserved" in msg or "cannot resolve" in msg:
            return msg
        return f"the server answered with an error (HTTP {e.status})" if e.status else "the server could not be reached"
    return "the server could not be reached"


async def collections(db: AsyncSession, user_id: int, *, refresh: bool = False, p=None) -> list[dict[str, Any]]:
    """Collections from the mirror (refresh=True syncs the accounts first). An account whose
    first sync failed shows as {"connector_id", "error"}."""
    await mirror.ensure_synced(db, user_id, p, force=refresh)
    conns = await mirror.visible_connectors(db, user_id, p, subscriptions=True)
    ov = await mirror.kind_overrides(db, user_id)
    out: list[dict[str, Any]] = []
    for c in conns:
        cols = await mirror.collections_of(db, [c.id])
        if not cols and c.status == "error":
            out.append({"connector_id": c.id, "error": (c.status_detail or "calendar unavailable")[:300]})
            continue
        out += [mirror.collection_out(col, c, ov) for col in cols]
    return out


async def defaults(db: AsyncSession, user_id: int, p=None) -> dict[str, Any]:
    """The user's default calendar and task list (user settings, else the first collection
    that accepts events / tasks)."""
    user = await db.get(User, user_id)
    cols = [c for c in await collections(db, user_id, p=p) if "error" not in c]
    saved = (user.settings or {}).get("calendar_defaults") or {}
    by_url = {c["url"]: c for c in cols}
    cal = saved.get("calendar") if saved.get("calendar") in by_url else None
    # A saved list that no longer takes tasks (the built-in stock calendar beside Tasks) is skipped.
    lst = saved.get("task_list") if (by_url.get(saved.get("task_list")) or {}).get("tasks") else None
    if not (cal and lst):
        # Next, the default chosen when an account was connected (connector config), then the first
        # collection in the server's listing, whose order is arbitrary.
        rows = (await db.execute(select(Connector).where(
            Connector.user_id == user_id, Connector.kind == "caldav", Connector.enabled.is_(True))
            .order_by(Connector.id))).scalars().all()
        conf_cal = [r.config.get("default_calendar") for r in rows if (r.config or {}).get("default_calendar")]
        conf_lst = [r.config.get("default_tasklist") for r in rows if (r.config or {}).get("default_tasklist")]
        cal = cal or next((u for u in conf_cal if u in by_url and by_url[u]["events"]), None)
        lst = lst or next((u for u in conf_lst if u in by_url and by_url[u]["tasks"]), None)
    cal = cal or next((c["url"] for c in cols if c["events"] and not c.get("hidden")), None) \
        or next((c["url"] for c in cols if c["events"]), None)
    lst = lst or next((c["url"] for c in cols if c["tasks"] and not c.get("hidden")), None) \
        or next((c["url"] for c in cols if c["tasks"]), None)
    return {"calendar": cal, "task_list": lst,
            "connector_id": (by_url.get(cal) or by_url.get(lst) or {}).get("connector_id")}


async def set_defaults(db: AsyncSession, user: User, calendar: str | None, task_list: str | None,
                       kinds: dict[str, dict[str, bool]] | None = None) -> dict[str, Any]:
    cols = {c["url"]: c for c in await collections(db, user.id) if "error" not in c}
    for url in (calendar, task_list):
        if url and url not in cols:
            raise ValueError(f"unknown calendar {url}")
    from mailr.core import usersettings

    if kinds:
        cur: dict[str, Any] = {}
        for url, k in kinds.items():
            if url not in cols:
                raise ValueError(f"unknown calendar {url}")
            cur[url] = {kk: bool(v) for kk, v in k.items() if kk in ("events", "tasks")}
        await usersettings.patch_in(db, user.id, "calendar_kinds", cur)
    await usersettings.patch(db, user.id, {"calendar_defaults": {"calendar": calendar, "task_list": task_list}})
    await db.flush()
    return await defaults(db, user.id)


# ---------------- events ----------------


async def agenda(db: AsyncSession, user_id: int, start: datetime, end: datetime, *,
                 calendars: list[str] | None = None, p=None, synced: bool = False) -> list[dict[str, Any]]:
    """`synced`: the caller has already run mirror.ensure_synced for this request."""
    if not synced:
        await mirror.ensure_synced(db, user_id, p)
    return await mirror.agenda(db, user_id, start, end, calendars=calendars, p=p)


async def search_events(db: AsyncSession, user_id: int, start: datetime, end: datetime, *,
                        text: str | None = None, calendar: str | None = None, p=None) -> list[dict[str, Any]]:
    """Occurrences in [start, end) of the events that match `text` (the full-text search and
    operators of calendar/search.py; GET /calendar/events/search keeps this shape)."""
    from mailr.calendar import search

    cals = [calendar] if calendar else None
    evs = await agenda(db, user_id, start, end, calendars=cals, p=p)
    uids = await search.matching_event_uids(db, user_id, p, text or "", cals)
    if uids is None:
        return evs
    return [e for e in evs if e["uid"] in uids]


async def search_tasks(db: AsyncSession, user_id: int, *, status: str = "open", text: str | None = None,
                       list: str | None = None, p=None) -> list[dict[str, Any]]:
    return await tasks(db, user_id, status, lists=[list] if list else None, q=text or "", p=p)


async def get_event(db: AsyncSession, user_id: int, uid: str, p=None) -> tuple[dict[str, Any], bytes] | None:
    """The series master (or the single event) with its RRULE, reminders and guests, plus the
    stored iCalendar object (for replies). With a principal, only its visible accounts."""
    obj = await writer.find(db, user_id, uid, "event", p)
    if obj is None:
        return None
    return await mirror.object_out(db, obj, full_description=True), obj.ics.encode()


async def _check_url_visible(db: AsyncSession, user_id: int, p, url: str | None) -> None:
    """A principal limited to some accounts may only write to their collections."""
    if not url:
        return
    cols = {c["url"] for c in await collections(db, user_id, p=p) if "error" not in c}
    if url not in cols:
        raise ValueError("unknown calendar")


async def locate(db: AsyncSession, user_id: int, uid: str, comp: str = "VEVENT", p=None) -> str | None:
    """The URL of the calendar (or task list) that holds this uid, among the accounts the
    principal may see; None when it is not found there."""
    obj = await writer.find(db, user_id, uid, "task" if comp == "VTODO" else "event", p)
    if obj is None:
        return None
    coll = await db.get(mirror.CalCollection, obj.collection_id)
    return coll.href if coll else None


def _restricted(p) -> bool:
    return p is not None and getattr(p, "token", None) is not None


async def create_event_from(db: AsyncSession, user_id: int, payload: dict[str, Any], p=None) -> dict[str, Any]:
    if _restricted(p) and payload.get("calendar"):
        await _check_url_visible(db, user_id, p, payload["calendar"])
    return await writer.create_event(db, user_id, payload, p=p)


async def update_event(db: AsyncSession, user_id: int, uid: str, changes: dict[str, Any], scope: str = "all",
                       occurrence: str | None = None, p=None, *, etag: str | None = None,
                       base_version: int | None = None) -> dict[str, Any] | None:
    if _restricted(p) and changes.get("calendar"):
        await _check_url_visible(db, user_id, p, changes["calendar"])
    return await writer.update_event(db, user_id, uid, changes, scope, occurrence, p=p, etag=etag,
                                     base_version=base_version)


async def delete_event(db: AsyncSession, user_id: int, uid: str, scope: str = "all",
                       occurrence: str | None = None, p=None, *, etag: str | None = None) -> bool:
    return await writer.delete_event(db, user_id, uid, scope, occurrence, p=p, etag=etag)


# ---------------- tasks ----------------


async def tasks(db: AsyncSession, user_id: int, status: str = "open", *, lists: list[str] | None = None,
                q: str = "", p=None) -> list[dict[str, Any]]:
    await mirror.ensure_synced(db, user_id, p)
    return await mirror.tasks(db, user_id, status, lists=lists, q=q, p=p)


async def create_task_from(db: AsyncSession, user_id: int, payload: dict[str, Any], p=None) -> dict[str, Any]:
    if _restricted(p) and payload.get("list"):
        await _check_url_visible(db, user_id, p, payload["list"])
    return await writer.create_task(db, user_id, payload, p=p)


async def update_task(db: AsyncSession, user_id: int, uid: str, changes: dict[str, Any], p=None, *,
                      etag: str | None = None) -> dict[str, Any] | None:
    if _restricted(p) and changes.get("list"):
        await _check_url_visible(db, user_id, p, changes["list"])
    return await writer.update_task(db, user_id, uid, changes, p=p, etag=etag)


async def delete_task(db: AsyncSession, user_id: int, uid: str, p=None, *, etag: str | None = None) -> bool:
    return await writer.delete_task(db, user_id, uid, p=p, etag=etag)


# ---------------- compatibility (older callers) ----------------


async def calendars(db: AsyncSession, user_id: int, p=None) -> list[dict[str, Any]]:
    return await collections(db, user_id, p=p)


async def create_event(db: AsyncSession, user_id: int, *, summary: str, start: datetime | date,
                       end: datetime | date | None = None, location: str = "", description: str = "",
                       calendar_url: str | None = None, connector_id: int | None = None) -> dict[str, Any]:
    return await create_event_from(db, user_id, {"summary": summary, "start": start, "end": end,
                                                 "location": location, "description": description,
                                                 "calendar": calendar_url})


async def create_task(db: AsyncSession, user_id: int, *, summary: str, due: datetime | date | None = None,
                      notes: str = "", list_url: str | None = None, connector_id: int | None = None) -> dict[str, Any]:
    return await create_task_from(db, user_id, {"summary": summary, "due": due, "notes": notes, "list": list_url})


async def set_partstat(db: AsyncSession, user_id: int, uid: str, attendee: str, partstat: str,
                       invite_ics: bytes | None, p=None) -> str:
    return await writer.set_partstat(db, user_id, uid, attendee, partstat, invite_ics, p=p)


async def my_partstat(db: AsyncSession, user_id: int, uid: str, addresses: set[str], p=None) -> str | None:
    """My PARTSTAT on the calendar copy of an event (what I last answered), or None."""
    obj = await writer.find(db, user_id, uid, "event", p)
    if obj is None:
        return None
    for a in obj.attendees or []:
        if a["email"].lower() in addresses:
            return a["partstat"]
    return None


def parse_when(value: str | None) -> datetime | date | None:
    """Accept ISO date or datetime strings (naive datetimes are treated as UTC)."""
    if not value:
        return None
    return ical.parse_when(value, UTC)


async def user_zone(db: AsyncSession, user_id: int):
    return await tzmod.user_zone(db, user_id)

