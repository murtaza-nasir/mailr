"""Rules (filters), identities (send-as), people (contacts, blocking)."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.api.schemas import conversation_out, identity_out
from mailr.core import live, permissions
from mailr.core import rules as rulesmod
from mailr.core.audit import audit
from mailr.core.auth import Principal, current, scope
from mailr.core.jobs import enqueue
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import (
    Connector,
    Conversation,
    ForwardAddress,
    Identity,
    Label,
    Message,
    Person,
    Rule,
)

router = APIRouter()


# ---------------- rules ----------------


class RuleIn(BaseModel):
    name: str = ""
    query: str = Field(min_length=1)
    actions: dict = {}
    enabled: bool = True
    stop: bool = False
    apply_existing: bool = False
    # The version the editor started from (core/live.py): changes saved since then are merged in.
    base_version: int | None = None


RULE_FIELDS = ("name", "query", "actions", "enabled", "stop")


def rule_snapshot(r: Rule) -> dict:
    """A filter's fields as a live-edited item (core/live.py)."""
    return {"name": r.name or "", "query": r.query, "actions": dict(r.actions or {}), "enabled": bool(r.enabled),
            "stop": bool(r.stop)}


def rule_out(r: Rule, p=None) -> dict:
    """`hits` counts matches in all of the user's mail, so a token that cannot see all of it
    does not get it."""
    from mailr.core.visibility import is_restricted

    hide = p is not None and is_restricted(p)
    return {"id": r.id, "name": r.name, "query": r.query, "actions": r.actions, "position": r.position,
            "enabled": r.enabled, "stop": r.stop, "source": r.source, "hits": None if hide else r.hits,
            "scoped": bool(r.scope), "created_at": r.created_at.isoformat() if r.created_at else None}


async def _check_labels(db: AsyncSession, uid: int, actions: dict) -> None:
    ids = set(actions.get("add_labels") or []) | set(actions.get("remove_labels") or [])
    if ids:
        owned = set((await db.execute(select(Label.id).where(Label.user_id == uid, Label.id.in_(ids)))).scalars())
        if owned != ids:
            raise ApiError(400, "filters.unknown_label")


@router.get("/rules", tags=["rules"])
async def list_rules(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Rule).where(Rule.user_id == p.user_id).order_by(Rule.position, Rule.id))).scalars()
    return [rule_out(r, p) for r in rows]


async def _validate_rule(db: AsyncSession, user_id: int, body: RuleIn) -> None:
    try:
        rulesmod.validate_actions(body.actions)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    await _check_labels(db, user_id, body.actions)
    fwd = (body.actions or {}).get("forward_to")
    if fwd:
        addr = str(fwd).strip().lower()
        ok = (await db.execute(select(ForwardAddress.id).where(
            ForwardAddress.user_id == user_id, ForwardAddress.email == addr,
            ForwardAddress.verified_at.is_not(None)))).first()
        if not ok:
            raise ApiError(400, "forwarding.not_verified", address=addr)
        body.actions["forward_to"] = addr


