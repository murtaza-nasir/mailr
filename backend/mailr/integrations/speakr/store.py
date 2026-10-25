"""Speakr connectors, the metadata mirror, the content cache and who may see what.

A Speakr connector is a `connectors` row of kind "speakr":
- `config`: base_url, speakr_user_id, speakr_username, speakr_name, speakr_models_local, the card's
  switches (write_back, share_links, include_shared), and what Speakr said about itself:
  `speakr_capabilities` (GET /capabilities, read on connect and on each sync; an older Speakr has
  no such route and gets {"available": false}) and `speakr_token` (GET /tokens/current: the
  token's name, scopes and expiry, never the token). Nothing secret.
- `secrets_enc`: {"token": ...}, sealed with the user's data key (core/crypto).
- `data_class`: local_only by default. Content of a local-only connector reaches a model only
  when the model is local, and a token only when the token is local-only (Principal.can_see_connector).
- `state`: sync bookkeeping (failures, breaker, auth_failed, counts).

The mirror (`speakr_recordings`) holds the metadata and, for search, each recording's summary text
with a full-text index (sync.index_summaries). For reading, summaries,
transcripts, notes, speakers and extracted events are fetched from Speakr and kept in this
process's memory for ten minutes. Transcripts, notes, speakers and events are never written to the
database. The stored summary follows the connector's data class like any other content: it reaches
a model only under the local-only rules.

Every read goes through `find`: the recording must be in the caller's mirror (own recordings,
and recordings shared with the user when the card's "Include recordings shared with me" is on)
and its connector visible to the caller.

Speakr's own models (chat, semantic search) see a recording only when `speakr_model_ok`: the
connector is `normal`, or Speakr reports `models_local: true`. A local-only connector on a Speakr
with hosted models (the production default) never sends a question or a query to them.

Conditional reads (capabilities `etags`): a cached part younger than FRESH is used as it is; an
older one, up to the ten minutes of CACHE_TTL, is asked again with If-None-Match, and a 304 keeps
it. After ten minutes the part is dropped and read in full, as before.
"""

from __future__ import annotations

import re
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.crypto import connector_context, decrypt_for_user
from mailr.integrations.speakr import client as sc
from mailr.models import Connector, RecordingLink, SpeakrRecording, User

KIND = "speakr"
CACHE_TTL = 600.0
FRESH = 60.0
PARTS = ("summary", "transcript", "notes", "speakers", "events")

_CACHE: dict[tuple[int, int, str], tuple[float, Any]] = {}
_ETAGS: dict[tuple[int, int, str], tuple[str, float]] = {}  # (etag, time of the last 200 or 304)
_PATHS = {"summary": "/summary", "transcript": "/transcript", "notes": "/notes", "speakers": "/speakers",
          "events": "/events"}


class RecordingNotFound(LookupError):
    pass


def base_url(conn: Connector) -> str:
    return sc.normalize_base(str((conn.config or {}).get("base_url") or ""))


async def token_of(db: AsyncSession, conn: Connector) -> str:
    user = await db.get(User, conn.user_id)
    secrets = decrypt_for_user(user.id, user.data_key_wrapped, conn.secrets_enc,
                               context=connector_context(conn.id)) or {}
    return str(secrets.get("token") or "")


async def client_for(db: AsyncSession, conn: Connector) -> sc.SpeakrClient:
    from mailr.core.netguard import private_ok_for

    return sc.SpeakrClient(base_url(conn), await token_of(db, conn), bucket=f"connector:{conn.id}",
                           private_ok=private_ok_for(await db.get(User, conn.user_id)),
                           models_local=speakr_models_local(conn))


def caps(conn: Connector) -> dict[str, Any]:
    return (conn.config or {}).get("speakr_capabilities") or {}


def feature(conn: Connector, name: str, sub: str | None = None) -> bool:
    """A feature Speakr reported in GET /capabilities (stored on connect and on each sync)."""
    return sc.has_feature(caps(conn), name, sub)


def speakr_models_local(conn: Connector) -> bool:
    """Are Speakr's own models local? Only when Speakr states it (`models_local`) AND the
    connector's host resolved to private or loopback addresses when the capabilities were last
    read (`host_private`, refresh_capabilities). Each call to those models checks the addresses
    again when it connects (SpeakrClient, core/outbound.py)."""
    c = caps(conn)
    return c.get("models_local") is True and c.get("host_private") is True


def speakr_model_ok(conn: Connector, *, strict: bool = False) -> bool:
    """May a question or a query about this connector's recordings reach Speakr's own models?
    For a `normal` connector, unless the local-models-only rule holds for its owner (`strict`:
    the household's `local_all` or the person's switch, intel/locality.py); otherwise only when
    Speakr's models are local (speakr_models_local)."""
    return (conn.data_class == "normal" and not strict) or speakr_models_local(conn)


