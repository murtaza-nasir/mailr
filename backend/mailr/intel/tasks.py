"""Background jobs for the intelligence layer.

intel.message   per new message: passages + embeddings, category, importance, label suggestions,
                smart labels, then optional spam scoring. The LLM is used only for live mail
                (received in the last LIVE_WINDOW) that is not bulk; history imports use the
                embedding, heuristics, and learned models only.
intel.retrain   retrain one per-user model (debounced: at most once per DEBOUNCE).
intel.backfill  embed and classify a user's imported mail in batches (intel_state "backfill", set by
                core/ingest.py for history imports and old mail indexed from the store), newest
                first, and mail whose own intel.message job never finished.
intel.embed_pending  build missing passages and embed pending ones (catch-up after an
                endpoint outage, a model change, or for mail indexed before passages existed).
intel.train_all train every model the user has enough data for.
spam.train      mark $junk / $notjunk in Stalwart for a user verdict.
spam.classify   score with Stalwart's spam filter (see intel/spam.py for the modes); live mail is
                scored at the start of intel.message instead, and this job is the fallback.
Likely spam (in Spam, a blocked sender, or a spam score at or above `spam_suspect_score`) gets
rules-only triage: no decision model or chat model reads it (intel/spam.py model_skip_reason).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, exists, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from mailr.core import outbound, threading
from mailr.core.jobs import Retry, enqueue, handler
from mailr.core.store import user_store
from mailr.core.visibility import touches_local_only
from mailr.db import session_scope
from mailr.intel import categories, classify, embed, features, importance, llm, passages, spam
from mailr.models import ClassifierModel, Job, Label, LabelDecision, Message, User

log = logging.getLogger("mailr.intel")

LIVE_WINDOW = timedelta(days=3)
DEBOUNCE = timedelta(minutes=2)
BACKFILL_BATCH = 128
# Messages classified per commit in a backfill batch.
BACKFILL_CHUNK = 32
# Live mail whose own intel.message job is gone (it failed) is taken by the backfill after this long.
BACKFILL_PENDING_AFTER = timedelta(hours=1)


# The predicate of the partial index ix_msg_intel_todo, as literal SQL: with bound values the
# planner could not prove that a generic plan's rows are in the index.
TODO_WHERE = "messages.intel_state IN ('backfill', 'pending') AND messages.deleted IS false"


def backfill_todo(uid: int):
    """The user's messages the backfill takes (order them by date for the index's order)."""
    waiting = exists().where(Job.dedupe_key == func.concat("intel.message:", Message.id),
                             Job.status.in_(("queued", "running")))
    return select(Message).where(
        Message.user_id == uid, text(TODO_WHERE),
        or_(Message.intel_state == "backfill",
            and_(Message.created_at < datetime.now(UTC) - BACKFILL_PENDING_AFTER, ~waiting)))


def _live(m: Message) -> bool:
    when = m.received_at or m.date
    return bool(when and when > datetime.now(UTC) - LIVE_WINDOW)


async def _has_decision(db: AsyncSession, m: Message, kind: str, sources: tuple[str, ...]) -> bool:
    return bool((await db.execute(select(LabelDecision.id).where(
        LabelDecision.message_id == m.id, LabelDecision.kind == kind,
        LabelDecision.source.in_(sources)).limit(1))).first())


async def process_message(db: AsyncSession, m: Message, user: User, *, embedded: bool = False,
                          allow_llm: bool | None = None, scored: bool | None = None) -> None:
    """Triage one message. `embedded`: its passages and embedding are written already;
    `scored`: the spam check ran already (True when it scored the message here)."""
    if m.deleted or m.draft:
        if m.intel_state != "purged":  # a scrubbed row keeps its mark (core/purge.py)
            m.intel_state = "skipped"
        return
    store = await user_store(db, user)
    sensitive = await touches_local_only(db, user.id, message_ids=[m.id])
    raw = None
    try:
        raw = await store.get_raw(m)
    except Exception as e:  # noqa: BLE001
        log.info("no raw for message %s: %s", m.id, e)
    if not embedded:
        await passages.build_for_message(db, m)
        await _embed_or_defer(db, user.id, [m.id])
    s = await features.compute(db, m, raw)
    # Live mail is scored for spam first, so no model reads likely spam (intel/spam.py).
    if scored is None:
        scored = await _score_spam_first(db, m, user, raw)
    skip = await model_skip(db, user, m, raw)
    triage_llm = (user.settings or {}).get("llm_triage", True) and not skip
    live = _live(m) if allow_llm is None else allow_llm
    llm_ok = bool(triage_llm and live and not features.is_bulk(s))
    llm_signals = bool(triage_llm and live)

    # The model calls run with the message row unwritten (nothing flushes until they end), so the
    # user's archive or move meanwhile neither waits on this job nor is overwritten by it. The
    # importance flag and the labels are then applied to the row as it stands, under a short lock.
    with db.no_autoflush:
        important = await _decide(db, user, m, s, raw, sensitive=sensitive, skip=skip, live=live,
                                  llm_ok=llm_ok, llm_signals=llm_signals)
    await db.flush()
    await db.refresh(m, ["important", "label_ids"], with_for_update=True)
    if important is not None and important != m.important \
            and not await _has_decision(db, m, "importance", ("user",)):  # the user's toggle meanwhile wins
        await store.set_keywords([m], important=important)

    # Learned label suggestions / auto-labels. Mail older than the live window (a history
    # import) only gets suggestions: its labels came with it from the provider. Labels never
    # apply to likely spam (8 Oct 2026): mail in Spam or Trash, from a blocked sender or scored as
    # spam gets none, and what the classifier left on it before is withdrawn.
    if skip or await classify.labels_blocked(db, m):
        await classify.withdraw_labels(db, store, [m])
    else:
        await suggest_labels(db, store, user, m, s, allow_auto=_live(m))

    m.intel_state = "done"
    if not scored and spam.settings_for(user)[0] != "off" and spam.eligible_for_scoring(m):
        await enqueue(db, "spam.classify", {"message_id": m.id}, user_id=user.id,
                      dedupe_key=f"spam.classify:{m.id}")
    if skip:
        return  # likely spam: no standing order (a model reads the mail) and no away message
    # Standing orders read the signals computed above.
    from mailr.assistant.orders import maybe_enqueue_for_message

    # The built-in order "Follow my forwarded notes" is one of them (assistant/forwards.py).
    await maybe_enqueue_for_message(db, m)
    from mailr.core.vacation import maybe_enqueue as away_message

    await away_message(db, user, m, s)


async def _decide(db: AsyncSession, user: User, m: Message, s, raw: bytes | None, *, sensitive: bool,
                  skip: str | None, live: bool, llm_ok: bool, llm_signals: bool) -> bool | None:
    """Category, signals, the main action and an event change for one message. Returns whether
    it is important, or None when a rule, the user or the provider decided that already."""
    important: bool | None = None
    # Category (a rule or the user's own choice wins).
    if not await _has_decision(db, m, "category", ("rule", "user", "provider")):
        g = await categories.decide(db, user, m, s, sensitive=sensitive, allow_llm=llm_ok)
        m.category = None if g.category == "primary" else g.category
        db.add(LabelDecision(user_id=user.id, message_id=m.id, kind="category", target=g.category,
                             value=True, source=g.source, confidence=g.confidence, reason=g.reason))

    # Importance. A VIP sender's mail is always important (Settings → Mail → Inbox → VIP senders).
    vip = (m.from_email or "").lower() in {a.lower() for a in (user.settings or {}).get("vip") or []}
    if vip and not m.sent_by_me:
        if not await _has_decision(db, m, "importance", ("vip",)):
            m.importance_score = 1.0
            important = True
            db.add(LabelDecision(user_id=user.id, message_id=m.id, kind="importance", target="important", value=True,
                                 source="vip", confidence=1.0, reason="from a VIP sender"))
    elif not await _has_decision(db, m, "importance", ("rule", "user", "provider")):
        imp = await importance.decide(db, m, s)
        m.importance_score = imp.score
        important = imp.important
        db.add(LabelDecision(user_id=user.id, message_id=m.id, kind="importance", target="important",
                             value=imp.important, source=imp.source, confidence=imp.score,
                             reason="; ".join(imp.reasons)))

    # Smart labels.
    # Signals decide bulk themselves (a bill's deadline counts; its reply signals do not).
    for d in await classify.smart(db, user, m, s, sensitive=sensitive, allow_llm=llm_signals):
        db.add(d)

    # The email's main action, for a chip in the list (mail.primary_action).
    from mailr.intel import actions

    try:
        m.primary_action = await actions.extract(db, user, m, raw, sensitive=sensitive, allow_model=live and not skip)
    except Exception as e:  # noqa: BLE001 - a chip is never worth failing triage
        log.info("primary action for %s failed: %s", m.id, e)

    # A change of an event announced in words: one suggestion card, local model only (intel/event_changes.py).
    if llm_signals and not m.sent_by_me:
        try:
            from mailr.intel import event_changes

            await event_changes.suggest(db, user, m)
        except Exception as e:  # noqa: BLE001 - a suggestion is never worth failing triage
            log.info("event change check for %s failed: %s", m.id, e)
    return important


async def suggest_labels(db: AsyncSession, store, user: User, m: Message, s, *, allow_auto: bool) -> None:
    """Run the label classifier on one message: apply what qualifies, suggest the rest."""
    for p in await classify.predict_labels(db, user, m, s, allow_auto=allow_auto):
        if p["auto"]:
            await store.set_labels([m], add=[p["label_id"]], remove=[], db=db)
        db.add(LabelDecision(user_id=user.id, message_id=m.id, kind="label", target=str(p["label_id"]),
                             value=True, source="classifier", source_ref=f"label:{p['label_id']}",
                             confidence=p["prob"], reason=f"similar to mail you labelled (p={p['prob']:.2f})",
                             status="applied" if p["auto"] else "suggested"))


async def _score_spam_first(db: AsyncSession, m: Message, user: User, raw: bytes | None) -> bool:
    """Score live mail with Stalwart's filter before triage (it may move the message to Spam).
    False when it was not scored here; the spam.classify job then tries later."""
    if raw is None or m.spam_score is not None or spam.settings_for(user)[0] == "off" \
            or not spam.eligible_for_scoring(m):
        return False
    try:
        await spam.score_and_act(db, m, raw)
    except Exception as e:  # noqa: BLE001 - scoring is best effort; the job retries it
        log.info("spam scoring before triage failed for %s: %s", m.id, e)
        return False
    return True


async def model_skip(db: AsyncSession, user: User, m: Message, raw: bytes | None) -> str | None:
    """Likely spam gets rules-only triage: no decision model or chat model reads it. The reason
    is recorded once, for the Why panel."""
    why = await spam.model_skip_reason(db, user, m, raw)
    if why and not await _has_decision(db, m, "triage", ("heuristic",)):
        db.add(LabelDecision(user_id=user.id, message_id=m.id, kind="triage", target="model_skipped", value=True,
                             source="heuristic", source_ref="spam", confidence=None, reason=why, status="applied"))
    elif not why:  # "Not spam" lifted it: the old note goes
        await db.execute(delete(LabelDecision).where(LabelDecision.message_id == m.id, LabelDecision.kind == "triage",
                                                     LabelDecision.target == "model_skipped"))
    return why


@handler("intel.message")
async def intel_message(payload: dict[str, Any], ctx) -> None:
    # Passages, the embedding and the spam score first, in a transaction of their own: they
    # write the message and its conversation, and no model runs while those rows are locked.
    async with session_scope() as db:
        m = await db.get(Message, payload["message_id"])
        if m is None or m.intel_state == "done":
            return
        user = await db.get(User, m.user_id)
        if user is None:
            return
        scored = None
        if not (m.deleted or m.draft):
            await passages.build_for_message(db, m)
            await _embed_or_defer(db, user.id, [m.id])
            try:
                raw = await (await user_store(db, user)).get_raw(m)
            except Exception:  # noqa: BLE001 - process_message notes it
                raw = None
            scored = await _score_spam_first(db, m, user, raw)
    async with session_scope() as db:
        m = await db.get(Message, payload["message_id"])
        if m is None or m.intel_state == "done":
            return
        user = await db.get(User, m.user_id)
        await process_message(db, m, user, embedded=True, scored=scored)
        await db.flush()
        if m.conversation_id:
            await threading.refresh(db, user.id, [m.conversation_id])


@handler("intel.backfill")
async def intel_backfill(payload: dict[str, Any], ctx) -> None:
    uid = payload["user_id"]
    # Passages and the batch's one embedding pass first, in a transaction of their own: the
    # embedding call no longer runs while conversation rows are locked.
    async with session_scope() as db:
        user = await db.get(User, uid)
        if user is None:
            return
        msgs = list((await db.execute(backfill_todo(uid).options(defer(Message.embedding))
                                      .order_by(Message.date.desc().nulls_last()).limit(BACKFILL_BATCH))).scalars())
        if not msgs:
            await enqueue(db, "intel.train_all", {"user_id": uid}, user_id=uid, dedupe_key=f"intel.train_all:{uid}")
            return
        for m in msgs:
            await passages.build_for_message(db, m)
        ids = [m.id for m in msgs]
        # The batch holds mail of every class; embed_pending_passages picks what the endpoint
        # may receive, so the batch's own reads do not taint the call (core/outbound.py).
        with outbound.item_scope():
            await _embed_or_defer(db, uid, ids)
    # Then classification in small commits: a user's own action on one of these conversations
    # waits for at most one chunk, not the whole batch.
    for i in range(0, len(ids), BACKFILL_CHUNK):
        async with session_scope() as db:
            user = await db.get(User, uid)
            if user is None:
                return
            chunk = list((await db.execute(select(Message).where(
                Message.id.in_(ids[i:i + BACKFILL_CHUNK]), Message.intel_state.in_(("backfill", "pending")),
                Message.deleted.is_(False)))).scalars())
            chunk.sort(key=lambda m: ids.index(m.id))  # newest first, as picked
            convs: set[int] = set()
            for m in chunk:
                # One message that cannot be triaged (bad model output, a bug) is set aside alone;
                # it no longer fails the chunk on every retry and stops the user's backfill.
                try:
                    # One message's class, not the chunk's (core/outbound.item_scope).
                    with outbound.item_scope(m):
                        async with db.begin_nested():
                            await process_message(db, m, user, embedded=True)
                except Exception:
                    log.exception("triage of message %s failed; it is skipped", m.id)
                    await db.execute(update(Message).where(Message.id == m.id).values(intel_state="skipped"))
                    continue
                if m.conversation_id:
                    convs.add(m.conversation_id)
            await db.flush()
            await threading.refresh(db, uid, list(convs))
    # The next batch: this job again, so it keeps its dedupe key and an import's enqueue meanwhile
    # adds no second one.
    raise Retry(delay=1)


async def _embed_or_defer(db: AsyncSession, uid: int, message_ids: list[int]) -> None:
    """Embed these messages' passages now; if the endpoint is down, leave them pending and let
    the catch-up job try again after the circuit breaker (classification proceeds without the
    vector, as it would with no endpoint at all)."""
    try:
        await embed.embed_pending_passages(db, uid, message_ids=message_ids)
    except llm.EmbedUnavailable as e:
        log.info("embedding deferred for user %s: %s", uid, e)
        await enqueue(db, "intel.embed_pending", {"user_id": uid}, user_id=uid,
                      dedupe_key=f"intel.embed_pending:{uid}",
                      run_at=datetime.now(UTC) + timedelta(seconds=embed.BREAKER))


@handler("intel.embed_pending")
async def intel_embed_pending(payload: dict[str, Any], ctx) -> None:
    """Catch-up: passages for messages that have none, then embeddings for passages that have
    no vector or one from an older model. Self-rescheduling until done."""
    uid = payload["user_id"]
    async with session_scope() as db:
        cur = await embed.current(db, sensitive=False)
        if cur is None:
            return  # no endpoint: nothing to do (keyword search still works)
        built = 0
        # Messages without passages are looked for until a batch finds none: the anti-join reads
        # the whole mailbox, and the batches after that only embed.
        if not payload.get("passages_done"):
            for m in await passages.messages_without_passages(db, uid, limit=500):
                built += await passages.build_for_message(db, m)
        # The re-embedding cursor holds for the model it was made for.
        before = payload.get("before") if payload.get("model") == cur[1] else None
        try:
            # The passages built above read mail of every class; the embedding picks its own rows.
            with outbound.item_scope():
                res = await embed.embed_pending_passages(db, uid, limit=2048, before=before)
        except llm.EmbedUnavailable as e:
            log.info("embedding catch-up for user %s waits: %s", uid, e)
            raise Retry(delay=embed.BREAKER) from None
        more = built > 0 or res["pending"] >= 2048
    if more:
        nxt = {"user_id": uid, "passages_done": built == 0, "model": res["model"],
               "before": res["before"] if res["before"] is not None else before}
        # The next batch is this job again with the new payload, so it keeps its dedupe key: a
        # second catch-up of the user (an import's, another worker's) is never queued beside it.
        async with session_scope() as db:
            await db.execute(update(Job).where(Job.id == ctx.job_id).values(payload=nxt))
        raise Retry(delay=1)


@handler("intel.retrain")
async def intel_retrain(payload: dict[str, Any], ctx) -> None:
    uid = ctx.user_id
    kind, target = payload.get("kind"), payload.get("target")
    key = {"label": f"label:{target}", "importance": "importance", "category": "category"}.get(kind)
    if key is None or uid is None:
        return
    async with session_scope() as db:
        row = (await db.execute(select(ClassifierModel.trained_at).where(
            ClassifierModel.user_id == uid, ClassifierModel.target == key))).scalar_one_or_none()
        # At most once per DEBOUNCE, except after a batch of importance choices (the review queue).
        if row and datetime.now(UTC) - row < DEBOUNCE and not (
                kind == "importance" and await importance.choices_since(db, uid, row) >= importance.RETRAIN_AFTER):
            raise Retry(delay=DEBOUNCE.total_seconds())
        user = await db.get(User, uid)
        await retrain(db, user, kind, target)


async def retrain(db: AsyncSession, user: User, kind: str, target: str | None) -> dict | None:
    if kind == "label":
        try:
            lid = int(target or "")
        except ValueError:
            return None
        return await classify.retrain_label(db, user, lid)
    if kind == "importance":
        return await importance.retrain(db, user.id)
    if kind == "category":
        return await categories.retrain(db, user.id)
    return None


@handler("intel.train_all")
async def intel_train_all(payload: dict[str, Any], ctx) -> None:
    uid = payload["user_id"]
    async with session_scope() as db:
        user = await db.get(User, uid)
        if user is None:
            return
        await importance.retrain(db, uid)
        await categories.retrain(db, uid)
        rows = (await db.execute(select(Label.id).where(Label.user_id == uid, Label.role.is_(None)))).all()
        # A deleted label's model goes with it.
        live_targets = {f"label:{lid}" for (lid,) in rows}
        await db.execute(delete(ClassifierModel).where(
            ClassifierModel.user_id == uid, ClassifierModel.target.like("label:%"),
            ClassifierModel.target.not_in(live_targets or {""})))
        trained = set((await db.execute(select(ClassifierModel.target).where(
            ClassifierModel.user_id == uid, ClassifierModel.target.like("label:%")))).scalars())
        for (lid,) in rows:
            n = (await db.execute(select(func.count()).select_from(Message).where(
                Message.user_id == uid, Message.label_ids.contains([lid])))).scalar_one()  # @> uses ix_msg_labels
            # A label with a model is retrained even with little data (its model is then deleted).
            if n >= classify.MIN_POSITIVES_TRAIN or f"label:{lid}" in trained:
                await classify.retrain_label(db, user, lid)


@handler("spam.train")
async def spam_train(payload: dict[str, Any], ctx) -> None:
    async with session_scope() as db:
        m = await db.get(Message, payload["message_id"])
        if m is None or m.deleted:
            return
        await spam.train(db, m, bool(payload.get("spam")))


@handler("spam.classify")
async def spam_classify(payload: dict[str, Any], ctx) -> None:
    async with session_scope() as db:
        m = await db.get(Message, payload["message_id"])
        if m is None or m.deleted:
            return
        user = await db.get(User, m.user_id)
        store = await user_store(db, user)
        raw = await store.get_raw(m)
        await spam.score_and_act(db, m, raw)


@handler("intel.summarize")
async def summarize_job(payload: dict[str, Any], ctx) -> None:
    from mailr.core.events import notify
    from mailr.intel.summarize import summarize

    async with session_scope() as db:
        user = await db.get(User, payload["user_id"])
        if user is None:
            return
        try:
            await summarize(db, user, payload["conversation_id"], force=bool(payload.get("force")))
        except LookupError:
            return
        await notify(db, user.id, {"type": "summary", "conversation_id": payload["conversation_id"]})


async def retriage_message(db: AsyncSession, m: Message, user: User, *, labels: bool = False,
                           only_labels: bool = False) -> None:
    """Decide category and signals again, the model allowed whatever the message's age
    (Settings → Ambrose → Decisions lab → Re-run). Your own and your filters' choices stay;
    labels are not re-applied (that could put back a label you removed). `labels`: after "Not
    spam", the label classifier runs again too (a label you removed stays off: its decision is
    rejected); `only_labels`: nothing else is decided again."""
    if m.deleted or m.draft:
        return
    store = await user_store(db, user)
    try:
        raw = await store.get_raw(m)
    except Exception:  # noqa: BLE001
        raw = None
    sensitive = await touches_local_only(db, user.id, message_ids=[m.id])
    s = await features.compute(db, m, raw)
    skip = await model_skip(db, user, m, raw)  # likely spam: rules only, here too
    if labels or only_labels:
        if not skip and not await classify.labels_blocked(db, m):
            await suggest_labels(db, store, user, m, s, allow_auto=_live(m))
        if only_labels:
            return
    llm_ok = bool((user.settings or {}).get("llm_triage", True) and not skip and not features.is_bulk(s))
    llm_signals = bool((user.settings or {}).get("llm_triage", True) and not skip)
    if not await _has_decision(db, m, "category", ("rule", "user", "provider")):
        g = await categories.decide(db, user, m, s, sensitive=sensitive, allow_llm=llm_ok)
        m.category = None if g.category == "primary" else g.category
        db.add(LabelDecision(user_id=user.id, message_id=m.id, kind="category", target=g.category,
                             value=True, source=g.source, confidence=g.confidence, reason=g.reason))
    await db.execute(delete(LabelDecision).where(
        LabelDecision.message_id == m.id, LabelDecision.kind == "smart",
        LabelDecision.source.in_(("heuristic", *classify.MODEL_SOURCES)), LabelDecision.status != "rejected"))
    # Signals decide bulk themselves (a bill's deadline counts; its reply signals do not).
    for d in await classify.smart(db, user, m, s, sensitive=sensitive, allow_llm=llm_signals):
        db.add(d)


@handler("intel.retriage")
async def retriage_job(payload: dict[str, Any], ctx) -> None:
    async with session_scope() as db:
        user = await db.get(User, payload["user_id"])
        if user is None:
            return
        done = set()
        for mid in payload["message_ids"]:
            m = await db.get(Message, mid)
            if m is None or m.user_id != user.id:
                continue
            with outbound.item_scope(m):
                await retriage_message(db, m, user, labels=bool(payload.get("labels")),
                                       only_labels=mid in set(payload.get("labels_only") or []))
            done.add(m.conversation_id)
            await db.flush()
        await threading.refresh(db, user.id, sorted(c for c in done if c))
