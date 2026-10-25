# ACT-SURFACES: api
"""Copying a connected CalDAV account into the mailr calendar (calendar/migrate.py, the routes
in api/calendar_migration_routes.py): calendar.migrate and calendar.migrate_cutover.

Both sides are small in-memory WebDAV servers behind httpx's MockTransport, so the real DavClient
code runs. tests/test_calendar_migrate_davis.py does the same against a throwaway Davis and Stalwart
(opt-in).
"""

from __future__ import annotations

import base64
import re
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote, urlsplit

import httpx
import pytest
from sqlalchemy import select

from mailr.calendar import builtin, migrate, restore
from mailr.config import get_settings
from mailr.db import sessionmaker
from mailr.models import CalCollection, Connector, Job, User

STORE = "http://store.test"
SRC = "http://source.test"


def _ev(uid: str, summary: str, extra: str = "") -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Source//EN\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:20261001T080000Z\r\nSUMMARY:{summary}\r\nDTSTART:20261012T090000Z\r\n"
            f"DURATION:PT1H\r\n{extra}END:VEVENT\r\nEND:VCALENDAR\r\n")


def _todo(uid: str, summary: str) -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Source//EN\r\nBEGIN:VTODO\r\n"
            f"UID:{uid}\r\nDTSTAMP:20261001T080000Z\r\nSUMMARY:{summary}\r\nEND:VTODO\r\nEND:VCALENDAR\r\n")


def _card(uid: str, fn: str) -> str:
    return f"BEGIN:VCARD\r\nVERSION:3.0\r\nUID:{uid}\r\nFN:{fn}\r\nEMAIL:{uid}@northwind.example\r\nEND:VCARD\r\n"


