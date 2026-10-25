"""The assistant's People tools: ranked people lookups and contact groups.

`find_people` ranks by strength (recent two-way correspondents first), the same order as the
recipient fields. `list_groups` lists the user's contact groups with their members. A group can
stand in a recipient list as "group:<name>" (create_draft, update_draft, create_event,
update_event); it is expanded to its members first, and every member then passes the send gate
or the invitation gate like any address, so a member never written to still asks.

Details learned from local-only mail or a local-only address book reach the model only when the
chat runs on a local model (tools.local_ok) and the caller may read local-only data.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from mailr.agent.tools import ToolCtx, ToolError, ToolResult, _obj, _u, local_ok, tool


async def may_see_local(ctx: ToolCtx) -> bool:
    from mailr.api.org_routes import include_local

    return include_local(ctx.p) and await local_ok(ctx)


def _brief(d: dict[str, Any]) -> dict[str, Any]:
    keep = ("email", "name", "organization", "title", "strength", "two_way", "sent_count", "received_count",
            "last_seen", "last_sent", "phones", "addresses", "birthday")
    out = {k: d.get(k) for k in keep if d.get(k) not in (None, "", [], {})}
    for k in ("name", "organization", "title"):
        if k in out:
            out[k] = _u(out[k])
    return out


# ACT person.find @assistant
@tool("find_people",
      "Find people you correspond with, best match first: recent two-way correspondents rank highest. Gives "
      "name, address, organisation, title and phone numbers when known. With no query it lists your strongest "
      "contacts. Use get_person for one person's history.",
      _obj({"query": {"type": "string", "description": "part of a name, address or organisation"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50}}))
async def find_people(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.api.org_routes import people as route
    from mailr.api.org_routes import person_out
    from mailr.models import Person

    limit = max(1, min(int(args.get("limit") or 10), 50))
    rows = await route(q=(args.get("query") or "").strip(), limit=limit, p=ctx.p, db=ctx.db)
    local = await may_see_local(ctx)
    sensitive = False
    if local and rows:
        # Re-read with local-only details; tell the loop when one is included.
        xs = {x.email: x for x in (await ctx.db.execute(select(Person).where(
            Person.user_id == ctx.p.user_id, Person.email.in_([r["email"] for r in rows])))).scalars()}
        for i, r in enumerate(rows):
            x = xs.get(r["email"])
            if x is not None:
                full = person_out(x, True)
                sensitive = sensitive or full != person_out(x, False)
                rows[i] = full
    elif rows:
        xs = {x.email: x for x in (await ctx.db.execute(select(Person).where(
            Person.user_id == ctx.p.user_id, Person.email.in_([r["email"] for r in rows])))).scalars()}
        rows = [person_out(xs[r["email"]], False) if r["email"] in xs else r for r in rows]
    return ToolResult({"items": [_brief(r) for r in rows]}, sensitive=sensitive)


# ACT group.list @assistant
@tool("list_groups",
      "List your contact groups (made by you, accepted from suggestions, or from your address book) with their "
      "members. To write to or invite a group, put \"group:<group name>\" among the recipients or guests; it is "
      "expanded to the members, and anyone you have never written to still needs the user's approval.",
      _obj({"query": {"type": "string", "description": "part of a group name"}}))
async def list_groups(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.contacts import groups as gm

    gs = await gm.list_groups(ctx.db, ctx.p, q=(args.get("query") or "").strip(), limit=100)
    if not await may_see_local(ctx):
        hidden = await _local_book_connectors(ctx)
        if hidden:
            gs = [g for g in gs if not (g["source"] == "carddav" and g.get("connector_id") in hidden)]
    items = [{"id": g["id"], "name": _u(g["name"]), "source": g["source"], "count": g["count"],
              "members": [{"email": m["email"], **({"name": _u(m["name"])} if m["name"] else {})} for m in g["members"]]}
             for g in gs]
    return ToolResult({"items": items})


async def _local_book_connectors(ctx: ToolCtx) -> set[int]:
    from mailr.models import Connector

    return set((await ctx.db.execute(select(Connector.id).where(
        Connector.user_id == ctx.p.user_id, Connector.data_class == "local_only"))).scalars())


async def expand(ctx: ToolCtx, value: Any) -> tuple[Any, list[dict[str, Any]]]:
    # ACT group.expand @assistant
    """ACT group.expand: recipients or guests with "group:<name>" entries replaced by the members."""
    from mailr.contacts import groups as gm

    if value is None:
        return None, []
    items = [x.strip() for x in value.replace(";", ",").split(",")] if isinstance(value, str) else list(value)
    if not any((isinstance(x, str) and x.lower().startswith(gm.GROUP_PREFIX)) or (isinstance(x, dict) and "group" in x)
               for x in items):
        return value, []
    try:
        return await gm.expand_recipients(ctx.db, ctx.p, items)
    except LookupError as e:
        raise ToolError(f"{e}; list_groups shows the groups") from None


async def scrub_person(ctx: ToolCtx, res: dict[str, Any]) -> bool:
    """get_person: keep local-only details out of a hosted model; drop the user's pending
    suggestions (they are for the user to decide). Returns True when local-only details stay."""
    from mailr.api.org_routes import person_out
    from mailr.models import Person

    res.pop("suggestions", None)
    x = (await ctx.db.execute(select(Person).where(Person.user_id == ctx.p.user_id,
                                                   Person.email == res["email"]))).scalar_one_or_none()
    if x is None:
        return False
    local = await may_see_local(ctx)
    out = person_out(x, local)
    res.update({k: out[k] for k in ("name", "organization", "title", "birthday", "phones", "addresses", "sources",
                                    "strength", "two_way")})
    return local and person_out(x, True) != person_out(x, False)

