"""The built-in calendar: one CalDAV connector per user on mailr's own Stalwart.

- The instance setting `calendars.builtin` (InstanceSetting, default off) turns the feature on.
  `calendars.builtin_who` says who gets one: "everyone" (the default) or "groups", the members of
  groups whose access policy turns on "Members get a mailr calendar" (core/access.py, feature
  `calendar`), plus admins. `builtin_calendar_wanted(db, user)` decides per user.
- A user who leaves every such group keeps the connector and its data; it is hidden and not
  synced (`shown_for`), like with the instance switch off. Back in a group, it shows again.
- The connector is a normal `caldav` Connector with `builtin = true` (at most one per user,
  unique index). Its address and sign-in are never stored: the URL is the store's own
  (MAILR_STALWART_URL + /dav/cal/), and mailr signs in by impersonation,
  `<internal account name>%<service login>` with the service password. The internal account name
  (config.account) never changes, unlike the login alias.
- The API refuses to delete it or change its address (connector_routes, calendar/routes). A user
  may hide it (enabled = false). With the setting off, the connectors stay in the database and in
  Stalwart, but they are not listed and not synced; turning it on again resumes them.
- After its first sync: calendars that still carry Stalwart's stock name are renamed "Calendar",
  a "Tasks" list (VTODO only) is made when the home has none, and both become the user's
  defaults where the user has no other default. The stock calendar then counts as events only
  (mirror.builtin_events_only) unless it already holds tasks.
- Stalwart refuses an account's fifth concurrent HTTP request. mailr keeps at most
  MAX_IN_FLIGHT DAV requests per connector in flight in each process, and the client repeats
  that refusal with backoff (dav.BUSY_RETRIES), which covers the API and the worker together.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import dav
from mailr.config import get_settings
from mailr.core.jobs import enqueue, handler
from mailr.db import session_scope
from mailr.models import Connector, InstanceSetting, User

log = logging.getLogger("mailr.calendar.builtin")

SETTING = "calendars.builtin"
WHO_SETTING = "calendars.builtin_who"
WHO = ("everyone", "groups")
POLICY_FEATURE = "calendar"
NAME = "Mailr calendar"
CALENDAR_NAME = "Calendar"
TASKS_NAME = "Tasks"
TASKS_SLUG = "tasks"
MAX_IN_FLIGHT = 3
# Stalwart's automatic default calendar: "<defaultDisplayName> (<account login>)". Older
# stores say "Stalwart Calendar (…)", newer ones "Calendar (…)".
STOCK_NAME = re.compile(r"^(?:Stalwart Calendar|Calendar) \([^()]*@[^()]*\)$")


# ---------------- the decision ----------------


async def instance_enabled(db: AsyncSession) -> bool:
    row = await db.get(InstanceSetting, SETTING)
    return bool(row and row.value)


async def who(db: AsyncSession) -> str:
    row = await db.get(InstanceSetting, WHO_SETTING)
    return row.value if row is not None and row.value in WHO else "everyone"


async def policy_allows(db: AsyncSession, user: User) -> bool:
    """The per-group policy: with "groups", only admins and members of a group whose access policy
    turns on "Members get a mailr calendar" (feature `calendar`)."""
    if await who(db) == "everyone":
        return True
    from mailr.core.access import effective_groups, is_admin
    from mailr.models import AccessPolicy

    if is_admin(user):
        return True
    groups = effective_groups(user)
    if not groups:
        return False
    pols = (await db.execute(select(AccessPolicy.features).where(AccessPolicy.group_name.in_(groups)))).scalars()
    return any(bool((f or {}).get(POLICY_FEATURE)) for f in pols)


async def builtin_calendar_wanted(db: AsyncSession, user: User) -> bool:
    """Should this user have the built-in calendar? The instance switch, then the group policy."""
    if user is None or not user.stalwart_account_id or user.disabled:
        return False
    return await instance_enabled(db) and await policy_allows(db, user)


async def shown_for(db: AsyncSession, user_id: int) -> bool:
    """Is this user's built-in connector listed and synced? Instance on and the policy allows it.
    Not shown: kept as it is, data included."""
    if not await instance_enabled(db):
        return False
    user = await db.get(User, user_id)
    return user is not None and await policy_allows(db, user)


async def active(db: AsyncSession, conn: Connector) -> bool:
    """A built-in connector is listed and synced only while it is shown for its user."""
    return not conn.builtin or await shown_for(db, conn.user_id)


# ---------------- signing in ----------------


def dav_url() -> str:
    return get_settings().stalwart_url.rstrip("/") + "/dav/cal/"


_LIMITERS: dict[tuple[int, int], asyncio.Semaphore] = {}


def limiter(connector_id: int) -> asyncio.Semaphore:
    """The per-connector cap on DAV requests in flight, one per event loop."""
    key = (id(asyncio.get_running_loop()), connector_id)
    sem = _LIMITERS.get(key)
    if sem is None:
        sem = _LIMITERS[key] = asyncio.Semaphore(MAX_IN_FLIGHT)
    return sem


def client_for(conn: Connector) -> dav.DavClient:
    """A DAV client signed in as the connector's user, through the service account. The
    credentials are derived here and never stored with the connector."""
    s = get_settings()
    account = (conn.config or {}).get("account") or ""
    try:
        lim = limiter(conn.id)
    except RuntimeError:  # no running loop (never in practice: callers are async)
        lim = None
    return dav.DavClient(dav_url(), f"{account}%{s.stalwart_admin_user}", s.stalwart_admin_password,
                         internal=True, limiter=lim)


async def internal_login(user: User) -> str:
    """The account's primary name on the store (`<local>@<store domain>`). It never changes;
    the user's own address is only an alias of it."""
    from mailr.core.users import _mgmt

    r = await _mgmt("x:Account/get", {"ids": [user.stalwart_account_id], "properties": ["name", "emailAddress"]})
    acct = (r.get("list") or [None])[0]
    if not acct:
        raise RuntimeError("the mail store has no account for this user")
    addr = acct.get("emailAddress") or ""
    if "@" in addr:
        return addr.lower()
    name = acct.get("name") or ""
    return (name if "@" in name else f"{name}@{get_settings().stalwart_domain}").lower()


