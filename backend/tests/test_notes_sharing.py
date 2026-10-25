# ACT-SURFACES: api
"""Sharing notes, checklists and tables (notes/sharing.py, notes/acl.py,
notes/share_routes.py).

Covered, with their action ids: note.share, note.unshare, note.leave, note.move_group,
note.share_notices, table.share, table.unshare, table.leave, table.move_group. Also: the access
matrix (owner, share, edit, view, none, for notes and tables, through a user and through a group),
the one instance policy (anyone, groups, nobody) for new shares and level changes, API tokens
refused, re-sharing that never touches the owner or oneself, group-owned lists and the creator-or-admin
rule, group membership changes, deleted and disabled users, live events that reach the sharees with
their author, the Shared with me view, Trash, `is:shared`, the "changed since you looked" dot, and
authors on items and rows.
"""

from __future__ import annotations

import uuid

import pytest
from helpers.notes_sharing import (  # noqa: F401
    API,
    EMAILS,
    PW,
    new_note,
    new_table,
    people,
    policy,
    share,
)
from httpx import AsyncClient

from mailr.db import sessionmaker
from mailr.models import User

# These tests never touch the mail store: database-only users (conftest.make_db_user).
pytestmark = pytest.mark.db_user


def gname() -> str:
    return f"House-{uuid.uuid4().hex[:6]}"


async def can_edit(c, kind, pid) -> int:
    if kind == "note":
        r = await c.post(f"{API}/notes/{pid}/items", json={"lines": "bread"})
    else:
        r = await c.patch(f"{API}/tables/{pid}", json={"name": "Spending"})
    return r.status_code


# ---------------------------------------------------------------- the access matrix


@pytest.mark.parametrize("kind", ["note", "table"])
@pytest.mark.parametrize("via", ["user", "group"])
async def test_access_matrix(people, policy, kind, via):
    """note.share / table.share at view, edit and share, through a user or a group: view reads only,
    edit writes, share also manages other people (never the owner or themselves); nobody else sees
    it (404, as an id that does not exist)."""
    await policy("anyone")
    g = gname()
    owner = await people("Olive", (g,))
    viewer, editor, sharer = (await people("Vic", (g + "-v",)), await people("Eda", (g + "-e",)),
                              await people("Sam", (g + "-s",)))
    stranger = await people("Xan")
    item = await (new_note(owner) if kind == "note" else new_table(owner))
    pid = item["id"]
    for who, lv in ((viewer, "view"), (editor, "edit"), (sharer, "share")):
        principal = str(who.uid) if via == "user" else f"group:{g}-{lv[0]}"
        if via == "group":  # the owner must be in a group to share with it
            async with sessionmaker()() as db:
                u = await db.get(User, owner.uid)
                u.assigned_groups = sorted({*(u.assigned_groups or []), f"{g}-{lv[0]}"})
                await db.commit()
        out = await share(owner, kind, pid, principal, lv)
        listed = out["people"] if via == "user" else out["groups"]
        assert any(x["level"] == lv for x in listed)
    # Levels as each person sees them.
    for who, lv in ((viewer, "view"), (editor, "edit"), (sharer, "share")):
        r = await who.get(f"{API}/{kind}s/{pid}")
        assert r.status_code == 200 and r.json()["level"] == lv
    assert (await owner.get(f"{API}/{kind}s/{pid}")).json()["level"] == "owner"
    r = await stranger.get(f"{API}/{kind}s/{pid}")
    assert r.status_code == 404
    assert (await stranger.get(f"{API}/{kind}s/{pid}/shares")).status_code == 404
    # view: reads, never writes; edit and share write.
    assert await can_edit(viewer, kind, pid) == 403
    assert await can_edit(editor, kind, pid) == 200
    assert await can_edit(sharer, kind, pid) == 200
    assert await can_edit(stranger, kind, pid) == 404
    # Only the owner (or the creator of a group item, or an admin) deletes.
    assert (await editor.delete(f"{API}/{kind}s/{pid}")).status_code == 403
    # share: manages others, not the owner or themselves; edit cannot share at all.
    third = await people("Tia")
    await share(editor, kind, pid, str(third.uid), "view", 403)
    await share(sharer, kind, pid, str(third.uid), "edit")
    assert (await third.get(f"{API}/{kind}s/{pid}")).json()["level"] == "edit"
    await share(sharer, kind, pid, str(owner.uid), "view", 400)
    r = await sharer.put(f"{API}/{kind}s/{pid}/shares/me", json={"level": "edit"})
    assert r.status_code == 400
    await share(sharer, kind, pid, str(sharer.uid), "edit", 400) if via == "user" else None
    # Stop (note.unshare / table.unshare): the person loses it at once.
    r = await sharer.delete(f"{API}/{kind}s/{pid}/shares/{third.uid}")
    assert r.status_code == 200
    assert (await third.get(f"{API}/{kind}s/{pid}")).status_code == 404
    # A view-only sharee still pins and labels it for themselves (per-viewer state never leaks).
    r = await viewer.put(f"{API}/{kind}s/{pid}/state", json={"pinned": True, "labels": ["Theirs"]})
    assert r.status_code == 200
    mine = (await owner.get(f"{API}/{kind}s/{pid}")).json()["state"]
    assert mine["pinned"] is False and mine["labels"] == []