async def speakr_model_ok_for(db: AsyncSession, conn: Connector) -> bool:
    """speakr_model_ok with the owner's local-models-only rule."""
    from mailr.intel import locality

    return speakr_model_ok(conn, strict=await locality.strict(db, await db.get(User, conn.user_id)))


def token_scopes(conn: Connector) -> list[str] | None:
    """The token's scopes from GET /tokens/current ("full" for full access); None when unknown."""
    tok = (conn.config or {}).get("speakr_token")
    return list(tok.get("scopes") or []) if isinstance(tok, dict) else None


async def refresh_capabilities(conn: Connector, cl: sc.SpeakrClient) -> dict[str, Any]:
    """Read GET /capabilities and GET /tokens/current into the connector's config. A 401 raises;
    anything else that fails keeps what was stored."""
    from datetime import UTC, datetime

    try:
        got = await cl.capabilities()
    except sc.SpeakrAuthError:
        raise
    except sc.SpeakrError:
        return caps(conn)
    import asyncio

    from mailr.core import outbound

    cfg = dict(conn.config or {})
    got["checked_at"] = datetime.now(UTC).isoformat()
    # Speakr's `models_local` counts only for a host on the local network (speakr_models_local).
    got["host_private"] = await asyncio.to_thread(outbound.host_is_local_now, base_url(conn))
    cfg["speakr_capabilities"] = got
    if sc.has_feature(got, "token_scopes"):
        try:
            tok = await cl.token_info()
        except sc.SpeakrAuthError:
            raise
        except sc.SpeakrError:
            tok = cfg.get("speakr_token")
        if tok is not None:
            cfg["speakr_token"] = tok
    else:
        cfg.pop("speakr_token", None)
    conn.config = cfg
    return got


async def visible_connectors(db: AsyncSession, p: Any) -> list[Connector]:
    """The caller's Speakr connectors: a token sees those its account limits and data class allow."""
    rows = (await db.execute(select(Connector).where(Connector.user_id == p.user_id, Connector.kind == KIND)
                             .order_by(Connector.id))).scalars().all()
    return [c for c in rows if p.can_see_connector(c)]


async def has_visible(db: AsyncSession, p: Any) -> bool:
    return bool(await visible_connectors(db, p))


async def find(db: AsyncSession, p: Any, rid: int, cid: int | None = None,
               conns: list[Connector] | None = None) -> tuple[Connector, SpeakrRecording]:
    """The visible connector and mirror row of a recording (own recordings only), else RecordingNotFound."""
    conns = conns if conns is not None else await visible_connectors(db, p)
    if cid is not None:
        conns = [c for c in conns if c.id == int(cid)]
    if not conns:
        raise RecordingNotFound("recording not found")
    by_id = {c.id: c for c in conns}
    row = (await db.execute(select(SpeakrRecording).where(
        SpeakrRecording.user_id == p.user_id, SpeakrRecording.connector_id.in_(list(by_id)),
        SpeakrRecording.recording_id == int(rid), SpeakrRecording.deleted_at.is_(None))
        .order_by(SpeakrRecording.connector_id).limit(1))).scalar_one_or_none()
    if row is None:
        raise RecordingNotFound("recording not found")
    return by_id[row.connector_id], row


def deep_link(conn: Connector, rid: int, t: float | None = None) -> str:
    """Speakr opens the recording, and with ?t= seeks there and plays."""
    url = f"{base_url(conn)}/recordings/{int(rid)}"
    return url + (f"?t={int(t)}" if t is not None else "")


def cite(rid: int, t: float | None = None) -> str:
    return f"[[r:{int(rid)}@{int(t)}]]" if t is not None else f"[[r:{int(rid)}]]"


def recording_out(conn: Connector, row: SpeakrRecording) -> dict[str, Any]:
    return {
        "connector_id": conn.id, "recording_id": row.recording_id, "title": row.title or "",
        "meeting_date": row.meeting_date.isoformat() if row.meeting_date else None,
        "created_at": row.created_upstream.isoformat() if row.created_upstream else None,
        "duration_s": row.duration_s, "participants": list(row.participants or []),
        "tags": [t.get("name") for t in row.tags or [] if t.get("name")],
        "folder": (row.folder or {}).get("name") if row.folder else None,
        "status": row.status, "has_transcript": row.has_transcript, "has_summary": row.has_summary,
        "audio_available": row.audio_available, "is_archived": row.is_archived,
        "local_only": conn.data_class == "local_only", "url": deep_link(conn, row.recording_id),
        "cite": cite(row.recording_id),
        # The connector card's switches for edits and share links (writes.py; off by default).
        # A recording another user shared is edited only when its share allows editing, and
        # only its owner can make a public link.
        "write_back": bool((conn.config or {}).get("write_back")) and bool(row.can_edit),
        "share_links": bool((conn.config or {}).get("share_links")) and not row.is_shared,
        "is_shared": bool(row.is_shared), "owner_name": row.owner_name or None, "can_edit": bool(row.can_edit),
        # Share links in Speakr's v1 API can be listed and revoked from the sheet.
        "share_list": feature(conn, "share_links"),
    }


