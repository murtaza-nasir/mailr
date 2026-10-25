"""Integration tests run against the dev services (deploy/dev/docker-compose.yml).

They use their OWN database (MAILR_TEST_DB, by default `mailr_wt_<checkout folder>__test`), recreated at the start of every
session as a copy of the migrated template (mailr/dev/testdb.py), and their own data directory,
so a running dev worker never picks up test connectors or jobs. Each test gets a fresh user;
Stalwart accounts get unique names.

Parallel runs (pytest -n auto, pytest-xdist): every worker gets its own database
(`<test db>_gw0`, …) and data directory (`<data dir>-gw0`). Tests marked `serial` run in a final
pass of their own: `pytest -p no:xdist -m serial`, as .github/workflows/test.yml runs them."""

import asyncio
import base64
import os
import uuid
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

from dotenv import dotenv_values

_env = {**dotenv_values(os.path.join(os.path.dirname(__file__), "..", ".env")), **os.environ}
_base_url = _env.get("MAILR_DATABASE_URL", "postgresql+asyncpg://mailr:mailr@localhost:5432/mailr")
_db_root, _, _db_name = _base_url.rpartition("/")
_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")

# Without MAILR_TEST_DB the name is still this checkout's own, so two checkouts of the repository
# never drop each other's test database: mailr_wt_<folder>__test.
def _default_test_db() -> str:
    from mailr.dev.testdb import worktree_slug

    return f"mailr_wt_{worktree_slug()}__test"


# Without a backend/.env the tests use the dev services as deploy/dev/docker-compose.yml defines
# them (its development-only service account). A .env or the environment overrides each one.
_DEV_SERVICES = {
    "MAILR_DEV": "true",
    "MAILR_SECRET_KEY": base64.b64encode(os.urandom(32)).decode(),  # a new one per test process
    "MAILR_STALWART_URL": "http://localhost:18080",
    "MAILR_STALWART_ADMIN_USER": "mailr-svc@mailr.internal",
    "MAILR_STALWART_ADMIN_PASSWORD": "dev-svc-pass",
    "MAILR_STALWART_DOMAIN": "mailr.internal",
}
for _k, _v in _DEV_SERVICES.items():
    if not _env.get(_k):
        os.environ[_k] = _v


def _store(key: str) -> str:
    return _env.get(key) or os.environ[key]


# The Stalwart calendar tests (*_stalwart.py) run against the suite's store server (the dev
# Stalwart) unless MAILR_TEST_STALWART_URL/_ADMIN name another or MAILR_TEST_WITHOUT_STALWART_DAV=1
# (tests/dev_services.py).
from dev_services import stalwart_dav_defaults  # noqa: E402

stalwart_dav_defaults(_env, _store("MAILR_STALWART_URL"), _store("MAILR_STALWART_ADMIN_USER"),
                      _store("MAILR_STALWART_ADMIN_PASSWORD"))

TEST_DB = (os.environ.get("MAILR_TEST_DB") or _default_test_db()) + (f"_{_WORKER}" if _WORKER else "")
os.environ["MAILR_DATABASE_URL"] = f"{_db_root}/{TEST_DB}"
os.environ["MAILR_DATA_DIR"] = os.environ.get("MAILR_TEST_DATA_DIR", "./.data-test") + (f"-{_WORKER}" if _WORKER else "")
os.environ["MAILR_BOOTSTRAP_ADMIN_EMAIL"] = ""
# Small connection pools: many workers (and agents) share one dev Postgres.
os.environ.setdefault("MAILR_DB_POOL_SIZE", "2")
os.environ.setdefault("MAILR_DB_MAX_OVERFLOW", "8")
# The dev services (fake upstream IMAP/SMTP, Radicale) run on this machine.
os.environ.setdefault("MAILR_OUTBOUND_ALLOW", "localhost,127.0.0.1,::1,ntfy.invalid")
# Authentication-Results in the fixtures come from these receivers (core/mime.trusted_authserv_ids).
os.environ.setdefault("MAILR_TRUSTED_AUTHSERV_IDS", "mx,mx.example,mx.example.com")
# Test users hash their passwords with the cheapest Argon2 parameters (core/auth.py; needs MAILR_DEV).
os.environ.setdefault("MAILR_TEST_CHEAP_PASSWORD_HASHES", "true")


def _recreate_test_db() -> None:
    from mailr.dev import testdb

    asyncio.run(testdb.clone(_base_url, TEST_DB))


