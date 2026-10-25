"""API tokens for agents, and the audit log."""

from __future__ import annotations

import base64
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.auth import ALL_SCOPES, Principal, create_token, current
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import ApiToken, AuditLog, Connector

router = APIRouter(tags=["tokens"])


class SendPolicy(BaseModel):
    mode: str = Field("approve_all", pattern="^(approve_all|policy)$")
    replies_only: bool = True
    known_only: bool = True
    domains: list[str] = []
    identities: list[int] = []
    max_per_hour: int = Field(5, ge=0, le=1000)
    max_per_day: int = Field(20, ge=0, le=10000)


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scopes: list[str]
    connector_ids: list[int] = []
    local_only: bool = False
    send_policy: SendPolicy = SendPolicy()
    expires_at: datetime | None = None


def token_out(t: ApiToken) -> dict:
    return {"id": t.id, "name": t.name, "prefix": t.prefix, "scopes": t.scopes,
            "connector_ids": t.connector_ids, "local_only": t.local_only,
            "send_policy": t.send_policy, "expires_at": t.expires_at.isoformat() if t.expires_at else None,
            "revoked": t.revoked, "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
            "created_at": t.created_at.isoformat()}


def _session_only(p: Principal) -> None:
    if p.is_token:
        raise ApiError(403, "tokens.user_only")


@router.get("/tokens")
async def list_tokens(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    rows = (await db.execute(select(ApiToken).where(ApiToken.user_id == p.user_id).order_by(ApiToken.id.desc()))).scalars()
    return {"scopes": list(ALL_SCOPES), "items": [token_out(t) for t in rows]}


@router.post("/tokens")
async def new_token(body: TokenIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    from mailr.core.access import require_feature

    await require_feature(db, p.user, "api_tokens")  # Agents & API tokens off in the access policy
    if body.local_only:  # the user's own statement that the agent runs locally; the policy decides who may make it
        await require_feature(db, p.user, "local_only_tokens")
    if body.connector_ids:
        owned = set((await db.execute(select(Connector.id).where(Connector.user_id == p.user_id))).scalars())
        if not set(body.connector_ids) <= owned:
            raise ApiError(400, "common.unknown_connector")
    raw, t = await create_token(db, p.user, body.name, body.scopes, body.connector_ids, body.local_only,
                                body.send_policy.model_dump(), body.expires_at)
    return {"token": raw, "api_token": token_out(t), "item": token_out(t)}


@router.patch("/tokens/{tid}")
async def update_token(tid: int, body: dict, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    t = await db.get(ApiToken, tid)
    if not t or t.user_id != p.user_id:
        raise ApiError(404, "tokens.token_not_found")
    if "send_policy" in body:
        t.send_policy = SendPolicy(**body["send_policy"]).model_dump()
    if "name" in body:
        t.name = str(body["name"])[:200]
    if "scopes" in body:
        bad = set(body["scopes"]) - set(ALL_SCOPES)
        if bad or ({"admin", "tls"} & set(body["scopes"]) and p.user.role != "admin"):
            raise HTTPException(400, "bad scopes")  # i18n: internal
        t.scopes = sorted(set(body["scopes"]))
    if "local_only" in body:
        if body["local_only"]:
            from mailr.core.access import require_feature

            await require_feature(db, p.user, "local_only_tokens")
        t.local_only = bool(body["local_only"])
    await db.commit()
    return token_out(t)


@router.delete("/tokens/{tid}")
async def revoke_token(tid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _session_only(p)
    t = await db.get(ApiToken, tid)
    if not t or t.user_id != p.user_id:
        raise ApiError(404, "tokens.token_not_found")
    t.revoked = True
    await db.commit()
    return {"ok": True}


@router.get("/audit")
async def audit_log(token_id: int | None = None, cursor: str | None = None,
                    limit: int = Query(100, ge=1, le=500), p: Principal = Depends(current),
                    db: AsyncSession = Depends(get_db)):
    _session_only(p)
    stmt = select(AuditLog).where(AuditLog.user_id == p.user_id)
    if token_id:
        stmt = stmt.where(AuditLog.token_id == token_id)
    if cursor:
        stmt = stmt.where(AuditLog.id < json.loads(base64.urlsafe_b64decode(cursor))[0])
    rows = (await db.execute(stmt.order_by(AuditLog.id.desc()).limit(limit + 1))).scalars().all()
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [{"id": a.id, "token_id": a.token_id, "actor": a.actor, "action": a.action,
                   "target": a.target, "inputs": a.inputs, "result": a.result, "ok": a.ok,
                   "created_at": a.created_at.isoformat()} for a in rows],
        "next_cursor": base64.urlsafe_b64encode(json.dumps([rows[-1].id]).encode()).decode() if more else None,
    }
