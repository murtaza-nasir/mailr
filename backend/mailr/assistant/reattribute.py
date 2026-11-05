"""`mailr decisions reattribute-orders`: past standing-order decisions stored as the user's.

Until 0.40.37 a standing order's actions ran as the user, so each label, importance flag and
category an order set was stored as a "user" decision ("user action", status "accepted"), and the
models learned it with the weight of the user's own choice. Since then an order's decisions are
"order" decisions (orders.attribution). This command finds the old ones and records them the same
way, then queues a retrain of each model whose examples changed.

A user decision is the order's when a finished run ("done") of an order acted on its message, or
on the message's conversation (an order acts on the whole conversation), within the run's time
(created_at − 2 s … finished_at + 5 s), and the run's recorded actions contain exactly that
decision: add_labels or remove_labels with the label's id, important or not_important for
importance, category with that category. Anything else stays the user's: a routine's run records
no conversations, so its decisions cannot be told from the user's own and stay too.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Table, bindparam, func, or_, select, text, update

from mailr.core.jobs import enqueue
from mailr.db import sessionmaker
from mailr.models import AssistantOrder, AssistantRun, AuditLog, Label, LabelDecision, Message

log = logging.getLogger(__name__)

BEFORE = timedelta(seconds=2)
AFTER = timedelta(seconds=5)
KINDS = ("label", "importance", "category")
DELETED_ORDER = "(deleted order)"


@dataclass
class Report:
    examined: int = 0
    matched: int = 0
    other_messages: int = 0  # matched on another message of the run's conversation
    by_order: Counter[str] = field(default_factory=Counter)
    by_target: Counter[tuple[str, str]] = field(default_factory=Counter)  # (kind, label name / target)
    retrains: list[tuple[str, str]] = field(default_factory=list)

    @property
    def kept(self) -> int:
        return self.examined - self.matched


def action_matches(kind: str, target: str, value: bool, actions: list[dict[str, Any]] | None) -> bool:
    """Whether a run's recorded actions (orders.run_instant) set exactly this decision."""
    for a in actions or []:
        if not isinstance(a, dict):
            continue
        name, raw = a.get("action"), a.get("args")
        args: dict[str, Any] = raw if isinstance(raw, dict) else {}
        if kind == "label":
            ids = args.get("label_ids")
            if name == ("add_labels" if value else "remove_labels") and isinstance(ids, list) \
                    and target in {str(x) for x in ids}:
                return True
        elif kind == "importance":
            if target == "important" and name == ("important" if value else "not_important"):
                return True
        elif kind == "category":
            if value and name == "category" and str(args.get("category") or "primary") == target:
                return True
    return False


def in_window(at: datetime, run: AssistantRun) -> bool:
    end = run.finished_at or run.created_at
    return run.created_at - BEFORE <= at <= end + AFTER


def _reason(run: AssistantRun, order: AssistantOrder | None) -> tuple[str, str, float | None]:
    """source_ref, reason and confidence as orders.attribution gives them for a new decision. A
    deleted order is described from what its run recorded."""
    from mailr.assistant.orders import attribution

    cond = run.condition or {}
    stand_in = order or AssistantOrder(name=DELETED_ORDER, trigger="message", query=str(cond.get("query") or ""))
    by = attribution(stand_in, cond)
    return str(by["name"] or DELETED_ORDER)[:200], by["reason"], by["confidence"]


async def _batch(db, uid: int, after: int, size: int):
    """The next user decisions (id > after) with their message's conversation."""
    return (await db.execute(
        select(LabelDecision.id, LabelDecision.message_id, LabelDecision.kind, LabelDecision.target,
               LabelDecision.value, LabelDecision.created_at, Message.conversation_id)
        .join(Message, Message.id == LabelDecision.message_id)
        .where(LabelDecision.user_id == uid, LabelDecision.source == "user",
               LabelDecision.reason == "user action", LabelDecision.kind.in_(KINDS), LabelDecision.id > after)
        .order_by(LabelDecision.id).limit(size))).all()


async def _match(db, uid: int, rows) -> list[tuple[Any, AssistantRun]]:
    """Each decision of `rows` that a run set, with that run (the nearest in time when several did;
    a run on the decision's own message first)."""
    mids = {r.message_id for r in rows}
    convs = {r.conversation_id for r in rows if r.conversation_id is not None}
    lo, hi = min(r.created_at for r in rows), max(r.created_at for r in rows)
    runs = (await db.execute(select(AssistantRun).where(
        AssistantRun.user_id == uid, AssistantRun.status == "done",
        or_(AssistantRun.message_id.in_(mids), AssistantRun.conversation_id.in_(convs or {-1})),
        AssistantRun.created_at <= hi + BEFORE,
        func.coalesce(AssistantRun.finished_at, AssistantRun.created_at) >= lo - AFTER))).scalars().all()
    out = []
    for r in rows:
        cands = [run for run in runs
                 if (run.message_id == r.message_id or (r.conversation_id is not None
                                                         and run.conversation_id == r.conversation_id))
                 and in_window(r.created_at, run) and action_matches(r.kind, r.target, r.value, run.actions)]
        if cands:
            cands.sort(key=lambda run: (run.message_id != r.message_id,
                                        abs((r.created_at - (run.finished_at or run.created_at)).total_seconds()),
                                        run.id))
            out.append((r, cands[0]))
    return out


