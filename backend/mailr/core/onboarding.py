"""The guided setup ("Setup guide") a new user sees on first sign-in, and the admin's presets.

Progress is stored per user in `users.settings["onboarding"]`:

    {"status": "pending" | "active" | "done" | "dismissed",
     "step": "<the step on screen>",
     "steps": {"<step>": "done" | "skipped"},
     "started_at": iso, "finished_at": iso | null, "preset": "<preset id>" | null}

- `pending`: the account is new (single sign-on created it, an admin added it, or a sign-up); the
  guide opens by itself on the next visit.
- `active`: the guide was started (or reopened from Settings → You → Setup guide) and is not
  finished; the app shows a "Continue setup" card instead of opening it again.
- `done`: finished. `dismissed`: closed with "Not now"; it stays closed, and Settings reopens it.
- No key at all: an account from before the guide existed. Nothing opens by itself; the card
  invites the user to set up only while the account has no mail account yet.

The steps are fixed (STEPS). Each is done or skipped; the optional ones may be skipped with no
consequence. Only the signed-in user changes their own progress (api/onboarding_routes.py).

**Presets** ("Prepare a new user", Admin → Users) are instance settings: defaults for one email
address or for everyone in an identity-provider group, applied once to an account that has not
started the guide (status pending: at its first single sign-on, or at the first GET /onboarding;
an account from before the guide never takes one). They fill in
the look, the inbox style, notifications, whether the assistant steps are offered and whether the
last screen introduces notes and lists, so the
guide has fewer choices. An email preset is used once; a group preset applies to every new
member.
"""

from __future__ import annotations

import copy
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import User

STEPS = ("welcome", "accounts", "look", "inbox", "phone", "calendar", "notifications", "about", "done")
OPTIONAL = frozenset({"phone", "calendar", "about"})
STATUSES = ("pending", "active", "done", "dismissed")
MARKS = ("done", "skipped")
KEY = "onboarding"
PRESETS_KEY = "onboarding_presets"
MAX_PRESETS = 200

# What a preset may set, with the values each key accepts (None: checked by its own rule).
THEMES = ("classic", "office", "focus", "desk", "cards")
PRESET_KEYS: dict[str, tuple[str, ...] | None] = {
    "ui_theme": THEMES,
    "theme": ("system", "light", "dark"),
    "density": ("default", "comfortable", "compact"),
    # The three inbox styles the guide offers (core/layouts.py presets).
    "inbox_type": ("simple", "important_first", "default"),
    # The mail notification level (core/notifyprefs.py). Presets saved before 7 Oct 2026 say
    # desktop_notifications; apply_preset writes either as mail_notifications.
    "mail_notifications": ("off", "vip", "important", "primary", "all"),
    "desktop_notifications": ("off", "important", "vip", "all"),
    # Offer the assistant steps (About you) in the guide. The access policy still decides
    # whether the assistant can be used.
    "assistant": None,
    # Introduce notes and lists on the last screen (a shopping list or a note in one tap, and the
    # Notes app on a phone).
    "notes": None,
    # A CalDAV server address the calendar step fills in (the admin's calendar server; each person signs in with their own account).
    "calendar_url": None,
}
# The settings a preset writes to the account at once (the rest steer the guide).
SETTING_KEYS = ("ui_theme", "theme", "density", "inbox_type", "mail_notifications")
_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,255}")
_GROUP = re.compile(r"[^\s].{0,199}")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def state(user: User) -> dict[str, Any]:
    """The stored progress (a copy), {} for an account from before the guide."""
    v = (user.settings or {}).get(KEY)
    return dict(v) if isinstance(v, dict) else {}


def public(user: User, *, has_accounts: bool | None = None) -> dict[str, Any]:
    """Progress as the apps read it. `status` is "none" for an account without a stored state."""
    st = state(user)
    steps = {k: v for k, v in (st.get("steps") or {}).items() if k in STEPS and v in MARKS}
    status = st.get("status") if st.get("status") in STATUSES else "none"
    step = st.get("step") if st.get("step") in STEPS else next((s for s in STEPS if s not in steps), "done")
    out = {"status": status, "step": step, "steps": steps, "order": list(STEPS), "optional": sorted(OPTIONAL),
           "started_at": st.get("started_at"), "finished_at": st.get("finished_at"), "preset": st.get("preset"),
           "completed": sum(1 for s in STEPS if s in steps and s != "done"), "total": len(STEPS) - 1}
    if has_accounts is not None:
        out["has_accounts"] = has_accounts
    return out


def summary(user: User) -> dict[str, Any]:
    """What Admin → Users shows: the status, the step and when it finished."""
    p = public(user)
    return {"status": p["status"], "step": p["step"], "completed": p["completed"], "total": p["total"],
            "finished_at": p["finished_at"]}


def _write(user: User, st: dict[str, Any]) -> None:
    user.settings = {**(user.settings or {}), KEY: st}


def mark_new(user: User) -> None:
    """A new account: the guide opens by itself on the first visit."""
    if not state(user):
        _write(user, {"status": "pending", "step": "welcome", "steps": {}, "started_at": None, "finished_at": None})


def update(user: User, *, step: str | None = None, mark: dict[str, str] | None = None,
           status: str | None = None) -> dict[str, Any]:
    """Move to `step`, record `mark` ({step: done|skipped}), set `status`. ValueError on bad input."""
    st = state(user) or {"status": "active", "steps": {}}
    steps = dict(st.get("steps") or {})
    for k, v in (mark or {}).items():
        if k not in STEPS:
            raise ValueError(f"unknown step {k!r}")
        if v not in MARKS:
            raise ValueError("a step is marked done or skipped")
        steps[k] = v
    if step is not None:
        if step not in STEPS:
            raise ValueError(f"unknown step {step!r}")
        st["step"] = step
    if status is not None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        st["status"] = status
    elif st.get("status") in (None, "pending"):
        st["status"] = "active"  # any progress starts the guide
    if st.get("status") == "active" and not st.get("started_at"):
        st["started_at"] = now_iso()
    if st.get("status") == "done":
        steps["done"] = "done"
        st["finished_at"] = st.get("finished_at") or now_iso()
        st["step"] = "done"
    st["steps"] = steps
    _write(user, st)
    return st


