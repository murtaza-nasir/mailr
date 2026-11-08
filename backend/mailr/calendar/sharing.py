"""Sharing a built-in calendar with other people on the server.

- **What can be shared.** A user's own calendars on the built-in connector (events; the Tasks
  list is not offered). Group calendars and external accounts are never shared from here.
- **With whom.** Other mailr users, picked from mailr's own user list, never Stalwart's directory.
  The instance setting `calendars.sharing` limits the list: "anyone" (every active user with a
  built-in calendar, the default), "groups" (users who share at least one mailr group with the
  person sharing) or "nobody". A narrower policy applies to new shares and level changes;
  existing shares stay until someone stops them.
- **Levels.** view (`read`), edit (`read` + `write`) and share (`read` + `write` + `write-acl`),
  on every server; free/busy only (`freebusy`: CalDAV `read-free-busy` alone) on Stalwart 1.x only
  (`levels_for`). On Stalwart 0.16 an ACE with only `read-free-busy` is stored, but the sharee
  neither lists the calendar nor may run a free-busy-query on it (403), so mailr would have nothing
  to show: the level is not offered there and a request for it is refused. On 1.0.1 the
  free-busy-query answers with the busy times and nothing else; calendar/shared_busy.py turns them
  into busy blocks (tests/test_calendar_sharing_stalwart.py).
- **The ACL.** Read, modify, write: an `ACL` request replaces the whole list. mailr writes it as
  the owner (impersonation) and puts the ACEs of mailr users first, in their old order, then every
  other ACE (a group, an account mailr does not know) in its old order. Privileges are written back
  in their base form (read, write, write-acl, CalDAV free/busy and schedule-*); the derived ones
  Stalwart reports (write-content, read-acl, …) are left out. After a write whose ACL names anything
  but a mailr user (a group), mailr runs `InvalidateCaches`; direct grants to a user take effect at
  once.
- **What a person may do** (`access_of`, verified on 0.16.27 and 1.0.1 on 2 November 2026 with a
  person's own ACE and a store group's ACE in either order). A person's grants are their own ACE
  and the ACE of every store group they are a member of (`store_groups`).
    - Stalwart 1.x: every check (read, free/busy, write, write-acl) takes the union of those grants.
      Own view plus a group's edit: the person writes.
    - Stalwart 0.16: read, free/busy and write-acl take the union too; write takes the first of
      those ACEs in the ACL's order. mailr puts people before groups, so a person's own grant
      decides writes when they have one.
  mailr follows the server it runs on (`server_v1`), so it never allows more than the server, and
  shows where a level comes from when a group gives more than the person's own grant
  ("Can edit (through Family)").
- **Re-sharing.** Someone who may change the calendar's ACL (write-acl through any of their
  grants, on both versions) manages the calendar's people like the owner, within the policy as it
  applies to them, except their own entry. mailr checks this in the owner's ACL and writes as the
  owner.
- **What the sharee sees.** The calendar appears in the sharee's home (assisted discovery). The
  sync records it in the connector state (`caldav.shared_in`: path → owner, level, can_share, own
  level, groups that give more) and stores it read-only when the person may not write by the rule
  above (on 0.16 the listing reports the union, its write check the first ACE, so the listing alone
  is not enough there). `overrides_for` marks it
  `shared = {kind: "user", owner, owner_id, level, access, can_share}`, plus `own_level` and `via`
  (group names) when a group gives more. A free/busy-only calendar is not listed; the sync adds it
  (calendar/shared_busy.py) with level `freebusy` and access `freebusy`. A group calendar shared
  with the user's group as free/busy only (calendar/groups.py) is added the same way
  (`_group_free_busy_only`); its `shared_in` entry carries `group_calendar_id` instead of an owner.
- **The owner's side.** Each sync of a built-in connector reads the ACLs of its own calendars in
  one depth-1 PROPFIND and keeps a summary (`caldav.acl`: path → [[login, level, is_user]]).
  The sharee's sync reads its level there; the owner's list shows "Shared with 2 people".
- **A deleted account.** Stalwart 0.16 answers 500 to an ACL read once an ACE names an account
  that was destroyed (0.16.23). Stalwart 1.x answers, and names that ACE's principal by the
  account's number (`/dav/pal/_2110/`). mailr removes a user's grants before it deletes their
  store account, and the job `calendar.shares_cleanup` removes any grant left to a missing account
  through JMAP (`Calendar/set` `shareWith/<id>: null`), which works where the DAV read fails.
  `read_acl` repairs both cases before it returns the list.
- **Notices.** A new share leaves a quiet notice for the sharee: a toast in an open session and a
  line in the calendar list until they dismiss it (`users.settings.calendar_share_notices`).
  No mail, no push, no desktop notification.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import builtin, dav, groups
from mailr.calendar.describe import calendar_name
from mailr.core.jobs import enqueue, handler
from mailr.db import session_scope
from mailr.models import CalCollection, Connector, InstanceSetting, User

log = logging.getLogger("mailr.calendar.sharing")

SETTING = "calendars.sharing"
POLICIES = ("anyone", "groups", "nobody")
DEFAULT_POLICY = "anyone"
FREEBUSY = "freebusy"
LEVELS = ("view", "edit", "share")  # on every server
LEVELS_V1 = (FREEBUSY, *LEVELS)  # Stalwart 1.x: free/busy only too
PRIVS = {FREEBUSY: ["read-free-busy"], "view": ["read"], "edit": ["read", "write"],
         "share": ["read", "write", "write-acl"]}
RANK = {FREEBUSY: 1, "view": 2, "edit": 3, "share": 4}
MAX_PEOPLE = 50  # provisioning sets x:Sharing.maxShares to 50
NOTICES = "calendar_share_notices"
CLEANUP_JOB = "calendar.shares_cleanup"

D, C = dav.NS["d"], dav.NS["c"]
# The privileges an ACL request takes; the others Stalwart reports are derived from these.
_BASE = {f"{{{D}}}read", f"{{{D}}}write", f"{{{D}}}write-acl", f"{{{C}}}read-free-busy"}


class SharingError(Exception):
    """A plain message for the person sharing; `status` is the HTTP answer."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------- the policy ----------------


