"""Write side: actions, labels, drafts, send, outbox."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.api.schemas import label_out, message_out, outbox_out
from mailr.config import get_settings
from mailr.core import actions as act
from mailr.core import label_colours, permissions, publicid, threading
from mailr.core.audit import audit
from mailr.core.auth import Principal, current, scope
from mailr.core.jobs import enqueue, enqueue_returning
from mailr.core.publicid import ConvId, MsgId
from mailr.core.sendgate import SendGate
from mailr.core.store import user_store
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import ApiToken, Connector, Identity, Label, Message, Outbox

router = APIRouter(tags=["mail"])


class ListQuery(BaseModel):
    """The list a bulk action covers ("Select all N conversations")."""
    view: str = "inbox"
    q: str = ""
    section: str | None = None
    sections: str = ""
    exclude: str = ""
    category: str | None = None
    layout: str | None = None
    tab: str | None = None
    sec: str | None = None
    # "Select all N" with some rows unchecked afterwards: these are left out.
    exclude_ids: list[int] = Field(default_factory=list, max_length=10000)


class ActionIn(BaseModel):
    ids: list[int] = []
    query: ListQuery | None = None
    action: str
    label_ids: list[int] | None = None
    label_id: int | None = None
    from_label_id: int | None = None
    until: str | None = None
    category: str | None = None
    labels: dict[str, list[int]] | None = None
    block_sender: bool = False
    scope: str | None = None
    # The undo of Mark important / not important (core/actions.restore_importance): the record
    # the toggle returned, or the select-all job (`job`) whose record the server kept.
    importance: dict[str, Any] | None = None
    job: int | None = None
    # The undo of a category change (core/actions.restore_category): the record it returned.
    categories: dict[str, Any] | None = None
    # Show in tab (core/tabplace.py): the id of a tab of the active layout.
    tab: str | None = None


def _pending(pa) -> JSONResponse:
    return JSONResponse(permissions.pending_response(pa), status_code=202)


async def _run_action(body: ActionIn, p: Principal, db: AsyncSession, by: str):
    if len(body.ids) > 5000:
        raise HTTPException(400, "too many ids")  # i18n: internal
    if body.action in ("restore_importance", "restore_category") and body.job is not None:
        msgs = await _bulk_undo_messages(body, p, db)
    else:
        msgs = await act.messages_for(db, p, conv_ids=body.ids if by == "conversation" else None,
                                      msg_ids=body.ids if by == "message" else None)
    if body.action in ("star", "unread") and by == "message":
        body.scope = "messages"
    args = body.model_dump(exclude={"ids", "action"})
    if p.is_token:
        # Agents: the permission engine decides. The affected message
        # ids are fixed now, so an approval later acts on exactly what the agent saw.
        await act.check_agent_labels(db, p.user_id, body.action, args)
        cls = await act.classify(db, p.user_id, msgs, body.action, args)
        target_labels = list(body.label_ids or ([body.label_id] if body.label_id else []))
        decision = await permissions.check(
            db, permissions.Actor.from_principal(p), cls,
            target=f"{len(msgs)} message(s)",
            ctx={"connectors": sorted({m.connector_id for m in msgs if m.connector_id}),
                 "labels": target_labels},
            payload={"message_ids": [m.id for m in msgs], "action": body.action, "args": args},
            preview=await act.preview(db, msgs, body.action, args))
        permissions.raise_for(decision)
        if isinstance(decision, permissions.Pending):
            await audit(db, p, f"{by}s.{body.action}", ",".join(map(str, body.ids[:50])),
                        body.model_dump(exclude_none=True), {"pending": decision.action.id})
            await db.commit()
            return _pending(decision.action)
    store = await user_store(db, p.user)
    ctx = act.ActionContext(db, store, p)
    res = await act.apply(ctx, msgs, body.action, args)
    # Settings → Activity (core/activity.py): the change, and its undo with internal ids.
    from mailr.core import activity

    act_entry = activity.mail(body.action, msgs, body.ids, by, res.get("undo"))
    # The undo payload is sent back as is, so its ids are in public form (core/publicid.py):
    # `labels` is keyed by message id, `ids` are conversations or messages as `by` says.
    if res.get("undo") and res["undo"].get("labels"):
        res["undo"]["labels"] = {str(publicid.encode("message", int(k))): v for k, v in res["undo"]["labels"].items()}
    if res.get("undo"):
        res["undo"]["ids"] = [ConvId(i) if by == "conversation" else MsgId(i) for i in body.ids]
        res["undo"]["by"] = by
    # Every action is logged, the user's own too: which conversations moved, and who moved them,
    # is what an incident needs (1 Oct 2026: deleted mail came back and no record showed when it
    # was deleted). A session's row keeps only the ids, so the insert stays small.
    inputs = body.model_dump(exclude_none=True) if p.is_token else {
        "ids": body.ids[:200], **{k: v for k, v in body.model_dump(
            exclude_none=True, include={"label_ids", "label_id", "from_label_id", "until", "category", "tab"}).items()}}
    await audit(db, p, f"{by}s.{body.action}", ",".join(map(str, body.ids[:50])),
                inputs, {"affected": res["affected"], "messages": [m.id for m in msgs[:200]],
                         **({activity.KEY: act_entry} if act_entry else {})}, always=True)
    await db.commit()
    return res


async def _bulk_undo_messages(body: ActionIn, p: Principal, db: AsyncSession) -> list[Message]:
    """The undo of a select-all importance toggle or category change that ran as a job: its
    record is in the job's payload and its undo parts (core/tasks.bulk_conversations). It is used
    once."""
    from mailr.models import Job

    key, kind, field = ("undo", "importance", "importance") if body.action == "restore_importance" \
        else ("undo_category", "category", "categories")
    j = await db.get(Job, body.job)
    rec = (j.payload or {}).get(key) if j is not None else None
    if j is None or j.user_id != p.user_id or j.kind != "bulk.conversations" or not rec:
        raise ApiError(404, "mail.nothing_to_undo")
    j.payload = {k: v for k, v in j.payload.items() if k != key}
    if rec.get("parts"):
        # Kept one part per chunk (core/tasks.bulk_conversations).
        merged: dict[str, list] = {}
        for part in await _take_undo_parts(db, j.id, kind):
            for k, v in part.items():
                merged[k] = merged.get(k, []) + list(v or [])
        rec = merged
    setattr(body, field, rec)
    body.ids = []
    return await act.messages_for(db, p, msg_ids=[int(x) for x in rec.get("message_ids") or []])


async def _take_undo_parts(db: AsyncSession, job_id: int, kind: str) -> list[dict[str, Any]]:
    """A bulk job's undo records of one kind, in chunk order; they are deleted (used once)."""
    from sqlalchemy import delete

    from mailr.models import JobUndoPart

    rows = (await db.execute(select(JobUndoPart.data).where(JobUndoPart.job_id == job_id, JobUndoPart.kind == kind)
                             .order_by(JobUndoPart.id))).scalars().all()
    await db.execute(delete(JobUndoPart).where(JobUndoPart.job_id == job_id, JobUndoPart.kind == kind))
    return list(rows)


