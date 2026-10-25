"""Sharing notes, checklists and tables with people and groups on this server.
Notes and tables follow one sharing policy.

The model is calendar sharing's (calendar/sharing.py), on rows of `note_shares` instead of a DAV ACL:

- **Levels.** view, edit, or edit and share (`share`). A group-owned item gives every member edit
  and its creator share. notes/acl.py turns the rows into a level.
- **With whom.** mailr users and the mailr groups the person sharing is in, under the one instance
  policy `calendars.sharing` (anyone, people who share a group, nobody). A narrower policy applies
  to new shares and level changes; existing shares stay until someone stops them.
- **Who shares.** The signed-in user only (the routes refuse API tokens; the assistant has no tool).
  Someone at "edit and share" manages the other people like the owner, never the owner or
  themselves.
- **Leaving.** A sharee removes the item from their own view; their own row keeps `left_at`, so a
  group that still has it does not bring it back. "Add back" (the undo) clears it. When the owner
  stops sharing with someone who left, the row loses its level (it stays only as the leave), so
  "Add back" no longer restores anything.
- **Finding people.** The dialog lists the people who share a group with the person sharing. Anyone
  else on the server appears only for a typed search of at least SEARCH_MIN characters, at most
  SEARCH_MAX at a time, so the dialog is not a directory of every account; at most
  SEARCH_PER_WINDOW searches per person in SEARCH_WINDOW seconds find anyone outside their groups.
  Sharing by user id (or stopping) names only someone the person may see: a group-mate, someone
  already on the item, or someone whose address the request gives (`email`, what the search showed).
  Anyone else gets the same 404 as an id nobody has, and no notice goes out.
- **Group-owned.** The owner moves an item into one of their groups; its creator or an admin moves
  it out again. Every member edits; a member who leaves the group loses it.
- **Notices.** A new share leaves a quiet notice (core/share_notices.py, key `notes_share_notices`)
  and a toast in an open session. No mail, no push.
- **People who go.** Deleting a user removes their shares and the shares of what they owned. A
  disabled user's shares are inert (notes/acl.py) and come back with the account.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import share_notices
from mailr.core.access import effective_groups, is_admin
from mailr.i18n import ApiError
from mailr.models import User
from mailr.notes import acl
from mailr.notes.models import Note, NoteShare, NoteViewerState

log = logging.getLogger("mailr.notes.sharing")

NOTICES = "notes_share_notices"
MAX_PEOPLE = 50
SEARCH_MIN = 3  # characters a search needs before people outside the viewer's groups appear
SEARCH_MAX = 8  # people a search returns
SEARCH_PER_WINDOW = 30  # searches for people outside one's groups, per person and window
SEARCH_WINDOW = 600.0
_searches: dict[int, deque[float]] = defaultdict(deque)


def _search_allowed(user_id: int) -> bool:
    """A sliding-window count of one person's people searches (in this process)."""
    now = time.monotonic()
    q = _searches[user_id]
    while q and now - q[0] > SEARCH_WINDOW:
        q.popleft()
    if len(q) >= SEARCH_PER_WINDOW:
        return False
    q.append(now)
    return True
KINDS = ("note", "table")


# ---------------------------------------------------------------- the kinds


def _model(kind: str) -> Any:
    if kind == "note":
        return Note
    from mailr.tables.models import DataTable

    return DataTable


def _access(kind: str) -> Any:
    if kind == "note":
        from mailr.notes import access
    else:
        from mailr.tables import access
    return access


def noun(kind: str, obj: Any) -> str:
    if kind == "table":
        return "table"
    return "list" if getattr(obj, "kind", "") == "checklist" else "note"


def name_of(kind: str, obj: Any) -> str:
    if kind == "table":
        return obj.name or "Untitled table"
    return obj.title or ("Untitled list" if obj.kind == "checklist" else "Untitled note")


def path_of(kind: str, obj: Any) -> str:
    return f"{kind}:{obj.pid}"


def person(u: User) -> dict[str, Any]:
    return {"id": u.id, "name": u.name or u.email.split("@")[0], "email": u.email}


