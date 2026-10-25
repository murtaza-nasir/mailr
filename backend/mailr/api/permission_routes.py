"""Permission rules and approvals."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.agent.present import approval_card
from mailr.core import permissions, publicid
from mailr.core.audit import audit
from mailr.core.auth import Principal, current
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import ApiToken, PendingAction, PermissionRule

router = APIRouter(tags=["permissions"])


def _session_only(p: Principal) -> None:
    if p.is_token:
        raise ApiError(403, "permissions.user_only")


def rule_out(r: PermissionRule) -> dict[str, Any]:
    return {"id": r.id, "subject": r.subject, "action": r.action, "effect": r.effect,
            "constraints": r.constraints or {}, "source": r.source,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None}


class RuleIn(BaseModel):
    subject: str = Field(pattern=r"^(\*|assistant|token:\d+|session:[A-Za-z0-9_-]{1,64})$")
    action: str
    effect: str = Field(pattern="^(allow|ask|deny)$")
    constraints: dict[str, Any] = {}
    expires_at: datetime | None = None


class RulePatch(BaseModel):
    effect: str | None = Field(None, pattern="^(allow|ask|deny)$")
    constraints: dict[str, Any] | None = None
    expires_at: datetime | None = None


def _validate(action: str, constraints: dict[str, Any]) -> None:
    ok = action == "*" or action in permissions.CLASSES or (
        action.endswith(".*") and any(c.startswith(action[:-1]) for c in permissions.CLASSES))
    if not ok:
        raise HTTPException(400, f"unknown action class {action}")  # i18n: internal
    if action.endswith(".read"):
        raise HTTPException(400, "reading is limited by token scopes and account / local-only limits, not by "  # i18n: internal
                                 "permission rules")
    bad = set(constraints) - permissions.CONSTRAINT_KEYS
    if bad:
        raise HTTPException(400, f"unknown constraints: {sorted(bad)}")  # i18n: internal


async def _check_subject(db: AsyncSession, p: Principal, subject: str) -> None:
    if subject.startswith("token:"):
        t = await db.get(ApiToken, int(subject.split(":", 1)[1]))
        if t is None or t.user_id != p.user_id:
            raise ApiError(400, "tokens.unknown_token")


@router.get("/permissions")
async def list_rules(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    rows = (await db.execute(select(PermissionRule).where(PermissionRule.user_id == p.user_id)
                             .order_by(PermissionRule.subject, PermissionRule.action))).scalars()
    return {"classes": [{"action": a, "default": d, "scope": s,
                         "assistant_default": permissions.default_effect(a, "assistant")}
                        for a, (d, s) in permissions.CLASSES.items() if a not in permissions.ORDER_CLASSES],
            "constraints": sorted(permissions.CONSTRAINT_KEYS),
            "rules": [rule_out(r) for r in rows]}


@router.post("/permissions")
async def create_rule(body: RuleIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    _validate(body.action, body.constraints)
    await _check_subject(db, p, body.subject)
    r = PermissionRule(user_id=p.user_id, subject=body.subject, action=body.action, effect=body.effect,
                       constraints=body.constraints, source="user", expires_at=body.expires_at)
    db.add(r)
    await db.flush()
    await audit(db, p, "permissions.create", str(r.id), body.model_dump(mode="json"), always=True)
    await db.commit()
    return rule_out(r)


@router.patch("/permissions/{rid}")
async def patch_rule(rid: int, body: RulePatch, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    r = await db.get(PermissionRule, rid)
    if r is None or r.user_id != p.user_id:
        raise ApiError(404, "permissions.rule_not_found")
    if body.effect is not None:
        r.effect = body.effect
    if body.constraints is not None:
        _validate(r.action, body.constraints)
        r.constraints = body.constraints
    if "expires_at" in body.model_fields_set:
        r.expires_at = body.expires_at
    await audit(db, p, "permissions.update", str(rid), body.model_dump(mode="json"), always=True)
    await db.commit()
    return rule_out(r)


@router.delete("/permissions/{rid}")
async def delete_rule(rid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    r = await db.get(PermissionRule, rid)
    if r is None or r.user_id != p.user_id:
        raise ApiError(404, "permissions.rule_not_found")
    await db.delete(r)
    await audit(db, p, "permissions.delete", str(rid), always=True)
    await db.commit()
    return {"ok": True}


@router.get("/permissions/effective")
async def effective(subject: str = "assistant", p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    return {"subject": subject, "items": await permissions.effective(db, p.user, subject)}


@router.get("/approvals")
async def list_approvals(status: str = "pending", limit: Annotated[int, Query(ge=1, le=500)] = 100, p: Principal = Depends(current),
                         db: AsyncSession = Depends(get_db)):
    stmt = select(PendingAction).where(PendingAction.user_id == p.user_id)
    if status != "all":
        stmt = stmt.where(PendingAction.status.in_(status.split(",")))
    if p.token is not None:
        stmt = stmt.where(PendingAction.token_id == p.token.id)  # an agent sees only its own requests
    rows = (await db.execute(stmt.order_by(PendingAction.id.desc()).limit(min(limit, 500)))).scalars().all()
    # Each card with its display model and outcome (agent/present.py).
    return [await approval_card(db, pa, covers=p.token is None) for pa in rows]


async def _approval(db: AsyncSession, p: Principal, aid: str) -> PendingAction:
    """The approval an id in the path names (public number, a… short form, or an old internal id
    as permissions.approval_from_ref allows); 404 for a missing or foreign one, or an agent's
    look at another token's request."""
    pa = await permissions.approval_from_ref(db, p.user_id, aid)
    if pa is None or (p.token is not None and pa.token_id != p.token.id):
        raise ApiError(404, "permissions.approval_not_found")
    return pa


