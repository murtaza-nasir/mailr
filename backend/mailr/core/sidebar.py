"""Every sidebar number, from the conditions its list uses (GET /counts, the SSE `counts` event).

A sidebar number is the length of a list: Drafts counts what the Drafts view lists, so a draft in
Trash counts in neither (3 Oct 2026: the sidebar said Drafts 4 over an empty Drafts list, all four
in Trash). Each view's conditions come from api/mail_read.conversation_conditions, the function
the list endpoint uses. The system views are one pair of FILTER aggregates each in a single query
over the user's conversations; the user labels, and each account's folders and labels, come from
grouped queries over the same conversations (by label set, by account set: the account index,
core/mailaccounts.py). Three queries in all, about 90 ms at 100,000 conversations with five
accounts and 30 labels (tests/test_scale_counts.py). The result is cached per caller until the
change_log cursor moves (or the settings or the accounts' addresses change), at most COUNTS_TTL
seconds, and computed once per caller at a time: requests that arrive meanwhile wait for it.

Shape: {"cursor", "views": {view: {"unread", "total"}}, "inbox_count": {"count", "mode",
"layout", "tab"}, "waiting", "approvals", "failed", "accounts"}. `accounts` lists the mail accounts
(core/mailaccounts.py); with "Show each account separately" on, `views` also holds each account's
folders as "acct:<id>:<view>", and each label that holds an account's mail as
"acct:<id>:label:<label id>" (labels with none are left out). `unread` is the conversations of the list
that have unread mail, as the list rows show them (for a restricted token, unread mail it may see).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections import OrderedDict
from typing import Any

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.visibility import is_restricted, visible_message_filter
from mailr.models import Conversation, Label, Message

# The views the sidebar draws. User labels are added as "label:<id>".
SYSTEM_VIEWS = ("inbox", "starred", "snoozed", "sent", "drafts", "important", "spam", "trash",
                "archive", "category:social", "category:updates", "category:forums", "category:promotions",
                "category:purchases")
# Scheduled is not a conversation list: the web and Android list GET /outbox?scheduled=true (mail
# queued beyond the undo window), so its number counts those outbox rows the same way.
# Sent shows how many conversations wait for a reply; it is the `is:follow_up` search.
WAITING_QUERY = "is:follow_up"
# Which view a role label's count is (GET /labels keeps its per-label numbers).
ROLE_VIEW = {"inbox": "inbox", "sent": "sent", "drafts": "drafts", "junk": "spam", "trash": "trash",
             "archive": "archive"}

# The numbers an account's own folders show: Inbox (unread), Drafts and Spam, and Scheduled from
# the outbox. Its Sent and Trash show none, as in the unified section.
ACCOUNT_COUNTED = ("inbox", "drafts", "spam")

COUNTS_TTL = 60.0
_CACHE: OrderedDict[tuple, tuple[float, dict[str, Any]]] = OrderedDict()
_CACHE_MAX = 1000


# The per-account numbers (on by default since they come from the account index; "0" turns them off).
ACCOUNT_COUNTS = os.environ.get("MAILR_ACCOUNT_COUNTS", "1") == "1"
_LOCKS: dict[tuple, asyncio.Lock] = {}


def label_view(lb: Label) -> str:
    return ROLE_VIEW.get(lb.role or "") or f"label:{lb.id}"


async def change_cursor(db: AsyncSession, uid: int) -> int:
    return (await db.execute(text("SELECT coalesce(max(id), 0) FROM change_log WHERE user_id=:u"),
                             {"u": uid})).scalar_one()


def _unread(p, uid: int):
    if is_restricted(p):
        # The list recomputes a restricted token's rows from the messages it may see
        # (mail_read.token_views), so its unread is unread mail among those.
        return Conversation.id.in_(select(Message.conversation_id).where(
            Message.user_id == uid, Message.seen.is_(False), Message.draft.is_(False),
            Message.deleted.is_(False), visible_message_filter(p)))
    return Conversation.unread_count > 0


async def compute(db: AsyncSession, p, settings: dict[str, Any]) -> dict[str, Any]:
    from mailr.api.layout_routes import first_tab_unread, inbox_count_mode
    from mailr.api.mail_read import _labels, conversation_conditions
    from mailr.core import layouts, mailaccounts

    uid = p.user_id
    labels = await _labels(db, uid)
    mailaccounts.memo_accounts(db)
    accounts = await mailaccounts.mail_accounts(db, p)
    # Each account's own folders (Settings → Mail → Inbox → "Show each account separately") are
    # counted while they are shown, from the account index (Conversation.account_ids). A restricted
    # token gets no per-account numbers (what it may see is per message), and neither does anyone
    # while the index is rebuilt after an account change (core/mailaccounts.py; a rebuild in the
    # request commits). 8 Oct 2026: computed with a sub-query per conversation and account, these
    # numbers took /counts to two minutes on production.
    separate = ACCOUNT_COUNTS and bool(settings.get("account_folders")) and len(accounts) > 1 \
        and not is_restricted(p) and await mailaccounts.ensure_index(db, uid)
    # These are short scans with many aggregates; compiling them (JIT) cost more than running them
    # (about 30 ms per query at 100k conversations, 8 Oct 2026). For this transaction only.
    await db.execute(text("SET LOCAL jit = off"))
    # The system views and the Sent "waiting" number: one pair of FILTER aggregates each, in one
    # query over the conversations, with the conditions of their lists.
    specs: dict[str, list[Any] | None] = {}
    for v in SYSTEM_VIEWS:
        specs[v] = await conversation_conditions(db, p, v, labels=labels)
    specs["__waiting"] = await conversation_conditions(db, p, "all", WAITING_QUERY, labels=labels)
    unread = _unread(p, uid)
    cols = []
    names = []
    for v, conds in specs.items():
        if conds is None:
            continue
        where = and_(*conds)
        k = len(names)
        names.append(v)
        cols.append(func.count().filter(where).label(f"t{k}"))
        # Unread first: most conversations are read, so the view's own conditions run on few rows.
        cols.append(func.count().filter(and_(unread, where)).label(f"u{k}"))
    row = (await db.execute(select(*cols).select_from(Conversation).where(Conversation.user_id == uid))
           ).mappings().one() if cols else {}
    out_views: dict[str, dict[str, int]] = {v: {"unread": 0, "total": 0} for v in specs}
    for k, v in enumerate(names):
        out_views[v] = {"unread": row[f"u{k}"], "total": row[f"t{k}"]}
    waiting = out_views.pop("__waiting")["total"]
    # User labels, and the accounts' folders and labels. A label's list is the "all" list narrowed
    # to the label and the labels nested under it, and an account's list adds only its account, so
    # the conversations of the "all" list grouped by their label set (and their account set) give
    # every label's number and every account's: each group adds to the views it belongs to.
    for lb in labels:
        if not lb.role:
            out_views[f"label:{lb.id}"] = {"unread": 0, "total": 0}
    base = await conversation_conditions(db, p, "all", labels=labels)
    if base is not None:
        out_views.update(await grouped_label_numbers(db, labels, [a.id for a in accounts] if separate else None,
                                             base, unread))
    if separate:
        out_views.update(await account_folder_numbers(db, p, [a.id for a in accounts], labels, unread))

    mode = inbox_count_mode(settings)
    lay = layouts.find(settings, layouts.active_id(settings)) or layouts.find(settings, "default")
    if mode == "first_tab" and lay and lay["tabs"]:
        inbox_count = {"count": await first_tab_unread(db, p, lay), "mode": mode, "layout": lay["id"],
                       "tab": lay["tabs"][0]["id"]}
    else:
        inbox_count = {"count": out_views["inbox"]["unread"], "mode": "all",
                       "layout": lay["id"] if lay else None, "tab": None}
    mine = "AND token_id = :t" if p.token is not None else ""
    outbox = (await db.execute(text(
        "SELECT count(*) FILTER (WHERE status='awaiting_approval') AS approvals, "
        "count(*) FILTER (WHERE status IN ('failed', 'uncertain') AND created_at > now() - interval '7 days') AS failed, "
        f"count(*) FILTER (WHERE status='queued' AND send_at > now() + interval '1 minute' {mine}) AS scheduled "
        "FROM outbox WHERE user_id=:u"), {"u": uid, **({"t": p.token.id} if p.token is not None else {})})).one()
    out_views["scheduled"] = {"unread": 0, "total": outbox.scheduled}
    if separate:
        # Scheduled per account: the queued sends of each account's send-as identities, grouped.
        rows = (await db.execute(text(
            "SELECT i.connector_id AS a, count(*) AS n FROM outbox o JOIN identities i ON i.id = o.identity_id "
            "WHERE o.user_id = :u AND o.status = 'queued' AND o.send_at > now() + interval '1 minute' "
            f"{mine.replace('token_id', 'o.token_id')} GROUP BY i.connector_id"),
            {"u": uid, **({"t": p.token.id} if p.token is not None else {})})).all()
        per = {r.a: r.n for r in rows}
        for a in accounts:
            out_views[f"acct:{a.id}:scheduled"] = {"unread": 0, "total": per.get(a.id, 0)}
    # The Screener counts senders, not conversations: senders waiting, and those with unread mail
    # (core/screener.counts, from the same held_conditions as its list).
    from mailr.core import screener

    out_views["screener"] = await screener.counts(db, p)
    return {"views": out_views, "inbox_count": inbox_count, "waiting": waiting,
            "approvals": outbox.approvals, "failed": outbox.failed,
            # The mail accounts the caller may see (id, address, colour…): the navigation's heading
            # and folders and the list's account dots. Their folders' numbers are in `views`.
            "accounts": [a.out() for a in accounts]}


def _view_map(labels) -> dict[int, list[int]]:
    """{label id: the user-label views it counts in}: itself and every label above it."""
    from mailr.core import mailaccounts

    out: dict[int, list[int]] = {}
    for leaf, lid in mailaccounts.label_view_ids(labels):
        out.setdefault(leaf, []).append(lid)
    return out


async def grouped_label_numbers(db: AsyncSession, labels, account_ids: list[int] | None, base: list[Any], unread
                        ) -> dict[str, dict[str, int]]:
    """"label:<id>" for every user label that holds mail and, with `account_ids`,
    "acct:<id>:label:<label id>" for every label that holds an account's mail: one query over the
    "all" list's conversations (`base`), grouped by label set (and account set)."""
    views = _view_map(labels)
    if not views:
        return {}
    cols = [Conversation.label_ids] + ([Conversation.account_ids] if account_ids is not None else [])
    stmt = (select(*cols, func.count().label("total"), func.count().filter(unread).label("unread"))
            .select_from(Conversation).where(*base).group_by(*cols))
    wanted = set(account_ids or [])
    out: dict[str, dict[str, int]] = {}

    def add(key: str, total: int, unread_n: int) -> None:
        cur = out.setdefault(key, {"unread": 0, "total": 0})
        cur["total"] += total
        cur["unread"] += unread_n

    for r in (await db.execute(stmt)).all():
        lids = {v for leaf in (r.label_ids or []) for v in views.get(leaf, ())}
        accts = [a for a in (r.account_ids or []) if a in wanted] if account_ids is not None else []
        for lid in lids:
            add(f"label:{lid}", r.total, r.unread)
            for a in accts:
                add(f"acct:{a}:label:{lid}", r.total, r.unread)
    return out


