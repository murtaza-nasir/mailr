"""Regression tests for the core mail and calendar paths:

- Accept on a mailed invitation stores only the card's event, under the arrival rules.
- The schema.org preview scan is linear, and ingest parses off the event loop.
- A schema.org description is the preview only when the visible text says the same.
- The away message refuses a message with more than one From.
- Previews lose bidi isolates and control characters.
- Triage reads a phone share as someone else's text.
(Authentication-Results parsing is in test_authres.py.)
"""

import asyncio
import time
import uuid

import pytest
from helpers.calendar_organizer import _cal, _outbox, _setup, radicale
from helpers.security_calendar_limits import GOOD_AR, _mail
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import CalObject

ALICE = "alice@acme.example"


def _ev(uid: str, *, org: str = ALICE, guests: tuple[str, ...] = (), rid: str = "", rule: str = "",
        summary: str = "Budget review", start: str = "20261105T090000Z") -> str:
    atts = "".join(f"ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{g}\r\n" for g in guests)
    return (f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261001T100000Z\r\n"
            + (f"RECURRENCE-ID:{rid}\r\n" if rid else "")
            + f"DTSTART:{start}\r\nDTEND:{start[:9]}235900Z\r\nSEQUENCE:0\r\nSUMMARY:{summary}\r\n"
            + (f"RRULE:{rule}\r\n" if rule else "")
            + (f"ORGANIZER:mailto:{org}\r\n" if org else "") + atts + "END:VEVENT\r\n")


async def _invitation(user, ics: str) -> int:
    from helpers.calendar_organizer import _ingest

    return await _ingest(user, _mail(ics, f"Alice <{ALICE}>", "x@example.com", method="REQUEST", auth_header=GOOD_AR))


async def _objects(user) -> list[CalObject]:
    async with sessionmaker()() as db:
        return list((await db.execute(select(CalObject).where(CalObject.user_id == user.id,
                                                              CalObject.deleted_at.is_(None)))).scalars())


# ---------------- Accept on a mailed invitation ----------------


@radicale
async def test_accept_stores_only_the_cards_event_under_the_arrival_rules(client, user):
    me, _ = await _setup(client, user)
    victims = ("v1@victim.example", "v2@victim.example")
    n_before = len(await _outbox(user))
    refused = {
        # The card shows a harmless override; the master makes me the organizer of the attacker's guests.
        "me_as_organizer": (lambda u: _ev(u, rid="20261112T090000Z", guests=(me,), summary="Coffee")
                            + _ev(u, org=me, guests=victims, rule="FREQ=WEEKLY;COUNT=3", summary="Spam")),
        "mixed_organizers": lambda u: _ev(u, guests=(me,)) + _ev(u, org="bob@other.example", rid="20261112T090000Z",
                                                                 guests=(me,)),
        "two_masters": lambda u: _ev(u, guests=(me,)) + _ev(u, guests=(me,) + victims),
        "no_organizer": lambda u: _ev(u, org="", guests=(me,)),
        "costly_rule": lambda u: _ev(u, guests=(me,), rule="FREQ=MINUTELY", start="20200101T090000Z"),
    }
    for case, build in refused.items():
        uid = f"new2-{case}-{uuid.uuid4().hex[:6]}"
        mid = await _invitation(user, _cal(build(uid), "REQUEST"))
        r = await client.post("/api/v1/calendar/invite-reply", json={"message_id": mid, "response": "accepted"})
        assert r.status_code == 409, (case, r.text)
        assert uid not in {o.uid for o in await _objects(user)}, case
    assert len(await _outbox(user)) == n_before  # no reply mailed for a refused invitation

    # A good invitation with a second event (another UID) packed in: only the card's event is stored.
    uid, other = f"new2-ok-{uuid.uuid4().hex[:6]}", f"new2-other-{uuid.uuid4().hex[:6]}"
    mid = await _invitation(user, _cal(_ev(uid, guests=(me,)) + _ev(other, org=me, guests=victims), "REQUEST"))
    inv = (await client.get(f"/api/v1/messages/{mid}/invite")).json()
    assert inv["uid"] == uid and inv["organizer"]["email"] == ALICE
    r = await client.post("/api/v1/calendar/invite-reply", json={"message_id": mid, "response": "accepted",
                                                                  "send": False})
    assert r.status_code == 200 and r.json()["calendar"] == "added", r.text
    objs = {o.uid: o for o in await _objects(user)}
    assert uid in objs and other not in objs
    assert not objs[uid].is_organizer and other not in objs[uid].ics


