# ACT-SURFACES: api
"""Sharing a calendar between users (calendar/sharing.py), without a calendar server: reading and
writing Stalwart's ACL (order, base privileges, a group's grant kept after the people), the policy
and who may be picked, who may re-share, the sharee's marks from the connector state, notices,
and the routes' checks (API tokens refused, another user's calendar not found).

The end-to-end run against a real Stalwart is tests/test_calendar_sharing_stalwart.py (opt-in).

Actions covered: calendar.share, calendar.user_shared, calendar.share_notices,
settings.calendar_sharing.
"""

from __future__ import annotations

import contextlib
import uuid
import xml.etree.ElementTree as ET

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from mailr.calendar import builtin, dav, groups, sharing
from mailr.db import sessionmaker
from mailr.models import CalCollection, Connector, Job, User

API = "/api/v1"
BASE = "http://stalwart:8080"
C = sharing.C

# What Stalwart 0.16.23 answers to PROPFIND d:acl (shape copied from a real server).
ACL_XML = """<?xml version="1.0" encoding="UTF-8"?><D:multistatus xmlns:D="DAV:" xmlns:A="urn:ietf:params:xml:ns:caldav">
<D:response><D:href>/dav/cal/ann%40example.test/work/</D:href><D:propstat><D:prop><D:acl>
<D:ace><D:principal><D:href>/dav/pal/bob%40example.test/</D:href></D:principal><D:grant>
 <D:privilege><D:read-current-user-privilege-set/></D:privilege><D:privilege><D:read/></D:privilege></D:grant></D:ace>
<D:ace><D:principal><D:href>/dav/pal/cal%40example.test/</D:href></D:principal><D:grant>
 <D:privilege><D:read/></D:privilege><D:privilege><D:write-properties/></D:privilege><D:privilege><D:write/></D:privilege>
 <D:privilege><D:write-content/></D:privilege><D:privilege><D:read-current-user-privilege-set/></D:privilege></D:grant></D:ace>
<D:ace><D:principal><D:href>/dav/pal/grp-home-abc%40example.test/</D:href></D:principal><D:grant>
 <D:privilege><D:write-acl/></D:privilege><D:privilege><D:read/></D:privilege><D:privilege><D:read-acl/></D:privilege>
 <D:privilege><D:write/></D:privilege><D:privilege><A:read-free-busy/></D:privilege></D:grant></D:ace>
</D:acl></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response></D:multistatus>"""


def _aces() -> list[sharing.Ace]:
    return sharing.parse_acl(ET.fromstring(ACL_XML).find("d:response", dav.NS))


# ---------------- the ACL ----------------


def test_parse_acl_reads_logins_levels_and_order():
    aces = _aces()
    assert [(a.login, a.level) for a in aces] == [("bob@example.test", "view"), ("cal@example.test", "edit"),
                                                  ("grp-home-abc@example.test", "share")]


def test_acl_body_writes_base_privileges_only():
    body = sharing.acl_body(_aces())
    root = ET.fromstring(body)
    got = [[p.tag for p in ace.findall("d:grant/d:privilege/*", dav.NS)] for ace in root.findall("d:ace", dav.NS)]
    assert got == [["{DAV:}read"], ["{DAV:}read", "{DAV:}write"],
                   ["{DAV:}write-acl", "{DAV:}read", "{DAV:}write", f"{{{C}}}read-free-busy"]]
    assert "read-current-user-privilege-set" not in body and "write-content" not in body
    assert "<c:read-free-busy/>" in body  # the CalDAV namespace, not DAV:


