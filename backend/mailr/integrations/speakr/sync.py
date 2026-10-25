"""The Speakr mirror sync: the `speakr.sync` job and its polling (pull only).

Each pass first reads GET /capabilities and GET /tokens/current into the connector's config
(store.refresh_capabilities); an older Speakr has neither, and every step below then works as
before.

Changes feed (capabilities `changes_feed`): the pass asks GET /recordings/changes for
what changed since the stored cursor (`state.changes_cursor`). Upserts update the mirror, and
tombstones (`delete`, with reason deleted, retention or access_revoked) mark rows deleted. The
first pass, a pass after a 410 cursor_expired, and a pass after "Include recordings shared with
me" changed start without a cursor: Speakr then lists every recording, and a row missing from that
complete list becomes a tombstone, as with polling. The cursor is stored after every answer.

Polling (an older Speakr): GET /recordings is read page by page (100 a page) into the metadata
mirror. Every pass covers the whole list; a recording missing from a complete pass becomes a
tombstone (`deleted_at`), and it returns when a later pass has it again.

A pass is queued every ten minutes per connector (core/tasks.py PERIODIC), at once on "Sync now",
and on each webhook delivery. After each pass, calendar events are suggested for new recordings
(matching.py), and mailr's own event references in Speakr become links first.

Shared recordings: with "Include recordings shared with me" on and a Speakr with
internal sharing, the pass reads `scope=all`; the mirror marks those rows `is_shared` with the
owner's name and whether the share allows edits. Shared recordings start no recording orders.

Each pass also reads the summaries the search index lacks (`index_summaries`): those of new and
changed recordings, then (without the changes feed) a few of the oldest ones again. The summary
text is stored in `speakr_recordings.summary_text` with a full-text index;
transcripts are never stored. The list's date filters (date_from, date_to) are not used: they
filter Speakr's created_at, not the meeting date.

Failures:
- 401: the connector is marked "Token expired or revoked", and no pass is queued until the user
  pastes a new token or presses Sync now.
- 403 insufficient_scope: the token lacks the read scope. The connector shows which token to
  create, as a configuration error (no breaker).
- 429: the job is retried after Retry-After (jobs.Retry, no attempt used).
- 5xx and network failures are counted; after three in a row no pass is queued for 15 minutes,
  and the pause doubles with each further failure up to 6 hours.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from mailr.core.jobs import Retry, enqueue, handler
from mailr.db import session_scope
from mailr.integrations.speakr import client as sc
from mailr.integrations.speakr import store
from mailr.models import Connector, SpeakrRecording

log = logging.getLogger("mailr.speakr")

SYNC_EVERY = timedelta(minutes=10)
BREAKER_AFTER = 3
BREAKER_BASE = timedelta(minutes=15)
BREAKER_MAX = timedelta(hours=6)
SUMMARIES_PER_PASS = 40
RECHECK_PER_PASS = 5
RECHECK_AFTER = timedelta(days=1)
MAX_SUMMARY = 100_000


_NEW_DEFAULTS = {"is_shared": False, "owner_name": "", "can_edit": True, "mailr_refs": []}


def _hash(meta: dict[str, Any]) -> str:
    # Keys added later count only when they differ from their default, so rows hashed before
    # them keep their hash (and their summaries are not read again).
    m = {k: v for k, v in meta.items() if not (k in _NEW_DEFAULTS and v == _NEW_DEFAULTS[k])}
    return hashlib.sha256(json.dumps(m, sort_keys=True, default=str).encode()).hexdigest()


def _scope(conn: Connector) -> str:
    """all when the card's "Include recordings shared with me" is on and Speakr has internal sharing."""
    on = bool((conn.config or {}).get("include_shared"))
    return "all" if on and store.feature(conn, "internal_sharing") else "own"


async def _read_changes(cl: sc.SpeakrClient, conn: Connector, scope: str) -> dict[str, Any]:
    """The changes since the stored cursor (or every recording without one). Returns
    {seen: {rid: meta}, deleted: {rid}, complete, cursor}."""
    st = conn.state or {}
    cursor = st.get("changes_cursor") if st.get("changes_scope", "own") == scope else None
    try:
        return await _drain(cl, cursor, scope)
    except sc.SpeakrCursorExpired:
        log.info("Speakr change cursor expired for connector %s; full pass", conn.id)
        return await _drain(cl, None, scope)


async def _drain(cl: sc.SpeakrClient, cursor: str | None, scope: str) -> dict[str, Any]:
    seen: dict[int, dict[str, Any]] = {}
    deleted: set[int] = set()
    complete = cursor is None
    for _ in range(sc.MAX_PAGES * 2):
        changes, nxt, more = await cl.changes(cursor, scope=scope)
        for c in changes:
            if c.get("type") == "upsert" and isinstance(c.get("recording"), dict):
                try:
                    meta = sc.recording_meta(c["recording"])
                except (KeyError, TypeError, ValueError):
                    continue
                seen[meta["recording_id"]] = meta
                deleted.discard(meta["recording_id"])
            elif c.get("type") == "delete" and isinstance(c.get("id"), int):
                deleted.add(int(c["id"]))
                seen.pop(int(c["id"]), None)
        cursor = nxt or cursor
        if not more:
            break
    return {"seen": seen, "deleted": deleted, "complete": complete, "cursor": cursor}


