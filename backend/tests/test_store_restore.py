"""The index after the mail store was restored from an older snapshot (core/storesync.py): a row whose
store id now names other mail is marked deleted and the store's message is indexed, a message that
fails to index is retried and then counted, and `mailr store resync` makes the worker compare the
whole mailbox again."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from helpers.core_flow import _ingest
from sqlalchemy import select, text

from mailr.core import storesync
from mailr.core.storesync import _UserSync
from mailr.db import session_scope, sessionmaker
from mailr.models import Connector, Message, StoreSyncState, User


async def _store(db, uid: int):
    from mailr.core.store import user_store

    return await user_store(db, await db.get(User, uid))


async def _rows(uid: int, jmap_id: str) -> list[Message]:
    async with sessionmaker()() as db:
        return list((await db.execute(select(Message).where(Message.user_id == uid, Message.jmap_id == jmap_id)
                                      )).scalars())


async def _as_lost_mail(mid: int) -> str:
    """Make the row look like the mail the restore lost, whose store id was handed out again to
    this message: another blob, another arrival time."""
    async with session_scope() as db:
        m = await db.get(Message, mid)
        m.blob_id = "blob-of-lost-mail"
        m.received_at = m.received_at - timedelta(hours=3)
        return m.jmap_id


def test_a_row_is_other_mail_only_when_blob_and_arrival_both_differ():
    from datetime import UTC, datetime

    at = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    e = {"id": "J1", "blobId": "B2", "receivedAt": "2026-10-01T08:00:00Z"}
    assert storesync._mismatched({"J1": ("B1", at)}, {"J1": e}) == []  # renamed blob, same message
    assert storesync._mismatched({"J1": ("B2", at - timedelta(hours=1))}, {"J1": e}) == []  # same blob
    assert storesync._mismatched({"J1": ("B1", at - timedelta(hours=1))}, {"J1": e}) == ["J1"]
    assert storesync._mismatched({"J1": (None, at)}, {"J1": e}) == []
    assert storesync._mismatched({"J1": ("B1", at)}, {}) == []


async def test_reconcile_marks_a_row_whose_id_names_other_mail_and_indexes_that_mail(user, raw):
    mid, _c = await _ingest(user, raw("Pier inspection", body="Tuesday at nine"))
    jid = await _as_lost_mail(mid)
    pending: dict = {}
    now = time.time()
    async with session_scope() as db:
        await _UserSync(user.id)._reconcile(db, await _store(db, user.id), pending, now)
    old = await _rows(user.id, jid)
    assert old == []  # the stale row let go of the id
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        assert m.deleted and m.jmap_id is None
    assert jid in pending
    async with session_scope() as db:
        await _UserSync(user.id)._index(db, await _store(db, user.id), [jid], pending, now)
    new = await _rows(user.id, jid)
    assert len(new) == 1 and new[0].id != mid and new[0].subject == "Pier inspection" and not new[0].deleted
    assert jid not in pending


async def test_reconcile_treats_no_row_as_gone_when_too_many_differ(user, raw, monkeypatch):
    """Most of the mailbox differing is no restore (a store that renamed its blobs): nothing is
    marked deleted."""
    mid, _c = await _ingest(user, raw("Quarterly figures"))
    await _as_lost_mail(mid)
    monkeypatch.setattr(storesync, "MISMATCH_MIN", 0)
    monkeypatch.setattr(storesync, "MISMATCH_SHARE", 0.0)
    async with session_scope() as db:
        await _UserSync(user.id)._reconcile(db, await _store(db, user.id), {}, time.time())
    async with sessionmaker()() as db:
        assert not (await db.get(Message, mid)).deleted


async def test_a_created_id_held_by_a_stale_row_is_indexed_as_new_mail(user, raw):
    """Without a resync: the store reports a new message under an id an old row still holds."""
    mid, _c = await _ingest(user, raw("Boat permit", body="Renewal due"))
    jid = await _as_lost_mail(mid)
    pending = {jid: time.time() - storesync.GRACE_SECONDS - 1}
    async with session_scope() as db:
        await _UserSync(user.id)._index(db, await _store(db, user.id), [jid], pending, time.time())
    new = await _rows(user.id, jid)
    assert len(new) == 1 and new[0].id != mid
    async with sessionmaker()() as db:
        assert (await db.get(Message, mid)).deleted


async def test_a_message_that_fails_to_index_is_retried_then_counted(user, raw, monkeypatch):
    mid, _c = await _ingest(user, raw("Flaky download"))
    async with session_scope() as db:  # a message only the store has (added by another mail app)
        m = await db.get(Message, mid)
        jid = m.jmap_id
        await db.execute(text("UPDATE messages SET jmap_id = NULL, deleted = true WHERE id = :i"), {"i": mid})

    async def broken(*a, **k):
        raise TimeoutError("download timed out")

    monkeypatch.setattr(storesync, "index_stored", broken)
    sync = _UserSync(user.id)
    pending: dict = {jid: 0.0}
    now = time.time()
    for n in range(1, storesync.INDEX_ATTEMPTS + 1):
        async with session_scope() as db:
            await sync._index(db, await _store(db, user.id), [jid], pending, now)
        entry = pending[jid]
        assert entry["n"] == n and "download timed out" in entry["error"]
        assert not storesync._ripe(entry, now)
        wait = entry["next"] - now
        assert wait == (storesync.RETRY_GAVE_UP if n == storesync.INDEX_ATTEMPTS
                        else min(storesync.RETRY_MAX, storesync.RETRY_FIRST * 2 ** (n - 1)))
        now = entry["next"]
    await _save_pending(user.id, pending)
    assert (await storesync.failed_counts()).get(user.id) == 1

    monkeypatch.undo()
    async with session_scope() as db:
        await sync._index(db, await _store(db, user.id), [jid], pending, now)
    assert jid not in pending and len(await _rows(user.id, jid)) == 1
    await _save_pending(user.id, pending)
    assert user.id not in await storesync.failed_counts()


async def _save_pending(uid: int, entries: dict) -> None:
    """The user's store_sync_pending rows become `entries` ({jmap id: {n, next, error}})."""
    async with session_scope() as db:
        if await db.get(StoreSyncState, uid) is None:
            db.add(StoreSyncState(user_id=uid))
            await db.flush()
        await db.execute(text("DELETE FROM store_sync_pending WHERE user_id = :u"), {"u": uid})
        p = storesync.Pending()
        for j, e in entries.items():
            p[j] = e
        await p.save(db, uid)


