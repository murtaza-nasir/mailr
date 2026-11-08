# ACT-SURFACES: api
"""Group calendars and the per-group calendar policy (calendar/groups.py, calendar/builtin.py),
without a calendar server: the membership reconcile against a fake store, the "Members get a mailr
calendar" policy and what leaving a group does, how a member's mirror marks and filters group
calendars, the limits on what a member may change, the admin routes' checks, and the plain
message for a write into a read-only calendar.

The end-to-end run against a real Stalwart is tests/test_group_calendars_stalwart.py (opt-in).

Actions covered: settings.group_calendars, calendar.group_shared.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from mailr.calendar import builtin, dav, groups, writer
from mailr.config import get_settings
from mailr.core.access import EVERYONE
from mailr.db import sessionmaker
from mailr.models import (
    AccessPolicy,
    CalCollection,
    CalendarGroup,
    Connector,
    GroupCalendar,
    Job,
    User,
)

API = "/api/v1"


class FakeStore:
    """x:Account get/set (memberGroupIds, create, destroy) and x:Action, in memory. A person's
    account (one made with a password: create_user, the `user` fixture) is made in the suite's real
    store too, because create_user goes on to make that account's mailboxes there; on a fresh store
    an account id the fake made up does not exist ("Account not found")."""

    def __init__(self, real: Any = None) -> None:
        self.real = real
        self.accounts: dict[str, dict[str, Any]] = {}
        self.actions: list[str] = []
        self.n = 0
        self.people: list[int] = []  # users made with fake store ids, removed afterwards
        self.sealed: list[str] = []  # groups.seal_home calls (the real one is in the Stalwart tests)

    async def seal(self, cg) -> dict[str, int]:
        self.sealed.append(cg.group_name)
        return {"placeholder": 0, "calendars": 0, "books": 0}

    def user(self) -> str:
        self.n += 1
        aid = f"u{self.n}"
        self.accounts[aid] = {"id": aid, "@type": "User", "memberGroupIds": {}}
        return aid

    async def __call__(self, method: str, args: dict[str, Any]) -> dict[str, Any]:
        if method == "x:Account/get":
            return {"list": [dict(self.accounts[i]) for i in args["ids"] if i in self.accounts]}
        if method == "x:Account/set":
            out: dict[str, Any] = {}
            for key, obj in (args.get("create") or {}).items():
                if self.real is not None and obj.get("credentials"):
                    made = await self.real("x:Account/set", {"create": {key: {
                        **obj, "domainId": (await self.real("x:Domain/query", {"filter": {"name": get_settings().stalwart_domain}}))["ids"][0]}}})
                    aid = made["created"][key]["id"]
                else:
                    self.n += 1
                    aid = f"g{self.n}"
                self.accounts[aid] = {"id": aid, **obj, "memberGroupIds": {}}
                out.setdefault("created", {})[key] = {"id": aid}
            for aid, patch in (args.get("update") or {}).items():
                acct = self.accounts.get(aid)
                if acct is None:
                    out.setdefault("notUpdated", {})[aid] = {"type": "notFound"}
                    continue
                for k, v in patch.items():
                    _, _, gid = k.partition("/")
                    if v is None:
                        acct["memberGroupIds"].pop(gid, None)
                    else:
                        acct["memberGroupIds"][gid] = True
                out.setdefault("updated", {})[aid] = None
            for aid in args.get("destroy") or []:
                if self.accounts.pop(aid, None) is not None:
                    out.setdefault("destroyed", []).append(aid)
            return out
        if method == "x:Action/set":
            self.actions += [a["@type"] for a in args["create"].values()]
            return {"created": {k: {"id": "x"} for k in args["create"]}}
        if method == "x:Domain/query":
            return {"ids": ["d1"]}
        raise AssertionError(method)


@pytest_asyncio.fixture
async def store(monkeypatch):
    import mailr.core.users as users_mod

    fake = FakeStore(users_mod._mgmt)
    monkeypatch.setattr(groups, "_mgmt", fake)
    monkeypatch.setattr(groups, "seal_home", fake.seal)
    monkeypatch.setattr(users_mod, "_mgmt", fake)
    yield fake
    async with sessionmaker()() as db:
        await db.execute(delete(GroupCalendar))
        await db.execute(delete(CalendarGroup))
        if fake.people:
            await db.execute(delete(User).where(User.id.in_(fake.people)))
        await builtin.set_instance_enabled(db, False, "everyone")
        await db.commit()


async def _person(store: FakeStore, groups_: list[str], *, role: str = "user", disabled: bool = False) -> User:
    async with sessionmaker()() as db:
        u = User(email=f"g-{uuid.uuid4().hex[:8]}@example.com", name="Person", role=role, settings={},
                 assigned_groups=groups_, disabled=disabled, stalwart_account_id=store.user())
        db.add(u)
        await db.commit()
        store.people.append(u.id)
        return u


async def _calendar(group: str, *, access: str = "write", href: str | None = None, editors=()) -> GroupCalendar:
    async with sessionmaker()() as db:
        gc = GroupCalendar(group_name=group, name="Family", access=access, editors=list(editors),
                           href=href or f"/dav/cal/grp-x-{uuid.uuid4().hex[:6]}%40example.test/fam/")
        db.add(gc)
        await db.commit()
        return gc


async def _on(who: str = "everyone") -> None:
    async with sessionmaker()() as db:
        await builtin.set_instance_enabled(db, True, who)
        await db.commit()


def _groups_of(store: FakeStore, user: User) -> set[str]:
    return set(store.accounts[user.stalwart_account_id]["memberGroupIds"])


# ---------------- the reconcile ----------------


async def test_reconcile_adds_removes_and_is_idempotent(store, app):
    """ACT settings.group_calendars: members of a group with a calendar join its store group; a
    second run changes nothing; leaving, being disabled or the last calendar going removes it."""
    await _on()
    g = f"Fam {uuid.uuid4().hex[:6]}"
    a, b, c = await _person(store, [g]), await _person(store, [g]), await _person(store, ["other"])
    store.accounts[c.stalwart_account_id]["memberGroupIds"]["foreign"] = True  # not mailr's: kept
    assert (await groups.reconcile())["added"] == 0  # no calendar, no store group
    gc = await _calendar(g)

    res = await groups.reconcile()
    async with sessionmaker()() as db:
        cg = await db.get(CalendarGroup, g)
    assert cg.store_group_id in store.accounts and store.accounts[cg.store_group_id]["@type"] == "Group"
    assert store.accounts[cg.store_group_id]["roles"] == {"@type": "Custom", "roleIds": {}}
    assert res["added"] == 2 and _groups_of(store, a) == {cg.store_group_id} == _groups_of(store, b)
    assert _groups_of(store, c) == {"foreign"}
    assert await groups.reconcile() == {"added": 0, "removed": 0, "groups_removed": 0, "failed": 0}
    assert g in store.sealed  # every reconcile keeps the group home tidy (seal_home)

    async with sessionmaker()() as db:
        (await db.get(User, a.id)).assigned_groups = []
        (await db.get(User, b.id)).disabled = True
        await db.commit()
    res = await groups.reconcile()
    assert res["removed"] == 2 and _groups_of(store, a) == set() == _groups_of(store, b)

    async with sessionmaker()() as db:
        await db.delete(await db.get(GroupCalendar, gc.id))
        await db.commit()
    res = await groups.reconcile()
    assert res["groups_removed"] == 1 and cg.store_group_id not in store.accounts
    async with sessionmaker()() as db:
        assert await db.get(CalendarGroup, g) is None
    assert _groups_of(store, c) == {"foreign"}


async def test_reconcile_does_nothing_while_the_instance_is_off(store, app):
    g = f"Fam {uuid.uuid4().hex[:6]}"
    a = await _person(store, [g])
    await _calendar(g)
    assert await groups.reconcile() == {"added": 0, "removed": 0, "groups_removed": 0, "failed": 0}
    assert _groups_of(store, a) == set()


async def test_group_changes_queue_the_reconcile(store, app, user):
    """A sign-in that reports other groups, or an admin assigning groups, queues the reconcile and
    the built-in calendar backfill."""
    from mailr.core.access import record_idp_groups

    await _on()
    async with sessionmaker()() as db:
        await db.execute(delete(Job).where(Job.kind.in_([groups.JOB, "calendar.builtin_backfill"])))
        u = await db.get(User, user.id)
        await record_idp_groups(db, u, ["family"])
        await db.commit()
        n = (await db.execute(select(func.count()).select_from(Job).where(Job.kind == groups.JOB))).scalar_one()
        assert n == 1
        await db.execute(delete(Job).where(Job.kind == groups.JOB))
        await record_idp_groups(db, u, ["family"])  # the same groups: nothing queued
        await db.commit()
        n = (await db.execute(select(func.count()).select_from(Job).where(Job.kind == groups.JOB))).scalar_one()
        assert n == 0


# ---------------- the policy ----------------


async def test_members_get_a_calendar_policy_and_leaving_hides_it(store, app, client, user):
    """With "groups", only admins and members of a group whose policy turns on "Members get a mailr
    calendar" get the built-in calendar. Leaving hides the connector and keeps it."""
    g = f"Fam {uuid.uuid4().hex[:6]}"
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.assigned_groups = [g]  # out of "testers", whose policy turns everything on
        db.add(AccessPolicy(group_name=g, features={"calendar": False}))
        await db.commit()
    await _on("groups")
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        assert not await builtin.builtin_calendar_wanted(db, u)
        adm = User(email=f"a-{uuid.uuid4().hex[:6]}@example.com", name="A", role="admin", settings={},
                   stalwart_account_id="x1")
        assert await builtin.policy_allows(db, adm)
        pol = (await db.execute(select(AccessPolicy).where(AccessPolicy.group_name == g))).scalar_one()
        pol.features = {"calendar": True}
        await db.commit()
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        assert await builtin.builtin_calendar_wanted(db, u)
        conn = Connector(user_id=u.id, kind="caldav", name=builtin.NAME, mode="mirror", config={"account": "x@e"},
                         state={}, enabled=True, builtin=True, status="idle")
        db.add(conn)
        await db.commit()
        cid = conn.id
    assert cid in [a["id"] for a in (await client.get(f"{API}/calendar/accounts")).json()]

    async with sessionmaker()() as db:  # leaves the group
        (await db.get(User, user.id)).assigned_groups = []
        await db.commit()
    assert cid not in [a["id"] for a in (await client.get(f"{API}/calendar/accounts")).json()]
    assert cid not in [a["id"] for a in (await client.get(f"{API}/connectors")).json()]
    async with sessionmaker()() as db:
        conn = await db.get(Connector, cid)
        assert conn is not None and conn.enabled  # kept, data included
        assert not await builtin.active(db, conn)
        await db.delete(conn)
        await db.delete((await db.execute(select(AccessPolicy).where(AccessPolicy.group_name == g))).scalar_one())
        await db.commit()


