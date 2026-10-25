"""Notifications to devices (UnifiedPush / ntfy / Web Push) and to open web sessions (SSE).

Events: "new_mail", "important_mail" (new mail marked important), "approval" (an agent's send
waits for approval), "send_failed", "assistant", "account" (a mail account needs its password),
"reminder". What notifies is the account's choice, the same for every device
(core/notifyprefs.py); a PushTarget's `events` list can only narrow it (an empty list
follows the account). "reminder" is opt-in: only targets that list it get
it, so an Android phone (UnifiedPush, which fires its own reminders) is never notified twice.
Web Push targets (core/webpush.py) list it when they are created; ntfy targets may.

Payloads are minimal. Content from a `local_only` account never leaves in a notification:
the text is just "New message", and every other event goes through `fan_out`, which replaces
title and body with fixed text when the caller says the content is private or the data names a
local-only conversation, message, account or send. For other accounts the sender's name is included, never the
body. UnifiedPush endpoints receive a small JSON document the app decodes (`up_payload`, at
most UP_MAX_BYTES: ntfy and the UnifiedPush spec carry 4096 bytes); when the app registered
Web Push keys with the endpoint (UnifiedPush connector 3), the document is encrypted for the
phone with aes128gcm (RFC 8291), so the push server sees only ciphertext; when the phone also
registered the endpoint with mailr's VAPID key (`PushTarget.vapid`), each message is signed with it
(RFC 8292) and the push server refuses unsigned posts. ntfy topics receive a
titled plain-text notification.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr import i18n
from mailr.config import get_settings
from mailr.core import publicid
from mailr.core.events import notify
from mailr.core.jobs import Retry, handler
from mailr.db import session_scope
from mailr.models import Connector, Label, Message, Outbox, PushTarget, User

log = logging.getLogger("mailr.push")

EVENTS = ("new_mail", "important_mail", "approval", "send_failed", "assistant", "account", "reminder")
# Events a target receives only when it lists them (an empty list does not include them).
OPT_IN = {"reminder"}


def wants(target: PushTarget, event: str) -> bool:
    """The device's own narrowing (core/notifyprefs.device_wants). What the account wants is
    checked once per notification (fan_out, push_new_mail)."""
    from mailr.core.notifyprefs import device_wants

    return device_wants(target.events, event)


# UnifiedPush: the JSON document stays under this, so the encrypted message (86 bytes of
# header and a 17-byte tag more) fits ntfy's 4096-byte message limit and one aes128gcm record.
UP_MAX_BYTES = 3800
# A push server's answer is a status; its body is never needed.
RESPONSE_LIMIT = 64 * 1024
UP_TITLE_MAX = 120
UP_BODY_MAX = 300


def up_payload(title: str, body: str, data: dict[str, Any]) -> bytes:
    """The JSON a UnifiedPush endpoint receives: {"title", "body", **data}, title and body cut
    to fixed lengths, and never more than UP_MAX_BYTES (the click link, then the body, give way)."""
    doc = {"title": (title or "")[:UP_TITLE_MAX], "body": (body or "")[:UP_BODY_MAX], **data}
    raw = json.dumps(doc, default=str, ensure_ascii=False).encode()
    if len(raw) > UP_MAX_BYTES:
        doc.pop("click", None)
        raw = json.dumps(doc, default=str, ensure_ascii=False).encode()
    while len(raw) > UP_MAX_BYTES and doc["body"]:
        doc["body"] = doc["body"][: len(doc["body"]) // 2]
        raw = json.dumps(doc, default=str, ensure_ascii=False).encode()
    if len(raw) > UP_MAX_BYTES:  # only the ids and the type are left: keep those
        raw = json.dumps({"title": doc["title"][:40], "body": "", **{k: v for k, v in data.items() if k != "click"}},
                         default=str, ensure_ascii=False).encode()[:UP_MAX_BYTES]
    return raw


async def deliver(target: PushTarget, title: str, body: str, data: dict[str, Any], priority: str = "default") -> bool:
    """Send one notification. False only when the endpoint is gone (404/410: the caller removes it)."""
    return (await deliver_report(target, title, body, data, priority))[0]


async def owner_private_ok(user_id: int | None) -> bool:
    """May this user's push endpoints be on the LAN (core/netguard.py policy)?"""
    from mailr.core import netguard

    if netguard.private_mode() != "admins":
        return netguard.private_mode() == "all"
    async with session_scope() as db:
        return await netguard.private_ok_for_user_id(db, user_id)


