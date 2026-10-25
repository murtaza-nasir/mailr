"""Embeddings of tables for the assistant's semantic search.
A table's text is its name, description, column names and the text of its rows (text cells,
option names and kept-as-text values), cut at 8,000 characters. Tables of at least MIN_CHARS
characters are embedded in batches by the worker, never on the request path. A local-only table is
embedded only by a local endpoint, exactly as a local-only note.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import outbound
from mailr.core.jobs import enqueue
from mailr.tables.models import DataTable, TableColumn, TableRow

MIN_CHARS = 300
BATCH = 32
MAX_CHARS = 8000


async def text_of(db: AsyncSession, t: DataTable) -> str:
    cols = (await db.execute(select(TableColumn.name).where(TableColumn.table_id == t.id,
                                                             TableColumn.deleted_at.is_(None))
                             .order_by(TableColumn.order))).scalars().all()
    parts = [t.name or "", t.description or "", " · ".join(cols)]
    size = sum(len(p) for p in parts)
    for (txt,) in (await db.execute(select(TableRow.search_text).where(TableRow.table_id == t.id,
                                                                        TableRow.deleted_at.is_(None))
                                    .order_by(TableRow.order).limit(2000))).all():
        if size >= MAX_CHARS:
            break
        parts.append(txt)
        size += len(txt) + 1
    return "\n".join(p for p in parts if p).strip()[:MAX_CHARS]


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def queue(db: AsyncSession, owner_id: int | None, group: str | None = None) -> None:
    """Queue the embedding of an owner's changed tables: a user's, or a group's (a group-owned
    table has no owning user)."""
    if owner_id:
        await enqueue(db, "tables.embed", {"user_id": owner_id}, user_id=owner_id,
                      run_at=datetime.now(UTC) + timedelta(seconds=60), dedupe_key=f"tables.embed:{owner_id}")
    elif group:
        await enqueue(db, "tables.embed", {"group": group},
                      run_at=datetime.now(UTC) + timedelta(seconds=60), dedupe_key=f"tables.embed:g:{group}"[:200])


async def embed_pending(db: AsyncSession, user_id: int | None, *, group: str | None = None) -> int:
    """Embed this user's tables whose text changed since their last embedding."""
    from mailr.intel import embed as emb
    from mailr.intel import llm, locality

    if user_id is None and not group:
        return 0
    # A group-owned table is embedded with the group's job, under the same local-only rules.
    owner = DataTable.owner_user_id == user_id if user_id is not None else DataTable.owner_group == group
    done = 0
    # A group's content follows any member's "Local models only for me" (intel/locality.group_strict).
    group_strict = user_id is None and await locality.group_strict(db, group)
    for local_only in (False, True):
        cur = await emb.current(db, sensitive=local_only or group_strict, user_id=user_id)
        if cur is None:
            continue
        r, key = cur
        if not outbound.allowed(local_only, r.endpoint):
            continue
        rows = list((await db.execute(select(DataTable).where(
            owner, DataTable.deleted_at.is_(None),
            DataTable.local_only.is_(local_only)).limit(500))).scalars())
        todo: list[tuple[DataTable, str, str]] = []
        for t in rows:
            text = await text_of(db, t)
            h = digest(text)
            if t.embed_hash == h and t.embed_model == key:
                continue
            if len(text) < MIN_CHARS:
                t.embedding, t.embed_model, t.embed_hash = None, key, h
                continue
            todo.append((t, text, h))
        for i in range(0, len(todo), BATCH):
            part = todo[i:i + BATCH]
            try:
                vecs = await emb.embed_texts(r, [x for _, x, _ in part])
            except llm.EmbedUnavailable:
                return done
            for (t, _, h), v in zip(part, vecs, strict=True):
                t.embedding, t.embed_model, t.embed_hash = v, key, h
                done += 1
        await db.flush()
    return done
