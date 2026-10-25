"""Connector supervisor (runs inside the worker process).

One asyncio task per enabled mail connector. The supervisor reloads the connector table
every few seconds: new connectors start, disabled or deleted ones stop, and a changed
config, mode, or secret restarts the task. A crashed task restarts with backoff.

`RunnerContext` implements the SyncContext the connectors use. Every database touch opens
its own short transaction; nothing holds a transaction across network waits.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text, update

from mailr.connectors.base import REGISTRY, SyncContext
from mailr.connectors.imap import all_refs, refs_to_upstream
from mailr.connectors.loader import _load_all, connector_for
from mailr.core import threading
from mailr.core.events import notify
from mailr.core.ingest import IngestOptions, apply_provider_state, ingest_raw
from mailr.core.store import user_store
from mailr.db import session_scope
from mailr.models import Connector, Label, Message, User

log = logging.getLogger("mailr.runner")

MAIL_KINDS = {"imap", "gmail", "file"}
RELOAD_SECONDS = 10
_initial_global = asyncio.Semaphore(3)
_initial_per_user: dict[int, asyncio.Semaphore] = {}


class LeaseLost(BaseException):
    """The connection holding this connector's lease went away, and with it the lock: another
    worker may run the connector now. The sync stops before its next change and takes the lease
    again (`_run_connector`). A BaseException, so the per-message error handling of the
    connectors (a message that fails is retried, then skipped) never takes it for a bad message."""


class RunnerContext(SyncContext):
    def __init__(self, connector_id: int, user_id: int, state: dict[str, Any]):
        self.connector_id = connector_id
        self.user_id = user_id
        self.state = state or {}
        self.stopping = asyncio.Event()
        self.lease: _Lease | None = None  # set by the runner; tests run without one

    async def ensure_lease(self) -> None:
        """Before every change a sync makes (an ingest, a state or flag update, a vanish, the
        cursor): the lease must still be held, or LeaseLost stops the sync first."""
        if self.lease is not None:
            await self.lease.ensure()

    async def ingest(self, raw: bytes, **kw: Any) -> dict[str, Any]:
        await self.ensure_lease()
        category = kw.pop("category", None)
        async with session_scope() as db:
            row = await db.get(Connector, self.connector_id)
            user = await db.get(User, self.user_id)
            store = await user_store(db, user)
            res = await ingest_raw(db, store, raw, IngestOptions(connector=row, **kw))
            m = res.message
            if kw.get("state_authoritative") and (not res.duplicate or (
                    kw.get("merge_provider_state") and m.local_change_at is None)):
                # Provider state applies to a message the import creates, and during the first
                # import to one mailr already had and never changed.
                await apply_provider_state(db, store, m, roles=kw.get("roles", []),
                                           label_names=kw.get("label_names", []),
                                           keywords=kw.get("keywords", {}), category=category,
                                           provider=row.name)
            elif category and not res.duplicate and not m.category:
                m.category = None if category == "primary" else category
                await db.flush()
                if m.conversation_id:
                    await threading.refresh(db, self.user_id, [m.conversation_id])
            return {"message_id": m.id, "duplicate": res.duplicate, "conversation_id": m.conversation_id}

    async def save_state(self, state: dict[str, Any]) -> None:
        await self.ensure_lease()
        self.state = state
        async with session_scope() as db:
            await db.execute(update(Connector).where(Connector.id == self.connector_id)
                             .values(state=json.loads(json.dumps(state, default=str))))

    async def set_status(self, status: str, detail: str = "", synced: bool = False) -> None:
        async with session_scope() as db:
            before = (await db.execute(select(Connector.status, Connector.status_detail)
                                       .where(Connector.id == self.connector_id))).first()
            vals: dict[str, Any] = {"status": status, "status_detail": detail[:2000]}
            if synced:
                vals["last_sync_at"] = datetime.now(UTC)
            await db.execute(update(Connector).where(Connector.id == self.connector_id).values(**vals))
            await notify(db, self.user_id, {"type": "connector", "id": self.connector_id, "status": status,
                                            "detail": detail[:300]})
            # The provider refused the sign-in: tell the owner once (core/accounthealth.py).
            from mailr.core import accounthealth

            await accounthealth.on_status(db, self.connector_id, self.user_id,
                                          (before[0], before[1]) if before else (None, None), status, detail)

    async def known_upstream(self, folder: str, uidvalidity: int, min_uid: int | None = None) -> set[int]:
        """UIDs of this folder already stored. The primary ref is served by the expression
        index ix_msg_upstream_ref (and bounded by min_uid on the IDLE hot path); secondary
        copies are only looked up among messages that have copies."""
        params = {"u": self.user_id, "c": str(self.connector_id), "f": folder, "v": int(uidvalidity),
                  "min": int(min_uid or 0)}
        async with session_scope() as db:
            primary = await db.execute(text(
                """
                SELECT (upstream->>'uid')::bigint FROM messages
                WHERE upstream ? 'uid' AND (upstream->>'connector') = :c AND (upstream->>'folder') = :f
                  AND ((upstream->>'uid')::bigint) >= :min AND user_id = :u AND NOT deleted
                  AND (upstream->>'uidvalidity')::bigint = :v
                """), params)
            copies = await db.execute(text(
                """
                SELECT (c->>'uid')::bigint FROM messages m,
                  jsonb_array_elements(m.upstream->'copies') c
                WHERE m.user_id = :u AND m.upstream ? 'copies' AND NOT m.deleted
                  AND c->>'connector' = :c AND c->>'folder' = :f AND (c->>'uidvalidity')::bigint = :v
                  AND (c->>'uid')::bigint >= :min
                """), params)
            return {int(r[0]) for r in primary} | {int(r[0]) for r in copies}

    async def forget_uidvalidity(self, folder: str, old_uidvalidity: int) -> int:
        """A folder's UIDVALIDITY changed: its old refs point at nothing. Drop them so no job
        ever acts on them (the rescan re-links messages by Message-ID)."""
        await self.ensure_lease()
        async with session_scope() as db:
            # This account's refs may also sit as copies on another account's message.
            rows = (await db.execute(text(
                """SELECT id FROM messages WHERE user_id=:u AND NOT deleted
                   AND ((connector_id=:cid AND upstream->>'folder' = :f)
                        OR upstream->'copies' @> CAST(:pi AS jsonb) OR upstream->'copies' @> CAST(:ps AS jsonb))"""),
                {"u": self.user_id, "cid": self.connector_id, "f": folder,
                 "pi": json.dumps([{"connector": int(self.connector_id), "folder": folder}]),
                 "ps": json.dumps([{"connector": str(self.connector_id), "folder": folder}])})).scalars().all()
            n = 0
            for mid in rows:
                m = await db.get(Message, mid)
                refs = all_refs(m.upstream)
                keep = [r for r in refs if not (r.get("folder") == folder and int(r.get("uidvalidity") or -1) == int(old_uidvalidity)
                                                and str(r.get("connector")) == str(self.connector_id))]
                if len(keep) != len(refs):
                    m.upstream = refs_to_upstream(keep, keep=m.upstream)
                    n += 1
            return n

    async def _messages_for_uids(self, db, folder: str, uv: int, uids: list[int]) -> list[tuple[Message, int]]:
        rows = await db.execute(text(
            """
            SELECT m.id, (r->>'uid')::bigint AS uid FROM messages m,
              LATERAL (SELECT m.upstream AS r UNION ALL
                       SELECT c FROM jsonb_array_elements(coalesce(m.upstream->'copies', '[]'::jsonb)) c) refs
            WHERE m.user_id = :u AND NOT m.deleted
                  AND (m.connector_id = :cid OR m.upstream ? 'copies') AND r->>'connector' = :c AND r->>'folder' = :f
              AND (r->>'uidvalidity')::bigint = :v AND (r->>'uid')::bigint = ANY(:uids)
            """), {"u": self.user_id, "c": str(self.connector_id), "cid": self.connector_id, "f": folder, "v": int(uv), "uids": uids})
        out = []
        for mid, uid in rows:
            m = await db.get(Message, mid)
            if m is not None:
                out.append((m, int(uid)))
        return out

    async def apply_remote(self, folder: str, uidvalidity: int, updates: dict[int, dict[str, Any]],
                           since: datetime | None = None) -> None:
        """Mirror mode: upstream flag (and Gmail label) state → mailr.

        mailr is the store of record. `since` is the start of the previous pass:
        the upstream changes in `updates` happened after it. A message that mailr changed at or
        after that time (local_change_at) keeps mailr's state, and so does a message with a
        write-back to this account still queued; that write-back brings upstream in line.
        Gmail label state reconciles only the placement roles and the labels the account has
        (`label_state["mapped"]`); a label that exists only in mailr, such as Later, stays."""
        await self.ensure_lease()
        async with session_scope() as db:
            user = await db.get(User, self.user_id)
            store = await user_store(db, user)
            touched: set[int] = set()
            pending = await _pending_write_backs(db, self.connector_id)
            for m, uid in await self._messages_for_uids(db, folder, uidvalidity, list(updates)):
                if m.id in pending:
                    continue
                if m.local_change_at is not None and (since is None or m.local_change_at >= since):
                    continue
                u = updates[uid]
                flags = {k: bool(u[k]) for k in ("seen", "flagged", "answered", "important") if k in u}
                changed = {k: v for k, v in flags.items() if getattr(m, k) != v}
                if changed:
                    await store.set_keywords([m], **changed)
                    touched.add(m.conversation_id)
                ls = u.get("label_state")
                if ls is not None:
                    ids = set(await _label_ids(db, store, self.user_id, ls["roles"], ls["label_names"]))
                    keep_local = await _local_only_labels(db, self.user_id, m)
                    owned = await _mirror_owned_labels(db, self.user_id, ls.get("mapped"))
                    current = set(m.label_ids or [])
                    target = sorted((current - (owned - keep_local)) | ids)
                    if target != sorted(current):
                        await store.replace_labels([m], target, db)
                        touched.add(m.conversation_id)
                        # Moved to Spam upstream: the classifier's labels come off (labels never
                        # apply to spam; intel/classify.py withdraw_labels).
                        from mailr.intel.classify import withdraw_if_moved_to_spam

                        await withdraw_if_moved_to_spam(db, store, [m], {m.id: current})
            await db.flush()
            await threading.refresh(db, self.user_id, [c for c in touched if c])

    async def apply_provider(self, folder: str, uidvalidity: int, updates: dict[int, dict[str, Any]]) -> int:
        """Import-mode reconcile: make already-imported messages match the provider's recorded
        state (see ingest.apply_provider_state). Returns the number of messages changed."""
        await self.ensure_lease()
        n = 0
        async with session_scope() as db:
            row = await db.get(Connector, self.connector_id)
            user = await db.get(User, self.user_id)
            store = await user_store(db, user)
            for m, uid in await self._messages_for_uids(db, folder, uidvalidity, list(updates)):
                if m.local_change_at is not None:
                    continue  # changed in mailr: mailr's state stands
                u = updates[uid]
                if await apply_provider_state(db, store, m, roles=u["roles"], label_names=u["label_names"],
                                              keywords=u["keywords"], category=u.get("category"),
                                              provider=row.name):
                    n += 1
        return n

    async def vanished(self, folder: str, uidvalidity: int, uids: list[int], target: tuple[str, str]) -> None:
        """Mirror mode: messages that left an upstream folder lose that folder's label. A
        message with no ref left in this account is NOT trashed right away: it may have been
        moved to a folder that has not been synced yet (or mailr's own archive write-back is
        still recording the new ref). It is marked orphaned; `finalize_orphans` trashes it only
        if a full pass over every folder did not find it again."""
        await self.ensure_lease()
        async with session_scope() as db:
            user = await db.get(User, self.user_id)
            store = await user_store(db, user)
            touched: set[int] = set()
            for m, uid in await self._messages_for_uids(db, folder, uidvalidity, uids):
                refs = [r for r in all_refs(m.upstream)
                        if not (str(r.get("connector")) == str(self.connector_id) and r.get("folder") == folder
                                and int(r.get("uid") or -1) == uid)]
                ids = await _label_ids(db, store, self.user_id,
                                       [target[1]] if target[0] == "role" else [],
                                       [target[1]] if target[0] == "label" else [])
                mine = [r for r in refs if str(r.get("connector")) == str(self.connector_id)]
                up = refs_to_upstream(refs, keep=m.upstream)
                if not mine:
                    up = {**up, "orphan": {"connector": self.connector_id, "since": datetime.now(UTC).isoformat()}}
                m.upstream = up
                if ids:
                    await store.set_labels([m], add=[], remove=ids, db=db)
                touched.add(m.conversation_id)
            await db.flush()
            await threading.refresh(db, self.user_id, [c for c in touched if c])

    async def finalize_orphans(self, pass_started: datetime) -> int:
        """After a complete pass over every synced folder: messages still orphaned since
        before the pass exist nowhere upstream any more, so they go to mailr's Trash
        (never deleted outright)."""
        await self.ensure_lease()
        async with session_scope() as db:
            rows = (await db.execute(select(Message).where(
                Message.user_id == self.user_id, Message.deleted.is_(False),
                Message.upstream["orphan"]["connector"].astext == str(self.connector_id)))).scalars().all()
            if not rows:
                return 0
            user = await db.get(User, self.user_id)
            store = await user_store(db, user)
            trash = await store.role_label(db, "trash")
            n = 0
            for m in rows:
                since = datetime.fromisoformat(m.upstream["orphan"]["since"])
                if since >= pass_started:
                    continue
                if any(str(r.get("connector")) == str(self.connector_id) for r in all_refs(m.upstream)):
                    m.upstream = {k: v for k, v in m.upstream.items() if k != "orphan"}
                    continue
                if m.local_change_at is not None and m.local_change_at >= since:
                    # Changed in mailr after it went missing upstream: mailr's state stands.
                    m.upstream = {k: v for k, v in m.upstream.items() if k != "orphan"}
                    continue
                await store.replace_labels([m], [trash.id], db)
                m.upstream = {k: v for k, v in m.upstream.items() if k != "orphan"}
                n += 1
            await db.flush()
            await threading.refresh(db, self.user_id, list({m.conversation_id for m in rows if m.conversation_id}))
            return n


async def _label_ids(db, store, user_id: int, roles: list[str], names: list[str]) -> list[int]:
    labels = list((await db.execute(select(Label).where(Label.user_id == user_id))).scalars())
    by_role = {lb.role: lb.id for lb in labels if lb.role}
    by_name = {lb.name.lower(): lb.id for lb in labels}
    out = [by_role[r] for r in roles if r in by_role]
    for n in names:
        if n.lower() in by_name:
            out.append(by_name[n.lower()])
        else:
            out.append((await store.create_label(db, n)).id)
    return out


# Placement roles that follow a mirror's label state. Sent and Drafts are never taken away.
_MIRROR_ROLES = ("inbox", "archive", "trash", "junk")


async def _mirror_owned_labels(db, user_id: int, mapped: list[str] | None) -> set[int]:
    """Labels that follow a mirror account: the placement roles, plus the user labels
    the account also has (by name, case-insensitive). Others exist only in mailr."""
    names = {n.lower() for n in mapped or []}
    rows = await db.execute(select(Label.id, Label.role, Label.name).where(Label.user_id == user_id))
    return {i for i, role, name in rows if role in _MIRROR_ROLES or (not role and name.lower() in names)}


async def _pending_write_backs(db, connector_id: int) -> set[int]:
    """Messages with a write-back to this connector the provider has not confirmed: still queued or
    running (retrying through an outage), or given up on in the last 30 days. Their local state
    stands; the provider's older state is not applied over it."""
    rows = await db.execute(text(
        """SELECT payload->'message_ids' FROM jobs WHERE kind = 'connector.write_back'
           AND (payload->>'connector_id')::int = :c
           AND (status IN ('queued', 'running')
                OR (status = 'failed' AND created_at > now() - interval '30 days'))"""), {"c": connector_id})
    return {int(x) for (ids,) in rows for x in (ids or [])}