async def reattribute(uid: int, *, dry_run: bool, batch: int = 500) -> Report:
    """Find (dry_run) or re-record one user's order decisions stored as theirs. Each batch is one
    transaction; a second run finds nothing."""
    rep = Report()
    changed: set[tuple[str, str]] = set()
    after = 0
    async with sessionmaker()() as db:
        names = {str(i): n for i, n in (await db.execute(
            select(Label.id, Label.name).where(Label.user_id == uid))).all()}
    while True:
        async with sessionmaker()() as db:
            if not dry_run:
                await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                                 {"k": f"decisions.reattribute-orders:{uid}"})
            rows = await _batch(db, uid, after, batch)
            if not rows:
                break
            after = rows[-1].id
            rep.examined += len(rows)
            found = await _match(db, uid, rows)
            orders = {o.id: o for o in (await db.execute(select(AssistantOrder).where(
                AssistantOrder.id.in_({run.order_id for _r, run in found if run.order_id is not None} or {-1}))
            )).scalars()}
            values = []
            for r, run in found:
                ref, why, conf = _reason(run, orders.get(run.order_id) if run.order_id is not None else None)
                rep.matched += 1
                rep.other_messages += run.message_id != r.message_id
                rep.by_order[ref] += 1
                rep.by_target[(str(r.kind), str(names.get(r.target, r.target) if r.kind == "label" else r.target))] += 1
                changed.add((r.kind, r.target))
                values.append({"d_id": r.id, "ref": ref, "why": why, "conf": conf})
            if not dry_run and values:
                t = cast(Table, LabelDecision.__table__)
                await db.execute(
                    update(t).where(t.c.id == bindparam("d_id"), t.c.source == "user", t.c.reason == "user action")
                    .values(source="order", source_ref=bindparam("ref"), reason=bindparam("why"),
                            confidence=bindparam("conf"), status="applied"),
                    values)
                await db.commit()
                log.info("decisions reattribute-orders: user %s, %s decisions recorded as standing orders' "
                         "(through decision %s)", uid, len(values), after)
            else:
                await db.rollback()
    if not dry_run and rep.matched:
        rep.retrains = _retrain_targets(changed)
        async with sessionmaker()() as db:
            for kind, target in rep.retrains:
                await enqueue(db, "intel.retrain", {"kind": kind, "target": target}, user_id=uid,
                              dedupe_key=f"intel.retrain:{uid}:{kind}:{target}", run_at=datetime.now(UTC))
            db.add(AuditLog(user_id=uid, token_id=None, actor="cli", action="decisions.reattribute_orders",
                            target=f"user {uid}", inputs={"batch": batch},
                            result={"examined": rep.examined, "reattributed": rep.matched,
                                    "kept": rep.kept, "by_order": dict(rep.by_order.most_common(50)),
                                    "retrains": [f"{k}:{t}" for k, t in rep.retrains]}, ok=True))
            await db.commit()
        log.info("decisions reattribute-orders: user %s, %s of %s user decisions were standing orders'; "
                 "%s retrains queued", uid, rep.matched, rep.examined, len(rep.retrains))
    return rep


def _retrain_targets(changed: set[tuple[str, str]]) -> list[tuple[str, str]]:
    """One retrain per model: each label's own, the importance model, the category model (one model
    for all categories, so one job)."""
    out = sorted({(k, t) for k, t in changed if k == "label"}, key=lambda x: int(x[1]) if x[1].isdigit() else 0)
    if any(k == "importance" for k, _t in changed):
        out.append(("importance", "important"))
    cats = sorted(t for k, t in changed if k == "category")
    if cats:
        out.append(("category", cats[0]))
    return out


def describe(email: str, rep: Report, *, dry_run: bool) -> list[str]:
    verb = "would be" if dry_run else "were"
    lines = [f"{email}: {rep.examined} decisions recorded as the user's, {rep.matched} {verb} a standing "
             f"order's ({rep.other_messages} of them on another message of the run's conversation), "
             f"{rep.kept} stay the user's"]
    for name, n in rep.by_order.most_common():
        lines.append(f"  order {name}: {n}")
    for (kind, target), n in rep.by_target.most_common():
        lines.append(f"  {kind} {target}: {n}")
    if rep.retrains:
        lines.append("  retrain queued: " + ", ".join(f"{k} {t}" for k, t in rep.retrains))
    return lines
