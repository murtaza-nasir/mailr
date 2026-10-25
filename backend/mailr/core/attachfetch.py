"""Attachments as something a model can read: text, a downscaled image, or a PDF's text.

Used by the agent tools get_attachment and list_attachments (agent/attachment_tools.py). The web
reader downloads the original file (GET /messages/{id}/attachments/{part}); agents get a
derived copy instead:

- images (PNG, JPEG, GIF, WebP): decoded, turned upright by their EXIF orientation, scaled so
  the long side is at most `max_px`, and re-encoded as JPEG quality 85 (PNG when the image uses
  transparency) with no metadata: EXIF, GPS and XMP never leave the server. HEIC is not
  converted (no HEIF decoder is installed); the tool says so.
- PDFs: the text of every page (or of `page`), and one page rendered as an image.
- text, Markdown, CSV, JSON, calendar files, HTML (as text) and attached emails: text, at most
  TEXT_CAP characters.
- anything else: its name, size and type only.

Images and PDFs are decoded in a child process with memory and CPU limits (core/attachwork.py).
Originals above MAX_ORIGINAL bytes are refused.
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import json
import mimetypes
import os
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

MAX_ORIGINAL = 25 * 1024 * 1024
TEXT_CAP = 200_000
HTML_INPUT_CAP = 3_000_000  # bytes of HTML converted to text
DEFAULT_MAX_PX, MIN_PX, MAX_PX = 1600, 256, 4096
WORKER = Path(__file__).with_name("attachwork.py")
WORKER_TIMEOUT = 45.0
# At most this many fetches per caller (token, or the signed-in user's assistant) per window.
RATE_LIMIT, RATE_WINDOW = 60, 600.0

IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpeg", "image/jpg": "jpeg", "image/pjpeg": "jpeg",
               "image/gif": "gif", "image/webp": "webp"}
HEIC_TYPES = {"image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence"}
TEXT_TYPES = {"text/plain", "text/markdown", "text/x-markdown", "text/csv", "text/tab-separated-values",
              "text/calendar", "application/ics", "text/x-vcard", "text/vcard", "application/json",
              "text/json", "application/ld+json", "text/x-log"}
HTML_TYPES = {"text/html", "application/xhtml+xml"}
EML_TYPES = {"message/rfc822"}
EXT_KIND = {".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
            ".heic": "heic", ".heif": "heic", ".pdf": "pdf", ".txt": "text", ".md": "text", ".markdown": "text",
            ".csv": "text", ".tsv": "text", ".ics": "text", ".vcf": "text", ".json": "text", ".log": "text",
            ".htm": "html", ".html": "html", ".eml": "eml"}


class AttachmentError(Exception):
    """The attachment cannot be returned; the message says why, for the model and the user."""


def clamp_px(v: Any) -> int:
    if v is None or v == "":
        return DEFAULT_MAX_PX
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise AttachmentError("'max_px' must be an integer") from None
    return max(MIN_PX, min(MAX_PX, n))


def kind_of(ctype: str, fname: str, data: bytes = b"") -> str:
    """image, heic, pdf, text, html, eml or other: by content type, then by file name and the
    content's first bytes (many mailers send everything as application/octet-stream)."""
    ctype = (ctype or "").lower()
    if ctype in IMAGE_TYPES:
        return "image"
    if ctype in HEIC_TYPES:
        return "heic"
    if ctype == "application/pdf":
        return "pdf"
    if ctype in TEXT_TYPES:
        return "text"
    if ctype in HTML_TYPES:
        return "html"
    if ctype in EML_TYPES:
        return "eml"
    head = data[:16]
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")) or (
            head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return "image"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"heif"):
        return "heic"
    ext = os.path.splitext((fname or "").lower())[1]
    if ext in EXT_KIND and (ctype.startswith(("application/octet", "binary/", "application/x-download",
                                              "application/download", "application/force-download",
                                              "text/", "application/unknown")) or not ctype):
        return EXT_KIND[ext]
    if ctype.startswith("text/") and ctype not in ("text/rtf", "text/x-vcalendar"):
        return "text"
    return "other"


