"""Parse raw RFC 5322 messages into the fields mailr indexes."""

from __future__ import annotations

import email
import email.parser
import email.policy
import hashlib
import html as htmllib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

import html2text

from mailr.core import fromline, langid, textsafe, visible

_WS = re.compile(r"\s+")
_MSGID = re.compile(r"<[^<>\s]+>")
_SUBJ_PREFIX = re.compile(
    r"^\s*((re|fwd?|aw|wg|sv|vs|antw|rif|r|tr|enc|odp|res)(\[\d+\])?\s*:\s*|\[[^\]]{1,40}\]\s*)+",
    re.IGNORECASE,
)
_QUOTE_HEADER = re.compile(
    r"^(On .{5,200}wrote:|Le .{5,200}a écrit\s?:|Am .{5,200}schrieb .{0,100}:|"
    r"-{2,}\s*Original Message\s*-{2,}|From: .+|_{10,})\s*$",
    re.MULTILINE,
)

MAX_BODY = 60_000


def norm_subject(s: str) -> str:
    s = _SUBJ_PREFIX.sub("", s or "")
    return _WS.sub(" ", s).strip().lower()


def parse_msgids(value: str | None) -> list[str]:
    if not value:
        return []
    return _MSGID.findall(value)


MAX_ADDRESSES = 1000  # per header; more is abuse, and getaddresses is super-linear


def _raw_header_values(msg: EmailMessage, name: str) -> list[str]:
    """Header values without the structured parser (which raises on e.g. an encoded CR/LF in a
    display name). Encoded words are decoded leniently and line breaks removed."""
    from email.header import decode_header, make_header

    out = []
    for _k, v in msg.raw_items():
        if _k.lower() != name.lower():
            continue
        try:
            v = str(make_header(decode_header(str(v))))
        except Exception:  # noqa: BLE001
            v = str(v)
        out.append(re.sub(r"[\r\n]+", " ", v))
    return out


def _addr_list(msg: EmailMessage, name: str) -> list[dict[str, str]]:
    try:
        vals = [str(v) for v in msg.get_all(name, [])]
    except Exception:  # noqa: BLE001  malformed header: fall back to lenient decoding
        vals = _raw_header_values(msg, name)
    vals = [re.sub(r"[\r\n]+", " ", v)[:200_000] for v in vals]
    out = []
    for disp, addr in getaddresses(vals)[:MAX_ADDRESSES]:
        # Bidi, invisible and control characters never stay in an address (core/fromline.address).
        addr = fromline.address(addr or "")
        if not addr or "@" not in addr:
            continue
        out.append({"email": addr, "name": textsafe.clean(disp or "").strip().strip('"')})
    return out


_MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!>|~])")


_BLOCK_END = re.compile(r"(?i)</(?:p|div|li|h[1-6]|td|th)\s*>|<br\s*/?>")

# CSS that reached the text: rule blocks ("selector { prop: value; }"), with the @media or
# @font-face wrappers left empty once their rules are gone. Some senders put their stylesheet in
# the plain-text part, and a <style> hidden in an Outlook conditional comment can surface as text.
# A rule starts a line or follows another brace, so a sentence before it on the same line stays.
_CSS_RULE = re.compile(r"(?m)(?:^|(?<=[{}]))[ \t]*(?:[^{}\n;!?.]|\.(?!\s)){0,200}?"
                       r"\{(\s*(?:-?[a-zA-Z][-a-zA-Z]*\s*:\s*[^{};]*?(?:;|(?=\s*\}))\s*)+)\}")
_CSS_EMPTY = re.compile(r"(?m)(?:^|(?<=[{}]))[ \t]*@[-\w]+[^{}\n]{0,200}\{\s*\}")
# A block counts as CSS only with a sign of it: a hyphenated or common property, a unit, a
# colour or !important. "{a: 1}" in a sentence stays.
_CSS_HINT = re.compile(r"(?i)!important|\d(?:px|em|rem|pt|vh|vw|%)|#[0-9a-f]{3,8}\b|(?:^|[\s;])(?:[a-z]+-[-a-z]+|color|"
                       r"margin|padding|font|width|height|display|border|background|float|clear|position|"
                       r"top|left|right|bottom|opacity|overflow|src)\s*:")