async def test_share_rows_and_summary(people, policy):
    """The dialog lists people and groups; the card says who else sees it; the sharee sees the owner."""
    await policy("anyone")
    g = gname()
    owner = await people("Olive", (g,))
    dana = await people("Dana")
    n = await new_note(owner)
    out = await share(owner, "note", n["id"], str(dana.uid), "edit")
    assert out["own"] is True and out["can_share"] is True and out["policy"] == "anyone"
    assert [p["name"] for p in out["people"]] == ["Dana"]
    assert all(c["id"] != dana.uid for c in out["candidates"])
    assert g in out["group_candidates"] and out["can_move_in"] is True
    await share(owner, "note", n["id"], f"group:{g}", "view")
    card = (await owner.get(f"{API}/notes/{n['id']}")).json()
    assert card["sharing"]["count"] == 2
    assert {p["name"] for p in card["sharing"]["people"]} == {"Dana", g}
    theirs = (await dana.get(f"{API}/notes/{n['id']}")).json()
    assert theirs["owner"]["name"] == "Olive" and theirs["level"] == "edit"
    d = (await dana.get(f"{API}/notes/{n['id']}/shares")).json()
    assert d["can_share"] is False and d["candidates"] == [] and d["can_leave"] is True


# ---------------------------------------------------------------- the policy and tokens


async def test_policy_governs_new_shares_and_changes(people, policy):
    """calendars.sharing covers notes and tables: nobody refuses new shares and level changes but
    still lets people stop; groups needs a shared group; existing shares stay."""
    g = gname()
    owner = await people("Olive", (g,))
    mate = await people("Mia", (g,))
    other = await people("Ola", ("elsewhere-" + g,))
    n = await new_note(owner)
    t = await new_table(owner)
    await policy("groups")
    await share(owner, "note", n["id"], str(mate.uid), "view")
    # Outside the owner's groups and not on the note: unknown without the address ...
    r = await owner.put(f"{API}/notes/{n['id']}/shares/{other.uid}", json={"level": "view"})
    assert r.status_code == 404 and "Ola" not in r.text
    # ... and refused by the policy with it.
    r = await owner.put(f"{API}/notes/{n['id']}/shares/{other.uid}", json={"level": "view", "email": other.email})
    assert r.status_code == 403 and "cannot share with Ola" in r.json()["detail"]
    d = (await owner.get(f"{API}/notes/{n['id']}/shares")).json()
    assert all(c["id"] != other.uid for c in d["candidates"]) and any(c["id"] == mate.uid for c in d["candidates"]) is False
    await policy("anyone")
    await share(owner, "table", t["id"], str(other.uid), "view")
    await policy("nobody")
    r = await owner.put(f"{API}/notes/{n['id']}/shares/{mate.uid}", json={"level": "edit"})  # a group-mate
    assert r.status_code == 403 and "turned off" in r.json()["detail"]
    # Existing shares stay and can be stopped.
    assert (await other.get(f"{API}/tables/{t['id']}")).status_code == 200
    assert (await owner.delete(f"{API}/tables/{t['id']}/shares/{other.uid}")).status_code == 200
    assert (await other.get(f"{API}/tables/{t['id']}")).status_code == 404
    assert (await owner.get(f"{API}/notes/{n['id']}/shares")).json()["candidates"] == []
    # Under "nobody" a new list in a group, or a move into one, is refused too (a group list is shared).
    r = await owner.post(f"{API}/notes", json={"kind": "checklist", "title": "x", "group": g})
    assert r.status_code == 403
    r = await owner.post(f"{API}/notes/{n['id']}/move-to-group", json={"group": g})
    assert r.status_code == 403


