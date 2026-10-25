"""Copy a connected CalDAV/CardDAV account into the user's mailr calendar.

Admin docs site/docs/admin/calendars.md ("Move from
another calendar server"), user guide site/docs/user-guide/calendar.md. Entry points: the admin
command `mailr calendars migrate` (cli.py), the job `calendar.migrate` and its routes
(api/calendar_migration_routes.py), behind "Copy into my mailr calendar…" in Settings.

What a run does, for one external CalDAV connector of a user who has the built-in calendar:

- Lists the source: every calendar, task list and journal collection in its calendar home, every
  address book in its address-book home, and what cannot be copied (below).
- Picks a target in the user's own home on the store for each: the target an earlier run used,
  else a collection of the same kind with the same name, else a new one with the source's name,
  colour and components (MKCALENDAR, extended MKCOL for address books) under the source's slug
  (made unique).
- Copies the items page by page (dav.MULTIGET_BATCH): each resource keeps its text, its UID and,
  where the name is plain, its resource name. A recurring series stays one resource with its
  overrides and VTIMEZONEs. Address books are read as vCard 4.0 (the store keeps vCard 4 anyway;
  Sabre converts, other servers send what they hold). The PUTs go through restore.put_items:
  If-None-Match: *, at most builtin.MAX_IN_FLIGHT at once; the same content already there is
  skipped, other content under the same UID is a conflict, or with `overwrite` replaced by the
  source's version (If-Match). "The same content" is restore.normalized: it ignores what servers
  add or rewrite (DTSTAMP, PRODID, VERSION, SCHEDULE-* parameters, folding, part order, vCard 3
  photos as data: URIs).
- Never deletes anything, on either side. The source account stays connected and unchanged.
- A source listing the server cut short (dav.Truncated) is read with sync-collection and the
  mirror's own list of the collection's items; the collection is marked `partial`.

A re-run continues where a stopped one ended: everything already copied is skipped. A dry run
reads both sides and writes nothing; it gives the same counts a copy would.

Not copied, reported with a short guide: calendars other people shared with the user on the source
(unless named in `collections`), the user's own shares to others and delegates (Sabre's
calendar-proxy-read/-write), subscriptions, the server's scheduling inbox, and resources without a
UID.

Cut-over (`cutover`), only on the user's request: the copies become the default calendar and task
list, and/or the source account's copied calendars are hidden in Calendar (mailr-only; the source
server is not touched).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from sqlalchemy import func, select, text

from mailr.calendar import backup, builtin, dav, restore
from mailr.core.jobs import handler
from mailr.db import engine, session_scope
from mailr.models import AddressBook, CalCollection, CalObject, Connector, ContactCard, Job, User

log = logging.getLogger("mailr.calendar.migrate")

JOB = "calendar.migrate"
PAGE = dav.MULTIGET_BATCH
CAL_TYPES = restore.CAL_TYPES
CARD_VERSION = "4.0"
CS, CARD = "http://calendarserver.org/ns/", "urn:ietf:params:xml:ns:carddav"
# A resource name kept as it is: plain characters, the right extension.
_PLAIN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+=-]{0,199}$")

Progress = Callable[[int, int, str], Awaitable[None]]


class MigrationError(Exception):
    """The run cannot start (no mailr calendar, not a CalDAV account, another run is going)."""


# ---------------- the report ----------------


@dataclass
class Collection:
    """One source collection and what happened to it."""
    source: str                    # the source href
    kind: str                      # calendar | addressbook
    name: str
    color: str | None = None
    components: list[str] = field(default_factory=list)
    slug: str = ""
    shared_in: str = ""            # owner, when another account shared it with this user
    sharees: list[str] = field(default_factory=list)   # the user's own shares on the source
    selected: bool = True
    target: str | None = None      # the target href (None: a dry run would make it)
    target_name: str = ""
    action: str = ""               # previous | existing | create | skipped
    counts: restore.Counts = field(default_factory=restore.Counts)
    total: int = 0
    partial: bool = False
    conflicts: list[str] = field(default_factory=list)  # restore.put_items appends "<label>: <uid>"
    errors: list[str] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        c = self.counts
        return {"source": self.source, "kind": self.kind, "name": self.name, "color": self.color,
                "components": self.components, "target": self.target, "target_name": self.target_name,
                "action": self.action, "total": self.total, "copied": c.created, "skipped": c.skipped,
                "conflicts": c.conflicts, "replaced": c.replaced, "failed": c.failed, "partial": self.partial,
                "conflict_uids": [x.split(": ", 1)[-1] for x in self.conflicts][:200],
                "failures": self.failures[:200]}


@dataclass
class Report:
    connector_id: int
    dry_run: bool = False
    overwrite: bool = False
    collections: list[Collection] = field(default_factory=list)
    not_migrated: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def totals(self) -> dict[str, int]:
        out = {"collections": 0, "created_collections": 0, "total": 0, "copied": 0, "skipped": 0, "conflicts": 0,
               "replaced": 0, "failed": 0}
        for c in self.collections:
            if c.action == "skipped":
                continue
            out["collections"] += 1
            out["created_collections"] += c.action == "create"
            out["total"] += c.total
            for k, attr in (("copied", "created"), ("skipped", "skipped"), ("conflicts", "conflicts"),
                            ("replaced", "replaced"), ("failed", "failed")):
                out[k] += getattr(c.counts, attr)
        return out

    def as_dict(self) -> dict[str, Any]:
        return {"connector_id": self.connector_id, "dry_run": self.dry_run, "overwrite": self.overwrite,
                "collections": [c.as_dict() for c in self.collections], "totals": self.totals(),
                "not_migrated": self.not_migrated, "errors": self.errors[:100],
                "started_at": self.started_at, "finished_at": self.finished_at}


# ---------------- clients ----------------


def source_client(conn: Connector, user: User):
    """The connected account, read with the CalDAV client's transport (network guard, credentials
    only for its own origin) plus the CardDAV reads."""
    from mailr.contacts import carddav

    return carddav.client_for(conn, user)


def target_client(conn: Connector, user: User):
    """The user's mailr calendar on the store (impersonation, at most MAX_IN_FLIGHT requests)."""
    from mailr.contacts import carddav

    return carddav.client_for(conn, user)


