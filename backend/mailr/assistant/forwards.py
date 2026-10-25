"""The built-in standing order "Follow my forwarded notes" (core/trusted.py; user guide
site/docs/user-guide/forwarded-notes.md).

Every user has this order (AssistantOrder.builtin = "forwarded_notes", seeded on, Ask me, at the top
of Standing orders). It runs in the orders pass like any message order (assistant/orders.run_orders,
job "assistant.orders"): when fresh mail passes the trust rule, really forwards a message (an attached
email, or a forward marker or header block in one of the supported languages) and carries a note
above it, the order acts. A message that forwards nothing is ordinary mail, and a note acts once per
Message-ID: a copy re-sent with another To never runs again. Its permission is the class agent.forwarded_instructions, for Ambrose:

- Ask me (the default): an approval card shows the note; approving starts the run;
- Allow: the run starts at once;
- Off is the order turned off: the message is ordinary mail.

The run is an errand (assistant/errands.py) with trigger "forward": the note is the user's own
instruction, given as if they typed it in a chat about the email. Same tools and permission
classes; nobody is reading along, so mail it would send waits for approval (permissions
ERRAND_NO_SEND) and the forwarded content is untrusted from the start, as in such a chat. The
result is a brief on the conversation and a notification. Each change the run makes has its own
activity entry, with its Undo, and links to the forward (core/activity.VIA).

The trust rule is checked again when an approval runs: removing the address, or turning the order
off, stops a note that is still waiting.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import permissions, trusted
from mailr.core.jobs import enqueue, handler
from mailr.db import session_scope
from mailr.models import AssistantOrder, AssistantRun, Message, PendingAction, User

log = logging.getLogger("mailr.assistant.forwards")
TRIGGER = "forward"


async def _existing(db: AsyncSession, m: Message) -> AssistantRun | None:
    return (await db.execute(select(AssistantRun).where(
        AssistantRun.user_id == m.user_id, AssistantRun.trigger == TRIGGER,
        AssistantRun.message_id == m.id).limit(1))).scalar_one_or_none()


async def _waiting(db: AsyncSession, m: Message) -> int | None:
    """An approval card for this forward that waits."""
    return (await db.execute(select(PendingAction.id).where(
        PendingAction.user_id == m.user_id, PendingAction.action == trusted.ACTION,
        PendingAction.status == "pending", PendingAction.payload["message_id"].astext == str(m.id))
        .limit(1))).scalar_one_or_none()


def preview(m: Message, note: str, address: str) -> dict[str, Any]:
    """The approval card: the whole note the run gets (at most trusted.MAX_NOTE characters), never a
    shorter cut."""
    return {"title": "Act on your forwarded note", "kind": "forward", "note": note,
            "subject": (m.subject or "")[:200], "address": address,
            "lines": [f"Note: {note}", f"Subject: {(m.subject or '')[:200]}", f"From: {address}"]}


async def replayed(db: AsyncSession, m: Message) -> bool:
    """A note acts once per Message-ID: a message without one, or one whose
    Message-ID an earlier message of the user's already carries (a copy re-sent with another To),
    never acts."""
    if not (m.message_id_hdr or "").strip() or m.message_id_hdr.endswith("@mailr.synthetic>"):
        return True  # no Message-ID of its own (ingest made one up from the content)
    return (await db.execute(select(Message.id).where(
        Message.user_id == m.user_id, Message.message_id_hdr == m.message_id_hdr, Message.id < m.id,
        Message.draft.is_(False)).limit(1))).first() is not None


async def note_for(db: AsyncSession, user: User, m: Message) -> tuple[trusted.Verdict, str]:
    """(verdict, note) when the message is a note the user forwarded: it passes the trust rule, it
    really carries a forwarded message (an attached email, or a forward marker or header block;
    agent/own_links.own_note), it has a note, and its Message-ID was not acted on before. ("", note)
    otherwise: the message is ordinary mail."""
    v, note, fwd, _raw = await trusted.forwarded_note(db, user, m)
    if not v.ok or not note or not fwd or await replayed(db, m):
        return v, ""
    return v, note


async def start(db: AsyncSession, user: User, m: Message, note: str, address: str,
                order: AssistantOrder | None = None) -> AssistantRun | None:
    """Queue the errand that acts on the note (once per message)."""
    from mailr import i18n
    from mailr.assistant.orders import ERRANDS_PER_HOUR, _errands_last_hour
    from mailr.core import activity

    if await _existing(db, m) is not None:
        return None
    run = AssistantRun(user_id=user.id, order_id=order.id if order else None, trigger=TRIGGER, slot=f"m{m.id}",
                       message_id=m.id,
                       conversation_id=m.conversation_id, status="queued",
                       condition={"forward": {"from": address, "note": note, "subject": (m.subject or "")[:200]}})
    db.add(run)
    await db.flush()
    if order is not None:
        order.runs = (order.runs or 0) + 1
        order.last_run_at = datetime.now(UTC)
    if await _errands_last_hour(db, user.id) > ERRANDS_PER_HOUR:
        run.status, run.error = "skipped", f"more than {ERRANDS_PER_HOUR} errands in the last hour"
        run.finished_at = datetime.now(UTC)
        return run
    act = activity.entry("mail", i18n.msg("activity.mail.forward_note", what=activity.quoted(m.subject, "a_conversation")),
                         kind="conversation", ref=m.conversation_id, title=m.subject)
    await activity.add(db, user.id, "agent", "assistant.forward", str(m.conversation_id or ""), act,
                       inputs={"message_id": m.id, "run_id": run.id})
    await enqueue(db, "assistant.errand", {"run_id": run.id}, user_id=user.id,
                  dedupe_key=f"assistant.errand:{run.id}")
    return run


# ACT trusted.act_on_note @assistant
async def run(db: AsyncSession, user: User, order: AssistantOrder, m: Message, *,
              dry_run: bool = False) -> AssistantRun | None:
    """The built-in order on one message (assistant/orders.run_message_order). None when the
    message is not a note the user forwarded (ordinary mail), or when this note was handled before
    or waits on an approval card. A run with status "queued" when the errand starts; a transient
    (unsaved) run with status "pending" when an approval card was made."""
    v, note = await note_for(db, user, m)
    if not v.ok or not note:
        # A forward without a note, a message that forwards nothing, or a replay is ordinary mail
        # (another order may still match it).
        return None
    cond = {"from_trusted": True, "forward": {"from": v.address, "subject": (m.subject or "")[:200]}}
    if dry_run:
        return AssistantRun(user_id=user.id, order_id=order.id, trigger=TRIGGER, status="done", message_id=m.id,
                            conversation_id=m.conversation_id, condition=cond,
                            actions=[{"action": "errand", "would": True}])
    if await _existing(db, m) is not None or await _waiting(db, m) is not None:
        return None
    actor = permissions.Actor(user=user, kind="assistant")
    decision = await permissions.check(db, actor, trusted.ACTION, target=(m.subject or "")[:200], ctx={},
                                       payload={"message_id": m.id, "order_id": order.id},
                                       preview=preview(m, note, v.address))
    if isinstance(decision, permissions.Denied):
        return None
    if isinstance(decision, permissions.Pending):
        from mailr.core import publicid

        return AssistantRun(user_id=user.id, order_id=order.id, trigger=TRIGGER, status="pending", message_id=m.id,
                            conversation_id=m.conversation_id, condition=cond,
                            actions=[{"action": "errand", "result": "pending",
                                      "approval_id": publicid.encode("approval", decision.action.id)}])
    return await start(db, user, m, note, v.address, order)


async def skip_elsewhere(db: AsyncSession, user: User, order: AssistantOrder, m: Message,
                         addresses: list[str]) -> AssistantRun | None:
    """A note the user forwarded to an address another assistant handles: recorded as skipped, so the
    run log says why nothing happened. Ordinary mail there is left out of the log."""
    v, note = await note_for(db, user, m)
    if not v.ok or not note or await _existing(db, m) is not None:
        return None
    run = AssistantRun(user_id=user.id, order_id=order.id, trigger=TRIGGER, slot=f"m{m.id}", message_id=m.id,
                       conversation_id=m.conversation_id, status="skipped",
                       condition={"from_trusted": True, "elsewhere": addresses},
                       error=f"another assistant handles {', '.join(addresses)}", finished_at=datetime.now(UTC))
    db.add(run)
    await db.flush()
    return run


async def builtin_for(db: AsyncSession, user: User) -> AssistantOrder:
    from mailr.assistant.orders import ensure_builtin

    return await ensure_builtin(db, user)


@handler("assistant.forward")
async def forward_job(payload: dict[str, Any], ctx) -> None:
    """Jobs queued before the built-in order existed (0.31): the orders pass handles the message."""
    from mailr.assistant.orders import run_orders

    async with session_scope() as db:
        m = await db.get(Message, int(payload["message_id"]))
        user = await db.get(User, m.user_id) if m is not None else None
        if m is None or user is None or user.disabled or m.deleted or m.draft:
            return
        await builtin_for(db, user)
        await run_orders(db, user, m)
        await db.commit()


@permissions.executor(trusted.ACTION)
async def _approved(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    """The user approved acting on a forwarded note: check the order and the rule again, then start."""
    from mailr.assistant.orders import assistant_allowed

    m = await db.get(Message, int(payload.get("message_id") or 0))
    if m is None or m.user_id != user.id or m.deleted:
        return {"error": "the forwarded email is gone"}
    if not await assistant_allowed(db, user):
        return {"error": "the assistant is off for you"}
    order = await builtin_for(db, user)
    if not order.enabled:
        return {"error": "Follow my forwarded notes is turned off"}
    v, note = await note_for(db, user, m)
    if not v.ok or not note:
        return {"error": "the email no longer counts as your forwarded note (the address was removed?)"}
    run = await start(db, user, m, note, v.address, order)
    return {"run_id": run.id if run else None, "status": run.status if run else "done_before"}
