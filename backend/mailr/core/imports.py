"""Imports for people moving in: Google Keep, calendar files and contact cards.

Each import is offered in its app's ⋮ menu and runs in two steps:

1. **Preview** (`preview`): the file is read and counted, nothing is written. The counts say what
   would be made, what is already there and what is left out, with a few examples.
2. **Import** (`run`): the same reading, then the writes, recorded as one `ImportBatch` with what
   was made. **Undo** (`undo`) takes the whole import back in one step.

The kinds:

- `keep`: a Google Takeout export of Keep. The .zip Takeout makes, or the .json (and .html) files
  inside its Keep folder. A note becomes a text note, a list becomes a checklist (checked items
  stay checked), labels become note labels, the colour and pin carry over, archived notes stay
  archived. Notes in Keep's trash and attachments are left out. Undo moves the notes to Trash
  (30 days, as any deleted note) and removes the labels the import made, when no other note uses
  them.
- `ics`: an .ics file into one calendar, each UID as the file wrote it (calendar/routes_collections
  .split_resources). An event already in the calendars is left alone. No invitation mail is sent.
  Undo deletes the events the import made.
- `vcf`: a .vcf file into People. A card's email addresses key its person. A new person is made;
  a person who exists keeps what they have, and only empty fields are filled. Cards without an
  email address are left out (People are found by address). Undo removes the people the import
  made and empties the fields it filled.

Files come as uploads (POST /uploads, 35 MB each). A .zip is read member by member with limits,
so a crafted archive cannot exhaust memory.
"""

from __future__ import annotations

import html
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr import i18n
from mailr.calendar.describe import calendar_name
from mailr.i18n import ApiError
from mailr.models import ImportBatch, Person, User

KINDS = ("keep", "ics", "vcf")
MAX_FILES = 20
ZIP_MEMBERS = 5000
ZIP_TOTAL = 60 * 1024 * 1024
MEMBER_MAX = 5 * 1024 * 1024
MAX_NOTES = 5000
MAX_CARDS = 5000
SAMPLE = 5

# Keep's colours, as mailr's note colours (notes/core.COLORS).
KEEP_COLORS = {"RED": "tomato", "ORANGE": "tangerine", "YELLOW": "banana", "GREEN": "sage", "TEAL": "basil",
               "BLUE": "peacock", "CERULEAN": "blueberry", "PURPLE": "grape", "PINK": "flamingo",
               "BROWN": "cocoa", "GRAY": "graphite"}


# ---------------------------------------------------------------- reading files


@dataclass
class SourceFile:
    name: str
    data: bytes


