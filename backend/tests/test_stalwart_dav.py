"""Stalwart as a CalDAV server for mailr: the facts the built-in calendars rely on, checked against a real Stalwart with mailr's own DavClient.

The module runs against the suite's own store server, the dev Stalwart of
deploy/dev/docker-compose.yml, unless these name another throwaway Stalwart provisioned from
deploy/stalwart:

    MAILR_TEST_STALWART_URL=http://127.0.0.1:18995      # Stalwart's HTTP listener (8080 inside)
    MAILR_TEST_STALWART_ADMIN=mailr-svc@example.test:<password>   # the Admin service account

It is never pointed at production. MAILR_TEST_WITHOUT_STALWART_DAV=1 skips it. A run applies
mailr's groupware settings (core/store_groupware.py) to that server, creates its own accounts with
random names and deletes them at the end. It runs in every backend run, so a Stalwart upgrade in
the compose file is checked at once.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import uuid

import httpx
import pytest
import pytest_asyncio
from dev_services import stalwart_dav_skip_reason

from mailr.calendar import dav
from mailr.core.jmap import CORE, JMAPClient
from mailr.core.store_groupware import ensure_groupware_settings

URL = os.environ.get("MAILR_TEST_STALWART_URL", "").rstrip("/")
ADMIN = os.environ.get("MAILR_TEST_STALWART_ADMIN", "")
pytestmark = [
    pytest.mark.skipif(not (URL and ":" in ADMIN), reason=stalwart_dav_skip_reason()),
    pytest.mark.serial,  # changes the server's settings
]
MGMT = [CORE, "urn:stalwart:jmap"]
TZ = ("BEGIN:VTIMEZONE\r\nTZID:America/Chicago\r\nBEGIN:STANDARD\r\nDTSTART:19701101T020000\r\n"
      "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\r\nTZOFFSETFROM:-0500\r\nTZOFFSETTO:-0600\r\nEND:STANDARD\r\n"
      "BEGIN:DAYLIGHT\r\nDTSTART:19700308T020000\r\nRRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\r\n"
      "TZOFFSETFROM:-0600\r\nTZOFFSETTO:-0500\r\nEND:DAYLIGHT\r\nEND:VTIMEZONE\r\n")


def _event(uid: str, summary: str, extra: str = "") -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//mailr test//EN\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:20261004T120000Z\r\nDTSTART:20261012T140000Z\r\nDTEND:20261012T150000Z\r\n"
            f"SUMMARY:{summary}\r\n{extra}END:VEVENT\r\nEND:VCALENDAR\r\n")


def _todo(uid: str, summary: str) -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//mailr test//EN\r\nBEGIN:VTODO\r\n"
            f"UID:{uid}\r\nDTSTAMP:20261004T120000Z\r\nSUMMARY:{summary}\r\nDUE;VALUE=DATE:20261010\r\n"
            "STATUS:NEEDS-ACTION\r\nEND:VTODO\r\nEND:VCALENDAR\r\n")


def _recurring(uid: str) -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//mailr test//EN\r\n" + TZ + "BEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:20261004T120000Z\r\nDTSTART;TZID=America/Chicago:20261012T090000\r\n"
            "DTEND;TZID=America/Chicago:20261012T100000\r\nRRULE:FREQ=WEEKLY;COUNT=5\r\nSUMMARY:Weekly\r\n"
            "END:VEVENT\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:20261004T120000Z\r\nRECURRENCE-ID;TZID=America/Chicago:20261019T090000\r\n"
            "DTSTART;TZID=America/Chicago:20261019T110000\r\nDTEND;TZID=America/Chicago:20261019T120000\r\n"
            "SUMMARY:Weekly (moved)\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


class Stalwart:
    def __init__(self):
        self.admin_login, self.admin_pw = ADMIN.split(":", 1)
        self.domain = self.admin_login.split("@", 1)[1]
        self.jmap = JMAPClient(URL, (self.admin_login, self.admin_pw))
        self.accounts: dict[str, tuple[str, str]] = {}  # short name -> (login, password)
        self.ids: list[str] = []

    async def mgmt(self, method: str, args: dict) -> dict:
        return await self.jmap.one(method, args, using=MGMT)

    async def make_user(self, short: str) -> tuple[str, str]:
        name = f"p0t-{short}-{secrets.token_hex(3)}"
        pw = secrets.token_urlsafe(16)
        dom = (await self.mgmt("x:Domain/query", {"filter": {"name": self.domain}}))["ids"][0]
        r = await self.mgmt("x:Account/set", {"create": {"a": {
            "@type": "User", "name": name, "domainId": dom, "description": "mailr test_stalwart_dav",
            "credentials": {"0": {"@type": "Password", "secret": pw}}}}})
        self.ids.append(r["created"]["a"]["id"])
        login = f"{name}@{self.domain}"
        self.accounts[short] = (login, pw)
        return login, pw

    def client(self, short: str) -> dav.DavClient:
        login, pw = self.accounts[short]
        return dav.DavClient(URL + "/dav/cal/", login, pw, private_ok=True)

    def home_of(self, short: str) -> str:
        return f"{URL}/dav/cal/{self.accounts[short][0].replace('@', '%40')}/"


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def sw():
    s = Stalwart()
    await ensure_groupware_settings(s.jmap)
    for short in ("alice", "bob", "carol"):
        await s.make_user(short)
    yield s
    if s.ids:
        await s.mgmt("x:Account/set", {"destroy": s.ids})
    await s.jmap.close()


async def _default(c: dav.DavClient) -> dav.CollectionInfo:
    infos = await c.collections(await c.home())
    return next(i for i in infos if i.href.rstrip("/").endswith("/default"))


async def test_settings_are_applied_and_the_reconcile_is_idempotent(sw):
    assert await ensure_groupware_settings(sw.jmap) == []
    got = await sw.jmap.call([[f"{o}/get", {"ids": ["singleton"]}, o] for o in (
        "x:CalendarScheduling", "x:CalendarAlarm", "x:WebDav", "x:Calendar", "x:AddressBook", "x:Sharing")], using=MGMT)
    s = {tag: args["list"][0] for _, args, tag in got}
    assert s["x:CalendarScheduling"]["enable"] is False and s["x:CalendarScheduling"]["httpRsvpEnable"] is False
    assert s["x:CalendarAlarm"]["enable"] is False
    assert s["x:WebDav"]["maxResults"] >= 50000 and s["x:WebDav"]["enableAssistedDiscovery"] is True
    assert s["x:Calendar"]["maxAttendees"] >= 100 and s["x:Sharing"]["allowDirectoryQueries"] is False


async def test_discovery_and_the_default_calendar(sw):
    async with httpx.AsyncClient() as h:
        r = await h.request("OPTIONS", URL + "/dav/cal/", auth=sw.accounts["alice"])
        caps = {x.strip() for x in r.headers.get("dav", "").split(",")}
        assert {"calendar-access", "addressbook", "access-control"} <= caps
        # Known quirk (0.16.23): still advertised with scheduling off. Phones then expect the
        # server to send invitations. When this fails, the upgrade fixed it: update the dev doc.
        assert "calendar-auto-schedule" in caps
        r = await h.get(URL + "/.well-known/caldav", auth=sw.accounts["alice"])
        assert r.status_code in (301, 307, 308) and r.headers["location"].rstrip("/").endswith("/dav/cal")
    async with sw.client("alice") as c:
        home = await c.home()
        assert c.same_resource(home, sw.home_of("alice"))
        d = await _default(c)  # created on first access
        assert d.displayname.startswith("Calendar") and {"VEVENT", "VTODO"} <= set(d.components)
        assert d.sync_token and d.sync_token.startswith("urn:stalwart:davsync:") and d.ctag and not d.read_only


async def test_sync_collection_delta_update_delete(sw):
    async with sw.client("alice") as c:
        coll = (await _default(c)).href
        _, _, t0, truncated = await c.sync_collection(coll, None)
        assert t0 and not truncated
        href = coll + f"{uuid.uuid4().hex}.ics"
        etag = await c.put(href, _event(uuid.uuid4().hex, "Sync me"), if_none_match=True)
        changed, removed, t1, _ = await c.sync_collection(coll, t0)
        assert changed == [(c.abs(href), etag)] and removed == [] and t1 != t0
        uid = re.search(r"UID:(\S+)", (await c.get(href))[0]).group(1)
        etag2 = await c.put(href, _event(uid, "Sync me again"), if_match=etag)
        changed, _, t2, _ = await c.sync_collection(coll, t1)
        assert changed == [(c.abs(href), etag2)]
        await c.delete(href, if_match=etag2)
        changed, removed, _, _ = await c.sync_collection(coll, t2)
        assert changed == [] and removed == [c.abs(href)]
        # Tokens are not checked: one from the future gives an empty delta (no InvalidSyncToken),
        # which is why sync.py lists everything when the ctag moved with an empty delta.
        changed, removed, _, _ = await c.sync_collection(coll, "urn:stalwart:davsync:999999999")
        assert changed == [] and removed == []


async def test_multiget_recurrence_override_and_conditional_writes(sw):
    async with sw.client("alice") as c:
        coll = (await _default(c)).href
        uid = uuid.uuid4().hex
        href = coll + f"{uid}.ics"
        etag = await c.put(href, _recurring(uid), if_none_match=True)
        [(_, e, ics)] = await c.multiget(coll, [href])
        assert e == etag and ics.count("BEGIN:VEVENT") == 2
        assert "RECURRENCE-ID;TZID=America/Chicago:20261019T090000" in ics and "RRULE:FREQ=WEEKLY;COUNT=5" in ics
        assert (await c.get(href))[1] == etag
        with pytest.raises(dav.PreconditionFailed):
            await c.put(href, _recurring(uid), if_none_match=True)
        with pytest.raises(dav.PreconditionFailed):
            await c.put(href, _recurring(uid), if_match='"stale"')
        assert await c.put(href, _recurring(uid).replace("Weekly (moved)", "Moved"), if_match=etag) != etag


async def test_vtodo_and_mkcalendar(sw):
    async with sw.client("bob") as c:
        home = await c.home()
        tasks = await c.mkcalendar(home, f"tasks-{secrets.token_hex(3)}", "Tasks", ["VTODO"], color="#3366cc")
        events = await c.mkcalendar(home, f"trips-{secrets.token_hex(3)}", "Trips", ["VEVENT"])
        infos = {i.href: i for i in await c.collections(home)}
        assert infos[tasks].components == ["VTODO"] and infos[tasks].color == "#3366cc"
        assert infos[events].components == ["VEVENT"]
        assert await c.proppatch(events, displayname="Holidays")
        assert {i.href: i for i in await c.collections(home)}[events].displayname == "Holidays"
        href = tasks + f"{uuid.uuid4().hex}.ics"
        etag = await c.put(href, _todo(uuid.uuid4().hex, "Buy stamps"), if_none_match=True)
        assert (await c.etags(tasks)) == {c.abs(href): etag}


async def test_scheduling_is_off(sw):
    """An organizer PUT without SCHEDULE-AGENT=CLIENT (as a phone would send it): Stalwart
    queues no mail, puts no copy in the guest's calendar and no invitation in their inbox."""
    alice, bob = sw.accounts["alice"][0], sw.accounts["bob"][0]
    before = (await sw.mgmt("x:QueuedMessage/query", {}))["ids"]
    async with sw.client("alice") as c:
        coll = (await _default(c)).href
        uid = uuid.uuid4().hex
        await c.put(coll + f"{uid}.ics", _event(uid, "Server scheduling probe",
                    f"ORGANIZER:mailto:{alice}\r\nATTENDEE;PARTSTAT=ACCEPTED:mailto:{alice}\r\n"
                    f"ATTENDEE;RSVP=TRUE:mailto:{bob}\r\nATTENDEE;RSVP=TRUE:mailto:guest@contoso.example\r\n"),
                    if_none_match=True)
        stored, _ = await c.get(coll + f"{uid}.ics")
        assert "SCHEDULE-STATUS" not in stored
    await asyncio.sleep(3)
    assert (await sw.mgmt("x:QueuedMessage/query", {}))["ids"] == before
    async with sw.client("bob") as c:
        assert not any(uid in h for h in await c.etags((await _default(c)).href))
    bj = JMAPClient(URL, sw.accounts["bob"])
    try:
        s = await bj.session()
        acct = s["primaryAccounts"]["urn:ietf:params:jmap:mail"]
        r = await bj.one("Email/query", {"accountId": acct, "filter": {}})
        assert r["ids"] == []
    finally:
        await bj.close()