# HTML attributes left in a sender's plain part ('" style="color: #5e5745" target="_blank"').
_ATTR_JUNK = re.compile(r'"?\s*\b(?:style|target|class|data-[-\w]+|align|valign|width|height|border|bgcolor|'
                        r'cellpadding|cellspacing|rel)\s*=\s*"[^"\n]{0,400}"?')


def strip_css(text: str) -> str:
    """Remove CSS rule text (and @media wrappers) from text meant for reading."""
    if "{" not in (text or ""):
        return text or ""
    for _ in range(4):  # nested @media blocks lose one level per pass
        t = _CSS_EMPTY.sub("", _CSS_RULE.sub(lambda mt: "" if _CSS_HINT.search(mt.group(1)) else mt.group(0), text))
        if t == text:
            break
        text = t
    return text


def looks_like_css(text: str, head: int = 2000) -> bool:
    """Whether the start of a text is a stylesheet or HTML attribute residue (a broken plain part)."""
    t = (text or "")[:head]
    return bool(any(_CSS_HINT.search(mt.group(1)) for mt in _CSS_RULE.finditer(t))
                or len(_ATTR_JUNK.findall(t)) >= 2)


def html_to_text(html: str, bullet: str = "*") -> str:
    h = html2text.HTML2Text()
    h.ul_item_mark = bullet
    h.ignore_images = True
    h.ignore_links = False
    h.body_width = 0
    h.ignore_emphasis = True
    try:
        # html2text writes Markdown and escapes characters that would be Markdown syntax
        # ("\--", "1\."); in plain-text mail those backslashes are noise (and break the
        # "-- " signature delimiter), so they are removed.
        # Inside a table cell html2text joins paragraphs and lines with nothing between them
        # ("ALEX RIVERA</p><p>Account" became "RIVERAAccount"); a space before each block end
        # keeps the words apart (it is trimmed where the block ends a line).
        html = _BLOCK_END.sub(r" \g<0>", html)
        text = _MD_ESCAPE.sub(r"\1", h.handle(html))
        text = re.sub(r"[ \t]+\n", "\n", text)  # Markdown hard-break padding
        text = strip_css(text)  # stylesheet text html2text let through
        return re.sub(r"(?m)^--$", "-- ", text)  # keep the standard signature delimiter
    except Exception:  # noqa: BLE001
        return re.sub(r"<[^>]+>", " ", html)


def strip_quoted(text: str) -> str:
    """Drop quoted history (lines starting with '>' and everything after a quote header)."""
    m = _QUOTE_HEADER.search(text)
    if m and m.start() > 20:
        text = text[: m.start()]
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith(">")]
    return "\n".join(lines).strip()


# Invisible characters newsletters pad their preheader with (soft hyphen, combining grapheme
# joiner, zero-width and bidi marks, word joiner, braille blank, BOM).
_INVISIBLE = re.compile("[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2069\u2800\u3164\ufeff\uffa0]")
_RULE_LINE = re.compile(r"[\s|:+*_=~#-]*")  # table rules, empty table rows, <hr> as "* * *"
_LINE_MARK = re.compile(r"^\s*(?:#{1,6}\s+|[*+-]\s+|>\s*)+")  # heading, bullet, quote markers


_ENTITY = re.compile(r"&(?:[a-zA-Z]{2,8}|#\d{2,5}|#x[0-9a-fA-F]{2,4});")


def make_snippet(text: str, n: int = 200) -> str:
    """The list preview: the body's first words as plain text. html2text turns tables into
    Markdown ("| | | See the details |"), headings into "# Title" and links into [text](url);
    none of that belongs in a one-line preview or a notification."""
    t = _ATTR_JUNK.sub(" ", strip_css(_INVISIBLE.sub("", text or "")))
    if "&" in t:
        t = _ENTITY.sub(lambda mt: htmllib.unescape(mt.group(0)), t)  # "Terms&nbsp;apply" in a plain part
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)  # Markdown links and images
    t = re.sub(r"<(?:https?|mailto):[^>\s]*>", " ", t)  # autolinks
    t = re.sub(r"\((?:https?|mailto):[^)\s]*\)", " ", t)  # bracketed link targets
    t = re.sub(r"\bhttps?://\S{25,}", " ", t)  # long bare URLs (tracking links)
    parts = []
    for line in t.splitlines():
        if _RULE_LINE.fullmatch(line):
            continue
        line = _LINE_MARK.sub("", line)
        cells = [c.strip() for c in line.split("|")]
        line = "  ".join(c for c in cells if c) if len(cells) > 1 else line
        line = re.sub(r"[#*_`>~=-]{2,}|\.{4,}|…{2,}", " ", line)  # separators, dot leaders
        if line.strip():
            parts.append(line.strip())
    # Bidi controls (isolates too), invisible and control characters out: an entity
    # can decode to one, and the phone shows the preview as it comes.
    t = _WS.sub(" ", textsafe.clean(" ".join(parts)))
    return re.sub(r" ([.,;:!?])(?=\s|$)", r"\1", t).strip()[:n]  # "see <url>." left "see ."


