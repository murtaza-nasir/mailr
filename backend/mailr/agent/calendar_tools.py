"""Agent tools for calendar events: defaults, search, read, find time, free/busy, create, update,
delete, cancel and RSVP. Registered in agent/tools.py, which re-exports them."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from mailr.agent.calendar_common import (
    _EVENT_PROPS,
    HIDDEN_NOTE,
    _cal,
    _check_query,
    _clock,
    _defaults,
    _ev_brief,
    _event_fields,
    _event_ref,
    _localize,
    _names,
    _record_result,
    _require_calendar,
    _run_event,
    _run_gated,
    _split_local,
    _task_brief,
    _task_default_text,
    _validation,
    _visible_collection,
    _when,
    _where,
    _zone,
    calendar_local_copy_reason,
    cite_event,
    stable_uids,
)
from mailr.agent.draft_tools import (
    _working_version,
)
from mailr.agent.tools import (
    PERSONAL,
    Tool,
    ToolCtx,
    ToolError,
    ToolResult,
    _int,
    _obj,
    tool,
)


@tool("get_calendar_defaults",
      "The user's default calendar and default task list (used when you do not name one), plus all "
      "calendars and task lists, each calendar's own `default_reminders`, and `event_reminders`: the "
      "default reminder a new event gets when create_event names none. `read_only` calendars cannot be "
      "written (never pick one for a new event); `shared` marks a group calendar (kind group, with its group) or "
      "a calendar another person shares with the user (kind user, with the owner and the level: view, edit or "
      "share). Sharing itself is done by the user in Calendar, never by you. Name a calendar or list by its `name` "
      "(in tool arguments too); `calendar_id` tells apart two with the same name. Never show an id to the user.",
      _obj({}))
async def get_calendar_defaults(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    cal = await _cal()
    if not await cal.has_calendar(ctx.db, ctx.p.user_id, p=ctx.p):
        return ToolResult({"connected": False})
    try:
        cals = [c for c in await cal.calendars(ctx.db, ctx.p.user_id, p=ctx.p) if "error" not in c]
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"calendar unavailable: {e}"[:200]) from None
    cals, hidden, sens = await _split_local(ctx, cals)
    from mailr.calendar.planner import event_defaults, settings_of

    keys = ("calendar_id", "color", "events", "tasks", "read_only", "hidden", "busy", "local_only",
            "default_reminders", "shared")
    d = event_defaults(ctx.p.user)
    names = await _names(ctx)
    dflt = await _defaults(ctx)
    out: dict[str, Any] = {"connected": True,
                           "defaults": {"calendar": names.get(dflt.get("calendar") or "") or None,
                                        "task_list": names.get(dflt.get("task_list") or "") or None},
                           "event_reminders": {
                               "timed": d["timed"], "all_day": d["all_day"], "timed_text": d["timed_text"],
                               "all_day_text": d["all_day_text"],
                               "note": "Written into a new event when create_event leaves out `reminders`; a "
                                       "calendar's own default_reminders come first. [] means none.",
                               "tasks": _task_default_text(settings_of(ctx.p.user))},
                           "calendars": [{"name": names.get(c.get("url") or "") or c.get("name"),
                                          **{k: c.get(k) for k in keys}} for c in cals]}
    if hidden:
        out["note"] = HIDDEN_NOTE
    return ToolResult(out, sensitive=sens)


_WHEN_ARGS = {
    "when": {"type": "string", "description": "a day or span in the user's zone: today, tomorrow, yesterday, tonight, "
                                              "this week, next week, this weekend, a weekday, or YYYY-MM-DD; "
                                              "use it instead of from/to"},
    "from": {"type": "string", "description": "YYYY-MM-DD (the whole day) or YYYY-MM-DDTHH:MM, in the user's zone"},
    "to": {"type": "string", "description": "YYYY-MM-DD (that day included) or YYYY-MM-DDTHH:MM, in the user's zone"},
}
_TIMES_NOTE = ("Times in results are in the user's zone with their UTC offset, and `when` says the weekday "
               "(\"Sun 4 Oct 2026, 23:00–23:30 CDT\"): use them as given, do not convert them.")


async def _occurrence_needed(ctx: ToolCtx, uid: str, occ: str | None, scope: str | None) -> None:
    """One occurrence (or this and following) of a repeating event needs `occurrence`. Without it the
    write changes the whole series, while the card would name the series' first date as "this
    occurrence" and would not say which occurrence changes."""
    if occ or scope not in ("this", "following"):
        return
    from mailr.calendar import mirror

    obj = await mirror.find_by_uid(ctx.db, ctx.p.user_id, uid, "event", ctx.p)
    if obj is not None and obj.recurring:
        raise ToolError(f"this event repeats: scope {scope!r} needs `occurrence` (the `occurrence` value of that "
                        "occurrence in list_calendar or search_events); use scope 'all' for the whole series")


