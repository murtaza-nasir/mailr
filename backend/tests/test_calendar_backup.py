"""Calendar and contact backups: the daily export
(calendar/backup.py), its readiness check and heartbeat (core/health.py), the restore into the
built-in store and the forced resync (calendar/restore.py).

The restore runs against a small in-memory WebDAV server behind httpx's MockTransport, so the real
DavClient code is used. tests/test_calendar_backup_stalwart.py does the same against a real
Stalwart (opt-in).
"""

from __future__ import annotations

import json
import os
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
import pytest
from icalendar import Calendar
from sqlalchemy import select, text

from mailr.calendar import backup, builtin, restore
from mailr.config import get_settings
from mailr.contacts import vcard
from mailr.core import backup as mail_backup
from mailr.core import health
from mailr.db import sessionmaker
from mailr.models import (
    AddressBook,
    CalCollection,
    CalObject,
    Connector,
    ContactCard,
    GroupCalendar,
    Job,
    User,
)

STORE = "http://stalwart.test"

VTZ = """BEGIN:VTIMEZONE\r
TZID:America/Chicago\r
BEGIN:STANDARD\r
DTSTART:19701101T020000\r
TZOFFSETFROM:-0500\r
TZOFFSETTO:-0600\r
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\r
END:STANDARD\r
BEGIN:DAYLIGHT\r
DTSTART:19700308T020000\r
TZOFFSETFROM:-0600\r
TZOFFSETTO:-0500\r
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\r
END:DAYLIGHT\r
END:VTIMEZONE\r
"""


def _event(uid: str, summary: str, *, rrule: bool = False, override: bool = False) -> str:
    body = (f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261001T120000Z\r\nSUMMARY:{summary}\r\n"
            "DTSTART;TZID=America/Chicago:20261012T090000\r\nDTEND;TZID=America/Chicago:20261012T100000\r\n"
            + ("RRULE:FREQ=WEEKLY;COUNT=5\r\n" if rrule else "") + "END:VEVENT\r\n")
    if override:
        body += (f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261001T120000Z\r\nSUMMARY:{summary} (moved)\r\n"
                 "RECURRENCE-ID;TZID=America/Chicago:20261019T090000\r\n"
                 "DTSTART;TZID=America/Chicago:20261019T110000\r\nDTEND;TZID=America/Chicago:20261019T120000\r\n"
                 "END:VEVENT\r\n")
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n{VTZ}{body}END:VCALENDAR\r\n"


def _task(uid: str, summary: str) -> str:
    return (f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\nBEGIN:VTODO\r\nUID:{uid}\r\n"
            f"DTSTAMP:20261001T120000Z\r\nSUMMARY:{summary}\r\nDUE;VALUE=DATE:20261020\r\nEND:VTODO\r\nEND:VCALENDAR\r\n")


def _card(uid: str, fn: str, email: str) -> str:
    return f"BEGIN:VCARD\r\nVERSION:4.0\r\nUID:{uid}\r\nFN:{fn}\r\nEMAIL:{email}\r\nEND:VCARD\r\n"


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(s, "backup_target", "")
    monkeypatch.setattr(s, "stalwart_url", STORE)
    return tmp_path


async def _builtin(user: User, account: str) -> Connector:
    async with sessionmaker()() as db:
        conn = Connector(user_id=user.id, kind="caldav", name=builtin.NAME, mode="mirror", config={"account": account},
                         state={}, enabled=True, builtin=True, status="idle")
        db.add(conn)
        await db.commit()
        return conn


async def _collection(conn: Connector, path: str, name: str, objects: list[tuple[str, str, str]], **kw) -> int:
    """A mirrored collection with (resource name, kind, ics) objects."""
    async with sessionmaker()() as db:
        coll = CalCollection(user_id=conn.user_id, connector_id=conn.id, href=STORE + path, displayname=name,
                             components=kw.pop("components", ["VEVENT", "VTODO"]), **kw)
        db.add(coll)
        await db.flush()
        for rname, kind, ics in objects:
            uid = backup.uid_of(ics)
            db.add(CalObject(user_id=conn.user_id, connector_id=conn.id, collection_id=coll.id, href=STORE + path + rname,
                             etag=f'"{uuid.uuid4().hex[:8]}"', uid=uid, kind=kind, ics=ics, summary=uid))
        await db.commit()
        return coll.id


