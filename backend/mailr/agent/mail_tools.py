"""Agent tools that search and read mail: conversations, message bodies, passages, people, labels
and reply context. Registered in agent/tools.py, which re-exports them."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from mailr.agent.tools import (
    UNTRUSTED,
    ToolCtx,
    ToolError,
    ToolResult,
    _audit,
    _conv_summary,
    _int,
    _label_names,
    _obj,
    _u,
    local_ok,
    query_sensitive,
    tool,
)

# ---------------------------------------------------------------- read tools


@tool("search_conversations",
      "Search the user's mail with Gmail search syntax (from:, to:, subject:, label:, is:unread, "
      "has:attachment, newer_than:7d, before:2026/01/01, \"exact phrase\", OR, -exclude). Returns "
      "conversation summaries, newest first. " + UNTRUSTED,
      _obj({"q": {"type": "string", "description": "Gmail-style query; empty lists the view"},
            "view": {"type": "string", "description": "inbox, starred, important, sent, drafts, all (default), spam, trash, snoozed, label:<id>"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50}}))
async def search_conversations(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.api.mail_read import list_conversations
    from mailr.core.search import RECORDING_SUMMARIES_LOCAL_OK

    q = args.get("q") or ""
    # recording:<text> reads stored Speakr summaries; a hosted model gets no match from a
    # local-only account's summaries.
    token = RECORDING_SUMMARIES_LOCAL_OK.set(True if "recording:" not in q.lower() else await local_ok(ctx))
    try:
        res = await list_conversations(request=None, view=args.get("view") or "all", q=q,
                                       section=None, exclude="", category=None, cursor=None,
                                       limit=min(int(args.get("limit") or 20), 50), p=ctx.p, db=ctx.db)
    except HTTPException as e:
        raise ToolError(str(e.detail)) from None
    finally:
        RECORDING_SUMMARIES_LOCAL_OK.reset(token)
    labels = await _label_names(ctx.db, ctx.p.user_id)
    items = [_conv_summary(c, labels) for c in res["items"]]
    return ToolResult({"total": res["total"], "items": items}, conversation_ids=[c["id"] for c in res["items"]])


@tool("get_conversation",
      "Read a whole conversation: every message with sender, recipients, date, attachments and the text "
      "body (quoted history removed). " + UNTRUSTED,
      _obj({"conversation_id": {"type": "integer"}}, ["conversation_id"]))
async def get_conversation(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.api.mail_read import load_conversation
    from mailr.intel.citations import url_for

    cid = _int(args, "conversation_id")
    try:
        conv, msgs = await load_conversation(ctx.db, ctx.p, cid)
    except HTTPException as e:
        raise ToolError(str(e.detail)) from None
    labels = await _label_names(ctx.db, ctx.p.user_id)
    out = {
        "conversation_id": conv.id, "subject": _u(conv.subject),
        "labels": [labels[i] for i in conv.label_ids or [] if i in labels],
        "url": url_for(conv.id),
        "messages": [{
            "message_id": m.id, "url": url_for(conv.id, m.id), "date": (m.date or m.received_at).isoformat() if (m.date or m.received_at) else None,
            "from": "me" if m.own_words else f"{m.from_name} <{m.from_email}>".strip(),
            **({"shared_from_another_app": True} if m.origin else {}),
            "to": [a["email"] for a in m.to or []], "cc": [a["email"] for a in m.cc or []],
            "draft": m.draft, "unread": not m.seen,
            "attachments": [a.get("filename") for a in m.attachments or [] if not a.get("inline")],
            "body": _u((m.body_text or m.snippet)[:6000]),
        } for m in msgs],
    }
    await _audit(ctx, "agent.get_conversation", str(cid))
    ctx.opened.add(conv.id)
    return ToolResult(out, message_ids=[m.id for m in msgs], conversation_ids=[conv.id])


# ACT agent.read_screen @assistant
@tool("read_screen",
      "The mail on the user's screen with subjects and senders: the open conversation, other open tabs, "
      "the checked rows and the rows the list shows (top first). On screen names them by id only; read "
      "them here when the question is about these emails (\"anything urgent here?\", \"archive the "
      "newsletters on screen\"), not for unrelated requests. " + UNTRUSTED,
      _obj({"which": {"type": "string", "enum": ["all", "open", "tabs", "selected", "list"],
                      "description": "one group, or all (default)"}}))
async def read_screen(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.agent import screen
    from mailr.intel.citations import url_for
    from mailr.models import AssistantRun

    ui = ctx.ui or {}
    which = args.get("which") or "all"
    by = await screen.screen_conversations(ctx.db, ctx.p, ui)
    briefs = set((await ctx.db.execute(select(AssistantRun.conversation_id).where(
        AssistantRun.user_id == ctx.p.user_id, AssistantRun.conversation_id.in_(list(by) or [0]),
        AssistantRun.status == "done", AssistantRun.delivered.contains(["brief"])))).scalars()) if by else set()

    def row(cid: int) -> dict[str, Any] | None:
        c = by.get(cid)
        if c is None:
            return None
        # A share from another app (participant "shared") is named, never folded into "me".
        who = next((x.get("name") or x.get("email") for x in (c.participants or [])
                    if not x.get("me") or x.get("shared")), "") or ""
        return {"conversation_id": cid, "url": url_for(cid), "subject": _u(c.subject or "(no subject)"),
                "from": _u(who), "last_date": c.last_date.isoformat() if c.last_date else None,
                "messages": c.message_count, "unread": bool(c.unread_count),
                **({"has_brief": True} if cid in briefs else {})}

    out: dict[str, Any] = {"view": ui.get("view"), **({"search": ui["q"]} if ui.get("q") else {})}
    shown: list[int] = []
    if which in ("all", "open") and ui.get("conversation_id") and (r := row(ui["conversation_id"])):
        out["open"] = r
        shown.append(r["conversation_id"])
    for key, name, _title in screen.MAIL_GROUPS:
        if which not in ("all", name):
            continue
        rows = [r for r in (row(i) for i in ui.get(key, []) if i != ui.get("conversation_id")) if r]
        if rows:
            out[name] = rows
            shown += [r["conversation_id"] for r in rows]
        if name == "selected" and ui.get("selected_all_matching"):
            out["selected_all_matching"] = True
    if not shown:
        out["note"] = "No conversations are on screen."
    await _audit(ctx, "agent.read_screen", which)
    return ToolResult(out, conversation_ids=list(dict.fromkeys(shown)))


@tool("get_message_body",
      "Read one message's full text, including quoted history and the plain-text rendering of HTML. "
      "`attachments` lists its files by index (fetch one with get_attachment). " + UNTRUSTED,
      _obj({"message_id": {"type": "integer"}}, ["message_id"]))
async def get_message_body(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.api.mail_read import load_message
    from mailr.core.mime import parse
    from mailr.core.store import user_store

    mid = _int(args, "message_id")
    try:
        m = await load_message(ctx.db, ctx.p, mid)
    except HTTPException as e:
        raise ToolError(str(e.detail)) from None
    store = await user_store(ctx.db, ctx.p.user)
    parsed = await asyncio.to_thread(parse, await store.get_raw(m))  # off the event loop
    await _audit(ctx, "agent.get_message_body", str(mid))
    if m.conversation_id:
        ctx.opened.add(m.conversation_id)
    from mailr.agent.attachment_tools import attachment_list

    return ToolResult({"message_id": m.id, "conversation_id": m.conversation_id, "subject": _u(m.subject),
                       # The sender's display name and the file names are the sender's text too.
                       "from": _u(f"{m.from_name} <{m.from_email}>"), "text": _u(parsed.text[:20000]),
                       "attachments": _u(attachment_list(m))},
                      message_ids=[m.id], conversation_ids=[m.conversation_id] if m.conversation_id else [])


@tool("search_semantic",
      "Find conversations by meaning rather than exact words (\"the thread where we discussed the "
      "budget overrun\"). Each result includes its best matching passage (passage_id, message_id, text). "
      "Falls back to full-text search when the semantic index is unavailable. " + UNTRUSTED,
      _obj({"q": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 30}}, ["q"]))
async def search_semantic(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.api.schemas import conversation_out
    from mailr.intel import embed, search
    from mailr.intel.citations import url_for
    from mailr.models import Conversation

    q = (args.get("q") or "").strip()
    if not q:
        raise ToolError("'q' is required")
    limit = min(int(args.get("limit") or 10), 30)
    qs = query_sensitive(ctx)
    semantic = (await embed.current(ctx.db, sensitive=qs)) is not None
    ranked = await search.conversations(ctx.db, ctx.p, q, limit=limit, query_sensitive=qs)
    if not ranked:
        res = await search_conversations(ctx, {"q": q, "view": "all", "limit": limit})
        res.data["mode"] = "fulltext"
        return res
    convs = {c.id: c for c in (await ctx.db.execute(select(Conversation).where(
        Conversation.id.in_([r["conversation_id"] for r in ranked])))).scalars()}
    labels = await _label_names(ctx.db, ctx.p.user_id)
    from mailr.api.mail_read import token_views

    views = await token_views(ctx.db, ctx.p, list(convs))
    out, mids = [], []
    for r in ranked:
        c = convs.get(r["conversation_id"])
        if c is None:
            continue
        b = r["best"]
        mids.append(b.message_id)
        out.append({**_conv_summary(conversation_out(c, views.get(c.id)), labels), "score": round(r["score"], 4),
                    "best_passage": {"passage_id": b.passage_id, "message_id": b.message_id,
                                     "text": _u(b.text[:700]), "url": url_for(c.id, b.message_id)}})
    await _audit(ctx, "agent.search_semantic", q[:200], {"q": q}, {"count": len(out)})
    return ToolResult({"mode": "semantic" if semantic else "keyword-passages", "items": out},
                      message_ids=mids, conversation_ids=[i["conversation_id"] for i in out])


@tool("find_passages",
      "Find the exact passages (paragraphs) of email that answer a question, optionally within one "
      "conversation. Returns passage_id, message_id, conversation_id and the passage text. Use this to "
      "locate what to quote and cite: cite with [[m:<message_id> \"short verbatim quote\"]] or "
      "[[p:<passage_id>]]. " + UNTRUSTED,
      _obj({"q": {"type": "string", "description": "what you are looking for, in natural language or keywords"},
            "conversation_id": {"type": "integer"},
            "k": {"type": "integer", "minimum": 1, "maximum": 20}}, ["q"]))
async def find_passages(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.intel import search
    from mailr.intel.citations import url_for
    from mailr.models import Message as M

    q = (args.get("q") or "").strip()
    if not q:
        raise ToolError("'q' is required")
    cid = _int(args, "conversation_id", required=False)
    hits = await search.passages(ctx.db, ctx.p, q, conversation_id=cid, k=min(int(args.get("k") or 8), 20),
                                 query_sensitive=query_sensitive(ctx))
    meta = {}
    if hits:
        meta = {m.id: m for m in (await ctx.db.execute(select(M).where(
            M.id.in_([h.message_id for h in hits])))).scalars()}
    items = []
    for h in hits:
        m = meta.get(h.message_id)
        items.append({"passage_id": h.passage_id, "message_id": h.message_id, "conversation_id": h.conversation_id,
                      "url": url_for(h.conversation_id, h.message_id),
                      "from": ("me" if m and m.own_words else _u(f"{m.from_name} <{m.from_email}>")) if m else None,
                      **({"shared_from_another_app": True} if m is not None and m.origin else {}),
                      "subject": _u(m.subject) if m else None,
                      "date": (m.date.isoformat() if m and m.date else None),
                      "text": _u(h.text), "score": round(h.score, 4)})
    await _audit(ctx, "agent.find_passages", q[:200], {"q": q, "conversation_id": cid}, {"count": len(items)})
    return ToolResult({"items": items}, message_ids=[h.message_id for h in hits],
                      conversation_ids=[h.conversation_id for h in hits if h.conversation_id])


@tool("get_person",
      "Profile of a correspondent: name, organisation, how often you exchange mail, first/last contact, "
      "recent conversations and threads waiting on your reply.",
      _obj({"email": {"type": "string"}}, ["email"]))
async def get_person(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.api.org_routes import person

    email = (args.get("email") or "").strip().lower()
    if "@" not in email:
        raise ToolError("'email' must be an email address")
    try:
        res = await person(email=email, p=ctx.p, db=ctx.db)
    except HTTPException as e:
        raise ToolError(str(e.detail)) from None
    labels = await _label_names(ctx.db, ctx.p.user_id)
    res["recent_conversations"] = [_conv_summary(c, labels) for c in res.get("recent_conversations", [])]
    res["notes"] = _u(res.get("notes", ""))
    from mailr.agent.people_tools import scrub_person

    res.pop("knowledge", None)  # given below, as `known`
    local_details = await scrub_person(ctx, res)
    # What the user's memory knows about this person (entities link to contacts by address).
    from mailr.knowledge import memory as kb

    ent = await kb.find_entity(ctx.db, ctx.p, email)
    sensitive = False
    if ent is not None:
        notes = await kb.recall(ctx.db, ctx.p, "", entity_ids=[ent.id], k=10, query_sensitive=query_sensitive(ctx))
        res["known"] = {"entity_id": ent.id, "name": ent.name, "kind": ent.kind, "summary": ent.summary,
                        "aliases": ent.aliases, "memories": [{"id": m.id, "text": m.text} for m in notes]}
        sensitive = ent.sensitive or any(m.sensitive for m in notes)
    # Speakr recordings with this person (mapped speakers or their name; integrations/speakr/people.py).
    from mailr.agent.speakr_tools import person_recordings

    recs, rec_sensitive = await person_recordings(ctx, email)
    if recs is not None:
        res["recent_recordings"] = recs
        sensitive = sensitive or rec_sensitive
    return ToolResult(res, conversation_ids=[c["conversation_id"] for c in res["recent_conversations"]],
                      sensitive=sensitive or local_details)


@tool("list_labels", "List the user's labels (system and custom) with unread and total counts.", _obj({}))
async def list_labels(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.api.mail_read import list_labels as route

    res = await route(p=ctx.p, db=ctx.db)
    return ToolResult([{"id": x["id"], "name": x["name"], "role": x["role"], "unread": x["unread"],
                        "total": x["total"]} for x in res])


@tool("build_context",
      "Gather everything needed to answer or write about a conversation: the thread, the people in it "
      "(history, organisation), related conversations, your past replies to them (for tone), unanswered "
      "questions, your open commitments, and calendar availability for dates mentioned. " + UNTRUSTED,
      _obj({"conversation_id": {"type": "integer"},
            "query": {"type": "string", "description": "topic to gather context about when there is no conversation"},
            "people": {"type": "array", "items": {"type": "string"}},
            "budget_tokens": {"type": "integer", "minimum": 1000, "maximum": 30000}}))
async def build_context(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.agent import context as cx

    cid = _int(args, "conversation_id", required=False)
    if cid is None and not args.get("query") and not args.get("people"):
        raise ToolError("give a conversation_id, a query, or people")
    try:
        bundle = await cx.build(ctx.db, ctx.p, conversation_id=cid, query=args.get("query"),
                                people=args.get("people"), budget_tokens=int(args.get("budget_tokens") or 8000),
                                allow_local_calendar=await local_ok(ctx))
    except LookupError as e:
        raise ToolError(str(e)) from None
    await _audit(ctx, "agent.build_context", str(cid or args.get("query") or ""))
    return ToolResult(bundle, message_ids=cx.message_ids_in(bundle), conversation_ids=cx.conversation_ids_in(bundle),
                      sensitive=bool(bundle.get("local_only") or (bundle.get("calendar") or {}).get("local_only")))