def test_card_describes_the_master_event():
    from mailr.calendar import itip

    ics = _cal(_ev("u1", rid="20261112T090000Z", summary="Coffee") + _ev("u1", summary="Weekly sync",
                                                                         rule="FREQ=WEEKLY;COUNT=3"),
               "REQUEST").encode()
    info = itip.summarize(ics, set())
    assert info["summary"] == "Weekly sync" and info["recurrence_id"] is None


# ---------------- The schema.org preview scan ----------------


def _adversarial_bodies():
    from mailr.core.visible import MAX_HTML

    typed = '<div itemscope itemtype="http://schema.org/EmailMessage">'
    for name, head, unit in (("nested_descriptions", typed, '<div itemprop="description">'),
                             ("nested_scopes", "", typed),
                             ("inner_scopes", typed, '<div itemscope><i itemprop="description"></i>'),
                             ("flat", typed, '<span itemprop="description"></span>')):
        yield name, head + unit * ((MAX_HTML - len(head)) // len(unit))


def _html_mail(html: str, frm: str = "a@sender.example", to: str = "me@example.com") -> bytes:
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = frm, to, "x"
    msg["Message-ID"] = f"<{uuid.uuid4().hex}@sender.example>"
    msg.set_content("x")
    msg.add_alternative(html, subtype="html")
    return bytes(msg)


def _without_microdata(html: str) -> str:
    """The same tree at the same size, with the microdata attributes renamed: the parse a plain
    mail of this shape costs."""
    out = html.replace("itemprop", "data-pro").replace("itemscope", "data-scop").replace("itemtype", "data-typ")
    assert len(out) == len(html)
    return out


# A relative bound: the adversarial body against the same tree without microdata, timed in turns
# on the same machine, best of three each. Machine load slows both alike, so the ratio holds where
# a wall-clock bound did not. The quadratic scan took over a minute here; a linear one stays near 1x.
# The ratio holds beside the other workers too, so it runs in the parallel pass.
@pytest.mark.parametrize("name,html", list(_adversarial_bodies()), ids=lambda x: x if len(x) < 40 else "")
def test_schema_scan_is_linear_at_the_size_cap(name, html):
    from mailr.core.mime import parse

    assert len(html) > 500 * 1024
    raw, plain = _html_mail(html), _html_mail(_without_microdata(html))
    best = {"raw": float("inf"), "plain": float("inf")}
    for _ in range(3):
        for key, body in (("raw", raw), ("plain", plain)):
            t = time.perf_counter()
            parse(body)
            best[key] = min(best[key], time.perf_counter() - t)
    assert best["raw"] < 4 * best["plain"] + 0.1, (name, best)
    assert best["raw"] < 30, (name, best)  # a backstop if both ever grow together


# The property itself, not a time bound (a stall bound failed under load: a thread that parses
# still competes with the loop for the GIL). The parse of the ingested mail runs in a worker
# thread with no event loop, and the loop ticks while it runs. Parsed on the loop, the loop
# could not tick at all during the parse. No timing: it runs in the parallel pass.
async def test_ingest_parses_off_the_event_loop(user, monkeypatch):
    import threading

    from mailr.core import ingest
    from mailr.core.store import user_store
    from mailr.models import User

    html = dict(_adversarial_bodies())["nested_descriptions"]
    real = ingest.parse
    calls: list[dict] = []
    ticks = 0

    def watched(raw, *a, **k):
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        call = {"on_loop": on_loop, "main": threading.current_thread() is threading.main_thread(),
                "ticks_before": ticks}
        calls.append(call)
        try:
            return real(raw, *a, **k)
        finally:
            call["ticks"] = ticks - call["ticks_before"]

    stop = False

    async def tick():
        nonlocal ticks
        while not stop:
            await asyncio.sleep(0.005)
            ticks += 1

    monkeypatch.setattr(ingest, "parse", watched)
    ticker = asyncio.create_task(tick())
    try:
        async with sessionmaker()() as db:
            u = await db.get(User, user.id)
            store = await user_store(db, u)
            await ingest.ingest_raw(db, store, _html_mail(html, to=u.email), ingest.IngestOptions())
            await db.commit()
    finally:
        stop = True
        await ticker
    assert calls, "ingest did not parse"
    for c in calls:
        assert not c["on_loop"] and not c["main"], c
        assert c["ticks"] >= 1, c  # the loop kept running during the parse


# ---------------- The away message ----------------


def test_away_message_refuses_two_senders_and_foreign_dmarc():
    from mailr.core.vacation import _sender_verified

    ok = {"dmarc": "pass", "dmarc_from": "acme.example"}
    assert _sender_verified(ok, "alice@acme.example")
    assert not _sender_verified(ok, "victim@bank.example")  # a DMARC pass for another domain
    assert not _sender_verified({"dmarc": "pass"}, "alice@acme.example")


async def test_away_message_refusal_for_two_from_headers(user):
    from mailr.core.vacation import _refusal
    from mailr.models import Message, User

    raw = (b"Authentication-Results: mx.example; dmarc=pass header.from=acme.example\r\n"
           b"From: victim@bank.example\r\nFrom: alice@acme.example\r\nTo: me@example.com\r\nSubject: x\r\n\r\nhi")
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        m = Message(user_id=u.id, from_email="alice@acme.example", label_ids=[],
                    auth_results={"dmarc": "pass", "dmarc_from": "acme.example"})
        assert await _refusal(db, u, m, raw) == "more than one sender"


# ---------------- Previews ----------------


def test_previews_lose_bidi_isolates_and_controls():
    from mailr.core.mime import make_snippet

    s = make_snippet("Pay ⁦invoice⁩ now ⁧x⁨ &#8295;y\x07z\x1b done")  # noqa: PLE2502
    assert not any(c in s for c in "⁦⁧⁨⁩\x07\x1b"), repr(s)  # noqa: PLE2502
    assert s.startswith("Pay invoice now x")


# ---------------- Triage of phone shares ----------------


def test_triage_reads_a_phone_share_as_someone_elses_text():
    from types import SimpleNamespace

    from mailr.intel.classify import heuristic_smart
    from mailr.models import Message

    values = {k: False for k in ("is_list", "bulk", "esp", "unsubscribe", "auto_submitted", "noreply", "cc_me")}
    s = SimpleNamespace(values={**values, "direct_to_me": True})
    text = "Can you send me the report by Friday?"
    mine = Message(sent_by_me=True, origin=None, body_text=text)
    share = Message(sent_by_me=True, origin="android-share", body_text=text)
    assert heuristic_smart(mine, s)["waiting_on"] is True
    got = heuristic_smart(share, s)
    assert got["waiting_on"] is False and got["needs_reply"] is False


# ---------------- Sensitive entity names ----------------


async def test_sensitive_entity_name_stays_hidden_and_off_hosted_embedders(client, user):
    from helpers.agent import _token_client

    from mailr.knowledge import memory
    from mailr.models import Entity, Memory

    async with sessionmaker()() as db:
        e = Entity(user_id=user.id, kind="person", name="SECRET-STUDENT-NAME", aliases=[], emails=["s@uni.example"],
                   domains=[], conversation_ids=[], summary="", sensitive=True, source="extracted")
        db.add(e)
        await db.flush()
        db.add(Memory(user_id=user.id, source="user", status="active", refs=[], sensitive=False, pinned=False,
                      use_count=0, text="prefers morning calls", kind="fact", entity_id=e.id))
        await db.commit()
        eid = e.id
    r = await client.post("/api/v1/tokens", json={"name": f"t-{uuid.uuid4().hex[:4]}", "scopes": ["read"]})
    async with _token_client(client, r.json()["token"]) as tc:
        got = await tc.get("/api/v1/assistant/memories")
        assert got.status_code == 200 and "SECRET-STUDENT-NAME" not in got.text, got.text
        assert "prefers morning calls" not in got.text
    # The session (which sees sensitive notes) still gets both.
    got = await client.get("/api/v1/assistant/memories")
    assert "SECRET-STUDENT-NAME" in got.text and "prefers morning calls" in got.text
    # A new note about the sensitive entity is sensitive itself.
    from types import SimpleNamespace

    async with sessionmaker()() as db:
        m, _ = await memory.remember(db, SimpleNamespace(user_id=user.id, token=None),
                                     {"text": "allergic to peanuts", "entity_id": eid})
        assert m.sensitive
        await db.rollback()


# ---------------- Calendar reads off the event loop ----------------


@pytest.mark.serial  # asserts event-loop lag: other workers would add their own CPU load
@radicale
async def test_calendar_reads_expand_off_the_event_loop(client, user, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from helpers.calendar import _connect
    from helpers.security_calendar_limits import _LagProbe

    from mailr.calendar import expand

    await _connect(client)
    start = datetime.now(UTC).replace(hour=7, minute=0, second=0, microsecond=0) + timedelta(days=1)
    r = await client.post("/api/v1/calendar/events", json={"summary": "Daily", "start": start.isoformat(),
                                                           "recurrence": "FREQ=DAILY"})
    assert r.status_code == 200, r.text
    real = expand.instances

    def slow(*a, **kw):  # a costly series: 1 s of expansion (on the loop it would lag >= 1 s)
        time.sleep(1.0)
        return real(*a, **kw)

    monkeypatch.setattr(expand, "instances", slow)
    far = start + timedelta(days=365 * 3)
    params = {"from": far.date().isoformat(), "to": (far + timedelta(days=3)).date().isoformat()}
    async with _LagProbe() as probe:
        ag = (await client.get("/api/v1/calendar/agenda", params=params)).json()
    assert [e["summary"] for e in ag["events"]] == ["Daily"] * 3
    assert probe.lag < 0.5, probe.lag  # wide margin: a loaded machine adds ~0.1-0.4 s of noise


# ---------------- Old Android builds ----------------


def test_version_compare():
    from mailr.core.appversion import too_old

    assert too_old("mailr-android/0.4.9", "0.5.0")
    assert too_old("okhttp mailr-android/0.4 x", "0.5.0")
    assert not too_old("mailr-android/0.5.0", "0.5.0")
    assert not too_old("mailr-android/0.5.2-debug", "0.5.0")
    assert not too_old("Mozilla/5.0 Firefox", "0.5.0")
    assert not too_old("mailr-android/0.1.0", "")


async def test_old_android_builds_get_426(client):
    old = await client.get("/api/v1/me", headers={"User-Agent": "mailr-android/0.4.2"})
    assert old.status_code == 426 and "Update" in old.json()["detail"]
    assert (await client.get("/api/v1/me", headers={"User-Agent": "mailr-android/0.5.1"})).status_code == 200
    # Health stays reachable for every client.
    assert (await client.get("/api/v1/health", headers={"User-Agent": "mailr-android/0.4.2"})).status_code == 200
    # So does the release list an old build checks for its update.
    assert (await client.get("/api/v1/app/releases", headers={"User-Agent": "mailr-android/0.4.2"})).status_code != 426
