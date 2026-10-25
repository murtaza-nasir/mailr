"""Keep the index in step with changes made directly in Stalwart (IMAP clients on phones
and desktops). Stalwart's event stream only covers the signed-in account, so mailr polls
Email/changes and Mailbox/changes for each user with the admin service account.

mailr's own writes also show up here. Created ids get a grace period before indexing, so
a message mailr is importing (and has not committed yet) is never indexed twice.

The ids waiting to be indexed are rows of store_sync_pending (`Pending`). A message that cannot be
indexed (a download timeout, a parse error) stays pending and is tried again after a growing wait; after INDEX_ATTEMPTS failures it is tried once a day and counted in the
`store_sync` check (core/health.py, Admin → Diagnostics).

A row and the store message its id names must hold the same blob. They do not when the store was
restored from an older snapshot and handed the id out again to other mail: such a row is marked
deleted like a destroyed one, and the store's message is indexed. `mailr store resync` (`resync`)
forgets the cursors, so the next tick compares the whole mailbox with the index.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy import select, text, update

from mailr.core import threading
from mailr.core.ingest import index_stored
from mailr.core.jmap import JMAPError
from mailr.core.store import user_store
from mailr.db import session_scope
from mailr.models import Label, Message, StoreSyncState, User, light_message

log = logging.getLogger("mailr.storesync")

POLL_SECONDS = 15
GRACE_SECONDS = 45


RECONCILE_PAGE = 5000
# Messages indexed per transaction, and batches per tick for as long as TICK_INDEX_SECONDS lasts: a
# lost cursor on a 500,000-message store drained at 200 per tick (15 s) took about ten hours.
INDEX_PER_TICK = 200
TICK_INDEX_SECONDS = 10.0
# Ids per statement when pending rows are written, and updated messages per query.
PENDING_CHUNK = 5000
UPDATE_CHUNK = 500
INDEX_ATTEMPTS = 8
RETRY_FIRST = 60  # seconds; doubles after each failure, up to RETRY_MAX
RETRY_MAX = 6 * 3600
RETRY_GAVE_UP = 86400
# More rows whose blob differs from the store's than this (and than MISMATCH_SHARE of the mailbox)
# are not treated as gone: that is no restore but a store that names its blobs another way.
MISMATCH_MIN = 50
MISMATCH_SHARE = 0.05


def _ripe(v: Any, now: float) -> bool:
    """A pending entry is due: a first-seen time past the grace period, or a failed one's retry time."""
    if isinstance(v, dict):
        return now >= float(v.get("next") or 0)
    return now - float(v) >= GRACE_SECONDS


def _failed(prev: Any, now: float, error: str) -> dict[str, Any]:
    """The pending entry after another failed attempt to index the message."""
    n = (int(prev.get("n") or 0) if isinstance(prev, dict) else 0) + 1
    wait = RETRY_GAVE_UP if n >= INDEX_ATTEMPTS else min(RETRY_MAX, RETRY_FIRST * 2 ** (n - 1))
    return {"n": n, "next": now + wait, "error": error[:200]}


def _received(e: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(e.get("receivedAt")).replace("Z", "+00:00"))
    except ValueError:
        return None


def _mismatched(rows: dict[str, tuple[str | None, datetime | None]], emails: dict[str, dict[str, Any]]) -> list[str]:
    """Ids whose row is another message than the store's message of that id: another blob and
    another arrival time. Both must differ, so a store that only names its blobs another way never
    makes rows look gone."""
    out = []
    for j, (blob, received) in rows.items():
        e = emails.get(j)
        if not e or not blob or not e.get("blobId") or e["blobId"] == blob:
            continue
        theirs = _received(e)
        if received is None or theirs is None or abs((theirs - received).total_seconds()) > 1:
            out.append(j)
    return out


