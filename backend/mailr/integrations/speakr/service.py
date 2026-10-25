"""Reads and links of recordings, shared by the REST routes (routes.py) and the assistant's tools
(agent/speakr_tools.py), so both apply the same visibility.

Visibility: a caller sees the recordings of the Speakr connectors it may see (store.visible_connectors:
a token needs the `recordings` scope, its account limits must name the connector when they name
any, and only a local-only token sees a local-only connector). Only recordings in the mirror are
readable, which keeps mailr to the user's own recordings.

In `search`, the mirror's metadata (title, participants, tags, folder) is matched first, then the
stored summaries of all recordings (a full-text index). Transcripts: on a
Speakr with v1 search (capabilities `search.keyword`), GET /api/v1/search in keyword mode
finds words in the notes, summaries and transcripts of every recording, with the time and speaker
of each transcript hit; semantic mode is added only when the connector's recordings may reach
Speakr's own models (store.speakr_model_ok: a `normal` connector, or `models_local`), because the
query goes to Speakr's embedding model. On an older Speakr, the transcripts of a bounded number of
recent recordings are scanned (fetched through the ten-minute cache; transcripts are never
stored). `list_recordings` with q matches the stored summaries too and names the matching line
(`summary_match`).
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Text, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.integrations.speakr import client as sc
from mailr.integrations.speakr import store
from mailr.models import Connector, RecordingLink, SpeakrRecording

MAX_LIST = 50
SCAN_LIMIT = 8
SCAN_LIMIT_IDS = 20
INDEX_LIMIT = 30


def _date(v: Any, end: bool = False) -> datetime | None:
    if not v:
        return None
    s = str(v).strip()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if len(s) == 10 and end:
        d = d.replace(hour=23, minute=59, second=59)
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d


def _when_col():
    return func.coalesce(SpeakrRecording.meeting_date, SpeakrRecording.created_upstream)


def _ts_word(w: str) -> str:
    """One query word as a safe to_tsquery operand (letters and digits only)."""
    return re.sub(r"[^\w]+", "", w or "", flags=re.UNICODE).lower()[:60]


def summary_match(text: str, q: str, limit: int = 240) -> str | None:
    """The first line of a summary that holds a word of `q` (prefix match, as the index)."""
    words = [_ts_word(w) for w in (q or "").split()]
    words = [w for w in words if len(w) >= 2]
    if not words or not text:
        return None
    for line in text.split("\n"):
        low = re.findall(r"\w+", line.lower())
        if any(t.startswith(w) for w in words for t in low):
            return re.sub(r"^[#>*\-\s]+", "", line).strip()[:limit] or None
    return None


async def _rows(db: AsyncSession, conns: list[Connector], *, q: str = "", date_from: Any = None, date_to: Any = None,
                tag: str = "", participant: str = "", status: str = "", recording_ids: list[int] | None = None,
                limit: int = 20, offset: int = 0, cap: int = 200) -> list[SpeakrRecording]:
    if not conns:
        return []
    conds = [SpeakrRecording.connector_id.in_([c.id for c in conns]), SpeakrRecording.deleted_at.is_(None)]
    for w in (q or "").split():
        like = f"%{w}%"
        alts = [SpeakrRecording.title.ilike(like),
                func.array_to_string(SpeakrRecording.participants, " ").ilike(like),
                SpeakrRecording.tags.cast(Text).ilike(like),
                SpeakrRecording.folder.cast(Text).ilike(like)]
        word = _ts_word(w)
        if word:
            # The stored summary, by word prefix.
            alts.append(SpeakrRecording.tsv.op("@@")(func.to_tsquery("simple", f"{word}:*")))
        conds.append(or_(*alts))
    if tag:
        conds.append(SpeakrRecording.tags.cast(Text).ilike(f"%{tag}%"))
    if participant:
        conds.append(func.array_to_string(SpeakrRecording.participants, " ").ilike(f"%{participant}%"))
    if status:
        conds.append(SpeakrRecording.status == status.lower())
    a, b = _date(date_from), _date(date_to, end=True)
    if a:
        conds.append(_when_col() >= a)
    if b:
        conds.append(_when_col() <= b)
    if recording_ids:
        conds.append(SpeakrRecording.recording_id.in_([int(x) for x in recording_ids][:100]))
    return list((await db.execute(select(SpeakrRecording).where(and_(*conds))
                                  .order_by(_when_col().desc().nulls_last(), SpeakrRecording.id.desc())
                                  .offset(max(0, int(offset or 0)))
                                  .limit(max(1, min(int(limit or 20), cap))))).scalars())


_PARTICIPANT = re.compile(r'(?:^|\s)participant:(?:"([^"]+)"|(\S+))', re.IGNORECASE)


def split_participant(q: str) -> tuple[str, str]:
    """`participant:<name>` (or `participant:"First Last"`) out of a search: (the rest, the name)."""
    m = _PARTICIPANT.search(q or "")
    if not m:
        return q, ""
    return (q[:m.start()] + " " + q[m.end():]).strip(), (m.group(1) or m.group(2) or "").strip()


async def list_recordings(db: AsyncSession, p: Any, *, conns: list[Connector] | None = None, linked: str = "",
                          annotate: bool = False, **filters: Any) -> list[dict[str, Any]]:
    conns = conns if conns is not None else await store.visible_connectors(db, p)
    by_id = {c.id: c for c in conns}
    q, who = split_participant(filters.get("q") or "")
    filters["q"] = q
    who = filters.pop("participant", "") or who
    if who:
        # One person under every name they were recorded with (participants.py): the filter is
        # applied to every recording, then the page is cut.
        from mailr.integrations.speakr import participants

        limit, offset = int(filters.pop("limit", 20) or 20), int(filters.pop("offset", 0) or 0)
        rows = await _rows(db, conns, **filters, limit=participants.ALL_ROWS, cap=participants.ALL_ROWS)
        res, whos = await participants.resolve_rows(db, p, conns, rows)
        rows = participants.filter_rows(res, conns, rows, whos, who)[max(0, offset):max(0, offset) + max(1, limit)]
    else:
        rows = await _rows(db, conns, **filters)
    if linked:
        keys = {(lk.connector_id, lk.recording_id) for lk in (await db.execute(select(RecordingLink).where(
            RecordingLink.user_id == p.user_id, RecordingLink.target == str(linked),
            RecordingLink.state != "dismissed"))).scalars()}
        rows = [r for r in rows if (r.connector_id, r.recording_id) in keys]
    out = []
    for r in rows:
        item = store.recording_out(by_id[r.connector_id], r)
        hit = summary_match(r.summary_text, filters.get("q") or "")
        if hit:
            item["summary_match"] = hit
        out.append(item)
    if annotate and rows:
        await _annotate(db, p, rows, out)
    return out


async def _annotate(db: AsyncSession, p: Any, rows: list[SpeakrRecording], items: list[dict[str, Any]]) -> None:
    """For the Recordings app: each item's event link (`event_link`, a confirmed one first, else the
    best suggestion, with the event's title and start) and its participants as `people`
    ([{name, email, key, label, me, how}]): each participant string resolved to one person by
    participants.py (the per-recording override, the speaker mapping of the name, the user, the
    one correspondent in People with that full name, an alias, else the string itself); an address
    the user dismissed for this recording ("Not them in this recording") is skipped. A few queries
    for the whole page, plus one calendar lookup per linked recording."""

    keys = {(r.connector_id, r.recording_id) for r in rows}
    best: dict[tuple[int, int], RecordingLink] = {}
    for lk in (await db.execute(select(RecordingLink).where(
            RecordingLink.user_id == p.user_id, RecordingLink.kind == "event", RecordingLink.state != "dismissed",
            RecordingLink.connector_id.in_({k[0] for k in keys}),
            RecordingLink.recording_id.in_({k[1] for k in keys})))).scalars():
        k = (lk.connector_id, lk.recording_id)
        if k not in keys:
            continue
        cur = best.get(k)
        rank = (lk.state == "confirmed", lk.score or 0.0)
        if cur is None or rank > (cur.state == "confirmed", cur.score or 0.0):
            best[k] = lk
    details = dict(zip(best, await link_details(db, p, list(best.values())), strict=True))

    from mailr.integrations.speakr import participants

    conns = [c for c in await store.visible_connectors(db, p) if c.id in {k[0] for k in keys}]
    _, whos = await participants.resolve_rows(db, p, conns, rows, {k: lk.target for k, lk in best.items()})
    for r, item, ws in zip(rows, items, whos, strict=True):
        item["event_link"] = details.get((r.connector_id, r.recording_id))
        item["people"] = [w.out(x) for x, w in zip(r.participants or [], ws, strict=True)]


async def links_of(db: AsyncSession, conn: Connector, rid: int, *, include_dismissed: bool = False) -> list[RecordingLink]:
    q = select(RecordingLink).where(RecordingLink.connector_id == conn.id, RecordingLink.recording_id == int(rid))
    if not include_dismissed:
        q = q.where(RecordingLink.state != "dismissed")
    return list((await db.execute(q.order_by(RecordingLink.id))).scalars())


async def link_details(db: AsyncSession, p: Any, links: list[RecordingLink]) -> list[dict[str, Any]]:
    """Links with what they point at: the event's title and start, the conversation's subject,
    the task's title (when the caller can see them)."""
    from mailr.calendar import mirror as cal_mirror
    from mailr.models import Conversation

    out = []
    for lk in links:
        d = store.link_out(lk)
        try:
            if lk.kind in ("event", "task"):
                obj = await cal_mirror.find_by_uid(db, p.user_id, lk.target, lk.kind, p)
                d["title"] = (obj.summary or "(no title)") if obj else None
                if obj and lk.kind == "event":
                    d["start"] = (obj.start_at.isoformat() if obj.start_at else None)
            elif lk.kind == "conversation":
                conv = await db.get(Conversation, int(lk.target))
                d["title"] = conv.subject if conv and conv.user_id == p.user_id else None
                d["conversation_id"] = int(lk.target)
        except Exception:  # noqa: BLE001  a missing calendar or a bad id: the link shows without a title
            d.setdefault("title", None)
        out.append(d)
    return out


