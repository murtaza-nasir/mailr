# ACT-SURFACES: api
"""Sharing a calendar between users, end to end against a real Stalwart.

The module runs against the suite's own store server, the dev Stalwart of
deploy/dev/docker-compose.yml, unless MAILR_TEST_STALWART_URL and MAILR_TEST_STALWART_ADMIN name
another throwaway Stalwart (tests/dev_services.py). It is never pointed at production. MAILR_TEST_WITHOUT_STALWART_DAV=1 skips it.

For the length of each test mailr's store settings point at that server. The test deletes its
accounts at the end.

Covered (actions calendar.share, calendar.user_shared, calendar.share_notices): view → the sharee
sees it read-only under Shared and a write is refused; edit → writes work; share → the sharee
re-shares; revoke → it goes at the next sync; a person's own grant and a store group's grant on the
same calendar count as the server counts them (union on 1.x, first ACE for writes on 0.16); the
policy; the free/busy-only level (1.x: busy blocks; 0.16: refused) and the server's answers to such a
grant; deleting a user removes their grants and leaves the owner's ACL readable.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from dev_services import stalwart_dav_skip_reason
from httpx import ASGITransport, AsyncClient

from mailr.calendar import builtin, dav, groups, sharing

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
async def people(store):
    """An admin and alice, bob, carol, dave; alice and bob share a mailr group."""
    from mailr.core.users import bootstrap, create_user, delete_store_account
    from mailr.db import sessionmaker
    from mailr.main import create_app

    await bootstrap()
    app = create_app()
    group = f"Home {uuid.uuid4().hex[:6]}"
    async with sessionmaker()() as db:
        await builtin.set_instance_enabled(db, True, "everyone")
        await sharing.set_policy(db, "anyone")
        await db.commit()
    users = {}
    async with sessionmaker()() as db:
        for short, role in (("admin", "admin"), ("alice", "user"), ("bob", "user"), ("carol", "user"),
                            ("dave", "user")):
            users[short] = await create_user(db, f"{short}-{uuid.uuid4().hex[:6]}@example.com", short.title(),
                                             password=PW, role=role)
        for short in ("alice", "bob"):
            u = await db.get(type(users[short]), users[short].id)
            u.assigned_groups = [group]
        await db.commit()
    clients = {k: await _login(app, u.email) for k, u in users.items()}
    yield users, clients
    for c in clients.values():
        await c.aclose()
    async with sessionmaker()() as db:
        await sharing.set_policy(db, "anyone")
        await builtin.set_instance_enabled(db, False, "everyone")
        await db.commit()
    for u in users.values():
        try:
            await delete_store_account(u)
        except Exception:  # noqa: BLE001  deleted by the test
            pass


async def _sync(user) -> list[dict]:
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


async def _work(users, clients) -> dict:
    """Alice's own calendar "Work", made through the API."""
    for u in users.values():
        await _sync(u)
    r = await clients["alice"].post(f"{API}/calendar/collections", json={"name": "Work", "kind": "events",
                                                                         "color": "#1565C0"})
    assert r.status_code == 200, r.text
    return _named(await _sync(users["alice"]), "Work")


async def _acl(owner, path: str) -> list[sharing.Ace]:
    from mailr.db import sessionmaker

    async with sessionmaker()() as db:
        login = await groups.user_login(db, owner)
    return await sharing.read_acl(owner, login, path)


async def _login_of(user) -> str:
    from mailr.db import sessionmaker

    async with sessionmaker()() as db:
        return await groups.user_login(db, user)


async def _v1() -> bool:
    from mailr.core.store import service_client
    from mailr.core.store_groupware import is_v1

    return await is_v1(service_client(), cache=False)


async def _stale_grant_shows(owner_login: str, path: str, v1: bool, keep: str) -> None:
    """A grant to a destroyed account: Stalwart 0.16 answers 500 to the DAV ACL read, 1.x lists it
    with the account's number as the principal (sharing._GONE), and a moment later drops it by
    itself (in about half the runs of 2 November 2026 it was gone by the first read)."""
    if v1:
        logins = [a.login for a in await sharing._read(owner_login, path)]
        assert all(x == keep or sharing._GONE.fullmatch(x) for x in logins), logins
    else:
        with pytest.raises(dav.ServerError):
            await sharing._read(owner_login, path)


