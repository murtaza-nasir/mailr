"""Standing orders and routines.

- `apply_order_op`: create / update / delete / pause orders (API, Settings and the
  `manage_order` tool after approval all go through it).
- `on_message` (job "assistant.orders"): after triage, run the user's message orders.
- `due_routines` (periodic): claim routines whose `next_run_at` passed; one run per slot.
- Errands (background agent work) run in assistant/errands.py.
- The built-in order "Follow my forwarded notes" (builtin="forwarded_notes", seeded per user) runs
  in the same pass as the user's own orders: assistant/forwards.run. It can be turned off and moved, never deleted or edited.

When several orders match one email, they run in list order (the top one first). An effect that
an earlier order already produced for the email is not produced again: one notification, one
"add the invitation" per invitation (by its UID; also when the event is already in the calendar or
an approval card for it waits). The run log names the order that acted (`result: duplicate`,
`by`). Mail addressed only to addresses another assistant handles (core/trusted.handled_elsewhere)
is left to it, unless an order's search names the address.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.jobs import enqueue, handler
from mailr.db import session_scope
from mailr.models import AssistantOrder, AssistantRun, Label, Message, User

log = logging.getLogger("mailr.assistant.orders")

TRIGGERS = ("message", "schedule", "recording")
DELIVER = ("brief", "notify", "digest")
INSTANT = ("add_labels", "remove_labels", "star", "important", "archive", "category", "mark_read", "notify",
           "add_to_calendar")
# Instant actions that only a message order has (they act on the one message, not on conversations).
MESSAGE_ONLY = ("add_to_calendar",)
CATEGORIES = ("primary", "social", "promotions", "updates", "forums", "purchases")
# Chain guard: background errands per user per hour; beyond it runs are recorded as skipped.
ERRANDS_PER_HOUR = 20
# Message orders only look at mail that just arrived, never at imports of old mail.
LIVE_WINDOW = timedelta(days=3)
ROUTINE_MAX_CONVERSATIONS = 40
# Built-in orders: key -> the name stored on the row (the apps show their own translated name).
BUILTIN_FORWARDS = "forwarded_notes"
BUILTINS = {BUILTIN_FORWARDS: "Follow my forwarded notes"}
# A built-in order's permission: Ask me or Allow (Off is the order turned off).
PERMISSIONS = ("ask", "allow")


# ---------------------------------------------------------------- schedule


def next_slot(cron: str, tz: str, after: datetime) -> datetime:
    """The first time after `after` that `cron` fires in time zone `tz` (returned in UTC)."""
    from cronsim import CronSim

    zone = ZoneInfo(tz or "UTC")
    it = CronSim(cron, after.astimezone(zone))
    return next(it).astimezone(UTC)


def validate_schedule(cron: str, tz: str) -> None:
    from cronsim import CronSimError

    try:
        ZoneInfo(tz or "UTC")
    except Exception:  # noqa: BLE001
        raise ValueError(f"unknown time zone: {tz}") from None
    try:
        a = next_slot(cron, tz, datetime.now(UTC))
        b = next_slot(cron, tz, a)
    except (CronSimError, StopIteration, ValueError) as e:
        raise ValueError(f"not a valid schedule (cron): {cron!r}") from e
    if b - a < timedelta(minutes=30):
        raise ValueError("a routine may run at most every 30 minutes")


# ---------------------------------------------------------------- validation and ops


async def _labels(db: AsyncSession, uid: int) -> list[Label]:
    return list((await db.execute(select(Label).where(Label.user_id == uid))).scalars())


async def clean(db: AsyncSession, user: User, data: dict[str, Any], existing: AssistantOrder | None = None) -> dict[str, Any]:
    """Validate an order's fields (raises ValueError with a message for the user)."""
    from mailr.core.search import compile_query

    base = {k: getattr(existing, k) for k in ("name", "trigger", "schedule", "timezone", "query", "question",
                                              "choices", "threshold", "actions", "errand", "deliver", "enabled",
                                              "recordings", "from_trusted", "from_me")} if existing else {
        "name": "", "trigger": "message", "schedule": "", "timezone": "", "query": "", "question": "", "choices": [],
        "threshold": 0.7, "actions": {}, "errand": "", "deliver": [], "enabled": True, "recordings": {},
        "from_trusted": False, "from_me": False}
    o = {**base, **{k: v for k, v in data.items() if k in base and v is not None}}
    o["name"] = str(o["name"] or "").strip()[:200]
    if o["trigger"] not in TRIGGERS:
        raise ValueError("trigger must be 'message', 'schedule' or 'recording'")
    from mailr.integrations.speakr.orders import clean_filter

    o["recordings"] = clean_filter(o.get("recordings"))
    o["query"] = str(o["query"] or "").strip()[:2000]
    o["question"] = str(o["question"] or "").strip()[:1000]
    o["errand"] = str(o["errand"] or "").strip()[:4000]
    try:
        o["threshold"] = max(0.5, min(0.99, float(o["threshold"])))
    except (TypeError, ValueError):
        raise ValueError("threshold must be a number between 0.5 and 0.99") from None
    if o["query"]:
        try:
            compile_query(o["query"], user.id, await _labels(db, user.id))
        except ValueError as e:
            raise ValueError(f"the search does not work: {e}") from None
    labels = {lb.id for lb in await _labels(db, user.id)}
    o["actions"] = _clean_actions(o["actions"], labels)
    o["choices"] = _clean_choices(o["choices"], labels)
    if o["choices"] and not o["question"]:
        raise ValueError("a choice order needs its question (e.g. 'Which project is this about?')")
    o["deliver"] = [d for d in dict.fromkeys(o["deliver"] or []) if d in DELIVER]
    o["from_trusted"] = bool(o.get("from_trusted"))
    o["from_me"] = bool(o.get("from_me"))
    if o["trigger"] != "message":
        if o["from_trusted"]:
            raise ValueError("only a message order can be limited to mail you forward from your trusted addresses")
        if o["from_me"]:
            raise ValueError("only a message order can be limited to mail from you or your trusted addresses")
        if set(o["actions"]) & set(MESSAGE_ONLY) or any(set(c["actions"]) & set(MESSAGE_ONLY) for c in o["choices"]):
            raise ValueError("adding an invitation to your calendar works only in a message order")
    if o["trigger"] == "recording":
        # A new Speakr recording (integrations/speakr/orders.py): mail actions do not apply.
        o["schedule"], o["timezone"], o["choices"] = "", "", []
        o["recordings"].pop("enabled", None)
        o["recordings"].pop("days", None)
        if o["query"]:
            raise ValueError("a recording order has no mail search; filter recordings with recordings.q, tag or participant")
        if set(o["actions"]) - {"notify"}:
            raise ValueError("a recording order's only instant action is notify")
        if not o["errand"] and not o["actions"]:
            raise ValueError("a recording order needs instructions for the assistant or notify")
        if o["errand"] and not o["deliver"]:
            o["deliver"] = ["notify"]
    elif o["trigger"] == "schedule":
        from mailr.knowledge import profile

        prof = await profile.get(db, user.id)
        o["timezone"] = str(o["timezone"] or profile.zone_name(user, prof.data) or "UTC")
        o["schedule"] = str(o["schedule"] or "").strip()
        validate_schedule(o["schedule"], o["timezone"])
        if o["question"] or o["choices"]:
            raise ValueError("a routine has no per-message question; describe the selection in its search")
        if not o["errand"] and not o["actions"]:
            raise ValueError("a routine needs instructions for the assistant or actions")
        if o["errand"] and not o["deliver"]:
            o["deliver"] = ["digest"]
    else:
        o["schedule"], o["timezone"] = "", ""
        if o["recordings"]:
            raise ValueError("a message order has no recording filter; use trigger 'recording' for recordings")
        if not o["query"] and not o["question"] and not o["from_trusted"] and not o["from_me"]:
            raise ValueError("an order needs a search, a question, or both, to know which mail it is for")
        if not o["actions"] and not o["errand"] and not any(c["actions"] for c in o["choices"]):
            raise ValueError("an order needs actions or instructions for the assistant")
        if o["errand"] and not o["deliver"]:
            o["deliver"] = ["brief"]
        o["deliver"] = [d for d in o["deliver"] if d != "digest"]
    if not o["name"]:
        o["name"] = (o["question"] or o["query"] or o["errand"])[:60] or "Standing order"
    if o["trigger"] == "schedule" and not o["recordings"].get("enabled"):
        o["recordings"] = {}
    return o


