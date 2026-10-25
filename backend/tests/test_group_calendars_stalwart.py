# ACT-SURFACES: api
"""Group calendars end to end against a real Stalwart.

The module runs against the suite's own store server, the dev Stalwart of
deploy/dev/docker-compose.yml, unless MAILR_TEST_STALWART_URL and MAILR_TEST_STALWART_ADMIN name
another throwaway Stalwart (tests/dev_services.py). It is never pointed at production. MAILR_TEST_WITHOUT_STALWART_DAV=1 skips it.

For the length of each test mailr's store settings point at that server. The test deletes its
accounts at the end.

Covered (action settings.group_calendars): two users in a group → a read-write group calendar is
visible to both and an event one of them makes is visible to the other; the group's stock default
calendar stays out of mailr; a read-only group calendar refuses a member's write and takes an
editor's; Stalwart's first-share bug (a group grant invisible until InvalidateCaches) and mailr's
cure; leaving the group takes the calendars away; deleting the last calendar removes the store
group.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from dev_services import stalwart_dav_skip_reason
from httpx import ASGITransport, AsyncClient

from mailr.calendar import builtin, dav, groups

URL = os.environ.get("MAILR_TEST_STALWART_URL", "").rstrip("/")
ADMIN = os.environ.get("MAILR_TEST_STALWART_ADMIN", "")
pytestmark = [
    pytest.mark.skipif(not (URL and ":" in ADMIN), reason=stalwart_dav_skip_reason()),
    pytest.mark.serial,  # points the process's store settings at another server
]
API = "/api/v1"
PW = "test-password-123"


@pytest_asyncio.fixture
async def store(monkeypatch):
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


async def _login(app, email: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post(f"{API}/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c


@pytest_asyncio.fixture
async def family(store):
    """An admin and three users; alice and bob are in the group, carol is not."""
    from mailr.core.users import bootstrap, create_user, delete_store_account
    from mailr.db import sessionmaker
    from mailr.main import create_app

    await bootstrap()
    app = create_app()
    group = f"Family {uuid.uuid4().hex[:6]}"
    async with sessionmaker()() as db:
        await builtin.set_instance_enabled(db, True, "everyone")
        await db.commit()
    users = {}
    async with sessionmaker()() as db:
        for short, role in (("admin", "admin"), ("alice", "user"), ("bob", "user"), ("carol", "user")):
            u = await create_user(db, f"{short}-{uuid.uuid4().hex[:6]}@example.com", short.title(), password=PW, role=role)
            users[short] = u
        for short in ("alice", "bob"):
            u = await db.get(type(users[short]), users[short].id)
            u.assigned_groups = [group]
        await db.commit()
    clients = {k: await _login(app, u.email) for k, u in users.items()}
    yield group, users, clients
    for c in clients.values():
        await c.aclose()
    async with sessionmaker()() as db:
        from sqlalchemy import select

        from mailr.models import GroupCalendar

        for gc in (await db.execute(select(GroupCalendar).where(GroupCalendar.group_name == group))).scalars():
            try:
                await groups.delete(db, gc)
            except Exception:  # noqa: BLE001  best effort
                pass
        await db.commit()
        await groups.reconcile()
        await builtin.set_instance_enabled(db, False, "everyone")
        await db.commit()
    for u in users.values():
        await delete_store_account(u)


async def _sync(user) -> list[dict]:
    """Sync the user's built-in connector and return their calendars."""
    from mailr.calendar import client as cal
    from mailr.calendar import sync
    from mailr.db import sessionmaker

    async with sessionmaker()() as db:
        conn = await builtin.find(db, user.id)
    res = await sync.sync_connector(conn.id)
    assert res.error is None, res.error
    async with sessionmaker()() as db:
        return await cal.collections(db, user.id)


def _named(cals: list[dict], name: str) -> dict | None:
    return next((c for c in cals if c.get("name") == name), None)


