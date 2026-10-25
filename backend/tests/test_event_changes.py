# ACT-SURFACES: api
"""Moves announced in words (intel/event_changes.py): event.change_suggestion.

A deterministic prefilter, then a LOCAL model only (sensitive=True), then strict checks: a listed event,
confidence, the quoted sentence really in the email, a future new time. Each refusal path is tested, and the
model is never asked when no event fits. Only the event's verified people (or the user's trusted forward) can
raise a card.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.agent import _ingest
from helpers.calendar import _connect

from mailr.db import sessionmaker
from mailr.intel import event_changes
from mailr.models import Message, User

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")

SENTENCE = "Could we move our Ops review to October 28 from 1 to 3?"


def test_prefilter_needs_a_move_and_a_time():
    assert event_changes.prefilter(SENTENCE)
    assert event_changes.prefilter("Let's reschedule to Tuesday at 2pm.")
    assert not event_changes.prefilter("The Ops review notes are attached.")
    assert not event_changes.prefilter("Could we move forward with the hire?")


AR = "Authentication-Results: mx.example; dkim=pass header.d=work.example; dmarc=pass header.from=work.example\r\n"


def _mail(body: str, subject: str = "Ops", frm: str = "Chair <chair@work.example>", ar: str = AR) -> bytes:
    return (f"{ar}From: {frm}\r\nTo: me@example.com\r\nSubject: {subject}\r\n"
            f"Message-ID: <{datetime.now().timestamp()}@work.example>\r\nDate: {datetime.now(UTC).strftime('%a, %d %b %Y %H:%M:%S +0000')}\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n").encode()


@pytest.fixture
def fake_model(monkeypatch):
    from mailr.intel import llm

    calls = []
    answer: dict = {}

    async def chat(db, user, messages, **kw):
        calls.append(kw)
        return {"content": json.dumps(answer)}

    monkeypatch.setattr(llm, "chat", chat)
    return calls, answer


async def _event(client, start: datetime, summary: str = "Ops Review") -> dict:
    r = await client.post("/api/v1/calendar/events", json={"summary": summary, "start": start.isoformat(),
                                                           "end": (start + timedelta(hours=1)).isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


async def _chairs_event(client, user, start: datetime, summary: str = "Ops Review") -> dict:
    """An event the chair organizes: their invitation, accepted without a reply."""
    import uuid

    from helpers.agent import _setup_identity

    await _setup_identity(user)
    uid = f"ops-{uuid.uuid4().hex[:10]}"
    async with sessionmaker()() as db:
        me = (await db.get(User, user.id)).email
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
           f"UID:{uid}\r\nSEQUENCE:0\r\nDTSTAMP:{stamp}\r\nDTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}\r\n"
           f"DTEND:{(start + timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}\r\nSUMMARY:{summary}\r\n"
           "ORGANIZER;CN=Chair:mailto:chair@work.example\r\n"
           f"ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{me}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    raw = (f"{AR}From: Chair <chair@work.example>\r\nTo: {me}\r\nSubject: {summary}\r\n"
           f"Message-ID: <{uid}@work.example>\r\nDate: {datetime.now(UTC).strftime('%a, %d %b %Y %H:%M:%S +0000')}\r\n"
           "MIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary=\"b1\"\r\n\r\n"
           "--b1\r\nContent-Type: text/plain\r\n\r\nInvitation.\r\n"
           "--b1\r\nContent-Type: text/calendar; charset=utf-8; method=REQUEST\r\n\r\n" + ics + "--b1--\r\n").encode()
    mid, _ = await _ingest(user.id, raw)
    r = await client.post("/api/v1/calendar/invite-reply", json={"message_id": mid, "response": "accepted", "send": False})
    assert r.status_code == 200, r.text
    return {"uid": uid}


async def _suggest(user, mid: int):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        m = await db.get(Message, mid)
        out = await event_changes.suggest(db, u, m)
        await db.commit()
        return out


async def test_a_clear_move_becomes_one_suggestion_from_a_local_model(client, user, fake_model):
    calls, answer = fake_model
    await _connect(client)
    start = (datetime.now(UTC) + timedelta(days=5)).replace(hour=18, minute=0, second=0, microsecond=0)
    ev = await _chairs_event(client, user, start)
    new = start + timedelta(days=14)
    answer.update({"event": 0, "new_start": new.strftime("%Y-%m-%dT%H:%M+00:00"), "new_end": "", "confidence": 0.95,
                   "sentence": SENTENCE})
    mid, cid = await _ingest(user.id, _mail(f"Hi all,\n\n{SENTENCE}\n\nThanks"))
    sug = await _suggest(user, mid)
    assert sug and sug["uid"] == ev["uid"] and sug["sentence"] == SENTENCE
    assert datetime.fromisoformat(sug["new_end"]) - datetime.fromisoformat(sug["new_start"]) == timedelta(hours=1)
    assert calls and calls[0]["sensitive"] is True  # a local model only
    body = (await client.get(f"/api/v1/conversations/{cid}")).json()
    msg = next(x for x in body["messages"] if x["id"] == mid)
    assert msg["event_change"]["uid"] == ev["uid"]
    assert (await client.post(f"/api/v1/messages/{mid}/event-change", json={"status": "dismissed"})).status_code == 200
    body = (await client.get(f"/api/v1/conversations/{cid}")).json()
    assert "event_change" not in next(x for x in body["messages"] if x["id"] == mid)


@pytest.mark.parametrize("case", ["unsure", "not_quoted", "past", "no_event_index"])
async def test_no_suggestion_when_anything_is_uncertain(client, user, fake_model, case):
    calls, answer = fake_model
    await _connect(client)
    start = (datetime.now(UTC) + timedelta(days=5)).replace(hour=18, minute=0, second=0, microsecond=0)
    await _chairs_event(client, user, start)
    new = start + timedelta(days=14)
    answer.update({"event": 0, "new_start": new.isoformat(), "new_end": "", "confidence": 0.95, "sentence": SENTENCE})
    if case == "unsure":
        answer["confidence"] = 0.6
    elif case == "not_quoted":
        answer["sentence"] = "Please move the Ops review to October 28."
    elif case == "past":
        answer["new_start"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    else:
        answer["event"] = -1
    mid, _ = await _ingest(user.id, _mail(SENTENCE))
    assert await _suggest(user, mid) is None


async def test_the_model_is_not_asked_without_a_matching_event(client, user, fake_model):
    calls, _ = fake_model
    await _connect(client)
    await _event(client, (datetime.now(UTC) + timedelta(days=5)).replace(hour=18, minute=0, second=0, microsecond=0),
                 "Dentist")
    mid, _ = await _ingest(user.id, _mail(SENTENCE, subject="Committee"))
    assert await _suggest(user, mid) is None and calls == []


@pytest.mark.parametrize("who", ["stranger", "unverified_chair"])
async def test_only_the_events_verified_people_can_suggest_a_move(client, user, fake_model, who):
    """A stranger whose email shares the event's title, or a forged From of the
    organizer, never raises a "Move <your event>?" card; the model is not even asked."""
    calls, answer = fake_model
    await _connect(client)
    start = (datetime.now(UTC) + timedelta(days=5)).replace(hour=18, minute=0, second=0, microsecond=0)
    await _chairs_event(client, user, start)
    answer.update({"event": 0, "new_start": (start + timedelta(days=14)).isoformat(), "new_end": "",
                   "confidence": 0.99, "sentence": SENTENCE})
    raw = _mail(SENTENCE, frm="Mallory <mallory@evil.example>",
                ar="Authentication-Results: mx.example; dkim=pass header.d=evil.example; dmarc=pass header.from=evil.example\r\n") \
        if who == "stranger" else _mail(SENTENCE, ar="")
    mid, _ = await _ingest(user.id, raw)
    assert await _suggest(user, mid) is None and calls == []