async def get_recording(db: AsyncSession, p: Any, rid: int, cid: int | None = None,
                        include: tuple[str, ...] = ("summary", "links")) -> tuple[Connector, dict[str, Any]]:
    """Metadata, the requested parts (summary, notes, events, speakers) and links. A part Speakr
    cannot give now is left out and named in `unavailable`; a 401 raises."""
    conn, row = await store.find(db, p, rid, cid)
    out = store.recording_out(conn, row)
    unavailable: dict[str, str] = {}
    for part in ("summary", "notes", "events", "speakers"):
        if part not in include:
            continue
        if part == "summary" and not row.has_summary:
            out["summary"] = ""
            continue
        try:
            out[part] = await store.part(db, conn, row.recording_id, part)
        except sc.SpeakrAuthError:
            raise
        except sc.SpeakrError as e:
            unavailable[part] = str(e)
    if "links" in include:
        out["links"] = await link_details(db, p, await links_of(db, conn, row.recording_id))
    if unavailable:
        out["unavailable"] = unavailable
    return conn, out


async def get_transcript(db: AsyncSession, p: Any, rid: int, cid: int | None = None, *, start: float | None = None,
                         end: float | None = None, speaker: str | None = None, q: str | None = None,
                         max_chars: int = 6000) -> tuple[Connector, dict[str, Any]]:
    conn, row = await store.find(db, p, rid, cid)
    if (start is not None or end is not None) and conn.id in store.CANONICAL \
            and not store.cached(conn, row.recording_id, "transcript"):
        # A Speakr with the canonical shape sends only the asked time range.
        try:
            segs = await (await store.client_for(db, conn)).transcript(row.recording_id, start=start, end=end)
        except sc.SpeakrAuthError:
            await store.mark_auth_failed(db, conn)
            raise
    else:
        segs = await store.part(db, conn, row.recording_id, "transcript")
    win = store.transcript_window(segs, start=start, end=end, speaker=speaker, q=q,
                                  max_chars=max(500, min(int(max_chars or 6000), 20000)))
    return conn, {"recording_id": row.recording_id, "connector_id": conn.id, "title": row.title, **win,
                  "url": store.deep_link(conn, row.recording_id)}