def _is_xdist_controller(config) -> bool:
    return not _WORKER and bool(getattr(config.option, "numprocesses", None))


_radicale_stop = None


def _internal_names() -> set[str]:
    """The single-label infrastructure names (MAILR_INTERNAL_HOSTS: egress, stalwart, postgres, …).
    netguard looks them up whenever its infrastructure cache is cold (each worker, and after every
    test that resets the cache). On a LAN each lookup took about 3.2 s to fail, so the first
    netguard check of a worker cost about 30 s. Names with a dot are left to real DNS."""
    from mailr.config import Settings, get_settings

    try:
        names = (get_settings().internal_hosts or "").split(",")
    except Exception:  # noqa: BLE001  a test's broken environment: the default names
        names = Settings.model_fields["internal_hosts"].default.split(",")
    return {n.strip().lower() for n in names if n.strip() and "." not in n.strip()}


def _no_reserved_dns() -> None:
    """Names under the reserved test TLDs (RFC 2606/6761: .test, .invalid, .example, .localhost) never
    reach real DNS in a test run. Fakes such as speakr.test were resolved for real on every
    netguard check, and parallel runs flooded the local DNS resolver until it refused queries. They fail at once, as an unknown name; allow-listed fakes carry on (netguard
    lets an admin-listed host through when it does not resolve) and their transports are mocked.

    The single-label infrastructure names (_internal_names) fail at once too, as they do on any
    machine outside the compose network. A test that needs one to resolve maps it in its own fake
    resolver (monkeypatch socket.getaddrinfo, as test_security_outbound._fake_dns does): names the
    fake does not know fall through to this guard."""
    import socket

    real = socket.getaddrinfo
    if getattr(real, "_mailr_guard", False):
        return

    def guarded(host, *args, **kwargs):
        h = host.decode() if isinstance(host, bytes) else (host or "")
        h = h.strip().lower().rstrip(".")
        if h.endswith((".test", ".invalid", ".example", ".localhost")) and h != "localhost":
            raise socket.gaierror(socket.EAI_NONAME, f"{h}: reserved test name, not looked up")
        if h in _internal_names():
            raise socket.gaierror(socket.EAI_NONAME, f"{h}: infrastructure name, not looked up in tests")
        return real(host, *args, **kwargs)

    guarded._mailr_guard = True
    socket.getaddrinfo = guarded


_no_reserved_dns()


def pytest_sessionstart(session):
    import shutil

    from dev_services import require_radicale, require_stalwart_dav

    if stop := require_radicale() or require_stalwart_dav():
        pytest.exit(stop, returncode=3)
    if _is_xdist_controller(session.config):
        return  # the workers make their own databases
    # The dev Radicale users this process makes (u + 8 hex, the demo seed's test user) are deleted
    # at the end of the session (mailr/dev/radicale.py); MAILR_TEST_KEEP_DB=1 keeps them too.
    global _radicale_stop
    from mailr.dev import radicale

    _radicale_stop = radicale.track()
    _recreate_test_db()
    # User ids restart with the fresh database, so stale exports/uploads must go too.
    shutil.rmtree(os.environ["MAILR_DATA_DIR"], ignore_errors=True)


def pytest_sessionfinish(session, exitstatus):
    if _radicale_stop is not None:
        made = _radicale_stop()
        if made and os.environ.get("MAILR_TEST_KEEP_DB", "") not in ("1", "true"):
            from mailr.dev import radicale

            try:
                radicale.delete_users(made)
            except Exception as e:  # noqa: BLE001  never fail a run over cleanup
                print(f"could not delete {len(made)} dev Radicale users: {e}")
    # Worker databases are throwaway copies; the serial database stays for inspection.
    if _WORKER and os.environ.get("MAILR_TEST_KEEP_DB", "") not in ("1", "true"):
        import shutil

        from mailr.dev import testdb

        try:
            asyncio.run(testdb.drop(_base_url, TEST_DB))
        except Exception as e:  # noqa: BLE001  never fail a run over cleanup
            print(f"could not drop {TEST_DB}: {e}")
        shutil.rmtree(os.environ["MAILR_DATA_DIR"], ignore_errors=True)


SCALE_ENV = "MAILR_TEST_SCALE"


def _scale_selected(config) -> bool:
    """Scale tests run when asked for: `-m scale` (any marker expression that names scale, other
    than `not scale`) or MAILR_TEST_SCALE=1."""
    if os.environ.get(SCALE_ENV, "").lower() in ("1", "true", "yes"):
        return True
    expr = (config.getoption("markexpr", "") or "").replace("not scale", "")
    return "scale" in expr


