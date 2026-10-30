"""Shared helpers of the calendar, task and collection tools (calendar_tools.py, task_tools.py,
collection_tools.py): the calendar client, visibility, local-only splitting, citations, result
shapes and the gated executors. agent/tools.py re-exports them."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from mailr.agent.tools import (
    ToolCtx,
    ToolError,
    ToolResult,
    _audit,
    gate,
    local_ok,
    pending_result,
)
from mailr.core.live import LiveConflict

# ---------------------------------------------------------------- calendar and tasks
#
# Reads use the mirror (calendar/client.py, mirror.py, todo.py, search.py) or call the read routes
# directly. Every change goes through the permission engine and is then carried out by the executor
# registered for its action class (calendar/routes.py, routes_tasks.py, routes_collections.py), so
# an action the user approves later runs exactly the same code as one that was allowed at once.
# Event writes that mail guests are gated like calendar/routes.py `_gated_event`: the write class
# and calendar.invite (calendar.cancel), with one approval card for both (`_run_event`).
# Payloads use the calendar REST fields.
#
# Results cite their records: every event carries `cite` ([[event:<uid>@<occurrence>]]) and every
# task `cite` ([[task:<uid>]]); intel/citations.py resolves those markers in the answer.
# Local-only calendars reach the built-in assistant only when its chat can run on a local
# model (the session then turns sensitive); otherwise they are left out and the result says so.

HIDDEN_NOTE = ("Some calendars or task lists are hidden from this model (local-only accounts). Say so when "
               "the answer may be incomplete.")


SAME_AS_WINDOW = 600  # seconds: a repeat of the same create within 10 to 20 minutes is the same create


def stable_uids(ctx: ToolCtx, kind: str, payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    """The payload of an agent's create with a UID made from who asks (the chat, else the token)
    and what is made (`keys`), plus the UIDs of the same create in the window before
    (`_same_as`). A create the model repeats in a turn, after a timeout or in a retried turn finds
    the first one's item (calendar/writer.py `create_once`) instead of making a second."""
    import hashlib
    import json
    import time

    if payload.get("uid"):
        return payload
    token = getattr(ctx.p, "token", None)
    who = ctx.chat_session_id or (f"token:{token.id}" if token is not None else "user")
    what = {k: payload.get(k) for k in keys}
    bucket = int(time.time() // SAME_AS_WINDOW)

    def uid(b: int) -> str:
        raw = json.dumps([ctx.p.user_id, who, kind, what, b], sort_keys=True, default=str)
        return f"{hashlib.sha256(raw.encode()).hexdigest()[:40]}@mailr"

    return {**payload, "uid": uid(bucket), "_same_as": [uid(bucket), uid(bucket - 1)]}


async def _cal():
    from mailr.calendar import client as cal

    return cal


async def _require_calendar(ctx: ToolCtx, *, read: bool = False) -> Any:
    """`read`: a calendar link or a phone calendar's busy times are enough (calendar/client.py
    has_any_calendar); writes need a calendar account."""
    cal = await _cal()
    has = cal.has_any_calendar if read else cal.has_calendar
    if not await has(ctx.db, ctx.p.user_id, p=ctx.p):
        raise ToolError("no calendar account is connected (Settings → Calendar)")
    return cal


async def _visible_collection(ctx: ToolCtx, url: str | None) -> None:
    """A token may only name calendars / task lists of the accounts it can see."""
    if not url or ctx.p.token is None:
        return
    cal = await _cal()
    try:
        await cal._check_url_visible(ctx.db, ctx.p.user_id, ctx.p, url)
    except ValueError:
        raise ToolError(f"unknown calendar {url!r}; see get_calendar_defaults") from None


async def _mirror_summary(ctx: ToolCtx, uid: str, kind: str) -> str:
    """The title an approval card shows when the agent did not name the item (not its uid)."""
    from mailr.calendar import mirror

    obj = await mirror.find_by_uid(ctx.db, ctx.p.user_id, uid, kind, ctx.p)
    return (obj.summary or "") if obj else ""


async def _where(ctx: ToolCtx, uid: str, comp: str = "VEVENT") -> str:
    """Where the event / task lives, among what this caller can see. Permission constraints
    are matched against it."""
    cal = await _cal()
    try:
        url = await cal.locate(ctx.db, ctx.p.user_id, uid, comp, p=ctx.p)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"calendar unavailable: {e}"[:200]) from None
    if url is None:
        raise ToolError("event not found" if comp == "VEVENT" else "task not found")
    return url


