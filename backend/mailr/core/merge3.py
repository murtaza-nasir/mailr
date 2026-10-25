"""Three-way merge for live editing.

Two writers changed the same item from one base version: the user typing in an editor and the
assistant (or an agent, or another device) writing through the API. `merge_text` merges two
edited copies of a text against their common base, `merge_fields` merges two edited copies of a
structured item (a draft, an event, a task) field by field.

The same algorithm runs in the web app (frontend/src/lib/live/merge3.ts) and on Android
(LiveMerge.kt). The three implementations share their test cases
(backend/tests/fixtures/merge3_cases.json), so a merge gives the same result everywhere.

Text is split into units:

- "lines": after every line break (plain text: task notes, event descriptions, notes);
- "html": after every closing block tag and every <br> (a draft's body). Units compare by a
  normalised key (div and p alike, <br/> and <br> alike, class / style / dir attributes and
  whitespace runs ignored), so an editor that re-serialises the same text does not count as a
  change. Where a unit is unchanged, the result keeps the "mine" spelling.

The diff is a patience diff: units that occur once in both copies anchor the match, the gaps
between anchors are matched recursively, and a small gap without anchors by a longest common
subsequence table. Changes from the two sides merge when their base ranges do not overlap. An
insertion at the edge of the other side's change is not an overlap (it goes before it). Two
changes to the same units conflict, unless both made the same change.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# Gaps larger than this (units in one copy times units in the other) without a unique anchor
# are not matched unit by unit: the whole gap is one change.
DP_LIMIT = 250_000

_WS = "[ \\t\\r\\n\\f]"
_HTML_SPLIT = re.compile(r"</(?:p|div|li|ul|ol|h[1-6]|blockquote|pre|table|tr)>|<br" + _WS + r"*/?>|\n", re.I)
_K_NBSP = re.compile(r"&nbsp;", re.I)
_K_DIV = re.compile(r"<(/?)div\b", re.I)
_K_BR = re.compile(r"<br" + _WS + r"*/?>", re.I)
_K_ATTR = re.compile(_WS + r"(?:class|style|dir)=\"[^\"]*\"", re.I)
_K_SPACE = re.compile(_WS + "+")


def tokens(text: str, mode: str = "lines") -> list[str]:
    """The units of `text`; joined, they give `text` back."""
    if not text:
        return []
    out: list[str] = []
    start = 0
    if mode == "html":
        for m in _HTML_SPLIT.finditer(text):
            out.append(text[start:m.end()])
            start = m.end()
    else:
        while True:
            i = text.find("\n", start)
            if i < 0:
                break
            out.append(text[start:i + 1])
            start = i + 1
    if start < len(text):
        out.append(text[start:])
    return out


def key(unit: str, mode: str = "lines") -> str:
    if mode != "html":
        return unit
    s = _K_NBSP.sub(" ", unit)
    s = _K_DIV.sub(lambda m: "<" + m.group(1) + "p", s)
    s = _K_BR.sub("<br>", s)
    s = _K_ATTR.sub("", s)
    return _K_SPACE.sub(" ", s).strip(" ")


# ---------------------------------------------------------------- diff


def _unique_lcs(a: list[str], a0: int, a1: int, b: list[str], b0: int, b1: int) -> list[tuple[int, int]]:
    """The longest increasing run of units that occur exactly once in a[a0:a1] and in b[b0:b1]."""
    ca: dict[str, int] = {}
    cb: dict[str, int] = {}
    pb: dict[str, int] = {}
    for i in range(a0, a1):
        ca[a[i]] = ca.get(a[i], 0) + 1
    for j in range(b0, b1):
        cb[b[j]] = cb.get(b[j], 0) + 1
        pb[b[j]] = j
    pairs = [(i, pb[a[i]]) for i in range(a0, a1) if ca[a[i]] == 1 and cb.get(a[i]) == 1]
    if not pairs:
        return []
    # Patience: piles by the b index, each element remembers the top of the pile to its left.
    tops: list[int] = []  # index into pairs of each pile's top
    back: list[int] = [-1] * len(pairs)
    for k, (_, j) in enumerate(pairs):
        lo, hi = 0, len(tops)
        while lo < hi:
            mid = (lo + hi) // 2
            if pairs[tops[mid]][1] < j:
                lo = mid + 1
            else:
                hi = mid
        back[k] = tops[lo - 1] if lo > 0 else -1
        if lo == len(tops):
            tops.append(k)
        else:
            tops[lo] = k
    out: list[tuple[int, int]] = []
    k = tops[-1]
    while k >= 0:
        out.append(pairs[k])
        k = back[k]
    out.reverse()
    return out


def _dp(a: list[str], a0: int, a1: int, b: list[str], b0: int, b1: int, out: list[tuple[int, int]]) -> None:
    n, m = a1 - a0, b1 - b0
    w = m + 1
    L = [0] * ((n + 1) * w)
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if a[a0 + i] == b[b0 + j]:
                L[i * w + j] = L[(i + 1) * w + j + 1] + 1
            else:
                x, y = L[(i + 1) * w + j], L[i * w + j + 1]
                L[i * w + j] = x if x >= y else y
    i = j = 0
    while i < n and j < m:
        if a[a0 + i] == b[b0 + j]:
            out.append((a0 + i, b0 + j))
            i += 1
            j += 1
        elif L[(i + 1) * w + j] >= L[i * w + j + 1]:
            i += 1
        else:
            j += 1


def _match(a: list[str], a0: int, a1: int, b: list[str], b0: int, b1: int, out: list[tuple[int, int]]) -> None:
    while a0 < a1 and b0 < b1 and a[a0] == b[b0]:
        out.append((a0, b0))
        a0 += 1
        b0 += 1
    suffix: list[tuple[int, int]] = []
    while a1 > a0 and b1 > b0 and a[a1 - 1] == b[b1 - 1]:
        a1 -= 1
        b1 -= 1
        suffix.append((a1, b1))
    if a0 < a1 and b0 < b1:
        anchors = _unique_lcs(a, a0, a1, b, b0, b1)
        if anchors:
            pa, pb = a0, b0
            for i, j in anchors:
                _match(a, pa, i, b, pb, j, out)
                out.append((i, j))
                pa, pb = i + 1, j + 1
            _match(a, pa, a1, b, pb, b1, out)
        elif (a1 - a0) * (b1 - b0) <= DP_LIMIT:
            _dp(a, a0, a1, b, b0, b1, out)
    out.extend(reversed(suffix))


def diff(a: list[str], b: list[str]) -> list[tuple[int, int, int, int]]:
    """Hunks (a0, a1, b0, b1): a[a0:a1] became b[b0:b1]. In order, never overlapping."""
    matches: list[tuple[int, int]] = []
    _match(a, 0, len(a), b, 0, len(b), matches)
    hunks = []
    i = j = 0
    for mi, mj in [*matches, (len(a), len(b))]:
        if mi > i or mj > j:
            hunks.append((i, mi, j, mj))
        i, j = mi + 1, mj + 1
    return hunks


# ---------------------------------------------------------------- merge


@dataclass
class Conflict:
    base: str
    mine: str
    theirs: str

    def out(self) -> dict[str, str]:
        return {"base": self.base, "mine": self.mine, "theirs": self.theirs}


@dataclass
class TextMerge:
    ok: bool
    text: str  # the merged text; where a conflict is, the "mine" side
    conflicts: list[Conflict] = field(default_factory=list)


def _overlap(x: tuple[int, int, int, int], y: tuple[int, int, int, int]) -> bool:
    (x0, x1), (y0, y1) = x[:2], y[:2]
    if x0 == x1 and y0 == y1:
        return x0 == y0
    if x0 == x1:
        return y0 < x0 < y1
    if y0 == y1:
        return x0 < y0 < x1
    return max(x0, y0) < min(x1, y1)


def merge_text(base: str | None, mine: str | None, theirs: str | None, mode: str = "lines") -> TextMerge:
    base, mine, theirs = base or "", mine or "", theirs or ""
    if mine == theirs or theirs == base:
        return TextMerge(True, mine)
    if mine == base:
        return TextMerge(True, theirs)
    tb, tm, tt = tokens(base, mode), tokens(mine, mode), tokens(theirs, mode)
    kb, km, kt = ([key(u, mode) for u in t] for t in (tb, tm, tt))
    hunks = [(*h, 0) for h in diff(kb, km)] + [(*h, 1) for h in diff(kb, kt)]
    hunks.sort(key=lambda h: (h[0], h[1], h[4]))
    groups: list[list[tuple[int, int, int, int, int]]] = []
    for h in hunks:
        if groups and any(g[4] != h[4] and _overlap(g, h) for g in groups[-1]):
            groups[-1].append(h)
        else:
            groups.append([h])
    out: list[str] = []
    conflicts: list[Conflict] = []
    pos = 0
    dm = dt = 0  # mine / theirs index minus base index, outside the hunks seen so far
    for g in groups:
        g0 = min(h[0] for h in g)
        g1 = max(h[1] for h in g)
        out.extend(tm[k + dm] for k in range(pos, g0))
        em = dm + sum((h[3] - h[2]) - (h[1] - h[0]) for h in g if h[4] == 0)
        et = dt + sum((h[3] - h[2]) - (h[1] - h[0]) for h in g if h[4] == 1)
        m0, m1, t0, t1 = g0 + dm, g1 + em, g0 + dt, g1 + et
        sides = {h[4] for h in g}
        if sides == {0}:
            out.extend(tm[m0:m1])
        elif sides == {1}:
            out.extend(tt[t0:t1])
        elif km[m0:m1] == kt[t0:t1]:
            out.extend(tm[m0:m1])
        else:
            conflicts.append(Conflict("".join(tb[g0:g1]), "".join(tm[m0:m1]), "".join(tt[t0:t1])))
            out.extend(tm[m0:m1])
        pos, dm, dt = g1, em, et
    out.extend(tm[k + dm] for k in range(pos, len(tb)))
    return TextMerge(not conflicts, "".join(out), conflicts)


def _canon(v: Any) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)


def same(a: Any, b: Any) -> bool:
    """Equal values; a missing value, None and "" are the same, and so are None and []."""
    def norm(v: Any) -> Any:
        return None if v in ("", []) else v
    return _canon(norm(a)) == _canon(norm(b))


@dataclass
class FieldMerge:
    ok: bool
    merged: dict[str, Any]  # where a field conflicts, the "mine" value
    conflicts: list[str] = field(default_factory=list)  # the fields both sides changed differently
    texts: dict[str, list[Conflict]] = field(default_factory=dict)


def merge_fields(base: dict[str, Any] | None, mine: dict[str, Any], theirs: dict[str, Any],
                 text_fields: dict[str, str] | None = None) -> FieldMerge:
    """Per field: one side's change wins over an unchanged field; equal changes agree; text
    fields named in `text_fields` (field -> "lines" | "html") merge by units; any other field
    both sides changed differently is a conflict."""
    base = base or {}
    text_fields = text_fields or {}
    merged: dict[str, Any] = {}
    conflicts: list[str] = []
    texts: dict[str, list[Conflict]] = {}
    for k in list(dict.fromkeys([*base, *mine, *theirs])):
        b, m, t = base.get(k), mine.get(k), theirs.get(k)
        if same(m, b):
            v = t if (k in theirs or not same(t, m)) else m
        elif same(t, b) or same(m, t):
            v = m
        elif k in text_fields and all(isinstance(x, (str, type(None))) for x in (b, m, t)):
            r = merge_text(b, m, t, text_fields[k])
            v = r.text
            if not r.ok:
                conflicts.append(k)
                texts[k] = r.conflicts
        else:
            conflicts.append(k)
            v = m
        if k in mine or k in theirs:
            merged[k] = v
    return FieldMerge(not conflicts, merged, conflicts, texts)
