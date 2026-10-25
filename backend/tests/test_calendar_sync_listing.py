"""Calendar and address-book sync against a fake CalDAV server: listings the server cuts short
never remove anything, and a token the server accepts with an empty delta while the collection's
ctag changed leads to a full ETag listing.

The fake answers like Stalwart 0.16: past its result limit a
sync-collection or a depth-1 PROPFIND ends with a 507 response for the request URI, and the
sync-collection carries the server's *current* token, not a continuation token. A token from the
future (a restored server) is taken and gives an empty delta.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlsplit

import httpx
import pytest
from sqlalchemy import select

from mailr.calendar import dav, sync
from mailr.db import sessionmaker
from mailr.models import CalCollection, CalObject, Connector

BASE = "https://dav.northwind.example/dav/cal/"
HOME = BASE + "u/"
COLL = HOME + "work/"
D = "{DAV:}"


def _ics(uid: str, summary: str) -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//fake//EN\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:20261004T120000Z\r\nDTSTART:20261012T090000Z\r\n"
            f"DTEND:20261012T100000Z\r\nSUMMARY:{summary}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


class FakeDav:
    """One calendar collection. `limit` is the result cap (None: no cap); `cap_report` and
    `cap_propfind` say which listings it applies to; `future_tokens` makes every unknown or newer
    token valid with an empty delta (Stalwart after a restore)."""

    def __init__(self, n: int = 5):
        self.counter = 0
        self.items: dict[str, tuple[int, str]] = {}  # name -> (version, ics)
        self.tombs: dict[str, int] = {}
        self.limit: int | None = None
        self.cap_report = self.cap_propfind = True
        self.future_tokens = False
        self.requests: list[str] = []
        for i in range(n):
            self.put(f"e{i}", f"Event {i}")

    def put(self, name: str, summary: str) -> None:
        self.counter += 1
        self.items[name] = (self.counter, _ics(f"{name}@fake", summary))
        self.tombs.pop(name, None)

    def delete(self, name: str) -> None:
        self.counter += 1
        self.items.pop(name)
        self.tombs[name] = self.counter

    def etag(self, name: str) -> str:
        return f'"{name}-{self.items[name][0]}"'

    @property
    def token(self) -> str:
        return f"urn:fake:{self.counter}"

    def ms(self, parts: list[str], token: str | None = None) -> httpx.Response:
        tok = f"<d:sync-token>{token}</d:sync-token>" if token else ""
        body = f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" ' \
               f'xmlns:cs="http://calendarserver.org/ns/">{"".join(parts)}{tok}</d:multistatus>'
        return httpx.Response(207, content=body.encode(), headers={"Content-Type": "application/xml"})

    def item(self, name: str, data: bool = False) -> str:
        cal = f"<c:calendar-data>{self.items[name][1]}</c:calendar-data>" if data else ""
        return (f"<d:response><d:href>{urlsplit(COLL).path}{name}.ics</d:href><d:propstat><d:prop>"
                f"<d:getetag>{self.etag(name)}</d:getetag>{cal}</d:prop>"
                "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>")

    @staticmethod
    def cut() -> str:
        return (f"<d:response><d:href>{urlsplit(COLL).path}</d:href>"
                "<d:status>HTTP/1.1 507 Insufficient Storage</d:status>"
                "<d:error><d:number-of-matches-within-limits/></d:error></d:response>")

    def capped(self, names: list[str], on: bool) -> tuple[list[str], bool]:
        if on and self.limit is not None and len(names) >= self.limit:
            return names[:self.limit], True
        return names, False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = unquote(req.url.path)
        self.requests.append(f"{req.method} {path}")
        body = ET.fromstring(req.content) if req.content else None
        if req.method == "PROPFIND" and path == urlsplit(HOME).path:
            coll = (f"<d:response><d:href>{urlsplit(COLL).path}</d:href><d:propstat><d:prop>"
                    "<d:resourcetype><d:collection/><c:calendar/></d:resourcetype><d:displayname>Work</d:displayname>"
                    f"<cs:getctag>\"{max([v for v, _ in self.items.values()] + list(self.tombs.values()) + [0])}\"</cs:getctag>"
                    f"<d:sync-token>{self.token}</d:sync-token>"
                    "<c:supported-calendar-component-set><c:comp name=\"VEVENT\"/></c:supported-calendar-component-set>"
                    "<d:current-user-privilege-set><d:privilege><d:all/></d:privilege></d:current-user-privilege-set>"
                    "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>")
            return self.ms([coll])
        if req.method == "PROPFIND" and path == urlsplit(COLL).path:
            names, cut = self.capped(sorted(self.items), self.cap_propfind)
            return self.ms([self.item(n) for n in names] + ([self.cut()] if cut else []))
        if req.method == "REPORT" and body is not None and body.tag == f"{D}sync-collection":
            raw = (body.findtext(f"{D}sync-token") or "").strip()
            since = 0
            if raw:
                m = re.fullmatch(r"urn:fake:(\d+)", raw)
                if not m:
                    return httpx.Response(403, content=b'<d:error xmlns:d="DAV:"><d:valid-sync-token/></d:error>')
                since = int(m.group(1))
                if since > self.counter and self.future_tokens:
                    return self.ms([], self.token)
            names = sorted(n for n, (v, _) in self.items.items() if v > since)
            gone = sorted(n for n, v in self.tombs.items() if v > since) if raw else []
            names, cut = self.capped(names, self.cap_report)
            parts = [self.item(n) for n in names] + [
                f"<d:response><d:href>{urlsplit(COLL).path}{n}.ics</d:href>"
                "<d:status>HTTP/1.1 404 Not Found</d:status></d:response>" for n in gone]
            return self.ms(parts + ([self.cut()] if cut else []), self.token)
        if req.method == "REPORT" and body is not None and body.tag.endswith("calendar-multiget"):
            want = [unquote(h.text or "").rsplit("/", 1)[-1].removesuffix(".ics") for h in body.findall(f"{D}href")]
            return self.ms([self.item(n, data=True) for n in want if n in self.items])
        return httpx.Response(405)


@pytest.fixture
def fake(monkeypatch):
    server = FakeDav()

    async def no_check(*a, **k):
        return None

    monkeypatch.setattr(dav.netguard, "check_url", no_check)

    def client_for(conn, user):
        return dav.DavClient(BASE, "u", "p", http=httpx.AsyncClient(transport=httpx.MockTransport(server)))

    monkeypatch.setattr(sync, "dav_client", client_for)
    return server


async def _connector(user) -> int:
    async with sessionmaker()() as db:
        c = Connector(user_id=user.id, kind="caldav", name="Fake", config={"url": BASE},
                      state={"caldav": {"home": HOME}})
        db.add(c)
        await db.commit()
        return c.id


async def _mirror(conn_id: int) -> tuple[CalCollection, dict[str, str]]:
    async with sessionmaker()() as db:
        coll = (await db.execute(select(CalCollection).where(CalCollection.connector_id == conn_id))).scalar_one()
        rows = (await db.execute(select(CalObject.href, CalObject.etag).where(
            CalObject.collection_id == coll.id, CalObject.deleted_at.is_(None)))).all()
        return coll, {h.rsplit("/", 1)[-1].removesuffix(".ics"): e for h, e in rows}


async def test_davclient_reports_a_cut_listing(fake):
    """A 507 inside the multistatus: sync-collection says truncated, an ETag listing raises."""
    fake.limit = 3
    async with sync.dav_client(None, None) as c:
        changed, _, token, truncated = await c.sync_collection(COLL, None)
        assert truncated and len(changed) == 3 and token == fake.token  # the current token, not a continuation
        with pytest.raises(dav.Truncated):
            await c.etags(COLL)
        fake.limit = None
        assert len(await c.etags(COLL)) == 5


async def test_cut_first_listing_removes_nothing_and_retries(fake, user):
    fake.limit = 3
    cid = await _connector(user)
    res = await sync.sync_connector(cid)
    coll, objs = await _mirror(cid)
    assert res.deleted == 0 and sorted(objs) == ["e0", "e1", "e2"]
    assert coll.sync_status == "backoff" and coll.sync_error == sync.TRUNCATED_ERROR
    assert coll.sync_token is None and coll.last_full_sync_at is None
    async with sessionmaker()() as db:
        conn = await db.get(Connector, cid)
        assert conn.status == "error" and "only part" in conn.status_detail
        assert not conn.state["caldav"].get("initial_done")

    fake.limit = None  # the admin raised the server's limit
    await sync.sync_connector(cid)
    coll, objs = await _mirror(cid)
    assert sorted(objs) == ["e0", "e1", "e2", "e3", "e4"]
    assert coll.sync_status == "idle" and coll.sync_token == fake.token and coll.last_full_sync_at


async def test_cut_full_resync_never_soft_deletes(fake, user):
    """The bug the feasibility study found: a full listing cut at the limit soft-deleted every
    mirror row past it."""
    cid = await _connector(user)
    await sync.sync_connector(cid)
    assert len((await _mirror(cid))[1]) == 5
    fake.limit = 2
    res = await sync.sync_connector(cid, full=True)
    coll, objs = await _mirror(cid)
    assert res.deleted == 0 and len(objs) == 5
    assert coll.sync_status == "backoff"


async def test_cut_sync_falls_back_to_a_full_etag_listing(fake, user):
    """Only the REPORT is capped: the ETag listing decides, and real removals still happen."""
    cid = await _connector(user)
    await sync.sync_connector(cid)
    fake.limit, fake.cap_propfind = 2, False
    fake.delete("e4")
    fake.put("e1", "Event 1 moved")
    fake.put("e5", "Event 5")
    fake.requests.clear()
    res = await sync.sync_connector(cid, full=True)
    coll, objs = await _mirror(cid)
    assert sorted(objs) == ["e0", "e1", "e2", "e3", "e5"] and res.deleted == 1
    assert objs["e1"] == fake.etag("e1")
    assert any(r.startswith("PROPFIND /dav/cal/u/work/") for r in fake.requests)
    assert coll.sync_status == "idle" and coll.sync_token == fake.token


async def test_cut_delta_is_completed_from_the_listing(fake, user):
    """A delta past the cap: the items after it would never come with Stalwart's token."""
    cid = await _connector(user)
    await sync.sync_connector(cid)
    fake.limit, fake.cap_propfind = 2, False
    for n in ("e0", "e1", "e2", "e3"):
        fake.put(n, f"{n} changed")
    await sync.sync_connector(cid)
    coll, objs = await _mirror(cid)
    assert all(objs[n] == fake.etag(n) for n in ("e0", "e1", "e2", "e3"))
    assert coll.sync_status == "idle"


