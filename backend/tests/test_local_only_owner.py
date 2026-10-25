"""Who may change Local models only on notes, lists and tables, and who hears about it
(notes/localonly.py). Also: an endpoint is marked local only when its address is private.

- Editors may turn it on; only the owner (for a group item, its creator) may turn it off (403).
- A change by someone else leaves the owner a notice and a history entry.
- An editor's revert of the table history keeps it on.
- An agent's request to turn it off asks first, and the approval card says what it does.
"""

from __future__ import annotations

import uuid

from helpers.notes_sharing import (  # noqa: F401 - fixtures
    new_note,
    new_table,
    people,
    policy,
    share,
)

API = "/api/v1"


async def test_only_the_owner_lowers_local_only_on_a_note(people, policy):
    await policy("anyone")
    owner = await people("Olive")
    dana = await people("Dana")
    n = await new_note(owner, title="Camping")
    await share(owner, "note", n["id"], str(dana.uid), "edit")

    # An editor raises it: allowed, and the owner hears about it.
    r = await dana.patch(f"{API}/notes/{n['id']}", json={"local_only": True})
    assert r.status_code == 200, r.text
    assert r.json()["note"]["local_only"] is True
    notices = (await owner.get(f"{API}/notes/share-notices")).json()["notices"]
    assert [(x["what"], x["on"], x["by"], x["name"]) for x in notices] == [("local_only", True, "Dana", "Camping")]
    revs = (await owner.get(f"{API}/notes/{n['id']}/revisions")).json()["revisions"]
    assert "Local models only on" in revs[0]["summary"]

    # The editor cannot lower it.
    r = await dana.patch(f"{API}/notes/{n['id']}", json={"local_only": False})
    assert r.status_code == 403
    assert "Only the owner" in r.json()["detail"]
    # The owner can, and hears nothing about their own change.
    await owner.delete(f"{API}/notes/share-notices")
    r = await owner.patch(f"{API}/notes/{n['id']}", json={"local_only": False})
    assert r.status_code == 200 and r.json()["note"]["local_only"] is False
    assert (await owner.get(f"{API}/notes/share-notices")).json()["notices"] == []
    revs = (await owner.get(f"{API}/notes/{n['id']}/revisions")).json()["revisions"]
    assert "Local models only off" in revs[0]["summary"]


async def test_only_the_owner_lowers_local_only_on_a_table(people, policy):
    await policy("anyone")
    owner = await people("Olive")
    dana = await people("Dana")
    t = await new_table(owner, name="Trip costs")
    await share(owner, "table", t["id"], str(dana.uid), "edit")
    r = await dana.patch(f"{API}/tables/{t['id']}", json={"local_only": True})
    assert r.status_code == 200, r.text
    notices = (await owner.get(f"{API}/notes/share-notices")).json()["notices"]
    assert [(x["what"], x["kind"], x["on"]) for x in notices] == [("local_only", "table", True)]
    hist = (await owner.get(f"{API}/tables/{t['id']}/changes")).json()
    assert any("Turned Local models only on" in c["summary"] for s in hist["sessions"] for c in s["changes"])
    assert (await dana.patch(f"{API}/tables/{t['id']}", json={"local_only": False})).status_code == 403

    # Dana reverts the table to before her change: the flag stays on.
    first = hist["sessions"][0]
    r = await dana.post(f"{API}/tables/{t['id']}/restore-point", json={"seq": first["from_seq"] - 1})
    assert r.status_code == 200, r.text
    assert (await owner.get(f"{API}/tables/{t['id']}")).json()["local_only"] is True

    r = await owner.patch(f"{API}/tables/{t['id']}", json={"local_only": False})
    assert r.status_code == 200 and r.json()["table"]["local_only"] is False


async def test_group_items_the_creator_is_the_owner(people, policy):
    await policy("anyone")
    g = f"House-{uuid.uuid4().hex[:6]}"
    olive = await people("Olive", (g,))
    gus = await people("Gus", (g,))
    r = await olive.post(f"{API}/notes", json={"kind": "checklist", "title": "Chores", "lines": "dishes",
                                               "group": g, "local_only": True})
    assert r.status_code == 200, r.text
    pid = r.json()["note"]["id"]
    assert (await gus.patch(f"{API}/notes/{pid}", json={"local_only": False})).status_code == 403
    assert (await olive.patch(f"{API}/notes/{pid}", json={"local_only": False})).status_code == 200


def test_the_approval_card_shows_the_change_and_lowering_is_dangerous():
    from types import SimpleNamespace

    from mailr.notes import gate, localonly

    note = SimpleNamespace(title="Camping", kind="checklist", local_only=True)
    _title, lines, danger = gate.describe("note.edit", {"local_only": False}, note)
    assert danger and any("Local models only: off" in x for x in lines)
    _title, lines, danger = gate.describe("note.edit", {"local_only": True},
                                         SimpleNamespace(title="Camping", kind="checklist", local_only=False))
    assert not danger and "Local models only: on" in lines
    assert localonly.lowers(note, False) and not localonly.lowers(note, True) and not localonly.lowers(note, None)


async def test_an_agent_turning_it_off_always_asks():
    from types import SimpleNamespace

    from mailr.notes import gate, localonly

    actor = SimpleNamespace(kind="assistant", user=SimpleNamespace(id=1), token=None)
    note = SimpleNamespace(title="Camping", kind="checklist", local_only=True, owner_user_id=1, owner_group=None)
    _soft, force = await gate.reasons(None, actor, "note.edit", note, {"local_only": False}, tainted=False)
    assert force == localonly.LOWER_ASKS


async def test_a_public_address_cannot_be_marked_local(admin_client):
    body = {"name": f"pub-{uuid.uuid4().hex[:6]}", "base_url": "http://8.8.8.8/v1", "locality": "local", "models": ["m"]}
    r = await admin_client.post(f"{API}/admin/models", json=body)
    assert r.status_code == 400
    assert "public address, so it can't be marked local" in r.json()["detail"]
    r = await admin_client.post(f"{API}/admin/models", json={**body, "locality": "hosted"})
    assert r.status_code == 200, r.text
    eid = r.json()["id"]
    r = await admin_client.patch(f"{API}/admin/models/{eid}", json={**body, "locality": "local"})
    assert r.status_code == 400
    r = await admin_client.post(f"{API}/admin/models", json={**body, "name": body["name"] + "-lan",
                                                             "base_url": "http://192.168.1.20:8000/v1"})
    assert r.status_code == 200, r.text