EV = {"summary": "Quarterly review", "start": "2026-11-24T15:00:00Z", "end": "2026-11-24T16:00:00Z"}


async def test_share_view_edit_reshare_revoke(people):
    """ACT calendar.share, ACT calendar.user_shared, ACT calendar.share_notices: the levels end to end."""
    users, clients = people
    alice, bob, carol = clients["alice"], clients["bob"], clients["carol"]
    work = await _work(users, clients)
    cid = work["calendar_id"]

    r = await alice.get(f"{API}/calendar/collections/{cid}/shares")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["own"] is True and got["people"] == [] and got["policy"] == "anyone"
    cands = {c["id"] for c in got["candidates"]}
    assert {users["bob"].id, users["carol"].id, users["dave"].id} <= cands and users["alice"].id not in cands

    # View: Bob sees it under Shared, read-only; a write is refused with the plain message.
    r = await alice.put(f"{API}/calendar/collections/{cid}/shares/{users['bob'].id}", json={"level": "view"})
    assert r.status_code == 200, r.text
    assert [(p["id"], p["level"]) for p in r.json()["people"]] == [(users["bob"].id, "view")]
    r = await bob.get(f"{API}/calendar/share-notices")
    assert [n["name"] for n in r.json()["notices"]] == ["Work"] and r.json()["notices"][0]["by"] == "Alice"
    w_b = _named(await _sync(users["bob"]), "Work")
    assert w_b and w_b["read_only"] is True
    assert w_b["shared"] == {"kind": "user", "owner": "Alice", "owner_id": users["alice"].id, "level": "view",
                             "access": "read", "can_share": False}
    r = await bob.post(f"{API}/calendar/events", json={**EV, "calendar": w_b["url"]})
    assert r.status_code == 400 and "read-only for you" in r.json()["detail"], r.text
    # No rename, no delete; a colour is Bob's own.
    r = await bob.patch(f"{API}/calendar/collections/{w_b['calendar_id']}", json={"name": "Mine"})
    assert r.status_code == 409 and "Alice" in r.json()["detail"], r.text
    r = await bob.delete(f"{API}/calendar/collections/{w_b['calendar_id']}", params={"confirm": "Work"})
    assert r.status_code == 409
    r = await bob.patch(f"{API}/calendar/collections/{w_b['calendar_id']}", json={"color": "#AA0000"})
    assert r.status_code == 200 and r.json()["color_local"] is True
    # A view-level sharee cannot open the share list.
    r = await bob.get(f"{API}/calendar/collections/{w_b['calendar_id']}/shares")
    assert r.status_code == 403
    # Alice's list says it is shared.
    assert _named(await _sync(users["alice"]), "Work")["sharing"] == {"people": 1, "others": 0}

    # Edit: Carol writes; Alice sees the event.
    r = await alice.put(f"{API}/calendar/collections/{cid}/shares/{users['carol'].id}", json={"level": "edit"})
    assert r.status_code == 200, r.text
    w_c = _named(await _sync(users["carol"]), "Work")
    assert w_c and w_c["read_only"] is False and w_c["shared"]["level"] == "edit"
    r = await carol.post(f"{API}/calendar/events", json={**EV, "calendar": w_c["url"]})
    assert r.status_code == 200, r.text
    await _sync(users["alice"])
    r = await alice.get(f"{API}/calendar/agenda", params={"from": "2026-11-24", "to": "2026-11-25"})
    assert any(e["summary"] == EV["summary"] for e in r.json()["events"]), r.json()
    r = await carol.get(f"{API}/calendar/collections/{w_c['calendar_id']}/shares")
    assert r.status_code == 403

    # Share: Carol re-shares with Dave, but cannot change her own level.
    r = await alice.put(f"{API}/calendar/collections/{cid}/shares/{users['carol'].id}", json={"level": "share"})
    assert r.status_code == 200, r.text
    w_c = _named(await _sync(users["carol"]), "Work")
    assert w_c["shared"]["can_share"] is True
    r = await carol.get(f"{API}/calendar/collections/{w_c['calendar_id']}/shares")
    assert r.status_code == 200 and r.json()["own"] is False and r.json()["owner"]["id"] == users["alice"].id
    r = await carol.put(f"{API}/calendar/collections/{w_c['calendar_id']}/shares/{users['dave'].id}",
                        json={"level": "view"})
    assert r.status_code == 200, r.text
    assert _named(await _sync(users["dave"]), "Work")["shared"]["owner"] == "Alice"
    r = await carol.put(f"{API}/calendar/collections/{w_c['calendar_id']}/shares/{users['carol'].id}",
                        json={"level": "view"})
    assert r.status_code == 400
    r = await carol.put(f"{API}/calendar/collections/{w_c['calendar_id']}/shares/{users['alice'].id}",
                        json={"level": "view"})
    assert r.status_code == 400

    # Revoke: Bob's copy goes at his next sync, and so does the notice.
    r = await alice.delete(f"{API}/calendar/collections/{cid}/shares/{users['bob'].id}")
    assert r.status_code == 200, r.text
    assert not _named(await _sync(users["bob"]), "Work")
    assert (await bob.get(f"{API}/calendar/share-notices")).json()["notices"] == []
    # Dismissing notices.
    assert (await dave_notices(clients)) == ["Work"]
    assert (await clients["dave"].delete(f"{API}/calendar/share-notices")).status_code == 200
    assert (await dave_notices(clients)) == []


