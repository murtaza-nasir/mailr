"""What a configured model can do beyond text: for now, whether it can view images.

Stored per endpoint and model name in ModelEndpoint.capabilities:
{"<model>": {"vision": true | false | null, "vision_manual": true | false}}.

- `vision` is what detection found when the endpoint was added, saved or refreshed (Admin → Models
  → Detect). null: the server did not say.
- `vision_manual` is an admin's override in Admin → Models. When present it wins.
- `checked`: the server answered a detection. Endpoints with unchecked models (all of them before
  this column existed) are asked automatically: the job JOB, queued at every worker start and
  daily (core/tasks.py); a server that does not answer is asked again at the next run.

Detection asks the server, with GETs and one POST that carry no user data:

1. OpenAI-compatible GET {base}/models: a model entry's `architecture.input_modalities`
   (OpenRouter, recent llama.cpp) or `capabilities` (a list such as ["completion", "vision"] or
   ["multimodal"], or a dict {"vision": true}).
2. llama.cpp GET {root}/props (`?model=` for a router): `modalities.vision`.
3. Ollama POST {root}/api/show {"model"}: `capabilities` holding "vision".

{root} is the base URL without a trailing /v1. No table of hosted model names exists in the code:
a hosted provider that says nothing is set by hand.

can_view_images() is the one question the assistant asks (agent/vision.py); llm.chat and
llm.chat_stream also strip image parts from a request to a model that cannot view them.
"""

from __future__ import annotations

import logging
from typing import Any

from mailr.core import outbound
from mailr.core.jobs import handler
from mailr.models import ModelEndpoint

log = logging.getLogger("mailr.modelcaps")

DETECT_TIMEOUT = 8.0
IMAGE_WORDS = {"image", "images", "vision", "multimodal", "image_input"}


def entry(ep: ModelEndpoint, model: str) -> dict[str, Any]:
    return dict((ep.capabilities or {}).get(model) or {})


def can_view_images(ep: ModelEndpoint, model: str) -> bool:
    """The admin's override when set, else what detection found (unknown counts as no)."""
    e = entry(ep, model)
    if isinstance(e.get("vision_manual"), bool):
        return e["vision_manual"]
    return e.get("vision") is True


def describe(ep: ModelEndpoint) -> dict[str, dict[str, Any]]:
    """For Admin → Models: per listed model {"vision": bool, "source": "manual" | "detected" | "unknown",
    "detected": bool | None}."""
    out: dict[str, dict[str, Any]] = {}
    for m in list(ep.models or []) or [v for k, v in (ep.defaults or {}).items() if isinstance(v, str)]:
        e = entry(ep, m)
        manual = e.get("vision_manual")
        det = e.get("vision")
        src = "manual" if isinstance(manual, bool) else ("detected" if isinstance(det, bool) else "unknown")
        out[m] = {"vision": can_view_images(ep, m), "source": src, "detected": det if isinstance(det, bool) else None}
    return out


def set_manual(ep: ModelEndpoint, model: str, vision: bool | None) -> None:
    """The admin's override; None goes back to what detection found."""
    caps = {k: dict(v) for k, v in (ep.capabilities or {}).items()}
    e = caps.setdefault(model, {})
    if vision is None:
        e.pop("vision_manual", None)
    else:
        e["vision_manual"] = bool(vision)
    ep.capabilities = caps


def apply_detected(ep: ModelEndpoint, found: dict[str, bool | None], *, checked: bool = True) -> None:
    """Store detection results, keeping each model's override. `checked`: the server answered, so
    the automatic check (check_unknown) does not ask about these models again."""
    caps = {k: dict(v) for k, v in (ep.capabilities or {}).items()}
    for m, v in found.items():
        e = caps.setdefault(m, {})
        e["vision"] = v
        if checked:
            e["checked"] = True
    ep.capabilities = caps


# ---------------------------------------------------------------- reading the server's answers


def _words(v: Any) -> set[str]:
    if isinstance(v, str):
        return {v.lower()}
    if isinstance(v, (list, tuple)):
        return {str(x).lower() for x in v if isinstance(x, (str, int))}
    return set()


