"""Sign-in (local + OIDC), the /me endpoints, sessions."""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.api import contract as C
from mailr.api.schemas import identity_out, user_out
from mailr.config import get_settings, server_version
from mailr.core.auth import (
    SESSION_COOKIE,
    Principal,
    create_session,
    current,
    dummy_hash,
    hash_password,
    optional_principal,
    run_hashing,
    scope,
    verify_password,
)
from mailr.core.backup import store_mode_available
from mailr.core.users import create_user, sign_out_mail_apps
from mailr.db import get_db, get_existing
from mailr.i18n import ApiError
from mailr.models import Identity, Session, User

log = logging.getLogger("mailr.auth")
router = APIRouter(tags=["auth"])

DEFAULT_SETTINGS = {
    "inbox_type": "default",
    "priority_sections": ["important", "unread", "everything"],
    # Spam (Settings → Mail → Spam & Screener).
    "spam_classifier": "junk",
    "spam_junk_score": 15.0,
    "spam_upstream_unknown_threshold": 4.0,
    "reading_pane": "none",
    "density": "default",
    "theme": "system",
    "conversation_view": True,
    "undo_send_seconds": 10,
    "categories": ["primary", "promotions", "social", "updates", "forums"],
    "importance_markers": True,
    "keyboard_shortcuts": True,
    # Remote images (core/imageproxy.py): "always" loads them through mailr's image proxy, which
    # fetches them when mail arrives so senders cannot see when a message is opened; "ask" shows
    # a bar first; "never" keeps them off. Spam always asks.
    "images": "always",
    "send_and_archive": False,
    "default_reply": "reply",
    "page_size": 50,
    # What notifies, on every device (core/notifyprefs.py, Settings → Notifications).
    "mail_notifications": "important",
    "notify_approvals": True,
    "notify_assistant": True,
    "notify_accounts": True,
    "auto_advance": "list",
    # Where an assistant brief sits in a conversation: above the messages, or beside them.
    "brief_position": "top",
    # Short reply suggestions under a personal email (intel/routes.py reply_suggestions).
    "reply_suggestions": True,
    # VIP senders (lower-case addresses): their mail is always important, so it notifies at once
    # (intel/tasks.py); `is:vip` finds it.
    "vip": [],
    # Archive (core/actions.py): also remove the label of the list archived from, and these labels.
    "archive_removes_current_label": True,
    "archive_also_removes": [],
    # Look up sender pictures (BIMI / Gravatar / Libravatar) on the server. Off by default:
    # a lookup tells a third party whom you correspond with.
    "sender_pictures": False,
    # The scheme the label colour picker opens on (POST /labels/colours changes it and the labels).
    "label_scheme": "default",
    # Favorites in the mail navigation of every theme: view keys in the user's
    # order ("inbox", "sent", "label:<id>"). A deleted label drops out on the client.
    "nav_favorites": ["inbox", "sent", "drafts"],
    # Label learning (intel/classify.py): recent labels count more; a label's weight halves every
    # half_life_months (6, 12 or 24). A change queues a retrain of the label models.
    "label_recency": {"enabled": True, "half_life_months": 12},
    # The number by Inbox in the navigation (GET /inbox/count, ACT settings.inbox_count @api): the
    # unread mail of the active layout's first tab ("first_tab") or of the whole Inbox ("all").
    "inbox_count": "first_tab",
    # Settings → Ambrose → Models → "Learn from my choices" (intel/classify.py): on, labels you
    # give teach mailr to suggest and apply them; how sure it must be before it applies one by
    # itself is auto_label_precision with auto_label_min_examples (three levels in the app).
    "auto_label": True,
    "auto_label_precision": 0.9,
    "auto_label_min_examples": 30,
    # Show the advanced parts of Settings (Agents & API, detailed filters, learning detail, the
    # decisions lab). Admins always see them.
    "advanced_settings": False,
    # Routines that deliver a digest end it with today's meetings and links to their prep packs
    # (calendar/meeting_prep.py, event.prep). Off by default.
    "meeting_prep_digest": False,
    # Addresses of the user's that another assistant handles (core/trusted.py): mail only to them is
    # never acted on by a standing order unless the order names the address. Empty by default.
    "handled_elsewhere": [],
    # The user's standing rules for a bulk review (agent/review.py prompt; Settings → Ambrose → Sweep):
    # plain text, at most 2000 characters, in every review's prompt before the run's own instructions.
    "sweep_rules": "",
    # The sender screener (core/screener.py): first-time senders wait in Mail → Screener. Off by
    # default; PUT /screener/settings changes it and the accounts it covers (screener_accounts,
    # {connector id: bool}; an account not listed follows screener.default_screened).
    "screener": False,
    "screener_accounts": {},
    # ACT mail.account_folders @api
    # Settings → Mail → Inbox → "Show each account separately": with two or more mail accounts, the
    # navigation of every theme gets a group per account with its own Inbox, Sent, Drafts,
    # Scheduled, Spam and Trash (core/mailaccounts.py). Off: one unified mailbox.
    "account_folders": False,
    # The account groups stay short (core/accountpins.py): each account's pinned folders in order
    # ({"<account id>": [label id, …]}), the accounts whose "More folders" is open, and whether a
    # folder with unread mail shows above "More folders" until it is read.
    # ACT mail.account_folder_pin @api
    # ACT mail.account_folder_unpin @api
    # ACT mail.account_folders_more @api
    # ACT mail.account_folders_show_unread @api
    "account_folder_pins": {},
    "account_folders_more_open": [],
    "account_folders_show_unread": False,
    # The one-line hint in the navigation that offers "Show each account separately" to a user with
    # two or more mail accounts: true once dismissed (or once the setting was changed), so it never
    # comes back.
    "account_folders_hint_dismissed": False,
}

# A favorite is a system view, a category or a label view.
_FAVORITE_KEY = re.compile(r"[a-z]{2,20}|category:[a-z]{2,20}|label:\d{1,20}")