async def _local_only_labels(db, user_id: int, m: Message) -> set[int]:
    """Labels kept when mirroring the provider's label state: those the provider cannot hold
    (drafts) and labels mailr applied itself (filters, the classifier) that were never
    written back, so an unrelated upstream flag change does not silently revert them."""
    from mailr.models import LabelDecision

    rows = await db.execute(select(Label.id).where(Label.user_id == user_id, Label.role.in_(("drafts",))))
    keep = {r[0] for r in rows}
    applied = await db.execute(select(LabelDecision.target).where(
        LabelDecision.message_id == m.id, LabelDecision.kind == "label", LabelDecision.value.is_(True),
        LabelDecision.source.in_(("rule", "classifier")), LabelDecision.status == "applied"))
    keep |= {int(t) for (t,) in applied if str(t).isdigit()}
    return set(m.label_ids or []) & keep


def _fingerprint(c: Connector) -> str:
    h = hashlib.sha256()
    h.update(json.dumps([c.kind, c.mode, c.config, c.preset, c.data_class], sort_keys=True, default=str).encode())
    h.update(c.secrets_enc or b"")
    return h.hexdigest()


class _Running:
    def __init__(self, task: asyncio.Task, ctx: RunnerContext, fp: str):
        self.task, self.ctx, self.fp = task, ctx, fp


