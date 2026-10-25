# ACT-SURFACES: api
"""The built-in calendar (calendar/builtin.py), without a calendar server: the instance setting
and its backfill, the rules that keep the connector fixed (not removable, not re-pointable,
hideable), the derived sign-in, the store account's description, the defaults rule, and the cap
on DAV requests in flight with the retry of Stalwart's "too many concurrent requests".

The end-to-end run against a real Stalwart is tests/test_builtin_calendar_stalwart.py (opt-in).

Actions covered: settings.builtin_calendars, calendar.builtin_hide.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from mailr.calendar import builtin, dav
from mailr.db import sessionmaker
from mailr.models import CalCollection, Connector, InstanceSetting, Job, User

API = "/api/v1"


async def _set(on: bool) -> None:
    async with sessionmaker()() as db:
        await builtin.set_instance_enabled(db, on)
        await db.commit()


@pytest_asyncio.fixture
async def off_after():
    """Each test leaves the instance setting off: other tests in this database must not see
    built-in connectors."""
    yield
    await _set(False)


@contextlib.asynccontextmanager
async def _admin_client(app):
    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        adm = await create_user(db, f"a-{uuid.uuid4().hex[:8]}@example.com", "Admin",
                                password="admin-password-123", role="admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(f"{API}/auth/login", json={"email": adm.email, "password": "admin-password-123"})
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        yield c


async def _builtin_of(user_id: int) -> Connector | None:
    async with sessionmaker()() as db:
        return await builtin.find(db, user_id)


async def test_off_by_default_and_admin_only(app, client, user, off_after):
    """ACT settings.builtin_calendars: off by default; only admins read or change it."""
    async with sessionmaker()() as db:
        assert await db.get(InstanceSetting, builtin.SETTING) is None or not await builtin.instance_enabled(db)
    assert await _builtin_of(user.id) is None
    assert (await client.get(f"{API}/admin/calendars")).status_code == 403
    assert (await client.put(f"{API}/admin/calendars", json={"builtin": True})).status_code == 403
    async with _admin_client(app) as adm:
        got = (await adm.get(f"{API}/admin/calendars")).json()
        assert got["builtin"] is False


async def test_turning_on_backfills_once_and_new_users_get_it(app, client, user, off_after):
    """ACT settings.builtin_calendars: on queues the backfill; it creates one connector per user,
    twice run still one; a user created afterwards gets it at creation."""
    from mailr.core.users import create_user

    async with _admin_client(app) as adm:
        r = await adm.put(f"{API}/admin/calendars", json={"builtin": True})
        assert r.status_code == 200 and r.json()["builtin"] is True
    async with sessionmaker()() as db:
        queued = (await db.execute(select(func.count()).select_from(Job).where(
            Job.kind == "calendar.builtin_backfill"))).scalar_one()
        assert queued >= 1
    await builtin.backfill()
    await builtin.backfill()
    async with sessionmaker()() as db:
        rows = (await db.execute(select(Connector).where(Connector.user_id == user.id,
                                                         Connector.builtin.is_(True)))).scalars().all()
        assert len(rows) == 1
        c = rows[0]
        assert c.kind == "caldav" and c.name == builtin.NAME and c.enabled
        # Nothing secret is stored: the sign-in is derived at use time.
        assert c.secrets_enc is None and set(c.config) == {"account"} and "@" in c.config["account"]
        u = await db.get(User, user.id)
        # A second ensure is a no-op.
        assert (await builtin.ensure_connector(db, u)).id == c.id
    async with sessionmaker()() as db:
        fresh = await create_user(db, f"n-{uuid.uuid4().hex[:8]}@example.com", "New Person", password="test-password-123")
    assert await _builtin_of(fresh.id) is not None


async def test_the_connector_is_fixed_but_can_be_hidden(app, client, user, off_after):
    """ACT calendar.builtin_hide: no delete, no new name, address or password; hide and show."""
    await _set(True)
    async with sessionmaker()() as db:
        c = await builtin.ensure_connector(db, await db.get(User, user.id))
        await db.commit()
    cid = c.id
    listed = {x["id"]: x for x in (await client.get(f"{API}/connectors")).json()}
    assert listed[cid]["builtin"] is True and listed[cid]["has_password"] is False
    acc = {x["id"]: x for x in (await client.get(f"{API}/calendar/accounts")).json()}
    assert acc[cid]["builtin"] is True and acc[cid]["url"] is None and acc[cid]["username"] is None

    assert (await client.delete(f"{API}/connectors/{cid}")).status_code == 400
    assert (await client.delete(f"{API}/calendar/accounts/{cid}")).status_code == 400
    for body in ({"name": "Mine"}, {"config": {"url": "https://cal.example.com/dav/"}},
                 {"secrets": {"password": "x"}}, {"config": {"account": "someone@example.test"}}):
        r = await client.patch(f"{API}/connectors/{cid}", json=body)
        assert r.status_code == 400, body
    assert (await client.patch(f"{API}/calendar/accounts/{cid}", json={"name": "Other"})).status_code == 400
    # The unchanged name is fine (the settings form sends it back).
    assert (await client.patch(f"{API}/connectors/{cid}", json={"name": builtin.NAME})).status_code == 200

    r = await client.patch(f"{API}/connectors/{cid}", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    from mailr.calendar import mirror

    async with sessionmaker()() as db:
        assert cid not in await mirror.visible_connector_ids(db, user.id)
        c = await db.get(Connector, cid)
        assert c.config == {"account": c.config["account"]} and c.secrets_enc is None
    r = await client.patch(f"{API}/connectors/{cid}", json={"enabled": True})
    assert r.status_code == 200
    async with sessionmaker()() as db:
        assert cid in await mirror.visible_connector_ids(db, user.id)


async def test_off_hides_and_stops_syncing_and_on_resumes(app, client, user, off_after):
    """ACT settings.builtin_calendars: off keeps the connector (and its mirror) but lists and
    syncs nothing; on again shows the same connector."""
    from mailr.calendar import mirror, sync

    await _set(True)
    async with sessionmaker()() as db:
        cid = (await builtin.ensure_connector(db, await db.get(User, user.id))).id
        await db.commit()
    await _set(False)
    assert cid not in {x["id"] for x in (await client.get(f"{API}/connectors")).json()}
    assert cid not in {x["id"] for x in (await client.get(f"{API}/calendar/accounts")).json()}
    assert (await client.get(f"{API}/connectors/{cid}")).status_code == 404
    async with sessionmaker()() as db:
        assert cid not in await mirror.visible_connector_ids(db, user.id)
        await db.execute(Job.__table__.delete().where(Job.kind == "calendar.sync"))
        await db.commit()
    await sync.due_syncs()
    async with sessionmaker()() as db:
        jobs = (await db.execute(select(Job.payload).where(Job.kind == "calendar.sync"))).scalars().all()
        assert all(j.get("connector_id") != cid for j in jobs)
    res = await sync.sync_connector(cid)
    assert res.error is None and res.collections == []  # not synced at all
    async with sessionmaker()() as db:
        assert await db.get(Connector, cid) is not None  # kept
    await _set(True)
    assert cid in {x["id"] for x in (await client.get(f"{API}/connectors")).json()}
    async with sessionmaker()() as db:
        assert (await builtin.ensure_connector(db, await db.get(User, user.id))).id == cid


async def test_wanted_is_a_function_of_the_user(user, off_after):
    """Whether a user gets one depends on the user: a disabled user or one without a store account gets none."""
    await _set(True)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        assert await builtin.builtin_calendar_wanted(db, u)
        u.disabled = True
        assert not await builtin.builtin_calendar_wanted(db, u)
        u.disabled, u.stalwart_account_id = False, None
        assert not await builtin.builtin_calendar_wanted(db, u)
        await db.rollback()


async def test_the_sign_in_is_derived_and_internal(monkeypatch):
    from mailr.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "stalwart_url", "http://stalwart:8080")
    monkeypatch.setattr(s, "stalwart_admin_user", "mailr-svc@example.test")
    monkeypatch.setattr(s, "stalwart_admin_password", "svc-secret")
    conn = Connector(id=987654, user_id=1, kind="caldav", name=builtin.NAME, builtin=True,
                     config={"account": "alice-1-abc@example.test", "url": "https://elsewhere.example.com/"})
    c = builtin.client_for(conn)
    try:
        assert c.base == "http://stalwart:8080/dav/cal/"  # from the settings, never from config
        assert c.internal and c.limiter is builtin.limiter(987654)
        assert c.auth._auth_header == httpx.BasicAuth("alice-1-abc@example.test%mailr-svc@example.test",
                                                      "svc-secret")._auth_header
    finally:
        await c.aclose()


# ---------------- the defaults rule ----------------


async def _collections(db, conn: Connector, specs: list[tuple[str, list[str]]]) -> list[str]:
    out = []
    for href, comps in specs:
        db.add(CalCollection(user_id=conn.user_id, connector_id=conn.id, href=href, displayname=href,
                             components=comps))
        out.append(href)
    await db.flush()
    return out


async def test_defaults_go_to_the_builtin_only_where_the_user_has_none(user, off_after):
    await _set(True)
    home = "http://stalwart:8080/dav/cal/u%40example.test/"
    async with sessionmaker()() as db:
        b = await builtin.ensure_connector(db, await db.get(User, user.id))
        await _collections(db, b, [(home + "default/", ["VEVENT", "VTODO", "VJOURNAL"]), (home + "tasks/", ["VTODO"])])
        await db.commit()
    await builtin.adopt_defaults(user.id, b.id, home)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        assert u.settings["calendar_defaults"] == {"calendar": home + "default/", "task_list": home + "tasks/"}
        # An external account that takes events keeps the calendar slot; a saved choice stays.
        u.settings = {**u.settings, "calendar_defaults": {}}
        ext = Connector(user_id=user.id, kind="caldav", name="Davis", config={"url": "https://cal.example.com/"})
        db.add(ext)
        await db.flush()
        await _collections(db, ext, [("https://cal.example.com/dav/calendars/me/work/", ["VEVENT"])])
        await db.commit()
    await builtin.adopt_defaults(user.id, b.id, home)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        assert u.settings["calendar_defaults"] == {"calendar": None, "task_list": home + "tasks/"}
        u.settings = {**u.settings, "calendar_defaults": {"calendar": "https://cal.example.com/dav/calendars/me/work/",
                                                          "task_list": None}}
        await db.commit()
    await builtin.adopt_defaults(user.id, b.id, home)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        assert u.settings["calendar_defaults"]["calendar"] == "https://cal.example.com/dav/calendars/me/work/"


async def test_the_stock_calendar_is_no_task_list_beside_tasks(user, off_after, monkeypatch):
    """Stalwart's stock calendar also takes tasks; next to the built-in "Tasks" list it is listed
    for events only, unless it already holds a task (nothing disappears)."""
    from mailr.calendar import client, mirror
    from mailr.models import CalObject

    async def no_sync(*a, **k):
        return None

    monkeypatch.setattr(mirror, "ensure_synced", no_sync)
    await _set(True)
    home = "http://stalwart:8080/dav/cal/u%40example.test/"
    async with sessionmaker()() as db:
        b = await builtin.ensure_connector(db, await db.get(User, user.id))
        await _collections(db, b, [(home + "default/", ["VEVENT", "VTODO", "VJOURNAL"]), (home + "tasks/", ["VTODO"])])
        await db.commit()

    async def kinds() -> dict[str, tuple[bool, bool]]:
        async with sessionmaker()() as db:
            return {c["url"]: (c["events"], c["tasks"]) for c in await client.collections(db, user.id)
                    if c.get("connector_id") == b.id}

    assert await kinds() == {home + "default/": (True, False), home + "tasks/": (False, True)}
    async with sessionmaker()() as db:
        assert [c.href for c in await mirror.task_collections(db, user.id, None, None)
                if c.connector_id == b.id] == [home + "tasks/"]
        # A task list saved before is not kept as the default where it no longer takes tasks.
        u = await db.get(User, user.id)
        u.settings = {**(u.settings or {}), "calendar_defaults": {"calendar": home + "default/",
                                                                  "task_list": home + "default/"}}
        await db.commit()
        assert (await client.defaults(db, user.id))["task_list"] not in (None, home + "default/")
        coll = (await db.execute(select(CalCollection).where(CalCollection.connector_id == b.id,
                                                                   CalCollection.href == home + "default/"))).scalar_one()
        db.add(CalObject(user_id=user.id, connector_id=b.id, collection_id=coll.id, href=home + "default/t1.ics",
                         uid="t1", kind="task", summary="Older task"))
        await db.commit()
    assert await kinds() == {home + "default/": (True, True), home + "tasks/": (False, True)}


# ---------------- the store account's description ----------------


async def test_the_store_account_shows_the_display_name(client, user):
    from mailr.core.users import _mgmt

    async def desc() -> str:
        r = await _mgmt("x:Account/get", {"ids": [user.stalwart_account_id], "properties": ["description"]})
        return r["list"][0]["description"]

    assert await desc() == "Test User"  # at creation; no id, no address
    await _mgmt("x:Account/set", {"update": {user.stalwart_account_id: {
        "description": f"mailr user {user.id} ({user.email})"}}})
    assert await builtin.ensure_descriptions(force=True) >= 1
    assert await desc() == "Test User"
    assert (await client.patch(f"{API}/me", json={"name": "Jordan Example"})).status_code == 200
    assert await desc() == "Jordan Example"


# ---------------- requests in flight ----------------


class _BusyServer:
    """A DAV server that, like Stalwart, refuses a request past `limit` in flight with HTTP 400
    and a JMAP problem body; `refuse_first` refuses that many requests regardless."""

    def __init__(self, limit: int = 4, refuse_first: int = 0):
        self.limit, self.refuse_first = limit, refuse_first
        self.now = self.peak = self.refused = self.calls = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.now += 1
        self.peak = max(self.peak, self.now)
        try:
            if self.now > self.limit or self.refused < self.refuse_first:
                self.refused += 1
                return httpx.Response(400, json={"type": "urn:ietf:params:jmap:error:limit",
                                                 "limit": "maxConcurrentRequests", "status": 400})
            await asyncio.sleep(0.02)
            return httpx.Response(201, headers={"ETag": '"1"'})
        finally:
            self.now -= 1


def _client(server: _BusyServer, *, internal: bool, limiter: asyncio.Semaphore | None) -> dav.DavClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return dav.DavClient("http://stalwart:8080/dav/cal/", "a%svc", "pw", http=http, internal=internal, limiter=limiter)


async def test_at_most_three_requests_in_flight_per_connector():
    server = _BusyServer(limit=4)
    c = _client(server, internal=True, limiter=asyncio.Semaphore(builtin.MAX_IN_FLIGHT))
    etags = await asyncio.gather(*(c.put(f"/dav/cal/a/default/{i}.ics", "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
                                   for i in range(30)))
    assert all(e == '"1"' for e in etags)
    assert server.peak <= 3 and server.refused == 0


async def test_the_busy_refusal_is_retried(monkeypatch):
    monkeypatch.setattr(dav, "BUSY_RETRIES", (0.01, 0.01, 0.01))
    server = _BusyServer(refuse_first=2)
    c = _client(server, internal=True, limiter=asyncio.Semaphore(3))
    assert await c.put("/dav/cal/a/default/x.ics", "x") == '"1"'
    assert server.calls == 3
    # Past the retries the refusal is an error.
    server = _BusyServer(refuse_first=10)
    c = _client(server, internal=True, limiter=None)
    with pytest.raises(dav.ServerError):
        await c.put("/dav/cal/a/default/x.ics", "x")
    assert server.calls == 4
    # Another 400 is not retried.

    async def bad(request):
        return httpx.Response(400, content=json.dumps({"type": "other"}).encode())

    c = dav.DavClient("http://stalwart:8080/dav/cal/", http=httpx.AsyncClient(transport=httpx.MockTransport(bad)),
                      internal=True)
    with pytest.raises(dav.ServerError):
        await c.put("/dav/cal/a/default/x.ics", "x")


async def test_an_internal_client_stays_on_the_stores_origin():
    async def redirect(request):
        return httpx.Response(307, headers={"Location": "http://evil.example.com/dav/"})

    c = dav.DavClient("http://stalwart:8080/dav/cal/", "a%svc", "pw",
                      http=httpx.AsyncClient(transport=httpx.MockTransport(redirect)), internal=True)
    with pytest.raises(dav.DavError, match="another address"):
        await c.home()
    with pytest.raises(dav.DavError, match="another address"):
        await c.get("http://other.example.com/x.ics")
