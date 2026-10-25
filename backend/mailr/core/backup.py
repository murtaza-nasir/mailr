"""Per-user Maildir export: a copy of every message in a standard format that does not
depend on mailr or Stalwart, and the backup check that gates upstream deletion.

Layout: `<data_dir>/exports/user-<id>/<label>/cur/<ts>.<id>.mailr:2,<flags>` plus
- `.exported`: manifest lines `blob_id<TAB>relative path` (what retention trusts),
- `.cursor`: `<message id><TAB><blob id>` of the last row a run went past (incremental; each
  run reads only newer rows). A run that finds no such row in the database (a database restored
  from a dump older than the export) starts again from the first row (`_read_cursor`); files
  already in the manifest are not written again.
- `.last_run`: when a run last caught up with the store (ISO time; also written to the backup
  target). The readiness check (core/health.py) reads it.
- `.purged`: lines `blob_id<TAB>relative path` of files removed after Delete forever
  (`remove_exported`) that the backup target still has; removed there when it is usable.
- `.forgotten`: lines `message id<TAB>unix time` of messages deleted forever in the last
  FORGOTTEN_SECONDS: an export run that read such a row before the deletion leaves its file out.
- `.lock`: held (flock) while the manifest is appended to, rewritten or copied whole.

Files leave the export only through Delete forever (`remove_exported`) and the removal of a
user (`remove_user_export`). Mail that vanished from the mail store keeps its file: after the
store was restored from an older snapshot, it is the only copy left (core/purge.py).

Every file starts with `X-Mailr-Labels:` (all labels) and `X-Keywords:` (the convention
Thunderbird and Dovecot understand) followed by the original bytes. Files are fsynced before
they enter the manifest.

With `MAILR_BACKUP_TARGET` set to a mounted path (e.g. a NAS share), new files are also
copied there; a message counts as backed up only when that copy exists. The target counts only
while it holds the marker file `.mailr-backup-target`, which the operator creates on the share
itself: an unmounted share leaves an empty mount point on the local disk (or Docker creates
one), and copies there would be no backup at all (Oct 2026). mailr never creates the
marker or the target directory. Files exported before
the target was set (or while it was unreachable) are copied by `seed_target`, which each export
run starts with while the target's manifest is shorter than the local one.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import json
import logging
import os
import random
import shutil
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from mailr.config import get_settings
from mailr.core import private
from mailr.db import session_scope
from mailr.models import Message, User

log = logging.getLogger("mailr.backup")
BATCH = 500
PREFIXES = (b"X-Mailr-Labels:", b"X-Keywords:")


def store_mode_available() -> bool:
    """Store mode can be chosen: the mail export is on, or the export check before deletion at the
    provider is off (MAILR_STORE_BACKUP_CHECK=false)."""
    s = get_settings()
    return s.mail_export or not s.store_backup_check


def export_root(user_id: int) -> Path:
    return Path(get_settings().data_dir) / "exports" / f"user-{user_id}"


TARGET_MARKER = ".mailr-backup-target"


def target_problem_msg(base: Path | str | None = None) -> dict | None:
    """None when the configured backup target is usable, else what is wrong as an i18n msg
    (health.ready.*). Usable means the directory exists and holds TARGET_MARKER (the share is mounted)."""
    from mailr import i18n

    t = base if base is not None else get_settings().backup_target
    if not t:
        return i18n.msg("health.ready.target_unconfigured")
    p = Path(t)
    if not p.is_dir():
        return i18n.msg("health.ready.target_missing")
    if not (p / TARGET_MARKER).is_file():
        return i18n.msg("health.ready.target_unmounted", marker=TARGET_MARKER)
    return None


def target_problem(base: Path | str | None = None) -> str | None:
    """target_problem_msg in English (logs, the readiness endpoint)."""
    from mailr import i18n

    m = target_problem_msg(base)
    return None if m is None else i18n.render(m)


_target_warned = 0.0


def _warn_target(problem: str) -> None:
    """A configured target that cannot be used is logged, at most once an hour: copies
    stop, and Admin → Diagnostics shows the backup_target check failing."""
    import time

    global _target_warned
    now = time.monotonic()
    if not _target_warned or now - _target_warned >= 3600:
        _target_warned = now
        log.warning("MAILR_BACKUP_TARGET is set but %s; mail is not copied there until it is fixed "
                    "(logged at most once an hour)", problem)


def target_root(user_id: int) -> Path | None:
    """The user's folder on the backup target, or None when no target is configured."""
    t = get_settings().backup_target
    return Path(t) / f"user-{user_id}" if t else None


