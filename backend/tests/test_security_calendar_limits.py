"""Calendar integrations: regression tests.

- A repeat rule that generates too many occurrences (sub-daily, DTSTART far in the past)
  never stalls the worker: inbound updates and imports refuse it, a synced copy shows only its
  first occurrence, and expansion runs off the event loop.
- Incoming iTIP changes apply on their own only with positive, aligned authentication from
  a trusted receiver, and never for a message with more than one From address.
- An organizer's update cannot make me the organizer or carry components of another event.
- Guest mail goes only to the recipients the approval (and the first-message rule) saw.
- DAV Basic credentials never follow a redirect to another origin.
"""

import contextlib
import time
import uuid
from datetime import UTC, timedelta

import httpx
import pytest
from dev_services import RADICALE
from helpers.calendar import _token_client
from helpers.calendar_organizer import (
    DANA,
    _cal,
    _ingest,
    _itip,
    _outbox,
    _put_invitation,
    _setup,
    _start,
    _stored,
    _vevent,
    radicale,
)
from helpers.security_calendar_limits import GOOD_AR, _LagProbe, _mail
from helpers.work import rrule_periods
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import CalCollection, CalOccurrence

ALICE = "Alice <alice@acme.example>"


def _reply(uid, attendee, partstat, organizer, stamp):
    return _cal(f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:{stamp}\r\nSEQUENCE:0\r\nDTSTART:20261005T090000Z\r\n"
                f"ORGANIZER:mailto:{organizer}\r\nATTENDEE;PARTSTAT={partstat}:mailto:{attendee}\r\nEND:VEVENT\r\n",
                "REPLY")


# ---------------- Costly repeat rules ----------------


def test_expansion_of_a_costly_rule_is_bounded():
    from icalendar import Calendar

    from mailr.calendar import expand

    for rule, start in (("FREQ=HOURLY", "19000101T090000Z"), ("FREQ=HOURLY;UNTIL=20300101T000000Z", "19000101T090000Z"),
                        ("FREQ=MINUTELY", "20200101T090000Z"), ("FREQ=SECONDLY", "20260101T090000Z")):
        cal = Calendar.from_ical(_cal(f"BEGIN:VEVENT\r\nUID:x\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:{start}\r\n"
                                      f"DTEND:{start[:-5]}1500Z\r\nRRULE:{rule}\r\nEND:VEVENT\r\n"))
        assert expand.costly(cal), rule
        lo, hi = expand.window()
        # The rule periods the expansion scans (helpers/work.py). Walked from DTSTART, these rules
        # scan hundreds of thousands to millions; an ordinary weekday rule from 2000 scans 1,500.
        with rrule_periods(limit=20_000):
            insts, _ = expand.instances(cal, lo, hi, UTC)
        assert len(insts) <= expand.CAP
        if start < "2025":
            assert len(insts) <= 1, rule
    # Ordinary rules still expand.
    cal = Calendar.from_ical(_cal("BEGIN:VEVENT\r\nUID:y\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:20000103T090000Z\r\n"
                                  "DTEND:20000103T100000Z\r\nRRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR\r\nEND:VEVENT\r\n"))
    assert not expand.costly(cal)
    lo, hi = expand.window()
    assert len(expand.instances(cal, lo, hi, UTC)[0]) > 500


@pytest.mark.serial  # asserts event-loop lag: other workers would add their own CPU load
@radicale
async def test_organizer_update_with_a_costly_rule_is_refused_quickly(client, user):
    me, username = await _setup(client, user)
    for rule in ("RRULE:FREQ=HOURLY\r\n", "RRULE:FREQ=HOURLY;UNTIL=20300101T000000Z\r\n"):
        uid = f"alice-{uuid.uuid4().hex[:8]}"
        await _put_invitation(user, username, uid, me)
        upd = _cal(_vevent(uid=uid, seq=1, start="19000101T090000Z", end="19000101T091500Z",
                           organizer="alice@acme.example", guests=(me,), alarm=False, extra=rule), "REQUEST")
        t0 = time.monotonic()
        async with _LagProbe() as probe:
            mid = await _ingest(user, _mail(upd, ALICE, me, method="REQUEST", auth_header=GOOD_AR))
        assert time.monotonic() - t0 < 2
        assert probe.lag < 1, probe.lag
        out = await _itip(user, mid)
        assert not out["applied"] and "repeat" in out["detail"], out
        assert (await _stored(user, uid)).sequence == 0


