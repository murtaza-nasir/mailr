"""Priority Inbox importance.

A transparent heuristic prior scores every message from the start. Once the user has
enough history (replies, stars, importance toggles, and mail archived without reading),
a per-user model learns from it and is blended with the prior. Each decision stores the
reasons shown in the UI ("You often reply to Alice", "Mailing list", ...).

Only the user's own judgment is training data. Gmail's Important verdicts on imported mail
stay stored as "provider" decisions (they keep that mail's flag from being judged again),
but they are not training data (1 Oct).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.intel import learn
from mailr.intel.features import Signals, compute_batch, is_bulk, learned_vector
from mailr.models import ClassifierModel, LabelDecision, Message

log = logging.getLogger("mailr.intel")

THRESHOLD = 0.5
MIN_EACH = 20
# Version 2: trained without the provider's verdicts. A stored model of an older version is
# retrained, or dropped when there is too little data (retrain below).
VERSION = 2
# After this many new choices in the review queue (Settings → Ambrose → Review importance),
# the model is retrained even inside the retrain debounce (intel/tasks.py).
RETRAIN_AFTER = 10


@dataclass
class Importance:
    score: float
    important: bool
    reasons: list[str]
    source: str


def prior(m: Message, s: Signals) -> tuple[float, list[str]]:
    v = s.values
    name = (s.sender.name if s.sender and s.sender.name else m.from_name) or m.from_email
    score, reasons = 0.15, []
    if m.sent_by_me:
        return 0.2, ["sent by you"]
    if v["i_wrote_to_sender"]:
        score += 0.35
        reasons.append(f"You often write to {name}" if v["log_sent"] > 0.35 else f"You have written to {name}")
    elif v["known_sender"]:
        score += 0.12
        reasons.append(f"You get mail from {name} regularly")
    if v["direct_to_me"]:
        score += 0.18
        reasons.append("Sent directly to you" if v["only_recipient"] else "Addressed to you")
    elif v["cc_me"]:
        score -= 0.05
        reasons.append("You were cc'd")
    if v["reply_to_me"] or v["i_participated"]:
        score += 0.25
        reasons.append("A conversation you are part of")
    if v["question"] and v["direct_to_me"] and not is_bulk(s):
        score += 0.08
        reasons.append("Asks a question")
    if v["has_invite"]:
        score += 0.1
        reasons.append("Contains an invitation")
    if is_bulk(s):
        score -= 0.35
        reasons.append("Mailing list" if v["is_list"] else "Bulk or automated mail")
    if v["many_recipients"]:
        score -= 0.08
        reasons.append("Sent to many people")
    if v["auth_fail"]:
        score -= 0.2
        reasons.append("Couldn't verify the sender")
    return max(0.0, min(1.0, score)), reasons


async def decide(db: AsyncSession, m: Message, s: Signals) -> Importance:
    p, reasons = prior(m, s)
    got = await learn.load(db, m.user_id, "importance")
    if got is not None and m.embedding is not None:
        model, _row = got
        lp = float(model.predict_proba(learn.row_vector(m.embedding, learned_vector(s)).reshape(1, -1))[0][1])
        score = 0.35 * p + 0.65 * lp
        if abs(lp - p) > 0.3:
            reasons.insert(0, "Based on how you treat similar mail" if lp > p else "You usually skip mail like this")
        return Importance(score, score >= THRESHOLD, reasons[:4], "classifier")
    return Importance(p, p >= THRESHOLD, reasons[:4], "heuristic")


async def training_examples(db: AsyncSession, user_id: int) -> tuple[list[int], list[int], set[int]]:
    """Return (positive ids, negative ids, ids that came from explicit toggles).

    Explicit examples are the user's own toggles (source "user", the latest per message). The
    behaviour examples are mail the user replied to or starred (important) and mail archived
    unread for more than 3 days (not important). Provider verdicts are left out."""
    rows = (await db.execute(select(LabelDecision.message_id, LabelDecision.value).where(
        LabelDecision.user_id == user_id, LabelDecision.kind == "importance", LabelDecision.source == "user")
        .order_by(LabelDecision.id))).all()
    explicit = {mid: val for mid, val in rows}
    pos = {mid for mid, v in explicit.items() if v}
    neg = {mid for mid, v in explicit.items() if not v}
    cutoff = datetime.now(UTC) - timedelta(days=3)
    q = await db.execute(text(
        """SELECT id FROM messages WHERE user_id=:u AND NOT deleted AND NOT sent_by_me AND NOT draft
           AND (answered OR flagged) ORDER BY date DESC LIMIT 1500"""), {"u": user_id})
    pos.update(r[0] for r in q)
    q = await db.execute(text(
        """SELECT m.id FROM messages m WHERE m.user_id=:u AND NOT m.deleted AND NOT m.sent_by_me AND NOT m.seen
           AND NOT m.answered AND NOT m.flagged AND m.date < :c
           AND NOT EXISTS (SELECT 1 FROM labels l WHERE l.id = ANY(m.label_ids)
                           AND l.role IN ('inbox','junk','trash','drafts'))
           ORDER BY m.date DESC LIMIT 1500"""), {"u": user_id, "c": cutoff})
    neg.update(r[0] for r in q)
    neg -= {m for m, v in explicit.items() if v}
    pos -= {m for m, v in explicit.items() if not v}
    return sorted(pos), sorted(neg), set(explicit)


async def choices_since(db: AsyncSession, user_id: int, when: datetime | None) -> int:
    """The user's own importance choices recorded after `when` (all of them without it)."""
    q = select(func.count()).select_from(LabelDecision).where(
        LabelDecision.user_id == user_id, LabelDecision.kind == "importance", LabelDecision.source == "user")
    if when is not None:
        q = q.where(LabelDecision.created_at > when)
    return int((await db.execute(q)).scalar_one())


