# ACT-SURFACES: api
"""Learned order within sections and usual items,
on scripted check logs: trips of checks at most 45 minutes apart, at least 3 checks, a rank step
of 0.3; drags; reset; usual items after 3 distinct days and 0.8 of the median interval, at most
8, dismissed for an interval or for good."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from mailr.notes import learn
from mailr.notes.learn import Check

# These tests never touch the mail store: database-only users (conftest.make_db_user).
pytestmark = pytest.mark.db_user

API = "/api/v1"
T0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


# ---------------------------------------------------------------- the rules alone


def test_trips_cut_at_45_minutes():
    cs = [Check("a", at(0)), Check("b", at(44)), Check("c", at(88)), Check("d", at(134))]
    assert [[c.key for c in t] for t in learn.trips(cs)] == [["a", "b", "c"], ["d"]]


def test_positions_and_rank_step():
    trip = [Check("milk", at(0)), Check("bread", at(5)), Check("eggs", at(9))]
    assert learn.positions(trip) == {"milk": 0.0, "bread": 0.5, "eggs": 1.0}
    r = learn.apply_trip({}, trip)
    assert r["bread"] == (0.5, 1)
    later = [Check("bread", at(100)), Check("milk", at(101)), Check("eggs", at(102))]
    r = learn.apply_trip(r, later)
    assert r["bread"] == (0.5 + 0.3 * (0.0 - 0.5), 2)
    assert r["milk"] == (0.0 + 0.3 * (0.5 - 0.0), 2)


def test_usual_due_rules():
    today = date(2026, 10, 5)
    h = {"egg": {"days": [today - timedelta(days=d) for d in (21, 14, 7)], "text": "eggs", "qty": None,
                 "section_id": None, "count": 3},
         "milk": {"days": [today - timedelta(days=d) for d in (6, 4, 2)], "text": "milk", "qty": None,
                  "section_id": None, "count": 3},
         "jam": {"days": [today - timedelta(days=d) for d in (30, 20)], "text": "jam", "qty": None,
                 "section_id": None, "count": 2}}
    due = learn.due(h, set(), set(), today)
    # eggs: m = 7, 7 days since ≥ 5.6; milk: m = 2, 2 days since ≥ max(2, 1.6); jam: 2 days only.
    assert [u.key for u in due] == ["egg", "milk"] or [u.key for u in due] == ["milk", "egg"]
    assert {u.key for u in learn.due(h, {"egg"}, set(), today)} == {"milk"}
    assert {u.key for u in learn.due(h, set(), {"milk"}, today)} == {"egg"}
    h["egg"]["days"] = [today - timedelta(days=d) for d in (15, 8, 1)]
    assert {u.key for u in learn.due(h, set(), set(), today)} == {"milk"}  # 1 day < 5.6
    many = {f"k{i}": {"days": [today - timedelta(days=d) for d in (30, 20, 10)], "text": f"k{i}", "qty": None,
                      "section_id": None, "count": 3} for i in range(12)}
    assert len(learn.due(many, set(), set(), today)) == 8


def test_median_gap():
    d = date(2026, 10, 1)
    assert learn.median_gap([d, d + timedelta(days=2), d + timedelta(days=10)]) == 5.0
    assert learn.median_gap([d]) is None


# ---------------------------------------------------------------- through the API


async def _list(client, lines: str, **kw):
    r = await client.post(f"{API}/notes", json={"kind": "checklist", "title": "Shop", "lines": lines,
                                                "shopping": True, **kw})
    assert r.status_code == 200, r.text
    return r.json()["note"]


async def _log(note_pid: str, rows: list[tuple[str, str | None, datetime]], undone: bool = False):
    """Insert checks into the log as a phone would have sent them: (text, section id, device time)."""
    from mailr.db import sessionmaker
    from mailr.notes.itemadd import item_key
    from mailr.notes.models import CheckitemCheck, Note

    async with sessionmaker()() as db:
        nid = (await db.execute(select(Note.id).where(Note.pid == note_pid))).scalar_one()
        for text, sid, when in rows:
            db.add(CheckitemCheck(note_id=nid, section_id=sid, key=item_key(text), text=text, device_at=when,
                                  undone=undone))
        await db.commit()


async def _note(client, pid):
    r = await client.get(f"{API}/notes/{pid}")
    assert r.status_code == 200, r.text
    return r.json()


def _section_texts(note, sid):
    return [i["text"] for i in note["items"] if i["section_id"] == sid and not i["checked"]]


async def test_learned_order_in_a_section(client):
    """Three trips through Costco teach its order; the top group, in learned mode too, is
    separate. Items with no rank follow in the order they were added."""
    n = await _list(client, "Costco:\npaper towels\navocados\nmilk\nbread\nnew thing")
    costco = n["sections"][0]["id"]
    assert n["sections"][0]["order_mode"] == "learned"
    trip = ["milk", "bread", "avocados", "paper towels"]
    rows = []
    for day in range(3):
        rows += [(t, costco, at(day * 1440 + k * 3)) for k, t in enumerate(trip)]
    await _log(n["id"], rows)
    got = await _note(client, n["id"])
    assert _section_texts(got, costco) == ["milk", "bread", "avocados", "paper towels", "new thing"]
    assert got["sections"][0]["trips"] == 3
    milk = next(i for i in got["items"] if i["text"] == "milk")
    assert milk["rank"] == 0.0


async def test_short_trips_and_undone_checks_teach_nothing(client):
    n = await _list(client, "Costco:\na\nb\nc")
    costco = n["sections"][0]["id"]
    await _log(n["id"], [("c", costco, at(0)), ("b", costco, at(1))])  # 2 checks: ignored
    await _log(n["id"], [("c", costco, at(2000)), ("b", costco, at(2001)), ("a", costco, at(2002))], undone=True)
    got = await _note(client, n["id"])
    assert _section_texts(got, costco) == ["a", "b", "c"] and got["sections"][0]["trips"] == 0


async def test_open_trip_waits_until_it_closes(client):
    n = await _list(client, "Costco:\na\nb\nc")
    costco = n["sections"][0]["id"]
    now = datetime.now(UTC)
    await _log(n["id"], [("c", costco, now - timedelta(minutes=10)), ("b", costco, now - timedelta(minutes=9)),
                         ("a", costco, now - timedelta(minutes=8))])
    got = await _note(client, n["id"])
    assert _section_texts(got, costco) == ["a", "b", "c"]  # still shopping
    from mailr.db import sessionmaker
    from mailr.notes.models import Note

    async with sessionmaker()() as db:
        note = (await db.execute(select(Note).where(Note.pid == n["id"]))).scalar_one()
        assert await learn.learn(db, note, costco, now=now + timedelta(minutes=60)) == 1
        await db.commit()
    got = await _note(client, n["id"])
    assert _section_texts(got, costco) == ["c", "b", "a"]


async def test_uncheck_within_trip_removes_the_check(client):
    n = await _list(client, "a\nb\nc")
    pid = n["id"]
    a = next(i for i in n["items"] if i["text"] == "a")["id"]
    t = datetime.now(UTC)
    await client.patch(f"{API}/notes/{pid}/items/{a}", json={"checked": True, "device_at": t.isoformat()})
    await client.patch(f"{API}/notes/{pid}/items/{a}", json={"checked": False,
                                                            "device_at": (t + timedelta(minutes=1)).isoformat()})
    from mailr.db import sessionmaker
    from mailr.notes.models import CheckitemCheck

    async with sessionmaker()() as db:
        rows = (await db.execute(select(CheckitemCheck).where(CheckitemCheck.item_id == a))).scalars().all()
    assert [r.undone for r in rows] == [True]


async def test_drag_in_learned_mode_sets_rank_and_reset(client):
    n = await _list(client, "Costco:\na\nb\nc\nd")
    costco = n["sections"][0]["id"]
    rows = []
    for day in range(2):
        rows += [(t, costco, at(day * 1440 + k)) for k, t in enumerate(["a", "b", "c", "d"])]
    await _log(n["id"], rows)
    got = await _note(client, n["id"])
    ids = {i["text"]: i["id"] for i in got["items"]}
    r = await client.post(f"{API}/notes/{n['id']}/items/{ids['d']}/move", json={"after": ids["a"]})
    assert r.status_code == 200, r.text
    got = await _note(client, n["id"])
    assert _section_texts(got, costco) == ["a", "d", "b", "c"]
    d = next(i for i in got["items"] if i["text"] == "d")
    a = next(i for i in got["items"] if i["text"] == "a")
    b = next(i for i in got["items"] if i["text"] == "b")
    assert a["rank"] < d["rank"] < b["rank"]
    # The undo puts the rank back.
    await client.post(f"{API}{r.json()['undo']['path']}", json=r.json()["undo"]["body"])
    assert _section_texts(await _note(client, n["id"]), costco) == ["a", "b", "c", "d"]
    # The end: the last rank + 0.05.
    await client.post(f"{API}/notes/{n['id']}/items/{ids['a']}/move", json={"section_id": costco})
    got = await _note(client, n["id"])
    assert _section_texts(got, costco)[-1] == "a"
    # Reset learned order; undo brings the ranks back.
    r = await client.patch(f"{API}/notes/{n['id']}/sections/{costco}", json={"reset_learned": True})
    got = await _note(client, n["id"])
    assert all(i["rank"] is None for i in got["items"]) and got["sections"][0]["trips"] == 0
    await client.post(f"{API}{r.json()['undo']['path']}", json=r.json()["undo"]["body"])
    got = await _note(client, n["id"])
    assert got["sections"][0]["trips"] == 2 and _section_texts(got, costco)[-1] == "a"


async def test_manual_mode_ignores_ranks(client):
    n = await _list(client, "Costco:\na\nb\nc")
    costco = n["sections"][0]["id"]
    await _log(n["id"], [(t, costco, at(k)) for k, t in enumerate(["c", "b", "a"])])
    assert _section_texts(await _note(client, n["id"]), costco) == ["c", "b", "a"]
    await client.patch(f"{API}/notes/{n['id']}/sections/{costco}", json={"order_mode": "manual"})
    assert _section_texts(await _note(client, n["id"]), costco) == ["a", "b", "c"]


async def test_learn_job_runs_from_the_worker(client):
    from mailr.db import sessionmaker
    from mailr.notes.models import Note
    from mailr.notes.tasks import learn_job

    n = await _list(client, "Costco:\na\nb\nc")
    costco = n["sections"][0]["id"]
    await _log(n["id"], [(t, costco, at(k)) for k, t in enumerate(["c", "b", "a"])])
    async with sessionmaker()() as db:
        nid = (await db.execute(select(Note.id).where(Note.pid == n["id"]))).scalar_one()
    await learn_job({"note_id": nid, "section_id": costco}, None)
    async with sessionmaker()() as db:
        note = await db.get(Note, nid)
        ranks = await learn.ranks_of(db, note)
    assert ranks[(costco, "c")][0] == 0.0


async def test_usual_items_suggest_add_dismiss(client):
    """Usual items: suggested once due, added to their last section with their last quantity
    (checkitem.usual_add), hidden for an interval or for good (checkitem.usual_dismiss) and back
    from "Hidden suggestions", off by the switch."""
    n = await _list(client, "Costco:\nmilk")
    pid, costco = n["id"], n["sections"][0]["id"]
    now = datetime.now(UTC)
    await _log(pid, [("Eggs", costco, now - timedelta(days=d)) for d in (21, 14, 7)])
    await _log(pid, [("coffee", None, now - timedelta(days=d)) for d in (40, 30, 20)])
    r = (await client.get(f"{API}/notes/{pid}/usual")).json()
    assert r["enabled"] is True
    assert [u["text"] for u in r["items"]] == ["coffee", "Eggs"]  # 20/10 before 7/7
    eggs = next(u for u in r["items"] if u["key"] == "egg")
    assert eggs["section"] == "Costco" and eggs["interval"] == 7.0
    r = await client.post(f"{API}/notes/{pid}/items", json={"usual": ["egg"]})
    assert r.status_code == 200, r.text
    added = r.json()["items"][0]
    assert added["text"] == "Eggs" and added["section_id"] == costco
    assert [u["key"] for u in (await client.get(f"{API}/notes/{pid}/usual")).json()["items"]] == ["coffee"]
    await client.post(f"{API}{r.json()['undo']['path']}", json=r.json()["undo"]["body"])
    r = await client.post(f"{API}/notes/{pid}/usual/coffee/dismiss", json={"for": "interval"})
    assert r.json()["undo"]["method"] == "DELETE"
    u = (await client.get(f"{API}/notes/{pid}/usual")).json()
    assert [x["key"] for x in u["items"]] == ["egg"] and u["hidden"][0]["key"] == "coffee"
    assert u["hidden"][0]["until"] is not None
    await client.delete(f"{API}/notes/{pid}/usual/coffee/dismiss")
    await client.post(f"{API}/notes/{pid}/usual/egg/dismiss", json={"for": "never"})
    u = (await client.get(f"{API}/notes/{pid}/usual")).json()
    assert [x["key"] for x in u["items"]] == ["coffee"] and u["hidden"] == [{"key": "egg", "text": "Eggs",
                                                                              "until": None}]
    await client.patch(f"{API}/notes/{pid}", json={"suggest_usual": False})
    u = (await client.get(f"{API}/notes/{pid}/usual")).json()
    assert u["enabled"] is False and u["items"] == []


async def test_check_logs_the_tap(client):
    n = await _list(client, "Costco:\n2 milk")
    pid, it = n["id"], n["items"][0]
    await client.patch(f"{API}/notes/{pid}/items/{it['id']}", json={"checked": True,
                                                                   "device_at": "2026-09-30T08:00:00+00:00"})
    from mailr.db import sessionmaker
    from mailr.notes.models import CheckitemCheck

    async with sessionmaker()() as db:
        row = (await db.execute(select(CheckitemCheck).where(CheckitemCheck.item_id == it["id"]))).scalar_one()
    assert row.key == "milk" and row.section_id == n["sections"][0]["id"] and row.qty["amount"] == "2"
    assert row.device_at == datetime(2026, 9, 30, 8, 0, tzinfo=UTC)