def mail_title(from_name: str | None, from_email: str | None, lang: str = i18n.DEFAULT) -> str:
    """The notification title for new mail: always says it is mail and from whom, so a
    sender's display name cannot pose as one of mailr's own notifications."""
    from mailr.core.textsafe import clean

    name = " ".join(clean(from_name or "").split())[:80]
    email = clean(from_email or "").strip()
    if not name or "@" in name:  # an address-shaped name shows the real address instead
        name = email or i18n.t("push.mail.unknown_sender", lang)
    return i18n.t("push.mail.title", lang, name=name)


PUSH_HEALTH = "push_health"


async def deliver_report(target: PushTarget, title: str, body: str, data: dict[str, Any],
                         priority: str = "default", *, private_ok: bool | None = None) -> tuple[bool, str | None]:
    """deliver() plus what went wrong, for the test button: (keep the target, error or None).
    Each outcome is noted per transport (core/healthlog.py) for Admin → Diagnostics; an endpoint
    the push server reports gone counts as the server working."""
    keep, error = await _deliver_report(target, title, body, data, priority, private_ok=private_ok)
    from mailr.core import healthlog

    await healthlog.note(PUSH_HEALTH, [f"transport:{target.kind}"], error is None or not keep, error)
    return keep, error


async def _deliver_report(target: PushTarget, title: str, body: str, data: dict[str, Any],
                          priority: str = "default", *, private_ok: bool | None = None) -> tuple[bool, str | None]:
    from mailr.core.netguard import (
        TooLarge,
        UnsafeTarget,
        check_url,
        fetch_capped,
        guarded_client,
        same_origin,
    )

    s = get_settings()
    if private_ok is None:
        private_ok = await owner_private_ok(target.user_id)
    try:
        await check_url(target.endpoint, private_ok=private_ok)
    except UnsafeTarget as e:
        log.warning("push target %s refused: %s", target.id, e)
        return True, f"Mailr does not connect to this endpoint: {e}"
    if target.kind == "webpush":
        from mailr.core import webpush

        ok = await webpush.send(target, {"title": title, "body": body, **data},
                                urgency="high" if priority == "high" else "low" if priority == "low" else "normal",
                                private_ok=private_ok)
        return ok, None if ok else "the push service says this subscription is gone"
    try:
        async with guarded_client(private_ok=private_ok, timeout=10) as c:
            if target.kind == "ntfy":
                headers = {"Title": title.encode("utf-8").decode("latin-1", "ignore") or "Mailr",
                           "Priority": priority, "Tags": data.get("type", "email")}
                if data.get("click"):
                    headers["Click"] = data["click"]
                # The instance token goes only to the configured ntfy server (exact origin).
                if s.ntfy_token and s.ntfy_url and same_origin(target.endpoint, s.ntfy_url):
                    headers["Authorization"] = f"Bearer {s.ntfy_token}"
                r, _ = await fetch_capped(c, "POST", target.endpoint, limit=RESPONSE_LIMIT, deadline=20,
                                          content=body.encode(), headers=headers)
            else:  # UnifiedPush: the JSON document, encrypted for the phone when it gave its keys
                raw = up_payload(title, body, data)
                headers = {"TTL": "86400", "Urgency": "high" if priority == "high" else "normal"}
                if target.p256dh and target.auth:
                    from mailr.core import webpush

                    raw = webpush.encrypt(target, raw)
                    headers |= {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream"}
                    if target.vapid:
                        # Registered with mailr's VAPID key: signed, so the push server refuses
                        # anything not from mailr (RFC 8292).
                        headers["Authorization"] = await webpush.vapid_for(target.endpoint)
                else:
                    headers["Content-Type"] = "application/json"
                r, _ = await fetch_capped(c, "POST", target.endpoint, limit=RESPONSE_LIMIT, deadline=20,
                                          content=raw, headers=headers)
        if r.status_code in (404, 410):
            return False, "the push server says this endpoint is gone"
        if r.status_code >= 400:
            log.warning("push to %s failed: %s", target.endpoint[:60], r.status_code)
            return True, f"the push server answered {r.status_code}"
        return True, None
    except (httpx.HTTPError, TooLarge, TimeoutError) as e:
        log.warning("push to %s failed: %s", target.endpoint[:60], type(e).__name__)
        return True, f"cannot reach the push server ({type(e).__name__})"


# What a notification says when its text came from a local-only account: nothing of the mail,
# the people or the recording, only that there is something to open. Catalogue keys
# push.private.<event>.title/.body (mailr/i18n/en.json); other events take push.private.default.
PRIVATE_EVENTS = ("follow_up", "approval", "send_failed", "send_uncertain", "reminder", "new_mail", "important_mail")


def private_text(event: str, lang: str = i18n.DEFAULT) -> tuple[str, str]:
    """The fixed title and body of a private notification, in the recipient's language."""
    key = f"push.private.{event}" if event in PRIVATE_EVENTS else "push.private.default"
    return i18n.t(f"{key}.title", lang), i18n.t(f"{key}.body", lang)


async def touches_local_only(db: AsyncSession, user_id: int, data: dict[str, Any]) -> bool:
    """Does a notification's data name local-only content: a conversation or message with
    local-only mail, a local-only account, a send from or about one, a local-only note or table, a
    calendar item of a local-only account, or a recording of a local-only Speakr account? Notes and
    tables are named by `note_id` / `table_id` (id or public id), calendar items by `object_id` or
    an `item` {"kind": "event"|"task"|"note", "uid"}, recordings by `recording_id` (with
    `connector_id` when given)."""
    from mailr.core.visibility import touches_local_only as mail_local_only

    def _int(v: Any) -> int | None:
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    cid, mid = _int(data.get("conversation_id")), _int(data.get("message_id"))
    if cid is not None and await mail_local_only(db, user_id, conversation_ids=[cid]):
        return True
    if mid is not None and await mail_local_only(db, user_id, message_ids=[mid]):
        return True
    conn = _int(data.get("connector_id"))
    if conn is not None:
        c = await db.get(Connector, conn)
        if c is not None and c.user_id == user_id and c.data_class == "local_only":
            return True
    ob = _int(data.get("outbox_id"))
    if ob is not None:
        row = await db.get(Outbox, ob)
        if row is not None and row.user_id == user_id and await _outbox_local_only(db, row):
            return True
    return await _items_local_only(db, user_id, data, conn)


async def _items_local_only(db: AsyncSession, user_id: int, data: dict[str, Any], conn: int | None) -> bool:
    """The note, table, calendar and recording part of touches_local_only."""
    from sqlalchemy import exists, or_

    from mailr.models import CalObject, SpeakrRecording
    from mailr.notes.models import Note
    from mailr.tables.models import DataTable

    def _ref(v: Any) -> tuple[int | None, str | None]:
        if isinstance(v, int) and not isinstance(v, bool):
            return v, None
        if isinstance(v, str) and v.strip():
            return (int(v), None) if v.strip().isdigit() else (None, v.strip())
        return None, None

    async def _flagged(model: Any, v: Any) -> bool:
        i, pid = _ref(v)
        if i is None and pid is None:
            return False
        cond = model.id == i if i is not None else model.pid == pid
        return bool((await db.execute(select(exists().where(cond, model.local_only.is_(True))))).scalar())

    async def _cal(cond: Any) -> bool:
        return bool((await db.execute(select(exists().where(
            cond, CalObject.user_id == user_id, Connector.id == CalObject.connector_id,
            Connector.data_class == "local_only")))).scalar())

    raw_item = data.get("item")
    item: dict[str, Any] = raw_item if isinstance(raw_item, dict) else {}
    if await _flagged(Note, data.get("note_id")) or await _flagged(DataTable, data.get("table_id")):
        return True
    if item.get("kind") == "note" and await _flagged(Note, item.get("uid") or item.get("id")):
        return True
    if item.get("kind") == "table" and await _flagged(DataTable, item.get("uid") or item.get("id")):
        return True
    oid, _ = _ref(data.get("object_id"))
    if oid is not None and await _cal(CalObject.id == oid):
        return True
    uid = item.get("uid")
    if item.get("kind") in ("event", "task") and isinstance(uid, str) and uid and await _cal(CalObject.uid == uid):
        return True
    rid, _ = _ref(data.get("recording_id"))
    if rid is not None:
        # Speakr's recording id or mailr's row id: either names it (a wrong match only hides text).
        conds = [SpeakrRecording.user_id == user_id, or_(SpeakrRecording.recording_id == rid, SpeakrRecording.id == rid),
                 Connector.id == SpeakrRecording.connector_id, Connector.data_class == "local_only"]
        if conn is not None:
            conds.append(SpeakrRecording.connector_id == conn)
        if (await db.execute(select(exists().where(*conds)))).scalar():
            return True
    return False


async def fan_out(db: AsyncSession, user_id: int, event: str, title: str, body: str, data: dict[str, Any],
                  priority: str = "default", *, webpush_extra: dict[str, Any] | None = None,
                  private: bool | None = None, private_as: str | None = None) -> int:
    """Deliver to every target that wants the event. `webpush_extra` goes only to Web Push
    targets (the signed token of a reminder's buttons); `private_as` names the fixed text's
    event when it is not `event` (an uncertain send is a send_failed event).

    This is the one place the local-only rule holds for every caller: with `private` (or, when
    the caller does not say, when the data names local-only content, `touches_local_only`) the
    title and body become fixed text (PRIVATE_TEXT) for every target, encrypted or not."""
    from mailr.core import notifyprefs

    user = await db.get(User, user_id)
    if user is None or user.disabled or not notifyprefs.kind_wanted(notifyprefs.of(user.settings), event):
        return 0  # the account turned this kind off (Settings → Notifications)
    targets = (await db.execute(select(PushTarget).where(PushTarget.user_id == user_id))).scalars().all()
    if not any(wants(t, event) for t in targets):
        return 0
    if private is None:
        private = await touches_local_only(db, user_id, data)
    if private:
        title, body = private_text(private_as or event, i18n.user_language(user))
    sent = 0
    for t in targets:
        if not wants(t, event):
            continue
        extra = webpush_extra if t.kind == "webpush" and webpush_extra else {}
        if await deliver(t, title, body, publicid.out({**data, **extra, "type": event}), priority):
            sent += 1
        else:
            await db.delete(t)
    return sent


async def _web(db: AsyncSession, user: User, event: str, payload: dict[str, Any]) -> None:
    """A toast, and a desktop notification while the tab is hidden, in the open web windows,
    when the account wants this kind (core/notifyprefs.py). Mail is decided before the call."""
    from mailr.core import notifyprefs

    if event in ("new_mail", "important_mail") or notifyprefs.kind_wanted(notifyprefs.of(user.settings), event):
        await notify(db, user.id, {"type": "notification", **payload})


@handler("push.new_mail")
async def push_new_mail(payload: dict[str, Any], ctx) -> None:
    async with session_scope() as db:
        m = await db.get(Message, payload["message_id"])
        if m is None or m.deleted or m.seen:
            return
        # Importance comes from the intelligence pass; wait briefly for it.
        if m.intel_state == "pending" and m.created_at > datetime.now(UTC) - timedelta(seconds=60):
            raise Retry(delay=8)
        user = await db.get(User, m.user_id)
        if user is None or user.disabled:
            return
        inbox = (await db.execute(select(Label.id).where(Label.user_id == m.user_id, Label.role == "inbox"))).scalar()
        if inbox not in (m.label_ids or []):
            return  # filtered away, archived, or spam
        # The data class lives on the message (stamped at ingest), so it holds after the account is
        # removed and for mail with no connector.
        local_only = bool(m.local_only)
        if not local_only and m.connector_id:
            c = await db.get(Connector, m.connector_id)
            local_only = bool(c and c.data_class == "local_only")
        lang = i18n.user_language(user)
        if local_only:
            title, body = private_text("new_mail", lang)
        else:
            title, body = mail_title(m.from_name, m.from_email, lang), i18n.t("push.mail.body", lang)
        from mailr.intel.citations import url_for

        # Public ids and the /mail/all/c… link: this payload skips fan_out's publicid.out.
        data = publicid.out({"conversation_id": m.conversation_id, "message_id": m.id})
        data["click"] = (url_for(m.conversation_id) or "").replace("/mail/all/", "/mail/inbox/", 1)
        event = "important_mail" if m.important else "new_mail"
        # One setting for every device (core/notifyprefs.py): the account's mail level
        # decides; a device can only take no mail at all.
        from mailr.core import notifyprefs

        vip = (m.from_email or "").lower() in {a.lower() for a in (user.settings or {}).get("vip") or []}
        prefs = notifyprefs.of(user.settings)
        if not notifyprefs.mail_wanted(prefs, important=bool(m.important), vip=vip,
                                       primary=m.category in (None, "primary")):
            return
        targets = (await db.execute(select(PushTarget).where(PushTarget.user_id == m.user_id))).scalars().all()
        for t in targets:
            if not wants(t, event):
                continue
            if not await deliver(t, title, body, {**data, "type": event}, "high" if m.important else "default"):
                await db.delete(t)
        await _web(db, user, event,
                   {"kind": "important" if m.important else "new", "vip": vip, "title": title, "body": body, **data})


async def _outbox_local_only(db: AsyncSession, ob: Outbox) -> bool:
    """A send from or about a local-only account (its conversation, draft or sending account)."""
    from types import SimpleNamespace

    from mailr.core.visibility import approval_touches_local_only

    return await approval_touches_local_only(db, ob.user_id, SimpleNamespace(payload={"outbox_id": ob.id}, ctx={}))


@handler("push.approval")
async def push_approval(payload: dict[str, Any], ctx) -> None:
    if payload.get("pending_id"):
        from mailr.models import PendingAction

        async with session_scope() as db:
            pa = await db.get(PendingAction, payload["pending_id"])
            if pa is None or pa.status != "pending":
                return
            user = await db.get(User, pa.user_id)
            lang = i18n.user_language(user)
            actor = pa.actor
            if pa.subject == "assistant" or (pa.subject or "").startswith("session:"):
                from mailr.knowledge import profile

                actor = await profile.name_of(db, pa.user_id)  # the name the user gives it now
            title = i18n.t("push.approval.title", lang, actor=actor)
            pv = pa.preview or {}
            # The display's plain text (agent/present.py): title, summary and the first fields.
            shown = (pv.get("display") or {}).get("text")
            body = (" · ".join(shown.split("\n")[:3]) if shown else
                    " · ".join([pv.get("title") or pa.action] + list(pv.get("lines") or [])[:2]))[:240]
            from mailr.core.visibility import approval_touches_local_only

            if await approval_touches_local_only(db, pa.user_id, pa):
                # Nothing of a local-only account leaves in a notification: no subject, recipient or text.
                body = private_text("approval", lang)[1]
            from mailr.core.permissions import approval_url

            data = {"pending_id": pa.id, "action": pa.action, "actor": actor, "click": approval_url(pa.id)}
            await fan_out(db, pa.user_id, "approval", title, body, data, "high")
            await _web(db, user, "approval", {"kind": "approval", "title": title, "body": body, **data})
        return
    async with session_scope() as db:
        ob = await db.get(Outbox, payload["outbox_id"])
        if ob is None or ob.status != "awaiting_approval":
            return
        user = await db.get(User, ob.user_id)
        lang = i18n.user_language(user)
        n = len(ob.recipients or [])
        title = i18n.t("push.approval.agent_email_title", lang)
        to = (i18n.t("push.approval.to_more", lang, first=ob.recipients[0], count=n - 1) if n > 1
              else i18n.t("push.approval.to_single", lang, first=ob.recipients[0]))
        body = f"{to} {ob.approval_reason}"[:240]
        if await _outbox_local_only(db, ob):
            body = private_text("approval", lang)[1]
        data = {"outbox_id": ob.id, "click": f"{get_settings().base_url.rstrip('/')}/settings/agents#approvals"}
        await fan_out(db, ob.user_id, "approval", title, body, data, "high")
        await _web(db, user, "approval", {"kind": "approval", "title": title, "body": body, **data})


# Push services that end a subscription whose pushes show nothing (Safari on iOS and macOS):
# an approval.decided push only closes notifications, so these never get one. An open tab
# there still closes them from the event stream.
SILENT_PUSH_REFUSED = ("web.push.apple.com",)


def takes_silent(target: PushTarget) -> bool:
    """Can this target receive a push that shows nothing (approval.decided)? A UnifiedPush phone
    and a Web Push browser can; an ntfy topic shows every message as text, and Apple's push
    service ends a subscription whose pushes show nothing."""
    if target.kind == "ntfy":
        return False
    if target.kind == "webpush":
        from urllib.parse import urlparse

        host = (urlparse(target.endpoint).hostname or "").lower()
        return not any(host == h or host.endswith("." + h) for h in SILENT_PUSH_REFUSED)
    return True


@handler("push.approval_decided")
async def push_approval_decided(payload: dict[str, Any], ctx) -> None:
    """Approvals left pending (core/permissions.announce_left): every device that gets approval
    notifications takes theirs down. The message is generic: `type`, the public ids and the
    outcome, with no title, body or content, so it says nothing even unencrypted. The device's
    own narrowing applies (a target that never gets approvals gets no clears); the account's
    choice does not, so turning approvals off still clears what is showing."""
    user_id = payload.get("user_id") or getattr(ctx, "user_id", None)
    ids = [int(i) for i in payload.get("ids") or []]
    if user_id is None or not ids:
        return
    async with session_scope() as db:
        targets = (await db.execute(select(PushTarget).where(PushTarget.user_id == user_id))).scalars().all()
        data = {"type": "approval.decided", "outcome": str(payload.get("outcome") or ""),
                "ids": [publicid.encode("approval", i) for i in ids]}
        for t in targets:
            if not wants(t, "approval") or not takes_silent(t):
                continue
            if not await deliver(t, "", "", data, "low"):
                await db.delete(t)


@handler("push.send_failed")
async def push_send_failed(payload: dict[str, Any], ctx) -> None:
    async with session_scope() as db:
        ob = await db.get(Outbox, payload["outbox_id"])
        if ob is None:
            return
        user = await db.get(User, ob.user_id)
        lang = i18n.user_language(user)
        # A send whose connection dropped after the message was handed over may have gone out:
        # its own words, so nobody sends it again unseen (core/sendgate.py, `uncertain`).
        key = "send_uncertain" if ob.status == "uncertain" else "send_failed"
        title = i18n.t(f"push.{key}.title", lang)
        body = i18n.t(f"push.{key}.body", lang, to=", ".join(ob.recipients[:2]), error=(ob.error or "")[:160])
        if await _outbox_local_only(db, ob):
            title, body = private_text(key, lang)
        data = {"outbox_id": ob.id, "click": f"{get_settings().base_url.rstrip('/')}/mail/outbox"}
        await fan_out(db, ob.user_id, "send_failed", title, body, data, "high", private_as=key)
        await _web(db, user, "send_failed", {"kind": "send_failed", "title": title, "body": body, **data})
