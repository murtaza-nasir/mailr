"""REST routes for Speakr recordings, under /api/v1/speakr.

Every route needs the `recordings` scope and shows only the recordings of the Speakr connectors
the caller may see (service.py). The agent tools are built on the same functions. Speakr errors
become HTTP errors: not found → 404, a revoked token → 502 with the reason, too many requests → 503 with
Retry-After, an unreachable server → 502.
"""

from __future__ import annotations

import builtins
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.auth import Principal, scope
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.integrations.speakr import action_items, followup, service, store
from mailr.integrations.speakr import client as sc

router = APIRouter(prefix="/speakr", tags=["speakr"])

INCLUDE = ("summary", "notes", "events", "speakers", "links")


def _http(e: Exception) -> HTTPException:
    if isinstance(e, (sc.SpeakrScopeError, sc.SpeakrRefused)):
        # The token lacks a scope, or Speakr turned the feature off: a setting, not a missing recording.
        return HTTPException(409, str(e))
    if isinstance(e, (store.RecordingNotFound, sc.SpeakrNotFound, LookupError)):
        if isinstance(e, sc.SpeakrNotFound):
            return ApiError(404, "speakr.recording_not_found")
        return HTTPException(404, str(e))
    if isinstance(e, sc.SpeakrRateLimited):
        return HTTPException(503, str(e), headers={"Retry-After": str(int(e.retry_after))})
    if isinstance(e, sc.SpeakrError):
        return ApiError(502, "speakr.error", error=str(e))
    if isinstance(e, ValueError):
        return HTTPException(400, str(e))
    raise e


def _model_error(e: Exception) -> HTTPException | None:
    from mailr.intel import llm

    if isinstance(e, llm.LocalOnlyViolation):
        return ApiError(409, "speakr.local_only_no_model")
    if isinstance(e, llm.NoModel):
        return ApiError(409, "speakr.no_model")
    return None


@router.get("/status")
async def status(p: Principal = Depends(scope("recordings")), db: AsyncSession = Depends(get_db)):
    """Whether the caller has a Speakr connector (the web shows recording features only then)."""
    conns = await store.visible_connectors(db, p)
    return {"connected": bool(conns),
            "connectors": [{"id": c.id, "name": c.name, "base_url": store.base_url(c), "status": c.status,
                            "data_class": c.data_class,
                            # Speakr searches every transcript; the mail search shows its hits.
                            "search": store.feature(c, "search", "keyword")} for c in conns]}


# ACT recording.search @api @web.recordings
@router.get("/recordings")
async def list_recordings(q: str = "", from_: str | None = Query(None, alias="from"), to: str | None = None, tag: str = "",
                          participant: str = "", status: str = "", linked: str = "", limit: int = 30, offset: int = 0,
                          annotate: bool = False, p: Principal = Depends(scope("recordings")),
                          db: AsyncSession = Depends(get_db)):
    """The mirror, newest first. `offset` pages through it (`more` says whether a next page may
    exist); `annotate` adds each recording's event link and its participants with the addresses
    mailr knows (as the Recordings app shows them)."""
    limit = min(max(limit, 1), service.MAX_LIST * 4)
    items = await service.list_recordings(db, p, q=q, date_from=from_, date_to=to, tag=tag, participant=participant,
                                          status=status, linked=linked, limit=limit, offset=max(0, offset),
                                          annotate=annotate)
    return {"items": items, "more": len(items) == limit and not linked}


# ACT recording.search
@router.get("/search")
async def search(q: str, from_: str | None = Query(None, alias="from"), to: str | None = None, top_k: int = 10,
                 p: Principal = Depends(scope("recordings")), db: AsyncSession = Depends(get_db)):
    try:
        return await service.search(db, p, q, date_from=from_, date_to=to, top_k=top_k)
    except sc.SpeakrError as e:
        raise _http(e) from None


# ACT recording.view @api @web.reader @web.pane
@router.get("/recordings/{cid}/{rid}")
async def get_recording(cid: int, rid: int, include: str = "summary,links", p: Principal = Depends(scope("recordings")),
                        db: AsyncSession = Depends(get_db)):
    parts = tuple(x for x in (include or "").split(",") if x in INCLUDE)
    try:
        conn, out = await service.get_recording(db, p, rid, cid, parts)
        if "links" in parts:
            out["candidates"] = await service.recording_candidates(db, p, conn, rid)
    except (store.RecordingNotFound, sc.SpeakrError) as e:
        raise _http(e) from None
    return out


