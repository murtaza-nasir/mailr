"""Notifications from the assistant to the user (never to anyone else).

One path for every assistant message: web (SSE `notification` with kind "assistant") and the
phone (push event "assistant"). Quiet hours (profile options quiet_from / quiet_to, in the
user's time zone) hold notifications back; `flush_quiet` sends what was held as one summary
when the quiet period ends.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import User

log = logging.getLogger("mailr.assistant.notify")


def _tz(user: User, prof) -> ZoneInfo:
    from mailr.calendar.tz import user_zone_from

    return user_zone_from(user, prof.data if prof is not None else None)


def in_quiet_hours(user: User, prof, now: datetime | None = None) -> bool:
    from mailr.knowledge import profile

    opts = profile.options(prof) if prof is not None else profile.DEFAULT_OPTIONS
    a, b = opts.get("quiet_from") or "", opts.get("quiet_to") or ""
    if not a or not b:
        return False
    local = (now or datetime.now(UTC)).astimezone(_tz(user, prof)).time()
    t0, t1 = time.fromisoformat(a), time.fromisoformat(b)
    return (t0 <= local < t1) if t0 < t1 else (local >= t0 or local < t1)


PRIVATE_BODY = "Open Mailr to read it."  # English; push.private.default.body in the recipient's language


def private_body(user: User) -> str:
    """PRIVATE_BODY in the user's language."""
    from mailr import i18n

    return i18n.t("push.private.default.body", i18n.user_language(user))


async def send(db: AsyncSession, user: User, *, title: str, body: str, data: dict[str, Any],
               priority: str = "default", force: bool = False, private: bool = False) -> str:
    """Notify the user. Returns "sent" or "held" (quiet hours). `private`: the text came from
    local-only mail, so the phone (push) gets only the title and PRIVATE_BODY; the open web
    session, served by mailr itself, still shows the text."""
    from mailr.core.events import notify as sse
    from mailr.knowledge import profile

    prof = await profile.get(db, user.id)
    payload = {"kind": "assistant", "title": title[:120], "body": body[:600], **data}
    if not force and in_quiet_hours(user, prof):
        state = dict(prof.state or {})
        held = list(state.get("held_notifications") or [])[-30:]
        held.append({"title": payload["title"], "body": private_body(user) if private else payload["body"][:200],
                     "at": datetime.now(UTC).isoformat(),
                     **{k: v for k, v in data.items() if k in ("run_id", "order_id", "conversation_id")}})
        state["held_notifications"] = held
        prof.state = state
        return "held"
    await sse(db, user.id, {"type": "notification", **payload})
    from mailr.core.push import fan_out

    try:
        # fan_out also checks the data itself (a local-only conversation or account) when the
        # caller did not mark the text private.
        await fan_out(db, user.id, "assistant", payload["title"], private_body(user) if private else payload["body"],
                      data, priority, private=True if private else None)
    except Exception as e:  # noqa: BLE001 - a push outage must not fail the run
        log.info("assistant push failed: %s", e)
    return "sent"


async def flush_quiet(db: AsyncSession, user: User) -> int:
    """After quiet hours: one summary of what was held."""
    from mailr.knowledge import profile

    prof = await profile.get(db, user.id)
    held = list((prof.state or {}).get("held_notifications") or [])
    if not held or in_quiet_hours(user, prof):
        return 0
    prof.state = {**(prof.state or {}), "held_notifications": []}
    name = profile.assistant_name(prof)
    from mailr import i18n

    lang = i18n.user_language(user)
    lines = [i18n.t("digest.quiet_line", lang, title=h["title"], body=h["body"][:80]) for h in held[-8:]]

    await send(db, user, title=i18n.t("assistant.quiet_summary", lang, name=name, count=len(held)),
               body="\n".join(lines), data={"summary": True}, force=True)
    return len(held)