async def test_tokens_get_403_on_every_share_route(client, user, people, policy):
    """Only the signed-in user shares: a token with the notes scope gets 403 on every share route."""
    await policy("anyone")
    dana = await people("Dana")
    n = await new_note(client)
    t = await new_table(client)
    r = await client.post(f"{API}/tokens", json={"name": "agent", "scopes": ["read", "notes"]})
    tok = r.json()["token"]
    async with AsyncClient(transport=client._transport, base_url="http://test",
                           headers={"Authorization": f"Bearer {tok}"}) as a:
        assert (await a.get(f"{API}/notes/{n['id']}")).status_code == 200
        for kind, pid in (("note", n["id"]), ("table", t["id"])):
            for method, path, body in (
                    ("GET", f"/{kind}s/{pid}/shares", None),
                    ("PUT", f"/{kind}s/{pid}/shares/{dana.uid}", {"level": "view"}),
                    ("DELETE", f"/{kind}s/{pid}/shares/{dana.uid}", None),
                    ("DELETE", f"/{kind}s/{pid}/shares/me", None),
                    ("POST", f"/{kind}s/{pid}/shares/me", None),
                    ("POST", f"/{kind}s/{pid}/move-to-group", {"group": "x"})):
                r = await a.request(method, API + path, json=body)
                assert r.status_code == 403, (method, path, r.text)
        assert (await a.get(f"{API}/notes/share-notices")).status_code == 403
        # A token cannot make a group list either: a group list is shared with the group.
        r = await a.post(f"{API}/notes", json={"kind": "checklist", "title": "x", "group": "testers"})
        assert r.status_code == 403
    assert (await dana.get(f"{API}/notes/{n['id']}")).status_code == 404


# ---------------------------------------------------------------- leave


