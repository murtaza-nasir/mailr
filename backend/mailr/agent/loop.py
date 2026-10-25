"""The built-in assistant's tool loop.

The caller's principal is the agent's principal: a signed-in user gets their scopes (with sends
held for a click), a token gets the token's scopes and send gate.

Chat history lives on the server (ChatSession / ChatMessage). The client sends only the new user
turn and the session id, so history cannot be forged, and the session's local-only sensitivity is
sticky: once any tool result carried content from a local_only account (or a recalled memory
learned from one), the session is marked sensitive (persisted immediately) and every later model
call in it is made with sensitive=True — the router then refuses hosted endpoints.

A turn is a timeline of segments: the model's text, then the tool steps it ran, then more text,
and so on. Every event carries its `segment`; text that precedes tool calls is finalised (with
verified citations) as a `segment` event instead of being overwritten by the final answer, and each
round's steps are stored on that round's row, so a reloaded chat shows the same timeline.

Budget: at most MAX_ROUNDS model rounds, MAX_SECONDS of wall-clock time and MAX_TOOL_CHARS_TOTAL
characters of tool results. When the budget runs out the model is called once more without tools
and answers with what it found, saying what is missing (never a bare error).

The user row of each turn records its status (running → done | stopped | error). A run that dies
with the process stays "running" until the API starts again and marks it interrupted
(sweep_interrupted); the UI then offers Retry, which re-runs the turn (rerun_from).

Answers cite with [[m:ID "quote"]] / [[p:ID]] / [[c:ID]]; intel/citations.py verifies each against
the caller's visibility and the message text before the answer is stored and shown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.agent import chat_title, claims, commands, present, screen, when, working_set
from mailr.agent.prompts import assistant_system
from mailr.agent.tools import UNTRUSTED_TOOLS, ToolCtx, ToolError, available_tools, run_tool
from mailr.config import get_settings
from mailr.core import outbound
from mailr.core.visibility import touches_local_only
from mailr.intel import citations, llm
from mailr.models import ChatMessage, ChatSession, Conversation

log = logging.getLogger("mailr.agent")

MAX_ROUNDS = 16
MAX_SECONDS = 150.0
MAX_TOOL_CHARS = 24_000
MAX_TOOL_CHARS_TOTAL = 160_000
MAX_HISTORY = 60          # stored messages replayed to the model
MAX_HISTORY_TOOL_CHARS = 6_000
SEARCH_TOOLS = {"search_conversations", "search_semantic", "find_passages", "build_context", "recall"}
SEARCH_HINT_AFTER = 4
# A call repeated with the same arguments in one turn is never run again (its result cannot change).
# The first repeat gets the earlier result back with a plain note; at MAX_REPEATS the tools are
# taken away and the model answers from what it has (one search
# once repeated 13 times until the step limit).
MAX_REPEATS = 2
# Tool calls that may fail unexpectedly (a calendar server's error, a timeout, a bug) in one turn,
# each answered to the model as an error, before the turn itself ends with the failure.
MAX_TOOL_FAILURES = 3
REPEAT_EXEMPT = ("update_draft", "remember", "get_draft")

# About how many characters a token takes in a prompt of prose and JSON (a rough, cautious count).
CHARS_PER_TOKEN = 3.5

STOP_REASONS = {
    "rounds": "it reached the step limit for one question",
    "time": "it reached the time limit for one question",
    "size": "it read as much mail as fits in one answer",
    "repeats": "it asked for the same thing again with the same arguments",
}

# After the step or time limit, the answer from what was found gets this long of its own (as does
# the one call that asks again when a reply claims a change nothing confirms).
WRAP_UP_SECONDS = 60.0


async def bounded(events: AsyncIterator[dict[str, Any]], deadline: float,
                  late: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """A model stream's events until `deadline` (time.monotonic()): a stream that stalls past it
    stops there and sets late["late"], rather than when its read timeout ends."""
    it = events.__aiter__()
    try:
        while True:
            try:
                async with asyncio.timeout(max(0.05, deadline - time.monotonic())):
                    ev = await it.__anext__()
            except StopAsyncIteration:
                return
            except TimeoutError:
                late["late"] = True
                return
            yield ev
    finally:
        aclose = getattr(it, "aclose", None)
        if aclose is not None:
            await aclose()


class _OverBudget(Exception):
    """The wrap-up prompt does not fit the budget even with this turn's results cut down."""


# Session id → stop flag (single API process; a stop request for another process is harmless).
_STOPS: dict[str, asyncio.Event] = {}


def request_stop(session_id: str) -> bool:
    ev = _STOPS.get(session_id)
    if ev is None:
        return False
    ev.set()
    return True


def _read_text(convo: list[dict[str, Any]]) -> str:
    """Everything the model read in this chat (tool results and the user's own words): the
    only place a link in its answer may come from (citations.verify_links)."""
    return "\n".join(str(m.get("content") or "") for m in convo if m.get("role") in ("tool", "user", "system"))


def _cut(text: str, cap: int) -> str:
    """The head and the tail: a thread's messages come oldest first, so its newest stay in view."""
    if len(text) <= cap:
        return text
    head = cap * 3 // 5
    return (text[:head] + f" … [{len(text) - cap} characters left out here; narrow the request] … "
            + text[len(text) - (cap - head):])


def _tool_message(call_id: str, payload: Any, cap: int = MAX_TOOL_CHARS) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": _cut(json.dumps(payload, default=str, ensure_ascii=False), cap)}


