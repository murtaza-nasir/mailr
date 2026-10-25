"""Model router: every model call in mailr goes through here.

Endpoints are OpenAI-compatible (chat/completions, embeddings) and registered by an admin
with a locality: `local` (on infrastructure the operator controls) or `hosted`.

THE LOCAL-ONLY GUARD. Callers declare whether the content they send includes anything from
a `local_only` connector (`sensitive=True`). The router refuses to send such content to a
hosted endpoint, whatever the task mapping says. This check is deliberately here, in the
one function every call passes through, and has no override flag.

Task → endpoint resolution:
  user.settings["models"][task] = {"endpoint": name, "model": name}   (user choice)
  else the first enabled endpoint whose `defaults` has the task
  else None → callers degrade gracefully (mailr works with no models configured).
Tasks: "chat" (agent, drafting, summaries), "classify" (tagging/importance), "embed", "transcribe"
(dictation, agent/transcribe.py).

LOCAL MODELS ONLY (intel/locality.py). The household rule and the person's own switch make a
call sensitive here too: effective rule = admin OR user OR the content's own local_only flag
(`sensitive`). A call the rule keeps local, with no local endpoint for its task, raises
LocalModelsOnly ("not available with local models only"), a NoModel. Every resolved call is
counted for Settings → Ambrose → Model use (intel/usage.py), with the feature the caller names.

ACCESS POLICIES (core/access.py). For a user who is not an admin, "chat" and "classify" resolve
only to the (endpoint, model) pairs the policies of the user's identity-provider groups allow
(_resolve_restricted); no allowed pair is a PolicyRefused (a NoModel), audited. "embed" stays
instance-wide. API tokens resolve as their owner, so they inherit the owner's policy.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import outbound
from mailr.core.crypto import decrypt_instance, model_endpoint_context
from mailr.models import ModelEndpoint, User

log = logging.getLogger("mailr.llm")


class ModelError(RuntimeError):
    """An endpoint answered a call with an error status. Its text names only the status, as users
    may see it; the endpoint and its answer are in `detail` (the logs and the model health page)."""

    def __init__(self, endpoint: str, status: int, body: str):
        super().__init__(f"the model endpoint returned {status}")
        self.status = status
        self.detail = f"model endpoint {endpoint} returned {status}: {body[:300]}"


class NoModel(Exception):
    """No endpoint is configured (or allowed) for this task."""


class LocalOnlyViolation(Exception):
    """Refused: sensitive content would have gone to a hosted endpoint."""


class LocalModelsOnly(NoModel):
    """Refused by the local-models-only rule (the household's or the person's): the task has no
    local endpoint. The message is the plain one shown to the user."""


class PolicyRefused(NoModel):
    """Refused by the user's access policy (core/access.py): no allowed model for this task, or
    the endpoint asked for is not allowed. A NoModel, so every caller degrades as it does when no
    model is configured; the message is the plain one shown to the user."""


# The model tasks, one vocabulary for Admin → Model endpoints and Assistant → Models.
TASKS: dict[str, dict[str, str]] = {
    "chat": {"label": "Ambrose and writing",
             "desc": "Chat, summaries, briefs, drafting and standing orders."},
    "classify": {"label": "Sorting and decisions",
                 "desc": "Importance, categories and standing-order checks. Runs on every new email, so use a "
                         "fast model."},
    "embed": {"label": "Search index",
              "desc": "Embeddings for search and Ambrose's recall. One model for the whole server, for a "
                      "consistent index."},
    "transcribe": {"label": "Speech to text",
                   "desc": "Dictation: a spoken clip becomes text (an OpenAI-compatible /audio/transcriptions)."},
}


def task_words(task: str, lang: str | None = None, name: str | None = None) -> dict[str, str]:
    """A task's label and desc in `lang` (health.task.<task>.*; TASKS holds the English), with
    `name` for the assistant ("Ambrose and writing"): the reader's own name for it."""
    from mailr import i18n
    from mailr.knowledge.profile import DEFAULT_NAME

    name = name or DEFAULT_NAME
    en = TASKS[task]
    return {k: i18n.t(f"health.task.{task}.{k}", lang, name=name) if i18n.has(f"health.task.{task}.{k}")
            else en[k].replace(DEFAULT_NAME, name) for k in ("label", "desc")}


