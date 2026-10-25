"""Deterministic rules (Gmail filters).

A rule's `query` uses the same Gmail search syntax as the search box, compiled by
core/search.py, so "Create filter from search" always matches what the search showed.
Rules run in `position` order on every newly ingested message, before any model.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.search import compile_query, query_zone
from mailr.models import Label, LabelDecision, Message, Person, Rule

log = logging.getLogger("mailr.rules")

ACTION_KEYS = {
    "add_labels", "remove_labels", "archive", "mark_read", "star", "important", "category",
    "spam", "never_spam", "trash", "forward_to",
}


def validate_actions(actions: dict[str, Any]) -> dict[str, Any]:
    bad = set(actions) - ACTION_KEYS
    if bad:
        raise ValueError(f"unknown rule actions: {sorted(bad)}")
    return actions


def scope_of(p) -> dict[str, Any] | None:
    """The visibility a restricted token imposes on rules it creates (None: no restriction)."""
    from mailr.core.visibility import is_restricted

    if p is None or not is_restricted(p):
        return None
    return {"connector_ids": sorted(int(x) for x in p.token.connector_ids or []),
            "local_only": bool(p.token.local_only)}


def scope_condition(*scopes: dict[str, Any] | None):
    """SQL condition on Message for rule scopes (AND of all given); None when unrestricted."""
    from sqlalchemy import and_

    conds = []
    for sc in scopes:
        if not sc:
            continue
        if sc.get("connector_ids"):
            conds.append(Message.connector_id.in_([int(x) for x in sc["connector_ids"]]))
        if not sc.get("local_only"):
            conds.append(Message.local_only.is_(False))
    return and_(*conds) if conds else None


def merge_scope(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any] | None:
    """The narrower of two scopes (both apply)."""
    if not a or not b:
        return a or b
    ca, cb = set(a.get("connector_ids") or []), set(b.get("connector_ids") or [])
    conns = sorted(ca & cb) if ca and cb else sorted(ca or cb)
    if ca and cb and not conns:
        conns = [-1]  # disjoint: matches nothing
    return {"connector_ids": conns, "local_only": bool(a.get("local_only")) and bool(b.get("local_only"))}


async def _matching(db: AsyncSession, user_id: int, query: str, labels: list[Label] | None, extra):
    labels = labels if labels is not None else await user_labels(db, user_id)
    c = compile_query(query, user_id, labels, zone=await query_zone(db, user_id, query))
    stmt = select(Message.id).where(Message.user_id == user_id, Message.deleted.is_(False), c.where)
    return stmt.where(extra) if extra is not None else stmt


async def matching_message_ids(
    db: AsyncSession, user_id: int, query: str, message_ids: list[int] | None = None,
    labels: list[Label] | None = None, limit: int | None = None, extra=None,
) -> list[int]:
    stmt = await _matching(db, user_id, query, labels, extra)
    if message_ids is not None:
        stmt = stmt.where(Message.id.in_(message_ids))
    if limit:
        stmt = stmt.order_by(Message.date.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars())


async def count_matching(db: AsyncSession, user_id: int, query: str, extra=None) -> int:
    """How many messages a rule matches, counted in the database (not loaded)."""
    stmt = await _matching(db, user_id, query, None, extra)
    return int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())


async def user_labels(db: AsyncSession, user_id: int) -> list[Label]:
    return list((await db.execute(select(Label).where(Label.user_id == user_id))).scalars())


async def plan_for_message(db: AsyncSession, m: Message) -> dict[str, Any]:
    """Evaluate all rules (and blocked senders) against one message.

    Returns merged actions: {"add_labels": set, "remove_labels": set, "archive": bool, ...,
    "_rules": [rule ids that matched]}. Blocked senders act like a spam rule.
    """
    plan: dict[str, Any] = {"add_labels": set(), "remove_labels": set(), "_rules": []}
    blocked = (
        await db.execute(
            select(Person.id).where(
                Person.user_id == m.user_id, Person.email == m.from_email, Person.blocked.is_(True)
            )
        )
    ).first()
    if blocked:
        plan["spam"] = True
        plan["_blocked"] = True
    rules = (
        await db.execute(
            select(Rule).where(Rule.user_id == m.user_id, Rule.enabled.is_(True)).order_by(Rule.position, Rule.id)
        )
    ).scalars().all()
    if not rules:
        return plan
    labels = await user_labels(db, m.user_id)
    hits = await _evaluate(db, m, rules, labels)
    for r in rules:
        if not hits.get(r.id):
            continue
        plan["_rules"].append(r.id)
        a = r.actions or {}
        plan["add_labels"].update(a.get("add_labels") or [])
        plan["remove_labels"].update(a.get("remove_labels") or [])
        for k in ("archive", "mark_read", "star", "spam", "never_spam", "trash"):
            if a.get(k):
                plan[k] = True
        if a.get("important") is not None:
            plan["important"] = bool(a["important"])
        if a.get("category"):
            plan["category"] = a["category"]
        if a.get("forward_to"):
            plan.setdefault("forward_to", []).append(a["forward_to"])
        if r.stop:
            break
    if plan.get("never_spam"):
        plan.pop("spam", None)
    if plan["_rules"]:
        await db.execute(update(Rule).where(Rule.id.in_(plan["_rules"])).values(hits=Rule.hits + 1))
    return plan


async def _evaluate(db: AsyncSession, m: Message, rules: list[Rule], labels: list[Label]) -> dict[int, bool]:
    """All rules in ONE query: `SELECT (rule1) AS r1, (rule2) AS r2 ... FROM messages WHERE id=:id`.
    Each attempt runs in a savepoint, so a rule that fails in the database cannot abort the
    ingest transaction; if the batch fails, rules are evaluated one by one to isolate it."""
    from sqlalchemy import case

    compiled = []
    from sqlalchemy import and_

    for r in rules:
        try:
            cond = compile_query(r.query, m.user_id, labels, zone=await query_zone(db, m.user_id, r.query)).where
            sc = scope_condition(getattr(r, "scope", None))
            compiled.append((r.id, and_(cond, sc) if sc is not None else cond))
        except Exception as e:  # noqa: BLE001  a broken rule must not block ingestion
            log.warning("rule %s does not compile: %s", r.id, e)

    async def run(items):
        stmt = select(*[case((cond, True), else_=False).label(f"r{rid}") for rid, cond in items]).where(
            Message.id == m.id)
        async with db.begin_nested():
            row = (await db.execute(stmt)).first()
        return {rid: bool(row[i]) for i, (rid, _) in enumerate(items)} if row else {}

    if not compiled:
        return {}
    try:
        return await run(compiled)
    except Exception as e:  # noqa: BLE001
        log.warning("batched rule evaluation failed (%s); isolating the broken rule", e)
    out: dict[int, bool] = {}
    for item in compiled:
        try:
            out.update(await run([item]))
        except Exception as e:  # noqa: BLE001
            log.warning("rule %s failed: %s", item[0], e)
    return out


def decisions_for_plan(m: Message, plan: dict[str, Any]) -> list[LabelDecision]:
    """Record why rules changed a message, so the UI can answer 'why is this here?'."""
    ref = ",".join(str(r) for r in plan.get("_rules", []))
    out = []
    for lid in plan.get("add_labels", ()):
        out.append(LabelDecision(user_id=m.user_id, message_id=m.id, kind="label", target=str(lid),
                                 value=True, source="rule", source_ref=ref, confidence=1.0,
                                 reason="matched a filter"))
    if "important" in plan:
        out.append(LabelDecision(user_id=m.user_id, message_id=m.id, kind="importance",
                                 target="important", value=plan["important"], source="rule",
                                 source_ref=ref, confidence=1.0, reason="matched a filter"))
    if plan.get("category"):
        out.append(LabelDecision(user_id=m.user_id, message_id=m.id, kind="category",
                                 target=plan["category"], source="rule", source_ref=ref,
                                 confidence=1.0, reason="matched a filter"))
    if plan.get("spam"):
        out.append(LabelDecision(user_id=m.user_id, message_id=m.id, kind="spam", target="spam",
                                 source="rule", source_ref=ref or "blocked", confidence=1.0,
                                 reason="blocked sender" if plan.get("_blocked") else "matched a filter"))
    return out