class Server:
    """A tiny CalDAV/CardDAV server: one principal, a calendar home and an address-book home.
    Collections are paths with properties; resources are name → (etag, body)."""

    def __init__(self, cal_home: str, card_home: str, principal: str = "/principals/u/"):
        self.cal_home, self.card_home, self.principal = cal_home, card_home, principal
        self.cols: dict[str, dict] = {}
        self.methods: list[str] = []
        self.cut: set[str] = set()       # collections whose listings end in a 507
        self.delegates: list[str] = []   # principal hrefs in calendar-proxy-write

    def add(self, path: str, name: str, kind: str = "calendar", comps=("VEVENT", "VTODO"), color=None, extra=""):
        self.cols[path] = {"name": name, "kind": kind, "comps": list(comps), "color": color, "extra": extra,
                           "items": {}}
        return path

    def put(self, path: str, name: str, body: str) -> None:
        self.cols[path]["items"][name] = (f'"{uuid.uuid4().hex[:8]}"', body)

    # ---------------- HTTP ----------------

    def _ms(self, rs: list[str]) -> httpx.Response:
        ns = ('xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:card="urn:ietf:params:xml:ns:carddav" '
              'xmlns:cs="http://calendarserver.org/ns/" xmlns:ic="http://apple.com/ns/ical/"')
        return httpx.Response(207, content=f"<d:multistatus {ns}>{''.join(rs)}</d:multistatus>".encode())

    @staticmethod
    def _resp(href: str, props: str, status: str = "200 OK") -> str:
        return (f"<d:response><d:href>{quote(href)}</d:href><d:propstat><d:prop>{props}</d:prop>"
                f"<d:status>HTTP/1.1 {status}</d:status></d:propstat></d:response>")

    def _cut(self, path: str) -> str:
        return (f"<d:response><d:href>{quote(path)}</d:href><d:status>HTTP/1.1 507 Insufficient Storage</d:status>"
                "<d:error><d:number-of-matches-within-limits/></d:error></d:response>")

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = unquote(urlsplit(str(req.url)).path)
        m, body = req.method, req.content or b""
        self.methods.append(m)
        if m == "PROPFIND":
            depth = req.headers.get("depth", "0")
            if b"current-user-principal" in body:
                return self._ms([self._resp(path, f"<d:current-user-principal><d:href>{self.principal}</d:href>"
                                                  "</d:current-user-principal>")])
            if b"calendar-home-set" in body:
                return self._ms([self._resp(path, f"<c:calendar-home-set><d:href>{self.cal_home}</d:href>"
                                                  "</c:calendar-home-set>")])
            if b"addressbook-home-set" in body:
                return self._ms([self._resp(path, f"<card:addressbook-home-set><d:href>{self.card_home}</d:href>"
                                                  "</card:addressbook-home-set>")])
            if b"group-member-set" in body:
                if path.endswith("calendar-proxy-write/") and self.delegates:
                    hs = "".join(f"<d:href>{h}</d:href>" for h in self.delegates)
                    return self._ms([self._resp(path, f"<d:group-member-set>{hs}</d:group-member-set>")])
                return httpx.Response(404)
            if b"calendar-proxy-write-for" in body or (b"displayname" in body and depth == "0"
                                                       and path not in self.cols):
                return self._ms([self._resp(path, "")])
            if path in (self.cal_home, self.card_home) and depth == "1":
                rs = [self._resp(path, "<d:resourcetype><d:collection/></d:resourcetype>")]
                for p, c in self.cols.items():
                    if not p.startswith(path):
                        continue
                    rt = "<c:calendar/>" if c["kind"] == "calendar" else (
                        "<card:addressbook/>" if c["kind"] == "addressbook" else c["kind"])
                    comps = "".join(f'<c:comp name="{x}"/>' for x in c["comps"])
                    color = f"<ic:calendar-color>{c['color']}</ic:calendar-color>" if c["color"] else ""
                    rs.append(self._resp(p, f"<d:resourcetype><d:collection/>{rt}</d:resourcetype>"
                                            f"<d:displayname>{c['name']}</d:displayname>"
                                            f"<c:supported-calendar-component-set>{comps}"
                                            f"</c:supported-calendar-component-set>{color}{c['extra']}"))
                return self._ms(rs)
            if path in self.cols:
                rs = [self._resp(path, "<d:resourcetype><d:collection/></d:resourcetype>")]
                for n, (etag, _b) in self.cols[path]["items"].items():
                    rs.append(self._resp(path + n, f"<d:getetag>{etag}</d:getetag><d:resourcetype/>"))
                if path in self.cut:
                    rs = rs[:2] + [self._cut(path)]
                return self._ms(rs)
            return httpx.Response(404)
        if m == "REPORT":
            root = ET.fromstring(body)
            col = self.cols.get(path)
            if col is None:
                return httpx.Response(404)
            if root.tag.endswith("sync-collection"):
                rs = [self._resp(path + n, f"<d:getetag>{e}</d:getetag>") for n, (e, _b) in col["items"].items()]
                if path in self.cut:
                    rs = rs[:3] + [self._cut(path)]
                return self._ms(rs + ["<d:sync-token>tok-1</d:sync-token>"])
            card = "carddav" in root.tag
            tag = "card:address-data" if card else "c:calendar-data"
            rs = []
            for h in root.iter("{DAV:}href"):
                p = unquote(h.text or "")
                got = col["items"].get(p.rsplit("/", 1)[1])
                if got:
                    data = got[1].replace("&", "&amp;").replace("<", "&lt;")
                    rs.append(self._resp(p, f"<d:getetag>{got[0]}</d:getetag><{tag}>{data}</{tag}>"))
            return self._ms(rs)
        if m in ("MKCALENDAR", "MKCOL"):
            if path in self.cols:
                return httpx.Response(405)
            root = ET.fromstring(body)
            name = next((e.text or "" for e in root.iter("{DAV:}displayname")), "")
            comps = [e.get("name") for e in root.iter("{urn:ietf:params:xml:ns:caldav}comp")]
            color = next((e.text for e in root.iter("{http://apple.com/ns/ical/}calendar-color")), None)
            self.add(path, name, "calendar" if m == "MKCALENDAR" else "addressbook", comps, color)
            return httpx.Response(201)
        col_path, n = path.rsplit("/", 1)[0] + "/", path.rsplit("/", 1)[1]
        col = self.cols.get(col_path)
        if m == "PUT":
            if col is None:
                return httpx.Response(409)
            have = col["items"].get(n)
            if req.headers.get("if-none-match") == "*" and have:
                return httpx.Response(412)
            if req.headers.get("if-match") and (have or ("",))[0] != req.headers["if-match"]:
                return httpx.Response(412)
            if b"TOO-BIG" in body:
                return httpx.Response(403, content=b'<?xml version="1.0"?><d:error xmlns:d="DAV:" '
                                                    b'xmlns:c="urn:ietf:params:xml:ns:caldav"><c:max-resource-size/>'
                                                    b'</d:error>')
            etag = f'"{uuid.uuid4().hex[:8]}"'
            col["items"][n] = (etag, body.decode())
            return httpx.Response(201, headers={"ETag": etag})
        if m == "GET":
            got = (col or {"items": {}})["items"].get(n)
            return httpx.Response(200, content=got[1].encode(), headers={"ETag": got[0]}) if got else httpx.Response(404)
        return httpx.Response(405)


