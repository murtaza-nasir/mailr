# ACT-SURFACES: api
"""Organizer invitations and incoming iTIP: organizer.plan cases (pure), then
against the dev Radicale: the outbox MIME of an invitation (text/calendar; method=REQUEST plus
invite.ics), a time change bumping SEQUENCE, a removed guest getting CANCEL, an agent's invite
waiting for one approval, event.cancel, and ingested replies, updates and cancellations, with
the forged and stale ones changing nothing, and an unverified change applied only by the user
(invite.apply_unverified). Nothing is sent: the outbox rows are only built."""

import uuid
from datetime import UTC, datetime, timedelta

from dev_services import RADICALE
from helpers.calendar import _token_client
from helpers.calendar_organizer import (
    DANA,
    EVAN,
    ME,
    _cal,
    _cal_part,
    _ingest,
    _itip,
    _itip_mail,
    _mime,
    _outbox,
    _put_invitation,
    _setup,
    _start,
    _stored,
    _vevent,
    radicale,
)
from icalendar import Calendar

from mailr.calendar import organizer
from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.store import user_store
from mailr.db import sessionmaker
from mailr.models import CalObject, User


def _parse(ics: str) -> Calendar:
    return Calendar.from_ical(ics)


# ---------------- organizer.plan (pure) ----------------


def test_plan_create_sends_request_to_every_guest_without_my_alarms():
    [m] = organizer.plan(None, _cal(_vevent()), {ME})
    assert (m.method, m.kind) == ("REQUEST", "invitation")
    assert [t["email"] for t in m.to] == [DANA, EVAN]
    assert "METHOD:REQUEST" in m.ics and "VALARM" not in m.ics


def test_plan_time_change_sends_update_and_stamp_bumps_sequence_and_resets_answers():
    old = _cal(_vevent(partstat="ACCEPTED"))
    new = _parse(_cal(_vevent(start="20261005T110000Z", end="20261005T120000Z", partstat="ACCEPTED")))
    organizer.stamp(new, _parse(old), ME, "Me", {ME})
    ev = next(iter(new.walk("VEVENT")))
    assert int(ev["sequence"]) == 1
    parts = {str(a).split(":", 1)[1]: a.params["PARTSTAT"] for a in ev["attendee"]}
    assert parts == {ME: "ACCEPTED", DANA: "NEEDS-ACTION", EVAN: "NEEDS-ACTION"}
    [m] = organizer.plan(old, new, {ME})
    assert (m.method, m.kind, [t["email"] for t in m.to]) == ("REQUEST", "update", [DANA, EVAN])
    # A summary-only change keeps the answers but still updates the guests.
    renamed = _parse(_cal(_vevent(partstat="ACCEPTED").replace("Budget review", "Budget review (Q4)")))
    organizer.stamp(renamed, _parse(old), ME, "Me", {ME})
    assert all(a.params["PARTSTAT"] == "ACCEPTED" for a in next(iter(renamed.walk("VEVENT")))["attendee"])
    assert [m.kind for m in organizer.plan(old, renamed, {ME})] == ["update"]


def test_plan_removed_guest_gets_cancel_and_an_added_guest_an_invitation():
    old = _cal(_vevent())
    new = _cal(_vevent(guests=(DANA, "fay@guest.example")))
    msgs = {m.kind: m for m in organizer.plan(old, new, {ME})}
    assert set(msgs) == {"cancel", "invitation"}
    assert [t["email"] for t in msgs["cancel"].to] == [EVAN] and "METHOD:CANCEL" in msgs["cancel"].ics
    assert DANA not in msgs["cancel"].ics  # only the removed guest is named
    assert [t["email"] for t in msgs["invitation"].to] == ["fay@guest.example"]


