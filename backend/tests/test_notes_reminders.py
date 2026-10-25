# ACT-SURFACES: api web.notification
"""Reminders on notes (notes/reminders.py):
note.remind sets the viewer's own reminders with an undo, keys
`n|<id>|abs|<instant>` on the shared reminder contract, the server planner fires them (SSE, push,
the tray), snooze and dismiss through /reminders (handled anywhere is gone everywhere), the Reminders
view and `has:reminder`, Trash and lost access, and `kinds` on /reminders/state for older phones."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from mailr.calendar import planner
from mailr.db import sessionmaker
from mailr.models import ReminderSchedule, ReminderState, User

# These tests never touch the mail store: database-only users (conftest.make_db_user).
pytestmark = pytest.mark.db_user

API = "/api/v1"
PW = "remind-password-123"


@pytest.fixture
def captured(monkeypatch):
    from mailr.core import events, push

    got = {"sse": [], "push": []}
    real = events.notify

    async def notify(db, user_id, event):
        if event.get("type") == "notification" and event.get("kind") == "reminder":
            got["sse"].append({**event, "_u": user_id})
        await real(db, user_id, event)

    async def deliver(target, title, body, data, priority="default"):
        got["push"].append({"title": title, "body": body, "data": data})
        return True

    monkeypatch.setattr(events, "notify", notify)
    monkeypatch.setattr(push, "deliver", deliver)
    return got


@pytest.fixture
async def other(app):
    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        u = await create_user(db, f"dana-{uuid.uuid4().hex[:8]}@example.com", "Dana", password=PW)
        await db.commit()
        uid, email = u.id, u.email
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post(f"{API}/auth/login", json={"email": email, "password": PW})
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    c.uid = uid  # type: ignore[attr-defined]
    c.email = email  # type: ignore[attr-defined]
    yield c
    await c.aclose()


def soon(minutes: int = 30) -> datetime:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).replace(microsecond=0)


async def note(client, **kw):
    r = await client.post(f"{API}/notes", json={"kind": "text", "title": "Call the plumber", **kw})
    assert r.status_code == 200, r.text
    return r.json()["note"]


async def rows(user_id: int) -> dict[str, ReminderSchedule]:
    async with sessionmaker()() as db:
        return {r.key: r for r in (await db.execute(select(ReminderSchedule).where(
            ReminderSchedule.user_id == user_id, ReminderSchedule.kind == "n"))).scalars()}


async def fire(row: ReminderSchedule) -> None:
    await planner.fire({"id": row.id, "fire_at": row.fire_at.isoformat()}, None)


async def test_note_remind_sets_plans_and_undoes(client, user):
    """note.remind: PUT /notes/{id}/reminders with a time sets the viewer's reminder, keyed
    n|<id>|abs|<instant>, planned on the server; the undo takes it off again."""
    n = await note(client)
    at = soon()
    r = await client.put(f"{API}/notes/{n['id']}/reminders", json={"at": [at.isoformat()]})
    assert r.status_code == 200, r.text
    key = f"n|{n['id']}|abs|{at.isoformat()}"
    assert [x["key"] for x in r.json()["reminders"]] == [key]
    assert r.json()["reminders"][0]["state"] == "upcoming"
    got = (await client.get(f"{API}/notes/{n['id']}")).json()
    assert got["reminders"][0]["at"] == at.isoformat() and got["next_reminder"] == at.isoformat()
    plan = await rows(user.id)
    assert plan[key].fire_at == at and plan[key].title == "Call the plumber" and plan[key].detail == "Note reminder"
    undo = r.json()["undo"]
    assert undo["method"] == "PUT" and undo["body"] == {"at": []}
    r = await client.request(undo["method"], API + undo["path"], json=undo["body"])
    assert r.status_code == 200 and r.json()["reminders"] == []
    assert await rows(user.id) == {}
    # A time in the past is refused; a time without a zone too.
    r = await client.put(f"{API}/notes/{n['id']}/reminders", json={"at": [(datetime.now(UTC) - timedelta(hours=2)).isoformat()]})
    assert r.status_code == 422 and "passed" in r.text
    r = await client.put(f"{API}/notes/{n['id']}/reminders", json={"at": ["2030-01-01T09:00:00"]})
    assert r.status_code == 422


async def test_note_reminder_fires_to_the_tray_and_dismiss_is_gone_everywhere(client, user, captured):
    """reminder.fire on a note reminder: SSE and push with the note's click; the tray lists it with
    Snooze and Dismiss; a dismiss (any device) takes it off the note and out of the tray."""
    n = await note(client, kind="checklist", title="Packing", lines="passport")
    at = soon(5)
    await client.put(f"{API}/notes/{n['id']}/reminders", json={"at": [at.isoformat()]})
    key = f"n|{n['id']}|abs|{at.isoformat()}"
    row = (await rows(user.id))[key]
    await fire(row)
    [sse] = captured["sse"]
    assert sse["key"] == key and sse["title"] == "Packing" and sse["body"] == "List reminder"
    assert sse["click"] == f"/notes/n/{n['id']}" and sse["item"]["kind"] == "note"
    assert sse["actions"] == ["snooze", "dismiss"]
    tray = (await client.get(f"{API}/reminders/active")).json()["items"]
    assert [(i["key"], i["kind"], i["state"]) for i in tray] == [(key, "note", "fired")]
    assert (await client.get(f"{API}/notes/{n['id']}")).json()["reminders"][0]["state"] == "fired"
    r = await client.post(f"{API}/reminders/handle", json={"keys": [key]})
    assert r.status_code == 200
    assert (await client.get(f"{API}/reminders/active")).json()["items"] == []
    assert (await client.get(f"{API}/notes/{n['id']}")).json()["reminders"] == []
    assert (await client.get(f"{API}/notes", params={"view": "reminders"})).json()["notes"] == []
    # Undo of the dismiss brings it back to the note and the tray.
    await client.post(f"{API}/reminders/handle", json={"keys": [key], "undo": True})
    assert [i["key"] for i in (await client.get(f"{API}/reminders/active")).json()["items"]] == [key]


async def test_note_reminder_snooze_moves_the_fire_time(client, user, captured):
    n = await note(client)
    at = soon(2)
    await client.put(f"{API}/notes/{n['id']}/reminders", json={"at": [at.isoformat()]})
    key = f"n|{n['id']}|abs|{at.isoformat()}"
    await fire((await rows(user.id))[key])
    until = soon(60)
    r = await client.post(f"{API}/reminders/snooze", json={"key": key, "until": until.isoformat()})
    assert r.status_code == 200, r.text
    row = (await rows(user.id))[key]
    assert row.fire_at == until and row.fired_at is None and row.source == "snooze"
    got = (await client.get(f"{API}/notes/{n['id']}")).json()
    assert got["reminders"][0]["state"] == "snoozed" and got["next_reminder"] == until.isoformat()
    assert (await client.get(f"{API}/reminders/active")).json()["items"] == []
    await fire(row)
    assert len(captured["sse"]) == 2


async def test_reminders_view_and_has_reminder_search(client):
    """The Reminders view: notes with a reminder, next first; `has:reminder` finds them; tables never."""
    a = await note(client, title="Later")
    b = await note(client, title="Sooner")
    await note(client, title="No reminder")
    await client.put(f"{API}/notes/{a['id']}/reminders", json={"at": [soon(120).isoformat()]})
    await client.put(f"{API}/notes/{b['id']}/reminders", json={"at": [soon(60).isoformat()]})
    await client.post(f"{API}/tables", json={"name": "Expenses"})
    r = (await client.get(f"{API}/notes", params={"view": "reminders"})).json()
    assert [c["title"] for c in r["notes"]] == ["Sooner", "Later"] and r["tables"] == []
    s = (await client.get(f"{API}/notes/search", params={"q": "has:reminder"})).json()
    assert {c["title"] for c in s["notes"]} == {"Sooner", "Later"} and s["tables"] == []


async def test_note_reminder_is_the_viewers_own(client, user, other, captured):
    """A sharee's reminder reminds only them; a view-only sharee may set one; a note in Trash or
    no longer shared does not fire."""
    n = await note(client)
    r = await client.put(f"{API}/notes/{n['id']}/shares/{other.uid}", json={"level": "view", "email": other.email})
    assert r.status_code == 200, r.text
    at = soon(3)
    r = await other.put(f"{API}/notes/{n['id']}/reminders", json={"at": [at.isoformat()]})
    assert r.status_code == 200, r.text
    assert (await client.get(f"{API}/notes/{n['id']}")).json()["reminders"] == []
    assert len((await other.get(f"{API}/notes/{n['id']}")).json()["reminders"]) == 1
    key = f"n|{n['id']}|abs|{at.isoformat()}"
    row = (await rows(other.uid))[key]
    await client.delete(f"{API}/notes/{n['id']}/shares/{other.uid}")
    await fire(row)
    assert captured["sse"] == []
    # Trash: the owner's own reminder does not fire either.
    at2 = soon(4)
    await client.put(f"{API}/notes/{n['id']}/reminders", json={"at": [at2.isoformat()]})
    await client.delete(f"{API}/notes/{n['id']}")
    await fire((await rows(user.id))[f"n|{n['id']}|abs|{at2.isoformat()}"])
    assert captured["sse"] == []


async def test_reminder_state_kinds_keep_note_keys_from_older_phones(client, user):
    """GET /reminders/state lists event and task keys by default; `kinds=e,t,n` adds note keys."""
    n = await note(client)
    at = soon(10)
    await client.put(f"{API}/notes/{n['id']}/reminders", json={"at": [at.isoformat()]})
    key = f"n|{n['id']}|abs|{at.isoformat()}"
    r = await client.post(f"{API}/reminders/snooze", json={"key": key, "until": soon(90).isoformat()})
    assert r.status_code == 200
    assert key not in [i["key"] for i in (await client.get(f"{API}/reminders/state")).json()["items"]]
    items = (await client.get(f"{API}/reminders/state", params={"kinds": "e,t,n"})).json()["items"]
    assert [(i["key"], i["kind"], i["uid"]) for i in items if i["key"] == key] == [(key, "n", n["id"])]
    assert (await client.post(f"{API}/reminders/handle", json={"keys": ["n|nzz|abs|nope"]})).status_code == 400


async def test_note_reminder_local_only_push_names_nothing(client, user, captured):
    n = await note(client, title="Secret plan", local_only=True)
    at = soon(1)
    await client.put(f"{API}/notes/{n['id']}/reminders", json={"at": [at.isoformat()]})
    async with sessionmaker()() as db:
        from mailr.models import PushTarget

        db.add(PushTarget(user_id=user.id, kind="ntfy", endpoint=f"https://push.invalid/{uuid.uuid4().hex}",
                          events=["reminder"]))
        await db.commit()
    await fire((await rows(user.id))[f"n|{n['id']}|abs|{at.isoformat()}"])
    [p] = captured["push"]
    assert p["title"] == "Note reminder" and p["body"] == "" and p["data"]["click"] == "/notes"


async def test_note_reminder_title_follows_the_note_and_purge_drops_the_plan(client, user):
    n = await note(client)
    at = soon(30)
    await client.put(f"{API}/notes/{n['id']}/reminders", json={"at": [at.isoformat()]})
    await client.patch(f"{API}/notes/{n['id']}", json={"title": "Call Dana"})
    key = f"n|{n['id']}|abs|{at.isoformat()}"
    assert (await rows(user.id))[key].title == "Call Dana"
    await client.delete(f"{API}/notes/{n['id']}")
    await client.delete(f"{API}/notes/{n['id']}", params={"forever": "true"})
    assert await rows(user.id) == {}
    async with sessionmaker()() as db:
        assert (await db.execute(select(ReminderState).where(ReminderState.key == key))).first() is None
        assert await db.get(User, user.id) is not None