async def test_group_calendars_end_to_end(family):
    """ACT settings.group_calendars: read-write and read-only group calendars, editors, leaving."""
    from mailr.db import sessionmaker
    from mailr.models import CalendarGroup

    group, users, clients = family
    admin = clients["admin"]
    for u in users.values():
        await _sync(u)

    # A read-write calendar: both members see it, writable, marked as the group's.
    r = await admin.post(f"{API}/admin/calendars/groups", json={"group": group, "name": "Family", "color": "#2E7D32",
                                                                "access": "write"})
    assert r.status_code == 200, r.text
    fam_id = r.json()["id"]
    a_cals, b_cals, c_cals = await _sync(users["alice"]), await _sync(users["bob"]), await _sync(users["carol"])
    fam_a, fam_b = _named(a_cals, "Family"), _named(b_cals, "Family")
    assert fam_a and fam_b and not _named(c_cals, "Family")
    assert fam_a["read_only"] is False and fam_a["shared"] == {
        "kind": "group", "group": group, "access": "write", "editor": False, "group_calendar_id": fam_id}
    assert fam_a["color"] == "#2E7D32"
    # The group account's stock default calendar never shows up.
    assert not any(builtin.STOCK_NAME.match(c["name"]) for c in a_cals), [c["name"] for c in a_cals]

    # Alice adds an event; Bob sees it.
    r = await clients["alice"].post(f"{API}/calendar/events", json={
        "summary": "Grandma's birthday", "start": "2026-11-20T18:00:00Z", "end": "2026-11-20T21:00:00Z",
        "calendar": fam_a["url"]})
    assert r.status_code == 200, r.text
    await _sync(users["bob"])
    r = await clients["bob"].get(f"{API}/calendar/agenda", params={"from": "2026-11-20", "to": "2026-11-21"})
    assert any(e["summary"] == "Grandma's birthday" for e in r.json()["events"]), r.json()

    # Members cannot rename or delete it; a colour stays theirs.
    r = await clients["bob"].patch(f"{API}/calendar/collections/{fam_b['calendar_id']}", json={"name": "Mine"})
    assert r.status_code == 409 and "group" in r.json()["detail"]
    r = await clients["bob"].delete(f"{API}/calendar/collections/{fam_b['calendar_id']}", params={"confirm": "Family"})
    assert r.status_code == 409
    r = await clients["bob"].patch(f"{API}/calendar/collections/{fam_b['calendar_id']}", json={"color": "#AA0000"})
    assert r.status_code == 200 and r.json()["color"] == "#AA0000" and r.json()["color_local"] is True
    assert _named(await _sync(users["alice"]), "Family")["color"] == "#2E7D32"

    # A read-only calendar with Alice as editor. Stalwart's first-share bug: without
    # InvalidateCaches the members would not see it.
    calls = []
    real = groups.invalidate_caches

    async def counted():
        calls.append(1)
        await real()

    groups.invalidate_caches = counted
    try:
        r = await admin.post(f"{API}/admin/calendars/groups", json={
            "group": group, "name": "School", "color": "#1565C0", "access": "read", "editors": [users["alice"].id]})
    finally:
        groups.invalidate_caches = real
    assert r.status_code == 200, r.text
    assert calls, "InvalidateCaches was not called after the group grant"
    school_id = r.json()["id"]
    sch_a, sch_b = _named(await _sync(users["alice"]), "School"), _named(await _sync(users["bob"]), "School")
    assert sch_a and sch_b
    assert sch_a["read_only"] is False and sch_a["shared"]["editor"] is True
    assert sch_b["read_only"] is True and sch_b["shared"]["access"] == "read"
    ev = {"summary": "Parents' evening", "start": "2026-11-24T23:00:00Z", "end": "2026-11-25T00:00:00Z"}
    r = await clients["bob"].post(f"{API}/calendar/events", json={**ev, "calendar": sch_b["url"]})
    assert r.status_code == 400 and "read-only for you" in r.json()["detail"], r.text
    r = await clients["alice"].post(f"{API}/calendar/events", json={**ev, "calendar": sch_a["url"]})
    assert r.status_code == 200, r.text

    # The admin's view: group calendars only, with counts.
    r = await admin.get(f"{API}/admin/calendars/groups")
    g = next(x for x in r.json()["groups"] if x["name"] == group)
    assert sorted(c["name"] for c in g["calendars"]) == ["Family", "School"]
    assert {c["name"]: c["items"] for c in g["calendars"]} == {"Family": 1, "School": 1}
    assert {m["id"] for m in g["members"]} == {users["alice"].id, users["bob"].id}

    # Bob leaves the group: both calendars go from his list; Alice keeps them.
    r = await admin.put(f"{API}/admin/users/{users['bob'].id}/groups", json={"groups": []})
    assert r.status_code == 200, r.text
    await groups.reconcile()
    b_cals = await _sync(users["bob"])
    assert not _named(b_cals, "Family") and not _named(b_cals, "School")
    assert _named(await _sync(users["alice"]), "Family")

    # Export, then delete both; the store group goes with the last one.
    r = await admin.get(f"{API}/admin/calendars/groups/{fam_id}/export")
    assert r.status_code == 200 and "Grandma's birthday" in r.text
    async with sessionmaker()() as db:
        cg = await db.get(CalendarGroup, group)
        store_ids = [cg.store_group_id, cg.owner_id]
    for cid, name in ((fam_id, "Family"), (school_id, "School")):
        r = await admin.delete(f"{API}/admin/calendars/groups/{cid}", params={"confirm": name})
        assert r.status_code == 200, r.text
    await groups.reconcile()
    async with sessionmaker()() as db:
        assert await db.get(CalendarGroup, group) is None
    from mailr.core.users import _mgmt

    got = await _mgmt("x:Account/get", {"ids": store_ids, "properties": ["name"]})
    assert not got.get("list"), got
    assert not _named(await _sync(users["alice"]), "Family")


