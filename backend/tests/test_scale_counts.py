"""GET /counts at scale with "Show each account separately" on (8 Oct 2026).

Production jammed: with five accounts and about 20k conversations each /counts took about two
minutes. Every view of every account carried the account sub-queries per conversation, and each
tab and the phone asked at once. The hotfix (one computation per user at a time, the per-account
numbers off) stays; the numbers are rebuilt on a per-conversation `account_ids` column
(core/mailaccounts.py) and one grouped query per kind of number.

The mailbox: COUNTS_CONVERSATIONS conversations (default 30,000; 100,000 for the measurement in
the report: MAILR_COUNTS_CONVERSATIONS=100000) of two messages each, five mail accounts with one
send-as identity each, and 30 user labels (five with labels nested under them). Budget: the whole
/counts call, computed afresh, in COUNTS_BUDGET_MS. MAILR_SCALE_REPORT=1 measures without asserting,
MAILR_SCALE_HIST=1 prints the slowest statements.

Measured 8 Oct 2026 (32 cores, other agents running): before, at 20,000 conversations, 10.7 s with
the per-account numbers on (110 ms with them off, the hotfix); after, 41 ms at 20,000 and 90 ms at
100,000, with the per-account numbers on. Rebuilding the index for 100,000 conversations: 1.3–2.4 s.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

pytestmark = [pytest.mark.scale, pytest.mark.serial]

COUNTS_CONVERSATIONS = int(os.environ.get("MAILR_COUNTS_CONVERSATIONS") or 30_000)
ACCOUNTS = 5
LABELS = 30
COUNTS_BUDGET_MS = 200.0
REPORT_ONLY = os.environ.get("MAILR_SCALE_REPORT") == "1"


async def build(db, user_id: int) -> dict:
    t0 = time.perf_counter()
    roles: dict[str, int] = {}
    for role, name in (("inbox", "Inbox"), ("sent", "Sent"), ("drafts", "Drafts"), ("junk", "Spam"),
                       ("trash", "Trash"), ("archive", "Archive")):
        lid = (await db.execute(text("SELECT id FROM labels WHERE user_id=:u AND role=:r"),
                                {"u": user_id, "r": role})).scalar()
        if lid is None:
            lid = (await db.execute(text(
                "INSERT INTO labels (user_id, jmap_id, name, role, show_in_list, show_in_message_list, sort, "
                "created_at) VALUES (:u, :j, :n, :r, 'show', true, 0, now()) RETURNING id"),
                {"u": user_id, "j": f"mb-{role}", "n": name, "r": role})).scalar_one()
        roles[role] = lid
    labels = []
    for i in range(LABELS):
        # Five parents with five children each, and five on their own.
        name = f"Area {i // 6}/Topic {i}" if i % 6 else f"Area {i // 6}"
        labels.append((await db.execute(text(
            "INSERT INTO labels (user_id, jmap_id, name, show_in_list, show_in_message_list, sort, created_at) "
            "VALUES (:u, :j, :n, 'show', true, 0, now()) RETURNING id"),
            {"u": user_id, "j": f"mb-user-{i}", "n": name})).scalar_one())
    accounts = []
    for i in range(ACCOUNTS):
        cid = (await db.execute(text(
            "INSERT INTO connectors (user_id, kind, name, mode, data_class, config, state, retention_days, "
            "delete_upstream, enabled, builtin, managed_by_admin, status, status_detail) VALUES "
            "(:u, 'imap', :n, 'mirror', 'normal', CAST(:cfg AS jsonb), '{}'::jsonb, 14, false, false, false, false, "
            "'idle', '') RETURNING id"),
            {"u": user_id, "n": f"Account {i}", "cfg": f'{{"email": "acct{i}@example.org"}}'})).scalar_one()
        await db.execute(text(
            "INSERT INTO identities (user_id, connector_id, email, name, reply_to, signature_html, is_default, receives) "
            "VALUES (:u, :c, :e, 'Me', '', '', false, '{}')"), {"u": user_id, "c": cid, "e": f"acct{i}@example.org"})
        accounts.append(cid)
    p = {"u": user_id, "n": COUNTS_CONVERSATIONS, "inbox": roles["inbox"], "archive": roles["archive"],
         "sent": roles["sent"], "trash": roles["trash"], "junk": roles["junk"], "drafts": roles["drafts"],
         "ul": labels, "acc": accounts, "nl": LABELS, "na": ACCOUNTS}
    # g % 10: 0-2 Inbox, 3 Sent, 4 Trash, 5 Spam, the rest Archive. Half carry one user label, a
    # tenth a second one; every 40th has a draft. The second message of every 7th conversation
    # came through another account, so a tenth or so of the threads are in two accounts.
    await db.execute(text("""
        INSERT INTO conversations (user_id, subject, norm_subject, snippet, last_date, first_date,
            message_count, unread_count, draft_count, has_attachments, starred, important, importance_score,
            category, label_ids, participants, muted, pinned, updated_at)
        SELECT :u, 'Subject ' || g, 'subject ' || g, 'Snippet', now() - g * interval '20 minutes',
               now() - g * interval '20 minutes' - interval '1 hour', 2, (g % 3 = 0)::int, (g % 40 = 0)::int,
               false, g % 50 = 0, g % 7 = 0, NULL,
               CASE WHEN g % 10 < 3 THEN (ARRAY[NULL,'updates','promotions','social'])[1 + g % 4] END,
               (CASE WHEN g % 10 < 3 THEN ARRAY[:inbox]::int[] WHEN g % 10 = 3 THEN ARRAY[:sent]::int[]
                     WHEN g % 10 = 4 THEN ARRAY[:trash]::int[] WHEN g % 10 = 5 THEN ARRAY[:junk]::int[]
                     ELSE ARRAY[:archive]::int[] END)
               || (CASE WHEN g % 2 = 0 THEN ARRAY[(:ul ::int[])[1 + (g / 2) % :nl]] ELSE ARRAY[]::int[] END)
               || (CASE WHEN g % 10 = 7 THEN ARRAY[(:ul ::int[])[1 + (g / 3) % :nl]] ELSE ARRAY[]::int[] END)
               || (CASE WHEN g % 40 = 0 THEN ARRAY[:drafts]::int[] ELSE ARRAY[]::int[] END),
               '[]'::jsonb, false, false, now()
        FROM generate_series(1, :n) g
    """), p)
    await db.execute(text("""
        INSERT INTO messages (user_id, conversation_id, connector_id, jmap_id, jmap_thread_id, blob_id, upstream,
            message_id_hdr, "references", subject, norm_subject, from_email, from_name, "to", cc, bcc,
            reply_to, delivered_to, date, received_at, size, snippet, body_text, has_attachments,
            attachments, auth_results, seen, flagged, answered, draft, important, label_ids, deleted,
            category, sent_by_me, local_only, intel_state, created_at, updated_at, tsv)
        SELECT c.user_id, c.id,
               CASE WHEN k = 2 AND c.id % 7 = 0 THEN (:acc ::int[])[1 + (c.id + 1) % :na]
                    ELSE (:acc ::int[])[1 + c.id % :na] END,
               'e' || c.id || '-' || k, 't' || c.id, 'b' || c.id || '-' || k, '{}'::jsonb,
               '<' || c.id || '.' || k || '@counts.test>', ARRAY[]::varchar[], c.subject, c.norm_subject,
               CASE WHEN c.label_ids @> ARRAY[:sent]::int[] AND k = 2 THEN 'acct' || (c.id % :na) || '@example.org'
                    ELSE 'sender' || (c.id % 300) || '@domain.example' END,
               'Sender', '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, ARRAY[]::varchar[],
               c.last_date - (2 - k) * interval '5 minutes', c.last_date - (2 - k) * interval '5 minutes',
               4000, 'Snippet', 'Body', false, '[]'::jsonb, '{}'::jsonb,
               NOT (c.unread_count > 0 AND k = 2), false, false, c.draft_count > 0 AND k = 2, c.important,
               c.label_ids, false, c.category, c.label_ids @> ARRAY[:sent]::int[] AND k = 2, false, 'done', now(), now(),
               to_tsvector('simple', c.subject)
        FROM conversations c, generate_series(1, 2) k
        WHERE c.user_id = :u
    """), p)
    await db.commit()
    from mailr.db import engine

    async with engine().connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("VACUUM ANALYZE conversations"))
        await conn.execute(text("VACUUM ANALYZE messages"))
    return {"accounts": accounts, "labels": labels, "roles": roles, "seconds": time.perf_counter() - t0}


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def mailbox():
    from mailr.core.users import bootstrap, create_user
    from mailr.db import sessionmaker
    from mailr.main import create_app
    from mailr.models import User

    await bootstrap()
    app = create_app()
    async with sessionmaker()() as db:
        u = await create_user(db, f"counts-{uuid.uuid4().hex[:8]}@example.com", "Counts", password="counts-password-123")
        built = await build(db, u.id)
        user = await db.get(User, u.id)
        user.settings = {**(user.settings or {}), "account_folders": True}
        await db.commit()
        uid = u.id
    print(f"\ncounts fixture: {COUNTS_CONVERSATIONS} conversations, {ACCOUNTS} accounts, {LABELS} labels "
          f"in {built['seconds']:.1f}s")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/login", json={"email": u.email, "password": "counts-password-123"})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        yield {**built, "user_id": uid}, c
    # Leave the database as it was for any later test (the rows and the planner's statistics). The
    # plan-shape tests of test_scale_budgets.py run before this module (file order).
    from mailr.db import engine

    async with sessionmaker()() as db:
        await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
        await db.commit()
    async with engine().connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("VACUUM ANALYZE conversations"))
        await conn.execute(text("VACUUM ANALYZE messages"))


def _forget():
    from mailr.core import sidebar

    sidebar._CACHE.clear()


async def _timed(c) -> tuple[float, dict]:
    _forget()
    t0 = time.perf_counter()
    r = await c.get("/api/v1/counts")
    ms = (time.perf_counter() - t0) * 1000
    assert r.status_code == 200, r.text
    return ms, r.json()


async def test_counts_with_account_folders_are_fast(mailbox):
    built, c = mailbox
    from mailr.core import mailaccounts
    from mailr.db import sessionmaker

    # The account index, as the job `mail.account_index` builds it after the upgrade.
    t0 = time.perf_counter()
    async with sessionmaker()() as db:
        await mailaccounts.rebuild_index(db, built["user_id"])
        await db.commit()
    print(f"  account index rebuilt in {time.perf_counter() - t0:.2f}s")
    await _timed(c)
    runs = sorted([(await _timed(c))[0] for _ in range(5)])
    from tests import scale_fixture as sf

    with sf.counting() as m:
        ms, body = await _timed(c)
    views = body["views"]
    if os.environ.get("MAILR_SCALE_HIST"):
        for d, st in sorted(m.durations, reverse=True)[:6]:
            print(f"      {d * 1000:7.1f} ms  {' '.join(st.split())[:200]}")
    print(f"  /counts (fresh, median of 5): {runs[2]:.1f} ms (min {runs[0]:.1f}, max {runs[-1]:.1f})")
    a = built["accounts"][0]
    assert f"acct:{a}:inbox" in views and f"acct:{a}:spam" in views
    assert any(k.startswith(f"acct:{a}:label:") for k in views)
    if not REPORT_ONLY:
        assert runs[2] < COUNTS_BUDGET_MS, runs


async def test_thirty_parallel_counts_compute_once(mailbox, monkeypatch):
    """Every tab and the phone asking at once share one computation (the hotfix lock)."""
    from mailr.core import sidebar

    _built, c = mailbox
    _forget()
    calls = 0
    real = sidebar.compute

    async def counting(*a, **kw):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)  # all thirty are waiting before the first finishes
        return await real(*a, **kw)

    monkeypatch.setattr(sidebar, "compute", counting)
    answers = await asyncio.gather(*[c.get("/api/v1/counts") for _ in range(30)])
    assert all(r.status_code == 200 for r in answers)
    assert calls == 1
    assert len({r.text for r in answers}) == 1
