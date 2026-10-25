"""Drafts live in the Stalwart Drafts mailbox as real messages (visible to IMAP clients).

JMAP emails are immutable, so saving replaces the stored message while the mailr message
row (and its id, which the UI holds) stays the same. The structured compose payload is kept
in Message.upstream['draft_payload'] so the editor can reopen it exactly.
"""

from __future__ import annotations

import asyncio
import base64
import html as htmllib
import json
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr import i18n
from mailr.core import threading
from mailr.core.compose import build_message, clean_html, quote_block, reply_subject, sniff_image
from mailr.core.mime import inline_images, norm_subject, parse
from mailr.core.store import user_store
from mailr.models import Identity, Message


async def identity_for(db: AsyncSession, p, identity_id: int | None) -> Identity:
    q = select(Identity).where(Identity.user_id == p.user_id)
    if identity_id:
        q = q.where(Identity.id == identity_id)
    else:
        q = q.order_by(Identity.is_default.desc(), Identity.id)
    i = (await db.execute(q)).scalars().first()
    if i is None:
        raise HTTPException(400, "add a sending address (Settings → Mail → Mail accounts) before composing")
    return i


async def _attachments(p, payload: dict[str, Any]) -> list[tuple[bytes, str, str]]:
    from mailr.api.mail_write import read_upload

    out = []
    for a in payload.get("attachments") or []:
        data, mtype, fname = read_upload(p.user_id, a.get("blob", ""))
        out.append((data, a.get("type") or mtype, a.get("filename") or fname))
    return out


def _upload_loader(uid: int):
    # ACT mail.insert_image @api
    """Reads the user's uploads for the inline images of a message (ACT mail.insert_image)."""
    from mailr.api.mail_write import read_upload

    def load(blob: str) -> tuple[bytes, str] | None:
        try:
            data, mtype, _ = read_upload(uid, blob)
        except HTTPException:
            return None
        return data, mtype

    return load


def store_upload(uid: int, data: bytes, filename: str, ctype: str) -> str:
    """Write bytes into the user's upload area (as POST /uploads does) and return the blob id."""
    import hashlib

    from mailr.api.mail_write import upload_dir

    blob = hashlib.sha256(data).hexdigest()[:32]
    d = upload_dir(uid)
    if not (d / blob).exists():
        (d / blob).write_bytes(data)
    (d / f"{blob}.json").write_text(json.dumps({"filename": filename, "type": ctype, "size": len(data)}))
    return blob


_DATA_SRC = re.compile(r"""src=(["'])data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=\s]+)\1""", re.IGNORECASE)
_CID_SRC = re.compile(r"""src=(["'])cid:([^"']+)\1""", re.IGNORECASE)


def data_images_to_uploads(uid: int, html: str) -> str:
    """The images of a quoted or forwarded message arrive as data: URIs (its cid: parts are
    inlined by render_body). Each one is stored as an upload, so the draft stays small and the
    image is sent as a Content-ID part again."""
    def repl(m: re.Match) -> str:
        try:
            data = base64.b64decode(re.sub(r"\s+", "", m.group(3)), validate=True)
        except Exception:  # noqa: BLE001
            return m.group(0)
        ctype = sniff_image(data)
        if ctype is None:
            return 'src=""'
        return f'src="/api/v1/uploads/{store_upload(uid, data, "image", ctype)}"'

    return _DATA_SRC.sub(repl, html) if "data:" in html else html


def restore_inline_images(uid: int, html: str, raw: bytes) -> str:
    """Make every inline image of a stored draft available again as an upload: an upload that
    is missing comes back from the draft's own Content-ID part (its cid names the upload), and
    a cid: reference (a draft written in another client) becomes an upload reference."""
    from mailr.api.mail_write import upload_dir

    images = inline_images(raw)
    by_key = {cid.split(".img@", 1)[0]: v for cid, v in images.items() if ".img@" in cid}
    for blob in set(re.findall(r"/api/v1/uploads/([0-9a-f]{32})", html)):
        if not (upload_dir(uid) / blob).exists() and blob in by_key:
            data, ctype = by_key[blob]
            store_upload(uid, data, "image", sniff_image(data) or ctype)

    def repl(m: re.Match) -> str:
        got = images.get(m.group(2).strip("<>"))
        if not got or not sniff_image(got[0]):
            return m.group(0)
        return f'src="/api/v1/uploads/{store_upload(uid, got[0], "image", sniff_image(got[0]) or got[1])}"'

    return _CID_SRC.sub(repl, html)