async def policy(db: AsyncSession) -> str:
    """The one instance sharing policy: `calendars.sharing`."""
    from mailr.calendar.sharing import policy as cal_policy

    return await cal_policy(db)


def allowed_by(pol: str, actor: User, other: User) -> bool:
    from mailr.calendar.sharing import allowed_by as cal_allowed

    return cal_allowed(pol, actor, other)


async def load(db: AsyncSession, user: User, kind: str, pid: str, *, need: str = "view") -> Any:
    """The item as `user` may use it (404 when they cannot see it), with its shares read now."""
    obj = await _access(kind).get(db, user, pid, need=need)
    await db.refresh(obj, ["share_acl"])
    return obj


async def _audience(db: AsyncSession, kind: str, obj: Any) -> set[int]:
    return set(await _access(kind).audience(db, obj))


async def _tell(db: AsyncSession, kind: str, obj: Any, before: set[int], actor: User) -> None:
    """After a change of who sees the item: everyone before and after refetches their lists; those
    who lost it hear who stopped sharing it ("Dana stopped sharing this note")."""
    from mailr.core import live
    from mailr.core.events import notify

    await db.flush()
    await db.refresh(obj, ["share_acl"])
    after = await _audience(db, kind, obj)
    who = person(actor)["name"]
    for uid in sorted(before | after):
        ev: dict[str, Any] = {"type": "notes", "what": "shares", "item_kind": kind, "id": obj.pid,
                              "origin": live.origin_var.get()}
        if uid in before and uid not in after:
            ev.update(what="unshared", by_name=who, name=name_of(kind, obj))
        await notify(db, uid, ev)


# ---------------------------------------------------------------- the dialog


def _matches(u: User, q: str) -> bool:
    """A typed search hits a person by the start of a word of their name, the start of their
    address, or the whole address."""
    q = q.strip().lower()
    email = (u.email or "").lower()
    if email == q or email.startswith(q):
        return True
    return any(w.startswith(q) for w in (u.name or "").lower().split())