async def policy(db: AsyncSession) -> str:
    row = await db.get(InstanceSetting, SETTING)
    return row.value if row is not None and row.value in POLICIES else DEFAULT_POLICY


async def set_policy(db: AsyncSession, value: str) -> None:
    if value not in POLICIES:
        raise ValueError(f"sharing is one of {', '.join(POLICIES)}")
    row = await db.get(InstanceSetting, SETTING)
    if row is None:
        db.add(InstanceSetting(key=SETTING, value=value))
    else:
        row.value = value


async def logins(db: AsyncSession) -> dict[str, int]:
    """Store login → mailr user id, for every user with a built-in connector."""
    rows = (await db.execute(select(Connector.user_id, Connector.config).where(Connector.builtin.is_(True)))).all()
    return {str((cfg or {}).get("account") or "").lower(): uid for uid, cfg in rows if (cfg or {}).get("account")}


def allowed_by(pol: str, actor: User, other: User) -> bool:
    """May `actor` share with `other` under the policy? (Both active users with calendars.)"""
    if pol == "nobody" or other.id == actor.id:
        return False
    if pol == "groups":
        from mailr.core.access import effective_groups

        return bool(set(effective_groups(actor)) & set(effective_groups(other)))
    return True


async def eligible(db: AsyncSession, actor: User) -> list[User]:
    """The people `actor` may share with: active users with a built-in calendar, by the policy."""
    pol = await policy(db)
    if pol == "nobody":
        return []
    have = set((await logins(db)).values())
    users = (await db.execute(select(User).where(User.disabled.is_(False), User.stalwart_account_id.is_not(None))
                              .order_by(User.name, User.email))).scalars().all()
    out = []
    for u in users:
        if u.id in have and allowed_by(pol, actor, u) and await builtin.policy_allows(db, u):
            out.append(u)
    return out


# ---------------- ACLs ----------------


@dataclass
class Ace:
    principal: str  # the principal's path, /dav/pal/<login>/
    privs: list[str] = field(default_factory=list)  # Clark names, e.g. "{DAV:}read"

    @property
    def login(self) -> str:
        p = self.principal.rstrip("/")
        return unquote(p.rsplit("/", 1)[-1]).lower() if "/dav/pal/" in p else ""

    @property
    def level(self) -> str | None:
        return level_of(self.privs)


def level_of(privs: list[str] | set[str] | frozenset[str]) -> str | None:
    """The level an ACE (Clark names) or a listing's privilege set (local names) amounts to."""
    names = {p.rsplit("}", 1)[-1] for p in privs}
    if "write-acl" in names or "all" in names:
        return "share"
    if "write" in names or "write-content" in names:
        return "edit"
    if "read" in names:
        return "view"
    if "read-free-busy" in names:
        return FREEBUSY
    return None


async def server_v1() -> bool:
    """Whether the mail store runs Stalwart 1.x (core/store_groupware.is_v1, kept five minutes).
    False when it cannot be asked: 0.16's rules never allow more than 1.x's."""
    from mailr.core.store import service_client
    from mailr.core.store_groupware import is_v1

    try:
        return await is_v1(service_client())
    except Exception as e:  # noqa: BLE001
        log.info("could not tell the mail store's version: %s", e)
        return False


def levels_for(v1: bool) -> list[str]:
    return list(LEVELS_V1 if v1 else LEVELS)


@dataclass
class Access:
    """What a person may do with a calendar, by all their grants (`access_of`)."""

    level: str | None  # freebusy, view, edit or share; None: nothing
    can_share: bool = False
    own: str | None = None  # the level of the person's own ACE
    via: list[str] = field(default_factory=list)  # logins of the groups that give more than `own`


def access_of(entries: list[tuple[str, str | None]] | list[list[Any]], me: str, my_groups: set[str] | dict[str, str],
              v1: bool) -> Access:
    """A person's access from an ACL's entries ([login, level, …] in ACL order), their own login
    and the logins of their store groups. Stalwart 1.x: the union of the grants. Stalwart 0.16: the
    union too, except write, which the first ACE naming the person or one of their groups decides
    (verified on both, 2 November 2026; the module docstring)."""
    me = me.lower()
    mine = [(str(e[0]).lower(), e[1]) for e in entries if e[1] and (str(e[0]).lower() == me or str(e[0]).lower() in my_groups)]
    if not mine:
        return Access(None)
    own = next((lv for lg, lv in mine if lg == me), None)
    best = max(RANK[lv] for _, lv in mine)
    can_share = any(lv == "share" for _, lv in mine)
    write = (any(RANK[lv] >= RANK["edit"] for _, lv in mine) if v1 else RANK[mine[0][1]] >= RANK["edit"])
    if write:
        level = "share" if can_share else "edit"
    else:
        level = "view" if best >= RANK["view"] else FREEBUSY
    own_rank = RANK.get(own or "", 0)
    raised = RANK[level] > own_rank or (can_share and own != "share")
    via = list(dict.fromkeys(lg for lg, lv in mine if lg != me and RANK[lv] > own_rank)) if raised else []
    return Access(level, can_share, own, via)