def pytest_collection_modifyitems(config, items):
    """`-m serial` / `-m "not serial"` select; under xdist the serial tests are skipped
    (run them afterwards in one process: `pytest -p no:xdist -m serial`).

    Tests marked `scale` build production-sized data (test_scale_chat_search seeds 400,000 chat
    messages, about a minute) and are skipped unless asked for (_scale_selected)."""
    if not _scale_selected(config):
        skip = pytest.mark.skip(reason=f"scale: production-sized data; run with -m scale or {SCALE_ENV}=1")
        for item in items:
            if item.get_closest_marker("scale"):
                item.add_marker(skip)
    if not (_WORKER or _is_xdist_controller(config)):
        return
    skip = pytest.mark.skip(reason="serial: runs in the final single-process pass")
    for item in items:
        if item.get_closest_marker("serial"):
            item.add_marker(skip)


import pytest  # noqa: E402  (environment must be set before app imports)
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

os.environ.setdefault("MAILR_DEV", "true")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _bootstrapped():
    """bootstrap() once per process (each xdist worker, the serial pass): migrations (a no-op on
    the template copy), the Stalwart server settings and the users scan. It used to run before
    every test that took `app`, about 8 store management calls each time. A test that needs
    bootstrap's effects again (a fresh database, users made while the store was away) calls it
    itself."""
    from mailr.core import tally
    from mailr.core.users import bootstrap

    tally.STRICT = True  # an error in the mail tally's upkeep fails the test
    await bootstrap()


@pytest_asyncio.fixture
async def app(_bootstrapped):
    from mailr.main import create_app

    return create_app()


# Access policies (core/access.py): a user who is not an admin gets model features only through a
# policy of one of their groups. The test user is in TEST_GROUP, whose policy allows every model
# and turns every switch on, so the other tests see mailr as before; tests/test_access.py covers
# the restricted cases.
TEST_GROUP = "testers"


async def grant_model_access(user_id: int, group: str = TEST_GROUP) -> None:
    from sqlalchemy.dialects.postgresql import insert

    from mailr.core.access import FEATURES
    from mailr.db import sessionmaker
    from mailr.models import AccessPolicy, User

    async with sessionmaker()() as db:
        await db.execute(insert(AccessPolicy).values(
            group_name=group, description="tests: everything",
            models={t: {"any": True, "allowed": [], "default": None} for t in ("chat", "classify")},
            features={k: True for k in FEATURES}, needs={}).on_conflict_do_nothing(index_elements=["group_name"]))
        u = await db.get(User, user_id)
        u.assigned_groups = sorted({*(u.assigned_groups or []), group})
        await db.commit()


@pytest_asyncio.fixture
async def user(app, request):
    """A signed-up user with a Stalwart account. In a test or module marked `db_user`, a user in
    the database only (make_db_user)."""
    if request.node.get_closest_marker("db_user"):
        return await make_db_user()
    from mailr.core.users import create_user
    from mailr.db import sessionmaker
    from mailr.models import User

    async with sessionmaker()() as db:
        u = await create_user(db, f"t-{uuid.uuid4().hex[:8]}@example.com", "Test User",
                              password="test-password-123")
    await grant_model_access(u.id)
    async with sessionmaker()() as db:
        return await db.get(User, u.id)


async def make_db_user(email: str | None = None, name: str = "Test User", *, password: str = "test-password-123",
                       role: str = "user", model_access: bool = True):
    """A user in the database only: no Stalwart account, no built-in calendar. For tests that
    never touch the user's mail store (IMAP, JMAP, the store's DAV); about a tenth of the cost of
    create_user. Anything that needs the store fails for such a user, as it would for a user
    created while the store was unreachable."""
    from mailr.core.auth import hash_password
    from mailr.core.crypto import new_wrapped_data_key
    from mailr.db import sessionmaker
    from mailr.models import User

    email = (email or f"t-{uuid.uuid4().hex[:8]}@example.com").lower()
    async with sessionmaker()() as db:
        u = User(email=email, name=name, role=role, password_hash=hash_password(password) if password else None,
                 settings={}, email_verified=True)
        db.add(u)
        await db.flush()
        u.data_key_wrapped = new_wrapped_data_key(u.id)
        from mailr.core import tally

        await tally.start_empty(db, u.id)  # as create_user
        await db.commit()
        uid = u.id
    if model_access:
        await grant_model_access(uid)
    async with sessionmaker()() as db:
        return await db.get(User, uid)


