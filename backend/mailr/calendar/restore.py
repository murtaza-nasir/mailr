"""Restore the calendar export into the built-in store, and force full resyncs.

Admin commands `mailr calendars restore`
and `mailr calendars resync` (cli.py); the export is calendar/backup.py.

Restore, per snapshot (a user's, or the group calendars'):

- Built-in calendars and address books go back into the user's own home on the store, under the
  same collection name (slug). A missing collection is made again with its name, colour and
  components. External accounts' collections are not restored: their data lives on the user's
  own server; the .ics and .vcf files are there to import by hand.
- Group calendars go back into the group calendar of the same path, else of the same group and
  name. One that no longer exists is made again (groups.create), with the editors who are still
  members. When the store lost the group's accounts, they are made again and the calendar moves
  to the new account's home.
- Each resource keeps its UID and, when the manifest has it, its resource name. A UID already in
  the collection is compared with the export: the same content is skipped, other content is a
  conflict, reported and left as it is (the store wins), unless `overwrite` asks for the export's
  version (after a restore of the store's volume the export is the newer copy). A new one is PUT with
  `If-None-Match: *`, so a second run creates nothing twice.
- Afterwards every affected connector gets a forced full resync (force_resync).

A dry run reads the store (listings only) and changes nothing.

Forced resync: the stored sync tokens and ctags of the connector's calendars and address books
are cleared and a full sync is queued. Stalwart accepts a sync token from its own future without
complaint (an empty delta), so after a restore of its volume an incremental sync would hide every
change; a sync without a token lists everything.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy import func, select, text

from mailr.calendar import backup, dav
from mailr.config import get_settings
from mailr.core.jobs import enqueue
from mailr.db import session_scope
from mailr.models import CalendarGroup, Connector, GroupCalendar, User

log = logging.getLogger("mailr.calendar.restore")

CAL_TYPES = ("VEVENT", "VTODO", "VJOURNAL")


# ---------------- finding the snapshots ----------------


def find_snapshots(src: Path | str) -> list[Path]:
    """The snapshots to restore from `src`: a snapshot folder itself, one owner's folder (its
    newest snapshot), or a folder above them (`exports/calendars`, `exports`, or the backup
    target), which gives the newest snapshot of every owner in it."""
    src = Path(src)
    if (src / backup.MANIFEST).is_file():
        return [src]
    if (name := backup.latest(src)) is not None:
        return [src / name]
    out = []
    for base in (src / "calendars", src):
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if child.is_dir() and (child.name.startswith("user-") or child.name == backup.GROUPS) \
                    and (name := backup.latest(child)) is not None:
                out.append(child / name)
        if out:
            break
    return out


# ---------------- resources ----------------


def split_calendar(text_: str) -> list[tuple[str, str]]:
    """[(uid, resource text)]: one VCALENDAR per UID (a series with its overrides, a task, a
    journal entry) with the VTIMEZONEs its components name. Components without a UID are left
    out (every mirrored resource has one)."""
    from icalendar import Calendar

    from mailr.calendar import ical

    src = Calendar.from_ical(text_)
    zones = {str(z.get("tzid")): z for z in src.walk("VTIMEZONE")}
    groups: dict[str, list[Any]] = {}
    for comp in src.subcomponents:
        if comp.name not in CAL_TYPES:
            continue
        uid = str(comp.get("uid") or "").strip()
        if uid:
            groups.setdefault(uid, []).append(comp)
    out = []
    for uid, comps in groups.items():
        cal = ical.new_calendar()
        body = b"".join(c.to_ical() for c in comps).decode("utf-8", "replace")
        for tzid, z in zones.items():
            if f"TZID={tzid}" in body or f'TZID="{tzid}"' in body:
                cal.add_component(z)
        for c in sorted(comps, key=lambda c: c.get("recurrence-id") is not None):
            cal.add_component(c)
        out.append((uid, cal.to_ical().decode()))
    return out


def split_cards(text_: str) -> list[tuple[str, str]]:
    """[(uid, card text)] of a .vcf file. Cards without a UID are left out."""
    out = []
    for m in re.finditer(r"BEGIN:VCARD\r?\n.*?END:VCARD\r?\n?", text_ or "", flags=re.DOTALL | re.IGNORECASE):
        card = m.group(0)
        card = card if card.endswith("\n") else card + "\r\n"
        uid = backup.uid_of(card)
        if uid:
            out.append((uid, card))
    return out


# What a server may add or rewrite without changing the item (the comparison of "is this the same
# resource", used by restore and by the migration, calendar/migrate.py):
# - properties: DTSTAMP, PRODID, VERSION, CALSCALE, X-WR-CALNAME, REV, and the VTIMEZONE blocks;
# - parameters: every SCHEDULE-* (SCHEDULE-STATUS, -AGENT, -FORCE-SEND), and quoting of values;
# - spelling: line folding, line order, the order of RRULE/EXRULE parts and of parameters, the
#   case of names and of TYPE values (Stalwart writes TYPE=HOME for TYPE=home);
# - embedded binaries (PHOTO, LOGO, SOUND, KEY): compared by their decoded bytes, whether written as
#   ENCODING=b (vCard 3) or as a data: URI (vCard 4; Stalwart turns every card into vCard 4).
_SKIP_PROPS = frozenset({"DTSTAMP", "PRODID", "VERSION", "CALSCALE", "X-WR-CALNAME", "REV"})
_BINARY = frozenset({"PHOTO", "LOGO", "SOUND", "KEY"})
_LIST_RULES = frozenset({"RRULE", "EXRULE"})


def _split_outside_quotes(s: str, seps: str) -> list[tuple[str, str]]:
    """[(part, the separator after it)], splitting at `seps` outside double quotes."""
    out, start, quoted = [], 0, False
    for i, ch in enumerate(s):
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch in seps:
            out.append((s[start:i], ch))
            start = i + 1
    out.append((s[start:], ""))
    return out


def _content_line(line: str) -> tuple[str, list[tuple[str, list[str]]], str] | None:
    """(NAME, [(PARAM, [values])], value) of one unfolded content line; None without a colon."""
    head, value = [], None
    for part, sep in _split_outside_quotes(line, ";:"):
        head.append(part)
        if sep == ":":
            value = line[sum(len(h) + 1 for h in head):]
            break
    if value is None:
        return None
    params = []
    for p in head[1:]:
        k, _, v = p.partition("=")
        params.append((k.strip().upper(), [x.strip().strip('"') for x, _ in _split_outside_quotes(v, ",")]))
    return head[0].strip().upper(), params, value


def _binary_digest(params: list[tuple[str, list[str]]], value: str) -> str | None:
    import base64
    import hashlib

    v = value.strip()
    try:
        if any(k == "ENCODING" and vals and vals[0].upper() in ("B", "BASE64") for k, vals in params):
            data = base64.b64decode(v, validate=False)
        elif v.lower().startswith("data:"):
            meta, _, payload = v.replace("\\,", ",").partition(",")
            if ";base64" not in meta.lower():
                return None
            data = base64.b64decode(payload, validate=False)
        else:
            return None
    except ValueError:
        return None
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical_line(line: str) -> str | None:
    parsed = _content_line(line)
    if parsed is None:
        return line
    name, params, value = parsed
    if name.rsplit(".", 1)[-1] in _SKIP_PROPS:
        return None
    params = [(k, vals) for k, vals in params if not k.startswith("SCHEDULE-")]
    if name.rsplit(".", 1)[-1] in _BINARY and (digest := _binary_digest(params, value)) is not None:
        return f"{name}:{digest}"
    if name in _LIST_RULES:
        value = ";".join(sorted(x for x in value.upper().split(";") if x))
    rendered = []
    for k, vals in sorted(params):
        if k == "TYPE":
            vals = sorted({y.upper() for x in vals for y in x.split(",") if y})
        rendered.append(f"{k}={','.join(vals)}")
    return ";".join([name, *rendered]) + ":" + value


def normalized(text_: str) -> tuple[str, ...]:
    """The content lines that matter, for "is this the same resource" (the rules above):
    unfolded, canonical, without the VTIMEZONE blocks, in a stable order."""
    flat = re.sub(r"\r?\n[ \t]", "", text_ or "")
    out, in_tz = [], 0
    for line in flat.splitlines():
        line = line.strip()
        up = line.upper()
        if up == "BEGIN:VTIMEZONE":
            in_tz += 1
        if in_tz:
            if up == "END:VTIMEZONE":
                in_tz -= 1
            continue
        if line and (c := _canonical_line(line)) is not None:
            out.append(c)
    return tuple(sorted(out))


def _name_for(uid: str, names: dict[str, str], ext: str) -> str:
    n = names.get(uid) or (re.sub(r"[^A-Za-z0-9._@-]+", "_", uid)[:120] + ext)
    return n if n.endswith(ext) else n + ext


# ---------------- the report ----------------


@dataclass
class Counts:
    collections_created: int = 0
    collections_existing: int = 0
    created: int = 0
    skipped: int = 0
    conflicts: int = 0
    replaced: int = 0  # conflicts replaced with the export's version (--overwrite)
    failed: int = 0
    not_restored: int = 0  # collections of external accounts

    def add(self, o: Counts) -> None:
        for k in self.__dataclass_fields__:
            setattr(self, k, getattr(self, k) + getattr(o, k))

    def line(self) -> str:
        return (f"{self.collections_created} collections made, {self.collections_existing} present; "
                f"{self.created} items created, {self.skipped} already there, {self.conflicts} conflicts, "
                f"{self.replaced} replaced, "
                f"{self.failed} failed" + (f"; {self.not_restored} external collections not restored"
                                           if self.not_restored else ""))


@dataclass
class Report:
    dry_run: bool = False
    owners: list[dict[str, Any]] = field(default_factory=list)
    total: Counts = field(default_factory=Counts)
    connectors: set[int] = field(default_factory=set)
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------- writing into the store ----------------


def _url(path: str) -> str:
    return get_settings().stalwart_url.rstrip("/") + path


def refusal(resp) -> str:
    """A short description of a refused write: the status and the WebDAV precondition the server
    named (`max-resource-size`, `valid-calendar-data`, `no-uid-conflict`, …), if any."""
    m = re.search(rb"<(?:[\w-]+:)?error\b[^>]*>\s*<(?:[\w-]+:)?([\w-]+)", resp.content[:4000] or b"")
    return f"HTTP {resp.status_code}" + (f" ({m.group(1).decode()})" if m else "")


async def _put(client: dav.DavClient, href: str, body: str, ctype: str, if_match: str | None = None) -> None:
    """Create (If-None-Match), or replace the version we read (If-Match)."""
    cond = {"If-Match": if_match} if if_match else {"If-None-Match": "*"}
    resp = await client.request("PUT", href, body=body.encode(), headers={
        "Content-Type": f"{ctype}; charset=utf-8", **cond})
    if resp.status_code >= 400 and resp.status_code not in (401, 404, 410, 412):
        cls = dav.Truncated if resp.status_code == 507 and b"number-of-matches" in resp.content else dav.ServerError
        raise cls(f"PUT {href}: {refusal(resp)}", resp.status_code)
    client._raise(resp, f"PUT {href}")


async def _existing(client: dav.DavClient, url: str, cards: bool) -> dict[str, tuple[str, str | None, str]] | None:
    """uid → (href, etag, text) of what the collection holds; None when the listing was cut short (the
    restore then relies on If-None-Match alone)."""
    try:
        tags = await client.etags(url)
    except dav.Truncated:
        return None
    if not tags:
        return {}
    got = await (client.multiget_cards(url, list(tags)) if cards else client.multiget(url, list(tags)))
    return {backup.uid_of(t): (h, e, t) for h, e, t in got if backup.uid_of(t)}


async def _conflict(client: dav.DavClient, href: str, etag: str | None, body: str, ctype: str, overwrite: bool,
                    dry: bool, c: Counts, report: Report, label: str, uid: str) -> None:
    """The store holds other content under this UID: reported, or replaced with `overwrite`."""
    if overwrite and etag:
        if dry:
            c.replaced += 1
            return
        try:
            await _put(client, href, body, ctype, if_match=etag)
            c.replaced += 1
            return
        except dav.DavError as e:
            report.errors.append(f"{label}: {uid}: {e}"[:300])
    c.conflicts += 1
    report.conflicts.append(f"{label}: {uid}")


async def put_items(client: dav.DavClient, url: str, items: list[tuple[str, str, str]],
                    have: dict[str, tuple[str, str | None, str]] | None, cards: bool, dry: bool, c: Counts,
                    report: Any, label: str, overwrite: bool = False, in_flight: int = 1) -> None:
    """Write [(uid, body, resource name)] into the collection at `url`, given what it holds
    (`have`, from _existing; None when unknown). Same content → skipped; other content under the
    UID → conflict (or replaced with `overwrite`); missing → PUT with If-None-Match: *. A name
    taken by another UID falls back to a name made from the UID. `report` takes .conflicts and
    .errors (lists). At most `in_flight` PUTs run at once. Shared by restore and the migration
    (calendar/migrate.py)."""
    import asyncio

    ext, ctype = (".vcf", "text/vcard") if cards else (".ics", "text/calendar")
    base = url if url.endswith("/") else url + "/"
    gate = asyncio.Semaphore(max(1, in_flight))

    async def one(uid: str, body: str, name: str) -> None:
        if have is not None and uid in have:
            href, etag, cur = have[uid]
            if normalized(cur) == normalized(body):
                c.skipped += 1
            else:
                await _conflict(client, href, etag, body, ctype, overwrite, dry, c, report, label, uid)
            return
        if dry:
            c.created += 1
            return
        names = [name, _name_for(uid, {}, ext)]
        for i, n in enumerate(dict.fromkeys(names)):
            href = base + quote(n, safe="@-._~")
            try:
                await _put(client, href, body, ctype)
                c.created += 1
                return
            except dav.PreconditionFailed:
                try:
                    cur, etag = await client.get(href)
                except dav.DavError:
                    cur, etag = "", None
                same_uid = bool(cur) and backup.uid_of(cur) == uid
                if same_uid and normalized(cur) == normalized(body):
                    c.skipped += 1
                    return
                if same_uid or i + 1 == len(dict.fromkeys(names)):
                    await _conflict(client, href, etag, body, ctype, overwrite and same_uid, dry, c, report, label,
                                    uid)
                    return
                # Another item holds this name: try the name made from the UID.
            except dav.DavError as e:
                c.failed += 1
                report.errors.append(f"{label}: {uid}: {e}"[:300])
                return

    async def gated(uid: str, body: str, name: str) -> None:
        async with gate:
            await one(uid, body, name)

    await asyncio.gather(*(gated(u, b, n) for u, b, n in items))


async def _restore_items(client: dav.DavClient, url: str, items: list[tuple[str, str]], names: dict[str, str],
                         cards: bool, dry: bool, c: Counts, report: Report, label: str,
                         overwrite: bool = False) -> None:
    have = await _existing(client, url, cards) if not dry or url else {}
    ext = ".vcf" if cards else ".ics"
    await put_items(client, url, [(uid, body, _name_for(uid, names, ext)) for uid, body in items], have, cards, dry,
                    c, report, label, overwrite)


async def _mkcol_addressbook(client: dav.DavClient, home: str, slug: str, name: str) -> str:
    from xml.sax.saxutils import escape

    href = client.abs(home + quote(slug, safe="-._~") + "/")
    body = ('<?xml version="1.0" encoding="utf-8"?><d:mkcol xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
            '<d:set><d:prop><d:resourcetype><d:collection/><card:addressbook/></d:resourcetype>'
            f'<d:displayname>{escape(name)}</d:displayname></d:prop></d:set></d:mkcol>')
    resp = await client.request("MKCOL", href, body=body, headers={"Content-Type": "application/xml; charset=utf-8"})
    client._raise(resp, f"MKCOL {href}")
    return href


async def _collection(client: dav.DavClient, home: str, slug: str, entry: dict[str, Any], cards: bool,
                      dry: bool, c: Counts) -> str:
    """The collection's URL in `home`, made when missing (not in a dry run). "" when a dry run
    would make it."""
    url = client.abs(home + quote(slug, safe="-._~") + "/")
    try:
        await client.etags(url)
        c.collections_existing += 1
        return url
    except dav.Truncated:
        c.collections_existing += 1
        return url
    except dav.NotFound:
        pass
    c.collections_created += 1
    if dry:
        return ""
    if cards:
        return await _mkcol_addressbook(client, home, slug, entry.get("name") or slug)
    comps = [x for x in entry.get("components") or [] if x in CAL_TYPES] or ["VEVENT", "VTODO"]
    return await client.mkcalendar(home, slug, entry.get("name") or slug, comps, entry.get("color"))


def _read(snapshot: Path, rel: str) -> str:
    return (snapshot / rel).read_text(encoding="utf-8")


async def _restore_user(snapshot: Path, manifest: dict[str, Any], user: User, dry: bool, report: Report,
                        overwrite: bool = False) -> Counts:
    from mailr.calendar import builtin
    from mailr.calendar.groups import home_of
    from mailr.contacts import carddav

    counts = Counts()
    async with session_scope() as db:
        conn = await builtin.find(db, user.id)
    wanted = [e for e in manifest.get("collections") or [] if e.get("kind") == "builtin"]
    books = [e for e in manifest.get("address_books") or [] if e.get("kind") == "builtin"]
    counts.not_restored = sum(1 for e in (manifest.get("collections") or []) + (manifest.get("address_books") or [])
                              if e.get("kind") == "external")
    if not wanted and not books:
        return counts
    if conn is None:
        report.errors.append(f"user {user.id}: no Mailr calendar on this server; turn built-in calendars on first")
        counts.failed += sum(e.get("objects", 0) for e in wanted) + sum(e.get("cards", 0) for e in books)
        return counts
    account = (conn.config or {}).get("account") or ""
    cal_home = _url(home_of(account))
    card_home = _url(f"/dav/card/{quote(account.lower(), safe='')}/")
    label = f"user {user.id}"
    async with carddav.client_for(conn, user) as client:
        for e, cards in [(x, False) for x in wanted] + [(x, True) for x in books]:
            c = Counts()
            try:
                body = _read(snapshot, e["file"])
                items = split_cards(body) if cards else split_calendar(body)
                url = await _collection(client, card_home if cards else cal_home, e.get("slug") or "restored", e,
                                        cards, dry, c)
                names = {r["uid"]: r["name"] for r in e.get("resources") or [] if r.get("uid") and r.get("name")}
                if url or dry:
                    await _restore_items(client, url, items, names, cards, dry, c, report,
                                         f"{label} {e.get('name') or e.get('slug')}", overwrite)
            except (OSError, ValueError) as err:
                c.failed += 1
                report.errors.append(f"{label}: {e.get('file')}: {err}"[:300])
            except dav.DavError as err:
                c.failed += 1
                report.errors.append(f"{label}: {e.get('name') or e.get('slug')}: {err}"[:300])
            counts.add(c)
    report.connectors.add(conn.id)
    return counts


async def _group_target(db, e: dict[str, Any], dry: bool, c: Counts) -> tuple[GroupCalendar | None, CalendarGroup | None]:
    """The group calendar to restore `e` into, made again when it (or its store account) is gone.
    (None, None) in a dry run that would make it."""
    from mailr.calendar import groups

    gc = None
    if e.get("href"):
        gc = (await db.execute(select(GroupCalendar).where(GroupCalendar.href == e["href"]))).scalar_one_or_none()
    if gc is None:
        gc = (await db.execute(select(GroupCalendar).where(GroupCalendar.group_name == e["group"],
                                                           GroupCalendar.name == e["name"]))).scalars().first()
    if gc is None:
        c.collections_created += 1
        if dry:
            return None, None
        members = {u.id: u for u in await groups.members(db, e["group"])}
        by_email = {u.email.lower(): u.id for u in members.values()}
        eds = [by_email.get((x.get("email") or "").lower()) or (x.get("user_id") if x.get("user_id") in members else None)
               for x in e.get("editors") or []]
        gc = await groups.create(db, e["group"], e["name"], e.get("color"), e.get("access") or "write",
                                 [x for x in eds if x])
        await db.commit()
        return gc, await db.get(CalendarGroup, gc.group_name)
    cg = await db.get(CalendarGroup, gc.group_name)
    owner_ok = gc.access == "write" or (cg is not None and await groups._account_exists(cg.owner_id))
    accounts_ok = cg is not None and await groups._account_exists(cg.store_group_id) and owner_ok
    if accounts_ok and gc.href:
        try:
            async with groups._client_for(cg, gc) as cl:
                await cl.etags(groups._url(gc.href))
            c.collections_existing += 1
            return gc, cg
        except dav.Truncated:
            c.collections_existing += 1
            return gc, cg
        except dav.NotFound:
            pass
    c.collections_created += 1
    if dry:
        return None, None
    cg = await groups.ensure_group(db, gc.group_name)
    home = groups.home_of(cg.store_group_login or "") if gc.access == "write" else groups.home_of(
        await groups.ensure_owner(db, cg))
    slug = backup._slug(gc.href or "") or f"restored-{gc.id}"
    async with groups._client_for(cg, gc) as cl:
        href = await cl.mkcalendar(groups._url(home), slug, gc.name, groups.COMPONENTS, gc.color)
    gc.href = groups.path_of(href)
    await db.flush()
    if gc.access == "read":
        await groups._write_acl(db, cg, gc)
    await db.commit()
    await groups.reconcile()
    return gc, cg


async def _restore_groups(snapshot: Path, manifest: dict[str, Any], dry: bool, report: Report,
                          overwrite: bool = False) -> Counts:
    from mailr.calendar import builtin, groups

    counts = Counts()
    affected: set[int] = set()
    for e in manifest.get("collections") or []:
        c = Counts()
        label = f"group {e.get('group')!r} {e.get('name')!r}"
        try:
            items = split_calendar(_read(snapshot, e["file"]))
            async with session_scope() as db:
                if not await builtin.instance_enabled(db):
                    raise dav.DavError("built-in calendars are off; turn them on first")
                gc, cg = await _group_target(db, e, dry, c)
                if gc is not None:
                    affected |= {u.id for u in await groups.members(db, gc.group_name)} | set(gc.editors or [])
            names = {r["uid"]: r["name"] for r in e.get("resources") or [] if r.get("uid") and r.get("name")}
            if gc is None:  # a dry run that would make it: everything would be created
                c.created += len(items)
            else:
                async with groups._client_for(cg, gc) as cl:
                    await _restore_items(cl, groups._url(gc.href or ""), items, names, False, dry, c, report, label,
                                         overwrite)
        except (OSError, ValueError) as err:
            c.failed += 1
            report.errors.append(f"{label}: {err}"[:300])
        except Exception as err:  # noqa: BLE001  one calendar must not stop the others
            c.failed += 1
            report.errors.append(f"{label}: {err}"[:300])
        counts.add(c)
    if affected:
        async with session_scope() as db:
            ids = (await db.execute(select(Connector.id).where(Connector.builtin.is_(True),
                                                               Connector.user_id.in_(list(affected))))).scalars().all()
        report.connectors |= set(ids)
    return counts


async def _user_for(manifest: dict[str, Any]) -> User | None:
    owner = manifest.get("owner") or {}
    async with session_scope() as db:
        if owner.get("email"):
            u = (await db.execute(select(User).where(func.lower(User.email) == owner["email"].lower()))).scalar_one_or_none()
            if u is not None:
                return u
        return await db.get(User, int(owner["user_id"])) if owner.get("user_id") else None


def _matches(manifest: dict[str, Any], user: User | None, only: str | None) -> bool:
    if only is None:
        return True
    if user is None:
        return False
    return only.lower() == (user.email or "").lower() or (only.isdigit() and int(only) == user.id)


async def restore(src: Path | str, *, user: str | None = None, groups_only: bool = False, dry_run: bool = False,
                  overwrite: bool = False, resync: bool = True) -> Report:
    """Restore from an export folder (see find_snapshots). `user` (email or id) limits it to one
    user; `groups_only` to the group calendars. Without either, everything found is restored.
    `overwrite`: a UID whose content on the store differs gets the export's version (after a
    volume restore, the export is the newer copy); without it the store's version stays."""
    report = Report(dry_run=dry_run)
    snaps = find_snapshots(src)
    if not snaps:
        report.errors.append(f"no calendar export found in {src}")
        return report
    for snap in snaps:
        manifest = backup.read_manifest(snap)
        if manifest is None or manifest.get("format") != backup.FORMAT:
            report.errors.append(f"{snap}: not a calendar export (no readable manifest)")
            continue
        owner = manifest.get("owner") or {}
        if owner.get("type") == "groups":
            if user is not None:
                continue
            counts = await _restore_groups(snap, manifest, dry_run, report, overwrite)
            report.owners.append({"owner": "group calendars", "snapshot": snap.name, "counts": counts})
        else:
            if groups_only:
                continue
            u = await _user_for(manifest)
            if not _matches(manifest, u, user):
                continue
            if u is None:
                report.errors.append(f"{snap}: the user {owner.get('user_id')} is not on this server")
                continue
            counts = await _restore_user(snap, manifest, u, dry_run, report, overwrite)
            report.owners.append({"owner": f"user {u.id} ({u.email})", "snapshot": snap.name, "counts": counts})
        report.total.add(counts)
    if resync and not dry_run and report.connectors:
        await force_resync(sorted(report.connectors))
    return report


