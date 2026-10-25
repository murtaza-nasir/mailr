"""Recompute fields derived from the raw message for mail already stored.

`mailr langid-backfill` stores the language of recent mail ingested before language
identification existed (core/langid.py). `mailr resnippet` rebuilds the list previews
(mime.preview) of recent mail after the preview rules change; the conversation previews follow
(threading.refresh). Both read each message from the store, run in batches with a commit per
batch, and can be stopped and run again: a message whose preview or language is already
current is left as it is.

`mailr reparse-attached-emails` parses again the mail stored while the text of attached
emails (message/rfc822) leaked into the outer message's body (attached_emails).

`mailr share-origin-backfill` repairs the phone's "Send to self" shares stored between the change
that stopped marking them as sent by the user and messages.origin (share_origin_backfill).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from mailr.core import threading
from mailr.core.jmap import JMAPError
from mailr.core.mime import parse
from mailr.core.store import user_store
from mailr.db import session_scope
from mailr.models import Message, User

log = logging.getLogger("mailr.reparse")


async def resnippet(user_id: int, days: int = 30, batch: int = 200, dry_run: bool = False,
                    progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Recompute the stored preview of the user's mail from the last `days` days."""
    since = datetime.now(UTC) - timedelta(days=days)
    stats = {"seen": 0, "changed": 0, "skipped": 0, "failed": 0, "examples": []}
    cursor = 0
    while True:
        async with session_scope() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise ValueError(f"no user {user_id}")
            rows = list((await db.execute(
                select(Message).where(Message.user_id == user_id, Message.deleted.is_(False),
                                      Message.date >= since, Message.id > cursor)
                .order_by(Message.id).limit(batch))).scalars())
            if not rows:
                break
            cursor = rows[-1].id
            store = await user_store(db, user)
            convs: list[int] = []
            for m in rows:
                stats["seen"] += 1
                if not m.blob_id:
                    stats["skipped"] += 1  # a local draft: its preview comes from the composer
                    continue
                try:
                    raw = await store.get_raw(m)
                except (JMAPError, OSError) as e:
                    stats["failed"] += 1
                    log.warning("resnippet: message %s: %s", m.id, e)
                    continue
                p = await asyncio.to_thread(parse, raw)
                if p.snippet == m.snippet:
                    continue
                stats["changed"] += 1
                if len(stats["examples"]) < 5:
                    stats["examples"].append({"id": m.id, "before": m.snippet[:120], "after": p.snippet[:120]})
                if not dry_run:
                    m.snippet = p.snippet
                    if m.conversation_id:
                        convs.append(m.conversation_id)
            if not dry_run:
                await threading.refresh(db, user_id, convs)
            else:
                await db.rollback()
        if progress:
            progress(stats)
    return stats