def test_people_go_before_groups_and_keep_their_place():
    """Stalwart takes the first ACE naming the user: a person's own grant must come before a group's
    (ACT calendar.share)."""
    people = {"bob@example.test": 2, "cal@example.test": 3, "dee@example.test": 4}
    group_first = [_aces()[2], _aces()[0], _aces()[1]]
    out = sharing.with_grant(group_first, "dee@example.test", "edit", people)
    assert [a.login for a in out] == ["bob@example.test", "cal@example.test", "dee@example.test",
                                      "grp-home-abc@example.test"]
    # A level change keeps the person's place; the group's grant is untouched.
    out2 = sharing.with_grant(out, "bob@example.test", "share", people)
    assert [(a.login, a.level) for a in out2] == [("bob@example.test", "share"), ("cal@example.test", "edit"),
                                                  ("dee@example.test", "edit"), ("grp-home-abc@example.test", "share")]
    # Stop: gone; duplicates of one person collapse into one entry.
    dup = out2 + [sharing.ace_for("cal@example.test", "view")]
    out3 = sharing.with_grant(dup, "cal@example.test", "view", people)
    assert [(a.login, a.level) for a in out3] == [("bob@example.test", "share"), ("cal@example.test", "view"),
                                                  ("dee@example.test", "edit"), ("grp-home-abc@example.test", "share")]
    out4 = sharing.with_grant(out3, "bob@example.test", None, people)
    assert [a.login for a in out4] == ["cal@example.test", "dee@example.test", "grp-home-abc@example.test"]
    assert sharing.summary(out4, people) == [["cal@example.test", "view", True], ["dee@example.test", "edit", True],
                                             ["grp-home-abc@example.test", "share", False]]


def test_levels_map_to_privileges():
    assert sharing.PRIVS == {"view": ["read"], "edit": ["read", "write"], "share": ["read", "write", "write-acl"]}
    for lvl in sharing.LEVELS:
        assert sharing.level_of(sharing.ace_for("x@example.test", lvl).privs) == lvl
    assert sharing.level_of([f"{{{C}}}read-free-busy"]) is None


# ---------------- people, connectors and a fake ACL ----------------


class FakeAcl:
    """read_acl / write_acl in memory, per calendar path."""

    def __init__(self) -> None:
        self.acl: dict[str, list[sharing.Ace]] = {}
        self.writes = 0

    async def read(self, owner, owner_login, path):
        return list(self.acl.get(path, []))

    async def write(self, owner_login, path, aces, user_logins):
        self.writes += 1
        self.acl[path] = list(aces)


@pytest_asyncio.fixture
async def acl(monkeypatch):
    fake = FakeAcl()
    monkeypatch.setattr(sharing, "read_acl", fake.read)
    monkeypatch.setattr(sharing, "write_acl", fake.write)
    made: list[int] = []
    fake.made = made
    async with sessionmaker()() as db:
        await builtin.set_instance_enabled(db, True, "everyone")
        await sharing.set_policy(db, "anyone")
        await db.commit()
    yield fake
    async with sessionmaker()() as db:
        if made:
            await db.execute(delete(User).where(User.id.in_(made)))
        await sharing.set_policy(db, "anyone")
        await builtin.set_instance_enabled(db, False, "everyone")
        await db.commit()


def _login(u: User) -> str:
    return f"u{u.id}-{uuid.uuid5(uuid.NAMESPACE_DNS, u.email).hex[:4]}@example.test"


async def _connect(user_id: int, *, own_cal: str = "work") -> tuple[int, int, str]:
    """A built-in connector and one own calendar for the user: (connector id, calendar id, path)."""
    async with sessionmaker()() as db:
        u = await db.get(User, user_id)
        login = _login(u)
        conn = await builtin.find(db, user_id)
        if conn is None:
            conn = Connector(user_id=user_id, kind="caldav", name=builtin.NAME, mode="mirror",
                             config={"account": login}, state={}, enabled=True, builtin=True, status="idle")
            db.add(conn)
            await db.flush()
        path = groups.home_of(login) + f"{own_cal}/"
        coll = CalCollection(user_id=user_id, connector_id=conn.id, href=BASE + path,
                             displayname=own_cal.title(), components=["VEVENT"])
        db.add(coll)
        await db.commit()
        return conn.id, coll.id, path


async def _person(acl: FakeAcl, name: str, groups_: list[str] | None = None, *, disabled: bool = False) -> User:
    async with sessionmaker()() as db:
        u = User(email=f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.com", name=name, role="user", settings={},
                 assigned_groups=groups_ or [], disabled=disabled, stalwart_account_id=f"x{uuid.uuid4().hex[:6]}")
        db.add(u)
        await db.commit()
        acl.made.append(u.id)
    await _connect(u.id)
    return u


