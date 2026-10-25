"""`mailr check-config` and the start-up check.

`problems()` reads the environment and returns one short line per problem, each naming the
environment variable. It never prints or returns a value: a secret may sit in any of them.
"error" problems stop `mailr serve` and `mailr worker` before they start (cli._prepare_process);
"warning" problems are printed and the process starts.

`online_problems()` adds the checks that need the network (the database, the mail store and its
service password, and a reminder when MAILR_STORE_BACKUP_CHECK=false while a store-mode account
deletes mail at the provider). Only the command runs them; a start-up waits for neither.
"""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Problem:
    level: str  # error | warning
    var: str  # the environment variable, e.g. MAILR_BASE_URL
    message: str

    def line(self) -> str:
        return f"{self.level:<7} {self.var}: {self.message}"


def _env(field: str) -> str:
    return "MAILR_" + field.upper()


def _url_ok(value: str, schemes: tuple[str, ...] = ("http", "https")) -> bool:
    try:
        u = urlsplit(value)
    except ValueError:
        return False
    return u.scheme in schemes and bool(u.hostname)


def _networks_bad(raw: str) -> list[int]:
    """Positions (1-based) of entries that are neither an IP address nor a network."""
    bad = []
    for i, item in enumerate([x.strip() for x in (raw or "").split(",") if x.strip()], 1):
        if item == "*":
            continue
        try:
            ipaddress.ip_network(item, strict=False)
        except ValueError:
            bad.append(i)
    return bad


def _load() -> tuple[Any | None, list[Problem]]:
    """The settings, or the variables pydantic refused (their names only)."""
    from pydantic import ValidationError

    from mailr.config import Settings

    try:
        return Settings(), []
    except ValidationError as e:
        out = []
        for err in e.errors():
            field = str(err.get("loc", ["?"])[0])
            # pydantic's message never repeats the input; err["input"] is left out on purpose.
            msg = str(err.get("msg") or "not a valid value").rstrip(".")
            out.append(Problem(ERROR, _env(field), msg[:1].lower() + msg[1:] + "."))
        return None, out


def _secret_key(raw: str) -> Problem | None:
    from mailr.core.crypto import MIN_KEY_BYTES, _decode_key

    if not raw:
        return Problem(ERROR, "MAILR_SECRET_KEY", "not set. Generate one with `mailr gen-secret`.")
    key = _decode_key(raw)
    problem = None
    if key is None:
        problem = "is not base64"
    elif len(key) < MIN_KEY_BYTES:
        problem = f"is too short ({MIN_KEY_BYTES} random bytes are needed)"
    elif len(set(key)) < 12:
        problem = "is not random"
    if problem is None:
        return None
    weak_ok = os.environ.get("MAILR_ALLOW_WEAK_SECRET_KEY", "").strip().lower() in ("1", "true", "yes")
    return Problem(WARNING if weak_ok else ERROR, "MAILR_SECRET_KEY",
                   f"{problem}. Generate one with `mailr gen-secret`."
                   + (" Accepted because MAILR_ALLOW_WEAK_SECRET_KEY is set." if weak_ok else ""))


def _seed_endpoints(raw: str) -> Problem | None:
    if not raw.strip():
        return None
    var = "MAILR_SEED_MODEL_ENDPOINTS"
    try:
        data = json.loads(raw)
    except ValueError:
        return Problem(ERROR, var, "not valid JSON (a list of endpoints, see the admin docs, Models).")
    if not isinstance(data, list):
        return Problem(ERROR, var, "must be a JSON list of endpoints.")
    for i, ep in enumerate(data, 1):
        if not isinstance(ep, dict) or not ep.get("name") or not ep.get("base_url"):
            return Problem(ERROR, var, f"endpoint {i} needs a name and a base_url.")
        if not _url_ok(str(ep["base_url"])):
            return Problem(ERROR, var, f"endpoint {i}: base_url is not an http or https address.")
        if ep.get("locality", "local") not in ("local", "hosted"):
            return Problem(ERROR, var, f"endpoint {i}: locality must be local or hosted.")
    return None


