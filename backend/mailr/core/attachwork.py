"""Decode one attachment in a separate, resource-limited process (core/attachfetch.py runs it).

Images and PDFs from email are untrusted input to C decoders (libjpeg, libpng, libwebp,
PDFium). They are decoded here, in a child process started with `python -I` that lowers its own
address-space and CPU limits first, so a decompression bomb or a decoder crash costs this
process, never the API server. This file imports nothing from mailr on purpose.

    python -I attachwork.py image <max_px>              < bytes   -> JSON on stdout
    python -I attachwork.py pdf <max_px> <page> <render> < bytes   -> JSON on stdout

Output: {"ok": true, ...} or {"ok": false, "error": "..."}. Image data is base64 in `data`.
"""

from __future__ import annotations

import base64
import io
import json
import sys

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


def main(argv: list[str]) -> int:
    _limit()
    data = sys.stdin.buffer.read()
    try:
        if argv[1] == "image":
            res = image(data, int(argv[2]))
        elif argv[1] == "pdf":
            res = pdf(data, int(argv[2]), int(argv[3]), argv[4] == "1")
        else:
            res = {"ok": False, "error": "unknown kind"}
    except MemoryError:
        res = {"ok": False, "error": "the file needs too much memory to decode"}
    sys.stdout.write(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