async def dave_notices(clients) -> list[str]:
    r = await clients["dave"].get(f"{API}/calendar/share-notices")
    return [n["name"] for n in r.json()["notices"]]


async def _store_group(users, member: str) -> tuple[str, str]:
    """A store group with `member` in it: (account id, login). Destroyed at the end of the session
    (after the member's account), or by the caller."""
    from mailr.core.store import service_client
    from mailr.core.users import _domain_id, _mgmt
    from mailr.dev import store_accounts

    gname = f"grp-test-{uuid.uuid4().hex[:6]}"
    r = await _mgmt("x:Account/set", {"create": {"g": {
        "@type": "Group", "name": gname, "domainId": await _domain_id(), "description": "Family",
        "roles": {"@type": "Custom", "roleIds": {}}, **store_accounts.mark()}}})
    gid = r["created"]["g"]["id"]
    store_accounts.note_created(service_client().base_url, gid, gname)
    glogin = f"{gname}@{(await _login_of(users[member])).split('@')[1]}"
    await _mgmt("x:Account/set", {"update": {users[member].stalwart_account_id: {f"memberGroupIds/{gid}": True}}})
    return gid, glogin


async def _remember_acl(users, path: str) -> None:
    """The owner's ACL summary, as Alice's next sync keeps it."""
    from mailr.db import sessionmaker

    async with sessionmaker()() as db:
        conn = await builtin.find(db, users["alice"].id)
        await sharing._remember(db, conn, path, await _acl(users["alice"], path), await sharing.logins(db))
        await db.commit()


async def _dav_put(login: str, path: str) -> int:
    """A PUT as `login` (a phone would do the same); the HTTP status."""
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\nBEGIN:VEVENT\r\nUID:x1\r\n"
           "DTSTAMP:20261004T120000Z\r\nDTSTART:20261010T100000Z\r\nSUMMARY:x\r\nEND:VEVENT\r\n"
           "END:VCALENDAR\r\n")
    async with dav.DavClient(builtin.dav_url(), f"{login}%{ADMIN.split(':')[0]}", ADMIN.split(":", 1)[1],
                             internal=True) as c:
        try:
            await c.put(groups._url(path) + f"{uuid.uuid4().hex}.ics", ics, if_none_match=True)
        except dav.DavError as e:
            return e.status
    return 201


