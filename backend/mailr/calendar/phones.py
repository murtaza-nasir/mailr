"""Phones and desktop apps on the built-in calendars.

Admin page site/docs/admin/calendars.md.

- **The switch.** The instance setting `calendars.phone_access` (off by default) and the public
  address phones use, `calendars.phone_url` (https://dav.example.com). An admin turns it on only
  while built-in calendars are on, and only after the reverse proxy publishes Stalwart's DAV paths
  (deploy/dav-gateway).
- **App passwords.** A user makes one per device under Settings → Calendar & tasks → "Phones and
  other apps". It is a Stalwart app password on the user's own store account, minted by the
  service account, with a permission set that holds DAV only (DAV_PERMISSIONS): no IMAP, no
  JMAP, no sending, no sharing (`davCalAcl`: sharing stays in mailr, which writes the people-first
  ACL) and no schedule outbox (`davCalFreeBusyQuery`: it tells whether an address has an
  account). The secret is shown once and never stored by mailr. mailr marks its own passwords with
  the description prefix PREFIX and lists, pauses and deletes only those.
- **Pausing.** A paused password has `expiresAt` in the past (PAUSED); Stalwart then refuses it at
  once (401, verified on 0.16.23). mailr pauses every password of a user while phone access is off,
  while the user is disabled, and while the user has no mailr calendar (built-in calendars off, or
  the group policy leaves them out). It lifts the pause when all three hold again. The job
  `calendar.phone_reconcile` applies this to everyone; it runs at every start and after each
  change of the switch, the built-in calendar settings or a user's state.
- **Scheduling stays off.** Stalwart does not send invitations (core/store_groupware.py). An event
  with guests that a phone creates is saved, and nobody is invited. mailr's own invitations still
  go out through SendGate. The setup page and the dialog say so.
- **Real client addresses.** With phone access on, Stalwart reads the client's address from
  `X-Forwarded-For` (`x:Http.useXForwarded`, core/store_groupware.ensure_phone_settings), so its
  auto-ban hits the attacker, not every phone behind the proxy. Stalwart trusts that header from
  anyone who reaches it, so only the gateway may reach port 8080 from outside, and the gateway
  overwrites the header (deploy/dav-gateway).
"""

from __future__ import annotations

import logging
import plistlib
import re
import uuid
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.jobs import enqueue, handler
from mailr.db import session_scope
from mailr.models import InstanceSetting, User

log = logging.getLogger("mailr.calendar.phones")

SETTING = "calendars.phone_access"
URL_SETTING = "calendars.phone_url"
JOB = "calendar.phone_reconcile"
PREFIX = "mailr: "
# expiresAt of a paused password. mailr never sets an expiry otherwise, so any past expiry is a pause.
PAUSED = "2000-01-01T00:00:00Z"
MAX_PER_USER = 20
MAX_NAME = 60
# The JMAP request limit of Stalwart (x:Jmap.maxMethodCalls) is 16 calls.
BATCH = 16

_VERBS = ("PropFind", "PropPatch", "Get", "MkCol", "Delete", "Put", "Copy", "Move", "Lock", "Query", "MultiGet")
# Everything a calendar or contacts app needs, nothing else. `authenticateWithAlias`: users sign in
# with their own address, an alias of the store account.
DAV_PERMISSIONS: tuple[str, ...] = (
    "authenticate", "authenticateWithAlias", "davSyncCollection", "davExpandProperty",
    *(f"davCal{v}" for v in _VERBS), *(f"davCard{v}" for v in _VERBS),
)
# Never in the set (tests/test_phone_access.py checks): sharing, the schedule outbox and the
# directory, besides every mail protocol.
EXCLUDED: tuple[str, ...] = (
    "davCalAcl", "davCardAcl", "davFileAcl", "davCalFreeBusyQuery", "calendarSchedulingSend",
    "calendarSchedulingReceive", "emailSend", "imapAuthenticate", "pop3Authenticate", "sieveAuthenticate",
    "davPrincipalAcl", "davPrincipalList", "davPrincipalMatch", "davPrincipalSearch", "davPrincipalSearchPropSet",
)

SCHEDULING_NOTE = ("Invitations are not sent from phones. An event with guests that you make in a phone or "
                   "desktop calendar app is saved, but nobody is invited. To invite people, make the event "
                   "in Mailr.")