async def apply_rule_op(db: AsyncSession, user, payload: dict) -> dict:
    """create | update | delete | apply | reorder. Shared by the routes and by approved agent
    requests (class rules.write)."""
    op = payload["op"]
    uid = user.id
    source = payload.get("source", "user")
    if op == "reorder":
        for pos, rid in enumerate(payload["ids"], start=1):
            await db.execute(update(Rule).where(Rule.id == int(rid), Rule.user_id == uid).values(position=pos))
        return {"ok": True}
    if op in ("create", "update"):
        body = RuleIn(**payload["rule"])
        await _validate_rule(db, uid, body)
        if op == "create":
            pos = (await db.execute(select(func.coalesce(func.max(Rule.position), 0)).where(Rule.user_id == uid))).scalar_one()
            r = Rule(user_id=uid, name=body.name, query=body.query, actions=body.actions,
                     enabled=body.enabled, stop=body.stop, position=pos + 1, source=source,
                     scope=payload.get("scope"))
            db.add(r)
            await db.flush()
        else:
            r = await db.get(Rule, int(payload["rule_id"]))
            if not r or r.user_id != uid:
                raise ApiError(404, "mail.rule_not_found")
            fields = {k: getattr(body, k) for k in RULE_FIELDS}
            if body.base_version is not None:
                # Made from an older version: merged with what changed since (core/live.py).
                await live.lock(db, uid, "rule", r.id, r)  # and read r again under it
                now = rule_snapshot(r)
                fields, _ = await live.resolve(db, uid, "rule", r.id, now, fields, body.base_version)
            r.name, r.query, r.actions, r.enabled, r.stop = (fields["name"], fields["query"], fields["actions"],
                                                             fields["enabled"], fields["stop"])
            if payload.get("scope"):
                r.scope = rulesmod.merge_scope(r.scope, payload["scope"])
        if body.apply_existing:
            await enqueue(db, "rules.apply", {"rule_id": r.id, "scope": payload.get("scope")}, user_id=uid,
                          dedupe_key=f"rules.apply:{r.id}")
        await db.flush()
        version = await live.commit(db, uid, "rule", r.id, rule_snapshot(r))
        out = rule_out(r)
        out["version"] = version
        if payload.get("scope"):
            out["hits"] = None
        return out
    r = await db.get(Rule, int(payload["rule_id"]))
    if not r or r.user_id != uid:
        raise ApiError(404, "mail.rule_not_found")
    if op == "delete":
        await db.delete(r)
        await live.forget(db, uid, "rule", r.id)
        return {"ok": True}
    if op == "apply":
        # A restricted token does not learn how much of the user's mail matches: no count for it.
        affected = None if payload.get("scope") else await rulesmod.count_matching(
            db, uid, r.query, extra=rulesmod.scope_condition(r.scope, None))
        await enqueue(db, "rules.apply", {"rule_id": r.id, "scope": payload.get("scope")}, user_id=uid,
                      dedupe_key=f"rules.apply:{r.id}")
        # A restricted token does not learn how much of the user's mail matches.
        return {"ok": True, "queued": True, **({} if payload.get("scope") else {"affected": affected})}
    raise HTTPException(400, f"unknown rule operation {op}")  # i18n: internal


async def _rule_request(db: AsyncSession, p: Principal, payload: dict, summary: str):
    """Run a rule change now (signed-in user) or through the permission engine (agents)."""
    if p.is_token:
        rule = payload.get("rule") or {}
        if (rule.get("actions") or {}).get("forward_to"):
            raise ApiError(403, "forwarding.agents_cannot_create")
        if payload["op"] == "update":
            existing = await db.get(Rule, int(payload["rule_id"]))
            if existing and existing.user_id == p.user_id and (existing.actions or {}).get("forward_to"):
                raise ApiError(403, "forwarding.agents_cannot_change")
        payload = {**payload, "source": "agent", "scope": rulesmod.scope_of(p)}
        if payload["op"] in ("create", "update"):
            await _validate_rule(db, p.user_id, RuleIn(**payload["rule"]))
        decision = await permissions.check(
            db, permissions.Actor.from_principal(p), "rules.write", target=summary, payload=payload,
            preview={"title": summary, "lines": [f"{k}: {v}" for k, v in (payload.get("rule") or {}).items()
                                                  if k in ("query", "actions", "name")]})
        permissions.raise_for(decision)
        if isinstance(decision, permissions.Pending):
            await audit(db, p, f"rules.{payload['op']}", str(payload.get("rule_id", "")), payload,
                        {"pending": decision.action.id})
            await db.commit()
            return JSONResponse(permissions.pending_response(decision.action), status_code=202)
    try:
        res = await apply_rule_op(db, p.user, payload)
    except live.LiveConflict as e:
        await db.rollback()
        return JSONResponse(e.body(), status_code=409)
    await audit(db, p, f"rules.{payload['op']}", str(payload.get("rule_id", res.get("id", ""))), payload)
    await db.commit()
    return res


@router.post("/rules", tags=["rules"])
async def create_rule(body: RuleIn, p: Principal = Depends(scope("rules")), db: AsyncSession = Depends(get_db)):
    return await _rule_request(db, p, {"op": "create", "rule": body.model_dump()}, f"Create filter: {body.query}")


@router.patch("/rules/{rid}", tags=["rules"])
async def update_rule(rid: int, body: RuleIn, p: Principal = Depends(scope("rules")),
                      db: AsyncSession = Depends(get_db)):
    r = await db.get(Rule, rid)
    if not r or r.user_id != p.user_id:
        raise ApiError(404, "mail.rule_not_found")
    return await _rule_request(db, p, {"op": "update", "rule_id": rid, "rule": body.model_dump()},
                               f"Change filter: {body.query}")


