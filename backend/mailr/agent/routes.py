"""Agent endpoints: chat (SSE), drafting help, the context builder, and the tool API used by MCP."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from mailr.agent import context as cx
from mailr.agent.loop import request_stop, run_chat
from mailr.agent.tools import (
    TOOLS,
    ToolCtx,
    ToolError,
    available_tools,
    compose_payload,
    run_tool,
    tool_hints,
)
from mailr.core import publicid
from mailr.core.audit import audit
from mailr.core.auth import Principal, current, scope
from mailr.core.visibility import touches_local_only, visible_message_filter
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.intel import llm
from mailr.models import ChatMessage, ChatSession, Message

router = APIRouter(tags=["agent"])
log = logging.getLogger("mailr.agent")


class ChatIn(BaseModel):
    # The new user turn. History lives on the server; `messages` (older clients) is accepted
    # but only its last user turn is used.
    message: str | None = Field(None, max_length=20_000)
    session_id: str | None = Field(None, max_length=40)
    messages: list[dict[str, Any]] | None = None
    # {conversation_id?, ui?: {view, q, conversation_id, compose: [...]}} (agent/screen.py)
    context: dict[str, Any] | None = None
    # Continue a chat after the user decided an approval it created (see agent/loop.py).
    resume_approval_id: int | None = None
    # Re-run a turn (keep its user message, replace its reply and everything after it), or
    # replace a user message with `message` and run from there ("edit and resend").
    rerun_from: int | None = None
    edit_from: int | None = None
    # `/local` (agent/commands.py): this chat stays on the local model from this turn on (the
    # session becomes sensitive, which is sticky).
    local_only: bool = False


class _Run:
    """One assistant turn running in the background. The run does not depend on the HTTP
    connection: if the stream drops, the turn continues and the client re-attaches with
    GET /agent/chat/{session_id}/stream?after=<seq> (events are buffered for the turn)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.done = False
        self.cond = asyncio.Condition()
        self.stop = asyncio.Event()
        self.session_id: str | None = None
        self.user_id: int | None = None
        # The actor that owns the chat: None for the signed-in user's sessions, else the token id.
        self.token_id: int | None = None
        self.finished_at: float | None = None

    async def push(self, ev: dict[str, Any]) -> None:
        async with self.cond:
            self.events.append(ev)
            self.cond.notify_all()

    async def close(self) -> None:
        async with self.cond:
            self.done = True
            self.finished_at = time.monotonic()
            self.cond.notify_all()

    async def follow(self, after: int = 0):
        i = max(0, after)
        while True:
            async with self.cond:
                while i >= len(self.events) and not self.done:
                    await self.cond.wait()
                batch, done = self.events[i:], self.done
            for ev in batch:
                i += 1
                yield i, ev
            if done and i >= len(self.events):
                return


_RUNS: dict[str, _Run] = {}
_TASKS: set[asyncio.Task] = set()
RUN_KEEP_SECONDS = 300


def _gc_runs() -> None:
    now = time.monotonic()
    for sid, r in list(_RUNS.items()):
        if r.done and r.finished_at and now - r.finished_at > RUN_KEEP_SECONDS:
            _RUNS.pop(sid, None)


def _sse(run: _Run, after: int = 0):
    async def gen():
        async for seq, ev in run.follow(after):
            for name, data in _wire(ev):
                yield {"event": name, "data": json.dumps(publicid.out({**data, "seq": seq}), default=str)}
    return EventSourceResponse(gen(), headers={"X-Accel-Buffering": "no"})


@router.post("/agent/chat")
async def chat(body: ChatIn, request: Request, p: Principal = Depends(scope("read")),
               db: AsyncSession = Depends(get_db)):
    from mailr.core.access import require_feature

    await require_feature(db, p.user, "assistant")  # the access policy (core/access.py)
    from mailr.calendar import tz as tzmod

    # The browser's zone and clock: those of this answer while Region and Time format are Automatic
    # (committed below).
    if not p.is_token:  # a signed-in device's zone and clock, never a token's
        await tzmod.note_device_zone(db, p.user, request.headers.get(tzmod.ZONE_HEADER))
        await tzmod.note_device_clock(db, p.user, request.headers.get(tzmod.CLOCK_HEADER))
    text = body.message
    if not text and body.messages:
        last = next((m for m in reversed(body.messages) if m.get("role") == "user"), None)
        text = (last or {}).get("content") if isinstance((last or {}).get("content"), str) else None
    await audit(db, p, "agent.chat", str((body.context or {}).get("conversation_id") or body.session_id or ""),
                {"message": (text or "")[:500], **({"rerun_from": body.rerun_from} if body.rerun_from else {})})
    await db.commit()
    _gc_runs()
    if p.is_token:
        # A token's chat calls a model: its budget is checked before the stream starts.
        from mailr.intel.llm import charge_model_budget

        await charge_model_budget()
    from mailr.models import ChatSession

    own = await db.get(ChatSession, body.session_id) if body.session_id else None
    busy = _RUNS.get(body.session_id) if body.session_id else None
    if busy is not None and not busy.done and _owns(p, busy.user_id, busy.token_id):
        raise ApiError(409, "assistant.still_answering")
    run = _Run()
    run.user_id = p.user_id
    run.token_id = _token_id(p)
    if own is not None and own.user_id == p.user_id:
        # Listed before the run starts (no await since the check above), so a second Send right
        # after this one finds the chat busy.
        run.session_id = own.id
        _RUNS[own.id] = run

    async def drive():
        # The request's session is closed once streaming starts; the run uses its own.
        from mailr.db import sessionmaker

        try:
            async with sessionmaker()() as sdb:
                async for ev in run_chat(sdb, p, text or "", session_id=body.session_id, context=body.context,
                                         resume=body.resume_approval_id, stop=run.stop,
                                         rerun_from=body.rerun_from, edit_from=body.edit_from,
                                         sensitive=body.local_only):
                    if ev.get("type") == "session" and ev.get("session_id"):
                        run.session_id = ev["session_id"]
                        _RUNS[run.session_id] = run
                    await run.push(ev)
        except Exception as e:
            log.exception("assistant run failed")
            await run.push({"type": "error", "message": f"The assistant failed: {type(e).__name__}."})
        finally:
            await run.close()

    task = asyncio.create_task(drive())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return _sse(run)