async def test_empty_delta_with_a_changed_ctag_lists_everything(fake, user):
    """A restored server: mailr's token is from its future, the server takes it and answers with
    an empty delta, but the collection's ctag moved."""
    fake.counter += 20  # changes elsewhere in the account
    cid = await _connector(user)
    await sync.sync_connector(cid)
    fake.future_tokens = True
    fake.counter -= 10  # the restore went back in time; mailr's token is now in the future
    fake.delete("e4")
    fake.put("e0", "e0 after restore")
    fake.put("e7", "Only on the restored server")
    async with sessionmaker()() as db:
        coll = (await db.execute(select(CalCollection).where(CalCollection.connector_id == cid))).scalar_one()
        assert int(coll.sync_token.rsplit(":", 1)[1]) > fake.counter  # really a future token
    fake.requests.clear()
    res = await sync.sync_connector(cid)
    coll, objs = await _mirror(cid)
    assert sorted(objs) == ["e0", "e1", "e2", "e3", "e7"] and res.deleted == 1
    assert objs["e0"] == fake.etag("e0")
    assert "PROPFIND /dav/cal/u/work/" in fake.requests


async def test_empty_delta_with_the_same_ctag_lists_nothing(fake, user):
    """The usual case on Stalwart: one token per account, so another calendar's change moves the
    token and this collection's delta is empty. Its ctag did not move: no listing."""
    cid = await _connector(user)
    await sync.sync_connector(cid)
    fake.counter += 1  # a change in another collection of the account
    fake.requests.clear()
    await sync.sync_connector(cid)
    assert not any(r.startswith("PROPFIND /dav/cal/u/work/") for r in fake.requests)
    assert any(r.startswith("REPORT") for r in fake.requests)


