"""Local models only, at two levels.

- **The household rule** (admin; instance setting `models.local_policy`):
  `per_user` (the default: each person chooses) or `local_all` (local models only for everyone).
  An older `hosted` value reads as `per_user`, and PUT still takes it as an alias for one release.
- **The person's switch** "Local models only for me" (`users.settings.local_models_only`, off by
  default). It can always make things stricter: no admin setting locks it off. Under `local_all` it
  shows as on and locked, with the reason.

The effective rule is admin OR user OR the item's own local_only flag. llm.resolve and
llm.resolve_embed apply it (intel/llm.py), the one place every model call passes: when the rule
holds, the call is treated as sensitive, so it can only reach a local endpoint. A feature with no
local endpoint for its task then fails with llm.LocalModelsOnly ("not available with local
models only"), a NoModel, so every caller degrades as it does with no model configured.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import InstanceSetting, User

SETTING = "models.local_policy"
POLICIES = ("per_user", "local_all")
ALIASES = {"hosted": "per_user"}  # the third choice of 6 Oct 2026, folded into per_user
DEFAULT = "per_user"
USER_KEY = "local_models_only"

REASONS = {
    "local_all": "Your administrator set local models only for everyone on this server.",
}
UNAVAILABLE = "Not available with local models only. No local model is set up for {what}."


async def household(db: AsyncSession) -> str:
    row = await db.get(InstanceSetting, SETTING)
    v = ALIASES.get(row.value, row.value) if row is not None else DEFAULT
    return v if v in POLICIES else DEFAULT


async def set_household(db: AsyncSession, value: str) -> None:
    value = ALIASES.get(value, value)
    if value not in POLICIES:
        raise ValueError(f"the rule for everyone is one of {', '.join(POLICIES)}")
    row = await db.get(InstanceSetting, SETTING)
    if row is None:
        db.add(InstanceSetting(key=SETTING, value=value))
    else:
        row.value = value


def user_wants(user: User | None) -> bool:
    return bool(user is not None and (user.settings or {}).get(USER_KEY) is True)


def effective(policy: str, user: User | None) -> bool:
    """Does the rule (household or person) keep this user's model calls on local endpoints?"""
    return policy == "local_all" or user_wants(user)


async def strict(db: AsyncSession, user: User | None) -> bool:
    """The rule for one call. A system job (user None) follows the household rule only."""
    return effective(await household(db), user)


async def group_strict(db: AsyncSession, group: str | None) -> bool:
    """The rule for content a group owns (group notes and tables): the household rule, or any
    member's "Local models only for me". A member who wants local models must not find their
    group's notes sent to a hosted model because nobody owns them as a person."""
    if await household(db) == "local_all":
        return True
    if not group:
        return False
    from sqlalchemy import select

    from mailr.core.access import effective_groups

    wanting = (await db.execute(select(User).where(User.settings[USER_KEY].astext == "true"))).scalars()
    return any(user_wants(u) and group in effective_groups(u) for u in wanting)


async def state(db: AsyncSession, user: User) -> dict[str, Any]:
    """What Settings → Ambrose shows: the household rule, the person's own choice, the result, and
    whether (and why) the switch is locked."""
    pol = await household(db)
    return {"household": pol, "mine": user_wants(user), "effective": effective(pol, user),
            "locked": pol != "per_user", "reason": REASONS.get(pol)}