def _clean_actions(acts: dict[str, Any] | None, labels: set[int]) -> dict[str, Any]:
    acts = dict(acts or {})
    bad = set(acts) - set(INSTANT)
    if bad:
        raise ValueError(f"unknown actions: {sorted(bad)} (allowed: {', '.join(INSTANT)})")
    for k in ("add_labels", "remove_labels"):
        if k in acts:
            ids = [int(x) for x in acts[k] or []]
            if set(ids) - labels:
                raise ValueError("unknown label in actions")
            acts[k] = ids
    if acts.get("category") is not None and acts["category"] not in CATEGORIES:
        raise ValueError(f"category must be one of {', '.join(CATEGORIES)}")
    return {k: v for k, v in acts.items() if v not in (None, False, [], "")}


MAX_CHOICES = 8


def _clean_choices(choices: list[dict[str, Any]] | None, labels: set[int]) -> list[dict[str, Any]]:
    """2 to 8 named options, each with a description for the model and its own actions."""
    import re

    out: list[dict[str, Any]] = []
    for c in choices or []:
        name = str((c or {}).get("name") or "").strip()[:60]
        if not name:
            raise ValueError("every choice needs a name")
        key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or f"choice-{len(out) + 1}"
        if key == "none" or any(x["key"] == key for x in out):
            raise ValueError(f"two choices have the same name, or a choice is called 'none': {name!r}")
        out.append({"key": key, "name": name, "description": str(c.get("description") or "").strip()[:300],
                    "actions": _clean_actions(c.get("actions"), labels)})
    if out and not 2 <= len(out) <= MAX_CHOICES:
        raise ValueError(f"a choice order has 2 to {MAX_CHOICES} choices")
    return out


