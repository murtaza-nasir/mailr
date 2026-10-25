"""What mailr writes to Speakr.

- New recording (recording.record, class speakr.upload): a file from the app or the web goes to
  POST /recordings/upload with the title, meeting date, notes, folder and tags the user gave. On a
  Speakr with upload metadata (capabilities `external_refs`) the participants and an `event`
  reference to the linked mailr event go on the upload too; on an older one the participants
  follow with a PATCH and the event link stays in mailr. This is the user's own new recording, so
  its metadata is set even while edits of existing recordings are off.
- Upload (mail.to_speakr, class speakr.upload): an audio or video attachment goes to
  POST /recordings/upload with notes "From mail: <subject> (<mailr link>)" and the message date as
  the file date. On a newer Speakr it also carries a `conversation` reference and an
  idempotency key (`mailr-<message id>:<part>`, capabilities `upload_idempotency`), so a retry after
  a timeout returns the same recording. The conversation is linked at once, and a mirror row with
  Speakr's answer (title, status) is added so the card shows "processing" until the next sync.
  An attachment of a local-only message (its own mark or its account's data class) goes only to a
  Speakr account marked local-only: the route, the assistant's tool and the approval's executor
  refuse any other (LOCAL_ONLY_UPLOAD), so neither the file nor the subject in the notes and the
  conversation reference leave for an account whose models may be hosted.
- Edit (recording.edit, class speakr.write): the title, the participants, a line appended to the
  notes (the notes are read fresh, the line added, and the whole text written back), and the tags
  (PUT /recordings/{id}/tags; on an older Speakr, POST and DELETE per tag). A group tag that shares
  the recording with its group is a share: tokens and the assistant need `speakr.share` for it,
  and an errand cannot add one.
- Links (recording.link, behind write-back): with write-back on and a Speakr with external
  references, mailr's confirmed event and conversation links of a recording are written to it as
  references (PUT /recordings/{id}/external-refs?system=mailr), so Speakr shows them and another
  mailr finds them first (matching.py). Unlinking removes the reference.
- Share (recording.share_link, class speakr.share): a public link, created or reused. On a Speakr
  with v1 share links (capabilities `share_links`), mailr uses POST /api/v1/recordings/{id}/share:
  a reused link keeps its flags, and the answer says whether they differ from the request; the
  sheet lists the links and revokes them. On an older Speakr, the web route. Anyone with the link
  can open the recording.

A recording another user shared with the account is edited only when its share allows edits,
and only its owner can make a public link.

The user's own clicks are not gated. Tokens and the assistant go through the
permission engine: speakr.upload and speakr.write ask by default, and speakr.share always asks
(a hard rule in core/permissions.py: a public link is a send to anyone). An errand never shares
(agent/speakr_tools.py). Edits and share links stay off until the user turns them on in the
connector card (config `write_back` and `share_links`, both false by default).
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import permissions
from mailr.integrations.speakr import client as sc
from mailr.integrations.speakr import store
from mailr.models import Connector, SpeakrRecording

MEDIA_EXT = (".mp3", ".m4a", ".wav", ".ogg", ".oga", ".opus", ".flac", ".aac", ".amr", ".wma", ".weba", ".webm",
             ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".3gp", ".3gpp")
WRITE_OFF = "Edits to Speakr are off for this account. Turn them on in Settings → Mail → Mail accounts → Speakr."
SHARE_OFF = "Share links are off for this account. Turn them on in Settings → Mail → Mail accounts → Speakr."


SHARED_READ_ONLY = "This recording was shared with you without edit rights in Speakr."
SHARED_NO_LINK = "Only the owner of a shared recording can make a public link to it."
LOCAL_ONLY_UPLOAD = ("This email comes from a local-only account, and this Speakr account is not marked "
                     "local-only, so its recording and subject cannot be sent there. Choose a Speakr account "
                     "marked local-only.")


class WriteOff(PermissionError):
    """The connector's switch for this kind of write is off."""


def event_ref(uid: str, occurrence: str = "") -> str:
    """mailr's reference to an event occurrence in Speakr: "<uid>" or "<uid>@<occurrence>"
    (matching.parse_event_ref reads it back)."""
    return f"{uid}@{occurrence}" if occurrence else uid


