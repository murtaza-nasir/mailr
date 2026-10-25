# ACT-SURFACES: api
"""Imports (core/imports.py, api/import_routes.py) and calendar links (calendar/webcal.py):
note.import_keep, event.import_ics, contacts.import_vcf, import.undo, calendar.subscribe and
calendar.unsubscribe. Each import previews its counts first, writes nothing then, and one Undo takes
it back."""

import io
import json
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.calendar_collections import _connect

from mailr.calendar import webcal
from mailr.core import imports

API = "/api/v1"


async def _upload(client, name: str, data: bytes, ctype: str = "application/octet-stream") -> str:
    r = await client.post(f"{API}/uploads", files={"file": (name, data, ctype)})
    assert r.status_code == 200, r.text
    return r.json()["blob"]


def _keep_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Takeout/Keep/Shopping.json", json.dumps({
            "title": "Shopping", "color": "YELLOW", "isPinned": True, "isArchived": False, "isTrashed": False,
            "listContent": [{"text": "Milk", "isChecked": False}, {"text": "Eggs", "isChecked": True}],
            "labels": [{"name": "Home"}]}))
        z.writestr("Takeout/Keep/Shopping.html", "<html><div class='title'>Shopping</div></html>")
        z.writestr("Takeout/Keep/Idea.json", json.dumps({
            "title": "Idea", "textContent": "Paint the fence", "color": "DEFAULT", "isArchived": True,
            "isTrashed": False, "labels": [{"name": "Home"}, {"name": "Garden"}],
            "annotations": [{"url": "https://example.com/paint"}]}))
        z.writestr("Takeout/Keep/Old.json", json.dumps({"title": "Old", "textContent": "gone", "isTrashed": True}))
        z.writestr("Takeout/Keep/Recipe.html",
                   "<html><body><div class='note'><div class='title'>Recipe</div>"
                   "<div class='content'>Flour<br>Water</div>"
                   "<div class='chips'><span class='chip label'><span class='label-name'>Kitchen</span></span></div>"
                   "</div></body></html>")
        z.writestr("Takeout/Keep/photo.png", b"\x89PNG not imported")
        z.writestr("Takeout/Keep/Labels.txt", "Home\nGarden")
    return buf.getvalue()


def test_keep_reading():
    notes, extra = imports.read_keep([imports.SourceFile("takeout.zip", _keep_zip())])
    by = {n.title: n for n in notes}
    assert set(by) == {"Shopping", "Idea", "Old", "Recipe"}
    assert by["Shopping"].kind == "checklist" and by["Shopping"].items[1] == {"text": "Eggs", "checked": True}
    assert by["Shopping"].color == "banana" and by["Shopping"].pinned
    assert by["Idea"].archived and "https://example.com/paint" in by["Idea"].text
    assert by["Old"].trashed
    assert by["Recipe"].text == "Flour\nWater" and by["Recipe"].labels == ["Kitchen"]
    assert extra["other_files"] == 2


def test_zip_limits():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("big.json", b"x" * (imports.MEMBER_MAX + 1))
    got, skipped = imports.expand([imports.SourceFile("a.zip", buf.getvalue())], (".json",))
    assert got == [] and skipped == 1
    with pytest.raises(Exception):  # noqa: B017
        imports.expand([imports.SourceFile("a.zip", b"PK\x03\x04broken")], (".json",))


