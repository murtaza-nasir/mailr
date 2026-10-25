"""Prepare a message body for display.

The web UI renders the HTML inside a sandboxed iframe (no scripts, no same-origin); this
module still sanitises it, blocks remote content unless allowed, rewrites cid: references,
and marks quoted history so the UI can collapse it behind "…".

Tracking (core/trackers.py): open-tracking images are removed whatever the image setting, and
wrapped links are rewritten to the address they carry. With the image proxy
(core/imageproxy.py), allowed remote images and CSS backgrounds point at mailr's own
/api/v1/messages/{id}/images/{hash} route and the CSP allows only 'self' and data: images.
"""

from __future__ import annotations

import base64
import email
import email.policy
import re
import secrets
from urllib.parse import urlsplit

import nh3
from bs4 import BeautifulSoup, CData, Comment, Declaration, Doctype, ProcessingInstruction

from mailr.core import imageproxy, trackers
from mailr.core.compose import text_to_html, url_ok
from mailr.core.mime import _QUOTE_HEADER, inline_images, own_parts

# The version of render_body's output. core/bodycache.py keys its entries by it: bump it with any
# change to what render_body returns for the same message (sanitiser rules, tags, attributes,
# CSS handling, quote marking, tracker handling), or workers would serve the old output.
RENDER_VERSION = 3

_TAGS = {
    "a", "abbr", "address", "b", "bdi", "bdo", "big", "blockquote", "br", "caption", "center",
    "cite", "code", "col", "colgroup", "dd", "del", "dfn", "div", "dl", "dt", "em", "font",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd", "li", "mark", "ol",
    "p", "pre", "q", "s", "samp", "small", "span", "strike", "strong", "sub", "sup", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "tt", "u", "ul", "wbr", "article", "section",
    "header", "footer", "main", "figure", "figcaption", "picture", "time", "details", "summary",
    "style",
}
_ATTRS = {
    "*": {"style", "class", "dir", "align", "valign", "width", "height", "bgcolor", "title",
          "lang", "id", "border", "cellpadding", "cellspacing", "role"},
    "a": {"href", "name", "target", "rel"},
    "img": {"src", "alt"},
    "font": {"color", "face", "size"},
    "td": {"colspan", "rowspan", "nowrap", "background"},
    "th": {"colspan", "rowspan", "nowrap", "background"},
    "table": {"background", "summary"},
    "blockquote": {"type", "cite"},
    "col": {"span"},
    "ol": {"start", "type"},
    "time": {"datetime"},
}
_PROTOCOLS = {"http", "https", "mailto", "tel", "data"}
# mailr's own markers (data-mailr-src, -bg, -quote, -tracked). render_body writes them under a
# random name of its own for each call (`data-m<random>-quote`), which alone passes the sanitiser,
# and renames them only after it (_finish): markup the first pass did not see cannot carry one.
_MARKS = ("src", "bg", "quote", "tracked")
# What a link or an image may point at in the output: absolute web, mail and phone links; images
# from data:, the web, or mailr's image proxy. Relative addresses would resolve against mailr.
_LINK_OK = re.compile(r"(?i)(https?://[^/]|mailto:|tel:)")
_SRC_OK = re.compile(r"(?i)^(https?://[^/]|data:image/|/api/v1/messages/[^/\s]+/images/[0-9a-f]{32}$)")
# The CSS properties allowed in style attributes and <style> elements: the common text and box
# properties, and what mail layouts use besides.
_CSS_PROPS = {
    "azimuth", "background-color", "border-bottom-color", "border-collapse", "border-color",
    "border-left-color", "border-right-color", "border-top-color", "clear", "color", "cursor",
    "direction", "display", "elevation", "float", "font", "font-family", "font-size", "font-style",
    "font-variant", "font-weight", "height", "letter-spacing", "line-height", "overflow", "pause",
    "pause-after", "pause-before", "pitch", "pitch-range", "richness", "speak", "speak-header",
    "speak-numeral", "speak-punctuation", "speech-rate", "stress", "text-align", "text-decoration",
    "text-indent", "unicode-bidi", "vertical-align", "voice-family", "volume", "white-space", "width",
} | {
    "background", "background-image", "background-position", "background-repeat", "background-size",
    "border", "border-top", "border-right", "border-bottom", "border-left", "border-radius",
    "border-collapse", "border-spacing", "border-width", "border-style", "border-color",
    "box-sizing", "display", "font", "line-height", "list-style", "list-style-type", "margin",
    "margin-top", "margin-right", "margin-bottom", "margin-left", "max-width", "min-width",
    "max-height", "min-height", "padding", "padding-top", "padding-right", "padding-bottom",
    "padding-left", "table-layout", "text-align", "text-decoration", "text-indent", "text-transform",
    "vertical-align", "white-space", "word-break", "word-wrap", "overflow-wrap", "letter-spacing",
    "width", "height", "opacity", "visibility", "direction", "unicode-bidi", "mso-line-height-rule",
    "outline", "overflow", "text-overflow", "font-variant", "border-top-left-radius",
    "border-top-right-radius", "border-bottom-left-radius", "border-bottom-right-radius",
}
# url() values are neutralised before sanitising (see render_body); position is omitted so an
# email cannot overlay the page chrome inside the frame.
# Remote images are shown over https only: the page is https, its CSP allows https images, and
# browsers never load plain-http images on a secure page. Most senders serve the same path over
# https (a sender that does not simply shows a broken image, as it would anyway).
_CSS_HTTP = re.compile(r"(url\(\s*(?:&quot;|['\"])?\s*)(?:http:)?//", re.IGNORECASE)