async def test_pending_rows_are_due_after_the_grace_period_or_their_retry(user):
    """A new id is due GRACE_SECONDS after it was first seen; a failed one at its retry time; an
    id already pending keeps its entry; a removed one goes."""
    t = time.time()
    async with session_scope() as db:
        if await db.get(StoreSyncState, user.id) is None:
            db.add(StoreSyncState(user_id=user.id))
            await db.flush()
        await db.execute(text("DELETE FROM store_sync_pending WHERE user_id = :u"), {"u": user.id})
        p = storesync.Pending()
        p.setdefault("J-new", t)
        p.setdefault("J-old", t - storesync.GRACE_SECONDS - 1)
        p.setdefault("J-gone", t - storesync.GRACE_SECONDS - 1)
        p["J-failed"] = {"n": 2, "next": t - 1, "error": "TimeoutError"}
        p["J-later"] = {"n": 1, "next": t + 60, "error": ""}
        await p.save(db, user.id)
    async with session_scope() as db:
        p = storesync.Pending()
        p.setdefault("J-old", t)  # already pending: unchanged
        p.pop("J-gone")
        await p.save(db, user.id)
    async with session_scope() as db:
        due = await storesync.Pending.due(db, user.id, t, 10)
    assert sorted(due.ids()) == ["J-failed", "J-old"]
    assert due.get("J-failed") == {"n": 2, "next": t - 1, "error": "TimeoutError"}
    assert storesync._ripe(due.get("J-old"), t)


