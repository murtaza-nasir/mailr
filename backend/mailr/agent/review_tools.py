"""review_mail: the bulk review (agent/review.py) from the chat, `/sweep`, or a routine's errand."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from mailr.agent.tools import ToolCtx, ToolError, ToolResult, _audit, _obj, tool
from mailr.agent.view_tools import EXCEPT_SCHEMA, SCOPE_SCHEMA


# ACT agent.review_mail @assistant
@tool("review_mail",
      "Review every conversation in a scope and propose what to trash, archive, label or mark read, on one "
      "review card the user approves (nothing changes before). Use it to clean up a whole view or search "
      "(\"clean up this view\", \"go through my promotions\", /sweep) instead of reading conversations one by "
      "one. Scope \"screen\" is the list on screen: on an inbox tab every section of the tab (or the one "
      "section shown alone); \"my inbox\" or \"my whole inbox\" is {\"view\": \"inbox\"}, the Inbox across all "
      "tabs. Starred, important and replied-to conversations, people the user writes to and invitations are "
      "kept without asking; put the user's own exceptions in `except_ids` and their wishes in `instructions` "
      "(\"keep anything about running\"). It runs in the background: tell the user the card will appear in "
      "this chat, and do not wait for it.",
      _obj({"scope": SCOPE_SCHEMA, "except_ids": EXCEPT_SCHEMA,
            "instructions": {"type": "string", "description": "what the user wants kept or removed, in their words"},
            "labels": {"type": "array", "items": {"type": "string"},
                       "description": "existing labels the review may add (the user named them)"}}),
      scope="label")
async def review_mail(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.agent import review
    from mailr.models import MailReview

    if not ctx.assistant:
        raise ToolError("review_mail runs in the assistant's chat in Mailr; use list_view and archive or "
                        "modify_conversations with a scope instead")
    if ctx.chat_session_id:
        busy = (await ctx.db.execute(select(MailReview).where(
            MailReview.user_id == ctx.p.user_id, MailReview.chat_session_id == ctx.chat_session_id,
            MailReview.status.in_(("running", "stopping"))))).scalars().first()
        if busy is not None:
            return ToolResult({**review.progress(busy), "status": "already_running",
                               "note": "A review is already running in this chat; its card will appear here."},
                              events=[{"type": "review", "review_id": busy.id}])
    r = await review.start(ctx, args.get("scope"), str(args.get("instructions") or ""),
                           [int(x) for x in args.get("except_ids") or []], args.get("labels"))
    await _audit(ctx, "agent.review_mail", (r.scope or {}).get("label", "")[:200], None,
                 {"review_id": r.id, "conversations": len(r.ids)})
    if not r.ids:
        return ToolResult({"status": "nothing_to_review", "review_id": r.id, "scope": r.scope.get("label")})
    return ToolResult({**review.progress(r), "status": "review_started",
                       "note": "The review runs in the background and ends in a review card in this chat. Tell "
                               "the user in one sentence, naming the scope and the number of conversations as "
                               "given here, so a wrong scope can be stopped; do not list the conversations."},
                      events=[{"type": "review", "review_id": r.id}])
