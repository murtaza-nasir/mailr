# ACT-SURFACES: api assistant
"""Canary sweep (outbound data policy, part 4): no local-only content reaches a hosted service,
whichever feature sends it.

One user gets local-only data with unique canary strings: mail on a local-only account (subject,
body, sender name, attachment name, a reply), a local-only note and table, a sensitive memory and
entity, an event on a local-only calendar and a recording on a local-only Speakr account. Ordinary
data gets canaries of its own. The only services are fakes that record every request
(helpers/outbound_sweep.py): an OpenAI-compatible model server (chat, classify, embeddings), a Jev
decision endpoint, a web search API, a page reader and pages, and the Speakr fake.

Pass 1 registers hosted services only and runs every model-using feature: the assistant chat over
the local-only data, every assistant tool with local-only canaries in its arguments in a chat that
has read local-only content, the REST features (summaries, translation, reply suggestions,
drafting help, events from mail, meeting prep, skill "Try it", search and recall), and the
background jobs (triage, categories, events, standing orders, signatures, memory learning, chat
suggestions, reviews, cleanup suggestions, embeddings of passages, memories, notes and tables).
It fails when a local-only canary appears in any request to a hosted service or to Speakr's own
models, and it checks that ordinary canaries did reach the hosted fakes.

Pass 2 swaps in local services (hosts that resolve to private addresses) and runs the same features:
local-only canaries must now reach them, which shows that pass 1 exercised the paths.

Coverage is data-driven: every call site of a model entry point in mailr/ (llm.chat, chat_stream,
embed_request, embed_texts, embed_query, embed_pending_passages, jev.*) and every literal
`feature=` name must be reached by the sweep or appear in an exemption below with its reason. A new
feature or call site without coverage fails this test.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any

import pytest
from helpers.agent import _events, _ingest, _setup_identity, _token_client
from helpers.agent_calendar import _connect_direct
from helpers.outbound_sweep import (
    FakeNet,
    Meter,
    static_features,
    static_sites,
    text_turn,
    tool_turn,
)
from helpers.websearch import _clean  # noqa: F401  (autouse: no providers, no egress)
from speakr_fake import connect as speakr_connect
from speakr_fake import speakr  # noqa: F401
from sqlalchemy import select, update

from mailr.db import sessionmaker
from mailr.models import Entity, Memory, ModelEndpoint, User, WebSearchProvider

pytestmark = pytest.mark.timeout(900)

# Call sites the sweep does not run, with the reason. Keys as helpers.outbound_sweep.static_sites.
SITE_EXEMPT: dict[str, str] = {
    "api/admin_routes.py::_embed_unchecked": "the admin's endpoint check embeds the fixed word 'test'",
    "api/admin_routes.py::test_endpoint": "the admin's endpoint check sends fixed test text, no user data",
    "cli.py::_reembed": "a command-line re-embedding run; the same embed_pending_passages as the job",
}
# feature= names the sweep does not run, with the reason.
FEATURE_EXEMPT: dict[str, str] = {}


def _c(kind: str, tag: str) -> str:
    return f"zq{tag}{kind}{uuid.uuid4().hex[:8]}"


@dataclass
class World:
    local: dict[str, str] = field(default_factory=dict)  # what -> canary
    normal: dict[str, str] = field(default_factory=dict)
    local_conn: int = 0
    local_me: str = ""
    normal_me: str = ""
    local_mids: list[int] = field(default_factory=list)
    local_conv: int = 0
    normal_mids: list[int] = field(default_factory=list)
    normal_conv: int = 0
    local_note: str = ""
    local_table: str = ""
    # Local-only items the tools sweep may change (its edits must not rewrite the canaries above).
    scratch_note: str = ""
    scratch_table: str = ""
    scratch_memory: int = 0
    event_uid: str = ""
    recording_id: int = 1
    speakr_conn: int = 0
    sender: str = ""
    registrar: str = ""
    audio_part: int = 0
    memory_id: int = 0


AUTHSERV = "mx.sweep.example"


def _mail(subject: str, frm: str, to: str, body: str, *, attach: str | None = None, irt: str | None = None,
          msgid: str | None = None, verified: bool = False) -> bytes:
    m = EmailMessage()
    if verified:  # the receiving server checked the sender's domain (needed for event-change cards)
        m["Authentication-Results"] = (f"{AUTHSERV}; spf=pass smtp.mailfrom=westfield.example; "
                                       "dkim=pass header.d=westfield.example; dmarc=pass header.from=westfield.example")
    m["From"], m["To"], m["Subject"] = frm, to, subject
    m["Message-ID"] = msgid or f"<{uuid.uuid4().hex}@sweep.example>"
    m["Date"] = "Mon, 05 Oct 2026 10:00:00 +0000"
    if irt:
        m["In-Reply-To"], m["References"] = irt, irt
    m.set_content(body + "\n\n--\nDana Ward\nDirector of Admissions\nWestfield University\n+1 202 555 0100\n")
    if attach:
        # Two action links: the primary action asks a model (or Jev) which one fits.
        m.add_alternative(f"<p>{body}</p><p><a href='https://westfield.example/pay'>Pay bill</a> "
                          f"<a href='https://westfield.example/track'>Track package</a></p>", subtype="html")
    if attach:
        m.add_attachment(b"ID3\x03\x00\x00\x00" + b"\x00" * 1024, maintype="audio", subtype="mpeg", filename=attach)
    return m.as_bytes()


async def _seed(client, user, speakr) -> World:
    from mailr.models import Message

    w = World()
    for tag, store in (("l", w.local), ("n", w.normal)):
        for k in ("subject", "body", "sender", "attach", "reply", "note", "table", "memory", "entity", "event",
                  "recording", "scratch"):
            store[k] = _c(k, tag)
    w.local_conn, w.local_me = await _setup_identity(user, data_class="local_only")
    _, w.normal_me = await _setup_identity(user)
    L, N = w.local, w.normal
    for d, me, mids in ((L, w.local_me, w.local_mids), (N, w.normal_me, w.normal_mids)):
        first = f"<{uuid.uuid4().hex}@sweep.example>"
        addr = f"dana{uuid.uuid4().hex[:4]}@westfield.example"
        sender = f"{d['sender']} Ward <{addr}>"
        if d is L:
            w.sender = addr
        a = _mail(f"Appeal {d['subject']} meeting on Friday", sender, me,
                  f"Can we meet Friday at 10:00 about {d['body']}? Please confirm by Thursday.",
                  attach=f"{d['attach']}.mp3", msgid=first)
        b = _mail(f"Re: Appeal {d['subject']} meeting on Friday", f"Me <{me}>", sender,
                  f"Yes, Friday works. {d['reply']}", irt=first)
        conn = w.local_conn if d is L else None
        m1, conv = await _ingest(user.id, a, connector_id=conn, roles=["inbox"])
        m2, _ = await _ingest(user.id, b, connector_id=conn, roles=["sent"])
        # A notice nobody answered: reviews and cleanup suggestions skip conversations the user wrote in.
        registrar = f"registrar{uuid.uuid4().hex[:4]}@westfield.example"
        if d is L:
            w.registrar = registrar
        c = _mail(f"Notice {d['subject']}", f"Registrar <{registrar}>", me,
                  f"A reminder about {d['body']}: the hearing moved to Thursday at 15:00.", verified=True)
        m3, _ = await _ingest(user.id, c, connector_id=conn, roles=["inbox"])
        mids += [m1, m2, m3]
        if d is L:
            w.local_conv = conv
        else:
            w.normal_conv = conv
    async with sessionmaker()() as db:
        assert (await db.get(Message, w.local_mids[0])).local_only
        assert not (await db.get(Message, w.normal_mids[0])).local_only
    msg = (await client.get(f"/api/v1/messages/{w.local_mids[0]}")).json()
    w.audio_part = next(a["part"] for a in msg["attachments"] if a["filename"].endswith(".mp3"))

    # Notes, tables, memory.
    for d, local in ((L, True), (N, False)):
        r = await client.post("/api/v1/notes", json={"kind": "text", "title": f"Plan {d['note']}",
                                                     "body": f"Notes on {d['note']} " + "and more words " * 40,
                                                     "local_only": local})
        assert r.status_code == 200, r.text
        if local:
            w.local_note = r.json()["note"]["id"]
        r = await client.post("/api/v1/tables", json={"name": f"Cases {d['table'][-6:]}", "local_only": local,
                                                      "columns": [{"name": "Case"}],
                                                      "description": f"Open cases {d['table']} " + "rows of open cases " * 30})
        assert r.status_code == 200, r.text
        if local:
            w.local_table = r.json()["table"]["id"]
    r = await client.post("/api/v1/notes", json={"kind": "text", "title": f"Scratch {L['scratch']}",
                                                 "body": "scratch " * 50, "local_only": True})
    w.scratch_note = r.json()["note"]["id"]
    r = await client.post("/api/v1/tables", json={"name": "Scratch", "local_only": True, "columns": [{"name": "Case"}],
                                                  "description": f"Scratch {L['scratch']}"})
    w.scratch_table = r.json()["table"]["id"]
    async with sessionmaker()() as db:
        scratch = Memory(user_id=user.id, text=f"Scratch {L['scratch']}", source="extracted", sensitive=True, refs=[])
        db.add(scratch)
        await db.commit()
        w.scratch_memory = scratch.id
    async with sessionmaker()() as db:
        for d, sens in ((L, True), (N, False)):
            e = Entity(user_id=user.id, kind="person", name=f"{d['entity']} Ward", aliases=[], domains=[],
                       emails=[], conversation_ids=[w.local_conv if sens else w.normal_conv],
                       summary=f"Advisor {d['entity']}", sensitive=sens, source="extracted")
            db.add(e)
            await db.flush()
            mem = Memory(user_id=user.id, entity_id=e.id, text=f"Prefers Fridays {d['memory']}", source="extracted",
                         sensitive=sens, refs=[{"conversation_id": w.local_conv if sens else w.normal_conv}])
            db.add(mem)
            await db.flush()
            if sens:
                w.memory_id = mem.id
        await db.commit()

    # A local-only calendar with an event.
    import caldav
    from dev_services import RADICALE

    from mailr.calendar.sync import sync_connector
    from mailr.models import Connector

    username, href = await _connect_direct(user.id)
    async with sessionmaker()() as db:
        cal = (await db.execute(select(Connector).where(Connector.user_id == user.id, Connector.kind == "caldav")
                                .order_by(Connector.id.desc()))).scalars().first()
        cal.data_class = "local_only"
        await db.commit()
        cal_id = cal.id
    w.event_uid = f"{uuid.uuid4().hex}@sweep"
    from datetime import UTC, datetime, timedelta

    start = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0, minute=0)
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//sweep//EN\r\nBEGIN:VEVENT\r\n"
           f"UID:{w.event_uid}\r\nDTSTAMP:{start:%Y%m%dT%H%M%SZ}\r\nDTSTART:{start:%Y%m%dT%H%M%SZ}\r\n"
           f"DTEND:{start + timedelta(hours=1):%Y%m%dT%H%M%SZ}\r\nSUMMARY:Hearing {L['event']}\r\n"
           f"DESCRIPTION:About {L['event']}\r\nORGANIZER:mailto:{w.local_me}\r\n"
           f"ATTENDEE;CN=Registrar:mailto:{w.registrar}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    await asyncio.to_thread(lambda: caldav.DAVClient(url=RADICALE, username=username, password="x")
                            .calendar(url=href).save_event(ics))
    await sync_connector(cal_id)

    # A local-only Speakr account with one recording.
    speakr.add(1, f"Advising {L['recording']}", summary=f"## Decisions\n- {L['recording']} decided",
               notes=f"Notes {L['recording']}")
    conn = await speakr_connect(client, speakr)
    assert conn["data_class"] == "local_only"
    w.speakr_conn = conn["id"]
    r = await client.patch(f"/api/v1/connectors/{conn['id']}", json={"config": {"write_back": True,
                                                                                "share_links": True}})
    assert r.status_code == 200, r.text
    return w


# ------------------------------------------------------------------ services


async def _services(net: FakeNet, locality: str) -> dict[str, str]:
    """Register this pass's fake services (and disable every other endpoint and provider)."""
    tag = "hosted" if locality == "hosted" else "local"
    llm_host = net.add(f"llm-{tag}.sweep.test", "openai", locality)
    jev_host = net.add(f"jev-{tag}.sweep.test", "jev", locality)
    search_host = net.add(f"search-{tag}.sweep.test", "search", locality)
    reader_host = net.add(f"reader-{tag}.sweep.test", "reader", locality)
    net.add("page.sweep.test", "page", "hosted")
    names = {"llm": f"sweep-llm-{tag}", "jev": f"sweep-jev-{tag}"}
    async with sessionmaker()() as db:
        await db.execute(update(ModelEndpoint).values(enabled=False))
        await db.execute(update(WebSearchProvider).values(enabled=False))
        db.add(ModelEndpoint(name=names["llm"], base_url=f"https://{llm_host}/v1", locality=locality, models=["m", "e"],
                             defaults={"chat": "m", "classify": "m", "embed": "e"}, enabled=True))
        db.add(ModelEndpoint(name=names["jev"], base_url=f"https://{jev_host}/v1", locality=locality, adapter="jev",
                             models=["jev-1"], defaults={"classify": "jev-1"}, enabled=True))
        net_kind = "local" if locality == "local" else "internet"
        db.add(WebSearchProvider(name=f"sweep-search-{tag}", purpose="search", kind="searxng",
                                 base_url=f"https://{search_host}", network=net_kind, use_egress=False, enabled=True))
        db.add(WebSearchProvider(name=f"sweep-reader-{tag}", purpose="reader", kind="jina",
                                 base_url=f"https://{reader_host}", network=net_kind, use_egress=False, enabled=True))
        await db.commit()
    from mailr.intel import embed

    embed.reset_breaker()
    return names


