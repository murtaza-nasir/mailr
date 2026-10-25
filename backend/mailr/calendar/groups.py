"""Group calendars on mailr's own Stalwart.

mailr's groups are the effective groups of core/access.py: the identity provider's (users.idp_groups),
else the ones an admin assigned (users.assigned_groups). A group gets store accounts only while an
admin has attached at least one calendar to it (table calendar_groups):

- a Stalwart **Group** account. Members of a Stalwart group get full rights on every calendar the
  group account owns. Read-write group calendars live there.
- an **owner** account (a User with no password, description "mailr group calendar owner"). Read-only
  group calendars live there, with an ACL that grants each editor read + write and then the group
  read. Stalwart takes the first ACE that names the user, so the editors come first: an editor
  listed after the group gets only the group's read (verified on 0.16.23).

After every ACL write mailr runs `x:Action InvalidateCaches`: a first share to a group stays
invisible to its members until then (Stalwart refreshes only the group's own token).

Membership is reconciled (`reconcile()`): each user's `memberGroupIds` holds exactly the managed
groups the user is in. Only managed groups are touched. It runs as the job
`calendar.groups_reconcile`, queued at every start, when a sign-in reports other groups, when an
admin assigns groups or disables a user, and after an admin changes a group calendar.

Stalwart makes a stock default calendar ("Calendar (<group login>)") in a group account the first
time anyone lists it while its home is empty. mailr puts a placeholder that takes free/busy only in
every group home and destroys the stock calendar (JMAP; DAV refuses to delete a default), so it does
not come back and phones never show it (`seal_home`). The sync leaves out anything under a
managed account that is not a registered group calendar, the placeholder included.

Members see the calendars through their built-in connector (assisted discovery lists them in the
user's own home). `overrides_for()` marks them for the client: `shared = {kind: "group", group,
access, editor}`.
"""

from __future__ import annotations

import logging
import re
import secrets
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import builtin, dav
from mailr.config import get_settings
from mailr.core.jobs import enqueue, handler
from mailr.db import session_scope
from mailr.models import CalCollection, CalendarGroup, Connector, GroupCalendar, User

log = logging.getLogger("mailr.calendar.groups")

JOB = "calendar.groups_reconcile"
OWNER_DESCRIPTION = "mailr group calendar owner"
ACCESS = ("read", "write")
COMPONENTS = ["VEVENT"]
MAX_NAME = 200


class GroupCalendarError(Exception):
    """A plain message for the admin (the route answers 400 or 502 with it)."""


# ---------------- names and paths ----------------


def _slug(text: str, n: int = 24) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n].strip("-") or "group"


def _domain() -> str:
    return get_settings().stalwart_domain


def home_of(login: str) -> str:
    """The calendar home of a store account, as a path."""
    return f"/dav/cal/{quote(login.lower(), safe='')}/"


def principal_of(login: str) -> str:
    return f"/dav/pal/{quote(login.lower(), safe='')}/"


def path_of(href: str) -> str:
    """The collection path (one spelling) of an absolute href or a path."""
    p = urlsplit(href).path or href
    p = quote(unquote(p), safe="/-._~!$&'()*+,;=:")
    return p if p.endswith("/") else p + "/"


def _url(path: str) -> str:
    return get_settings().stalwart_url.rstrip("/") + path


def admin_client() -> dav.DavClient:
    s = get_settings()
    return dav.DavClient(builtin.dav_url(), s.stalwart_admin_user, s.stalwart_admin_password, internal=True)


def owner_client(owner_login: str) -> dav.DavClient:
    """The owner account has no password: mailr reaches it by impersonation, like a user's."""
    s = get_settings()
    return dav.DavClient(builtin.dav_url(), f"{owner_login}%{s.stalwart_admin_user}", s.stalwart_admin_password,
                         internal=True)


async def _mgmt(method: str, args: dict[str, Any]) -> dict[str, Any]:
    from mailr.core.users import _mgmt as mgmt

    return await mgmt(method, args)


async def invalidate_caches() -> None:
    """Stalwart's fix for the first share to a group (only the group's token is refreshed)."""
    await _mgmt("x:Action/set", {"create": {"a": {"@type": "InvalidateCaches"}}})


# ---------------- who is in a group ----------------