def _https(url: str) -> str:
    u = url.strip()
    if u.lower().startswith("http://"):
        return "https://" + u[7:]
    if u.startswith("//"):
        return "https:" + u
    return u


_URL_IN_CSS = re.compile(r"url\(\s*['\"]?(https?:)?//[^)]*\)", re.IGNORECASE)
# A remote url() whose address can be read off: the proxy rewrites these.
_CSS_REMOTE_URL = re.compile(r"url\(\s*(['\"]?)\s*((?:https?:)?//[^)'\"\s]+)\s*\1\s*\)", re.IGNORECASE)
_QUOTE_CLASSES = ("gmail_quote", "mailr_quote", "yahoo_quoted", "moz-cite-prefix", "protonmail_quote",
                  "divRplyFwdMsg", "OutlookMessageHeader")


def _extract(raw: bytes) -> tuple[str | None, str]:
    """(html, text) of the body. A message nested past mime.MAX_DEPTH, or one the parser
    cannot walk, is shown as its raw text (mime.parse does the same at ingest)."""
    from mailr.core.mime import _parse_minimal, too_deep

    if too_deep(raw):
        return None, _parse_minimal(raw).text
    try:
        return _extract_parts(raw)
    except RecursionError:
        return None, _parse_minimal(raw).text


def _extract_parts(raw: bytes) -> tuple[str | None, str]:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    html, text = None, None
    # The parts of an attached email (message/rfc822) are not this message's body: the reader
    # shows the attached email as a card of its own (GET /messages/{id}/attachments/{part}/email).
    for _idx, part, inside in own_parts(msg):
        if inside or part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ct = part.get_content_type()
        try:
            content = part.get_content()
        except Exception:  # noqa: BLE001
            content = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
        if ct == "text/html" and html is None and isinstance(content, str):
            html = content
        elif ct == "text/plain" and text is None and isinstance(content, str):
            text = content
    return html, text or ""


def _mark_quotes(soup: BeautifulSoup, mark: str = "data-mailr-") -> bool:
    found = False
    q = mark + "quote"
    for cls in _QUOTE_CLASSES:
        for el in soup.find_all(class_=cls):
            el[q] = "1"
            found = True
    seps = soup.find_all("div", id=re.compile(r"(appendonsend|divRplyFwdMsg|mail-editor-reference-message-container)"))
    if seps:
        # Outlook: everything after the first separator is history. One pass over the
        # following siblings (marking them per separator was quadratic on crafted mail).
        seps[0][q] = "1"
        for sib in seps[0].find_next_siblings():
            sib[q] = "1"
        for el in seps[1:]:
            el[q] = "1"
        found = True
    if not found:
        for bq in soup.find_all("blockquote"):
            if bq.get("type") == "cite" or bq.find_parent("blockquote") is None:
                prev = bq.find_previous(string=True)
                if bq.get("type") == "cite" or (prev and _QUOTE_HEADER.search(str(prev).strip() + "\n")):
                    bq[q] = "1"
                    found = True
    return found