ACCT = "rosa-1a2b@example.test"
Q = ACCT.replace("@", "%40")


@pytest.fixture
def servers(monkeypatch):
    from mailr.contacts import carddav

    s = get_settings()
    monkeypatch.setattr(s, "stalwart_url", STORE)
    src = Server("/cal/rosa/", "/card/rosa/")
    dst = Server(f"/dav/cal/{ACCT}/", f"/dav/card/{ACCT}/")
    dst.add(f"/dav/cal/{ACCT}/default/", "Calendar", comps=["VEVENT", "VTODO", "VJOURNAL"])
    dst.add(f"/dav/cal/{ACCT}/tasks/", "Tasks", comps=["VTODO"])
    dst.add(f"/dav/card/{ACCT}/default/", "Contacts", kind="addressbook", comps=[])

    def client(server: Server, base: str):
        def make(conn, user):
            http = httpx.AsyncClient(transport=httpx.MockTransport(server.handler))
            return carddav.CardDavClient(base, "x", "y", internal=True, http=http)
        return make

    monkeypatch.setattr(migrate, "source_client", client(src, SRC + "/dav/"))
    monkeypatch.setattr(migrate, "target_client", client(dst, STORE + "/dav/cal/"))

    async def active(db, conn):
        return True

    monkeypatch.setattr(builtin, "active", active)
    return src, dst


async def _accounts(user: User) -> tuple[int, int]:
    async with sessionmaker()() as db:
        target = Connector(user_id=user.id, kind="caldav", name=builtin.NAME, mode="mirror", config={"account": ACCT},
                           state={}, enabled=True, builtin=True, status="idle")
        src = Connector(user_id=user.id, kind="caldav", name="Davis", mode="mirror",
                        config={"url": SRC + "/dav/", "username": "rosa"}, state={}, enabled=True, status="idle")
        db.add_all([target, src])
        await db.commit()
        return src.id, target.id


