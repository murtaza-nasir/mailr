"""Shared helpers, from test_audit_fixes.py."""

import uuid

from httpx import AsyncClient

from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.store import user_store
from mailr.db import session_scope, sessionmaker
from mailr.models import Connector, User


async def _conn(user_id, data_class="normal"):
    async with session_scope() as db:
        c = Connector(user_id=user_id, kind="imap", name=f"acct-{uuid.uuid4().hex[:4]}", mode="mirror",
                      data_class=data_class, config={}, state={}, enabled=False)
        db.add(c)
        await db.flush()
        return c.id


async def _ingest(user, data, connector_id=None, **kw):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        conn = await db.get(Connector, connector_id) if connector_id else None
        res = await ingest_raw(db, await user_store(db, u), data, IngestOptions(connector=conn, **kw))
        await db.commit()
        return res.message.id, res.message.conversation_id, res.duplicate


async def _token(client, scopes, local_only=False):
    r = await client.post("/api/v1/tokens", json={"name": "t", "scopes": scopes, "local_only": local_only})
    a = AsyncClient(transport=client._transport, base_url="http://test")
    a.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return a