def _when(v: Any, name: str) -> Any:
    from mailr.calendar.client import parse_when

    if v in (None, ""):
        return None
    try:
        w = parse_when(str(v))
    except (ValueError, TypeError):
        w = None
    if w is None:
        raise ToolError(f"'{name}' must be an ISO date or datetime")
    return w


DAY_FORMS = "an ISO date (2026-10-19), today, tomorrow, yesterday or a weekday (fri)"


async def _check_query(ctx: ToolCtx, text: Any, name: str = "text") -> None:
    """Refuse operators the search cannot read. The search itself makes such an operator match
    nothing, so a call with "due:next-week" came back empty with no reason given.
    The error says what to send instead."""
    if not text:
        return
    from mailr.agent import when
    from mailr.calendar.search import HAS_VALUES, IS_VALUES, parse, unreadable

    bad = unreadable(parse(str(text)), now=when.now(), user_tz=await _zone(ctx))
    if bad:
        raise ToolError(
            f"`{name}` has operators the search cannot read: {', '.join(bad)}. Dates in after:, before: and "
            f"due: take {DAY_FORMS}, with < or > for due: (due:<2026-10-19). is: takes {', '.join(IS_VALUES)}; "
            f"has: takes {', '.join(HAS_VALUES)}; pri: takes high, medium, low, none or 1-9. For a range of days "
            "use `when` (\"next week\") or `from`/`to`.")


async def _defaults(ctx: ToolCtx) -> dict[str, Any]:
    cal = await _cal()
    return await cal.defaults(ctx.db, ctx.p.user_id, p=ctx.p)


def _validation(e: Exception) -> ToolError:
    errs = getattr(e, "errors", None)
    if callable(errs):
        first = (errs() or [{}])[0]
        where = ".".join(str(x) for x in first.get("loc") or [])
        return ToolError(f"{where}: {first.get('msg', 'invalid value')}"[:300])
    return ToolError(str(e)[:300])




async def _split_local(ctx: ToolCtx, items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, bool]:
    """(the items this model may see, how many were hidden, whether a local-only one is kept)."""
    lo = [i for i in items if i.get("local_only")]
    if not lo:
        return items, 0, False
    if await local_ok(ctx):
        return items, 0, True
    return [i for i in items if not i.get("local_only")], len(lo), False


def cite_event(uid: str, occurrence: str | None) -> str:
    return f"[[event:{uid}@{occurrence}]]" if occurrence else f"[[event:{uid}]]"


def cite_task(uid: str) -> str:
    return f"[[task:{uid}]]"


_EV_KEYS = ("uid", "occurrence", "summary", "start", "end", "all_day", "tz", "location", "calendar", "recurring",
            "rrule", "status", "transparent", "color", "my_status", "is_organizer", "attendees", "conversation_id",
            "description")
_TASK_KEYS = ("uid", "summary", "due", "start", "completed", "status", "list", "priority", "categories", "rrule",
              "repeat_from", "parent", "open_subtask_count", "section", "conversation_id", "notes")


async def _names(ctx: ToolCtx) -> dict[str, str]:
    """{calendar or task list URL: the label the user knows it by} (calendar/describe.py)."""
    from mailr.calendar.describe import labels

    try:
        return await labels(ctx.db, ctx.p.user_id, ctx.p)
    except Exception:  # noqa: BLE001
        return {}


