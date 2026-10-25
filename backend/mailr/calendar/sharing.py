"""Sharing a built-in calendar with other people on the server.

- **What can be shared.** A user's own calendars on the built-in connector (events; the Tasks
  list is not offered). Group calendars and external accounts are never shared from here.
- **With whom.** Other mailr users, picked from mailr's own user list, never Stalwart's directory.
  The instance setting `calendars.sharing` limits the list: "anyone" (every active user with a
  built-in calendar, the default), "groups" (users who share at least one mailr group with the
  person sharing) or "nobody". A narrower policy applies to new shares and level changes;
  existing shares stay until someone stops them.
- **Levels.** view (`read`), edit (`read` + `write`) and share (`read` + `write` + `write-acl`).
  A free/busy-only level is not offered: on Stalwart 0.16.23 an ACE with only CalDAV
  `read-free-busy` is stored, but the sharee does not list the calendar, a free-busy-query on it
  answers 403, and the schedule outbox's VFREEBUSY reply carries no busy times from it, so
  mailr would have nothing to show (tests/test_calendar_sharing_stalwart.py).
- **The ACL.** Read, modify, write: an `ACL` request replaces the whole list. mailr writes it as
  the owner (impersonation) and puts the ACEs of mailr users first, in their old order, then every
  other ACE (a group, an account mailr does not know) in its old order. Stalwart takes the first
  ACE that names the user, so a person's own level wins over a group's grant. Privileges are
  written back in their base form (read, write, write-acl, CalDAV free/busy and schedule-*);
  the derived ones Stalwart reports (write-content, read-acl, …) are left out.
  After a write whose ACL names anything but a mailr user (a group), mailr runs
  `InvalidateCaches`; direct grants to a user take effect at once.
- **Re-sharing.** Someone with the share level manages the calendar's people like the owner,
  within the policy as it applies to them, except their own entry. mailr checks their level in
  the owner's ACL and writes as the owner.
- **What the sharee sees.** The calendar appears in the sharee's home (assisted discovery). The
  sync records it in the connector state (`caldav.shared_in`: path → owner, level), and a
  view-only share is stored read-only even when a group grant would allow more (Stalwart's
  listing reports the union, its write check the first ACE). `overrides_for` marks it
  `shared = {kind: "user", owner, owner_id, level, access, can_share}`.
- **The owner's side.** Each sync of a built-in connector reads the ACLs of its own calendars in
  one depth-1 PROPFIND and keeps a summary (`caldav.acl`: path → [[login, level, is_user]]).
  The sharee's sync reads its level there; the owner's list shows "Shared with 2 people".
- **A deleted account.** Stalwart answers 500 to an ACL read once an ACE names an account that
  was destroyed (0.16.23). mailr removes a user's grants before it deletes their store account,
  and the job `calendar.shares_cleanup` removes any grant left to a missing account through JMAP
  (`Calendar/set` `shareWith/<id>: null`), which works where the DAV read fails.
- **Notices.** A new share leaves a quiet notice for the sharee: a toast in an open session and a
  line in the calendar list until they dismiss it (`users.settings.calendar_share_notices`).
  No mail, no push, no desktop notification.
"""

from __future__ import annotations

import logging
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
LEVELS = ("view", "edit", "share")
PRIVS = {"view": ["read"], "edit": ["read", "write"], "share": ["read", "write", "write-acl"]}
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


def level_of(privs: list[str] | set[str]) -> str | None:
    names = {p.rsplit("}", 1)[-1] for p in privs}
    if "write-acl" in names:
        return "share"
    if "write" in names:
        return "edit"
    if "read" in names:
        return "view"
    return None


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
    return Ace(groups.principal_of(login), [f"{{{D}}}{p}" for p in PRIVS[level]])


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


