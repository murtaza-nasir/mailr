"""Connector jobs: mirror write-back, store-mode retention (upstream deletion), sync-now."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text

from mailr.config import get_settings
from mailr.connectors.base import WriteBack
from mailr.connectors.imap import all_refs, refs_to_upstream
from mailr.connectors.loader import connector_for
from mailr.core.ingest import my_addresses
from mailr.core.jobs import CHANNELS, Retry, handler
from mailr.db import session_scope
from mailr.models import Connector, Label, Message, User

log = logging.getLogger("mailr.connector.tasks")

RETENTION_BATCH = 500


LOCK_WAIT_S = 5  # a job that cannot get the account's lock goes back to the queue
NETWORK_TIMEOUT_S = 180  # one write-back / folder operation against the mail server


class _ConnectorLock:
    """Session advisory lock (own connection) serialising write-backs per connector, so two
    jobs never work from the same snapshot of a message's upstream refs.

    It never waits for long: while another job holds it, this job is re-queued (Retry) instead
    of occupying a worker slot. (30 Sep 2026: one hung Gmail call held the lock and three
    waiting write-backs filled all four worker slots, so no other job ran for hours.)"""

    def __init__(self, cid: int):
        self.cid = cid
        self.conn = None

    async def __aenter__(self):
        import asyncio

        import asyncpg

        dsn = get_settings().database_url.replace("postgresql+asyncpg", "postgresql")
        self.conn = await asyncpg.connect(dsn)
        for _ in range(LOCK_WAIT_S):
            if await self.conn.fetchval("SELECT pg_try_advisory_lock(727200, $1)", self.cid):
                return self
            await asyncio.sleep(1)
        await self.conn.close()
        raise Retry(delay=60)

    async def ensure(self) -> None:
        """Before each change at the provider: the lock's connection is alive, so the lock is still
        held. When it dropped, the lock went with it and another job may work on the account: this
        job stops before the change and runs again later."""
        try:
            if self.conn is not None and not self.conn.is_closed():
                await self.conn.fetchval("SELECT 1")
                return
        except Exception:  # noqa: BLE001, S110  fail closed below
            pass
        log.warning("connector %s: the write lock's connection was lost; the job stops and runs again", self.cid)
        raise Retry(delay=60)

    async def __aexit__(self, *exc):
        try:
            await self.conn.close()
        except Exception:  # noqa: BLE001, S110
            pass


def _ref_key(r: dict) -> tuple:
    return (str(r.get("connector")), r.get("folder"), r.get("uidvalidity"), r.get("uid"))


@handler("connector.write_back")
async def write_back(payload: dict[str, Any], ctx) -> None:
    """Mirror mode: reflect a local action upstream. payload: {connector_id, message_ids, change}
    where change has add_labels / remove_labels (role or label names), seen, flagged, delete."""
    change = payload.get("change") or {}
    async with _ConnectorLock(int(payload["connector_id"])) as lock:
        async with session_scope() as db:
            row = await db.get(Connector, payload["connector_id"])
            if row is None or row.mode != "mirror" or not row.enabled:
                return
            # The message may have arrived through another account first; this account then
            # holds a copy (upstream.copies), and the copy gets the change too.
            msgs = [m for m in (await db.execute(select(Message).where(
                Message.id.in_(payload["message_ids"]), Message.user_id == row.user_id))).scalars()
                if m.connector_id == row.id or any(str(r.get("connector")) == str(row.id) for r in all_refs(m.upstream))]
            if not msgs:
                return
            conn = await connector_for(db, row)
            snapshots = {m.id: dict(m.upstream or {}) for m in msgs}
            owner = await db.get(User, row.user_id) if any(m.sent_by_me for m in msgs) else None
            mine = await my_addresses(db, owner) if owner is not None else set()
            role_of = dict((await db.execute(select(Label.id, Label.role).where(
                Label.user_id == row.user_id, Label.role.is_not(None)))).all())
            items = []
            for m in msgs:
                add = [x for x in (change.get("add_labels") or []) if x not in PLACEMENT]
                remove = [x for x in (change.get("remove_labels") or []) if x not in PLACEMENT]
                if not change.get("delete") and row.kind == "imap":
                    # Placement (Inbox / Archive / Trash / Spam) is written back as STATE, not as the
                    # delta that was queued: jobs for one account may run out of order (archive then
                    # undo), and the message's current local placement is what upstream must match.
                    want = {role_of[i] for i in (m.label_ids or []) if role_of.get(i) in PLACEMENT}
                    have = await _upstream_placement(conn, snapshots[m.id], row.id)
                    add += sorted(want - have)
                    remove += sorted(have - want)
                else:
                    add += [x for x in (change.get("add_labels") or []) if x in PLACEMENT]
                    remove += [x for x in (change.get("remove_labels") or []) if x in PLACEMENT]
                items.append(WriteBack(upstream=snapshots[m.id], add_labels=add, remove_labels=remove,
                                       seen=change.get("seen"), flagged=change.get("flagged"),
                                       delete=bool(change.get("delete")), message_id=m.message_id_hdr,
                                       content_hash=m.content_hash, mailr_id=m.id, received=_received(m, mine)))
        # Network work happens outside any transaction, and never waits forever.
        import asyncio

        await lock.ensure()
        try:
            new_refs = await asyncio.wait_for(conn.write_back(items), NETWORK_TIMEOUT_S)
        except TimeoutError:
            # Counts as an attempt (the queue's backoff grows, and the job ends after
            # WRITE_BACK_ATTEMPTS): a Retry never used one up, so a server that hangs on every
            # call looped every two minutes forever.
            log.warning("write-back to connector %s timed out; retrying later", row.id)
            raise TimeoutError(f"the mail server did not answer within {NETWORK_TIMEOUT_S} s") from None
        if not new_refs:
            return
        async with session_scope() as db:
            for m, up in zip(msgs, new_refs, strict=False):
                if up is None:
                    continue
                # Only this account's refs come back from write_back; refs of other accounts stay as they are.
                mine = str(row.id)
                before = {_ref_key(r) for r in all_refs(snapshots[m.id]) if str(r.get("connector")) == mine}
                after_refs = [r for r in all_refs(up) if str(r.get("connector")) == mine]
                after = {_ref_key(r) for r in after_refs}
                removed, added = before - after, [r for r in after_refs if _ref_key(r) not in before]
                # The label folders mailr wrote the message to (ImapConnector.write_back): the only
                # folders an untracked copy is ever looked for in.
                lc_before = set(snapshots[m.id].get("label_copies") or [])
                lc_after = set(up.get("label_copies") or []) if "label_copies" in up else lc_before
                if not removed and not added and lc_before == lc_after:
                    continue
                # Merge into the CURRENT refs (a sync may have recorded new copies meanwhile).
                cur = (await db.execute(select(Message).where(Message.id == m.id).with_for_update())).scalar_one()
                refs = [r for r in all_refs(cur.upstream) if _ref_key(r) not in removed] + added
                new_up = refs_to_upstream(refs, keep=cur.upstream)
                lc = (set((cur.upstream or {}).get("label_copies") or []) | (lc_after - lc_before)) - (lc_before - lc_after)
                new_up.pop("label_copies", None)
                if lc:
                    new_up["label_copies"] = sorted(lc)
                await db.execute(text("UPDATE messages SET upstream = CAST(:u AS jsonb) WHERE id = :id"),
                                 {"u": _json(new_up), "id": m.id})


PLACEMENT = ("inbox", "archive", "trash", "junk")


def _received(m: Message, mine: set[str]) -> bool:
    """The message reached the user as mail: not sent by them, or sent to one of their own
    addresses (its delivered copy then sits beside the Sent copy; WriteBack.received)."""
    if not m.sent_by_me:
        return True
    rcpt = {str(a.get("email", "")).lower() for a in [*(m.to or []), *(m.cc or []), *(m.bcc or [])]
            if isinstance(a, dict)}
    return bool(rcpt & mine)


def _like_escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _messages_with_folder_refs(db, user_id: int, cid: int, folder: str) -> list[Message]:
    """Messages of the user holding a ref of this connector to `folder` or one of its subfolders,
    as their primary ref or as a copy on ANOTHER account's message. The text match only
    narrows the rows; callers compare each ref's folder exactly."""
    needle = "%" + _like_escape(json.dumps(folder, ensure_ascii=False)[1:-1]) + "%"
    return list((await db.execute(select(Message).where(
        Message.user_id == user_id, Message.deleted.is_(False),
        text("((messages.upstream->>'connector') = :c OR messages.upstream ? 'copies') "
             "AND messages.upstream::text LIKE :needle ESCAPE '\\'"),
    ).params(c=str(cid), needle=needle))).scalars())