def _event_url(uid: str, occurrence: str = "") -> str:
    from urllib.parse import quote

    from mailr.config import get_settings

    base = get_settings().base_url.rstrip("/")
    return f"{base}/calendar/event/{quote(uid, safe='')}/{quote(occurrence or '', safe='')}"


def conversation_ref(conversation_id: int) -> str:
    from mailr.core import publicid

    return publicid.token("conversation", int(conversation_id))


def is_media(ctype: str, filename: str) -> bool:
    c = (ctype or "").lower()
    return c.startswith(("audio/", "video/")) or (filename or "").lower().endswith(MEDIA_EXT)


def media_type(ctype: str, filename: str) -> str:
    c = (ctype or "").lower()
    if c.startswith(("audio/", "video/")):
        return c
    ext = (filename or "").lower().rsplit(".", 1)[-1]
    kind = "video" if ext in ("mp4", "m4v", "mov", "mkv", "avi", "webm", "3gp", "3gpp") else "audio"
    sub = {"m4a": "mp4", "oga": "ogg", "weba": "webm", "mp3": "mpeg", "mov": "quicktime"}.get(ext, ext)
    return f"{kind}/{sub}"


async def message_local_only(db: AsyncSession, m: Any) -> bool:
    """Is this message local-only: its own mark, or the data class of the account it came from."""
    if getattr(m, "local_only", False):
        return True
    cid = getattr(m, "connector_id", None)
    if cid is None:
        return False
    c = await db.get(Connector, cid)
    return c is not None and c.data_class == "local_only"


def upload_allowed(message_local_only: bool, conn: Connector) -> bool:
    """A local-only message's attachment and subject go only to a Speakr account marked local-only."""
    return not message_local_only or conn.data_class == "local_only"


async def check_upload_class(db: AsyncSession, p: Any, message_id: int, conn_id: int) -> None:
    """Refuse, before any approval is asked, an upload of a local-only message's attachment to a
    Speakr account that is not local-only (WriteOff). A message the caller cannot see passes here;
    upload_attachment refuses it later."""
    from mailr.models import Message

    m = await db.get(Message, int(message_id))
    conn = await db.get(Connector, int(conn_id))
    if m is None or conn is None or m.user_id != p.user_id:
        return
    if not upload_allowed(await message_local_only(db, m), conn):
        raise WriteOff(LOCAL_ONLY_UPLOAD)


def require(conn: Connector, key: str) -> None:
    if not (conn.config or {}).get(key):
        raise WriteOff(WRITE_OFF if key == "write_back" else SHARE_OFF)


def recording_ctx(conn: Connector, row: SpeakrRecording) -> dict[str, Any]:
    """The permission context of a recording: its connector and its tag names (the `tags` constraint)."""
    return {"connectors": [conn.id], "tags": [t.get("name") for t in row.tags or [] if t.get("name")]}


async def connector_for_upload(db: AsyncSession, p: Any, cid: int | None) -> Connector:
    """The account an upload goes to: `cid`, else the caller's first usable Speakr account. The
    routes and tools resolve it BEFORE the permission check, so a rule limited to one account
    (`connectors`) holds when the caller leaves the account out."""
    conns = [c for c in await store.visible_connectors(db, p) if c.enabled and not (c.state or {}).get("auth_failed")]
    if cid is not None:
        conns = [c for c in conns if c.id == int(cid)]
    if not conns:
        raise LookupError("no Speakr account is connected (Settings → Mail → Mail accounts → Speakr)")
    return conns[0]


