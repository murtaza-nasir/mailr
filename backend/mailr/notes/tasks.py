"""Background work for notes: learning a closed trip (checks 45 minutes quiet), embeddings, and the
daily cleanup (Trash after 30 days, revisions after 30 days with the newest kept, check events after
365 days, expired usual-item dismissals, note reminders a week past their time)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.jobs import handler
from mailr.notes import actions, core, learn
from mailr.notes.models import CheckitemCheck, CheckitemUsualDismissed, Note

log = logging.getLogger("mailr.notes")


@handler("notes.learn")
async def learn_job(payload: dict, ctx) -> None:
    from mailr.db import session_scope

    async with session_scope() as db:
        note = await db.get(Note, int(payload["note_id"]))
        if note is None or note.deleted_at is not None:
            return
        n = await learn.learn(db, note, payload.get("section_id"))
        if n:
            await core.touch(db, note)


@handler("notes.embed")
async def embed_job(payload: dict, ctx) -> None:
    from mailr.db import session_scope
    from mailr.notes.embed import embed_pending

    async with session_scope() as db:
        uid = payload.get("user_id")
        await embed_pending(db, int(uid) if uid is not None else None, group=payload.get("group"))


async def prune(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """The daily cleanup (core/tasks._cleanup_db)."""
    now = now or datetime.now(UTC)
    out = {"purged": 0}
    old = (await db.execute(select(Note).where(Note.deleted_at < now - timedelta(days=core.TRASH_DAYS)))).scalars().all()
    for note in old:
        await actions._purge(db, note)
        out["purged"] += 1
    r = await db.execute(text(
        "DELETE FROM note_revisions r WHERE r.at < :cut AND EXISTS (SELECT 1 FROM note_revisions n "
        "WHERE n.note_id = r.note_id AND n.rev > r.rev)"), {"cut": now - timedelta(days=core.REVISION_DAYS)})
    out["revisions"] = r.rowcount or 0
    # Deleted items and sections stay restorable from Version history for 30 days.
    await db.execute(text("DELETE FROM note_items WHERE deleted_at < :cut"),
                     {"cut": now - timedelta(days=core.REVISION_DAYS)})
    await db.execute(text("DELETE FROM note_sections s WHERE s.deleted_at < :cut AND NOT EXISTS "
                          "(SELECT 1 FROM note_items i WHERE i.section_id = s.id)"),
                     {"cut": now - timedelta(days=core.REVISION_DAYS)})
    r = await db.execute(delete(CheckitemCheck).where(CheckitemCheck.server_at < now - timedelta(days=learn.LOG_DAYS)))
    out["checks"] = r.rowcount or 0
    await db.execute(delete(CheckitemUsualDismissed).where(CheckitemUsualDismissed.until.is_not(None),
                                                           CheckitemUsualDismissed.until < now.date()))
    from mailr.notes import reminders

    out["reminders"] = await reminders.prune(db, now)
    return out