MAX_HTML = 3 * 1024 * 1024  # larger HTML bodies are shown as text (parsing cost grows fast)
_REMOTE = ("http://", "https://", "//")


def _img_src(img) -> str:
    """An image's address: src, or the last (largest) srcset candidate when there is no src."""
    src = (img.get("src") or "").strip()
    if not src and img.get("srcset"):
        cands = [c.strip().split()[0] for c in str(img["srcset"]).split(",") if c.strip()]
        src = cands[-1] if cands else ""
    return src


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _parse(raw: bytes) -> tuple[BeautifulSoup, str, str]:
    html, text = _extract(raw)
    if html and len(html) > MAX_HTML:
        from mailr.core.mime import html_to_text

        text = text or html_to_text(html[:MAX_HTML])
        html = None
    source = "html" if html else "text"
    if not html:
        html = text_to_html(text)
    soup = BeautifulSoup(html, "html.parser")
    # Comments, and the constructs html.parser reads differently from an HTML5 parser (the
    # sanitiser's and the browser's): it takes "<![CDATA[" up to "]]>" as one node where HTML5
    # ends it at the first ">", so markup after it would reach the output unseen. Removed whole.
    for c in soup.find_all(string=lambda s: isinstance(s, (Comment, CData, Declaration, Doctype, ProcessingInstruction))):
        c.extract()
    # HTML5 parsers read <image> as <img>; so does this pass.
    for el in soup.find_all("image"):
        el.name = "img"
    for bad in soup(["script", "iframe", "object", "embed", "form", "input", "button", "meta",
                     "link", "base", "frame", "frameset", "applet", "svg", "math", "audio", "video"]):
        bad.decompose()
    _strip_mailr_markers(soup)
    return soup, text, source


def _strip_mailr_markers(soup: BeautifulSoup) -> None:
    """mailr marks quotes, blocked images and cited passages with its own attributes, classes
    and ids (data-mailr-*, mailr-*, id="mailr-hl-1"). The same names written by the sender are
    removed, so an email cannot pose as one of them. (The class mailr_quote, which mailr writes
    into the mail it sends, stays: it marks quoted history.)"""
    for el in soup.find_all(True):
        for attr in [a for a in el.attrs if a.lower().startswith("data-mailr")]:
            del el[attr]
        if str(el.get("id") or "").strip().lower().startswith("mailr"):
            del el["id"]
        cls = el.get("class")
        if cls:
            keep = [c for c in cls if not c.lower().startswith("mailr-")]
            if len(keep) != len(cls):
                if keep:
                    el["class"] = keep
                else:
                    del el["class"]


def collect_image_urls(raw: bytes) -> list[str]:
    """Every remote image address render_body may send through the proxy (normalised, trackers
    left out): <img> src/srcset, background attributes, and url() in style attributes and
    <style> elements. The serving route allows exactly these for the message."""
    soup, _, _ = _parse(raw)
    out: list[str] = []
    for img in soup.find_all("img"):
        src = _img_src(img)
        if src.lower().startswith(_REMOTE):
            url = imageproxy.normalize(src)
            if url and not trackers.is_tracker_url(url) and not trackers.looks_like_pixel(img.attrs):
                out.append(url)
    # The same order render_body rewrites in: style attributes, background attributes, <style>.
    css = [el["style"] for el in soup.find_all(style=True)]
    for chunk in css:
        for m in _CSS_REMOTE_URL.finditer(chunk):
            url = imageproxy.normalize(m.group(2))
            if url and not trackers.is_tracker_url(url):
                out.append(url)
    for el in soup.find_all(attrs={"background": True}):
        url = imageproxy.normalize(str(el["background"]))
        if url and not trackers.is_tracker_url(url):
            out.append(url)
    for st in soup.find_all("style"):
        for m in _CSS_REMOTE_URL.finditer(st.string or ""):
            url = imageproxy.normalize(m.group(2))
            if url and not trackers.is_tracker_url(url):
                out.append(url)
    # At most MAX_IMAGES distinct addresses, in document order: the rest are never fetched.
    return list(dict.fromkeys(out))[:imageproxy.MAX_IMAGES]