def _seed(src: Server) -> None:
    home = src.cal_home
    src.add(home + "default/", "Personal", comps=["VEVENT"], color="#3F51B5FF")
    for i in range(130):  # two pages
        src.put(home + "default/", f"ev-{i}.ics", _ev(f"ev-{i}", f"Event {i}"))
    src.add(home + "chores/", "Chores", comps=["VTODO"])
    src.put(home + "chores/", "weird name (1).ics", _todo("t-1", "Pay rent"))
    src.add(home + "team/", "Team", extra="<d:share-access><d:read/></d:share-access><cs:invite><cs:organizer>"
                                          "<d:href>/principals/kai/</d:href></cs:organizer></cs:invite>")
    src.put(home + "team/", "lunch.ics", _ev("lunch", "Team lunch"))
    src.add(home + "holidays/", "Holidays", kind="<cs:subscribed/>",
            extra="<cs:source><d:href>https://calendars.example.net/h.ics</d:href></cs:source>")
    src.add(home + "inbox/", "", kind="<c:schedule-inbox/>")
    src.put(home + "inbox/", "x.ics", _ev("invite", "Invite"))
    src.add(src.card_home + "default/", "Contacts", kind="addressbook", comps=[])
    src.put(src.card_home + "default/", "ada.vcf", _card("ada", "Ada Brook"))
    src.put(src.card_home + "default/", "nouid.vcf", "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:No Uid\r\nEND:VCARD\r\n")
    src.delegates = ["/principals/kai/"]


async def test_dry_run_then_copy_is_idempotent_and_reports_what_stays(app, user, servers):
    """calendar.migrate: a dry run changes nothing, the copy keeps UIDs, names and properties, a
    second run copies nothing, shares/subscriptions/delegates/scheduling are reported."""
    src, dst = servers
    _seed(src)
    sid, _tid = await _accounts(user)
    before = {p: dict(c["items"]) for p, c in src.cols.items()}

    dry = await migrate.migrate(user.id, sid, dry_run=True)
    t = dry.totals()
    assert (t["collections"], t["created_collections"], t["total"], t["copied"], t["failed"]) == (3, 2, 133, 132, 1)
    assert set(dst.methods) <= {"PROPFIND", "REPORT"}
    contacts = next(c for c in dry.collections if c.kind == "addressbook")
    assert (contacts.action, contacts.target_name) == ("existing", "Contacts")  # mapped by name
    assert contacts.failures[0]["reason"].startswith("The item has no UID")
    kinds = sorted(x["kind"] for x in dry.not_migrated)
    assert kinds == ["delegate", "scheduling", "shared_with_you", "subscription"], dry.not_migrated
    assert next(c for c in dry.collections if c.name == "Team").action == "skipped"

    seen: list[tuple[int, int, str]] = []

    async def progress(done, total, name):
        seen.append((done, total, name))

    rep = await migrate.migrate(user.id, sid, progress=progress)
    t = rep.totals()
    assert not rep.errors, rep.errors
    assert (t["copied"], t["skipped"], t["conflicts"], t["failed"]) == (132, 0, 0, 1)
    assert seen[0][:2] == (0, 133) and seen[-1][:2] == (133, 133) and ("Personal" in {s[2] for s in seen})
    personal = dst.cols[f"/dav/cal/{ACCT}/personal/"] if f"/dav/cal/{ACCT}/personal/" in dst.cols else \
        dst.cols[f"/dav/cal/{ACCT}/default-2/"]
    assert personal["name"] == "Personal" and personal["color"] == "#3F51B5" and personal["comps"] == ["VEVENT"]
    assert set(personal["items"]) == {f"ev-{i}.ics" for i in range(130)}  # resource names kept
    chores = dst.cols[f"/dav/cal/{ACCT}/chores/"]
    assert chores["comps"] == ["VTODO"] and list(chores["items"]) == ["t-1.ics"]  # a plain name from the UID
    assert set(dst.cols[f"/dav/card/{ACCT}/default/"]["items"]) == {"ada.vcf"}
    assert {p: dict(c["items"]) for p, c in src.cols.items()} == before  # the source is untouched
    assert set(src.methods) <= {"PROPFIND", "REPORT"}

    await migrate.record(user.id, rep)
    again = await migrate.migrate(user.id, sid)
    t = again.totals()
    assert (t["copied"], t["skipped"], t["conflicts"], t["created_collections"]) == (0, 132, 0, 0)
    assert {c.action for c in again.collections if c.action != "skipped"} == {"previous"}