def _renamed(folder: str | None, old: str, new: str, delim: str) -> str | None:
    if folder == old:
        return new
    if folder and folder.startswith(old + delim):
        return new + folder[len(old):]
    return None


async def _rename_refs(user_id: int, cid: int, old: str, new: str, delim: str) -> int:
    """After an upstream RENAME, point this connector's refs to `old` (and its subfolders, by
    the server's hierarchy delimiter) at the new name. "Work" never touches "Workshop"."""
    n = 0
    async with session_scope() as db:
        for m in await _messages_with_folder_refs(db, user_id, cid, old):
            up = dict(m.upstream or {})
            changed = False
            if str(up.get("connector")) == str(cid) and (f := _renamed(up.get("folder"), old, new, delim)):
                up["folder"], changed = f, True
            copies = []
            for c in up.get("copies", []) or []:
                if str(c.get("connector")) == str(cid) and (f := _renamed(c.get("folder"), old, new, delim)):
                    c, changed = {**c, "folder": f}, True
                copies.append(c)
            if changed:
                if "copies" in up:
                    up["copies"] = copies
                await db.execute(text("UPDATE messages SET upstream = CAST(:u AS jsonb) WHERE id = :id"),
                                 {"u": _json(up), "id": m.id})
                n += 1
    return n