# ACT recording.view
@router.get("/recordings/{cid}/{rid}/transcript")
async def transcript(cid: int, rid: int, start: float | None = None, end: float | None = None, speaker: str = "",
                     q: str = "", max_chars: int = 20000, p: Principal = Depends(scope("recordings")),
                     db: AsyncSession = Depends(get_db)):
    try:
        _, out = await service.get_transcript(db, p, rid, cid, start=start, end=end, speaker=speaker or None,
                                              q=q or None, max_chars=max_chars)
    except (store.RecordingNotFound, sc.SpeakrError) as e:
        raise _http(e) from None
    return out


async def _part(db: AsyncSession, p: Principal, cid: int, rid: int, name: str) -> Any:
    try:
        conn, row = await store.find(db, p, rid, cid)
        if name == "summary" and not row.has_summary:
            return ""
        return await store.part(db, conn, row.recording_id, name)
    except (store.RecordingNotFound, sc.SpeakrError) as e:
        raise _http(e) from None


@router.get("/recordings/{cid}/{rid}/summary")
async def summary(cid: int, rid: int, p: Principal = Depends(scope("recordings")), db: AsyncSession = Depends(get_db)):
    text = await _part(db, p, cid, rid, "summary")
    return {"summary": text, "has_summary": bool(text)}


@router.get("/recordings/{cid}/{rid}/notes")
async def notes(cid: int, rid: int, p: Principal = Depends(scope("recordings")), db: AsyncSession = Depends(get_db)):
    text = await _part(db, p, cid, rid, "notes")
    return {"notes": text, "has_notes": bool(text)}


@router.get("/recordings/{cid}/{rid}/speakers")
async def speakers(cid: int, rid: int, p: Principal = Depends(scope("recordings")), db: AsyncSession = Depends(get_db)):
    return {"speakers": await _part(db, p, cid, rid, "speakers")}


# ACT recording.event_to_calendar @api (the attendees, resolved to addresses for the event editor)
@router.get("/recordings/{cid}/{rid}/events")
async def events(cid: int, rid: int, p: Principal = Depends(scope("recordings")), db: AsyncSession = Depends(get_db)):
    evs = await _part(db, p, cid, rid, "events")
    return {"events": await service.resolve_attendees(db, p.user_id, cid, evs)}


# ---------------------------------------------------------------- links


# ACT recording.view (the event detail's Recording section)
@router.get("/events/recordings")
async def event_recordings(uid: str, occurrence: str = "", p: Principal = Depends(scope("recordings")),
                           db: AsyncSession = Depends(get_db)):
    conns = await store.visible_connectors(db, p)
    if not conns:
        return {"connected": False, "items": []}
    return {"connected": True, "items": await service.event_recordings(db, p, uid, occurrence or None)}


# ACT recording.view (the task pane's "From:" line)
@router.get("/tasks/recordings")
async def task_recordings(uid: str, p: Principal = Depends(scope("recordings")), db: AsyncSession = Depends(get_db)):
    """Recordings a task was created from or linked to, from the mirror only (no Speakr request).
    `t` is the moment the task's notes name with the recording's Speakr link, if any."""
    import re

    from sqlalchemy import select

    from mailr.calendar import mirror as cal_mirror
    from mailr.models import RecordingLink

    conns = await store.visible_connectors(db, p)
    if not conns:
        return {"connected": False, "items": []}
    by_id = {c.id: c for c in conns}
    links = (await db.execute(select(RecordingLink).where(
        RecordingLink.user_id == p.user_id, RecordingLink.kind == "task", RecordingLink.target == uid,
        RecordingLink.state != "dismissed", RecordingLink.connector_id.in_(list(by_id)))
        .order_by(RecordingLink.id))).scalars().all()
    if not links:
        return {"connected": True, "items": []}
    task = await cal_mirror.find_by_uid(db, p.user_id, uid, "task", p)
    if task is None:
        return {"connected": True, "items": []}
    notes = task.description or ""
    out = []
    for lk in links:
        try:
            conn, row = await store.find(db, p, lk.recording_id, lk.connector_id, conns)
        except store.RecordingNotFound:
            continue
        m = re.search(re.escape(store.deep_link(conn, row.recording_id)) + r"\?t=(\d+)", notes)
        out.append(store.recording_out(conn, row) | {"link": store.link_out(lk), "t": int(m.group(1)) if m else None})
    return {"connected": True, "items": out}


class LinkIn(BaseModel):
    connector_id: int | None = None
    recording_id: int
    kind: str = Field(pattern="^(event|conversation|task)$")
    target: str | None = Field(None, max_length=300)
    occurrence: str = Field("", max_length=40)
    conversation_id: int | None = None


