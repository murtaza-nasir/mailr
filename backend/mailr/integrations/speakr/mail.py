"""Speakr notification emails and recordings in mail.

Speakr mails "Transcription ready: <title>" (or "Transcription failed: …") with a link
`<base>/recordings/<id>`. Such an email is recognised only by that link: its scheme, host, port
and path must match the base URL of one of the user's Speakr connectors, and the number after
/recordings/ is the only thing read from it. Subject, sender, title and summary in the email are
never trusted. The reader shows a recording card only when that id is in the caller's mirror
(own recordings of a connector the caller may see), so a forged email with a foreign host or with
an id the user cannot see shows nothing.

A recognised email links its conversation to the recording (recording_links, kind "conversation",
source "mail"), and only once the recording is in the mirror. The link is confirmed only when the
email authenticates as the Speakr server's own mail (`trusted_notice`: DMARC pass or a passing
DKIM signature aligned with the From domain, and that domain is the Speakr host's domain or a parent
of it) or is the user's own mail; any other sender's link is only suggested. Anyone can write such
a URL into an email, so a link from mail never makes a thread "the email linked to" a recording
for a recording order (orders.py) and is never written to Speakr as a reference (writes.sync_refs)
until the user confirms it. The links feed the list chip, `has:recording` and `recording:<text>` in search
(core/search.py), and the Links tab of the recording sheet. Links are made by the `speakr.mail`
job queued at ingest (core/ingest.py), by the reader card when it finds a link first, and by a scan
of recent mail after each sync (the mail may arrive before the recording is in the mirror).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import Text, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.jobs import enqueue, handler
from mailr.db import session_scope
from mailr.integrations.speakr import client as sc
from mailr.integrations.speakr import store
from mailr.models import Connector, Message, RecordingLink, SpeakrRecording, User

log = logging.getLogger("mailr.speakr.mail")

_URL = re.compile(r"https?://[^\s\"'<>()\[\]]+", re.IGNORECASE)
SCAN_DAYS = 30
SCAN_LIMIT = 200
SUBJECTS = ("transcription ready:", "transcription failed:")


def _origin(u) -> tuple[str, str, int | None]:
    port = u.port
    if port is None:
        port = 443 if u.scheme == "https" else 80
    return u.scheme.lower(), (u.hostname or "").lower(), port


def recording_refs(text: str, conns: list[Connector]) -> list[tuple[Connector, int]]:
    """(connector, recording id) for each URL in `text` that is exactly <base>/recordings/<id>
    (a trailing slash, a query such as ?t= or a fragment are allowed) on one of `conns`."""
    out: list[tuple[Connector, int]] = []
    bases = [(c, urlsplit(store.base_url(c))) for c in conns]
    for m in _URL.finditer(text or ""):
        url = m.group(0).rstrip(".,;:!?")
        try:
            u = urlsplit(url)
            origin = _origin(u)
        except ValueError:
            continue
        for conn, b in bases:
            if not b.hostname or origin != _origin(b):
                continue
            mm = re.fullmatch(re.escape(b.path.rstrip("/")) + r"/recordings/(\d{1,9})/?", u.path)
            if mm and (conn, int(mm.group(1))) not in out:
                out.append((conn, int(mm.group(1))))
    return out


def _maybe_speakr(m: Message) -> bool:
    subj = (m.subject or "").strip().lower()
    return subj.startswith(SUBJECTS) or "/recordings/" in (m.body_text or "")


async def _connectors(db: AsyncSession, user_id: int) -> list[Connector]:
    return list((await db.execute(select(Connector).where(Connector.user_id == user_id, Connector.kind == store.KIND)
                                  .order_by(Connector.id))).scalars())


async def message_text(db: AsyncSession, m: Message) -> str:
    """The message's plain text and HTML (links in the HTML count). Falls back to the indexed text."""
    from mailr.core.mime import parse
    from mailr.core.store import user_store

    try:
        user = await db.get(User, m.user_id)
        raw = await (await user_store(db, user)).get_raw(m)
        p = await asyncio.to_thread(parse, raw)  # off the event loop
        return f"{p.text or ''}\n{p.html or ''}"
    except Exception:  # noqa: BLE001  the store is unavailable: the indexed text still has plain links
        return m.body_text or ""