BULK_INLINE_MAX = 500


# ACT mail.archive @web.section, ACT mail.delete @web.section, ACT mail.read @web.section, ACT mail.snooze @web.section, ACT mail.label @web.section (and the rest of core/actions.apply)
@router.post("/conversations/actions")
async def conversation_actions(body: ActionIn, p: Principal = Depends(scope("label")),
                               db: AsyncSession = Depends(get_db)):
    if body.query is not None:
        return await _bulk_by_query(body, p, db)
    if body.job is not None and body.action not in ("restore_importance", "restore_category"):
        return await _undo_bulk_job(body, p, db)
    return await _run_action(body, p, db, "conversation")


# ACT mail.archive, ACT mail.delete, ACT mail.read, ACT mail.unread @web.section, ACT mail.snooze, ACT mail.move @web.section,
# ACT mail.label, ACT mail.important @web.section, ACT mail.mute @web.section (a section's actions, web.section: the list as a
# query above, and the Undo of a job here)
async def _undo_bulk_job(body: ActionIn, p: Principal, db: AsyncSession):
    """The Undo of a finished select-all job (archive, delete, move, labels, snooze, mute,
    read/unread, star): a new job over the same conversations runs the reverse the first job kept
    (core/tasks.bulk_conversations, `reverse`). It is used once, and only by the job's owner."""
    from mailr.models import Job

    if p.is_token:
        raise HTTPException(403, "agents act on explicit ids")  # i18n: internal
    j = await db.get(Job, body.job)
    rev = (j.payload or {}).get("reverse") if j is not None else None
    if j is None or j.user_id != p.user_id or j.kind != "bulk.conversations" or j.status != "done" or not rev \
            or rev.get("action") != body.action:
        raise ApiError(404, "mail.nothing_to_undo")
    j.payload = {k: v for k, v in j.payload.items() if k != "reverse"}
    ids = list(j.payload.get("ids") or [])
    labels = dict(rev.get("labels") or {})
    if rev.get("parts"):
        # The labels before, one part per chunk (core/tasks.bulk_conversations).
        for part in await _take_undo_parts(db, j.id, "reverse"):
            labels.update(part)
    args = {"labels": labels} if labels else {}
    job = await enqueue_returning(db, "bulk.conversations",
                                  {"ids": ids, "action": rev["action"], "args": args, "done": 0, "total": len(ids),
                                   "is_undo": True},
                                  user_id=p.user_id)
    await audit(db, p, f"conversations.{rev['action']}.bulk", f"{len(ids)} conversations",
                {"undo_of_job": j.id}, {"job": job}, always=True)
    await db.commit()
    return JSONResponse({"ok": True, "job": job, "total": len(ids), "status": "running"}, status_code=202)


