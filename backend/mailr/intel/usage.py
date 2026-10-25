"""Model use per person: which model did each kind of work, and whether it was local
(Settings → Ambrose → Model use).

llm.resolve and llm.resolve_embed call `record` once per model call they resolve (never for a
probe). Each call adds one to a row (user, day, feature, endpoint, model) of `model_usage`, in a
transaction of its own so a caller's rollback or long transaction never holds it. A failure is
logged and ignored: counting never breaks a model call. Rows older than KEEP_DAYS are pruned by
the daily maintenance.

A feature is what the person would call the work ("Summaries", "Ambrose"). Callers name it
(`feature=` on llm.chat, chat_stream and resolve); without a name the task's label is used.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("mailr.usage")

KEEP_DAYS = 90
WINDOW_DAYS = 30

FEATURES: dict[str, str] = {
    "assistant": "Ambrose",
    "summaries": "Summaries",
    "translation": "Translation",
    "replies": "Reply suggestions and drafting help",
    "events": "Events from email",
    "triage": "Sorting new mail",
    "categories": "Categories",
    "standing_orders": "Standing orders",
    "meeting_prep": "Meeting prep",
    "recordings": "Recordings",
    "memory": "Ambrose's memory",
    "signatures": "Signatures",
    "setup": "Setup guide",
    "search": "Search index",
    "dictation": "Dictation",
    # The task labels, for calls that name no feature (llm.TASKS).
    "chat": "Writing (other)",
    "classify": "Sorting (other)",
}


def label(feature: str, name: str | None = None) -> str:
    """The feature in words; `name` is the user's name for the assistant ("Ambrose's memory")."""
    text = FEATURES.get(feature, feature.replace("_", " ").capitalize())
    return text.replace("Ambrose", name) if name and feature in ("assistant", "memory") else text


async def record(user_id: int | None, feature: str, endpoint: str, model: str, local: bool) -> None:
    if user_id is None:
        return
    from mailr.db import session_scope

    try:
        async with session_scope() as db:
            await db.execute(text(
                "INSERT INTO model_usage (user_id, day, feature, endpoint, model, local, calls) "
                "VALUES (:u, :d, :f, :e, :m, :l, 1) ON CONFLICT (user_id, day, feature, endpoint, model) "
                "DO UPDATE SET calls = model_usage.calls + 1, local = EXCLUDED.local"),
                {"u": user_id, "d": datetime.now(UTC).date(), "f": feature[:40], "e": endpoint[:200],
                 "m": (model or "")[:200], "l": bool(local)})
            await db.commit()
    except Exception as e:  # noqa: BLE001  counting never breaks a model call
        log.warning("could not count a model call: %s", type(e).__name__)


async def summary(db: AsyncSession, user_id: int, days: int = WINDOW_DAYS) -> list[dict[str, Any]]:
    """One row per feature and model over the last `days` days, most used first."""
    since = datetime.now(UTC).date() - timedelta(days=days - 1)
    rows = (await db.execute(text(
        "SELECT feature, endpoint, model, bool_and(local) AS local, sum(calls) AS calls, max(day) AS last "
        "FROM model_usage WHERE user_id = :u AND day >= :s GROUP BY feature, endpoint, model "
        "ORDER BY sum(calls) DESC, feature"), {"u": user_id, "s": since})).all()
    from mailr.knowledge import profile

    name = await profile.name_of(db, user_id)
    return [{"feature": r.feature, "label": label(r.feature, name), "endpoint": r.endpoint, "model": r.model,
             "local": bool(r.local), "calls": int(r.calls), "last": r.last.isoformat()} for r in rows]


async def prune(db: AsyncSession) -> int:
    res = await db.execute(text("DELETE FROM model_usage WHERE day < :d"),
                           {"d": datetime.now(UTC).date() - timedelta(days=KEEP_DAYS)})
    return res.rowcount or 0
