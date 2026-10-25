"""Search over the user's Ambrose chats (GET /agent/sessions?q=) and the snippet each result shows.

What is searched: the chat's title, the user's questions and Ambrose's answers (chat_messages
rows with role user or assistant; tool results never). The query is the calendar's full-text
query (calendar/search.py `tsquery`: websearch syntax with the 'simple' configuration, the last
word a prefix) over `to_tsvector('simple', content)`, which the GIN index ix_chat_msg_fts holds.
When full text finds nothing, a substring match (ILIKE, the trigram index ix_chat_msg_trgm) is
tried: part of a word, an address, a file name.

Order: newest match first. A chat whose title matches is placed by its last activity; any other
by its newest matching message. Each result carries a snippet: the
text of its newest matching message as one plain line around the first hit (no Markdown, no
citation links), cut to about SNIPPET_CHARS characters, with the hits marked as parts
(`snippet_parts`: [{text, hit}]). Clients render the parts as text; there is no markup to escape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar.search import parse, tsquery
from mailr.models import ChatMessage, ChatSession

SNIPPET_CHARS = 160
# Characters of context kept before the first hit.
SNIPPET_LEAD = 48
SEARCHED_ROLES = ("user", "assistant")
# The newest questions and answers read before the index is asked (see _run).
RECENT_WINDOW = 3000


@dataclass
class Terms:
    words: list[str]  # free words as typed (quoted phrases keep their quotes)
    plain: str  # the words without quotes or negations, for the substring fallback
    stems: list[str]  # lower-case word parts, each matched at the start of a word


def terms(q: str) -> Terms:
    qr = parse(q)
    # Operators mean nothing here: `from:x` is searched as the words it holds.
    words = [*qr.words, *[f"{o}:{v}" for o, v, neg in qr.ops if not neg]]
    plain = " ".join(w.strip('"') for w in words if not w.startswith("-")).strip()
    stems = [x for w in words if not w.startswith("-") for x in re.split(r"[^\w]+", w.lower()) if x]
    return Terms(words, plain, list(dict.fromkeys(stems)))


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def owned(p) -> list[Any]:
    """The chats GET /agent/sessions lists: the actor's own (the signed-in user's, or exactly one
    token's), without background errands (those are in the activity log)."""
    token = ChatSession.token_id == p.token.id if p.token is not None else ChatSession.token_id.is_(None)
    return [ChatSession.user_id == p.user_id, token, ~ChatSession.context.has_key("errand")]


async def search(db: AsyncSession, p, q: str, limit: int) -> list[tuple[ChatSession, dict[str, Any]]]:
    """The matching chats, newest match first, each with what matched and its snippet."""
    t = terms(q)
    if not t.words:
        return []
    tq = tsquery(t.words)
    rows: list[tuple[ChatSession, int | None, bool]] = []
    if tq is not None:
        rows = await _run(db, p, limit,
                          func.to_tsvector("simple", ChatMessage.content).op("@@")(tq),
                          func.to_tsvector("simple", ChatSession.title).op("@@")(tq))
    substring = False
    if not rows and len(t.plain) >= 3:
        like = f"%{_like(t.plain)}%"
        rows = await _run(db, p, limit, ChatMessage.content.ilike(like), ChatSession.title.ilike(like))
        substring = True
    hit_ids = [mid for _, mid, _ in rows if mid is not None]
    texts: dict[int, tuple[str, str]] = {}
    if hit_ids:
        texts = {i: (role, content) for i, role, content in (await db.execute(
            select(ChatMessage.id, ChatMessage.role, ChatMessage.content).where(ChatMessage.id.in_(hit_ids)))).all()}
    needles = [t.plain] if substring else t.stems
    out = []
    for s, mid, in_title in rows:
        info: dict[str, Any] = {"match": "title", "snippet": None, "snippet_parts": None,
                                "title_parts": parts(s.title, needles, substring) if in_title else None}
        if mid is not None and mid in texts:
            role, content = texts[mid]
            snip = snippet(content, needles, substring)
            info.update({"match": "question" if role == "user" else "answer", "snippet": "".join(x["text"] for x in snip)
                         if snip else None, "snippet_parts": snip})
        out.append((s, info))
    return out


