"""Build outgoing RFC 5322 messages from the compose payload.

Payload (also the API/agent contract for drafts and sends):
{
  "identity_id": int,
  "to": [{"email","name"}], "cc": [...], "bcc": [...],
  "subject": str,
  "html": str | None, "text": str | None,     # at least one; text is derived from html
  "in_reply_to_message_id": int | None,       # mailr message id being answered
  "forward_of_message_id": int | None,
  "attachments": [{"blob": "<upload id>", "filename", "type"}],
                                              # inline images: <img src="/api/v1/uploads/<blob>"> in html
  "headers": {"X-...": "..."}                 # optional extra headers (whitelisted)
}
"""

from __future__ import annotations

import base64
import hashlib
import html as htmllib
import re
from collections.abc import Callable
from email.message import EmailMessage, MIMEPart
from email.utils import formataddr, formatdate, make_msgid
from typing import Any

import nh3
from bs4 import BeautifulSoup

from mailr.core.mime import html_to_text

ALLOWED_TAGS = {
    "a", "abbr", "acronym", "b", "blockquote", "code", "em", "i", "li", "ol", "strong", "ul",
    "p", "br", "div", "span", "img", "h1", "h2", "h3", "h4", "pre", "hr", "table", "thead",
    "tbody", "tr", "td", "th", "u", "s", "font", "sup", "sub",
}
# Inline images (ACT mail.insert_image @api). In the editor HTML an uploaded image is
# <img src="/api/v1/uploads/<blob>">; build_message() writes each one as a Content-ID part.
UPLOAD_SRC = re.compile(r"^(?:https?://[^/\s]+)?/api/v1/uploads/([0-9a-f]{32})$")
DATA_IMAGE = re.compile(r"^data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=\s]+)$", re.IGNORECASE)
INLINE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")
MAX_INLINE = 10 * 1024 * 1024  # per image
MAX_INLINE_TOTAL = 25 * 1024 * 1024  # per message


# A remote image of a quoted or forwarded message, as the editor holds it: src is mailr's image
# proxy (or empty while images are blocked) and data-mailr-src the image's own address.
PROXY_SRC = re.compile(r"^(?:https?://[^/\s]+)?/api/v1/messages/[^/\s]+/images/[0-9a-f]{32}$")
REMOTE_SRC = re.compile(r"^(?:https?:)?//[^/\s]", re.IGNORECASE)


def _img_attr(tag: str, name: str, value: str) -> bool:
    """Image attributes kept in outgoing mail. A src must be https/http, cid:, a raster data: URI
    (never SVG) or one of the user's uploads; anything else (javascript:, data:image/svg+xml,
    other relative paths) is dropped. A proxied src with its data-mailr-src survives here and
    becomes the original address in restore_remote_images."""
    if name in ("alt", "width", "height", "title", "style", "class", "align"):
        return True
    v = value.strip()
    if name == "data-mailr-src":
        return bool(REMOTE_SRC.match(v))
    if name != "src":
        return False
    return bool(re.match(r"^(https?:|cid:)", v, re.IGNORECASE) or DATA_IMAGE.match(v) or UPLOAD_SRC.match(v)
                or PROXY_SRC.match(v))


def restore_remote_images(html: str) -> str:
    """Outgoing HTML: every image the editor showed through the proxy, or held back while images
    were blocked, gets its own address again; a proxy reference without one is removed (its alt
    text stays). No data-mailr-src leaves mailr."""
    if "<img" not in html:
        return html
    soup = BeautifulSoup(html, "html.parser")
    changed = False
    for img in soup.find_all("img"):
        orig = (img.get("data-mailr-src") or "").strip()
        src = (img.get("src") or "").strip()
        if img.has_attr("data-mailr-src"):
            del img["data-mailr-src"]
            changed = True
        if orig and REMOTE_SRC.match(orig) and (not src or PROXY_SRC.match(src)):
            img["src"] = "https:" + orig if orig.startswith("//") else orig
            changed = True
        elif not src or PROXY_SRC.match(src):
            alt = (img.get("alt") or "").strip()
            img.replace_with(alt) if alt else img.decompose()
            changed = True
    return str(soup) if changed else html