async def test_conflicts_and_overwrite(app, user, servers):
    """calendar.migrate: other content under the same UID is a conflict; overwrite replaces it."""
    src, dst = servers
    src.add(src.cal_home + "work/", "Work", comps=["VEVENT"])
    src.put(src.cal_home + "work/", "a.ics", _ev("a", "Board meeting"))
    src.put(src.cal_home + "work/", "b.ics", _ev("b", "Review"))
    dst.add(f"/dav/cal/{ACCT}/work/", "Work", comps=["VEVENT"])
    dst.put(f"/dav/cal/{ACCT}/work/", "a.ics", _ev("a", "Board meeting (changed in mailr)"))
    # The same item as the server rewrote it: other folding, order and parameters.
    dst.put(f"/dav/cal/{ACCT}/work/", "b.ics", _ev("b", "Review").replace("PRODID:-//Source//EN", "PRODID:-//S//X")
            .replace("DTSTAMP:20261001T080000Z", "DTSTAMP:20261004T000000Z"))
    sid, _ = await _accounts(user)
    rep = await migrate.migrate(user.id, sid, collections=["Work"])
    c = rep.collections[0]
    assert (c.action, c.counts.created, c.counts.skipped, c.counts.conflicts) == ("existing", 0, 1, 1)
    assert c.as_dict()["conflict_uids"] == ["a"]
    assert "changed in mailr" in dst.cols[f"/dav/cal/{ACCT}/work/"]["items"]["a.ics"][1]
    dry = await migrate.migrate(user.id, sid, collections=["Work"], overwrite=True, dry_run=True)
    assert dry.collections[0].counts.replaced == 1
    assert "changed in mailr" in dst.cols[f"/dav/cal/{ACCT}/work/"]["items"]["a.ics"][1]
    over = await migrate.migrate(user.id, sid, collections=["Work"], overwrite=True)
    assert (over.collections[0].counts.replaced, over.collections[0].counts.conflicts) == (1, 0)
    assert "changed in mailr" not in dst.cols[f"/dav/cal/{ACCT}/work/"]["items"]["a.ics"][1]


async def test_name_taken_by_another_uid_refused_items_and_named_shared(app, user, servers):
    """A resource name another item holds gets a name from the UID; a refused item is a failure
    with a reason; a calendar shared with the user is copied when named."""
    src, dst = servers
    src.add(src.cal_home + "team/", "Team", extra="<d:share-access><d:read/></d:share-access>")
    src.put(src.cal_home + "team/", "x.ics", _ev("x-new", "New"))
    src.put(src.cal_home + "team/", "big.ics", _ev("big", "Huge", "DESCRIPTION:TOO-BIG\r\n"))
    dst.add(f"/dav/cal/{ACCT}/team/", "Team", comps=["VEVENT"])
    dst.put(f"/dav/cal/{ACCT}/team/", "x.ics", _ev("x-old", "Old"))
    sid, _ = await _accounts(user)
    rep = await migrate.migrate(user.id, sid)
    assert [c.action for c in rep.collections] == ["skipped"]  # shared with the user: not by default
    rep = await migrate.migrate(user.id, sid, collections=["team"])
    c = rep.collections[0]
    assert (c.counts.created, c.counts.failed) == (1, 1), c.as_dict()
    items = dst.cols[f"/dav/cal/{ACCT}/team/"]["items"]
    assert "x-new.ics" in items and "Old" in items["x.ics"][1]
    assert c.failures == [{"uid": "big", "item": "",
                           "reason": "Too large for the Mailr calendar (512 KiB per item, attachments included)."}]