async def members(db: AsyncSession, group: str) -> list[User]:
    """Active users with a store account whose effective groups include `group`."""
    from mailr.core.access import effective_groups

    users = (await db.execute(select(User).where(User.disabled.is_(False), User.stalwart_account_id.is_not(None))
                              .order_by(User.id))).scalars().all()
    return [u for u in users if group in effective_groups(u)]


async def known_groups(db: AsyncSession) -> list[str]:
    """Every group name mailr knows: seen at sign-ins, assigned by an admin, with a policy, or with
    group calendars. Not the Everyone policy's "*", which is no group."""
    from mailr.core.access import EVERYONE
    from mailr.models import AccessPolicy, IdpGroupSeen

    names: set[str] = set((await db.execute(select(IdpGroupSeen.name))).scalars())
    names |= set((await db.execute(select(AccessPolicy.group_name))).scalars())
    names |= set((await db.execute(select(GroupCalendar.group_name))).scalars())
    for gs in (await db.execute(select(User.assigned_groups))).scalars():
        names |= set(gs or [])
    return sorted(n for n in names if n and n != EVERYONE)


async def user_login(db: AsyncSession, user: User) -> str:
    """The user's internal store login (the built-in connector's, else asked of the store)."""
    conn = await builtin.find(db, user.id)
    acct = ((conn.config or {}).get("account") if conn else None) or ""
    return acct or await builtin.internal_login(user)


# ---------------- the store accounts of a group ----------------


async def _account_exists(acct_id: str | None) -> bool:
    if not acct_id:
        return False
    r = await _mgmt("x:Account/get", {"ids": [acct_id], "properties": ["name"]})
    return bool(r.get("list"))


async def _create_account(kind: str, local: str, description: str) -> tuple[str, str]:
    from mailr.core.users import _domain_id

    obj: dict[str, Any] = {"@type": kind, "name": local, "domainId": await _domain_id(), "description": description[:200]}
    if kind == "Group":
        # No role. The stock group role grants the principal permissions mailr took away from the
        # User role (core/store_groupware.py), and the service account may not grant those: the
        # create would be refused. Members need nothing from the group but its calendars.
        obj["roles"] = {"@type": "Custom", "roleIds": {}}
    r = await _mgmt("x:Account/set", {"create": {"a": obj}})
    made = (r.get("created") or {}).get("a")
    if not made:
        raise GroupCalendarError(f"the mail store did not create the account: {r.get('notCreated')}")
    return made["id"], f"{local}@{_domain()}".lower()


async def ensure_group(db: AsyncSession, group: str) -> CalendarGroup:
    """The group's row and its Stalwart Group account (made when missing)."""
    cg = await db.get(CalendarGroup, group)
    if cg is None:
        cg = CalendarGroup(group_name=group)
        db.add(cg)
        await db.flush()
    if not await _account_exists(cg.store_group_id):
        local = f"grp-{_slug(group)}-{secrets.token_hex(3)}"
        cg.store_group_id, cg.store_group_login = await _create_account("Group", local, group)
        log.info("store group account made for group %r", group)
        await db.flush()
    return cg


async def ensure_owner(db: AsyncSession, cg: CalendarGroup) -> str:
    """The owner account of the group's read-only calendars (made when missing)."""
    if not await _account_exists(cg.owner_id):
        local = f"grpcal-{_slug(cg.group_name)}-{secrets.token_hex(3)}"
        cg.owner_id, cg.owner_login = await _create_account("User", local, OWNER_DESCRIPTION)
        await db.flush()
    return cg.owner_login or ""


async def drop_stock_default(cg: CalendarGroup) -> int:
    """Destroy the empty stock-named calendars of the group account (Stalwart's automatic default).
    JMAP, because DAV refuses to delete a default calendar; without onDestroyRemoveEvents, so a
    calendar that holds anything stays."""
    from mailr.core.jmap import CORE
    from mailr.core.store import service_client

    if not cg.store_group_id:
        return 0
    using = [CORE, "urn:ietf:params:jmap:calendars"]
    cli = service_client()
    got = await cli.one("Calendar/get", {"accountId": cg.store_group_id, "properties": ["name"]}, using=using)
    stock = [c["id"] for c in got.get("list") or [] if builtin.STOCK_NAME.match(c.get("name") or "")]
    if not stock:
        return 0
    r = await cli.one("Calendar/set", {"accountId": cg.store_group_id, "destroy": stock}, using=using)
    return len(r.get("destroyed") or [])