@pytest.mark.parametrize("kind", ["note", "table"])
async def test_leave_and_add_back(people, policy, kind):
    """note.leave / table.leave: a sharee removes the item from their own view (a group that still has
    it does not bring it back); the undo adds it back; the owner and group members cannot leave."""
    await policy("anyone")
    g = gname()
    owner = await people("Olive", (g,))
    dana = await people("Dana", (g,))
    cara = await people("Cara", (g,))
    item = await (new_note(owner) if kind == "note" else new_table(owner))
    pid = item["id"]
    await share(owner, kind, pid, str(dana.uid), "edit")
    await share(owner, kind, pid, f"group:{g}", "view")
    r = await dana.delete(f"{API}/{kind}s/{pid}/shares/me")
    assert r.status_code == 200, r.text
    undo = r.json()["undo"]
    assert undo["method"] == "POST" and undo["path"] == f"/{kind}s/{pid}/shares/me"
    assert (await dana.get(f"{API}/{kind}s/{pid}")).status_code == 404
    grid = (await dana.get(f"{API}/notes")).json()
    assert pid not in [c["id"] for c in grid["notes"] + grid["tables"]]
    assert (await cara.get(f"{API}/{kind}s/{pid}")).status_code == 200  # the others keep it
    r = await dana.request(undo["method"], API + undo["path"], json=undo["body"])
    assert r.status_code == 200, r.text
    assert (await dana.get(f"{API}/{kind}s/{pid}")).json()["level"] == "edit"
    # A share that came only through the group: leaving hides it from Cara alone.
    assert (await cara.delete(f"{API}/{kind}s/{pid}/shares/me")).status_code == 200
    assert (await cara.get(f"{API}/{kind}s/{pid}")).status_code == 404
    assert (await cara.post(f"{API}/{kind}s/{pid}/shares/me")).status_code == 200
    assert (await cara.get(f"{API}/{kind}s/{pid}")).json()["level"] == "view"
    # The owner cannot leave; a re-share after a leave brings it back.
    assert (await owner.delete(f"{API}/{kind}s/{pid}/shares/me")).status_code == 400
    await dana.delete(f"{API}/{kind}s/{pid}/shares/me")
    await share(owner, kind, pid, str(dana.uid), "view")
    assert (await dana.get(f"{API}/{kind}s/{pid}")).json()["level"] == "view"
    # A stop after a leave: the add back finds nothing.
    await dana.delete(f"{API}/{kind}s/{pid}/shares/me")
    assert (await dana.post(f"{API}/{kind}s/{pid}/shares/me")).status_code == 200
    await owner.delete(f"{API}/{kind}s/{pid}/shares/{dana.uid}")
    await owner.delete(f"{API}/{kind}s/{pid}/shares/group:{g}")
    assert (await dana.post(f"{API}/{kind}s/{pid}/shares/me")).status_code == 404


# ---------------------------------------------------------------- group-owned


@pytest.mark.parametrize("kind", ["note", "table"])
async def test_group_owned_items(people, policy, kind):
    """"New list in <group>": every member edits; only the creator or an admin deletes it or moves it
    out; note.move_group / table.move_group; a member who leaves the group loses it."""
    await policy("anyone")
    g = gname()
    maker = await people("Mo", (g,))
    member = await people("Meg", (g,))
    outsider = await people("Oz")
    admin = await people("Ada", admin=True)
    if kind == "note":
        r = await maker.post(f"{API}/notes", json={"kind": "checklist", "title": "House", "group": g})
    else:
        r = await maker.post(f"{API}/tables", json={"name": "House", "group": g})
    assert r.status_code == 200, r.text
    item = r.json()[kind]
    pid = item["id"]
    assert item["owner"] == {"kind": "group", "name": g} and item["level"] == "share"
    assert (await member.get(f"{API}/{kind}s/{pid}")).json()["level"] == "edit"
    assert await can_edit(member, kind, pid) == 200
    assert (await outsider.get(f"{API}/{kind}s/{pid}")).status_code == 404
    bad = await (outsider.post(f"{API}/notes", json={"title": "x", "group": g}) if kind == "note"
                 else outsider.post(f"{API}/tables", json={"name": "x", "group": g}))
    assert bad.status_code == 403
    # Members cannot delete, leave or move it out.
    assert (await member.delete(f"{API}/{kind}s/{pid}")).status_code == 403
    assert (await member.delete(f"{API}/{kind}s/{pid}/shares/me")).status_code == 403
    r = await member.post(f"{API}/{kind}s/{pid}/move-to-group", json={"group": None})
    assert r.status_code == 403
    # A member who leaves the group loses it; the item stays.
    async with sessionmaker()() as db:
        (await db.get(User, member.uid)).assigned_groups = []
        await db.commit()
    assert (await member.get(f"{API}/{kind}s/{pid}")).status_code == 404
    assert (await maker.get(f"{API}/{kind}s/{pid}")).status_code == 200
    # An admin deletes it and restores it; the creator moves it out (it becomes the creator's).
    assert (await admin.delete(f"{API}/{kind}s/{pid}")).status_code == 200
    assert (await admin.post(f"{API}/{kind}s/{pid}/restore")).status_code == 200
    r = await maker.post(f"{API}/{kind}s/{pid}/move-to-group", json={"group": None})
    assert r.status_code == 200, r.text
    assert r.json()["owner"]["id"] == maker.uid
    assert (await maker.get(f"{API}/{kind}s/{pid}")).json()["level"] == "owner"
    # The owner moves it back into a group of theirs, not into someone else's.
    r = await maker.post(f"{API}/{kind}s/{pid}/move-to-group", json={"group": "not-mine-" + g})
    assert r.status_code == 403
    r = await maker.post(f"{API}/{kind}s/{pid}/move-to-group", json={"group": g})
    assert r.status_code == 200 and r.json()["owner"] == {"kind": "group", "name": g}


