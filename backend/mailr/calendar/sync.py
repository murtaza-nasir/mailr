"""Calendar sync: CalDAV → mirror.

One run per connector at a time (pg_try_advisory_lock on 'calsync:<id>'). Discovery is one
PROPFIND on the home set; only collections whose sync-token or ctag changed are synced:
RFC 6578 sync-collection when the server supports it, else an ETag listing. Changed resources
are fetched with calendar-multiget and written in batches, each batch recording one change
per entity. A first sync records one `calendar` change per collection.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import builtin, dav, ical, mirror
from mailr.calendar import tz as tzmod
from mailr.core.crypto import connector_context, decrypt_for_user
from mailr.core.events import notify, record_change
from mailr.core.jobs import enqueue, handler
from mailr.core.netguard import UnsafeTarget
from mailr.db import session_scope, side_engine, side_pool, side_pool_slots
from mailr.models import CalCollection, CalObject, Connector, User

log = logging.getLogger("mailr.calendar.sync")

BATCH = 200
ACTIVE_INTERVAL = timedelta(minutes=2)
IDLE_INTERVAL = timedelta(minutes=15)
FULL_EVERY = timedelta(hours=24)
MAX_BACKOFF = timedelta(hours=6)
TRUNCATED_ERROR = ("The calendar server listed only part of this calendar (it has more items than the "
                   "server lists at once). Nothing was removed; Mailr tries again later.")

_BACKGROUND: set[asyncio.Task] = set()


@dataclass
class SyncResult:
    connector_id: int
    busy: bool = False
    error: str | None = None
    changed: int = 0
    deleted: int = 0
    collections: list[int] = field(default_factory=list)


def lock_key(connector_id: int) -> str:
    return f"calsync:{connector_id}"


async def uid_lock(db: AsyncSession, user_id: int, uid: str, *, wait: bool = True) -> bool:
    """The per-UID transaction lock writers and sync share (released at commit)."""
    fn = "pg_advisory_xact_lock" if wait else "pg_try_advisory_xact_lock"
    got = (await db.execute(text(f"SELECT {fn}(hashtextextended(:k, 0))"), {"k": f"{user_id}:{uid}"})).scalar()
    return True if wait else bool(got)


def dav_client(conn: Connector, user: User) -> dav.DavClient:
    if conn.builtin:  # mailr's own store: impersonation, credentials never stored (builtin.py)
        return builtin.client_for(conn)
    secrets = decrypt_for_user(user.id, user.data_key_wrapped, conn.secrets_enc,
                               context=connector_context(conn.id)) or {}
    from mailr.core.netguard import private_ok_for

    return dav.DavClient(conn.config.get("url") or "", conn.config.get("username") or "", secrets.get("password", ""),
                         private_ok=private_ok_for(user))


def _backoff(fails: int) -> timedelta:
    return min(MAX_BACKOFF, timedelta(minutes=1) * (2 ** max(0, fails - 1)))


async def _interval(db: AsyncSession, user_id: int) -> timedelta:
    seen = (await db.execute(text("SELECT max(last_seen_at) FROM sessions WHERE user_id=:u"), {"u": user_id})).scalar()
    return ACTIVE_INTERVAL if seen and datetime.now(UTC) - seen < timedelta(minutes=15) else IDLE_INTERVAL


# ---------------- entry points ----------------


async def sync_connector(connector_id: int, *, full: bool = False, served_since: datetime | None = None) -> SyncResult:
    """Sync one connector now. Returns busy=True when another run holds its lock. Its
    connections come from the side pool (mailr/db.py), never from the requests' pool.

    `served_since` (run_inline): once the lock is held, a run that listed the server at or after
    this moment has already done the work, so nothing runs (checked under the lock, so a run that
    ended a moment ago is never repeated)."""
    with side_pool():
        async with _slots():
            return await _sync_locked(connector_id, full, served_since)


_SLOTS: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


def _slots() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _SLOTS:
        _SLOTS.clear()  # one loop per process outside tests
        _SLOTS[loop] = asyncio.Semaphore(side_pool_slots())
    return _SLOTS[loop]


async def _sync_locked(connector_id: int, full: bool, served_since: datetime | None = None) -> SyncResult:
    async with side_engine().connect() as raw:
        lc = await raw.execution_options(isolation_level="AUTOCOMMIT")
        got = (await lc.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"),
                                {"k": lock_key(connector_id)})).scalar()
        if not got:
            return SyncResult(connector_id, busy=True)
        try:
            if served_since is not None and not full and await _listed_since(connector_id, served_since):
                return SyncResult(connector_id)
            return await _sync(connector_id, full)
        finally:
            await lc.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": lock_key(connector_id)})


async def run_inline(connector_id: int, *, budget: float, full: bool = False) -> SyncResult | None:
    """Sync from a request, waiting at most `budget` seconds; a longer run goes on in the
    background. When another run holds the lock, wait (within the budget) for it to end.

    A run by someone else that listed the server after this call began is as good as a run of
    our own, so the wait ends there without a second run (not for `full`). Without this, every
    read that arrived during a sync waited for it and then ran one more, one after another."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget
    asked = datetime.now(UTC)
    while True:
        task = asyncio.create_task(sync_connector(connector_id, full=full, served_since=None if full else asked))
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)
        done, _ = await asyncio.wait({task}, timeout=max(0.0, deadline - loop.time()))
        if not done:
            return None
        try:
            res = task.result()
        except Exception as e:  # noqa: BLE001
            log.warning("inline calendar sync of connector %s failed: %s", connector_id, e)
            return None
        if not res.busy or loop.time() >= deadline:
            return res
        while True:
            await asyncio.sleep(0.1)
            if not full and await _listed_since(connector_id, asked):
                return SyncResult(connector_id)
            if loop.time() >= deadline or not await _locked(connector_id):
                break