# ACT mail.to_speakr @api
async def upload_attachment(db: AsyncSession, p: Any, message_id: int, part: int, cid: int | None = None, *,
                            source: str = "user") -> dict[str, Any]:
    """Send one attachment to Speakr. `source` is "user", "assistant", or "order" for an errand
    (a recording an errand uploaded never starts a recording order: the chain guard)."""
    from mailr.api.mail_read import load_message
    from mailr.core.mime import get_part
    from mailr.core.store import user_store
    from mailr.integrations.speakr.mail import link_conversation
    from mailr.intel.citations import url_for

    m = await load_message(db, p, int(message_id))
    att = next((a for a in m.attachments or [] if int(a.get("part", -1)) == int(part)), None)
    if att is None:
        raise LookupError("attachment not found")
    conn = await connector_for_upload(db, p, cid)
    local = await message_local_only(db, m)
    if not upload_allowed(local, conn):
        raise WriteOff(LOCAL_ONLY_UPLOAD)
    raw = await (await user_store(db, p.user)).get_raw(m)
    try:
        data, ctype, fname = get_part(raw, int(part))
    except KeyError:
        raise LookupError("attachment not found") from None
    fname = att.get("filename") or fname or "recording"
    if not is_media(ctype, fname):
        raise ValueError("Only audio and video files can be transcribed")
    link = url_for(m.conversation_id) or ""
    subject = re.sub(r"\s+", " ", m.subject or "(no subject)").strip()[:200]
    notes = f"From mail: {subject} ({link})" if link else f"From mail: {subject}"
    when = m.date or m.received_at
    cl = await store.client_for(db, conn)
    extra: dict[str, Any] = {}
    if store.feature(conn, "external_refs") and m.conversation_id is not None:
        extra["refs"] = [{"system": "mailr", "kind": "conversation", "ref": conversation_ref(m.conversation_id),
                          **({"url": link[:1000]} if link else {}), "label": subject[:200]}]
    if store.feature(conn, "upload_idempotency"):
        extra["idempotency_key"] = f"mailr-{int(message_id)}:{int(part)}"
    try:
        rec = await cl.upload(fname, data, media_type(ctype, fname), notes=notes,
                              file_last_modified=int(when.timestamp() * 1000) if when else None, **extra)
    except sc.SpeakrAuthError:
        await store.mark_auth_failed(db, conn)
        raise
    rid = int(rec["id"])
    meta = sc.recording_meta({"title": fname.rsplit(".", 1)[0], **rec, "id": rid})
    row = (await db.execute(select(SpeakrRecording).where(SpeakrRecording.connector_id == conn.id,
                                                          SpeakrRecording.recording_id == rid))).scalar_one_or_none()
    if row is None:
        # Speakr's answer as a first mirror row (status pending); the next sync completes it.
        db.add(SpeakrRecording(user_id=conn.user_id, connector_id=conn.id, content_hash="", **meta))
    if m.conversation_id is not None:
        await link_conversation(db, conn, rid, m.conversation_id, source="order" if source == "order" else "upload")
    from mailr.integrations.speakr.sync import queue_sync

    await queue_sync(db, conn)
    await db.flush()
    return {"connector_id": conn.id, "recording_id": rid, "title": meta["title"], "status": meta["status"] or "pending",
            "url": store.deep_link(conn, rid), "notes": notes, "existing": bool(rec.get("idempotent_replay")),
            "local_only": local or conn.data_class == "local_only"}


def _pick(items: list[dict[str, Any]], wanted: str | int, what: str) -> dict[str, Any]:
    """A folder or tag by id or by name (case does not matter)."""
    w = str(wanted).strip()
    for it in items:
        if w.isdigit() and int(w) == it["id"]:
            return it
    for it in items:
        if it["name"].strip().lower() == w.lower():
            return it
    raise ValueError(f"no {what} “{w}” in Speakr")


def _iso(v: str | None) -> str | None:
    """An ISO 8601 date-time as UTC with Z (a time without zone is taken as UTC, as Speakr does)."""
    if not v or not str(v).strip():
        return None
    d = sc.parse_dt(str(v))
    if d is None:
        raise ValueError("meeting_date must be an ISO 8601 date-time, such as 2026-10-01T14:00:00-05:00")
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