def shown_text(html: str | None, plain: str) -> tuple[str, str]:
    """(the body text a reader sees, the sender's schema.org description or "").

    With an HTML part this is the rendered HTML's visible text (core/visible.py: preheader
    first, hidden elements, alt text and quoted history left out), as mail providers build their
    previews: senders' generated plain parts often open with image alt text or a different
    layout. The plain text is used when there is no HTML part or the HTML shows almost no
    text (an image-only message)."""
    if not html:
        return plain, ""
    try:
        v = visible.extract(html)
    except Exception as e:  # noqa: BLE001  a body the parser cannot read: the plain text
        import logging

        logging.getLogger("mailr.mime").warning("visible text: %s: %s", type(e).__name__, e)
        return plain, ""
    shown = strip_quoted(v.shown())
    if visible.letters(shown) < MIN_SHOWN_LETTERS and visible.letters(plain) > visible.letters(shown):
        return plain, v.description
    return shown, v.description


MIN_SHOWN_LETTERS = 20  # fewer letters in the rendered HTML: "almost no text", use the plain part


def preview(html: str | None, plain: str) -> str:
    """The list preview: the sender's declared description when there is one, else the start
    of the text a reader sees."""
    shown, description = shown_text(html, plain)
    return make_snippet(description or shown)


@dataclass
class Parsed:
    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    subject: str
    from_email: str
    from_name: str
    to: list[dict[str, str]]
    cc: list[dict[str, str]]
    bcc: list[dict[str, str]]
    reply_to: list[dict[str, str]]
    delivered_to: list[str]
    list_id: str | None
    list_unsubscribe: str | None
    date: datetime | None
    text: str
    html: str | None
    snippet: str
    body_text: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    auth_results: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    size: int = 0
    # Language of the text a reader sees (core/langid.py): see Message.lang.
    lang: str | None = None
    lang_confidence: float | None = None
    # The text of attached emails (message/rfc822 parts, nested ones too), kept apart from the
    # body: search finds it at a lower rank (Message.attached_text), and nothing else reads it.
    attached_text: str = ""


# Names any sender can write and no real receiver should stamp: the store's hostname counts only
# when it is a real name. Listing one in MAILR_TRUSTED_AUTHSERV_IDS still
# trusts it, for a setup that really runs that way.
_UNSET_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "localdomain", "127.0.0.1", "::1", "[::1]"})


def trusted_authserv_ids() -> set[str]:
    """The authserv-ids whose Authentication-Results are believed: MAILR_TRUSTED_AUTHSERV_IDS
    plus the store's own hostname (MAILR_IMAP_HOSTNAME, which Stalwart writes), unless that is
    still the default `localhost`. An entry that starts with "." or "*." also matches its
    subdomains."""
    from mailr.config import get_settings

    s = get_settings()
    ids = {x.strip().lower() for x in (s.trusted_authserv_ids or "").split(",") if x.strip()}
    host = (s.imap_hostname or "").strip().lower().rstrip(".")
    if host and host not in _UNSET_HOSTNAMES:
        ids.add(host)
    return ids


def _authserv_trusted(authserv: str, trusted: set[str]) -> bool:
    a = authserv.strip().lower().rstrip(".")
    for t in trusted:
        if t.startswith(("*.", ".")):
            suffix = t.lstrip("*")
            if a.endswith(suffix) and len(a) > len(suffix):
                return True
        elif a == t:
            return True
    return False