class PhoneAccessError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


async def _mgmt(method: str, args: dict[str, Any]) -> dict[str, Any]:
    from mailr.core.users import _mgmt as mgmt

    return await mgmt(method, args)


def _client() -> Any:
    from mailr.core.store import service_client

    return service_client()


# ---------------- the instance setting ----------------


async def instance_enabled(db: AsyncSession) -> bool:
    row = await db.get(InstanceSetting, SETTING)
    return bool(row and row.value)


async def public_url(db: AsyncSession) -> str:
    row = await db.get(InstanceSetting, URL_SETTING)
    return str(row.value) if row is not None and isinstance(row.value, str) else ""


def normalize_url(url: str) -> str:
    """https://host[:port], nothing else. Stalwart's links are absolute paths (/dav/…), so the DAV
    paths must sit at the root of the host: a path prefix cannot work."""
    u = urlsplit((url or "").strip())
    if u.scheme != "https":
        raise PhoneAccessError("The address must start with https://. Phones send the password with every request.")
    if not u.hostname or u.username or u.password:
        raise PhoneAccessError("The address needs a host name and nothing before it.")
    if u.path not in ("", "/") or u.query or u.fragment:
        raise PhoneAccessError("Give the host only, for example https://dav.example.com. The calendar paths "
                               "must sit at the root of the host.")
    try:
        port = u.port
    except ValueError:
        raise PhoneAccessError("The port is not valid.") from None
    host = u.hostname.lower()
    return f"https://{host}" + (f":{port}" if port and port != 443 else "")


async def _put(db: AsyncSession, key: str, value: Any) -> None:
    row = await db.get(InstanceSetting, key)
    if row is None:
        db.add(InstanceSetting(key=key, value=value))
    else:
        row.value = value


async def set_instance(db: AsyncSession, on: bool | None, url: str | None) -> None:
    """The admin switch (in the caller's transaction). The store setting (useXForwarded) follows
    after the commit: apply_store_settings()."""
    from mailr.calendar import builtin

    if url is not None:
        await _put(db, URL_SETTING, normalize_url(url) if url.strip() else "")
    if on is not None:
        if on:
            if not await builtin.instance_enabled(db):
                raise PhoneAccessError("Turn on built-in calendars first.", 409)
            if not await public_url(db):
                raise PhoneAccessError("Enter the public address first.", 409)
        await _put(db, SETTING, bool(on))
    await request_reconcile(db)


async def status(db: AsyncSession) -> dict[str, Any]:
    return {"phone_access": await instance_enabled(db), "phone_url": await public_url(db)}


async def apply_store_settings() -> list[str]:
    """Stalwart's settings follow the switch (core/store_groupware.ensure_phone_settings): on, the
    proxy's X-Forwarded-For names the client and no address book is made by itself. Called at
    every start and after the admin switch; writes only what differs."""
    from mailr.core.store_groupware import ensure_phone_settings

    async with session_scope() as db:
        on = await instance_enabled(db)
    return await ensure_phone_settings(on)


# ---------------- who may use it ----------------


async def why_not(db: AsyncSession, user: User) -> str | None:
    """None when this user may use phone access now, else the reason (plain, for the user)."""
    from mailr.calendar import builtin

    if not await instance_enabled(db):
        return "Phone access is off on this server. Ask your administrator to turn it on."
    if user.disabled or not user.stalwart_account_id:
        return "Your account cannot use phone access."
    if not await builtin.shown_for(db, user.id):
        return "Your account has no Mailr calendar, so there is nothing for a phone to show."
    if not await public_url(db):
        return "The server's public address for phones is not set. Ask your administrator."
    return None


# ---------------- the passwords ----------------


def _ours(item: dict[str, Any]) -> bool:
    return str(item.get("description") or "").startswith(PREFIX)


def _paused(item: dict[str, Any]) -> bool:
    return item.get("expiresAt") is not None


def _out(item: dict[str, Any]) -> dict[str, Any]:
    return {"id": item["id"], "name": str(item.get("description") or "")[len(PREFIX):],
            "created_at": item.get("createdAt"), "paused": _paused(item)}


async def _raw(account_id: str) -> list[dict[str, Any]]:
    r = await _mgmt("x:AppPassword/get", {"accountId": account_id})
    return [x for x in r.get("list") or [] if _ours(x)]


