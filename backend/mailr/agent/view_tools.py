"""Agent tools over a whole view: list_view pages through every conversation of a list in short
rows, and resolve_scope turns "this view except these" into conversation ids for the bulk actions
(archive, modify_conversations, review_mail), so the chat never takes the top rows it was shown for
the whole view."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from mailr.agent.tools import UNTRUSTED, ToolCtx, ToolError, ToolResult, _audit, _obj, _u, tool

# How many conversations one bulk action or review may cover. Above it, the user narrows the search.
SCOPE_MAX = 2000

SCOPE_SCHEMA = {
    "description": "\"screen\" for the whole list on screen (every conversation in it, not only the rows "
                   "shown; on an inbox tab, every section of the tab, or the one section shown alone), or "
                   "{view, q} for a view and a search (Gmail syntax). {\"view\": \"inbox\"} is the Inbox "
                   "across all tabs (\"my whole inbox\"), not the tab on screen",
    "anyOf": [{"type": "string", "enum": ["screen"]},
              {"type": "object", "additionalProperties": False,
               "properties": {"view": {"type": "string"}, "q": {"type": "string"}}}],
}
EXCEPT_SCHEMA = {"type": "array", "items": {"type": "integer"},
                 "description": "conversations in the scope to leave alone"}


def scope_query(ctx: ToolCtx, scope: Any) -> dict[str, Any]:
    """The list query of a scope: the screen's list (its view, search and inbox section), or a view
    and a search the model names."""
    if scope == "screen" or scope is None:
        lq = (ctx.ui or {}).get("list")
        if not lq:
            raise ToolError("no list is on screen: pass scope as {view, q}")
        return dict(lq)
    if isinstance(scope, dict):
        view = str(scope.get("view") or "all")[:60]
        q = str(scope.get("q") or "")[:300]
        if view == "all" and not q.strip():
            raise ToolError("a scope over all mail needs a search (q)")
        return {"view": view, **({"q": q} if q else {})}
    raise ToolError("scope must be \"screen\" or {view, q}")


VIEW_NAMES = {"inbox": "Inbox", "all": "All Mail", "starred": "Starred", "important": "Important", "sent": "Sent",
              "spam": "Spam", "trash": "Trash", "snoozed": "Snoozed", "drafts": "Drafts"}


def _layout_name(settings: dict[str, Any], lq: dict[str, Any]) -> str | None:
    """A layout tab or section by its own names: "Inbox tab (all sections)", "Inbox: Unread"."""
    from mailr.core import layouts

    tab = next((t for t in (layouts.find(settings, lq["layout"]) or {}).get("tabs", [])
                if t["id"] == lq.get("tab")), None)
    if tab is None:
        return None
    secs = tab.get("sections") or []
    if lq.get("sec"):
        sec = next((x for x in secs if x["id"] == lq["sec"]), None)
        if sec is None:
            return None
        return f"{tab['name']}: {sec['title']}" if len(secs) > 1 else f"{tab['name']} tab"
    return f"{tab['name']} tab (all sections)" if len(secs) > 1 else f"{tab['name']} tab"


def inbox_tab_count(settings: dict[str, Any]) -> int:
    """How many tabs the user's inbox has (the active layout)."""
    from mailr.core import layouts

    lay = layouts.find(settings, layouts.active_id(settings)) or layouts.find(settings, "default")
    return len((lay or {}).get("tabs") or [])


def describe_query(lq: dict[str, Any], settings: dict[str, Any] | None = None) -> str:
    """The scope's name, unambiguous (bulk review, 20 Oct 2026): with the user's settings, a tab is
    "<Tab> tab (all sections)", a section "<Tab>: <Section>", and the Inbox folder without a layout
    "Inbox (all tabs)" when the inbox has more than one tab."""
    where = None
    if settings is not None and lq.get("layout"):
        where = _layout_name(settings, lq)
    elif settings is not None and lq["view"] == "inbox":
        where = "Inbox (all tabs)" if inbox_tab_count(settings) > 1 else "Inbox"
    where = where or lq.get("name") or VIEW_NAMES.get(lq["view"], f"view {lq['view']}")
    return where + (f' matching "{lq["q"]}"' if lq.get("q") else "")