async def test_store_resync_forgets_the_state_and_rescans_the_accounts(user, capsys):
    from mailr import cli

    async with session_scope() as db:
        st = await db.get(StoreSyncState, user.id)
        if st is None:
            db.add(StoreSyncState(user_id=user.id, email_state="s-42", mailbox_state="m-7"))
            await db.flush()
        else:
            st.email_state = "s-42"
        p = storesync.Pending()
        p.setdefault("J9", 1.0)
        await p.save(db, user.id)
        c = Connector(user_id=user.id, kind="imap", name="Harbour office", mode="mirror", enabled=True,
                      config={"email": "office@harbour.example"}, state={"initial_done": True, "folders": {}})
        db.add(c)
        await db.flush()
        cid = c.id
    assert await cli._store_resync(user.email, False) == 0
    out = capsys.readouterr().out
    assert user.email in out and "compared with the mail store" in out
    async with sessionmaker()() as db:
        assert await db.get(StoreSyncState, user.id) is None
        assert not (await db.execute(text("SELECT 1 FROM store_sync_pending WHERE user_id = :u"),
                                     {"u": user.id})).first()  # the pending ids went with it
        assert (await db.get(Connector, cid)).state == {"initial_done": True, "folders": {}}

    assert await cli._store_resync(str(user.id), True) == 0
    assert "1 mail accounts are scanned again from the start" in capsys.readouterr().out
    async with sessionmaker()() as db:
        c = await db.get(Connector, cid)
        assert c.state == {} and c.status == "new"
        n = (await db.execute(text("SELECT count(*) FROM jobs WHERE kind = 'connector.sync_now' "
                                   "AND payload->>'connector_id' = :c"), {"c": str(cid)})).scalar_one()
    assert n == 1


@pytest.mark.parametrize("value", [1.0, {"n": 1, "next": 0}])
def test_pending_entries_of_both_shapes_are_read(value):
    assert storesync._ripe(value, time.time())


async def _connector(uid: int, address: str, data_class: str = "normal") -> int:
    async with session_scope() as db:
        c = Connector(user_id=uid, kind="imap", name=address, mode="store", enabled=True, data_class=data_class,
                      config={"email": address}, state={})
        db.add(c)
        await db.flush()
        return c.id


async def _only_in_store(uid: int, data: bytes) -> dict:
    """A message that is in the mail store but not in the index (after a database restore)."""
    async with sessionmaker()() as db:
        store = await _store(db, uid)
        stored = await store.import_message(db, data, [], {}, None)
        await db.commit()
        return (await store.get_emails([stored["jmap_id"]]))[0]


async def test_mail_indexed_from_the_store_keeps_its_account_and_stays_local_only(user, raw):
    """After a database restore the store sync indexes mail that arrived since the dump: mail of a
    local-only account stays out of hosted models, and keeps its account."""
    from mailr.core.ingest import index_stored

    private = await _connector(user.id, "clinic@private.example", "local_only")
    await _connector(user.id, "hello@shop.example")
    data = raw("Test results", to="clinic@private.example", body="Your results are ready")
    e = await _only_in_store(user.id, data)
    async with session_scope() as db:
        m = await index_stored(db, await _store(db, user.id), e, data)
        assert m.local_only is True and m.connector_id == private

    plain = raw("Order shipped", to="hello@shop.example")
    e = await _only_in_store(user.id, plain)
    async with session_scope() as db:
        m = await index_stored(db, await _store(db, user.id), e, plain)
        assert m.local_only is False and m.connector_id is not None and m.connector_id != private

    # Addressed to both: no single account, but local-only wins.
    both = raw("Two inboxes", to="clinic@private.example, hello@shop.example")
    e = await _only_in_store(user.id, both)
    async with session_scope() as db:
        m = await index_stored(db, await _store(db, user.id), e, both)
        assert m.local_only is True and m.connector_id is None