async def _foreign(user_id: int, owner: User, path: str, *, read_only: bool, level: str) -> int:
    """The sharee's mirror row of a calendar someone shares, and the state the sync keeps."""
    async with sessionmaker()() as db:
        conn = await builtin.find(db, user_id)
        coll = CalCollection(user_id=user_id, connector_id=conn.id, href=BASE + path, displayname="Work",
                             components=["VEVENT"], read_only=read_only)
        db.add(coll)
        st = dict(conn.state or {})
        cd = dict(st.get("caldav") or {})
        cd["shared_in"] = {**(cd.get("shared_in") or {}), path: {"owner_id": owner.id, "level": level}}
        conn.state = {**st, "caldav": cd}
        await db.commit()
        return coll.id


# ---------------- policy and candidates ----------------


async def test_policy_filters_the_people_offered(acl, app):
    """ACT settings.calendar_sharing: anyone, people who share a group, or nobody; disabled people
    and people without a mailr calendar are never offered."""
    ann = await _person(acl, "Ann", ["Home"])
    bob = await _person(acl, "Bob", ["Home", "Work"])
    cal = await _person(acl, "Cal", ["Work"])
    dee = await _person(acl, "Dee", ["Home"], disabled=True)
    async with sessionmaker()() as db:
        nocal = User(email=f"n-{uuid.uuid4().hex[:8]}@example.com", name="Nocal", settings={}, assigned_groups=["Home"],
                     stalwart_account_id="xnocal")
        db.add(nocal)
        await db.commit()
        acl.made.append(nocal.id)
        ids = {u.id for u in await sharing.eligible(db, ann)}
        assert {bob.id, cal.id} <= ids and not ids & {ann.id, dee.id, nocal.id}
        await sharing.set_policy(db, "groups")
        ids = {u.id for u in await sharing.eligible(db, ann)}
        assert bob.id in ids and cal.id not in ids
        assert {u.id for u in await sharing.eligible(db, cal)} & {bob.id, ann.id} == {bob.id}
        await sharing.set_policy(db, "nobody")
        assert await sharing.eligible(db, ann) == []
        with pytest.raises(ValueError):
            await sharing.set_policy(db, "everyone")
        await db.rollback()
    assert sharing.allowed_by("anyone", ann, ann) is False


# ---------------- sharing, re-sharing, notices ----------------


async def test_share_change_stop_and_the_sharees_marks(acl, app):
    """ACT calendar.share, ACT calendar.share_notices, ACT calendar.user_shared."""
    ann, bob, cal = await _person(acl, "Ann"), await _person(acl, "Bob"), await _person(acl, "Cal")
    _c, work_id, path = await _connect(ann.id, own_cal="trips")
    async with sessionmaker()() as db:
        a = await db.get(User, ann.id)
        coll = await db.get(CalCollection, work_id)
        out = await sharing.set_level(db, a, coll, bob.id, "view")
        await db.commit()
        assert [(p["id"], p["level"]) for p in out["people"]] == [(bob.id, "view")]
        assert out["own"] is True and bob.id not in {c["id"] for c in out["candidates"]}
        # The owner's summary is kept for the sharees' syncs and the owner's list.
        conn = await builtin.find(db, ann.id)
        assert conn.state["caldav"]["acl"][path] == [[_login(bob), "view", True]]
        # Bob's notice and a queued sync.
        notes = await sharing.notices(db, bob.id)
        assert [(n["name"], n["by"], n["path"]) for n in notes] == [("Trips", "Ann", path)]
        bconn = await builtin.find(db, bob.id)
        assert (await db.execute(select(Job).where(Job.kind == "calendar.sync", Job.user_id == bob.id))).first()
        assert bconn is not None
        # A level change sends no second notice; stopping removes it.
        await sharing.set_level(db, a, coll, bob.id, "edit")
        assert len(await sharing.notices(db, bob.id)) == 1
        assert [a_.level for a_ in acl.acl[path]] == ["edit"]
        await sharing.set_level(db, a, coll, bob.id, None)
        await db.commit()
        assert acl.acl[path] == [] and await sharing.notices(db, bob.id) == []
        # Ann's own calendar now says nothing about sharing; refusals.
        with pytest.raises(sharing.SharingError):
            await sharing.set_level(db, a, coll, ann.id, "view")  # the owner
        with pytest.raises(sharing.SharingError) as e:
            await sharing.set_level(db, a, coll, 999999999, "view")
        assert e.value.status == 404
        await sharing.set_policy(db, "nobody")
        with pytest.raises(sharing.SharingError) as e:
            await sharing.set_level(db, a, coll, cal.id, "view")
        assert e.value.status == 403
        await sharing.set_policy(db, "anyone")
        await db.commit()

    # The sharee's view: marked with the owner, read-only for view.
    fid = await _foreign(bob.id, ann, path, read_only=True, level="view")
    async with sessionmaker()() as db:
        ov = await sharing.overrides_for(db, bob.id)
        assert ov[BASE + path] == {"shared": {"kind": "user", "owner": "Ann", "owner_id": ann.id, "level": "view",
                                              "access": "read", "can_share": False}}
        conn = await builtin.find(db, ann.id)
        conn.state = {**conn.state, "caldav": {**conn.state["caldav"], "acl": {path: [[_login(bob), "view", True],
                                                                                     ["grp-x@example.test", "view", False]]}}}
        await db.commit()
        ov = await sharing.overrides_for(db, ann.id)
        assert ov[BASE + path] == {"sharing": {"people": 1, "others": 1}}
    assert fid


