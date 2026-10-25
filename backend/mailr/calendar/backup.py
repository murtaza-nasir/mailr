"""Daily export of calendars and address books.

The mail export is core/backup.py; this one follows
its conventions (atomic markers, `.last_run`, the backup target and its marker file).

What is exported, from the Postgres mirror (the raw resources in cal_objects.ics and the cards in
contact_cards.vcard), never from the store:

- per user: every calendar and task list of every CalDAV account (the built-in one and the
  user's own servers), one .ics file each (one VCALENDAR with every event, task and journal and
  each VTIMEZONE once), and every address book, one .vcf file each. Collections of the built-in
  account that live in another account's home (group calendars, calendars shared with the user)
  are left out here: their owner's export holds them.
- once for the whole server: every group calendar (calendar/groups.py), from the freshest copy
  in a member's mirror, else read from the store.
- a manifest.json per snapshot: each collection's name, colour, components, kind (builtin,
  external, group), access, group, owner, its path on the server and the resource name of each
  UID, so `mailr calendars restore` (calendar/restore.py) can make the collections again.

local_only accounts are exported like the others, as the mail export does: the export stays on
the server and on the admin's backup target and never reaches a model. The manifest records each
account's data_class. Files are plain text, like the mail export: protect the target (its own
snapshots, its own access rules).

Layout (`<data_dir>/exports/calendars/`, and `<MAILR_BACKUP_TARGET>/calendars/` when set):

    user-<id>/<snapshot>/manifest.json
    user-<id>/<snapshot>/calendars/<nn>-<name>.ics
    user-<id>/<snapshot>/contacts/<nn>-<name>.vcf
    user-<id>/LATEST       the newest snapshot's name
    user-<id>/.last_run    when a run last completed (the readiness check reads it)
    groups/...             the same for the group calendars

A snapshot (`2026-10-07T031500Z`) is written into a temporary folder, fsynced and renamed, so it is
complete or absent. A run whose content equals the newest snapshot writes no new one and only
records the run. Retention: the newest snapshot always stays; within CALENDAR_EXPORT_KEEP_DAYS the
newest snapshot of each day stays; older ones go. The same rule applies on the target.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from sqlalchemy import select

from mailr.config import get_settings
from mailr.core import private
from mailr.core.backup import (
    LAST_RUN,
    _fsync_dir,
    _fsync_file,
    _write_atomic,
    last_run,
    target_problem,
)
from mailr.db import session_scope
from mailr.models import (
    AddressBook,
    CalCollection,
    CalendarGroup,
    CalObject,
    Connector,
    ContactCard,
    GroupCalendar,
    User,
)

log = logging.getLogger("mailr.calendar.backup")

FORMAT = "mailr-calendar-export/1"
MANIFEST = "manifest.json"
LATEST = "LATEST"
GROUPS = "groups"
SNAPSHOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{6}Z$")


# ---------------- where ----------------


def root() -> Path:
    return Path(get_settings().data_dir) / "exports" / "calendars"


def owner_name(user_id: int | None) -> str:
    """The folder of one user's export, or of the group calendars (None)."""
    return GROUPS if user_id is None else f"user-{user_id}"


def export_dir(user_id: int | None) -> Path:
    return root() / owner_name(user_id)


def target_dir(user_id: int | None) -> Path | None:
    """The folder on the backup target, or None without a target."""
    t = get_settings().backup_target
    return Path(t) / "calendars" / owner_name(user_id) if t else None


def _ready_target_dir(user_id: int | None) -> Path | None:
    """The target folder when the target is usable (made inside it; the target itself never)."""
    tgt = target_dir(user_id)
    if tgt is None or target_problem() is not None:
        return None
    try:
        private.mkdirs(tgt)
    except OSError as e:
        log.warning("backup target for calendars of %s: %s", owner_name(user_id), e)
        return None
    return tgt


def keep_days() -> int:
    return max(1, int(get_settings().calendar_export_keep_days or 1))


def _safe(name: str, fallback: str) -> str:
    s = re.sub(r"[^\w .-]+", "_", name or "", flags=re.UNICODE).strip().lstrip(".").strip()
    return (s[:60].strip() or fallback) if s.strip("._ ") else fallback