async def describe(db: AsyncSession, actor: User, kind: str, obj: Any, q: str | None = None) -> dict[str, Any]:
    """The people dialog: owner, people and groups with their levels, and who may be added.

    The candidates are the people who share a group with `actor`; a search `q` of at least
    SEARCH_MIN characters adds up to SEARCH_MAX others who match it."""
    access = _access(kind)
    lv = access.level(actor, obj)
    pol = await policy(db)
    rows = list((await db.execute(select(NoteShare).where(NoteShare.item_kind == kind, NoteShare.item_id == obj.id)
                                  .order_by(NoteShare.created_at, NoteShare.id))).scalars())
    uids = {int(r.principal) for r in rows if r.principal_kind == "user" and r.principal.isdigit()}
    uids |= {i for i in (obj.owner_user_id, obj.created_by) if i}
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(uids or {0})))).scalars()}
    can_share = acl.RANK[lv] >= acl.RANK["share"]
    mine = effective_groups(actor)
    people, groups_, taken = [], [], {obj.owner_user_id, actor.id}
    left: list[dict[str, Any]] = []
    shared_groups = {r.principal for r in rows if r.principal_kind == "group" and r.left_at is None
                     and r.level in acl.SHARE_LEVELS}
    for r in rows:
        if r.left_at is not None and r.principal_kind == "user" and r.principal.isdigit() and can_share:
            u = users.get(int(r.principal))
            via_group = r.level == "none"
            if u is not None and via_group and not set(effective_groups(u)) & shared_groups:
                # A leave whose share the owner stopped: nothing reaches them any more.
                continue
            if u is not None and u.id != actor.id:
                # People who removed it from their notes: the owner sees who left
                # and when. A new share from here brings them back.
                left.append({"kind": "user", **person(u), "left_at": r.left_at.isoformat(),
                             "via_group": via_group})
            continue
        if r.left_at is not None or r.level not in acl.SHARE_LEVELS:
            continue
        if r.principal_kind == "user":
            u = users.get(int(r.principal)) if r.principal.isdigit() else None
            if u is None:
                continue
            taken.add(u.id)
            people.append({"kind": "user", **person(u), "level": r.level, "disabled": bool(u.disabled),
                           "you": u.id == actor.id,
                           "outside_policy": u.id != actor.id and not allowed_by(pol, actor, u)})
        else:
            members = await _members(db, r.principal)
            groups_.append({"kind": "group", "name": r.principal, "level": r.level, "members": len(members),
                            "outside_policy": r.principal not in mine or pol == "nobody"})
    owner = users.get(obj.owner_user_id) if obj.owner_user_id else None
    out: dict[str, Any] = {
        "id": obj.pid, "kind": kind, "noun": noun(kind, obj), "name": name_of(kind, obj), "level": lv,
        "own": lv == "owner", "can_share": can_share, "policy": pol, "levels": list(acl.SHARE_LEVELS),
        "owner": ({"kind": "user", **person(owner)} if owner else {"kind": "group", "name": obj.owner_group}),
        "created_by": person(users[obj.created_by]) if obj.created_by in users else None,
        "people": people, "groups": groups_, "left": left, "max": MAX_PEOPLE,
        "my_groups": mine,
        "can_move_in": lv == "owner" and bool(mine),
        "can_move_out": obj.owner_group is not None and (
            (obj.created_by == actor.id and lv != "none") or is_admin(actor)),
        "can_leave": lv in acl.SHARE_LEVELS and not (obj.owner_group is not None and obj.owner_group in mine),
        "candidates": [], "group_candidates": [], "search_min": SEARCH_MIN,
        # The candidates who share a group with the viewer (a household): "Share with Cleo" in the
        # item's menu offers them first.
        "household": [],
    }
    if can_share and pol != "nobody":
        cands = (await db.execute(select(User).where(User.disabled.is_(False)).order_by(User.name, User.email))
                 ).scalars().all()
        ok = [u for u in cands if u.id not in taken and allowed_by(pol, actor, u)]
        near = [u for u in ok if set(effective_groups(u)) & set(mine)]
        found: list[User] = []
        if q is not None and len(q.strip()) >= SEARCH_MIN and _search_allowed(actor.id):
            found = [u for u in ok if u not in near and _matches(u, q)][:SEARCH_MAX]
        out["candidates"] = [person(u) for u in near + found]
        out["household"] = [u.id for u in near]
        have = {g["name"] for g in groups_}
        out["group_candidates"] = [g for g in mine if g not in have and g != obj.owner_group]
    return out


async def _members(db: AsyncSession, group: str) -> list[User]:
    users = (await db.execute(select(User).where(User.disabled.is_(False)))).scalars().all()
    return [u for u in users if group in effective_groups(u)]


# ---------------------------------------------------------------- share, change, stop


def parse_principal(raw: str) -> tuple[str, str]:
    """`12` (a user id), `group:<name>` or `me`."""
    raw = str(raw or "").strip()
    if raw == "me":
        return "me", ""
    if raw.startswith("group:") and raw[6:].strip():
        return "group", raw[6:].strip()[:200]
    if raw.isdigit():
        return "user", raw
    raise HTTPException(422, "The person is a user id, group:<name> or me")


async def _visible(db: AsyncSession, actor: User, kind: str, obj: Any, other: User, email: str | None) -> bool:
    """Whether `actor` may name `other` by id here: an admin, the owner or the actor
    themselves, a group-mate, someone already on the item (in any state), or the address the
    request gives is theirs."""
    if is_admin(actor) or other.id in (actor.id, obj.owner_user_id):
        return True
    if set(effective_groups(other)) & set(effective_groups(actor)):
        return True
    if email and email.strip().lower() == (other.email or "").lower():
        return True
    row = (await db.execute(select(NoteShare.id).where(
        NoteShare.item_kind == kind, NoteShare.item_id == obj.id, NoteShare.principal_kind == "user",
        NoteShare.principal == str(other.id)))).first()
    return row is not None


