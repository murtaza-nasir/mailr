"""The last success and the last error of each model task and endpoint.

Every model call (intel/llm.chat, chat_stream, embed_request, intel/jev.decide) reports its
outcome here; Admin → Models per task and Admin → Diagnostics show it, and the Test button
of an endpoint (api/admin_routes.test_endpoint) records its result too.

One row in instance_settings (key "model_health"): {"task:<task>": {...}, "endpoint:<name>":
{...}}, each {ok_at, error_at, error, endpoint, model}, written by core/healthlog.py.

`plain()` turns an exception or an HTTP answer into one sentence an admin can act on. It never
includes the API key or the request body, and cuts what the server said to a short tail. The
sentence is English that keeps its catalogue key (i18n.Text, health.model.*), so Admin → Which
model does what and Diagnostics show it in the admin's language (`of(..., lang)`).
"""

from __future__ import annotations

from typing import Any

import httpx

from mailr import i18n

KEY = "model_health"
T = i18n.text


def plain(err: Any, status: int | None = None, body: str = "") -> str:
    """One plain sentence for what went wrong with a model call (an i18n.Text)."""
    if isinstance(err, httpx.TimeoutException):
        return T("health.model.timeout")
    if isinstance(err, httpx.ConnectError):
        return T("health.model.connect")
    if isinstance(err, httpx.TransportError):
        return T("health.model.transport")
    raw = str(err or "")
    if status is None:
        import re

        m = re.search(r"returned (\d{3})|HTTP (\d{3})", raw)
        if m:
            status = int(m.group(1) or m.group(2))
    low = (body or raw).lower()
    if status in (401, 403):
        return T("health.model.key_refused")
    if status == 404:
        if "model" in low:
            return T("health.model.unknown_model")
        return T("health.model.no_path")
    if status == 400 and "model" in low:
        return T("health.model.model_refused")
    if status == 429:
        return T("health.model.busy")
    if status is not None and status >= 500:
        return T("health.model.server_failed", status=status)
    if status is not None and status >= 400:
        return T("health.model.refused", status=status)
    if "local-only" in low or "local_only" in low:
        return T("health.model.local_only")
    tail = " ".join(raw.split())[:160]
    return T("health.model.said", text=tail) if tail else T("health.model.failed")


async def record(task: str | None, endpoint: str | None, model: str | None, ok: bool,
                 error: str | None = None) -> None:
    """Note one call's outcome. Never raises: health bookkeeping must not break a model call."""
    from mailr.core import healthlog

    keys = [k for k in ((f"task:{task}" if task else None), (f"endpoint:{endpoint}" if endpoint else None)) if k]
    await healthlog.note(KEY, keys, ok, error, {"endpoint": endpoint, "model": model})


async def view(db) -> dict[str, Any]:
    from mailr.core import healthlog

    return await healthlog.read(db, KEY)


def of(health: dict[str, Any], kind: str, name: str, lang: str = i18n.DEFAULT) -> dict[str, Any] | None:
    from mailr.core import healthlog

    return healthlog.entry(health, f"{kind}:{name}", lang)