async def list_for(user: User) -> list[dict[str, Any]]:
    if not user.stalwart_account_id:
        return []
    return sorted((_out(x) for x in await _raw(user.stalwart_account_id)), key=lambda x: x["created_at"] or "")


def clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", "", name or "")).strip()
    if not name:
        raise PhoneAccessError("Give the device a name, for example “Sam’s iPhone”.")
    if len(name) > MAX_NAME:
        raise PhoneAccessError(f"The name can be at most {MAX_NAME} characters.")
    return name


async def create(db: AsyncSession, user: User, name: str) -> dict[str, Any]:
    """A new DAV-only app password. Returns it with its secret, which is never stored."""
    reason = await why_not(db, user)
    if reason:
        raise PhoneAccessError(reason, 409)
    name = clean_name(name)
    have = await _raw(user.stalwart_account_id)
    if len(have) >= MAX_PER_USER:
        raise PhoneAccessError(f"You have {MAX_PER_USER} app passwords. Delete one you no longer use first.", 409)
    r = await _mgmt("x:AppPassword/set", {"accountId": user.stalwart_account_id, "create": {"p": {
        "description": PREFIX + name,
        "permissions": {"@type": "Replace", "permissions": {p: True for p in DAV_PERMISSIONS}}}}})
    made = (r.get("created") or {}).get("p")
    if not made or not made.get("secret"):
        log.warning("could not create an app password for user %s: %s", user.id, r.get("notCreated"))
        raise PhoneAccessError("The mail server did not create the password. Try again later.", 502)
    got = [x for x in await _raw(user.stalwart_account_id) if x["id"] == made["id"]]
    created = got[0].get("createdAt") if got else None
    return {"id": made["id"], "name": name, "password": made["secret"], "created_at": created, "paused": False}


async def revoke(user: User, pid: str) -> bool:
    """Delete one of the user's mailr app passwords. False when there is no such password."""
    if not user.stalwart_account_id or not pid:
        return False
    if pid not in {x["id"] for x in await _raw(user.stalwart_account_id)}:
        return False
    r = await _mgmt("x:AppPassword/set", {"accountId": user.stalwart_account_id, "destroy": [pid]})
    if pid not in (r.get("destroyed") or []):
        raise PhoneAccessError("The mail server did not delete the password. Try again later.", 502)
    return True


def _pause_patch(items: list[dict[str, Any]], paused: bool) -> dict[str, dict[str, Any]]:
    return {x["id"]: {"expiresAt": PAUSED if paused else None} for x in items if _paused(x) != paused}


async def set_paused(account_id: str, paused: bool) -> int:
    """Pause (or resume) every mailr app password of one store account. Returns how many changed."""
    patch = _pause_patch(await _raw(account_id), paused)
    if not patch:
        return 0
    r = await _mgmt("x:AppPassword/set", {"accountId": account_id, "update": patch})
    bad = set(patch) - set(r.get("updated") or {})
    if bad:
        raise RuntimeError(f"could not change app passwords {sorted(bad)}: {r.get('notUpdated')}")
    return len(patch)


async def revoke_all(account_id: str) -> int:
    """Delete every app password of one store account: the ones made in mailr and any made
    elsewhere (a user being deleted, or account recovery: an intruder's own app password must not
    survive it). Returns how many went. Raises when the store refused one
    (`notDestroyed`), so the caller reports a failure, never a success."""
    r = await _mgmt("x:AppPassword/get", {"accountId": account_id})
    ids = [x["id"] for x in r.get("list") or [] if x.get("id")]
    if not ids:
        return 0
    res = await _mgmt("x:AppPassword/set", {"accountId": account_id, "destroy": ids})
    gone = set(res.get("destroyed") or [])
    left = [i for i in ids if i not in gone]
    if left or res.get("notDestroyed"):
        raise RuntimeError(f"the store kept {len(left) or len(res.get('notDestroyed') or {})} app passwords")
    return len(ids)