def restart(user: User) -> dict[str, Any]:
    """Settings → You → Setup guide: open the guide again at the start. Steps already done
    stay ticked; finishing again keeps the first finish time."""
    st = state(user) or {"steps": {}}
    st.update({"status": "active", "step": "welcome", "started_at": st.get("started_at") or now_iso()})
    _write(user, st)
    return st


# ---------------------------------------------------------------- presets


def clean_preset(body: dict[str, Any]) -> dict[str, Any]:
    """Validate a preset: {match: {email} | {group}, defaults: {...}, note}. ValueError."""
    match = body.get("match") or {}
    email = str(match.get("email") or "").strip().lower()
    group = str(match.get("group") or "").strip()
    if bool(email) == bool(group):
        raise ValueError("a preset is for one email address or for one group")
    if email and not _EMAIL.fullmatch(email):
        raise ValueError("that is not an email address")
    if group and not _GROUP.fullmatch(group):
        raise ValueError("a group name is 1-200 characters")
    raw = body.get("defaults") or {}
    if not isinstance(raw, dict):
        raise ValueError("defaults must be an object")
    defaults: dict[str, Any] = {}
    for k, v in raw.items():
        if v in (None, ""):
            continue
        if k not in PRESET_KEYS:
            raise ValueError(f"a preset cannot set {k!r}")
        allowed = PRESET_KEYS[k]
        if k in ("assistant", "notes"):
            if not isinstance(v, bool):
                raise ValueError(f"{k} must be true or false")
        elif k == "calendar_url":
            v = str(v).strip()
            if len(v) > 500 or not v.startswith(("https://", "http://")):
                raise ValueError("the calendar address must be an http(s) link")
        elif allowed is not None and v not in allowed:
            raise ValueError(f"{k} must be one of {', '.join(allowed)}")
        defaults[k] = v
    note = str(body.get("note") or "").strip()[:300]
    return {"match": {"email": email} if email else {"group": group}, "defaults": defaults, "note": note}


async def presets(db: AsyncSession) -> list[dict[str, Any]]:
    from mailr.api.admin_routes import get_instance_setting

    v = await get_instance_setting(db, PRESETS_KEY, []) or []
    # Copies: the caller changes them, and the row must see a new value to save it.
    return [copy.deepcopy(p) for p in v if isinstance(p, dict) and p.get("id")]


async def save_presets(db: AsyncSession, items: list[dict[str, Any]]) -> None:
    from mailr.api.admin_routes import _set_instance

    await _set_instance(db, PRESETS_KEY, items)


def new_preset_id() -> str:
    return "p" + secrets.token_hex(5)


def preset_for(items: list[dict[str, Any]], user: User) -> dict[str, Any] | None:
    """The preset for this account: an unused one for its address first, else the first one for
    a group it is in (provider groups, or assigned groups for a password account)."""
    from mailr.core.access import effective_groups

    email = (user.email or "").lower()
    for p in items:
        if (p.get("match") or {}).get("email") == email and not p.get("used_by"):
            return p
    groups = set(effective_groups(user))
    for p in items:
        g = (p.get("match") or {}).get("group")
        if g and g in groups:
            return p
    return None


def apply_defaults(user: User, defaults: dict[str, Any], source: str) -> None:
    """Write a preset's (or an invite's, core/invites.py) defaults to the account: the settings at
    once, the rest as what the guide offers. `source` is the preset id ("invite:<id>" for an invite)."""
    d = dict(defaults or {})
    if "desktop_notifications" in d:  # a preset saved under the old name
        d.setdefault("mail_notifications", d.pop("desktop_notifications"))
    settings = dict(user.settings or {})
    for k in SETTING_KEYS:
        if k in d:
            settings[k] = d[k]
    st = dict(settings.get(KEY) or {})
    st["preset"] = source
    st["offer"] = {k: d[k] for k in ("assistant", "notes", "calendar_url") if k in d}
    settings[KEY] = st
    user.settings = settings


def preset_due(user: User) -> bool:
    """A preset may still apply: the guide has not started and no preset was looked for yet."""
    st = state(user)
    return not st.get("preset_checked") and st.get("status") == "pending"


async def apply_preset(db: AsyncSession, user: User) -> dict[str, Any] | None:
    """Apply the matching preset once, to an account that has not started the guide. Returns the
    preset applied, or None. The caller commits; it passes a copy of the user it has locked (or
    one that no other request can see yet), so the settings written are not an older copy's."""
    st = state(user)
    if not preset_due(user):
        return None  # started, finished, or an account from before the guide
    items = await presets(db)
    p = preset_for(items, user)
    st["preset_checked"] = True
    if p is None:
        _write(user, st)
        return None
    _write(user, st)
    apply_defaults(user, p.get("defaults") or {}, p["id"])
    if (p.get("match") or {}).get("email"):
        p["used_by"] = user.id
        p["used_at"] = now_iso()
        if p.get("mail_account"):  # an account the admin prepared (core/managed.py)
            from mailr.core import managed

            await managed.connect_from_preset(db, user, p)
    else:
        p["applied"] = int(p.get("applied") or 0) + 1
    await save_presets(db, items)
    return p