async def apply_order_op(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    """op: create | update | delete | pause | resume. Returns the order (or {"deleted": id})."""
    op = payload.get("op")
    oid = payload.get("order_id")
    existing = None
    if op != "create":
        existing = await db.get(AssistantOrder, int(oid or 0))
        if existing is None or existing.user_id != user.id:
            raise ValueError("order not found")
    if op == "delete":
        if existing.builtin:
            raise ValueError("a built-in order cannot be deleted; turn it off instead")
        await db.delete(existing)
        await db.flush()
        return {"deleted": int(oid)}
    if op == "move":
        await move(db, user, existing, str(payload.get("direction") or ""))
        return order_out(existing)
    if op == "update" and existing.builtin:
        return await _update_builtin(db, user, existing, payload.get("order") or {})
    if op in ("pause", "resume"):
        existing.enabled = op == "resume"
        if existing.enabled and existing.trigger == "schedule":
            existing.next_run_at = next_slot(existing.schedule, existing.timezone, datetime.now(UTC))
        await db.flush()
        return order_out(existing)
    if op not in ("create", "update"):
        raise ValueError("op must be create, update, delete, pause, resume or move")
    o = await clean(db, user, payload.get("order") or {}, existing)
    row = existing or AssistantOrder(user_id=user.id, source=payload.get("source") or "user")
    if existing is None:
        pos = (await db.execute(select(func.coalesce(func.max(AssistantOrder.position), 0)).where(
            AssistantOrder.user_id == user.id))).scalar_one()
        row.position = int(pos) + 1
        db.add(row)
    for k, v in o.items():
        setattr(row, k, v)
    row.next_run_at = next_slot(row.schedule, row.timezone, datetime.now(UTC)) if (
        row.trigger == "schedule" and row.enabled) else None
    await db.flush()
    return order_out(row)


async def _update_builtin(db: AsyncSession, user: User, o: AssistantOrder, data: dict[str, Any]) -> dict[str, Any]:
    """A built-in order: on or off, and its permission (Ask me or Allow). Its condition and its work
    are fixed."""
    fixed = {k for k, v in data.items() if v is not None and k not in ("enabled", "permission")
             and k in order_out(o) and v != order_out(o)[k]}
    if fixed:
        raise ValueError("a built-in order's condition and work are fixed; you can turn it off, move it, "
                         "or choose Ask me or Allow")
    if data.get("enabled") is not None:
        o.enabled = bool(data["enabled"])
    if data.get("permission") is not None:
        await set_permission(db, user, str(data["permission"]))
    await db.flush()
    return await order_out_full(db, user, o)


async def move(db: AsyncSession, user: User, o: AssistantOrder, direction: str) -> None:
    """Move an order one place up or down among the orders of its kind (the top one runs first)."""
    if direction not in ("up", "down"):
        raise ValueError("direction must be up or down")
    rows = list((await db.execute(select(AssistantOrder).where(
        AssistantOrder.user_id == user.id, AssistantOrder.trigger == o.trigger)
        .order_by(AssistantOrder.position, AssistantOrder.id))).scalars())
    i = next(n for n, r in enumerate(rows) if r.id == o.id)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(rows):
        rows[i], rows[j] = rows[j], rows[i]
    for n, r in enumerate(rows):
        r.position = n  # renumbered: equal positions from older rows sort the same everywhere
    await db.flush()


# ---------------------------------------------------------------- built-in orders


async def ensure_builtin(db: AsyncSession, user: User, key: str = BUILTIN_FORWARDS) -> AssistantOrder:
    """The user's built-in order, seeded (on, Ask me, at the top) when missing. The migration seeds it
    for users who existed then; this covers the rest (confirming a trusted address, listing orders)."""
    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import insert

    q = select(AssistantOrder).where(AssistantOrder.user_id == user.id, AssistantOrder.builtin == key)
    row = (await db.execute(q)).scalar_one_or_none()
    if row is not None:
        return row
    top = (await db.execute(select(func.min(AssistantOrder.position)).where(
        AssistantOrder.user_id == user.id, AssistantOrder.trigger == "message"))).scalar_one()
    await db.execute(insert(AssistantOrder).values(
        user_id=user.id, name=BUILTINS[key], trigger="message", schedule="", timezone="", query="", question="",
        choices=[], threshold=0.7, actions={}, errand="", recordings={}, from_trusted=True, from_me=False,
        builtin=key, deliver=["brief", "notify"], enabled=True,
        position=0 if top is None or top > 0 else int(top) - 1, source="builtin", runs=0,
    ).on_conflict_do_nothing(index_elements=["user_id", "builtin"], index_where=text("builtin <> ''")))
    return (await db.execute(q)).scalar_one()


async def permission(db: AsyncSession, user: User) -> tuple[str, str]:
    """(ask | allow | deny, source): the built-in order's permission, the class
    agent.forwarded_instructions as the permission engine resolves it for Ambrose. A grant from an
    approval card ("Allow always") shows here as Allow."""
    from mailr.core import permissions, trusted

    effect, source, _rule = await permissions.resolve(db, permissions.Actor(user=user, kind="assistant"), trusted.ACTION)
    return effect, source


async def set_permission(db: AsyncSession, user: User, value: str) -> None:
    """Store Ask me or Allow as the assistant-level rule of the class (none for the default, Ask me).
    A plain deny rule from the old Off switch goes too: Off is the order's own switch now."""
    from mailr.core import permissions, trusted
    from mailr.models import PermissionRule

    if value not in PERMISSIONS:
        raise ValueError("permission must be ask or allow")
    rows = (await db.execute(select(PermissionRule).where(
        PermissionRule.user_id == user.id, PermissionRule.subject.in_(("assistant", "*")),
        PermissionRule.action == trusted.ACTION))).scalars().all()
    for r in rows:
        if not r.constraints and (r.subject == "assistant" or r.effect == "deny"):
            await db.delete(r)
    if value != permissions.default_effect(trusted.ACTION, "assistant"):
        db.add(PermissionRule(user_id=user.id, subject="assistant", action=trusted.ACTION, effect=value,
                              constraints={}, source="user"))
    await db.flush()


async def order_out_full(db: AsyncSession, user: User, o: AssistantOrder) -> dict[str, Any]:
    """order_out, with a built-in order's permission (Ask me or Allow)."""
    out = order_out(o)
    if o.builtin == BUILTIN_FORWARDS:
        effect, source = await permission(db, user)
        out["permission"] = "allow" if effect == "allow" else "ask"
        out["permission_source"] = "default" if source == "default" else "rule"
    return out


def order_out(o: AssistantOrder) -> dict[str, Any]:
    return {"id": o.id, "name": o.name, "trigger": o.trigger, "schedule": o.schedule, "timezone": o.timezone,
            "query": o.query, "question": o.question, "choices": list(o.choices or []), "threshold": o.threshold,
            "actions": o.actions or {},
            "errand": o.errand, "deliver": list(o.deliver or []), "enabled": o.enabled, "position": o.position,
            "recordings": dict(o.recordings or {}), "from_trusted": bool(o.from_trusted),
            "from_me": bool(o.from_me), "builtin": o.builtin or None,
            "source": o.source, "runs": o.runs,
            "last_run_at": o.last_run_at.isoformat() if o.last_run_at else None,
            "next_run_at": o.next_run_at.isoformat() if o.next_run_at else None,
            "created_at": o.created_at.isoformat() if o.created_at else None}


def run_out(r: AssistantRun, order_name: str | None = None, order_builtin: str | None = None) -> dict[str, Any]:
    from mailr.agent.present import normalize_markdown
    from mailr.core.publicid import ConvId, MsgId

    return {"id": r.id, "order_id": r.order_id, "order_name": order_name, "order_builtin": order_builtin or None,
            "trigger": r.trigger, "slot": r.slot,
            "message_id": MsgId(r.message_id) if r.message_id else None,
            "conversation_id": ConvId(r.conversation_id) if r.conversation_id else None,
            "status": r.status, "condition": r.condition or {}, "actions": r.actions or [],
            "session_id": r.session_id, "output": normalize_markdown(r.output) if r.output else r.output, "citations": r.citations or [],
            "delivered": list(r.delivered or []), "error": r.error,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None}


# ---------------------------------------------------------------- matching


async def query_matches(db: AsyncSession, m: Message, query: str, labels: list[Label]) -> bool:
    from mailr.core.search import compile_query, query_zone

    if not query:
        return True
    cond = compile_query(query, m.user_id, labels, zone=await query_zone(db, m.user_id, query)).where
    async with db.begin_nested():
        row = (await db.execute(select(case((cond, True), else_=False)).where(Message.id == m.id))).first()
    return bool(row and row[0])


def principal_for(user: User):
    from mailr.core.auth import ALL_SCOPES, Principal

    return Principal(user=user, scopes=set(ALL_SCOPES) - {"admin", "tls"})


async def run_instant(db: AsyncSession, user: User, conv_ids: list[int], actions: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply an order's instant actions to whole conversations (like any user action)."""
    from mailr.core import actions as act
    from mailr.core.store import user_store

    if not conv_ids:
        return []
    p = principal_for(user)
    msgs = await act.messages_for(db, p, conv_ids=conv_ids)
    if not msgs:
        return []
    ctx = act.ActionContext(db, await user_store(db, user), p)
    done: list[dict[str, Any]] = []
    steps: list[tuple[str, dict[str, Any]]] = []
    if actions.get("add_labels"):
        steps.append(("add_labels", {"label_ids": actions["add_labels"]}))
    if actions.get("remove_labels"):
        steps.append(("remove_labels", {"label_ids": actions["remove_labels"]}))
    if actions.get("star"):
        steps.append(("star", {}))
    if actions.get("important"):
        steps.append(("important", {}))
    if actions.get("category"):
        steps.append(("category", {"category": actions["category"]}))
    if actions.get("mark_read"):
        steps.append(("read", {}))
    if actions.get("archive"):
        steps.append(("archive", {}))
    for name, args in steps:
        res = await act.apply(ctx, msgs, name, args)
        done.append({"action": name, **({"args": args} if args else {}), "affected": res.get("affected", 0)})
    return done


def by_order(order: AssistantOrder | None) -> dict[str, Any]:
    """Which order produced an effect (the run log's `by`)."""
    if order is None:
        return {}
    return {"order_id": order.id, "name": order.name, **({"builtin": order.builtin} if order.builtin else {})}


async def _waiting_card(db: AsyncSession, user_id: int, uid: str) -> int | None:
    """An approval card that waits to add this invitation (calendar.create, variant invite)."""
    from mailr.models import PendingAction

    return (await db.execute(select(PendingAction.id).where(
        PendingAction.user_id == user_id, PendingAction.action == "calendar.create",
        PendingAction.status == "pending", PendingAction.target == uid,
        PendingAction.payload["_variant"].astext == "invite").limit(1))).scalar_one_or_none()


async def add_to_calendar(db: AsyncSession, user: User, m: Message, *, order: AssistantOrder | None = None,
                          seen: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """The order action "Add the invitation to my calendar": the invite accept path with no reply to
    the organizer (calendar/routes.add_invite), for an invitation in the message or, when the user
    forwarded it from a trusted address, inside the forwarded email. Ambrose acts for the order, so
    the calendar.create class decides (Ask me: an approval card).

    Never twice for one invitation (its UID): not when an earlier order of this pass added it
    (`seen`), not when it is in the calendar already, not when an approval card for it waits."""
    from fastapi import HTTPException

    from mailr.calendar import mirror
    from mailr.calendar.routes import _invite, add_invite
    from mailr.core import activity, permissions, trusted
    from mailr.core.sendgate import _SystemPrincipal

    p = principal_for(user)
    out: dict[str, Any] = {"action": "add_to_calendar"}
    try:
        _, _, info = await _invite(db, p, m.id)
    except HTTPException:
        return {**out, "result": "no_invitation"}
    if info.get("method") not in ("REQUEST", ""):
        return {**out, "result": "not_a_request"}
    uid = str(info.get("uid") or "")
    key = f"event:{uid or m.id}"
    if seen is not None and key in seen:
        return {**out, "result": "duplicate", "by": seen[key]}
    if seen is not None:
        seen[key] = by_order(order)
    if uid:
        try:
            await mirror.ensure_synced(db, user.id, p)
            have = await mirror.find_by_uid(db, user.id, uid, "event", p)
        except Exception:  # noqa: BLE001 - no calendar yet: add_invite says so below
            have = None
        if have is not None:
            return {**out, "result": "already_in_calendar", "uid": uid}
        waiting = await _waiting_card(db, user.id, uid)
        if waiting is not None:
            from mailr.core import publicid

            return {**out, "result": "duplicate", "approval_id": publicid.encode("approval", waiting)}
    payload = {"message_id": m.id, "_variant": "invite", "summary": info.get("summary") or ""}
    decision = await permissions.check(db, permissions.Actor(user=user, kind="assistant"), "calendar.create",
                                       target=info.get("uid") or str(m.id), ctx={}, payload=payload,
                                       preview=trusted.invite_preview(info, m.subject))
    if isinstance(decision, permissions.Denied):
        return {**out, "result": "denied"}
    if isinstance(decision, permissions.Pending):
        from mailr.core import publicid

        return {**out, "result": "pending", "approval_id": publicid.encode("approval", decision.action.id)}
    try:
        res = await add_invite(db, _SystemPrincipal(user), m.id, cal_p=p)
    except HTTPException as e:
        return {**out, "result": "failed", "error": str(e.detail)[:300]}
    act = activity.calendar_task("calendar.create", payload, None, res, None)
    if act:
        await activity.add(db, user.id, "agent", "agent.calendar.create", str(res.get("uid") or ""), act,
                           inputs={"message_id": m.id})
    return {**out, "result": res.get("calendar") or "added", "uid": res.get("uid")}


async def _errands_last_hour(db: AsyncSession, uid: int) -> int:
    since = datetime.now(UTC) - timedelta(hours=1)
    return (await db.execute(select(func.count()).select_from(AssistantRun).where(
        AssistantRun.user_id == uid, AssistantRun.created_at >= since, AssistantRun.session_id.is_not(None)
        | (AssistantRun.status == "queued")))).scalar_one()


async def _claim_run(db: AsyncSession, run: AssistantRun) -> bool:
    """Insert a run; False when this (order, slot) already ran."""
    try:
        async with db.begin_nested():
            db.add(run)
            await db.flush()
        return True
    except IntegrityError:
        return False


def merge_actions(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """The order's own actions plus the picked choice's (labels together; the choice wins otherwise)."""
    out = {**a, **b}
    for k in ("add_labels", "remove_labels"):
        if a.get(k) or b.get(k):
            out[k] = list(dict.fromkeys([*(a.get(k) or []), *(b.get(k) or [])]))
    return out


async def run_message_order(db: AsyncSession, user: User, order: AssistantOrder, m: Message,
                            labels: list[Label], *, trigger: str = "message", dry_run: bool = False,
                            seen: dict[str, dict[str, Any]] | None = None) -> AssistantRun | None:
    """Evaluate one order on one message. Returns the run (None: the search did not match).
    dry_run (Settings "test on this message"): answers the condition, changes nothing.
    `seen`: the effects earlier orders of this pass produced for the message (module docstring)."""
    from mailr.assistant import decide, notify

    if order.builtin == BUILTIN_FORWARDS:
        from mailr.assistant import forwards

        return await forwards.run(db, user, order, m, dry_run=dry_run)
    if not await query_matches(db, m, order.query, labels):
        return None
    via = None
    if order.from_me:
        # Only mail from the user or a trusted address of theirs, verified (core/trusted.from_user).
        from mailr.core import trusted

        if not (await trusted.from_user(db, user, m)).ok:
            return None
    if order.from_trusted:
        # Only mail the user forwarded from a trusted address, verified (core/trusted.py).
        from mailr.core import trusted

        if not (await trusted.check(db, user, m)).ok:
            return None
        via = {"kind": "conversation", "id": m.conversation_id, "title": (m.subject or "")[:200]}
    run = AssistantRun(user_id=user.id, order_id=order.id, trigger=trigger,
                       slot=None if dry_run else f"m{m.id}" if trigger != "manual"
                       else f"m{m.id}:manual:{datetime.now(UTC).isoformat()}",  # re-runnable by hand
                       message_id=m.id, conversation_id=m.conversation_id,
                       status="running")
    cond: dict[str, Any] = {"query": order.query, "matched": True, **({"from_trusted": True} if via else {}),
                            **({"from_me": True} if order.from_me else {})}
    picked: dict[str, Any] | None = None
    if order.question and order.choices:
        # A choice order: one call picks the option (or none); its actions join the order's own.
        ans = await decide.decide_choice(db, user, m, order.question, order.choices)
        if ans.source == "error" and not dry_run:
            raise ModelUnavailable(ans.reason)
        cond.update({"question": order.question, **ans.as_dict(), "threshold": order.threshold})
        picked = next((c for c in order.choices if c["key"] == ans.choice), None)
        if picked is None or ans.confidence < order.threshold:
            run.condition, run.status = cond, "skipped"
            run.finished_at = datetime.now(UTC)
            if not dry_run:
                await _claim_run(db, run)
            return run
    elif order.question:
        ans = await decide.decide(db, user, m, order.question)
        if ans.source == "error" and not dry_run:
            raise ModelUnavailable(ans.reason)
        cond.update({"question": order.question, **ans.as_dict(), "threshold": order.threshold})
        if not ans.yes or ans.confidence < order.threshold:
            run.condition, run.status = cond, "skipped"
            run.finished_at = datetime.now(UTC)
            if not dry_run:
                await _claim_run(db, run)
            return run
    run.condition = cond
    acts = merge_actions(order.actions or {}, picked["actions"] if picked else {})
    if dry_run:
        run.status = "done"
        run.actions = [{"action": k, "would": True} for k in acts] + (
            [{"action": "errand", "would": True}] if order.errand else [])
        return run
    if not await _claim_run(db, run):
        return None
    notify_too = bool(acts.pop("notify", False))
    invite_too = bool(acts.pop("add_to_calendar", False))
    from mailr.core import activity

    token = activity.VIA.set(via) if via else None
    try:
        done = await run_instant(db, user, [m.conversation_id], acts)
        if invite_too:
            done.append(await add_to_calendar(db, user, m, order=order, seen=seen))
        run.actions = done  # assigned whole: the permission check flushes in between
    finally:
        if token is not None:
            activity.VIA.reset(token)
    if notify_too and not order.errand and seen is not None and "notify" in seen:
        # One notification per email: an earlier order already sent it.
        run.actions = [*run.actions, {"action": "notify", "result": "duplicate", "by": seen["notify"]}]
        notify_too = False
    if notify_too and not order.errand:
        if seen is not None:
            seen["notify"] = by_order(order)
        from mailr.knowledge import profile

        prof = await profile.get(db, user.id)
        how = await notify.send(db, user, title=f"{profile.assistant_name(prof)} · {order.name}",
                                body=f"{m.from_name or m.from_email}: {m.subject}",
                                data={"order_id": order.id, "run_id": run.id, "conversation_id": m.conversation_id,
                                      "message_id": m.id}, private=bool(m.local_only))
        run.actions = [*run.actions, {"action": "notify", "result": how}]
    order.runs = (order.runs or 0) + 1
    order.last_run_at = datetime.now(UTC)
    if order.errand:
        if await _errands_last_hour(db, user.id) >= ERRANDS_PER_HOUR:
            run.status, run.error = "skipped", f"more than {ERRANDS_PER_HOUR} errands in the last hour"
            run.finished_at = datetime.now(UTC)
        else:
            run.status = "queued"
            await enqueue(db, "assistant.errand", {"run_id": run.id}, user_id=user.id,
                          dedupe_key=f"assistant.errand:{run.id}")
    else:
        run.status, run.finished_at = "done", datetime.now(UTC)
    return run


async def assistant_allowed(db: AsyncSession, user: User) -> bool:
    from mailr.core.access import for_user

    return (await for_user(db, user)).feature("assistant")


class ModelUnavailable(Exception):
    """An order's question could not be asked (the model call failed): not a "no"."""


# The order pass runs again this many seconds after an order's model call failed (twice).
ORDER_RETRIES = (300, 1800)


@handler("assistant.orders")
async def on_message(payload: dict[str, Any], ctx) -> None:
    tries = int(payload.get("tries") or 0)
    later: list[int] = []
    async with session_scope() as db:
        m = await db.get(Message, int(payload["message_id"]))
        if m is None or m.deleted or m.draft:
            return
        # Orders run on received mail. A share from another app (Message.origin) counts as received
        # and is read as untrusted data like any email; it is never the user's own instruction.
        if m.own_words and not payload.get("self_addressed"):
            return
        user = await db.get(User, m.user_id)
        if user is None or user.disabled:
            return
        if not await assistant_allowed(db, user):
            return  # the access policy turns the assistant off (core/access.py)
        from mailr.intel.spam import model_skip_reason

        if await model_skip_reason(db, user, m, None):
            return  # likely spam (moved to Spam since triage): no model reads it
        await run_orders(db, user, m, later=later if tries < len(ORDER_RETRIES) else None)
        uid = user.id
    if later:
        # The orders whose question failed run again later (their runs are not claimed yet); the
        # orders that ran keep theirs and are not run twice. The last pass records the failure.
        async with session_scope() as db:
            await enqueue(db, "assistant.orders", {**payload, "tries": tries + 1}, user_id=uid,
                          run_at=datetime.now(UTC) + timedelta(seconds=ORDER_RETRIES[tries]),
                          dedupe_key=f"assistant.orders:{payload['message_id']}:{tries + 1}")


async def run_orders(db: AsyncSession, user: User, m: Message, *,
                     later: list[int] | None = None) -> list[AssistantRun]:
    """Every enabled message order on one message, top first, sharing the effects they produce
    (module docstring): the one orders pipeline, the built-in order included. `later` collects the
    orders whose model call failed, to be run again; without it such an order is a failed run."""
    from mailr.core import trusted

    orders = (await db.execute(select(AssistantOrder).where(
        AssistantOrder.user_id == m.user_id, AssistantOrder.enabled.is_(True),
        AssistantOrder.trigger == "message").order_by(AssistantOrder.position, AssistantOrder.id))).scalars().all()
    if not orders:
        return []
    labels = await _labels(db, m.user_id)
    elsewhere = trusted.handled_elsewhere(m, user, await trusted.own_addresses(db, user)) \
        if trusted.elsewhere_of(user) else []
    seen: dict[str, dict[str, Any]] = {}
    runs: list[AssistantRun] = []
    for o in orders:
        if elsewhere and not trusted.names_address(o.query, elsewhere):
            # Another assistant handles the address this mail went to (Settings → Ambrose →
            # Trusted addresses): mailr leaves it alone.
            if o.builtin == BUILTIN_FORWARDS:
                from mailr.assistant import forwards

                r = await forwards.skip_elsewhere(db, user, o, m, elsewhere)
                if r is not None:
                    runs.append(r)
            continue
        # Each order in a savepoint: a failing one is undone alone and shows as a failed run; the
        # runs, decisions and errands of the orders before and after it stay.
        sp = await db.begin_nested()
        try:
            r = await run_message_order(db, user, o, m, labels, seen=seen)
            await sp.commit()
            if r is not None:
                runs.append(r)
        except Exception as e:  # noqa: BLE001 - one broken order must not stop the others
            log.warning("order %s on message %s failed: %s", o.id, m.id, e)
            await sp.rollback()
            await db.refresh(m)
            await db.refresh(user)
            if isinstance(e, ModelUnavailable) and later is not None:
                later.append(o.id)
                continue
            failed = AssistantRun(user_id=user.id, order_id=o.id, trigger="message", slot=f"m{m.id}",
                                  message_id=m.id, conversation_id=m.conversation_id, status="failed",
                                  error=f"{type(e).__name__}: {e}"[:500], finished_at=datetime.now(UTC))
            if await _claim_run(db, failed):
                runs.append(failed)
    return runs


async def maybe_enqueue_for_message(db: AsyncSession, m: Message) -> None:
    """Called when triage finishes (intel/tasks.py): queue the order pass for fresh mail."""
    if m.draft or m.deleted:
        return
    if (m.date or m.received_at or datetime.now(UTC)) < datetime.now(UTC) - LIVE_WINDOW:
        return
    rows = (await db.execute(select(AssistantOrder.from_trusted).where(
        AssistantOrder.user_id == m.user_id, AssistantOrder.enabled.is_(True),
        AssistantOrder.trigger == "message"))).scalars().all()
    if not rows:
        return
    if all(rows):
        # Only orders for mail the user forwards from a trusted address (the built-in order among
        # them): mail from any other sender cannot match, so no job for it.
        from mailr.models import TrustedAddress

        if not m.from_email or (await db.execute(select(TrustedAddress.id).where(
                TrustedAddress.user_id == m.user_id, TrustedAddress.confirmed_at.is_not(None),
                TrustedAddress.email == func.lower(m.from_email)).limit(1))).first() is None:
            return
    await enqueue(db, "assistant.orders", {"message_id": m.id}, user_id=m.user_id,
                  dedupe_key=f"assistant.orders:{m.id}")


# ---------------------------------------------------------------- routines


async def routine_conversations(db: AsyncSession, user: User, query: str, limit: int = ROUTINE_MAX_CONVERSATIONS,
                                visible: Any = None) -> list[int]:
    """Conversation ids a routine's search selects (newest first). `visible`: a token's message
    filter (core/visibility.visible_message_filter) when a token previews an order."""
    from sqlalchemy import true

    from mailr.core.search import compile_query, query_zone

    labels = await _labels(db, user.id)
    q = compile_query(query or "in:inbox", user.id, labels, zone=await query_zone(db, user.id, query))
    # Spam and Trash only when the search asks for them (in:spam, in:trash, in:anywhere), as in search.
    hidden = [] if q.includes_spam_trash else [lb.id for lb in labels if lb.role in ("junk", "trash")]
    rows = (await db.execute(
        select(Message.conversation_id, func.max(Message.date).label("d"))
        .where(q.where, Message.user_id == user.id, Message.deleted.is_(False), Message.draft.is_(False),
               visible if visible is not None else true(),
               ~Message.label_ids.overlap(hidden) if hidden else true())
        .group_by(Message.conversation_id).order_by(func.max(Message.date).desc()).limit(limit))).all()
    return [r[0] for r in rows]


async def start_routine(db: AsyncSession, user: User, order: AssistantOrder, slot: str, trigger: str = "schedule"
                        ) -> AssistantRun | None:
    run = AssistantRun(user_id=user.id, order_id=order.id, trigger=trigger, slot=slot, status="running")
    if not await _claim_run(db, run):
        return None
    with_recordings = bool((order.recordings or {}).get("enabled"))
    # A routine over recordings reads mail only when its search asks for some.
    convs = await routine_conversations(db, user, order.query) if order.query or not with_recordings else []
    run.condition = {"query": order.query or ("" if with_recordings else "in:inbox"), "conversations": len(convs)}
    if with_recordings:
        from mailr.integrations.speakr.orders import routine_recordings

        run.condition = {**run.condition, "recordings": await routine_recordings(db, user, order)}
    if order.actions:
        acts = {k: v for k, v in (order.actions or {}).items() if k != "notify"}
        run.actions = await run_instant(db, user, convs, acts)
    order.runs = (order.runs or 0) + 1
    order.last_run_at = datetime.now(UTC)
    if order.errand:
        run.status = "queued"
        await enqueue(db, "assistant.errand", {"run_id": run.id, "conversation_ids": convs}, user_id=user.id,
                      dedupe_key=f"assistant.errand:{run.id}")
    else:
        run.status, run.finished_at = "done", datetime.now(UTC)
    return run


async def due_routines() -> int:
    """Periodic: start every routine whose next_run_at passed. Rows are claimed with SKIP LOCKED,
    and a run is keyed by its slot, so a restart or a second worker never runs a slot twice.
    Missed slots collapse into one run."""
    started = 0
    now = datetime.now(UTC)
    async with session_scope() as db:
        rows = (await db.execute(select(AssistantOrder).where(
            AssistantOrder.trigger == "schedule", AssistantOrder.enabled.is_(True),
            AssistantOrder.next_run_at.is_not(None), AssistantOrder.next_run_at <= now)
            .order_by(AssistantOrder.next_run_at).limit(50).with_for_update(skip_locked=True))).scalars().all()
        for o in rows:
            slot = o.next_run_at.isoformat()
            try:
                o.next_run_at = next_slot(o.schedule, o.timezone, now)
            except Exception as e:  # noqa: BLE001 - a broken schedule pauses the routine
                log.warning("routine %s has a broken schedule: %s", o.id, e)
                o.enabled, o.next_run_at = False, None
                continue
            user = await db.get(User, o.user_id)
            if user is None or user.disabled or not await assistant_allowed(db, user):
                continue  # the access policy turns the assistant off: the slot passes unrun
            if await start_routine(db, user, o, slot):
                started += 1
        # Quiet hours ended: send what was held.
        from mailr.assistant.notify import flush_quiet
        from mailr.models import AssistantProfile

        held = (await db.execute(select(AssistantProfile.user_id).where(
            AssistantProfile.state["held_notifications"].astext.not_in(["[]", "null"])))).scalars().all()
        for uid in held:
            u = await db.get(User, uid)
            if u is not None:
                await flush_quiet(db, u)
    return started
