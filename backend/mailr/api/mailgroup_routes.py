"""The Subscriptions page and the Clean up page (core/mailgroups.py).

Both pages act on whole groups of conversations through the ordinary actions: small groups at
once with their Undo, large ones as a bulk job with progress and Undo (api/mail_write.py
run_on_conversations). Every change is audited. These routes serve the signed-in user; agents
use the assistant tools (agent/cleanup_tools.py), whose changes wait for the user's approval.

Subscriptions:
    GET  /subscriptions                    the rows (sort, filter, q, limit, offset)
    GET  /subscriptions/unsubscribe?key=   how the newest message unsubscribes, before acting
    POST /subscriptions/unsubscribe        {key}: one-click POST (through the egress proxy) or mailto
    GET  /subscriptions/preview?key=&action=   what Archive all / Label would change
    POST /subscriptions/actions            {key, action: archive_all|label|auto_archive|
                                            remove_auto_archive|keep|unkeep, label_id}
Clean up:
    GET  /cleanup/groups                   groups by sender, domain, list or category, in a scope
    GET  /cleanup/preview                  exactly what an action on one group changes
    POST /cleanup/apply                    {by, key, scope, action: archive|label|trash|read|category|keep}
    POST /cleanup/rule                     {by, key, actions}: a filter for the group's future mail
    POST /cleanup/suggest                  categories for uncategorised groups, by the user's model
    GET  /cleanup/suggest                  the state of that job
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import mailgroups as mg
from mailr.core.audit import audit
from mailr.core.auth import Principal, scope
from mailr.core.jobs import enqueue
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import Job, Label, Rule

router = APIRouter(tags=["mail"])


def _session_only(p: Principal) -> None:
    if p.is_token:
        raise HTTPException(403, "agents use the assistant tools for this (list_subscriptions, "  # i18n: internal
                                 "list_cleanup_groups, propose_cleanup); their changes wait for approval")


class ScopeIn(BaseModel):
    where: str = "archived"
    older_than_days: int = Field(0, ge=0, le=36500)
    uncategorised: bool = False
    bulk_only: bool = False
    include_starred: bool = False
    include_important: bool = False
    include_replied: bool = False

    def scope(self) -> mg.Scope:
        return mg.Scope(**self.model_dump())


def _scope_q(where: str = "archived", older_than_days: int = Query(0, ge=0, le=36500),
             uncategorised: bool = False, bulk_only: bool = False, include_starred: bool = False,
             include_important: bool = False, include_replied: bool = False) -> mg.Scope:
    return mg.Scope(where=where, older_than_days=older_than_days, uncategorised=uncategorised,
                    bulk_only=bulk_only, include_starred=include_starred, include_important=include_important,
                    include_replied=include_replied)


async def _label_of(db: AsyncSession, uid: int, label_id: int | None) -> Label:
    lb = await db.get(Label, int(label_id)) if label_id else None
    if lb is None or lb.user_id != uid or lb.role:
        raise ApiError(400, "labels.choose_own")
    return lb


# ------------------------------------------------------------------ subscriptions


# ACT subscription.list @api
@router.get("/subscriptions")
async def subscriptions(q: str = "", sort: str = "score", filter: str = "all",
                        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                        p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    out = await mg.subscriptions(db, p, q=q, sort=sort, filter=filter, limit=limit, offset=offset)
    if out["unknown"] and not p.is_token:
        # Mail stored before the one-click marker was indexed: read the newest message of each
        # such subscription from the store, in the background (the page refreshes later).
        await enqueue(db, "subscriptions.probe", {}, user_id=p.user_id,
                      dedupe_key=f"subscriptions.probe:{p.user_id}")
        await db.commit()
    return out


@router.get("/subscriptions/unsubscribe")
async def unsubscribe_plan(key: str, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """How Unsubscribe would work for this subscription, shown before acting: one-click (the
    address it posts to), mail (the address and the fixed text), a web page to open, or nothing."""
    from mailr.core import unsubscribe as unsub

    m = await mg.latest_with_unsubscribe(db, p, key)
    if m is None:
        return {"method": "none", "key": key}
    plan = await unsub.plan(db, p.user, m)
    await db.commit()  # plan() keeps what it learned about the message (Message.unsub_method)
    out = {"key": key, "message_id": m.id, "method": plan["method"]}
    for k in ("url", "to", "reason"):
        if plan.get(k):
            out[k] = plan[k]
    return out


class SubKey(BaseModel):
    key: str = Field(min_length=6, max_length=420)


async def unsubscribe_subscription(db: AsyncSession, p, key: str, *, via: str) -> dict[str, Any]:
    """Unsubscribe from a subscription by its newest message with a List-Unsubscribe header, the
    same way as from a message (api/mail_write.unsubscribe: one-click through core/egress.py, which
    MAILR_REQUIRE_EGRESS can refuse, or a mailto through the send gate). Records the result on the
    subscription."""
    from mailr.core import unsubscribe as unsub

    m = await mg.latest_with_unsubscribe(db, p, key)
    if m is None:
        raise ApiError(400, "cleanup.no_unsubscribe_link")
    plan = await unsub.plan(db, p.user, m)
    if plan["method"] == "none":
        raise ApiError(400, "cleanup.no_unsubscribe_link")
    if plan["method"] == "refused":
        raise ApiError(400, "mail.unsubscribe_refused", to=plan['to'], reason=plan['reason'])
    if plan["method"] == "link":
        await db.commit()
        return {**plan, "key": key, "message_id": m.id}
    result = await unsub.execute(db, p, m, plan)
    if result.get("ok"):
        st = await mg.state(db, p.user_id, mg.SUB, key)
        st.unsubscribed_at, st.unsubscribe_method = datetime.now(UTC), plan["method"]
    await audit(db, p, "subscriptions.unsubscribe", key, {"message_id": m.id, "via": via}, result, always=True)
    await db.commit()
    return {**result, "key": key, "message_id": m.id}


# ACT subscription.unsubscribe @api
@router.post("/subscriptions/unsubscribe")
async def unsubscribe(body: SubKey, p: Principal = Depends(scope("send")), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    return await unsubscribe_subscription(db, p, body.key, via="web")


@router.get("/subscriptions/preview")
async def subscription_preview(key: str, action: str = "archive_all", include_starred: bool = False,
                               include_important: bool = False, include_replied: bool = False,
                               p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    sc = mg.Scope(where="inbox" if action == "archive_all" else "all", include_starred=include_starred,
                  include_important=include_important, include_replied=include_replied)
    return await mg.preview(db, p, mg.SUB, key, sc)


class SubAction(BaseModel):
    key: str = Field(min_length=6, max_length=420)
    action: str
    label_id: int | None = None
    include_starred: bool = False
    include_important: bool = False
    include_replied: bool = False


# ACT subscription.archive_all @api, ACT subscription.auto_archive @api,
# ACT subscription.label @api, ACT subscription.keep @api
@router.post("/subscriptions/actions")
async def subscription_action(body: SubAction, p: Principal = Depends(scope("label")),
                              db: AsyncSession = Depends(get_db)):
    """archive_all and label act on the subscription's conversations (Undo as usual);
    auto_archive makes a filter for its future mail (Undo: remove_auto_archive); keep marks it
    reviewed (Undo: unkeep)."""
    from mailr.api.mail_write import ActionIn, run_on_conversations

    _session_only(p)
    mg.key_match(mg.SUB, body.key)  # 400 for a malformed key
    uid = p.user_id
    origin = {"page": "subscriptions", "key": body.key}
    if body.action in ("archive_all", "label"):
        sc = mg.Scope(where="inbox" if body.action == "archive_all" else "all",
                      include_starred=body.include_starred, include_important=body.include_important,
                      include_replied=body.include_replied)
        ids = await mg.resolve(db, p, mg.SUB, body.key, sc)
        if body.action == "archive_all":
            act = ActionIn(action="archive")
        else:
            lb = await _label_of(db, uid, body.label_id)
            act = ActionIn(action="add_labels", label_ids=[lb.id])
        if not ids:
            return {"ok": True, "affected": 0, "conversations": 0, "undo": None}
        return await run_on_conversations(act, p, db, ids, origin=origin)
    if body.action == "auto_archive":
        from mailr.api.org_routes import apply_rule_op

        st = await mg.state(db, uid, mg.SUB, body.key)
        if st.rule_id and await db.get(Rule, st.rule_id):
            return {"ok": True, "rule_id": st.rule_id, "undo": {"action": "remove_auto_archive"}}
        q = mg.rule_query(mg.SUB, body.key)
        name = f"Auto-archive {body.key.partition(':')[2]}"[:200]
        out = await apply_rule_op(db, p.user, {"op": "create", "rule": {
            "name": name, "query": q, "actions": {"archive": True}, "apply_existing": False}})
        st.rule_id = out["id"]
        await audit(db, p, "subscriptions.auto_archive", body.key, {"query": q}, {"rule_id": out["id"]}, always=True)
        await db.commit()
        return {"ok": True, "rule_id": out["id"], "query": q, "undo": {"action": "remove_auto_archive"}}
    if body.action == "remove_auto_archive":
        st = await mg.state(db, uid, mg.SUB, body.key)
        rid = st.rule_id
        if rid:
            r = await db.get(Rule, rid)
            if r is not None and r.user_id == uid:
                await db.delete(r)
            st.rule_id = None
        await audit(db, p, "subscriptions.remove_auto_archive", body.key, {}, {"rule_id": rid}, always=True)
        await db.commit()
        return {"ok": True, "undo": {"action": "auto_archive"} if rid else None}
    if body.action in ("keep", "unkeep"):
        st = await mg.state(db, uid, mg.SUB, body.key)
        st.reviewed_at = datetime.now(UTC) if body.action == "keep" else None
        await audit(db, p, f"subscriptions.{body.action}", body.key, {}, {}, always=True)
        await db.commit()
        return {"ok": True, "undo": {"action": "unkeep" if body.action == "keep" else "keep"}}
    raise HTTPException(400, f"unknown action {body.action}")  # i18n: internal


# ------------------------------------------------------------------ clean up


# ACT cleanup.groups @api
@router.get("/cleanup/groups")
async def cleanup_groups(by: str = "sender", q: str = "", sort: str = "count",
                         limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                         sc: mg.Scope = Depends(_scope_q), p: Principal = Depends(scope("read")),
                         db: AsyncSession = Depends(get_db)):
    return await mg.groups(db, p, by, sc, q=q, sort=sort, limit=limit, offset=offset)


@router.get("/cleanup/preview")
async def cleanup_preview(by: str, key: str, sc: mg.Scope = Depends(_scope_q),
                          p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    if by not in mg.KINDS:
        raise HTTPException(400, f"group by one of {', '.join(mg.KINDS)}")  # i18n: internal
    return await mg.preview(db, p, by, key, sc)


class CleanupIn(BaseModel):
    by: str
    key: str = Field(max_length=400)
    scope: ScopeIn = Field(default_factory=ScopeIn)
    action: str
    label_id: int | None = None
    category: str | None = None
    # The number of conversations the preview showed: the action refuses to run when the group
    # changed in between (new mail, another device), so it never changes more than was shown.
    expect: int | None = None


CLEANUP_ACTIONS = {"archive": "archive", "label": "add_labels", "trash": "trash", "read": "read",
                   "category": "category"}


# ACT cleanup.archive @api, ACT cleanup.label @api, ACT cleanup.trash @api,
# ACT cleanup.read @api, ACT cleanup.category @api, ACT cleanup.keep @api
@router.post("/cleanup/apply")
async def cleanup_apply(body: CleanupIn, p: Principal = Depends(scope("label")), db: AsyncSession = Depends(get_db)):
    """Run one action on exactly the conversations the preview showed (Trash moves them to Trash
    and never deletes them forever)."""
    from mailr.api.mail_write import ActionIn, run_on_conversations

    _session_only(p)
    if body.by not in mg.KINDS:
        raise HTTPException(400, f"group by one of {', '.join(mg.KINDS)}")  # i18n: internal
    if body.action == "keep":
        st = await mg.state(db, p.user_id, body.by, body.key)
        st.reviewed_at = datetime.now(UTC)
        await audit(db, p, "cleanup.keep", f"{body.by}:{body.key}", {}, {}, always=True)
        await db.commit()
        return {"ok": True}
    if body.action not in CLEANUP_ACTIONS:
        raise HTTPException(400, f"unknown action {body.action}")  # i18n: internal
    sc = body.scope.scope()
    ids = await mg.resolve(db, p, body.by, body.key, sc)
    if body.expect is not None and len(ids) != body.expect:
        raise ApiError(409, "cleanup.group_changed", count=len(ids), expected=body.expect)
    act = ActionIn(action=CLEANUP_ACTIONS[body.action])
    if body.action == "label":
        act.label_ids = [(await _label_of(db, p.user_id, body.label_id)).id]
    if body.action == "category":
        if body.category not in mg.CATEGORIES:
            raise HTTPException(400, f"category must be one of {', '.join(mg.CATEGORIES)}")  # i18n: internal
        act.category = body.category
    if not ids:
        return {"ok": True, "affected": 0, "conversations": 0, "undo": None}
    return await run_on_conversations(act, p, db, ids, origin={"page": "cleanup", "by": body.by, "key": body.key,
                                                               "scope": sc.as_dict()})


class RuleFromGroup(BaseModel):
    by: str
    key: str = Field(max_length=420)
    actions: dict[str, Any]
    name: str = ""


# ACT cleanup.rule @api
@router.post("/cleanup/rule")
async def cleanup_rule(body: RuleFromGroup, p: Principal = Depends(scope("rules")), db: AsyncSession = Depends(get_db)):
    """A filter for the group's future mail (Settings → Mail → Filters & blocking lists it)."""
    from mailr.api.org_routes import apply_rule_op

    _session_only(p)
    if body.by not in (*mg.KINDS, mg.SUB):
        raise HTTPException(400, "unknown grouping")  # i18n: internal
    q = mg.rule_query(body.by, body.key)
    if not q:
        raise ApiError(400, "cleanup.no_future_search")
    if (body.actions or {}).get("forward_to"):
        raise ApiError(400, "cleanup.forwarding_in_settings")
    name = body.name or f"{body.key}"[:200]
    out = await apply_rule_op(db, p.user, {"op": "create", "rule": {
        "name": name, "query": q, "actions": body.actions, "apply_existing": False}})
    await audit(db, p, "cleanup.rule", f"{body.by}:{body.key}", {"query": q, "actions": body.actions},
                {"rule_id": out["id"]}, always=True)
    await db.commit()
    return {"ok": True, "rule": out, "undo": {"rule_id": out["id"]}}


