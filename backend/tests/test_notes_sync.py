# ACT-SURFACES: api
"""The phones' delta sync: `GET /notes/sync?since=` and
`GET /tables/sync?since=`. Besides what changed, they list what was shared with the caller since
then, and `visible` (every note or table the caller sees now, with the level), so a phone drops what
was unshared, left or purged (note.share, note.unshare, note.leave, note.purge, table.share,
table.unshare, row.add, row.edit, row.delete, row.restore)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from helpers.notes_sharing import API, new_note, new_table, people  # noqa: F401  (fixture)

# These tests never touch the mail store: database-only users (conftest.make_db_user).
pytestmark = pytest.mark.db_user


def stamp() -> str:
    return datetime.now(UTC).isoformat()


async def tick():
    # Postgres times have microseconds; a short pause keeps "before" and "after" apart.
    await asyncio.sleep(0.05)


async def nsync(c, since=None):
    r = await c.get(f"{API}/notes/sync", params={"since": since} if since else None)
    assert r.status_code == 200, r.text
    return r.json()


async def tsync(c, since=None):
    r = await c.get(f"{API}/tables/sync", params={"since": since} if since else None)
    assert r.status_code == 200, r.text
    return r.json()


async def test_notes_sync_lists_a_note_shared_since(people):
    """note.share: a note shared with the caller after `since` is in the delta although the note did
    not change; `visible` holds it with the caller's level."""
    dana, sam = await people("Dana"), await people("Sam")
    n = await new_note(dana)
    await tick()
    since = stamp()
    await tick()
    assert (await nsync(sam, since))["notes"] == []
    r = await dana.put(f"{API}/notes/{n['id']}/shares/{sam.uid}", json={"level": "view", "email": sam.email})
    assert r.status_code == 200, r.text
    d = await nsync(sam, since)
    assert [x["id"] for x in d["notes"]] == [n["id"]]
    assert [i["text"] for i in d["notes"][0]["items"]] == ["milk", "eggs"]
    assert d["visible"] == {n["id"]: "view"}
    assert datetime.fromisoformat(d["now"]) > datetime.fromisoformat(since)


async def test_notes_sync_through_a_group_share(people):
    g = "House-sync"
    dana, sam = await people("Dana"), await people("Sam", groups=(g,))
    n = await new_note(dana)
    since = stamp()
    await tick()
    # A group share needs the sharer in the group: Dana joins it first.
    from mailr.db import sessionmaker
    from mailr.models import User

    async with sessionmaker()() as db:
        u = await db.get(User, dana.uid)
        u.assigned_groups = [g]
        await db.commit()
    r = await dana.put(f"{API}/notes/{n['id']}/shares/group:{g}", json={"level": "edit"})
    assert r.status_code == 200, r.text
    d = await nsync(sam, since)
    assert [x["id"] for x in d["notes"]] == [n["id"]] and d["visible"][n["id"]] == "edit"


async def test_notes_sync_visible_drops_unshared_left_and_purged(people):
    """note.unshare, note.leave, note.purge: what the caller no longer sees is missing from
    `visible`; a changed level shows there."""
    dana, sam = await people("Dana"), await people("Sam")
    a, b, c = await new_note(dana, title="A"), await new_note(dana, title="B"), await new_note(dana, title="C")
    for n in (a, b, c):
        await dana.put(f"{API}/notes/{n['id']}/shares/{sam.uid}", json={"level": "edit", "email": sam.email})
    d = await nsync(sam)
    assert set(d["visible"]) == {a["id"], b["id"], c["id"]}
    since = d["now"]
    await tick()
    await dana.put(f"{API}/notes/{a['id']}/shares/{sam.uid}", json={"level": "view", "email": sam.email})
    r = await dana.delete(f"{API}/notes/{b['id']}/shares/{sam.uid}")
    assert r.status_code == 200, r.text
    r = await sam.delete(f"{API}/notes/{c['id']}/shares/me")
    assert r.status_code == 200, r.text
    d = await nsync(sam, since)
    assert d["visible"] == {a["id"]: "view"}
    # Purged by the owner: gone from the owner's own `visible` too.
    mine = await new_note(dana, title="Mine")
    await dana.delete(f"{API}/notes/{mine['id']}")
    d = await nsync(dana, since)
    assert mine["id"] in d["trashed"] and mine["id"] not in d["visible"]
    await dana.delete(f"{API}/notes/{mine['id']}", params={"forever": "true"})
    d = await nsync(dana, since)
    assert mine["id"] not in d["trashed"] and mine["id"] not in d["visible"]
    assert d["visible"][a["id"]] == "owner"


async def test_notes_sync_bad_since(people):
    dana = await people("Dana")
    r = await dana.get(f"{API}/notes/sync", params={"since": "yesterday"})
    assert r.status_code == 400