# ---------------- address books (contacts/carddav.py) ----------------

AB = BASE.replace("/cal/", "/card/") + "u/contacts/"


def _vcf(uid: str, name: str) -> str:
    return f"BEGIN:VCARD\r\nVERSION:3.0\r\nUID:{uid}\r\nFN:{name}\r\nEMAIL:{uid}@northwind.example\r\nEND:VCARD\r\n"


class FakeCard(FakeDav):
    def item(self, name: str, data: bool = False) -> str:
        card = f"<card:address-data>{_vcf(name, name.upper())}</card:address-data>" if data else ""
        return (f"<d:response><d:href>{urlsplit(AB).path}{name}.vcf</d:href><d:propstat><d:prop>"
                f"<d:getetag>{self.etag(name)}</d:getetag>{card}</d:prop>"
                "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>")

    def ms(self, parts, token=None):
        r = super().ms(parts, token)
        return httpx.Response(207, content=r.content.replace(
            b'xmlns:d="DAV:"', b'xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav"'),
            headers={"Content-Type": "application/xml"})

    def __call__(self, req):
        path = unquote(req.url.path)
        body = ET.fromstring(req.content) if req.content else None
        if req.method == "PROPFIND" and path == urlsplit(AB).path:
            names, cut = self.capped(sorted(self.items), self.cap_propfind)
            return self.ms([self.item(n) for n in names] + ([self.cut()] if cut else []))
        if req.method == "REPORT" and body is not None and body.tag.endswith("addressbook-multiget"):
            want = [unquote(h.text or "").rsplit("/", 1)[-1].removesuffix(".vcf") for h in body.findall(f"{D}href")]
            return self.ms([self.item(n, data=True) for n in want if n in self.items])
        return super().__call__(req)