@router.delete("/rules/{rid}", tags=["rules"])
async def delete_rule(rid: int, p: Principal = Depends(scope("rules")), db: AsyncSession = Depends(get_db)):
    r = await db.get(Rule, rid)
    if not r or r.user_id != p.user_id:
        raise ApiError(404, "mail.rule_not_found")
    return await _rule_request(db, p, {"op": "delete", "rule_id": rid}, f"Delete filter: {r.query}")


@router.post("/rules/{rid}/apply", tags=["rules"])
async def apply_rule(rid: int, p: Principal = Depends(scope("rules")), db: AsyncSession = Depends(get_db)):
    r = await db.get(Rule, rid)
    if not r or r.user_id != p.user_id:
        raise ApiError(404, "mail.rule_not_found")
    return await _rule_request(db, p, {"op": "apply", "rule_id": rid}, f"Apply filter to existing mail: {r.query}")


# ---------------- forwarding addresses ----------------


class ForwardIn(BaseModel):
    email: str


class ForwardVerifyIn(BaseModel):
    email: str
    code: str


def _forward_out(f: ForwardAddress) -> dict:
    return {"email": f.email, "verified": f.verified_at is not None,
            "verified_at": f.verified_at.isoformat() if f.verified_at else None}


@router.get("/forwarding", tags=["rules"])
async def list_forwarding(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ForwardAddress).where(ForwardAddress.user_id == p.user_id)
                             .order_by(ForwardAddress.email))).scalars()
    return [_forward_out(f) for f in rows]


FORWARD_SUBJECT = "Mailr forwarding confirmation code:"
FORWARD_CODES_PER_HOUR = 3


