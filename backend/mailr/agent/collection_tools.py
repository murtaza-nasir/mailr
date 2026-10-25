"""Agent tools for calendars and task lists (manage_calendars, manage_lists) and the read-only
reminders list. Registered in agent/tools.py, which re-exports them."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from mailr.agent.calendar_common import (
    HIDDEN_NOTE,
    _names,
    _require_calendar,
    _run_gated,
    _split_local,
    cite_event,
    cite_task,
)
from mailr.agent.calendar_tools import (
    _res,
)
from mailr.agent.tools import (
    ToolCtx,
    ToolError,
    ToolResult,
    _obj,
    tool,
)

# ---------------- calendars and task lists


async def _collection(ctx: ToolCtx, ref: Any, want: str) -> Any:
    """A calendar or task list by URL, numeric id or name, among those the caller can see."""
    from mailr.calendar import mirror
    from mailr.calendar.dav import norm_href

    s = str(ref or "").strip()
    if not s:
        raise ToolError(f"name the {want}")
    cols = await mirror.collections_of(ctx.db, await mirror.visible_connector_ids(ctx.db, ctx.p.user_id, ctx.p))
    hits = [c for c in cols if str(c.id) == s or ("/" in s and norm_href(c.href).rstrip("/") == norm_href(s).rstrip("/"))]
    if not hits:
        hits = [c for c in cols if (c.displayname or "").strip().lower() == s.lower()]
    if len(hits) != 1:
        raise ToolError(f"{'several' if hits else 'no'} {want}s match {s!r}; use its URL from get_calendar_defaults")
    return hits[0]


_COLOR_RE = r"^(#[0-9A-Fa-f]{6}|)$"


def _check_color(v: Any) -> str | None:
    import re

    if v is None:
        return None
    if not re.match(_COLOR_RE, str(v)):
        raise ToolError("'color' must be #rrggbb (\"\" for the server's colour)")
    return str(v)


async def _manage_collection(ctx: ToolCtx, args: dict[str, Any], kind: str) -> ToolResult:
    """manage_lists and manage_calendars, with the calendar in the result by its label, not its URL
    (calendar/describe.py)."""
    res = await _manage_collection_run(ctx, args, kind)
    d = res.data
    if isinstance(d, dict) and d.get("url") and d.get("status") != "pending_approval":
        label = (await _names(ctx)).get(d["url"]) or d.get("name")
        res.data = {**{k: v for k, v in d.items() if k not in ("id", "url", "connector_id", "sync")}, "name": label}
    return res


async def _manage_collection_run(ctx: ToolCtx, args: dict[str, Any], kind: str) -> ToolResult:
    """manage_lists and manage_calendars: the executors of calendar/routes_collections.py, under
    tasks.lists (a collection that holds only tasks) or calendar.manage (any other)."""
    from mailr.calendar.routes_collections import _class_for, _name

    await _require_calendar(ctx)
    action = args.get("action")
    noun = "task list" if kind == "tasks" else "calendar"
    color = _check_color(args.get("color"))
    if action == "create":
        name = str(args.get("name") or "").strip()
        if not name:
            raise ToolError("'name' is required")
        ckind = "tasks" if kind == "tasks" else ("both" if args.get("tasks_too") else "events")
        payload = {"op": "create", "name": name[:200], "kind": ckind, **({"color": color} if color else {})}
        res = await _run_gated(ctx, _class_for(ckind), payload, target=name, cvals={},
                               preview={"title": f"Create {noun} “{name[:80]}”", "kind": "calendar",
                                        "lines": [name, f"holds {ckind}"]})
        return _res(res)
    coll = await _collection(ctx, args.get("list") if kind == "tasks" else args.get("calendar"), noun)
    cls = _class_for(coll)
    cvals = {"calendar": coll.href} if cls != "tasks.lists" else {"list": coll.href}
    label = (await _names(ctx)).get(coll.href) or _name(coll)
    if action in ("rename", "color"):
        payload: dict[str, Any] = {"op": "update", "calendar_id": coll.id}
        if action == "rename":
            name = str(args.get("name") or "").strip()
            if not name:
                raise ToolError("'name' is required")
            payload["name"] = name[:200]
        else:
            if color is None:
                raise ToolError("'color' is required")
            payload["color"] = color
        lines = [f"{k}: {v}" for k, v in payload.items() if k in ("name", "color")]
        return _res(await _run_gated(ctx, cls, payload, target=coll.href, cvals=cvals,
                                     ask_by_default="renaming a task list asks by default"
                                     if action == "rename" and cls == "tasks.lists" else None,
                                     preview={"title": f"Change “{label}”", "kind": "calendar", "lines": lines}))
    if action == "delete":
        move_to = None
        if args.get("move_to"):
            move_to = (await _collection(ctx, args["move_to"], "task list")).href
        payload = {"op": "delete", "calendar_id": coll.id, "confirm": _name(coll), "move_to": move_to}
        moved = (await _names(ctx)).get(move_to or "") or "another list"
        return _res(await _run_gated(ctx, cls, payload, target=coll.href, cvals=cvals,
                                     ask_by_default=f"deleting a {noun} asks by default",
                                     preview={"title": f"Delete “{label}”", "kind": "calendar", "danger": True,
                                              "lines": [label] + ([f"Tasks move to: {moved}"] if move_to else [])}))
    raise ToolError("action must be create, rename, color or delete")


# ACT list.create @assistant, ACT list.rename @assistant, ACT list.delete @assistant
@tool("manage_lists",
      "Create, rename, recolour or delete a task list. Renaming and deleting ask the user; `move_to` (a list) "
      "keeps the tasks of a deleted list.",
      _obj({"action": {"type": "string", "enum": ["create", "rename", "color", "delete"]},
            "list": {"type": "string", "description": "the list: URL, id or name (not for create)"},
            "name": {"type": "string"}, "color": {"type": "string"}, "move_to": {"type": "string"}}, ["action"]),
      scope="calendar")
async def manage_lists(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    return await _manage_collection(ctx, args, "tasks")


# ACT calendar.create @assistant, ACT calendar.rename @assistant, ACT calendar.color @assistant, ACT calendar.delete @assistant
@tool("manage_calendars",
      "Create, rename, recolour or delete a calendar (on the calendar server). Needs the user's approval.",
      _obj({"action": {"type": "string", "enum": ["create", "rename", "color", "delete"]},
            "calendar": {"type": "string", "description": "the calendar: URL, id or name (not for create)"},
            "name": {"type": "string"}, "color": {"type": "string"},
            "tasks_too": {"type": "boolean", "description": "create: the calendar also holds tasks"}}, ["action"]),
      scope="calendar")
async def manage_calendars(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    return await _manage_collection(ctx, args, "events")


# ---------------- reminders (read only: the assistant never snoozes or dismisses them)


@tool("list_reminders",
      "Reminders that fired and are not handled yet (`active`), and those due in the next `hours` (24) "
      "(`upcoming`). Read only: the user snoozes or dismisses reminders.",
      _obj({"hours": {"type": "integer", "minimum": 1, "maximum": 168}}))
async def list_reminders(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from datetime import timedelta

    from mailr.calendar import mirror, planner
    from mailr.models import CalObject, ReminderSchedule

    await _require_calendar(ctx)
    now = datetime.now(UTC)
    hours = max(1, min(int(args.get("hours") or 24), 168))
    active = await planner.active(ctx.db, ctx.p.user_id, ctx.p, now=now)
    rows = (await ctx.db.execute(select(ReminderSchedule, CalObject.connector_id).outerjoin(
        CalObject, CalObject.id == ReminderSchedule.object_id).where(
        ReminderSchedule.user_id == ctx.p.user_id, ReminderSchedule.fired_at.is_(None),
        ReminderSchedule.fire_at > now, ReminderSchedule.fire_at <= now + timedelta(hours=hours))
        .order_by(ReminderSchedule.fire_at).limit(100))).all()
    visible = set(await mirror.visible_connector_ids(ctx.db, ctx.p.user_id, ctx.p))
    keys = [a["key"] for a in active]
    lo = set((await ctx.db.execute(select(ReminderSchedule.key).where(
        ReminderSchedule.user_id == ctx.p.user_id, ReminderSchedule.key.in_(keys),
        ReminderSchedule.local_only.is_(True)))).scalars()) if keys else set()

    def item(kind: str, uid: str, occ: str, title: str, **kw: Any) -> dict[str, Any]:
        return {"kind": kind, "uid": uid, "occurrence": occ, "title": title, **kw,
                "cite": cite_event(uid, occ) if kind == "event" else cite_task(uid)}

    act = [{**item(a["kind"], a["uid"], a["occurrence"], a["title"], start=a["start"], fired_at=a["fired_at"],
                   missed=a["missed"], state=a["state"]), "local_only": a["key"] in lo} for a in active]
    up = [{**item("event" if r.kind == "e" else "task", r.uid, r.occurrence, r.title,
                  start=r.anchor_at.isoformat() if r.anchor_at else None, fire_at=r.fire_at.isoformat(),
                  minutes=r.minutes), "local_only": r.local_only}
          for r, conn in rows if conn in visible]
    act, h1, s1 = await _split_local(ctx, act)
    up, h2, s2 = await _split_local(ctx, up)
    out: dict[str, Any] = {"active": act, "upcoming": up, "items": act + up}
    if h1 or h2:
        out["note"] = HIDDEN_NOTE
    return ToolResult(out, sensitive=s1 or s2)
