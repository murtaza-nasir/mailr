"""Contact groups: the user's own (manual), accepted suggestions (suggested) and
address-book groups (carddav, read-only). Recipient fields and the assistant expand a group to
its members; the members then go through the send gate one address at a time, so a member never
written to still asks.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.i18n import ApiError
from mailr.models import Connector, Message, Person, PersonGroup, PersonGroupMember

MAX_MEMBERS = 200
COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")
_EMAIL = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")


def valid_email(e: str) -> bool:
    return bool(_EMAIL.match(e or ""))


async def _hidden_connectors(db: AsyncSession, p) -> set[int]:
    """Address-book groups of a local-only calendar account stay hidden from tokens that are not local-only."""
    if p is None or p.token is None or p.token.local_only:
        return set()
    return set((await db.execute(select(Connector.id).where(
        Connector.user_id == p.user_id, Connector.data_class == "local_only"))).scalars())


async def visible_emails(db: AsyncSession, p, emails: list[str]) -> set[str]:
    """The members a caller may see. A restricted token does not see an address that appears
    only in mail it cannot read (another account, or local-only mail); addresses the user typed
    in that appear in no mail at all stay visible."""
    from mailr.core.visibility import is_restricted, visible_message_filter

    if not emails or p is None or not is_restricted(p):
        return set(emails)

    async def appearing(cond) -> set[str]:
        found = set((await db.execute(select(func.lower(Message.from_email)).where(
            Message.user_id == p.user_id, cond, func.lower(Message.from_email).in_(emails)).distinct())).scalars())
        for e in set(emails) - found:
            if (await db.execute(select(Message.id).where(
                    Message.user_id == p.user_id, cond,
                    Message.to.contains([{"email": e}]) | Message.cc.contains([{"email": e}])).limit(1))).first():
                found.add(e)
        return found

    anywhere = await appearing(true())
    if not anywhere:
        return set(emails)
    visible = await appearing(visible_message_filter(p))
    return {e for e in emails if e in visible or e not in anywhere}


async def members_of(db: AsyncSession, group_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Members by group, with the People name when there is one."""
    if not group_ids:
        return {}
    rows = (await db.execute(select(PersonGroupMember.group_id, PersonGroupMember.email, PersonGroupMember.name,
                                    PersonGroup.user_id)
                             .join(PersonGroup, PersonGroup.id == PersonGroupMember.group_id)
                             .where(PersonGroupMember.group_id.in_(group_ids))
                             .order_by(PersonGroupMember.email))).all()
    emails = list({r.email for r in rows})
    uid = rows[0].user_id if rows else None
    names = dict((await db.execute(select(Person.email, Person.name).where(
        Person.user_id == uid, Person.email.in_(emails)))).all()) if rows else {}
    out: dict[int, list[dict[str, Any]]] = {g: [] for g in group_ids}
    for r in rows:
        out[r.group_id].append({"email": r.email, "name": names.get(r.email) or r.name or ""})
    return out


def group_out(g: PersonGroup, members: list[dict[str, Any]] | None = None, *, with_members: bool = True) -> dict[str, Any]:
    out = {"id": g.id, "name": g.name, "colour": g.colour, "source": g.source, "state": g.state,
           "read_only": g.source == "carddav", "count": len(members or []), "connector_id": g.connector_id,
           "created_at": g.created_at.isoformat() if g.created_at else None}
    if g.state != "active":
        out.update({"name_hint": g.name_hint, "score": g.score, "evidence": g.evidence or {}})
    if with_members:
        out["members"] = members or []
    return out


