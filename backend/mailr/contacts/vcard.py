"""A small vCard 3.0 / 4.0 reader (RFC 6350, RFC 2426) for the address-book mirror.

It reads what People needs: names, email addresses, phones, postal addresses, organization, title,
birthday, categories, and group cards (vCard 4 KIND:group with MEMBER, and Apple's
X-ADDRESSBOOKSERVER-KIND:group with X-ADDRESSBOOKSERVER-MEMBER). Line folding, parameter
quoting, escapes, quoted-printable values of old cards, and Apple's `item1.` property groups
are handled. Nothing is ever written back.
"""

from __future__ import annotations

import quopri
import re
from typing import Any

_EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in re.split(r"\r\n|\n|\r", text or ""):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return [x for x in lines if x.strip()]


def _split(value: str, sep: str) -> list[str]:
    """Split on an unescaped separator."""
    out, cur, esc = [], [], False
    for ch in value:
        if esc:
            cur.append("\\" + ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == sep:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def _unescape(v: str) -> str:
    return re.sub(r"\\([nN,;:\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v).strip()


def _parse_line(line: str) -> tuple[str, dict[str, list[str]], str] | None:
    # name;param=a,b;param2="x:y":value — the first ':' outside quotes ends the parameters.
    inq = False
    for i, ch in enumerate(line):
        if ch == '"':
            inq = not inq
        elif ch == ":" and not inq:
            head, value = line[:i], line[i + 1:]
            break
    else:
        return None
    parts = [p for p in re.findall(r'(?:[^;"]|"[^"]*")+', head)]
    if not parts:
        return None
    name = parts[0].split(".")[-1].upper()  # drop Apple's "item1." group
    params: dict[str, list[str]] = {}
    for p in parts[1:]:
        k, eq, v = p.partition("=")
        key = k.strip().upper() if eq else "TYPE"
        vals = [x.strip().strip('"') for x in (v if eq else k).split(",")]
        params.setdefault(key, []).extend(x for x in vals if x)
    if "QUOTED-PRINTABLE" in [e.upper() for e in params.get("ENCODING", [])]:
        charset = (params.get("CHARSET") or ["utf-8"])[0]
        try:
            value = quopri.decodestring(value.encode()).decode(charset, "replace")
        except LookupError:
            value = quopri.decodestring(value.encode()).decode("utf-8", "replace")
    return name, params, value


def _types(params: dict[str, list[str]]) -> list[str]:
    return [t.lower() for t in params.get("TYPE", []) if t.lower() not in ("internet", "pref", "x400", "voice")]


def _uid_ref(v: str) -> str:
    v = v.strip()
    for prefix in ("urn:uuid:", "urn:uid:"):
        if v.lower().startswith(prefix):
            return v[len(prefix):]
    return v


def parse(text: str) -> dict[str, Any]:
    """The first VCARD in `text` as a dict: uid, kind (individual | group), fn, emails [{email, type}],
    phones [{value, type}], addresses [{text, street, city, region, postcode, country, type}],
    organization, title, birthday (YYYY-MM-DD or --MM-DD), categories [str], members [uid or email]."""
    card: dict[str, Any] = {"uid": "", "kind": "individual", "fn": "", "n": "", "emails": [], "phones": [],
                            "addresses": [], "organization": "", "title": "", "birthday": "", "categories": [],
                            "members": []}
    inside = False
    for line in _unfold(text):
        parsed = _parse_line(line)
        if parsed is None:
            continue
        name, params, value = parsed
        if name == "BEGIN" and value.strip().upper() == "VCARD":
            inside = True
            continue
        if name == "END" and value.strip().upper() == "VCARD":
            break
        if not inside:
            continue
        if name == "UID":
            card["uid"] = _uid_ref(_unescape(value))
        elif name == "FN":
            card["fn"] = _unescape(value)
        elif name == "N":
            parts = [_unescape(x) for x in _split(value, ";")] + [""] * 5
            family, given, middle, prefix, suffix = parts[:5]
            card["n"] = " ".join(x for x in (prefix, given, middle, family, suffix) if x)
        elif name in ("KIND", "X-ADDRESSBOOKSERVER-KIND") and value.strip().lower() == "group":
            card["kind"] = "group"
        elif name in ("MEMBER", "X-ADDRESSBOOKSERVER-MEMBER"):
            v = value.strip()
            card["members"].append(v[7:].lower() if v.lower().startswith("mailto:") else _uid_ref(v))
        elif name == "EMAIL":
            e = _unescape(value).removeprefix("mailto:").strip().lower()
            if _EMAIL.match(e) and e not in [x["email"] for x in card["emails"]]:
                card["emails"].append({"email": e, "type": ",".join(_types(params))})
        elif name == "TEL":
            v = _unescape(value).removeprefix("tel:").strip()
            if v:
                card["phones"].append({"value": v, "type": ",".join(_types(params))})
        elif name == "ADR":
            parts = [_unescape(x).replace("\n", ", ") for x in _split(value, ";")] + [""] * 7
            pobox, ext, street, city, region, postcode, country = parts[:7]
            street = ", ".join(x for x in (pobox, ext, street) if x)
            text_ = ", ".join(x for x in (street, city, " ".join(y for y in (region, postcode) if y), country) if x)
            label = (params.get("LABEL") or [""])[0]
            if text_ or label:
                card["addresses"].append({"text": text_ or label.replace("\\n", ", "), "street": street, "city": city,
                                          "region": region, "postcode": postcode, "country": country,
                                          "type": ",".join(_types(params))})
        elif name == "ORG":
            card["organization"] = ", ".join(x for x in (_unescape(p) for p in _split(value, ";")) if x)
        elif name == "TITLE":
            card["title"] = _unescape(value)
        elif name == "BDAY":
            card["birthday"] = _birthday(value)
        elif name == "CATEGORIES":
            for c in _split(value, ","):
                c = _unescape(c)
                if c and c not in card["categories"]:
                    card["categories"].append(c)
    if not card["fn"]:
        card["fn"] = card["n"]
    return card


def _birthday(v: str) -> str:
    v = v.strip()
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})", v)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m.group(1) not in ("1604", "0000") else f"--{m.group(2)}-{m.group(3)}"
    m = re.match(r"^--(\d{2})-?(\d{2})", v)
    return f"--{m.group(1)}-{m.group(2)}" if m else ""
