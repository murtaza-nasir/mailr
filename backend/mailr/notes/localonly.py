"""Who may change `local_only` on a note, a list or a table, and who hears about it.

- Anyone who can edit may turn Local models only **on** (it only makes the item stricter).
- Only the owner may turn it **off**. For a group-owned item the owner is its creator. An editor's
  attempt is refused with 403, and a revert or a version restore by an editor keeps the flag on.
- When someone other than the owner changes it, the owner gets a quiet notice (the notes notices
  in `users.settings.notes_share_notices`, `what: "local_only"`) and a notification event. The
  change is in the item's history: a note revision ("Local models only on") or a table history
  row ("Turned Local models only on").
- An agent (the assistant or an API token) that turns it off always asks first, and the approval
  card says so (notes/ops.py and tables/routes.py gates).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import User

OWNER_ONLY = "Only the owner can turn Local models only off"
LOWER_ASKS = "hard rule: turning Local models only off lets hosted models read this, so it asks first"


def owner_id(obj: Any) -> int | None:
    """The owner of a note or table: its owning user, or the creator of a group-owned item."""
    return obj.owner_user_id if obj.owner_user_id is not None else getattr(obj, "created_by", None)


def can_lower(user: User, obj: Any) -> bool:
    return owner_id(obj) == user.id


def lowers(obj: Any, value: Any) -> bool:
    """Would setting local_only to `value` turn it off?"""
    return bool(obj.local_only) and value is not None and not bool(value)


def check(user: User, obj: Any, changes: dict[str, Any]) -> None:
    """Refuse an editor's attempt to turn local_only off (403)."""
    if "local_only" in changes and lowers(obj, changes["local_only"]) and not can_lower(user, obj):
        raise HTTPException(403, OWNER_ONLY)


async def tell_owner(db: AsyncSession, kind: str, obj: Any, actor: User, on: bool) -> None:
    """A change by someone other than the owner: a notice and a notification for the owner."""
    from mailr.core import share_notices
    from mailr.core.events import notify
    from mailr.notes import sharing

    oid = owner_id(obj)
    if oid is None or oid == actor.id:
        return
    owner = await db.get(User, oid)
    if owner is None or owner.disabled:
        return
    by = sharing.person(actor)["name"]
    name = sharing.name_of(kind, obj)
    notice = {"path": f"{sharing.path_of(kind, obj)}#local_only", "what": "local_only", "on": on, "kind": kind,
              "id": obj.pid, "name": name, "noun": sharing.noun(kind, obj), "by": by,
              "at": datetime.now(UTC).isoformat()}
    await share_notices.add(db, sharing.NOTICES, oid, notice)
    from mailr import i18n

    key = "notify.local_only_on" if on else "notify.local_only_off"
    await notify(db, oid, {"type": "notification", "kind": "notes_local_only", "item_kind": kind, "id": obj.pid,
                           "title": i18n.t(key, i18n.user_language(owner), by=by, name=name)})


def card_line(value: Any) -> str:
    """The approval card's line for a local_only change."""
    return ("Local models only: on" if value else
            "Local models only: off. Hosted models and agents that use them can then read it.")
