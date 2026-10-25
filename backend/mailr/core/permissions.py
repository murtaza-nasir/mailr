"""Unified permission engine for agent actions.

Agents (API tokens and the built-in assistant) are gated by allow / ask / deny rules, like
Claude Code's permissions. A person using the UI with their own session is never gated.

    decision = await check(db, actor, "calendar.create", target=..., ctx=..., payload=..., preview=...)

`ask` stores a PendingAction; when the user approves it, the worker runs the registered
executor exactly once (conditional status transitions: pending → approved → executing →
executed | failed).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.config import get_settings
from mailr.core import publicid
from mailr.core.events import notify
from mailr.core.jobs import enqueue, handler
from mailr.db import get_existing
from mailr.models import ApiToken, Outbox, PendingAction, PermissionRule, User

log = logging.getLogger("mailr.permissions")

# class -> (default effect, token scope that must be present)
CLASSES: dict[str, tuple[str, str]] = {
    "mail.read": ("allow", "read"),
    "mail.draft": ("allow", "draft"),
    "mail.label": ("allow", "label"),
    "mail.send": ("ask", "send"),
    "mail.trash": ("ask", "label"),
    "mail.spam": ("ask", "label"),
    "mail.delete": ("ask", "delete"),
    "mail.unsubscribe": ("ask", "send"),
    # A sender screener decision (core/screener.py): allow, block or send to a label. Always asks.
    "mail.screener": ("ask", "label"),
    "rules.write": ("ask", "rules"),
    # Standing orders and routines for the assistant.
    "orders.write": ("ask", "rules"),
    # A notification to the user themselves (phone and web); never to anyone else.
    "notify.send": ("allow", "read"),
    "calendar.read": ("allow", "calendar"),
    "calendar.create": ("ask", "calendar"),
    "calendar.update": ("ask", "calendar"),
    "calendar.delete": ("ask", "calendar"),
    "calendar.rsvp": ("ask", "send"),
    # Event writes that mail the guests (calendar/organizer.py): invitations and updates, and
    # cancellations. Checked as well as the write class; one approval card covers both.
    "calendar.invite": ("ask", "send"),
    "calendar.cancel": ("ask", "send"),
    "tasks.read": ("allow", "calendar"),
    "tasks.create": ("allow", "calendar"),
    "tasks.update": ("allow", "calendar"),
    "tasks.complete": ("allow", "calendar"),
    "tasks.delete": ("ask", "calendar"),
    # Task lists (create allowed; a rename or delete asks unless a rule allows it), bulk task
    # actions (a bulk delete asks the same way) and calendar collections.
    "tasks.lists": ("allow", "calendar"),
    "tasks.bulk": ("allow", "calendar"),
    "calendar.manage": ("ask", "calendar"),
    "contacts.read": ("allow", "read"),
    # Contact groups: create, rename, change members, delete.
    "contacts.write": ("ask", "label"),
    "labels.write": ("ask", "label"),
    "labels.delete": ("ask", "label"),
    "prefs.write": ("ask", "rules"),
    "memory.read": ("allow", "read"),
    "memory.write": ("ask", "memory"),
    "skills.write": ("ask", "memory"),
    "settings.write": ("deny", "admin"),
    # Links between Speakr recordings and events, conversations and tasks.
    "speakr.link": ("allow", "recordings"),
    # Writes to Speakr (integrations/speakr/writes.py): an upload starts transcription; an edit
    # changes the title, participants or notes; a share creates a public link and always asks.
    "speakr.upload": ("ask", "recordings"),
    "speakr.write": ("ask", "recordings"),
    "speakr.share": ("ask", "recordings"),
    # The assistant's web search and page reading (agent/web_tools.py). Allowed by default: the
    # query is shown in the chat's steps, and nothing from local-only mail may go into it. A user
    # who wants to see each query first sets it to ask.
    "web.search": ("allow", "read"),
    # Notes, checklists and their labels (notes/gate.py).
    # A single item's add, check, edit or delete is notes.edit and does not ask: the note's history
    # keeps it. A note to Trash and clearing checked items are notes.delete. Deleting a label asks
    # unless a rule allows it (notes/gate.py).
    "notes.read": ("allow", "notes"),
    "notes.create": ("allow", "notes"),
    "notes.edit": ("allow", "notes"),
    "notes.delete": ("ask", "notes"),
    "notes.labels": ("ask", "notes"),
}
# Tables (agent/table_tools.py). tables.schema
# allows creating a table, adding a column and saving a view; its other changes (rename, retype,
# reorder, delete or restore columns, options, deleting views, a restore to a point in time) ask
# by default through `ask_by_default`. A table to Trash is notes.delete (ask).
CLASSES.update({
    "tables.rows": ("allow", "notes"),
    "tables.rows_delete": ("ask", "notes"),
    "tables.schema": ("allow", "notes"),
})
# Notes the user forwards from a trusted address (core/trusted.py, assistant/forwards.py): the
# permission of the built-in standing order "Follow my forwarded notes". Ask (default): each
# forwarded note waits for a click. Allow: Ambrose acts at once, and every step still passes its own
# class (sending keeps its stricter rules). Off is the order turned off.
CLASSES["agent.forwarded_instructions"] = ("ask", "read")
# Classes a standing order shows and sets (Settings → Ambrose → Standing orders); the Permissions
# page leaves them out. Their rules still apply, and a grant from an approval card still makes one.
ORDER_CLASSES = frozenset({"agent.forwarded_instructions"})
# Classes that ask even when a rule or a grant allows them (as the first message to a new
# address does): a public share link can be opened by anyone.
ALWAYS_ASK: dict[str, str] = {"speakr.share": "hard rule: a public share link",
                              # A stranger's mail could tell an agent to let its sender in.
                              "mail.screener": "hard rule: a Screener decision is yours"}
# Built-in defaults that differ for the signed-in user's own assistant. It may keep notes
# (visible and editable in Settings → Ambrose) without a click; tokens are asked.
ASSISTANT_DEFAULTS: dict[str, str] = {"memory.write": "allow", "labels.write": "allow",
                                      # Note labels: the user's own, visible and undoable.
                                      "notes.labels": "allow"}


def default_effect(action: str, actor_kind: str) -> str:
    if actor_kind == "assistant" and action in ASSISTANT_DEFAULTS:
        return ASSISTANT_DEFAULTS[action]
    return CLASSES[action][0]
EFFECTS = ("allow", "ask", "deny")
SESSION_GRANT_HOURS = 12
CONSTRAINT_KEYS = {"replies_only", "known_only", "domains", "recipients", "identities", "max_per_hour",
                   "max_per_day", "connectors", "labels", "calendars", "lists", "tags", "notes"}


# ---------------------------------------------------------------- actors


@dataclass
class Actor:
    user: User
    kind: str  # user | token | assistant
    token: ApiToken | None = None
    chat_session_id: str | None = None
    # The assistant runs a background errand (its chat context says so; see is_errand).
    errand: bool = False

    @property
    def user_id(self) -> int:
        return self.user.id

    @property
    def api_token(self) -> ApiToken:
        """The token of a token actor (kind "token")."""
        if self.token is None:
            raise RuntimeError(f"a {self.kind} actor has no token")
        return self.token

    @classmethod
    def from_principal(cls, p, *, assistant: bool = False, chat_session_id: str | None = None,
                       errand: bool = False) -> Actor:
        if p.token is not None:
            return cls(user=p.user, kind="token", token=p.token)
        if assistant:
            return cls(user=p.user, kind="assistant", chat_session_id=chat_session_id, errand=errand)
        return cls(user=p.user, kind="user")

    @classmethod
    def assistant(cls, user: User, chat_session_id: str | None) -> Actor:
        return cls(user=user, kind="assistant", chat_session_id=chat_session_id)

    @property
    def subject(self) -> str:
        if self.kind == "token":
            return f"token:{self.api_token.id}"
        if self.kind == "assistant":
            return f"session:{self.chat_session_id}" if self.chat_session_id else "assistant"
        return "user"

    def subjects(self) -> list[list[str]]:
        """Subject levels, most specific first."""
        if self.kind == "token":
            return [[f"token:{self.api_token.id}"], ["*"]]
        levels = []
        if self.chat_session_id:
            levels.append([f"session:{self.chat_session_id}"])
        return levels + [["assistant"], ["*"]]

    @property
    def name(self) -> str:
        if self.kind == "token":
            return self.api_token.name
        return "Ambrose" if self.kind == "assistant" else "you"


@dataclass
class Allowed:
    reason: str = ""
    rule_id: int | None = None


@dataclass
class Pending:
    action: PendingAction


@dataclass
class Denied:
    reason: str


Decision = Allowed | Pending | Denied


# ---------------------------------------------------------------- evaluation


def _action_matches(rule_action: str, action: str, effect: str | None = None) -> bool:
    if rule_action == action:
        return True
    # A standing order's own class (ORDER_CLASSES) is allowed or asked only by its exact name:
    # `*` or `agent.*` on the Permissions page must not turn "Follow my forwarded notes" to Allow
    # unseen. A broad deny still stops it.
    if action in ORDER_CLASSES and effect != "deny":
        return False
    if rule_action == "*":
        return True
    return rule_action.endswith(".*") and action.startswith(rule_action[:-1])


def _domain_ok(email: str, domains: list[str]) -> bool:
    d = email.rsplit("@", 1)[-1].lower()
    return any(d == x.lower().lstrip("@") or d.endswith("." + x.lower().lstrip("@")) for x in domains)


SEND_COUNT_LOCK = 0x6D61696C  # advisory-lock namespace for agent send counting ("mail")


async def _count_sends(db: AsyncSession, actor: Actor, window: timedelta, level: str | None = None) -> int:
    """Agent sends in the window, counted at the level of the rule that sets the limit: a
    `token:<id>` rule counts that token, `session:<id>` that chat, `assistant` every assistant
    chat, `*` every agent. A limit therefore never resets because a new chat was started."""
    since = datetime.now(UTC) - window
    level = level or actor.subject
    # Count and insert must not interleave: concurrent sends of one user wait here until the
    # earlier transaction commits, so each count sees the rows queued before it. The
    # lock is per user (every level of a user's limits shares it) and ends with the transaction.
    await db.execute(text("SELECT pg_advisory_xact_lock(:k, :u)"), {"k": SEND_COUNT_LOCK, "u": actor.user_id})
    conds = [Outbox.user_id == actor.user_id, Outbox.created_at > since,
             Outbox.status.in_(("queued", "sending", "sent", "uncertain"))]
    if level.startswith("token:"):
        conds.append(Outbox.token_id == int(level.split(":", 1)[1]))
    elif level == "assistant":
        conds += [Outbox.token_id.is_(None), or_(Outbox.subject == "assistant", Outbox.subject.like("session:%"))]
    elif level == "*":
        conds.append(Outbox.subject != "user")
    else:
        conds.append(Outbox.subject == level)
    return (await db.execute(select(func.count()).select_from(Outbox).where(*conds))).scalar_one()


def _unset(val: Any) -> bool:
    # 0 is a real limit ("none allowed"); only absent values are skipped.
    return val is None or val is False or val == "" or val == []


def _hits(got: set, listed: set, *, every: bool) -> bool:
    """`allow` rules need every value listed; `deny` and `ask` rules match when any value is,
    so adding one more recipient, account or label never slips past a deny."""
    if not got:
        return False
    return got <= listed if every else bool(got & listed)


async def constraints_match(db: AsyncSession, actor: Actor, action: str, cons: dict[str, Any],
                            ctx: dict[str, Any], *, effect: str = "allow", level: str | None = None) -> bool:
    """True when the request satisfies every constraint of a rule. For list constraints an
    `allow` rule needs all values of the request to be listed, a `deny`/`ask` rule any of them."""
    if not cons:
        return True
    every = effect == "allow"
    rcpts = [r.lower() for r in ctx.get("recipients") or []]
    for key, val in cons.items():
        if _unset(val):
            continue
        if key == "replies_only":
            parts = {x.lower() for x in ctx.get("participants") or []}
            if not ctx.get("reply") or not rcpts or not set(rcpts) <= parts:
                return False
        elif key == "known_only":
            if ctx.get("unknown_recipients"):
                return False
        elif key == "domains":
            if not rcpts:
                return False
            ok = [_domain_ok(r, val) for r in rcpts]
            if not (all(ok) if every else any(ok)):
                return False
        elif key == "recipients":
            if not _hits(set(rcpts), {x.lower() for x in val}, every=every):
                return False
        elif key == "identities":
            if ctx.get("identity_id") not in val:
                return False
        elif key == "max_per_hour":
            if await _count_sends(db, actor, timedelta(hours=1), level) >= int(val):
                return False
        elif key == "max_per_day":
            if await _count_sends(db, actor, timedelta(days=1), level) >= int(val):
                return False
        elif key == "connectors":
            if not _hits(set(ctx.get("connectors") or []), set(val), every=every):
                return False
        elif key == "labels":
            if action.startswith("notes."):
                # The viewer's note labels on the note (names; case does not matter).
                if not _hits({str(x).lower() for x in ctx.get("labels") or []}, {str(x).lower() for x in val},
                             every=every):
                    return False
            elif not _hits(set(ctx.get("labels") or []), set(val), every=every):
                return False
        elif key == "calendars":
            cals = {c for c in [ctx.get("calendar"), *(ctx.get("calendars") or [])] if c}
            if not _hits(cals, set(val), every=every):
                return False
        elif key == "lists":
            lists = {c for c in [ctx.get("list"), *(ctx.get("lists") or [])] if c}
            if not _hits(lists, set(val), every=every):
                return False
        elif key == "notes":
            # Notes and tables by public id: the one the request actually writes (notes/gate.py).
            if not _hits(set(ctx.get("notes") or []), {str(x) for x in val}, every=every):
                return False
        elif key == "tags":
            # Speakr tag names of the recording (speakr.write / speakr.share only on, say, Advising).
            if not _hits({str(t).lower() for t in ctx.get("tags") or []}, {str(t).lower() for t in val}, every=every):
                return False
    return True


async def _rules(db: AsyncSession, user_id: int, subjects: list[str]) -> list[PermissionRule]:
    now = datetime.now(UTC)
    return list((await db.execute(select(PermissionRule).where(
        PermissionRule.user_id == user_id, PermissionRule.subject.in_(subjects),
        or_(PermissionRule.expires_at.is_(None), PermissionRule.expires_at > now)))).scalars())


SEND_POLICY_KEYS = ("replies_only", "known_only", "domains", "identities", "max_per_hour", "max_per_day")


def _policy_rule(actor: Actor) -> PermissionRule | None:
    """A token's send policy (`mode: policy`) acts as an `allow` rule for mail.send at the
    token's level, with the policy's limits as constraints."""
    if actor.kind != "token":
        return None
    pol = actor.api_token.send_policy or {}
    if pol.get("mode") != "policy":
        return None
    return PermissionRule(id=None, user_id=actor.user_id, subject=f"token:{actor.api_token.id}", action="mail.send",
                          effect="allow", constraints={k: pol[k] for k in SEND_POLICY_KEYS if k in pol},
                          source="send_policy")


def _src(r: PermissionRule) -> str:
    return f"rule:{r.id}" if r.id is not None else "send policy"


async def resolve(db: AsyncSession, actor: Actor, action: str, ctx: dict[str, Any] | None = None, *,
                  extra_rules: tuple[PermissionRule, ...] = ()) -> tuple[str, str, PermissionRule | None]:
    """(effect, source, rule) for this actor and action. Scope and hard rules not applied.
    `extra_rules` are rules not stored yet (a grant being weighed: would_cover)."""
    ctx = ctx or {}
    levels = actor.subjects()
    rules = await _rules(db, actor.user_id, [s for lvl in levels for s in lvl])
    rules += [r for r in extra_rules if r.user_id == actor.user_id]
    pol = _policy_rule(actor) if action == "mail.send" else None
    if pol is not None:
        rules.append(pol)
    applicable = [r for r in rules if _action_matches(r.action, action, r.effect)
                  and await constraints_match(db, actor, action, r.constraints or {}, ctx, effect=r.effect,
                                              level=r.subject)]
    for r in applicable:
        if r.effect == "deny":
            return "deny", _src(r), r
    for lvl in levels:
        at = [r for r in applicable if r.subject in lvl]
        ask = next((r for r in at if r.effect == "ask"), None)
        if ask:
            return "ask", _src(ask), ask
        allow = next((r for r in at if r.effect == "allow"), None)
        if allow:
            return "allow", _src(allow), allow
    return default_effect(action, actor.kind), "default", None


# Classes whose effect sends mail to someone.
MAIL_OUT = ("mail.send", "mail.unsubscribe", "calendar.rsvp", "calendar.invite", "calendar.cancel")
ERRAND_NO_SEND = "hard rule: a background errand never sends mail by itself"


async def is_errand(db: AsyncSession, actor: Actor) -> bool:
    """True when the actor is the assistant running a background errand (assistant/errands.py):
    its chat session carries `errand` in its context, which only the server sets."""
    if actor.kind != "assistant":
        return False
    if actor.errand:
        return True
    if not actor.chat_session_id:
        return False
    from mailr.models import ChatSession

    s = await db.get(ChatSession, actor.chat_session_id)
    return bool(s is not None and s.user_id == actor.user_id and (s.context or {}).get("errand"))


def default_ttl() -> timedelta:
    return timedelta(hours=get_settings().approval_ttl_hours)


REQUESTER_KEY = "_token_id"


def stamp(payload: dict[str, Any] | None, actor_or_p: Any) -> dict[str, Any]:
    """The payload an executor receives, with the requesting token recorded (never taken from
    the caller's own arguments), so an executor keeps that token's restrictions."""
    out = {k: v for k, v in (payload or {}).items() if k != REQUESTER_KEY}
    tok = getattr(actor_or_p, "token", None)
    if tok is not None:
        out[REQUESTER_KEY] = tok.id
    return out


async def requester(db: AsyncSession, user: User, payload: dict[str, Any]):
    """The principal an executor acts as: the requesting token (its connector limits and
    local-only flag still apply after approval), or the user's full rights for the user's own
    assistant and for the signed-in user."""
    tid = (payload or {}).get(REQUESTER_KEY)
    if tid is None:
        from mailr.core.sendgate import _SystemPrincipal

        return _SystemPrincipal(user)
    from mailr.core.auth import Principal

    tok = await db.get(ApiToken, int(tid))
    if tok is None or tok.user_id != user.id or tok.revoked:
        raise HTTPException(403, "the token that asked for this no longer exists")
    return Principal(user=user, token=tok, scopes=set(tok.scopes or []))


def scope_denial(actor: Actor, action: str) -> str | None:
    """The reason a token may not ask for `action` at all: it lacks the class's scope."""
    if actor.kind == "token":
        scope = CLASSES[action][1]
        if scope not in (actor.api_token.scopes or []):
            return f"this token lacks the '{scope}' scope needed for {action}"
    return None


async def evaluate(db: AsyncSession, actor: Actor, action: str, ctx: dict[str, Any], *,
                   force_ask: str | None = None, ask_by_default: str | None = None,
                   extra_rules: tuple[PermissionRule, ...] = ()) -> tuple[str, str, PermissionRule | None]:
    """(effect, source, rule) with every hard rule applied: what `check` decides, without
    storing anything. The scope check is separate (scope_denial). `ask_by_default` (a reason)
    turns the built-in default into `ask` (a rule still decides); `force_ask` (a reason) turns
    any `allow` into `ask`."""
    effect, source, rule = await resolve(db, actor, action, ctx, extra_rules=extra_rules)
    if effect == "allow" and ask_by_default and source == "default":
        effect, source = "ask", ask_by_default
    if effect == "allow" and action in ("mail.send", "calendar.rsvp", "calendar.invite", "calendar.cancel") \
            and ctx.get("new_recipients"):
        effect, source = "ask", "hard rule: first message to a new address"
    if effect == "allow" and action in MAIL_OUT and await is_errand(db, actor):
        # Nobody watches an errand: whatever the rules allow the assistant, mail it would send waits
        # for the user (the errand prompt alone is not a control).
        effect, source = "ask", ERRAND_NO_SEND
    if effect == "allow" and action in ALWAYS_ASK:
        effect, source = "ask", ALWAYS_ASK[action]
    if effect == "allow" and force_ask:
        effect, source = "ask", force_ask
    return effect, source, rule


# A pending action's ctx records how it was gated (`_gate`), so a later grant can tell whether it
# would let the same request through (would_cover): a forced ask never is.
GATE_KEY = "_gate"


async def check(db: AsyncSession, actor: Actor, action: str, *, target: str = "",
                ctx: dict[str, Any] | None = None, payload: dict[str, Any] | None = None,
                preview: dict[str, Any] | None = None, force_ask: str | None = None,
                ask_by_default: str | None = None) -> Decision:
    """Gate an agent action. Session users are always allowed. On `ask` a PendingAction is
    created (in the caller's transaction) and the user is notified. `force_ask` (a reason)
    turns an `allow` into `ask`, like the first-message hard rule; `deny` still wins.
    `ask_by_default` (a reason) asks when no rule decides (a user's allow rule still lets it through)."""
    if action not in CLASSES:
        raise ValueError(f"unknown action class {action}")
    if actor.kind == "user":
        return Allowed("signed-in user")
    ctx = {k: v for k, v in (ctx or {}).items() if k != GATE_KEY}
    if (why := scope_denial(actor, action)) is not None:
        return Denied(why)
    effect, source, rule = await evaluate(db, actor, action, ctx, force_ask=force_ask, ask_by_default=ask_by_default)
    if effect == "deny":
        return Denied(f"{action} is not allowed for {actor.name} ({source})")
    if effect == "allow":
        return Allowed(source, rule.id if rule else None)
    gated = {"force": force_ask or None, "soft": ask_by_default or None}
    actor_name = actor.name
    if actor.kind == "assistant":  # the name the user gave the assistant (About you)
        from mailr.knowledge import profile

        actor_name = await profile.name_of(db, actor.user_id)
    if actor.errand:
        gated["errand"] = True
    ctx = {**ctx, GATE_KEY: gated}
    pa = PendingAction(
        user_id=actor.user_id, token_id=actor.api_token.id if actor.token else None,
        chat_session_id=actor.chat_session_id, subject=actor.subject, actor=actor_name, action=action,
        target=target[:2000], ctx=_jsonable(ctx), payload=_jsonable(stamp(payload, actor)),
        preview=_jsonable(preview or {"title": action}), status="pending",
        expires_at=datetime.now(UTC) + default_ttl(),
    )
    if source.startswith("hard rule") or (force_ask and source == force_ask) \
            or (ask_by_default and source == ask_by_default):
        pa.preview = {**pa.preview, "reason": source}
    # The card's display model and readable lines (agent/present.py).
    from mailr.agent import present

    pa.preview = _jsonable(await present.enrich_preview(db, actor.user_id, action, pa.payload, pa.preview))
    db.add(pa)
    await db.flush()
    await notify(db, actor.user_id, {"type": "approval", "id": publicid.encode("approval", pa.id), "status": "pending",
                                     "action": action, "actor": actor_name,
                                     "title": (pa.preview or {}).get("title", action),
                                     "chat_session_id": pa.chat_session_id})
    await enqueue(db, "push.approval", {"pending_id": pa.id}, user_id=actor.user_id)
    return Pending(pa)


def _jsonable(v: Any) -> Any:
    import json

    return json.loads(json.dumps(v, default=str))


def approval_url(approval_id: int) -> str:
    """The web link that opens one approval (the approvals drawer, on that card). Agents, MCP
    results and push notifications all hand out this one form: <base>/approvals/<id>, the id in
    its public short form (a + base62, core/publicid.py) when public ids are on."""
    return f"{get_settings().base_url.rstrip('/')}/approvals/{publicid.token('approval', approval_id)}"


async def approval_from_ref(db: AsyncSession, user_id: int, ref: str) -> PendingAction | None:
    """The user's approval named by an id from outside: the public number, the URL short form
    (a…), or a plain internal id. A plain internal id is read as public ids read old ids
    (MAILR_PUBLIC_IDS_LEGACY), with one allowance: with legacy ids refused, it still names an
    approval that is pending and unexpired. Links and push notifications sent before the switch
    (an installed app's Approve and Deny buttons) stay usable for the approval's lifetime
    (MAILR_APPROVAL_TTL_HOURS); nothing new hands out the internal form. None when not found."""
    ref = (ref or "").strip()
    if (publicid.enabled() and not publicid.legacy_allowed() and ref.isascii() and ref.isdigit()
            and len(ref) <= 15 and int(ref) < 1 << 52):
        pa = await db.get(PendingAction, int(ref))
        if (pa is None or pa.user_id != user_id or pa.status != "pending"
                or pa.expires_at <= datetime.now(UTC)):
            return None
        return pa
    try:
        aid = publicid.from_token("approval", ref)
    except publicid.InvalidId:
        return None
    if aid <= 0 or aid >= 1 << 62:
        return None
    pa = await db.get(PendingAction, aid)
    return pa if pa is not None and pa.user_id == user_id else None


def pending_response(pa: PendingAction) -> dict[str, Any]:
    """The HTTP 202 body of a route whose action waits for approval, its ids already in public form
    (the routes return it in a plain JSONResponse, which would drop ApprovalId's kind)."""
    return publicid.out({"status": "pending_approval", "approval": pending_out(pa), "approval_id": pa.id,
                         "approval_url": approval_url(pa.id)})


def raise_for(decision: Decision) -> None:
    """For routes: raise 403 on Denied. Pending is handled by the caller (HTTP 202)."""
    if isinstance(decision, Denied):
        raise HTTPException(403, detail=decision.reason)


def pending_out(pa: PendingAction) -> dict[str, Any]:
    def iso(d):
        return d.isoformat() if d else None
    return {"id": publicid.ApprovalId(pa.id), "subject": pa.subject, "actor": pa.actor, "action": pa.action, "target": pa.target,
            "preview": pa.preview, "status": pa.status, "result": pa.result, "decided_by": pa.decided_by,
            "created_at": iso(pa.created_at), "expires_at": iso(pa.expires_at), "decided_at": iso(pa.decided_at),
            "executed_at": iso(pa.executed_at), "chat_session_id": pa.chat_session_id}


# ---------------------------------------------------------------- deciding


def derived_constraints(pa: PendingAction) -> dict[str, Any]:
    ctx = pa.ctx or {}
    a = pa.action
    if a.startswith("calendar.") and ctx.get("calendar"):
        return {"calendars": [ctx["calendar"]]}
    if a.startswith("tasks.") and ctx.get("list"):
        return {"lists": [ctx["list"]]}
    if a == "mail.send" and ctx.get("recipients"):
        return {"domains": sorted({r.rsplit("@", 1)[-1].lower() for r in ctx["recipients"]})}
    if a == "mail.label" and ctx.get("labels"):
        return {"labels": sorted(ctx["labels"])}
    if a.startswith("notes.") and ctx.get("notes"):
        return {"notes": sorted(ctx["notes"])}
    if a.startswith("speakr.") and ctx.get("connectors"):
        return {"connectors": sorted(ctx["connectors"])}
    if a.startswith("mail.") and ctx.get("connectors"):
        return {"connectors": sorted(ctx["connectors"])}
    return {}


def grant_rule(pa: PendingAction, scope: str, decision: str = "approve", constraint: str = "none") -> PermissionRule:
    """The rule a "session" or "always" decision on `pa` creates (not stored): the requester's
    subject (a session grant for the assistant outside a chat covers the assistant), the action
    class, the derived constraints when asked for, and 12 hours for a session grant."""
    subject = pa.subject
    if scope == "session" and pa.token_id is None and not pa.chat_session_id:
        subject = "assistant"
    cons = derived_constraints(pa) if constraint == "derived" else {}
    return PermissionRule(
        id=None, user_id=pa.user_id, subject=subject, action=pa.action,
        effect="allow" if decision == "approve" else "deny", constraints=cons, source="grant",
        expires_at=datetime.now(UTC) + timedelta(hours=SESSION_GRANT_HOURS) if scope == "session" else None)


def takes_grant(pa: PendingAction) -> bool:
    """May "Allow for this chat" or "Always allow" be chosen on this card? Not when it asks whatever
    the rules say (a forced ask), nor, for the assistant, when a hard rule made it ask where the
    rules would allow (`ask_by_default` with a "hard rule" reason: a write after the turn read mail,
    hiding mail after reading it). Such a card is decided once; a rule the user adds on the
    Permissions page still applies to later requests. A token's text writes ask by default for
    every token, not after a particular read, so its cards keep their grants."""
    gated = (pa.ctx or {}).get(GATE_KEY)
    reason = str((pa.preview or {}).get("reason") or "")
    if gated is None:
        return not reason.startswith("hard rule")
    if gated.get("force"):
        return False
    soft = str(gated.get("soft") or "")
    return pa.token_id is not None or not (soft.startswith("hard rule") and reason == soft)


async def _requester_actor(db: AsyncSession, user: User, pa: PendingAction) -> Actor | None:
    """The actor that asked for `pa`, as the permission check saw it; None when it is gone."""
    if pa.token_id is not None:
        tok = await db.get(ApiToken, pa.token_id)
        if tok is None or tok.user_id != user.id or tok.revoked:
            return None
        return Actor(user=user, kind="token", token=tok)
    if pa.subject == "assistant" or pa.subject.startswith("session:"):
        gated = (pa.ctx or {}).get(GATE_KEY) or {}
        return Actor(user=user, kind="assistant", chat_session_id=pa.chat_session_id, errand=bool(gated.get("errand")))
    return None


async def _covers(db: AsyncSession, user: User, rule: PermissionRule, pa: PendingAction) -> bool:
    """True when `rule`, once stored, decides the pending `pa` the way it decides new requests:
    the same requester, the same class, the rule's constraints met (constraints_match, as the
    check uses them), and for an allow no hard rule left that asks (a forced ask, the first
    message to a new address, an errand's mail, a Screener decision, a share link)."""
    if not _action_matches(rule.action, pa.action, rule.effect):
        return False
    actor = await _requester_actor(db, user, pa)
    if actor is None or rule.subject not in [s for lvl in actor.subjects() for s in lvl]:
        return False
    ctx = {k: v for k, v in (pa.ctx or {}).items() if k != GATE_KEY}
    if not await constraints_match(db, actor, pa.action, rule.constraints or {}, ctx, effect=rule.effect,
                                   level=rule.subject):
        return False
    if rule.effect == "deny":
        return True
    if scope_denial(actor, pa.action) is not None:
        return False
    gated = (pa.ctx or {}).get(GATE_KEY)
    if gated is None:
        # Asked before mailr recorded how: any stated reason may be a hard rule, so it stays.
        if (pa.preview or {}).get("reason"):
            return False
        gated = {}
    if not takes_grant(pa):
        return False  # held by a hard rule: decided on its own card
    effect, _, _ = await evaluate(db, actor, pa.action, ctx, ask_by_default=gated.get("soft"), extra_rules=(rule,))
    if effect != "allow":
        return False
    # One card for an event write that mails the guests: the write class must pass as well.
    op = (pa.payload or {}).get("op")
    if isinstance(op, str) and op in CLASSES and op != pa.action:
        if scope_denial(actor, op) is not None:
            return False
        effect, _, _ = await evaluate(db, actor, op, ctx, extra_rules=(rule,))
        if effect != "allow":
            return False
    return True


COVER_LIMIT = 200


async def _covered(db: AsyncSession, user: User, rule: PermissionRule, pa: PendingAction) -> list[PendingAction]:
    """The user's other pending, unexpired actions that `rule` covers (_covers), oldest first."""
    conds = [PendingAction.user_id == user.id, PendingAction.status == "pending", PendingAction.id != pa.id,
             PendingAction.expires_at > datetime.now(UTC), PendingAction.action == pa.action]
    if rule.subject.startswith("token:"):
        conds.append(PendingAction.token_id == int(rule.subject.split(":", 1)[1]))
    elif rule.subject.startswith("session:"):
        conds.append(PendingAction.chat_session_id == rule.subject.split(":", 1)[1])
    else:
        conds.append(PendingAction.token_id.is_(None))
    rows = (await db.execute(select(PendingAction).where(*conds).order_by(PendingAction.id)
                             .limit(COVER_LIMIT))).scalars().all()
    return [r for r in rows if await _covers(db, user, rule, r)]


async def would_cover(db: AsyncSession, pa: PendingAction) -> dict[str, int]:
    """For a pending card: how many OTHER waiting requests each grant on it would decide too.
    `session` is "Allow for 12 hours" (or for this chat), `always` "Always allow" for every
    such action, `always_derived` "Always allow" narrowed to this request (derived_constraints)."""
    user = await get_existing(db, User, pa.user_id)
    out = {}
    for key, scope, cons in (("session", "session", "none"), ("always", "always", "none"),
                             ("always_derived", "always", "derived")):
        out[key] = len(await _covered(db, user, grant_rule(pa, scope, "approve", cons), pa))
    return out


# How an approval left pending, as `approval.decided` names it: decided (approved, denied),
# expired unanswered, or withdrawn (the user sent or cancelled the draft it asked to send).
OUTCOMES = ("approved", "denied", "expired", "withdrawn")
# Ids per push message: well inside UnifiedPush's 3,800-byte document (17 digits each).
DECIDED_PUSH_CHUNK = 100


async def announce_left(db: AsyncSession, user_id: int, ids: list[int], outcome: str) -> None:
    """Approvals that left pending, by any path (ACT approval.sync @api): one `approval.decided
    {ids, outcome}` event to the open web and app sessions, and one push job for the devices
    (core/push.py, push.approval_decided), so their notifications are taken down everywhere.
    Callers pass every approval one action decided together (a grant's covered requests, Allow
    all, an expiry sweep), so several ids travel in one event and one push. In the caller's
    transaction."""
    ids = list(dict.fromkeys(int(i) for i in ids))
    if not ids or outcome not in OUTCOMES:
        return
    await notify(db, user_id, {"type": "approval.decided", "outcome": outcome,
                               "ids": [publicid.encode("approval", i) for i in ids]})
    for at in range(0, len(ids), DECIDED_PUSH_CHUNK):
        await enqueue(db, "push.approval_decided", {"user_id": user_id, "ids": ids[at:at + DECIDED_PUSH_CHUNK],
                                                         "outcome": outcome},
                      user_id=user_id)


@dataclass
class DecideResult:
    pa: PendingAction
    rule_id: int | None = None
    # The other waiting requests this decision's rule decided as well (ids, oldest first).
    also: list[int] | None = None


async def decide_full(db: AsyncSession, user: User, pending_id: int, decision: str, scope: str = "once",
                      constraint: str = "none", *, announce: bool = True) -> DecideResult:
    """Decide one approval. A "session" or "always" decision also stores its rule (grant_rule),
    and decides every other waiting request that rule covers the same way, in the same
    transaction: each gets decided_by "rule:<id>", its own execute job (or on_denied hook) and
    its own event. All of them leave pending in one `approval.decided` (announce_left); a caller
    that decides several in a row (decide-many) passes announce=False and announces them once."""
    if decision not in ("approve", "deny") or scope not in ("once", "session", "always"):
        raise HTTPException(400, "decision must be approve|deny and scope once|session|always")
    if scope != "once":
        # A request a hard rule held (a review card, a write after the turn read mail, hiding mail
        # after reading it) takes no grant, whatever the client offers: it is decided once.
        cur = await db.get(PendingAction, pending_id)
        if cur is not None and cur.user_id == user.id and not takes_grant(cur):
            raise HTTPException(400, "this request always asks: decide it once")  # i18n: internal
    new_status = "approved" if decision == "approve" else "denied"
    row = (await db.execute(
        update(PendingAction)
        .where(PendingAction.id == pending_id, PendingAction.user_id == user.id,
               PendingAction.status == "pending", PendingAction.expires_at > datetime.now(UTC))
        .values(status=new_status, decided_by="user", decided_at=datetime.now(UTC))
        .returning(PendingAction.id)
    )).first()
    pa = await db.get(PendingAction, pending_id)
    if pa is None or pa.user_id != user.id:
        raise HTTPException(404, "approval not found")
    await db.refresh(pa)
    if row is None:
        raise HTTPException(409, f"this request is already {pa.status}")
    out = DecideResult(pa=pa)
    others: list[PendingAction] = []
    if scope != "once" and pa.subject:
        # A standing grant never covers the first message to a new address (hard rule), and a
        # session grant expires.
        rule = grant_rule(pa, scope, decision, constraint)
        others = await _covered(db, user, rule, pa)
        db.add(rule)
        await db.flush()
        out.rule_id = rule.id
        if others:
            done = (await db.execute(
                update(PendingAction)
                .where(PendingAction.id.in_([o.id for o in others]), PendingAction.status == "pending")
                .values(status=new_status, decided_by=f"rule:{rule.id}", decided_at=datetime.now(UTC))
                .returning(PendingAction.id))).scalars().all()
            others = [o for o in others if o.id in set(done)]
            for o in others:
                await db.refresh(o)
        out.also = [o.id for o in others]
    for item in [pa, *others]:
        if new_status == "approved":
            await enqueue(db, "approval.execute", {"pending_id": item.id}, user_id=user.id,
                          dedupe_key=f"approval.execute:{item.id}")
        else:
            await _on_denied(db, item)
        await notify(db, user.id, {"type": "approval", "id": publicid.encode("approval", item.id), "status": new_status,
                                   "action": item.action})
    if announce:
        await announce_left(db, user.id, [pa.id, *(o.id for o in others)], new_status)
    return out


async def decide(db: AsyncSession, user: User, pending_id: int, decision: str, scope: str = "once",
                 constraint: str = "none") -> PendingAction:
    return (await decide_full(db, user, pending_id, decision, scope, constraint)).pa


async def undo_grant(db: AsyncSession, user: User, rule_id: int) -> dict[str, Any]:
    """Undo a grant made on an approval card: delete its rule, and put back to waiting the
    requests it decided that have not started yet. Those already running or done stay so;
    a denial whose class has an on_denied hook (a rejected send) stays denied."""
    rule = await db.get(PermissionRule, rule_id)
    if rule is None or rule.user_id != user.id or rule.source != "grant":
        raise HTTPException(404, "grant not found")
    rows = (await db.execute(select(PendingAction).where(
        PendingAction.user_id == user.id, PendingAction.decided_by == f"rule:{rule_id}")
        .execution_options(populate_existing=True))).scalars().all()
    revertible = [r.id for r in rows if r.status == "approved"
                  or (r.status == "denied" and r.action not in _ON_DENIED)]
    back: list[int] = []
    if revertible:
        back = list((await db.execute(
            update(PendingAction)
            .where(PendingAction.id.in_(revertible), PendingAction.status.in_(("approved", "denied")))
            .values(status="pending", decided_by="", decided_at=None)
            .returning(PendingAction.id))).scalars().all())
    await db.delete(rule)
    await db.flush()
    for r in rows:
        if r.id in back:
            await db.refresh(r)
            await notify(db, user.id, {"type": "approval", "id": publicid.encode("approval", r.id), "status": "pending",
                                       "action": r.action})
    kept = [r.id for r in rows if r.id not in back]
    return {"reverted": back, "kept": kept}


# ---------------------------------------------------------------- execution

Executor = Callable[[AsyncSession, User, dict[str, Any]], Awaitable[dict[str, Any] | None]]
_EXECUTORS: dict[str, Executor] = {}
_ON_DENIED: dict[str, Executor] = {}


def executor(action: str):
    def deco(fn: Executor) -> Executor:
        _EXECUTORS[action] = fn
        return fn
    return deco


def executor_for(action: str, payload: dict[str, Any]) -> Executor | None:
    """The executor of an approved action: a variant registered as "<class>:<variant>" when the
    payload names one (`_variant`; a table to Trash under notes.delete is "notes.delete:table"),
    else the class's own."""
    variant = (payload or {}).get("_variant")
    if variant:
        return _EXECUTORS.get(f"{action}:{variant}")
    return _EXECUTORS.get(action)


def on_denied(action: str):
    def deco(fn: Executor) -> Executor:
        _ON_DENIED[action] = fn
        return fn
    return deco


async def _on_denied(db: AsyncSession, pa: PendingAction) -> None:
    fn = _ON_DENIED.get(pa.action)
    if fn:
        user = await get_existing(db, User, pa.user_id)
        await fn(db, user, pa.payload or {})


@handler("approval.execute")
async def execute_job(payload: dict[str, Any], ctx) -> None:
    from mailr.db import session_scope

    _load_executors()
    pid = payload["pending_id"]
    async with session_scope() as db:
        claimed = (await db.execute(
            update(PendingAction).where(PendingAction.id == pid, PendingAction.status == "approved")
            .values(status="executing").returning(PendingAction.id))).first()
    if not claimed:
        return  # already executed (or no longer approved): exactly once
    async with session_scope() as db:
        pa = await get_existing(db, PendingAction, pid)
        user = await get_existing(db, User, pa.user_id)
        fn = executor_for(pa.action, pa.payload or {})
        try:
            if fn is None:
                raise RuntimeError(f"no executor for {pa.action}")
            result = await fn(db, user, pa.payload or {})
            pa.status, pa.result, pa.executed_at = "executed", _jsonable(result or {}), datetime.now(UTC)
        except HTTPException as e:
            await db.rollback()
            pa = await get_existing(db, PendingAction, pid)
            pa.status, pa.result = "failed", {"error": str(e.detail)}
        except Exception as e:
            log.exception("approved action %s failed", pid)
            await db.rollback()
            pa = await get_existing(db, PendingAction, pid)
            pa.status, pa.result = "failed", {"error": f"{type(e).__name__}: {e}"[:1000]}
        await notify(db, pa.user_id, {"type": "approval", "id": publicid.encode("approval", pa.id), "status": pa.status, "action": pa.action})


async def expire_pending() -> int:
    from mailr.db import session_scope

    async with session_scope() as db:
        rows = (await db.execute(
            update(PendingAction).where(PendingAction.status == "pending",
                                        PendingAction.expires_at <= datetime.now(UTC))
            .values(status="expired").returning(PendingAction.id, PendingAction.user_id, PendingAction.action,
                                                PendingAction.payload))).all()
        by_user: dict[int, list[int]] = {}
        for pid, uid, action, pl in rows:
            fn = _ON_DENIED.get(action)
            if fn:
                await fn(db, await get_existing(db, User, uid), pl or {})
            await notify(db, uid, {"type": "approval", "id": publicid.encode("approval", pid), "status": "expired", "action": action})
            by_user.setdefault(uid, []).append(pid)
        for uid, pids in by_user.items():
            await announce_left(db, uid, pids, "expired")
        # Executions interrupted by a crash are reported, never re-run (exactly once).
        await db.execute(text(
            "UPDATE pending_actions SET status='failed', result='{\"error\": \"interrupted; check the result before retrying\"}'::jsonb "
            "WHERE status='executing' AND decided_at < now() - interval '1 hour'"))
        return len(rows)


def _load_executors() -> None:
    # Modules register their executors on import.
    import mailr.agent.config_tools
    import mailr.core.approval_executors
    import mailr.knowledge.ops
    try:
        import mailr.calendar.routes
    except ModuleNotFoundError:
        pass
    import mailr.assistant.forwards
    import mailr.calendar.forwarded
    import mailr.integrations.speakr.service
    import mailr.integrations.speakr.writes
    import mailr.notes.gate  # noqa: F401


# ---------------------------------------------------------------- effective view


async def effective(db: AsyncSession, user: User, subject: str) -> list[dict[str, Any]]:
    if subject.startswith("token:"):
        tok = await db.get(ApiToken, int(subject.split(":", 1)[1]))
        if tok is None or tok.user_id != user.id:
            raise HTTPException(404, "unknown token")
        actor = Actor(user=user, kind="token", token=tok)
    elif subject.startswith("session:"):
        actor = Actor.assistant(user, subject.split(":", 1)[1])
    else:
        actor = Actor.assistant(user, None)
    out = []
    for action, (_default, scope) in CLASSES.items():
        if action in ORDER_CLASSES:
            continue
        default = default_effect(action, actor.kind)
        if actor.kind == "token" and scope not in (actor.api_token.scopes or []):
            out.append({"action": action, "effect": "deny", "source": f"missing scope '{scope}'",
                        "default": default})
            continue
        # Unconstrained rules only: constrained rules apply per request.
        rules = await _rules(db, user.id, [s for lvl in actor.subjects() for s in lvl])
        eff, src = default, "default"
        denies = [r for r in rules if _action_matches(r.action, action, r.effect) and r.effect == "deny"
                  and not r.constraints]
        if denies:
            eff, src = "deny", f"rule:{denies[0].id}"
        else:
            for lvl in actor.subjects():
                at = [r for r in rules if r.subject in lvl and _action_matches(r.action, action, r.effect)]
                if at:
                    unc = [r for r in at if not r.constraints]
                    pick = next((r for r in unc if r.effect == "ask"), None) or next(
                        (r for r in unc if r.effect == "allow"), None)
                    if pick:
                        eff, src = pick.effect, f"rule:{pick.id}"
                    elif at:
                        src = "conditional rules"
                    break
        out.append({"action": action, "effect": eff, "source": src, "default": default})
    return out

