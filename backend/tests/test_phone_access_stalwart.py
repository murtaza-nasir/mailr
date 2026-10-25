# ACT-SURFACES: api
"""Phone access end to end against a real Stalwart.

The module runs against the suite's own store server, the dev Stalwart of
deploy/dev/docker-compose.yml, unless MAILR_TEST_STALWART_URL and MAILR_TEST_STALWART_ADMIN name
another throwaway Stalwart (tests/dev_services.py). It is never pointed at production. MAILR_TEST_WITHOUT_STALWART_DAV=1 skips it.
On the dev Stalwart two more are set by default:

    MAILR_TEST_STALWART_IMAPS=127.0.0.1:11993         # its port 993: IMAP refuses the app password
    MAILR_TEST_STALWART_CONTAINER=mailr-dev-stalwart-1  # its container: the gateway test runs
                                                      # deploy/dav-gateway in front of it (docker)

For the length of each test mailr's store settings point at that server. It changes the server's
settings (useXForwarded, automatic address books). Every other test process's start (bootstrap)
sets them back to its own database's phone-access switch, so a test that depends on them checks
afterwards that they held, and runs again when another process changed them meanwhile
(`_holding`).

Covered: a DAV app password made through mailr's API works for discovery (with the user's own
address), PROPFIND, PUT and CardDAV; IMAP, JMAP mail, the ACL method and the schedule outbox refuse
it; deleting it gives 401; a disabled user's app password and store password give 401 and come
back when enabled; phone access off pauses it; the store settings follow the switch; a group home
shows a member nothing writable but the group's calendars; mailr makes the user's own address book;
the documented gateway restricts what it should and passes the phone's address.
Actions covered: settings.phone_access, settings.phone_passwords.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import ssl
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from dev_services import stalwart_dav_skip_reason
from httpx import ASGITransport, AsyncClient

from mailr.calendar import builtin, phones

URL = os.environ.get("MAILR_TEST_STALWART_URL", "").rstrip("/")
ADMIN = os.environ.get("MAILR_TEST_STALWART_ADMIN", "")
IMAPS = os.environ.get("MAILR_TEST_STALWART_IMAPS", "")
CONTAINER = os.environ.get("MAILR_TEST_STALWART_CONTAINER", "")
pytestmark = [
    pytest.mark.skipif(not (URL and ":" in ADMIN), reason=stalwart_dav_skip_reason()),
    pytest.mark.serial,  # points the process's store settings at another server
]
API = "/api/v1"
PUBLIC = "https://dav.example.com"
TEMPLATE = Path(__file__).resolve().parents[2] / "deploy" / "dav-gateway" / "default.conf.template"
GATEWAY_IMAGE = "nginxinc/nginx-unprivileged:1.29-alpine"
PROPFIND = b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>'


def _event(uid: str) -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//mailr test//EN\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:20261008T120000Z\r\nDTSTART:20261012T140000Z\r\nDTEND:20261012T150000Z\r\n"
            "SUMMARY:From a phone\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


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


async def _switch(on: bool) -> None:
    from mailr.db import sessionmaker

    async with sessionmaker()() as db:
        if on:
            await builtin.set_instance_enabled(db, True)
            await phones.set_instance(db, None, PUBLIC)
        await phones.set_instance(db, on, None)
        await db.commit()
    await phones.apply_store_settings()


async def _login(app, user) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await c.__aenter__()
    r = await c.post(f"{API}/auth/login", json={"email": user.email, "password": "test-password-123"})
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c


async def _home(base: str, auth: tuple[str, str]) -> str:
    """Discovery as a phone does it: current-user-principal, then calendar-home-set."""
    async with httpx.AsyncClient(base_url=base, auth=auth) as h:
        r = await h.request("PROPFIND", "/dav/cal/", headers={"Depth": "0"}, content=PROPFIND)
        assert r.status_code == 207, r.text
        principal = re.search(r"<D:current-user-principal><D:href>([^<]+)</D:href>", r.text).group(1)
        r = await h.request("PROPFIND", principal, headers={"Depth": "0"}, content=(
            b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            b'<d:prop><c:calendar-home-set/></d:prop></d:propfind>'))
        assert r.status_code == 207, r.text
        return re.search(r"calendar-home-set><D:href>([^<]+)</D:href>", r.text).group(1)


async def _put(base: str, auth: tuple[str, str], href: str) -> int:
    uid = uuid.uuid4().hex
    async with httpx.AsyncClient(base_url=base, auth=auth) as h:
        r = await h.put(href + f"{uid}.ics", content=_event(uid), headers={"Content-Type": "text/calendar",
                                                                          "If-None-Match": "*"})
        return r.status_code


@pytest_asyncio.fixture
async def setup(store):
    from mailr.core.users import bootstrap, create_user, delete_store_account
    from mailr.db import sessionmaker
    from mailr.main import create_app
    from mailr.models import InstanceSetting

    await bootstrap()
    app = create_app()
    await _switch(True)
    made = []

    async def new_user(role: str = "user", groups: list[str] | None = None):
        async with sessionmaker()() as db:
            u = await create_user(db, f"p-{uuid.uuid4().hex[:8]}@example.com", "Robin Example",
                                  password="test-password-123", role=role)
            if groups:
                u.assigned_groups = groups
                await db.commit()
        made.append(u)
        return u

    user = await new_user()
    c = await _login(app, user)
    yield app, user, c, new_user
    await c.aclose()
    async with sessionmaker()() as db:
        for key in (phones.SETTING, phones.URL_SETTING):
            row = await db.get(InstanceSetting, key)
            if row is not None:
                await db.delete(row)
        await builtin.set_instance_enabled(db, False)
        await db.commit()
    await phones.apply_store_settings()
    for u in made:
        await delete_store_account(u)


async def _phone_settings() -> tuple:
    """(useXForwarded, the automatic address book's name) as the server has them now."""
    from mailr.core.store import service_client

    got = await service_client().call([["x:Http/get", {"ids": ["singleton"]}, "h"],
                                       ["x:AddressBook/get", {"ids": ["singleton"]}, "a"]],
                                      using=["urn:ietf:params:jmap:core", "urn:stalwart:jmap"])
    return got[0][1]["list"][0]["useXForwarded"], got[1][1]["list"][0]["defaultHrefName"]


async def _holding(attempt, tries: int = 3) -> None:
    """Run `attempt` with phone access on. On a shared Stalwart (the dev one), any other test
    process or dev server that starts meanwhile sets the server's phone settings back to its own
    switch (bootstrap → phones.apply_store_settings). A failure while the settings did not hold is
    run again; a failure while they held is the test's own."""
    for n in range(tries):
        await _switch(True)
        try:
            await attempt(n)
        except AssertionError:
            if n + 1 < tries and await _phone_settings() != (True, None):
                continue
            raise
        return


async def _make(c: AsyncClient, name: str = "Test phone") -> dict:
    r = await c.post(f"{API}/me/dav/app-passwords", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


async def _propfind(base: str, auth: tuple[str, str], path: str = "/dav/cal/", headers: dict | None = None) -> int:
    async with httpx.AsyncClient() as h:
        r = await h.request("PROPFIND", base + path, auth=auth, headers={"Depth": "0", **(headers or {})},
                            content=PROPFIND)
        return r.status_code


def _imap(user: str, secret: str) -> bool:
    import imaplib

    host, port = IMAPS.rsplit(":", 1)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        c = imaplib.IMAP4_SSL(host, int(port), ssl_context=ctx)
        c.login(user, secret)
        c.logout()
        return True
    except (imaplib.IMAP4.error, OSError):
        return False


async def test_a_dav_password_opens_dav_only(setup):
    """ACT settings.phone_passwords: discovery with the user's own address, PROPFIND, PUT and CardDAV
    work; IMAP, JMAP mail, ACL and the schedule outbox refuse it; deleting it gives 401."""
    app, user, c, _ = setup
    info = (await c.get(f"{API}/me/dav")).json()
    assert info["available"] and info["username"] == user.email  # the alias, not the internal name
    made = await _make(c)
    auth = (user.email, made["password"])
    home = await _home(URL, auth)
    assert home.startswith("/dav/cal/") and user.email.replace("@", "%40") not in home  # the internal account's home
    assert await _put(URL, auth, home + "default/") == 201
    assert await _propfind(URL, auth, "/dav/card/") == 207
    async with httpx.AsyncClient() as h:
        r = await h.request("ACL", URL + home + "default/", auth=auth, content=(
            b'<?xml version="1.0"?><d:acl xmlns:d="DAV:"><d:ace><d:principal><d:href>/dav/pal/x%40example.test/'
            b'</d:href></d:principal><d:grant><d:privilege><d:read/></d:privilege></d:grant></d:ace></d:acl>'))
        assert r.status_code == 403
        login = home.rstrip("/").rsplit("/", 1)[1]
        r = await h.post(f"{URL}/dav/itip/{login}/outbox/", auth=auth, headers={"Content-Type": "text/calendar"},
                         content=b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
        assert r.status_code == 403
        r = await h.post(URL + "/jmap/", auth=auth, json={"using": ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
                                                          "methodCalls": [["Email/query", {}, "0"]]})
        assert r.status_code in (401, 403) or "forbidden" in r.text
    if IMAPS:
        assert not _imap(user.email, made["password"])
    assert (await c.delete(f"{API}/me/dav/app-passwords/{made['id']}")).status_code == 200
    assert await _propfind(URL, auth) == 401


async def test_disabled_user_and_phone_access_off_give_401(setup):
    """ACT settings.phone_passwords: a disabled user's app password and store password stop working
    and come back when enabled; phone access off pauses the passwords and on resumes them."""
    from mailr.core.crypto import decrypt_for_user
    from mailr.db import sessionmaker
    from mailr.models import User

    app, user, c, new_user = setup
    made = await _make(c)
    auth = (user.email, made["password"])
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        store_pw = decrypt_for_user(u.id, u.data_key_wrapped, u.stalwart_secret_enc)
    assert await _propfind(URL, auth) == 207 and await _propfind(URL, (user.email, store_pw)) == 207
    adm = await new_user("admin")
    a = await _login(app, adm)
    try:
        assert (await a.patch(f"{API}/admin/users/{user.id}", json={"disabled": True})).status_code == 200
        assert await _propfind(URL, auth) == 401
        assert await _propfind(URL, (user.email, store_pw)) == 401
        if IMAPS:
            assert not _imap(user.email, store_pw)
        assert (await a.patch(f"{API}/admin/users/{user.id}", json={"disabled": False})).status_code == 200
    finally:
        await a.aclose()
    assert await _propfind(URL, auth) == 207 and await _propfind(URL, (user.email, store_pw)) == 207
    if IMAPS:
        assert _imap(user.email, store_pw)
    await _switch(False)
    await phones.reconcile()
    assert await _propfind(URL, auth) == 401
    await _switch(True)
    await phones.reconcile()
    assert await _propfind(URL, auth) == 207


async def test_store_settings_follow_the_switch(setup):
    """ACT settings.phone_access: on → useXForwarded and no automatic address books; off → back."""
    async def attempt(n):
        assert await _phone_settings() == (True, None)
        assert await phones.apply_store_settings() == []
        await _switch(False)
        assert await _phone_settings() == (False, "default")
        await _switch(True)
        assert await _phone_settings() == (True, None)

    await _holding(attempt)


async def _listing(base: str, auth: tuple[str, str], path: str) -> list[tuple[str, list[str]]]:
    body = (b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            b'<d:prop><d:resourcetype/><c:supported-calendar-component-set/></d:prop></d:propfind>')
    async with httpx.AsyncClient() as h:
        r = await h.request("PROPFIND", base + path, auth=auth, headers={"Depth": "1"}, content=body)
    assert r.status_code == 207, r.text
    out = []
    for resp in re.findall(r"<D:response>(.*?)</D:response>", r.text, re.S):
        href = re.search(r"<D:href>([^<]+)</D:href>", resp).group(1)
        out.append((href, re.findall(r'comp name="([A-Z]+)"', resp)))
    return out


async def test_a_group_home_shows_members_only_the_group_calendars(setup):
    """The stock calendar of a group account and the group's address book never reach a member's phone:
    the member's listing holds the group's own calendars and a free/busy-only placeholder."""
    from mailr.calendar import groups
    from mailr.contacts import carddav
    from mailr.db import sessionmaker

    app, user, c, new_user = setup

    async def attempt(n):
        group = f"fam-p5-{n}"  # each run its own group, member and group home
        member = await new_user(groups=[group])
        async with sessionmaker()() as db:
            gc = await groups.create(db, group, "School", "#3366cc", "read", [])
            await db.commit()
            cg = await db.get(groups.CalendarGroup, group)
            group_home = groups.home_of(cg.store_group_login)
        try:
            # A member's discovery lists the group home (assisted discovery): before seal_home that
            # made "Calendar (grp-…)" and "Contacts (grp-…)" there.
            m = await _login(app, member)
            try:
                made = await _make(m)
            finally:
                await m.aclose()
            auth = (member.email, made["password"])
            for _ in range(2):  # twice: nothing comes back after the first listing
                cals = await _listing(URL, auth, "/dav/cal/" + member.email.replace("@", "%40") + "/")
                under_group = [(h, comps) for h, comps in cals if h.startswith(group_home)]
                assert under_group and all(comps == ["VFREEBUSY"] for _h, comps in under_group), under_group
                assert any(h.rstrip("/").endswith(gc.href.rstrip("/").rsplit("/", 1)[1]) for h, _ in cals)
                books = await _listing(URL, auth, "/dav/card/" + member.email.replace("@", "%40") + "/")
                assert not [h for h, _ in books if "grp-" in h], books
            # The member's own address book: Stalwart no longer makes it; mailr's sync does.
            async with sessionmaker()() as db:
                conn = await builtin.find(db, member.id)
            st = await carddav.sync_connector(conn.id)
            assert st["available"] and st["books"] >= 1, st
            books = await _listing(URL, auth, "/dav/card/" + member.email.replace("@", "%40") + "/")
            assert any(h.rstrip("/").endswith("/default") for h, _ in books)
        finally:
            async with sessionmaker()() as db:
                await groups.delete(db, await db.get(groups.GroupCalendar, gc.id))
                await db.commit()

    await _holding(attempt)


# ---------------- the documented gateway in front of the server ----------------


def _docker(*args: str, check: bool = True) -> str:
    r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
    if check and r.returncode:
        raise RuntimeError(f"docker {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


@pytest.fixture
def gateway():
    if not CONTAINER or not shutil.which("docker"):
        pytest.skip("set MAILR_TEST_STALWART_CONTAINER (and have docker) for the gateway test")
    name = f"mailr-davgw-{uuid.uuid4().hex[:6]}"
    _docker("network", "create", name)
    try:
        _docker("network", "connect", "--alias", "stalwart", name, CONTAINER)
        gw = _docker("network", "inspect", name, "-f", "{{(index .IPAM.Config 0).Gateway}}")
        _docker("run", "-d", "--name", name, "--network", name, "-p", "127.0.0.1::8080",
                "-e", "MAILR_DAV_UPSTREAM=stalwart:8080", "-e", f"MAILR_DAV_TRUSTED_PROXY={gw}",
                "--read-only", "--tmpfs", "/tmp:uid=101,gid=101", "--tmpfs", "/etc/nginx/conf.d:uid=101,gid=101",
                "--cap-drop", "ALL", "-v", f"{TEMPLATE}:/etc/nginx/templates/default.conf.template:ro", GATEWAY_IMAGE)
        port = _docker("port", name, "8080/tcp").splitlines()[0].rsplit(":", 1)[1]
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                if httpx.get(base + "/.well-known/caldav").status_code == 301:
                    break
            except httpx.HTTPError:
                pass
            import time

            time.sleep(0.2)
        yield base
    finally:
        _docker("rm", "-f", name, check=False)
        _docker("network", "disconnect", name, CONTAINER, check=False)
        _docker("network", "rm", name, check=False)


async def test_the_documented_gateway_restricts_and_passes_the_real_address(setup, gateway):
    """ACT settings.phone_access: deploy/dav-gateway in front of the server: the discovery
    redirects, DAV with an app password, only a Basic challenge, everything else refused, the
    body limit, and the phone's address reaching Stalwart (useXForwarded) and not a forged one."""
    app, user, c, _ = setup

    async def attempt(n):
        made = await _make(c)
        auth = (user.email, made["password"])
        async with httpx.AsyncClient(base_url=gateway) as h:
            r = await h.get("/.well-known/caldav")
            assert r.status_code == 301 and r.headers["location"] == "/dav/cal/"
            assert (await h.get("/.well-known/carddav")).headers["location"] == "/dav/card/"
            r = await h.request("PROPFIND", "/dav/cal/", headers={"Depth": "0"}, content=PROPFIND)
            assert r.status_code == 401
            assert r.headers.get_list("www-authenticate") == ['Basic realm="mailr calendars", charset="UTF-8"']
            assert (await h.request("PROPFIND", "/dav/cal/", auth=auth, headers={"Depth": "0"}, content=PROPFIND)).status_code == 207
            home = await _home(gateway, auth)
            assert await _put(gateway, auth, home + "default/") == 201
            login = home.rstrip("/").rsplit("/", 1)[1]
            assert (await h.request("ACL", f"/dav/cal/{login}/default/", auth=auth, content=b"<x/>")).status_code == 405
            assert (await h.post(f"/dav/itip/{login}/outbox/", auth=auth, content=b"x")).status_code == 403
            assert (await h.request("PROPFIND", f"/dav/file/{login}/", auth=auth)).status_code == 403
            for path in ("/jmap/session", "/.well-known/jmap", "/.well-known/oauth-protected-resource", "/admin",
                         "/api/", "/auth/token", "/account", "/healthz/live"):
                r = await h.get(path, auth=auth)
                assert r.status_code == 404, (path, r.status_code)
            r = await h.put(f"/dav/cal/{login}/default/big.ics", auth=auth, content=b"x" * (11 * 1024 * 1024))
            assert r.status_code == 413
        # The phone's address: an app password limited to 203.0.113.0/24 works when the trusted proxy
        # says the phone is 203.0.113.9, and not for another address or a forged first entry.
        from mailr.core.users import _mgmt

        r = await _mgmt("x:AppPassword/set", {"accountId": user.stalwart_account_id, "create": {"p": {
            "description": phones.PREFIX + "limited", "allowedIps": {"203.0.113.0/24": True},
            "permissions": {"@type": "Replace", "permissions": {p: True for p in phones.DAV_PERMISSIONS}}}}})
        limited = (user.email, r["created"]["p"]["secret"])
        assert await _propfind(gateway, limited, headers={"X-Forwarded-For": "203.0.113.9"}) == 207
        # Stalwart answers 403 for a password used from an address it does not allow.
        assert await _propfind(gateway, limited, headers={"X-Forwarded-For": "198.51.100.1"}) == 403
        assert await _propfind(gateway, limited, headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.1"}) == 403
        assert await _propfind(gateway, limited) == 403
        # Without useXForwarded Stalwart sees the gateway's own address: the limit cannot match.
        from mailr.core.store_groupware import ensure_phone_settings

        await ensure_phone_settings(False)
        await asyncio.sleep(0.5)
        assert await _propfind(gateway, limited, headers={"X-Forwarded-For": "203.0.113.9"}) == 403
        await ensure_phone_settings(True)

    await _holding(attempt)