def part_bytes(raw: bytes, index: int) -> tuple[bytes, str, str, str | None]:
    """(payload, content type, file name, charset) of the walk() index recorded in a message's
    attachments (core/mime.py). An attached email (message/rfc822) comes back as its bytes."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    for idx, part in enumerate(msg.walk()):
        if idx != index:
            continue
        ctype = part.get_content_type()
        if ctype == "message/rfc822":
            inner = part.get_payload()
            if isinstance(inner, list) and inner:
                data = inner[0].as_bytes(policy=email.policy.default)
            else:
                data = part.get_payload(decode=True) or b""
        else:
            data = part.get_payload(decode=True) or b""
        from mailr.core.mime import safe_filename

        # The sender's file name, cleaned like everywhere else (bidi controls, invisible characters, paths).
        return data, ctype, safe_filename(part.get_filename()) or "attachment", part.get_content_charset()
    raise KeyError(index)


def _decode(data: bytes, charset: str | None) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    for cs in (charset, "utf-8"):
        if not cs:
            continue
        try:
            return data.decode(cs)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("cp1252", "replace")


def _cap(text: str) -> tuple[str, bool]:
    text = text.replace("\x00", "")
    return (text[:TEXT_CAP], True) if len(text) > TEXT_CAP else (text, False)


def _text_of(kind: str, data: bytes, charset: str | None) -> str:
    if kind == "html":
        from mailr.core.mime import html_to_text

        return html_to_text(_decode(data[:HTML_INPUT_CAP], charset))
    if kind == "eml":
        from mailr.core.mime import parse

        p = parse(data)
        head = [f"From: {p.from_name} <{p.from_email}>".replace(" <>", ""),
                "To: " + ", ".join(a.get("email", "") for a in p.to),
                *(["Cc: " + ", ".join(a.get("email", "") for a in p.cc)] if p.cc else []),
                f"Date: {p.date.isoformat() if p.date else ''}", f"Subject: {p.subject}"]
        names = [a.get("filename") for a in p.attachments if not a.get("inline")]
        if names:
            head.append("Attachments: " + ", ".join(n or "attachment" for n in names))
        return "\n".join(head) + "\n\n" + (p.text or "")
    return _decode(data, charset)


# ---------------------------------------------------------------- the decoder process

_SEM: asyncio.Semaphore | None = None


def _sem() -> asyncio.Semaphore:
    global _SEM
    if _SEM is None:
        _SEM = asyncio.Semaphore(2)  # decoders running at once, for the whole server
    return _SEM


async def _work(args: list[str], data: bytes) -> dict[str, Any]:
    async with _sem():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(WORKER), *args, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": os.defpath, "LANG": "C.UTF-8"})  # none of the server's secrets
        try:
            out, _ = await asyncio.wait_for(proc.communicate(data), WORKER_TIMEOUT)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise AttachmentError("decoding the file took too long") from None
    try:
        res = json.loads(out or b"{}")
    except ValueError:
        res = {}
    if proc.returncode != 0 or not res:
        raise AttachmentError("the file could not be decoded (the decoder stopped)")
    if not res.get("ok"):
        raise AttachmentError(res.get("error") or "the file could not be decoded")
    return res


THUMB_PX = 320  # the Attachments grid's cards
THUMB_MIN_BYTES = 150_000  # a smaller image is sent as it is: decoding it saves nothing


async def thumbnail(data: bytes) -> tuple[bytes, str] | None:
    """A small copy of an image for a preview card, decoded in the limited child process, without
    metadata; None when the image is small already or cannot be decoded (the caller sends it as
    it is)."""
    if len(data) < THUMB_MIN_BYTES:
        return None
    try:
        res = await _work(["image", str(THUMB_PX)], data)
    except AttachmentError:
        return None
    import base64

    return base64.b64decode(res["data"]), res["mime_type"]


# ---------------------------------------------------------------- rate limit

_CALLS: dict[str, deque[float]] = {}


def check_rate(key: str, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    q = _CALLS.setdefault(key, deque())
    while q and now - q[0] > RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        raise AttachmentError(f"too many attachment fetches: at most {RATE_LIMIT} in {int(RATE_WINDOW // 60)} "
                              "minutes; try again later")
    q.append(now)
    if len(_CALLS) > 10_000:  # forget idle callers
        for k in [k for k, v in _CALLS.items() if not v or now - v[-1] > RATE_WINDOW]:
            _CALLS.pop(k, None)


# ---------------------------------------------------------------- extraction


async def extract(data: bytes, ctype: str, fname: str, charset: str | None, *, max_px: int = DEFAULT_MAX_PX,
                  page: int | None = None, render: bool = True) -> dict[str, Any]:
    """What a model gets from one attachment: {"kind", "text"?, "truncated"?, "image"?, "pages"?,
    "note"?}. `image` holds base64 `data` with mime_type, width and height. `render` False skips
    rendering a PDF page (the caller cannot show images)."""
    if len(data) > MAX_ORIGINAL:
        raise AttachmentError(f"the attachment is {len(data) / 1048576:.1f} MB; files over "
                              f"{MAX_ORIGINAL // 1048576} MB are not returned")
    kind = kind_of(ctype, fname, data)
    if kind == "image":
        res = await _work(["image", str(max_px)], data)
        img = {k: res[k] for k in ("mime_type", "width", "height", "bytes", "data", "original_width",
                                   "original_height")}
        return {"kind": "image", "image": img}
    if kind == "pdf":
        res = await _work(["pdf", str(max_px), str(page or 0), "1" if render else "0"], data)
        out: dict[str, Any] = {"kind": "pdf", "pages": res["pages"], "text": res.get("text", ""),
                               "truncated": bool(res.get("truncated"))}
        if page:
            out["page"] = page
        if res.get("image"):
            out["image"] = res["image"]
        if res.get("render_error"):
            out["note"] = res["render_error"]
        if not out["text"].strip():
            out["note"] = ((out.get("note") + "; ") if out.get("note") else "") + \
                "the PDF has no text layer (perhaps a scan)"
        return out
    if kind in ("text", "html", "eml"):
        text, truncated = _cap(await asyncio.to_thread(_text_of, kind, data, charset))
        return {"kind": "text", "text": text, "truncated": truncated,
                **({"note": f"truncated to the first {TEXT_CAP:,} characters"} if truncated else {})}
    if kind == "heic":
        return {"kind": "other", "note": "HEIC/HEIF images cannot be converted on this server; download the "
                                         "file in Mailr to view it"}
    guess = mimetypes.guess_type(fname or "")[0]
    return {"kind": "other", "note": f"this file type ({ctype or guess or 'unknown'}) cannot be returned as text "
                                     "or an image; only its name, size and type are shown"}
