"""The checklist item grammar: the input that adds items to a checklist.

One input becomes one or more checklist items: lines and commas split it, a leading bullet or
checkbox is removed, `Name:` and `## Name` pick or make a section, `#List` picks a list (capture
mode), and a quantity at the start or the end becomes `{amount, unit}`. The rules are
deterministic and conservative. The vectors are spec/cases/notes-quickadd-cases.yaml; the
web twin (frontend/src/lib/itemadd.ts) and, later, the Kotlin twin run the same vectors.

The server parses lines that come from the assistant, imports, the capture line and the text of a
note that becomes a checklist (`note.to_checklist`).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any

MODES = ("list", "capture", "edit", "key")

# ---------------------------------------------------------------- units

_UNIT_FORMS: dict[str, tuple[str, ...]] = {
    "g": ("g", "gram", "grams", "gr"),
    "kg": ("kg", "kilo", "kilos", "kilogram", "kilograms"),
    "mg": ("mg",),
    "lb": ("lb", "lbs", "pound", "pounds"),
    "oz": ("oz", "ounce", "ounces"),
    "ml": ("ml", "millilitre", "millilitres", "milliliter", "milliliters"),
    "l": ("l", "litre", "litres", "liter", "liters"),
    "cl": ("cl",),
    "dl": ("dl",),
    "gal": ("gal", "gallon", "gallons"),
    "qt": ("qt", "quart", "quarts"),
    "pt": ("pt", "pint", "pints"),
    "cup": ("cup", "cups"),
    "tbsp": ("tbsp", "tablespoon", "tablespoons"),
    "tsp": ("tsp", "teaspoon", "teaspoons"),
    "dozen": ("dozen", "doz"),
    "pack": ("pack", "packs", "pk"),
    "can": ("can", "cans", "tin", "tins"),
    "bottle": ("bottle", "bottles"),
    "box": ("box", "boxes"),
    "bag": ("bag", "bags"),
    "jar": ("jar", "jars"),
    "bunch": ("bunch", "bunches"),
    "loaf": ("loaf", "loaves"),
    "carton": ("carton", "cartons"),
    "piece": ("piece", "pieces", "pc", "pcs", "ct"),
}
UNITS: dict[str, str] = {form: canon for canon, forms in _UNIT_FORMS.items() for form in forms}
_UNIT_RX = "|".join(sorted((re.escape(u) for u in UNITS), key=len, reverse=True))

_VULGAR = {"½": Fraction(1, 2), "⅓": Fraction(1, 3), "⅔": Fraction(2, 3), "¼": Fraction(1, 4), "¾": Fraction(3, 4),
           "⅕": Fraction(1, 5), "⅖": Fraction(2, 5), "⅗": Fraction(3, 5), "⅘": Fraction(4, 5), "⅙": Fraction(1, 6),
           "⅚": Fraction(5, 6), "⅛": Fraction(1, 8), "⅜": Fraction(3, 8), "⅝": Fraction(5, 8), "⅞": Fraction(7, 8)}
_V = "".join(_VULGAR)
# An amount: a mixed number (1 1/2), an integer with a vulgar fraction (1½), a fraction (1/2), a
# decimal or an integer, or a vulgar fraction alone (½).
AMOUNT = rf"(?:\d+[ \t]+\d+/\d+|\d+[{_V}]|\d+/\d+|\d+(?:\.\d+)?|[{_V}])"

_LEAD_UNIT = re.compile(rf"^(?P<q>(?P<a>{AMOUNT})[ \t]?(?P<u>{_UNIT_RX})(?:[ \t]+of)?)(?=[ \t])[ \t]+(?P<rest>\S.*)$",
                        re.IGNORECASE | re.DOTALL)
_LEAD_X = re.compile(rf"^(?P<q>(?P<a>{AMOUNT})[ \t]?[x×])[ \t]+(?P<rest>\S.*)$", re.IGNORECASE | re.DOTALL)
_LEAD_BARE = re.compile(rf"^(?P<q>(?P<a>{AMOUNT}))[ \t]+(?P<rest>\S.*)$", re.DOTALL)
_TRAIL_X = re.compile(rf"^(?P<rest>.*\S)[ \t]+(?P<q>[x×][ \t]?(?P<a>{AMOUNT}))$", re.IGNORECASE | re.DOTALL)
_TRAIL_UNIT = re.compile(rf"^(?P<rest>.*\S)[ \t]+(?P<q>(?P<a>{AMOUNT})[ \t]?(?P<u>{_UNIT_RX}))$",
                         re.IGNORECASE | re.DOTALL)
_TRAIL_PAREN_UNIT = re.compile(rf"^(?P<rest>.*\S)[ \t]*(?P<q>\([ \t]*(?P<a>{AMOUNT})[ \t]?(?P<u>{_UNIT_RX})[ \t]*\))$",
                               re.IGNORECASE | re.DOTALL)
_TRAIL_PAREN = re.compile(rf"^(?P<rest>.*\S)[ \t]*(?P<q>\([ \t]*(?P<a>{AMOUNT})[ \t]*\))$", re.DOTALL)
# "2 x 4 lumber", "2x4": an x between two numbers turns quantities off for the piece.
_X_BETWEEN = re.compile(r"\d[ \t]*[x×][ \t]*\d", re.IGNORECASE)

# ---------------------------------------------------------------- line tokens

_CHECKBOX = re.compile(r"^(?:[-*•–][ \t]*)?\[(?P<c>[ xX])\][ \t]*")
_CHECKMARK = re.compile(r"^[✓✔][ \t]*")
_BULLET = re.compile(r"^(?:[-*•–]|\d{1,3}[.)])[ \t]+")
_MD_HEADER = re.compile(r"^(?P<tok>#{1,6}[ \t]+(?P<name>[^\n]*\S))[ \t]*$")
_NAME = r"[^\W\d_][^\s:]*(?:[ \t]+[^\s:]+){0,3}"
_COLON_HEADER = re.compile(rf"^(?P<tok>(?P<name>{_NAME}):)[ \t]*$")
_SECTION_PREFIX = re.compile(rf"^(?P<tok>(?P<name>{_NAME}):)(?=[ \t])[ \t]*")
_LIST_TOKEN = re.compile(r"(?<!\S)#(?P<name>[^\W\d_][\w\-/]*)")


@dataclass
class Token:
    kind: str
    text: str
    start: int  # UTF-16 offsets into the input
    end: int

    def out(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "start": self.start, "end": self.end}


@dataclass
class Item:
    text: str
    qty: dict[str, Any] | None = None  # {"amount": "2", "unit": None, "text": "2"}
    section: str | None = None
    checked: bool = False

    def out(self) -> dict[str, Any]:
        return {"text": self.text, "qty": self.qty, "section": self.section, "checked": self.checked}


@dataclass
class Parse:
    items: list[Item] = field(default_factory=list)
    new_sections: list[str] = field(default_factory=list)
    tokens: list[Token] = field(default_factory=list)
    list: dict[str, Any] | None = None  # capture mode: {"id", "name", "create"}

    def out(self) -> dict[str, Any]:
        return {"list": self.list, "items": [i.out() for i in self.items], "new_sections": self.new_sections,
                "tokens": [t.out() for t in self.tokens]}


# ---------------------------------------------------------------- helpers


def _u16(s: str, i: int) -> int:
    """The UTF-16 offset of code point index `i` in `s`."""
    return i + sum(1 for ch in s[:i] if ord(ch) > 0xFFFF)


def _fold(s: str) -> str:
    """Names compared with case and spaces ignored (lists also ignore - and _)."""
    return re.sub(r"[\s_\-/]+", "", unicodedata.normalize("NFKC", s)).casefold()


def amount_value(a: str) -> Fraction:
    a = a.strip()
    m = re.fullmatch(r"(\d+)[ \t]+(\d+)/(\d+)", a)
    if m:
        return int(m.group(1)) + Fraction(int(m.group(2)), max(1, int(m.group(3))))
    m = re.fullmatch(rf"(\d+)([{_V}])", a)
    if m:
        return int(m.group(1)) + _VULGAR[m.group(2)]
    if a in _VULGAR:
        return _VULGAR[a]
    m = re.fullmatch(r"(\d+)/(\d+)", a)
    if m:
        return Fraction(int(m.group(1)), max(1, int(m.group(2))))
    return Fraction(Decimal(a))


def fmt_amount(v: Fraction | Decimal | float | str) -> str:
    """A decimal string with at most 3 decimals: "2", "0.5", "0.333"."""
    if isinstance(v, str):
        v = Decimal(v)
    if isinstance(v, Fraction):
        d = Decimal(v.numerator) / Decimal(v.denominator)
    else:
        d = Decimal(str(v))
    d = d.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    s = format(d.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"[ ,;.]+$", "", s).strip()


def _quoted(s: str) -> bool:
    s = s.strip()
    return len(s) >= 2 and s[0] in "\"“" and s[-1] in "\"”"


def _split(line: str) -> list[tuple[int, str]]:
    """Pieces of a line split at commas and semicolons outside quotes and parentheses, with
    their start offsets."""
    out: list[tuple[int, str]] = []
    depth, quote, start = 0, False, 0
    for i, ch in enumerate(line):
        if ch in "\"“”":
            quote = not quote if ch == "\"" else ch == "“"
        elif quote:
            continue
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch in ",;" and depth == 0:
            out.append((start, line[start:i]))
            start = i + 1
    out.append((start, line[start:]))
    return out


def parse_qty(piece: str, keep: set[str] | frozenset[str] = frozenset()) -> tuple[str, dict[str, Any] | None,
                                                                                    tuple[int, int] | None]:
    """The item text and quantity of one piece, and the quantity token's span in `piece`."""
    if _quoted(piece) or _X_BETWEEN.search(piece):
        return piece, None, None
    for rx, has_unit in ((_LEAD_UNIT, True), (_LEAD_X, False), (_LEAD_BARE, False), (_TRAIL_X, False),
                         (_TRAIL_PAREN_UNIT, True), (_TRAIL_UNIT, True), (_TRAIL_PAREN, False)):
        m = rx.match(piece)
        if not m:
            continue
        a = m.group("a")
        rest = m.group("rest")
        if rx is _LEAD_BARE:
            if re.fullmatch(r"\d+", a) and int(a) >= 100:
                continue  # "2026 planner", "150 napkins"
            if rest.startswith("%"):
                continue
        if not _clean(rest):
            continue  # "500 g", "2": no item text would be left
        if m.group("q").strip() in keep:
            return piece, None, None
        unit = UNITS[m.group("u").lower()] if has_unit else None
        qty = {"amount": fmt_amount(amount_value(a)), "unit": unit, "text": m.group("q").strip()}
        return rest, qty, m.span("q")
    return piece, None, None


