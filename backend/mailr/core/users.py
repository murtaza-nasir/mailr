"""User lifecycle: create (with a Stalwart account), IMAP app passwords, delete, bootstrap."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.config import get_settings
from mailr.core.auth import hash_password
from mailr.core.crypto import (
    encrypt_for_user,
    encrypt_instance,
    model_endpoint_context,
    new_wrapped_data_key,
)
from mailr.core.jmap import CORE
from mailr.core.jobs import handler
from mailr.core.store import service_client, user_store
from mailr.db import session_scope
from mailr.i18n import ApiError
from mailr.models import InstanceSetting, ModelEndpoint, User

log = logging.getLogger("mailr.users")
MGMT = [CORE, "urn:stalwart:jmap"]


async def _mgmt(method: str, args: dict[str, Any]) -> dict[str, Any]:
    return await service_client().one(method, args, using=MGMT)


async def _domain_id(name: str | None = None) -> str:
    d = name or get_settings().stalwart_domain
    r = await _mgmt("x:Domain/query", {"filter": {"name": d}})
    if r["ids"]:
        return r["ids"][0]
    made = await _mgmt("x:Domain/set", {"create": {"d": {
        "name": d, "certificateManagement": {"@type": "Manual"},
        "dkimManagement": {"@type": "Manual"}, "dnsManagement": {"@type": "Manual"}}}})
    return made["created"]["d"]["id"]


def _local_part(email: str, uid: int) -> str:
    # A random suffix keeps names unique even when the database is restored or recreated
    # while the mail store keeps its old accounts.
    base = re.sub(r"[^a-z0-9]+", "-", email.split("@")[0].lower()).strip("-")[:30] or "user"
    return f"{base}-{uid}-{secrets.token_hex(3)}"


async def provision_store_account(db: AsyncSession, u: User) -> None:
    """Create the user's Stalwart account. Its password is only for the user's IMAP clients."""
    if u.stalwart_account_id:
        return
    from mailr.calendar.builtin import description_for

    local = _local_part(u.email, u.id)
    pw = secrets.token_urlsafe(24)
    r = await _mgmt("x:Account/set", {"create": {"a": {
        "@type": "User", "name": local, "domainId": await _domain_id(),
        # The display name: whoever can read one of the user's calendars sees it.
        "description": description_for(u),
        "credentials": {"0": {"@type": "Password", "secret": pw}}}}})
    created = (r.get("created") or {}).get("a")
    if not created:
        raise RuntimeError(f"could not create mail store account: {r.get('notCreated')}")
    u.stalwart_account_id = created["id"]
    u.stalwart_login = f"{local}@{get_settings().stalwart_domain}"
    u.stalwart_secret_enc = encrypt_for_user(u.id, u.data_key_wrapped, pw)
    await db.flush()
    await ensure_login_alias(u)
    await db.flush()
    store = await user_store(db, u)
    await store.sync_labels(db)


async def ensure_login_alias(u: User) -> bool:
    """Let the user sign in to IMAP with their own address (mail@example.com) instead of the
    store's internal account name. The address becomes an alias of their store account; the
    store is never an MX, so this only affects IMAP sign-in. Returns True when it changed."""
    if not u.stalwart_account_id or u.stalwart_login == u.email:
        return False
    local, _, domain = u.email.partition("@")
    if not local or not domain or domain == get_settings().stalwart_domain:
        return False
    try:
        dom = await _domain_id(domain)
        r = await _mgmt("x:Account/set", {"update": {u.stalwart_account_id: {
            "aliases": {"0": {"name": local, "domainId": dom}}}}})
    except Exception as e:  # noqa: BLE001  the internal login keeps working
        log.warning("could not add IMAP login alias %s: %s", u.email, e)
        return False
    if u.stalwart_account_id not in (r.get("updated") or {}):
        log.warning("could not add IMAP login alias %s: %s", u.email, r.get("notUpdated"))
        return False
    u.stalwart_login = u.email
    return True


async def create_user(db: AsyncSession, email: str, name: str = "", *, password: str | None = None,
                      oidc_sub: str | None = None, role: str = "user", email_verified: bool = True) -> User:
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ApiError(400, "common.invalid_email")
    if (await db.execute(select(User.id).where(func.lower(User.email) == email))).first():
        raise HTTPException(409, "a user with that email already exists")
    u = User(email=email, name=name or email.split("@")[0], role=role,
             password_hash=hash_password(password) if password else None, oidc_sub=oidc_sub, settings={},
             email_verified=email_verified)
    db.add(u)
    await db.flush()
    u.data_key_wrapped = new_wrapped_data_key(u.id)
    await provision_store_account(db, u)
    from mailr.calendar import builtin

    await builtin.ensure_for_user(db, u)
    from mailr.core import tally

    await tally.start_empty(db, u.id)
    await db.commit()
    return u


async def imap_password(db: AsyncSession, u: User) -> str:
    """Rotate and return a fresh password for the user's IMAP clients (shown once)."""
    pw = secrets.token_urlsafe(18)
    r = await _mgmt("x:Account/set", {"update": {u.stalwart_account_id: {"credentials/0/secret": pw}}})
    if u.stalwart_account_id not in (r.get("updated") or {}):
        raise RuntimeError(f"password change failed: {r.get('notUpdated')}")
    u.stalwart_secret_enc = encrypt_for_user(u.id, u.data_key_wrapped, pw)
    await db.commit()
    return pw


