"""Connector leases (connectors/runner.py): every running connector's advisory lock sits on one
shared connection per worker process, so mail accounts no longer take a Postgres connection each.
A lost lock connection fails closed: every lease on it is lost, the syncs stop before their next
change, another worker may take the connector, and the first one takes it again only after that.
The jobs that change mail at the provider check their own lock the same way (connectors/tasks.py,
connectors/sentcopy.py)."""

from __future__ import annotations

import asyncpg
import pytest
from sqlalchemy import select, text

from mailr.config import get_settings
from mailr.connectors import runner
from mailr.core.jobs import Retry
from mailr.db import session_scope, sessionmaker
from mailr.models import Connector, Message


def _dsn() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg", "postgresql")


async def _drop(conn, pid: int) -> None:
    """The lock connection dies (a Postgres restart, a network cut): its session ends."""
    assert await conn.fetchval("SELECT pg_terminate_backend($1)", pid)


async def test_leases_share_one_connection_and_still_exclude_other_processes(app):
    locks = runner._Locks()
    other = await asyncpg.connect(_dsn())
    try:
        ids = [990001, 990002, 990003]
        for cid in ids:
            assert await locks.acquire(cid) is not None
        assert locks.held == set(ids)
        pid = await locks.conn.fetchval("SELECT pg_backend_pid()")
        n = await other.fetchval("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND classid = 727100 "
                                 "AND objid = ANY($1::int[]) AND pid = $2", ids, pid)
        assert n == 3  # one connection holds them all
        # The same connector twice in this process: no (a session's advisory locks would nest).
        assert await locks.acquire(990001) is None
        # Another worker process cannot take it while it is held, and can once it is released.
        assert not await other.fetchval("SELECT pg_try_advisory_lock(727100, 990002)")
        await locks.release(990002)
        assert await other.fetchval("SELECT pg_try_advisory_lock(727100, 990002)")
        assert await locks.acquire(990002) is None
        await other.fetchval("SELECT pg_advisory_unlock(727100, 990002)")
        assert await locks.acquire(990002) is not None
    finally:
        await locks._close()
        await other.close()


async def test_a_lost_lock_connection_loses_every_lease_on_it(app):
    locks = runner._Locks()
    other = await asyncpg.connect(_dsn())
    a, b = runner._Lease(990011, locks), runner._Lease(990012, locks)
    try:
        assert await a.acquire() and await b.acquire()
        await a.ensure()
        await _drop(other, await locks.conn.fetchval("SELECT pg_backend_pid()"))
        with pytest.raises(runner.LeaseLost):
            await a.ensure()
        with pytest.raises(runner.LeaseLost):  # the other lease on that connection too
            await b.ensure()
        assert locks.held == set()
        await a.release()  # nothing held: nothing to do, and no error
        assert await a.acquire()  # a fresh connection, a new generation
        await a.ensure()
    finally:
        await locks._close()
        await other.close()


async def test_a_sync_stops_before_its_next_change_when_the_lease_is_lost(user, raw):
    """The lock connection drops between two messages: the second is never stored, the cursor is
    not saved, a second worker takes the connector, and the first waits until it is free."""
    cid = 990021
    first, second = runner._Locks(), runner._Locks()
    killer = await asyncpg.connect(_dsn())
    lease = runner._Lease(cid, first)
    try:
        async with session_scope() as db:
            c = Connector(user_id=user.id, kind="imap", name="Harbour office", mode="store", enabled=True,
                          config={"email": "office@harbour.example"}, state={"cursor": 1})
            db.add(c)
            await db.flush()
            conn_id = c.id
        ctx = runner.RunnerContext(conn_id, user.id, {"cursor": 1})
        assert await lease.acquire()
        ctx.lease = lease
        await ctx.ingest(raw("Mooring 1", msgid="<mooring-1@harbour.example>"), roles=["inbox"])
        await _drop(killer, await first.conn.fetchval("SELECT pg_backend_pid()"))

        with pytest.raises(runner.LeaseLost):
            await ctx.ingest(raw("Mooring 2", msgid="<mooring-2@harbour.example>"), roles=["inbox"])
        with pytest.raises(runner.LeaseLost):
            await ctx.save_state({"cursor": 2})
        with pytest.raises(runner.LeaseLost):
            await ctx.vanished("INBOX", 1, [5], ("role", "inbox"))
        async with sessionmaker()() as db:
            subjects = set((await db.execute(select(Message.subject).where(Message.user_id == user.id))).scalars())
            assert (await db.get(Connector, conn_id)).state == {"cursor": 1}
        assert "Mooring 1" in subjects and "Mooring 2" not in subjects

        # Another worker takes the connector now; the first one does not run beside it.
        other = runner._Lease(cid, second)
        assert await other.acquire()
        assert not await lease.acquire()
        await other.release()
        assert await lease.acquire()
        await lease.ensure()
    finally:
        await first._close()
        await second._close()
        await killer.close()


