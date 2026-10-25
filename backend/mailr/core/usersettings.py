"""Changes to `users.settings` (JSONB) that never lose another writer's keys.

Many places change one or two keys of a user's settings: the Settings page, layouts, model choices,
the Screener, spam learning, task tags, onboarding, the assistant's approved change, calendar jobs.
Reading the whole dict, changing it and writing it back (`user.settings = {**user.settings, k: v}`)
loses any key another request or job wrote in between: the copy is often the one loaded at
sign-in or at the start of a long job. `patch` changes only the named keys, in one UPDATE
(`settings || patch - removed`), so concurrent writers of other keys never undo each other.

The UPDATE locks the user's row until the transaction ends, as any write does. Call `patch` after
the slow work (network calls, model calls), close to the commit, and never hold the row while
waiting on another lock (the sidebar counts take one and may write the row: core/sidebar.py).

The session's own copy of the user, if it has one, gets the new settings as its loaded value, so
a later flush does not write an older dict back.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.orm.util import identity_key


async def patch(db: AsyncSession, user_id: int, changes: dict[str, Any] | None = None,
                remove: Iterable[str] = ()) -> dict[str, Any]:
    """Set the top-level keys in `changes` and drop those in `remove`, atomically. Returns the
    settings as stored now."""
    from mailr.models import User

    gone = [k for k in remove if k not in (changes or {})]
    new = (await db.execute(text(
        "UPDATE users SET settings = (coalesce(settings, '{}'::jsonb) || CAST(:p AS jsonb)) - CAST(:r AS text[]) "
        "WHERE id = :u RETURNING settings"),
        {"p": json.dumps(changes or {}), "r": gone, "u": user_id})).scalar_one()
    mine = db.identity_map.get(identity_key(User, user_id))
    if mine is not None:
        set_committed_value(mine, "settings", new)
    return new


async def patch_in(db: AsyncSession, user_id: int, key: str, changes: dict[str, Any] | None = None,
                   remove: Iterable[str] = ()) -> dict[str, Any]:
    """As `patch`, one level down: set and drop keys of the object under `key` (made when missing),
    leaving its other keys as stored now. Returns the settings as stored now."""
    from mailr.models import User

    gone = [k for k in remove if k not in (changes or {})]
    new = (await db.execute(text(
        "UPDATE users SET settings = jsonb_set(coalesce(settings, '{}'::jsonb), ARRAY[CAST(:k AS text)], "
        "((CASE WHEN jsonb_typeof(settings -> CAST(:k AS text)) = 'object' THEN settings -> CAST(:k AS text) "
        "ELSE '{}'::jsonb END) || CAST(:p AS jsonb)) - CAST(:r AS text[])) "
        "WHERE id = :u RETURNING settings"),
        {"k": key, "p": json.dumps(changes or {}), "r": gone, "u": user_id})).scalar_one()
    mine = db.identity_map.get(identity_key(User, user_id))
    if mine is not None:
        set_committed_value(mine, "settings", new)
    return new


async def current(db: AsyncSession, user_id: int) -> dict[str, Any]:
    """The settings as stored now (not the session's possibly older copy), for a read-modify-write
    of one key's nested value right before `patch`."""
    return (await db.execute(text("SELECT settings FROM users WHERE id = :u"), {"u": user_id})).scalar_one() or {}