async def test_listings_past_2000_are_complete(sw):
    async with sw.client("carol") as c:
        coll = await c.mkcalendar(await c.home(), f"big-{secrets.token_hex(3)}", "Big", ["VTODO"])
        sem = asyncio.Semaphore(4)  # Stalwart refuses a 5th concurrent request per account (400)

        async def one(i: int) -> None:
            async with sem:
                await c.put(coll + f"t{i}.ics", _todo(f"big-{i}-{uuid.uuid4().hex[:6]}", f"Item {i}"))

        await asyncio.gather(*(one(i) for i in range(2100)))
        changed, _, _, truncated = await c.sync_collection(coll, None)
        assert not truncated and len(changed) == 2100
        assert len(await c.etags(coll)) == 2100  # raises dav.Truncated when cut
        await c.delete_collection(coll)


async def test_no_directory_for_normal_users(sw):
    """A user sees no other account: no JMAP Principal/*, no DAV principal reports."""
    bob_name = sw.accounts["bob"][0].split("@")[0]
    async with httpx.AsyncClient() as h:
        auth = sw.accounts["alice"]
        s = (await h.get(URL + "/jmap/session", auth=auth)).json()
        acct = s["primaryAccounts"]["urn:ietf:params:jmap:mail"]
        r = (await h.post(URL + "/jmap/", auth=auth, json={
            "using": [CORE, "urn:ietf:params:jmap:principals"],
            "methodCalls": [["Principal/query", {"accountId": acct}, "q"],
                            ["Principal/get", {"accountId": acct, "ids": None}, "g"]]})).json()
        assert [x[0] for x in r["methodResponses"]] == ["error", "error"]
        assert {x[1]["type"] for x in r["methodResponses"]} == {"forbidden"}
        pps = ('<?xml version="1.0"?><d:principal-property-search xmlns:d="DAV:"><d:property-search><d:prop>'
               f'<d:displayname/></d:prop><d:match>{bob_name}</d:match></d:property-search><d:prop><d:displayname/>'
               '</d:prop></d:principal-property-search>')
        r = await h.request("REPORT", URL + "/dav/pal/", auth=auth, content=pps,
                            headers={"Depth": "0", "Content-Type": "application/xml"})
        assert r.status_code == 403 and bob_name not in r.text
        pf = '<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:displayname/></d:prop></d:propfind>'
        r = await h.request("PROPFIND", URL + "/dav/pal/", auth=auth, content=pf, headers={"Depth": "1"})
        assert bob_name not in r.text
        r = await h.request("PROPFIND", URL + f"/dav/pal/{sw.accounts['bob'][0].replace('@', '%40')}/",
                            auth=auth, content=pf, headers={"Depth": "0"})
        assert "403" in r.text and "mailr test" not in r.text


