# ACT-SURFACES: api
"""Phones and desktop apps on the built-in calendars (calendar/phones.py, calendar/phone_check.py),
without a real Stalwart: the instance switch and its gating, the app password lifecycle, the
permission set, pausing when phone access is off or a user is disabled, the order of the steps
when a user is deleted, the iOS profile and the admin's phone access check.

The store's management API is a fake (FakeStore). The same steps against a real Stalwart, behind
the documented proxy, are in tests/test_phone_access_stalwart.py (opt-in).

Actions covered: settings.phone_access, settings.phone_passwords, settings.phone_access_check.
"""

from __future__ import annotations

import base64
import contextlib
import plistlib
import uuid
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from mailr.calendar import builtin, phone_check, phones
from mailr.core import egress, netguard
from mailr.core import store_groupware as sg
from mailr.db import sessionmaker
from mailr.models import InstanceSetting, User

API = "/api/v1"
URL = "https://dav.northwind.example"


class FakeStore:
    """x:AppPassword/get|set and x:Account/set (the password secret) of a few accounts."""

    def __init__(self, real=None):
        self.real = real  # every other call goes to the dev store (accounts for new users)
        self.creds: dict[str, dict[str, dict[str, Any]]] = {}
        self.secrets: dict[str, str] = {}
        self.n = 0
        self.calls: list[str] = []

    async def mgmt(self, method: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(method)
        acct = args.get("accountId")
        if method == "x:AppPassword/get":
            items = self.creds.get(acct, {})
            return {"list": [{"id": k, **v} for k, v in items.items()]}
        if method == "x:AppPassword/set":
            items = self.creds.setdefault(acct, {})
            out: dict[str, Any] = {}
            for cid, obj in (args.get("create") or {}).items():
                self.n += 1
                pid = f"p{self.n}"
                items[pid] = {"description": obj["description"], "permissions": obj["permissions"],
                              "createdAt": f"2026-10-08T10:00:{self.n:02d}Z", "expiresAt": None}
                out.setdefault("created", {})[cid] = {"id": pid, "secret": f"app_secret{self.n}"}
            for pid, patch in (args.get("update") or {}).items():
                items[pid].update(patch)
                out.setdefault("updated", {})[pid] = None
            for pid in args.get("destroy") or []:
                if items.pop(pid, None) is not None:
                    out.setdefault("destroyed", []).append(pid)
            return out
        updates = args.get("update") or {}
        if method == "x:Account/set" and updates and all(set(p) == {"credentials/0/secret"} for p in updates.values()):
            for aid, patch in updates.items():
                self.secrets[aid] = patch["credentials/0/secret"]
            return {"updated": {aid: None for aid in updates}}
        if method.startswith(("x:Http/", "x:AddressBook/", "x:Action/")):
            raise AssertionError(f"the shared dev store's settings must not change: {method}")
        assert self.real is not None, method
        return await self.real(method, args)

    async def call(self, calls, using=None):
        out = []
        for method, args, tag in calls:
            out.append([method, await self.mgmt(method, args), tag])
        return out


@pytest.fixture
def store(monkeypatch):
    import mailr.core.users as users_mod

    fake = FakeStore(users_mod._mgmt)
    monkeypatch.setattr(users_mod, "_mgmt", fake.mgmt)
    monkeypatch.setattr(phones, "_client", lambda: fake)
    fake.applied = []

    async def apply():  # the shared dev store's settings never change in these tests
        async with sessionmaker()() as db:
            fake.applied.append(await phones.instance_enabled(db))
        return []

    monkeypatch.setattr(phones, "apply_store_settings", apply)
    return fake


async def _setting(key: str, value: Any) -> None:
    async with sessionmaker()() as db:
        row = await db.get(InstanceSetting, key)
        if value is None:
            if row is not None:
                await db.delete(row)
        elif row is None:
            db.add(InstanceSetting(key=key, value=value))
        else:
            row.value = value
        await db.commit()


@pytest_asyncio.fixture
async def reset():
    yield
    for key in (phones.SETTING, phones.URL_SETTING, builtin.SETTING):
        await _setting(key, None)


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


async def _turn_on(app) -> None:
    async with _admin(app) as adm:
        r = await adm.put(f"{API}/admin/calendars", json={"builtin": True, "phone_url": URL + "/", "phone_access": True})
        assert r.status_code == 200, r.text
        assert r.json()["phone_access"] is True and r.json()["phone_url"] == URL


async def _account(user_id: int) -> str:
    async with sessionmaker()() as db:
        return (await db.get(User, user_id)).stalwart_account_id


def test_the_permission_set_holds_dav_only():
    """ACT settings.phone_passwords: no mail protocol, no sharing, no schedule outbox, no directory."""
    perms = set(phones.DAV_PERMISSIONS)
    assert {"authenticate", "authenticateWithAlias", "davCalPut", "davCardPut", "davCalPropFind",
            "davCardMultiGet", "davSyncCollection", "davExpandProperty", "davCalMkCol"} <= perms
    assert not perms & set(phones.EXCLUDED)
    for p in perms:
        assert p.startswith(("dav", "authenticate")), p
        assert not p.startswith(("davFile", "davPrincipal")), p
    assert "davCalAcl" in phones.EXCLUDED and "davCalFreeBusyQuery" in phones.EXCLUDED


def test_the_public_address_is_a_host_only():
    assert phones.normalize_url("HTTPS://Dav.Northwind.Example/") == URL
    assert phones.normalize_url("https://dav.northwind.example:8443") == URL + ":8443"
    for bad in ("http://dav.northwind.example", "https://dav.northwind.example/dav/", "https://u:p@x.example",
                "dav.northwind.example", "https:///"):
        with pytest.raises(phones.PhoneAccessError):
            phones.normalize_url(bad)


def test_the_profile_holds_both_accounts():
    raw = phones.mobileconfig(url=URL + ":8443", username="sam@northwind.example", password="app_x", device="iPhone")
    prof = plistlib.loads(raw)
    assert prof["PayloadType"] == "Configuration" and len(prof["PayloadContent"]) == 2
    cal, card = prof["PayloadContent"]
    assert cal["PayloadType"] == "com.apple.caldav.account" and card["PayloadType"] == "com.apple.carddav.account"
    assert cal["CalDAVHostName"] == "dav.northwind.example" and cal["CalDAVPort"] == 8443 and cal["CalDAVUseSSL"]
    assert cal["CalDAVUsername"] == card["CardDAVUsername"] == "sam@northwind.example"
    assert cal["CalDAVPassword"] == card["CardDAVPassword"] == "app_x"
    assert "nobody is invited" in prof["PayloadDescription"]


async def test_off_by_default_and_gated(app, client, user, store, reset):
    """ACT settings.phone_access: off by default; it needs built-in calendars and an address; a user
    cannot make a password while it is off, and only admins change it."""
    r = (await client.get(f"{API}/me/dav")).json()
    assert r["available"] is False and r["instance"] is False and "off" in r["reason"]
    r = await client.post(f"{API}/me/dav/app-passwords", json={"name": "iPhone"})
    assert r.status_code == 409 and "off" in r.json()["detail"]
    assert store.creds == {}
    assert (await client.put(f"{API}/admin/calendars", json={"builtin": True, "phone_access": True})).status_code == 403
    async with _admin(app) as adm:
        got = (await adm.get(f"{API}/admin/calendars")).json()
        assert got["phone_access"] is False and got["phone_url"] == ""
        r = await adm.put(f"{API}/admin/calendars", json={"builtin": False, "phone_access": True, "phone_url": URL})
        assert r.status_code == 409 and "built-in calendars first" in r.json()["detail"]
        r = await adm.put(f"{API}/admin/calendars", json={"builtin": True, "phone_access": True, "phone_url": ""})
        assert r.status_code == 409 and "address" in r.json()["detail"]
        r = await adm.put(f"{API}/admin/calendars", json={"builtin": True, "phone_url": "http://dav.northwind.example"})
        assert r.status_code == 400 and "https" in r.json()["detail"]


async def test_password_lifecycle(app, client, user, store, reset):
    """ACT settings.phone_passwords: make one per device (shown once, with the profile), list without
    the secret, delete; an API token can do none of it."""
    await _turn_on(app)
    info = (await client.get(f"{API}/me/dav")).json()
    assert info["available"] and info["server"] == "dav.northwind.example" and info["passwords"] == []
    assert info["username"] == user.stalwart_login and "nobody is invited" in info["scheduling_note"]
    r = await client.post(f"{API}/me/dav/app-passwords", json={"name": "  Sam's\n iPhone "})
    assert r.status_code == 200, r.text
    made = r.json()
    assert made["password"].startswith("app_") and made["name"] == "Sam's iPhone"
    assert made["profile_name"] == "mailr-sam-s-iphone.mobileconfig"
    prof = plistlib.loads(base64.b64decode(made["profile"]))
    assert prof["PayloadContent"][0]["CalDAVPassword"] == made["password"]
    acct = await _account(user.id)
    stored = store.creds[acct][made["id"]]
    assert stored["description"] == "mailr: Sam's iPhone"
    assert stored["permissions"] == {"@type": "Replace", "permissions": {p: True for p in phones.DAV_PERMISSIONS}}
    listed = (await client.get(f"{API}/me/dav")).json()["passwords"]
    assert listed == [{"id": made["id"], "name": "Sam's iPhone", "created_at": stored["createdAt"], "paused": False}]
    assert "password" not in listed[0]
    # Another credential on the account (not made by mailr) is neither listed nor deletable.
    store.creds[acct]["other"] = {"description": "admin key", "expiresAt": None}
    assert len((await client.get(f"{API}/me/dav")).json()["passwords"]) == 1
    assert (await client.delete(f"{API}/me/dav/app-passwords/other")).status_code == 404
    assert (await client.post(f"{API}/me/dav/app-passwords", json={"name": "  "})).status_code == 400
    r = await client.post(f"{API}/tokens", json={"name": "t", "scopes": ["read", "calendar"]})
    tok = r.json()["token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {tok}"}) as t:
        assert (await t.get(f"{API}/me/dav")).status_code == 403
        assert (await t.post(f"{API}/me/dav/app-passwords", json={"name": "x"})).status_code == 403
        assert (await t.delete(f"{API}/me/dav/app-passwords/{made['id']}")).status_code == 403
    assert (await client.delete(f"{API}/me/dav/app-passwords/{made['id']}")).status_code == 200
    assert made["id"] not in store.creds[acct]
    assert (await client.delete(f"{API}/me/dav/app-passwords/{made['id']}")).status_code == 404


