"""Query budgets at production scale.

A 10k-conversation, 20k-message mailbox (tests/scale_fixture.py) is built once for the module.
Each test counts the SQL statements an endpoint sends and, for bulk actions, the mail-store
calls, and fails when a count passes its budget. The main list query must walk the ordered
conversation index rather than sort the view.

The module is marked `scale`: the normal suite skips it, `pytest -m scale` (or MAILR_TEST_SCALE=1)
runs it, the nightly run includes it, and `pytest -m scale -s` prints the measurements.
MAILR_SCALE_REPORT=1 measures without asserting the budgets, MAILR_SCALE_CONVERSATIONS=45000
builds the production size (90k messages), and MAILR_SCALE_HIST=1 adds each request's most
frequent and slowest statements.
"""

from __future__ import annotations

import json
import os
import uuid
from collections import Counter

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests import scale_fixture as sf

# Plan shape depends on what else is in the database: run alone, after the parallel pass.
pytestmark = [pytest.mark.scale, pytest.mark.serial]

# Upper bounds on SQL statements per request (measured 3 Oct 2026 in brackets). A list page runs
# the session, the labels, the change cursor, the page, attachment previews, signals and the
# audit row [8]; the first request of a list also counts its total [9].
BUDGET_LIST_FIRST = 10
BUDGET_LIST_NEXT = 9
BUDGET_SEARCH = 10
# Archive of 500 conversations (1000 messages) and its Undo: a fixed number of statements,
# not one per message or conversation [17, 16; before: 1515, 2015]. The mail tally's upkeep at
# the commit adds six (core/tally.py: a savepoint, the locks, the count, the write, the tidy-up,
# the release) [24 Oct 2026: 26, 25].
BUDGET_ARCHIVE_SQL = 30
BUDGET_UNDO_SQL = 30
# One Email/set per 200 messages [5, 5; the Undo before: 1000].
BUDGET_ARCHIVE_JMAP = 5
BUDGET_UNDO_JMAP = 5
# A bulk job, per 200-conversation chunk [about 19; with the mail tally's upkeep, about 25].
BUDGET_JOB_CHUNK_SQL = 30

MEASURED: dict[str, sf.Counter] = {}
# MAILR_SCALE_REPORT=1: measure only (the budgets are not asserted), for before/after numbers.
REPORT_ONLY = os.environ.get("MAILR_SCALE_REPORT") == "1"


def budget(ok: bool, detail) -> None:
    if not REPORT_ONLY:
        assert ok, detail


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scale():
    from mailr.core.users import bootstrap, create_user
    from mailr.db import sessionmaker
    from mailr.main import create_app

    await bootstrap()
    app = create_app()
    async with sessionmaker()() as db:
        u = await create_user(db, f"scale-{uuid.uuid4().hex[:8]}@example.com", "Scale", password="scale-password-123")
        built = await sf.build(db, u.id)
    print(f"\nscale fixture: {built.conversations} conversations, {built.messages} messages in {built.seconds:.2f}s")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/login", json={"email": u.email, "password": "scale-password-123"})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        yield built, c
    print()
    for name, m in MEASURED.items():
        print(f"  {name:<28} {m.seconds * 1000:8.1f} ms  {m.sql:4d} SQL  {len(m.jmap):5d} JMAP")
        if os.environ.get("MAILR_SCALE_HIST"):
            for st, k in Counter(" ".join(x.split())[:110] for x in m.statements).most_common(6):
                print(f"      {k:5d}  {st}")
            for d, st in sorted(m.durations, reverse=True)[:4]:
                print(f"      {d * 1000:7.1f} ms  {' '.join(st.split())[:150]}")


async def _get(c, name: str, url: str, **params):
    with sf.counting() as cold:  # the first request counts the total; the second reuses it
        await c.get(url, params=params)
    MEASURED[name + " (cold)"] = cold
    with sf.counting() as m:
        r = await c.get(url, params=params)
    assert r.status_code == 200, r.text
    MEASURED[name] = m
    return r.json(), m