async def learned_from(db: AsyncSession, user_id: int) -> int:
    """How many messages the user has judged themselves (the explicit examples)."""
    return int((await db.execute(select(func.count(func.distinct(LabelDecision.message_id))).where(
        LabelDecision.user_id == user_id, LabelDecision.kind == "importance",
        LabelDecision.source == "user"))).scalar_one())


async def _drop_stale(db: AsyncSession, user_id: int) -> None:
    """Without enough data for a new model, a model of an older version (one trained on the
    provider's verdicts) is deleted; mail is then scored by the heuristic until there is enough."""
    row = (await db.execute(select(ClassifierModel).where(
        ClassifierModel.user_id == user_id, ClassifierModel.target == "importance"))).scalar_one_or_none()
    if row is not None and (row.metrics or {}).get("version", 1) < VERSION:
        await db.delete(row)
        learn._CACHE.pop((user_id, "importance"), None)
        log.info("importance model of user %s dropped: trained on provider verdicts, too little data to retrain",
                 user_id)


async def retrain(db: AsyncSession, user_id: int) -> dict | None:
    pos, neg, explicit = await training_examples(db, user_id)
    if len(pos) < MIN_EACH or len(neg) < MIN_EACH:
        await _drop_stale(db, user_id)
        return None
    ids = pos + neg
    msgs = {m.id: m for m in (await db.execute(select(Message).where(
        Message.id.in_(ids), Message.embedding.is_not(None)))).scalars()}
    order = [i for i in ids if i in msgs]
    if len(order) < 2 * MIN_EACH:
        await _drop_stale(db, user_id)
        return None
    ml = [msgs[i] for i in order]
    sigs = await compute_batch(db, user_id, ml)
    X = np.stack([learn.row_vector(m.embedding, learned_vector(s)) for m, s in zip(ml, sigs, strict=True)])
    posset = set(pos)
    y = np.asarray([1 if i in posset else 0 for i in order])
    w = np.asarray([2.0 if i in explicit else 1.0 for i in order])
    t = await asyncio.to_thread(learn.train_binary, X, y, w)  # off the event loop
    if t is None:
        await _drop_stale(db, user_id)
        return None
    # Where the examples came from, for the review page's model status.
    provider = int((await db.execute(select(func.count()).select_from(LabelDecision).where(
        LabelDecision.user_id == user_id, LabelDecision.kind == "importance",
        LabelDecision.source == "provider"))).scalar_one())
    t.metrics.update(version=VERSION, sources={
        "user": sum(1 for i in order if i in explicit),
        "replied_or_starred": sum(1 for i in order if i in posset and i not in explicit),
        "archived_unread": sum(1 for i in order if i not in posset and i not in explicit),
        "provider_excluded": provider})
    await learn.save(db, user_id, "importance", t)
    return t.metrics