# ---------------- creating the connectors ----------------


async def find(db: AsyncSession, user_id: int) -> Connector | None:
    return (await db.execute(select(Connector).where(Connector.user_id == user_id,
                                                     Connector.builtin.is_(True)))).scalar_one_or_none()


async def ensure_connector(db: AsyncSession, user: User) -> Connector | None:
    """Create the user's built-in connector when it is wanted and missing (idempotent; safe to
    run from the API and the worker at once). Returns the connector, or None when not wanted.
    An existing connector is left as it is, hidden or not."""
    have = await find(db, user.id)
    if have is not None:
        return have
    if not await builtin_calendar_wanted(db, user):
        return None
    account = await internal_login(user)
    stmt = insert(Connector).values(
        user_id=user.id, kind="caldav", name=NAME, mode="mirror", data_class="normal",
        config={"account": account}, state={}, enabled=True, builtin=True, status="new",
        status_detail="Starting…", retention_days=14, delete_upstream=False,
    ).on_conflict_do_nothing(index_elements=["user_id"], index_where=text("builtin")
                             ).returning(Connector.id)
    cid = (await db.execute(stmt)).scalar_one_or_none()
    if cid is None:  # made meanwhile by the other process
        return await find(db, user.id)
    await enqueue(db, "calendar.sync", {"connector_id": cid}, user_id=user.id, dedupe_key=f"calendar.sync:{cid}")
    log.info("built-in calendar created for user %s", user.id)
    return await db.get(Connector, cid)


async def ensure_for_user(db: AsyncSession, user: User) -> None:
    """At user creation: never fails the creation (the backfill tries again)."""
    try:
        async with db.begin_nested():
            await ensure_connector(db, user)
    except Exception as e:  # noqa: BLE001
        log.warning("could not create the built-in calendar of user %s: %s", user.id, e)