async def test_a_user_may_have_at_most_twenty(app, client, user, store, reset, monkeypatch):
    await _turn_on(app)
    monkeypatch.setattr(phones, "MAX_PER_USER", 2)
    for name in ("a", "b"):
        assert (await client.post(f"{API}/me/dav/app-passwords", json={"name": name})).status_code == 200
    r = await client.post(f"{API}/me/dav/app-passwords", json={"name": "c"})
    assert r.status_code == 409 and "Delete one" in r.json()["detail"]


async def test_off_pauses_every_password_and_on_resumes(app, client, user, store, reset):
    """ACT settings.phone_access: turning it off pauses the passwords (they stop working on the
    store), turning it on again resumes them. The reconcile writes only differences."""
    await _turn_on(app)
    made = (await client.post(f"{API}/me/dav/app-passwords", json={"name": "iPad"})).json()
    acct = await _account(user.id)
    async with _admin(app) as adm:
        r = await adm.put(f"{API}/admin/calendars", json={"builtin": True, "phone_access": False})
        assert r.status_code == 200 and r.json()["phone_access"] is False
    out = await phones.reconcile()
    assert out["paused"] >= 1 and store.creds[acct][made["id"]]["expiresAt"] == phones.PAUSED
    listed = (await client.get(f"{API}/me/dav")).json()
    assert listed["available"] is False and listed["passwords"][0]["paused"] is True
    before = len([c for c in store.calls if c == "x:AppPassword/set"])
    assert (await phones.reconcile())["paused"] == 0  # nothing to change the second time
    assert len([c for c in store.calls if c == "x:AppPassword/set"]) == before
    await _turn_on(app)
    out = await phones.reconcile()
    assert out["resumed"] >= 1 and store.creds[acct][made["id"]]["expiresAt"] is None
    # Built-in calendars off: phone access goes off with them.
    async with _admin(app) as adm:
        r = await adm.put(f"{API}/admin/calendars", json={"builtin": False})
        assert r.json()["phone_access"] is False
    await phones.reconcile()
    assert store.creds[acct][made["id"]]["expiresAt"] == phones.PAUSED


