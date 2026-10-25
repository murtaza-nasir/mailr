"""Access policies: what each user may do with models, keyed by identity-provider groups.

mailr keeps no group membership of its own. The identity provider is the source of truth: the
OIDC `groups` claim is read at every sign-in (web and Android, core/users.user_for_oidc_claims)
and stored on the user (`users.idp_groups`, `idp_groups_at`, `idp_groups_source`). mailr stores
only *policies*, one per external group name (`access_policies`).

Other feeds. `record_idp_groups(db, user, groups, source=...)` is the one writer of those
columns. A SAML attribute statement or a SCIM `Group` push would call it with source "saml" or
"scim"; nothing else has to change.

Accounts without a provider (password-only) take the groups an admin assigns in Admin → Users
(`users.assigned_groups`). They count only while the provider has never reported groups for the
account, so a provider-managed account cannot be widened by hand.

Resolution (`for_user`):
- an admin (role admin, which MAILR_OIDC_ADMIN_GROUP sets) is never restricted;
- a user in no group that has a policy gets the built-in **Everyone** policy (group name
  EVERYONE, made by migration c3a9e6f1b2d4): the assistant with the local models the server
  has defaults for, and nothing else, so family members have Ambrose on day one with their own
  permissions (4 Oct 2026). The admin edits it like any policy and can
  narrow it to nothing; it cannot be deleted. Without that row (a test, or a deleted row) such a
  user gets no model features: the assistant is off with NO_ASSISTANT_MESSAGE;
- a user in several mapped groups gets the union: every model any of the groups allows, and each
  switch on if any group turns it on. The hosted-models guard is applied inside each policy
  first, so one group's hosted allowance never widens another group's list. The default model for
  a task is the first default, in group-name order, that the union allows.

Enforcement is in intel/llm.resolve (every model call passes through it) plus the feature checks
at each entry point (agent chat, standing orders, the lab, API tokens, PUT /me/models).

"local_only_tokens" governs the local-only flag of API tokens (api/tokens_routes, core/auth). The
flag is the user's own statement that an agent runs on their infrastructure; mailr cannot check
it. Without the feature a user cannot set it, and an existing flagged token counts as not
local-only at sign-in (it keeps working but cannot read local-only accounts). It is not revoked.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import outbound
from mailr.models import AccessPolicy, IdpGroupSeen, ModelEndpoint, User

log = logging.getLogger("mailr.access")

# The model tasks a policy governs. Embeddings (the search index) stay instance-wide and admin-only.
# Summaries, translation, drafting help, briefs and Speakr follow-ups are "chat"; triage, standing
# order questions, categories, actions and the lab are "classify" (intel/llm.TASKS).
POLICY_TASKS = ("chat", "classify")

FEATURES: dict[str, dict[str, Any]] = {
    "assistant": {"label": "Ambrose", "default": True,
                  "desc": "Chat with Ambrose, standing orders, routines and errands."},
    "choose_models": {"label": "Choose models", "default": False,
                      "desc": "Pick among the allowed models in Settings → Ambrose → Models. Off: shown read-only."},
    "lab": {"label": "Decisions lab", "default": False,
            "desc": "Try the allowed sorting models on their own mail and instructions."},
    "api_tokens": {"label": "Agents & API tokens", "default": False,
                   "desc": "Create and use API tokens (REST, MCP, agents)."},
    "local_only_tokens": {"label": "Mark agents as local-only", "default": False,
                          "desc": "Members may state that an agent runs on their own computers, so its token can read "
                                  "their local-only accounts. Mailr cannot check this statement."},
    "hosted_models": {"label": "Hosted models", "default": False,
                      "desc": "Allow endpoints marked hosted. Off: only local endpoints in the list count."},
    # The assistant's web search (agent/web_tools.py, websearch/). Admins are never restricted.
    "web_search": {"label": "Web search", "default": False,
                   "desc": "Ambrose may search the web and read pages from the results, through the providers "
                           "in Settings → Admin → Web search. Nothing from local-only accounts goes into a query."},
    # Not a model feature: read by calendar/builtin.py when Admin → Calendars gives calendars by group.
    "calendar": {"label": "Members get a Mailr calendar", "default": False,
                 "desc": "A calendar and a task list on this server. Counts when Settings → Admin → Calendars gives "
                         "Mailr calendars to members of chosen groups."},
}

NO_ASSISTANT_MESSAGE = "Your administrator hasn't given you access to Ambrose yet."


def no_assistant_message(name: str | None = None) -> str:
    """NO_ASSISTANT_MESSAGE with the user's own name for the assistant (About you)."""
    return NO_ASSISTANT_MESSAGE.replace("Ambrose", name) if name else NO_ASSISTANT_MESSAGE


