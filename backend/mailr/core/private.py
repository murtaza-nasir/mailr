"""Backup files hold every user's mail and data, so they are readable by mailr's own account only:
files 0600, folders 0700, whatever the process umask (027 leaves them readable by the group).
Used by the database dumps (core/dbdump.py), the Maildir export (core/backup.py) and the
calendar and notes exports, on the server and on the backup target.

A share that refuses chmod (some CIFS mounts) keeps its own modes; a failed chmod never stops a
backup."""

from __future__ import annotations

import os
from pathlib import Path
from typing import IO, Any

FILE_MODE = 0o600
DIR_MODE = 0o700


def tighten(p: Path) -> None:
    """Take the group and other bits off one file or folder (one an older release made)."""
    try:
        st = p.stat()
        if st.st_mode & 0o077:
            os.chmod(p, st.st_mode & 0o7700)
    except OSError:
        pass


def mkdirs(p: Path, parents: bool = True) -> Path:
    """`mkdir -p` whose new folders are 0700 (Path.mkdir makes the parents with the umask's mode),
    and `p` itself 0700 when it was already there. parents=False makes only `p`."""
    missing = []
    q = p
    while not q.is_dir():
        missing.append(q)
        if not parents:
            break
        q = q.parent
    for d in reversed(missing):
        try:
            os.mkdir(d, DIR_MODE)
        except FileExistsError:
            pass
    tighten(p)
    return p


def open_new(p: Path, mode: str = "wb", **kw: Any) -> IO[Any]:
    """open() for writing, creating the file 0600 (and making an existing one 0600)."""
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if "a" in mode else os.O_TRUNC)
    fd = os.open(p, flags, FILE_MODE)
    try:
        os.fchmod(fd, FILE_MODE)
    except OSError:
        pass
    return os.fdopen(fd, mode, **kw)


def write_bytes(p: Path, data: bytes) -> None:
    with open_new(p, "wb") as f:
        f.write(data)


def write_text(p: Path, text: str, **kw: Any) -> None:
    with open_new(p, "w", encoding=kw.get("encoding", "utf-8"), newline=kw.get("newline")) as f:
        f.write(text)