async def test_inbox_pages(scale):
    built, c = scale
    page1, m1 = await _get(c, "inbox page 1", "/api/v1/conversations", view="inbox")
    assert len(page1["items"]) == 50
    assert page1["total"] == built.conversations // 10
    budget(m1.sql <= BUDGET_LIST_FIRST, m1.statements)
    page2, m2 = await _get(c, "inbox page 2", "/api/v1/conversations", view="inbox", cursor=page1["next_cursor"])
    assert len(page2["items"]) == 50
    assert not {i["id"] for i in page1["items"]} & {i["id"] for i in page2["items"]}
    # Later pages carry the total from the first without counting again.
    assert page2["total"] == page1["total"]
    assert not [s for s in m2.statements if "count(" in s.lower() and "FROM conversations" in s], m2.statements
    budget(m2.sql <= BUDGET_LIST_NEXT, m2.statements)
    budget(MEASURED["inbox page 1 (cold)"].sql <= BUDGET_LIST_FIRST, MEASURED["inbox page 1 (cold)"].statements)
    # Deep paging stays ordered and complete.
    seen, cursor = set(), None
    while True:
        r = await c.get("/api/v1/conversations", params={"view": "inbox", "limit": 200, **({"cursor": cursor} if cursor else {})})
        body = r.json()
        dates = [i["last_date"] for i in body["items"]]
        assert dates == sorted(dates, reverse=True)
        seen |= {i["id"] for i in body["items"]}
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert len(seen) == page1["total"]


async def test_archive_view_page(scale):
    _built, c = scale
    body, m = await _get(c, "all mail page 1", "/api/v1/conversations", view="all")
    assert len(body["items"]) == 50
    budget(m.sql <= BUDGET_LIST_FIRST, m.statements)


def _nodes(plan: dict):
    yield plan
    for child in plan.get("Plans") or []:
        yield from _nodes(child)


async def _explain(stmt: str, params, *, no_seqscan: bool = False) -> list[dict]:
    from mailr.db import engine

    async with engine().connect() as conn:
        raw = (await conn.get_raw_connection()).driver_connection
        async with raw.transaction():
            if no_seqscan:
                await raw.execute("SET LOCAL enable_seqscan = off")
            plan = await raw.fetchval("EXPLAIN (FORMAT JSON) " + stmt, *params)
    plan = json.loads(plan) if isinstance(plan, str) else plan
    return list(_nodes(plan[0]["Plan"]))