def _missing_uploads(uid: int, html: str) -> bool:
    from mailr.api.mail_write import upload_dir

    return "cid:" in html or any(not (upload_dir(uid) / b).exists()
                                 for b in re.findall(r"/api/v1/uploads/([0-9a-f]{32})", html))


async def build_for_payload(db: AsyncSession, p, payload: dict[str, Any],
                            message_id: str | None = None) -> tuple[bytes, Identity, Message | None]:
    ident = await identity_for(db, p, payload.get("identity_id"))
    parent = None
    # A reply's parent is the message it answers; a forward's is the message it carries. Both go out
    # with In-Reply-To and References to it and stay in its conversation, as Gmail files a forward.
    parent_id = payload.get("in_reply_to_message_id") or payload.get("forward_of_message_id")
    if parent_id:
        from mailr.api.mail_read import load_message

        parent = await load_message(db, p, int(parent_id))
    msg = build_message(
        payload,
        from_addr={"email": ident.email, "name": ident.name or p.user.name},
        reply_to=ident.reply_to,
        in_reply_to=parent.message_id_hdr if parent else None,
        references=list(parent.references or []) if parent else None,
        attachments=await _attachments(p, payload),
        message_id=message_id,
        inline_loader=_upload_loader(p.user_id),
    )
    return msg.as_bytes(), ident, parent


async def load_draft(db: AsyncSession, p, mid: int) -> Message:
    """A draft the caller may see (tokens: visibility rules apply, incl. local-only)."""
    from mailr.core.visibility import visible_message_filter

    m = (await db.execute(select(Message).where(
        Message.id == mid, Message.user_id == p.user_id, Message.draft.is_(True), Message.deleted.is_(False),
        visible_message_filter(p)))).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, "draft not found")
    return m


async def live_content(db: AsyncSession, p, m: Message) -> dict[str, Any]:
    """The draft's editable content for live editing (core/live.py): its compose payload as stored."""
    stored = (m.upstream or {}).get("draft_payload")
    return json.loads(json.dumps(stored)) if stored else await draft_payload(db, p, m)


async def live_version(db: AsyncSession, p, m: Message) -> int:
    from mailr.core import live

    return await live.current(db, p.user_id, "draft", m.id, live_loader(db, p, m))


def live_loader(db: AsyncSession, p, m: Message):
    """`live_content` as a loader for core/live.py (`current`, `resolve`)."""
    from mailr.core import live

    return live.loader(db, m, lambda m: live_content(db, p, m))


async def save_draft(db: AsyncSession, p, payload: dict[str, Any], mid: int | None, *,
                     base_version: int | None = None, by: str | None = None) -> Message:
    """Save a draft. A change to a saved draft made from `base_version` merges with what was
    saved since then (core/live.py; raises live.LiveConflict when both changed the same field or
    paragraph). The saved message carries `live_version`, `live_payload` (what was stored) and
    `live_merged`."""
    from mailr.core import live
    from mailr.core.compose import AddressError, HeaderInjection

    store = await user_store(db, p.user)
    m: Message | None = None
    merged = False
    if mid is not None:
        m = await load_draft(db, p, mid)
        # The draft is read again under the item's lock (live.resolve): a save that committed after
        # load_draft is merged with, never stored over.
        payload, merged = await live.resolve(db, p.user_id, "draft", m.id, live_loader(db, p, m),
                                             payload, base_version, m)
    try:
        raw, ident, parent = await build_for_payload(db, p, payload)
    except (HeaderInjection, AddressError) as e:
        # A line break in the subject or a malformed address is the caller's mistake.
        raise HTTPException(400, str(e)) from None
    drafts = await store.role_label(db, "drafts")
    if m is not None:
        old_jmap_id = m.jmap_id
        stored = await store.import_message(db, raw, [drafts.id], {"$draft": True, "$seen": True}, None)
        if old_jmap_id:
            # The copy this save replaces goes only after the save commits (a job in this
            # transaction): a failed commit leaves the row pointing at a copy that still exists.
            # It used to be destroyed here, before the commit.
            from mailr.core.jobs import enqueue

            await enqueue(db, "drafts.destroy_replaced", {"user_id": p.user_id, "jmap_id": old_jmap_id},
                          user_id=p.user_id, dedupe_key=f"drafts.destroy_replaced:{p.user_id}:{old_jmap_id}")
    else:
        stored = await store.import_message(db, raw, [drafts.id], {"$draft": True, "$seen": True}, None)
        m = Message(user_id=p.user_id, draft=True, seen=True, sent_by_me=True, intel_state="skipped")
        db.add(m)
    pz = await asyncio.to_thread(parse, raw)  # off the event loop
    m.jmap_id, m.jmap_thread_id, m.blob_id = stored["jmap_id"], stored["thread_id"], stored["blob_id"]
    m.connector_id = ident.connector_id
    if ident.connector_id:
        from mailr.models import Connector

        conn = await db.get(Connector, ident.connector_id)
        m.local_only = bool(conn and conn.data_class == "local_only")
    if parent is not None and parent.local_only:
        m.local_only = True  # a reply quoting local-only mail stays local-only
    m.message_id_hdr = pz.message_id
    m.in_reply_to, m.references = pz.in_reply_to, pz.references
    m.subject, m.norm_subject = pz.subject, norm_subject(pz.subject)
    m.from_email, m.from_name = pz.from_email, pz.from_name
    m.to, m.cc = pz.to, pz.cc
    m.bcc = payload.get("bcc") or []
    m.date = pz.date
    m.received_at = pz.date
    m.size, m.snippet, m.body_text = pz.size, pz.snippet, pz.body_text
    m.attached_text = pz.attached_text or None
    m.attachments, m.has_attachments = pz.attachments, bool(pz.attachments)
    m.label_ids = [drafts.id]
    m.upstream = {"draft_payload": json.loads(json.dumps(payload))}
    await db.flush()
    if m.conversation_id is None or (parent and m.conversation_id != parent.conversation_id):
        if parent and parent.conversation_id:
            m.conversation_id = parent.conversation_id
            await db.flush()
            await threading.refresh(db, p.user_id, [parent.conversation_id])
        else:
            await threading.assign(db, m)
    else:
        await threading.refresh(db, p.user_id, [m.conversation_id])
    m.live_version = await live.commit(db, p.user_id, "draft", m.id, m.upstream["draft_payload"], by=by)
    m.live_payload = m.upstream["draft_payload"]
    m.live_merged = merged
    return m


