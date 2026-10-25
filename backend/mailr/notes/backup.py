"""Daily plain-file export of notes, lists and tables.

Postgres is the store of record for notes and tables. This export gives
them the same safety net as mail (core/backup.py) and calendars (calendar/backup.py): plain files
that any program opens, written every day without anyone asking, and copied to the backup target.

Per user, the items they own: their own notes and tables, and the group-owned ones they created
(notes/localonly.owner_id), so an item shared with several people is exported once. Trashed items
are left out; Trash keeps them for 30 days anyway.

Layout (`<data_dir>/exports/notes/`, and `<MAILR_BACKUP_TARGET>/notes/` when set):

    user-<id>/<snapshot>/manifest.json     counts, file list, format
    user-<id>/<snapshot>/notes/<title>.md  one per note or list (notes/exporting.markdown)
    user-<id>/<snapshot>/notes.json        every note in full (items, sections, labels, links)
    user-<id>/<snapshot>/tables/<name>.csv one per table, every row and column
    user-<id>/<snapshot>/tables.xlsx       every table, one sheet each, typed cells
    user-<id>/<snapshot>/tables.json       every table in full (columns, views, rows with raw cells)
    user-<id>/LATEST                       the newest snapshot's name
    user-<id>/.last_run                    when a run last completed (core/health.py reads it)

Snapshots, retention (CALENDAR_EXPORT_KEEP_DAYS) and the copy to the target follow
calendar/backup.py and reuse its helpers. A run whose content equals the newest snapshot writes no
new one (the xlsx file is left out of the comparison: it is made from the same rows). local_only
items are exported like the others, as local-only mail is: the export stays on the server and the
admin's backup target and never reaches a model.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select, text

from mailr.calendar import backup as cal
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
from mailr.models import User

log = logging.getLogger("mailr.notes.backup")

FORMAT = "mailr-notes-export/1"
MANIFEST = cal.MANIFEST
NOT_IN_DIGEST = ("tables.xlsx",)


def root() -> Path:
    return Path(get_settings().data_dir) / "exports" / "notes"


def export_dir(user_id: int) -> Path:
    return root() / f"user-{user_id}"


def target_dir(user_id: int) -> Path | None:
    t = get_settings().backup_target
    return Path(t) / "notes" / f"user-{user_id}" if t else None


def _ready_target_dir(user_id: int) -> Path | None:
    tgt = target_dir(user_id)
    if tgt is None or target_problem() is not None:
        return None
    try:
        private.mkdirs(tgt)
    except OSError as e:
        log.warning("backup target for the notes of user %s: %s", user_id, e)
        return None
    return tgt


def _name(base: str, ext: str, used: set[str]) -> str:
    stem = re.sub(r"[^\w\- ]+", "", base or "", flags=re.UNICODE).strip()[:60] or "Untitled"
    name, n = f"{stem}{ext}", 2
    while name.lower() in used:
        name, n = f"{stem} ({n}){ext}", n + 1
    used.add(name.lower())
    return name


# ---------------- what ----------------


async def collect(user_id: int) -> tuple[dict[str, Any], dict[str, str | bytes]] | None:
    """(manifest, files) of one user's notes and tables, or None for an unknown user."""
    from mailr.notes import core as ncore
    from mailr.notes import exporting
    from mailr.notes.models import Note
    from mailr.tables import core as tcore
    from mailr.tables import csvio, query, xlsxio
    from mailr.tables.models import DataTable

    def owned(model: Any) -> Any:
        return or_(model.owner_user_id == user_id,
                   (model.owner_user_id.is_(None)) & (model.created_by == user_id))

    files: dict[str, str | bytes] = {}
    async with session_scope() as db:
        user = await db.get(User, user_id)
        if user is None:
            return None
        notes = list((await db.execute(select(Note).where(owned(Note), Note.deleted_at.is_(None))
                                       .order_by(Note.created_at, Note.id))).scalars())
        full_notes, used = [], set()
        for n in notes:
            data = await ncore.note_full(db, user, n)
            full_notes.append(data)
            files[f"notes/{exporting.filename(n, used)}"] = exporting.markdown(data)
        tables = list((await db.execute(select(DataTable).where(owned(DataTable), DataTable.deleted_at.is_(None))
                                        .order_by(DataTable.created_at, DataTable.id))).scalars())
        full_tables, sheets, used = [], [], set()
        nm: dict[int, str] = {}
        for t in tables:
            res = await query.run(db, user, t, spec={"filters": [], "sort": [], "hidden": []})
            titles = {k: v.get("title", "") for k, v in res.titles.items()}
            files[f"tables/{_name(t.name or 'Table', '.csv', used)}"] = csvio.export(res.ordered, res.rows, titles)
            sheets.append((t.name or "Table", res.ordered, res.rows, titles))
            data = await tcore.table_full(db, user, t)
            data["rows"] = [tcore.row_out(r, nm, None) for r in res.rows]
            full_tables.append(data)
        if sheets:  # made here: the rows are expired once the session ends
            files["tables.xlsx"] = await asyncio.to_thread(xlsxio.workbook, sheets)
        counts = {"notes": sum(1 for n in notes if n.kind != "checklist"),
                  "lists": sum(1 for n in notes if n.kind == "checklist"), "tables": len(tables)}
        await db.rollback()  # reading only (note_full may stamp a viewer state)
    files["notes.json"] = json.dumps({"format": FORMAT, "notes": full_notes}, indent=1, ensure_ascii=False,
                                     default=str)
    files["tables.json"] = json.dumps({"format": FORMAT, "tables": full_tables}, indent=1, ensure_ascii=False,
                                      default=str)
    manifest = {"format": FORMAT, "user_id": user_id,
                "counts": counts,
                "files": sorted(files)}
    return manifest, files


