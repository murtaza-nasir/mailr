"""Recordings matched to calendar events (recording.link).

For a completed recording, candidates are the timed, uncancelled event occurrences in the user's
calendars that overlap [meeting_date, meeting_date + duration], widened by ten minutes on both
sides. All times are compared as aware UTC instants, so a meeting on a DST change day matches
like any other. A recurring event is matched per occurrence; the link names that occurrence.

The score adds the time overlap (0.6), title similarity (0.25) and the overlap between the
event's guests and the recording's participants (0.15). Participants are compared with the guests
twice, and the better of the two counts: by the words of their names, and by email address. A
participant has an address when the user confirmed a speaker of that name as a person
(`speakr_speaker_people`, people.py), or when exactly one correspondent in People has that full
name (two words or more). A link is suggested only for a clear
winner: a score of at least 0.35 and 0.15 above the next candidate. Two equally good events get no
link; they are listed in the recording sheet for the user to pick.

The user confirms or dismisses a suggestion. A confirmed link is never replaced, and a
dismissed suggestion never comes back.

References first: a recording that carries one of mailr's own event references in Speakr
(`mailr_refs`, written when a link was confirmed with write-back on, or by a new recording made in
mailr) gets that link, confirmed, before any scoring, unless the user dismissed it. A reference to
an event the user's calendars no longer have is ignored.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.db import session_scope
from mailr.integrations.speakr import store
from mailr.models import Connector, Person, RecordingLink, SpeakrRecording, SpeakrSpeakerPerson

WIDEN = timedelta(minutes=10)
DEFAULT_DURATION = 1800.0
THRESHOLD = 0.35
MARGIN = 0.15
LOOKBACK = timedelta(days=120)
_STOP = {"the", "and", "for", "with", "meeting", "call", "sync", "chat", "re", "fw", "fwd", "about", "from"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 3 and w not in _STOP}


def _people(values: list[str]) -> set[str]:
    out: set[str] = set()
    for v in values:
        v = (v or "").lower()
        local = v.split("@", 1)[0] if "@" in v else v
        out |= {w for w in re.split(r"[^a-z0-9]+", local) if len(w) >= 3}
    return out


def _when(v: Any) -> datetime | None:
    if not isinstance(v, str) or "T" not in v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def interval(row: SpeakrRecording) -> tuple[datetime, datetime] | None:
    start = row.meeting_date or row.created_upstream
    if start is None:
        return None
    return start, start + timedelta(seconds=row.duration_s or DEFAULT_DURATION)


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


async def participant_emails(db: AsyncSession, user_id: int, row: SpeakrRecording) -> set[str]:
    """Addresses of the recording's participants: confirmed speaker mappings of this connector
    first, then a correspondent whose full name is the participant's (only when one person has it)."""
    names = {_norm(x) for x in row.participants or [] if _norm(x)}
    if not names:
        return set()
    out: set[str] = set()
    maps = (await db.execute(select(SpeakrSpeakerPerson.speaker_name, SpeakrSpeakerPerson.email).where(
        SpeakrSpeakerPerson.user_id == user_id, SpeakrSpeakerPerson.connector_id == row.connector_id,
        SpeakrSpeakerPerson.state == "confirmed"))).all()
    mapped = set()
    for name, email in maps:
        if _norm(name) in names:
            out.add(email.lower())
            mapped.add(_norm(name))
    full = [n for n in names - mapped if len(n.split()) >= 2]
    if full:
        rows = (await db.execute(select(Person.name, Person.email).where(
            Person.user_id == user_id, Person.blocked.is_(False),
            func.lower(Person.name).in_(full)))).all()
        by: dict[str, set[str]] = {}
        for name, email in rows:
            by.setdefault(_norm(name), set()).add(email.lower())
        out |= {next(iter(es)) for es in by.values() if len(es) == 1}
    return out


async def candidates(db: AsyncSession, user_id: int, row: SpeakrRecording) -> list[dict[str, Any]]:
    """Scored event occurrences around the recording, best first."""
    from mailr.calendar import mirror as cal_mirror

    span = interval(row)
    if span is None:
        return []
    rs, re_ = span
    try:
        evs = await cal_mirror.agenda(db, user_id, rs - WIDEN, re_ + WIDEN)
    except Exception:  # noqa: BLE001  no calendar
        return []
    dur = (re_ - rs).total_seconds()
    title_words = _words(row.title)
    people = _people(list(row.participants or []))
    emails = await participant_emails(db, user_id, row)
    out = []
    for e in evs:
        if e.get("all_day") or (e.get("status") or "").upper() == "CANCELLED":
            continue
        es, ee = _when(e.get("start")), _when(e.get("end"))
        if es is None or ee is None:
            continue
        ov = (min(ee, re_ + WIDEN) - max(es, rs - WIDEN)).total_seconds()
        if ov <= 0:
            continue
        time_score = min(1.0, ov / max(60.0, min(dur, max(60.0, (ee - es).total_seconds()))))
        ew = _words(e.get("summary") or "")
        title = len(ew & title_words) / len(ew | title_words) if ew and title_words else 0.0
        guests = [a.get("email") or "" for a in e.get("attendee_list") or []] + \
                 [a.get("name") or "" for a in e.get("attendee_list") or []] + [e.get("organizer") or "", e.get("organizer_name") or ""]
        gw = _people(guests)
        ppl = len(gw & people) / max(1, min(len(gw), len(people))) if gw and people else 0.0
        ge = {x.strip().lower().removeprefix("mailto:") for x in
              [a.get("email") or "" for a in e.get("attendee_list") or []] + [e.get("organizer") or ""] if "@" in x}
        if ge and emails:
            ppl = max(ppl, len(ge & emails) / max(1, min(len(ge), len(row.participants or []))))
        out.append({"uid": e["uid"], "occurrence": e.get("occurrence") or "", "summary": e.get("summary") or "",
                    "start": e.get("start"), "end": e.get("end"), "calendar": e.get("calendar"),
                    "score": round(0.6 * time_score + 0.25 * title + 0.15 * ppl, 4)})
    out.sort(key=lambda c: -c["score"])
    return out


