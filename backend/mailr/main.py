"""The API process: REST API, SSE, and the built web UI."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from mailr.config import get_settings, server_version
from mailr.core.auth import Principal, current
from mailr.core.events import hub

# The page headers live in core/webheaders.json; the development and test servers read it too
# (frontend/vite.config.ts, pageHeaders). shell_csp stays importable from here.
from mailr.core.webheaders import shell_csp
from mailr.db import get_db
from mailr.i18n import ApiError

log = logging.getLogger("mailr")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from mailr.core import errors, logsetup
    from mailr.core.users import bootstrap

    if not logsetup.configured():
        # `mailr serve` set up the logs before uvicorn started (core/logsetup.py); an app started
        # another way (tests, the docs screenshots) keeps the plain format.
        logging.basicConfig(level=get_settings().log_level, format=logsetup.TEXT_FORMAT)
        for noisy in logsetup.NOISY:
            logging.getLogger(noisy).setLevel(logging.WARNING)
    errors.init("app")
    # Model calls from this process are live ones: someone waits for them (intel/slots.py).
    from mailr.intel import slots

    slots.PROCESS_INTERACTIVE = True
    # The outbound guard's infrastructure addresses, looked up once in the background now, so no
    # request waits for names that do not exist on this deployment (core/netguard.py _infra_ips).
    from mailr.core import netguard

    asyncio.get_running_loop().run_in_executor(None, netguard.refresh_infra_cache)
    # Only the API creates the first admin: it alone holds MAILR_BOOTSTRAP_ADMIN_PASSWORD.
    await bootstrap(first_admin=True)
    # Secrets written before they were bound to their row and field are rewritten once.
    try:
        from mailr.core.crypto import upgrade_ciphertexts
        from mailr.db import sessionmaker

        async with sessionmaker()() as db:
            n = await upgrade_ciphertexts(db)
        if n:
            logging.getLogger("mailr").info("bound %d stored secret(s) to their record", n)
    except Exception as e:  # noqa: BLE001
        logging.getLogger("mailr").warning("could not upgrade stored secrets: %s", e)
    # Assistant turns left "running" by a previous process can never finish: mark them
    # interrupted so the chat offers Retry (agent/loop.py).
    try:
        from mailr.agent.loop import sweep_interrupted
        from mailr.db import sessionmaker

        async with sessionmaker()() as db:
            n = await sweep_interrupted(db)
        if n:
            logging.getLogger("mailr").info("marked %d interrupted assistant turn(s)", n)
    except Exception as e:  # noqa: BLE001
        logging.getLogger("mailr").warning("could not sweep interrupted assistant turns: %s", e)
    hub.start()
    mgr = getattr(app.state, "mcp_manager", None)
    if mgr is None:
        yield
    else:
        async with mgr.run():
            yield


# ---------------- live-update streams ----------------

_STREAM_CLOSE: dict = {"type": "__close__"}
_STREAMS: dict[int, list[asyncio.Queue]] = {}


def _open_stream(user_id: int, q: asyncio.Queue) -> None:
    """Track a user's open streams; past MAILR_SSE_MAX_PER_USER the oldest one is ended (a
    forgotten tab), so a new tab always gets live updates."""
    streams = _STREAMS.setdefault(user_id, [])
    streams.append(q)
    limit = max(1, int(getattr(get_settings(), "sse_max_per_user", 12) or 12))
    while len(streams) > limit:
        old = streams.pop(0)
        try:
            old.put_nowait(_STREAM_CLOSE)
        except asyncio.QueueFull:
            pass


def _static_file(root: Path, path: str) -> bool:
    """A file of the built web app at this path. A path the file system cannot take (too long, a
    NUL byte) is none, never a 500: the app's page answers it (core/helpdocs.py has the same guard)."""
    from mailr.core.helpdocs import _too_long

    if "\x00" in path or _too_long(path):
        return False
    try:
        f = root / path
        return f.is_file() and root in f.resolve().parents
    except (OSError, ValueError):
        return False


# How often an open stream checks that its session is still signed in, in seconds.
_STREAM_RECHECK = 60.0


async def _stream_still_signed_in(user_id: int, sid: str | None) -> bool:
    from sqlalchemy import select

    from mailr.db import sessionmaker
    from mailr.models import Session, User

    async with sessionmaker()() as db:
        u = await db.get(User, user_id)
        if u is None or u.disabled:
            return False
        if sid is None:
            return True
        s = (await db.execute(select(Session.expires_at).where(Session.id == sid))).scalar()
        return s is not None and s > datetime.now(UTC)


