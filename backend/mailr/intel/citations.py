"""Citations in model output.

Models cite with markers:
    [[m:5714 "exact short quote"]]   a message, optionally with a verbatim quote
    [[p:88123]]                      a passage returned by find_passages
    [[c:3065]]                       a whole conversation
    [[event:<uid>@<occurrence>]]     a calendar event (one occurrence, section 1)
    [[task:<uid>]]                   a task
    [[r:<id>]]                       a Speakr recording
    [[r:<id>@<seconds> "quote"]]     a moment of a recording, the quote checked within ±30 s of it
    [[web:<url>]]                    a web page the assistant's web tools returned (agent/web_tools.py);
                                     kept only when the url is in what the model read this turn
    [[table:<id>]]                   a table
    [[table:<id>#<row_id>]]          one row of a table
    [[note:<id>]]                    a note or checklist (agent/note_tools.py)
    [[note:<id>#<item_id>]]          one item of a checklist
(legacy `[c:3065]`, `[event:…]` and `[task:…]` are accepted too). The grammar is tolerant
(citegrammar.py): a title after an event or task, spaces and capitals are accepted, and a
marker that cannot be read becomes its quoted text, never raw `[[…]]`. `resolve` checks each one against the caller's
visibility, verifies quotes against the message text (never trusting the model), numbers
them, and rewrites the markers to Markdown links `[n](mailr-cite:n)`. The citation list
carries a `url` that opens the conversation at the message with the quote highlighted, so
the same links work in the web UI and in outside agents (MCP / REST).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote as urlquote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.config import get_settings
from mailr.core.visibility import visible_message_filter
from mailr.intel import citegrammar as cg
from mailr.intel.highlight import leading_words, locate
from mailr.models import Conversation, Message, MessagePassage

MAX_QUOTE = 200
# The marker grammar (tolerant of titles, spaces and capitals) lives in citegrammar.py, with
# shared vectors in spec/cases/citation-cases.yaml.
_MARKER = cg.MARKER


def url_for(conversation_id: int | None, message_id: int | None = None, quote: str | None = None) -> str | None:
    if conversation_id is None:
        return None
    from mailr.core import publicid

    base = get_settings().base_url.rstrip("/")
    u = f"{base}/mail/all/{publicid.token('conversation', conversation_id)}"
    params = []
    if message_id:
        params.append(f"m={publicid.encode('message', message_id)}")
    if quote:
        params.append(f"hl={urlquote(quote[:MAX_QUOTE], safe='')}")
    return u + ("?" + "&".join(params) if params else "")


def find_markers(text: str) -> list[tuple[str, int, str | None]]:
    """The message, passage and conversation markers in `text`: (kind, id, quote)."""
    return [(m.kind, int(m.id), m.quote) for m in cg.scan(text) if m.kind in ("m", "p", "c")]


async def _visible_message(db: AsyncSession, principal, mid: int) -> Message | None:
    q = select(Message).where(Message.id == mid, Message.user_id == principal.user_id, Message.deleted.is_(False))
    if principal.token is not None:
        q = q.where(visible_message_filter(principal))
    return (await db.execute(q)).scalar_one_or_none()


async def _visible_conversation(db: AsyncSession, principal, cid: int) -> Conversation | None:
    conv = await db.get(Conversation, cid)
    if conv is None or conv.user_id != principal.user_id:
        return None
    if principal.token is not None:
        ok = (await db.execute(select(Message.id).where(
            Message.conversation_id == cid, visible_message_filter(principal)).limit(1))).first()
        if not ok:
            return None
    return conv


# Markdown images in model output: ![alt](url). Rendering one would fetch the URL with no click,
# which an instruction hidden in an email could use to send mail content to its author.
_MD_IMAGE = re.compile(r"!\[([^\]\n]{0,300})\]\(\s*<?([^)\s>]{1,2000})>?(?:\s+\"[^\"]*\")?\s*\)")


def defang_images(text: str) -> str:
    """Turn Markdown images into plain links (the UI also refuses to load images)."""
    return _MD_IMAGE.sub(lambda m: f"[image: {m.group(1) or 'link'}]({m.group(2)})", text or "")


# Web links in an answer. Models copy long URLs badly (tracking links of 300 characters come
# back with the right start and a wrong tail), so a link is only kept when it appears in what
# the model actually read; a garbled one is repaired when its start matches exactly one real
# URL, otherwise the link is dropped and its text kept.
_MD_LINK = re.compile(r"\[([^\]\n]{0,300})\]\(\s*<?(https?://[^)\s>]{1,4000})>?(?:\s+\"[^\"]*\")?\s*\)")
_BARE_URL = re.compile(r"(?<![(<\[\w/])https?://[^\s)<>\]\"'`]+")
_URL_IN_SOURCE = re.compile(r"https?://[^\s\"'<>)\]\\`]+")
_MIN_PREFIX = 40


def _known_urls(known: str) -> set[str]:
    return {u.rstrip(".,;:!?") for u in _URL_IN_SOURCE.findall(known or "")}


def _repair(url: str, urls: set[str]) -> str | None:
    url = url.rstrip(".,;:!?")
    if url in urls:
        return url
    best, best_len, tie = None, 0, False
    for u in urls:
        n = 0
        for a, b in zip(u, url):  # noqa: B905
            if a != b:
                break
            n += 1
        if n > best_len:
            best, best_len, tie = u, n, False
        elif n == best_len:
            tie = True
    host_end = url.find("/", 8)
    need = max(_MIN_PREFIX, (host_end if host_end > 0 else len(url)) + 12)
    return best if best and best_len >= need and not tie else None


def verify_links(text: str, known: str) -> str:
    """Keep only links found in `known` (repairing near-copies); drop the rest to plain text."""
    if "http" not in (text or ""):
        return text
    urls = _known_urls(known)

    def md(mt: re.Match) -> str:
        fixed = _repair(mt.group(2), urls)
        return f"[{mt.group(1)}]({fixed})" if fixed else mt.group(1)

    text = _MD_LINK.sub(md, text)

    def bare(mt: re.Match) -> str:
        fixed = _repair(mt.group(0), urls)
        if fixed:
            return fixed
        host = re.sub(r"^https?://", "", mt.group(0)).split("/")[0]
        return f"{host} (link not found in the mail)"

    return _BARE_URL.sub(bare, text)


_KNOWN_CONV = re.compile(r'"(?:conversation_id|source_conversation_id)"\s*:\s*(\d+)|Open conversation (\d+)|^\s*- (\d+): \'', re.M)
_KNOWN_CONV_LIST = re.compile(r'"(?:conversation_ids|open_conversation_ids)"\s*:\s*\[([\d,\s]*)\]')
_KNOWN_MSG = re.compile(r'"(?:message_id|draft_id|in_reply_to_message_id)"\s*:\s*(\d+)|\bdraft (\d+)\b|message (\d+)\b')
_SAID_ID = re.compile(
    r"\b(?P<word>conversations?|threads?|emails?|messages?|msgs?|drafts?)\s*(?:id\s*)?(?:#|no\.?\s*)?:?\s*(?P<id>\d{2,})\b"
    r"|\(\s*(?:id|ID)\s*:?\s*(?P<bare>\d{2,})\s*\)", re.I)


def known_ids(known: str) -> tuple[set[int], set[int]]:
    """(conversation ids, message ids) that appear in what the model read this turn."""
    convs = {int(g) for m in _KNOWN_CONV.finditer(known or "") for g in m.groups() if g}
    convs |= {int(x) for m in _KNOWN_CONV_LIST.finditer(known or "") for x in re.findall(r"\d+", m.group(1))}
    msgs = {int(g) for m in _KNOWN_MSG.finditer(known or "") for g in m.groups() if g}
    return convs, msgs


def humanize_ids(text: str, known: str) -> str:
    """The model sometimes names an email by its internal id ("conversation 19867"). Those
    become citation markers (links named by subject), but only for ids the model actually saw
    this turn, of the kind the words say: an amount, a date or an order number never matches."""
    convs, msgs = known_ids(known)

    def sub(m: re.Match) -> str:
        if m.group("bare"):
            n = int(m.group("bare"))
            return "" if n in convs or n in msgs else m.group(0)
        n, word = int(m.group("id")), m.group("word").lower()
        if word.startswith(("conversation", "thread", "email")) and n in convs:
            return f"[[c:{n}]]"
        if word.startswith(("message", "msg", "draft", "email")) and n in msgs:
            return f"[[m:{n}]]"
        return m.group(0)

    return _SAID_ID.sub(sub, text or "")


async def resolve(db: AsyncSession, principal, text: str, known: str | None = None) -> tuple[str, list[dict[str, Any]]]:
    """Return (rewritten text, citations). Invisible or unknown targets are removed from the
    text; unverifiable quotes keep the link but are marked verified=False. Markdown images
    become plain links. With `known` (what the model read: tool results, the user's words),
    web links are checked against it and the cited messages (verify_links)."""
    r = Resolver(db, principal, known)
    return await r.resolve(text), r.cites


class Resolver:
    """Turns markers into numbered links `[n](mailr-cite:n)`, numbered in the order they are
    first met. One resolver serves a streaming answer (`marker`, one at a time as each closes;
    see `Streamer`) and then the whole text (`resolve`): a marker already met keeps its number.
    `offset` shifts the numbers, so the segments of one turn share one numbering."""

    def __init__(self, db: AsyncSession, principal, known: str | None = None, offset: int = 0,
                 web: dict[str, dict[str, Any]] | None = None):
        self.db, self.principal, self.known, self.offset = db, principal, known, offset
        # Web results and pages of this turn (ToolCtx.web_sources): url -> {title, site, published, provider}.
        self.web = web if web is not None else {}
        self.cites: list[dict[str, Any]] = []
        self.index: dict[tuple, int] = {}
        self.cited_text: list[str] = []
        self.seen_convs, self.seen_msgs = known_ids(known) if known is not None else (set(), set())
        self._conns = None

    def _link(self, n: int) -> str:
        return f"[{n}](mailr-cite:{n})" if n else ""

    def _add(self, key: tuple, c: dict[str, Any] | None) -> int:
        if c is None:
            self.index[key] = 0
            return 0
        c["n"] = self.offset + len(self.cites) + 1
        self.cites.append(c)
        self.index[key] = c["n"]
        return c["n"]

    async def marker(self, mt: re.Match) -> str:
        """What one marker match (citegrammar.MARKER) becomes: a numbered link, nothing (a record
        the caller cannot see), its quoted text (a marker that cannot be read), or itself
        (`[[…]]` that is not a marker). The spaces before it stay unless it disappears."""
        sp = mt.group("sp")
        if mt.group("legacy"):
            mk = cg.legacy(mt.group("legacy"))
        else:
            body = mt.group("body")
            if not cg.looks_like_marker(body):
                return mt.group(0)
            mk = cg.parse(body)
            if mk is None:
                fb = cg.fallback(body)
                return sp + fb if fb else ""
        n = await self.number(mk) if mk is not None else 0
        return sp + self._link(n) if n else ""

    async def number(self, mk: cg.Marker) -> int:
        """The number of a parsed marker (0: not cited)."""
        if mk.kind in ("event", "task"):
            return await self._calendar(mk)
        if mk.kind == "r":
            return await self._recording(mk)
        if mk.kind == "web":
            return self._web(mk)
        if mk.kind == "table":
            return await self._table(mk)
        if mk.kind == "note":
            return await self._note(mk)
        quote = mk.quote if mk.kind in ("m", "p") else None
        ident = int(mk.id)
        key = (mk.kind, ident, (quote or "").lower())
        if key not in self.index:
            self._add(key, await self._build(mk.kind, ident, quote))
        return self.index[key]

    async def resolve(self, text: str) -> str:
        """The whole text: images defanged, ids the model named turned into markers, every
        marker resolved, and (with `known`) web links checked."""
        text = defang_images(text)
        if self.known:
            text = humanize_ids(text, self.known)
        parts: list[str] = []
        pos = 0
        for mt in _MARKER.finditer(text or ""):
            parts.append(text[pos:mt.start()])
            parts.append(await self.marker(mt))
            pos = mt.end()
        parts.append((text or "")[pos:])
        out = cg.OPEN.sub("", "".join(parts))
        if self.known is not None:
            out = verify_links(out, self.known + "\n" + "\n".join(self.cited_text))
        return out

    # -- messages, passages, conversations

    def _holds(self, m: Message, quote: str | None) -> bool:
        return not quote or bool(locate(m.body_text or "", quote[:MAX_QUOTE]) or locate(m.subject or "", quote[:MAX_QUOTE]))

    async def _in_conversation(self, cid: int, quote: str | None) -> Message | None:
        """The message of conversation `cid` the quote comes from (else its latest), if visible."""
        db, principal = self.db, self.principal
        conv = await _visible_conversation(db, principal, cid)
        if conv is None:
            return None
        q = select(Message).where(Message.conversation_id == cid, Message.deleted.is_(False), Message.draft.is_(False))
        if principal.token is not None:
            q = q.where(visible_message_filter(principal))
        msgs = list((await db.execute(q.order_by(Message.date.desc().nulls_last(), Message.id.desc()))).scalars())
        return next((x for x in msgs if self._holds(x, quote)), msgs[0] if msgs else None)

    async def _build(self, kind: str, ident: int, quote: str | None) -> dict[str, Any] | None:
        db, principal, known = self.db, self.principal, self.known
        if kind == "p":
            p = (await db.execute(select(MessagePassage).where(
                MessagePassage.id == ident, MessagePassage.user_id == principal.user_id))).scalar_one_or_none()
            if p is None:
                return None
            m = await _visible_message(db, principal, p.message_id)
            if m is None:
                return None
            q = quote or leading_words(p.text, 16) or None
            return self._from_message(m, q, passage=p)
        if kind == "m":
            m = await _visible_message(db, principal, ident)
            seen = known is None or ident in self.seen_msgs or (m is not None and m.conversation_id in self.seen_convs)
            if m is None or not seen or not self._holds(m, quote):
                # Models sometimes cite a conversation id from a search result as a message
                # ([[m:19914 …]] for conversation 19914). When that conversation was in what it
                # read, cite its message that holds the quote; a stray id never links elsewhere.
                alt = await self._in_conversation(ident, quote) if known is None or ident in self.seen_convs else None
                if alt is not None and (self._holds(alt, quote) or m is None or not seen):
                    m = alt
                elif m is not None and not seen:
                    return None
            if m is not None:
                self.cited_text.append(m.body_text or "")
            return self._from_message(m, quote) if m else None
        conv = await _visible_conversation(db, principal, ident)
        if conv is None:
            return None
        return {"kind": "conversation", "conversation_id": conv.id, "message_id": None,
                "subject": conv.subject, "from": (conv.participants or [{}])[0].get("name")
                or (conv.participants or [{}])[0].get("email", ""),
                "date": conv.last_date.isoformat() if conv.last_date else None,
                "quote": None, "verified": True, "start": None, "end": None, "url": url_for(conv.id)}

    def _from_message(self, m: Message, quote: str | None, passage: MessagePassage | None = None) -> dict[str, Any]:
        q = (quote or "")[:MAX_QUOTE] or None
        verified, start, end = True, None, None
        if q:
            hit = locate(m.body_text or "", q) or locate(m.subject or "", q)
            if hit is None:
                verified = False
            else:
                start, end = hit.start, hit.end
                q = (m.body_text or m.subject or "")[start:end] if hit.score < 100 else q
        elif passage is not None:
            start, end = passage.start, passage.end
        return {"kind": "message", "conversation_id": m.conversation_id, "message_id": m.id,
                "subject": m.subject, "from": m.from_name or m.from_email,
                "date": (m.date or m.received_at).isoformat() if (m.date or m.received_at) else None,
                "quote": q, "verified": verified, "start": start, "end": end,
                "url": url_for(m.conversation_id, m.id, q if verified else None)}

    # -- events and tasks

    async def _calendar(self, mk: cg.Marker) -> int:
        kind, uid, occ = mk.kind, mk.id, mk.at
        key = (kind, uid, occ or "")
        if key in self.index:
            return self.index[key]
        c = await _calendar_cite(self.db, self.principal, kind, uid, occ)
        if c is None and kind == "event" and occ:  # a UID that ends in "@<date>"
            c = await _calendar_cite(self.db, self.principal, kind, f"{uid}@{occ}", None)
        if c is not None:
            same = next((x for x in self.cites if x["kind"] == kind and x.get("uid") == c["uid"]
                         and x.get("occurrence") == c.get("occurrence")), None)
            if same is not None:
                self.index[key] = same["n"]
                return same["n"]
        return self._add(key, c)

    # -- web pages

    def _web(self, mk: cg.Marker) -> int:
        """A web source: the url must be one the model read this turn (a web result, a page, or a link in
        another tool result). A near-copy of a long url is repaired as in verify_links; anything else is
        dropped. The chip shows the page's title and site and opens the page."""
        url = web_source_url(mk.id, self.web, self.known)
        if url is None:
            return 0
        key = ("web", url)
        if key not in self.index:
            self._add(key, web_cite(url, self.web.get(url) or {}, mk.quote))
        return self.index[key]

    # -- tables

    async def _table(self, mk: cg.Marker) -> int:
        key = ("table", mk.id, mk.at or "")
        if key not in self.index:
            self._add(key, await table_cite(self.db, self.principal, mk.id, mk.at))
        return self.index[key]

    # -- notes

    async def _note(self, mk: cg.Marker) -> int:
        key = ("note", mk.id, mk.at or "")
        if key not in self.index:
            self._add(key, await note_cite(self.db, self.principal, mk.id, mk.at))
        return self.index[key]

    # -- recordings

    async def _recording(self, mk: cg.Marker) -> int:
        from mailr.integrations.speakr import client as sc
        from mailr.integrations.speakr import store

        rid, t = int(mk.id), sc.seconds(mk.at) if mk.at else None
        key = ("r", rid, t, (mk.quote or "").lower())
        if key not in self.index:
            if self._conns is None:
                self._conns = await store.visible_connectors(self.db, self.principal)
            self._add(key, await _recording_cite(self.db, self.principal, rid, t, mk.quote, self._conns))
        return self.index[key]


class Streamer:
    """Resolves markers while an answer streams. `feed` takes the model's next piece of text and
    returns (text to show, citations new in it): each marker becomes its numbered link as soon as
    it closes, and text that may still become a marker (`[`, `[[…`) is held back until it is
    decided. The resolver keeps the numbers for the final text."""

    _LEGACY_START = re.compile(r"\[(?:c(?::\d{0,12})?|e(?:v(?:e(?:n(?:t(?::[^\]\s]{0,300})?)?)?)?)?"
                               r"|t(?:a(?:s(?:k(?::[^\]\s]{0,300})?)?)?)?)$")

    @classmethod
    def _may_become_marker(cls, tail: str) -> bool:
        """`tail` starts with "[" and is not a marker yet: could more text make it one?"""
        if tail == "[":
            return True
        if tail.startswith("[["):
            return "\n" not in tail and "]]" not in tail and len(tail) <= 504
        return bool(cls._LEGACY_START.match(tail))

    def __init__(self, resolver: Resolver):
        self.r = resolver
        self.buf = ""

    async def feed(self, piece: str) -> tuple[str, list[dict[str, Any]]]:
        before = len(self.r.cites)
        self.buf += piece
        out: list[str] = []
        while self.buf:
            i = self.buf.find("[")
            if i < 0:
                out.append(self.buf)
                self.buf = ""
                break
            # Spaces just before a marker go with it (a marker that disappears takes them along).
            j = i
            while j > 0 and self.buf[j - 1] in " \t":
                j -= 1
            out.append(self.buf[:j])
            self.buf = self.buf[j:]
            k = i - j
            mt = _MARKER.match(self.buf)
            if mt is not None and mt.start("sp") == 0 and mt.end("sp") == k:
                out.append(await self.r.marker(mt))
                self.buf = self.buf[mt.end():]
                continue
            if self._may_become_marker(self.buf[k:]):
                break
            out.append(self.buf[:k + 1])
            self.buf = self.buf[k + 1:]
        return "".join(out), self.r.cites[before:]

    def rest(self) -> str:
        """What is still held back when the text ends (an unclosed marker is dropped)."""
        out, self.buf = cg.clean(self.buf), ""
        return out


# ---------------------------------------------------------------- events and tasks


def calendar_url(kind: str, uid: str, occurrence: str | None = None) -> str:
    """The web route of an event occurrence or a task (frontend/src/lib/routes.ts eventPath / taskPath)."""
    base = get_settings().base_url.rstrip("/")
    if kind == "event":
        return f"{base}/calendar/event/{urlquote(uid, safe='')}/{urlquote(occurrence or '', safe='')}"
    return f"{base}/tasks/task/{urlquote(uid, safe='')}"


async def _calendar_cite(db: AsyncSession, principal, kind: str, uid: str, occ: str | None) -> dict[str, Any] | None:
    """A cited event or task, if the caller may see it (else None: the marker is dropped). An
    event's occurrence is checked against the mirror; an unknown one falls back to the event's
    own start (a single event) or its next occurrence (a series)."""
    from datetime import UTC, datetime

    from sqlalchemy import select as sel

    from mailr.calendar import ical, mirror
    from mailr.models import CalCollection, CalOccurrence

    try:
        obj = await mirror.find_by_uid(db, principal.user_id, uid, kind, principal)
    except Exception:  # noqa: BLE001  no calendar: nothing to cite
        return None
    if obj is None:
        return None
    coll = await db.get(CalCollection, obj.collection_id)
    prefs = await mirror.kind_overrides(db, principal.user_id)
    coll_color = ((prefs.get(coll.href) or {}).get("color_local") or coll.color) if coll else None
    title = obj.summary or "(no title)"
    from mailr.calendar.describe import calendar_label

    # The calendar by the label the user knows it by, never its URL or the store account (4 Oct 2026).
    shared = (prefs.get(coll.href) or {}).get("shared") if coll else None
    out: dict[str, Any] = {"kind": kind, "uid": obj.uid, "title": title, "subject": title,
                           "from": calendar_label(coll.displayname, coll.href, shared) if coll else None,
                           "conversation_id": None,
                           "message_id": None, "quote": None, "verified": True, "start": None, "end": None,
                           "color": obj.color or coll_color, "calendar_color": coll_color}
    if kind == "task":
        due = obj.due_date if obj.due_date is not None else obj.due_at
        out.update(due=ical.out_when(due) if due is not None else None, date=ical.out_when(due) if due else None,
                   all_day=obj.due_date is not None, completed=obj.status == "COMPLETED" or obj.completed_at is not None,
                   occurrence=None, url=calendar_url("task", obj.uid))
        return out
    first = ical.canonical_occurrence(obj.start_date if obj.all_day else obj.start_at)
    want = ical.normalize_occurrence(occ) if occ else ""
    row = None
    if obj.recurring:
        rows = (await db.execute(sel(CalOccurrence).where(CalOccurrence.object_id == obj.id)
                                 .order_by(CalOccurrence.start_at))).scalars().all()
        row = next((r for r in rows if r.occurrence == want), None)
        if row is None:
            now = datetime.now(UTC)
            row = next((r for r in rows if r.end_at >= now), rows[-1] if rows else None)
    if row is not None:
        occurrence, all_day = row.occurrence, row.all_day
        s_at, e_at = (row.start_at.date(), row.end_at.date()) if all_day else (row.start_at, row.end_at)
    else:
        occurrence, all_day = first, obj.all_day
        s_at, e_at = (obj.start_date, obj.end_date) if obj.all_day else (obj.start_at, obj.end_at)
    out.update(occurrence=occurrence, all_day=all_day, starts_at=ical.out_when(s_at), ends_at=ical.out_when(e_at),
               date=ical.out_when(s_at), location=obj.location or None, url=calendar_url("event", obj.uid, occurrence))
    return out


# ---------------------------------------------------------------- notes


def note_url(pid: str, item: str | None = None) -> str:
    """The web route of a note, with an item focused (frontend/src/lib/routes.ts notePath)."""
    base = get_settings().base_url.rstrip("/")
    return f"{base}/notes/n/{urlquote(pid, safe='')}" + (f"#i-{urlquote(item, safe='')}" if item else "")


async def note_cite(db: AsyncSession, principal, pid: str, item: str | None) -> dict[str, Any] | None:
    """A cited note or item, if the caller may see it (else None: the marker is dropped). An item
    that is gone leaves the citation on the note."""
    from mailr.notes import access
    from mailr.notes.models import Note, NoteItem

    note = (await db.execute(select(Note).where(Note.pid == str(pid), access.visible_to(principal),
                                                Note.deleted_at.is_(None)))).scalar_one_or_none()
    if note is None:
        return None
    title = note.title or ("Untitled list" if note.kind == "checklist" else "Untitled note")
    snippet, item_id, checked = None, None, None
    if item:
        it = await db.get(NoteItem, item) if re.fullmatch(r"[0-9a-fA-F-]{36}", item) else None
        if it is not None and it.note_id == note.id and it.deleted_at is None:
            item_id, snippet, checked = it.id, it.text, bool(it.checked)
    if snippet is None and note.kind == "text":
        snippet = next((ln.strip() for ln in (note.body or "").splitlines() if ln.strip()), None)
    return {"kind": "note", "note_id": note.pid, "item_id": item_id, "title": title, "subject": title,
            "from": "List" if note.kind == "checklist" else "Note", "note_kind": note.kind,
            "snippet": (snippet or "")[:200] or None, "checked": checked, "conversation_id": None, "message_id": None,
            "quote": None, "verified": True, "start": None, "end": None, "color": note.color,
            "date": note.updated_at.isoformat() if note.updated_at else None, "url": note_url(note.pid, item_id)}


# ---------------------------------------------------------------- recordings


async def _recording_cite(db: AsyncSession, principal, rid: int, t: float | None, quote: str | None,
                          conns: list) -> dict[str, Any] | None:
    """A cited recording, if the caller may see it (else None: the marker is dropped). A quote is
    checked against the transcript within ±30 s of `t`; one that is not there stays unverified."""
    from mailr.integrations.speakr import client as sc
    from mailr.integrations.speakr import store

    try:
        conn, row = await store.find(db, principal, rid, conns=conns)
    except store.RecordingNotFound:
        return None
    q = (quote or "")[:MAX_QUOTE] or None
    verified, speaker, at = True, None, t
    if q:
        try:
            segs = await store.part(db, conn, row.recording_id, "transcript")
            verified, seg = store.verify_quote(segs, t, q)
        except sc.SpeakrError:
            verified, seg = False, None
        if seg is not None:
            speaker = seg["speaker"] or None
            if at is None and seg["t"] is not None:
                at = seg["t"]
    when = row.meeting_date or row.created_upstream
    title = row.title or "Recording"
    return {"kind": "recording", "recording_id": row.recording_id, "connector_id": conn.id, "title": title,
            "subject": title, "from": speaker or "Recording", "speaker": speaker, "conversation_id": None,
            "message_id": None, "meeting_date": when.isoformat() if when else None,
            "date": when.isoformat() if when else None, "quote": q, "verified": verified,
            "start": at, "t": at, "end": None, "url": store.deep_link(conn, row.recording_id, at)}


# ---------------------------------------------------------------- web pages


def web_source_url(url: str, sources: dict[str, dict[str, Any]], known: str | None) -> str | None:
    """The url a [[web:…]] marker points at, when the model read it this turn (else None)."""
    url = (url or "").strip().rstrip(".,;:!?")
    if not url.lower().startswith(("https://", "http://")):
        return None
    if url in sources:
        return url
    fixed = _repair(url, set(sources)) if sources else None
    if fixed:
        return fixed
    if known:
        return _repair(url, _known_urls(known))
    return None


def web_cite(url: str, info: dict[str, Any], quote: str | None = None) -> dict[str, Any]:
    from mailr.websearch.adapters import site_of

    site = info.get("site") or site_of(url)
    title = (info.get("title") or quote or site or url)[:300]
    return {"kind": "web", "url": url, "title": title, "site": site, "subject": title, "from": site,
            "date": info.get("published"), "provider": info.get("provider"), "quote": None, "verified": True,
            "conversation_id": None, "message_id": None, "start": None, "end": None}


# ---------------------------------------------------------------- tables


async def table_cite(db: AsyncSession, principal, pid: str, row_id: str | None) -> dict[str, Any] | None:
    """A cited table or row, if the caller may see it (else None: the marker is dropped). A row
    that is gone, or a table in Trash, drops the marker too. The chip shows the row's primary
    value (or the table's name) and opens the table with that row's form."""
    from mailr.tables import access, core
    from mailr.tables.models import DataTable, TableRow

    t = (await db.execute(select(DataTable).where(DataTable.pid == pid))).scalar_one_or_none()
    if t is None or t.deleted_at is not None or access.level(principal.user, t) == "none":
        return None
    if getattr(principal, "token", None) is not None and "notes" not in (principal.scopes or set()):
        return None
    base = get_settings().base_url.rstrip("/")
    out: dict[str, Any] = {"kind": "table", "table": t.pid, "row": None, "title": t.name or "Untitled table",
                           "subject": t.name or "Untitled table", "from": "Table", "local_only": bool(t.local_only),
                           "date": t.updated_at.isoformat() if t.updated_at else None, "conversation_id": None,
                           "message_id": None, "quote": None, "verified": True, "start": None, "end": None,
                           "url": f"{base}/tables/t/{t.pid}"}
    if row_id:
        r = await db.get(TableRow, row_id)
        if r is None or r.table_id != t.id or r.deleted_at is not None:
            return None
        cols = core.cols_map(await core.columns_of(db, t))
        title = core.row_title(t, cols, r)
        out.update(row=r.id, title=title, subject=title, **{"from": t.name or "Table"},
                   date=r.updated_at.isoformat() if r.updated_at else None, url=f"{base}/tables/t/{t.pid}/r/{r.id}")
    return out