def test_plan_occurrence_delete_whole_delete_cancel_and_private_changes():
    rule = "RRULE:FREQ=WEEKLY;COUNT=5\r\n"
    old = _cal(_vevent(extra=rule))
    new = _cal(_vevent(extra=rule + "EXDATE:20261012T090000Z\r\n"))
    [m] = organizer.plan(old, new, {ME})
    assert (m.method, m.recurrence_id) == ("CANCEL", "2026-10-12T09:00:00+00:00")
    assert "RECURRENCE-ID:20261012T090000Z" in m.ics and "STATUS:CANCELLED" in m.ics
    [d] = organizer.plan(old, None, {ME})
    assert d.method == "CANCEL" and d.recurrence_id is None and "SEQUENCE:1" in d.ics
    [c] = organizer.plan(old, _cal(_vevent(extra=rule + "STATUS:CANCELLED\r\n")), {ME})
    assert c.method == "CANCEL" and [t["email"] for t in c.to] == [DANA, EVAN]
    # My alarm or colour: nothing for the guests. Someone else's event: never mine to send.
    assert organizer.plan(old, _cal(_vevent(extra=rule, alarm=False)), {ME}) == []
    assert organizer.plan(None, _cal(_vevent(organizer="alice@acme.example")), {ME}) == []


def test_stamp_sets_organizer_me_and_schedule_agent_on_auto_scheduling_servers():
    raw = _parse(_cal("BEGIN:VEVENT\r\nUID:n\r\nDTSTAMP:20260920T100000Z\r\nDTSTART:20261005T090000Z\r\n"
                      f"SUMMARY:x\r\nATTENDEE:mailto:{DANA}\r\nEND:VEVENT\r\n"))
    organizer.stamp(raw, None, ME, "Me", {ME}, auto_schedule=True)
    ev = next(iter(raw.walk("VEVENT")))
    assert str(ev["organizer"]) == f"mailto:{ME}" and ev["organizer"].params["CN"] == "Me"
    atts = {str(a).split(":", 1)[1]: a.params for a in ev["attendee"]}
    assert atts[ME]["PARTSTAT"] == "ACCEPTED" and atts[DANA]["RSVP"] == "TRUE"
    assert all(p["SCHEDULE-AGENT"] == "CLIENT" for p in atts.values())


# ---------------- against Radicale ----------------


async def _server_copy(username: str, obj: CalObject) -> Calendar:
    from mailr.calendar.dav import DavClient

    async with DavClient(RADICALE, username, "x") as d:
        ics, _ = await d.get(obj.href)
    return Calendar.from_ical(ics)


def _partstats(cal: Calendar) -> dict[str, str]:
    ev = next(c for c in cal.walk("VEVENT") if c.get("recurrence-id") is None)
    return {str(a).split(":", 1)[1].lower(): str(a.params.get("PARTSTAT")) for a in ev.get("attendee")}


def _reply(uid: str, attendee: str, partstat: str, *, organizer: str, seq: int = 0,
           stamp: str = "20261001T120000Z") -> str:
    return _cal(f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:{stamp}\r\nSEQUENCE:{seq}\r\nDTSTART:20261005T090000Z\r\n"
                f"ORGANIZER:mailto:{organizer}\r\nATTENDEE;PARTSTAT={partstat}:mailto:{attendee}\r\n"
                "COMMENT:See you there\r\nEND:VEVENT\r\n", "REPLY")


