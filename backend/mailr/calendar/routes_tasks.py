"""Tasks API (sections 2.2, 3 and 6).

Smart lists, filters, sorts and keyset paging; counts and tags; create, change, complete
(repeating tasks advance), skip, move (list, parent, manual order), delete with
subtasks, and bulk actions with per-task results. Writes go through `_gated` (permissions for
tokens and the assistant) and run in calendar/todo.py.
"""

from __future__ import annotations

import builtins
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mailr import i18n
from mailr.api import contract as C
from mailr.calendar import client as cal
from mailr.calendar import mirror, todo, writer
from mailr.calendar.describe import change_lines, name_list
from mailr.calendar.routes import (
    _UID,
    EXECUTORS,
    _check_rrule,
    _check_tz,
    _check_when,
    _collection_visible,
    _etag,
    _link,
    _need_calendar,
    _own_conversation,
    _with_source_email,
    where_ctx,
    where_is,
)
from mailr.core import live, permissions
from mailr.core.audit import audit
from mailr.core.auth import Principal, scope
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import User

router = APIRouter(tags=["tasks"])


_TOKEN_TEXT = "hard rule: task text written through an API token asks first"


async def gated(db: AsyncSession, p: Principal, action: str, payload: dict[str, Any], *, target: str,
                ctx: dict[str, Any], preview: dict[str, Any], ask_by_default: str | None = None):
    """Run a task or collection change now (signed-in user, or an allowed token) or queue it
    for approval (HTTP 202). `ask_by_default` (a reason) makes the class ask when no rule
    decides, for the parts of a class that default to ask (renaming or
    deleting a list, a bulk delete)."""
    if p.is_token:
        actor = permissions.Actor.from_principal(p)
        decision = await permissions.check(db, actor, action, target=target, ctx=ctx, payload=payload,
                                           preview=preview, ask_by_default=ask_by_default)
        permissions.raise_for(decision)
        if isinstance(decision, permissions.Pending):
            await audit(db, p, action, target, payload, {"pending": decision.action.id})
            await db.commit()
            return JSONResponse(permissions.pending_response(decision.action), status_code=202)
    from mailr.core import activity

    # Settings → Activity (core/activity.py): what the Undo needs, read before the change.
    before = await activity.snapshot(db, p.user_id, action, payload, p)
    try:
        result = await EXECUTORS[action](db, p.user, permissions.stamp(payload, p))
    except HTTPException:
        raise
    except writer.Conflict as c:
        await db.commit()  # the mirror keeps the server copy it fetched
        return JSONResponse(c.body(), status_code=409)
    except live.LiveConflict as c:
        await db.rollback()
        return JSONResponse(c.body(), status_code=409)
    except LookupError as e:
        raise HTTPException(404, str(e).strip("'")) from None
    except Exception as e:  # noqa: BLE001
        raise _need_calendar(e) from None
    act_entry = activity.calendar_task(action, payload, preview, result, before)
    # Every change is logged now, the user's own too (Activity); a session's row keeps the ids only.
    inputs = payload if p.is_token else {"uid": payload.get("uid"), "fields": sorted(payload.get("changes") or {})}
    await audit(db, p, action, target, inputs, {"ok": True, **({activity.KEY: act_entry} if act_entry else {})},
                always=True)
    await db.commit()
    return result


def _err(e: Exception) -> HTTPException:
    if isinstance(e, LookupError):
        return HTTPException(404, str(e).strip("'"))
    return _need_calendar(e)


def attach(parent: APIRouter, *children: APIRouter) -> None:
    """Add the routes of `children` to the calendar router (calendar/routes.py). A route here
    replaces an older one there with the same method and path (the task routes moved here)."""
    mine = {(r.path, m) for c in children for r in c.routes for m in getattr(r, "methods", ())}
    parent.routes[:] = [r for r in parent.routes
                        if not any((getattr(r, "path", None), m) in mine for m in getattr(r, "methods", ()) or ())]
    for c in children:
        parent.include_router(c)


# ---------------- reads ----------------


