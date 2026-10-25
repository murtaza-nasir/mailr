"""One strict reading of the From and Sender headers.

Python's header parsers disagree on a malformed address header. `From: boss@work.example
<student@work.example>` is one example: RFC 5322 (and the sender's submission server) reads the
angle address, student@; the lenient `policy.default` parser recovers by taking the first
addr-spec, boss@; the strict `getaddresses` returns no address at all. A sender at the same domain
(whose own mail is DKIM-signed by it) could then pass for another person there. So every place
where the sender's identity matters reads From here, and a header this module cannot read one way
only never matches anyone:

- `read(values)`: the address and name to show, and `problem`, a short code when the header is not
  one plain address: "missing", "several" (two From headers, or two addresses in one), "malformed"
  (the parsers disagree, the header has defects, junk outside the address, or an encoded word
  where the address belongs), "name_address" (the display name holds an address other than the
  real one: `"ceo@example.com" <x@evil.test>`).
- The address shown for a malformed header is the angle address when there is one (what RFC 5322
  and the sender's submission server read), else the first address a lenient reading finds, else
  none. The rest of the header becomes the name, so the reader's "the name shows another address"
  note appears.
- `mime._auth_results` stores the code as Message.auth_results["from_problem"], and
  `authres.verified_for` refuses every domain for such a message: no sent_by_me, no trust rule, no
  iTIP auto-apply, no vacation reply, no Speakr match.
- `sender_ok(values, from_addr)`: the Sender header (rule 3 of core/trusted.py) is absent, or reads
  cleanly as exactly the From address. A malformed Sender fails closed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.utils import getaddresses
from typing import Any

from mailr.core import textsafe

MAX_LEN = 20_000
# An address-shaped token inside a display name.
_ADDR_IN_NAME = re.compile(r"[^\s<>()\"',;:@]+@[^\s<>()\"',;:@]+\.[^\s<>()\"',;:@]+")
_ANGLE = re.compile(r"<\s*([^<>\s]+@[^<>\s]+?)\s*>")
# Defects that do not change who the address is: 8-bit text in a name or local part (RFC 6532).
_BENIGN = {"NonASCIILocalPartDefect", "UndecodableBytesDefect", "NonPrintableDefect"}
# The obsolete form RFC 5322 section 4.1 says a reader must accept, and the commonest one in real
# mail: obs-phrase, a period in an unquoted display name (`Booking.com <…>`, `Dr. Jane Doe <…>`,
# `J. Smith <…>`, `U.S. Bank <…>`). Only the name part is let through. The obsolete
# forms that touch the address itself (a source route `<@a:b@c>`, a local part with white space)
# still count, and a forged-address shape still fails the two-parser comparison or carries an
# InvalidHeaderDefect.
_BENIGN_OBSOLETE = ("period in 'phrase'", "comment found without atom")


def _benign(d: Any) -> bool:
    kind = type(d).__name__
    return kind in _BENIGN or (kind == "ObsoleteHeaderDefect" and str(d) in _BENIGN_OBSOLETE)


# A From that is only an empty group (`undisclosed-recipients:;`): no sender address at all. That is
# "missing", not "malformed".
_EMPTY_GROUP = re.compile(r'^\s*[^:<>@";,]*:\s*;\s*$')


@dataclass
class FromLine:
    email: str
    name: str
    problem: str = ""


def _text(v: Any) -> str:
    s = str(v or "")
    try:
        s = s.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    except Exception:  # noqa: BLE001, S110
        pass
    return re.sub(r"[\r\n]+", " ", s).strip()[:MAX_LEN]


def raw_values(msg: Any, name: str) -> list[str]:
    """The header's values as they arrived (no structured parse, encoded words left as they are)."""
    try:
        items = list(msg.raw_items())
    except Exception:  # noqa: BLE001
        return []
    return [_text(v) for k, v in items if str(k).lower() == name.lower()]


def _decode(s: str) -> str:
    try:
        return str(make_header(decode_header(s)))
    except Exception:  # noqa: BLE001
        return s


def address(s: str) -> str:
    """An address as mailr stores it: lower case, without bidi, invisible or control characters
    or white space (an address has no use for them)."""
    return textsafe.clean_address(s).lower()


def _clean_name(s: str) -> str:
    return textsafe.clean(_decode(s or "")).strip().strip('"').strip()


def _structured(v: str) -> tuple[list[str], bool]:
    """(addresses, has a defect that matters) by the standard library's structured parser."""
    import email.policy

    try:
        h = email.policy.default.header_factory("from", v)
        addrs = [a.addr_spec for a in h.addresses]
        bad = any(not _benign(d) for d in h.defects)
        return addrs, bad
    except Exception:  # noqa: BLE001
        return [], True


def _fallback(v: str) -> tuple[str, str]:
    """(address, name) to show for a header that does not read cleanly."""
    found = _ANGLE.findall(v)
    if found:
        addr = found[-1]
        rest = _ANGLE.sub(" ", v)
        return address(addr), _clean_name(re.sub(r"\s+", " ", rest))
    for _n, a in getaddresses([v], strict=False):
        if a and "@" in a and "=?" not in a:
            return address(a), ""
    return "", _clean_name(v)


def read(values: list[str]) -> FromLine:
    """The From header (see the module docstring)."""
    vals = [v for v in (_text(x) for x in values or []) if v]
    if not vals:
        return FromLine("", "", "missing")
    v = vals[0]
    problem = "several" if len(vals) > 1 else ""
    if _EMPTY_GROUP.match(v):
        return FromLine("", _clean_name(v.split(":", 1)[0]), problem or "missing")
    pairs = getaddresses([v])  # strict: [("", "")] for anything it cannot read
    real = [(n, a) for n, a in pairs if a]
    if len(real) > 1:
        n, a = real[0]
        return FromLine(address(a), _clean_name(n), "several")
    if len(pairs) != 1 or not real or "@" not in real[0][1] or "=?" in real[0][1]:
        a, n = _fallback(v)
        return FromLine(a, n, problem or "malformed")
    n, a = real[0]
    addr = address(a)
    name = _clean_name(n)
    if addr != a.strip().lower():
        problem = problem or "malformed"  # bidi, invisible or control characters inside the address
    structured, defect = _structured(v)
    if len(structured) != 1 or address(structured[0]) != addr or defect:
        problem = problem or "malformed"
    if any(t.lower() != addr for t in _ADDR_IN_NAME.findall(name)):
        problem = problem or "name_address"
    return FromLine(addr, name, problem)


def of(msg: Any) -> FromLine:
    return read(raw_values(msg, "From"))


def sender_ok(values: list[str], from_addr: str) -> bool:
    """Rule 3's Sender check: no Sender header, or every one reads cleanly as the From address."""
    vals = [v for v in (_text(x) for x in values or []) if v]
    for v in vals:
        got = read([v])
        if got.problem or not got.email or got.email != address(from_addr or ""):
            return False
    return True