async def backfill() -> tuple[int, int]:
    """Create the connector for every user who should have one: (created, failed)."""
    made = failed = 0
    async with session_scope() as db:
        if not await instance_enabled(db):
            return 0, 0
        have = select(Connector.user_id).where(Connector.builtin.is_(True))
        ids = (await db.execute(select(User.id).where(User.stalwart_account_id.is_not(None),
                                                      User.id.not_in(have)).order_by(User.id))).scalars().all()
    for uid in ids:
        try:
            async with session_scope() as db:
                user = await db.get(User, uid)
                if user is not None and await ensure_connector(db, user) is not None:
                    made += 1
        except Exception as e:  # noqa: BLE001  one user's store account must not stop the others
            failed += 1
            log.warning("could not create the built-in calendar of user %s: %s", uid, e)
    if made or failed:
        log.info("built-in calendars: %s created, %s failed", made, failed)
    return made, failed


@handler("calendar.builtin_backfill")
async def backfill_job(payload: dict[str, Any], ctx) -> None:
    from mailr.core.jobs import Retry

    _made, failed = await backfill()
    if failed:
        raise Retry(delay=300)


async def _set(db: AsyncSession, key: str, value: Any) -> None:
    row = await db.get(InstanceSetting, key)
    if row is None:
        db.add(InstanceSetting(key=key, value=value))
    else:
        row.value = value


async def set_instance_enabled(db: AsyncSession, on: bool, who_value: str | None = None) -> None:
    """The admin switch. On: queue the backfill and the group reconcile. Off: nothing is deleted;
    the connectors are no longer listed or synced (active())."""
    await _set(db, SETTING, bool(on))
    if who_value is not None:
        if who_value not in WHO:
            raise ValueError(f"who is one of {', '.join(WHO)}")
        await _set(db, WHO_SETTING, who_value)
    if on:
        await enqueue(db, "calendar.builtin_backfill", {}, dedupe_key="calendar.builtin_backfill")
        await enqueue(db, "calendar.groups_reconcile", {}, dedupe_key="calendar.groups_reconcile")


async def status(db: AsyncSession) -> dict[str, Any]:
    from sqlalchemy import func

    n = (await db.execute(select(func.count()).select_from(Connector).where(Connector.builtin.is_(True)))).scalar_one()
    users = (await db.execute(select(func.count()).select_from(User).where(User.stalwart_account_id.is_not(None)))
             ).scalar_one()
    from mailr.calendar import sharing

    return {"builtin": await instance_enabled(db), "who": await who(db), "sharing": await sharing.policy(db),
            "connectors": n, "users": users}


# ---------------- first sync: names, the Tasks list, defaults ----------------


def _own(client: dav.DavClient, home: str, href: str) -> bool:
    return client.abs(href).startswith(client.abs(home))


async def prepare(client: dav.DavClient, home: str, infos: list[dav.CollectionInfo]) -> bool:
    """Rename stock-named calendars and make the Tasks list. True when something changed (the
    caller lists the collections again)."""
    changed = False
    own = [i for i in infos if _own(client, home, i.href)]
    for info in own:
        if STOCK_NAME.match(info.displayname or ""):
            try:
                if await client.proppatch(info.href, displayname=CALENDAR_NAME):
                    changed = True
            except dav.DavError as e:
                log.warning("could not rename %s: %s", info.href, e)
    if not any(i.components == ["VTODO"] for i in own):
        try:
            await client.mkcalendar(home, TASKS_SLUG, TASKS_NAME, ["VTODO"])
            changed = True
        except dav.DavError as e:  # e.g. 405 when a "tasks" collection exists with other components
            log.warning("could not create the Tasks list in %s: %s", home, e)
    return changed