def image_state(h: str) -> tuple | None:
    """What render_body reads from the image proxy's cache for one image: None when the cache
    does not know it (or its entry went stale)."""
    return _state(imageproxy.lookup(h))


def _state(e: imageproxy.Entry | None) -> tuple | None:
    return None if e is None else (e.status, e.width, e.height)


def render_body(raw: bytes, message_id: int, allow_remote: bool = False, proxy_base: str | None = None,
                keep_source: bool = False, deps: dict[str, tuple | None] | None = None) -> dict:
    """Sanitised HTML for the reader. With proxy_base (".../messages/{public id}/images/"),
    allowed remote images are rewritten to the image proxy; without it they load directly.
    keep_source (reply and forward bodies) keeps each proxied image's own address in
    data-mailr-src, so the outgoing mail gets it back (core/compose.restore_remote_images).
    `deps`, when given, receives image_state() of every image whose cache entry the output
    depends on (core/bodycache.py checks them again before serving a cached copy)."""
    soup, text, source = _parse(raw)
    mark = f"data-m{secrets.token_hex(6)}-"  # mailr's markers until _finish names them

    def lookup(h: str):
        e = imageproxy.lookup(h)
        if deps is not None:
            deps[h] = _state(e)
        return e

    cid_map: dict[str, str] = {}
    budget = 8 * 1024 * 1024
    for cid, (data, ctype) in inline_images(raw).items():
        if ctype.startswith("image/") and len(data) <= budget:
            budget -= len(data)
            cid_map[cid] = f"data:{ctype};base64,{base64.b64encode(data).decode()}"

    proxied = bool(allow_remote and proxy_base)
    blocked: dict[str, int] = {}  # tracker host -> images removed
    emitted: set[str] = set()  # proxy URLs written into the body
    considered: set[str] = set()  # distinct remote images, in document order (MAX_IMAGES)

    def block(url: str) -> None:
        h = _host(url) or "unknown"
        blocked[h] = blocked.get(h, 0) + 1

    def via_proxy(url: str) -> str | None:
        """The proxy URL for an image, or None when the cache knows it cannot be shown or the
        message already has MAX_IMAGES remote images (collect_image_urls allows no more)."""
        h = imageproxy.url_hash(url)
        path = f"{proxy_base}{h}"
        if h not in considered and len(considered) >= imageproxy.MAX_IMAGES:
            return None
        considered.add(h)
        e = lookup(h)
        if e is not None and e.status != "ok":
            return None
        emitted.add(path)
        return path

    remote = False
    shown = 0
    for img in soup.find_all("img"):
        src = _img_src(img)
        if img.has_attr("srcset"):
            del img["srcset"]
        if src.lower().startswith("cid:"):
            # Inline as data: URIs; the sandboxed iframe cannot send cookies.
            img["src"] = cid_map.get(src[4:].strip("<>"), "")
        elif src.lower().startswith(_REMOTE):
            url = imageproxy.normalize(src)
            if url is None:
                img["src"] = ""
                continue
            if trackers.is_tracker_url(url) or trackers.looks_like_pixel(img.attrs):
                block(url)
                img.decompose()
                continue
            if proxy_base:
                # A fetched image of 1×1 or 2×2 pixels is a tracker whatever its address.
                e = lookup(imageproxy.url_hash(url))
                if e is not None and e.status == "ok" and trackers.tiny(e.width, e.height):
                    block(url)
                    img.decompose()
                    continue
            shown += 1
            if shown > imageproxy.MAX_IMAGES:
                img.decompose()
                continue
            remote = True
            if not allow_remote:
                img[mark + "src"] = src
                img["src"] = ""
            elif proxy_base:
                path = via_proxy(url)
                if path is None:
                    # The proxy could not fetch it (or refused it): show the alt text.
                    alt = (img.get("alt") or "").strip()
                    if alt:
                        img.replace_with(alt)
                    else:
                        img.decompose()
                    continue
                img["src"] = path
                if keep_source:
                    img[mark + "src"] = url
            else:
                img["src"] = _https(src)
        elif not src.lower().startswith("data:image/"):
            img["src"] = ""

    def css_proxy(css: str) -> str:
        def repl(m: re.Match) -> str:
            url = imageproxy.normalize(m.group(2))
            if url is None:
                return "none"
            if trackers.is_tracker_url(url):
                block(url)
                return "none"
            path = via_proxy(url)
            return f"url({path})" if path else "none"
        return _URL_IN_CSS.sub("none", _CSS_REMOTE_URL.sub(repl, css))

    for el in soup.find_all(style=True):
        if _URL_IN_CSS.search(el["style"]):
            remote = True
            if proxied:
                el["style"] = css_proxy(el["style"])
            elif not allow_remote:
                el["style"] = _URL_IN_CSS.sub("none", el["style"])
    for el in soup.find_all(attrs={"background": True}):
        remote = True
        if not allow_remote:
            el[mark + "bg"] = el["background"]
            del el["background"]
        elif proxied:
            url = imageproxy.normalize(str(el["background"]))
            path = via_proxy(url) if url and not trackers.is_tracker_url(url) else None
            if path:
                el["background"] = path
            else:
                del el["background"]
        else:
            el["background"] = _https(el["background"])
    for style in soup.find_all("style"):
        css = style.string or ""
        if _URL_IN_CSS.search(css):
            remote = True
            if proxied:
                style.string = css_proxy(css)
            elif not allow_remote:
                style.string = _URL_IN_CSS.sub("none", css)
        if "@import" in css:
            style.string = re.sub(r"@import[^;]+;", "", style.string or "")
    unwrapped = 0
    for a in soup.find_all("a"):
        # Browsers drop tabs/newlines/control characters inside URLs ("da\tta:" is "data:");
        # normalise the same way, then allow only absolute web, mail and phone links. Relative
        # and protocol-relative links would resolve against the app's own origin.
        href = re.sub(r"[\x00-\x20\x7f]+", "", a.get("href") or "")
        if not re.match(r"(?i)(https?://[^/]|mailto:|tel:)", href):
            if a.has_attr("href"):
                del a["href"]
        else:
            target = trackers.unwrap_link(href) if href.lower().startswith("http") else None
            if target:
                # The wrapper's own address stays on the link; the title says where it goes.
                a[mark + "tracked"] = href
                if not a.get("title"):
                    a["title"] = f"Opens {_host(target)} directly (tracking link removed)"
                href = target
                unwrapped += 1
            a["href"] = href
            a["target"] = "_blank"
            a["rel"] = "noopener noreferrer nofollow"

    has_quote = _mark_quotes(soup, mark)
    body = soup.body.decode_contents() if soup.body else str(soup)
    cleaned = _sanitize(body, mark)
    # Final pass on the sanitiser's OUTPUT (_finish): the CSS sanitiser re-serialises escapes
    # (`u\72l(` becomes `url(`), so remote references are only reliably found afterwards.
    # Through the proxy, only the proxy URLs written above survive in CSS. It also holds the
    # output to what the first pass decided, whatever the sanitiser's parser saw that it did not.
    cleaned, late_remote = _finish(cleaned, mark, allow_remote, proxied, frozenset(emitted))
    remote = remote or late_remote
    if allow_remote and not proxied:
        cleaned = _CSS_HTTP.sub(r"\1https://", cleaned)
    img_src = "img-src 'self' data:" if proxied else ("img-src data: https: http:" if allow_remote else "img-src data:")
    return {"html": cleaned, "text": text, "source": source, "remote_images": remote,
            "has_quote": has_quote, "proxied": proxied,
            # Open-tracking images removed from this body, by host (the reader's "N trackers blocked").
            "trackers": {"count": sum(blocked.values()), "domains": sorted(blocked)},
            "links_unwrapped": unwrapped,
            # For the viewer's iframe: a CSP that enforces the same decision in the browser.
            "csp": ("default-src 'none'; style-src 'unsafe-inline'; font-src data:; base-uri 'none'; "
                    "form-action 'none'; " + img_src)}


