"""The text a reader sees in an HTML body, in document order (list previews, language ID).

Mail providers build the list preview from the rendered message, not from the sender's
auto-generated plain part (which often opens with image alt text). This module reads an HTML
body the same way:

- Visible text only: <head>, <style>, <script>, comments and hidden elements (display:none,
  visibility:hidden, opacity:0, font-size:0, max-height:0, mso-hide:all, the `hidden`
  attribute, or a class/id rule in the message's own <style> with one of those) are skipped.
  Image alt text is skipped (it is not shown while images load). Link text stays, URLs do not.
- Preheader: hidden text at the very top of the body, before any visible text, is the sender's
  preview line (the email-industry convention Gmail, Apple Mail and Outlook honour). Pure
  padding (zero-width characters, &zwnj;&nbsp; chains) does not count. Hidden text anywhere
  else is never used: hiding content in the middle of a message is a spam and phishing trick.
- Quoted history (the reply markers mail clients write: gmail_quote, blockquote type=cite,
  Outlook's reply separator) is left out, as the list shows the new text of a reply.
- schema.org: a description the sender declared for the message (JSON-LD or microdata on an
  EmailMessage or promotion type), which Gmail reads for its annotations. It counts only when
  the reader sees the same text in the message (the visible text or the preheader): Gmail
  honours the markup only from registered senders, and any sender could otherwise put text in
  the list that the message never shows, such as a fake account warning.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.element import CData, Comment, Declaration, Doctype, ProcessingInstruction

# Class names mail clients put on quoted history (also used by core/render.py).
QUOTE_CLASSES = ("gmail_quote", "mailr_quote", "yahoo_quoted", "moz-cite-prefix", "protonmail_quote",
                 "divRplyFwdMsg", "OutlookMessageHeader")
# Outlook's reply separator: it and everything after it is history.
_OUTLOOK_SEP = re.compile(r"^(appendonsend|divRplyFwdMsg|mail-editor-reference-message-container)$")

MAX_HTML = 512 * 1024  # parsing cost grows with size; a preview needs the start
MAX_TEXT = 8000  # characters of visible text collected (previews and language ID need far less)
MAX_DESCRIPTION = 300  # a schema.org description longer than this is not a preview line
# Bounds on the schema.org scan: JSON-LD scripts read and their size, elements walked
# for microdata, and microdata description candidates read.
MAX_SCHEMA_SCRIPTS = 10
MAX_SCHEMA_JSON = 64 * 1024
MAX_SCHEMA_ELEMENTS = 20000
MAX_SCHEMA_PROPS = 50
_LD_JSON = re.compile(r"^\s*application/ld\+json\s*$", re.IGNORECASE)

_SKIP_TAGS = frozenset(("head", "title", "style", "script", "noscript", "template", "img", "picture", "map",
                        "area", "svg", "math", "object", "embed", "iframe", "audio", "video", "canvas",
                        "input", "select", "textarea", "meta", "link", "base"))
_BLOCK_TAGS = frozenset(("address", "article", "aside", "blockquote", "br", "center", "dd", "div", "dl", "dt",
                         "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5",
                         "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table",
                         "tbody", "td", "tfoot", "th", "thead", "tr", "ul", "caption"))
_NOT_TEXT = (Comment, CData, ProcessingInstruction, Declaration, Doctype)

# Characters that render as nothing or as blank space: preheader padding is made of these.
_PADDING = re.compile("[\\s\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180e\u200b-\u200f\u202a-\u202e"
                      "\u2060-\u2069\u2800\u3164\ufeff\uffa0]+")
_LETTER = re.compile(r"[^\W\d_]")

# Declarations that hide an element. Values are compared without spaces and !important.
_ZERO = r"0(?:\.0+)?(?:px|pt|em|rem|%)?"
_HIDING = re.compile(rf"^(?:display:none|visibility:hidden|opacity:{_ZERO}|font-size:{_ZERO}|max-height:{_ZERO}"
                     rf"|mso-hide:all)$")
_SIMPLE_SELECTOR = re.compile(r"^([a-z][a-z0-9]*)?([.#])([-\w]+)$", re.IGNORECASE)
_SCHEMA_TYPES = frozenset(("emailmessage", "message", "promotioncard", "discountoffer", "promotion", "offer"))


@dataclass
class Visible:
    preheader: str  # hidden preview text at the top of the body; "" when there is none
    text: str  # the visible text, one block per line, quoted history left out
    description: str  # a schema.org description the sender declared; "" when there is none

    def shown(self) -> str:
        """The preheader followed by the visible text (once, when the body repeats it)."""
        pre = self.preheader
        if pre and _squash(self.text).startswith(_squash(pre)):
            pre = ""
        return f"{pre}\n{self.text}" if pre else self.text


def _squash(s: str) -> str:
    return _PADDING.sub(" ", s).strip().lower()


def letters(s: str | None) -> int:
    return len(_LETTER.findall(s or ""))


def _declarations(style: str) -> list[str]:
    out = []
    for d in (style or "").split(";"):
        d = re.sub(r"\s+|!important", "", d.lower())
        if d:
            out.append(d)
    return out


def _hides(style: str) -> bool:
    return any(_HIDING.match(d) for d in _declarations(style))


def _top_level_rules(css: str) -> list[tuple[str, str]]:
    """(selector, declarations) of the rules outside @media and other at-rule blocks: a rule
    inside a media query applies only on some screens (e.g. 'hide on mobile')."""
    rules: list[tuple[str, str]] = []
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    depth, start, sel_start, selector, in_at = 0, 0, 0, "", False
    for i, ch in enumerate(css):
        if ch == "{":
            if depth == 0:
                selector = css[sel_start:i].strip()
                in_at = selector.startswith("@")
                start = i + 1
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
            if depth == 0:
                if not in_at:
                    rules.append((selector, css[start:i]))
                in_at = False
                sel_start = i + 1
        elif ch == ";" and depth == 0:
            sel_start = i + 1  # @import/@charset statements
    return rules


def _hidden_selectors(soup: BeautifulSoup) -> tuple[set[str], set[str]]:
    """Class names and ids the message's own stylesheet hides with a simple selector."""
    classes: set[str] = set()
    ids: set[str] = set()
    for st in soup.find_all("style"):
        for selector, body in _top_level_rules(st.get_text() or ""):
            if not _hides(body):
                continue
            for sel in selector.split(","):
                m = _SIMPLE_SELECTOR.match(sel.strip())
                if m:
                    (classes if m.group(2) == "." else ids).add(m.group(3).lower())
    return classes, ids