async def _bulk_by_query(body: ActionIn, p: Principal, db: AsyncSession):
    """Act on every conversation of a list. Small lists run now (with undo); large ones run as a
    background job over a snapshot of the matching ids, reporting progress over SSE
    ({"type": "bulk", "job", "done", "total", "status"}) and at GET /bulk/{job}."""
    from mailr.api.mail_read import conversation_conditions
    from mailr.models import Conversation

    if p.is_token:
        raise HTTPException(403, "agents act on explicit ids")  # i18n: internal
    if body.action == "delete_forever":
        raise ApiError(400, "mail.no_delete_forever_list")
    q = body.query
    conds = await conversation_conditions(db, p, q.view, q.q, q.section, q.sections, q.exclude, q.category,
                                          q.layout, q.tab, q.sec)
    if conds is None:
        return {"ok": True, "affected": 0, "undo": None}
    ids = list((await db.execute(select(Conversation.id).where(*conds).order_by(Conversation.id)
                                 .limit(200_000))).scalars())
    if q.exclude_ids:
        left_out = set(q.exclude_ids)
        ids = [i for i in ids if i not in left_out]
    return await run_on_conversations(body, p, db, ids)


async def run_on_conversations(body: ActionIn, p: Principal, db: AsyncSession, ids: list[int],
                               origin: dict[str, Any] | None = None):
    """Run an action on a fixed list of conversations: at once (with its Undo record) up to
    BULK_INLINE_MAX, otherwise as a bulk job (progress at GET /bulk/{job}, Undo when it is done).
    Select-all lists and the cleanup and subscriptions pages (core/mailgroups.py) use it;
    `origin` says where the list came from in the audit row."""
    if len(ids) <= BULK_INLINE_MAX:
        body.ids, body.query = ids, None
        res = await _run_action(body, p, db, "conversation")
        # `affected` counts messages; the list's own count is of conversations.
        return {**res, "conversations": len(ids)} if isinstance(res, dict) else res
    args = body.model_dump(exclude={"ids", "action", "query"})
    job = await enqueue_returning(db, "bulk.conversations",
                                  {"ids": ids, "action": body.action, "args": args, "done": 0, "total": len(ids)},
                                  user_id=p.user_id)
    await audit(db, p, f"conversations.{body.action}.bulk", f"{len(ids)} conversations",
                {**body.model_dump(exclude_none=True, exclude={"ids"}), **({"origin": origin} if origin else {})},
                {"job": job}, always=True)
    await db.commit()
    return JSONResponse({"ok": True, "job": job, "total": len(ids), "status": "running"}, status_code=202)


