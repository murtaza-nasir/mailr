"""Connect the instance's Speakr at sign-in (speakr.connect; Admin → Integrations → Speakr).

An admin enters one Speakr address (instance setting `speakr_instance`). When it is set with
`auto_connect`, each single sign-on to mailr's web app connects that Speakr for the person, with
the same exchange as the "Connect with" button (sso.connect_with), after the sign-in response:
the sign-in never waits for Speakr, and a Speakr that cannot be reached never blocks it. Design:
docs/design/speakr-auto-connect.md.

The provider's access token is handed to the task in memory only, as for the account picture
(api/auth_routes.picture_refresh); it is never stored, logged or queued.

Nothing is tried when the person already has a connector for that address, removed one before
(state `declined`, until they connect it again themselves), or the last try failed recently: 7
days for a lasting reason, 1 hour for a passing one. The state is kept on the user
(`users.speakr_auto`) and shown in their Speakr settings.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.config import get_settings
from mailr.integrations.speakr import client as sc
from mailr.integrations.speakr import store
from mailr.models import Connector, InstanceSetting, User

log = logging.getLogger("mailr.speakr.auto")

KEY = "speakr_instance"
# Reasons only the admin can fix: tried again after a week (or at once with the button). Every
# other failure is tried again after an hour, including `registration_closed` (no Speakr account
# yet), which the person fixes by signing in to Speakr once.
LASTING = ("not_supported", "not_allowed", "refused_by_policy", "scopes")
LASTING_SECONDS = 7 * 24 * 3600
PASSING_SECONDS = 3600


def normalize(value: Any) -> dict[str, Any] | None:
    """The stored setting, checked; None when it is missing or has no address."""
    if not isinstance(value, dict):
        return None
    url = sc.normalize_base(str(value.get("url") or ""))
    if not url.lower().startswith(("http://", "https://")):
        return None
    return {"url": url, "auto_connect": value.get("auto_connect") is True,
            "data_class": "normal" if value.get("data_class") == "normal" else "local_only"}


async def instance(db: AsyncSession) -> dict[str, Any] | None:
    row = await db.get(InstanceSetting, KEY)
    return normalize(row.value if row else None)


def available() -> bool:
    s = get_settings()
    return bool(s.oidc_issuer and s.oidc_client_id)


def _state(user: User, url: str) -> dict[str, Any]:
    st = user.speakr_auto if isinstance(user.speakr_auto, dict) else {}
    return st if st.get("url") == url else {}


def due(state: dict[str, Any], now: float | None = None) -> bool:
    """Is a try due, given the person's state for this address?"""
    now = time.time() if now is None else now
    kind = state.get("state")
    if kind in ("declined", "connected"):
        return False
    if kind == "failed":
        wait = LASTING_SECONDS if state.get("reason") in LASTING else PASSING_SECONDS
        return now - float(state.get("at") or 0) >= wait
    return True


def _set(user: User, url: str, state: str, reason: str = "", seen: bool | None = None) -> None:
    old = _state(user, url)
    user.speakr_auto = {"url": url, "state": state, "reason": reason, "at": int(time.time()),
                        "seen": old.get("seen", False) if seen is None else seen}


async def _connected_to(db: AsyncSession, user_id: int, url: str) -> bool:
    rows = (await db.execute(select(Connector).where(Connector.user_id == user_id,
                                                     Connector.kind == store.KIND))).scalars()
    return any(store.base_url(c) == url for c in rows)


