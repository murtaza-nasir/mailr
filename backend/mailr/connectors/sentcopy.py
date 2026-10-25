"""A copy of mail sent from mailr in the provider's Sent folder (mirror mode).

Mail goes out through the account's own SMTP server (core/sendgate.py). Most providers do not keep
a copy of what is submitted there: a mail app saves it itself, with IMAP APPEND after the
submission (Roundcube, Thunderbird). Mailr does the same for a mirror-mode IMAP account, so the
provider's Sent folder (its webmail, other apps) has everything sent from mailr.

- Which accounts: kind "imap" in mirror mode, by the account setting `save_sent` (config):
  "auto" (the default) saves unless the provider keeps its own copy of SMTP submissions
  (`provider_saves_sent`: Gmail and Microsoft, by IMAP host), "on" and "off" decide for the
  account. Store and import accounts never save (store mode deletes the provider's mail after
  the retention window anyway), nor do Gmail-kind accounts.
- When: outbox.store_sent queues `sent.provider_copy` {connector_id, message_id} once the sent copy
  is in mailr's store (`queue_after_send`). Sending never waits for it. The job appends the stored
  bytes (the message exactly as submitted, Message-ID included), flagged \\Seen, to the folder sync
  maps to Sent (ImapConnector.sent_folder). A copy with that Message-ID already there (an earlier
  attempt, or the provider's own copy) is reused instead of a second one.
- Linking: the provider copy is recorded on the message as an upstream ref, as a mirror sync
  records the messages it imports, so the next sync skips its UID (known_upstream) instead of
  importing it again; a sync that fetched it meanwhile matches the ref (core/ingest.py
  `_find_duplicate`, "ref"). The append and the link run under the advisory lock ingest takes per
  Message-ID, so such a sync waits for the link.
- Failures: retried with the write-back's backoff (SENT_COPY_ATTEMPTS, about two and a half days).
  Then the account's health says "N sent messages could not be saved in <provider>'s Sent folder"
  (core/accounthealth.py) until Retry (`retry`) or a backfill saves them.
- Backfill (`sent.backfill`, `mailr sent-backfill`): the account's past sent mail that is missing
  at the provider, appended oldest first. Only messages sent from an identity of this account (its
  outbox rows) qualify, never mail sent through another account. It reads the Sent folder's
  Message-IDs once, saves its position after every batch (a restarted job continues there) and
  never deletes anything. A dry run connects, reads and counts only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.config import get_settings
from mailr.connectors.imap import all_refs, refs_to_upstream
from mailr.connectors.presets import PRESETS
from mailr.core.jobs import Retry, enqueue, enqueue_returning, handler
from mailr.db import get_existing, session_scope
from mailr.models import Connector, Job, Label, Message, User

log = logging.getLogger("mailr.sentcopy")

JOB = "sent.provider_copy"
BACKFILL_JOB = "sent.backfill"
SETTINGS = ("auto", "on", "off")
# Providers that put a message submitted through their SMTP server in Sent themselves (owner, 21 Oct
# 2026): an append there would make a second copy. By the IMAP host, the host itself or a subdomain.
PROVIDER_SAVES = ("gmail.com", "googlemail.com", "outlook.com", "office365.com", "hotmail.com", "live.com")
# As long as a write-back keeps trying (core/actions.py WRITE_BACK_ATTEMPTS: about two and a half days).
SENT_COPY_ATTEMPTS = 72
NETWORK_TIMEOUT_S = 180
LOCK_WAIT_S = 5
BACKFILL_BATCH = 50
# The health warning counts failures of this many days (failed jobs are kept 30 days, core/jobs.py).
FAILED_DAYS = 30


def setting(c: Connector) -> str:
    v = (c.config or {}).get("save_sent")
    return v if v in SETTINGS else "auto"


def imap_host(c: Connector) -> str:
    host = ((c.config or {}).get("imap") or {}).get("host") or \
        ((PRESETS.get(c.preset or "") or {}).get("imap") or {}).get("host") or ""
    return str(host).strip().lower().rstrip(".")


def provider_saves_sent(host: str) -> bool:
    """The provider keeps its own copy of mail submitted through its SMTP server (Gmail, Microsoft)."""
    host = (host or "").strip().lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in PROVIDER_SAVES)


def eligible(c: Connector) -> bool:
    """A mirror-mode IMAP account: the only kind that can save sent mail at the provider."""
    return c.kind == "imap" and c.mode == "mirror"


def active(c: Connector) -> bool:
    """Whether mail sent through this account is saved in the provider's Sent folder."""
    if not eligible(c) or not c.enabled:
        return False
    s = setting(c)
    return s == "on" or (s == "auto" and not provider_saves_sent(imap_host(c)))