@pytest_asyncio.fixture
async def db_user(app):
    """The `user` fixture without a mail store account (make_db_user)."""
    return await make_db_user()


async def _signed_in(app, u):
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/api/v1/auth/login", json={"email": u.email, "password": "test-password-123"})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c


@pytest_asyncio.fixture
async def db_client(app, db_user):
    """`client`, signed in as db_user."""
    async with await _signed_in(app, db_user) as c:
        yield c


@pytest_asyncio.fixture
async def client(app, user):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/login", json={"email": user.email, "password": "test-password-123"})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        yield c


def mail_date(day: int = 0, at: str = "10:00") -> str:
    """A Date header for test mail, relative to today, so no test ages as the calendar moves.

    Day 0 is two weeks ago (UTC); `raw` stamps mail with day 0 at 10:00 by default. A test places
    later mail on day 1 to 13 (yesterday at the latest, so never in the future) and older mail on a
    negative day. `at` is the time of day, "HH:MM" in UTC. Code that looks back over a window from
    now (14, 30, 60 or 90 days, follow-ups, digests, decay) sees the same ages on every run."""
    hh, mm = (int(x) for x in at.split(":"))
    d = datetime.now(UTC).date() - timedelta(days=14 - day)
    return format_datetime(datetime(d.year, d.month, d.day, hh, mm, tzinfo=UTC))


@pytest.fixture
def raw():
    def make(subject, frm="Alice <alice@example.org>", to="me@example.com", msgid=None, irt=None,
             body="Hello there", date=None, extra=""):
        msgid = msgid or f"<{uuid.uuid4().hex}@example.org>"
        date = date or mail_date()
        h = [f"From: {frm}", f"To: {to}", f"Subject: {subject}", f"Message-ID: {msgid}", f"Date: {date}",
             "MIME-Version: 1.0", "Content-Type: text/plain; charset=utf-8"]
        if irt:
            h += [f"In-Reply-To: {irt}", f"References: {irt}"]
        if extra:
            h.append(extra)
        return ("\r\n".join(h) + "\r\n\r\n" + body + "\r\n").encode()
    return make


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def _stop_connector_runners():
    """At the end of a worker's session, stop every connector task a test left running (an IMAP
    IDLE loop polls its socket in a thread). Without this, closing the session's event loop
    waited on them and pytest-timeout killed whichever test ran last on that worker."""
    yield
    from mailr.connectors import runner

    for cid in list(runner._running):
        try:
            await asyncio.wait_for(runner._stop(cid), timeout=20)
        except Exception:  # noqa: BLE001  stopping is best effort; the loop closes next
            pass
    # Any other task still pending would hold the loop open the same way: cancel it and give
    # it a moment to unwind.
    me = asyncio.current_task()
    rest = [t for t in asyncio.all_tasks() if t is not me and not t.done()]
    for t in rest:
        t.cancel()
    if rest:
        await asyncio.wait(rest, timeout=15)


@pytest_asyncio.fixture(autouse=True)
async def _model_endpoints_per_test(request):
    """Model endpoints are server-wide rows, and a worker's database lives for the whole session.
    An endpoint one test left behind was a model for every later test on that worker: a leftover
    local chat endpoint from test_diagnostics answered for local-only mail in
    test_assistant_knowledge and gave a user with no policy a summary in test_meeting_prep
    (9 Oct 2026). Every endpoint a test adds is removed after it."""
    if "app" not in request.fixturenames:
        yield
        return
    from sqlalchemy import delete, select

    from mailr.db import sessionmaker
    from mailr.models import ModelEndpoint

    async with sessionmaker()() as db:
        before = set((await db.execute(select(ModelEndpoint.id))).scalars())
    yield
    async with sessionmaker()() as db:
        q = delete(ModelEndpoint)
        if before:
            q = q.where(ModelEndpoint.id.not_in(before))
        await db.execute(q)
        await db.commit()


# Fixtures that many test modules use. Helpers shared by a few modules are in tests/helpers/, and
# test modules import them from there, never from each other.
from helpers.agent import fake_llm  # noqa: E402, F401
from helpers.helpdocs import anon, help_site  # noqa: E402, F401
from helpers.security_auth import admin_client  # noqa: E402, F401