# ---------------- a member's view ----------------


@contextlib.asynccontextmanager
async def _shared_collection(user: User, group: str, *, access: str = "write", read_only: bool = False):
    path = f"/dav/cal/grp-x-{uuid.uuid4().hex[:6]}%40example.test/fam/"
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.assigned_groups = sorted({*(u.assigned_groups or []), group})
        conn = Connector(user_id=user.id, kind="caldav", name=builtin.NAME, mode="mirror",
                         config={"account": "x@example.test"}, state={}, enabled=True, builtin=True, status="idle")
        db.add(conn)
        await db.flush()
        own = CalCollection(user_id=user.id, connector_id=conn.id, href="http://stalwart:8080/dav/cal/x%40example.test/default/",
                            displayname="Calendar", components=["VEVENT"])
        coll = CalCollection(user_id=user.id, connector_id=conn.id, href="http://stalwart:8080" + path,
                             displayname="Family", components=["VEVENT"], read_only=read_only, color="#2E7D32")
        db.add_all([own, coll])
        gc = GroupCalendar(group_name=group, name="Family", access=access, href=path,
                           editors=[] if access == "write" else [])
        db.add(gc)
        await db.commit()
        ids = (conn.id, coll.id, gc.id)
    try:
        yield ids
    finally:
        async with sessionmaker()() as db:
            await db.delete(await db.get(Connector, ids[0]))
            await db.delete(await db.get(GroupCalendar, ids[2]))
            await db.commit()