async def refs_of_message(db: AsyncSession, m: Message, conns: list[Connector] | None = None) -> list[tuple[Connector, int]]:
    conns = conns if conns is not None else await _connectors(db, m.user_id)
    if not conns or m.draft:
        return []
    return recording_refs(await message_text(db, m), conns)


def _aligned(auth: dict[str, Any], domain: str) -> bool:
    """A positive result for `domain`: DMARC pass for it, or a passing DKIM signature from it
    (or a parent) (core/authres.verified_for)."""
    from mailr.core import authres

    return authres.verified_for(auth, domain)


def trusted_notice(m: Message, conn: Connector) -> bool:
    """The email is the user's own, or authenticates for the Speakr host's domain (the From
    domain equals the host or a parent of it, and DMARC or aligned DKIM passed)."""
    if m.own_words:  # not a share from another app
        return True
    frm = (m.from_email or "").lower()
    if frm.count("@") != 1:
        return False
    dom = frm.rsplit("@", 1)[1].strip(".")
    host = (urlsplit(store.base_url(conn)).hostname or "").lower().strip(".")
    if not dom or "." not in dom or not (host == dom or host.endswith("." + dom)):
        return False
    return _aligned(m.auth_results or {}, dom)


async def _in_mirror(db: AsyncSession, conn: Connector, rid: int) -> bool:
    return (await db.execute(select(SpeakrRecording.id).where(
        SpeakrRecording.connector_id == conn.id, SpeakrRecording.recording_id == int(rid)).limit(1))).first() is not None


async def link_conversation(db: AsyncSession, conn: Connector, rid: int, conversation_id: int,
                            source: str = "mail", state: str = "confirmed") -> RecordingLink:
    """A conversation link (kept as it is when it exists: a dismissed one never returns, and a
    suggestion is confirmed only by a confirmed link of a trusted source)."""
    lk = (await db.execute(select(RecordingLink).where(
        RecordingLink.connector_id == conn.id, RecordingLink.recording_id == int(rid),
        RecordingLink.kind == "conversation", RecordingLink.target == str(conversation_id),
        RecordingLink.occurrence == ""))).scalar_one_or_none()
    if lk is None:
        lk = RecordingLink(user_id=conn.user_id, connector_id=conn.id, recording_id=int(rid), kind="conversation",
                           target=str(conversation_id), occurrence="", state=state, source=source)
        db.add(lk)
        await db.flush()
    elif lk.state == "suggested" and state == "confirmed":
        lk.state, lk.source, lk.updated_at = "confirmed", source, datetime.now(UTC)
    return lk


async def link_from_mail(db: AsyncSession, m: Message, conn: Connector, rid: int) -> RecordingLink | None:
    """The link an email makes: none while the recording is not in the mirror, confirmed for a
    trusted notification, else suggested."""
    if m.conversation_id is None or not await _in_mirror(db, conn, rid):
        return None
    return await link_conversation(db, conn, rid, m.conversation_id,
                                   state="confirmed" if trusted_notice(m, conn) else "suggested")


async def link_message(db: AsyncSession, m: Message) -> int:
    """Link the message's conversation to each recording it points at. A recording not yet in the
    mirror gets a sync, so the card and the chip show once Speakr lists it. Returns the links made."""
    if m.conversation_id is None:
        return 0
    n = 0
    for conn, rid in await refs_of_message(db, m):
        if await link_from_mail(db, m, conn, rid) is not None:
            n += 1
        elif conn.enabled and not (conn.state or {}).get("auth_failed"):
            # Not in the mirror yet: a sync, and the scan after it (scan_recent) links the email.
            from mailr.integrations.speakr.sync import queue_sync

            await queue_sync(db, conn)
    return n


async def maybe_enqueue(db: AsyncSession, m: Message) -> None:
    """At ingest (core/ingest.py): queue the check for mail that may come from Speakr."""
    if not _maybe_speakr(m):
        return
    has = (await db.execute(select(Connector.id).where(Connector.user_id == m.user_id,
                                                       Connector.kind == store.KIND).limit(1))).first()
    if has:
        await enqueue(db, "speakr.mail", {"message_id": m.id}, user_id=m.user_id, dedupe_key=f"speakr.mail:{m.id}")


@handler("speakr.mail")
async def mail_job(payload: dict[str, Any], ctx) -> None:
    async with session_scope() as db:
        m = await db.get(Message, int(payload["message_id"]))
        if m is None or m.deleted:
            return
        await link_message(db, m)
        await db.commit()


