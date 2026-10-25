"""Live editing under concurrency (core/live.py): a read or a write that loaded an item before another write committed must not record
its old copy as a new "sync" version, and must not merge against it.

Each test interleaves two requests at a fixed point, without sleeps: a hook on the function that
loads the item lets the first request load it, then runs the second request to the end (it
commits), then lets the first go on with the copy it loaded before.

# ACT-SURFACES: api
"""

from __future__ import annotations

import pytest
from helpers.agent import _setup_identity
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import ItemVersion

API = "/api/v1"


def P(*xs):
    return "".join(f"<p>{x}</p>" for x in xs)


async def _versions(user_id: int, kind: str, item: str) -> list[tuple[int, str, dict]]:
    async with sessionmaker()() as db:
        rows = (await db.execute(select(ItemVersion.version, ItemVersion.by, ItemVersion.content).where(
            ItemVersion.user_id == user_id, ItemVersion.kind == kind, ItemVersion.item_id == str(item))
            .order_by(ItemVersion.version))).all()
    return [(v, by, c) for v, by, c in rows]


def _interleave(monkeypatch, module, name, other):
    """Make the next call of module.name (the loader of the item) run `other()` to the end after
    it loaded the item and before it returns. Fires once."""
    real = getattr(module, name)
    state = {"armed": True}

    async def hooked(*a, **kw):
        got = await real(*a, **kw)
        if state["armed"]:
            state["armed"] = False
            await other()
        return got

    monkeypatch.setattr(module, name, hooked)
    return state


# ---------------------------------------------------------------- drafts


async def _draft(client, html):
    payload = {"identity_id": None, "to": [{"email": "alice@acme.example", "name": "Alice"}], "cc": [], "bcc": [],
               "subject": "Plan", "html": html, "attachments": []}
    r = await client.post(f"{API}/drafts", json={"payload": payload})
    assert r.status_code == 200, r.text
    return r.json()["draft"]["id"], payload


async def _draft_db_id(did) -> int:
    from mailr.core import publicid

    return publicid.decode("message", did) if isinstance(did, str) else int(did)


@pytest.mark.parametrize("path", ["drafts", "live"])
async def test_draft_read_during_a_save_records_no_stale_version(client, user, monkeypatch, path):
    """A GET that loaded the draft before a PUT committed answers a consistent (version, content)
    pair and records no "sync" version holding the old text (the trace of 13 Oct 2026)."""
    from mailr.core import drafts

    await _setup_identity(user)
    old, new = P("Hi Alice,", "Old text.", "Best,"), P("Hi Alice,", "New text.", "Best,")
    did, payload = await _draft(client, old)

    async def save():
        r = await client.put(f"{API}/drafts/{did}", json={"payload": {**payload, "html": new}, "base_version": 1})
        assert r.status_code == 200 and r.json()["version"] == 2, r.text

    hook = _interleave(monkeypatch, drafts, "load_draft", save)
    r = await client.get(f"{API}/drafts/{did}" if path == "drafts" else f"{API}/live/draft/{did}")
    assert r.status_code == 200, r.text
    assert hook["armed"] is False
    got = r.json()
    version, html = got["version"], (got["payload"] if path == "drafts" else got["content"])["html"]
    vers = await _versions(user.id, "draft", str(await _draft_db_id(did)))
    assert [(v, by) for v, by, _ in vers] == [(1, "user"), (2, "user")], vers
    # The answer is a pair that exists: the version's stored copy is the content it came with.
    assert version == 2 and html == new

    # A save made from the version that read answered keeps the other writer's text.
    r = await client.put(f"{API}/drafts/{did}", json={
        "payload": {**payload, "html": P("Hi Alice,", "New text.", "Best regards,")}, "base_version": version})
    assert r.status_code == 200 and r.json()["merged"] is False and r.json()["version"] == 3
    got = (await client.get(f"{API}/drafts/{did}")).json()
    assert got["payload"]["html"] == P("Hi Alice,", "New text.", "Best regards,")


async def test_draft_save_that_loaded_before_another_save_merges_with_it(client, user, monkeypatch):
    """Two saves from version 1: the one that loaded the draft first and locked it second merges
    with the other's change instead of overwriting it with the copy it loaded."""
    from mailr.core import drafts

    await _setup_identity(user)
    did, payload = await _draft(client, P("Hi Alice,", "The numbers are ready.", "Best,"))

    async def other():
        r = await client.put(f"{API}/drafts/{did}", json={
            "payload": {**payload, "subject": "Q4 plan"}, "base_version": 1})
        assert r.status_code == 200 and r.json()["version"] == 2, r.text

    _interleave(monkeypatch, drafts, "load_draft", other)
    r = await client.put(f"{API}/drafts/{did}", json={
        "payload": {**payload, "html": P("Hi Alice,", "The revised numbers are ready.", "Best,")}, "base_version": 1})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["merged"] is True and out["version"] == 3
    assert out["payload"]["subject"] == "Q4 plan"
    assert out["payload"]["html"] == P("Hi Alice,", "The revised numbers are ready.", "Best,")
    vers = await _versions(user.id, "draft", str(await _draft_db_id(did)))
    assert [by for _, by, _ in vers] == ["user", "user", "user"], vers


