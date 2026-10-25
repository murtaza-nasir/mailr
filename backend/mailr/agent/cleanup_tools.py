"""The assistant's subscriptions and cleanup tools (core/mailgroups.py).

`list_subscriptions` and `list_cleanup_groups` only read. `unsubscribe` and `propose_cleanup`
never act by themselves for an agent: each always asks the user (the permission engine, with
a hard rule over any allow rule), and the approved request runs exactly what the card showed:
the unsubscribe of that message (core/approval_executors.py, mail.unsubscribe), or the action
on exactly those conversations as a bulk job with progress and Undo (`op: cleanup`).

Local-only mail reaches the built-in assistant only when its chat runs on a local model
(tools.local_ok); otherwise these results leave it out and say so. A token sees what its account
limits allow.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from mailr.agent.tools import ToolCtx, ToolError, ToolResult, _audit, _obj, _u, gate, local_ok, tool
from mailr.core import mailgroups as mg

ALWAYS_ASK = "hard rule: an agent's cleanup plan or unsubscribe always waits for the user"
PLAN_MAX = 10_000  # conversations in one plan: above this a bulk job has no Undo (core/tasks.BULK_UNDO_MAX)
HIDDEN = "Mail from local-only accounts is left out of this result for this model."


async def _view(ctx: ToolCtx) -> tuple[Any, bool]:
    """The principal the tool reads as, and whether local-only mail was left out."""
    if ctx.p.token is not None or await local_ok(ctx):
        return ctx.p, False
    # The built-in assistant on a hosted model: the user's mail without local-only messages.
    return SimpleNamespace(user_id=ctx.p.user_id, user=ctx.p.user,
                           token=SimpleNamespace(local_only=False, connector_ids=[])), True


def _scope(args: dict[str, Any]) -> mg.Scope:
    return mg.Scope(where=args.get("where") or "archived", older_than_days=int(args.get("older_than_days") or 0),
                    uncategorised=bool(args.get("uncategorised")), bulk_only=bool(args.get("bulk_only")),
                    include_starred=bool(args.get("include_starred")),
                    include_important=bool(args.get("include_important")),
                    include_replied=bool(args.get("include_replied")))


_SCOPE_PROPS = {
    "where": {"type": "string", "enum": list(mg.WHERES),
              "description": "archived (default: not in the Inbox, Trash or Spam), inbox, or all"},
    "older_than_days": {"type": "integer", "minimum": 0, "description": "only mail older than this many days"},
    "uncategorised": {"type": "boolean", "description": "only mail without a category"},
    "bulk_only": {"type": "boolean", "description": "only newsletters, lists and automated senders"},
    "include_starred": {"type": "boolean", "description": "also starred conversations (left alone by default)"},
    "include_important": {"type": "boolean", "description": "also important conversations (left alone by default)"},
    "include_replied": {"type": "boolean",
                        "description": "also conversations with the user's own replies (left alone by default)"},
}


def _sub_brief(it: dict[str, Any]) -> dict[str, Any]:
    return {"subscription": it["key"], "name": _u(it["name"]), "email": it["email"], "list_id": it["list_id"],
            "last_30_days": it["last_30"], "last_90_days": it["last_90"], "total": it["total"],
            "read_share": it["open_rate"], "last_received": it["last_received"], "last_read": it["last_read"],
            "replied": it["replied"], "in_inbox": it["in_inbox"], "unsubscribe": it["unsubscribe"],
            "kept": it["kept"], "unsubscribed_at": it["unsubscribed_at"],
            "auto_archive": it["auto_archive_rule_id"] is not None}


# ACT subscription.list @assistant
@tool("list_subscriptions",
      "List the user's subscriptions (mailing lists and bulk senders): mail in the last 30 and 90 days, how "
      "much of it the user read, when they last read one, whether they ever replied, and whether one-click "
      "unsubscribe is available. Ordered by default with the least-read, busiest first. Read-only.",
      _obj({"query": {"type": "string", "description": "part of the list id or the sender's address"},
            "sort": {"type": "string", "enum": list(mg.SUB_SORTS)},
            "filter": {"type": "string", "enum": list(mg.SUB_FILTERS)},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}))
async def list_subscriptions(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    p, hidden = await _view(ctx)
    out = await mg.subscriptions(ctx.db, p, q=args.get("query") or "", sort=args.get("sort") or "score",
                                 filter=args.get("filter") or "all", limit=min(int(args.get("limit") or 25), 100))
    data: dict[str, Any] = {"total": out["total"], "items": [_sub_brief(i) for i in out["items"]]}
    if hidden:
        data["note"] = HIDDEN
    return ToolResult(data, sensitive=any(i["local_only"] for i in out["items"]))


# ACT subscription.unsubscribe @assistant
@tool("unsubscribe",
      "Unsubscribe the user from one subscription (from list_subscriptions): a one-click request or an "
      "'unsubscribe' mail to the list's own address. Always waits for the user's approval. When the list only "
      "offers a web page, the link is returned for the user to open.",
      _obj({"subscription": {"type": "string", "description": "the `subscription` value from list_subscriptions"}},
           ["subscription"]), scope="send")
async def unsubscribe(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.core import unsubscribe as unsub

    key = str(args["subscription"])
    mg.key_match(mg.SUB, key)
    p, _hidden = await _view(ctx)
    m = await mg.latest_with_unsubscribe(ctx.db, p, key)
    if m is None:
        raise ToolError("this subscription has no unsubscribe link")
    plan = await unsub.plan(ctx.db, ctx.p.user, m)
    if plan["method"] in ("none", "refused"):
        raise ToolError(plan.get("reason") or "this subscription has no usable unsubscribe method")
    if plan["method"] == "link":
        return ToolResult({"status": "open_link", "url": plan["url"],
                           "note": "This list only offers a web page. Give the user the link to open."})
    how = f"by mail to {plan['to']} (subject and text: unsubscribe)" if plan["method"] == "mailto" \
        else f"one-click request to {plan.get('url', '')[:200]}"
    pending = await gate(ctx, "mail.unsubscribe", target=m.from_email,
                         cvals={"connectors": [m.connector_id] if m.connector_id else []},
                         payload={"message_id": m.id, "subscription_key": key},
                         preview={"title": f"Unsubscribe from {m.from_name or m.from_email}",
                                  "lines": [m.list_id or m.from_email, how]}, force_ask=ALWAYS_ASK)
    if pending is not None:
        await _audit(ctx, "agent.unsubscribe", key, {"message_id": m.id}, {"pending": pending.data["approval_id"]})
        await ctx.db.commit()
        return pending
    # The signed-in user calling the tool directly (no approval needed).
    from mailr.api.mailgroup_routes import unsubscribe_subscription

    return ToolResult(await unsubscribe_subscription(ctx.db, ctx.p, key, via="tool"))


# ACT cleanup.groups @assistant
@tool("list_cleanup_groups",
      "Group the user's mail for a cleanup: by sender, domain, list or category, with the number of "
      "conversations, messages, size, unread, the oldest and newest date, how many protected conversations "
      "(starred, important, with the user's replies) are left alone, and any category the user's model "
      "suggested. Default scope: archived mail. Read-only.",
      _obj({"by": {"type": "string", "enum": list(mg.KINDS)},
            "query": {"type": "string", "description": "part of the group key"},
            "sort": {"type": "string", "enum": list(mg.SORTS)},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}, **_SCOPE_PROPS}))
async def list_cleanup_groups(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    p, hidden = await _view(ctx)
    out = await mg.groups(ctx.db, p, args.get("by") or "sender", _scope(args), q=args.get("query") or "",
                          sort=args.get("sort") or "count", limit=min(int(args.get("limit") or 25), 100))
    items = [{k: v for k, v in i.items() if k not in ("reviewed",)} for i in out["items"]]
    data: dict[str, Any] = {"by": out["by"], "scope": out["scope"], "total": out["total"], "items": items}
    if hidden:
        data["note"] = HIDDEN
    return ToolResult(data, sensitive=any(i["local_only"] for i in out["items"]))


_PLAN_ACTIONS = {"archive": "archive", "label": "add_labels", "trash": "trash", "read": "read",
                 "category": "category"}


# ACT cleanup.plan @assistant
@tool("propose_cleanup",
      "Propose one cleanup action on one group from list_cleanup_groups: archive, label (with `label`), trash "
      "(to Trash, never deleted forever), read (mark read), category (with `category`), or rule (a filter "
      "for the group's future mail: `rule_actions` such as {\"archive\": true}). The user sees exactly what "
      "changes (counts, 20 sample subjects, what is left alone) and approves it; nothing changes before. "
      "Use the same scope as the list. " + "Large plans run in the background with Undo.",
      _obj({"by": {"type": "string", "enum": list(mg.KINDS)}, "key": {"type": "string"},
            "action": {"type": "string", "enum": [*list(_PLAN_ACTIONS), "rule"]},
            "label": {"type": "string", "description": "label name, for action=label"},
            "category": {"type": "string", "enum": list(mg.CATEGORIES)},
            "rule_actions": {"type": "object", "description": "for action=rule: archive, mark_read, add_labels "
                                                              "(label ids), category, trash"},
            **_SCOPE_PROPS}, ["by", "key", "action"]), scope="label")
async def propose_cleanup(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from sqlalchemy import select

    from mailr.models import Label

    by, key, action = args["by"], str(args["key"]), args["action"]
    if by not in mg.KINDS:
        raise ToolError(f"by must be one of {', '.join(mg.KINDS)}")
    p, _hidden = await _view(ctx)
    sc = _scope(args)
    if action == "rule":
        from mailr.core import rules as rulesmod

        q = mg.rule_query(by, key)
        acts = dict(args.get("rule_actions") or {})
        if not q or not acts:
            raise ToolError("a rule needs a group with a search (not Uncategorised) and rule_actions")
        if acts.get("forward_to"):
            raise ToolError("agents cannot create forwarding rules")
        payload = {"op": "create", "rule": {"name": f"Cleanup: {key}"[:200], "query": q, "actions": acts,
                                            "apply_existing": False},
                   "source": "agent", "scope": rulesmod.scope_of(ctx.p)}
        # The group's name is mail text: its conversations name the mail (core/visibility.py).
        refs = await mg.resolve(ctx.db, p, by, key, sc, limit=500)
        pending = await gate(ctx, "rules.write", target=q, payload=payload,
                             preview={"title": f"New filter for {key}", "lines": [q, ", ".join(sorted(acts))],
                                      "mail_refs": {"conversation_ids": refs}},
                             force_ask=ALWAYS_ASK)
        if pending is not None:
            await _audit(ctx, "agent.cleanup.rule", key, {"query": q, "actions": acts},
                         {"pending": pending.data["approval_id"]})
            await ctx.db.commit()
            return pending
        from mailr.core.approval_executors import _rules_write

        return ToolResult(await _rules_write(ctx.db, ctx.p.user, payload))
    if action not in _PLAN_ACTIONS:
        raise ToolError(f"action must be one of {', '.join([*_PLAN_ACTIONS, 'rule'])}")
    plan_args: dict[str, Any] = {}
    label_name = ""
    if action == "label":
        name = (args.get("label") or "").strip()
        lb = (await ctx.db.execute(select(Label).where(Label.user_id == ctx.p.user_id, Label.role.is_(None),
                                                       Label.name.ilike(name)))).scalars().first() if name else None
        if lb is None:
            raise ToolError(f"no label named {name!r}; create it first (apply_label with create=true)")
        plan_args["label_ids"], label_name = [lb.id], lb.name
    if action == "category":
        if args.get("category") not in mg.CATEGORIES:
            raise ToolError(f"category must be one of {', '.join(mg.CATEGORIES)}")
        plan_args["category"] = args["category"]
    # The cap first: a group far over it is refused before it is counted.
    ids = await mg.resolve(ctx.db, p, by, key, sc, limit=PLAN_MAX + 1)
    if len(ids) > PLAN_MAX:
        raise ToolError(f"more than {PLAN_MAX} conversations: propose at most {PLAN_MAX} at a time (narrow the "
                        "scope, for example older_than_days)")
    pv = await mg.preview(ctx.db, p, by, key, sc)
    if not ids:
        return ToolResult({"status": "nothing_to_do", "conversations": 0, "protected": pv["protected"]})
    verb = {"archive": "Archive", "label": f"Label “{label_name}”", "trash": "Move to Trash", "read": "Mark read",
            "category": f"Move to {plan_args.get('category', '').capitalize()}"}[action]
    left = ", ".join(f"{n} {why}" for why, n in pv["protected"].items() if n)
    lines = [f"{pv['conversations']} conversations ({pv['messages']} messages) from {pv['label']}",
             *([f"Left alone: {left}"] if left else []),
             *[f"{s['subject'] or '(no subject)'}" for s in pv["samples"]]]
    cls = "mail.trash" if action == "trash" else "mail.label"
    from mailr.core.visibility import any_local_only

    pending = await gate(ctx, cls, target=f"{len(ids)} conversation(s)",
                         payload={"op": "cleanup", "conversation_ids": ids, "action": _PLAN_ACTIONS[action],
                                  "args": plan_args, "group": {"by": by, "key": key, "scope": sc.as_dict()},
                                  # Marked when built: push text and "Ask about this approval" read it.
                                  "sensitive": await any_local_only(ctx.db, ctx.p.user_id, conversation_ids=ids)},
                         preview={"title": f"Clean up: {verb} {len(ids)} conversations", "lines": lines[:24]},
                         force_ask=ALWAYS_ASK)
    summary = {"conversations": pv["conversations"], "messages": pv["messages"], "protected": pv["protected"],
               "samples": [{"subject": _u(s["subject"]), "from": _u(s["from_name"] or s["from_email"]),
                            "date": s["date"]} for s in pv["samples"]]}
    if pending is not None:
        await _audit(ctx, "agent.cleanup", f"{by}:{key}", {"action": action, "scope": sc.as_dict()},
                     {"pending": pending.data["approval_id"], "conversations": len(ids)})
        await ctx.db.commit()
        pending.data["plan"] = summary
        return pending
    from mailr.core import permissions
    from mailr.core.approval_executors import _cleanup_plan

    return ToolResult({**summary, **await _cleanup_plan(ctx.db, ctx.p.user, permissions.stamp({
        "conversation_ids": ids, "action": _PLAN_ACTIONS[action], "args": plan_args}, ctx.p))})