PLACEHOLDER_SLUG = "mailr-unused"
PLACEHOLDER_NAME = "Not used"
# A collection that takes free/busy only: calendar apps list collections for events or tasks
# (and journals), so phones leave it out. mailr's sync leaves it out too (filter_listing).
PLACEHOLDER_COMPONENTS = ["VFREEBUSY"]


async def seal_home(cg: CalendarGroup) -> dict[str, int]:
    """Keep the group account free of collections nobody asked for (phones see what
    members see). Stalwart makes a default calendar in an account whose calendar home is empty, the
    first time anyone lists it, and every member gets full rights on it; a member's phone then shows
    a writable "Calendar (grp-…)". A placeholder that takes free/busy only keeps the home from being
    empty, so the default is not made again after mailr destroys it. Address books, while phone
    access is on: Stalwart no longer makes them (x:AddressBook.defaultHrefName null), and mailr
    destroys the empty ones a group account already has. Non-empty ones stay (someone wrote to
    them). Idempotent."""
    from mailr.core.jmap import CORE
    from mailr.core.store import service_client

    out = {"placeholder": 0, "calendars": 0, "books": 0}
    if not cg.store_group_id or not cg.store_group_login:
        return out
    cli = service_client()
    cal_using = [CORE, "urn:ietf:params:jmap:calendars"]
    got = await cli.one("Calendar/get", {"accountId": cg.store_group_id, "properties": ["name"]}, using=cal_using)
    names = [c.get("name") or "" for c in got.get("list") or []]
    if PLACEHOLDER_NAME not in names:
        async with admin_client() as c:
            try:
                await c.mkcalendar(_url(home_of(cg.store_group_login)), PLACEHOLDER_SLUG, PLACEHOLDER_NAME,
                                   PLACEHOLDER_COMPONENTS)
                out["placeholder"] = 1
            except dav.DavError as e:  # 405: made meanwhile
                if e.status != 405:
                    raise
    if any(builtin.STOCK_NAME.match(n) for n in names) or out["placeholder"]:
        out["calendars"] = await drop_stock_default(cg)
    from mailr.calendar import phones

    async with session_scope() as db:
        if not await phones.instance_enabled(db):
            return out  # Stalwart makes address books by itself again: destroying them would only churn
    ab_using = [CORE, "urn:ietf:params:jmap:contacts"]
    books = await cli.one("AddressBook/get", {"accountId": cg.store_group_id, "properties": ["name"]}, using=ab_using)
    ids = [b["id"] for b in books.get("list") or []]
    if ids:
        r = await cli.one("AddressBook/set", {"accountId": cg.store_group_id, "destroy": ids}, using=ab_using)
        out["books"] = len(r.get("destroyed") or [])
        if r.get("notDestroyed"):
            log.warning("address books of group %r kept (not empty): %s", cg.group_name, list(r["notDestroyed"]))
    return out


async def _destroy_group(db: AsyncSession, cg: CalendarGroup) -> None:
    """The group has no calendar left: remove its store accounts and its row."""
    ids = [i for i in (cg.store_group_id, cg.owner_id) if i]
    if ids:
        r = await _mgmt("x:Account/set", {"destroy": ids})
        bad = {k: v for k, v in (r.get("notDestroyed") or {}).items() if (v or {}).get("type") != "notFound"}
        if bad:
            raise GroupCalendarError(f"the mail store kept the group's accounts: {bad}")
    await db.delete(cg)
    log.info("store accounts of group %r removed (no group calendar left)", cg.group_name)


# ---------------- membership ----------------