async def draft_payload(db: AsyncSession, p, m: Message) -> dict[str, Any]:
    pl = (m.upstream or {}).get("draft_payload")
    if pl:
        if pl.get("html") and _missing_uploads(p.user_id, pl["html"]):
            store = await user_store(db, p.user)
            pl = {**pl, "html": restore_inline_images(p.user_id, pl["html"], await store.get_raw(m))}
        return pl
    # A draft made in another client: rebuild a payload from the stored message.
    store = await user_store(db, p.user)
    raw = await store.get_raw(m)
    pz = await asyncio.to_thread(parse, raw)  # off the event loop
    ident = (await db.execute(select(Identity).where(Identity.user_id == p.user_id,
                                                     Identity.email == pz.from_email))).scalars().first()
    return {"identity_id": ident.id if ident else None, "to": pz.to, "cc": pz.cc, "bcc": pz.bcc,
            "subject": pz.subject, "html": restore_inline_images(p.user_id, pz.html, raw) if pz.html else None,
            "text": pz.text if not pz.html else None, "attachments": []}


async def discard_draft(db: AsyncSession, p, mid: int) -> None:
    from mailr.core import live

    m = await load_draft(db, p, mid)
    store = await user_store(db, p.user)
    await store.destroy([m])
    m.deleted = True
    m.jmap_id = None
    await db.flush()
    from mailr.core import purge

    await purge.queue(db, p.user_id, [m.id], export=False)  # drafts are not exported
    await live.forget(db, p.user_id, "draft", m.id)
    if m.conversation_id:
        await threading.refresh(db, p.user_id, [m.conversation_id])


def _matches(addr: str, patterns: list[str]) -> bool:
    addr = addr.lower()
    for pat in patterns:
        pat = pat.lower()
        if pat.startswith("*@") and addr.endswith(pat[1:]):
            return True
        if addr == pat:
            return True
    return False


async def pick_identity(db: AsyncSession, uid: int, m: Message) -> Identity | None:
    """The identity a reply should come from: the address the message was sent to."""
    idents = list((await db.execute(select(Identity).where(Identity.user_id == uid))).scalars())
    targets = [a["email"] for a in (m.to or []) + (m.cc or [])] + list(m.delivered_to or [])
    for t in targets:
        for i in idents:
            if t.lower() == i.email.lower():
                return i
    for t in targets:
        for i in idents:
            if _matches(t, i.receives or []):
                return i
    return next((i for i in idents if i.is_default), idents[0] if idents else None)