_REFRESHING: dict[int, asyncio.Task] = {}


def refresh_in_background(connector_id: int) -> None:
    """Start an incremental sync of one connector without waiting for it (mirror.ensure_synced
    with wait=False). One at a time per connector in this process: a read that comes while one is
    running starts nothing (a second run would find the lock taken or the listing fresh)."""
    running = _REFRESHING.get(connector_id)
    if running is not None and not running.done():
        return

    async def run() -> None:
        try:
            await sync_connector(connector_id, served_since=datetime.now(UTC))
        except Exception as e:  # noqa: BLE001  a failed refresh is retried by the next read or the worker
            log.warning("background calendar sync of connector %s failed: %s", connector_id, e)

    task = asyncio.create_task(run())
    _REFRESHING[connector_id] = task
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


async def _listed_since(connector_id: int, since: datetime) -> bool:
    """A sync of this connector listed the server at or after `since` and has finished."""
    async with session_scope() as db:
        conn = await db.get(Connector, connector_id)
        listed = ((conn.state or {}).get("caldav") or {}).get("listed_at") if conn is not None else None
    try:
        return bool(listed) and datetime.fromisoformat(listed) >= since
    except ValueError:
        return False


async def _locked(connector_id: int) -> bool:
    """Another run holds this connector's sync lock (pg_locks; nothing is taken here)."""
    async with session_scope() as db:
        return bool((await db.execute(text(
            "SELECT EXISTS (SELECT 1 FROM pg_locks, (SELECT hashtext(:k)::bigint AS key) k "
            "WHERE locktype = 'advisory' AND granted AND database = (SELECT oid FROM pg_database "
            "WHERE datname = current_database()) AND objsubid = 1 "
            "AND classid = ((k.key >> 32) & 4294967295)::text::oid AND objid = (k.key & 4294967295)::text::oid)"),
            {"k": lock_key(connector_id)})).scalar())


# ---------------- the run ----------------


async def _set_connector(conn_id: int, **kw: Any) -> None:
    async with session_scope() as db:
        conn = await db.get(Connector, conn_id)
        if conn is None:
            return
        st = dict((conn.state or {}).get("caldav") or {})
        st.update(kw.pop("caldav", {}))
        conn.state = {**(conn.state or {}), "caldav": st}
        for k, v in kw.items():
            setattr(conn, k, v)
        await notify(db, conn.user_id, {"type": "connector", "id": conn.id, "status": conn.status})