def clean_nav_favorites(value: Any) -> list[str]:
    # ACT nav.favorite_add @api
    # ACT nav.favorite_remove @api
    # ACT nav.favorite_reorder @api
    """Validate and de-duplicate `nav_favorites` (ACT nav.favorite_add, ACT nav.favorite_remove,
    ACT nav.favorite_reorder: the whole ordered list is saved on each change)."""
    if not isinstance(value, list) or len(value) > 50 or not all(isinstance(v, str) and _FAVORITE_KEY.fullmatch(v) for v in value):
        raise ValueError("nav_favorites must be a list of view keys (inbox, sent, label:<id>, …)")
    return list(dict.fromkeys(value))


# The colours (frontend/src/lib/colour/choice.ts): {preset, seed, harmony}.
# Before 8 Oct 2026 `ui_accent` was a bare Cards accent; such a string is saved in the new shape.
_ACCENT_PRESET = re.compile(r"[a-z]{2,12}\.[a-z]{2,16}")
_ACCENT_SEED = re.compile(r"#[0-9a-fA-F]{6}")
_ACCENT_HARMONIES = ("single", "complementary", "analogous", "triadic", "split")
_LEGACY_CARDS = {"violet": "#6750a4", "teal": "#006a6a", "rose": "#984061", "green": "#3b6a1f", "blue": "#00629e"}


def clean_ui_accent(value: Any) -> dict | None:
    # ACT settings.colours_preset @api
    # ACT settings.colours_custom @api
    """Validate `ui_accent` (ACT settings.colours_preset, ACT settings.colours_custom). None means
    the theme's own colours."""
    if value is None:
        return None
    if isinstance(value, str) and value in _LEGACY_CARDS:
        return {"preset": f"cards.{value}", "seed": _LEGACY_CARDS[value], "harmony": "single"}
    if not isinstance(value, dict) or set(value) - {"preset", "seed", "harmony"}:
        raise ValueError("ui_accent must be null or {preset, seed, harmony}")
    out: dict = {}
    preset, seed, harmony = value.get("preset"), value.get("seed"), value.get("harmony")
    if preset is not None:
        if not isinstance(preset, str) or not _ACCENT_PRESET.fullmatch(preset):
            raise ValueError("ui_accent.preset must be a preset id such as cards.teal")
        out["preset"] = preset
    if seed is not None:
        if not isinstance(seed, str) or not _ACCENT_SEED.fullmatch(seed):
            raise ValueError("ui_accent.seed must be a colour such as #1f6c86")
        out["seed"] = seed.lower()
    if harmony is not None:
        if harmony not in _ACCENT_HARMONIES:
            raise ValueError(f"ui_accent.harmony must be one of {', '.join(_ACCENT_HARMONIES)}")
        out["harmony"] = harmony
    if "preset" not in out and "seed" not in out:
        raise ValueError("ui_accent needs a preset or a seed")
    return out


SIGNIN_COOKIE = "mailr_signin"
SIGNIN_DAYS = 400


def _signin_serializer() -> URLSafeTimedSerializer:
    s = get_settings()
    return URLSafeTimedSerializer(s.session_secret or s.secret_key, salt="mailr-signin-client")


def signin_stamp(u: User) -> str:
    """What a sign-in cookie is bound to besides the account: its password and its sign-in epoch.
    A password change (by the user, an administrator or the command line) and Sign out everywhere
    (`revoke_signin_cookies`) change it, so every earlier known-client cookie stops counting.
   Keyed by the server secret: the readable cookie never tells anything."""
    import hashlib
    import hmac

    s = get_settings()
    epoch = int(u.signin_epoch or 0)
    msg = f"{u.id}|{u.password_hash or ''}|{epoch}".encode()
    return hmac.new((s.session_secret or s.secret_key).encode(), msg, hashlib.sha256).hexdigest()[:24]


def revoke_signin_cookies(u: User) -> None:
    """Every known-client cookie of the account stops counting (Sign out everywhere, resets)."""
    u.signin_epoch = int(u.signin_epoch or 0) + 1


def _cookie(resp: Response, raw: str, user: User | None = None) -> None:
    """The session cookie; with `user`, also the sign-in cookie that marks this browser as one
    the account signed in from (password sign-in throttling). It holds no secret of the
    session: a signed account id, a random client id and the account's sign-in stamp, sent
    to the sign-in routes only."""
    s = get_settings()
    resp.set_cookie(
        SESSION_COOKIE, raw, max_age=s.session_days * 86400, httponly=True, samesite="lax",
        secure=s.base_url.startswith("https://"), path="/",
    )
    if user is not None:
        val = _signin_serializer().dumps({"u": int(user.id), "c": secrets.token_urlsafe(12), "s": signin_stamp(user)})
        resp.set_cookie(SIGNIN_COOKIE, val, max_age=SIGNIN_DAYS * 86400, httponly=True, samesite="lax",
                        secure=s.base_url.startswith("https://"), path="/api/v1/auth")


async def known_client(db: AsyncSession, request: Request, u: User | None, ip: str) -> str | None:
    """A key for this client when the account signed in from it before: a browser with the
    account's sign-in cookie, or an IP that holds a live session of the account. None otherwise.
    The session lookup runs for unknown addresses too, so its time tells nothing."""
    uid = u.id if u is not None else 0
    live = (await db.execute(select(Session.id).where(Session.user_id == uid, Session.ip == (ip or "-"),
                                                      Session.expires_at > datetime.now(UTC)).limit(1))).first()
    if u is None:
        return None
    raw = request.cookies.get(SIGNIN_COOKIE)
    if raw:
        try:
            data = _signin_serializer().loads(raw, max_age=SIGNIN_DAYS * 86400)
        except BadSignature:
            data = None
        if (isinstance(data, dict) and data.get("u") == u.id and isinstance(data.get("c"), str)
                and isinstance(data.get("s"), str) and secrets.compare_digest(data["s"], signin_stamp(u))):
            return "cookie:" + data["c"][:40]
    return "ip:" + ip if live is not None and ip else None