# ACT recording.record @api
async def create_recording(db: AsyncSession, p: Any, *, filename: str, content: Any, content_type: str,
                           title: str = "", participants: list[str] | None = None, meeting_date: str | None = None,
                           notes: str = "", folder: str | int | None = None, tags: list[str | int] | None = None,
                           event_uid: str | None = None, occurrence: str = "", cid: int | None = None,
                           source: str = "user") -> dict[str, Any]:
    """A new recording made in mailr (the app's recorder or a file the user picked). This is the
    user's own new recording, so mailr sets its metadata even though edits of existing recordings
    stay off by default.

    Speakr's upload takes the file with notes, title, meeting_date, folder_id and tag_ids; the
    participants follow at once with a PATCH, and so does any field the upload answer does not
    show (an older Speakr ignores the newer upload fields). A field Speakr refuses is named in
    `unsupported_fields`. The event link lives in mailr only (Speakr has no external reference,
    on a newer Speakr): `event_uid` makes a confirmed link and is named in `kept_in_mailr`. A mirror row with
    status pending is added at once, and the next sync completes it."""
    if not is_media(content_type, filename):
        raise ValueError("Only audio and video files can be transcribed")
    conn = await connector_for_upload(db, p, cid)
    want_title = re.sub(r"\s+", " ", title or "").strip()[:500]
    people = [x.strip() for x in participants or [] if x and x.strip()][:50]
    when = _iso(meeting_date)
    folder_it = _pick(await store.listing(db, conn, "folders"), folder, "folder") if folder not in (None, "") else None
    tag_its: list[dict[str, Any]] = []
    if tags:
        known = await store.listing(db, conn, "tags")
        for t in tags:
            it = _pick(known, t, "tag")
            if it not in tag_its:
                tag_its.append(it)
    if event_uid:
        from mailr.calendar import mirror as cal_mirror

        if await cal_mirror.find_by_uid(db, p.user_id, event_uid, "event", p) is None:
            raise LookupError("event not found")
    cl = await store.client_for(db, conn)
    # A Speakr with upload metadata takes the participants and the event reference on the
    # upload itself; an older one gets the participants by PATCH and keeps no reference.
    g8 = store.feature(conn, "external_refs")
    extra: dict[str, Any] = {}
    if g8 and people:
        extra["participants"] = ", ".join(people)[:500]
    if g8 and event_uid:
        ev = await cal_mirror_title(db, p, event_uid)
        extra["refs"] = [{"system": "mailr", "kind": "event", "ref": event_ref(event_uid, occurrence or "")[:500],
                          "url": _event_url(event_uid, occurrence or "")[:1000], **({"label": ev[:200]} if ev else {})}]
    try:
        rec = await cl.upload(filename, content, media_type(content_type, filename), notes=(notes or "")[:20000],
                              title=want_title or None, meeting_date=when,
                              folder_id=folder_it["id"] if folder_it else None, tag_ids=[t["id"] for t in tag_its],
                              **extra)
    except sc.SpeakrAuthError:
        await store.mark_auth_failed(db, conn)
        raise
    rid = int(rec["id"])
    unsupported: dict[str, str] = {}
    fix: dict[str, Any] = {}
    sent_people = g8 and "participants" in rec and sc.split_participants(rec.get("participants")) == people
    if people and not sent_people:
        fix["participants"] = ", ".join(people)[:2000]
    if want_title and "title" in rec and rec.get("title") != want_title:
        fix["title"] = want_title
    if when and "meeting_date" in rec and sc.parse_dt(rec.get("meeting_date")) != sc.parse_dt(when):
        fix["meeting_date"] = when
    if folder_it and "folder_id" in rec and rec.get("folder_id") != folder_it["id"]:
        fix["folder_id"] = folder_it["id"]
    if fix:
        try:
            await cl.patch_recording(rid, fix)
        except sc.SpeakrAuthError:
            await store.mark_auth_failed(db, conn)
            raise
        except sc.SpeakrError as e:
            unsupported.update({k: str(e) for k in fix})
    if tag_its and isinstance(rec.get("tags"), list):
        have = {t.get("id") for t in rec["tags"] if isinstance(t, dict)}
        missing = [t["id"] for t in tag_its if t["id"] not in have]
        if missing:
            try:
                await cl.add_tags(rid, missing)
            except sc.SpeakrError as e:
                unsupported["tags"] = str(e)
    meta = sc.recording_meta({**rec, "id": rid, "title": want_title or rec.get("title") or filename.rsplit(".", 1)[0],
                              "participants": ", ".join(people) or rec.get("participants"),
                              "meeting_date": when or rec.get("meeting_date"),
                              "folder": {"id": folder_it["id"], "name": folder_it["name"]} if folder_it else rec.get("folder"),
                              "tags": [{"id": t["id"], "name": t["name"], "color": t.get("color")} for t in tag_its]
                              or rec.get("tags") or []})
    meta["status"] = meta["status"] or "pending"
    row = (await db.execute(select(SpeakrRecording).where(SpeakrRecording.connector_id == conn.id,
                                                          SpeakrRecording.recording_id == rid))).scalar_one_or_none()
    if row is None:
        db.add(SpeakrRecording(user_id=conn.user_id, connector_id=conn.id, content_hash="", **meta))
        await db.flush()
    link = None
    if event_uid:
        from mailr.integrations.speakr.service import set_link

        link = store.link_out(await set_link(db, p, rid=rid, cid=conn.id, kind="event", target=event_uid,
                                             occurrence=occurrence or "", source=source))
    from mailr.integrations.speakr.sync import queue_sync

    await queue_sync(db, conn)
    await db.flush()
    in_speakr = bool(event_uid) and any(r.get("kind") == "event" for r in sc.external_refs(rec))
    return {"connector_id": conn.id, "recording_id": rid, "title": meta["title"], "status": meta["status"],
            "url": store.deep_link(conn, rid), "unsupported_fields": sorted(unsupported),
            "unsupported_detail": unsupported, "kept_in_mailr": ["event_uid"] if event_uid and not in_speakr else [],
            "link": link}


