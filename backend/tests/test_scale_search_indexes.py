"""Search paths and indexes at production shape (dev/speed, 14 Oct 2026).

Two mailboxes share the tables, as in production: a large one (tests/scale_fixture.py, 10k
conversations) and a small one (400 conversations), so the small one holds about 4 % of the
message vectors (production: 3.5 %). Each message gets two passages.

- Reply context (agent/context.py `_related`): the shared HNSW index with a per-user filter. The
  small mailbox must get its full set of neighbours, not the one or two the default scan leaves.
- Passage keyword search (intel/search.py): the matching passages come from ix_passage_tsv and
  ix_msg_tsv, not from a scan of every passage.
- The indexes dropped as unused are gone, and people are found by address through an index.

`pytest -m scale -s tests/test_scale_search_indexes.py` prints the plans' numbers.
"""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from tests import scale_fixture as sf

pytestmark = [pytest.mark.scale, pytest.mark.serial]

SMALL = 400
KEY = "scale-embed"


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def two():
    from mailr.core.users import bootstrap, create_user
    from mailr.db import engine, sessionmaker

    await bootstrap()
    async with sessionmaker()() as db:
        big = await create_user(db, f"scale-big-{uuid.uuid4().hex[:8]}@example.com", "Big", password="scale-password-123")
        await sf.build(db, big.id)
        small = await create_user(db, f"scale-small-{uuid.uuid4().hex[:8]}@example.com", "Small", password="scale-password-123")
        keep = sf.SCALE_CONVERSATIONS
        sf.SCALE_CONVERSATIONS = SMALL
        try:
            await sf.build(db, small.id)
        finally:
            sf.SCALE_CONVERSATIONS = keep
        for uid in (big.id, small.id):
            # A vector of its own per message (the fixture's subquery is evaluated once, so all of a
            # mailbox's vectors are the same), and the model key the queries ask for.
            await db.execute(text(
                "UPDATE messages m SET embed_model = :k, embedding = array(SELECT random() + 0 * m.id "
                "FROM generate_series(1, 512))::vector WHERE m.user_id = :u AND m.embedding IS NOT NULL"), {"k": KEY, "u": uid})
            # Two passages per message: the first and the second half of the body.
            await db.execute(text("""
                INSERT INTO message_passages (user_id, message_id, conversation_id, idx, start, "end", text, content_hash)
                SELECT m.user_id, m.id, m.conversation_id, k, k * 300, k * 300 + 300,
                       substr(m.body_text, k * 300 + 1, 300) || CASE WHEN m.id % 97 = 0 AND k = 1 THEN ' zebracorn' ELSE '' END,
                       md5(m.id::text || ':' || k)
                FROM messages m, generate_series(0, 1) k WHERE m.user_id = :u"""), {"u": uid})
        await db.commit()
    async with engine().connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for t in ("messages", "message_passages", "people"):
            await conn.execute(text(f"VACUUM ANALYZE {t}"))
    yield big, small


