"""Text a model writes for an email, from the Markdown models write by habit to email HTML or to
clean plain text.

Only the subset models produce: paragraphs and line breaks, **bold**, *italic* and _italic_,
bullet lists (`*`, `-`, `+`, `•`) and numbered lists (nested by indent), headings (a bold line:
no large h1 in an email), [links](url), bare URLs and <autolinks>, `code` (as plain text),
blockquotes (`>`), ``` fences (as plain lines) and rules. Everything else stays text, so ordinary
writing comes through unchanged: "5 * 3", a lone "*", addresses and identifiers with underscores,
the "-- " signature delimiter, a "- Name" sign-off as the last line.

- to_html: email HTML, through core/compose.clean_html.
- to_text: plain text for fields without rich rendering (event descriptions, iTIP comments):
  no ** or _, bullets as "• ", headings as plain lines, links as "text (url)".
- inline_text: the same for a one-line name (titles, label and filter names).
- fragment_html: a replacement inside existing HTML (an edit's new text): inline HTML when it is
  one paragraph, blocks otherwise.
"""

from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass, field

from mailr.core.compose import clean_html

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)(?:\s+#+)?\s*$")
_RULE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_BULLET = re.compile(r"^([ \t]*)([-*+•])\s+(\S.*)$")
_NUMBER = re.compile(r"^([ \t]*)(\d{1,3})([.)])\s+(\S.*)$")

# Inline syntax, in order of precedence where two start at the same place. Each span is bounded
# (SPAN characters of text, URL characters of address), so a line of unclosed marks ("**a **a
# **a …", "[a [a …") costs time in proportion to its length, not its square; a longer span stays
# plain text.
SPAN, URL = 300, 2000
# Text past this many characters is kept as plain text, unparsed.
MAX_INPUT = 200_000
# Lists and quotes nest at most this deep; deeper marks stay at the deepest level (or text), so no
# input recurses without end.
MAX_NEST = 8
_INLINE = re.compile(
    rf"(?P<code>`{{1,8}})(?P<code_t>.{{1,{SPAN}}}?)(?P=code)"
    rf"|\[(?P<link_t>[^\]\n]{{1,{SPAN}}})\]\(\s*<?(?P<link_u>(?:https?://|mailto:)[^)\s>]{{1,{URL}}})>?\s*\)"
    rf"|<(?P<auto>(?:https?://|mailto:)[^>\s]{{1,{URL}}})>"
    r"|(?P<url>\bhttps?://[^\s<>()\[\]*]+)"
    rf"|(?<![\w*])\*\*(?=\S)(?P<b1>.{{1,{SPAN}}}?)(?<=\S)\*\*(?![\w*])"
    rf"|(?<![\w_])__(?=\S)(?P<b2>.{{1,{SPAN}}}?)(?<=\S)__(?![\w_])"
    rf"|(?<![\w*])\*(?=[^\s*])(?P<i1>[^*\n]{{1,{SPAN}}}?)(?<=[^\s*])\*(?![\w*])"
    rf"|(?<![\w_])_(?=[^\s_])(?P<i2>[^_\n]{{1,{SPAN}}}?)(?<=[^\s_])_(?![\w_])")
_URL_TAIL = re.compile(r"[.,;:!?'\"_]+$")


@dataclass
class _Item:
    lines: list[str]
    children: list[_Block] = field(default_factory=list)


@dataclass
class _Block:
    kind: str  # p, h, list, quote, code, hr
    lines: list[str] = field(default_factory=list)
    gap: bool = False  # a blank line before it
    ordered: bool = False
    start: int = 1
    marker: str = ""
    indent: int = 0
    items: list[_Item] = field(default_factory=list)
    children: list[_Block] = field(default_factory=list)


def _indent(ws: str) -> int:
    return len(ws.replace("\t", "    "))


def _item(line: str) -> tuple[int, bool, str, int, str] | None:
    """(indent, ordered, marker, number, text) of a list item line, or None."""
    if _RULE.match(line):
        return None
    m = _BULLET.match(line)
    if m:
        return _indent(m.group(1)), False, m.group(2), 1, m.group(3)
    m = _NUMBER.match(line)
    if m:
        return _indent(m.group(1)), True, m.group(3), int(m.group(2)), m.group(4)
    return None


def _list_run(lines: list[str], i: int) -> tuple[list[_Block], int]:
    """The list starting at line i (items, indented continuation lines, blank lines inside),
    nested by indent; and the line after it."""
    top: list[_Block] = []
    stack: list[_Block] = []
    n = len(lines)
    while i < n:
        line = lines[i]
        it = _item(line)
        if it is None:
            if line.strip() and stack and line[:1] in (" ", "\t"):
                stack[-1].items[-1].lines.append(line.strip())  # a continuation line
                i += 1
                continue
            if not line.strip():
                j = i
                while j < n and not lines[j].strip():
                    j += 1
                if j < n and stack and (_item(lines[j]) is not None or lines[j][:1] in (" ", "\t")):
                    i = j  # a blank line inside the list
                    continue
            break
        ind, ordered, marker, num, text = it
        while stack and ind < stack[-1].indent - 1:
            stack.pop()
        if stack and (ind <= stack[-1].indent + 1 or len(stack) >= MAX_NEST):
            cur = stack[-1]  # past MAX_NEST levels, a deeper item stays at the deepest one
            if cur.ordered != ordered:
                # Another kind of list at the same level: a list of its own beside this one.
                stack.pop()
                new = _Block("list", ordered=ordered, start=num, marker=marker, indent=ind)
                (stack[-1].items[-1].children if stack else top).append(new)
                stack.append(new)
                cur = new
            cur.items.append(_Item([text]))
        else:
            new = _Block("list", ordered=ordered, start=num, marker=marker, indent=ind, items=[_Item([text])])
            (stack[-1].items[-1].children if stack else top).append(new)
            stack.append(new)
        i += 1
    return top, i


def _blocks(lines: list[str], depth: int = 0) -> list[_Block]:
    out: list[_Block] = []
    para: _Block | None = None
    gap = False
    i, n = 0, len(lines)

    def add(b: _Block) -> None:
        nonlocal gap, para
        b.gap = gap and bool(out)
        out.append(b)
        gap = False
        para = None

    while i < n:
        line = lines[i]
        if not line.strip():
            para = None
            gap = True
            i += 1
            continue
        fence = _FENCE.match(line)
        if fence:
            j = i + 1
            while j < n and not lines[j].strip().startswith(fence.group(1)):
                j += 1
            add(_Block("code", lines=lines[i + 1:j]))
            i = j + 1
            continue
        h = _HEADING.match(line)
        if h and h.group(1).strip():
            add(_Block("h", lines=[h.group(1).strip()]))
            i += 1
            continue
        if _RULE.match(line):
            add(_Block("hr"))
            i += 1
            continue
        if _QUOTE.match(line) and depth < MAX_NEST:
            j = i
            inner = []
            while j < n and (q := _QUOTE.match(lines[j])):
                inner.append(q.group(1))
                j += 1
            add(_Block("quote", children=_blocks(inner, depth + 1)))
            i = j
            continue
        if _item(line) is not None:
            run, i = _list_run(lines, i)
            add(run[0])
            out.extend(run[1:])  # another kind of list right after it
            continue
        if para is None:
            blk = _Block("p")
            add(blk)
            para = blk
        para.lines.append(line.strip() if line.strip() != "--" else "-- ")  # the signature delimiter
        i += 1
    # A single "- Name" as the last line is a sign-off, not a list.
    last = out[-1] if out else None
    if last is not None and last.kind == "list" and not last.ordered and last.marker == "-" \
            and len(last.items) == 1 and len(last.items[0].lines) == 1 and not last.items[0].children:
        last.kind, last.lines = "p", ["- " + last.items[0].lines[0]]
        if len(out) > 1 and out[-2].kind == "p" and not last.gap:
            out[-2].lines += last.lines
            out.pop()
    return out


def _parse(text: str) -> list[_Block]:
    return _blocks((text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"))


# ---------------------------------------------------------------- inline


def _split_url(u: str) -> tuple[str, str]:
    tail = _URL_TAIL.search(u)
    return (u[:tail.start()], tail.group(0)) if tail else (u, "")


def _inline_html(s: str, links: bool = True) -> str:
    out: list[str] = []
    at = 0
    for m in _INLINE.finditer(s):
        if m.start() < at:
            continue
        out.append(htmllib.escape(s[at:m.start()], quote=False))
        at = m.end()
        g = m.groupdict()
        if g["code"]:
            out.append(htmllib.escape(g["code_t"].strip(), quote=False))
        elif g["link_t"] is not None:
            label = _inline_html(g["link_t"], links=False)
            out.append(f'<a href="{htmllib.escape(g["link_u"])}">{label}</a>' if links else label)
        elif g["auto"] is not None or g["url"] is not None:
            u, tail = _split_url(g["auto"] or g["url"])
            if g["auto"]:
                tail = ""
            esc = htmllib.escape(u, quote=False)
            out.append((f'<a href="{htmllib.escape(u)}">{esc}</a>' if links else esc)
                       + htmllib.escape(tail, quote=False))
        elif g["b2"] is not None and g["b2"].isidentifier():
            out.append(htmllib.escape(m.group(0), quote=False))  # __init__, not bold
        elif g["b1"] is not None or g["b2"] is not None:
            out.append(f"<strong>{_inline_html(g['b1'] or g['b2'] or '', links)}</strong>")
        else:
            out.append(f"<em>{_inline_html(g['i1'] or g['i2'] or '', links)}</em>")
    out.append(htmllib.escape(s[at:], quote=False))
    return "".join(out)


def _inline_text(s: str) -> str:
    out: list[str] = []
    at = 0
    for m in _INLINE.finditer(s):
        if m.start() < at:
            continue
        out.append(s[at:m.start()])
        at = m.end()
        g = m.groupdict()
        if g["code"]:
            out.append(g["code_t"].strip())
        elif g["link_t"] is not None:
            label, url = _inline_text(g["link_t"]), g["link_u"]
            bare = url.removeprefix("mailto:")
            out.append(label if label.strip() in (url, bare) else f"{label} ({bare})")
        elif g["auto"] is not None:
            out.append(g["auto"])
        elif g["url"] is not None:
            out.append(g["url"])
        elif g["b2"] is not None and g["b2"].isidentifier():
            out.append(m.group(0))
        else:
            out.append(_inline_text(g["b1"] or g["b2"] or g["i1"] or g["i2"]))
    out.append(s[at:])
    return "".join(out)


# ---------------------------------------------------------------- render


def _html(blocks: list[_Block]) -> str:
    out: list[str] = []
    prev = None
    for b in blocks:
        if b.gap and prev in ("p", "h", "code") and b.kind in ("p", "h", "code"):
            out.append("<div><br></div>")
        if b.kind == "p":
            out.append("<div>" + "<br>".join(_inline_html(x) for x in b.lines) + "</div>")
        elif b.kind == "h":
            out.append(f"<div><strong>{_inline_html(b.lines[0])}</strong></div>")
        elif b.kind == "code":
            out.append("<div>" + "<br>".join(htmllib.escape(x, quote=False) for x in b.lines) + "</div>")
        elif b.kind == "hr":
            out.append("<hr>")
        elif b.kind == "quote":
            out.append(f"<blockquote>{_html(b.children)}</blockquote>")
        elif b.kind == "list":
            tag = "ol" if b.ordered else "ul"
            start = f' start="{b.start}"' if b.ordered and b.start != 1 else ""
            items = "".join("<li>" + "<br>".join(_inline_html(x) for x in it.lines) + _html(it.children) + "</li>"
                            for it in b.items)
            out.append(f"<{tag}{start}>{items}</{tag}>")
        prev = b.kind
    return "".join(out)


def _text(blocks: list[_Block], depth: int = 0) -> str:
    out: list[str] = []
    pad = "  " * depth
    for b in blocks:
        if out:
            out.append("\n\n" if b.gap else "\n")
        if b.kind in ("p", "h"):
            out.append("\n".join(_inline_text(x) for x in b.lines))
        elif b.kind == "code":
            out.append("\n".join(b.lines))
        elif b.kind == "hr":
            out.append("---")
        elif b.kind == "quote":
            out.append("\n".join(("> " + x).rstrip() for x in _text(b.children).split("\n")))
        elif b.kind == "list":
            rows = []
            for k, it in enumerate(b.items):
                mark = f"{b.start + k}. " if b.ordered else "• "
                rows.append(pad + mark + _inline_text(it.lines[0]))
                rows += [pad + " " * len(mark) + _inline_text(x) for x in it.lines[1:]]
                if it.children:
                    rows.append(_text(it.children, depth + 1))
            out.append("\n".join(rows))
    return "".join(out)


def _split_long(text: str) -> tuple[str, str]:
    """(the part read as Markdown, the rest kept as plain text): a text longer than MAX_INPUT, such
    as a crafted recording summary, is not parsed past it. Cut at a line end where there is one."""
    if len(text) <= MAX_INPUT:
        return text, ""
    cut = text.rfind("\n", 0, MAX_INPUT)
    cut = cut if cut > 0 else MAX_INPUT
    return text[:cut], text[cut:].lstrip("\n")


def to_html(text: str) -> str:
    """Model-written text (plain or Markdown) as email HTML: <div> paragraphs with a blank <div>
    between them, as the compose editor writes them, and lists, bold, italics and links."""
    head, rest = _split_long((text or "").strip("\n"))
    tail = "".join(f"<div>{htmllib.escape(x, quote=False) or '<br>'}</div>" for x in rest.split("\n")) if rest else ""
    return clean_html(_html(_parse(head)) + tail)


def to_text(text: str) -> str:
    """Model-written text as clean plain text: the Markdown marks removed, bullets as "• "."""
    if not text:
        return text
    head, rest = _split_long(text.strip("\n"))
    return _text(_parse(head)) + (f"\n\n{rest}" if rest else "")


def inline_text(text: str) -> str:
    """A one-line name (an event or task title, a label) without inline marks: **x**, _x_, `x`,
    [x](url) as "x (url)". Line starts ("# ", "- ") are left: a name is not a block."""
    if not text:
        return text
    return " ".join(_inline_text(x.strip()) for x in text.splitlines() if x.strip())


def fragment_html(text: str) -> str:
    """New text that replaces part of existing HTML: inline HTML (line breaks as <br>) when it is
    one paragraph, else blocks."""
    blocks = _parse(text or "")
    if not blocks or (len(blocks) == 1 and blocks[0].kind == "p"):
        # Spaces at the ends are kept: the text goes between other words.
        return clean_html("<br>".join(_inline_html(x) for x in (text or "").split("\n")))
    return to_html(text)