async def _zone(ctx: ToolCtx):
    """The user's zone: the one every time in a calendar or task result is given in."""
    from mailr.calendar import tz as tzmod

    if ctx.user_zone is None:
        ctx.user_zone = await tzmod.user_zone(ctx.db, ctx.p.user_id)
    return ctx.user_zone


def _clock(ctx: ToolCtx) -> str:
    """The user's clock ("12h" or "24h", calendar/tz.py user_clock): the one the `when` lines use."""
    from mailr.calendar import tz as tzmod

    return tzmod.user_clock(ctx.p.user)


def _localize(out: dict[str, Any], zone: Any, start: str, end: str | None, all_day: bool, label: str,
              clock: str = "24h") -> None:
    """Times in the user's zone with their offset, a line with the weekday (`label`) in the user's
    `clock`, and the record's own zone renamed `event_tz` when it differs (it is not the zone the
    times are in)."""
    from mailr.agent import when
    from mailr.calendar import tz as tzmod

    raw_start, raw_end = out.get(start), out.get(end) if end else None
    for k in (start, end):
        if k and k in out:
            out[k] = when.local_iso(out[k], zone)
    if raw_start:
        line = when.describe(raw_start, raw_end, all_day, zone, clock)
        if line:
            out[label] = line
    if "tz" in out:
        own = out.pop("tz")
        if own and own != tzmod.zone_name(zone):
            out["event_tz"] = own


def _named(out: dict[str, Any], key: str, names: dict[str, str] | None) -> None:
    """The calendar or list of a brief by its label (calendar/describe.py), never its URL."""
    from mailr.calendar.describe import looks_internal

    v = out.get(key)
    if v and looks_internal(v):
        if names and v in names:
            out[key] = names[v]
        else:
            out.pop(key, None)


# What Ambrose reads of a block from a phone calendar (calendar/phonecal.py): its time, and for a
# calendar that sends details its title and place.
_BUSY_KEYS = ("uid", "occurrence", "start", "end", "all_day", "tz")


def busy_block(e: dict[str, Any], names: dict[str, str] | None = None) -> dict[str, Any]:
    """A phone calendar's block as the model reads it, marked as unchangeable here.

    A busy-only block reads "Busy (<calendar>)" with its times: no title, place or guests reach
    mailr for it, so the local-only rules have nothing to hide in it. A block with details
    (`phone_details`: the calendar is set to send titles and places) reads like an ordinary
    read-only event: its title, place, calendar name and times. Its connector is "normal" like the
    owner's other calendar events (see calendar/phonecal.py on the outbound data policy). Guests,
    descriptions and links never reach mailr from a phone, so neither kind has them."""
    from mailr.calendar import phonecal

    out = {k: e[k] for k in _BUSY_KEYS if k in e and e[k] not in (None, "", [])}
    calendar = (names or {}).get(e.get("calendar") or "")
    if e.get("phone_details"):
        out["summary"] = e.get("summary") or phonecal.model_title(None)
        if e.get("location"):
            out["location"] = e["location"]
        if calendar:
            out["calendar"] = calendar
        out["read_only"] = "event from a phone calendar; it changes only on the phone"
        return out
    out["summary"] = phonecal.model_title(calendar)
    out["read_only"] = "busy time from a phone calendar; it changes only on the phone"
    return out


def _ev_brief(e: dict[str, Any], keys: tuple[str, ...] = _EV_KEYS, zone: Any = None,
              names: dict[str, str] | None = None, clock: str = "24h") -> dict[str, Any]:
    if e.get("phone_busy"):
        out = busy_block(e, names)
        if zone is not None:
            _localize(out, zone, "start", "end", bool(e.get("all_day")), "when", clock)
        out["cite"] = cite_event(e["uid"], e.get("occurrence") or e.get("recurrence_id"))
        return out
    out = {k: e[k] for k in keys if k in e and e[k] not in (None, "", [])}
    _named(out, "calendar", names)
    if out.get("rrule"):
        from mailr.agent import when

        out["repeats"] = when.repeat_text(out["rrule"], e.get("start"))
    if "description" in out:
        out["description"] = str(out["description"])[:400]
    if zone is not None:
        _localize(out, zone, "start", "end", bool(e.get("all_day")), "when", clock)
    out["cite"] = cite_event(e["uid"], e.get("occurrence") or e.get("recurrence_id"))
    return out