async def lock_store_signin(db: AsyncSession, u: User) -> bool:
    """A disabled user: their store password (IMAP, and DAV through the phone gateway) stops
    working. Stalwart 0.16.23 does not apply an expired password to HTTP sign-ins, and an account
    lock would also stop mailr's own access by impersonation, so mailr replaces the secret with a
    random one and keeps the real one (stalwart_secret_enc) for unlock_store_signin. App passwords
    are paused separately (calendar/phones.py).

    The store gets a new random secret even when the account is marked locked already: the call
    follows the account's state, so a lock that was never applied cannot be skipped. The mark
    (users.store_signin_locked) is server-owned and set only here. Returns True when the account
    was not marked locked before."""
    if not u.stalwart_account_id:
        return False
    r = await _mgmt("x:Account/set", {"update": {u.stalwart_account_id: {
        "credentials/0/secret": secrets.token_urlsafe(32)}}})
    if u.stalwart_account_id not in (r.get("updated") or {}):
        raise RuntimeError(f"could not lock the store sign-in: {r.get('notUpdated')}")
    was = bool(u.store_signin_locked)
    u.store_signin_locked = True
    await db.flush()
    return not was


async def unlock_store_signin(db: AsyncSession, u: User) -> bool:
    """Undo lock_store_signin: the password the user's devices know works again."""
    if not u.stalwart_account_id or not u.store_signin_locked or u.disabled:
        return False
    from mailr.core.crypto import decrypt_for_user

    pw = None
    try:
        pw = decrypt_for_user(u.id, u.data_key_wrapped, u.stalwart_secret_enc) if u.stalwart_secret_enc else None
    except Exception as e:  # noqa: BLE001  the user makes a new IMAP password instead
        log.warning("could not read the stored store password of user %s: %s", u.id, e)
    if pw:
        r = await _mgmt("x:Account/set", {"update": {u.stalwart_account_id: {"credentials/0/secret": pw}}})
        if u.stalwart_account_id not in (r.get("updated") or {}):
            raise RuntimeError(f"could not unlock the store sign-in: {r.get('notUpdated')}")
    u.store_signin_locked = False
    await db.flush()
    return True


