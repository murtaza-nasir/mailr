"""Hybrid passage search: vector similarity (pgvector HNSW) + keyword rank (Postgres FTS),
fused with reciprocal rank fusion. Two indexed queries, no in-process index.

Visibility: token callers only see passages of messages their token may see
(core/visibility.visible_message_filter); spam and trash are excluded unless asked for.
Every caller declares `query_sensitive` (a required keyword): True when the query may carry
local-only content, which keeps its embedding off hosted endpoints. That is a query a model wrote
in a chat that read local-only content, a query built from mail content (the agent's context
builder), and any query from a local-only token (core/visibility.token_query_sensitive), whose
caller reads local-only mail. The person's "Local models only for me" and the household rule apply
on top (llm.resolve_embed, through user_id).
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

from sqlalchemy import case, func, not_, or_, select, text, union
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.visibility import visible_message_filter
from mailr.intel import embed
from mailr.models import Label, Message, MessagePassage

RRF_K = 60
MIN_SIMILARITY = 0.35  # vector-only hits below this are noise
_S, _E = "\x01", "\x02"


@dataclass
class Hit:
    passage_id: int
    message_id: int
    conversation_id: int | None
    idx: int
    start: int
    end: int
    text: str
    score: float
    similarity: float | None
    keyword: bool
    highlight: str = ""

    def out(self) -> dict[str, Any]:
        return {"passage_id": self.passage_id, "message_id": self.message_id,
                "conversation_id": self.conversation_id, "start": self.start, "end": self.end,
                "text": self.text, "highlight": self.highlight, "score": round(self.score, 5),
                "similarity": None if self.similarity is None else round(self.similarity, 3),
                "keyword_match": self.keyword}


def _headline_html(raw: str) -> str:
    """ts_headline output with sentinel markers → HTML-escaped text with <mark>."""
    return html.escape(raw).replace(_S, "<mark>").replace(_E, "</mark>")


async def _base_conditions(db: AsyncSession, principal, include_spam_trash: bool) -> list[Any]:
    conds: list[Any] = [MessagePassage.user_id == principal.user_id, Message.deleted.is_(False),
                        Message.draft.is_(False)]
    if principal.token is not None:
        conds.append(visible_message_filter(principal))
    if not include_spam_trash:
        hidden = list((await db.execute(select(Label.id).where(
            Label.user_id == principal.user_id, Label.role.in_(("junk", "trash"))))).scalars())
        if hidden:
            conds.append(not_(Message.label_ids.overlap(hidden)))
    return conds


async def passages(db: AsyncSession, principal, q: str, *, conversation_id: int | None = None,
                   message_ids: list[int] | None = None, k: int = 10, include_spam_trash: bool = False,
                   query_sensitive: bool, candidates: int = 80) -> list[Hit]:
    q = (q or "").strip()
    if not q:
        return []
    conds = await _base_conditions(db, principal, include_spam_trash)
    if conversation_id is not None:
        conds.append(Message.conversation_id == conversation_id)
    if message_ids is not None:
        conds.append(Message.id.in_(message_ids))
    joined = (select(MessagePassage.id, Message.conversation_id)
              .select_from(MessagePassage).join(Message, Message.id == MessagePassage.message_id))

    vec_rows: list[Any] = []
    qv = await embed.embed_query(db, q, sensitive=query_sensitive, user_id=principal.user_id)
    if qv is not None:
        vec, key = qv
        dist = MessagePassage.embedding.cosine_distance(vec)
        # Filtered HNSW: keep scanning until enough rows pass the filters (pgvector ≥ 0.8).
        await db.execute(text("SET LOCAL hnsw.ef_search = 200"))
        await db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        vec_rows = (await db.execute(
            joined.add_columns(dist.label("d"))
            .where(*conds, MessagePassage.embed_model == key, MessagePassage.embedding.is_not(None))
            .order_by(dist).limit(candidates))).all()

    tsq = func.websearch_to_tsquery("simple", q)
    rank = func.ts_rank_cd(MessagePassage.tsv, tsq)
    # Subject/sender matches count too (they are not in the passage text): passage 0 carries them.
    subj = func.ts_rank_cd(Message.tsv, tsq)
    # The text of attached emails (not in any passage) counts on passage 0 too, at a lower
    # rank than the message's own words.
    attached = func.coalesce(func.ts_rank_cd(Message.attached_tsv, tsq), 0.0)
    kw_rank = (rank + case((MessagePassage.idx == 0, 0.5 * subj + 0.2 * attached), else_=0.0)).label("r")
    # The matching passages come from two indexed lookups: passages whose own text matches
    # (ix_passage_tsv), and the first passage of messages whose subject, sender or attached
    # emails match (ix_msg_tsv, ix_msg_attached_tsv). One OR across the join could use neither
    # index and read every passage (14 Oct 2026: ix_passage_tsv had never been used in production).
    by_text = (select(MessagePassage.id).select_from(MessagePassage)
               .join(Message, Message.id == MessagePassage.message_id)
               .where(*conds, MessagePassage.tsv.op("@@")(tsq)))
    by_message = (select(MessagePassage.id).select_from(MessagePassage)
                  .join(Message, Message.id == MessagePassage.message_id)
                  .where(*conds, MessagePassage.idx == 0,
                         or_(Message.tsv.op("@@")(tsq), Message.attached_tsv.op("@@")(tsq))))
    matched = union(by_text, by_message).subquery()
    kw_rows = (await db.execute(
        joined.add_columns(kw_rank)
        .where(MessagePassage.id.in_(select(matched.c.id)))
        .order_by(kw_rank.desc()).limit(candidates))).all()

    score: dict[int, float] = {}
    sim: dict[int, float] = {}
    kw_ids = {r.id for r in kw_rows}
    for i, r in enumerate(vec_rows):
        s = 1.0 - float(r.d)
        sim[r.id] = s
        if s >= MIN_SIMILARITY or r.id in kw_ids:
            score[r.id] = score.get(r.id, 0.0) + 1.0 / (RRF_K + i + 1)
    for i, r in enumerate(kw_rows):
        score[r.id] = score.get(r.id, 0.0) + 1.0 / (RRF_K + i + 1)
    top = sorted(score, key=lambda i: -score[i])[:k]
    if not top:
        return []
    opts = f"StartSel={_S}, StopSel={_E}, MaxWords=40, MinWords=12, MaxFragments=2, FragmentDelimiter= … "
    rows = (await db.execute(
        select(MessagePassage.id, MessagePassage.message_id, Message.conversation_id, MessagePassage.idx,
               MessagePassage.start, MessagePassage.end, MessagePassage.text,
               func.ts_headline("simple", MessagePassage.text, tsq, opts).label("hl"))
        .join(Message, Message.id == MessagePassage.message_id)
        .where(MessagePassage.id.in_(top)))).all()
    by = {r.id: r for r in rows}
    out = []
    for pid in top:
        r = by.get(pid)
        if r is None:
            continue
        hl = r.hl if (r.hl and _S in r.hl) else None
        out.append(Hit(pid, r.message_id, r.conversation_id, r.idx, r.start, r.end, r.text, score[pid],
                       sim.get(pid), pid in kw_ids,
                       _headline_html(hl) if hl else html.escape((r.text or "")[:280])))
    return out


async def conversations(db: AsyncSession, principal, q: str, *, limit: int = 20,
                        query_sensitive: bool) -> list[dict[str, Any]]:
    """Conversation ranking from passage hits: best passage score plus a small bonus for
    further matching passages in the same conversation."""
    hits = await passages(db, principal, q, k=max(60, limit * 4), query_sensitive=query_sensitive,
                          candidates=200)
    agg: dict[int, dict[str, Any]] = {}
    for h in hits:
        if h.conversation_id is None:
            continue
        a = agg.setdefault(h.conversation_id, {"score": 0.0, "best": h, "count": 0})
        a["score"] += h.score if a["count"] == 0 else 0.25 * h.score
        a["count"] += 1
    ordered = sorted(agg.items(), key=lambda kv: -kv[1]["score"])[:limit]
    return [{"conversation_id": cid, "score": a["score"], "passages": a["count"], "best": a["best"]}
            for cid, a in ordered]
