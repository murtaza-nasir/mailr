"""The user's profile for the assistant ("About you") and the assistant options.

The profile is typed by the user, so it is not mail content: it may go to any model. It is
injected, compactly, into every assistant, drafting and summary prompt, so drafts use the
user's name and sign-off instead of placeholders like "[Your name]".
"""

from __future__ import annotations

import re
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import AssistantProfile, User

FIELDS = ("about", "preferred_name", "signoff_name", "role", "timezone", "languages", "writing")
# `timezone` is an alias (7 Oct 2026): the one time zone is the account setting `timezone`
# (Settings → You → Region). GET /assistant/profile shows it, and a PUT or the update_profile tool
# that sends it changes the setting. The profile row no longer stores it (migration d1e5a9c3f7b2).
STORED = tuple(k for k in FIELDS if k != "timezone")
LIMITS = {"about": 2000, "writing": 1000, "preferred_name": 100, "signoff_name": 200, "role": 200,
          "timezone": 64, "languages": 200}
DEFAULT_OPTIONS: dict[str, Any] = {
    "learn_from_mail": True, "auto_recall": True,
    # Suggest memories from what the user tells Ambrose in a chat (knowledge/tasks.suggest_from_chat).
    "learn_from_chats": True,
    # The assistant's name: prompts, notifications, approvals, the UI.
    "assistant_name": "Ambrose",
    # Assistant notifications are held back in these hours (HH:MM, the user's time zone; "" = off)
    # and sent as one summary afterwards.
    "quiet_from": "", "quiet_to": "",
}
_NAME = re.compile(r"^[\w][\w .'-]{0,39}$", re.UNICODE)
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def clean_options(opts: dict[str, Any]) -> dict[str, Any]:
    """Validate option values: booleans stay booleans, the name is a short plain name, the
    quiet hours are HH:MM or empty."""
    out: dict[str, Any] = {}
    for k, v in opts.items():
        if k not in DEFAULT_OPTIONS:
            raise ValueError(f"unknown option: {k}")
        default = DEFAULT_OPTIONS[k]
        if isinstance(default, bool):
            if not isinstance(v, bool):
                raise ValueError(f"{k} must be true or false")
        elif k == "assistant_name":
            v = " ".join(str(v or "").split()) or default
            if not _NAME.match(v):
                raise ValueError("the assistant's name: 1-40 letters, spaces, dots, hyphens or apostrophes")
        elif k in ("quiet_from", "quiet_to"):
            v = str(v or "").strip()
            if v and not _HHMM.match(v):
                raise ValueError(f"{k} must be HH:MM")
        out[k] = v
    return out


def assistant_name(row: AssistantProfile | None) -> str:
    return str(options(row)["assistant_name"]) if row is not None else DEFAULT_OPTIONS["assistant_name"]


DEFAULT_NAME: str = DEFAULT_OPTIONS["assistant_name"]


async def name_of(db: AsyncSession | None, user_id: int | None) -> str:
    """The name this user gave the assistant (About you), for texts shown to them; "Ambrose"
    without a user, a profile or a database."""
    if db is None or not user_id:
        return DEFAULT_NAME
    try:
        return assistant_name(await db.get(AssistantProfile, user_id))
    except Exception:  # noqa: BLE001 - a text never fails over the name
        return DEFAULT_NAME


async def get(db: AsyncSession, user_id: int, *, create: bool = True) -> AssistantProfile:
    row = await db.get(AssistantProfile, user_id)
    if row is None:
        row = AssistantProfile(user_id=user_id, data={}, options={}, state={})
        if create:
            db.add(row)
            await db.flush()
    return row


def clean(data: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in FIELDS:
        if k in data and data[k] is not None:
            v = " ".join(str(data[k]).split()) if k not in ("about", "writing") else str(data[k]).strip()
            out[k] = v[: LIMITS[k]]
    tz = out.get("timezone")
    if tz:
        try:
            ZoneInfo(tz)
        except (KeyError, ValueError):
            raise ValueError(f"unknown time zone {tz!r} (use an IANA name such as Europe/London)") from None
    return out


def options(row: AssistantProfile) -> dict[str, Any]:
    return {**DEFAULT_OPTIONS, **(row.options or {})}


def zone_name(user: Any, data: dict[str, Any] | None = None) -> str:
    """The user's one time zone: the account setting. A zone left in profile data by an older
    server counts only while the setting is empty."""
    from mailr.calendar.tz import iana

    raw = str((getattr(user, "settings", None) or {}).get("timezone") or (data or {}).get("timezone") or "").strip()
    return iana(raw) or ""


async def set_zone(db: AsyncSession, user_id: int, tz: str) -> None:
    """Change the account's time zone (the alias `timezone` of the profile). "" clears it."""
    from mailr.calendar import planner

    u = await db.get(User, user_id, with_for_update=True, populate_existing=True)
    if u is None:
        return
    settings = dict(u.settings or {})
    if tz:
        from mailr.calendar.tz import iana

        settings["timezone"] = iana(tz) or tz
    else:
        settings.pop("timezone", None)
    u.settings = settings
    await db.flush()
    await planner.replan_user(db, user_id)  # date-only reminders fire in that zone


def out(row: AssistantProfile, user: User) -> dict[str, Any]:
    d = {k: v for k, v in (row.data or {}).items() if k != "timezone"}
    return {**{k: d.get(k, "") for k in FIELDS}, "timezone": zone_name(user, row.data), "options": options(row),
            "account_name": user.name, "account_email": user.email,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


def names(data: dict[str, Any], user: User) -> tuple[str, str]:
    """(what to call the user, how they sign their mail)."""
    preferred = (data.get("preferred_name") or "").strip()
    full = (user.name or "").strip()
    first = preferred or (full.split()[0] if full else "")
    signoff = (data.get("signoff_name") or "").strip() or first or full
    return first or full or "the user", signoff


def block(data: dict[str, Any], user: User, *, compact: bool = False) -> str:
    """The profile as a few prompt lines. Empty fields are left out."""
    call, sign = names(data, user)
    lines = [f"- Name: {user.name or call}" + (f" (goes by {call})" if call and call not in (user.name or "") else "")
             + f"; signs emails as: {sign}"]
    if data.get("role"):
        lines.append(f"- Role: {data['role']}")
    if zone_name(user, data):
        lines.append(f"- Time zone: {zone_name(user, data)}")
    if data.get("languages"):
        lines.append(f"- Languages: {data['languages']}")
    if not compact:
        if data.get("writing"):
            lines.append(f"- Writing preferences: {data['writing']}")
        if data.get("about"):
            about = str(data["about"]).strip()
            lines.append("- About them (their own words): " + (about[:1500] + "…" if len(about) > 1500 else about))
    return "\n".join(lines)