async def sign_out_mail_apps(u: User) -> dict[str, Any]:
    """Account recovery (a password change, an admin's reset, "Sign out everywhere"):
    every mail and calendar app the user's store account let in stops working.

    - The IMAP password (POST /me/imap/app-password) gets a new random secret that nobody is shown.
      The user makes a new IMAP password for their mail apps afterwards. The store and the kept
      copy both get it, whatever the account is marked: for a disabled user the store held a
      random secret already (lock_store_signin), and enabling the user later restores one nobody
      knows. An enabled account is no longer marked locked.
    - Every DAV app password of the account (calendar/phones.py), made in mailr or elsewhere, is
      deleted; one the store refuses to delete is reported (`mail_apps_failed`).

    mailr's own access to the store is the service account (MAILR_STALWART_ADMIN_USER with
    MAILR_SVC_PASSWORD, acting for users by impersonation), never these secrets, so nothing in mailr
    stops working. The caller commits (stalwart_secret_enc). A store failure is logged and reported
    (`mail_apps_failed`); it never undoes the password change."""
    out: dict[str, Any] = {"imap_password_reset": False, "revoked_app_passwords": 0, "mail_apps_failed": False}
    if not u.stalwart_account_id:
        return out
    pw = secrets.token_urlsafe(32)
    try:
        r = await _mgmt("x:Account/set", {"update": {u.stalwart_account_id: {"credentials/0/secret": pw}}})
        if u.stalwart_account_id not in (r.get("updated") or {}):
            raise RuntimeError(f"not updated: {r.get('notUpdated')}")
        u.stalwart_secret_enc = encrypt_for_user(u.id, u.data_key_wrapped, pw)
        if not u.disabled:
            u.store_signin_locked = False
        out["imap_password_reset"] = True
    except Exception as e:  # noqa: BLE001  reported to the user; the new password still counts
        log.warning("could not replace the IMAP password of user %s: %s", u.id, e)
        out["mail_apps_failed"] = True
    try:
        from mailr.calendar import phones

        out["revoked_app_passwords"] = await phones.revoke_all(u.stalwart_account_id)
    except Exception as e:  # noqa: BLE001
        log.warning("could not delete the app passwords of user %s: %s", u.id, e)
        out["mail_apps_failed"] = True
    return out


async def store_signin_follow(db: AsyncSession) -> int:
    """At start: lock the store sign-in of disabled users (users disabled before this existed),
    unlock that of enabled ones. Writes only what differs."""
    n = 0
    rows = (await db.execute(select(User).where(User.stalwart_account_id.is_not(None)))).scalars().all()
    for u in rows:
        locked = bool(u.store_signin_locked)
        try:
            if u.disabled and not locked:
                n += await lock_store_signin(db, u)
            elif not u.disabled and locked:
                n += await unlock_store_signin(db, u)
        except Exception as e:  # noqa: BLE001  one account must not stop the others
            log.warning("could not change the store sign-in of user %s: %s", u.id, e)
    return n


async def delete_store_account(u: User) -> None:
    if u.stalwart_account_id:
        await _mgmt("x:Account/set", {"destroy": [u.stalwart_account_id]})


@handler("users.delete_store_account")
async def delete_store_account_job(payload: dict[str, Any], ctx) -> None:
    """A deleted user's mail store account, when the store could not be reached at the time.
    Never one that a user still has."""
    acct = payload.get("account_id")
    if not acct:
        return
    async with session_scope() as db:
        if (await db.execute(select(User.id).where(User.stalwart_account_id == acct).limit(1))).first():
            return
    await _mgmt("x:Account/set", {"destroy": [acct]})


async def _migrate() -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from mailr.core import dbdump

    # A dump first when the schema is behind (core/dbdump.py before_migrate).
    await dbdump.before_migrate()
    ini = Path(__file__).resolve().parents[2] / "alembic.ini"  # noqa: ASYNC240
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "migrations"))
    await asyncio.to_thread(command.upgrade, cfg, "heads")


async def _wait_for_store(timeout: float = 120) -> None:
    import httpx

    url = get_settings().stalwart_url.rstrip("/") + "/healthz/live"
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                if (await c.get(url)).status_code < 500:
                    return
        except httpx.HTTPError:
            pass
        if loop.time() > deadline:
            raise RuntimeError(f"mail store at {url} is not reachable")
        await asyncio.sleep(2)


