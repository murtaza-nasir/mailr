"""Passage index: messages split into short, searchable slices with exact offsets.

Search, the agent's `find_passages` tool, and citations all work on passages, so an answer can
point at the paragraph it relies on. Passages are built in the worker from Message.body_text
(quoted history already stripped); nothing here runs on a request path.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import Message, MessagePassage

TARGET = 600      # characters per passage
HARD_MAX = 900    # a single paragraph longer than this is split at sentences / words
OVERLAP = 80      # characters repeated at the start of the next passage (split paragraphs only)
MAX_PASSAGES = 24

MIN_PASSAGE = 80  # shorter fragments are folded into a neighbour
_SIGNOFF = re.compile(
    r"^[ \t]*(--[ \t]*$|__+$|(best|kind|warm|many)?[ \t]*(regards|wishes|thanks|thank you)[,.!]?[ \t]*$|"
    r"cheers[,.!]?[ \t]*$|sincerely[,.]?[ \t]*$|best[,.!]?[ \t]*$|thx[,.!]?[ \t]*$|"
    r"sent from my .{1,40}$|get outlook for .{1,40}$)", re.I | re.M)
_WS = re.compile(r"\s+")
_PARA = re.compile(r"\n\s*\n")
_SENT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    start: int
    end: int
    text: str


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip()


def content_hash(s: str) -> str:
    return hashlib.sha1(_norm(s).lower().encode()).hexdigest()


def _paragraph_spans(body: str) -> list[tuple[int, int]]:
    spans, pos = [], 0
    for m in _PARA.finditer(body):
        if m.start() > pos:
            spans.append((pos, m.start()))
        pos = m.end()
    if pos < len(body):
        spans.append((pos, len(body)))
    out = []
    for a, b in spans:
        # trim whitespace but keep offsets exact
        while a < b and body[a].isspace():
            a += 1
        while b > a and body[b - 1].isspace():
            b -= 1
        if b > a:
            out.append((a, b))
    return out


def _split_long(body: str, a: int, b: int) -> list[tuple[int, int]]:
    """Split one long paragraph into ≤ TARGET-ish pieces at sentence (else word) boundaries,
    with OVERLAP characters of carry-over between pieces."""
    out: list[tuple[int, int]] = []
    start = a
    while start < b:
        end = min(b, start + TARGET)
        if end < b:
            window = body[start:end]
            cut = None
            for m in _SENT.finditer(window):
                if m.start() > TARGET // 2:
                    cut = start + m.start()
            if cut is None:
                sp = window.rfind(" ", TARGET // 2)
                cut = start + sp if sp > 0 else end
            end = cut
        out.append((start, end))
        if end >= b:
            break
        nxt = max(start + 1, end - OVERLAP)
        sp = body.find(" ", nxt, end)
        start = sp + 1 if sp != -1 else end
        while start < b and body[start].isspace():
            start += 1
    return out


def content_end(body: str) -> int:
    """Where the message proper ends: before a trailing sign-off and signature block ("Best
    regards, / Name / Title", "-- ", "Sent from my phone"). Only a sign-off in the last 600
    characters counts, so a "thanks" in the middle of a message is kept."""
    tail_from = max(0, len(body) - 600)
    cut = None
    for m in _SIGNOFF.finditer(body, tail_from):
        cut = m.start()
        break
    if cut is None or cut < len(body) * 0.2:
        return len(body)
    return cut


def chunk(body: str) -> list[Chunk]:
    """Paragraph-aware chunks with exact offsets into `body`. The trailing signature is left
    out; short paragraphs are merged up to TARGET characters; long ones are split; fragments
    shorter than MIN_PASSAGE are folded into a neighbour. At most MAX_PASSAGES chunks."""
    if not body or not body.strip():
        return []
    body_end = content_end(body)
    pieces: list[tuple[int, int]] = []
    for a, b in _paragraph_spans(body[:body_end]):
        if b - a > HARD_MAX:
            pieces.extend(_split_long(body, a, b))
        else:
            pieces.append((a, b))
    merged: list[tuple[int, int]] = []
    for a, b in pieces:
        if merged and (b - merged[-1][0]) <= TARGET and (merged[-1][1] - merged[-1][0]) < TARGET // 2 + 100:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    # Fold short fragments into the previous (or next) passage.
    folded: list[tuple[int, int]] = []
    for a, b in merged:
        if folded and (b - a) < MIN_PASSAGE:
            folded[-1] = (folded[-1][0], b)
        elif folded and (folded[-1][1] - folded[-1][0]) < MIN_PASSAGE:
            folded[-1] = (folded[-1][0], b)
        else:
            folded.append((a, b))
    return [Chunk(a, b, body[a:b]) for a, b in folded[:MAX_PASSAGES]]


async def build_for_message(db: AsyncSession, m: Message, *, rebuild: bool = False) -> int:
    """Create the passages of one message (idempotent). Every message gets at least one passage
    (an empty-bodied message gets an empty one, embedded from its subject and sender), which is
    also how the backfill knows a message is done."""
    if rebuild:
        await db.execute(delete(MessagePassage).where(MessagePassage.message_id == m.id))
    elif (await db.execute(select(exists().where(MessagePassage.message_id == m.id)))).scalar():
        return 0
    chunks = chunk(m.body_text or "") or [Chunk(0, 0, "")]
    for i, c in enumerate(chunks):
        db.add(MessagePassage(user_id=m.user_id, message_id=m.id, conversation_id=m.conversation_id, idx=i,
                              start=c.start, end=c.end, text=c.text,
                              content_hash=content_hash((m.subject or "") + "\n" + m.from_email + "\n" + c.text)))
    await db.flush()
    return len(chunks)


async def messages_without_passages(db: AsyncSession, user_id: int, limit: int) -> list[Message]:
    return list((await db.execute(
        select(Message).where(Message.user_id == user_id, Message.deleted.is_(False), Message.draft.is_(False),
                              ~exists().where(MessagePassage.message_id == Message.id,
                                              MessagePassage.user_id == user_id))
        .order_by(Message.date.desc().nulls_last()).limit(limit))).scalars())