async def store_groups(db: AsyncSession, users: list[User]) -> dict[int, dict[str, str]]:
    """Per user id: {store group login: name to show} for the store groups the person is a member
    of. A group of mailr's group calendars shows its mailr group name; another one its description,
    else its account name. One or two management calls for all the people."""
    from mailr.config import get_settings
    from mailr.models import CalendarGroup

    accts = {u.stalwart_account_id: u.id for u in users if u.stalwart_account_id}
    out: dict[int, dict[str, str]] = {u.id: {} for u in users}
    if not accts:
        return out
    got = await groups._mgmt("x:Account/get", {"ids": list(accts), "properties": ["memberGroupIds"]})
    member = {a["id"]: [g for g, on in (a.get("memberGroupIds") or {}).items() if on] for a in got.get("list") or []}
    gids = sorted({g for gs in member.values() for g in gs})
    if not gids:
        return out
    info = await groups._mgmt("x:Account/get", {"ids": gids, "properties": ["name", "description"]})
    domain = get_settings().stalwart_domain.lower()
    managed = {(cg.store_group_login or "").lower(): cg.group_name
               for cg in (await db.execute(select(CalendarGroup))).scalars() if cg.store_group_login}
    names: dict[str, tuple[str, str]] = {}
    for g in info.get("list") or []:
        name = str(g.get("name") or "")
        login = (name if "@" in name else f"{name}@{domain}").lower()
        names[g["id"]] = (login, managed.get(login) or str(g.get("description") or "") or name.split("@")[0])
    for acct, gs in member.items():
        uid = accts.get(acct)
        if uid is not None:
            out[uid] = {names[g][0]: names[g][1] for g in gs if g in names}
    return out


def _base(privs: list[str]) -> list[str]:
    """The privileges an ACL request takes (the derived ones left out), in a stable order."""
    keep = [p for p in privs if p in _BASE or (p.startswith(f"{{{C}}}schedule-"))]
    return list(dict.fromkeys(keep))


def parse_acl(resp: ET.Element) -> list[Ace]:
    """The grant ACEs of one multistatus response, in order."""
    out = []
    for ace in resp.findall(".//d:acl/d:ace", dav.NS):
        href = dav._text(ace.find("d:principal/d:href", dav.NS))
        if not href:
            continue  # all, authenticated, self: never written by mailr
        privs = [p.tag for g in ace.findall("d:grant", dav.NS) for x in g.findall("d:privilege", dav.NS) for p in x]
        out.append(Ace(groups.path_of(href).rstrip("/") + "/", privs))
    return out


def ace_for(login: str, level: str) -> Ace:
    return Ace(groups.principal_of(login), [f"{{{C if p == 'read-free-busy' else D}}}{p}" for p in PRIVS[level]])


def ordered(aces: list[Ace], user_logins: set[str] | dict[str, int]) -> list[Ace]:
    """mailr users' ACEs first (old order), then the rest (old order)."""
    mine = [a for a in aces if a.login in user_logins]
    return mine + [a for a in aces if a.login not in user_logins]


def with_grant(aces: list[Ace], login: str, level: str | None, user_logins: set[str] | dict[str, int]) -> list[Ace]:
    """The ACL with `login` at `level` (None: removed). The person's entry keeps its place."""
    login = login.lower()
    out: list[Ace] = []
    placed = False
    for a in aces:
        if a.login == login:
            if level and not placed:
                out.append(ace_for(login, level))
                placed = True
            continue
        out.append(a)
    if level and not placed:
        out.append(ace_for(login, level))
    return ordered(out, user_logins)


def acl_body(aces: list[Ace]) -> str:
    parts = []
    for a in aces:
        privs = _base(a.privs)
        if not privs:
            continue
        p = "".join(f"<d:privilege><{'c' if x.startswith('{' + C) else 'd'}:{x.rsplit('}', 1)[-1]}/></d:privilege>"
                    for x in privs)
        parts.append(f"<d:ace><d:principal><d:href>{a.principal}</d:href></d:principal><d:grant>{p}</d:grant></d:ace>")
    return (f'<?xml version="1.0" encoding="utf-8"?><d:acl xmlns:d="{D}" xmlns:c="{C}">'
            f"{''.join(parts)}</d:acl>")


def summary(aces: list[Ace], user_logins: set[str] | dict[str, int]) -> list[list[Any]]:
    """What the connector state keeps of an ACL: [login, level, is_user] per ACE, in order."""
    return [[a.login, a.level, a.login in user_logins] for a in aces if a.level]


async def _read(owner_login: str, path: str) -> list[Ace]:
    async with groups.owner_client(owner_login) as c:
        ms = await c._multistatus("PROPFIND", groups._url(path), dav._propfind_body(["d:acl"]), "0")
    resp = ms.find("d:response", dav.NS)
    return parse_acl(resp) if resp is not None else []


# How Stalwart 1.x names the principal of a grant whose account was destroyed: its number.
_GONE = re.compile(r"_\d+")