async def test_address_book_cut_listing_deletes_no_card(monkeypatch, user):
    from mailr.contacts import carddav
    from mailr.models import AddressBook, ContactCard

    server = FakeCard()

    async def no_check(*a, **k):
        return None

    monkeypatch.setattr(dav.netguard, "check_url", no_check)
    client = carddav.CardDavClient(BASE, "u", "p", http=httpx.AsyncClient(transport=httpx.MockTransport(server)))
    async with sessionmaker()() as db:
        c = Connector(user_id=user.id, kind="caldav", name="Fake", config={"url": BASE})
        db.add(c)
        await db.flush()
        b = AddressBook(user_id=user.id, connector_id=c.id, href=client.abs(AB))
        db.add(b)
        await db.commit()
        cid, bid = c.id, b.id

    def info():
        return dav.CollectionInfo(href=client.abs(AB), ctag=f'"{server.counter}"', sync_token=server.token)

    async def cards():
        async with sessionmaker()() as db:
            return sorted((await db.execute(select(ContactCard.uid).where(ContactCard.book_id == bid))).scalars())

    await carddav._sync_book(client, bid, info(), False, user.id, cid)
    assert await cards() == ["e0", "e1", "e2", "e3", "e4"]
    server.limit = 2  # both listings capped: an error, nothing deleted
    with pytest.raises(dav.Truncated):
        await carddav._sync_book(client, bid, info(), True, user.id, cid)
    assert len(await cards()) == 5
    server.cap_propfind = False  # only the REPORT capped: the listing decides
    server.delete("e3")
    server.put("e9", "Nine")
    async with sessionmaker()() as db:
        (await db.get(AddressBook, bid)).sync_token = None
        await db.commit()
    await carddav._sync_book(client, bid, info(), False, user.id, cid)
    assert await cards() == ["e0", "e1", "e2", "e4", "e9"]
    await client.aclose()


