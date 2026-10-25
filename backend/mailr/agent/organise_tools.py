"""Agent tools that organise mail: labels, archive, read state and the other conversation actions,
and showing a view in a tab. Registered in agent/tools.py, which re-exports them."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select

from mailr.agent.tools import (
    ToolCtx,
    ToolError,
    ToolResult,
    _audit,
    _ids,
    _obj,
    gate,
    tool,
)
from mailr.models import Label

# ---------------------------------------------------------------- organise


# Actions that take mail out of the user's sight. After the turn read mail they ask (unless a rule
# allows them): an injected "archive the bank's alert" must not hide it silently. Removing a system
# label (Inbox and the other roles) and moving mail to another tab count too (_hides).
HIDE_ACTIONS = frozenset({"archive", "read", "mute", "snooze", "not_important"})
HIDE_REASON = "hard rule: the assistant read email in this turn, and this hides mail from you"
# A token acts on conversations it names (as REST does); a whole list or search always asks.
SCOPE_REASON = "hard rule: an agent's action on a whole list needs your approval"


async def _hides(ctx: ToolCtx, action: str, extra: dict[str, Any]) -> bool:
    if action in HIDE_ACTIONS or action == "show_in_tab":
        return True
    if action != "remove_labels" or not extra.get("label_ids"):
        return False
    return bool((await ctx.db.execute(select(Label.id).where(
        Label.user_id == ctx.p.user_id, Label.id.in_(extra["label_ids"]), func.coalesce(Label.role, "") != "").limit(1))).first())


async def _action(ctx: ToolCtx, ids: list[int], action: str, extra: dict[str, Any]) -> dict[str, Any] | ToolResult:
    """Apply a mail action to conversations, gated by the permission engine exactly like the
    REST route (same class, constraints context, payload and preview), so an approval later
    runs Worker-registered `_mail_action` on precisely these messages."""
    from mailr.core import actions as act
    from mailr.core.store import user_store

    msgs = await act.messages_for(ctx.db, ctx.p, conv_ids=ids)
    if not msgs:
        raise ToolError("no matching conversations")
    labels = list(extra.get("label_ids") or ([extra["label_id"]] if extra.get("label_id") else []))
    try:
        await act.check_agent_labels(ctx.db, ctx.p.user_id, action, extra)
    except HTTPException as e:
        raise ToolError(str(e.detail)) from None
    cls = await act.classify(ctx.db, ctx.p.user_id, msgs, action, extra)
    pending = await gate(ctx, cls, target=f"{len(msgs)} message(s)",
                         cvals={"connectors": sorted({m.connector_id for m in msgs if m.connector_id}),
                                "labels": labels},
                         payload={"message_ids": [m.id for m in msgs], "action": action, "args": extra},
                         preview=await act.preview(ctx.db, msgs, action, extra),
                         ask_by_default=HIDE_REASON if ctx.untrusted and await _hides(ctx, action, extra) else None)
    if pending is not None:
        await _audit(ctx, f"agent.{action}", ",".join(map(str, ids[:50])), extra, {"pending": pending.data["approval_id"]})
        await ctx.db.commit()
        pending.conversation_ids = ids
        return pending
    store = await user_store(ctx.db, ctx.p.user)
    try:
        res = await act.apply(act.ActionContext(ctx.db, store, ctx.p), msgs, action, extra)
    except HTTPException as e:
        raise ToolError(str(e.detail)) from None
    from mailr.core import activity

    act_entry = activity.mail(action, msgs, ids, "conversation", res.get("undo"))
    await _audit(ctx, f"agent.{action}", ",".join(map(str, ids[:50])), extra,
                 {activity.KEY: act_entry} if act_entry else None)
    await ctx.db.commit()
    return {"ok": True, "affected_messages": res["affected"]}


_BULK_VERBS = {"archive": "Archive", "trash": "Move to Trash", "read": "Mark read", "unread": "Mark unread",
               "spam": "Report as spam", "not_important": "Mark not important", "important": "Mark important",
               "star": "Star", "unstar": "Unstar", "mute": "Mute", "snooze": "Snooze", "inbox": "Move to the inbox"}


async def _bulk(ctx: ToolCtx, ids: list[int], action: str, extra: dict[str, Any], where: str,
                left_alone: int = 0) -> dict[str, Any] | ToolResult:
    """An action on a whole scope ("this view except these"): gated like the Cleanup page's plans
    (one card with the counts and sample subjects), then run as a background bulk job with progress
    and Undo (core/approval_executors.py _cleanup_plan)."""
    from mailr.core import permissions
    from mailr.core.approval_executors import _cleanup_plan
    from mailr.models import Conversation

    rows = (await ctx.db.execute(select(Conversation.subject).where(Conversation.id.in_(ids[:20]))
                                 )).scalars().all()
    verb = _BULK_VERBS.get(action, action.replace("_", " ").capitalize())
    lines = [f"{len(ids)} conversations in {where}", *([f"Left alone: {left_alone}"] if left_alone else []),
             *[s or "(no subject)" for s in rows]]
    cls = {"trash": "mail.trash", "spam": "mail.spam"}.get(action, "mail.label")
    from mailr.core.visibility import any_local_only

    payload = {"op": "cleanup", "conversation_ids": ids, "action": action, "args": extra,
               "sensitive": await any_local_only(ctx.db, ctx.p.user_id, conversation_ids=ids)}
    pending = await gate(ctx, cls, target=f"{len(ids)} conversation(s)", payload=payload,
                         preview={"title": f"{verb} {len(ids)} conversations", "lines": lines[:24]},
                         ask_by_default=HIDE_REASON if ctx.untrusted and action in HIDE_ACTIONS else None,
                         force_ask=SCOPE_REASON if ctx.p.is_token else None)
    if pending is not None:
        await _audit(ctx, f"agent.{action}", where[:200], extra,
                     {"pending": pending.data["approval_id"], "conversations": len(ids)})
        await ctx.db.commit()
        pending.conversation_ids = ids[:50]
        return pending
    # The job acts as the requester: a token's connector limits and local-only flag still apply.
    res = await _cleanup_plan(ctx.db, ctx.p.user, permissions.stamp(payload, ctx.p))
    await _audit(ctx, f"agent.{action}", where[:200], extra, {"job": res.get("job"), "conversations": len(ids)})
    await ctx.db.commit()
    return {"ok": True, "job": res.get("job"), "running_in_background": True,
            "note": "Runs in the background with Undo; the user sees its progress."}


async def _run(ctx: ToolCtx, ids: list[int], action: str, extra: dict[str, Any],
               more: dict[str, Any]) -> dict[str, Any] | ToolResult:
    if more.get("scope"):
        return await _bulk(ctx, ids, action, extra, more["scope"], more.get("left_alone", 0))
    return await _action(ctx, ids, action, extra)


def _done(res: dict[str, Any] | ToolResult, ids: list[int], **extra: Any) -> ToolResult:
    if isinstance(res, ToolResult):
        return res
    return ToolResult({**res, **extra}, conversation_ids=ids)


# ACT mail.label @assistant
@tool("apply_label",
      "Add (or remove) a label on conversations. The label is found by name (nested labels use '/'); "
      "set create=true to create it if missing.",
      _obj({"conversation_ids": {"type": "array", "items": {"type": "integer"}},
            "label": {"type": "string"}, "remove": {"type": "boolean"}, "create": {"type": "boolean"}},
           ["conversation_ids", "label"]), scope="label")
async def apply_label(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.core.store import user_store

    ids = _ids(args, "conversation_ids")
    name = (args.get("label") or "").strip()
    if not name:
        raise ToolError("'label' is required")
    lb = (await ctx.db.execute(select(Label).where(Label.user_id == ctx.p.user_id,
                                                   Label.name.ilike(name)))).scalars().first()
    if lb is None:
        if not args.get("create"):
            raise ToolError(f"no label named {name!r} (pass create=true to create it)")
        # Creating a label is labels.write, like manage_label.
        pending = await gate(ctx, "labels.write", target=name, payload={"op": "label.create", "name": name},
                             preview={"title": f"Create label “{name}”", "kind": "label", "lines": [name]})
        if pending is not None:
            pending.data["note"] = ("Creating the label waits for the user's approval; nothing was labelled. "
                                    "After it is approved, call apply_label again.")
            return pending
        store = await user_store(ctx.db, ctx.p.user)
        lb = await store.create_label(ctx.db, name)
    res = await _action(ctx, ids, "remove_labels" if args.get("remove") else "add_labels", {"label_ids": [lb.id]})
    return _done(res, ids, label=lb.name)


async def _targets(ctx: ToolCtx, args: dict[str, Any]) -> tuple[list[int], dict[str, Any]]:
    """The conversations a bulk tool acts on: the ids given, or every conversation in `scope` (the list
    on screen, or a view and a search) less `except_ids` (agent/view_tools.py)."""
    from mailr.agent import view_tools

    if not view_tools.scope_ids(args):
        return _ids(args, "conversation_ids"), {}
    ids, lq = await view_tools.resolve_scope(ctx, args["scope"], args.get("except_ids"))
    name = await view_tools.scope_name(ctx.db, ctx.p, lq)
    if not ids:
        raise ToolError(f"no conversations in {name}" + (" after the exceptions" if args.get("except_ids") else ""))
    return ids, {"scope": name, "conversations": len(ids),
                 **({"left_alone": len(set(args.get("except_ids") or []))} if args.get("except_ids") else {})}


def _scoped(props: dict[str, Any]) -> dict[str, Any]:
    from mailr.agent.view_tools import EXCEPT_SCHEMA, SCOPE_SCHEMA

    return {**props, "scope": SCOPE_SCHEMA, "except_ids": EXCEPT_SCHEMA}


# ACT mail.archive @assistant
@tool("archive", "Archive conversations (remove them from the inbox; they stay searchable). Pass "
      "conversation_ids, or `scope` (\"screen\" for the whole list on screen, or {view, q}) with "
      "`except_ids` for the ones to keep (\"archive this view except these 9\").",
      _obj(_scoped({"conversation_ids": {"type": "array", "items": {"type": "integer"}}})),
      scope="label")
async def archive(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    ids, more = await _targets(ctx, args)
    return _done(await _run(ctx, ids, "archive", {}, more), ids if not more else ids[:50], **more)


_MODIFY = ["read", "unread", "star", "unstar", "important", "not_important", "inbox", "trash", "untrash",
           "spam", "not_spam", "snooze", "unsnooze", "mute", "unmute", "follow_up", "cancel_follow_up"]


# ACT mail.read @assistant, ACT mail.unread @assistant, ACT mail.star @assistant, ACT mail.important @assistant, ACT mail.delete @assistant, ACT mail.snooze @assistant,
# ACT mail.follow_up @assistant
@tool("modify_conversations",
      "Change conversations: read, unread, star, unstar, important, not_important, inbox (move back), "
      "trash, untrash, spam, not_spam, snooze (with `until`, ISO time), unsnooze, mute, unmute, "
      "follow_up (with `until`: if nobody has replied to the user's last message by then, the conversation "
      "comes back to the inbox marked No reply), cancel_follow_up. Trash and spam usually need the user's "
      "approval. Pass conversation_ids, or `scope` (\"screen\" for the whole list on screen, or {view, q}) "
      "with `except_ids` for the ones to leave alone (\"trash this view except these 9\"). Only act when the "
      "user asked.",
      _obj(_scoped({"conversation_ids": {"type": "array", "items": {"type": "integer"}},
                    "action": {"type": "string", "enum": _MODIFY},
                    "until": {"type": "string", "description": "snooze or follow-up time (ISO 8601)"}}),
           ["action"]), scope="label")
async def modify_conversations(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    action = args.get("action")
    if action not in _MODIFY:
        raise ToolError(f"action must be one of {', '.join(_MODIFY)}")
    timed = action in ("snooze", "follow_up")
    extra = {"until": args["until"]} if timed and args.get("until") else {}
    if timed and not extra:
        raise ToolError(f"{action} needs 'until'")
    ids, more = await _targets(ctx, args)
    return _done(await _run(ctx, ids, action, extra, more), ids if not more else ids[:50], action=action, **more)


# ACT mail.show_in_tab @assistant
@tool("show_in_tab",
      "Make conversations show in one tab of the user's inbox layout (the tab bar above the inbox: "
      "Inbox, a label tab such as Github, a category tab such as Promotions). `tab` is the tab's name "
      "or id. Mailr adds the tab's label or category and removes the labels of tabs that would "
      "otherwise take the conversation first. Tabs defined by a search or a state filter cannot be "
      "chosen. Only act when the user asked.",
      _obj({"conversation_ids": {"type": "array", "items": {"type": "integer"}},
            "tab": {"type": "string", "description": "tab name or id"}},
           ["conversation_ids", "tab"]), scope="label")
async def show_in_tab(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.api.mail_read import _settings
    from mailr.core import tabplace

    ids = _ids(args, "conversation_ids")
    want = str(args.get("tab") or "").strip()
    lay = tabplace.active_layout(await _settings(ctx.db, ctx.p))
    tabs = tabplace.targets(lay)
    t = next((x for x in tabs if x["id"] == want), None) or next(
        (x for x in tabs if x["name"].lower() == want.lower()), None)
    if t is None:
        raise ToolError(f"no tab {want!r}; the tabs are {', '.join(x['name'] for x in tabs)}")
    if t["kind"] == "disabled":
        raise ToolError(t["reason"])
    return _done(await _action(ctx, ids, "show_in_tab", {"tab": t["id"]}), ids, tab=t["name"])
