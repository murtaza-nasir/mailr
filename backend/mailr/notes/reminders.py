"""Reminders on notes.

Absolute reminders only, on the shared reminder contract: the key is
`n|<note pid>|abs|<instant>`, with the instant in the canonical form of the calendar API
(UTC isoformat, seconds). A reminder belongs to the viewer who set it (`note_reminders`),
so a sharee's reminder reminds only them.

For the web the server plans and fires them: setting a reminder writes a row of
`reminder_schedule` (kind `n`, uid = the note's public id, occurrence = the instant), which the
minute job fires like a one-off event reminder (SSE, Web Push, the tray). Snoozes and dismissals go
through /reminders/snooze and /reminders/handle with the same key, so handled anywhere is gone
everywhere. The assistant never snoozes or dismisses a reminder; it may set one.

There are no item-level reminders: an item that needs a time becomes a task.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import ical
from mailr.core.events import record_change
from mailr.models import ReminderSchedule, ReminderState, User
from mailr.notes.models import Note, NoteReminder

MAX = 10  # reminders per note and viewer
PAST_GRACE = timedelta(minutes=1)
KEEP = timedelta(days=7)  # a reminder that fired stays on the note this long, as fired rows do
DETAIL = {"text": "Note reminder", "checklist": "List reminder"}


def instant(v: Any) -> datetime:
    """A reminder time as an aware UTC datetime (seconds), from an ISO string or a datetime."""
    try:
        d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, f"{str(v)[:40]!r} is not an ISO date and time") from None
    if d.tzinfo is None:
        raise HTTPException(422, "A reminder time needs its zone (Z or an offset)")
    return d.astimezone(UTC).replace(microsecond=0)


def key_for(pid: str, at: datetime) -> str:
    return f"n|{pid}|abs|{ical.canonical_occurrence(at)}"


def title_of(note: Note) -> str:
    return (note.title or "").strip() or ("Untitled list" if note.kind == "checklist" else "Untitled note")


async def states(db: AsyncSession, user_id: int, keys: list[str]) -> dict[str, ReminderState]:
    if not keys:
        return {}
    return {s.key: s for s in (await db.execute(select(ReminderState).where(
        ReminderState.user_id == user_id, ReminderState.key.in_(keys)))).scalars()}


def state_word(rem: NoteReminder, st: ReminderState | None, now: datetime, fired: bool = False) -> str:
    """upcoming | fired | snoozed | handled, as the note's bell and the Reminders view show it.
    `fired`: the server's plan has fired it already."""
    if st is not None and st.state == "handled":
        return "handled"
    if st is not None and st.state == "snoozed" and st.snoozed_until and st.snoozed_until > now:
        return "snoozed"
    return "fired" if fired or rem.at <= now else "upcoming"


async def of_notes(db: AsyncSession, user: User, notes: list[Note]) -> dict[int, list[dict[str, Any]]]:
    """This viewer's reminders on each note: [{key, at, state, snoozed_until}], soonest first.
    Handled ones are left out."""
    if not notes:
        return {}
    pid = {n.id: n.pid for n in notes}
    rows = list((await db.execute(select(NoteReminder).where(
        NoteReminder.user_id == user.id, NoteReminder.note_id.in_(list(pid))).order_by(NoteReminder.at))).scalars())
    keys = {r.id: key_for(pid[r.note_id], r.at) for r in rows}
    sts = await states(db, user.id, list(keys.values()))
    fired = set((await db.execute(select(ReminderSchedule.key).where(
        ReminderSchedule.user_id == user.id, ReminderSchedule.key.in_(list(keys.values()) or [""]),
        ReminderSchedule.fired_at.is_not(None)))).scalars())
    now = datetime.now(UTC)
    out: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        st = sts.get(keys[r.id])
        word = state_word(r, st, now, keys[r.id] in fired)
        if word == "handled":
            continue
        out.setdefault(r.note_id, []).append({
            "key": keys[r.id], "at": ical.canonical_occurrence(r.at), "state": word,
            "snoozed_until": st.snoozed_until.isoformat() if word == "snoozed" and st and st.snoozed_until else None})
    return out


def next_at(rems: list[dict[str, Any]]) -> str | None:
    """When the note reminds next: the soonest snooze or upcoming time; else the latest fired one."""
    if not rems:
        return None
    soon = sorted((r["snoozed_until"] or r["at"]) for r in rems if r["state"] in ("upcoming", "snoozed"))
    return soon[0] if soon else max(r["at"] for r in rems)


async def _schedule(db: AsyncSession, user: User, note: Note, at: datetime) -> None:
    """The server's plan for one reminder (calendar/planner.py fires it)."""
    key = key_for(note.pid, at)
    row = (await db.execute(select(ReminderSchedule).where(ReminderSchedule.user_id == user.id,
                                                           ReminderSchedule.key == key))).scalar_one_or_none()
    if row is None:
        row = ReminderSchedule(user_id=user.id, key=key, kind="n", uid=note.pid,
                               occurrence=ical.canonical_occurrence(at), minutes=0)
        db.add(row)
    row.anchor_at, row.fire_at, row.fired_at = at, at, None
    row.title, row.detail, row.source = title_of(note), DETAIL.get(note.kind, "Note reminder"), "oneoff"
    row.local_only, row.planned_at = bool(note.local_only), datetime.now(UTC)