@handler("connector.folder_op")
async def folder_op(payload: dict[str, Any], ctx) -> None:
    """Mirror mode: a label was renamed or deleted in mailr; do the same to the upstream folder so
    the next sync does not bring the old label back. A folder made in an account's group is
    created on that account's server ("create"). payload: {connector_id, op, old, new | names | name}.

    Delete never loses mail: a message whose only upstream copy is in the doomed folder is first
    copied to where mailr has it (INBOX if it is in the Inbox locally, otherwise Archive)."""
    cid = int(payload["connector_id"])
    async with _ConnectorLock(cid) as lock:
        async with session_scope() as db:
            row = await db.get(Connector, cid)
            if row is None or row.mode != "mirror" or not row.enabled or row.kind not in ("imap", "gmail"):
                return
            conn = await connector_for(db, row)
            inbox_id = (await db.execute(select(Label.id).where(Label.user_id == row.user_id,
                                                                 Label.role == "inbox"))).scalar()
        await lock.ensure()
        try:
            if payload["op"] == "rename":
                res = await conn.rename_folder(payload["old"], payload["new"])
                if res:
                    old_f, new_f = res
                    try:
                        delim = next((d for n, _, d in await conn.folders() if n == new_f and d), None) or "/"
                    except Exception:  # noqa: BLE001  the rename is done; "/" is the common delimiter
                        delim = "/"
                    await _rename_refs(row.user_id, cid, old_f, new_f, delim)
            elif payload["op"] == "create":
                # A folder made in this account's group of the navigation (labels.home_account_id).
                await conn.folder_for(("label", payload["name"]), create=True)
            elif payload["op"] == "delete":
                for name in payload["names"]:
                    found = await conn.folder_uids(name)
                    if not found:
                        continue
                    folder, uv, uids = found
                    async with session_scope() as db:
                        msgs = await _messages_with_folder_refs(db, row.user_id, cid, folder)
                    known = {int(r["uid"]) for m in msgs for r in all_refs(m.upstream)
                             if r.get("connector") == cid and r.get("folder") == folder
                             and int(r.get("uidvalidity") or 0) == uv}
                    if set(uids) - known and row.kind == "imap":
                        # Something arrived there that mailr has not synced yet: never delete
                        # unseen mail. The job retries after the next sync has recorded it.
                        raise Retry(delay=300)
                    rescue = []
                    alive_cache: dict[tuple[str, int], set[int] | None] = {}
                    for m in msgs:
                        refs = [r for r in all_refs(m.upstream) if r.get("connector") == cid]
                        here = [r for r in refs if r.get("folder") == folder and int(r.get("uidvalidity") or 0) == uv]
                        if not here or row.kind != "imap":
                            continue
                        # Only copies that verifiably exist elsewhere count: refs can be stale
                        # (servers may change UIDVALIDITY on RENAME), and a stale "other copy"
                        # must never let the only real copy be deleted.
                        others = [r for r in refs if r.get("folder") != folder]
                        alive = False
                        for r in others:
                            k = (r["folder"], int(r.get("uidvalidity") or 0))
                            if k not in alive_cache:
                                alive_cache[k] = await conn.live_uids(r["folder"], k[1])
                            if alive_cache[k] and int(r["uid"]) in alive_cache[k]:
                                alive = True
                                break
                        if not alive:
                            rescue.append((m, here[0]))
                    for m, r in rescue:
                        dest = await conn.folder_for(("role", "inbox" if inbox_id in (m.label_ids or []) else "archive"))
                        new = await conn._copy(r, dest, m.message_id_hdr)
                        if new is None:
                            raise RuntimeError(f"could not rescue message {m.id} from {folder}; folder kept")
                        async with session_scope() as db:
                            cur = await db.get(Message, m.id, with_for_update=True)
                            refs = [x for x in all_refs(cur.upstream) if _ref_key(x) != _ref_key(r)] + [new]
                            await db.execute(text("UPDATE messages SET upstream = CAST(:u AS jsonb) WHERE id = :id"),
                                             {"u": _json(refs_to_upstream(refs, keep=cur.upstream)), "id": m.id})
                    await lock.ensure()
                    await conn.delete_folder(folder)
                    async with session_scope() as db:
                        for m in msgs:
                            cur = await db.get(Message, m.id, with_for_update=True)
                            refs = [x for x in all_refs(cur.upstream) if not (x.get("connector") == cid and x.get("folder") == folder)]
                            await db.execute(text("UPDATE messages SET upstream = CAST(:u AS jsonb) WHERE id = :id"),
                                             {"u": _json(refs_to_upstream(refs, keep=cur.upstream)), "id": m.id})
        finally:
            close = getattr(conn, "_close", None)
            if close:
                await close()