def _path(href: str) -> str:
    return urlsplit(href).path or href


def _slug(href: str) -> str:
    return unquote(_path(href).rstrip("/").rsplit("/", 1)[-1])


def _resource_name(href: str) -> str:
    return unquote(_path(href).rsplit("/", 1)[-1])


# ---------------- building the files ----------------


def merge_calendar(texts: Iterable[str], name: str = "") -> tuple[str, list[str]]:
    """One VCALENDAR with the components of every resource and each VTIMEZONE once, and the
    resources the parser refused (kept apart, so nothing is lost)."""
    from icalendar import Calendar

    from mailr.calendar import ical

    out = ical.new_calendar()
    if name:
        out.add("x-wr-calname", name)
    zones: set[str] = set()
    failed: list[str] = []
    for text in texts:
        try:
            c = Calendar.from_ical(text)
        except Exception:  # noqa: BLE001
            failed.append(text)
            continue
        for comp in c.subcomponents:
            if comp.name == "VTIMEZONE":
                tzid = str(comp.get("tzid", ""))
                if tzid in zones:
                    continue
                zones.add(tzid)
            if comp.name in ("VTIMEZONE", "VEVENT", "VTODO", "VJOURNAL"):
                out.add_component(comp)
    return out.to_ical().decode(), failed