def _link_attr(tag: str, name: str, value: str) -> bool:
    if name == "href":
        return not value.strip().lower().startswith("data:")
    return name in ("href", "title", "target", "rel", "style", "class", "dir", "align")


# Inline styles from the composer (colour, alignment, image size and placement, the quote
# border). Properties that can carry url() are left out, so nothing is loaded from a style.
CSS = {
    "color", "background-color", "text-align", "text-decoration", "font-weight", "font-style",
    "font-size", "font-family", "line-height", "vertical-align", "white-space", "display", "float",
    "width", "height", "max-width", "min-width", "max-height", "margin", "margin-left", "margin-right",
    "margin-top", "margin-bottom", "padding", "padding-left", "padding-right", "padding-top",
    "padding-bottom", "border", "border-left", "border-collapse", "list-style-type",
}

# Attributes on every tag ("*") and per tag; links and images then pass their own checks
# (_link_attr, _img_attr), which also decide the "*" attributes on them.
ALLOWED_ATTRS = {
    "*": {"style", "class", "dir", "align"},
    "a": {"href", "title", "target", "rel"},
    "img": {"src", "alt", "width", "height", "title", "data-mailr-src"},
    "font": {"color", "face", "size"},
    "td": {"colspan", "rowspan", "width"},
    "th": {"colspan", "rowspan", "width"},
    "blockquote": {"type", "cite"},
    "ol": {"start", "type"},
}
_ATTR_CHECKS: dict[str, Callable[[str, str, str], bool]] = {"a": _link_attr, "img": _img_attr}
PROTOCOLS = {"http", "https", "mailto", "cid", "data"}
# URL attributes the sanitiser does not check by itself (it checks href and src).
_URL_ATTRS = frozenset({"cite", "background"})
_URL_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")


def url_ok(value: str, protocols: set[str]) -> bool:
    """A relative URL, or one whose scheme is in `protocols`. Control characters and whitespace
    are removed first, as browsers do ("java\tscript:" is "javascript:")."""
    m = _URL_SCHEME.match(re.sub(r"[\x00-\x20\x7f-\xa0]+", "", htmllib.unescape(value)))
    return m is None or m.group(1).lower() in protocols


def _attr_filter(tag: str, name: str, value: str) -> str | None:
    check = _ATTR_CHECKS.get(tag)
    if check is not None and not check(tag, name, value):
        return None
    if name in _URL_ATTRS and not url_ok(value, PROTOCOLS):
        return None
    return value
EXTRA_HEADER_OK = re.compile(r"^X-[A-Za-z0-9-]{1,60}$")
# CR, LF and NUL never belong in a header value. The standard library refuses them only from
# Python 3.12.5 on, and only when the message is serialised; build_message refuses them first.
_HEADER_BAD = re.compile(r"[\r\n\x00]")


class HeaderInjection(ValueError):
    """A header input (subject, name, address, file name, extra header) holds CR, LF or NUL."""

    permanent = True  # the outbox worker fails the send at once instead of retrying


def _safe_header(what: str, value: Any) -> str:
    v = "" if value is None else str(value)
    if _HEADER_BAD.search(v):
        raise HeaderInjection(f"the {what} contains a line break or NUL character")
    return v


class AddressError(ValueError):
    """Not a plain email address (local@domain)."""


# A plain addr-spec: a dot-atom local part and a host name. No display names, comments, angle
# brackets, quoted local parts, IP literals or whitespace: what the policy checks is exactly
# what the headers and the SMTP envelope carry.
_LOCAL = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
ITIP_METHODS = ("REQUEST", "CANCEL", "REPLY", "COUNTER", "DECLINECOUNTER", "ADD", "REFRESH")


