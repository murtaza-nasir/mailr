"""Which messages a caller may see.

A session user sees all of their own mail. A token sees only messages from connectors it
is allowed (token.connector_ids, empty = all), and never a `local_only` message unless the
token itself is local_only. The data class lives on the message (`Message.local_only`,
stamped at ingest), so it still applies after a connector is deleted and to mail that has
no connector (e.g. a reply a phone saved to Sent).
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from sqlalchemy import and_, exists, select, true

from mailr.models import Connector, Message

# The caller of this request or tool call (core/auth.optional_principal, agent/tools.run). Code that
# renders stored text about other objects (note and table link titles) reads it, so a restricted
# token never sees the subject of mail it may not read.
viewer_var: ContextVar[Any] = ContextVar("mailr_viewer", default=None)


def restricted_viewer(user_id: int) -> Any:
    """The current caller when it is a token of `user_id` (whose limits apply), else None."""
    p = viewer_var.get()
    if p is None or getattr(p, "token", None) is None or getattr(p, "user_id", None) != user_id:
        return None
    return p


async def visible_conversations(db, user_id: int, ids: list[int]) -> set[int]:
    """Of these conversation ids: the ones of `user_id` that the current caller may see (a
    restricted token: those holding a message it may read)."""
    from mailr.models import Conversation

    ids = [int(i) for i in ids if i]
    if not ids:
        return set()
    mine = set((await db.execute(select(Conversation.id).where(Conversation.id.in_(ids),
                                                                Conversation.user_id == user_id))).scalars())
    p = restricted_viewer(user_id)
    if p is None or not mine:
        return mine
    return set((await db.execute(select(Message.conversation_id).where(
        Message.conversation_id.in_(mine), Message.user_id == user_id, Message.deleted.is_(False),
        visible_message_filter(p)).distinct())).scalars())


async def visible_connector_set(db, user_id: int, ids: set[int]) -> set[int]:
    """Of these connector ids: the ones the current caller may see (all of the user's for a
    session or the user's own assistant)."""
    ids = {int(i) for i in ids if i}
    if not ids:
        return set()
    rows = (await db.execute(select(Connector).where(Connector.id.in_(ids), Connector.user_id == user_id))).scalars()
    p = restricted_viewer(user_id)
    return {c.id for c in rows if p is None or p.can_see_connector(c)}


def visible_message_filter(p):
    if p.token is None:
        return true()
    t = p.token
    conds = [Message.user_id == p.user_id]
    if not t.local_only:
        conds.append(Message.local_only.is_(False))
    if t.connector_ids:
        conds.append(Message.connector_id.in_(list(t.connector_ids)))
    return and_(*conds)


def visible_conversation_ids(p):
    """Subquery of conversation ids with at least one message visible to p."""
    return select(Message.conversation_id).where(
        Message.user_id == p.user_id, Message.deleted.is_(False), visible_message_filter(p))


def is_restricted(p) -> bool:
    """True when p cannot see all of the user's mail (so aggregates must be recomputed)."""
    return p.token is not None and (not p.token.local_only or bool(p.token.connector_ids))


# ---- one rule for every assistant route and tool
#
# Mail is filtered per message (visible_message_filter). What the assistant *wrote* about mail (a
# brief, an errand's result, a routine's digest, a smart-label reason, a run's condition) can draw
# on any message of the user, from any account, so it is shown only to callers that see all of the
# user's mail: the signed-in user, the user's own assistant, and a token that is local_only with no
# connector limit. A restricted token never gets that text.


def token_query_sensitive(p) -> bool:
    """Is a search or recall query from this caller treated as local-only content? A local-only
    token reads local-only mail, so what it types may quote that mail: its query is embedded only
    by a local endpoint. The person's "Local models only for me" and the household rule apply on
    top in llm.resolve_embed (the callers pass user_id)."""
    tok = getattr(p, "token", None)
    return bool(tok is not None and getattr(tok, "local_only", False))


def sees_all_mail(p) -> bool:
    return not is_restricted(p)


async def conversation_visible(db, p, conversation_id: int | None) -> bool:
    """True when the conversation is the caller's and (for a token) holds a message it may see."""
    if not conversation_id:
        return False
    from mailr.models import Conversation

    c = await db.get(Conversation, int(conversation_id))
    if c is None or c.user_id != p.user_id:
        return False
    if p.token is None:
        return True
    return bool((await db.execute(select(exists().where(
        Message.conversation_id == c.id, Message.user_id == p.user_id, Message.deleted.is_(False),
        visible_message_filter(p))))).scalar())


def redact_run(p, out: dict) -> dict:
    """An assistant run as a restricted token may see it: status and ids, none of the model's text."""
    if sees_all_mail(p):
        return out
    return {**out, "output": None, "citations": [], "condition": {}, "actions": [], "error": None,
            "session_id": None, "redacted": True}


async def touches_local_only(db, user_id: int, message_ids: list[int] | None = None,
                             conversation_ids: list[int] | None = None) -> bool:
    """True if any of these messages is local_only. Model calls that include their
    content must pass sensitive=True to the router."""
    conds = [Message.user_id == user_id, Message.local_only.is_(True)]
    if message_ids is not None:
        conds.append(Message.id.in_(message_ids))
    if conversation_ids is not None:
        conds.append(Message.conversation_id.in_(conversation_ids))
    return bool((await db.execute(select(exists().where(*conds)))).scalar())


async def user_has_local_only(db, user_id: int) -> bool:
    has_conn = (await db.execute(select(exists().where(
        Connector.user_id == user_id, Connector.data_class == "local_only")))).scalar()
    if has_conn:
        return True
    return bool((await db.execute(select(exists().where(
        Message.user_id == user_id, Message.local_only.is_(True))))).scalar())



def _ints(values) -> list[int]:
    out = []
    for v in values:
        try:
            if v is not None and str(v).strip().isdigit():
                out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


# Where an approval names the mail it shows: these keys, at any depth of its payload or preview. A
# card whose text comes from mail but whose payload names no mail (a filter's match check, a
# brief) declares it in its preview under MAIL_REFS, for example {"mail_refs": {"conversation_ids":
# [..]}}. tests/test_agent_ask_approval.py lists every place that creates an approval.
MAIL_REF_KEYS = frozenset({"message_id", "message_ids", "draft_id", "in_reply_to_message_id", "conversation_id",
                           "conversation_ids"})
MAIL_REFS = "mail_refs"


def mail_refs(*trees: Any, limit: int | None = 2000) -> tuple[list[int], list[int]]:
    """(message ids, conversation ids) named anywhere in these JSON trees under MAIL_REF_KEYS, up to
    `limit` values (None: all of them) and 8 levels deep."""
    mids: list[Any] = []
    cids: list[Any] = []

    def walk(node: Any, depth: int) -> None:
        if depth > 8 or (limit is not None and len(mids) + len(cids) >= limit):
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if k in MAIL_REF_KEYS:
                    vals = v if isinstance(v, (list, tuple)) else [v]
                    (cids if k.startswith("conversation") else mids).extend(vals[:limit])
                elif isinstance(v, (dict, list)):
                    walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node[:limit]:
                walk(v, depth + 1)

    for t in trees:
        walk(t, 0)
    return _ints(mids[:limit]), _ints(cids[:limit])


REF_CHUNK = 2000


async def any_local_only(db, user_id: int, message_ids: list[int] | None = None,
                         conversation_ids: list[int] | None = None) -> bool:
    """touches_local_only over any number of ids, a chunk at a time (a cleanup plan may name 10,000)."""
    for key, ids in (("message_ids", list(dict.fromkeys(message_ids or []))),
                     ("conversation_ids", list(dict.fromkeys(conversation_ids or [])))):
        for i in range(0, len(ids), REF_CHUNK):
            if await touches_local_only(db, user_id, **{key: ids[i:i + REF_CHUNK]}):
                return True
    return False


async def approval_touches_local_only(db, user_id: int, pa) -> bool:
    """True when an approval (PendingAction) may carry local-only content: the messages, drafts or
    conversations it names anywhere in its payload or preview (MAIL_REF_KEYS) hold local-only mail
    (Message.local_only), its send (outbox) replies in such a conversation, saves such a draft or goes
    out through a local-only account, it names a local-only connector, note or table, or its payload
    says it is sensitive. Push notifications and the assistant's "ask about this approval" use it."""
    from mailr.models import Identity, Outbox
    from mailr.notes.models import Note
    from mailr.tables.models import DataTable

    pl = pa.payload or {}
    if pl.get("sensitive") is True:
        return True
    # The card's own rows first (what it shows), then every id the payload names.
    pmids, pcids = mail_refs(getattr(pa, "preview", None) or {}, limit=None)
    if await any_local_only(db, user_id, message_ids=pmids, conversation_ids=pcids):
        return True
    mids, cids = mail_refs(pl, limit=None)
    conns = set(_ints([pl.get("connector_id"), *((pa.ctx or {}).get("connectors") or [])]))
    if pl.get("outbox_id"):
        ob = await db.get(Outbox, int(pl["outbox_id"]))
        if ob is not None and ob.user_id == user_id:
            cids += _ints([ob.conversation_id])
            mids += _ints([ob.draft_jmap_id, (ob.payload or {}).get("in_reply_to_message_id")])
            ident = await db.get(Identity, ob.identity_id) if ob.identity_id else None
            if ident is not None and ident.connector_id:
                conns.add(ident.connector_id)
    if await any_local_only(db, user_id, message_ids=mids, conversation_ids=cids):
        return True
    pids = [str(x) for x in ((getattr(pa, "ctx", None) or {}).get("notes") or [])[:50]]
    for model in (Note, DataTable) if pids else ():
        if (await db.execute(select(exists().where(model.pid.in_(pids), model.local_only.is_(True))))).scalar():
            return True
    if conns:
        return bool((await db.execute(select(exists().where(
            Connector.id.in_(list(conns)), Connector.user_id == user_id,
            Connector.data_class == "local_only")))).scalar())
    return False