async def bootstrap(run_migrations: bool = True, *, first_admin: bool = False) -> None:
    """Migrate, create the first admin, provision pending store accounts. The API and the worker
    both call this at start; a Postgres advisory lock makes them take turns.

    Only the API passes first_admin=True. The worker and the cli container run without
    MAILR_BOOTSTRAP_ADMIN_PASSWORD, and when the worker took the lock first on a fresh install it
    created the first admin with no password, so nobody could sign in (10 Oct 2026)."""
    import asyncpg

    dsn = get_settings().database_url.replace("postgresql+asyncpg", "postgresql")
    for attempt in range(30):
        try:
            lock_conn = await asyncpg.connect(dsn)
            break
        except (OSError, asyncpg.PostgresError):
            if attempt == 29:
                raise
            await asyncio.sleep(2)
    try:
        # Poll, never block: a session blocked in pg_advisory_lock is a running statement, and a
        # migration's CREATE INDEX CONCURRENTLY waits for every running statement to finish, so
        # the waiter and the migrator deadlocked (4 Oct 2026: the app hung at startup).
        while not await lock_conn.fetchval("SELECT pg_try_advisory_lock(727001)"):  # noqa: ASYNC110
            await asyncio.sleep(1)
        await _bootstrap_locked(first_admin=first_admin)
    finally:
        await lock_conn.close()


async def ensure_stalwart_resolver() -> bool:
    """Point Stalwart's DNS at the stack's recursive resolver (for blocklist lookups).
    Returns True when the setting changed; Stalwart applies it on its next restart."""
    ip = get_settings().stalwart_resolver_ip.strip()
    if not ip:
        return False
    cur = await _mgmt("x:DnsResolver/get", {"ids": ["singleton"]})
    obj = (cur.get("list") or [{}])[0]
    servers = [v.get("address") for v in (obj.get("servers") or {}).values()]
    if obj.get("@type") == "Custom" and servers == [ip]:
        return False
    await _mgmt("x:DnsResolver/set", {"update": {"singleton": {"@type": "Custom", "servers": {"0": {"address": ip}}}}})
    log.warning("Stalwart DNS resolver set to %s; restart the stalwart container to apply it", ip)
    return True


async def ensure_imap_tls_only() -> None:
    """IMAP clients may only send passwords over TLS (port 993, or 143 after STARTTLS)."""
    cur = await _mgmt("x:Imap/get", {"ids": ["singleton"]})
    if ((cur.get("list") or [{}])[0]).get("allowPlainTextAuth") is False:
        return
    await _mgmt("x:Imap/set", {"update": {"singleton": {"allowPlainTextAuth": False}}})
    log.warning("Stalwart: IMAP sign-in now requires TLS")


async def ensure_first_admin(db: AsyncSession) -> User | None:
    """Create MAILR_BOOTSTRAP_ADMIN_EMAIL as the first admin when there are no users yet.

    Never without a password while password sign-in is on: such an account cannot sign in. With
    MAILR_LOCAL_LOGIN=false the account has no password by design; single sign-on joins it by its
    verified address."""
    s = get_settings()
    if not s.bootstrap_admin_email:
        return None
    if (await db.execute(select(func.count()).select_from(User))).scalar_one():
        return None
    password = s.bootstrap_admin_password or None
    if password is None and s.local_login:
        log.warning("MAILR_BOOTSTRAP_ADMIN_PASSWORD is empty: the first admin %s was not created. Set it "
                    "and restart, or run `mailr create-user --admin`", s.bootstrap_admin_email)
        return None
    log.info("creating first admin %s", s.bootstrap_admin_email)
    return await create_user(db, s.bootstrap_admin_email, "", password=password, role="admin")