async def _name(db: AsyncSession | None, user: User | None) -> str:
    from mailr.knowledge import profile

    return await profile.name_of(db, user.id if user is not None else None)


# What each kind of endpoint can do: a Jev-protocol endpoint (a decision model) only decides.
ADAPTER_TASKS = {"openai": {"chat", "classify", "embed", "transcribe"}, "jev": {"classify"}}
# defaults[task] = the server default; defaults[task + LOCAL] = the model for local-only mail.
LOCAL = ".local"


def can(ep: ModelEndpoint, task: str) -> bool:
    return task in ADAPTER_TASKS.get(ep.adapter, set())


@dataclass
class Resolved:
    endpoint: ModelEndpoint
    model: str
    api_key: str | None

    @property
    def local(self) -> bool:
        return outbound.local(self.endpoint)


# The decisions lab (intel/lab.py) pins one endpoint per run; everything else resolves normally.
FORCE_ENDPOINT: ContextVar[str | None] = ContextVar("mailr_force_endpoint", default=None)


# Set for each request an API token makes (core/auth.optional_principal). The first model call the
# request causes (a chat, a summary, a query embedding, a decision) counts once against the token's
# model budget (core/throttle.py). The signed-in user, the user's own
# assistant and background jobs carry no budget.
MODEL_BUDGET: ContextVar[dict[str, Any] | None] = ContextVar("mailr_model_budget", default=None)


async def charge_model_budget() -> None:
    b = MODEL_BUDGET.get()
    if b is None or b.get("charged"):
        return
    b["charged"] = True
    from mailr.core import throttle

    await throttle.token_model_call(b["token_id"])


def endpoint_key(ep: ModelEndpoint) -> str | None:
    """The endpoint's stored API key (bound to that endpoint row), or None."""
    return decrypt_instance(ep.api_key_enc, context=model_endpoint_context(ep.id)) if ep.api_key_enc else None


async def resolve(db: AsyncSession, user: User | None, task: str, *, sensitive: bool,
                  probe: bool = False, feature: str | None = None, count: bool = True) -> Resolved:
    """`probe`: only asking whether a model is available (a policy refusal is not audited, and
    nothing is counted). `feature`: what the call is for, for Model use (intel/usage.FEATURES).
    `count` False: the caller counts the call itself (`counted`), once it knows it makes one."""
    from mailr.intel import locality

    # The effective class (core/outbound.py): the caller's declaration, the request's or job's
    # taint scope (it read local-only rows), or the local-models-only rule.
    sensitive = sensitive or outbound.tainted()
    by_rule = not sensitive and await locality.strict(db, user)
    try:
        r = await _resolve(db, user, task, sensitive=sensitive or by_rule, probe=probe)
    except LocalOnlyViolation:
        if by_rule:
            what = task_words(task, name=await _name(db, user))["label"].lower()
            raise LocalModelsOnly(locality.UNAVAILABLE.format(what=what)) from None
        raise
    if not probe and count:
        await counted(user, feature or task, r)
    return r


async def counted(user: User | None, feature: str, r: Resolved) -> None:
    """Count one model call for Model use (intel/usage.py)."""
    from mailr.intel import usage

    await usage.record(user.id if user is not None else None, feature, r.endpoint.name, r.model, r.local)


def enabled_endpoints():
    """The enabled endpoints, in order. One the environment set up (MAILR_ASR_URL) counts only while
    the environment still sets it."""
    from mailr.config import get_settings

    q = select(ModelEndpoint).where(ModelEndpoint.enabled.is_(True)).order_by(ModelEndpoint.id)
    if not (get_settings().asr_url or "").strip():
        q = q.where(ModelEndpoint.from_env.is_(False))
    return q