def features_named(name: str | None = None) -> dict[str, dict[str, Any]]:
    """FEATURES with the reader's name for the assistant in the labels and descriptions (Admin →
    Access policies shows the admin's own name)."""
    if not name or name == "Ambrose":
        return FEATURES
    return {k: {**v, "label": v["label"].replace("Ambrose", name), "desc": v["desc"].replace("Ambrose", name)}
            for k, v in FEATURES.items()}


NO_FEATURE_MESSAGES = {
    "assistant": NO_ASSISTANT_MESSAGE,
    "lab": "Your administrator hasn't turned on the decisions lab for you.",
    "api_tokens": "Your administrator hasn't turned on API tokens for you.",
    "local_only_tokens": "Your administrator decides whether agents may read local-only accounts, "
                         "and hasn't allowed it for you.",
    "choose_models": "Your models are set by your administrator.",
    "web_search": "Your administrator hasn't turned on web search for you.",
}

SOURCES = ("oidc", "saml", "scim")

# The built-in policy for every user in no group that has a policy of its own. A group name
# no identity provider sends; the screens show it as EVERYONE_LABEL.
EVERYONE = "*"
EVERYONE_LABEL = "Everyone"
EVERYONE_DESCRIPTION = "Everyone who is not in a group with a policy of its own."


def normalise_groups(value: Any) -> list[str]:
    """A groups claim or attribute as a sorted list of distinct names: a list as its string items, a
    single string as one name, anything else as no groups (never a substring search)."""
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = [x for x in value if isinstance(x, str)]
    else:
        items = []
    return sorted({x.strip()[:200] for x in items if x.strip()})


async def record_idp_groups(db: AsyncSession, user: User, groups: Any, *, source: str = "oidc") -> list[str]:
    """Store what the provider reported for this user now (an empty list is "in no group"), and
    remember each name as seen. Callers skip this when the provider sent no groups claim at all,
    so a provider that is not set up to send groups leaves the account on its assigned groups."""
    if source not in SOURCES:
        raise ValueError(f"unknown group source {source!r}")
    names = normalise_groups(groups)
    now = datetime.now(UTC)
    before = effective_groups(user)
    user.idp_groups = names
    user.idp_groups_at = now
    user.idp_groups_source = source
    for n in names:
        await db.execute(insert(IdpGroupSeen).values(name=n, source=source, first_seen_at=now, last_seen_at=now)
                         .on_conflict_do_update(index_elements=[IdpGroupSeen.name],
                                                set_={"last_seen_at": now, "source": source}))
    from mailr.calendar import groups as calendar_groups

    await calendar_groups.groups_changed(db, before, effective_groups(user))
    return names


def group_source(user: User) -> str:
    """"idp" when the provider has reported groups for this account, else "assigned"."""
    return "idp" if user.idp_groups is not None and user.idp_groups_at is not None else "assigned"


def effective_groups(user: User) -> list[str]:
    if group_source(user) == "idp":
        return sorted(user.idp_groups or [])
    return sorted(user.assigned_groups or [])


# ---------------------------------------------------------------- resolution