class Pending:
    """One transaction's view of a user's pending ids (the store_sync_pending rows): the entries
    it loaded (`_ripe` reads them: a first-seen time, or {n, next, error} after a failure), and the
    adds, changes and removals it makes, written by `save` in the same transaction. Used like
    the dict it replaced: setdefault adds an id unless it is pending already, pop removes one."""

    def __init__(self, loaded: dict[str, Any] | None = None):
        self._loaded: dict[str, Any] = dict(loaded or {})
        self._added: dict[str, float] = {}
        self._set: dict[str, dict[str, Any]] = {}
        self._gone: set[str] = set()

    def __contains__(self, jid: str) -> bool:
        return jid in self._loaded or jid in self._added or jid in self._set

    def get(self, jid: str, default: Any = None) -> Any:
        return self._set.get(jid, self._loaded.get(jid, self._added.get(jid, default)))

    def setdefault(self, jid: str, first_seen: float) -> None:
        if jid not in self:
            self._added[jid] = first_seen
            self._gone.discard(jid)

    def pop(self, jid: str, default: Any = None) -> Any:
        v = self.get(jid, default)
        for d in (self._loaded, self._added, self._set):
            d.pop(jid, None)
        self._gone.add(jid)
        return v

    def __setitem__(self, jid: str, entry: dict[str, Any]) -> None:
        self._set[jid] = entry
        self._gone.discard(jid)

    @staticmethod
    async def due(db, user_id: int, now: float, limit: int) -> Pending:
        rows = (await db.execute(text(
            "SELECT jmap_id, next_at, n, error FROM store_sync_pending WHERE user_id = :u AND next_at <= :t "
            "ORDER BY next_at LIMIT :k"), {"u": user_id, "t": now, "k": limit})).all()
        return Pending({j: {"n": n, "next": t, "error": e} if n else t - GRACE_SECONDS for j, t, n, e in rows})

    def ids(self) -> list[str]:
        return list(self._loaded)

    async def save(self, db, user_id: int) -> None:
        gone = sorted(self._gone)
        for i in range(0, len(gone), PENDING_CHUNK):
            await db.execute(text("DELETE FROM store_sync_pending WHERE user_id = :u AND jmap_id = ANY(:j)"),
                             {"u": user_id, "j": gone[i:i + PENDING_CHUNK]})
        added = list(self._added.items())
        for i in range(0, len(added), PENDING_CHUNK):
            part = added[i:i + PENDING_CHUNK]
            await db.execute(text(
                "INSERT INTO store_sync_pending (user_id, jmap_id, next_at) "
                "SELECT :u, j, t FROM unnest(CAST(:j AS varchar[]), CAST(:t AS float8[])) AS x(j, t) "
                "ON CONFLICT (user_id, jmap_id) DO NOTHING"),
                {"u": user_id, "j": [j for j, _ in part], "t": [t + GRACE_SECONDS for _, t in part]})
        for jid, e in self._set.items():
            await db.execute(text(
                "INSERT INTO store_sync_pending (user_id, jmap_id, next_at, n, error) VALUES (:u, :j, :t, :n, :e) "
                "ON CONFLICT (user_id, jmap_id) DO UPDATE SET next_at = :t, n = :n, error = :e"),
                {"u": user_id, "j": jid, "t": float(e["next"]), "n": int(e["n"]), "e": e.get("error") or ""})
        self._added, self._set, self._gone = {}, {}, set()


# Every worker process runs this loop for every user (several worker containers): each transaction
# of a tick holds this lock for its user, and a worker that finds it taken leaves the user to the
# one that holds it until its next tick.
USER_LOCK = 727300


async def _turn(db, user_id: int) -> bool:
    return bool((await db.execute(text("SELECT pg_try_advisory_xact_lock(:l, :u)"),
                                  {"l": USER_LOCK, "u": user_id})).scalar())


