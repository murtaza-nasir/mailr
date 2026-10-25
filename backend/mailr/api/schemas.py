"""Serialisers shared by routers (plain functions, so workers and MCP reuse them)."""

from __future__ import annotations

from typing import Any

from mailr.core.publicid import ConvId, MsgId
from mailr.models import Conversation, Identity, Label, Message, Outbox, User


def iso(d) -> str | None:
    return d.isoformat() if d else None


def user_out(u: User) -> dict[str, Any]:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role,
            "created_at": iso(u.created_at), "last_login_at": iso(u.last_login_at),
            "disabled": u.disabled, "oidc": bool(u.oidc_sub), "has_password": bool(u.password_hash),
            "email_verified": bool(getattr(u, "email_verified", True))}


def label_out(lb: Label, unread: int = 0, total: int = 0) -> dict[str, Any]:
    return {"id": lb.id, "name": lb.name, "role": lb.role, "color": lb.color,
            "show_in_list": lb.show_in_list, "show_in_message_list": lb.show_in_message_list,
            "sort": lb.sort, "home_account_id": lb.home_account_id, "unread": unread, "total": total}


def conversation_out(c: Conversation, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    d = {
        "id": ConvId(c.id), "subject": c.subject, "snippet": c.snippet, "last_date": iso(c.last_date),
        "message_count": c.message_count, "unread_count": c.unread_count,
        "draft_count": c.draft_count, "has_attachments": c.has_attachments,
        "starred": c.starred, "important": c.important, "importance_score": c.importance_score,
        "category": c.category, "label_ids": c.label_ids or [], "participants": c.participants or [],
        # A note to self, and whom a conversation only the user wrote in went to (core/threading.py).
        "to_self": bool(c.to_self), "recipients": c.recipients or [],
        "snoozed_until": iso(c.snoozed_until), "muted": c.muted,
        "follow_up_at": iso(c.follow_up_at), "nudged_at": iso(c.nudged_at),
        "primary_action": c.primary_action,  # mail.primary_action of the latest message
    }
    if extra:
        d.update(extra)
    return d


def message_out(m: Message, untrusted: bool = False, own_langs: frozenset[str] | None = None) -> dict[str, Any]:
    """own_langs: the reader's own languages (langid.own_languages), for `offer_translate`;
    English when not given."""
    from mailr.core.langid import offer_translate

    d = {
        "id": MsgId(m.id), "conversation_id": m.conversation_id,
        "from": {"email": m.from_email, "name": m.from_name},
        "to": m.to or [], "cc": m.cc or [], "bcc": m.bcc or [], "reply_to": m.reply_to or [],
        "subject": m.subject, "date": iso(m.date or m.received_at), "snippet": m.snippet,
        "seen": m.seen, "flagged": m.flagged, "important": m.important, "draft": m.draft,
        "answered": m.answered, "label_ids": m.label_ids or [], "has_attachments": m.has_attachments,
        "attachments": [
            {k: a.get(k) for k in ("part", "filename", "type", "size", "inline", "cid", "email") if k != "email" or a.get(k)}
            for a in (m.attachments or [])
        ],
        "list_unsubscribe": m.list_unsubscribe, "list_id": m.list_id,
        "auth_results": m.auth_results or {}, "sent_by_me": m.sent_by_me,
        # "android-share": shared in by another app ("Send to self"); not the user's own words.
        "origin": m.origin,
        "category": m.category, "delivered_to": m.delivered_to or [],
        "message_id": m.message_id_hdr, "connector_id": m.connector_id, "primary_action": m.primary_action,
        # A move of one of the user's events this email announces in words (intel/event_changes.py).
        **({"event_change": ec} if (ec := _event_change(m)) else {}),
        # The body's language (ISO 639-1, core/langid.py) and whether the reader offers
        # "Translate": the model is sure of it and it is not one of the user's languages.
        "lang": m.lang,
        "offer_translate": not m.sent_by_me and not m.draft and offer_translate(m.lang, m.lang_confidence, own_langs),
    }
    if untrusted:
        d["untrusted"] = True
    return d


def _event_change(m: Any) -> dict[str, Any] | None:
    s = (getattr(m, "upstream", None) or {}).get("event_change")
    return {k: s.get(k) for k in ("uid", "summary", "old_start", "old_end", "new_start", "new_end", "sentence")} \
        if isinstance(s, dict) and s.get("status") == "open" else None


def identity_out(i: Identity) -> dict[str, Any]:
    return {"id": i.id, "email": i.email, "name": i.name, "reply_to": i.reply_to,
            "signature_html": i.signature_html, "is_default": i.is_default,
            "connector_id": i.connector_id, "receives": i.receives or [], "has_avatar": _has_avatar(i)}


def _has_avatar(i: Identity) -> bool:
    """Whether a picture was uploaded, so clients do not ask for one that does not exist."""
    from mailr.api.avatar_routes import _user_file

    return _user_file(i.user_id, "identities", str(i.id)).exists()


def outbox_out(o: Outbox, token_name: str | None = None) -> dict[str, Any]:
    p = o.payload or {}
    return {"id": o.id, "status": o.status, "send_at": iso(o.send_at), "recipients": o.recipients,
            "subject": p.get("subject", ""), "identity_id": o.identity_id,
            "token_name": token_name, "approval_reason": o.approval_reason, "error": o.error,
            "conversation_id": o.conversation_id, "created_at": iso(o.created_at),
            "sent_at": iso(o.sent_at), "payload": p,
            # The saved draft this send came from (the thread shows Sending / Not sent on it), and
            # the Message-ID it left with (the thread marks that message Sent for a moment).
            "draft_id": int(o.draft_jmap_id) if (o.draft_jmap_id or "").isdigit() else None,
            "sent_message_id": o.sent_message_id, "attempts": o.attempts}