async def _pin_classify(user_id: int, endpoint: str | None, model: str = "m") -> None:
    async with sessionmaker()() as db:
        u = await db.get(User, user_id)
        models = dict((u.settings or {}).get("models") or {})
        if endpoint is None:
            models.pop("classify", None)
        else:
            models["classify"] = {"endpoint": endpoint, "model": model}
        u.settings = {**(u.settings or {}), "models": models, "llm_triage": True, "auto_recall": True,
                      "sender_pictures": False}
        await db.commit()


# ------------------------------------------------------------------ features


@dataclass
class Run:
    log: list[str] = field(default_factory=list)

    async def step(self, name: str, coro) -> Any:
        try:
            return await asyncio.wait_for(coro, 120)
        except Exception as e:  # noqa: BLE001  a refused or failed feature is fine here; the payloads are checked
            self.log.append(f"{name}: {type(e).__name__}: {str(e)[:160]}")
            return None


async def _job(kind: str, payload: dict[str, Any], user_id: int) -> None:
    from mailr.core import jobs
    from mailr.core.jobs import JobContext, Retry

    fn = jobs._HANDLERS[kind]
    try:
        await fn(payload, JobContext(0, kind, user_id, 1))
    except Retry:
        pass


def _load_handlers() -> None:
    import importlib

    for mod in ("mailr.intel.tasks", "mailr.knowledge.tasks", "mailr.contacts.tasks", "mailr.notes.tasks",
                "mailr.tables.tasks", "mailr.agent.review", "mailr.assistant.orders", "mailr.core.mailgroup_tasks",
                "mailr.integrations.speakr.sync"):
        importlib.import_module(mod)