async def _book(conn: Connector, path: str, name: str, cards: list[tuple[str, str | None]]) -> int:
    async with sessionmaker()() as db:
        b = AddressBook(user_id=conn.user_id, connector_id=conn.id, href=STORE + path, displayname=name)
        db.add(b)
        await db.flush()
        for rname, raw in cards:
            parsed = vcard.parse(raw)
            db.add(ContactCard(user_id=conn.user_id, connector_id=conn.id, book_id=b.id, href=STORE + path + rname,
                               etag='"1"', uid=parsed["uid"], kind=parsed["kind"], data=parsed,
                               vcard=raw if rname.startswith("keep") else None))
        await db.commit()
        return b.id


def _snapshot(owner_dir: Path) -> Path:
    name = backup.latest(owner_dir)
    assert name is not None
    return owner_dir / name


# ---------------- the export ----------------


async def test_export_contents_round_trip(app, user, dirs):
    """Built-in and external (local_only) accounts, events with a recurrence override and zones, a
    task, address books with a kept card and an old parsed-only card; a group calendar is left out
    of the user's export."""
    acct = f"u{uuid.uuid4().hex[:6]}@example.test"
    conn = await _builtin(user, acct)
    home = f"/dav/cal/{acct.replace('@', '%40')}/"
    await _collection(conn, home + "default/", "Calendar", [
        ("a.ics", "event", _event("ev-a", "Standup", rrule=True, override=True)),
        ("b.ics", "event", _event("ev-b", "Review")),
        ("t.ics", "task", _task("task-1", "File the report"))], color="#2E7D32")
    await _collection(conn, "/dav/cal/grp-fam-abc%40example.test/fam/", "Family",
                      [("g.ics", "event", _event("grp-1", "Picnic"))])
    await _book(conn, f"/dav/card/{acct.replace('@', '%40')}/default/", "Contacts",
                [("keep-1.vcf", _card("card-1", "Ada Lovelace", "ada@northwind.example")),
                 ("old-2.vcf", _card("card-2", "Grace Hopper", "grace@northwind.example"))])
    async with sessionmaker()() as db:
        ext = Connector(user_id=user.id, kind="caldav", name="Work server", mode="mirror", data_class="local_only",
                        config={"url": "https://dav.northwind.example/"}, state={}, enabled=True, status="idle")
        db.add(ext)
        await db.commit()
    await _collection(ext, "/cal/work/", "Work", [("w.ics", "event", _event("work-1", "Board meeting"))])

    out = await backup.export_user(user.id)
    assert out["snapshot"] and out["counts"] == {"calendars": 2, "objects": 4, "address_books": 1, "cards": 2}
    snap = _snapshot(backup.export_dir(user.id))
    m = json.loads((snap / backup.MANIFEST).read_text())
    assert m["owner"]["email"] == user.email and m["owner"]["account"] == acct
    assert m["skipped_not_owned"] == 1  # the group calendar
    by_name = {c["name"]: c for c in m["collections"]}
    assert set(by_name) == {"Calendar", "Work"}
    cal = by_name["Calendar"]
    assert cal["kind"] == "builtin" and cal["slug"] == "default" and cal["color"] == "#2E7D32"
    assert {r["uid"]: r["name"] for r in cal["resources"]} == {"ev-a": "a.ics", "ev-b": "b.ics", "task-1": "t.ics"}
    work = by_name["Work"]
    assert work["kind"] == "external" and work["connector"]["data_class"] == "local_only"
    assert work["connector"]["url"] == "https://dav.northwind.example/"

    parsed = Calendar.from_ical((snap / cal["file"]).read_text())
    assert [str(z["tzid"]) for z in parsed.walk("VTIMEZONE")] == ["America/Chicago"]  # once
    events = parsed.walk("VEVENT")
    assert sorted(str(e["uid"]) for e in events) == ["ev-a", "ev-a", "ev-b"]
    assert any(e.get("recurrence-id") for e in events)
    assert [str(t["uid"]) for t in parsed.walk("VTODO")] == ["task-1"]
    assert "Picnic" not in (snap / cal["file"]).read_text()

    book = m["address_books"][0]
    assert book["cards"] == 2 and book["synthesized"] == 1
    vcf = (snap / book["file"]).read_text()
    cards = restore.split_cards(vcf)
    assert [u for u, _ in cards] == ["card-1", "card-2"]
    assert vcard.parse(cards[1][1])["emails"] == [{"email": "grace@northwind.example", "type": ""}]
    assert vcard.parse(cards[0][1])["fn"] == "Ada Lovelace"

    # The local marker, LATEST and no temporary folders.
    assert mail_backup.last_run(backup.export_dir(user.id)) is not None
    assert (backup.export_dir(user.id) / backup.LATEST).read_text() == snap.name
    assert not [p for p in backup.export_dir(user.id).iterdir() if p.name.startswith(".tmp-")]


