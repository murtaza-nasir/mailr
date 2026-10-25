"""Calendar changes as the user reads them (calendar/describe.py, agent/present.py), against the dev
Radicale. An approval card for a change to a recurring
event once did not say which occurrence it changed, and the calendar showed by an internal name. Cards say
the occurrence and the scope in the user's zone, before and after; calendars go by the name the user
knows; the assistant's tool results carry no calendar URL, id or store address.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import caldav
import pytest
from dev_services import RADICALE
from dev_services import radicale_up as _radicale_up
from helpers.agent_calendar import _connect_direct
from helpers.agent_calendar_v2 import _allow, _ctx, _identity

from mailr.agent import when
from mailr.agent.tools import ToolError, run_tool
from mailr.calendar import describe
from mailr.calendar import tz as tzmod
from mailr.db import sessionmaker
from mailr.models import PendingAction

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


def _field(d, label):
    return next(f["value"] for f in d["fields"] if f["label"] == label)


def _clean(text: str, *secrets: str, card: bool = False) -> None:
    """No calendar URL, id or store address; on a card, no RRULE or UTC time either."""
    for bad in (*secrets, "/dav/", "@mailr.internal", "calendar_id", "connector_id",
                *(("FREQ=", "+00:00", "UTC") if card else ())):
        assert bad not in text, f"{bad!r} in {text[:400]!r}"


async def test_recurring_change_and_delete_cards_say_which_occurrence(user):
    username, href = await _connect_direct(user.id)
    await _allow(user.id, "calendar.create")
    start = (datetime.now(UTC) + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        made = await run_tool(ctx, "create_event", {
            "summary": "Standup", "start": start.isoformat(), "end": (start + timedelta(minutes=30)).isoformat(),
            "rrule": "FREQ=WEEKLY;COUNT=4"})
        # A written event reaches the model without calendar identifiers, by the calendar's name.
        _clean(json.dumps(made.data), href, username)
        assert made.data["calendar"] == "Personal" and made.data["repeats"] == "Weekly on " + f"{start:%A}, 4 times"
        listed = await run_tool(ctx, "list_calendar", {"from": start.date().isoformat(),
                                                       "to": (start + timedelta(days=22)).date().isoformat()})
        occ = [e for e in listed.data["items"] if e["summary"] == "Standup"]
        assert len(occ) == 4 and all(e["calendar"] == "Personal" for e in occ)
        _clean(json.dumps(listed.data), href, username)
        second = occ[1]

        res = await run_tool(ctx, "update_event", {"uid": made.data["uid"], "occurrence": second["occurrence"],
                                                   "scope": "this", "start": (start + timedelta(days=7, hours=4)).isoformat()})
        assert res.data["status"] == "pending_approval"
        pa = await db.get(PendingAction, res.data["approval_id"])
        d = pa.preview["display"]
        zone = await tzmod.user_zone(db, user.id)
        o_start = start + timedelta(days=7)
        span = when.span_text(o_start.isoformat(), (o_start + timedelta(minutes=30)).isoformat(), False, zone, fmt="12h")
        assert d["title"] == "Change one occurrence of “Standup”"
        assert _field(d, "Applies to")["text"] == f"{span} (this occurrence only)"
        moved = o_start + timedelta(hours=4)
        after = when.span_text(moved.isoformat(), (moved + timedelta(minutes=30)).isoformat(), False, zone, fmt="12h")
        assert _field(d, "Time")["after"] in (after, after.split(", ", 1)[1])  # the date only when it changes
        _clean(d["text"] + json.dumps(pa.preview["lines"]), href, username, made.data["uid"], card=True)

        gone = await run_tool(ctx, "delete_event", {"uid": made.data["uid"], "occurrence": occ[2]["occurrence"],
                                                    "scope": "following"})
        d = (await db.get(PendingAction, gone.data["approval_id"])).preview["display"]
        day = when.day_text((start + timedelta(days=14)).astimezone(zone).date())
        assert d["title"] == "Delete “Standup”" and d["danger"]
        assert _field(d, "Applies to")["text"] == f"This and following occurrences, from {day}"
        assert _field(d, "Calendar")["text"] == "Personal"
        _clean(d["text"], href, username, made.data["uid"], card=True)

        whole = await run_tool(ctx, "delete_event", {"uid": made.data["uid"], "scope": "all"})
        d = (await db.get(PendingAction, whole.data["approval_id"])).preview["display"]
        assert _field(d, "Applies to")["text"] == "The whole series (weekly on " \
            f"{start.astimezone(zone):%A}, 4 times)"


async def test_one_occurrence_without_an_occurrence_is_never_shown_as_one(user):
    """Scope `this` without `occurrence` changes the whole series (calendar/writer.py), so a card that
    said "<the first date> (this occurrence only)" was wrong. The tools ask for the occurrence, and a
    preview made anyway (a token's REST call) says the whole series."""
    username, href = await _connect_direct(user.id)
    await _allow(user.id, "calendar.create")
    start = (datetime.now(UTC) + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        made = await run_tool(ctx, "create_event", {
            "summary": "Office hours", "start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(),
            "rrule": "FREQ=WEEKLY;COUNT=3"})
        uid = made.data["uid"]
        for tool, extra in (("update_event", {"location": "Room 2"}), ("delete_event", {}),
                            ("update_event", {"location": "Room 3", "scope": "following"})):
            with pytest.raises(ToolError) as e:
                await run_tool(ctx, tool, {"uid": uid, "scope": "this", **extra} if "scope" not in extra
                               else {"uid": uid, **extra})
            assert "`occurrence`" in str(e.value) and "'all'" in str(e.value)
        pv = await describe.event_preview(db, ctx.p, "update", uid, None, "this", {"location": "Room 2"})
        assert pv["scope"] == "all" and pv["title"] == "Change “Office hours”"
        # A single event needs no occurrence for scope this.
        one = await run_tool(ctx, "create_event", {"summary": "Dentist", "start": start.isoformat()})
        res = await run_tool(ctx, "update_event", {"uid": one.data["uid"], "scope": "this", "location": "Clinic"})
        assert res.data.get("status") == "pending_approval"


async def test_calendars_go_by_their_names_in_tools_and_cards(user):
    username, href = await _connect_direct(user.id)
    family = str(caldav.DAVClient(url=RADICALE, username=username, password="x").principal().make_calendar(
        name="Family", supported_calendar_component_set=["VEVENT"]).url)
    start = (datetime.now(UTC) + timedelta(days=3)).replace(hour=16, minute=0, second=0, microsecond=0)
    caldav.DAVClient(url=RADICALE, username=username, password="x").calendar(url=href).save_event(
        dtstart=start, dtend=start + timedelta(hours=1), summary="Dentist", uid=f"dentist-{uuid.uuid4().hex[:6]}")
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        d = (await run_tool(ctx, "get_calendar_defaults", {})).data
        assert d["defaults"]["calendar"] == "Personal"
        assert {c["name"] for c in d["calendars"]} == {"Personal", "Family"}
        # The list keeps `calendar_id` to tell two calendars of one name apart; no URL or account.
        assert all(x not in json.dumps(d) for x in (href, family, username, '"url"'))
        ev = (await run_tool(ctx, "search_events", {"text": "dentist"})).data["items"][0]
        assert ev["calendar"] == "Personal"
        # Moving it by the calendar's name: the card says from where to where.
        res = await run_tool(ctx, "update_event", {"uid": ev["uid"], "calendar": "Family"})
        pa = await db.get(PendingAction, res.data["approval_id"])
        assert pa.payload["changes"]["calendar"] == family
        card = pa.preview["display"]
        assert (_field(card, "Calendar")["before"], _field(card, "Calendar")["after"]) == ("Personal", "Family")
        assert _field(card, "When")["text"].startswith(when.day_text(start.astimezone(await tzmod.user_zone(db, user.id)).date()))
        _clean(card["text"], href, family, username, card=True)


async def test_answering_one_occurrence_says_which(user):
    username, href = await _connect_direct(user.id)
    me = await _identity(user)
    first = (datetime.now(UTC) + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    uid = f"series-{uuid.uuid4().hex[:10]}"
    stamp = lambda d: d.strftime("%Y%m%dT%H%M%SZ")
    caldav.DAVClient(url=RADICALE, username=username, password="x").calendar(url=href).save_event(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nDTSTAMP:20260920T100000Z\r\nDTSTART:{stamp(first)}\r\n"
        f"DTEND:{stamp(first + timedelta(minutes=30))}\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\nSUMMARY:Weekly 1:1\r\n"
        "ORGANIZER;CN=Alice:mailto:alice@acme.example\r\n"
        f"ATTENDEE;CN=Me;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{me}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    second = first + timedelta(days=7)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        await run_tool(ctx, "get_calendar_defaults", {})  # the mirror reads the calendar
        res = await run_tool(ctx, "rsvp", {"event_uid": uid, "response": "declined", "scope": "this",
                                           "occurrence": second.isoformat()})
        assert res.data["status"] == "pending_approval"
        d = (await db.get(PendingAction, res.data["approval_id"])).preview["display"]
        zone = await tzmod.user_zone(db, user.id)
        span = when.span_text(second.isoformat(), (second + timedelta(minutes=30)).isoformat(), False, zone, fmt="12h")
        assert _field(d, "Applies to")["text"] == f"{span} (this occurrence only)"
        _clean(d["text"], href, username, uid, card=True)


async def test_approval_history_reads_like_the_card(client, user):
    """Settings → Agents & API → Recent decisions: a decided calendar change keeps the card's
    wording (title, time, the calendar by name). The web shows that, never `target` (the event's
    uid or a calendar URL)."""
    username, href = await _connect_direct(user.id)
    start = (datetime.now(UTC) + timedelta(days=4)).replace(hour=15, minute=0, second=0, microsecond=0)
    uid = f"review-{uuid.uuid4().hex[:8]}"
    caldav.DAVClient(url=RADICALE, username=username, password="x").calendar(url=href).save_event(
        dtstart=start, dtend=start + timedelta(hours=1), summary="Budget review", uid=uid)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        await run_tool(ctx, "get_calendar_defaults", {})  # the mirror reads the calendar
        res = await run_tool(ctx, "delete_event", {"uid": uid, "scope": "all"})
        assert res.data["status"] == "pending_approval"
        zone = await tzmod.user_zone(db, user.id)
    r = await client.post(f"/api/v1/approvals/{res.data['approval_id']}/decide", json={"decision": "deny"})
    assert r.status_code == 200, r.text
    hist = (await client.get("/api/v1/approvals", params={"status": "approved,executing,executed,denied,expired,failed"})).json()
    row = next(x for x in hist if x["id"] == res.data["approval_id"])
    assert row["status"] == "denied" and row["target"] == uid  # the raw target is the uid: not for people
    d = row["display"]
    assert d["title"] == "Delete “Budget review”"
    assert _field(d, "Calendar")["text"] == "Personal"
    assert when.day_text(start.astimezone(zone).date()) in d["text"]
    _clean(json.dumps(d) + json.dumps(row["outcome"]), href, username, uid, card=True)


def test_labels_hide_internal_names():
    assert describe.calendar_label("Calendar (alice@mailr.internal)", "/dav/cal/alice/default/") == "Calendar"
    assert describe.calendar_label("Stalwart Calendar (grp-family@mailr.internal)", "/x/") == "Calendar"
    assert describe.calendar_label("", "/dav/cal/alice@mailr.internal/3f2a9c0e-77b1-4c1e-9a2e-0d1c2b3a4f5e/") == "Calendar"
    assert describe.calendar_label("", "/dav/cal/alice/work/") == "Work"
    assert describe.calendar_label("grp-family@mailr.internal", "/x/", {"kind": "group", "group": "Family"}) == "Family"
    assert describe.calendar_label("Soccer", "/x/", {"kind": "group", "group": "Family"}) == "Soccer (the Family group)"
    assert describe.calendar_label("Rosa's", "/x/", {"kind": "user", "owner": "Rosa"}) == "Rosa's (shared by Rosa)"
    assert all(describe.looks_internal(v) for v in ("/dav/cal/a/b/", "https://x/y", "12", "a@b.c"))
    assert not describe.looks_internal("Work")


async def _ask(user_id, *actions):
    from mailr.models import PermissionRule

    async with sessionmaker()() as db:
        for a in actions:
            db.add(PermissionRule(user_id=user_id, subject="assistant", action=a, effect="ask", constraints={}))
        await db.commit()


async def test_task_cards_name_the_list(user):
    """A task card once
    read "List: https://…/dav/calendars/…/". Task cards and their lines name the list."""
    username, href = await _connect_direct(user.id)
    errands = str(caldav.DAVClient(url=RADICALE, username=username, password="x").principal().make_calendar(
        name="Errands", supported_calendar_component_set=["VTODO"]).url)
    await _ask(user.id, "tasks.create", "tasks.update")
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        await run_tool(ctx, "get_calendar_defaults", {})
        res = await run_tool(ctx, "create_task", {"summary": "Buy stamps", "list": "Errands"})
        pa = await db.get(PendingAction, res.data["approval_id"])
        assert pa.payload["list"] == errands
        d = pa.preview["display"]
        assert _field(d, "List")["text"] == "Errands"
        _clean(d["text"] + json.dumps(pa.preview["lines"]), errands, href, username)
        uid = str(caldav.DAVClient(url=RADICALE, username=username, password="x").calendar(url=href)
                  .save_todo(summary="Call the bank").icalendar_component["uid"])
        res = await run_tool(ctx, "update_task", {"uid": uid, "list": "Errands"})
        pa = await db.get(PendingAction, res.data["approval_id"])
        d = pa.preview["display"]
        assert _field(d, "List")["text"] == "Errands"
        assert any("Errands" in ln for ln in pa.preview["lines"])
        _clean(d["text"] + json.dumps(pa.preview["lines"]), errands, href, username)


def test_a_card_never_shows_a_list_url():
    from mailr.agent import present

    d = present._task({"kind": "task", "title": "Add task “x”",
                       "task": {"summary": "x", "list": "https://cal.example/dav/u/tasks/", "due": "2026-10-09"}}, None)
    assert "https://" not in d["text"] and "List" not in d["text"]
    d = present._task({"kind": "task", "title": "Add task “x”",
                       "task": {"summary": "x", "list": "/dav/u/tasks/", "list_name": "Errands"}}, None)
    assert "List: Errands" in d["text"]


def test_collection_names_are_never_a_url_part_or_an_address():
    from mailr.calendar import mirror
    from mailr.models import CalCollection

    def out(displayname, href):
        coll = CalCollection(id=1, connector_id=1, href=href, displayname=displayname, components=["VEVENT"])
        return mirror.collection_out(coll, None, {})["name"]

    assert out("Work", "/dav/cal/a/work/") == "Work"
    assert out("Calendar (rosa@mailr.internal)", "/dav/cal/rosa/default/") == "Calendar"
    assert out("", "/dav/cal/a/3f2a9c0e-77b1-4c1e-9a2e-0d1c2b3a4f5e/") == "Calendar"
    assert out(None, "/dav/cal/a/errands/") == "Errands"


def test_stored_citations_read_by_the_calendar_name():
    from mailr.agent.routes import _clean_cites

    got = _clean_cites([{"kind": "event", "from": "Calendar (rosa@mailr.internal)"},
                        {"kind": "task", "from": "https://cal.example/dav/calendars/rosa/default/"},
                        {"kind": "event", "from": "Soccer (the Family group)"},
                        {"kind": "message", "from": "alice@acme.example"}])
    assert [c["from"] for c in got] == ["Calendar", None, "Soccer (the Family group)", "alice@acme.example"]
