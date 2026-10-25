# ACT-SURFACES: api
"""Calendar collections against the dev Radicale: create
(MKCALENDAR), rename and colour (PROPPATCH, with the mailr-side colour when the server refuses),
mailr-only preferences, default reminders, reorder, delete with confirm and the refusals, moving a
list's tasks first, moving an event between calendars, and the permission classes for tokens.

Actions covered: calendar.create, calendar.rename, calendar.color, calendar.toggle_visibility,
calendar.default_reminders, calendar.delete, list.create, list.rename, list.color, list.reorder,
list.delete, event.move_calendar.
"""


import caldav
import pytest
from dev_services import radicale_up as _radicale_up
from helpers.calendar_collections import API, _connect

from mailr.calendar import dav

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


async def _cols(client) -> dict[str, dict]:
    return {c["name"]: c for c in (await client.get(f"{API}/calendar/calendars")).json()}


def _server(pr: caldav.Principal, url: str) -> caldav.Calendar:
    return next(c for c in pr.calendars() if str(c.url).rstrip("/") == url.rstrip("/"))


async def test_create_rename_colour_and_preferences(client, user):
    """ACT calendar.create, ACT list.create, ACT calendar.rename, ACT calendar.color,
    ACT calendar.toggle_visibility, ACT calendar.default_reminders, ACT list.reorder"""
    pr, _ = await _connect(client)
    r = await client.post(f"{API}/calendar/collections", json={"name": "Team", "kind": "events", "color": "#3366ff",
                                                                "default_reminders": {"timed": [10], "all_day": [-540]}})
    assert r.status_code == 200, r.text
    team = r.json()
    assert team["events"] and not team["tasks"] and team["color"] == "#3366ff" and team["calendar_id"]
    assert team["default_reminders"] == {"timed": [10], "all_day": [-540]}
    r = await client.post(f"{API}/calendar/collections", json={"name": "Groceries", "kind": "tasks"})
    lst = r.json()
    assert lst["tasks"] and not lst["events"] and lst["components"] == ["VTODO"]
    both = (await client.post(f"{API}/calendar/collections", json={"name": "Both", "kind": "both"})).json()
    assert both["events"] and both["tasks"]
    assert _server(pr, team["url"]).get_display_name() == "Team"

    r = await client.patch(f"{API}/calendar/collections/{team['calendar_id']}",
                           json={"name": "Team cal", "color": "#00aa00", "hidden": True, "busy": False,
                                 "notify": False, "sort_order": 5})
    assert r.status_code == 200, r.text
    got = r.json()
    assert (got["name"], got["color"], got["hidden"], got["busy"], got["notify"], got["sort_order"]) == \
        ("Team cal", "#00aa00", True, False, False, 5) and not got["color_local"]
    assert _server(pr, team["url"]).get_display_name() == "Team cal"
    # A sync does not undo the mailr-only preferences, and the defaults show in the list.
    cols = {c["calendar_id"]: c for c in (await client.get(f"{API}/calendar/calendars", params={"refresh": 1})).json()}
    assert cols[team["calendar_id"]]["hidden"] and cols[team["calendar_id"]]["color"] == "#00aa00"
    assert cols[team["calendar_id"]]["default_reminders"] == {"timed": [10], "all_day": [-540]}
    r = await client.patch(f"{API}/calendar/collections/{team['calendar_id']}", json={"clear_default_reminders": True})
    assert r.json()["default_reminders"] is None
    assert (await client.patch(f"{API}/calendar/collections/{team['calendar_id']}", json={})).status_code == 400

    r = await client.post(f"{API}/calendar/collections/reorder", json={"ids": [lst["calendar_id"], both["calendar_id"]]})
    assert r.status_code == 200, r.text
    assert [c["sort_order"] for c in r.json()["items"]] == [0, 1]


async def test_colour_kept_in_mailr_when_the_server_refuses(client, user, monkeypatch):
    """ACT list.color, ACT list.rename"""
    await _connect(client)
    lst = (await client.post(f"{API}/calendar/collections", json={"name": "Chores", "kind": "tasks"})).json()

    real = dav.DavClient.proppatch

    async def no_colour(self, href, *, displayname=None, color=None):
        if color is not None:
            return False
        return await real(self, href, displayname=displayname)

    monkeypatch.setattr(dav.DavClient, "proppatch", no_colour)
    r = await client.patch(f"{API}/calendar/collections/{lst['calendar_id']}", json={"color": "#aa00aa",
                                                                                    "name": "House chores"})
    assert r.status_code == 200, r.text
    assert r.json()["color"] == "#aa00aa" and r.json()["color_local"] and r.json()["name"] == "House chores"
    cols = {c["calendar_id"]: c for c in (await client.get(f"{API}/calendar/calendars", params={"refresh": 1})).json()}
    assert cols[lst["calendar_id"]]["color"] == "#aa00aa" and cols[lst["calendar_id"]]["color_local"]
    # "" goes back to the server's colour.
    r = await client.patch(f"{API}/calendar/collections/{lst['calendar_id']}", json={"color": ""})
    assert not r.json()["color_local"]


