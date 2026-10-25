"""The citation marker grammar, without the database (spec/cases/citation-cases.yaml).

Models cite with markers such as `[[m:5714 "quote"]]` or `[[event:<uid>@<occurrence>]]`
(intel/citations.py lists them). Models do not always write them exactly: they add a title
(`[[event:<uid>@<occ> "Chinatown walk"]]`), spaces (`[[ event : x @ 2026-05-20 ]]`) or a capital
(`[[Event:…]]`). The parser accepts those. A marker that names a kind but cannot be read never
shows as raw `[[…]]`: it becomes its quoted text, or nothing.

The web (frontend/src/features/rail/assistant/citeGrammar.ts) and Android
(ui/present/CiteGrammar.kt) have twins of `parse` and `clean`; all three run the vectors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Kinds and the longer names models sometimes write.
KINDS = {"m": "m", "message": "m", "msg": "m", "p": "p", "passage": "p", "c": "c", "conversation": "c",
         "thread": "c", "r": "r", "recording": "r", "event": "event", "task": "task", "web": "web", "note": "note"}
# Tables: [[table:<id>]] and [[table:<id>#<row_id>]].
KINDS["table"] = "table"
_TABLE_ID = re.compile(r"^t[0-9a-z]{6,15}$")
_ROW_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
QUOTES = "\"“”"
# An event occurrence: an ISO date or instant at the end of the UID ("…@2026-05-20T13:00:00+00:00").
OCC = r"\d{4}-?\d{2}-?\d{2}(?:T\d{2}:?\d{2}(?::?\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
_OCC_TAIL = re.compile(r"^(.+?)@(" + OCC + r")$")
_REC = re.compile(r"^(\d{1,9})(?:@(\d{1,2}:\d{2}(?::\d{2})?|\d{1,6}(?:\.\d+)?))?")
_KIND = re.compile(r"^\s*([A-Za-z]+)\s*:\s*")
# A note: its public id (letters and digits), and after "#" one of its items.
_NOTE = re.compile(r"^([A-Za-z0-9]{2,40})(?:#([A-Za-z0-9-]{1,64}))?$")
# One marker: `[[body]]` (no newline, at most 500 characters), or a legacy single-bracket form
# (`[c:12]`, `[event:uid]`, `[task:uid]`) that is not the text of a Markdown link. The spaces
# before it are captured, so a marker that disappears takes them along.
MARKER = re.compile(r"(?P<sp>[ \t]*)(?:\[\[(?P<body>(?:(?!\]\]|\[\[)[^\n]){0,500})\]\]"
                    r"|\[(?P<legacy>c:\d{1,12}|(?:event|task):[^\]\s]{1,300})\](?!\())")
# A marker left open at the end of the text (`[[m:12 "quo`): never shown.
OPEN = re.compile(r"[ \t]*\[\[\s*[A-Za-z]+\s*:[^\]\n]{0,500}$")
# While streaming, anything that may still become a marker: `[`, `[[` and what follows them.
PARTIAL = re.compile(r"[ \t]*\[\[?[^\]\n]{0,500}$")


@dataclass(frozen=True)
class Marker:
    kind: str  # m, p, c, r, event, task, web, note, table
    id: str
    at: str | None = None  # an event's occurrence, a recording's moment, a note's item or a table's row
    quote: str | None = None  # a message quote, or the title a model added


def looks_like_marker(body: str) -> bool:
    """`[[kind:…]]` with a kind-like word: ours. `[[1,2],[3,4]]` and wiki links are not."""
    return bool(_KIND.match(body))


def parse(body: str) -> Marker | None:
    """The marker in `body` (the text between `[[` and `]]`), or None when it cannot be read."""
    m = _KIND.match(body)
    if not m:
        return None
    kind = KINDS.get(m.group(1).lower())
    if kind is None:
        return None
    rest = body[m.end():]
    qi = next((i for i, ch in enumerate(rest) if ch in QUOTES), -1)
    quote = None
    head = rest
    if qi >= 0:
        last = max(rest.rfind(q) for q in QUOTES)
        quote = rest[qi + 1:last] if last > qi else rest[qi + 1:]
        quote = quote.strip() or None
        head = rest[:qi]
    head = re.sub(r"\s*@\s*", "@", head).strip()
    head = re.sub(r"[\s|,]+$", "", head)
    if kind in ("m", "p", "c"):
        d = re.match(r"^(\d{1,12})(?![\w@])", head)
        return Marker(kind, d.group(1), None, quote) if d else None
    if kind == "r":
        d = _REC.match(head)
        if not d or (len(head) > d.end() and not head[d.end()].isspace()):
            return None
        return Marker("r", d.group(1), d.group(2), quote)
    if kind == "table":
        return _table(head, quote)
    token = head.split()[0] if head.split() else ""
    if not token or "[" in token or "]" in token:
        return None
    if kind == "web":
        return Marker("web", token, None, quote) if re.match(r"(?i)^https?://[^/\s]", token) else None
    if kind == "note":
        n = _NOTE.match(token)
        return Marker("note", n.group(1), n.group(2), quote) if n else None
    if kind == "event":
        o = _OCC_TAIL.match(token)
        if o:
            return Marker("event", o.group(1), o.group(2), quote)
    return Marker(kind, token, None, quote)


def _table(head: str, quote: str | None) -> Marker | None:
    """A table (`t…`) or one of its rows (`t…#<uuid>`); spaces around "#" are dropped."""
    parts = re.sub(r"\s*#\s*", "#", head).split()
    token = parts[0] if parts else ""
    pid, _, row = token.partition("#")
    if not _TABLE_ID.match(pid) or (row and not _ROW_ID.match(row)) or (token.endswith("#") and not row):
        return None
    return Marker("table", pid, row.lower() or None, quote)


def legacy(text: str) -> Marker | None:
    """`c:12`, `event:uid@occ` or `task:uid` from a single-bracket marker."""
    return parse(text)


def fallback(body: str) -> str:
    """What a marker that cannot be read (or cited) shows: its quoted text, else nothing."""
    qi = next((i for i, ch in enumerate(body) if ch in QUOTES), -1)
    if qi < 0:
        return ""
    last = max(body.rfind(q) for q in QUOTES)
    return (body[qi + 1:last] if last > qi else body[qi + 1:]).strip()


def scan(text: str) -> list[Marker]:
    """Every marker in `text` that can be read, in order (the vectors' `markers`)."""
    out = []
    for mt in MARKER.finditer(text or ""):
        if mt.group("legacy"):
            mk = legacy(mt.group("legacy"))
        elif looks_like_marker(mt.group("body")):
            mk = parse(mt.group("body"))
        else:
            mk = None
        if mk is not None:
            out.append(mk)
    return out


def clean(text: str, streaming: bool = False) -> str:
    """What a reader sees where markers were not turned into numbered links: markers that can be
    read are left out (with the spaces before them), markers that cannot become their quoted text,
    `[[…]]` that is not a marker stays, and a marker left open at the end is dropped. While
    streaming, a `[` or `[[` at the end is hidden too: it may still become a marker."""
    def sub(mt: re.Match) -> str:
        if mt.group("legacy"):
            return ""
        body = mt.group("body")
        if not looks_like_marker(body):
            return mt.group(0)
        if parse(body) is not None:
            return ""
        fb = fallback(body)
        return mt.group("sp") + fb if fb else ""

    out = OPEN.sub("", MARKER.sub(sub, text or ""))
    if streaming:
        out = PARTIAL.sub("", out)
    return out