async def reconcile() -> dict[str, int]:
    """Make each user's memberGroupIds hold exactly the managed groups the user is in, remove the
    store accounts of groups with no calendar left, and queue a sync of everyone whose membership
    changed. Idempotent; touches only managed groups."""
    out = {"added": 0, "removed": 0, "groups_removed": 0, "failed": 0}
    async with session_scope() as db:
        if not await builtin.instance_enabled(db):
            return out
        with_cals = set((await db.execute(select(GroupCalendar.group_name))).scalars())
        rows = {cg.group_name: cg for cg in (await db.execute(select(CalendarGroup))).scalars()}
        if not rows and not with_cals:  # no group calendars: nothing on the store to keep
            return out
        for name in sorted(with_cals):
            try:
                rows[name] = await ensure_group(db, name)
            except Exception as e:  # noqa: BLE001  one group must not stop the others
                out["failed"] += 1
                log.warning("could not make the store group of %r: %s", name, e)
                continue
            try:
                await seal_home(rows[name])
            except Exception as e:  # noqa: BLE001  the next reconcile tries again
                out["failed"] += 1
                log.warning("could not tidy the store group of %r: %s", name, e)
        await db.commit()
        managed = {cg.store_group_id: name for name, cg in rows.items() if cg.store_group_id}
        live = {cg.store_group_id for name, cg in rows.items() if name in with_cals and cg.store_group_id}
        from mailr.core.access import effective_groups

        users = (await db.execute(select(User).where(User.stalwart_account_id.is_not(None)))).scalars().all()
        want = {u.stalwart_account_id: ({rows[g].store_group_id for g in effective_groups(u)
                                         if g in with_cals and g in rows and rows[g].store_group_id}
                                        if not u.disabled else set()) for u in users}
        uid_of = {u.stalwart_account_id: u.id for u in users}
    changed_users: set[int] = set()
    ids = list(want)
    for i in range(0, len(ids), 200):
        batch = ids[i:i + 200]
        cur = await _mgmt("x:Account/get", {"ids": batch, "properties": ["memberGroupIds"]})
        update: dict[str, dict[str, Any]] = {}
        for acct in cur.get("list") or []:
            have = {g for g, on in (acct.get("memberGroupIds") or {}).items() if on and g in managed}
            target = want.get(acct["id"], set()) & live
            patch: dict[str, Any] = {f"memberGroupIds/{g}": True for g in target - have}
            patch.update({f"memberGroupIds/{g}": None for g in have - target})
            if patch:
                update[acct["id"]] = patch
                out["added"] += len(target - have)
                out["removed"] += len(have - target)
        if update:
            r = await _mgmt("x:Account/set", {"update": update})
            for aid in update:
                if aid in (r.get("updated") or {}):
                    changed_users.add(uid_of[aid])
                else:
                    out["failed"] += 1
                    log.warning("could not change the groups of store account %s: %s", aid,
                                (r.get("notUpdated") or {}).get(aid))
    async with session_scope() as db:
        for name, cg in rows.items():
            if name in with_cals:
                continue
            cg = await db.get(CalendarGroup, name)
            if cg is None:
                continue
            try:
                await _destroy_group(db, cg)
                out["groups_removed"] += 1
            except Exception as e:  # noqa: BLE001
                out["failed"] += 1
                log.warning("could not remove the store accounts of group %r: %s", name, e)
        await queue_syncs(db, changed_users)
        await db.commit()
    if out["added"] or out["removed"] or out["groups_removed"]:
        log.info("group calendars reconciled: %s", out)
    return out


async def queue_syncs(db: AsyncSession, user_ids: set[int] | list[int]) -> None:
    """Sync these users' built-in connectors soon, so group calendars appear or go."""
    if not user_ids:
        return
    conns = (await db.execute(select(Connector).where(Connector.builtin.is_(True),
                                                      Connector.user_id.in_(list(user_ids))))).scalars().all()
    for c in conns:
        await enqueue(db, "calendar.sync", {"connector_id": c.id}, user_id=c.user_id, dedupe_key=f"calendar.sync:{c.id}")


async def request_reconcile(db: AsyncSession) -> None:
    """Queue the reconcile (in the caller's transaction; a queued one is not repeated)."""
    await enqueue(db, JOB, {}, dedupe_key=JOB)


@handler(JOB)
async def reconcile_job(payload: dict[str, Any], ctx) -> None:
    from mailr.core.jobs import Retry

    res = await reconcile()
    from mailr.calendar import phones
    from mailr.models import InstanceSetting

    async with session_scope() as db:  # membership decides who has a mailr calendar, so phone access too
        if await db.get(InstanceSetting, phones.SETTING) is not None:
            await phones.request_reconcile(db)
    if res["failed"]:
        raise Retry(delay=300)


async def groups_changed(db: AsyncSession, before: list[str], after: list[str]) -> None:
    """After a user's groups changed (a sign-in, an admin): reconcile when a managed group is
    involved, and create the built-in calendar when the policy now gives one."""
    if sorted(before) == sorted(after):
        return
    if not await builtin.instance_enabled(db):
        return
    await request_reconcile(db)
    await enqueue(db, "calendar.builtin_backfill", {}, dedupe_key="calendar.builtin_backfill")


# ---------------- the calendars ----------------