def _schema_description(soup: BeautifulSoup) -> str:
    def typed(obj) -> bool:
        t = obj.get("@type")
        names = t if isinstance(t, list) else [t]
        return any(isinstance(n, str) and n.rsplit("/", 1)[-1].lower() in _SCHEMA_TYPES for n in names)

    def walk(obj, depth=0):
        if depth > 6:
            return None
        if isinstance(obj, list):
            for x in obj:
                r = walk(x, depth + 1)
                if r:
                    return r
        elif isinstance(obj, dict):
            d = obj.get("description")
            if typed(obj) and isinstance(d, str) and d.strip():
                return d
            for k in ("@graph", "mainEntity", "about", "hasPart"):
                if k in obj:
                    r = walk(obj[k], depth + 1)
                    if r:
                        return r
        return None

    for n, sc in enumerate(soup.find_all("script", attrs={"type": _LD_JSON})):
        if n >= MAX_SCHEMA_SCRIPTS:
            break
        src = sc.string or sc.get_text() or ""
        if len(src) > MAX_SCHEMA_JSON:
            continue
        try:
            data = json.loads(src)
        except (ValueError, TypeError, RecursionError):
            continue
        d = walk(data)
        if d:
            return d
    return _microdata_description(soup)


def _typed_scope(el: Tag) -> bool:
    return el.has_attr("itemtype") and any(
        t.rstrip("/").rsplit("/", 1)[-1].lower() in _SCHEMA_TYPES for t in str(el["itemtype"]).split())