async def cal_mirror_title(db: AsyncSession, p: Any, uid: str) -> str:
    from mailr.calendar import mirror as cal_mirror

    obj = await cal_mirror.find_by_uid(db, p.user_id, uid, "event", p)
    return (obj.summary or "") if obj else ""


# ACT recording.edit @api
async def update_recording(db: AsyncSession, p: Any, rid: int, cid: int | None = None, *, title: str | None = None,
                           participants: list[str] | None = None, append_notes: str | None = None,
                           tags: list[str | int] | None = None) -> dict[str, Any]:
    """Change the title and participants, append a line to the notes, and set the tags (names or
    ids; the recording's tags become exactly these). Returns the new values and the previous title,
    participants and tags (the web's Undo writes them back)."""
    conn, row = await store.find(db, p, rid, cid)
    require(conn, "write_back")
    if not row.can_edit:
        raise WriteOff(SHARED_READ_ONLY)
    fields: dict[str, Any] = {}
    if title is not None:
        t = re.sub(r"\s+", " ", title).strip()[:500]
        if not t:
            raise ValueError("the title cannot be empty")
        fields["title"] = t
    if participants is not None:
        fields["participants"] = ", ".join(x.strip() for x in participants if x and x.strip())[:2000]
    add = (append_notes or "").strip()[:4000]
    want_tags = await resolve_tags(db, conn, tags) if tags is not None else None
    if not fields and not add and want_tags is None:
        raise ValueError("nothing to change: give title, participants, append_notes or tags")
    previous = {"title": row.title, "participants": list(row.participants or []),
                "tags": [t.get("name") for t in row.tags or [] if t.get("name")]}
    cl = await store.client_for(db, conn)
    try:
        if want_tags is not None:
            current = [int(t["id"]) for t in row.tags or [] if isinstance(t.get("id"), int)]
            await cl.set_tags(row.recording_id, [t["id"] for t in want_tags], current)
            row.tags = [{"id": t["id"], "name": t["name"], "color": t.get("color")} for t in want_tags]
            row.content_hash = ""
        if fields:
            await cl.patch_recording(row.recording_id, fields)
            if "title" in fields:
                row.title = fields["title"]
            if "participants" in fields:
                row.participants = sc.split_participants(fields["participants"])
            row.content_hash = ""  # the next sync stores Speakr's own values
        if add:
            old = await cl.notes(row.recording_id)
            await cl.put_notes(row.recording_id, f"{old.rstrip()}\n\n{add}" if old.strip() else add)
    except sc.SpeakrAuthError:
        await store.mark_auth_failed(db, conn)
        raise
    store.forget(conn.id, row.recording_id)
    await db.flush()
    return {"recording": store.recording_out(conn, row), "previous": previous, "notes_appended": bool(add),
            "tags_set": want_tags is not None}


async def resolve_tags(db: AsyncSession, conn: Connector, tags: list[str | int]) -> list[dict[str, Any]]:
    """Tag names or ids as the account's Speakr tags, in order, without repeats."""
    known = await store.listing(db, conn, "tags")
    out: list[dict[str, Any]] = []
    for t in tags:
        it = _pick(known, t, "tag")
        if it not in out:
            out.append(it)
    return out