async def _analyze(stmt: str, params, *, index_paths: bool = False) -> tuple[list[dict], int]:
    """EXPLAIN (ANALYZE, BUFFERS) of one statement, rolled back: its plan nodes and the shared
    buffers the whole plan touched. `index_paths` turns sequential and bitmap heap scans off, so
    the plan shows the cheapest index path whatever the planner would pick at the fixture's size
    (its cost model differs between servers: a fresh Postgres container picks other plans)."""
    from mailr.db import engine

    async with engine().connect() as conn:
        raw = (await conn.get_raw_connection()).driver_connection
        tr = raw.transaction()
        await tr.start()
        try:
            if index_paths:
                await raw.execute("SET LOCAL enable_seqscan = off")
                await raw.execute("SET LOCAL enable_bitmapscan = off")
            plan = await raw.fetchval("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + stmt, *params)
        finally:
            await tr.rollback()
    plan = json.loads(plan) if isinstance(plan, str) else plan
    root = plan[0]["Plan"]
    return list(_nodes(root)), root.get("Shared Hit Blocks", 0) + root.get("Shared Read Blocks", 0)


def _rows_read(nodes: list[dict], relation: str) -> int:
    """The rows of `relation` the plan read from the table itself: every row a scan looked at,
    returned or filtered out, in all its loops. An index-only scan reads only its heap fetches."""
    n = 0
    for node in nodes:
        if node.get("Relation Name") != relation:
            continue
        if node["Node Type"] == "Index Only Scan":
            n += node.get("Heap Fetches", 0)
            continue
        looked = (node.get("Actual Rows", 0) + node.get("Rows Removed by Filter", 0)
                  + node.get("Rows Removed by Index Recheck", 0))
        n += looked * node.get("Actual Loops", 1)
    return n


async def _table(relation: str) -> tuple[int, int]:
    """(pages, rows) of a table, from the statistics VACUUM ANALYZE keeps."""
    from mailr.db import engine

    async with engine().connect() as conn:
        raw = (await conn.get_raw_connection()).driver_connection
        pages, rows = await raw.fetchrow("SELECT relpages, reltuples FROM pg_class WHERE relname = $1", relation)
    return pages, int(rows)


async def _vacuum(*tables: str) -> None:
    """VACUUM ANALYZE as autovacuum would: the bulk tests before rewrite message rows, which clears
    their pages from the visibility map that index-only scans rely on."""
    from sqlalchemy import text

    from mailr.db import engine

    async with engine().connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for t in tables:
            await conn.execute(text(f"VACUUM ANALYZE {t}"))


def _page_query(m: sf.Counter) -> tuple[str, tuple]:
    page = [(s, p) for s, p in zip(m.statements, m.params, strict=True)
            if s.lstrip().upper().startswith("SELECT") and "FROM conversations" in s and "ORDER BY" in s
            and "LIMIT" in s and "count(" not in s.lower()]
    assert page, m.statements
    return page[0]


# A list page reads about a page of conversations, not the view: rows looked at and buffers
# touched by the page query [measured 13 Oct 2026: at most 510 rows (the inbox, whose filter skips
# the archived rows between) and 22 buffers; a scan of the view reads all 10,000 rows, 332 buffers].
BUDGET_PAGE_ROWS = 1_000
BUDGET_PAGE_BUFFERS = 100


async def test_list_query_uses_ordered_index(scale):
    """EXPLAIN ANALYZE of the page query: it walks the conversations in list order (no Sort) and
    stops after the page, on page 1 and on a later page (the cursor is an index condition). The
    index the planner picks for it is not asserted, only what it reads."""
    _built, c = scale
    for view in ("inbox", "all", "archive"):
        cursor = None
        for _page in range(2):
            with sf.counting() as m:
                r = await c.get("/api/v1/conversations", params={"view": view, **({"cursor": cursor} if cursor else {})})
            assert r.status_code == 200
            cursor = r.json()["next_cursor"]
            nodes, buffers = await _analyze(*_page_query(m))
            kinds = [(n["Node Type"], n.get("Relation Name"), n.get("Index Name")) for n in nodes]
            assert not any(k in ("Sort", "Incremental Sort") for k, _r, _i in kinds), (view, kinds)
            assert _rows_read(nodes, "conversations") <= BUDGET_PAGE_ROWS, (view, kinds)
            assert buffers <= BUDGET_PAGE_BUFFERS, (view, buffers, kinds)


async def test_search_by_word(scale):
    _built, c = scale
    body, m = await _get(c, "search word", "/api/v1/conversations", view="all", q=sf.RARE_WORD)
    assert body["total"] > 50
    assert all(sf.RARE_WORD in i["subject"] for i in body["items"])
    budget(m.sql <= BUDGET_SEARCH, m.statements)


async def test_search_by_recipient_and_file_name(scale):
    """to: and cc: with part of an address or name, and filename:, read the trigram indexes on
    the JSON text, then check the matching messages' arrays."""
    _built, c = scale
    for name, q in (("search to: substring", f"to:{sf.PERSON[:9]}"), ("search cc: name", "cc:whitfield"),
                    ("search filename:", "filename:report-12")):
        body, m = await _get(c, name, "/api/v1/conversations", view="all", q=q)
        assert body["total"] > 0, q
        budget(m.sql <= BUDGET_SEARCH, m.statements)
        budget(m.seconds < 0.5, m.durations)


async def test_search_by_sender(scale):
    _built, c = scale
    body, m = await _get(c, "search sender", "/api/v1/conversations", view="all", q=f"from:{sf.SENDER}")
    assert body["total"] > 0
    budget(m.sql <= BUDGET_SEARCH, m.statements)


async def test_search_uses_indexes(scale):
    """Plain-word, sender and subject searches can reach the messages through their indexes
    (pg_trgm GIN on the subject and on the sender; the tsv GIN). At the fixture's size a
    sequential scan is still the cheaper plan (at 90k messages the planner picks the BitmapOr),
    so the check plans the search predicate alone with sequential scans off: what must not
    regress is that every arm is indexable (a lower() around a column was not)."""
    from sqlalchemy import select

    from mailr.core import search
    from mailr.db import engine
    from mailr.models import Message

    built, _c = scale
    expect = {
        # A plain word also matches the text of attached emails (partial index ix_msg_attached_tsv).
        sf.RARE_WORD: {"ix_msg_tsv", "ix_msg_attached_tsv", "ix_msg_subject_trgm", "ix_msg_sender_trgm"},
        f"from:{sf.SENDER}": {"ix_msg_sender_trgm"},
        "subject:report": {"ix_msg_subject_trgm"},
        # Substrings of recipients, file names and list ids: the trigram indexes on their text.
        f"to:{sf.PERSON[:9]}": {"ix_msg_to_trgm"},
        "cc:whitfield": {"ix_msg_cc_trgm"},
        "filename:report-12": {"ix_msg_attachments_trgm"},
        "list:list7.lists": {"ix_msg_list_trgm"},
    }
    for q, indexes in expect.items():
        where = search.compile_query(q, built.user_id, []).where
        compiled = select(Message.id).where(where).compile(dialect=engine().sync_engine.dialect)
        params = tuple(compiled.params[k] for k in compiled.positiontup)
        nodes = await _explain(str(compiled), params, no_seqscan=True)
        kinds = [(n["Node Type"], n.get("Relation Name"), n.get("Index Name")) for n in nodes]
        assert not any(k == "Seq Scan" for k, _r, _i in kinds), (q, kinds)
        assert indexes == {i for _k, _r, i in kinds if i}, (q, kinds)


async def test_bulk_archive_and_undo(scale, monkeypatch):
    built, c = scale
    fake = sf.FakeStore()
    fake.install(monkeypatch)
    r = await c.get("/api/v1/conversations", params={"view": "inbox", "limit": 200})
    ids = [i["id"] for i in r.json()["items"]]
    cursor = r.json()["next_cursor"]
    while len(ids) < 500 and cursor:
        r = await c.get("/api/v1/conversations", params={"view": "inbox", "limit": 200, "cursor": cursor})
        ids += [i["id"] for i in r.json()["items"]]
        cursor = r.json()["next_cursor"]
    ids = ids[:500]
    assert len(ids) == 500
    with sf.counting() as m:
        fake.counter = m
        r = await c.post("/api/v1/conversations/actions", json={"action": "archive", "ids": ids})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["affected"] == 1000
    MEASURED["bulk archive 500"] = m
    budget(m.sql <= BUDGET_ARCHIVE_SQL, (m.sql, m.statements[:80]))
    budget(len(m.jmap) <= BUDGET_ARCHIVE_JMAP, m.jmap)

    undo = res["undo"]
    with sf.counting() as m:
        fake.counter = m
        r = await c.post("/api/v1/conversations/actions", json={**undo, "action": undo["action"]})

    assert r.status_code == 200, r.text
    MEASURED["undo archive 500"] = m
    budget(len(m.jmap) <= BUDGET_UNDO_JMAP, (len(m.jmap), m.jmap[:5]))
    budget(m.sql <= BUDGET_UNDO_SQL, (m.sql, m.statements[:80]))
    r = await c.get("/api/v1/conversations", params={"view": "inbox"})
    assert r.json()["total"] == built.conversations // 10


async def test_untrash_batches_store_calls(scale, monkeypatch):
    _built, c = scale
    fake = sf.FakeStore()
    fake.install(monkeypatch)
    r = await c.get("/api/v1/conversations", params={"view": "trash", "limit": 200})
    ids = [i["id"] for i in r.json()["items"]]
    assert len(ids) == 200
    with sf.counting() as m:
        fake.counter = m
        r = await c.post("/api/v1/conversations/actions", json={"action": "untrash", "ids": ids})
    assert r.status_code == 200, r.text
    assert r.json()["affected"] == 400
    MEASURED["untrash 200"] = m
    budget(len(m.jmap) <= 4, m.jmap)
    undo = r.json()["undo"]
    r = await c.post("/api/v1/conversations/actions", json=undo)
    assert r.status_code == 200, r.text


async def _run_job(job_id: int, user_id: int) -> None:
    import types

    from mailr.core import tasks
    from mailr.db import session_scope
    from mailr.models import Job

    async with session_scope() as db:
        j = await db.get(Job, job_id)
        payload = dict(j.payload)
        j.status = "running"
    await tasks.bulk_conversations(payload, types.SimpleNamespace(job_id=job_id, user_id=user_id))
    async with session_scope() as db:
        (await db.get(Job, job_id)).status = "done"


def _payload_writes(m: sf.Counter) -> tuple[int, int]:
    """(UPDATEs of jobs.payload, bytes of payload they sent)."""
    n = size = 0
    for st, params in zip(m.statements, m.params, strict=True):
        if st.lstrip().upper().startswith("UPDATE JOBS") and "payload" in st:
            n += 1
            size += sum(len(str(x)) for x in (params or ()))
    return n, size


async def test_bulk_job_writes_progress_not_payload(scale, monkeypatch):
    """A select-all archive of 1000 conversations runs as a job in 200-conversation chunks:
    each chunk writes its progress and its undo part, and the payload is written at most once."""
    built, c = scale
    fake = sf.FakeStore()
    fake.install(monkeypatch)
    label = built.user_labels[-1]  # every g % 10 == 4 conversation of the fixture
    q = {"view": f"label:{label}"}
    total = (await c.get("/api/v1/conversations", params=q)).json()["total"]
    assert total == built.conversations // 10
    r = await c.post("/api/v1/conversations/actions", json={"action": "archive", "query": q, "from_label_id": label})
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    with sf.counting() as m:
        fake.counter = m
        await _run_job(job, built.user_id)
    MEASURED[f"bulk job archive {total}"] = m
    budget(m.sql <= BUDGET_JOB_CHUNK_SQL * (total // 200 + 1), m.sql)
    budget(len(m.jmap) <= 2 * (total // 200 + 1), m.jmap)
    writes, size = _payload_writes(m)
    print(f"\n  job payload writes: {writes}, {size} bytes")
    budget(writes <= 1, (writes, size))
    st = (await c.get(f"/api/v1/bulk/{job}")).json()
    assert st["done"] == st["total"] == total and st["status"] == "done"
    assert (await c.get("/api/v1/conversations", params=q)).json()["total"] == 0
    r = await c.post("/api/v1/conversations/actions", json=st["undo"])
    assert r.status_code == 202, r.text
    with sf.counting() as m:
        fake.counter = m
        await _run_job(r.json()["job"], built.user_id)
    MEASURED[f"bulk job undo {total}"] = m
    budget(m.sql <= BUDGET_JOB_CHUNK_SQL * (total // 200 + 1), m.sql)
    assert (await c.get("/api/v1/conversations", params=q)).json()["total"] == total


async def test_sidebar_counts_in_one_query(scale):
    """GET /counts: the system views in one aggregate query over the conversations, the user labels
    in one grouped query (core/sidebar.py), then cached until the next change. No query per view."""
    _built, c = scale
    body, m = await _get(c, "sidebar counts", "/api/v1/counts")
    assert body["views"]["inbox"]["total"] > 0
    cold = MEASURED["sidebar counts (cold)"]
    scans = [s for s in cold.statements if "count(*) FILTER" in s and "FROM conversations" in s]
    assert len(scans) == 2, len(scans)
    assert sum("GROUP BY conversations.label_ids" in s for s in scans) == 1
    budget(cold.sql <= 16, cold.statements)
    budget(m.sql <= 5, m.statements)


# The subscriptions page and the cleanup groups (core/mailgroups.py): one aggregate over the mail
# tally (core/tally.py), never over the messages [measured 4 Oct 2026: 4, 4, 9 statements].
BUDGET_SUBSCRIPTIONS = 8
BUDGET_CLEANUP_GROUPS = 8
BUDGET_CLEANUP_PREVIEW = 12
# The tally aggregate's buffers, against the message table's pages. Measured 24 Oct 2026 at 300k
# messages (150k conversations, MAILR_SCALE_CONVERSATIONS=150000): before the tally, the
# index-only aggregate over ix_msg_groups touched 28,145 (subscriptions) and 28,237 (by sender)
# buffers against 54,975 heap pages, more than the third this test allowed, in 218 and 190 ms;
# from the tally, 332 and 322 buffers against 55,299, in 27 and 11 ms. At the fixture's 20k
# messages: 171 and 163 buffers against 3,641.
TALLY_SHARE = 10


def _aggregate(m: sf.Counter, marker: str) -> tuple[str, tuple]:
    hits = [(s, p) for s, p in zip(m.statements, m.params, strict=True)
            if s.lstrip().upper().startswith("SELECT") and "GROUP BY" in s and marker in s]
    assert hits, m.statements
    return hits[0]


async def _assert_from_tally(m: sf.Counter, detail) -> None:
    """The page groups the mail from the tally: its aggregate reads only the messages between an
    age band's edge and the page's cutoff (here the few of the minutes since the tally was counted)
    and touches fewer pages than a tenth of the message table."""
    await _vacuum("messages", "mail_tally", "mail_tally_units")
    pages, _rows = await _table("messages")
    nodes, buffers = await _analyze(*_aggregate(m, "FROM mail_tally"))
    kinds = [(n["Node Type"], n.get("Relation Name"), n.get("Index Name")) for n in nodes]
    print(f"\n  tally aggregate {detail}: {buffers} buffers ({pages} message pages)")
    assert _rows_read(nodes, "messages") <= 100, (detail, kinds)
    assert buffers < pages // TALLY_SHARE, (detail, buffers, pages, kinds)


async def test_subscriptions_page(scale):
    """GET /subscriptions: every list and bulk sender in one aggregate over the mail tally."""
    _built, c = scale
    body, m = await _get(c, "subscriptions", "/api/v1/subscriptions")
    assert body["total"] >= 50 and len(body["items"]) == 50
    assert all(i["unsubscribe"] in ("one_click", "mailto", "link") for i in body["items"])
    budget(m.sql <= BUDGET_SUBSCRIPTIONS, m.statements)
    await _assert_from_tally(m, "subscriptions")
    # The other orders and a later page cost the same.
    for sort in ("volume", "last", "open_rate"):
        _b, m2 = await _get(c, f"subscriptions by {sort}", "/api/v1/subscriptions", sort=sort, offset=50)
        budget(m2.sql <= BUDGET_SUBSCRIPTIONS, m2.statements)


async def test_cleanup_groups(scale):
    """GET /cleanup/groups by sender, domain, list and category over the archived mail (most of
    the fixture), with the protected conversations counted apart, from the mail tally."""
    _built, c = scale
    for by in ("sender", "domain", "list", "category"):
        body, m = await _get(c, f"cleanup groups by {by}", "/api/v1/cleanup/groups", by=by)
        assert body["total"] > 0 and body["items"][0]["conversations"] > 0
        budget(m.sql <= BUDGET_CLEANUP_GROUPS, m.statements)
        await _assert_from_tally(m, by)
    body, m = await _get(c, "cleanup groups uncategorised", "/api/v1/cleanup/groups", by="sender",
                         uncategorised="true", older_than_days=30)
    assert body["items"][0]["protected"] > 0, "starred and important conversations are left alone"
    await _assert_from_tally(m, "sender, older than 30 days")


async def test_cleanup_preview_of_a_large_group(scale):
    """GET /cleanup/preview of the largest domain group: exact conversations, 20 samples."""
    _built, c = scale
    top = (await c.get("/api/v1/cleanup/groups", params={"by": "domain"})).json()["items"][0]
    body, m = await _get(c, "cleanup preview", "/api/v1/cleanup/preview", by="domain", key=top["key"])
    assert body["conversations"] > 100 and len(body["samples"]) == 20
    budget(m.sql <= BUDGET_CLEANUP_PREVIEW, m.statements)
    # The group is counted and sampled in the database: no statement carries its ids (a group
    # over 32,767 conversations failed, asyncpg's limit on bound parameters).
    widest = max(len(x or ()) for x in m.params)
    assert widest < 50, widest


# The person page: the person, their recent conversations (from, to or cc), the open threads
# and the groups, whatever the person's volume of mail.
BUDGET_PERSON = 12
PERSON_PAGES = {
    "person (typical sender)": "sender45@domain5.com",
    "person (to/cc only)": sf.PERSON,
    "person (notification sender)": sf.ALERTS,
}


def _messages_lookups(m: sf.Counter) -> list[tuple[str, tuple]]:
    """The person page's statements over the messages: the recent conversations (to/cc) and the
    open threads."""
    return [(s, p) for s, p in zip(m.statements, m.params, strict=True)
            if s.lstrip().upper().startswith(("SELECT", "WITH")) and ('"to" @>' in s or "JOIN LATERAL" in s)]


async def test_person_page(scale):
    """GET /people/{email} for a typical sender, a colleague who is only in to and cc, and a
    notification sender with 900 conversations: a fixed number of statements, and the to/cc
    containment and the open threads reach the messages through their indexes (GIN
    jsonb_path_ops on "to" and cc, ix_msg_from, or the conversation order for a frequent
    correspondent): each lookup reads at most a tenth of the message and conversation rows, never
    the whole table [measured 4 Oct 2026 at 45k conversations, 90k messages: 7 statements; 4.2,
    3.0 and 4.1 ms; before: 76, 21 and 24 ms, a sequential scan of the messages for the to/cc
    test. 13 Oct 2026 at 10k conversations: at most 455 conversation and 912 message rows read]."""
    from sqlalchemy import text

    from mailr.db import sessionmaker

    built, c = scale
    async with sessionmaker()() as db:
        for email in PERSON_PAGES.values():
            await db.execute(text(
                "INSERT INTO people (user_id, email, name, organization, received_count, sent_count, notes, blocked) "
                "VALUES (:u, :e, '', '', 1, 0, '', false) ON CONFLICT DO NOTHING"), {"u": built.user_id, "e": email})
        await db.commit()
    tables = {t: await _table(t) for t in ("messages", "conversations")}
    for name, email in PERSON_PAGES.items():
        body, m = await _get(c, name, f"/api/v1/people/{email}")
        assert body["recent_conversations"], name
        budget(m.sql <= BUDGET_PERSON, m.statements)
        lookups = _messages_lookups(m)
        assert len(lookups) == 2, (name, m.statements)
        for stmt, params in lookups:
            # A rare correspondent's messages come from the from/to/cc indexes; a frequent one's
            # page walks the conversations newest first and stops at ten. Either way the lookup
            # reads a small part of each table, whichever indexes the planner takes.
            nodes, _buffers = await _analyze(stmt, params)
            kinds = [(n["Node Type"], n.get("Relation Name"), n.get("Index Name")) for n in nodes]
            for table, (_pages, rows) in tables.items():
                budget(_rows_read(nodes, table) <= rows // 10, (name, table, _rows_read(nodes, table), kinds))
    # Open threads: the newest ten of the notification sender's 900 unanswered conversations; none
    # for the colleague, who never writes first; the typical sender's are all open (archive mail).
    alerts = (await c.get(f"/api/v1/people/{sf.ALERTS}")).json()
    assert len(alerts["open_threads"]) == 10
    assert (await c.get(f"/api/v1/people/{sf.PERSON}")).json()["open_threads"] == []
    assert len((await c.get("/api/v1/people/sender45@domain5.com")).json()["open_threads"]) == 10


async def test_periodic_scans_have_indexes(scale):
    """The per-minute snooze wake-up reads the partial index of the snoozed conversations,
    and the daily pruning of the change and audit logs reads their created_at indexes. The plans
    are made with sequential scans off: what must hold is that each scan is indexable (the logs
    are nearly empty here, so the planner's own choice proves nothing)."""
    expect = {
        "SELECT id, user_id, snoozed_until FROM conversations WHERE snoozed_until < now() - interval '2 minutes'":
            "ix_conv_snoozed",
        "DELETE FROM change_log WHERE created_at < now() - interval '60 days'": "ix_change_log_created",
        "DELETE FROM audit_log WHERE created_at < now() - interval '365 days'": "ix_audit_log_created",
        # A sender compared in lower case (trusted senders, contact groups for tokens, deadlines).
        "SELECT id FROM messages WHERE user_id = 1 AND lower(from_email) = 'dana@northwind.example'":
            "ix_msg_from_lower",
    }
    for stmt, index in expect.items():
        kinds = [(n["Node Type"], n.get("Relation Name"), n.get("Index Name"))
                 for n in await _explain(stmt, (), no_seqscan=True)]
        assert any(i == index for _k, _r, i in kinds), (stmt, kinds)
        assert not any(k == "Seq Scan" for k, _r, _i in kinds), (stmt, kinds)


# A quoted subject phrase that one conversation has (8 Oct 2026: `subject:"The Perfect Dive Watch"`
# with view=all and limit=5 took more than 60 s on the production mailbox).
PHRASE = "The Perfect Dive Watch"


async def test_subject_phrase_search(scale):
    """A quoted subject phrase finds its one conversation in well under a second, also after the
    statement has run often enough for a generic plan. Measured 8 Oct 2026 at 100k messages
    (MAILR_SCALE_CONVERSATIONS=50000): 8.6 ms cold, 4.9 ms warm, the custom and the generic plan
    both a bitmap scan of ix_msg_subject_trgm. The production plan (EXPLAIN, no ANALYZE) has the
    same shape, so the 60 s did not come from this query's plan."""
    from sqlalchemy import text as sqltext

    from mailr.db import sessionmaker

    built, c = scale
    async with sessionmaker()() as db:
        cid = (await db.execute(sqltext(
            "SELECT id FROM conversations WHERE user_id=:u AND label_ids @> ARRAY[:a]::int[] ORDER BY id LIMIT 1 OFFSET 7"),
            {"u": built.user_id, "a": built.labels["archive"]})).scalar_one()
        await db.execute(sqltext("UPDATE messages SET subject=:s WHERE conversation_id=:c"), {"s": f"{PHRASE}?", "c": cid})
        await db.execute(sqltext("UPDATE conversations SET subject=:s WHERE id=:c"), {"s": f"{PHRASE}?", "c": cid})
        await db.commit()
    body, m = await _get(c, "search subject phrase", "/api/v1/conversations", view="all",
                         q=f'subject:"{PHRASE}"', limit=5)
    assert [i["id"] for i in body["items"]] == [cid]
    budget(m.sql <= BUDGET_SEARCH, m.statements)
    budget(MEASURED["search subject phrase (cold)"].seconds < 1.0, MEASURED["search subject phrase (cold)"].durations)
    # asyncpg prepares each statement, and Postgres may switch a prepared statement to a generic
    # plan after its fifth run: later searches of the same shape must stay fast too.
    for k in range(8):
        with sf.counting() as again:
            r = await c.get("/api/v1/conversations", params={"view": "all", "q": f'subject:"{PHRASE} {k}"', "limit": 5})
        assert r.status_code == 200, r.text
        budget(again.seconds < 1.0, (k, again.durations))


async def test_attachments_pages(scale):
    """GET /attachments, newest first: page 1, a page deep in by cursor and PDFs only. The plan
    walks the partial index of the messages with attachments in date order and stops at the
    page (it sorted every such message on every page before)."""
    from sqlalchemy import text as sqltext

    from mailr.db import sessionmaker

    built, c = scale
    p1, m1 = await _get(c, "attachments page 1", "/api/v1/attachments", limit=60)
    assert len(p1["items"]) == 60 and p1["next_cursor"]
    cur, pages = p1["next_cursor"], 1
    for _ in range(20):
        r = await c.get("/api/v1/attachments", params={"limit": 60, "cursor": cur})
        cur, pages = r.json()["next_cursor"], pages + 1
    deep, md = await _get(c, "attachments page 22", "/api/v1/attachments", limit=60, cursor=cur)
    assert len(deep["items"]) == 60
    pdf, mp = await _get(c, "attachments pdf", "/api/v1/attachments", limit=60, kind="pdf")
    assert pdf["items"] and {i["type"] for i in pdf["items"]} == {"application/pdf"}
    for m in (m1, md, mp):
        budget(m.seconds < 0.5, m.durations)
    async with sessionmaker()() as db:
        n = (await db.execute(sqltext("SELECT count(*) FROM messages WHERE user_id=:u AND has_attachments"),
                              {"u": built.user_id})).scalar_one()
    stmt = next(s for s in m1.statements if "jsonb_array_elements" in s)
    params = next(p for s, p in zip(m1.statements, m1.params, strict=True) if s == stmt)
    nodes, _bufs = await _analyze(stmt, params)
    budget(_rows_read(nodes, "messages") < max(200, n // 10), (n, [(x["Node Type"], x.get("Index Name")) for x in nodes]))