async def test_resharing_needs_the_share_level(acl, app):
    """A sharee with view or edit cannot open the list; with share they add people within the
    policy, but never change their own entry or the owner's."""
    ann, bob, cal, dee = (await _person(acl, "Ann"), await _person(acl, "Bob"), await _person(acl, "Cal"),
                          await _person(acl, "Dee"))
    _c, work_id, path = await _connect(ann.id, own_cal="rota")
    fid = await _foreign(bob.id, ann, path, read_only=False, level="edit")
    async with sessionmaker()() as db:
        a, b = await db.get(User, ann.id), await db.get(User, bob.id)
        await sharing.set_level(db, a, await db.get(CalCollection, work_id), bob.id, "edit")
        mine = await db.get(CalCollection, fid)
        with pytest.raises(sharing.SharingError) as e:
            await sharing.describe(db, b, mine)
        assert e.value.status == 403
        await sharing.set_level(db, a, await db.get(CalCollection, work_id), bob.id, "share")
        got = await sharing.describe(db, b, mine)
        assert got["own"] is False and got["owner"]["id"] == ann.id
        assert [p["you"] for p in got["people"]] == [True]
        assert ann.id not in {c["id"] for c in got["candidates"]}
        await sharing.set_level(db, b, mine, cal.id, "view")
        assert [x.login for x in acl.acl[path]] == [_login(bob), _login(cal)]
        with pytest.raises(sharing.SharingError):
            await sharing.set_level(db, b, mine, bob.id, "view")
        with pytest.raises(sharing.SharingError):
            await sharing.set_level(db, b, mine, ann.id, "view")
        # The policy applies to the person re-sharing.
        await sharing.set_policy(db, "groups")
        with pytest.raises(sharing.SharingError) as e:
            await sharing.set_level(db, b, mine, dee.id, "view")
        assert e.value.status == 403
        await db.rollback()


async def test_sharing_only_own_calendars_of_the_builtin_account(acl, app):
    ann = await _person(acl, "Ann")
    async with sessionmaker()() as db:
        conn = Connector(user_id=ann.id, kind="caldav", name="Fastmail", mode="mirror", config={}, state={},
                         enabled=True, status="idle")
        db.add(conn)
        await db.flush()
        ext = CalCollection(user_id=ann.id, connector_id=conn.id, href="https://caldav.example.com/cal/x/",
                            displayname="External", components=["VEVENT"])
        bconn = await builtin.find(db, ann.id)
        tasks = CalCollection(user_id=ann.id, connector_id=bconn.id, href=BASE + groups.home_of(_login(ann)) + "tasks/",
                              displayname="Tasks", components=["VTODO"])
        db.add_all([ext, tasks])
        await db.commit()
        a = await db.get(User, ann.id)
        for coll in (ext, tasks):
            with pytest.raises(sharing.SharingError):
                await sharing.target_for(db, a, coll)


# ---------------- routes ----------------


@contextlib.asynccontextmanager
async def _client(app, user: User, password: str = "share-password-123"):
    from mailr.core.auth import hash_password

    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.password_hash = hash_password(password)
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(f"{API}/auth/login", json={"email": user.email, "password": password})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        yield c