async def read_acl(owner: User, owner_login: str, path: str) -> list[Ace]:
    """The calendar's ACL as its owner sees it. An ACE naming a destroyed account (a 5xx on
    Stalwart 0.16, a numbered principal on 1.x) is repaired through JMAP and the read repeated once."""
    try:
        aces = await _read(owner_login, path)
    except dav.ServerError as e:
        if e.status < 500:
            raise
        log.info("ACL read of %s failed (%s); removing grants to missing accounts", path, e)
    else:
        if not any(_GONE.fullmatch(a.login) for a in aces):
            return aces
        log.info("ACL of %s names a deleted account; removing grants to missing accounts", path)
    await heal(owner)
    return await _read(owner_login, path)


async def write_acl(owner_login: str, path: str, aces: list[Ace], user_logins: set[str] | dict[str, int]) -> None:
    async with groups.owner_client(owner_login) as c:
        r = await c.request("ACL", groups._url(path), body=acl_body(aces),
                            headers={"Content-Type": "application/xml; charset=utf-8"})
        c._raise(r, f"ACL {path}")
    if any(a.login not in user_logins for a in aces):
        # A group in the ACL: a first share to it stays invisible to its members until then.
        await groups.invalidate_caches()


async def owner_summaries(client: dav.DavClient, home: str, user_logins: dict[str, int]) -> dict[str, list] | None:
    """Every own calendar's ACL summary in one depth-1 PROPFIND (for the connector state).
    None when the server refused (an ACE naming a destroyed account answers 500)."""
    try:
        ms = await client._multistatus("PROPFIND", client.abs(home), dav._propfind_body(["d:acl"]), "1")
    except dav.DavError as e:
        log.info("could not read the ACLs under %s: %s", home, e)
        return None
    own = groups.path_of(home)
    out: dict[str, list] = {}
    for resp in ms.findall("d:response", dav.NS):
        path = groups.path_of(dav._text(resp.find("d:href", dav.NS)))
        if path == own or not path.startswith(own):
            continue
        s = summary(parse_acl(resp), user_logins)
        if s:
            out[path] = s
    return out


# ---------------- grants to accounts that no longer exist ----------------


_JMAP_CAL = "urn:ietf:params:jmap:calendars"


async def heal(owner: User) -> int:
    """Remove from the owner's calendars every grant to an account that no longer exists.
    JMAP, because the DAV ACL read fails while such a grant is there (0.16). On Stalwart 1.x the
    DAV read works and JMAP's shareWith leaves such a grant out, so the DAV ACL is rewritten
    without it (`_heal_numbered`). Returns how many."""
    from mailr.core.jmap import CORE
    from mailr.core.store import service_client

    if not owner.stalwart_account_id:
        return 0
    return await _heal_shared_with(owner, service_client(), [CORE, _JMAP_CAL]) + await _heal_numbered(owner)


async def _heal_numbered(owner: User) -> int:
    """Stalwart 1.x: grants to destroyed accounts that the owner's DAV ACLs still list under the
    account's number (`_GONE`). Each such ACL is written again without them."""
    async with session_scope() as db:
        login = await groups.user_login(db, owner)
        who = await logins(db)
    if not login:
        return 0
    try:
        async with groups.owner_client(login) as c:
            ms = await c._multistatus("PROPFIND", groups._url(groups.home_of(login)), dav._propfind_body(["d:acl"]), "1")
    except dav.DavError as e:  # 0.16 with such a grant: the JMAP repair above runs first
        log.info("could not read the ACLs of user %s: %s", owner.id, e)
        return 0
    n = 0
    for resp in ms.findall("d:response", dav.NS):
        aces = parse_acl(resp)
        keep = [a for a in aces if not _GONE.fullmatch(a.login)]
        if len(keep) == len(aces):
            continue
        path = groups.path_of(dav._text(resp.find("d:href", dav.NS)))
        await write_acl(login, path, keep, who)
        n += len(aces) - len(keep)
    if n:
        log.info("removed %s grants to deleted accounts from the calendar ACLs of user %s", n, owner.id)
    return n


async def _heal_shared_with(owner: User, cli: Any, using: list[str]) -> int:
    got = await cli.one("Calendar/get", {"accountId": owner.stalwart_account_id, "properties": ["shareWith"]},
                        using=using)
    cals = got.get("list") or []
    ids = sorted({i for c in cals for i in (c.get("shareWith") or {})})
    if not ids:
        return 0
    from mailr.core.users import _mgmt

    r = await _mgmt("x:Account/get", {"ids": ids, "properties": ["name"]})
    gone = set(r.get("notFound") or [])
    update = {c["id"]: {f"shareWith/{i}": None for i in (c.get("shareWith") or {}) if i in gone}
              for c in cals}
    update = {k: v for k, v in update.items() if v}
    if not update:
        return 0
    res = await cli.one("Calendar/set", {"accountId": owner.stalwart_account_id, "update": update}, using=using)
    if res.get("notUpdated"):
        log.warning("could not remove grants to deleted accounts from the calendars of user %s: %s", owner.id,
                    res["notUpdated"])
    n = sum(len(update[k]) for k in (res.get("updated") or {}))
    log.info("removed %s grants to deleted accounts from the calendars of user %s", n, owner.id)
    return n


