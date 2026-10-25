"""Agent tools for tasks: search, list, create, update, complete, move, delete and bulk changes.
Registered in agent/tools.py, which re-exports them."""

from __future__ import annotations

from typing import Any

from mailr.agent.calendar_common import (
    HIDDEN_NOTE,
    _cal,
    _check_query,
    _clock,
    _defaults,
    _mirror_summary,
    _names,
    _record_result,
    _require_calendar,
    _run_gated,
    _split_local,
    _task_brief,
    _task_uid,
    _validation,
    _visible_collection,
    _when,
    _where,
    _zone,
    stable_uids,
)
from mailr.agent.calendar_tools import (
    _BASE_VERSION,
    _base_version,
    _res,
)
from mailr.agent.tools import (
    ToolCtx,
    ToolError,
    ToolResult,
    _obj,
    tool,
)

# ---------------- tasks


async def _task_list(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.calendar import todo
    from mailr.calendar.search import parse

    cal = await _cal()
    if not await cal.has_calendar(ctx.db, ctx.p.user_id, p=ctx.p):
        return ToolResult({"connected": False, "items": []})
    smart = args.get("smart") or None
    status = args.get("status") or ("all" if args.get("include_completed") else "open")
    lists = args.get("lists") or ([args["list"]] if args.get("list") else None)
    tags = args.get("tags") or ([args["tag"]] if args.get("tag") else None)
    q = str(args.get("q") or args.get("text") or "")
    await _check_query(ctx, q, "text" if args.get("text") else "q")

    async def run(q: str) -> dict[str, Any]:
        try:
            return await todo.list_tasks(ctx.db, ctx.p.user, ctx.p, smart=smart, status=status, lists=lists, tags_=tags,
                                         q=q, due_before=args.get("due_before"), due_after=args.get("due_after"),
                                         sort=args.get("sort"), limit=max(1, min(int(args.get("limit") or 100), 200)))
        except ValueError as e:
            raise ToolError(str(e)[:300]) from None
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"tasks unavailable: {e}"[:200]) from None

    async def run_slice(name: str) -> dict[str, Any]:
        try:
            return await todo.list_tasks(ctx.db, ctx.p.user, ctx.p, smart=name, status="open", sort="due", limit=1)
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"tasks unavailable: {e}"[:200]) from None

    from mailr.calendar.routes import _with_source_email

    zone = await _zone(ctx)
    names = await _names(ctx)

    async def shown(res: dict[str, Any]) -> tuple[list[dict[str, Any]], int, bool]:
        items = await _with_source_email(ctx.db, ctx.p, res["items"], "task")
        items, hidden, sens = await _split_local(ctx, items)
        return [_task_brief(t, zone=zone, names=names, clock=_clock(ctx)) for t in items], hidden, sens

    res = await run(q)
    items, hidden, sens = await shown(res)
    out: dict[str, Any] = {"connected": True, "items": items}
    if res.get("next_cursor"):
        out["more"] = True
    qr = parse(q)
    if not items and qr.words:
        # Words match a task's title and notes only. "deadline" or "due" finds nothing in a list of
        # tasks that are all due: show what the other filters find.
        ops = " ".join(f"{'-' if neg else ''}{op}:\"{val}\"" if " " in val else f"{'-' if neg else ''}{op}:{val}"
                       for op, val, neg in qr.ops)
        alt, h2, s2 = await shown(await run(ops))
        hidden, sens = hidden + h2, sens or s2
        words = " ".join(qr.words)
        out["note"] = (f"No task's title or notes contain {words!r}. Words match titles and notes only; they do not "
                       "mean \"due\" or \"deadline\". " + (
                           f"Without the words, the same filters find {len(alt)} task(s): see `without_words`."
                           if alt else "Without the words, the same filters find no task either."))
        if alt:
            out["without_words"] = alt[:50]
    narrowed = (smart not in (None, "all", "completed") or args.get("due_before") or args.get("due_after"))
    if not items and narrowed and status == "open" and not (q or lists or tags):
        # An empty Today is not an empty task list: /today answered "Task list is empty" while a
        # task was due on Friday (20 Oct 2026). Say what else is open, with the next one due.
        nxt, h2, s2 = await shown(await run_slice("upcoming"))
        hidden, sens = hidden + h2, sens or s2
        if nxt:
            out["next_due"] = nxt[0]
            out["note"] = ("No open task is in this selection, but the task list is not empty: the next task due "
                           "is in `next_due`.")
        elif (await run_slice("all"))["items"]:
            out["note"] = "No open task is in this selection, but the task list is not empty."
        else:
            out["note"] = "The task list has no open tasks."
    if hidden:
        out["note"] = (out.get("note", "") + " " + HIDDEN_NOTE).strip()
    return ToolResult(out, sensitive=sens)