def _tool_args(tool, w: World, canary: str) -> dict[str, Any]:
    """Arguments for a tool, from its schema: local-only ids and canary text everywhere."""
    props = (tool.parameters or {}).get("properties") or {}
    ids = {"message_id": w.local_mids[0], "conversation_id": w.local_conv, "draft_id": w.local_mids[1],
           "recording_id": w.recording_id, "connector_id": w.speakr_conn, "memory_id": w.scratch_memory,
           "attachment": w.audio_part, "part": w.audio_part}
    strs = {"uid": w.event_uid, "event_uid": w.event_uid, "note": w.scratch_note, "note_id": w.scratch_note,
            "id": w.scratch_note, "table": w.scratch_table, "table_id": w.scratch_table,
            "url": f"https://page.sweep.test/{canary}", "when": "this week", "view": "all", "timezone": "UTC",
            "email": w.sender, "freshness": "week"}
    from datetime import UTC, datetime, timedelta

    soon = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0, minute=0)
    dates = {"start": soon.isoformat(), "end": (soon + timedelta(hours=1)).isoformat(), "due": soon.date().isoformat(),
             "date_from": (soon - timedelta(days=30)).date().isoformat(), "date_to": soon.date().isoformat(),
             "due_before": soon.date().isoformat(), "expires_at": (soon + timedelta(days=30)).isoformat()}
    lists = {"conversation_ids": [w.local_conv], "message_ids": [w.local_mids[0]], "recording_ids": [w.recording_id],
             "to": [w.sender], "emails": [w.sender], "rows": [{"Case": canary}]}
    skip = {"occurrence", "color", "identity", "identity_id", "rrule", "recurrence", "base_version", "cursor",
            "template", "parent", "list", "calendar", "section", "except_ids"}
    out: dict[str, Any] = {}
    if "scope" in props:
        out["scope"] = {"view": "all", "q": f"{canary}"}
    if "duration" in props:
        out["duration"] = 30
    if "changes" in props:
        out["changes"] = {}
    for k, s in props.items():
        if k in skip or k in out:
            continue
        if k in dates:
            out[k] = dates[k]
            continue
        if k in lists:
            out[k] = lists[k]
            continue
        t = s.get("type") if isinstance(s, dict) else None
        if isinstance(t, list):
            t = next((x for x in t if x != "null"), None)
        if k in ids and t in ("integer", "number", None):
            out[k] = ids[k]
        elif k in strs and t in ("string", None):
            out[k] = strs[k]
        elif isinstance(s, dict) and s.get("enum"):
            out[k] = s["enum"][0]
        elif t == "string":
            out[k] = f"{canary} about {canary}"
        elif t == "integer":
            out[k] = 1
        elif t == "boolean":
            out[k] = False
        elif t == "array":
            item = (s.get("items") or {}) if isinstance(s, dict) else {}
            out[k] = [canary] if item.get("type") == "string" else []
    return out