def link_out(link: RecordingLink) -> dict[str, Any]:
    return {"id": link.id, "connector_id": link.connector_id, "recording_id": link.recording_id, "kind": link.kind,
            "target": link.target, "occurrence": link.occurrence or None, "state": link.state,
            "score": round(link.score, 3) if link.score is not None else None, "source": link.source}


async def mark_auth_failed(db: AsyncSession, conn: Connector) -> None:
    """A 401: the connector shows the error and polling stops until the token is replaced."""
    st = dict(conn.state or {})
    st["auth_failed"] = True
    conn.state = st
    conn.status, conn.status_detail = "error", "Token expired or revoked. Paste a new Speakr token."
    await db.commit()


async def part(db: AsyncSession, conn: Connector, rid: int, name: str) -> Any:
    """summary | transcript | notes | speakers | events of a recording, from the ten-minute cache
    or from Speakr. Raises client errors (SpeakrNotFound, SpeakrAuthError, ...)."""
    if name not in PARTS:
        raise ValueError(name)
    key = (conn.id, int(rid), name)
    hit = _CACHE.get(key)
    now = time.monotonic()
    use_etag = feature(conn, "etags")
    if hit and now - hit[0] < CACHE_TTL:
        tag = _ETAGS.get(key)
        if not use_etag or tag is None or now - tag[1] < FRESH:
            return hit[1]
        # Older than FRESH: ask Speakr whether it changed (304 keeps the cached part).
        cl = await client_for(db, conn)
        try:
            same, body, etag = await cl.get_cached(f"/recordings/{int(rid)}{_PATHS[name]}", _params(name), tag[0])
        except sc.SpeakrAuthError:
            await mark_auth_failed(db, conn)
            raise
        if same:
            _ETAGS[key] = (tag[0], now)
            return hit[1]
        value = _parse(name, body)
        if name == "speakers":
            value = await _named_speakers(db, conn, int(rid), value)
        _put(key, value, now, etag)
        return value
    cl = await client_for(db, conn)
    try:
        if use_etag:
            _, body, etag = await cl.get_cached(f"/recordings/{int(rid)}{_PATHS[name]}", _params(name), None)
            value = _parse(name, body)
        else:
            value, etag = await getattr(cl, name)(int(rid)), None
    except sc.SpeakrAuthError:
        await mark_auth_failed(db, conn)
        raise
    if name == "speakers":
        value = await _named_speakers(db, conn, int(rid), value)
    _put(key, value, now, etag)
    if name == "transcript" and value and "label" in value[0]:
        CANONICAL.add(conn.id)
    return value


# Connectors whose Speakr answered a transcript in the canonical shape, which also takes
# `start` and `end`: a time range not in the cache is then read alone (service.get_transcript).
CANONICAL: set[int] = set()


def cached(conn: Connector, rid: int, name: str) -> bool:
    hit = _CACHE.get((conn.id, int(rid), name))
    return bool(hit) and time.monotonic() - hit[0] < CACHE_TTL


def _params(name: str) -> dict[str, Any] | None:
    return {"format": "json"} if name == "transcript" else None


def _parse(name: str, body: Any) -> Any:
    """A part's answer as the client's own readers shape it (client.SpeakrClient.<name>)."""
    d = body if isinstance(body, dict) else {}
    if name == "transcript":
        return sc.segments(body)
    if name in ("summary", "notes"):
        return str(d.get(name) or "")
    if name == "events":
        return [sc.event_out(e) for e in d.get("events") or [] if isinstance(e, dict)]
    out = []
    for s in d.get("speakers") or []:
        if isinstance(s, dict):
            row = {"label": str(s.get("label") or ""), "name": str(s.get("identified_name") or s.get("name") or ""),
                   "speaker_id": s.get("speaker_id"), "segments": s.get("segment_count")}
            if "email" in s:
                row["email"] = str(s.get("email") or "").strip().lower() or None
            out.append(row)
    return out


def _put(key: tuple[int, int, str], value: Any, now: float, etag: str | None) -> None:
    if len(_CACHE) > 2000:
        for k in [k for k, v in _CACHE.items() if now - v[0] >= CACHE_TTL] or list(_CACHE)[:500]:
            _CACHE.pop(k, None)
            _ETAGS.pop(k, None)
    _CACHE[key] = (now, value)
    if etag:
        _ETAGS[key] = (etag, now)
    else:
        _ETAGS.pop(key, None)