# ACT task.search @api
@router.get("/tasks", response_model=C.calendar.TaskPage, **C.TYPED)
async def list_tasks(status: str = "open", q: str = "", list_: list[str] | None = Query(None, alias="list"),
                     smart: str | None = None, tag: list[str] | None = Query(None), priority: str | None = None,
                     parent: str | None = None, has_due: bool | None = None, due_before: str | None = None,
                     due_after: str | None = None, start_before: str | None = None,
                     include_future: bool | None = None, sort: str | None = None, cursor: str | None = None,
                     limit: int = Query(200, ge=1, le=200), p: Principal = Depends(scope("read")),
                     db: AsyncSession = Depends(get_db)):
    """Tasks from the mirror. `smart`: today, overdue, upcoming, scheduled, nodate (no_date),
    high, email, all, completed (server-defined, so every client shows the same rows); rows of
    Today and Upcoming carry `section`. `q` takes the search operators (list:, tag: or @tag,
    due:<fri, is:done, is:overdue, has:email, pri:high)."""
    if status not in ("open", "completed", "all"):
        raise HTTPException(400, "status must be open, completed, or all")  # i18n: internal
    _check_when(due_before, due_after, start_before)
    try:
        res = await todo.list_tasks(db, p.user, p, smart=smart, status=status, lists=list_, tags_=tag, q=q,
                                    priority=priority, parent=parent, has_due=has_due, due_before=due_before,
                                    due_after=due_after, start_before=start_before, include_future=include_future,
                                    sort=sort, cursor=cursor, limit=limit)
    except Exception as e:  # noqa: BLE001
        raise _err(e) from None
    items = await _with_source_email(db, p, res["items"], "task")
    out = {"connected": await cal.has_calendar(db, p.user_id, p=p), "items": items, "next_cursor": res["next_cursor"],
           "sort": res.get("sort"), "sync": await mirror.sync_state(db, p.user_id, p)}
    if smart in ("today", "upcoming"):
        secs: dict[str, int] = {}
        for t in items:
            secs[t.get("section") or ""] = secs.get(t.get("section") or "", 0) + 1
        out["sections"] = secs
    await db.commit()
    return out