async def test_same_content_writes_no_new_snapshot(app, user, dirs):
    acct = f"u{uuid.uuid4().hex[:6]}@example.test"
    conn = await _builtin(user, acct)
    await _collection(conn, f"/dav/cal/{acct.replace('@', '%40')}/default/", "Calendar",
                      [("a.ics", "event", _event("ev-a", "Standup"))])
    first = await backup.export_user(user.id)
    run1 = mail_backup.last_run(backup.export_dir(user.id))
    second = await backup.export_user(user.id)
    assert first["snapshot"] and second["snapshot"] is None
    assert backup.snapshots(backup.export_dir(user.id)) == [first["snapshot"]]
    assert mail_backup.last_run(backup.export_dir(user.id)) >= run1
    async with sessionmaker()() as db:  # a change makes a new one
        o = (await db.execute(select(CalObject).where(CalObject.user_id == user.id))).scalar_one()
        o.ics = _event("ev-a", "Standup (renamed)")
        await db.commit()
    third = await backup.export_user(user.id)
    assert third["snapshot"] and third["snapshot"] != first["snapshot"]
    async with sessionmaker()() as db:  # everything gone: an empty snapshot, so LATEST is right
        await db.execute(text("UPDATE cal_collections SET deleted_at = now() WHERE user_id = :u"), {"u": user.id})
        await db.commit()
    fourth = await backup.export_user(user.id)
    assert fourth["snapshot"] and backup.latest(backup.export_dir(user.id)) == fourth["snapshot"]
    m = json.loads((backup.export_dir(user.id) / fourth["snapshot"] / backup.MANIFEST).read_text())
    assert m["collections"] == []


async def test_group_calendar_exported_once(app, user, dirs):
    """Two members mirror the same group calendar: the group export holds it once, from the
    freshest mirror; neither user's export holds it."""
    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        other = await create_user(db, f"t-{uuid.uuid4().hex[:8]}@example.com", "Other", password="test-password-123")
    group = f"Fam {uuid.uuid4().hex[:6]}"
    path = f"/dav/cal/grp-{uuid.uuid4().hex[:6]}%40example.test/fam/"
    a = await _builtin(user, f"a{uuid.uuid4().hex[:6]}@example.test")
    b = await _builtin(other, f"b{uuid.uuid4().hex[:6]}@example.test")
    old = datetime.now(UTC) - timedelta(hours=3)
    await _collection(a, path, "Family", [("p.ics", "event", _event("picnic", "Picnic"))], last_sync_at=old)
    await _collection(b, path, "Family", [("p.ics", "event", _event("picnic", "Picnic")),
                                          ("z.ics", "event", _event("zoo", "Zoo"))], last_sync_at=datetime.now(UTC))
    async with sessionmaker()() as db:
        gc = GroupCalendar(group_name=group, name="Family", access="read", href=path, color="#123456",
                           editors=[other.id])
        db.add(gc)
        await db.commit()
    try:
        await backup.export_groups()
        snap = _snapshot(backup.export_dir(None))
        m = json.loads((snap / backup.MANIFEST).read_text())
        mine = [c for c in m["collections"] if c["group"] == group]
        assert len(mine) == 1
        e = mine[0]
        assert e["source"] == "mirror" and e["objects"] == 2 and e["access"] == "read"
        assert e["editors"] == [{"user_id": other.id, "email": other.email}]
        assert sorted(u for u, _ in restore.split_calendar((snap / e["file"]).read_text())) == ["picnic", "zoo"]
        for u in (user, other):  # nothing of their own: no snapshot, the run is still recorded
            assert (await backup.export_user(u.id))["snapshot"] is None
            assert backup.latest(backup.export_dir(u.id)) is None
            assert mail_backup.last_run(backup.export_dir(u.id)) is not None
    finally:
        async with sessionmaker()() as db:
            await db.execute(text("DELETE FROM group_calendars WHERE group_name = :g"), {"g": group})
            await db.commit()