@radicale
async def test_event_invite_sends_request_and_replies_update_the_calendar(client, user):
    """event.invite: a create with guests queues an invitation (text/calendar; method=REQUEST
    and invite.ics); an ingested REPLY sets the guest's PARTSTAT in Radicale and the mirror; a
    time change bumps SEQUENCE and resets the answers; a stale, forged or unknown reply changes
    nothing."""
    me, username = await _setup(client, user)
    start = _start()
    r = await client.post("/api/v1/calendar/events", json={
        "summary": "Budget review", "start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(),
        "location": "Room 4", "reminders": [10],
        "attendees": [{"email": DANA, "name": "Dana"}, EVAN]})
    assert r.status_code == 200, r.text
    ev = r.json()
    assert ev["invites"]["recipients"] == [DANA, EVAN] and len(ev["invites"]["outbox_ids"]) == 1
    assert ev["organizer"] == me
    [ob] = await _outbox(user)
    assert ob.status == "queued" and sorted(ob.recipients) == [DANA, EVAN]
    assert ob.payload["subject"] == "Invitation: Budget review"
    msg = await _mime(user, ob)
    assert msg.get_content_type() == "multipart/mixed"
    method, ics = _cal_part(msg)
    assert method == "REQUEST" and "METHOD:REQUEST" in ics and "VALARM" not in ics
    assert any(p.get_content_type() == "application/ics" and p.get_filename() == "invite.ics" for p in msg.walk())
    assert "When:" in msg.get_body(("plain",)).get_content()
    uid = ev["uid"]
    obj = await _stored(user, uid)
    assert obj.is_organizer and _partstats(await _server_copy(username, obj))[me.lower()] == "ACCEPTED"

    # Dana accepts: applied (Radicale and the mirror), and InviteCard can say so.
    mid = await _ingest(user, _itip_mail(_reply(uid, DANA, "ACCEPTED", organizer=me), f"Dana <{DANA}>", me))
    out = await _itip(user, mid)
    assert out["applied"] and out["detail"].startswith("Dana accepted") and out["partstat"] == "ACCEPTED"
    obj = await _stored(user, uid)
    assert {a["email"]: a["partstat"] for a in obj.attendees}[DANA] == "ACCEPTED"
    assert _partstats(await _server_copy(username, obj))[DANA] == "ACCEPTED"
    inv = (await client.get(f"/api/v1/messages/{mid}/invite")).json()
    assert inv["itip"]["applied"] and inv["method"] == "REPLY"

    # Safety: each of these changes nothing.
    cases = {
        "spoofed From": _itip_mail(_reply(uid, EVAN, "DECLINED", organizer=me, stamp="20261001T130000Z"),
                                   "Mallory <mallory@evil.example>", me),
        "a stranger (forwarded invitation)": _itip_mail(_reply(uid, "stranger@other.example", "ACCEPTED", organizer=me),
                                                        "stranger@other.example", me),
        "an event that is not mine": _itip_mail(_reply("nope-" + uid, EVAN, "DECLINED", organizer=me), EVAN, me),
        "DMARC failed": _itip_mail(_reply(uid, EVAN, "DECLINED", organizer=me, stamp="20261001T140000Z"), EVAN, me,
                                   auth="dmarc=fail"),
        "an older reply": _itip_mail(_reply(uid, DANA, "DECLINED", organizer=me, stamp="20261001T110000Z"), DANA, me),
    }
    for name, raw in cases.items():
        mid = await _ingest(user, raw)
        assert not (await _itip(user, mid))["applied"], name
    obj = await _stored(user, uid)
    assert {a["email"]: a["partstat"] for a in obj.attendees} == {me.lower(): "ACCEPTED", DANA: "ACCEPTED",
                                                                  EVAN: "NEEDS-ACTION"}
    assert (await _itip(user, mid))["detail"] == "An older reply; a newer one was applied"

    # event.reschedule with guests: SEQUENCE bumps, answers reset, an updated invitation is queued.
    seq = obj.sequence
    later = start + timedelta(hours=2)
    r = await client.patch(f"/api/v1/calendar/events/{uid}", json={"start": later.isoformat(),
                                                                   "end": (later + timedelta(hours=1)).isoformat()})
    assert r.status_code == 200, r.text
    obj = await _stored(user, uid)
    assert obj.sequence > seq
    assert {a["email"]: a["partstat"] for a in obj.attendees}[DANA] == "NEEDS-ACTION"
    ob = (await _outbox(user))[-1]
    assert ob.payload["subject"] == "Updated invitation: Budget review"
    method, ics = _cal_part(await _mime(user, ob))
    assert method == "REQUEST" and f"SEQUENCE:{obj.sequence}" in ics
    # A reply to the old version is stale now.
    mid = await _ingest(user, _itip_mail(_reply(uid, EVAN, "ACCEPTED", organizer=me, seq=seq,
                                                stamp="20261001T150000Z"), EVAN, me))
    assert (await _itip(user, mid))["detail"].startswith("Outdated")

    # event.uninvite: Evan is removed and gets CANCEL; Dana gets nothing new for that.
    n = len(await _outbox(user))
    r = await client.patch(f"/api/v1/calendar/events/{uid}", json={"attendees": [DANA]})
    assert r.status_code == 200, r.text
    [cancel] = (await _outbox(user))[n:]
    assert cancel.recipients == [EVAN] and cancel.payload["subject"] == "Cancelled: Budget review"
    method, ics = _cal_part(await _mime(user, cancel))
    assert method == "CANCEL" and EVAN in ics and DANA not in ics

    # send_updates=false writes without mail.
    n = len(await _outbox(user))
    r = await client.patch(f"/api/v1/calendar/events/{uid}", json={"location": "Room 5", "send_updates": False})
    assert r.status_code == 200 and "invites" not in r.json()
    assert len(await _outbox(user)) == n

    # event.cancel: STATUS:CANCELLED in the calendar, CANCEL to the guests.
    r = await client.post(f"/api/v1/calendar/events/{uid}/cancel", json={"message": "Sorry, moving this to email."})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CANCELLED" and r.json()["invites"]["recipients"] == [DANA]
    method, _ = _cal_part(await _mime(user, (await _outbox(user))[-1]))
    assert method == "CANCEL"
    assert "Sorry, moving this" in (await _outbox(user))[-1].payload["text"]