async def account_folder_numbers(db: AsyncSession, p, account_ids: list[int], labels, unread
                                 ) -> dict[str, dict[str, int]]:
    """"acct:<id>:<view>" for ACCOUNT_COUNTED: the unified view's conditions (an account's list adds
    only its account), counted per account set, each set adding to its accounts."""
    from mailr.api.mail_read import conversation_conditions

    out: dict[str, dict[str, int]] = {f"acct:{a}:{v}": {"unread": 0, "total": 0}
                                      for a in account_ids for v in ACCOUNT_COUNTED}
    cols = []
    names = []
    for v in ACCOUNT_COUNTED:
        conds = await conversation_conditions(db, p, v, labels=labels)
        if conds is None:
            continue
        where = and_(*conds)
        k = len(names)
        names.append(v)
        cols.append(func.count().filter(where).label(f"t{k}"))
        cols.append(func.count().filter(and_(unread, where)).label(f"u{k}"))
    if not cols:
        return out
    wanted = set(account_ids)
    rows = (await db.execute(select(Conversation.account_ids, *cols).where(Conversation.user_id == p.user_id)
                             .group_by(Conversation.account_ids))).mappings()
    for r in rows:
        for a in r["account_ids"] or []:
            if a not in wanted:
                continue
            for k, v in enumerate(names):
                cur = out[f"acct:{a}:{v}"]
                cur["total"] += r[f"t{k}"]
                cur["unread"] += r[f"u{k}"]
    return out