async def test_group_calendars_are_marked_and_members_cannot_rename_or_delete(store, app, client, user):
    """ACT calendar.group_shared: the collection carries `shared`; a member hides it, recolours it
    for themselves, and cannot rename or delete it."""
    await _on()
    g = f"Fam {uuid.uuid4().hex[:6]}"
    async with _shared_collection(user, g) as (_cid, coll_id, gc_id):
        cals = (await client.get(f"{API}/calendar/calendars")).json()
        fam = next(c for c in cals if c["calendar_id"] == coll_id)
        assert fam["shared"] == {"kind": "group", "group": g, "access": "write", "editor": False,
                                 "group_calendar_id": gc_id}
        own = next(c for c in cals if c["name"] == "Calendar" and c["connector_id"] == fam["connector_id"])
        assert own["shared"] is None
        r = await client.patch(f"{API}/calendar/collections/{coll_id}", json={"name": "Ours"})
        assert r.status_code == 409 and g in r.json()["detail"]
        r = await client.delete(f"{API}/calendar/collections/{coll_id}", params={"confirm": "Family"})
        assert r.status_code == 409 and "hide" in r.json()["detail"]
        # A colour stays the member's own; nothing goes to the server.
        r = await client.patch(f"{API}/calendar/collections/{coll_id}", json={"color": "#AA0000"})
        assert r.status_code == 200 and r.json()["color"] == "#AA0000" and r.json()["color_local"] is True
        # Hiding works as for any calendar.
        r = await client.patch(f"{API}/calendar/collections/{coll_id}", json={"hidden": True})
        assert r.status_code == 200 and r.json()["hidden"] is True