async def attempt(user_id: int, access_token: str | None) -> str | None:
    """One try for one person after a sign-in. Returns the result, or None when nothing was
    tried. Never raises."""
    from mailr.db import session_scope
    from mailr.integrations.speakr import sso

    if not isinstance(access_token, str) or not access_token or not available():
        return None
    try:
        async with session_scope() as db:
            inst = await instance(db)
            user = await db.get(User, user_id)
            if inst is None or not inst["auto_connect"] or user is None or not user.oidc_sub:
                return None
            url = inst["url"]
            if await _connected_to(db, user.id, url):
                return None
            if not due(_state(user, url)):
                return None
            result = await sso.connect_with(db, user, url, access_token, None, inst["data_class"], "auto")
            user = await db.get(User, user_id)
            if user is None:
                return result
            if result == "connected":
                _set(user, url, "connected", seen=False)
            elif result == "already_connected":
                _set(user, url, "connected", seen=True)
            else:
                _set(user, url, "failed", result)
            return result
    except Exception:  # a sign-in is never affected by Speakr
        log.exception("Speakr auto-connect failed")
        return None


def background(user_id: int, access_token: str | None):
    """The task to run after the sign-in response (api/auth_routes)."""
    from starlette.background import BackgroundTask

    token = access_token if isinstance(access_token, str) and access_token else None
    return BackgroundTask(attempt, user_id, token)


async def on_remove(db: AsyncSession, conn: Connector) -> None:
    """A person removed a connector: when it is the instance's Speakr, it is not connected again
    at sign-in until they connect it themselves."""
    inst = await instance(db)
    user = await db.get(User, conn.user_id)
    if inst is not None and user is not None and store.base_url(conn) == inst["url"]:
        _set(user, inst["url"], "declined", seen=True)


async def on_connect(db: AsyncSession, user_id: int, url: str) -> None:
    """A Speakr was connected (pasted token, the button or at sign-in): a decline of the
    instance's Speakr no longer holds."""
    inst = await instance(db)
    user = await db.get(User, user_id)
    if inst is not None and user is not None and sc.normalize_base(url) == inst["url"]:
        _set(user, inst["url"], "connected", seen=True)


async def status(db: AsyncSession, user: User) -> dict[str, Any]:
    """For the person's Speakr settings: the instance's address (to pre-fill the wizard), whether
    it is connected at sign-in, and the state of the last try."""
    inst = await instance(db)
    if inst is None:
        return {"url": None, "auto_connect": False, "state": None, "reason": None, "at": None, "notice": False}
    st = _state(user, inst["url"])
    auto = inst["auto_connect"] and available() and bool(user.oidc_sub)
    return {"url": inst["url"], "auto_connect": auto, "state": st.get("state"), "reason": st.get("reason") or None,
            "at": st.get("at"), "notice": st.get("state") == "connected" and st.get("seen") is False}


async def mark_seen(db: AsyncSession, user: User) -> None:
    inst = await instance(db)
    if inst is None:
        return
    st = _state(user, inst["url"])
    if st:
        user.speakr_auto = {**st, "seen": True}


async def check(url: str, private_ok: bool | None) -> dict[str, Any]:
    """Admin → Speakr → Check: what the Speakr at `url` says about the exchange, without a user
    token (GET /api/v1/sso/status, a newer Speakr). Mailr's web client id is sent so Speakr can
    say whether it is allowed."""
    from urllib.parse import urlencode

    base = sc.normalize_base(url)
    out: dict[str, Any] = {"reachable": False, "supported": False, "sso_token_exchange": False,
                           "sso_exchange_provisioning": False, "exchange_client_allowed": None}
    try:
        await sc.check_base_url(base, private_ok)
        r = await sc.SpeakrClient(base, "", bucket=f"sso:{base}", private_ok=private_ok)._raw(
            "GET", "/api/v1/sso/status?" + urlencode({"client_id": get_settings().oidc_client_id}))
    except sc.SpeakrError as e:
        out["detail"] = str(e)
        return out
    out["reachable"] = True
    try:
        d = r.json()
    except ValueError:
        d = None
    if r.status_code != 200 or not isinstance(d, dict):
        return out  # an older Speakr: no such route
    out["supported"] = True
    for k in ("sso_token_exchange", "sso_exchange_provisioning"):
        out[k] = d.get(k) is True
    allowed = d.get("exchange_client_allowed")
    out["exchange_client_allowed"] = allowed if isinstance(allowed, bool) else None
    return out