async def _bootstrap_locked(first_admin: bool = False) -> None:
    s = get_settings()
    await _migrate()
    await _wait_for_store()
    try:
        await ensure_stalwart_resolver()
    except Exception as e:  # noqa: BLE001
        log.warning("could not set Stalwart's DNS resolver: %s", e)
    try:
        await ensure_imap_tls_only()
    except Exception as e:  # noqa: BLE001
        log.warning("could not require TLS for IMAP sign-in: %s", e)
    try:
        from mailr.core.store_groupware import ensure_groupware_settings

        await ensure_groupware_settings()
    except Exception as e:  # noqa: BLE001  mail keeps working; the next start tries again
        log.warning("could not apply Stalwart's calendar and directory settings: %s", e)
    try:  # useXForwarded follows the phone access switch (calendar/phones.py)
        from mailr.calendar import phones

        await phones.apply_store_settings()
    except Exception as e:  # noqa: BLE001
        log.warning("could not apply Stalwart's phone access setting: %s", e)
    async with session_scope() as db:
        if first_admin:
            await ensure_first_admin(db)
        # Users created while the store was unreachable get their account now.
        pending = (await db.execute(select(User).where(User.stalwart_account_id.is_(None)))).scalars().all()
        for u in pending:
            if u.data_key_wrapped is None:
                u.data_key_wrapped = new_wrapped_data_key(u.id)
            await provision_store_account(db, u)
        # Users whose store account was just made get their built-in calendar, when wanted.
        from mailr.calendar import builtin

        for u in pending:
            await builtin.ensure_for_user(db, u)
        # Accounts made before IMAP sign-in by address existed get their alias.
        for u in (await db.execute(select(User).where(User.stalwart_account_id.is_not(None),
                                                      User.stalwart_login != User.email))).scalars():
            await ensure_login_alias(u)
        # Disabled users cannot sign in to the store (IMAP, DAV); enabled ones can again.
        await store_signin_follow(db)
        if s.seed_model_endpoints:
            have = (await db.execute(select(func.count()).select_from(ModelEndpoint))).scalar_one()
            if have == 0:
                for ep in json.loads(s.seed_model_endpoints):
                    row = ModelEndpoint(
                        name=ep["name"], base_url=ep["base_url"],
                        locality=ep.get("locality", "local"), adapter=ep.get("adapter", "openai"),
                        models=ep.get("models", []), defaults=ep.get("defaults", {}))
                    db.add(row)
                    if ep.get("api_key"):  # sealed for this row, so it needs the id
                        await db.flush()
                        row.api_key_enc = encrypt_instance(ep["api_key"], context=model_endpoint_context(row.id))
    from mailr.calendar import builtin

    try:
        await builtin.ensure_descriptions()
    except Exception as e:  # noqa: BLE001  mail keeps working; the next start tries again
        log.warning("could not set the store accounts' descriptions: %s", e)
    try:
        await builtin.backfill()
    except Exception as e:  # noqa: BLE001
        log.warning("could not create the built-in calendars: %s", e)
    try:
        from mailr.calendar import groups as calendar_groups

        await calendar_groups.reconcile()
    except Exception as e:  # noqa: BLE001  the job runs again on the next change or start
        log.warning("could not reconcile the group calendars: %s", e)
    try:  # phone app passwords follow the switch and each user's state (calendar/phones.py)
        from mailr.calendar import phones

        async with session_scope() as db:
            if await db.get(InstanceSetting, phones.SETTING) is not None:
                await phones.request_reconcile(db)
    except Exception as e:  # noqa: BLE001
        log.warning("could not queue the phone app password check: %s", e)
    try:  # grants to deleted accounts (calendar/sharing.py); a job, so the start does not wait
        from mailr.calendar import sharing

        async with session_scope() as db:
            if await builtin.instance_enabled(db):
                await sharing.request_cleanup(db)
    except Exception as e:  # noqa: BLE001
        log.warning("could not queue the calendar share cleanup: %s", e)


def oidc_groups(claims: dict) -> set[str]:
    """The `groups` claim as a set of names."""
    g = claims.get("groups")
    if isinstance(g, str):
        return {g} if g else set()
    if isinstance(g, (list, tuple, set)):
        return {x for x in g if isinstance(x, str) and x}
    return set()