def item_key(text: str) -> str:
    """The item key: NFKC, lower case, accents removed, punctuation at the
    ends removed, single spaces, and the last word's plural folded."""
    s = unicodedata.normalize("NFKC", text or "").lower()
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if not unicodedata.combining(ch))
    s = unicodedata.normalize("NFC", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^[^\w]+|[^\w]+$", "", s)
    if not s:
        return ""
    words = s.split(" ")
    words[-1] = _singular(words[-1])
    return " ".join(words)


def _singular(w: str) -> str:
    if w.endswith("ies") and len(w) >= 5:
        return w[:-3] + "y"
    if w.endswith(("xes", "ches", "shes", "sses")):
        return w[:-2]
    if w.endswith("oes") and len(w) >= 7:
        return w[:-2]
    if w.endswith("s") and len(w) >= 4 and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def resolve_section(name: str, sections: list[str], *, header: bool = False) -> str | None:
    """A typed section name against the list's sections: exact (case and spaces ignored), else
    the only one that starts with it when it has at least 3 characters."""
    f = _fold(name)
    if not f:
        return None
    for s in sections:
        if _fold(s) == f:
            return s
    if len(f) >= 3:
        hits = [s for s in sections if _fold(s).startswith(f)]
        if len(hits) == 1:
            return hits[0]
    return None


def resolve_list(name: str, lists: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A `#name` against the user's lists: exact first, else names starting with it, shortest first."""
    f = _fold(name)
    for lst in lists:
        if _fold(lst.get("name") or "") == f:
            return lst
    hits = sorted((lst for lst in lists if _fold(lst.get("name") or "").startswith(f)),
                  key=lambda x: len(_fold(x.get("name") or "")))
    return hits[0] if hits else None


# ---------------------------------------------------------------- the parser


def parse(text: str, mode: str = "list", *, sections: list[str] | None = None,
          lists: list[dict[str, Any]] | None = None, default_list: str | None = None,
          keep: list[str] | None = None, prefix_creates: bool = False) -> Parse:
    """Parse `text`. `sections`: the list's section names (list and edit modes). Capture mode:
    `lists` ([{id, name, sections}], the lists the user may edit) and `default_list` (an id).
    `keep`: token texts the user turned back into text. `prefix_creates` (list mode): an unknown
    `Name:` prefix makes that section, as a header line does (the new-list composer)."""
    if mode not in ("list", "capture", "edit"):
        raise ValueError(f"unknown mode {mode!r}")
    keepset = frozenset(k.strip() for k in keep or [])
    res = Parse()
    src = text or ""
    known = list(sections or [])
    removed: list[tuple[int, int]] = []

    if mode == "capture":
        lists = lists or []
        chosen = None
        m = _LIST_TOKEN.search(src)
        if m and m.group(0) not in keepset:
            name = m.group("name")
            found = resolve_list(name, lists)
            chosen = ({"id": found.get("id"), "name": found.get("name"), "create": False} if found
                      else {"id": None, "name": name, "create": True})
            res.tokens.append(Token("list", m.group(0), _u16(src, m.start()), _u16(src, m.end())))
            removed.append(m.span())
            known = list(found.get("sections") or []) if found else []
        if chosen is None:
            found = next((x for x in lists if x.get("id") == default_list), None)
            if found is not None:
                chosen = {"id": found.get("id"), "name": found.get("name"), "create": False}
                known = list(found.get("sections") or [])
        res.list = chosen

    if mode == "edit":
        res.items.extend(_pieces(src, 0, src, None, False, keepset, res, split=False))
        return res

    current: str | None = None
    pos = 0
    for raw in src.split("\n"):
        line_start = pos
        pos += len(raw) + 1
        line = raw
        for a, b in removed:  # blank out the list token, keeping offsets
            if line_start <= a < line_start + len(raw):
                line = line[: a - line_start] + " " * (b - a) + line[b - line_start:]
        lead = len(line) - len(line.lstrip())
        body = line.strip()
        if not body:
            continue
        off = line_start + lead
        # A header line: `Name:` or `## Name`, taken even when the section is new.
        hm = _MD_HEADER.match(body) or _COLON_HEADER.match(body)
        if hm:
            name = hm.group("name").strip()
            canon = resolve_section(name, known)
            if canon is None:
                canon = name[:100]
                known.append(canon)
                res.new_sections.append(canon)
            current = canon
            res.tokens.append(Token("section", hm.group("tok"), _u16(src, off), _u16(src, off + len(hm.group("tok")))))
            continue
        # A checkbox or a bullet.
        checked = False
        cm = _CHECKBOX.match(body)
        if cm:
            checked = cm.group("c") in "xX"
            if checked:
                tok = cm.group(0).rstrip()
                res.tokens.append(Token("check", tok, _u16(src, off), _u16(src, off + len(tok))))
            body, off = body[cm.end():], off + cm.end()
        else:
            km = _CHECKMARK.match(body)
            if km:
                checked = True
                tok = km.group(0).rstrip()
                res.tokens.append(Token("check", tok, _u16(src, off), _u16(src, off + len(tok))))
                body, off = body[km.end():], off + km.end()
            else:
                bm = _BULLET.match(body)
                if bm:
                    body, off = body[bm.end():], off + bm.end()
        # A section prefix for this line only, when the list has that section.
        section = current
        sm = _SECTION_PREFIX.match(body)
        if sm:
            canon = resolve_section(sm.group("name"), known)
            if canon is None and prefix_creates and mode == "list" and sm.group("tok") not in keepset:
                canon = sm.group("name").strip()[:100]
                known.append(canon)
                res.new_sections.append(canon)
            if canon is not None:
                section = canon
                tok = sm.group("tok")
                res.tokens.append(Token("section", tok, _u16(src, off), _u16(src, off + len(tok))))
                body, off = body[sm.end():], off + sm.end()
        res.items.extend(_pieces(body, off, src, section, checked, keepset, res, split=True))
    return res


def _pieces(body: str, off: int, src: str, section: str | None, checked: bool, keep: frozenset[str],
            res: Parse, *, split: bool) -> list[Item]:
    out: list[Item] = []
    for start, piece in (_split(body) if split else [(0, body)]):
        lead = len(piece) - len(piece.lstrip())
        p = piece.strip()
        p = re.sub(r"[ ,;.]+$", "", p) if split else p.rstrip()
        if not p:
            continue
        p_off = off + start + lead
        rest, qty, span = parse_qty(p, keep)
        text = _clean(rest)
        if not text:
            continue
        if qty is not None and span is not None:
            res.tokens.append(Token("qty", qty["text"], _u16(src, p_off + span[0]),
                                    _u16(src, p_off + span[0] + len(qty["text"]))))
        out.append(Item(text=text[:1000], qty=qty, section=section, checked=checked))
    return out


def qty_text(qty: dict[str, Any] | None) -> str:
    """The chip text of a quantity: "2", "500 g", "1.5 kg"."""
    if not qty:
        return ""
    a = qty.get("amount")
    u = qty.get("unit")
    return f"{a} {u}" if u else f"{a}"


def line_for(text: str, qty: dict[str, Any] | None) -> str:
    """An item as one line the grammar parses back to the same item: `milk ×2`, `500 g flour`."""
    if not qty:
        return text
    if qty.get("unit"):
        return f"{qty['amount']} {qty['unit']} {text}"
    return f"{text} ×{qty['amount']}"


# ---------------------------------------------------------------- quick note

_QUICK_LIST = re.compile(r"\s*#(?P<name>[^\W\d_][\w\-/]*)")


def quick(text: str, lists: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """"New note…" from anywhere: text that starts with a `#List` token adds items to that list
    (capture mode: `#Shopping Costco: milk` puts milk in the Costco section of Shopping; an unknown
    name makes the list), anything else is a text note (the first line its title, the rest its
    text). A `#name` later in the text stays text, so a note may mention a hashtag.

    {"kind": "items", "list", "items", "new_sections", "tokens"} or {"kind": "note", "title", "body"}."""
    src = text or ""
    if _QUICK_LIST.match(src):
        res = parse(src, "capture", lists=lists or [])
        return {"kind": "items", **res.out()}
    lines = [ln.strip() for ln in src.strip().split("\n")]
    return {"kind": "note", "title": (lines[0] if lines else "")[:500], "body": "\n".join(lines[1:]).strip()}
