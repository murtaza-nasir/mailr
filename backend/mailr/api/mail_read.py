"""Read side: labels, conversation lists, conversations, message bodies, attachments."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import (
    BigInteger,
    DateTime,
    and_,
    false,
    func,
    literal,
    literal_column,
    not_,
    or_,
    select,
    text,
    tuple_,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from mailr.api import contract as C
from mailr.api.schemas import conversation_out, label_out, message_out
from mailr.core import publicid
from mailr.core import search as searchmod
from mailr.core.audit import audit
from mailr.core.auth import Principal, scope
from mailr.core.langid import own_languages
from mailr.core.publicid import ConvId, MsgId
from mailr.core.visibility import is_restricted, visible_conversation_ids, visible_message_filter
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import Conversation, Label, Message, Outbox, light_message

router = APIRouter(tags=["mail"])


async def _labels(db: AsyncSession, uid: int) -> list[Label]:
    memo = db.info.get(_LABELS_MEMO)
    if memo is not None and uid in memo:
        return memo[uid]
    out = list((await db.execute(select(Label).where(Label.user_id == uid).order_by(Label.sort, Label.name))).scalars())
    if memo is not None:
        memo[uid] = out
    return out


_LABELS_MEMO = "mailr.labels_memo"


def memo_labels(db: AsyncSession) -> None:
    """Read the label list once for the rest of this request: a layout's counts build the
    conditions of every section and every claiming tab, and each read the labels again. Only for
    requests that do not change labels."""
    db.info.setdefault(_LABELS_MEMO, {})


async def _label_counts(db: AsyncSession, p: Principal) -> tuple[dict[int, tuple[int, int]], int]:
    """{label id: (unread, total)} and the Drafts total, each the count of the list the label
    opens (core/sidebar.py: the list's own conditions, so Trash and Spam, snoozed and muted, and a
    token's visibility are left out exactly as the list leaves them out)."""
    from mailr.core import sidebar

    counts = await sidebar.sidebar_counts(db, p)
    return sidebar.label_numbers(counts, await _labels(db, p.user_id)), counts["views"]["drafts"]["total"]


@router.get("/counts", response_model=C.mail.SidebarCounts, **C.TYPED)
async def sidebar_counts(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """Every sidebar number in one answer: {cursor, views: {view: {unread,
    total}}, inbox_count, waiting, approvals, failed}. Each view is counted with the conditions
    its list uses, so a number always equals the length of the list it opens. The live stream
    sends the same object as a `counts` event after each change."""
    from mailr.core import sidebar

    return await sidebar.sidebar_counts(db, p)


@router.get("/labels", response_model=C.mail.LabelList, **C.TYPED)
async def list_labels(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    labels = await _labels(db, p.user_id)
    counts, drafts = await _label_counts(db, p)
    out = []
    for lb in labels:
        u, t = counts.get(lb.id, (0, 0))
        if lb.role == "drafts":
            u, t = drafts, drafts
        out.append(label_out(lb, u, t))
    return out


# List order: newest first, undated last, the id as the tie-break. The expression is the
# one ix_conv_user_order indexes (migration f3a8c1d6b2e9), so a page, and the cursor's row
# comparison, is a range scan of that index instead of a sort of the whole view.
_NO_DATE = literal_column("'-infinity'::timestamptz")
LIST_DATE = func.coalesce(Conversation.last_date, _NO_DATE)
LIST_ORDER = (LIST_DATE.desc(), Conversation.id.desc())


def after_cursor(d: datetime | None, i: int):
    """Rows after (d, i) in list order."""
    return tuple_(LIST_DATE, Conversation.id) < tuple_(
        literal(d, DateTime(timezone=True)) if d is not None else _NO_DATE, literal(i, BigInteger))


def _encode_cursor(c: Conversation, total: int | None = None, capped: bool = False) -> str:
    # The id inside is the public one: a cursor is readable base64 and must not reveal how many
    # conversations the instance holds. It also carries the list's total from the first page
    # (the same number the answer shows), so a later page need not count.
    raw: list[Any] = [c.last_date.isoformat() if c.last_date else None, publicid.encode("conversation", c.id)]
    if total is not None:
        raw += [total, capped]
    return base64.urlsafe_b64encode(json.dumps(raw).encode()).decode()


def _decode_cursor(cur: str) -> tuple[datetime | None, int, tuple[int, bool] | None]:
    """(last date, conversation id, (total, capped) or None for a cursor without a total)."""
    try:
        parts = json.loads(base64.urlsafe_b64decode(cur.encode()))
        d, i = parts[0], parts[1]
        total = None
        if len(parts) >= 4 and isinstance(parts[2], int) and not isinstance(parts[2], bool) and parts[2] >= 0:
            total = (min(parts[2], TOTAL_CAP), bool(parts[3]))
        return (datetime.fromisoformat(d) if d else None), publicid.decode("conversation", i), total
    except (ValueError, TypeError, IndexError, KeyError, publicid.InvalidId):
        raise ApiError(400, "common.invalid_cursor") from None


# Counted list totals, per process: {key: (change cursor, time, total)}. A total is reused
# while the user's change_log cursor stands still, for at most TOTAL_TTL seconds (relative date
# searches such as newer_than:2d move without a change).
_TOTALS: OrderedDict[tuple, tuple[int, float, int]] = OrderedDict()
TOTAL_TTL = 60.0
_TOTALS_MAX = 2000


def _total_key(p: Principal, conds: list[Any]) -> tuple:
    stmt = select(Conversation.id).where(*conds).compile(dialect=postgresql.dialect())
    params = tuple(sorted((k, repr(v)) for k, v in stmt.params.items()))
    return (p.user_id, p.token.id if p.token is not None else None, str(stmt), params)


async def _change_cursor(db: AsyncSession, uid: int) -> int:
    return (await db.execute(text("SELECT coalesce(max(id), 0) FROM change_log WHERE user_id=:u"),
                             {"u": uid})).scalar_one()


async def list_total(db: AsyncSession, p: Principal, conds: list[Any], *, cached_only: bool = False
                     ) -> int | None:
    """How many conversations match, up to TOTAL_CAP + 1, counted once per change cursor.
    `cached_only`: None instead of counting when no counted total is current."""
    key = _total_key(p, conds)
    cur = await _change_cursor(db, p.user_id)
    hit = _TOTALS.get(key)
    now = time.monotonic()
    if hit and hit[0] == cur and now - hit[1] < TOTAL_TTL:
        _TOTALS.move_to_end(key)
        return hit[2]
    if cached_only:
        return None
    total = (await db.execute(select(func.count()).select_from(
        select(Conversation.id).where(*conds).limit(TOTAL_CAP + 1).subquery()))).scalar_one()
    _TOTALS[key] = (cur, now, total)
    while len(_TOTALS) > _TOTALS_MAX:
        _TOTALS.popitem(last=False)
    return total


def _section_filter(section: str):
    if section == "important":
        # Important conversations stay here after they are read (Gmail's "Important" section).
        return Conversation.important.is_(True)
    if section == "important_unread":
        return and_(Conversation.important.is_(True), Conversation.unread_count > 0)
    if section == "unread":
        return Conversation.unread_count > 0
    if section == "starred":
        return Conversation.starred.is_(True)
    return None


async def conversation_conditions(db: AsyncSession, p: Principal, view: str = "inbox", q: str = "",
                                  section: str | None = None, sections: str = "", exclude: str = "",
                                  category: str | None = None, layout: str | None = None,
                                  tab: str | None = None, sec: str | None = None, *,
                                  labels: list[Label] | None = None) -> list[Any] | None:
    """WHERE conditions for a conversation list (views, Priority Inbox sections, categories,
    layout sections, search, token visibility). None means the list is empty by definition
    (e.g. no Inbox label yet). Shared by the list endpoint and bulk actions on "all N
    conversations"."""
    if layout:
        if not sec:
            # The whole tab: every section of it together (what the tab shows), for "this view" in
            # the chat (agent/view_tools.py) and any list asked by layout and tab alone.
            return await layout_tab_conditions(db, p, layout, tab or "", q)
        return await layout_section_conditions(db, p, layout, tab or "", sec, q)
    from mailr.core import mailaccounts

    try:
        scoped = mailaccounts.parse_view(view)
    except ValueError:
        raise HTTPException(400, f"unknown view {view}") from None  # i18n: internal
    if scoped is not None:
        # ACT mail.account_view @api, ACT mail.account_label_view @api
        # One account's own folder (`acct:<id>:<view>`): the unified view, narrowed to the mail of
        # that account (core/mailaccounts.py). An account the caller may not see lists nothing.
        acct_id, base = scoped
        if acct_id not in {a.id for a in await mailaccounts.mail_accounts(db, p)}:
            return None
        conds = await conversation_conditions(db, p, base, q, section, sections, exclude, category, labels=labels)
        if conds is None:
            return None
        indexed = not is_restricted(p) and await mailaccounts.index_current(db, p.user_id)
        return [*conds, mailaccounts.conversation_in_accounts(p, [acct_id], indexed=indexed)]
    uid = p.user_id
    if labels is None:
        labels = await _labels(db, uid)
    by_role = {lb.role: lb.id for lb in labels if lb.role}
    conds: list[Any] = [Conversation.user_id == uid]
    hidden = [by_role[r] for r in ("junk", "trash") if r in by_role]

    v = view
    if v == "inbox":
        if "inbox" not in by_role:
            return None
        conds += [Conversation.label_ids.contains([by_role["inbox"]]), Conversation.snoozed_until.is_(None),
                  Conversation.muted.is_(False)]
    elif v == "starred":
        conds.append(Conversation.starred.is_(True))
    elif v == "snoozed":
        conds.append(Conversation.snoozed_until.is_not(None))
    elif v == "important":
        conds.append(Conversation.important.is_(True))
    elif v == "drafts":
        conds.append(Conversation.draft_count > 0)
    elif v == "scheduled":
        conds.append(
            Conversation.id.in_(
                select(Outbox.conversation_id).where(
                    Outbox.user_id == uid, Outbox.status.in_(("queued", "awaiting_approval"))
                )
            )
        )
    elif v in ("sent", "spam", "trash", "archive"):
        role = {"spam": "junk"}.get(v, v)
        if role not in by_role:
            return None
        conds.append(Conversation.label_ids.contains([by_role[role]]))
    elif v.startswith("label:"):
        try:
            lid = int(v.split(":", 1)[1])
        except ValueError:
            raise HTTPException(400, "bad label id") from None  # i18n: internal
        child_ids = [lb.id for lb in labels if lb.id == lid]
        parent = next((lb for lb in labels if lb.id == lid), None)
        if parent:
            child_ids += [lb.id for lb in labels if lb.name.startswith(parent.name + "/")]
        conds.append(Conversation.label_ids.overlap(child_ids))
    elif v == "screener":
        # Conversations with mail held in the Screener (core/screener.held_conditions).
        from mailr.core import screener

        conds.append(Conversation.id.in_(await screener.held_conversation_ids(db, uid, p)))
    elif v.startswith("category:"):
        cat = v.split(":", 1)[1]
        conds.append(Conversation.category == cat)
        if "inbox" in by_role:
            conds.append(Conversation.label_ids.contains([by_role["inbox"]]))
    elif v != "all":
        raise HTTPException(400, f"unknown view {v}")  # i18n: internal

    if v not in ("spam", "trash") and hidden and not q:
        # Hidden only when everything in it is in Trash/Spam: a conversation with one deleted
        # message and one in the Inbox still shows in the Inbox (Gmail behaviour).
        conds.append(not_(Conversation.label_ids.contained_by(hidden)))

    if category:
        if category == "primary":
            conds.append(or_(Conversation.category.is_(None), Conversation.category == "primary"))
        else:
            conds.append(Conversation.category == category)

    if section:
        f = _section_filter(section)
        if f is not None:
            conds.append(f)
        # Sections are exclusive in the order given: a conversation shown in an earlier
        # section is not repeated in a later one ("everything" = none of the earlier ones).
        order = [x for x in sections.split(",") if x]
        earlier = order[: order.index(section)] if section in order else []
        for ex in [e for e in exclude.split(",") if e] + earlier:
            fx = _section_filter(ex)
            if fx is not None:
                conds.append(not_(fx))

    if q.strip():
        try:
            compiled = searchmod.compile_query(q, uid, labels, local_ok=p.token is None or bool(p.token.local_only),
                                              connector_ids=p.token.connector_ids if p.token is not None else None,
                                              zone=await searchmod.query_zone(db, uid, q))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        sub = searchmod.conversation_ids_query(uid, compiled, labels)
        if p.token is not None:
            sub = sub.where(visible_message_filter(p))
        conds.append(Conversation.id.in_(sub))
    elif p.token is not None:
        # A conversation whose only visible message is deleted is not listed: it would open as a
        # 404. visible_conversation_ids holds the deleted filter.
        conds.append(Conversation.id.in_(visible_conversation_ids(p)))

    return conds


async def section_conditions(db: AsyncSession, p: Principal, spec: dict[str, Any], q: str = ""
                             ) -> list[Any] | None:
    """Conditions for one layout section (see core/layouts.py). A section with `inbox_only` also
    takes the Inbox list's own conditions, so its list, its counts, the tabs it claims mail from
    and "Show in tab" all agree on what it holds."""
    from mailr.core import layouts

    query = " ".join(x for x in ((spec.get("q") or "").strip(), q.strip()) if x)
    if query and (spec.get("q") or "").strip() and q.strip():
        query = f"({spec['q'].strip()}) {q.strip()}"
    conds = await conversation_conditions(db, p, spec.get("base") or "inbox", query, spec.get("filter"),
                                          category=spec.get("category"))
    if conds is None or not layouts.inbox_only(spec):
        return conds
    inbox = await conversation_conditions(db, p, "inbox")
    if inbox is None:
        return None
    return [*conds, *inbox[1:]]  # inbox[0] is the user condition conds already holds


async def layout_section_conditions(db: AsyncSession, p: Principal, layout_id: str, tab_id: str,
                                    sec_id: str, q: str = "") -> list[Any] | None:
    from mailr.core import layouts

    found = layouts.find_section(await _settings(db, p), layout_id, tab_id, sec_id)
    if found is None:
        raise ApiError(404, "mail.no_layout_section")
    spec, above = found
    conds = await section_conditions(db, p, spec, q)
    if conds is None:
        return None
    if spec.get("exclude_above"):
        for other in above:
            oc = await section_conditions(db, p, other)
            if oc:
                conds.append(excluding(oc))
    conds += await claimed_conditions(db, p, layout_id, tab_id)
    return conds


async def layout_tab_conditions(db: AsyncSession, p: Principal, layout_id: str, tab_id: str,
                                q: str = "") -> list[Any] | None:
    """A whole layout tab: the conversations any of its sections lists (tab_conditions)."""
    from mailr.core import layouts

    tab = next((t for t in (layouts.find(await _settings(db, p), layout_id) or {}).get("tabs", [])
                if t["id"] == tab_id), None)
    if tab is None:
        raise ApiError(404, "mail.no_layout_section")
    return await tab_conditions(db, p, layout_id, tab, q)


async def tab_conditions(db: AsyncSession, p: Principal, layout_id: str, tab: dict[str, Any],
                         q: str = "") -> list[Any] | None:
    """What a tab lists: any of its sections, each with its own rules (base, category, filter,
    search, inbox_only), less what claiming tabs take, as each section's list does. `exclude_above`
    only splits the union between the sections, so the union is the same with or without it. None
    when no section can match."""
    ors = []
    for s in tab["sections"]:
        c = await section_conditions(db, p, s, q)
        if c:
            ors.append(and_(*c))
    if not ors:
        return None
    return [Conversation.user_id == p.user_id, or_(*ors), *await claimed_conditions(db, p, layout_id, tab["id"])]


def excluding(conds: list[Any]) -> Any:
    """NOT (all of conds), where a NULL comparison counts as false. A plain NOT(a AND b) is NULL
    when b is NULL (category IS NULL = 'promotions'), and SQL drops that row, so an
    uncategorised conversation fell out of every tab when another tab claimed a category."""
    return not_(func.coalesce(and_(*conds), false()))


async def claimed_conditions(db: AsyncSession, p: Principal, layout_id: str, tab_id: str) -> list[Any]:
    """Leave out what another tab of the layout claims (core/layouts.py `claims`)."""
    from mailr.core import layouts

    out = []
    for spec in layouts.claimed_elsewhere(await _settings(db, p), layout_id, tab_id):
        oc = await section_conditions(db, p, spec)
        if oc:
            out.append(excluding(oc))
    return out


async def _settings(db: AsyncSession, p: Principal) -> dict[str, Any]:
    from mailr.api.auth_routes import DEFAULT_SETTINGS
    from mailr.models import User

    u = await db.get(User, p.user_id)
    return {**DEFAULT_SETTINGS, **((u.settings if u else None) or {})}


@router.get("/conversations", response_model=C.mail.ConversationPage, **C.TYPED)
async def list_conversations(
    request: Request,
    view: str = "inbox",
    q: str = "",
    section: str | None = None,
    sections: str = "",
    exclude: str = "",
    category: str | None = None,
    layout: str | None = None,
    tab: str | None = None,
    sec: str | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    count: bool | None = None,
    p: Principal = Depends(scope("read")),
    db: AsyncSession = Depends(get_db),
):
    memo_labels(db)
    from mailr.core.mailaccounts import memo_accounts

    memo_accounts(db)
    conds = await conversation_conditions(db, p, view, q, section, sections, exclude, category, layout, tab, sec)
    if conds is None:
        return {"items": [], "next_cursor": None, "total": 0}
    # The total: counted on the first page, at most once per change cursor (cached), capped
    # (the UI shows "200,000+" above the cap). A later page reuses a current count, else the
    # total its cursor carries from the first page; it counts only for an old cursor without one
    # or when asked (count=true). count=false leaves the total out.
    page_conds = list(conds)
    carried: tuple[int, bool] | None = None
    if cursor:
        d, i, carried = _decode_cursor(cursor)
        page_conds.append(after_cursor(d, i))
    total: int | None = None
    capped = False
    if count is not False:
        if cursor and count is None and carried is not None:
            total = await list_total(db, p, conds, cached_only=True)
            if total is None:
                total, capped = carried
            else:
                total, capped = min(total, TOTAL_CAP), total > TOTAL_CAP
        else:
            n = await list_total(db, p, conds)
            total, capped = min(n, TOTAL_CAP), n > TOTAL_CAP
    rows = (
        await db.execute(
            select(Conversation)
            .where(*page_conds)
            .order_by(*LIST_ORDER)
            .limit(limit + 1)
        )
    ).scalars().all()
    more = len(rows) > limit
    rows = rows[:limit]
    previews = await _attachment_previews(db, [c.id for c in rows if c.has_attachments], p)
    views = await token_views(db, p, [c.id for c in rows])
    signals = await conversation_signals(db, p, [c.id for c in rows])
    from mailr.core.mailaccounts import conversation_accounts

    # The accounts each row belongs to (the row's account dots; only with two or more accounts).
    accts = await conversation_accounts(db, p, [c.id for c in rows])
    # A search whose words match only inside an attached email: the row says so.
    in_attached: set[int] = set()
    if q.strip():
        words = searchmod.plain_words(q)
        in_attached = await searchmod.attached_only_matches(db, p.user_id, [c.id for c in rows], words)
    await audit(db, p, "conversations.list", view, {"q": q, "section": section}, {"count": len(rows)})
    await db.commit()
    return {
        "items": [conversation_out(c, {**views.get(c.id, {}), "attachments_preview": previews.get(c.id, []),
                                       "signals": signals.get(c.id, []),
                                       **({"accounts": accts[c.id]} if c.id in accts else {}),
                                       **({"match_in_attached": True} if c.id in in_attached else {})})
                  for c in rows],
        "next_cursor": _encode_cursor(rows[-1], total, capped) if more and rows else None,
        **({"total": total, "total_capped": capped} if total is not None else {}),
    }


async def conversation_signals(db: AsyncSession, p: Principal, conv_ids: list[int]) -> dict[int, list[dict]]:
    """Triage signals on each conversation's newest message, as the is:needs_reply / is:waiting /
    has:deadline searches read them: needs_reply while the newest message is to you and
    unanswered, waiting_on while it is yours, a deadline until the day after it. Messages in Spam
    or Trash carry none (core/spamtrash.py): "newest" is the newest message in neither, so a
    conversation that is all Spam or Trash has no signals, and they come back when it leaves."""
    from mailr.core.spamtrash import live_sql

    if not conv_ids:
        return {}
    rows = (await db.execute(text(
        "SELECT DISTINCT ON (m.conversation_id) m.conversation_id cid, m.id mid, m.sent_by_me, m.answered "
        "FROM messages m WHERE m.conversation_id = ANY(:c) AND NOT m.deleted AND NOT m.draft "
        f"AND {live_sql('m')} "
        + ("AND m.id = ANY(:v) " if p.token is not None else "")
        + "ORDER BY m.conversation_id, m.date DESC NULLS LAST, m.id DESC"),
        {"c": conv_ids, **({"v": list((await db.execute(select(Message.id).where(
            Message.conversation_id.in_(conv_ids), visible_message_filter(p)))).scalars())} if p.token is not None else {})},
    )).all()
    last = {r.mid: r for r in rows}
    if not last:
        return {}
    decs = (await db.execute(text(
        "SELECT message_id, target, reason, source_ref FROM label_decisions WHERE message_id = ANY(:m) "
        "AND kind = 'smart' AND value AND status <> 'rejected' ORDER BY id"), {"m": list(last)})).all()
    from mailr.calendar.tz import user_zone

    # A deadline's chip lasts to the end of its day on the user's calendar, not the UTC one. The
    # zone is read only when a deadline is on the page (one statement less on every list page).
    today = datetime.now(await user_zone(db, p.user)).date().isoformat() \
        if any(d.target == "deadline" and d.source_ref for d in decs) else ""
    out: dict[int, list[dict]] = {}
    for d in decs:
        r = last[d.message_id]
        if d.target == "needs_reply" and (r.sent_by_me or r.answered):
            continue
        if d.target == "waiting_on" and not r.sent_by_me:
            continue
        if d.target == "deadline" and d.source_ref and d.source_ref < today:
            continue
        sig = {"name": d.target, "reason": (d.reason or "")[:120]}
        if d.target == "deadline":
            sig["date"] = d.source_ref or None
        lst = out.setdefault(r.cid, [])
        if all(x["name"] != d.target for x in lst):
            lst.append(sig)
    return out


# Counting stops here (a large search stays cheap); above it the list says "200,000+".
TOTAL_CAP = 200_000


async def token_views(db: AsyncSession, p: Principal, conv_ids: list[int]) -> dict[int, dict]:
    """For a token that cannot see every message, conversation summaries (subject, snippet,
    participants, counts, labels) are recomputed from the messages it may see, so a mixed
    thread never shows local-only text in its summary row."""
    if not conv_ids or not is_restricted(p):
        return {}
    from mailr.core import threading

    visible = (await db.execute(select(Message.id).where(
        Message.conversation_id.in_(conv_ids), Message.deleted.is_(False), visible_message_filter(p)))).scalars().all()
    return await threading.aggregate(db, conv_ids, only_message_ids=list(visible))


async def _attachment_previews(db: AsyncSession, conv_ids: list[int], p: Principal | None = None
                               ) -> dict[int, list[dict]]:
    if not conv_ids:
        return {}
    extra, params = "", {"ids": conv_ids}
    if p is not None and is_restricted(p):
        visible = (await db.execute(select(Message.id).where(
            Message.conversation_id.in_(conv_ids), visible_message_filter(p)))).scalars().all()
        extra, params["mids"] = "AND id = ANY(:mids)", list(visible)
    rows = await db.execute(
        text(
            "SELECT conversation_id, a->>'filename' AS fname, a->>'type' AS ftype, a->>'size' AS fsize "
            "FROM messages, jsonb_array_elements(attachments) a WHERE conversation_id = ANY(:ids) "
            f"AND NOT deleted AND coalesce((a->>'inline')::bool, false) = false {extra} "
            "ORDER BY conversation_id, date, id"
        ),
        params,
    )
    # The same file (name and size) sent twice in a thread is one chip with a count.
    out: dict[int, list[dict]] = {}
    keys: dict[int, list[tuple]] = {}
    for r in rows:
        lst = out.setdefault(r.conversation_id, [])
        seen = keys.setdefault(r.conversation_id, [])
        key = (r.fname, r.fsize)
        if key in seen:
            lst[seen.index(key)]["n"] += 1
        elif len(lst) < 3:
            lst.append({"filename": r.fname, "type": r.ftype, "n": 1})
            seen.append(key)
    return out


async def load_conversation(db: AsyncSession, p: Principal, cid: int, *, with_body: bool = True
                            ) -> tuple[Conversation, list[Message]]:
    """The conversation and its messages. `with_body` False leaves out the body text and the
    embedding (the reader renders bodies from the store)."""
    conv = await db.get(Conversation, cid)
    if conv is None or conv.user_id != p.user_id:
        raise ApiError(404, "mail.conversation_not_found")
    stmt = (
        select(Message)
        .options(*(light_message() if not with_body else (defer(Message.embedding, raiseload=True),)))
        .where(Message.conversation_id == cid, Message.deleted.is_(False))
        .order_by(Message.date.asc().nulls_last(), Message.id)
    )
    if p.token is not None:
        stmt = stmt.where(visible_message_filter(p))
    msgs = list((await db.execute(stmt)).scalars())
    if not msgs:
        raise ApiError(404, "mail.conversation_not_found")
    return conv, msgs


@router.get("/conversations/{cid}", response_model=C.mail.ConversationDetail, **C.TYPED)
async def get_conversation(cid: int, response: Response, p: Principal = Depends(scope("read")),
                           db: AsyncSession = Depends(get_db)):
    conv, msgs = await load_conversation(db, p, cid, with_body=False)
    await audit(db, p, "conversations.get", str(cid))
    await db.commit()
    if p.is_token:
        response.headers["X-Mailr-Content"] = "untrusted"
    views = await token_views(db, p, [conv.id])
    # The reader shows the same signal chips as the list row (the Due chip and its menu).
    signals = (await conversation_signals(db, p, [conv.id])).get(conv.id, [])
    own = await own_languages(db, p.user_id)
    return {"conversation": conversation_out(conv, {**views.get(conv.id, {}), "signals": signals}),
            "messages": [message_out(m, untrusted=p.is_token, own_langs=own) for m in msgs]}


async def load_message(db: AsyncSession, p: Principal, mid: int) -> Message:
    m = await db.get(Message, mid)
    if m is None or m.user_id != p.user_id or m.deleted:
        raise ApiError(404, "mail.message_not_found")
    if p.token is not None:
        ok = (await db.execute(select(Message.id).where(Message.id == mid, visible_message_filter(p)))).first()
        if not ok:
            raise ApiError(404, "mail.message_not_found")
    return m


@router.get("/messages/{mid}")
async def get_message(mid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    m = await load_message(db, p, mid)
    return message_out(m, untrusted=p.is_token, own_langs=await own_languages(db, p.user_id))


async def render_for_reader(db: AsyncSession, m: Message, raw: bytes | Callable[[], Awaitable[bytes]],
                            allow_images: bool, *, cache: bool = False, part: int | None = None) -> dict:
    """The sanitised body (core/render.py). With the image proxy on, allowed images point at
    GET /messages/{id}/images/{hash}; `junk` tells the reader to ask before loading them.
    While remote content is paused (core/egress.py: the egress proxy is required but not
    usable), images stay blocked and `images_paused` says why.

    `raw` is the message (or a function that fetches it, called only when the render is not
    cached). With `cache`, the render comes from core/bodycache.py when it holds one for this
    message (`part`: an attached email inside it) and these image settings. The caller has
    authorised the request (load_message) before; junk and paused are worked out every time."""
    from mailr.core import egress, imageproxy, publicid
    from mailr.core.bodycache import bodies
    from mailr.core.render import render_body

    on, configured = await imageproxy.instance_config(db)
    paused = None
    if on and allow_images:
        try:
            await egress.gate(configured)
        except egress.EgressUnavailable as e:
            paused = str(e)
    pid = publicid.encode("message", m.id) if publicid.enabled() else m.id
    base = imageproxy.proxy_path(pid, "") if on else None
    allow = allow_images and paused is None
    key = bodies.key(m.id, m.blob_id, allow, base, part) if cache else None
    out = bodies.get(key) if key else None
    if out is None:
        data = raw if isinstance(raw, bytes) else await raw()
        deps: dict[str, tuple | None] = {}
        # Parsing and sanitising run off the event loop: a crafted body must not stall every request.
        out = await asyncio.to_thread(render_body, data, m.id, allow, base, False, deps)
        if key:
            bodies.put(key, out, deps)
    out["junk"] = await imageproxy.is_junk(db, m)
    if paused and out.get("remote_images"):
        out["images_paused"] = paused
    return out


@router.get("/messages/{mid}/body", response_model=C.mail.MessageBody, **C.TYPED)
async def get_body(
    mid: int,
    response: Response,
    images: str = "blocked",
    p: Principal = Depends(scope("read")),
    db: AsyncSession = Depends(get_db),
):
    """The message body for the reader. It changes nothing: no read flag, no receipt, no audit
    entry for a session (only tokens are audited), so the web app may prefetch it."""
    from mailr.core.store import user_store

    m = await load_message(db, p, mid)  # authorisation first, cached or not

    async def fetch() -> bytes:
        store = await user_store(db, p.user)
        return await store.get_raw(m)

    out = await render_for_reader(db, m, fetch, images == "allowed", cache=True)
    await audit(db, p, "messages.body", str(mid))
    await db.commit()
    if p.is_token:
        response.headers["X-Mailr-Content"] = "untrusted"
        out["untrusted"] = True
    return out


@router.get("/messages/{mid}/raw")
async def get_raw(mid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    from mailr.core.store import user_store

    m = await load_message(db, p, mid)
    store = await user_store(db, p.user)
    raw = await store.get_raw(m)
    await audit(db, p, "messages.raw", str(mid))
    await db.commit()
    headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'"}
    if p.is_token:
        headers["X-Mailr-Content"] = "untrusted"
    return Response(raw, media_type="text/plain; charset=utf-8", headers=headers)


@router.get("/messages/{mid}/attachments/{part}")
async def get_attachment(mid: int, part: int, download: bool = False, thumb: bool = False,
                         p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    from urllib.parse import quote

    from mailr.core.mime import get_part
    from mailr.core.store import user_store

    m = await load_message(db, p, mid)
    store = await user_store(db, p.user)
    raw = await store.get_raw(m)
    try:
        data, ctype, fname = await asyncio.to_thread(get_part, raw, part)  # off the event loop
    except KeyError:
        raise ApiError(404, "mail.no_such_part") from None
    await audit(db, p, "messages.attachment", f"{mid}/{part}")
    await db.commit()
    ctype = _effective_type(ctype, fname, data)
    if thumb and ctype in SAFE_IMAGE_TYPES and not download:
        # ?thumb=1: the Attachments grid's preview, a small copy instead of the original.
        from mailr.core.attachfetch import thumbnail

        small = await thumbnail(data)
        if small:
            data, ctype = small
    # Images, PDFs, media and plain text open in the browser; everything else downloads.
    # Scripts never run: responses carry nosniff and a CSP that forbids them.
    safe_inline = not download and (ctype in SAFE_IMAGE_TYPES or ctype.startswith(("audio/", "video/"))
                                     or ctype in ("text/plain", "application/pdf"))
    disp = "inline" if safe_inline else "attachment"
    headers = {
        "Content-Disposition": f"{disp}; filename*=UTF-8''{quote(fname)}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=3600",
    }
    # Chrome and Firefox refuse to render a PDF under `sandbox` (as a header or an iframe
    # attribute), so PDFs get a CSP without it: nothing loads or runs outside the browsers' own
    # PDF viewers, and only mailr may frame them. Both browsers render PDFs under this policy.
    if ctype == "application/pdf" and safe_inline:
        headers["Content-Security-Policy"] = PDF_CSP
    else:
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"
    if safe_inline:
        headers["X-Frame-Options"] = "SAMEORIGIN"  # the in-app viewer frames it
    return Response(data, media_type=ctype if safe_inline else "application/octet-stream", headers=headers)


@router.get("/messages/{mid}/attachments/{part}/email")
async def get_attached_email(mid: int, part: int, response: Response, images: str = "blocked",
                             p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    # ACT mail.open_attached_email @api
    """An email attached to the message (message/rfc822) as the reader shows it: its headers,
    its body sanitised like any message body (remote images blocked unless `images=allowed`, and
    then only through the image proxy), and its own attachments, numbered as parts of the outer
    message so they download with GET /messages/{mid}/attachments/{part}."""
    from mailr.core.mime import attached_email_at, parse
    from mailr.core.store import user_store

    m = await load_message(db, p, mid)
    store = await user_store(db, p.user)
    raw = await store.get_raw(m)
    try:
        inner, offset = await asyncio.to_thread(attached_email_at, raw, part)
    except KeyError:
        raise ApiError(404, "mail.no_such_part") from None
    except (ValueError, UnicodeError, TypeError, AttributeError):
        # One that cannot be read back is a coded refusal, never a 500; it still downloads.
        raise ApiError(422, "mail.attached_email_unreadable") from None
    pz = await asyncio.to_thread(parse, inner)
    try:
        out = await render_for_reader(db, m, inner, images == "allowed", cache=True, part=part)
    except (UnicodeError, LookupError):
        raise ApiError(422, "mail.attached_email_unreadable") from None
    # The attached email's own parts sit right after it in the outer message's part order, as
    # long as it was sent the standard way; one sent base64-encoded (not allowed, but seen) has
    # no such parts, so its files are listed without a part to download.
    outer_types = await asyncio.to_thread(_part_types, raw)
    atts = []
    for a in pz.attachments:
        if a.get("inline") and a.get("cid"):
            continue
        n = offset + int(a["part"])
        item = {k: a.get(k) for k in ("filename", "type", "size", "inline", "email") if k != "email" or a.get(k)}
        item["part"] = n if outer_types.get(n) in (a["type"], *(("message/global", "message/news") if a["type"] == "message/rfc822" else ())) else None
        atts.append(item)
    await audit(db, p, "messages.attached_email", f"{mid}/{part}")
    await db.commit()
    if p.is_token:
        response.headers["X-Mailr-Content"] = "untrusted"
        out["untrusted"] = True
    out.update({
        "part": part, "subject": pz.subject, "from": {"email": pz.from_email, "name": pz.from_name},
        "to": pz.to, "cc": pz.cc, "date": pz.date.isoformat() if pz.date else None, "attachments": atts,
    })
    return out


def _part_types(raw: bytes) -> dict[int, str]:
    """The content type of each part of a message, by walk index."""
    import email
    import email.policy

    from mailr.core.mime import MAX_PARTS, too_deep

    if too_deep(raw):
        return {}
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    out: dict[int, str] = {}
    for idx, part in enumerate(msg.walk()):
        if idx >= MAX_PARTS:
            break
        out[idx] = part.get_content_type()
    return out


PDF_CSP = "default-src 'none'; object-src 'self'; style-src 'unsafe-inline'; img-src data: blob:; frame-ancestors 'self'"


def _effective_type(ctype: str, fname: str, data: bytes) -> str:
    """Many mailers label every attachment application/octet-stream. Trust the file name and
    the content's magic bytes for the few types mailr previews; never upgrade to HTML/SVG."""
    import mimetypes

    if ctype not in ("application/octet-stream", "binary/octet-stream", "application/x-download",
                     "application/download", "application/force-download"):
        return ctype
    if data[:5] == b"%PDF-":
        return "application/pdf"
    guess = (mimetypes.guess_type(fname)[0] or "").lower()
    if guess in SAFE_IMAGE_TYPES or guess.startswith(("audio/", "video/")) or guess == "text/plain":
        return guess
    return ctype


# Parsed inline images per message, for a few seconds: a message with 20 cid: images asks
# 20 times in a row, and each ask used to fetch and parse the whole MIME tree again.
_INLINE: OrderedDict[tuple[int, int], tuple[float, dict]] = OrderedDict()
_INLINE_TTL = 30.0
_INLINE_MAX = 16


def _inline_cached(user_id: int, mid: int) -> dict | None:
    hit = _INLINE.get((user_id, mid))
    if hit is None or time.monotonic() - hit[0] > _INLINE_TTL:
        return None
    return hit[1]


def _inline_cache_put(user_id: int, mid: int, imgs: dict) -> None:
    _INLINE[(user_id, mid)] = (time.monotonic(), imgs)
    _INLINE.move_to_end((user_id, mid))
    while len(_INLINE) > _INLINE_MAX:
        _INLINE.popitem(last=False)


@router.get("/messages/{mid}/cid/{cid}")
async def get_inline_image(mid: int, cid: str, p: Principal = Depends(scope("read")),
                           db: AsyncSession = Depends(get_db)):
    from mailr.core.mime import inline_images
    from mailr.core.store import user_store

    m = await load_message(db, p, mid)
    imgs = _inline_cached(p.user_id, mid)
    if imgs is None:
        store = await user_store(db, p.user)
        imgs = await asyncio.to_thread(inline_images, await store.get_raw(m))
        _inline_cache_put(p.user_id, mid, imgs)
    if cid not in imgs:
        raise ApiError(404, "mail.no_such_image")
    data, ctype = imgs[cid]
    # Only raster images are served inline; SVG (which can carry script) and anything else
    # downloads, and every response is sandboxed.
    headers = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff",
               "Content-Security-Policy": "sandbox; default-src 'none'; img-src data:"}
    if ctype not in SAFE_IMAGE_TYPES:
        headers["Content-Disposition"] = "attachment"
        ctype = "application/octet-stream"
    return Response(data, media_type=ctype, headers=headers)


# Bytes each message has pulled through the image proxy in the serving route (prefetch has its
# own limit in imageproxy.fetch_all). Bounded LRU: an old message that drops out may fetch again.
_IMAGE_BUDGET: OrderedDict[tuple[int, int], int] = OrderedDict()
_IMAGE_BUDGET_MAX = 4096


def _image_budget_spent(user_id: int, mid: int) -> bool:
    from mailr.core import imageproxy

    return _IMAGE_BUDGET.get((user_id, mid), 0) >= imageproxy.MAX_MESSAGE_BYTES


def _charge_image_budget(user_id: int, mid: int, size: int) -> None:
    key = (user_id, mid)
    _IMAGE_BUDGET[key] = _IMAGE_BUDGET.get(key, 0) + max(size, 0)
    _IMAGE_BUDGET.move_to_end(key)
    while len(_IMAGE_BUDGET) > _IMAGE_BUDGET_MAX:
        _IMAGE_BUDGET.popitem(last=False)


@router.get("/messages/{mid}/images/{h}")
async def get_proxied_image(mid: int, h: str, p: Principal = Depends(scope("read")),
                            db: AsyncSession = Depends(get_db)):
    # ACT mail.show_images @api
    """A remote image of the message, fetched by the server. Only images
    the caller's own message references are served; one not fetched yet is fetched now
    (older mail opened for the first time)."""
    # ACT mail.show_images
    from mailr.core import imageproxy
    from mailr.core.egress import EgressUnavailable
    from mailr.core.store import user_store

    if not imageproxy.HASH_RE.match(h):
        raise ApiError(404, "mail.no_such_image")
    m = await load_message(db, p, mid)
    on, egress = await imageproxy.instance_config(db)
    if not on:
        raise ApiError(404, "mail.image_proxy_off")

    async def raw():
        store = await user_store(db, p.user)
        return await store.get_raw(m)

    refs = await imageproxy.cached_refs(p.user_id, m.id, raw)
    url = refs.get(h)
    if url is None:
        raise ApiError(404, "mail.no_such_image")
    await db.commit()  # no transaction stays open while a remote server answers
    # Only an entry fetched for this user counts as cached: a quick answer must not tell them that
    # another user's mail showed the image (core/imageproxy.py).
    cached = imageproxy.lookup_for(h, p.user_id)
    if cached is None and _image_budget_spent(p.user_id, m.id):
        # The message has already pulled MAX_MESSAGE_BYTES through the proxy.
        raise ApiError(404, "mail.image_unavailable")
    try:
        e = cached or await imageproxy.fetch(url, egress=egress or None, user_id=p.user_id)
    except EgressUnavailable:
        raise ApiError(404, "mail.images_paused") from None
    if cached is None:
        _charge_image_budget(p.user_id, m.id, e.size)
    data = imageproxy.read_blob(e) if e.status == "ok" else None
    if data is None:
        raise ApiError(404, "mail.image_unavailable")
    return Response(data, media_type=e.type, headers={
        "Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'", "Content-Disposition": "inline",
        "ETag": f'"{e.sha[:32]}"', "Referrer-Policy": "no-referrer"})


SAFE_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp",
                    "image/x-icon", "image/vnd.microsoft.icon"}


async def _visible_calendar_changes(db: AsyncSession, p: Principal, rows: list) -> list:
    """Calendar entities (event, task, calendar) for a restricted token: only those of the
    connectors it may see. Deletes pass (they reveal only an id)."""
    from mailr.calendar.mirror import visible_connector_ids

    obj_ids = [r.entity_id for r in rows if r.entity in ("event", "task") and r.op != "delete"]
    coll_ids = [r.entity_id for r in rows if r.entity == "calendar" and r.op != "delete"]
    if not obj_ids and not coll_ids:
        return rows
    allowed = set(await visible_connector_ids(db, p.user_id, p))
    seen: set[tuple[str, int]] = set()
    if obj_ids:
        for oid, kind, cid in (await db.execute(text(
                "SELECT id, kind, connector_id FROM cal_objects WHERE user_id=:u AND id = ANY(:ids)"),
                {"u": p.user_id, "ids": obj_ids})).all():
            if cid in allowed:
                seen.add((kind, oid))
    if coll_ids:
        for cid_, conn in (await db.execute(text(
                "SELECT id, connector_id FROM cal_collections WHERE user_id=:u AND id = ANY(:ids)"),
                {"u": p.user_id, "ids": coll_ids})).all():
            if conn in allowed:
                seen.add(("calendar", cid_))
    return [r for r in rows if r.entity not in ("event", "task", "calendar") or r.op == "delete"
            or (r.entity, r.entity_id) in seen]


@router.get("/changes", response_model=C.mail.Changes, **C.TYPED)
async def changes(since: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000), p: Principal = Depends(scope("read")),
                  db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            text(
                "SELECT id, entity, entity_id, op FROM change_log WHERE user_id=:u AND id > :s "
                "ORDER BY id LIMIT :l"
            ),
            {"u": p.user_id, "s": since, "l": limit + 1},
        )
    ).all()
    more = len(rows) > limit
    rows = rows[:limit]
    if since > 0:
        # Ids grow with time for every user and pruning goes by age: a cursor below the oldest row
        # left in the log may have missed pruned changes of this user, newer ones or not. The
        # client must resync fully.
        oldest = (await db.execute(text("SELECT min(id) FROM change_log"))).scalar()
        if oldest is not None and since < oldest - 1:
            return {"cursor": since, "more": False, "changes": [], "reset": True,
                    "server_time": datetime.now(UTC).isoformat()}
    raw_last = rows[-1].id if rows else None
    if is_restricted(p) and rows:
        conv_ids = [r.entity_id for r in rows if r.entity == "conversation"]
        visible = set((await db.execute(select(Message.conversation_id).where(
            Message.conversation_id.in_(conv_ids), visible_message_filter(p)).distinct())).scalars()) if conv_ids else set()
        rows = [r for r in rows if r.entity != "conversation" or r.entity_id in visible or r.op == "delete"]
        rows = await _visible_calendar_changes(db, p, rows)
    if not rows and since == 0:
        cur = (await db.execute(text("SELECT coalesce(max(id),0) FROM change_log WHERE user_id=:u"),
                                {"u": p.user_id})).scalar_one()
    else:
        cur = raw_last if raw_last is not None else since
    # Collapse to the latest op per entity.
    latest: dict[tuple[str, int], str] = {}
    for r in rows:
        latest[(r.entity, r.entity_id)] = r.op
    return {"cursor": cur, "more": more,
            "changes": [{"entity": e, "id": ConvId(i) if e == "conversation" else MsgId(i) if e == "message" else i,
                         "op": o} for (e, i), o in latest.items()],
            "server_time": datetime.now(UTC).isoformat()}


# The attachments view: every file across the mailbox, newest first.
ATTACHMENT_KINDS: dict[str, tuple[str, ...]] = {
    "image": ("image/%",),
    "pdf": ("application/pdf",),
    "document": ("application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml%",
                 "application/vnd.oasis.opendocument.text%", "application/rtf", "text/plain", "text/markdown"),
    "spreadsheet": ("application/vnd.ms-excel%", "application/vnd.openxmlformats-officedocument.spreadsheetml%",
                    "application/vnd.oasis.opendocument.spreadsheet%", "text/csv"),
    "presentation": ("application/vnd.ms-powerpoint%", "application/vnd.openxmlformats-officedocument.presentationml%",
                     "application/vnd.oasis.opendocument.presentation%"),
    "archive": ("application/zip", "application/x-zip-compressed", "application/x-7z-compressed",
                "application/x-rar%", "application/vnd.rar", "application/gzip", "application/x-tar"),
}
_EXT_KIND = {"image": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic"), "pdf": (".pdf",),
             "document": (".doc", ".docx", ".odt", ".rtf", ".txt", ".md"),
             "spreadsheet": (".xls", ".xlsx", ".ods", ".csv"), "presentation": (".ppt", ".pptx", ".odp"),
             "archive": (".zip", ".7z", ".rar", ".gz", ".tgz", ".tar")}


@router.get("/attachments")
async def list_attachments(q: str = "", kind: str = "", name: str = "", offset: int = Query(0, ge=0),
                           cursor: str = "", limit: int = Query(60, ge=1, le=200),
                           p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """Attachments (not inline images) of the messages a search matches, newest first. `kind`
    is image, pdf, document, spreadsheet, presentation or archive (by type or file name);
    `name` filters on the file name. Page with `cursor` (the answer's `next_cursor`, which
    stays fast on long lists); `offset` and
    `next_offset` still work for older clients."""
    uid = p.user_id
    labels = await _labels(db, uid)
    msg_conds = [Message.user_id == uid, Message.deleted.is_(False), Message.draft.is_(False),
                 Message.has_attachments.is_(True), visible_message_filter(p)]
    include_hidden = False
    if q.strip():
        try:
            compiled = searchmod.compile_query(q, uid, labels, local_ok=p.token is None or bool(p.token.local_only),
                                              connector_ids=p.token.connector_ids if p.token is not None else None,
                                              zone=await searchmod.query_zone(db, uid, q))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        msg_conds.append(compiled.where)
        include_hidden = compiled.includes_spam_trash
    if not include_hidden:
        hidden = [lb.id for lb in labels if lb.role in ("junk", "trash")]
        if hidden:
            msg_conds.append(not_(Message.label_ids.overlap(hidden)))
    if kind and kind not in ATTACHMENT_KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(ATTACHMENT_KINDS)}")  # i18n: internal
    att = func.jsonb_array_elements(Message.attachments).table_valued("value", with_ordinality="ord").render_derived("a")
    val = att.c.value
    ctype, fname = func.lower(val.op("->>")("type")), val.op("->>")("filename")
    conds = [func.coalesce(val.op("->>")("inline"), "false") != "true"]
    if kind:
        by_type = [ctype.like(t) for t in ATTACHMENT_KINDS[kind]]
        by_name = [func.lower(fname).like(f"%{ext}") for ext in _EXT_KIND[kind]]
        conds.append(or_(*by_type, *by_name))
    if name.strip():
        conds.append(fname.ilike(f"%{name.strip().replace('%', '').replace('_', '')}%"))
    # Newest message first, its files in their order; the cursor compares (date, id, -position).
    # The order is that of ix_msg_attachments (the conditions above are its predicate).
    mdate = func.coalesce(Message.date, _NO_DATE)
    if cursor:
        try:
            cd, cm, co = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            at_date = literal(datetime.fromisoformat(cd), DateTime(timezone=True)) if cd else _NO_DATE
            at_msg = literal(publicid.decode("message", cm), BigInteger)
            after = tuple_(mdate, Message.id, -att.c.ord) < tuple_(at_date, at_msg, literal(-int(co), BigInteger))
        except (ValueError, TypeError, publicid.InvalidId):
            raise ApiError(400, "common.invalid_cursor") from None
        # The same range by message alone, which ix_msg_attachments can start from.
        conds += [after, tuple_(mdate, Message.id) <= tuple_(at_date, at_msg)]
        offset = 0
    stmt = (select(Message.id, Message.conversation_id, Message.subject, Message.from_name, Message.from_email,
                   Message.date, val, att.c.ord).select_from(Message).join(att, text("true"))
            .where(*msg_conds, *conds)
            .order_by(mdate.desc(), Message.id.desc(), att.c.ord)
            .offset(offset).limit(limit + 1))
    rows = (await db.execute(stmt)).all()
    more = len(rows) > limit
    last = rows[limit - 1] if more else None
    items = [{"message_id": MsgId(r.id), "conversation_id": ConvId(r.conversation_id), "part": r.value.get("part"),
              "filename": r.value.get("filename") or "attachment", "type": r.value.get("type") or "",
              "size": r.value.get("size") or 0, "subject": r.subject, "from_name": r.from_name,
              "from_email": r.from_email, "date": r.date.isoformat() if r.date else None} for r in rows[:limit]]
    next_cursor = base64.urlsafe_b64encode(json.dumps(
        [last.date.isoformat() if last.date else None, publicid.encode("message", last.id), last.ord]).encode()).decode() if last else None
    return {"items": items, "next_cursor": next_cursor, "next_offset": offset + limit if more and not cursor else None}
