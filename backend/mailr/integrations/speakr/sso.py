"""Connect Speakr with single sign-on (speakr.connect; Settings → Mail accounts → Speakr).

Mailr and Speakr can sign people in through the same identity provider. A newer Speakr (capability
`sso_token_exchange`) then trades the person's access token from that provider, once, for a
normal scoped Speakr API token (POST /api/v1/tokens/exchange). The token is stored exactly like a
pasted one, so sync, webhooks and the scope warnings work as before. Design:
docs/design/speakr-sso-connect.md.

1. POST /speakr/sso/start (a signed-in session, CSRF-checked) checks the address and returns a
   link to /auth/oidc/connect with a signed ticket: who asked, the Speakr address, the connector
   to renew (or none) and the data class. The ticket lasts two minutes.
2. /auth/oidc/connect (api/auth_routes.py) starts the usual sign-in with the provider on mailr's
   canonical address; the ticket goes into the sign-in cookie.
3. The sign-in callback sees the ticket and calls `finish` instead of opening a session: the ID
   token must belong to the same mailr account (its `sub`), then the access token is sent to that
   Speakr address only, in the exchange request, and is then dropped. It is never stored or logged.
4. The browser lands on the Speakr settings with `?speakr_sso=<result>`.

On a renewal the old token is revoked with itself (DELETE /api/v1/tokens/current) when Speakr has
the capability `token_self_revoke`, so tokens do not pile up against Speakr's per-account limit.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.config import get_settings
from mailr.core.netguard import private_ok_for
from mailr.integrations.speakr import client as sc
from mailr.integrations.speakr import store
from mailr.models import Connector, User

log = logging.getLogger("mailr.speakr.sso")

TICKET_SECONDS = 120
# What mailr's switches can use (connect.scope_notes). Speakr grants at most its own ceiling.
WANTED_SCOPES = ("read", "write", "upload", "share", "webhooks")
SETTINGS_PATH = "/settings/mail/accounts"

# Results named in ?speakr_sso= (the web app shows a sentence for each).
RESULTS = ("connected", "renewed", "renewed_old_kept", "not_supported", "refused", "not_allowed",
           "limit", "scopes", "rate_limited", "unreachable", "wrong_account", "already_connected",
           "expired", "failed", "link_required", "registration_closed", "refused_by_policy")

# `token_origin` in the connector's config: the token was made by the exchange ("sso" with the
# button, "auto" at sign-in), so mailr may revoke it when the account is removed. A pasted token
# has no origin and is never revoked: the user may use it elsewhere.
ORIGINS = ("sso", "auto")


def _serializer() -> URLSafeTimedSerializer:
    s = get_settings()
    return URLSafeTimedSerializer(s.session_secret or s.secret_key, salt="mailr-speakr-sso")


def sso_available() -> bool:
    s = get_settings()
    return bool(s.oidc_issuer and s.oidc_client_id)


async def start(db: AsyncSession, user: User, base_url: str | None, connector_id: int | None,
                data_class: str) -> dict[str, str]:
    """The link that starts single sign-on for Speakr (step 1)."""
    if not sso_available():
        raise HTTPException(400, "Single sign-on is not set up on this Mailr server")
    if not user.oidc_sub:
        raise HTTPException(400, "Your Mailr account does not sign in with single sign-on; paste a token instead")
    if connector_id is not None:
        c = await db.get(Connector, connector_id)
        if c is None or c.user_id != user.id or c.kind != store.KIND:
            raise HTTPException(404, "Speakr account not found")
        url = store.base_url(c)
    else:
        url = sc.normalize_base(str(base_url or ""))
        if not url.lower().startswith(("http://", "https://")):
            raise HTTPException(400, "The Speakr address must start with https:// (or http:// on a private network)")
        dup = (await db.execute(select(Connector).where(Connector.user_id == user.id,
                                                        Connector.kind == store.KIND))).scalars()
        if any(store.base_url(c) == url for c in dup):
            raise HTTPException(409, "This Speakr server is already connected")
    try:
        await sc.check_base_url(url, private_ok_for(user))
    except sc.SpeakrError as e:
        raise HTTPException(400, str(e)) from None
    ticket = _serializer().dumps({"u": int(user.id), "url": url, "cid": connector_id,
                                  "dc": "normal" if data_class == "normal" else "local_only"})
    return {"url": get_settings().base_url.rstrip("/") + "/api/v1/auth/oidc/connect?" + urlencode({"ticket": ticket})}


def read_ticket(ticket: str) -> dict[str, Any] | None:
    try:
        data = _serializer().loads(ticket, max_age=TICKET_SECONDS)
    except BadSignature:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("u"), int) else None


def result_url(result: str) -> str:
    return SETTINGS_PATH + "?" + urlencode({"speakr_sso": result}) + "#speakr"


def token_name() -> str:
    host = urlsplit(get_settings().base_url).hostname or ""
    return f"Mailr ({host})"[:100] if host else "Mailr"


class _Principal:
    """What connect.create needs from a principal."""

    def __init__(self, user: User):
        self.user = user
        self.user_id = user.id


async def finish(db: AsyncSession, data: dict[str, Any], claims: dict[str, Any], access_token: str | None) -> str:
    """Step 3: exchange, then store. Returns the address to send the browser to; never raises."""
    try:
        return result_url(await _finish(db, data, claims, access_token))
    except Exception:  # the browser always lands on the settings with a reason
        log.exception("Speakr single sign-on failed")
        await db.rollback()
        return result_url("failed")


async def _finish(db: AsyncSession, data: dict[str, Any], claims: dict[str, Any], access_token: str | None) -> str:
    user = await db.get(User, int(data.get("u") or 0))
    if user is None or not user.oidc_sub or claims.get("sub") != user.oidc_sub:
        # Someone else signed in at the provider: their token never reaches Speakr.
        return "wrong_account"
    if not isinstance(access_token, str) or not access_token:
        return "failed"
    url = str(data.get("url") or "")
    conn: Connector | None = None
    if data.get("cid") is not None:
        conn = await db.get(Connector, int(data["cid"]))
        if conn is None or conn.user_id != user.id or conn.kind != store.KIND or store.base_url(conn) != url:
            return "failed"
    return await connect_with(db, user, url, access_token, conn, str(data.get("dc") or "local_only"), "sso")


async def connect_with(db: AsyncSession, user: User, url: str, access_token: str, conn: Connector | None,
                       data_class: str, origin: str) -> str:
    """Exchange the provider's access token at `url` and store the new token: a new connector,
    or a new token for `conn`. Also used at sign-in (auto.py). Returns a result of RESULTS."""
    old = await store.token_of(db, conn) if conn is not None else ""
    try:
        token = await exchange(url, access_token, private_ok_for(user))
    except ExchangeError as e:
        return e.result
    from mailr.api.connector_routes import ConnectorDraft, ConnectorPatch
    from mailr.integrations.speakr import connect

    if conn is None:
        body = ConnectorDraft(kind=store.KIND, config={"base_url": url}, secrets={"token": token},
                              data_class=data_class, retention_days=None)
        try:
            row = await connect.create(db, _Principal(user), body)
        except HTTPException as e:
            await _revoke(url, token, private_ok_for(user))
            return "already_connected" if e.status_code == 409 else "failed"
        row.config = {**(row.config or {}), "token_origin": origin}
        await db.commit()
        await _instant_updates(db, row)
        return "connected"
    await connect.patch(db, _Principal(user), conn, ConnectorPatch(secrets={"token": token}, data_class=None, retention_days=None))
    conn.config = {**(conn.config or {}), "token_origin": origin}
    await db.commit()
    if old and old != token:
        # Capabilities as read with the new token (connect.patch); an older Speakr cannot revoke
        # a token with itself, and the user is told to revoke it there.
        if not sc.has_feature((conn.config or {}).get("speakr_capabilities"), "token_self_revoke") \
                or not await _revoke(url, old, private_ok_for(user)):
            return "renewed_old_kept"
    return "renewed"


async def _instant_updates(db: AsyncSession, conn: Connector) -> None:
    """Turn on instant updates (Speakr's webhook) for a new connector when its token allows it.
    Best effort: the ten-minute check covers a failure, and the user can turn them on later."""
    from mailr.integrations.speakr import webhook

    if "webhooks" not in (store.token_scopes(conn) or []) or webhook.path_for(conn) is not None:
        return
    if not webhook._base().lower().startswith("https://"):
        return  # Speakr sends webhooks only to https:// addresses
    try:
        await webhook.register(db, conn)
        await db.commit()
    except Exception:  # noqa: BLE001 - any failure leaves the ten-minute check in place
        await db.rollback()
        log.warning("Speakr instant updates could not be turned on for connector %s", conn.id)


def made_by_mailr(conn: Connector) -> bool:
    """Was the connector's token made by mailr's exchange? Marked since 0.40.45; a connector made
    with 0.40.44 is recognised by Speakr's own record (an exchanged token named "Mailr (…)")."""
    cfg = conn.config or {}
    if cfg.get("token_origin") in ORIGINS:
        return True
    tok = cfg.get("speakr_token")
    return isinstance(tok, dict) and tok.get("sso") is True and str(tok.get("name") or "").startswith("Mailr")


async def revoke_on_remove(db: AsyncSession, conn: Connector) -> bool:
    """Before a connector is removed: revoke its token in Speakr when mailr made it over single
    sign-on and Speakr can revoke a token with itself. A pasted token is left alone."""
    if not made_by_mailr(conn) or not store.feature(conn, "token_self_revoke"):
        return False
    token = await store.token_of(db, conn)
    return bool(token) and await _revoke(store.base_url(conn), token, private_ok_for(await db.get(User, conn.user_id)))


class ExchangeError(Exception):
    def __init__(self, result: str):
        super().__init__(result)
        self.result = result


async def exchange(url: str, access_token: str, private_ok: bool | None) -> str:
    """POST /api/v1/tokens/exchange with the provider's access token: the new Speakr token."""
    cl = sc.SpeakrClient(url, access_token, bucket=f"sso:{url}", private_ok=private_ok)
    scopes = list(WANTED_SCOPES)
    for _ in range(2):
        try:
            r = await cl._raw("POST", "/api/v1/tokens/exchange",
                              json={"name": token_name(), "scopes": scopes, "expires_in_days": 0})
        except sc.SpeakrRateLimited:
            raise ExchangeError("rate_limited") from None
        except sc.SpeakrError:
            raise ExchangeError("unreachable") from None
        body = _json(r)
        if r.status_code == 201 and isinstance(body.get("token"), str) and body["token"]:
            return body["token"]
        if r.status_code == 400 and isinstance(body.get("allowed_scopes"), list):
            # Speakr's ceiling is lower than what mailr asks for: take what it allows.
            allowed = {str(s) for s in body["allowed_scopes"]}
            fewer = [s for s in WANTED_SCOPES if s in allowed]
            if "read" in fewer and fewer != scopes:
                scopes = fewer
                continue
            raise ExchangeError("scopes")
        raise ExchangeError(_result_for(r.status_code, body))
    raise ExchangeError("scopes")


def _result_for(status: int, body: dict[str, Any]) -> str:
    code = body.get("code")
    if status == 401:
        return "refused"  # feature off, client not allowed, or (an older Speakr) no Speakr account yet
    if status == 403 and code == "sso_exchange_not_allowed":
        return "not_allowed"
    if status == 409 and code == "token_limit_reached":
        return "limit"
    # A newer Speakr says why it has no account for this person (the token itself was valid).
    if status == 403 and code == "sso_registration_closed":
        return "registration_closed"
    if status == 409 and code == "sso_link_required":
        return "link_required"
    if status == 403 and code in ("sso_domain_not_allowed", "sso_email_not_verified", "sso_email_missing"):
        return "refused_by_policy"
    if status == 502 and code == "sso_userinfo_failed":
        return "unreachable"
    if status == 429:
        return "rate_limited"
    if status in (404, 405) or 300 <= status < 400:
        return "not_supported"  # an older Speakr: no such route
    return "failed"


def _json(r: Any) -> dict[str, Any]:
    try:
        d = r.json()
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


async def _revoke(url: str, token: str, private_ok: bool | None) -> bool:
    """Revoke a token with itself (DELETE /api/v1/tokens/current, capability `token_self_revoke`).
    False when Speakr refuses or cannot be reached."""
    try:
        r = await sc.SpeakrClient(url, token, bucket=f"sso:{url}", private_ok=private_ok)._raw(
            "DELETE", "/api/v1/tokens/current")
    except sc.SpeakrError:
        return False
    return 200 <= r.status_code < 300