def _task_brief(t: dict[str, Any], keys: tuple[str, ...] = _TASK_KEYS, zone: Any = None,
                names: dict[str, str] | None = None, clock: str = "24h") -> dict[str, Any]:
    out = {k: t[k] for k in keys if k in t and t[k] not in (None, "", [])}
    _named(out, "list", names)
    if "notes" in out:
        out["notes"] = str(out["notes"])[:400]
    if zone is not None:
        if out.get("start"):
            from mailr.agent import when

            out["start"] = when.local_iso(out["start"], zone)
        _localize(out, zone, "due", None, bool(t.get("due_all_day")) or len(str(t.get("due") or "")) == 10, "due_when",
                  clock)
    out["cite"] = cite_task(t["uid"])
    return out


async def _record_result(ctx: ToolCtx, rec: Any, kind: str) -> ToolResult:
    """A written event or task as a tool result: cited, and reduced to its id when it lives in a
    local-only calendar this model may not see."""
    if not isinstance(rec, dict) or not rec.get("uid"):
        return ToolResult(rec if isinstance(rec, dict) else {"ok": True})
    sensitive = bool(rec.get("local_only"))
    if sensitive and not await local_ok(ctx):
        return ToolResult({"ok": True, "uid": rec["uid"], "note": HIDDEN_NOTE})
    from mailr.calendar.describe import for_model

    out = await for_model(ctx.db, ctx.p.user_id, dict(rec), ctx.p)
    out["cite"] = cite_event(rec["uid"], rec.get("occurrence")) if kind == "event" else cite_task(rec["uid"])
    refs = []
    if rec.get("version") is not None:
        from mailr.core import live

        refs = [{**live.ref(kind, rec["uid"], rec["version"]), "title": rec.get("summary") or ""}]
    return ToolResult(out, sensitive=sensitive, refs=refs)


async def _execute(ctx: ToolCtx, action: str, payload: dict[str, Any], target: str) -> dict[str, Any]:
    """Run the executor of `action` now (the permission check has passed)."""
    from mailr.calendar import writer
    from mailr.core import activity, permissions

    permissions._load_executors()
    fn = permissions.executor_for(action, payload)
    if fn is None:
        raise ToolError(f"{action} is not available in this version of Mailr")
    # Settings → Activity (core/activity.py): what the Undo needs, read before the change.
    before = await activity.snapshot(ctx.db, ctx.p.user_id, action, payload, ctx.p)
    try:
        result = await fn(ctx.db, ctx.p.user, permissions.stamp(payload, ctx.p))
    except HTTPException as e:
        raise ToolError(str(e.detail)) from None
    except writer.Conflict:
        await ctx.db.commit()  # the mirror keeps the server copy it fetched
        raise ToolError("it changed on another device just now; read it again, then retry") from None
    except LiveConflict as e:
        # Refused before anything was written; the session stays usable (no rollback).
        raise ToolError(e.body()["detail"] + " Nothing changed. Read it again and make the change on its "
                        "current version, or ask the user.") from None
    except writer.Gone:
        raise ToolError("it was deleted on the calendar server") from None
    except writer.CalendarNotConnected as e:
        raise ToolError(str(e)[:300]) from None
    except (ValueError, LookupError, PermissionError) as e:
        raise ToolError(str(e).strip("'")[:300]) from None
    act_entry = activity.calendar_task(action, payload, None, result, before)
    await _audit(ctx, f"agent.{action}", target, payload,
                 {**(result if isinstance(result, dict) else {}), **({activity.KEY: act_entry} if act_entry else {})})
    await ctx.db.commit()
    return result if isinstance(result, dict) else {"ok": True}