async def read_acl(owner: User, owner_login: str, path: str) -> list[Ace]:
    """The calendar's ACL as its owner sees it. A 5xx (an ACE naming a destroyed account) is
    repaired through JMAP and the read repeated once."""
    try:
        return await _read(owner_login, path)
    except dav.ServerError as e:
        if e.status < 500:
            raise
        log.info("ACL read of %s failed (%s); removing grants to missing accounts", path, e)
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
    JMAP, because the DAV ACL read fails while such a grant is there. Returns how many."""
    from mailr.core.jmap import CORE
    from mailr.core.store import service_client

    if not owner.stalwart_account_id:
        return 0
    cli = service_client()
    using = [CORE, _JMAP_CAL]
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


async def _actor_may_share(t: Target, actor_login: str, aces: list[Ace]) -> None:
    if t.own:
        return
    mine = next((a for a in aces if a.login == actor_login), None)
    if mine is None or mine.level != "share":
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
    await _actor_may_share(t, me, aces)
    by_id = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(
        [who[a.login] for a in aces if a.login in who])))).scalars()}
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
        people.append({**_person(u), "level": a.level, "disabled": bool(u.disabled), "you": uid == actor.id,
                       "outside_policy": uid != actor.id and not allowed_by(pol, actor, u)})
    cands = [_person(u) for u in await eligible(db, actor) if u.id not in seen and u.id != t.owner.id]
    return {"calendar_id": coll.id, "name": _name(coll), "own": t.own, "owner": _person(t.owner),
            "policy": pol, "levels": list(LEVELS), "people": people, "others": others,
            "candidates": cands, "max": MAX_PEOPLE}


async def set_level(db: AsyncSession, actor: User, coll: CalCollection, user_id: int, level: str | None) -> dict[str, Any]:
    """Share with a person at a level, change it, or stop (level None). The caller commits."""
    if level is not None and level not in LEVELS:
        raise SharingError("The level is view, edit or share.")
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
    await _actor_may_share(t, me, aces)
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


async def on_listing(client: dav.DavClient, user_id: int, home: str, infos: list[dav.CollectionInfo]
                     ) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """During a sync: the owner summaries of the user's own calendars, and the calendars shared
    with the user (path → owner, level). A view-only share is set read-only in `infos`.
    Returns (shared_in, acl); acl is None when it could not be read (the old one stays)."""
    async with session_scope() as db:
        who = await logins(db)
        me = await groups.user_login(db, await db.get(User, user_id))
        managed = await groups.managed_homes(db)
        own_home = groups.path_of(home)
        foreign: dict[str, dav.CollectionInfo] = {}
        for i in infos:
            p = groups.path_of(i.href)
            if p.startswith(own_home) or any(p.startswith(h) for h in managed):
                continue
            foreign[p] = i
        owner_of = {p: unquote(p.split("/")[3]).lower() for p in foreign if p.count("/") >= 4}
        need = {who[o] for o in owner_of.values() if o in who}
        states = {c.user_id: ((c.state or {}).get("caldav") or {}).get("acl") or {}
                  for c in (await db.execute(select(Connector).where(Connector.builtin.is_(True),
                                                                      Connector.user_id.in_(need)))).scalars()} \
            if need else {}
    shared_in: dict[str, Any] = {}
    for p, info in foreign.items():
        oid = who.get(owner_of.get(p, ""))
        if oid is None or oid == user_id:
            continue
        entries = states.get(oid, {}).get(p) or []
        level = next((e[1] for e in entries if e[0] == me), None)
        if level == "view":
            info.read_only = True  # the first ACE decides writes; the listing reports the union
        shared_in[p] = {"owner_id": oid, "level": level or ("view" if info.read_only else "edit"),
                        "direct": level is not None}
    acl = await owner_summaries(client, home, who)
    if acl is None:
        try:
            async with session_scope() as db:
                u = await db.get(User, user_id)
                if u is not None and await heal(u):
                    acl = await owner_summaries(client, home, who)
        except Exception as e:  # noqa: BLE001  the next sync tries again
            log.warning("could not repair the calendar grants of user %s: %s", user_id, e)
    return shared_in, acl


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
    ids = {v.get("owner_id") for v in shared_in.values()}
    if ids:
        owners = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars()}
    out: dict[str, dict[str, Any]] = {}
    for href in colls:
        p = groups.path_of(href)
        if p in shared_in:
            s = shared_in[p]
            o = owners.get(s.get("owner_id"))
            lvl = s.get("level") or "view"
            out[href] = {"shared": {"kind": "user", "owner": _person(o)["name"] if o else "someone",
                                    "owner_id": s.get("owner_id"), "level": lvl,
                                    "access": "read" if lvl == "view" else "write", "can_share": lvl == "share"}}
        elif p in acl:
            people = sum(1 for e in acl[p] if e[2])
            others = sum(1 for e in acl[p] if not e[2])
            out[href] = {"sharing": {"people": people, "others": others}}
    return out
