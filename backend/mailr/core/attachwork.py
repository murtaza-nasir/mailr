"""Decode one attachment in a separate, resource-limited process (core/attachfetch.py runs it).

Images and PDFs from email are untrusted input to C decoders (libjpeg, libpng, libwebp,
PDFium). They are decoded here, in a child process started with `python -I` that lowers its own
address-space and CPU limits first, so a decompression bomb or a decoder crash costs this
process, never the API server. This file imports nothing from mailr on purpose.

    python -I attachwork.py image <max_px>              < bytes   -> JSON on stdout
    python -I attachwork.py pdf <max_px> <page> <render> < bytes   -> JSON on stdout
    python -I attachwork.py doc <format>                < bytes   -> JSON on stdout

Output: {"ok": true, ...} or {"ok": false, "error": "..."}. Image data is base64 in `data`.

Office and OpenDocument files (docx, xlsx, pptx, odt, ods, odp) are ZIP archives of XML: before
any library opens one, the archive's entries are checked (count, total and single sizes, and the
compression ratio), so a ZIP bomb is refused unread; the reads stay within the sizes the archive
declares. Sheets are read row by row with caps on sheets, rows, columns, cells and characters;
repeated rows and columns in OpenDocument sheets are never expanded beyond those caps. Embedded
images are not read: they are counted. RTF is read as text with striprtf.
"""

from __future__ import annotations

import base64
import io
import json
import sys
from typing import Any

MEMORY_LIMIT = 1536 * 1024 * 1024  # address space, bytes
CPU_LIMIT = 30  # seconds
MAX_PIXELS = 50_000_000  # decoded pixels; larger images are refused as decompression bombs
TEXT_CAP = 200_000  # characters of PDF text
MAX_TEXT_PAGES = 500
FORMATS = ["PNG", "JPEG", "GIF", "WEBP"]


def _limit() -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT, CPU_LIMIT))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, ValueError, OSError):
        pass


def _encode(im, max_px: int) -> dict:
    """Downscale so the long side is at most max_px and re-encode without any metadata: JPEG
    quality 85, or PNG when the image really uses transparency (screenshots, logos)."""
    from PIL import Image

    alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
    if alpha:
        im = im.convert("RGBA")
        alpha = im.getchannel("A").getextrema()[0] < 255
    if not alpha and im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    im.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    im.info = {}  # no EXIF, GPS, XMP, comments or text chunks travel with the copy
    buf = io.BytesIO()
    if alpha:
        im.save(buf, "PNG", optimize=True)
        mime = "image/png"
    else:
        im.save(buf, "JPEG", quality=85, optimize=True)
        mime = "image/jpeg"
    return {"mime_type": mime, "width": im.width, "height": im.height, "bytes": buf.tell(),
            "data": base64.b64encode(buf.getvalue()).decode("ascii")}


def image(data: bytes, max_px: int) -> dict:
    import warnings

    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    try:
        im = Image.open(io.BytesIO(data), formats=FORMATS)
        fmt = im.format
        ow, oh = im.size
        if ow * oh > MAX_PIXELS:
            return {"ok": False, "error": f"the image is too large to decode ({ow}×{oh} pixels)"}
        im.seek(0)  # an animated GIF or WebP: its first frame
        im.load()
        im = ImageOps.exif_transpose(im)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        return {"ok": False, "error": "the image is too large to decode"}
    except Exception as e:  # noqa: BLE001  any decoder failure is the file's fault
        return {"ok": False, "error": f"the image could not be decoded ({type(e).__name__})"}
    return {"ok": True, "format": fmt, "original_width": ow, "original_height": oh, **_encode(im, max_px)}


