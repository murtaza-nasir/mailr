"""Shared helpers, from test_core_flow.py."""

from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.store import user_store
from mailr.db import sessionmaker


async def _ingest(user, data, **kw):
    async with sessionmaker()() as db:
        from mailr.models import User
        u = await db.get(User, user.id)
        store = await user_store(db, u)
        res = await ingest_raw(db, store, data, IngestOptions(**kw))
        await db.commit()
        return res.message.id, res.message.conversation_id