async def _gate_link(db: AsyncSession, p: Principal, body: LinkIn, target: str) -> JSONResponse | None:
    """Tokens go through the permission engine (speakr.link, allowed by default); the user's own
    clicks are never gated."""
    if not p.is_token:
        return None
    from mailr.core import permissions

    # The recording's account first, so a rule limited to one account holds without connector_id.
    try:
        conn, _ = await store.find(db, p, body.recording_id, body.connector_id)
    except store.RecordingNotFound as e:
        raise _http(e) from None
    body.connector_id = conn.id
    payload = permissions.stamp({"recording_id": body.recording_id, "connector_id": body.connector_id,
                                 "kind": body.kind, "target": target, "occurrence": body.occurrence}, p)
    decision = await permissions.check(db, permissions.Actor.from_principal(p), "speakr.link", target=target,
                                       ctx={"connectors": [conn.id]},
                                       payload=payload, preview={"title": "Link a recording",
                                                                 "lines": [f"{body.kind}: {target}"]})
    permissions.raise_for(decision)
    if isinstance(decision, permissions.Pending):
        await db.commit()
        return JSONResponse(permissions.pending_response(decision.action), status_code=202)
    return None


# ACT recording.link @api
@router.post("/links")
async def create_link(body: LinkIn, p: Principal = Depends(scope("recordings")), db: AsyncSession = Depends(get_db)):
    target = str(body.conversation_id) if body.kind == "conversation" and body.conversation_id else (body.target or "")
    if not target:
        raise HTTPException(400, "target is required")  # i18n: internal
    pending = await _gate_link(db, p, body, target)
    if pending is not None:
        return pending
    try:
        lk = await service.set_link(db, p, rid=body.recording_id, cid=body.connector_id, kind=body.kind,
                                    target=target, occurrence=body.occurrence,
                                    source="user" if not p.is_token else "assistant")
    except (store.RecordingNotFound, LookupError, ValueError) as e:
        raise _http(e) from None
    await db.commit()
    return store.link_out(lk)


# ACT recording.unlink @api
@router.delete("/links/{link_id}")
async def delete_link(link_id: int, p: Principal = Depends(scope("recordings")), db: AsyncSession = Depends(get_db)):
    try:
        lk = await service.dismiss_link(db, p, link_id)
    except LookupError as e:
        raise _http(e) from None
    await db.commit()
    return store.link_out(lk)


class FollowUpIn(BaseModel):
    mode: str = Field("summary", pattern="^(summary|assistant)$")
    event_uid: str | None = Field(None, max_length=300)
    occurrence: str | None = Field(None, max_length=40)
    instructions: str = Field("", max_length=2000)


# ACT recording.follow_up @api
@router.post("/recordings/{cid}/{rid}/follow-up")
async def follow_up(cid: int, rid: int, body: FollowUpIn, p: Principal = Depends(scope("recordings")),
                    db: AsyncSession = Depends(get_db)):
    p.require("draft")
    try:
        return await followup.draft_follow_up(db, p, rid, cid, mode=body.mode, uid=body.event_uid,
                                              occurrence=body.occurrence, instructions=body.instructions)
    except Exception as e:  # noqa: BLE001
        raise (_model_error(e) or _http(e)) from None


# ACT recording.to_tasks @api
@router.post("/recordings/{cid}/{rid}/action-items")
async def propose_action_items(cid: int, rid: int, p: Principal = Depends(scope("recordings")),
                               db: AsyncSession = Depends(get_db)):
    try:
        return await action_items.propose(db, p, rid, cid)
    except Exception as e:  # noqa: BLE001
        raise (_model_error(e) or _http(e)) from None


