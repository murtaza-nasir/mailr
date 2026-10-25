"""Repair for conversations glued together out of automated mail (`mailr rethread-automated`).

Before 3 Oct 2026 the subject fallback (and a shared provider thread id) could put alerts with a
recurring subject ("8 New Jobs - My Job Alert") into one conversation. The threader no longer
does (core/threading.py); this splits what it did before. A conversation qualifies when it is not
pinned, every live message in it is automated mail the user did not send (no drafts), and its
messages fall into more than one group by reply headers. The group holding the earliest message
stays; every other group moves to a conversation of its own (threading.split, unpinned), which
keeps each message's labels and flags and copies the conversation's mute and snooze.

Safe on a live database: one batch per transaction, each conversation locked and re-checked
before it changes, and a per-user advisory lock so two runs never work on the same user.
Running it again finds nothing to do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import threading
from mailr.core.events import record_change
from mailr.intel.features import NOREPLY, is_automated
from mailr.models import AuditLog, Conversation, Message

BATCH = 50

_COLS = (Message.id, Message.date, Message.subject, Message.message_id_hdr, Message.in_reply_to,
         Message.references, Message.upstream, Message.sent_by_me, Message.draft, Message.list_id,
         Message.list_unsubscribe, Message.from_email)

# Prefilter in SQL (Python re-checks every candidate with features.is_automated).
_CANDIDATES = """
SELECT m.conversation_id
FROM messages m JOIN conversations c ON c.id = m.conversation_id
WHERE m.user_id = :u AND NOT m.deleted AND NOT c.pinned AND m.conversation_id > :after
GROUP BY m.conversation_id
HAVING count(*) >= 2 AND bool_and(NOT m.sent_by_me AND NOT m.draft AND (
    coalesce(m.list_id, '') <> '' OR coalesce(m.list_unsubscribe, '') <> '' OR m.from_email ~* :noreply))