_running: dict[int, _Running] = {}
_wake = asyncio.Event()


def request_restart(connector_id: int) -> None:
    """Called by the connector.sync_now job: restart the task so it syncs immediately."""
    r = _running.get(connector_id)
    if r is not None:
        r.fp = "restart"
    _wake.set()


class _Locks:
    """One Postgres connection per worker process that holds the session-level advisory locks of
    all its running connectors. A connection per connector held one of Postgres's slots for each
    mail account, outside every pool (deploy/docker-compose.yml counts the connections).

    It fails closed: when the connection is found closed, or a query on it fails, every lock it
    held is gone at once (another worker may take those connectors now), so every lease taken on
    it counts as lost (`generation`), and their syncs stop before their next change (LeaseLost)."""

    def __init__(self) -> None:
        self.conn = None
        self.held: set[int] = set()
        self.generation = 0
        self.mutex = asyncio.Lock()

    async def _lost(self, why: str) -> None:
        if self.held:
            log.warning("connector lock connection lost (%s): %s connector leases are lost", why, len(self.held))
        await self._close()
        self.generation += 1

    async def _connection(self):
        import asyncpg

        from mailr.config import get_settings

        if self.conn is not None and self.conn.is_closed():
            await self._lost("closed")
        if self.conn is None:
            dsn = get_settings().database_url.replace("postgresql+asyncpg", "postgresql")
            self.conn = await asyncpg.connect(dsn)
        return self.conn

    async def acquire(self, cid: int) -> int | None:
        """The generation the lock was taken in, or None when it is held (here or elsewhere)."""
        async with self.mutex:
            if cid in self.held:  # advisory locks nest inside one session: never twice here
                return None
            conn = await self._connection()
            try:
                ok = await conn.fetchval("SELECT pg_try_advisory_lock(727100, $1)", cid)
            except Exception as e:  # noqa: BLE001  the connection broke: every lock on it is gone
                await self._lost(type(e).__name__)
                return None
            if ok:
                self.held.add(cid)
                return self.generation
            return None

    async def holds(self, cid: int, generation: int) -> bool:
        """The lock taken in `generation` is still held: the same connection, alive (one query)."""
        async with self.mutex:
            if generation != self.generation or cid not in self.held:
                return False
            if self.conn is None or self.conn.is_closed():
                await self._lost("closed")
                return False
            try:
                await self.conn.fetchval("SELECT 1")
            except Exception as e:  # noqa: BLE001  fail closed
                await self._lost(type(e).__name__)
                return False
            return True

    async def release(self, cid: int, generation: int | None = None) -> None:
        async with self.mutex:
            if cid not in self.held or (generation is not None and generation != self.generation):
                return
            self.held.discard(cid)
            if self.conn is None:
                return
            try:
                await self.conn.fetchval("SELECT pg_advisory_unlock(727100, $1)", cid)
            except Exception as e:  # noqa: BLE001  a lost connection released it already
                await self._lost(type(e).__name__)

    async def _close(self) -> None:
        if self.conn is not None:
            try:
                await self.conn.close()
            except Exception:  # noqa: BLE001, S110
                pass
        self.conn = None
        self.held.clear()