async def test_tables_sync_full_then_delta(people):
    """row.add, row.edit, row.delete, row.restore: the delta holds the rows that changed and the
    rows deleted since; a table whose view changed comes with its columns and views."""
    dana = await people("Dana")
    t = await new_table(dana, columns=[{"name": "Item", "type": "text"}, {"name": "Qty", "type": "number"}])
    cols = {c["name"]: c["id"] for c in t["columns"]}
    r = await dana.post(f"{API}/tables/{t['id']}/rows", json={"rows": [{"cells": {cols["Item"]: "Pens", cols["Qty"]: 3}},
                                                                       {"cells": {cols["Item"]: "Ink"}}]})
    assert r.status_code == 200, r.text
    pens, ink = [x["id"] for x in r.json()["rows"]]
    d = await tsync(dana)
    assert [x["id"] for x in d["tables"]] == [t["id"]] and d["tables"][0]["columns"]
    assert {x["id"] for x in d["rows"]} == {pens, ink} and all(x["table"] == t["id"] for x in d["rows"])
    assert d["visible"] == {t["id"]: "owner"} and d["deleted_rows"] == []
    since = d["now"]
    await tick()
    assert (await tsync(dana, since))["rows"] == []
    r = await dana.patch(f"{API}/tables/{t['id']}/rows/{pens}", json={"cells": {cols["Qty"]: 4}})
    assert r.status_code == 200, r.text
    r = await dana.delete(f"{API}/tables/{t['id']}/rows/{ink}")
    assert r.status_code == 200, r.text
    d = await tsync(dana, since)
    assert [(x["id"], x["cells"][cols["Qty"]]) for x in d["rows"]] == [(pens, "4")]
    assert d["deleted_rows"] == [{"table": t["id"], "id": ink}]
    since2 = d["now"]
    await tick()
    r = await dana.post(f"{API}/tables/{t['id']}/rows/restore", json={"rows": [ink]})
    assert r.status_code == 200, r.text
    d = await tsync(dana, since2)
    assert [x["id"] for x in d["rows"]] == [ink] and d["deleted_rows"] == []
    # A view saved: the table comes with its views.
    since3 = d["now"]
    await tick()
    r = await dana.post(f"{API}/tables/{t['id']}/views", json={"name": "Big", "spec": {"sort": [{"col": cols["Qty"], "dir": "desc"}]}})
    assert r.status_code == 200, r.text
    d = await tsync(dana, since3)
    assert [x["id"] for x in d["tables"]] == [t["id"]] and any(v["name"] == "Big" for v in d["tables"][0]["views"])


async def test_tables_sync_shared_table_comes_with_every_row_and_leaves(people):
    """table.share, table.unshare: a table shared since the last sync comes with all its live rows;
    after the share stops it is missing from `visible`; Trash lists it by id."""
    dana, sam = await people("Dana"), await people("Sam")
    t = await new_table(dana)
    col = t["columns"][0]["id"]
    await dana.post(f"{API}/tables/{t['id']}/rows", json={"cells": {col: "Rent"}})
    await tick()
    since = (await tsync(sam))["now"]
    await tick()
    r = await dana.put(f"{API}/tables/{t['id']}/shares/{sam.uid}", json={"level": "edit", "email": sam.email})
    assert r.status_code == 200, r.text
    d = await tsync(sam, since)
    assert [x["id"] for x in d["tables"]] == [t["id"]] and d["tables"][0]["level"] == "edit"
    assert [x["cells"][col] for x in d["rows"]] == ["Rent"]
    assert d["visible"] == {t["id"]: "edit"}
    since = d["now"]
    await dana.delete(f"{API}/tables/{t['id']}/shares/{sam.uid}")
    d = await tsync(sam, since)
    assert d["visible"] == {} and d["tables"] == []
    await dana.delete(f"{API}/tables/{t['id']}")
    d = await tsync(dana, since)
    assert d["trashed"] == [t["id"]] and d["visible"] == {}


async def test_tables_sync_bad_since_and_nothing_new(people):
    dana = await people("Dana")
    r = await dana.get(f"{API}/tables/sync", params={"since": "soon"})
    assert r.status_code == 400
    assert (await tsync(dana, (datetime.now(UTC) - timedelta(days=1)).isoformat()))["tables"] == []


async def test_notes_sync_carries_a_changed_reminder(people):
    """note.remind: setting or removing the caller's reminder brings the note back in the delta with
    its reminders, so a phone plans (or drops) the n| key."""
    dana = await people("Dana")
    n = await new_note(dana)
    since = (await nsync(dana))["now"]
    await tick()
    at = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0).isoformat()
    r = await dana.put(f"{API}/notes/{n['id']}/reminders", json={"at": [at]})
    assert r.status_code == 200, r.text
    d = await nsync(dana, since)
    assert [x["id"] for x in d["notes"]] == [n["id"]]
    assert d["notes"][0]["reminders"][0]["key"].startswith(f"n|{n['id']}|abs|")
    since = d["now"]
    await tick()
    r = await dana.put(f"{API}/notes/{n['id']}/reminders", json={"at": []})
    assert r.status_code == 200, r.text
    d = await nsync(dana, since)
    assert [x["id"] for x in d["notes"]] == [n["id"]] and d["notes"][0]["reminders"] == []