async def test_target_copy_and_markers(app, user, dirs, monkeypatch):
    acct = f"u{uuid.uuid4().hex[:6]}@example.test"
    conn = await _builtin(user, acct)
    await _collection(conn, f"/dav/cal/{acct.replace('@', '%40')}/default/", "Calendar",
                      [("a.ics", "event", _event("ev-a", "Standup"))])
    nas = dirs / "nas"
    nas.mkdir()
    monkeypatch.setattr(get_settings(), "backup_target", str(nas))
    # Not mounted (no marker): nothing goes there, the run is still recorded locally.
    out = await backup.export_user(user.id)
    assert out["snapshot"] and out["copied"] is None
    assert not (nas / "calendars").exists()
    assert mail_backup.last_run(backup.export_dir(user.id)) is not None
    # Mounted: the newest snapshot is copied on the next run, though the content is unchanged.
    (nas / mail_backup.TARGET_MARKER).write_text("")
    out = await backup.export_user(user.id)
    assert out["snapshot"] is None and out["copied"] == backup.latest(backup.export_dir(user.id))
    tgt = backup.target_dir(user.id)
    assert (tgt / out["copied"] / backup.MANIFEST).is_file()
    assert (tgt / backup.LATEST).read_text() == out["copied"]
    assert mail_backup.last_run(tgt) is not None
    assert (await backup.export_user(user.id))["copied"] is None  # already there


def test_retention(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "calendar_export_keep_days", 3)
    now = datetime(2026, 10, 10, 12, tzinfo=UTC)
    names = ["2026-10-01T030000Z", "2026-10-08T030000Z", "2026-10-08T150000Z", "2026-10-09T030000Z",
             "2026-10-10T030000Z", "2026-10-10T110000Z"]
    for n in names:
        (tmp_path / n).mkdir()
        (tmp_path / n / backup.MANIFEST).write_text("{}")
    stale = tmp_path / ".tmp-2026-10-02T000000Z-ab"
    stale.mkdir()
    # Its age counts from its mtime, not the real clock: on 10 Oct 2026 itself a fresh mtime was
    # under the six hours and the folder stayed (the test failed on that day only).
    old = (now - timedelta(days=8)).timestamp()
    os.utime(stale, (old, old))
    gone = backup.prune(tmp_path, now)
    assert gone == ["2026-10-01T030000Z", "2026-10-08T030000Z", "2026-10-10T030000Z"]
    assert backup.snapshots(tmp_path) == ["2026-10-08T150000Z", "2026-10-09T030000Z", "2026-10-10T110000Z"]
    assert not (tmp_path / ".tmp-2026-10-02T000000Z-ab").exists()
    # The newest always stays, however old.
    monkeypatch.setattr(get_settings(), "calendar_export_keep_days", 1)
    backup.prune(tmp_path, now + timedelta(days=100))
    assert backup.snapshots(tmp_path) == ["2026-10-10T110000Z"]


def test_a_failed_write_leaves_no_snapshot(tmp_path, monkeypatch):
    def boom(p):
        raise OSError("disk full")

    monkeypatch.setattr(backup, "_fsync_file", boom)
    with pytest.raises(OSError):
        backup.store_snapshot(tmp_path, {"collections": [1], "address_books": []}, {"calendars/01-x.ics": "x"})
    assert backup.snapshots(tmp_path) == [] and list(tmp_path.iterdir()) == []


