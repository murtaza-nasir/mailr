# ACT-SURFACES: api
"""The built-in calendar end to end against a real Stalwart.

The module runs against the suite's own store server, the dev Stalwart of
deploy/dev/docker-compose.yml, unless MAILR_TEST_STALWART_URL and MAILR_TEST_STALWART_ADMIN name
another throwaway Stalwart (tests/dev_services.py). It is never pointed at production. MAILR_TEST_WITHOUT_STALWART_DAV=1 skips it.

For the length of each test mailr's store settings point at that server, so the user it creates gets
its store account there. The test deletes its accounts at the end.

Covered: a new user gets the connector at creation (setting on) → first sync → the stock-named
default calendar is renamed "Calendar" → a "Tasks" list (VTODO only) → both are the defaults → an
event and a task made through mailr's API are on the server over DAV; the account's description is
the display name; many parallel writes through the built-in client all succeed (the cap and the
retry). Action covered: settings.builtin_calendars.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from dev_services import stalwart_dav_skip_reason
from httpx import ASGITransport, AsyncClient

from mailr.calendar import builtin, dav

URL = os.environ.get("MAILR_TEST_STALWART_URL", "").rstrip("/")
ADMIN = os.environ.get("MAILR_TEST_STALWART_ADMIN", "")
pytestmark = [
    pytest.mark.skipif(not (URL and ":" in ADMIN), reason=stalwart_dav_skip_reason()),
    pytest.mark.serial,  # points the process's store settings at another server
]
API = "/api/v1"


@pytest_asyncio.fixture
async def store(monkeypatch):
    """mailr's store settings pointed at the throwaway Stalwart (and back afterwards)."""
    from mailr.config import get_settings
    from mailr.core import store as store_mod

    login, pw = ADMIN.split(":", 1)
    s = get_settings()
    monkeypatch.setattr(s, "stalwart_url", URL)
    monkeypatch.setattr(s, "stalwart_admin_user", login)
    monkeypatch.setattr(s, "stalwart_admin_password", pw)
    monkeypatch.setattr(s, "stalwart_domain", login.split("@", 1)[1])
    monkeypatch.setattr(store_mod, "_service", None)
    yield s
    if store_mod._service is not None:
        await store_mod._service.close()
    store_mod._service = None


@pytest_asyncio.fixture
async def setup(store):
    from mailr.core.users import bootstrap, create_user, delete_store_account
    from mailr.db import sessionmaker
    from mailr.main import create_app

    await bootstrap()
    app = create_app()
    async with sessionmaker()() as db:
        await builtin.set_instance_enabled(db, True)
        await db.commit()
    async with sessionmaker()() as db:
        user = await create_user(db, f"b-{uuid.uuid4().hex[:8]}@example.com", "Robin Example",
                                 password="test-password-123")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(f"{API}/auth/login", json={"email": user.email, "password": "test-password-123"})
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        yield user, c
    async with sessionmaker()() as db:
        await builtin.set_instance_enabled(db, False)
        await db.commit()
    await delete_store_account(user)


def _service_as(account: str) -> dav.DavClient:
    """A separate client, as the test sees the server (not mailr's)."""
    login, pw = ADMIN.split(":", 1)
    return dav.DavClient(URL + "/dav/cal/", f"{account}%{login}", pw, internal=True)


async def test_new_user_gets_a_ready_calendar_and_task_list(setup):
    """ACT settings.builtin_calendars: on → a new user's mailr calendar works end to end."""
    from mailr.calendar import sync
    from mailr.core.users import _mgmt
    from mailr.db import sessionmaker

    user, client = setup
    async with sessionmaker()() as db:
        conn = await builtin.find(db, user.id)
    assert conn is not None and conn.config["account"].endswith("@" + ADMIN.split(":", 1)[0].split("@", 1)[1])

    # Before mailr's first sync the account's default calendar carries the stock name.
    async with _service_as(conn.config["account"]) as c:
        home = await c.home()
        before = await c.collections(home)
    assert any(builtin.STOCK_NAME.match(i.displayname) for i in before), [i.displayname for i in before]

    res = await sync.sync_connector(conn.id)
    assert res.error is None, res.error
    async with _service_as(conn.config["account"]) as c:
        after = {i.href: i for i in await c.collections(home)}
    names = sorted(i.displayname for i in after.values())
    assert "Calendar" in names and "Tasks" in names and not any(builtin.STOCK_NAME.match(n) for n in names)
    tasks_href = next(h for h, i in after.items() if i.displayname == "Tasks")
    cal_href = next(h for h, i in after.items() if i.displayname == "Calendar")
    assert after[tasks_href].components == ["VTODO"]

    d = (await client.get(f"{API}/calendar/defaults")).json()
    assert d["calendar"] == cal_href and d["task_list"] == tasks_href and d["connector_id"] == conn.id
    accts = (await client.get(f"{API}/calendar/accounts")).json()
    assert [a["builtin"] for a in accts] == [True]

    # A second sync changes nothing on the server (no second Tasks list).
    assert (await sync.sync_connector(conn.id)).error is None
    async with _service_as(conn.config["account"]) as c:
        assert sorted(i.displayname for i in await c.collections(home)) == names

    r = await client.post(f"{API}/calendar/events", json={"summary": "Budget review", "start": "2026-10-20T15:00:00Z",
                                                          "end": "2026-10-20T16:00:00Z"})
    assert r.status_code == 200, r.text
    ev_uid = r.json()["uid"]
    r = await client.post(f"{API}/tasks", json={"summary": "Send the slides", "due": "2026-10-21"})
    assert r.status_code == 200, r.text
    task_uid = r.json()["uid"]
    async with _service_as(conn.config["account"]) as c:
        evs = await c.multiget(cal_href, list(await c.etags(cal_href)))
        tks = await c.multiget(tasks_href, list(await c.etags(tasks_href)))
    assert any(ev_uid in ics and "Budget review" in ics for _, _, ics in evs)
    assert any(task_uid in ics and "VTODO" in ics for _, _, ics in tks)

    acct = (await _mgmt("x:Account/get", {"ids": [user.stalwart_account_id], "properties": ["description"]}))["list"][0]
    assert acct["description"] == "Robin Example"


async def test_parallel_writes_through_the_builtin_client_all_land(setup):
    """Stalwart refuses an account's fifth concurrent request. Two built-in clients with limiters
    of their own (the API and the worker: up to six in flight) write 200 events at once; the
    refusals are repeated, so every write lands."""
    user, _client = setup
    from mailr.db import sessionmaker

    async with sessionmaker()() as db:
        conn = await builtin.find(db, user.id)
    a, b = builtin.client_for(conn), builtin.client_for(conn)
    b.limiter = asyncio.Semaphore(builtin.MAX_IN_FLIGHT)  # another process's limiter
    async with a, b:
        home = await a.home()
        coll = next(i.href for i in await a.collections(home) if "VEVENT" in i.components)

        async def put(c: dav.DavClient, i: int) -> str | None:
            uid = uuid.uuid4().hex
            ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//mailr test//EN\r\nBEGIN:VEVENT\r\n"
                   f"UID:{uid}\r\nDTSTAMP:20261004T120000Z\r\nDTSTART:20261012T140000Z\r\n"
                   f"DTEND:20261012T150000Z\r\nSUMMARY:Parallel {i}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
            return await c.put(coll + f"{uid}.ics", ics, if_none_match=True)

        etags = await asyncio.gather(*(put(a if i % 2 else b, i) for i in range(200)))
        assert all(etags)
        assert len(await a.etags(coll)) >= 200
