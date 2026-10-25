"""Seed a throwaway Davis (Sabre/DAV) server for the calendar migration tests (internal).

Used by tests/test_calendar_migrate_davis.py. Give it a throwaway Davis only, never a real one:

    docker run -d --name davis-test -p 127.0.0.1:18998:9000 -v davis-test-data:/data \
      -e APP_ENV=prod -e APP_SECRET=<32 hex> -e DATABASE_DRIVER=sqlite \
      -e DATABASE_URL=sqlite:////data/davis-database.db -e ADMIN_LOGIN=admin -e ADMIN_PASSWORD=<pw> \
      -e AUTH_METHOD=Basic -e CALDAV_ENABLED=true -e CARDDAV_ENABLED=true -e WEBDAV_ENABLED=false \
      -e MAILER_DSN=null://null -e API_KEY=<key> ghcr.io/tchapi/davis-standalone:5.4.3
    docker exec davis-test sh -c 'cd /var/www/davis && php bin/console doctrine:migrations:migrate -n \
      && chown -R www-data /data'

Users are made through the admin pages (as an admin would), delegation through the admin's
"Delegates" page, calendar shares through Davis's API, everything else over CalDAV/CardDAV as
the user.
"""

from __future__ import annotations

import base64
import re
import uuid
from dataclasses import dataclass, field

import httpx

TZ = """BEGIN:VTIMEZONE\r
TZID:Europe/Berlin\r
BEGIN:STANDARD\r
DTSTART:19701025T030000\r
TZOFFSETFROM:+0200\r
TZOFFSETTO:+0100\r
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU\r
END:STANDARD\r
BEGIN:DAYLIGHT\r
DTSTART:19700329T020000\r
TZOFFSETFROM:+0100\r
TZOFFSETTO:+0200\r
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU\r
END:DAYLIGHT\r
END:VTIMEZONE\r
"""

# A 1x1 JPEG, for the contact photos.
PHOTO = base64.b64encode(bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f"
    "1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100ffc4001f0000"
    "010501010101010100000000000000000102030405060708090a0bffc400b5100002010303020403050504040000017d010203000411"
    "05122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445"
    "464748494a535455565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9"
    "aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100"
    "003f00fbfcffd9")).decode()


def cal(body: str, tz: bool = True) -> str:
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Northwind//Seed//EN\r\n{TZ if tz else ''}{body}END:VCALENDAR\r\n"


def fold(line: str) -> str:
    """RFC 5545 line folding at 75 octets."""
    out, cur = [], line
    while len(cur) > 75:
        out.append(cur[:75])
        cur = " " + cur[75:]
    out.append(cur)
    return "\r\n".join(out) + "\r\n"


@dataclass
class Seeded:
    """What the seed put on the server, for the assertions."""
    user: str
    password: str
    calendars: dict[str, dict[str, str]] = field(default_factory=dict)   # slug → {uid: text}
    books: dict[str, dict[str, str]] = field(default_factory=dict)       # slug → {uid: text}
    names: dict[str, str] = field(default_factory=dict)                  # slug → display name
    delegate: str = ""
    shared_in: str = ""      # the slug of the calendar another user shared with this one
    subscription: str = ""