async def test_first_share_to_a_group_needs_invalidate_caches(family):
    """Stalwart 0.16.23: a first grant from an account to a group stays invisible to its members
    until InvalidateCaches. If this starts failing at the first assert, Stalwart fixed it; the
    call in calendar/groups.py can then go."""
    group, users, clients = family
    # Alice is in the store group and her access token is built (a read-write calendar first, then
    # a sync), before the owner account shares anything with the group.
    r = await clients["admin"].post(f"{API}/admin/calendars/groups", json={"group": group, "name": "Home"})
    assert r.status_code == 200, r.text
    assert _named(await _sync(users["alice"]), "Home")
    real = groups.invalidate_caches

    async def nothing():
        return None

    groups.invalidate_caches = nothing
    try:
        r = await clients["admin"].post(f"{API}/admin/calendars/groups", json={
            "group": group, "name": "Club", "access": "read"})
    finally:
        groups.invalidate_caches = real
    assert r.status_code == 200, r.text
    assert not _named(await _sync(users["alice"]), "Club"), "the first-share bug is gone"
    await groups.invalidate_caches()
    assert _named(await _sync(users["alice"]), "Club")


async def test_owner_acl_puts_editors_before_the_group(family):
    """Stalwart takes the first ACE naming the user: an editor listed after the group's read gets
    only read. mailr writes the editors first."""
    group, users, clients = family
    r = await clients["admin"].post(f"{API}/admin/calendars/groups", json={
        "group": group, "name": "Rota", "access": "read", "editors": [users["bob"].id]})
    assert r.status_code == 200, r.text
    from mailr.db import sessionmaker
    from mailr.models import CalendarGroup, GroupCalendar

    async with sessionmaker()() as db:
        gc = await db.get(GroupCalendar, r.json()["id"])
        cg = await db.get(CalendarGroup, group)
        async with groups.owner_client(cg.owner_login) as c:
            ms = await c._multistatus("PROPFIND", groups._url(gc.href), dav._propfind_body(["d:acl"]), "0")
    hrefs = [dav._text(x) for x in ms.iter("{DAV:}href") if "/dav/pal/" in dav._text(x)]
    assert hrefs[-1] == groups.principal_of(cg.store_group_login), hrefs
    r = await clients["admin"].patch(f"{API}/admin/calendars/groups/{gc.id}", json={"editors": []})
    assert r.status_code == 200 and r.json()["editors"] == []
    sch = _named(await _sync(users["bob"]), "Rota")
    assert sch and sch["read_only"] is True
