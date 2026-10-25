"""File import: an uploaded .mbox (incl. Google Takeout), or a .zip of Maildir folders
or .mbox files.

Resumable: state records the file index and, inside an mbox, the number of messages
already handed to ingest. Takeout's X-Gmail-Labels header becomes labels; Maildir folder
names become labels (Dovecot "." and "/" hierarchies); Maildir flags become keywords.

A message that cannot be imported is skipped and counted (state `failed`, the first FAILED_KEEP
with their place and error), so one bad message never stops an import. FAILED_IN_A_ROW failures
in a row stop the run instead: then the trouble is not the messages (the database or the mail
store is down), and the runner starts it again later from the saved place.
"""

from __future__ import annotations

import asyncio
import email.utils
import logging
import mailbox
import os
import re
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mailr.config import get_settings
from mailr.connectors.base import Capabilities, MailConnector, TestResult, register
from mailr.connectors.imap import ROLE_NAMES

log = logging.getLogger("mailr.fileimport")

TAKEOUT_ROLES = {"inbox": "inbox", "sent": "sent", "spam": "junk", "trash": "trash", "drafts": "drafts",
                 "archived": "archive"}
TAKEOUT_SKIP = {"opened", "unread", "important", "starred", "chat", "chats", "draft"}


def imports_root(user_id: int) -> Path:
    return Path(get_settings().data_dir) / "imports" / str(user_id)


# Limits for uploaded files (Oct 2026). A message larger than MAX_MESSAGE_BYTES is
# skipped (the IMAP limit); a zip may unpack to at most max_unpacked_bytes() in all, and a
# member larger than RATIO_MIN_BYTES may be at most MAX_RATIO times its compressed size (mail
# compresses 3 to 10 times; a zip bomb thousands of times).
MAX_MESSAGE_BYTES = 75 * 1024 * 1024
FAILED_KEEP = 200
FAILED_IN_A_ROW = 20
MAX_RATIO = 100
RATIO_MIN_BYTES = 1024 * 1024


def max_import_bytes() -> int:
    """The largest file a user may upload for an import (MAILR_MAX_IMPORT_BYTES)."""
    return int(os.environ.get("MAILR_MAX_IMPORT_BYTES", str(20 * 1024**3)))


def max_unpacked_bytes() -> int:
    """The most a zip may unpack to: four times the upload limit."""
    return 4 * max_import_bytes()


def import_quota_bytes() -> int:
    """What one user's import folder may hold in all: twice the upload limit."""
    return 2 * max_import_bytes()


# The sizes of a user's import uploads of the last 24 hours ("<unix time> <bytes>" lines), for
# MAILR_IMPORT_BYTES_PER_DAY. In the import folder, but not counted as part of what it holds.
UPLOAD_LEDGER = ".uploaded"
DAY = 86400


def _ledger(user_id: int) -> list[tuple[float, int]]:
    try:
        lines = (imports_root(user_id) / UPLOAD_LEDGER).read_text().split("\n")
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            at, size = line.split()
            out.append((float(at), int(size)))
        except ValueError:
            continue
    return out


def import_bytes_today(user_id: int) -> int:
    """What the user uploaded for imports in the last 24 hours."""
    since = time.time() - DAY
    return sum(size for at, size in _ledger(user_id) if at > since)