async def _plan(sql: str, params: tuple, *, settings: tuple[str, ...] = ()) -> dict:
    from mailr.db import engine

    async with engine().connect() as conn:
        raw = (await conn.get_raw_connection()).driver_connection
        tr = raw.transaction()
        await tr.start()
        try:
            for s in settings:
                await raw.execute(s)
            plan = await raw.fetchval("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, *params)
        finally:
            await tr.rollback()
    plan = json.loads(plan) if isinstance(plan, str) else plan
    return plan[0]["Plan"]


def _nodes(n: dict):
    yield n
    for c in n.get("Plans", []):
        yield from _nodes(c)


def _read(root: dict, relation: str) -> int:
    """Rows of `relation` the plan looked at from the table (all loops)."""
    total = 0
    for n in _nodes(root):
        if n.get("Relation Name") == relation and n["Node Type"] != "Bitmap Index Scan":
            total += (n.get("Actual Rows", 0) + n.get("Rows Removed by Filter", 0)
                      + n.get("Rows Removed by Index Recheck", 0)) * n.get("Actual Loops", 1)
    return total


def _indexes(root: dict) -> set[str]:
    return {n["Index Name"] for n in _nodes(root) if n.get("Index Name")}


async def _principal(uid: int):
    from mailr.core.auth import ALL_SCOPES, Principal
    from mailr.db import sessionmaker
    from mailr.models import User

    async with sessionmaker()() as db:
        u = await db.get(User, uid)
    return Principal(user=u, scopes=set(ALL_SCOPES))


async def test_reply_context_gets_neighbours_for_a_small_mailbox(two, monkeypatch):
    """The small mailbox (4 % of the vectors) gets its full set of semantic neighbours."""
    from mailr.agent import context
    from mailr.db import sessionmaker
    from mailr.intel import embed

    big, small = two
    async with sessionmaker()() as db:
        vec = (await db.execute(text("SELECT embedding::text FROM messages WHERE user_id = :u AND embedding IS NOT NULL "
                                     "ORDER BY id LIMIT 1"), {"u": small.id})).scalar_one()
    vec = json.loads(vec)

    async def fake_embed(*_a, **_k):
        return vec, KEY

    monkeypatch.setattr(embed, "embed_query", fake_embed)
    p = await _principal(small.id)
    async with sessionmaker()() as db:
        # Words no message has: the keyword fallback (which runs only when the vectors found
        # nothing) cannot fill the list, so this counts semantic neighbours alone.
        got = await context._related(db, p, [], None, "xyzzy plugh", limit=8)
    print(f"\nreply context, small mailbox: {len(got)} related conversations (limit 8)")
    assert len(got) == 8

    # The plan: the shared HNSW index. Without the iterative scan its 40 candidates are mostly the
    # big mailbox's, and the user filter leaves almost nothing.
    sql = ("SELECT conversation_id FROM messages WHERE user_id = $1 AND NOT deleted AND embedding IS NOT NULL "
           "AND embed_model = $2 ORDER BY embedding <=> $3::vector LIMIT 24")
    params = (small.id, KEY, json.dumps(vec))
    force = ("SET LOCAL enable_seqscan = off", "SET LOCAL enable_bitmapscan = off", "SET LOCAL enable_sort = off")
    before = await _plan(sql, params, settings=force)
    after = await _plan(sql, params, settings=(*force, "SET LOCAL hnsw.ef_search = 200",
                                               "SET LOCAL hnsw.iterative_scan = relaxed_order"))
    print(f"HNSW scan for the small mailbox: default {before['Actual Rows']} rows, "
          f"iterative {after['Actual Rows']} rows (of 24 asked)")
    assert "ix_msg_embedding" in _indexes(before) and "ix_msg_embedding" in _indexes(after)
    assert before["Actual Rows"] < 8
    assert after["Actual Rows"] == 24


async def test_passage_keyword_search_uses_its_indexes(two, monkeypatch):
    """The keyword half of passage search reads the matching passages through ix_passage_tsv and the
    matching messages through ix_msg_tsv; the old OR across the join read every passage."""
    from mailr.db import sessionmaker
    from mailr.intel import embed, search

    big, _small = two

    async def no_embed(*_a, **_k):
        return None

    monkeypatch.setattr(embed, "embed_query", no_embed)
    p = await _principal(big.id)
    with sf.counting() as m:
        async with sessionmaker()() as db:
            hits = await search.passages(db, p, "zebracorn", k=10, query_sensitive=False)
    assert hits and all("zebracorn" in h.text for h in hits)
    kw = [(s, prm) for s, prm in zip(m.statements, m.params, strict=True) if "ts_rank_cd" in s and "UNION" in s]
    assert kw, m.statements
    sql, params = kw[0]
    root = await _plan(sql, params)
    used = _indexes(root)
    total = (await _scalar("SELECT count(*) FROM message_passages"))
    read = _read(root, "message_passages")
    print(f"\npassage keyword search: indexes {sorted(used)}; passages read {read} of {total}; "
          f"{root['Actual Total Time']:.1f} ms")
    assert "ix_passage_tsv" in used and "ix_msg_tsv" in used
    assert read < total / 20

    # The old form, for the record: one OR across the join.
    old_sql = """
        SELECT message_passages.id FROM message_passages JOIN messages ON messages.id = message_passages.message_id
        WHERE messages.user_id = $1 AND NOT messages.deleted AND (message_passages.tsv @@ websearch_to_tsquery('simple', $2)
          OR (message_passages.idx = 0 AND (messages.tsv @@ websearch_to_tsquery('simple', $2)
                                            OR messages.attached_tsv @@ websearch_to_tsquery('simple', $2))))"""
    old_root = await _plan(old_sql, (big.id, "zebracorn"))
    print(f"old OR form: indexes {sorted(_indexes(old_root))}; passages read {_read(old_root, 'message_passages')}; "
          f"{old_root['Actual Total Time']:.1f} ms")


async def _scalar(sql: str, **params):
    from mailr.db import sessionmaker

    async with sessionmaker()() as db:
        return (await db.execute(text(sql), params)).scalar()


async def test_unused_indexes_are_gone_and_people_searches_have_theirs(two):
    names = set(await _all_index_names())
    assert not names & {"ix_msg_content_hash", "ix_msg_upstream_thread", "ix_change_log_user_id"}
    assert "ix_people_user_lower_email" in names
    # The thread-key lookup (core/threading.py) still has its index.
    big, _ = two
    root = await _plan("SELECT DISTINCT conversation_id FROM messages WHERE user_id=$1 AND id<>$2 "
                       "AND conversation_id IS NOT NULL AND upstream->>'thread_key' = $3", (big.id, 0, "gm:1"),
                       settings=("SET LOCAL enable_seqscan = off",))
    assert "ix_messages_thread_key" in _indexes(root)


async def test_people_lookup_by_address_uses_its_index(two):
    """People by lower(email) (Speakr speakers, the person card), at 9,000 people."""
    from mailr.db import engine, sessionmaker

    big, _ = two
    async with sessionmaker()() as db:
        await db.execute(text("""
            INSERT INTO people (user_id, email, name, organization, received_count, sent_count, notes, blocked)
            SELECT :u, 'person' || g || '@org' || (g % 300) || '.example', 'Person ' || g || ' Name', 'Org ' || (g % 300),
                   0, 0, '', false
            FROM generate_series(1, 9000) g ON CONFLICT DO NOTHING"""), {"u": big.id})
        await db.commit()
    async with engine().connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("ANALYZE people"))
    root = await _plan("SELECT id FROM people WHERE user_id = $1 AND lower(email) = ANY($2)",
                       (big.id, ["person12@org12.example", "person13@org13.example"]))
    print(f"\npeople by address: indexes {sorted(_indexes(root))}; rows read {_read(root, 'people')}; "
          f"{root['Actual Total Time']:.2f} ms")
    assert "ix_people_user_lower_email" in _indexes(root)
    assert _read(root, "people") <= 2


async def _all_index_names() -> list[str]:
    from mailr.db import sessionmaker

    async with sessionmaker()() as db:
        return list((await db.execute(text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"))).scalars())
