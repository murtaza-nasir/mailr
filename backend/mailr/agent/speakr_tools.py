"""The assistant's Speakr tools (section 6.1).

Each needs the `recordings` scope, is offered only while the caller can see a Speakr connector
(`hidden_tools`), and is built on the same functions as the REST routes (integrations/speakr/service.py).
Their results carry transcripts, summaries, notes and titles that other people said or Speakr's
model wrote, so every read tool is in UNTRUSTED_TOOLS and its content sits in {"untrusted": true}.

Local-only connectors (the default): their recordings reach the built-in assistant only while its
chat runs on a local model (tools.local_ok); the result is then `sensitive`, which keeps the session
on local models. A chat on a hosted model gets "Recordings are hidden from this model".

Speakr's own chat is called only by ask_recording, which is offered only for connectors whose
recordings may reach Speakr's models (store.speakr_model_ok: `normal`, or Speakr reports
`models_local`) on a Speakr with chat sources; its citations are checked against the transcript
like the assistant's own. Otherwise the assistant reads the transcript itself.

Writes to Speakr (integrations/speakr/writes.py) go through the permission engine:
send_to_speakr is speakr.upload, update_recording is speakr.write (both ask by default), and
share_recording is speakr.share, which always asks. update_recording and share_recording also need
their switch in the connector card. An errand never creates a share link.
"""

from __future__ import annotations

from typing import Any

from mailr.agent.tools import ToolCtx, ToolError, ToolResult, _obj, _u, gate, local_ok, tool
from mailr.integrations.speakr import client as sc
from mailr.integrations.speakr import service, store

HIDDEN = "Recordings are hidden from this model (the Speakr account is local-only). Say so if the answer may be incomplete."
CITE_HELP = ("Cite a recording as [[r:<recording_id>]] and a moment as [[r:<recording_id>@<seconds> \"short exact quote\"]] "
             "with the quote copied from the transcript.")

READ_TOOLS = ("list_recordings", "get_recording", "get_transcript", "search_recordings", "get_summary",
              "recordings_for_event", "propose_action_items", "ask_recording")
WRITE_TOOLS = ("link_recording", "draft_follow_up", "send_to_speakr", "update_recording", "share_recording")
ALL_TOOLS = READ_TOOLS + WRITE_TOOLS


def speakr_chat_ok(conn, *, strict: bool = False) -> bool:
    """ask_recording may use Speakr's chat for this connector (`strict`: the owner's
    local-models-only rule, intel/locality.py)."""
    return store.speakr_model_ok(conn, strict=strict) and store.feature(conn, "chat_sources")


async def hidden_tools(db, p) -> set[str]:
    """The Speakr tools are offered only while the caller can see a Speakr connector, and
    ask_recording only while one of them may use Speakr's own model."""
    conns = await store.visible_connectors(db, p)
    if not conns:
        return set(ALL_TOOLS)
    from mailr.intel import locality

    strict = await locality.strict(db, p.user)
    return set() if any(speakr_chat_ok(c, strict=strict) for c in conns) else {"ask_recording"}


async def _conns(ctx: ToolCtx) -> tuple[list, int]:
    """The connectors this model may read, and how many local-only ones it may not."""
    conns = await store.visible_connectors(ctx.db, ctx.p)
    if not conns:
        raise ToolError("no Speakr account is connected (Settings → Mail → Mail accounts → Speakr)")
    if await local_ok(ctx):
        return conns, 0
    allowed = [c for c in conns if c.data_class != "local_only"]
    return allowed, len(conns) - len(allowed)


def _sensitive(conns: list) -> bool:
    return any(c.data_class == "local_only" for c in conns)


async def _one(ctx: ToolCtx, args: dict[str, Any]):
    conns, hidden = await _conns(ctx)
    try:
        rid = int(args.get("recording_id"))
    except (TypeError, ValueError):
        raise ToolError("'recording_id' must be an integer") from None
    try:
        return await store.find(ctx.db, ctx.p, rid, args.get("connector_id"), conns)
    except store.RecordingNotFound:
        raise ToolError(HIDDEN if hidden else "recording not found") from None


def _speakr_error(e: sc.SpeakrError) -> ToolError:
    if isinstance(e, sc.SpeakrNotFound):
        return ToolError("recording not found in Speakr")
    return ToolError(f"Speakr: {e}")