def from_models_entry(item: dict[str, Any]) -> bool | None:
    """An entry of GET /models: True or False when it says, else None."""
    if not isinstance(item, dict):
        return None
    said: list[set[str]] = []
    arch = item.get("architecture")
    if isinstance(arch, dict) and isinstance(arch.get("input_modalities"), (list, str)):
        said.append(_words(arch["input_modalities"]))
    for key in ("input_modalities", "modalities"):
        if isinstance(item.get(key), (list, str)):
            said.append(_words(item[key]))
    caps = item.get("capabilities")
    if isinstance(caps, dict):
        said.append({str(k).lower() for k, v in caps.items() if v is True})
    elif isinstance(caps, (list, tuple)) and caps:
        said.append(_words(caps))
    meta = item.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("modalities"), dict) \
            and isinstance(meta["modalities"].get("vision"), bool):
        said.append({"vision"} if meta["modalities"]["vision"] else set())
    if not said:
        return None
    return any(w & IMAGE_WORDS for w in said)


def from_props(data: Any) -> bool | None:
    """llama.cpp's GET /props: {"modalities": {"vision": bool}}."""
    if isinstance(data, dict) and isinstance(data.get("modalities"), dict):
        v = data["modalities"].get("vision")
        if isinstance(v, bool):
            return v
    return None


def from_ollama_show(data: Any) -> bool | None:
    """Ollama's POST /api/show: {"capabilities": ["completion", "vision", ...]}."""
    if isinstance(data, dict) and isinstance(data.get("capabilities"), list):
        return bool(_words(data["capabilities"]) & IMAGE_WORDS)
    return None