async def scan_recent(connector_id: int) -> int:
    """After a sync: link recent Speakr emails of the connector's user that are not linked yet."""
    n = 0
    async with session_scope() as db:
        conn = await db.get(Connector, connector_id)
        if conn is None:
            return 0
        host = (urlsplit(store.base_url(conn)).hostname or "").lower()
        if not host:
            return 0
        since = datetime.now(UTC) - timedelta(days=SCAN_DAYS)
        linked = select(RecordingLink.target).where(RecordingLink.connector_id == conn.id,
                                                    RecordingLink.kind == "conversation")
        rows = (await db.execute(select(Message).where(
            Message.user_id == conn.user_id, Message.deleted.is_(False), Message.draft.is_(False),
            Message.date >= since,
            (Message.subject.ilike("Transcription ready:%") | Message.subject.ilike("Transcription failed:%")
             | Message.body_text.ilike(f"%{host}%/recordings/%")),
            ~Message.conversation_id.cast(Text).in_(linked))
            .order_by(Message.date.desc()).limit(SCAN_LIMIT))).scalars().all()
        conns = [conn]
        for m in rows:
            for c, rid in await refs_of_message(db, m, conns):
                if await link_from_mail(db, m, c, rid) is not None:
                    n += 1
        await db.commit()
    return n


# ---------------------------------------------------------------- the reader card and the list chip


async def card(db: AsyncSession, p: Any, message_id: int, *, excerpt: bool = True) -> dict[str, Any]:
    """The recording card of one message: {"items": [...]} with one entry per recording the
    message points at that the caller can see. Nothing from the email is shown."""
    from mailr.api.mail_read import load_message
    from mailr.integrations.speakr import service

    m = await load_message(db, p, message_id)
    conns = await store.visible_connectors(db, p)
    if not conns:
        return {"connected": False, "items": []}
    items = []
    for conn, rid in await refs_of_message(db, m, conns):
        try:
            conn, row = await store.find(db, p, rid, conn.id, conns)
        except store.RecordingNotFound:
            continue  # not the caller's recording (or not synced yet): no card
        if m.conversation_id is not None and not p.is_token:
            await link_from_mail(db, m, conn, rid)
        item = store.recording_out(conn, row)
        item["failed"] = row.status == "failed"
        item["processing"] = row.status not in ("completed", "failed")
        events = [lk for lk in await service.links_of(db, conn, rid) if lk.kind == "event"]
        if events:
            item["links"] = await service.link_details(db, p, sorted(events, key=lambda lk: lk.state != "confirmed")[:1])
        if excerpt and row.has_summary:
            try:
                item["summary_excerpt"] = one_line(await store.part(db, conn, rid, "summary"), 300)
            except sc.SpeakrAuthError:
                raise
            except sc.SpeakrError:
                item["summary_excerpt"] = None
        items.append(item)
    await db.commit()
    return {"connected": True, "items": items}


def one_line(md: str, n: int = 200) -> str:
    """The first line of a Markdown summary that says something (headings and marks removed)."""
    lines = [re.sub(r"[*_`#>]+", "", x).strip(" -•\t") for x in (md or "").splitlines()]
    heads = {i for i, x in enumerate((md or "").splitlines()) if x.lstrip().startswith("#")}
    body = [x for i, x in enumerate(lines) if x and i not in heads]
    text = body[0] if body else next((x for x in lines if x), "")
    return text[:n]


async def linked_conversations(db: AsyncSession, p: Any, limit: int = 1000) -> list[int]:
    """Conversations linked to a recording the caller can see (the list's ▶ chip)."""
    conns = await store.visible_connectors(db, p)
    if not conns:
        return []
    rows = (await db.execute(select(RecordingLink.target).join(SpeakrRecording, (
        SpeakrRecording.connector_id == RecordingLink.connector_id)
        & (SpeakrRecording.recording_id == RecordingLink.recording_id)).where(
        RecordingLink.user_id == p.user_id, RecordingLink.kind == "conversation", RecordingLink.state != "dismissed",
        RecordingLink.connector_id.in_([c.id for c in conns]), SpeakrRecording.deleted_at.is_(None))
        .order_by(RecordingLink.id.desc()).limit(limit))).scalars().all()
    return list(dict.fromkeys(int(t) for t in rows if str(t).isdigit()))
