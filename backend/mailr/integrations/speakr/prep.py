"""Meeting prep: "Last time" on an upcoming event and its reminder
(event.last_recording).

For an event occurrence that has not started yet (or started in the last half hour), the latest
earlier recording of the same meeting, found in this order:
1. a recording linked to another occurrence of the same series (the same event uid);
2. a recording linked to another event with the same title and at least one shared guest;
3. a recording in the mirror whose title has the event's title words and whose participants
   name one of the event's guests.
Only recordings that started before the occurrence count. Dismissed links never count.

The one-line summary is fetched from Speakr when it is asked for (`excerpt`), through the
ten-minute cache, and never stored.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.integrations.speakr import client as sc
from mailr.integrations.speakr import store
from mailr.integrations.speakr.matching import _people, _words, interval
from mailr.models import RecordingLink, SpeakrRecording

GRACE = timedelta(minutes=30)
TITLE_MATCH = 0.6
MAX_EVENTS = 100


def _occ_start(occurrence: str | None) -> datetime | None:
    if not occurrence:
        return None
    try:
        d = datetime.fromisoformat(occurrence.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _same_title(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= TITLE_MATCH


def _emails(ev: dict[str, Any], mine: set[str]) -> set[str]:
    out = {str(a.get("email") or "").lower().removeprefix("mailto:") for a in ev.get("attendee_list") or []}
    out.add(str(ev.get("organizer") or "").lower().removeprefix("mailto:"))
    return {e for e in out if "@" in e and e not in mine}


def _names(ev: dict[str, Any], mine: set[str]) -> set[str]:
    people = [a for a in ev.get("attendee_list") or [] if str(a.get("email") or "").lower() not in mine]
    return _people([str(a.get("name") or "") for a in people] + [a.get("email") or "" for a in people])


# ACT event.last_recording @api
async def last_time(db: AsyncSession, p: Any, uid: str, occurrence: str | None = None, *, excerpt: bool = True,
                    now: datetime | None = None) -> dict[str, Any]:
    from mailr.calendar import mirror as cal_mirror

    conns = await store.visible_connectors(db, p)
    if not conns:
        return {"connected": False, "item": None}
    obj = await cal_mirror.find_by_uid(db, p.user_id, uid, "event", p)
    if obj is None:
        raise LookupError("event not found")
    ev = await cal_mirror.object_out(db, obj)
    now = now or datetime.now(UTC)
    start = _occ_start(occurrence) or obj.start_at
    if start is None or start < now - GRACE:
        return {"connected": True, "item": None, "upcoming": False}
    mine = await cal_mirror.my_addresses(db, p.user_id)
    by_id = {c.id: c for c in conns}

    async def rows_of(links: list[RecordingLink]) -> list[SpeakrRecording]:
        keys = {(lk.connector_id, lk.recording_id) for lk in links}
        if not keys:
            return []
        rows = (await db.execute(select(SpeakrRecording).where(
            SpeakrRecording.connector_id.in_(list(by_id)), SpeakrRecording.deleted_at.is_(None),
            SpeakrRecording.recording_id.in_([k[1] for k in keys])))).scalars().all()
        return [r for r in rows if (r.connector_id, r.recording_id) in keys]

    def before(rows: list[SpeakrRecording]) -> list[SpeakrRecording]:
        out = [r for r in rows if (span := interval(r)) is not None and span[0] < start - timedelta(minutes=5)]
        return sorted(out, key=lambda r: interval(r)[0], reverse=True)

    links = (await db.execute(select(RecordingLink).where(
        RecordingLink.user_id == p.user_id, RecordingLink.kind == "event", RecordingLink.state != "dismissed",
        RecordingLink.connector_id.in_(list(by_id))).order_by(RecordingLink.id.desc()).limit(2000))).scalars().all()

    why, found = "", None
    series = before(await rows_of([lk for lk in links if lk.target == uid]))
    if series:
        why, found = "series", series[0]
    if found is None:
        emails = _emails(ev, mine)
        others: list[RecordingLink] = []
        seen: dict[str, bool] = {}
        for lk in links:
            if lk.target == uid or len(seen) >= MAX_EVENTS and lk.target not in seen:
                continue
            if lk.target not in seen:
                o = await cal_mirror.find_by_uid(db, p.user_id, lk.target, "event", p)
                ok = False
                if o is not None and _same_title(o.summary or "", obj.summary or ""):
                    oe = await cal_mirror.object_out(db, o)
                    ok = bool(emails & _emails(oe, mine)) or not emails
                seen[lk.target] = ok
            if seen[lk.target]:
                others.append(lk)
        rows = before(await rows_of(others))
        if rows:
            why, found = "same title and guests", rows[0]
    if found is None and _words(obj.summary or ""):
        names = _names(ev, mine)
        rows = (await db.execute(select(SpeakrRecording).where(
            SpeakrRecording.connector_id.in_(list(by_id)), SpeakrRecording.deleted_at.is_(None),
            SpeakrRecording.status == "completed")
            .order_by(SpeakrRecording.meeting_date.desc().nulls_last()).limit(300))).scalars().all()
        rows = [r for r in rows if _same_title(r.title, obj.summary or "")
                and (not names or names & _people(list(r.participants or [])))]
        rows = before(rows)
        if rows:
            why, found = "same title and people", rows[0]
    if found is None:
        return {"connected": True, "item": None, "upcoming": True}
    conn = by_id[found.connector_id]
    item = store.recording_out(conn, found) | {"why": why}
    if excerpt and found.has_summary:
        from mailr.integrations.speakr.mail import one_line

        try:
            item["summary_line"] = one_line(await store.part(db, conn, found.recording_id, "summary"), 200)
        except sc.SpeakrAuthError:
            raise
        except sc.SpeakrError:
            item["summary_line"] = None
    return {"connected": True, "item": item, "upcoming": True}
