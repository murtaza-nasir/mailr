"""REST routes for contact groups, detail suggestions and the address book.

Groups live under /groups (the /people/{email} route owns one path segment after /people).
The signed-in user is never gated. A token's group changes are permission class contacts.write
(ask by default; the built-in assistant may change groups without a click, as with labels).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.contacts import groups as gm
from mailr.contacts import signatures, suggest
from mailr.core import permissions
from mailr.core.audit import audit
from mailr.core.auth import Principal, scope
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import AddressBook, Connector, User

router = APIRouter()


class MemberIn(BaseModel):
    email: str
    name: str = ""


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    members: list[MemberIn | str] = []
    colour: str | None = None


class GroupPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    colour: str | None = ""
    add: list[MemberIn | str] = []
    remove: list[str] = []


class AcceptIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    members: list[str] | None = None


def _members(v: list[MemberIn | str]) -> list[Any]:
    return [m.model_dump() if isinstance(m, MemberIn) else m for m in v]


async def apply_op(db: AsyncSession, p, op: dict[str, Any]) -> dict[str, Any]:
    """One group change, for the routes and for an approved token request."""
    kind = op["op"]
    if kind == "create":
        g = await gm.create(db, p, op["name"], op.get("members") or [], op.get("colour"))
    elif kind == "update":
        g = await gm.update(db, p, int(op["id"]), name=op.get("name"), colour=op.get("colour", ""),
                            add=op.get("add") or [], remove=op.get("remove") or [])
    elif kind == "delete":
        await gm.remove(db, p, int(op["id"]))
        return {"ok": True, "id": int(op["id"])}
    elif kind == "accept":
        g = await gm.accept(db, p, int(op["id"]), op["name"], op.get("members"))
    elif kind == "dismiss":
        await gm.dismiss(db, p, int(op["id"]))
        return {"ok": True, "id": int(op["id"])}
    else:
        raise HTTPException(400, f"unknown group change {kind!r}")  # i18n: internal
    return await gm.get_group(db, p, g.id)


@permissions.executor("contacts.write")
async def _contacts_write(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    p = await permissions.requester(db, user, payload)
    return await apply_op(db, p, payload["change"])


async def _run(db: AsyncSession, p: Principal, change: dict[str, Any], title: str, lines: list[str]):
    if p.is_token:
        decision = await permissions.check(db, permissions.Actor.from_principal(p), "contacts.write",
                                           target=title, payload={"change": change},
                                           preview={"title": title, "kind": "people", "lines": lines})
        permissions.raise_for(decision)
        if isinstance(decision, permissions.Pending):
            await audit(db, p, f"groups.{change['op']}", title, {}, {"pending": decision.action.id})
            await db.commit()
            return JSONResponse(permissions.pending_response(decision.action), status_code=202)
    out = await apply_op(db, p, change)
    await audit(db, p, f"groups.{change['op']}", title)
    await db.commit()
    return out


# ACT group.list @api
@router.get("/groups", tags=["people"])
async def list_groups(q: str = "", state: str = "active", limit: Annotated[int, Query(ge=1, le=500)] = 100, p: Principal = Depends(scope("read")),
                      db: AsyncSession = Depends(get_db)):
    if state not in ("active", "suggested"):
        raise HTTPException(400, "state is active or suggested")  # i18n: internal
    if state == "suggested" and p.is_token:
        return []  # suggestions are for the user to decide
    return await gm.list_groups(db, p, q=q, state=state, limit=limit)


# ACT group.suggest @api
@router.post("/groups/suggestions/refresh", tags=["people"])
async def refresh_suggestions(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "contacts.suggest_user_only")
    res = await suggest.refresh(db, p.user_id)
    await db.commit()
    return {**res, "suggestions": await gm.list_groups(db, p, state="suggested")}


@router.get("/groups/{gid}", tags=["people"])
async def get_group(gid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    return await gm.get_group(db, p, gid)


# ACT group.create @api
@router.post("/groups", tags=["people"])
async def create_group(body: GroupIn, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    members = _members(body.members)
    return await _run(db, p, {"op": "create", "name": body.name, "members": members, "colour": body.colour},
                      f"Create the group “{body.name.strip()}”", [f"{len(members)} people"])


# ACT group.rename @api, ACT group.add_members @api, ACT group.remove_members @api
@router.patch("/groups/{gid}", tags=["people"])
async def update_group(gid: int, body: GroupPatch, p: Principal = Depends(scope("read")),
                       db: AsyncSession = Depends(get_db)):
    g = await gm.load(db, p, gid)
    add = _members(body.add)
    lines = ([f"Rename to “{body.name}”"] if body.name is not None and body.name != g.name else []) + \
        ([f"Add {', '.join(m['email'] if isinstance(m, dict) else m for m in add[:5])}"] if add else []) + \
        ([f"Remove {', '.join(body.remove[:5])}"] if body.remove else [])
    return await _run(db, p, {"op": "update", "id": gid, "name": body.name, "colour": body.colour, "add": add,
                              "remove": body.remove}, f"Change the group “{g.name}”", lines)


# ACT group.delete @api
@router.delete("/groups/{gid}", tags=["people"])
async def delete_group(gid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    g = await gm.load(db, p, gid)
    return await _run(db, p, {"op": "delete", "id": gid}, f"Delete the group “{g.name}”",
                      ["The people stay in People; only the group goes"])


# ACT group.accept_suggestion @api
@router.post("/groups/{gid}/accept", tags=["people"])
async def accept_suggestion(gid: int, body: AcceptIn, p: Principal = Depends(scope("read")),
                            db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "contacts.accept_user_only")
    return await _run(db, p, {"op": "accept", "id": gid, "name": body.name, "members": body.members}, "", [])


# ACT group.dismiss_suggestion @api
@router.post("/groups/{gid}/dismiss", tags=["people"])
async def dismiss_suggestion(gid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "contacts.dismiss_user_only")
    return await _run(db, p, {"op": "dismiss", "id": gid}, "", [])


# ---------------- detail suggestions ----------------


async def _decide(email: str, sid: int, accept: bool, p: Principal, db: AsyncSession):
    if p.is_token:
        raise ApiError(403, "contacts.suggestion_user_only")
    s = await signatures.decide(db, p.user_id, email, sid, accept)
    await audit(db, p, "people.accept_detail" if accept else "people.dismiss_detail", email, {"field": s.field})
    await db.commit()
    return signatures.suggestion_out(s)


# ACT person.accept_detail @api
@router.post("/people/{email}/suggestions/{sid}/accept", tags=["people"])
async def accept_detail(email: str, sid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    return await _decide(email, sid, True, p, db)


# ACT person.dismiss_detail @api
@router.post("/people/{email}/suggestions/{sid}/dismiss", tags=["people"])
async def dismiss_detail(email: str, sid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    return await _decide(email, sid, False, p, db)


# ---------------- address books ----------------


def _book_status(conn: Connector, books: list[AddressBook]) -> dict[str, Any]:
    st = (conn.state or {}).get("carddav") or {}
    return {"connector_id": conn.id, "name": conn.name, "url": conn.config.get("url"),
            "available": st.get("available"), "checked_at": st.get("checked_at"), "error": st.get("error") or "",
            "cards": st.get("cards", 0), "groups": st.get("groups", 0), "local_only": conn.data_class == "local_only",
            "books": [{"name": b.displayname, "synced_at": b.synced_at.isoformat() if b.synced_at else None,
                       "error": b.error} for b in books if b.connector_id == conn.id]}


@router.get("/address-books", tags=["people"])
async def address_books(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "contacts.settings_user_only")
    conns = (await db.execute(select(Connector).where(Connector.user_id == p.user_id, Connector.kind == "caldav"))).scalars().all()
    books = (await db.execute(select(AddressBook).where(AddressBook.user_id == p.user_id)
                              .order_by(AddressBook.displayname))).scalars().all()
    return [_book_status(c, books) for c in conns]


# ACT contacts.sync @api
@router.post("/address-books/sync", tags=["people"])
async def sync_address_books(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """Read the address books now (read-only: mailr never writes to them)."""
    from mailr.contacts import carddav

    if p.is_token:
        raise ApiError(403, "contacts.settings_user_only")
    ids = (await db.execute(select(Connector.id).where(Connector.user_id == p.user_id, Connector.kind == "caldav",
                                                       Connector.enabled.is_(True)))).scalars().all()
    await db.commit()
    for cid in ids:
        await carddav.sync_connector(cid)
    return await address_books(p=p, db=db)


# ---------------- places for the event editor ----------------


# ACT event.suggest_location @api
@router.get("/contacts/places", tags=["people"])
async def places(emails: str = "", q: str = "", limit: Annotated[int, Query(ge=1, le=200)] = 20, p: Principal = Depends(scope("read")),
                 db: AsyncSession = Depends(get_db)):
    """Postal addresses from People for a location field: the guests' first, then the strongest
    correspondents', narrowed by `q` (a part of the name or the address)."""
    from sqlalchemy import func, text

    from mailr.api.org_routes import include_local
    from mailr.core.people import rank_expr
    from mailr.models import Person

    guests = [e.strip().lower() for e in emails.split(",") if "@" in e][:100]
    stmt = select(Person).where(Person.user_id == p.user_id, Person.blocked.is_(False),
                                text("jsonb_array_length(people.addresses) > 0"))
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(func.lower(Person.name).like(like) | func.lower(Person.email).like(like)
                          | text("lower(people.addresses::text) LIKE :like").bindparams(like=like))
    first = Person.email.in_(guests) if guests else None
    order = ([first.desc()] if first is not None else []) + [rank_expr().desc(), Person.id]
    rows = (await db.execute(stmt.order_by(*order).limit(200))).scalars().all()
    loc = include_local(p)
    needle = q.strip().lower()
    out, seen = [], set()
    for x in rows:
        # A caller without local-only access sees (and matches on) only the fields it is shown:
        # a name from a local-only source is blank, local-only addresses are left out.
        name = x.name if loc or not ((x.provenance or {}).get("name") or {}).get("local_only") else ""
        for a in x.addresses or []:
            t = (a.get("text") or "").strip()
            if not t or t.lower() in seen or (not loc and a.get("local_only")):
                continue
            if needle and not loc and not any(needle in v.lower() for v in (x.email, name, t)):
                continue
            seen.add(t.lower())
            out.append({"email": x.email, "name": name, "text": t, "type": a.get("type") or "",
                        "source": a.get("source") or "", "guest": x.email in guests})
    return out[:max(1, min(limit, 50))]