async def test_the_account_that_fetches_a_store_indexed_message_again_claims_it(user, raw):
    """The store sync indexed the message first (no account); the account's sync then fetches it
    again: the row gets the account, its upstream copy and its data class."""
    from mailr.core.ingest import IngestOptions, index_stored, ingest_raw

    private = await _connector(user.id, "desk@private.example", "local_only")
    data = raw("Lease draft", frm="Agent <agent@lettings.example>", to="someone@else.example")
    e = await _only_in_store(user.id, data)
    async with session_scope() as db:
        m = await index_stored(db, await _store(db, user.id), e, data)
        mid = m.id
        assert m.connector_id is None and m.local_only is False
    async with session_scope() as db:
        row = await db.get(Connector, private)
        res = await ingest_raw(db, await _store(db, user.id), data, IngestOptions(
            connector=row, roles=["inbox"], upstream={"connector": private, "folder": "INBOX",
                                                      "uidvalidity": 7, "uid": 31}))
        assert res.duplicate and res.message.id == mid
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
    assert m.connector_id == private and m.local_only is True
    assert m.upstream["uid"] == 31 and m.upstream["connector"] == private


async def test_an_import_whose_transaction_failed_is_not_indexed_twice(user, raw):
    """The store keeps the copy of an ingest whose transaction rolled back (a database blip, a worker
    stopped mid-ingest), and the retried ingest stores a second copy. The store sync finds the first
    copy duplicating the indexed message: its folders join that message and the copy goes."""
    from mailr.core.ingest import IngestOptions, ingest_raw

    data = raw("Invoice 7781", frm="Billing <billing@supplier.example>")
    # A connector passes the provider's arrival time (INTERNALDATE), so the retry carries the same one.
    # Without it each try took "now" (the store keeps whole seconds), and on a slow runner the copies
    # differed by more than the second the store sync allows: the orphan was indexed as its own message.
    opts = IngestOptions(received_at=datetime(2026, 10, 1, 8, 0, tzinfo=UTC))
    async with sessionmaker()() as db:
        orphan = (await ingest_raw(db, await _store(db, user.id), data, opts)).message.jmap_id
        await db.rollback()
    async with session_scope() as db:
        kept = (await ingest_raw(db, await _store(db, user.id), data, opts)).message.jmap_id
    assert kept != orphan
    pending = {orphan: 0.0}
    async with session_scope() as db:
        await _UserSync(user.id)._index(db, await _store(db, user.id), [orphan], pending, time.time())
    assert pending == {} and await _rows(user.id, orphan) == [] and len(await _rows(user.id, kept)) == 1
    async with sessionmaker()() as db:
        store = await _store(db, user.id)
        assert await store.get_emails([orphan]) == [] and len(await store.get_emails([kept])) == 1
        n = (await db.execute(text("SELECT count(*) FROM messages WHERE user_id = :u AND subject = 'Invoice 7781'"),
                              {"u": user.id})).scalar_one()
    assert n == 1


async def test_a_sent_copy_stored_again_after_a_rollback_is_not_indexed_twice(client, user, monkeypatch):
    """The sent copy's store job rolled back after the store took the copy, and its retry ran later.
    Both copies arrive at the time the message went out, so the store sync merges the first one into
    the indexed copy. Before, each try took "now": a retry a second or more later left a copy that
    the store sync indexed as a second sent message."""
    from helpers.agent import _setup_identity

    from mailr.core import ingest, sendgate
    from mailr.models import Identity, Outbox

    await _setup_identity(user)

    class Smtp:
        async def send(self, raw, mail_from, rcpts):
            return "250 queued"

    async def fake_connector_for(db, row):
        return Smtp()

    monkeypatch.setattr("mailr.connectors.loader.connector_for", fake_connector_for)
    async with sessionmaker()() as db:
        ident = (await db.execute(select(Identity).where(Identity.user_id == user.id))).scalars().first()
    r = await client.post("/api/v1/send", json={"payload": {
        "identity_id": ident.id, "to": [{"email": "marco@harbour.example"}], "subject": "Mooring fees",
        "text": "The fees are paid."}})
    assert r.status_code in (200, 202), r.text
    oid = r.json()["outbox"]["id"]
    async with session_scope() as db:
        (await db.get(Outbox, oid)).send_at = datetime.now(UTC) - timedelta(seconds=1)
    await sendgate.send_job({"outbox_id": oid}, None)

    stored: list[str] = []
    real_ingest = ingest.ingest_raw

    async def recording_ingest(*a, **kw):
        res = await real_ingest(*a, **kw)
        stored.append(res.message.jmap_id)
        if len(stored) == 1:
            raise RuntimeError("database blip")  # after the store import: the transaction rolls back
        return res

    monkeypatch.setattr(ingest, "ingest_raw", recording_ingest)
    with pytest.raises(RuntimeError):
        await sendgate.store_sent_job({"outbox_id": oid}, None)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(minutes=5)  # the job's retry, minutes later

    monkeypatch.setattr(ingest, "datetime", Later)
    await sendgate.store_sent_job({"outbox_id": oid}, None)
    orphan, kept = stored
    assert orphan != kept and await _rows(user.id, orphan) == []
    pending = {orphan: 0.0}
    async with session_scope() as db:
        await _UserSync(user.id)._index(db, await _store(db, user.id), [orphan], pending, time.time())
    assert pending == {} and await _rows(user.id, orphan) == [] and len(await _rows(user.id, kept)) == 1
    async with sessionmaker()() as db:
        assert await (await _store(db, user.id)).get_emails([orphan]) == []