def _url_attr(tag: str, name: str, value: str) -> str | None:
    if name == "href":
        # Browsers drop tabs, newlines and control characters inside URLs ("da\tta:" is "data:").
        return value if _LINK_OK.match(re.sub(r"[\x00-\x20\x7f]+", "", value)) else None
    if name in ("src", "background"):
        v = value.strip()
        return value if not v or _SRC_OK.match(v) else None
    return None if name == "cite" and not url_ok(value, _PROTOCOLS) else value


def _sanitize(body: str, mark: str = "") -> str:
    """The allow-lists above. Other tags are removed and their text kept; script elements go with
    their content. "data" only survives on images; links are limited to absolute web, mail and
    phone addresses, images to data:, the web and mailr's image proxy (_url_attr). `mark` is the
    name render_body gave its markers in this call."""
    attrs = {**_ATTRS, "*": _ATTRS["*"] | {mark + m for m in _MARKS}} if mark else _ATTRS
    return nh3.clean(body, tags=_TAGS, clean_content_tags={"script"}, attributes=attrs,
                     attribute_filter=_url_attr, url_schemes=_PROTOCOLS, filter_style_properties=_CSS_PROPS,
                     link_rel=None, strip_comments=True)


_CSS_HEX_ESCAPE = re.compile(r"\\([0-9a-fA-F]{1,6})\s?")
_CSS_CHAR_ESCAPE = re.compile(r"\\(.)", re.DOTALL)
_CSS_URLISH = re.compile(r"(?:url|image-set|-webkit-image-set|image|cross-fade|element)\s*\(", re.IGNORECASE)
_CSS_FN_CALL = re.compile(r"(?:url|image-set|-webkit-image-set|image|cross-fade|element)\s*\((?:[^()]|\([^()]*\))*\)", re.IGNORECASE)
_CSS_IMPORT = re.compile(r"@import[^;{}]*(;|$)", re.IGNORECASE | re.MULTILINE)
_CSS_FONT_FACE = re.compile(r"@font-face\s*\{[^}]*\}", re.IGNORECASE)
_DATA_ONLY = re.compile(r"(?:url|image-set|-webkit-image-set)\s*\(\s*['\"]?\s*data:", re.IGNORECASE)