async def test_listing_leaves_out_stray_calendars_of_managed_accounts(store, app):
    g = f"Fam {uuid.uuid4().hex[:6]}"
    login = f"grp-x-{uuid.uuid4().hex[:6]}@example.test"
    async with sessionmaker()() as db:
        db.add(CalendarGroup(group_name=g, store_group_id="g1", store_group_login=login))
        db.add(GroupCalendar(group_name=g, name="Family", access="write", href=groups.home_of(login) + "fam/"))
        await db.commit()
    base = "http://stalwart:8080"
    infos = [dav.CollectionInfo(href=base + groups.home_of(login) + "default/", displayname=f"Calendar ({login})"),
             dav.CollectionInfo(href=base + groups.home_of(login) + "fam/", displayname="Family"),
             dav.CollectionInfo(href=base + "/dav/cal/me%40example.test/default/", displayname="Calendar")]
    kept = await groups.filter_listing(infos)
    assert [i.displayname for i in kept] == ["Family", "Calendar"]


# ---------------- the admin routes ----------------


@contextlib.asynccontextmanager
async def _admin(app):
    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        adm = await create_user(db, f"a-{uuid.uuid4().hex[:8]}@example.com", "Admin",
                                password="admin-password-123", role="admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(f"{API}/auth/login", json={"email": adm.email, "password": "admin-password-123"})
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        yield c


async def test_admin_routes_checks(store, app, client, user):
    """ACT settings.group_calendars: admins only; built-in calendars must be on; editors only for
    read-only calendars and only members; access is fixed once made; delete needs the name."""
    async with sessionmaker()() as db:
        if not (await db.execute(select(AccessPolicy).where(AccessPolicy.group_name == EVERYONE))).first():
            db.add(AccessPolicy(group_name=EVERYONE))
            await db.commit()
    assert (await client.get(f"{API}/admin/calendars/groups")).status_code == 403
    g = f"Fam {uuid.uuid4().hex[:6]}"
    async with _admin(app) as adm:
        r = await adm.post(f"{API}/admin/calendars/groups", json={"group": g, "name": "Family"})
        assert r.status_code == 400 and "off" in r.json()["detail"]
        await _on()
        r = await adm.post(f"{API}/admin/calendars/groups", json={"group": g, "name": "School", "access": "read",
                                                                  "editors": [user.id]})
        assert r.status_code == 400 and "members" in r.json()["detail"]
        r = await adm.post(f"{API}/admin/calendars/groups", json={"group": g, "name": "X", "color": "red"})
        assert r.status_code == 422
        gc = await _calendar(g, access="read")
        r = await adm.patch(f"{API}/admin/calendars/groups/{gc.id}", json={"access": "write"})
        assert r.status_code == 400 and "fixed" in r.json()["detail"]
        r = await adm.delete(f"{API}/admin/calendars/groups/{gc.id}", params={"confirm": "wrong"})
        assert r.status_code == 400
        listed = (await adm.get(f"{API}/admin/calendars/groups")).json()
        assert listed["enabled"] is True
        assert EVERYONE not in {x["name"] for x in listed["groups"]}  # the Everyone policy is no group
        mine = next(x for x in listed["groups"] if x["name"] == g)
        assert [c["name"] for c in mine["calendars"]] == ["Family"] and mine["calendars"][0]["access"] == "read"
        # The admin sees group calendars only: no personal calendar of any user is listed.
        assert all(set(c) <= {"id", "group", "name", "color", "access", "editors", "ready", "items"}
                   for x in listed["groups"] for c in x["calendars"])
        r = await adm.put(f"{API}/admin/calendars", json={"builtin": True, "who": "groups"})
        assert r.status_code == 200 and r.json()["who"] == "groups"
        r = await adm.put(f"{API}/admin/calendars", json={"builtin": True, "who": "nobody"})
        assert r.status_code == 422


# ---------------- writing ----------------


async def test_read_only_message_names_the_calendar():
    coll = CalCollection(displayname="School", read_only=True)
    with pytest.raises(ValueError, match="“School” is read-only for you"):
        await writer.create(None, 0, coll, None, "uid")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="read-only for you"):
        writer._refused(dav.DavError("PUT x: forbidden", 403), coll)
    writer._refused(dav.DavError("PUT x: HTTP 500", 500), coll)  # anything else is not about rights