@router.get("/bulk/{job_id}")
async def bulk_status(job_id: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    from mailr.models import Job

    j = await db.get(Job, job_id)
    if j is None or j.user_id != p.user_id or j.kind != "bulk.conversations":
        raise ApiError(404, "common.not_found")
    # A finished importance toggle can be undone: POST /conversations/actions
    # {"action": "restore_importance", "job": <id>, "ids": []}.
    # Any other action's job offers its reverse the same way (_undo_bulk_job).
    rev = (j.payload or {}).get("reverse") if j.status == "done" else None
    undo = {"action": "restore_importance", "job": j.id, "ids": []} \
        if j.status == "done" and (j.payload or {}).get("undo") else \
        {"action": "restore_category", "job": j.id, "ids": []} \
        if j.status == "done" and (j.payload or {}).get("undo_category") else \
        {"action": rev["action"], "job": j.id, "ids": []} if rev else None
    return {"job": j.id, "status": {"queued": "running"}.get(j.status, j.status),
            "done": j.progress if j.progress is not None else (j.payload or {}).get("done", 0),
            "total": (j.payload or {}).get("total", 0),
            "error": j.error[:300] if j.status == "failed" else "", "undo": undo}


@router.post("/messages/actions")
async def message_actions(body: ActionIn, p: Principal = Depends(scope("label")),
                          db: AsyncSession = Depends(get_db)):
    return await _run_action(body, p, db, "message")


@router.post("/messages/{mid}/unsubscribe")
async def unsubscribe(mid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """List-Unsubscribe: one-click POST (RFC 8058) when offered, else a mailto sent through the
    send gate, else the web link is returned for the user to open."""
    from mailr.api.mail_read import load_message
    from mailr.core import unsubscribe as unsub

    m = await load_message(db, p, mid)
    plan = await unsub.plan(db, p.user, m)
    if plan["method"] == "none":
        raise ApiError(400, "mail.message_no_unsubscribe_link")
    if plan["method"] == "refused":
        raise ApiError(400, "mail.unsubscribe_refused", to=plan['to'], reason=plan['reason'])
    if plan["method"] == "link":
        return plan
    if p.is_token:
        how = f"by mail to {plan['to']} (subject and text: unsubscribe)" if plan["method"] == "mailto" \
            else f"one-click request to {plan.get('url', '')[:200]}"
        decision = await permissions.check(
            db, permissions.Actor.from_principal(p), "mail.unsubscribe", target=m.from_email,
            ctx={"connectors": [m.connector_id] if m.connector_id else []},
            payload={"message_id": m.id},
            preview={"title": f"Unsubscribe from {m.from_name or m.from_email}",
                     "lines": [m.list_id or m.from_email, how]})
        permissions.raise_for(decision)
        if isinstance(decision, permissions.Pending):
            await db.commit()
            return _pending(decision.action)
    result = await unsub.execute(db, p, m, plan)
    await audit(db, p, "messages.unsubscribe", str(mid), {}, result)
    await db.commit()
    return result


class SplitIn(BaseModel):
    message_ids: list[int]


@router.post("/conversations/{cid}/split")
async def split(cid: int, body: SplitIn, p: Principal = Depends(scope("label")), db: AsyncSession = Depends(get_db)):
    msgs = await act.messages_for(db, p, msg_ids=body.message_ids)
    if not msgs or any(m.conversation_id != cid for m in msgs):
        raise ApiError(400, "mail.messages_not_in_conversation")
    new_id = await threading.split(db, p.user_id, [m.id for m in msgs])
    await db.commit()
    return {"conversation_id": new_id}


class MergeIn(BaseModel):
    ids: list[int]


@router.post("/conversations/merge")
async def merge(body: MergeIn, p: Principal = Depends(scope("label")), db: AsyncSession = Depends(get_db)):
    try:
        target = await threading.merge(db, p.user_id, body.ids)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    await db.commit()
    return {"conversation_id": target}


# ---------------- labels ----------------


class LabelIn(BaseModel):
    name: str | None = None
    color: str | None = None
    show_in_list: str | None = None
    show_in_message_list: bool | None = None
    # The mail account the label is a folder of ("New folder" in an account's group). null: none.
    home_account_id: int | None = None


def normalize_color(value: str | None) -> str | None:
    """Label colours are stored as a scheme token ("@default/teal/3"), "#bg" or "#bg,#fg"
    (lower-case; core/label_colours.py). "#bg|#fg" is accepted."""
    try:
        return label_colours.normalize(value)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


async def _label_gate(db: AsyncSession, p: Principal, action: str, payload: dict[str, Any], title: str,
                      lines: list[str]) -> JSONResponse | None:
    """Tokens pass labels.write / labels.delete; an approval runs the same change later
    (agent/config_tools.py config_op). None: go ahead now."""
    if not p.is_token:
        return None
    decision = await permissions.check(db, permissions.Actor.from_principal(p), action, target=title,
                                       payload=payload, preview={"title": title, "kind": "label", "lines": lines,
                                                                 "danger": action == "labels.delete"})
    permissions.raise_for(decision)
    if isinstance(decision, permissions.Pending):
        await audit(db, p, f"labels.{payload['op'].split('.')[-1]}", title, payload, {"pending": decision.action.id})
        await db.commit()
        return _pending(decision.action)
    return None


@router.post("/labels")
async def create_label(body: LabelIn, p: Principal = Depends(scope("label")), db: AsyncSession = Depends(get_db)):
    if not body.name or not body.name.strip():
        raise ApiError(400, "labels.name_missing")
    home = await _home_account(db, p, body.home_account_id)
    held = await _label_gate(db, p, "labels.write", {"op": "label.create", "name": body.name, "color": body.color,
                                                     **({"home_account_id": home} if home else {})},
                             f"Create label “{body.name}”", [body.name])
    if held is not None:
        return held
    store = await user_store(db, p.user)
    lb = await store.create_label(db, body.name, normalize_color(body.color))
    if home and lb.home_account_id is None:
        # ACT mail.account_folder_create @api
        # A folder made in an account's group: listed under that account at once, and made on
        # that account's server now (other accounts get it when mail is filed there).
        lb.home_account_id = home
        await _create_upstream_folder(db, p.user_id, home, lb.name)
    if home:
        # The user made it for that account: pinned there, so it stays in the group's short part
        # (core/accountpins.py). A same-name label made from another account is pinned there too.
        from mailr.core import accountpins

        await db.flush()
        await accountpins.pin_new_folder(db, p.user_id, home, lb.id)
    await audit(db, p, "labels.create", lb.name)
    await db.commit()
    return label_out(lb)


async def _home_account(db: AsyncSession, p: Principal, account_id: int | None) -> int | None:
    """The account a label is made in, checked: one of the caller's mail accounts it may see."""
    if account_id is None:
        return None
    from mailr.core import mailaccounts

    if account_id not in {a.id for a in await mailaccounts.mail_accounts(db, p)}:
        raise ApiError(404, "labels.account_not_found")
    return account_id


async def _create_upstream_folder(db: AsyncSession, uid: int, account_id: int, name: str) -> None:
    """Make the label's folder on the account's own server (mirror mode, where the server keeps
    the mail; a store-mode account is drained, so it gets none)."""
    row = await db.get(Connector, account_id)
    if row is not None and row.user_id == uid and row.mode == "mirror" and row.enabled \
            and row.kind in ("imap", "gmail"):
        await enqueue(db, "connector.folder_op", {"connector_id": row.id, "op": "create", "name": name}, user_id=uid)


@router.patch("/labels/{lid}")
async def update_label(lid: int, body: LabelIn, p: Principal = Depends(scope("label")), db: AsyncSession = Depends(get_db)):
    lb = await db.get(Label, lid)
    if not lb or lb.user_id != p.user_id:
        raise ApiError(404, "labels.label_not_found")
    rehome = "home_account_id" in body.model_fields_set
    home = await _home_account(db, p, body.home_account_id) if rehome else None
    held = await _label_gate(db, p, "labels.write", {
        "op": "label.update", "label_id": lb.id, "name": body.name, "color": body.color,
        "show_in_list": body.show_in_list, "show_in_message_list": body.show_in_message_list,
        **({"home_account_id": home} if rehome else {})},
        f"Change label “{lb.name}”", [x for x in (body.name and f"rename to {body.name}",
                                                  body.color and f"colour {body.color}",
                                                  body.show_in_list and f"in the label list: {body.show_in_list}")
                                     if x])
    if held is not None:
        return held
    if body.name and body.name != lb.name:
        store = await user_store(db, p.user)
        old_name = lb.name
        try:
            await store.rename_label(db, lb, body.name)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        await _mirror_folder_op(db, p.user_id, {"op": "rename", "old": old_name, "new": lb.name})
    if "color" in body.model_fields_set:  # null clears the colour, as "" does
        lb.color = normalize_color(body.color)
    if body.show_in_list in ("show", "hide", "unread"):
        lb.show_in_list = body.show_in_list
    if body.show_in_message_list is not None:
        lb.show_in_message_list = body.show_in_message_list
    if rehome and lb.role is None and home != lb.home_account_id:
        lb.home_account_id = home
        if home is not None:
            await _create_upstream_folder(db, p.user_id, home, lb.name)
    await db.commit()
    return label_out(lb)


class LabelColoursIn(BaseModel):
    scheme: str | None = None
    colors: dict[int, str | None] = Field(default_factory=dict)


@router.post("/labels/colours")
async def set_label_colours(body: LabelColoursIn, p: Principal = Depends(scope("label")),
                            db: AsyncSession = Depends(get_db)):
    """Recolour labels in one change. With scheme: the label colour scheme setting becomes it and
    every label with a scheme colour moves to the same hue and shade in it. colors: label id to
    its new colour (null clears it), applied after the scheme, as the conversion of old colours
    does. Only the user makes this change (as with settings)."""
    if p.is_token:
        raise ApiError(403, "labels.colours_user_only")
    if body.scheme is not None and body.scheme not in label_colours.SCHEMES:
        raise HTTPException(400, f"scheme must be one of {', '.join(label_colours.SCHEMES)}")  # i18n: internal
    if len(body.colors) > 5000:
        raise HTTPException(400, "too many labels")  # i18n: internal
    wanted = {lid: normalize_color(c) for lid, c in body.colors.items()}
    labels = {lb.id: lb for lb in (await db.execute(select(Label).where(Label.user_id == p.user_id))).scalars()}
    missing = [lid for lid in wanted if lid not in labels]
    if missing:
        raise ApiError(404, "labels.label_not_found_named", label=missing[0])
    changed: set[int] = set()
    if body.scheme is not None:
        from mailr.core import usersettings

        await usersettings.patch(db, p.user_id, {"label_scheme": body.scheme})
        for lb in labels.values():
            moved = label_colours.rescheme(lb.color, body.scheme)
            if moved and moved != lb.color:
                lb.color = moved
                changed.add(lb.id)
    for lid, c in wanted.items():
        if labels[lid].color != c:
            labels[lid].color = c
            changed.add(lid)
    await audit(db, p, "labels.recolour", body.scheme or "", {"labels": len(changed)}, always=True)
    await db.commit()
    return {"changed": sorted(changed), "label_scheme": body.scheme,
            "colors": {i: labels[i].color for i in sorted(changed)}}


@router.delete("/labels/{lid}")
async def delete_label(lid: int, p: Principal = Depends(scope("label")), db: AsyncSession = Depends(get_db)):
    lb = await db.get(Label, lid)
    if not lb or lb.user_id != p.user_id:
        raise ApiError(404, "labels.label_not_found")
    held = await _label_gate(db, p, "labels.delete", {"op": "label.delete", "label_id": lb.id},
                             f"Delete label “{lb.name}”", [f"{lb.name} (conversations keep their other labels)"])
    if held is not None:
        return held
    store = await user_store(db, p.user)
    children = list((await db.execute(select(Label).where(
        Label.user_id == p.user_id, Label.name.startswith(lb.name + "/")))).scalars())
    doomed = [lb.name] + [c.name for c in children]
    doomed_ids = [lid] + [c.id for c in children]
    # Every conversation carrying the label OR one of its children needs its aggregates redone.
    conv_ids = [c for c in (await db.execute(select(Message.conversation_id).where(
        Message.user_id == p.user_id, Message.label_ids.overlap(doomed_ids)))).scalars() if c]
    try:
        await store.delete_label(db, lb)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    await threading.refresh(db, p.user_id, conv_ids)
    # Children first, so a parent folder is empty of sub-folders when it goes.
    await _mirror_folder_op(db, p.user_id, {"op": "delete", "names": sorted(doomed, key=lambda n: -n.count("/"))})
    await audit(db, p, "labels.delete", str(lid))
    await db.commit()
    return {"ok": True}


async def _mirror_folder_op(db: AsyncSession, uid: int, op: dict[str, Any]) -> None:
    """Label renames/deletes are applied to the upstream folders of mirror-mode accounts."""
    conns = (await db.execute(select(Connector.id).where(
        Connector.user_id == uid, Connector.mode == "mirror", Connector.enabled.is_(True),
        Connector.kind.in_(("imap", "gmail"))))).scalars().all()
    for cid in conns:
        await enqueue(db, "connector.folder_op", {"connector_id": cid, **op}, user_id=uid)


# ---------------- uploads ----------------


def upload_dir(uid: int) -> Path:
    d = Path(get_settings().data_dir) / "uploads" / str(uid)
    d.mkdir(parents=True, exist_ok=True)
    return d


# An upload nothing refers to yet (the editor saves the draft a moment later) is kept this long
# before it may be removed to make room.
UNUSED_GRACE = 3600


def uploads_held(uid: int) -> int:
    """What a user's attachment uploads take on disk."""
    return sum(f.stat().st_size for f in upload_dir(uid).iterdir() if f.is_file() and not f.name.endswith(".json"))


async def prune_unused_uploads(uid: int) -> None:
    """Remove a user's uploads that no draft and no queued send refers to, older than UNUSED_GRACE
    (the daily clean-up removes them after 30 days)."""
    import time

    from sqlalchemy import text

    from mailr.db import session_scope

    async with session_scope() as db:
        referenced = set((await db.execute(text(
            "SELECT jsonb_array_elements(upstream->'draft_payload'->'attachments')->>'blob' FROM messages "
            "WHERE user_id = :u AND draft AND NOT deleted AND upstream ? 'draft_payload'"), {"u": uid})).scalars())
        referenced |= set((await db.execute(text(
            "SELECT jsonb_array_elements(payload->'attachments')->>'blob' FROM outbox WHERE user_id = :u "
            "AND status IN ('queued','awaiting_approval','sending')"), {"u": uid})).scalars())
    cutoff = time.time() - UNUSED_GRACE
    for f in upload_dir(uid).iterdir():
        if f.name.split(".")[0] not in referenced and f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


@router.post("/uploads")
async def upload(file: UploadFile = File(...), p: Principal = Depends(scope("draft"))):
    s = get_settings()
    limit = s.max_attachment_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise ApiError(413, "mail.attachments_limit", max=max(1, limit // 1024**2))
    blob = hashlib.sha256(data).hexdigest()[:32]
    path = upload_dir(p.user_id) / blob
    if not path.exists():
        # Every upload of a user counts, until it is sent or expires: first make room from the
        # ones nothing uses any more.
        if uploads_held(p.user_id) + len(data) > s.uploads_quota_bytes:
            await prune_unused_uploads(p.user_id)
        if uploads_held(p.user_id) + len(data) > s.uploads_quota_bytes:
            raise ApiError(413, "mail.uploads_full", max=max(1, s.uploads_quota_bytes // 1024**2))
    path.write_bytes(data)
    meta = {"filename": os.path.basename(file.filename or "attachment")[:200],
            "type": file.content_type or "application/octet-stream", "size": len(data)}
    (upload_dir(p.user_id) / f"{blob}.json").write_text(json.dumps(meta))
    return {"blob": blob, **meta}


# ACT mail.insert_image @api: an inline image in the compose editor is shown from the user's own
# upload. Only PNG, JPEG, GIF and WebP (by their bytes) are served; anything else is refused.
@router.get("/uploads/{blob}")
async def get_upload_image(blob: str, p: Principal = Depends(scope("draft"))):
    from fastapi.responses import Response

    from mailr.core.compose import sniff_image

    data, _, _ = read_upload(p.user_id, blob)
    ctype = sniff_image(data)
    if ctype is None:
        raise ApiError(415, "mail.inline_image_type")
    return Response(data, media_type=ctype, headers={
        "Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'"})


def read_upload(uid: int, blob: str) -> tuple[bytes, str, str]:
    if not re.fullmatch(r"[0-9a-f]{32}", blob or ""):
        raise HTTPException(400, "bad attachment reference")  # i18n: internal
    path = upload_dir(uid) / blob
    if not path.exists():
        raise ApiError(400, "mail.attachment_expired")
    meta = json.loads((upload_dir(uid) / f"{blob}.json").read_text())
    return path.read_bytes(), meta["type"], meta["filename"]


# ---------------- drafts ----------------


class DraftIn(BaseModel):
    payload: dict[str, Any]
    # The version the editor started from (core/live.py): changes saved since then are merged in;
    # without it the payload replaces the draft.
    base_version: int | None = None


def _live_conflict(e) -> JSONResponse:
    return JSONResponse(e.body(), status_code=409)


def _draft_saved(m: Message) -> dict[str, Any]:
    """A saved draft, its version, and (when the server merged it with changes saved meanwhile)
    the payload as stored."""
    out: dict[str, Any] = {"draft": message_out(m), "version": getattr(m, "live_version", None),
                           "merged": bool(getattr(m, "live_merged", False))}
    if out["merged"]:
        out["payload"] = m.live_payload
    return out


async def _token_draft_gate(db: AsyncSession, p: Principal, payload: dict[str, Any], mid: int | None,
                            base_version: int | None = None) -> JSONResponse | None:
    """A token's draft write passes the same check as the agent tools (agent/draft_tools.py _draft_gate):
    the user's rules for "Write drafts", a recipient change on a saved draft always asks, and
    a draft to people outside the replied conversation (or with a Bcc) asks unless a rule allows.
    Remote images in the token's HTML are removed. None: go ahead."""
    if not p.is_token:
        return None
    from mailr.agent.agentwrite import strip_remote_images_html
    from mailr.agent.tools import ToolCtx, ToolError, _draft_gate
    from mailr.core.drafts import draft_payload, load_draft

    if isinstance(payload.get("html"), str):
        payload["html"] = strip_remote_images_html(payload["html"])
    before, conv = None, None
    if mid is not None:
        m = await load_draft(db, p, mid)
        before, conv = await draft_payload(db, p, m), m.conversation_id
    elif payload.get("in_reply_to_message_id"):
        from mailr.api.mail_read import load_message

        conv = (await load_message(db, p, int(payload["in_reply_to_message_id"]))).conversation_id
    try:
        pending = await _draft_gate(ToolCtx(db=db, p=p), payload, draft_id=mid, before=before, conversation_id=conv,
                                    base_version=base_version)
    except ToolError as e:
        raise HTTPException(403, str(e)) from None
    if pending is None:
        return None
    from mailr.models import PendingAction

    return _pending(await db.get(PendingAction, int(pending.data["approval_id"])))


# ACT draft.save @api
@router.post("/drafts")
async def create_draft(body: DraftIn, p: Principal = Depends(scope("draft")), db: AsyncSession = Depends(get_db)):
    from mailr.core.drafts import save_draft

    if (held := await _token_draft_gate(db, p, body.payload, None)) is not None:
        return held
    m = await save_draft(db, p, body.payload, None, by=_live_by(p))
    res = _draft_saved(m)
    await audit(db, p, "drafts.create", str(m.id), {"subject": body.payload.get("subject"),
                                                     "to": body.payload.get("to")})
    await db.commit()
    return res


@router.put("/drafts/{mid}")
async def update_draft(mid: int, body: DraftIn, p: Principal = Depends(scope("draft")), db: AsyncSession = Depends(get_db)):
    from mailr.core import live
    from mailr.core.drafts import save_draft

    if (held := await _token_draft_gate(db, p, body.payload, mid, body.base_version)) is not None:
        return held
    try:
        m = await save_draft(db, p, body.payload, mid, base_version=body.base_version, by=_live_by(p))
    except live.LiveConflict as e:
        await db.rollback()
        return _live_conflict(e)
    await audit(db, p, "drafts.update", str(m.id), {"merged": True} if m.live_merged else None)
    await db.commit()
    return _draft_saved(m)


def _live_by(p: Principal) -> str:
    from mailr.core import live

    return live.who(p)


@router.get("/drafts/{mid}")
async def get_draft(mid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    from mailr.core.drafts import draft_payload, live_version, load_draft

    m = await load_draft(db, p, mid)
    # The version first: it may read the draft again (core/live.py `state`), and the payload must be
    # the copy that version holds.
    version = await live_version(db, p, m)
    out = {"draft": message_out(m), "payload": await draft_payload(db, p, m), "version": version}
    await db.commit()
    return out


# ACT draft.discard @api
@router.delete("/drafts/{mid}")
async def delete_draft(mid: int, p: Principal = Depends(scope("draft")), db: AsyncSession = Depends(get_db)):
    from mailr.core.drafts import discard_draft

    await discard_draft(db, p, mid)
    await audit(db, p, "drafts.delete", str(mid))
    await db.commit()
    return {"ok": True}


@router.get("/compose/reply")
async def compose_reply(message_id: int, mode: str = "reply", images: str = "blocked",
                        p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """images=allowed when the reader shows the message's images: the quote then shows them
    through the image proxy; otherwise they stay placeholders until the mail is sent."""
    from mailr.core.drafts import reply_payload

    if mode not in ("reply", "reply_all", "forward"):
        raise HTTPException(400, "mode must be reply, reply_all, or forward")  # i18n: internal
    return await reply_payload(db, p, message_id, mode, images=images)


# ---------------- send ----------------


class SendIn(BaseModel):
    payload: dict[str, Any]
    draft_id: int | None = None
    send_at: datetime | None = None


# ACT draft.send @api, ACT draft.schedule @api (send_at)
@router.post("/send")
async def send(body: SendIn, p: Principal = Depends(scope("draft")), db: AsyncSession = Depends(get_db)):
    gate = SendGate(db, p)
    ob = await gate.submit(body.payload, draft_id=body.draft_id, send_at=body.send_at)
    res: dict[str, Any] = {"outbox": outbox_out(ob, p.token.name if p.token else None)}
    await audit(db, p, "send.submit", str(ob.id),
                {"to": body.payload.get("to"), "cc": body.payload.get("cc"), "bcc": body.payload.get("bcc"),
                 "subject": body.payload.get("subject"), "send_at": body.send_at},
                {"status": ob.status, "reason": ob.approval_reason})
    await db.commit()
    if gate.pending is not None:
        return JSONResponse({**permissions.pending_response(gate.pending), **_jsonable(res)}, status_code=202)
    return res


def _jsonable(v: Any) -> Any:
    return json.loads(json.dumps(v, default=str))


@router.get("/outbox")
async def outbox(status: str | None = None, scheduled: bool = False, active: bool = False,
                 account: int | None = Query(None, ge=1, le=2**40),
                 p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """`scheduled=true`: messages queued for later (beyond the undo window), soonest first.
    `active=true`: what has not left yet or did not leave: queued to go within a minute,
    sending, waiting for approval, failed or uncertain (it may have been sent) in the last 7 days, and sent in the last 2 minutes (so a
    thread can say Sent). The web's Outbox and the thread's send status read it."""
    stmt = select(Outbox, ApiToken.name).outerjoin(ApiToken, ApiToken.id == Outbox.token_id).where(Outbox.user_id == p.user_id)
    if active:
        now = datetime.now(UTC)
        stmt = stmt.where(or_(
            and_(Outbox.status == "queued", Outbox.send_at <= now + timedelta(minutes=1)),
            Outbox.status.in_(("sending", "awaiting_approval")),
            and_(Outbox.status.in_(("failed", "uncertain")), Outbox.created_at > now - timedelta(days=7)),
            and_(Outbox.status == "sent", Outbox.sent_at > now - timedelta(minutes=2))))
        if p.token is not None:
            stmt = stmt.where(Outbox.token_id == p.token.id)
        rows = (await db.execute(stmt.order_by(Outbox.id.desc()).limit(200))).all()
        return [outbox_out(o, name) for o, name in rows]
    if scheduled:
        stmt = stmt.where(Outbox.status == "queued", Outbox.send_at > datetime.now(UTC) + timedelta(minutes=1))
        if account is not None:
            # One account's Scheduled folder (core/mailaccounts.py): sends from its identities.
            stmt = stmt.where(Outbox.identity_id.in_(select(Identity.id).where(
                Identity.user_id == p.user_id, Identity.connector_id == account)))
        if p.token is not None:
            stmt = stmt.where(Outbox.token_id == p.token.id)
        rows = (await db.execute(stmt.order_by(Outbox.send_at.asc()).limit(500))).all()
        return [outbox_out(o, name) for o, name in rows]
    if status:
        stmt = stmt.where(Outbox.status.in_(status.split(",")))
    else:
        stmt = stmt.where(Outbox.created_at > datetime.now(UTC) - timedelta(days=30))
    if p.token is not None:
        stmt = stmt.where(Outbox.token_id == p.token.id)
    rows = (await db.execute(stmt.order_by(Outbox.id.desc()).limit(200))).all()
    return [outbox_out(o, name) for o, name in rows]


@router.post("/outbox/{oid}/cancel")
async def cancel(oid: int, p: Principal = Depends(scope("draft")), db: AsyncSession = Depends(get_db)):
    gate = SendGate(db, p)
    ob = await gate.cancel(oid)
    await audit(db, p, "send.cancel", str(oid))
    await db.commit()
    draft_id = int(ob.draft_jmap_id) if ob.draft_jmap_id else None
    if draft_id:
        d = await db.get(Message, draft_id)
        if not d or d.deleted:
            draft_id = None
    return {"outbox": outbox_out(ob), "payload": ob.payload, "draft_id": draft_id}


# ACT draft.retry_send @api
@router.post("/outbox/{oid}/retry")
async def retry_send(oid: int, p: Principal = Depends(scope("draft")), db: AsyncSession = Depends(get_db)):
    gate = SendGate(db, p)
    ob = await gate.retry(oid)
    await audit(db, p, "send.retry", str(oid), always=True)
    await db.commit()
    return {"outbox": outbox_out(ob)}


@router.post("/outbox/{oid}/send-now")
async def send_now(oid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    gate = SendGate(db, p)
    ob = await gate.send_now(oid)
    await audit(db, p, "send.now", str(oid), always=True)
    await db.commit()
    return {"outbox": outbox_out(ob)}


@router.post("/outbox/{oid}/approve")
async def approve(oid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "mail.approve_user_only")
    gate = SendGate(db, p)
    ob = await gate.decide(oid, approve=True)
    await audit(db, p, "send.approve", str(oid), always=True)
    await db.commit()
    return {"outbox": outbox_out(ob, await _token_name(db, ob.token_id))}


@router.post("/outbox/{oid}/reject")
async def reject(oid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "mail.reject_user_only")
    gate = SendGate(db, p)
    ob = await gate.decide(oid, approve=False)
    await audit(db, p, "send.reject", str(oid), always=True)
    await db.commit()
    return {"outbox": outbox_out(ob, await _token_name(db, ob.token_id))}


async def _token_name(db: AsyncSession, token_id: int | None) -> str | None:
    if token_id is None:
        return None
    t = await db.get(ApiToken, token_id)
    return t.name if t else None


@router.get("/identities/default")
async def default_identity(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    i = (await db.execute(select(Identity).where(Identity.user_id == p.user_id).order_by(Identity.is_default.desc(), Identity.id))).scalars().first()
    if not i:
        raise ApiError(404, "mail.no_identities")
    return {"id": i.id, "email": i.email, "name": i.name}


