"""Triage of imported mail and passage lookups at production scale.

The scale mailbox (tests/scale_fixture.py) gets one passage per message, and half of its mail is
marked as imported mail still to triage (intel_state 'backfill'). In one transaction that is
rolled back, each statement runs under EXPLAIN (ANALYZE, BUFFERS) twice: with the indexes for it
(ix_msg_intel_todo, ix_passage_message) and, for the before numbers, with them dropped.

- the backfill's pick of its next batch (intel/tasks.py backfill_todo), newest first;
- a message's passages by its id (passages.build_for_message, update_message_vectors);
- the messages without passages (passages.messages_without_passages), the embedding catch-up.

`pytest -m scale -s` prints the numbers; MAILR_SCALE_REPORT=1 measures without asserting.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.dialects import postgresql

from tests import scale_fixture as sf

pytestmark = [pytest.mark.scale, pytest.mark.serial]

REPORT_ONLY = os.environ.get("MAILR_SCALE_REPORT") == "1"
# Shared buffers each statement may touch with its index. The messages without passages are an
# anti-join over the whole mailbox either way: the catch-up now runs it only until it finds none.
BUDGET_BUFFERS = {"backfill batch": 2000, "passages of a message": 20}


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def mailbox():
    from mailr.core.users import bootstrap, create_user
    from mailr.db import sessionmaker

    await bootstrap()
    async with sessionmaker()() as db:
        u = await create_user(db, f"scale-intel-{uuid.uuid4().hex[:8]}@example.com", "Scale", password="scale-password-123")
        built = await sf.build(db, u.id)
    print(f"\nscale fixture: {built.conversations} conversations, {built.messages} messages in {built.seconds:.2f}s")
    return built


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


async def _measure(raw, sql: str) -> tuple[float, int]:
    plan = await raw.fetchval("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql)
    plan = (json.loads(plan) if isinstance(plan, str) else plan)[0]
    top = plan["Plan"]
    return plan["Execution Time"], top.get("Shared Hit Blocks", 0) + top.get("Shared Read Blocks", 0)


async def test_backfill_and_passage_lookups(mailbox):
    from sqlalchemy import select

    from mailr.db import engine
    from mailr.intel import tasks as intel_tasks
    from mailr.models import Message, MessagePassage

    uid = mailbox.user_id
    async with engine().connect() as conn:
        raw = (await conn.get_raw_connection()).driver_connection
        tr = raw.transaction()
        await tr.start()
        try:
            await raw.execute(
                "INSERT INTO message_passages (user_id, message_id, conversation_id, idx, start, \"end\", text, "
                "content_hash) SELECT user_id, id, conversation_id, 0, 0, 0, '', md5(id::text) "
                "FROM messages WHERE user_id = $1 AND id % 10 <> 3", uid)
            await raw.execute("UPDATE messages SET intel_state = 'backfill' WHERE user_id = $1 AND id % 2 = 0", uid)
            await raw.execute("ANALYZE messages")
            await raw.execute("ANALYZE message_passages")
            probe = await raw.fetchval("SELECT id FROM messages WHERE user_id = $1 ORDER BY id LIMIT 1 OFFSET 1000", uid)
            stmts = {
                "backfill batch": _sql(intel_tasks.backfill_todo(uid).order_by(Message.date.desc().nulls_last())
                                       .limit(intel_tasks.BACKFILL_BATCH)),
                "passages of a message": _sql(select(MessagePassage.id).where(MessagePassage.message_id == probe)),
                "messages without passages": _sql(select(Message.id).where(
                    Message.user_id == uid, Message.deleted.is_(False), Message.draft.is_(False),
                    ~select(MessagePassage.id).where(MessagePassage.message_id == Message.id,
                                                     MessagePassage.user_id == uid).exists())
                    .order_by(Message.date.desc().nulls_last()).limit(500)),
            }
            after = {k: await _measure(raw, s) for k, s in stmts.items()}
            await raw.execute("DROP INDEX ix_msg_intel_todo")
            await raw.execute("DROP INDEX ix_passage_message")
            # Before: the batch was every pending message of the user, sorted by date each time.
            stmts["backfill batch"] = (
                f"SELECT * FROM messages WHERE user_id = {uid} AND intel_state = 'backfill' AND deleted IS false "
                f"ORDER BY date DESC NULLS LAST LIMIT {intel_tasks.BACKFILL_BATCH}")
            before = {k: await _measure(raw, s) for k, s in stmts.items()}
        finally:
            await tr.rollback()
    print()
    for k in stmts:
        print(f"  {k:<28} before {before[k][0]:9.2f} ms {before[k][1]:7d} buffers   "
              f"after {after[k][0]:9.2f} ms {after[k][1]:7d} buffers")
    if not REPORT_ONLY:
        for k, limit in BUDGET_BUFFERS.items():
            assert after[k][1] <= limit, (k, after[k])