def _summary_hits(r: SpeakrRecording, conn: Connector, summary: str, words: list[str], need: int) -> list[dict[str, Any]]:
    out = []
    for para in [x for x in summary.split("\n") if x.strip()]:
        n = sum(1 for w in words if w in para.lower())
        if n >= need:
            out.append({"recording_id": r.recording_id, "connector_id": conn.id, "title": r.title,
                        "where": "summary", "t": None, "speaker": "", "text": para.strip()[:400],
                        "score": n / len(words), "cite": store.cite(r.recording_id)})
    return out


async def search(db: AsyncSession, p: Any, q: str, *, date_from: Any = None, date_to: Any = None,
                 recording_ids: list[int] | None = None, top_k: int = 10,
                 conns: list[Connector] | None = None, query_sensitive: bool = False) -> dict[str, Any]:
    """Metadata matches; summary lines from the stored summaries of every recording (the index);
    then a keyword scan of the transcripts of the most recent recordings (at most SCAN_LIMIT, or
    SCAN_LIMIT_IDS when ids are given), whose summaries are read from Speakr when the index has
    not got them yet."""
    conns = conns if conns is not None else await store.visible_connectors(db, p)
    by_id = {c.id: c for c in conns}
    words = [w for w in (q or "").lower().split() if len(w) >= 2]
    top_k = max(1, min(int(top_k or 10), 20))
    meta = [store.recording_out(by_id[r.connector_id], r)
            for r in await _rows(db, conns, q=q, date_from=date_from, date_to=date_to, recording_ids=recording_ids, limit=5)]
    if not words:
        return {"recordings": meta, "hits": [], "scanned": 0, "more": False}
    need = 1 if len(words) == 1 else math.ceil(len(words) * 0.6)
    hits: list[dict[str, Any]] = []
    terms = [_ts_word(w) for w in words]
    terms = [t for t in terms if t]
    if terms and conns:
        tsq = func.to_tsquery("simple", " | ".join(f"{t}:*" for t in terms))
        conds = [SpeakrRecording.connector_id.in_(list(by_id)), SpeakrRecording.deleted_at.is_(None),
                 SpeakrRecording.summary_at.is_not(None), SpeakrRecording.summary_text != "",
                 SpeakrRecording.tsv.op("@@")(tsq)]
        a, b = _date(date_from), _date(date_to, end=True)
        if a:
            conds.append(_when_col() >= a)
        if b:
            conds.append(_when_col() <= b)
        if recording_ids:
            conds.append(SpeakrRecording.recording_id.in_([int(x) for x in recording_ids][:100]))
        for r in (await db.execute(select(SpeakrRecording).where(and_(*conds))
                                   .order_by(func.ts_rank(SpeakrRecording.tsv, tsq).desc(), _when_col().desc().nulls_last())
                                   .limit(INDEX_LIMIT))).scalars():
            hits += _summary_hits(r, by_id[r.connector_id], r.summary_text, words, need)
    skipped = 0
    searched: list[Connector] = []
    for conn in conns:
        if not store.feature(conn, "search", "keyword"):
            continue
        try:
            got = await _speakr_search(db, conn, q, date_from=date_from, date_to=date_to, recording_ids=recording_ids,
                                       top_k=top_k, query_sensitive=query_sensitive)
        except sc.SpeakrAuthError:
            raise
        except sc.SpeakrError:
            continue  # this connector falls back to the scan below
        searched.append(conn)
        hits += got
    scan = [c for c in conns if c not in searched]
    limit = SCAN_LIMIT_IDS if recording_ids else SCAN_LIMIT
    rows = [r for r in await _rows(db, scan, date_from=date_from, date_to=date_to, recording_ids=recording_ids,
                                   limit=limit + 1) if r.has_transcript or r.has_summary] if scan else []
    more = len(rows) > limit
    for r in rows[:limit]:
        conn = by_id[r.connector_id]
        try:
            if r.has_summary and r.summary_at is None:
                # Not in the index yet (the sync reads it soon): read it from Speakr now.
                hits += _summary_hits(r, conn, await store.part(db, conn, r.recording_id, "summary"), words, need)
            if r.has_transcript:
                for s in await store.part(db, conn, r.recording_id, "transcript"):
                    n = sum(1 for w in words if w in s["text"].lower())
                    if n >= need:
                        hits.append({"recording_id": r.recording_id, "connector_id": conn.id, "title": r.title,
                                     "where": "transcript", "t": s["t"], "speaker": s["speaker"],
                                     "text": s["text"][:400], "score": n / len(words),
                                     "cite": store.cite(r.recording_id, s["t"])})
        except sc.SpeakrAuthError:
            raise
        except sc.SpeakrError:
            skipped += 1
    hits = _dedupe(hits)
    hits.sort(key=lambda h: -h["score"])
    out = {"recordings": meta, "hits": hits[:top_k], "scanned": min(len(rows), limit), "more": more,
           # True when every connector's transcripts were searched in full by Speakr.
           "full_text": bool(conns) and not scan}
    if skipped:
        out["unavailable"] = skipped
    return out


