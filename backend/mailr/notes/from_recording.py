"""A text note from a Speakr recording (note.from_recording).

The note holds what mailr already mirrors of the recording: its title, the meeting's date and
length, the people in it, Speakr's summary and the action items. Action items are the summary's own
"Action items" section and the tasks already made from the recording (recording.to_tasks). No
transcript text goes in, and no model is asked. The note links to the recording, so it shows in the
recording's Links tab and the recording shows on the note. A note from a local-only recording is
local-only too: it follows the recording's data class wherever notes go.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from fastapi import HTTPException

BODY_MAX = 20_000
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_ACTIONS = re.compile(r"^(action items?|next steps|to-?dos?|follow-?ups?)\s*:?\s*$", re.I)


def _plain_heading(line: str) -> str | None:
    m = _HEADING.match(line)
    if m:
        return m.group(1).strip().strip("*_").strip()
    b = re.fullmatch(r"\s*\*\*(.+?)\*\*:?\s*", line)
    return b.group(1).strip() if b else None


def split_summary(md: str) -> tuple[str, list[str]]:
    """The summary without its action-item section, and that section's items (one per bullet)."""
    keep: list[str] = []
    items: list[str] = []
    inside = False
    for line in (md or "").replace("\r\n", "\n").split("\n"):
        head = _plain_heading(line)
        if head is not None:
            inside = bool(_ACTIONS.match(head))
            if not inside:
                keep.append(f"**{head}**")  # notes take light Markdown: no headings
            continue
        if inside:
            m = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.*\S)", line)
            if m:
                items.append(m.group(1).strip())
            elif line.strip() and items:
                items[-1] += " " + line.strip()  # a bullet that wrapped
            continue
        keep.append(line)
    text = "\n".join(keep).strip()
    return re.sub(r"\n{3,}", "\n\n", text), items


def _norm(t: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (t or "").lower()))


def _length(seconds: float | None) -> str | None:
    if not seconds:
        return None
    m = round(seconds / 60)
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{max(m, 1)} min"


async def content(db, p, conn, row) -> dict[str, Any]:
    """{title, body, local_only} of the note a recording becomes."""
    from mailr.agent import when
    from mailr.calendar import mirror as cal_mirror
    from mailr.calendar import tz as tzmod
    from mailr.integrations.speakr import service, store

    summary = row.summary_text or ""
    if not summary and row.has_summary:
        try:
            summary = str(await store.part(db, conn, row.recording_id, "summary") or "")
        except Exception:  # noqa: BLE001  Speakr unreachable: the note is made without the summary
            summary = ""
    text, items = split_summary(summary)
    # Tasks already made from the recording, unless the summary lists them in the same words.
    have = {_norm(i) for i in items}
    for lk in await service.links_of(db, conn, row.recording_id):
        if lk.kind != "task" or lk.state != "confirmed":
            continue
        obj = await cal_mirror.find_by_uid(db, p.user_id, lk.target, "task", p)
        if obj is not None and obj.summary and _norm(obj.summary) not in have:
            have.add(_norm(obj.summary))
            items.append(obj.summary)

    zone = await tzmod.user_zone(db, p.user_id)
    fmt = "24h" if (p.user.settings or {}).get("time_format") == "24h" else "12h"
    start = row.meeting_date or row.created_upstream
    lines: list[str] = []
    meta = []
    if start is not None:
        end = start + timedelta(seconds=row.duration_s) if row.duration_s else None
        meta.append(when.span_text(start.isoformat(), end.isoformat() if end else None, False, zone, fmt=fmt) or "")
    elif (ln := _length(row.duration_s)):
        meta.append(ln)
    if meta:
        lines.append(" · ".join(x for x in meta if x))
    if row.participants:
        lines.append("With " + ", ".join(row.participants))
    if text:
        lines += ["", "**Summary**", "", text]
    if items:
        lines += ["", "**Action items**", ""] + [f"- {i}" for i in items]
    if not text and not items:
        lines += ["", "No summary yet."]
    body = "\n".join(lines).strip()
    if len(body) > BODY_MAX:
        body = body[: BODY_MAX - 1].rstrip() + "…"
    return {"title": (row.title or "Recording")[:500], "body": body, "local_only": conn.data_class == "local_only"}


async def check(db, p, connector_id: Any, recording_id: Any):
    """The recording's connector and mirror row, as the caller may read them: an API token needs
    the recordings scope (403), and the recording must be one it can see (404)."""
    from mailr.integrations.speakr import store

    if getattr(p, "is_token", False) and "recordings" not in (getattr(p, "scopes", None) or set()):
        raise HTTPException(403, "this token cannot read recordings (scope recordings)")
    try:
        return await store.find(db, p, int(recording_id), int(connector_id) if connector_id is not None else None)
    except (TypeError, ValueError):
        raise HTTPException(422, "connector_id and recording_id are numbers") from None
    except store.RecordingNotFound:
        raise HTTPException(404, "recording not found") from None


async def make(db, p, connector_id: Any, recording_id: Any) -> dict[str, Any]:
    """ACT note.from_recording: the note, made and linked to the recording."""
    from mailr.notes import access, actions, core

    conn, row = await check(db, p, connector_id, recording_id)
    c = await content(db, p, conn, row)
    made = await actions.create_note(db, p.user, kind="text", title=c["title"], body=c["body"],
                                     local_only=c["local_only"])
    note = await access.get(db, p.user, made["note"]["id"], p=p)
    await actions.link(db, p.user, note, "recording",
                       {"connector_id": conn.id, "recording_id": row.recording_id})
    made["note"] = await core.note_full(db, p.user, note)
    return made
