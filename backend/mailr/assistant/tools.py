"""Assistant tools for standing orders and triage signals."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from mailr.agent.config_tools import _label_ids, _LabelPending, match_check, match_result
from mailr.agent.tools import ToolCtx, ToolError, ToolResult, _audit, _obj, gate, tool
from mailr.assistant import orders as ord_
from mailr.core import permissions
from mailr.models import AssistantOrder, LabelDecision, Message, User

_ORDER_FIELDS = {
    "name": {"type": "string", "description": "short label, e.g. 'Bills: label BILL DUE'"},
    "trigger": {"type": "string", "enum": ["message", "schedule", "recording"],
                "description": "message: each new email (after triage); schedule: a routine at `schedule`; "
                               "recording: each new Speakr meeting recording (after transcription)"},
    "recordings": {"type": "object", "description": (
        "Speakr recordings. trigger=recording: which recordings (q in the title or participants, tag, "
        "participant), with `question` answered from the summary. A routine: enabled=true also gives it "
        "today's recordings (days: how many days back, default 1)."),
        "properties": {"enabled": {"type": "boolean"}, "q": {"type": "string"}, "tag": {"type": "string"},
                       "participant": {"type": "string"}, "days": {"type": "integer", "minimum": 1, "maximum": 31}}},
    "schedule": {"type": "string", "description": "routines: cron in the user's time zone, e.g. '0 7 * * 1-5' "
                                                  "(weekdays 07:00). At most every 30 minutes."},
    "query": {"type": "string", "description": "search syntax. Message orders: a cheap prefilter (from:, "
                                               "subject:, category:, list:, is:needs_reply, has:deadline). "
                                               "Routines: which mail the routine reads."},
    "question": {"type": "string", "description": "message orders: a yes/no question about the email answered by "
                                                  "the decision model, e.g. 'Is this a bill or payment reminder?'"},
    "choices": {"type": "array", "maxItems": 8, "description": (
        "a choice order instead of yes/no: with `question` (e.g. 'Which of my projects is this about?'), the "
        "decision model picks one option, or none, and that option's actions run. 2 to 8 options."),
        "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "description": {"type": "string", "description": "what fits this option"},
            "add_labels": {"type": "array", "items": {"type": "string"}, "description": "label names"},
            "star": {"type": "boolean"}, "important": {"type": "boolean"}, "archive": {"type": "boolean"},
            "mark_read": {"type": "boolean"}, "category": {"type": "string", "enum": list(ord_.CATEGORIES)}},
            "required": ["name"]}},
    "add_labels": {"type": "array", "items": {"type": "string"}, "description": "label names"},
    "star": {"type": "boolean"}, "important": {"type": "boolean"}, "archive": {"type": "boolean"},
    "mark_read": {"type": "boolean"},
    "category": {"type": "string", "enum": list(ord_.CATEGORIES)},
    "notify": {"type": "boolean", "description": "send the user a notification when the order acts"},
    "add_to_calendar": {"type": "boolean", "description": (
        "message orders: add the email's meeting invitation to the user's calendar, with no reply to the organizer "
        "(an invitation the user forwarded from a trusted address counts too)")},
    "from_trusted": {"type": "boolean", "description": (
        "message orders: only mail the user forwards from one of their trusted addresses (Settings → Ambrose → "
        "Trusted addresses), verified. E.g. 'when I forward a meeting invite from work, add it to my "
        "calendar': from_trusted + add_to_calendar.")},
    "from_me": {"type": "boolean", "description": (
        "message orders: only mail from the user's own addresses or their trusted addresses, verified by the "
        "domain. Use it for an order that matches on a recipient address (to:alias@…) and writes (calendar, "
        "memory, tasks, drafts): without it anyone who knows the address can trigger the order.")},
    "errand": {"type": "string", "description": "instructions for a background run by the assistant (e.g. 'list "
                                                "the analyst jobs that allow remote work, cite each')"},
    "deliver": {"type": "array", "items": {"type": "string", "enum": list(ord_.DELIVER)},
                "description": "where the errand's answer goes: brief (card on the email), notify (phone), "
                               "digest (routines)"},
    "enabled": {"type": "boolean"},
    "create_missing_labels": {"type": "boolean"},
}


# ACT order.create @assistant, ACT order.edit @assistant, ACT order.pause @assistant, ACT order.delete @assistant
@tool("manage_order",
      "Create, update, pause, resume, delete or list standing orders and routines: instructions that make the "
      "assistant act on its own when matching mail arrives (trigger=message) or on a schedule (trigger=schedule). "
      "Use an order (not a filter) when the condition needs judgement ('bill reminders', 'job listings for me') or "
      "when the assistant should do work (brief, note, prepared reply, digest). Creating or changing one needs the "
      "user's approval.",
      _obj({"action": {"type": "string", "enum": ["list", "create", "update", "pause", "resume", "delete"]},
            "order_id": {"type": "integer"}, **_ORDER_FIELDS}, ["action"]), scope="rules")
async def manage_order(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    action = args.get("action")
    if action == "list":
        rows = (await ctx.db.execute(select(AssistantOrder).where(AssistantOrder.user_id == ctx.p.user_id)
                                     .order_by(AssistantOrder.trigger, AssistantOrder.position))).scalars().all()
        return ToolResult({"orders": [ord_.order_out(o) for o in rows]})
    if (ctx.chat_context or {}).get("errand"):
        raise ToolError("an errand cannot create or change standing orders")
    from mailr.core.visibility import sees_all_mail

    if not sees_all_mail(ctx.p):
        # An approved order runs as the user over all mail; a token limited to some accounts (or to
        # non-local mail) must not get work done beyond its own limits that way.
        raise ToolError("a token limited to some accounts or to non-local mail cannot create or change standing "
                        "orders; the user can add them in Settings")
    user: User = ctx.p.user
    payload: dict[str, Any] = {"op": action, "source": "assistant"}
    if action != "create":
        if not args.get("order_id"):
            raise ToolError("order_id is required (manage_order action=list)")
        payload["order_id"] = int(args["order_id"])
        existing = await ctx.db.get(AssistantOrder, payload["order_id"])
        if existing is None or existing.user_id != user.id:
            raise ToolError("order not found")
        if existing.builtin and action not in ("pause", "resume"):
            from mailr.knowledge import profile

            name = await profile.name_of(ctx.db, user.id)
            raise ToolError(f"order {existing.id} ('{existing.name}') is built in: it can be paused or resumed, not "
                            "changed or deleted. Its permission (Ask me or Allow) is the user's choice in Settings → "
                            f"{name} → Standing orders.")
    lines: list[str] = []
    if action in ("create", "update"):
        o: dict[str, Any] = {k: args[k] for k in ("name", "trigger", "schedule", "query", "question", "errand",
                                                  "deliver", "enabled", "recordings", "from_trusted", "from_me")
                             if args.get(k) is not None}
        acts: dict[str, Any] = {}
        if args.get("add_labels"):
            try:
                acts["add_labels"] = await _label_ids(ctx, args["add_labels"], bool(args.get("create_missing_labels")))
            except _LabelPending as e:
                return e.result
        for k in ("star", "important", "archive", "mark_read", "notify", "add_to_calendar"):
            if args.get(k):
                acts[k] = True
        if args.get("category"):
            acts["category"] = args["category"]
        if acts or action == "create":
            o["actions"] = acts
        if args.get("choices") is not None:
            choices = []
            for c in args["choices"] or []:
                ca: dict[str, Any] = {k: True for k in ("star", "important", "archive", "mark_read") if c.get(k)}
                if c.get("category"):
                    ca["category"] = c["category"]
                if c.get("add_labels"):
                    try:
                        ca["add_labels"] = await _label_ids(ctx, c["add_labels"], bool(args.get("create_missing_labels")))
                    except _LabelPending as e:
                        return e.result
                choices.append({"name": c.get("name"), "description": c.get("description") or "", "actions": ca})
            o["choices"] = choices
        try:
            existing_row = await ctx.db.get(AssistantOrder, payload["order_id"]) if action == "update" else None
            cleaned = await ord_.clean(ctx.db, user, o, existing_row)
        except ValueError as e:
            raise ToolError(str(e)) from None
        payload["order"] = cleaned
        if action == "create":
            await _no_duplicate(ctx, cleaned)
        when = {"message": "each new email", "recording": "each new recording"}.get(
            cleaned["trigger"], f"on schedule {cleaned['schedule']} ({cleaned['timezone']})")
        lines = [f"When: {when}"]
        if cleaned.get("recordings"):
            lines.append("Recordings: " + ", ".join(f"{k}={v}" for k, v in cleaned["recordings"].items()))
        if cleaned.get("from_trusted"):
            lines.append("Mail: only what you forward from your trusted addresses")
        if cleaned.get("from_me"):
            lines.append("Mail: only from you or your trusted addresses")
        if cleaned["query"]:
            lines.append(f"Mail: {cleaned['query']}")
        if cleaned["question"]:
            lines.append(f"{'Pick' if cleaned['choices'] else 'If'}: {cleaned['question']}")
        for c in cleaned["choices"]:
            lines.append(f"  • {c['name']}: " + (", ".join(k if v is True else f"{k}={v}" for k, v in c["actions"].items())
                                                 or "no actions"))
        if cleaned["actions"]:
            lines.append("Then: " + ", ".join(k if v is True else f"{k}={v}" for k, v in cleaned["actions"].items()))
        if cleaned["errand"]:
            lines.append(f"Assistant: {cleaned['errand']}")
            lines.append("Deliver: " + ", ".join(cleaned["deliver"]))
    title = {"create": "Create standing order", "update": "Change standing order", "pause": "Pause standing order",
             "resume": "Resume standing order", "delete": "Delete standing order"}.get(action)
    if title is None:
        raise ToolError("action must be list, create, update, pause, resume or delete")
    # ACT agent.match_check @assistant: a message order's search, checked against existing mail first.
    o = payload.get("order") or {}
    match = await match_check(ctx, o["query"]) if action in ("create", "update") and o.get("trigger") == "message" \
        and str(o.get("query") or "").strip() else None
    pending = await gate(ctx, "orders.write", target=title, payload=payload,
                         preview={"title": f"{title}: {(payload.get('order') or {}).get('name') or ''}".strip(': '),
                                  "kind": "order", "lines": lines, "danger": action == "delete",
                                  **({"match": match} if match is not None else {})})
    if pending is not None:
        await _audit(ctx, f"agent.orders.{action}", str(payload.get("order_id") or ""), payload,
                     {"pending": pending.data["approval_id"]})
        await ctx.db.commit()
        pending.data.update(match_result(match))
        return pending
    try:
        res = await ord_.apply_order_op(ctx.db, user, payload)
    except ValueError as e:
        raise ToolError(str(e)) from None
    await _audit(ctx, f"agent.orders.{action}", str(res.get("id", "")), payload)
    await ctx.db.commit()
    return ToolResult({"status": "done", "summary": title, "order": json.loads(json.dumps(res, default=str)),
                       **match_result(match)},
                      events=[{"type": "config", "title": f"{title}: {res.get('name', '')}"}])


def _same_mail(a: dict[str, Any], b: dict[str, Any]) -> bool:
    norm = lambda s: " ".join((s or "").lower().split())  # noqa: E731
    return (a.get("trigger") or "message") == (b.get("trigger") or "message") and bool(norm(a.get("query"))) \
        and norm(a.get("query")) == norm(b.get("query"))


async def _no_duplicate(ctx: ToolCtx, new: dict[str, Any]) -> None:
    """One order per search: a second create for the same mail (e.g. the user re-sent the
    request) would act twice on every email. Point the model at the existing one instead."""
    from mailr.models import PendingAction

    for o in (await ctx.db.execute(select(AssistantOrder).where(AssistantOrder.user_id == ctx.p.user_id))).scalars():
        if _same_mail(ord_.order_out(o), new):
            raise ToolError(f"order {o.id} ('{o.name}') already covers `{o.query}`. Change it with manage_order "
                            f"action=update order_id={o.id} instead of creating a second order.")
    pend = (await ctx.db.execute(select(PendingAction).where(
        PendingAction.user_id == ctx.p.user_id, PendingAction.action == "orders.write",
        PendingAction.status == "pending"))).scalars()
    for pa in pend:
        if (pa.payload or {}).get("op") == "create" and _same_mail((pa.payload or {}).get("order") or {}, new):
            raise ToolError(f"a request to create an order for `{new['query']}` is already waiting for the user's "
                            f"approval (approval {pa.id}). Tell the user to decide that one; do not ask again.")


async def _order_executor(db, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("op") == "create":
        for o in (await db.execute(select(AssistantOrder).where(AssistantOrder.user_id == user.id))).scalars():
            if _same_mail(ord_.order_out(o), payload.get("order") or {}):
                from mailr.knowledge import profile

                name = await profile.name_of(db, user.id)
                return {"error": f"order '{o.name}' already covers {o.query}; edit it in Settings → {name} "
                                 "instead of adding a second one"}
    try:
        res = await ord_.apply_order_op(db, user, payload)
    except ValueError as e:
        return {"error": str(e)}
    return {"title": f"Standing order {payload.get('op')}", "order": json.loads(json.dumps(res, default=str))}


permissions.executor("orders.write")(_order_executor)


@tool("get_signals",
      "Triage signals the fast pass computed: which conversations need a reply, wait on someone else, or carry a "
      "deadline (with its date). Use it for 'what needs my attention', morning briefings and follow-ups instead of "
      "re-reading everything.",
      _obj({"signal": {"type": "string", "enum": ["needs_reply", "waiting_on", "deadline", "all"]},
            "days": {"type": "integer", "description": "look back this many days (default 14, max 90)"},
            "limit": {"type": "integer", "description": "default 30, max 100"}}))
async def get_signals(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    from mailr.core.spamtrash import live_sql
    from mailr.core.visibility import visible_message_filter

    sig = args.get("signal") or "all"
    days = max(1, min(int(args.get("days") or 14), 90))
    limit = max(1, min(int(args.get("limit") or 30), 100))
    targets = ["needs_reply", "waiting_on", "deadline"] if sig == "all" else [sig]
    q = (select(LabelDecision.target, LabelDecision.source_ref, LabelDecision.reason, Message)
         .join(Message, Message.id == LabelDecision.message_id)
         .where(LabelDecision.user_id == ctx.p.user_id, LabelDecision.kind == "smart",
                LabelDecision.status == "applied", LabelDecision.value.is_(True),
                LabelDecision.target.in_(targets), Message.deleted.is_(False),
                Message.date >= datetime.now(UTC) - timedelta(days=days), visible_message_filter(ctx.p),
                text(live_sql("messages")))  # nothing from Spam or Trash (core/spamtrash.py)
         .order_by(Message.date.desc()).limit(limit * 3))
    rows = (await ctx.db.execute(q)).all()
    seen: set[tuple[str, int]] = set()
    items = []
    for target, ref, reason, m in rows:
        if target == "needs_reply" and m.answered:
            continue
        key = (target, m.conversation_id)
        if key in seen:
            continue
        seen.add(key)
        items.append({"signal": target, "conversation_id": m.conversation_id, "message_id": m.id,
                      "subject": (m.subject or "")[:160], "from": m.from_name or m.from_email,
                      "date": m.date.isoformat() if m.date else None, "reason": reason,
                      **({"deadline": ref} if target == "deadline" and ref else {})})
        if len(items) >= limit:
            break
    return ToolResult({"items": items, "count": len(items)})


async def _brief_run(ctx: ToolCtx, conversation_id: int | None):
    from mailr.assistant.errands import latest_brief

    # The email on screen first; the chat's own conversation only when nothing is open.
    from mailr.core.visibility import conversation_visible, sees_all_mail
    from mailr.models import AssistantRun, Conversation

    cid = conversation_id or (ctx.ui or {}).get("conversation_id") or (ctx.chat_context or {}).get("conversation_id")
    if not cid:
        raise ToolError("which conversation? pass conversation_id")
    conv = await ctx.db.get(Conversation, int(cid))
    if conv is None or not await conversation_visible(ctx.db, ctx.p, conv.id):
        raise ToolError("conversation not found")
    if not sees_all_mail(ctx.p):
        # The same rule as GET /conversations/{id}/brief: a brief can draw on mail this token may not see.
        raise ToolError("briefs are not available to a token limited to some accounts or to non-local mail")
    run: AssistantRun | None = await latest_brief(ctx.db, ctx.p.user_id, conv.id)
    if run is None:
        raise ToolError("this conversation has no brief (a brief is what a standing order's work leaves on an email)")
    return conv, run


def _as_markers(text: str, cites: list[dict[str, Any]]) -> str:
    """The stored brief shows links [n](mailr-cite:n); the model cites with [[m:<id> "quote"]]. Give it
    those markers, so a summary or a rewrite can cite the same places (never a guessed id)."""
    import re

    by_n = {int(c.get("n") or 0): c for c in cites or []}

    def sub(mt):
        c = by_n.get(int(mt.group(1)))
        if not c:
            return ""
        if c.get("message_id"):
            q = (c.get("quote") or "").replace('"', "'")
            return f'[[m:{c["message_id"]} "{q}"]]' if q else f"[[m:{c['message_id']}]]"
        return f"[[c:{c['conversation_id']}]]" if c.get("conversation_id") else ""
    return re.sub(r"\[(\d+)\]\(mailr-cite:\d+\)", sub, text or "")


def apply_brief_change(run, change: dict[str, Any]) -> str:
    """Rewrite (text, citations) or restore a run's brief; the caller commits."""
    cond = dict(run.condition or {})
    if change.get("restore"):
        if not cond.get("original_output"):
            raise ValueError("the brief has not been edited")
        run.output = cond.pop("original_output")
        run.citations = cond.pop("original_citations", run.citations)
        what = "Brief restored"
    else:
        cond.setdefault("original_output", run.output)
        cond.setdefault("original_citations", run.citations or [])
        run.output, run.citations = change["text"], change.get("citations") or []
        what = "Brief updated"
    run.condition = cond
    return what