def _close_stream(user_id: int, q: asyncio.Queue) -> None:
    streams = _STREAMS.get(user_id)
    if streams is None:
        return
    if q in streams:
        streams.remove(q)
    if not streams:
        _STREAMS.pop(user_id, None)


# ---------------- request size ----------------

def _upload_limits() -> list[tuple[str, str, int]]:
    """(method, path prefix, limit) for routes that take large uploads and check their own,
    larger limit while they read. Everything else gets MAILR_MAX_REQUEST_BYTES."""
    import os

    slack = 1024 * 1024  # multipart framing around the file
    imports = int(os.environ.get("MAILR_MAX_IMPORT_BYTES", str(20 * 1024**3)))
    speakr = int(os.environ.get("MAILR_SPEAKR_MAX_UPLOAD_MB", "1024")) * 1024 * 1024
    apk = 300 * 1024 * 1024  # core/phone_apps.MAX_BYTES
    attachment = int(getattr(get_settings(), "max_attachment_bytes", 35 * 1024 * 1024))
    return [
        ("POST", "/api/v1/uploads", attachment + slack),
        ("POST", "/api/v1/connectors/uploads", imports + slack),
        ("POST", "/api/v1/speakr/recordings", speakr + slack),
        *(("POST", f"/api/v1/admin/phone-apps/{app}", apk + slack) for app in ("mailr", "notes")),
    ]


def body_limit(method: str, path: str) -> int:
    for m, prefix, limit in _upload_limits():
        if method == m and path.rstrip("/") == prefix:
            return limit
    return int(getattr(get_settings(), "max_request_bytes", 50 * 1024 * 1024) or 50 * 1024 * 1024)


class _TooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(413, "the request is too large")