async def test_a_cut_listing_uses_sync_and_the_mirror(app, user, servers):
    """A source listing cut short (507): sync-collection plus the mirror's list, marked partial."""
    from mailr.models import CalObject

    src, _dst = servers
    path = src.add(src.cal_home + "big/", "Big", comps=["VEVENT"])
    for i in range(6):
        src.put(path, f"e{i}.ics", _ev(f"e{i}", f"E{i}"))
    src.cut.add(path)
    sid, _ = await _accounts(user)
    async with sessionmaker()() as db:
        coll = CalCollection(user_id=user.id, connector_id=sid, href=SRC + path, displayname="Big", components=["VEVENT"])
        db.add(coll)
        await db.flush()
        for i in (4, 5):
            db.add(CalObject(user_id=user.id, connector_id=sid, collection_id=coll.id, href=SRC + path + f"e{i}.ics",
                             etag='"1"', uid=f"e{i}", kind="event", ics=_ev(f"e{i}", f"E{i}"), summary=f"E{i}"))
        await db.commit()
    rep = await migrate.migrate(user.id, sid)
    c = rep.collections[0]
    assert c.partial and c.total == 5 and c.counts.created == 5  # e0–e2 from the cut sync, e4–e5 from the mirror


def test_plan_targets_previous_name_and_unique_slugs():
    home = {"calendar": "http://store.test/dav/cal/a/", "addressbook": "http://store.test/dav/card/a/"}
    targets = {"calendar": [{"href": home["calendar"] + "default/", "name": "Calendar"},
                            {"href": home["calendar"] + "work/", "name": "Office"}],
               "addressbook": [{"href": home["addressbook"] + "default/", "name": "Contacts"}]}
    cols = [migrate.Collection(source="s/default/", kind="calendar", name="calendar", slug="default"),
            migrate.Collection(source="s/work/", kind="calendar", name="Work", slug="work"),
            migrate.Collection(source="s/w2/", kind="calendar", name="Work stuff", slug="work"),
            migrate.Collection(source="s/old/", kind="calendar", name="Renamed", slug="old"),
            migrate.Collection(source="s/ab/", kind="addressbook", name="Default Address Book", slug="default")]
    migrate.plan_targets(cols, targets, {"s/old/": home["calendar"] + "work/"}, home)
    got = {c.source: (c.action, c.target) for c in cols}
    assert got["s/default/"] == ("existing", home["calendar"] + "default/")  # same name, any case
    assert got["s/old/"] == ("previous", home["calendar"] + "work/")
    assert got["s/work/"] == ("create", home["calendar"] + "work-2/")
    assert got["s/w2/"] == ("create", home["calendar"] + "work-3/")
    assert got["s/ab/"] == ("create", home["addressbook"] + "default-2/")


PHOTO = base64.b64encode(b"\xff\xd8\xff\xe0 a tiny jpeg").decode()