async def _resolve(db: AsyncSession, user: User | None, task: str, *, sensitive: bool, probe: bool) -> Resolved:
    await charge_model_budget()
    eps = list((await db.execute(enabled_endpoints())).scalars())
    from mailr.core import access

    acc = await access.for_user(db, user, eps)
    if not acc.unrestricted and task in access.POLICY_TASKS:
        return await _resolve_restricted(db, user, acc, eps, task, sensitive=sensitive, probe=probe)
    forced = FORCE_ENDPOINT.get()
    if forced is not None:
        ep = next((e for e in eps if e.name == forced and can(e, task)), None)
        if ep is None:
            raise NoModel(task)
        if not outbound.allowed(sensitive, ep):
            raise LocalOnlyViolation(f"local-only mail never goes to the hosted endpoint {ep.name!r}")
        return Resolved(ep, ep.defaults.get(task) or (ep.models[0] if ep.models else ""),
                        endpoint_key(ep))
    choice = ((user.settings or {}).get("models") or {}).get(task) if user else None
    chosen: tuple[ModelEndpoint, str] | None = None
    if choice:
        ep = next((e for e in eps if e.name == choice.get("endpoint")), None)
        if ep is not None and can(ep, task):
            chosen = (ep, choice.get("model") or ep.defaults.get(task) or (ep.models[0] if ep.models else ""))
    if chosen is None and sensitive:
        # Local-only mail: the model the admin chose for it (Admin → Models per task).
        ep = next((e for e in eps if outbound.local(e) and e.defaults.get(task + LOCAL)), None)
        if ep is not None:
            chosen = (ep, ep.defaults[task + LOCAL])
    if chosen is None:
        # The server default; for sensitive content a local endpoint first.
        ordered = sorted(eps, key=lambda e: (not outbound.local(e)) if sensitive else 0)
        for ep in ordered:
            if ep.defaults.get(task):
                chosen = (ep, ep.defaults[task])
                break
    if chosen is None:
        raise NoModel(task)
    ep, model = chosen
    if not outbound.allowed(sensitive, ep):
        # The model an admin chose for local-only mail, else any local default for this task.
        local = next((e for e in eps if outbound.local(e) and e.defaults.get(task + LOCAL)), None)
        if local is not None:
            return Resolved(local, local.defaults[task + LOCAL],
                            endpoint_key(local))
        local = next((e for e in eps if outbound.local(e) and e.defaults.get(task)), None)
        if local is None:
            raise LocalOnlyViolation(
                f"task {task!r}: content from a local-only account cannot be sent to hosted endpoint "
                f"{ep.name!r}, and no local endpoint is configured for this task")
        ep, model = local, local.defaults[task]
    assert outbound.allowed(sensitive, ep)  # the guard, restated
    return Resolved(ep, model, endpoint_key(ep))


def _model_for(ep: ModelEndpoint, task: str, wanted: str | None = None) -> str:
    return wanted or ep.defaults.get(task) or (ep.models[0] if ep.models else "")


async def _refuse(user: User | None, task: str, reason: str, *, probe: bool, detail: dict[str, Any] | None = None,
                  message: str | None = None, name: str | None = None) -> PolicyRefused:
    from mailr.core import access

    if user is not None and not probe:
        await access.audit_refusal(user.id, f"model:{task}", reason, detail=detail)
    what = task_words(task, name=name)["label"].lower()
    return PolicyRefused(message or f"Your administrator hasn't allowed a model for {what}.")