class BodySizeLimit:
    """Refuses request bodies over the route's limit with 413: by Content-Length before the
    handler runs, and by counting a streamed (chunked) body as it arrives."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = body_limit(scope.get("method", "GET"), scope.get("path", ""))
        headers = dict(scope.get("headers") or [])
        try:
            declared = int(headers.get(b"content-length", b"0") or 0)
        except ValueError:
            declared = 0
        if declared > limit:
            await _too_large(scope, receive, send)
            return
        seen = 0
        exceeded = False
        replaced = False

        async def counted():
            nonlocal seen, exceeded
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    exceeded = True
                    raise _TooLarge()
            return msg

        async def guarded_send(msg):
            # Once the body went over the limit, whatever the app answers (middleware between
            # here and the route may turn the error into a 400 or a 500) becomes the 413.
            nonlocal replaced
            if not exceeded:
                await send(msg)
                return
            if msg["type"] == "http.response.start" and not replaced:
                replaced = True
                await _too_large(scope, receive, send)

        try:
            await self.app(scope, counted, guarded_send)
        except Exception:
            if not exceeded:
                raise
            if not replaced:
                replaced = True
                await _too_large(scope, receive, send)


async def _too_large(scope, receive, send) -> None:
    # Made outside security_headers (BodySizeLimit wraps it): the base headers go on here.
    from mailr.core import webheaders

    resp = JSONResponse({"detail": "the request is too large"}, status_code=413,
                        headers={**webheaders.all_responses(), "Cache-Control": "no-store", "Connection": "close"})
    await resp(scope, receive, send)


# ---------------- page security headers ----------------

_IMG_DIRECT: tuple[float, bool] = (0.0, False)


async def _remote_images_direct() -> bool:
    """True when the instance image proxy is off, so the reader (a srcdoc frame, which inherits
    this page's policy) loads allowed remote images straight from their https:// address.
    Cached for a minute."""
    import time

    global _IMG_DIRECT
    at, direct = _IMG_DIRECT
    if time.monotonic() - at < 60:
        return direct
    try:
        from mailr.api.admin_routes import get_instance_setting
        from mailr.db import sessionmaker

        async with sessionmaker()() as db:
            from mailr.core.egress import required

            direct = not bool(await get_instance_setting(db, "image_proxy", True)) and not required()
    except Exception:  # noqa: BLE001  no database (static pages in tests): the strict policy
        direct = False
    _IMG_DIRECT = (time.monotonic(), direct)
    return direct


def create_app() -> FastAPI:
    s = get_settings()
    # The interactive API docs and the OpenAPI document are routes of their own
    # (api/apidocs_routes.py): only for a signed-in person who may make API tokens, with Swagger UI
    # served from this server.
    from mailr.core import publicid
    from mailr.core.publicid_http import PublicIdJSONResponse, PublicIdMiddleware

    app = FastAPI(title="Mailr", version=server_version(), lifespan=lifespan, default_response_class=PublicIdJSONResponse,
                  docs_url=None, openapi_url=None, redoc_url=None)

    from mailr.api import (
        admin_routes,
        apidocs_routes,
        auth_routes,
        mail_read,
        mail_write,
        org_routes,
        tokens_routes,
    )

    for r in (auth_routes, mail_read, mail_write, org_routes, tokens_routes, admin_routes, apidocs_routes):
        app.include_router(r.router, prefix="/api/v1")

    # Optional feature routers owned by other modules; absent modules are skipped.
    for mod in ("mailr.api.avatar_routes", "mailr.api.account_picture_routes", "mailr.api.native_auth", "mailr.api.connector_routes", "mailr.api.calendar_migration_routes", "mailr.api.permission_routes", "mailr.api.model_prefs", "mailr.api.access_routes", "mailr.api.group_calendar_routes", "mailr.api.calendar_share_routes", "mailr.api.phone_routes", "mailr.api.onboarding_routes", "mailr.api.invite_routes", "mailr.api.managed_routes", "mailr.api.phone_app_routes", "mailr.intel.routes", "mailr.intel.deadlines", "mailr.intel.spam", "mailr.agent.routes", "mailr.agent.transcribe", "mailr.calendar.routes", "mailr.calendar.prep_routes", "mailr.calendar.webcal", "mailr.api.import_routes", "mailr.api.screener_routes", "mailr.calendar.reminders", "mailr.api.push_routes", "mailr.api.layout_routes", "mailr.api.template_routes", "mailr.api.mailgroup_routes", "mailr.knowledge.routes", "mailr.assistant.routes", "mailr.assistant.trusted_routes", "mailr.api.live_routes", "mailr.contacts.routes", "mailr.websearch.routes", "mailr.notes.share_routes", "mailr.notes.template_routes", "mailr.notes.presence", "mailr.notes.routes", "mailr.tables.calendar", "mailr.tables.routes",
                "mailr.integrations.speakr.routes", "mailr.integrations.speakr.webhook",
                "mailr.api.today_routes", "mailr.api.activity_routes", "mailr.calendar.household", "mailr.api.i18n_routes",
                "mailr.api.error_routes"):
        try:
            m = __import__(mod, fromlist=["router"])
            app.include_router(m.router, prefix="/api/v1")
        except ModuleNotFoundError as e:
            if e.name != mod:
                raise
            log.info("optional router %s not present", mod)

    # Development only: the scripted model of the demo instance (mailr/dev/demo_model.py, tools/demo).
    if s.dev and os.environ.get("MAILR_DEMO_ASSISTANT") == "1":
        from mailr.dev import demo_model

        app.include_router(demo_model.router)

    _mount_mcp(app)

    @app.get("/api/v1/health")
    async def health():
        """Liveness: the web process answers."""
        return {"ok": True}

    async def _health_detail(request: Request) -> bool:
        """The health endpoints answer anyone with the bare verdict; the per-check
        details (counts, sync and export state, which egress failure) only with the monitor
        token (MAILR_HEALTH_TOKEN, header X-Health-Token) or a signed-in admin's session."""
        import hmac

        want = (get_settings().health_token or "").encode()
        got = request.headers.get("x-health-token", "").encode()
        if want and got and hmac.compare_digest(got, want):
            return True
        from mailr.core.auth import SESSION_COOKIE, _session_principal

        raw = request.cookies.get(SESSION_COOKIE)
        if not raw:
            return False
        from mailr.db import session_scope

        async with session_scope() as db:
            p = await _session_principal(db, raw)
            return bool(p is not None and p.user.role == "admin")

    @app.get("/api/v1/health/live")
    async def health_live(request: Request):
        """Liveness for the container's healthcheck: 200 while the web process, the database and
        the mail store answer, 503 otherwise. No sign-in; the per-check details only as for
        readiness. Backups, sync and the worker are readiness (/health/ready), for monitors."""
        from mailr.core import health as checks

        result = checks.public(await checks.live())
        if not await _health_detail(request):
            result = {"ok": result["ok"], "checked_at": result.get("checked_at")}
        return JSONResponse(result, status_code=200 if result["ok"] else 503)

    @app.get("/api/v1/health/ready")
    async def health_ready(request: Request):
        """Readiness for uptime monitors: 200 when every required check passes, 503 otherwise.
        No sign-in. Anyone gets {ok, checked_at}; the monitor token (X-Health-Token) or an
        admin's session also gets each check's {ok, detail, required} (counts only, never
        names). Cached for 20 seconds."""
        from mailr.core import health as checks

        result = checks.public(await checks.ready())
        if not await _health_detail(request):
            result = {"ok": result["ok"], "checked_at": result.get("checked_at")}
        return JSONResponse(result, status_code=200 if result["ok"] else 503)

    @app.get("/api/v1/health/egress")
    async def health_egress(request: Request):
        """For a keyword monitor: 200 with "state":"ok" while the VPN egress for remote content
        works (the worker's last check), 503 with "state":"failing" otherwise.
        The kind of failure (down, leak, stale, off) and its detail need the monitor token
        (X-Health-Token) or an admin's session. Never addresses: admins see the exit address in
        Settings > Admin."""
        from mailr.core import egress

        ok, state, detail = await egress.view()
        if not await _health_detail(request):
            return JSONResponse({"ok": ok, "state": "ok" if ok else "failing"}, status_code=200 if ok else 503)
        return JSONResponse({"ok": ok, "state": state, "detail": detail}, status_code=200 if ok else 503)

    @app.get("/api/v1/events")
    async def events(request: Request, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
        # The stream lives for hours: end the sign-in lookup's transaction now, or it stays
        # "idle in transaction" for the whole connection (holding back vacuum and blocking
        # CREATE INDEX CONCURRENTLY).
        if p.is_token:
            # The live stream carries approval previews, outgoing-mail subjects and new-mail
            # notifications for the whole account; tokens (which may be limited to some accounts
            # or data classes) do not get it.
            raise ApiError(403, "live.stream_apps_only")
        user_id = p.user_id
        sid = p.session.id if p.session else None
        await db.close()
        q = hub.subscribe(user_id)
        _open_stream(user_id, q)

        async def gen():
            # Unnamed events: EventSource.onmessage only sees the default event type.
            checked = time.monotonic()
            try:
                yield {"data": json.dumps({"type": "hello", "user": user_id})}
                while True:
                    if await request.is_disconnected():
                        break
                    if time.monotonic() - checked >= _STREAM_RECHECK:
                        # A revoked session (sign-out, Sign out everywhere, a password change) or a
                        # disabled account ends the stream within a minute.
                        checked = time.monotonic()
                        if not await _stream_still_signed_in(user_id, sid):
                            break
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=25)
                        if ev is _STREAM_CLOSE:
                            break
                        yield {"data": json.dumps(publicid.out(ev), default=str)}
                    except TimeoutError:
                        # A data event, not a comment: the page's watchdog sees it and knows
                        # the stream is alive (a proxy can hold a dead stream open silently).
                        yield {"data": '{"type": "ping"}'}
            finally:
                hub.unsubscribe(user_id, q)
                _close_stream(user_id, q)

        return EventSourceResponse(gen(), headers={"X-Accel-Buffering": "no"})

    from mailr.core.idempotency import IdempotencyMiddleware
    from mailr.core.live import LiveClientMiddleware

    app.add_middleware(LiveClientMiddleware)
    app.add_middleware(IdempotencyMiddleware)
    # Outermost: ids are translated before anything else sees the request, and after the rest
    # (idempotency replays included) has produced the response.
    app.add_middleware(PublicIdMiddleware)

    from mailr.core import webheaders

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        for k, v in webheaders.all_responses().items():
            resp.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            # Mail and account data must not sit in shared or disk caches.
            resp.headers.setdefault("Cache-Control", "no-store")
        if not request.url.path.startswith("/api/"):
            for k, v in webheaders.pages().items():
                resp.headers.setdefault(k, v)
            resp.headers.setdefault("Content-Security-Policy", shell_csp(await _remote_images_direct()))
        return resp

    from mailr.core.appversion import AndroidMinVersion

    app.add_middleware(AndroidMinVersion)  # an Android build below MAILR_ANDROID_MIN_VERSION: 426
    from mailr.core.proxies import ForwardedWatch

    app.add_middleware(ForwardedWatch)  # X-Forwarded-For from an untrusted peer: logged once
    from mailr.core.compress import ApiGZip

    app.add_middleware(ApiGZip)  # the API's JSON, gzipped (core/compress.py)
    from mailr.core.slowlog import SlowRequestLog

    app.add_middleware(SlowRequestLog)  # API requests over MAILR_SLOW_REQUEST_MS: one warning line
    from mailr.core.uploadgate import UploadGate

    # Uploads: the caller is authenticated and given a slot before the body is read.
    app.add_middleware(UploadGate)
    from mailr.core.outbound import ScopeMiddleware

    # Every request in a taint scope of its own (core/outbound.py).
    app.add_middleware(ScopeMiddleware)
    # Outermost of all: a request body over the limit is refused before any handler reads it.
    app.add_middleware(BodySizeLimit)

    from mailr.i18n import ApiError

    @app.exception_handler(ApiError)
    async def coded_error(request: Request, exc: ApiError):
        # A coded API error: English `detail` as before, plus `code` and `params`.
        return JSONResponse(exc.body(), status_code=exc.status_code, headers=exc.headers)

    from fastapi.exceptions import ResponseValidationError

    @app.exception_handler(ResponseValidationError)
    async def contract_mismatch(request: Request, exc: ResponseValidationError):
        # A typed route's answer does not match its response model (api/contract). In development
        # and the tests that is a 500, so the mismatch is fixed before release; in production the
        # answer goes out as the route built it, and the mismatch is logged and reported.
        # Where and what, never the value itself (it is the user's data: a subject, a note).
        errs = [{k: e[k] for k in ("type", "loc", "msg") if k in e} for e in exc.errors()[:5]]
        if s.dev:
            log.error("response does not match its model on %s: %s", request.url.path, errs)
            return JSONResponse({"detail": f"response does not match its model: {errs}"}, status_code=500)
        log.warning("response does not match its model on %s: %s", request.url.path, errs)
        from mailr.core import errors

        errors.capture(RuntimeError(f"response does not match its model on {request.url.path}: {errs}"))
        return PublicIdJSONResponse(exc.body)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        from mailr.core import errors

        errors.capture(exc)
        return JSONResponse({"detail": "internal error" if not s.dev else f"{type(exc).__name__}: {exc}"},
                            status_code=500)

    # The user guide, for signed-in people only (core/helpdocs.py). Before the web app's
    # catch-all, so the app never answers for /help.
    from mailr.core import helpdocs

    @app.api_route("/help", methods=["GET", "HEAD"], include_in_schema=False)
    async def help_root():
        return RedirectResponse("/help/", status_code=308, headers={"X-Robots-Tag": helpdocs.ROBOTS})

    @app.api_route("/help/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def help_page(path: str, request: Request):
        return await helpdocs.serve(path, request)

    if STATIC.exists():
        from mailr.core import webstatic

        # Hashed names: precompressed copies, kept for a year (core/webstatic.py).
        app.mount("/assets", webstatic.HashedAssets(directory=STATIC / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str, request: Request):
            if path.startswith("api/") or path == "api":
                # Unknown API paths are API errors, not the web app's page.
                return JSONResponse({"detail": "not found"}, status_code=404)
            # Page loads through a LAN alias move to the canonical address (MAILR_BASE_URL), so
            # cookies, OIDC redirects and links all use one origin.
            base = httpx.URL(s.base_url)
            host = (request.headers.get("host") or "").split(":")[0].lower()
            if s.canonical_redirect and base.host and host and host != base.host.lower():
                target = s.base_url.rstrip("/") + "/" + path + (f"?{request.url.query}" if request.url.query else "")
                return RedirectResponse(target, status_code=308)
            if path and _static_file(STATIC, path):
                return webstatic.top_level(STATIC / path, request)
            moved = await _legacy_mail_link(path, request) or await _legacy_approval_link(path, request)
            if moved:
                return RedirectResponse(moved, status_code=302)
            return webstatic.top_level(STATIC / "index.html", request)

    return app


_LEGACY_MAIL = __import__("re").compile(r"^mail/([^/]+)/(\d+)$")


async def _legacy_mail_link(path: str, request: Request) -> str | None:
    """Links made before public ids (calendar events, saved citations, bookmarks) name the
    internal id: /mail/<view>/<id>?m=<id>. Move them to the public form, but only for the
    signed-in owner, so the redirect cannot map internal ids for anyone else."""
    from mailr.core import publicid
    from mailr.core.auth import SESSION_COOKIE, _session_principal
    from mailr.db import sessionmaker
    from mailr.models import Conversation, Message

    m = _LEGACY_MAIL.match(path)
    mq = request.query_params.get("m", "")
    if not publicid.enabled() or not m or int(m.group(2)) >= 1 << 52:
        return None
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    async with sessionmaker()() as db:
        p = await _session_principal(db, raw)
        conv = await db.get(Conversation, int(m.group(2))) if p else None
        if conv is None or conv.user_id != p.user_id:
            return None
        params = dict(request.query_params)
        if mq.isdigit() and int(mq) < 1 << 52:
            msg = await db.get(Message, int(mq))
            if msg is not None and msg.conversation_id == conv.id:
                params["m"] = str(publicid.encode("message", msg.id))
            else:
                params.pop("m", None)
    from urllib.parse import urlencode

    target = f"/mail/{m.group(1)}/{publicid.token('conversation', conv.id)}"
    return target + (f"?{urlencode(params)}" if params else "")


_LEGACY_APPROVAL = __import__("re").compile(r"^approvals/(\d{1,15})$")


async def _legacy_approval_link(path: str, request: Request) -> str | None:
    """Approval links sent before approvals had public ids (push notifications, agent and MCP
    results) name the internal id: /approvals/<id>. Move them to the public form, for the
    signed-in owner only (as _legacy_mail_link), whatever the approval's status."""
    from mailr.core import publicid
    from mailr.core.auth import SESSION_COOKIE, _session_principal
    from mailr.db import sessionmaker
    from mailr.models import PendingAction

    m = _LEGACY_APPROVAL.match(path)
    if not publicid.enabled() or not m or int(m.group(1)) >= 1 << 52:
        return None
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    async with sessionmaker()() as db:
        p = await _session_principal(db, raw)
        pa = await db.get(PendingAction, int(m.group(1))) if p else None
        if pa is None or pa.user_id != p.user_id:
            return None
    return f"/approvals/{publicid.token('approval', pa.id)}"


def _mount_mcp(app: FastAPI) -> None:
    """Streamable-HTTP MCP endpoint at /mcp. Each request's bearer token is forwarded to the
    REST tool API in-process, so scopes, local_only, auditing and the send gate all apply."""
    import httpx
    from mcp.server.streamable_http_manager import (
        StreamableHTTPASGIApp,
        StreamableHTTPSessionManager,
    )

    from mailr.mcp.server import build_server

    server = build_server(url="http://mailr.internal", token="", transport=httpx.ASGITransport(app=app))
    mgr = StreamableHTTPSessionManager(app=server, stateless=True, json_response=True)
    app.state.mcp_manager = mgr
    async def _valid_token(raw: str) -> bool:
        from mailr.core.auth import _token_principal
        from mailr.db import sessionmaker

        try:
            async with sessionmaker()() as db:
                return await _token_principal(db, raw) is not None
        except Exception:  # noqa: BLE001
            return False

    class _MCPEndpoint:
        """Raw ASGI app (a class, so Starlette does not wrap it as a request handler)."""

        def __init__(self) -> None:
            self.inner = StreamableHTTPASGIApp(mgr)

        async def __call__(self, scope, receive, send):
            # Bearer tokens only: the endpoint is for agents, never for browser sessions.
            auth = dict(scope.get("headers") or []).get(b"authorization", b"").decode()
            if not auth.lower().startswith("bearer mlr_") or not await _valid_token(auth[7:].strip()):
                # Checked here, before the MCP session is created: unknown tokens must not
                # allocate anything (each distinct token string used to create a cached client).
                resp = JSONResponse({"detail": "send a valid mailr API token: Authorization: Bearer mlr_..."},
                                    status_code=401, headers={"WWW-Authenticate": "Bearer"})
                await resp(scope, receive, send)
                return
            await self.inner(scope, receive, send)

    app.add_route("/mcp", _MCPEndpoint(), methods=["GET", "POST", "DELETE"], include_in_schema=False)


app = create_app()