async def adopt_defaults(user_id: int, connector_id: int, home: str) -> None:
    """Make the built-in calendar and Tasks list the user's defaults, slot by slot, where the user
    has no default of their own: nothing saved, and no other account's collection takes that
    kind (an external calendar that was the default stays the default)."""
    from mailr.calendar import mirror

    async with session_scope() as db:
        from mailr.core import usersettings

        user = await db.get(User, user_id)
        if user is None:
            return
        saved = dict((await usersettings.current(db, user_id)).get("calendar_defaults") or {})
        conns = [c for c in await mirror.visible_connectors(db, user_id) if c.id != connector_id]
        others = await mirror.collections_of(db, [c.id for c in conns])
        mine = [c for c in await mirror.collections_of(db, [connector_id]) if c.href.startswith(home)]
        ov = await mirror.kind_overrides(db, user_id)
        conn = await db.get(Connector, connector_id)
        mine_out = [mirror.collection_out(c, conn, ov) for c in mine]
        others_out = [mirror.collection_out(c, next(x for x in conns if x.id == c.connector_id), ov)
                      for c in others if not c.read_only]
        cal_url = next((c["url"] for c in mine_out if c["events"] and c["url"].rstrip("/").endswith("/default")), None) \
            or next((c["url"] for c in mine_out if c["events"] and not c["tasks"]), None) \
            or next((c["url"] for c in mine_out if c["events"]), None)
        task_url = next((c["url"] for c in mine_out if c["tasks"] and not c["events"]), None) \
            or next((c["url"] for c in mine_out if c["tasks"]), None)
        new = dict(saved)
        if not saved.get("calendar") and cal_url and not any(c["events"] for c in others_out):
            new["calendar"] = cal_url
        if not saved.get("task_list") and task_url and not any(c["tasks"] for c in others_out):
            new["task_list"] = task_url
        if new != saved:
            # Written directly: client.set_defaults would sync this connector inline, and this
            # run holds its sync lock.
            await usersettings.patch(db, user_id, {"calendar_defaults": {
                "calendar": new.get("calendar"), "task_list": new.get("task_list")}})


# ---------------- the store account's description ----------------


def description_for(user: User) -> str:
    """What other accounts may see of this one (a sharee can read it as the owner's name)."""
    return (user.name or "").strip()[:200] or user.email.split("@")[0]


async def set_description(user: User) -> bool:
    """Write the user's display name as the store account's description. True when it changed."""
    from mailr.core.users import _mgmt

    if not user.stalwart_account_id:
        return False
    want = description_for(user)
    cur = await _mgmt("x:Account/get", {"ids": [user.stalwart_account_id], "properties": ["description"]})
    acct = (cur.get("list") or [None])[0]
    if acct is None or acct.get("description") == want:
        return False
    r = await _mgmt("x:Account/set", {"update": {user.stalwart_account_id: {"description": want}}})
    if user.stalwart_account_id not in (r.get("updated") or {}):
        log.warning("could not set the store account description of user %s: %s", user.id, r.get("notUpdated"))
        return False
    return True


_DESCRIPTIONS_DONE = False


async def ensure_descriptions(*, force: bool = False) -> int:
    """At start (once per process): every store account's description is its user's display
    name (accounts made by older versions say "mailr user <id> (<address>)"). Reads in batches,
    writes only what differs."""
    global _DESCRIPTIONS_DONE
    from mailr.core.users import _mgmt

    if _DESCRIPTIONS_DONE and not force:
        return 0
    async with session_scope() as db:
        users = (await db.execute(select(User).where(User.stalwart_account_id.is_not(None)))).scalars().all()
        want = {u.stalwart_account_id: description_for(u) for u in users}
    changed = 0
    ids = list(want)
    for i in range(0, len(ids), 200):
        batch = ids[i:i + 200]
        cur = await _mgmt("x:Account/get", {"ids": batch, "properties": ["description"]})
        update = {a["id"]: {"description": want[a["id"]]} for a in cur.get("list") or []
                  if a.get("id") in want and a.get("description") != want[a["id"]]}
        if update:
            r = await _mgmt("x:Account/set", {"update": update})
            changed += len(r.get("updated") or {})
    if changed:
        log.info("store account descriptions set to display names: %s", changed)
    _DESCRIPTIONS_DONE = True
    return changed


async def user_renamed(user: User) -> None:
    """After a display name change (best effort; the next start reconciles)."""
    try:
        await set_description(user)
    except Exception as e:  # noqa: BLE001
        log.warning("could not update the store account description of user %s: %s", user.id, e)