def _auth_results(msg: EmailMessage, trusted: set[str] | None = None) -> dict[str, str]:
    """Results from the TOP-MOST Authentication-Results header only: it is the one the
    receiving provider prepended. Lower ones can be forged by the sender, and so can the top
    one on a path where no receiver adds any; so it counts only when its authserv-id is a
    trusted one (trusted_authserv_ids). Parsed by core/authres.py (RFC 8601): results are read
    from the unit tokens only, never from inside property values or comments."""
    from mailr.core import authres

    try:
        headers = msg.get_all("Authentication-Results", [])
    except Exception:  # noqa: BLE001
        headers = []
    ar = authres.top_trusted(list(headers or []), trusted if trusted is not None else trusted_authserv_ids())
    out = authres.summarize(ar)
    # A From header that does not read as one plain address never verifies anyone (core/fromline.py):
    # authres.verified_for refuses every domain while "from_problem" is set.
    problem = fromline.of(msg).problem
    if problem and problem != "missing":
        out["from_problem"] = problem
    return out


def content_hash(raw: bytes) -> str:
    """Identity of a message's content: key headers plus the normalised body. Transport
    headers added in transit (Received, Delivered-To, ARC, provider spam scores) do not
    change it, so the same message delivered to two addresses hashes the same."""
    sep = raw.find(b"\r\n\r\n")
    if sep < 0:
        sep = raw.find(b"\n\n")
        body = raw[sep + 2:] if sep >= 0 else b""
    else:
        body = raw[sep + 4:]
    head = email.parser.BytesHeaderParser(policy=email.policy.compat32).parsebytes(raw[: max(sep, 0)] + b"\n\n")
    h = hashlib.sha256()
    for name in ("Message-ID", "Date", "From", "Subject", "Content-Type"):
        h.update(_WS.sub(" ", str(head.get(name, "") or "")).strip().lower().encode("utf-8", "replace"))
        h.update(b"\x00")
    lines = [ln.rstrip() for ln in body.replace(b"\r\n", b"\n").split(b"\n")]
    while lines and not lines[-1]:
        lines.pop()
    h.update(b"\n".join(lines))
    return h.hexdigest()


def _addr_key(msg: EmailMessage, name: str) -> list[str]:
    return sorted({a["email"] for a in _addr_list(msg, name)})


def semantic_digest(raw: bytes) -> str | None:
    """Identity of a message that survives re-encoding in transit (quoted-printable vs 8bit,
    folded headers): the decoded headers people act on (From, Reply-To, To, Cc, Subject, Date,
    Message-ID) and the decoded content of every part. Two copies with the same digest show
    and do the same thing. None when the message cannot be parsed fully."""
    if too_deep(raw):
        return None
    try:
        msg: EmailMessage = email.message_from_bytes(raw, policy=email.policy.default)  # type: ignore[assignment]
        h = hashlib.sha256()
        for name in ("From", "Reply-To", "To", "Cc"):
            h.update(name.encode() + b":" + ",".join(_addr_key(msg, name)).encode("utf-8", "replace") + b"\x00")
        for name in ("Subject", "Date", "Message-ID"):
            v = msg.get(name)
            h.update(name.encode() + b":" + _WS.sub(" ", str(v or "")).strip().encode("utf-8", "replace") + b"\x00")
        for idx, part in enumerate(msg.walk()):
            if idx >= MAX_PARTS:
                break
            if part.is_multipart():
                continue
            ctype = part.get_content_type()
            h.update(ctype.encode() + b"|" + str(safe_filename(part.get_filename()) or "").encode("utf-8", "replace") + b"|")
            data = part.get_payload(decode=True) or b""
            if ctype.startswith("text/"):
                try:
                    data = data.decode(part.get_content_charset() or "utf-8", "replace").encode("utf-8")
                except LookupError:
                    pass
                lines = [ln.rstrip() for ln in data.replace(b"\r\n", b"\n").split(b"\n")]
                while lines and not lines[-1]:
                    lines.pop()
                data = b"\n".join(lines)
            h.update(hashlib.sha256(data).digest())
        return h.hexdigest()
    except Exception:  # noqa: BLE001  unparseable: never "the same" as anything
        return None


def _strip_nul(v: Any) -> Any:
    """Postgres text and jsonb cannot hold NUL (0x00); some real mail has it in bodies or
    headers. Remove it from every string the index stores."""
    if isinstance(v, str):
        return v.replace("\x00", "") if "\x00" in v else v
    if isinstance(v, list):
        return [_strip_nul(x) for x in v]
    if isinstance(v, dict):
        return {k: _strip_nul(x) for k, x in v.items()}
    return v


MAX_PARTS = 2000
MAX_DEPTH = 40


