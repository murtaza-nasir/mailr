"""Drafts and messages must not drop out of the index: a draft save keeps the copy it replaces until it commits, storesync applies a destroy only to the
row that still points at the destroyed copy, reconcile pages the store completely, and a message
mailr is still importing is not indexed a second time under the same store id."""

from __future__ import annotations

import asyncio
import uuid

from helpers.agent import _setup_identity
from sqlalchemy import select, text

from mailr.core.storesync import _UserSync
from mailr.db import session_scope, sessionmaker
from mailr.models import Job, Message, User

API = "/api/v1"


async def _store_has(user_id: int, jmap_id: str) -> bool:
    from mailr.core.store import user_store

    async with sessionmaker()() as db:
        store = await user_store(db, await db.get(User, user_id))
        r = await store.jmap.one("Email/get", {"ids": [jmap_id], "properties": ["id"]})
    return jmap_id in {e["id"] for e in r.get("list") or []}


async def _row(mid: int) -> Message:
    async with sessionmaker()() as db:
        return await db.get(Message, mid)


async def test_a_draft_save_that_fails_to_commit_keeps_the_stored_copy(client, user, monkeypatch):
    from mailr.api import mail_write
    from mailr.core import publicid

    await _setup_identity(user)
    payload = {"to": [{"email": "alice@acme.example", "name": "Alice"}], "subject": "Plan", "html": "<p>one</p>"}
    r = await client.post(f"{API}/drafts", json={"payload": payload})
    assert r.status_code == 200, r.text
    mid = publicid.decode("message", r.json()["draft"]["id"])
    first = (await _row(mid)).jmap_id
    assert await _store_has(user.id, first)

    real = mail_write.audit

    async def failing(db, p, action, *a, **kw):
        if action == "drafts.update":
            raise RuntimeError("the database went away before the commit")
        return await real(db, p, action, *a, **kw)

    monkeypatch.setattr(mail_write, "audit", failing)
    try:
        r = await client.put(f"{API}/drafts/{mid}", json={"payload": {**payload, "html": "<p>two</p>"}})
        assert r.status_code >= 500
    except RuntimeError:
        pass
    row = await _row(mid)
    assert row.jmap_id == first and not row.deleted
    assert await _store_has(user.id, first)  # the row's copy is still there: nothing lost

    # A save that commits replaces the copy; the old one goes once the job runs.
    monkeypatch.setattr(mail_write, "audit", real)
    r = await client.put(f"{API}/drafts/{mid}", json={"payload": {**payload, "html": "<p>three</p>"}})
    assert r.status_code == 200, r.text
    second = (await _row(mid)).jmap_id
    assert second != first and await _store_has(user.id, second)
    from mailr.core.jobs import _HANDLERS

    async with session_scope() as db:
        jobs = (await db.execute(select(Job).where(Job.kind == "drafts.destroy_replaced",
                                                   Job.user_id == user.id))).scalars().all()
        payloads = [j.payload for j in jobs]
        for j in jobs:
            j.status = "done"
    assert {p_["jmap_id"] for p_ in payloads} >= {first}
    for pl in payloads:
        await _HANDLERS["drafts.destroy_replaced"](pl, None)
    assert not await _store_has(user.id, first) and await _store_has(user.id, second)
    assert not (await _row(mid)).deleted


async def _plain_row(user_id: int, jmap_id: str, **kw) -> int:
    async with session_scope() as db:
        m = Message(user_id=user_id, jmap_id=jmap_id, message_id_hdr=f"<{uuid.uuid4().hex}@x.example>",
                    subject="x", label_ids=[], **kw)
        db.add(m)
        await db.flush()
        return m.id


async def test_a_destroy_seen_by_storesync_spares_a_row_that_moved_to_a_new_copy(app, user):
    """Storesync sees the old copy destroyed while a draft save (not yet committed) points the row
    at its new copy: once the save commits, the row keeps its new copy."""
    mid = await _plain_row(user.id, "J-old", draft=True)
    saver = sessionmaker()()
    await saver.execute(text("UPDATE messages SET jmap_id = 'J-new' WHERE id = :i"), {"i": mid})

    async def tick():
        async with session_scope() as db:
            await _UserSync(user.id)._apply_destroyed(db, ["J-old"])

    t = asyncio.create_task(tick())
    await asyncio.sleep(0.5)  # the tick waits for the save's row lock
    await saver.commit()
    await saver.close()
    await asyncio.wait_for(t, 10)
    row = await _row(mid)
    assert row.jmap_id == "J-new" and not row.deleted


class _PagedStore:
    """A store that answers Email/query in pages of at most 2 ids (a low maxObjectsInGet)."""

    def __init__(self, ids: list[str]):
        self.ids = ids
        self.jmap = self

    async def one(self, method, args, using=None):
        if method == "Email/query":
            pos = int(args.get("position") or 0)
            return {"ids": self.ids[pos:pos + 2], "total": len(self.ids), "position": pos}
        if method == "Email/get":
            return {"list": [{"id": i} for i in args["ids"] if i in self.ids],
                    "notFound": [i for i in args["ids"] if i not in self.ids]}
        raise AssertionError(method)


async def test_reconcile_reads_every_page_and_marks_only_missing_mail(app, user):
    tag = uuid.uuid4().hex[:6]
    live = [f"L{tag}{i}" for i in range(5)]
    for j in live:
        await _plain_row(user.id, j)
    gone = await _plain_row(user.id, f"G{tag}")
    store = _PagedStore(live)
    async with session_scope() as db:
        await _UserSync(user.id)._reconcile(db, store, {}, 0.0)
    async with sessionmaker()() as db:
        rows = dict((await db.execute(select(Message.id, Message.deleted).where(Message.user_id == user.id))).all())
        kept = (await db.execute(select(Message.jmap_id).where(Message.user_id == user.id,
                                                               Message.deleted.is_(False)))).scalars().all()
    assert set(live) <= set(kept)
    assert rows[gone] is True


async def test_storesync_waits_for_an_import_in_flight_instead_of_indexing_it_twice(app, user, raw):
    from mailr.core.ingest import IngestOptions, index_stored, ingest_raw
    from mailr.core.store import user_store

    data = raw("Slow import", frm="Dana <dana@vendor.example>")
    importer = sessionmaker()()
    u = await importer.get(User, user.id)
    store = await user_store(importer, u)
    res = await ingest_raw(importer, store, data, IngestOptions())
    jid = res.message.jmap_id
    email = (await store.get_emails([jid]))[0]

    async def tick():
        async with session_scope() as db:
            s2 = await user_store(db, await db.get(User, user.id))
            return await index_stored(db, s2, email, data)

    t = asyncio.create_task(tick())
    await asyncio.sleep(0.5)  # the import is still open (longer than storesync's grace period)
    await importer.commit()
    await importer.close()
    got = await asyncio.wait_for(t, 10)
    assert got is None
    async with sessionmaker()() as db:
        n = (await db.execute(text("SELECT count(*) FROM messages WHERE user_id = :u AND jmap_id = :j"),
                              {"u": user.id, "j": jid})).scalar_one()
    assert n == 1