async def _sync(connector_id: int, full: bool) -> SyncResult:
    res = SyncResult(connector_id)
    async with session_scope() as db:
        conn = await db.get(Connector, connector_id)
        if conn is None or conn.kind != "caldav" or not conn.enabled or not await builtin.active(db, conn):
            return res
        is_builtin = conn.builtin
        user = await db.get(User, conn.user_id)
        client = dav_client(conn, user)
        cstate = dict((conn.state or {}).get("caldav") or {})
        user_id = conn.user_id
        user_tz = await tzmod.user_zone(db, user)
        me = await mirror.my_addresses(db, user_id)
        interval = await _interval(db, user_id)
    # A collection deleted here after this moment may still be in the listing below: the delete
    # (calendar/routes_collections.py) takes no sync lock, so the listing can predate it.
    listed_at = datetime.now(UTC)
    async with client:
        try:
            home = cstate.get("home")
            infos = None
            if home:
                try:
                    infos = await client.collections(home)
                except dav.NotFound:
                    infos = None
            if not infos:
                home = await client.home()
                infos = await client.collections(home)
            if is_builtin and not cstate.get("builtin_ready") and await builtin.prepare(client, home, infos):
                infos = await client.collections(home)
            if is_builtin:  # only registered group calendars of the group accounts (calendar/groups.py)
                from mailr.calendar import groups

                infos = await groups.filter_listing(infos)
        except dav.Unauthorized as e:
            res.error = str(e)
            await _set_connector(connector_id, status="error", status_detail="Sign-in failed. Check the password.",
                                 caldav={"auth_failed": True, "checked_at": datetime.now(UTC).isoformat()})
            return res
        except (dav.DavError, UnsafeTarget) as e:
            res.error = str(e)[:500]
            await _set_connector(connector_id, status="error", status_detail=res.error,
                                 caldav={"checked_at": datetime.now(UTC).isoformat()})
            return res
        share_state: dict[str, Any] = {}
        if is_builtin:  # calendars people share with this user, and this user's own grants (calendar/sharing.py)
            from mailr.calendar import sharing

            try:
                shared_in, own_acl = await sharing.on_listing(client, user_id, home, infos)
                share_state = {"shared_in": shared_in, **({"acl": own_acl} if own_acl is not None else {})}
            except Exception as e:  # noqa: BLE001  the calendars still sync; the marks wait for the next run
                log.warning("could not read the calendar shares of connector %s: %s", connector_id, e)
        colls = await _diff_collections(connector_id, infos, res, listed_at)
        initial_done = True
        for info in infos:
            coll_id = colls.get(info.href)
            if coll_id is None:
                continue
            try:
                ok = await _sync_collection(client, coll_id, info, full, user_tz, me, interval, res)
                initial_done = initial_done and ok
            except Exception as e:  # noqa: BLE001  one collection failing must not stop the others
                log.warning("calendar sync of %s failed: %s", info.href, e)
                initial_done = False
                await _collection_failed(coll_id, str(e))
    worst = await _worst(connector_id)
    ready = {}
    if is_builtin and not cstate.get("builtin_ready") and initial_done:
        await builtin.adopt_defaults(user_id, connector_id, client.abs(home))
        ready = {"builtin_ready": True}
    now = datetime.now(UTC)
    await _set_connector(
        connector_id, status="error" if worst else "idle",
        status_detail=worst or f"{len(infos)} calendars", last_sync_at=now,
        caldav={"home": home, "checked_at": now.isoformat(), "listed_at": listed_at.isoformat(), "auth_failed": False,
                **({"initial_done": True} if initial_done else {}), **ready, **share_state})
    return res


# Development only (mailr e2e-seed): the dev Radicale grants its users every right, so a seeded
# collection whose description starts with this mark is treated as read-only.
DEV_READ_ONLY_MARK = "mailr-e2e: read-only"


def _read_only(info: dav.CollectionInfo) -> bool:
    if info.read_only:
        return True
    from mailr.config import get_settings

    return bool(get_settings().dev and (info.description or "").startswith(DEV_READ_ONLY_MARK))