def _microdata_description(soup: BeautifulSoup) -> str:
    """The first `itemprop="description"` whose nearest itemscope is a typed EmailMessage (or
    promotion) item, in one walk of the tree: each element is visited once, with its nearest
    itemscope carried down (looking the owner up per property was quadratic). At most
    MAX_SCHEMA_ELEMENTS elements are walked and MAX_SCHEMA_PROPS candidates read."""
    stack: list[tuple[Tag, Tag | None]] = [(c, None) for c in reversed(soup.contents) if isinstance(c, Tag)]
    walked = props = 0
    while stack and walked < MAX_SCHEMA_ELEMENTS:
        el, owner = stack.pop()
        walked += 1
        if el.has_attr("itemprop") and "description" in str(el["itemprop"]).lower().split() \
                and owner is not None and _typed_scope(owner):
            props += 1
            if props > MAX_SCHEMA_PROPS:
                break
            v = el.get("content") if el.has_attr("content") else el.get_text(" ")[:MAX_DESCRIPTION * 4]
            if v and str(v).strip():
                return str(v)
        inner = el if el.has_attr("itemscope") else owner
        stack.extend((c, inner) for c in reversed(el.contents) if isinstance(c, Tag))
    return ""


def extract(html: str) -> Visible:
    """Read an HTML body as a reader sees it (see the module docstring)."""
    if not html:
        return Visible("", "", "")
    soup = BeautifulSoup(html[:MAX_HTML], "html.parser")
    description = _PADDING.sub(" ", _schema_description(soup)).strip()
    if len(description) > MAX_DESCRIPTION or letters(description) < 3:
        description = ""
    hidden_classes, hidden_ids = _hidden_selectors(soup)

    def hidden(el: Tag) -> bool:
        if el.has_attr("hidden") or _hides(str(el.get("style") or "")):
            return True
        if hidden_classes and any(str(c).lower() in hidden_classes for c in (el.get("class") or ())):
            return True
        return bool(hidden_ids and str(el.get("id") or "").lower() in hidden_ids)

    def quoted(el: Tag) -> bool:
        if el.name == "blockquote" and str(el.get("type") or "").lower() == "cite":
            return True
        return any(c in QUOTE_CLASSES for c in (el.get("class") or ()))

    root = soup.body or soup
    out: list[str] = []
    pre: list[str] = []
    size = 0
    seen_visible = False  # visible text has started: no preheader from here on
    pre_root: Tag | None = None  # the hidden element holding the preheader
    pre_done = False
    # Iterative walk (deeply nested mail must not hit the recursion limit). Each entry:
    # (node, hidden root of the node or None, leaving?)
    stack: list[tuple[object, Tag | None, bool]] = [(c, None, False) for c in reversed(list(root.children))]
    while stack and size < MAX_TEXT:
        node, hroot, leaving = stack.pop()
        if leaving:
            out.append("\n")
            continue
        if isinstance(node, NavigableString):
            if isinstance(node, _NOT_TEXT):
                continue
            s = str(node)
            if hroot is None:
                if _PADDING.fullmatch(s) or not s:
                    out.append(" " if s else "")
                    continue
                seen_visible = True
                pre_done = True
                out.append(s)
                size += len(s)
            elif not pre_done:
                if _PADDING.fullmatch(s) or not s:
                    continue
                if pre_root is None:
                    pre_root = hroot
                if hroot is pre_root:
                    pre.append(s)
                else:
                    pre_done = True  # a second hidden block: the preheader ended
            continue
        if not isinstance(node, Tag):
            continue
        name = (node.name or "").lower()
        if name in _SKIP_TAGS or quoted(node):
            if name == "br":
                out.append("\n")
            continue
        if _OUTLOOK_SEP.match(str(node.get("id") or "")) and name == "div":
            break  # Outlook reply separator: the rest is history
        child_root = hroot if hroot is not None else (node if hidden(node) else None)
        if child_root is not None and hroot is None and seen_visible:
            continue  # hidden content after visible text: never used
        if child_root is not None and pre_done:
            continue
        if name in _BLOCK_TAGS:
            out.append("\n")
            stack.append((node, hroot, True))
        stack.extend((c, child_root, False) for c in reversed(list(node.children)))
    text = _tidy("".join(out))
    preheader = _tidy(" ".join(pre)).replace("\n", " ")
    v = Visible(preheader, text, description)
    if description and _squash(description) not in _squash(v.shown().replace("\n", " ")):
        v.description = ""  # text the reader never sees is not the preview
    return v


def _tidy(s: str) -> str:
    """Padding characters out, spaces collapsed within lines, blank lines dropped."""
    lines = []
    for line in s.split("\n"):
        line = _PADDING.sub(" ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)