async def test_a_groups_grant_counts_as_the_server_counts_it(people):
    """Bob's own grant on Alice's calendar and a grant to a store group of his (Stalwart's rule,
    verified on both versions, sharing.access_of). mailr puts Bob's own grant before the group's.
    - Own edit, group view: Bob writes on both versions.
    - Own view, group edit: Stalwart 1.x checks the union, so Bob writes, from a phone and in
      mailr ("Can edit (through Family)"). Stalwart 0.16 takes his own (first) grant: read-only in
      mailr, and the server refuses a phone's write.
    - Own view, group share: Bob may re-share on both versions (write-acl is the union).
    ACT calendar.user_shared ACT calendar.share"""
    from mailr.core.users import _mgmt

    users, clients = people
    work = await _work(users, clients)
    path = groups.path_of(work["url"])
    gid, glogin = await _store_group(users, "bob")
    v1 = await _v1()
    try:
        alogin, bob_login = await _login_of(users["alice"]), await _login_of(users["bob"])
        await _sync(users["bob"])
        # The group's read grant, first.
        await sharing.write_acl(alogin, path, [sharing.ace_for(glogin, "view")], {})
        await groups.invalidate_caches()
        w_b = _named(await _sync(users["bob"]), "Work")
        assert w_b and w_b["read_only"] is True and w_b["shared"]["kind"] == "user"

        r = await clients["alice"].put(f"{API}/calendar/collections/{work['calendar_id']}/shares/{users['bob'].id}",
                                       json={"level": "edit"})
        assert r.status_code == 200, r.text
        assert r.json()["others"] == 1  # the group's grant is kept
        aces = await _acl(users["alice"], path)
        assert [a.login for a in aces] == [bob_login, glogin.lower()], [a.login for a in aces]
        assert [a.level for a in aces] == ["edit", "view"]
        w_b = _named(await _sync(users["bob"]), "Work")
        assert w_b["read_only"] is False and w_b["shared"]["level"] == "edit" and "via" not in w_b["shared"]
        r = await clients["bob"].post(f"{API}/calendar/events", json={**EV, "calendar": w_b["url"]})
        assert r.status_code == 200, r.text

        # Reverse: the group may write, Bob may only view.
        await sharing.write_acl(alogin, path, [sharing.ace_for(bob_login, "view"), sharing.ace_for(glogin, "edit")],
                                {bob_login: users["bob"].id})
        await _remember_acl(users, path)
        w_b = _named(await _sync(users["bob"]), "Work")
        later = {**EV, "summary": "Planning", "start": "2026-11-25T15:00:00Z", "end": "2026-11-25T16:00:00Z"}
        r = await clients["bob"].post(f"{API}/calendar/events", json={**later, "calendar": w_b["url"]})
        if v1:
            assert w_b["read_only"] is False, w_b
            assert w_b["shared"] == {"kind": "user", "owner": "Alice", "owner_id": users["alice"].id, "level": "edit",
                                     "access": "write", "can_share": False, "own_level": "view", "via": ["Family"]}
            assert await _dav_put(bob_login, path) == 201
            assert r.status_code == 200, r.text
        else:
            assert w_b["read_only"] is True and w_b["shared"]["level"] == "view" and "via" not in w_b["shared"]
            assert await _dav_put(bob_login, path) == 403
            assert r.status_code == 400 and "read-only for you" in r.json()["detail"], r.text
        # The owner's dialog says where Bob's access comes from, when a group gives more.
        got = (await clients["alice"].get(f"{API}/calendar/collections/{work['calendar_id']}/shares")).json()
        [row] = got["people"]
        assert row["level"] == "view"
        assert row.get("access") == ({"level": "edit", "can_share": False, "via": ["Family"]} if v1 else None)

        # Own view, group share: Bob re-shares (the server allows his ACL change on both versions).
        await sharing.write_acl(alogin, path, [sharing.ace_for(bob_login, "view"), sharing.ace_for(glogin, "share")],
                                {bob_login: users["bob"].id})
        await _remember_acl(users, path)
        w_b = _named(await _sync(users["bob"]), "Work")
        assert w_b["shared"]["can_share"] is True and w_b["shareable"] is True
        r = await clients["bob"].put(f"{API}/calendar/collections/{w_b['calendar_id']}/shares/{users['carol'].id}",
                                     json={"level": "view"})
        assert r.status_code == 200, r.text
        assert _named(await _sync(users["carol"]), "Work")
    finally:
        await _mgmt("x:Account/set", {"update": {users["bob"].stalwart_account_id: {f"memberGroupIds/{gid}": None}}})
        await _mgmt("x:Account/set", {"destroy": [gid]})


