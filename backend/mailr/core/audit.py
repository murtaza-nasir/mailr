from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import AuditLog

_MAX = 4000


def _trim(v: Any) -> Any:
    if isinstance(v, str):
        return v if len(v) <= _MAX else v[:_MAX] + "…"
    if isinstance(v, dict):
        return {k: _trim(x) for k, x in list(v.items())[:60]}
    if isinstance(v, list):
        return [_trim(x) for x in v[:60]]
    return v


async def audit(
    db: AsyncSession,
    principal,
    action: str,
    target: str = "",
    inputs: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    ok: bool = True,
    always: bool = False,
) -> None:
    """Log token (agent) activity. Session activity is logged only when `always` is set."""
    if principal is None:
        return
    if principal.token is None and not always:
        return
    db.add(
        AuditLog(
            user_id=principal.user_id,
            token_id=principal.token.id if principal.token else None,
            actor=principal.actor,
            action=action,
            target=str(target)[:200],
            inputs=_trim(inputs or {}),
            result=_trim(result or {}),
            ok=ok,
        )
    )


NOTICE_PREFIX = "admin_notice."


async def notice(db: AsyncSession, principal, user_id: int, kind: str, description: str | dict[str, Any],
                 details: dict[str, Any] | None = None) -> None:
    """An admin's change that affects another person, written to THAT person's own audit
    log: user_id is the affected user, actor "admin", action "admin_notice.<kind>", and
    inputs.description one plain sentence they can read ("Your administrator turned off your
    account."), plus who did it. The admin's own entry (admin.*) stays in the admin's log.
    Never a password or a key in `details`. A change admins make to their own account is
    not repeated.

    `description` is an i18n.msg (a key under activity.admin, rendered in the reader's language
    when the feed is read; its English is kept as inputs.description) or, from older code, a
    plain English sentence."""
    if principal is None or user_id == getattr(principal, "user_id", None):
        return
    u = getattr(principal, "user", None)
    by = (getattr(u, "name", "") or getattr(u, "email", "") or "your administrator")[:120]
    from mailr import i18n

    extra: dict[str, Any] = {}
    if i18n.is_msg(description):
        extra["msg"] = description
        description = i18n.render(description)
    db.add(AuditLog(user_id=user_id, token_id=None, actor="admin", action=f"{NOTICE_PREFIX}{kind}"[:100],
                    target=str(user_id), inputs=_trim({"description": description, "by": by, **(details or {}), **extra}),
                    result={}, ok=True))