class _UserSync:
    """Cursor and the tick's changes to the pending ids are committed together; the state only
    advances with a successful commit, so a crash or failed tick replays the changes. The due ids
    are then indexed a batch per transaction, for up to TICK_INDEX_SECONDS."""

    def __init__(self, user_id: int):
        self.user_id = user_id

    async def tick(self) -> None:
        if not await self._follow():
            return
        deadline = time.monotonic() + TICK_INDEX_SECONDS
        while time.monotonic() < deadline:
            async with session_scope() as db:
                if not await _turn(db, self.user_id):
                    return
                user = await db.get(User, self.user_id)
                if user is None:
                    return
                store = await user_store(db, user)
                now = time.time()
                pending = await Pending.due(db, self.user_id, now, INDEX_PER_TICK)
                ripe = pending.ids()
                if ripe:
                    await self._index(db, store, ripe, pending, now)
                    await pending.save(db, self.user_id)
            if len(ripe) < INDEX_PER_TICK:
                return

    async def _follow(self) -> bool:
        """The store's changes since the cursor: labels and flags, destroyed mail, and new ids
        queued for indexing. False when the user is not synced."""
        async with session_scope() as db:
            if not await _turn(db, self.user_id):
                return False
            user = await db.get(User, self.user_id)
            if user is None or user.disabled or not user.stalwart_account_id:
                return False
            store = await user_store(db, user)
            st = await db.get(StoreSyncState, self.user_id)
            if st is None:
                st = StoreSyncState(user_id=self.user_id)
                db.add(st)
                await db.flush()  # the pending rows refer to it
            pending = Pending()
            now = time.time()
            mb = await store.jmap.one("Mailbox/get", {"ids": [], "properties": ["id"]})
            if mb["state"] != st.mailbox_state:
                await store.sync_labels(db)
                st.mailbox_state = mb["state"]
            if st.email_state is None:
                # First run (or the cursor was lost): take the current state and reconcile the
                # whole mailbox once, so changes made while mailr was not watching are indexed.
                st.email_state = await store.email_state()
                await self._reconcile(db, store, pending, now)
            else:
                try:
                    ch = await store.changes(st.email_state)
                except JMAPError as e:
                    if e.kind != "cannotCalculateChanges":
                        raise
                    st.email_state = await store.email_state()
                    await self._reconcile(db, store, pending, now)
                    ch = None
                if ch is not None:
                    for jid in ch["created"]:
                        pending.setdefault(jid, now)
                    replaced = await self._apply_updates(
                        db, store, ch["updated"] + [j for j in ch["created"] if j not in pending])
                    for jid in replaced:
                        pending.setdefault(jid, now - GRACE_SECONDS)
                    await self._apply_destroyed(db, ch["destroyed"])
                    for jid in ch["destroyed"]:
                        pending.pop(jid, None)
                    st.email_state = ch["state"]
            await pending.save(db, self.user_id)
        return True

    async def _index(self, db, store, ripe: list[str], pending: Pending | dict[str, Any], now: float) -> None:
        """Index the due ids that no row holds. A row that holds one with another blob is stale (the
        store handed the id out again): it is marked deleted and the store's message is indexed. One
        message that cannot be indexed must not stop the account: its own savepoint, and it stays
        pending for a later try (`_failed`)."""
        known = {j: (b, r) for j, b, r in (await db.execute(select(Message.jmap_id, Message.blob_id, Message.received_at)
                                                            .where(Message.user_id == self.user_id,
                                                                   Message.jmap_id.in_(ripe)))).all()}
        found = {e["id"]: e for e in await store.get_emails(ripe)}
        stale = _mismatched(known, found)
        if stale:
            log.warning("user %s: %d index rows name store messages that hold other mail (a restored mail store?); "
                        "marked deleted, the store's messages are indexed", self.user_id, len(stale))
            await self._apply_destroyed(db, stale)
            for j in stale:
                known.pop(j, None)
        for j in ripe:
            e = found.get(j)
            if j in known or e is None:
                pending.pop(j, None)  # indexed already (mailr's own write), or gone again
                continue
            try:
                async with db.begin_nested():
                    raw = await store.jmap.download(e["blobId"], "m.eml", "message/rfc822")
                    await index_stored(db, store, e, raw)
            except Exception as ex:  # noqa: BLE001  retried later; counted in the store_sync check
                entry = _failed(pending.get(j), now, f"{type(ex).__name__}: {ex}")
                pending[j] = entry
                log.warning("user %s: could not index stored message %s (attempt %d): %s", self.user_id, j,
                            entry["n"], entry["error"])
            else:
                pending.pop(j, None)

    async def _reconcile(self, db, store, pending: Pending | dict[str, Any], now: float) -> None:
        """Compare every id in the store with the index: missing ones are queued for
        indexing, index rows whose message vanished from the store are marked deleted."""
        store_ids: set[str] = set()
        position = 0
        total: int | None = None
        while True:
            # A stable order (oldest first, so new mail lands after the last page): pages of an unsorted query may shift
            # while mail arrives, and a message that falls between two pages looked destroyed.
            # Paging stops on an empty page or at the total, never on a page that
            # came back shorter than asked (a server's maxObjectsInGet may be lower).
            r = await store.jmap.one("Email/query", {
                "position": position, "limit": RECONCILE_PAGE, "calculateTotal": True,
                "sort": [{"property": "receivedAt", "isAscending": True}]})
            ids = r.get("ids") or []
            store_ids.update(ids)
            total = r.get("total", total)
            position += len(ids)
            if not ids or (total is not None and position >= total):
                break
        index_rows = {j: (b, r) for j, b, r in (await db.execute(select(
            Message.jmap_id, Message.blob_id, Message.received_at).where(
            Message.user_id == self.user_id, Message.deleted.is_(False), Message.jmap_id.is_not(None)))).all()}
        index_ids = set(index_rows)
        gone = await self._really_gone(store, list(index_ids - store_ids))
        # A row whose id the store holds with another message: the store was restored and gave the
        # id to other mail. The row's own message is gone, and the store's is indexed.
        both = sorted(index_ids & store_ids)
        emails: dict[str, dict[str, Any]] = {}
        for i in range(0, len(both), 500):
            r = await store.jmap.one("Email/get", {"ids": both[i:i + 500],
                                                   "properties": ["id", "blobId", "receivedAt"]})
            emails.update({e["id"]: e for e in r.get("list") or []})
        replaced = _mismatched({j: index_rows[j] for j in both}, emails)
        if len(replaced) > max(MISMATCH_MIN, MISMATCH_SHARE * len(index_ids)):
            log.error("store reconcile for user %s: %d of %d index rows hold another blob than the store's message "
                      "of the same id; too many to be a restore, so none is treated as gone", self.user_id,
                      len(replaced), len(index_ids))
            replaced = []
        gone += replaced
        for i in range(0, len(gone), 500):
            await self._apply_destroyed(db, gone[i:i + 500])
        for jid in (store_ids - index_ids) | set(replaced):
            pending.setdefault(jid, now)
        log.info("store reconcile for user %s: %d to index, %d gone (%d of them with another message at their id)",
                 self.user_id, len((store_ids - index_ids) | set(replaced)), len(gone), len(replaced))

    async def _really_gone(self, store, jids: list[str]) -> list[str]:
        """Of the ids the listing did not show, those the store says it does not have (Email/get
        notFound). A message missed by the paging (a deletion moved the pages meanwhile) stays."""
        out: list[str] = []
        for i in range(0, len(jids), 500):
            chunk = jids[i:i + 500]
            r = await store.jmap.one("Email/get", {"ids": chunk, "properties": ["id"]})
            out += [j for j in (r.get("notFound") or []) if j in set(chunk)]
        return out

    async def _apply_updates(self, db, store, jids: list[str]) -> list[str]:
        """Labels and flags changed in the store. Returns the ids whose row held another blob
        (marked deleted here; the caller queues the store's message for indexing). UPDATE_CHUNK
        ids at a time."""
        replaced: list[str] = []
        for i in range(0, len(jids), UPDATE_CHUNK):
            replaced += await self._apply_update_chunk(db, store, jids[i:i + UPDATE_CHUNK])
        return replaced

    async def _apply_update_chunk(self, db, store, jids: list[str]) -> list[str]:
        if not jids:
            return []
        rows = {m.jmap_id: m for m in (await db.execute(select(Message).options(*light_message()).where(
            Message.user_id == self.user_id, Message.jmap_id.in_(jids)))).scalars()}
        if not rows:
            return []
        jmap_to_label = {lb.jmap_id: lb.id for lb in (await db.execute(
            select(Label).where(Label.user_id == self.user_id))).scalars()}
        touched: set[int] = set()
        emails = await store.get_emails(list(rows))
        replaced = _mismatched({j: (m.blob_id, m.received_at) for j, m in rows.items()}, {e["id"]: e for e in emails})
        for e in emails:
            if e["id"] in replaced:
                continue
            m = rows[e["id"]]
            kws: dict[str, Any] = e.get("keywords") or {}
            labels = sorted(jmap_to_label[j] for j in (e.get("mailboxIds") or {}) if j in jmap_to_label)
            new = {"seen": bool(kws.get("$seen")), "flagged": bool(kws.get("$flagged")),
                   "answered": bool(kws.get("$answered")), "important": bool(kws.get("$important")),
                   "draft": bool(kws.get("$draft"))}
            changed = labels != sorted(m.label_ids or []) or any(getattr(m, k) != v for k, v in new.items())
            if changed:
                m.label_ids = labels
                for k, v in new.items():
                    setattr(m, k, v)
                if m.conversation_id:
                    touched.add(m.conversation_id)
        if touched:
            await db.flush()
            await threading.refresh(db, self.user_id, list(touched))
        if replaced:
            await self._apply_destroyed(db, replaced)
        return replaced

    async def _apply_destroyed(self, db, jids: list[str]) -> None:
        if not jids:
            return
        # One UPDATE that matches on the destroyed id itself: a row a concurrent write points at a
        # new copy (a draft save replaces its copy) no longer matches once that write commits, and
        # keeps its new copy. Loading the rows first and marking them by primary key deleted a
        # draft that had just been saved again.
        gone = (await db.execute(
            update(Message).where(Message.user_id == self.user_id, Message.jmap_id.in_(jids),
                                  Message.deleted.is_(False))
            .values(deleted=True, jmap_id=None)  # ids may be reused by the store
            .returning(Message.id, Message.conversation_id).execution_options(synchronize_session=False))).all()
        if gone:
            # Deleted in the store itself (another mail app, the store's Trash expiry, or a store
            # that lost its mail): only marked here. The daily sweep scrubs them, and holds back
            # when most of a user's mail looks deleted (core/purge.py).
            await threading.refresh(db, self.user_id, list({c for _i, c in gone if c}))


