# ACT-SURFACES: api
"""GET /calendar/search (ACT event.search) and the /calendar/events/search alias against the dev
Radicale: full text with a prefix on the last word, ranking, the operators, the occurrence an
event is shown at, kinds, calendars and restricted tokens."""

import uuid
from datetime import UTC, datetime, timedelta

import caldav
import pytest
from dev_services import RADICALE
from dev_services import radicale_up as _radicale_up

from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.store import user_store
from mailr.db import sessionmaker
from mailr.models import User

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")

API = "/api/v1"


async def _connect(client, *names: str):
    username = f"u{uuid.uuid4().hex[:8]}"
    pr = caldav.DAVClient(url=RADICALE, username=username, password="x").principal()
    cals = {n: pr.make_calendar(name=n, supported_calendar_component_set=["VEVENT", "VTODO"])
            for n in names or ("Personal",)}
    r = await client.post(f"{API}/calendar/connect", json={"url": RADICALE, "username": username, "password": "x"})
    assert r.status_code == 200, r.text
    cols = {c["name"]: c for c in (await client.get(f"{API}/calendar/calendars")).json()}
    return cals, cols, r.json()["id"]


def _at(days: int, hour: int) -> datetime:
    return (datetime.now(UTC) + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


async def _event(client, summary: str, start: datetime, **kw) -> dict:
    r = await client.post(f"{API}/calendar/events", json={"summary": summary, "start": start.isoformat(),
                                                           "end": (start + timedelta(hours=1)).isoformat(), **kw})
    assert r.status_code == 200, r.text
    return r.json()


async def _search(client, q: str, **params) -> list[dict]:
    r = await client.get(f"{API}/calendar/search", params={"q": q, **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _names(items: list[dict]) -> list[str]:
    return [i["summary"] for i in items]


async def test_text_prefix_ranking_and_occurrence(client, user):
    _, cols, _ = await _connect(client, "Work", "Home")
    work = cols["Work"]["url"]
    await _event(client, "Budget review", _at(3, 15), calendar=work, location="Room 4")
    await _event(client, "Lunch", _at(2, 12), calendar=cols["Home"]["url"], description="talk about the budget")
    # A weekly series that started three weeks ago: shown at its next occurrence.
    series = await _event(client, "Budget standup", _at(-21, 9), calendar=work, recurrence="FREQ=WEEKLY")
    past = await _event(client, "Budget retro", _at(-10, 16), calendar=work)
    found = await _search(client, "budg")
    assert set(_names(found)) == {"Budget review", "Lunch", "Budget standup", "Budget retro"}
    # Title matches rank above a description match; upcoming before past at the same rank.
    assert _names(found)[-1] == "Lunch"
    assert _names(found).index("Budget retro") == 2
    standup = next(i for i in found if i["uid"] == series["uid"])
    assert datetime.fromisoformat(standup["start"]) > datetime.now(UTC) and standup["recurring"]
    retro = next(i for i in found if i["uid"] == past["uid"])
    assert retro["occurrence"] == past["occurrence"] and retro["kind"] == "event" and retro["rank"] > 0
    assert _names(await _search(client, "budget review")) == ["Budget review"]
    assert set(_names(await _search(client, "budget cal:work"))) == {"Budget review", "Budget standup", "Budget retro"}
    assert _names(await _search(client, "budget", calendar=cols["Home"]["url"])) == ["Lunch"]
    # A range keeps events with an occurrence inside it (the weekly series has none in these days).
    r = await _search(client, "budget", **{"from": _at(1, 0).date().isoformat(), "to": _at(5, 0).date().isoformat()})
    assert set(_names(r)) == {"Budget review", "Lunch"}
    r = await _search(client, "budget", **{"from": _at(1, 0).date().isoformat(), "to": _at(9, 0).date().isoformat()})
    assert set(_names(r)) == {"Budget review", "Budget standup", "Lunch"}
    # Paging.
    page = (await client.get(f"{API}/calendar/search", params={"q": "budget", "limit": 2})).json()
    assert len(page["items"]) == 2 and page["next_cursor"] == "2"
    rest = (await client.get(f"{API}/calendar/search", params={"q": "budget", "limit": 2, "cursor": "2"})).json()
    assert len(rest["items"]) == 2 and rest["next_cursor"] is None
    # The old route is an alias with its old shape (occurrences in a range).
    old = (await client.get(f"{API}/calendar/events/search", params={"q": "budg"})).json()["items"]
    assert "Budget review" in _names(old) and "Lunch" in _names(old)


async def test_operators(client, user):
    cals, _, _ = await _connect(client)
    me = user.email
    await _event(client, "Design sync", _at(4, 10), attendees=["dana@acme.example"], location="https://zoom.us/j/123",
                 categories=["Project-X"], send_updates=False)
    await _event(client, "Dentist", _at(6, 8), location="Main Street clinic")
    await _event(client, "Gym", _at(1, 7), recurrence="FREQ=DAILY;COUNT=3")
    uid = f"inv-{uuid.uuid4().hex[:8]}"
    s = _at(8, 14)
    cals["Personal"].save_event(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:{s.strftime('%Y%m%dT%H%M%SZ')}\r\n"
        f"DTEND:{(s + timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}\r\nSUMMARY:Board meeting\r\n"
        "ORGANIZER;CN=Alice:mailto:alice@acme.example\r\n"
        f"ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:{me}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    t1 = (await client.post(f"{API}/tasks", json={"summary": "File expenses", "due": _at(-2, 9).isoformat(),
                                                   "priority": 1, "categories": ["finance"]})).json()
    t2 = (await client.post(f"{API}/tasks", json={"summary": "Old report"})).json()
    await client.patch(f"{API}/tasks/{t2['uid']}", json={"completed": True})

    assert _names(await _search(client, "with:dana")) == ["Design sync"]
    assert set(_names(await _search(client, "has:guests", kind="event"))) == {"Board meeting", "Design sync"}
    assert _names(await _search(client, "has:video")) == ["Design sync"]
    assert _names(await _search(client, "tag:project-x")) == ["Design sync"]
    assert _names(await _search(client, "@finance")) == ["File expenses"]
    assert _names(await _search(client, "loc:clinic")) == ["Dentist"]
    assert _names(await _search(client, "is:recurring")) == ["Gym"]
    assert _names(await _search(client, "is:invite")) == ["Board meeting"]
    assert _names(await _search(client, "is:done")) == ["Old report"]
    assert _names(await _search(client, "is:overdue")) == ["File expenses"]
    assert _names(await _search(client, "pri:high")) == ["File expenses"]
    assert _names(await _search(client, "-is:done", kind="task")) == ["File expenses"]
    after = _at(5, 0).date().isoformat()
    assert set(_names(await _search(client, f"after:{after}", kind="event"))) == {"Dentist", "Board meeting"}
    assert set(_names(await _search(client, f"before:{_at(2, 0).date().isoformat()}", kind="event"))) == {"Gym"}
    assert _names(await _search(client, "with:dana", kind="task")) == []
    assert t1["uid"] in {i["uid"] for i in await _search(client, "list:personal expenses")}


async def test_from_email_and_restricted_tokens(client, user):
    from helpers.security_authz import _caldav, _token

    await _connect(client)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        raw = (b"From: Bob <bob@acme.example>\r\nTo: me@example.com\r\nSubject: Send the slides\r\n"
               b"Message-ID: <slides@acme.example>\r\nDate: Mon, 21 Sep 2026 10:00:00 +0000\r\n\r\n"
               b"Could you send the slides?\r\n")
        res = await ingest_raw(db, await user_store(db, u), raw, IngestOptions())
        await db.commit()
    r = await client.post(f"{API}/calendar/from-message/confirm", json={"message_id": res.message.id, "kind": "task",
                                                                       "title": "Send slides"})
    assert r.status_code == 200, r.text
    await client.post(f"{API}/tasks", json={"summary": "Send invoice"})
    assert _names(await _search(client, "send has:email")) == ["Send slides"]
    email = (await client.get(f"{API}/tasks", params={"smart": "email"})).json()["items"]
    assert [t["summary"] for t in email] == ["Send slides"] and email[0]["conversation_id"]
    # A token limited to another account sees nothing of this one.
    cid_b, cal_b = await _caldav(user.id)
    cal_b.save_event("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\nBEGIN:VEVENT\r\nUID:b-1\r\n"
                     "DTSTAMP:20260101T000000Z\r\nDTSTART:20261110T100000Z\r\nDTEND:20261110T110000Z\r\n"
                     "SUMMARY:Send flowers\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    tok = await _token(client, ["read"], connector_ids=[cid_b])
    got = (await tok.get(f"{API}/calendar/search", params={"q": "send"})).json()["items"]
    assert _names(got) == ["Send flowers"]
    assert set(_names(await _search(client, "send"))) == {"Send slides", "Send invoice", "Send flowers"}
    await tok.aclose()
