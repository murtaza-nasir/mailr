"""Last success and last error of things that fail quietly: model calls (intel/modelhealth.py)
and push deliveries (core/push.py). Admin → Diagnostics shows them.

One instance_settings row per area, e.g. "model_health" = {"task:chat": {ok_at, error_at, error,
…}, "endpoint:Home GPU": {…}}. Each sub-key is merged in one statement, so two processes never
overwrite each other's entries, and the last success survives the next error (and the other way
round). A success is written at most once per OK_EVERY seconds per sub-key (a busy classifier
calls its model for every new message); an error, and the first success after an error, are
always written. Nothing here ever raises: bookkeeping must not break the call it describes.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

log = logging.getLogger("mailr.healthlog")

OK_EVERY = 300
ERROR_MAX = 300
_last: dict[str, tuple[bool, float]] = {}


def _due(key: str, ok: bool) -> bool:
    prev = _last.get(key)
    now = time.monotonic()
    if prev is None or prev[0] != ok or not ok or now - prev[1] >= OK_EVERY:
        _last[key] = (ok, now)
        return True
    return False


async def note(row: str, keys: list[str], ok: bool, error: str | None = None,
               extra: dict[str, Any] | None = None) -> None:
    keys = [k for k in keys if _due(f"{row}/{k}", ok)]
    if not keys:
        return
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    patch: dict[str, Any] = dict(extra or {})
    if ok:
        patch["ok_at"] = now
    else:
        from mailr import i18n

        patch["error_at"] = now
        patch["error"] = (error or i18n.t("health.model.failed"))[:ERROR_MAX]
        # The error's key (an i18n.Text), so a reader sees it in their language; null for plain text.
        patch["error_msg"] = i18n.msg_of(error)
    try:
        from mailr.db import session_scope

        async with session_scope() as db:
            for k in keys:
                await db.execute(text(
                    "INSERT INTO instance_settings (key, value, updated_at) "
                    "VALUES (:k, jsonb_build_object(CAST(:sub AS text), CAST(:patch AS jsonb)), now()) "
                    "ON CONFLICT (key) DO UPDATE SET updated_at = now(), value = instance_settings.value || "
                    "jsonb_build_object(CAST(:sub AS text), "
                    "COALESCE(instance_settings.value -> CAST(:sub AS text), '{}'::jsonb) || CAST(:patch AS jsonb))"),
                    {"k": row, "sub": k, "patch": json.dumps(patch)})
    except Exception as e:  # noqa: BLE001
        log.debug("could not record %s: %s", row, type(e).__name__)


async def read(db, row: str) -> dict[str, Any]:
    v = (await db.execute(text("SELECT value FROM instance_settings WHERE key = :k"), {"k": row})).scalar()
    return v if isinstance(v, dict) else {}


def entry(data: dict[str, Any], key: str, lang: str | None = None) -> dict[str, Any] | None:
    """One entry, with `ok`: whether its newest outcome was a success, and its error in `lang`."""
    from mailr import i18n

    h = data.get(key)
    if not isinstance(h, dict):
        return None
    ok_at, err_at = h.get("ok_at"), h.get("error_at")
    out = {k: v for k, v in h.items() if k != "error_msg"}
    if h.get("error") and i18n.is_msg(h.get("error_msg")):
        out["error"] = i18n.render(h["error_msg"], lang, fallback=h["error"])[:ERROR_MAX]
    return {**out, "ok": bool(ok_at and (not err_at or ok_at >= err_at))}


def reset() -> None:
    _last.clear()
