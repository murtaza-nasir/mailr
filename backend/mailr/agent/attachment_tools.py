"""Agent tools for attachments: list_attachments and get_attachment.

get_attachment returns what a model can use (core/attachfetch.py): text, a downscaled image
without metadata, or a PDF's text and one rendered page. The message is loaded with the same
visibility rules as get_message_body (a token sees only its accounts, and local-only mail only
when the token is local_only). Every fetch is audited; extracted text is wrapped as untrusted.

Images reach the caller as {"_content": [{"type": "image", "data": <base64>, "mimeType"}]}.
The MCP server turns that into an MCP image block (mcp/server.py); REST callers read the base64.
The built-in assistant's chat models have no vision flag, so it gets "image attached (w×h)".
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException

from mailr.agent.tools import (
    UNTRUSTED,
    ToolCtx,
    ToolError,
    ToolResult,
    _audit,
    _int,
    _obj,
    _u,
    tool,
)
from mailr.core import attachfetch
from mailr.models import Message


def attachment_list(m: Message) -> list[dict[str, Any]]:
    """A message's attachments with the index get_attachment takes (the position in this list)."""
    return [{"index": i, "filename": a.get("filename") or "attachment", "type": a.get("type") or "",
             "size": a.get("size") or 0, "inline": bool(a.get("inline"))}
            for i, a in enumerate(m.attachments or [])]


def _pick(m: Message, ref: Any) -> tuple[int, dict[str, Any]]:
    atts = m.attachments or []
    if not atts:
        raise ToolError("this message has no attachments")
    if isinstance(ref, bool):
        ref = None
    if isinstance(ref, str) and ref.strip().isdigit() and not any(
            (a.get("filename") or "") == ref.strip() for a in atts):
        ref = int(ref.strip())
    if isinstance(ref, int):
        if 0 <= ref < len(atts):
            return ref, atts[ref]
        raise ToolError(f"no attachment {ref}: this message has {len(atts)} (indexes 0–{len(atts) - 1})")
    if isinstance(ref, str) and ref.strip():
        name = ref.strip()
        for want in (name, name.lower()):
            for i, a in enumerate(atts):
                fn = a.get("filename") or "attachment"
                if fn == want or fn.lower() == want:
                    return i, a
        raise ToolError(f"no attachment named {name[:120]!r}; list_attachments shows the names")
    raise ToolError("'attachment' must be an index (from list_attachments) or a file name")


async def _load(ctx: ToolCtx, mid: int) -> Message:
    from mailr.api.mail_read import load_message

    try:
        return await load_message(ctx.db, ctx.p, mid)
    except HTTPException as e:
        raise ToolError(str(e.detail)) from None


@tool("list_attachments",
      "List the attachments of one message, or of every message in a conversation: index, file name, "
      "type, size in bytes and whether it is inline (an image in the body). Fetch one with get_attachment. "
      + UNTRUSTED,
      _obj({"message_id": {"type": "integer"},
            "conversation_id": {"type": "integer", "description": "instead of message_id: every message"}}))