def test_uid_and_split_helpers():
    assert backup.uid_of("BEGIN:VEVENT\r\nUID:urn:uuid:12:34\r\nEND:VEVENT") == "urn:uuid:12:34"
    assert backup.uid_of("UID;X-P=1:folded-\r\n uid\r\n") == "folded-uid"
    text_, failed = backup.merge_calendar([_event("a", "A", rrule=True, override=True), _task("t", "T"), "garbage"])
    assert len(failed) == 1
    res = dict(restore.split_calendar(text_))
    assert set(res) == {"a", "t"}
    assert "VTIMEZONE" in res["a"] and "VTIMEZONE" not in res["t"]
    assert res["a"].index("RECURRENCE-ID") > res["a"].index("RRULE")
    assert restore.normalized(res["a"]) == restore.normalized(_event("a", "A", rrule=True, override=True))
    assert restore.normalized(res["a"]) != restore.normalized(_event("a", "B", rrule=True, override=True))


# ---------------- readiness and heartbeat ----------------


def test_evaluate_calendar_runs():
    now = datetime.now(UTC)
    fresh, stale = now - timedelta(hours=1), now - timedelta(hours=27)
    assert health.evaluate_calendar_runs([], None, now) == (True, "no calendars yet")
    assert health.evaluate_calendar_runs([fresh, None], None, now) == (False, "1 of 2 calendar exports not run in 26 h")
    assert health.evaluate_calendar_runs([fresh, stale], None, now)[0] is False
    assert health.evaluate_calendar_runs([fresh], [stale], now) == (False, "1 of 1 calendar export not copied in 26 h")
    assert health.evaluate_calendar_runs([fresh], [fresh], now) == (True, "1 calendar export in the last 26 h and copied")
    assert health.evaluate_calendar_runs([fresh, fresh], None, now) == (True, "2 calendar exports in the last 26 h")


async def test_calendar_export_check_and_ready(app, user, dirs, monkeypatch):
    owners = [user.id, None]

    async def with_data():
        return owners

    monkeypatch.setattr(backup, "owners_with_data", with_data)

    async def old_data(owners):  # data older than a day: no first-export grace (_stale_waiting)
        return [datetime.now(UTC) - timedelta(days=2) for _ in owners]

    monkeypatch.setattr(backup, "data_since", old_data)
    assert await health.check_calendar_export() == (False, "2 of 2 calendar exports not run in 26 h")
    await backup.export_user(user.id)
    await backup.export_groups()
    assert await health.check_calendar_export() == (True, "2 calendar exports in the last 26 h")
    # Stale: the readiness endpoint answers 503 and names the check.
    (backup.export_dir(None) / mail_backup.LAST_RUN).write_text((datetime.now(UTC) - timedelta(hours=30)).isoformat())
    health.clear_cache()
    res = await health.run_checks(("calendar_export",))
    assert res["ok"] is False and res["checks"]["calendar_export"]["detail"].startswith("1 of 2")
    assert health.summary(res) == ("down", "calendar_export")
    # With a mounted target the copies count too.
    nas = dirs / "nas"
    nas.mkdir()
    (nas / mail_backup.TARGET_MARKER).write_text("")
    monkeypatch.setattr(get_settings(), "backup_target", str(nas))
    await backup.export_groups()
    assert await health.check_calendar_export() == (False, "1 of 2 calendar exports not copied in 26 h")
    await backup.export_user(user.id)
    assert (await health.check_calendar_export())[0] is True


async def test_owners_with_data(app, user, dirs):
    assert user.id not in [o for o in await backup.owners_with_data() if o is not None]
    conn = await _builtin(user, f"u{uuid.uuid4().hex[:6]}@example.test")
    await _collection(conn, "/dav/cal/x/default/", "Calendar", [])
    assert user.id in await backup.owners_with_data()


async def test_backup_heartbeat_covers_the_calendar_export(monkeypatch):
    calls = []

    async def ok():
        return True, "fine"

    async def bad():
        return False, "1 of 1 calendar export not run in 26 h"

    async def send(kind, url, status, msg):
        calls.append((kind, status, msg))
        return True

    monkeypatch.setattr(get_settings(), "backup_heartbeat_url", "http://kuma.test/api/push/x")
    monkeypatch.setattr(get_settings(), "backup_target", "")
    monkeypatch.setattr(health, "send", send)
    monkeypatch.setattr(health, "CHECK_FUNCS", {**health.CHECK_FUNCS, "export": ok, "backup_target": ok,
                                                "calendar_export": bad, "notes_export": ok, "db_dump": ok})
    assert await health.backup_heartbeat() is False and calls == []
    monkeypatch.setattr(health, "CHECK_FUNCS", {**health.CHECK_FUNCS, "calendar_export": ok})
    assert await health.backup_heartbeat() is True
    assert calls == [("backup", "up", "export ok")]  # one heartbeat, the same message as before