def canonical_address(value: Any) -> str:
    """The one form of an address mailr checks, writes and sends to: lower case, the domain in
    its ASCII (IDNA) form. Raises AddressError for anything but a plain local@domain."""
    s = str(value or "").strip(" \t")
    if not s or len(s) > 254 or s.count("@") != 1:
        raise AddressError(f"not an email address: {s[:80]!r}")
    local, domain = s.rsplit("@", 1)
    local = local.lower()
    if not local or len(local) > 64 or not _LOCAL.match(local):
        raise AddressError(f"not an email address: {s[:80]!r}")
    try:
        import idna

        domain = idna.encode(domain.rstrip("."), uts46=True).decode("ascii").lower()
    except Exception:  # noqa: BLE001 - any IDNA failure means not a host name
        raise AddressError(f"not an email address: {s[:80]!r}") from None
    if not domain or not all(_LABEL.match(x) for x in domain.split(".")):
        raise AddressError(f"not an email address: {s[:80]!r}")
    return f"{local}@{domain}"


def check_header_inputs(payload: dict[str, Any]) -> None:
    """Refuse (HeaderInjection) a payload whose header inputs hold CR, LF or NUL: the subject,
    recipient names and addresses, attachment names and types, extra X- headers."""
    _safe_header("subject", payload.get("subject") or "")
    for key in ("to", "cc", "bcc"):
        for a in payload.get(key) or []:
            _safe_header("name", a.get("name") or "")
            _safe_header("address", a.get("email") or "")
    for a in payload.get("attachments") or []:
        _safe_header("attachment name", a.get("filename") or "")
        _safe_header("attachment type", a.get("type") or "")
    for k, v in (payload.get("headers") or {}).items():
        _safe_header("header name", k)
        _safe_header(str(k), v)


def canonical_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """The payload with every recipient address in canonical form (raises AddressError)."""
    out = dict(payload)
    for key in ("to", "cc", "bcc"):
        if payload.get(key):
            out[key] = [{**a, "email": canonical_address(a.get("email"))} for a in payload[key]]
    return out


def fmt_addr(a: dict[str, str]) -> str:
    return formataddr((_safe_header("name", a.get("name") or ""), _safe_header("address", a["email"])))


def text_to_html(text: str) -> str:
    esc = htmllib.escape(text)
    esc = re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', esc)
    return "<div>" + esc.replace("\n", "<br>") + "</div>"


def clean_html(html: str) -> str:
    """Outgoing and stored HTML (drafts, signatures, templates): the tags, attributes, URL schemes
    and inline style properties above. Other tags are removed and their text kept; script and
    style elements go with their content."""
    return nh3.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, attribute_filter=_attr_filter,
                     url_schemes=PROTOCOLS, filter_style_properties=CSS, link_rel=None, strip_comments=True)