def _dedupe(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One hit per recording, place and text (the index and Speakr can both find a summary line)."""
    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    for h in hits:
        k = (h["connector_id"], h["recording_id"], h["where"], h["t"], re.sub(r"\W+", " ", h["text"].lower()).strip()[:200])
        if k not in best or h["score"] > best[k]["score"]:
            best[k] = h
    return list(best.values())


async def _speakr_search(db: AsyncSession, conn: Connector, q: str, *, date_from: Any, date_to: Any,
                         recording_ids: list[int] | None, top_k: int, query_sensitive: bool = False
                         ) -> list[dict[str, Any]]:
    """Speakr's v1 search on one connector: keyword hits always, semantic ones only when
    store.speakr_model_ok, and for a sensitive query (written in a chat that read local-only
    content) only when Speakr's models are local. Hits name recordings in the mirror only (the
    caller's visible set)."""
    from mailr.integrations.speakr.sync import _scope

    cl = await store.client_for(db, conn)
    a, b = _date(date_from), _date(date_to, end=True)
    kw: dict[str, Any] = {"limit": min(50, top_k * 2), "recording_ids": recording_ids, "scope": _scope(conn),
                          "date_from": a.isoformat() if a else None, "date_to": b.isoformat() if b else None}
    results = list((await cl.search(q, mode="keyword", **kw)).get("results") or [])
    semantic_ok = await store.speakr_model_ok_for(db, conn) and (not query_sensitive or store.speakr_models_local(conn))
    if semantic_ok and store.feature(conn, "search", "semantic"):
        try:
            results += [r | {"semantic": True} for r in (await cl.search(q, mode="semantic", **kw)).get("results") or []]
        except sc.SpeakrAuthError:
            raise
        except sc.SpeakrError:
            pass
    rids = {int(r["recording_id"]) for r in results if isinstance(r, dict) and isinstance(r.get("recording_id"), int)}
    if not rids:
        return []
    rows = {r.recording_id: r for r in (await db.execute(select(SpeakrRecording).where(
        SpeakrRecording.connector_id == conn.id, SpeakrRecording.recording_id.in_(list(rids)),
        SpeakrRecording.deleted_at.is_(None)))).scalars()}
    out = []
    for r in results:
        row = rows.get(r.get("recording_id")) if isinstance(r, dict) else None
        text = str(r.get("text") or "").strip() if row is not None else ""
        if not text:
            continue
        t = sc.seconds(r.get("start_time"))
        where = str(r.get("field") or "transcript")
        score = r.get("score") if isinstance(r.get("score"), (int, float)) else 0.5
        out.append({"recording_id": row.recording_id, "connector_id": conn.id, "title": row.title, "where": where,
                    "t": t, "speaker": str(r.get("speaker") or ""), "text": text[:400], "score": float(score),
                    "cite": store.cite(row.recording_id, t if where == "transcript" else None),
                    **({"semantic": True} if r.get("semantic") else {})})
    return out


# ---------------------------------------------------------------- links


async def event_recordings(db: AsyncSession, p: Any, uid: str, occurrence: str | None) -> list[dict[str, Any]]:
    """Recordings linked (or suggested) for an event occurrence; a link without an occurrence
    covers every occurrence."""
    conns = await store.visible_connectors(db, p)
    if not conns:
        return []
    by_id = {c.id: c for c in conns}
    links = (await db.execute(select(RecordingLink).where(
        RecordingLink.user_id == p.user_id, RecordingLink.kind == "event", RecordingLink.target == uid,
        RecordingLink.state != "dismissed", RecordingLink.connector_id.in_(list(by_id))))).scalars().all()
    out = []
    for lk in links:
        if occurrence and lk.occurrence and lk.occurrence != occurrence:
            continue
        try:
            conn, row = await store.find(db, p, lk.recording_id, lk.connector_id, conns)
        except store.RecordingNotFound:
            continue
        item = store.recording_out(conn, row) | {"link": store.link_out(lk)}
        if row.has_summary:
            try:
                item["summary_excerpt"] = (await store.part(db, conn, row.recording_id, "summary"))[:400]
            except sc.SpeakrError:
                item["summary_excerpt"] = None
        out.append(item)
    out.sort(key=lambda x: (x["link"]["state"] != "confirmed", -(x["link"]["score"] or 0)))
    return out


async def recording_candidates(db: AsyncSession, p: Any, conn: Connector, rid: int) -> list[dict[str, Any]]:
    """Events the user may link this recording to (best first, without the ones linked or dismissed)."""
    from mailr.integrations.speakr import matching

    _, row = await store.find(db, p, rid, conn.id)
    taken = {(lk.target, lk.occurrence) for lk in await links_of(db, conn, rid, include_dismissed=True)
             if lk.kind == "event"}
    return [c for c in await matching.candidates(db, p.user_id, row) if (c["uid"], c["occurrence"]) not in taken][:5]


async def set_link(db: AsyncSession, p: Any, *, rid: int, cid: int | None, kind: str, target: str,
                   occurrence: str = "", source: str = "user") -> RecordingLink:
    """Create a confirmed link, or confirm a suggested or dismissed one (the target is checked)."""
    from mailr.calendar import mirror as cal_mirror
    from mailr.models import Conversation

    conn, row = await store.find(db, p, rid, cid)
    if kind not in ("event", "conversation", "task"):
        raise ValueError("kind must be event, conversation or task")
    target = str(target or "").strip()
    if kind == "conversation":
        conv = await db.get(Conversation, int(target)) if target.isdigit() else None
        if conv is None or conv.user_id != p.user_id:
            raise LookupError("conversation not found")
    else:
        if await cal_mirror.find_by_uid(db, p.user_id, target, kind, p) is None:
            raise LookupError(f"{kind} not found")
    occurrence = (occurrence or "")[:40] if kind == "event" else ""
    lk = (await db.execute(select(RecordingLink).where(
        RecordingLink.connector_id == conn.id, RecordingLink.recording_id == row.recording_id,
        RecordingLink.kind == kind, RecordingLink.target == target[:300],
        RecordingLink.occurrence == occurrence))).scalar_one_or_none()
    if lk is None:
        lk = RecordingLink(user_id=p.user_id, connector_id=conn.id, recording_id=row.recording_id, kind=kind,
                           target=target[:300], occurrence=occurrence, state="confirmed", source=source)
        db.add(lk)
    else:
        lk.state, lk.updated_at = "confirmed", datetime.now(UTC)
        if lk.source in ("auto", "mail"):
            # A suggestion the user (or the assistant) confirms becomes theirs.
            lk.source = source
    if kind == "event":
        # One recording, one meeting: other suggestions for this recording go.
        for other in await links_of(db, conn, row.recording_id):
            if other is not lk and other.kind == "event" and other.state == "suggested":
                other.state = "dismissed"
    await db.flush()
    if kind in ("event", "conversation"):
        # With write-back on, the link is written to Speakr as a reference (writes.sync_refs).
        from mailr.integrations.speakr.writes import sync_refs

        await sync_refs(db, conn, row.recording_id)
    return lk


async def dismiss_link(db: AsyncSession, p: Any, link_id: int) -> RecordingLink:
    """Remove a link or dismiss a suggestion; it stays as dismissed so the matcher never brings it back."""
    lk = await db.get(RecordingLink, int(link_id))
    if lk is None or lk.user_id != p.user_id:
        raise LookupError("link not found")
    conn = await db.get(Connector, lk.connector_id)
    if conn is None or not p.can_see_connector(conn):
        raise LookupError("link not found")
    was = lk.state
    lk.state, lk.updated_at = "dismissed", datetime.now(UTC)
    await db.flush()
    if was == "confirmed" and lk.kind in ("event", "conversation"):
        from mailr.integrations.speakr.writes import sync_refs

        await sync_refs(db, conn, lk.recording_id)
    return lk


# The executor of an approved speakr.link request (a token's POST /speakr/links or the assistant's
# link_recording that waited for approval). It acts as the requester, with its connector limits.
from mailr.core import permissions  # noqa: E402


@permissions.executor("speakr.link")
async def _x_speakr_link(db: AsyncSession, user, payload: dict[str, Any]) -> dict[str, Any]:
    p = await permissions.requester(db, user, payload)
    if payload.get("speaker_key"):
        # A speaker ↔ person mapping (people.py) is a link too.
        from mailr.integrations.speakr import people

        if payload.get("unmap"):
            n = await people.unmap_speaker(db, p, int(payload["connector_id"]), key=str(payload["speaker_key"]),
                                           email=str(payload["email"]))
            await db.commit()
            return {"removed": n}
        sid = payload.get("speaker_id")
        m = await people.map_speaker(db, p, int(payload["connector_id"]), key=str(payload["speaker_key"]),
                                     speaker_name=str(payload.get("speaker_name") or ""), email=str(payload["email"]),
                                     name=str(payload.get("name") or ""), source="assistant",
                                     speaker_id=int(sid) if isinstance(sid, int) else None)
        await db.commit()
        return people.mapping_out(m)
    lk = await set_link(db, p, rid=int(payload["recording_id"]), cid=payload.get("connector_id"),
                        kind=str(payload["kind"]), target=str(payload["target"]),
                        occurrence=str(payload.get("occurrence") or ""), source="assistant")
    await db.commit()
    return store.link_out(lk)


async def resolve_attendees(db: AsyncSession, user_id: int, cid: int, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extracted events with each attendee's address filled where mailr knows it: a speaker of
    that name the user mapped to a person on this connector, else the one person in People with
    that full name. Names stay without an address otherwise; nothing is guessed from a first name."""
    from mailr.models import Person, SpeakrSpeakerPerson

    names = {re.sub(r"\s+", " ", a["name"].strip().lower()) for e in events for a in e.get("attendees") or []
             if a.get("name") and not a.get("email")}
    if not names:
        return events
    found: dict[str, str] = {}
    for name, email in (await db.execute(select(SpeakrSpeakerPerson.speaker_name, SpeakrSpeakerPerson.email).where(
            SpeakrSpeakerPerson.user_id == user_id, SpeakrSpeakerPerson.connector_id == cid,
            SpeakrSpeakerPerson.state == "confirmed", SpeakrSpeakerPerson.speaker_name != ""))).all():
        k = re.sub(r"\s+", " ", (name or "").strip().lower())
        if k in names:
            found.setdefault(k, email)
    full = [n for n in names - set(found) if len(n.split()) >= 2]
    if full:
        by: dict[str, set[str]] = {}
        for name, email in (await db.execute(select(Person.name, Person.email).where(
                Person.user_id == user_id, Person.blocked.is_(False), func.lower(Person.name).in_(full)))).all():
            by.setdefault(re.sub(r"\s+", " ", name.strip().lower()), set()).add(email.lower())
        found.update({k: next(iter(v)) for k, v in by.items() if len(v) == 1})
    out = []
    for e in events:
        att = [a | {"email": found.get(re.sub(r"\s+", " ", (a.get("name") or "").strip().lower()))}
               if a.get("name") and not a.get("email") else a for a in e.get("attendees") or []]
        out.append(e | {"attendees": att})
    return out