def problems() -> list[Problem]:
    """Every problem found without the network, errors first."""
    s, out = _load()
    if s is None:
        return out

    if p := _secret_key(s.secret_key):
        out.append(p)

    # The public address.
    if not _url_ok(s.base_url):
        out.append(Problem(ERROR, "MAILR_BASE_URL", "not an http or https address, e.g. https://mail.example.com."))
    else:
        u = urlsplit(s.base_url)
        if u.path not in ("", "/"):
            out.append(Problem(ERROR, "MAILR_BASE_URL", "must be the address alone, with no path."))
        if not s.dev and (u.hostname or "") in ("localhost", "127.0.0.1", "::1"):
            out.append(Problem(WARNING, "MAILR_BASE_URL",
                               "still points at this machine (localhost). Links in mail and phone sign-in use "
                               "it; set the address people type."))
        if not s.dev and u.scheme == "http" and (u.hostname or "") not in ("localhost", "127.0.0.1", "::1"):
            out.append(Problem(WARNING, "MAILR_BASE_URL",
                               "uses http. Sign-in cookies then travel without encryption; put Mailr behind "
                               "an HTTPS proxy."))

    # Mail authentication: the store's default name is not trusted.
    if not s.dev and (s.imap_hostname or "").strip().lower() in ("", "localhost") and not s.trusted_authserv_ids:
        out.append(Problem(WARNING, "MAILR_IMAP_HOSTNAME",
                           "is still localhost and MAILR_TRUSTED_AUTHSERV_IDS is empty, so no Authentication-Results "
                           "header counts and no mail shows as verified. Set your mail server's name."))

    # The browser gate fronts the stack's own browser only.
    if (s.browser_gate or "").strip() and (s.browser_url or "").strip():
        bh = (urlsplit(s.browser_url).hostname or "").lower()
        if bh and bh != "browser":
            out.append(Problem(WARNING, "MAILR_BROWSER_GATE",
                               f"is set while MAILR_BROWSER_URL points at {bh}, a browser outside the stack. "
                               "Pages then fail to load; set MAILR_BROWSER_GATE= (empty)."))

    # Sign-in.
    if s.oidc_issuer and not _url_ok(s.oidc_issuer, ("https", "http")):
        out.append(Problem(ERROR, "MAILR_OIDC_ISSUER", "not an http or https address."))
    if s.oidc_issuer and not s.oidc_client_id:
        out.append(Problem(ERROR, "MAILR_OIDC_CLIENT_ID",
                           "empty while MAILR_OIDC_ISSUER is set. The single sign-on button stays hidden."))
    if s.oidc_client_id and not s.oidc_issuer:
        out.append(Problem(WARNING, "MAILR_OIDC_ISSUER",
                           "empty while MAILR_OIDC_CLIENT_ID is set. Single sign-on is off."))
    if not s.local_login and not (s.oidc_issuer and s.oidc_client_id):
        out.append(Problem(ERROR, "MAILR_LOCAL_LOGIN",
                           "is off and single sign-on is not set up, so nobody can sign in."))
    if s.oidc_auto_create_group and not s.oidc_auto_create:
        out.append(Problem(WARNING, "MAILR_OIDC_AUTO_CREATE_GROUP", "has no effect while MAILR_OIDC_AUTO_CREATE is off."))

    # The mail store.
    if not _url_ok(s.stalwart_url):
        out.append(Problem(ERROR, "MAILR_STALWART_URL", "not an http or https address."))
    if not s.stalwart_admin_password and not s.dev:
        out.append(Problem(ERROR, "MAILR_STALWART_ADMIN_PASSWORD",
                           "not set. Mailr cannot create mailboxes without the store's service password."))

    # Models.
    if p := _seed_endpoints(s.seed_model_endpoints or ""):
        out.append(p)

    # Network and proxies.
    if bad := _networks_bad(s.trusted_proxies or ""):
        out.append(Problem(ERROR, "MAILR_TRUSTED_PROXIES",
                           f"entry {', '.join(map(str, bad))} is not an IP address or network (comma-separated)."))
    if s.net_subnet and _networks_bad(s.net_subnet):
        out.append(Problem(WARNING, "MAILR_NET_SUBNET", "not a network like 10.253.54.0/24; only loopback is trusted."))
    if str(s.allow_private_hosts).strip().lower() not in ("admins", "all", "none", "true", "false", "1", "0",
                                                          "yes", "no", "on", "off"):
        out.append(Problem(ERROR, "MAILR_ALLOW_PRIVATE_HOSTS", "must be admins, all or none."))
    if s.log_level.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        out.append(Problem(ERROR, "MAILR_LOG_LEVEL", "must be DEBUG, INFO, WARNING, ERROR or CRITICAL."))

    # Push, monitors and other optional services: a malformed address only warns.
    for field in ("ntfy_url", "heartbeat_url", "backup_heartbeat_url", "asr_url", "image_proxy_egress"):
        v = getattr(s, field) or ""
        if v and not _url_ok(v):
            out.append(Problem(WARNING, _env(field), "not an http or https address; it is not used."))
    if s.android_min_version and not all(x.isdigit() for x in s.android_min_version.split(".")):
        out.append(Problem(WARNING, "MAILR_ANDROID_MIN_VERSION", "not a version like 0.5.0; the check is off."))

    # Storage.
    if s.backup_target:
        from mailr.core.backup import target_problem

        if problem := target_problem(s.backup_target):
            out.append(Problem(WARNING, "MAILR_BACKUP_TARGET",
                               f"{problem}. Mail is not copied there until it is fixed."))
    data = Path(s.data_dir)
    if data.exists() and not os.access(data, os.W_OK):
        out.append(Problem(ERROR, "MAILR_DATA_DIR", "exists but this process cannot write to it."))
    elif not data.exists() and not s.dev:
        out.append(Problem(WARNING, "MAILR_DATA_DIR", "does not exist yet; exports and the attachment cache need it."))

    order = {ERROR: 0, WARNING: 1}
    return sorted(out, key=lambda p: order.get(p.level, 2))


