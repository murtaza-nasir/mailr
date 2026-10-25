"""Change stream.

Every mutation of user-visible state records a row in change_log (the delta cursor for sync
clients) and sends NOTIFY mailr_changes, '<user_id>'. The API process runs one listener and
fans notifications out to that user's open SSE streams.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.config import get_settings

log = logging.getLogger("mailr.events")


async def record_change(
    db: AsyncSession, user_id: int, entity: str, entity_ids: list[int] | int, op: str = "upsert"
) -> None:
    ids = [entity_ids] if isinstance(entity_ids, int) else list(dict.fromkeys(entity_ids))
    if not ids:
        return
    await db.execute(
        text(
            "INSERT INTO change_log (user_id, entity, entity_id, op) "
            "SELECT :u, :e, unnest(CAST(:ids AS bigint[])), :op"
        ),
        {"u": user_id, "e": entity, "ids": ids, "op": op},
    )
    event: dict = {"type": "changes", "entity": entity}
    if entity == "conversation" and len(ids) <= EVENT_IDS:
        # Which conversations: the web app then refetches only those if open, not every
        # conversation it has looked at (api/events.ts). A bigger batch says nothing: any.
        event["conversation_ids"] = ids
    await notify(db, user_id, event)


# At most this many ids ride on a `changes` event (a NOTIFY payload holds 8,000 bytes).
EVENT_IDS = 100


async def notify(db: AsyncSession, user_id: int, event: dict) -> None:
    payload = json.dumps({"u": user_id, **event})[:7900]
    await db.execute(text("SELECT pg_notify('mailr_changes', :p)"), {"p": payload})


# Sent to a stream that missed events; the client catches up from the change cursor.
RESYNC = {"type": "resync"}


def _overflow(q: asyncio.Queue) -> None:
    closing = None
    while True:
        try:
            ev = q.get_nowait()
        except asyncio.QueueEmpty:
            break
        if isinstance(ev, dict) and ev.get("type") == "__close__":
            closing = ev  # a stream being ended (main.py _open_stream) still ends
    q.put_nowait(closing if closing is not None else dict(RESYNC))


# After a change, the user's open streams get the new sidebar numbers as one `counts` event
# (core/sidebar.py). Changes within COUNTS_DELAY seconds are coalesced: a bulk action of many
# chunks sends one update per window, not one per change. Under a steady stream of changes (an
# import, a backfill) the numbers are also computed at most once per COUNTS_SPACING times their
# last cost: at 150,000 conversations a recount every 0.75 s kept about 100 ms of aggregates
# running for each open tab. The last change of a burst is always followed by a count.
COUNTS_DELAY = 0.75
COUNTS_SPACING = 4.0
COUNTS_MAX_WAIT = 15.0


class Hub:
    def __init__(self) -> None:
        self._subs: dict[int, set[asyncio.Queue]] = defaultdict(set)
        self._task: asyncio.Task | None = None
        self._counts_due: dict[int, asyncio.Task] = {}
        # Per user: when the numbers were last computed, and what the slowest recent one cost.
        self._counts_at: dict[int, float] = {}
        self._counts_cost: dict[int, float] = {}
        # Users with a change since their running count task last computed.
        self._counts_dirty: set[int] = set()

    def subscribe(self, user_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subs[user_id].add(q)
        return q

    def unsubscribe(self, user_id: int, q: asyncio.Queue) -> None:
        self._subs[user_id].discard(q)

    def _dispatch(self, _conn, _pid, _channel, payload: str) -> None:
        try:
            data = json.loads(payload)
        except ValueError:
            return
        uid = data.pop("u", None)
        if data.get("type") == "typing":
            # An editor's typing report (core/live.py): kept for writes that wait for a pause,
            # not sent to the streams.
            from mailr.core import live

            if uid is not None:
                live.saw_typing(uid, str(data.get("kind")), data.get("id"))
            return
        self._put(uid, data)
        if data.get("type") in ("changes", "outbox", "approval") and self._subs.get(uid):
            task = self._counts_due.get(uid)
            if task is None or task.done():
                try:
                    self._counts_due[uid] = asyncio.get_running_loop().create_task(self._send_counts(uid))
                except RuntimeError:
                    pass  # no running loop (a test calling _dispatch directly)
            else:
                self._counts_dirty.add(uid)

    def _put(self, uid, data: dict) -> None:
        for q in list(self._subs.get(uid, ())):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                # The stream is behind (a slow client, a burst of changes): what is queued is
                # dropped for one `resync`, on which the client catches up from the change cursor
                # (api/events.ts). Before, the newest events were dropped and nobody knew.
                _overflow(q)

    def resync_all(self) -> None:
        """Every open stream catches up: notifications may have been missed."""
        for uid in list(self._subs):
            self._put(uid, dict(RESYNC))

    async def _send_counts(self, uid: int) -> None:
        """The new sidebar numbers for a signed-in user's streams (the stream is for signed-in
        apps only, so these are the user's own, unrestricted counts)."""
        loop = asyncio.get_running_loop()
        try:
            while True:
                await asyncio.sleep(COUNTS_DELAY)
                gap = min(COUNTS_MAX_WAIT, COUNTS_SPACING * self._counts_cost.get(uid, 0.0))
                wait = self._counts_at.get(uid, float("-inf")) + gap - loop.time()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._counts_dirty.discard(uid)
                if not self._subs.get(uid):
                    self._counts_at.pop(uid, None)
                    self._counts_cost.pop(uid, None)
                    return
                await self._push_counts(uid, loop)
                # A change while these were computed: once more, after the spacing.
                if uid not in self._counts_dirty:
                    return
        finally:
            self._counts_due.pop(uid, None)

    async def _push_counts(self, uid: int, loop: asyncio.AbstractEventLoop) -> None:
        t0 = loop.time()
        try:
            counts = await sidebar_counts_for(uid)
        except Exception as e:  # noqa: BLE001
            log.warning("counts for user %s failed: %s", uid, e)
            return
        finally:
            now = loop.time()
            # The slowest recent cost, halved on each count: a cached answer (a few ms) does not
            # undo the spacing a slow one called for at once.
            self._counts_cost[uid] = max(now - t0, self._counts_cost.get(uid, 0.0) / 2)
            self._counts_at[uid] = now
        if counts is not None:
            self._put(uid, {"type": "counts", **counts})

    async def run(self) -> None:
        import asyncpg

        dsn = get_settings().database_url.replace("postgresql+asyncpg", "postgresql")
        lost = False
        while True:
            try:
                conn = await asyncpg.connect(dsn)
                await conn.add_listener("mailr_changes", self._dispatch)
                if lost:
                    # NOTIFYs sent while the listener was away are gone: the streams catch up.
                    lost = False
                    self.resync_all()
                while True:
                    await asyncio.sleep(30)
                    await conn.execute("SELECT 1")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("event listener reconnecting: %s", e)
                lost = True
                await asyncio.sleep(3)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run())


async def sidebar_counts_for(user_id: int) -> dict | None:
    from mailr.core import sidebar
    from mailr.core.auth import ALL_SCOPES, Principal
    from mailr.db import sessionmaker
    from mailr.models import User

    async with sessionmaker()() as db:
        user = await db.get(User, user_id)
        if user is None or user.disabled:
            return None
        return await sidebar.sidebar_counts(db, Principal(user=user, scopes=set(ALL_SCOPES) - {"admin"}))


hub = Hub()
