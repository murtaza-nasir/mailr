"""Groups of mail: the subscriptions manager and the bulk cleanup assistant.

Both pages group the user's messages and act on the conversations of one group with the
ordinary actions (core/actions.py, the bulk job of core/tasks.py), so every change is audited
and has the Undo the action system gives it.

Subscriptions. One row per list or bulk sender: mail with a List-Id is grouped by the list
(`list:<id>`), other mail with a List-Unsubscribe header by the sender's address
(`from:<address>`). Each row counts the mail of the last 30 and 90 days and in total, how much of
it was read ("opened"), the date of the newest read message, whether the user ever replied in one
of its conversations, and how the newest message's List-Unsubscribe can be used
(Message.unsub_method). The default order is the "you never read this" score: the mail of the
last 90 days weighted by the share left unread.

Cleanup. Mail grouped by sender, by the sender's domain, by list or by category, within a scope:
where the mail is (archived, Inbox, everywhere but Trash and Spam), how old it is, uncategorised
mail only, bulk mail only. Some conversations are always left alone unless the user includes
them explicitly:
  - starred conversations;
  - important conversations;
  - conversations with the user's own messages (a reply, or a message the user started).
The group list counts messages by their own labels and dates (one index-only aggregate over
ix_msg_groups). The preview of an action resolves the conversations exactly (each
conversation's labels and last date) and that list is what the action changes.

Performance. Both pages read the mail tally (core/tally.py, "from the mail tally" below): a few
thousand rows per user, whatever the size of the mailbox. Until a user's tally is ready, for a
token limited to some accounts, and for an age cutoff other than tally.BANDS, they group the
messages as before: one aggregate over ix_msg_groups (a covering index of the grouping columns)
and the two small partial indexes ix_msg_sent_conv and ix_conv_protected, which at 300k messages
reads about half the index. tests/test_scale_budgets.py measures them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    String,
    and_,
    any_,
    bindparam,
    case,
    cast,
    false,
    func,
    literal,
    not_,
    null,
    or_,
    select,
    true,
    union_all,
)
from sqlalchemy.dialects.postgresql import ARRAY, distinct_on
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from mailr.core import tally
from mailr.core.visibility import visible_message_filter
from mailr.models import Conversation, Label, MailGroupState, MailTally, MailTallyUnit, Message

KINDS = ("sender", "domain", "list", "category")
SUB = "sub"
SAMPLES = 20
# Above this many conversations an action runs as a background job (api/mail_write.BULK_INLINE_MAX)
# and above BULK_UNDO_MAX (core/tasks.py) it has no Undo; the preview says both.
WHERES = ("archived", "inbox", "all")
CATEGORIES = ("primary", "social", "promotions", "updates", "forums", "purchases")
# Second-level labels under which registrations happen (as core/unsubscribe._SLD).
_SLD = ("co", "com", "net", "org", "ac", "edu", "gov", "or", "ne", "go", "gob", "nic", "ltd", "plc", "sch", "mil")
# Senders that only send automated mail, for "bulk mail only" (as intel/features.py "noreply").
_AUTOMATED_RE = r"^(no-?reply|do-?not-?reply|notifications?|notify|alerts?|mailer-daemon|bounces?|news(letter)?|updates?)[^@]*@"


def _domain(from_email=None):
    """The registrable domain of the sender, as core/unsubscribe.registrable_domain approximates
    it: the last two labels of the host, or three under a two-letter country code with a common
    second level (shop.co.uk). String functions, not a regular expression: this runs once per
    message (or tally row) of the group list."""
    host = func.split_part(Message.from_email if from_email is None else from_email, "@", 2)
    rev = func.reverse(host)
    tld = func.reverse(func.split_part(rev, ".", 1))
    sld = func.reverse(func.split_part(rev, ".", 2))
    third = func.reverse(func.split_part(rev, ".", 3))
    two = sld + literal(".", String) + tld
    return case((sld == "", host),
                (and_(func.length(tld) == 2, sld.in_(_SLD), third != ""), third + literal(".", String) + two),
                else_=two)


def key_expr(kind: str, cols=None):
    """The group key of a message (or, with `cols`, of a mail_tally row), in SQL."""
    c = Message if cols is None else cols
    if kind == "sender":
        return c.from_email
    if kind == "domain":
        return _domain(c.from_email)
    if kind == "list":
        return c.list_id
    if kind == "category":
        return func.coalesce(c.category, "")
    if kind == SUB:
        return case((c.list_id.isnot(None), literal("list:", String) + c.list_id),
                    else_=literal("from:", String) + c.from_email)
    raise HTTPException(400, f"unknown grouping {kind!r}")


def key_match(kind: str, key: str):
    """The messages of one group."""
    if kind == "sender":
        return Message.from_email == key.lower()
    if kind == "domain":
        return _domain() == key.lower()
    if kind == "list":
        return Message.list_id == key.lower()
    if kind == "category":
        return Message.category.is_(None) if key in ("", "none") else Message.category == key
    if kind == SUB:
        what, _, val = key.partition(":")
        if what == "list" and val:
            return Message.list_id == val
        if what == "from" and val:
            return and_(Message.list_id.is_(None), Message.from_email == val)
    raise HTTPException(400, "unknown group")


def rule_query(kind: str, key: str) -> str | None:
    """The search (and filter) that matches a group's future mail, or None (uncategorised)."""
    def q(v: str) -> str:
        return f'"{v}"' if any(c in v for c in ' ()"') else v

    if kind == "sender":
        return f"from:{q(key)}"
    if kind == "domain":
        return f"from:{q('@' + key)}"
    if kind == "list":
        return f"list:{q(key)}"
    if kind == "category":
        return f"category:{key}" if key in CATEGORIES else None
    if kind == SUB:
        what, _, val = key.partition(":")
        return f"list:{q(val)}" if what == "list" else f"from:{q(val)}"
    return None