async def cleanup() -> dict[str, int]:
    """Every owner: grants to missing accounts removed. Runs after a user is deleted and at start."""
    out = {"owners": 0, "removed": 0, "failed": 0}
    async with session_scope() as db:
        if not await builtin.instance_enabled(db):
            return out
        ids = (await db.execute(select(User.id).join(Connector, Connector.user_id == User.id)
                                .where(Connector.builtin.is_(True), User.stalwart_account_id.is_not(None))
                                .order_by(User.id))).scalars().all()
    for uid in ids:
        try:
            async with session_scope() as db:
                u = await db.get(User, uid)
                if u is not None:
                    out["owners"] += 1
                    out["removed"] += await heal(u)
        except Exception as e:  # noqa: BLE001  one account must not stop the others
            out["failed"] += 1
            log.warning("could not check the calendar grants of user %s: %s", uid, e)
    return out


@handler(CLEANUP_JOB)
async def cleanup_job(payload: dict[str, Any], ctx) -> None:
    from mailr.core.jobs import Retry

    if (await cleanup())["failed"]:
        raise Retry(delay=600)


async def request_cleanup(db: AsyncSession) -> None:
    await enqueue(db, CLEANUP_JOB, {}, dedupe_key=CLEANUP_JOB)


async def user_removed(db: AsyncSession, user: User) -> None:
    """Before a user's store account is destroyed: take their grants off other people's calendars
    (while the DAV ACL can still be read), and queue a sync of the people their own calendars were
    shared with. Best effort; the cleanup job catches the rest."""
    me = await groups.user_login(db, user) if user.stalwart_account_id else ""
    if not me:
        return
    who = await logins(db)
    owners = (await db.execute(select(Connector).where(Connector.builtin.is_(True), Connector.user_id != user.id))
              ).scalars().all()
    for conn in owners:
        acl = ((conn.state or {}).get("caldav") or {}).get("acl") or {}
        paths = [p for p, entries in acl.items() if any(e[0] == me for e in entries)]
        if not paths:
            continue
        owner = await db.get(User, conn.user_id)
        login = (conn.config or {}).get("account") or ""
        for path in paths:
            try:
                aces = await read_acl(owner, login, path)
                new = with_grant(aces, me, None, who)
                await write_acl(login, path, new, who)
                await _remember(db, conn, path, new, who)
            except Exception as e:  # noqa: BLE001
                log.warning("could not remove the grant of user %s on %s: %s", user.id, path, e)
    own = await builtin.find(db, user.id)
    sharees = {who[e[0]] for entries in (((own.state or {}).get("caldav") or {}).get("acl") or {}).values()
               for e in entries if e[0] in who and who[e[0]] != user.id} if own else set()
    await groups.queue_syncs(db, sharees)
    await request_cleanup(db)


# ---------------- a calendar someone shares ----------------


@dataclass
class Target:
    coll: CalCollection
    path: str
    owner: User
    owner_login: str
    own: bool  # the actor owns it (else the actor re-shares with the share level)


async def target_for(db: AsyncSession, user: User, coll: CalCollection) -> Target:
    conn = await db.get(Connector, coll.connector_id)
    if conn is None or not conn.builtin:
        raise SharingError("Only calendars of your Mailr calendar account can be shared.")
    me = ((conn.config or {}).get("account") or "").lower()
    path = groups.path_of(coll.href)
    if path.startswith(groups.home_of(me)):
        if "VEVENT" not in [c.upper() for c in coll.components or []]:
            raise SharingError("Only calendars can be shared, not task lists.")
        return Target(coll, path, user, me, True)
    if any(path.startswith(h) for h in await groups.managed_homes(db)):
        raise SharingError("This is a group calendar. An administrator decides who sees it.", 403)
    owner_login = unquote(path.split("/")[3]).lower() if path.count("/") >= 4 else ""
    owner_id = (await logins(db)).get(owner_login)
    owner = await db.get(User, owner_id) if owner_id else None
    if owner is None:
        raise SharingError("This calendar's owner is not on this server any more.", 403)
    return Target(coll, path, owner, owner_login, False)


def _person(u: User) -> dict[str, Any]:
    return {"id": u.id, "name": u.name or u.email.split("@")[0], "email": u.email}


def _entries(aces: list[Ace]) -> list[tuple[str, str | None]]:
    return [(a.login, a.level) for a in aces if a.level]


async def _groups_if_needed(db: AsyncSession, people: list[User], aces: list[Ace],
                            who: dict[str, int]) -> dict[int, dict[str, str]]:
    """The people's store groups, asked of the store only when the ACL grants anything to an
    account that is not a mailr user (a group)."""
    if not any(a.level and a.login not in who for a in aces):
        return {u.id: {} for u in people}
    return await store_groups(db, people)


async def _actor_may_share(db: AsyncSession, t: Target, actor: User, actor_login: str, aces: list[Ace],
                           who: dict[str, int], v1: bool) -> None:
    """The owner, or someone whose grants give write-acl (the person's own, or a group's: the
    server checks the union on 0.16 and on 1.x)."""
    if t.own:
        return
    mine = (await _groups_if_needed(db, [actor], aces, who)).get(actor.id) or {}
    if not access_of(_entries(aces), actor_login, mine, v1).can_share:
        raise SharingError(f"{_person(t.owner)['name']} has not let you share “{_name(t.coll)}”.", 403)


def _name(coll: CalCollection) -> str:
    return calendar_name(coll.displayname, coll.href)


