"""The share rules that notes/access.py and tables/access.py both read.

A note or a table is seen by:
- its owner (`owner`);
- the members of its owning group (`edit`; its creator, while a member, at `share`, so someone in the
  group can bring other people in);
- the users it is shared with directly, at their row's level, unless they left it;
- the members of the groups it is shared with, at the best of those levels, unless they have a row
  of their own: a user's own row wins over their groups' rows (as in calendar sharing), so a user
  who left keeps a row that hides the item from them.

A disabled user's shares are inert: they count for nobody, and come back when the account does.
The shares are loaded with the item (`share_acl`, notes/models.py), so `level()` stays a plain
function and every caller keeps its shape.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, exists, false, or_, select

from mailr.core.access import effective_groups
from mailr.models import User
from mailr.notes.models import NoteShare

LEVELS = ("none", "view", "edit", "share", "owner")
RANK = {lv: i for i, lv in enumerate(LEVELS)}
SHARE_LEVELS = ("view", "edit", "share")


def best(*lvs: str) -> str:
    return max(lvs, key=lambda x: RANK.get(x, 0)) if lvs else "none"


def acl_of(obj: Any) -> list[list[Any]]:
    """The item's share rows as loaded with it ([kind, principal, level, left]). An item made in this
    session has none, and its attribute is not loaded yet."""
    v = obj.__dict__.get("share_acl")
    return [r for r in (v or []) if isinstance(r, list) and len(r) >= 4]


def shared_level(user: User, acl: list[list[Any]], gs: list[str] | None = None) -> str:
    """The level the share rows give `user` (none when they left, or when a row of their own says
    none)."""
    if getattr(user, "disabled", False):
        return "none"
    me = str(user.id)
    own = next((r for r in acl if r[0] == "user" and str(r[1]) == me), None)
    if own is not None:
        return "none" if own[3] or own[2] not in SHARE_LEVELS else own[2]
    gs = set(gs if gs is not None else effective_groups(user))
    return best("none", *[r[2] for r in acl if r[0] == "group" and r[1] in gs and not r[3] and r[2] in SHARE_LEVELS])


def level(user: User, obj: Any, acl: list[list[Any]] | None = None) -> str:
    """none, view, edit, share or owner: the owner; a member of the owning group; the shares."""
    if obj.owner_user_id is not None and obj.owner_user_id == user.id:
        return "owner"
    gs = effective_groups(user)
    base = "none"
    if obj.owner_group is not None and obj.owner_group in gs and not getattr(user, "disabled", False):
        base = "share" if obj.created_by == user.id else "edit"
    return best(base, shared_level(user, acl if acl is not None else acl_of(obj), gs))


def visible(user: User, kind: str, model: Any) -> Any:
    """The SQL condition of the items of `kind` (note | table) that `user` can see."""
    gs = effective_groups(user)
    s = NoteShare
    on = and_(s.item_kind == kind, s.item_id == model.id)
    me = and_(on, s.principal_kind == "user", s.principal == str(user.id))
    conds = [model.owner_user_id == user.id]
    if gs:
        conds.append(model.owner_group.in_(gs))
    if not getattr(user, "disabled", False):
        conds.append(exists(select(s.id).where(me, s.left_at.is_(None), s.level.in_(SHARE_LEVELS))))
        if gs:
            conds.append(and_(~exists(select(s.id).where(me)),
                              exists(select(s.id).where(on, s.principal_kind == "group", s.principal.in_(gs),
                                                        s.left_at.is_(None)))))
    return or_(*conds) if conds else false()


def shared_since(user: User, kind: str, since: Any) -> Any:
    """The ids of the items of `kind` shared with `user`, or with one of their groups, after `since`
    (a select for `.in_()`): the phone's delta sync lists them even when the item itself did not
    change."""
    gs = effective_groups(user)
    s = NoteShare
    who = and_(s.principal_kind == "user", s.principal == str(user.id))
    if gs:
        who = or_(who, and_(s.principal_kind == "group", s.principal.in_(gs)))
    return select(s.item_id).where(s.item_kind == kind, s.created_at > since, who)


async def rows(db: Any, kind: str, item_id: int) -> list[list[Any]]:
    """The share rows of an item, read now (not the copy loaded with it)."""
    return [[r.principal_kind, r.principal, r.level, r.left_at is not None]
            for r in (await db.execute(select(NoteShare).where(NoteShare.item_kind == kind,
                                                               NoteShare.item_id == item_id))).scalars()]


async def audience(db: Any, kind: str, obj: Any) -> list[int]:
    """Every active user who can see the item now: they get its change events."""
    acl = await rows(db, kind, obj.id)
    if obj.owner_group is None and not any(r[0] == "group" for r in acl):
        ids = {obj.owner_user_id} | {int(r[1]) for r in acl if r[0] == "user" and str(r[1]).isdigit()}
        users = (await db.execute(select(User).where(User.id.in_([i for i in ids if i]),
                                                     User.disabled.is_(False)))).scalars().all()
    else:
        users = (await db.execute(select(User).where(User.disabled.is_(False)))).scalars().all()
    return sorted(u.id for u in users if level(u, obj, acl) != "none")


def is_shared(obj: Any) -> bool:
    """Other people can see it: group-owned, or shared with someone (a leave row does not count)."""
    return obj.owner_group is not None or any(r[2] in SHARE_LEVELS and not r[3] for r in acl_of(obj))


def user_ids(objs: list[Any]) -> set[int]:
    """The users named by the share rows of these items (for one names() query)."""
    return {int(r[1]) for o in objs for r in acl_of(o) if r[0] == "user" and str(r[1]).isdigit()}


def summary(obj: Any, nm: dict[int, str], user: User | None = None) -> dict[str, Any]:
    """Who else sees the item, for "Shared with 2 people" and the initials on a card (the viewer
    is left out)."""
    me = str(user.id) if user is not None else None
    rows = [r for r in acl_of(obj) if r[2] in SHARE_LEVELS and not r[3] and not (r[0] == "user" and str(r[1]) == me)]
    people = [{"kind": "user", "name": nm.get(int(r[1]), "")} if r[0] == "user" else {"kind": "group", "name": r[1]}
              for r in rows]
    return {"count": len(rows), "people": people[:8]}


def changed(user: User, obj: Any, st: Any) -> bool:
    """The "changed since you looked" dot: someone else changed a shared item after this viewer last
    opened it."""
    if not is_shared(obj) or obj.updated_at is None:
        return False
    if (obj.updated_by or {}).get("id") in (None, user.id):
        return False
    seen = getattr(st, "seen_at", None) if st is not None else None
    return seen is None or obj.updated_at > seen


def shared_cond(user: User, kind: str, model: Any) -> Any:
    """`is:shared`: items other people see too (someone else's, a group's, or the user's own with an
    active share)."""
    s = NoteShare
    return or_(model.owner_user_id.is_(None), model.owner_user_id != user.id,
               exists(select(s.id).where(s.item_kind == kind, s.item_id == model.id, s.left_at.is_(None),
                                         s.level.in_(SHARE_LEVELS))))