@router.post("/forwarding", tags=["rules"])
async def add_forwarding(body: ForwardIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Send a confirmation code to the address; forwarding to it works once it is confirmed,
    as in Gmail. Only the signed-in user can do this."""
    import hashlib
    import secrets as pysecrets

    from mailr.core.sendgate import SendGate

    if p.is_token:
        raise ApiError(403, "forwarding.user_managed")
    addr = body.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", addr):
        raise ApiError(400, "common.invalid_email")
    from datetime import UTC, datetime, timedelta

    from mailr.models import Outbox

    # At most FORWARD_CODES_PER_HOUR codes per address and hour (also across remove + add again),
    # and a new code keeps the wrong-attempt count: guessing stays bounded.
    recent = (await db.execute(select(func.count()).select_from(Outbox).where(
        Outbox.user_id == p.user_id, Outbox.recipients.contains([addr]),
        Outbox.payload["subject"].astext.ilike(f"{FORWARD_SUBJECT}%"),
        Outbox.created_at > datetime.now(UTC) - timedelta(hours=1)))).scalar_one()
    if recent >= FORWARD_CODES_PER_HOUR:
        raise ApiError(429, "forwarding.too_many_codes")
    code = f"{pysecrets.randbelow(10**6):06d}"
    f = (await db.execute(select(ForwardAddress).where(ForwardAddress.user_id == p.user_id,
                                                       ForwardAddress.email == addr))).scalar_one_or_none()
    if f is None:
        f = ForwardAddress(user_id=p.user_id, email=addr, attempts=0)
        db.add(f)
    f.code_hash, f.verified_at = hashlib.sha256(code.encode()).hexdigest(), None
    await db.flush()
    await SendGate(db, p).submit({
        "to": [{"email": addr, "name": ""}],
        "subject": f"{FORWARD_SUBJECT} {code}",
        "text": (f"{p.user.name or p.user.email} asked Mailr to forward mail to {addr}.\n\n"
                 f"Confirmation code: {code}\n\nIf you did not expect this, ignore this message; "
                 "nothing is forwarded until the code is entered.")})
    await audit(db, p, "forwarding.add", addr, always=True)
    await db.commit()
    return _forward_out(f)


@router.post("/forwarding/verify", tags=["rules"])
async def verify_forwarding(body: ForwardVerifyIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    import hashlib
    import hmac
    from datetime import UTC, datetime

    if p.is_token:
        raise ApiError(403, "forwarding.user_managed")
    f = (await db.execute(select(ForwardAddress).where(ForwardAddress.user_id == p.user_id,
                                                       ForwardAddress.email == body.email.strip().lower()))).scalar_one_or_none()
    if f is None:
        raise ApiError(404, "forwarding.unknown_address")
    if f.attempts >= 5:
        raise ApiError(429, "forwarding.too_many_wrong")
    f.attempts += 1
    if not hmac.compare_digest(f.code_hash, hashlib.sha256(body.code.strip().encode()).hexdigest()):
        await db.commit()
        raise ApiError(400, "forwarding.wrong_code")
    f.verified_at = datetime.now(UTC)
    await audit(db, p, "forwarding.verify", f.email, always=True)
    await db.commit()
    return _forward_out(f)


@router.delete("/forwarding/{email}", tags=["rules"])
async def delete_forwarding(email: str, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "forwarding.user_managed")
    f = (await db.execute(select(ForwardAddress).where(ForwardAddress.user_id == p.user_id,
                                                       ForwardAddress.email == email.strip().lower()))).scalar_one_or_none()
    if f is None:
        raise ApiError(404, "forwarding.unknown_address")
    # Rules that forward there stop forwarding (the action is removed).
    for r in (await db.execute(select(Rule).where(Rule.user_id == p.user_id))).scalars():
        if (r.actions or {}).get("forward_to") == f.email:
            r.actions = {k: v for k, v in r.actions.items() if k != "forward_to"}
    await db.delete(f)
    await db.commit()
    return {"ok": True}


class ReorderIn(BaseModel):
    ids: list[int]


@router.post("/rules/reorder", tags=["rules"])
async def reorder(body: ReorderIn, p: Principal = Depends(scope("rules")), db: AsyncSession = Depends(get_db)):
    return await _rule_request(db, p, {"op": "reorder", "ids": body.ids}, "Reorder filters")


class PreviewIn(BaseModel):
    query: str


@router.post("/rules/preview", tags=["rules"])
async def preview(body: PreviewIn, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    from mailr.api.mail_read import token_views
    from mailr.core.visibility import visible_message_filter

    ids = await rulesmod.matching_message_ids(db, p.user_id, body.query, extra=visible_message_filter(p))
    convs = list((await db.execute(
        select(Conversation).where(Conversation.id.in_(
            select(Message.conversation_id).where(Message.id.in_(ids[:2000]))
        )).order_by(Conversation.last_date.desc()).limit(20)
    )).scalars())
    views = await token_views(db, p, [c.id for c in convs])
    return {"count": len(ids), "sample": [conversation_out(c, views.get(c.id)) for c in convs]}


_GMAIL_NS = {"atom": "http://www.w3.org/2005/Atom", "apps": "http://schemas.google.com/apps/2006"}


def gmail_filter_to_rule(props: dict[str, str], label_by_name: dict[str, int]) -> tuple[str, dict, list[str]]:
    """Translate one Gmail filter (mailFilters.xml properties) into (query, actions, missing labels)."""
    q = []
    for key, op in (("from", "from"), ("to", "to"), ("subject", "subject")):
        if props.get(key):
            v = props[key]
            q.append(f'{op}:"{v}"' if " " in v and not v.startswith("(") else f"{op}:{v}")
    if props.get("hasTheWord"):
        q.append(props["hasTheWord"])
    if props.get("doesNotHaveTheWord"):
        q.append(f"-{{{props['doesNotHaveTheWord']}}}")
    if props.get("hasAttachment") == "true":
        q.append("has:attachment")
    if props.get("size"):
        op = "larger" if props.get("sizeOperator") == "s_sl" else "smaller"
        unit = {"s_smb": "M", "s_skb": "K"}.get(props.get("sizeUnit", ""), "")
        q.append(f"{op}:{props['size']}{unit}")
    actions: dict = {}
    missing = []
    if props.get("label"):
        lid = label_by_name.get(props["label"].lower())
        if lid:
            actions["add_labels"] = [lid]
        else:
            missing.append(props["label"])
    if props.get("shouldArchive") == "true":
        actions["archive"] = True
    if props.get("shouldMarkAsRead") == "true":
        actions["mark_read"] = True
    if props.get("shouldStar") == "true":
        actions["star"] = True
    if props.get("shouldAlwaysMarkAsImportant") == "true":
        actions["important"] = True
    if props.get("shouldNeverMarkAsImportant") == "true":
        actions["important"] = False
    if props.get("shouldNeverSpam") == "true":
        actions["never_spam"] = True
    if props.get("shouldTrash") == "true":
        actions["trash"] = True
    if props.get("smartLabelToApply"):
        cat = props["smartLabelToApply"].replace("^smartlabel_", "")
        mapped = {"personal": "primary", "notification": "updates", "group": "forums", "promo": "promotions",
                  "social": "social", "receipt": "purchases", "finance": "purchases",
                  "travel": "updates"}.get(cat)
        if mapped:
            actions["category"] = mapped
    if props.get("forwardTo"):
        actions["forward_to"] = props["forwardTo"].strip().lower()
    return " ".join(q), actions, missing


GMAIL_IMPORT_MAX_BYTES = 2 * 1024 * 1024


@router.post("/rules/import-gmail", tags=["rules"])
async def import_gmail_filters(file: UploadFile = File(...), p: Principal = Depends(scope("rules")),
                               db: AsyncSession = Depends(get_db)):
    from mailr.core.store import user_store

    if p.is_token:
        raise ApiError(403, "filters.import_user_only")

    data = await file.read(GMAIL_IMPORT_MAX_BYTES + 1)
    if len(data) > GMAIL_IMPORT_MAX_BYTES:
        raise ApiError(413, "filters.file_limit_2mb")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise ApiError(400, "filters.not_gmail_xml") from None
    labels = list((await db.execute(select(Label).where(Label.user_id == p.user_id))).scalars())
    by_name = {lb.name.lower(): lb.id for lb in labels}
    store = await user_store(db, p.user)
    created = 0
    verified = set((await db.execute(select(ForwardAddress.email).where(
        ForwardAddress.user_id == p.user_id, ForwardAddress.verified_at.is_not(None)))).scalars())
    needs_verification: set[str] = set()
    for entry in root.findall("atom:entry", _GMAIL_NS):
        props = {el.get("name"): el.get("value") for el in entry.findall("apps:property", _GMAIL_NS)}
        query, actions, missing = gmail_filter_to_rule(props, by_name)
        for name in missing:
            lb = await store.create_label(db, name)
            by_name[name.lower()] = lb.id
        if missing:
            query, actions, _ = gmail_filter_to_rule(props, by_name)
        if not query:
            continue
        name = ""
        fwd = actions.get("forward_to")
        if fwd and fwd not in verified:
            # Forwarding needs a verified address (Settings → Mail → Filters & blocking → Forwarding addresses);
            # the filter is imported without it and the address is reported back.
            actions = {k: v for k, v in actions.items() if k != "forward_to"}
            needs_verification.add(fwd)
            name = f"Forward to {fwd} after verifying it"
            if not actions:
                actions = {}
        pos = (await db.execute(select(func.coalesce(func.max(Rule.position), 0)).where(Rule.user_id == p.user_id))).scalar_one()
        db.add(Rule(user_id=p.user_id, name=name, query=query, actions=actions, position=pos + 1, source="gmail-import"))
        await db.flush()
        created += 1
    await db.commit()
    return {"created": created, "imported": created, "forwarding_needs_verification": sorted(needs_verification)}


# ---------------- identities ----------------


class IdentityIn(BaseModel):
    email: str
    name: str = ""
    reply_to: str = ""
    signature_html: str = ""
    is_default: bool = False
    connector_id: int | None = None
    receives: list[str] = []


@router.get("/identities", tags=["identities"])
async def list_identities(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Identity).where(Identity.user_id == p.user_id).order_by(Identity.id))).scalars()
    return [identity_out(i) for i in rows]


async def _validate_identity(db: AsyncSession, p: Principal, body: IdentityIn) -> None:
    if "@" not in body.email:
        raise ApiError(400, "common.invalid_email")
    if body.connector_id is not None:
        c = await db.get(Connector, body.connector_id)
        if not c or c.user_id != p.user_id:
            raise ApiError(400, "common.unknown_connector")


@router.post("/identities", tags=["identities"])
async def create_identity(body: IdentityIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "identities.user_managed")
    await _validate_identity(db, p, body)
    from mailr.core.compose import clean_html

    i = Identity(user_id=p.user_id, **{**body.model_dump(), "email": body.email.lower(),
                                        "signature_html": clean_html(body.signature_html)})
    if body.is_default:
        await db.execute(update(Identity).where(Identity.user_id == p.user_id).values(is_default=False))
    db.add(i)
    await db.commit()
    return identity_out(i)


@router.patch("/identities/{iid}", tags=["identities"])
async def update_identity(iid: int, body: IdentityIn, p: Principal = Depends(current),
                          db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "identities.user_managed")
    i = await db.get(Identity, iid)
    if not i or i.user_id != p.user_id:
        raise ApiError(404, "identities.identity_not_found")
    await _validate_identity(db, p, body)
    from mailr.core.compose import clean_html

    if body.is_default:
        await db.execute(update(Identity).where(Identity.user_id == p.user_id).values(is_default=False))
    for k, v in body.model_dump().items():
        setattr(i, k, clean_html(v) if k == "signature_html" else v)
    i.email = body.email.lower()
    await db.commit()
    return identity_out(i)


@router.delete("/identities/{iid}", tags=["identities"])
async def delete_identity(iid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "identities.user_managed")
    i = await db.get(Identity, iid)
    if not i or i.user_id != p.user_id:
        raise ApiError(404, "identities.identity_not_found")
    await db.delete(i)
    await db.commit()
    return {"ok": True}


# ---------------- people ----------------


def include_local(p) -> bool:
    """Details learned from local-only mail or a local-only address book reach a token only when
    the token itself is local-only."""
    return p is None or p.token is None or bool(p.token.local_only)


def _local_field(name: str):
    """SQL: the person's `name` field came from a local-only source (provenance)."""
    return func.coalesce(Person.provenance[name]["local_only"].astext == "true", False)


def person_out(x: Person, local: bool = True) -> dict:
    from mailr.core.people import strength_now

    prov = {k: v for k, v in (x.provenance or {}).items() if local or not (v or {}).get("local_only")}
    hidden = set(x.provenance or {}) - set(prov)
    keep = lambda items: [i for i in items or [] if local or not i.get("local_only")]  # noqa: E731
    return {"email": x.email, "name": "" if "name" in hidden else x.name,
            "organization": "" if "organization" in hidden else x.organization,
            "received_count": x.received_count, "sent_count": x.sent_count,
            "first_seen": x.first_seen.isoformat() if x.first_seen else None,
            "last_seen": x.last_seen.isoformat() if x.last_seen else None,
            "last_sent": x.last_sent.isoformat() if x.last_sent else None,
            "blocked": x.blocked, "notes": x.notes,
            # Ranking, contact details and where each came from.
            "strength": strength_now(x.strength), "two_way": bool(x.sent_count and x.received_count),
            "title": "" if "title" in hidden else (x.title or ""),
            "birthday": "" if "birthday" in hidden else (x.birthday or ""),
            "phones": keep(x.phones), "addresses": keep(x.addresses),
            "sources": {k: (v or {}).get("source") for k, v in prov.items()}}


# ACT person.find @api (ranked by strength)
@router.get("/people", tags=["people"])
async def people(q: str = "", limit: Annotated[int, Query(ge=1, le=1000)] = 10, blocked: bool = False, offset: Annotated[int, Query(ge=0)] = 0, p: Principal = Depends(scope("read")),
                 db: AsyncSession = Depends(get_db)):
    if blocked:
        rows = (await db.execute(select(Person).where(Person.user_id == p.user_id, Person.blocked.is_(True))
                                 .order_by(Person.email).limit(min(limit, 1000)))).scalars()
        return [person_out(x, include_local(p)) for x in rows]
    stmt = select(Person).where(Person.user_id == p.user_id, Person.blocked.is_(False))
    if q:
        like = f"%{q.lower()}%"
        if include_local(p):
            stmt = stmt.where(or_(func.lower(Person.email).like(like), func.lower(Person.name).like(like),
                                  func.lower(Person.organization).like(like)))
        else:
            # Only the fields this caller is shown: a name or organisation from a local-only
            # source is blanked in the answer, so it must not decide the match either.
            stmt = stmt.where(or_(func.lower(Person.email).like(like),
                                  func.lower(Person.name).like(like) & ~_local_field("name"),
                                  func.lower(Person.organization).like(like) & ~_local_field("organization")))
    from mailr.core.visibility import is_restricted, visible_message_filter

    if is_restricted(p):
        # Only correspondents that appear in mail this token may see.
        stmt = stmt.where(select(Message.id).where(
            Message.user_id == p.user_id, Message.from_email == Person.email,
            visible_message_filter(p)).exists())
    # Order: strength (recency-weighted, two-way first), then the counts, then recency.
    from mailr.core.people import fallback_expr, rank_expr

    if is_restricted(p):
        # Strength, counts and last_seen add up all mail, local-only and other accounts
        # included, so the order alone would hint at mail this token may not see. A restricted
        # token gets the order of the newest message it may see from each person instead.
        newest = (select(func.max(Message.date)).where(
            Message.user_id == p.user_id, Message.from_email == Person.email, Message.deleted.is_(False),
            visible_message_filter(p)).correlate(Person).scalar_subquery())
        stmt = stmt.order_by(newest.desc().nulls_last(), Person.email)
    else:
        stmt = stmt.order_by(rank_expr().desc(), fallback_expr().desc(), Person.last_seen.desc().nulls_last())
    stmt = stmt.offset(max(0, offset)).limit(min(limit, 200))
    loc = include_local(p)
    rows = [person_out(x, loc) for x in (await db.execute(stmt)).scalars()]
    if offset == 0 and not is_restricted(p):
        rows += await _people_without_mail(db, p, q)
    return rows


async def _people_without_mail(db: AsyncSession, p: Principal, q: str) -> list[dict]:
    """People Ambrose knows who are not contacts (no address, or none you have mail with): one
    directory lists both. Each row has `entity_id` and an empty `email`; its card is the person's
    notes (GET /assistant/entities/{id})."""
    from mailr.knowledge import memory
    from mailr.models import Entity

    # An entry whose address already has a contact row is that contact: its card shows the notes by
    # address (memory.entity_for_email). Without this check a person Ambrose learnt about before any
    # mail with them showed twice once mail arrived (the link is only made when the entry is saved).
    has_contact = select(Person.id).where(Person.user_id == p.user_id, Person.blocked.is_(False),
                                          Person.email == func.any(Entity.emails)).exists()
    stmt = select(Entity).where(memory.ent_filter(p), Entity.kind == "person", Entity.is_self.is_(False),
                                Entity.person_id.is_(None), ~has_contact)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Entity.name).like(like),
                              func.lower(func.array_to_string(Entity.aliases, " ")).like(like)))
    return [{"email": "", "entity_id": e.id, "name": e.name, "organization": "", "title": e.summary or "",
             "emails": list(e.emails or []),
             "received_count": 0, "sent_count": 0, "last_seen": None, "two_way": False, "no_mail": True}
            for e in (await db.execute(stmt.order_by(Entity.name).limit(50))).scalars()]