async def calendar_local_copy_reason(ctx: ToolCtx, calendars: list[str | None]) -> str | None:
    """Why an assistant calendar write asks first because its chat has read local-only content
    (agentwrite.local_copy_reason), or None. A write whose target calendars all belong to local-only
    accounts keeps the content local and does not ask; an unknown target (the default calendar
    when none is set) asks."""
    from mailr.agent.agentwrite import local_copy_reason
    from mailr.calendar import mirror
    from mailr.models import Connector

    reason = local_copy_reason(ctx)
    if reason is None:
        return None
    urls = [str(c) for c in calendars if c]
    if not urls:
        return reason
    for url in urls:
        coll = await mirror.collection_by_url(ctx.db, ctx.p.user_id, url, ctx.p)
        if coll is None and url.isdigit():
            coll = await ctx.db.get(mirror.CalCollection, int(url))
            coll = coll if coll is not None and coll.user_id == ctx.p.user_id else None
        conn = await ctx.db.get(Connector, coll.connector_id) if coll is not None else None
        if conn is None or conn.data_class != "local_only":
            return reason
    return None


async def _run_gated(ctx: ToolCtx, action: str, payload: dict[str, Any], *, target: str, cvals: dict[str, Any],
                     preview: dict[str, Any], ask_by_default: str | None = None,
                     force_ask: str | None = None) -> ToolResult | dict[str, Any]:
    """Check `action`, then run it now or leave it for the user's approval. `ask_by_default` (a
    reason) makes the class ask when no rule decides, for the parts of a class that default to
    ask (renaming or deleting a list, a bulk delete), as calendar/routes_tasks.py `gated` does;
    `force_ask` asks even when a rule allows."""
    pending = await gate(ctx, action, target=target, cvals=cvals, payload=payload, preview=preview,
                         ask_by_default=ask_by_default, force_ask=force_ask)
    if pending is not None:
        return pending
    return await _execute(ctx, action, payload, target)


async def _run_event(ctx: ToolCtx, action: str, payload: dict[str, Any], *, target: str, cvals: dict[str, Any],
                     preview: dict[str, Any]) -> ToolResult | dict[str, Any]:
    """An event write that may mail the guests (calendar/organizer.py), gated as
    calendar/routes.py `_gated_event` gates a token: the write class and calendar.invite
    (calendar.cancel for a cancel or delete). When either asks, or a recipient is new, one
    approval card of the invite class covers the write and the mail; its executor performs both.
    Without guest mail it is `_run_gated`.

    In a chat that has read local-only content, the write always asks unless every target
    calendar belongs to a local-only account (`calendar_local_copy_reason`)."""
    from mailr.calendar import organizer
    from mailr.core import permissions

    try:
        inv = await organizer.invite_context(ctx.db, ctx.p, action, payload)
    except ValueError as e:
        raise ToolError(str(e)) from None
    local = await calendar_local_copy_reason(ctx, list(cvals.get("calendars") or [cvals.get("calendar")]))
    if inv is None:
        return await _run_gated(ctx, action, payload, target=target, cvals=cvals, preview=preview, force_ask=local)
    payload = {**payload, "_itip": "ok"}
    actor = ctx.actor
    if actor.kind != "user":
        ictx = {**cvals, **inv["ctx"]}
        if actor.kind == "token":
            for cls in (action, inv["cls"]):
                need = permissions.CLASSES[cls][1]
                if need not in (actor.token.scopes or []):
                    raise ToolError(f"this token lacks the '{need}' scope needed for {cls}")
        w_eff, w_src, _ = await permissions.resolve(ctx.db, actor, action, cvals)
        i_eff, i_src, _ = await permissions.resolve(ctx.db, actor, inv["cls"], ictx)
        if w_eff == "deny" or i_eff == "deny":
            raise ToolError(f"{action if w_eff == 'deny' else inv['cls']} is not allowed for {actor.name} "
                            f"({w_src if w_eff == 'deny' else i_src})")
        if local or not (w_eff == "allow" and i_eff == "allow" and not ictx.get("new_recipients")):
            reason = local
            if reason is None and i_eff == "allow":
                reason = ("hard rule: first message to a new address" if ictx.get("new_recipients")
                          else f"{action} needs approval")
            rc = inv["recipients"]
            pv = {**preview, "kind": "invite",
                  "lines": list(preview.get("lines") or []) + [
                      ("Cancel for " if inv["cls"] == "calendar.cancel" else "Mail to ")
                      + ", ".join(rc[:10]) + (" …" if len(rc) > 10 else ""), f"From {inv['identity'].email}"],
                  "recipients": rc}
            decision = await permissions.check(ctx.db, actor, inv["cls"], target=target, ctx=ictx,
                                               payload={**payload, "op": action}, preview=pv, force_ask=reason)
            if isinstance(decision, permissions.Denied):
                raise ToolError(decision.reason)
            if isinstance(decision, permissions.Pending):
                await ctx.db.commit()
                return pending_result(decision.action, note=(
                    "This change mails the guests, so it waits for the user's approval in Mailr; approving "
                    "it saves the event and sends the mail. Do not retry it; tell the user what is waiting."))
    return await _execute(ctx, action, payload, target)