async def sharing_tags(db: AsyncSession, conn: Connector, row: SpeakrRecording, tags: list[str | int]) -> list[str]:
    """The tags among `tags` the recording does not have yet and that share it with a group in
    Speakr when applied: adding one is a share (speakr.share)."""
    have = {t.get("id") for t in row.tags or []}
    return [t["name"] for t in await resolve_tags(db, conn, tags) if t.get("shares") and t["id"] not in have]


async def sync_refs(db: AsyncSession, conn: Connector, rid: int) -> bool | None:
    """Write mailr's confirmed event and conversation links of a recording to Speakr as references
    (PUT ...?system=mailr). Only with write-back on and a Speakr with external references, and
    never for a shared recording without edit rights. Returns True when written, None when not
    applicable; a Speakr error is logged and the link in mailr stays."""
    import logging

    from mailr.integrations.speakr.service import links_of

    if not (conn.config or {}).get("write_back") or not store.feature(conn, "external_refs"):
        return None
    row = (await db.execute(select(SpeakrRecording).where(SpeakrRecording.connector_id == conn.id,
                                                          SpeakrRecording.recording_id == int(rid)))).scalar_one_or_none()
    if row is None or not row.can_edit:
        return None
    refs = []
    for lk in await links_of(db, conn, rid):
        # A link an email made (source "mail") stays in mailr until the user confirms it.
        if lk.state != "confirmed" or lk.source == "mail":
            continue
        if lk.kind == "event":
            refs.append({"system": "mailr", "kind": "event", "ref": event_ref(lk.target, lk.occurrence)[:500],
                         "url": _event_url(lk.target, lk.occurrence)[:1000]})
        elif lk.kind == "conversation" and lk.target.isdigit():
            from mailr.intel.citations import url_for

            u = url_for(int(lk.target))
            refs.append({"system": "mailr", "kind": "conversation", "ref": conversation_ref(int(lk.target)),
                         **({"url": u[:1000]} if u else {})})
    try:
        await (await store.client_for(db, conn)).put_refs(rid, refs)
    except sc.SpeakrAuthError:
        await store.mark_auth_failed(db, conn)
        return False
    except sc.SpeakrError as e:
        logging.getLogger("mailr.speakr").warning("could not write references of recording %s: %s", rid, e)
        return False
    row.mailr_refs = [{"kind": r["kind"], "ref": r["ref"]} for r in refs]
    return True


# ACT recording.share_link @api
async def share_recording(db: AsyncSession, p: Any, rid: int, cid: int | None = None, *,
                          share_notes: bool = False) -> dict[str, Any]:
    """A public Speakr link to the recording (with its summary; the notes only when asked)."""
    conn, row = await store.find(db, p, rid, cid)
    require(conn, "share_links")
    if row.is_shared:
        raise WriteOff(SHARED_NO_LINK)
    cl = await store.client_for(db, conn)
    v1 = store.feature(conn, "share_links")
    try:
        d = await cl.share(row.recording_id, share_summary=True, share_notes=share_notes, v1=v1)
    except sc.SpeakrAuthError:
        await store.mark_auth_failed(db, conn)
        raise
    out = {"connector_id": conn.id, "recording_id": row.recording_id, "title": row.title,
           "share_url": str(d["share_url"]), "existing": bool(d.get("existing")), "public": True}
    share = d.get("share") if isinstance(d.get("share"), dict) else None
    if share is not None:
        # v1: the link's own flags. A reused link keeps them, so notes may be shared (or not)
        # differently from what was asked; `flags_differ` says so.
        out.update(share_id=share.get("id"), share_summary=bool(share.get("share_summary")),
                   share_notes=bool(share.get("share_notes")), flags_differ=bool(d.get("flags_differ")),
                   expires_at=share.get("expires_at"))
    return out