async def sidebar_counts(db: AsyncSession, p, *, fresh: bool = False) -> dict[str, Any]:
    """All sidebar numbers for the caller, cached until the user's next change. One computation per
    key at a time: every tab and phone asking at once waits for it and shares the answer (they used
    to start one each, and on 8 Oct 2026 thirty slow copies jammed the database)."""
    from mailr.api.mail_read import _settings

    settings = await _settings(db, p)
    cursor = await change_cursor(db, p.user_id)
    shash = hashlib.sha256(json.dumps(settings, sort_keys=True, default=str).encode()).hexdigest()[:16]
    # The accounts and their addresses too: a new send-as address changes which conversations an
    # account holds without a change_log entry (core/mailaccounts.py, the account index).
    from mailr.core import mailaccounts

    astamp = mailaccounts.index_stamp(await mailaccounts.account_addresses(db, p.user_id))
    key = (p.user_id, p.token.id if p.token is not None else None, shash, astamp)
    lock = _LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _CACHE.get(key)
        now = time.monotonic()
        if hit and hit[1]["cursor"] == cursor and (not fresh or now - hit[0] < 1.0) and now - hit[0] < COUNTS_TTL:
            _CACHE.move_to_end(key)
            return hit[1]
        out = {"cursor": cursor, **await compute(db, p, settings)}
        _CACHE[key] = (now, out)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
        if len(_LOCKS) > _CACHE_MAX * 2:
            for k in [k for k, lk in _LOCKS.items() if not lk.locked() and k not in _CACHE]:
                _LOCKS.pop(k, None)
        return out


def forget_user(uid: int) -> None:
    """Drop a user's cached numbers (a change that the change_log cursor does not show, such as an
    account's colour)."""
    for k in [k for k in _CACHE if k[0] == uid]:
        _CACHE.pop(k, None)


def label_numbers(counts: dict[str, Any], labels: list[Label]) -> dict[int, tuple[int, int]]:
    """{label id: (unread, total)} for GET /labels, from the views those labels open."""
    views = counts["views"]
    out = {}
    for lb in labels:
        v = views.get(label_view(lb))
        if v is not None:
            out[lb.id] = (v["unread"], v["total"])
    return out


def legacy_me_counts(counts: dict[str, Any]) -> dict[str, int]:
    """The `counts` object of GET /me (web before this change, Android 0.5.x), now from the views."""
    v = counts["views"]
    return {"scheduled": v["scheduled"]["total"], "approvals": counts["approvals"], "failed": counts["failed"],
            "snoozed": v["snoozed"]["total"], "drafts": v["drafts"]["total"], "waiting": counts["waiting"]}