# ---------------- where it goes ----------------


def digest(manifest: dict[str, Any], files: dict[str, str | bytes]) -> str:
    h = hashlib.sha256()
    meta = {k: v for k, v in manifest.items() if k not in ("exported_at", "digest", "snapshot")}
    h.update(json.dumps(meta, sort_keys=True, default=str).encode())
    for name in sorted(files):
        if name in NOT_IN_DIGEST:
            continue
        v = files[name]
        h.update(b"\0" + name.encode() + b"\0" + (v if isinstance(v, bytes) else v.encode("utf-8", "replace")))
    return h.hexdigest()


def _write_tree(dest: Path, manifest: dict[str, Any], files: dict[str, str | bytes]) -> None:
    """Write a snapshot into a temporary folder beside `dest`, fsync it and rename it into place."""
    tmp = dest.parent / f".tmp-{dest.name}-{secrets.token_hex(4)}"
    try:
        private.mkdirs(tmp)
        dirs = {tmp}
        for rel, v in files.items():
            p = tmp / rel
            private.mkdirs(p.parent)
            dirs.add(p.parent)
            if isinstance(v, bytes):
                private.write_bytes(p, v)
            else:
                private.write_text(p, v, encoding="utf-8", newline="")
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


def store_snapshot(base: Path, manifest: dict[str, Any], files: dict[str, str | bytes]) -> str | None:
    """A new snapshot unless the newest one holds the same content. Its name, or None."""
    private.mkdirs(base)
    d = digest(manifest, files)
    cur = cal.latest(base)
    if cur is not None and (cal.read_manifest(base / cur) or {}).get("digest") == d:
        return None
    now = datetime.now(UTC)
    name = now.strftime("%Y-%m-%dT%H%M%SZ")
    newest = (cal.snapshots(base) or [""])[-1]
    while (base / name).exists() or name <= newest:
        now += timedelta(seconds=1)
        name = now.strftime("%Y-%m-%dT%H%M%SZ")
    _write_tree(base / name, {**manifest, "snapshot": name, "exported_at": now.isoformat(), "digest": d}, files)
    _write_atomic(base / cal.LATEST, name)
    cal.prune(base)
    return name


def _finish(user_id: int, manifest: dict[str, Any], files: dict[str, str | bytes]) -> dict[str, Any]:
    base = export_dir(user_id)
    out: dict[str, Any] = {"user_id": user_id, "snapshot": None, "copied": None, "counts": manifest["counts"]}
    # Nothing to keep and no earlier snapshot: no folder. With earlier snapshots an empty one is
    # written, so LATEST never shows notes that are gone.
    if any(manifest["counts"].values()) or cal.latest(base) is not None:
        out["snapshot"] = store_snapshot(base, manifest, files)
    tgt = None
    if target_dir(user_id) is not None:
        tgt = _ready_target_dir(user_id)
        if tgt is None:
            log.warning("backup target for the notes of user %s: %s", user_id, target_problem())
        elif cal.latest(base) is not None:
            out["copied"] = cal.copy_to_target(base, tgt)
    now = datetime.now(UTC).isoformat()
    private.mkdirs(base)
    _write_atomic(base / LAST_RUN, now)
    if tgt is not None:
        _write_atomic(tgt / LAST_RUN, now)
    return out


async def export_user(user_id: int) -> dict[str, Any]:
    """One user's notes and tables export. Returns what was written."""
    got = await collect(user_id)
    if got is None:
        return {"user_id": user_id, "snapshot": None}
    manifest, files = got
    return await asyncio.to_thread(_finish, user_id, manifest, files)


# ---------------- the readiness check ----------------


async def owners_with_data() -> list[int]:
    """Active users who own a note or a table (as export_user counts ownership)."""
    async with session_scope() as db:
        return list((await db.execute(text(
            "SELECT u.id FROM users u WHERE NOT u.disabled AND (EXISTS (SELECT 1 FROM notes n WHERE "
            "n.deleted_at IS NULL AND (n.owner_user_id = u.id OR (n.owner_user_id IS NULL AND n.created_by = u.id))) "
            "OR EXISTS (SELECT 1 FROM tables t WHERE t.deleted_at IS NULL AND (t.owner_user_id = u.id OR "
            "(t.owner_user_id IS NULL AND t.created_by = u.id)))) ORDER BY u.id"))).scalars())


def runs(owners: list[int], on_target: bool) -> list[datetime | None]:
    return [last_run(target_dir(o) if on_target else export_dir(o)) for o in owners]


async def data_since(owners: list[int]) -> list[datetime | None]:
    """When each owner's first note or table appeared (ownership as owners_with_data): the
    readiness check gives an owner that has never been exported until then plus EXPORT_STALE for
    its first export. None when unknown."""
    if not owners:
        return []
    async with session_scope() as db:
        rows = dict((await db.execute(text(
            "SELECT u.id, LEAST((SELECT min(n.created_at) FROM notes n WHERE n.deleted_at IS NULL AND "
            "(n.owner_user_id = u.id OR (n.owner_user_id IS NULL AND n.created_by = u.id))), "
            "(SELECT min(t.created_at) FROM tables t WHERE t.deleted_at IS NULL AND (t.owner_user_id = u.id "
            "OR (t.owner_user_id IS NULL AND t.created_by = u.id)))) FROM users u WHERE u.id = ANY(:ids)"),
            {"ids": owners})).all())
    return [rows.get(o) for o in owners]
