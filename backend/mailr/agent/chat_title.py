"""Short titles for the chat list.

A chat starts with its first question as its title. While the first answer is being written, a
short title is asked of the chat's own model (the same task and sensitivity as the chat, so a
local-only chat never reaches a hosted model for it) and replaces the question, unless the user
has renamed the chat. A model that is unavailable or answers badly leaves the question in place."""

from __future__ import annotations

import logging
import re

from mailr.db import session_scope
from mailr.models import ChatSession, User

log = logging.getLogger("mailr.agent")

MAX_WORDS = 7
MAX_CHARS = 60
QUESTION_CHARS = 1500

PROMPT = (
    "You name chats in the chat list of an email, calendar and notes assistant. Reply with the "
    "title only: 2 to 6 words in the language of the user's message, sentence case, no quotes, "
    "no final punctuation, no emoji. Name the subject, not the form of the request: for example "
    "\"Fitness log entry\", \"This week's calendar\", \"Sous-vide pork loin note\", \"Emails "
    "from Dana about the lease\". If the message is text pasted from elsewhere, name what it is "
    "about. Never answer the message."
)


def clean(raw: str | None) -> str | None:
    """The model's reply as a title, or None when it is not one (empty, long, several lines)."""
    if not raw:
        return None
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if len(lines) != 1:
        return None
    t = re.sub(r"^(title|chat title)\s*:\s*", "", lines[0], flags=re.I)
    t = t.strip().strip("\"'“”‘’`*#").strip()
    t = re.sub(r"[.!?:;,]+$", "", t).strip()
    if not t or len(t) > MAX_CHARS or len(t.split()) > MAX_WORDS:
        return None
    return t[0].upper() + t[1:]


async def suggest(user_id: int, question: str, *, sensitive: bool) -> str | None:
    """A short title for a chat that starts with `question`, from the user's chat model."""
    from mailr.intel import llm

    question = (question or "").strip()[:QUESTION_CHARS]
    if not question:
        return None
    async with session_scope() as db:
        user = await db.get(User, user_id)
        try:
            out = await llm.chat(db, user, [{"role": "system", "content": PROMPT},
                                            {"role": "user", "content": question}],
                                 task="chat", feature="assistant", sensitive=sensitive, temperature=0.2,
                                 max_tokens=40, timeout=30,
                                 extra={"chat_template_kwargs": {"enable_thinking": False}})
        except Exception as e:  # noqa: BLE001  no model, local-only refusal, timeout: keep the question
            log.info("chat title: no title (%s)", type(e).__name__)
            return None
    return clean(out.get("content"))


async def retitle(user_id: int, session_id: str, question: str, *, sensitive: bool) -> str | None:
    """Give the chat a short title, unless the user has named it meanwhile. Returns the title set."""
    title = await suggest(user_id, question, sensitive=sensitive)
    if not title:
        return None
    async with session_scope() as db:
        s = await db.get(ChatSession, session_id)
        if s is None or s.user_id != user_id or s.title_custom:
            return None
        from sqlalchemy import cast, func, update
        from sqlalchemy.dialects.postgresql import JSONB

        # Merged into the context in SQL (the running turn writes the context too). The chat keeps
        # its place in the list (its last activity does not change).
        await db.execute(update(ChatSession).where(ChatSession.id == s.id).values(
            title=title, updated_at=ChatSession.updated_at,
            context=func.coalesce(ChatSession.context, cast({}, JSONB)).op("||")(cast({"auto_title": title}, JSONB))))
    return title


def automatic(context: dict | None) -> str | None:
    """The short title made for a chat, if any (what a cleared custom name goes back to)."""
    t = (context or {}).get("auto_title")
    return t if isinstance(t, str) and t else None