async def _tool_range(ctx: ToolCtx, args: dict[str, Any], default_days: int = 8) -> tuple[Any, Any]:
    """The UTC range of a calendar tool's `when` / `from` / `to`, read in the user's zone."""
    from mailr.agent import when

    try:
        return when.local_range(args.get("from"), args.get("to"), await _zone(ctx), when=args.get("when"),
                                default_days=default_days)
    except when.RangeError as e:
        raise ToolError(str(e)) from None


@tool("search_events",
      "List or search calendar events in a range: `when` (\"tomorrow\", \"next week\", a weekday) or `from`/`to` "
      "(default: today and the next 7 days). `text` takes words and the operators of search_calendar. Each item "
      "has `cite`. " + _TIMES_NOTE,
      _obj({**_WHEN_ARGS, "text": {"type": "string"},
            "calendar": {"type": "string", "description": "a calendar's name or id; default all"}}))
async def search_events(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.calendar.routes import _my_addresses, _with_my_status

    cal = await _cal()
    if not await cal.has_calendar(ctx.db, ctx.p.user_id, p=ctx.p):
        return ToolResult({"connected": False, "items": []})
    start, end = await _tool_range(ctx, args)
    await _check_query(ctx, args.get("text"))
    cals = args.get("calendars") or ([args["calendar"]] if args.get("calendar") else None)
    try:
        if args.get("text"):
            events = await cal.search_events(ctx.db, ctx.p.user_id, start, end, text=args.get("text"),
                                             calendar=cals[0] if cals and len(cals) == 1 else None, p=ctx.p)
            if cals and len(cals) > 1:
                events = [e for e in events if e.get("calendar") in cals]
        else:
            events = await cal.agenda(ctx.db, ctx.p.user_id, start, end, calendars=cals, p=ctx.p)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"calendar unavailable: {e}"[:200]) from None
    events = _with_my_status(events, await _my_addresses(ctx.db, ctx.p))
    events, hidden, sens = await _split_local(ctx, events)
    from mailr.agent import when
    from mailr.calendar import tz as tzmod

    zone = await _zone(ctx)
    out: dict[str, Any] = {"connected": True, "from": when.local_iso(start.isoformat(), zone),
                           "to": when.local_iso(end.isoformat(), zone),
                           "range": when.range_text(start, end, zone, _clock(ctx)),
                           "tz": tzmod.zone_name(zone),
                           "items": [_ev_brief(e, zone=zone, names=await _names(ctx), clock=_clock(ctx))
                                     for e in events[:100]]}
    if hidden:
        out["note"] = HIDDEN_NOTE
    return ToolResult(out, sensitive=sens)


@tool("list_calendar", "List calendar events in a range: `when` (\"tomorrow\", \"tonight\", \"next week\", a "
      "weekday) or `from`/`to` (default: today and the next 7 days). `calendars` limits to some calendars, `q` "
      "filters like search_calendar. Items carry `occurrence` and `cite`. " + _TIMES_NOTE,
      _obj({**_WHEN_ARGS, "q": {"type": "string"},
            "calendars": {"type": "array", "items": {"type": "string"}}}))