async def test_a_copy_with_another_arrival_time_is_its_own_message(user, raw):
    """A mail app that saved the same message again later (another arrival time) keeps its copy."""
    from mailr.core.ingest import IngestOptions, index_stored, ingest_raw

    data = raw("Minutes", frm="Clerk <clerk@council.example>")
    async with session_scope() as db:
        await ingest_raw(db, await _store(db, user.id), data, IngestOptions())
    async with sessionmaker()() as db:
        store = await _store(db, user.id)
        from datetime import UTC, datetime

        stored = await store.import_message(db, data, [], {}, datetime(2026, 1, 5, 9, 0, tzinfo=UTC))
        await db.commit()
        e = (await store.get_emails([stored["jmap_id"]]))[0]
    async with session_scope() as db:
        assert await index_stored(db, await _store(db, user.id), e, data) is not None


async def test_import_export_brings_back_mail_the_store_restore_lost(user, raw, tmp_path, monkeypatch, capsys):
    """The snapshot lacks a message (the reconcile marked its row deleted); its export file stays,
    and `mailr store import-export` imports it back. What is still there is skipped."""
    from helpers.connectors import run_sync

    from mailr import cli
    from mailr.config import get_settings
    from mailr.core import backup

    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(get_settings(), "backup_target", "")
    monkeypatch.setattr(get_settings(), "mail_export", True)
    lost, _c = await _ingest(user, raw("Survey booking", body="Thursday 10:00"))
    kept, _c = await _ingest(user, raw("Survey invoice"))
    assert await backup.export_user(user.id) == 2
    async with session_scope() as db:
        jid = (await db.get(Message, lost)).jmap_id
        await _UserSync(user.id)._apply_destroyed(db, [jid])

    # Right after `mailr store resync` the comparison has not run: rows of lost mail still look
    # present, so the import would skip them. It waits.
    async with session_scope() as db:
        st = await db.get(StoreSyncState, user.id)
        if st is not None:
            await db.delete(st)
    assert await cli._store_import_export(user.email) == 1
    assert "has not compared the mailbox" in capsys.readouterr().out
    async with session_scope() as db:
        db.add(StoreSyncState(user_id=user.id, email_state="s-1"))
    assert await cli._store_import_export(user.email) == 0
    assert "2 exported messages queued for import" in capsys.readouterr().out
    async with sessionmaker()() as db:
        c = (await db.execute(select(Connector).where(Connector.user_id == user.id, Connector.kind == "file"))
             ).scalar_one()
    totals = await run_sync(c.id)
    assert totals["new"] == 1 and totals["duplicates"] == 1
    async with sessionmaker()() as db:
        back = (await db.execute(select(Message).where(Message.user_id == user.id, Message.deleted.is_(False),
                                                       Message.subject == "Survey booking"))).scalars().all()
    assert len(back) == 1 and back[0].id != lost