_locks = _Locks()


class _Lease:
    """Session-level Postgres advisory lock (on the process's shared lock connection, `_Locks`):
    only one worker process runs a given connector at a time (overlapping deploys, a second worker)."""

    def __init__(self, cid: int, locks: _Locks | None = None):
        self.cid = cid
        self.locks = locks or _locks
        self.generation: int | None = None

    async def acquire(self) -> bool:
        self.generation = await self.locks.acquire(self.cid)
        return self.generation is not None

    async def ensure(self) -> None:
        if self.generation is None or not await self.locks.holds(self.cid, self.generation):
            self.generation = None
            raise LeaseLost(f"connector {self.cid}: the lease was lost")

    async def release(self) -> None:
        if self.generation is not None:
            await self.locks.release(self.cid, self.generation)
        self.generation = None


async def _run_connector(cid: int) -> None:
    lease = _Lease(cid)
    while True:
        while not await lease.acquire():
            log.info("connector %s is running in another worker; waiting", cid)
            await asyncio.sleep(30)
        try:
            await _run_connector_leased(cid, lease)
            return
        except LeaseLost:
            # Stopped before its next change; another worker may hold the connector now, so it
            # runs again only once the lease is taken anew.
            log.warning("connector %s: the lease was lost; the sync stopped and waits for the lease again", cid)
        finally:
            await lease.release()