ORDER BY m.conversation_id
LIMIT :lim
"""


@dataclass
class Found:
    conversation_id: int
    subject: str
    groups: list[list[int]]  # message ids; groups[0] stays
    first: datetime | None
    last: datetime | None
    provider_thread: bool  # the groups share a provider thread id (Gmail grouped them too)


@dataclass
class Report:
    user_id: int
    found: list[Found] = field(default_factory=list)
    new_conversations: int = 0
    messages_moved: int = 0
    summaries_dropped: int = 0


def _groups(rows) -> list[list[int]]:
    """Messages connected by reply headers: a message is joined to the ids it names and to
    every message naming the same id; messages with reply headers also join by provider thread id
    (as threading.assign does). Groups are ordered by their earliest message."""
    parent: dict[tuple, tuple] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for r in rows:
        node = ("m", r.id)
        find(node)
        if r.message_id_hdr:
            union(node, ("h", r.message_id_hdr))
        linked = threading.reply_ids(r)
        for x in linked:
            union(node, ("h", x))
        key = (r.upstream or {}).get("thread_key")
        if linked and key:
            union(node, ("t", key))
    order = sorted(rows, key=lambda r: (r.date is None, r.date or datetime.min.replace(tzinfo=UTC), r.id))
    out: dict[tuple, list[int]] = {}
    for r in order:
        out.setdefault(find(("m", r.id)), []).append(r.id)
    return list(out.values())


def _plan(conv_id: int, rows) -> Found | None:
    if len(rows) < 2 or any(r.draft or not is_automated(r) for r in rows):
        return None
    groups = _groups(rows)
    if len(groups) < 2:
        return None
    by_id = {r.id: r for r in rows}
    keys = [{(by_id[i].upstream or {}).get("thread_key") for i in g} - {None} for g in groups]
    shared = any(keys[i] & keys[j] for i in range(len(keys)) for j in range(i + 1, len(keys)))
    dates = [r.date for r in rows if r.date]
    first = by_id[groups[0][0]]
    return Found(conv_id, first.subject or "", groups, min(dates) if dates else None,
                 max(dates) if dates else None, shared)


async def _rows(db: AsyncSession, conv_id: int):
    return (await db.execute(select(*_COLS).where(
        Message.conversation_id == conv_id, Message.deleted.is_(False)))).all()


async def find(db: AsyncSession, uid: int, *, after: int = 0, limit: int = BATCH) -> tuple[list[Found], int | None]:
    """One batch of qualifying conversations after conversation id `after`, and the cursor for
    the next batch (None at the end)."""
    ids = list((await db.execute(text(_CANDIDATES), {"u": uid, "after": after, "lim": limit,
                                                     "noreply": NOREPLY.pattern})).scalars())
    out = [f for cid in ids if (f := _plan(cid, await _rows(db, cid)))]
    return out, (ids[-1] if len(ids) == limit else None)


async def _apply(db: AsyncSession, uid: int, conv_id: int, report: Report) -> Found | None:
    conv = (await db.execute(select(Conversation).where(
        Conversation.id == conv_id, Conversation.user_id == uid).with_for_update())).scalar_one_or_none()
    if conv is None or conv.pinned:
        return None
    f = _plan(conv_id, await _rows(db, conv_id))  # re-checked under the lock
    if f is None:
        return None
    muted, snoozed = conv.muted, conv.snoozed_until
    moved: list[int] = []
    for group in f.groups[1:]:
        new_id = await threading.split(db, uid, group, pin=False)
        new = await db.get(Conversation, new_id)
        new.muted, new.snoozed_until = muted, snoozed
        moved += group
        report.new_conversations += 1
    await db.flush()
    await record_change(db, uid, "message", moved)
    report.messages_moved += len(moved)
    return f


async def run(uid: int, *, dry_run: bool) -> Report:
    """Find (and unless dry_run, split) every qualifying conversation of user `uid`."""
    from mailr.db import sessionmaker
    from mailr.intel.summarize import drop_cached

    report = Report(uid)
    after: int | None = 0
    while after is not None:
        async with sessionmaker()() as db:
            if dry_run:
                found, after = await find(db, uid, after=after)
                report.found += found
                continue
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                             {"k": f"rethread-automated:{uid}"})
            found, after = await find(db, uid, after=after)
            done = [f for f in found if await _apply(db, uid, f.conversation_id, report)]
            await db.commit()
        report.found += done
        report.summaries_dropped += drop_cached(uid, [f.conversation_id for f in done])
    if not dry_run and report.found:
        async with sessionmaker()() as db:
            db.add(AuditLog(user_id=uid, actor="system", action="threading.rethread_automated",
                            target=f"user {uid}", inputs={"dry_run": False},
                            result={"conversations": len(report.found),
                                    "new_conversations": report.new_conversations,
                                    "messages_moved": report.messages_moved,
                                    "summaries_dropped": report.summaries_dropped,
                                    "conversation_ids": [f.conversation_id for f in report.found][:60]}))
            await db.commit()
    return report


def describe(report: Report, *, dry_run: bool, sample: int = 10) -> str:
    n = len(report.found)
    msgs = sum(len(g) for f in report.found for g in f.groups)
    convs = sum(len(f.groups) for f in report.found)
    gm = sum(1 for f in report.found if f.provider_thread)
    verb = "would split" if dry_run else "split"
    lines = [(f"user {report.user_id}: {verb} {n} conversation(s) of automated mail: {msgs} messages "
              f"into {convs} conversations ({convs - n} new); {gm} of them also share a provider thread id")]
    if not dry_run:
        lines.append(f"moved {report.messages_moved} messages, dropped {report.summaries_dropped} cached summaries")
    for f in report.found[:sample]:
        subj = f.subject if len(f.subject) <= 40 else f.subject[:39] + "…"
        span = f"{f.first:%Y-%m-%d}..{f.last:%Y-%m-%d}" if f.first and f.last else "-"
        sizes = "+".join(str(len(g)) for g in f.groups)
        lines.append(f"  conversation {f.conversation_id}: {sizes} messages, {span}, \"{subj}\"")
    if n > sample:
        lines.append(f"  … and {n - sample} more")
    return "\n".join(lines)