async def test_draft_changed_outside_mailr_still_gets_a_sync_version(client, user):
    """The reason "sync" versions exist: a stored copy that no recorded write made (another
    client, a restore) gets the next version when it is read."""
    from sqlalchemy import update

    from mailr.models import Message

    await _setup_identity(user)
    did, payload = await _draft(client, P("Hi Alice,", "Old text.", "Best,"))
    mid = await _draft_db_id(did)
    changed = {**payload, "subject": "Changed elsewhere"}
    async with sessionmaker()() as db:
        await db.execute(update(Message).where(Message.id == mid).values(upstream={"draft_payload": changed}))
        await db.commit()
    got = (await client.get(f"{API}/live/draft/{did}")).json()
    assert got["version"] == 2 and got["content"]["subject"] == "Changed elsewhere"
    vers = await _versions(user.id, "draft", str(mid))
    assert [(v, by) for v, by, _ in vers] == [(1, "user"), (2, "sync")]
    assert vers[1][2]["subject"] == "Changed elsewhere"


# ---------------------------------------------------------------- notes


async def _note(client, **kw):
    r = await client.post(f"{API}/notes", json=kw)
    assert r.status_code == 200, r.text
    return r.json()["note"]


async def test_note_read_during_a_save_records_no_stale_version(client, user, monkeypatch):
    from mailr.notes import access

    n = await _note(client, kind="text", title="Plan", body="one\ntwo\n")
    pid, v1 = n["id"], n["version"]

    async def save():
        r = await client.patch(f"{API}/notes/{pid}", json={"body": "one\ntwo\nthree\n", "base_version": v1})
        assert r.status_code == 200, r.text

    hook = _interleave(monkeypatch, access, "get", save)
    r = await client.get(f"{API}/notes/{pid}")
    assert r.status_code == 200, r.text
    assert hook["armed"] is False
    got = r.json()
    vers = await _versions(user.id, "note", pid)
    assert "sync" not in [by for _, by, _ in vers], vers
    assert got["version"] == vers[-1][0] and got["body"] == "one\ntwo\nthree\n"


async def test_note_save_that_loaded_before_another_save_merges_with_it(client, user, monkeypatch):
    from mailr.notes import access

    n = await _note(client, kind="text", title="Plan", body="one\ntwo\nthree\n")
    pid, v1 = n["id"], n["version"]

    async def other():
        r = await client.patch(f"{API}/notes/{pid}", json={"body": "one\ntwo\nTHREE\n", "base_version": v1})
        assert r.status_code == 200, r.text

    _interleave(monkeypatch, access, "get", other)
    r = await client.patch(f"{API}/notes/{pid}", json={"body": "ONE\ntwo\nthree\n", "base_version": v1})
    assert r.status_code == 200, r.text
    got = (await client.get(f"{API}/notes/{pid}")).json()
    assert got["body"] == "ONE\ntwo\nTHREE\n"
    vers = await _versions(user.id, "note", pid)
    assert "sync" not in [by for _, by, _ in vers], vers


async def test_checkitem_read_during_a_check_records_no_stale_version(client, user, monkeypatch):
    from mailr.notes import core

    n = await _note(client, kind="checklist", title="Shop", lines="bread")
    pid = n["id"]
    full = (await client.get(f"{API}/notes/{pid}")).json()
    item = full["items"][0]
    iid = item["id"]

    async def edit():
        r = await client.patch(f"{API}/notes/{pid}/items/{iid}", json={"text": "sourdough",
                                                                       "base_version": item["version"]})
        assert r.status_code == 200, r.text

    hook = _interleave(monkeypatch, core, "items_of", edit)
    r = await client.get(f"{API}/notes/{pid}")
    assert r.status_code == 200, r.text
    assert hook["armed"] is False
    got = next(i for i in r.json()["items"] if i["id"] == iid)
    vers = await _versions(user.id, "checkitem", iid)
    assert "sync" not in [by for _, by, _ in vers], vers
    assert got["version"] == vers[-1][0] and vers[-1][2]["text"] == got["text"] == "sourdough"