async def _run_connector_leased(cid: int, lease: _Lease | None = None) -> None:
    backoff = 5
    while True:
        async with session_scope() as db:
            row = await db.get(Connector, cid)
            if row is None or not row.enabled:
                return
            conn = await connector_for(db, row)
            ctx = _running[cid].ctx if cid in _running else RunnerContext(cid, row.user_id, dict(row.state or {}))
            ctx.state = dict(row.state or {})
            ctx.lease = lease
            uid = row.user_id
        try:
            if not ctx.state.get("initial_done") and hasattr(conn, "sync_once") and row.kind != "file":
                sem = _initial_per_user.setdefault(uid, asyncio.Semaphore(1))
                await ctx.set_status("syncing", "Waiting for other imports to finish…")
                async with sem, _initial_global:
                    await conn.sync_once(ctx)
            await conn.run(ctx)
            return
        except (asyncio.CancelledError, LeaseLost):
            raise
        except Exception as e:
            log.exception("connector %s crashed", cid)
            try:
                await ctx.set_status("error", f"{type(e).__name__}: {e}"[:500])
            except Exception:  # noqa: BLE001, S110
                pass
            try:
                await asyncio.wait_for(ctx.stopping.wait(), timeout=backoff)
                return
            except TimeoutError:
                backoff = min(backoff * 2, 600)