def expand(files: list[SourceFile], wanted: tuple[str, ...]) -> tuple[list[SourceFile], int]:
    """The files with the wanted extensions, a .zip opened into its members. Returns (files, skipped)."""
    out: list[SourceFile] = []
    skipped = 0
    for f in files:
        if f.name.lower().endswith(".zip") or f.data[:4] == b"PK\x03\x04":
            try:
                zf = zipfile.ZipFile(io.BytesIO(f.data))
            except zipfile.BadZipFile:
                raise ApiError(400, "import.not_a_zip", name=f.name) from None
            total = 0
            infos = zf.infolist()
            if len(infos) > ZIP_MEMBERS:
                raise ApiError(413, "import.zip_too_many_files", name=f.name, max=ZIP_MEMBERS)
            for info in infos:
                if info.is_dir():
                    continue
                low = info.filename.lower()
                if not low.endswith(wanted):
                    skipped += 1
                    continue
                if info.file_size > MEMBER_MAX:
                    skipped += 1
                    continue
                total += info.file_size
                if total > ZIP_TOTAL:
                    raise ApiError(413, "import.zip_too_large", name=f.name, mb=ZIP_TOTAL // (1024 * 1024))
                with zf.open(info) as fh:
                    data = fh.read(MEMBER_MAX + 1)
                if len(data) > MEMBER_MAX:
                    skipped += 1
                    continue
                out.append(SourceFile(info.filename, data))
        elif f.name.lower().endswith(wanted):
            out.append(f)
        else:
            skipped += 1
    return out, skipped


def _text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


# ---------------------------------------------------------------- Google Keep


@dataclass
class KeepNote:
    title: str = ""
    text: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    color: str | None = None
    pinned: bool = False
    archived: bool = False
    trashed: bool = False
    attachments: int = 0

    @property
    def kind(self) -> str:
        return "checklist" if self.items else "text"


def keep_from_json(data: bytes) -> KeepNote | None:
    """One note from a Keep Takeout .json file, or None when the file is not a Keep note."""
    try:
        d = json.loads(_text(data))
    except ValueError:
        return None
    if not isinstance(d, dict) or not any(k in d for k in ("textContent", "listContent", "title")):
        return None
    links = [a.get("url") for a in d.get("annotations") or [] if isinstance(a, dict) and a.get("url")]
    text = str(d.get("textContent") or "")
    if links:
        text = (text + "\n\n" if text else "") + "\n".join(str(u) for u in links)
    items = [{"text": str(i.get("text") or ""), "checked": bool(i.get("isChecked"))}
             for i in d.get("listContent") or [] if isinstance(i, dict) and str(i.get("text") or "").strip()]
    return KeepNote(title=str(d.get("title") or ""), text=text if not items else "", items=items,
                    labels=[str(lb.get("name")) for lb in d.get("labels") or [] if isinstance(lb, dict) and lb.get("name")],
                    color=KEEP_COLORS.get(str(d.get("color") or "").upper()), pinned=bool(d.get("isPinned")),
                    archived=bool(d.get("isArchived")), trashed=bool(d.get("isTrashed")),
                    attachments=len(d.get("attachments") or []))


class _KeepHtml(HTMLParser):
    """The parts of a Keep Takeout .html note: title, content, list items, labels."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.title: list[str] = []
        self.content: list[str] = []
        self.items: list[dict[str, Any]] = []
        self.labels: list[str] = []
        self.archived = False
        self._item: dict[str, Any] | None = None
        self._in: str | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        cls = dict(attrs).get("class") or ""
        classes = set(cls.split())
        if self._in:
            self._depth += 1
            if tag == "br" and self._in == "content":
                self.content.append("\n")
        if tag == "br":
            self._depth -= 1 if self._in else 0
        if "title" in classes and tag == "div":
            self._in, self._depth = "title", 0
        elif "content" in classes and tag == "div":
            self._in, self._depth = "content", 0
        elif "listitem" in classes:
            self._item = {"text": "", "checked": "checked" in classes}
        elif "text" in classes and self._item is not None:
            self._in, self._depth = "item", 0
        elif "label-name" in classes:
            self._in, self._depth = "label", 0
            self.labels.append("")
        elif "archived" in classes:
            self.archived = True

    def handle_endtag(self, tag: str) -> None:
        if self._in:
            if self._depth == 0:
                if self._in == "item" and self._item is not None:
                    self.items.append(self._item)
                    self._item = None
                self._in = None
            else:
                self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in == "title":
            self.title.append(data)
        elif self._in == "content":
            self.content.append(data)
        elif self._in == "item" and self._item is not None:
            self._item["text"] += data
        elif self._in == "label" and self.labels:
            self.labels[-1] += data


def keep_from_html(data: bytes) -> KeepNote | None:
    p = _KeepHtml()
    try:
        p.feed(_text(data))
    except Exception:  # noqa: BLE001 - a broken file is left out, never a 500
        return None
    title = html.unescape("".join(p.title)).strip()
    text = "".join(p.content).strip()
    items = [{"text": " ".join(i["text"].split()), "checked": i["checked"]} for i in p.items if i["text"].strip()]
    if not (title or text or items):
        return None
    return KeepNote(title=title, text="" if items else text, items=items,
                    labels=[x.strip() for x in p.labels if x.strip()], archived=p.archived)


def read_keep(files: list[SourceFile]) -> tuple[list[KeepNote], dict[str, int]]:
    """The notes of a Keep export. A note in both .json and .html form is read once, from the .json."""
    got, skipped = expand(files, (".json", ".html"))
    stems_json = {re.sub(r"\.json$", "", f.name, flags=re.I) for f in got if f.name.lower().endswith(".json")}
    notes: list[KeepNote] = []
    unreadable = 0
    for f in got:
        low = f.name.lower()
        if low.endswith(".html"):
            if re.sub(r"\.html$", "", f.name, flags=re.I) in stems_json or low.endswith("index.html"):
                continue
            n = keep_from_html(f.data)
        else:
            if low.endswith("labels.json"):
                continue
            n = keep_from_json(f.data)
        if n is None:
            unreadable += 1
            continue
        notes.append(n)
        if len(notes) > MAX_NOTES:
            raise ApiError(413, "import.too_many_notes", max=MAX_NOTES)
    return notes, {"other_files": skipped, "unreadable": unreadable}


# ---------------------------------------------------------------- contact cards


def split_cards(text: str) -> list[str]:
    out, cur, inside = [], [], False
    for line in text.splitlines():
        u = line.strip().upper()
        if u == "BEGIN:VCARD":
            inside, cur = True, [line]
            continue
        if inside:
            cur.append(line)
            if u == "END:VCARD":
                out.append("\n".join(cur))
                inside = False
    return out


def read_vcf(files: list[SourceFile]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    from mailr.contacts import vcard

    got, skipped = expand(files, (".vcf", ".vcard"))
    cards: list[dict[str, Any]] = []
    groups = no_email = 0
    for f in got:
        for raw in split_cards(_text(f.data)):
            c = vcard.parse(raw)
            if c["kind"] == "group":
                groups += 1
                continue
            if not c["emails"]:
                no_email += 1
                continue
            cards.append(c)
            if len(cards) > MAX_CARDS:
                raise ApiError(413, "import.too_many_contacts", max=MAX_CARDS)
    return cards, {"other_files": skipped, "no_email": no_email, "groups": groups}


# ---------------------------------------------------------------- the three kinds


@dataclass
class Plan:
    kind: str
    counts: dict[str, int]
    sample: list[str]
    data: Any = None
    target: dict[str, Any] | None = None


async def plan(db: AsyncSession, user: User, p: Any, kind: str, files: list[SourceFile],
               calendar: str | None = None) -> Plan:
    """Read and count, writing nothing (the preview, and the first half of an import)."""
    if kind == "keep":
        notes, extra = read_keep(files)
        live = [n for n in notes if not n.trashed]
        labels = sorted({lb for n in live for lb in n.labels}, key=str.lower)
        from mailr.notes.actions import labels_of

        have = {lb.name.lower() for lb in await labels_of(db, user)}
        counts = {"notes": sum(1 for n in live if n.kind == "text"),
                  "checklists": sum(1 for n in live if n.kind == "checklist"),
                  "labels": len(labels), "new_labels": sum(1 for lb in labels if lb.lower() not in have),
                  "archived": sum(1 for n in live if n.archived), "pinned": sum(1 for n in live if n.pinned),
                  "trashed": len(notes) - len(live), "attachments": sum(n.attachments for n in live), **extra}
        return Plan(kind, counts, [n.title or (n.text or (n.items[0]["text"] if n.items else ""))[:60]
                                   for n in live[:SAMPLE]], live)
    if kind == "ics":
        from mailr.calendar import mirror
        from mailr.calendar import (
            routes as _routes,  # noqa: F401 - routes_collections imports it first
        )
        from mailr.calendar.routes_collections import split_resources

        got, skipped = expand(files, (".ics", ".ical", ".ifb", ".icalendar"))
        resources: list[tuple[str, Any]] = []
        bad = 0
        from mailr.calendar import zones

        for f in got:
            try:
                # Off the event loop, within a time budget.
                resources += await zones.off_loop(split_resources, _text(f.data))
            except Exception:  # noqa: BLE001
                bad += 1
        from mailr.calendar.routes_collections import IMPORT_MAX_EVENTS

        if len(resources) > IMPORT_MAX_EVENTS:
            raise ApiError(413, "import.too_many_events", max=IMPORT_MAX_EVENTS)
        existing = 0
        for uid, _ in resources:
            if await mirror.find_by_uid(db, user.id, uid) is not None:
                existing += 1
        target = await _ics_target(db, user, p, calendar)
        untitled = i18n.t("import.untitled", i18n.user_language(user))
        sample = [str(next(iter(c.walk("VEVENT"))).get("summary") or untitled) for _, c in resources[:SAMPLE]]
        return Plan(kind, {"events": len(resources) - existing, "existing": existing, "unreadable": bad,
                           "other_files": skipped}, sample, resources, target)
    if kind == "vcf":
        cards, extra = read_vcf(files)
        emails = {c["emails"][0]["email"] for c in cards}
        known = set((await db.execute(select(Person.email).where(
            Person.user_id == user.id, Person.email.in_(list(emails))))).scalars()) if emails else set()
        return Plan(kind, {"people": len(emails - known), "existing": len(emails & known),
                           "cards": len(cards), **extra},
                    [c["fn"] or c["emails"][0]["email"] for c in cards[:SAMPLE]], cards)
    raise ApiError(400, "import.bad_kind", kinds=", ".join(KINDS))


async def _ics_target(db: AsyncSession, user: User, p: Any, calendar: str | None) -> dict[str, Any]:
    from mailr.calendar import writer
    from mailr.calendar.routes import _need_calendar
    from mailr.calendar.routes_collections import _visible

    target = calendar
    if target and target.isdigit():
        try:
            coll_, _ = await _visible(db, user.id, p, int(target))
        except LookupError:
            raise ApiError(404, "calendar.calendar_not_found") from None
        target = coll_.href
    try:
        coll = await writer.target_collection(db, user.id, p, target, "event")
    except Exception as e:  # noqa: BLE001
        raise _need_calendar(e) from None
    if coll.read_only:
        raise ApiError(400, "import.read_only_calendar")
    return {"href": coll.href, "id": coll.id, "name": calendar_name(coll.displayname, coll.href)}


async def run(db: AsyncSession, user: User, p: Any, pl: Plan, source: str) -> ImportBatch:
    """Write what the plan read, and record the batch. The caller commits."""
    batch = ImportBatch(user_id=user.id, kind=pl.kind, source=source[:300], counts={}, undo={})
    db.add(batch)
    await db.flush()
    if pl.kind == "keep":
        counts, undo = await _run_keep(db, user, pl.data, batch.id)
    elif pl.kind == "ics":
        counts, undo = await _run_ics(db, user, p, pl)
    else:
        counts, undo = await _run_vcf(db, user, pl.data, batch.id)
    batch.counts = {**counts, **{k: v for k, v in pl.counts.items() if k not in counts}}
    batch.undo = undo
    return batch


async def _run_keep(db: AsyncSession, user: User, notes: list[KeepNote], batch_id: int) -> tuple[dict, dict]:
    from mailr.notes import core as ncore
    from mailr.notes.actions import create_note, labels_of
    from mailr.notes.models import Note, NoteViewerState

    before = {lb.name.lower() for lb in await labels_of(db, user)}
    made: list[str] = []
    shortened = 0
    for n in notes:
        body = n.text
        if len(body) > ncore.LIMITS["body"]:
            body, shortened = body[:ncore.LIMITS["body"]], shortened + 1
        items = n.items[:ncore.LIMITS["items"]]
        items = [{"text": i["text"][:ncore.LIMITS["item"]], "checked": i["checked"]} for i in items]
        out = await create_note(db, user, kind=n.kind, title=n.title[:ncore.LIMITS["title"]], body=body,
                                items=items or None, color=n.color, labels=n.labels, pinned=n.pinned,
                                source="import")
        pid = out["note"]["id"]
        made.append(pid)
        if n.archived:
            note = (await db.execute(select(Note).where(Note.pid == pid))).scalar_one()
            st = (await db.execute(select(NoteViewerState).where(
                NoteViewerState.user_id == user.id, NoteViewerState.item_kind == "note",
                NoteViewerState.item_id == note.id))).scalar_one_or_none()
            if st is not None:
                st.archived = True
    after = [lb.name for lb in await labels_of(db, user)]
    new_labels = [n for n in after if n.lower() not in before]
    await ncore.notify_user(db, user.id, "notes")
    return ({"notes": sum(1 for n in notes if n.kind == "text"),
             "checklists": sum(1 for n in notes if n.kind == "checklist"), "new_labels": len(new_labels),
             "shortened": shortened}, {"notes": made, "labels": new_labels})


async def _run_ics(db: AsyncSession, user: User, p: Any, pl: Plan) -> tuple[dict, dict]:
    # ACT event.import_ics: the same writes as POST /calendar/import, recorded for Undo.
    from mailr.calendar import expand as cexpand
    from mailr.calendar import writer
    from mailr.models import CalCollection

    coll = await db.get(CalCollection, pl.target["id"])
    made, skipped, failed = [], 0, 0
    for uid, cal_ in pl.data:
        if cexpand.rule_problem(cal_):
            failed += 1
            continue
        sp = await db.begin_nested()
        try:
            await writer.create(db, user.id, coll, cal_, uid)
            await sp.commit()
            made.append(uid)
        except writer.Conflict:
            await sp.rollback()
            skipped += 1
        except Exception:  # noqa: BLE001
            await sp.rollback()
            failed += 1
    return ({"events": len(made), "existing": skipped, "failed": failed},
            {"uids": made, "calendar": pl.target})


async def _run_vcf(db: AsyncSession, user: User, cards: list[dict[str, Any]], batch_id: int) -> tuple[dict, dict]:
    from mailr.core.events import notify
    from mailr.core.people import org_from_email

    prov = {"source": "import", "import_id": batch_id}
    made: list[str] = []
    filled: dict[str, list[str]] = {}
    seen: set[str] = set()
    for c in cards:
        email = c["emails"][0]["email"]
        if email in seen:
            continue
        seen.add(email)
        p = (await db.execute(select(Person).where(Person.user_id == user.id, Person.email == email))).scalar_one_or_none()
        values = {"name": c.get("fn") or "", "organization": c.get("organization") or "", "title": c.get("title") or "",
                  "birthday": c.get("birthday") or ""}
        phones = [{"value": x["value"], "type": x.get("type", ""), **prov} for x in c.get("phones") or []]
        addrs = [{**{k: x.get(k, "") for k in ("text", "street", "city", "region", "postcode", "country", "type")}, **prov}
                 for x in c.get("addresses") or []]
        if p is None:
            p = Person(user_id=user.id, email=email, name=values["name"][:300],
                       organization=values["organization"][:300] or org_from_email(email), received_count=0,
                       sent_count=0, notes="", blocked=False, strength=0.0, phones=phones, addresses=addrs,
                       title=values["title"][:300], birthday=values["birthday"][:10],
                       provenance={f: prov for f in ("name", "organization", "title", "birthday") if values[f]})
            db.add(p)
            made.append(email)
            continue
        changed: list[str] = []
        pv = dict(p.provenance or {})
        for f in ("name", "organization", "title", "birthday"):
            cur = getattr(p, f) or ""
            empty = not cur or (f == "organization" and cur == org_from_email(email)) or \
                (f == "name" and cur == email.split("@")[0])
            if values[f] and empty:
                setattr(p, f, values[f][:300] if f != "birthday" else values[f][:10])
                pv[f] = prov
                changed.append(f)
        if not p.phones and phones:
            p.phones = phones
            changed.append("phones")
        if not p.addresses and addrs:
            p.addresses = addrs
            changed.append("addresses")
        if changed:
            p.provenance = pv
            filled[email] = changed
    await db.flush()
    await notify(db, user.id, {"type": "changes", "entity": "people"})
    return {"people": len(made), "updated": len(filled)}, {"people": made, "filled": filled}


async def undo(db: AsyncSession, user: User, p: Any, batch: ImportBatch) -> dict[str, int]:
    """Take an import back. The caller commits."""
    if batch.undone_at is not None:
        raise ApiError(409, "import.already_undone")
    u = batch.undo or {}
    out: dict[str, int] = {}
    if batch.kind == "keep":
        from mailr.notes import access as naccess
        from mailr.notes import actions as nactions
        from mailr.notes.models import Note, NoteLabel, NoteViewerState

        notes = (await db.execute(select(Note).where(Note.pid.in_(u.get("notes") or [])))).scalars().all()
        n = 0
        for note in notes:
            if naccess.can_delete(user, note) and note.deleted_at is None:
                await nactions.trash(db, user, note)
                n += 1
        out["notes"] = n
        removed = 0
        for name in u.get("labels") or []:
            used = (await db.execute(select(NoteViewerState.item_id).join(Note, Note.id == NoteViewerState.item_id).where(
                NoteViewerState.user_id == user.id, NoteViewerState.item_kind == "note",
                NoteViewerState.labels.any(name), Note.deleted_at.is_(None)).limit(1))).first()
            if used:
                continue
            lb = (await db.execute(select(NoteLabel).where(NoteLabel.user_id == user.id, NoteLabel.name == name))
                  ).scalar_one_or_none()
            if lb is not None:
                await db.delete(lb)
                removed += 1
        out["labels"] = removed
        from mailr.notes import core as ncore

        await ncore.notify_user(db, user.id, "labels")
    elif batch.kind == "ics":
        from mailr.calendar import mirror, writer

        n = 0
        for uid in u.get("uids") or []:
            obj = await mirror.find_by_uid(db, user.id, uid)
            if obj is None or obj.deleted_at is not None:
                continue
            try:
                await writer.delete(db, user.id, obj)
                n += 1
            except Exception:  # noqa: BLE001 - an event changed or gone since: left as it is
                continue
        out["events"] = n
    else:
        from mailr.core.events import notify
        from mailr.core.people import org_from_email

        mine = {"source": "import", "import_id": batch.id}
        people = (await db.execute(select(Person).where(
            Person.user_id == user.id, Person.email.in_(list(u.get("people") or []) + list((u.get("filled") or {}).keys())))
        )).scalars().all()
        removed = cleared = 0
        for person in people:
            if person.email in (u.get("people") or []):
                if (person.received_count or 0) == 0 and (person.sent_count or 0) == 0:
                    await db.delete(person)
                    removed += 1
                    continue
                fields = ["name", "organization", "title", "birthday", "phones", "addresses"]
            else:
                fields = (u.get("filled") or {}).get(person.email) or []
            pv = dict(person.provenance or {})
            for f in fields:
                if f in ("phones", "addresses"):
                    setattr(person, f, [x for x in getattr(person, f) or [] if x.get("import_id") != batch.id])
                elif pv.get(f) == mine:
                    pv.pop(f, None)
                    setattr(person, f, org_from_email(person.email) if f == "organization" else "")
            person.provenance = pv
            cleared += 1
        out["people"] = removed
        out["updated"] = cleared
        await notify(db, user.id, {"type": "changes", "entity": "people"})
    batch.undone_at = datetime.now(UTC)
    return out


def batch_out(b: ImportBatch) -> dict[str, Any]:
    return {"id": b.id, "kind": b.kind, "source": b.source, "counts": b.counts or {},
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "undone_at": b.undone_at.isoformat() if b.undone_at else None,
            "calendar": (b.undo or {}).get("calendar")}