async def set_share(db: AsyncSession, actor: User, kind: str, obj: Any, principal_kind: str, principal: str,
                    level: str | None, *, email: str | None = None) -> dict[str, Any]:
    """ACT note.share, note.unshare, table.share, table.unshare: share at a level, change it, or stop
    (level None). The caller commits. A person is named by user id; one the actor may not see
    (`_visible`) is answered as if there were no such account."""
    access = _access(kind)
    lv = access.level(actor, obj)
    what = noun(kind, obj)
    if level is not None and level not in acl.SHARE_LEVELS:
        raise HTTPException(422, "The level is view, edit or share.")
    if acl.RANK[lv] < acl.RANK["share"]:
        owner = await _owner_name(db, obj)
        raise HTTPException(403, f"{owner} has not let you share “{name_of(kind, obj)}”.")
    pol = await policy(db)
    if level is not None and pol == "nobody":
        raise HTTPException(403, "Sharing is turned off on this server. You can still stop sharing.")
    label = principal
    if principal_kind == "user":
        other = await db.get(User, int(principal))
        if other is None or not await _visible(db, actor, kind, obj, other, email):
            raise HTTPException(404, "That person is not on this server.")
        if other.id == obj.owner_user_id:
            raise HTTPException(400, f"That person owns this {what}.")
        if other.id == actor.id:
            raise HTTPException(400, "Only the owner can change your access.")
        label = person(other)["name"]
        if level is not None and (other.disabled or not allowed_by(pol, actor, other)):
            raise HTTPException(403, f"You cannot share with {label} on this server.")
    else:
        if level is not None and principal not in effective_groups(actor):
            raise HTTPException(403, "You can share only with groups you are in.")
        if principal == obj.owner_group:
            raise HTTPException(400, f"This {what} belongs to that group already.")
    row = (await db.execute(select(NoteShare).where(
        NoteShare.item_kind == kind, NoteShare.item_id == obj.id, NoteShare.principal_kind == principal_kind,
        NoteShare.principal == principal).with_for_update())).scalar_one_or_none()
    active = row is not None and row.left_at is None and row.level in acl.SHARE_LEVELS
    before = await _audience(db, kind, obj)
    if level is None:
        if not active:
            if row is not None and row.level in acl.SHARE_LEVELS:
                # Someone who left: the leave stays, so a group share keeps it hidden from them,
                # but the level goes, so "Add back" cannot restore it.
                row.level = "none"
                await db.flush()
                await db.refresh(obj, ["share_acl"])
                if principal_kind == "user":
                    await share_notices.drop(db, NOTICES, int(principal), path_of(kind, obj))
                log.info("%s %s: stopped the left share of %s %s by user %s", kind, obj.pid, principal_kind,
                         principal, actor.id)
            return await describe(db, actor, kind, obj)
        await db.delete(row)
        if principal_kind == "user":
            await share_notices.drop(db, NOTICES, int(principal), path_of(kind, obj))
    else:
        if not active:
            n = len([r for r in acl.acl_of(obj) if r[2] in acl.SHARE_LEVELS and not r[3]])
            if n >= MAX_PEOPLE:
                raise HTTPException(400, f"A {what} can be shared with at most {MAX_PEOPLE} people and groups.")
        if row is None:
            row = NoteShare(item_kind=kind, item_id=obj.id, principal_kind=principal_kind, principal=principal,
                            level=level, created_by=actor.id)
            db.add(row)
        else:
            row.level, row.left_at = level, None
            if not active:
                row.created_by = actor.id
    await db.flush()
    await db.refresh(obj, ["share_acl"])
    if level is not None and not active:
        await _notices(db, kind, obj, actor, principal_kind, principal, before)
    await _tell(db, kind, obj, before, actor)
    log.info("%s %s: %s %s %s by user %s", kind, obj.pid, principal_kind, principal, level or "removed", actor.id)
    return await describe(db, actor, kind, obj)


async def _owner_name(db: AsyncSession, obj: Any) -> str:
    if obj.owner_user_id:
        u = await db.get(User, obj.owner_user_id)
        return person(u)["name"] if u else "The owner"
    return f"The {obj.owner_group} group"


