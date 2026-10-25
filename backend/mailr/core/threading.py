"""mailr's conversation overlay.

Order of evidence when a message arrives:
1. Header links: its In-Reply-To/References name a known message, or a known message
   names it (children that arrived first). Several matching conversations are merged,
   unless a conversation is pinned by a manual split.
2. A provider thread id (Gmail X-GM-THRID, M365 conversationId) shared with a known message.
3. Fallback for messages without usable headers: same normalised subject, overlapping
   participants, within 45 days.
4. Otherwise a new conversation.

Automated mail (lists, bulk senders, no-reply addresses, auto-submitted mail; the same header
rules as intel/features.is_bulk) threads only by its reply headers. Without In-Reply-To or
References it skips steps 2 and 3, and step 3 never attaches a person's message to a
conversation made only of such mail: alerts with a recurring subject ("8 New Jobs") are separate
emails, even when the provider grouped them.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.events import record_change
from mailr.intel.features import is_automated
from mailr.models import Conversation, Message

FALLBACK_WINDOW = timedelta(days=45)
GENERIC_SUBJECTS = {"", "hi", "hello", "hey", "question", "thanks", "thank you", "meeting", "update",
                    "(no subject)", "no subject", "test", "fyi", "info", "request"}


def _participants(m: Message) -> set[str]:
    s = {m.from_email}
    s.update(a["email"] for a in (m.to or []) + (m.cc or []))
    s.discard("")
    return s


# The columns the automated-mail check and reply_ids read (a light row instead of the message).
_AUTO_COLS = (Message.id, Message.sent_by_me, Message.list_id, Message.list_unsubscribe, Message.from_email,
              Message.in_reply_to, Message.references, Message.draft)


def reply_ids(m) -> list[str]:
    return [x for x in ([m.in_reply_to] + list(m.references or [])) if x]


async def _all_automated_unlinked(db: AsyncSession, conv_id: int) -> bool:
    """True when every message of the conversation is automated mail without reply headers
    (so it was put together only by subject or provider thread id)."""
    rows = (await db.execute(select(*_AUTO_COLS).where(
        Message.conversation_id == conv_id, Message.deleted.is_(False)).limit(200))).all()
    return bool(rows) and all(is_automated(x) and not reply_ids(x) and not x.draft for x in rows)


async def assign(db: AsyncSession, m: Message, *, refresh_now: bool = True,
                 headers: dict[str, str] | None = None) -> int:
    """Attach message m (already flushed, has id) to a conversation and return its id.

    With refresh_now=False the caller refreshes the conversation itself (ingest does, once,
    after rules and labels are final). `headers` (lower-case names, features.header_map) adds
    the header-only bulk signals (Precedence, Auto-Submitted, ESP headers) to the automated-mail
    check; without them it uses the fields stored on the message."""
    uid = m.user_id
    linked_ids = reply_ids(m)
    # Automated mail without reply headers is never grouped by anything but headers.
    loose_automated = not linked_ids and is_automated(m, headers)
    candidates: set[int] = set()

    if linked_ids:
        rows = await db.execute(
            select(Message.conversation_id).where(
                Message.user_id == uid,
                Message.message_id_hdr.in_(linked_ids),
                Message.conversation_id.is_not(None),
                Message.id != m.id,
            )
        )
        candidates.update(r[0] for r in rows)

    if m.message_id_hdr:
        # Two index-backed lookups (btree on (user_id, in_reply_to), GIN on references);
        # an OR or `= ANY(array)` would force a scan of every message of the user.
        rows = await db.execute(
            text(
                "SELECT conversation_id FROM messages WHERE user_id=:u AND in_reply_to=:mid "
                "AND id<>:id AND conversation_id IS NOT NULL "
                "UNION "
                "SELECT conversation_id FROM messages WHERE \"references\" @> ARRAY[CAST(:mid AS varchar)] "
                "AND user_id=:u AND id<>:id AND conversation_id IS NOT NULL"
            ),
            {"u": uid, "id": m.id, "mid": m.message_id_hdr},
        )
        candidates.update(r[0] for r in rows)

    thrid = (m.upstream or {}).get("thread_key")
    if not candidates and thrid and not loose_automated:
        rows = await db.execute(
            text(
                "SELECT DISTINCT conversation_id FROM messages WHERE user_id=:u AND id<>:id "
                "AND conversation_id IS NOT NULL AND upstream->>'thread_key' = :t"
            ),
            {"u": uid, "id": m.id, "t": thrid},
        )
        candidates.update(r[0] for r in rows)

    if (not candidates and not linked_ids and not loose_automated and m.norm_subject not in GENERIC_SUBJECTS
            and m.date):
        rows = (
            await db.execute(
                select(Conversation).where(
                    Conversation.user_id == uid,
                    Conversation.norm_subject == m.norm_subject,
                    Conversation.last_date >= m.date - FALLBACK_WINDOW,
                    # Anchor the start too, so recurring automated subjects ("Your order has
                    # shipped") do not chain into one ever-growing conversation.
                    Conversation.first_date >= m.date - FALLBACK_WINDOW,
                    Conversation.first_date <= m.date + FALLBACK_WINDOW,
                )
                .order_by(Conversation.last_date.desc())
                .limit(5)
            )
        ).scalars()
        # The user's own addresses (identities, and wherever mail was delivered, e.g. a
        # forwarding mailbox) are in every conversation, so they never count as a shared
        # correspondent: two strangers writing to you with the same subject are not a thread.
        from mailr.core.ingest import my_addresses
        from mailr.models import Label, User

        user = await db.get(User, uid)
        self_addrs = set(await my_addresses(db, user)) | {a.lower() for a in (m.delivered_to or [])}
        hidden = set((await db.execute(select(Label.id).where(
            Label.user_id == uid, Label.role.in_(("junk", "trash"))))).scalars())
        mine = _participants(m) - self_addrs
        for conv in rows:
            if conv.label_ids and set(conv.label_ids) <= hidden:
                continue  # never attach by subject to a conversation that is all Spam/Trash
            if await _all_automated_unlinked(db, conv.id):
                continue  # nor to a run of alerts or newsletters
            others = {p["email"] for p in conv.participants or []}
            # Recipients of the conversation's messages too.
            rec = await db.execute(
                text(
                    "SELECT DISTINCT e FROM messages, LATERAL (SELECT from_email AS e UNION ALL "
                    "SELECT x->>'email' FROM jsonb_array_elements(\"to\" || cc) x) s "
                    "WHERE conversation_id=:c"
                ),
                {"c": conv.id},
            )
            others.update(r[0] for r in rec if r[0])
            delivered = await db.execute(text(
                "SELECT DISTINCT lower(d) FROM messages, unnest(delivered_to) d WHERE conversation_id=:c"), {"c": conv.id})
            others -= self_addrs | {r[0] for r in delivered}
            if len(mine & others) >= 2 or (mine & others and len(mine) <= 2):
                candidates.add(conv.id)
                break

    if not candidates:
        conv = Conversation(user_id=uid, subject=m.subject, norm_subject=m.norm_subject)
        db.add(conv)
        await db.flush()
        target = conv.id
    else:
        convs = (
            await db.execute(select(Conversation).where(Conversation.id.in_(candidates)))
        ).scalars().all()
        pinned = [c for c in convs if c.pinned]
        target_conv = pinned[0] if pinned else min(convs, key=lambda c: c.id)
        target = target_conv.id
        merge = [c.id for c in convs if c.id != target and not c.pinned]
        if merge:
            await merge_into(db, uid, target, merge)

    m.conversation_id = target
    await db.flush()
    if refresh_now:
        await refresh(db, uid, [target])
    return target


async def merge_into(db: AsyncSession, uid: int, target: int, sources: list[int]) -> None:
    await db.execute(
        update(Message)
        .where(Message.user_id == uid, Message.conversation_id.in_(sources))
        .values(conversation_id=target)
    )
    # Snooze / mute / a follow-up reminder survive the merge if any source had them.
    await db.execute(
        text(
            "UPDATE conversations t SET muted = t.muted OR s.muted, "
            "snoozed_until = GREATEST(t.snoozed_until, s.snoozed_until), "
            "follow_up_at = COALESCE(t.follow_up_at, s.follow_up_at), "
            "follow_up_since = COALESCE(t.follow_up_since, s.follow_up_since), "
            "nudged_at = COALESCE(t.nudged_at, s.nudged_at) "
            "FROM (SELECT bool_or(muted) muted, max(snoozed_until) snoozed_until, max(follow_up_at) follow_up_at, "
            "      max(follow_up_since) follow_up_since, max(nudged_at) nudged_at "
            "      FROM conversations WHERE id = ANY(:src)) s WHERE t.id=:t"
        ),
        {"src": sources, "t": target},
    )
    await db.execute(text("DELETE FROM conversations WHERE id = ANY(:src)"), {"src": sources})
    await record_change(db, uid, "conversation", sources, "delete")


async def split(db: AsyncSession, uid: int, message_ids: list[int], *, pin: bool = True) -> int:
    """Move messages into a new conversation, pinned for a manual correction so the threader
    will not merge it back (pin=False for repairs of the threader's own mistakes)."""
    msgs = (
        await db.execute(select(Message).where(Message.user_id == uid, Message.id.in_(message_ids)))
    ).scalars().all()
    if not msgs:
        raise ValueError("no messages")
    old = {m.conversation_id for m in msgs}
    conv = Conversation(user_id=uid, subject=msgs[0].subject, norm_subject=msgs[0].norm_subject,
                        pinned=pin)
    db.add(conv)
    await db.flush()
    for m in msgs:
        m.conversation_id = conv.id
    await db.flush()
    await refresh(db, uid, [conv.id, *[o for o in old if o]])
    return conv.id


async def merge(db: AsyncSession, uid: int, conversation_ids: list[int]) -> int:
    convs = (
        await db.execute(
            select(Conversation.id).where(
                Conversation.user_id == uid, Conversation.id.in_(conversation_ids)
            )
        )
    ).scalars().all()
    if len(convs) < 2:
        raise ValueError("need two conversations")
    target = min(convs)
    await merge_into(db, uid, target, [c for c in convs if c != target])
    await db.execute(update(Conversation).where(Conversation.id == target).values(pinned=True))
    await refresh(db, uid, [target])
    return target


# "live" = not in Trash or Spam (:hidden holds those label ids). Like Gmail, a conversation's
# summary (unread count, snippet, date, size) comes from its live messages when it has any;
# messages the user deleted only count when everything in the conversation is deleted.
_STATS_SQL = """
SELECT conversation_id AS cid,
  count(*) AS n,
  count(*) FILTER (WHERE NOT draft) AS n_visible,
  count(*) FILTER (WHERE NOT draft AND NOT (label_ids && CAST(:hidden AS int[]))) AS n_live,
  count(*) FILTER (WHERE NOT seen AND NOT draft) AS unread,
  count(*) FILTER (WHERE NOT seen AND NOT draft AND NOT (label_ids && CAST(:hidden AS int[]))) AS unread_live,
  count(*) FILTER (WHERE draft) AS drafts,
  bool_or(has_attachments) AS has_attachments,
  bool_or(flagged) AS starred,
  bool_or(important) AS important,
  max(importance_score) AS importance_score,
  min(date) AS first_date,
  max(coalesce(date, received_at)) FILTER (WHERE NOT draft) AS last_visible,
  max(coalesce(date, received_at)) FILTER (WHERE NOT draft AND NOT (label_ids && CAST(:hidden AS int[]))) AS last_live,
  max(coalesce(date, received_at)) AS last_any,
  (array_agg(subject ORDER BY date ASC NULLS LAST, id) FILTER (WHERE subject <> ''))[1] AS subject,
  (array_agg(norm_subject ORDER BY date ASC NULLS LAST, id) FILTER (WHERE norm_subject <> ''))[1] AS norm_subject,
  (array_agg(snippet ORDER BY date DESC NULLS LAST, id DESC)
     FILTER (WHERE NOT draft AND NOT (label_ids && CAST(:hidden AS int[]))))[1] AS snippet_live,
  (array_agg(snippet ORDER BY date DESC NULLS LAST, id DESC) FILTER (WHERE NOT draft))[1] AS snippet_visible,
  (array_agg(snippet ORDER BY date DESC NULLS LAST, id DESC))[1] AS snippet_any,
  (array_agg(category ORDER BY date DESC NULLS LAST, id DESC) FILTER (WHERE category IS NOT NULL AND NOT draft))[1] AS category,
  (array_agg(primary_action ORDER BY date DESC NULLS LAST, id DESC)
     FILTER (WHERE NOT draft AND NOT (label_ids && CAST(:hidden AS int[]))))[1] AS primary_action
FROM messages
WHERE conversation_id = ANY(:ids) AND NOT deleted {extra}
GROUP BY conversation_id
"""
# A conversation's labels: every label of its messages, except that a message in Spam or Trash
# brings only its folder labels (Spam, Trash, Sent...), not its user labels. A label's list, its
# count and the inbox tabs built on it then leave spam out, as Gmail does (8 Oct 2026: a promotion
# in Spam that carried the "GitHub" label showed in the GitHub tab). Searches read the messages,
# so `label:github in:spam` still finds it.
_LABELS_SQL = """
SELECT m.conversation_id AS cid, array_agg(DISTINCT l ORDER BY l) AS labels
FROM messages m, unnest(m.label_ids) l
WHERE m.conversation_id = ANY(:ids) AND NOT m.deleted {extra}
  AND (NOT (m.label_ids && CAST(:hidden AS int[])) OR l = ANY(CAST(:roles AS int[])))
GROUP BY m.conversation_id
"""
_PEOPLE_SQL = """
SELECT DISTINCT ON (conversation_id, from_email) conversation_id AS cid, from_email, from_name, sent_by_me,
       bool_or(origin IS NOT NULL) OVER (PARTITION BY conversation_id, from_email) AS shared,
       coalesce(date, received_at) AS d, id, draft
FROM messages
WHERE conversation_id = ANY(:ids) AND NOT deleted AND from_email <> '' {extra}
ORDER BY conversation_id, from_email, draft, coalesce(date, received_at) ASC NULLS LAST, id
"""


# The messages of conversations in which no one but the user wrote (a sent email without a reply, a
# note to self): only these need their recipients. A non-draft message from someone else leaves the
# conversation out.
_SOLO_SQL = """
SELECT conversation_id AS cid, id, draft, sent_by_me, coalesce(date, received_at) AS d, "to", cc, bcc
FROM messages
WHERE conversation_id = ANY(:ids) AND NOT deleted {extra}
  AND conversation_id NOT IN (SELECT conversation_id FROM messages WHERE conversation_id = ANY(:ids)
                              AND NOT deleted AND NOT draft AND NOT sent_by_me {extra})
"""
# The user's own addresses per conversation: the account address and every identity (aliases
# included), as core.ingest.my_addresses has them.
_MINE_SQL = """
SELECT c.id AS cid, lower(u.email) AS a FROM conversations c JOIN users u ON u.id = c.user_id WHERE c.id = ANY(:ids)
UNION
SELECT c.id, lower(i.email) FROM conversations c JOIN identities i ON i.user_id = c.user_id WHERE c.id = ANY(:ids)
"""
MAX_RECIPIENTS = 12


def solo_summary(rows: list, mine: set[str]) -> tuple[bool, list[dict[str, Any]]]:
    """`to_self` and `recipients` of a conversation from its messages (`rows`: draft, sent_by_me,
    d, id, to, cc, bcc), as the list rows read them. The messages that count are the non-draft ones,
    or the drafts when there is nothing else (as for `participants`). When the user wrote all of them,
    `recipients` holds the people they went to, in order of first appearance, without the user's own
    addresses, at most MAX_RECIPIENTS ("To: Dana Whitfield" in a Sent list). `to_self` is true only
    when every such message went to the user's own addresses and nowhere else (a note to self)."""
    rows = [r for r in rows if not r["draft"]] or list(rows)
    if not rows or not all(r["sent_by_me"] for r in rows):
        return False, []
    rows.sort(key=lambda r: (r["d"] is None, r["d"] or 0, r["id"]))
    out: dict[str, dict[str, Any]] = {}
    to_self = True
    for r in rows:
        addrs = [a for a in (r["to"] or []) + (r["cc"] or []) + (r["bcc"] or []) if (a or {}).get("email")]
        if not addrs:
            to_self = False  # nowhere known (a copy without its headers): not provably to self
        for a in addrs:
            e = a["email"].strip().lower()
            if e in mine:
                continue
            to_self = False
            if e not in out and len(out) < MAX_RECIPIENTS:
                out[e] = {"email": a["email"], "name": a.get("name") or None}
    return to_self, list(out.values())


async def aggregate(db: AsyncSession, conv_ids: list[int],
                    only_message_ids: list[int] | None = None) -> dict[int, dict]:
    """Conversation summary fields computed in SQL (three set-based queries for any number
    of conversations). `only_message_ids` restricts the inputs (used to build the view a
    restricted API token may see)."""
    conv_ids = [c for c in dict.fromkeys(conv_ids) if c]
    if not conv_ids:
        return {}
    hidden, roles = (await db.execute(text(
        "SELECT coalesce(array_agg(id) FILTER (WHERE role IN ('trash', 'junk')), '{}'), coalesce(array_agg(id), '{}') "
        "FROM labels WHERE role IS NOT NULL AND user_id IN "
        "(SELECT DISTINCT user_id FROM conversations WHERE id = ANY(:ids))"), {"ids": conv_ids})).one()
    extra, params = "", {"ids": conv_ids, "hidden": list(hidden or []), "roles": list(roles or [])}
    if only_message_ids is not None:
        extra, params["mids"] = "AND id = ANY(:mids)", list(only_message_ids)
    out: dict[int, dict] = {}
    for r in (await db.execute(text(_STATS_SQL.format(extra=extra)), params)).mappings():
        visible = r["n_visible"] > 0
        live = r["n_live"] > 0
        out[r["cid"]] = {
            "subject": r["subject"] or "", "norm_subject": r["norm_subject"] or "",
            "snippet": (r["snippet_live"] if live else r["snippet_visible"] if visible else r["snippet_any"]) or "",
            "first_date": r["first_date"],
            "last_date": r["last_live"] if live else r["last_visible"] if visible else r["last_any"],
            "message_count": r["n_live"] if live else r["n_visible"] if visible else r["n"],
            "unread_count": r["unread_live"] if live else r["unread"], "draft_count": r["drafts"],
            "has_attachments": bool(r["has_attachments"]), "starred": bool(r["starred"]),
            "important": bool(r["important"]), "importance_score": r["importance_score"],
            "category": r["category"], "label_ids": [], "participants": [],
            "primary_action": r["primary_action"],
            "_visible": visible,
        }
    lextra = extra.replace("id =", "m.id =")
    for r in (await db.execute(text(_LABELS_SQL.format(extra=lextra)), params)).mappings():
        if r["cid"] in out:
            out[r["cid"]]["label_ids"] = list(r["labels"] or [])
    people: dict[int, list] = {}
    for r in (await db.execute(text(_PEOPLE_SQL.format(extra=extra)), params)).mappings():
        agg = out.get(r["cid"])
        if agg is None or (agg["_visible"] and r["draft"]):
            continue
        people.setdefault(r["cid"], []).append(r)
    for cid, rows in people.items():
        rows.sort(key=lambda x: (x["d"] is None, x["d"] or 0, x["id"]))
        # "shared": mail from this address here was shared in by another app (Message.origin); the
        # assistant does not call it "me". The key is there only when true.
        out[cid]["participants"] = [{"email": x["from_email"], "name": x["from_name"], "me": x["sent_by_me"],
                                     **({"shared": True} if x["shared"] else {})}
                                    for x in rows[:12]]
    solo: dict[int, list] = {}
    for r in (await db.execute(text(_SOLO_SQL.format(extra=extra)), params)).mappings():
        if r["cid"] in out:
            solo.setdefault(r["cid"], []).append(r)
    mine: dict[int, set[str]] = {}
    if solo:
        for r in (await db.execute(text(_MINE_SQL), {"ids": list(solo)})).mappings():
            if r["a"]:
                mine.setdefault(r["cid"], set()).add(r["a"])
    for cid, v in out.items():
        v["to_self"], v["recipients"] = solo_summary(solo.get(cid, []), mine.get(cid, set()))
        v.pop("_visible", None)
    return out


async def refresh(db: AsyncSession, uid: int, conv_ids: list[int]) -> None:
    """Recompute denormalised conversation aggregates from their messages."""
    conv_ids = [c for c in dict.fromkeys(conv_ids) if c]
    if not conv_ids:
        return
    await db.flush()
    aggs = await aggregate(db, conv_ids)
    from mailr.core.mailaccounts import account_ids_for

    accts = await account_ids_for(db, uid, conv_ids)  # the account index (core/mailaccounts.py)
    convs = (await db.execute(select(Conversation).where(Conversation.id.in_(conv_ids)))).scalars().all()
    deleted = []
    for conv in convs:
        a = aggs.get(conv.id)
        if a is None:
            await db.delete(conv)
            deleted.append(conv.id)
            continue
        conv.subject = a["subject"] or conv.subject
        conv.norm_subject = a["norm_subject"]
        conv.snippet = a["snippet"]
        conv.first_date = a["first_date"]
        conv.last_date = a["last_date"]
        conv.message_count = a["message_count"]
        conv.unread_count = a["unread_count"]
        conv.draft_count = a["draft_count"]
        conv.has_attachments = a["has_attachments"]
        conv.starred = a["starred"]
        conv.important = a["important"]
        conv.importance_score = a["importance_score"]
        conv.category = a["category"] or conv.category
        conv.label_ids = a["label_ids"]
        conv.account_ids = accts.get(conv.id, [])
        conv.participants = a["participants"]
        conv.to_self = a["to_self"]
        conv.recipients = a["recipients"]
        conv.primary_action = a.get("primary_action")
    await db.flush()
    if deleted:
        await record_change(db, uid, "conversation", deleted, "delete")
    live = [c for c in conv_ids if c not in deleted]
    if live:
        await record_change(db, uid, "conversation", live)


async def rethread_all(db: AsyncSession, uid: int) -> int:
    """Rebuild the overlay from scratch for a user (after an import). Keeps pinned splits."""
    from mailr.core import tally

    tally.touch(db, (await db.execute(text("SELECT id FROM conversations WHERE user_id=:u AND NOT pinned"),
                                      {"u": uid})).scalars())
    await db.execute(
        text(
            "UPDATE messages SET conversation_id=NULL WHERE user_id=:u AND conversation_id IN "
            "(SELECT id FROM conversations WHERE user_id=:u AND NOT pinned)"
        ),
        {"u": uid},
    )
    await db.execute(
        text("DELETE FROM conversations WHERE user_id=:u AND NOT pinned"), {"u": uid}
    )
    ids = (
        await db.execute(
            select(Message.id).where(Message.user_id == uid, Message.conversation_id.is_(None))
            .order_by(Message.date.asc().nulls_first(), Message.id)
        )
    ).scalars().all()
    for mid in ids:
        m = await db.get(Message, mid)
        await assign(db, m)
    return len(ids)


async def count(db: AsyncSession, uid: int) -> int:
    return (
        await db.execute(select(func.count()).select_from(Conversation).where(Conversation.user_id == uid))
    ).scalar_one()