def pdf(data: bytes, max_px: int, page: int, render: bool) -> dict:
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as e:
        msg = str(e).lower()
        if "password" in msg:
            return {"ok": False, "error": "the PDF is encrypted with a password"}
        return {"ok": False, "error": "the PDF could not be opened"}
    n = len(doc)
    if n == 0:
        return {"ok": True, "pages": 0, "text": "", "truncated": False}
    if page > n:
        return {"ok": False, "error": f"the PDF has {n} page(s); page {page} does not exist"}
    out: dict = {"ok": True, "pages": n}
    parts: list[str] = []
    size, truncated = 0, False
    wanted = [page - 1] if page else range(min(n, MAX_TEXT_PAGES))
    for i in wanted:
        try:
            tp = doc[i].get_textpage()
            t = tp.get_text_bounded()
        except Exception:  # noqa: BLE001
            t = ""
        block = (f"--- page {i + 1} ---\n" if not page else "") + t.replace("\r\n", "\n").strip()
        parts.append(block)
        size += len(block)
        if size > TEXT_CAP:
            truncated = True
            break
    if not page and n > MAX_TEXT_PAGES:
        truncated = True
    text = "\n\n".join(parts)
    out["text"] = text[:TEXT_CAP]
    out["truncated"] = truncated or len(text) > TEXT_CAP
    if render:
        try:
            pg = doc[(page or 1) - 1]
            w, h = pg.get_size()
            scale = min(max_px / max(w, h, 1.0), 4.0)
            im = pg.render(scale=scale).to_pil()
            out["image"] = {"page": page or 1, **_encode(im, max_px)}
        except Exception as e:  # noqa: BLE001  the text is still useful without the picture
            out["render_error"] = f"the page could not be rendered ({type(e).__name__})"
    return out


# ---------------------------------------------------------------- documents

ZIP_MAX_ENTRIES = 5000
ZIP_MAX_TOTAL = 100 * 1024 * 1024  # bytes, all entries uncompressed
ZIP_MAX_ENTRY = 50 * 1024 * 1024  # bytes, one entry uncompressed
ZIP_MAX_RATIO = 200  # uncompressed / compressed, for entries over 1 MB
MAX_SHEETS = 20
MAX_ROWS = 2000  # per sheet
MAX_COLS = 60
MAX_CELLS = 100_000  # all sheets
MAX_REPEAT = 50  # an OpenDocument row or cell repeated (with content) counts at most this often
MAX_SLIDES = 500
RTF_MAX = 10 * 1024 * 1024


class _Full(Exception):
    """The text reached its cap."""


class _Text:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.size = 0
        self.truncated = False

    def add(self, line: str) -> None:
        line = line.replace("\x00", "").rstrip()
        if not line:
            return
        if self.size + len(line) + 1 > TEXT_CAP:
            self.parts.append(line[:max(0, TEXT_CAP - self.size)])
            self.truncated = True
            raise _Full
        self.parts.append(line)
        self.size += len(line) + 1

    def text(self) -> str:
        return "\n".join(self.parts)


def zip_guard(data: bytes) -> str | None:
    """None when the archive may be opened, else why not."""
    import zipfile

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError):
        return "the file is damaged or not a document"
    infos = zf.infolist()
    if len(infos) > ZIP_MAX_ENTRIES:
        return "the document holds too many parts"
    total = 0
    for i in infos:
        total += i.file_size
        if i.file_size > ZIP_MAX_ENTRY or total > ZIP_MAX_TOTAL:
            return "the document is too large once unpacked"
        if i.file_size > 1024 * 1024 and i.file_size > ZIP_MAX_RATIO * max(i.compress_size, 1):
            return "the document is compressed suspiciously far (a ZIP bomb?)"
    return None


def _cell(v) -> str:
    if v is None:
        return ""
    t = str(v).replace("\r", " ").replace("\n", " ").strip()
    if any(c in t for c in ',"'):
        t = '"' + t.replace('"', '""') + '"'
    return t


def _row_line(cells: list[str]) -> str:
    while cells and not cells[-1]:
        cells.pop()
    return ",".join(cells)


def _docx(data: bytes, out: _Text, notes: list[str]) -> None:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(io.BytesIO(data))
    body = d.element.body
    for el in body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            out.add(Paragraph(el, d).text)
        elif tag == "tbl":
            rows = 0
            for row in Table(el, d).rows:
                rows += 1
                if rows > MAX_ROWS:
                    notes.append("a table was cut short")
                    break
                out.add(" | ".join(c.text.replace("\n", " ").strip() for c in row.cells[:MAX_COLS]))
    images = len(d.inline_shapes)
    if images:
        notes.append(f"{images} embedded image(s) skipped")