def _serializer() -> URLSafeTimedSerializer:
    s = get_settings()
    return URLSafeTimedSerializer(s.session_secret or s.secret_key, salt="mailr-oidc")


def _apk_url() -> str:
    from mailr.api.onboarding_routes import apk_url

    return apk_url()


@router.get("/auth/config")
async def auth_config():
    s = get_settings()
    return {"local_login": s.local_login, "oidc": bool(s.oidc_issuer and s.oidc_client_id),
            "oidc_name": _oidc_name(), "signup": s.allow_signup,
            # The setup guide's Get the app page (/get-app) links phones here.
            "android_apk_url": _apk_url()}


def _oidc_name() -> str:
    """MAILR_OIDC_NAME, or empty: the clients then say "single sign-on"."""
    s = get_settings()
    return s.oidc_name.strip()[:60] if s.oidc_issuer else ""


class LoginIn(BaseModel):
    email: str
    password: str


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.post("/auth/login")
async def login(body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Password sign-in. Failed attempts are limited per IP across addresses, per IP and address, and per address from any IP. Every failure costs
    one Argon2 verification, whether or not the address has an account, so timing does not tell
    which addresses exist."""
    from mailr.core import throttle

    if not get_settings().local_login:
        raise ApiError(403, "auth.local_login_disabled")
    ip = _ip(request)
    email = body.email.strip().lower()
    u = (await db.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
    checked, counted = throttle.login_buckets(ip, email, await known_client(db, request, u, ip))
    # The attempt counts before the password is checked, so parallel guesses cannot all pass the
    # limits; a successful sign-in takes its count back.
    taken = await throttle.attempt(db, checked, counted)
    usable = u is not None and not u.disabled and bool(u.password_hash)
    ok = await run_hashing(verify_password, body.password, u.password_hash if usable and u is not None else dummy_hash())
    if not (usable and ok):
        raise ApiError(401, "auth.wrong_credentials")
    await throttle.forgive(db, taken)
    raw, s = await create_session(db, u, request.headers.get("user-agent", ""), ip,
                                  device=request.headers.get("x-mailr-device", "web"))
    _cookie(response, raw, u)
    return {"user": user_out(u), "csrf": s.csrf, "session_token": raw if request.headers.get("x-mailr-device") else None}


class SignupIn(BaseModel):
    email: str
    password: str
    name: str = ""


@router.post("/auth/signup")
async def signup(body: SignupIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Self-service sign-up (off unless MAILR_ALLOW_SIGNUP). Nobody proves they own the address,
    so the account is marked unverified and single sign-on never links to it by email. Sign-ups
    are limited per IP."""
    from mailr.core import throttle

    if not get_settings().allow_signup or not get_settings().local_login:
        raise ApiError(403, "auth.signup_disabled")
    buckets = [(throttle.SIGNUP_PER_IP, (_ip(request),))]
    await throttle.attempt(db, buckets, buckets, "too many sign-ups from this address; try again later")
    if len(body.password) < 10:
        raise ApiError(400, "auth.password_too_short", min=10)
    u = await create_user(db, body.email, body.name, password=body.password, email_verified=False)
    from mailr.core import onboarding

    onboarding.mark_new(u)
    await db.commit()
    raw, s = await create_session(db, u, request.headers.get("user-agent", ""), _ip(request))
    _cookie(response, raw, u)
    return {"user": user_out(u), "csrf": s.csrf}


@router.post("/auth/logout")
async def logout(request: Request, response: Response, p: Principal = Depends(current),
                 db: AsyncSession = Depends(get_db)):
    if p.session:
        await db.execute(delete(Session).where(Session.id == p.session.id))
        await db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    # Attachments, inline images and pictures are cached privately by the browser; on a shared
    # computer they must not outlive the session.
    response.headers["Clear-Site-Data"] = '"cache"'
    return {"ok": True}


# ---------- OIDC (authorization code + PKCE) ----------

_DISCOVERY: dict[str, dict] = {}


async def _discovery() -> dict:
    s = get_settings()
    if not s.oidc_issuer:
        raise ApiError(404, "auth.oidc_not_configured")
    if s.oidc_issuer not in _DISCOVERY:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(s.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration")
            r.raise_for_status()
            _DISCOVERY[s.oidc_issuer] = r.json()
    return _DISCOVERY[s.oidc_issuer]


def _canonical_mismatch(request: Request) -> bool:
    """True when the browser reached mailr through another name than MAILR_BASE_URL (a LAN
    alias such as http://mail.home.arpa). The sign-in cookie must be set on the host the identity
    provider returns to, so sign-in restarts on the canonical address."""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    return bool(host) and host != (httpx.URL(get_settings().base_url).host or "").lower()


@router.get("/auth/oidc/start")
async def oidc_start(request: Request, next: str = "/"):
    s = get_settings()
    if _canonical_mismatch(request):
        return RedirectResponse(s.base_url.rstrip("/") + "/api/v1/auth/oidc/start?" + urlencode({"next": next}))
    d = await _discovery()
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state_raw = secrets.token_urlsafe(16)
    nonce = secrets.token_urlsafe(16)
    if not next.startswith("/") or next.startswith("//"):
        next = "/"
    cookie = _serializer().dumps({"s": state_raw, "v": verifier, "n": nonce, "next": next})
    params = {
        "response_type": "code", "client_id": s.oidc_client_id,
        "redirect_uri": s.base_url.rstrip("/") + "/api/v1/auth/oidc/callback",
        "scope": s.oidc_scopes, "state": state_raw, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    resp = RedirectResponse(d["authorization_endpoint"] + "?" + urlencode(params))
    resp.set_cookie("mailr_oidc", cookie, max_age=600, httponly=True, samesite="lax",
                    secure=s.base_url.startswith("https://"), path="/api/v1/auth/oidc")
    return resp


@router.get("/auth/oidc/callback")
async def oidc_callback(request: Request, code: str = "", state: str = "", error: str = "",
                        db: AsyncSession = Depends(get_db)):
    """Browser-facing: any failure lands back on the sign-in page with a readable message."""
    try:
        return await _oidc_callback(request, code, state, error, db)
    except HTTPException as e:
        log.info("OIDC sign-in failed: %s", e.detail)
        resp = RedirectResponse("/login?" + urlencode({"error": f"Single sign-on failed: {e.detail}"}))
        resp.delete_cookie("mailr_oidc", path="/api/v1/auth/oidc")
        return resp


async def _oidc_callback(request: Request, code: str, state: str, error: str, db: AsyncSession):
    from authlib.jose import JsonWebKey, jwt

    s = get_settings()
    if error:
        # The provider's error code comes from the redirect URL (attacker-controllable): log it,
        # show a fixed message.
        log.info("OIDC provider returned error=%r", error[:200])
        raise ApiError(400, "auth.provider_refused")
    try:
        data = _serializer().loads(request.cookies.get("mailr_oidc", ""), max_age=600)
    except BadSignature:
        raise ApiError(400, "auth.signin_expired") from None
    if not secrets.compare_digest(data["s"], state):
        raise ApiError(400, "auth.state_mismatch")
    d = await _discovery()
    async with httpx.AsyncClient(timeout=15) as c:
        tr = await c.post(
            d["token_endpoint"],
            data={"grant_type": "authorization_code", "code": code,
                  "redirect_uri": s.base_url.rstrip("/") + "/api/v1/auth/oidc/callback",
                  "client_id": s.oidc_client_id, "code_verifier": data["v"]},
            auth=(s.oidc_client_id, s.oidc_client_secret) if s.oidc_client_secret else None,
        )
        if tr.status_code >= 400:
            log.warning("OIDC token exchange failed: %s %s", tr.status_code, tr.text[:300])
            raise ApiError(400, "auth.provider_not_accepted")
        tokens = tr.json()
        jwks = (await c.get(d["jwks_uri"])).json()
    try:
        claims = jwt.decode(tokens["id_token"], JsonWebKey.import_key_set(jwks))
        claims.validate()
    except Exception as e:  # noqa: BLE001 - any malformed or foreign token is a refusal, never a 500
        log.info("OIDC ID token rejected: %s", type(e).__name__)
        raise ApiError(400, "auth.token_unverifiable") from None
    if claims.get("iss") != d.get("issuer") or s.oidc_client_id not in (
        claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
    ):
        raise ApiError(400, "auth.token_wrong_app")
    if claims.get("nonce") != data["n"]:
        raise ApiError(400, "auth.nonce_mismatch")
    from mailr.core.invites import token_from_next
    from mailr.core.users import user_for_oidc_claims

    # A sign-in that started on a household invite page may create the invited account.
    u = await user_for_oidc_claims(db, dict(claims), invite_token=token_from_next(data.get("next")))
    await db.commit()
    raw, _ = await create_session(db, u, request.headers.get("user-agent", ""),
                                  request.client.host if request.client else "")
    resp = RedirectResponse(data.get("next") or "/", background=picture_refresh(u.id, dict(claims), tokens.get("access_token")))
    _cookie(resp, raw, u)
    resp.delete_cookie("mailr_oidc", path="/api/v1/auth/oidc")
    return resp


def picture_refresh(user_id: int, claims: dict, access_token: str | None = None):
    """After the sign-in response: bring the account picture in line with the provider's
    `picture` claim (core/account_picture.py). The access token, when the provider needs it for
    the picture, is used during this request only and never stored."""
    from starlette.background import BackgroundTask

    from mailr.core.account_picture import refresh_from_idp

    token = access_token if isinstance(access_token, str) and access_token else None
    return BackgroundTask(refresh_from_idp, user_id, claims, token)


# ---------- me ----------


@router.get("/me", response_model=C.account.Me, **C.TYPED)
async def me(request: Request, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token and not p.has("read"):
        # A token without `read` learns who it belongs to and what it may do, nothing more.
        return {"user": {"id": p.user.id, "name": p.user.name, "email": p.user.email}, "csrf": None,
                "scopes": sorted(p.scopes), "token": {"name": p.token.name, "local_only": p.token.local_only}}
    from mailr.calendar import tz as tzmod

    # The browser's zone and clock, the user's while Region and Time format are Automatic (calendar/tz.py);
    # a signed-in device's only, never a token's.
    if not p.is_token:
        await tzmod.note_device_zone(db, p.user, request.headers.get(tzmod.ZONE_HEADER))
        await tzmod.note_device_clock(db, p.user, request.headers.get(tzmod.CLOCK_HEADER))
    # Committed even when nothing changed: a note that found its value already written by a request
    # alongside (the page asks twice as it starts) still holds the user's row, and the counts below
    # wait for that request's counts, whose account index rebuild writes the row.
    await db.commit()
    ids = (await db.execute(select(Identity).where(Identity.user_id == p.user_id).order_by(Identity.id))).scalars()
    return {
        "user": user_out(p.user),
        "csrf": p.session.csrf if p.session else None,
        "settings": _zone_safe({**DEFAULT_SETTINGS, **(p.user.settings or {}), **_notify_of(p.user.settings)}),
        "identities": [identity_out(i) for i in ids],
        "scopes": sorted(p.scopes),
        "counts": await _counts(db, p),
        "token": {"name": p.token.name, "local_only": p.token.local_only} if p.token else None,
        # What this server offers the apps beyond the basics (agent/transcribe.py; store mode
        # needs the daily mail export, MAILR_MAIL_EXPORT, unless MAILR_STORE_BACKUP_CHECK is off;
        # store_backup_check false: deletion at the provider is not checked against a backup).
        "features": {"transcribe": await _transcribe_available(db, p.user), "store_mode": store_mode_available(),
                     "store_backup_check": get_settings().store_backup_check},
        # The account picture (core/account_picture.py): has, source (upload | idp), version,
        # idp_available. Identities without their own picture show it.
        "picture": _picture_info(p.user_id),
        # What the user's access policy allows (core/access.py): switches, allowed models, message.
        "access": await _access_info(db, p.user),
        # Whether the account is set up, so a phone can say "finish setting up on the web".
        "setup": await _setup_info(db, p.user),
        # Shown in the web's help menu and the phone's Settings, for bug reports.
        "version": server_version(),
    }


async def _setup_info(db: AsyncSession, user: User) -> dict[str, Any]:
    """has_accounts: a mail account is connected; guide: the setup guide's status (core/onboarding.py);
    calendar: a calendar account is visible (built-in or connected); url: where to finish on the web."""
    from mailr.api.onboarding_routes import _has_accounts
    from mailr.calendar import mirror
    from mailr.core import onboarding

    return {"has_accounts": await _has_accounts(db, user.id), "guide": onboarding.public(user)["status"],
            "calendar": bool(await mirror.visible_connectors(db, user.id)),
            "url": get_settings().base_url.rstrip("/") + "/setup"}


async def _access_info(db: AsyncSession, user: User) -> dict[str, Any]:
    from mailr.core import access
    from mailr.models import ModelEndpoint

    eps = list((await db.execute(select(ModelEndpoint).order_by(ModelEndpoint.id))).scalars())
    from mailr.knowledge import profile

    return (await access.for_user(db, user, eps)).as_dict(eps, await profile.name_of(db, user.id))


def _picture_info(uid: int) -> dict[str, Any]:
    from mailr.core.account_picture import info

    return info(uid)


async def _transcribe_available(db: AsyncSession, user: User) -> bool:
    from mailr.agent.transcribe import available

    return await available(db, user)


async def _counts(db: AsyncSession, p: Principal) -> dict[str, int]:
    """The sidebar numbers GET /me has always carried (web before GET /counts, Android 0.5.x):
    scheduled, approvals, failed, snoozed, drafts, waiting. Each is now the length of the list it
    opens (core/sidebar.py); a draft in Trash no longer counts as a draft."""
    from mailr.core import sidebar

    return sidebar.legacy_me_counts(await sidebar.sidebar_counts(db, p))


@router.get("/auth/session")
async def session_probe(request: Request, p: Principal | None = Depends(optional_principal),
                        db: AsyncSession = Depends(get_db)):
    """Like GET /me, but answers 200 {"authenticated": false} when signed out, so the web
    client can probe without an error in the browser console."""
    if p is None:
        return {"authenticated": False}
    return {"authenticated": True, **(await me(request, p, db))}


# Calendar and date settings the web and Android apps read (section 11):
# each is checked here; null removes the key (the client default applies again).
_BOOL_KEYS = ("calendar_dim_past", "calendar_week_numbers", "calendar_conflicts", "calendar_share_free_busy")
CALENDAR_KEYS = ("calendar_default_duration", "calendar_snap", "calendar_working_hours", *_BOOL_KEYS,
                 "calendar_video_template", "morning_hour", "timezone", "week_start", "time_format", "calendar_view")
# The view Calendar opens on (the last one chosen; Today by default).
CALENDAR_VIEWS = ("today", "day", "ndays", "workweek", "week", "month", "schedule")
_WEEKDAYS = ("sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday")


def _hhmm(v: Any) -> str:
    import re as _re

    m = _re.fullmatch(r"(\d{1,2}):(\d{2})", str(v or "").strip())
    if not m or int(m[1]) > 24 or int(m[2]) > 59 or (int(m[1]) == 24 and int(m[2])):
        raise ValueError("working hours take times as HH:MM")
    return f"{int(m[1]):02d}:{m[2]}"


def _zone_safe(settings: dict[str, Any]) -> dict[str, Any]:
    """The settings as sent: `timezone` is an IANA key or absent (an old stored Windows name maps)."""
    if settings.get("timezone"):
        from mailr.calendar import tz as tzmod

        name = tzmod.iana(str(settings["timezone"]))
        if name:
            settings["timezone"] = name
        else:
            settings.pop("timezone")
    return settings


def clean_calendar_settings(body: dict[str, Any]) -> dict[str, Any]:
    """Check and normalise the calendar and date keys of a settings change (ValueError)."""
    out = dict(body)
    for k in ("timezone", "week_start", "time_format"):
        if out.get(k) == "":
            out[k] = None  # "" clears as null does

    def need_int(k: str, lo: int, hi: int) -> None:
        v = out[k]
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ValueError(f"{k} must be a whole number from {lo} to {hi}")

    if out.get("calendar_default_duration") is not None:
        need_int("calendar_default_duration", 5, 1440)
    if out.get("calendar_snap") is not None:
        need_int("calendar_snap", 1, 60)
    if out.get("morning_hour") is not None:
        need_int("morning_hour", 0, 23)
    for k in _BOOL_KEYS:
        if out.get(k) is not None and not isinstance(out[k], bool):
            raise ValueError(f"{k} must be true or false")
    if out.get("calendar_working_hours") is not None:
        wh = out["calendar_working_hours"]
        if not isinstance(wh, dict):
            raise ValueError("calendar_working_hours must be {start, end, days}")
        start, end = _hhmm(wh.get("start", "09:00")), _hhmm(wh.get("end", "17:00"))
        if end <= start:
            raise ValueError("working hours must end after they start")
        days = wh.get("days", [1, 2, 3, 4, 5])
        if not isinstance(days, list) or not days or any(isinstance(d, bool) or not isinstance(d, int)
                                                         or not 0 <= d <= 7 for d in days):
            raise ValueError("working days are ISO weekdays, 1 (Monday) to 7 (Sunday)")
        out["calendar_working_hours"] = {"start": start, "end": end,
                                         "days": sorted({7 if d == 0 else d for d in days})}
    if out.get("calendar_video_template") is not None:
        v = out["calendar_video_template"]
        if not isinstance(v, str) or len(v) > 500 or (v and not v.startswith(("https://", "http://"))):
            raise ValueError("calendar_video_template must be an http(s) link ({id} becomes a room name)")
    if out.get("timezone") is not None:
        from mailr.calendar import tz as tzmod

        if not isinstance(out["timezone"], str) or tzmod.zone(out["timezone"]) is None:
            raise ValueError("timezone must be an IANA zone such as Europe/London")
        out["timezone"] = tzmod.iana(out["timezone"])  # stored as the IANA key ("Central Standard Time" maps)
    if out.get("week_start") is not None:
        v = out["week_start"]
        ok = (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 6) or (
            isinstance(v, str) and v.lower() in _WEEKDAYS)
        if not ok:
            raise ValueError("week_start must be 0 (Sunday) to 6 (Saturday) or a weekday name")
        if isinstance(v, str):
            out["week_start"] = v.lower()
    if out.get("calendar_view") is not None and out["calendar_view"] not in CALENDAR_VIEWS:
        raise ValueError(f"calendar_view must be one of {', '.join(CALENDAR_VIEWS)}")
    if out.get("time_format") is not None and out["time_format"] not in ("12h", "24h", "auto"):
        raise ValueError("time_format must be 12h, 24h or auto")
    # Tables.
    if out.get("date_order") is not None and out["date_order"] not in ("mdy", "dmy"):
        raise ValueError("date_order must be mdy or dmy")
    if out.get("currency") is not None:
        import re as _re

        if not isinstance(out["currency"], str) or not _re.fullmatch(r"[A-Za-z]{3}", out["currency"]):
            raise ValueError("currency must be an ISO 4217 code such as USD")
        out["currency"] = out["currency"].upper()
    # The web app's language (Settings → You → Region). Null follows the browser.
    if out.get("language") == "":
        out["language"] = None
    if out.get("language") is not None:
        import re as _re

        if not isinstance(out["language"], str) or not _re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?", out["language"]):
            raise ValueError("language must be a language tag such as en or pt-BR")
    return out


# Settings that have their own route, which checks them: PATCH /me/settings refuses them.
ROUTED_SETTINGS = {
    "inbox_layouts": "save layouts with PUT /layouts (they are validated there)",
    "onboarding": "the setup guide's progress is saved with PUT /onboarding",
    "screener": "change the Screener with PUT /screener/settings (turning it off releases held mail)",
    "screener_accounts": "change the Screener with PUT /screener/settings (turning it off releases held mail)",
    "local_models_only": "set Local models only for me with PUT /me/local-models (the rule for everyone on the server can lock it)",
    "label_scheme": "set the label colour scheme with POST /labels/colours (it recolours the labels too)",
    "models": "choose a model for a task with PUT /me/models/{task}",
}
# The keys PATCH /me/settings writes: the documented settings a person chooses (Settings, the
# Android app, the assistant's update_settings). Every other key of users.settings is kept by the
# server (learned spam senders, the device's zone and clock, reminders, calendar bookkeeping, task
# tags, notices) or has its own route above, and a client never sets it.
CLIENT_SETTINGS = frozenset({
    *DEFAULT_SETTINGS, *CALENDAR_KEYS, "date_order", "currency", "language", "vacation",
    "calendar_add_new_invites", "llm_triage", "card_bundles", "multiple_inboxes", "reading_pane_by_theme",
    "side_panel", "signature_placement", "spam_suspect_score", "ui_accent", "ui_theme", "tasks",
}) - set(ROUTED_SETTINGS)


@router.patch("/me/settings")
async def patch_settings(body: dict, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "settings.user_only")
    import json as _json

    from mailr.core import notifyprefs

    if len(_json.dumps(body)) > 32_000:
        raise HTTPException(400, "invalid settings")  # i18n: internal
    if "working_hours" in body:
        # The older name of calendar_working_hours, which earlier web builds also sent.
        body = {k: v for k, v in body.items() if k != "working_hours"} | (
            {} if "calendar_working_hours" in body else {"calendar_working_hours": body["working_hours"]})
    for k in body:
        if k in ROUTED_SETTINGS:
            raise HTTPException(400, ROUTED_SETTINGS[k])  # i18n: internal
        if k not in CLIENT_SETTINGS and k != notifyprefs.LEGACY:
            raise HTTPException(400, f"{k[:60]!r} is not a setting that can be changed here")  # i18n: internal
    if "vacation" in body:
        from mailr.core.vacation import clean as clean_vacation

        try:
            body = {**body, "vacation": clean_vacation(body["vacation"] or {})}
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    if "nav_favorites" in body:
        try:
            body = {**body, "nav_favorites": clean_nav_favorites(body["nav_favorites"])}
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    try:
        from mailr.core import accountpins

        body = await accountpins.clean(db, p, body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if "handled_elsewhere" in body:
        from mailr.core.trusted import clean_elsewhere

        try:
            body = {**body, "handled_elsewhere": clean_elsewhere(body["handled_elsewhere"])}
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    if "sweep_rules" in body:  # ACT settings.sweep_rules @api
        from mailr.agent.config_tools import clean_sweep_rules

        try:
            body = {**body, "sweep_rules": clean_sweep_rules(body["sweep_rules"])}
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    if "ui_accent" in body:
        try:
            body = {**body, "ui_accent": clean_ui_accent(body["ui_accent"])}
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    if "label_recency" in body:
        from mailr.intel.classify import clean_recency

        try:
            body = {**body, "label_recency": clean_recency(body["label_recency"])}
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    if "tasks" in body:  # Settings → Calendar → Tasks: {"keep_completed_copy": bool} (calendar/todo.py)
        v = body["tasks"]
        if not isinstance(v, dict) or not isinstance(v.get("keep_completed_copy", True), bool):
            raise HTTPException(400, "tasks takes {\"keep_completed_copy\": true or false}")  # i18n: internal
        body = {**body, "tasks": {k: v[k] for k in ("keep_completed_copy",) if k in v}}
    for k in ("auto_label", "advanced_settings", "meeting_prep_digest", "account_folders", "account_folders_hint_dismissed"):
        if k in body and not isinstance(body[k], bool):
            raise HTTPException(400, f"{k} must be true or false")  # i18n: internal
    if "auto_label_precision" in body:
        v = body["auto_label_precision"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.5 <= v <= 0.99:
            raise HTTPException(400, "auto_label_precision must be a number from 0.5 to 0.99")  # i18n: internal
    if "auto_label_min_examples" in body:
        v = body["auto_label_min_examples"]
        if isinstance(v, bool) or not isinstance(v, int) or not 3 <= v <= 1000:
            raise HTTPException(400, "auto_label_min_examples must be a whole number from 3 to 1000")  # i18n: internal
    if notifyprefs.LEGACY in body:  # an older app's name for the mail level
        legacy = body.pop(notifyprefs.LEGACY)
        body.setdefault("mail_notifications", legacy)
    if "mail_notifications" in body and body["mail_notifications"] not in notifyprefs.MAIL_LEVELS:
        raise HTTPException(400, "mail_notifications must be off, vip, important, primary or all")  # i18n: internal
    for k in ("notify_approvals", "notify_assistant", "notify_accounts"):
        if k in body and not isinstance(body[k], bool):
            raise HTTPException(400, f"{k} must be true or false")  # i18n: internal
    if "images" in body and body["images"] not in ("always", "ask", "never"):
        raise HTTPException(400, "images must be always, ask or never")  # i18n: internal
    if "inbox_count" in body and body["inbox_count"] not in ("first_tab", "all"):
        raise HTTPException(400, "inbox_count must be first_tab or all")  # i18n: internal
    if "vip" in body:
        import re as _re

        vip = body["vip"] if isinstance(body["vip"], list) else None
        if vip is None or len(vip) > 500 or not all(isinstance(a, str) and _re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,255}", a.strip())
                                                   for a in vip):
            raise HTTPException(400, "vip must be a list of email addresses")  # i18n: internal
        body = {**body, "vip": list(dict.fromkeys(a.strip().lower() for a in vip))}
    try:
        body = clean_calendar_settings(body)
        if "language" in body:  # Settings → You → Region; null is Automatic
            from mailr import i18n

            body = {**body, "language": i18n.clean_language(body["language"])}
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    # null clears a key and the client default applies again (Undo of a first change sends it).
    cleared = {k for k in (*CALENDAR_KEYS, "date_order", "currency", "language") if k in body and body[k] is None}
    # The row is locked until the commit: two saves at once (a settings page saves on every change)
    # would otherwise both merge into the same old settings, and the later write would drop the
    # other's keys.
    u = await db.get(User, p.user_id, with_for_update=True, populate_existing=True)
    recency_before = {**DEFAULT_SETTINGS, **(u.settings or {})}["label_recency"]
    tz_before = ((u.settings if u is not None else None) or {}).get("timezone")
    u.settings = {k: v for k, v in {**(u.settings or {}), **body}.items() if k not in cleared}
    if "label_recency" in body and body["label_recency"] != recency_before:
        # Retrain the label models with the new weights (Settings → Ambrose → Models).
        from mailr.core.jobs import enqueue

        await enqueue(db, "intel.train_all", {"user_id": p.user_id}, user_id=p.user_id,
                      dedupe_key=f"intel.train_all:{p.user_id}")
    # Date-only reminders fire at that hour, in that zone; the reminder texts follow the clock.
    if "morning_hour" in body or "timezone" in body or "time_format" in body:
        from mailr.calendar import planner

        await db.flush()
        await planner.replan_user(db, p.user_id)
    if "timezone" in body and body["timezone"] != tz_before:
        from mailr.calendar.tz import zone_changed

        await zone_changed(db, p.user_id)
    await db.commit()
    return {**DEFAULT_SETTINGS, **u.settings, **_notify_of(u.settings)}


def _notify_of(settings: dict | None) -> dict:
    """The notification settings, an older `desktop_notifications` read as the mail level."""
    from mailr.core import notifyprefs

    return notifyprefs.of(settings)


class ProfileIn(BaseModel):
    name: str


@router.patch("/me")
async def patch_me(body: ProfileIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "auth.profile_user_only")
    u = await db.get(User, p.user_id)
    u.name = body.name.strip()[:200]
    await db.commit()
    from mailr.calendar import builtin

    await builtin.user_renamed(u)  # the store account's description (shown to calendar sharees)
    return user_out(u)


class PasswordIn(BaseModel):
    current: str = ""
    new: str
    # Also stop the IMAP password and the calendar apps' app passwords.
    # On unless the user unticks it.
    sign_out_mail_apps: bool = True


async def revoke_tokens(db: AsyncSession, user_id: int) -> int:
    """Revoke every live API token of a user (password change and administrator reset). Returns
    how many were revoked."""
    from sqlalchemy import update

    from mailr.models import ApiToken

    res = await db.execute(update(ApiToken).where(ApiToken.user_id == user_id, ApiToken.revoked.is_(False))
                           .values(revoked=True).returning(ApiToken.id))
    return len(res.all())


async def drop_push_targets(db: AsyncSession, user_id: int, keep_session: str | None = None) -> int:
    """Remove a user's push targets (ntfy topics, UnifiedPush endpoints, browsers' Web Push) on
    recovery: all of them, or all but those of the session that stays signed in. A target ends with
    its session anyway (PushTarget.session_id); this also takes targets older than that link."""
    from sqlalchemy import or_

    from mailr.models import PushTarget

    cond = [PushTarget.user_id == user_id]
    if keep_session:
        cond.append(or_(PushTarget.session_id.is_(None), PushTarget.session_id != keep_session))
    res = await db.execute(delete(PushTarget).where(*cond).returning(PushTarget.id))
    return len(res.all())


# ACT settings.password_change @api
@router.post("/me/password")
async def change_password(body: PasswordIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "auth.password_user_only")
    if not get_settings().local_login:
        # With password sign-in off, a password is useless today and a backdoor the day it is
        # switched on: do not let a session (or anything riding one) plant one.
        raise ApiError(403, "auth.password_signin_off")
    u = await get_existing(db, User, p.user_id)
    if not u.password_hash:
        raise ApiError(403, "auth.signs_in_with_sso")
    if not await run_hashing(verify_password, body.current, u.password_hash):
        raise ApiError(400, "auth.current_password_wrong")
    if len(body.new) < 10:
        raise ApiError(400, "auth.password_too_short", min=10)
    u.password_hash = await run_hashing(hash_password, body.new)
    revoke_signin_cookies(u)  # the new password changes the stamp too; this says it
    ended = (await db.execute(delete(Session).where(Session.user_id == u.id,
                                                    Session.id != (p.session.id if p.session else ""))
                              .returning(Session.created_at))).scalars().all()
    # A new password is the usual recovery step after someone else had the account, and a session
    # can mint API tokens: every token is revoked too (create new ones afterwards).
    revoked = await revoke_tokens(db, u.id)
    # Push targets another device registered would keep receiving notifications.
    await drop_push_targets(db, p.user_id, getattr(p.session, "id", None))
    # The store's own credentials too: an IMAP password or a DAV app password minted from a stolen
    # session would otherwise keep reading mail and calendars after the recovery.
    apps = await sign_out_mail_apps(u) if body.sign_out_mail_apps else {}
    # What could keep acting for the user afterwards (core/recovery.py), for them to review.
    from mailr.core import recovery

    review = await recovery.reset_channels(db, u, recovery.window_start(list(ended)))
    await db.commit()
    return {"ok": True, "revoked_tokens": revoked, **apps, "review": review}


# ACT settings.sign_out_everywhere @api
@router.post("/me/sign-out-everywhere")
async def sign_out_everywhere(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Recovery without a password change (single sign-on users have none): every other session
    ends, every API token is revoked, other devices' push targets are removed, and the mail and
    calendar apps are signed out (the IMAP password replaced, the DAV app passwords deleted). This
    device stays signed in."""
    if p.is_token:
        raise ApiError(403, "auth.revoke_user_only")
    u = await get_existing(db, User, p.user_id)
    res = await db.execute(delete(Session).where(Session.user_id == u.id,
                                                 Session.id != (p.session.id if p.session else ""))
                           .returning(Session.created_at))
    started = list(res.scalars().all())
    revoked = await revoke_tokens(db, u.id)
    await drop_push_targets(db, p.user_id, getattr(p.session, "id", None))
    revoke_signin_cookies(u)  # a former intruder's browser is no longer a known client
    apps = await sign_out_mail_apps(u)
    # What could keep acting for the user afterwards (core/recovery.py), for them to review.
    from mailr.core import recovery

    review = await recovery.reset_channels(db, u, recovery.window_start(started))
    await db.commit()
    return {"ok": True, "ended_sessions": len(started), "revoked_tokens": revoked, **apps, "review": review}


# ACT settings.sign_out_everywhere @api (dismissing what it took back; core/recovery.py)
@router.delete("/me/recovery-review")
async def dismiss_recovery_review(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Dismiss "Review after signing out": the list of what a recovery took back."""
    if p.is_token:
        raise ApiError(403, "auth.revoke_user_only")
    from mailr.core import recovery, usersettings

    await usersettings.patch(db, p.user_id, remove=[recovery.KEY])
    await db.commit()
    return {"ok": True}


@router.get("/me/sessions")
async def sessions(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "auth.sessions_user_only")
    rows = (await db.execute(select(Session).where(Session.user_id == p.user_id).order_by(Session.last_seen_at.desc()))).scalars()
    cur = p.session.id if p.session else None
    return [{"id": s.id[:16], "device": s.device, "user_agent": s.user_agent, "ip": s.ip,
             "created_at": s.created_at.isoformat(), "last_seen_at": s.last_seen_at.isoformat(),
             "current": s.id == cur} for s in rows]


@router.delete("/me/sessions/{sid}")
async def revoke_session(sid: str, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "auth.revoke_user_only")
    # The list shows the first 16 hex characters of each session id; accept exactly that form
    # (a bare prefix like "" or "%" must not match every session).
    if not re.fullmatch(r"[0-9a-f]{16}", sid or ""):
        raise ApiError(400, "auth.unknown_session")
    await db.execute(delete(Session).where(Session.user_id == p.user_id, func.substr(Session.id, 1, 16) == sid))
    await db.commit()
    return {"ok": True}


# ---------- IMAP access for phone and desktop clients ----------


@router.get("/me/imap")
async def imap_info(p: Principal = Depends(scope("read"))):
    s = get_settings()
    return {"host": s.imap_hostname, "port": s.imaps_port, "security": "SSL/TLS",
            "starttls_port": s.imap_port, "username": p.user.stalwart_login,
            "note": "Create an app password below; your Mailr password does not work for IMAP."}


@router.post("/me/imap/app-password")
async def imap_app_password(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    from mailr.core.users import imap_password

    if p.is_token:
        raise ApiError(403, "auth.imap_password_user_only")
    u = await db.get(User, p.user_id)
    pw = await imap_password(db, u)
    return {"username": u.stalwart_login, "password": pw,
            "note": "Shown once. Creating a new one replaces this password on every device."}