def safe_filename(name: str | None) -> str | None:
    """An attachment's file name as shown and saved: no bidi controls (an RTLO turns
    "invoice<RLO>fdp.exe" into "invoiceexe.pdf" on screen), invisible or control characters,
    and no path."""
    if not name:
        return name
    n = textsafe.clean(name).replace("/", "_").replace("\\", "_").strip()
    return n or "attachment"


_BOUNDARY = re.compile(rb'(?i)boundary\s*=\s*(?:"([^"\r\n]{1,200})"|([^\s;"]{1,200}))')


def mime_depth(raw: bytes) -> int:
    """How deeply the multipart bodies of raw nest, from its boundaries alone (no parsing):
    each declared boundary opens a level that its closing "--boundary--" line ends."""
    import bisect

    closes: dict[bytes, list[int]] = {}
    for m in _CLOSE.finditer(raw):
        closes.setdefault(m.group(1), []).append(m.start())
    events: list[tuple[int, int]] = []
    for m in _BOUNDARY.finditer(raw):
        b = m.group(1) or m.group(2)
        events.append((m.start(), 1))
        ends = closes.get(b, [])
        i = bisect.bisect_right(ends, m.end())
        events.append((ends[i] if i < len(ends) else len(raw), -1))
    depth = best = 0
    for _, d in sorted(events):
        depth += d
        best = max(best, depth)
    return best


_CLOSE = re.compile(rb"(?m)^--([^\r\n]{1,200}?)--[ \t]*\r?$")


def too_deep(raw: bytes) -> bool:
    """True when the MIME tree nests past MAX_DEPTH: such a message is read from its headers
    and raw text only (the standard parser recurses once per level)."""
    return b"oundary" in raw and mime_depth(raw) > MAX_DEPTH


# Content types of an attached email. A part of a multipart/digest without a Content-Type is
# message/rfc822 by default (RFC 2046), and the standard parser types it so.
EMAIL_TYPES = ("message/rfc822", "message/global", "message/news")
MAX_ATTACHED_EMAILS = 50  # summarised and indexed per message; any more are listed as plain .eml files
MAX_ATTACHED_DEPTH = 4  # nested forwards read for search; deeper ones are listed only
MAX_ATTACHED_TEXT = 30_000
EXCERPT = 300


def is_attached_email(part: EmailMessage) -> bool:
    return part.get_content_type() in EMAIL_TYPES


def own_parts(msg: EmailMessage):
    """(walk index, part, inside) for every part of msg in msg.walk() order, so the indices stay
    the ones recorded in Message.attachments. `inside` is True for the parts of an attached email
    (message/rfc822 and the like): those belong to the attached email, not to the outer message's
    body. The attached email's own part is yielded with inside False."""
    idx = 0

    def walk(part: EmailMessage, inside: bool):
        nonlocal idx
        yield idx, part, inside
        idx += 1
        if not part.is_multipart():
            return
        child_inside = inside or is_attached_email(part)
        for sub in part.iter_parts():
            yield from walk(sub, child_inside)

    yield from walk(msg, False)


def attached_email_bytes(part: EmailMessage) -> bytes:
    """The attached email of a message/rfc822 part as bytes (CRLF lines), for download and for
    reading it. A part sent base64 or quoted-printable (not allowed, but seen) is decoded first."""
    import base64
    import quopri

    cte = str(part.get("Content-Transfer-Encoding") or "").strip().lower()
    payload = part.get_payload()
    inner = payload[0] if isinstance(payload, list) and payload else None
    if cte in ("base64", "quoted-printable"):
        # The parser read the encoded text as a message: take that text back and decode it.
        if inner is None:
            text = str(payload or "")
        elif not inner.keys() and not inner.is_multipart():
            text = str(inner.get_payload() or "")
        else:
            text = inner.as_string(policy=email.policy.compat32)
        data = text.encode("ascii", "replace")
        try:
            return base64.b64decode(data) if cte == "base64" else quopri.decodestring(data)
        except Exception:  # noqa: BLE001  undecodable: the text as it came
            return data
    if inner is not None:
        try:
            return inner.as_bytes(policy=email.policy.default.clone(linesep="\r\n"))
        except (UnicodeError, ValueError, TypeError, AttributeError):
            # A raw 8-bit byte in a structured header cannot be re-folded under policy.default;
            # compat32 writes the headers back as they came.
            return inner.as_bytes(policy=email.policy.compat32.clone(linesep="\r\n"))
    if isinstance(payload, (str, bytes)):
        return payload.encode("utf-8", "replace") if isinstance(payload, str) else payload
    raise ValueError("an attached email without content")