# ---------------- restore ----------------


class FakeDav:
    """A tiny CalDAV/CardDAV server: collections are paths, resources (name → (etag, body))."""

    def __init__(self):
        self.cols: dict[str, dict[str, tuple[str, str]]] = {}
        self.methods: list[str] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = unquote(urlsplit(str(req.url)).path)
        m = req.method
        self.methods.append(m)
        col = path if path.endswith("/") else path.rsplit("/", 1)[0] + "/"
        if m in ("MKCALENDAR", "MKCOL"):
            if path in self.cols:
                return httpx.Response(405)
            self.cols[path] = {}
            return httpx.Response(201)
        if m == "PROPFIND":
            if path not in self.cols:
                return httpx.Response(404)
            rs = [(f"<d:response><d:href>{path}</d:href><d:propstat><d:prop><d:resourcetype><d:collection/>"
                   "</d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>")]
            for n, (etag, _b) in self.cols[path].items():
                rs.append(f"<d:response><d:href>{path}{n}</d:href><d:propstat><d:prop><d:getetag>{etag}</d:getetag>"
                          "<d:resourcetype/></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>")
            return httpx.Response(207, content=f'<d:multistatus xmlns:d="DAV:">{"".join(rs)}</d:multistatus>')
        if m == "REPORT":
            root = ET.fromstring(req.content)
            card = "carddav" in root.tag
            tag = "card:address-data" if card else "c:calendar-data"
            rs = []
            for h in root.iter("{DAV:}href"):
                p = unquote(h.text or "")
                n = p.rsplit("/", 1)[1]
                got = self.cols.get(col, {}).get(n)
                if got:
                    body = got[1].replace("&", "&amp;").replace("<", "&lt;")
                    rs.append(f"<d:response><d:href>{p}</d:href><d:propstat><d:prop><d:getetag>{got[0]}</d:getetag>"
                              f"<{tag}>{body}</{tag}></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>"
                              "</d:response>")
            ns = ('xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" '
                  'xmlns:card="urn:ietf:params:xml:ns:carddav"')
            return httpx.Response(207, content=f"<d:multistatus {ns}>{''.join(rs)}</d:multistatus>")
        n = path.rsplit("/", 1)[1]
        if m == "PUT":
            if col not in self.cols:
                return httpx.Response(409)
            if req.headers.get("if-none-match") == "*" and n in self.cols[col]:
                return httpx.Response(412)
            if req.headers.get("if-match") and self.cols[col].get(n, ("",))[0] != req.headers["if-match"]:
                return httpx.Response(412)
            etag = f'"{uuid.uuid4().hex[:8]}"'
            self.cols[col][n] = (etag, req.content.decode())
            return httpx.Response(201, headers={"ETag": etag})
        if m == "GET":
            got = self.cols.get(col, {}).get(n)
            return httpx.Response(200, content=got[1].encode(), headers={"ETag": got[0]}) if got else httpx.Response(404)
        return httpx.Response(405)


@pytest.fixture
def fake_store(monkeypatch, dirs):
    from mailr.contacts import carddav

    server = FakeDav()

    def client_for(conn, user):
        http = httpx.AsyncClient(transport=httpx.MockTransport(server.handler))
        return carddav.CardDavClient(STORE + "/dav/cal/", "x", "y", internal=True, http=http)

    monkeypatch.setattr(carddav, "client_for", client_for)
    return server


async def _jobs(kind: str, conn_id: int) -> list[Job]:
    async with sessionmaker()() as db:
        return list((await db.execute(select(Job).where(Job.kind == kind, Job.status == "queued",
                                                        Job.payload["connector_id"].astext == str(conn_id)))).scalars())