def _f(v: Any, name: str) -> float | None:
    if v in (None, ""):
        return None
    t = sc.seconds(v)
    if t is None:
        raise ToolError(f"'{name}' must be seconds or m:ss")
    return t


# ACT recording.search @assistant
@tool("list_recordings",
      "List the user's Speakr meeting recordings (newest first): title, meeting date, duration, participants, tags, "
      "status, links. Filters: q (title, participants, tags, folder), date_from, date_to (ISO dates), participant, "
      "tag, status (completed, processing, failed), linked_to (an event uid or a conversation id). " + CITE_HELP,
      _obj({"q": {"type": "string"}, "date_from": {"type": "string"}, "date_to": {"type": "string"},
            "participant": {"type": "string"}, "tag": {"type": "string"}, "status": {"type": "string"},
            "linked_to": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
      scope="recordings")
async def list_recordings(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    conns, hidden = await _conns(ctx)
    items = await service.list_recordings(
        ctx.db, ctx.p, conns=conns, q=args.get("q") or "", date_from=args.get("date_from"), date_to=args.get("date_to"),
        tag=args.get("tag") or "", participant=args.get("participant") or "", status=args.get("status") or "",
        linked=str(args.get("linked_to") or ""), limit=min(int(args.get("limit") or 20), service.MAX_LIST))
    for it in items:
        it["title"] = _u(it["title"])
        it["participants"] = _u(it["participants"])
        if "summary_match" in it:
            it["summary_match"] = _u(it["summary_match"])
    out: dict[str, Any] = {"items": items}
    if hidden:
        out["note"] = HIDDEN
    return ToolResult(out, sensitive=_sensitive(conns))


# ACT recording.view @assistant
@tool("get_recording",
      "One recording: metadata and the parts asked for in `include` (summary, notes, events, speakers, links; "
      "default summary and links). Read the summary before the transcript. " + CITE_HELP,
      _obj({"recording_id": {"type": "integer"}, "connector_id": {"type": "integer"},
            "include": {"type": "array", "items": {"type": "string", "enum": ["summary", "notes", "events", "speakers", "links"]}}},
           ["recording_id"]),
      scope="recordings")
async def get_recording(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    conn, row = await _one(ctx, args)
    include = tuple(args.get("include") or ("summary", "links"))
    try:
        _, out = await service.get_recording(ctx.db, ctx.p, row.recording_id, conn.id, include)
    except sc.SpeakrError as e:
        raise _speakr_error(e) from None
    for k in ("title", "participants", "summary", "notes", "events", "speakers"):
        if k in out:
            out[k] = _u(out[k])
    return ToolResult(out, sensitive=conn.data_class == "local_only")


# ACT recording.view
@tool("get_transcript",
      "Read a recording's transcript in a time range (start, end in seconds), optionally one speaker's turns or the "
      "turns containing every word of q. Returns segments {t, end, speaker, text}; when `truncated` is true, call "
      "again with start = next_start. max_chars defaults to 6000 (at most 20000). " + CITE_HELP,
      _obj({"recording_id": {"type": "integer"}, "connector_id": {"type": "integer"},
            "start": {"type": "number"}, "end": {"type": "number"}, "speaker": {"type": "string"},
            "q": {"type": "string"}, "max_chars": {"type": "integer", "minimum": 500, "maximum": 20000}},
           ["recording_id"]),
      scope="recordings")
async def get_transcript(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    conn, row = await _one(ctx, args)
    try:
        _, out = await service.get_transcript(ctx.db, ctx.p, row.recording_id, conn.id, start=_f(args.get("start"), "start"),
                                              end=_f(args.get("end"), "end"), speaker=args.get("speaker"),
                                              q=args.get("q"), max_chars=int(args.get("max_chars") or 6000))
    except sc.SpeakrError as e:
        raise _speakr_error(e) from None
    out["title"] = _u(out["title"])
    out["segments"] = _u(out["segments"])
    out["cite"] = store.cite(row.recording_id)
    return ToolResult(out, sensitive=conn.data_class == "local_only")


# ACT recording.view
@tool("get_summary", "The Speakr summary of a recording (Markdown). " + CITE_HELP,
      _obj({"recording_id": {"type": "integer"}, "connector_id": {"type": "integer"}}, ["recording_id"]),
      scope="recordings")
async def get_summary(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    conn, row = await _one(ctx, args)
    text = ""
    if row.has_summary:
        try:
            text = await store.part(ctx.db, conn, row.recording_id, "summary")
        except sc.SpeakrError as e:
            raise _speakr_error(e) from None
    return ToolResult({"recording_id": row.recording_id, "title": _u(row.title), "summary": _u(text),
                       "has_summary": bool(text), "cite": store.cite(row.recording_id)},
                      sensitive=conn.data_class == "local_only")


# ACT recording.search
@tool("search_recordings",
      "Search recordings by words: matches titles, participants, tags and the summaries of all recordings, and the "
      "transcripts (of every recording when Speakr can search them; otherwise only the most recent ones are scanned, "
      "and the result's note says so). Hits carry where they matched (summary, notes or transcript), the moment (t), "
      "the speaker and the text. " + CITE_HELP,
      _obj({"q": {"type": "string"}, "date_from": {"type": "string"}, "date_to": {"type": "string"},
            "recording_ids": {"type": "array", "items": {"type": "integer"}},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 20}}, ["q"]),
      scope="recordings")
async def search_recordings(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    conns, hidden = await _conns(ctx)
    try:
        from mailr.agent.tools import query_sensitive

        out = await service.search(ctx.db, ctx.p, str(args.get("q") or ""), date_from=args.get("date_from"),
                                   date_to=args.get("date_to"), recording_ids=args.get("recording_ids"),
                                   top_k=int(args.get("top_k") or 10), conns=conns,
                                   query_sensitive=query_sensitive(ctx))
    except sc.SpeakrError as e:
        raise _speakr_error(e) from None
    for r in out["recordings"]:
        r["title"] = _u(r["title"])
        r["participants"] = _u(r["participants"])
    out["hits"] = _u(out["hits"])
    if out.get("more"):
        out["note"] = ("Summaries of all recordings were searched; only the transcripts of the most recent ones were "
                       "scanned. Narrow the dates to scan older transcripts.")
    if hidden:
        out["hidden_note"] = HIDDEN
    return ToolResult(out, sensitive=_sensitive(conns))


# ACT recording.ask @assistant (Speakr's own model)
@tool("ask_recording",
      "Ask Speakr's own model a question about one recording. Offered only for accounts whose recordings may go to "
      "Speakr's models. The answer cites moments as [[r:<recording_id>@<seconds> \"quote\"]]; a citation whose quote "
      "is not in the transcript near that moment is removed. Prefer get_summary and get_transcript when you can "
      "answer yourself.",
      _obj({"recording_id": {"type": "integer"}, "connector_id": {"type": "integer"},
            "question": {"type": "string"}}, ["recording_id", "question"]),
      scope="recordings")
async def ask_recording(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.agent.tools import query_sensitive
    from mailr.intel import locality

    conn, row = await _one(ctx, args)
    if not speakr_chat_ok(conn, strict=await locality.strict(ctx.db, ctx.p.user)):
        raise ToolError("Speakr's own model may not read this recording; read the transcript instead")
    if query_sensitive(ctx) and not store.speakr_models_local(conn):
        # The question is model-written and may quote local-only mail this chat read.
        raise ToolError("this chat has read local-only content, so the question may not go to Speakr's model; "
                        "read the summary or the transcript instead")
    question = str(args.get("question") or "").strip()
    if not question:
        raise ToolError("'question' is required")
    try:
        d = await (await store.client_for(ctx.db, conn)).chat(row.recording_id, question, with_sources=True)
        segs = await store.part(ctx.db, conn, row.recording_id, "transcript")
    except sc.SpeakrError as e:
        raise _speakr_error(e) from None
    answer, cites, dropped = verified_answer(row.recording_id, str(d.get("response") or ""), d.get("sources") or [], segs)
    return ToolResult({"recording_id": row.recording_id, "title": _u(row.title), "answer": _u(answer),
                       "citations": cites, "dropped_citations": dropped, "source": "speakr"},
                      sensitive=conn.data_class == "local_only")


def verified_answer(rid: int, answer: str, sources: list[Any], segs: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]], int]:
    """Speakr's numbered citations ([1], [2]) as mailr's own [[r:id@t "quote"]] markers, each
    checked against the transcript with store.verify_quote (Speakr's `verified` is not trusted).
    A citation that does not check out is removed from the answer."""
    import re

    out: list[dict[str, Any]] = []
    marks: dict[int, str] = {}
    dropped = 0
    for src in sources:
        if not isinstance(src, dict) or not isinstance(src.get("n"), int):
            continue
        t = sc.seconds(src.get("start_time"))
        quote = re.sub(r"\s+", " ", str(src.get("quote") or "")).strip().replace('"', "'")[:200]
        ok = bool(quote) and store.verify_quote(segs, t, quote)[0]
        if not ok:
            marks[src["n"]] = ""
            dropped += 1
            continue
        marker = f'[[r:{int(rid)}@{int(t)} "{quote}"]]' if t is not None else f'[[r:{int(rid)}]]'
        marks[src["n"]] = marker
        out.append({"n": src["n"], "t": t, "speaker": str(src.get("speaker") or ""), "quote": quote, "cite": marker})
    text = re.sub(r"\s?\[(\d+)\]", lambda m: (" " + marks[int(m.group(1))]) if marks.get(int(m.group(1))) else
                  ("" if int(m.group(1)) in marks else m.group(0)), answer)
    return text, out, dropped


# ACT recording.view
@tool("recordings_for_event",
      "Recordings linked to (or suggested for) a calendar event occurrence: uid and occurrence as calendar tools "
      "return them (default: the event focused on screen). previous=true instead finds the last recording of the same "
      "meeting before this occurrence (same series, or the same title with the same people): meeting prep.",
      _obj({"uid": {"type": "string"}, "occurrence": {"type": "string"}, "previous": {"type": "boolean"}}),
      scope="recordings")
async def recordings_for_event(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    conns, hidden = await _conns(ctx)
    uid = args.get("uid") or ((ctx.ui.get("focused_event") or {}).get("uid") if isinstance(ctx.ui, dict) else None)
    occ = args.get("occurrence") or ((ctx.ui.get("focused_event") or {}).get("occurrence") if isinstance(ctx.ui, dict) else None)
    if not uid:
        raise ToolError("'uid' is required")
    if args.get("previous"):
        # ACT event.last_recording @assistant
        from mailr.integrations.speakr import prep

        try:
            res = await prep.last_time(ctx.db, ctx.p, str(uid), occ)
        except LookupError as e:
            raise ToolError(str(e).strip("'")) from None
        except sc.SpeakrError as e:
            raise _speakr_error(e) from None
        item = res.get("item")
        if item and item["connector_id"] not in {c.id for c in conns}:
            item = None
        if item:
            item["title"] = _u(item["title"])
            item["participants"] = _u(item["participants"])
            if item.get("summary_line") is not None:
                item["summary_line"] = _u(item["summary_line"])
        out = {"previous": item, "upcoming": res.get("upcoming", False)}
        if hidden:
            out["note"] = HIDDEN
        return ToolResult(out, sensitive=bool(item and item.get("local_only")))
    items = [x for x in await service.event_recordings(ctx.db, ctx.p, str(uid), occ)
             if x["connector_id"] in {c.id for c in conns}]
    for it in items:
        it["title"] = _u(it["title"])
        it["participants"] = _u(it["participants"])
        if it.get("summary_excerpt") is not None:
            it["summary_excerpt"] = _u(it["summary_excerpt"])
    out: dict[str, Any] = {"items": items}
    if hidden:
        out["note"] = HIDDEN
    return ToolResult(out, sensitive=_sensitive(conns))


# ACT recording.to_tasks @assistant
@tool("propose_action_items",
      "Propose the action items of a meeting recording (text, owner, due, t). Nothing is created: show them to the "
      "user, and create the ones they want with create_task (put the recording's url in the notes).",
      _obj({"recording_id": {"type": "integer"}, "connector_id": {"type": "integer"}}, ["recording_id"]),
      scope="recordings")
async def propose_action_items(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.integrations.speakr import action_items
    from mailr.intel import llm

    conn, row = await _one(ctx, args)
    try:
        out = await action_items.propose(ctx.db, ctx.p, row.recording_id, conn.id)
    except sc.SpeakrError as e:
        raise _speakr_error(e) from None
    except (llm.NoModel, llm.LocalOnlyViolation) as e:
        raise ToolError(f"no model may read this recording: {e}"[:200]) from None
    out["items"] = _u(out["items"])
    out["recording"]["title"] = _u(out["recording"]["title"])
    return ToolResult(out, sensitive=conn.data_class == "local_only")


# ACT recording.link @assistant
@tool("link_recording",
      "Link a recording to a calendar event occurrence (event_uid and occurrence), a conversation (conversation_id) "
      "or a task (task_uid). Confirms a suggested link.",
      _obj({"recording_id": {"type": "integer"}, "connector_id": {"type": "integer"}, "event_uid": {"type": "string"},
            "occurrence": {"type": "string"}, "conversation_id": {"type": "integer"}, "task_uid": {"type": "string"}},
           ["recording_id"]),
      scope="recordings")
async def link_recording(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    conn, row = await _one(ctx, args)
    if args.get("event_uid"):
        kind, target = "event", str(args["event_uid"])
    elif args.get("conversation_id"):
        kind, target = "conversation", str(int(args["conversation_id"]))
    elif args.get("task_uid"):
        kind, target = "task", str(args["task_uid"])
    else:
        raise ToolError("give event_uid, conversation_id or task_uid")
    payload = {"recording_id": row.recording_id, "connector_id": conn.id, "kind": kind, "target": target,
               "occurrence": str(args.get("occurrence") or "")}
    pending = await gate(ctx, "speakr.link", target=f"{kind} {target}", cvals={"connectors": [conn.id]},
                         payload=payload, preview={"title": "Link a recording", "lines": [row.title, f"{kind}: {target}"]})
    if pending is not None:
        return pending
    try:
        lk = await service.set_link(ctx.db, ctx.p, rid=row.recording_id, cid=conn.id, kind=kind, target=target,
                                    occurrence=payload["occurrence"], source="assistant")
    except (LookupError, ValueError) as e:
        raise ToolError(str(e).strip("'")) from None
    await ctx.db.commit()
    return ToolResult({"ok": True, "link": store.link_out(lk)}, sensitive=conn.data_class == "local_only")


# ACT recording.follow_up @assistant
@tool("draft_follow_up",
      "Draft the follow-up email of a meeting recording (never sent): recipients are the linked event's guests "
      "without the user, lists and robots; a reply in the event's email thread when it has one. mode summary puts "
      "Speakr's summary in the draft as written; mode write has mailr's model write it (instructions optional). "
      "`to` overrides the recipients.",
      _obj({"recording_id": {"type": "integer"}, "connector_id": {"type": "integer"},
            "mode": {"type": "string", "enum": ["summary", "write"]}, "to": {"type": "array", "items": {"type": "string"}},
            "instructions": {"type": "string"}, "event_uid": {"type": "string"}, "occurrence": {"type": "string"}},
           ["recording_id"]),
      scope="recordings")
async def draft_follow_up(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.api.schemas import message_out
    from mailr.integrations.speakr import followup
    from mailr.intel import llm
    from mailr.models import Message

    if not ctx.p.has("draft"):
        raise ToolError("this caller lacks the 'draft' scope needed for draft_follow_up")
    conn, row = await _one(ctx, args)
    pending = await gate(ctx, "mail.draft", target=row.title, preview={"title": "Draft a follow-up email",
                                                                         "lines": [row.title]})
    if pending is not None:
        return pending
    from mailr.agent.tools import query_sensitive

    mode = "assistant" if args.get("mode") == "write" else "summary"
    to = args.get("to")

    async def check(payload: dict[str, Any], guests: set[str]) -> ToolResult | None:
        # The ordinary draft gate: recipients the model chose beyond the meeting's guests and the
        # thread it replies to ask after reading mail (or for a token), like create_draft's.
        from mailr.agent.draft_tools import _draft_gate

        parent = payload.get("in_reply_to_message_id")
        pm = await ctx.db.get(Message, int(parent)) if parent else None
        return await _draft_gate(ctx, payload, known=guests,
                                 conversation_id=pm.conversation_id if pm is not None and pm.user_id == ctx.p.user_id
                                 else None)

    try:
        out = await followup.draft_follow_up(ctx.db, ctx.p, row.recording_id, conn.id, mode=mode,
                                             uid=args.get("event_uid"), occurrence=args.get("occurrence"),
                                             to=to if isinstance(to, list) and to else None,
                                             instructions=str(args.get("instructions") or ""),
                                             sensitive=query_sensitive(ctx), check=check)
    except sc.SpeakrError as e:
        raise _speakr_error(e) from None
    except (llm.NoModel, llm.LocalOnlyViolation) as e:
        raise ToolError(f"no model may write from this recording: {e}"[:200]) from None
    except (LookupError, ValueError) as e:
        raise ToolError(str(e).strip("'")) from None
    if out.get("pending") is not None:
        held = out["pending"]
        held.data["note"] = ("Saving this follow-up draft waits for the user's approval (it goes to people who were "
                             "not at the meeting, or a rule asks). Do not retry it; tell the user.")
        return held
    m = await ctx.db.get(Message, out["draft_id"])
    data = {"draft_id": out["draft_id"], "conversation_id": out["conversation_id"], "to": out["to"],
            "subject": out["payload"].get("subject"), "reply": out["reply"], "mode": mode, "sent": False}
    return ToolResult(data, message_ids=[m.id], conversation_ids=[m.conversation_id] if m.conversation_id else [],
                      draft={"draft_id": m.id, "payload": out["payload"], "message": message_out(m)},
                      sensitive=conn.data_class == "local_only")


def _write_error(e: Exception) -> ToolError:
    from mailr.integrations.speakr import writes

    if isinstance(e, writes.WriteOff):
        return ToolError(str(e))
    if isinstance(e, sc.SpeakrError):
        return _speakr_error(e)
    return ToolError(str(e).strip("'"))


# ACT mail.to_speakr @assistant
@tool("send_to_speakr",
      "Send an audio or video attachment of an email to Speakr for transcription (asks the user first). The "
      "recording's notes link back to the email, and the conversation is linked to the new recording.",
      _obj({"message_id": {"type": "integer"}, "attachment": {"type": "integer", "description": "the attachment's part"},
            "connector_id": {"type": "integer"}}, ["message_id", "attachment"]),
      scope="recordings")
async def send_to_speakr(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.integrations.speakr import writes

    conns, _ = await _conns(ctx)
    if not conns:
        raise ToolError(HIDDEN)
    try:
        mid, part = int(args["message_id"]), int(args["attachment"])
    except (KeyError, TypeError, ValueError):
        raise ToolError("'message_id' and 'attachment' must be integers") from None
    errand = bool((ctx.chat_context or {}).get("errand"))
    # The account first, so a rule limited to one account holds when the model leaves it out.
    try:
        conn_id = (await writes.connector_for_upload(ctx.db, ctx.p, args.get("connector_id"))).id
    except (LookupError, TypeError, ValueError):
        raise ToolError("no usable Speakr account for this upload") from None
    if conn_id not in {c.id for c in conns}:
        raise ToolError(HIDDEN)
    try:
        await writes.check_upload_class(ctx.db, ctx.p, mid, conn_id)
    except writes.WriteOff as e:
        raise ToolError(str(e)) from None
    payload = {"message_id": mid, "part": part, "connector_id": conn_id,
               "source": "order" if errand else "assistant"}
    pending = await gate(ctx, "speakr.upload", target=f"message {mid} part {part}",
                         cvals={"connectors": [conn_id]},
                         payload=payload, preview={"title": "Transcribe an attachment in Speakr",
                                                   "lines": [f"message {mid}, attachment {part}"]})
    if pending is not None:
        return pending
    try:
        out = await writes.upload_attachment(ctx.db, ctx.p, mid, part, conn_id,
                                             source=payload["source"])
    except Exception as e:  # noqa: BLE001
        raise _write_error(e) from None
    await ctx.db.commit()
    return ToolResult(out, sensitive=bool(out.get("local_only")))


# ACT recording.edit @assistant
@tool("update_recording",
      "Change a recording in Speakr: its title, its participants, a line appended to its notes, or its tags (the "
      "recording's tags become exactly the list given, by name). Asks the user first; only when write-backs are on "
      "for the account. A group tag that shares the recording with its group is a share: it always asks, and an "
      "errand cannot add one.",
      _obj({"recording_id": {"type": "integer"}, "connector_id": {"type": "integer"}, "title": {"type": "string"},
            "participants": {"type": "array", "items": {"type": "string"}}, "append_notes": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}}},
           ["recording_id"]),
      scope="recordings")
async def update_recording(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.integrations.speakr import writes

    conn, row = await _one(ctx, args)
    try:
        writes.require(conn, "write_back")
        if not row.can_edit:
            raise writes.WriteOff(writes.SHARED_READ_ONLY)
    except writes.WriteOff as e:
        raise ToolError(str(e)) from None
    fields = {k: args[k] for k in ("title", "participants", "append_notes") if args.get(k) not in (None, "", [])}
    if isinstance(args.get("tags"), list):
        fields["tags"] = [str(t) for t in args["tags"]][:50]
    if not fields:
        raise ToolError("give title, participants, append_notes or tags")
    sharing: list[str] = []
    if fields.get("tags"):
        try:
            sharing = await writes.sharing_tags(ctx.db, conn, row, fields["tags"])
        except (ValueError, sc.SpeakrError) as e:
            raise _write_error(e) from None
    if sharing and (ctx.chat_context or {}).get("errand"):
        raise ToolError("an errand cannot add a tag that shares the recording with a group; tell the user")
    lines = [f"{k}: {v}" for k, v in fields.items()]
    if sharing:
        lines.append(f"Shares the recording with the group of: {', '.join(sharing)}")
    from mailr.agent.agentwrite import local_copy_reason

    # From a chat that has read local-only content, a title, participants, notes or tags written
    # into a Speakr account that is not local-only would copy it where hosted models may read it.
    local = local_copy_reason(ctx) if conn.data_class != "local_only" else None
    pending = await gate(ctx, "speakr.share" if sharing else "speakr.write", target=row.title,
                         cvals=writes.recording_ctx(conn, row),
                         payload={"recording_id": row.recording_id, "connector_id": conn.id, **fields},
                         preview={"title": f"Edit the recording “{row.title}” in Speakr", "lines": lines,
                                  **({"danger": True} if sharing else {})},
                         force_ask=local)
    if pending is not None:
        return pending
    try:
        out = await writes.update_recording(ctx.db, ctx.p, row.recording_id, conn.id, **fields)
    except Exception as e:  # noqa: BLE001
        raise _write_error(e) from None
    await ctx.db.commit()
    return ToolResult({"ok": True, "recording_id": row.recording_id, "notes_appended": out["notes_appended"]},
                      sensitive=conn.data_class == "local_only")


# ACT recording.share_link @assistant
@tool("share_recording",
      "Create (or reuse) a public Speakr link to a recording, with its summary (and its notes when share_notes is "
      "true). Anyone with the link can open it, so this always asks the user; an errand cannot do it.",
      _obj({"recording_id": {"type": "integer"}, "connector_id": {"type": "integer"},
            "share_notes": {"type": "boolean"}}, ["recording_id"]),
      scope="recordings")
async def share_recording(ctx: ToolCtx, args: dict[str, Any]) -> ToolResult:
    from mailr.integrations.speakr import writes

    if (ctx.chat_context or {}).get("errand"):
        raise ToolError("an errand cannot create share links; tell the user, who can share it from the recording")
    conn, row = await _one(ctx, args)
    try:
        writes.require(conn, "share_links")
    except writes.WriteOff as e:
        raise ToolError(str(e)) from None
    notes = bool(args.get("share_notes"))
    pending = await gate(ctx, "speakr.share", target=row.title, cvals=writes.recording_ctx(conn, row),
                         payload={"recording_id": row.recording_id, "connector_id": conn.id, "share_notes": notes},
                         preview={"title": f"Create a public link to “{row.title}”", "danger": True,
                                  "lines": ["Anyone with the link can open the recording and its summary"
                                            + (" and notes" if notes else "") + "."]})
    if pending is not None:
        return pending
    try:
        out = await writes.share_recording(ctx.db, ctx.p, row.recording_id, conn.id, share_notes=notes)
    except Exception as e:  # noqa: BLE001
        raise _write_error(e) from None
    return ToolResult(out, sensitive=conn.data_class == "local_only")


async def person_recordings(ctx: ToolCtx, email: str) -> tuple[dict[str, Any] | None, bool]:
    """get_person's recent_recordings (agent/mail_tools.py): (the result or None, sensitive)."""
    from mailr.integrations.speakr import people

    if not ctx.p.has("recordings") or not await store.has_visible(ctx.db, ctx.p):
        return None, False
    conns, hidden = await _conns(ctx)
    res = await people.person_recordings(ctx.db, ctx.p, email, conns=conns)
    for it in res["items"]:
        it["title"] = _u(it["title"])
        it["participants"] = _u(it["participants"])
    if hidden:
        res["note"] = HIDDEN
    return res, bool(res.pop("local_only", False))