def pick(cands: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The clear winner, or None (nothing good enough, or two too close to call)."""
    if not cands or cands[0]["score"] < THRESHOLD:
        return None
    if len(cands) > 1 and cands[0]["score"] - cands[1]["score"] < MARGIN:
        return None
    return cands[0]


async def match_recording(db: AsyncSession, conn: Connector, row: SpeakrRecording) -> RecordingLink | None:
    """Suggest (or keep) the event link of one recording. Returns the suggested link, if any."""
    if row.status != "completed" or row.deleted_at is not None:
        return None
    links = (await db.execute(select(RecordingLink).where(
        RecordingLink.connector_id == conn.id, RecordingLink.recording_id == row.recording_id,
        RecordingLink.kind == "event"))).scalars().all()
    if any(lk.state == "confirmed" for lk in links):
        return None
    dismissed = {(lk.target, lk.occurrence) for lk in links if lk.state == "dismissed"}
    by_ref = await _from_ref(db, conn, row, links, dismissed)
    if by_ref is not None:
        return by_ref
    cands = [c for c in await candidates(db, conn.user_id, row) if (c["uid"], c["occurrence"]) not in dismissed]
    best = pick(cands)
    keep = None
    for lk in links:
        if lk.state != "suggested":
            continue
        if best and (lk.target, lk.occurrence) == (best["uid"], best["occurrence"]):
            lk.score, lk.updated_at, keep = best["score"], datetime.now(UTC), lk
        elif lk.source == "auto":
            await db.delete(lk)
    if best and keep is None:
        keep = RecordingLink(user_id=conn.user_id, connector_id=conn.id, recording_id=row.recording_id, kind="event",
                             target=best["uid"][:300], occurrence=best["occurrence"][:40], state="suggested",
                             score=best["score"], source="auto")
        db.add(keep)
    return keep


def parse_event_ref(ref: str) -> tuple[str, str]:
    """An event reference is "<uid>" or "<uid>@<occurrence>" (writes.event_ref); a uid may hold
    "@" itself, so the part after the last "@" is an occurrence only when it is a date."""
    head, _, tail = (ref or "").rpartition("@")
    if head and re.match(r"^\d{4}-\d{2}-\d{2}", tail):
        return head, tail[:40]
    return ref, ""


async def _from_ref(db: AsyncSession, conn: Connector, row: SpeakrRecording, links: list[RecordingLink],
                    dismissed: set[tuple[str, str]]) -> RecordingLink | None:
    """mailr's own event reference on the recording in Speakr, as a confirmed link."""
    from mailr.calendar import mirror as cal_mirror

    for r in row.mailr_refs or []:
        if r.get("kind") != "event":
            continue
        uid, occ = parse_event_ref(str(r.get("ref") or ""))
        if not uid or (uid, occ) in dismissed:
            continue
        if await cal_mirror.find_by_uid(db, conn.user_id, uid, "event") is None:
            continue
        lk = next((x for x in links if (x.target, x.occurrence) == (uid[:300], occ)), None)
        if lk is None:
            lk = RecordingLink(user_id=conn.user_id, connector_id=conn.id, recording_id=row.recording_id,
                               kind="event", target=uid[:300], occurrence=occ, source="speakr")
            db.add(lk)
        lk.state, lk.score, lk.updated_at = "confirmed", None, datetime.now(UTC)
        for other in links:
            if other is not lk and other.state == "suggested":
                other.state = "dismissed"
        return lk
    return None


# ACT recording.link @api (the matcher's suggestions)
async def match_connector(connector_id: int) -> int:
    """Suggest event links for the connector's recent completed recordings. Returns how many
    recordings have a suggestion after the pass."""
    n = 0
    async with session_scope() as db:
        conn = await db.get(Connector, connector_id)
        if conn is None or conn.kind != store.KIND:
            return 0
        since = datetime.now(UTC) - LOOKBACK
        rows = (await db.execute(select(SpeakrRecording).where(
            SpeakrRecording.connector_id == conn.id, SpeakrRecording.deleted_at.is_(None),
            SpeakrRecording.status == "completed",
            func.coalesce(SpeakrRecording.meeting_date, SpeakrRecording.created_upstream) >= since))).scalars().all()
        for row in rows:
            if await match_recording(db, conn, row) is not None:
                n += 1
    return n