def _decode_css(css: str) -> str:
    css = _CSS_HEX_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)) if int(m.group(1), 16) < 0x110000 else "", css)
    return _CSS_CHAR_ESCAPE.sub(lambda m: m.group(1), css)


_CSS_PLAIN_URL = re.compile(r"url\(\s*['\"]?([^'\")\s]+)['\"]?\s*\)", re.IGNORECASE)


def neutralize_css(css: str, allow_remote: bool, keep: frozenset[str] = frozenset()) -> tuple[str, bool]:
    """Remove external references from CSS. Returns (css, had_remote). url() calls whose address
    is in `keep` (the image proxy URLs render_body wrote) stay."""
    css = _decode_css(css).replace("\x00", "")  # NUL marks kept calls below
    remote = False
    # @import and @font-face can fetch anything (and @import cannot be allowed safely).
    if _CSS_IMPORT.search(css) or _CSS_FONT_FACE.search(css):
        remote = True
        css = _CSS_FONT_FACE.sub("", _CSS_IMPORT.sub("", css))
    kept: list[str] = []
    def repl(m: re.Match) -> str:
        nonlocal remote
        call = m.group(0)
        plain = _CSS_PLAIN_URL.fullmatch(call)
        if keep and plain and plain.group(1) in keep:
            kept.append(f"url({plain.group(1)})")
            return f"\x00{len(kept) - 1}\x00"
        if _DATA_ONLY.match(call) and "http" not in call.lower():
            return call
        remote = True
        return call if allow_remote and re.search(r"https?:", call, re.IGNORECASE) else "none"
    css = _CSS_FN_CALL.sub(repl, css)
    if _CSS_URLISH.search(css) and not allow_remote:
        # An unbalanced or odd construct the pattern above did not consume: drop it.
        remote = True
        css = _CSS_URLISH.sub("none(", css)
    if kept:
        css = re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], css)
    return css, remote