async def test_policy_limits_who_and_when(people):
    """ACT settings.calendar_sharing: "groups" offers only people who share a group; "nobody" refuses."""
    users, clients = people
    work = await _work(users, clients)
    cid = work["calendar_id"]
    r = await clients["admin"].put(f"{API}/admin/calendars", json={"builtin": True, "sharing": "groups"})
    assert r.status_code == 200 and r.json()["sharing"] == "groups", r.text
    got = (await clients["alice"].get(f"{API}/calendar/collections/{cid}/shares")).json()
    assert [c["id"] for c in got["candidates"]] == [users["bob"].id]
    r = await clients["alice"].put(f"{API}/calendar/collections/{cid}/shares/{users['carol'].id}", json={"level": "view"})
    assert r.status_code == 403
    r = await clients["alice"].put(f"{API}/calendar/collections/{cid}/shares/{users['bob'].id}", json={"level": "view"})
    assert r.status_code == 200
    r = await clients["admin"].put(f"{API}/admin/calendars", json={"builtin": True, "sharing": "nobody"})
    assert r.json()["sharing"] == "nobody"
    got = (await clients["alice"].get(f"{API}/calendar/collections/{cid}/shares")).json()
    assert got["candidates"] == [] and [p["id"] for p in got["people"]] == [users["bob"].id]
    r = await clients["alice"].put(f"{API}/calendar/collections/{cid}/shares/{users['bob'].id}", json={"level": "edit"})
    assert r.status_code == 403
    # Stopping a share still works.
    r = await clients["alice"].delete(f"{API}/calendar/collections/{cid}/shares/{users['bob'].id}")
    assert r.status_code == 200 and r.json()["people"] == []


FB_QUERY = ('<?xml version="1.0"?><c:free-busy-query xmlns:c="urn:ietf:params:xml:ns:caldav">'
            '<c:time-range start="20261101T000000Z" end="20261201T000000Z"/></c:free-busy-query>')


