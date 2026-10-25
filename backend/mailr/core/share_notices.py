"""The quiet notice of a new share: a line kept in the sharee's own settings until they dismiss it
(calendar/sharing.py for calendars, notes/sharing.py for notes and tables).

Each list lives under its own key of `users.settings` and holds at most MAX notices, newest last.
A notice is a small object with a `path` that names what was shared ("/dav/…" for a calendar,
"note:n…" or "table:t…" for notes), so sharing the same thing again replaces its notice. Every
change is one statement, so a concurrent change of the user's own settings is not overwritten.
No mail, no push, no desktop notification.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MAX = 20


async def add(db: AsyncSession, key: str, user_id: int, notice: dict[str, Any]) -> None:
    await db.execute(text(
        "UPDATE users SET settings = jsonb_set(coalesce(settings, '{}'::jsonb), CAST(:key AS text[]), "
        "(SELECT coalesce(jsonb_agg(n), '[]'::jsonb) FROM (SELECT n FROM jsonb_array_elements("
        "coalesce(settings->:k, '[]'::jsonb)) n WHERE n->>'path' <> :path "
        "UNION ALL SELECT CAST(:note AS jsonb)) x) ) WHERE id = :u"),
        {"key": [key], "k": key, "path": notice["path"], "note": json.dumps(notice), "u": user_id})
    await db.execute(text(
        "UPDATE users SET settings = jsonb_set(settings, CAST(:key AS text[]), (SELECT coalesce(jsonb_agg(n ORDER BY i), '[]') "
        "FROM jsonb_array_elements(settings->:k) WITH ORDINALITY AS t(n, i) "
        "WHERE i > jsonb_array_length(settings->:k) - :max)) WHERE id = :u"),
        {"key": [key], "k": key, "max": MAX, "u": user_id})


async def drop(db: AsyncSession, key: str, user_id: int, path: str | None) -> None:
    """Drop the notice of `path`, or every notice (path None: a dismissal)."""
    await db.execute(text(
        "UPDATE users SET settings = jsonb_set(settings, CAST(:key AS text[]), (SELECT coalesce(jsonb_agg(n), '[]'::jsonb) "
        "FROM jsonb_array_elements(settings->:k) n WHERE CAST(:path AS text) IS NOT NULL AND n->>'path' <> :path)) "
        "WHERE id = :u AND settings ? :k"),
        {"key": [key], "k": key, "path": path, "u": user_id})


async def listed(db: AsyncSession, key: str, user_id: int) -> list[dict[str, Any]]:
    row = (await db.execute(text("SELECT settings->:k FROM users WHERE id = :u"), {"k": key, "u": user_id})).scalar()
    return [n for n in (row or []) if isinstance(n, dict)]