# ---------------------------------------------------------------- notices, events


async def test_notices_and_dismiss(people, policy):
    """note.share_notices: a new share leaves a quiet notice for the sharee (and each member of a
    group); a stop drops it; dismissing clears the list."""
    await policy("anyone")
    g = gname()
    owner = await people("Olive", (g,))
    dana = await people("Dana")
    gus = await people("Gus", (g,))
    n = await new_note(owner, title="Camping")
    t = await new_table(owner, name="Trip costs")
    await share(owner, "note", n["id"], str(dana.uid), "view")
    await share(owner, "table", t["id"], f"group:{g}", "edit")
    nd = (await dana.get(f"{API}/notes/share-notices")).json()["notices"]
    assert [(x["kind"], x["name"], x["by"]) for x in nd] == [("note", "Camping", "Olive")]
    ng = (await gus.get(f"{API}/notes/share-notices")).json()["notices"]
    assert [(x["kind"], x["name"]) for x in ng] == [("table", "Trip costs")]
    assert (await owner.get(f"{API}/notes/share-notices")).json()["notices"] == []
    # A level change is not a new share; a stop drops the notice.
    await share(owner, "note", n["id"], str(dana.uid), "edit")
    assert len((await dana.get(f"{API}/notes/share-notices")).json()["notices"]) == 1
    await owner.delete(f"{API}/notes/{n['id']}/shares/{dana.uid}")
    assert (await dana.get(f"{API}/notes/share-notices")).json()["notices"] == []
    assert (await gus.delete(f"{API}/notes/share-notices")).status_code == 200
    assert (await gus.get(f"{API}/notes/share-notices")).json()["notices"] == []


async def test_live_events_reach_sharees_with_their_author(people, policy, monkeypatch):
    """A write to a shared list reaches every viewer (owner, sharees, group members) with its author,
    for "Dana is editing"; a stop tells the person who lost it."""
    from mailr.core import events

    await policy("anyone")
    owner = await people("Olive")
    dana = await people("Dana")
    n = await new_note(owner)
    t = await new_table(owner)
    await share(owner, "note", n["id"], str(dana.uid), "edit")
    await share(owner, "table", t["id"], str(dana.uid), "edit")
    got: list[tuple[int, dict]] = []
    real = events.notify

    async def record(db, uid, ev):
        got.append((uid, ev))
        await real(db, uid, ev)

    monkeypatch.setattr(events, "notify", record)
    r = await dana.post(f"{API}/notes/{n['id']}/items", json={"lines": "bread"})
    assert r.status_code == 200
    items = [(u, e) for u, e in got if e.get("type") == "item" and e.get("kind") == "checkitem"]
    assert {u for u, _ in items} == {owner.uid, dana.uid}
    assert all(e["author"] == {"id": dana.uid, "name": "Dana"} for _, e in items)
    got.clear()
    cols = (await owner.get(f"{API}/tables/{t['id']}")).json()["columns"]
    r = await dana.post(f"{API}/tables/{t['id']}/rows", json={"cells": {cols[0]["id"]: "Fuel"}})
    assert r.status_code == 200, r.text
    rows = [(u, e) for u, e in got if e.get("type") == "item" and e.get("kind") == "row"]
    assert {u for u, _ in rows} == {owner.uid, dana.uid} and rows[0][1]["author"]["name"] == "Dana"
    got.clear()
    await owner.delete(f"{API}/notes/{n['id']}/shares/{dana.uid}")
    lost = [e for u, e in got if u == dana.uid and e.get("what") == "unshared"]
    assert lost and lost[0]["by_name"] == "Olive" and lost[0]["id"] == n["id"]


