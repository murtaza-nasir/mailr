"""Embeddings through the instance's embedding endpoint (OpenAI-compatible `/embeddings`).

The server never runs a model itself. `intel/llm.resolve_embed` picks the one endpoint every
vector comes from and applies the local-only guard; with no endpoint configured, semantic
features are off and search is keyword-only.

Cost control:
- batches of BATCH texts per request, at most CONCURRENCY requests in flight;
- identical passage text (signatures, newsletter boilerplate) is embedded once: vectors are
  reused by content hash + model;
- a circuit breaker: after a failure the endpoint is left alone for BREAKER seconds, and
  callers leave passages pending for the catch-up job instead of retrying in a loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import numpy as np
from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from mailr.core import outbound
from mailr.intel import llm
from mailr.models import EMBED_DIM, Connector, Message, MessagePassage, light_message

log = logging.getLogger("mailr.embed")

BATCH = 64
CONCURRENCY = 2
BREAKER = 300.0

_down_until = 0.0
_sem: asyncio.Semaphore | None = None

# Query instructions: retrieval-tuned embedding models expect queries (not documents) to be
# prefixed. Matched on the model name; unknown models get the raw query.
_QUERY_PREFIX = (
    ("qwen3-embedding", "Instruct: Given a question about the user's email, retrieve passages that answer it\nQuery: "),
    ("bge", "Represent this sentence for searching relevant passages: "),
    ("nomic", "search_query: "),
    ("e5", "query: "),
)


def query_prefix(model: str) -> str:
    low = model.lower()
    return next((p for k, p in _QUERY_PREFIX if k in low), "")


def available_now() -> bool:
    return time.monotonic() >= _down_until


def reset_breaker() -> None:
    global _down_until
    _down_until = 0.0


async def current(db: AsyncSession, *, sensitive: bool, user_id: int | None = None,
                  count: bool = False) -> tuple[llm.Resolved, str] | None:
    """The embedding endpoint and model key. `user_id`: whose content it is, so their "Local models
    only for me" counts (intel/locality.py); `count`: an embedding happens now (Model use)."""
    r = await llm.resolve_embed(db, sensitive=sensitive, user_id=user_id, count=count)
    return (r, llm.embed_model_key(r, EMBED_DIM)) if r else None


async def embed_texts(r: llm.Resolved, texts: list[str]) -> list[list[float]]:
    """Embed with batching and bounded concurrency. Raises llm.EmbedUnavailable (and trips
    the breaker) if the endpoint fails after retries."""
    global _down_until, _sem
    if not texts:
        return []
    if not available_now():
        raise llm.EmbedUnavailable("embedding endpoint recently failed; waiting")
    if _sem is None:
        _sem = asyncio.Semaphore(CONCURRENCY)
    sem = _sem

    async def one(chunk: list[str]) -> list[list[float]]:
        async with sem:
            return await llm.embed_request(r, chunk, EMBED_DIM)

    try:
        parts = await asyncio.gather(*(one(texts[i:i + BATCH]) for i in range(0, len(texts), BATCH)))
    except llm.EmbedRefused:
        raise  # the policy, not the endpoint: no breaker
    except llm.EmbedUnavailable:
        _down_until = time.monotonic() + BREAKER
        raise
    return [v for part in parts for v in part]


async def embed_query(db: AsyncSession, q: str, *, sensitive: bool,
                      user_id: int | None = None) -> tuple[list[float], str] | None:
    """(vector, model key) for a search query, or None when semantic search is unavailable."""
    cur = await current(db, sensitive=sensitive, user_id=user_id, count=user_id is not None)
    if cur is None or not available_now():
        return None
    r, key = cur
    try:
        return (await embed_texts(r, [query_prefix(r.model) + q.strip()[:2000]]))[0], key
    except llm.EmbedUnavailable as e:
        log.info("query embedding unavailable: %s", e)
        return None


def passage_input(m: Message, p: MessagePassage) -> str:
    """What gets embedded for a passage: the passage plus the message's subject and sender, so a
    short paragraph ("Yes, Friday works") still carries its context."""
    who = f"{m.from_name} <{m.from_email}>" if m.from_name else m.from_email
    head = f"Subject: {m.subject or ''}\nFrom: {who}\n"
    return head + (p.text or m.snippet or "")


def _as_list(v: Any) -> list[float]:
    if hasattr(v, "to_list"):
        return v.to_list()
    return list(v)


