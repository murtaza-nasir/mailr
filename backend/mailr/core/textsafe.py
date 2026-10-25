"""Display-safe text from mail headers: names, subjects and file names a sender chose.

Bidirectional controls (U+202A-202E, U+2066-2069, U+200E/F, U+061C) can reverse the text
around them, so a display name can make the real address beside it read backwards. Zero-width
spaces and other invisible format characters hide inside words ("Pay<ZWSP>Pal"). Both are removed;
NUL is removed and other C0/C1 controls become spaces.
"""

from __future__ import annotations

import re
import unicodedata

BIDI = "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200e\u200f\u061c"
# Zero-width joiners (U+200C/D) stay in shown text: emoji sequences and Persian and Indic
# scripts need them. skeleton() drops them for matching.
INVISIBLE = "\u200b\u2060\u2061\u2062\u2063\u2064\ufeff\u180e\u00ad\u034f\u115f\u1160\u3164\uffa0"
JOINERS = "\u200c\u200d"
_DROP = re.compile(f"[{re.escape(BIDI + INVISIBLE)}]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_WS = re.compile(r"[\t\r\n]+")


def clean(s: str | None) -> str:
    """The text without bidi controls, invisible characters or control characters."""
    if not s:
        return s or ""
    s = _DROP.sub("", s).replace("\x00", "")
    s = _CTRL.sub(" ", s)
    return _WS.sub(" ", s)


_ADDR_DROP = re.compile(f"[{re.escape(BIDI + INVISIBLE + JOINERS)}\\s\\x00-\\x1f\\x7f-\\x9f]")


def clean_address(s: str | None) -> str:
    """An email address without bidi controls, invisible characters, joiners, control characters or
    white space: an address has no use for any of them, and they make it read as another one."""
    return _ADDR_DROP.sub("", s or "")


def skeleton(s: str) -> str:
    """A rough confusable skeleton for brand matching: NFKC, invisible characters removed,
    common Cyrillic/Greek look-alikes folded to Latin, lower case."""
    s = unicodedata.normalize("NFKC", clean(s))
    s = re.sub(f"[{JOINERS}]", "", s)
    return s.translate(_CONFUSABLE).lower()


_CONFUSABLE = str.maketrans({
    # Cyrillic
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j",
    "ѕ": "s", "ԁ": "d", "ɡ": "g", "һ": "h", "ӏ": "l", "ԛ": "q", "ԝ": "w", "ь": "b", "т": "t",
    "м": "m", "к": "k", "н": "h", "в": "b",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C",
    "Т": "T", "Х": "X", "У": "Y", "І": "I", "Ј": "J", "Ѕ": "S",
    # Greek
    "α": "a", "ο": "o", "ρ": "p", "ν": "v", "τ": "t", "υ": "u", "ι": "i", "κ": "k",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N",
    "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    # Latin look-alikes
    "ı": "i", "ℓ": "l", "ǀ": "l",
})


def mixed_script(s: str) -> bool:
    """True when one word mixes Latin letters with Cyrillic or Greek ones ("PаyPal")."""
    for word in re.findall(r"\w+", s or ""):
        scripts = set()
        for ch in word:
            if not ch.isalpha():
                continue
            name = unicodedata.name(ch, "")
            if name.startswith("LATIN"):
                scripts.add("latin")
            elif name.startswith("CYRILLIC"):
                scripts.add("cyrillic")
            elif name.startswith("GREEK"):
                scripts.add("greek")
        if "latin" in scripts and len(scripts) > 1:
            return True
    return False
