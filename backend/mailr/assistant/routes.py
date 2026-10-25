"""HTTP API for standing orders, routines and the assistant's activity.

Signed-in users manage orders directly (this is the Settings page); the assistant changes
them through the `manage_order` tool, which asks first (orders.write).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.assistant import orders as ord_
from mailr.core.audit import audit
from mailr.core.auth import Principal, scope
from mailr.core.visibility import (
    conversation_visible,
    is_restricted,
    redact_run,
    sees_all_mail,
    visible_conversation_ids,
    visible_message_filter,
)
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import AssistantOrder, AssistantRun, Message

router = APIRouter(tags=["assistant"])


class OrderIn(BaseModel):
    name: str | None = Field(None, max_length=200)
    trigger: str | None = None
    schedule: str | None = Field(None, max_length=120)
    timezone: str | None = Field(None, max_length=64)
    query: str | None = Field(None, max_length=2000)
    question: str | None = Field(None, max_length=1000)
    # A choice order: options the decision model picks among, each with its own actions.
    choices: list[dict[str, Any]] | None = Field(None, max_length=8)
    threshold: float | None = None
    actions: dict[str, Any] | None = None
    errand: str | None = Field(None, max_length=4000)
    deliver: list[str] | None = None
    enabled: bool | None = None
    # Speakr recordings: a recording order's filter or a routine's recording source.
    recordings: dict[str, Any] | None = None
    # A message order only for mail the user forwards from a trusted address (core/trusted.py).
    from_trusted: bool | None = None
    # A message order only for mail from the user or one of their trusted addresses (core/trusted.from_user).
    from_me: bool | None = None
    # The built-in order "Follow my forwarded notes": ask | allow (assistant/forwards.py).
    permission: str | None = Field(None, pattern="^(ask|allow)$")


class MoveIn(BaseModel):
    direction: str = Field(pattern="^(up|down)$")


def _session_only(p: Principal) -> None:
    if p.is_token:
        raise ApiError(403, "assistant.orders_in_app")


async def _assistant_on(db: AsyncSession, p: Principal) -> None:
    """Standing orders and routines are the assistant's: off when the access policy turns the
    assistant off (core/access.py). Listing and deleting still work."""
    from mailr.core.access import require_feature

    await require_feature(db, p.user, "assistant")


async def _op(db: AsyncSession, p: Principal, payload: dict[str, Any]) -> dict[str, Any]:
    if payload["op"] != "delete":
        await _assistant_on(db, p)
    try:
        res = await ord_.apply_order_op(db, p.user, payload)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    await audit(db, p, f"assistant.orders.{payload['op']}", str(payload.get("order_id") or res.get("id", "")),
                {k: v for k, v in payload.items() if k != "op"})
    await db.commit()
    return res


@router.get("/assistant/orders")
async def list_orders(p: Principal = Depends(scope("rules")), db: AsyncSession = Depends(get_db)):
    if not p.is_token:
        # The built-in order exists for everyone (seeded here for users made after its migration).
        await ord_.ensure_builtin(db, p.user)
        await db.commit()
    rows = (await db.execute(select(AssistantOrder).where(AssistantOrder.user_id == p.user_id)
                             .order_by(AssistantOrder.trigger, AssistantOrder.position, AssistantOrder.id))).scalars().all()
    return {"items": [await ord_.order_out_full(db, p.user, o) for o in rows]}


# ACT order.create @api
@router.post("/assistant/orders")
async def create_order(body: OrderIn, p: Principal = Depends(scope("rules")), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    return await _op(db, p, {"op": "create", "order": body.model_dump(exclude_none=True), "source": "user"})


# ACT order.edit @api, ACT order.pause @api, ACT order.permission @api
@router.patch("/assistant/orders/{oid}")
async def update_order(oid: int, body: OrderIn, p: Principal = Depends(scope("rules")), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    return await _op(db, p, {"op": "update", "order_id": oid, "order": body.model_dump(exclude_none=True)})


# ACT order.move @api
@router.post("/assistant/orders/{oid}/move")
async def move_order(oid: int, body: MoveIn, p: Principal = Depends(scope("rules")), db: AsyncSession = Depends(get_db)):
    """One place up or down among the orders of its kind: when several orders match one email, the
    top one acts first."""
    _session_only(p)
    return await _op(db, p, {"op": "move", "order_id": oid, "direction": body.direction})


# ACT order.delete @api
@router.delete("/assistant/orders/{oid}")
async def delete_order(oid: int, p: Principal = Depends(scope("rules")), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    return await _op(db, p, {"op": "delete", "order_id": oid})


async def _own_order(db: AsyncSession, p: Principal, oid: int) -> AssistantOrder:
    o = await db.get(AssistantOrder, oid)
    if o is None or o.user_id != p.user_id:
        raise ApiError(404, "assistant.order_not_found")
    return o


class RunIn(BaseModel):
    conversation_id: int | None = None
    conversation_ids: list[int] | None = Field(None, max_length=25)  # message orders: run on these


# ACT order.run @api
@router.post("/assistant/orders/{oid}/run")
async def run_order_now(oid: int, body: RunIn | None = None, p: Principal = Depends(scope("rules")),
                        db: AsyncSession = Depends(get_db)):
    """Run now: a routine runs immediately; a message order runs on the given conversation's
    latest message (conditions still apply); a recording order runs on the newest completed
    recording its filter matches, once per recording."""
    _session_only(p)
    await _assistant_on(db, p)
    o = await _own_order(db, p, oid)
    ids = (body.conversation_ids if body else None) or []
    if ids and o.trigger == "message":
        labels = await ord_._labels(db, p.user_id)
        tally = {"queued": 0, "done": 0, "skipped": 0, "already": 0, "no_match": 0}
        for cid in dict.fromkeys(ids):
            m = await _latest_message(db, p, cid)
            r = await ord_.run_message_order(db, p.user, o, m, labels, trigger="manual")
            if r is None:
                tally["already" if await _already_ran(db, o.id, m.id) else "no_match"] += 1
            else:
                tally[r.status if r.status in tally else "done"] += 1
        await audit(db, p, "assistant.orders.run", str(oid), {"conversation_ids": ids})
        await db.commit()
        return {"matched": True, **tally}
    if o.trigger == "recording":
        # ACT order.run (a recording order: the newest recording its filter matches)
        from mailr.integrations.speakr import orders as rec_orders

        try:
            out = await rec_orders.run_latest(db, p, o)
        except LookupError as e:
            raise HTTPException(400, str(e)) from None
        run = out.pop("run", None)
        await audit(db, p, "assistant.orders.run", str(oid), {"recording": out["recording"]})
        await db.commit()
        return {**out, "run": ord_.run_out(run, o.name) if run else None}
    if o.trigger == "schedule":
        run = await ord_.start_routine(db, p.user, o, f"manual:{datetime.now(UTC).isoformat()}", trigger="manual")
    else:
        m = await _latest_message(db, p, (body or RunIn()).conversation_id)
        run = await ord_.run_message_order(db, p.user, o, m, await ord_._labels(db, p.user_id), trigger="manual")
        if run is None:
            await db.commit()
            return {"matched": False, "reason": "the order's search does not match this conversation"}
        if run.slot and run.id is None:
            raise ApiError(409, "assistant.order_already_ran")
    await audit(db, p, "assistant.orders.run", str(oid), {"conversation_id": (body or RunIn()).conversation_id})
    await db.commit()
    return {"matched": True, "run": ord_.run_out(run, o.name) if run else None}


@router.post("/assistant/orders/{oid}/test")
async def test_order(oid: int, body: RunIn, p: Principal = Depends(scope("rules")), db: AsyncSession = Depends(get_db)):
    """Would this order act on this conversation? Answers the condition; changes nothing."""
    await _assistant_on(db, p)
    o = await _own_order(db, p, oid)
    m = await _latest_message(db, p, body.conversation_id)
    run = await ord_.run_message_order(db, p.user, o, m, await ord_._labels(db, p.user_id), trigger="test", dry_run=True)
    await db.rollback()
    if run is None:
        return {"matched": False, "reason": "the order's search does not match this conversation"}
    return {"matched": run.status != "skipped", "condition": run.condition if sees_all_mail(p) else {},
            "would": run.actions}


class PreviewIn(OrderIn):
    scope: str = "inbox"  # inbox | all
    limit: int = 15


# ACT order.test @api
@router.post("/assistant/orders/preview")
async def preview_order(body: PreviewIn, p: Principal = Depends(scope("rules")), db: AsyncSession = Depends(get_db)):
    """Test an order (saved or not) on recent mail: the newest conversations its search matches
    in the inbox (or all mail), each with the answer to its question. Changes nothing."""
    from mailr.core.publicid import ConvId

    await _assistant_on(db, p)
    if body.trigger == "recording":
        from mailr.integrations.speakr import orders as rec_orders

        try:
            out = await rec_orders.preview(db, p, body.recordings or {}, (body.question or "").strip(),
                                           body.threshold or 0.7, max(1, min(body.limit, 25)))
        except (LookupError, ValueError) as e:
            raise HTTPException(400, str(e)) from None
        await db.rollback()
        return {"scope": "recordings", **out}
    if not (body.query or "").strip() and not (body.question or "").strip() and not body.from_trusted \
            and not body.from_me:
        raise ApiError(400, "assistant.order_test_needs_query")
    limit = max(1, min(body.limit, 25))
    base = "in:inbox" if body.scope == "inbox" else "in:all"
    try:
        cids = await ord_.routine_conversations(db, p.user, f"{base} {body.query or ''}".strip(), limit,
                                                visible=visible_message_filter(p))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    labels_ids = {lb.id for lb in await ord_._labels(db, p.user_id)}
    try:
        choices = ord_._clean_choices(body.choices, labels_ids) if body.choices else []
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    order = AssistantOrder(user_id=p.user_id, query="", question=(body.question or "").strip(), choices=choices,
                           threshold=body.threshold or 0.7, actions={}, errand="",
                           from_trusted=bool(body.from_trusted), from_me=bool(body.from_me), builtin="")
    labels = await ord_._labels(db, p.user_id)
    items = []
    for cid in cids:
        m = await _latest_message(db, p, cid)
        run = await ord_.run_message_order(db, p.user, order, m, labels, trigger="test", dry_run=True)
        cond = (run.condition if run else None) or {}
        items.append({"conversation_id": ConvId(cid), "subject": m.subject or "", "from": m.from_name or m.from_email,
                      "date": m.date.isoformat() if m.date else None,
                      "matched": bool(run and run.status != "skipped"),
                      **({"answer": cond.get("answer"), "confidence": cond.get("confidence"),
                          "reason": cond.get("reason") if sees_all_mail(p) else None}
                         if order.question else {}),
                      **({"choice": next((c["name"] for c in choices if c["key"] == cond.get("choice")), None)}
                         if choices else {})})
    await db.rollback()
    return {"scope": body.scope, "searched": len(cids), "matched": sum(i["matched"] for i in items), "items": items}


async def _already_ran(db: AsyncSession, oid: int, mid: int) -> bool:
    return (await db.execute(select(AssistantRun.id).where(
        AssistantRun.order_id == oid, AssistantRun.slot == f"m{mid}"))).first() is not None


async def _latest_message(db: AsyncSession, p: Principal, cid: int | None) -> Message:
    if not cid:
        raise ApiError(400, "assistant.choose_conversation")
    m = (await db.execute(select(Message).where(
        Message.conversation_id == int(cid), Message.user_id == p.user_id, Message.deleted.is_(False),
        Message.draft.is_(False), visible_message_filter(p)).order_by(Message.date.desc().nulls_last())
        .limit(1))).scalar_one_or_none()
    if m is None:
        raise ApiError(404, "mail.conversation_not_found")
    return m


@router.get("/assistant/runs")
async def list_runs(order_id: int | None = None, conversation_id: int | None = None, limit: Annotated[int, Query(ge=1, le=200)] = 50,
                    p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    q = select(AssistantRun, AssistantOrder.name, AssistantOrder.builtin).outerjoin(AssistantOrder, AssistantOrder.id == AssistantRun.order_id) \
        .where(AssistantRun.user_id == p.user_id)
    if order_id:
        q = q.where(AssistantRun.order_id == order_id)
    if conversation_id:
        q = q.where(AssistantRun.conversation_id == conversation_id)
    if is_restricted(p):
        # A restricted token sees runs only on conversations it can see, and never their text
        # (core/visibility.py). Routines have no conversation and summarise all mail: left out.
        q = q.where(AssistantRun.conversation_id.in_(visible_conversation_ids(p)))
    rows = (await db.execute(q.order_by(AssistantRun.id.desc()).limit(min(max(limit, 1), 200)))).all()
    return {"items": [redact_run(p, ord_.run_out(r, name, builtin)) for r, name, builtin in rows]}


# ACT brief.read @api
@router.get("/conversations/{cid}/brief")
async def conversation_brief(cid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    from mailr.assistant.errands import latest_brief
    from mailr.models import Conversation

    conv = await db.get(Conversation, cid)
    if conv is None or not await conversation_visible(db, p, cid):
        raise ApiError(404, "mail.conversation_not_found")
    if not sees_all_mail(p):
        return {"brief": None}  # a brief can draw on mail the token may not see
    from sqlalchemy import text

    from mailr.core.spamtrash import live_sql

    if not (await db.execute(text(
            f"SELECT 1 FROM messages m WHERE m.conversation_id = :c AND NOT m.deleted AND {live_sql('m')} LIMIT 1"),
            {"c": cid})).first():
        return {"brief": None}  # a conversation all in Spam or Trash shows no brief
    run = await latest_brief(db, p.user_id, cid)
    if run is None:
        return {"brief": None}
    o = await db.get(AssistantOrder, run.order_id) if run.order_id else None
    return {"brief": {**ord_.run_out(run, o.name if o else None, o.builtin if o else None)}}