async def reconcile() -> dict[str, int]:
    """Pause or resume every user's passwords to match why_not(). Idempotent: reads all, writes
    only differences. One JMAP request lists up to BATCH accounts."""
    out = {"paused": 0, "resumed": 0, "failed": 0}
    async with session_scope() as db:
        users = (await db.execute(select(User).where(User.stalwart_account_id.is_not(None)).order_by(User.id))
                 ).scalars().all()
        want = {u.stalwart_account_id: (await why_not(db, u)) is None for u in users}
    ids = list(want)
    cli = _client()
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        try:
            got = await cli.call([["x:AppPassword/get", {"accountId": a}, a] for a in chunk],
                                 using=["urn:ietf:params:jmap:core", "urn:stalwart:jmap"])
        except Exception as e:  # noqa: BLE001
            out["failed"] += len(chunk)
            log.warning("could not list app passwords: %s", e)
            continue
        updates = []
        for name, args, acct in got:
            if name == "error":
                continue  # an account without app passwords support (a group): nothing of ours
            items = [x for x in args.get("list") or [] if _ours(x)]
            patch = _pause_patch(items, not want[acct])
            if patch:
                updates.append(["x:AppPassword/set", {"accountId": acct, "update": patch}, acct])
                out["resumed" if want[acct] else "paused"] += len(patch)
        if updates:
            try:
                res = await cli.call(updates, using=["urn:ietf:params:jmap:core", "urn:stalwart:jmap"])
                for _name, args, acct in res:
                    if args.get("notUpdated"):
                        out["failed"] += 1
                        log.warning("could not pause or resume app passwords of %s: %s", acct, args.get("notUpdated"))
            except Exception as e:  # noqa: BLE001
                out["failed"] += len(updates)
                log.warning("could not pause or resume app passwords: %s", e)
    if out["paused"] or out["resumed"]:
        log.info("phone app passwords reconciled: %s", out)
    return out


async def request_reconcile(db: AsyncSession) -> None:
    await enqueue(db, JOB, {}, dedupe_key=JOB)


@handler(JOB)
async def reconcile_job(payload: dict[str, Any], ctx) -> None:
    from mailr.core.jobs import Retry

    if (await reconcile())["failed"]:
        raise Retry(delay=300)


async def user_changed(user: User) -> None:
    """A user was disabled or enabled: pause or resume their passwords now (the job repeats it)."""
    if not user.stalwart_account_id:
        return
    async with session_scope() as db:
        fresh = await db.get(User, user.id)
        allowed = fresh is not None and (await why_not(db, fresh)) is None
    try:
        await set_paused(user.stalwart_account_id, not allowed)
    except Exception as e:  # noqa: BLE001  the reconcile job repeats it
        log.warning("could not pause or resume the app passwords of user %s: %s", user.id, e)


# ---------------- an iOS / macOS profile ----------------


def mobileconfig(*, url: str, username: str, password: str, device: str) -> bytes:
    """An unsigned configuration profile with a CalDAV and a CardDAV account for iOS and macOS.
    It holds the password: it is offered once, as a download, and never stored."""
    u = urlsplit(url)
    host = u.hostname or ""
    port = u.port or 443
    ident = "mailr." + ".".join(reversed(host.split(".")))

    def account(kind: str) -> dict[str, Any]:
        k = "CalDAV" if kind == "caldav" else "CardDAV"
        return {
            f"{k}AccountDescription": f"mailr {'calendar' if kind == 'caldav' else 'contacts'}",
            f"{k}HostName": host,
            f"{k}Port": port,
            f"{k}UseSSL": True,
            f"{k}Username": username,
            f"{k}Password": password,
            "PayloadType": f"com.apple.{kind}.account",
            "PayloadIdentifier": f"{ident}.{kind}.{uuid.uuid4()}",
            "PayloadUUID": str(uuid.uuid4()).upper(),
            "PayloadVersion": 1,
            "PayloadDisplayName": f"Mailr {'calendar' if kind == 'caldav' else 'contacts'}",
        }

    profile = {
        "PayloadContent": [account("caldav"), account("carddav")],
        "PayloadDisplayName": f"Mailr calendar and contacts ({device})",
        "PayloadDescription": "Your Mailr calendars, task lists and contacts. " + SCHEDULING_NOTE,
        "PayloadIdentifier": f"{ident}.profile.{uuid.uuid4()}",
        "PayloadOrganization": "Mailr",
        "PayloadType": "Configuration",
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadVersion": 1,
        "PayloadRemovalDisallowed": False,
    }
    return plistlib.dumps(profile, fmt=plistlib.FMT_XML)