async def describe(db: AsyncSession, actor: User, coll: CalCollection) -> dict[str, Any]:
    """The Share dialog: the people the calendar is shared with, their levels, and who else may
    be added under the policy."""
    t = await target_for(db, actor, coll)
    who = await logins(db)
    me = await groups.user_login(db, actor)
    aces = await read_acl(t.owner, t.owner_login, t.path)
    v1 = await server_v1()
    await _actor_may_share(db, t, actor, me, aces, who, v1)
    by_id = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(
        [who[a.login] for a in aces if a.login in who])))).scalars()}
    member = await _groups_if_needed(db, list(by_id.values()), aces, who)
    login_of = {v: k for k, v in who.items()}
    entries = _entries(aces)
    pol = await policy(db)
    people, seen, others = [], set(), 0
    for a in aces:
        uid = who.get(a.login)
        if uid is None:
            others += 1 if a.level else 0
            continue
        if uid in seen or not a.level:
            continue
        seen.add(uid)
        u = by_id.get(uid)
        if u is None:
            continue
        row = {**_person(u), "level": a.level, "disabled": bool(u.disabled), "you": uid == actor.id,
               "outside_policy": uid != actor.id and not allowed_by(pol, actor, u)}
        mine = member.get(uid) or {}
        acc = access_of(entries, login_of.get(uid, a.login), mine, v1)
        if acc.via:  # a group gives this person more than the level set here
            row["access"] = {"level": acc.level, "can_share": acc.can_share, "via": [mine.get(g, g) for g in acc.via]}
        people.append(row)
    cands = [_person(u) for u in await eligible(db, actor) if u.id not in seen and u.id != t.owner.id]
    return {"calendar_id": coll.id, "name": _name(coll), "own": t.own, "owner": _person(t.owner),
            "policy": pol, "levels": levels_for(v1), "people": people, "others": others,
            "candidates": cands, "max": MAX_PEOPLE}


async def set_level(db: AsyncSession, actor: User, coll: CalCollection, user_id: int, level: str | None) -> dict[str, Any]:
    """Share with a person at a level, change it, or stop (level None). The caller commits."""
    if level is not None and level not in LEVELS_V1:
        raise SharingError("The level is freebusy, view, edit or share.")
    v1 = await server_v1()
    if level is not None and level not in levels_for(v1):
        raise SharingError("This server cannot share free/busy only yet. Choose View, Edit or Edit and share.")
    pol = await policy(db)
    if level is not None and pol == "nobody":
        raise SharingError("Calendar sharing is turned off on this server.", 403)
    t = await target_for(db, actor, coll)
    other = await db.get(User, user_id)
    if other is None:
        raise SharingError("That person is not on this server.", 404)
    if other.id == t.owner.id:
        raise SharingError("That person owns this calendar.")
    if other.id == actor.id:
        raise SharingError("Only the owner can change your access.")
    who = await logins(db)
    login = next((k for k, v in who.items() if v == other.id), "")
    if not login:
        raise SharingError("That person has no Mailr calendar.")
    me = await groups.user_login(db, actor)
    aces = await read_acl(t.owner, t.owner_login, t.path)
    await _actor_may_share(db, t, actor, me, aces, who, v1)
    before = next((a.level for a in aces if a.login == login), None)
    if level is not None:
        if other.disabled or not allowed_by(pol, actor, other) or not await builtin.policy_allows(db, other):
            raise SharingError(f"You cannot share calendars with {_person(other)['name']} on this server.", 403)
        if before is None and sum(1 for a in aces if a.login in who) >= MAX_PEOPLE:
            raise SharingError(f"A calendar can be shared with at most {MAX_PEOPLE} people.")
    if before == level:
        return await describe(db, actor, coll)
    new = with_grant(aces, login, level, who)
    try:
        await write_acl(t.owner_login, t.path, new, who)
    except dav.DavError as e:
        raise SharingError(f"The calendar server did not accept the change: {e}"[:300], 502) from None
    owner_conn = await builtin.find(db, t.owner.id)
    if owner_conn is not None:
        await _remember(db, owner_conn, t.path, new, who)
    if level is None:
        await _drop_notice(db, other.id, t.path)
    elif before is None and not other.disabled:
        await _add_notice(db, other, t, actor)
    await groups.queue_syncs(db, [other.id])
    log.info("calendar %s: user %s %s by user %s", t.path, other.id, level or "removed", actor.id)
    return await describe(db, actor, coll)


async def _remember(db: AsyncSession, conn: Connector, path: str, aces: list[Ace], who: dict[str, int]) -> None:
    """Keep the owner's ACL summary in the connector state (read by sharees' syncs and lists)."""
    await db.refresh(conn, ["state"])
    st = dict(conn.state or {})
    cd = dict(st.get("caldav") or {})
    acl = dict(cd.get("acl") or {})
    s = summary(aces, who)
    if s:
        acl[path] = s
    else:
        acl.pop(path, None)
    cd["acl"] = acl
    conn.state = {**st, "caldav": cd}
    await db.flush()


# ---------------- notices ----------------


async def _add_notice(db: AsyncSession, other: User, t: Target, actor: User) -> None:
    from mailr.core import share_notices
    from mailr.core.events import notify

    note = {"path": t.path, "name": _name(t.coll), "by": _person(actor)["name"], "owner": _person(t.owner)["name"],
            "at": datetime.now(UTC).isoformat()}
    await share_notices.add(db, NOTICES, other.id, note)
    from mailr import i18n

    await notify(db, other.id, {"type": "notification", "kind": "calendar_share",
                                "title": i18n.t("notify.shared", i18n.user_language(other), by=note["by"], name=note["name"])})


async def _drop_notice(db: AsyncSession, user_id: int, path: str | None) -> None:
    from mailr.core import share_notices

    await share_notices.drop(db, NOTICES, user_id, path)


