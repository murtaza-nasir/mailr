"""The link picker's candidates for a link column: only things the
user can see, matched by their title."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import User
from mailr.tables import access, core
from mailr.tables import types as ty
from mailr.tables.models import DataTable
from mailr.tables.types import Col

LIMIT = 20


async def candidates(db: AsyncSession, user: User, col: Col, q: str) -> list[dict[str, Any]]:
    target = (col.config or {}).get("target")
    words = (q or "").strip().lower()
    like = f"%{words}%"
    if target == "table":
        t = (await db.execute(select(DataTable).where(DataTable.pid == (col.config or {}).get("table")))).scalar_one_or_none()
        if t is None or access.level(user, t) == "none" or t.deleted_at is not None:
            return []
        cols = core.cols_map(await core.columns_of(db, t))
        out = []
        for r in sorted(await core.rows_of(db, t), key=lambda r: (r.order, r.id)):
            title = core.row_title(t, cols, r)
            if words and words not in title.lower() and words not in (r.search_text or "").lower():
                continue
            out.append({"ref": {"kind": "row", "table": t.pid, "row": r.id}, "title": title, "sub": t.name})
            if len(out) >= LIMIT:
                break
        return out
    if target == "conversation":
        from mailr.core.publicid import ConvId
        from mailr.models import Conversation

        qy = select(Conversation).where(Conversation.user_id == user.id)
        if words:
            qy = qy.where(func.lower(Conversation.subject).like(like))
        rows = (await db.execute(qy.order_by(Conversation.last_date.desc().nulls_last()).limit(LIMIT))).scalars().all()
        return [{"ref": {"kind": "conversation", "conversation_id": ConvId(c.id)}, "title": c.subject or "(no subject)",
                 "sub": c.last_date.date().isoformat() if c.last_date else None} for c in rows]
    if target in ("event", "task"):
        from mailr.models import CalObject

        qy = select(CalObject).where(CalObject.user_id == user.id, CalObject.kind == target,
                                     CalObject.deleted_at.is_(None))
        if words:
            qy = qy.where(func.lower(CalObject.summary).like(like))
        rows = (await db.execute(qy.limit(LIMIT * 3))).scalars().all()
        out = []
        seen = set()
        for o in rows:
            if o.uid in seen:
                continue
            seen.add(o.uid)
            when = (o.start_date if getattr(o, "all_day", False) else getattr(o, "start_at", None)) if target == "event" \
                else (getattr(o, "due_date", None) or getattr(o, "due_at", None))
            out.append({"ref": {"kind": target, "uid": o.uid}, "title": o.summary or "(no title)",
                        "sub": when.isoformat()[:10] if when else None})
            if len(out) >= LIMIT:
                break
        return out
    if target == "note":
        from mailr.notes import access as naccess
        from mailr.notes.models import Note

        nq = select(Note).where(naccess.visible(user), Note.deleted_at.is_(None))
        tq = select(DataTable).where(access.visible(user), DataTable.deleted_at.is_(None))
        if words:
            nq = nq.where(func.lower(Note.title).like(like))
            tq = tq.where(func.lower(DataTable.name).like(like))
        notes = (await db.execute(nq.order_by(Note.updated_at.desc()).limit(LIMIT))).scalars().all()
        tables = (await db.execute(tq.order_by(DataTable.updated_at.desc()).limit(LIMIT))).scalars().all()
        out = [{"ref": {"kind": "note", "id": n.pid}, "title": n.title or "Untitled", "sub": n.kind} for n in notes]
        out += [{"ref": {"kind": "note", "id": t.pid}, "title": t.name or "Untitled table", "sub": "table"} for t in tables]
        return out[:LIMIT]
    return []


_ = ty