async def _stop(cid: int) -> None:
    r = _running.pop(cid, None)
    if r is None:
        return
    r.ctx.stopping.set()
    try:
        await asyncio.wait_for(asyncio.shield(r.task), timeout=15)
    except (TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
        r.task.cancel()


async def supervise() -> None:
    _load_all()
    while True:
        try:
            async with session_scope() as db:
                rows = (await db.execute(select(Connector).where(Connector.kind.in_(MAIL_KINDS & set(REGISTRY))))).scalars().all()
                want = {c.id: c for c in rows if c.enabled}
                paused = [c.id for c in rows if not c.enabled and c.status != "paused"]
                if paused:
                    await db.execute(update(Connector).where(Connector.id.in_(paused))
                                     .values(status="paused", status_detail="Paused"))
            for cid in list(_running):
                if cid not in want or _running[cid].fp != _fingerprint(want[cid]) or _running[cid].task.done():
                    await _stop(cid)
            for cid, c in want.items():
                if cid not in _running:
                    ctx = RunnerContext(cid, c.user_id, dict(c.state or {}))
                    _running[cid] = _Running(None, ctx, _fingerprint(c))  # type: ignore[arg-type]
                    _running[cid].task = asyncio.create_task(_run_connector(cid), name=f"connector-{cid}")
        except Exception:
            log.exception("connector supervisor error")
        _wake.clear()
        try:
            await asyncio.wait_for(_wake.wait(), timeout=RELOAD_SECONDS)
        except TimeoutError:
            pass