@dataclass
class Scope:
    """Which mail a cleanup group covers, and which protected conversations it includes."""
    where: str = "archived"
    older_than_days: int = 0
    uncategorised: bool = False
    bulk_only: bool = False
    include_starred: bool = False
    include_important: bool = False
    include_replied: bool = False

    def __post_init__(self) -> None:
        if self.where not in WHERES:
            raise HTTPException(400, f"where must be one of {', '.join(WHERES)}")
        self.older_than_days = max(0, min(int(self.older_than_days or 0), 36500))

    @property
    def cutoff(self) -> datetime | None:
        return datetime.now(UTC) - timedelta(days=self.older_than_days) if self.older_than_days else None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def roles(db: AsyncSession, uid: int) -> dict[str, int]:
    return {r: i for i, r in (await db.execute(select(Label.id, Label.role).where(
        Label.user_id == uid, Label.role.isnot(None)))).all()}


def _ids(*xs: int | None) -> list[int]:
    return [int(x) for x in xs if x]


def _location(col, where: str, r: dict[str, int]):
    """Where mail is, on a label_ids column (a message's or a conversation's)."""
    inbox, trash, junk = r.get("inbox"), r.get("trash"), r.get("junk")
    if where == "inbox":
        return col.overlap(_ids(inbox)) if inbox else false()
    away = _ids(trash, junk) if where == "all" else _ids(inbox, trash, junk)
    return not_(col.overlap(away)) if away else true()


def _sent_conversations(uid: int):
    """The conversations with the user's own messages (ix_msg_sent_conv)."""
    s = aliased(Message)
    return select(s.conversation_id).where(s.user_id == uid, s.sent_by_me.is_(True), s.deleted.is_(False),
                                           s.conversation_id.isnot(None))


def _flagged_conversations(uid: int, starred: bool, important: bool):
    """Starred and/or important conversations (ix_conv_protected)."""
    flags = ([Conversation.starred.is_(True)] if starred else []) + \
            ([Conversation.important.is_(True)] if important else [])
    return select(Conversation.id).where(Conversation.user_id == uid, or_(*flags))


def protected(uid: int, scope: Scope):
    """True for a message whose conversation the scope leaves alone; None when it includes all."""
    parts = []
    if not (scope.include_starred and scope.include_important):
        parts.append(Message.conversation_id.in_(_flagged_conversations(
            uid, not scope.include_starred, not scope.include_important)))
    if not scope.include_replied:
        parts.append(Message.conversation_id.in_(_sent_conversations(uid)))
    return or_(*parts) if parts else None


def protected_ids(uid: int, scope: Scope):
    """The conversations the scope leaves alone, as one subquery (cid), for a join; None when it
    includes all. The group list joins it once instead of testing each aggregate against it."""
    from sqlalchemy import union

    parts = []
    if not (scope.include_starred and scope.include_important):
        parts.append(_flagged_conversations(uid, not scope.include_starred, not scope.include_important))
    if not scope.include_replied:
        parts.append(_sent_conversations(uid))
    if not parts:
        return None
    return (union(*parts) if len(parts) > 1 else parts[0]).subquery("held")


def _bulk_mail():
    return or_(Message.list_id.isnot(None), Message.unsub_method.isnot(None),
               Message.from_email.op("~")(_AUTOMATED_RE))


def base_conditions(p, r: dict[str, int]) -> list:
    """Every grouped message: the caller's, not deleted, not their own, not in Spam."""
    conds = [Message.user_id == p.user_id, Message.deleted.is_(False), Message.sent_by_me.is_(False),
             visible_message_filter(p)]
    if r.get("junk"):
        conds.append(not_(Message.label_ids.overlap([r["junk"]])))
    return conds


def scope_conditions(kind: str, scope: Scope, r: dict[str, int]) -> list:
    conds = [_location(Message.label_ids, scope.where, r)]
    if scope.cutoff is not None:
        conds.append(Message.date < scope.cutoff)
    if scope.uncategorised:
        conds.append(Message.category.is_(None))
    if scope.bulk_only:
        conds.append(_bulk_mail())
    if kind == "list":
        conds.append(Message.list_id.isnot(None))
    return conds