async def user_for_oidc_claims(db: AsyncSession, claims: dict, invite_token: str | None = None) -> User:
    """The mailr user for verified OIDC claims. One set of rules for the web and native flows:

    - an account already linked to this subject is used;
    - an unlinked account is linked by email only when the provider verified the address, and
      an account linked to a *different* subject is never re-linked;
    - a self-service sign-up account (email_verified false: nobody proved the address) is never
      linked by email, so whoever signed up first cannot share the real owner's account;
    - accounts are created only with auto-create on and a verified email;
    - an account that auto-create made is signed in only while the user is still in
      MAILR_OIDC_AUTO_CREATE_GROUP (removal from the group locks it out of single sign-on);
    - with an admin group configured, the group decides the role both ways (removal demotes),
      except that the bootstrap admin always stays admin (lock-out guard).

    `groups` is read as a set of names: a list as its items, a single string as one name,
    anything else as no groups (a string is never searched for a substring).

    `invite_token`: a household invite (core/invites.py) the sign-in started from. An open invite
    for the provider's verified address creates the account even with auto-create off; the
    account is then not marked auto-created, so the auto-create group never locks it out."""
    from fastapi import HTTPException

    s = get_settings()
    sub = str(claims["sub"])
    email = (claims.get("email") or "").lower()
    name = claims.get("name") or claims.get("preferred_username") or ""
    groups = oidc_groups(claims)
    verified = claims.get("email_verified") is True or str(claims.get("email_verified")).lower() == "true"

    u = (await db.execute(select(User).where(User.oidc_sub == sub))).scalar_one_or_none()
    created = False
    if u is None and email:
        existing = (await db.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
        if existing is not None:
            if not verified:
                raise HTTPException(403, "your sign-in provider has not verified this email address; "
                                         "ask an administrator to link the account")
            if existing.oidc_sub and existing.oidc_sub != sub:
                raise HTTPException(403, "this Mailr account is linked to a different sign-in")
            if not existing.email_verified:
                raise HTTPException(403, "this Mailr account was created by sign-up and its address was never "
                                         "verified; ask an administrator to link it")
            existing.oidc_sub = sub
            u = existing
    if u is None and email and verified and invite_token:
        from mailr.core import invites

        if await invites.valid_for_email(db, invite_token, email) is not None:
            u = await create_user(db, email, name, oidc_sub=sub)
            created = True
    if u is None:
        if not s.oidc_auto_create or not email:
            raise ApiError(403, "auth.no_account")
        if s.oidc_auto_create_group and s.oidc_auto_create_group not in groups:
            raise ApiError(403, "auth.no_account")
        if not verified:
            raise HTTPException(403, "your sign-in provider has not verified this email address")
        u = await create_user(db, email, name, oidc_sub=sub)
        u.oidc_auto_created = True
        created = True
    elif (u.oidc_auto_created and s.oidc_auto_create_group
          and s.oidc_auto_create_group not in groups):
        raise ApiError(403, "auth.no_account")
    if s.oidc_admin_group:
        is_bootstrap = bool(s.bootstrap_admin_email) and u.email.lower() == s.bootstrap_admin_email.lower()
        u.role = "admin" if (s.oidc_admin_group in groups or is_bootstrap) else "user"
    if u.disabled:
        raise HTTPException(403, "account disabled")
    if "groups" in claims:
        # The provider is the source of truth for group membership (core/access.py): refreshed at
        # every sign-in. No claim at all leaves the stored groups as they are.
        from mailr.core.access import record_idp_groups

        await record_idp_groups(db, u, claims.get("groups"), source="oidc")
    if created:
        # A new account: the setup guide opens on the first visit, with the defaults an admin
        # prepared for this address or group (core/onboarding.py).
        from mailr.core import onboarding

        onboarding.mark_new(u)
        await onboarding.apply_preset(db, u)
    return u