@pytest.mark.serial  # asserts event-loop lag: other workers would add their own CPU load
@radicale
async def test_import_of_a_costly_rule_is_refused_quickly(client, user):
    await _setup(client, user)
    ics = _cal("BEGIN:VEVENT\r\nUID:imp-costly-1\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:19000101T090000Z\r\n"
               "DTEND:19000101T091500Z\r\nRRULE:FREQ=HOURLY\r\nSUMMARY:x\r\nEND:VEVENT\r\n"
               "BEGIN:VEVENT\r\nUID:imp-fine-1\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:20261005T090000Z\r\n"
               "DTEND:20261005T100000Z\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\nSUMMARY:y\r\nEND:VEVENT\r\n")
    t0 = time.monotonic()
    async with _LagProbe() as probe:
        r = await client.post("/api/v1/calendar/import", json={"ics": ics})
    assert time.monotonic() - t0 < 2 and probe.lag < 1, (time.monotonic() - t0, probe.lag)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [i["uid"] for i in body["imported"]] == ["imp-fine-1"]
    assert [f["uid"] for f in body["failed"]] == ["imp-costly-1"]


@pytest.mark.serial  # asserts event-loop lag: other workers would add their own CPU load
@radicale
async def test_synced_costly_rule_shows_one_occurrence_quickly(client, user):
    from mailr.calendar import mirror
    from mailr.calendar.dav import DavClient

    _, username = await _setup(client, user)
    async with sessionmaker()() as db:
        await mirror.ensure_synced(db, user.id)
        coll = (await db.execute(select(CalCollection).where(CalCollection.user_id == user.id))).scalars().first()
        await db.commit()
    uid = f"sync-{uuid.uuid4().hex[:8]}"
    ics = _cal(f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:19000101T090000Z\r\n"
               "DTEND:19000101T091500Z\r\nRRULE:FREQ=HOURLY\r\nSUMMARY:x\r\nEND:VEVENT\r\n")
    async with DavClient(RADICALE, username, "x") as d:
        await d.put(coll.href + uid + ".ics", ics, if_none_match=True)
    t0 = time.monotonic()
    async with _LagProbe() as probe, sessionmaker()() as db:
        await mirror.ensure_synced(db, user.id, force=True)
        await db.commit()
    assert time.monotonic() - t0 < 2 and probe.lag < 1, (time.monotonic() - t0, probe.lag)
    obj = await _stored(user, uid)
    async with sessionmaker()() as db:
        n = len((await db.execute(select(CalOccurrence).where(CalOccurrence.object_id == obj.id))).scalars().all())
    assert n <= 1
    # Reads outside the window do not re-expand the whole rule either.
    t0 = time.monotonic()
    r = await client.get("/api/v1/calendar/agenda", params={"from": "2010-01-01", "to": "2010-02-01"})
    assert r.status_code == 200 and time.monotonic() - t0 < 2


# ---------------- The iTIP authentication gate ----------------

TWO_FROM_AR = "mx.example.com; spf=pass; dkim=pass header.d=attacker.example; dmarc=pass header.from=attacker.example"
CASES = {
    "a_no_header": (ALICE, None, ""),
    "b_dmarc_none": (ALICE, "mx.example.com; spf=fail; dkim=none; dmarc=none", ""),
    "c_dkim_not_aligned": (ALICE, "mx.example.com; spf=pass; dkim=pass header.d=attacker.example", ""),
    "d_foreign_authserv": (ALICE, "attacker.example; spf=pass; dkim=pass header.d=acme.example; dmarc=pass", ""),
    "e_two_from_addresses": ("Alice <alice@acme.example>, x@attacker.example",
                             TWO_FROM_AR, ""),
    "e_two_from_headers": (ALICE, TWO_FROM_AR, "From: x@attacker.example\r\n"),
    "f_dmarc_pass_for_another_domain": (ALICE, "mx.example.com; spf=pass; dmarc=pass header.from=attacker.example", ""),
}


@radicale
async def test_unauthenticated_itip_changes_nothing(client, user):
    me, username = await _setup(client, user)
    start = _start()
    r = await client.post("/api/v1/calendar/events", json={
        "summary": "Budget review", "start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(),
        "attendees": [DANA]})
    mine = r.json()["uid"]
    for n, (case, (frm, ar, extra)) in enumerate(CASES.items()):
        dana = frm.replace("Alice <alice@acme.example>", f"Dana <{DANA}>").replace("acme.example", "guest.example")
        rep = _reply(mine, DANA, "DECLINED", me, f"20261001T12{n:02d}00Z")
        out = await _itip(user, await _ingest(user, _mail(rep, dana, me, method="REPLY",
                                                          auth_header=ar and ar.replace("acme.example", "guest.example"),
                                                          extra_headers=extra)))
        assert not out["applied"], (case, out)
        uid = f"alice-{uuid.uuid4().hex[:8]}"
        await _put_invitation(user, username, uid, me)
        upd = _cal(_vevent(uid=uid, seq=3, start="20261005T130000Z", end="20261005T140000Z",
                           organizer="alice@acme.example", guests=(me,), alarm=False), "REQUEST")
        out = await _itip(user, await _ingest(user, _mail(upd, frm, me, method="REQUEST", auth_header=ar,
                                                          extra_headers=extra)))
        assert not out["applied"], (case, out)
        cancel = _cal(_vevent(uid=uid, seq=4, organizer="alice@acme.example", guests=(me,),
                              extra="STATUS:CANCELLED\r\n", alarm=False), "CANCEL")
        out = await _itip(user, await _ingest(user, _mail(cancel, frm, me, method="CANCEL", auth_header=ar,
                                                          extra_headers=extra)))
        assert not out["applied"], (case, out)
        obj = await _stored(user, uid)
        assert obj.sequence == 0 and obj.status != "CANCELLED", case
    obj = await _stored(user, mine)
    assert {a["email"]: a.get("partstat") for a in obj.attendees}[DANA] != "DECLINED"