async def _identity_id(ctx: ToolCtx, v: Any) -> int | None:
    """An identity named by id or address (the organizer of an invitation)."""
    from mailr.models import Identity

    if v in (None, ""):
        return None
    rows = (await ctx.db.execute(select(Identity).where(Identity.user_id == ctx.p.user_id))).scalars().all()
    s = str(v).strip().lower()
    hit = next((i for i in rows if str(i.id) == s or (i.email or "").lower() == s), None)
    if hit is None:
        raise ToolError(f"unknown identity {v!r}")
    return hit.id


def _video_link(user: Any) -> str:
    import secrets

    tpl = str((user.settings or {}).get("calendar_video_template") or "https://meet.jit.si/mailr-{id}")
    ident = secrets.token_hex(4)
    return tpl.replace("{id}", ident) if "{id}" in tpl else f"{tpl.rstrip('/')}/{ident}"


_VIS = {"default": "", "public": "PUBLIC", "private": "PRIVATE", "confidential": "CONFIDENTIAL"}


async def _event_fields(ctx: ToolCtx, args: dict[str, Any]) -> dict[str, Any]:
    """Tool arguments in the REST field names of POST/PATCH /calendar/events."""
    from mailr.agent import agentwrite

    out: dict[str, Any] = {k: args[k] for k in ("summary", "start", "end", "location", "description", "calendar",
                                                "all_day", "categories", "color", "send_updates")
                           if k in args and args[k] is not None}
    from mailr.core import mdmail

    # Plain text in every calendar (and in the invitation's text): no Markdown marks.
    for k in ("summary", "location", "description"):
        if isinstance(out.get(k), str):
            out[k] = agentwrite.clean_text(ctx, out[k])
            out[k] = mdmail.to_text(out[k]) if k == "description" else mdmail.inline_text(out[k])
    tz = args.get("timezone") or args.get("tz")
    if tz:
        out["tz"] = str(tz)
    rule = args.get("rrule") if args.get("rrule") is not None else args.get("recurrence")
    if rule is not None:
        out["recurrence"] = str(rule).strip().removeprefix("RRULE:")
    if args.get("reminders") is not None:
        rem = args["reminders"]
        if not isinstance(rem, list) or not all(isinstance(x, int) for x in rem):
            raise ToolError("'reminders' must be a list of minutes before the start")
        out["reminders"] = rem
    if args.get("attendees") is not None:
        from mailr.agent.people_tools import expand

        out["attendees"] = (await expand(ctx, args["attendees"]))[0]
    conf = args.get("conference")
    if conf is True:
        out["url"] = _video_link(ctx.p.user)
    elif isinstance(conf, str) and conf.strip():
        out["url"] = conf.strip()
    elif conf is False or conf == "":
        out["url"] = ""
    tr = args.get("transparency")
    if tr is not None:
        if tr not in ("busy", "free"):
            raise ToolError("'transparency' must be busy or free")
        out["transparent"] = tr == "free"
    vis = args.get("visibility")
    if vis is not None:
        if vis not in _VIS:
            raise ToolError("'visibility' must be default, public, private or confidential")
        out["class"] = _VIS[vis]
    if args.get("identity") not in (None, ""):
        out["identity_id"] = await _identity_id(ctx, args["identity"])
    return out


