"""The send gate and the outbox worker.

Every outgoing message becomes an Outbox row first:
- a signed-in user's send waits the user's undo window (default 10 s), or until send_at;
- an agent's send (API token or the built-in assistant) is checked by the permission
  engine (class `mail.send`): allowed sends wait the agent hold window
  (default 60 s); `ask` waits for the user's approval; the first-ever message to an address
  always asks.
The worker (`outbox.send` job) claims a queued row with a conditional update (so Undo and
the send can never both win), builds the MIME once with a fixed Message-ID (a retry after an
ambiguous SMTP failure resends the same message), submits it through the identity's
connector, then stores the sent copy in Sent in a separate, retried job. A mirror-mode account's
provider gets a copy in its own Sent folder after that, in a job of its own
(connectors/sentcopy.py).

A failure before the message was handed over (no connection, a refusal) is retried. A connection
that drops or times out after DATA started leaves it unknown whether the message went out: the row
becomes `uncertain` and is never resent on its own. `outbox.verify_sent` looks for the message's
Message-ID in the store and, for a provider that keeps its own copy of what is sent (Gmail,
Microsoft), in the provider's Sent folder. Found, the send counts as sent; otherwise the row stays
`uncertain` and the user decides (Retry or Discard).

The Message-ID is fixed when the row is made. A message that went out is noted on the data
volume (`record_sent`), so a row that a database restore brings back as queued is marked sent
instead of going out a second time (`sent_before`).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from email.utils import make_msgid
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.config import get_settings
from mailr.core import permissions
from mailr.core.compose import (
    AddressError,
    HeaderInjection,
    canonical_payload,
    check_header_inputs,
    envelope_recipients,
)
from mailr.core.events import notify
from mailr.core.jobs import Retry, enqueue, handler
from mailr.core.jobs import wake as jobs_wake
from mailr.db import get_existing
from mailr.i18n import ApiError
from mailr.models import Connector, Identity, Message, Outbox, PendingAction, Person, User

log = logging.getLogger("mailr.send")

MAX_RECIPIENTS = 100
STUCK_SENDING = timedelta(minutes=15)
# When outbox.verify_sent looks for an uncertain send's copy, in seconds after the failure.
VERIFY_DELAYS = (30, 120, 600)
VERIFY_TIMEOUT_S = 120
UNCERTAIN_ERROR = ("Sending was interrupted after the message was handed to the mail server. It may have been "
                   "sent; check your provider's Sent folder before retrying.")
# Labels a send may put on its own sent copy (payload "label_ids").
MAX_SEND_LABELS = 20


async def own_label_ids(db: AsyncSession, user_id: int, raw: Any) -> list[int]:
    """The payload's "label_ids" that name the user's own labels, in order, once each.

    The sent copy gets these labels when it is stored (store_sent_job): the phone's "Send to self"
    files a note under the labels picked on its card. Ids of other users' labels, system labels
    (Inbox, Sent, Trash, Spam) and anything that is not an id are dropped without an error, so a
    replay after a label was deleted still sends."""
    if not isinstance(raw, list):
        return []
    ids: list[int] = []
    for v in raw:
        if isinstance(v, bool) or not isinstance(v, (int, str)):
            continue
        try:
            n = int(v)
        except ValueError:
            continue
        if 0 < n < 2**31 and n not in ids:
            ids.append(n)
    ids = ids[:MAX_SEND_LABELS]
    if not ids:
        return []
    from mailr.models import Label

    mine = set((await db.execute(select(Label.id).where(
        Label.user_id == user_id, Label.role.is_(None), Label.id.in_(ids)))).scalars())
    return [i for i in ids if i in mine]


def _outbox_dir() -> Path:
    d = Path(get_settings().data_dir) / "outbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def send_context(db: AsyncSession, user_id: int, payload: dict[str, Any], rcpts: list[str],
                       ident: Identity, parent: Message | None) -> dict[str, Any]:
    """Facts the mail.send permission constraints are matched against."""
    people = {r.email: r for r in (await db.execute(
        select(Person).where(Person.user_id == user_id, Person.email.in_(rcpts)))).scalars()}
    # Only mail the user wrote or approved counts: a server-made send (the away message, an
    # invitation answer, calendar mail) never turns an address into a "previously mailed" one.
    sent_before = set((await db.execute(text(
        "SELECT DISTINCT unnest(recipients) FROM outbox WHERE user_id=:u AND status='sent' "
        "AND NOT (payload ? '_system') AND recipients && CAST(:r AS varchar[])"),
        {"u": user_id, "r": rcpts})).scalars())
    new = [r for r in rcpts if (r not in people or people[r].sent_count == 0) and r not in sent_before]
    unknown = [r for r in rcpts if r not in people
               or (people[r].sent_count == 0 and people[r].received_count == 0)]
    participants: set[str] = set()
    if parent is not None and parent.conversation_id:
        rows = await db.execute(text(
            "SELECT from_email, \"to\", cc FROM messages WHERE conversation_id=:c AND NOT deleted"),
            {"c": parent.conversation_id})
        for frm, to, cc in rows:
            participants.add((frm or "").lower())
            participants.update(a["email"].lower() for a in (to or []) + (cc or []))
    return {"recipients": rcpts, "identity_id": ident.id, "reply": parent is not None,
            "participants": sorted(participants), "new_recipients": new, "unknown_recipients": unknown}


def send_preview(payload: dict[str, Any], ident: Identity, rcpts: list[str]) -> dict[str, Any]:
    from mailr.core.compose import plain_text

    text_body = plain_text(payload)
    if len(text_body) > PREVIEW_TEXT_MAX:
        text_body = (text_body[:PREVIEW_TEXT_MAX]
                     + f"… ({len(text_body) - PREVIEW_TEXT_MAX} more characters not shown)")
    subj = payload.get("subject") or "(no subject)"
    images, links = remote_references(payload)
    return {"title": f"Send “{subj[:80]}”",
            "lines": [f"From {ident.email}", "To " + ", ".join(rcpts[:10]) + (" …" if len(rcpts) > 10 else "")],
            "email": {"from": ident.email, "to": payload.get("to") or [], "cc": payload.get("cc") or [],
                      # The whole body: the card folds a long one, it never cuts it.
                      "bcc": payload.get("bcc") or [], "subject": subj, "text": text_body,
                      "attachments": [a.get("filename") for a in payload.get("attachments") or []],
                      # The text shows "[image]" where an image is: the card lists each remote
                      # address it loads from, and the hosts the links point at.
                      "images": images, "links": links}}


PREVIEW_TEXT_MAX = 60_000


def remote_references(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    """The remote image addresses and the link hosts of the HTML that will be sent."""
    from urllib.parse import urlsplit

    from bs4 import BeautifulSoup

    from mailr.core.compose import clean_html, restore_remote_images

    html = payload.get("html")
    if not html:
        return [], []
    soup = BeautifulSoup(restore_remote_images(clean_html(html)), "html.parser")
    images: dict[str, None] = {}
    hosts: dict[str, None] = {}
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if src.lower().startswith(("http:", "https:", "//")):
            images[src] = None
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if href.lower().startswith(("http:", "https:", "//")):
            try:
                host = urlsplit(href if not href.startswith("//") else "https:" + href).hostname
            except ValueError:
                host = None
            hosts[host or href[:200]] = None
        elif href.lower().startswith("mailto:"):
            hosts["mailto: " + href[7:].split("?", 1)[0][:200]] = None
    return list(images), list(hosts)


def _comparable(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in ("identity_id", "_system") and v not in (None, "", [], {})}


async def _is_drafts_payload(db: AsyncSession, p, d: Message, payload: dict[str, Any]) -> bool:
    from mailr.core.drafts import draft_payload

    try:
        stored = await draft_payload(db, p, d)
    except Exception:  # noqa: BLE001 - an unreadable draft is not this payload
        return False
    return _comparable(stored) == _comparable(payload)


class SendGate:
    def __init__(self, db: AsyncSession, principal, actor: permissions.Actor | None = None):
        self.db = db
        self.p = principal
        self.actor = actor or permissions.Actor.from_principal(principal)
        self.pending: PendingAction | None = None

    async def submit(self, payload: dict[str, Any], draft_id: int | None = None,
                     send_at: datetime | None = None, *, system: dict[str, Any] | None = None,
                     idem_key: str | None = None) -> Outbox:
        """`idem_key`: a send the server makes on its own (a filter's forward) is queued once per
        key; a second submit with the same key returns the first Outbox row and sends nothing."""
        p, actor = self.p, self.actor
        if send_at is not None:
            from mailr.calendar.tz import aware

            send_at = await aware(self.db, p.user_id, send_at)
        if idem_key:
            prior = (await self.db.execute(select(Outbox).where(
                Outbox.user_id == p.user_id, Outbox.idem_key == idem_key))).scalar_one_or_none()
            if prior is not None:
                return prior
        # "_system" is server-only (e.g. auto_reply for the away message): never from the caller.
        payload = {k: v for k, v in payload.items() if k != "_system"}
        # Labels for the sent copy: only the user's own (own_label_ids); none is no key at all.
        labels = await own_label_ids(self.db, p.user_id, payload.pop("label_ids", None))
        if labels:
            payload["label_ids"] = labels
        if system:
            payload["_system"] = system
        if p.is_token and not p.has("send"):
            raise HTTPException(403, "this token cannot send; create a draft instead (POST /drafts)")
        ident = await self.db.get(Identity, int(payload.get("identity_id") or 0)) if payload.get("identity_id") else None
        if ident is None:
            ident = (await self.db.execute(select(Identity).where(Identity.user_id == p.user_id)
                                           .order_by(Identity.is_default.desc(), Identity.id))).scalars().first()
        if ident is None or ident.user_id != p.user_id:
            raise HTTPException(400, "unknown sending identity")
        payload = {**payload, "identity_id": ident.id}
        if ident.connector_id is None:
            raise HTTPException(400, f"{ident.email} has no outgoing mail server")
        conn = await self.db.get(Connector, ident.connector_id)
        if p.token is not None and not p.can_see_connector(conn):
            raise HTTPException(403, "this token may not use that account")
        caller_payload = payload
        try:
            # One canonical address per recipient: the policy, the headers and the SMTP envelope
            # all use it.
            check_header_inputs(payload)
            payload = canonical_payload(payload)
            rcpts = envelope_recipients(payload)
        except (AddressError, HeaderInjection) as e:
            raise HTTPException(400, str(e)) from None
        if not rcpts:
            raise HTTPException(400, "add at least one recipient")
        if len(rcpts) > MAX_RECIPIENTS:
            raise HTTPException(400, f"at most {MAX_RECIPIENTS} recipients")
        if not (payload.get("subject") or payload.get("html") or payload.get("text") or payload.get("attachments")):
            raise HTTPException(400, "the message is empty")
        parent = None
        parent_conv = None
        if payload.get("in_reply_to_message_id"):
            from mailr.api.mail_read import load_message

            parent = await load_message(self.db, p, int(payload["in_reply_to_message_id"]))
            parent_conv = parent.conversation_id
        if draft_id:
            from mailr.core.drafts import load_draft

            try:
                d = await load_draft(self.db, p, int(draft_id))
            except HTTPException:
                raise HTTPException(400, "unknown draft") from None
            if actor.kind != "user" and not await _is_drafts_payload(self.db, p, d, caller_payload):
                # A token or the assistant names a draft only to send that draft: with other
                # content the draft is left alone, never destroyed after the send.
                draft_id = None
            else:
                parent_conv = parent_conv or d.conversation_id

        now = datetime.now(UTC)
        settings = get_settings()
        if send_at and send_at < now - timedelta(minutes=1):
            raise HTTPException(400, "send time is in the past")
        # The Message-ID is fixed now, not at the first send: a row a database restore brings back
        # carries it, so the send can tell it went out already (`sent_before`).
        ob = Outbox(user_id=p.user_id, identity_id=ident.id, token_id=p.token.id if p.token else None,
                    subject=actor.subject, draft_jmap_id=str(draft_id) if draft_id else None,
                    conversation_id=parent_conv, payload=payload, recipients=rcpts,
                    send_at=send_at or now, status="awaiting_approval", idem_key=idem_key,
                    mime_message_id=make_msgid(domain=(ident.email or "mailr.local").split("@")[-1]))
        self.db.add(ob)
        await self.db.flush()

        if actor.kind == "user":
            undo = int((p.user.settings or {}).get("undo_send_seconds", settings.user_undo_send_seconds))
            ob.send_at = send_at or now + timedelta(seconds=max(0, min(undo, 30)))
            ob.status = "queued"
        else:
            ctx = await send_context(self.db, p.user_id, payload, rcpts, ident, parent)
            decision = await permissions.check(
                self.db, actor, "mail.send", target=", ".join(rcpts[:5]), ctx=ctx,
                payload={"outbox_id": ob.id}, preview=send_preview(payload, ident, rcpts))
            if isinstance(decision, permissions.Denied):
                await self.db.delete(ob)
                await self.db.flush()
                raise HTTPException(403, decision.reason)
            if isinstance(decision, permissions.Pending):
                self.pending = decision.action
                ob.approval_reason = (decision.action.preview or {}).get("reason") or "approval required"
            else:
                ob.status = "queued"
                ob.send_at = max(send_at or now, now + timedelta(seconds=settings.agent_send_hold_seconds))
        if ob.status == "queued":
            await enqueue(self.db, "outbox.send", {"outbox_id": ob.id}, user_id=p.user_id, run_at=ob.send_at,
                          dedupe_key=f"outbox:{ob.id}")
        if actor.kind == "user" and draft_id:
            # The user sends a draft Ambrose (or a token) asked to send: that request is withdrawn,
            # so the draft never goes out twice.
            await self._withdraw_for_draft(int(draft_id), keep=ob.id)
        title = None
        if ob.status == "awaiting_approval":
            subj = (payload.get("subject") or "(no subject)")[:80]
            title = f"{actor.name} wants to send “{subj}”: {ob.approval_reason}"
        await notify(self.db, p.user_id, {"type": "outbox", "id": ob.id, "status": ob.status, "title": title})
        return ob

    async def _withdraw_for_draft(self, draft_id: int, keep: int | None = None) -> None:
        rows = (await self.db.execute(
            update(Outbox).where(Outbox.user_id == self.p.user_id, Outbox.draft_jmap_id == str(draft_id),
                                 Outbox.status == "awaiting_approval", Outbox.id != (keep or 0))
            .values(status="cancelled", decided_by="user").returning(Outbox.id))).scalars().all()
        withdrawn: list[int] = []
        for oid in rows:
            withdrawn += (await self.db.execute(
                update(PendingAction).where(PendingAction.action == "mail.send", PendingAction.status == "pending",
                                            PendingAction.user_id == self.p.user_id,
                                            PendingAction.payload["outbox_id"].astext == str(oid))
                .values(status="denied", decided_by="cancelled", decided_at=datetime.now(UTC))
                .returning(PendingAction.id))).scalars().all()
            await notify(self.db, self.p.user_id, {"type": "outbox", "id": oid, "status": "cancelled"})
        await permissions.announce_left(self.db, self.p.user_id, withdrawn, "withdrawn")

    async def retry(self, oid: int) -> Outbox:
        """A failed send goes back in the queue, to leave now (Outbox → Retry). So does an uncertain
        one, once the user has decided it did not leave."""
        ob = await self.db.get(Outbox, oid)
        if not ob or ob.user_id != self.p.user_id:
            raise ApiError(404, "common.not_found")
        if self.p.token is not None and ob.token_id != self.p.token.id:
            raise HTTPException(403, "not this token's message")
        now = datetime.now(UTC)
        done = (await self.db.execute(
            update(Outbox).where(Outbox.id == oid, Outbox.status.in_(("failed", "uncertain")))
            .values(status="queued", send_at=now, error="").returning(Outbox.id))).first()
        if not done:
            await self.db.refresh(ob)
            raise ApiError(409, "outbox.message_state", status=ob.status)
        await enqueue(self.db, "outbox.send", {"outbox_id": oid}, user_id=ob.user_id,
                      dedupe_key=f"outbox:{oid}:retry:{int(now.timestamp())}")
        await jobs_wake(self.db, "outbox.send")
        await self.db.refresh(ob)
        await notify(self.db, ob.user_id, {"type": "outbox", "id": ob.id, "status": "queued"})
        return ob

    async def cancel(self, oid: int) -> Outbox:
        ob = await self.db.get(Outbox, oid)
        if not ob or ob.user_id != self.p.user_id:
            raise ApiError(404, "common.not_found")
        if self.p.token is not None and ob.token_id != self.p.token.id:
            raise HTTPException(403, "not this token's message")
        # Conditional transition: if the worker already claimed it (sending/sent), undo loses.
        done = (await self.db.execute(
            update(Outbox).where(Outbox.id == oid,
                                 Outbox.status.in_(("queued", "awaiting_approval", "failed", "uncertain")))
            .values(status="cancelled").returning(Outbox.id))).first()
        if not done:
            await self.db.refresh(ob)
            raise HTTPException(409, "too late to cancel: the message was already sent")
        withdrawn = (await self.db.execute(
            update(PendingAction).where(PendingAction.action == "mail.send", PendingAction.status == "pending",
                                        PendingAction.user_id == ob.user_id,
                                        PendingAction.payload["outbox_id"].astext == str(oid))
            .values(status="denied", decided_by="cancelled", decided_at=datetime.now(UTC))
            .returning(PendingAction.id))).scalars().all()
        await permissions.announce_left(self.db, ob.user_id, list(withdrawn), "withdrawn")
        await self.db.refresh(ob)
        await notify(self.db, ob.user_id, {"type": "outbox", "id": ob.id, "status": "cancelled"})
        return ob

    async def send_now(self, oid: int) -> Outbox:
        """Release a queued (scheduled or held) message immediately. Messages awaiting approval
        are not affected: approval is a separate decision."""
        ob = await self.db.get(Outbox, oid)
        if not ob or ob.user_id != self.p.user_id:
            raise ApiError(404, "common.not_found")
        if self.p.token is not None:
            raise HTTPException(403, "only the user can release a scheduled message early")
        now = datetime.now(UTC)
        done = (await self.db.execute(
            update(Outbox).where(Outbox.id == oid, Outbox.status == "queued")
            .values(send_at=now).returning(Outbox.id))).first()
        if not done:
            await self.db.refresh(ob)
            raise ApiError(409, "outbox.message_state", status=ob.status)
        # Pull the queued job forward (or add one if none is waiting).
        moved = (await self.db.execute(text(
            "UPDATE jobs SET run_at=now() WHERE kind='outbox.send' AND status='queued' "
            "AND payload->>'outbox_id' = :oid RETURNING id"), {"oid": str(oid)})).first()
        if not moved:
            await enqueue(self.db, "outbox.send", {"outbox_id": oid}, user_id=ob.user_id,
                          dedupe_key=f"outbox:{oid}:now")
        await jobs_wake(self.db, "outbox.send")
        await self.db.refresh(ob)
        await notify(self.db, ob.user_id, {"type": "outbox", "id": ob.id, "status": "queued"})
        return ob

    async def decide(self, oid: int, approve: bool) -> Outbox:
        """Legacy /outbox/{id}/approve|reject: decide the matching pending action once."""
        ob = await self.db.get(Outbox, oid)
        if not ob or ob.user_id != self.p.user_id:
            raise ApiError(404, "common.not_found")
        pa = (await self.db.execute(select(PendingAction).where(
            PendingAction.user_id == ob.user_id, PendingAction.action == "mail.send",
            PendingAction.status == "pending", PendingAction.payload["outbox_id"].astext == str(oid)))).scalars().first()
        if pa is None:
            if ob.status != "awaiting_approval":
                raise ApiError(409, "outbox.message_state", status=ob.status)
            if approve:
                await release(self.db, ob)
            else:
                ob.status = "rejected"
        else:
            await permissions.decide(self.db, self.p.user, pa.id, "approve" if approve else "deny")
            await self.db.refresh(ob)
        ob.decided_by = "user"
        await notify(self.db, ob.user_id, {"type": "outbox", "id": ob.id, "status": ob.status})
        return ob


async def release(db: AsyncSession, ob: Outbox) -> bool:
    """awaiting_approval → queued, exactly once."""
    done = (await db.execute(
        update(Outbox).where(Outbox.id == ob.id, Outbox.status == "awaiting_approval")
        .values(status="queued", send_at=datetime.now(UTC)).returning(Outbox.id))).first()
    if done:
        await enqueue(db, "outbox.send", {"outbox_id": ob.id}, user_id=ob.user_id, dedupe_key=f"outbox:{ob.id}")
    await db.refresh(ob)
    return bool(done)


@permissions.executor("mail.send")
async def _execute_send(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    ob = await db.get(Outbox, int(payload["outbox_id"]))
    if ob is None or ob.user_id != user.id:
        raise HTTPException(404, "message no longer exists")
    if not await release(db, ob):
        raise ApiError(409, "outbox.message_state", status=ob.status)
    return {"outbox_id": ob.id, "status": "queued"}


@permissions.on_denied("mail.send")
async def _deny_send(db: AsyncSession, user: User, payload: dict[str, Any]) -> None:
    await db.execute(update(Outbox).where(Outbox.id == int(payload["outbox_id"]),
                                          Outbox.status == "awaiting_approval").values(status="rejected"))


class _SystemPrincipal:
    """Acts for the outbox worker with the user's full rights."""

    def __init__(self, user: User):
        self.user = user
        self.user_id = user.id
        self.token = None
        self.is_token = False
        self.actor = "system"
        self.scopes = {"read", "label", "draft", "send", "delete", "rules", "calendar", "memory"}

    def require(self, s: str) -> None:
        return None

    def has(self, s: str) -> bool:
        return True

    def can_see_connector(self, c) -> bool:
        return c.user_id == self.user_id


SENT_KEEP_DAYS = 60  # longer than any database dump is kept (MAILR_DB_DUMP_KEEP)
_sent_pruned = 0.0


def _sent_marker(mime_message_id: str) -> Path:
    import hashlib

    return _outbox_dir() / "sent" / hashlib.sha256(mime_message_id.encode()).hexdigest()[:40]


def record_sent(mime_message_id: str) -> None:
    """Note on the data volume that this Message-ID went out. The database may be restored from a
    dump taken before the send, with the row queued again; the data volume keeps the note. Notes
    older than SENT_KEEP_DAYS go, at most once a day."""
    import time

    from mailr.core import private

    global _sent_pruned
    if not mime_message_id:
        return
    f = _sent_marker(mime_message_id)
    try:
        private.mkdirs(f.parent)
        private.write_bytes(f, b"")
        now = time.time()
        if now - _sent_pruned > 86400:
            _sent_pruned = now
            for old in f.parent.iterdir():
                if now - old.stat().st_mtime > SENT_KEEP_DAYS * 86400:
                    old.unlink(missing_ok=True)
    except OSError as e:
        log.warning("could not note a sent message on the data volume: %s", e)


async def sent_before(db: AsyncSession, ob: Outbox) -> bool:
    """This row's message went out already: the data volume has its note (`record_sent`), or the
    index has the user's sent copy with its Message-ID. Both catch a row that a database restore
    queued again after it was sent."""
    if not ob.mime_message_id:
        return False
    if _sent_marker(ob.mime_message_id).exists():
        return True
    return bool((await db.execute(select(Message.id).where(
        Message.user_id == ob.user_id, Message.message_id_hdr == ob.mime_message_id,
        Message.sent_by_me.is_(True), Message.draft.is_(False), Message.deleted.is_(False)).limit(1))).first())


@handler("outbox.send")
async def send_job(payload: dict[str, Any], ctx) -> None:
    from mailr.connectors.loader import connector_for
    from mailr.core.drafts import build_for_payload
    from mailr.db import session_scope

    oid = payload["outbox_id"]
    async with session_scope() as db:
        claimed = (await db.execute(
            update(Outbox).where(Outbox.id == oid, Outbox.status == "queued",
                                 Outbox.send_at <= datetime.now(UTC) + timedelta(seconds=1))
            .values(status="sending", attempts=Outbox.attempts + 1, claimed_at=func.now()).returning(Outbox.id))).first()
        if not claimed:
            ob = await db.get(Outbox, oid)
            if ob is not None and ob.status == "queued":
                raise Retry(delay=max(1.0, (ob.send_at - datetime.now(UTC)).total_seconds()))
            return  # cancelled, rejected, or already handled
        ob = await get_existing(db, Outbox, oid)
        if not ob.mime_message_id:
            ident = await db.get(Identity, ob.identity_id)
            ob.mime_message_id = make_msgid(domain=(ident.email if ident else "mailr.local").split("@")[-1])
        uid = ob.user_id
        if await sent_before(db, ob):
            # The database was restored from a dump taken before this message went out: it is
            # never sent twice. The row is marked sent, as it was before the restore.
            ob.status, ob.error = "sent", ""
            ob.sent_at = ob.sent_at or datetime.now(UTC)
            ob.sent_message_id = ob.mime_message_id
            log.warning("outbox %s: this message was sent already (the database was restored?); not sent again", oid)
            await notify(db, uid, {"type": "outbox", "id": ob.id, "status": "sent"})
            return

    async with session_scope() as db:
        ob = await get_existing(db, Outbox, oid)
        user = await get_existing(db, User, uid)
        sp = _SystemPrincipal(user)
        parent = None
        try:
            raw, ident, parent = await build_for_payload(db, sp, ob.payload, message_id=ob.mime_message_id)
            (_outbox_dir() / f"{oid}.eml").write_bytes(raw)
            conn_row = await db.get(Connector, ident.connector_id)
            conn = await connector_for(db, conn_row)
            resp = await conn.send(raw, ident.email, ob.recipients)
            record_sent(ob.mime_message_id or "")
        except Exception as e:  # noqa: BLE001
            if getattr(e, "uncertain", False):
                # The message may be out: never resend it blindly (look for it, then ask).
                ob.status = "uncertain"
                ob.error = f"{UNCERTAIN_ERROR}\n{type(e).__name__}: {e}"[:2000]
                await enqueue(db, "outbox.verify_sent", {"outbox_id": ob.id, "parent_id": _answered(ob, parent)},
                              user_id=uid, run_at=datetime.now(UTC) + timedelta(seconds=VERIFY_DELAYS[0]),
                              dedupe_key=f"outbox.verify_sent:{ob.id}:{ob.attempts}")
                await notify(db, uid, {"type": "outbox", "id": ob.id, "status": ob.status, "error": ob.error,
                                       "subject": ((ob.payload or {}).get("subject") or "")[:120]})
                log.warning("send %s uncertain: %s", oid, e)
                return
            permanent = getattr(e, "permanent", False) or type(e).__name__ in ("SendPermanentError", "HTTPException")
            ob.error = f"{type(e).__name__}: {getattr(e, 'detail', None) or e}"[:2000]
            ob.status = "queued" if ob.attempts < 4 and not permanent else "failed"
            if ob.status == "queued":
                ob.send_at = datetime.now(UTC) + timedelta(seconds=30 * ob.attempts)
                await enqueue(db, "outbox.send", {"outbox_id": ob.id}, user_id=uid, run_at=ob.send_at,
                              dedupe_key=f"outbox:{ob.id}:{ob.attempts}")
            else:
                await enqueue(db, "push.send_failed", {"outbox_id": ob.id}, user_id=uid)
            await notify(db, uid, {"type": "outbox", "id": ob.id, "status": ob.status, "error": ob.error,
                                   "subject": ((ob.payload or {}).get("subject") or "")[:120],
                                   "title": (f"Couldn't send “{(ob.payload or {}).get('subject') or '(no subject)'}”: "
                                             f"{ob.error}")[:200] if ob.status == "failed" else None})
            log.warning("send %s failed: %s", oid, e)
            return
        await _mark_sent(db, ob, _answered(ob, parent), str(resp))


def _answered(ob: Outbox, parent: Message | None) -> int | None:
    """The message a send answers: only a reply marks its parent answered; a forward's parent stays
    as it was."""
    return parent.id if parent and (ob.payload or {}).get("in_reply_to_message_id") else None


async def _mark_sent(db: AsyncSession, ob: Outbox, answered: int | None, detail: str) -> None:
    ob.status = "sent"
    ob.sent_at = datetime.now(UTC)
    ob.error = ""
    ob.sent_message_id = ob.mime_message_id
    await notify(db, ob.user_id, {"type": "outbox", "id": ob.id, "status": "sent", "detail": detail[:200]})
    # The message is out; storing the sent copy is a separate, retried job so a store
    # hiccup never makes a sent message look unsent, and never loses the copy.
    await enqueue(db, "outbox.store_sent", {"outbox_id": ob.id, "parent_id": answered},
                  user_id=ob.user_id, dedupe_key=f"outbox.store_sent:{ob.id}", max_attempts=10)


async def _copy_found(db: AsyncSession, ob: Outbox) -> bool | None:
    """Is an uncertain send's message already somewhere it would be only if it went out? True when
    the store holds a copy with its Message-ID (the provider's own sent copy, fetched by sync) or the
    provider's Sent folder does; False when it can still turn up there; None when nothing can show
    it (a provider that keeps no copy of what is sent)."""
    from mailr.connectors.loader import connector_for
    from mailr.connectors.sentcopy import imap_host, provider_saves_sent

    msgid = ob.mime_message_id
    if not msgid:
        return None
    if (await db.execute(select(Message.id).where(
            Message.user_id == ob.user_id, Message.message_id_hdr == msgid,
            Message.draft.is_(False), Message.deleted.is_(False)).limit(1))).first():
        return True
    ident = await db.get(Identity, ob.identity_id)
    conn_row = await db.get(Connector, ident.connector_id) if ident and ident.connector_id else None
    if conn_row is None or conn_row.kind != "imap" or not provider_saves_sent(imap_host(conn_row)):
        return None
    path = _outbox_dir() / f"{ob.id}.eml"
    conn: Any = await connector_for(db, conn_row)  # an ImapConnector
    try:
        folder = await asyncio.wait_for(conn.sent_folder(), VERIFY_TIMEOUT_S)
        _uv, uid = await asyncio.wait_for(
            conn.find_message_id(folder, msgid, path.stat().st_size if path.exists() else 0), VERIFY_TIMEOUT_S)
    finally:
        await conn._close()
    return uid is not None


@handler("outbox.verify_sent")
async def verify_sent_job(payload: dict[str, Any], ctx) -> None:
    """An uncertain send (`send_job`): sent when its copy turns up, else, after the last look, the
    user is told it may have been sent and decides. It is never resent from here."""
    from mailr.db import session_scope

    oid, n = payload["outbox_id"], int(payload.get("n") or 0)
    async with session_scope() as db:
        ob = await db.get(Outbox, oid)
        if ob is None or ob.status != "uncertain":
            return  # retried, discarded or settled meanwhile
        try:
            found = await _copy_found(db, ob)
        except Exception as e:  # noqa: BLE001  a provider that cannot be reached now may be later
            log.warning("verify send %s: %s", oid, e)
            found = False
        if found:
            await _mark_sent(db, ob, payload.get("parent_id"), "found in Sent")
            return
        if found is False and n + 1 < len(VERIFY_DELAYS):
            await enqueue(db, "outbox.verify_sent", {**payload, "n": n + 1}, user_id=ob.user_id,
                          run_at=datetime.now(UTC) + timedelta(seconds=VERIFY_DELAYS[n + 1] - VERIFY_DELAYS[n]),
                          dedupe_key=f"outbox.verify_sent:{oid}:{ob.attempts}:{n + 1}")
            return
        await enqueue(db, "push.send_failed", {"outbox_id": ob.id}, user_id=ob.user_id)
        await notify(db, ob.user_id, {"type": "outbox", "id": ob.id, "status": ob.status, "error": ob.error,
                                      "subject": ((ob.payload or {}).get("subject") or "")[:120],
                                      "title": "A message may not have been sent; check Sent before retrying."})


@handler("outbox.store_sent")
async def store_sent_job(payload: dict[str, Any], ctx) -> None:
    from mailr.core import threading
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.store import user_store
    from mailr.db import session_scope

    oid = payload["outbox_id"]
    path = _outbox_dir() / f"{oid}.eml"
    async with session_scope() as db:
        ob = await db.get(Outbox, oid)
        if ob is None or not path.exists():
            return
        user = await db.get(User, ob.user_id)
        ident = await db.get(Identity, ob.identity_id)
        conn_row = await db.get(Connector, ident.connector_id) if ident and ident.connector_id else None
        store = await user_store(db, user)
        # ACT mail.send_to_self_labels @api: the labels picked when sending, checked again (one
        # may have been deleted since), go on the sent copy with the filters' own labels.
        labels = await own_label_ids(db, ob.user_id, (ob.payload or {}).get("label_ids"))
        res = await ingest_raw(db, store, path.read_bytes(), IngestOptions(
            # Filters run only if the message is addressed to the user too (ingest decides).
            connector=conn_row, roles=["sent"], keywords={"seen": True}, source="send",
            run_rules=True, notify=False, label_ids=labels))
        ob.conversation_id = res.message.conversation_id
        # A mirror account's provider gets a copy in its Sent folder too (connectors/sentcopy.py).
        from mailr.connectors.sentcopy import queue_after_send

        await queue_after_send(db, conn_row, res.message.id)
        if (ob.payload or {}).get("_system") and not res.duplicate:
            # A server-made send (away message, invitation mail) does not make its recipients
            # people the user has written to: undo the sent count ingest just added.
            await db.execute(text(
                "UPDATE people SET sent_count = greatest(sent_count - 1, 0) "
                "WHERE user_id = :u AND email = ANY(CAST(:r AS varchar[]))"),
                {"u": ob.user_id, "r": [r.lower() for r in ob.recipients or []]})
        if payload.get("parent_id"):
            parent = await db.get(Message, payload["parent_id"])
            if parent is not None and not parent.answered:
                await store.set_keywords([parent], answered=True)
                if parent.conversation_id:
                    await threading.refresh(db, ob.user_id, [parent.conversation_id])
        if ob.draft_jmap_id:
            d = await db.get(Message, int(ob.draft_jmap_id))
            if d and d.user_id == ob.user_id and d.draft and not d.deleted:
                await store.destroy([d])
                d.deleted = True
                d.jmap_id = None
                from mailr.core import purge

                await purge.queue(db, ob.user_id, [d.id], export=False)  # the draft's text (core/purge.py)
                if d.conversation_id:
                    await threading.refresh(db, ob.user_id, [d.conversation_id])
                from mailr.core import live

                await live.forget(db, ob.user_id, "draft", d.id, by="user")
    path.unlink(missing_ok=True)


@handler("drafts.destroy_replaced")
async def destroy_replaced_draft(payload: dict[str, Any], ctx) -> None:
    """The store copy a draft save replaced (core/drafts.py `save_draft`), destroyed after that
    save committed. A copy some row still points at (it never should) is kept."""
    from mailr.core.store import user_store
    from mailr.db import session_scope

    jid = payload["jmap_id"]
    async with session_scope() as db:
        if (await db.execute(select(Message.id).where(Message.user_id == payload["user_id"],
                                                      Message.jmap_id == jid))).first():
            return
        user = await db.get(User, payload["user_id"])
        if user is None:
            return
        store = await user_store(db, user)
        await store.destroy([Message(jmap_id=jid)])


async def sweep_stuck() -> int:
    """`sending` rows whose worker died: never resend blindly (the message may be out)."""
    from mailr.db import session_scope

    async with session_scope() as db:
        rows = (await db.execute(
            # Keyed on the claim, not on send_at: a send claimed late (a queue backlog, a worker
            # restart) is still in flight long after its send_at. Rows claimed before claimed_at
            # existed fall back to send_at.
            update(Outbox).where(Outbox.status == "sending",
                                 func.coalesce(Outbox.claimed_at, Outbox.send_at) < datetime.now(UTC) - STUCK_SENDING)
            .values(status="failed", error="Sending was interrupted. The message may have been sent; "
                                           "check your provider's Sent folder before retrying.")
            .returning(Outbox.id, Outbox.user_id))).all()
        for oid, uid in rows:
            await notify(db, uid, {"type": "outbox", "id": oid, "status": "failed",
                                   "title": "A message may not have been sent; check Sent before retrying."})
        return len(rows)
