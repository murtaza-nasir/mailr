"""The sender screener's routes (core/screener.py; site/docs/user-guide/screener.md).

GET  /screener                 the senders waiting, with their held mail summarised (read)
POST /screener/{id}/decide     allow, block or label a sender; an API token's request always asks
                               the user first (permission class mail.screener)
DELETE /screener/{id}          undo a decision: screen the sender again (the signed-in user only)
GET  /screener/settings        on or off, the accounts it covers, the decisions made
PUT  /screener/settings        turn it on or off, choose the accounts (the signed-in user only)
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import permissions, screener
from mailr.core.audit import audit
from mailr.core.auth import Principal, current, scope
from mailr.core.jobs import enqueue
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import ScreenerSender, User

router = APIRouter(tags=["screener"])

ALWAYS_ASK_REASON = "hard rule: a sender's own mail could ask an agent to let it in"


class Decide(BaseModel):
    decision: Literal["allow", "block", "label"]
    label_id: int | None = None
    # "Feed": the label named Feed, made when it does not exist yet.
    feed: bool = False
    block_to: Literal["spam", "trash"] = "spam"


class ScreenerSettings(BaseModel):
    enabled: bool | None = None
    accounts: dict[str, bool] | None = Field(default=None, max_length=200)


async def _entry(db: AsyncSession, p: Principal, sid: int) -> ScreenerSender:
    e = await db.get(ScreenerSender, sid)
    if e is None or e.user_id != p.user_id:
        raise ApiError(404, "screener.no_such_sender")
    return e


def preview_for(e: ScreenerSender, body: Decide, label_name: str | None = None) -> dict[str, Any]:
    what = {"allow": f"Allow {e.key}: their mail goes to the Inbox",
            "block": f"Block {e.key}: their mail goes to {'Trash' if body.block_to == 'trash' else 'Spam'}",
            "label": f"Send {e.key} to {'Feed' if body.feed else label_name or 'a label'}, out of the Inbox"}[body.decision]
    return {"title": f"Screener: {body.decision} {e.key}", "kind": "people", "danger": body.decision == "block",
            "lines": [what, "Mail already waiting in the Screener moves now; later mail follows."]}


# ACT screener.review @api
@router.get("/screener")
async def list_screener(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    from mailr.api.org_routes import include_local

    user = await db.get(User, p.user_id)
    senders = await screener.pending(db, p, include_local=include_local(p))
    return {"enabled": screener.enabled(user), "count": len(senders), "senders": senders}


# ACT screener.allow @api
# ACT screener.block @api
# ACT screener.label @api
@router.post("/screener/{sid}/decide")
async def decide(sid: int, body: Decide, p: Principal = Depends(scope("label")), db: AsyncSession = Depends(get_db)):
    e = await _entry(db, p, sid)
    label_name = None
    if body.decision == "label" and not body.feed:
        from mailr.models import Label

        lb = await db.get(Label, body.label_id) if body.label_id else None
        if lb is None or lb.user_id != p.user_id or lb.role:
            raise ApiError(400, "labels.choose_own")
        label_name = lb.name
    if p.is_token:
        payload = {"sender_id": e.id, "decision": body.decision, "label_id": body.label_id, "feed": body.feed,
                   "block_to": body.block_to, "by": p.actor[:60]}
        decision = await permissions.check(db, permissions.Actor.from_principal(p), "mail.screener", target=e.key,
                                           payload=payload, preview=preview_for(e, body, label_name))
        permissions.raise_for(decision)
        if isinstance(decision, permissions.Pending):
            await audit(db, p, "screener.decide", e.key, {"decision": body.decision}, {"pending": decision.action.id})
            await db.commit()
            return JSONResponse(permissions.pending_response(decision.action), status_code=202)
    try:
        out = await screener.decide(db, p, e, body.decision, label_id=body.label_id, feed=body.feed,
                                    block_to=body.block_to, by="user" if not p.is_token else p.actor)
    except ValueError as err:
        raise HTTPException(400, str(err)) from None
    await audit(db, p, "screener.decide", e.key, {"decision": body.decision, "affected": out["affected"]})
    await db.commit()
    return out


# ACT screener.forget @api
@router.delete("/screener/{sid}")
async def forget(sid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "screener.decision_user_only")
    e = await _entry(db, p, sid)
    out = await screener.forget(db, p, e)
    await audit(db, p, "screener.forget", out["key"])
    await db.commit()
    return out


@router.get("/screener/settings")
async def get_settings(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "screener.settings_see_user_only")
    user = await db.get(User, p.user_id)
    return {"enabled": screener.enabled(user), "accounts": await screener.accounts(db, user),
            "decided": await screener.decided(db, user.id)}


# ACT screener.configure @api
@router.put("/screener/settings")
async def put_settings(body: ScreenerSettings, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "screener.settings_change_user_only")
    from mailr.core import usersettings

    user = await db.get(User, p.user_id)
    was = screener.enabled(user)
    if body.accounts is not None:
        mine = {str(a["id"]) for a in await screener.accounts(db, user)}
        bad = [k for k in body.accounts if k not in mine]
        if bad:
            raise ApiError(400, "screener.no_such_account", accounts=', '.join(bad[:5]))
        await usersettings.patch_in(db, p.user_id, screener.ACCOUNTS, body.accounts)
    if body.enabled is not None:
        await usersettings.patch(db, p.user_id, {screener.SETTING: body.enabled})
    if was and body.enabled is False:
        # Turned off: the held mail goes back to the Inbox (a job: it may be many messages).
        await enqueue(db, "screener.release_all", {"user_id": user.id}, user_id=user.id,
                      dedupe_key=f"screener:release:{user.id}")
    await audit(db, p, "screener.settings", "", {"enabled": body.enabled, "accounts": body.accounts})
    await db.commit()
    return {"enabled": screener.enabled(user), "accounts": await screener.accounts(db, user),
            "decided": await screener.decided(db, user.id)}