async def scope_name(db, p, lq: dict[str, Any]) -> str:
    """describe_query with the user's layouts."""
    from mailr.api.mail_read import _settings

    return describe_query(lq, await _settings(db, p))


async def resolve_scope(ctx: ToolCtx, scope: Any, except_ids: list[int] | None = None,
                        cap: int = SCOPE_MAX) -> tuple[list[int], dict[str, Any]]:
    """Every conversation id in the scope (newest first), less the exceptions. Frozen at the call:
    mail that arrives later is not included."""
    from mailr.api.mail_read import LIST_ORDER, conversation_conditions
    from mailr.models import Conversation

    lq = scope_query(ctx, scope)
    try:
        conds = await conversation_conditions(ctx.db, ctx.p, lq["view"], lq.get("q", ""), None, "", "", None,
                                              lq.get("layout"), lq.get("tab"), lq.get("sec"))
    except HTTPException as e:
        raise ToolError(str(e.detail)) from None
    if conds is None:
        return [], lq
    ids = list((await ctx.db.execute(select(Conversation.id).where(*conds).order_by(*LIST_ORDER)
                                     .limit(cap + 1))).scalars())
    if len(ids) > cap:
        raise ToolError(f"more than {cap} conversations in {await scope_name(ctx.db, ctx.p, lq)}: narrow the search "
                        "(for example older_than:30d or from:)")
    skip = {int(x) for x in except_ids or []}
    return [i for i in ids if i not in skip], lq


def scope_ids(args: dict[str, Any]) -> bool:
    """The bulk tools take conversation_ids or a scope, not both."""
    if args.get("scope") is not None and args.get("conversation_ids"):
        raise ToolError("pass conversation_ids or scope, not both")
    return args.get("scope") is not None


# ACT agent.list_view @assistant
@tool("list_view",
      "Page through every conversation of a list in short rows (sender, subject, date, a short preview, "
      "unread/starred/important), up to 100 a page with `cursor` for the next page. Default: the list on "
      "screen (its view and search; on an inbox tab every section of the tab, or the one section shown alone), "
      "so \"this view\" is read whole, not only the rows shown. \"My inbox\" is scope {\"view\": \"inbox\"}: "
      "the Inbox across all tabs. "
      "For bulk changes prefer archive or modify_conversations with `scope`, or review_mail. " + UNTRUSTED,
      _obj({"scope": SCOPE_SCHEMA, "cursor": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}))
async def list_view(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.api.mail_read import list_conversations

    lq = scope_query(ctx, args.get("scope"))
    try:
        res = await list_conversations(request=None, view=lq["view"], q=lq.get("q", ""), section=None, sections="",  # pyright: ignore[reportArgumentType]  # the route, called directly (as search_conversations does)
                                       exclude="", category=None, layout=lq.get("layout"), tab=lq.get("tab"),
                                       sec=lq.get("sec"), cursor=args.get("cursor") or None,
                                       limit=min(int(args.get("limit") or 50), 100), count=None, p=ctx.p, db=ctx.db)
    except HTTPException as e:
        raise ToolError(str(e.detail)) from None

    def row(c: dict[str, Any]) -> dict[str, Any]:
        who = next((x.get("name") or x.get("email") for x in c["participants"]
                    if not x.get("me") or x.get("shared")), "") or ""
        return {"conversation_id": c["id"], "from": who, "subject": c["subject"] or "(no subject)",
                "date": (c["last_date"] or "")[:10] if isinstance(c["last_date"], str) else
                (c["last_date"].date().isoformat() if c["last_date"] else None),
                "preview": (c["snippet"] or "")[:140],
                **{k: True for k in ("starred", "important") if c.get(k)},
                **({"unread": True} if c.get("unread_count") else {})}

    name = await scope_name(ctx.db, ctx.p, lq)
    await _audit(ctx, "agent.list_view", name[:200])
    return ToolResult({"list": name, "total": res["total"],
                       "rows": _u([row(c) for c in res["items"]]),
                       "next_cursor": res["next_cursor"]},
                      conversation_ids=[c["id"] for c in res["items"]])