@radicale
async def test_agent_invite_waits_for_one_approval(client, user):
    """An agent's event with guests: one approval card (calendar.invite) covers the write and
    the mail; nothing is written or queued before it."""
    await _setup(client, user)
    agent = await _token_client(client, ["read", "calendar", "send"])
    start = _start(5)
    r = await agent.post("/api/v1/calendar/events", json={"summary": "Agent sync", "start": start.isoformat(),
                                                          "attendees": [DANA]})
    assert r.status_code == 202, r.text
    appr = r.json()["approval"]
    assert appr["action"] == "calendar.invite" and DANA in " ".join(appr["preview"]["lines"])
    rng = {"from": start.date().isoformat(), "to": (start + timedelta(days=1)).date().isoformat()}
    assert (await client.get("/api/v1/calendar/agenda", params=rng)).json()["events"] == []
    assert await _outbox(user) == []
    r = await client.post(f"/api/v1/approvals/{appr['id']}/decide", json={"decision": "approve"})
    assert r.status_code == 200, r.text
    from mailr.core.permissions import execute_job

    await execute_job({"pending_id": appr["id"]}, None)
    st = (await client.get(f"/api/v1/approvals/{appr['id']}")).json()
    assert st["status"] == "executed", st
    assert [e["summary"] for e in (await client.get("/api/v1/calendar/agenda", params=rng)).json()["events"]] == ["Agent sync"]
    [ob] = await _outbox(user)
    assert ob.recipients == [DANA] and ob.payload["_system"]["itip"]["method"] == "REQUEST"
    # Without the send scope the agent may not invite at all (it may save with send_updates=false).
    calendar_only = await _token_client(client, ["read", "calendar"])
    r = await calendar_only.post("/api/v1/calendar/events", json={"summary": "x", "start": start.isoformat(),
                                                                  "attendees": [DANA]})
    assert r.status_code == 403
    await agent.aclose()
    await calendar_only.aclose()