def _ace(principal: str, privs: list[str]) -> str:
    p = "".join(f"<d:privilege><d:{x}/></d:privilege>" for x in privs)
    return f"<d:ace><d:principal><d:href>{principal}</d:href></d:principal><d:grant>{p}</d:grant></d:ace>"


async def _write_acl(db: AsyncSession, cg: CalendarGroup, gc: GroupCalendar) -> None:
    """The whole ACL of a read-only group calendar: editors (read + write) first, then the group
    (read). An ACL request replaces the ACL, so it is always written in full."""
    aces = []
    for uid in gc.editors or []:
        u = await db.get(User, uid)
        if u is not None and u.stalwart_account_id and not u.disabled:
            aces.append(_ace(principal_of(await user_login(db, u)), ["read", "write"]))
    aces.append(_ace(principal_of(cg.store_group_login or ""), ["read"]))
    body = f'<?xml version="1.0" encoding="utf-8"?><d:acl xmlns:d="DAV:">{"".join(aces)}</d:acl>'
    async with owner_client(cg.owner_login or "") as c:
        r = await c.request("ACL", _url(gc.href or ""), body=body, headers={"Content-Type": "application/xml"})
        c._raise(r, f"ACL {gc.href}")
    await invalidate_caches()


def _client_for(cg: CalendarGroup, gc: GroupCalendar) -> dav.DavClient:
    return admin_client() if gc.access == "write" else owner_client(cg.owner_login or "")


def _clean_color(color: str | None) -> str | None:
    if not color:
        return None
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise GroupCalendarError("the colour must look like #2E7D32")
    return color.upper()


async def _check_editors(db: AsyncSession, group: str, editors: list[int]) -> list[int]:
    ids = sorted({int(x) for x in editors})
    if not ids:
        return []
    ok = {u.id for u in await members(db, group)}
    bad = [i for i in ids if i not in ok]
    if bad:
        raise GroupCalendarError("editors must be members of the group")
    return ids


async def create(db: AsyncSession, group: str, name: str, color: str | None, access: str,
                 editors: list[int] | None = None) -> GroupCalendar:
    """Make a group calendar on the store and register it. The caller commits."""
    group, name = group.strip()[:MAX_NAME], name.strip()[:MAX_NAME]
    if not group or not name:
        raise GroupCalendarError("a group and a name are needed")
    if access not in ACCESS:
        raise GroupCalendarError("access is read or write")
    if not await builtin.instance_enabled(db):
        raise GroupCalendarError("built-in calendars are off; turn them on first")
    color = _clean_color(color)
    eds = await _check_editors(db, group, editors or []) if access == "read" else []
    cg = await ensure_group(db, group)
    # The row first (without a path), so the reconcile counts the group as having a calendar and
    # puts the members in before the calendar exists.
    gc = GroupCalendar(group_name=group, name=name, color=color, access=access, editors=eds)
    db.add(gc)
    await db.commit()
    try:
        await reconcile()
        cg = await db.get(CalendarGroup, group)
        slug = f"{_slug(name)}-{secrets.token_hex(3)}"
        if access == "write":
            async with admin_client() as c:
                href = await c.mkcalendar(_url(home_of(cg.store_group_login or "")), slug, name, COMPONENTS, color)
            gc.href = path_of(href)
            try:
                await drop_stock_default(cg)
            except Exception as e:  # noqa: BLE001  cosmetic: the sync leaves it out
                log.warning("could not remove the stock calendar of group %r: %s", group, e)
        else:
            owner = await ensure_owner(db, cg)
            async with owner_client(owner) as c:
                href = await c.mkcalendar(_url(home_of(owner)), slug, name, COMPONENTS, color)
            gc.href = path_of(href)
            await db.flush()
            await _write_acl(db, cg, gc)
        await db.flush()
    except Exception:
        await db.rollback()
        stale = await db.get(GroupCalendar, gc.id)
        if stale is not None and not stale.href:
            await db.delete(stale)
        await request_reconcile(db)
        await db.commit()
        raise
    await queue_syncs(db, [u.id for u in await members(db, group)] + list(eds))
    return gc