async def test_a_listing_older_than_a_delete_does_not_bring_the_collection_back(fake, user, monkeypatch):
    """The e2e flake in tasks-app-lists: a sync lists the collections, the user deletes one here
    before the sync writes the listing, and the sync used to undelete it ("back again")."""
    from mailr.calendar import mirror

    cid = await _connector(user)
    await sync.sync_connector(cid)
    coll, _ = await _mirror(cid)
    assert coll.deleted_at is None

    listing = dav.DavClient.collections

    async def deleted_meanwhile(self, home):
        infos = await listing(self, home)  # still lists COLL
        async with sessionmaker()() as db:
            await mirror.soft_delete_collection(db, await db.get(CalCollection, coll.id))
            await db.commit()
        return infos

    monkeypatch.setattr(dav.DavClient, "collections", deleted_meanwhile)
    res = await sync.sync_connector(cid)
    coll, objs = await _mirror(cid)
    assert coll.deleted_at is not None and objs == {} and coll.id not in res.collections

    # A later listing that still has it (made again on the server) brings it back.
    monkeypatch.setattr(dav.DavClient, "collections", listing)
    await sync.sync_connector(cid)
    coll, objs = await _mirror(cid)
    assert coll.deleted_at is None and sorted(objs) == ["e0", "e1", "e2", "e3", "e4"]


async def test_readers_waiting_on_a_sync_share_one_more_run(fake, user, monkeypatch):
    """Reads that arrive while a sync runs wait for it, then one more run serves them all. Each
    used to run its own sync in turn, which slowed every read on a busy server.

    No sleeps: the first run is held inside its listing (so it listed before the readers asked),
    and it is let go only once every reader is in the busy wait."""
    import asyncio

    cid = await _connector(user)
    await sync.sync_connector(cid)

    runs: list[int] = []
    real = sync._sync

    async def counted(connector_id, full):
        runs.append(connector_id)
        return await real(connector_id, full)

    listing_started, gate = asyncio.Event(), asyncio.Event()
    listing = dav.DavClient.collections

    async def held(self, home):
        if len(runs) == 1:
            listing_started.set()
            await gate.wait()
        return await listing(self, home)

    waiting: set[asyncio.Task] = set()
    readers: list[asyncio.Task] = []
    listed_since = sync._listed_since

    async def noted(connector_id, since):
        # A reader asks this only from the busy wait, after its own try found the lock taken.
        task = asyncio.current_task()
        if task in readers:
            waiting.add(task)
        return await listed_since(connector_id, since)

    monkeypatch.setattr(sync, "_sync", counted)
    monkeypatch.setattr(sync, "_listed_since", noted)
    monkeypatch.setattr(dav.DavClient, "collections", held)
    first = asyncio.create_task(sync.sync_connector(cid))
    await asyncio.wait_for(listing_started.wait(), 30)
    readers.extend(asyncio.create_task(sync.run_inline(cid, budget=60)) for _ in range(3))
    async with asyncio.timeout(30):
        while len(waiting) < 3:  # noqa: ASYNC110
            await asyncio.sleep(0.01)
    gate.set()
    await first
    results = await asyncio.wait_for(asyncio.gather(*readers), 60)
    assert all(r is not None and not r.busy for r in results), results
    assert len(runs) == 2, runs


async def test_a_run_that_already_served_the_reader_is_not_repeated(fake, user, monkeypatch):
    """Under the lock, a reader's run first asks whether a run listed the server after the reader
    asked. One that ended a moment ago (between the reader's last look and its try) is enough:
    the extra runs seen under load ([6, 6, 6])."""
    from datetime import UTC, datetime

    cid = await _connector(user)
    runs: list[int] = []
    real = sync._sync

    async def counted(connector_id, full):
        runs.append(connector_id)
        return await real(connector_id, full)

    monkeypatch.setattr(sync, "_sync", counted)
    asked = datetime.now(UTC)
    await sync.sync_connector(cid)  # someone else's run, listing after the reader asked
    res = await sync.sync_connector(cid, served_since=asked)
    assert not res.busy and runs == [cid]
    await sync.sync_connector(cid, served_since=datetime.now(UTC))  # asked after that listing: runs
    await sync.sync_connector(cid, full=True, served_since=asked)  # a full sync always runs
    assert runs == [cid, cid, cid]