async def _notices(db: AsyncSession, kind: str, obj: Any, actor: User, principal_kind: str, principal: str,
                   before: set[int]) -> None:
    """A new share: a notice for each person who did not see the item before."""
    from mailr.core.events import notify

    if principal_kind == "user":
        targets = [int(principal)]
    else:
        targets = [u.id for u in await _members(db, principal)]
    name = name_of(kind, obj)
    by = person(actor)["name"]
    note = {"path": path_of(kind, obj), "kind": kind, "id": obj.pid, "name": name, "noun": noun(kind, obj),
            "by": by, "at": datetime.now(UTC).isoformat()}
    for uid in targets:
        if uid in before or uid == actor.id or uid == obj.owner_user_id:
            continue
        u = await db.get(User, uid)
        if u is None or u.disabled or _access(kind).level(u, obj) == "none":
            continue
        await share_notices.add(db, NOTICES, uid, note)
        from mailr import i18n

        await notify(db, uid, {"type": "notification", "kind": "notes_share", "item_kind": kind, "id": obj.pid,
                               "title": i18n.t("notify.shared", i18n.user_language(u), by=by, name=name)})


# ---------------------------------------------------------------- leave, add back


async def leave(db: AsyncSession, user: User, kind: str, obj: Any) -> dict[str, Any]:
    """ACT note.leave, table.leave: the item leaves this sharee's own notes (not anyone else's)."""
    from mailr.notes.actions import undo

    access = _access(kind)
    lv = access.level(user, obj)
    what = noun(kind, obj)
    if lv == "owner":
        raise HTTPException(400, f"You own this {what}. Stop sharing it, or move it to Trash.")
    if obj.owner_group is not None and obj.owner_group in effective_groups(user):
        raise HTTPException(403, f"This {what} belongs to the {obj.owner_group} group. "
                                 "It leaves your notes when you leave the group.")
    if lv == "none":
        raise ApiError(404, f"common.{kind}_not_found", fallback=f"{kind} not found")
    before = await _audience(db, kind, obj)
    row = (await db.execute(select(NoteShare).where(
        NoteShare.item_kind == kind, NoteShare.item_id == obj.id, NoteShare.principal_kind == "user",
        NoteShare.principal == str(user.id)).with_for_update())).scalar_one_or_none()
    now = datetime.now(UTC)
    if row is None:
        db.add(NoteShare(item_kind=kind, item_id=obj.id, principal_kind="user", principal=str(user.id), level="none",
                         created_by=user.id, left_at=now))
    else:
        row.left_at = now
    await share_notices.drop(db, NOTICES, user.id, path_of(kind, obj))
    await _tell(db, kind, obj, before, user)
    return {"ok": True, "undo": undo("POST", f"/{kind}s/{obj.pid}/shares/me", None, f"Removed “{name_of(kind, obj)}”")}


async def rejoin(db: AsyncSession, user: User, kind: str, pid: str) -> dict[str, Any]:
    """The undo of a leave: the share comes back as it was (404 when it was stopped meanwhile)."""
    obj = (await db.execute(select(_model(kind)).where(_model(kind).pid == str(pid)))).scalar_one_or_none()
    row = None
    if obj is not None:
        row = (await db.execute(select(NoteShare).where(
            NoteShare.item_kind == kind, NoteShare.item_id == obj.id, NoteShare.principal_kind == "user",
            NoteShare.principal == str(user.id), NoteShare.left_at.is_not(None)).with_for_update())).scalar_one_or_none()
    if row is None:
        raise ApiError(404, f"common.{kind}_not_found", fallback=f"{kind} not found")
    before = await _audience(db, kind, obj)
    if row.level in acl.SHARE_LEVELS:
        row.left_at = None
    else:
        await db.delete(row)
    await _tell(db, kind, obj, before, user)
    if _access(kind).level(user, obj) == "none":
        raise ApiError(404, f"common.{kind}_not_found", fallback=f"{kind} not found")
    return {"ok": True, "id": obj.pid, "undo": None}