async def list_calendar(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    await _check_query(ctx, args.get("q"), "q")
    res = await search_events(ctx, {"when": args.get("when"), "from": args.get("from"), "to": args.get("to"),
                                    "text": args.get("q"), "calendars": args.get("calendars")})
    if isinstance(res.data, dict) and "items" in res.data:
        res.data["events"] = res.data["items"]
    return res


@tool("get_event", "One calendar event by uid (default: the event focused on screen), with its full "
      "description, guests, reminders and recurrence.",
      _obj({"uid": {"type": "string"}}))
async def get_event(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.calendar.routes import _my_addresses, _with_my_status

    cal = await _require_calendar(ctx)
    uid, _ = await _event_ref(ctx, args)
    try:
        got = await cal.get_event(ctx.db, ctx.p.user_id, uid, p=ctx.p)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"calendar unavailable: {e}"[:200]) from None
    if not got:
        raise ToolError("event not found")
    ev = _with_my_status([got[0]], await _my_addresses(ctx.db, ctx.p))[0]
    kept, hidden, sens = await _split_local(ctx, [ev])
    if hidden:
        raise ToolError("this event is in a calendar that is hidden from this model (a local-only account)")
    ev = dict(kept[0])
    _localize(ev, await _zone(ctx), "start", "end", bool(ev.get("all_day")), "when", _clock(ctx))
    ev["cite"] = cite_event(uid, ev.get("occurrence"))
    return ToolResult(ev, sensitive=sens)


# ACT event.search @assistant, ACT task.search @assistant
@tool("search_calendar",
      "Search events and tasks: words plus operators with: cal: list: tag: (or @tag) after: before: (ISO, "
      "today, tomorrow, a weekday) due:<date is:recurring is:invite is:done is:overdue has:guests has:video "
      "has:email has:reminder loc: pri:high; '-' negates. An event item is its next occurrence. Items have `cite`. "
      + _TIMES_NOTE,
      _obj({"q": {"type": "string"}, "kind": {"type": "string", "enum": ["event", "task", "all"]}, **_WHEN_ARGS,
            "calendars": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}))
async def search_calendar(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.calendar.routes_collections import calendar_search

    cal = await _cal()
    if not await cal.has_calendar(ctx.db, ctx.p.user_id, p=ctx.p):
        return ToolResult({"connected": False, "items": []})
    kind = args.get("kind") or "all"
    if kind not in ("event", "task", "all"):
        raise ToolError("kind must be event, task or all")
    await _check_query(ctx, args.get("q"), "q")
    frm = to = None
    if args.get("from") or args.get("to") or args.get("when"):
        start, end = await _tool_range(ctx, args, default_days=366)
        frm, to = start.isoformat(), end.isoformat()
    zone = await _zone(ctx)
    out = await calendar_search(q=str(args.get("q") or ""), kind=kind, frm=frm, to=to,
                                calendar=args.get("calendars") or None, limit=max(1, min(int(args.get("limit") or 25), 100)),
                                cursor=None, p=ctx.p, db=ctx.db)
    items, hidden, sens = await _split_local(ctx, out["items"])
    names = await _names(ctx)
    data: dict[str, Any] = {"connected": True, "items": [
        {"kind": i["kind"], **(_ev_brief(i, zone=zone, names=names, clock=_clock(ctx)) if i["kind"] == "event"
                               else _task_brief(i, zone=zone, names=names, clock=_clock(ctx)))}
        for i in items]}
    if hidden:
        data["note"] = HIDDEN_NOTE
    return ToolResult(data, sensitive=sens)


# ACT event.find_time @assistant
@tool("find_time",
      "Free slots in the user's own calendars inside working hours, best first. `duration` in minutes "
      "(30); `from`/`to` ISO (default the next 7 days); `working_hours` {start: \"09:00\", end: \"17:00\", "
      "days: [1..5]} (default the user's).",
      _obj({"duration": {"type": "integer", "minimum": 5, "maximum": 1440}, "from": {"type": "string"},
            "to": {"type": "string"}, "timezone": {"type": "string"},
            "working_hours": {"type": "object"}, "buffer_minutes": {"type": "integer", "minimum": 0, "maximum": 240},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            "calendars": {"type": "array", "items": {"type": "string"}}}))
async def find_time(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from pydantic import ValidationError

    from mailr.calendar.routes_collections import FindTimeIn
    from mailr.calendar.routes_collections import find_time as find_time_route

    await _require_calendar(ctx)
    body = {"duration": args.get("duration") or 30, "from": args.get("from"), "to": args.get("to"),
            "tz": args.get("timezone"), "working_hours": args.get("working_hours"),
            "buffer_minutes": args.get("buffer_minutes") or 0, "limit": args.get("limit") or 5,
            "calendar": args.get("calendars")}
    try:
        b = FindTimeIn.model_validate({k: v for k, v in body.items() if v is not None})
    except ValidationError as e:
        raise _validation(e) from None
    return ToolResult(await find_time_route(b, p=ctx.p, db=ctx.db))


@tool("get_free_busy",
      "The user's busy times (merged) in a range: `when` or `from`/`to` (default the next 7 days): `busy`, "
      "`tentative` and busy all-day days, in the user's zone with their offset. Free/busy only: no titles.",
      _obj({**_WHEN_ARGS, "calendars": {"type": "array", "items": {"type": "string"}}}))
async def get_free_busy(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.agent import when
    from mailr.calendar.routes_collections import calendar_freebusy

    await _require_calendar(ctx)
    start, end = await _tool_range(ctx, args, default_days=7)
    res = await calendar_freebusy(frm=start.isoformat(), to=end.isoformat(),
                                  calendar=args.get("calendars") or None, p=ctx.p, db=ctx.db)
    zone = await _zone(ctx)
    res["from"], res["to"] = when.local_iso(res["from"], zone), when.local_iso(res["to"], zone)
    for k in ("busy", "tentative"):
        res[k] = [{"start": when.local_iso(i["start"], zone), "end": when.local_iso(i["end"], zone),
                   "when": when.describe(i["start"], i["end"], False, zone, _clock(ctx))} for i in res.get(k) or []]
    return ToolResult(res)


# ACT event.create @assistant, ACT event.invite @assistant (attendees), ACT mail.to_event @assistant (with the email's conversation_id)
_REMINDERS_CREATE = ("minutes before the start. Leave out to use the user's default reminder{now}; pass [] "
                     "only when the user wants no reminder: the event is then marked silent, so not even the "
                     "default fires; pass minutes to override when the user asks for a different reminder")
_CREATE_EVENT_DESC = (
    "Create a calendar event (in the default calendar unless `calendar` is given). A date alone makes an "
    "all-day event. Guests in `attendees` get invitations (that needs the user's approval). Link it to the "
    "email it came from with conversation_id. Reminders: leave `reminders` out unless the user asks for a "
    "specific reminder or none; the default{now} is then written into the event, and the result's "
    "`reminders_text` says which, so mention it when you confirm. May need the user's approval.")


def _now_defaults(user: Any) -> str:
    from mailr.calendar.planner import event_defaults

    d = event_defaults(user)
    return (f" (currently: timed events {d['timed_text']}; all-day events {d['all_day_text']}; a calendar's "
            "own default reminders come first)")


def _create_event_personal(t: Tool, user: Any) -> tuple[str, dict[str, Any]]:
    now = _now_defaults(user)
    params = {**t.parameters, "properties": {**t.parameters["properties"], "reminders": {
        **t.parameters["properties"]["reminders"], "description": _REMINDERS_CREATE.format(now=now)}}}
    return _CREATE_EVENT_DESC.format(now=now), params


async def _errand_invite_conversation(ctx: ToolCtx, args: dict[str, Any], summary: str) -> int | None:
    """An errand on an email (a standing order, a forwarded note) that creates the event the email
    invites to, without naming the email: the event is linked to the email anyway, so it takes the
    invitation's identity and is never made twice (calendar/from_mail.py). Only for the same title or the same start."""
    cc = ctx.chat_context or {}
    cid = cc.get("conversation_id")
    if not cc.get("errand") or not cid or args.get("attendees"):
        return None
    from mailr.calendar.from_mail import invite_in_conversation, same_title

    try:
        inv = await invite_in_conversation(ctx.db, ctx.p, int(cid))
    except Exception:  # noqa: BLE001 - no invitation to match: an ordinary event
        return None
    if inv is None:
        return None
    info = inv[2]
    start = str(args.get("start") or "")
    if same_title(info.get("summary"), summary) or (start and str(info.get("start") or "").startswith(start[:16])):
        return int(cid)
    return None


@tool("create_event", _CREATE_EVENT_DESC.format(now=""),
      _obj({**_EVENT_PROPS, "recurrence": {"type": "string", "description": "same as rrule"},
            "reminders": {"type": "array", "items": {"type": "integer"},
                          "description": _REMINDERS_CREATE.format(now="")},
            "conversation_id": {"type": "integer"}}, ["summary", "start"]), scope="calendar")
async def create_event(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from pydantic import ValidationError

    from mailr.calendar.planner import offset_label
    from mailr.calendar.routes import (
        EventIn,
        _check_rrule,
        _check_tz,
        _check_when,
        _ev_preview,
        _is_all_day,
        _own_conversation,
        create_payload,
        initial_reminders,
    )

    await _require_calendar(ctx)
    summary = str(args.get("summary") or "").strip()
    if not summary:
        raise ToolError("'summary' is required")
    _when(args.get("start"), "start")
    _when(args.get("end"), "end")
    fields = await _event_fields(ctx, args)
    d = await _defaults(ctx)
    calendar = args.get("calendar") or d.get("calendar")
    await _visible_collection(ctx, calendar)
    conv_id = args.get("conversation_id") or await _errand_invite_conversation(ctx, args, summary)
    try:
        body = EventIn.model_validate({**fields, "summary": summary, "calendar": calendar,
                                       "conversation_id": conv_id})
    except ValidationError as e:
        raise _validation(e) from None
    _check_when(body.start, body.end)
    _check_tz(body.tz)
    if body.recurrence:
        _check_rrule(body.recurrence)
    await _own_conversation(ctx.db, ctx.p, body.conversation_id)
    reminders, source = await initial_reminders(ctx.db, ctx.p, body, calendar)
    body = body.model_copy(update={"reminders": reminders})
    payload = stable_uids(ctx, "event", create_payload(body, source), "summary", "start", "end", "calendar")
    from mailr.calendar.describe import label_of

    preview = _ev_preview(f"Create event “{summary[:80]}”", payload)
    preview["event"] = {**preview["event"], "calendar_name": await label_of(ctx.db, ctx.p.user_id, calendar, ctx.p)}
    res = await _run_event(ctx, "calendar.create", payload, target=calendar or "default calendar",
                           cvals={"calendar": calendar}, preview=preview)
    res = res if isinstance(res, ToolResult) else await _record_result(ctx, res, "event")
    if isinstance(res.data, dict):
        # What the event was written with, so the answer can say it ("with your default reminder,
        # 1 day before").
        res.data["reminders"] = reminders
        res.data["reminders_source"] = source
        res.data["reminders_text"] = ", ".join(offset_label(m, _is_all_day(body)) for m in reminders) or "none"
    return res


PERSONAL["create_event"] = _create_event_personal


_BASE_VERSION = {"type": "integer", "description": "the version you read (get_event / list_tasks, the screen or your "
                                                   "working set); changes saved since then are merged in"}


def _base_version(ctx: ToolCtx, args: dict[str, Any], kind: str, item: Any) -> int | None:
    """The version an agent's change is made from: what it says it read, else the version of the
    editor open on screen, else what this chat last wrote (core/live.py merges from there)."""
    from mailr.agent import screen

    bv = _int(args, "base_version", required=False)
    if bv is None and (ed := screen.find_editor(ctx.ui or {}, kind, item)) is not None:
        bv = ed.get("base_version")
    return bv if bv is not None else _working_version(ctx, kind, item)


_SCOPE = {"type": "string", "enum": ["this", "following", "all"],
          "description": "for recurring events: this occurrence, this and following, or the whole series"}


# ACT event.reschedule @assistant, ACT event.edit @assistant, ACT event.invite, ACT event.uninvite @assistant, ACT event.set_color @assistant,
# ACT event.move_calendar @assistant, ACT event.add_video @assistant
@tool("update_event",
      "Change a calendar event (default: the one focused on screen): any field of create_event; `calendar` "
      "moves it. `attendees` replaces the guest list (added guests are invited, removed ones get a "
      "cancellation). For recurring events give `occurrence` (its `occurrence` value) and `scope`. May need "
      "the user's approval.",
      _obj({"uid": {"type": "string"}, **_EVENT_PROPS, "occurrence": {"type": "string"}, "scope": _SCOPE,
            "base_version": _BASE_VERSION}),
      scope="calendar")
async def update_event(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from pydantic import ValidationError

    from mailr.calendar.routes import EventPatch, _check_rrule, _check_tz, _check_when, where_ctx

    await _require_calendar(ctx)
    uid, occ = await _event_ref(ctx, args)
    for k in ("start", "end"):
        _when(args.get(k), k)
    fields = await _event_fields(ctx, args)
    extra = {k: fields.pop(k) for k in ("send_updates", "identity_id") if k in fields}
    try:
        body = EventPatch.model_validate({**fields, "scope": args.get("scope") or "all", "occurrence": occ})
    except ValidationError as e:
        raise _validation(e) from None
    _check_when(body.start, body.end)
    _check_tz(body.tz)
    changes = {k: v for k, v in body.model_dump(exclude={"scope", "occurrence", "etag", "send_updates", "identity_id"},
                                                by_alias=True).items() if v is not None}
    if not changes:
        raise ToolError("nothing to change")
    await _occurrence_needed(ctx, uid, occ, body.scope)
    if changes.get("recurrence"):
        _check_rrule(changes["recurrence"])
    await _visible_collection(ctx, body.calendar)
    src = await _where(ctx, uid)
    payload = {"uid": uid, "changes": changes, "scope": body.scope, "occurrence": occ, **extra}
    if (bv := _base_version(ctx, args, "event", uid)) is not None:
        payload["base_version"] = bv
    from mailr.calendar.describe import event_preview

    preview = await event_preview(ctx.db, ctx.p, "update", uid, occ, body.scope, changes)
    res = await _run_event(ctx, "calendar.update", payload, target=uid, cvals=where_ctx(src, body.calendar),
                           preview=preview)
    return res if isinstance(res, ToolResult) else await _record_result(ctx, res, "event")


# ACT event.delete @assistant
@tool("delete_event",
      "Delete a calendar event (default: the one focused on screen), or for recurring events one occurrence, "
      "this and following, or the series. Guests of an event the user organizes get a cancellation. "
      "Usually needs the user's approval.",
      _obj({"uid": {"type": "string"}, "occurrence": {"type": "string"}, "scope": _SCOPE,
            "summary": {"type": "string", "description": "the event title, shown to the user when approving"},
            "send_updates": {"type": "boolean", "description": "mail the guests (default true)"}}),
      scope="calendar")
async def delete_event(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.calendar.routes import where_ctx

    await _require_calendar(ctx)
    uid, occ = await _event_ref(ctx, args)
    payload: dict[str, Any] = {"uid": uid, "scope": args.get("scope") or "all", "occurrence": occ}
    await _occurrence_needed(ctx, uid, occ, payload["scope"])
    if args.get("send_updates") is not None:
        payload["send_updates"] = bool(args["send_updates"])
    src = await _where(ctx, uid)
    from mailr.calendar.describe import event_preview

    preview = await event_preview(ctx.db, ctx.p, "delete", uid, occ, payload["scope"], summary=args.get("summary"))
    return _res(await _run_event(ctx, "calendar.delete", payload, target=uid, cvals=where_ctx(src, None),
                                 preview=preview))


# ACT event.cancel @assistant
@tool("cancel_event",
      "Cancel an event the user organizes (default: the one focused on screen): it stays in the calendar "
      "marked cancelled and the guests get a cancellation with `message`. For one occurrence give "
      "`occurrence` and scope this. Needs the user's approval when it mails guests.",
      _obj({"uid": {"type": "string"}, "occurrence": {"type": "string"}, "scope": _SCOPE,
            "message": {"type": "string", "description": "a note for the guests"}}), scope="calendar")
async def cancel_event(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.calendar import mirror
    from mailr.calendar.routes import where_ctx

    await _require_calendar(ctx)
    uid, occ = await _event_ref(ctx, args)
    src = await _where(ctx, uid)
    obj = await mirror.find_by_uid(ctx.db, ctx.p.user_id, uid, "event", ctx.p)
    if obj is None:
        raise ToolError("event not found")
    if not obj.is_organizer:
        raise ToolError("only the organiser can cancel this event; decline it (rsvp) or delete it instead")
    scope = args.get("scope") or ("this" if occ and obj.recurring else "all")
    await _occurrence_needed(ctx, uid, occ, scope)
    payload: dict[str, Any] = {"uid": uid, "changes": {"status": "CANCELLED"}, "scope": scope, "occurrence": occ}
    if args.get("message"):
        from mailr.core import mdmail

        payload["message"] = mdmail.to_text(str(args["message"]))[:4000]  # a plain-text email
    from mailr.calendar.describe import event_preview

    preview = await event_preview(ctx.db, ctx.p, "cancel", uid, occ, scope, summary=obj.summary or None)
    if payload.get("message"):
        preview["event"]["message"] = payload["message"]
    res = await _run_event(ctx, "calendar.update", payload, target=uid, cvals=where_ctx(src, None), preview=preview)
    return res if isinstance(res, ToolResult) else await _record_result(ctx, res, "event")


# ACT event.rsvp @assistant
@tool("rsvp",
      "Answer a meeting invitation: accepted, declined or tentative. Give `message_id` for an invitation in an "
      "email, or `event_uid` for one already in the calendar (search_events shows `my_status` on those). "
      "This sends a reply to the organizer and updates the user's calendar, so it needs the user's approval "
      "unless allowed. For a repeating event in the calendar, `scope` this (with `occurrence`, the occurrence "
      "string from search_events) answers one occurrence, following answers it and the ones after, all the series. "
      "`send` false with `message_id` and accepted only adds the invitation to the user's calendar, with no reply "
      "to the organizer (use it when the user asks you to add an invitation to their calendar, for example one they "
      "forwarded from their work address).",
      _obj({"message_id": {"type": "integer"}, "event_uid": {"type": "string"},
            "response": {"type": "string", "enum": ["accepted", "declined", "tentative"]},
            "comment": {"type": "string"}, "send": {"type": "boolean"},
            "scope": {"type": "string", "enum": ["this", "following", "all"]},
            "occurrence": {"type": "string"}}, ["response"]), scope="calendar")
async def rsvp(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.api.mail_read import load_message

    response = args.get("response")
    if response not in ("accepted", "declined", "tentative"):
        raise ToolError("response must be accepted, declined or tentative")
    from mailr.calendar.routes import _event_invite, _invite, rsvp_ctx, rsvp_preview

    verb = {"accepted": "Accept", "declined": "Decline", "tentative": "Maybe"}[response]
    if args.get("comment"):
        from mailr.core import mdmail

        args = {**args, "comment": mdmail.to_text(str(args["comment"]))}  # plain text in the reply
    if args.get("event_uid") and args.get("message_id") is None:
        uid = str(args["event_uid"])
        try:
            ev, _, _ = await _event_invite(ctx.db, ctx.p, uid, cal_p=ctx.p)
        except HTTPException as e:
            raise ToolError(str(e.detail)) from None
        org = (ev.get("organizer") or "").strip().lower() or None
        payload = {"event_uid": uid, "response": response, "comment": args.get("comment")}
        rscope = args.get("scope") or "all"
        if rscope not in ("this", "following", "all"):
            raise ToolError("scope must be this, following or all")
        if ev.get("recurring") and rscope != "all":
            if not args.get("occurrence"):
                raise ToolError("answering one occurrence needs `occurrence` (from search_events)")
            payload.update(scope=rscope, occurrence=str(args["occurrence"]))
        cvals: dict[str, Any] = {"recipients": []}
        if org:  # facts for the calendar.rsvp rules, as for mail.send (a first message to someone: ask)
            from mailr.core.sendgate import send_context
            from mailr.models import Identity

            ident = (await ctx.db.execute(select(Identity).where(Identity.user_id == ctx.p.user_id)
                                          .order_by(Identity.is_default.desc(), Identity.id))).scalars().first()
            cvals = (await send_context(ctx.db, ctx.p.user_id, {}, [org], ident, None) if ident is not None
                     else {"recipients": [org], "new_recipients": [org]})
        preview = rsvp_preview(verb, ev.get("summary") or "", org, response, args.get("comment"))
        from mailr.calendar.describe import rsvp_lines

        preview["lines"] += await rsvp_lines(ctx.db, ctx.p, uid, payload.get("occurrence"), payload.get("scope") or "all")
        return _res(await _run_gated(ctx, "calendar.rsvp", payload, target=org or uid, cvals=cvals,
                                     preview=preview,
                                     force_ask=await calendar_local_copy_reason(ctx, [await _where(ctx, uid)])))
    mid = _int(args, "message_id")
    try:
        m = await load_message(ctx.db, ctx.p, mid)
        _, _, info = await _invite(ctx.db, ctx.p, mid)
    except HTTPException as e:
        raise ToolError(str(e.detail)) from None
    org = info["organizer"]["email"]
    local = await calendar_local_copy_reason(ctx, [(await _defaults(ctx)).get("calendar")])
    if args.get("send") is False:
        # ACT invite.add_to_calendar @assistant
        # Only the calendar changes (no mail goes out): a calendar write, gated as calendar.create.
        if response != "accepted":
            raise ToolError("send false only adds an invitation (response accepted); to decline, send a reply")
        from mailr.core.trusted import invite_preview

        payload = {"message_id": mid, "_variant": "invite", "summary": info.get("summary") or m.subject or ""}
        res = _res(await _run_gated(ctx, "calendar.create", payload, target=info.get("uid") or str(mid),
                                    cvals={}, preview=invite_preview(info, m.subject), force_ask=local))
        res.message_ids = [mid]
        return res
    payload = {"message_id": mid, "response": response, "comment": args.get("comment")}
    res = _res(await _run_gated(ctx, "calendar.rsvp", payload, target=org or m.from_email,
                                cvals=await rsvp_ctx(ctx.db, ctx.p, m, org),
                                preview=rsvp_preview(verb, info.get("summary") or m.subject or "", org, response,
                                                     args.get("comment")), force_ask=local))
    res.message_ids = [mid]
    return res


def _res(r: ToolResult | dict[str, Any]) -> ToolResult:
    return r if isinstance(r, ToolResult) else ToolResult(r)
