# ACT-SURFACES: api assistant
"""The read-only CardDAV mirror (contacts.sync) against the dev Radicale (:15232):
an address book seeded over HTTP, the first sync, merge rules (address-book values win, mail names
never replace them, provenance), groups from KIND:group, Apple's X-ADDRESSBOOKSERVER members and
CATEGORIES, incremental sync with the sync token, removed cards, and that mailr never writes.
Also a group of guests in an assistant invitation: a new guest still asks (group.list)."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from dev_services import radicale_up as _radicale_up
from helpers.agent_calendar_v2 import _allow, _ctx, _identity
from helpers.calendar import _connect
from helpers.carddav import ALICE, BOB, _mkbook, _put
from helpers.people_groups import _msg
from sqlalchemy import select

from mailr.agent.tools import run_tool
from mailr.contacts import carddav, vcard
from mailr.db import sessionmaker
from mailr.models import Connector, Person

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")

CLUB = """BEGIN:VCARD\r
VERSION:4.0\r
UID:club-uid\r
KIND:group\r
FN:Book club\r
MEMBER:urn:uuid:alice-uid\r
MEMBER:mailto:carol@ab.example\r
END:VCARD\r
"""
APPLE = """BEGIN:VCARD\r
VERSION:3.0\r
UID:apple-group\r
X-ADDRESSBOOKSERVER-KIND:group\r
FN:Neighbours\r
X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:bob-uid\r
END:VCARD\r
"""


def test_vcard_parser_reads_both_versions():
    a = vcard.parse(ALICE)
    assert a["fn"] == "Alice Smith" and a["emails"] == [{"email": "alice@ab.example", "type": "work"}]
    assert a["organization"] == "Northwind Traders, Harbor Logistics" and a["title"] == "Operations Manager"
    assert a["birthday"] == "1980-02-14" and a["categories"] == ["Board"]
    assert a["addresses"][0]["text"] == "100 Main St, Anytown, ST 00000, USA"
    b = vcard.parse(BOB)
    assert b["emails"][0]["email"] == "bob@ab.example" and b["phones"] == [{"value": "(202) 555-0102", "type": "work"}]
    assert b["categories"] == ["Board", "Friends"]
    assert vcard.parse(CLUB)["kind"] == "group" and vcard.parse(CLUB)["members"] == ["alice-uid", "carol@ab.example"]
    assert vcard.parse(APPLE)["kind"] == "group" and vcard.parse(APPLE)["members"] == ["bob-uid"]
    assert vcard.parse("BEGIN:VCARD\nVERSION:3.0\nFN:X\nBDAY:--0315\nEND:VCARD")["birthday"] == "--03-15"


async def test_sync_merges_people_and_groups_read_only(client, user, monkeypatch):
    """contacts.sync: first sync, merge rules, groups, incremental sync, removal; only reads are sent."""
    username = await _connect(client)
    book = _mkbook(username)
    for name, card in (("alice", ALICE), ("bob", BOB), ("club", CLUB), ("apple", APPLE)):
        _put(book, username, name, card)
    # Alice is already known from mail under another name and an org taken from her domain.
    await _msg(user.id, sent=False, frm="alice@ab.example", when=datetime.now(UTC) - timedelta(days=1))
    async with sessionmaker()() as db:
        p = (await db.execute(select(Person).where(Person.user_id == user.id, Person.email == "alice@ab.example"))).scalar_one()
        p.name = "alice s"
        await db.commit()

    methods: list[str] = []
    orig = carddav.CardDavClient.request

    async def spy(self, method, url, **kw):
        methods.append(method)
        return await orig(self, method, url, **kw)

    monkeypatch.setattr(carddav.CardDavClient, "request", spy)

    r = await client.post("/api/v1/address-books/sync")
    assert r.status_code == 200, r.text
    [st] = r.json()
    assert st["available"] is True and st["cards"] == 2 and st["groups"] == 4, st
    assert [b["name"] for b in st["books"]] == ["Contacts"]

    a = (await client.get("/api/v1/people/alice@ab.example")).json()
    assert a["name"] == "Alice Smith" and a["organization"] == "Northwind Traders, Harbor Logistics"
    assert a["title"] == "Operations Manager" and a["birthday"] == "1980-02-14"
    assert a["phones"][0]["value"] == "+1 202 555 0101" and a["phones"][0]["source"] == "carddav"
    assert a["addresses"][0]["city"] == "Anytown"
    assert a["sources"] == {"name": "carddav", "organization": "carddav", "title": "carddav", "birthday": "carddav"}
    assert a["received_count"] == 1  # the mail counts are kept
    # Bob had no mail: the address book adds him to People.
    b = (await client.get("/api/v1/people", params={"q": "bob@"})).json()
    assert b[0]["name"] == "Bob Jones" and b[0]["strength"] == 0

    groups = {g["name"]: g for g in (await client.get("/api/v1/groups")).json()}
    assert set(groups) == {"Book club", "Neighbours", "Board", "Friends"}
    assert [m["email"] for m in groups["Book club"]["members"]] == ["alice@ab.example", "carol@ab.example"]
    assert [m["email"] for m in groups["Neighbours"]["members"]] == ["bob@ab.example"]
    assert [m["email"] for m in groups["Board"]["members"]] == ["alice@ab.example", "bob@ab.example"]
    assert all(g["source"] == "carddav" and g["read_only"] for g in groups.values())
    # Address-book groups are read-only in mailr.
    gid = groups["Board"]["id"]
    assert (await client.patch(f"/api/v1/groups/{gid}", json={"name": "Mine"})).status_code == 409
    assert (await client.delete(f"/api/v1/groups/{gid}")).status_code == 409

    # Mail with another display name does not replace the address-book name.
    await _msg(user.id, sent=False, frm="alice@ab.example")
    async with sessionmaker()() as db:
        x = (await db.execute(select(Person).where(Person.user_id == user.id, Person.email == "alice@ab.example"))).scalar_one()
        assert x.name == "Alice Smith"
        conn = (await db.execute(select(Connector).where(Connector.user_id == user.id, Connector.kind == "caldav"))).scalar_one()
        cid = conn.id

    # Changes upstream: Alice's title changes, Bob's card goes, Carol gets a card.
    _put(book, username, "alice", ALICE.replace("Operations Manager", "Director"))
    assert httpx.delete(f"{book}bob.vcf", auth=(username, "x")).status_code in (200, 204)  # noqa: ASYNC210
    _put(book, username, "carol", "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:carol-uid\r\nFN:Carol King\r\n"
                                  "EMAIL:carol@ab.example\r\nEND:VCARD\r\n")
    methods.clear()
    st = await carddav.sync_connector(cid)
    assert st["cards"] == 2 and st["available"]
    assert "REPORT" in methods  # sync-collection with the stored token
    a = (await client.get("/api/v1/people/alice@ab.example")).json()
    assert a["title"] == "Director"
    async with sessionmaker()() as db:
        bob = (await db.execute(select(Person).where(Person.user_id == user.id, Person.email == "bob@ab.example"))).scalar_one()
        assert bob.phones == [] and "name" not in (bob.provenance or {}) and bob.organization == "ab"
    groups = {g["name"]: g for g in (await client.get("/api/v1/groups")).json()}
    assert "Friends" not in groups and [m["email"] for m in groups["Board"]["members"]] == ["alice@ab.example"]
    assert groups["Neighbours"]["members"] == []

    # Nothing but reads ever went to the server.
    assert methods and set(methods) <= {"PROPFIND", "REPORT", "OPTIONS"}, methods


async def test_no_address_book_is_reported_unavailable(client, user):
    await _connect(client)
    r = await client.post("/api/v1/address-books/sync")
    [st] = r.json()
    assert st["cards"] == 0 and st["groups"] == 0


async def test_group_of_guests_in_an_invitation_still_asks_for_new_guests(client, user):
    """group.list: "group:<name>" in attendees expands to the members; with calendar.* allowed, a
    member never written to still turns the invitation into an approval (hard rule)."""
    await _connect(client)
    await _identity(user)
    await _msg(user.id, sent=True, to=["known@guest.example"])
    await client.post("/api/v1/groups", json={"name": "Lab", "members": ["known@guest.example", f"new-{uuid.uuid4().hex[:4]}@guest.example"]})
    await _allow(user.id, "calendar.*")
    start = (datetime.now(UTC) + timedelta(days=3)).replace(hour=15, minute=0, second=0, microsecond=0)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = await run_tool(ctx, "create_event", {"summary": "Lab meeting", "start": start.isoformat(),
                                                   "attendees": ["group:Lab"]})
        assert res.pending and res.pending["action"] == "calendar.invite"
        assert res.pending["preview"]["reason"].startswith("hard rule")
        lines = " ".join(res.pending["preview"]["lines"])
        assert "known@guest.example" in lines and "new-" in lines
