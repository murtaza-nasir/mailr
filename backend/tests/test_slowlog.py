"""The slow-request log (core/slowlog.py, 8 Oct 2026): one warning line per API request slower
than MAILR_SLOW_REQUEST_MS, with the route template, method, status, total time, DB time and
query count, and the pool wait; never the raw path, the query string or mail content. Pool
timeouts get a warning line of their own."""

from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import exc as sa_exc
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from mailr.config import get_settings
from mailr.core import slowlog
from mailr.db import sessionmaker


def _slow_app(threshold_ms: int) -> FastAPI:
    app = FastAPI()

    @app.get("/api/v1/test/slow/{item_id}")
    async def slow(item_id: str, q: str = ""):
        async with sessionmaker()() as db:
            await db.execute(text("SELECT pg_sleep(0.3)"))
            await db.execute(text("SELECT 1"))
        return {"ok": True, "subject": "The Perfect Dive Watch?"}

    @app.get("/api/v1/test/fast")
    async def fast():
        async with sessionmaker()() as db:
            await db.execute(text("SELECT 1"))
        return {"ok": True}

    @app.get("/api/v1/test/broken/{item_id}")
    async def broken(item_id: str):
        await asyncio.sleep(0.2)
        raise RuntimeError("boom")

    app.add_middleware(slowlog.SlowRequestLog, threshold_ms=threshold_ms)
    return app


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "mailr.slow"]


async def test_a_slow_request_is_one_line_with_the_route_template_and_db_time(caplog):
    caplog.set_level(logging.WARNING, logger="mailr.slow")
    async with AsyncClient(transport=ASGITransport(app=_slow_app(200), raise_app_exceptions=False),
                           base_url="http://test") as c:
        assert (await c.get("/api/v1/test/fast")).status_code == 200
        assert _lines(caplog) == []
        r = await c.get("/api/v1/test/slow/c_8f3a2b91", params={"q": "subject:\"The Perfect Dive Watch\""})
        assert r.status_code == 200
        (line,) = _lines(caplog)
        assert line.startswith("slow request: GET /api/v1/test/slow/{item_id} 200 in ")
        # Two statements, about 300 ms in the database; no pool wait on an idle pool.
        db_ms = int(line.split("; db ")[1].split(" ms")[0])
        assert db_ms >= 280 and " in 2 queries" in line
        assert line.endswith("pool wait 0 ms") or int(line.rsplit("pool wait ", 1)[1].split()[0]) < 100
        # Never the raw path, the query string or content.
        for secret in ("c_8f3a2b91", "Dive", "subject", "q="):
            assert secret not in line
        # A request that fails is logged with status 500.
        caplog.clear()
        assert (await c.get("/api/v1/test/broken/123")).status_code == 500
        (line,) = _lines(caplog)
        assert line.startswith("slow request: GET /api/v1/test/broken/{item_id} 500 in ") and "123" not in line


async def test_waiting_for_a_pool_connection_shows_and_a_pool_timeout_is_a_warning(caplog):
    caplog.set_level(logging.WARNING, logger="mailr.slow")
    eng = create_async_engine(get_settings().database_url, pool_size=1, max_overflow=0, pool_timeout=0.3,
                              poolclass=slowlog.TimedPool)
    slowlog.instrument(eng)
    try:
        app = FastAPI()

        @app.get("/api/v1/test/pool/{n}")
        async def pooled(n: int):
            async with eng.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return {"ok": True}

        app.add_middleware(slowlog.SlowRequestLog, threshold_ms=100)
        held = await eng.connect()
        await held.execute(text("SELECT 1"))

        async def release_later():
            await asyncio.sleep(0.2)
            await held.close()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            task = asyncio.create_task(release_later())
            assert (await c.get("/api/v1/test/pool/1")).status_code == 200
            await task
        (line,) = _lines(caplog)
        assert line.startswith("slow request: GET /api/v1/test/pool/{n} 200 in ")
        assert int(line.rsplit("pool wait ", 1)[1].split()[0]) >= 150

        # The only connection stays taken: the next checkout times out, a warning line of its own.
        caplog.clear()
        before = slowlog.pool_timeouts
        held = await eng.connect()
        try:
            with pytest.raises(sa_exc.TimeoutError):
                async with eng.connect():
                    pass
        finally:
            await held.close()
        assert slowlog.pool_timeouts == before + 1
        (line,) = _lines(caplog)
        assert line.startswith("db pool timeout after ") and "pool 1 + overflow 0, 1 checked out" in line
    finally:
        await eng.dispose()


async def test_the_app_logs_slow_requests_by_default(app):
    assert get_settings().slow_request_ms == 2000
    assert any(m.cls is slowlog.SlowRequestLog for m in app.user_middleware)
    from mailr.db import engine

    assert isinstance(engine().sync_engine.pool, slowlog.TimedPool)


async def test_the_real_routes_give_their_template(app, client, caplog, monkeypatch):
    """Through the whole middleware stack (public ids, idempotency, ...): the line names
    /api/v1/conversations/{cid}, not the id the client sent."""
    caplog.set_level(logging.WARNING, logger="mailr.slow")
    mw = next(m for m in app.user_middleware if m.cls is slowlog.SlowRequestLog)
    monkeypatch.setitem(mw.kwargs, "threshold_ms", 1)
    app.middleware_stack = app.build_middleware_stack()
    try:
        await client.get("/api/v1/conversations/987654321", params={"q": "secret words"})
        line = next(x for x in _lines(caplog) if "/conversations/" in x)
        assert line.startswith("slow request: GET /api/v1/conversations/{cid} 404 in ")
        assert "987654321" not in line and "secret" not in line
    finally:
        monkeypatch.undo()
        app.middleware_stack = app.build_middleware_stack()