# ------------------------------------------------------------------ cleanup groups

SORTS = {"count": "conversations DESC", "size": "size DESC", "oldest": "oldest ASC", "newest": "newest DESC",
         "name": "key ASC"}


async def groups(db: AsyncSession, p, kind: str, scope: Scope, *, q: str = "", sort: str = "count",
                 limit: int = 50, offset: int = 0, live: bool = False) -> dict[str, Any]:
    """The cleanup groups of one kind, largest first, with what each would change. From the mail
    tally when it can answer (`_tally_anchor`), else (or with `live`) from the messages."""
    if kind not in KINDS:
        raise HTTPException(400, f"group by one of {', '.join(KINDS)}")
    if sort not in SORTS:
        raise HTTPException(400, f"sort by one of {', '.join(SORTS)}")
    uid = p.user_id
    r = await roles(db, uid)
    anchor = None if live else await _tally_anchor(db, p, scope.older_than_days)
    if anchor is not None:
        inner = _groups_tally(p, kind, scope, r, anchor, q.strip().lower())
    else:
        inner = _groups_live(p, kind, scope, r, q)
    stmt = select(inner, func.count().over().label("n")).where(inner.c.conversations > 0)
    order = {"count": [inner.c.conversations.desc(), inner.c.key],
             "size": [inner.c.size.desc(), inner.c.key],
             "oldest": [inner.c.oldest.asc(), inner.c.key],
             "newest": [inner.c.newest.desc(), inner.c.key],
             "name": [inner.c.key]}[sort]
    limit = max(1, min(int(limit), 200))
    rows = (await db.execute(stmt.order_by(*order).limit(limit).offset(max(0, int(offset))))).mappings().all()
    return await _group_items(db, uid, kind, scope, rows, offset)


def _groups_live(p, kind: str, scope: Scope, r: dict[str, int], q: str):
    """The group list over the messages (one aggregate over ix_msg_groups)."""
    uid = p.user_id
    key = key_expr(kind)
    conds = base_conditions(p, r) + scope_conditions(kind, scope, r)
    if q.strip():
        conds.append(key.ilike(f"%{q.strip().lower()}%"))
    held_ids = protected_ids(uid, scope)
    held = held_ids.c[0].isnot(None) if held_ids is not None else false()
    free = held_ids.c[0].is_(None) if held_ids is not None else true()
    inbox = r.get("inbox")
    cid = Message.conversation_id
    src = Message.__table__.outerjoin(held_ids, held_ids.c[0] == cid) if held_ids is not None else Message.__table__
    return select(
        key.label("key"),
        func.count(cid.distinct()).filter(free).label("conversations"),
        func.count().filter(free).label("messages"),
        func.coalesce(func.sum(Message.size).filter(free), 0).label("size"),
        func.count().filter(and_(free, Message.seen.is_(False))).label("unread"),
        func.count(cid.distinct()).filter(held).label("protected"),
        func.count(cid.distinct()).filter(and_(free, Message.label_ids.overlap(_ids(inbox)))).label("in_inbox"),
        func.count().filter(and_(free, Message.category.is_(None))).label("uncategorised"),
        func.min(Message.date).filter(free).label("oldest"),
        func.max(Message.date).filter(free).label("newest"),
        func.bool_or(Message.local_only).label("local_only"),
    ).select_from(src).where(*conds).group_by(key).subquery()


async def _group_items(db: AsyncSession, uid: int, kind: str, scope: Scope, rows, offset: int) -> dict[str, Any]:
    states = await _states(db, uid, kind, [x["key"] for x in rows])
    items = []
    for x in rows:
        st = states.get(x["key"])
        items.append({
            "key": x["key"], "label": _label(kind, x["key"]), "conversations": x["conversations"],
            "messages": x["messages"], "size": int(x["size"] or 0), "unread": x["unread"],
            "protected": x["protected"], "in_inbox": x["in_inbox"], "uncategorised": x["uncategorised"],
            "oldest": _iso(x["oldest"]), "newest": _iso(x["newest"]), "local_only": bool(x["local_only"]),
            "rule_query": rule_query(kind, x["key"]),
            "reviewed": bool(st and st.reviewed_at),
            "suggestion": _suggestion(st),
        })
    total = rows[0]["n"] if rows else 0
    return {"by": kind, "scope": scope.as_dict(), "items": items, "total": total,
            "next_offset": offset + len(items) if offset + len(items) < total else None}


def _label(kind: str, key: str) -> str:
    if kind == "category":
        return key.capitalize() if key else "Uncategorised"
    return key


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _suggestion(st: MailGroupState | None) -> dict[str, Any] | None:
    if st is None or not st.suggested_category:
        return None
    return {"category": st.suggested_category, "confidence": st.suggestion_confidence,
            "model": st.suggestion_model, "samples": st.suggestion_samples, "at": _iso(st.suggested_at)}