@tool("search_tasks",
      "List or search tasks: status open (default), completed or all. `when` (\"this week\", \"next week\", "
      "\"tomorrow\", a weekday or a date) finds the tasks due in that range; `due_before` / `due_after` (ISO) "
      "bound the due time (`due_before` alone also finds overdue tasks). `text` takes words (they match a task's "
      "title and notes, so leave them out to list everything due) and the operators of search_calendar; `list` "
      "limits to one task list.",
      _obj({"status": {"type": "string", "enum": ["open", "completed", "all"]}, "text": {"type": "string"},
            "list": {"type": "string"}, "when": {"type": "string"}, "due_before": {"type": "string"},
            "due_after": {"type": "string"}}))
async def search_tasks(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    due_before, due_after = args.get("due_before"), args.get("due_after")
    for k in ("due_before", "due_after"):
        if args.get(k) and _when_or_none(args[k]) is None:
            raise ToolError(f"'{k}' must be an ISO date or datetime (2026-10-19); for a range of days use "
                            "`when` (\"next week\", \"this week\", \"tomorrow\", a weekday or a date)")
    if args.get("when"):
        from mailr.agent import when

        try:
            a, b = when.window(str(args["when"]), await _zone(ctx))
        except when.RangeError as e:
            raise ToolError(str(e)) from None
        due_after, due_before = a.isoformat(), b.isoformat()
    return await _task_list(ctx, {"status": args.get("status") or "open", "text": args.get("text"),
                                  "list": args.get("list"), "due_before": due_before, "due_after": due_after})


def _when_or_none(v: Any) -> Any:
    try:
        return _when(v, "due")
    except ToolError:
        return None


@tool("list_tasks",
      "List tasks. `smart`: today, overdue, upcoming (due after today), scheduled, nodate, high, email (made "
      "from email), all, completed. Or `list` (a task list URL), `tag`, `q` (words and operators). Open tasks "
      "unless include_completed. Items have `cite`; Today and Upcoming rows carry `section`.",
      _obj({"smart": {"type": "string", "enum": ["today", "overdue", "upcoming", "scheduled", "nodate", "high",
                                                  "email", "all", "completed"]},
            "list": {"type": "string"}, "tag": {"type": "string"}, "q": {"type": "string"},
            "include_completed": {"type": "boolean"},
            "status": {"type": "string", "enum": ["open", "completed", "all"]},
            "sort": {"type": "string", "enum": ["manual", "due", "priority", "created", "title"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200}}))
async def list_tasks(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    return await _task_list(ctx, args)


def _task_fields(args: dict[str, Any]) -> dict[str, Any]:
    """Tool arguments in the REST field names of POST/PATCH /tasks."""
    out = {k: args[k] for k in ("summary", "due", "start", "notes", "list", "priority", "parent", "repeat_from",
                                "tags", "timezone") if k in args and args[k] is not None}
    if "timezone" in out:
        out["tz"] = out.pop("timezone")
    if "tags" in out:
        out["categories"] = out.pop("tags")
    rule = args.get("rrule") if args.get("rrule") is not None else args.get("recurrence")
    if rule is not None:
        out["recurrence"] = str(rule).strip().removeprefix("RRULE:")
    rem = args.get("reminders")
    if rem is not None:
        if not isinstance(rem, list):
            raise ToolError("'reminders' must be a list")
        out["reminders"] = [r for r in rem if isinstance(r, int)]
        out["reminders_at"] = [str(r) for r in rem if isinstance(r, str) and r.strip()]
        for r in out["reminders_at"]:
            _when(r, "reminders")
    return out


_TASK_PROPS: dict[str, Any] = {
    "summary": {"type": "string"},
    "due": {"type": "string", "description": "ISO date or datetime"},
    "start": {"type": "string", "description": "deferred until then"},
    "notes": {"type": "string"},
    "list": {"type": "string", "description": "the task list's name (get_calendar_defaults) or id; on update: move there"},
    "priority": {"type": "integer", "minimum": 0, "maximum": 9, "description": "1 high … 9 low, 0 none"},
    "rrule": {"type": "string", "description": "repeat rule, e.g. FREQ=WEEKLY;BYDAY=FR (\"\" stops it)"},
    "repeat_from": {"type": "string", "enum": ["due", "completion"]},
    "tags": {"type": "array", "items": {"type": "string"}},
    "parent": {"type": "string", "description": "uid of the parent task (a subtask)"},
    "reminders": {"type": "array", "items": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
                  "description": "the task's whole list of reminders, replacing every one it has ([] removes "
                                 "them all): minutes before the due (0 = at the due), or ISO datetimes for a "
                                 "reminder at a set time (\"2026-10-14T06:00\"). A reminder needs a due."},
}

# Arguments the tools read besides _TASK_PROPS (older spellings). Anything else is refused by name:
# a reminder in a field the tool does not read ("remind_at") was dropped without a word (10 Oct 2026).
_ALIASES = {"recurrence", "timezone"}
_CREATE_ARGS = frozenset(_TASK_PROPS) | _ALIASES | {"conversation_id"}
_UPDATE_ARGS = frozenset(_TASK_PROPS) | _ALIASES | {"uid", "status", "base_version"}


def _known_args(args: dict[str, Any], allowed: frozenset[str], tool_name: str) -> None:
    extra = sorted(k for k in args if k not in allowed)
    if not extra:
        return
    hint = (" Reminders go in `reminders`: minutes before the due (0 = at the due) or ISO datetimes."
            if any("remind" in k or "alarm" in k for k in extra) else "")
    raise ToolError(f"{tool_name} has no field {', '.join(repr(k) for k in extra)}; nothing was changed.{hint}")


def _wants_reminders(fields: dict[str, Any]) -> bool:
    return bool(fields.get("reminders") or fields.get("reminders_at"))


NEEDS_DUE = ("'reminders': a reminder needs the task to have a due, and this one has none. Pass `due` too; "
             "for \"remind me at <time>\", set `due` to that time and `reminders` to [0]. Nothing was changed.")


async def _saved(ctx: ToolCtx, out: ToolResult, rec: dict[str, Any], fields: dict[str, Any]) -> ToolResult:
    """The stored state the user cares about, added to a written task's result: `will_remind`
    (every reminder still to fire, with its time) and `due_when`. Then each asked-for due and
    reminder is checked against what was stored and planned: a field that did not take is an
    error naming it, so the model never reports a change that did not happen (10 Oct 2026)."""
    from datetime import UTC, datetime

    from mailr.agent import when
    from mailr.calendar import ical, mirror, planner

    if not isinstance(out.data, dict) or not rec.get("uid") or out.data.get("note"):
        return out  # hidden from this model (local-only): nothing more to say
    zone = await _zone(ctx)
    obj = await mirror.find_by_uid(ctx.db, ctx.p.user_id, rec["uid"], "task", ctx.p)
    fires: list[tuple[datetime, int, str]] = []
    if obj is not None:
        await ctx.db.refresh(obj)  # the row as written, not a copy read earlier in this session
        cfg, _z, _me = await planner._context(ctx.db, ctx.p.user_id)
        fires = planner.task_fire_times(obj, cfg, zone, datetime.now(UTC))
    out.data["will_remind"] = [
        {"at": when.local_iso(f.isoformat(), zone), "when": when.describe(f.isoformat(), None, False, zone, _clock(ctx)),
         **({"default": True} if src == "default" else {})} for f, _m, src in fires]
    if not fires:
        out.data["will_remind_note"] = ("No reminder will fire for this task" +
                                        (": it has no due." if not rec.get("due") else "."))
    problems: list[str] = []

    def canon(v: Any) -> str:
        return ical.canonical_occurrence(ical.parse_when(v, zone)) if v not in (None, "") else ""

    if fields.get("due") and canon(fields["due"]) != canon(rec.get("due")):
        problems.append(f"due (asked {fields['due']}; the task's due is {rec.get('due') or 'none'})")
    if "reminders" in fields and sorted(set(fields["reminders"])) != sorted(rec.get("reminders") or []):
        problems.append(f"reminders (asked {sorted(set(fields['reminders']))} minutes before the due; "
                        f"the task has {rec.get('reminders') or []})")
    if "reminders_at" in fields:
        asked = sorted({canon(x) for x in fields["reminders_at"]})
        stored = sorted(rec.get("reminders_at") or [])
        planned = [f for f, _m, _s in fires]
        if asked != stored:
            problems.append(f"reminders (asked {asked}; the task has {stored})")
        else:
            late = [a for a in asked if datetime.fromisoformat(a) > datetime.now(UTC)
                    and datetime.fromisoformat(a) not in planned]
            if late:
                problems.append(f"reminders ({', '.join(late)} is saved on the task but will not fire)")
    if problems:
        raise ToolError("The task was written, but these did not take: " + "; ".join(problems) +
                        ". Tell the user exactly this; do not say the change is set.")
    return out



# ACT task.create @assistant, ACT task.subtask_add @assistant, ACT mail.to_task @assistant (with the email's conversation_id)
@tool("create_task",
      "Create a task (in the default task list unless `list` is given): due, start, notes, priority, repeat, "
      "tags, parent (a subtask), reminders, and a link to the email it came from (conversation_id). For "
      "\"remind me about X at <time>\": a new task named after X with `due` = that time and `reminders` = [0]. "
      "The answer has `will_remind`: the reminders that will fire, with their times.",
      _obj({**_TASK_PROPS, "conversation_id": {"type": "integer"}}, ["summary"]), scope="calendar")
async def create_task(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from pydantic import ValidationError

    from mailr.agent import agentwrite
    from mailr.calendar.routes import _check_rrule, _check_tz, _check_when, _own_conversation
    from mailr.calendar.routes_tasks import TaskIn

    await _require_calendar(ctx)
    _known_args(args, _CREATE_ARGS, "create_task")
    from mailr.core import mdmail

    # The title is plain text; the notes keep their light Markdown, which the task panes show.
    summary = mdmail.inline_text(agentwrite.clean_text(ctx, str(args.get("summary") or "").strip()))
    if not summary:
        raise ToolError("'summary' is required")
    _when(args.get("due"), "due")
    _when(args.get("start"), "start")
    fields = _task_fields(args)
    if _wants_reminders(fields) and not fields.get("due"):
        raise ToolError(NEEDS_DUE)
    if fields.get("notes"):
        fields["notes"] = agentwrite.clean_text(ctx, fields["notes"])
    d = await _defaults(ctx)
    lst = args.get("list") or d.get("task_list")
    await _visible_collection(ctx, lst)
    try:
        body = TaskIn.model_validate({**fields, "summary": summary, "list": lst,
                                      "priority": fields.get("priority") or None,
                                      "reminders_at": fields.get("reminders_at") or None,
                                      "conversation_id": args.get("conversation_id")})
    except ValidationError as e:
        raise _validation(e) from None
    _check_when(body.due, body.start)
    _check_tz(body.tz)
    if body.recurrence:
        _check_rrule(body.recurrence)
    await _own_conversation(ctx.db, ctx.p, body.conversation_id)
    payload = stable_uids(ctx, "task", {k: v for k, v in body.model_dump().items() if v not in (None, "")},
                          "summary", "due", "list", "parent")
    from mailr.calendar.describe import name_list

    res = await _run_gated(ctx, "tasks.create", payload, target=lst or "default task list", cvals={"list": lst},
                           preview={"title": f"Add task “{summary[:80]}”", "kind": "task",
                                    "task": await name_list(ctx.db, ctx.p.user_id, payload, ctx.p),
                                    "lines": [x for x in (summary, args.get("due") and f"due {args['due']}",
                                                          payload.get("notes")) if x]},
                           ask_by_default=agentwrite.taint_reason(ctx), force_ask=agentwrite.local_copy_reason(ctx))
    if isinstance(res, ToolResult):
        return res
    return await _saved(ctx, await _record_result(ctx, res, "task"), res, fields)


# ACT task.reschedule @assistant, ACT task.edit @assistant, ACT task.reopen @assistant, ACT task.set_start @assistant, ACT task.set_repeat @assistant, ACT task.tag @assistant,
# ACT task.reparent @assistant, ACT task.remind_at @assistant, ACT task.set_priority @assistant, ACT task.move_list @assistant
@tool("update_task",
      "Change a task (default: the one focused on screen): any field of create_task (`list` moves it, \"\" "
      "for due or start clears it), or reopen a completed one with status open. To complete use complete_task. "
      "Change only the task the user named or is clearly talking about; to be reminded of something else, "
      "create_task. The answer has `will_remind`: the reminders that will fire, with their times.",
      _obj({"uid": {"type": "string"}, **_TASK_PROPS, "status": {"type": "string", "enum": ["open"]},
            "base_version": _BASE_VERSION}),
      scope="calendar")
async def update_task(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from pydantic import ValidationError

    from mailr.calendar.routes import _check_rrule, _check_tz, _check_when, where_ctx
    from mailr.calendar.routes_tasks import TaskPatch

    await _require_calendar(ctx)
    _known_args(args, _UPDATE_ARGS, "update_task")
    uid = _task_uid(ctx, args)
    clear = {k: True for k in ("due", "start") if args.get(k) == ""}
    for k in ("due", "start"):
        if args.get(k):
            _when(args[k], k)
    from mailr.agent import agentwrite

    fields = {k: v for k, v in _task_fields(args).items() if not (k in clear and v == "")}
    from mailr.core import mdmail

    for k in ("summary", "notes"):
        if fields.get(k):
            fields[k] = agentwrite.clean_text(ctx, fields[k])
    if fields.get("summary"):
        fields["summary"] = mdmail.inline_text(fields["summary"])
    # `reminders` is the task's whole list: relative and absolute ones are both replaced, so [] clears
    # every reminder (an absolute one stayed before: 10 Oct 2026).
    if _wants_reminders(fields) and not fields.get("due"):
        from mailr.calendar import mirror

        cur = await mirror.find_by_uid(ctx.db, ctx.p.user_id, uid, "task", ctx.p)
        if "due" in clear or (cur is not None and cur.due_at is None and cur.due_date is None):
            raise ToolError(NEEDS_DUE)
    try:
        body = TaskPatch.model_validate(fields)
    except ValidationError as e:
        raise _validation(e) from None
    _check_when(body.due, body.start)
    _check_tz(body.tz)
    if body.recurrence:
        _check_rrule(body.recurrence)
    await _visible_collection(ctx, body.list)
    changes: dict[str, Any] = {k: v for k, v in body.model_dump().items()
                               if v is not None and k in ("summary", "notes", "priority", "list", "due", "parent",
                                                          "reminders", "reminders_at", "start", "recurrence",
                                                          "repeat_from", "categories", "tz")}
    for k in clear:
        changes[k] = None
    if args.get("status") == "open":
        changes["status"] = "open"
    if not changes:
        raise ToolError("nothing to change")
    action = "tasks.complete" if set(changes) == {"status"} else "tasks.update"
    from mailr.calendar.describe import change_lines, name_list

    src = await _where(ctx, uid, "VTODO")
    free_text = bool(changes.get("summary") or changes.get("notes"))
    tpayload: dict[str, Any] = {"uid": uid, "changes": changes}
    if (bv := _base_version(ctx, args, "task", uid)) is not None:
        tpayload["base_version"] = bv
    shown = await name_list(ctx.db, ctx.p.user_id, {"uid": uid, **changes}, ctx.p)
    res = await _run_gated(ctx, action, tpayload, target=uid,
                           cvals=where_ctx(src, body.list, "list"),
                           preview={"title": "Change task" if action == "tasks.update" else "Reopen task",
                                    "kind": "task", "task": shown,
                                    "lines": change_lines(changes, shown.get("list_name"))},
                           ask_by_default=agentwrite.taint_reason(ctx) if free_text else None,
                           force_ask=agentwrite.local_copy_reason(ctx) if free_text else None)
    if isinstance(res, ToolResult):
        return res
    out = await _saved(ctx, await _record_result(ctx, res, "task"), res, fields)
    if isinstance(out.data, dict) and not out.data.get("note"):
        out.data["changed"] = sorted(changes)
    return out


# ACT task.complete @assistant, ACT task.skip @assistant
@tool("complete_task",
      "Mark a task done (default: the one focused on screen). A repeating task moves to its next due and "
      "the answer has `next`; `skip` moves it on without marking this one done.",
      _obj({"uid": {"type": "string"}, "summary": {"type": "string"}, "skip": {"type": "boolean"}}),
      scope="calendar")
async def complete_task(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.calendar import mirror
    from mailr.calendar.routes import where_ctx

    await _require_calendar(ctx)
    uid = _task_uid(ctx, args)
    src = await _where(ctx, uid, "VTODO")
    obj = await mirror.find_by_uid(ctx.db, ctx.p.user_id, uid, "task", ctx.p)
    title = str(args.get("summary") or (obj.summary if obj else "") or uid)
    if args.get("skip"):
        payload: dict[str, Any] = {"op": "skip", "uid": uid}
        head = f"Skip this time of “{title[:80]}”"
    else:
        payload = {"uid": uid, "changes": {"status": "completed"}}
        head = f"Complete task “{title[:80]}”"
    res = await _run_gated(ctx, "tasks.complete", payload, target=uid, cvals=where_ctx(src, None, "list"),
                           preview={"title": head, "kind": "task", "task": {"uid": uid, "summary": title},
                                    "lines": [title]})
    if isinstance(res, ToolResult):
        return res
    out = await _record_result(ctx, res, "task")
    if isinstance(out.data, dict) and obj is not None and obj.recurring:
        out.data["repeating"] = True
        out.data["next"] = res.get("next")
    return out


# ACT task.move_list, ACT task.reorder @assistant, ACT task.reparent
@tool("move_task",
      "Move a task (default: the one focused on screen) to another list (its subtasks follow), under a "
      "parent task (\"\" makes it top-level), or before/after a sibling in the manual order.",
      _obj({"uid": {"type": "string"}, "list": {"type": "string"}, "parent": {"type": "string"},
            "before": {"type": "string"}, "after": {"type": "string"}, "summary": {"type": "string"}}),
      scope="calendar")
async def move_task(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.calendar.routes import where_ctx

    await _require_calendar(ctx)
    uid = _task_uid(ctx, args)
    where = {k: str(args[k]) for k in ("list", "parent", "before", "after") if args.get(k) is not None}
    if not where:
        raise ToolError("give list, parent, before or after")
    await _visible_collection(ctx, where.get("list"))
    src = await _where(ctx, uid, "VTODO")
    res = await _run_gated(ctx, "tasks.update", {"op": "move", "uid": uid, **where}, target=uid,
                           cvals=where_ctx(src, where.get("list"), "list"),
                           preview={"title": f"Move task “{str(args.get('summary') or uid)[:80]}”", "kind": "task",
                                    "task": {"uid": uid}, "lines": [f"{k}: {v}" for k, v in where.items()]})
    return res if isinstance(res, ToolResult) else await _record_result(ctx, res, "task")


# ACT task.delete @assistant
@tool("delete_task", "Delete a task (default: the one focused on screen); `cascade` deletes its subtasks too "
      "(else they stay as top-level tasks). Usually needs the user's approval.",
      _obj({"uid": {"type": "string"}, "summary": {"type": "string"}, "cascade": {"type": "boolean"}}),
      scope="calendar")
async def delete_task(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.calendar.routes import where_ctx

    await _require_calendar(ctx)
    uid = _task_uid(ctx, args)
    src = await _where(ctx, uid, "VTODO")
    title = str(args.get("summary") or await _mirror_summary(ctx, uid, "task") or uid)
    cascade = bool(args.get("cascade"))
    return _res(await _run_gated(ctx, "tasks.delete", {"uid": uid, "cascade": cascade}, target=uid,
                                 cvals=where_ctx(src, None, "list"),
                                 preview={"title": f"Delete task “{title[:80]}”" + (" and its subtasks" if cascade else ""),
                                          "kind": "task", "danger": True,
                                          "task": {"uid": uid, "summary": args.get("summary")}, "lines": [title]}))


# ACT task.bulk_complete @assistant, ACT task.bulk_reschedule @assistant, ACT task.bulk_move @assistant, ACT task.bulk_tag @assistant,
# ACT task.bulk_priority @assistant, ACT task.bulk_delete @assistant
@tool("bulk_tasks",
      "One action on many tasks (default: the tasks selected on screen). op: complete, reopen, reschedule "
      "(args {due} or {shift_days}), move (args {list}), tag / untag (args {tag}), priority (args {priority}), "
      "delete (args {cascade}; asks the user). Each task succeeds or fails on its own.",
      _obj({"op": {"type": "string", "enum": ["complete", "reopen", "reschedule", "move", "tag", "untag", "priority",
                                              "delete"]},
            "uids": {"type": "array", "items": {"type": "string"}}, "args": {"type": "object"}}, ["op"]),
      scope="calendar")
async def bulk_tasks(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from pydantic import ValidationError

    from mailr.calendar.routes import _check_when
    from mailr.calendar.routes_tasks import BulkIn

    await _require_calendar(ctx)
    uids = args.get("uids") or (ctx.ui.get("tasks") or {}).get("selected") or []
    try:
        body = BulkIn.model_validate({"op": args.get("op"), "uids": [str(u) for u in uids], "args": args.get("args") or {}})
    except ValidationError as e:
        raise _validation(e) from None
    a = body.args
    if body.op == "reschedule":
        _check_when(a.get("due"), *(a.get("dues") or {}).values())
    cvals: dict[str, Any] = {}
    if body.op == "move":
        if not a.get("list"):
            raise ToolError("move needs args.list")
        await _visible_collection(ctx, a["list"])
        cvals = {"list": a["list"], "lists": [a["list"]]}
    payload = {"op": "bulk", "bulk_op": body.op, "uids": body.uids, "args": a}
    # The card names the tasks by title (a uid tells the user nothing).
    shown = [await _mirror_summary(ctx, u, "task") or u for u in body.uids[:20]]
    if len(body.uids) > len(shown):
        shown.append(f"…and {len(body.uids) - len(shown)} more")
    res = await _run_gated(ctx, "tasks.bulk", payload, target=f"{len(body.uids)} tasks", cvals=cvals,
                           preview={"title": f"{body.op.capitalize()} {len(body.uids)} tasks", "kind": "task",
                                    "danger": body.op == "delete", "lines": shown},
                           ask_by_default="a bulk delete asks by default" if body.op == "delete" else None)
    if isinstance(res, ToolResult):
        return res
    items, hidden, sens = await _split_local(ctx, list(res.get("items") or []))
    out: dict[str, Any] = {"op": body.op, "ok": res.get("ok") or [], "failed": [
        {k: f.get(k) for k in ("uid", "error", "detail")} for f in res.get("failed") or []],
        "items": [_task_brief(t, zone=await _zone(ctx), names=await _names(ctx), clock=_clock(ctx)) for t in items]}
    if hidden:
        out["note"] = HIDDEN_NOTE
    return ToolResult(out, sensitive=sens)