async def test_keep_preview_import_and_undo(client):
    """ACT note.import_keep, ACT import.undo"""
    blob = await _upload(client, "takeout.zip", _keep_zip(), "application/zip")
    pv = await client.post(f"{API}/imports/preview", json={"kind": "keep", "blobs": [blob]})
    assert pv.status_code == 200, pv.text
    c = pv.json()["counts"]
    assert (c["notes"], c["checklists"], c["trashed"], c["archived"], c["labels"]) == (2, 1, 1, 1, 3)
    # The preview wrote nothing.
    assert not [n for n in (await client.get(f"{API}/notes", params={"view": "all"})).json().get("notes", [])
                if n["title"] in ("Shopping", "Idea")]
    r = await client.post(f"{API}/imports", json={"kind": "keep", "blobs": [blob]})
    assert r.status_code == 200, r.text
    batch = r.json()
    assert batch["counts"]["checklists"] == 1 and batch["counts"]["notes"] == 2
    labels = {lb["name"] for lb in (await client.get(f"{API}/notes/labels")).json()["labels"]}
    assert {"Home", "Garden", "Kitchen"} <= labels
    listed = (await client.get(f"{API}/imports", params={"kind": "keep"})).json()["items"]
    assert listed[0]["id"] == batch["id"]
    u = await client.post(f"{API}/imports/{batch['id']}/undo")
    assert u.status_code == 200, u.text
    assert u.json()["undone"]["notes"] == 3 and u.json()["undone"]["labels"] == 3
    labels = {lb["name"] for lb in (await client.get(f"{API}/notes/labels")).json()["labels"]}
    assert not {"Home", "Garden", "Kitchen"} & labels
    assert (await client.post(f"{API}/imports/{batch['id']}/undo")).status_code == 409


VCF = """BEGIN:VCARD
VERSION:3.0
FN:Marco Example
EMAIL:marco-{t}@example.com
TEL;TYPE=CELL:+1 555 0100
ORG:Example School
BDAY:1990-04-02
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:No Address
TEL:+1 555 0101
END:VCARD
BEGIN:VCARD
VERSION:4.0
FN:Known Person
EMAIL:known-{t}@example.com
TITLE:Coach
END:VCARD
"""


async def test_vcf_preview_import_and_undo(client, user):
    """ACT contacts.import_vcf, ACT import.undo"""
    from mailr.db import sessionmaker
    from mailr.models import Person

    t = uuid.uuid4().hex[:8]
    async with sessionmaker()() as db:
        db.add(Person(user_id=user.id, email=f"known-{t}@example.com", name="Known Person", received_count=4,
                      sent_count=1, notes="", blocked=False, strength=0.0, phones=[], addresses=[], provenance={},
                      title="", birthday=""))
        await db.commit()
    blob = await _upload(client, "contacts.vcf", VCF.format(t=t).encode(), "text/vcard")
    pv = (await client.post(f"{API}/imports/preview", json={"kind": "vcf", "blobs": [blob]})).json()
    assert pv["counts"]["people"] == 1 and pv["counts"]["existing"] == 1 and pv["counts"]["no_email"] == 1
    batch = (await client.post(f"{API}/imports", json={"kind": "vcf", "blobs": [blob]})).json()
    assert batch["counts"]["people"] == 1 and batch["counts"]["updated"] == 1
    async with sessionmaker()() as db:
        from sqlalchemy import select

        marco = (await db.execute(select(Person).where(Person.user_id == user.id,
                                                       Person.email == f"marco-{t}@example.com"))).scalar_one()
        assert marco.organization == "Example School" and marco.birthday == "1990-04-02"
        assert marco.phones[0]["value"] == "+1 555 0100"
        known = (await db.execute(select(Person).where(Person.user_id == user.id,
                                                       Person.email == f"known-{t}@example.com"))).scalar_one()
        assert known.title == "Coach" and known.received_count == 4
    u = (await client.post(f"{API}/imports/{batch['id']}/undo")).json()
    assert u["undone"]["people"] == 1
    async with sessionmaker()() as db:
        from sqlalchemy import select

        assert (await db.execute(select(Person).where(Person.user_id == user.id,
                                                      Person.email == f"marco-{t}@example.com"))).first() is None
        known = (await db.execute(select(Person).where(Person.user_id == user.id,
                                                       Person.email == f"known-{t}@example.com"))).scalar_one()
        assert known.title == "" and known.name == "Known Person" and known.received_count == 4


def _ics(uid_a: str, uid_b: str) -> str:
    d = (date.today() + timedelta(days=10)).strftime("%Y%m%d")
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example//EN\r\nX-WR-CALNAME:School terms\r\n"
            f"BEGIN:VEVENT\r\nUID:{uid_a}\r\nDTSTAMP:20260101T000000Z\r\nDTSTART;VALUE=DATE:{d}\r\n"
            "SUMMARY:Term starts\r\nEND:VEVENT\r\n"
            f"BEGIN:VEVENT\r\nUID:{uid_b}\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:{d}T090000Z\r\n"
            f"DTEND:{d}T100000Z\r\nSUMMARY:Parents evening\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


@pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")
async def test_ics_preview_import_and_undo(client):
    """ACT event.import_ics, ACT import.undo"""
    await _connect(client, "Family")
    a, b = f"a-{uuid.uuid4().hex}@example.com", f"b-{uuid.uuid4().hex}@example.com"
    blob = await _upload(client, "terms.ics", _ics(a, b).encode(), "text/calendar")
    pv = (await client.post(f"{API}/imports/preview", json={"kind": "ics", "blobs": [blob]})).json()
    assert pv["counts"]["events"] == 2 and pv["counts"]["existing"] == 0 and pv["calendar"]["name"]
    batch = (await client.post(f"{API}/imports", json={"kind": "ics", "blobs": [blob]})).json()
    assert batch["counts"]["events"] == 2
    again = (await client.post(f"{API}/imports/preview", json={"kind": "ics", "blobs": [blob]})).json()
    assert again["counts"]["existing"] == 2
    assert (await client.get(f"{API}/calendar/events/{a}")).status_code == 200
    u = (await client.post(f"{API}/imports/{batch['id']}/undo")).json()
    assert u["undone"]["events"] == 2
    assert (await client.get(f"{API}/calendar/events/{a}")).status_code == 404


async def test_imports_refuse_tokens_and_bad_kinds(client):
    r = await client.post(f"{API}/imports/preview", json={"kind": "csv", "blobs": ["0" * 32]})
    assert r.status_code == 422
    tok = (await client.post(f"{API}/tokens", json={"name": "imp", "scopes": ["read", "draft"]})).json()["token"]
    r = await client.post(f"{API}/imports", json={"kind": "keep", "blobs": ["0" * 32]},
                          headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403


# ---------------- calendar links ----------------


def test_webcal_links_are_normalized():
    assert webcal.normalize("webcal://example.com/holidays.ics") == "https://example.com/holidays.ics"
    with pytest.raises(ValueError):
        webcal.normalize("ftp://example.com/x.ics")
    with pytest.raises(ValueError):
        webcal.parse(b"<html>not a calendar</html>")


async def test_subscribe_refresh_and_remove(client, monkeypatch):
    """ACT calendar.subscribe, ACT calendar.unsubscribe"""
    a, b = f"a-{uuid.uuid4().hex}@example.com", f"b-{uuid.uuid4().hex}@example.com"
    body = {"v": _ics(a, b).encode()}
    calls: list[dict] = []

    async def fake_fetch(url, *, private_ok, etag=None, modified=None):
        calls.append({"url": url, "etag": etag})
        if etag == "e2":
            return 304, b"", {}
        return 200, body["v"], {"etag": "e2" if body["v"] != _ics(a, b).encode() else "e1"}

    monkeypatch.setattr(webcal, "fetch", fake_fetch)
    r = await client.post(f"{API}/calendar/subscriptions", json={"url": "webcal://example.com/terms.ics"})
    assert r.status_code == 200, r.text
    sub = r.json()
    assert sub["events"] == 2 and calls[0]["url"] == "https://example.com/terms.ics"
    cal = sub["calendar"]
    assert cal["read_only"] and cal["name"] == "School terms" and cal["subscription"]["url"].startswith("https://")
    cols = (await client.get(f"{API}/calendar/calendars")).json()
    # A calendar link alone counts as a calendar for the views (the empty "not set up" page goes).
    assert (await client.get(f"{API}/calendar/agenda")).json()["connected"] is True
    assert any(c.get("calendar_id") == cal["calendar_id"] for c in cols)
    # Read-only: no edits, no deletes.
    assert (await client.delete(f"{API}/calendar/events/{a}")).status_code >= 400
    assert (await client.get(f"{API}/calendar/events/{a}")).status_code == 200
    # The file changes: one event goes, the next fetch applies it; then 304 changes nothing.
    body["v"] = _ics(a, b).replace(f"UID:{b}", f"UID:c-{b}").encode()
    out = (await client.post(f"{API}/calendar/subscriptions/{sub['connector_id']}/refresh")).json()
    assert out["error"] is None and out["removed"] == 1
    out = (await client.post(f"{API}/calendar/subscriptions/{sub['connector_id']}/refresh")).json()
    assert "removed" not in out
    listed = (await client.get(f"{API}/calendar/subscriptions")).json()["items"]
    assert [s["connector_id"] for s in listed] == [sub["connector_id"]]
    # Not an account.
    assert sub["connector_id"] not in [c["id"] for c in (await client.get(f"{API}/connectors")).json()]
    assert (await client.delete(f"{API}/calendar/subscriptions/{sub['connector_id']}")).json()["ok"]
    assert (await client.get(f"{API}/calendar/events/{a}")).status_code == 404
    assert (await client.get(f"{API}/calendar/subscriptions")).json()["items"] == []


async def test_subscribe_refuses_private_and_bad_links(client):
    r = await client.post(f"{API}/calendar/subscriptions", json={"url": "https://127.0.0.1/x.ics"})
    assert r.status_code == 400
    r = await client.post(f"{API}/calendar/subscriptions", json={"url": "file:///etc/passwd"})
    assert r.status_code == 400


def _feed(n: int, *, stamp: str = "20261001T000000Z", series: bool = True) -> bytes:
    """A calendar link's file: n single events (one all-day, one with a VALARM) and a weekly series."""
    d = (datetime.now(UTC).date() + timedelta(days=20)).strftime("%Y%m%d")
    ev = []
    for i in range(n):
        when = f"DTSTART;VALUE=DATE:{d}\r\n" if i == 0 else f"DTSTART:{d}T{9 + i % 8:02d}0000Z\r\nDTEND:{d}T{10 + i % 8:02d}0000Z\r\n"
        alarm = "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT5M\r\nEND:VALARM\r\n" if i == 1 else ""
        ev.append(f"BEGIN:VEVENT\r\nUID:bulk-{i}@feed.example\r\nDTSTAMP:{stamp}\r\n{when}SUMMARY:Event {i}\r\n"
                  f"LOCATION:Room {i}\r\nTRANSP:{'TRANSPARENT' if i % 5 == 0 else 'OPAQUE'}\r\n{alarm}END:VEVENT\r\n")
    if series:
        ev.append(f"BEGIN:VEVENT\r\nUID:bulk-series@feed.example\r\nDTSTAMP:{stamp}\r\nDTSTART:{d}T070000Z\r\n"
                  "DTEND:{d}T080000Z\r\nRRULE:FREQ=WEEKLY;COUNT=5\r\nSUMMARY:Weekly\r\nEND:VEVENT\r\n".replace("{d}", d))
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\nX-WR-CALNAME:Bulk\r\n" + "".join(ev) + "END:VCALENDAR\r\n").encode()


async def test_a_large_calendar_link_is_written_in_bulk(client, user, monkeypatch):
    """Subscribing to (and refreshing) a calendar link writes its events in bulk: a few statements per
    500 events, not a dozen per event (test audit, 13 Oct 2026: 9,000 events took about 95 s in one
    request). The rows are the ones the one-at-a-time path (mirror.upsert_object) writes."""
    from sqlalchemy import func, select

    from mailr.db import sessionmaker
    from mailr.models import CalCollection, CalObject, CalOccurrence
    from tests.scale_fixture import counting

    n = 600
    body = {"v": _feed(n)}

    async def fake_fetch(url, **kw):
        return 200, body["v"], {}

    monkeypatch.setattr(webcal, "fetch", fake_fetch)
    with counting() as c:
        r = await client.post(f"{API}/calendar/subscriptions", json={"url": "https://feed.example/bulk.ics"})
    assert r.status_code == 200, r.text
    sub = r.json()
    assert sub["events"] == n + 1
    # Before: 13 statements per event (7,900 here). Now: the request's own, a few per chunk of 500,
    # and the series through upsert_object.
    assert c.sql < 120, c.sql
    async with sessionmaker()() as db:
        coll = (await db.execute(select(CalCollection).where(CalCollection.connector_id == sub["connector_id"]))).scalar_one()
        coll_id, coll_href = coll.id, coll.href
        assert await db.scalar(select(func.count()).select_from(CalObject).where(
            CalObject.collection_id == coll.id, CalObject.deleted_at.is_(None))) == n + 1
        assert await db.scalar(select(func.count()).select_from(CalOccurrence).where(
            CalOccurrence.collection_id == coll.id)) == n + 5
        revs = (await db.execute(select(CalObject.rev).where(CalObject.collection_id == coll.id))).scalars().all()
        assert len(set(revs)) == len(revs) and all(revs)
        await _same_as_one_at_a_time(db, user, coll_id, coll_href)

    # A refresh where every event changed (a new DTSTAMP, as many feeds send on each fetch) and a
    # third of them are gone: in bulk too.
    body["v"] = _feed(400, stamp="20261002T000000Z")
    with counting() as c:
        out = (await client.post(f"{API}/calendar/subscriptions/{sub['connector_id']}/refresh")).json()
    assert out["error"] is None and out["changed"] == 401 and out["removed"] == 200, out
    assert c.sql < 120, c.sql
    async with sessionmaker()() as db:
        assert await db.scalar(select(func.count()).select_from(CalObject).where(
            CalObject.collection_id == coll_id, CalObject.deleted_at.is_(None))) == 401
        assert await db.scalar(select(func.count()).select_from(CalOccurrence).where(
            CalOccurrence.collection_id == coll_id)) == 400 + 5
        # Rows updated in bulk equal new rows written one at a time, too.
        await _same_as_one_at_a_time(db, user, coll_id, coll_href)
    # The same file again: nothing to write.
    out = (await client.post(f"{API}/calendar/subscriptions/{sub['connector_id']}/refresh")).json()
    assert out["changed"] == 0 and out["removed"] == 0, out


async def _same_as_one_at_a_time(db, user, coll_id: int, coll_href: str) -> None:
    """A few rows of the collection equal the ones mirror.upsert_object writes for the same
    resources in a scratch collection (rolled back), with the same occurrences."""
    from sqlalchemy import select

    from mailr.calendar import mirror
    from mailr.calendar.writer import _ctx
    from mailr.models import CalCollection, CalObject, CalOccurrence

    coll = await db.get(CalCollection, coll_id)
    scratch = CalCollection(user_id=user.id, connector_id=coll.connector_id, href=coll_href + "-scratch",
                            displayname="scratch", description="", components=["VEVENT"], read_only=True,
                            busy=False, notify=False)
    db.add(scratch)
    await db.flush()
    user_tz, me = await _ctx(db, user.id)
    # expanded_from and _until follow the clock (a series is expanded from now).
    skip = {"id", "collection_id", "href", "rev", "fetched_at", "updated_at", "created_at", "expanded_from",
            "expanded_until"}
    cols = [c.key for c in CalObject.__table__.columns if c.key not in skip]
    occ_cols = [c.key for c in CalOccurrence.__table__.columns if c.key not in {"id", "object_id", "collection_id"}]
    uids = ("bulk-0@feed.example", "bulk-1@feed.example", "bulk-5@feed.example", "bulk-series@feed.example")
    bulk = (await db.execute(select(CalObject).where(CalObject.collection_id == coll_id, CalObject.uid.in_(uids),
                                                     CalObject.deleted_at.is_(None)))).scalars().all()
    assert len(bulk) == len(uids)
    for b in bulk:
        one = await mirror.upsert_object(db, scratch, b.href.replace(coll_href, scratch.href), b.etag, b.ics,
                                         user_tz=user_tz, me=me)
        rows = [{k: getattr(o, k if k != "class" else "class_") for k in cols} for o in (one, b)]
        assert rows[0] == rows[1], b.uid
        occs = [sorted(tuple(r) for r in (await db.execute(select(*[getattr(CalOccurrence, k) for k in occ_cols])
                                                            .where(CalOccurrence.object_id == oid))).all())
                for oid in (one.id, b.id)]
        assert occs[0] == occs[1] and occs[0], b.uid
    await db.rollback()