async def _states(db: AsyncSession, uid: int, kind: str, keys: list[str]) -> dict[str, MailGroupState]:
    if not keys:
        return {}
    return {s.key: s for s in (await db.execute(select(MailGroupState).where(
        MailGroupState.user_id == uid, MailGroupState.kind == kind, MailGroupState.key.in_(keys)))).scalars()}


async def state(db: AsyncSession, uid: int, kind: str, key: str) -> MailGroupState:
    """The group's state row, created when missing (in the caller's transaction)."""
    from sqlalchemy.dialects.postgresql import insert

    await db.execute(insert(MailGroupState).values(user_id=uid, kind=kind, key=key[:400])
                     .on_conflict_do_nothing(constraint="uq_mail_group_states"))
    return (await db.execute(select(MailGroupState).where(
        MailGroupState.user_id == uid, MailGroupState.kind == kind, MailGroupState.key == key[:400]))).scalar_one()


# ------------------------------------------------------------------ one group, exactly

async def resolve(db: AsyncSession, p, kind: str, key: str, scope: Scope, *, limit: int = 200_000) -> list[int]:
    """The conversations an action on the group changes, newest first. Exact: a conversation is
    in scope by its own labels and last date (not only its messages'), and the protected ones
    are left out unless the scope includes them."""
    uid = p.user_id
    r = await roles(db, uid)
    conds = base_conditions(p, r) + scope_conditions(kind if kind != SUB else "sender", scope, r) + \
        [key_match(kind, key), Message.conversation_id.isnot(None)]
    prot = protected(uid, scope)
    if prot is not None:
        conds.append(not_(prot))
    msgs = select(Message.conversation_id).where(*conds)
    cconds = [Conversation.user_id == uid, Conversation.id.in_(msgs),
              _location(Conversation.label_ids, scope.where, r)]
    if scope.cutoff is not None:
        cconds.append(func.coalesce(Conversation.last_date, datetime.min.replace(tzinfo=UTC)) < scope.cutoff)
    if not scope.include_starred:
        cconds.append(Conversation.starred.is_(False))
    if not scope.include_important:
        cconds.append(Conversation.important.is_(False))
    stmt = select(Conversation.id).where(*cconds).order_by(
        func.coalesce(Conversation.last_date, datetime.min.replace(tzinfo=UTC)).desc(), Conversation.id.desc())
    return list((await db.execute(stmt.limit(limit))).scalars())


async def preview(db: AsyncSession, p, kind: str, key: str, scope: Scope) -> dict[str, Any]:
    """Exactly what an action on the group changes: the conversations and their messages, 20 of
    them as samples, and the protected conversations it leaves alone, by reason."""
    from mailr.api.mail_write import BULK_INLINE_MAX
    from mailr.core.tasks import BULK_UNDO_MAX

    uid = p.user_id
    r = await roles(db, uid)
    ids = await resolve(db, p, kind, key, scope)
    n_convs = len(ids)
    n_msgs = 0
    if ids:
        # The ids go out as one array parameter: an IN list binds one parameter per id, and a
        # group of more than 32,767 conversations (asyncpg's limit) failed.
        n_msgs = (await db.execute(select(func.count()).select_from(Message).where(
            Message.user_id == uid, Message.deleted.is_(False),
            Message.conversation_id == any_(bindparam("group_ids", ids, type_=ARRAY(BigInteger))),
            visible_message_filter(p)))).scalar_one()
    samples = []
    if ids:
        rows = (await db.execute(select(Conversation).where(Conversation.id.in_(ids[:SAMPLES])))).scalars().all()
        by_id = {c.id: c for c in rows}
        for cid in ids[:SAMPLES]:
            c = by_id.get(cid)
            if c is None:
                continue
            who = (c.participants or [{}])[0] if c.participants else {}
            samples.append({"conversation_id": cid, "subject": c.subject or "", "from_name": who.get("name") or "",
                            "from_email": who.get("email") or "", "date": _iso(c.last_date),
                            "unread": c.unread_count > 0, "messages": c.message_count})
    # The conversations the scope leaves alone, by reason (the group's mail without the
    # protection, against each protected set).
    conds = base_conditions(p, r) + scope_conditions(kind if kind != SUB else "sender", scope, r) + \
        [key_match(kind, key)]
    cid = Message.conversation_id
    left = (await db.execute(select(
        func.count(cid.distinct()).filter(cid.in_(_flagged_conversations(uid, True, False))),
        func.count(cid.distinct()).filter(cid.in_(_flagged_conversations(uid, False, True))),
        func.count(cid.distinct()).filter(cid.in_(_sent_conversations(uid))),
    ).where(*conds))).one()
    protected_out = {"starred": 0 if scope.include_starred else left[0],
                     "important": 0 if scope.include_important else left[1],
                     "replied": 0 if scope.include_replied else left[2]}
    return {"by": kind, "key": key, "label": _label(kind, key) if kind != SUB else key.partition(":")[2],
            "scope": scope.as_dict(), "conversations": n_convs, "messages": n_msgs, "samples": samples,
            "protected": protected_out, "included": {"starred": scope.include_starred,
                                                     "important": scope.include_important,
                                                     "replied": scope.include_replied},
            "background": n_convs > BULK_INLINE_MAX, "undo": n_convs <= BULK_UNDO_MAX,
            "rule_query": rule_query(kind, key)}


