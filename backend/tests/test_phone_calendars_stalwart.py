# ACT-SURFACES: api
"""Phone calendars (calendar/phonecal.py) are not served over DAV: busy times sent from the phone
exist only in mailr's database, so the mail store's calendar server (which the DAV gateway serves to
phone and desktop calendar apps) never holds them, before or after a sync of the built-in calendar.

Runs against the suite's Stalwart, as test_builtin_calendar_stalwart.py does. Covers (api):
ACT calendar.phone_sync."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

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
async def setup(monkeypatch):
    from mailr.config import get_settings
    from mailr.core import store as store_mod
    from mailr.core.users import bootstrap, create_user, delete_store_account
    from mailr.db import sessionmaker
    from mailr.main import create_app

    login, pw = ADMIN.split(":", 1)
    s = get_settings()
    monkeypatch.setattr(s, "stalwart_url", URL)
    monkeypatch.setattr(s, "stalwart_admin_user", login)
    monkeypatch.setattr(s, "stalwart_admin_password", pw)
    monkeypatch.setattr(s, "stalwart_domain", login.split("@", 1)[1])
    monkeypatch.setattr(store_mod, "_service", None)
    await bootstrap()
    app = create_app()
    async with sessionmaker()() as db:
        await builtin.set_instance_enabled(db, True)
        await db.commit()
    async with sessionmaker()() as db:
        user = await create_user(db, f"p-{uuid.uuid4().hex[:8]}@example.com", "Robin Example",
                                 password="test-password-123")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(f"{API}/auth/login", json={"email": user.email, "password": "test-password-123"})
            c.headers["X-CSRF-Token"] = r.json()["csrf"]
            yield user, c
    finally:
        async with sessionmaker()() as db:
            await builtin.set_instance_enabled(db, False)
            await db.commit()
        await delete_store_account(user)
        if store_mod._service is not None:
            await store_mod._service.close()
        store_mod._service = None


def _service_as(account: str) -> dav.DavClient:
    login, pw = ADMIN.split(":", 1)
    return dav.DavClient(URL + "/dav/cal/", f"{account}%{login}", pw, internal=True)


async def _server_texts(account: str) -> tuple[list[str], list[str]]:
    """(calendar names, every resource's text) in the account's calendar home on the server."""
    async with _service_as(account) as c:
        home = await c.home()
        cols = await c.collections(home)
        texts = []
        for col in cols:
            hrefs = list(await c.etags(col.href))
            if hrefs:
                texts += [ics for _, _, ics in await c.multiget(col.href, hrefs)]
    return [col.displayname for col in cols], texts


async def test_busy_times_never_reach_the_calendar_server(setup):
    from mailr.calendar import sync
    from mailr.db import sessionmaker

    user, client = setup
    async with sessionmaker()() as db:
        conn = await builtin.find(db, user.id)
    assert conn is not None
    assert (await sync.sync_connector(conn.id)).error is None
    start = (datetime.now(UTC) + timedelta(days=3)).replace(hour=15, minute=0, second=0, microsecond=0)
    key = uuid.uuid4().hex
    body = {"name": "Phone work calendar", "color": "#0078d4", "device": "Pixel",
            "window_start": (start - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window_end": (start + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "busy": [{"id": "ab" * 12, "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "end": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), "all_day": False}]}
    r = await client.put(f"{API}/calendar/phone-calendars/{key}", json=body)
    assert r.status_code == 200, r.text
    # mailr's own views have the block...
    r = await client.get(f"{API}/calendar/agenda", params={"from": start.date().isoformat(),
                                                           "to": (start + timedelta(days=1)).date().isoformat()})
    assert [e["summary"] for e in r.json()["events"] if e.get("phone_busy")] == ["Busy"]
    # ...the server that the DAV gateway serves to calendar apps does not, before or after a sync.
    for _ in range(2):
        names, texts = await _server_texts(conn.config["account"])
        assert "Phone work calendar" not in names
        assert not any(key in t or "phone-" in t for t in texts)
        assert (await sync.sync_connector(conn.id)).error is None