async def update(db: AsyncSession, gc: GroupCalendar, *, name: str | None = None, color: str | None = None,
                 editors: list[int] | None = None, access: str | None = None) -> GroupCalendar:
    """Rename, recolour, change the editors (read-only calendars). Access is fixed at creation."""
    if access is not None and access != gc.access:
        raise GroupCalendarError("access is fixed when the calendar is made; export it, make a new one and import")
    cg = await db.get(CalendarGroup, gc.group_name)
    if cg is None or not gc.href:
        raise GroupCalendarError("this group calendar is not on the server")
    name = name.strip()[:MAX_NAME] if name is not None else None
    if name == "":
        raise GroupCalendarError("a name is needed")
    color = _clean_color(color) if color else None
    if name is not None or color is not None:
        async with _client_for(cg, gc) as c:
            if not await c.proppatch(_url(gc.href), displayname=name, color=color):
                raise GroupCalendarError("the calendar server did not accept the change")
        gc.name = name if name is not None else gc.name
        gc.color = color or gc.color
    if editors is not None:
        if gc.access != "read":
            raise GroupCalendarError("editors apply to read-only calendars; members already edit this one")
        before = set(gc.editors or [])
        gc.editors = await _check_editors(db, gc.group_name, editors)
        await _write_acl(db, cg, gc)
        await queue_syncs(db, before | set(gc.editors))
    await queue_syncs(db, [u.id for u in await members(db, gc.group_name)])
    return gc


async def delete(db: AsyncSession, gc: GroupCalendar) -> None:
    """Delete the calendar on the store and its row; the reconcile removes the group's accounts
    when it was the last one. The caller commits."""
    cg = await db.get(CalendarGroup, gc.group_name)
    if cg is not None and gc.href:
        async with _client_for(cg, gc) as c:
            try:
                await c.delete_collection(_url(gc.href))
            except dav.NotFound:
                pass
    users = [u.id for u in await members(db, gc.group_name)] + list(gc.editors or [])
    await db.delete(gc)
    await queue_syncs(db, users)
    await request_reconcile(db)


async def items(gc: GroupCalendar, cg: CalendarGroup | None) -> list[str]:
    """Every resource of the calendar (for the export before a delete)."""
    if cg is None or not gc.href:
        return []
    async with _client_for(cg, gc) as c:
        href = _url(gc.href)
        tags = await c.etags(href)
        got = await c.multiget(href, list(tags)) if tags else []
    return [ics for _h, _e, ics in got]


async def count(gc: GroupCalendar, cg: CalendarGroup | None) -> int | None:
    if cg is None or not gc.href:
        return None
    try:
        async with _client_for(cg, gc) as c:
            return len(await c.etags(_url(gc.href)))
    except Exception as e:  # noqa: BLE001  the page still lists it
        log.warning("could not count the items of group calendar %s: %s", gc.id, e)
        return None


# ---------------- what a member's mirror sees ----------------


async def managed_homes(db: AsyncSession) -> set[str]:
    rows = (await db.execute(select(CalendarGroup))).scalars().all()
    return {home_of(x) for cg in rows for x in (cg.store_group_login, cg.owner_login) if x}


async def filter_listing(infos: list[dav.CollectionInfo]) -> list[dav.CollectionInfo]:
    """A built-in connector's listing without the collections of managed accounts that are not
    registered group calendars (the group account's stock default, the owner's own calendar)."""
    async with session_scope() as db:
        homes = await managed_homes(db)
        if not homes:
            return infos
        known = set((await db.execute(select(GroupCalendar.href).where(GroupCalendar.href.is_not(None)))).scalars())
    out = []
    for i in infos:
        p = path_of(i.href)
        if any(p.startswith(h) for h in homes) and p not in known:
            continue
        out.append(i)
    return out


async def overrides_for(db: AsyncSession, user_id: int) -> dict[str, dict[str, Any]]:
    """Per collection href of the user's built-in connector: {"shared": {...}} for group calendars."""
    gcs = (await db.execute(select(GroupCalendar).where(GroupCalendar.href.is_not(None)))).scalars().all()
    if not gcs:
        return {}
    by_path = {gc.href: gc for gc in gcs}
    colls = (await db.execute(select(CalCollection).join(Connector, Connector.id == CalCollection.connector_id)
                              .where(CalCollection.user_id == user_id, Connector.builtin.is_(True),
                                     CalCollection.deleted_at.is_(None)))).scalars().all()
    out: dict[str, dict[str, Any]] = {}
    for c in colls:
        gc = by_path.get(path_of(c.href))
        if gc is not None:
            out[c.href] = {"shared": {"kind": "group", "group": gc.group_name, "access": gc.access,
                                      "editor": user_id in (gc.editors or []), "group_calendar_id": gc.id}}
    return out