# ------------------------------------------------------------------ subscriptions

SUB_SORTS = ("score", "volume", "total", "last", "open_rate", "name")
SUB_FILTERS = ("all", "review", "kept", "unsubscribed", "auto_archived", "never_read")


def _sub_conditions(p, r: dict[str, int]) -> list:
    return base_conditions(p, r) + [or_(Message.list_id.isnot(None), Message.unsub_method.isnot(None))]


async def subscriptions(db: AsyncSession, p, *, q: str = "", sort: str = "score", filter: str = "all",
                        limit: int = 50, offset: int = 0, live: bool = False) -> dict[str, Any]:
    """One row per list or bulk sender (see the module docstring). From the mail tally when it can
    answer (`_tally_anchor`), else (or with `live`) from the messages."""
    if sort not in SUB_SORTS:
        raise HTTPException(400, f"sort by one of {', '.join(SUB_SORTS)}")
    if filter not in SUB_FILTERS:
        raise HTTPException(400, f"filter by one of {', '.join(SUB_FILTERS)}")
    uid = p.user_id
    r = await roles(db, uid)
    now = datetime.now(UTC)
    anchor = None if live else await _tally_anchor(db, p, 0, now)
    if anchor is not None:
        inner = _subs_tally(p, r, anchor, now, q.strip().lower())
    else:
        inner = _subs_live(p, r, now, q)
    st = aliased(MailGroupState)
    unread_share = 1.0 - (inner.c.read * 1.0) / func.greatest(inner.c.total, 1)
    score = (inner.c.last_90 + inner.c.total / 12.0) * unread_share
    open_rate = (inner.c.read * 1.0) / func.greatest(inner.c.total, 1)
    stmt = select(inner, st.reviewed_at, st.unsubscribed_at, st.unsubscribe_method, st.rule_id,
                  score.label("score"), open_rate.label("open_rate"), func.count().over().label("n")) \
        .select_from(inner.outerjoin(st, and_(st.user_id == uid, st.kind == SUB, st.key == inner.c.key)))
    if filter == "review":
        stmt = stmt.where(st.reviewed_at.is_(None), st.unsubscribed_at.is_(None), st.rule_id.is_(None))
    elif filter == "kept":
        stmt = stmt.where(st.reviewed_at.isnot(None))
    elif filter == "unsubscribed":
        stmt = stmt.where(st.unsubscribed_at.isnot(None))
    elif filter == "auto_archived":
        stmt = stmt.where(st.rule_id.isnot(None))
    elif filter == "never_read":
        stmt = stmt.where(inner.c.read == 0)
    order = {"score": [score.desc(), inner.c.last_received.desc()],
             "volume": [inner.c.last_90.desc(), inner.c.total.desc()],
             "total": [inner.c.total.desc()],
             "last": [inner.c.last_received.desc().nulls_last()],
             "open_rate": [open_rate.asc(), inner.c.total.desc()],
             "name": [inner.c.key.asc()]}[sort]
    limit = max(1, min(int(limit), 200))
    rows = (await db.execute(stmt.order_by(*order, inner.c.key).limit(limit)
                             .offset(max(0, int(offset))))).mappings().all()
    latest = await (_latest_tally(db, p, [x["key"] for x in rows]) if anchor is not None
                    else _latest(db, p, r, [x["key"] for x in rows]))
    items = []
    for x in rows:
        lm = latest.get(x["key"]) or {}
        what, _, val = x["key"].partition(":")
        items.append({
            "key": x["key"], "kind": "list" if what == "list" else "sender",
            "list_id": val if what == "list" else None,
            "name": lm.get("from_name") or "", "email": lm.get("from_email") or (val if what == "from" else ""),
            "subject": lm.get("subject") or "", "message_id": lm.get("id"),
            "unsubscribe": lm.get("unsub_method"),
            "total": x["total"], "last_30": x["last_30"], "last_90": x["last_90"], "read": x["read"],
            "open_rate": round(float(x["open_rate"] or 0), 3), "score": round(float(x["score"] or 0), 2),
            "last_received": _iso(x["last_received"]), "last_read": _iso(x["last_read"]),
            "conversations": x["conversations"], "in_inbox": x["in_inbox"], "replied": bool(x["replied"]),
            "local_only": bool(x["local_only"]), "size": int(x["size"] or 0),
            "kept": x["reviewed_at"] is not None, "unsubscribed_at": _iso(x["unsubscribed_at"]),
            "unsubscribed_by": x["unsubscribe_method"], "auto_archive_rule_id": x["rule_id"],
            "rule_query": rule_query(SUB, x["key"]),
        })
    total = rows[0]["n"] if rows else 0
    return {"items": items, "total": total,
            "next_offset": offset + len(items) if offset + len(items) < total else None,
            "unknown": sum(1 for i in items if i["unsubscribe"] == "unknown")}