async def _run(db: AsyncSession, p, limit: int, msg_cond: Any, title_cond: Any) -> list[tuple[ChatSession, int | None, bool]]:
    """One search pass: the chats with the newest matches, at most `limit`.

    A word in nearly every message ("the", a one-letter prefix) would make the index return
    hundreds of thousands of rows to group. So the newest RECENT_WINDOW questions and answers are
    read first, in order: when they hold matches in `limit` chats, those are the answer (every chat
    left out has older matches). Otherwise the word is rarer, and the full-text index finds all of
    its matches cheaply. Chats whose title matches are merged in by their last activity."""
    mine = select(ChatSession.id).where(*owned(p))
    recent = (select(ChatMessage.session_id.label("sid"), ChatMessage.id.label("mid"),
                     ChatMessage.created_at.label("at"), msg_cond.label("hit"))
              .where(ChatMessage.role.in_(SEARCHED_ROLES), ChatMessage.session_id.in_(mine))
              .order_by(ChatMessage.id.desc()).limit(RECENT_WINDOW).subquery())
    found = (await db.execute(select(recent.c.sid, func.max(recent.c.mid), func.max(recent.c.at))
                              .where(recent.c.hit).group_by(recent.c.sid))).all()
    if len(found) < limit:
        found = (await db.execute(
            select(ChatMessage.session_id, func.max(ChatMessage.id), func.max(ChatMessage.created_at))
            .where(ChatMessage.role.in_(SEARCHED_ROLES), msg_cond, ChatMessage.session_id.in_(mine))
            .group_by(ChatMessage.session_id).order_by(func.max(ChatMessage.created_at).desc()).limit(limit))).all()
    hits = {sid: (mid, at) for sid, mid, at in found}
    titled = (await db.execute(select(ChatSession.id).where(*owned(p), title_cond)
                               .order_by(ChatSession.updated_at.desc()).limit(limit))).scalars().all()
    ids = set(hits) | set(titled)
    if not ids:
        return []
    rows = (await db.execute(select(ChatSession, title_cond.label("in_title")).where(ChatSession.id.in_(ids)))).all()

    def key(row: Any) -> Any:
        s, in_title = row
        return (s.updated_at if in_title else hits[s.id][1], s.id)

    rows = sorted(rows, key=key, reverse=True)[:limit]
    return [(s, hits[s.id][0] if s.id in hits else None, bool(it)) for s, it in rows]


# ---------------- snippet ----------------

_CITE_LINK = re.compile(r"\[\d+\]\(mailr-cite:\d+\)|\[\[[^\]]*\]\]")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_MARKS = re.compile(r"[*_`#>|~]+")


def plain_line(text: str) -> str:
    """Text as one plain line: no citation links, no Markdown marks, whitespace collapsed."""
    t = _CITE_LINK.sub("", text or "")
    t = _MD_LINK.sub(r"\1", t)
    t = _MD_MARKS.sub("", t)
    t = re.sub(r"^\s*[-+]\s+", "", t, flags=re.MULTILINE)
    return re.sub(r"\s+([.,;:!?])", r"\1", " ".join(t.split()))


def _hits(text: str, needles: list[str], substring: bool) -> list[tuple[int, int]]:
    """Where the needles occur in `text`: a full-text hit is the whole word that starts with a
    needle; a substring hit is the needle itself. Sorted, not overlapping."""
    found: list[tuple[int, int]] = []
    low = text.lower()
    for n in needles:
        if not n:
            continue
        if substring:
            for m in re.finditer(re.escape(n.lower()), low):
                found.append((m.start(), m.end()))
        else:
            for m in re.finditer(r"(?<!\w)" + re.escape(n) + r"\w*", low):
                found.append((m.start(), m.end()))
    found.sort()
    merged: list[tuple[int, int]] = []
    for a, b in found:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def parts(text: str, needles: list[str], substring: bool = False) -> list[dict[str, Any]]:
    """`text` cut into parts, the hits marked."""
    out: list[dict[str, Any]] = []
    at = 0
    for a, b in _hits(text, needles, substring):
        if a > at:
            out.append({"text": text[at:a], "hit": False})
        out.append({"text": text[a:b], "hit": True})
        at = b
    if at < len(text):
        out.append({"text": text[at:], "hit": False})
    return out


def snippet(text: str, needles: list[str], substring: bool = False, width: int = SNIPPET_CHARS) -> list[dict[str, Any]] | None:
    """A short plain line around the first hit, as parts. The words of a phrase that full text
    matched across Markdown may not be found in the plain line; the line then starts at the top."""
    line = plain_line(text)
    if not line:
        return None
    hits = _hits(line, needles, substring)
    first = hits[0][0] if hits else 0
    start = 0
    if first > SNIPPET_LEAD:
        start = first - SNIPPET_LEAD
        sp = line.find(" ", start, first)
        start = sp + 1 if sp >= 0 else start
    end = min(len(line), start + width)
    if end < len(line):
        sp = line.rfind(" ", max(first + 1, start + width // 2), end)
        end = sp if sp > 0 else end
    cut = line[start:end].strip()
    lead = "…" if start > 0 else ""
    tail = "…" if end < len(line) else ""
    body = parts(cut, needles, substring)
    if lead:
        body.insert(0, {"text": lead, "hit": False})
    if tail:
        body.append({"text": tail, "hit": False})
    # Neighbouring plain parts join (the ellipses).
    out: list[dict[str, Any]] = []
    for x in body:
        if out and not x["hit"] and not out[-1]["hit"]:
            out[-1] = {"text": out[-1]["text"] + x["text"], "hit": False}
        else:
            out.append(x)
    return out


# ---------------- rename ----------------

TITLE_MAX = 120


def clean_title(raw: str | None) -> str:
    """A title as typed: one line, spaces collapsed, trimmed."""
    return " ".join((raw or "").split())


async def automatic_title(db: AsyncSession, s: ChatSession) -> str:
    """The title a chat has when the user has not named it: the short title made for it
    (agent/chat_title.py), or else its first question."""
    from mailr.agent import chat_title
    from mailr.agent.loop import is_resume_note

    if made := chat_title.automatic(s.context):
        return made

    rows = (await db.execute(select(ChatMessage.content).where(ChatMessage.session_id == s.id, ChatMessage.role == "user")
                             .order_by(ChatMessage.id).limit(5))).scalars().all()
    first = next((c for c in rows if c and not is_resume_note(c)), None)
    return (first or s.title or "")[:300]