async def _tools_sweep(run: Run, user, w: World, canary: str, *, local_model: bool) -> None:
    """Every assistant tool, in a chat that has read local-only content, with local-only ids and
    canary text in its arguments."""
    from mailr.agent.tools import TOOLS, ToolCtx, run_tool
    from mailr.core.auth import ALL_SCOPES, Principal

    for name in sorted(TOOLS):
        async with sessionmaker()() as db:
            u = await db.get(User, user.id)
            ctx = ToolCtx(db=db, p=Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"}), assistant=True,
                          chat_session_id=f"sweep-{uuid.uuid4().hex[:6]}", session_sensitive=True,
                          local_model=local_model)
            await run.step(f"tool {name}", run_tool(ctx, name, _tool_args(TOOLS[name], w, canary)))


async def _exercise(run: Run, client, user, w: World, net: FakeNet, names: dict[str, str], *, local: bool) -> None:
    from mailr.agent.tools import ToolCtx, run_tool
    from mailr.core.auth import ALL_SCOPES, Principal

    _load_handlers()
    L = w.local
    uid = user.id
    from mailr.knowledge import profile

    async with sessionmaker()() as db:  # each pass learns from all the mail again
        prof = await profile.get(db, uid)
        prof.state = {k: v for k, v in (prof.state or {}).items() if k not in ("extract_since", "extract_at")}
        await db.commit()
    all_mids = w.local_mids + w.normal_mids

    # Background jobs on every message: triage, categories, events, actions; once with the chat
    # model as the classifier and once with Jev.
    for pin in (names["llm"], names["jev"]):
        await _pin_classify(uid, pin, "m" if pin == names["llm"] else "jev-1")
        async with sessionmaker()() as db:
            from mailr.models import Message

            await db.execute(update(Message).where(Message.id.in_(all_mids)).values(intel_state="pending"))
            await db.commit()
        for mid in all_mids:
            await run.step(f"intel.message {mid}", _job("intel.message", {"message_id": mid}, uid))
        await run.step("intel.retriage", _job("intel.retriage", {"user_id": uid, "message_ids": all_mids}, uid))
        # Standing orders: a yes/no order and a choice order on every message.
        from mailr.models import AssistantOrder

        async with sessionmaker()() as db:
            db.add(AssistantOrder(user_id=uid, name="Yes/no", trigger="message", question="Is this about a meeting?",
                                  actions={"label": "Meetings"}, enabled=True))
            db.add(AssistantOrder(user_id=uid, name="Choice", trigger="message", question="Which kind?",
                                  choices=[{"key": "meeting", "name": "Meeting", "description": "a meeting"},
                                           {"key": "other", "name": "Other", "description": "anything else"}],
                                  actions={}, enabled=True))
            await db.commit()
        for mid in all_mids:
            await run.step(f"assistant.orders {mid}", _job("assistant.orders", {"message_id": mid}, uid))
        async with sessionmaker()() as db:  # cleanup suggestions guess categories for mail that has none
            await db.execute(update(Message).where(Message.id.in_(all_mids)).values(category=None))
            await db.commit()
        from mailr.core import tally

        await run.step("tally", tally.rebuild(uid))
        await run.step("cleanup.suggest", _job("cleanup.suggest", {"user_id": uid, "now": True, "by": "sender",
                                                               "scope": {"where": "all"}}, uid))
    await _pin_classify(uid, None)

    # Jobs per user.
    await run.step("contacts.signatures", _job("contacts.signatures", {"user_id": uid}, uid))
    from mailr.knowledge.tasks import extract_for_user

    res = await run.step("kb.extract", extract_for_user(uid, force=True))
    run.log.append(f"kb.extract -> {res}")
    await run.step("intel.embed_pending", _job("intel.embed_pending", {"user_id": uid}, uid))
    await run.step("kb.embed", _job("kb.embed", {"user_id": uid}, uid))
    await run.step("notes.embed", _job("notes.embed", {"user_id": uid}, uid))
    await run.step("tables.embed", _job("tables.embed", {"user_id": uid}, uid))
    for conv in (w.local_conv, w.normal_conv):
        await run.step(f"intel.summarize {conv}",
                       _job("intel.summarize", {"user_id": uid, "conversation_id": conv, "force": True}, uid))

    # REST features, on local-only and ordinary mail.
    for mid in (w.local_mids[0], w.normal_mids[0]):
        await run.step(f"translate {mid}", client.post(f"/api/v1/messages/{mid}/translate", json={"to": "French"}))
        await run.step(f"reply suggestions {mid}", client.get(f"/api/v1/messages/{mid}/reply-suggestions"))
        await run.step(f"from-message {mid}", client.post("/api/v1/calendar/from-message",
                                                         json={"message_id": mid, "kind": "event"}))
    for conv in (w.local_conv, w.normal_conv):
        await run.step(f"summary {conv}", client.get(f"/api/v1/conversations/{conv}/summary",
                                                     params={"force": "true", "wait": "true"}))
        await run.step(f"draft {conv}", client.post("/api/v1/agent/draft", json={
            "mode": "reply", "conversation_id": conv, "instructions": f"Say yes, mention {L['body']}"}))
        await run.step(f"context {conv}", client.post("/api/v1/context", json={"conversation_id": conv,
                                                                              "query": L["body"]}))
    for body in ({"trigger": "recording", "question": f"Was {L['body']} decided?", "recordings": {}},
                 {"trigger": "message", "question": "Is this about a meeting?", "query": "appeal", "scope": "all"}):
        await run.step(f"order preview {body['trigger']}", client.post("/api/v1/assistant/orders/preview", json=body))
    await run.step("meeting prep", client.post(f"/api/v1/calendar/events/{w.event_uid}/prep/summary", json={}))
    await run.step("skill try", client.post("/api/v1/assistant/skills/test", json={
        "sample": f"Reply about {L['body']}", "instructions": "Be brief."}))
    await run.step("onboarding chat", client.post("/api/v1/onboarding/chat", json={
        "messages": [{"role": "assistant", "content": "What do you do?"}, {"role": "user", "content": "I teach."}]}))
    await run.step("onboarding summary", client.post("/api/v1/onboarding/chat/summary", json={
        "messages": [{"role": "assistant", "content": "What do you do?"}, {"role": "user", "content": "I teach."}]}))
    tok = (await client.post("/api/v1/tokens", json={"name": f"sweep-{uuid.uuid4().hex[:4]}", "scopes": ["read"],
                                                     "local_only": True})).json()["token"]
    async with _token_client(client, tok) as a:
        for path in ("/api/v1/search/semantic", "/api/v1/passages/search", "/api/v1/assistant/memories",
                     "/api/v1/assistant/recall"):
            await run.step(f"token {path}", a.get(path, params={"q": f"Fridays {L['body']} {L['memory']}"}))

    # The assistant chat: one chat per read, each reading local-only content first.
    reads = [("get_conversation", {"conversation_id": w.local_conv}),
             ("get_message_body", {"message_id": w.local_mids[0]}),
             ("search_semantic", {"q": "appeal meeting"}),
             ("find_passages", {"q": "appeal meeting"}),
             ("get_note", {"note": w.local_note}),
             ("get_table", {"table": w.local_table}),
             ("search_events", {"text": "Hearing", "when": "next week"}),
             ("get_recording", {"recording_id": w.recording_id}),
             ("get_transcript", {"recording_id": w.recording_id}),
             ("recall", {"q": "Fridays"}),
             ("meeting_prep", {"uid": w.event_uid}),
             ("search_notes", {"q": "plan"}),
             ("ask_recording", {"recording_id": w.recording_id, "question": "What was decided?"}),
             ("draft_follow_up", {"recording_id": w.recording_id, "mode": "write", "instructions": "thank them"}),
             ("get_summary", {"recording_id": w.recording_id})]
    sessions: list[str] = []
    for tool, args in reads:
        net.script[:] = [tool_turn(tool, args), tool_turn("web_search", {"query": f"{L['subject']} {L['body']}"}, "c2"),
                         tool_turn("create_note", {"title": L["body"], "body": L["subject"]}, "c3"),
                         text_turn(f"Summary of {L['subject']}.")]
        r = await run.step(f"chat {tool}", client.post("/api/v1/agent/chat", json={
            "message": "What is new about the appeal?", "context": {"conversation_id": w.local_conv}}))
        if r is not None:
            sid = next((e.get("session_id") for e in _events(r.text) if e.get("session_id")), None)
            if sid:
                sessions.append(sid)
    net.script.clear()
    for sid in sessions[:2]:
        await run.step("retitle", client.post(f"/api/v1/agent/sessions/{sid}/retitle"))
        async with sessionmaker()() as db:
            from mailr.models import ChatMessage

            last = (await db.execute(select(ChatMessage.id).where(ChatMessage.session_id == sid)
                                     .order_by(ChatMessage.id.desc()).limit(1))).scalar()
        if last:
            await run.step("kb.chat_suggest", _job("kb.chat_suggest", {"user_id": uid, "session_id": sid,
                                                                       "message_id": last}, uid))

    # Reviews, Speakr models and the tools, in a chat that has read local-only content.
    async with sessionmaker()() as db:
        u = await db.get(User, uid)
        ctx = ToolCtx(db=db, p=Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"}), assistant=True,
                      chat_session_id=f"sweep-{uuid.uuid4().hex[:6]}", session_sensitive=True, local_model=local)
        res = await run.step("review_mail", run_tool(ctx, "review_mail", {
            "scope": {"view": "all", "q": "notice"}, "instructions": f"archive {L['subject']}"}))
        for tool, args in (("draft_follow_up", {"recording_id": w.recording_id, "mode": "write",
                                                "instructions": f"mention {L['body']}"}),
                           ("propose_action_items", {"recording_id": w.recording_id}),
                           ("ask_recording", {"recording_id": w.recording_id, "question": L["body"]}),
                           ("search_recordings", {"q": L["body"]})):
            await run.step(f"sensitive {tool}", run_tool(ctx, tool, args))
    run.log.append(f"review_mail -> {getattr(res, 'data', None)}"[:300])
    rid = getattr(res, "data", {}).get("review_id") if res is not None and isinstance(res.data, dict) else None
    if rid:
        await run.step("assistant.review", _job("assistant.review", {"review_id": rid}, uid))
    await _tools_sweep(run, user, w, L["body"], local_model=local)
    # The same tools from an ordinary chat on ordinary data reach the hosted fakes (not vacuous).
    async with sessionmaker()() as db:
        u = await db.get(User, uid)
        ctx = ToolCtx(db=db, p=Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"}), assistant=True,
                      chat_session_id=f"sweep-{uuid.uuid4().hex[:6]}", session_sensitive=False, local_model=False)
        for tool, args in (("web_search", {"query": f"weather {w.normal['body']}"}),
                           ("read_page", {"url": "https://page.sweep.test/result"}),
                           ("search_semantic", {"q": f"appeal {w.normal['body']}"}),
                           ("search_notes", {"q": f"plan {w.normal['note']}"})):
            await run.step(f"normal {tool}", run_tool(ctx, tool, args))