async def list_shares(db: AsyncSession, p: Any, rid: int, cid: int | None = None) -> dict[str, Any]:
    """The recording's public links (v1 share links only; an older Speakr cannot list them)."""
    conn, row = await store.find(db, p, rid, cid)
    if not store.feature(conn, "share_links") or row.is_shared:
        return {"supported": False, "shares": []}
    try:
        rows = await (await store.client_for(db, conn)).shares(row.recording_id)
    except sc.SpeakrAuthError:
        await store.mark_auth_failed(db, conn)
        raise
    return {"supported": True, "shares": [{"id": s.get("id"), "share_url": s.get("share_url"),
                                          "share_summary": bool(s.get("share_summary")),
                                          "share_notes": bool(s.get("share_notes")),
                                          "created_at": s.get("created_at"), "expires_at": s.get("expires_at")}
                                         for s in rows]}


async def revoke_share(db: AsyncSession, p: Any, rid: int, cid: int | None, share_id: int) -> dict[str, Any]:
    """Revoke one public link of the recording (it must be one of the recording's links)."""
    conn, row = await store.find(db, p, rid, cid)
    if not store.feature(conn, "share_links"):
        raise ValueError("This Speakr cannot revoke links through its API; revoke the link in Speakr")
    cl = await store.client_for(db, conn)
    try:
        ids = {s.get("id") for s in await cl.shares(row.recording_id)}
        if int(share_id) not in ids:
            raise LookupError("share link not found")
        await cl.revoke_share(int(share_id))
    except sc.SpeakrAuthError:
        await store.mark_auth_failed(db, conn)
        raise
    return {"ok": True, "share_id": int(share_id)}


# Executors of approved requests (a token's REST call or an assistant tool that waited for
# approval). They act as the requester, with its connector limits and data class.


@permissions.executor("speakr.upload")
async def _x_upload(db: AsyncSession, user, payload: dict[str, Any]) -> dict[str, Any]:
    p = await permissions.requester(db, user, payload)
    out = await upload_attachment(db, p, int(payload["message_id"]), int(payload["part"]), payload.get("connector_id"),
                                  source=str(payload.get("source") or "assistant"))
    await db.commit()
    return out


@permissions.executor("speakr.write")
async def _x_write(db: AsyncSession, user, payload: dict[str, Any]) -> dict[str, Any]:
    p = await permissions.requester(db, user, payload)
    if payload.get("speaker_key"):
        # A token's speaker mapping whose address goes to Speakr (routes.map_speaker): map, then write.
        from mailr.integrations.speakr import people

        sid, rid = payload.get("speaker_id"), payload.get("recording_id")
        m = await people.map_speaker(db, p, int(payload["connector_id"]), key=str(payload["speaker_key"]),
                                     speaker_name=str(payload.get("speaker_name") or ""), email=str(payload["email"]),
                                     name=str(payload.get("name") or ""), source="assistant",
                                     speaker_id=int(sid) if isinstance(sid, int) else None)
        written = None
        if isinstance(sid, int) and isinstance(rid, int) and not m.speaker_key.startswith(("rec:", "label:")):
            written = await people.write_speaker_email(db, p, int(payload["connector_id"]), rid, sid, m.email)
        await db.commit()
        return people.mapping_out(m) | {"written_to_speakr": written}
    if payload.get("revoke_share") is not None:
        out = await revoke_share(db, p, int(payload["recording_id"]), payload.get("connector_id"),
                                 int(payload["revoke_share"]))
        await db.commit()
        return out
    out = await update_recording(db, p, int(payload["recording_id"]), payload.get("connector_id"),
                                 title=payload.get("title"), participants=payload.get("participants"),
                                 append_notes=payload.get("append_notes"), tags=payload.get("tags"))
    await db.commit()
    return out


@permissions.executor("speakr.share")
async def _x_share(db: AsyncSession, user, payload: dict[str, Any]) -> dict[str, Any]:
    p = await permissions.requester(db, user, payload)
    if payload.get("tags") is not None:
        # Tags that share the recording with a group in Speakr were approved as a share.
        out = await update_recording(db, p, int(payload["recording_id"]), payload.get("connector_id"),
                                     title=payload.get("title"), participants=payload.get("participants"),
                                     append_notes=payload.get("append_notes"), tags=payload.get("tags"))
        await db.commit()
        return out
    out = await share_recording(db, p, int(payload["recording_id"]), payload.get("connector_id"),
                                share_notes=bool(payload.get("share_notes")))
    await db.commit()
    return out
