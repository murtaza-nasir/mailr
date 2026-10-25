"""The user's mail store in Stalwart, reached over JMAP.

Labels are Stalwart mailboxes (a JMAP Email can be in several mailboxes at once, which is
exactly Gmail's label model). Flags are JMAP keywords; importance is the `$important`
keyword so it survives an export. Categories, snooze, and the conversation overlay live
only in mailr's index.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from mailr.config import get_settings
from mailr.core.jmap import JMAPClient, JMAPError
from mailr.models import Label, Message, User, light_message

log = logging.getLogger("mailr.store")

SYSTEM_ROLES = {
    "inbox": "Inbox",
    "sent": "Sent",
    "drafts": "Drafts",
    "junk": "Spam",
    "trash": "Trash",
    "archive": "Archive",
}
KEYWORDS = {"seen": "$seen", "flagged": "$flagged", "answered": "$answered", "draft": "$draft",
            "important": "$important"}


class _BlobCache:
    """Small LRU of raw messages so opening a thread does not refetch every body."""

    def __init__(self, max_bytes: int = 64 * 1024 * 1024):
        self.max = max_bytes
        self.size = 0
        self.items: OrderedDict[str, bytes] = OrderedDict()

    def get(self, k: str) -> bytes | None:
        v = self.items.get(k)
        if v is not None:
            self.items.move_to_end(k)
        return v

    def put(self, k: str, v: bytes) -> None:
        if len(v) > self.max // 4:
            return
        old = self.items.pop(k, None)
        if old is not None:
            self.size -= len(old)
        self.items[k] = v
        self.size += len(v)
        while self.size > self.max and self.items:
            _, old = self.items.popitem(last=False)
            self.size -= len(old)


_cache = _BlobCache()


class _Scoped:
    """JMAP calls pinned to one account (the admin service account may address any)."""

    def __init__(self, client: JMAPClient, account_id: str):
        self.client = client
        self.acc = account_id

    async def one(self, method: str, args: dict[str, Any], using: list[str] | None = None) -> dict:
        return await self.client.one(method, {"accountId": self.acc, **args}, using=using)

    async def upload(self, data: bytes, content_type: str = "message/rfc822") -> dict[str, Any]:
        return await self.client.upload(self.acc, data, content_type)

    async def download(self, blob_id: str, name: str = "blob", type_: str = "application/octet-stream") -> bytes:
        return await self.client.download(self.acc, blob_id, name, type_)


class UserStore:
    def __init__(self, user: User, client: JMAPClient):
        if not user.stalwart_account_id:
            raise RuntimeError(f"user {user.id} has no mail store account")
        self.user = user
        self.jmap = _Scoped(client, user.stalwart_account_id)

    # ----------------- labels / mailboxes -----------------

    async def sync_labels(self, db: AsyncSession) -> list[Label]:
        """Mirror Stalwart mailboxes into the labels table and create missing system ones."""
        res = await self.jmap.one("Mailbox/get", {"ids": None, "properties": ["id", "name", "parentId", "role", "sortOrder"]})
        boxes = {b["id"]: b for b in res["list"]}
        present_roles = {b.get("role") for b in boxes.values() if b.get("role")}
        missing = {r: n for r, n in SYSTEM_ROLES.items() if r not in present_roles}
        if missing:
            create = {}
            for role, name in missing.items():
                # A mailbox with that name may exist without a role; adopt it.
                existing = next((b for b in boxes.values() if b["name"].lower() == name.lower() and not b.get("parentId")), None)
                if existing:
                    await self.jmap.one("Mailbox/set", {"update": {existing["id"]: {"role": role}}})
                    existing["role"] = role
                else:
                    create[role] = {"name": name, "role": role, "parentId": None}
            if create:
                r = await self.jmap.one("Mailbox/set", {"create": create})
                for role, made in (r.get("created") or {}).items():
                    boxes[made["id"]] = {"id": made["id"], "name": SYSTEM_ROLES[role], "role": role, "parentId": None}
                for role, err in (r.get("notCreated") or {}).items():
                    log.warning("could not create %s mailbox: %s", role, err)

        def path(b: dict) -> str:
            parts = [b["name"]]
            seen = {b["id"]}
            while b.get("parentId") and b["parentId"] in boxes and b["parentId"] not in seen:
                b = boxes[b["parentId"]]
                seen.add(b["id"])
                parts.append(b["name"])
            return "/".join(reversed(parts))

        existing = {lb.jmap_id: lb for lb in (await db.execute(select(Label).where(Label.user_id == self.user.id))).scalars()}
        by_name = {lb.name.lower(): lb for lb in existing.values()}
        out = []
        for b in boxes.values():
            name = SYSTEM_ROLES.get(b.get("role") or "", None) or path(b)
            lb = existing.get(b["id"]) or by_name.get(name.lower())
            if lb is None:
                lb = Label(user_id=self.user.id, jmap_id=b["id"], name=name, role=b.get("role"),
                           sort=b.get("sortOrder") or 0)
                db.add(lb)
            else:
                lb.jmap_id, lb.role = b["id"], b.get("role")
                if not lb.role:
                    lb.name = name
            out.append(lb)
        # Labels whose mailbox vanished (deleted from an IMAP client) are removed.
        for jid, lb in existing.items():
            if jid and jid not in boxes:
                await db.delete(lb)
        await db.flush()
        return out

    async def create_label(self, db: AsyncSession, name: str, color: str | None = None) -> Label:
        name = "/".join(p.strip() for p in name.split("/") if p.strip())
        if not name:
            raise ValueError("empty label name")
        labels = {lb.name.lower(): lb for lb in (await db.execute(select(Label).where(Label.user_id == self.user.id))).scalars()}
        if name.lower() in labels:
            return labels[name.lower()]
        parent_id = None
        parts = name.split("/")
        for i in range(len(parts) - 1):
            parent_path = "/".join(parts[: i + 1])
            parent = labels.get(parent_path.lower())
            if parent is None:
                parent = await self.create_label(db, parent_path)
                labels[parent_path.lower()] = parent
            parent_id = parent.jmap_id
        r = await self.jmap.one("Mailbox/set", {"create": {"n": {"name": parts[-1], "parentId": parent_id}}})
        made = (r.get("created") or {}).get("n")
        if made:
            jid = made["id"]
        else:
            err = (r.get("notCreated") or {}).get("n") or {}
            if err.get("type") != "alreadyExists" or not err.get("existingId"):
                raise JMAPError("mailbox", r.get("notCreated"))
            jid = err["existingId"]  # created concurrently (another worker, an IMAP client)
            other = (await db.execute(select(Label).where(Label.user_id == self.user.id,
                                                          Label.jmap_id == jid))).scalar_one_or_none()
            if other is not None:
                return other
        return await self._upsert_label(db, name, jid, color)

    async def _upsert_label(self, db: AsyncSession, name: str, jid: str, color: str | None) -> Label:
        """INSERT … ON CONFLICT: two transactions creating the same label (two connectors,
        an overlapping worker) converge on one row instead of failing."""
        from sqlalchemy.dialects.postgresql import insert

        stmt = insert(Label).values(user_id=self.user.id, jmap_id=jid, name=name, color=color,
                                    show_in_list="show", show_in_message_list=True, sort=0)
        stmt = stmt.on_conflict_do_update(index_elements=["user_id", "name"],
                                          set_={"jmap_id": stmt.excluded.jmap_id}).returning(Label.id)
        lid = (await db.execute(stmt)).scalar_one()
        lb = await db.get(Label, lid)
        await db.refresh(lb)
        return lb

    async def rename_label(self, db: AsyncSession, lb: Label, new_name: str) -> None:
        if lb.role:
            raise ValueError("system labels cannot be renamed")
        parts = [p.strip() for p in new_name.split("/") if p.strip()]
        parent_jmap = None
        if len(parts) > 1:
            parent = await self.create_label(db, "/".join(parts[:-1]))
            parent_jmap = parent.jmap_id
        await self.jmap.one("Mailbox/set", {"update": {lb.jmap_id: {"name": parts[-1], "parentId": parent_jmap}}})
        old = lb.name
        lb.name = "/".join(parts)
        # Children keep their mailbox parent; rename their paths in the index.
        children = (await db.execute(select(Label).where(Label.user_id == self.user.id,
                                                         Label.name.startswith(old + "/", autoescape=True)))).scalars()
        for ch in children:
            ch.name = lb.name + ch.name[len(old):]
        await db.flush()

    async def delete_label(self, db: AsyncSession, lb: Label) -> None:
        """Delete a label (and nested labels). Messages keep their other labels; messages
        whose only mailbox was this one move to Archive so they are never lost."""
        if lb.role:
            raise ValueError("system labels cannot be deleted")
        victims = [lb] + list((await db.execute(select(Label).where(
            Label.user_id == self.user.id, Label.name.startswith(lb.name + "/", autoescape=True)))).scalars())
        victim_ids = [v.id for v in victims]
        # 1. Take every message out of the victim mailboxes (set_labels re-homes messages
        #    that would be left with no mailbox to Archive, so nothing is ever lost).
        msgs = (await db.execute(select(Message).options(*light_message()).where(
            Message.user_id == self.user.id, Message.label_ids.overlap(victim_ids)))).scalars().all()
        for i in range(0, len(msgs), 200):
            await self.set_labels(msgs[i:i + 200], add=[], remove=victim_ids, db=db)
        still = [m.id for m in msgs if set(m.label_ids or []) & set(victim_ids)]
        if still:
            raise JMAPError("mailbox", f"{len(still)} message(s) could not be moved out of the label")
        # 2. Destroy the now-empty mailboxes, children first, and check the result.
        for v in sorted(victims, key=lambda x: -x.name.count("/")):
            if not v.jmap_id:
                continue
            r = await self.jmap.one("Mailbox/set", {"destroy": [v.jmap_id], "onDestroyRemoveEmails": False})
            bad = (r.get("notDestroyed") or {}).get(v.jmap_id)
            if bad and bad.get("type") != "notFound":
                raise JMAPError("mailbox", f"could not delete {v.name}: {bad.get('type')} {bad.get('description', '')}")
        for v in victims:
            await db.delete(v)
        await db.flush()

    async def role_label(self, db: AsyncSession, role: str) -> Label:
        lb = (await db.execute(select(Label).where(Label.user_id == self.user.id, Label.role == role))).scalar_one_or_none()
        if lb is None:
            await self.sync_labels(db)
            lb = (await db.execute(select(Label).where(Label.user_id == self.user.id, Label.role == role))).scalar_one()
        return lb

    async def _jmap_ids(self, db: AsyncSession, label_ids: list[int]) -> dict[int, str]:
        if not label_ids:
            return {}
        rows = await db.execute(select(Label.id, Label.jmap_id).where(Label.user_id == self.user.id, Label.id.in_(label_ids)))
        return {r[0]: r[1] for r in rows}

    # ----------------- messages -----------------

    async def import_message(
        self, db: AsyncSession, raw: bytes, label_ids: list[int], keywords: dict[str, bool],
        received_at: datetime | None,
    ) -> dict[str, Any]:
        up = await self.jmap.upload(raw)
        jids = await self._jmap_ids(db, label_ids)
        if not jids:
            jids = {0: (await self.role_label(db, "archive")).jmap_id}
        args: dict[str, Any] = {
            "blobId": up["blobId"],
            "mailboxIds": {j: True for j in jids.values()},
            "keywords": {k: True for k, v in keywords.items() if v},
        }
        if received_at:
            args["receivedAt"] = received_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = await self.jmap.one("Email/import", {"emails": {"e": args}})
        made = (r.get("created") or {}).get("e")
        if not made:
            err = (r.get("notCreated") or {}).get("e") or {}
            if err.get("type") == "alreadyExists" and err.get("existingId"):
                made = {"id": err["existingId"], "blobId": up["blobId"]}
                got = await self.jmap.one("Email/get", {"ids": [made["id"]], "properties": ["threadId", "blobId"]})
                if got["list"]:
                    made.update(got["list"][0])
            else:
                raise JMAPError("import", err)
        if (made.get("blobId") or up["blobId"]) == up["blobId"]:
            _cache.put(up["blobId"], raw)  # never cache these bytes under another message's blob
        return {"jmap_id": made["id"], "thread_id": made.get("threadId"), "blob_id": made.get("blobId") or up["blobId"]}

    async def set_labels(self, msgs: list[Message], add: list[int], remove: list[int], db: AsyncSession) -> None:
        """Add/remove labels. The index changes only for messages Stalwart actually updated,
        so the two never drift apart. The current labels are read again under a row lock, so a
        copy loaded earlier (a job that ran a model in between) never writes back labels the
        user changed meanwhile."""
        jids = await self._jmap_ids(db, list(set(add) | set(remove)))
        fresh = await _locked_labels(db, [m.id for m in msgs if m.jmap_id and m.id is not None])
        updates: dict[str, Any] = {}
        planned: list[tuple[Message, list[int]]] = []
        arch: Label | None = None  # looked up once, when the first message needs it
        for m in msgs:
            if not m.jmap_id:
                continue
            if m.id in fresh and fresh[m.id] != list(m.label_ids or []) \
                    and not inspect(m).attrs.label_ids.history.has_changes():
                set_committed_value(m, "label_ids", fresh[m.id])
            patch: dict[str, Any] = {}
            current = set(m.label_ids or [])
            m_add = set(add)
            final = (current | m_add) - set(remove)
            if not final:
                # Never leave a message with no mailbox: it would vanish from IMAP clients.
                if arch is None:
                    arch = await self.role_label(db, "archive")
                    jids[arch.id] = arch.jmap_id
                final = {arch.id}
                m_add.add(arch.id)
            for lid in m_add:
                if lid in jids and lid not in current:
                    patch[f"mailboxIds/{jids[lid]}"] = True
            for lid in remove:
                if lid in jids and lid in current and lid not in m_add:
                    patch[f"mailboxIds/{jids[lid]}"] = None
            if patch:
                updates[m.jmap_id] = patch
                planned.append((m, sorted(final)))
        failed = await self._set(updates)
        for m, final in planned:
            if m.jmap_id not in failed:
                m.label_ids = final

    async def replace_labels(self, msgs: list[Message], label_ids: list[int], db: AsyncSession) -> None:
        jids = await self._jmap_ids(db, label_ids)
        if not jids:
            arch = await self.role_label(db, "archive")
            jids = {arch.id: arch.jmap_id}
        updates = {m.jmap_id: {"mailboxIds": {j: True for j in jids.values()}} for m in msgs if m.jmap_id}
        failed = await self._set(updates)
        for m in msgs:
            if m.jmap_id and m.jmap_id not in failed:
                m.label_ids = sorted(jids)

    async def set_keywords(self, msgs: list[Message], **flags: bool) -> None:
        updates: dict[str, Any] = {}
        planned: list[tuple[Message, dict[str, bool]]] = []
        for m in msgs:
            patch = {}
            changes = {}
            for name, value in flags.items():
                kw = KEYWORDS[name]
                if getattr(m, name) != value:
                    patch[f"keywords/{kw}"] = True if value else None
                    changes[name] = value
            if patch and m.jmap_id:
                updates[m.jmap_id] = patch
                planned.append((m, changes))
        failed = await self._set(updates)
        for m, changes in planned:
            if m.jmap_id not in failed:
                for k, v in changes.items():
                    setattr(m, k, v)

    async def _set(self, updates: dict[str, Any]) -> set[str]:
        """Email/set in chunks; returns the ids Stalwart did NOT update."""
        failed: set[str] = set()
        items = list(updates.items())
        for i in range(0, len(items), 200):
            chunk = dict(items[i: i + 200])
            if not chunk:
                continue
            r = await self.jmap.one("Email/set", {"update": chunk})
            bad = r.get("notUpdated") or {}
            if bad:
                log.warning("Email/set notUpdated: %s", list(bad.items())[:3])
                failed.update(bad)
        return failed

    async def destroy(self, msgs: list[Message]) -> set[str]:
        """Destroy messages; returns ids that could not be destroyed (notFound counts as done)."""
        failed: set[str] = set()
        ids = [m.jmap_id for m in msgs if m.jmap_id]
        for i in range(0, len(ids), 200):
            r = await self.jmap.one("Email/set", {"destroy": ids[i: i + 200]})
            for jid, err in (r.get("notDestroyed") or {}).items():
                if (err or {}).get("type") != "notFound":
                    failed.add(jid)
        if failed:
            log.warning("Email/set notDestroyed: %s", sorted(failed)[:5])
        return failed

    async def get_raw(self, m: Message) -> bytes:
        if not m.blob_id:
            raise JMAPError("blob", "message has no blob")
        cached = _cache.get(m.blob_id)
        if cached is not None:
            return cached
        data = await self.jmap.download(m.blob_id, "message.eml", "message/rfc822")
        _cache.put(m.blob_id, data)
        return data

    async def email_state(self) -> str:
        r = await self.jmap.one("Email/get", {"ids": [], "properties": ["id"]})
        return r["state"]

    async def changes(self, since: str) -> dict[str, Any]:
        """Email/changes, following hasMoreChanges."""
        created, updated, destroyed = [], [], []
        state = since
        while True:
            r = await self.jmap.one("Email/changes", {"sinceState": state, "maxChanges": 500})
            created += r.get("created", [])
            updated += r.get("updated", [])
            destroyed += r.get("destroyed", [])
            state = r["newState"]
            if not r.get("hasMoreChanges"):
                break
        return {"created": created, "updated": updated, "destroyed": destroyed, "state": state}

    async def get_emails(self, ids: list[str]) -> list[dict[str, Any]]:
        out = []
        for i in range(0, len(ids), 200):
            r = await self.jmap.one("Email/get", {"ids": ids[i: i + 200],
                                                  "properties": ["id", "blobId", "threadId", "mailboxIds", "keywords", "receivedAt", "size"]})
            out += r["list"]
        return out


_service: JMAPClient | None = None


def service_client() -> JMAPClient:
    """The shared JMAP client authenticated as mailr's Stalwart admin service account."""
    global _service
    if _service is None:
        st = get_settings()
        _service = JMAPClient(st.stalwart_url, (st.stalwart_admin_user, st.stalwart_admin_password))
    return _service


async def _locked_labels(db: AsyncSession, ids: list[int]) -> dict[int, list[int]]:
    """The stored label ids of these messages, their rows locked until the transaction ends
    (in id order, so two writers never wait on each other crosswise)."""
    out: dict[int, list[int]] = {}
    for i in range(0, len(ids), 5000):
        rows = await db.execute(select(Message.id, Message.label_ids).where(Message.id.in_(ids[i:i + 5000]))
                                .order_by(Message.id).with_for_update())
        out.update({r[0]: list(r[1] or []) for r in rows})
    return out


async def user_store(db: AsyncSession, user: User) -> UserStore:
    return UserStore(user, service_client())
