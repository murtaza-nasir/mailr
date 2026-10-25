"""A multipart upload streamed straight to its destination, checked while it is written.

UploadFile has Starlette spool the whole file to TMPDIR before the route runs, so a quota or a
disk check in the route comes after the bytes are on the disk. The large uploads (file imports,
Speakr recordings, phone apps) read the request themselves with `receive`: the file part goes to
the path the route chose once it knows the file's name, and `check` sees every chunk before it is
written, so an upload over the user's quota, the route's limit or the disk reserve stops there and
its file is removed. The caller is authenticated already (core/uploadgate.py), before any of the
body is read.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser, parse_options_header

MAX_FIELDS = 30
MAX_FIELD_BYTES = 64 * 1024  # all text fields together


@dataclass
class Received:
    path: Path
    size: int
    filename: str
    content_type: str
    fields: dict[str, str] = field(default_factory=dict)


def disk_reserve(where: Path, fail_percent: float) -> Callable[[int, int], None]:
    """A `check` that stops before the disk under `where` falls below `fail_percent` free (507)."""
    from mailr.i18n import ApiError

    reserve = shutil.disk_usage(where).total * fail_percent / 100

    def check(_size: int, chunk: int) -> None:
        if shutil.disk_usage(where).free - chunk < reserve:
            raise ApiError(507, "accounts.import_disk_full")

    return check


async def receive(request: Request, open_file: Callable[[str, str], Path],
                  check: Callable[[int, int], None], *, name: str = "file") -> Received:
    """Read a multipart body: the part `name` (one file) into the path `open_file(filename,
    content_type)` returns, the text fields into `fields`. `check(size, chunk)` runs before each
    chunk of the file is written (size counts that chunk) and raises to stop. On any failure the
    file is removed. 422 without the file part, 400 for a body that is not multipart."""
    ctype, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary")
    if ctype != b"multipart/form-data" or not boundary:
        raise HTTPException(400, "send the file as multipart/form-data")  # i18n: internal
    charset = params.get(b"charset", b"utf-8").decode("latin-1")

    state: dict[str, Any] = {"headers": [], "hname": b"", "hval": b"", "kind": None, "fname": "", "data": bytearray()}
    fields: dict[str, str] = {}
    pending: list[bytes] = []
    out: dict[str, Any] = {"path": None, "fh": None, "size": 0, "filename": "", "ctype": "", "files": 0, "text": 0}

    def decode(b: bytes) -> str:
        try:
            return b.decode(charset)
        except (LookupError, UnicodeDecodeError):
            return b.decode("latin-1")

    def on_part_begin() -> None:
        state.update(headers=[], kind=None, fname="", data=bytearray())

    def on_header_field(data: bytes, start: int, end: int) -> None:
        state["hname"] += data[start:end]

    def on_header_value(data: bytes, start: int, end: int) -> None:
        state["hval"] += data[start:end]

    def on_header_end() -> None:
        state["headers"].append((state["hname"].lower(), state["hval"]))
        state["hname"], state["hval"] = b"", b""

    def on_headers_finished() -> None:
        hdrs = dict(state["headers"])
        _, opts = parse_options_header(hdrs.get(b"content-disposition", b""))
        part = decode(opts.get(b"name", b""))
        if b"filename" in opts:
            if part != name or out["files"]:
                raise HTTPException(400, f"send one file, in the field {name}")  # i18n: internal
            out["files"] += 1
            out["filename"] = decode(opts[b"filename"])
            out["ctype"] = decode(hdrs.get(b"content-type", b"")).strip()
            state["kind"] = "file"
        else:
            if len(fields) >= MAX_FIELDS:
                raise HTTPException(400, "too many form fields")  # i18n: internal
            state["kind"], state["fname"] = "text", part

    def on_part_data(data: bytes, start: int, end: int) -> None:
        if state["kind"] == "file":
            pending.append(bytes(data[start:end]))
            return
        out["text"] += end - start
        if out["text"] > MAX_FIELD_BYTES:
            raise HTTPException(400, "the form fields are too long")  # i18n: internal
        state["data"].extend(data[start:end])

    def on_part_end() -> None:
        if state["kind"] == "text":
            fields[state["fname"]] = decode(bytes(state["data"]))

    parser = MultipartParser(boundary, {
        "on_part_begin": on_part_begin, "on_part_data": on_part_data, "on_part_end": on_part_end,
        "on_header_field": on_header_field, "on_header_value": on_header_value,
        "on_header_end": on_header_end, "on_headers_finished": on_headers_finished,
    })

    def write(chunks: list[bytes]) -> None:
        for c in chunks:
            out["fh"].write(c)

    try:
        async for chunk in request.stream():
            try:
                parser.write(chunk)
            except FormParserError:
                raise HTTPException(400, "invalid multipart data") from None  # i18n: internal
            if not pending:
                continue
            if out["fh"] is None:
                out["path"] = open_file(out["filename"], out["ctype"])
                out["fh"] = out["path"].open("xb")
            n = sum(len(c) for c in pending)
            out["size"] += n
            check(out["size"], n)
            batch = pending[:]
            pending.clear()
            await asyncio.to_thread(write, batch)
        try:
            parser.finalize()
        except FormParserError:
            raise HTTPException(400, "invalid multipart data") from None  # i18n: internal
        if not out["files"]:
            raise HTTPException(422, f"the file is missing (multipart field {name})")  # i18n: internal
        if out["fh"] is None:  # an empty file
            out["path"] = open_file(out["filename"], out["ctype"])
            out["fh"] = out["path"].open("xb")
        out["fh"].close()
    except BaseException:
        if out["fh"] is not None:
            out["fh"].close()
            out["path"].unlink(missing_ok=True)
        raise
    return Received(out["path"], out["size"], out["filename"], out["ctype"], fields)