@router.get("/approvals/{aid}")
async def get_approval(aid: str, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    return await approval_card(db, await _approval(db, p, aid), covers=p.token is None)


class DecideIn(BaseModel):
    decision: str = Field(pattern="^(approve|deny)$")
    scope: str = Field("once", pattern="^(once|session|always)$")
    constraint: str = Field("none", pattern="^(none|derived)$")
    # A review card or cleanup plan approved with some conversations left out (agent/review.py narrow):
    # the approval runs on what the card showed less these, never on anything else.
    except_ids: list[int] = Field(default_factory=list, max_length=10_000)
    # A review card approved with the user's own moves (agent/review.py apply_changes): conversation id
    # (public form) -> "trash", "archive", "read", "keep" or "label:<label id>". Only conversations the
    # card showed, and only labels the user owns.
    changes: dict[str, str] = Field(default_factory=dict, max_length=10_000)


# ACT approval.approve @api, ACT approval.deny @api
@router.post("/approvals/{aid}/decide")
async def decide(aid: str, body: DecideIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    found = await _approval(db, p, aid)
    op = (found.payload or {}).get("op")
    if body.decision == "approve" and op == "review" and (body.except_ids or body.changes):
        await _review_changes(db, p, found, body)
    elif body.except_ids and body.decision == "approve" and op == "cleanup":
        from mailr.agent.review import narrow

        if found.status != "pending":
            raise HTTPException(409, "this request was already decided")  # i18n: internal
        found.payload, found.preview = narrow(found.payload, found.preview or {}, body.except_ids)
        await db.flush()
    res = await permissions.decide_full(db, p.user, found.id, body.decision, body.scope, body.constraint)
    pa = res.pa
    also = res.also or []
    await audit(db, p, f"approvals.{body.decision}", str(pa.id),
                {**body.model_dump(), **({"rule_id": res.rule_id, "also": also} if res.rule_id else {})}, always=True)
    await db.commit()
    # The other waiting requests the new rule decided as well (ids in public form), and the
    # rule, for the toast's Undo (POST /approvals/grants/{rule_id}/undo).
    return {**await approval_card(db, pa), "rule_id": res.rule_id,
            "also_decided": {"ids": [publicid.ApprovalId(i) for i in also], "count": len(also)}}


async def _review_changes(db: AsyncSession, p: Principal, found: PendingAction, body: DecideIn) -> None:
    """The user's own moves on a review card (and except_ids, the same as "keep"): checked, then the plan
    and the card are rebuilt from what the card showed (agent/review.py apply_changes). Each sender's
    changed move is remembered for later reviews (agent/review.py learn)."""
    from mailr.agent.present import enrich_preview
    from mailr.agent.review import apply_changes, choices_from, learn, parse_move
    from mailr.models import Label

    if found.status != "pending":
        raise HTTPException(409, "this request was already decided")  # i18n: internal
    changes: dict[int, str] = {int(i): "keep" for i in body.except_ids}
    for k, v in body.changes.items():
        try:
            changes[publicid.decode("conversation", k)] = v
        except publicid.InvalidId:
            raise HTTPException(400, "a change names a conversation that is not on this review card") from None  # i18n: internal
    try:
        wanted = {lid for m in changes.values() for a, lid in [parse_move(m)] if a == "label"}
    except ValueError as e:
        raise HTTPException(400, str(e)) from None  # i18n: internal
    names = {lb.id: lb.name for lb in (await db.execute(select(Label).where(
        Label.user_id == p.user_id, Label.role.is_(None), Label.id.in_(wanted)))).scalars()} if wanted else {}
    if wanted - set(names):
        raise HTTPException(400, "a change names a label you do not have")  # i18n: internal
    try:
        picks = choices_from(found.payload or {}, found.preview or {}, changes, names)
        payload, preview = apply_changes(found.payload or {}, found.preview or {}, changes, names)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None  # i18n: internal
    await learn(db, p.user_id, picks)
    found.payload = payload
    found.preview = await enrich_preview(db, p.user_id, found.action, payload, preview)
    await db.flush()


class DecideManyIn(BaseModel):
    ids: list[str | int] = Field(min_length=1, max_length=50)
    decision: str = Field("approve", pattern="^(approve|deny)$")


def _requester(pa: PendingAction) -> str:
    """Who asked: one chat, else the agent token or the assistant (the grouping "Allow all" uses)."""
    return f"chat:{pa.chat_session_id}" if pa.chat_session_id else pa.subject


# ACT approval.approve_many @api
@router.post("/approvals/decide-many")
async def decide_many(body: DecideManyIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """"Allow all n from this chat": decide several waiting requests from one requester, each one
    as if its own card had been tapped once, with "Allow once". No rule is stored, so
    a hard rule that asks every time (the first message to a new address) is decided only for the
    requests listed. A request already decided is skipped and reported."""
    _session_only(p)
    found: list[PendingAction] = []
    for aid in dict.fromkeys(str(i) for i in body.ids):
        found.append(await _approval(db, p, aid))
    if len({_requester(pa) for pa in found}) > 1:
        raise ApiError(400, "permissions.different_sources")
    decided: list[PendingAction] = []
    skipped: list[dict[str, Any]] = []
    for pa in found:
        try:
            decided.append((await permissions.decide_full(db, p.user, pa.id, body.decision, "once", announce=False)).pa)
        except HTTPException as e:
            if e.status_code != 409:
                raise
            await db.refresh(pa)
            skipped.append({"id": publicid.ApprovalId(pa.id), "status": pa.status})
    # One approval.decided for the whole batch: one push takes down every notification.
    await permissions.announce_left(db, p.user_id, [pa.id for pa in decided],
                                    "approved" if body.decision == "approve" else "denied")
    await audit(db, p, f"approvals.{body.decision}_many", ",".join(str(pa.id) for pa in decided),
                {"decision": body.decision, "ids": [pa.id for pa in decided],
                 "skipped": [s["id"] for s in skipped]}, always=True)
    await db.commit()
    return {"decided": [await approval_card(db, pa) for pa in decided], "skipped": skipped,
            "count": len(decided)}


# ACT approval.undo_grant @api
@router.post("/approvals/grants/{rule_id}/undo")
async def undo_grant(rule_id: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Undo "Allow for 12 hours" / "Always allow" from a card: delete the rule and put back to
    waiting the other requests it approved that have not started. The card's own request stays
    decided. `kept` lists the ones already running or done."""
    _session_only(p)
    out = await permissions.undo_grant(db, p.user, rule_id)
    await audit(db, p, "approvals.undo_grant", str(rule_id), out, always=True)
    await db.commit()
    return {"rule_id": rule_id, "reverted": [publicid.ApprovalId(i) for i in out["reverted"]],
            "kept": [publicid.ApprovalId(i) for i in out["kept"]]}