@router.get("/tasks/counts", response_model=C.calendar.TaskCounts, **C.TYPED)
async def task_counts(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """Open tasks per smart list, per list (by URL; `lists_by_id` by calendar_id) and per tag."""
    try:
        out = await todo.counts(db, p.user, p)
    except Exception as e:  # noqa: BLE001
        raise _err(e) from None
    await db.commit()
    return out


@router.get("/tasks/tags")
async def task_tags(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    user = await db.get(User, p.user_id)
    out = await todo.tags(db, user, p)
    await db.commit()
    return out


@router.get("/tasks/{uid}")
async def get_task(uid: str, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """One task (deep links such as /tasks/task/<uid>): the task routes' shape, with the full
    notes, `subtask_count` and the parent's title (`parent_summary`)."""
    from mailr.calendar import tz as tzmod

    obj = await writer.find(db, p.user_id, uid, "task", p)
    if obj is None:
        raise ApiError(404, "calendar.task_not_found")
    user_tz = await tzmod.user_zone(db, p.user_id)
    t = await todo.task_dict(db, obj, user_tz)
    t["notes"] = obj.description or ""
    t["parent_summary"] = None
    if obj.parent_uid:
        par = await writer.find(db, p.user_id, obj.parent_uid, "task", p)
        t["parent_summary"] = (par.summary or "") if par is not None else None
    [t] = await _with_source_email(db, p, [t], "task")
    await db.commit()
    return t


class TagPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)  # rename on every task
    color: str | None = Field(None, max_length=40)  # mailr-side ("" clears)
    order: int | None = None
    pinned: bool | None = None


# ACT tag.rename @api, ACT tag.color @api
@router.patch("/tasks/tags/{name}")
async def patch_tag(name: str, body: TagPatch, p: Principal = Depends(scope("calendar")),
                    db: AsyncSession = Depends(get_db)):
    """Rename a tag (rewrites CATEGORIES on every task that has it: inside the request for up to
    25 tasks, else as a background job), or set its colour, order or pin (kept on
    mailr's side)."""
    payload = {"op": "tag", "tag": name, **{("new_name" if k == "name" else k): v
                                            for k, v in body.model_dump(exclude_none=True).items()}}
    lines = [f"{k}: {v}" for k, v in body.model_dump(exclude_none=True).items()]
    return await gated(db, p, "tasks.bulk", payload, target=f"tag:{name}", ctx={},
                       preview={"title": f"Change tag “{name}”", "kind": "task", "lines": lines})


# ACT tag.delete @api
@router.delete("/tasks/tags/{name}")
async def delete_tag(name: str, p: Principal = Depends(scope("calendar")), db: AsyncSession = Depends(get_db)):
    """Remove the tag from every task (and forget its colour)."""
    return await gated(db, p, "tasks.bulk", {"op": "tag_delete", "tag": name}, target=f"tag:{name}", ctx={},
                       preview={"title": f"Delete tag “{name}”", "kind": "task", "danger": True,
                                "lines": [f"Removes {name} from every task"]},
                       ask_by_default="removing a tag from every task asks by default")


# ---------------- create and change ----------------


class TaskIn(BaseModel):
    summary: str = Field(min_length=1, max_length=500)
    due: str | None = None
    notes: str = ""
    list: str | None = None
    priority: int | None = Field(None, ge=1, le=9)
    parent: str | None = Field(None, max_length=300)  # UID of the parent task (a subtask)
    # `list` is a field name here, so the builtin is spelled out.
    reminders: builtins.list[int] = Field(default_factory=builtins.list, max_length=10)  # minutes before due
    reminders_at: builtins.list[str] | None = Field(None, max_length=10)  # absolute reminders (instants)
    uid: str | None = Field(None, pattern=_UID)
    connector_id: int | None = None
    conversation_id: int | None = None
    start: str | None = None  # deferred until then
    recurrence: str | None = Field(None, validation_alias=AliasChoices("recurrence", "rrule"))
    repeat_from: str | None = Field(None, pattern="^(due|completion)$")
    categories: builtins.list[str] | None = Field(None, max_length=50,
                                                  validation_alias=AliasChoices("categories", "tags"))
    sort_order: int | None = None
    tz: str | None = Field(None, max_length=64)


async def create_task_for(db: AsyncSession, p: Principal, body: TaskIn):
    _check_when(body.due, body.start, *(body.reminders_at or []))
    _check_tz(body.tz)
    if body.recurrence:
        _check_rrule(body.recurrence)
    await _own_conversation(db, p, body.conversation_id)
    await _collection_visible(db, p, body.list)
    if p.is_token:
        # A token's text never carries an image into the task pane; the write asks unless a rule allows.
        from mailr.agent.agentwrite import strip_markdown_images

        body.summary, body.notes = strip_markdown_images(body.summary), strip_markdown_images(body.notes)
    payload = {k: v for k, v in body.model_dump().items() if v not in (None, "")}
    target = body.list or (await cal.defaults(db, p.user_id, p=p))["task_list"] or "default task list"
    return await gated(db, p, "tasks.create", payload, target=target, ctx={"list": target},
                       preview={"title": f"Add task “{body.summary[:80]}”", "kind": "task",
                                "title_msg": i18n.msg("approvals.task.add", name=i18n.msg(
                                    "approvals.event.quoted", name=body.summary[:80])),
                                "task": await name_list(db, p.user_id, {**payload, "list": target}, p),
                                "lines": [x for x in (body.summary, body.due and f"due {body.due}", body.notes) if x]},
                       ask_by_default=_TOKEN_TEXT if p.is_token else None)


# ACT task.create @api, ACT task.subtask_add @api, ACT task.duplicate @api (a copy is a create)
@router.post("/tasks")
async def create_task(body: TaskIn, p: Principal = Depends(scope("calendar")), db: AsyncSession = Depends(get_db)):
    return await create_task_for(db, p, body)


class TaskPatch(BaseModel):
    completed: bool | None = None
    status: str | None = None  # "completed" | "open" ("needs-action" is accepted as open)
    occurrence: str | None = None  # the due this completion is for (idempotent per uid + due)
    summary: str | None = None
    due: str | None = None
    notes: str | None = None
    priority: int | None = Field(None, ge=0, le=9)
    list: str | None = None  # move to another task list
    clear_due: bool = False
    parent: str | None = Field(None, max_length=300)  # "" detaches a subtask
    reminders: builtins.list[int] | None = Field(None, max_length=10)
    reminders_at: builtins.list[str] | None = Field(None, max_length=10)
    start: str | None = None
    clear_start: bool = False
    recurrence: str | None = Field(None, validation_alias=AliasChoices("recurrence", "rrule"))  # "" stops it
    repeat_from: str | None = Field(None, pattern="^(due|completion)$")
    categories: builtins.list[str] | None = Field(None, max_length=50,
                                                  validation_alias=AliasChoices("categories", "tags"))
    sort_order: int | None = None
    tz: str | None = Field(None, max_length=64)
    etag: str | None = None
    # The version the editor started from (core/live.py): changes saved since then are merged in.
    base_version: int | None = None


# ACT task.complete @api, ACT task.reopen @api, ACT task.reschedule @api, ACT task.edit @api, ACT task.set_repeat @api,
# ACT task.set_start @api, ACT task.set_priority @api, ACT task.tag @api, ACT task.remind_at @api
@router.patch("/tasks/{uid}")
async def patch_task(uid: str, body: TaskPatch, p: Principal = Depends(scope("calendar")),
                     db: AsyncSession = Depends(get_db), if_match: str | None = Header(None)):
    """Change a task. `completed: true` on a repeating task advances it and answers with the
    task plus `next` and `history_uid`; `completed: false` on it undoes the last advance (the
    due and COUNT come back, the history copy is deleted)."""
    _check_when(body.due, body.start, body.occurrence, *(body.reminders_at or []))
    _check_tz(body.tz)
    if body.recurrence:
        _check_rrule(body.recurrence)
    await _collection_visible(db, p, body.list)
    changes: dict[str, Any] = {k: v for k, v in body.model_dump().items()
                               if v is not None and k in ("summary", "notes", "priority", "list", "due", "parent",
                                                          "reminders", "reminders_at", "start", "recurrence",
                                                          "repeat_from", "categories", "sort_order", "tz")}
    st = body.status.lower() if body.status else None
    if body.completed is not None:
        changes["status"] = "completed" if body.completed else "open"
    elif st:
        changes["status"] = "completed" if st == "completed" else "open"
    if body.clear_due:
        changes["due"] = None
    if body.clear_start:
        changes["start"] = None
    if not changes:
        raise ApiError(400, "common.nothing_to_change")
    free_text = p.is_token and bool(changes.get("summary") or changes.get("notes"))
    if free_text:
        from mailr.agent.agentwrite import strip_markdown_images

        for k in ("summary", "notes"):
            if changes.get(k):
                changes[k] = strip_markdown_images(changes[k])
    action = "tasks.complete" if set(changes) == {"status"} else "tasks.update"
    wctx = where_ctx(await where_is(db, p, uid, "VTODO"), body.list, "list") if p.is_token else {}
    payload: dict[str, Any] = {"uid": uid, "changes": changes}
    if etag := _etag(if_match, body.etag):
        payload["etag"] = etag
    if body.base_version is not None:
        payload["base_version"] = body.base_version
    if body.occurrence:
        payload["occurrence"] = body.occurrence
    title, tk = "Change task", "change"
    if action == "tasks.complete":
        title, tk = ("Complete task", "complete") if changes["status"] == "completed" else ("Reopen task", "reopen")
    return await gated(db, p, action, payload, target=uid, ctx=wctx,
                       preview={"title": title, "title_msg": i18n.msg(f"approvals.task.{tk}"), "kind": "task",
                                "task": (shown := await name_list(db, p.user_id, {"uid": uid, **changes}, p)),
                                "lines": change_lines(changes, shown.get("list_name"))},
                       ask_by_default=_TOKEN_TEXT if free_text else None)


# ACT task.delete @api
@router.delete("/tasks/{uid}")
async def delete_task(uid: str, etag: str | None = None, cascade: bool = False,
                      p: Principal = Depends(scope("calendar")), db: AsyncSession = Depends(get_db),
                      if_match: str | None = Header(None)):
    """Delete a task. `cascade=true` deletes its subtasks too (leaves first); without it they
    are detached and stay. → {ok, uid, deleted: [subtask uids], detached: [uids]}."""
    wctx = where_ctx(await where_is(db, p, uid, "VTODO"), None, "list") if p.is_token else {}
    payload: dict[str, Any] = {"uid": uid, "cascade": cascade}
    if v := _etag(if_match, etag):
        payload["etag"] = v
    return await gated(db, p, "tasks.delete", payload, target=uid, ctx=wctx,
                       preview={"title": "Delete task" + (" and its subtasks" if cascade else ""), "kind": "task",
                                "title_msg": i18n.msg("approvals.task.delete_cascade" if cascade else "approvals.task.delete"),
                                "danger": True, "task": {"uid": uid}, "lines": [uid]})


class MoveIn(BaseModel):
    list: str | None = None
    before: str | None = Field(None, max_length=300)
    after: str | None = Field(None, max_length=300)
    parent: str | None = Field(None, max_length=300)  # "" makes it a top-level task
    etag: str | None = None


# ACT task.move_list @api, ACT task.reorder @api, ACT task.reparent @api
@router.post("/tasks/{uid}/move")
async def move_task(uid: str, body: MoveIn, p: Principal = Depends(scope("calendar")),
                    db: AsyncSession = Depends(get_db), if_match: str | None = Header(None)):
    """Move to another list (subtasks follow), under another parent (one level) or to a place
    among the siblings. The sort order is the midpoint between the neighbours; when there is no
    room the level is renumbered (`renumbered`, or `renumber_queued` for long lists)."""
    if body.list is None and body.before is None and body.after is None and body.parent is None:
        raise ApiError(400, "tasks.nothing_to_move")
    await _collection_visible(db, p, body.list)
    wctx = where_ctx(await where_is(db, p, uid, "VTODO"), body.list, "list") if p.is_token else {}
    payload: dict[str, Any] = {"op": "move", "uid": uid, **body.model_dump(exclude={"etag"}, exclude_none=True)}
    if etag := _etag(if_match, body.etag):
        payload["etag"] = etag
    return await gated(db, p, "tasks.update", payload, target=uid, ctx=wctx,
                       preview={"title": "Move task", "title_msg": i18n.msg("approvals.task.move"), "kind": "task",
                                "task": (shown := await name_list(db, p.user_id, {"uid": uid, "list": body.list}
                                                                  if body.list else {"uid": uid}, p)),
                                "lines": change_lines(body.model_dump(exclude_none=True), shown.get("list_name"))})


class SkipIn(BaseModel):
    etag: str | None = None
    occurrence: str | None = None


# ACT task.skip @api
@router.post("/tasks/{uid}/skip")
async def skip_task(uid: str, body: SkipIn | None = None, p: Principal = Depends(scope("calendar")),
                    db: AsyncSession = Depends(get_db), if_match: str | None = Header(None)):
    """Skip this occurrence of a repeating task: it advances without a completed copy.
    → {task, next}. A reopen (`PATCH {completed: false}`) undoes it."""
    body = body or SkipIn()
    wctx = where_ctx(await where_is(db, p, uid, "VTODO"), None, "list") if p.is_token else {}
    payload: dict[str, Any] = {"op": "skip", "uid": uid}
    if etag := _etag(if_match, body.etag):
        payload["etag"] = etag
    if body.occurrence:
        payload["occurrence"] = body.occurrence
    res = await gated(db, p, "tasks.complete", payload, target=uid, ctx=wctx,
                      preview={"title": "Skip this occurrence", "title_msg": i18n.msg("approvals.task.skip"), "kind": "task",
                               "task": {"uid": uid}, "lines": [uid]})
    if isinstance(res, dict):
        return {"task": res, "next": res.get("next")}
    return res


class BulkIn(BaseModel):
    uids: builtins.list[str] = Field(min_length=1, max_length=500)
    op: str = Field(pattern="^(complete|reopen|reschedule|move|tag|untag|priority|delete)$")
    args: dict[str, Any] = Field(default_factory=dict)


# ACT task.bulk_complete @api, ACT task.bulk_reschedule @api, ACT task.bulk_move @api, ACT task.bulk_tag @api,
# ACT task.bulk_priority @api, ACT task.bulk_delete @api, ACT task.clear_completed @api
@router.post("/tasks/bulk")
async def bulk_tasks(body: BulkIn, p: Principal = Depends(scope("calendar")), db: AsyncSession = Depends(get_db)):
    """One action on many tasks, each on its own: → {op, ok: [uid], failed: [{uid, error,
    detail, server?}], items: [task]}. `args`: reschedule {due} (absolute; null clears),
    {dues: {uid: due}} or {shift_days} (times kept); move {list}; tag/untag {tag} or tag
    {add: [], remove: []}; priority {priority}; delete {cascade (default true)}; and
    {etags: {uid: etag}} for versions."""
    a = body.args
    if body.op == "reschedule":
        _check_when(a.get("due"), *(a.get("dues") or {}).values())
    if body.op == "move":
        if not a.get("list"):
            raise HTTPException(400, "move needs args.list")  # i18n: internal
        await _collection_visible(db, p, a["list"])
    payload = {"op": "bulk", "bulk_op": body.op, "uids": body.uids, "args": a}
    ctx: dict[str, Any] = {}
    if p.is_token and body.op == "move":
        ctx = {"list": a["list"], "lists": [a["list"]]}
    return await gated(db, p, "tasks.bulk", payload, target=f"{len(body.uids)} tasks", ctx=ctx,
                       preview={"title": f"{body.op.capitalize()} {len(body.uids)} tasks", "kind": "task",
                                "danger": body.op == "delete", "lines": body.uids[:20]},
                       ask_by_default="a bulk delete asks by default" if body.op == "delete" else None)


# ---------------- executors (run now, or after approval) ----------------


async def _x_tasks_create(db, user, payload):
    p = await permissions.requester(db, user, payload)
    t = await todo.create(db, user, p, payload)
    await _link(db, user.id, payload.get("conversation_id"), "task", t)
    return t


async def _x_tasks_update(db, user, payload):
    p = await permissions.requester(db, user, payload)
    if payload.get("op") == "move":
        return await todo.move(db, user, p, payload["uid"], list_url=payload.get("list"), before=payload.get("before"),
                               after=payload.get("after"), parent=payload.get("parent"), etag=payload.get("etag"))
    return await todo.update(db, user, p, payload["uid"], dict(payload.get("changes") or {}),
                             etag=payload.get("etag"), occurrence=payload.get("occurrence"),
                             base_version=payload.get("base_version"))


async def _x_tasks_complete(db, user, payload):
    p = await permissions.requester(db, user, payload)
    if payload.get("op") == "skip":
        return await todo.complete(db, user, p, payload["uid"], done=True, skip=True, base=payload.get("occurrence"),
                                   etag=payload.get("etag"))
    return await todo.update(db, user, p, payload["uid"], dict(payload.get("changes") or {}),
                             etag=payload.get("etag"), occurrence=payload.get("occurrence"))


async def _x_tasks_delete(db, user, payload):
    p = await permissions.requester(db, user, payload)
    return await todo.delete(db, user, p, payload["uid"], cascade=bool(payload.get("cascade")),
                             etag=payload.get("etag"))


async def _x_tasks_bulk(db, user, payload):
    from mailr.core.jobs import enqueue

    p = await permissions.requester(db, user, payload)
    user = await db.get(User, user.id)
    op = payload.get("op")
    if op == "bulk":
        return await todo.bulk(db, user, p, list(payload["uids"]), payload["bulk_op"], dict(payload.get("args") or {}))
    name = str(payload["tag"])
    meta = todo.tag_meta(user)
    key = next((k for k in meta if k.lower() == name.lower()), name)
    if op == "tag_delete":
        rows = await todo.tagged(db, user.id, p, name)
        if len(rows) > todo.INLINE_RETAG:
            await enqueue(db, "calendar.retag", {"user_id": user.id, "name": name, "new_name": None}, user_id=user.id)
            return {"ok": True, "name": name, "queued": True, "count": len(rows)}
        res = await todo.retag_all(db, user, p, name, None)
        return {"ok": True, "name": name, "queued": False, **res}
    out: dict[str, Any] = {"ok": True, "queued": False}
    m = dict(meta.get(key) or {})
    for k in ("color", "order", "pinned"):
        if k in payload:
            m[k] = payload[k] if payload[k] != "" else None
    if any(k in payload for k in ("color", "order", "pinned")):
        from mailr.core import usersettings

        meta[key] = m
        # This tag's entry only: another tag's change at the same time keeps its own.
        await usersettings.patch_in(db, user.id, "task_tags", {key: m})
    new = payload.get("new_name")
    if op == "tag" and new and new != name:
        rows = await todo.tagged(db, user.id, p, name)
        if len(rows) > todo.INLINE_RETAG:
            await enqueue(db, "calendar.retag", {"user_id": user.id, "name": name, "new_name": new}, user_id=user.id)
            out.update(queued=True, count=len(rows))
        else:
            out.update(await todo.retag_all(db, user, p, name, new))
        name = new
    tags = await todo.tags(db, user, p)
    out["tag"] = next((t for t in tags if t["name"].lower() == name.lower()),
                      {"name": name, "count": 0, **{k: m.get(k) for k in ("color", "order")},
                       "pinned": bool(m.get("pinned"))})
    return out


for _a, _fn in {"tasks.create": _x_tasks_create, "tasks.update": _x_tasks_update,
                "tasks.complete": _x_tasks_complete, "tasks.delete": _x_tasks_delete,
                "tasks.bulk": _x_tasks_bulk}.items():
    EXECUTORS[_a] = _fn
    permissions.executor(_a)(_fn)