# ACT brief.read @assistant
@tool("get_brief",
      "The brief on a conversation: the report a standing order's background work left on it, shown in a card "
      "above (or beside) the messages. It is not a draft and not an email. Its text carries citation markers "
      "[[m:<message_id> \"quote\"]]: copy them as they are to cite the same places. Defaults to the open "
      "conversation.",
      _obj({"conversation_id": {"type": "integer"}}))
async def get_brief(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    conv, run = await _brief_run(ctx, args.get("conversation_id"))
    o = await ctx.db.get(AssistantOrder, run.order_id) if run.order_id else None
    return ToolResult({"conversation_id": conv.id, "message_id": run.message_id, "order": o.name if o else None,
                       "text": _as_markers(run.output, run.citations or []),
                       "edited": bool((run.condition or {}).get("original_output")),
                       "citations": len(run.citations or [])})


# ACT brief.rewrite @assistant, ACT brief.restore @assistant
@tool("update_brief",
      "Rewrite the brief on a conversation (the card a standing order left on it; see get_brief): shorten it, "
      "change its tone or language, fix a mistake. Keep the [[m:<message_id> \"quote\"]] markers from get_brief "
      "next to the facts they support (quotes stay verbatim). restore=true brings back the original. Changes "
      "only the card, never the email or a draft.",
      _obj({"conversation_id": {"type": "integer"}, "text": {"type": "string", "description": "the new brief"},
            "restore": {"type": "boolean"}}), scope="draft")
async def update_brief(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.agent import agentwrite
    from mailr.core.visibility import touches_local_only

    conv, run = await _brief_run(ctx, args.get("conversation_id"))
    if not args.get("restore"):
        text = (args.get("text") or "").strip()
        if not text:
            raise ToolError("text is required (or restore=true)")
        # The brief is text the user reads as the assistant's own: no images, and links only from
        # what the model read; written after reading mail, or by a token, it asks first.
        from mailr.intel import citations

        text, cites = await citations.resolve(ctx.db, ctx.p, agentwrite.clean_text(ctx, text[:20000]),
                                              known=ctx.known() if ctx.known else None)
        change = {"op": "brief", "run_id": run.id, "text": text, "citations": cites}
        pending = await gate(ctx, "mail.draft", target=f"brief on {conv.subject or 'a conversation'}"[:200],
                             payload=change, preview={"title": f"Rewrite the brief on “{(conv.subject or '')[:80]}”",
                                                      "kind": "brief", "lines": [text],
                                                      "mail_refs": {"conversation_ids": [conv.id]}},
                             ask_by_default=agentwrite.taint_reason(ctx),
                             # A brief on local-only mail is read as local-only already.
                             force_ask=None if await touches_local_only(ctx.db, ctx.p.user_id, conversation_ids=[conv.id])
                             else agentwrite.local_copy_reason(ctx))
        if pending is not None:
            return pending
    else:
        change = {"op": "brief", "run_id": run.id, "restore": True}
    try:
        what = apply_brief_change(run, change)
    except ValueError as e:
        raise ToolError(str(e)) from None
    await _audit(ctx, "agent.brief.update", str(conv.id), {"run_id": run.id, "restore": bool(args.get("restore"))})
    await ctx.db.commit()
    return ToolResult({"status": "done", "summary": what},
                      events=[{"type": "config", "title": f"{what}: {conv.subject or '(no subject)'}",
                               "brief_conversation_id": conv.id}])