async def _diff_collections(connector_id: int, infos: list[dav.CollectionInfo], res: SyncResult,
                            listed_at: datetime | None = None) -> dict[str, int]:
    """Upsert the discovered collections, tombstone the ones that are gone; {href: id}.

    A tombstone newer than `listed_at` (when the listing was asked for) stays: the collection
    was deleted here while the listing was on its way, so the listing is older than the delete.
    Bringing it back would show a deleted list until the next sync."""
    out: dict[str, int] = {}
    async with session_scope() as db:
        conn = await db.get(Connector, connector_id)
        rows = {c.href: c for c in await mirror.collections_of(db, [connector_id], include_deleted=True)}
        changed: list[int] = []
        for info in infos:
            c = rows.get(info.href)
            props = {"displayname": info.displayname, "description": info.description, "color": info.color,
                     "components": info.components, "server_order": info.order, "read_only": _read_only(info),
                     "calendar_tz": info.calendar_tz}
            if c is None:
                c = CalCollection(user_id=conn.user_id, connector_id=connector_id, href=info.href, **props)
                db.add(c)
                await db.flush()
                changed.append(c.id)
            elif c.deleted_at is not None and listed_at is not None and c.deleted_at >= listed_at:
                continue
            else:
                diff = {k: v for k, v in props.items() if getattr(c, k) != v}
                if diff or c.deleted_at is not None:
                    for k, v in diff.items():
                        setattr(c, k, v)
                    if c.deleted_at is not None:  # back again: synced from scratch
                        c.deleted_at, c.sync_token, c.ctag, c.last_full_sync_at = None, None, None, None
                    await mirror.mark_collection_rev(db, c)
                    changed.append(c.id)
            out[info.href] = c.id
            res.collections.append(c.id)
        seen = {i.href for i in infos}
        for href, c in rows.items():
            if href in seen or c.deleted_at is not None:
                continue
            gone = await mirror.soft_delete_collection(db, c)
            for kind in ("event", "task"):
                ids = [i for k, i in gone if k == kind]
                if ids:
                    await record_change(db, conn.user_id, kind, ids, op="delete")
            await record_change(db, conn.user_id, "calendar", c.id, op="delete")
        if changed:
            await record_change(db, conn.user_id, "calendar", changed)
        # Older readers (and one release of clients) still look at config.collections.
        ov: dict[str, Any] = {}
        conn.config = {**(conn.config or {}), "collections": [
            {k: v for k, v in mirror.collection_out(c, conn, ov).items()
             if k in ("id", "url", "connector_id", "name", "color", "events", "tasks", "components")}
            for c in await mirror.collections_of(db, [connector_id])]}
    return out


async def _collection_failed(coll_id: int, error: str) -> None:
    async with session_scope() as db:
        c = await db.get(CalCollection, coll_id)
        if c is None:
            return
        c.fail_count = (c.fail_count or 0) + 1
        c.sync_status, c.sync_error = "backoff", error[:500]
        c.next_sync_at = datetime.now(UTC) + _backoff(c.fail_count)


async def _worst(connector_id: int) -> str | None:
    async with session_scope() as db:
        rows = (await db.execute(select(CalCollection.sync_error).where(
            CalCollection.connector_id == connector_id, CalCollection.deleted_at.is_(None),
            CalCollection.sync_status.in_(["error", "backoff"])))).scalars().all()
    return rows[0] if rows else None