async def test_sharing_still_works_without_the_directory(sw):
    """An ACL names the sharee by principal href; mailr picks people from its own user list."""
    bob = sw.accounts["bob"][0]
    async with sw.client("alice") as c:
        coll = await c.mkcalendar(await c.home(), f"shared-{secrets.token_hex(3)}", "Shared", ["VEVENT"])
        body = ('<?xml version="1.0"?><d:acl xmlns:d="DAV:"><d:ace><d:principal><d:href>/dav/pal/'
                f'{bob.replace("@", "%40")}/</d:href></d:principal><d:grant><d:privilege><d:read/></d:privilege>'
                '</d:grant></d:ace></d:acl>')
        r = await c.request("ACL", coll, body=body, headers={"Content-Type": "application/xml"})
        assert r.status_code == 200, r.text
    async with sw.client("bob") as c:
        seen = {i.href: i for i in await c.collections(await c.home())}
        assert c.abs(coll) in seen and seen[c.abs(coll)].read_only
        with pytest.raises(dav.DavError):
            await c.put(coll + "x.ics", _event(uuid.uuid4().hex, "not allowed"), if_none_match=True)


async def test_impersonation_reaches_the_users_calendars(sw):
    """The built-in connector signs in as `<user login>%<service login>` with the service password, so rotating
    the user's IMAP password never breaks calendar sync."""
    login = sw.accounts["carol"][0]
    c = dav.DavClient(URL + "/dav/cal/", f"{login}%{sw.admin_login}", sw.admin_pw, private_ok=True)
    async with c:
        home = await c.home()
        assert c.same_resource(home, sw.home_of("carol"))
        coll = (await _default(c)).href
        uid = uuid.uuid4().hex
        etag = await c.put(coll + f"{uid}.ics", _event(uid, "Written through impersonation"), if_none_match=True)
        assert etag
        made = await c.mkcalendar(home, f"imp-{secrets.token_hex(3)}", "Made as carol", ["VTODO"])
        assert c.same_resource(made.rsplit("/", 2)[0] + "/", sw.home_of("carol"))
    async with sw.client("carol") as own:
        assert any(uid in h for h in await own.etags(coll))
    bad = dav.DavClient(URL + "/dav/cal/", f"{login}%{sw.admin_login}", "wrong-password", private_ok=True)
    async with bad:
        with pytest.raises(dav.Unauthorized):
            await bad.home()
