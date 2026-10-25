"""Embeddings of long notes, for the assistant's semantic search.
Notes of at least MIN_CHARS characters (title, body or items) are embedded in batches
by the worker, never on the request path. A local-only note is embedded only by a local endpoint.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import outbound
from mailr.core.jobs import enqueue
from mailr.notes.models import Note, NoteItem

MIN_CHARS = 300
BATCH = 64


async def text_of(db: AsyncSession, note: Note) -> str:
    if note.kind == "checklist":
        rows = (await db.execute(select(NoteItem.text).where(NoteItem.note_id == note.id,
                                                              NoteItem.deleted_at.is_(None)).order_by(NoteItem.order)
                                 )).scalars().all()
        return "\n".join([note.title or "", *rows]).strip()
    return f"{note.title or ''}\n{note.body or ''}".strip()


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def queue(db: AsyncSession, owner_id: int | None, group: str | None = None) -> None:
    """Queue the embedding of an owner's changed notes: a user's, or a group's (a group-owned
    note has no owning user)."""
    if owner_id:
        await enqueue(db, "notes.embed", {"user_id": owner_id}, user_id=owner_id,
                      run_at=datetime.now(UTC) + timedelta(seconds=30), dedupe_key=f"notes.embed:{owner_id}")
    elif group:
        await enqueue(db, "notes.embed", {"group": group},
                      run_at=datetime.now(UTC) + timedelta(seconds=30), dedupe_key=f"notes.embed:g:{group}"[:200])


async def embed_pending(db: AsyncSession, user_id: int | None, *, group: str | None = None) -> int:
    """Embed this user's notes whose text changed since their last embedding."""
    from mailr.intel import embed as emb
    from mailr.intel import llm, locality

    if user_id is None and not group:
        return 0
    # A group-owned note is embedded with the group's job, under the same local-only rules.
    owner = Note.owner_user_id == user_id if user_id is not None else Note.owner_group == group
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
        rows = list((await db.execute(select(Note).where(
            owner, Note.deleted_at.is_(None), Note.local_only.is_(local_only),
            or_(Note.embed_hash.is_(None), Note.embed_model.is_(None), Note.embed_model != key,
                Note.updated_at > Note.created_at)).limit(500))).scalars())
        todo: list[tuple[Note, str, str]] = []
        for n in rows:
            text = await text_of(db, n)
            h = digest(text)
            if n.embed_hash == h and n.embed_model == key:
                continue
            if len(text) < MIN_CHARS:
                n.embedding, n.embed_model, n.embed_hash = None, key, h
                continue
            todo.append((n, text, h))
        for i in range(0, len(todo), BATCH):
            part = todo[i:i + BATCH]
            try:
                vecs = await emb.embed_texts(r, [t[:8000] for _, t, _ in part])
            except llm.EmbedUnavailable:
                return done
            for (n, _, h), v in zip(part, vecs, strict=True):
                n.embedding, n.embed_model, n.embed_hash = v, key, h
                done += 1
        await db.flush()
    return done