async def _resolve_restricted(db: AsyncSession, user: User | None, acc: Any, eps: list[ModelEndpoint], task: str,
                              *, sensitive: bool, probe: bool) -> Resolved:
    """resolve() for a user under an access policy. Only the policy's allowed (endpoint, model)
    pairs are candidates, in this order: the forced lab endpoint (refused if not allowed); the
    user's own choice (only when the policy lets them choose); the policy's default; the server
    default; the first allowed pair. Local-only content then needs a local pair from the same
    allowed set. A request for a disallowed model falls back silently to that order; nothing
    allowed at all is a PolicyRefused, written to the audit log."""
    def ok(ep: ModelEndpoint, model: str) -> bool:
        return can(ep, task) and acc.allows(task, ep, model)

    usable = [e for e in eps if can(e, task) and e.id in acc.allowed.get(task, {})]
    if not usable:
        why = "no model allowed" if acc.policies else "no access policy for the user's groups"
        name = await _name(db, user)
        raise await _refuse(user, task, why, probe=probe, detail={"groups": acc.groups}, name=name,
                            message=None if acc.policies else _no_access_message(name))
    forced = FORCE_ENDPOINT.get()
    if forced is not None:
        ep = next((e for e in usable if e.name == forced), None)
        model = _model_for(ep, task) if ep else ""
        if ep is not None and not ok(ep, model):
            models = acc.allowed[task].get(ep.id) or set()
            model = sorted(models)[0] if models else model
        if ep is None or not ok(ep, model):
            raise await _refuse(user, task, "endpoint not allowed", probe=probe, detail={"endpoint": forced},
                                message="Your administrator hasn't allowed this model for you.")
        if not outbound.allowed(sensitive, ep):
            raise LocalOnlyViolation(f"local-only mail never goes to the hosted endpoint {ep.name!r}")
        return Resolved(ep, model, endpoint_key(ep))

    cands: list[tuple[ModelEndpoint, str]] = []
    choice = ((user.settings or {}).get("models") or {}).get(task) if user is not None else None
    if choice and acc.feature("choose_models"):
        ep = next((e for e in usable if e.name == choice.get("endpoint")), None)
        if ep is not None:
            cands.append((ep, _model_for(ep, task, choice.get("model"))))
    d = acc.defaults.get(task)
    if d is not None:
        ep = next((e for e in usable if e.id == d[0]), None)
        if ep is not None:
            cands.append((ep, _model_for(ep, task, d[1])))
    if sensitive:
        for ep in usable:
            if outbound.local(ep) and ep.defaults.get(task + LOCAL):
                cands.append((ep, ep.defaults[task + LOCAL]))
    for ep in usable:
        if ep.defaults.get(task):
            cands.append((ep, ep.defaults[task]))
    for ep in usable:
        if ep.id in acc.named.get(task, set()):
            models = acc.allowed[task][ep.id]
            cands.append((ep, sorted(models)[0] if models else _model_for(ep, task)))
    cands = [(e, m) for e, m in cands if ok(e, m)]
    if sensitive:
        local = [(e, m) for e, m in cands if outbound.local(e)]
        if not local:
            raise LocalOnlyViolation(
                f"task {task!r}: content from a local-only account cannot be sent to a hosted endpoint, and "
                "the access policy allows no local endpoint for this task")
        cands = local
    if not cands:
        raise await _refuse(user, task, "no allowed model is served", probe=probe, name=await _name(db, user))
    ep, model = cands[0]
    assert outbound.allowed(sensitive, ep)  # the guard, restated
    return Resolved(ep, model, endpoint_key(ep))


def _no_access_message(name: str | None = None) -> str:
    from mailr.core.access import no_assistant_message

    return no_assistant_message(name)


def _headers(r: Resolved) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if r.api_key:
        h["Authorization"] = f"Bearer {r.api_key}"
    return h


def _url(r: Resolved, path: str) -> str:
    return r.endpoint.base_url.rstrip("/") + path


def _http(r: Resolved, task: str, *, local_only: bool, **kw: Any) -> httpx.AsyncClient:
    """The HTTP client for one call to r's endpoint (core/outbound.client): pinned to checked
    addresses, a local endpoint's addresses checked again on every connection, and the taint scope
    checked again when the request is sent. Tests replace this function."""
    return outbound.client(outbound.endpoint_service(r.endpoint, task), local_only=local_only, **kw)


