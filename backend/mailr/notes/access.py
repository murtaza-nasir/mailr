"""Who may see and change a note: the one place that scopes every notes query.

`level(user, note)` is none, view, edit, share or owner. A note is owned by one user (owner) or by a
group (every member edits), and may be shared with users and groups (the rules are in
notes/acl.py, the shares in `note_shares`). A note the caller cannot see is a 404, exactly as a
note that does not exist.

Deleting and moving out of a group follow their own rule: the owner; for a group note its
creator or an instance admin.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.access import effective_groups, is_admin
from mailr.models import User
from mailr.notes import acl
from mailr.notes.models import Note

LEVELS = acl.LEVELS
RANK = acl.RANK

NOT_FOUND = "note not found"
VIEW_ONLY = "You have view access to this note only"


def groups(user: User) -> list[str]:
    return effective_groups(user)


def visible(user: User) -> Any:
    """The SQL condition of the notes `user` can see: own, group-owned and shared (notes/acl.py)."""
    return acl.visible(user, "note", Note)


def hidden_from(p: Any) -> bool:
    """Local-only notes are hidden from this caller: an API token that is not local-only (its
    model may be hosted), as local-only mail is."""
    tok = getattr(p, "token", None)
    return tok is not None and not tok.local_only


def visible_to(p: Any) -> Any:
    """`visible(p.user)`, without the local-only notes a token may not see."""
    cond = visible(p.user)
    return and_(cond, Note.local_only.is_(False)) if hidden_from(p) else cond


def level(user: User, note: Note) -> str:
    """none, view, edit, share or owner (notes/acl.py: ownership, group membership, shares)."""
    return acl.level(user, note)


def at_least(lv: str, need: str) -> bool:
    return RANK[lv] >= RANK[need]


def can_delete(user: User, note: Note) -> bool:
    """Trash, restore and purge: the owner; for a group note its creator or an admin."""
    if note.owner_user_id is not None:
        return note.owner_user_id == user.id
    return (note.created_by == user.id and level(user, note) != "none") or is_admin(user)


async def get(db: AsyncSession, user: User, pid: str, *, need: str = "view", trashed: bool = True,
              lock: bool = False, p: Any = None) -> Note:
    """The note `pid` as `user` may use it: 404 when they cannot see it, 403 when they see it
    at a lower level than `need`, 409 when it is in Trash and `trashed` is False. With `p` (the
    caller), a local-only note is a 404 for a token that may not see it (hidden_from)."""
    q = select(Note).where(Note.pid == str(pid))
    if lock:
        q = q.with_for_update()
    note = (await db.execute(q)).scalar_one_or_none()
    if note is None or (note.local_only and hidden_from(p)):
        raise HTTPException(404, NOT_FOUND)
    lv = level(user, note)
    if lv == "none" and not (need == "delete" and can_delete(user, note)):
        raise HTTPException(404, NOT_FOUND)
    if need == "delete":
        if not can_delete(user, note):
            raise HTTPException(403, "Only the owner can delete this note")
    elif not at_least(lv, need):
        raise HTTPException(403, VIEW_ONLY)
    if not trashed and note.deleted_at is not None:
        raise HTTPException(409, "This note is in Trash. Restore it to change it.")
    return note


def live_uid(note: Note) -> int:
    """The user whose live-version store (item_versions) holds the note and its items."""
    return int(note.owner_user_id or note.created_by or 0)


async def audience(db: AsyncSession, note: Note) -> list[int]:
    """Every user who can see the note now: they get its change events."""
    return await acl.audience(db, "note", note)