async def test_free_busy_only_grant_on_the_server(people):
    """What the server gives a person with only CalDAV read-free-busy on a calendar (canary).
    Stalwart 0.16: the calendar is not listed and the free-busy-query answers 403, so mailr offers no
    such level there. Stalwart 1.0.1: still not listed, PROPFIND, calendar-query and GET answer 403,
    and the free-busy-query answers 200 with the busy times and nothing else (calendar/shared_busy.py
    is built on that). If this fails, the server's behaviour changed."""
    users, clients = people
    work = await _work(users, clients)
    path = groups.path_of(work["url"])
    r = await clients["alice"].post(f"{API}/calendar/events", json={**EV, "calendar": work["url"]})
    assert r.status_code == 200, r.text
    alogin, blogin = await _login_of(users["alice"]), await _login_of(users["bob"])
    await sharing.write_acl(alogin, path, [sharing.ace_for(blogin, "freebusy")], {blogin: users["bob"].id})
    assert [(a.login, a.level) for a in await _acl(users["alice"], path)] == [(blogin, "freebusy")]
    v1 = await _v1()
    async with dav.DavClient(builtin.dav_url(), f"{blogin}%{ADMIN.split(':')[0]}", ADMIN.split(":", 1)[1],
                             internal=True) as c:
        listed = [groups.path_of(i.href) for i in await c.collections(groups._url(groups.home_of(blogin)))]
        assert path not in listed
        r = await c.request("REPORT", groups._url(path), body=FB_QUERY,
                            headers={"Depth": "1", "Content-Type": "application/xml"})
        q = await c.request("REPORT", groups._url(path), body=(
            '<?xml version="1.0"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            '<d:prop><d:getetag/></d:prop><c:filter><c:comp-filter name="VCALENDAR"/></c:filter></c:calendar-query>'),
            headers={"Depth": "1", "Content-Type": "application/xml"})
        f = await c.request("PROPFIND", groups._url(path), body=dav._propfind_body(["d:displayname"]),
                            headers={"Depth": "0", "Content-Type": "application/xml"})
    assert q.status_code == 403 and f.status_code == 403, (q.status_code, f.status_code)
    if v1:
        assert r.status_code == 200, r.status_code
        assert "FREEBUSY;FBTYPE=BUSY:20261124T150000Z/20261124T160000Z" in r.text and "Quarterly" not in r.text
    else:
        assert r.status_code == 403, r.status_code


async def test_free_busy_only_share(people):
    """ACT calendar.share, ACT calendar.user_shared: the "free/busy only" level. Stalwart 1.x: the
    dialog offers it; Bob's list shows "Work" read-only with busy blocks titled "Busy" and nothing
    else; View shows the events; back to free/busy only, no title is left in Bob's mirror. 0.16:
    the level is not offered and a request for it is refused."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from mailr.calendar import shared_busy
    from mailr.db import sessionmaker
    from mailr.models import CalObject

    users, clients = people
    alice, bob = clients["alice"], clients["bob"]
    work = await _work(users, clients)
    cid = work["calendar_id"]
    # Ten days from now: the busy times mailr asks for start 62 days back (shared_busy.window).
    d = (datetime.now(UTC) + timedelta(days=10)).date()
    ev = {"summary": "Quarterly review", "start": f"{d}T15:00:00Z", "end": f"{d}T16:00:00Z"}
    r = await alice.post(f"{API}/calendar/events", json={**ev, "location": "Room 4", "calendar": work["url"]})
    assert r.status_code == 200, r.text
    v1 = await _v1()
    levels = (await alice.get(f"{API}/calendar/collections/{cid}/shares")).json()["levels"]
    r = await alice.put(f"{API}/calendar/collections/{cid}/shares/{users['bob'].id}", json={"level": "freebusy"})
    if not v1:
        assert levels == ["view", "edit", "share"]
        assert r.status_code == 400 and "free/busy" in r.json()["detail"], r.text
        assert (await alice.get(f"{API}/calendar/collections/{cid}/shares")).json()["people"] == []
        return
    assert levels == ["freebusy", "view", "edit", "share"]
    assert r.status_code == 200, r.text
    assert [(p["id"], p["level"]) for p in r.json()["people"]] == [(users["bob"].id, "freebusy")]
    w_b = _named(await _sync(users["bob"]), "Work")
    assert w_b and w_b["read_only"] is True and w_b["shareable"] is False
    assert w_b["shared"] == {"kind": "user", "owner": "Alice", "owner_id": users["alice"].id, "level": "freebusy",
                             "access": "freebusy", "can_share": False}
    day = {"from": str(d), "to": str(d + timedelta(days=1))}

    async def bobs_events() -> list[dict]:
        evs = (await bob.get(f"{API}/calendar/agenda", params=day)).json()["events"]
        return [e for e in evs if e["calendar_id"] == w_b["calendar_id"]]

    [e] = await bobs_events()
    assert e["summary"] == "Busy" and e["shared_busy"] is True and not e["location"]
    assert (e["start"], e["end"]) == (f"{d}T15:00:00+00:00", f"{d}T16:00:00+00:00")
    r = await bob.get(f"{API}/calendar/freebusy", params=day)
    assert r.status_code == 200 and r.json()["busy"], r.text
    r = await bob.post(f"{API}/calendar/events", json={**ev, "calendar": w_b["url"]})
    assert r.status_code == 400, r.text
    # View: the event itself; back to free/busy only: Bob's mirror keeps no title or place.
    r = await alice.put(f"{API}/calendar/collections/{cid}/shares/{users['bob'].id}", json={"level": "view"})
    assert r.status_code == 200, r.text
    w_b2 = _named(await _sync(users["bob"]), "Work")
    assert w_b2["calendar_id"] == w_b["calendar_id"] and w_b2["shared"]["level"] == "view"
    [e] = await bobs_events()
    assert e["summary"] == ev["summary"] and not e.get("shared_busy")
    r = await alice.put(f"{API}/calendar/collections/{cid}/shares/{users['bob'].id}", json={"level": "freebusy"})
    assert r.status_code == 200, r.text
    assert _named(await _sync(users["bob"]), "Work")["shared"]["level"] == "freebusy"
    [e] = await bobs_events()
    assert e["summary"] == "Busy"
    async with sessionmaker()() as db:
        rows = (await db.execute(select(CalObject).where(CalObject.collection_id == w_b["calendar_id"]))).scalars().all()
    assert rows and all(shared_busy.is_block(o.uid) or not (o.summary or o.location or o.ics) for o in rows)
    assert not any("Quarterly" in (o.summary or "") + (o.ics or "") for o in rows)
    # Stopped: gone at Bob's next sync.
    r = await alice.delete(f"{API}/calendar/collections/{cid}/shares/{users['bob'].id}")
    assert r.status_code == 200, r.text
    assert not _named(await _sync(users["bob"]), "Work")


async def test_deleted_user_grants_are_removed(people):
    """Deleting a user takes their grants off other people's calendars first. Without that, Stalwart
    answers 500 to the owner's ACL read; the cleanup repairs that too."""
    from mailr.core.users import _mgmt

    users, clients = people
    work = await _work(users, clients)
    cid, path = work["calendar_id"], groups.path_of(work["url"])
    for who in ("carol", "dave", "admin"):
        r = await clients["alice"].put(f"{API}/calendar/collections/{cid}/shares/{users[who].id}", json={"level": "edit"})
        assert r.status_code == 200, r.text
    admin = await _login_of(users["admin"])  # a live grant: every repair below keeps it

    async def logins():
        return sorted(a.login for a in await _acl(users["alice"], path))

    # Carol through the admin route: her grant goes before her account.
    r = await clients["admin"].delete(f"{API}/admin/users/{users['carol'].id}", params={"confirm": users["carol"].email})
    assert r.status_code == 200, r.text
    assert await logins() == sorted([await _login_of(users["dave"]), admin])
    # Dave's account destroyed behind mailr's back: the DAV read fails, and read_acl repairs it
    # (heal through JMAP) and reads again.
    dlogin = await _login_of(users["dave"])
    await _mgmt("x:Account/set", {"destroy": [users["dave"].stalwart_account_id]})
    alogin = await _login_of(users["alice"])
    v1 = await _v1()
    await _stale_grant_shows(alogin, path, v1, admin)
    assert [a.login for a in await sharing.read_acl(users["alice"], alogin, path)] == [admin]
    assert dlogin
    # Bob's too, after a new grant: the cleanup at start (and after a deletion) repairs every owner.
    r = await clients["alice"].put(f"{API}/calendar/collections/{cid}/shares/{users['bob'].id}", json={"level": "view"})
    assert r.status_code == 200, r.text
    assert await logins() == sorted([await _login_of(users["bob"]), admin])
    await _mgmt("x:Account/set", {"destroy": [users["bob"].stalwart_account_id]})
    await _stale_grant_shows(alogin, path, v1, admin)
    out = await sharing.cleanup()
    # Stalwart 1.x also drops such a grant itself, a moment after the account is destroyed, so the
    # cleanup may find nothing left; the ACL below is what counts.
    assert out["removed"] >= (0 if v1 else 1), out
    assert [a.login for a in await sharing._read(alogin, path)] == [admin]
    # A grant to an account the server does not know is refused, not written.
    with pytest.raises(dav.DavError):
        await sharing.write_acl(alogin, path, [*await sharing._read(alogin, path),
                                               sharing.ace_for("nobody-" + uuid.uuid4().hex[:6], "view")], {admin: 1})
    assert [a.login for a in await sharing._read(alogin, path)] == [admin]
    # Nothing is left to repair on Alice's calendars, and an owner without a store account has
    # nothing to repair at all.
    assert await sharing.heal(users["alice"]) == 0
    from sqlalchemy import update

    from mailr.db import sessionmaker
    from mailr.models import User

    async with sessionmaker()() as db:
        await db.execute(update(User).where(User.id == users["bob"].id).values(stalwart_account_id=None))
        await db.commit()
        assert await sharing.heal(await db.get(User, users["bob"].id)) == 0
