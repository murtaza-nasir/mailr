"""Live events (core/events.py, 14 Oct 2026): a conversation change names its conversations, and a
stream that missed events is told to catch up (`resync`) instead of silently going stale."""

from __future__ import annotations

import asyncio
import json

import pytest

from mailr.core import events


class _Db:
    def __init__(self):
        self.sql = []

    async def execute(self, stmt, params=None):
        self.sql.append((str(stmt), params))


async def _payloads(entity, ids):
    db = _Db()
    await events.record_change(db, 5, entity, ids)
    return [json.loads(p["p"]) for s, p in db.sql if "pg_notify" in s]


async def test_conversation_changes_name_their_conversations():
    assert await _payloads("conversation", [3, 4, 3]) == [
        {"u": 5, "type": "changes", "entity": "conversation", "conversation_ids": [3, 4]}]
    # A big batch names none (the client then refreshes any open conversation).
    big = await _payloads("conversation", list(range(events.EVENT_IDS + 1)))
    assert "conversation_ids" not in big[0]
    # Other entities as before.
    assert await _payloads("task", [1]) == [{"u": 5, "type": "changes", "entity": "task"}]


def test_a_full_stream_gets_one_resync_instead_of_losing_the_newest_events():
    hub = events.Hub()
    q = hub.subscribe(9)
    for i in range(q.maxsize):
        hub._put(9, {"type": "changes", "entity": "conversation", "n": i})
    assert q.full()
    hub._put(9, {"type": "changes", "entity": "label"})
    assert q.qsize() == 1
    assert q.get_nowait() == {"type": "resync"}
    # Later events queue normally again.
    hub._put(9, {"type": "changes", "entity": "label"})
    assert q.get_nowait()["entity"] == "label"


def test_a_stream_being_closed_still_closes_on_overflow():
    hub = events.Hub()
    q = hub.subscribe(9)
    close = {"type": "__close__"}
    for i in range(q.maxsize - 1):
        q.put_nowait({"type": "changes", "n": i})
    q.put_nowait(close)
    hub._put(9, {"type": "changes"})
    assert q.get_nowait() is close


async def test_listener_reconnect_tells_every_stream_to_resync(monkeypatch):
    hub = events.Hub()
    a, b = hub.subscribe(1), hub.subscribe(2)
    calls = {"n": 0}

    class Conn:
        async def add_listener(self, *_a):
            pass

        async def execute(self, *_a):
            raise ConnectionError("gone")

    async def connect(_dsn):
        calls["n"] += 1
        if calls["n"] > 2:
            raise asyncio.CancelledError
        return Conn()

    import asyncpg

    monkeypatch.setattr(asyncpg, "connect", connect)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(events.asyncio, "sleep", lambda s: real_sleep(0))
    with pytest.raises(asyncio.CancelledError):
        await hub.run()
    # The first connection needs no resync; the reconnection after the drop does.
    assert a.get_nowait() == {"type": "resync"} and a.empty()
    assert b.get_nowait() == {"type": "resync"}
