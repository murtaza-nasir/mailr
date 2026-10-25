# ACT-SURFACES: api
"""GET /agent/sessions?q= at scale (ACT agent.chat_search, 8 Oct 2026).

One user with CHATS chats (default 10,000) of 20 turns each: 200,000 questions and 200,000
answers, about 60 words per answer drawn from a skewed vocabulary, so some words are in every
answer and some in a handful. Budget: each search, through the API, in SEARCH_BUDGET_MS (the
median of five). The searches: a word in every answer ("the"), a word in 1 % of the answers, a word
in three, a prefix, two words, a title, a word that is nowhere (full text finds nothing, then the
substring fallback runs) and a part of a word (the fallback finds it).
MAILR_SCALE_REPORT=1 measures without asserting; MAILR_CHAT_SEARCH_CHATS sets the number of chats.

Measured 8 Oct 2026 (32 cores, other agents running), median of five through the API: with one
query that grouped every match, "the" took 116 ms, a one-letter prefix 350 ms and a part of many
words (the substring fallback) 497 ms; the rest 15–30 ms. With the newest 3,000 questions and
answers read first (chat_search._run), every search took 58–74 ms. Building the 400,000 messages
and their indexes: about 45 s.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

pytestmark = [pytest.mark.scale, pytest.mark.serial]

CHATS = int(os.environ.get("MAILR_CHAT_SEARCH_CHATS") or 10_000)
TURNS = 20
SEARCH_BUDGET_MS = 300.0
REPORT_ONLY = os.environ.get("MAILR_SCALE_REPORT") == "1"


async def build(db, user_id: int) -> float:
    t0 = time.perf_counter()
    p = {"u": user_id, "us": str(user_id), "n": CHATS, "turns": TURNS}
    # Texts to draw from: 500 questions and 2,000 answers over a vocabulary of 5,000 words, the
    # low-numbered words far more often (random()^3).
    await db.execute(text("""
        CREATE TEMP TABLE scale_q ON COMMIT DROP AS
        SELECT i, 'Can you find ' || array_to_string(ARRAY(
                   SELECT 'word' || (1 + floor(power(random(), 3) * 5000))::int FROM generate_series(1, 8 + 0 * i)), ' ')
                   || '?' AS t
        FROM generate_series(0, 499) i"""))
    await db.execute(text("""
        CREATE TEMP TABLE scale_a ON COMMIT DROP AS
        SELECT i, 'I looked at the **mail** and ' || array_to_string(ARRAY(
                   SELECT 'word' || (1 + floor(power(random(), 3) * 5000))::int FROM generate_series(1, 60 + 0 * i)), ' ')
                   || ' [1](mailr-cite:1).' AS t
        FROM generate_series(0, 1999) i"""))
    await db.execute(text("""
        INSERT INTO chat_sessions (id, user_id, token_id, title, title_custom, sensitive, context, created_at, updated_at)
        SELECT 'scale-' || :us || '-' || g, :u, NULL,
               CASE WHEN g = 4242 THEN 'Harbourmaster contract' ELSE 'Chat number ' || g END, g = 4242, false,
               '{}'::jsonb, now() - g * interval '1 hour', now() - g * interval '1 hour' + interval '30 minutes'
        FROM generate_series(1, :n) g"""), p)
    await db.execute(text("""
        INSERT INTO chat_messages (session_id, role, content, status, created_at)
        SELECT 'scale-' || :us || '-' || g, r.role,
               CASE WHEN r.role = 'user' THEN (SELECT t FROM scale_q WHERE i = (g * 20 + k) % 500)
                    ELSE (SELECT t FROM scale_a WHERE i = (g * 31 + k * 7) % 2000)
                         || CASE WHEN (g + k) % 100 = 0 THEN ' The quarterly numbers are in.' ELSE '' END
                         || CASE WHEN g * 20 + k IN (2045, 77003, 150017) THEN ' Ask the zebracorn desk.' ELSE '' END
               END,
               CASE WHEN r.role = 'user' THEN 'done' END,
               now() - g * interval '1 hour' + k * interval '1 minute' + r.o * interval '1 second'
        FROM generate_series(1, :n) g, generate_series(1, :turns) k,
             (VALUES ('user', 0), ('assistant', 1)) AS r(role, o)
        ORDER BY g, k, r.o"""), p)
    await db.commit()
    from mailr.db import engine

    async with engine().connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("VACUUM ANALYZE chat_sessions"))
        await conn.execute(text("VACUUM ANALYZE chat_messages"))
    return time.perf_counter() - t0


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def chats():
    from mailr.core.users import bootstrap, create_user
    from mailr.db import engine, sessionmaker
    from mailr.main import create_app

    await bootstrap()
    app = create_app()
    async with sessionmaker()() as db:
        u = await create_user(db, f"chats-{uuid.uuid4().hex[:8]}@example.com", "Chats", password="chats-password-123")
        seconds = await build(db, u.id)
        uid = u.id
    print(f"\nchat search fixture: {CHATS} chats x {TURNS} turns in {seconds:.1f}s")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/login", json={"email": u.email, "password": "chats-password-123"})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        yield c
    async with sessionmaker()() as db:
        await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
        await db.commit()
    async with engine().connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("VACUUM ANALYZE chat_sessions"))
        await conn.execute(text("VACUUM ANALYZE chat_messages"))


async def _timed(c, q: str) -> tuple[float, list]:
    t0 = time.perf_counter()
    r = await c.get("/api/v1/agent/sessions", params={"q": q, "limit": 30})
    ms = (time.perf_counter() - t0) * 1000
    assert r.status_code == 200, r.text
    return ms, r.json()


SEARCHES = [
    # (query, what it shows, the least results expected)
    ("the", "a word in every answer", 30),
    ("w", "a one-letter prefix: every question and answer", 30),
    ("quarterly", "a word in 1% of the answers", 30),
    ("zebracorn", "a word in three answers", 3),
    ("zebrac", "a prefix", 3),
    ("quarterly numbers", "two words", 30),
    ("harbourmaster", "a title", 1),
    ("qqxyzzy", "nowhere (full text, then the fallback)", 0),
    ("ebracor", "part of a word (the fallback)", 3),
    ("ord12", "part of many words (the fallback, many hits)", 30),
]


@pytest.mark.parametrize("q,what,least", SEARCHES, ids=[s[0].replace(" ", "_") for s in SEARCHES])
async def test_chat_search_is_fast(chats, q, what, least):
    await _timed(chats, q)  # warm
    runs = []
    body: list = []
    for _ in range(5):
        ms, body = await _timed(chats, q)
        runs.append(ms)
    runs.sort()
    print(f"  {q!r:22} {what:42} median {runs[2]:6.1f} ms (min {runs[0]:.1f}, max {runs[-1]:.1f}), {len(body)} results")
    assert len(body) >= least
    if body:
        assert body[0]["snippet"] or body[0]["title_parts"]
        assert len({r["id"] for r in body}) == len(body)
    if not REPORT_ONLY:
        assert runs[2] < SEARCH_BUDGET_MS, runs