async def test_disabling_a_user_pauses_and_locks_and_enabling_restores(app, client, user, store, reset):
    """ACT settings.phone_passwords: a disabled user's app passwords pause and their store password
    is replaced (IMAP and DAV stop); enabling brings the same password and the app passwords back."""
    from mailr.core.crypto import decrypt_for_user

    await _turn_on(app)
    made = (await client.post(f"{API}/me/dav/app-passwords", json={"name": "Phone"})).json()
    acct = await _account(user.id)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        original = decrypt_for_user(u.id, u.data_key_wrapped, u.stalwart_secret_enc)
    async with _admin(app) as adm:
        assert (await adm.patch(f"{API}/admin/users/{user.id}", json={"disabled": True})).status_code == 200
        assert store.creds[acct][made["id"]]["expiresAt"] == phones.PAUSED
        assert store.secrets[acct] != original
        async with sessionmaker()() as db:
            assert (await db.get(User, user.id)).store_signin_locked is True
        assert (await adm.patch(f"{API}/admin/users/{user.id}", json={"disabled": False})).status_code == 200
    assert store.secrets[acct] == original
    assert store.creds[acct][made["id"]]["expiresAt"] is None
    async with sessionmaker()() as db:
        assert (await db.get(User, user.id)).store_signin_locked is False