async def test_the_runner_takes_the_lease_again_after_losing_it(app, monkeypatch):
    """`_run_connector`: a sync that stopped on LeaseLost runs again only with the lease taken anew."""
    locks = runner._Locks()
    monkeypatch.setattr(runner, "_locks", locks)
    killer = await asyncpg.connect(_dsn())
    runs: list[int | None] = []

    async def leased(cid, lease):
        runs.append(lease.generation)
        await lease.ensure()
        if len(runs) == 1:
            await _drop(killer, await locks.conn.fetchval("SELECT pg_backend_pid()"))
            await lease.ensure()  # the next change: the sync stops here

    monkeypatch.setattr(runner, "_run_connector_leased", leased)
    try:
        await runner._run_connector(990031)
        assert len(runs) == 2 and runs[1] is not None and runs[0] is not None and runs[1] > runs[0]
        assert 990031 not in locks.held  # released at the end
    finally:
        await locks._close()
        await killer.close()


def test_lease_lost_is_not_a_bad_message():
    """The connectors' per-message handling (`except Exception`) never takes it for one, and a
    context without a lease (tests, one-off runs) checks nothing."""
    assert not issubclass(runner.LeaseLost, Exception)
    assert runner.RunnerContext(0, 0, {}).lease is None


async def _object(*_a):
    return object()


async def test_a_provider_deletion_stops_when_its_lock_connection_drops(user, monkeypatch):
    """Delete forever at the provider: the job's lock connection drops before the deletion; the
    deletion never starts, and the job runs again later (Retry)."""
    from mailr.connectors import tasks

    async with session_scope() as db:
        c = Connector(user_id=user.id, kind="imap", name="Old provider", mode="store", enabled=True,
                      config={"email": "old@provider.example"}, state={})
        db.add(c)
        await db.flush()
        m = Message(user_id=user.id, connector_id=c.id, deleted=True, subject="", message_id_hdr="<gone@x.example>",
                    upstream={"connector": c.id, "folder": "INBOX", "uidvalidity": 1, "uid": 7})
        db.add(m)
        await db.flush()
        cid, mid = c.id, m.id
    deleted: list = []

    async def fake_delete(conn, connector_id, msgs):
        deleted.append([x.id for x in msgs])
        return [], []

    killer = await asyncpg.connect(_dsn())

    async def connector_then_drop(db, row):
        pid = await killer.fetchval(
            "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND classid = 727200 AND objid = $1", cid)
        await _drop(killer, pid)
        return object()

    monkeypatch.setattr(tasks, "_delete_message_refs", fake_delete)
    monkeypatch.setattr(tasks, "connector_for", connector_then_drop)
    try:
        with pytest.raises(Retry):
            await tasks.delete_forever_upstream({"connector_id": cid, "message_ids": [mid]}, None)
        assert deleted == []
        # With its lock held throughout, the same job deletes.
        monkeypatch.setattr(tasks, "connector_for", _object)
        await tasks.delete_forever_upstream({"connector_id": cid, "message_ids": [mid]}, None)
        assert deleted == [[mid]]
    finally:
        await killer.close()
        async with session_scope() as db:
            await db.execute(text("DELETE FROM messages WHERE id = :i"), {"i": mid})


async def test_a_lost_message_lock_stops_the_sent_copy_before_the_append(app):
    from mailr.connectors import sentcopy

    lock = sentcopy._MessageLock(1, "<sent-1@harbour.example>")
    killer = await asyncpg.connect(_dsn())
    try:
        async with lock:
            await lock.ensure()
            await _drop(killer, await lock.conn.fetchval("SELECT pg_backend_pid()"))
            with pytest.raises(Retry):
                await lock.ensure()
    finally:
        await killer.close()
