"""How many model calls run at once against one model endpoint, across every process.

MAILR_MODEL_CONCURRENCY (default 4, sized for one local GPU) calls run at once per endpoint, in the
app and all workers together; 0 turns the cap off. While another user's call waits, one user holds
at most half of them (at least one), so a user's import triage never takes every slot. Calls made by the app (chat, Help me
write, a search's query vector: someone is waiting) may take every slot; the workers' background
calls (triage, summaries, embeddings of imported mail) leave the last one free for them. Inside a
process the waiting calls go in order: live calls first, then the user with the fewest calls
running, then the one that waited longest.

Each slot is a Postgres session advisory lock on one connection per process: (SLOT_LOCK, endpoint
id * 1024 + slot) for the endpoint's slots and (USER_LOCK + share index, user id) for a user's
share. A process that stops frees its slots with its connection. While that connection cannot be
had, calls run without a slot rather than wait.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass, field

from mailr.config import get_settings

log = logging.getLogger("mailr.slots")

SLOT_LOCK = 727500
USER_LOCK = 727510  # + the share index (0 … share-1)
POLL_SECONDS = 0.25  # a slot freed in another process is noticed this soon
SLOTS_PER_ENDPOINT = 1024

# Live calls: the app's process sets PROCESS_INTERACTIVE (main.py); LIVE overrides it for one task.
PROCESS_INTERACTIVE = False
LIVE: ContextVar[bool | None] = ContextVar("mailr_model_live", default=None)
# Set while a call holds a slot: a model call made inside it (a tool of a chat) takes none.
_HELD: ContextVar[bool] = ContextVar("mailr_model_slot_held", default=False)


def cap() -> int:
    return max(0, get_settings().model_concurrency)


def share(n: int) -> int:
    return max(1, n // 2)


def live() -> bool:
    v = LIVE.get()
    return PROCESS_INTERACTIVE if v is None else v


@dataclass(order=True)
class _Waiter:
    rank: tuple = field(compare=True)
    endpoint: int = field(compare=False)
    user: int | None = field(compare=False)
    live: bool = field(compare=False)
    got: asyncio.Future = field(compare=False, default=None)  # type: ignore[assignment]


class _Slots:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.conn = None
        self.mutex = asyncio.Lock()
        self.waiting: list[_Waiter] = []
        self.running: dict[tuple[int, int | None], int] = {}  # (endpoint, user) → calls running here
        # The locks this process's connection holds: a session takes its own lock again (they
        # stack), so the process skips them itself.
        self.held: set[tuple[int, int]] = set()
        self.seq = itertools.count()

    async def _connection(self):
        if self.conn is None or self.conn.is_closed():
            import asyncpg

            self.held.clear()  # a closed connection let go of them
            dsn = get_settings().database_url.replace("postgresql+asyncpg", "postgresql")
            self.conn = await asyncpg.connect(dsn)
        return self.conn

    async def _lock(self, conn, k1: int, k2: int) -> bool:
        if (k1, k2) in self.held or not await conn.fetchval("SELECT pg_try_advisory_lock($1, $2)", k1, k2):
            return False
        self.held.add((k1, k2))
        return True

    async def _unlock(self, k1: int, k2: int) -> None:
        if (k1, k2) in self.held and self.conn is not None:
            self.held.discard((k1, k2))
            await self.conn.fetchval("SELECT pg_advisory_unlock($1, $2)", k1, k2)

    async def _try(self, w: _Waiter, n: int, others: bool) -> tuple[int, int | None] | None | bool:
        """Take an endpoint slot and the user's share for `w`: (slot key, share key or None);
        None when every slot the call may use is taken; False when only the user's share is.
        `others`: another user's call waits here; without one, a call past its share goes too."""
        conn = await self._connection()
        user_key = None
        user = w.user
        if user is not None:
            for j in range(share(n)):
                if await self._lock(conn, USER_LOCK + j, user):
                    user_key = USER_LOCK + j
                    break
            else:
                if others:
                    return False
        usable = n if w.live or n == 1 else n - 1
        for i in range(usable):
            key = w.endpoint * SLOTS_PER_ENDPOINT + i
            if await self._lock(conn, SLOT_LOCK, key):
                return key, user_key
        if user_key is not None and user is not None:
            await self._unlock(user_key, user)
        return None

    async def pump(self, endpoint: int) -> None:
        """Hand free slots to this endpoint's waiting calls in their order."""
        n = cap()
        async with self.mutex:
            todo = [x for x in self.waiting if x.endpoint == endpoint and not x.got.done()]
            for w in todo:
                w.rank = (w.rank[0], self.running.get((endpoint, w.user), 0), w.rank[2])
            for w in sorted(todo):
                try:
                    got = await self._try(w, n, any(x.user != w.user for x in todo))
                except Exception as e:  # noqa: BLE001  the database is away: run without a slot
                    log.warning("model call slots unavailable (%s); calls run without one", type(e).__name__)
                    self.conn = None
                    w.got.set_result(None)
                    continue
                if got is None:
                    break  # live calls come first: the ones behind may use no more slots
                if got is False:
                    continue  # this user's share is taken; the next user's call may go
                self.running[(endpoint, w.user)] = self.running.get((endpoint, w.user), 0) + 1
                w.got.set_result(got)

    async def release(self, endpoint: int, user: int | None, got: tuple[int, int | None]) -> None:
        key, user_key = got
        k = (endpoint, user)
        self.running[k] = self.running.get(k, 1) - 1
        if self.running[k] <= 0:
            self.running.pop(k, None)
        async with self.mutex:
            try:
                if self.conn is not None and not self.conn.is_closed():
                    await self._unlock(SLOT_LOCK, key)
                    if user_key is not None and user is not None:
                        await self._unlock(user_key, user)
            except Exception as e:  # noqa: BLE001  a lost connection freed them already
                log.info("model slot release: %s", e)
                self.conn = None
        await self.pump(endpoint)


_slots: _Slots | None = None


def _current() -> _Slots:
    """The slots of the running event loop (a command that runs several loops starts afresh)."""
    global _slots
    loop = asyncio.get_running_loop()
    if _slots is None or _slots.loop is not loop:
        _slots = _Slots()
        _slots.loop = loop
    return _slots


@contextlib.asynccontextmanager
async def slot(endpoint_id: int, user_id: int | None) -> AsyncIterator[None]:
    """Hold one of the endpoint's slots for a model call (waiting for one, in turn)."""
    n = cap()
    if n == 0 or _HELD.get():
        yield
        return
    is_live = live()
    pool = _current()
    w = _Waiter(rank=(0 if is_live else 1, 0, next(pool.seq)), endpoint=endpoint_id, user=user_id,
                live=is_live, got=asyncio.get_running_loop().create_future())
    pool.waiting.append(w)
    try:
        while not w.got.done():
            await pool.pump(endpoint_id)
            if not w.got.done():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(w.got), POLL_SECONDS)
    except BaseException:
        if w.got.done() and not w.got.cancelled() and w.got.result():
            await pool.release(endpoint_id, user_id, w.got.result())
        raise
    finally:
        pool.waiting.remove(w)
    got = w.got.result()
    token = _HELD.set(True)
    try:
        yield
    finally:
        _HELD.reset(token)
        if got:
            await pool.release(endpoint_id, user_id, got)