async def test_deleting_a_user_revokes_first_then_shares_then_the_account(app, user, store, reset, monkeypatch):
    """ACT settings.phone_passwords: the app passwords go first, then sharing.user_removed, then the
    store account."""
    from mailr.api import admin_routes
    from mailr.calendar import sharing

    await _turn_on(app)
    acct = await _account(user.id)
    store.creds[acct] = {"p1": {"description": "mailr: Phone", "expiresAt": None}}
    order: list[str] = []
    real_revoke = phones.revoke_all

    async def revoke_all(a):
        order.append("revoke")
        return await real_revoke(a)

    async def user_removed(db, u):
        order.append("shares")

    async def delete_store_account(u):
        order.append("store")

    monkeypatch.setattr(phones, "revoke_all", revoke_all)
    monkeypatch.setattr(sharing, "user_removed", user_removed)
    monkeypatch.setattr(admin_routes, "delete_store_account", delete_store_account)
    async with _admin(app) as adm:
        r = await adm.delete(f"{API}/admin/users/{user.id}", params={"confirm": user.email})
        assert r.status_code == 200, r.text
    assert order == ["revoke", "shares", "store"]
    assert store.creds[acct] == {}


async def test_store_settings_follow_the_switch():
    """ACT settings.phone_access: on → useXForwarded on and no automatic address books; off → back."""
    class Store:
        def __init__(self):
            self.s = {"x:Http": {"useXForwarded": False}, "x:AddressBook": {"defaultHrefName": "default"}}
            self.writes = 0

        async def call(self, calls, using=None):
            out = []
            for method, args, tag in calls:
                obj, verb = method.split("/")
                if verb == "get":
                    out.append([method, {"list": [dict(self.s[obj])]}, tag])
                elif obj == "x:Action":
                    out.append([method, {"created": {"a": {}}}, tag])
                else:
                    self.s[obj].update(args["update"]["singleton"])
                    self.writes += 1
                    out.append([method, {"updated": {"singleton": None}}, tag])
            return out

    st = Store()
    assert set(await sg.ensure_phone_settings(True, st)) == {"x:Http.useXForwarded", "x:AddressBook.defaultHrefName"}
    assert st.s == {"x:Http": {"useXForwarded": True}, "x:AddressBook": {"defaultHrefName": None}}
    assert await sg.ensure_phone_settings(True, st) == [] and st.writes == 2
    await sg.ensure_phone_settings(False, st)
    assert st.s == {"x:Http": {"useXForwarded": False}, "x:AddressBook": {"defaultHrefName": "default"}}