async def list_groups(db: AsyncSession, p, *, q: str = "", state: str = "active", limit: int = 100) -> list[dict[str, Any]]:
    stmt = select(PersonGroup).where(PersonGroup.user_id == p.user_id, PersonGroup.state == state)
    hidden = await _hidden_connectors(db, p)
    if hidden:
        stmt = stmt.where((PersonGroup.connector_id.is_(None)) | (PersonGroup.connector_id.notin_(hidden)))
    if q:
        stmt = stmt.where(func.lower(PersonGroup.name).like(f"%{q.lower().strip()}%"))
    order = PersonGroup.score.desc() if state == "suggested" else func.lower(PersonGroup.name)
    groups = (await db.execute(stmt.order_by(order, PersonGroup.id).limit(min(limit, 500)))).scalars().all()
    mem = await members_of(db, [g.id for g in groups])
    out = []
    for g in groups:
        ms = mem.get(g.id, [])
        if p.token is not None:
            ok = await visible_emails(db, p, [m["email"] for m in ms])
            ms = [m for m in ms if m["email"] in ok]
        out.append(group_out(g, ms))
    return out


async def load(db: AsyncSession, p, gid: int, *, states: tuple[str, ...] = ("active",)) -> PersonGroup:
    g = await db.get(PersonGroup, gid)
    if g is None or g.user_id != p.user_id or g.state not in states:
        raise ApiError(404, "contacts.group_not_found")
    if g.connector_id and g.connector_id in await _hidden_connectors(db, p):
        raise ApiError(404, "contacts.group_not_found")
    return g


async def get_group(db: AsyncSession, p, gid: int) -> dict[str, Any]:
    g = await load(db, p, gid)
    ms = (await members_of(db, [g.id]))[g.id]
    if p.token is not None:
        ok = await visible_emails(db, p, [m["email"] for m in ms])
        ms = [m for m in ms if m["email"] in ok]
    return group_out(g, ms)