def record_import_bytes(user_id: int, size: int) -> None:
    import fcntl

    root = imports_root(user_id)
    root.mkdir(parents=True, exist_ok=True)
    with (root / UPLOAD_LEDGER).open("a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        since = time.time() - DAY
        keep = [ln for ln in f.read().split("\n") if ln.strip() and _recent(ln, since)]
        f.seek(0)
        f.truncate()
        f.write("\n".join([*keep, f"{time.time():.0f} {int(size)}"]) + "\n")


def _recent(line: str, since: float) -> bool:
    try:
        return float(line.split()[0]) > since
    except (ValueError, IndexError):
        return False


def _too_compressed(info: zipfile.ZipInfo) -> bool:
    return info.file_size > RATIO_MIN_BYTES and info.file_size > MAX_RATIO * max(info.compress_size, 1)


def _read_member(z: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes | None:
    """A zip member's bytes, read with a counter: None when it is larger than `limit`, whatever
    its header claims."""
    if info.file_size > limit or _too_compressed(info):
        return None
    out = bytearray()
    try:
        with z.open(info) as f:
            while chunk := f.read(1024 * 1024):
                out += chunk
                if len(out) > limit:
                    return None
    except (zipfile.BadZipFile, OSError, EOFError) as e:  # a damaged or lying member
        log.warning("zip member %s unreadable: %s", info.filename, e)
        return None
    return bytes(out)


def takeout_labels(header: str | None) -> dict[str, Any]:
    roles, names = [], []
    kw = {"seen": True, "important": False, "flagged": False}
    category = None
    for lab in [x.strip() for x in (header or "").split(",") if x.strip()]:
        low = lab.lower()
        if low in TAKEOUT_ROLES:
            roles.append(TAKEOUT_ROLES[low])
        elif low == "unread":
            kw["seen"] = False
        elif low == "important":
            kw["important"] = True
        elif low == "starred":
            kw["flagged"] = True
        elif low.startswith("category "):
            category = {"personal": "primary", "promotions": "promotions", "social": "social",
                        "updates": "updates", "forums": "forums", "purchases": "purchases"}.get(low.split(" ", 1)[1])
        elif low in TAKEOUT_SKIP:
            continue
        else:
            names.append(lab)
    if not roles or roles == ["archive"]:
        roles = ["archive"]
    return {"roles": roles, "label_names": names, "keywords": kw, "category": category}


def folder_to_target(folder: str) -> dict[str, Any]:
    f = folder.strip("/").replace("\\", "/")
    parts = [p for p in re.split(r"[./]", f) if p and p not in ("cur", "new", "tmp")]
    if parts and parts[0].upper() == "INBOX":
        parts = parts[1:]
    if not parts:
        return {"roles": ["inbox"], "label_names": []}
    leaf = parts[-1].lower()
    for role, names in ROLE_NAMES.items():
        if leaf in names and len(parts) == 1:
            return {"roles": [role], "label_names": []}
    return {"roles": ["archive"], "label_names": ["/".join(parts)]}


def maildir_flags(name: str) -> dict[str, bool]:
    info = name.split(":2,", 1)[1] if ":2," in name else ""
    return {"seen": "S" in info, "flagged": "F" in info, "answered": "R" in info, "draft": "D" in info}


def _date_of(msg: mailbox.Message) -> datetime | None:
    try:
        d = email.utils.parsedate_to_datetime(msg.get("Date", ""))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


@register
class FileImportConnector(MailConnector):
    kind = "file"
    capabilities = Capabilities(modes=("import",))

    def _path(self) -> Path:
        p = Path(self.row.config.get("path", ""))
        root = imports_root(self.row.user_id).resolve()
        rp = p.resolve()
        if root not in rp.parents:
            raise ValueError("import file must be inside the user's import folder")
        if not rp.exists():
            raise FileNotFoundError(str(p))
        return rp

    async def test(self) -> TestResult:
        try:
            p = self._path()
        except FileNotFoundError as e:
            if (getattr(self.row, "state", None) or {}).get("done"):
                return TestResult(ok=True, detail="Import finished")
            return TestResult(ok=False, detail=str(e))
        except ValueError as e:
            return TestResult(ok=False, detail=str(e))
        return TestResult(ok=True, detail=f"{p.name}: {p.stat().st_size:,} bytes")

    async def run(self, ctx) -> None:
        if ctx.state.get("done"):
            await ctx.set_status("idle", ctx.state.get("summary", "Import finished"), synced=True)
            from mailr.connectors.imap import _wait

            await _wait(ctx.stopping, 3600 * 24 * 365)
            return
        await self.sync_once(ctx)

    async def sync_once(self, ctx) -> dict[str, int]:
        totals = {"new": 0, "duplicates": 0, "skipped": 0, "failed": int(ctx.state.get("failed_count") or 0)}
        self._in_a_row = 0
        try:
            p = self._path()
        except FileNotFoundError:
            if ctx.state.get("done"):
                return totals  # finished earlier; the upload was removed then
            raise
        if zipfile.is_zipfile(p):
            await self._import_zip(ctx, p, totals)
        else:
            await self._import_mbox(ctx, p.name, p, totals, takeout=True)
        if not ctx.stopping.is_set():
            ctx.state["done"] = True
            ctx.state["summary"] = f"Imported {totals['new']:,} messages ({totals['duplicates']:,} already present)"
            if totals["skipped"]:
                ctx.state["summary"] += f"; {totals['skipped']:,} skipped (too large)"
            if totals["failed"]:
                ctx.state["summary"] += f"; {totals['failed']:,} could not be imported (see the server log)"
            await ctx.save_state(ctx.state)
            # The messages are in the store now: the upload stops counting against the
            # user's import space.
            try:
                p.unlink()
            except OSError:
                pass
            await ctx.set_status("idle", ctx.state["summary"], synced=True)
        return totals

    async def _ingest(self, ctx, raw: bytes, kw: dict[str, Any], totals: dict[str, int], where: str) -> None:
        try:
            res = await ctx.ingest(raw, source="import", run_rules=False, notify=False, **kw)
        except Exception as e:  # one bad message must not stop the import (module docstring)
            self._in_a_row += 1
            if self._in_a_row >= FAILED_IN_A_ROW:
                raise
            failed = ctx.state.setdefault("failed", [])
            if any(f.get("at") == where for f in failed):
                return  # the same message again, after a restart from the last saved place
            totals["failed"] += 1
            ctx.state["failed_count"] = totals["failed"]
            if len(failed) < FAILED_KEEP:
                failed.append({"at": where, "error": f"{type(e).__name__}: {e}"[:200]})
            log.warning("import %s: %s could not be imported; skipped: %s", self.row.id, where, e)
            return
        self._in_a_row = 0
        totals["duplicates" if res.get("duplicate") else "new"] += 1

    async def _import_mbox(self, ctx, key: str, path: Path, totals: dict[str, int], takeout: bool,
                           folder_kw: dict[str, Any] | None = None) -> None:
        pos = ctx.state.setdefault("mbox", {})
        start = int(pos.get(key, 0))
        box = mailbox.mbox(str(path), create=False)
        keys = await asyncio.to_thread(lambda: list(box.keys()))
        total = len(keys)
        toc = getattr(box, "_toc", None) or {}
        for i in range(start, total):
            if ctx.stopping.is_set():
                break
            span = toc.get(keys[i])
            if span and span[1] - span[0] > MAX_MESSAGE_BYTES:
                # Never read into memory: recorded as skipped.
                log.warning("import %s: message %s of %s is larger than %s MB; skipped", self.row.id, i, key,
                            MAX_MESSAGE_BYTES // (1024 * 1024))
                totals["skipped"] += 1
                pos[key] = i + 1
                continue
            msg = await asyncio.to_thread(box.get_message, keys[i])
            raw = msg.as_bytes()
            if len(raw) > MAX_MESSAGE_BYTES:
                totals["skipped"] += 1
                continue
            if takeout and msg.get("X-Gmail-Labels") is not None:
                lk = takeout_labels(msg.get("X-Gmail-Labels"))
                if "drafts" in lk["roles"]:
                    continue
                kw = {"roles": lk["roles"], "label_names": lk["label_names"], "keywords": lk["keywords"]}
                if lk["category"]:
                    kw["category"] = lk["category"]
            else:
                kw = dict(folder_kw or {"roles": ["archive"], "label_names": []})
                flags = msg.get_flags() if hasattr(msg, "get_flags") else ""
                kw["keywords"] = {"seen": "R" in flags or "O" in flags, "flagged": "F" in flags,
                                  "answered": "A" in flags}
            kw["received_at"] = _date_of(msg)
            kw["upstream"] = {"connector": self.row.id, "file": key, "index": i}
            await self._ingest(ctx, raw, kw, totals, f"message {i + 1} of {key}")
            if (i + 1) % 50 == 0 or i + 1 == total:
                pos[key] = i + 1
                await ctx.save_state(ctx.state)
                await ctx.set_status("syncing", f"Importing {Path(key).name}: {i + 1:,} of {total:,}")
        box.close()

    async def _import_zip(self, ctx, p: Path, totals: dict[str, int]) -> None:
        with zipfile.ZipFile(p) as z:
            infos = {i.filename: i for i in z.infolist()}
            names = sorted(n for n in infos if not n.endswith("/"))
            mboxes = [n for n in names if n.lower().endswith(".mbox") or n.lower().endswith(".mbx")]
            maildir = [n for n in names if re.search(r"(^|/)(cur|new)/[^/]+$", n)]
            unpacked = sum(infos[n].file_size for n in mboxes + maildir)
            if unpacked > max_unpacked_bytes():
                raise ValueError(f"the archive unpacks to {unpacked // 1024**3:,} GB, larger than "
                                 f"{max_unpacked_bytes() // 1024**3:,} GB")
            done_files = set(ctx.state.setdefault("files_done", []))
            tmp = imports_root(self.row.user_id) / f".extract-{self.row.id}"
            tmp.mkdir(parents=True, exist_ok=True)
            for n in mboxes:
                if n in done_files or ctx.stopping.is_set():
                    continue
                out = tmp / re.sub(r"[^A-Za-z0-9._-]+", "_", n)
                if _too_compressed(infos[n]):
                    log.warning("import %s: %s unpacks to %s times its size; skipped", self.row.id, n,
                                infos[n].file_size // max(infos[n].compress_size, 1))
                    totals["skipped"] += 1
                    done_files.add(n)
                    ctx.state["files_done"] = sorted(done_files)
                    continue
                if not out.exists():
                    try:
                        await asyncio.to_thread(_extract, z, infos[n], out)
                    except ValueError:
                        out.unlink(missing_ok=True)
                        raise
                folder = os.path.splitext(n)[0].split("/")[-1]
                await self._import_mbox(ctx, n, out, totals, takeout=True, folder_kw=folder_to_target(folder))
                if not ctx.stopping.is_set():
                    done_files.add(n)
                    ctx.state["files_done"] = sorted(done_files)
                    out.unlink(missing_ok=True)
                    await ctx.save_state(ctx.state)
            # Maildir entries resume by position in the sorted list (names can number 100k+).
            start = int(ctx.state.get("maildir_index", 0))
            total = len(maildir)
            for i in range(start, total):
                if ctx.stopping.is_set():
                    break
                n = maildir[i]
                folder = n.rsplit("/", 2)[0] if n.count("/") >= 2 else ""
                kw = folder_to_target(folder)
                kw["keywords"] = maildir_flags(n.rsplit("/", 1)[-1])
                if "/new/" in n:
                    kw["keywords"]["seen"] = False
                if not kw["keywords"].get("draft"):
                    raw = await asyncio.to_thread(_read_member, z, infos[n], MAX_MESSAGE_BYTES)
                    if raw is None:
                        log.warning("import %s: %s is larger than %s MB or packed too densely; skipped",
                                    self.row.id, n, MAX_MESSAGE_BYTES // (1024 * 1024))
                        totals["skipped"] += 1
                    else:
                        kw["received_at"] = _date_of(mailbox.Message(raw))
                        kw["upstream"] = {"connector": self.row.id, "file": n}
                        await self._ingest(ctx, raw, kw, totals, n)
                if (i + 1) % 50 == 0 or i + 1 == total:
                    ctx.state["maildir_index"] = i + 1
                    await ctx.save_state(ctx.state)
                    await ctx.set_status("syncing", f"Importing Maildir: {i + 1:,} of {total:,}")
            try:
                tmp.rmdir()
            except OSError:
                pass


def _extract(z: zipfile.ZipFile, info: zipfile.ZipInfo, out: Path) -> None:
    """Unpack one member to disk, stopping when it grows past what its header declared."""
    written = 0
    with z.open(info) as src, open(out, "wb") as dst:
        while chunk := src.read(1024 * 1024):
            written += len(chunk)
            if written > info.file_size:
                raise ValueError(f"{info.filename} unpacks to more than it declares")
            dst.write(chunk)