# ---------------- the admin's phone access check ----------------


def _resp(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, headers=headers or {})


GOOD = {
    "wk-caldav": _resp(301, {"location": "/dav/cal/"}),
    "wk-carddav": _resp(301, {"location": URL + "/dav/card/"}),
    "dav": _resp(401, {"www-authenticate": 'Basic realm="mailr calendars"'}),
    "acl": _resp(405), "outbox": _resp(403), "file": _resp(403),
    **{p: _resp(404, {"content-type": "text/html"}) for p in phone_check.BLOCKED_PATHS},
}


def test_check_verdicts():
    """ACT settings.phone_access_check: a good gateway passes every line; Stalwart reached directly
    fails the restriction lines."""
    assert all(c["ok"] for c in phone_check.evaluate(URL, GOOD))
    direct = {**GOOD, "acl": _resp(401, {"www-authenticate": 'Basic realm="Stalwart Server"'}),
              "/jmap/session": _resp(200, {"content-type": "application/json"}),
              "wk-caldav": _resp(307, {"location": "https://elsewhere.example/dav/cal"}),
              "dav": _resp(404)}
    bad = {c["id"]: c for c in phone_check.evaluate(URL, direct)}
    assert not bad["acl_blocked"]["ok"] and "reached the mail server" in bad["acl_blocked"]["detail"]
    assert not bad["other_paths_blocked"]["ok"] and "/jmap/session" in bad["other_paths_blocked"]["detail"]
    assert not bad["wellknown_caldav"]["ok"] and "another host" in bad["wellknown_caldav"]["detail"]
    assert not bad["dav_auth"]["ok"] and bad["outbox_blocked"]["ok"]


async def test_check_obeys_the_egress_rules(app, user, reset, monkeypatch):
    """ACT settings.phone_access_check: when the egress rules forbid the fetch the check does not
    run and says why, with commands to run from outside; otherwise it runs."""
    await _turn_on(app)

    async def no_egress(_):
        raise egress.EgressUnavailable("unconfigured", "Remote content is paused: no VPN egress proxy is configured.")

    monkeypatch.setattr(egress, "gate", no_egress)
    async with _admin(app) as adm:
        r = (await adm.post(f"{API}/admin/calendars/phone-check")).json()
        assert r["ran"] is False and "VPN egress" in r["reason"] and any("PROPFIND" in c for c in r["commands"])

    async def direct(_):
        return ""

    monkeypatch.setattr(egress, "gate", direct)

    async def private(url, proxy):
        raise netguard.UnsafeTarget("dav.northwind.example resolves to a private address")

    monkeypatch.setattr(phone_check, "_precheck", private)
    async with _admin(app) as adm:
        r = (await adm.post(f"{API}/admin/calendars/phone-check")).json()
        assert r["ran"] is False and "inside your network" in r["reason"]

    async def fine(url, proxy):
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        path, m = request.url.path, request.method
        if path == "/.well-known/caldav":
            return httpx.Response(301, headers={"location": "/dav/cal/"})
        if path == "/.well-known/carddav":
            return httpx.Response(301, headers={"location": "/dav/card/"})
        if m == "ACL":
            return httpx.Response(405)
        if path.startswith(("/dav/itip/", "/dav/file/")):
            return httpx.Response(403)
        if path.startswith("/dav/"):
            return httpx.Response(401, headers={"www-authenticate": 'Basic realm="mailr calendars"'})
        return httpx.Response(404)

    monkeypatch.setattr(phone_check, "_precheck", fine)
    monkeypatch.setattr(phone_check, "_client", lambda proxy: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    async with _admin(app) as adm:
        r = (await adm.post(f"{API}/admin/calendars/phone-check")).json()
        assert r["ran"] is True and r["ok"] is True and len(r["checks"]) == 7, r
        assert r["via"] == "directly from the server"
