"""CSV import and export.

Import: UTF-8 (with or without a BOM), else Latin-1; the delimiter is detected (comma, semicolon,
tab) unless given; "first row is the header" is on when the first row has no numbers. The preview
shows the first 20 rows and, per column, the detected type with the share of values that convert
("Number · 98%"). Values are read with the column types' text reader (tables/types.py): dates by
the date-order setting and ISO dates, currency symbols and thousands separators stripped (a `$`
in most values sets the column's currency), checkbox words; what does not convert goes to `raw`.

Export: the current view (its filters, sort and visible columns) or the whole table, UTF-8 with an
optional BOM; numbers without grouping, the currency code in the header ("Amount (USD)"), dates in
ISO form, multi-select options joined with "; ", links as their titles.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from fastapi import HTTPException

from mailr.tables import types as ty
from mailr.tables.types import Ctx

DELIMS = {",": "comma", ";": "semicolon", "\t": "tab"}
PREVIEW_ROWS = 20


def decode(data: bytes) -> tuple[str, str]:
    if len(data) > ty.LIMITS["csv_bytes"]:
        raise HTTPException(413, "A CSV file can be at most 5 MB")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


def sniff(text: str) -> str:
    sample = "\n".join(text.splitlines()[:20])
    counts = {d: 0 for d in DELIMS}
    for line in sample.splitlines():
        for d in DELIMS:
            counts[d] += line.count(d)
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else ","


def parse(text: str, delimiter: str | None = None) -> tuple[list[list[str]], str]:
    d = delimiter if delimiter in DELIMS else sniff(text)
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=d) if any(c.strip() for c in r)]
    if len(rows) > ty.LIMITS["rows"] + 1:
        raise HTTPException(422, f"A CSV import can have at most {ty.LIMITS['rows']:,} rows")
    width = max((len(r) for r in rows), default=0)
    if width > ty.LIMITS["columns"]:
        raise HTTPException(422, f"A CSV import can have at most {ty.LIMITS['columns']} columns")
    return [r + [""] * (width - len(r)) for r in rows], d


def header_guess(rows: list[list[str]]) -> bool:
    """The first row is a header when it holds no numbers (and there is a second row)."""
    if len(rows) < 2:
        return False
    return not any(re.search(r"\d", c) for c in rows[0])


def columns_of(rows: list[list[str]], header: bool, ctx: Ctx) -> list[dict[str, Any]]:
    """Per column: a name, the detected type with the share that converts, and a currency."""
    if not rows:
        return []
    width = len(rows[0])
    body = rows[1:] if header else rows
    out = []
    used: list[str] = []
    for i in range(width):
        name = (rows[0][i].strip() if header else "") or f"Column {i + 1}"
        name = name[:ty.LIMITS["column_name"]]
        base, n = name, 2
        while name.lower() in (u.lower() for u in used):
            name = f"{base} {n}"
            n += 1
        used.append(name)
        values = [r[i] for r in body if i < len(r)]
        t, share, cur = ty.detect(values, ctx)
        out.append({"index": i, "name": name, "type": t, "share": round(share, 3), "currency": cur,
                    "label": f"{ty.LABELS[t]} · {round(share * 100)}%"})
    return out


def preview(data: bytes, *, delimiter: str | None, header: bool | None, ctx: Ctx) -> dict[str, Any]:
    text, enc = decode(data)
    rows, d = parse(text, delimiter)
    hdr = header_guess(rows) if header is None else header
    cols = columns_of(rows, hdr, ctx)
    body = rows[1:] if hdr else rows
    return {"encoding": enc, "delimiter": d, "delimiter_name": DELIMS[d], "header": hdr, "columns": cols,
            "rows": body[:PREVIEW_ROWS], "total": len(body)}


# A cell a spreadsheet would read as a formula ("=", "+", "-", "@", or a tab or carriage return
# first), unless it is a plain number such as "-12.5".
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_NUMBER = re.compile(r"[+-]?(?:\d[\d,]*)?(?:\.\d+)?(?:[eE][+-]?\d+)?")


def safe_cell(v: Any) -> Any:
    """A value for a CSV cell that Excel and other spreadsheets open as text, never as a formula: a
    leading apostrophe on text that would start one (OWASP's advice for CSV injection)."""
    if isinstance(v, str) and v.startswith(_FORMULA_START) and not (_NUMBER.fullmatch(v) and any(ch.isdigit() for ch in v)):
        return "'" + v
    return v


def export(columns: list[ty.Col], rows: list[Any], titles: dict[str, str], *, bom: bool = False) -> str:
    buf = io.StringIO()
    if bom:
        buf.write("\ufeff")
    w = csv.writer(buf, lineterminator="\r\n")
    head = []
    for c in columns:
        h = c.name
        if c.type == "currency" and (c.config or {}).get("currency"):
            h = f"{h} ({c.config['currency']})"
        head.append(safe_cell(h))
    w.writerow(head)
    for r in rows:
        line = []
        for c in columns:
            v = (r.cells or {}).get(c.col_id)
            raw = (r.raw or {}).get(c.col_id)
            line.append(safe_cell(raw if (raw and ty.is_empty(v)) else ty.plain(c, v, titles=titles)))
        w.writerow(line)
    return buf.getvalue()