async def list_attachments(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.api.mail_read import load_conversation

    mid, cid = _int(args, "message_id", required=False), _int(args, "conversation_id", required=False)
    if mid is None and cid is None:
        raise ToolError("give 'message_id' or 'conversation_id'")
    if mid is not None:
        msgs = [await _load(ctx, mid)]
    else:
        try:
            _, msgs = await load_conversation(ctx.db, ctx.p, cid)
        except HTTPException as e:
            raise ToolError(str(e.detail)) from None
    items = [{"message_id": m.id, **a} for m in msgs for a in attachment_list(m)]
    await _audit(ctx, "agent.list_attachments", str(mid or cid), {"message_id": mid, "conversation_id": cid},
                 {"count": len(items)})
    conv_ids = sorted({m.conversation_id for m in msgs if m.conversation_id})
    return ToolResult({"items": items}, message_ids=[m.id for m in msgs], conversation_ids=conv_ids)


@tool("get_attachment",
      "Fetch one attachment of a message in a form you can read. Images (PNG, JPEG, GIF, WebP) come back "
      "as an image, upright, scaled so the long side is at most max_px, without metadata. PDFs come "
      "back as their text plus one page as an image (`page`, default 1; with `page`, only that page's "
      "text). Text, Markdown, CSV, JSON, calendar files, HTML and attached emails come back as text "
      "(at most 200,000 characters). Other types return only name, size and type. Files over 25 MB "
      "are refused. " + UNTRUSTED,
      _obj({"message_id": {"type": "integer"},
            "attachment": {"type": ["integer", "string"],
                           "description": "the index from list_attachments or get_message_body, or the file name"},
            "max_px": {"type": "integer", "minimum": 256, "maximum": 4096,
                       "description": "longest side of a returned image in pixels (default 1600)"},
            "page": {"type": "integer", "minimum": 1, "description": "PDFs: the page to read and render"}},
           ["message_id", "attachment"]))
async def get_attachment(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.core.store import user_store

    mid = _int(args, "message_id")
    page = _int(args, "page", required=False)
    if page is not None and page < 1:
        raise ToolError("'page' starts at 1")
    try:
        max_px = attachfetch.clamp_px(args.get("max_px"))
        attachfetch.check_rate(f"t{ctx.p.token.id}" if ctx.p.token else f"u{ctx.p.user_id}")
    except attachfetch.AttachmentError as e:
        raise ToolError(str(e)) from None
    m = await _load(ctx, mid)
    if ctx.assistant and m.local_only and not ctx.session_sensitive:
        # Local-only mail never goes to a hosted model: the chat must be able to
        # continue on a local one, which the next model call then uses (agent/loop.py).
        from mailr.intel import llm

        if not await llm.available(ctx.db, ctx.p.user, "chat", sensitive=True):
            raise ToolError("this attachment is from a local-only account and no local model is configured, "
                            "so it cannot be read in this chat")
    index, meta = _pick(m, args.get("attachment"))
    info = {"index": index, "filename": meta.get("filename") or "attachment", "type": meta.get("type") or "",
            "size": meta.get("size") or 0, "inline": bool(meta.get("inline"))}
    base = {"message_id": m.id, "conversation_id": m.conversation_id, "attachment": info}
    ids = {"message_ids": [m.id], "conversation_ids": [m.conversation_id] if m.conversation_id else []}

    async def refuse(reason: str) -> ToolError:
        await _audit(ctx, "agent.get_attachment", f"{m.id}/{index}", {"attachment": index}, {"refused": reason})
        return ToolError(reason)

    if info["size"] > attachfetch.MAX_ORIGINAL:
        raise await refuse(f"the attachment is {info['size'] / 1048576:.1f} MB; files over "
                           f"{attachfetch.MAX_ORIGINAL // 1048576} MB are not returned")
    store = await user_store(ctx.db, ctx.p.user)
    raw = await store.get_raw(m)
    try:
        data, ctype, fname, charset = await asyncio.to_thread(attachfetch.part_bytes, raw, int(meta.get("part", -1)))
    except (KeyError, TypeError, ValueError):
        raise await refuse("the attachment is missing from the stored message") from None
    try:
        res = await attachfetch.extract(data, ctype or info["type"], fname or info["filename"], charset,
                                        max_px=max_px, page=page, render=ctx.images)
    except attachfetch.AttachmentError as e:
        raise await refuse(str(e)) from None
    info["size"] = len(data)
    out: dict[str, Any] = {**base, "kind": res["kind"]}
    for k in ("pages", "page", "truncated", "note"):
        if k in res:
            out[k] = res[k]
    if "text" in res:
        out["text"] = _u(res["text"])
    img = res.get("image")
    if img:
        shape = {k: img[k] for k in ("mime_type", "width", "height", "bytes", "original_width",
                                     "original_height", "page") if k in img}
        if ctx.images:
            out["image"] = shape
            out["_content"] = [{"type": "image", "data": img["data"], "mimeType": img["mime_type"]}]
        else:
            out["image"] = {**shape, "note": f"image attached ({img['width']}×{img['height']}); this chat "
                                             "model cannot view images, so only its size is shown"}
    await _audit(ctx, "agent.get_attachment", f"{m.id}/{index}",
                 {"attachment": index, "max_px": max_px, "page": page},
                 {"kind": res["kind"], "type": ctype, "bytes_in": len(data),
                  "image_bytes": (img or {}).get("bytes") if ctx.images else None,
                  "text_chars": len(res.get("text") or "")})
    return ToolResult(out, **ids)