async def test_delete_needs_confirm_and_can_move_tasks_first(client, user):
    """ACT calendar.delete, ACT list.delete"""
    pr, _ = await _connect(client, "Personal")
    cols = await _cols(client)
    personal = cols["Personal"]
    # The last calendar cannot go.
    r = await client.delete(f"{API}/calendar/collections/{personal['calendar_id']}", params={"confirm": "Personal"})
    assert r.status_code == 409, r.text
    old = (await client.post(f"{API}/calendar/collections", json={"name": "Old list", "kind": "tasks"})).json()
    t1 = (await client.post(f"{API}/tasks", json={"summary": "Keep 1", "list": old["url"]})).json()
    t2 = (await client.post(f"{API}/tasks", json={"summary": "Keep 2", "list": old["url"], "parent": t1["uid"]})).json()
    r = await client.delete(f"{API}/calendar/collections/{old['calendar_id']}", params={"confirm": "old list"})
    assert r.status_code == 400
    r = await client.delete(f"{API}/calendar/collections/{old['calendar_id']}",
                            params={"confirm": "Old list", "move_to": personal["url"]})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["moved"]) == sorted([t1["uid"], t2["uid"]])
    items = (await client.get(f"{API}/tasks", params={"list": personal["url"]})).json()["items"]
    assert {t["summary"] for t in items} == {"Keep 1", "Keep 2"}
    assert old["calendar_id"] not in {c["calendar_id"] for c in (await client.get(f"{API}/calendar/calendars")).json()}
    assert all(str(c.url).rstrip("/") != old["url"].rstrip("/") for c in pr.calendars())
    # The saved default cannot be deleted.
    extra = (await client.post(f"{API}/calendar/collections", json={"name": "Extra", "kind": "both"})).json()
    await client.put(f"{API}/calendar/defaults", json={"calendar": extra["url"], "task_list": personal["url"]})
    r = await client.delete(f"{API}/calendar/collections/{extra['calendar_id']}", params={"confirm": "Extra"})
    assert r.status_code == 409


async def test_event_moves_between_calendars(client, user):
    """ACT event.move_calendar"""
    pr, _ = await _connect(client, "Home", "Work")
    cols = await _cols(client)
    ev = (await client.post(f"{API}/calendar/events", json={"summary": "Dinner", "start": "2026-11-10T18:00:00Z",
                                                             "calendar": cols["Home"]["url"],
                                                             "recurrence": "FREQ=WEEKLY;COUNT=3"})).json()
    r = await client.post(f"{API}/calendar/events/{ev['uid']}/move", json={"calendar": cols["Work"]["url"],
                                                                         "etag": ev["etag"]})
    assert r.status_code == 200, r.text
    assert r.json()["calendar"] == cols["Work"]["url"] and r.json()["uid"] == ev["uid"]
    assert _server(pr, cols["Work"]["url"]).event_by_uid(ev["uid"]) is not None
    with pytest.raises(caldav.error.NotFoundError):
        _server(pr, cols["Home"]["url"]).event_by_uid(ev["uid"])
    # A version that changed since is a conflict.
    await client.patch(f"{API}/calendar/events/{ev['uid']}", json={"summary": "Dinner out"})
    r = await client.post(f"{API}/calendar/events/{ev['uid']}/move", json={"calendar": cols["Home"]["url"],
                                                                         "etag": ev["etag"]})
    assert r.status_code == 409 and r.json()["error"] == "conflict"


async def test_token_classes_and_restricted_accounts(client, user):
    from helpers.security_authz import _caldav, _token

    await _connect(client)
    cid_b, _ = await _caldav(user.id)
    await client.get(f"{API}/calendar/calendars", params={"refresh": 1})
    agent = await _token(client, ["read", "calendar"])
    # A task list may be created; a calendar asks.
    r = await agent.post(f"{API}/calendar/collections", json={"name": "Agent list", "kind": "tasks"})
    assert r.status_code == 200, r.text
    lst = r.json()
    r = await agent.post(f"{API}/calendar/collections", json={"name": "Agent cal", "kind": "events"})
    assert r.status_code == 202
    # Renaming or deleting a list asks.
    r = await agent.patch(f"{API}/calendar/collections/{lst['calendar_id']}", json={"name": "Renamed"})
    assert r.status_code == 202
    r = await agent.patch(f"{API}/calendar/collections/{lst['calendar_id']}", json={"sort_order": 3})
    assert r.status_code == 200 and r.json()["sort_order"] == 3
    r = await agent.delete(f"{API}/calendar/collections/{lst['calendar_id']}", params={"confirm": "Agent list"})
    assert r.status_code == 202
    # A token limited to the other account does not see this one's collections.
    other = await _token(client, ["read", "calendar"], connector_ids=[cid_b], name="limited")
    r = await other.patch(f"{API}/calendar/collections/{lst['calendar_id']}", json={"hidden": True})
    assert r.status_code == 404
    await agent.aclose()
    await other.aclose()