class Davis:
    def __init__(self, base: str, admin: str, admin_password: str, api_key: str):
        self.base = base.rstrip("/")
        self.admin = (admin, admin_password)
        self.api_key = api_key
        self.web = httpx.Client(base_url=self.base, follow_redirects=False, timeout=30)
        self._logged_in = False

    def close(self) -> None:
        self.web.close()

    # ---------------- the admin pages ----------------

    def _login(self) -> None:
        if self._logged_in:
            return
        page = self.web.get("/login").text
        token = re.search(r'name="_csrf_token" value="([^"]+)"', page).group(1)
        r = self.web.post("/login", data={"_username": self.admin[0], "_password": self.admin[1], "_csrf_token": token})
        assert r.status_code == 302 and "dashboard" in r.headers.get("location", ""), r.text[:300]
        self._logged_in = True

    def create_user(self, username: str, password: str, name: str, email: str) -> int:
        """A user with its default calendar and address book, as the admin page makes them. Returns the user id."""
        self._login()
        page = self.web.get("/users/new").text
        token = re.search(r'name="user\[_token\]"[^>]*value="([^"]+)"', page).group(1)
        r = self.web.post("/users/new", data={
            "user[username]": username, "user[displayName]": name, "user[email]": email,
            "user[password][first]": password, "user[password][second]": password, "user[_token]": token})
        assert r.status_code == 302, r.text[:500]
        return self.user_id(username)

    def _api(self, method: str, path: str, **kw) -> httpx.Response:
        return self.web.request(method, "/api/v1" + path, headers={"X-Davis-API-Token": self.api_key,
                                                                   "Accept": "application/json"}, **kw)

    def user_id(self, username: str) -> int:
        return int(self._user(username)["user_id"])

    def _user(self, username: str) -> dict:
        users = self._api("GET", "/users").json()["data"]
        return next(u for u in users if u["username"] == username)

    def principal_id(self, username: str) -> int:
        return int(self._user(username)["principal_id"])

    def add_delegate(self, owner: str, delegate: str, write: bool = True) -> None:
        """calendar-proxy-write (or -read) on the owner's principal, through the Delegates page."""
        self._login()
        pid = self.principal_id(delegate)
        r = self.web.get(f"/users/delegates/{self.user_id(owner)}/add",
                         params={"principalId": pid, "write": "true" if write else "false"})
        assert r.status_code == 302, r.text[:300]

    def calendar_id(self, username: str, slug: str) -> int:
        data = self._api("GET", f"/calendars/{self.user_id(username)}").json()["data"]
        for c in data.get("user_calendars") or []:
            if c.get("uri") == slug:
                return int(c["id"])
        raise LookupError(slug)

    def share(self, owner: str, slug: str, sharee: str, write: bool) -> None:
        r = self._api("POST", f"/calendars/{self.user_id(owner)}/share/{self.calendar_id(owner, slug)}/add",
                      json={"username": sharee, "write_access": write})
        assert r.status_code == 200, r.text[:300]

    # ---------------- DAV as the user ----------------

    def dav(self, user: str, password: str) -> httpx.Client:
        return httpx.Client(base_url=self.base, auth=(user, password), timeout=30)


def _mkcalendar(c: httpx.Client, path: str, name: str, comps: list[str], color: str | None = None) -> None:
    comp = "".join(f'<C:comp name="{x}"/>' for x in comps)
    col = f'<A:calendar-color xmlns:A="http://apple.com/ns/ical/">{color}</A:calendar-color>' if color else ""
    body = (f'<?xml version="1.0" encoding="utf-8"?><C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
            f'<D:set><D:prop><D:displayname>{name}</D:displayname><C:supported-calendar-component-set>{comp}'
            f'</C:supported-calendar-component-set>{col}</D:prop></D:set></C:mkcalendar>')
    r = c.request("MKCALENDAR", path, content=body, headers={"Content-Type": "application/xml"})
    assert r.status_code == 201, (path, r.status_code, r.text[:300])


def _mkbook(c: httpx.Client, path: str, name: str) -> None:
    body = ('<?xml version="1.0" encoding="utf-8"?><D:mkcol xmlns:D="DAV:" xmlns:CR="urn:ietf:params:xml:ns:carddav">'
            '<D:set><D:prop><D:resourcetype><D:collection/><CR:addressbook/></D:resourcetype>'
            f'<D:displayname>{name}</D:displayname></D:prop></D:set></D:mkcol>')
    r = c.request("MKCOL", path, content=body, headers={"Content-Type": "application/xml"})
    assert r.status_code == 201, (path, r.status_code, r.text[:300])