# ------------------------------------------------------------------ model suggestions

class SuggestIn(BaseModel):
    by: str = "sender"
    scope: ScopeIn = Field(default_factory=lambda: ScopeIn(uncategorised=True))
    # Off-hours (the default): tonight between 01:00 and 06:00 in the user's time zone.
    now: bool = False
    groups: int = Field(100, ge=1, le=500)


async def next_quiet_start(db: AsyncSession, user) -> datetime:
    """The next 01:00 in the user's time zone (now, when it is already between 01:00 and 06:00)."""
    from mailr.calendar import tz as tzmod

    zone = await tzmod.user_zone(db, user)
    local = datetime.now(UTC).astimezone(zone)
    if QUIET_START <= local.hour < QUIET_END:
        return datetime.now(UTC)
    start = local.replace(hour=QUIET_START, minute=0, second=0, microsecond=0)
    if start <= local:
        start += timedelta(days=1)
    return start.astimezone(UTC)


QUIET_START, QUIET_END = 1, 6


# ACT cleanup.suggest @api
@router.post("/cleanup/suggest")
async def cleanup_suggest(body: SuggestIn, p: Principal = Depends(scope("label")), db: AsyncSession = Depends(get_db)):
    """Ask the user's classify model (their access policy decides which) for a
    category of each uncategorised group. Suggestions only: a group's category changes when the
    user applies it. Runs off-hours unless `now`."""
    from mailr.intel import llm

    _session_only(p)
    if body.by not in mg.KINDS or body.by == "category":
        raise ApiError(400, "cleanup.categories_by_sender")
    try:
        await llm.resolve(db, p.user, "classify", sensitive=False, probe=True)
    except llm.NoModel as e:
        if str(e):
            raise HTTPException(403, str(e)) from None
        raise ApiError(403, "cleanup.no_sorting_model") from None
    run_at = datetime.now(UTC) if body.now else await next_quiet_start(db, p.user)
    sc = body.scope.scope()
    sc.uncategorised = True
    await enqueue(db, "cleanup.suggest", {"by": body.by, "scope": sc.as_dict(), "groups": body.groups,
                                          "now": body.now},
                  user_id=p.user_id, run_at=run_at, dedupe_key=f"cleanup.suggest:{p.user_id}", max_attempts=2)
    await audit(db, p, "cleanup.suggest", body.by, {"scope": sc.as_dict(), "now": body.now},
                {"run_at": run_at.isoformat()}, always=True)
    await db.commit()
    return {"ok": True, "run_at": run_at.isoformat()}


@router.get("/cleanup/suggest")
async def cleanup_suggest_status(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    j = (await db.execute(select(Job).where(Job.user_id == p.user_id, Job.kind == "cleanup.suggest")
                          .order_by(Job.id.desc()).limit(1))).scalar()
    if j is None:
        return {"status": "none"}
    return {"status": j.status, "run_at": j.run_at.isoformat() if j.run_at else None,
            "done": j.progress, "total": (j.payload or {}).get("groups"),
            "error": (j.error or "")[:300] if j.status == "failed" else ""}
