"""Locate a quoted phrase in a message and mark it.

`locate` finds a quote in plain text (Message.body_text) and returns exact offsets; it is how
citations are verified. `mark_html` finds the same quote in the sanitised HTML body (whose
text differs from body_text in whitespace and markup) and wraps it in
<mark class="mailr-hl">, spanning several text nodes when the quote crosses tags.

Matching is exact after normalisation (case, whitespace, curly quotes, dashes); if that
fails, a fuzzy alignment (rapidfuzz) accepts near-verbatim quotes at ≥ FUZZY_MIN.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString
from bs4.element import Comment
from rapidfuzz import fuzz

FUZZY_MIN = 90.0
_TRANS = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"',
                        "„": '"', "–": "-", "—": "-", "−": "-", " ": " ", "…": "..."})
_SKIP = {"style", "script", "title", "head"}


@dataclass
class Match:
    start: int
    end: int
    score: float  # 100 = exact after normalisation


def _norm_map(s: str) -> tuple[str, list[int]]:
    """Normalised text plus, for each normalised char, its index in `s`. Whitespace runs
    collapse to one space."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = True
    for i, ch in enumerate(s):
        c = unicodedata.normalize("NFKC", ch).translate(_TRANS).lower()
        for cc in c:
            if cc.isspace():
                if prev_space:
                    continue
                out.append(" ")
                idx.append(i)
                prev_space = True
            else:
                out.append(cc)
                idx.append(i)
                prev_space = False
    return "".join(out), idx


def normalize(s: str) -> str:
    return _norm_map(s)[0].strip()


def _find(hay: str, needle: str) -> tuple[int, int, float] | None:
    if not needle:
        return None
    i = hay.find(needle)
    if i >= 0:
        return i, i + len(needle), 100.0
    if len(needle) < 12:
        return None  # too short for a trustworthy fuzzy match
    al = fuzz.partial_ratio_alignment(needle, hay, score_cutoff=FUZZY_MIN)
    if al is None:
        return None
    return al.dest_start, al.dest_end, float(al.score)


_ELLIPSIS = re.compile(r"\s*\.{3,}\s*")


def locate(text: str, quote: str) -> Match | None:
    """Offsets of `quote` in `text` (original indices), or None.

    A quote that elides text ("Jokes on me... a joke that I'm the punch line") matches when
    every fragment appears in order, close together; the match spans first to last fragment
    and scores just under 100, so callers show the real text rather than the model's."""
    hay, idx = _norm_map(text or "")
    needle = normalize(quote or "")
    if not idx:
        return None
    f = _find(hay, needle)
    if f is None and "..." in needle:
        return _locate_elided(hay, idx, needle)
    if f is None:
        return None
    a, b, score = f
    b = max(a + 1, b)
    return Match(idx[a], idx[min(b, len(idx)) - 1] + 1, score)


def _locate_elided(hay: str, idx: list[int], needle: str) -> Match | None:
    frags = [x.strip(" \"'") for x in _ELLIPSIS.split(needle)]
    frags = [x for x in frags if len(x) >= 3]
    if not frags:
        return None
    first = last = None
    pos, worst = 0, 100.0
    for fr in frags:
        f = _find(hay[pos:], fr)
        if f is None:
            return None
        a, b, score = f
        a, b = a + pos, max(a + pos + 1, b + pos)
        if first is None:
            first = a
        elif a - pos > 400:  # fragments must be near each other
            return None
        last, pos, worst = b, b, min(worst, score)
    return Match(idx[first], idx[min(last, len(idx)) - 1] + 1, min(worst, 99.0))


def mark_html(html: str, quote: str, mark_id: str = "mailr-hl-1") -> tuple[str, bool]:
    """Wrap the first occurrence of `quote` in the HTML's visible text with <mark>. Returns
    (html, found). Never changes the document when the quote is not found."""
    if not quote or not html:
        return html, False
    soup = BeautifulSoup(html, "html.parser")
    nodes: list[NavigableString] = []
    for t in soup.find_all(string=True):
        if isinstance(t, Comment) or (t.parent and t.parent.name in _SKIP):
            continue
        nodes.append(t)
    # Concatenate visible text; a space between nodes so words in adjacent blocks do not fuse.
    full, owner, local = [], [], []
    for ni, node in enumerate(nodes):
        s = str(node)
        for ci, ch in enumerate(s):
            full.append(ch)
            owner.append(ni)
            local.append(ci)
        full.append(" ")
        owner.append(ni)
        local.append(len(s))
    text = "".join(full)
    m = locate(text, quote)
    if m is None:
        return html, False
    # Per node, the [a, b) character range to wrap.
    spans: dict[int, list[int]] = {}
    for k in range(m.start, m.end):
        ni, ci = owner[k], local[k]
        if ci >= len(str(nodes[ni])):
            continue  # the synthetic separator
        span = spans.setdefault(ni, [ci, ci + 1])
        span[0] = min(span[0], ci)
        span[1] = max(span[1], ci + 1)
    first = True
    for ni in sorted(spans):
        node = nodes[ni]
        s = str(node)
        a, b = spans[ni]
        if not s[a:b].strip():
            continue
        mark = soup.new_tag("mark")
        mark["class"] = "mailr-hl"
        if first:
            mark["id"] = mark_id
            first = False
        mark.string = s[a:b]
        pieces = [NavigableString(s[:a]), mark, NavigableString(s[b:])]
        node.replace_with(*[p for p in pieces if not (isinstance(p, NavigableString) and not str(p))])
    return str(soup), True


_WORDS = re.compile(r"\S+")


def leading_words(text: str, n: int = 12) -> str:
    return " ".join(_WORDS.findall(text or "")[:n])
