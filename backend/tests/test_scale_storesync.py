"""The store sync's pending ids at scale: a lost cursor on a big mailbox.

PENDING ids (default 300,000; MAILR_SCALE_PENDING sets it) wait to be indexed. One batch of a tick
reads INDEX_PER_TICK due ids and removes them (core/storesync.Pending), and must stay well under
BATCH_BUDGET_MS. For the before number, the same ids are written as the one JSON document they used
to be, which every tick read and wrote whole. MAILR_SCALE_REPORT=1 measures without asserting.
"""

from __future__ import annotations

import json
import os
import time
import uuid

import pytest
from sqlalchemy import text

from mailr.core import storesync

pytestmark = [pytest.mark.scale, pytest.mark.serial]

PENDING = int(os.environ.get("MAILR_SCALE_PENDING") or 300_000)
BATCH_BUDGET_MS = 100.0
REPORT_ONLY = os.environ.get("MAILR_SCALE_REPORT") == "1"


async def test_a_batch_of_pending_ids_costs_the_same_however_many_wait(app):
    from mailr.core.users import create_user
    from mailr.db import session_scope
    from mailr.models import StoreSyncState

    async with session_scope() as db:
        u = await create_user(db, f"scale-sync-{uuid.uuid4().hex[:8]}@example.com", "Scale", password="scale-password-123")
        uid = u.id
        db.add(StoreSyncState(user_id=uid))
        await db.flush()
        await db.execute(text(
            "INSERT INTO store_sync_pending (user_id, jmap_id, next_at) "
            "SELECT :u, 'J' || g, extract(epoch FROM now()) - 3600 + g * 0.001 FROM generate_series(1, :n) g"),
            {"u": uid, "n": PENDING})
    async with session_scope() as db:
        await db.execute(text("ANALYZE store_sync_pending"))
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        async with session_scope() as db:
            p = await storesync.Pending.due(db, uid, time.time(), storesync.INDEX_PER_TICK)
            ids = p.ids()
            for j in ids:
                p.pop(j)
            await p.save(db, uid)
        times.append((time.perf_counter() - t0) * 1000)
        assert len(ids) == storesync.INDEX_PER_TICK
    # Before: the whole set as one JSON document, read and written back on every tick.
    doc = {f"J{g}": time.time() - 3600 for g in range(PENDING)}
    async with session_scope() as db:
        await db.execute(text("CREATE TEMP TABLE old_pending (doc jsonb) ON COMMIT DROP"))
        await db.execute(text("INSERT INTO old_pending VALUES (CAST(:d AS jsonb))"), {"d": json.dumps(doc)})
        t0 = time.perf_counter()
        got = (await db.execute(text("SELECT doc FROM old_pending"))).scalar_one()
        for j in list(got)[:storesync.INDEX_PER_TICK]:
            got.pop(j)
        await db.execute(text("UPDATE old_pending SET doc = CAST(:d AS jsonb)"), {"d": json.dumps(got)})
        before = (time.perf_counter() - t0) * 1000
    print(f"\n{PENDING} pending ids: a batch of {storesync.INDEX_PER_TICK} takes "
          f"{min(times):.1f}-{max(times):.1f} ms (the JSON document: {before:.0f} ms per tick)")
    async with session_scope() as db:
        await db.execute(text("DELETE FROM store_sync_state WHERE user_id = :u"), {"u": uid})
    if not REPORT_ONLY:
        assert max(times) < BATCH_BUDGET_MS, times
