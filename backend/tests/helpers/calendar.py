"""Shared helpers, from test_calendar.py."""

import uuid
from datetime import datetime, timedelta

import caldav
from dev_services import RADICALE


def _make_calendar(username: str) -> None:
    p = caldav.DAVClient(url=RADICALE, username=username, password="x").principal()
    p.make_calendar(name="Personal", supported_calendar_component_set=["VEVENT", "VTODO"])


async def _connect(client) -> str:
    username = f"u{uuid.uuid4().hex[:8]}"
    _make_calendar(username)
    r = await client.post("/api/v1/calendar/connect", json={"url": RADICALE, "username": username, "password": "x"})
    assert r.status_code == 200, r.text
    assert r.json()["default_calendar"]
    return username


def _invite(uid: str, me: str, start: datetime) -> bytes:
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nSEQUENCE:0\r\nDTSTAMP:20260920T100000Z\r\n"
        f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}\r\nDTEND:{(start + timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}\r\n"
        "SUMMARY:Project sync\r\nORGANIZER;CN=Alice:mailto:alice@acme.example\r\n"
        f"ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{me}\r\n"
        "ATTENDEE;PARTSTAT=ACCEPTED:mailto:alice@acme.example\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    msg = (
        f"From: Alice <alice@acme.example>\r\nTo: {me}\r\nSubject: Invitation: Project sync\r\n"
        f"Message-ID: <{uid}@acme.example>\r\nDate: Mon, 21 Sep 2026 10:00:00 +0000\r\nMIME-Version: 1.0\r\n"
        'Content-Type: multipart/alternative; boundary="b1"\r\n\r\n'
        "--b1\r\nContent-Type: text/plain\r\n\r\nYou are invited.\r\n"
        '--b1\r\nContent-Type: text/calendar; charset=utf-8; method=REQUEST\r\n\r\n' + ics + "--b1--\r\n")
    return msg.encode()


async def _token_client(client, scopes):
    from httpx import AsyncClient

    r = await client.post("/api/v1/tokens", json={"name": "agent", "scopes": scopes})
    assert r.status_code == 200, r.text
    a = AsyncClient(transport=client._transport, base_url="http://test")
    a.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return a