async def embed_pending_passages(db: AsyncSession, user_id: int, message_ids: list[int] | None = None,
                                 limit: int = 2048, before: int | None = None) -> dict[str, Any]:
    """Embed passages that have no vector (or one from another model). Content from local-only
    accounts is embedded only if the canonical endpoint is local. Updates each touched
    message's message-level vector (the normalised mean of its passages, used by the learned
    classifiers). Returns counts; raises llm.EmbedUnavailable when the endpoint is down.
    Re-embedding after a model change goes down the message ids: `before` is the `before` of the
    previous call ("model": the model it was for), so a batch starts where the last one stopped
    instead of walking every passage already re-embedded."""
    from mailr.core.visibility import user_has_local_only

    cur = await current(db, sensitive=False, user_id=user_id)
    if cur is None:
        return {"embedded": 0, "reused": 0, "pending": 0}
    r, key = cur
    local_ok = outbound.local(r.endpoint)

    # The message's subject and sender go into the input; its body and vector, and the passage's
    # old vector, are not read.
    q = (select(MessagePassage, Message).join(Message, Message.id == MessagePassage.message_id)
         .options(defer(MessagePassage.embedding), *light_message())
         .where(MessagePassage.user_id == user_id))
    if not local_ok and await user_has_local_only(db, user_id):
        # The local-only guard: this endpoint is hosted, so local-only mail is never sent to it
        # (those passages stay keyword-only). The data class is the message's own (stamped at
        # ingest, kept when its account is removed, inherited by a phone-saved reply); a message
        # of a connector marked local-only later is held back as well.
        q = q.where(Message.local_only.is_(False),
                    ~exists().where(Connector.id == Message.connector_id, Connector.data_class == "local_only"))
    if message_ids is not None:
        q = q.where(MessagePassage.message_id.in_(message_ids))
    # Two queries, not one OR: passages without a vector come from ix_passage_pending; only
    # when those run out are vectors of another model looked for (a model change re-embeds).
    order = (MessagePassage.message_id.desc(), MessagePassage.idx)
    todo = list((await db.execute(q.where(MessagePassage.embedding.is_(None)).order_by(*order).limit(limit))).all())
    other = None
    if len(todo) < limit:
        oq = q.where(MessagePassage.embedding.is_not(None), MessagePassage.embed_model != key)
        if before is not None:
            # <=: the last batch may have stopped inside a message.
            oq = oq.where(MessagePassage.message_id <= before)
        other = (await db.execute(oq.order_by(*order).limit(limit - len(todo)))).all()
        todo += other
    if not todo:
        return {"embedded": 0, "reused": 0, "pending": 0, "before": None, "model": key}

    # Reuse vectors for identical text already embedded by this model.
    hashes = list({p.content_hash for p, _ in todo})
    known: dict[str, Any] = {}
    for i in range(0, len(hashes), 1000):
        for h, v in (await db.execute(
                select(MessagePassage.content_hash, MessagePassage.embedding)
                .where(MessagePassage.content_hash.in_(hashes[i:i + 1000]), MessagePassage.embed_model == key,
                       MessagePassage.embedding.is_not(None))
                .order_by(MessagePassage.content_hash))).all():
            known[h] = v
    uniq: dict[str, tuple[MessagePassage, Message]] = {}
    for p, m in todo:
        if p.content_hash not in known:
            uniq.setdefault(p.content_hash, (p, m))
    if uniq:
        vecs = await embed_texts(r, [passage_input(m, p) for p, m in uniq.values()])
        for h, v in zip(uniq, vecs, strict=True):
            known[h] = v
    for p, _ in todo:
        p.embedding = known[p.content_hash]
        p.embed_model = key
    await db.flush()
    await update_message_vectors(db, list({m.id for _, m in todo}), key)
    return {"embedded": len(uniq), "reused": len(todo) - len(uniq), "pending": len(todo),
            "before": min(p.message_id for p, _ in other) if other else None, "model": key}


async def update_message_vectors(db: AsyncSession, message_ids: list[int], key: str) -> None:
    if not message_ids:
        return
    rows = (await db.execute(select(MessagePassage.message_id, MessagePassage.embedding).where(
        MessagePassage.message_id.in_(message_ids), MessagePassage.embed_model == key,
        MessagePassage.embedding.is_not(None)))).all()
    by: dict[int, list] = {}
    for mid, v in rows:
        by.setdefault(mid, []).append(np.asarray(_as_list(v), dtype=np.float32))
    vals = []
    for mid, vs in by.items():
        mean = np.mean(vs, axis=0)
        n = float(np.linalg.norm(mean)) or 1.0
        vals.append({"id": mid, "embedding": (mean / n).tolist(), "embed_model": key})
    if vals:
        # One executemany by primary key instead of an UPDATE per message.
        await db.execute(update(Message), vals)


async def backfill_message_models(user_id: int | None = None, batch: int = 1000,
                                  progress=None) -> dict[str, int]:
    """Give the message vectors stored before messages.embed_model existed the current model's key
    (`mailr embed-model-backfill`). Assumption: those vectors came from the instance's current
    embedding model, the one every vector is meant to come from (llm.resolve_embed). The one
    exception the data shows is skipped: a message with a passage of another model was being
    re-embedded after a model change, so its vector may be the old model's; the re-embedding stamps
    it when it reaches that message. Batches of `batch` messages in id order, each committed, so
    it never holds many row locks and can be stopped and run again. Raises LookupError when no
    embedding endpoint is configured."""
    from sqlalchemy import text

    from mailr.db import session_scope

    async with session_scope() as db:
        cur = await current(db, sensitive=False)
    if cur is None:
        raise LookupError("no embedding endpoint is configured (admin model endpoint with an 'embed' default)")
    key = cur[1]
    st = {"stamped": 0, "skipped": 0, "batches": 0}
    last = 0
    while True:
        async with session_scope() as db:
            rows = (await db.execute(text(
                "SELECT m.id, EXISTS (SELECT 1 FROM message_passages p WHERE p.user_id = m.user_id "
                "AND p.message_id = m.id AND p.embed_model IS NOT NULL AND p.embed_model <> :k) AS other "
                "FROM messages m WHERE m.id > :last AND m.embedding IS NOT NULL AND m.embed_model IS NULL "
                + ("AND m.user_id = :u " if user_id is not None else "")
                + "ORDER BY m.id LIMIT :n"), {"k": key, "last": last, "u": user_id, "n": batch})).all()
            if not rows:
                break
            last = rows[-1].id
            ids = [r.id for r in rows if not r.other]
            if ids:
                await db.execute(text(
                    "UPDATE messages SET embed_model = :k WHERE id = ANY(:ids) AND embed_model IS NULL"),
                    {"k": key, "ids": ids})
            st["stamped"] += len(ids)
            st["skipped"] += len(rows) - len(ids)
            st["batches"] += 1
        if progress:
            progress(st)
    st["model"] = key  # type: ignore[assignment]
    return st