LABEL_RX = re.compile(r"^speaker[ _-]?\d+$", re.IGNORECASE)


async def _named_speakers(db: AsyncSession, conn: Connector, rid: int, speakers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Speakr's /speakers answers identified_name and speaker_id as null (its placeholder loop),
    while the transcript's segments carry the names the user gave. When no speaker has a name,
    the speakers are the transcript's: a named one is keyed by its name, an unnamed label
    (SPEAKER_00) only within this recording (people.speaker_key)."""
    if any(s.get("name") or s.get("speaker_id") not in (None, "") for s in speakers):
        return [s | {"recording_id": rid} for s in speakers]
    try:
        segs = await part(db, conn, rid, "transcript")
    except sc.SpeakrError:
        return [s | {"recording_id": rid} for s in speakers]
    counts: dict[str, int] = {}
    for seg in segs:
        who = (seg.get("speaker") or "").strip()
        if who:
            counts[who] = counts.get(who, 0) + 1
    if not counts:
        return [s | {"recording_id": rid} for s in speakers]
    return [{"label": who, "name": "" if LABEL_RX.match(who) else who, "speaker_id": None, "segments": n,
             "recording_id": rid} for who, n in counts.items()]


def forget(connector_id: int, rid: int | None = None) -> None:
    """Drop cached content of a connector (removed, token changed) or of one recording."""
    for k in [k for k in _CACHE if k[0] == connector_id and (rid is None or k[1] == rid)]:
        _CACHE.pop(k, None)
        _ETAGS.pop(k, None)
    if rid is None:
        for k in [k for k in _LISTS if k[0] == connector_id]:
            _LISTS.pop(k, None)


LIST_TTL = 60.0
_LISTS: dict[tuple[int, str], tuple[float, list[dict[str, Any]]]] = {}


async def listing(db: AsyncSession, conn: Connector, kind: str) -> list[dict[str, Any]]:
    """The account's Speakr folders or tags, cached for a minute (the upload form asks often)."""
    if kind not in ("folders", "tags"):
        raise ValueError(kind)
    key = (conn.id, kind)
    hit = _LISTS.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < LIST_TTL:
        return hit[1]
    cl = await client_for(db, conn)
    try:
        value = await getattr(cl, kind)()
    except sc.SpeakrAuthError:
        await mark_auth_failed(db, conn)
        raise
    _LISTS[key] = (now, value)
    return value


# ---------------------------------------------------------------- transcripts


def fmt_t(t: float | None) -> str:
    if t is None:
        return ""
    s = int(t)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def transcript_window(segs: list[dict[str, Any]], *, start: float | None = None, end: float | None = None,
                      speaker: str | None = None, q: str | None = None, max_chars: int = 6000) -> dict[str, Any]:
    """The segments in [start, end] (by speaker, containing every word of q), cut at max_chars.
    `next_start` is where to continue when the window was cut."""
    words = [w for w in (q or "").lower().split() if w]
    spk = (speaker or "").strip().lower()
    sel = []
    for s in segs:
        if s["t"] is not None:
            if start is not None and (s["end"] if s["end"] is not None else s["t"]) < start:
                continue
            if end is not None and s["t"] > end:
                continue
        if spk and spk not in s["speaker"].lower():
            continue
        if words and not all(w in s["text"].lower() for w in words):
            continue
        sel.append(s)
    out: list[dict[str, Any]] = []
    used, truncated, next_start = 0, False, None
    for s in sel:
        n = len(s["text"]) + len(s["speaker"]) + 16
        if out and used + n > max_chars:
            truncated, next_start = True, s["t"]
            break
        out.append(s)
        used += n
    return {"segments": out, "truncated": truncated, "next_start": next_start, "total_segments": len(segs),
            "timed": any(s["t"] is not None for s in segs)}


def verify_quote(segs: list[dict[str, Any]], t: float | None, quote: str) -> tuple[bool, dict[str, Any] | None]:
    """Is `quote` said within ±30 s of `t`? Returns (verified, the segment that holds it). A
    plain-text transcript has no times, so the quote is looked for anywhere in it."""
    from mailr.intel.highlight import locate

    timed = t is not None and any(s["t"] is not None for s in segs)
    if timed:
        near = [s for s in segs if s["t"] is not None and s["t"] <= t + 30
                and (s["end"] if s["end"] is not None else s["t"]) >= t - 30]
    else:
        near = list(segs)
    for s in near:
        if locate(s["text"], quote):
            return True, s
    if len(near) > 1 and locate(" ".join(s["text"] for s in near), quote):
        best = min(near, key=lambda s: abs((s["t"] or 0) - (t or 0)))
        return True, best
    return False, None