def _token_id(p: Principal) -> int | None:
    return p.token.id if p.token is not None else None


def _owns(p: Principal, user_id: int | None, token_id: int | None) -> bool:
    """A chat belongs to one actor: the signed-in user's sessions, or exactly one token."""
    return user_id == p.user_id and token_id == _token_id(p)


@router.get("/agent/chat/{session_id}/stream")
async def chat_stream(session_id: str, after: int = 0, p: Principal = Depends(scope("read"))):
    """Re-attach to a turn that is still running (or finished in the last few minutes) after the
    stream dropped. `after` is the last `seq` the client received. 404 when there is no run in
    this process: reload the chat (GET /agent/sessions/{id}) instead."""
    run = _RUNS.get(session_id)
    if run is None or not _owns(p, run.user_id, run.token_id):
        raise ApiError(404, "assistant.no_running_turn")
    return _sse(run, after)


@router.post("/agent/chat/{session_id}/stop")
async def chat_stop(session_id: str, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    s = await db.get(ChatSession, session_id)
    if s is None or not _owns(p, s.user_id, s.token_id):
        raise ApiError(404, "assistant.unknown_session")
    return {"ok": True, "stopped": request_stop(session_id)}


# ACT agent.chat_search @api (q: title, questions and answers; newest match first, with a snippet)
# ACT agent.commands @api
@router.get("/agent/commands")
async def chat_commands(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """The chat's slash commands, in picker order, with what each does in the
    user's language. The same list on the web and Android."""
    from mailr import i18n
    from mailr.agent import commands
    from mailr.knowledge import profile as kprofile

    return {"items": commands.listing(i18n.user_language(p.user), await kprofile.name_of(db, p.user_id))}


async def _review(db: AsyncSession, p: Principal, rid: int):
    from mailr.agent import review
    from mailr.models import MailReview

    r = await db.get(MailReview, rid)
    if r is None or r.user_id != p.user_id or p.token is not None:
        raise ApiError(404, "assistant.unknown_review")
    await review.settle_orphan(db, r)  # no job left: failed or stopped, so Continue works
    return r


# ACT agent.review_mail @api
@router.get("/agent/reviews/{rid}")
async def review_status(rid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """A bulk review's progress, for the chat's progress card: how many of the
    conversations are done, how many were set aside, and the review card once it is ready."""
    from mailr.agent import review

    r = await _review(db, p, rid)
    await db.commit()  # an orphaned review settled by _review
    return publicid.out(review.progress(r))


# ACT agent.review_mail @api
@router.post("/agent/reviews/{rid}/stop")
async def review_stop(rid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """Stop a review after the batch in progress; what it decided so far is kept (Continue resumes)."""
    from mailr.agent import review

    r = await _review(db, p, rid)
    if r.status != "running":
        raise ApiError(409, "assistant.review_not_running")
    r.status = "stopping"
    await audit(db, p, "agent.review_stop", str(rid), {}, always=True)
    await db.commit()
    return publicid.out(review.progress(r))


# ACT agent.review_mail @api
@router.post("/agent/reviews/{rid}/resume")
async def review_resume(rid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """Continue a stopped (or failed) review where it stopped."""
    from mailr.agent import review

    r = await _review(db, p, rid)
    if r.status not in ("stopped", "failed"):
        raise ApiError(409, "assistant.review_not_stopped")
    await review.resume(db, r)
    await audit(db, p, "agent.review_resume", str(rid), {}, always=True)
    await db.commit()
    return publicid.out(review.progress(r))


@router.get("/agent/review-choices")
async def review_choices(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Settings → Ambrose → Sweep: the moves the user chose on review cards, remembered per sender,
    latest first."""
    from mailr.agent import review

    _no_token(p)
    return {"items": await review.list_choices(db, p.user_id)}


# ACT agent.review_choice_remove @api
@router.delete("/agent/review-choices/{cid}")
async def review_choice_remove(cid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Forget one remembered choice: that sender's mail goes to the model again."""
    from mailr.agent import review

    _no_token(p)
    if not await review.forget_choices(db, p.user_id, cid):
        raise ApiError(404, "assistant.unknown_review_choice")
    await audit(db, p, "agent.review_choice_remove", str(cid), {}, always=True)
    await db.commit()
    return {"removed": 1}


# ACT agent.review_choice_clear @api
@router.delete("/agent/review-choices")
async def review_choices_clear(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Forget every remembered choice."""
    from mailr.agent import review

    _no_token(p)
    n = await review.forget_choices(db, p.user_id)
    await audit(db, p, "agent.review_choice_clear", "", {"removed": n}, always=True)
    await db.commit()
    return {"removed": n}


def _no_token(p: Principal) -> None:
    # The user's own record of their choices: the signed-in apps only, as for the review itself.
    if p.token is not None:
        raise ApiError(403, "assistant.review_choices_user_only")


# ACT agent.chat_retitle @api
@router.post("/agent/sessions/{session_id}/retitle")
async def retitle_chat(session_id: str, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """`/retitle`: a new short title from the chat's first question, made by
    the chat's own model with its sensitivity. A name the user gave is replaced. The chat keeps its
    place in the list."""
    from mailr.agent import chat_title
    from mailr.agent.loop import is_resume_note

    s = await db.get(ChatSession, session_id)
    if s is None or not _owns(p, s.user_id, s.token_id):
        raise ApiError(404, "assistant.unknown_session")
    rows = (await db.execute(select(ChatMessage.content).where(ChatMessage.session_id == s.id, ChatMessage.role == "user")
                             .order_by(ChatMessage.id).limit(5))).scalars().all()
    first = next((c for c in rows if c and not is_resume_note(c)), None)
    if not first:
        raise ApiError(422, "assistant.empty_chat")
    from mailr.agent import commands

    if (cmd := commands.parse(first)) is not None and cmd[0].kind == "ask":
        first = commands.request_for(*cmd)
    before = {"title": s.title, "renamed": bool(s.title_custom)}
    sid, user_id, sensitive = s.id, s.user_id, bool(s.sensitive)
    await db.execute(update(ChatSession).where(ChatSession.id == sid)
                     .values(title_custom=False, updated_at=ChatSession.updated_at))
    await db.commit()
    title = await chat_title.retitle(user_id, sid, first, sensitive=sensitive)
    if not title:
        await db.execute(update(ChatSession).where(ChatSession.id == sid)
                         .values(title_custom=before["renamed"], updated_at=ChatSession.updated_at))
        await db.commit()
        raise ApiError(503, "assistant.no_title")
    await audit(db, p, "agent.chat_retitle", sid, {}, always=True)
    await db.commit()
    return {"id": sid, "title": title, "renamed": False, "previous": before}


@router.get("/agent/sessions")
async def chat_sessions(limit: Annotated[int, Query(ge=1, le=100)] = 30,
                        q: Annotated[str | None, Query(max_length=200)] = None,
                        p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """The actor's chats, newest first. With `q`, the chats whose title, questions or answers
    match, each with `match` (title, question or answer), `snippet` (a
    plain line around the hit) and its parts with the hits marked (`snippet_parts`, `title_parts`)."""
    from mailr.agent import chat_search

    found: list[tuple[ChatSession, dict[str, Any]]]
    if q and q.strip():
        found = await chat_search.search(db, p, q.strip(), min(limit, 100))
    else:
        rows = (await db.execute(select(ChatSession).where(*chat_search.owned(p))
                                 .order_by(ChatSession.updated_at.desc()).limit(min(limit, 100)))).scalars()
        found = [(s, {}) for s in rows]
    previews = await _previews(db, [s.id for s, _ in found])
    return [{"id": s.id, "title": s.title, "renamed": bool(s.title_custom), "updated_at": s.updated_at.isoformat(),
             "sensitive": s.sensitive, "context": s.context, "preview": previews.get(s.id), **info}
            for s, info in found]


class ChatRenameIn(BaseModel):
    # Empty (or only spaces) gives the chat its automatic title back: the first question.
    title: str = Field("", max_length=1000)


# ACT agent.chat_rename @api
@router.patch("/agent/sessions/{session_id}")
async def rename_chat(session_id: str, body: ChatRenameIn, p: Principal = Depends(scope("read")),
                      db: AsyncSession = Depends(get_db)):
    """Rename a chat. The title is trimmed to one line of at most 120 characters; a renamed title
    is kept when the chat goes on. An empty title goes back to the automatic one. The chat keeps
    its place in the list (its last activity does not change)."""
    from sqlalchemy import update

    from mailr.agent import chat_search

    s = await db.get(ChatSession, session_id)
    if s is None or not _owns(p, s.user_id, s.token_id):
        raise ApiError(404, "assistant.unknown_session")
    title = chat_search.clean_title(body.title)
    if len(title) > chat_search.TITLE_MAX:
        raise ApiError(422, "assistant.chat_title_too_long", max=chat_search.TITLE_MAX)
    before = {"title": s.title, "renamed": bool(s.title_custom)}
    custom = bool(title)
    if not custom:
        title = await chat_search.automatic_title(db, s)
    await db.execute(update(ChatSession).where(ChatSession.id == s.id)
                     .values(title=title, title_custom=custom, updated_at=ChatSession.updated_at))
    await audit(db, p, "agent.chat_rename", s.id, {"title": title if custom else ""}, always=True)
    await db.commit()
    return {"id": s.id, "title": title, "renamed": custom, "previous": before}


_CITE_LINK = re.compile(r"\[\d+\]\(mailr-cite:\d+\)|\[\[[^\]]*\]\]")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_MARKS = re.compile(r"[*_`#>|~]+")


def chat_preview(text: str, limit: int = 140) -> str | None:
    """The start of an answer as one plain line for the list of chats: no citation links or raw
    markers, no Markdown marks, whitespace collapsed, cut at a word near `limit` characters."""
    t = _CITE_LINK.sub("", text or "")
    t = _MD_LINK.sub(r"\1", t)
    t = _MD_MARKS.sub("", t)
    t = re.sub(r"^\s*[-+]\s+", "", t, flags=re.M)
    t = re.sub(r"\s+([.,;:!?])", r"\1", " ".join(t.split()))
    if not t:
        return None
    if len(t) > limit:
        cut = t[:limit].rsplit(" ", 1)[0] or t[:limit]
        t = cut.rstrip(",.;:") + "…"
    return t


async def _previews(db: AsyncSession, ids: list[str]) -> dict[str, str]:
    """Each chat's latest answer (a final assistant row with text), as a preview line."""
    from sqlalchemy.dialects.postgresql import distinct_on

    if not ids:
        return {}
    q = (select(ChatMessage.session_id, ChatMessage.content)
         .where(ChatMessage.session_id.in_(ids), ChatMessage.role == "assistant", ChatMessage.content != "",
                # a final answer: no tool calls (SQL NULL, JSON null or an empty list, as `timeline` reads it)
                sql_text("(chat_messages.tool_calls IS NULL OR chat_messages.tool_calls IN ('null'::jsonb, '[]'::jsonb))"))
         .ext(distinct_on(ChatMessage.session_id)).order_by(ChatMessage.session_id, ChatMessage.id.desc()))
    out: dict[str, str] = {}
    for sid, content in (await db.execute(q)).all():
        p = chat_preview(content)
        if p:
            out[sid] = p
    return out


STEP_OUTCOME = {"approved": "approved", "executing": "approved", "executed": "approved", "denied": "denied",
                "expired": "expired", "failed": "failed"}


def _clean_cites(cites: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Stored citations as they read today: an event or task citation saved before 4 Oct 2026 named
    its calendar by its raw name ("Calendar (rosa@…)") or not at all by a name; it now reads as the
    calendar's name (calendar/describe.py calendar_name), and a URL or id is dropped."""
    from mailr.calendar.describe import calendar_name, looks_internal

    out = []
    for c in cites or []:
        if isinstance(c, dict) and c.get("kind") in ("event", "task") and c.get("from"):
            v = str(c["from"])
            c = {**c, "from": None if looks_internal(v) and "(" not in v else calendar_name(v, None, fallback=v)}
        out.append(c)
    return out


def timeline(rows: list[ChatMessage], cards: dict[int, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Stored rows → visible turns. A user turn is followed
    by one assistant turn whose `timeline` lists, in order, text segments, tool-step groups, the
    approval cards those steps asked for (from `cards`, keyed by approval id, with their outcome)
    and, after a decision, a `resume` item and the continuation. `content` and `citations` are the
    final answer, `steps` all steps (for older clients)."""
    from mailr.agent.loop import is_resume_note
    from mailr.agent.present import normalize_markdown, step_icon

    cards = cards or {}
    turns: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None

    def close() -> None:
        nonlocal cur
        if cur is not None:
            turns.append(cur)
            cur = None

    def add_steps(steps: list[dict[str, Any]]) -> None:
        steps = [{**st, "icon": st.get("icon") or step_icon(str(st.get("name") or ""))} for st in steps]
        for st in steps:  # the step that asked says what became of the request
            card = cards.get(int(st["approval_id"])) if st.get("approval_id") is not None else None
            if card is not None and card["status"] != "pending":
                st["summary"] = STEP_OUTCOME.get(card["status"], st.get("summary"))
                if card["status"] in STEP_OUTCOME:
                    st["summary_msg"] = {"key": f"approvals.step_outcome.{STEP_OUTCOME[card['status']]}", "params": {}}
        cur["timeline"].append({"type": "steps", "steps": steps})
        for st in steps:
            aid = st.get("approval_id")
            if aid is not None and int(aid) in cards:
                cur["timeline"].append({"type": "approval", "approval": cards[int(aid)]})

    for r in rows:
        if r.role == "user":
            resume_ids = (r.meta or {}).get("resume")
            note = is_resume_note(r.content)
            if note and cur is not None:
                # A continuation after an approval: the same turn goes on.
                cur["status"] = r.status or "done"
                cur["stop_reason"] = (r.meta or {}).get("stop_reason")
                titles = [cards[int(i)]["display"]["title"] for i in resume_ids or [] if int(i) in cards]
                cur["timeline"].append({"type": "resume", "approval_ids": [int(i) for i in resume_ids or []],
                                        "text": "After your approval" if not titles else
                                        f"After your decision on “{titles[0]}”"})
                continue
            close()
            if not note:
                turns.append({"role": "user", "id": r.id, "content": r.content, "citations": [], "steps": [],
                              "created_at": r.created_at.isoformat(), "status": r.status,
                              "skills": (r.meta or {}).get("skills") or []})
            cur = {"role": "assistant", "user_message_id": None if note else r.id, "content": "", "citations": [],
                   "steps": [], "timeline": [], "status": r.status or "done",
                   "stop_reason": (r.meta or {}).get("stop_reason"), "resume": note,
                   "memory_ids": [int(i) for i in (r.meta or {}).get("memory_ids") or []],
                   "created_at": r.created_at.isoformat()}
            continue
        if r.role != "assistant":
            continue
        if cur is None:  # rows before any user row (should not happen)
            cur = {"role": "assistant", "user_message_id": None, "content": "", "citations": [], "steps": [],
                   "timeline": [], "status": "done", "stop_reason": None, "resume": False,
                   "created_at": r.created_at.isoformat()}
        final = not r.tool_calls
        steps = list(r.steps or [])
        if final and steps and not any(t["type"] == "steps" for t in cur["timeline"]):
            # Older rows kept every step of the turn on the final answer.
            add_steps(steps)
        content = normalize_markdown(r.content) if r.content else ""
        if content:
            cur["timeline"].append({"type": "text", "content": content, "citations": _clean_cites(r.citations),
                                    "final": final})
        if not final and steps:
            add_steps(steps)
        cur["steps"] += steps
        if r.citations:
            cur["citations"] += [c for c in _clean_cites(r.citations) if c not in cur["citations"]]
        if final:
            cur["content"] = content
            cur["created_at"] = r.created_at.isoformat()
            if (r.meta or {}).get("model"):  # {name, endpoint, local}: the answer's model chip
                cur["model"] = r.meta["model"]
            # The turn's confirmed writes, and a reply that claims a change none of them confirms
            # (agent/claims.py).
            if (r.meta or {}).get("changes"):
                cur["changes"] = r.meta["changes"]
            if (r.meta or {}).get("unconfirmed"):
                cur["unconfirmed"] = True
    close()
    return turns


async def timeline_cards(db: AsyncSession, user_id: int, rows: list[ChatMessage]) -> dict[int, dict[str, Any]]:
    """The approval cards the rows' steps refer to, with their current status and outcome."""
    from mailr.agent.present import approval_card
    from mailr.models import PendingAction

    ids = {int(st["approval_id"]) for r in rows for st in (r.steps or []) if st.get("approval_id") is not None}
    for r in rows:
        ids |= {int(i) for i in (r.meta or {}).get("resume") or []}
    if not ids:
        return {}
    pas = (await db.execute(select(PendingAction).where(PendingAction.id.in_(ids),
                                                        PendingAction.user_id == user_id))).scalars().all()
    return {pa.id: await approval_card(db, pa) for pa in pas}


# ACT agent.answer_model @api (each assistant turn carries `model`: name, endpoint, local)
@router.get("/agent/sessions/{session_id}")
async def chat_session(session_id: str, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """The visible turns of a chat for re-opening it in the UI: user messages, and assistant
    turns as a timeline of text segments and tool steps, with each turn's status (running,
    done, stopped, error, interrupted)."""
    s = await db.get(ChatSession, session_id)
    if s is None or not _owns(p, s.user_id, s.token_id):
        raise ApiError(404, "assistant.unknown_session")
    rows = list((await db.execute(select(ChatMessage).where(ChatMessage.session_id == s.id)
                                  .order_by(ChatMessage.id))).scalars())
    turns = timeline(rows, await timeline_cards(db, p.user_id, rows))
    run = _RUNS.get(s.id)
    running = bool(run and not run.done)
    for t in turns:
        if t["role"] == "assistant" and t["status"] == "running" and not running:
            t["status"] = "interrupted"  # its process is gone (the start-up sweep also records this)
    # Approvals this chat created and the user has not decided yet: the UI shows their cards again.
    from mailr.agent.present import approval_card
    from mailr.models import PendingAction

    pend = (await db.execute(select(PendingAction).where(
        PendingAction.user_id == p.user_id, PendingAction.chat_session_id == s.id,
        PendingAction.status == "pending").order_by(PendingAction.id))).scalars().all()
    # Bulk reviews this chat started that are not settled yet (agent/review.py): their progress cards.
    from mailr.models import MailReview

    reviews = (await db.execute(select(MailReview.id).where(
        MailReview.user_id == p.user_id, MailReview.chat_session_id == s.id,
        MailReview.status.in_(("running", "stopping", "stopped", "failed", "done"))).order_by(MailReview.id)
        .limit(5))).scalars().all()
    return {"id": s.id, "title": s.title, "renamed": bool(s.title_custom), "sensitive": s.sensitive,
            "context": s.context, "turns": turns,
            "running": running, "skills": (s.context or {}).get("skills", []),
            "pending_approvals": [await approval_card(db, pa, covers=not p.is_token) for pa in pend],
            "reviews": [{"review_id": r} for r in reviews]}


@router.delete("/agent/sessions/{session_id}")
async def delete_chat(session_id: str, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    s = await db.get(ChatSession, session_id)
    if s is None or not _owns(p, s.user_id, s.token_id):
        raise ApiError(404, "assistant.unknown_session")
    request_stop(session_id)
    await db.delete(s)
    await db.commit()
    return {"ok": True}


def _wire(ev: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Loop events → SSE events: session, token, cite (a citation the streamed text now links to,
    sent before that text), segment (text before tool calls, finalised),
    tool_start, tool (step finished), draft, draft_edit, config, approval_required, final (the
    answer with citations), done, error. Text and steps carry `segment`, the position in the
    turn's timeline. `tool_call`/`tool_result` are kept for older clients."""
    t = ev.get("type")
    if t == "delta":
        return [("token", {"text": ev.get("text", ""), "segment": ev.get("segment", 0)})]
    if t == "tool_start":
        start = {k: ev.get(k) for k in ("id", "name", "label", "icon", "args", "segment")}
        if ev.get("label_msg"):
            start["label_msg"] = ev["label_msg"]  # the apps render it in their own language
        return [("tool_start", start),
                ("tool_call", {"name": ev.get("name"), "args": ev.get("args")})]
    if t == "tool":
        data = {k: ev.get(k) for k in ("id", "name", "label", "icon", "ok", "summary", "count", "ms", "segment",
                                       "approval_id")}
        for k in ("label_msg", "summary_msg", "repeat_of"):  # repeat_of: the step this one repeated
            if ev.get(k):
                data[k] = ev[k]
        if ev.get("error"):
            data["error"] = ev["error"]  # A failed step's own text, for Technical details only
        return [("tool", data), ("tool_result", {"name": ev.get("name"), "ok": ev.get("ok", True),
                                                 "summary": ev.get("summary", "")})]
    if t == "error":
        err = {"detail": ev.get("message") or ev.get("detail") or "The assistant failed."}
        if ev.get("code"):
            err["code"] = ev["code"]  # no_access (the policy turned the assistant off), command (agent/commands.py)
        return [("error", err)]
    return [(t or "message", {k: v for k, v in ev.items() if k != "type"})]


class ContextIn(BaseModel):
    conversation_id: int | None = None
    query: str | None = None
    people: list[str] | None = None
    budget_tokens: int = Field(8000, ge=1000, le=30000)


@router.post("/context")
async def build_context(body: ContextIn, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    if body.conversation_id is None and not body.query and not body.people:
        raise HTTPException(400, "give a conversation_id, a query, or people")  # i18n: internal
    try:
        bundle = await cx.build(db, p, conversation_id=body.conversation_id, query=body.query, people=body.people,
                                budget_tokens=body.budget_tokens)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    await audit(db, p, "agent.context", str(body.conversation_id or body.query or ""))
    await db.commit()
    return JSONResponse(publicid.out(bundle), headers={"X-Mailr-Content": "untrusted"})


class DraftIn(BaseModel):
    conversation_id: int | None = None
    message_id: int | None = None
    instructions: str = Field("", max_length=4000)
    mode: str = Field("reply", pattern="^(reply|reply_all|forward|new)$")
    tone: str | None = Field(None, max_length=200)
    to: list[str] | None = None
    save: bool = False
    draft_id: int | None = None
    # The draft as the compose window has it now (a compose payload: subject, to, cc, html or
    # text). With text in it, the instruction is treated as an edit of this draft.
    current: dict[str, Any] | None = None


_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["subject", "body"], "additionalProperties": False,
}
_REWRITE = re.compile(r"\b(re-?write|from scratch|start over|new version|completely|entirely|write (a|an) "
                      r"(new|different|fresh))\b", re.IGNORECASE)


def _parse_draft(content: str) -> dict[str, str]:
    c = (content or "").strip()
    c = re.sub(r"^```(?:json)?\s*|\s*```$", "", c)
    try:
        d = json.loads(c)
        if isinstance(d, dict) and "body" in d:
            return {"subject": str(d.get("subject") or ""), "body": str(d["body"])}
    except ValueError:
        pass
    return {"subject": "", "body": c}


def current_text(current: dict[str, Any] | None) -> str:
    """The user's own text of a compose payload: no quoted history, no signature."""
    from mailr.agent.screen import html_to_text
    from mailr.agent.tools import _split_new_text

    if not current:
        return ""
    if current.get("html"):
        new_html, _sig, _quote = _split_new_text(str(current["html"]))
        return html_to_text(new_html)
    return str(current.get("text") or "").strip()


def word_diff(old: str, new: str, limit: int = 30) -> list[dict[str, str]]:
    """What changed between two texts, as {old, new} chunks (for the Undo / highlight UI)."""
    import difflib

    a, b = re.findall(r"\S+|\s+", old), re.findall(r"\S+|\s+", new)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        out.append({"old": "".join(a[i1:i2])[:300], "new": "".join(b[j1:j2])[:300]})
        if len(out) >= limit:
            break
    return out


@router.post("/agent/draft")
async def draft(body: DraftIn, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """Help me write: draft a message, or edit the current draft. /name tokens in the
    instructions apply skills; the user's profile, writing defaults and relevant memory are
    part of the prompt."""
    from mailr.agent.draft_tools import identity_local_only, sending_identity, signature_text
    from mailr.agent.prompts import draft_system
    from mailr.api.schemas import message_out
    from mailr.core.drafts import reply_payload, save_draft
    from mailr.intel.llm import charge_model_budget

    await charge_model_budget()  # a token's model budget; a no-op for the signed-in user
    from mailr.knowledge import memory, profile, skills

    if body.save:
        p.require("draft")
    inv = await skills.resolve(db, p.user_id, body.instructions)
    if inv.problems:
        raise HTTPException(400, " ".join(inv.problems))
    instructions = inv.text
    target: Message | None = None
    if body.mode != "new":
        if body.message_id:
            from mailr.api.mail_read import load_message

            target = await load_message(db, p, body.message_id)
        elif body.conversation_id:
            target = (await db.execute(
                select(Message).where(Message.user_id == p.user_id, Message.conversation_id == body.conversation_id,
                                      Message.deleted.is_(False), Message.draft.is_(False), visible_message_filter(p))
                .order_by(Message.date.desc().nulls_last(), Message.id.desc()).limit(1))).scalar_one_or_none()
        if target is None or target.user_id != p.user_id:
            raise HTTPException(404, "nothing to reply to: give a message_id or conversation_id")  # i18n: internal
    conv_id = target.conversation_id if target else body.conversation_id
    cur = body.current or {}
    cur_text = current_text(cur)
    edit = bool(cur_text.strip()) and not _REWRITE.search(instructions or "")
    recipients = [str(a.get("email") if isinstance(a, dict) else a) for a in (cur.get("to") or []) + (cur.get("cc") or [])]
    recipients = [e.lower() for e in recipients if "@" in e] or [e.lower() for e in body.to or []]
    # Local-only calendars go into the bundle only for local-only mail (that draft stays on a local model).
    local_cal = bool(conv_id and await touches_local_only(db, p.user_id, conversation_ids=[conv_id]))
    try:
        bundle = await cx.build(db, p, conversation_id=conv_id, query=None if conv_id else (instructions or None),
                                people=recipients or None, budget_tokens=5000 if edit else 7000,
                                allow_local_calendar=local_cal) \
            if (conv_id or instructions or recipients) else {}
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    base = await reply_payload(db, p, target.id, body.mode) if target else None
    ident = await sending_identity(db, p.user_id, cur.get("identity_id") or (base or {}).get("identity_id"))
    # A draft saved on a local-only account, or sent from one (its text and signature), stays on a
    # local model like local-only mail.
    sensitive = bool(conv_id and await touches_local_only(db, p.user_id, conversation_ids=[conv_id])) or \
        bool(bundle and bundle.get("local_only")) or \
        bool(cx.message_ids_in(bundle) and await touches_local_only(db, p.user_id, message_ids=cx.message_ids_in(bundle))) or \
        bool(body.draft_id and await touches_local_only(db, p.user_id, message_ids=[body.draft_id])) or \
        await identity_local_only(db, ident)
    prof = await profile.get(db, p.user_id)
    context = "reply" if body.mode in ("reply", "reply_all") else "compose"
    use = list(inv.skills) + await skills.auto_for(db, p.user_id, context, exclude=inv.names)
    allow_sensitive = sensitive or await llm.available(db, p.user, "chat", sensitive=True)
    mem_text, minfo = ("", {"sensitive": False, "memory_ids": []})
    if profile.options(prof).get("auto_recall", True):
        mem_text, minfo = await memory.context_block(db, p, query=instructions, emails=recipients,
                                                     conversation_id=conv_id, allow_sensitive=allow_sensitive,
                                                     budget_chars=1200, people_local=sensitive,
                                                     query_sensitive=sensitive)
    sensitive = sensitive or minfo["sensitive"]
    system = draft_system(p.user, prof.data or {}, skills_text=skills.instructions_block(use), memory_block=mem_text,
                          edit=edit, signature=signature_text(ident.signature_html) if ident else "")
    task = instructions or ("Improve this draft." if edit else "Write an appropriate reply.")
    ask = (f"Mode: {body.mode}" + (f" to message {target.id}" if target else "") +
           f"\nInstructions from the user: {task}" + (f"\nTone: {body.tone}" if body.tone else ""))
    if edit:
        to_line = ", ".join(recipients)
        ask += (f"\n\nCurrent draft:\nSubject: {cur.get('subject') or ''}\nTo: {to_line}\n<<<\n{cur_text}\n>>>")
    if bundle:
        ask += "\n\nContext bundle (JSON):\n" + json.dumps(bundle, default=str, ensure_ascii=False)
    try:
        msg = await llm.chat(db, p.user, [{"role": "system", "content": system}, {"role": "user", "content": ask}],
                             task="chat", feature="replies", sensitive=sensitive, temperature=0.2 if edit else 0.4, max_tokens=1500,
                             response_format={"type": "json_schema",
                                              "json_schema": {"name": "draft", "schema": _DRAFT_SCHEMA, "strict": True}},
                             extra={"chat_template_kwargs": {"enable_thinking": False}})
    except llm.NoModel:
        raise ApiError(409, "models.none_configured") from None
    except llm.LocalOnlyViolation as e:
        raise HTTPException(409, str(e)) from None
    except Exception as e:  # noqa: BLE001
        raise ApiError(502, "models.call_failed", error=str(e)[:277]) from None
    got = _parse_draft(msg.get("content") or "")
    ctx = ToolCtx(db=db, p=p)
    args: dict[str, Any] = {"body": got["body"], "identity_id": ident.id if ident else None}
    if target is None:
        args.update({"subject": got["subject"] or cur.get("subject") or "", "to": body.to or []})
    if edit and cur.get("subject") and got["subject"] and got["subject"] != cur.get("subject"):
        args["subject"] = got["subject"]
    payload = await compose_payload(ctx, args, base=base)
    out: dict[str, Any] = {"payload": payload, "model": msg.get("_model"), "sensitive": sensitive,
                           "sources": cx.conversation_ids_in(bundle) if bundle else [], "edit": edit,
                           "skills": [x.name for x in use], "memory_ids": minfo["memory_ids"]}
    if edit:
        out["previous_text"] = cur_text
        # The diff is of what the user reads: no Markdown marks (the payload has them as HTML).
        from mailr.agent.draft_tools import is_html
        from mailr.core import mdmail

        out["changes"] = word_diff(cur_text, got["body"] if is_html(got["body"]) else mdmail.to_text(got["body"]))
    pending = None
    if body.save and p.is_token:
        # A token saving a model-written draft passes the draft check of the agent tools.
        from mailr.agent.tools import _draft_gate
        from mailr.core.drafts import draft_payload, load_draft

        before = await draft_payload(db, p, await load_draft(db, p, body.draft_id)) if body.draft_id else None
        try:
            pending = await _draft_gate(ctx, payload, draft_id=body.draft_id, before=before, conversation_id=conv_id)
        except ToolError as e:
            raise HTTPException(403, str(e)) from None
        if pending is not None:
            out["approval"] = pending.data
    if body.save and pending is None:
        m = await save_draft(db, p, payload, body.draft_id)
        out["draft"] = message_out(m)
    await audit(db, p, "agent.draft", str(conv_id or ""), {"mode": body.mode, "instructions": body.instructions,
                                                          "edit": edit}, {"saved": body.save})
    await db.commit()
    return out


# ---------------- tool API (used by the MCP server and other agents) ----------------


@router.get("/agent/tools")
async def list_tools(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    from mailr.agent import web_tools
    from mailr.agent.speakr_tools import hidden_tools

    tools = available_tools(p, await hidden_tools(db, p) | await web_tools.hidden_tools(db, p))
    # A token sees only the tools it may call; the full list is for the signed-in user.
    return {"tools": [t.schema(p.user)["function"] | {"scope": t.scope, "hints": tool_hints(t.name)} for t in tools],
            "all": sorted(t.name for t in tools) if p.is_token else sorted(TOOLS)}


@router.post("/agent/tools/{name}")
async def call_tool(name: str, args: dict[str, Any], p: Principal = Depends(current),
                    db: AsyncSession = Depends(get_db)):
    if name not in TOOLS:
        raise HTTPException(404, f"unknown tool {name}")  # i18n: internal
    try:
        res = await run_tool(ToolCtx(db=db, p=p, images=True), name, args)
    except ToolError as e:
        await audit(db, p, f"tool.{name}", "", args, {"error": str(e)}, ok=False)
        await db.commit()
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    await audit(db, p, f"tool.{name}", "", args, {"summary": res.summary()})
    await db.commit()
    body = {"ok": True, "result": json.loads(json.dumps(publicid.out(res.data), default=str))}
    if res.pending is not None:
        # HTTP 202: nothing happened yet; poll GET /approvals/{id} for the outcome.
        body["status"] = "pending_approval"
        body["approval"] = json.loads(json.dumps(publicid.out(res.pending), default=str))
        return JSONResponse(body, status_code=202, headers={"X-Mailr-Content": "untrusted"})
    return JSONResponse(body, headers={"X-Mailr-Content": "untrusted"})