def provider_name(c: Connector) -> str:
    return (PRESETS.get(c.preset or "") or {}).get("name") or c.name


def out(c: Connector, failed: int = 0) -> dict[str, Any] | None:
    """connector_out's `sent_copies`: the setting, whether it saves now, whether the provider keeps
    its own copy, and the messages that could not be saved. None for an account that never saves."""
    if not eligible(c):
        return None
    return {"setting": setting(c), "active": active(c), "provider_saves": provider_saves_sent(imap_host(c)),
            "provider": provider_name(c), "failed": failed}


# ---------------- after a send ----------------


async def queue_after_send(db: AsyncSession, conn_row: Connector | None, message_id: int) -> None:
    """Queue the provider copy of a sent message just stored in mailr (outbox.store_sent)."""
    if conn_row is None or not active(conn_row):
        return
    await enqueue(db, JOB, {"connector_id": conn_row.id, "message_id": message_id}, user_id=conn_row.user_id,
                  dedupe_key=f"{JOB}:{conn_row.id}:{message_id}", max_attempts=SENT_COPY_ATTEMPTS)


class _MessageLock:
    """The advisory lock ingest takes per Message-ID (core/ingest.py ingest_raw), held on a
    connection of its own across the append and the link: a sync that fetches the new copy
    meanwhile waits, then finds the ref. Busy for LOCK_WAIT_S: the job is re-queued (Retry)."""

    def __init__(self, user_id: int, msgid: str):
        self.key = f"msg:{user_id}:{msgid}"
        self.conn = None

    async def __aenter__(self):
        import asyncpg

        dsn = get_settings().database_url.replace("postgresql+asyncpg", "postgresql")
        self.conn = await asyncpg.connect(dsn)
        for _ in range(LOCK_WAIT_S):
            if await self.conn.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1, 0))", self.key):
                return self
            await asyncio.sleep(1)
        await self.conn.close()
        raise Retry(delay=30)

    async def ensure(self) -> None:
        """Before the append: the lock's connection is alive, so the lock is still held. When it
        dropped, a sync may fetch the new copy before the link: the job stops and runs again."""
        try:
            if self.conn is not None and not self.conn.is_closed():
                await self.conn.fetchval("SELECT 1")
                return
        except Exception:  # noqa: BLE001, S110  fail closed below
            pass
        raise Retry(delay=30)

    async def __aexit__(self, *exc):
        try:
            if self.conn is not None:
                await self.conn.close()  # ends the session, and its lock with it
        except Exception:  # noqa: BLE001, S110
            pass


def wire_bytes(raw: bytes) -> bytes:
    """The message with CRLF line ends, as it went out through SMTP and as IMAP servers store it
    (mailr's store may keep bare LF): the size the provider reports is then the size searched for."""
    return re.sub(rb"\r?\n", b"\r\n", raw)


def _key(r: dict[str, Any]) -> tuple:
    return (str(r.get("connector")), r.get("folder"), str(r.get("uidvalidity")), str(r.get("uid")))


def in_folder(m: Message, connector_id: int, folder: str) -> bool:
    """The message is already linked to a copy in this folder of this account."""
    return any(str(r.get("connector")) == str(connector_id) and r.get("folder") == folder for r in all_refs(m.upstream))


async def link(db: AsyncSession, message_id: int, connector_id: int, copy: dict[str, Any], raw: bytes) -> bool:
    """Record the provider copy on the message, as a mirror sync records what it imports: the
    primary ref when the message has none, else one of its copies. Then the failed attempts
    for this message no longer count. False when there is no UID to record or it is there."""
    from mailr.core.mime import content_hash

    if copy.get("uid") is None or copy.get("uidvalidity") is None:
        return False
    ref = {"connector": connector_id, "folder": copy["folder"], "uidvalidity": int(copy["uidvalidity"]),
           "uid": int(copy["uid"]), "hash": content_hash(raw), "hv": 2}
    cur = (await db.execute(select(Message).where(Message.id == message_id).with_for_update())).scalar_one_or_none()
    if cur is None:
        return False
    refs = all_refs(cur.upstream)
    if _key(ref) in {_key(r) for r in refs}:
        return False
    keep = {k: v for k, v in (cur.upstream or {}).items() if k != "removed"}
    cur.upstream = refs_to_upstream([*refs, ref], keep=keep)
    await _settle_failures(db, connector_id, [message_id])
    return True


