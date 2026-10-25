# ACT-SURFACES: api
"""Household lists (note.local_only and table.local_only): repeating lists, presence, quick note and
tables on the calendar.

Covered, with their action ids: note.repeat (a list repeats weekly on a day or every N days; the
reset unticks, keeps history and plans the next), note.presence and table.presence (who has a shared
item open), note.quick ("New note…" from anywhere), table.on_calendar (a table's date column on this
viewer's calendar), and `can_lower_local_only` on notes and tables (the locked state the menus show).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from helpers.notes_sharing import (  # noqa: F401 - fixtures
    new_note,
    new_table,
    people,
    policy,
    share,
)
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.notes import repeat
from mailr.notes.models import CheckitemCheck, Note

API = "/api/v1"


# ---------------------------------------------------------------- the repeat rule


def test_repeat_rules_and_their_one_spelling():
    assert repeat.canonical("FREQ=WEEKLY;BYDAY=su") == "FREQ=WEEKLY;BYDAY=SU"
    assert repeat.canonical("RRULE:FREQ=WEEKLY;BYDAY=MO;INTERVAL=1") == "FREQ=WEEKLY;BYDAY=MO"
    assert repeat.canonical("FREQ=WEEKLY;INTERVAL=2;BYDAY=FR") == "FREQ=WEEKLY;BYDAY=FR;INTERVAL=2"
    assert repeat.canonical("FREQ=DAILY;INTERVAL=3") == "FREQ=DAILY;INTERVAL=3"
    assert repeat.canonical("FREQ=DAILY") == "FREQ=DAILY;INTERVAL=1"
    assert repeat.canonical("") is None and repeat.canonical(None) is None
    for bad in ("FREQ=MONTHLY;BYMONTHDAY=1", "FREQ=WEEKLY", "FREQ=WEEKLY;BYDAY=MO,TU", "FREQ=DAILY;INTERVAL=0",
                "FREQ=DAILY;INTERVAL=400", "FREQ=DAILY;COUNT=3", "nonsense"):
        try:
            repeat.canonical(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_the_first_reset_and_the_next_at_four_in_the_morning():
    # Tuesday 6 Oct 2026, 10:00 in Chicago (15:00 UTC).
    now = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)
    sun = repeat.first("FREQ=WEEKLY;BYDAY=SU", "America/Chicago", now)
    assert sun == datetime(2026, 10, 11, 9, 0, tzinfo=UTC)  # Sunday 04:00 CDT
    # The same weekday as today: next week, never today.
    assert repeat.first("FREQ=WEEKLY;BYDAY=TU", "America/Chicago", now) == datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
    assert repeat.first("FREQ=DAILY;INTERVAL=3", "America/Chicago", now) == datetime(2026, 10, 9, 9, 0, tzinfo=UTC)
    # After a reset: the cadence goes on from the due day, across the change to standard time.
    nxt = repeat.after_reset("FREQ=WEEKLY;BYDAY=SU", "America/Chicago", datetime(2026, 10, 25, 9, 0, tzinfo=UTC),
                             datetime(2026, 10, 25, 9, 1, tzinfo=UTC))
    assert nxt == datetime(2026, 11, 1, 10, 0, tzinfo=UTC)  # 04:00 CST
    # Missed repeats are not replayed: the next one lies after now.
    late = repeat.after_reset("FREQ=DAILY;INTERVAL=2", "UTC", datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
                              datetime(2026, 10, 8, 12, 0, tzinfo=UTC))
    assert late == datetime(2026, 10, 9, 4, 0, tzinfo=UTC)


async def test_a_list_repeats_and_its_ticks_reset_with_history_kept(people, policy):
    await policy("anyone")
    olive = await people("Olive")
    dana = await people("Dana")
    n = await new_note(olive, title="Chores", lines="dishes\nlaundry\nbins")
    await share(olive, "note", n["id"], str(dana.uid), "edit")
    # ACT note.repeat: an editor sets it.
    r = await dana.patch(f"{API}/notes/{n['id']}", json={"repeat": "FREQ=WEEKLY;BYDAY=SU"})
    assert r.status_code == 200, r.text
    got = r.json()["note"]["repeat"]
    assert got["rule"] == "FREQ=WEEKLY;BYDAY=SU" and got["next"] and got["last"] is None
    assert r.json()["undo"]["body"]["repeat"] is None
    assert (await dana.patch(f"{API}/notes/{n['id']}", json={"repeat": "FREQ=MONTHLY"})).status_code == 422

    items = (await olive.get(f"{API}/notes/{n['id']}")).json()["items"]
    for it in items[:2]:
        assert (await olive.patch(f"{API}/notes/{n['id']}/items/{it['id']}", json={"checked": True})).status_code == 200

    # The reset is due: the scheduler's minute unticks both, and plans the next Sunday.
    async with sessionmaker()() as db:
        note = (await db.execute(select(Note).where(Note.pid == n["id"]))).scalar_one()
        note.repeat_next = datetime.now(UTC) - timedelta(minutes=1)
        due = note.repeat_next
        await db.commit()
    assert await repeat.run_due() >= 1
    after = (await olive.get(f"{API}/notes/{n['id']}")).json()
    assert [i["checked"] for i in after["items"]] == [False, False, False]
    assert after["repeat"]["last"] is not None
    assert datetime.fromisoformat(after["repeat"]["next"]) > datetime.now(UTC)
    assert datetime.fromisoformat(after["repeat"]["next"]) - due >= timedelta(days=6)
    # History kept: the checks stay in the log, and the reset is a version of its own.
    async with sessionmaker()() as db:
        nid = (await db.execute(select(Note.id).where(Note.pid == n["id"]))).scalar_one()
        checks = (await db.execute(select(CheckitemCheck).where(CheckitemCheck.note_id == nid))).scalars().all()
    assert len(checks) >= 2
    revs = (await olive.get(f"{API}/notes/{n['id']}/revisions")).json()["revisions"]
    assert revs[0]["source"] == "repeat"
    # A second run does nothing: it is not due again.
    async with sessionmaker()() as db:
        assert (await db.execute(select(Note.repeat_next).where(Note.pid == n["id"]))).scalar_one() > datetime.now(UTC)

    # Repeat off, and the undo of setting it puts it back.
    r = await olive.patch(f"{API}/notes/{n['id']}", json={"repeat": None})
    assert r.status_code == 200 and r.json()["note"]["repeat"] is None
    undo = r.json()["undo"]
    r = await olive.request(undo["method"], f"{API}{undo['path']}", json=undo["body"])
    assert r.status_code == 200 and r.json()["note"]["repeat"]["rule"] == "FREQ=WEEKLY;BYDAY=SU"


async def test_a_text_note_does_not_repeat(people):
    olive = await people("Olive")
    r = await olive.post(f"{API}/notes", json={"kind": "text", "title": "Idea"})
    pid = r.json()["note"]["id"]
    r = await olive.patch(f"{API}/notes/{pid}", json={"repeat": "FREQ=DAILY;INTERVAL=2"})
    assert r.status_code == 422 and "Only a list" in r.json()["detail"]


# ---------------------------------------------------------------- presence


async def test_presence_reaches_the_other_people_only(people, policy, monkeypatch):
    from mailr.core import events

    await policy("anyone")
    olive = await people("Olive")
    dana = await people("Dana")
    gus = await people("Gus")
    n = await new_note(olive)
    t = await new_table(olive)
    await share(olive, "note", n["id"], str(dana.uid), "edit")
    await share(olive, "table", t["id"], str(dana.uid), "view")
    got: list[tuple[int, dict]] = []

    async def record(db, uid, ev):
        got.append((uid, ev))

    monkeypatch.setattr(events, "notify", record)
    # ACT note.presence
    r = await dana.post(f"{API}/notes/{n['id']}/presence", json={"state": "here", "hello": True})
    assert r.status_code == 200, r.text
    assert r.json()["others"] == 1 and r.json()["heartbeat"] == 15
    pres = [(u, e) for u, e in got if e.get("type") == "presence"]
    assert [u for u, _ in pres] == [olive.uid]
    ev = pres[0][1]
    assert (ev["kind"], ev["id"], ev["state"], ev["hello"], ev["person"]) == (
        "note", n["id"], "here", True, {"id": dana.uid, "name": "Dana"})
    got.clear()
    assert (await dana.post(f"{API}/notes/{n['id']}/presence", json={"state": "left"})).status_code == 200
    assert [e["state"] for _, e in got] == ["left"]
    # Someone who cannot see it gets a 404 and tells nobody.
    got.clear()
    assert (await gus.post(f"{API}/notes/{n['id']}/presence", json={"state": "here"})).status_code == 404
    assert got == []
    # ACT table.presence: a viewer counts too.
    r = await dana.post(f"{API}/tables/{t['id']}/presence", json={"state": "here"})
    assert r.status_code == 200 and [u for u, _ in got] == [olive.uid]


# ---------------------------------------------------------------- quick note


async def test_quick_note_makes_a_note_or_adds_to_a_list(people):
    olive = await people("Olive")
    shop = await new_note(olive, title="Shopping", lines="## Costco\npaper towels")
    # ACT note.quick: a #List first adds the items, in the named section.
    r = await olive.post(f"{API}/notes/quick", json={"text": "#Shopping Costco: milk"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "items" and r.json()["note"]["id"] == shop["id"]
    full = (await olive.get(f"{API}/notes/{shop['id']}")).json()
    costco = next(s for s in full["sections"] if s["name"] == "Costco")
    assert any(i["text"] == "milk" and i["section_id"] == costco["id"] for i in full["items"])
    # Anything else is a text note: the first line its title.
    r = await olive.post(f"{API}/notes/quick", json={"text": "Call the plumber\nabout the #kitchen sink"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "note"
    note = r.json()["note"]
    assert (note["kind"], note["title"], note["body"]) == ("text", "Call the plumber", "about the #kitchen sink")
    # An unknown list name makes the list.
    r = await olive.post(f"{API}/notes/quick", json={"text": "#Hardware nails"})
    assert r.status_code == 200 and r.json()["created"] is True


# ---------------------------------------------------------------- local-only lock


async def test_the_lock_on_local_only_shows_who_may_lower_it(people, policy):
    await policy("anyone")
    olive = await people("Olive")
    dana = await people("Dana")
    n = await new_note(olive, local_only=True)
    t = await new_table(olive)
    await share(olive, "note", n["id"], str(dana.uid), "edit")
    await share(olive, "table", t["id"], str(dana.uid), "edit")
    assert (await olive.get(f"{API}/notes/{n['id']}")).json()["can_lower_local_only"] is True
    assert (await dana.get(f"{API}/notes/{n['id']}")).json()["can_lower_local_only"] is False
    assert (await olive.get(f"{API}/tables/{t['id']}")).json()["can_lower_local_only"] is True
    assert (await dana.get(f"{API}/tables/{t['id']}")).json()["can_lower_local_only"] is False


# ---------------------------------------------------------------- a table's dates on the calendar


async def test_a_tables_dates_show_on_the_viewers_calendar(people, policy):
    await policy("anyone")
    olive = await people("Olive")
    dana = await people("Dana")
    t = await new_table(olive, name="Birthdays")
    tid = t["id"]
    first = (await olive.get(f"{API}/tables/{tid}")).json()["columns"][0]["id"]
    r = await olive.post(f"{API}/tables/{tid}/columns", json={"name": "Day", "type": "date"})
    assert r.status_code == 200, r.text
    day_col = r.json()["column"]["id"]
    for name, d in (("Ana", "2026-10-12"), ("Ben", "2026-10-20"), ("Cy", "2026-12-01"), ("Dee", None)):
        cells = {first: name, **({day_col: d} if d else {})}
        assert (await olive.post(f"{API}/tables/{tid}/rows", json={"cells": cells})).status_code == 200
    q = {"start": "2026-10-01", "end": "2026-10-31"}
    # Off by default.
    assert (await olive.get(f"{API}/tables/calendar", params=q)).json()["items"] == []
    # Only a date column.
    assert (await olive.put(f"{API}/tables/{tid}/state", json={"calendar_column": first})).status_code == 422
    # ACT table.on_calendar
    r = await olive.put(f"{API}/tables/{tid}/state", json={"calendar_column": day_col})
    assert r.status_code == 200, r.text
    assert r.json()["state"]["calendar_column"] == day_col
    got = (await olive.get(f"{API}/tables/calendar", params=q)).json()
    assert [(x["title"], x["date"], x["table_name"]) for x in got["items"]] == [
        ("Ana", "2026-10-12", "Birthdays"), ("Ben", "2026-10-20", "Birthdays")]
    assert got["tables"][0]["column_name"] == "Day"
    # Per viewer: a sharee's calendar is unchanged until they turn it on.
    await share(olive, "table", tid, str(dana.uid), "view")
    assert (await dana.get(f"{API}/tables/calendar", params=q)).json()["items"] == []
    assert (await dana.put(f"{API}/tables/{tid}/state", json={"calendar_column": day_col})).status_code == 200
    assert len((await dana.get(f"{API}/tables/calendar", params=q)).json()["items"]) == 2
    # A range too long is refused; off again is null.
    assert (await olive.get(f"{API}/tables/calendar", params={"start": "2026-01-01", "end": "2026-12-31"})).status_code == 422
    r = await olive.put(f"{API}/tables/{tid}/state", json={"calendar_column": None})
    assert r.status_code == 200 and r.json()["state"]["calendar_column"] is None
    assert (await olive.get(f"{API}/tables/calendar", params=q)).json()["items"] == []
    # A table in Trash leaves the calendar.
    await dana.put(f"{API}/tables/{tid}/state", json={"calendar_column": day_col})
    assert (await olive.delete(f"{API}/tables/{tid}")).status_code == 200
    assert (await dana.get(f"{API}/tables/calendar", params=q)).json()["items"] == []


# ---------------------------------------------------------------- "Share with Cleo"


async def test_the_household_candidates_come_first_for_a_quick_share(people, policy):
    """note.share_quick: the people dialog's answer names the candidates who share a group with the
    viewer, and one PUT with level edit shares the list with one of them."""
    import uuid

    await policy("anyone")
    g = f"House-{uuid.uuid4().hex[:6]}"
    olive = await people("Olive", (g,))
    cleo = await people("Cleo", (g,))
    await people("Stranger")
    n = await new_note(olive, title="Groceries")
    st = (await olive.get(f"{API}/notes/{n['id']}/shares")).json()
    assert cleo.uid in st["household"]
    names = {c["id"]: c["name"] for c in st["candidates"]}
    assert all(names[i] != "Stranger" for i in st["household"])
    st = await share(olive, "note", n["id"], str(cleo.uid), "edit")
    assert cleo.uid not in st["household"]
    assert [p["name"] for p in st["people"] if not p["you"]] == ["Cleo"]
