"""Agent tools for drafts: create, list, read, edit and send, behind the draft gate (`_draft_gate`)
and the send gate. Registered in agent/tools.py, which re-exports them."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from mailr.agent.tools import (
    ToolCtx,
    ToolError,
    ToolResult,
    _audit,
    _int,
    _obj,
    approval_url,
    gate,
    pending_result,
    tool,
)
from mailr.models import Message, Outbox

# ---------------------------------------------------------------- drafts


_PAYLOAD_PROPS = {
    "to": {"type": "array", "items": {"type": "string"},
           "description": "email addresses; \"group:<name>\" adds a contact group's members (list_groups)"},
    "cc": {"type": "array", "items": {"type": "string"}},
    "bcc": {"type": "array", "items": {"type": "string"}},
    "subject": {"type": "string"},
    "body": {"type": "string", "description": "the new text you write (plain text or simple HTML); quoted history is added automatically for replies. "
             "It may use simple formatting: **bold**, bullet and numbered lists, [links](url); no tables, headings or code blocks"},
    "identity_id": {"type": "integer", "description": "send-as identity; default picks the address the original was sent to"},
}


def _addrs(v: Any) -> list[dict[str, str]]:
    if not v:
        return []
    if isinstance(v, str):
        v = [x for x in v.replace(";", ",").split(",")]
    out = []
    for a in v:
        if isinstance(a, dict) and a.get("email"):
            out.append({"email": a["email"].strip(), "name": a.get("name", "")})
        elif isinstance(a, str) and "@" in a:
            from email.utils import parseaddr

            name, addr = parseaddr(a.strip())
            if addr:
                out.append({"email": addr, "name": name})
    return out


def is_html(body: str) -> bool:
    b = body or ""
    return "<" in b and ">" in b and any(t in b.lower() for t in ("<p", "<div", "<br", "<ul", "<ol", "<b>", "<a "))


def body_html(body: str) -> str:
    """A body an agent wrote: simple HTML as it is (cleaned); plain text and the Markdown models
    write by habit (**bold**, bullet lists, links) as email HTML (core/mdmail.py)."""
    from mailr.core import mdmail
    from mailr.core.compose import clean_html

    b = (body or "").strip()
    if is_html(b):
        return clean_html(b)
    return mdmail.to_html(b)


def agent_html(body: str) -> str:
    """body_html of an agent's text, with no remote images: they load when the draft is opened,
    and go to every recipient when it is sent; and every link shows its address (agent/agentwrite.py)."""
    from mailr.agent import agentwrite

    return agentwrite.reveal_links_html(
        agentwrite.strip_remote_images_html(body_html(agentwrite.strip_markdown_images(body))))


def _signature(sig: str) -> str:
    return f'<div><br></div><div class="mailr_signature">-- <br>{sig}</div>' if sig else ""


async def sending_identity(db: Any, user_id: int, identity_id: Any = None) -> Any:
    """The identity a draft is sent as: the one named (if it is the user's), else the default."""
    from mailr.models import Identity

    ident = await db.get(Identity, int(identity_id)) if identity_id else None
    if ident is None or ident.user_id != user_id:
        ident = (await db.execute(select(Identity).where(Identity.user_id == user_id)
                                      .order_by(Identity.is_default.desc(), Identity.id))).scalars().first()
    return ident


async def chat_signature(db: Any, p: Any, sensitive: bool) -> str:
    """The signature the assistant's chat prompt shows: of the default identity among those the
    caller can see (a token limited to some accounts sees only theirs), and none when it is a
    local-only account's and the chat is not kept on a local model."""
    from mailr.models import Connector, Identity

    rows = (await db.execute(select(Identity).where(Identity.user_id == p.user_id)
                             .order_by(Identity.is_default.desc(), Identity.id))).scalars().all()
    for ident in rows:
        c = await db.get(Connector, ident.connector_id) if ident.connector_id else None
        if c is not None and not p.can_see_connector(c):
            continue
        if c is not None and c.data_class == "local_only" and not sensitive:
            return ""
        return signature_text(ident.signature_html)
    return ""


async def identity_local_only(db: Any, ident: Any) -> bool:
    """True when mail from this identity goes out through a local-only account: its signature and
    what is written as it stay on a local model."""
    from mailr.models import Connector

    if ident is None or not ident.connector_id:
        return False
    c = await db.get(Connector, ident.connector_id)
    return bool(c is not None and c.data_class == "local_only")


def signature_text(sig_html: str) -> str:
    """A signature as plain text, without the "-- " separator line."""
    from mailr.agent.screen import html_to_text

    lines = html_to_text(sig_html or "").splitlines()
    return "\n".join(x.rstrip() for x in lines if x.strip() not in ("--", "")).strip()


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def strip_repeated_signoff(body: str, sig_text: str) -> str:
    """The body without a closing block that repeats the signature (models write "Thanks, Sam"
    although the signature below says it already). Only the last 1-3 lines go, and only when, with
    case, punctuation and spacing ignored, they are the signature's first lines; anything else stays."""
    sig = _norm(sig_text)
    lines = (body or "").rstrip().split("\n")
    filled = [i for i, x in enumerate(lines) if x.strip()]
    if not sig or is_html(body) or len(filled) < 2:
        return body
    sig_lines = [_norm(x) for x in sig_text.splitlines() if _norm(x)]
    heads = {" ".join(sig_lines[:j]) for j in range(1, len(sig_lines) + 1)}
    for k in (3, 2, 1):
        if k >= len(filled):
            continue
        tail = _norm(" ".join(lines[i] for i in filled[-k:]))
        if tail and tail in heads:
            return "\n".join(lines[: filled[-k]]).rstrip()
    return body


async def compose_payload(ctx: ToolCtx, args: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge tool arguments into a compose payload (see core/compose.py)."""
    from mailr.core.drafts import reply_payload

    mode = args.get("mode") or ("reply" if args.get("reply_to_message_id") else "new")
    if base is None:
        if args.get("reply_to_message_id"):
            if mode not in ("reply", "reply_all", "forward"):
                raise ToolError("mode must be reply, reply_all, or forward when replying")
            try:
                base = await reply_payload(ctx.db, ctx.p, int(args["reply_to_message_id"]), mode)
            except HTTPException as e:
                raise ToolError(str(e.detail)) from None
        else:
            base = {"to": [], "cc": [], "bcc": [], "subject": "", "html": "", "attachments": []}
    payload = dict(base)
    for key in ("to", "cc", "bcc"):
        if key in args and args[key] is not None:
            from mailr.agent.people_tools import expand

            payload[key] = _addrs((await expand(ctx, args[key]))[0])
    if args.get("subject") is not None:
        payload["subject"] = str(args["subject"])[:500]
    if args.get("identity_id"):
        payload["identity_id"] = int(args["identity_id"])
    if args.get("body") is not None:
        ident = await sending_identity(ctx.db, ctx.p.user_id, payload.get("identity_id"))
        sig = ident.signature_html if ident else ""
        # The signature is added below, so a closing that repeats it would sign the mail twice.
        text = strip_repeated_signoff(str(args["body"]), signature_text(sig)) if sig else str(args["body"])
        new = agent_html(text) + _signature(sig)
        quote = base.get("_quote")
        if quote is None:
            prior = base.get("html") or ""
            # Keep quoted history from the prefill (reply/forward) below the new text.
            idx = prior.find('<div class="mailr_quote">')
            fwd = prior.find('<br><div class="mailr_forward">')
            old = prior.find("---------- Forwarded message")  # a prefill made before the class
            cut = idx if idx >= 0 else fwd if fwd >= 0 else (prior.rfind("<br><div>", 0, old) if old >= 0 else -1)
            quote = prior[cut:] if cut >= 0 else ""
        payload["html"] = new + ("<br>" + quote if quote else "")
        payload.pop("text", None)
    payload.pop("_quote", None)
    return payload


DRAFT_RECIPIENTS = "hard rule: an agent changes who a saved draft goes to"
DRAFT_NEW_PEOPLE = ("hard rule: an agent that read email (or an API token) writes a draft to people outside "
                    "the conversation")


def _rcpts(pl: dict[str, Any], keys: tuple[str, ...] = ("to", "cc", "bcc")) -> dict[str, list[str]]:
    return {k: sorted({str(a.get("email") or "").strip().lower() for a in pl.get(k) or [] if a.get("email")})
            for k in keys}


async def _stays_local(ctx: ToolCtx, payload: dict[str, Any], draft_id: int | None) -> bool:
    """The draft is saved as local-only mail (core/drafts.py): it is written as a local-only account,
    or answers or forwards local-only mail, or is such a draft already."""
    from mailr.core.visibility import touches_local_only

    ident = await sending_identity(ctx.db, ctx.p.user_id, payload.get("identity_id"))
    if await identity_local_only(ctx.db, ident):
        return True
    ids = [int(x) for x in (payload.get("in_reply_to_message_id"), payload.get("forward_of_message_id"), draft_id)
           if x]
    return bool(ids and await touches_local_only(ctx.db, ctx.p.user_id, message_ids=ids))


async def _draft_gate(ctx: ToolCtx, payload: dict[str, Any], *, draft_id: int | None = None,
                      before: dict[str, Any] | None = None, conversation_id: int | None = None,
                      base_version: int | None = None, known: set[str] | None = None) -> ToolResult | None:
    """The permission check of an agent's draft write (`mail.draft`).
    The user's allow / ask / deny rules for "Write drafts" apply. On top of them:
    - a change to a saved draft's To, Cc or Bcc always asks (an open compose window gets the same
      change as a proposal to accept); a deny rule still wins;
    - an agent that has read email in this turn, or an API token, asks before it writes a draft
      that goes to anyone outside the conversation it replies to (and `known`, the addresses the
      server chose itself, such as a meeting's guests), or has a Bcc (unless a rule allows);
    - in a chat that has read local-only content, a draft that would not be local-only itself (its
      account, or the message it answers or forwards) always asks (agentwrite.local_copy_reason).
    Approval saves exactly this payload (core/approval_executors.py)."""
    from mailr.agent.agentwrite import local_copy_reason, taint_reason
    from mailr.models import Conversation

    force = ask = None
    if local_copy_reason(ctx) and not await _stays_local(ctx, payload, draft_id):
        force = local_copy_reason(ctx)
    outsiders: list[str] = []
    now = _rcpts(payload)
    if before is not None and _rcpts(before) != now:
        force = force or DRAFT_RECIPIENTS
    elif taint_reason(ctx):
        people: set[str] = set(known or ())
        if conversation_id:
            c = await ctx.db.get(Conversation, int(conversation_id))
            if c is not None and c.user_id == ctx.p.user_id:
                people = {str(x.get("email") or "").lower() for x in c.participants or []}
        everyone = set(now["to"]) | set(now["cc"]) | set(now["bcc"])
        if now["bcc"] or (everyone - people):
            ask = DRAFT_NEW_PEOPLE
            outsiders = sorted(everyone - people)
    from mailr.core.compose import plain_text

    subj = payload.get("subject") or "(no subject)"
    preview = {"title": f"{'Change' if draft_id else 'Save'} the draft “{subj[:80]}”", "kind": "draft",
               "email": {"from": "", "to": payload.get("to") or [], "cc": payload.get("cc") or [],
                         "bcc": payload.get("bcc") or [], "subject": subj, "text": plain_text(payload)[:60_000],
                         "attachments": [a.get("filename") for a in payload.get("attachments") or []]},
               "lines": [f"To: {', '.join(now['to']) or '—'}"] + ([f"Cc: {', '.join(now['cc'])}"] if now["cc"] else [])
               + ([f"Bcc: {', '.join(now['bcc'])}"] if now["bcc"] else []) + [f"Subject: {subj}"]}
    if ask:
        # The card's reason names them (agent/present._reason): "Marco Silva was not in that conversation."
        names = {str(a.get("email") or "").lower(): a.get("name") for k in ("to", "cc", "bcc") for a in payload.get(k) or []}
        preview["outsiders"] = [names.get(x) or x for x in outsiders]
        by = ctx.actor.name
        if ctx.actor.kind == "assistant":
            from mailr.knowledge import profile as kprofile
            from mailr.models import AssistantProfile

            by = kprofile.assistant_name(await ctx.db.get(AssistantProfile, ctx.p.user_id))
        preview["by_name"] = by
    if before is not None and force:
        preview["before"] = "; ".join(f"{k.capitalize()}: {', '.join(v) or '—'}" for k, v in _rcpts(before).items())
        preview["lines"].insert(0, f"Recipients now: {preview['before']}")
    from mailr.core import live

    return await gate(ctx, "mail.draft", target=subj[:200],
                      payload={"op": "save", "draft_id": draft_id, "payload": payload, "base_version": base_version,
                               "by": live.who(ctx.p, assistant=ctx.assistant)},
                      preview=preview, ask_by_default=ask, force_ask=force)


# ACT draft.save @assistant, ACT mail.reply @assistant (reply_to_message_id)
@tool("create_draft",
      "Create a draft email (saved in Drafts; nothing is sent). For a reply give reply_to_message_id and "
      "mode (reply, reply_all, forward): recipients, subject, and quoted history are filled in, and body "
      "is the new text. For a new message give to, subject, body.",
      _obj({**_PAYLOAD_PROPS, "reply_to_message_id": {"type": "integer"},
            "mode": {"type": "string", "enum": ["new", "reply", "reply_all", "forward"]}}),
      scope="draft")
async def create_draft(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.api.schemas import message_out
    from mailr.core.drafts import save_draft

    payload = await compose_payload(ctx, args)
    if not payload.get("to") and (args.get("mode") or "new") in ("new", "forward"):
        raise ToolError("a new message needs at least one recipient in 'to'")
    conv_id = None
    if args.get("reply_to_message_id"):
        from mailr.models import Message as M

        parent = await ctx.db.get(M, int(args["reply_to_message_id"]))
        conv_id = parent.conversation_id if parent is not None and parent.user_id == ctx.p.user_id else None
    pending = await _draft_gate(ctx, payload, conversation_id=conv_id)
    if pending is not None:
        pending.data["note"] = ("Saving this draft waits for the user's approval (it goes to people outside the "
                                "conversation, has a Bcc, copies local-only content, or a rule asks). Do not retry it; tell the user.")
        return pending
    from mailr.core import live

    try:
        m = await save_draft(ctx.db, ctx.p, payload, None, by=live.who(ctx.p, assistant=ctx.assistant))
    except HTTPException as e:
        raise ToolError(str(e.detail)) from None
    await _audit(ctx, "agent.create_draft", str(m.id), {"to": payload.get("to"), "subject": payload.get("subject")})
    await ctx.db.commit()
    r = live.ref("draft", m.id, m.live_version)
    data = {"draft_id": m.id, "version": m.live_version, "conversation_id": m.conversation_id,
            "to": [a["email"] for a in payload.get("to", [])],
            "cc": [a["email"] for a in payload.get("cc", [])], "subject": payload.get("subject"),
            "ref": r}
    return ToolResult(data, message_ids=[m.id], conversation_ids=[m.conversation_id] if m.conversation_id else [],
                      draft={"draft_id": m.id, "payload": payload, "message": message_out(m), "ref": r},
                      refs=[{**r, "title": payload.get("subject") or ""}])


_EDITS = {"type": "array", "description": "small changes: exact text to find in the body and its replacement",
          "items": {"type": "object", "additionalProperties": False, "required": ["find", "replace"],
                    "properties": {"find": {"type": "string"}, "replace": {"type": "string"}}}}


def _split_new_text(prior_html: str) -> tuple[str, str, str]:
    """(new text html, signature html, quote html) of a stored draft body."""
    idx = prior_html.find('<div class="mailr_quote">')
    main, quote = (prior_html[:idx], prior_html[idx:]) if idx >= 0 else (prior_html, "")
    sidx = main.find('<div class="mailr_signature">')
    if sidx >= 0:
        # The signature block begins with a spacer div that compose_payload adds before it.
        head, sig = main[:sidx], main[sidx:]
        if head.endswith("<div><br></div>"):
            head, sig = head[: -len("<div><br></div>")], "<div><br></div>" + sig
        return head, sig, quote
    return main, "", quote


async def _load_draft(ctx: ToolCtx, did: int) -> Message:
    """A saved draft the caller may see (tokens: connector limits and local-only apply)."""
    from mailr.core.drafts import load_draft

    try:
        return await load_draft(ctx.db, ctx.p, did)
    except HTTPException:
        raise ToolError("draft not found") from None


async def _saved_drafts(ctx: ToolCtx, limit: int = 15) -> list[Message]:
    from mailr.core.visibility import visible_message_filter

    return list((await ctx.db.execute(select(Message).where(
        Message.user_id == ctx.p.user_id, Message.draft.is_(True), Message.deleted.is_(False),
        visible_message_filter(ctx.p)).order_by(Message.updated_at.desc()).limit(limit))).scalars())


@tool("list_drafts",
      "The user's drafts: compose windows open on screen right now (with their unsaved text; `focused` is the one "
      "they are working in) and saved drafts in Drafts.", _obj({}))
async def list_drafts(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    wins = (ctx.ui or {}).get("compose") or []
    open_ids = {w.get("draft_id") for w in wins if w.get("draft_id")}
    saved = [m for m in await _saved_drafts(ctx) if m.id not in open_ids]
    return ToolResult({
        "open": [{"window": w["id"], "draft_id": w.get("draft_id"), "focused": w["focused"], "mode": w["mode"],
                  "to": w["to"], "cc": w["cc"], "subject": w["subject"], "body": w["body"][:600]} for w in wins],
        "saved": [{"draft_id": m.id, "conversation_id": m.conversation_id, "to": [a["email"] for a in m.to or []],
                   "subject": m.subject, "snippet": m.snippet[:200],
                   "updated": m.updated_at.isoformat() if m.updated_at else None} for m in saved],
    }, message_ids=[m.id for m in saved])


@tool("get_draft",
      "Read one draft in full: an open compose window (`window`, its current unsaved text) or a saved draft "
      "(`draft_id`). Without arguments: the focused window. `version` is the version you read: pass it to "
      "update_draft as base_version.",
      _obj({"window": {"type": "string"}, "draft_id": {"type": "integer"}}))
async def get_draft(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.agent import screen
    from mailr.core.drafts import draft_payload, live_version

    did = _int(args, "draft_id", required=False)
    w = screen.find_window(ctx.ui or {}, window=args.get("window"), draft_id=did) if (args.get("window") or did) \
        else screen.focused(ctx.ui or {})
    if w is not None:
        return ToolResult({"open": True, "window": w["id"], "draft_id": w.get("draft_id"), "mode": w["mode"],
                           "version": w.get("base_version"), "unsaved_changes": bool(w.get("dirty")),
                           "to": w["to"], "cc": w["cc"], "bcc": w["bcc"], "subject": w["subject"], "body": w["body"],
                           "note": "This is the text in the open compose window, including unsaved changes. "
                                   "Quoted history and the signature are not shown and are kept."})
    if args.get("window"):
        raise ToolError(f"no open compose window {args['window']!r}; call list_drafts")
    if did is None:
        did = _working_draft(ctx)
    if did is None:
        raise ToolError("no compose window is open; give a draft_id (list_drafts)")
    m = await _load_draft(ctx, did)
    version = await live_version(ctx.db, ctx.p, m)  # first: it may read the draft again
    pl = await draft_payload(ctx.db, ctx.p, m)
    new_html, _sig, quote = _split_new_text(pl.get("html") or "")
    return ToolResult({"open": False, "draft_id": m.id, "version": version, "conversation_id": m.conversation_id,
                       "to": [a["email"] for a in pl.get("to") or []], "cc": [a["email"] for a in pl.get("cc") or []],
                       "subject": pl.get("subject"), "body": screen.html_to_text(new_html) if new_html else
                       (pl.get("text") or ""), "has_quoted_history": bool(quote)},
                      message_ids=[m.id], conversation_ids=[m.conversation_id] if m.conversation_id else [])


def _working_draft(ctx: ToolCtx) -> int | None:
    """The draft this chat made or changed, when it is the only one (the chat's working set)."""
    from mailr.agent import working_set

    items = [i for i in working_set.items(ctx.chat_context) if i["kind"] == "draft"]
    return int(items[-1]["id"]) if len(items) == 1 else None


def _working_version(ctx: ToolCtx, kind: str, item: Any) -> int | None:
    from mailr.agent import working_set

    return working_set.version_of(ctx.chat_context, kind, item)


def _edit_window_locally(ctx: ToolCtx, w: dict[str, Any], args: dict[str, Any], edits: list) -> ToolResult:
    """A compose window that was never saved has no draft on the server yet: the change goes to
    the window itself (a `draft_edit` event), which applies it with Undo. The new text also comes
    as HTML (body_html, each edit's replace_html), its Markdown made into formatting."""
    from mailr.agent import screen
    from mailr.core import mdmail

    changes: dict[str, Any] = {}
    body = w["body"]
    if edits:
        try:
            body, _n = screen.apply_edits(body, edits)
        except screen.EditError as e:
            raise ToolError(str(e)) from None
        changes["edits"] = [{"find": str(e.get("find")), "replace": str(e.get("replace") or ""),
                             "replace_html": mdmail.fragment_html(str(e.get("replace") or ""))} for e in edits]
        changes["body_text"] = body
        changes["body_html"] = agent_html(body)
    elif args.get("body") is not None:
        body = str(args["body"])
        changes["body_text"] = body
        changes["body_html"] = agent_html(body)
    # Recipients of an open window are only ever proposed: the form shows them highlighted
    # with Accept / Reject and applies nothing until the user accepts (a changed Bcc is
    # easy to miss and sends mail somewhere else).
    proposed = {k: _addrs(args[k]) for k in ("to", "cc", "bcc") if args.get(k) is not None}
    if proposed:
        changes["proposed_recipients"] = proposed
    if args.get("subject") is not None:
        changes["subject"] = str(args["subject"])[:500]
    if not changes:
        raise ToolError("nothing to change: give edits, body, subject or recipients")
    # Later calls in this turn see the edited text (not the proposed recipients).
    w["body"] = body
    if "subject" in changes:
        w["subject"] = changes["subject"]
    status = "applied to the open compose window; the user can undo it"
    if proposed:
        status = ("recipient changes are shown to the user to accept or reject in the compose window; nothing "
                  "changes until they accept" + ("; the other changes were applied (with undo)"
                                                if set(changes) - {"proposed_recipients"} else ""))
    return ToolResult({"status": status, "window": w["id"], "draft_id": w.get("draft_id"), "body": body[:1500]},
                      events=[{"type": "draft_edit", "window": w["id"], "draft_id": w.get("draft_id"),
                               "changes": changes}])


# Revising a draft in place (ACT draft.save above covers the assistant surface).
@tool("update_draft",
      "Change a draft in place. Revising (\"change it\", \"make it shorter\", \"fix that\") always means this: never "
      "create a new draft for a revision. Give the draft's `draft_id` (an open compose window shows one; `window` "
      "only for a window that is not saved yet) and `base_version`, the version you read (get_draft, the screen or "
      "your working set): anything the user saved since then is merged in, and if you both changed the same "
      "paragraph nothing changes and you are told so. For small changes pass `edits` (exact find/replace pairs, "
      "e.g. [Your name] -> the user's name) and keep the rest; pass `body` only to replace the whole text. Quoted "
      "history and the signature are kept. The open window shows the change at once, with Undo. Recipient "
      "changes to an open window are shown to the user to accept.",
      _obj({**_PAYLOAD_PROPS, "draft_id": {"type": "integer"}, "window": {"type": "string"}, "edits": _EDITS,
            "base_version": {"type": "integer", "description": "the version of the draft you read"}}),
      scope="draft")
async def update_draft(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.agent import screen
    from mailr.api.schemas import message_out
    from mailr.core import live
    from mailr.core.drafts import draft_payload, live_version, save_draft

    did = _int(args, "draft_id", required=False)
    edits = args.get("edits") or []
    if not isinstance(edits, list):
        raise ToolError("'edits' must be a list of {find, replace}")
    if args.get("body") is not None and edits:
        raise ToolError("give either body (whole text) or edits (small changes), not both")
    ui = ctx.ui or {}
    if args.get("window") or did:
        w = screen.find_window(ui, window=args.get("window"), draft_id=did)
    else:
        w = screen.focused(ui)
        if w is None:
            wins = ui.get("compose") or []
            if len(wins) > 1:
                raise ToolError("more than one draft is open and none is focused ("
                                + "; ".join(f"window {x['id']}: {x['subject'] or '(no subject)'!r}" for x in wins)
                                + "). Ask the user which one, then pass its draft_id.")
            did = _working_draft(ctx)
    if args.get("window") and w is None:
        raise ToolError(f"no open compose window {args['window']!r}; call list_drafts")
    if w is not None and not w.get("draft_id"):
        res = _edit_window_locally(ctx, w, args, edits)
        await _audit(ctx, "agent.update_open_draft", w["id"], {"window": w["id"], "fields": sorted(
            res.events[0]["changes"])})
        return res
    if w is not None:
        did = int(w["draft_id"])
    if did is None:
        raise ToolError("no compose window is open; give a draft_id (list_drafts)")
    m = await _load_draft(ctx, did)
    stored_version = await live_version(ctx.db, ctx.p, m)
    base = await draft_payload(ctx.db, ctx.p, m)
    prior = base.get("html") or ""
    new_html, sig, quote = _split_new_text(prior)
    base = {**base, "_quote": quote}
    a = dict(args)
    events: list[dict[str, Any]] = []
    proposed: dict[str, Any] = {}
    if w is not None:
        # Recipients of an open window are only ever proposed: the form shows them highlighted with
        # Accept / Reject and applies nothing until the user accepts.
        proposed = {k: _addrs(a.pop(k)) for k in ("to", "cc", "bcc") if a.get(k) is not None}
        if proposed:
            events.append({"type": "draft_edit", "window": w["id"], "draft_id": did,
                           "changes": {"proposed_recipients": proposed}})
    edited: str | None = None
    if edits:
        edited = screen.apply_edits_html(new_html, edits)
        if edited is not None:
            a.pop("body", None)
        else:
            # Not verbatim in the saved text: as plain text, in what the user sees (an open window's
            # text includes what they typed and have not saved yet).
            source = w["body"] if w is not None else screen.html_to_text(new_html)
            try:
                text, _n = screen.apply_edits(source, edits)
            except screen.EditError as e:
                raise ToolError(str(e)) from None
            a["body"] = text
    # Small edits are made to the text as saved now; a whole new text was written from the version
    # the agent read (the user's later changes are merged in).
    if edited is not None:
        base_version = stored_version
    else:
        base_version = _int(args, "base_version", required=False) or (w or {}).get("base_version") \
            or _working_version(ctx, "draft", did) or stored_version
    content_change = edits or a.get("body") is not None or a.get("subject") is not None \
        or any(a.get(k) is not None for k in ("to", "cc", "bcc", "identity_id"))
    if not content_change:
        if proposed:
            await _audit(ctx, "agent.update_open_draft", w["id"], {"window": w["id"], "draft_id": did,
                                                                    "fields": ["proposed_recipients"]})
            return ToolResult({"status": "recipient changes are shown to the user to accept or reject in the compose "
                                         "window; nothing changes until they accept", "draft_id": did,
                               "version": stored_version}, events=events)
        raise ToolError("nothing to change: give edits, body, subject or recipients")
    payload = await compose_payload(ctx, a, base=base)
    if a.get("body") is None:
        payload["html"] = (edited + sig + ("<br>" + quote if quote else "")) if edited is not None else prior
    pending = await _draft_gate(ctx, payload, draft_id=did, before=base, conversation_id=m.conversation_id,
                                base_version=base_version)
    if pending is not None:
        pending.data["note"] = ("The change to this saved draft waits for the user's approval (its recipients "
                                "change, it copies local-only content, or a rule asks). Nothing changed yet. Do not retry it; tell the user.")
        pending.message_ids = [did]
        pending.events = events
        return pending
    try:
        m = await save_draft(ctx.db, ctx.p, payload, did, base_version=base_version,
                             by=live.who(ctx.p, assistant=ctx.assistant))
    except live.LiveConflict as e:
        # Refused before anything was written; the session stays usable (no rollback).
        raise ToolError(e.body()["detail"] + " Nothing changed. Read the draft again with get_draft and make the "
                        "change on its current text, or ask the user.") from None
    except HTTPException as e:
        raise ToolError(str(e.detail)) from None
    await _audit(ctx, "agent.update_draft", str(did), {"merged": True} if m.live_merged else None)
    await ctx.db.commit()
    r = live.ref("draft", m.id, m.live_version)
    status = "saved in place" + ("; the open compose window shows it and the user can undo it" if w is not None else "")
    if m.live_merged:
        status += "; the user's own changes made meanwhile were kept"
    out = m.live_payload or payload
    return ToolResult({"draft_id": m.id, "version": m.live_version, "status": status, "subject": out.get("subject"),
                       "to": [x["email"] for x in out.get("to", [])], "ref": r},
                      message_ids=[m.id], events=events,
                      draft={"draft_id": m.id, "payload": out, "message": message_out(m), "ref": r},
                      refs=[{**r, "title": out.get("subject") or ""}])


# ACT draft.send @assistant
@tool("send_draft",
      "Send a draft. Sending goes through mailr's send gate and permission rules: by default the user "
      "approves every agent send (they see the message and press Allow), sends wait a short hold window, "
      "and the first message to a new address always needs approval. Only call this when the user "
      "explicitly asked to send. Returns the outbox status, or a pending approval.",
      _obj({"draft_id": {"type": "integer"},
            "send_at": {"type": "string", "description": "optional ISO time for scheduled send"}}, ["draft_id"]),
      scope="draft")
async def send_draft(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.core.drafts import draft_payload
    from mailr.core.sendgate import SendGate

    did = _int(args, "draft_id")
    m = await _load_draft(ctx, did)
    payload = await draft_payload(ctx.db, ctx.p, m)
    if ctx.p.is_token and not ctx.p.has("send"):
        raise ToolError("this token cannot send; the draft is saved for the user to send")
    send_at = None
    if args.get("send_at"):
        try:
            # Without an offset: the user's zone (SendGate.submit), as the approval card shows it.
            send_at = datetime.fromisoformat(str(args["send_at"]).replace("Z", "+00:00"))
        except ValueError:
            raise ToolError("send_at must be an ISO datetime") from None
    gate_ = SendGate(ctx.db, ctx.p, actor=ctx.actor)
    try:
        ob = await gate_.submit(payload, draft_id=did, send_at=send_at)
    except HTTPException as e:
        raise ToolError(str(e.detail)) from None
    await _audit(ctx, "agent.send_draft", str(did), {"send_at": args.get("send_at")},
                 {"outbox_id": ob.id, "status": ob.status, "reason": ob.approval_reason})
    await ctx.db.commit()
    if gate_.pending is not None:
        res = pending_result(gate_.pending, outbox_id=ob.id)
        res.message_ids = [did]
        return res
    return ToolResult({"outbox_id": ob.id, "status": ob.status, "send_at": ob.send_at.isoformat(),
                       "reason": ob.approval_reason or None}, message_ids=[did])


@tool("get_outbox_status", "Status of a message submitted with send_draft (queued, awaiting_approval, sent, "
      "cancelled, rejected, failed, or uncertain: the connection dropped after the message was handed over, so it "
      "may have gone out; never resend it, the user decides).", _obj({"outbox_id": {"type": "integer"}}, ["outbox_id"]))
async def get_outbox_status(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    oid = _int(args, "outbox_id")
    ob = await ctx.db.get(Outbox, oid)
    if not ob or ob.user_id != ctx.p.user_id or (ctx.p.is_token and ob.token_id != ctx.p.token.id):
        raise ToolError("outbox entry not found")
    return ToolResult({"outbox_id": ob.id, "status": ob.status, "send_at": ob.send_at.isoformat(),
                       "sent_at": ob.sent_at.isoformat() if ob.sent_at else None,
                       "reason": ob.approval_reason or None, "error": ob.error or None})


@tool("get_approval_status",
      "Status of an action that is waiting for the user's approval (pending, approved, executing, executed, "
      "denied, expired, failed), with its result once executed.",
      _obj({"approval_id": {"type": "integer"}}, ["approval_id"]))
async def get_approval_status(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.core.permissions import pending_out
    from mailr.models import PendingAction

    pid = _int(args, "approval_id")
    pa = await ctx.db.get(PendingAction, pid)
    if pa is None or pa.user_id != ctx.p.user_id or (ctx.p.is_token and pa.token_id != ctx.p.token.id):
        raise ToolError("approval not found")
    out = pending_out(pa)
    return ToolResult({"approval_id": pa.id, "status": pa.status, "action": pa.action,
                       "title": (pa.preview or {}).get("title"), "result": out.get("result"),
                       "approval_url": approval_url(pa.id)})