def test_the_comparison_ignores_what_servers_rewrite():
    """restore.normalized, the definition of "the same item" for restore and the migration."""
    a = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Davis//EN\r\nBEGIN:VEVENT\r\nUID:m\r\nDTSTAMP:20261001T080000Z\r\n"
         "SUMMARY:Budget\r\nRRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=20\r\nORGANIZER;CN=Rosa Lind:mailto:rosa@northwind.example\r\n"
         "ATTENDEE;CN=Kai Berg;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;SCHEDULE-STATUS=1.2:mailto:kai@northwind.example\r\n"
         "ATTACH;FMTTYPE=application/pdf:https://files.northwind.example/forms/dentist.pdf\r\nEND:VEVENT\r\n"
         "END:VCALENDAR\r\n")
    b = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Stalwart//EN\r\nBEGIN:VEVENT\r\nUID:m\r\n"
         "DTSTAMP:20261005T000000Z\r\nSUMMARY:Budget\r\nRRULE:FREQ=WEEKLY;COUNT=20;BYDAY=MO\r\n"
         'ORGANIZER;CN="Rosa Lind":mailto:rosa@northwind.example\r\n'
         'ATTENDEE;RSVP=TRUE;CN="Kai Berg";PARTSTAT=NEEDS-ACTION:\r\n mailto:kai@northwind.example\r\n'
         "ATTACH;FMTTYPE=application/pdf:https://files.northwind.example/forms/dentis\r\n t.pdf\r\nEND:VEVENT\r\n"
         "END:VCALENDAR\r\n")
    assert restore.normalized(a) == restore.normalized(b)
    assert restore.normalized(a) != restore.normalized(a.replace("SUMMARY:Budget", "SUMMARY:Budget 2"))
    assert restore.normalized(a) != restore.normalized(a.replace("PARTSTAT=NEEDS-ACTION", "PARTSTAT=ACCEPTED"))
    v3 = (f"BEGIN:VCARD\r\nVERSION:3.0\r\nUID:c\r\nFN:Ines\r\nEMAIL;TYPE=INTERNET,work:i@northwind.example\r\n"
          f"PHOTO;ENCODING=b;TYPE=JPEG:{PHOTO}\r\nEND:VCARD\r\n")
    v4 = (f"BEGIN:VCARD\r\nVERSION:4.0\r\nUID:c\r\nFN:Ines\r\nEMAIL;TYPE=WORK,INTERNET:i@northwind.example\r\n"
          f"PHOTO;TYPE=JPEG:data:image/jpeg;base64\\,{PHOTO}\r\nEND:VCARD\r\n")
    assert restore.normalized(v3) == restore.normalized(v4)
    other = base64.b64encode(b"another picture").decode()
    assert restore.normalized(v3) != restore.normalized(v4.replace(PHOTO, other))