@radicale
async def test_organizer_update_and_cancel_apply_to_my_copy(client, user):
    me, username = await _setup(client, user)
    uid = f"alice-{uuid.uuid4().hex[:8]}"
    await _put_invitation(user, username, uid, me)
    alice = "Alice <alice@acme.example>"
    # A new time from the organizer (higher SEQUENCE): applied; my alarm stays; my answer resets.
    upd = _cal(_vevent(uid=uid, seq=1, start="20261005T130000Z", end="20261005T140000Z",
                       organizer="alice@acme.example", guests=(me,), alarm=False), "REQUEST")
    mid = await _ingest(user, _itip_mail(upd, alice, me, method="REQUEST"))
    out = await _itip(user, mid)
    assert out["applied"] and out["time_changed"], out
    obj = await _stored(user, uid)
    assert obj.start_at == datetime(2026, 10, 5, 13, tzinfo=UTC) and obj.alarms == [10] and obj.sequence == 1
    assert obj.my_partstat == "NEEDS-ACTION"
    # An older REQUEST, and one from someone who is not the organizer: nothing.
    old = _cal(_vevent(uid=uid, seq=0, start="20261005T170000Z", end="20261005T180000Z",
                       organizer="alice@acme.example", guests=(me,)), "REQUEST")
    assert not (await _itip(user, await _ingest(user, _itip_mail(old, alice, me, method="REQUEST"))))["applied"]
    forged = _cal(_vevent(uid=uid, seq=5, organizer="alice@acme.example", guests=(me,)), "REQUEST")
    assert not (await _itip(user, await _ingest(user, _itip_mail(forged, "mallory@evil.example", me,
                                                                 method="REQUEST"))))["applied"]
    # A new invitation is never added (calendar_add_new_invites is off by default).
    new = _cal(_vevent(uid="brand-new-" + uid, organizer="alice@acme.example", guests=(me,)), "REQUEST")
    out = await _itip(user, await _ingest(user, _itip_mail(new, alice, me, method="REQUEST")))
    assert not out["applied"] and out["detail"] == "Not in your calendar"
    # The organizer cancels: marked cancelled, not deleted.
    cancel = _cal(_vevent(uid=uid, seq=2, organizer="alice@acme.example", guests=(me,),
                          extra="STATUS:CANCELLED\r\n", alarm=False), "CANCEL")
    out = await _itip(user, await _ingest(user, _itip_mail(cancel, alice, me, method="CANCEL")))
    assert out["applied"], out
    obj = await _stored(user, uid)
    assert obj.status == "CANCELLED" and obj.deleted_at is None
    assert ical_status(await _server_copy(username, obj)) == "CANCELLED"


def ical_status(cal: Calendar) -> str:
    return str(next(iter(cal.walk("VEVENT"))).get("status"))


@radicale
async def test_rsvp_reply_carries_the_method_parameter(client, user):
    """The RSVP path uses the same itip payload: text/calendar; method=REPLY."""
    from helpers.calendar import _invite

    me, _ = await _setup(client, user)
    uid = f"inv-{uuid.uuid4().hex[:10]}"
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        res = await ingest_raw(db, await user_store(db, u), _invite(uid, me, _start(3, 14)), IngestOptions())
        await db.commit()
    r = await client.post("/api/v1/calendar/invite-reply", json={"message_id": res.message.id, "response": "accepted"})
    assert r.status_code == 200, r.text
    ob = next(o for o in await _outbox(user) if o.id == r.json()["outbox"]["id"])
    assert "attachments" not in ob.payload or not ob.payload["attachments"]
    method, ics = _cal_part(await _mime(user, ob))
    assert method == "REPLY" and "PARTSTAT=ACCEPTED" in ics


@radicale
async def test_counter_is_recorded_and_accepting_it_moves_the_event(client, user):
    """event.accept_proposal: a guest's COUNTER is shown; accepting moves the event and sends
    the updated invitation."""
    me, _ = await _setup(client, user)
    start = _start(6)
    ev = (await client.post("/api/v1/calendar/events", json={"summary": "Budget review", "start": start.isoformat(),
                                                             "attendees": [DANA]})).json()
    uid = ev["uid"]
    proposed = start + timedelta(hours=3)
    counter = _cal(f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261001T120000Z\r\nSEQUENCE:0\r\n"
                   f"DTSTART:{proposed:%Y%m%dT%H%M%SZ}\r\nDTEND:{proposed + timedelta(hours=1):%Y%m%dT%H%M%SZ}\r\n"
                   f"ORGANIZER:mailto:{me}\r\nATTENDEE;PARTSTAT=TENTATIVE:mailto:{DANA}\r\nEND:VEVENT\r\n", "COUNTER")
    mid = await _ingest(user, _itip_mail(counter, DANA, me, method="COUNTER"))
    out = await _itip(user, mid)
    assert out["counter"] and out["proposed"]["start"] == proposed.isoformat()
    n = len(await _outbox(user))
    r = await client.post(f"/api/v1/calendar/events/{uid}/counter/accept", json={"message_id": mid})
    assert r.status_code == 200, r.text
    assert r.json()["start"] == proposed.isoformat()
    [ob] = (await _outbox(user))[n:]
    assert ob.payload["subject"] == "Updated invitation: Budget review"


