"""Background work for tables: the embedding job and the nightly snapshots and cleanup
(tables/history.nightly)."""

from __future__ import annotations

import logging

from mailr.core.jobs import handler

log = logging.getLogger("mailr.tables")


@handler("tables.embed")
async def embed_job(payload: dict, ctx) -> None:
    from mailr.db import session_scope
    from mailr.tables.embed import embed_pending

    async with session_scope() as db:
        uid = payload.get("user_id")
        await embed_pending(db, int(uid) if uid is not None else None, group=payload.get("group"))


async def nightly() -> dict[str, int]:
    """Snapshots of tables changed since their last one, then the 30-day cleanup."""
    from mailr.db import session_scope
    from mailr.tables.history import nightly as run

    async with session_scope() as db:
        out = await run(db)
    log.info("tables nightly: %s", out)
    return out