def _speakr_model_leaks(speakr_log: list[tuple[str, str]], canaries: list[str]) -> list[str]:
    """Requests to Speakr's own models (chat, inquire, semantic search, summaries) with a canary."""
    out = []
    for url, body in speakr_log:
        path = url.lower()
        if any(x in path for x in ("/chat", "inquire", "summarize", "mode=semantic", "/search")):
            out += [f"{c} -> Speakr {url}" for c in canaries if c.lower() in (url + body).lower()]
    return out


@pytest.fixture
async def isolated_models():
    """Only the sweep's fakes serve model calls; the endpoints that were on are back on afterwards."""
    async with sessionmaker()() as db:
        on = list((await db.execute(select(ModelEndpoint.id).where(ModelEndpoint.enabled.is_(True)))).scalars())
        await db.execute(update(ModelEndpoint).values(enabled=False))
        await db.commit()
    yield
    from sqlalchemy import delete

    async with sessionmaker()() as db:
        await db.execute(delete(ModelEndpoint).where(ModelEndpoint.name.like("sweep-%")))
        await db.execute(delete(WebSearchProvider).where(WebSearchProvider.name.like("sweep-%")))
        if on:
            await db.execute(update(ModelEndpoint).where(ModelEndpoint.id.in_(on)).values(enabled=True))
        await db.commit()