def root_of(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


async def detect(ep: ModelEndpoint, api_key: str | None, models: list[str] | None = None) -> dict[str, bool | None]:
    """Ask the endpoint's server which of `models` (default: the endpoint's list) can view images.
    A model nothing answered for is None. Never raises: an unreachable server gives all None."""
    return (await probe(ep, api_key, models))[0]


async def probe(ep: ModelEndpoint, api_key: str | None,
                models: list[str] | None = None) -> tuple[dict[str, bool | None], bool]:
    """detect(), and whether the server answered at all (False: unreachable, try again later)."""
    import httpx

    names = [m for m in (models if models is not None else list(ep.models or [])) if m]
    if ep.adapter != "openai" or not names:
        return {m: None for m in names}, False
    found: dict[str, bool | None] = {m: None for m in names}
    reached = False
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    base, root = ep.base_url.rstrip("/"), root_of(ep.base_url)
    try:
        async with outbound.client(outbound.endpoint_service(ep, "chat"), timeout=DETECT_TIMEOUT) as c:
            try:
                r = await c.get(base + "/models", headers=headers)
                reached = True
                if r.status_code < 400:
                    body = r.json()
                    items = body.get("data") if isinstance(body, dict) else None
                    if not isinstance(items, list) and isinstance(body, dict):
                        items = body.get("models")  # llama.cpp also lists them here
                    for item in items or []:
                        if not isinstance(item, dict):
                            continue
                        mid = str(item.get("id") or item.get("model") or item.get("name") or "")
                        if mid in found and found[mid] is None:
                            found[mid] = from_models_entry(item)
            except (httpx.HTTPError, ValueError) as e:
                log.info("capability detection: %s /models: %s", ep.name, type(e).__name__)
            # A server that serves one model answers /props for it; a router takes ?model=.
            if any(v is None for v in found.values()):
                for m in [m for m, v in found.items() if v is None]:
                    try:
                        r = await c.get(root + "/props", params={"model": m}, headers=headers)
                        reached = True
                        if r.status_code < 400:
                            found[m] = from_props(r.json())
                    except (httpx.HTTPError, ValueError):
                        break  # not llama.cpp
            if any(v is None for v in found.values()):
                for m in [m for m, v in found.items() if v is None]:
                    try:
                        r = await c.post(root + "/api/show", json={"model": m}, headers=headers)
                        reached = True
                        if r.status_code < 400:
                            found[m] = from_ollama_show(r.json())
                        elif r.status_code == 404 and not r.headers.get("content-type", "").startswith("application/json"):
                            break  # not Ollama
                    except (httpx.HTTPError, ValueError):
                        break
    except outbound.Refused as e:
        log.info("capability detection refused for %s: %s", ep.name, e)
    except Exception as e:  # noqa: BLE001  detection is a convenience; the admin can set it by hand
        log.info("capability detection failed for %s: %s", ep.name, type(e).__name__)
    return found, reached


async def refresh(ep: ModelEndpoint) -> dict[str, bool | None]:
    """Detect and store (the caller commits)."""
    from mailr.intel import llm

    found, reached = await probe(ep, llm.endpoint_key(ep))
    apply_detected(ep, found, checked=reached)
    return found


# ---------------------------------------------------------------- the automatic check

# One endpoint's whole check may take this long (several requests of DETECT_TIMEOUT each).
ENDPOINT_TIMEOUT = 30.0
JOB = "models.detect_capabilities"


def unchecked(ep: ModelEndpoint) -> list[str]:
    """The listed models nobody has asked the server about yet: no answer stored, no admin override."""
    out = []
    for m in ep.models or []:
        e = entry(ep, m)
        if isinstance(e.get("vision_manual"), bool) or isinstance(e.get("vision"), bool) or e.get("checked"):
            continue
        out.append(m)
    return out


async def check_unknown() -> dict[str, str]:
    """Ask each enabled OpenAI-compatible endpoint about its unchecked models, one endpoint at a time
    with ENDPOINT_TIMEOUT each. An unreachable server is left as it is, to be asked again at the next
    run (worker start and daily); an admin's override is never touched. Returns, per endpoint, what
    happened (also logged)."""
    import asyncio

    from sqlalchemy import select

    from mailr.db import session_scope
    from mailr.intel import llm

    async with session_scope() as db:
        eps = [e for e in (await db.execute(select(ModelEndpoint).where(ModelEndpoint.enabled.is_(True))
                                            .order_by(ModelEndpoint.id))).scalars()
               if e.adapter == "openai" and unchecked(e)]
        todo = [(e.id, e.name, unchecked(e), e, llm.endpoint_key(e)) for e in eps]
    results: dict[str, str] = {}
    for eid, name, models, ep, key in todo:
        try:
            found, reached = await asyncio.wait_for(probe(ep, key, models), ENDPOINT_TIMEOUT)
        except TimeoutError:
            found, reached = {}, False
        if not reached:
            results[name] = "unreachable; asked again at the next run"
            log.info("model capabilities: %s did not answer; asked again at the next run", name)
            continue
        async with session_scope() as db:
            row = (await db.execute(select(ModelEndpoint).where(ModelEndpoint.id == eid)
                                    .with_for_update())).scalar_one_or_none()
            if row is None:
                continue
            # Only what is still unchecked now: an admin may have set or detected it meanwhile.
            still = set(unchecked(row))
            apply_detected(row, {m: v for m, v in found.items() if m in still}, checked=True)
            await db.commit()
        said = ", ".join(f"{m}: {'images' if v else 'text only' if v is False else 'not said'}"
                         for m, v in found.items() if m in still) or "nothing left to check"
        results[name] = said
        log.info("model capabilities: %s: %s", name, said)
    return results


async def queue_check(db: Any) -> None:
    """Queue the automatic check (once: a queued one is not doubled)."""
    from mailr.core.jobs import enqueue

    await enqueue(db, JOB, {}, dedupe_key=JOB, max_attempts=1)


@handler(JOB)
async def detect_capabilities_job(payload: dict[str, Any], ctx: Any) -> None:
    await check_unknown()


# ---------------------------------------------------------------- requests


def has_images(messages: list[dict[str, Any]]) -> bool:
    return any(isinstance(m.get("content"), list) and any(
        isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"]) for m in messages)


NOT_SHOWN = "[image not shown: this model cannot view images]"


def without_images(messages: list[dict[str, Any]], note: str = NOT_SHOWN) -> list[dict[str, Any]]:
    """The messages with every image part replaced by a short text part (a copy)."""
    out: list[dict[str, Any]] = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in c):
            parts = [p if not (isinstance(p, dict) and p.get("type") == "image_url") else {"type": "text", "text": note}
                     for p in c]
            out.append({**m, "content": parts})
        else:
            out.append(m)
    return out


def for_model(messages: list[dict[str, Any]], ep: ModelEndpoint, model: str) -> list[dict[str, Any]]:
    """What a request to (ep, model) may carry: image parts only for a model that can view them."""
    if has_images(messages) and not can_view_images(ep, model):
        return without_images(messages)
    return messages