async def _sync_collection(client: dav.DavClient, coll_id: int, info: dav.CollectionInfo, full: bool,
                           user_tz, me: set[str], interval: timedelta, res: SyncResult) -> bool:
    """Bring one collection up to date. Returns True when it has finished a full sync."""
    started = datetime.now(UTC)
    async with session_scope() as db:
        coll = await db.get(CalCollection, coll_id)
        initial = coll.last_full_sync_at is None
        due_full = full or initial or (coll.last_full_sync_at < started - FULL_EVERY)
        unchanged = (not due_full and coll.sync_status not in ("error", "backoff") and (
            (info.sync_token and coll.sync_token == info.sync_token)
            or (not info.sync_token and info.ctag and coll.ctag == info.ctag)))
        if unchanged:
            coll.last_sync_at, coll.next_sync_at = started, started + interval
            return True
        token = None if due_full else coll.sync_token
        stored_ctag = coll.ctag
        sync_supported = coll.sync_supported
        mirror_etags = dict((await db.execute(select(CalObject.href, CalObject.etag).where(
            CalObject.collection_id == coll_id, CalObject.deleted_at.is_(None)))).all())
        coll.sync_status = "syncing"
    changed: list[tuple[str, str | None]] = []
    removed: list[str] = []
    new_token: str | None = None
    listed = False
    need_listing = False  # the delta cannot be trusted: list every ETag instead
    if sync_supported is not False:
        try:
            try:
                changed, removed, new_token, truncated = await client.sync_collection(info.href, token)
                listed = token is None
            except dav.InvalidSyncToken:
                changed, removed, new_token, truncated = await client.sync_collection(info.href, None)
                listed = True
            sync_supported = True
            if truncated:
                # Never a complete listing, and Stalwart's token with it is its current one, so
                # the items past its limit would never come in a later delta.
                need_listing = True
            elif token is not None and not changed and not removed and stored_ctag and info.ctag \
                    and info.ctag != stored_ctag:
                # The server took the token yet reports no change while the collection did change:
                # a token from the future (a restored server) or one it no longer tracks.
                need_listing = True
        except dav.Truncated:  # the whole answer was a 507
            sync_supported, need_listing = True, True
        except (dav.Unauthorized, dav.NotFound):
            raise
        except dav.DavError:
            sync_supported = False
    incomplete = False
    if sync_supported is False or need_listing:
        try:
            server = await client.etags(info.href)
            changed, removed, listed = list(server.items()), [], True
            if sync_supported is False:
                new_token = None
        except dav.Truncated:
            # Keep what the delta brought (explicit changes and removals are real), compute no
            # removals and keep the old token: the next run tries again.
            incomplete, listed = True, False
    if listed:
        present = {h for h, _ in changed}
        removed = [h for h in mirror_etags if h not in present]
    fetch = [h for h, e in changed if e is None or mirror_etags.get(h) != e]
    skipped = False
    for i in range(0, len(fetch), BATCH):
        got = await client.multiget(info.href, fetch[i:i + BATCH])
        async with session_scope() as db:
            coll = await db.get(CalCollection, coll_id)
            ids: dict[str, list[int]] = {"event": [], "task": []}
            for href, etag, ics in got:
                try:
                    uid = ical.parse_resource(ics, user_tz).fields.get("uid") or href
                except Exception:  # noqa: BLE001
                    uid = href
                if not await uid_lock(db, coll.user_id, uid, wait=False):
                    skipped = True  # a writer has it; the next run sees the change again
                    continue
                obj = await mirror.upsert_object(db, coll, href, etag, ics, user_tz=user_tz, me=me,
                                                 skip_if_updated_after=started)
                if obj is not None:
                    ids.setdefault(obj.kind, []).append(obj.id)
                    res.changed += 1
            if not initial:
                for kind, kids in ids.items():
                    if kids:
                        await record_change(db, coll.user_id, kind, kids)
    if removed:
        async with session_scope() as db:
            coll = await db.get(CalCollection, coll_id)
            rows = (await db.execute(select(CalObject).where(
                CalObject.collection_id == coll_id, CalObject.href.in_(removed),
                CalObject.deleted_at.is_(None)))).scalars().all()
            ids = {"event": [], "task": []}
            for o in rows:
                if o.updated_at and o.updated_at > started:
                    continue
                await mirror.soft_delete(db, o)
                ids.setdefault(o.kind, []).append(o.id)
                res.deleted += 1
            if not initial:
                for kind, kids in ids.items():
                    if kids:
                        await record_change(db, coll.user_id, kind, kids, op="delete")
    async with session_scope() as db:
        coll = await db.get(CalCollection, coll_id)
        now = datetime.now(UTC)
        if incomplete:
            coll.fail_count = (coll.fail_count or 0) + 1
            coll.sync_status, coll.sync_error = "backoff", TRUNCATED_ERROR
            coll.last_sync_at, coll.next_sync_at = now, now + _backoff(coll.fail_count)
            if initial:
                await record_change(db, coll.user_id, "calendar", coll.id)
            log.warning("calendar sync of %s: the server listed only part of the collection", info.href)
            return False
        if not skipped:  # with a skipped resource the old token stays, so the next run fetches it again
            coll.sync_token = new_token if sync_supported else None
            coll.ctag = info.ctag
        coll.sync_supported = sync_supported
        coll.sync_status, coll.sync_error, coll.fail_count = "idle", "", 0
        coll.last_sync_at, coll.next_sync_at = now, now + interval
        if listed:
            coll.last_full_sync_at = now
        if initial:
            await mirror.mark_collection_rev(db, coll)
            await record_change(db, coll.user_id, "calendar", coll.id)
    return True