def test_more_than_one_sender_is_never_verified():
    from mailr.calendar.inbound import _senders

    assert _senders(b"From: a@x.example\r\n\r\nbody") == ["a@x.example"]
    assert _senders(b"From: a@x.example, b@y.example\r\n\r\nbody") == ["a@x.example", "b@y.example"]
    assert _senders(b"From: a@x.example\r\nFrom: b@y.example\r\n\r\nbody") == ["a@x.example", "b@y.example"]


# ---------------- A REQUEST cannot make me the organizer ----------------


@radicale
async def test_request_cannot_make_me_the_organizer(client, user):
    me, username = await _setup(client, user)
    victims = ("v1@victim.example", "v2@victim.example")
    atts = "".join(f"ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:{v}\r\n" for v in victims)

    def override(uid, org="alice@acme.example"):
        return (f"BEGIN:VEVENT\r\nUID:{uid}\r\nRECURRENCE-ID:20261012T090000Z\r\nDTSTAMP:20261001T100000Z\r\n"
                "DTSTART:20261012T090000Z\r\nDTEND:20261012T100000Z\r\nSEQUENCE:1\r\nSUMMARY:x\r\n"
                f"ORGANIZER:mailto:{org}\r\nEND:VEVENT\r\n")

    def master(uid, org):
        o = f"ORGANIZER:mailto:{org}\r\n" if org else ""
        return (f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261001T100000Z\r\nDTSTART:20261005T090000Z\r\n"
                "DTEND:20261005T100000Z\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\nSEQUENCE:1\r\nSUMMARY:Spam at evil.example\r\n"
                f"{o}ATTENDEE;PARTSTAT=ACCEPTED:mailto:{me}\r\n{atts}END:VEVENT\r\n")

    variants = {
        "master_names_me": lambda uid: override(uid) + master(uid, me),
        "master_without_organizer": lambda uid: override(uid) + master(uid, ""),
        "component_of_another_event": lambda uid: override(uid) + master(uid, "alice@acme.example").replace(
            f"UID:{uid}\r\nDTSTAMP", "UID:other-event\r\nDTSTAMP", 1),
        "two_masters": lambda uid: master(uid, "alice@acme.example") + master(uid, "alice@acme.example"),
    }
    for name, build in variants.items():
        uid = f"alice-{uuid.uuid4().hex[:8]}"
        await _put_invitation(user, username, uid, me)
        mid = await _ingest(user, _mail(_cal(build(uid), "REQUEST"), ALICE, me, method="REQUEST", auth_header=GOOD_AR))
        out = await _itip(user, mid)
        assert not out["applied"], (name, out)
        obj = await _stored(user, uid)
        assert not obj.is_organizer and obj.organizer == "alice@acme.example", name
        r = await client.patch(f"/api/v1/calendar/events/{uid}", json={"location": "Room 9"})
        assert r.status_code == 200, r.text
        assert not any(set(victims) & set(o.recipients) for o in await _outbox(user)), name


# ---------------- Guest mail goes only to the approved recipients ----------------


async def _put_raw(user, username, uid, ics):
    from mailr.calendar import mirror
    from mailr.calendar.dav import DavClient

    async with sessionmaker()() as db:
        await mirror.ensure_synced(db, user.id)
        coll = (await db.execute(select(CalCollection).where(CalCollection.user_id == user.id))).scalars().first()
        await db.commit()
    async with DavClient(RADICALE, username, "x") as d:
        await d.put(coll.href + uid + ".ics", ics, if_none_match=True)
    # Setup, not the subject: wait for the sync rather than the 3 s read budget, which a loaded
    # parallel run can exceed (the sync then finishes in the background, after _stored looked).
    from mailr.calendar import sync

    await sync.run_inline(coll.connector_id, budget=60)
    return coll