# ---------------------------------------------------------------- group-owned


async def move_to_group(db: AsyncSession, actor: User, kind: str, obj: Any, group: str | None) -> dict[str, Any]:
    """ACT note.move_group, table.move_group: the owner moves the item into one of their groups;
    its creator or an admin moves it out again, back to its creator when they are still here."""
    access = _access(kind)
    what = noun(kind, obj)
    before = await _audience(db, kind, obj)
    if group is not None:
        group = group.strip()[:200]
        if await policy(db) == "nobody":
            raise HTTPException(403, "Sharing is turned off on this server.")
        if access.level(actor, obj) != "owner":
            raise HTTPException(403, f"Only the owner moves this {what} to a group.")
        if group not in effective_groups(actor):
            raise HTTPException(403, "You can move it only to a group you are in.")
        if obj.created_by is None:
            obj.created_by = actor.id
        obj.owner_user_id, obj.owner_group = None, group
        # The group's own row would be a second grant to the same people.
        await db.execute(delete(NoteShare).where(NoteShare.item_kind == kind, NoteShare.item_id == obj.id,
                                                 NoteShare.principal_kind == "group", NoteShare.principal == group))
    else:
        if obj.owner_group is None:
            raise HTTPException(400, f"This {what} does not belong to a group.")
        creator = await db.get(User, obj.created_by) if obj.created_by else None
        if not ((creator is not None and creator.id == actor.id and access.level(actor, obj) != "none")
                or is_admin(actor)):
            raise HTTPException(403, f"Only the person who made this {what} or an administrator moves it out of "
                                     "the group.")
        owner = creator if creator is not None and not creator.disabled else actor
        obj.owner_group, obj.owner_user_id = None, owner.id
        await db.execute(delete(NoteShare).where(NoteShare.item_kind == kind, NoteShare.item_id == obj.id,
                                                 NoteShare.principal_kind == "user",
                                                 NoteShare.principal == str(owner.id)))
        st = await db.get(NoteViewerState, (owner.id, kind, obj.id))
        if st is None:
            db.add(NoteViewerState(user_id=owner.id, item_kind=kind, item_id=obj.id, labels=[]))
    await _tell(db, kind, obj, before, actor)
    return await describe(db, actor, kind, obj)


# ---------------------------------------------------------------- notices


async def notices(db: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    return await share_notices.listed(db, NOTICES, user_id)


async def dismiss(db: AsyncSession, user_id: int) -> None:
    await share_notices.drop(db, NOTICES, user_id, None)


# ---------------------------------------------------------------- cleanup


async def check_group_create(db: AsyncSession, user: User, group: str | None) -> None:
    """A new note or table in a group is shared with the group: the user must be in it, and the
    instance policy must allow sharing."""
    if group is None:
        return
    if group not in effective_groups(user):
        raise HTTPException(403, "You can make notes only in groups you are in.")
    if await policy(db) == "nobody":
        raise HTTPException(403, "Sharing is turned off on this server.")


async def drop_item(db: AsyncSession, kind: str, item_id: int) -> None:
    """An item deleted forever: its shares go with it."""
    await db.execute(delete(NoteShare).where(NoteShare.item_kind == kind, NoteShare.item_id == item_id))


async def user_removed(db: AsyncSession, user: User) -> None:
    """Before a user is deleted: their shares, and the shares and viewer state of what they owned
    (their notes and tables go with the account)."""
    from mailr.tables.models import DataTable

    await db.execute(delete(NoteShare).where(NoteShare.principal_kind == "user", NoteShare.principal == str(user.id)))
    for kind, model in (("note", Note), ("table", DataTable)):
        ids = select(model.id).where(model.owner_user_id == user.id)
        await db.execute(delete(NoteShare).where(NoteShare.item_kind == kind, NoteShare.item_id.in_(ids)))
        await db.execute(delete(NoteViewerState).where(NoteViewerState.item_kind == kind,
                                                       NoteViewerState.item_id.in_(ids)))