@dataclass
class Access:
    unrestricted: bool
    groups: list[str] = field(default_factory=list)
    source: str = "assigned"
    policies: list[str] = field(default_factory=list)  # the group names that have a policy
    features: dict[str, bool] = field(default_factory=dict)
    # task -> endpoint id -> allowed model names (None: any model the endpoint lists)
    allowed: dict[str, dict[int, set[str] | None]] = field(default_factory=dict)
    defaults: dict[str, tuple[int, str | None] | None] = field(default_factory=dict)
    # task -> endpoint ids a policy names one by one (not only through "any endpoint"): only these
    # are used without a server default for the task (an embedding endpoint never chats by accident)
    named: dict[str, set[int]] = field(default_factory=dict)
    needs: dict[str, str] = field(default_factory=dict)

    def feature(self, name: str) -> bool:
        return self.unrestricted or bool(self.features.get(name))

    def allows(self, task: str, ep: ModelEndpoint, model: str | None) -> bool:
        if self.unrestricted:
            return True
        if task not in POLICY_TASKS:
            return True  # embed: instance-wide
        if ep.id not in self.allowed.get(task, {}):
            return False
        models = self.allowed[task][ep.id]
        return models is None or model in models


    def as_dict(self, eps: list[ModelEndpoint], name: str | None = None) -> dict[str, Any]:
        """The client view (GET /me "access", Admin → Users): names instead of ids. `name`: the
        reader's name for the assistant, for the message."""
        by_id = {e.id: e for e in eps}
        tasks: dict[str, Any] = {}
        for t in POLICY_TASKS:
            if self.unrestricted:
                tasks[t] = {"all": True, "allowed": [], "default": None}
                continue
            allowed = []
            for eid, models in sorted(self.allowed.get(t, {}).items()):
                e = by_id.get(eid)
                if e is not None:
                    allowed.append({"endpoint": e.name, "locality": e.locality,
                                    "models": sorted(models) if models is not None else None})
            d = self.defaults.get(t)
            de = by_id.get(d[0]) if d else None
            tasks[t] = {"all": False, "allowed": allowed,
                        "default": {"endpoint": de.name, "model": d[1]} if de else None}
        return {"unrestricted": self.unrestricted, "groups": self.groups, "source": self.source,
                "policies": self.policies,
                "features": {k: self.feature(k) for k in FEATURES},
                "tasks": tasks, "needs_endpoints": bool(self.needs),
                "message": None if self.feature("assistant") else no_assistant_message(name)}


UNRESTRICTED = Access(unrestricted=True)


def is_admin(user: User | None) -> bool:
    return user is not None and user.role == "admin"


def clean_entry(v: Any) -> tuple[int, str | None] | None:
    if not isinstance(v, dict):
        return None
    try:
        eid = int(v.get("endpoint_id"))
    except (TypeError, ValueError):
        return None
    m = v.get("model")
    return eid, (m if isinstance(m, str) and m else None)


def _policy_allowed(pol: AccessPolicy, task: str, eps: list[ModelEndpoint]) -> dict[int, set[str] | None]:
    """One policy's allowed endpoints for a task, after its own hosted-models guard. "Any endpoint"
    means every endpoint that can do the task and is set up for it (a server default for the task,
    or a Jev-protocol endpoint for sorting)."""
    from mailr.intel import llm

    hosted_ok = bool((pol.features or {}).get("hosted_models"))
    spec = (pol.models or {}).get(task) or {}
    out: dict[int, set[str] | None] = {}
    usable = {e.id: e for e in eps if e.enabled and llm.can(e, task) and (hosted_ok or outbound.local(e))}
    if spec.get("any"):
        for eid, e in usable.items():
            if (e.defaults or {}).get(task) or (e.defaults or {}).get(task + ".local") or e.adapter == "jev":
                out[eid] = None
        return out
    for raw in spec.get("allowed") or []:
        ent = clean_entry(raw)
        if ent is None or ent[0] not in usable:
            continue
        eid, model = ent
        if model is None:
            out[eid] = None
        elif eid not in out:
            out[eid] = {model}
        elif out[eid] is not None:
            out[eid].add(model)
    return out


async def _endpoints(db: AsyncSession) -> list[ModelEndpoint]:
    return list((await db.execute(select(ModelEndpoint).order_by(ModelEndpoint.id))).scalars())