async def chat(
    db: AsyncSession,
    user: User | None,
    messages: list[dict[str, Any]],
    *,
    task: str = "chat",
    sensitive: bool,
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    timeout: float = 180,
    extra: dict[str, Any] | None = None,
    feature: str | None = None,
    count: bool = True,
) -> dict[str, Any]:
    """Non-streaming chat completion. Returns the first choice's message dict
    ({"role","content","tool_calls"?}) plus "_model", "_endpoint" and "_local"."""
    r = await resolve(db, user, task, sensitive=sensitive, feature=feature, count=count)
    body: dict[str, Any] = {"model": r.model, "messages": messages, "temperature": temperature,
                            "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if response_format:
        body["response_format"] = response_format
    if extra:
        body.update(extra)
    from mailr.intel import modelhealth, slots

    try:
        async with slots.slot(r.endpoint.id, user.id if user is not None else None), \
                _http(r, task, local_only=sensitive, timeout=timeout) as c:
            resp = await c.post(_url(r, "/chat/completions"), headers=_headers(r), json=body)
    except outbound.Refused as e:
        raise LocalOnlyViolation(str(e)) from None
    except httpx.HTTPError as e:
        await modelhealth.record(task, r.endpoint.name, r.model, False, modelhealth.plain(e))
        raise
    if resp.status_code >= 400:
        await modelhealth.record(task, r.endpoint.name, r.model, False,
                                 modelhealth.plain(None, resp.status_code, resp.text[:300]))
        log.warning("model call failed: model endpoint %s returned %s: %s", r.endpoint.name, resp.status_code,
                    resp.text[:300])
        raise ModelError(r.endpoint.name, resp.status_code, resp.text)
    await modelhealth.record(task, r.endpoint.name, r.model, True)
    data = resp.json()
    msg = data["choices"][0]["message"]
    msg["_model"] = r.model
    msg["_endpoint"] = r.endpoint.name
    msg["_local"] = r.local
    msg["_finish"] = data["choices"][0].get("finish_reason")
    return msg


class ThinkSplitter:
    """Reasoning that arrives inside the content stream, kept apart from the answer.

    Servers that run a thinking model without a reasoning parser (llama.cpp with
    --reasoning-format none, some vLLM setups) send it in `content` between <think> and </think>.
    When the chat template opens the block itself (Qwen3's generation prompt ends in "<think>"),
    only the closing tag arrives: everything before it was reasoning, already passed on as
    content. feed() returns events: ("content", text), ("reasoning", text) and, for that
    closing tag, ("retract", text): the content so far in this stream was reasoning. Tags split
    across pieces are held back until they are decided. Reasoning in its own field
    (`reasoning_content`, `reasoning`) never needs this."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.buf = ""
        self.inside = False
        self.strip_lead = False  # the blank lines after </think> are not part of the answer

    @staticmethod
    def _partial(buf: str, *tags: str) -> int:
        """How many trailing characters of buf may start one of the tags."""
        for k in range(min(len(buf), max(len(t) for t in tags) - 1), 0, -1):
            if any(t.startswith(buf[-k:]) for t in tags):
                return k
        return 0

    def _content(self, text: str, out: list[tuple[str, str]]) -> None:
        if self.strip_lead:
            text = text.lstrip()
            if text:
                self.strip_lead = False
        if text:
            out.append(("content", text))

    def feed(self, piece: str) -> list[tuple[str, str]]:
        self.buf += piece
        out: list[tuple[str, str]] = []
        while self.buf:
            if self.inside:
                i = self.buf.find(self.CLOSE)
                if i < 0:
                    k = self._partial(self.buf, self.CLOSE)
                    if len(self.buf) > k:
                        out.append(("reasoning", self.buf[:len(self.buf) - k]))
                    self.buf = self.buf[len(self.buf) - k:]
                    break
                if i:
                    out.append(("reasoning", self.buf[:i]))
                self.buf, self.inside, self.strip_lead = self.buf[i + len(self.CLOSE):], False, True
                continue
            o, c = self.buf.find(self.OPEN), self.buf.find(self.CLOSE)
            if c >= 0 and (o < 0 or c < o):
                out.append(("retract", self.buf[:c]))
                self.buf, self.strip_lead = self.buf[c + len(self.CLOSE):], True
                continue
            if o >= 0:
                self._content(self.buf[:o], out)
                self.buf, self.inside = self.buf[o + len(self.OPEN):], True
                continue
            k = self._partial(self.buf, self.OPEN, self.CLOSE)
            self._content(self.buf[:len(self.buf) - k], out)
            self.buf = self.buf[len(self.buf) - k:]
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        rest, self.buf = self.buf, ""
        if not rest:
            return []
        if self.inside:
            return [("reasoning", rest)]
        out: list[tuple[str, str]] = []
        self._content(rest, out)
        return out


def strip_think(text: str) -> str:
    """A whole answer without the reasoning a thinking model left in it (see ThinkSplitter)."""
    sp = ThinkSplitter()
    parts: list[str] = []
    for kind, t in [*sp.feed(text or ""), *sp.flush()]:
        if kind == "content":
            parts.append(t)
        elif kind == "retract":
            parts.clear()
    return "".join(parts)


async def chat_stream(
    db: AsyncSession,
    user: User | None,
    messages: list[dict[str, Any]],
    *,
    task: str = "chat",
    sensitive: bool,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    extra: dict[str, Any] | None = None,
    feature: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream deltas: yields {"content": str} pieces, {"reasoning": str} (the model's thinking,
    from its own field or from <think> tags in the content: never part of the answer),
    {"retract": True} when a closing </think> shows that the content so far was reasoning
    (ThinkSplitter), and, at the end, {"tool_calls": [...], "finish": reason, "_model",
    "_endpoint", "_local"}."""
    r = await resolve(db, user, task, sensitive=sensitive, feature=feature)
    body: dict[str, Any] = {"model": r.model, "messages": messages, "temperature": temperature,
                            "max_tokens": max_tokens, "stream": True}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if extra:
        body.update(extra)
    calls: dict[int, dict[str, Any]] = {}
    finish = None
    think = ThinkSplitter()

    def split(events: list[tuple[str, str]]) -> list[dict[str, Any]]:
        return [{"content": t} if kind == "content" else {"reasoning": t} if kind == "reasoning"
                else {"retract": True, "reasoning": t} for kind, t in events]

    from mailr.intel import modelhealth, slots

    try:
        async with slots.slot(r.endpoint.id, user.id if user is not None else None), \
                _http(r, task, local_only=sensitive, timeout=httpx.Timeout(300, connect=15)) as c, \
                c.stream("POST", _url(r, "/chat/completions"), headers=_headers(r), json=body) as resp:
            if resp.status_code >= 400:
                text = (await resp.aread()).decode(errors="replace")
                await modelhealth.record(task, r.endpoint.name, r.model, False,
                                         modelhealth.plain(None, resp.status_code, text[:300]))
                log.warning("model call failed: model endpoint %s returned %s: %s", r.endpoint.name, resp.status_code,
                            text[:300])
                raise ModelError(r.endpoint.name, resp.status_code, text)
            await modelhealth.record(task, r.endpoint.name, r.model, True)
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    data = json.loads(chunk)
                except ValueError:
                    continue
                if not data.get("choices"):
                    continue
                ch = data["choices"][0]
                delta = ch.get("delta") or {}
                if delta.get("content"):
                    for ev in split(think.feed(delta["content"])):
                        yield ev
                if delta.get("reasoning_content") or delta.get("reasoning"):
                    yield {"reasoning": delta.get("reasoning_content") or delta.get("reasoning")}
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {"id": "", "type": "function",
                                                                 "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    slot["function"]["name"] += fn.get("name") or ""
                    slot["function"]["arguments"] += fn.get("arguments") or ""
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
    except outbound.Refused as e:
        raise LocalOnlyViolation(str(e)) from None
    except httpx.HTTPError as e:
        await modelhealth.record(task, r.endpoint.name, r.model, False, modelhealth.plain(e))
        raise
    for ev in split(think.flush()):
        yield ev
    yield {"tool_calls": [calls[i] for i in sorted(calls)], "finish": finish, "_model": r.model,
           "_endpoint": r.endpoint.name, "_local": r.local}


async def resolve_embed(db: AsyncSession, *, sensitive: bool, user_id: int | None = None,
                        count: bool = False) -> Resolved | None:
    """The instance's ONE embedding endpoint (vectors from different models cannot be compared,
    so every passage and query uses the same one): a local endpoint with an "embed" default if
    there is one, otherwise the first hosted one. Returns None when no endpoint is configured,
    or when the content is sensitive and the canonical endpoint is hosted (the local-only guard:
    such content is then simply not embedded and stays keyword-searchable).

    The local-models-only rule (intel/locality.py) counts as sensitive: the household's rule
    always, the person's own switch when `user_id` names whose content it is. `count`: a call
    that embeds now (Model use), not a check."""
    from mailr.intel import locality

    sensitive = sensitive or outbound.tainted()  # the taint scope (core/outbound.py)
    if not sensitive:
        user = await db.get(User, user_id) if user_id is not None else None
        sensitive = await locality.strict(db, user)
    r = await _resolve_embed(db, sensitive=sensitive)
    if r is not None and count and user_id is not None:
        from mailr.intel import usage

        await usage.record(user_id, "search", r.endpoint.name, r.model, r.local)
    return r


async def _resolve_embed(db: AsyncSession, *, sensitive: bool) -> Resolved | None:
    await charge_model_budget()
    eps = [e for e in (await db.execute(enabled_endpoints())).scalars() if e.defaults.get("embed")]
    if not eps:
        return None
    ep = next((e for e in eps if outbound.local(e)), eps[0])
    if not outbound.allowed(sensitive, ep):
        return None
    assert outbound.allowed(sensitive, ep)  # the guard, restated
    return Resolved(ep, ep.defaults["embed"], endpoint_key(ep))


def embed_model_key(r: Resolved, dim: int) -> str:
    """Vectors are comparable when the model and size match, whichever endpoint served them
    (e.g. the same model self-hosted or via a provider), so the key omits the endpoint."""
    return f"{r.model}:{dim}"


class EmbedUnavailable(Exception):
    """The embedding endpoint failed (network, 5xx, rate limit after retries)."""


class EmbedRefused(EmbedUnavailable):
    """Not sent: local-only text and a hosted embedding endpoint (core/outbound.py). The endpoint
    itself is fine, so this trips no circuit breaker."""


async def embed_request(r: Resolved, texts: list[str], dim: int, *, timeout: float = 60,
                        record: bool = True, user_id: int | None = None,
                        local_only: bool = False) -> list[list[float]]:
    """One /embeddings call. Requests `dimensions`; a longer vector (an endpoint that ignores the
    parameter) is truncated and re-normalised, which is valid for Matryoshka models only; a
    shorter one is an error. Retries 429/5xx with backoff. With record=False the outcome is not
    noted in model health (the Search index check of a model that is not in use)."""
    import asyncio
    import math

    from mailr.intel import modelhealth

    async def note(ok: bool, error: str | None = None) -> None:
        if record:
            await modelhealth.record("embed", r.endpoint.name, r.model, ok, error)

    body = {"model": r.model, "input": texts, "dimensions": dim, "encoding_format": "float"}
    delay = 1.0
    last: Exception | None = None
    last_plain: str | None = None
    from mailr.intel import slots

    for _attempt in range(4):
        try:
            async with slots.slot(r.endpoint.id, user_id), _http(r, "embed", local_only=local_only,
                                                                 timeout=timeout) as c:
                resp = await c.post(_url(r, "/embeddings"), headers=_headers(r), json=body)
            if resp.status_code == 400 and "dimensions" in resp.text and "dimensions" in body:
                body.pop("dimensions")  # endpoint does not accept the parameter; truncate instead
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last = EmbedUnavailable(f"{r.endpoint.name}: HTTP {resp.status_code}")
                ra = resp.headers.get("retry-after")
                await asyncio.sleep(min(30.0, float(ra)) if ra and ra.replace(".", "").isdigit() else delay)
                delay *= 2
                continue
            if resp.status_code >= 400:
                raise EmbedUnavailable(f"{r.endpoint.name}: HTTP {resp.status_code}: {resp.text[:200]}")
            out = []
            for d in sorted(resp.json()["data"], key=lambda d: d["index"]):
                v = d["embedding"]
                if len(v) < dim:
                    raise EmbedUnavailable(f"{r.model} returns {len(v)}-d vectors; Mailr needs {dim}")
                if len(v) > dim:
                    v = v[:dim]
                    n = math.sqrt(sum(x * x for x in v)) or 1.0
                    v = [x / n for x in v]
                out.append(v)
            if len(out) != len(texts):
                raise EmbedUnavailable(f"{r.endpoint.name}: expected {len(texts)} vectors, got {len(out)}")
            await note(True)
            return out
        except outbound.Refused as e:
            # Local-only text and a hosted embedder (the taint scope): not sent, not retried.
            raise EmbedRefused(f"{r.endpoint.name}: {e}") from None
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last = EmbedUnavailable(f"{r.endpoint.name}: {type(e).__name__}: {e}")
            last_plain = modelhealth.plain(e)
            await asyncio.sleep(delay)
            delay *= 2
        except EmbedRefused:
            raise
        except EmbedUnavailable as e:
            await note(False, modelhealth.plain(e))
            raise
    await note(False, last_plain or modelhealth.plain(last))
    raise last or EmbedUnavailable(r.endpoint.name)


async def available(db: AsyncSession, user: User | None, task: str, *, sensitive: bool) -> bool:
    try:
        await resolve(db, user, task, sensitive=sensitive, probe=True)
        return True
    except (NoModel, LocalOnlyViolation):
        return False
