"""Writes from a pruned base version: versions are kept
for KEEP / KEEP_HOURS (core/live.py), so a laptop asleep for a day edits from a version that is gone.
With no base to merge against, a changed field that differs from the stored copy is refused with
409; it is never stored over another writer's newer change.
"""

from __future__ import annotations

import pytest
from helpers.tables import new_table
from sqlalchemy import delete

from mailr.db import session_scope
from mailr.models import ItemVersion

pytestmark = pytest.mark.db_user

API = "/api/v1"


async def _prune(user_id: int, kind: str, item, version: int) -> None:
    """What live._insert's pruning does to a version older than KEEP and KEEP_HOURS."""
    async with session_scope() as db:
        await db.execute(delete(ItemVersion).where(ItemVersion.user_id == user_id, ItemVersion.kind == kind,
                                                   ItemVersion.item_id == str(item),
                                                   ItemVersion.version == int(version)))


async def test_a_note_body_from_a_pruned_base_is_refused(client, user):
    r = await client.post(f"{API}/notes", json={"kind": "text", "title": "Trip", "body": "pack\nbook\n"})
    n = r.json()["note"]
    v0 = n["version"]
    # Another device adds a line meanwhile.
    r = await client.patch(f"{API}/notes/{n['id']}", json={"body": "pack\nbook\ntickets\n", "base_version": v0})
    assert r.status_code == 200
    await _prune(user.id, "note", n["id"], v0)
    # The sleeping laptop saves its copy, made from v0: it would drop "tickets".
    r = await client.patch(f"{API}/notes/{n['id']}", json={"body": "pack\nbook\nshoes\n", "base_version": v0})
    assert r.status_code == 409, r.text
    assert r.json()["fields"] == ["body"] and r.json()["theirs"]["content"]["body"] == "pack\nbook\ntickets\n"
    got = (await client.get(f"{API}/notes/{n['id']}")).json()
    assert (got.get("note") or got)["body"] == "pack\nbook\ntickets\n"
    # The later write still wins for colour, and a value equal to the stored one is no conflict.
    r = await client.patch(f"{API}/notes/{n['id']}", json={"color": "sage", "base_version": v0})
    assert r.status_code == 200, r.text
    r = await client.patch(f"{API}/notes/{n['id']}", json={"title": "Trip", "base_version": v0})
    assert r.status_code == 200, r.text


async def test_a_table_row_cell_from_a_pruned_base_is_refused(client, user):
    t = await new_table(client, name="Weights", columns=[{"name": "Weight", "type": "number"},
                                                         {"name": "Notes", "type": "text"}])
    w = next(c for c in t["columns"] if c["name"] == "Weight")["id"]
    row = (await client.post(f"{API}/tables/{t['id']}/rows", json={"cells": {w: "80"}})).json()["rows"][0]
    v0 = row["version"]
    r = await client.patch(f"{API}/tables/{t['id']}/rows/{row['id']}", json={"cells": {w: "85"}, "base_version": v0})
    assert r.status_code == 200, r.text
    await _prune(user.id, "row", row["id"], v0)
    r = await client.patch(f"{API}/tables/{t['id']}/rows/{row['id']}", json={"cells": {w: "90"}, "base_version": v0})
    assert r.status_code == 409, r.text
    assert r.json()["fields"] == [f"cells.{w}"]
    rows = (await client.get(f"{API}/tables/{t['id']}/rows")).json()["rows"]
    assert str(rows[0]["cells"][w]) == "85"
    # The same value as stored: nothing to refuse.
    r = await client.patch(f"{API}/tables/{t['id']}/rows/{row['id']}", json={"cells": {w: "85"}, "base_version": v0})
    assert r.status_code == 200, r.text


async def test_a_table_name_from_a_pruned_base_is_refused(client, user):
    t = await new_table(client, name="Groceries")
    v0 = t["version"]
    r = await client.patch(f"{API}/tables/{t['id']}", json={"name": "Shopping", "base_version": v0})
    assert r.status_code == 200, r.text
    await _prune(user.id, "table", t["id"], v0)
    r = await client.patch(f"{API}/tables/{t['id']}", json={"name": "Food", "base_version": v0})
    assert r.status_code == 409, r.text
    r = await client.patch(f"{API}/tables/{t['id']}", json={"color": "tomato", "base_version": v0})
    assert r.status_code == 200, r.text