def prompt_tokens(convo: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> int:
    """About how many tokens a model call takes: its messages and its tool definitions."""
    n = sum(len(json.dumps(m, default=str, ensure_ascii=False)) for m in convo)
    if tools:
        n += len(json.dumps(tools, default=str, ensure_ascii=False))
    return int(n / CHARS_PER_TOKEN)


def fit_history(convo: list[dict[str, Any]], history: int, budget: int,
                tools: list[dict[str, Any]] | None = None) -> int:
    """Leave out the oldest earlier turns (`history` messages after the system prompt) until the
    prompt fits `budget`, a whole turn at a time. Returns how many messages were left out."""
    dropped = 0
    while history > 0 and prompt_tokens(convo, tools) > budget:
        del convo[1]
        dropped, history = dropped + 1, history - 1
        while history > 0 and convo[1]["role"] != "user":  # the rest of that turn
            del convo[1]
            dropped, history = dropped + 1, history - 1
    return dropped


def shrink_results(convo: list[dict[str, Any]], start: int, budget: int) -> bool:
    """Cut this turn's tool results (from `start`) to smaller head-and-tail extracts until the
    prompt fits `budget`. False when even the smallest extracts do not fit."""
    cap = MAX_TOOL_CHARS
    while prompt_tokens(convo) > budget:
        cap //= 2
        if cap < 500:
            return False
        for i in range(start, len(convo)):
            m = convo[i]
            if m.get("role") == "tool" and len(m.get("content") or "") > cap:
                convo[i] = {**m, "content": _cut(m["content"], cap)}
    return True


def over_budget_text(need: int, budget: int) -> str:
    return (f"This question needs more than the assistant's model can read at once (about {need:,} tokens; "
            f"the limit is {budget:,}). Ask about fewer messages or a shorter period, or start a new chat.")


def context_overflow(e: Exception) -> bool:
    """A model endpoint's refusal of a prompt longer than its context window."""
    t = f"{e} {getattr(e, 'detail', '')}".lower()
    return ("context" in t and any(w in t for w in ("length", "window", "too long", "maximum", "exceed"))) \
        or "too many tokens" in t or "prompt is too long" in t


def repeat_note(name: str, first_content: str, *, last: bool) -> dict[str, Any]:
    """What the model reads when it repeats a call: the earlier result again, said plainly, and
    where to go next. `last`: the tools are taken away after this round."""
    try:
        earlier: Any = json.loads(first_content)
    except ValueError:
        earlier = first_content
    note = (f"You already called {name} with exactly these arguments in this question. The result is the same "
            "as before, and it is repeated below. Calling it again will not change it. ")
    note += ("This was the last repeat: answer now from the results you have, and say what is missing."
             if last else
             "Do not call it again. Use a different tool or different arguments (for tasks: list_tasks with "
             "smart=upcoming, or search_tasks without `text`), or answer now from what you have.")
    return {"repeated_call": True, "note": note, "same_result_as_before": earlier}


async def result_is_sensitive(db: AsyncSession, user_id: int, res) -> bool:
    """True if a tool result carried content from a local_only account."""
    if getattr(res, "sensitive", False):
        return True
    if res.message_ids and await touches_local_only(db, user_id, message_ids=res.message_ids):
        return True
    if res.conversation_ids and await touches_local_only(db, user_id, conversation_ids=res.conversation_ids):
        return True
    return False


def renumber(text: str, cites: list[dict[str, Any]], offset: int) -> tuple[str, list[dict[str, Any]]]:
    """Shift citation numbers so the segments of one turn share one numbering."""
    if not offset:
        return text, cites
    text = re.sub(r"\[(\d+)\]\(mailr-cite:(\d+)\)", lambda m: f"[{int(m.group(1)) + offset}](mailr-cite:"
                  f"{int(m.group(2)) + offset})", text)
    return text, [{**c, "n": int(c.get("n") or 0) + offset} for c in cites]


# ---------------------------------------------------------------- step labels

# The words and icons of tool steps live in the presentation layer (agent/present.py). A step
# carries its label and summary in English and, as msgs, `label_msg` and `summary_msg`, which the
# apps render in their own language.
step_label = present.step_label
result_label = present.result_label


def _with_msg(step: dict[str, Any], key: str, m: Any) -> str:
    """The English of `m`, with the msg set as step[key] (or removed when `m` is plain text)."""
    if present.i18n.is_msg(m):
        step[key] = m
    else:
        step.pop(key, None)
    return present.en(m)


# ---------------------------------------------------------------- sessions

def split_context(context: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """(what is kept on the session, the per-turn screen state)."""
    c = dict(context or {})
    ui = screen.sanitize(c.pop("ui", None) or {})
    keep: dict[str, Any] = {}
    if c.get("conversation_id") not in (None, ""):
        try:
            keep["conversation_id"] = int(c["conversation_id"])
        except (TypeError, ValueError):
            pass
    elif "conversation_id" in c:
        keep["conversation_id"] = None
    if ui.get("conversation_id") and not keep.get("conversation_id"):
        keep["conversation_id"] = ui["conversation_id"]
    # "Ask assistant" from an event or a task (agent/screen.py): kept on the session.
    if (ev := screen.focused_event(c.get("focused_event"))) is not None:
        keep["focused_event"] = ev
    if (tk := screen.focused_task(c.get("focused_task"))) is not None:
        keep["focused_task"] = tk
    # ACT agent.ask_about @assistant: "Ask assistant" in the note editor, kept on the session the same way.
    if (fn := screen.focused_note(c.get("focused_note"))) is not None:
        keep["focused_note"] = fn
    # ACT recording.ask @assistant: "Ask assistant" from the recording sheet, kept on the session the same way.
    if (rec := screen.focused_recording(c.get("focused_recording"))) is not None:
        keep["focused_recording"] = rec
    # ACT agent.ask_about @assistant: "Ask" from an approval card, kept the same way.
    if (aid := screen.focused_approval(c.get("focused_approval"))) is not None:
        keep["focused_approval"] = aid
    return keep, ui


async def get_session(db: AsyncSession, p, session_id: str | None, context: dict[str, Any] | None,
                      title: str) -> ChatSession:
    if session_id:
        s = await db.get(ChatSession, session_id)
        if s is None or s.user_id != p.user_id or s.token_id != (p.token.id if p.token else None):
            raise LookupError("unknown chat session")
        if context:
            s.context = {**(s.context or {}), **context}
        return s
    s = ChatSession(id=secrets.token_urlsafe(16), user_id=p.user_id, token_id=p.token.id if p.token else None,
                    title=title[:300], sensitive=False, context=context or {})
    db.add(s)
    await db.flush()
    return s


async def _history(db: AsyncSession, s: ChatSession, before_id: int | None = None) -> list[dict[str, Any]]:
    q = select(ChatMessage).where(ChatMessage.session_id == s.id)
    if before_id is not None:
        q = q.where(ChatMessage.id < before_id)
    rows = list((await db.execute(q.order_by(ChatMessage.id.desc()).limit(MAX_HISTORY))).scalars())[::-1]
    # Never start the replay in the middle of a tool exchange.
    while rows and rows[0].role == "tool":
        rows.pop(0)
    out: list[dict[str, Any]] = []
    for r in rows:
        if r.role == "tool":
            out.append({"role": "tool", "tool_call_id": r.tool_call_id,
                        "content": (r.content or "")[:MAX_HISTORY_TOOL_CHARS]})
        elif r.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": r.content or None}
            if r.tool_calls:
                msg["tool_calls"] = r.tool_calls
            out.append(msg)
        else:
            out.append({"role": "user", "content": r.content})
    # A tool call without its result (an interrupted round) would make the request invalid.
    answered = {m["tool_call_id"] for m in out if m["role"] == "tool"}
    for m in out:
        if m["role"] == "assistant" and m.get("tool_calls"):
            kept = [c for c in m["tool_calls"] if c.get("id") in answered]
            if kept:
                m["tool_calls"] = kept
            else:
                m.pop("tool_calls")
    return [m for m in out if not (m["role"] == "assistant" and not m.get("content") and not m.get("tool_calls"))]


async def history_untrusted(db: AsyncSession, s: ChatSession, before_id: int | None = None) -> bool:
    """True when the history replayed to the model holds results of tools that return mail
    content: text read in an earlier turn is still in the model's context. A recall result counts
    when it named correspondents (their display names are marked untrusted)."""
    marked = ChatMessage.content.contains('"untrusted": true')
    q = select(ChatMessage.role, ChatMessage.name, marked).where(ChatMessage.session_id == s.id)
    if before_id is not None:
        q = q.where(ChatMessage.id < before_id)
    rows = (await db.execute(q.order_by(ChatMessage.id.desc()).limit(MAX_HISTORY))).all()
    return any(role == "tool" and (name in UNTRUSTED_TOOLS or (name == "recall" and bool(mark)))
               for role, name, mark in rows)


async def _approval_notes(db: AsyncSession, s: ChatSession, *, wait_for: int | None = None,
                          wait_seconds: float = 15.0, decided: list | None = None) -> list[str]:
    """Outcomes of this session's approvals that the model has not been told about yet.
    With `wait_for`, first wait (briefly) for that approval to finish executing. `decided`
    collects the decided PendingAction rows (their cards change in place)."""
    from mailr.models import PendingAction

    ids = [int(x) for x in (s.context or {}).get("pending", [])]
    told = [int(x) for x in (s.context or {}).get("told", [])]
    if wait_for is not None and wait_for not in ids and wait_for not in told:
        # An approval of this chat that did not come from a tool call of a turn (a review card,
        # agent/review.py) and is not listed, for instance when a turn rewrote the context
        # meanwhile: it is still this chat's to report, once.
        pa = await db.get(PendingAction, wait_for)
        if pa is not None and pa.chat_session_id == s.id:
            ids.append(wait_for)
    if wait_for is not None and wait_for in ids:
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            pa = await db.get(PendingAction, wait_for)
            await db.refresh(pa)
            if pa.status not in ("approved", "executing"):
                break
            await asyncio.sleep(0.5)
    notes, still = [], []
    for pid in ids:
        pa = await db.get(PendingAction, pid)
        if pa is None:
            continue
        await db.refresh(pa)
        title = (pa.preview or {}).get("title", pa.action)
        if pa.status in ("pending", "approved", "executing"):
            still.append(pid)
            continue
        verdict = {"executed": "approved and done", "denied": "denied by the user", "expired": "expired unanswered",
                   "failed": "approved but it failed"}.get(pa.status, pa.status)
        result = pa.result
        if result and pa.action.startswith(("calendar.", "tasks.")):
            # The assistant names calendars by their labels, never by URL or id (calendar/describe.py).
            from mailr.calendar.describe import for_model

            result = await for_model(db, s.user_id, result)
        res = json.dumps(result, default=str)[:600] if result else ""
        notes.append(f"Approval {pa.id} ({title}): {verdict}." + (f" Result: {res}" if res else ""))
        told.append(pa.id)
        if decided is not None:
            decided.append(pa)
    # The approvals already reported (the last 50), so a second resume does not report one again.
    s.context = {**(s.context or {}), "pending": still, "told": told[-50:]}
    return notes


async def _turn_tail(db: AsyncSession, s: ChatSession) -> tuple[int, int]:
    """(next segment, citations so far) of the session's last turn, for a continuation."""
    rows = list((await db.execute(select(ChatMessage).where(ChatMessage.session_id == s.id)
                                  .order_by(ChatMessage.id.desc()).limit(MAX_HISTORY))).scalars())
    seg, cites, n = -1, 0, 0
    for r in rows:  # newest first, back to the turn's own user message
        if r.role == "user" and not (r.meta or {}).get("resume") and not is_resume_note(r.content):
            break
        if r.role == "assistant":
            n += 1
            seg = max(seg, int((r.meta or {}).get("segment") or 0))
            cites += len(r.citations or [])
    return (seg + 1 if n else 0), cites


RESUME_NOTE = "[mailr: approval updates"


def is_resume_note(text: str | None) -> bool:
    return (text or "").startswith(RESUME_NOTE)


async def sweep_interrupted(db: AsyncSession) -> int:
    """At API start-up: turns still marked running belong to a process that is gone."""
    res = await db.execute(update(ChatMessage).where(ChatMessage.status == "running")
                           .values(status="interrupted").returning(ChatMessage.id))
    n = len(res.all())
    await db.commit()
    return n


async def truncate_from(db: AsyncSession, s: ChatSession, message_id: int, *, keep: bool) -> ChatMessage:
    """Drop a turn and everything after it (keep=False, edit and resend), or everything after
    its user message (keep=True, re-run it). Returns the user row."""
    row = await db.get(ChatMessage, message_id)
    if row is None or row.session_id != s.id or row.role != "user":
        raise LookupError("that message is not a user message of this chat")
    cut = row.id if keep else row.id - 1
    await db.execute(delete(ChatMessage).where(ChatMessage.session_id == s.id, ChatMessage.id > cut))
    await db.flush()
    return row


async def _suggest_memories(db: AsyncSession, p, s: ChatSession, user_row: ChatMessage, body_text: str,
                            steps: list[dict[str, Any]]) -> None:
    """After a finished turn: when the user's message looks like a durable statement ("I'm
    vegetarian", "Dana is my accountant") and Ambrose saved nothing itself, queue a small model
    call that may propose it as a suggested memory (knowledge/tasks.suggest_from_chat)."""
    from mailr.knowledge.tasks import looks_like_statement

    if p.is_token or not body_text or any(st.get("name") == "remember" for st in steps):
        return
    if not looks_like_statement(body_text):
        return
    from mailr.core.jobs import enqueue

    try:
        await enqueue(db, "kb.chat_suggest", {"user_id": p.user_id, "session_id": s.id, "message_id": user_row.id},
                      user_id=p.user_id, dedupe_key=f"kb.chat_suggest:{user_row.id}", max_attempts=2)
        await db.commit()
    except Exception as e:  # noqa: BLE001 - a suggestion must never fail a finished turn
        log.warning("could not queue memory suggestions: %s", e)
        await db.rollback()


# ---------------------------------------------------------------- prompt assembly

async def build_system(db: AsyncSession, p, s: ChatSession, user_text: str, ui: dict[str, Any],
                       invoked: list) -> tuple[str, dict[str, Any]]:
    """The system prompt for this turn and what went into it: {"skills", "memory_ids",
    "sensitive", "untrusted"}. sensitive: a recalled note, an open draft, the chat's or the screen's
    conversation, or an approval card carries local-only content. untrusted: the prompt holds text
    other people wrote (subjects, senders, correspondents matching a name in the request, a brief,
    the screen's conversations, events, tasks, recordings, an approval), so the turn is treated as having read mail (ToolCtx.untrusted).
    Conversations and briefs reach the prompt only when the caller may see them (core/visibility.py)."""
    from mailr.core.visibility import conversation_visible, sees_all_mail
    from mailr.knowledge import memory, profile, skills

    prof = await profile.get(db, p.user_id)
    opts = profile.options(prof)
    info: dict[str, Any] = {"skills": [], "memory_ids": [], "sensitive": False, "untrusted": False}
    everything = await skills.all_for(db, p.user_id, enabled_only=True)
    by_name = {x.name: x for x in everything}
    active = list(invoked)
    active += [by_name[n] for n in (s.context or {}).get("skills", []) if n in by_name and by_name[n] not in active]
    active += [x for x in await skills.auto_for(db, p.user_id, "chat") if x not in active]
    info["skills"] = [x.name for x in active]
    conv_id = (s.context or {}).get("conversation_id")
    conv_subject = None
    conv_brief = None
    emails: list[str] = screen.emails(ui)
    if conv_id and await conversation_visible(db, p, conv_id):
        c = await db.get(Conversation, int(conv_id))
        conv_subject = c.subject
        emails += [str(x.get("email", "")).lower() for x in c.participants or [] if not x.get("me")]
        if await touches_local_only(db, p.user_id, conversation_ids=[c.id]):
            info["sensitive"] = True  # its subject and participants are in the prompt
    open_cid = ui.get("conversation_id") or conv_id  # the brief of the email on screen
    if open_cid and not await conversation_visible(db, p, open_cid):
        open_cid = None
    # An email that is only on screen (the chat is not about it) is named by id: its subject and
    # brief are other people's text and would make the turn untrusted for an unrelated question.
    about_open = open_cid is not None and str(open_cid) == str(conv_id)
    has_brief = False
    if ui.get("conversation_id") and str(ui.get("conversation_id")) != str(conv_id):
        conv_subject = None
    if open_cid and sees_all_mail(p):
        from mailr.assistant.errands import latest_brief

        br = await latest_brief(db, p.user_id, int(open_cid))
        conv_brief = br.output if br is not None and about_open else None
        has_brief = br is not None
    if about_open and open_cid and await touches_local_only(db, p.user_id, conversation_ids=[int(open_cid)]):
        info["sensitive"] = True
    mem_text = ""
    mem_prep = None
    if opts.get("auto_recall", True):
        allow_sensitive = s.sensitive or await llm.available(db, p.user, "chat", sensitive=True)
        # The index of what notes exist names sensitive people and topics only when the chat runs
        # on a local model anyway.
        try:
            index_sensitive = s.sensitive or (await llm.resolve(db, p.user, "chat", sensitive=False, probe=True)).local
        except llm.NoModel:
            index_sensitive = False
        # Started before the rest of the prompt: the decision model checks the candidate notes
        # (knowledge/relevance.py) while the screen and the working set are described.
        # Correspondents known only from local-only mail are named only to a local model.
        from mailr.api.org_routes import include_local

        mem_prep = await memory.prepare_context(db, p, query=user_text, emails=emails[:12],
                                                conversation_id=int(conv_id) if conv_id else None,
                                                allow_sensitive=allow_sensitive,
                                                query_sensitive=bool(s.sensitive or info["sensitive"]),
                                                index=True, index_sensitive=index_sensitive,
                                                people_local=index_sensitive and include_local(p))
    apps_more = await screen.describe_apps(db, p, ui)
    # The mail and the Notes app on screen, by ids only: nobody's text, so they leave the turn
    # trusted (agent/screen.py). read_screen and get_note read the text when it is needed.
    mail_more = await screen.describe_mail(db, p, ui)
    notes_more = await screen.notes_block(db, p, ui)
    ids = [w["draft_id"] for w in ui.get("compose") or [] if w.get("draft_id")] + \
          [w["in_reply_to_message_id"] for w in ui.get("compose") or [] if w.get("in_reply_to_message_id")]
    if ids and await touches_local_only(db, p.user_id, message_ids=ids):
        info["sensitive"] = True
    if ui.get("focused_approval") and await screen.approval_touches_local_only(db, p, ui["focused_approval"]):
        info["sensitive"] = True
    # Text other people wrote is in the prompt: the turn starts untrusted, exactly as if a tool had
    # returned it (subjects and briefs carry injected instructions as well as message bodies do).
    people_block = ""
    if mem_prep is not None:
        mem_text, minfo = await memory.finish_context(db, p, mem_prep)
        info["memory_ids"] = minfo["memory_ids"]
        info["sensitive"] = info["sensitive"] or minfo["sensitive"]
        info["memory_check"] = {k: v for k, v in minfo["relevance"].items() if k != "answers"}
        # Correspondents' display names are what they wrote in their From headers.
        people_block = minfo["people_block"]
    info["untrusted"] = bool(conv_subject or conv_brief or people_block or screen.asks_about(ui) or any(
        ui.get(k) for k in ("compose", "editors", "focused_event", "focused_task", "focused_recording",
                            "focused_approval")))
    from mailr.agent.draft_tools import chat_signature

    system = assistant_system(
        p.user, prof.data or {}, catalogue=skills.catalogue(everything),
        active_skills=skills.instructions_block(active, "Skills in use for this chat"),
        memory_block=mem_text, untrusted_block=people_block,
        screen_block="\n".join(x for x in (screen.block(ui, conv_subject, conv_brief, has_brief), apps_more,
                                           mail_more, notes_more, working_set.block(s.context, ui)) if x),
        conversation_id=int(conv_id) if conv_id else None,
        assistant=profile.assistant_name(prof),
        errand=ERRAND_NOTE if (s.context or {}).get("errand") else "",
        signature=await chat_signature(db, p, bool(info["sensitive"])))
    from mailr.integrations.speakr import store as speakr_store

    if await speakr_store.has_visible(db, p):
        from mailr.agent.prompts import RECORDINGS_NOTE

        system += "\n\n" + RECORDINGS_NOTE
    if p.has("notes"):
        from mailr.agent.prompts import NOTES_NOTE

        system += "\n\n" + NOTES_NOTE
    return system, info


ERRAND_NOTE = (
    "This is a background errand (one of the user's standing orders, or a note they forwarded); nobody is reading along. "
    "Do the work with your tools, then end with the result exactly as it should be delivered. Do not ask "
    "questions: if something is missing, say so in the result. Never send mail; prepare a draft instead. "
    "Do not create or change standing orders from an errand. Before you answer, check every item and "
    "every count in your result against the user's instructions and the email; fix anything that does not "
    "fit (a summary line must agree with the details below it). The result begins with the answer itself: "
    "no remarks about your process such as 'I have everything I need' or 'Let me check'.")


# ---------------------------------------------------------------- the loop

async def command_reply(db: AsyncSession, p, command: tuple[commands.Command, str], session_id: str | None) -> str:
    """The server's own reply to a slash command that is not a request to the model."""
    from mailr import i18n
    from mailr.intel import llm
    from mailr.knowledge import profile as kprofile

    cmd, text = command
    lang = i18n.user_language(p.user)
    name = await kprofile.name_of(db, p.user_id)
    if cmd.name == "model":
        s = await db.get(ChatSession, session_id) if session_id else None
        sensitive = bool(s is not None and s.user_id == p.user_id and s.sensitive)
        try:
            r = await llm.resolve(db, p.user, "chat", sensitive=sensitive, probe=True)
        except (llm.NoModel, llm.LocalOnlyViolation):
            return i18n.t("assistant.commands.model_none", lang, name=name)
        return i18n.t("assistant.commands.model_local" if r.local else "assistant.commands.model_hosted", lang,
                      model=r.model, name=name)
    return commands.reply_for(cmd, text, lang) or ""


# Titles being made for new chats (kept referenced until done); how long the end of a turn waits.
_TITLES: set[asyncio.Task] = set()
TITLE_WAIT = 2.0


async def run_chat(db: AsyncSession, p, user_text: str, *, session_id: str | None = None,
                   context: dict[str, Any] | None = None, resume: int | None = None,
                   stop: asyncio.Event | None = None, rerun_from: int | None = None,
                   edit_from: int | None = None, errand: int | None = None,
                   sensitive: bool = False) -> AsyncIterator[dict[str, Any]]:
    """`errand`: a background run for a standing order (assistant/errands.py). Only the server
    sets it; the session is hidden from the chat list and its memory writes are suggestions.
    `sensitive`: the first message itself carries local-only content (an errand's prompt lists a
    local-only conversation): the session is sensitive before the first model call."""
    from mailr.core import access
    from mailr.knowledge import skills

    if not (await access.for_user(db, p.user)).feature("assistant"):
        # The access policy turns the assistant off (chat, standing orders' errands, routines).
        await access.audit_refusal(p.user_id, "feature:assistant", "off in the access policy")
        from mailr.knowledge import profile as kprofile

        name = await kprofile.name_of(db, p.user_id)
        yield {"type": "error", "message": access.no_assistant_message(name), "code": "no_access"}
        return
    user_text = (user_text or "").strip()[:20_000]
    keep_ctx, ui = split_context(context)
    if errand is not None:
        keep_ctx["errand"] = int(errand)
    if rerun_from is not None or edit_from is not None:
        if not session_id:
            yield {"type": "error", "message": "Re-running needs the chat's session id."}
            return
    elif not user_text and resume is None:
        yield {"type": "error", "message": "Send a message to start."}
        return
    if resume is not None and session_id is None:
        yield {"type": "error", "message": "Resuming needs the chat's session id."}
        return
    inv = await skills.resolve(db, p.user_id, user_text) if user_text else skills.Invocation(text="")
    if inv.problems:
        await db.rollback()
        yield {"type": "error", "message": " ".join(inv.problems)}
        return
    # A slash command (agent/commands.py): answered here without a model, or turned into a request.
    command = commands.parse(user_text) if rerun_from is None and resume is None else None
    if command is not None and (command[0].kind != "ask" or commands.missing_text(*command)):
        await db.rollback()
        yield {"type": "error", "code": "command", "message": await command_reply(db, p, command, session_id)}
        return
    try:
        s = await get_session(db, p, session_id, keep_ctx, user_text or "Approval update")
    except LookupError:
        yield {"type": "error", "message": "That chat has expired. Start a new one."}
        return
    # The event or task this chat was opened about ("Ask assistant"), unless the screen focuses another.
    ui = screen.with_focus(ui, (s.context or {}).get("focused_event"), (s.context or {}).get("focused_task"),
                           (s.context or {}).get("focused_recording"), (s.context or {}).get("focused_approval"))
    ui = screen.with_note(ui, (s.context or {}).get("focused_note"))
    user_row: ChatMessage | None = None
    try:
        if rerun_from is not None:
            user_row = await truncate_from(db, s, int(rerun_from), keep=True)
            user_text = user_row.content
            inv = await skills.resolve(db, p.user_id, user_text)
            command = commands.parse(user_text)
        elif edit_from is not None:
            await truncate_from(db, s, int(edit_from), keep=False)
            if not user_text:
                yield {"type": "error", "message": "Write the new message."}
                return
            # The first question edited: the automatic title follows it; a title the user gave stays.
            if not s.title_custom and not (await db.execute(select(ChatMessage.id).where(
                    ChatMessage.session_id == s.id, ChatMessage.role == "user").limit(1))).first():
                s.title = user_text[:300]
                s.context = {k: v for k, v in (s.context or {}).items() if k != "auto_title"}
    except LookupError as e:
        yield {"type": "error", "message": str(e).capitalize() + "."}
        return
    if inv.skills:
        s.context = {**(s.context or {}), "skills": list(dict.fromkeys(
            [*(s.context or {}).get("skills", []), *inv.names]))[-4:]}
    decided: list = []
    notes = await _approval_notes(db, s, wait_for=resume, decided=decided)
    body_text = user_text
    model_text = inv.text if inv.skills else user_text
    if inv.skills and not model_text:
        model_text = "Apply the skill to what we are working on."
    if command is not None and command[0].kind == "ask":
        # What the user typed stays in the chat; the model reads the request (the skills still apply).
        model_text = commands.request_for(command[0], (commands.parse(inv.text) or command)[1])
    if notes:
        # The outcome of approvals reaches the model as a clearly marked note from mailr.
        note = "[mailr: approval updates, not written by the user]\n" + "\n".join(f"- {n}" for n in notes)
        model_text = f"{note}\n\n{model_text}" if model_text else note + "\nContinue: tell the user the outcome briefly."
        if not user_text:
            user_text = model_text
    elif resume is not None and not user_text:
        from mailr.models import PendingAction

        pa = await db.get(PendingAction, resume)
        if pa is not None and pa.user_id != p.user_id:
            pa = None  # another user's approval: nothing about it, not even whether it waits
        waiting = pa is None or pa.status in ("pending", "approved", "executing")
        yield {"type": "error", "message": "That approval is still waiting." if waiting
               else "The chat already has that approval's outcome."}
        return
    stop = stop or asyncio.Event()
    _STOPS[s.id] = stop
    started = time.monotonic()
    status: str | None = None
    stop_reason: str | None = None
    title_task: asyncio.Task | None = None

    async def title_event() -> AsyncIterator[dict[str, Any]]:
        """The short title, when it is ready by the end of the turn (it is usually long done)."""
        if title_task is None:
            return
        await asyncio.wait({title_task}, timeout=TITLE_WAIT)
        if title_task.done() and not title_task.cancelled() and title_task.exception() is None and title_task.result():
            yield {"type": "title", "title": title_task.result()}

    try:
        # A continuation after an approval (no words from the user) joins the turn before it:
        # its segments and citation numbers follow that turn's.
        continuation = resume is not None and not body_text and bool(notes)
        first_segment, first_cite = (await _turn_tail(db, s)) if continuation else (0, 0)
        if user_row is None:
            meta: dict[str, Any] = {"skills": inv.names} if inv.names else {}
            if continuation:
                meta["resume"] = [pa.id for pa in decided]
            user_row = ChatMessage(session_id=s.id, role="user", content=user_text, status="running",
                                   meta=meta or None)
            db.add(user_row)
        else:
            user_row.status, user_row.meta = "running", ({"skills": inv.names} if inv.names else None)
        await db.commit()
        system, info = await build_system(db, p, s, model_text, ui, inv.skills)
        # The request's taint scope (core/outbound.py): anything local-only read so far is in the
        # prompt's reach, so the session keeps it.
        if (info["sensitive"] or sensitive or outbound.tainted()) and not s.sensitive:
            s.sensitive = True
        # The first question: a short title for the chat list is made while the answer is written,
        # with the chat's own sensitivity (agent/chat_title.py).
        if (errand is None and not continuation and not s.title_custom and body_text
                and not chat_title.automatic(s.context)
                and not (await db.execute(select(ChatMessage.id).where(
                    ChatMessage.session_id == s.id, ChatMessage.role == "user",
                    ChatMessage.id < user_row.id).limit(1))).first()):
            title_task = asyncio.create_task(chat_title.retitle(p.user_id, s.id, model_text if command else
                                                                (inv.text or body_text),
                                                                sensitive=bool(s.sensitive)))
            _TITLES.add(title_task)
            title_task.add_done_callback(_TITLES.discard)
        if info["memory_ids"]:
            # Kept with the turn: its "Used memories" line lists them when the chat is opened again.
            user_row.meta = {**(user_row.meta or {}), "memory_ids": info["memory_ids"]}
        if info.get("memory_check", {}).get("candidates"):
            # What the relevance gate did (counts and timing, no note text).
            user_row.meta = {**(user_row.meta or {}), "memory_check": info["memory_check"]}
        await db.commit()
        yield {"type": "session", "session_id": s.id, "title": s.title, "user_message_id": user_row.id,
               "skills": info["skills"], "memory_ids": info["memory_ids"],
               **({"continuation": True, "segment": first_segment} if continuation else {})}
        # Decided approvals: their cards change in place ("Approved · moved 6 messages to Trash").
        for pa in decided:
            yield {"type": "approval_update", "approval": await present.approval_card(db, pa)}

        from mailr.agent import web_tools
        from mailr.agent.speakr_tools import hidden_tools

        hidden = await hidden_tools(db, p) | await web_tools.hidden_tools(db, p)
        if "web_search" not in hidden:
            from mailr.agent.prompts import WEB_NOTE

            system += "\n\n" + WEB_NOTE
        history = await _history(db, s, before_id=user_row.id)
        convo: list[dict[str, Any]] = [{"role": "system", "content": system}, *history,
                                       {"role": "user", "content": model_text}]
        tctx = ToolCtx(db=db, p=p, assistant=not p.is_token, chat_session_id=s.id, ui=ui,
                       chat_context=dict(s.context or {}), session_sensitive=s.sensitive,
                       untrusted=errand is not None or info["untrusted"]
                       or await history_untrusted(db, s, before_id=user_row.id),
                       known=lambda: _read_text(convo),
                       user_urls=web_tools.user_urls(body_text) if errand is None else set())
        tools = [t.schema(p.user) for t in available_tools(p, hidden)]
        # The prompt's budget: the oldest earlier turns give way first; a question that does not
        # fit even alone is answered with the reason, never sent to fail at the endpoint.
        budget = get_settings().assistant_prompt_tokens
        fit_history(convo, len(history), budget, tools)
        turn_start = len(convo)
        if (need := prompt_tokens(convo, tools)) > budget:
            status = "error"
            yield {"type": "error", "message": over_budget_text(need, budget), "code": "context_budget"}
            return
        steps: list[dict[str, Any]] = []
        conv_seen: set[int] = set()
        segment = first_segment
        cite_n = first_cite
        tool_chars = 0
        searches = 0
        # Call key → (the first step's id, the result the model got, its summary msg, ok).
        seen_calls: dict[str, tuple[str, str, Any, bool]] = {}
        repeats = 0
        tool_failures = 0

        # The citations of the round being streamed: the stream shows each marker as its numbered
        # link as soon as it closes, and the finished text keeps those numbers (citations.Streamer).
        cur: dict[str, Any] = {}

        def round_resolver() -> citations.Resolver:
            return cur.get("resolver") or citations.Resolver(db, p, known=_read_text(convo), offset=cite_n,
                                                             web=tctx.web_sources)

        async def finish(text_raw: str, **extra: Any) -> AsyncIterator[dict[str, Any]]:
            r = round_resolver()
            text = present.normalize_markdown(await r.resolve(text_raw))
            cites = r.cites
            meta: dict[str, Any] = {"segment": segment, "final": True}
            if cur.get("model"):  # which model answered, and whether it was local
                meta["model"] = cur["model"]
            if extra.get("stop_reason"):
                meta["stop_reason"] = extra["stop_reason"]
            # The turn's confirmed writes, from tool results (agent/claims.py), and whether the reply
            # claims a change that none of them confirms.
            changes = [st["change"] for st in steps if st.get("change")]
            if changes:
                meta["changes"] = changes
            if extra.get("unconfirmed"):
                meta["unconfirmed"] = True
            if extra.get("claim_check"):
                meta["claim_check"] = extra.pop("claim_check")
            db.add(ChatMessage(session_id=s.id, role="assistant", content=text, citations=cites, steps=[],
                               meta=meta))
            await merge_context(db, s)
            await db.commit()
            yield {"type": "final", "content": text, "citations": cites, "segment": segment,
                   "model": cur.get("model"), **({"changes": changes} if changes else {}),
                   **{k: v for k, v in extra.items() if v is not None}}

        async def checked(text_raw: str) -> tuple[str, dict[str, Any]]:
            """The reply, asked again once when it claims a change and nothing in this turn (a tool,
            or an approval decided before this continuation) confirms one (agent/claims.py)."""
            confirmed = any(st.get("change") for st in steps) or any(
                getattr(pa, "status", None) in ("approved", "executing", "executed") for pa in decided)
            if confirmed or not claims.claims_change(text_raw):
                return text_raw, {}
            convo.append({"role": "assistant", "content": text_raw})
            convo.append({"role": "user", "content": claims.correction_note(steps)})
            again = ""
            try:
                async for ev in stream_round(False, time.monotonic() + WRAP_UP_SECONDS):
                    if ev["type"] == "_round":
                        again = ev["text"]
                    # The first reply is on screen already: the final event replaces it as a whole.
            except Exception as e:  # noqa: BLE001  keep the first reply, flagged
                log.warning("assistant claim check call failed: %s", e)
            if again.strip() and not claims.claims_change(again):
                return again, {"claim_check": "corrected"}
            return (again if again.strip() else text_raw), {"claim_check": "flagged", "unconfirmed": True}

        async def stream_round(use_tools: bool, deadline: float) -> AsyncIterator[dict[str, Any]]:
            """One model call: deltas, then {"type": "_round", "text", "final"}; a call that runs
            past `deadline` stops there (cur["late"])."""
            final: dict[str, Any] = {}
            parts: list[str] = []
            cur["retracted"] = False
            cur["resolver"] = None
            cur["resolver"] = round_resolver()
            cur["late"] = False
            streamer = citations.Streamer(cur["resolver"])
            async for ev in bounded(llm.chat_stream(db, p.user, convo, task="chat", sensitive=s.sensitive,
                                                    tools=tools if use_tools else None, feature="assistant"),
                                    deadline, cur):
                if stop.is_set():
                    break
                if ev.get("retract"):
                    # A closing </think>: the text so far was the model's reasoning (intel/llm.py
                    # ThinkSplitter). It never reaches the answer; the final or segment event
                    # replaces what was already on screen.
                    parts.clear()
                    cur["retracted"] = True
                    cur["resolver"] = round_resolver()
                    streamer = citations.Streamer(cur["resolver"])
                elif "content" in ev:
                    parts.append(ev["content"])
                    shown, new_cites = await streamer.feed(ev["content"])
                    for c in new_cites:  # before the text that links to it
                        yield {"type": "cite", "citation": c, "segment": segment}
                    if shown:
                        yield {"type": "delta", "text": shown, "segment": segment}
                elif "tool_calls" in ev:
                    final = ev
                    if ev.get("_endpoint"):
                        cur["model"] = {"name": ev.get("_model"), "endpoint": ev["_endpoint"],
                                        "local": bool(ev.get("_local"))}
            yield {"type": "_round", "text": "".join(parts), "final": final}

        for _round in range(MAX_ROUNDS):
            if time.monotonic() - started > MAX_SECONDS:
                stop_reason = "time"
                break
            if tool_chars > MAX_TOOL_CHARS_TOTAL or prompt_tokens(convo, tools) > budget:
                stop_reason = "size"
                break
            raw_text, final = "", {}
            try:
                async for ev in stream_round(True, started + MAX_SECONDS):
                    if ev["type"] == "_round":
                        raw_text, final = ev["text"], ev["final"]
                    else:
                        yield ev
            except llm.LocalModelsOnly as e:
                status = "error"
                yield {"type": "error", "message": str(e), "code": "local_models_only"}
                return
            except llm.NoModel:
                status = "error"
                yield {"type": "error", "message": "No language model is configured for the assistant. "
                                                   "An admin can add one under Settings → Admin → Models."}
                return
            except llm.LocalOnlyViolation:
                status = "error"
                yield {"type": "error", "message": "This chat includes mail from a local-only account and no local "
                                                   "model is configured, so the assistant stopped before sending that "
                                                   "content to a hosted model."}
                return
            except HTTPException as e:  # a token's model budget is spent (core/throttle.py)
                status = "error"
                yield {"type": "error", "message": str(e.detail)}
                return
            except Exception as e:  # noqa: BLE001
                log.warning("assistant model call failed: %s", e)
                status = "error"
                if context_overflow(e):
                    yield {"type": "error", "code": "context_budget",
                           "message": over_budget_text(prompt_tokens(convo, tools), budget)}
                    return
                yield {"type": "error", "message": "The model did not respond. Try again in a moment."}
                return

            elapsed = round(time.monotonic() - started, 1)
            if cur.get("late") and not stop.is_set():
                # The time limit came in the middle of the model's answer: answer from what was found.
                stop_reason = "time"
                break
            if stop.is_set():
                if raw_text:
                    async for ev in finish(raw_text, stopped=True):
                        yield ev
                status = "stopped"
                async for ev in title_event():
                    yield ev
                yield {"type": "done", "stopped": True, "steps": len(steps), "conversations": len(conv_seen),
                       "seconds": elapsed}
                return
            calls = final.get("tool_calls") or []
            if not calls:
                raw_text, check = await checked(raw_text)
                async for ev in finish(raw_text, **check):
                    yield ev
                status = "done"
                await _suggest_memories(db, p, s, user_row, body_text, steps)
                async for ev in title_event():
                    yield ev
                yield {"type": "done", "model": final.get("_model"), "model_info": cur.get("model"),
                       "sensitive": s.sensitive, "memory_ids": info["memory_ids"],
                       "steps": len(steps), "conversations": len(conv_seen), "seconds": elapsed}
                return

            # Text before the tool calls is a segment of its own: finalise it now.
            seg_text, seg_cites = "", []
            if raw_text.strip():
                r = round_resolver()
                seg_text, seg_cites = present.normalize_markdown(await r.resolve(raw_text)), r.cites
                cite_n += len(seg_cites)
                yield {"type": "segment", "segment": segment, "content": seg_text, "citations": seg_cites}
            elif cur.get("retracted"):
                # Only reasoning before the tool calls: clear what was streamed of it.
                yield {"type": "segment", "segment": segment, "content": "", "citations": []}
            tool_calls = [{"id": c.get("id") or f"call_{_round}_{i}", "type": "function",
                           "function": {"name": c["function"]["name"], "arguments": c["function"]["arguments"] or "{}"}}
                          for i, c in enumerate(calls)]
            convo.append({"role": "assistant", "content": raw_text or None, "tool_calls": tool_calls})
            round_row = ChatMessage(session_id=s.id, role="assistant", content=seg_text or raw_text,
                                    tool_calls=tool_calls, citations=seg_cites or None, meta={"segment": segment})
            db.add(round_row)
            round_steps: list[dict[str, Any]] = []

            for c in tool_calls:
                name = c["function"]["name"]
                if stop.is_set():
                    tm = _tool_message(c["id"], {"error": "stopped by the user"})
                    convo.append(tm)
                    db.add(ChatMessage(session_id=s.id, role="tool", tool_call_id=c["id"], name=name,
                                       content=tm["content"]))
                    continue
                try:
                    args = json.loads(c["function"]["arguments"] or "{}")
                except ValueError:
                    args = None
                step: dict[str, Any] = {"id": c["id"], "name": name, "icon": present.step_icon(name),
                                        "args": args, "segment": segment}
                step["label"] = _with_msg(step, "label_msg", await present.step_msg(
                    db, p.user_id, name, args if isinstance(args, dict) else None))
                yield {"type": "tool_start", **step}
                t0 = time.monotonic()
                key = name + json.dumps(args, sort_keys=True, default=str)
                if key in seen_calls and name not in REPEAT_EXEMPT:
                    first_id, first_content, _first_summary, first_ok = seen_calls[key]
                    repeats += 1
                    step.update(ok=first_ok, summary=_with_msg(step, "summary_msg", present.M("approvals.result.same_again")),
                                count=None, ms=0, repeat_of=first_id)
                    yield {"type": "tool", **step}
                    tm = _tool_message(c["id"], repeat_note(name, first_content, last=repeats >= MAX_REPEATS))
                else:
                    try:
                        if not isinstance(args, dict):
                            raise ToolError("arguments were not valid JSON")
                        tctx.session_sensitive = s.sensitive
                        # A savepoint, so a tool that fails unexpectedly (a database error among
                        # them) is undone alone and the turn can go on; a tool that commits ends it.
                        sp = await db.begin_nested()
                        try:
                            res = await run_tool(tctx, name, args)
                        except ToolError:
                            if sp.is_active:
                                await sp.commit()
                            raise
                        except Exception as e:  # told to the model as a tool error
                            if sp.is_active:
                                await sp.rollback()
                            tool_failures += 1
                            log.exception("tool %s failed in session %s", name, s.id)
                            if tool_failures > MAX_TOOL_FAILURES:
                                raise
                            raise ToolError(f"the tool failed unexpectedly ({type(e).__name__}: {e})"[:500]) from e
                        if sp.is_active:
                            await sp.commit()
                        if name in UNTRUSTED_TOOLS:
                            tctx.untrusted = True
                        if name == "recall" and isinstance(res.data, dict):
                            # Notes that came back from recall count as used in this turn too.
                            got = [int(m["id"]) for m in res.data.get("memories") or [] if m.get("id")]
                            new_ids = [i for i in got if i not in info["memory_ids"]]
                            if new_ids:
                                info["memory_ids"] = [*info["memory_ids"], *new_ids]
                                user_row.meta = {**(user_row.meta or {}), "memory_ids": info["memory_ids"]}
                        if not s.sensitive and (outbound.tainted() or await result_is_sensitive(db, p.user_id, res)):
                            s.sensitive = True
                            await db.flush()
                        conv_seen.update(x for x in res.conversation_ids if x)
                        summary_m, count = present.result_msg(name, res)
                        summary = _with_msg(step, "summary_msg", summary_m)
                        step.update(ok=True, summary=summary, count=count, ms=int((time.monotonic() - t0) * 1000))
                        if res.pending is None and claims.is_write(name) and not (
                                isinstance(res.data, dict) and res.data.get("status") == "pending_approval"):
                            step["change"] = claims.change_of(name, res.data, res.refs, step.get("summary_msg") or summary)
                        yield {"type": "tool", **step}
                        if res.refs:
                            # The chat's working set (agent/working_set.py), and a card per item.
                            s.context = working_set.add(s.context, res.refs)
                            tctx.chat_context = {**(tctx.chat_context or {}),
                                                 working_set.KEY: s.context[working_set.KEY]}
                            for r in res.refs:
                                yield {"type": "ref", "kind": r["kind"], "id": r["id"], "version": r.get("version"),
                                       "title": r.get("title") or "", "segment": segment}
                        if res.draft:
                            yield {"type": "draft", "draft_id": res.draft["draft_id"], "payload": res.draft["payload"],
                                   "ref": res.draft.get("ref"),
                                   "confirm_send": bool(res.draft.get("confirm_send")), "segment": segment}
                        for extra_ev in res.events:
                            yield {**extra_ev, "segment": segment}
                        if res.pending is not None:
                            s.context = {**(s.context or {}),
                                         "pending": [*(s.context or {}).get("pending", []), res.pending["id"]]}
                            step["approval_id"] = res.pending["id"]
                            from mailr.models import PendingAction

                            pa_row = await db.get(PendingAction, int(res.pending["id"]))
                            card = await present.approval_card(db, pa_row) if pa_row is not None else None
                            yield {"type": "approval_required", "approval_id": res.pending["id"], "approval": card,
                                   "action": res.pending["action"], "target": res.pending.get("target"),
                                   "title": (res.pending.get("preview") or {}).get("title", res.pending["action"]),
                                   "preview": (card or {}).get("preview") or res.pending.get("preview"),
                           "status": res.pending.get("status"),
                                   "expires_at": res.pending.get("expires_at"), "segment": segment}
                        data = res.data
                        if name in SEARCH_TOOLS:
                            searches += 1
                            if searches >= SEARCH_HINT_AFTER and isinstance(data, dict):
                                data = {**data, "_mailr_hint": f"You have run {searches} searches in this turn. If "
                                        "the answer is not in these results, stop searching: answer with what you "
                                        "found and say what is missing."}
                        # Times in the user's zone (mail dates are stored in UTC): the model
                        # quotes what it reads (agent/when.py localize_times).
                        from mailr.agent.calendar_common import _zone

                        data = when.localize_times(data, await _zone(tctx))
                        tm = _tool_message(c["id"], data)
                        seen_calls[key] = (c["id"], tm["content"], summary_m, True)
                    except ToolError as e:
                        # The step line never shows the tool's own error (it is written for the
                        # model); clients show it under Technical details.
                        step.update(ok=False, summary="", error=str(e)[:500], count=None,
                                    ms=int((time.monotonic() - t0) * 1000))
                        yield {"type": "tool", **step}
                        tm = _tool_message(c["id"], {"error": str(e)})
                        seen_calls[key] = (c["id"], tm["content"], None, False)
                tool_chars += len(tm["content"])
                steps.append(step)
                round_steps.append(step)
                convo.append(tm)
                db.add(ChatMessage(session_id=s.id, role="tool", tool_call_id=c["id"], name=name, content=tm["content"]))
            # Skills a tool activated (use_skill) stick to the session.
            tool_skills = (tctx.chat_context or {}).get("skills")
            if tool_skills and tool_skills != (s.context or {}).get("skills"):
                s.context = {**(s.context or {}), "skills": tool_skills}
            round_row.steps = round_steps
            # Persist the round (and a sensitivity change) before the next model call.
            await merge_context(db, s)
            await db.commit()
            segment += 1
            if repeats >= MAX_REPEATS:
                stop_reason = "repeats"
                break
        else:
            stop_reason = "rounds"

        # Out of budget: one last call without tools, answering from what was found.
        why = STOP_REASONS.get(stop_reason or "rounds", "")
        convo.append({"role": "user", "content": (
            f"[mailr: no more tool calls for this question: {why}. Answer now from the tool results above. Say "
            "briefly what you found, what is still missing, and suggest a narrower follow-up question. Do not "
            "call tools.]")})
        raw_text = ""
        try:
            if not shrink_results(convo, turn_start, budget):
                raise _OverBudget(over_budget_text(prompt_tokens(convo), budget))
            async for ev in stream_round(False, time.monotonic() + WRAP_UP_SECONDS):
                if ev["type"] == "_round":
                    raw_text = ev["text"]
                else:
                    yield ev
        except (llm.NoModel, llm.LocalOnlyViolation):
            raw_text = ""
        except _OverBudget as e:
            raw_text = str(e)
        except Exception as e:  # noqa: BLE001
            log.warning("assistant wrap-up call failed: %s", e)
            if context_overflow(e):
                raw_text = over_budget_text(prompt_tokens(convo), budget)
        if not raw_text.strip():
            raw_text = (f"I stopped before finishing because {why}. I looked at {len(conv_seen)} conversation(s) "
                        "but did not find a clear answer. Try a narrower question, for example with a name, a date "
                        "or a subject.")
        raw_text, check = await checked(raw_text) if not stop.is_set() else (raw_text, {})
        async for ev in finish(raw_text, stop_reason=stop_reason, stopped=True if stop.is_set() else None, **check):
            yield ev
        status = "stopped" if stop.is_set() else "done"
        async for ev in title_event():
            yield ev
        yield {"type": "done", "stop_reason": stop_reason, "stopped": True if stop.is_set() else None,
               "note": f"Stopped searching: {why}.", "sensitive": s.sensitive, "memory_ids": info["memory_ids"],
               "steps": len(steps),
               "conversations": len(conv_seen), "seconds": round(time.monotonic() - started, 1)}
    finally:
        _STOPS.pop(s.id, None)
        if user_row is not None and user_row.id is not None:
            await _record_status(db, user_row, status or "interrupted", stop_reason)


async def merge_context(db: AsyncSession, s: ChatSession) -> None:
    """Before a turn saves its chat's context: approvals another writer added meanwhile (a bulk
    review's card, agent/review.py `_wait_in_chat`) stay pending. Read under the row lock, which
    the commit right after releases."""
    with db.no_autoflush:  # the stored copy, not this turn's own (unwritten) one
        stored = (await db.execute(select(ChatSession.context).where(ChatSession.id == s.id)
                                   .with_for_update())).scalar_one_or_none() or {}
    mine = s.context or {}
    have = {int(x) for x in [*mine.get("pending", []), *mine.get("told", [])]}
    extra = [int(x) for x in stored.get("pending", []) if int(x) not in have]
    if extra:
        s.context = {**mine, "pending": [*mine.get("pending", []), *extra]}


async def _record_status(db: AsyncSession, row: ChatMessage, status: str, stop_reason: str | None) -> None:
    meta = {**(row.meta or {}), **({"stop_reason": stop_reason} if stop_reason else {})} or None
    stmt = update(ChatMessage).where(ChatMessage.id == row.id).values(status=status, meta=meta)
    try:
        await db.execute(stmt)
        await db.commit()
        return
    except Exception:  # noqa: BLE001, S110
        pass
    # The session may be unusable (cancelled mid-query): use a fresh one.
    try:
        from mailr.db import sessionmaker

        async with sessionmaker()() as db2:
            await db2.execute(stmt)
            await db2.commit()
    except Exception:  # noqa: BLE001
        log.warning("could not record the status of chat turn %s", row.id)