async def _settle_failures(db: AsyncSession, connector_id: int, message_ids: list[int]) -> None:
    await db.execute(text(
        f"UPDATE jobs SET status = 'done' WHERE kind = '{JOB}' AND status = 'failed' "
        "AND (payload->>'connector_id')::bigint = :c AND (payload->>'message_id')::bigint = ANY(:m)"),
        {"c": connector_id, "m": message_ids})


async def _load(connector_id: int, message_id: int) -> tuple[Connector, User, Message] | None:
    async with session_scope() as db:
        row = await db.get(Connector, connector_id)
        m = await db.get(Message, message_id)
        if row is None or m is None or m.user_id != row.user_id or m.deleted or not active(row):
            return None
        return row, await get_existing(db, User, row.user_id), m


async def save_one(connector_id: int, message_id: int) -> dict[str, Any] | None:
    """Put one sent message in the provider's Sent folder and link it. None: nothing to do (the
    message is gone, or the account does not save sent mail any more)."""
    from mailr.connectors.loader import connector_for
    from mailr.core.store import user_store

    got = await _load(connector_id, message_id)
    if got is None:
        return None
    row, user, m = got
    async with session_scope() as db:
        raw = wire_bytes(await (await user_store(db, user)).get_raw(m))
        conn: Any = await connector_for(db, row)  # an ImapConnector (eligible)
    try:
        async with _MessageLock(user.id, m.message_id_hdr or "") as lock:
            folder = await asyncio.wait_for(conn.sent_folder(), NETWORK_TIMEOUT_S)
            async with session_scope() as db:
                cur = await db.get(Message, message_id)
                if cur is None or cur.deleted or in_folder(cur, row.id, folder):
                    await _settle_failures(db, row.id, [message_id])
                    return {"folder": folder, "existed": True}
            when = m.date or m.received_at
            await lock.ensure()
            copy = await asyncio.wait_for(conn.append_sent(folder, raw, m.message_id_hdr or "", when),
                                          NETWORK_TIMEOUT_S)
            async with session_scope() as db:
                await link(db, message_id, row.id, copy, raw)
                if copy.get("uid") is None:
                    await _settle_failures(db, row.id, [message_id])
    except TimeoutError:
        raise TimeoutError(f"the mail server did not answer within {NETWORK_TIMEOUT_S} s") from None
    finally:
        await conn._close()
    return copy


@handler(JOB)
async def provider_copy_job(payload: dict[str, Any], ctx) -> None:
    copy = await save_one(int(payload["connector_id"]), int(payload["message_id"]))
    if copy is not None and not copy.get("existed"):
        log.info("saved sent message %s in %s of account %s", payload["message_id"], copy.get("folder"),
                 payload["connector_id"])


# ---------------- failures: the health warning and Retry ----------------


async def unsaved(db: AsyncSession, connector_ids: list[int]) -> dict[int, int]:
    """Per account: sent messages whose copy at the provider failed for good (the last FAILED_DAYS)."""
    if not connector_ids:
        return {}
    rows = await db.execute(text(
        f"""SELECT (j.payload->>'connector_id')::bigint, count(DISTINCT m.id) FROM jobs j
             JOIN messages m ON m.id = (j.payload->>'message_id')::bigint AND NOT m.deleted
           WHERE j.kind = '{JOB}' AND j.status = 'failed' AND (j.payload->>'connector_id')::bigint = ANY(:ids)
             AND j.created_at > now() - make_interval(days => :d)
           GROUP BY 1"""), {"ids": list(connector_ids), "d": FAILED_DAYS})
    return {int(c): int(n) for c, n in rows}