def _ready_target(user_id: int) -> Path | None:
    """The user's folder on the target when the target is usable (created inside it if needed;
    the target itself is never created)."""
    tgt = target_root(user_id)
    if tgt is None:
        return None
    if (problem := target_problem()) is not None:
        _warn_target(problem)
        return None
    try:
        private.mkdirs(tgt, parents=False)
    except OSError as e:
        log.warning("backup target for user %s: %s", user_id, e)
        return None
    return tgt


LAST_RUN = ".last_run"


def last_run(root: Path | None) -> datetime | None:
    """When the export at `root` last caught up, or None (never, or unreadable)."""
    if root is None:
        return None
    try:
        return datetime.fromisoformat((root / LAST_RUN).read_text().strip())
    except (OSError, ValueError):
        return None


def _write_atomic(p: Path, text: str) -> None:
    """Replace a small marker file in one step. A plain write_text empties the file first, and a
    reader in that moment (the readiness check reading .last_run on the NAS) saw no run at all.

    The temporary file has a unique random name made with O_CREAT|O_EXCL (mkstemp), so the app
    and the worker (which can share a PID in their containers) never write the same file and a
    symlink planted at the name is never followed. It is removed when anything fails. mkstemp makes
    it 0600, as every backup file (core/private.py)."""
    import tempfile

    fd, name = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(name, p)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def mark_run(user_id: int) -> None:
    """Record a run that caught up: locally, then on the target. A target that cannot be
    written is only logged here; the readiness check reports it."""
    now = datetime.now(UTC).isoformat()
    _write_atomic(export_root(user_id) / LAST_RUN, now)
    if target_root(user_id) is None:
        return
    tgt = _ready_target(user_id)
    if tgt is None:  # _ready_target logged why (at most once an hour)
        return
    try:
        _write_atomic(tgt / LAST_RUN, now)
    except OSError as e:
        log.warning("backup target for user %s: cannot record the run: %s", user_id, e)