# ---------------- free/busy only (Stalwart 1.x) ----------------


def test_the_group_ace_grants_read_free_busy_alone():
    """A "Free/busy only" calendar's group ACE is CalDAV read-free-busy (the caldav namespace), a
    "Can view" one DAV read."""
    assert groups.group_privileges("freebusy") == ["read-free-busy"]
    assert groups.group_privileges("read") == ["read"]
    ace = groups._ace("/dav/pal/grp/", groups.group_privileges("freebusy"))
    assert "<c:read-free-busy/>" in ace and "<d:read/>" not in ace
    assert "<d:read/><" in groups._ace("/dav/pal/a/", ["read", "write"])


async def test_free_busy_only_is_refused_on_stalwart_0_16(store, app, client, user, monkeypatch):
    """ACT settings.group_calendars: the level needs Stalwart 1.x. On 0.16 the admin list does not
    offer it, a new calendar at that level and a switch to it are refused with a plain message,
    and nothing is written."""
    from mailr.calendar import sharing

    async with sessionmaker()() as db:
        if not (await db.execute(select(AccessPolicy).where(AccessPolicy.group_name == EVERYONE))).first():
            db.add(AccessPolicy(group_name=EVERYONE))
            await db.commit()
    await _on()
    g = f"Fam {uuid.uuid4().hex[:6]}"
    acls: list[str] = []

    async def no_acl(db, cg, gc):
        acls.append(gc.access)

    monkeypatch.setattr(groups, "_write_acl", no_acl)
    async with _admin(app) as adm:
        monkeypatch.setattr(sharing, "server_v1", _const(False))
        assert (await adm.get(f"{API}/admin/calendars/groups")).json()["access"] == ["write", "read"]
        r = await adm.post(f"{API}/admin/calendars/groups", json={"group": g, "name": "Rota", "access": "freebusy"})
        assert r.status_code == 400 and "free/busy only" in r.json()["detail"] and "1.x" in r.json()["detail"], r.text
        gc = await _calendar(g, access="read")
        r = await adm.patch(f"{API}/admin/calendars/groups/{gc.id}", json={"access": "freebusy"})
        assert r.status_code == 400 and "free/busy only" in r.json()["detail"], r.text
        async with sessionmaker()() as db:
            assert (await db.get(GroupCalendar, gc.id)).access == "read"
            assert not (await db.execute(select(GroupCalendar).where(GroupCalendar.name == "Rota"))).first()
        assert acls == []
        r = await adm.post(f"{API}/admin/calendars/groups", json={"group": g, "name": "X", "access": "busy"})
        assert r.status_code == 422

        # Stalwart 1.x: offered; a Can view calendar switches to free/busy only and back (the ACL is
        # written again each time); Can edit stays fixed either way.
        monkeypatch.setattr(sharing, "server_v1", _const(True))
        assert (await adm.get(f"{API}/admin/calendars/groups")).json()["access"] == ["write", "read", "freebusy"]
        async with sessionmaker()() as db:
            db.add(CalendarGroup(group_name=g, store_group_id="g1", store_group_login="grp@example.test",
                                 owner_id="o1", owner_login="grpcal@example.test"))
            await db.commit()
        r = await adm.patch(f"{API}/admin/calendars/groups/{gc.id}", json={"access": "freebusy"})
        assert r.status_code == 200 and r.json()["access"] == "freebusy", r.text
        r = await adm.patch(f"{API}/admin/calendars/groups/{gc.id}", json={"access": "read"})
        assert r.status_code == 200 and r.json()["access"] == "read", r.text
        assert acls == ["freebusy", "read"]
        r = await adm.patch(f"{API}/admin/calendars/groups/{gc.id}", json={"access": "write"})
        assert r.status_code == 400 and "fixed" in r.json()["detail"]
        wgc = await _calendar(g, access="write")
        r = await adm.patch(f"{API}/admin/calendars/groups/{wgc.id}", json={"access": "freebusy"})
        assert r.status_code == 400 and "fixed" in r.json()["detail"]


