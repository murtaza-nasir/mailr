"""What account recovery resets beside sessions, tokens and app passwords: the standing channels
someone holding a session could have set up to keep acting for the user afterwards.

Sign out everywhere, a password change and an administrator's reset call `reset_channels`. It
takes back what was set up within the window of the sessions that ended (from the oldest of them,
and never less than REVIEW_DAYS), and leaves older choices alone, so a routine password change
costs nothing when nothing new was added:

- "Follow my forwarded notes" set to Allow in the window goes back to Ask me: a forwarded note then
  waits on an approval card instead of running as the user's instruction.
- Allow rules for Ambrose made in the window (rules and "Allow always" grants of every agent
  action) become Ask me: the action asks first until the user allows it again.
- Addresses that speak for the user (core/trusted.py) confirmed in the window need a new code.
- Forwarding addresses verified in the window need a new code; mail is not forwarded to them
  until then (filters forward only to verified addresses).

The list is kept as the server-owned setting `recovery_review` (never written by a client: see
api/auth_routes.CLIENT_SETTINGS), shown under Settings → You → Password & devices as "Review after
signing out" until the user dismisses it (DELETE /me/recovery-review), and returned by the
recovery routes as `review`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import ForwardAddress, PermissionRule, TrustedAddress, User

REVIEW_DAYS = 14
KEY = "recovery_review"
# Rules that act for Ambrose and every agent (token and session rules end with them anyway).
_SUBJECTS = ("assistant", "*")


def window_start(ended: list[datetime], now: datetime | None = None) -> datetime:
    """The start of the window: the oldest ended session, and at least REVIEW_DAYS back."""
    now = now or datetime.now(UTC)
    return min([now - timedelta(days=REVIEW_DAYS), *ended])


async def reset_channels(db: AsyncSession, user: User, since: datetime) -> dict[str, Any]:
    """Reset what was set up since `since` (above) and keep the list for the user to review.
    Returns the review ({} when nothing was reset). The caller commits."""
    from mailr.core import trusted, usersettings

    now = datetime.now(UTC)
    review: dict[str, Any] = {"at": now.isoformat(), "since": since.isoformat(), "forwarded_notes_reset": False,
                              "paused_rules": [], "trusted": [], "forwards": []}
    rules = (await db.execute(select(PermissionRule).where(
        PermissionRule.user_id == user.id, PermissionRule.effect == "allow",
        PermissionRule.subject.in_(_SUBJECTS), PermissionRule.created_at >= since))).scalars().all()
    for r in rules:
        if r.action == trusted.ACTION and not r.constraints:
            review["forwarded_notes_reset"] = True
        else:
            review["paused_rules"].append({"id": r.id, "action": r.action, "source": r.source})
        r.effect = "ask"
    for t in (await db.execute(select(TrustedAddress).where(
            TrustedAddress.user_id == user.id, TrustedAddress.confirmed_at.is_not(None),
            TrustedAddress.confirmed_at >= since))).scalars():
        review["trusted"].append({"email": t.email, "confirmed_at": (t.confirmed_at or now).isoformat()})
        t.confirmed_at, t.code_hash, t.code_expires_at = None, "", None
    for f in (await db.execute(select(ForwardAddress).where(
            ForwardAddress.user_id == user.id, ForwardAddress.verified_at.is_not(None),
            ForwardAddress.verified_at >= since))).scalars():
        review["forwards"].append({"email": f.email, "verified_at": (f.verified_at or now).isoformat()})
        f.verified_at, f.code_hash, f.attempts = None, "", 0
    await db.flush()
    if not (review["forwarded_notes_reset"] or review["paused_rules"] or review["trusted"] or review["forwards"]):
        return {}
    await usersettings.patch(db, user.id, {KEY: review})
    return review