def filter_stylesheet(css: str) -> str:
    """A <style> element's rules through the same property allowlist as style attributes:
    declarations of other properties (position, z-index, transform, ...) are dropped, at-rules
    other than @media are dropped, and so are rules whose selector names one of mailr's own
    classes, ids or attributes (anything containing "mailr")."""
    import tinycss2

    def rules(nodes, depth: int) -> list[str]:
        out: list[str] = []
        for r in nodes:
            if r.type == "qualified-rule":
                sel = tinycss2.serialize(r.prelude).strip()
                if not sel or "mailr" in _decode_css(sel).lower() or "<" in sel:
                    continue
                kept = []
                for d in tinycss2.parse_declaration_list(r.content, skip_comments=True, skip_whitespace=True):
                    if d.type == "declaration" and d.lower_name in _CSS_PROPS:
                        kept.append(f"{d.lower_name}:{tinycss2.serialize(d.value).strip()}"
                                    f"{' !important' if d.important else ''}")
                if kept:
                    out.append(f"{sel}{{{';'.join(kept)}}}")
            elif r.type == "at-rule" and r.lower_at_keyword == "media" and r.content is not None and depth < 3:
                inner = rules(tinycss2.parse_rule_list(r.content, skip_comments=True, skip_whitespace=True), depth + 1)
                if inner:
                    out.append(f"@media {tinycss2.serialize(r.prelude).strip()}{{{''.join(inner)}}}")
        return out

    return "\n".join(rules(tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True), 0))


def _finish(html: str, mark: str, allow_remote: bool, proxied: bool, keep: frozenset[str]) -> tuple[str, bool]:
    """The last pass, on the sanitiser's output. Returns (html, had_remote).

    - The sender's own mailr markers are removed (data-mailr-*, mailr-* classes, mailr… ids), then
      the ones render_body wrote under `mark` get their names.
    - Images and backgrounds load only what render_body allowed: data: images, the proxy URLs it
      wrote (`keep`), or https while remote images load directly. Anything else is cleared and
      counts as a remote image (the "images blocked" bar).
    - Links open in a new tab without a referrer.
    - CSS loses its remote references (neutralize_css, filter_stylesheet)."""
    soup = BeautifulSoup(html, "html.parser")
    _strip_mailr_markers(soup)
    remote = False

    def loads(v: str) -> bool:
        v = v.strip()
        if not v or v.lower().startswith("data:image/"):
            return True
        return v in keep if proxied else (allow_remote and v.lower().startswith("https://"))

    for el in soup.find_all(True):
        for attr in [a for a in el.attrs if a.startswith(mark)]:
            el["data-mailr-" + attr[len(mark):]] = el.attrs.pop(attr)
        if el.name == "img" and not loads(str(el.get("src") or "")):
            el["src"] = ""
            remote = True
        if el.has_attr("background") and not loads(str(el["background"])):
            del el["background"]
            remote = True
        if el.name == "a" and el.has_attr("href"):
            el["target"] = "_blank"
            el["rel"] = "noopener noreferrer nofollow"
        if el.has_attr("style"):
            css, r = neutralize_css(str(el["style"]), allow_remote and not proxied, keep)
            remote |= r
            el["style"] = css
    for st in soup.find_all("style"):
        css, r = neutralize_css(filter_stylesheet(st.string or st.get_text() or ""), allow_remote and not proxied, keep)
        remote |= r
        # Decoding escapes above can produce "<" (e.g. from "\\3c/style\\3e"); style text is
        # written out verbatim, so a literal "<" could close the element and inject markup.
        # CSS reads "\\3c " as the same character, HTML does not.
        st.string = css.replace("<", "\\3c ")
    return str(soup), remote