def _subs_live(p, r: dict[str, int], now: datetime, q: str):
    """The subscriptions over the messages (one aggregate over ix_msg_groups)."""
    uid = p.user_id
    key = key_expr(SUB)
    conds = _sub_conditions(p, r)
    if q.strip():
        conds.append(key.ilike(f"%{q.strip().lower()}%"))
    inbox = r.get("inbox")
    replied = Message.conversation_id.in_(_sent_conversations(uid))
    return select(
        key.label("key"),
        func.count().label("total"),
        func.count().filter(Message.date >= now - timedelta(days=30)).label("last_30"),
        func.count().filter(Message.date >= now - timedelta(days=90)).label("last_90"),
        func.count().filter(Message.seen.is_(True)).label("read"),
        func.max(Message.date).label("last_received"),
        func.max(Message.date).filter(Message.seen.is_(True)).label("last_read"),
        func.count(Message.conversation_id.distinct()).label("conversations"),
        func.count(Message.conversation_id.distinct()).filter(
            Message.label_ids.overlap(_ids(inbox))).label("in_inbox"),
        func.bool_or(replied).label("replied"),
        func.bool_or(Message.local_only).label("local_only"),
        func.coalesce(func.sum(Message.size), 0).label("size"),
    ).where(*conds).group_by(key).subquery()


async def _latest(db: AsyncSession, p, r: dict[str, int], keys: list[str]) -> dict[str, dict[str, Any]]:
    """The newest message of each subscription, preferring one with a List-Unsubscribe header."""
    if not keys:
        return {}
    lists = [k[5:] for k in keys if k.startswith("list:")]
    senders = [k[5:] for k in keys if k.startswith("from:")]
    which = []
    if lists:
        which.append(Message.list_id.in_(lists))
    if senders:
        which.append(and_(Message.list_id.is_(None), Message.from_email.in_(senders)))
    key = key_expr(SUB)
    stmt = select(key.label("key"), Message.id, Message.from_name, Message.from_email, Message.subject,
                  Message.unsub_method).where(*_sub_conditions(p, r), or_(*which)) \
        .ext(distinct_on(key)).order_by(key, Message.unsub_method.is_(None), Message.date.desc().nulls_last())
    return {x["key"]: dict(x) for x in (await db.execute(stmt)).mappings()}


async def latest_with_unsubscribe(db: AsyncSession, p, key: str) -> Message | None:
    """The newest message of a subscription that carries a List-Unsubscribe header."""
    from mailr.models import light_message

    r = await roles(db, p.user_id)
    return (await db.execute(select(Message).options(*light_message()).where(
        *_sub_conditions(p, r), key_match(SUB, key), Message.list_unsubscribe.isnot(None))
        .order_by(Message.date.desc().nulls_last(), Message.id.desc()).limit(1))).scalar()


async def unknown_methods(db: AsyncSession, p, limit: int = 200) -> list[int]:
    """Newest messages of subscriptions whose one-click support is not known yet ("unknown": mail
    stored before List-Unsubscribe-Post was indexed): the probe job reads their headers."""
    r = await roles(db, p.user_id)
    key = key_expr(SUB)
    stmt = select(Message.id).where(*_sub_conditions(p, r), Message.list_unsubscribe.isnot(None)) \
        .ext(distinct_on(key)).order_by(key, Message.date.desc().nulls_last())
    sub = stmt.subquery()
    ids = list((await db.execute(select(sub.c.id))).scalars())
    if not ids:
        return []
    return list((await db.execute(select(Message.id).where(Message.id.in_(ids), Message.unsub_method == "unknown")
                                  .limit(limit))).scalars())


# ------------------------------------------------------------------ from the mail tally
#
# core/tally.py keeps the grouped mail counted per sender, list, category, place, age band,
# List-Unsubscribe, data class and protection. A page sums the tally rows its scope selects; the
# conversations with mail in several rows (`spread`) are counted distinct from their units, and the
# mail between an age band's edge and the page's cutoff (since the anchor moved last) is read from
# the messages. The results are the same as the live aggregates above.


async def _tally_anchor(db: AsyncSession, p, older_than_days: int, now: datetime | None = None) -> datetime | None:
    """The anchor of the caller's tally when it can answer: it is ready, the caller sees all of the
    user's accounts, and the age cutoff is one of tally.BANDS."""
    if p.token is not None and p.token.connector_ids:
        return None
    if older_than_days and older_than_days not in tally.BANDS:
        return None
    st = await tally.state(db, p.user_id)
    if st is None or st[0] != "ready" or st[1] > (now or datetime.now(UTC)):
        return None
    return st[1]