def email_filename(part: EmailMessage, subject: str | None) -> str:
    """The file name an attached email is listed and downloaded under: its subject plus .eml;
    the sender's own name for the part when the subject is empty."""
    name = (safe_filename(_WS.sub(" ", subject or "").strip()) or "")[:120].strip()
    if name:
        return f"{name}.eml"
    own = safe_filename(part.get_filename())
    if own:
        return own if own.lower().endswith(".eml") else f"{own}.eml"
    return "attached-email.eml"


def _attached_email(part: EmailMessage, idx: int, depth: int, summarise: bool) -> tuple[dict[str, Any], str]:
    """(the attachment entry, the attached email's text for search) of one attached email. A
    broken or unreadable one degrades to a plain .eml entry without a summary, never an error."""
    try:
        data = attached_email_bytes(part)
    except Exception:  # noqa: BLE001
        data = b""
    entry: dict[str, Any] = {"part": idx, "filename": email_filename(part, None), "type": "message/rfc822",
                             "size": len(data), "cid": None, "inline": False}
    if not summarise or not data.strip() or depth >= MAX_ATTACHED_DEPTH:
        return entry, ""
    try:
        if too_deep(data):
            raise RecursionError("attached email nested too deeply")
        inner = _parse(data, depth + 1)
    except Exception as e:  # noqa: BLE001  includes RecursionError
        import logging

        logging.getLogger("mailr.mime").info("attached email %s not read: %s", idx, type(e).__name__)
        return entry, ""
    if not (inner.subject or inner.from_email or inner.date):
        return entry, ""  # no headers: not an email that can be shown as one
    entry["filename"] = email_filename(part, inner.subject)
    entry["email"] = {
        "subject": inner.subject[:300],
        "from_name": inner.from_name[:200],
        "from_email": inner.from_email[:320],
        "date": inner.date.isoformat() if inner.date else None,
        "excerpt": inner.snippet[:EXCERPT],
        "attachments": len([a for a in inner.attachments if not a.get("inline")]),
    }
    if any(a.get("type") in ("text/calendar", "application/ics") or str(a.get("filename") or "").lower().endswith(".ics")
           for a in inner.attachments):
        # The attached email is an invitation: the reader asks for the invitation card, which the
        # server shows only when the user forwarded it from a trusted address (core/trusted.py).
        entry["email"]["invite"] = True
    text = "\n\n".join(t for t in (inner.subject, inner.body_text, inner.attached_text) if t and t.strip())
    return entry, text


def attached_email_at(raw: bytes, index: int) -> tuple[bytes, int]:
    """(the bytes of the attached email at walk index `index`, the walk index of its first own
    part in the outer message): the reader opens it as a message of its own."""
    if too_deep(raw):
        raise KeyError(index)
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    for idx, part, _inside in own_parts(msg):
        if idx == index:
            if not is_attached_email(part):
                raise KeyError(index)
            return attached_email_bytes(part), idx + 1
        if idx > index:
            break
    raise KeyError(index)



def parse(raw: bytes) -> Parsed:
    """Never raises: a message the full parser cannot handle (malformed headers, absurd MIME
    nesting) is indexed from its headers and raw text instead, so one bad message cannot stop
    ingestion or store sync for the whole account."""
    try:
        if too_deep(raw):
            raise RecursionError(f"MIME nesting deeper than {MAX_DEPTH}")
        p = _parse(raw)
    except Exception as e:  # noqa: BLE001  includes RecursionError
        import logging

        logging.getLogger("mailr.mime").warning("falling back to a minimal parse: %s: %s", type(e).__name__, e)
        p = _parse_minimal(raw)
    for f in p.__dataclass_fields__:
        setattr(p, f, _strip_nul(getattr(p, f)))
    return p


