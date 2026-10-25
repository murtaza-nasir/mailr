"""The cap on concurrent model calls (intel/slots.py): at most MAILR_MODEL_CONCURRENCY calls per
endpoint across processes, a fair share per user, live calls ahead of background ones, and a slot
kept for live calls. Each test uses an endpoint id of its own, so the slots never meet."""

from __future__ import annotations

import asyncio
import random

import pytest

from mailr.intel import slots

WAIT = 10.0


@pytest.fixture
def endpoint(app, monkeypatch):
    monkeypatch.setattr(slots, "cap", lambda: 4)
    return random.randint(100_000, 1_000_000)


class Calls:
    """Model calls that hold their slot until released, and the peak of how many ran at once."""

    def __init__(self, endpoint: int):
        self.endpoint = endpoint
        self.running: list[str] = []
        self.order: list[str] = []
        self.peak = 0
        self.go: dict[str, asyncio.Event] = {}
        self.tasks: list[asyncio.Task] = []

    def start(self, name: str, user: int | None, live: bool) -> None:
        self.go[name] = asyncio.Event()

        async def call():
            slots.LIVE.set(live)
            async with slots.slot(self.endpoint, user):
                self.running.append(name)
                self.order.append(name)
                self.peak = max(self.peak, len(self.running))
                await self.go[name].wait()
                self.running.remove(name)

        self.tasks.append(asyncio.create_task(call()))

    async def settle(self, n: int | None = None) -> None:
        """Until n calls run (or, without n, a little while)."""
        deadline = asyncio.get_running_loop().time() + WAIT
        while n is not None and len(self.running) < n:
            assert asyncio.get_running_loop().time() < deadline, f"{self.running} run, wanted {n}"
            await asyncio.sleep(0.02)
        await asyncio.sleep(slots.POLL_SECONDS * 2)

    async def done(self) -> None:
        for e in self.go.values():
            e.set()
        await asyncio.wait_for(asyncio.gather(*self.tasks), WAIT)


async def test_at_most_the_cap_and_one_slot_kept_for_live_calls(endpoint):
    c = Calls(endpoint)
    for i in range(6):
        c.start(f"bg{i}", 1000 + i, live=False)
    await c.settle(3)
    assert len(c.running) == 3  # background calls leave the fourth slot to live ones
    c.start("live", 2000, live=True)
    await c.settle(4)
    assert "live" in c.running and len(c.running) == 4
    await c.done()
    assert c.peak == 4


async def test_one_user_holds_half_of_the_slots_while_another_waits(endpoint):
    c = Calls(endpoint)
    for i in range(4):
        c.start(f"hold{i}", 10 + i, live=True)
    await c.settle(4)
    for i in range(4):
        c.start(f"a{i}", 1, live=True)
    await asyncio.sleep(0.1)
    c.start("b", 2, live=True)
    await asyncio.sleep(0.1)
    for i in range(4):
        c.go[f"hold{i}"].set()
    await c.settle(4)
    first = c.order[4:7]
    assert "b" in first and sum(n.startswith("a") for n in first) == 2  # user 1's share, then user 2
    await c.done()


async def test_a_user_alone_uses_every_slot(endpoint):
    c = Calls(endpoint)
    for i in range(4):
        c.start(f"a{i}", 1, live=True)
    await c.settle(4)
    await c.done()
    assert c.peak == 4


async def test_live_calls_go_before_background_ones_that_waited_longer(endpoint):
    c = Calls(endpoint)
    for i in range(4):
        c.start(f"hold{i}", 10 + i, live=True)
    await c.settle(4)
    c.start("triage", 20, live=False)
    await asyncio.sleep(0.1)
    c.start("chat", 21, live=True)
    await asyncio.sleep(0.1)
    c.go["hold0"].set()
    await c.settle()
    assert "chat" in c.running and "triage" not in c.running
    c.go["hold1"].set()
    await c.settle()
    assert "triage" in c.running
    await c.done()


async def test_slots_held_by_another_process_count(endpoint):
    """Another process (an advisory lock on another connection) holds three slots: one is left."""
    import asyncpg

    from mailr.config import get_settings

    other = await asyncpg.connect(get_settings().database_url.replace("postgresql+asyncpg", "postgresql"))
    try:
        for i in range(3):
            assert await other.fetchval("SELECT pg_try_advisory_lock($1, $2)", slots.SLOT_LOCK,
                                        endpoint * slots.SLOTS_PER_ENDPOINT + i)
        c = Calls(endpoint)
        c.start("x", 1, live=True)
        c.start("y", 2, live=True)
        await c.settle(1)
        assert len(c.running) == 1
        await other.fetchval("SELECT pg_advisory_unlock_all()")
        await c.settle(2)
        assert len(c.running) == 2
        await c.done()
    finally:
        await other.close()


async def test_a_call_inside_a_call_takes_no_second_slot(endpoint, monkeypatch):
    monkeypatch.setattr(slots, "cap", lambda: 1)
    async with slots.slot(endpoint, 1):
        async with slots.slot(endpoint, 1):
            pass


async def test_no_cap(endpoint, monkeypatch):
    monkeypatch.setattr(slots, "cap", lambda: 0)
    c = Calls(endpoint)
    for i in range(8):
        c.start(f"n{i}", 1, live=False)
    await c.settle(8)
    await c.done()
    assert c.peak == 8


async def test_model_calls_take_a_slot(app, user, monkeypatch):
    """llm.chat holds a slot of its endpoint for the call."""
    import contextlib

    import httpx

    from mailr.db import sessionmaker
    from mailr.intel import llm
    from mailr.models import User

    taken = []

    @contextlib.asynccontextmanager
    async def slot(endpoint_id, user_id):
        taken.append(user_id)
        yield

    async def post(self, url, **kw):
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
                              request=httpx.Request("POST", url))

    import uuid

    from sqlalchemy import delete

    from mailr.models import ModelEndpoint

    monkeypatch.setattr(slots, "slot", slot)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    name = f"slots-{uuid.uuid4().hex[:6]}"
    token = llm.FORCE_ENDPOINT.set(name)
    async with sessionmaker()() as db:
        db.add(ModelEndpoint(name=name, base_url="https://api.hosted.example/v1", locality="hosted",
                             defaults={"chat": "big"}))
        await db.commit()
    try:
        async with sessionmaker()() as db:
            u = await db.get(User, user.id)
            await llm.chat(db, u, [{"role": "user", "content": "hi"}], sensitive=False)
    finally:
        llm.FORCE_ENDPOINT.reset(token)
        async with sessionmaker()() as db:
            await db.execute(delete(ModelEndpoint).where(ModelEndpoint.name == name))
            await db.commit()
    assert taken == [user.id]