class TaskItem(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    due: str | None = None
    t: float | None = None


class TasksIn(BaseModel):
    # `list` is a field name here, so the builtin is spelled out.
    items: builtins.list[TaskItem] = Field(min_length=1, max_length=25)
    list: str | None = None
    event_uid: str | None = Field(None, max_length=300)
    occurrence: str | None = Field(None, max_length=40)


# ACT recording.to_tasks
@router.post("/recordings/{cid}/{rid}/tasks")
async def create_tasks(cid: int, rid: int, body: TasksIn, p: Principal = Depends(scope("recordings")),
                       db: AsyncSession = Depends(get_db)):
    p.require("calendar")
    try:
        return await action_items.create(db, p, rid, cid, [i.model_dump() for i in body.items], task_list=body.list,
                                         event_uid=body.event_uid, occurrence=body.occurrence)
    except (store.RecordingNotFound, sc.SpeakrError, LookupError) as e:
        raise _http(e) from None


# ---------------------------------------------------------------- writes


def _write_http(e: Exception) -> HTTPException:
    from mailr.integrations.speakr import writes

    if isinstance(e, writes.WriteOff):
        return HTTPException(403, str(e))
    if isinstance(e, sc.SpeakrTooLarge):
        return HTTPException(413, str(e))
    if isinstance(e, HTTPException):
        return e
    return _http(e)


async def _gate(db: AsyncSession, p: Principal, action: str, *, target: str, ctx: dict[str, Any],
                payload: dict[str, Any], preview: dict[str, Any]) -> JSONResponse | None:
    """Tokens go through the permission engine (202 with the approval when it asks); the user's
    own clicks are never gated."""
    if not p.is_token:
        return None
    from mailr.core import permissions

    decision = await permissions.check(db, permissions.Actor.from_principal(p), action, target=target, ctx=ctx,
                                       payload=permissions.stamp(payload, p), preview=preview)
    permissions.raise_for(decision)
    if isinstance(decision, permissions.Pending):
        await db.commit()
        return JSONResponse(permissions.pending_response(decision.action), status_code=202)
    return None


# ACT recording.view (the reader's recording card)
@router.get("/mail-card")
async def mail_card(message_id: int, p: Principal = Depends(scope("recordings")), db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import mail

    p.require("read")
    try:
        return await mail.card(db, p, message_id)
    except sc.SpeakrError as e:
        raise _http(e) from None


# ACT recording.search (the list's ▶ chip)
@router.get("/linked-conversations")
async def linked_conversations(p: Principal = Depends(scope("recordings")), db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import mail

    return {"conversation_ids": await mail.linked_conversations(db, p)}


class UploadIn(BaseModel):
    message_id: int
    part: int
    connector_id: int | None = None


# ACT mail.to_speakr @api
@router.post("/upload")
async def upload(body: UploadIn, p: Principal = Depends(scope("recordings")), db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import writes

    p.require("read")
    # The account first: a rule limited to one account must see it even when the caller leaves it out.
    try:
        body.connector_id = (await writes.connector_for_upload(db, p, body.connector_id)).id
        await writes.check_upload_class(db, p, body.message_id, body.connector_id)
    except (LookupError, writes.WriteOff) as e:
        raise _write_http(e) from None
    pending = await _gate(db, p, "speakr.upload", target=f"message {body.message_id} part {body.part}",
                          ctx={"connectors": [body.connector_id]},
                          payload=body.model_dump(), preview={"title": "Transcribe an attachment in Speakr",
                                                              "lines": [f"message {body.message_id}, part {body.part}"]})
    if pending is not None:
        return pending
    try:
        out = await writes.upload_attachment(db, p, body.message_id, body.part, body.connector_id,
                                             source="user" if not p.is_token else "assistant")
    except Exception as e:  # noqa: BLE001
        raise _write_http(e) from None
    await db.commit()
    return out


class EditIn(BaseModel):
    title: str | None = Field(None, max_length=500)
    participants: builtins.list[str] | None = Field(None, max_length=50)
    append_notes: str | None = Field(None, max_length=4000)
    # The recording's tags become exactly these (names or ids).
    tags: builtins.list[str | int] | None = Field(None, max_length=50)


# ACT recording.edit @api
@router.patch("/recordings/{cid}/{rid}")
async def edit_recording(cid: int, rid: int, body: EditIn, p: Principal = Depends(scope("recordings")),
                         db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import writes

    try:
        conn, row = await store.find(db, p, rid, cid)
        writes.require(conn, "write_back")
        lines = [f"{k}: {v}" for k, v in body.model_dump(exclude_none=True).items()]
        # A tag that shares the recording with a group in Speakr makes this a share (speakr.share).
        sharing = await writes.sharing_tags(db, conn, row, body.tags) if body.tags else []
        if sharing:
            lines.append(f"Shares the recording with the group of: {', '.join(sharing)}")
        pending = await _gate(db, p, "speakr.share" if sharing else "speakr.write", target=row.title,
                              ctx=writes.recording_ctx(conn, row),
                              payload={"recording_id": rid, "connector_id": cid, **body.model_dump(exclude_none=True)},
                              preview={"title": f"Edit the recording “{row.title}” in Speakr", "lines": lines,
                                       **({"danger": True} if sharing else {})})
        if pending is not None:
            return pending
        out = await writes.update_recording(db, p, rid, cid, title=body.title, participants=body.participants,
                                            append_notes=body.append_notes, tags=body.tags)
    except Exception as e:  # noqa: BLE001
        raise _write_http(e) from None
    await db.commit()
    return out


class ShareIn(BaseModel):
    share_notes: bool = False


# ACT recording.share_link @api
@router.post("/recordings/{cid}/{rid}/share")
async def share(cid: int, rid: int, body: ShareIn | None = None, p: Principal = Depends(scope("recordings")),
                db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import writes

    body = body or ShareIn()
    try:
        conn, row = await store.find(db, p, rid, cid)
        writes.require(conn, "share_links")
        pending = await _gate(db, p, "speakr.share", target=row.title, ctx=writes.recording_ctx(conn, row),
                              payload={"recording_id": rid, "connector_id": cid, "share_notes": body.share_notes},
                              preview={"title": f"Create a public link to “{row.title}”", "danger": True,
                                       "lines": ["Anyone with the link can open the recording and its summary"
                                                 + (" and notes" if body.share_notes else "") + "."]})
        if pending is not None:
            return pending
        out = await writes.share_recording(db, p, rid, cid, share_notes=body.share_notes)
    except Exception as e:  # noqa: BLE001
        raise _write_http(e) from None
    await db.commit()
    return out


# ACT recording.share_link (the sheet's list of public links, v1 share links)
@router.get("/recordings/{cid}/{rid}/shares")
async def shares(cid: int, rid: int, p: Principal = Depends(scope("recordings")), db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import writes

    try:
        return await writes.list_shares(db, p, rid, cid)
    except Exception as e:  # noqa: BLE001
        raise _write_http(e) from None


# ACT recording.share_link (revoke a public link)
@router.delete("/recordings/{cid}/{rid}/shares/{share_id}")
async def revoke_share(cid: int, rid: int, share_id: int, p: Principal = Depends(scope("recordings")),
                       db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import writes

    try:
        conn, row = await store.find(db, p, rid, cid)
        pending = await _gate(db, p, "speakr.write", target=row.title, ctx=writes.recording_ctx(conn, row),
                              payload={"recording_id": rid, "connector_id": cid, "revoke_share": share_id},
                              preview={"title": f"Revoke a public link to “{row.title}”", "lines": [f"link {share_id}"]})
        if pending is not None:
            return pending
        return await writes.revoke_share(db, p, rid, cid, share_id)
    except Exception as e:  # noqa: BLE001
        raise _write_http(e) from None


# ACT event.last_recording @api
@router.get("/events/last-time")
async def last_time(uid: str, occurrence: str = "", excerpt: bool = True, p: Principal = Depends(scope("recordings")),
                    db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import prep

    try:
        return await prep.last_time(db, p, uid, occurrence or None, excerpt=excerpt)
    except (LookupError, sc.SpeakrError) as e:
        raise _http(e) from None


# ACT speaker.map @api
@router.get("/recordings/{cid}/{rid}/people")
async def recording_people(cid: int, rid: int, p: Principal = Depends(scope("recordings")),
                           db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import people

    try:
        return await people.speakers_with_people(db, p, rid, cid)
    except (store.RecordingNotFound, sc.SpeakrError) as e:
        raise _http(e) from None


class SpeakerPersonIn(BaseModel):
    key: str = Field(min_length=3, max_length=200)
    speaker_name: str = Field("", max_length=200)
    email: str = Field(min_length=3, max_length=320)
    name: str = Field("", max_length=300)
    # Speakr's saved speaker and the recording it was seen in: the mapping follows a rename,
    # and with write-back on the address is written to the speaker in Speakr.
    speaker_id: int | None = None
    recording_id: int | None = None


# ACT speaker.map
@router.post("/speakers/{cid}/person")
async def map_speaker(cid: int, body: SpeakerPersonIn, p: Principal = Depends(scope("recordings")),
                      db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import people

    pending = await _gate(db, p, "speakr.link", target=f"{body.speaker_name or body.key} → {body.email}",
                          ctx={"connectors": [cid]}, payload={"connector_id": cid, "speaker_key": body.key,
                                                              "speaker_name": body.speaker_name, "email": body.email,
                                                              "name": body.name, "speaker_id": body.speaker_id},
                          preview={"title": "Map a speaker to a person", "lines": [f"{body.speaker_name}: {body.email}"]})
    if pending is not None:
        return pending
    writes_back = (body.speaker_id is not None and body.recording_id is not None
                   and not body.key.startswith(("rec:", "label:")) and await people.write_back_on(db, p, cid))
    if writes_back and p.is_token:
        # Writing the address to the speaker in Speakr is a write to Speakr: a token needs
        # speakr.write for it (asks by default), not just speakr.link.
        pending = await _gate(db, p, "speakr.write", target=f"{body.speaker_name or body.key} → {body.email}",
                              ctx={"connectors": [cid]},
                              payload={"connector_id": cid, "speaker_key": body.key, "speaker_name": body.speaker_name,
                                       "email": body.email, "name": body.name, "speaker_id": body.speaker_id,
                                       "recording_id": body.recording_id},
                              preview={"title": "Write a speaker's address to Speakr",
                                       "lines": [f"{body.speaker_name or body.key}: {body.email}"]})
        if pending is not None:
            return pending
    try:
        m = await people.map_speaker(db, p, cid, key=body.key, speaker_name=body.speaker_name, email=body.email,
                                     name=body.name, source="user" if not p.is_token else "assistant",
                                     speaker_id=body.speaker_id)
    except (LookupError, ValueError) as e:
        raise _http(e) from None
    written = None
    if writes_back and not m.speaker_key.startswith(("rec:", "label:")):
        written = await people.write_speaker_email(db, p, cid, body.recording_id, body.speaker_id, m.email)
    await db.commit()
    return people.mapping_out(m) | {"written_to_speakr": written}


# ACT speaker.map
@router.delete("/speakers/{cid}/person")
async def unmap_speaker(cid: int, key: str, email: str, p: Principal = Depends(scope("recordings")),
                        db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import people

    pending = await _gate(db, p, "speakr.link", target=f"{key} → {email}", ctx={"connectors": [cid]},
                          payload={"connector_id": cid, "speaker_key": key, "email": email, "unmap": True},
                          preview={"title": "Remove a speaker mapping", "lines": [f"{key}: {email}"]})
    if pending is not None:
        return pending
    try:
        n = await people.unmap_speaker(db, p, cid, key=key, email=email)
    except LookupError as e:
        raise _http(e) from None
    await db.commit()
    return {"ok": True, "removed": n}


@router.get("/people/recordings")
async def person_recordings(email: str, limit: int = 5, p: Principal = Depends(scope("recordings")),
                            db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import people

    return await people.person_recordings(db, p, email, limit=max(1, min(limit, 20)))


# ---------------------------------------------------------------- participants by person (participants.py)


def _own_click(p: Principal) -> None:
    """Merges are made by the user's own clicks only, not by tokens."""
    if p.is_token:
        raise ApiError(403, "speakr.merge_in_app")


@router.get("/participants")
async def participants_overview(p: Principal = Depends(scope("recordings")), db: AsyncSession = Depends(get_db)):
    """The Participants list by person: each identity with its count and the names it merges,
    suggested merges, the aliases and the connectors with write-back on."""
    from mailr.integrations.speakr import participants

    return await participants.overview(db, p)


class MergeIn(BaseModel):
    into: str = Field(min_length=2, max_length=330)
    key: str = Field("", max_length=330)
    name: str = Field("", max_length=200)
    connector_id: int | None = None
    label: str = Field("", max_length=300)


# ACT participant.merge @api
@router.post("/participants/merge")
async def participants_merge(body: MergeIn, p: Principal = Depends(scope("recordings")),
                             db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import participants

    _own_click(p)
    if not body.key and not body.name:
        raise HTTPException(400, "key or name is needed")  # i18n: internal
    try:
        out = await participants.merge(db, p, into=body.into, key=body.key, name=body.name,
                                       connector_id=body.connector_id, label=body.label)
    except (LookupError, ValueError) as e:
        raise _http(e) from None
    await db.commit()
    return out


class SeparateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    connector_id: int | None = None


# ACT participant.merge
@router.post("/participants/separate")
async def participants_separate(body: SeparateIn, p: Principal = Depends(scope("recordings")),
                                db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import participants

    _own_click(p)
    try:
        out = await participants.separate(db, p, name=body.name, connector_id=body.connector_id)
    except LookupError as e:
        raise _http(e) from None
    await db.commit()
    return out


class DismissIn(BaseModel):
    connector_id: int
    name: str = Field(min_length=1, max_length=200)
    into: str = Field(min_length=2, max_length=330)


# ACT participant.merge
@router.post("/participants/dismiss")
async def participants_dismiss(body: DismissIn, p: Principal = Depends(scope("recordings")),
                               db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import participants

    _own_click(p)
    try:
        out = await participants.dismiss(db, p, connector_id=body.connector_id, name=body.name, into=body.into)
    except (LookupError, ValueError) as e:
        raise _http(e) from None
    await db.commit()
    return out


class RenameIn(BaseModel):
    connector_id: int
    name: str = Field(min_length=1, max_length=200)
    to: str = Field(min_length=1, max_length=200)


# ACT recording.edit (Also rename in Speakr, with write-back on)
@router.post("/participants/rename")
async def participants_rename(body: RenameIn, p: Principal = Depends(scope("recordings")),
                              db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import participants

    _own_click(p)
    try:
        out = await participants.rename_in_speakr(db, p, connector_id=body.connector_id, name=body.name, to=body.to)
    except Exception as e:  # noqa: BLE001  write-back off, a refusal or Speakr down; renamed ones stay
        await db.commit()
        raise _write_http(e) from None
    await db.commit()
    return out


# ---------------------------------------------------------------- recording from mailr, folders, tags


async def _upload_connector(db: AsyncSession, p: Principal, connector_id: int | None):
    conns = [c for c in await store.visible_connectors(db, p) if c.enabled]
    if connector_id is not None:
        conns = [c for c in conns if c.id == int(connector_id)]
    if not conns:
        raise ApiError(404, "speakr.not_connected")
    return conns[0]


# ACT recording.record @api (the upload form's folder list)
@router.get("/folders")
async def folders(connector_id: int | None = None, p: Principal = Depends(scope("recordings")),
                  db: AsyncSession = Depends(get_db)):
    """The Speakr folders of the account (personal and group), from Speakr, cached for a minute.
    An empty list also means folders are turned off in Speakr."""
    conn = await _upload_connector(db, p, connector_id)
    try:
        return {"connector_id": conn.id, "folders": await store.listing(db, conn, "folders")}
    except sc.SpeakrError as e:
        raise _http(e) from None


# ACT recording.record (the upload form's tag list)
@router.get("/tags")
async def tags(connector_id: int | None = None, p: Principal = Depends(scope("recordings")),
               db: AsyncSession = Depends(get_db)):
    """The Speakr tags of the account (personal and group), from Speakr, cached for a minute."""
    conn = await _upload_connector(db, p, connector_id)
    try:
        return {"connector_id": conn.id, "tags": await store.listing(db, conn, "tags")}
    except sc.SpeakrError as e:
        raise _http(e) from None


def _max_recording_bytes() -> int:
    import os

    return int(os.environ.get("MAILR_SPEAKR_MAX_UPLOAD_MB", "1024")) * 1024 * 1024


async def _gate_now(db: AsyncSession, p: Principal, action: str, ctx: dict[str, Any]) -> None:
    """For a request that cannot wait for an approval (the file would be gone): a token goes
    ahead only when the permission engine allows it outright. The user's own requests go straight
    through."""
    if not p.is_token:
        return
    from mailr.core import permissions

    needed = permissions.CLASSES[action][1]
    if needed not in (p.token.scopes or []):
        raise HTTPException(403, f"this token lacks the '{needed}' scope needed for {action}")  # i18n: internal
    effect, source, _ = await permissions.resolve(db, permissions.Actor.from_principal(p), action, ctx)
    if effect == "deny":
        raise HTTPException(403, f"{action} is not allowed for this token ({source})")  # i18n: internal
    if effect != "allow":
        raise HTTPException(403, f"{action} asks for approval for this token, and an upload cannot wait for one. "  # i18n: internal
                                 f"Add a rule that allows {action} for the token, or upload from the Mailr app.")


def _split(v: str | None) -> list[str]:
    """A comma-separated form field, or a JSON list."""
    import json

    v = (v or "").strip()
    if v.startswith("["):
        try:
            return [str(x).strip() for x in json.loads(v) if str(x).strip()]
        except ValueError:
            raise HTTPException(400, "not a JSON list") from None  # i18n: internal
    return [x.strip() for x in v.split(",") if x.strip()]


_RECORDING_FIELDS = ("title", "participants", "meeting_date", "notes", "folder", "tags", "event_uid", "occurrence",
                     "connector_id")


# ACT recording.record
@router.post("/recordings", openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {
    "schema": {"type": "object", "required": ["file"], "properties": {
        "file": {"type": "string", "format": "binary"}, "connector_id": {"type": "integer"},
        **{k: {"type": "string"} for k in _RECORDING_FIELDS if k != "connector_id"}}}}}}})
async def create_recording(request: Request, p: Principal = Depends(scope("recordings")),
                           db: AsyncSession = Depends(get_db)):
    """A new recording: the file goes to Speakr with its title, participants, meeting date, notes
    (Markdown), folder (id or name) and tags (ids or names, comma-separated or a JSON list); with
    event_uid (and occurrence) it is linked to that event. Answers {connector_id, recording_id,
    title, status, url, unsupported_fields, kept_in_mailr, link}. 413 when the file is over mailr's
    limit (MAILR_SPEAKR_MAX_UPLOAD_MB, 1024 by default) or Speakr's. The upload stops at the limit
    and before the disk falls under MAILR_DISK_FAIL_PERCENT free."""
    # Streamed to a temporary file (core/streamupload.py), removed afterwards.
    import tempfile
    from pathlib import Path

    from mailr.config import get_settings
    from mailr.core import streamupload
    from mailr.integrations.speakr import writes

    tmp = Path(tempfile.gettempdir())
    limit = _max_recording_bytes()
    disk = streamupload.disk_reserve(tmp, get_settings().disk_fail_percent)

    def open_file(_name: str, _ctype: str) -> Path:
        import secrets

        return tmp / f"recording-{secrets.token_hex(12)}"  # receive() creates it exclusively

    def check(size: int, chunk: int) -> None:
        if size > limit:
            raise ApiError(413, "speakr.recording_too_large", max=limit // 1024 ** 2)
        disk(size, chunk)

    got = await streamupload.receive(request, open_file, check)
    try:
        f = got.fields
        title, notes = f.get("title", ""), f.get("notes", "")
        event_uid, occurrence = f.get("event_uid", ""), f.get("occurrence", "")
        if len(title) > 500 or len(notes) > 20000 or len(event_uid) > 300 or len(occurrence) > 40:
            raise ApiError(400, "speakr.field_too_long")
        connector_id: int | None = None
        if f.get("connector_id", "").strip():
            try:
                connector_id = int(f["connector_id"])
            except ValueError:
                raise HTTPException(422, "connector_id must be a whole number") from None  # i18n: internal
        if got.size == 0:
            raise ApiError(400, "speakr.file_empty")
        try:
            connector_id = (await writes.connector_for_upload(db, p, connector_id)).id
        except LookupError as e:
            raise _write_http(e) from None
        await _gate_now(db, p, "speakr.upload", {"connectors": [connector_id]})
        with got.path.open("rb") as fh:
            try:
                out = await writes.create_recording(
                    db, p, filename=got.filename or "recording", content=fh, content_type=got.content_type or "",
                    title=title, participants=_split(f.get("participants", "")),
                    meeting_date=f.get("meeting_date") or None, notes=notes, folder=f.get("folder") or None,
                    tags=list[str | int](_split(f.get("tags", ""))), event_uid=event_uid or None, occurrence=occurrence,
                    cid=connector_id, source="user" if not p.is_token else "assistant")
            except Exception as e:  # noqa: BLE001
                raise _write_http(e) from None
    finally:
        got.path.unlink(missing_ok=True)
    await db.commit()
    return out


# ---------------------------------------------------------------- webhook setup (the connector card)


async def _own_connector(db: AsyncSession, p: Principal, cid: int):
    if p.is_token:
        raise ApiError(403, "speakr.account_user_only")
    conns = [c for c in await store.visible_connectors(db, p) if c.id == int(cid)]
    if not conns:
        raise ApiError(404, "speakr.account_not_found")
    return conns[0]


def _hook_http(e: Exception) -> HTTPException:
    from mailr.integrations.speakr import webhook

    if isinstance(e, sc.SpeakrError) and not isinstance(e, (sc.SpeakrNotFound, sc.SpeakrRateLimited)):
        hint = webhook.private_hint(str(e))
        if hint:
            return ApiError(502, "speakr.error_hint", error=str(e), hint=hint)
        return ApiError(502, "speakr.error", error=str(e))
    return _http(e)


# ACT speakr.connect @api (Get updates instantly)
@router.post("/connectors/{cid}/webhook")
async def webhook_on(cid: int, p: Principal = Depends(scope("recordings")), db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import webhook

    conn = await _own_connector(db, p, cid)
    try:
        out = await webhook.register(db, conn)
    except Exception as e:  # noqa: BLE001
        raise _hook_http(e) from None
    await db.commit()
    return out


# ACT speakr.connect @api (Send test)
@router.post("/connectors/{cid}/webhook/test")
async def webhook_test(cid: int, p: Principal = Depends(scope("recordings")), db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import webhook

    conn = await _own_connector(db, p, cid)
    try:
        return await webhook.send_test(db, conn)
    except Exception as e:  # noqa: BLE001
        raise _hook_http(e) from None


# ACT speakr.connect @api (Turn off)
@router.delete("/connectors/{cid}/webhook")
async def webhook_off(cid: int, p: Principal = Depends(scope("recordings")), db: AsyncSession = Depends(get_db)):
    from mailr.integrations.speakr import webhook

    conn = await _own_connector(db, p, cid)
    out = await webhook.unregister(db, conn)
    await db.commit()
    return out