async def online_problems() -> list[Problem]:
    """The checks that need the network: the database answers, the store answers, and the store
    takes the service password."""
    import httpx
    from sqlalchemy import text

    from mailr.config import get_settings

    s = get_settings()
    out: list[Problem] = []
    try:
        from mailr.db import session_scope

        async with session_scope() as db:
            await db.execute(text("SELECT 1"))
            armed = 0 if s.store_backup_check else (await db.execute(text(
                "SELECT count(*) FROM connectors WHERE mode = 'store' AND delete_upstream AND enabled"))).scalar_one()
    except Exception:  # noqa: BLE001  the error text may hold the DSN
        out.append(Problem(ERROR, "MAILR_DATABASE_URL", "the database does not answer, or refuses the sign-in."))
        armed = 0
    if armed:
        # A reminder, not an error: the setting is allowed, and the backups are the admin's own.
        out.append(Problem(WARNING, "MAILR_STORE_BACKUP_CHECK",
                           f"is false, and deletion at the provider is turned on for {armed} "
                           f"account{'s' if armed != 1 else ''} where Mailr keeps the mail: mail there is deleted after the retention window "
                           "without a backup check. Make sure your own backups of the mail store work."))
    # A hosted error tracker gets the type of each error only (core/errors.py): say so.
    import asyncio

    from mailr.core import errors, outbound

    for var, dsn in (("MAILR_SENTRY_DSN", s.sentry_dsn), ("MAILR_SENTRY_BROWSER_DSN", s.sentry_browser_dsn)):
        if dsn and not outbound.local(await asyncio.to_thread(errors.dsn_service, dsn)):
            out.append(Problem(WARNING, var, "the error tracker's host is not on the local network, so reports "
                                             "keep only the type of each error, not its text."))
    base = s.stalwart_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            live = await c.get(base + "/healthz/live")
            if live.status_code >= 500:
                out.append(Problem(ERROR, "MAILR_STALWART_URL", f"the mail store answered {live.status_code}."))
            elif s.stalwart_admin_password:
                r = await c.get(base + "/.well-known/jmap", auth=(s.stalwart_admin_user, s.stalwart_admin_password))
                if r.status_code in (401, 403):
                    out.append(Problem(ERROR, "MAILR_STALWART_ADMIN_PASSWORD",
                                       "the mail store refuses this service user and password."))
    except httpx.HTTPError:
        out.append(Problem(ERROR, "MAILR_STALWART_URL", "the mail store does not answer."))
    return out


def report(found: list[Problem]) -> str:
    if not found:
        return "ok: no problems found in the configuration"
    return "\n".join(p.line() for p in found)


def fatal(found: list[Problem]) -> bool:
    return any(p.level == ERROR for p in found)