async def _upstream_placement(conn, upstream: dict[str, Any], connector_id: int) -> set[str]:
    """Which placement roles a message currently has upstream, from its recorded copies."""
    folders = {r.get("folder") for r in all_refs(upstream) if r.get("connector") == connector_id}
    out = set()
    for role in PLACEMENT:
        f = await conn.folder_for(("role", role), create=False)
        if f and f in folders:
            out.add(role)
    return out


def _json(v: Any) -> str:
    import json

    return json.dumps(v, default=str)


def _is_primary(up: dict[str, Any] | None, r: dict[str, Any]) -> bool:
    up = up or {}
    return all(str(up.get(k)) == str(r.get(k)) for k in ("connector", "folder", "uidvalidity", "uid"))


def exported_blobs(user_id: int, among: set[str] | None = None) -> set[str]:
    """Blobs present in the (fsynced) Maildir export, and in the backup target if set. With
    `among`, only those blobs are looked up: on a NAS target each file is one round trip, and
    checking the whole export (93,000 files) took 26 s (2 Oct 2026). Nothing while the mail
    export is off (MAILR_MAIL_EXPORT=false): an export left from before is not kept up to date,
    so it proves nothing."""
    from mailr.core.backup import exported_manifest, target_problem, target_root

    if not get_settings().mail_export:
        return set()
    manifest = exported_manifest(user_id)
    if among is not None:
        manifest = {b: rel for b, rel in manifest.items() if b in among}
    tgt = target_root(user_id)
    if tgt is None:
        return set(manifest)
    if target_problem() is not None:
        return set()  # share not mounted: nothing counts as backed up
    return {b for b, rel in manifest.items() if (tgt / rel).is_file()}


