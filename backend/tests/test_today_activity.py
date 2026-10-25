# ACT-SURFACES: api
"""Today (GET /today), Activity (GET /activity, the Undo and POST /activity/{id}/undone) and Find a
time with household members (GET /calendar/household/freebusy, POST /calendar/find-time `people`).

Covers (api): ACT activity.list, ACT activity.undo, ACT event.find_time_household, ACT calendar.today_view; the mail,
notes and tables entries; the admin notices; who may read them."""

import uuid
from datetime import UTC, datetime, timedelta

from conftest import mail_date
from helpers.core_flow import _ingest
from httpx import ASGITransport, AsyncClient

from mailr.core.users import create_user
from mailr.db import sessionmaker
from mailr.models import CalCollection, CalObject, CalOccurrence, Connector, Conversation, User

API = "/api/v1"


async def _thread(user, raw, tag):
    q = raw(f"Contract {tag}", frm="Dana <dana@example.org>", to=user.email, msgid=f"<q-{tag}-{uuid.uuid4().hex[:6]}@x.org>",
            date=mail_date(7, "09:00"))
    _, cid = await _ingest(user, q, roles=["inbox"], source="connector")
    return cid


async def _act(client, cid, action, **kw):
    r = await client.post(f"{API}/conversations/actions", json={"ids": [cid], "action": action, **kw})
    assert r.status_code == 200, r.text
    return r.json()