def _const(v):
    async def f():
        return v
    return f


async def test_members_get_busy_blocks_unless_the_calendar_is_listed(store, app, client, user, monkeypatch):
    """ACT calendar.group_shared: during a member's sync, a free/busy-only group calendar that the
    listing leaves out is asked as a free-busy-query and stored as busy blocks; a listed one (an
    editor's, or through an own grant: Stalwart 1.x takes the union) is not asked and syncs in full;
    a calendar of another group, a "Can view" one and a refusal give nothing. The member's list marks
    the blocks' calendar access "freebusy"."""
    from mailr.calendar import shared_busy, sharing

    await _on()
    g = f"Fam {uuid.uuid4().hex[:6]}"
    asked: list[str] = []
    refused: set[str] = set()

    async def query(client_, href, start, end):
        asked.append(groups.path_of(href))
        if groups.path_of(href) in refused:
            raise shared_busy.Refused("403")
        return [shared_busy.Period(start + timedelta(days=70), start + timedelta(days=70, hours=1))]

    monkeypatch.setattr(shared_busy, "query", query)
    async with _shared_collection(user, g, access="freebusy", read_only=True) as (cid, coll_id, gc_id):
        other = await _calendar(f"Other {uuid.uuid4().hex[:6]}", access="freebusy")
        view = await _calendar(g, access="read")
        async with sessionmaker()() as db:
            gc = await db.get(GroupCalendar, gc_id)
            path = gc.href
        out = sharing.Listing({}, None)
        await sharing._group_free_busy_only(None, user.id, set(), {}, out)
        assert asked == [path]
        assert [(groups.path_of(i.href), i.displayname, i.read_only) for i, _ in out.busy] == [(path, "Family", True)]
        assert out.shared_in[path]["group_calendar_id"] == gc_id and out.shared_in[path]["level"] == "freebusy"
        assert other.href not in asked and view.href not in asked

        # Listed for this member (an editor, or an own grant): not asked.
        asked.clear()
        out2 = sharing.Listing({}, None)
        await sharing._group_free_busy_only(None, user.id, {path}, {}, out2)
        assert asked == [] and out2.busy == [] and out2.shared_in == {}

        # Refused (not yet in the store group): left out.
        refused.add(path)
        out3 = sharing.Listing({}, None)
        await sharing._group_free_busy_only(None, user.id, set(), {}, out3)
        assert out3.busy == [] and out3.shared_in == {}

        # The list: "freebusy" while the last sync stored blocks, else what the listing gave.
        async with sessionmaker()() as db:
            conn = await db.get(Connector, cid)
            conn.state = {"caldav": {"shared_in": out.shared_in}}
            await db.commit()
        cals = (await client.get(f"{API}/calendar/calendars")).json()
        fam = next(c for c in cals if c["calendar_id"] == coll_id)
        assert fam["shared"] == {"kind": "group", "group": g, "access": "freebusy", "editor": False,
                                 "group_calendar_id": gc_id}
        async with sessionmaker()() as db:
            conn = await db.get(Connector, cid)
            conn.state = {"caldav": {"shared_in": {}}}
            await db.commit()
        fam = next(c for c in (await client.get(f"{API}/calendar/calendars")).json() if c["calendar_id"] == coll_id)
        assert fam["shared"]["access"] == "read"
        # Household free/busy leaves the blocks out, as for a person's free/busy-only calendar.
        from mailr.calendar import household

        async with sessionmaker()() as db:
            conn = await db.get(Connector, cid)
            conn.state = {"caldav": {"shared_in": out.shared_in}}
            await db.commit()
            assert str(coll_id) not in await household.calendars_of(db, user.id)