async def test_routes(acl, app):
    """ACT calendar.share: the signed-in person only; another person's calendar is not found."""
    from conftest import grant_model_access

    ann, bob = await _person(acl, "Ann"), await _person(acl, "Bob")
    await grant_model_access(ann.id)  # API tokens are a policy switch
    _c, work_id, path = await _connect(ann.id, own_cal="club")
    async with _client(app, ann) as c:
        r = await c.get(f"{API}/calendar/collections/{work_id}/shares")
        assert r.status_code == 200, r.text
        assert {"policy", "levels", "people", "candidates", "own", "owner"} <= set(r.json())
        r = await c.put(f"{API}/calendar/collections/{work_id}/shares/{bob.id}", json={"level": "edit"})
        assert r.status_code == 200 and r.json()["people"][0]["level"] == "edit"
        r = await c.put(f"{API}/calendar/collections/{work_id}/shares/{bob.id}", json={"level": "all"})
        assert r.status_code == 422
        # The calendar list says it is shared once the summary exists.
        cals = (await c.get(f"{API}/calendar/calendars")).json()
        assert next(x for x in cals if x["calendar_id"] == work_id)["sharing"] == {"people": 1, "others": 0}
        # An API token cannot share.
        r = await c.post(f"{API}/tokens", json={"name": "t", "scopes": ["read", "calendar"]})
        assert r.status_code == 200, r.text
        tok = r.json()["token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {tok}"}) as t:
        r = await t.get(f"{API}/calendar/collections/{work_id}/shares")
        assert r.status_code == 403 and "API token" in r.json()["detail"]
    async with _client(app, bob) as c:
        r = await c.get(f"{API}/calendar/collections/{work_id}/shares")
        assert r.status_code == 404
        r = await c.delete(f"{API}/calendar/collections/{work_id}/shares/{bob.id}")
        assert r.status_code == 404
        notes = (await c.get(f"{API}/calendar/share-notices")).json()["notices"]
        assert [n["name"] for n in notes] == ["Club"]
        assert (await c.delete(f"{API}/calendar/share-notices")).status_code == 200
        assert (await c.get(f"{API}/calendar/share-notices")).json()["notices"] == []


async def test_admin_policy_route(acl, app):
    """ACT settings.calendar_sharing: GET/PUT /admin/calendars carry the policy."""
    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        adm = await create_user(db, f"a-{uuid.uuid4().hex[:8]}@example.com", "Admin",
                                password="admin-password-123", role="admin")
    async with _client(app, adm, "admin-password-123") as c:
        assert (await c.get(f"{API}/admin/calendars")).json()["sharing"] == "anyone"
        r = await c.put(f"{API}/admin/calendars", json={"builtin": True, "sharing": "groups"})
        assert r.status_code == 200 and r.json()["sharing"] == "groups"
        r = await c.put(f"{API}/admin/calendars", json={"builtin": True})
        assert r.json()["sharing"] == "groups"  # omitted: unchanged
        assert (await c.put(f"{API}/admin/calendars", json={"builtin": True, "sharing": "all"})).status_code == 422


async def test_sync_marks_a_view_share_read_only(acl, app, monkeypatch):
    """The listing reports the union of grants; a person's own view grant decides writes, so the
    sync stores the calendar read-only (on_listing)."""
    ann, bob = await _person(acl, "Ann"), await _person(acl, "Bob")
    _c, _w, path = await _connect(ann.id, own_cal="board")
    async with sessionmaker()() as db:
        conn = await builtin.find(db, ann.id)
        conn.state = {"caldav": {"acl": {path: [[_login(bob), "view", True], ["grp-x@example.test", "edit", False]]}}}
        await db.commit()

    async def no_summaries(client, home, who):
        return {}

    monkeypatch.setattr(sharing, "owner_summaries", no_summaries)
    infos = [dav.CollectionInfo(href=BASE + path, displayname="Board", read_only=False),
             dav.CollectionInfo(href=BASE + groups.home_of(_login(bob)) + "work/", displayname="Work")]
    shared_in, own = await sharing.on_listing(None, bob.id, BASE + groups.home_of(_login(bob)), infos)
    assert infos[0].read_only is True and infos[1].read_only is False
    assert shared_in == {path: {"owner_id": ann.id, "level": "view", "direct": True}} and own == {}
