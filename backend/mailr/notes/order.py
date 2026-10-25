"""Fractional order keys for items, sections, labels and the grid: two inserts
never collide.

A key is a string of the digits 0-9 and the letters a-z, compared as a plain string; the same
order holds in Python, JavaScript and Postgres. A key never ends in "0", so there is always room
between two keys. Only the server makes keys. When a key grows past MAX_LEN, the caller renumbers
its group (`spread`), which happens after many inserts at one spot.
"""

from __future__ import annotations

DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
BASE = len(DIGITS)
MAX_LEN = 32
FIRST = "i"


def _d(ch: str) -> int:
    return DIGITS.index(ch)


def between(a: str | None, b: str | None) -> str:
    """A key strictly between `a` and `b` (None: the start or the end)."""
    a = a or ""
    if b is not None and b <= a:
        raise ValueError(f"order keys out of order: {a!r} >= {b!r}")
    if b is None:
        return after(a) if a else FIRST
    # The common prefix (a read with trailing zeros).
    n = 0
    while n < len(b) and (a[n] if n < len(a) else "0") == b[n]:
        n += 1
    if n:
        return b[:n] + _mid(a[n:], b[n:])
    return _mid(a, b)


def _mid(a: str, b: str | None) -> str:
    da = _d(a[0]) if a else 0
    db = _d(b[0]) if b else BASE
    if db - da > 1:
        return DIGITS[(da + db) // 2]
    if b is not None and len(b) > 1:
        return b[0]
    return DIGITS[da] + _mid(a[1:], None)


def after(a: str | None) -> str:
    """A short key after `a` (appending to the end of a group)."""
    if not a:
        return FIRST
    for i in range(len(a) - 1, -1, -1):
        if a[i] != "z":
            nxt = DIGITS[_d(a[i]) + 1]
            # Step by a few places when there is room, so later inserts between fit short keys.
            if _d(a[i]) + 4 < BASE:
                nxt = DIGITS[_d(a[i]) + 4]
            return a[:i] + nxt
    return a + FIRST


def keys_between(a: str | None, b: str | None, n: int) -> list[str]:
    """n keys strictly between `a` and `b` (None: the start or the end), short ones: each is a
    midpoint of the space left, so a thousand keys stay a few characters long."""
    if n <= 0:
        return []
    mid = between(a, b) if b is not None else _mid(a or "", None) if a else FIRST
    if b is None and a and mid <= a:
        mid = after(a)
    left = (n - 1) // 2
    return keys_between(a, mid, left) + [mid] + keys_between(mid, b, n - 1 - left)


def before(b: str | None) -> str:
    """A key before `b` (inserting at the start of a group)."""
    if not b:
        return FIRST
    return between(None, b)


def spread(n: int) -> list[str]:
    """n evenly spaced keys of one length, for renumbering a group."""
    width = 1
    while BASE**width < (n + 1) * 8:
        width += 1
    step = BASE**width // (n + 1)
    out = []
    for i in range(1, n + 1):
        v = step * i
        s = ""
        for _ in range(width):
            s = DIGITS[v % BASE] + s
            v //= BASE
        out.append(s.rstrip("0") or "1")
    return out


def too_long(key: str) -> bool:
    return len(key) > MAX_LEN
