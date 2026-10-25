"""Events and tasks as live-edited items (core/live.py).

`snapshot` is an item's editable fields in one fixed shape, recorded as its version content after
every write mailr makes (calendar/writer.py `_store`). A change made from an older version
(`base_version` on PATCH /calendar/events/{uid}, PATCH /tasks/{uid} and the assistant's tools) is a
patch: `resolve_patch` keeps each changed field when nobody else changed it since that version,
merges a description or notes changed on both sides line by line, and refuses (HTTP 409) a field
both changed differently.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import live, merge3

EVENT_KEYS = ("summary", "location", "description", "start", "end", "all_day", "tz", "rrule", "transparent",
              "class", "color", "url", "reminders", "attendee_list", "status", "calendar")
TASK_KEYS = ("summary", "notes", "due", "due_all_day", "start", "status", "priority", "categories", "rrule",
             "repeat_from", "parent", "reminders", "reminders_at", "list", "sort_order", "tz")

# The fields of the snapshot a patch field changes. A patch field nobody else changed since the
# base version is applied as it is.
PATCH_FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "event": {"start": ("start", "all_day", "tz"), "end": ("end", "all_day"), "recurrence": ("rrule",),
              "attendees": ("attendee_list",), "all_day": ("all_day", "start", "end")},
    "task": {"due": ("due", "due_all_day"), "recurrence": ("rrule",), "reminders_at": ("reminders_at",),
             "completed": ("status",), "clear_due": ("due",), "clear_start": ("start",)},
}
TEXT = {"event": {"description": "lines"}, "task": {"notes": "lines"}}


def snapshot(obj: Any, coll: Any, user_tz: Any) -> dict[str, Any]:
    from mailr.calendar import mirror

    if obj.kind == "task":
        out = mirror.task_out(obj, coll, user_tz)
        from mailr.calendar.todo import split_alarms

        snap = {k: out.get(k) for k in TASK_KEYS}
        snap["notes"] = obj.description or ""
        # Relative and absolute reminders apart, as the task API gives them: an absolute one alone
        # changed (the assistant's edit of 10 Oct 2026) makes a version too.
        snap["reminders"], snap["reminders_at"] = split_alarms(obj.ics or "", out.get("reminders") or [])
        return snap
    out = mirror.event_out(obj, coll, user_tz, full_description=True)
    return {k: out.get(k) for k in EVENT_KEYS}


async def snapshot_of(db: AsyncSession, obj: Any) -> dict[str, Any]:
    from mailr.calendar import tz as tzmod
    from mailr.models import CalCollection

    return snapshot(obj, await db.get(CalCollection, obj.collection_id), await tzmod.user_zone(db, obj.user_id))


async def record(db: AsyncSession, obj: Any) -> int:
    """After mailr wrote `obj`: its new version (and the live event)."""
    return await live.commit(db, obj.user_id, obj.kind, obj.uid, await snapshot_of(db, obj))


async def version_of(db: AsyncSession, obj: Any) -> int | None:
    row = await live.latest(db, obj.user_id, obj.kind, obj.uid)
    return row.version if row is not None else None


async def current(db: AsyncSession, obj: Any) -> tuple[int, dict[str, Any]]:
    """(version, snapshot) of the item as stored now (core/live.py `state`)."""
    return await live.state(db, obj.user_id, obj.kind, obj.uid, live.loader(db, obj, lambda o: snapshot_of(db, o)))


async def resolve_patch(db: AsyncSession, obj: Any, changes: dict[str, Any], base_version: int | None
                        ) -> tuple[dict[str, Any], bool]:
    """The changes to write for a patch made from `base_version` (see the module docstring).
    Raises live.LiveConflict."""
    if base_version is None:
        return changes, False
    kind = obj.kind
    await live.lock(db, obj.user_id, kind, obj.uid, obj)  # and read obj again under it
    head, now = await current(db, obj)
    if int(base_version) >= head:
        return changes, False
    base = await live.at(db, obj.user_id, kind, obj.uid, int(base_version))
    fields = PATCH_FIELDS.get(kind, {})
    text = TEXT.get(kind, {})
    out = dict(changes)
    conflicts: list[str] = []
    texts: dict[str, list[merge3.Conflict]] = {}
    for f, val in changes.items():
        keys = fields.get(f, (f,))
        if base is not None and all(merge3.same(base.get(k), now.get(k)) for k in keys):
            continue  # nobody else changed it
        if base is not None and f in text and isinstance(val, (str, type(None))):
            r = merge3.merge_text(base.get(f), val, now.get(f), text[f])
            if r.ok:
                out[f] = r.text
                continue
            texts[f] = r.conflicts
        elif len(keys) == 1 and merge3.same(val, now.get(keys[0])):
            continue  # the same change on both sides
        conflicts.append(f)
    if conflicts:
        row = await live.latest(db, obj.user_id, kind, obj.uid)
        raise live.LiveConflict(kind, obj.uid, base, int(base_version) if base is not None else None, now, head,
                                changes, conflicts, texts, by=row.by if row is not None else None)
    return out, True