def _clean_members(members: list[Any]) -> list[dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for m in members or []:
        email, name = (m.get("email", ""), m.get("name", "")) if isinstance(m, dict) else (str(m), "")
        email = email.strip().lower()
        if not valid_email(email):
            raise HTTPException(400, f"not an email address: {email[:80]!r}")  # i18n: internal
        out.setdefault(email, {"email": email, "name": (name or "")[:300]})
    if len(out) > MAX_MEMBERS:
        raise ApiError(400, "contacts.group_max_members", max=MAX_MEMBERS)
    return list(out.values())


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ApiError(400, "contacts.group_name_required")
    return name[:200]


def _check_colour(colour: str | None) -> str | None:
    if colour in (None, ""):
        return None
    if not COLOUR.match(colour):
        raise HTTPException(400, "colour must be #rrggbb")  # i18n: internal
    return colour.lower()


def _writable(g: PersonGroup) -> None:
    if g.source == "carddav":
        raise ApiError(409, "contacts.group_from_address_book")


async def create(db: AsyncSession, p, name: str, members: list[Any], colour: str | None = None) -> PersonGroup:
    # ACT group.create @api
    """ACT group.create"""
    name = _check_name(name)
    g = PersonGroup(user_id=p.user_id, name=name, colour=_check_colour(colour), source="manual", state="active",
                    evidence={})
    db.add(g)
    await db.flush()
    for m in _clean_members(members):
        db.add(PersonGroupMember(group_id=g.id, email=m["email"], name=m["name"]))
    await db.flush()
    return g


async def update(db: AsyncSession, p, gid: int, *, name: str | None = None, colour: str | None = "",
                 add: list[Any] | None = None, remove: list[str] | None = None) -> PersonGroup:
    # ACT group.rename @api
    # ACT group.add_members @api
    # ACT group.remove_members @api
    """ACT group.rename, ACT group.add_members, ACT group.remove_members"""
    g = await load(db, p, gid)
    _writable(g)
    if name is not None:
        g.name = _check_name(name)
    if colour != "":
        g.colour = _check_colour(colour)
    if remove:
        await db.execute(delete(PersonGroupMember).where(PersonGroupMember.group_id == g.id,
                                                         PersonGroupMember.email.in_([e.lower() for e in remove])))
    if add:
        have = set((await db.execute(select(PersonGroupMember.email).where(PersonGroupMember.group_id == g.id))).scalars())
        fresh = [m for m in _clean_members(add) if m["email"] not in have]
        if len(have) + len(fresh) > MAX_MEMBERS:
            raise ApiError(400, "contacts.group_max_members", max=MAX_MEMBERS)
        for m in fresh:
            db.add(PersonGroupMember(group_id=g.id, email=m["email"], name=m["name"]))
    g.updated_at = datetime.now(UTC)
    await db.flush()
    return g


async def remove(db: AsyncSession, p, gid: int) -> None:
    # ACT group.delete @api
    """ACT group.delete. An accepted suggestion becomes dismissed, so it is not suggested again."""
    g = await load(db, p, gid)
    _writable(g)
    if g.source == "suggested":
        g.state = "dismissed"
        g.updated_at = datetime.now(UTC)
    else:
        await db.delete(g)
    await db.flush()


async def accept(db: AsyncSession, p, gid: int, name: str, members: list[str] | None = None) -> PersonGroup:
    # ACT group.accept_suggestion @api
    """ACT group.accept_suggestion: the user names it; it becomes an ordinary group."""
    g = await load(db, p, gid, states=("suggested",))
    g.name = _check_name(name)
    g.state = "active"
    g.updated_at = datetime.now(UTC)
    if members is not None:  # the user may leave some suggested members out
        keep = {e.lower() for e in members}
        await db.execute(delete(PersonGroupMember).where(PersonGroupMember.group_id == g.id,
                                                         PersonGroupMember.email.notin_(keep)))
    await db.flush()
    return g


async def dismiss(db: AsyncSession, p, gid: int) -> None:
    # ACT group.dismiss_suggestion @api
    """ACT group.dismiss_suggestion: kept as dismissed so the same set never returns."""
    g = await load(db, p, gid, states=("suggested",))
    g.state = "dismissed"
    g.updated_at = datetime.now(UTC)
    await db.flush()


# ---------------- expansion ----------------


async def find(db: AsyncSession, p, ref: str | int) -> PersonGroup | None:
    """A group by id or by name (case-insensitive; an exact name first, then a unique prefix)."""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        try:
            return await load(db, p, int(ref))
        except HTTPException:
            return None
    name = str(ref).strip().lower()
    if not name:
        return None
    hidden = await _hidden_connectors(db, p)
    rows = [g for g in (await db.execute(select(PersonGroup).where(
        PersonGroup.user_id == p.user_id, PersonGroup.state == "active"))).scalars()
            if not (g.connector_id and g.connector_id in hidden)]
    exact = [g for g in rows if g.name.lower() == name]
    if exact:
        return exact[0]
    pre = [g for g in rows if g.name.lower().startswith(name)]
    return pre[0] if len(pre) == 1 else None


async def expand(db: AsyncSession, p, ref: str | int) -> tuple[PersonGroup, list[dict[str, str]]]:
    g = await find(db, p, ref)
    if g is None:
        raise LookupError(f"no group named {ref!r}")
    ms = (await members_of(db, [g.id]))[g.id]
    if p.token is not None:
        ok = await visible_emails(db, p, [m["email"] for m in ms])
        ms = [m for m in ms if m["email"] in ok]
    return g, [{"email": m["email"], "name": m["name"]} for m in ms]


GROUP_PREFIX = "group:"


async def expand_recipients(db: AsyncSession, p, items: list[Any]) -> tuple[list[Any], list[dict[str, Any]]]:
    """Replace "group:<name or id>" entries (or {"group": ...}) by the group's members.
    Returns (items, [{"group", "id", "members"}]) so callers can say what was expanded."""
    out: list[Any] = []
    used: list[dict[str, Any]] = []
    for it in items or []:
        ref = None
        if isinstance(it, str) and it.strip().lower().startswith(GROUP_PREFIX):
            ref = it.strip()[len(GROUP_PREFIX):].strip()
        elif isinstance(it, dict) and it.get("group") is not None:
            ref = it["group"]
        if ref is None:
            out.append(it)
            continue
        g, ms = await expand(db, p, ref)
        used.append({"group": g.name, "id": g.id, "members": [m["email"] for m in ms]})
        out.extend({"email": m["email"], "name": m["name"]} for m in ms)
    return out, used