def _iso(v: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(v) if v else None
    except ValueError:
        return None


async def _fail(conn: Connector, detail: str, *, counts: bool) -> None:
    st = dict(conn.state or {})
    if counts:
        st["fails"] = int(st.get("fails") or 0) + 1
        if st["fails"] >= BREAKER_AFTER:
            span = min(BREAKER_BASE * (2 ** (st["fails"] - BREAKER_AFTER)), BREAKER_MAX)
            until = datetime.now(UTC) + span
            st["breaker_until"] = until.isoformat()
            detail = f"{detail}. Speakr is not responding; Mailr tries again at {until:%H:%M} UTC."
    conn.state = st
    conn.status, conn.status_detail = "error", detail[:500]


async def sync_connector(connector_id: int) -> dict[str, Any]:
    """One full pass for one connector. Returns counts (tests read them)."""
    async with session_scope() as db:
        conn = await db.get(Connector, connector_id)
        if conn is None or conn.kind != store.KIND:
            return {"skipped": "missing"}
        if not conn.enabled:
            return {"skipped": "paused"}
        cl = await store.client_for(db, conn)
        st = dict(conn.state or {})
        st["last_attempt_at"] = datetime.now(UTC).isoformat()
        conn.state = st
        conn.status, conn.status_detail = "syncing", "Syncing recordings…"
        await db.commit()
        seen: dict[int, dict[str, Any]] = {}
        deleted: set[int] = set()
        complete, cursor = True, None
        try:
            await store.refresh_capabilities(conn, cl)
            scope = _scope(conn)
            feed = store.feature(conn, "changes_feed")
            if feed:
                got = await _read_changes(cl, conn, scope)
                seen, deleted, complete, cursor = got["seen"], got["deleted"], got["complete"], got["cursor"]
            else:
                async for d in cl.all_recordings(scope=scope):
                    try:
                        meta = sc.recording_meta(d)
                    except (KeyError, TypeError, ValueError):
                        continue
                    seen[meta["recording_id"]] = meta
        except sc.SpeakrAuthError:
            await store.mark_auth_failed(db, conn)
            return {"error": "auth"}
        except sc.SpeakrScopeError as e:
            conn.status, conn.status_detail = "error", str(e)[:500]
            await db.commit()
            return {"error": "scope"}
        except sc.SpeakrRateLimited as e:
            conn.status, conn.status_detail = "syncing", f"Speakr asked to wait {int(e.retry_after)} s"
            await db.commit()
            raise Retry(delay=e.retry_after) from None
        except sc.SpeakrUnavailable as e:
            await _fail(conn, str(e), counts=True)
            return {"error": "unavailable"}
        except sc.SpeakrError as e:
            await _fail(conn, str(e), counts=True)
            return {"error": "speakr"}

        now = datetime.now(UTC)
        first_pass = not (conn.state or {}).get("last_full_sync")
        completed: list[int] = []  # recordings that became completed in this pass (recording orders)
        existing = {r.recording_id: r for r in (await db.execute(select(SpeakrRecording).where(
            SpeakrRecording.connector_id == conn.id))).scalars()}
        added = changed = removed = 0
        rows: list[SpeakrRecording] = []
        for rid, meta in seen.items():
            h = _hash(meta)
            row = existing.get(rid)
            if row is None:
                row = SpeakrRecording(user_id=conn.user_id, connector_id=conn.id, content_hash=h, synced_at=now, **meta)
                db.add(row)
                rows.append(row)
                added += 1
                if meta["status"] == "completed" and not meta.get("is_shared"):
                    completed.append(rid)
                continue
            if row.status != "completed" and meta["status"] == "completed" and row.deleted_at is None \
                    and not meta.get("is_shared"):
                completed.append(rid)
            if row.content_hash != h or row.deleted_at is not None:
                for k, v in meta.items():
                    setattr(row, k, v)
                row.content_hash, row.deleted_at = h, None
                row.summary_at = None  # the summary is read again below
                store.forget(conn.id, rid)
                changed += 1
            row.synced_at = now
            rows.append(row)
        for rid, row in existing.items():
            gone = (rid not in seen) if complete else (rid in deleted)
            if gone and row.deleted_at is None:
                row.deleted_at = now
                store.forget(conn.id, rid)
                removed += 1
            elif not complete and rid not in seen and row.deleted_at is None:
                rows.append(row)  # unchanged since the cursor; still live
        try:
            indexed = await index_summaries(cl, conn, rows, now, recheck=not feed)
        except sc.SpeakrAuthError:
            await store.mark_auth_failed(db, conn)
            return {"error": "auth"}
        st = dict(conn.state or {})
        for k in ("fails", "breaker_until", "auth_failed"):
            st.pop(k, None)
        live = [r for r in rows if r.deleted_at is None]
        processing = sum(1 for r in live if r.status not in ("completed", "failed", ""))
        st.update(count=len(live), processing=processing, last_full_sync=now.isoformat())
        if feed:
            st.update(changes_cursor=cursor, changes_scope=scope)
        else:
            st.pop("changes_cursor", None)
            st.pop("changes_scope", None)
        conn.state = st
        conn.status = "idle"
        conn.status_detail = f"{len(live)} recordings" + (f", {processing} processing" if processing else "")
        conn.last_sync_at = now
        await db.commit()
    out = {"seen": len(seen), "added": added, "changed": changed, "removed": removed, "feed": feed,
           "complete": complete, **indexed}
    try:
        from mailr.integrations.speakr import matching

        out["suggested"] = await matching.match_connector(connector_id)
    except Exception:
        log.exception("recording matching failed for connector %s", connector_id)
    try:
        from mailr.integrations.speakr import mail

        out["mail_linked"] = await mail.scan_recent(connector_id)
    except Exception:
        log.exception("Speakr mail scan failed for connector %s", connector_id)
    # Recording orders run on recordings that just completed; the first pass of a connector
    # imports history and starts nothing (orders.py, chain guard).
    out["completed"] = [] if first_pass else completed
    if out["completed"]:
        try:
            from mailr.integrations.speakr import orders

            out["orders"] = await orders.on_new_recordings(connector_id, out["completed"])
        except Exception:
            log.exception("recording orders failed for connector %s", connector_id)
    return out


def _when(row: SpeakrRecording) -> datetime:
    return row.meeting_date or row.created_upstream or datetime.min.replace(tzinfo=UTC)


async def index_summaries(cl: sc.SpeakrClient, conn: Connector, rows: list[SpeakrRecording],
                          now: datetime, *, recheck: bool = True) -> dict[str, int]:
    """Read the summaries the search index lacks. A summary is read when
    its recording is new or changed (summary_at NULL, also after a webhook named it), newest first
    and at most SUMMARIES_PER_PASS a pass; then the RECHECK_PER_PASS summaries read longest ago,
    because Speakr can regenerate a summary without changing anything the list shows (not with
    the changes feed, whose upserts include summary changes: `recheck` False). Transcripts
    are never stored. Returns {"indexed": summaries changed, "unindexed": summaries still to read}."""
    live = [r for r in rows if r.deleted_at is None]
    for r in live:
        if not r.has_summary and (r.summary_at is None or r.summary_text):
            r.summary_text, r.summary_hash, r.summary_at = "", "", now
    due = sorted((r for r in live if r.has_summary and r.summary_at is None), key=_when, reverse=True)
    stale = sorted((r for r in live if r.has_summary and r.summary_at is not None
                    and now - r.summary_at > RECHECK_AFTER), key=lambda r: r.summary_at)
    todo = due[:SUMMARIES_PER_PASS] + (stale[:RECHECK_PER_PASS] if recheck else [])
    indexed = read = 0
    for r in todo:
        try:
            text = (await cl.summary(r.recording_id))[:MAX_SUMMARY]
        except (sc.SpeakrRateLimited, sc.SpeakrUnavailable):
            break  # the next pass goes on
        except sc.SpeakrAuthError:
            raise
        except sc.SpeakrError:
            r.summary_at = now  # gone or refused: tried again after RECHECK_AFTER
            continue
        read += 1
        h = hashlib.sha256(text.encode()).hexdigest()
        if h != r.summary_hash:
            r.summary_text, r.summary_hash = text, h
            store.forget(conn.id, r.recording_id)
            indexed += 1
        r.summary_at = now
    return {"indexed": indexed, "unindexed": sum(1 for r in due if r.summary_at is None)}


# ACT speakr.sync @api
@handler("speakr.sync")
async def sync_job(payload: dict[str, Any], ctx) -> None:
    await sync_connector(int(payload["connector_id"]))


async def queue_sync(db, conn: Connector, *, reset: bool = False) -> None:
    """Sync now (the user's click, a new connector, a new token): also clears a 401 or an open breaker."""
    if reset:
        st = dict(conn.state or {})
        for k in ("fails", "breaker_until", "auth_failed"):
            st.pop(k, None)
        conn.state = st
    await enqueue(db, "speakr.sync", {"connector_id": conn.id}, user_id=conn.user_id,
                  dedupe_key=f"speakr.sync:{conn.id}")


async def due_syncs(now: datetime | None = None) -> list[int]:
    """Queue a pass for each enabled connector not synced for ten minutes, unless its token failed
    or its breaker is open. Returns the connector ids queued."""
    now = now or datetime.now(UTC)
    queued: list[int] = []
    async with session_scope() as db:
        rows = (await db.execute(select(Connector).where(Connector.kind == store.KIND,
                                                         Connector.enabled.is_(True)))).scalars().all()
        for c in rows:
            st = c.state or {}
            if st.get("auth_failed"):
                continue
            until = _iso(st.get("breaker_until"))
            if until and until > now:
                continue
            last = _iso(st.get("last_attempt_at")) or c.last_sync_at
            if last and now - last < SYNC_EVERY:
                continue
            await queue_sync(db, c)
            queued.append(c.id)
    return queued