# ---------------- forced resync ----------------


async def connectors_for(*, user: str | None = None, include_external: bool = False) -> list[int]:
    """The connectors `mailr calendars resync` acts on: every built-in one (or one user's), plus
    the external CalDAV accounts with include_external."""
    async with session_scope() as db:
        q = select(Connector.id).where(Connector.kind == "caldav")
        if not include_external:
            q = q.where(Connector.builtin.is_(True))
        if user is not None:
            u = (await db.execute(select(User).where(func.lower(User.email) == user.lower()))).scalar_one_or_none()
            if u is None and user.isdigit():
                u = await db.get(User, int(user))
            if u is None:
                return []
            q = q.where(Connector.user_id == u.id)
        return list((await db.execute(q.order_by(Connector.id))).scalars())


async def force_resync(connector_ids: Iterable[int]) -> int:
    """Clear the sync tokens and ctags of these connectors' calendars and address books and queue
    a full sync of each. Returns how many connectors were queued."""
    ids = sorted(set(connector_ids))
    if not ids:
        return 0
    async with session_scope() as db:
        await db.execute(text("UPDATE cal_collections SET sync_token = NULL, ctag = NULL, next_sync_at = now() "
                              "WHERE connector_id = ANY(:ids)"), {"ids": ids})
        await db.execute(text("UPDATE address_books SET sync_token = NULL, ctag = NULL WHERE connector_id = ANY(:ids)"),
                         {"ids": ids})
        conns = (await db.execute(select(Connector).where(Connector.id.in_(ids)))).scalars().all()
        for c in conns:
            await enqueue(db, "calendar.sync", {"connector_id": c.id, "full": True}, user_id=c.user_id,
                          dedupe_key=f"calendar.resync:{c.id}")
            await enqueue(db, "contacts.sync", {"connector_id": c.id, "full": True}, user_id=c.user_id,
                          dedupe_key=f"contacts.resync:{c.id}")
    log.info("forced a full resync of %d calendar connectors", len(ids))
    return len(ids)