def _esc(v: str) -> str:
    return (v or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def synthesize_vcard(card: dict[str, Any]) -> str:
    """A vCard 4.0 from the parsed card, for cards the mirror read before it kept the card itself.
    It holds what mailr parsed (contacts/vcard.py); the next address-book sync stores the original."""
    lines = ["BEGIN:VCARD", "VERSION:4.0"]
    if card.get("uid"):
        lines.append(f"UID:{_esc(card['uid'])}")
    if card.get("kind") == "group":
        lines.append("KIND:group")
    lines.append(f"FN:{_esc(card.get('fn') or card.get('n') or '')}")
    for e in card.get("emails") or []:
        lines.append(f"EMAIL:{_esc(e.get('email', ''))}")
    for p in card.get("phones") or []:
        lines.append(f"TEL:{_esc(p.get('value', ''))}")
    for a in card.get("addresses") or []:
        parts = ["", "", a.get("street", ""), a.get("city", ""), a.get("region", ""), a.get("postcode", ""),
                 a.get("country", "")]
        lines.append("ADR:" + ";".join(_esc(x) for x in parts))
    if card.get("organization"):
        lines.append(f"ORG:{_esc(card['organization'])}")
    if card.get("title"):
        lines.append(f"TITLE:{_esc(card['title'])}")
    if card.get("birthday"):
        lines.append(f"BDAY:{card['birthday']}")
    if card.get("categories"):
        lines.append("CATEGORIES:" + ",".join(_esc(c) for c in card["categories"]))
    for m in card.get("members") or []:
        lines.append(f"MEMBER:{'mailto:' + m if '@' in m else 'urn:uuid:' + m}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def _card_text(raw: str | None, data: dict[str, Any]) -> tuple[str, bool]:
    if raw and raw.strip():
        t = raw.replace("\r\n", "\n").replace("\n", "\r\n")
        return (t if t.endswith("\r\n") else t + "\r\n"), False
    return synthesize_vcard(data or {}), True


def _connector_meta(c: Connector) -> dict[str, Any]:
    meta = {"id": c.id, "name": c.name, "builtin": bool(c.builtin), "data_class": c.data_class,
            "enabled": bool(c.enabled)}
    if c.builtin:
        meta["account"] = (c.config or {}).get("account") or ""
    else:
        meta["url"] = (c.config or {}).get("url") or ""
    return meta


async def _objects(db, coll_id: int) -> list[tuple[str, str, str, str]]:
    return list((await db.execute(select(CalObject.href, CalObject.uid, CalObject.kind, CalObject.ics).where(
        CalObject.collection_id == coll_id, CalObject.deleted_at.is_(None)).order_by(CalObject.id))).all())


def _calendar_entry(files: dict[str, str], n: int, name: str, objs: list[tuple[str, str, str, str]]) -> dict[str, Any]:
    """Write one collection's .ics (and its refused resources) into `files`; the manifest entry."""
    fname = f"calendars/{n:02d}-{_safe(name, 'calendar')}.ics"
    text, failed = merge_calendar([o[3] for o in objs if o[3]], name)
    files[fname] = text
    entry: dict[str, Any] = {"file": fname, "objects": len(objs) - len(failed),
                             "resources": [{"uid": o[1], "name": _resource_name(o[0]), "kind": o[2]} for o in objs]}
    if failed:
        uf = fname[:-4] + ".unparsed.txt"
        files[uf] = "\n".join(failed)
        entry["unparsed"], entry["unparsed_file"] = len(failed), uf
    return entry


async def collect_user(user_id: int) -> tuple[dict[str, Any], dict[str, str]] | None:
    """The manifest and files of one user's export, or None for an unknown user."""
    from mailr.calendar.groups import home_of

    async with session_scope() as db:
        user = await db.get(User, user_id)
        if user is None:
            return None
        conns = (await db.execute(select(Connector).where(Connector.user_id == user_id, Connector.kind == "caldav")
                                  .order_by(Connector.id))).scalars().all()
        account = next(((c.config or {}).get("account") for c in conns if c.builtin), None) or None
        files: dict[str, str] = {}
        collections: list[dict[str, Any]] = []
        books: list[dict[str, Any]] = []
        skipped = 0
        for c in conns:
            own_cal = home_of(c.config["account"]) if c.builtin and (c.config or {}).get("account") else None
            colls = (await db.execute(select(CalCollection).where(
                CalCollection.connector_id == c.id, CalCollection.deleted_at.is_(None))
                .order_by(CalCollection.id))).scalars().all()
            for coll in colls:
                path = _path(coll.href)
                if c.builtin and (own_cal is None or not unquote(path).lower().startswith(unquote(own_cal).lower())):
                    skipped += 1  # a group calendar or someone else's: exported by its owner
                    continue
                name = coll.displayname or _slug(coll.href)
                entry = _calendar_entry(files, len(collections) + 1, name, await _objects(db, coll.id))
                collections.append({**entry, "kind": "builtin" if c.builtin else "external",
                                    "connector": _connector_meta(c), "href": coll.href, "path": path,
                                    "slug": _slug(coll.href), "name": name, "description": coll.description or "",
                                    "color": coll.color, "components": list(coll.components or []),
                                    "read_only": bool(coll.read_only), "calendar_tz": coll.calendar_tz,
                                    "hidden": bool(coll.hidden)})
            own_card = f"/dav/card/{own_cal.split('/')[3]}/" if own_cal else None
            for b in (await db.execute(select(AddressBook).where(AddressBook.connector_id == c.id)
                                       .order_by(AddressBook.id))).scalars().all():
                path = _path(b.href)
                if c.builtin and (own_card is None or not unquote(path).lower().startswith(unquote(own_card).lower())):
                    skipped += 1
                    continue
                cards = (await db.execute(select(ContactCard.href, ContactCard.uid, ContactCard.vcard, ContactCard.data)
                                          .where(ContactCard.book_id == b.id).order_by(ContactCard.id))).all()
                synthesized = 0
                parts = []
                for _h, _u, raw, data in cards:
                    t, syn = _card_text(raw, data)
                    parts.append(t)
                    synthesized += syn
                name = b.displayname or _slug(b.href)
                fname = f"contacts/{len(books) + 1:02d}-{_safe(name, 'contacts')}.vcf"
                files[fname] = "".join(parts)
                books.append({"file": fname, "kind": "builtin" if c.builtin else "external",
                              "connector": _connector_meta(c), "href": b.href, "path": path, "slug": _slug(b.href),
                              "name": name, "cards": len(cards), "synthesized": synthesized,
                              "resources": [{"uid": u, "name": _resource_name(h)} for h, u, _r, _d in cards]})
        manifest = {"format": FORMAT,
                    "owner": {"type": "user", "user_id": user.id, "email": user.email, "name": user.name or "",
                              "account": account},
                    "collections": collections, "address_books": books, "skipped_not_owned": skipped,
                    "counts": {"calendars": len(collections), "objects": sum(x["objects"] for x in collections),
                               "address_books": len(books), "cards": sum(x["cards"] for x in books)}}
    return manifest, files


async def _from_store(cg: CalendarGroup, gc: GroupCalendar) -> list[tuple[str, str, str, str]]:
    """A group calendar no member has mirrored: read from the store."""
    from mailr.calendar import groups

    async with groups._client_for(cg, gc) as c:
        href = groups._url(gc.href or "")
        tags = await c.etags(href)
        got = await c.multiget(href, list(tags)) if tags else []
    return [(h, uid_of(text), "event", text) for h, _e, text in got]


def uid_of(text: str) -> str:
    """The UID of a calendar resource or card (the first UID line, unfolded), or ""."""
    flat = re.sub(r"\r?\n[ \t]", "", text or "")
    m = re.search(r"^UID(?:;[^:\r\n]*)?:([^\r\n]*)", flat, flags=re.MULTILINE | re.IGNORECASE)
    return m.group(1).strip() if m else ""


async def collect_groups() -> tuple[dict[str, Any], dict[str, str]]:
    """Every group calendar, once: from the freshest member mirror, else from the store."""
    from mailr.calendar.groups import path_of

    files: dict[str, str] = {}
    collections: list[dict[str, Any]] = []
    async with session_scope() as db:
        gcs = (await db.execute(select(GroupCalendar).where(GroupCalendar.href.is_not(None))
                                .order_by(GroupCalendar.id))).scalars().all()
        mirrored = (await db.execute(select(CalCollection).join(Connector, Connector.id == CalCollection.connector_id)
                                     .where(Connector.builtin.is_(True), CalCollection.deleted_at.is_(None)))).scalars().all()
        best: dict[str, CalCollection] = {}
        for coll in mirrored:
            p = path_of(coll.href)
            cur = best.get(p)
            if cur is None or (coll.last_sync_at or datetime.min.replace(tzinfo=UTC)) > (
                    cur.last_sync_at or datetime.min.replace(tzinfo=UTC)):
                best[p] = coll
        for gc in gcs:
            cg = await db.get(CalendarGroup, gc.group_name)
            coll = best.get(gc.href or "")
            source = "mirror"
            if coll is not None:
                objs = await _objects(db, coll.id)
            else:
                source = "store"
                try:
                    objs = await _from_store(cg, gc) if cg is not None else []
                except Exception as e:  # noqa: BLE001  one calendar must not stop the others
                    log.warning("group calendar %s: not mirrored and not readable from the store: %s", gc.id, e)
                    objs, source = [], "unavailable"
            editors = []
            for uid in gc.editors or []:
                u = await db.get(User, uid)
                editors.append({"user_id": uid, "email": u.email if u else None})
            entry = _calendar_entry(files, len(collections) + 1, gc.name, objs)
            collections.append({**entry, "kind": "group", "group": gc.group_name, "group_calendar_id": gc.id,
                                "access": gc.access, "editors": editors, "href": gc.href, "path": gc.href,
                                "slug": _slug(gc.href or ""), "name": gc.name, "color": gc.color,
                                "components": ["VEVENT"], "source": source,
                                "store": {"group_login": cg.store_group_login if cg else None,
                                          "owner_login": cg.owner_login if cg else None}})
    manifest = {"format": FORMAT, "owner": {"type": "groups"}, "collections": collections, "address_books": [],
                "counts": {"calendars": len(collections), "objects": sum(x["objects"] for x in collections),
                           "address_books": 0, "cards": 0}}
    return manifest, files


# ---------------- snapshots on disk ----------------


def digest(manifest: dict[str, Any], files: dict[str, str]) -> str:
    h = hashlib.sha256()
    meta = {k: v for k, v in manifest.items() if k not in ("exported_at", "digest", "snapshot")}
    h.update(json.dumps(meta, sort_keys=True, default=str).encode())
    for name in sorted(files):
        h.update(b"\0" + name.encode() + b"\0" + files[name].encode("utf-8", "replace"))
    return h.hexdigest()


def snapshots(base: Path) -> list[str]:
    """Complete snapshots in a folder, oldest first."""
    try:
        return sorted(e.name for e in base.iterdir() if e.is_dir() and SNAPSHOT_RE.match(e.name)
                      and (e / MANIFEST).is_file())
    except OSError:
        return []


def latest(base: Path) -> str | None:
    try:
        name = (base / LATEST).read_text().strip()
        if SNAPSHOT_RE.match(name) and (base / name / MANIFEST).is_file():
            return name
    except OSError:
        pass
    snaps = snapshots(base)
    return snaps[-1] if snaps else None


def read_manifest(snapshot: Path) -> dict[str, Any] | None:
    try:
        return json.loads((snapshot / MANIFEST).read_text())
    except (OSError, ValueError):
        return None


def _write_tree(dest: Path, manifest: dict[str, Any], files: dict[str, str]) -> None:
    """Write a snapshot into a temporary folder beside `dest`, fsync it and rename it into place."""
    tmp = dest.parent / f".tmp-{dest.name}-{secrets.token_hex(4)}"
    try:
        private.mkdirs(tmp)
        dirs = {tmp}
        for rel, text in files.items():
            p = tmp / rel
            private.mkdirs(p.parent)
            dirs.add(p.parent)
            private.write_text(p, text, encoding="utf-8", newline="")
            _fsync_file(p)
        private.write_text(tmp / MANIFEST, json.dumps(manifest, indent=1, ensure_ascii=False, default=str))
        _fsync_file(tmp / MANIFEST)
        for d in dirs:
            _fsync_dir(d)
        os.rename(tmp, dest)
        _fsync_dir(dest.parent)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _copy_tree(src: Path, dest: Path) -> None:
    tmp = dest.parent / f".tmp-{dest.name}-{secrets.token_hex(4)}"
    try:
        shutil.copytree(src, tmp)
        for dirpath, _dirs, names in os.walk(tmp):
            for n in names:
                _fsync_file(Path(dirpath) / n)
            _fsync_dir(Path(dirpath))
        os.rename(tmp, dest)
        _fsync_dir(dest.parent)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def prune(base: Path, now: datetime | None = None) -> list[str]:
    """Retention: keep the newest snapshot, and the newest of each day within keep_days(). Also
    removes temporary folders a crashed run left. Returns the names removed."""
    now = now or datetime.now(UTC)
    snaps = snapshots(base)
    if not snaps:
        return []
    keep = {snaps[-1]}
    cutoff = (now - timedelta(days=keep_days())).strftime("%Y-%m-%d")
    by_day: dict[str, str] = {}
    for s in snaps:
        by_day[s[:10]] = s  # sorted: the last one of a day wins
    keep |= {s for day, s in by_day.items() if day >= cutoff}
    gone = [s for s in snaps if s not in keep]
    for s in gone:
        shutil.rmtree(base / s, ignore_errors=True)
    try:
        for e in base.iterdir():
            if e.is_dir() and e.name.startswith(".tmp-") and \
                    now.timestamp() - e.stat().st_mtime > 6 * 3600:
                shutil.rmtree(e, ignore_errors=True)
    except OSError:
        pass
    return gone


def store_snapshot(base: Path, manifest: dict[str, Any], files: dict[str, str]) -> str | None:
    """Write a new snapshot unless the newest one holds the same content. Returns its name, or
    None when nothing changed."""
    private.mkdirs(base)
    d = digest(manifest, files)
    cur = latest(base)
    if cur is not None and (read_manifest(base / cur) or {}).get("digest") == d:
        return None
    now = datetime.now(UTC)
    name = now.strftime("%Y-%m-%dT%H%M%SZ")
    newest = (snapshots(base) or [""])[-1]
    while (base / name).exists() or name <= newest:  # names sort in time order, also within one second
        now += timedelta(seconds=1)
        name = now.strftime("%Y-%m-%dT%H%M%SZ")
    _write_tree(base / name, {**manifest, "snapshot": name, "exported_at": now.isoformat(), "digest": d}, files)
    _write_atomic(base / LATEST, name)
    prune(base)
    return name


def copy_to_target(base: Path, tgt: Path) -> str | None:
    """Put the newest local snapshot on the target (when it lacks it), then LATEST and the
    retention there. Returns the name copied, or None."""
    name = latest(base)
    if name is None:
        return None
    copied = None
    if not (tgt / name / MANIFEST).is_file():
        _copy_tree(base / name, tgt / name)
        copied = name
    _write_atomic(tgt / LATEST, name)
    prune(tgt)
    return copied


def _mark(base: Path, tgt: Path | None) -> None:
    now = datetime.now(UTC).isoformat()
    private.mkdirs(base)
    _write_atomic(base / LAST_RUN, now)
    if tgt is not None:
        _write_atomic(tgt / LAST_RUN, now)


def _finish(user_id: int | None, manifest: dict[str, Any] | None, files: dict[str, str]) -> dict[str, Any]:
    """Write the snapshot (when there is something to keep), copy it, record the run."""
    base = export_dir(user_id)
    out: dict[str, Any] = {"owner": owner_name(user_id), "snapshot": None, "copied": None}
    # Nothing to keep and no earlier snapshot: no folder. With earlier snapshots an empty one is
    # written, so LATEST never shows calendars that are gone.
    has_data = manifest is not None and (manifest["collections"] or manifest["address_books"]
                                         or latest(base) is not None)
    if has_data:
        out["snapshot"] = store_snapshot(base, manifest, files)
        out["counts"] = manifest["counts"]
    tgt = None
    if target_dir(user_id) is not None:
        tgt = _ready_target_dir(user_id)
        if tgt is None:
            log.warning("backup target for calendars of %s: %s", owner_name(user_id), target_problem())
        elif latest(base) is not None:
            out["copied"] = copy_to_target(base, tgt)
    _mark(base, tgt)
    return out


async def export_user(user_id: int) -> dict[str, Any]:
    """One user's calendar and address-book export. Returns what was written."""
    import asyncio

    got = await collect_user(user_id)
    if got is None:
        return {"owner": owner_name(user_id), "snapshot": None}
    manifest, files = got
    return await asyncio.to_thread(_finish, user_id, manifest, files)


async def export_groups() -> dict[str, Any]:
    """The group calendars' export (once for the server)."""
    import asyncio

    manifest, files = await collect_groups()
    return await asyncio.to_thread(_finish, None, manifest, files)


# ---------------- the readiness check ----------------


async def owners_with_data() -> list[int | None]:
    """Active users with calendar or address-book data in the mirror, and None for the group
    calendars when any exist: whose export the readiness check expects."""
    from sqlalchemy import text

    async with session_scope() as db:
        users = list((await db.execute(text(
            "SELECT u.id FROM users u WHERE NOT u.disabled AND (EXISTS (SELECT 1 FROM cal_collections c "
            "WHERE c.user_id = u.id AND c.deleted_at IS NULL) OR EXISTS (SELECT 1 FROM address_books b "
            "WHERE b.user_id = u.id)) ORDER BY u.id"))).scalars())
        has_groups = (await db.execute(text("SELECT 1 FROM group_calendars WHERE href IS NOT NULL LIMIT 1"))).scalar()
    return [*users, *([None] if has_groups else [])]


def runs(owners: list[int | None], on_target: bool) -> list[datetime | None]:
    return [last_run(target_dir(o) if on_target else export_dir(o)) for o in owners]


async def data_since(owners: list[int | None]) -> list[datetime | None]:
    """When each owner's calendar data appeared (its oldest calendar or address-book account; for
    None, the oldest group calendar): the readiness check gives an owner that has never been
    exported until then plus EXPORT_STALE for its first export. None when unknown."""
    from sqlalchemy import text

    ids = [o for o in owners if o is not None]
    async with session_scope() as db:
        rows = dict((await db.execute(text(
            "SELECT u.id, LEAST((SELECT min(c.created_at) FROM cal_collections c WHERE c.user_id = u.id "
            "AND c.deleted_at IS NULL), (SELECT min(k.created_at) FROM address_books b JOIN connectors k "
            "ON k.id = b.connector_id WHERE b.user_id = u.id)) FROM users u WHERE u.id = ANY(:ids)"),
            {"ids": ids})).all()) if ids else {}
        groups = (await db.execute(text(
            "SELECT min(created_at) FROM group_calendars WHERE href IS NOT NULL"))).scalar() if None in owners else None
    return [groups if o is None else rows.get(o) for o in owners]
