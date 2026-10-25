"""Authentication-Results (RFC 8601): one parser and one verification rule for every caller.

The header looks like

    mx.google.com; dkim=pass header.i=@example.org header.s=s1 header.b=AbCd;
        spf=pass (google.com: domain of a@example.org designates 192.0.2.1 as permitted sender)
        smtp.mailfrom=a@example.org; dmarc=pass (p=REJECT) header.from=example.org

An authserv-id, then result units separated by ";". Each unit starts with `method=result`, then
an optional `reason=value` and properties `ptype.property=value`. Comments ("(...)", nested,
with backslash escapes) may sit between any two tokens.

The receiver copies sender-chosen text into some property values (the envelope sender in
`smtp.mailfrom`, the HELO name in `smtp.helo`) and into comments. A local part such as
`dkim=pass.header.d=victim.example` is a valid address, so the method results are read only
from the token at the start of a unit, never by searching the text (2026-10 re-review).

Hardening beyond the grammar:
- a quoted string inside a comment is read as one piece, so a `)` or `;` inside a quoted local
  part that the receiver copied into its comment cannot end the comment and start a unit;
- a unit that does not parse is dropped (fewer results, never more);
- more than one `dmarc`, `spf` or `arc` unit in one header is ambiguous, and that method then
  counts as failed (an injected unit must never erase the receiver's own fail);
- with more than one `dkim` unit, a pass for the DMARC domain counts only when DMARC passed;
- a message whose From header does not read as one plain address (`from_problem`, core/fromline.py)
  is verified for no domain;
- a DMARC pass counts only for the domain in its own `header.from`, and a DMARC fail for a
  domain cancels any DKIM pass for that domain (`verified_for`).

`summarize` turns the trusted top-most header into the dict stored on Message.auth_results:
`spf`, `dkim`, `dmarc`, `arc` (results), `dkim_domains` (comma-separated domains of the passing
DKIM signatures) and `dmarc_from` (the DMARC unit's header.from). core/mime._auth_results adds
`from_problem` when the From header is malformed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# RFC 2045 tspecials plus space: what ends a token.
_TSPECIALS = set('()<>@,;:\\"/[]?= \t\r\n')
_KEYWORD = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
# Methods that appear at most once in a well-formed header from one receiver.
_SINGLE = ("spf", "dmarc", "arc")


@dataclass
class Unit:
    method: str
    result: str
    version: str = ""
    reason: str = ""
    # "header.d" -> ["example.org"]; a property may repeat.
    props: dict[str, list[str]] = field(default_factory=dict)

    def prop(self, name: str) -> str:
        v = self.props.get(name)
        return v[0] if v else ""


@dataclass
class AuthResults:
    authserv_id: str
    version: str = ""
    units: list[Unit] = field(default_factory=list)

    def of(self, method: str) -> list[Unit]:
        return [u for u in self.units if u.method == method]


class _Syntax(ValueError):
    pass


class _Lexer:
    def __init__(self, s: str):
        self.s = s
        self.i = 0

    def end(self) -> bool:
        return self.i >= len(self.s)

    def peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def _quoted(self) -> str:
        """A quoted string starting at the current '"'; returns its content."""
        assert self.s[self.i] == '"'
        self.i += 1
        out = []
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\" and self.i + 1 < len(self.s):
                out.append(self.s[self.i + 1])
                self.i += 2
                continue
            if c == '"':
                self.i += 1
                return "".join(out)
            out.append(c)
            self.i += 1
        raise _Syntax("unterminated quoted string")

    def _comment(self) -> None:
        assert self.s[self.i] == "("
        depth = 0
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\":
                self.i += 2
                continue
            if c == '"':
                self._quoted()  # hardening: a quoted local part copied into a comment stays inside it
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    self.i += 1
                    return
            self.i += 1
        raise _Syntax("unterminated comment")

    def cfws(self) -> None:
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif c == "(":
                self._comment()
            else:
                return

    def keyword(self) -> str:
        j = self.i
        while self.i < len(self.s) and self.s[self.i] in _KEYWORD:
            self.i += 1
        if self.i == j:
            raise _Syntax(f"keyword expected at {j}")
        return self.s[j:self.i].lower()

    def expect(self, c: str) -> None:
        if self.peek() != c:
            raise _Syntax(f"{c!r} expected at {self.i}")
        self.i += 1

    def value(self) -> str:
        """RFC 2045 value: a token or a quoted string."""
        if self.peek() == '"':
            return self._quoted()
        j = self.i
        while self.i < len(self.s) and self.s[self.i] not in _TSPECIALS:
            self.i += 1
        if self.i == j:
            raise _Syntax(f"value expected at {j}")
        return self.s[j:self.i]

    def pvalue(self) -> str:
        """A property value: value, or [local-part] "@" domain, where the local part may be a
        quoted string. Read up to white space, a comment, or ";" outside quotes."""
        out = []
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in " \t\r\n;(":
                break
            if c == '"':
                out.append(self._quoted())
                continue
            out.append(c)
            self.i += 1
        return "".join(out)

    def skip_unit(self) -> None:
        """Error recovery: move to the next ";" outside quoted strings and comments."""
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == ";":
                return
            try:
                if c == '"':
                    self._quoted()
                    continue
                if c == "(":
                    self._comment()
                    continue
            except _Syntax:
                self.i = len(self.s)
                return
            self.i += 1


def _unit(lx: _Lexer) -> Unit:
    lx.cfws()
    method = lx.keyword()
    lx.cfws()
    version = ""
    if lx.peek() == "/":
        lx.i += 1
        lx.cfws()
        version = lx.keyword()
        lx.cfws()
    lx.expect("=")
    lx.cfws()
    unit = Unit(method=method, result=lx.keyword(), version=version)
    while True:
        lx.cfws()
        if lx.end() or lx.peek() == ";":
            return unit
        name = lx.keyword()
        lx.cfws()
        if lx.peek() == ".":
            lx.i += 1
            lx.cfws()
            name = f"{name}.{lx.keyword()}"
            lx.cfws()
            lx.expect("=")
            lx.cfws()
            unit.props.setdefault(name, []).append(lx.pvalue())
            continue
        lx.expect("=")
        lx.cfws()
        val = lx.value()
        if name == "reason" and not unit.reason:
            unit.reason = val
        else:
            # A non-standard `key=value` (Microsoft's `action=none`): kept, never read as a result.
            unit.props.setdefault(name, []).append(val)


def parse(header: str) -> AuthResults | None:
    """Parse one Authentication-Results header value; None when even the authserv-id cannot be
    read. Units that do not parse are dropped."""
    lx = _Lexer(str(header or ""))
    try:
        lx.cfws()
        authserv = lx.value()
    except _Syntax:
        return None
    out = AuthResults(authserv_id=authserv.strip().lower().rstrip("."))
    try:
        lx.cfws()
        if lx.peek().isdigit():
            j = lx.i
            while lx.peek().isdigit():
                lx.i += 1
            out.version = lx.s[j:lx.i]
            lx.cfws()
    except _Syntax:
        return out
    while not lx.end():
        if lx.peek() != ";":
            lx.skip_unit()  # junk between units
            if lx.end():
                break
        lx.i += 1  # the ";"
        start = lx.i
        try:
            u = _unit(lx)
        except _Syntax:
            lx.i = start
            lx.skip_unit()
            continue
        out.units.append(u)
    return out


def _domain(v: str) -> str:
    """The domain of a header.d / header.from / header.i value: after the last "@", lowercase,
    letters, digits, dots and hyphens only ("" otherwise)."""
    d = (v or "").rsplit("@", 1)[-1].strip().strip(".").lower()
    if not d or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in d):
        return ""
    return d


def summarize(ar: AuthResults | None) -> dict[str, str]:
    """The dict stored on Message.auth_results (see the module docstring)."""
    out: dict[str, str] = {}
    if ar is None:
        return out
    for m in _SINGLE:
        units = ar.of(m)
        if len(units) == 1:
            out[m] = units[0].result
        elif units:
            # Two units of a method one receiver writes once: text the sender chose (an unquoted ";"
            # in the HELO name or the envelope sender) started one of them. Which is the receiver's
            # cannot be told, so the method fails; an injected unit never erases a real fail.
            out[m] = "fail"
    dmarc = ar.of("dmarc")
    dmarc_dom = ""
    if len(dmarc) == 1:
        dmarc_dom = _domain(dmarc[0].prop("header.from"))
        if dmarc_dom:
            out["dmarc_from"] = dmarc_dom
    dkim = ar.of("dkim")
    if dkim:
        # One passing signature is enough for "dkim": others may be broken by a list footer.
        out["dkim"] = "pass" if any(u.result == "pass" for u in dkim) else dkim[0].result
        doms: list[str] = []
        for u in dkim:
            if u.result != "pass":
                continue
            d = _domain(u.prop("header.d")) if u.prop("header.d") else _domain(u.prop("header.i"))
            if not d or d in doms:
                continue
            if len(dkim) > 1 and out.get("dmarc") != "pass" and dmarc_dom and \
                    (dmarc_dom == d or dmarc_dom.endswith("." + d)):
                # One of several DKIM units claims the From domain while DMARC for that domain did
                # not pass: the receiver's own DMARC verdict says no aligned signature passed, so
                # the claim is suspect.
                continue
            doms.append(d)
        if doms:
            out["dkim_domains"] = ",".join(doms)
    return out


def top_trusted(headers: list[Any], trusted: set[str]) -> AuthResults | None:
    """The TOP-MOST Authentication-Results header, parsed, when its authserv-id is trusted.
    Lower headers can be written by the sender, so they are never read."""
    from mailr.core.mime import _authserv_trusted

    if not headers:
        return None
    ar = parse(str(headers[0]))
    if ar is None or not ar.authserv_id or not _authserv_trusted(ar.authserv_id, trusted):
        return None
    return ar


def verified_for(auth: dict[str, Any] | None, domain: str) -> bool:
    """A positive authentication result for `domain` (the From domain) in a stored
    Message.auth_results: a DMARC pass whose header.from is that domain, or a passing DKIM
    signature from that domain or a parent of it. A DMARC fail for the domain cancels DKIM:
    it says no aligned signature passed. Absent results never count."""
    auth = auth or {}
    dom = (domain or "").strip().strip(".").lower()
    if not dom or auth.get("from_problem"):
        # A From header that is not one plain address (core/fromline.py) verifies nobody.
        return False
    dmarc = str(auth.get("dmarc") or "").lower()
    dfrom = str(auth.get("dmarc_from") or "").lower()
    if dmarc == "pass" and dfrom == dom:
        return True
    if dmarc == "fail" and (not dfrom or dfrom == dom):
        return False
    for d in str(auth.get("dkim_domains") or "").split(","):
        d = d.strip().lower()
        if d and (dom == d or dom.endswith("." + d)):
            return True
    return False


def failed(auth: dict[str, Any] | None) -> bool:
    """An explicit DMARC or DKIM failure in the stored results."""
    auth = auth or {}
    return str(auth.get("dmarc") or "").lower() == "fail" or str(auth.get("dkim") or "").lower() == "fail"
