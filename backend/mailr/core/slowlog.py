"""The slow-request log (8 Oct 2026).

An API request that takes longer than MAILR_SLOW_REQUEST_MS (default 2000; 0 turns it off) is
logged as one warning line on the `mailr.slow` logger:

    slow request: GET /api/v1/conversations/{cid} 200 in 2345 ms; db 2100 ms in 14 queries; pool wait 0 ms

The path is the route's template, never the raw path (no ids) and never the query string, so no
search text, address or mail content reaches the log. "db" is the time spent in SQL statements
and their count (SQLAlchemy cursor events), "pool wait" the time spent waiting for a connection
from the pool (`TimedPool`). A request that waited a long time for the pool is a sign that the
pool is too small for the load, not that its own queries are slow.

A pool timeout (no connection within the pool's timeout) is logged as its own warning line, with
the route when it happened in a request and the process's running count.

Statements run by a task the request started count toward the request while they run. Server-sent
event streams (`text/event-stream`) are left out: they are meant to stay open.
"""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event
from sqlalchemy import exc as sa_exc
from sqlalchemy.pool import AsyncAdaptedQueuePool

log = logging.getLogger("mailr.slow")


@dataclass
class RequestStats:
    route: str = ""
    db_ms: float = 0.0
    queries: int = 0
    pool_wait_ms: float = 0.0
    scope: dict[str, Any] | None = field(default=None, repr=False)

    def route_now(self) -> str:
        """The route template as far as routing got (a middleware's query runs before it)."""
        if self.scope is not None:
            self.route = _route_template(self.scope)
        return self.route


_stats: ContextVar[RequestStats | None] = ContextVar("mailr_request_stats", default=None)
pool_timeouts = 0  # this process's pool timeouts since start


def current() -> RequestStats | None:
    return _stats.get()


class TimedPool(AsyncAdaptedQueuePool):
    """The engines' pool: it adds the time spent waiting for a connection to the current
    request's stats, and logs a pool timeout as a warning line."""

    def _do_get(self) -> Any:
        t = time.perf_counter()
        try:
            return super()._do_get()
        except sa_exc.TimeoutError:
            global pool_timeouts
            pool_timeouts += 1
            st = _stats.get()
            log.warning("db pool timeout%s after %d ms (pool %d + overflow %d, %d checked out; %d timeouts since start)",
                        f" in {st.route_now()}" if st is not None else "",
                        (time.perf_counter() - t) * 1000, self.size(), self._max_overflow,
                        self.checkedout(), pool_timeouts)
            raise
        finally:
            st = _stats.get()
            if st is not None:
                st.pool_wait_ms += (time.perf_counter() - t) * 1000


def instrument(engine: Any) -> None:
    """Count the statements and their time per request (an AsyncEngine or a sync Engine)."""
    sync = getattr(engine, "sync_engine", engine)
    if getattr(sync, "_mailr_slowlog", False):
        return
    sync._mailr_slowlog = True

    @event.listens_for(sync, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        if _stats.get() is not None:
            conn.info.setdefault("_mailr_t0", []).append(time.perf_counter())

    @event.listens_for(sync, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        st = _stats.get()
        starts = conn.info.get("_mailr_t0")
        if st is not None and starts:
            st.db_ms += (time.perf_counter() - starts.pop()) * 1000
            st.queries += 1

    @event.listens_for(sync, "handle_error")
    def _error(ctx):  # a failed statement never reaches after_cursor_execute
        starts = ctx.connection.info.get("_mailr_t0") if ctx.connection is not None else None
        st = _stats.get()
        if starts:
            t0 = starts.pop()
            if st is not None:
                st.db_ms += (time.perf_counter() - t0) * 1000
                st.queries += 1


def _route_template(scope: dict[str, Any]) -> str:
    """The template of the route that served the request (`/api/v1/conversations/{cid}`). The
    router puts the route on the scope; an included router's route holds only its own part of the
    path (`/conversations/{cid}`), so the part before it, the include prefix (`/api/v1`), is taken
    from the request path. "(no route)" when nothing matched."""
    route = scope.get("route")
    template = getattr(route, "path", None)
    regex = getattr(route, "path_regex", None)
    if not template:
        return "(no route)"
    if regex is None:
        return template
    segs = (scope.get("path") or "").split("/")  # ["", "api", "v1", "conversations", "123"]
    for i in range(1, len(segs)):
        if regex.match("/" + "/".join(segs[i:])):
            return "/".join(segs[:i]) + template
    return template


access_log = logging.getLogger("mailr.access")
# Polled by the container's healthcheck and the uptime monitor: their lines go to DEBUG.
QUIET_ROUTES = ("/api/v1/health", "/api/v1/health/live", "/api/v1/health/ready", "/api/v1/health/egress")


def _user_id(scope: dict[str, Any]) -> int | None:
    """The signed-in user of the request, as core/auth.py recorded it on request.state (the
    scope's "state") when it resolved the principal."""
    uid = (scope.get("state") or {}).get("user_id")
    return uid if isinstance(uid, int) else None


class SlowRequestLog:
    """ASGI middleware: measures each request; logs one access line per request (MAILR_ACCESS_LOG)
    and a warning for slow /api/ requests (module docstring)."""

    def __init__(self, app: Any, threshold_ms: int | None = None, access: bool | None = None) -> None:
        self.app = app
        from mailr.config import get_settings

        if threshold_ms is None:
            threshold_ms = get_settings().slow_request_ms
        if access is None:
            access = get_settings().access_log
        self.threshold_ms = threshold_ms
        self.access = access

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or (self.threshold_ms <= 0 and not self.access):
            await self.app(scope, receive, send)
            return
        api = scope.get("path", "").startswith("/api/")
        st = RequestStats(scope=scope)
        token = _stats.set(st)
        t = time.perf_counter()
        status = 0
        stream = False

        async def _send(message: dict[str, Any]) -> None:
            nonlocal status, stream
            if message.get("type") == "http.response.start":
                status = int(message.get("status") or 0)
                for k, v in message.get("headers") or ():
                    if k.lower() == b"content-type" and v.startswith(b"text/event-stream"):
                        stream = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except BaseException:
            status = status or 500
            raise
        finally:
            _stats.reset(token)
            ms = (time.perf_counter() - t) * 1000
            st.route_now()
            if scope.get("path", "").startswith("/assets/"):
                st.route = "/assets/*"
            if self.access:
                route = st.route
                access_log.log(logging.DEBUG if route in QUIET_ROUTES else logging.INFO,
                               "%s %s %d %d ms", scope.get("method", ""), route, status, ms,
                               extra={"method": scope.get("method", ""), "route": route, "status": status,
                                      "ms": round(ms, 1), "user": _user_id(scope), "db_ms": round(st.db_ms, 1),
                                      "queries": st.queries, "stream": stream})
            if api and self.threshold_ms > 0 and ms >= self.threshold_ms and not stream:
                log.warning("slow request: %s %s %d in %d ms; db %d ms in %d queries; pool wait %d ms",
                            scope.get("method", ""), st.route, status, ms, st.db_ms, st.queries,
                            st.pool_wait_ms)