@router.get("/people/blocked", tags=["people"])
async def blocked(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Person).where(Person.user_id == p.user_id, Person.blocked.is_(True)))).scalars()
    return [person_out(x, include_local(p)) for x in rows]


@router.get("/people/{email}", tags=["people"])
async def person(email: str, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    from mailr.api.mail_read import token_views
    from mailr.core.visibility import is_restricted, visible_message_filter

    x = (await db.execute(select(Person).where(Person.user_id == p.user_id, Person.email == email.lower()))).scalar_one_or_none()
    if not x:
        raise ApiError(404, "people.unknown_person")
    vis = visible_message_filter(p)
    if is_restricted(p) and not (await db.execute(select(Message.id).where(
            Message.user_id == p.user_id, Message.from_email == x.email, vis).limit(1))).first():
        raise ApiError(404, "people.unknown_person")
    conv_ids = select(Message.conversation_id).where(
        Message.user_id == p.user_id, vis,
        or_(Message.from_email == x.email,
            text("messages.\"to\" @> CAST(:j AS jsonb) OR messages.cc @> CAST(:j AS jsonb)").bindparams(
                j=json.dumps([{"email": x.email}]))),
    )
    # The person's messages come from ix_msg_from and the GIN jsonb_path_ops indexes ix_msg_to and
    # ix_msg_cc; the user_id condition lets a frequent correspondent's page walk ix_conv_user_last
    # newest first and stop at ten instead of sorting every conversation.
    recent = (await db.execute(select(Conversation).where(Conversation.user_id == p.user_id,
                                                          Conversation.id.in_(conv_ids))
                               .order_by(Conversation.last_date.desc()).limit(10))).scalars().all()
    # Open threads: last message is from them and I have not replied. Only conversations with a
    # message from them are looked at (ix_msg_from), not every conversation's last message: the
    # set is made first (MATERIALIZED), because as a semi-join the planner walked the whole mailbox
    # newest first looking for ten, which for someone who only appears in to or cc never ends.
    open_rows = await db.execute(text(
        """WITH theirs AS MATERIALIZED (
             SELECT DISTINCT conversation_id FROM messages WHERE user_id=:u AND from_email=:e AND NOT deleted)
           SELECT c.id FROM theirs t JOIN conversations c ON c.id = t.conversation_id JOIN LATERAL (
             SELECT from_email, sent_by_me FROM messages m WHERE m.conversation_id=c.id AND NOT m.deleted
             AND NOT m.draft ORDER BY m.date DESC NULLS LAST LIMIT 1) last ON true
           WHERE c.user_id=:u AND last.from_email=:e AND NOT last.sent_by_me
           ORDER BY c.last_date DESC LIMIT 10"""), {"u": p.user_id, "e": x.email})
    open_ids = [r.id for r in open_rows]
    if is_restricted(p) and open_ids:
        seen = set((await db.execute(select(Message.conversation_id).where(
            Message.conversation_id.in_(open_ids), vis).distinct())).scalars())
        open_ids = [c for c in open_ids if c in seen]
    views = await token_views(db, p, [c.id for c in recent])
    # The groups this person is in, and (for the user) details waiting for Accept.
    from mailr.contacts import groups as gm
    from mailr.contacts import signatures

    in_groups = [{"id": g["id"], "name": g["name"], "colour": g["colour"], "source": g["source"]}
                 for g in await gm.list_groups(db, p, limit=500) if any(m["email"] == x.email for m in g["members"])]
    sugg = [] if p.is_token else [signatures.suggestion_out(s) for s in await signatures.pending(db, p.user_id, x.email)]
    knowledge = await _knowledge(db, p, x.email)
    await audit(db, p, "people.get", email)
    await db.commit()
    return {**person_out(x, include_local(p)), "recent_conversations": [conversation_out(c, views.get(c.id)) for c in recent],
            "open_threads": open_ids, "groups": in_groups, "suggestions": sugg, "knowledge": knowledge}


async def _knowledge(db: AsyncSession, p: Principal, email: str) -> dict:
    """What Ambrose knows about this contact: the person entity linked to it (or carrying its
    address) and that person's notes, the ones this caller may see. The person card shows it."""
    from mailr.knowledge import memory
    from mailr.models import Memory

    ent = await memory.entity_for_email(db, p, email)
    if ent is None:
        return {"entity": None, "memories": []}
    notes = list((await db.execute(select(Memory).where(memory.mem_filter(p, include_expired=True),
                                                        Memory.entity_id == ent.id)
                                   .order_by(Memory.pinned.desc(), Memory.updated_at.desc()).limit(50))).scalars())
    return {"entity": memory.entity_out(ent, notes=len(notes)), "memories": await memory.with_entities(db, notes, p)}


async def set_blocked(db: AsyncSession, user_id: int, email: str, blocked: bool) -> None:
    if blocked:
        await db.execute(text(
            "INSERT INTO people (user_id, email, name, organization, received_count, sent_count, notes, blocked) "
            "VALUES (:u, :e, '', '', 0, 0, '', true) ON CONFLICT (user_id, email) DO UPDATE SET blocked=true"),
            {"u": user_id, "e": email.lower()})
    else:
        await db.execute(update(Person).where(Person.user_id == user_id, Person.email == email.lower())
                         .values(blocked=False))


async def _block_gate(db: AsyncSession, p: Principal, email: str, blocked: bool):
    """Blocking (or unblocking) a sender sends their mail to Spam: class mail.spam for tokens."""
    if not p.is_token:
        return None
    title = f"{'Block' if blocked else 'Unblock'} {email.lower()}"
    decision = await permissions.check(
        db, permissions.Actor.from_principal(p), "mail.spam", target=email.lower(),
        payload={"op": "block" if blocked else "unblock", "email": email.lower()},
        preview={"title": title, "kind": "people", "danger": blocked,
                 "lines": [f"Future mail from {email.lower()} goes to Spam" if blocked
                           else f"Mail from {email.lower()} is no longer sent to Spam"]})
    permissions.raise_for(decision)
    if isinstance(decision, permissions.Pending):
        await audit(db, p, "people.block" if blocked else "people.unblock", email, {}, {"pending": decision.action.id})
        await db.commit()
        return JSONResponse(permissions.pending_response(decision.action), status_code=202)
    return None


@router.post("/people/{email}/block", tags=["people"])
async def block(email: str, p: Principal = Depends(scope("rules")), db: AsyncSession = Depends(get_db)):
    held = await _block_gate(db, p, email, True)
    if held is not None:
        return held
    await set_blocked(db, p.user_id, email, True)
    await audit(db, p, "people.block", email)
    await db.commit()
    return {"ok": True}


@router.delete("/people/{email}/block", tags=["people"])
async def unblock(email: str, p: Principal = Depends(scope("rules")), db: AsyncSession = Depends(get_db)):
    held = await _block_gate(db, p, email, False)
    if held is not None:
        return held
    await set_blocked(db, p.user_id, email, False)
    await db.commit()
    return {"ok": True}