def _xlsx(data: bytes, out: _Text, notes: list[str]) -> None:
    import warnings

    import openpyxl

    warnings.simplefilter("ignore")
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    cells = 0
    for n, ws in enumerate(wb.worksheets):
        if n >= MAX_SHEETS:
            notes.append(f"only the first {MAX_SHEETS} sheets are read")
            break
        out.add(f"--- sheet: {ws.title} ---")
        rows = 0
        for row in ws.iter_rows(max_col=MAX_COLS, values_only=True):
            vals = [_cell(v) for v in row]
            line = _row_line(vals)
            if not line:
                continue
            rows += 1
            cells += len(vals)  # _row_line dropped the empty cells at the end
            if rows > MAX_ROWS:
                notes.append(f"sheet {ws.title!r}: only the first {MAX_ROWS} rows are read")
                break
            if cells > MAX_CELLS:
                notes.append(f"only the first {MAX_CELLS:,} cells are read")
                out.truncated = True
                return
            out.add(line)
    wb.close()


def _pptx(data: bytes, out: _Text, notes: list[str]) -> None:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(io.BytesIO(data))
    pictures = 0

    def shapes(items):
        nonlocal pictures
        for sh in items:
            if sh.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from shapes(sh.shapes)
                continue
            if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
                pictures += 1
            yield sh

    for i, slide in enumerate(prs.slides, 1):
        if i > MAX_SLIDES:
            notes.append(f"only the first {MAX_SLIDES} slides are read")
            break
        out.add(f"--- slide {i} ---")
        for sh in shapes(slide.shapes):
            if getattr(sh, "has_text_frame", False) and sh.has_text_frame:
                for para in sh.text_frame.paragraphs:
                    out.add("".join(r.text for r in para.runs))
            if getattr(sh, "has_table", False) and sh.has_table:
                for r, row in enumerate(sh.table.rows):
                    if r >= MAX_ROWS:
                        break
                    out.add(" | ".join(c.text.replace("\n", " ").strip() for c in list(row.cells)[:MAX_COLS]))
        if slide.has_notes_slide:
            t = slide.notes_slide.notes_text_frame.text if slide.notes_slide.notes_text_frame else ""
            if t.strip():
                out.add("Notes: " + t.strip())
    if pictures:
        notes.append(f"{pictures} embedded image(s) skipped")