def _cell_conds(c, p) -> list:
    conds = [c.user_id == p.user_id]
    if p.token is not None and not p.token.local_only:
        conds.append(c.local_only.is_(False))  # as visible_message_filter
    return conds


def _cell_scope(c, kind: str, scope: Scope) -> list:
    """scope_conditions on mail_tally rows."""
    conds = [{"inbox": c.loc.op("&")(tally.INBOX) != 0, "archived": c.loc == 0,
              "all": c.loc.op("&")(tally.TRASH) == 0}[scope.where]]
    if scope.older_than_days:
        conds.append(c.band.between(tally.BANDS.index(scope.older_than_days) + 1, len(tally.BANDS)))
    if scope.uncategorised:
        conds.append(c.category.is_(None))
    if scope.bulk_only:
        conds.append(or_(c.list_id.isnot(None), c.unsub.is_(True), c.from_email.op("~")(_AUTOMATED_RE)))
    if kind == "list":
        conds.append(c.list_id.isnot(None))
    return conds


def _held_mask(scope: Scope) -> int:
    return (0 if scope.include_starred else tally.STARRED) | (0 if scope.include_important else tally.IMPORTANT) \
        | (0 if scope.include_replied else tally.REPLIED)


def _sum(col):
    """A count summed over the parts: 0 for none, as count() is."""
    return func.coalesce(cast(func.sum(col), BigInteger), 0)


def _groups_tally(p, kind: str, scope: Scope, r: dict[str, int], anchor: datetime, q: str):
    """The group list from the tally, with the columns of _groups_live."""
    uid = p.user_id
    c, u = aliased(MailTally), aliased(MailTallyUnit)
    key = key_expr(kind, c)
    conds = _cell_conds(c, p) + _cell_scope(c, kind, scope)
    if q:
        conds.append(key.ilike(f"%{q}%"))
    hm = _held_mask(scope)
    held = c.prot.op("&")(hm) != 0 if hm else false()
    free = not_(held)
    inbox = c.loc.op("&")(tally.INBOX) != 0
    zero, no_date, no_bool = literal(0, BigInteger), null().cast(DateTime(timezone=True)), null().cast(Boolean)
    parts: list[Any] = [select(
        key.label("key"), func.sum(c.solo).filter(free), func.sum(c.messages).filter(free),
        func.sum(c.size).filter(free), func.sum(c.unread).filter(free), func.sum(c.solo).filter(held),
        func.sum(c.solo).filter(and_(free, inbox)), func.sum(c.messages).filter(and_(free, c.category.is_(None))),
        func.min(c.oldest).filter(free), func.max(c.newest).filter(free), func.bool_or(c.local_only),
    ).where(*conds).group_by(key)]
    pairs: list[Any] = [select(key.label("key"), u.conv.label("cid"), held.label("held"), inbox.label("inbox"))
             .select_from(u).join(c, c.id == u.cell_id).where(u.user_id == uid, u.spread, *conds)]
    if scope.older_than_days:
        # The mail between the band's edge and the cutoff, from the messages (ix_msg_user_date).
        days = timedelta(days=scope.older_than_days)
        lo, hi = anchor - days, scope.cutoff
        mkey = key_expr(kind)
        wconds = base_conditions(p, r) + scope_conditions(kind, replace(scope, older_than_days=0), r) + \
            [Message.date >= lo, Message.date < hi]
        if q:
            wconds.append(mkey.ilike(f"%{q}%"))
        held_ids = protected_ids(uid, scope)
        mheld = held_ids.c[0].isnot(None) if held_ids is not None else false()
        mfree = held_ids.c[0].is_(None) if held_ids is not None else true()
        cid = Message.conversation_id
        src = Message.__table__.outerjoin(held_ids, held_ids.c[0] == cid) if held_ids is not None \
            else Message.__table__
        pairs.append(select(mkey, cid, mheld, Message.label_ids.overlap(_ids(r.get("inbox"))))
                     .select_from(src).where(*wconds))
        parts.append(select(
            mkey, zero, func.count().filter(mfree), func.sum(Message.size).filter(mfree),
            func.count().filter(and_(mfree, Message.seen.is_(False))), zero, zero,
            func.count().filter(and_(mfree, Message.category.is_(None))),
            func.min(Message.date).filter(mfree), func.max(Message.date).filter(mfree),
            func.bool_or(Message.local_only),
        ).select_from(src).where(*wconds).group_by(mkey))
    pu = union_all(*pairs).subquery()
    parts.append(select(
        pu.c.key, func.count(pu.c.cid.distinct()).filter(not_(pu.c.held)), zero, zero, zero,
        func.count(pu.c.cid.distinct()).filter(pu.c.held),
        func.count(pu.c.cid.distinct()).filter(and_(not_(pu.c.held), pu.c.inbox)), zero, no_date, no_date, no_bool,
    ).group_by(pu.c.key))
    a = union_all(*parts).subquery()
    k, conv, msgs, size, unread, prot, in_inbox, uncat, oldest, newest, local = a.c
    return select(
        k.label("key"), _sum(conv).label("conversations"), _sum(msgs).label("messages"),
        _sum(size).label("size"), _sum(unread).label("unread"), _sum(prot).label("protected"),
        _sum(in_inbox).label("in_inbox"), _sum(uncat).label("uncategorised"), func.min(oldest).label("oldest"),
        func.max(newest).label("newest"), func.bool_or(local).label("local_only"),
    ).group_by(k).subquery()