# ---------------------------------------------------------------- people who go


async def test_deleted_and_disabled_users(people, policy):
    """Deleting a user removes their shares and the shares of what they owned; a disabled user's
    shares are inert (no access, no events) and come back with the account."""
    from mailr.notes import access
    from mailr.notes.models import Note, NoteShare

    await policy("anyone")
    admin = await people("Ada", admin=True)
    owner = await people("Olive")
    dana = await people("Dana")
    n = await new_note(owner)
    theirs = await new_note(dana, title="Dana's")
    await share(owner, "note", n["id"], str(dana.uid), "edit")
    await share(dana, "note", theirs["id"], str(owner.uid), "view")
    async with sessionmaker()() as db:
        (await db.get(User, dana.uid)).disabled = True
        await db.commit()
    async with sessionmaker()() as db:
        note = (await db.execute(Note.__table__.select().where(Note.pid == n["id"]))).first()
        obj = await db.get(Note, note.id)
        assert await access.audience(db, obj) == [owner.uid]
        assert access.level(await db.get(User, dana.uid), obj) == "none"
        (await db.get(User, dana.uid)).disabled = False
        await db.commit()
    assert (await dana.get(f"{API}/notes/{n['id']}")).json()["level"] == "edit"
    r = await admin.delete(f"{API}/admin/users/{dana.uid}", params={"confirm": (await _email(dana.uid))})
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        from sqlalchemy import select

        rows = (await db.execute(select(NoteShare).where(NoteShare.principal == str(dana.uid)))).scalars().all()
        assert rows == []
    assert (await owner.get(f"{API}/notes/{theirs['id']}")).status_code == 404
    assert (await owner.get(f"{API}/notes/{n['id']}/shares")).json()["people"] == []


async def _email(uid: int) -> str:
    async with sessionmaker()() as db:
        return (await db.get(User, uid)).email


# ---------------------------------------------------------------- views, search, the dot, authors


