from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from mailr.config import get_settings
from mailr.core.slowlog import TimedPool, instrument


class Base(DeclarativeBase):
    pass


_engine = None
_sessionmaker = None
_side_engine = None
_side_sessionmaker = None
# Set inside a calendar sync (calendar/sync.py): its sessions come from the side pool.
_side: ContextVar[bool] = ContextVar("mailr_db_side_pool", default=False)
SIDE_POOL_SIZE, SIDE_MAX_OVERFLOW = 2, 8


def engine():
    global _engine, _sessionmaker
    if _engine is None:
        s = get_settings()
        _engine = create_async_engine(
            s.database_url, pool_size=s.db_pool_size, max_overflow=s.db_max_overflow, pool_pre_ping=True,
            poolclass=TimedPool,  # the slow-request log's pool wait and pool timeouts (core/slowlog.py)
            # A database error's text names no values (subjects, bodies, notes): logs and error
            # reports get the SQL only.
            hide_parameters=True,
        )
        instrument(_engine)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
        from mailr.core import tally

        tally.install()  # the mail tally's upkeep at each commit
        from mailr.core import outbound

        outbound.install_listeners()  # local-only rows raise the taint scope as they load
    return _engine


def side_engine():
    """A small second pool for calendar syncs. A request that waits for an inline sync keeps its
    own connection meanwhile; with one pool, enough waiting requests left the sync no connection
    to finish with, and every one of them waited until the pool timed out (30 s)."""
    global _side_engine, _side_sessionmaker
    if _side_engine is None:
        s = get_settings()
        _side_engine = create_async_engine(
            s.database_url, pool_size=SIDE_POOL_SIZE, max_overflow=min(SIDE_MAX_OVERFLOW, s.db_max_overflow),
            pool_pre_ping=True, poolclass=TimedPool, hide_parameters=True)
        instrument(_side_engine)
        _side_sessionmaker = async_sessionmaker(_side_engine, expire_on_commit=False)
    return _side_engine


def side_pool_slots() -> int:
    """How many syncs may hold side-pool connections at once: each takes two (its lock and one
    session), so this many can never use up the pool between them."""
    return max(1, (SIDE_POOL_SIZE + min(SIDE_MAX_OVERFLOW, get_settings().db_max_overflow)) // 2)


@contextmanager
def side_pool() -> Iterator[None]:
    """Sessions opened in this context (and in tasks it starts) use the side pool."""
    token = _side.set(True)
    try:
        yield
    finally:
        _side.reset(token)


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _side.get():
        side_engine()
        return _side_sessionmaker
    engine()
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """A session that commits on success and rolls back on error (for workers and scripts)."""
    async with sessionmaker()() as s:
        try:
            yield s
            await s.commit()
        except BaseException:
            await s.rollback()
            raise


async def get_existing[T](db: AsyncSession, model: type[T], ident: Any) -> T:
    """`db.get` for a row the caller knows exists (it was just claimed, written or read in this job).
    Raises LookupError instead of returning None, so a vanished row fails with its name."""
    row = await db.get(model, ident)
    if row is None:
        raise LookupError(f"{model.__name__} {ident} does not exist")
    return row


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with sessionmaker()() as s:
        yield s