def _fsync_file(p: Path) -> None:
    fd = os.open(p, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(p: Path) -> None:
    try:
        fd = os.open(p, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _safe(name: str) -> str:
    """A label name as one folder name inside the export: no path separators, and never
    "." / ".." or a leading dot (which would leave the export folder or hide it)."""
    s = "".join(ch if ch.isalnum() or ch in " -_." else "_" for ch in name.replace("/", ".").replace("\\", "."))
    s = s.strip().lstrip(".").strip()
    return s if s.strip("._ ") else "Archive"


def exported_manifest(user_id: int) -> dict[str, str]:
    return exported_manifest_from(export_root(user_id) / ".exported")


def exported_manifest_from(f: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not f.exists():
        return out
    for line in f.read_text().splitlines():
        blob, _, rel = line.partition("\t")
        # Lines of the first manifest format (blob id only, before 29 Sep 2026) name no file,
        # so they prove nothing: such a message counts as exported only once a line with its
        # path exists.
        if blob and rel.strip():
            out[blob] = rel
    return out


def strip_prefix(data: bytes) -> bytes:
    """The original message bytes of an exported file."""
    while data.startswith(PREFIXES):
        nl = data.find(b"\n")
        if nl < 0:
            break
        data = data[nl + 1:]
    return data


# The blob ids of each manifest, read once and then from where the last read stopped: the
# manifest only grows by appends between rewrites (a rewrite makes a new file, a new inode). Each
# export batch used to read the whole file, which for 500,000 messages is tens of megabytes a batch.
_DONE: dict[Path, tuple[int, int, set[str]]] = {}  # path: (inode, bytes read, blob ids)


def _exported_blobs(f: Path) -> set[str]:
    """The blob ids `f` (a `.exported` manifest) lists with their file, as exported_manifest reads them."""
    try:
        st = f.stat()
    except OSError:
        _DONE.pop(f, None)
        return set()
    ino, pos, blobs = _DONE.get(f, (st.st_ino, 0, set()))
    if ino != st.st_ino or st.st_size < pos:
        pos, blobs = 0, set()
    if st.st_size > pos:
        with f.open("rb") as fh:
            fh.seek(pos)
            chunk = fh.read(st.st_size - pos)
        end = chunk.rfind(b"\n") + 1  # a line still being written waits for the next read
        for line in chunk[:end].decode("utf-8", "replace").splitlines():
            blob, _, rel = line.partition("\t")
            if blob and rel.strip():
                blobs.add(blob)
        pos += end
    _DONE[f] = (st.st_ino, pos, blobs)
    return blobs


def _manifest_lines(f: Path) -> int:
    try:
        with f.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


class ExportBusy(Exception):
    """Another process holds the export's lock (a long copy to the backup target)."""


@contextlib.contextmanager
def export_lock(base: Path, wait: float | None = None) -> Iterator[None]:
    """The export's lock (`.lock`, flock), across the app and worker processes: the manifest is
    appended to, rewritten and copied whole under it, so no line is lost to a rewrite. `wait`:
    seconds to try before ExportBusy; None waits as long as it takes."""
    private.mkdirs(base)
    with private.open_new(base / ".lock", "a") as f:
        start = time.monotonic()
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | (fcntl.LOCK_NB if wait is not None else 0))
                break
            except BlockingIOError:
                if time.monotonic() - start >= (wait or 0):
                    raise ExportBusy(str(base)) from None
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def seed_target(user_id: int) -> int:
    """Copy exported files that the backup target lacks, then its manifest. Returns how many
    were copied. A no-op without a target, or when the target's manifest is complete."""
    if target_root(user_id) is None:
        return 0
    tgt = _ready_target(user_id)
    base = export_root(user_id)
    if tgt is None or _same_size(tgt / ".exported", base / ".exported") \
            or _manifest_lines(tgt / ".exported") >= _manifest_lines(base / ".exported"):
        return 0
    copied = 0
    with export_lock(base):
        for rel in exported_manifest(user_id).values():
            src, dest = base / rel, tgt / rel
            if dest.is_file() or not src.is_file():
                continue
            private.mkdirs(dest.parent)
            shutil.copy2(src, dest)
            _fsync_file(dest)
            copied += 1
        shutil.copy2(base / ".exported", tgt / ".exported")
        _fsync_file(tgt / ".exported")
    log.info("backup target for user %s: %s files copied", user_id, copied)
    return copied


PURGED = ".purged"


def _message_file(root: Path, rel: str) -> Path | None:
    """root/rel when `rel` has the shape of an exported message (`<folder>/cur/<file>`, nothing
    that leaves the folder), else None: nothing else is ever removed."""
    parts = rel.split("/")
    if len(parts) != 3 or parts[1] != "cur" or any(x in ("", ".", "..") or "\\" in x for x in parts):
        return None
    return root / rel


FORGOTTEN = ".forgotten"
FORGOTTEN_SECONDS = 2 * 86400  # longer than any export run (its job times out after hours)


def _forgotten(base: Path) -> dict[int, float]:
    """{message id: when it was deleted forever} of `.forgotten`, recent lines only."""
    out: dict[int, float] = {}
    try:
        lines = (base / FORGOTTEN).read_text().splitlines()
    except OSError:
        return out
    cutoff = time.time() - FORGOTTEN_SECONDS
    for line in lines:
        mid, _, at = line.partition("\t")
        try:
            if float(at) >= cutoff:
                out[int(mid)] = float(at)
        except ValueError:
            continue
    return out


def _rel_message_id(rel: str) -> int | None:
    """The message id in an exported file's name (`<folder>/cur/<ts>.<id>.mailr:2,<flags>`)."""
    parts = rel.rsplit("/", 1)[-1].split(".")
    return int(parts[1]) if len(parts) > 2 and parts[1].isdigit() else None


def remove_exported(user_id: int, blobs: set[str], wait: float = 30,
                    message_ids: list[int] | None = None) -> dict[str, int]:
    """Delete forever: remove these blobs' files from the export and their manifest lines (the
    manifest is rewritten in one step), then from the backup target (`apply_target_removals`).
    Only the file the manifest lists for a blob is removed; a missing file is fine. The deleted
    `message_ids` are noted in `.forgotten` for an export run that is writing them right now.
    Returns {removed, target_removed, target_pending}."""
    base = export_root(user_id)
    out = {"removed": 0, "target_removed": 0, "target_pending": 0}
    manifest = base / ".exported"
    if message_ids and base.is_dir():
        with export_lock(base, wait):
            now = time.time()
            noted = {**_forgotten(base), **dict.fromkeys(message_ids, now)}
            _write_atomic(base / FORGOTTEN, "".join(f"{i}\t{at:.0f}\n" for i, at in sorted(noted.items())))
    if blobs and manifest.exists():
        with export_lock(base, wait):
            keep: list[str] = []
            gone: list[tuple[str, str]] = []
            for line in manifest.read_text().splitlines():
                blob, _, rel = line.partition("\t")
                if blob in blobs:
                    if rel.strip():
                        gone.append((blob, rel.strip()))
                    continue
                keep.append(line + "\n")
            if gone:
                # The target's list first: a stop after the rewrite still removes its copies later.
                if target_root(user_id) is not None:
                    with private.open_new(base / PURGED, "a") as f:
                        f.write("".join(f"{b}\t{rel}\n" for b, rel in gone))
                        f.flush()
                        os.fsync(f.fileno())
                _write_atomic(manifest, "".join(keep))
                for _b, rel in gone:
                    p = _message_file(base, rel)
                    if p is not None and p.is_file():
                        p.unlink()
                        out["removed"] += 1
                for d in {(base / rel).parent for _b, rel in gone}:
                    _fsync_dir(d)
    out.update(apply_target_removals(user_id, wait))
    return out


def apply_target_removals(user_id: int, wait: float = 30) -> dict[str, int]:
    """Remove from the backup target the files listed in `.purged`, then copy the manifest there.
    While the target cannot be used the list waits for the next run (each export run and the
    daily purge sweep try again); without a target it is dropped."""
    base = export_root(user_id)
    pending = base / PURGED
    if not pending.exists():
        return {}
    if target_root(user_id) is None:
        pending.unlink(missing_ok=True)
        return {}
    lines = [ln for ln in pending.read_text().splitlines() if "\t" in ln]
    tgt = _ready_target(user_id)
    if tgt is None:
        return {"target_pending": len(lines)}
    removed = 0
    with export_lock(base, wait):
        lines = [ln for ln in pending.read_text().splitlines() if "\t" in ln]
        for line in lines:
            p = _message_file(tgt, line.partition("\t")[2].strip())
            if p is not None and p.is_file():
                p.unlink()
                removed += 1
        if (base / ".exported").exists():
            shutil.copy2(base / ".exported", tgt / ".exported")
            _fsync_file(tgt / ".exported")
        pending.unlink(missing_ok=True)
    if removed:
        log.info("backup target for user %s: %s deleted messages removed", user_id, removed)
    return {"target_removed": removed}


def remove_user_export(user_id: int) -> bool:
    """A deleted user's exports, here and on the backup target (the folders `user-<id>` of the
    mail, notes and calendar exports). False when the target is set but cannot be reached now:
    its copies wait for the next try."""
    from mailr.calendar import backup as calbackup
    from mailr.notes import backup as notesbackup

    for base in (export_root(user_id), notesbackup.export_dir(user_id), calbackup.export_dir(user_id)):
        if base.is_dir():
            shutil.rmtree(base, ignore_errors=True)
    targets = [t for t in (target_root(user_id), notesbackup.target_dir(user_id), calbackup.target_dir(user_id))
               if t is not None]
    if targets and target_problem() is not None:
        return False
    for tgt in targets:
        if tgt.is_dir():
            shutil.rmtree(tgt, ignore_errors=True)
    return True


def _write_message(tmp: Path, data: bytes, dest: Path, tdest: Path | None) -> None:
    """One exported message: written to tmp/, fsynced, moved into cur/, copied to the target."""
    private.write_bytes(tmp, data)
    _fsync_file(tmp)
    tmp.replace(dest)
    if tdest is not None:
        private.mkdirs(tdest.parent)
        shutil.copy2(dest, tdest)
        _fsync_file(tdest)


def _finish_batch(base: Path, written: list[tuple[str, str]], tgt: Path | None) -> None:
    """Directory fsyncs and the manifest of a batch (in a worker thread). A message deleted forever
    while the batch was written (`.forgotten`) leaves its file out, here and on the target."""
    for d in {base / Path(rel).parent for _, rel in written}:
        _fsync_dir(d)
    with export_lock(base):
        gone = _forgotten(base)
        if gone:
            kept = []
            for b, rel in written:
                if _rel_message_id(rel) in gone:
                    for root in (base, tgt):
                        p = _message_file(root, rel) if root is not None else None
                        if p is not None:
                            p.unlink(missing_ok=True)
                else:
                    kept.append((b, rel))
            written[:] = kept
        lines = "".join(f"{b}\t{rel}\n" for b, rel in written)
        # The target's manifest gets the same lines when it matched before them; otherwise
        # (a new target, one that missed a batch) it is copied whole.
        in_step = tgt is not None and _same_size(tgt / ".exported", base / ".exported")
        with private.open_new(base / ".exported", "a") as f:
            f.write(lines)
            f.flush()
            os.fsync(f.fileno())
        if tgt is not None:
            if in_step:
                with private.open_new(tgt / ".exported", "a") as f:
                    f.write(lines)
                    f.flush()
                    os.fsync(f.fileno())
            else:
                shutil.copy2(base / ".exported", tgt / ".exported")


def _same_size(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_size == b.stat().st_size
    except OSError:
        return not a.exists() and not b.exists()


def _read_cursor(f: Path) -> tuple[int, str | None]:
    """(message id, blob id) of `.cursor`; (0, None) when there is none. A cursor written before
    the blob id was kept has only the id."""
    try:
        cid, _, blob = f.read_text().strip().partition("\t")
        return int(cid or 0), blob.strip() or None
    except (OSError, ValueError):
        return 0, None


async def _checked_cursor(db, user_id: int, base: Path) -> int:
    """The cursor, or 0 when the database does not hold the row it points at: the database was
    restored from an older dump, and its ids are being handed out again from where the dump left
    them, below the cursor. Without the rewind those messages were never exported."""
    cursor, blob = _read_cursor(base / ".cursor")
    if not cursor:
        return 0
    row = (await db.execute(select(Message.blob_id).where(Message.user_id == user_id, Message.id == cursor))).first()
    if row is not None and (blob is None or row.blob_id == blob):
        return cursor
    log.warning("export user %s: the database does not match the export's cursor (message %s); it was restored "
                "from an older dump. Checking every message against the export again", user_id, cursor)
    with contextlib.suppress(OSError):
        (base / FORGOTTEN).unlink()  # those ids are handed out again
    return 0


async def export_batch(user_id: int) -> dict[str, Any]:
    """Export up to BATCH messages newer than the cursor. Returns {written, scanned, complete,
    stalled}: complete when the run caught up with the store, stalled when an unreadable message
    stopped it (the next run retries). Another batch is due while neither."""
    from mailr.core.store import user_store

    base = private.mkdirs(export_root(user_id))
    await asyncio.to_thread(seed_target, user_id)
    try:
        await asyncio.to_thread(apply_target_removals, user_id)
    except ExportBusy:
        pass  # the next run, or the daily purge sweep
    cur_file = base / ".cursor"
    async with session_scope() as db:
        user = await db.get(User, user_id)
        if user is None:
            return {"written": 0, "scanned": 0, "complete": True, "stalled": False}
        cursor = await _checked_cursor(db, user_id, base)
        store = await user_store(db, user)
        labels = dict((await db.execute(text("SELECT id, name FROM labels WHERE user_id=:u"), {"u": user_id})).all())
        rows = (await db.execute(select(
            Message.id, Message.blob_id, Message.label_ids, Message.seen, Message.flagged, Message.answered,
            Message.received_at, Message.date,
        ).where(Message.user_id == user_id, Message.id > cursor, Message.deleted.is_(False),
                Message.draft.is_(False), Message.blob_id.is_not(None)).order_by(Message.id).limit(BATCH))).all()
    if not rows:
        mark_run(user_id)
        return {"written": 0, "scanned": 0, "complete": True, "stalled": False}
    done = await asyncio.to_thread(_exported_blobs, base / ".exported")
    # An unusable target (share not mounted) gets nothing now; seed_target copies the files
    # once it is back.
    tgt = _ready_target(user_id)
    written: list[tuple[str, str]] = []
    last_ok, last_blob = cursor, None
    complete = len(rows) < BATCH
    stalled = False
    scanned = 0
    for r in rows:
        if r.blob_id in done:
            last_ok, last_blob = r.id, r.blob_id
            scanned += 1
            continue
        names = [labels[i] for i in (r.label_ids or []) if i in labels] or ["Archive"]
        folder = base / _safe(names[0])
        for sub in ("cur", "new", "tmp"):
            private.mkdirs(folder / sub)
        flags = "".join(sorted(("S" if r.seen else "") + ("F" if r.flagged else "") + ("R" if r.answered else "")))
        ts = int((r.received_at or r.date or datetime.now(UTC)).timestamp())
        rel = f"{folder.name}/cur/{ts}.{r.id}.mailr:2,{flags}"
        try:
            raw = await store.get_raw(Message(blob_id=r.blob_id))
        except Exception as e:  # noqa: BLE001  one unreadable message must not stop the export
            log.warning("export user %s: message %s unreadable: %s", user_id, r.id, e)
            complete = False
            stalled = True
            break  # keep the cursor before it; the next run retries
        head = (b"X-Mailr-Labels: " + ", ".join(names).encode("utf-8", "replace") + b"\r\n"
                + b"X-Keywords: " + ", ".join(n for n in names if n not in (
                    "Inbox", "Sent", "Drafts", "Spam", "Trash", "Archive")).encode("utf-8", "replace") + b"\r\n")
        tmp = folder / "tmp" / f"{ts}.{r.id}.tmp"
        dest = base / rel
        # Write, fsync and copy in a worker thread: an fsync per message held the event loop.
        await asyncio.to_thread(_write_message, tmp, head + raw, dest, (tgt / rel) if tgt is not None else None)
        written.append((r.blob_id, rel))
        last_ok, last_blob = r.id, r.blob_id
        scanned += 1
    if written:
        await asyncio.to_thread(_finish_batch, base, written, tgt)
    if last_ok != cursor or not cur_file.exists():
        _write_atomic(cur_file, f"{last_ok}\t{last_blob or ''}" if last_ok else "0")
    if complete:
        mark_run(user_id)
    return {"written": len(written), "scanned": scanned, "complete": complete, "stalled": stalled}


async def export_user(user_id: int) -> int:
    """One export batch (export_batch). Returns how many files were written."""
    return (await export_batch(user_id))["written"]


async def export_until_caught_up(user_id: int) -> tuple[int, bool]:
    """Run export_user batch after batch in this process (the `mailr export` command; the worker
    chains jobs instead). Returns (messages written, caught up). Stops when a batch neither
    catches up nor moves the cursor (an unreadable message: the next run retries it)."""
    start = datetime.now(UTC)
    total = 0
    while True:
        r = await export_batch(user_id)
        total += r["written"]
        done = last_run(export_root(user_id))
        if done is not None and done >= start:
            return total, True
        if r["stalled"] or not r["scanned"]:
            return total, False


async def verify(user_id: int, sample: int = 20) -> dict[str, Any]:
    """Restore check: re-read a random sample of exported files (from the backup target when
    one is configured) and compare them byte-for-byte with the store."""
    from mailr.core.store import user_store

    manifest = exported_manifest(user_id)
    if not manifest:
        return {"ok": False, "checked": 0, "reason": "no export yet"}
    if target_root(user_id) is not None and (problem := target_problem()) is not None:
        result = {"ok": False, "checked": 0, "reason": problem}
        _write_atomic(export_root(user_id) / ".verified", json.dumps(result))
        return result
    root = target_root(user_id) or export_root(user_id)
    picks = random.sample(list(manifest.items()), min(sample, len(manifest)))
    failures = []
    async with session_scope() as db:
        user = await db.get(User, user_id)
        store = await user_store(db, user)
    for blob, rel in picks:
        f = root / rel
        if not f.exists():
            failures.append({"blob": blob, "error": "file missing from the backup"})
            continue
        exported = strip_prefix(f.read_bytes())
        try:
            original = await store.get_raw(Message(blob_id=blob))
        except Exception as e:  # noqa: BLE001
            failures.append({"blob": blob, "error": f"store read failed: {e}"[:200]})
            continue
        if hashlib.sha256(exported).digest() != hashlib.sha256(original).digest():
            failures.append({"blob": blob, "error": "content differs"})
    result = {"ok": not failures, "checked": len(picks), "failures": failures[:5],
              "verified_at": datetime.now(UTC).isoformat(), "target": str(root)}
    _write_atomic(export_root(user_id) / ".verified", json.dumps(result))
    return result