async def test_shared_view_trash_search_and_the_dot(people, policy):
    """The Shared view holds what others own (by owner); Trash holds only what the viewer may
    restore; `is:shared` finds shared notes and tables; the dot shows after someone else's change
    and goes when the viewer opens it; items and rows name their authors."""
    await policy("anyone")
    owner = await people("Olive")
    dana = await people("Dana")
    n = await new_note(owner, title="Weekend")
    mine = await new_note(owner, title="Private")
    t = await new_table(owner, name="Ledger")
    await share(owner, "note", n["id"], str(dana.uid), "edit")
    await share(owner, "table", t["id"], str(dana.uid), "edit")
    shared = (await dana.get(f"{API}/notes", params={"view": "shared"})).json()
    assert [c["id"] for c in shared["notes"]] == [n["id"]] and [c["id"] for c in shared["tables"]] == [t["id"]]
    assert (await owner.get(f"{API}/notes", params={"view": "shared"})).json()["notes"] == []
    found = (await owner.get(f"{API}/notes/search", params={"q": "is:shared"})).json()
    assert [c["id"] for c in found["notes"]] == [n["id"]] and [c["id"] for c in found["tables"]] == [t["id"]]
    assert mine["id"] not in [c["id"] for c in found["notes"]]
    found = (await dana.get(f"{API}/notes/search", params={"q": "is:shared weekend"})).json()
    assert [c["id"] for c in found["notes"]] == [n["id"]]
    # The dot: Dana adds an item; the owner sees it until they open the note.
    await dana.post(f"{API}/notes/{n['id']}/items", json={"lines": "tent"})
    card = next(c for c in (await owner.get(f"{API}/notes")).json()["notes"] if c["id"] == n["id"])
    assert card["changed"] is True
    full = (await owner.get(f"{API}/notes/{n['id']}")).json()
    tent = next(i for i in full["items"] if i["text"] == "tent")
    assert tent["added_by"] == {"id": dana.uid, "name": "Dana"}
    assert full["updated_by"]["name"] == "Dana"
    assert (await owner.put(f"{API}/notes/{n['id']}/state", json={"seen": True})).status_code == 200
    card = next(c for c in (await owner.get(f"{API}/notes")).json()["notes"] if c["id"] == n["id"])
    assert card["changed"] is False
    # Own writes never light the dot.
    await owner.post(f"{API}/notes/{n['id']}/items", json={"lines": "stove"})
    card = next(c for c in (await owner.get(f"{API}/notes")).json()["notes"] if c["id"] == n["id"])
    assert card["changed"] is False
    # Tables: a row's author, and the dot.
    cols = (await owner.get(f"{API}/tables/{t['id']}")).json()["columns"]
    r = await dana.post(f"{API}/tables/{t['id']}/rows", json={"cells": {cols[0]["id"]: "Gas"}})
    row = r.json()["rows"][0]
    assert row["created_by"]["name"] == "Dana"
    tc = next(c for c in (await owner.get(f"{API}/notes")).json()["tables"] if c["id"] == t["id"])
    assert tc["changed"] is True
    await owner.put(f"{API}/tables/{t['id']}/state", json={"seen": True})
    tc = next(c for c in (await owner.get(f"{API}/notes")).json()["tables"] if c["id"] == t["id"])
    assert tc["changed"] is False
    # Trash: Dana does not see the owner's deleted note, and gets it back on restore.
    await owner.delete(f"{API}/notes/{n['id']}")
    assert n["id"] not in [c["id"] for c in (await dana.get(f"{API}/notes", params={"view": "trash"})).json()["notes"]]
    assert n["id"] not in [c["id"] for c in (await dana.get(f"{API}/notes")).json()["notes"]]
    await owner.post(f"{API}/notes/{n['id']}/restore")
    assert n["id"] in [c["id"] for c in (await dana.get(f"{API}/notes")).json()["notes"]]
    # Deleted forever: the shares go too.
    await owner.delete(f"{API}/notes/{n['id']}")
    assert (await owner.delete(f"{API}/notes/{n['id']}", params={"forever": True})).status_code == 200
    from sqlalchemy import select

    from mailr.notes.models import NoteShare
    async with sessionmaker()() as db:
        left = (await db.execute(select(NoteShare).where(NoteShare.item_kind == "note",
                                                         NoteShare.created_by == owner.uid))).scalars().all()
        assert left == []


async def test_learned_order_counts_everyones_checks(people, policy):
    """Shared lists learn from everyone's checks: both people's checks
    land in the list's check log."""
    from sqlalchemy import select

    from mailr.notes.models import CheckitemCheck, Note

    await policy("anyone")
    owner = await people("Olive")
    dana = await people("Dana")
    n = await new_note(owner, lines="milk\neggs")
    await share(owner, "note", n["id"], str(dana.uid), "edit")
    full = (await owner.get(f"{API}/notes/{n['id']}")).json()
    milk, eggs = full["items"][0]["id"], full["items"][1]["id"]
    assert (await owner.patch(f"{API}/notes/{n['id']}/items/{milk}", json={"checked": True})).status_code == 200
    assert (await dana.patch(f"{API}/notes/{n['id']}/items/{eggs}", json={"checked": True})).status_code == 200
    async with sessionmaker()() as db:
        nid = (await db.execute(select(Note.id).where(Note.pid == n["id"]))).scalar_one()
        who = {c.user_id for c in (await db.execute(select(CheckitemCheck).where(CheckitemCheck.note_id == nid)))
               .scalars()}
    assert who == {owner.uid, dana.uid}


