# ACT-SURFACES: api
"""Calendar and iTIP: regression tests. Each one is an attack that must fail, plus the edges
around it:

- A sender's VTIMEZONE neither stalls the API nor is stored (calendar/zones.py), and one
  message's VTIMEZONE never defines a zone for another message (icalendar's process-wide cache);
- A calendar link is parsed off the event loop, https only, no https -> http redirect,
  per-link backoff;
- The sender's VALARMs and X-MAILR-* never reach the user's copy;
- An unverified COUNTER is shown as unverified and moves nothing without the user's "trust".
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.calendar import _connect
from helpers.work import rrule_periods
from sqlalchemy import select

from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.store import user_store
from mailr.db import sessionmaker
from mailr.models import CalObject, Connector, Identity, Message, User

# The most rule periods one zone lookup or one invitation may scan (helpers/work.py). A real zone
# from 1601 scans about 900; the bombs below scan millions.
ZONE_WORK = 5_000

radicale = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


def _stamp(d: datetime) -> str:
    return d.strftime("%Y%m%dT%H%M%S")


async def _identity(user) -> str:
    async with sessionmaker()() as db:
        c = Connector(user_id=user.id, kind="imap", name="out", mode="mirror", enabled=False, status="paused", config={})
        db.add(c)
        await db.flush()
        me = f"me-{c.id}@example.com"
        db.add(Identity(user_id=user.id, connector_id=c.id, email=me, name="Me", is_default=True, receives=[]))
        await db.commit()
    return me


async def _ingest(user, raw: bytes) -> int:
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        res = await ingest_raw(db, await user_store(db, u), raw, IngestOptions())
        await db.commit()
        return res.message.id


def _mail(sender: str, me: str, ics: str, *, auth: str = "", subject: str = "Invitation",
          method: str = "REQUEST") -> bytes:
    return ((auth and f"Authentication-Results: {auth}\r\n") +
            f"From: {sender}\r\nTo: {me}\r\nSubject: {subject}\r\n"
            f"Message-ID: <{uuid.uuid4().hex}@evil.example>\r\nDate: {datetime.now(UTC).strftime('%a, %d %b %Y %H:%M:%S +0000')}\r\n"
            "MIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary=\"b1\"\r\n\r\n"
            "--b1\r\nContent-Type: text/plain\r\n\r\nPlease come.\r\n"
            f"--b1\r\nContent-Type: text/calendar; charset=utf-8; method={method}\r\n\r\n"
            + ics + "--b1--\r\n").encode()


async def _stored(user, uid: str) -> CalObject:
    async with sessionmaker()() as db:
        return (await db.execute(select(CalObject).where(CalObject.user_id == user.id, CalObject.uid == uid,
                                                         CalObject.deleted_at.is_(None)))).scalar_one()


async def _cancel_outbox(client, r) -> None:
    ob = r.json().get("outbox") or {}
    if ob.get("id"):
        await client.post(f"/api/v1/outbox/{ob['id']}/cancel")


# ---------------- VTIMEZONE bomb ----------------


def _bomb_zone(tzid: str, rule_start: datetime, freq: str = "MINUTELY") -> str:
    rules = "".join(
        f"BEGIN:{kind}\r\nDTSTART:{_stamp(rule_start)}\r\nTZOFFSETFROM:{a}\r\nTZOFFSETTO:{b}\r\n"
        f"RRULE:FREQ={freq}\r\nEND:{kind}\r\n"
        for kind, a, b in (("STANDARD", "+0100", "+0000"), ("DAYLIGHT", "+0000", "+0100")))
    return f"BEGIN:VTIMEZONE\r\nTZID:{tzid}\r\n{rules}END:VTIMEZONE\r\n"


def _bomb_ics(uid: str, organizer: str, me: str, start: datetime, rule_start: datetime,
              tzid: str = "Mallory Standard Zone", extra: str = "") -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\nMETHOD:REQUEST\r\n"
            + _bomb_zone(tzid, rule_start) +
            f"BEGIN:VEVENT\r\nUID:{uid}\r\nSEQUENCE:0\r\nDTSTAMP:{_stamp(datetime.now(UTC))}Z\r\n"
            f"DTSTART;TZID={tzid}:{_stamp(start)}\r\nDTEND;TZID={tzid}:{_stamp(start + timedelta(hours=1))}\r\n"
            f"SUMMARY:Planning\r\nORGANIZER:mailto:{organizer}\r\n{extra}"
            f"ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{me}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


@pytest.mark.serial  # measures event-loop lag
@radicale
async def test_vtimezone_bomb_neither_blocks_the_api_nor_is_stored(client, user):
    """The original attack used a rule start 400 days back (~2 s per lookup before the fix); this one starts
    in 1970 (90 s and 3 GB before). Opening the card is quick, says which zone was not used, and
    Accept stores a fixed-offset VTIMEZONE in its place."""
    from helpers.security_calendar_limits import _LagProbe

    await _connect(client)
    me = await _identity(user)
    uid = f"bomb-{uuid.uuid4().hex[:8]}"
    start = (datetime.now(UTC) + timedelta(days=4)).replace(hour=10, minute=0, second=0, microsecond=0, tzinfo=None)
    ics = _bomb_ics(uid, "mallory@evil.example", me, start, datetime(1970, 1, 1))
    mid = await _ingest(user, _mail("Mallory <mallory@evil.example>", me, ics))

    async with _LagProbe() as probe:
        t = time.monotonic()
        r = await client.get(f"/api/v1/messages/{mid}/invite")
        took = time.monotonic() - t
    assert r.status_code == 200, r.text
    assert took < 1.5 and probe.lag < 0.5, (took, probe.lag)
    info = r.json()
    assert info["zones_replaced"] == ["Mallory Standard Zone"]
    # The newest STANDARD part says +00:00: the times are read with that fixed offset.
    assert info["start"] == start.replace(tzinfo=UTC).isoformat()

    r = await client.post("/api/v1/calendar/invite-reply", json={"message_id": mid, "response": "accepted"})
    assert r.status_code == 200, r.text
    assert r.json()["calendar"] == "added"
    await _cancel_outbox(client, r)
    obj = await _stored(user, uid)
    assert "FREQ=MINUTELY" not in obj.ics and "RRULE" not in obj.ics.split("BEGIN:VEVENT")[0]
    assert "TZID:Mallory Standard Zone" in obj.ics and "TZOFFSETTO:+0000" in obj.ics
    assert obj.start_at == start.replace(tzinfo=UTC)


@pytest.mark.serial  # measures event-loop lag
@radicale
async def test_a_known_zone_name_is_always_the_iana_zone(client, user):
    """A bomb under a Windows name: the sender's definition is ignored on the card and on Accept;
    the stored copy names the IANA zone with the standard VTIMEZONE."""
    from zoneinfo import ZoneInfo

    from helpers.security_calendar_limits import _LagProbe

    await _connect(client)
    me = await _identity(user)
    uid = f"bomb-win-{uuid.uuid4().hex[:8]}"
    start = (datetime.now(UTC) + timedelta(days=4)).replace(hour=10, minute=0, second=0, microsecond=0, tzinfo=None)
    ics = _bomb_ics(uid, "mallory@evil.example", me, start, datetime(1970, 1, 1), tzid="Central Standard Time")
    mid = await _ingest(user, _mail("Mallory <mallory@evil.example>", me, ics))
    async with _LagProbe() as probe:
        r = await client.get(f"/api/v1/messages/{mid}/invite")
    assert r.status_code == 200 and probe.lag < 0.5, (r.text, probe.lag)
    chicago = start.replace(tzinfo=ZoneInfo("America/Chicago"))
    assert datetime.fromisoformat(r.json()["start"]) == chicago and r.json()["tz"] == "America/Chicago"
    assert "zones_replaced" not in r.json()

    r = await client.post("/api/v1/calendar/invite-reply", json={"message_id": mid, "response": "accepted"})
    assert r.status_code == 200 and r.json()["calendar"] == "added", r.text
    await _cancel_outbox(client, r)
    obj = await _stored(user, uid)
    assert "FREQ=MINUTELY" not in obj.ics and "Central Standard Time" not in obj.ics
    assert "DTSTART;TZID=America/Chicago" in obj.ics and "TZID:America/Chicago" in obj.ics
    assert obj.start_at == chicago


def test_one_messages_zone_never_defines_another_messages():
    """icalendar keeps every VTIMEZONE it parses in one cache for the process, keyed by TZID: before
    the fix, the first message naming "Central Standard Time" set that zone for every later
    message of every user (and "Customized Time Zone", Outlook's name for a custom zone, too)."""
    from icalendar import Calendar

    import mailr.calendar  # noqa: F401  installs the rules

    def ics(tzid: str, off: str | None, uid: str) -> str:
        z = (f"BEGIN:VTIMEZONE\r\nTZID:{tzid}\r\nBEGIN:STANDARD\r\nDTSTART:19700101T000000\r\n"
             f"TZOFFSETFROM:{off}\r\nTZOFFSETTO:{off}\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\n") if off else ""
        return (f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\n{z}BEGIN:VEVENT\r\nUID:{uid}\r\n"
                f"DTSTAMP:20261001T000000Z\r\nDTSTART;TZID={tzid}:20261102T100000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")

    def start(text: str) -> str:
        return next(iter(Calendar.from_ical(text).walk("VEVENT")))["dtstart"].dt.isoformat()

    forged = f"Central Standard Time {uuid.uuid4().hex[:4]}"  # unknown: its own definition, bounded
    assert start(ics(forged, "+1400", "a")) == "2026-11-02T10:00:00+14:00"
    assert start(ics("Central Standard Time", "+1400", "b")) == "2026-11-02T10:00:00-06:00"
    assert start(ics("Central Standard Time", None, "c")) == "2026-11-02T10:00:00-06:00"
    custom = f"Customized Time Zone {uuid.uuid4().hex[:4]}"
    assert start(ics(custom, "+0500", "d")) == "2026-11-02T10:00:00+05:00"
    assert start(ics(custom, "-0300", "e")) == "2026-11-02T10:00:00-03:00"  # its own, not the first sender's


def _zone(parts: str, tzid: str = "Test Zone") -> object:
    from icalendar import Calendar

    cal = Calendar.from_ical(f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\nBEGIN:VTIMEZONE\r\nTZID:{tzid}\r\n"
                             f"{parts}END:VTIMEZONE\r\nEND:VCALENDAR\r\n")
    return next(iter(cal.walk("VTIMEZONE")))


def _part(rule: str = "", start: str = "19700101T000000", kind: str = "STANDARD", extra: str = "") -> str:
    return (f"BEGIN:{kind}\r\nDTSTART:{start}\r\nTZOFFSETFROM:+0200\r\nTZOFFSETTO:+0100\r\n"
            + (f"RRULE:{rule}\r\n" if rule else "") + f"{extra}END:{kind}\r\n")


@pytest.mark.parametrize("parts", [
    _part("FREQ=MINUTELY"), _part("FREQ=SECONDLY"), _part("FREQ=HOURLY"), _part("FREQ=DAILY"),
    _part("FREQ=WEEKLY"), _part("FREQ=YEARLY;BYHOUR=1,2,3"), _part("FREQ=YEARLY;COUNT=100000"),
    _part("FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30"), _part("FREQ=MONTHLY;BYDAY=6SU"),
    _part("FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR,SA,SU"), _part("FREQ=YEARLY", start="10000101T000000"),
    _part("FREQ=MONTHLY;BYMONTHDAY=1", start="16010101T000000"),  # 6,000 changes up to 2100
    _part("FREQ=YEARLY;INTERVAL=0"), _part() * 13,
    _part(extra="".join(f"RDATE:{1980 + i}0301T020000\r\n" for i in range(61))),
    "",
])
def test_costly_or_odd_zones_are_refused(parts):
    from mailr.calendar import zones

    vtz = _zone(parts)
    assert zones.zone_problem(vtz) is not None
    # A refused zone becomes a fixed offset, so a lookup walks no rule. Walking the bomb would scan
    # millions of periods; a real zone from 1601 scans about 900.
    with rrule_periods(limit=ZONE_WORK) as work:
        tz = zones.build(vtz)
        assert datetime(2026, 11, 2, 10, tzinfo=tz).utcoffset() is not None
    assert work.n <= ZONE_WORK


@pytest.mark.parametrize("parts", [
    # Outlook's own custom zone, Apple's and Google's shapes, a monthly zone, plain RDATEs.
    _part("FREQ=YEARLY;BYDAY=-1SU;BYMONTH=10", start="16010101T030000")
    + _part("FREQ=YEARLY;BYDAY=-1SU;BYMONTH=3", start="16010101T020000", kind="DAYLIGHT"),
    _part("FREQ=YEARLY;BYMONTH=11;BYMONTHDAY=1,2,3,4,5,6,7;BYDAY=SU")
    + _part("FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=8,9,10,11,12,13,14;BYDAY=SU", kind="DAYLIGHT"),
    _part("FREQ=YEARLY;BYMONTH=10;BYDAY=1SU;UNTIL=20070101T000000Z"),
    _part("FREQ=MONTHLY;BYMONTHDAY=-1"),
    _part(extra="RDATE:19900301T020000,19910301T020000\r\n"),
    _part(),
])
def test_real_zone_shapes_are_kept(parts):
    from mailr.calendar import zones

    vtz = _zone(parts)
    assert zones.zone_problem(vtz) is None
    assert type(zones.build(vtz)).__name__ == "_tzicalvtz"


def test_resolve_clean_and_fix_zones_use_the_limits():
    from icalendar import Calendar

    from mailr.calendar import fix_zones, ical, tz, zones

    start = datetime(2026, 11, 2, 10)
    text = _bomb_ics("u1", "o@example.com", "me@example.com", start, datetime(1970, 1, 1), tzid="Mallory Zone")
    cal = Calendar.from_ical(text)
    with rrule_periods(limit=ZONE_WORK):
        z = tz.resolve_tz("Mallory Zone", cal)
        assert datetime(2026, 11, 2, 10, tzinfo=z).utcoffset() == timedelta(0)
    assert zones.problems(cal) == ["Mallory Zone"]
    assert fix_zones.needs_fix(text)
    out = ical.serialize(cal)
    assert "FREQ=MINUTELY" not in out and "TZOFFSETTO:+0000" in out
    assert not fix_zones.needs_fix(out)

    # More than MAX_ZONES zones in one object: the rest become fixed offsets; unused ones go.
    many = "".join(_bomb_zone(f"Z{i}", datetime(2020, 1, 1), freq="YEARLY") for i in range(zones.MAX_ZONES + 5))
    evs = "".join(f"BEGIN:VEVENT\r\nUID:m{i}\r\nDTSTAMP:20261001T000000Z\r\nDTSTART;TZID=Z{i}:20261102T100000\r\n"
                  "END:VEVENT\r\n" for i in range(zones.MAX_ZONES + 3))
    cal = Calendar.from_ical(f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\n{many}{evs}END:VCALENDAR\r\n")
    replaced = zones.clean(cal)
    assert len(replaced) == 3 and len(list(cal.walk("VTIMEZONE"))) == zones.MAX_ZONES + 3


async def test_an_invitation_part_is_capped_and_parsed_off_the_loop():
    from mailr.calendar import inbound, itip, zones

    big = b"BEGIN:VCALENDAR\r\n" + b"X-PAD:" + b"x" * zones.MAX_ITIP_BYTES + b"\r\nEND:VCALENDAR\r\n"
    assert itip.summarize(big, set()) is None
    assert await inbound.parse_incoming(big) is None


@radicale
async def test_auto_add_and_organizer_update_store_no_bomb(client, user):
    """`calendar_add_new_invites` on, a verified organizer: the invitation is added with a fixed
    offset in place of its zone; the organizer's update brings a bomb again and it is replaced again."""
    from mailr.calendar import inbound

    await _connect(client)
    me = await _identity(user)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.settings = {**(u.settings or {}), "calendar_add_new_invites": "needs_action"}
        await db.commit()
    org = "chair@acme.example"
    auth = "mx.example; dkim=pass header.d=acme.example; dmarc=pass header.from=acme.example"
    uid = f"autobomb-{uuid.uuid4().hex[:8]}"
    start = (datetime.now(UTC) + timedelta(days=4)).replace(hour=10, minute=0, second=0, microsecond=0, tzinfo=None)
    for seq, subject in ((0, "Invitation"), (1, "Updated")):
        ics = _bomb_ics(uid, org, me, start, datetime(1970, 1, 1)).replace("SEQUENCE:0", f"SEQUENCE:{seq}")
        raw = _mail(f"Chair <{org}>", me, ics, auth=auth, subject=subject)
        mid = await _ingest(user, raw)
        async with sessionmaker()() as db:
            u = await db.get(User, user.id)
            with rrule_periods(limit=ZONE_WORK):
                out = await inbound.handle(db, u, await db.get(Message, mid), raw)
            await db.commit()
        assert out["applied"], out
        obj = await _stored(user, uid)
        assert "FREQ=MINUTELY" not in obj.ics and "TZOFFSETTO:+0000" in obj.ics, (seq, obj.ics)


# ---------------- Calendar links ----------------


@pytest.mark.serial  # measures event-loop lag
@radicale
async def test_a_calendar_link_is_parsed_off_the_loop_and_keeps_no_bomb(client, user, monkeypatch):
    from helpers.security_calendar_limits import _LagProbe

    from mailr.calendar import webcal

    start = (datetime.now(UTC) + timedelta(days=4)).replace(hour=10, minute=0, second=0, microsecond=0, tzinfo=None)
    bomb = _bomb_ics("feed-bomb", "feed@evil.example", "x@example.com", start, datetime(1970, 1, 1),
                     extra="BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:sign in at evil\r\nTRIGGER:-PT5M\r\nEND:VALARM\r\n"
                           "X-MAILR-NO-REMINDER:TRUE\r\n")
    bomb_body = bomb.split("METHOD:REQUEST\r\n", 1)[1].rsplit("END:VCALENDAR", 1)[0]
    filler = "".join(f"BEGIN:VEVENT\r\nUID:e{i}\r\nDTSTAMP:20261001T000000Z\r\nDTSTART:20261101T100000Z\r\n"
                     f"DTEND:20261101T110000Z\r\nSUMMARY:{'x' * 200}\r\nEND:VEVENT\r\n" for i in range(50))
    body = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\n" + bomb_body + filler + "END:VCALENDAR\r\n").encode()

    async def fake_fetch(url, **kw):
        return 200, body, {}

    # The parse is made slow (1.5 s, the length of the stall a hostile zone caused) instead of
    # big: on the event loop it would show as lag; in its thread it does not. A file of 9,000 events
    # did the same with about 60 s of writes (tests/test_scale_webcal.py covers that size).
    real_parse = webcal.parse
    threads: list[str] = []

    def slow_parse(b):
        threads.append(threading.current_thread().name)
        time.sleep(1.5)
        return real_parse(b)

    monkeypatch.setattr(webcal, "fetch", fake_fetch)
    monkeypatch.setattr(webcal, "parse", slow_parse)
    async with _LagProbe() as probe:
        r = await client.post("/api/v1/calendar/subscriptions", json={"url": "https://feeds.evil.example/cal.ics"})
    assert r.status_code == 200, r.text
    assert threads and threads[0] != threading.main_thread().name
    assert probe.lag < 1.0, probe.lag
    obj = await _stored(user, "feed-bomb")
    assert "FREQ=MINUTELY" not in obj.ics and "TZOFFSETTO:+0000" in obj.ics
    assert "VALARM" not in obj.ics and "X-MAILR-NO-REMINDER" not in obj.ics  # no sender alarms from feeds either


async def test_a_calendar_link_is_https_and_never_downgrades(monkeypatch):
    import httpx

    from mailr.calendar import webcal
    from mailr.core import netguard
    from mailr.core.netguard import UnsafeTarget

    with pytest.raises(UnsafeTarget):
        await webcal.check_scheme("http://feeds.example.com/cal.ics", private_ok=False)
    with pytest.raises(UnsafeTarget):  # an admin's LAN permission covers LAN hosts only
        await webcal.check_scheme("http://8.8.8.8/cal.ics", private_ok=True)
    await webcal.check_scheme("http://127.0.0.1/cal.ics", private_ok=True)  # a LAN feed the admin allows
    await webcal.check_scheme("https://feeds.example.com/cal.ics", private_ok=False)
    with pytest.raises(UnsafeTarget):
        await webcal.check_scheme("http://127.0.0.1/cal.ics", private_ok=True, came_from="https://feeds.example.com/x")

    async def no_check(url, **kw):
        return None

    seen: list[str] = []

    async def fake_capped(client, method, url, **kw):
        seen.append(url)
        if url.startswith("https://"):
            return httpx.Response(302, headers={"location": "http://127.0.0.1/cal.ics"},
                                  request=httpx.Request("GET", url)), b""
        return httpx.Response(200, request=httpx.Request("GET", url)), b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"

    monkeypatch.setattr(netguard, "check_url", no_check)
    monkeypatch.setattr(netguard, "fetch_capped", fake_capped)
    with pytest.raises(UnsafeTarget):
        await webcal.fetch("https://feeds.example.com/cal.ics", private_ok=True)
    assert seen == ["https://feeds.example.com/cal.ics"]  # the http hop was never requested


async def test_a_failing_link_backs_off(monkeypatch, user):
    from mailr.calendar import webcal

    queued: list[int] = []

    async def fake_enqueue(db, kind, payload, **kw):
        queued.append(payload["connector_id"])

    monkeypatch.setattr(webcal, "enqueue", fake_enqueue)
    now = datetime.now(UTC)
    async with sessionmaker()() as db:
        conns = []
        for fails, ago in ((1, 25), (3, 30), (3, 97), (20, 169)):
            c = Connector(user_id=user.id, kind="webcal", name=f"f{fails}-{ago}", mode="import", enabled=True,
                          status="error", config={"url": "https://x.example/c.ics", "refresh_hours": 12},
                          state={"fails": fails}, last_sync_at=now - timedelta(hours=ago))
            db.add(c)
            conns.append(c)
        await db.commit()
        ids = [c.id for c in conns]
    await webcal.due_refreshes()
    # 1 failure: a day; 3 in a row: 4 days; never more than a week.
    assert [i for i in ids if i in queued] == [ids[0], ids[2], ids[3]]
    async with sessionmaker()() as db:
        for i in ids:
            await db.delete(await db.get(Connector, i))
        await db.commit()


def test_the_parse_is_capped():
    from mailr.calendar import webcal

    with pytest.raises(ValueError):
        webcal.parse(b"BEGIN:VCALENDAR\r\n" + b"BEGIN:VEVENT\r\nEND:VEVENT\r\n" * (webcal.MAX_EVENTS + 1)
                      + b"END:VCALENDAR\r\n")
    with pytest.raises(ValueError):
        webcal.parse(b"BEGIN:VCALENDAR\r\n" + b"x" * (webcal.MAX_BYTES + 1))


# ---------------- The sender's alarms and X-MAILR-* ----------------


def _plain_ics(uid: str, organizer: str, me: str, start: datetime, *, seq: int = 0, extra: str = "",
               alarms: int = 0) -> str:
    al = "".join(f"BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Your mailbox is full: sign in again at "
                 f"https://mailr-login.evil.example\r\nTRIGGER:-PT{m + 1}M\r\nEND:VALARM\r\n" for m in range(alarms))
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\nMETHOD:REQUEST\r\n"
            f"BEGIN:VEVENT\r\nUID:{uid}\r\nSEQUENCE:{seq}\r\nDTSTAMP:{_stamp(datetime.now(UTC))}Z\r\n"
            f"DTSTART:{_stamp(start)}Z\r\nDTEND:{_stamp(start + timedelta(hours=1))}Z\r\n"
            f"SUMMARY:Quarterly review\r\n{extra}"
            f"ORGANIZER:mailto:{organizer}\r\nATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;X-MAILR-REPLY-STAMP=29991231T000000Z:mailto:{me}\r\n"
            f"{al}END:VEVENT\r\nEND:VCALENDAR\r\n")


@radicale
async def test_the_senders_alarms_and_x_mailr_never_reach_my_copy(client, user):
    from mailr.calendar import inbound, planner
    from mailr.calendar.tz import user_zone

    await _connect(client)
    me = await _identity(user)
    org = "chair@acme.example"
    auth = "mx.example; dkim=pass header.d=acme.example; dmarc=pass header.from=acme.example"
    start = (datetime.now(UTC) + timedelta(days=2)).replace(minute=0, second=0, microsecond=0, tzinfo=None)

    async def plan(uid: str) -> list:
        async with sessionmaker()() as db:
            u = await db.get(User, user.id)
            obj = await _stored(user, uid)
            return planner.plan_event(obj.ics, uid, planner.settings_of(u), start.replace(tzinfo=UTC) - timedelta(hours=2),
                                      await user_zone(db, u), {me})

    async def update(uid: str, seq: int, **kw) -> dict:
        raw = _mail(f"Chair <{org}>", me, _plain_ics(uid, org, me, start, seq=seq, **kw), auth=auth, subject="Updated")
        mid2 = await _ingest(user, raw)
        async with sessionmaker()() as db:
            u = await db.get(User, user.id)
            out = await inbound.handle(db, u, await db.get(Message, mid2), raw)
            await db.commit()
        assert out["applied"], out
        return out

    # 1. Accept an invitation with 60 alarms and mailr's own flags: none of them is stored.
    uid = f"alarms-{uuid.uuid4().hex[:8]}"
    mid = await _ingest(user, _mail(f"Chair <{org}>", me, _plain_ics(uid, org, me, start, alarms=60,
                                                                     extra="X-MAILR-NO-REMINDER:TRUE\r\n"), auth=auth))
    r = await client.post("/api/v1/calendar/invite-reply", json={"message_id": mid, "response": "accepted"})
    assert r.status_code == 200 and r.json()["calendar"] == "added", r.text
    await _cancel_outbox(client, r)
    obj = await _stored(user, uid)
    assert "BEGIN:VALARM" not in obj.ics and "mailr-login.evil.example" not in obj.ics
    assert "X-MAILR" not in obj.ics
    assert [p.source for p in await plan(uid)] == ["default"]  # my default reminder, nothing else

    # 2. The organizer's update adds X-MAILR-NO-REMINDER and alarms: my default reminder stays.
    await update(uid, 1, alarms=3, extra="X-MAILR-NO-REMINDER:TRUE\r\n")
    obj = await _stored(user, uid)
    assert "X-MAILR-NO-REMINDER" not in obj.ics and "BEGIN:VALARM" not in obj.ics
    assert [p.source for p in await plan(uid)] == ["default"]

    # 3. My own reminder (and my own "no reminders") survive the organizer's next update.
    r = await client.patch(f"/api/v1/calendar/events/{uid}", json={"reminders": [25]})
    assert r.status_code == 200, r.text
    await update(uid, 10, alarms=5)  # my edit raised my SEQUENCE
    obj = await _stored(user, uid)
    assert obj.ics.count("BEGIN:VALARM") == 1 and "TRIGGER:-PT25M" in obj.ics
    r = await client.patch(f"/api/v1/calendar/events/{uid}", json={"reminders": []})
    assert r.status_code == 200, r.text
    await update(uid, 20, alarms=2)
    obj = await _stored(user, uid)
    assert "X-MAILR-NO-REMINDER:TRUE" in obj.ics and "BEGIN:VALARM" not in obj.ics
    assert await plan(uid) == []


def test_strip_sender_extras():
    from icalendar import Calendar

    from mailr.calendar import ical

    cal = Calendar.from_ical(_plain_ics("s1", "o@example.com", "me@example.com", datetime(2026, 11, 2, 10), alarms=2,
                                        extra="X-MAILR-NO-REMINDER:TRUE\r\nX-MAILR-OCCURRENCE:x\r\nX-OTHER:kept\r\n"))
    ev = next(iter(cal.walk("VEVENT")))
    ical.strip_sender_extras(ev)
    text = cal.to_ical().decode()
    assert "VALARM" not in text and "X-MAILR" not in text and "X-OTHER:kept" in text


# ---------------- COUNTER needs a verified guest ----------------


@radicale
async def test_an_unverified_counter_moves_nothing_without_trust(client, user):
    """A forged COUNTER (no Authentication-Results at all): shown as unverified with the
    address, Accept refused (409) until the user trusts it, and who gets mail is shown first."""
    from helpers.calendar_organizer import DANA, EVAN, _cal, _itip, _outbox, _setup, _start
    from helpers.calendar_organizer import _ingest as org_ingest

    me, _ = await _setup(client, user)
    start = _start(6)
    ev = (await client.post("/api/v1/calendar/events", json={"summary": "Budget review", "start": start.isoformat(),
                                                             "attendees": [DANA, EVAN]})).json()
    uid = ev["uid"]
    moved = start + timedelta(days=1, hours=3)
    counter = _cal(f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261001T120000Z\r\nSEQUENCE:0\r\n"
                   f"DTSTART:{moved:%Y%m%dT%H%M%SZ}\r\nDTEND:{moved + timedelta(hours=1):%Y%m%dT%H%M%SZ}\r\n"
                   f"ORGANIZER:mailto:{me}\r\nATTENDEE;PARTSTAT=TENTATIVE:mailto:{DANA}\r\n"
                   "COMMENT:Can we move it? I have a conflict.\r\nEND:VEVENT\r\n", "COUNTER")
    forged = (f"From: Dana <{DANA}>\r\nTo: {me}\r\nSubject: New time proposed: Budget review\r\n"
              f"Message-ID: <{uuid.uuid4().hex}@mallory.example>\r\nDate: Thu, 01 Oct 2026 12:00:00 +0000\r\n"
              'MIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary="b1"\r\n\r\n'
              "--b1\r\nContent-Type: text/plain\r\n\r\nNew time?\r\n"
              f"--b1\r\nContent-Type: text/calendar; charset=utf-8; method=COUNTER\r\n\r\n{counter}--b1--\r\n").encode()
    mid = await org_ingest(user, forged)
    out = await _itip(user, mid)
    assert out["counter"] is True and out["verified"] is False and out["sender"] == DANA
    assert DANA in out["detail"] and "unverified" in out["detail"]

    info = (await client.get(f"/api/v1/messages/{mid}/invite")).json()
    assert info["counter_check"] == {"verified": False, "sender": DANA, "notify": sorted([DANA, EVAN])}

    n = len(await _outbox(user))
    r = await client.post(f"/api/v1/calendar/events/{uid}/counter/accept", json={"message_id": mid})
    assert r.status_code == 409 and r.json()["code"] == "calendar.proposal_unverified", r.text
    assert r.json()["params"]["sender"] == DANA
    r = await client.post(f"/api/v1/calendar/events/{uid}/counter/accept",
                          json={"message_id": mid, "trust": True, "notify": [DANA]})
    assert r.status_code == 409 and r.json()["code"] == "calendar.proposal_recipients_changed", r.text
    assert len(await _outbox(user)) == n
    got = (await client.get(f"/api/v1/calendar/events/{uid}")).json()
    assert got["start"] == start.isoformat()  # not moved

    r = await client.post(f"/api/v1/calendar/events/{uid}/counter/accept",
                          json={"message_id": mid, "trust": True, "notify": [EVAN, DANA]})
    assert r.status_code == 200, r.text
    assert r.json()["start"] == moved.isoformat()
    [ob] = (await _outbox(user))[n:]
    assert sorted(ob.recipients) == sorted([DANA, EVAN])


@radicale
async def test_a_verified_counter_still_applies_and_tokens_cannot_trust(client, user):
    from helpers.calendar_organizer import DANA, _cal, _itip, _itip_mail, _setup, _start
    from helpers.calendar_organizer import _ingest as org_ingest

    me, _ = await _setup(client, user)
    start = _start(7)
    uid = (await client.post("/api/v1/calendar/events", json={"summary": "Budget review", "start": start.isoformat(),
                                                              "attendees": [DANA]})).json()["uid"]
    moved = start + timedelta(hours=2)
    counter = _cal(f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261001T120000Z\r\nSEQUENCE:0\r\n"
                   f"DTSTART:{moved:%Y%m%dT%H%M%SZ}\r\nDTEND:{moved + timedelta(hours=1):%Y%m%dT%H%M%SZ}\r\n"
                   f"ORGANIZER:mailto:{me}\r\nATTENDEE;PARTSTAT=TENTATIVE:mailto:{DANA}\r\nEND:VEVENT\r\n", "COUNTER")
    mid = await org_ingest(user, _itip_mail(counter, DANA, me, method="COUNTER"))
    out = await _itip(user, mid)
    assert out["verified"] is True and out["detail"] == "Proposed a new time"
    info = (await client.get(f"/api/v1/messages/{mid}/invite")).json()
    assert info["counter_check"] == {"verified": True, "sender": DANA, "notify": [DANA]}
    r = await client.post(f"/api/v1/calendar/events/{uid}/counter/accept", json={"message_id": mid, "notify": [DANA]})
    assert r.status_code == 200 and r.json()["start"] == moved.isoformat(), r.text