def _parse(raw: bytes, depth: int = 0) -> Parsed:
    msg: EmailMessage = email.message_from_bytes(raw, policy=email.policy.default)  # type: ignore[assignment]

    def hdr(name: str) -> str:
        try:
            v = msg.get(name)
            return str(v) if v is not None else ""
        except Exception:  # noqa: BLE001  malformed header
            return ""

    frm = fromline.of(msg)  # the one strict reading of From
    date = None
    try:
        if msg.get("Date"):
            date = parsedate_to_datetime(hdr("Date"))
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
    except Exception:  # noqa: BLE001
        date = None

    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict[str, Any]] = []
    attached_texts: list[str] = []
    n_emails = 0
    for idx, part, inside in own_parts(msg):
        if idx >= MAX_PARTS:
            break
        if inside:
            continue  # a part of an attached email: listed with it, never in this body
        if is_attached_email(part):
            n_emails += 1
            entry, atext = _attached_email(part, idx, depth, n_emails <= MAX_ATTACHED_EMAILS)
            attachments.append(entry)
            if atext and sum(len(t) for t in attached_texts) < MAX_ATTACHED_TEXT:
                attached_texts.append(atext)
            continue
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = part.get_content_disposition()
        fname = safe_filename(part.get_filename())
        if ctype == "text/calendar" and not fname and disp != "attachment":
            # An iMIP invitation sent as a body alternative. Recorded as a hidden attachment so
            # the viewer knows to offer the invitation card.
            fname, disp = "invite.ics", "inline"
        if disp == "attachment" or (fname and ctype not in ("text/plain", "text/html")):
            try:
                size = len(part.get_payload(decode=True) or b"")
            except Exception:  # noqa: BLE001
                size = 0
            attachments.append(
                {
                    "part": idx,
                    "filename": fname or "attachment",
                    "type": ctype,
                    "size": size,
                    "cid": (part.get("Content-ID") or "").strip("<>") or None,
                    "inline": disp == "inline",
                }
            )
            continue
        try:
            content = part.get_content()
        except Exception:  # noqa: BLE001
            payload = part.get_payload(decode=True) or b""
            content = payload.decode("utf-8", "replace")
        if ctype == "text/plain" and isinstance(content, str):
            text_parts.append(content)
        elif ctype == "text/html" and isinstance(content, str):
            html_parts.append(content)
        elif part.get("Content-ID") and ctype.startswith("image/"):
            attachments.append(
                {
                    "part": idx,
                    "filename": fname or "image",
                    "type": ctype,
                    "size": len(part.get_payload(decode=True) or b""),
                    "cid": part.get("Content-ID", "").strip("<>"),
                    "inline": True,
                }
            )

    html = "\n".join(html_parts) if html_parts else None
    text = "\n".join(text_parts) if text_parts else (html_to_text(html) if html else "")
    if html and text_parts and len(text.strip()) < 80:
        # A stub plain part ("View this email in your browser") next to real HTML content.
        from_html = html_to_text(html)
        if len(from_html.strip()) > 3 * len(text.strip()):
            text = from_html
    elif html and text_parts and looks_like_css(text):
        # A plain part that starts with the sender's stylesheet or attribute residue: the HTML
        # part reads better (it is what is shown in the reader).
        from_html = html_to_text(html)
        if from_html.strip():
            text = from_html
    elif not html and looks_like_css(text, 600):
        text = strip_css(text)
    body = strip_quoted(text)[:MAX_BODY]
    # One reading of the body for the list preview and the language: what the reader shows.
    shown, description = shown_text(html, body or text)
    lang, lang_confidence = langid.detect(shown)
    delivered = [
        a.lower()
        for v in (msg.get_all("Delivered-To", []) + msg.get_all("X-Original-To", []))
        for _, a in getaddresses([str(v)])
        if a
    ]
    list_id = hdr("List-Id") or None
    if list_id:
        m = re.search(r"<([^>]+)>", list_id)
        list_id = (m.group(1) if m else list_id).strip().lower()[:400]

    return Parsed(
        message_id=(parse_msgids(hdr("Message-ID")) or [None])[0],
        in_reply_to=(parse_msgids(hdr("In-Reply-To")) or [None])[0],
        references=parse_msgids(hdr("References"))[-50:],
        subject=_WS.sub(" ", textsafe.clean(hdr("Subject"))).strip(),
        from_email=frm.email,
        from_name=frm.name,
        to=_addr_list(msg, "To"),
        cc=_addr_list(msg, "Cc"),
        bcc=_addr_list(msg, "Bcc"),
        reply_to=_addr_list(msg, "Reply-To"),
        delivered_to=list(dict.fromkeys(delivered)),
        list_id=list_id,
        list_unsubscribe=hdr("List-Unsubscribe") or None,
        date=date,
        text=text,
        html=html,
        snippet=make_snippet(description or shown),
        body_text=body,
        attachments=[a for a in attachments if not (a["inline"] and a["cid"])] ,
        auth_results=_auth_results(msg),
        headers={
            k: hdr(k)
            for k in (
                "Precedence",
                "Auto-Submitted",
                "X-Mailer",
                "List-Unsubscribe-Post",
                "X-Spam-Status",
                "X-Spam-Score",
                "X-Spam-Flag",
                "X-GM-LABELS",
                "X-Mailr-Origin",
            )
            if msg.get(k)
        },
        size=len(raw),
        lang=lang,
        lang_confidence=lang_confidence,
        attached_text="\n\n".join(attached_texts)[:MAX_ATTACHED_TEXT],
    )