async def for_user(db: AsyncSession, user: User | None, eps: list[ModelEndpoint] | None = None) -> Access:
    """The effective access of a user (None: a system job, unrestricted)."""
    if user is None or is_admin(user):
        return UNRESTRICTED
    groups = effective_groups(user)
    acc = Access(unrestricted=False, groups=groups, source=group_source(user),
                 features={k: False for k in FEATURES}, allowed={t: {} for t in POLICY_TASKS},
                 defaults={t: None for t in POLICY_TASKS}, named={t: set() for t in POLICY_TASKS})
    pols = list((await db.execute(select(AccessPolicy).where(AccessPolicy.group_name.in_(groups))
                                  .order_by(AccessPolicy.group_name))).scalars()) if groups else []
    if not pols:
        pols = list((await db.execute(select(AccessPolicy).where(AccessPolicy.group_name == EVERYONE))).scalars())
    if not pols:
        return acc
    if eps is None:
        eps = await _endpoints(db)
    acc.policies = [p.group_name for p in pols]
    for pol in pols:
        for k in FEATURES:
            acc.features[k] = acc.features[k] or bool((pol.features or {}).get(k))
        for t in POLICY_TASKS:
            if not ((pol.models or {}).get(t) or {}).get("any"):
                acc.named[t] |= set(_policy_allowed(pol, t, eps))
            for eid, models in _policy_allowed(pol, t, eps).items():
                if models is None or (eid in acc.allowed[t] and acc.allowed[t][eid] is None):
                    acc.allowed[t][eid] = None
                else:
                    acc.allowed[t][eid] = (acc.allowed[t].get(eid) or set()) | models
        for t, what in (pol.needs or {}).items():
            acc.needs.setdefault(t, str(what))
    by_id = {e.id: e for e in eps}
    for t in POLICY_TASKS:
        for pol in pols:
            d = clean_entry(((pol.models or {}).get(t) or {}).get("default"))
            if d is None or d[0] not in by_id:
                continue
            e = by_id[d[0]]
            model = d[1] or e.defaults.get(t) or (e.models[0] if e.models else None)
            if acc.allows(t, e, model):
                acc.defaults[t] = (e.id, model)
                break
    return acc


async def require_feature(db: AsyncSession, user: User, name: str) -> None:
    """403 with a plain message when the user's policy turns this feature off."""
    acc = await for_user(db, user)
    if not acc.feature(name):
        await audit_refusal(user.id, f"feature:{name}", "off in the access policy")
        raise HTTPException(403, NO_FEATURE_MESSAGES.get(name, "Your administrator hasn't turned this on for you."))


# ---------------------------------------------------------------- audit

_RECENT: dict[tuple[int, str, str], float] = {}
_QUIET_SECONDS = 600


async def audit_refusal(user_id: int, what: str, reason: str, *, detail: dict[str, Any] | None = None) -> None:
    """Write a policy refusal to the audit log, in its own transaction (the caller usually fails
    and rolls back). The same refusal for the same user is written at most once per ten minutes,
    so a background triage pass does not fill the log."""
    key = (user_id, what, reason)
    now = time.monotonic()
    if now - _RECENT.get(key, -1e9) < _QUIET_SECONDS:
        return
    _RECENT[key] = now
    if len(_RECENT) > 5000:
        _RECENT.clear()
    from mailr.db import session_scope
    from mailr.intel.llm import MODEL_BUDGET
    from mailr.models import AuditLog

    budget = MODEL_BUDGET.get()
    try:
        async with session_scope() as adb:
            adb.add(AuditLog(user_id=user_id, token_id=budget["token_id"] if budget else None,
                             actor="access-policy", action="access.refused", target=what[:200],
                             inputs={"reason": reason, **(detail or {})}, result={}, ok=False))
            await adb.commit()
    except Exception:  # an audit write never breaks the caller
        log.warning("could not audit an access refusal for user %s", user_id, exc_info=True)


def reset_audit_quiet() -> None:
    """Tests: forget which refusals were recently written."""
    _RECENT.clear()
