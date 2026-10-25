"""Household invites: a one-time link an admin makes for a household member.

The admin makes an invite for one email address, with optional setup defaults in the shape of an
onboarding preset (core/onboarding.py). The link is shown once, with a QR code; mailr keeps only
the SHA-256 of its token. Opening the link leads through sign-in to the member's own setup guide:

- With single sign-on, the member signs in with the provider. When no mailr account exists for
  the address, the invite lets single sign-on create one, even with auto-create off, as long as
  the provider verified that same address (core/users.user_for_oidc_claims). mailr never creates
  or changes accounts at the provider.
- With password sign-in, the member chooses a password on the invite page. That makes the
  account, or replaces the initial password of an account an admin added and nobody has signed
  in to yet. An account that has signed in already, or that single sign-on manages, keeps its
  password; the member signs in as usual and the invite then applies.

Accepting applies the defaults once, opens the setup guide at its start, and uses up the invite.
An invite lasts DEFAULT_DAYS days unless the admin picks another number (1 to MAX_DAYS), and an
admin can withdraw it.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.config import get_settings
from mailr.models import HouseholdInvite, User

DEFAULT_DAYS = 7
MAX_DAYS = 30
MAX_OPEN = 100
_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]+")
_TOKEN = re.compile(r"[A-Za-z0-9_\-]{20,100}")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    while (t := secrets.token_urlsafe(32)).startswith(APP_PREFIX):  # never mistaken for a phone form
        pass
    return t


def base_url() -> str:
    return get_settings().base_url.rstrip("/")


def join_url(token: str) -> str:
    """The link the member opens on a computer or a phone."""
    return f"{base_url()}/join/{token}"


APP_PREFIX = "app_"


def _app_form(hashed: str) -> str:
    """The phone form of an invite whose token hashes to `hashed` (see app_token)."""
    import base64
    import hmac

    key = hashlib.sha256(b"mailr-invite-app|" + (get_settings().secret_key or "").encode()).digest()
    mac = hmac.new(key, hashed.encode(), hashlib.sha256).digest()
    return APP_PREFIX + base64.urlsafe_b64encode(mac).decode().rstrip("=")


def app_token(token: str) -> str:
    """The invite as the phone link carries it. Another app on the phone can register the
    custom scheme and read the link, so the link never holds the real token. The phone form works
    only together with a sign-in to the invited address (single sign-on in the app, or accepting
    while signed in); it never opens the signed-out password path of the invite page."""
    return _app_form(token_hash(token))


def is_app_token(token: str | None) -> bool:
    return bool(token) and token.startswith(APP_PREFIX) and bool(_TOKEN.fullmatch(token))


async def by_app_token(db: AsyncSession, token: str, email: str, *, lock: bool = False) -> HouseholdInvite | None:
    """The open invite for `email` whose phone form is `token`; the caller has proved `email` by a
    sign-in. There is at most one open invite per address (create withdraws the older ones)."""
    import hmac

    if not is_app_token(token) or not email:
        return None
    q = select(HouseholdInvite).where(
        func.lower(HouseholdInvite.email) == email.strip().lower(), HouseholdInvite.used_at.is_(None),
        HouseholdInvite.revoked_at.is_(None)).order_by(HouseholdInvite.id.desc())
    if lock:
        q = q.with_for_update()
    for inv in (await db.execute(q)).scalars():
        if hmac.compare_digest(_app_form(inv.token_hash), token):
            return inv
    return None


def app_url(token: str) -> str:
    """The mailr Android app's setup link: the server filled in, the invite's phone form carried to
    sign-in (app_token; the app asks before it uses the server)."""
    return f"mailr://setup?server={quote(base_url(), safe='')}&invite={app_token(token)}"


def state(inv: HouseholdInvite, now: datetime | None = None) -> str:
    """ok | used | revoked | expired."""
    now = now or datetime.now(UTC)
    if inv.revoked_at is not None:
        return "revoked"
    if inv.used_at is not None:
        return "used"
    if inv.expires_at <= now:
        return "expired"
    return "ok"


def out(inv: HouseholdInvite, *, made_by: str | None = None) -> dict[str, Any]:
    """An invite as Admin → Users lists it (never the token)."""
    return {"id": inv.id, "email": inv.email, "name": inv.name, "defaults": inv.defaults or {},
            "state": state(inv), "created_at": inv.created_at.isoformat() if inv.created_at else None,
            "expires_at": inv.expires_at.isoformat(), "used_at": inv.used_at.isoformat() if inv.used_at else None,
            "used_by": inv.used_by, "revoked_at": inv.revoked_at.isoformat() if inv.revoked_at else None,
            "created_by": made_by}


def clean(email: str, name: str, days: int | None, defaults: dict[str, Any] | None) -> tuple[str, str, int, dict[str, Any]]:
    """Validate what the admin typed. ValueError with a message for the form."""
    from mailr.core import onboarding

    email = (email or "").strip().lower()
    if not _EMAIL.fullmatch(email):
        raise ValueError("that is not an email address")
    name = (name or "").strip()[:200]
    days = DEFAULT_DAYS if days is None else int(days)
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"an invite lasts 1 to {MAX_DAYS} days")
    d = onboarding.clean_preset({"match": {"email": email}, "defaults": defaults or {}})["defaults"]
    return email, name, days, d


def invite_mail(inv: HouseholdInvite, url: str, admin: User | None = None) -> dict[str, str]:
    """The subject and text of the mail that carries an invite link, for the admin's mail app
    (the web's "Send by email"). In the language the invite's defaults set for the new account,
    else the admin's."""
    from mailr import i18n

    d = inv.defaults or {}
    wanted = d.get("language") or (d.get("settings") or {}).get("language") if isinstance(d, dict) else None
    lang = i18n.match(wanted) or (i18n.user_language(admin) if admin is not None else i18n.DEFAULT)
    # The day the link ends, in the admin's zone (the dialog shows the same day).
    from mailr.calendar.tz import user_zone_from

    ends = inv.expires_at.astimezone(user_zone_from(admin)) if admin is not None and inv.expires_at else inv.expires_at
    body = (i18n.t("invites.mail.body_until", lang, url=url, date=i18n.day_text(ends.date(), lang))
            if ends is not None else i18n.t("invites.mail.body", lang, url=url, name=inv.name or ""))
    return {"subject": i18n.t("invites.mail.subject", lang), "body": body}


async def create(db: AsyncSession, admin: User, email: str, name: str = "", days: int | None = None,
                 defaults: dict[str, Any] | None = None) -> tuple[HouseholdInvite, str]:
    """A new invite and its token (shown once). An open invite for the same address is withdrawn,
    so only the newest link works."""
    email, name, days, d = clean(email, name, days, defaults)
    now = datetime.now(UTC)
    open_ = (await db.execute(select(func.count()).select_from(HouseholdInvite).where(
        HouseholdInvite.used_at.is_(None), HouseholdInvite.revoked_at.is_(None),
        HouseholdInvite.expires_at > now))).scalar_one()
    if open_ >= MAX_OPEN:
        raise ValueError(f"at most {MAX_OPEN} open invites; withdraw some first")
    for old in (await db.execute(select(HouseholdInvite).where(
            func.lower(HouseholdInvite.email) == email, HouseholdInvite.used_at.is_(None),
            HouseholdInvite.revoked_at.is_(None)))).scalars():
        old.revoked_at = now
    token = new_token()
    inv = HouseholdInvite(token_hash=token_hash(token), email=email, name=name, defaults=d,
                          created_by=admin.id, expires_at=now + timedelta(days=days))
    db.add(inv)
    await db.flush()
    return inv, token


async def by_token(db: AsyncSession, token: str, *, lock: bool = False) -> HouseholdInvite | None:
    if not token or not _TOKEN.fullmatch(token):
        return None
    q = select(HouseholdInvite).where(HouseholdInvite.token_hash == token_hash(token))
    if lock:
        q = q.with_for_update()
    return (await db.execute(q)).scalar_one_or_none()


async def valid_for_email(db: AsyncSession, token: str | None, email: str) -> HouseholdInvite | None:
    """The open invite behind `token` when it is for `email` (single sign-on may then create the account)."""
    if not token or not email:
        return None
    inv = await (by_app_token(db, token, email) if is_app_token(token) else by_token(db, token))
    if inv is None or state(inv) != "ok" or inv.email.lower() != email.strip().lower():
        return None
    return inv


async def account_for(db: AsyncSession, inv: HouseholdInvite) -> User | None:
    return (await db.execute(select(User).where(func.lower(User.email) == inv.email.lower()))).scalar_one_or_none()


def may_set_password(user: User | None) -> bool:
    """Can the invite page set this account's password? Only with password sign-in on, and only
    for an account that does not exist yet or that nobody has signed in to and single sign-on does
    not manage (the initial password an admin typed)."""
    s = get_settings()
    if not s.local_login:
        return False
    if user is None:
        return True
    return not user.oidc_sub and user.last_login_at is None and not user.disabled


def token_from_next(next_: str | None) -> str | None:
    """The invite token in a sign-in's return path (/join/<token>)."""
    m = re.fullmatch(r"/join/([A-Za-z0-9_\-]{20,100})/?", next_ or "")
    return m.group(1) if m else None


async def accept(db: AsyncSession, inv: HouseholdInvite, user: User) -> dict[str, Any]:
    """Use the invite for `user`: the name (when the account has none of its own), the defaults
    once, and the setup guide from its start. The caller checked the address and commits."""
    from mailr.core import onboarding

    now = datetime.now(UTC)
    inv.used_at, inv.used_by = now, user.id
    if inv.name and (not user.name or user.name == user.email.split("@")[0]):
        user.name = inv.name
    st = onboarding.state(user)
    if not st or st.get("status") in ("pending", "dismissed", None):
        settings = dict(user.settings or {})
        settings[onboarding.KEY] = {**st, "status": "pending", "step": "welcome", "steps": st.get("steps") or {},
                                    "started_at": st.get("started_at"), "finished_at": None,
                                    # The invite's defaults stand in for a preset (none: a preset may still apply).
                                    **({"preset_checked": True} if inv.defaults else {})}
        user.settings = settings
        if inv.defaults:
            onboarding.apply_defaults(user, inv.defaults, f"invite:{inv.id}")
    return {"next": "/?setup=1", "applied": bool(inv.defaults)}