def _odf(data: bytes, fmt: str, out: _Text, notes: list[str]) -> None:
    from odf import teletype
    from odf.namespaces import DRAWNS, PRESENTATIONNS, TABLENS, TEXTNS
    from odf.opendocument import load

    doc: Any = load(io.BytesIO(data))
    budget = {"cells": 0}
    pictures = 0

    def attr(el, ns, name, default=None):
        return el.attributes.get((ns, name), default)

    def table(el) -> None:
        name = attr(el, TABLENS, "name", "")
        if fmt == "ods":
            out.add(f"--- sheet: {name} ---")
        rows = 0
        for row in el.getElementsByType(_TableRow):
            cells: list[str] = []
            for c in row.childNodes:
                if c.qname not in ((TABLENS, "table-cell"), (TABLENS, "covered-table-cell")):
                    continue
                v = teletype.extractText(c).replace("\n", " ").strip()
                rep = int(attr(c, TABLENS, "number-columns-repeated", "1") or 1)
                # Empty repeated cells (often thousands, to the sheet's edge) are never expanded.
                rep = min(rep, MAX_REPEAT) if v else min(rep, 1)
                cells.extend([_cell(v)] * rep)
                if len(cells) >= MAX_COLS:
                    cells = cells[:MAX_COLS]
                    break
            line = _row_line(cells)
            if not line:
                continue
            rrep = min(int(attr(row, TABLENS, "number-rows-repeated", "1") or 1), MAX_REPEAT)
            for _ in range(rrep):
                rows += 1
                budget["cells"] += len(cells)
                if rows > MAX_ROWS:
                    notes.append(f"{name or 'a table'}: only the first {MAX_ROWS} rows are read")
                    return
                if budget["cells"] > MAX_CELLS:
                    notes.append(f"only the first {MAX_CELLS:,} cells are read")
                    out.truncated = True
                    raise _Full
                out.add(line)

    def walk(node) -> None:
        nonlocal pictures
        for el in node.childNodes:
            q = getattr(el, "qname", None)
            if q is None:
                continue
            if q in ((TEXTNS, "p"), (TEXTNS, "h")):
                out.add(teletype.extractText(el))
            elif q == (TABLENS, "table"):
                table(el)
            elif q == (DRAWNS, "image"):
                pictures += 1
            elif q == (PRESENTATIONNS, "notes"):
                parts: list[str] = []
                for p in el.getElementsByType(_P):
                    t = teletype.extractText(p).strip()
                    if t:
                        parts.append(t)
                if parts:
                    out.add("Notes: " + " ".join(parts))
            else:
                walk(el)

    from odf.table import TableRow as _TableRow
    from odf.text import P as _P

    if fmt == "odp":
        from odf.draw import Page

        for i, page in enumerate(doc.presentation.getElementsByType(Page), 1):
            if i > MAX_SLIDES:
                notes.append(f"only the first {MAX_SLIDES} slides are read")
                break
            out.add(f"--- slide {i} ---")
            walk(page)
    elif fmt == "ods":
        for n, t in enumerate(el for el in doc.spreadsheet.childNodes if getattr(el, "qname", None) == (TABLENS, "table")):
            if n >= MAX_SHEETS:
                notes.append(f"only the first {MAX_SHEETS} sheets are read")
                break
            table(t)
    else:
        walk(doc.text)
    if pictures:
        notes.append(f"{pictures} embedded image(s) skipped")


def _rtf(data: bytes, out: _Text, notes: list[str]) -> None:
    from striprtf.striprtf import rtf_to_text

    if len(data) > RTF_MAX:
        notes.append(f"only the first {RTF_MAX // 1048576} MB of the file are read")
        data = data[:RTF_MAX]
    for line in rtf_to_text(data.decode("cp1252", "replace"), errors="ignore").splitlines():
        out.add(line)


DOC_FORMATS = ("docx", "xlsx", "pptx", "odt", "ods", "odp", "rtf")


def document(data: bytes, fmt: str) -> dict:
    if fmt not in DOC_FORMATS:
        return {"ok": False, "error": "unknown document format"}
    if fmt != "rtf":
        why = zip_guard(data)
        if why:
            return {"ok": False, "error": why}
    out, notes = _Text(), []
    try:
        if fmt == "docx":
            _docx(data, out, notes)
        elif fmt == "xlsx":
            _xlsx(data, out, notes)
        elif fmt == "pptx":
            _pptx(data, out, notes)
        elif fmt == "rtf":
            _rtf(data, out, notes)
        else:
            _odf(data, fmt, out, notes)
    except _Full:
        pass
    except MemoryError:
        raise
    except Exception as e:  # noqa: BLE001  any parser failure is the file's fault
        if not out.parts:
            return {"ok": False, "error": f"the document could not be read ({type(e).__name__})"}
        notes.append(f"the rest could not be read ({type(e).__name__})")
    if out.truncated:
        notes.append(f"truncated to the first {TEXT_CAP:,} characters")
    return {"ok": True, "text": out.text(), "truncated": out.truncated, "notes": list(dict.fromkeys(notes))}


def main(argv: list[str]) -> int:
    _limit()
    data = sys.stdin.buffer.read()
    try:
        if argv[1] == "image":
            res = image(data, int(argv[2]))
        elif argv[1] == "pdf":
            res = pdf(data, int(argv[2]), int(argv[3]), argv[4] == "1")
        elif argv[1] == "doc":
            res = document(data, argv[2])
        else:
            res = {"ok": False, "error": "unknown kind"}
    except MemoryError:
        res = {"ok": False, "error": "the file needs too much memory to decode"}
    sys.stdout.write(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