# ---------------------------------------------------------------- the assistant on real shares


async def _tool(uid: int, name: str, args: dict, **kw):
    from helpers.notes_assistant import _ctx

    from mailr.agent.tools import run_tool

    async with sessionmaker()() as db:
        u = await db.get(User, uid)
        res = await run_tool(_ctx(db, u, **kw), name, args)
        await db.commit()
        return res


async def test_assistant_parity_on_real_shares(people, policy):
    """Assistant parity with real share rows: on a list shared at view the assistant reads and every write is
    refused with the owner's name ("Olive shared this note with view access only"); at edit it
    writes; the viewer's own pin stays theirs. Tables likewise. Reads of shared items are untrusted
    (other people's text), and an errand writing to a shared item always asks, also on the user's
    own list once it is shared."""
    from mailr.agent.tools import UNTRUSTED_TOOLS, ToolError

    await policy("anyone")
    owner = await people("Olive")
    dana = await people("Dana")
    lst = await new_note(owner, title="Olive's list", lines="milk")
    t = await new_table(owner, name="Olive's ledger")
    await share(owner, "note", lst["id"], str(dana.uid), "view")
    await share(owner, "table", t["id"], str(dana.uid), "view")
    assert {"get_note", "list_notes", "search_notes", "get_table", "list_tables"} <= UNTRUSTED_TOOLS
    g = await _tool(dana.uid, "get_note", {"note": lst["id"]})
    assert g.data["items"][0]["text"] == "milk"
    ls = await _tool(dana.uid, "list_notes", {"shared": True})
    assert [x["id"] for x in ls.data["items"]] == [lst["id"]] and ls.data["items"][0]["access"] == "view"
    for name, args in (("checklist_items", {"ops": [{"op": "check", "item": "milk"}]}),
                       ("update_note", {"title": "x"})):
        with pytest.raises(ToolError, match="Olive shared this note with view access only"):
            await _tool(dana.uid, name, {"note": lst["id"], **args})
    assert (await _tool(dana.uid, "update_note", {"note": lst["id"], "pinned": True})).data["results"][0]["ok"]
    with pytest.raises(ToolError, match="Olive shared this table with view access only"):
        await _tool(dana.uid, "table_rows", {"table": t["id"], "action": "add", "rows": [{"values": {"Name": "x"}}]})
    # At edit, the same calls write.
    await share(owner, "note", lst["id"], str(dana.uid), "edit")
    await share(owner, "table", t["id"], str(dana.uid), "edit")
    res = await _tool(dana.uid, "checklist_items", {"note": lst["id"], "ops": [{"op": "check", "item": "milk"}]})
    assert res.pending is None and res.data["results"][0]["ok"] is True
    res = await _tool(dana.uid, "table_rows", {"table": t["id"], "action": "add", "rows": [{"values": {"Name": "x"}}]})
    assert res.data.get("status") != "pending_approval", res.data
    # An errand asks before writing to someone else's shared list and table…
    res = await _tool(dana.uid, "checklist_items", {"note": lst["id"], "ops": [{"op": "add", "lines": "eggs"}]},
                      errand=True)
    assert res.pending is not None
    res = await _tool(dana.uid, "table_rows", {"table": t["id"], "action": "add", "rows": [{"values": {"Name": "y"}}]},
                      errand=True)
    assert res.data["status"] == "pending_approval"
    # …and before writing to the owner's own list once it is shared; not to an unshared one.
    mine = await new_note(owner, title="Just mine", lines="a")
    res = await _tool(owner.uid, "checklist_items", {"note": mine["id"], "ops": [{"op": "check", "item": "a"}]},
                      errand=True)
    assert res.pending is None
    res = await _tool(owner.uid, "checklist_items", {"note": lst["id"], "ops": [{"op": "add", "lines": "jam"}]},
                      errand=True)
    assert res.pending is not None