async def _activity(client, **params):
    r = await client.get(f"{API}/activity", params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


# ---------------------------------------------------------------- Today


async def test_today_lists_mail_waiting_and_approvals(client, user, raw):
    cid = await _thread(user, raw, "today")
    async with sessionmaker()() as db:
        c = await db.get(Conversation, cid)
        c.nudged_at = datetime.now(UTC) - timedelta(hours=2)
        c.follow_up_since = datetime.now(UTC) - timedelta(days=3)
        await db.commit()
    r = await client.get(f"{API}/today")
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["date"] and t["zone"]
    assert t["connected"] is False and t["events"] == [] and t["tasks"] == []
    assert [f["conversation_id"] for f in t["followups"]] == [cid]
    f = t["followups"][0]
    assert f["nudged"] is True and f["subject"] == "Contract today" and f["who"] == "Dana"
    assert t["counts"] == {"events": 0, "tasks": 0, "reminders": 0, "followups": 1, "approvals": 0}
    # A follow-up due next week is not today's.
    async with sessionmaker()() as db:
        c = await db.get(Conversation, cid)
        c.nudged_at = None
        c.follow_up_at = datetime.now(UTC) + timedelta(days=7)
        await db.commit()
    assert (await client.get(f"{API}/today")).json()["followups"] == []
    assert (await client.get(f"{API}/today", params={"date": "not-a-date"})).status_code == 400


# ---------------------------------------------------------------- Activity: mail


async def test_activity_lists_my_mail_changes_with_undo(client, user, raw):
    cid = await _thread(user, raw, "act")
    await _act(client, cid, "read")  # reading is not listed
    await _act(client, cid, "archive")
    items = await _activity(client)
    assert len(items) == 1
    a = items[0]
    assert a["who"] == "you" and a["by"] is None and a["app"] == "mail"
    assert a["text"].startswith("Archived “Contract act")
    assert a["target"]["kind"] == "conversation" and a["target"]["id"] == cid
    undo = a["undo"]
    assert undo["method"] == "POST" and undo["path"] == "/conversations/actions"
    assert undo["body"]["action"] == "restore_labels" and undo["body"]["ids"] == [cid] and undo["body"]["labels"]
    # The client sends it as it is, then marks the entry.
    r = await client.post(f"{API}{undo['path']}", json=undo["body"])
    assert r.status_code == 200, r.text
    inbox = (await client.get(f"{API}/conversations", params={"view": "inbox"})).json()["items"]
    assert any(x["id"] == cid for x in inbox)
    assert (await client.post(f"{API}/activity/{a['id']}/undone")).status_code == 200
    items = await _activity(client)
    first = next(i for i in items if i["id"] == a["id"])
    assert first["undone"] is True and first["undo"] is None
    # The Undo's own change is an entry of its own.
    assert items[0]["text"].startswith("Undid a change to “Contract act")


async def test_a_later_change_takes_the_undo_away(client, user, raw):
    cid = await _thread(user, raw, "later")
    await _act(client, cid, "archive")
    await _act(client, cid, "star")
    star, archive = (await _activity(client))[:2]
    assert star["text"].startswith("Starred") and star["undo"] is not None
    assert archive["text"].startswith("Archived") and archive["undo"] is None
    # Paged: the older page still knows about the newer change.
    page1 = await client.get(f"{API}/activity", params={"limit": 1})
    nxt = page1.json()["next_cursor"]
    assert nxt
    page2 = (await client.get(f"{API}/activity", params={"limit": 1, "cursor": nxt})).json()["items"]
    assert page2[0]["id"] == archive["id"] and page2[0]["undo"] is None


async def test_activity_is_mine_and_for_the_session_only(app, client, user, raw):
    cid = await _thread(user, raw, "mine")
    await _act(client, cid, "archive")
    entry = (await _activity(client))[0]
    other = await _second_user()
    async with _login(app, other) as c2:
        assert await _activity(c2) == []
        assert (await c2.post(f"{API}/activity/{entry['id']}/undone")).status_code == 404
    r = await client.post(f"{API}/tokens", json={"name": "bot", "scopes": ["read", "label"]})
    assert r.status_code in (200, 201), r.text
    tok = r.json()["token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bot:
        resp = await bot.get(f"{API}/activity", headers={"Authorization": f"Bearer {tok}"})
        assert resp.status_code == 403
        # A token's own change is listed for the person, under the agent's name.
        r = await bot.post(f"{API}/conversations/actions", json={"ids": [cid], "action": "star"},
                           headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200, r.text
    top = (await _activity(client))[0]
    assert top["who"] == "agent" and top["by"] == "bot" and top["text"].startswith("Starred")


async def test_admin_notices_are_activity(admin_client, client, user):
    r = await admin_client.patch(f"{API}/admin/users/{user.id}", json={"name": "Robin Example"})
    assert r.status_code == 200, r.text
    items = await _activity(client, who="admin")
    assert items and items[0]["who"] == "admin" and items[0]["app"] == "account"
    assert items[0]["text"] == "Your administrator changed your name to Robin Example."
    assert items[0]["by"] and items[0]["undo"] is None


# ---------------------------------------------------------------- Activity: notes and tables


async def test_notes_and_tables_make_entries(client, user):
    r = await client.post(f"{API}/notes", json={"title": "Groceries", "kind": "checklist", "lines": "milk\nbread"})
    assert r.status_code == 200, r.text
    pid = r.json()["note"]["id"]
    for t in ("Groceries for Sunday", "Groceries for Monday"):
        r = await client.patch(f"{API}/notes/{pid}", json={"title": t})
        assert r.status_code == 200, r.text
    items = await _activity(client, app="notes")
    texts = [i["text"] for i in items]
    assert texts == ["Edited “Groceries for Monday”", "Created “Groceries”"]
    edited, created = items
    assert edited["count"] == 2 and edited["undo"] is None
    assert created["undo"]["method"] == "DELETE" and created["undo"]["path"] == f"/notes/{pid}"
    # The note was edited after it was made: its creation is no longer undone from here (a later
    # change with a different key leaves it; the note's own key differs per operation).
    r = await client.delete(f"{API}/notes/{pid}")
    assert r.status_code == 200, r.text
    trashed = (await _activity(client, app="notes"))[0]
    assert trashed["text"].startswith("Moved “Groceries for Monday” to Trash")
    assert trashed["undo"]["path"] == f"/notes/{pid}/restore"

    r = await client.post(f"{API}/tables", json={"name": "Budget", "columns": [{"name": "Item", "type": "text"}]})
    assert r.status_code in (200, 201), r.text
    tid = r.json()["table"]["id"]
    made = (await _activity(client, app="tables"))[0]
    assert made["text"] == "Created the table “Budget”" and made["undo"]["path"] == f"/tables/{tid}"


# ---------------------------------------------------------------- household free/busy


async def _second_user(groups=()) -> User:
    async with sessionmaker()() as db:
        u = await create_user(db, f"t-{uuid.uuid4().hex[:8]}@example.com", "Dana Example", password="test-password-123")
        u.assigned_groups = list(groups)
        await db.commit()
        return u


class _login:
    def __init__(self, app, u: User):
        self.app, self.u = app, u

    async def __aenter__(self):
        self.c = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")
        r = await self.c.post(f"{API}/auth/login", json={"email": self.u.email, "password": "test-password-123"})
        assert r.status_code == 200, r.text
        self.c.headers["X-CSRF-Token"] = r.json()["csrf"]
        return self.c

    async def __aexit__(self, *exc):
        await self.c.aclose()


async def _busy_builtin(user_id: int, start: datetime, end: datetime) -> None:
    """A built-in calendar for the user with one busy occurrence (no Stalwart needed)."""
    from mailr.calendar import builtin

    async with sessionmaker()() as db:
        conn = await builtin.find(db, user_id)
        if conn is None:
            conn = Connector(user_id=user_id, kind="caldav", name="mailr", builtin=True, mode="mirror",
                             config={"account": f"x{user_id}"}, state={}, enabled=True, status="idle")
            db.add(conn)
            await db.flush()
        coll = CalCollection(user_id=user_id, connector_id=conn.id, href=f"/dav/cal/x{user_id}/default/",
                             displayname="Personal", components=["VEVENT"])
        db.add(coll)
        await db.flush()
        obj = CalObject(user_id=user_id, connector_id=conn.id, collection_id=coll.id, href="a.ics", uid=uuid.uuid4().hex,
                        kind="event", summary="Dentist", start_at=start, end_at=end)
        db.add(obj)
        await db.flush()
        db.add(CalOccurrence(object_id=obj.id, occurrence=start.isoformat(), user_id=user_id, collection_id=coll.id,
                             start_at=start, end_at=end))
        await db.commit()


async def test_household_freebusy_needs_a_grant_and_a_shared_group(app, client, user):
    from mailr.calendar import builtin

    async with sessionmaker()() as db:
        await builtin.set_instance_enabled(db, True, "everyone")
        await db.commit()
    async with sessionmaker()() as db:
        groups = list((await db.get(User, user.id)).assigned_groups or [])
    try:
        await _household(app, client, user)
    finally:
        async with sessionmaker()() as db:
            await builtin.set_instance_enabled(db, False, "everyone")
            (await db.get(User, user.id)).assigned_groups = groups
            await db.commit()


async def _household(app, client, user):
    # Only a group of this test: users of earlier tests may share the user's other groups and
    # have free/busy sharing on.
    group = f"family-{uuid.uuid4().hex[:6]}"
    async with sessionmaker()() as db:
        me = await db.get(User, user.id)
        me.assigned_groups = [group]
        await db.commit()
    dana = await _second_user([group])
    stranger = await _second_user([f"other-{uuid.uuid4().hex[:6]}"])
    day = (datetime.now(UTC) + timedelta(days=9)).replace(hour=15, minute=0, second=0, microsecond=0)
    await _busy_builtin(dana.id, day, day + timedelta(hours=1))
    await _busy_builtin(stranger.id, day, day + timedelta(hours=1))
    params = {"from": (day - timedelta(days=1)).isoformat(), "to": (day + timedelta(days=1)).isoformat()}

    # Off by default: nobody is listed.
    r = await client.get(f"{API}/calendar/household/freebusy", params=params)
    assert r.status_code == 200, r.text
    assert r.json()["people"] == [] and r.json()["sharing"] is False

    for u in (dana, stranger):
        async with _login(app, u) as c:
            assert (await c.patch(f"{API}/me/settings", json={"calendar_share_free_busy": True})).status_code == 200
    people = (await client.get(f"{API}/calendar/household/freebusy", params=params)).json()["people"]
    assert [p["id"] for p in people] == [dana.id]  # the stranger shares no group with me
    assert people[0]["busy"] == [{"start": day.isoformat(), "end": (day + timedelta(hours=1)).isoformat()}]
    assert "Dentist" not in str(people)  # busy times only

    # Find a time leaves Dana's hour out when she is chosen.
    body = {"duration": 60, "from": day.replace(hour=0).isoformat(), "to": (day + timedelta(days=1)).replace(hour=0).isoformat(),
            "tz": "UTC", "working_hours": {"start": "14:00", "end": "17:00", "days": [1, 2, 3, 4, 5, 6, 7]},
            "granularity": 60, "limit": 10}
    alone = (await client.post(f"{API}/calendar/find-time", json=body)).json()["slots"]
    both = (await client.post(f"{API}/calendar/find-time", json={**body, "people": [dana.id, stranger.id]})).json()["slots"]
    starts = lambda s: {x["start"][:16] for x in s}
    assert day.isoformat()[:16] in starts(alone)
    assert day.isoformat()[:16] not in starts(both)

    # The sharing setting "nobody" turns it off for everyone.
    from mailr.calendar import sharing

    async with sessionmaker()() as db:
        before = await sharing.policy(db)
        await sharing.set_policy(db, "nobody")
        await db.commit()
    try:
        assert (await client.get(f"{API}/calendar/household/freebusy", params=params)).json()["people"] == []
    finally:
        async with sessionmaker()() as db:
            await sharing.set_policy(db, before)
            await db.commit()


async def test_settings_validate_the_new_keys(client):
    assert (await client.patch(f"{API}/me/settings", json={"calendar_view": "today"})).status_code == 200
    assert (await client.patch(f"{API}/me/settings", json={"calendar_view": "year"})).status_code == 400
    assert (await client.patch(f"{API}/me/settings", json={"calendar_share_free_busy": "yes"})).status_code == 400


# ---------------------------------------------------------------- calendar and tasks (dev Radicale)


async def _radicale(client):
    import caldav
    from dev_services import RADICALE

    username = f"u{uuid.uuid4().hex[:8]}"
    pr = caldav.DAVClient(url=RADICALE, username=username, password="x").principal()
    pr.make_calendar(name="Personal", supported_calendar_component_set=["VEVENT", "VTODO"])
    r = await client.post(f"{API}/calendar/connect", json={"url": RADICALE, "username": username, "password": "x"})
    assert r.status_code == 200, r.text


async def test_calendar_and_task_changes_with_undo(client):
    from dev_services import radicale_up as _radicale_up

    if not _radicale_up():
        import pytest

        pytest.skip("Radicale dev server not running on :15232")
    await _radicale(client)
    today = datetime.now(UTC).date()
    r = await client.post(f"{API}/tasks", json={"summary": "Pay the plumber", "due": today.isoformat()})
    assert r.status_code == 200, r.text
    uid = r.json()["uid"]
    # Today lists it, with its tick.
    t = (await client.get(f"{API}/today")).json()
    assert t["connected"] is True and [x["uid"] for x in t["tasks"]] == [uid]
    r = await client.patch(f"{API}/tasks/{uid}", json={"completed": True})
    assert r.status_code == 200, r.text
    done, added = (await _activity(client, app="tasks"))[:2]
    assert added["text"] == "Added the task “Pay the plumber”" and added["undo"] is None  # changed since
    assert done["text"] == "Completed “Pay the plumber”" and done["who"] == "you"
    assert done["undo"] == {"method": "PATCH", "path": f"/tasks/{uid}", "body": {"completed": False}}
    r = await client.patch(f"{API}/tasks/{uid}", json=done["undo"]["body"])
    assert r.status_code == 200 and r.json()["completed"] is False

    start = datetime.now(UTC).replace(microsecond=0, second=0, minute=0) + timedelta(days=2)
    r = await client.post(f"{API}/calendar/events", json={"summary": "Piano lesson", "start": start.isoformat(),
                                                          "end": (start + timedelta(hours=1)).isoformat(),
                                                          "location": "Room 4"})
    assert r.status_code == 200, r.text
    ev = r.json()["uid"]
    r = await client.delete(f"{API}/calendar/events/{ev}")
    assert r.status_code == 200, r.text
    deleted = (await _activity(client, app="calendar"))[0]
    assert deleted["text"] == "Deleted the event “Piano lesson”"
    u = deleted["undo"]
    assert u["method"] == "POST" and u["path"] == "/calendar/events"
    assert u["body"]["summary"] == "Piano lesson" and u["body"]["location"] == "Room 4"
    r = await client.post(f"{API}{u['path']}", json=u["body"])
    assert r.status_code == 200, r.text
    back = r.json()
    assert back["summary"] == "Piano lesson" and back["start"][:16] == start.isoformat()[:16]
