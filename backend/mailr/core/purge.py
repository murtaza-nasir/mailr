"""What is left of a message deleted for good, removed after the fact.

Delete forever (and Empty Trash or Empty Spam, which run it) destroys the store copy and marks the
row deleted at once; the provider copy goes through the write-back (a mirror account) or
`connector.delete_forever` (a store account). This module removes the rest,
in the job `mail.purge_deleted`, so the action itself stays fast:

- the row's text: subject, sender and recipients, preview, body, attached-email text, attachment
  names, list headers, the main-action chip, the language and the vector (the search vectors
  follow by trigger), and its passages;
- what was made from it: the reasons of model decisions, the person details a signature
  suggested and the memories the learner only suggested, its references in memories, quotes in
  chat and routine citations, a routine run's output, the preview lines of a decided approval, a
  sent message's composed copy in the outbox, and the cached summary and reply suggestions;
- its file in the daily mail export and on the backup target, and its manifest line
  (core/backup.py `remove_exported`).

The row stays as a tombstone with `intel_state = 'purged'`. It keeps only what other parts need:
ids (user, conversation, store thread), the Message-ID, In-Reply-To and References (a later
reply still joins the conversation, core/threading.py), the upstream refs, connector and
content hash (a queued write-back still finds and deletes the provider copy,
connectors/tasks.py), the blob id (the export line is matched by it), dates, size, flags and
labels. The sender's address stays only on a message reported as spam: spam sender learning
counts reports whose mail is deleted (intel/spam.py `spam_marks`).

Which deletions are purged when:
- explicit ones at once: Delete forever (the job carries `export: true`), a discarded draft and
  the draft of a sent message (rows only: drafts are not exported); removing a user removes their
  export folders (`purge_user_export`). Only these remove export files.
- a message that vanished from the mail store (another mail app, the store's own Trash expiry,
  or a store that lost its data or was restored from an older snapshot) is only marked deleted
  (core/storesync.py); the daily sweep scrubs its row and never touches its export file: after a
  restore, the export is the only copy left of mail the store lost. The sweep holds back when the
  rows are more than MASS_SHARE of the user's messages and more than MASS_MIN: a store that lost
  its mail looks the same, and the rows' text is then all that is left. A hold is logged and
  recorded (instance setting `purge_held`, shown in Admin → Diagnostics); `mailr purge-deleted`
  purges anyway, when an administrator asks, and `mailr purge-deleted --exports` also removes the
  export files of all deleted mail.

The sweep (`sweep_user`) also cleans rows deleted before this module existed: it runs daily and
once at the first scheduler tick after an upgrade. Every step is idempotent. Nightly database
dumps are not edited: a nightly dump ages out after MAILR_DB_DUMP_KEEP dumps, but the newest three
dumps taken before migrations or deploys and every manual dump stay until removed (core/dbdump.py).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.jobs import Retry, enqueue, handler
from mailr.db import session_scope
from mailr.models import Message, MessagePassage, User

log = logging.getLogger("mailr.purge")

JOB = "mail.purge_deleted"
CHUNK = 500  # rows per transaction
JOB_IDS = 2000  # message ids per queued job
MASS_SHARE = 0.10
MASS_MIN = 1000
PURGED = "purged"  # intel_state of a scrubbed row


async def queue(db: AsyncSession, user_id: int, message_ids: list[int], *, export: bool = True) -> None:
    """Queue the purge of these deleted messages in the caller's transaction. `export`: their
    export files go too (Delete forever); without it only the rows are scrubbed, and the daily
    sweep looks at the files."""
    ids = sorted({int(i) for i in message_ids})
    for i in range(0, len(ids), JOB_IDS):
        await enqueue(db, JOB, {"user_id": user_id, "message_ids": ids[i:i + JOB_IDS], "export": export},
                      user_id=user_id, max_attempts=10)


_CLEAR = {
    "subject": "", "norm_subject": "", "from_name": "", "to": [], "cc": [], "bcc": [], "reply_to": [],
    "delivered_to": [], "list_id": None, "list_unsubscribe": None, "unsub_method": None, "primary_action": None,
    "snippet": "", "body_text": "", "attached_text": None, "lang": None, "lang_confidence": None,
    "has_attachments": False, "attachments": [], "auth_results": {}, "screened_key": None, "embedding": None,
    "embed_model": None, "intel_state": PURGED,
}


async def scrub(db: AsyncSession, user_id: int, ids: list[int]) -> dict[str, Any]:
    """Scrub these rows of the user, deleted ones only (module docstring); citations in chat go
    separately (`scrub_citations`). Returns {rows, ids, conversations, blobs}: the scrubbed rows,
    the conversations touched and the blob ids."""
    rows = (await db.execute(select(Message.id, Message.conversation_id, Message.blob_id,
                                    Message.message_id_hdr, Message.sent_by_me).where(
        Message.user_id == user_id, Message.id.in_(ids), Message.deleted.is_(True)))).all()
    if not rows:
        return {"rows": 0, "ids": [], "conversations": set(), "blobs": set()}
    mids = [r.id for r in rows]
    spam_marked = set((await db.execute(text(
        "SELECT DISTINCT message_id FROM label_decisions WHERE message_id = ANY(:ids) AND kind = 'spam' "
        "AND source = 'user' AND value"), {"ids": mids})).scalars())
    await db.execute(update(Message).where(Message.id.in_(mids)).values(**_CLEAR)
                     .execution_options(synchronize_session=False))
    if plain := [i for i in mids if i not in spam_marked]:
        await db.execute(update(Message).where(Message.id.in_(plain)).values(from_email="")
                         .execution_options(synchronize_session=False))
    await db.execute(delete(MessagePassage).where(MessagePassage.user_id == user_id,  # ix_passage_user_msg
                                                  MessagePassage.message_id.in_(mids)))
    params = {"u": user_id, "ids": mids, "tids": [str(i) for i in mids]}
    await db.execute(text("UPDATE label_decisions SET reason = '' WHERE message_id = ANY(:ids) AND reason <> ''"),
                     {"ids": mids})
    await db.execute(text("DELETE FROM person_suggestions WHERE user_id = :u AND message_id = ANY(:ids) "
                          "AND state = 'suggested'"), {"u": user_id, "ids": mids})
    # Memories: one the learner only suggested from such a message goes; any other keeps its
    # text (the user accepted or wrote it) and loses the reference.
    refs = "EXISTS (SELECT 1 FROM jsonb_array_elements(refs) r WHERE r->>'message_id' = ANY(:tids))"
    await db.execute(text(f"DELETE FROM kb_memories WHERE user_id = :u AND status = 'suggested' AND {refs}"), params)
    await db.execute(text(
        "UPDATE kb_memories SET refs = (SELECT coalesce(jsonb_agg(r), '[]'::jsonb) FROM jsonb_array_elements(refs) r "
        f"WHERE r->>'message_id' IS NULL OR NOT r->>'message_id' = ANY(:tids)) WHERE user_id = :u AND {refs}"), params)
    await db.execute(text("UPDATE assistant_runs SET output = '', citations = '[]'::jsonb "
                          "WHERE user_id = :u AND message_id = ANY(:ids)"), params)
    await db.execute(text(
        "UPDATE pending_actions SET preview = jsonb_set(preview, '{lines}', '[]'::jsonb) "
        "WHERE user_id = :u AND status <> 'pending' AND preview ? 'message_ids' AND preview ? 'lines' "
        "AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(preview->'message_ids') x WHERE x = ANY(:tids))"), params)
    if sent := [r.message_id_hdr for r in rows if r.sent_by_me and r.message_id_hdr]:
        # The composed copy of a sent message; the recipients stay (they are people you wrote to).
        await db.execute(text("UPDATE outbox SET payload = '{}'::jsonb WHERE user_id = :u AND status = 'sent' "
                              "AND sent_message_id = ANY(:m) AND payload <> '{}'::jsonb"), {"u": user_id, "m": sent})
    convs = {r.conversation_id for r in rows if r.conversation_id}
    await asyncio.to_thread(_drop_caches, user_id, mids, sorted(convs))
    return {"rows": len(mids), "ids": mids, "conversations": convs, "blobs": {r.blob_id for r in rows if r.blob_id}}


_MESSAGE_CITE = ("subject", "from", "quote")


async def scrub_citations(db: AsyncSession, user_id: int, ids: list[int]) -> None:
    """Citations of these messages in chat answers keep their link, without the subject, sender or
    quote they carried. Once per job, not per chunk: it reads every chat message of the user that
    has citations."""
    if not ids:
        return
    params = {"u": user_id, "tids": [str(i) for i in ids]}
    hit = "EXISTS (SELECT 1 FROM jsonb_array_elements(citations) c WHERE c->>'message_id' = ANY(:tids))"
    rows = (await db.execute(text(
        "SELECT cm.id, cm.citations FROM chat_messages cm JOIN chat_sessions s ON s.id = cm.session_id "
        f"WHERE s.user_id = :u AND jsonb_typeof(cm.citations) = 'array' AND {hit}"), params)).all()
    gone = set(params["tids"])
    for cid, cites in rows:
        out = []
        for c in cites:
            if isinstance(c, dict) and str(c.get("message_id")) in gone:
                c = {**c, **dict.fromkeys(_MESSAGE_CITE), "url": _without_quote(c.get("url"))}
            out.append(c)
        await db.execute(text("UPDATE chat_messages SET citations = CAST(:c AS jsonb) WHERE id = :id"),
                         {"c": _json(out), "id": cid})


def _without_quote(url: Any) -> Any:
    """A citation link without its highlighted quote (`hl=`, intel/citations.py url_for)."""
    if not isinstance(url, str) or "hl=" not in url:
        return url
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    u = urlsplit(url)
    return urlunsplit(u._replace(query=urlencode([(k, v) for k, v in parse_qsl(u.query) if k != "hl"])))


def _json(v: Any) -> str:
    import json

    return json.dumps(v, default=str)


def _drop_caches(user_id: int, mids: list[int], convs: list[int]) -> None:
    """The cached summaries of the conversations and the reply suggestions of the messages."""
    from pathlib import Path

    from mailr.config import get_settings
    from mailr.intel import summarize

    summarize.drop_cached(user_id, convs)
    d = Path(get_settings().data_dir) / "cache" / "replies" / str(user_id)
    for mid in mids:
        (d / f"{mid}.json").unlink(missing_ok=True)


async def _live_blobs(db: AsyncSession, user_id: int, blobs: set[str]) -> set[str]:
    """Of these blobs, those another message of the user that is not deleted still has (the same
    bytes stored twice): their export file stays."""
    if not blobs:
        return set()
    return {b for b in (await db.execute(select(Message.blob_id).where(
        Message.user_id == user_id, Message.deleted.is_(False), Message.blob_id.in_(list(blobs))))).scalars() if b}


async def _remove_exports(user_id: int, blobs: set[str], message_ids: list[int] | None = None) -> dict[str, int]:
    from mailr.core import backup

    try:
        return await asyncio.to_thread(backup.remove_exported, user_id, blobs, 30, message_ids)
    except backup.ExportBusy:
        raise Retry(delay=120) from None


@handler(JOB)
async def purge_job(payload: dict[str, Any], ctx) -> None:
    """Purge the deleted messages `message_ids` of `user_id` (module docstring); without ids, the
    sweep of that user."""
    uid = int(payload["user_id"])
    ids = [int(i) for i in payload.get("message_ids") or []]
    if not ids:
        await sweep_user(uid)
        return
    blobs: set[str] = set()
    done: list[int] = []
    for i in range(0, len(ids), CHUNK):
        async with session_scope() as db:
            res = await scrub(db, uid, ids[i:i + CHUNK])
            blobs |= res["blobs"]
            done += res["ids"]
    async with session_scope() as db:
        await scrub_citations(db, uid, done)
    if payload.get("export") and done:
        async with session_scope() as db:
            blobs -= await _live_blobs(db, uid, blobs)
        # The ids go too: an export run that read these rows before they were deleted leaves
        # their files out when it finishes (core/backup.py `.forgotten`).
        await _remove_exports(uid, blobs, done)


async def _unscrubbed(db: AsyncSession, user_id: int) -> list[int]:
    """Deleted rows of the user not scrubbed yet, or given text again after their scrub (a job
    that read the message just before it was deleted)."""
    return list((await db.execute(text(
        "SELECT id FROM messages m WHERE m.user_id = :u AND m.deleted AND (m.intel_state <> :p "
        "OR m.embedding IS NOT NULL OR m.body_text <> '' OR m.subject <> '' "
        "OR EXISTS (SELECT 1 FROM message_passages p WHERE p.user_id = :u AND p.message_id = m.id)) ORDER BY m.id"),
        {"u": user_id, "p": PURGED})).scalars())


async def _stale_blobs(db: AsyncSession, user_id: int) -> tuple[set[str], int]:
    """Blobs the user's export manifest lists whose messages are all deleted, and the size of
    the manifest."""
    from mailr.core import backup

    manifest = set(await asyncio.to_thread(backup.exported_manifest, user_id))
    if not manifest:
        return set(), 0
    dead = set((await db.execute(text(
        "SELECT DISTINCT blob_id FROM messages WHERE user_id = :u AND deleted AND blob_id IS NOT NULL"),
        {"u": user_id})).scalars()) & manifest
    return dead - await _live_blobs(db, user_id, dead), len(manifest)


def _mass(n: int, total: int) -> bool:
    return n > MASS_MIN and n > MASS_SHARE * total


HELD_KEY = "purge_held"


async def _record_hold(user_id: int, held: dict[str, int] | None) -> None:
    """Keep or clear this user's entry in the instance setting `purge_held` (Admin → Diagnostics):
    {"<user id>": {rows, files, at}}. Merged in one statement per user."""
    from datetime import UTC, datetime

    async with session_scope() as db:
        if held:
            entry = {**held, "at": datetime.now(UTC).replace(microsecond=0).isoformat()}
            await db.execute(text(
                "INSERT INTO instance_settings (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
                "ON CONFLICT (key) DO UPDATE SET value = coalesce(instance_settings.value, '{}'::jsonb) || "
                "CAST(:v AS jsonb), updated_at = now()"), {"k": HELD_KEY, "v": _json({str(user_id): entry})})
        else:
            await db.execute(text("UPDATE instance_settings SET value = value - :u, updated_at = now() "
                                  "WHERE key = :k AND value ? :u"), {"k": HELD_KEY, "u": str(user_id)})


async def held_sweeps(db: AsyncSession) -> list[dict[str, Any]]:
    """The users whose last sweep held back, for Admin → Diagnostics: {user, rows, files, at}."""
    value = (await db.execute(text("SELECT value FROM instance_settings WHERE key = :k"),
                              {"k": HELD_KEY})).scalar_one_or_none() or {}
    if not value:
        return []
    ids = [int(k) for k in value if str(k).isdigit()]
    emails = dict((await db.execute(select(User.id, User.email).where(User.id.in_(ids)))).all())
    return [{"user": emails[i], "rows": int(value[str(i)].get("rows") or 0),
             "files": int(value[str(i)].get("files") or 0), "at": value[str(i)].get("at")}
            for i in sorted(ids) if i in emails]


async def sweep_user(user_id: int, *, dry_run: bool = False, force: bool = False,
                     exports: bool = False) -> dict[str, Any]:
    """Scrub every deleted row of the user that still holds text. Without `force`, the rows are held
    back when they are a large part of the user's mail (module docstring); a hold is logged and
    recorded. Export files of deleted mail stay (`files_kept`), unless `exports` (`mailr
    purge-deleted --exports`) removes them, with the same hold for the files.
    Returns counts: rows, files (removed), files_kept, held_rows, held_files, held (either)."""
    async with session_scope() as db:
        ids = await _unscrubbed(db, user_id)
        stale, total = await _stale_blobs(db, user_id)
        # Tombstones scrubbed before do not count: they would dilute the share for good.
        rows_total = (await db.execute(text(
            "SELECT count(*) FROM messages WHERE user_id = :u AND NOT (deleted AND intel_state = :p)"),
            {"u": user_id, "p": PURGED})).scalar_one() if ids else 0
    held_rows = not force and _mass(len(ids), rows_total)
    held_files = exports and not force and _mass(len(stale), total)
    remove = exports and not held_files
    out: dict[str, Any] = {"rows": len(ids), "files": len(stale) if remove else 0,
                           "files_kept": 0 if remove else len(stale), "held": held_rows or held_files,
                           "held_rows": held_rows, "held_files": held_files, "dry_run": dry_run}
    if dry_run:
        return out
    if held_rows or held_files:
        log.warning("purge user %s: held back, %s of %s messages are deleted (a mail store that lost its data looks "
                    "the same). If the mail store is healthy, run `mailr purge-deleted` to purge them", user_id,
                    len(ids), rows_total)
        await _record_hold(user_id, {"rows": len(ids) if held_rows else 0, "files": len(stale)})
    else:
        await _record_hold(user_id, None)
    if not held_rows:
        for i in range(0, len(ids), CHUNK):
            async with session_scope() as db:
                await scrub(db, user_id, ids[i:i + CHUNK])
        for i in range(0, len(ids), JOB_IDS):
            async with session_scope() as db:
                await scrub_citations(db, user_id, ids[i:i + JOB_IDS])
    if stale and remove:
        out.update(await _remove_exports(user_id, stale))
    else:
        # Removals the backup target did not have yet (it was not mounted): files of mail deleted
        # forever, never of mail that only vanished from the store.
        from mailr.core import backup

        await asyncio.to_thread(backup.apply_target_removals, user_id)
    return out


async def queue_sweep(db, uid: int) -> None:
    """The daily sweep of one user (core/tasks.py SPREAD). The first run after an upgrade cleans the
    messages deleted before this module."""
    await enqueue(db, JOB, {"user_id": uid}, user_id=uid, dedupe_key=f"{JOB}:{uid}")


@handler("mail.purge_user_export")
async def purge_user_export(payload: dict[str, Any], ctx) -> None:
    """A user was deleted with all their mail: their mail, notes and calendar exports go too, here
    and on the backup target; while the target cannot be reached, the job tries again hourly.
    Nothing while a user with that id exists."""
    from mailr.core import backup
    from mailr.core.jobs import Retry

    uid = int(payload["user_id"])
    async with session_scope() as db:
        if await db.get(User, uid) is not None:
            return
    if not await asyncio.to_thread(backup.remove_user_export, uid):
        raise Retry(delay=3600)