async def test_uninvite_and_delete_carry_the_organizers_message(client, user):
    """event.uninvite and event.delete with guests take a message ("Add a message" when
    updates are sent): it is in the text of the removed guests' and the remaining guests' CANCEL."""
    await _setup(client, user)
    start = _start(days=5)
    r = await client.post("/api/v1/calendar/events", json={
        "summary": "Roadmap", "start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(),
        "attendees": [DANA, EVAN]})
    assert r.status_code == 200, r.text
    uid = r.json()["uid"]
    n = len(await _outbox(user))
    r = await client.patch(f"/api/v1/calendar/events/{uid}", json={"attendees": [DANA], "message": "Evan, you are off the hook."})
    assert r.status_code == 200, r.text
    [cancel] = (await _outbox(user))[n:]
    assert cancel.recipients == [EVAN] and "off the hook" in cancel.payload["text"]
    n = len(await _outbox(user))
    r = await client.delete(f"/api/v1/calendar/events/{uid}", params={"message": "Plans changed."})
    assert r.status_code == 200, r.text
    [cancel] = (await _outbox(user))[n:]
    assert cancel.recipients == [DANA] and "Plans changed." in cancel.payload["text"]
    assert _cal_part(await _mime(user, cancel))[0] == "CANCEL"


@radicale
async def test_unverified_update_and_cancel_wait_for_the_user(client, user):
    """A REQUEST or CANCEL whose sender has no positive DMARC/DKIM result
    for the organizer's domain (none, absent, or a DKIM pass for another domain) changes nothing;
    the card offers to apply it, and only the signed-in user can."""
    me, username = await _setup(client, user)
    uid = f"alice-{uuid.uuid4().hex[:8]}"
    await _put_invitation(user, username, uid, me)
    alice = "Alice <alice@acme.example>"
    before = await _stored(user, uid)
    upd = _cal(_vevent(uid=uid, seq=7, start="20261005T130000Z", end="20261005T140000Z",
                       organizer="alice@acme.example", guests=(me,), alarm=False), "REQUEST")
    mid = await _ingest(user, _itip_mail(upd, alice, me, method="REQUEST", auth="dmarc=none"))
    out = await _itip(user, mid)
    assert not out["applied"] and out["pending"] and "unverified" in out["detail"], out
    after = await _stored(user, uid)
    assert after.start_at == before.start_at and after.sequence == before.sequence
    cancel = _cal(_vevent(uid=uid, seq=8, organizer="alice@acme.example", guests=(me,),
                          extra="STATUS:CANCELLED\r\n", alarm=False), "CANCEL")
    cmid = await _ingest(user, _itip_mail(cancel, alice, me, method="CANCEL", auth="dmarc=none"))
    assert (await _itip(user, cmid))["pending"]
    assert (await _stored(user, uid)).status != "CANCELLED"
    # The card's button: the user applies the update.
    r = await client.post(f"/api/v1/messages/{mid}/itip/apply")
    assert r.status_code == 200 and r.json()["applied"] and r.json()["confirmed"], r.text
    assert (await _stored(user, uid)).start_at == datetime(2026, 10, 5, 13, tzinfo=UTC)
    assert (await client.post(f"/api/v1/messages/{mid}/itip/apply")).status_code == 409  # nothing pending now