@radicale
async def test_override_only_guest_is_on_the_card(client, user):
    from mailr.core.permissions import execute_job

    me, username = await _setup(client, user)
    uid = f"mine-{uuid.uuid4().hex[:8]}"
    mallory = "mallory@evil.example"
    ics = _cal(
        f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261001T100000Z\r\nDTSTART:20261005T090000Z\r\n"
        "DTEND:20261005T100000Z\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\nSUMMARY:Sync\r\n"
        f"ORGANIZER:mailto:{me}\r\nATTENDEE;PARTSTAT=ACCEPTED:mailto:{me}\r\n"
        f"ATTENDEE;PARTSTAT=ACCEPTED:mailto:{DANA}\r\nEND:VEVENT\r\n"
        f"BEGIN:VEVENT\r\nUID:{uid}\r\nRECURRENCE-ID:20261012T090000Z\r\nDTSTAMP:20261001T100000Z\r\n"
        "DTSTART:20261012T090000Z\r\nDTEND:20261012T100000Z\r\nSUMMARY:Sync\r\n"
        f"ORGANIZER:mailto:{me}\r\nATTENDEE;PARTSTAT=ACCEPTED:mailto:{me}\r\n"
        f"ATTENDEE;PARTSTAT=ACCEPTED:mailto:{DANA}\r\nATTENDEE;PARTSTAT=ACCEPTED:mailto:{mallory}\r\nEND:VEVENT\r\n")
    await _put_raw(user, username, uid, ics)
    agent = await _token_client(client, ["read", "calendar", "send"])
    try:
        r = await agent.patch(f"/api/v1/calendar/events/{uid}", json={"summary": "Sync (moved agenda)"})
        assert r.status_code == 202, r.text
        appr = r.json()["approval"]
        assert mallory in appr["preview"]["recipients"] and DANA in appr["preview"]["recipients"]
        assert any(mallory in ln for ln in appr["preview"]["lines"])
        r = await client.post(f"/api/v1/approvals/{appr['id']}/decide", json={"decision": "approve"})
        assert r.status_code == 200, r.text
        await execute_job({"pending_id": appr["id"]}, None)
        obs = await _outbox(user)
        assert obs and set(obs[-1].recipients) <= set(appr["preview"]["recipients"])
    finally:
        await agent.aclose()


@radicale
async def test_guests_added_elsewhere_meanwhile_are_not_mailed(client, user):
    """The mirror is stale: another client added Erin on the server. My edit hits 412, is redone
    on the fresh copy, and mails only the guests my request was checked against."""
    from mailr.calendar.dav import DavClient

    me, username = await _setup(client, user)
    erin = "erin@guest.example"
    uid = f"mine-{uuid.uuid4().hex[:8]}"
    base = (f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261001T100000Z\r\nDTSTART:20261005T090000Z\r\n"
            "DTEND:20261005T100000Z\r\nSEQUENCE:0\r\nSUMMARY:Sync\r\n"
            f"ORGANIZER:mailto:{me}\r\nATTENDEE;PARTSTAT=ACCEPTED:mailto:{me}\r\n"
            f"ATTENDEE;PARTSTAT=ACCEPTED:mailto:{DANA}\r\n__ERIN__END:VEVENT\r\n")
    coll = await _put_raw(user, username, uid, _cal(base.replace("__ERIN__", "")))
    obj = await _stored(user, uid)
    async with DavClient(RADICALE, username, "x") as d:
        await d.put(obj.href, _cal(base.replace("__ERIN__", f"ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:{erin}\r\n")),
                    if_match=obj.etag)
    assert coll is not None
    r = await client.patch(f"/api/v1/calendar/events/{uid}", json={"summary": "Sync (new agenda)"})
    assert r.status_code == 200, r.text
    rcpts = {e for o in await _outbox(user) for e in o.recipients}
    assert DANA in rcpts and erin not in rcpts, rcpts


# ---------------- DAV credentials stay with their origin ----------------
# A redirect to another port and address: tests/test_security_outbound.py
# (test_dav_redirect_to_another_origin_gets_no_credentials).


async def test_basic_auth_does_not_follow_a_cross_origin_redirect(monkeypatch):
    from mailr.calendar import dav
    from mailr.core import netguard

    async def ok(*a, **kw):
        return None

    monkeypatch.setattr(netguard, "check_url", ok)
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.url.host, req.headers.get("authorization")))
        if req.url.host == "dav.example":
            return httpx.Response(302, headers={"location": "https://collector.example/x"})
        return httpx.Response(207, content=b'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"/>')

    c = dav.DavClient("https://dav.example/", "me", "s3cret", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with contextlib.suppress(Exception):  # refusing the hop is fine too
        await c.request("PROPFIND", "https://dav.example/")
    assert seen[0][1] and seen[0][1].startswith("Basic ")
    assert all(not auth for host, auth in seen if host != "dav.example"), seen
