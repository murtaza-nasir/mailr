"""Who may see and change a table: the one place that scopes every tables query (a copy of
notes/access.py for the `tables` table).

`level(user, table)` is none, view, edit, share or owner. A table is owned by one user (owner) or
by a group (every member edits), and may be shared with users and groups (the rules are in
notes/acl.py, the shares in `note_shares`). A table the caller cannot see is a 404, exactly as one
that does not exist.

Deleting follows its own rule: the owner; for a group table its creator or an admin.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.access import effective_groups, is_admin
from mailr.models import User
from mailr.notes import acl
from mailr.tables.models import DataTable

LEVELS = acl.LEVELS
RANK = acl.RANK

NOT_FOUND = "table not found"


def groups(user: User) -> list[str]:
    return effective_groups(user)


def visible(user: User) -> Any:
    """The SQL condition of the tables `user` can see: own, group-owned and shared (notes/acl.py)."""
    return acl.visible(user, "table", DataTable)


def level(user: User, t: DataTable) -> str:
    """none, view, edit, share or owner (notes/acl.py: ownership, group membership, shares)."""
    return acl.level(user, t)


def at_least(lv: str, need: str) -> bool:
    return RANK[lv] >= RANK[need]


def can_delete(user: User, t: DataTable) -> bool:
    """Trash, restore and purge: the owner; for a group table its creator or an admin."""
    if t.owner_user_id is not None:
        return t.owner_user_id == user.id
    return (t.created_by == user.id and level(user, t) != "none") or is_admin(user)


async def get(db: AsyncSession, user: User, pid: str, *, need: str = "view", trashed: bool = True,
              lock: bool = False) -> DataTable:
    """The table `pid` as `user` may use it: 404 when they cannot see it, 403 when they see it at a
    lower level than `need`, 409 when it is in Trash and `trashed` is False."""
    q = select(DataTable).where(DataTable.pid == str(pid)).execution_options(populate_existing=True)
    if lock:
        q = q.with_for_update()
    t = (await db.execute(q)).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, NOT_FOUND)
    lv = level(user, t)
    if lv == "none" and not (need == "delete" and can_delete(user, t)):
        raise HTTPException(404, NOT_FOUND)
    if need == "delete":
        if not can_delete(user, t):
            raise HTTPException(403, "Only the owner can delete this table")
    elif not at_least(lv, need):
        raise HTTPException(403, "You have view access to this table only")
    if not trashed and t.deleted_at is not None:
        raise HTTPException(409, "This table is in Trash. Restore it to change it.")
    return t


def live_uid(t: DataTable) -> int:
    """The user whose live-version store (item_versions) holds the table and its rows."""
    return int(t.owner_user_id or t.created_by or 0)


async def audience(db: AsyncSession, t: DataTable) -> list[int]:
    """Every user who can see the table now: they get its change events."""
    return await acl.audience(db, "table", t)