def _subs_tally(p, r: dict[str, int], anchor: datetime, now: datetime, q: str):
    """The subscriptions from the tally, with the columns of _subs_live."""
    uid = p.user_id
    c, u = aliased(MailTally), aliased(MailTallyUnit)
    key = key_expr(SUB, c)
    conds = _cell_conds(c, p) + [or_(c.list_id.isnot(None), c.unsub.is_(True))]
    if q:
        conds.append(key.ilike(f"%{q}%"))
    inbox = c.loc.op("&")(tally.INBOX) != 0
    zero, no_date, no_bool = literal(0, BigInteger), null().cast(DateTime(timezone=True)), null().cast(Boolean)
    parts: list[Any] = [select(
        key.label("key"), func.sum(c.messages), func.sum(c.messages).filter(c.band == 0),
        func.sum(c.messages).filter(c.band <= 1), func.sum(c.messages - c.unread), func.max(c.newest),
        func.max(c.newest_read), func.sum(c.solo), func.sum(c.solo).filter(inbox),
        func.bool_or(c.prot.op("&")(tally.REPLIED) != 0), func.bool_or(c.local_only), func.sum(c.size),
    ).where(*conds).group_by(key)]
    pu = select(key.label("key"), u.conv.label("cid"), inbox.label("inbox")).select_from(u) \
        .join(c, c.id == u.cell_id).where(u.user_id == uid, u.spread, *conds).subquery()
    parts.append(select(pu.c.key, zero, zero, zero, zero, no_date, no_date, func.count(pu.c.cid.distinct()),
                        func.count(pu.c.cid.distinct()).filter(pu.c.inbox), no_bool, no_bool, zero)
                 .group_by(pu.c.key))
    # Band 0 and bands 0-1 hold the mail since anchor - 30 and - 90 days; the mail from there to
    # now - 30 and - 90 days is older than the page's windows.
    w30 = and_(Message.date >= anchor - timedelta(days=30), Message.date < now - timedelta(days=30))
    w90 = and_(Message.date >= anchor - timedelta(days=90), Message.date < now - timedelta(days=90))
    if anchor < now:
        mkey = key_expr(SUB)
        wconds = _sub_conditions(p, r) + [or_(w30, w90)]
        if q:
            wconds.append(mkey.ilike(f"%{q}%"))
        parts.append(select(mkey, zero, -func.count().filter(w30), -func.count().filter(w90), zero, no_date,
                            no_date, zero, zero, no_bool, no_bool, zero).where(*wconds).group_by(mkey))
    a = union_all(*parts).subquery()
    k, total, l30, l90, read, last, last_read, conv, in_inbox, replied, local, size = a.c
    return select(
        k.label("key"), _sum(total).label("total"), _sum(l30).label("last_30"), _sum(l90).label("last_90"),
        _sum(read).label("read"), func.max(last).label("last_received"), func.max(last_read).label("last_read"),
        _sum(conv).label("conversations"), _sum(in_inbox).label("in_inbox"), func.bool_or(replied).label("replied"),
        func.bool_or(local).label("local_only"), _sum(size).label("size"),
    ).group_by(k).subquery()


async def _latest_tally(db: AsyncSession, p, keys: list[str]) -> dict[str, dict[str, Any]]:
    """_latest from the tally: each row keeps its newest message, so the newest of a subscription
    is one lookup per row of the page's subscriptions."""
    if not keys:
        return {}
    c = aliased(MailTally)
    lists = [k[5:] for k in keys if k.startswith("list:")]
    senders = [k[5:] for k in keys if k.startswith("from:")]
    which = []
    if lists:
        which.append(c.list_id.in_(lists))
    if senders:
        which.append(and_(c.list_id.is_(None), c.unsub.is_(True), c.from_email.in_(senders)))
    key = key_expr(SUB, c)
    stmt = select(key.label("key"), Message.id, Message.from_name, Message.from_email, Message.subject,
                  Message.unsub_method).select_from(c).join(Message, Message.id == c.newest_id) \
        .where(*_cell_conds(c, p), or_(*which)) \
        .ext(distinct_on(key)).order_by(key, c.unsub.desc(), c.newest.desc().nulls_last(), c.newest_id.desc())
    return {x["key"]: dict(x) for x in (await db.execute(stmt)).mappings()}