async def notices(db: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    from mailr.core import share_notices

    return await share_notices.listed(db, NOTICES, user_id)


async def dismiss(db: AsyncSession, user_id: int) -> None:
    await _drop_notice(db, user_id, None)


# ---------------- the sync of a built-in connector ----------------


@dataclass
class Listing:
    """What a built-in sync learns about sharing (`on_listing`)."""

    shared_in: dict[str, Any]  # path → owner_id, level, can_share, own, via, direct
    acl: dict[str, Any] | None  # the user's own calendars' ACL summaries; None: could not be read
    busy: list[tuple[dav.CollectionInfo, list[Any]]] = field(default_factory=list)  # free/busy only: (info, periods)
    kept: list[dav.CollectionInfo] = field(default_factory=list)  # free/busy only, not refreshed this time


def _listed_level(info: dav.CollectionInfo) -> str:
    """The level the listing reports for the current user (the union of their grants)."""
    if info.privileges:
        return level_of(info.privileges) or "view"
    return "view" if info.read_only else "edit"


async def on_listing(client: dav.DavClient, user_id: int, home: str, infos: list[dav.CollectionInfo],
                     previous: dict[str, Any] | None = None) -> Listing:
    """During a sync: the owner summaries of the user's own calendars, the calendars shared with
    the user (path → owner, level), and the calendars shared with the user as free/busy only
    (Stalwart 1.x; calendar/shared_busy.py). A calendar the user may not write to by the server's
    rule (`access_of`) is set read-only in `infos`. `previous` is the last `shared_in`: a
    free/busy-only calendar whose busy times cannot be read this time (a server error) is kept."""
    v1 = await server_v1()
    async with session_scope() as db:
        who = await logins(db)
        user = await db.get(User, user_id)
        if user is None:
            return Listing({}, None)
        me = await groups.user_login(db, user)
        managed = await groups.managed_homes(db)
        own_home = groups.path_of(home)
        foreign: dict[str, dav.CollectionInfo] = {}
        for i in infos:
            p = groups.path_of(i.href)
            if p.startswith(own_home) or any(p.startswith(h) for h in managed):
                continue
            foreign[p] = i
        # Every other owner's summaries: listed calendars, and free/busy-only grants (not listed).
        states = {c.user_id: ((c.state or {}).get("caldav") or {}).get("acl") or {}
                  for c in (await db.execute(select(Connector).where(Connector.builtin.is_(True),
                                                                      Connector.user_id != user_id))).scalars()}
        need_groups = any(not e[2] for acl in states.values() for entries in acl.values() for e in entries
                          if len(e) > 2 and e[1])
        mine = (await store_groups(db, [user])).get(user_id, {}) if need_groups else {}
    shared_in: dict[str, Any] = {}
    for p, info in foreign.items():
        owner_login = unquote(p.split("/")[3]).lower() if p.count("/") >= 4 else ""
        oid = who.get(owner_login)
        if oid is None or oid == user_id:
            continue
        entries = states.get(oid, {}).get(p) or []
        acc = access_of(entries, me, mine, v1) if entries else Access(None)
        listed = _listed_level(info)
        if v1 or acc.level is None:
            # Stalwart 1.x: the listing reports the union, as the server's checks use it.
            level, can_share = listed, "write-acl" in info.privileges or listed == "share"
        else:
            # Stalwart 0.16: the listing reports the union, but writes follow the first ACE.
            level, can_share = acc.level, acc.can_share
            if RANK[level] < RANK["edit"]:
                info.read_only = True
        if level == FREEBUSY:  # listed, so at least view through some grant
            level = "view"
        shared_in[p] = {"owner_id": oid, "level": level, "can_share": can_share, "own": acc.own,
                        "via": [mine.get(g, g) for g in acc.via], "direct": acc.own is not None}
    out = Listing(shared_in, None)
    if v1:
        await _free_busy_only(client, user_id, me, mine, states, foreign, own_home, managed, previous or {}, out)
        await _group_free_busy_only(client, user_id, {groups.path_of(i.href) for i in infos}, previous or {}, out)
    acl = await owner_summaries(client, home, who)
    if acl is None:
        try:
            async with session_scope() as db:
                u = await db.get(User, user_id)
                if u is not None and await heal(u):
                    acl = await owner_summaries(client, home, who)
        except Exception as e:  # noqa: BLE001  the next sync tries again
            log.warning("could not repair the calendar grants of user %s: %s", user_id, e)
    out.acl = acl
    return out


async def _free_busy_only(client: dav.DavClient, user_id: int, me: str, mine: dict[str, str],
                          states: dict[int, dict[str, Any]], foreign: dict[str, Any], own_home: str, managed: set[str],
                          previous: dict[str, Any], out: Listing) -> None:
    """The calendars the user may see as free/busy only (not listed), with their busy periods,
    asked of the server as the user. A refusal leaves the calendar out."""
    from mailr.calendar import shared_busy

    want: dict[str, tuple[int, Access]] = {}
    for oid, acl in states.items():
        if oid == user_id:
            continue
        for p, entries in acl.items():
            if p in foreign or p.startswith(own_home) or any(p.startswith(h) for h in managed):
                continue
            acc = access_of(entries, me, mine, True)
            if acc.level == FREEBUSY:
                want[p] = (oid, acc)
    if not want:
        return
    async with session_scope() as db:  # the owners' names and colours for the calendars
        rows = (await db.execute(select(CalCollection).join(Connector, Connector.id == CalCollection.connector_id).where(
            Connector.builtin.is_(True), Connector.user_id.in_({o for o, _ in want.values()}),
            CalCollection.deleted_at.is_(None)))).scalars().all()
        meta = {groups.path_of(c.href): c for c in rows}
    start, end = shared_busy.window()
    for p, (oid, acc) in want.items():
        c = meta.get(p)
        href = client.abs(p) if client is not None else p
        info = dav.CollectionInfo(href=href, displayname=(c.displayname if c else "") or "",
                                  color=c.color if c else None, components=["VEVENT"], read_only=True,
                                  privileges=frozenset({"read-free-busy"}))
        entry = {"owner_id": oid, "level": FREEBUSY, "can_share": False, "own": acc.own,
                 "via": [mine.get(g, g) for g in acc.via], "direct": acc.own is not None}
        try:
            periods = await shared_busy.query(client, href, start, end)
        except shared_busy.Refused as e:
            log.info("free/busy of %s not readable for user %s: %s", p, user_id, e)
            continue
        except Exception as e:  # noqa: BLE001  kept as it was until the next sync
            log.warning("could not read the free/busy of %s for user %s: %s", p, user_id, e)
            if (previous.get(p) or {}).get("level") == FREEBUSY:
                out.kept.append(info)
                out.shared_in[p] = entry
            continue
        out.busy.append((info, periods))
        out.shared_in[p] = entry


async def _group_free_busy_only(client: dav.DavClient, user_id: int, listed: set[str], previous: dict[str, Any],
                                out: Listing) -> None:
    """Group calendars shared with the user's group as free/busy only (calendar/groups.py) that the
    listing leaves out: their busy periods, asked of the server as the user. A listed one (an
    editor's, or one the user may read through an own grant: Stalwart 1.x takes the union, as
    `access_of`) syncs in full instead. A refusal (not yet in the store group) leaves it out."""
    from mailr.calendar import shared_busy
    from mailr.core.access import effective_groups
    from mailr.models import GroupCalendar

    async with session_scope() as db:
        user = await db.get(User, user_id)
        if user is None or user.disabled:
            return
        mine = set(effective_groups(user))
        gcs = [gc for gc in (await db.execute(select(GroupCalendar).where(
            GroupCalendar.access == groups.FREEBUSY, GroupCalendar.href.is_not(None)))).scalars()
            if gc.group_name in mine and groups.path_of(gc.href or "") not in listed]
    if not gcs:
        return
    start, end = shared_busy.window()
    for gc in gcs:
        p = groups.path_of(gc.href or "")
        href = client.abs(p) if client is not None else p
        info = dav.CollectionInfo(href=href, displayname=gc.name, color=gc.color, components=["VEVENT"],
                                  read_only=True, privileges=frozenset({"read-free-busy"}))
        entry = {"owner_id": None, "group_calendar_id": gc.id, "group": gc.group_name, "level": FREEBUSY,
                 "can_share": False, "own": None, "via": [], "direct": False}
        try:
            periods = await shared_busy.query(client, href, start, end)
        except shared_busy.Refused as e:
            log.info("free/busy of group calendar %s not readable for user %s: %s", gc.id, user_id, e)
            continue
        except Exception as e:  # noqa: BLE001  kept as it was until the next sync
            log.warning("could not read the free/busy of group calendar %s for user %s: %s", gc.id, user_id, e)
            if (previous.get(p) or {}).get("level") == FREEBUSY:
                out.kept.append(info)
                out.shared_in[p] = entry
            continue
        out.busy.append((info, periods))
        out.shared_in[p] = entry


# ---------------- what the user's calendar list shows ----------------


async def overrides_for(db: AsyncSession, user_id: int) -> dict[str, dict[str, Any]]:
    """Per collection href of the user's built-in connector: `shared` for a calendar someone
    shares with the user, `sharing` for the user's own calendar shared with others."""
    conn = await builtin.find(db, user_id)
    if conn is None:
        return {}
    st = (conn.state or {}).get("caldav") or {}
    shared_in, acl = st.get("shared_in") or {}, st.get("acl") or {}
    if not shared_in and not acl:
        return {}
    colls = (await db.execute(select(CalCollection.href).where(CalCollection.connector_id == conn.id,
                                                               CalCollection.deleted_at.is_(None)))).scalars().all()
    owners = {}
    ids = {v.get("owner_id") for v in shared_in.values() if v.get("owner_id")}
    if ids:
        owners = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars()}
    out: dict[str, dict[str, Any]] = {}
    for href in colls:
        p = groups.path_of(href)
        if p in shared_in and (shared_in[p] or {}).get("group_calendar_id"):
            continue  # a group calendar's busy blocks: groups.overrides_for marks it
        if p in shared_in:
            s = shared_in[p]
            o = owners.get(s.get("owner_id"))
            lvl = s.get("level") or "view"
            shared = {"kind": "user", "owner": _person(o)["name"] if o else "someone",
                      "owner_id": s.get("owner_id"), "level": lvl,
                      "access": FREEBUSY if lvl == FREEBUSY else "read" if lvl == "view" else "write",
                      "can_share": bool(s.get("can_share", lvl == "share"))}
            if s.get("via"):  # a group gives more than the person's own grant
                shared.update({"own_level": s.get("own"), "via": list(s["via"])})
            out[href] = {"shared": shared}
        elif p in acl:
            people = sum(1 for e in acl[p] if e[2])
            others = sum(1 for e in acl[p] if not e[2])
            out[href] = {"sharing": {"people": people, "others": others}}
    return out