async def quote_images(db: AsyncSession, p, m: Message, images: str) -> tuple[bool, str | None]:
    """(allow, proxy base) for the remote images of a quoted or forwarded body: the reader's
    decision. Shown only when the caller says the reader shows them ("allowed"), never in Spam
    or with the "never" setting, and only through the image proxy; otherwise each image keeps
    its address in data-mailr-src with an empty src (the editor shows a placeholder)."""
    from mailr.core import imageproxy, publicid

    if images != "allowed" or imageproxy.user_mode(p.user) == "never" or await imageproxy.is_junk(db, m):
        return False, None
    on, _ = await imageproxy.instance_config(db)
    if not on:
        return False, None
    pid = publicid.encode("message", m.id) if publicid.enabled() else m.id
    return True, imageproxy.proxy_path(pid, "")


async def quote_date(db: AsyncSession, user: Any, when: Any, lang: str) -> str:
    """When the quoted message was sent, on the user's calendar and clock and in their language
    ("Tue 6 Oct 2026, 14:05"; "2:05 PM" on a 12-hour clock)."""
    if when is None:
        return ""
    from mailr.calendar import tz

    local = when.astimezone(await tz.user_zone(db, user))
    clock = (f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"
             if tz.user_clock(user) == "12h" else i18n.time_text(local, lang))
    return i18n.t("mail.date.day_time", lang, date=i18n.day_text(local, lang), time=clock)


async def reply_payload(db: AsyncSession, p, message_id: int, mode: str, images: str = "blocked") -> dict[str, Any]:
    import asyncio

    from mailr.api.mail_read import load_message
    from mailr.core.render import render_body

    m = await load_message(db, p, message_id)
    ident = await pick_identity(db, p.user_id, m)
    store = await user_store(db, p.user)
    raw = await store.get_raw(m)
    # Remote images never load straight from the sender's server (core/render.py, compose.py).
    allow, base = await quote_images(db, p, m, images)
    body = await asyncio.to_thread(render_body, raw, m.id, allow, base, True)
    lang = i18n.user_language(p.user)
    when = await quote_date(db, p.user, m.date, lang)
    who = f"{m.from_name} <{m.from_email}>" if m.from_name else m.from_email
    mine = {i.email.lower() for i in (await db.execute(select(Identity).where(Identity.user_id == p.user_id))).scalars()}
    mine.add(p.user.email.lower())

    payload: dict[str, Any] = {"identity_id": ident.id if ident else None, "attachments": [],
                               "to": [], "cc": [], "bcc": []}
    if mode == "forward":
        payload["subject"] = reply_subject(m.subject, forward=True)
        line = lambda k, v: i18n.t(f"mail.quote.{k}", lang, value=htmllib.escape(v or "")) + "<br>"  # noqa: E731
        hdr = (
            f'<br><div class="mailr_forward">{i18n.t("mail.quote.forwarded", lang)}<br>'
            + line("from", who) + line("date", when) + line("subject", m.subject)
            + line("to", ", ".join(a["email"] for a in m.to or [])) + "</div><br>"
        )
        # The original's inline images go along as uploads, and out again as cid: parts.
        payload["html"] = "<div><br></div>" + hdr + data_images_to_uploads(p.user_id, clean_html(body["html"]))
        payload["forward_of_message_id"] = m.id
        # Carry the original attachments into the upload area.
        import hashlib

        from mailr.api.mail_write import upload_dir
        from mailr.core.mime import get_part

        for a in m.attachments or []:
            if a.get("inline"):
                continue
            data, ctype, fname = get_part(raw, a["part"])
            blob = hashlib.sha256(data).hexdigest()[:32]
            (upload_dir(p.user_id) / blob).write_bytes(data)
            (upload_dir(p.user_id) / f"{blob}.json").write_text(json.dumps({"filename": fname, "type": ctype, "size": len(data)}))
            payload["attachments"].append({"blob": blob, "filename": fname, "type": ctype, "size": len(data)})
        return payload

    payload["subject"] = reply_subject(m.subject)
    payload["in_reply_to_message_id"] = m.id
    if m.sent_by_me:
        payload["to"] = list(m.to or [])
        if mode == "reply_all":
            payload["cc"] = list(m.cc or [])
    else:
        payload["to"] = list(m.reply_to or []) or [{"email": m.from_email, "name": m.from_name}]
        if mode == "reply_all":
            seen = {a["email"].lower() for a in payload["to"]} | mine
            cc = []
            for a in (m.to or []) + (m.cc or []):
                e = a["email"].lower()
                if e not in seen:
                    seen.add(e)
                    cc.append(a)
            payload["cc"] = cc
    # Images in the quote become uploads too; they are sent only if the quote is kept.
    quoted = data_images_to_uploads(p.user_id, body["html"]) if body["html"] else body["html"]
    payload["html"] = "<div><br></div><br>" + quote_block(who, when, quoted, body["text"], lang)
    return payload