def test_refusal_names_the_precondition():
    resp = httpx.Response(403, content=b'<d:error xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                                       b"<c:no-uid-conflict><d:href>/x</d:href></c:no-uid-conflict></d:error>")
    assert restore.refusal(resp) == "HTTP 403 (no-uid-conflict)"
    assert migrate._reason("PUT /x: HTTP 403 (no-uid-conflict)").startswith("Another calendar")
    assert restore.refusal(httpx.Response(500)) == "HTTP 500"


async def test_needs_the_mailr_calendar_and_a_caldav_account(app, user, monkeypatch):
    async with sessionmaker()() as db:
        ext = Connector(user_id=user.id, kind="caldav", name="Davis", mode="mirror", config={"url": SRC}, state={},
                        enabled=True, status="idle")
        imap = Connector(user_id=user.id, kind="imap", name="Mail", mode="mirror", config={}, state={}, enabled=True,
                         status="idle")
        db.add_all([ext, imap])
        await db.commit()
    with pytest.raises(migrate.MigrationError, match="no Mailr calendar"):
        await migrate.migrate(user.id, ext.id, dry_run=True)
    with pytest.raises(migrate.MigrationError, match="Only a connected CalDAV"):
        await migrate.migrate(user.id, imap.id, dry_run=True)
    with pytest.raises(migrate.MigrationError, match="No such"):
        await migrate.migrate(user.id + 100000, ext.id, dry_run=True)


async def test_routes_preview_progress_and_cutover(client, user, servers):
    """calendar.migrate over the API (a job with progress), and calendar.migrate_cutover."""
    from mailr.core.jobs import JobContext

    src, _dst = servers
    _seed(src)
    sid, tid = await _accounts(user)
    r = await client.post(f"/api/v1/connectors/{sid}/migration", json={"dry_run": True})
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    assert r.json()["status"] == "queued" and r.json()["dry_run"] is True
    # A second start while one is queued returns the same job.
    assert (await client.post(f"/api/v1/connectors/{sid}/migration", json={"dry_run": True})).json()["job"] == job
    async with sessionmaker()() as db:
        j = await db.get(Job, job)
        payload = j.payload
    await migrate.migrate_job(payload, JobContext(job, migrate.JOB, user.id, 1))
    async with sessionmaker()() as db:
        await db.execute(Job.__table__.update().where(Job.id == job).values(status="done"))
        await db.commit()
    r = await client.get(f"/api/v1/connectors/{sid}/migration/{job}")
    out = r.json()
    assert out["status"] == "done" and out["done"] == out["total"] == 133
    assert out["result"]["totals"]["copied"] == 132 and out["result"]["dry_run"] is True
    assert {x["kind"] for x in out["result"]["not_migrated"]} >= {"delegate", "subscription"}
    assert (await client.get(f"/api/v1/connectors/{sid}/migration")).json()["last_copy"] is None
    # Cut-over needs a copy first.
    r = await client.post(f"/api/v1/connectors/{sid}/migration/cutover", json={"hide_copied": True})
    assert r.status_code == 409 and "first" in r.json()["detail"]

    r = await client.post(f"/api/v1/connectors/{sid}/migration", json={"dry_run": False})
    job = r.json()["job"]
    async with sessionmaker()() as db:
        payload = (await db.get(Job, job)).payload
    await migrate.migrate_job(payload, JobContext(job, migrate.JOB, user.id, 1))
    async with sessionmaker()() as db:
        await db.execute(Job.__table__.update().where(Job.id == job).values(status="done"))
        await db.commit()
    latest = (await client.get(f"/api/v1/connectors/{sid}/migration")).json()
    assert latest["job"] == job and latest["last_copy"]["result"]["totals"]["copied"] == 132
    # The account's mirrored calendars: hidden on request, nothing else changes.
    async with sessionmaker()() as db:
        for p in ("default/", "chores/", "team/"):
            db.add(CalCollection(user_id=user.id, connector_id=sid, href=SRC + src.cal_home + p, displayname=p,
                                 components=["VEVENT"]))
        await db.commit()
    r = await client.post(f"/api/v1/connectors/{sid}/migration/cutover", json={"hide_copied": True})
    assert r.status_code == 200, r.text
    assert r.json()["hidden"] == 2  # Personal and Chores; Team was not copied
    async with sessionmaker()() as db:
        hidden = {c.href.rsplit("/", 2)[1]: c.hidden for c in (await db.execute(
            select(CalCollection).where(CalCollection.connector_id == sid))).scalars()}
    assert hidden == {"default": True, "chores": True, "team": False}
    assert (await client.post(f"/api/v1/connectors/{sid}/migration/cutover", json={})).status_code == 400
    # Only the user's own external CalDAV accounts.
    assert (await client.post(f"/api/v1/connectors/{tid}/migration", json={})).status_code == 400
    assert (await client.get(f"/api/v1/connectors/{sid}/migration/999999")).status_code == 404


def test_cli_parses_migrate(monkeypatch):
    import sys

    from mailr import cli

    got = {}

    async def fake(a):
        got.update(vars(a))

    monkeypatch.setattr(cli, "_calendars", fake)
    monkeypatch.setattr(sys, "argv", ["mailr", "calendars", "migrate", "--user", "rosa@example.com",
                                      "--from-connector", "7", "--collections", "Work,Chores", "--dry-run",
                                      "--overwrite", "--make-default", "--hide-copied"])
    cli.main()
    assert (got["action"], got["user"], got["from_connector"], got["collections"]) == (
        "migrate", "rosa@example.com", 7, "Work,Chores")
    assert got["dry_run"] and got["overwrite"] and got["make_default"] and got["hide_copied"]


def test_resource_names():
    assert migrate._resource_name("http://x/c/ev-1.ics", "ev-1", ".ics") == "ev-1.ics"
    assert migrate._resource_name("http://x/c/weird%20name%20(1).ics", "t-1", ".ics") == "t-1.ics"
    assert migrate._resource_name("http://x/c/a.vcf", "u", ".ics") == "u.ics"
    assert re.fullmatch(r"[A-Za-z0-9._@-]+\.ics", migrate._resource_name("http://x/c/%E2%9C%93", "a b/c", ".ics"))