async def test_restore_is_idempotent_and_dry_run_changes_nothing(app, user, fake_store):
    acct = f"u{uuid.uuid4().hex[:6]}@example.test"
    conn = await _builtin(user, acct)
    q = acct.replace("@", "%40")
    await _collection(conn, f"/dav/cal/{q}/default/", "Calendar", [
        ("a.ics", "event", _event("ev-a", "Standup", rrule=True, override=True)),
        ("t.ics", "task", _task("task-1", "Report"))])
    await _collection(conn, f"/dav/cal/{q}/projects-1a/", "Projects", [("p.ics", "event", _event("ev-p", "Kickoff"))],
                      color="#AA0000", components=["VEVENT"])
    await _book(conn, f"/dav/card/{q}/default/", "Contacts", [("keep-1.vcf", _card("card-1", "Ada", "ada@northwind.example"))])
    async with sessionmaker()() as db:
        ext = Connector(user_id=user.id, kind="caldav", name="Work", mode="mirror", config={"url": "https://x.example/"},
                        state={}, enabled=True, status="idle")
        db.add(ext)
        await db.commit()
    await _collection(ext, "/cal/work/", "Work", [("w.ics", "event", _event("work-1", "Board"))])
    await backup.export_user(user.id)
    src = backup.export_dir(user.id)
    # The store lost everything but the default calendar, which still holds one event.
    fake_store.cols = {f"/dav/cal/{acct}/default/": {}, f"/dav/card/{acct}/": {}}

    dry = await restore.restore(src, dry_run=True)
    assert not dry.errors
    assert (dry.total.collections_created, dry.total.collections_existing, dry.total.created) == (2, 1, 4)
    assert dry.total.not_restored == 1
    assert {m for m in fake_store.methods} <= {"PROPFIND", "REPORT"}
    assert fake_store.cols[f"/dav/cal/{acct}/default/"] == {}

    rep = await restore.restore(src)
    assert not rep.errors, rep.errors
    assert (rep.total.collections_created, rep.total.created, rep.total.skipped, rep.total.conflicts) == (2, 4, 0, 0)
    default = fake_store.cols[f"/dav/cal/{acct}/default/"]
    assert set(default) == {"a.ics", "t.ics"}  # the resource names are kept
    assert sorted(u for u, _ in restore.split_calendar(default["a.ics"][1])) == ["ev-a"]
    assert set(fake_store.cols[f"/dav/cal/{acct}/projects-1a/"]) == {"p.ics"}
    assert set(fake_store.cols[f"/dav/card/{acct}/default/"]) == {"keep-1.vcf"}
    assert rep.connectors == {conn.id}

    # Twice: nothing new, nothing duplicated.
    again = await restore.restore(src)
    assert (again.total.created, again.total.skipped, again.total.conflicts) == (0, 4, 0)
    assert len(default) == 2

    # Changed on the server since: a conflict, left as it is.
    etag, _ = default["t.ics"]
    default["t.ics"] = (etag, _task("task-1", "Report (edited on the phone)"))
    third = await restore.restore(src)
    assert third.total.conflicts == 1 and third.conflicts == [f"user {user.id} Calendar: task-1"]
    assert "edited on the phone" in default["t.ics"][1]
    # --overwrite puts the export's version back (after a volume restore the export is newer).
    dry = await restore.restore(src, overwrite=True, dry_run=True)
    assert dry.total.replaced == 1 and "edited on the phone" in default["t.ics"][1]
    over = await restore.restore(src, overwrite=True)
    assert (over.total.replaced, over.total.conflicts, over.total.created) == (1, 0, 0)
    assert "edited on the phone" not in default["t.ics"][1] and "Report" in default["t.ics"][1]


async def test_restore_filters_by_user_and_reports_missing_calendar(app, user, fake_store):
    acct = f"u{uuid.uuid4().hex[:6]}@example.test"
    conn = await _builtin(user, acct)
    await _collection(conn, f"/dav/cal/{acct.replace('@', '%40')}/default/", "Calendar",
                      [("a.ics", "event", _event("ev-a", "Standup"))])
    await backup.export_user(user.id)
    rep = await restore.restore(backup.root(), user="nobody@example.com", dry_run=True)
    assert rep.owners == []
    async with sessionmaker()() as db:
        await db.delete(await db.get(Connector, conn.id))
        await db.commit()
    rep = await restore.restore(backup.export_dir(user.id), user=user.email)
    assert rep.errors and "no Mailr calendar" in rep.errors[0]
    assert (await restore.restore(Path("/nonexistent"))).errors