async def stored_blobs(db, user_id: int, msgs: list[Message]) -> set[str]:
    """Blobs of these messages whose copy the mail store has: asked of the store by id, and the
    copy it returns has the message's blob (MAILR_STORE_BACKUP_CHECK=false, where nothing else
    stands between the provider's copy and deletion)."""
    from mailr.core.store import user_store
    from mailr.models import User

    if not msgs:
        return set()
    user = await db.get(User, user_id)
    if user is None or not user.stalwart_account_id:
        return set()
    store = await user_store(db, user)
    found = {e["id"]: e.get("blobId") for e in await store.get_emails([m.jmap_id for m in msgs if m.jmap_id])}
    return {m.blob_id for m in msgs if m.jmap_id and m.blob_id and found.get(m.jmap_id) == m.blob_id}


def retention_refs(m: Message, connector_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A message's upstream refs on this connector that retention may delete, and the refs that
    may be deleted only once their upstream bytes are shown to hash to the message.

    Deletable: the primary ref (it is the copy the message was stored from) when its hash is the
    message's; a copy whose own hash (hv 2) is the message's, or that was proven the same message
    re-encoded ("semantic"). A copy without a hash is kept. A copy recorded before Oct 2026 (a
    hash but no hv) carries the stored message's hash whatever it held, so it is checked first.
    A different message that reused the Message-ID is never deleted."""
    ok: list[dict[str, Any]] = []
    verify: list[dict[str, Any]] = []
    for r in all_refs(m.upstream):
        if str(r.get("connector")) != str(connector_id) or r.get("uidvalidity") is None or r.get("uid") is None:
            continue
        if _is_primary(m.upstream, r):
            if not r.get("hash") or not m.content_hash or r["hash"] == m.content_hash:
                ok.append(r)
            continue
        if not r.get("hash"):
            continue
        if r.get("hv") == 2:
            if r["hash"] == m.content_hash or r.get("match") == "semantic":
                ok.append(r)
            continue
        verify.append(r)
    return ok, verify


@handler("connector.retention")
async def retention(payload: dict[str, Any], ctx) -> None:
    """Store mode: delete upstream copies older than the retention window, but only messages
    already written to the Maildir export (a backup that does not depend on mailr). Nothing at
    all while the mail export is off. With MAILR_STORE_BACKUP_CHECK=false the export is not
    consulted: a message counts once its copy is in the mail store (`stored_blobs`)."""
    checked = get_settings().store_backup_check
    if checked and not get_settings().mail_export:
        log.info("retention %s: paused, the mail export is off (MAILR_MAIL_EXPORT=false)", payload["connector_id"])
        return
    async with session_scope() as db:
        row = await db.get(Connector, payload["connector_id"])
        if row is None or row.mode != "store" or not row.delete_upstream or not row.enabled:
            return
        cutoff = datetime.now(UTC) - timedelta(days=max(0, row.retention_days))
        # Walk forward with an id cursor so messages that are not eligible yet (not exported)
        # never pin the batch; deleted rows and drafts are not candidates.
        cursor = int(payload.get("after_id") or 0)
        msgs = list((await db.execute(select(Message).where(
            Message.connector_id == row.id, Message.upstream_deleted_at.is_(None), Message.id > cursor,
            Message.deleted.is_(False), Message.draft.is_(False),
            Message.jmap_id.is_not(None), Message.blob_id.is_not(None),
            # The window counts from when mailr stored the message, so upstream keeps
            # every message for at least retention_days after mailr has it.
            Message.created_at <= cutoff,
            text("(messages.upstream ? 'uid' OR jsonb_array_length(coalesce(messages.upstream->'copies', '[]'::jsonb)) > 0)"),
        ).order_by(Message.id).limit(RETENTION_BATCH))).scalars())
        if checked:
            exported = exported_blobs(row.user_id, {m.blob_id for m in msgs if m.blob_id})
        else:
            exported = await stored_blobs(db, row.user_id, msgs)
        eligible = [m for m in msgs if m.blob_id in exported]
        skipped = len(msgs) - len(eligible)
        last_id = msgs[-1].id if msgs else cursor
        if not eligible:
            if skipped:
                log.info("retention %s: %s messages wait for %s", row.id, skipped,
                         "the next export" if checked else "their copy in the mail store")
            if len(msgs) == RETENTION_BATCH:
                from mailr.core.jobs import enqueue

                await enqueue(db, "connector.retention", {"connector_id": row.id, "after_id": last_id},
                              user_id=row.user_id, run_at=datetime.now(UTC) + timedelta(seconds=5))
            return
        conn = await connector_for(db, row)
    # Deletions at the provider take the account's write lock, as write-backs do, and only while
    # its connection holds it.
    async with _ConnectorLock(row.id) as lock:
        await lock.ensure()
        finished, deleted = await _delete_message_refs(conn, row.id, eligible)
    async with session_scope() as db:
        if finished:
            await db.execute(text("UPDATE messages SET upstream_deleted_at = now() WHERE id = ANY(:ids)"),
                             {"ids": finished})
        row = await db.get(Connector, payload["connector_id"])
        log.info("retention %s: deleted %s upstream (%s refs), %s waiting for export", row.id, len(finished),
                 len(deleted), skipped)
        if len(msgs) == RETENTION_BATCH:
            from mailr.core.jobs import enqueue

            await enqueue(db, "connector.retention", {"connector_id": row.id, "after_id": last_id},
                          user_id=row.user_id, run_at=datetime.now(UTC) + timedelta(seconds=30))


async def _delete_message_refs(conn, connector_id: int, msgs: list[Message]) -> tuple[list[int], list[dict]]:
    """Delete these messages' copies at the provider of this store-mode account: the refs
    `retention_refs` allows, and the older copies whose upstream bytes hash to the message.
    Returns (ids of messages with every such ref deleted, the refs deleted)."""
    by_ref: dict[tuple[str, int, int], int] = {}
    refs = []
    unproven: list[tuple[int, str | None, dict[str, Any]]] = []
    for m in msgs:
        ok, verify = retention_refs(m, connector_id)
        unproven += [(m.id, m.content_hash, r) for r in verify]
        for r in ok:
            refs.append(r)
            by_ref[(r["folder"], int(r["uidvalidity"]), int(r["uid"]))] = m.id
    ref_hashes = getattr(conn, "ref_hashes", None)
    if unproven and ref_hashes is not None:
        # Copies recorded before Oct 2026 carry the stored message's hash whatever they held:
        # their upstream bytes are hashed first.
        got = await ref_hashes([r for _, _, r in unproven])
        for mid, chash, r in unproven:
            key = (r["folder"], int(r["uidvalidity"]), int(r["uid"]))
            if chash and got.get(key) == chash:
                refs.append(r)
                by_ref[key] = mid
    if not refs:
        return [], []
    deleted = await conn.delete_refs(refs)
    done_ids = {by_ref[(r["folder"], int(r["uidvalidity"]), int(r["uid"]))] for r in deleted}
    # A message counts as removed upstream only when all its refs were deleted.
    ref_count: dict[int, int] = {}
    for mid in by_ref.values():
        ref_count[mid] = ref_count.get(mid, 0) + 1
    del_count: dict[int, int] = {}
    for r in deleted:
        mid = by_ref[(r["folder"], int(r["uidvalidity"]), int(r["uid"]))]
        del_count[mid] = del_count.get(mid, 0) + 1
    return [mid for mid in done_ids if del_count.get(mid) == ref_count.get(mid)], deleted


@handler("connector.delete_forever")
async def delete_forever_upstream(payload: dict[str, Any], ctx) -> None:
    """Store mode: the user deleted these messages forever (core/actions.py `_delete_forever`);
    their copies still at this account's provider are deleted too, at once, whatever the
    retention window and whether or not deletion is armed. Queued by that action only: a row
    marked deleted for another reason (a store that lost its mail) never deletes anything here."""
    async with _ConnectorLock(int(payload["connector_id"])) as lock:
        async with session_scope() as db:
            row = await db.get(Connector, payload["connector_id"])
            if row is None or row.mode != "store" or not row.enabled:
                return
            msgs = [m for m in (await db.execute(select(Message).where(
                Message.id.in_(payload["message_ids"]), Message.user_id == row.user_id,
                Message.deleted.is_(True), Message.upstream_deleted_at.is_(None)))).scalars()]
            if not msgs:
                return
            conn = await connector_for(db, row)
        import asyncio

        await lock.ensure()
        try:
            finished, deleted = await asyncio.wait_for(_delete_message_refs(conn, row.id, msgs), NETWORK_TIMEOUT_S)
        except TimeoutError:
            log.warning("delete forever at connector %s timed out; retrying later", row.id)
            raise TimeoutError(f"the mail server did not answer within {NETWORK_TIMEOUT_S} s") from None
        async with session_scope() as db:
            if finished:
                await db.execute(text("UPDATE messages SET upstream_deleted_at = now() WHERE id = ANY(:ids)"),
                                 {"ids": finished})
        log.info("delete forever %s: deleted %s upstream (%s refs)", row.id, len(finished), len(deleted))


@handler("connector.forget_refs")
async def forget_refs(payload: dict[str, Any], ctx) -> None:
    """A mail account was removed: its messages stay in mailr, and its refs leave each
    message's upstream (the primary ref and upstream.copies). A message that another account
    also holds gets that account as its connector, so it is still found by that account's retention."""
    cid = str(int(payload["connector_id"]))
    uid = payload.get("user_id")
    probe_int, probe_str = _json([{"connector": int(cid)}]), _json([{"connector": cid}])
    last = 0
    while True:
        async with session_scope() as db:
            rows = (await db.execute(text(
                """SELECT id, upstream FROM messages WHERE id > :last AND (CAST(:u AS int) IS NULL OR user_id = :u)
                   AND (upstream->>'connector' = :c OR upstream->'copies' @> CAST(:pi AS jsonb)
                        OR upstream->'copies' @> CAST(:ps AS jsonb))
                   ORDER BY id LIMIT 1000"""),
                {"last": last, "u": uid, "c": cid, "pi": probe_int, "ps": probe_str})).all()
            if not rows:
                return
            for mid, up in rows:
                last = mid
                up = up or {}
                refs = [r for r in all_refs(up) if str(r.get("connector")) != cid]
                new = refs_to_upstream(refs, keep=up) if refs else \
                    {k: v for k, v in up.items() if k not in ("connector", "copies", "folder", "uidvalidity", "uid")}
                owner = int(refs[0]["connector"]) if refs and str(refs[0].get("connector") or "").isdigit() else None
                await db.execute(text(
                    "UPDATE messages SET upstream = CAST(:up AS jsonb), "
                    "connector_id = CASE WHEN connector_id IS NULL OR connector_id = :cid THEN :o ELSE connector_id END "
                    "WHERE id = :id"),
                    {"up": _json(new), "o": owner, "cid": int(cid), "id": mid})


# A sync now reaches every worker process: the account syncs in the one that holds its lease
# (connectors/runner.py), which need not be the one that ran the job.
SYNC_CHANNEL = "mailr_connector_sync"


def _sync_requested(_conn, _pid, _channel, payload: str) -> None:
    from mailr.connectors.runner import request_restart

    try:
        request_restart(int(payload))
    except ValueError:
        pass


CHANNELS[SYNC_CHANNEL] = _sync_requested


@handler("connector.sync_now")
async def sync_now(payload: dict[str, Any], ctx) -> None:
    """Every worker's listener, this one's too, restarts its task of the account (request_restart)."""
    async with session_scope() as db:
        await db.execute(text("SELECT pg_notify(:c, :p)"), {"c": SYNC_CHANNEL, "p": str(int(payload["connector_id"]))})


@handler("connector.test")
async def test_job(payload: dict[str, Any], ctx) -> None:
    async with session_scope() as db:
        row = await db.get(Connector, payload["connector_id"])
        if row is None:
            return
        conn = await connector_for(db, row)
    res = await conn.test()
    async with session_scope() as db:
        row = await db.get(Connector, payload["connector_id"])
        if not res.ok:
            row.status, row.status_detail = "error", res.detail