async def retry(db: AsyncSession, c: Connector) -> int:
    """Queue the failed copies of this account again (the health warning's Retry). Returns how many."""
    if not active(c):
        return 0
    ids = [int(r[0]) for r in (await db.execute(text(
        f"""SELECT DISTINCT (j.payload->>'message_id')::bigint FROM jobs j
             JOIN messages m ON m.id = (j.payload->>'message_id')::bigint AND NOT m.deleted
           WHERE j.kind = '{JOB}' AND j.status = 'failed' AND (j.payload->>'connector_id')::bigint = :c
             AND j.created_at > now() - make_interval(days => :d)"""), {"c": c.id, "d": FAILED_DAYS})).all()]
    if not ids:
        return 0
    await _settle_failures(db, c.id, ids)
    for mid in ids:
        await enqueue(db, JOB, {"connector_id": c.id, "message_id": mid}, user_id=c.user_id,
                      dedupe_key=f"{JOB}:{c.id}:{mid}", max_attempts=SENT_COPY_ATTEMPTS)
    return len(ids)


# ---------------- backfill ----------------


class BackfillError(Exception):
    pass


async def candidates(db: AsyncSession, c: Connector, since: date | None, after: int = 0) -> list[tuple[int, int]]:
    """(outbox id, message id) of the mail sent from an identity of this account, still in mailr's
    Sent, oldest first, after outbox id `after` and since `since` (a date, inclusive)."""
    sent = (await db.execute(select(Label.id).where(Label.user_id == c.user_id, Label.role == "sent"))).scalar()
    if sent is None:
        return []
    rows = await db.execute(text(
        """SELECT DISTINCT ON (o.id) o.id, m.id FROM outbox o
             JOIN identities i ON i.id = o.identity_id AND i.connector_id = :c
             JOIN messages m ON m.user_id = o.user_id AND m.message_id_hdr = o.sent_message_id
                            AND NOT m.deleted AND NOT m.draft AND :sent = ANY(m.label_ids)
           WHERE o.user_id = :u AND o.status = 'sent' AND o.sent_message_id IS NOT NULL AND o.id > :after
             AND (CAST(:since AS timestamptz) IS NULL OR o.sent_at >= CAST(:since AS timestamptz))
           ORDER BY o.id, m.id"""),
        {"c": c.id, "u": c.user_id, "sent": sent, "after": after,
         "since": datetime(since.year, since.month, since.day, tzinfo=UTC) if since else None})
    return [(int(o), int(m)) for o, m in rows]


def _since(v: Any) -> date | None:
    if not v:
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


async def backfill(user_id: int, connector_id: int, *, since: date | None = None, dry_run: bool = False,
                   state: dict[str, Any] | None = None, save=None, progress=None) -> dict[str, Any]:
    """Copy this account's past sent mail that is missing in the provider's Sent folder. `state`
    {cursor, counts} is where an earlier run stopped; `save(state)` is called after every batch,
    `progress(done, total)` as it goes. Returns the counts: total, present (already at the
    provider), copied (to copy, in a dry run), failed."""
    from mailr.connectors.loader import connector_for
    from mailr.core.store import user_store

    state = dict(state or {})
    counts = {"total": 0, "present": 0, "copied": 0, "failed": 0, **(state.get("counts") or {})}
    async with session_scope() as db:
        row = await db.get(Connector, connector_id)
        if row is None or row.user_id != user_id:
            raise BackfillError("no such account")
        if not eligible(row):
            raise BackfillError("only an IMAP account in mirror mode saves sent mail at the provider")
        user = await get_existing(db, User, user_id)
        cursor = int(state.get("cursor") or 0)
        todo = await candidates(db, row, since, after=cursor)
        done_before = counts["present"] + counts["copied"] + counts["failed"]
        counts["total"] = done_before + len(todo)
        store = await user_store(db, user)
        conn: Any = await connector_for(db, row)  # an ImapConnector (eligible)
    if progress:
        await progress(done_before, counts["total"])
    if not todo:
        return counts
    try:
        folder = await asyncio.wait_for(conn.sent_folder(), NETWORK_TIMEOUT_S)
        _uv, there = await asyncio.wait_for(conn.message_ids(folder), NETWORK_TIMEOUT_S * 5)
        for i in range(0, len(todo), BACKFILL_BATCH):
            batch = todo[i:i + BACKFILL_BATCH]
            async with session_scope() as db:
                msgs = {m.id: m for m in (await db.execute(select(Message).where(
                    Message.id.in_([mid for _o, mid in batch])))).scalars()}
            for _oid, mid in batch:
                m = msgs.get(mid)
                if m is None or m.deleted:
                    counts["present"] += 1  # gone meanwhile: nothing to copy
                    continue
                msgid = m.message_id_hdr or ""
                if in_folder(m, row.id, folder):
                    counts["present"] += 1
                    continue
                if msgid in there:
                    counts["present"] += 1
                    if not dry_run:
                        # At the provider already (the provider's own copy, another app's, an
                        # earlier run): linked, never appended again.
                        raw = wire_bytes(await store.get_raw(m))
                        async with session_scope() as db:
                            await link(db, mid, row.id, {"folder": folder, "uidvalidity": _uv, "uid": there[msgid]}, raw)
                    continue
                if dry_run:
                    counts["copied"] += 1
                    continue
                try:
                    raw = wire_bytes(await store.get_raw(m))
                    async with _MessageLock(user_id, msgid):
                        copy = await asyncio.wait_for(
                            conn.append_sent(folder, raw, msgid, m.date or m.received_at, check=False),
                            NETWORK_TIMEOUT_S)
                        async with session_scope() as db:
                            await link(db, mid, row.id, copy, raw)
                    if copy.get("uid") is not None:
                        there[msgid] = int(copy["uid"])
                    counts["copied"] += 1
                except Retry:
                    counts["failed"] += 1
                except Exception as e:
                    from mailr.connectors.imap import _transient

                    if _transient(e) or isinstance(e, TimeoutError):
                        raise  # the server or the network is down: the job tries the rest later
                    log.warning("backfill of account %s: message %s not copied: %s", row.id, mid, e)
                    counts["failed"] += 1
            state["cursor"] = batch[-1][0]
            state["counts"] = counts
            if save and not dry_run:
                await save(state)
            if progress:
                await progress(counts["present"] + counts["copied"] + counts["failed"], counts["total"])
    finally:
        await conn._close()
    return counts