def _put(c: httpx.Client, path: str, text: str, ctype: str = "text/calendar") -> None:
    r = c.put(path, content=text.encode(), headers={"Content-Type": f"{ctype}; charset=utf-8"})
    assert r.status_code in (201, 204), (path, r.status_code, r.text[:300])


def seed(davis: Davis, *, bulk: int = 230) -> Seeded:
    """Two users, `rosa-<hex>` (the one who moves) and `kai-<hex>` (her assistant):

    - rosa's default calendar: a weekly series with two overrides and an EXDATE (Europe/Berlin), an
      event with a reminder and an attachment by URL, an all-day event, a floating-time event, and
      a meeting rosa organizes with kai as a guest (scheduling stamps it on the server);
    - "Work" (#3F51B5, events), shared with kai read-write;
    - "Chores" (tasks only): a parent task with two subtasks (RELATED-TO), one completed, one with
      a reminder;
    - "Notes" (journal entries);
    - "Bulk": `bulk` events, more than one page of the copy;
    - address books: the default one with a vCard 3.0 with an embedded photo, a vCard 4.0 with a
      data: URI photo, a card with several addresses and a group card; "Clients" with one card;
    - kai: write delegate of rosa (calendar-proxy-write), and a "Team" calendar shared with rosa
      read-only; kai invites rosa to a meeting (in rosa's scheduling inbox);
    - a subscription in rosa's calendar home.
    """
    tag = uuid.uuid4().hex[:6]
    rosa, kai = f"rosa-{tag}", f"kai-{tag}"
    pw = "seed-pass-" + tag
    davis.create_user(rosa, pw, "Rosa Lind", f"{rosa}@northwind.example")
    davis.create_user(kai, pw, "Kai Berg", f"{kai}@northwind.example")
    s = Seeded(user=rosa, password=pw, delegate=kai)
    home = f"/dav/calendars/{rosa}/"
    books = f"/dav/addressbooks/{rosa}/"
    with davis.dav(rosa, pw) as c:
        d: dict[str, str] = {}
        uid = f"series-{tag}@northwind.example"
        d[uid] = cal(
            "BEGIN:VEVENT\r\n" f"UID:{uid}\r\n" "DTSTAMP:20261001T080000Z\r\nSUMMARY:Weekly planning\r\n"
            "DTSTART;TZID=Europe/Berlin:20261005T090000\r\nDTEND;TZID=Europe/Berlin:20261005T093000\r\n"
            "RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=20\r\nEXDATE;TZID=Europe/Berlin:20261019T090000\r\n"
            "DESCRIPTION:Agenda in the shared folder\\, as usual.\r\nCATEGORIES:Work,Planning\r\n"
            "END:VEVENT\r\n"
            "BEGIN:VEVENT\r\n" f"UID:{uid}\r\n" "DTSTAMP:20261001T080000Z\r\nSUMMARY:Weekly planning (moved)\r\n"
            "RECURRENCE-ID;TZID=Europe/Berlin:20261012T090000\r\n"
            "DTSTART;TZID=Europe/Berlin:20261012T110000\r\nDTEND;TZID=Europe/Berlin:20261012T113000\r\n"
            "END:VEVENT\r\n"
            "BEGIN:VEVENT\r\n" f"UID:{uid}\r\n" "DTSTAMP:20261001T080000Z\r\nSUMMARY:Weekly planning (long)\r\n"
            "RECURRENCE-ID;TZID=Europe/Berlin:20261026T090000\r\n"
            "DTSTART;TZID=Europe/Berlin:20261026T090000\r\nDTEND;TZID=Europe/Berlin:20261026T110000\r\n"
            "LOCATION:Room 2\r\nEND:VEVENT\r\n")
        uid = f"dentist-{tag}@northwind.example"
        d[uid] = cal(
            "BEGIN:VEVENT\r\n" f"UID:{uid}\r\n" "DTSTAMP:20261001T080000Z\r\nSUMMARY:Dentist\r\n"
            "DTSTART;TZID=Europe/Berlin:20261014T153000\r\nDTEND;TZID=Europe/Berlin:20261014T161500\r\n"
            "ATTACH;FMTTYPE=application/pdf:https://files.northwind.example/forms/dentist.pdf\r\n"
            "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Dentist\r\nTRIGGER:-PT30M\r\nEND:VALARM\r\n"
            "END:VEVENT\r\n")
        uid = f"holiday-{tag}@northwind.example"
        d[uid] = cal("BEGIN:VEVENT\r\n" f"UID:{uid}\r\n" "DTSTAMP:20261001T080000Z\r\nSUMMARY:Holiday\r\n"
                     "DTSTART;VALUE=DATE:20261103\r\nDTEND;VALUE=DATE:20261106\r\nTRANSP:TRANSPARENT\r\n"
                     "END:VEVENT\r\n", tz=False)
        uid = f"floating-{tag}@northwind.example"
        d[uid] = cal("BEGIN:VEVENT\r\n" f"UID:{uid}\r\n" "DTSTAMP:20261001T080000Z\r\nSUMMARY:Morning run\r\n"
                     "DTSTART:20261007T070000\r\nDURATION:PT45M\r\nEND:VEVENT\r\n", tz=False)
        uid = f"meeting-{tag}@northwind.example"
        d[uid] = cal(
            "BEGIN:VEVENT\r\n" f"UID:{uid}\r\n" "DTSTAMP:20261001T080000Z\r\nSUMMARY:Budget review\r\n"
            "DTSTART;TZID=Europe/Berlin:20261016T140000\r\nDTEND;TZID=Europe/Berlin:20261016T150000\r\n"
            f"ORGANIZER;CN=Rosa Lind:mailto:{rosa}@northwind.example\r\n"
            f"ATTENDEE;CN=Kai Berg;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{kai}@northwind.example\r\n"
            "END:VEVENT\r\n")
        for u, text in d.items():
            _put(c, f"{home}default/{u}.ics", text)
        s.calendars["default"] = d

        _mkcalendar(c, f"{home}work/", "Work", ["VEVENT"], "#3F51B5FF")
        w = {}
        for i in range(3):
            uid = f"work-{i}-{tag}@northwind.example"
            w[uid] = cal("BEGIN:VEVENT\r\n" f"UID:{uid}\r\n" f"DTSTAMP:20261001T080000Z\r\nSUMMARY:Client call {i}\r\n"
                         f"DTSTART;TZID=Europe/Berlin:2026102{i}T100000\r\nDTEND;TZID=Europe/Berlin:2026102{i}T103000\r\n"
                         "END:VEVENT\r\n")
            # A resource name that is not the UID, as phones write them.
            _put(c, f"{home}work/{uuid.uuid4().hex.upper()}.ics", w[uid])
        s.calendars["work"] = w

        _mkcalendar(c, f"{home}chores/", "Chores", ["VTODO"], "#0B8043")
        t = {}
        parent = f"move-{tag}@northwind.example"
        t[parent] = cal("BEGIN:VTODO\r\n" f"UID:{parent}\r\n" "DTSTAMP:20261001T080000Z\r\nSUMMARY:Move flat\r\n"
                        "DUE;VALUE=DATE:20261130\r\nPRIORITY:1\r\nSTATUS:NEEDS-ACTION\r\nEND:VTODO\r\n", tz=False)
        for i, (summary, extra) in enumerate([
            ("Book the van", "STATUS:COMPLETED\r\nCOMPLETED:20261002T120000Z\r\nPERCENT-COMPLETE:100\r\n"),
            ("Pack the kitchen", ("DUE;TZID=Europe/Berlin:20261120T180000\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\n"
                                  "DESCRIPTION:Pack\r\nTRIGGER;RELATED=END:-PT2H\r\nEND:VALARM\r\n")),
        ]):
            uid = f"move-sub{i}-{tag}@northwind.example"
            body = ("BEGIN:VTODO\r\n" f"UID:{uid}\r\n" f"DTSTAMP:20261001T080000Z\r\nSUMMARY:{summary}\r\n"
                    f"RELATED-TO;RELTYPE=PARENT:{parent}\r\n")
            if "BEGIN:VALARM" in extra:
                head, alarm = extra.split("BEGIN:VALARM", 1)
                body += head + "BEGIN:VALARM" + alarm + "END:VTODO\r\n"
            else:
                body += extra + "END:VTODO\r\n"
            t[uid] = cal(body, tz="TZID=" in extra)
        for u, text in t.items():
            _put(c, f"{home}chores/{u}.ics", text)
        s.calendars["chores"] = t

        _mkcalendar(c, f"{home}notes/", "Notes", ["VJOURNAL"])
        uid = f"journal-{tag}@northwind.example"
        j = {uid: cal("BEGIN:VJOURNAL\r\n" f"UID:{uid}\r\n" "DTSTAMP:20261001T080000Z\r\nDTSTART;VALUE=DATE:20261001\r\n"
                      "SUMMARY:Kick-off notes\r\nDESCRIPTION:Decided on the vendor.\r\nEND:VJOURNAL\r\n", tz=False)}
        _put(c, f"{home}notes/{uid}.ics", j[uid])
        s.calendars["notes"] = j

        _mkcalendar(c, f"{home}bulk/", "Bulk", ["VEVENT"])
        b = {}
        for i in range(bulk):
            uid = f"bulk-{i:04d}-{tag}@northwind.example"
            b[uid] = cal("BEGIN:VEVENT\r\n" f"UID:{uid}\r\n" f"DTSTAMP:20261001T080000Z\r\nSUMMARY:Shift {i}\r\n"
                         f"DTSTART:2027{(i % 12) + 1:02d}{(i % 27) + 1:02d}T080000Z\r\nDURATION:PT8H\r\nEND:VEVENT\r\n",
                         tz=False)
            _put(c, f"{home}bulk/{uid}.ics", b[uid])
        s.calendars["bulk"] = b
        s.names.update({"default": "", "work": "Work", "chores": "Chores", "notes": "Notes", "bulk": "Bulk"})

        # Address books.
        a = {}
        uid = f"card-v3-{tag}"
        a[uid] = ("BEGIN:VCARD\r\nVERSION:3.0\r\nPRODID:-//Northwind//Seed//EN\r\n" f"UID:{uid}\r\n"
                  "FN:Ines Ortega\r\nN:Ortega;Ines;;;\r\nEMAIL;TYPE=INTERNET,WORK:ines@northwind.example\r\n"
                  "TEL;TYPE=CELL:+49 30 1234567\r\nORG:Northwind Traders\r\nTITLE:Buyer\r\n"
                  + fold("PHOTO;ENCODING=b;TYPE=JPEG:" + PHOTO) + "END:VCARD\r\n")
        uid = f"card-v4-{tag}"
        a[uid] = ("BEGIN:VCARD\r\nVERSION:4.0\r\n" f"UID:{uid}\r\n"
                  "FN:Tomas Weber\r\nN:Weber;Tomas;;;\r\nEMAIL;TYPE=home:tomas@example.org\r\nBDAY:19840212\r\n"
                  + fold("PHOTO:data:image/jpeg;base64," + PHOTO) + "END:VCARD\r\n")
        uid = f"card-adr-{tag}"
        a[uid] = ("BEGIN:VCARD\r\nVERSION:3.0\r\n" f"UID:{uid}\r\n"
                  "FN:Lena Novak\r\nN:Novak;Lena;;;\r\nEMAIL;TYPE=INTERNET:lena@northwind.example\r\n"
                  "EMAIL;TYPE=INTERNET:lena.novak@example.org\r\nTEL;TYPE=WORK:+49 30 7654321\r\n"
                  "ADR;TYPE=HOME:;;Linienstr. 5;Berlin;;10119;Germany\r\n"
                  "ADR;TYPE=WORK:;;Hafenweg 2;Hamburg;;20457;Germany\r\nNOTE:Prefers mornings\\, not Fridays.\r\n"
                  "CATEGORIES:Suppliers\r\nEND:VCARD\r\n")
        uid = f"group-{tag}"
        a[uid] = ("BEGIN:VCARD\r\nVERSION:3.0\r\n" f"UID:{uid}\r\n" "FN:Movers\r\nN:Movers;;;;\r\n"
                  "X-ADDRESSBOOKSERVER-KIND:group\r\n"
                  f"X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:card-adr-{tag}\r\nEND:VCARD\r\n")
        for u, text in a.items():
            _put(c, f"{books}default/{u}.vcf", text, "text/vcard")
        s.books["default"] = a
        _mkbook(c, f"{books}clients/", "Clients")
        uid = f"card-client-{tag}"
        cl = {uid: ("BEGIN:VCARD\r\nVERSION:3.0\r\n" f"UID:{uid}\r\n" "FN:Ada Brook\r\nN:Brook;Ada;;;\r\n"
                    "EMAIL:ada@example.com\r\nEND:VCARD\r\n")}
        _put(c, f"{books}clients/{uid}.vcf", cl[uid], "text/vcard")
        s.books["clients"] = cl

    davis.share(rosa, "work", kai, write=True)
    davis.add_delegate(rosa, kai, write=True)
    with davis.dav(kai, pw) as c:
        khome = f"/dav/calendars/{kai}/"
        _mkcalendar(c, f"{khome}team/", "Team", ["VEVENT"], "#E67C73")
        uid = f"team-{tag}@northwind.example"
        _put(c, f"{khome}team/{uid}.ics", cal("BEGIN:VEVENT\r\n" f"UID:{uid}\r\n" "DTSTAMP:20261001T080000Z\r\n"
                                               "SUMMARY:Team lunch\r\nDTSTART;TZID=Europe/Berlin:20261023T120000\r\n"
                                               "DTEND;TZID=Europe/Berlin:20261023T130000\r\nEND:VEVENT\r\n"))
        uid = f"invite-{tag}@northwind.example"
        _put(c, f"{khome}default/{uid}.ics", cal(
            "BEGIN:VEVENT\r\n" f"UID:{uid}\r\n" "DTSTAMP:20261001T080000Z\r\nSUMMARY:Offsite planning\r\n"
            "DTSTART;TZID=Europe/Berlin:20261028T100000\r\nDTEND;TZID=Europe/Berlin:20261028T120000\r\n"
            f"ORGANIZER;CN=Kai Berg:mailto:{kai}@northwind.example\r\n"
            f"ATTENDEE;CN=Rosa Lind;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{rosa}@northwind.example\r\n"
            "END:VEVENT\r\n"))
    davis.share(kai, "team", rosa, write=False)
    # Last: Davis 5.4.3's API answers 500 for the calendars of a user with a subscription.
    with davis.dav(rosa, pw) as c:
        # A subscription (calendarserver.org "subscribed" collection) in the calendar home.
        body = ('<?xml version="1.0" encoding="utf-8"?><D:mkcol xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">'
                '<D:set><D:prop><D:resourcetype><D:collection/><CS:subscribed/></D:resourcetype>'
                '<D:displayname>Public holidays</D:displayname>'
                # Davis's table wants an order and a colour (NOT NULL); clients send both.
                '<A:calendar-order xmlns:A="http://apple.com/ns/ical/">5</A:calendar-order>'
                '<A:calendar-color xmlns:A="http://apple.com/ns/ical/">#9E69AFFF</A:calendar-color>'
                '<CS:source><D:href>https://calendars.example.net/holidays.ics</D:href></CS:source>'
                '</D:prop></D:set></D:mkcol>')
        r = c.request("MKCOL", f"{home}holidays-sub/", content=body, headers={"Content-Type": "application/xml"})
        assert r.status_code == 201, r.text[:300]
        s.subscription = "holidays-sub"
    return s