async def test_canary_sweep(client, user, speakr, monkeypatch, isolated_models):
    import httpx

    from mailr.integrations.speakr import client as sc

    net = FakeNet().install(monkeypatch)
    meter = Meter().install(monkeypatch)
    speakr_log: list[tuple[str, str]] = []
    inner = speakr.handler

    def recording_handler(request: httpx.Request) -> httpx.Response:
        try:
            body = request.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001  a streamed upload: the URL is checked alone
            body = ""
        speakr_log.append((str(request.url), body))
        return inner(request)

    sc.use_transport(httpx.MockTransport(recording_handler))
    from mailr.config import get_settings

    monkeypatch.setattr(get_settings(), "trusted_authserv_ids", AUTHSERV)
    w = await _seed(client, user, speakr)
    local_canaries = list(w.local.values())

    # ---------------- pass 1: hosted services only
    names = await _services(net, "hosted")
    run1 = Run()
    await _exercise(run1, client, user, w, net, names, local=False)
    leaks = [f"{c} -> {r.kind} {r.method} {r.url} (from {r.where})" for c, r in net.leaks(local_canaries,
                                                                                          locality="hosted")]
    leaks += _speakr_model_leaks(speakr_log, local_canaries)
    assert not leaks, "local-only canaries reached hosted services:\n" + "\n".join(sorted(set(leaks))[:60])
    hosted_seen = {c for c, _ in net.leaks(list(w.normal.values()), locality="hosted")}
    assert {w.normal["subject"], w.normal["body"]} <= hosted_seen, (
        f"ordinary canaries never reached the hosted fakes, so the sweep ran nothing: {sorted(hosted_seen)}; "
        f"log: {run1.log[:20]}")

    # ---------------- pass 2: local services
    n_before = len(net.records)
    names = await _services(net, "local")
    run2 = Run()
    await _exercise(run2, client, user, w, net, names, local=True)
    later = net.records[n_before:]
    import os

    if os.environ.get("MAILR_SWEEP_DEBUG"):  # a file with what each pass sent, for working on the sweep
        from pathlib import Path

        dump = json.dumps({"log1": run1.log, "log2": run2.log, "sites": sorted(meter.sites),
                           "features": sorted(meter.features), "speakr": speakr_log, "canaries": w.local,
                           "records": [(r.locality, r.kind, r.method, r.url, r.where, r.body) for r in net.records]},
                          indent=1)
        await asyncio.to_thread(Path(os.environ["MAILR_SWEEP_DEBUG"]).write_text, dump)
    leaks = [f"{c} -> {r.kind} {r.method} {r.url} (from {r.where})"
             for c, r in net.leaks(local_canaries, locality="hosted") if r in later]
    leaks += _speakr_model_leaks(speakr_log, local_canaries)
    assert not leaks, "pass 2: local-only canaries reached hosted services:\n" + "\n".join(sorted(set(leaks))[:60])
    reached = {k for k, c in w.local.items() if any(c.lower() in r.text() for r in later if r.locality == "local")}
    want = set(w.local) - {"scratch"}  # every kind of local-only data reached a local service
    assert want <= reached, (f"local-only canaries that never reached a local service: {sorted(want - reached)}; "
                             f"log: {run2.log[:30]}")

    # ---------------- coverage
    sites = static_sites()
    missing = sorted(s for s in sites if s not in meter.sites and s not in SITE_EXEMPT)
    stale = sorted(s for s in SITE_EXEMPT if s not in sites)
    feats = static_features()
    fmissing = sorted(f for f in feats if f not in meter.features and f not in FEATURE_EXEMPT)
    fstale = sorted(f for f in FEATURE_EXEMPT if f not in feats)
    assert not stale and not fstale, f"exemptions for code that is gone: {stale + fstale}"
    assert not missing and not fmissing, (
        "model call sites or features the sweep never ran (exercise them in _exercise, or exempt them "
        f"with a reason):\nsites: {missing}\nfeatures: {fmissing}\nlog: {(run1.log + run2.log)[:40]}")