def sniff_image(data: bytes) -> str | None:
    """The raster type the bytes really are (png, jpeg, gif, webp), or None (SVG, HTML, other)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def content_id(key: str, domain: str) -> str:
    """The Content-ID of an inline image: the same upload always gets the same id, so a draft
    keeps its cid: references from one save to the next."""
    return f"{key}.img@{domain}"


InlineLoader = Callable[[str], "tuple[bytes, str] | None"]


def inline_parts(html: str, loader: InlineLoader | None, domain: str) -> tuple[str, list[tuple[bytes, str, str, str]]]:
    """Point every uploaded or data: image in already-cleaned HTML at a Content-ID part.

    Returns the HTML and the parts [(bytes, mime type, content id, filename)]. Width and height
    attributes stay on the <img> for clients that ignore CSS. An image that cannot be loaded, is
    not a raster image, or goes over the size limits is removed (its alt text stays)."""
    if "<img" not in html:
        return html, []
    soup = BeautifulSoup(html, "html.parser")
    parts: dict[str, tuple[bytes, str, str, str]] = {}
    total = 0
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        data: bytes | None = None
        key = ""
        if m := UPLOAD_SRC.match(src):
            key = m.group(1)
            got = loader(key) if loader else None
            data = got[0] if got else None
        elif m := DATA_IMAGE.match(src):
            try:
                data = base64.b64decode(re.sub(r"\s+", "", m.group(2)), validate=True)
            except Exception:  # noqa: BLE001
                data = None
            key = hashlib.sha256(data or b"").hexdigest()[:32]
        else:
            continue
        ctype = sniff_image(data or b"")
        if key not in parts and data and ctype and len(data) <= MAX_INLINE and total + len(data) <= MAX_INLINE_TOTAL:
            ext = ctype.split("/")[1].replace("jpeg", "jpg")
            parts[key] = (data, ctype, content_id(key, domain), f"image-{key[:8]}.{ext}")
            total += len(data)
        if key in parts:
            img["src"] = f"cid:{parts[key][2]}"
        else:
            alt = (img.get("alt") or "").strip()
            img.replace_with(alt) if alt else img.decompose()
    return str(soup), list(parts.values())


def _alt_markers(html: str) -> str:
    """Images become "[image: alt]" text, so the text alternative says where each one was."""
    if "<img" not in html:
        return html
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        alt = re.sub(r"\s+", " ", (img.get("alt") or "").strip())
        img.replace_with(f"[image: {alt}]" if alt else "[image]")
    return str(soup)


def quote_block(orig_from: str, orig_date: str, orig_html: str | None, orig_text: str, lang: str | None = None) -> str:
    """A reply's quoted history: "On <date>, <sender> wrote:" in the writer's language, then the quote."""
    from mailr import i18n

    body = orig_html if orig_html else text_to_html(orig_text)
    line = i18n.t("mail.quote.wrote", lang, date=htmllib.escape(orig_date), sender=htmllib.escape(orig_from))
    return (
        f'<div class="mailr_quote"><div dir="ltr">{line}<br></div>'
        '<blockquote class="mailr_quote" style="margin:0 0 0 .8ex;border-left:1px #ccc solid;'
        f'padding-left:1ex">{body}</blockquote></div>'
    )


def build_message(
    payload: dict[str, Any],
    *,
    from_addr: dict[str, str],
    reply_to: str = "",
    in_reply_to: str | None = None,
    references: list[str] | None = None,
    attachments: list[tuple[bytes, str, str]] | None = None,
    message_id_domain: str | None = None,
    message_id: str | None = None,
    inline_loader: InlineLoader | None = None,
) -> EmailMessage:
    """Return an EmailMessage. attachments: [(bytes, mime_type, filename)].

    inline_loader(blob) returns an upload's (bytes, type) for the inline images in the HTML.
    With inline images the tree is multipart/related (alternative first, then the images), and
    with attachments as well, multipart/mixed around that."""
    msg = EmailMessage()
    msg["From"] = fmt_addr(from_addr)
    if payload.get("to"):
        msg["To"] = ", ".join(fmt_addr(a) for a in payload["to"])
    if payload.get("cc"):
        msg["Cc"] = ", ".join(fmt_addr(a) for a in payload["cc"])
    # Bcc is never written into the message; it only goes into the SMTP envelope.
    if reply_to:
        msg["Reply-To"] = _safe_header("Reply-To", reply_to)
    msg["Subject"] = _safe_header("subject", payload.get("subject") or "")
    msg["Date"] = formatdate(localtime=False, usegmt=True)
    domain = message_id_domain or from_addr["email"].split("@", 1)[-1]
    msg["Message-ID"] = message_id or make_msgid(domain=domain)
    if in_reply_to:
        msg["In-Reply-To"] = _safe_header("In-Reply-To", in_reply_to)
        refs = [_safe_header("References", r) for r in references or []]
        if in_reply_to not in refs:
            refs.append(in_reply_to)
        msg["References"] = " ".join(refs[-30:])
    msg["MIME-Version"] = "1.0"
    msg["X-Mailer"] = "mailr"
    for k, v in (payload.get("headers") or {}).items():
        if EXTRA_HEADER_OK.match(k):
            msg[k] = _safe_header(k, v)
    # Set only by the server (the away message, core/vacation.py); SendGate.submit strips it from
    # every payload a user, token or agent sends.
    if (payload.get("_system") or {}).get("auto_reply"):
        msg["Auto-Submitted"] = "auto-replied"
        msg["X-Autoreply"] = "yes"
    elif (payload.get("_system") or {}).get("auto_forward"):
        # A forwarding filter (core/tasks.py rules_forward): RFC 3834, so other systems never
        # answer or loop it.
        msg["Auto-Submitted"] = "auto-generated"

    html = payload.get("html")
    text = payload.get("text")
    if html:
        html = restore_remote_images(clean_html(html))
        # The text part is always derived from the HTML, so the two parts cannot say different
        # things (the approval preview shows the same derived text: plain_text()).
        text = plain_text(payload)
    elif text:
        html = text_to_html(text)
    else:
        text, html = "", "<div></div>"
    html, related = inline_parts(html, inline_loader, domain)

    # With inline images the alternative goes into a part of its own, under multipart/related
    # (the standard library can only nest related inside alternative).
    body = MIMEPart() if related else msg
    body.set_content(text or "", subtype="plain", charset="utf-8")
    body.add_alternative(
        f'<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>{html}</body></html>',
        subtype="html",
        charset="utf-8",
    )
    # An iTIP message (calendar/organizer.py, RSVP replies), set only by the server like
    # auto_reply: the iCalendar object is the third alternative, `text/calendar; method=X`, and
    # again as an invite.ics attachment for clients that read only attachments (RFC 6047).
    itip = (payload.get("_system") or {}).get("itip")
    itip_ok = bool(itip and itip.get("blob") and str(itip.get("method", "")).upper() in ITIP_METHODS)
    if itip_ok:
        from mailr.calendar.itip import strip_schedule_params

        method = str(itip["method"]).upper()
        itip = {**itip, "blob": strip_schedule_params(str(itip["blob"]))}
        body.add_alternative(str(itip["blob"]), subtype="calendar", charset="utf-8", params={"method": method})
    if related:
        images = []
        for data, ctype, cid, fname in related:
            part = MIMEPart()
            maintype, _, subtype = ctype.partition("/")
            part.set_content(data, maintype=maintype, subtype=subtype, cid=f"<{cid}>",
                             disposition="inline", filename=fname)
            images.append(part)
        msg["Content-Type"] = 'multipart/related; type="multipart/alternative"'
        msg.set_payload([body, *images])
    if itip_ok:
        msg.add_attachment(str(itip["blob"]).encode(), maintype="application", subtype="ics", filename="invite.ics")
    for data, mtype, fname in attachments or []:
        _safe_header("attachment name", fname)
        maintype, _, subtype = _safe_header("attachment type", mtype or "application/octet-stream").partition("/")
        if (maintype, subtype) == ("message", "rfc822"):
            # An attached email (a forward carries it along) goes as a message part, as RFC 2046
            # wants: base64 inside message/rfc822 is not allowed, and readers would not open it.
            inner = _attached_message(data)
            if inner is not None:
                msg.add_attachment(inner, disposition="attachment", filename=fname)
                continue
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream",
                           filename=fname)
    return msg


def _attached_message(data: bytes):
    """The email in an .eml attachment as a message object, or None when it is not one."""
    import email
    import email.policy

    try:
        inner = email.message_from_bytes(data, policy=email.policy.default)
    except Exception:  # noqa: BLE001
        return None
    if not (inner.get("From") or inner.get("Subject") or inner.get("Date")):
        return None
    return inner


def plain_text(payload: dict[str, Any]) -> str:
    """The text/plain part build_message() writes for this payload."""
    if payload.get("html"):
        # A list reads "- item" (not "* item"), with no run of blank lines after it.
        text = html_to_text(_alt_markers(clean_html(payload["html"])), bullet="-")
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    return payload.get("text") or ""


def envelope_recipients(payload: dict[str, Any]) -> list[str]:
    """Every recipient once, in canonical form (raises AddressError for one that is not a plain
    address; an empty entry is skipped)."""
    seen: dict[str, None] = {}
    for key in ("to", "cc", "bcc"):
        for a in payload.get(key) or []:
            if str(a.get("email") or "").strip():
                seen[canonical_address(a.get("email"))] = None
    return list(seen)


def reply_subject(subject: str, forward: bool = False) -> str:
    prefix = "Fwd: " if forward else "Re: "
    s = subject or ""
    if re.match(r"^\s*(re|fwd?)\s*:", s, re.IGNORECASE) and not forward:
        return s
    return prefix + s