async def test_forced_resync_clears_tokens_and_queues_full_syncs(app, user, dirs):
    conn = await _builtin(user, f"u{uuid.uuid4().hex[:6]}@example.test")
    coll_id = await _collection(conn, "/dav/cal/x/default/", "Calendar", [], sync_token="urn:stalwart:davsync:9",
                                ctag='"9"')
    book_id = await _book(conn, "/dav/card/x/default/", "Contacts", [])
    async with sessionmaker()() as db:
        b = await db.get(AddressBook, book_id)
        b.sync_token, b.ctag = "urn:stalwart:davsync:9", '"9"'
        ext = Connector(user_id=user.id, kind="caldav", name="Work", mode="mirror", config={"url": "https://x.example/"},
                        state={}, enabled=True, status="idle")
        db.add(ext)
        await db.commit()
    ids = await restore.connectors_for(user=user.email)
    assert ids == [conn.id]
    assert set(await restore.connectors_for(user=str(user.id), include_external=True)) == {conn.id, ext.id}
    assert conn.id in await restore.connectors_for()
    assert await restore.force_resync(ids) == 1
    async with sessionmaker()() as db:
        coll = await db.get(CalCollection, coll_id)
        b = await db.get(AddressBook, book_id)
        assert coll.sync_token is None and coll.ctag is None and coll.next_sync_at is not None
        assert b.sync_token is None and b.ctag is None
    cal_jobs = await _jobs("calendar.sync", conn.id)
    assert cal_jobs and cal_jobs[0].payload["full"] is True
    assert (await _jobs("contacts.sync", conn.id))[0].payload["full"] is True
    assert await restore.force_resync(ids) == 1  # queued again: deduplicated, not doubled
    assert len(await _jobs("calendar.sync", conn.id)) == 1


def test_cli_parses_the_calendar_commands(monkeypatch):
    import sys

    from mailr import cli

    seen = []

    async def fake(a):
        seen.append((a.action, getattr(a, "source", None), getattr(a, "dry_run", None), getattr(a, "all", None)))

    monkeypatch.setattr(cli, "_calendars", fake)
    for argv in (["mailr", "calendars", "restore", "--from", "/backup/calendars", "--dry-run"],
                 ["mailr", "calendars", "resync", "--all"], ["mailr", "calendars", "export"]):
        monkeypatch.setattr(sys, "argv", argv)
        cli.main()
    assert seen == [("restore", "/backup/calendars", True, None), ("resync", None, False, True),
                    ("export", None, None, None)]


# ---------------- the address-book mirror keeps the card ----------------


async def test_carddav_keeps_the_card_and_refetches_old_rows(client, user):
    from dev_services import radicale_up as _radicale_up
    from helpers.calendar import _connect
    from helpers.carddav import ALICE, BOB, _mkbook, _put

    from mailr.contacts import carddav

    if not _radicale_up():
        pytest.skip("Radicale dev server not running on :15232")
    username = await _connect(client)
    book = _mkbook(username)
    _put(book, username, "alice", ALICE)
    _put(book, username, "bob", BOB)
    async with sessionmaker()() as db:
        conn = (await db.execute(select(Connector).where(Connector.user_id == user.id, Connector.kind == "caldav",
                                                         Connector.builtin.is_(False)))).scalar_one()
    await carddav.sync_connector(conn.id)
    async with sessionmaker()() as db:
        rows = (await db.execute(select(ContactCard).where(ContactCard.connector_id == conn.id))).scalars().all()
        assert {r.uid: bool(re.search(r"FN:Bob Jones", r.vcard or "")) for r in rows} == {"alice-uid": False,
                                                                                           "bob-uid": True}
        assert all(r.vcard and r.vcard.startswith("BEGIN:VCARD") for r in rows)
        await db.execute(text("UPDATE contact_cards SET vcard = NULL WHERE connector_id = :c"), {"c": conn.id})
        await db.commit()
    await carddav.sync_connector(conn.id)  # nothing changed on the server: still fetched again
    async with sessionmaker()() as db:
        rows = (await db.execute(select(ContactCard).where(ContactCard.connector_id == conn.id))).scalars().all()
        assert len(rows) == 2 and all(r.vcard for r in rows)