def _homes(conn: Connector) -> tuple[str, str]:
    from mailr.calendar.groups import home_of
    from mailr.config import get_settings

    account = (conn.config or {}).get("account") or ""
    base = get_settings().stalwart_url.rstrip("/")
    return base + home_of(account), base + f"/dav/card/{quote(account.lower(), safe='')}/"


# ---------------- reading the source ----------------


def _q(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


_INVENTORY_PROPS = ["d:resourcetype", "d:displayname", "c:supported-calendar-component-set", "ic:calendar-color",
                    "d:share-access", "cs:invite", "cs:source", "d:current-user-privilege-set"]


def _slug(href: str) -> str:
    return unquote(urlsplit(href).path.rstrip("/").rsplit("/", 1)[-1])


def _who(href: str) -> str:
    """A principal or mailto: href as a short name."""
    h = (href or "").strip()
    if h.lower().startswith("mailto:"):
        return h[7:]
    return unquote(h.rstrip("/").rsplit("/", 1)[-1]) or h


async def inventory_calendars(client, home: str) -> tuple[list[Collection], list[dict[str, str]]]:
    """The calendar collections in `home`, and what cannot be copied (subscriptions, shares, the
    scheduling inbox)."""
    ms = await client._multistatus("PROPFIND", home, dav._propfind_body(_INVENTORY_PROPS), "1")
    if dav._truncated(ms):
        raise dav.Truncated(f"PROPFIND {home}: the server listed only part of the calendars", 507)
    out: list[Collection] = []
    skipped: list[dict[str, str]] = []
    for resp in ms.findall("d:response", dav.NS):
        href = client.abs(dav._text(resp.find("d:href", dav.NS)))
        if client.same_resource(href, home):
            continue
        p = dav._ok_props(resp)
        rt = p.find("d:resourcetype", dav.NS)
        if rt is None:
            continue
        name = dav._text(p.find("d:displayname", dav.NS)) or _slug(href)
        if rt.find(_q(CS, "subscribed")) is not None:
            src = dav._text(p.find(f"{_q(CS, 'source')}/{_q('DAV:', 'href')}")) or dav._text(p.find(_q(CS, "source")))
            skipped.append({"kind": "subscription", "name": name,
                            "detail": f"A subscription to {src}." if src else "A subscription.",
                            "how": "Subscriptions stay on the other server. Keep that account connected for it, "
                                   "or download the calendar from its link and import the file."})
            continue
        if rt.find("c:schedule-inbox", dav.NS) is not None:
            try:
                waiting = len(await client.etags(href))
            except dav.DavError:
                waiting = 0
            if waiting:
                skipped.append({"kind": "scheduling", "name": "Scheduling inbox",
                                "detail": f"{waiting} invitation message{'s' if waiting != 1 else ''} waiting there.",
                                "how": "Answer open invitations on the other server first. The Mailr calendar does "
                                       "not take over the other server's scheduling messages."})
            continue
        if rt.find("c:calendar", dav.NS) is None:
            continue
        comps = [c.get("name", "").upper() for c in p.findall("c:supported-calendar-component-set/c:comp", dav.NS)]
        color = dav._text(p.find("ic:calendar-color", dav.NS))
        col = Collection(source=href, kind="calendar", name=name, slug=_slug(href),
                         color=color[:7] if color.startswith("#") and len(color) >= 7 else None,
                         components=[c for c in comps if c in CAL_TYPES] or ["VEVENT", "VTODO"])
        access = p.find("d:share-access", dav.NS)
        access_kind = access[0].tag.split("}")[-1] if access is not None and len(access) else ""
        invite = p.find(_q(CS, "invite"))
        organizer = dav._text(invite.find(f"{_q(CS, 'organizer')}/{_q('DAV:', 'href')}")) if invite is not None else ""
        privs = {x.tag.split("}")[-1] for x in p.findall("d:current-user-privilege-set/d:privilege/*", dav.NS)}
        if access_kind in ("read", "read-write") or rt.find(_q(CS, "shared")) is not None:
            col.shared_in = _who(organizer) or "another account"
        elif privs and not ({"write", "write-content", "all"} & privs):
            col.shared_in = "another account"
        if invite is not None and not col.shared_in:
            for u in invite.findall(_q(CS, "user")):
                who = _who(dav._text(u.find(_q("DAV:", "href"))))
                acc = u.find(_q(CS, "access"))
                level = acc[0].tag.split("}")[-1] if acc is not None and len(acc) else "read"
                if who:
                    col.sharees.append(f"{who} ({'can edit' if level == 'read-write' else 'can view'})")
        out.append(col)
    return out, skipped


async def inventory_books(client) -> list[Collection]:
    home = await client.addressbook_home()
    if not home:
        return []
    return [Collection(source=i.href, kind="addressbook", name=i.displayname or _slug(i.href), slug=_slug(i.href))
            for i in await client.books(home)]


async def _display_name(client, href: str) -> str:
    try:
        ms = await client._multistatus("PROPFIND", href, dav._propfind_body(["d:displayname"]), "0")
        for resp in ms.findall("d:response", dav.NS):
            n = dav._text(dav._ok_props(resp).find("d:displayname", dav.NS))
            if n:
                return f"{n} ({_who(href)})"
    except dav.DavError:
        pass
    return _who(href)


async def inventory_delegation(client) -> list[dict[str, str]]:
    """Delegates on the source (Apple/Sabre calendar-proxy): who acts for this user, and for whom
    this user acts. They have no counterpart that a copy could make."""
    out: list[dict[str, str]] = []
    try:
        principal = await client._href_prop(client.base, "d:current-user-principal")
    except dav.DavError:
        return out
    if not principal:
        return out
    base = principal if principal.endswith("/") else principal + "/"
    for kind, label in (("calendar-proxy-write", "can edit"), ("calendar-proxy-read", "can view")):
        try:
            ms = await client._multistatus("PROPFIND", base + kind + "/",
                                           dav._propfind_body(["d:group-member-set"]), "0")
        except dav.DavError:
            continue
        for resp in ms.findall("d:response", dav.NS):
            for h in dav._ok_props(resp).findall("d:group-member-set/d:href", dav.NS):
                who = await _display_name(client, client.abs(dav._text(h)))
                out.append({"kind": "delegate", "name": who, "detail": f"Delegate on the other server ({label}).",
                            "how": "In Mailr, open Share… on each calendar and add this person with "
                                   + ("Edit" if kind.endswith("write") else "View")
                                   + ". If they have no Mailr account, ask your administrator for one."})
    try:
        ms = await client._multistatus("PROPFIND", principal, dav._propfind_body(
            ["cs:calendar-proxy-write-for", "cs:calendar-proxy-read-for"]), "0")
        for resp in ms.findall("d:response", dav.NS):
            p = dav._ok_props(resp)
            for tag, label in (("cs:calendar-proxy-write-for", "edit"), ("cs:calendar-proxy-read-for", "view")):
                for h in p.findall(f"{tag}/d:href", dav.NS):
                    who = await _display_name(client, client.abs(dav._text(h)))
                    out.append({"kind": "delegated_to_you", "name": who,
                                "detail": f"You can {label} this person's calendars on the other server.",
                                "how": "Ask them to share their calendars with you in Mailr."})
    except dav.DavError:
        pass
    return out


async def _mirror_hrefs(connector_id: int, source: str, cards: bool) -> list[str]:
    """The items mailr's mirror holds for a source collection (for a listing the server cut)."""
    async with session_scope() as db:
        if cards:
            q = select(ContactCard.href).join(AddressBook, AddressBook.id == ContactCard.book_id).where(
                AddressBook.connector_id == connector_id, AddressBook.href == source)
        else:
            q = select(CalObject.href).join(CalCollection, CalCollection.id == CalObject.collection_id).where(
                CalCollection.connector_id == connector_id, CalCollection.href == source,
                CalObject.deleted_at.is_(None))
        return list((await db.execute(q)).scalars())


async def source_items(client, col: Collection, connector_id: int) -> list[str]:
    """Every resource href of a source collection. A listing the server cut short is completed with
    sync-collection and the mirror's list, and the collection is marked partial."""
    try:
        return sorted(await client.etags(col.source))
    except dav.Truncated:
        col.partial = True
    hrefs: set[str] = set()
    try:
        changed, _removed, _token, _cut = await client.sync_collection(col.source, None)
        hrefs |= {h for h, _ in changed}
    except dav.DavError:
        pass
    hrefs |= {client.abs(h) for h in await _mirror_hrefs(connector_id, col.source, col.kind == "addressbook")}
    return sorted(hrefs)


# ---------------- choosing the targets ----------------


def _clean_slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-.")[:60] or "imported"


def plan_targets(sources: list[Collection], targets: dict[str, list[dict[str, str]]], previous: dict[str, str],
                 home: dict[str, str]) -> None:
    """Fill in target/action/target_name of each selected source. `targets` per kind: the user's own
    collections on the store ({"href", "name"}); `previous`: source href → target href of an
    earlier run; `home` per kind: the home URL new collections go in."""
    used: set[str] = set()
    for kind in ("calendar", "addressbook"):
        have = targets.get(kind) or []
        by_href = {t["href"]: t for t in have}
        taken = {_slug(t["href"]).lower() for t in have}
        for col in (s for s in sources if s.kind == kind and s.selected):
            prev = previous.get(col.source)
            if prev and prev in by_href and prev not in used:
                col.action, col.target, col.target_name = "previous", prev, by_href[prev]["name"]
                used.add(prev)
                continue
            same = next((t for t in have if t["href"] not in used
                         and (t["name"] or "").strip().casefold() == col.name.strip().casefold()), None)
            if same is not None:
                col.action, col.target, col.target_name = "existing", same["href"], same["name"]
                used.add(same["href"])
                continue
            slug, n = _clean_slug(col.slug), 2
            while slug.lower() in taken:
                slug, n = f"{_clean_slug(col.slug)[:56]}-{n}", n + 1
            taken.add(slug.lower())
            col.action, col.slug, col.target_name = "create", slug, col.name
            col.target = home[kind] + quote(slug, safe="-._~") + "/"


# ---------------- the run ----------------


def _resource_name(href: str, uid: str, ext: str) -> str:
    """The source's resource name where it is plain, else one made from the UID."""
    n = _slug(href)
    if _PLAIN_NAME.match(n) and n.lower().endswith(ext):
        return n
    return restore._name_for(uid, {}, ext)


async def _previous_map(connector_id: int) -> dict[str, str]:
    """source href → target href from the newest finished copy of this account (not a dry run)."""
    async with session_scope() as db:
        rows = (await db.execute(select(Job.payload).where(
            Job.kind == JOB, Job.payload["connector_id"].astext == str(connector_id),
            Job.payload["result"].isnot(None)).order_by(Job.id.desc()).limit(10))).scalars().all()
    for p in rows:
        r = (p or {}).get("result") or {}
        if not r.get("dry_run"):
            return {c["source"]: c["target"] for c in r.get("collections") or [] if c.get("target")}
    return {}


async def _targets(tclient, conn: Connector) -> tuple[dict[str, list[dict[str, str]]], dict[str, str]]:
    cal_home, card_home = _homes(conn)
    cals = [i for i in await tclient.collections(cal_home) if tclient.abs(i.href).startswith(tclient.abs(cal_home))]
    try:
        books = await tclient.books(card_home)
    except dav.NotFound:
        books = []
    return ({"calendar": [{"href": tclient.abs(i.href), "name": i.displayname} for i in cals],
             "addressbook": [{"href": tclient.abs(i.href), "name": i.displayname} for i in books]},
            {"calendar": tclient.abs(cal_home), "addressbook": tclient.abs(card_home)})


async def _make_target(tclient, col: Collection, home: str) -> str:
    if col.kind == "addressbook":
        return await restore._mkcol_addressbook(tclient, home, col.slug, col.name)
    return await tclient.mkcalendar(home, col.slug, col.name, col.components, col.color)


def _reason(err: str) -> str:
    """A plain reason for a refused item."""
    e = err.lower()
    if "max-resource-size" in e or "http 413" in e:
        return "Too large for the Mailr calendar (512 KiB per item, attachments included)."
    if "no-uid-conflict" in e:
        return "Another calendar of your Mailr calendar already holds an item with this UID."
    if "max-attendees" in e:
        return "Too many guests for the Mailr calendar."
    if "valid-calendar" in e or "valid-address" in e or "supported-calendar" in e or "http 415" in e:
        return "The Mailr calendar refused the item as invalid."
    if "http 403" in e:
        return "The Mailr calendar refused the item."
    return err.split(": ", 2)[-1][:200]


async def _copy(sclient, tclient, col: Collection, connector_id: int, dry: bool, overwrite: bool,
                hrefs: list[str], tick: Callable[[int], Awaitable[None]]) -> None:
    cards = col.kind == "addressbook"
    ext = ".vcf" if cards else ".ics"
    have: dict[str, tuple[str, str | None, str]] | None = {}
    if col.target and col.action != "create":
        have = await restore._existing(tclient, col.target, cards)
    label = col.name
    for i in range(0, len(hrefs), PAGE):
        page = hrefs[i:i + PAGE]
        got = await (sclient.multiget_cards(col.source, page, version=CARD_VERSION) if cards
                     else sclient.multiget(col.source, page))
        items = []
        for href, _etag, body in got:
            uid = backup.uid_of(body)
            if not uid:
                col.counts.failed += 1
                col.failures.append({"uid": "", "item": _slug(href),
                                     "reason": "The item has no UID; Mailr cannot tell copies apart without one."})
                continue
            items.append((uid, body, _resource_name(href, uid, ext)))
        before = len(col.errors)
        if col.target is None:  # a dry run that would make the collection: everything would be copied
            col.counts.created += len(items)
        else:
            await restore.put_items(tclient, col.target, items, have, cards, dry, col.counts, col, label, overwrite,
                                    in_flight=builtin.MAX_IN_FLIGHT)
        for e in col.errors[before:]:
            uid = e.split(": ", 2)[1] if e.count(": ") >= 2 else ""
            col.failures.append({"uid": uid, "item": "", "reason": _reason(e)})
        await tick(len(page))


async def _lock(conn_id: int):
    """A session advisory lock: one run per source account at a time (CLI and worker)."""
    raw = await engine().connect()
    lc = await raw.execution_options(isolation_level="AUTOCOMMIT")
    got = (await lc.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": f"calmigrate:{conn_id}"})).scalar()
    if not got:
        await raw.close()
        raise MigrationError("A copy of this account is already running.")
    return raw, lc


async def _unlock(raw, lc, conn_id: int) -> None:
    try:
        await lc.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": f"calmigrate:{conn_id}"})
    finally:
        await raw.close()


async def load(user_id: int, connector_id: int) -> tuple[User, Connector, Connector]:
    """(user, source connector, built-in connector), or MigrationError."""
    async with session_scope() as db:
        user = await db.get(User, user_id)
        src = await db.get(Connector, connector_id)
        if user is None or src is None or src.user_id != user_id:
            raise MigrationError("No such calendar account.")
        if src.kind != "caldav" or src.builtin:
            raise MigrationError("Only a connected CalDAV account can be copied into the Mailr calendar.")
        target = await builtin.find(db, user_id)
        if target is None or not await builtin.active(db, target):
            raise MigrationError("You have no Mailr calendar on this server. An administrator turns built-in "
                                 "calendars on under Settings → Admin → Calendars.")
        db.expunge_all()
    return user, src, target


async def migrate(user_id: int, connector_id: int, *, collections: list[str] | None = None, dry_run: bool = False,
                  overwrite: bool = False, progress: Progress | None = None) -> Report:
    """Copy (or with dry_run, compare) one external account into the user's mailr calendar.
    `collections`: names, slugs or hrefs of the source collections to copy (default: every one
    the user owns; a collection shared by someone else is copied only when named here)."""
    user, src, target = await load(user_id, connector_id)
    report = Report(connector_id=connector_id, dry_run=dry_run, overwrite=overwrite,
                    started_at=datetime.now(UTC).isoformat())
    raw, lc = await _lock(connector_id)
    try:
        async with source_client(src, user) as sclient, target_client(target, user) as tclient:
            # The source.
            home = await sclient.home()
            cols, skipped = await inventory_calendars(sclient, home)
            try:
                cols += await inventory_books(sclient)
            except dav.DavError as e:
                report.errors.append(f"address books: {e}"[:300])
            skipped += await inventory_delegation(sclient)
            wanted = {w.strip().casefold() for w in collections or [] if w.strip()}

            def named(c: Collection) -> bool:
                return bool({c.name.casefold(), c.slug.casefold(), c.source.casefold(),
                             urlsplit(c.source).path.casefold()} & wanted)

            for c in cols:
                c.selected = named(c) if wanted else not c.shared_in
                if c.shared_in and not c.selected:
                    c.action = "skipped"
                    skipped.append({"kind": "shared_with_you", "name": c.name,
                                    "detail": f"Shared with you by {c.shared_in} on the other server.",
                                    "how": "Ask the owner to share it with you in Mailr. To keep a copy of your own, "
                                           "an administrator can copy it by name with `mailr calendars migrate "
                                           "--collections`."})
                elif not c.selected:
                    c.action = "skipped"
                if c.sharees and c.selected:
                    skipped.append({"kind": "share", "name": c.name,
                                    "detail": "Shared on the other server with " + ", ".join(c.sharees) + ".",
                                    "how": "Share the copy in Mailr with the same people."})
            report.collections = cols
            report.not_migrated = skipped
            # The targets.
            targets, homes = await _targets(tclient, target)
            plan_targets(cols, targets, await _previous_map(connector_id), homes)
            # The items: listing first, so progress has a total.
            lists: dict[str, list[str]] = {}
            for c in cols:
                if c.selected:
                    try:
                        lists[c.source] = await source_items(sclient, c, connector_id)
                    except dav.DavError as e:
                        c.errors.append(f"{c.name}: {e}")
                        c.failures.append({"uid": "", "item": "", "reason": f"The other server did not list it: {e}"})
                        lists[c.source] = []
                    c.total = len(lists[c.source])
            total = sum(len(v) for v in lists.values())
            done = 0

            async def tick(n: int, name: str = "") -> None:
                nonlocal done
                done += n
                if progress is not None:
                    await progress(done, total, name)

            if progress is not None:
                await progress(0, total, "")
            for c in cols:
                if not c.selected:
                    continue
                if c.action == "create" and not dry_run:
                    try:
                        c.target = await _make_target(tclient, c, homes[c.kind])
                    except dav.DavError as e:
                        c.counts.failed += c.total
                        c.failures.append({"uid": "", "item": "", "reason": f"Couldn't create the collection: {e}"})
                        await tick(c.total, c.name)
                        continue
                elif c.action == "create":
                    c.target = None

                async def page_tick(n: int, name: str = c.name) -> None:
                    await tick(n, name)

                try:
                    await _copy(sclient, tclient, c, connector_id, dry_run, overwrite, lists[c.source], page_tick)
                except dav.DavError as e:
                    c.errors.append(f"{c.name}: {e}")
                    c.failures.append({"uid": "", "item": "", "reason": f"Stopped: {e}"[:300]})
                    report.errors.append(f"{c.name}: {e}"[:300])
    finally:
        await _unlock(raw, lc, connector_id)
    report.finished_at = datetime.now(UTC).isoformat()
    if not dry_run and any(c.counts.created or c.counts.replaced or c.action == "create" for c in report.collections):
        await _sync_target(target.id, user_id)
    t = report.totals()
    log.info("calendar migration of connector %s (%s): %s", connector_id, "dry run" if dry_run else "copy", t)
    return report


async def _sync_target(connector_id: int, user_id: int) -> None:
    from mailr.core.jobs import enqueue

    async with session_scope() as db:
        await enqueue(db, "calendar.sync", {"connector_id": connector_id}, user_id=user_id,
                      dedupe_key=f"calendar.sync:{connector_id}")
        await enqueue(db, "contacts.sync", {"connector_id": connector_id}, user_id=user_id,
                      dedupe_key=f"contacts.sync:{connector_id}")


# ---------------- the job ----------------


async def start(db, user_id: int, connector_id: int, *, dry_run: bool, overwrite: bool = False,
                collections: list[str] | None = None) -> int:
    """Queue a run (one per account at a time). Returns the job id; a run already queued or
    running for this account is returned instead of a new one."""
    from mailr.core.jobs import enqueue_returning

    running = (await db.execute(select(Job.id).where(
        Job.kind == JOB, Job.status.in_(["queued", "running"]),
        Job.payload["connector_id"].astext == str(connector_id)).order_by(Job.id.desc()).limit(1))).scalar()
    if running is not None:
        return int(running)
    return await enqueue_returning(db, JOB, {"connector_id": connector_id, "dry_run": bool(dry_run),
                                             "overwrite": bool(overwrite), "collections": collections or []},
                                   user_id=user_id, max_attempts=3)


async def _job_payload(job_id: int, patch: dict[str, Any]) -> None:
    import json

    async with session_scope() as db:
        await db.execute(text("UPDATE jobs SET payload = coalesce(payload, '{}') || CAST(:p AS jsonb) WHERE id=:id"),
                         {"p": json.dumps(patch, default=str), "id": job_id})


@handler(JOB)
async def migrate_job(payload: dict[str, Any], ctx) -> None:
    import time

    last = [0.0]

    async def progress(done: int, total: int, name: str) -> None:
        now = time.monotonic()
        if done == 0:
            await _job_payload(ctx.job_id, {"total": total})
        if done == 0 or done >= total or now - last[0] >= 1.0:
            last[0] = now
            async with session_scope() as db:
                await db.execute(text("UPDATE jobs SET progress=:d WHERE id=:id"), {"d": done, "id": ctx.job_id})
            await _job_payload(ctx.job_id, {"current": name})

    try:
        rep = await migrate(ctx.user_id, int(payload["connector_id"]), collections=payload.get("collections") or None,
                            dry_run=bool(payload.get("dry_run")), overwrite=bool(payload.get("overwrite")),
                            progress=progress)
    except MigrationError as e:
        await _job_payload(ctx.job_id, {"result": {"error": str(e)}})
        return
    await _job_payload(ctx.job_id, {"result": rep.as_dict()})


async def record(user_id: int, report: Report, collections: list[str] | None = None) -> int:
    """Keep a finished run made outside the worker (the admin command) as a done job, so the next
    run finds its targets and the user's Settings show it."""
    from mailr.core.jobs import enqueue_returning

    t = report.totals()
    async with session_scope() as db:
        jid = await enqueue_returning(db, JOB, {"connector_id": report.connector_id, "dry_run": report.dry_run,
                                                "overwrite": report.overwrite, "collections": collections or [],
                                                "total": t["total"], "result": report.as_dict(), "source": "cli"},
                                      user_id=user_id)
        await db.execute(text("UPDATE jobs SET status='done', progress=:n WHERE id=:id"), {"n": t["total"], "id": jid})
    return jid


def job_out(j: Job) -> dict[str, Any]:
    p = j.payload or {}
    result = p.get("result")
    status = j.status
    if status == "done" and isinstance(result, dict) and result.get("error"):
        status = "failed"
    return {"job": j.id, "status": status, "dry_run": bool(p.get("dry_run")), "overwrite": bool(p.get("overwrite")),
            "done": j.progress or 0, "total": p.get("total"), "current": p.get("current") or "",
            "result": result if isinstance(result, dict) and not result.get("error") else None,
            "error": (result or {}).get("error") if isinstance(result, dict) and result.get("error")
            else (j.error.splitlines()[0][:300] if j.status == "failed" and j.error else ""),
            "created_at": j.created_at.isoformat() if j.created_at else None}


# ---------------- cut-over ----------------


async def cutover(user_id: int, connector_id: int, *, make_default: bool, hide_source: bool) -> dict[str, Any]:
    """After a copy, on the user's request: (a) the copies become the default calendar and task
    list; (b) the source account's copied calendars are hidden in Calendar (mailr-only). The
    source server is not touched. Reads the newest finished copy's targets."""
    from mailr.calendar import client as cal
    from mailr.calendar import mirror, sync
    from mailr.core.events import record_change

    _user, _src, target = await load(user_id, connector_id)
    mapping = await _previous_map(connector_id)
    if not mapping:
        raise MigrationError("Copy this account into the Mailr calendar first.")
    out: dict[str, Any] = {"hidden": 0, "defaults": None}
    if make_default:
        # The new collections must be in the mirror before they can be the defaults.
        await sync.run_inline(target.id, budget=20)
        async with session_scope() as db:
            u = await db.get(User, user_id)
            cols = {c["url"]: c for c in await cal.collections(db, user_id) if "error" not in c}
            saved = (u.settings or {}).get("calendar_defaults") or {}
            src_cols = {c.href: c for c in await mirror.collections_of(db, [connector_id])}
            mine = [cols[t] for t in mapping.values() if t in cols]

            def pick(kind: str, saved_url: str | None) -> str | None:
                if saved_url and saved_url in mapping and mapping[saved_url] in cols and cols[mapping[saved_url]][kind]:
                    return mapping[saved_url]
                if saved_url and saved_url not in src_cols and saved_url in cols:
                    return saved_url if cols[saved_url].get("connector_id") == target.id else None
                only = [c["url"] for c in mine if c[kind] and not c[{"events": "tasks", "tasks": "events"}[kind]]]
                return (only or [c["url"] for c in mine if c[kind]] or [None])[0]

            new_cal = pick("events", saved.get("calendar"))
            new_list = pick("tasks", saved.get("task_list"))
            if new_cal is None and new_list is None:
                raise MigrationError("The copies are not in your calendar list yet. Try again in a minute.")
            out["defaults"] = await cal.set_defaults(db, u, new_cal or saved.get("calendar"),
                                                     new_list or saved.get("task_list"))
    if hide_source:
        async with session_scope() as db:
            rows = (await db.execute(select(CalCollection).where(
                CalCollection.connector_id == connector_id, CalCollection.href.in_(list(mapping)),
                CalCollection.deleted_at.is_(None)))).scalars().all()
            for c in rows:
                if not c.hidden:
                    c.hidden = True
                    await mirror.mark_collection_rev(db, c)
                    await record_change(db, user_id, "calendar", c.id)
                    out["hidden"] += 1
    return out


async def connectors_of(email_or_id: str) -> tuple[User | None, list[Connector]]:
    """For the CLI: the user and their external CalDAV accounts."""
    async with session_scope() as db:
        u = (await db.execute(select(User).where(func.lower(User.email) == email_or_id.lower()))).scalar_one_or_none()
        if u is None and email_or_id.isdigit():
            u = await db.get(User, int(email_or_id))
        if u is None:
            return None, []
        conns = (await db.execute(select(Connector).where(Connector.user_id == u.id, Connector.kind == "caldav",
                                                          Connector.builtin.is_(False)).order_by(Connector.id))
                 ).scalars().all()
        db.expunge_all()
        return u, list(conns)


__all__ = ["JOB", "Collection", "MigrationError", "Report", "cutover", "inventory_calendars", "migrate",
           "plan_targets", "start"]