async def start_backfill(db: AsyncSession, c: Connector, *, since: date | None, dry_run: bool) -> int:
    """Queue a backfill or a dry run (one per account at a time). Returns the job id; a run already
    queued or running for this account is returned instead of a new one."""
    running = (await db.execute(select(Job.id).where(
        Job.kind == BACKFILL_JOB, Job.status.in_(["queued", "running"]),
        Job.payload["connector_id"].astext == str(c.id)).order_by(Job.id.desc()).limit(1))).scalar()
    if running is not None:
        return int(running)
    return await enqueue_returning(db, BACKFILL_JOB, {"connector_id": c.id, "dry_run": bool(dry_run),
                                                      "since": since.isoformat() if since else None},
                                   user_id=c.user_id, max_attempts=5)


async def _job_payload(job_id: int, patch: dict[str, Any]) -> None:
    async with session_scope() as db:
        await db.execute(text("UPDATE jobs SET payload = coalesce(payload, '{}') || CAST(:p AS jsonb) WHERE id=:id"),
                         {"p": json.dumps(patch, default=str), "id": job_id})


@handler(BACKFILL_JOB)
async def backfill_job(payload: dict[str, Any], ctx) -> None:
    job_id = ctx.job_id

    async def save(state: dict[str, Any]) -> None:
        await _job_payload(job_id, {"state": state})

    async def progress(done: int, total: int) -> None:
        async with session_scope() as db:
            await db.execute(text("UPDATE jobs SET progress=:d WHERE id=:id"), {"d": done, "id": job_id})
        await _job_payload(job_id, {"total": total})

    async with session_scope() as db:
        uid = (await get_existing(db, Job, job_id)).user_id or 0
    try:
        counts = await backfill(uid, int(payload["connector_id"]), since=_since(payload.get("since")),
                                dry_run=bool(payload.get("dry_run")), state=payload.get("state"),
                                save=save, progress=progress)
    except BackfillError as e:
        await _job_payload(job_id, {"result": {"error": str(e)}})
        return
    await _job_payload(job_id, {"result": counts})


def job_out(j: Job) -> dict[str, Any]:
    p = j.payload or {}
    result = p.get("result")
    status = j.status
    if status == "done" and isinstance(result, dict) and result.get("error"):
        status = "failed"
    return {"job": j.id, "status": status, "dry_run": bool(p.get("dry_run")), "since": p.get("since"),
            "done": j.progress or 0, "total": p.get("total"),
            "result": result if isinstance(result, dict) and not result.get("error") else None,
            "error": (result or {}).get("error") if isinstance(result, dict) and result.get("error")
            else (j.error.splitlines()[0][:300] if j.status == "failed" and j.error else ""),
            "created_at": j.created_at.isoformat() if j.created_at else None}