async def set_reminders(db: AsyncSession, user: User, note: Note, ats: list[Any]) -> dict[str, Any]:
    """ACT note.remind: the viewer's reminders on the note become `ats` (an empty list removes them).
    A new time must not be in the past. The undo puts the previous list back."""
    now = datetime.now(UTC)
    want = sorted({instant(a) for a in ats})
    if len(want) > MAX:
        raise HTTPException(422, f"A note can have at most {MAX} reminders")
    old = list((await db.execute(select(NoteReminder).where(
        NoteReminder.user_id == user.id, NoteReminder.note_id == note.id))).scalars())
    sts = await states(db, user.id, [key_for(note.pid, r.at) for r in old])
    have = {r.at: r for r in old}
    before = [ical.canonical_occurrence(r.at) for r in sorted(old, key=lambda r: r.at)
              if state_word(r, sts.get(key_for(note.pid, r.at)), now) != "handled"]
    for at in want:
        if at not in have and at < now - PAST_GRACE:
            raise HTTPException(422, "That time has passed. Pick a later one.")
    gone = [r for r in old if r.at not in set(want)]
    gone_keys = [key_for(note.pid, r.at) for r in gone]
    for r in gone:
        await db.delete(r)
    if gone_keys:
        await db.execute(delete(ReminderSchedule).where(ReminderSchedule.user_id == user.id,
                                                        ReminderSchedule.key.in_(gone_keys)))
    fresh: list[str] = []
    for at in want:
        k = key_for(note.pid, at)
        st = sts.get(k)
        if at in have and not (st is not None and st.state == "handled"):
            continue  # kept as it is: its schedule and state stand
        if at not in have:
            db.add(NoteReminder(user_id=user.id, note_id=note.id, at=at))
        fresh.append(k)
        await _schedule(db, user, note, at)
    # A time set again (after it was handled, or removed and added back) reminds again: its old state goes.
    if fresh or gone_keys:
        res = await db.execute(delete(ReminderState).where(ReminderState.user_id == user.id,
                                                           ReminderState.key.in_(fresh + gone_keys))
                               .returning(ReminderState.id))
        ids = [i for (i,) in res.all()]
        if ids:
            await record_change(db, user.id, "reminder", ids, op="delete")
    await db.flush()
    from mailr.notes import actions, core

    # The viewer's state row carries the change time, so the phones' delta sync (GET /notes/sync)
    # brings the note back with its new reminders.
    st = await core.state_of(db, user, note, create=True)
    st.updated_at = now
    await core.touch(db, note)
    rems = (await of_notes(db, user, [note])).get(note.id, [])
    if want:
        when = want[0].isoformat()
        label = "Reminder set" if len(want) == 1 else f"{len(want)} reminders set"
    else:
        when, label = None, "Reminder removed"
    return {"reminders": rems, "next": when,
            "undo": actions.undo("PUT", f"/notes/{note.pid}/reminders", {"at": before}, label)}


async def replan_note(db: AsyncSession, note: Note) -> None:
    """The note's title or kind changed, or it came back from Trash: the plan's rows follow."""
    rows = list((await db.execute(select(ReminderSchedule).where(ReminderSchedule.kind == "n",
                                                                 ReminderSchedule.uid == note.pid))).scalars())
    for r in rows:
        r.title, r.detail, r.local_only = title_of(note), DETAIL.get(note.kind, "Note reminder"), bool(note.local_only)


async def drop_note(db: AsyncSession, note: Note) -> None:
    """The note is deleted forever: its plan rows go (its reminder rows cascade)."""
    await db.execute(delete(ReminderSchedule).where(ReminderSchedule.kind == "n", ReminderSchedule.uid == note.pid))


async def relevant(db: AsyncSession, r: ReminderSchedule) -> bool:
    """A note reminder still reminds: the note exists, is not in Trash, the viewer still sees it,
    and the reminder is still on it."""
    from mailr.notes import access

    note = (await db.execute(select(Note).where(Note.pid == r.uid))).scalar_one_or_none()
    if note is None or note.deleted_at is not None:
        return False
    user = await db.get(User, r.user_id)
    if user is None or access.level(user, note) == "none":
        return False
    at = ical.parse_when(r.occurrence)
    return bool((await db.execute(select(NoteReminder.id).where(
        NoteReminder.user_id == r.user_id, NoteReminder.note_id == note.id, NoteReminder.at == at))).first())


async def visible_to(db: AsyncSession, p: Any, pid: str) -> bool:
    """A token that may not see local-only notes does not see their reminders in the tray."""
    from mailr.notes import access

    note = (await db.execute(select(Note).where(Note.pid == pid))).scalar_one_or_none()
    return note is not None and not (note.local_only and access.hidden_from(p))


async def view_ids(db: AsyncSession, user: User) -> list[int]:
    """The notes of the Reminders view: those with a reminder of this viewer
    that is not handled."""
    rows = (await db.execute(select(NoteReminder.note_id, NoteReminder.at, Note.pid).join(
        Note, Note.id == NoteReminder.note_id).where(NoteReminder.user_id == user.id))).all()
    sts = await states(db, user.id, [key_for(pid, at) for _, at, pid in rows])
    out = []
    for nid, at, pid in rows:
        st = sts.get(key_for(pid, at))
        if not (st is not None and st.state == "handled"):
            out.append(nid)
    return list(dict.fromkeys(out))


async def prune(db: AsyncSession, now: datetime | None = None) -> int:
    """The daily cleanup: reminders more than KEEP past their time leave the note (the tray had
    them for as long; their schedule rows go with the planner's own cleanup)."""
    now = now or datetime.now(UTC)
    res = await db.execute(delete(NoteReminder).where(NoteReminder.at < now - KEEP))
    return res.rowcount or 0