# ---------------- jobs and the scheduler ----------------


@handler("calendar.sync")
async def sync_job(payload: dict[str, Any], ctx) -> None:
    from mailr.core.jobs import Retry

    res = await sync_connector(int(payload["connector_id"]), full=bool(payload.get("full")))
    if res.busy:
        raise Retry(delay=20)


@handler("calendar.check")
async def check_job(payload: dict[str, Any], ctx) -> None:
    """The older job name: re-check the account (clears a paused sign-in failure) and sync."""
    cid = int(payload["connector_id"])
    await _set_connector(cid, caldav={"auth_failed": False})
    await sync_connector(cid)


@handler("calendar.rematerialize")
async def rematerialize_job(payload: dict[str, Any], ctx) -> None:
    """Re-expand a user's events (zone change, or windows that need extending)."""
    await rematerialize(int(payload["user_id"]), only_stale=bool(payload.get("only_stale")))


async def rematerialize(user_id: int, *, only_stale: bool = False) -> int:
    n = 0
    async with session_scope() as db:
        user_tz = await tzmod.user_zone(db, user_id)
        me = await mirror.my_addresses(db, user_id)
        q = select(CalObject.id).where(CalObject.user_id == user_id, CalObject.deleted_at.is_(None),
                                       CalObject.kind == "event")
        if only_stale:
            q = q.where(CalObject.recurring.is_(True), CalObject.expanded_until < datetime.now(UTC) + timedelta(days=700))
        ids = (await db.execute(q)).scalars().all()
    for i in range(0, len(ids), BATCH):
        async with session_scope() as db:
            for oid in ids[i:i + BATCH]:
                obj = await db.get(CalObject, oid)
                coll = await db.get(CalCollection, obj.collection_id) if obj else None
                if obj is None or coll is None:
                    continue
                await mirror.upsert_object(db, coll, obj.href, obj.etag, obj.ics, user_tz=user_tz, me=me, force=True)
                n += 1
    return n


async def due_syncs() -> None:
    """Periodic (every minute): queue a sync for connectors whose next sync is due."""
    async with session_scope() as db:
        q = select(Connector).where(Connector.kind == "caldav", Connector.enabled.is_(True))
        if not await builtin.instance_enabled(db):
            q = q.where(Connector.builtin.is_(False))
        rows = (await db.execute(q)).scalars().all()
        now = datetime.now(UTC)
        for c in rows:
            if c.builtin and not await builtin.active(db, c):  # the group policy leaves this user out
                continue
            st = (c.state or {}).get("caldav") or {}
            if st.get("auth_failed"):
                continue
            nxt = (await db.execute(select(CalCollection.next_sync_at).where(
                CalCollection.connector_id == c.id, CalCollection.deleted_at.is_(None))
                .order_by(CalCollection.next_sync_at.nulls_first()).limit(1))).scalar()
            if st.get("initial_done") and nxt is not None and nxt > now:
                continue
            await enqueue(db, "calendar.sync", {"connector_id": c.id}, user_id=c.user_id,
                          dedupe_key=f"calendar.sync:{c.id}")


async def daily_maintenance() -> None:
    """Periodic (daily): extend recurrence windows and purge old tombstones."""
    async with session_scope() as db:
        users = (await db.execute(text(
            "SELECT DISTINCT user_id FROM cal_objects WHERE recurring AND deleted_at IS NULL "
            "AND expanded_until < now() + interval '700 days'"))).scalars().all()
        for uid in users:
            await enqueue(db, "calendar.rematerialize", {"user_id": uid, "only_stale": True}, user_id=uid,
                          dedupe_key=f"calendar.rematerialize:{uid}")
        await mirror.purge_tombstones(db)