async def langid_backfill(user_id: int, days: int = 90, batch: int = 200, redo: bool = False,
                          progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Detect the language (core/langid.py) of the user's mail from the last `days` days that
    has none yet (lang_confidence is null); `redo` checks every message again. Each batch is
    committed, so a stopped run resumes where it ended."""
    from mailr.core import langid

    if not langid.available():
        raise RuntimeError(f"the language model is missing: {langid.MODEL_PATH} (run `mailr langid-model`)")
    since = datetime.now(UTC) - timedelta(days=days)
    stats: dict[str, Any] = {"seen": 0, "detected": 0, "short": 0, "failed": 0, "languages": {}}
    cursor = 0
    while True:
        async with session_scope() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise ValueError(f"no user {user_id}")
            q = (select(Message).where(Message.user_id == user_id, Message.deleted.is_(False),
                                       Message.date >= since, Message.id > cursor)
                 .order_by(Message.id).limit(batch))
            if not redo:
                q = q.where(Message.lang_confidence.is_(None))
            rows = list((await db.execute(q)).scalars())
            if not rows:
                break
            cursor = rows[-1].id
            store = await user_store(db, user)
            for m in rows:
                stats["seen"] += 1
                if m.blob_id:
                    try:
                        raw = await store.get_raw(m)
                    except (JMAPError, OSError) as e:
                        stats["failed"] += 1
                        log.warning("langid backfill: message %s: %s", m.id, e)
                        continue
                    p = await asyncio.to_thread(parse, raw)
                    lang, conf = p.lang, p.lang_confidence
                else:  # a local draft: its stored text
                    lang, conf = langid.detect(m.body_text or "")
                if conf is None:
                    stats["failed"] += 1
                    continue
                m.lang, m.lang_confidence = lang, conf
                if lang:
                    stats["detected"] += 1
                    stats["languages"][lang] = stats["languages"].get(lang, 0) + 1
                else:
                    stats["short"] += 1
        if progress:
            progress(stats)
    return stats


# The change that stopped marking phone shares as the user's own mail (commit 07b9b72) was
# deployed on 3 Oct 2026; from then until messages.origin
# existed, a share from the phone was stored with sent_by_me false and without its origin.
SHARE_REPAIR_SINCE = datetime(2026, 10, 3, 10, 35, tzinfo=UTC)


async def share_origin_backfill(user_id: int, since: datetime = SHARE_REPAIR_SINCE, batch: int = 200,
                                dry_run: bool = False,
                                progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Repair the phone's "Send to self" shares stored since `since` (by ingest time) without an
    origin: read the X-Mailr-Origin header from the store, record it (Message.origin) and, where
    the message otherwise qualifies (From one of the user's addresses, in Sent or authenticated:
    ingest.is_sent_by_me), make it sent_by_me again, read, and run the user's filters on a note to
    self, so "from:me to:me" rules apply as they would have on arrival. Only messages From one of
    the user's addresses are read; a repaired message has its origin, so a second run skips it."""
    from mailr.core.ingest import (
        IngestOptions,
        _labels_by,
        apply_rules_to_stored,
        is_sent_by_me,
        my_addresses,
        shared_origin,
    )

    stats: dict[str, Any] = {"seen": 0, "shared": 0, "sent_by_me": 0, "to_self": 0, "filtered": 0, "failed": 0,
                             "examples": []}
    cursor = 0
    while True:
        async with session_scope() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise ValueError(f"no user {user_id}")
            mine = await my_addresses(db, user)
            rows = list((await db.execute(
                select(Message).where(Message.user_id == user_id, Message.deleted.is_(False),
                                      Message.draft.is_(False), Message.origin.is_(None),
                                      Message.blob_id.is_not(None), Message.created_at >= since,
                                      func.lower(Message.from_email).in_(sorted(mine)), Message.id > cursor)
                .order_by(Message.id).limit(batch))).scalars())
            if not rows:
                break
            cursor = rows[-1].id
            store = await user_store(db, user)
            by_role, _ = await _labels_by(db, user_id)
            sent_id = by_role["sent"].id if "sent" in by_role else None
            convs: list[int] = []
            for m in rows:
                stats["seen"] += 1
                try:
                    raw = await store.get_raw(m)
                except (JMAPError, OSError) as e:
                    stats["failed"] += 1
                    log.warning("share repair: message %s: %s", m.id, e)
                    continue
                p = await asyncio.to_thread(parse, raw)
                origin = shared_origin(p)
                if origin is None:
                    continue
                stats["shared"] += 1
                in_sent = sent_id is not None and sent_id in (m.label_ids or [])
                mine_now = not m.sent_by_me and is_sent_by_me(
                    p, mine, IngestOptions(roles=["sent"] if in_sent else [], source="store"))
                rcpt = {str(a.get("email", "")).lower() for a in list(m.to or []) + list(m.cc or [])}
                to_self = mine_now and bool(rcpt & mine)
                if len(stats["examples"]) < 10:
                    stats["examples"].append({"id": m.id, "subject": (m.subject or "")[:80],
                                              "sent_by_me": m.sent_by_me or mine_now, "filters": to_self})
                stats["to_self"] += int(to_self)
                if dry_run:
                    stats["sent_by_me"] += int(mine_now)
                    continue
                m.origin = origin
                if mine_now:
                    stats["sent_by_me"] += 1
                    m.sent_by_me = True
                    add = [sent_id] if sent_id is not None and not in_sent else []
                    if add:
                        await store.set_labels([m], add=add, remove=[], db=db)
                    if not m.seen:
                        await store.set_keywords([m], seen=True)
                    if to_self:
                        await db.flush()  # the filters' `from:me` reads the row
                        plan = await apply_rules_to_stored(db, store, m, by_role)
                        stats["filtered"] += int(bool(plan.get("_rules")))
                if m.conversation_id:
                    convs.append(m.conversation_id)
            if dry_run:
                await db.rollback()
            else:
                await threading.refresh(db, user_id, convs)
        if progress:
            progress(stats)
    return stats


async def auth_results_backfill(user_id: int, days: int = 365, batch: int = 200, dry_run: bool = False,
                                progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Re-read the Authentication-Results of the user's mail from the last `days` days with the
    RFC 8601 parser (core/authres.py) and store them. A message marked sent by the user
    only on the strength of the old results (From one of the user's addresses, not in Sent) and
    that no longer authenticates loses sent_by_me: it was a forged "From: me". Each batch is
    committed; a second run finds nothing to change."""
    import email
    import email.policy
    from types import SimpleNamespace

    from mailr.core.ingest import IngestOptions, _labels_by, is_sent_by_me, my_addresses
    from mailr.core.mime import _auth_results

    since = datetime.now(UTC) - timedelta(days=days)
    stats: dict[str, Any] = {"seen": 0, "changed": 0, "not_mine": 0, "failed": 0, "examples": []}
    cursor = 0
    while True:
        async with session_scope() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise ValueError(f"no user {user_id}")
            mine = await my_addresses(db, user)
            by_role, _ = await _labels_by(db, user_id)
            sent_id = by_role["sent"].id if "sent" in by_role else None
            rows = list((await db.execute(
                select(Message).where(Message.user_id == user_id, Message.deleted.is_(False),
                                      Message.blob_id.is_not(None), Message.date >= since, Message.id > cursor)
                .order_by(Message.id).limit(batch))).scalars())
            if not rows:
                break
            cursor = rows[-1].id
            store = await user_store(db, user)
            convs: list[int] = []
            for m in rows:
                stats["seen"] += 1
                try:
                    raw = await store.get_raw(m)
                except (JMAPError, OSError) as e:
                    stats["failed"] += 1
                    log.warning("auth-results backfill: message %s: %s", m.id, e)
                    continue
                head = raw.split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0] + b"\r\n\r\n"
                ar = _auth_results(email.message_from_bytes(head, policy=email.policy.default))
                in_sent = sent_id is not None and sent_id in (m.label_ids or [])
                drop = bool(m.sent_by_me) and not in_sent and not is_sent_by_me(
                    SimpleNamespace(from_email=(m.from_email or "").lower(), auth_results=ar), mine,
                    IngestOptions(roles=[], source="store"))
                if ar == (m.auth_results or {}) and not drop:
                    continue
                stats["changed"] += 1
                stats["not_mine"] += int(drop)
                if drop and len(stats["examples"]) < 20:
                    stats["examples"].append({"id": m.id, "subject": (m.subject or "")[:80], "from": m.from_email})
                if dry_run:
                    continue
                m.auth_results = ar
                if drop:
                    m.sent_by_me = False
                    if m.conversation_id:
                        convs.append(m.conversation_id)
            if dry_run:
                await db.rollback()
            else:
                await threading.refresh(db, user_id, convs)
        if progress:
            progress(stats)
    return stats


# What an attached email looks like in the raw message: a message/* part or a digest. Mail
# without any of these is passed over without parsing.
_ATTACHED_HINT = re.compile(rb"(?i)content-type:\s*(?:message/(?:rfc822|global|news)|multipart/digest)")


def _attached_state_path(user_id: int, dry_run: bool) -> Path:
    from mailr.config import get_settings

    d = Path(get_settings().data_dir) / "state" / "reparse-attached-emails"
    return d / f"{user_id}{'-dry-run' if dry_run else ''}.json"


async def attached_emails(user_id: int, batch: int = 200, dry_run: bool = True, rate: float = 50.0,
                          restart: bool = False,
                          progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Find the user's mail stored before attached emails were kept apart (core/mime.py: the
    text of a message/rfc822 part leaked into the outer message's body) and, unless `dry_run`,
    parse those messages again.

    Every message with a stored copy is read from the store, `batch` at a time and at most `rate`
    a second; only messages whose raw text has an attached email are parsed. Counts only, per mail
    account (connector id; 0 = none), never content. The cursor is saved after each batch
    (<data dir>/state/reparse-attached-emails/), so a stopped run resumes where it ended; a
    finished one, or `restart`, starts again from the first message. A dry run changes nothing in the database.

    A real run stores the new body, attached-email text, preview, language and attachment list
    (the search vectors follow by trigger), rebuilds the message's passages and queues their
    embedding (intel.embed_pending, never waited for), drops the cached summaries of the
    conversations, withdraws memories the learner only suggested from such a message when it is
    the user's own words (accepted memories stay), drops what triage read in the old text (the
    deadline, needs-reply and waiting-on signals mailr set, the action chip and an open
    event-change card; the user's own choices stay), refreshes the
    conversation rows, and writes one audit entry per batch. A second run finds nothing to change."""
    import json

    from sqlalchemy import delete
    from sqlalchemy.orm import undefer

    from mailr.core.jobs import enqueue
    from mailr.intel import passages, summarize
    from mailr.models import AuditLog, Memory, MessagePassage

    state_path = _attached_state_path(user_id, dry_run)
    state: dict[str, Any] = {}
    if not restart:
        try:
            state = json.loads(state_path.read_text())
        except (OSError, ValueError):
            state = {}
        if state.get("done"):
            state = {}  # the last run finished: this one starts again from the first message
    stats: dict[str, Any] = {"seen": 0, "with_attached": 0, "changed": 0, "failed": 0,
                             "memories_withdrawn": 0, "signals_cleared": 0, "accounts": {},
                             "resumed_from": state.get("cursor", 0)}
    for k in ("seen", "with_attached", "changed", "failed", "memories_withdrawn", "signals_cleared"):
        stats[k] = int((state.get("stats") or {}).get(k, 0))
    stats["accounts"] = dict((state.get("stats") or {}).get("accounts") or {})
    cursor = int(state.get("cursor") or 0)
    min_gap = 1.0 / rate if rate and rate > 0 else 0.0
    while True:
        async with session_scope() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise ValueError(f"no user {user_id}")
            rows = list((await db.execute(
                select(Message).options(undefer(Message.attached_text))
                .where(Message.user_id == user_id, Message.deleted.is_(False),
                       Message.blob_id.is_not(None), Message.id > cursor)
                .order_by(Message.id).limit(batch))).scalars())
            if not rows:
                break
            store = await user_store(db, user)
            convs: set[int] = set()
            changed_ids: list[int] = []
            for m in rows:
                started = asyncio.get_running_loop().time()
                cursor = m.id
                stats["seen"] += 1
                try:
                    raw = await store.get_raw(m)
                except (JMAPError, OSError) as e:
                    stats["failed"] += 1
                    log.warning("reparse-attached-emails: message %s: %s", m.id, type(e).__name__)
                    continue
                finally:
                    wait = min_gap - (asyncio.get_running_loop().time() - started)
                    if wait > 0:
                        await asyncio.sleep(wait)
                if not _ATTACHED_HINT.search(raw):
                    continue
                p = await asyncio.to_thread(parse, raw)
                if not any(a.get("type") == "message/rfc822" for a in p.attachments):
                    continue
                stats["with_attached"] += 1
                same = (p.body_text == m.body_text and (p.attached_text or None) == m.attached_text
                        and p.attachments == (m.attachments or []) and p.snippet == m.snippet)
                if same:
                    continue
                stats["changed"] += 1
                acct = str(m.connector_id or 0)
                stats["accounts"][acct] = stats["accounts"].get(acct, 0) + 1
                if dry_run:
                    continue
                m.body_text, m.attached_text, m.snippet = p.body_text, p.attached_text or None, p.snippet
                m.attachments = p.attachments
                m.has_attachments = bool([a for a in p.attachments if not a.get("inline")])
                if p.lang_confidence is not None:
                    m.lang, m.lang_confidence = p.lang, p.lang_confidence
                m.embedding, m.embed_model = None, None
                changed_ids.append(m.id)
                if m.conversation_id:
                    convs.add(m.conversation_id)
                if m.own_words:
                    gone = await db.execute(delete(Memory).where(
                        Memory.user_id == user_id, Memory.status == "suggested",
                        Memory.refs.contains([{"message_id": m.id}])))
                    stats["memories_withdrawn"] += gone.rowcount or 0
                stats["signals_cleared"] += await _drop_derived(db, m)
            if dry_run:
                await db.rollback()
            elif changed_ids:
                await db.flush()
                await db.execute(delete(MessagePassage).where(MessagePassage.message_id.in_(changed_ids)))
                for m in rows:
                    if m.id in changed_ids:
                        await passages.build_for_message(db, m)
                await threading.refresh(db, user_id, sorted(convs))
                await enqueue(db, "intel.embed_pending", {"user_id": user_id}, user_id=user_id,
                              dedupe_key=f"intel.embed_pending:{user_id}")
                db.add(AuditLog(user_id=user_id, token_id=None, actor="cli", action="mail.reparse_attached_emails",
                                target=f"{rows[0].id}-{cursor}", inputs={"batch": batch},
                                result={"changed": len(changed_ids), "conversations": len(convs)}, ok=True))
        if not dry_run and convs:
            summarize.drop_cached(user_id, sorted(convs))
        _save_state(state_path, cursor, stats)
        if progress:
            progress(stats)
    _save_state(state_path, cursor, stats, done=True)
    return stats


async def _drop_derived(db, m: Message) -> int:
    """What triage took from a message's old text: the smart signals mailr set (deadline,
    needs reply, waiting on; one the user turned down stays, as their choice), the action chip,
    and an open event-change card. Returns how many were dropped."""
    from sqlalchemy import delete

    from mailr.intel import event_changes
    from mailr.models import LabelDecision

    gone = await db.execute(delete(LabelDecision).where(
        LabelDecision.message_id == m.id, LabelDecision.kind == "smart",
        LabelDecision.status.in_(("applied", "suggested"))))
    n = gone.rowcount or 0
    if m.primary_action:
        m.primary_action = None
        n += 1
    if event_changes.open_suggestion(m) is not None:
        await event_changes.close(db, m, "withdrawn")
        n += 1
    return n


def _save_state(path: Path, cursor: int, stats: dict[str, Any], done: bool = False) -> None:
    import json

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"cursor": cursor, "done": done,
                                   "stats": {k: v for k, v in stats.items() if k != "resumed_from"}}))
        tmp.replace(path)
    except OSError as e:
        log.warning("reparse-attached-emails: cannot save the cursor: %s", e)