async def resync(user_ids: list[int]) -> int:
    """`mailr store resync`: forget these users' store cursors and pending ids, so the next tick
    compares each whole mailbox with the index (`_reconcile`). Returns how many users had a state."""
    from sqlalchemy import delete

    async with session_scope() as db:
        gone = (await db.execute(delete(StoreSyncState).where(StoreSyncState.user_id.in_(user_ids))
                                 .returning(StoreSyncState.user_id))).scalars().all()
        return len(gone)


async def failed_counts() -> dict[int, int]:
    """{user id: messages that failed to index INDEX_ATTEMPTS times}, for the store_sync check."""
    async with session_scope() as db:
        rows = (await db.execute(text(
            "SELECT user_id, count(*) FROM store_sync_pending WHERE n >= :n GROUP BY user_id"),
            {"n": INDEX_ATTEMPTS})).all()
    return {int(u): int(n) for u, n in rows}


async def supervise() -> None:
    syncs: dict[int, _UserSync] = {}
    while True:
        try:
            async with session_scope() as db:
                ids = (await db.execute(select(User.id).where(User.disabled.is_(False),
                                                              User.stalwart_account_id.is_not(None)))).scalars().all()
            for uid in ids:
                syncs.setdefault(uid, _UserSync(uid))
            for uid in list(syncs):
                if uid not in ids:
                    syncs.pop(uid)
            for s in syncs.values():
                try:
                    await s.tick()
                except Exception as e:  # noqa: BLE001  the tick rolled back; it replays next time
                    log.warning("store sync for user %s failed: %s", s.user_id, e)
        except Exception:
            log.exception("store sync loop error")
        await asyncio.sleep(POLL_SECONDS)