def _parse_minimal(raw: bytes) -> Parsed:
    """Headers (leniently decoded) and the raw body as text."""
    import email.policy as pol

    # Headers only: the body is taken as raw text, so no MIME nesting is walked.
    msg = email.parser.BytesHeaderParser(policy=pol.compat32).parsebytes(raw[:2_000_000])
    sep = raw.find(b"\r\n\r\n")
    sep = sep if sep >= 0 else raw.find(b"\n\n")
    body = raw[sep + 2:][:MAX_BODY].decode("utf-8", "replace") if sep >= 0 else ""

    def one(name: str) -> str:
        vals = _raw_header_values(msg, name)
        return _WS.sub(" ", vals[0]).strip() if vals else ""

    def addrs(name: str) -> list[dict[str, str]]:
        out = []
        for disp, addr in getaddresses(_raw_header_values(msg, name))[:MAX_ADDRESSES]:
            addr = fromline.address(addr or "")
            if addr and "@" in addr:
                out.append({"email": addr, "name": textsafe.clean(disp or "").strip().strip('"')})
        return out

    frm = fromline.of(msg)
    lang, lang_confidence = langid.detect(strip_quoted(body))
    date = None
    try:
        date = parsedate_to_datetime(one("Date")) if one("Date") else None
        if date is not None and date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
    except Exception:  # noqa: BLE001
        date = None
    return Parsed(
        message_id=(parse_msgids(one("Message-ID")) or [None])[0],
        in_reply_to=(parse_msgids(one("In-Reply-To")) or [None])[0],
        references=parse_msgids(one("References"))[-50:],
        subject=textsafe.clean(one("Subject")), from_email=frm.email, from_name=frm.name,
        to=addrs("To"), cc=addrs("Cc"), bcc=addrs("Bcc"), reply_to=addrs("Reply-To"), delivered_to=[],
        list_id=None, list_unsubscribe=None, date=date, text=body, html=None,
        snippet=make_snippet(body), body_text=body, attachments=[],
        auth_results={"from_problem": frm.problem} if frm.problem not in ("", "missing") else {}, headers={}, size=len(raw),
        lang=lang, lang_confidence=lang_confidence,
    )


def get_part(raw: bytes, index: int) -> tuple[bytes, str, str]:
    """Return (payload, content_type, filename) for the walk() index recorded in attachments."""
    if too_deep(raw):
        raise KeyError(index)
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    for idx, part in enumerate(msg.walk()):
        if idx == index:
            if is_attached_email(part):
                # An attached email downloads as an .eml file of its own.
                try:
                    data = attached_email_bytes(part)
                except Exception:  # noqa: BLE001
                    data = b""
                subject = ""
                try:
                    head = email.parser.BytesHeaderParser(policy=email.policy.default).parsebytes(data[:200_000])
                    subject = _WS.sub(" ", textsafe.clean(str(head.get("Subject") or ""))).strip()
                except Exception:  # noqa: BLE001, S110
                    pass
                return data, "message/rfc822", email_filename(part, subject)
            return (
                part.get_payload(decode=True) or b"",
                part.get_content_type(),
                safe_filename(part.get_filename()) or "attachment",
            )
    raise KeyError(index)


def inline_images(raw: bytes) -> dict[str, tuple[bytes, str]]:
    if too_deep(raw):
        return {}
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    out = {}
    for part in msg.walk():
        cid = (part.get("Content-ID") or "").strip("<>")
        if cid and not part.is_multipart():
            out[cid] = (part.get_payload(decode=True) or b"", part.get_content_type())
    return out