_EVENT_PROPS: dict[str, Any] = {
    "summary": {"type": "string"},
    "start": {"type": "string", "description": "ISO date (all day) or datetime"},
    "end": {"type": "string"},
    "timezone": {"type": "string", "description": "IANA zone the times are written in (default the user's)"},
    "location": {"type": "string"}, "description": {"type": "string"},
    "calendar": {"type": "string", "description": "the calendar's name (get_calendar_defaults) or id; on update: move there"},
    "rrule": {"type": "string", "description": "repeat rule, e.g. FREQ=WEEKLY;BYDAY=MO;COUNT=10 (\"\" stops it)"},
    "reminders": {"type": "array", "items": {"type": "integer"},
                  "description": "minutes before the start; replaces the event's reminders. [] means no reminder "
                                 "at all (the event is marked silent, so the user's default does not fire either)"},
    "attendees": {"type": "array", "description": "guests: addresses or {email, name, role}; \"group:<name>\" adds a contact group; they get invitations",
                  "items": {"anyOf": [{"type": "string"}, {"type": "object"}]}},
    "conference": {"anyOf": [{"type": "boolean"}, {"type": "string"}],
                   "description": "true adds a video link; a URL uses that link; false removes it"},
    "color": {"type": "string", "description": "#rrggbb"},
    "transparency": {"type": "string", "enum": ["busy", "free"]},
    "visibility": {"type": "string", "enum": ["default", "public", "private", "confidential"]},
    "send_updates": {"type": "boolean", "description": "mail the guests (default true)"},
    "identity": {"type": "string", "description": "the sending address (organizer) of an invitation"},
}


async def _event_ref(ctx: ToolCtx, args: dict[str, Any]) -> tuple[str, str | None]:
    """(uid, occurrence): from the arguments, else the event focused on screen ("this meeting")."""
    uid = str(args.get("uid") or "").strip()
    occ = args.get("occurrence")
    if not uid:
        f = (ctx.ui.get("calendar") or {}).get("focused_event") or {}
        uid, occ = f.get("uid") or "", occ or f.get("occurrence")
    if not uid:
        raise ToolError("'uid' is required (no event is focused on screen)")
    _when(occ, "occurrence")
    return uid, occ or None


async def _refuse_phone(ctx: ToolCtx, uid: str) -> None:
    """A busy block from a phone calendar is never changed, deleted, cancelled or answered here."""
    from fastapi import HTTPException

    from mailr.calendar import phonecal

    try:
        await phonecal.refuse_uid(ctx.db, ctx.p.user_id, uid)
    except HTTPException as e:
        raise ToolError(f"{e.detail} It is a busy time from a phone calendar; it changes only on the phone.") from None


def _task_uid(ctx: ToolCtx, args: dict[str, Any]) -> str:
    """The task: from the arguments, else the focused (or single selected) task on screen."""
    uid = str(args.get("uid") or "").strip()
    if not uid:
        t = ctx.ui.get("tasks") or {}
        sel = t.get("selected") or []
        uid = t.get("focused") or (sel[0] if len(sel) == 1 else "")
    if not uid:
        raise ToolError("'uid' is required (no task is focused on screen)")
    return uid


def _task_default_text(cfg: dict[str, Any]) -> str:
    """Tasks keep the planner's default (nothing is written into the task): mailr reminds at the
    due time, or at the morning hour on the day of a date-only due."""
    if not cfg["tasks"] or not cfg["default_task"]:
        return "none unless the task has reminders of its own"
    return (f"at the due time (at {cfg['morning_hour']}:00 on the day for a date-only due), in Mailr only, "
            "unless the task has reminders of its own")
