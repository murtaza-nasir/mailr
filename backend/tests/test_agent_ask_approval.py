# ACT-SURFACES: assistant
"""agent.ask_about on an approval card: "Ask" sends `focused_approval` {id} at the top of the chat's
`context`. The session keeps it; the prompt gets the card, who asked, the status, why it asks and
what each choice does. Only the owner's signed-in session sees it, and asking never decides it."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from helpers.agent import (
    _drop_endpoint,
    _endpoint,
    _events,
    _ingest,
    _setup_identity,
    _text_turn,
    _tool_turn,
)

from mailr.agent import loop, screen
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.core.users import create_user
from mailr.db import sessionmaker
from mailr.models import ApiToken, Connector, PendingAction, User


async def _pending(db, user_id, **kw):
    pa = PendingAction(user_id=user_id, subject=kw.pop("subject", "assistant"), actor=kw.pop("actor", "Ambrose"),
                       action=kw.pop("action", "mail.send"), target="",
                       payload=kw.pop("payload", {}), ctx=kw.pop("ctx", {"recipients": ["dana@example.org"]}),
                       preview=kw.pop("preview", {"title": "Send email", "lines": ["To: dana@example.org"]}),
                       status=kw.pop("status", "pending"), expires_at=datetime.now(UTC) + timedelta(hours=2), **kw)
    db.add(pa)
    await db.flush()
    return pa


def test_focused_approval_is_kept_and_validated():
    keep, ui = loop.split_context({"focused_approval": {"id": 41}})
    assert keep == {"focused_approval": 41} and ui == {}
    assert loop.split_context({"focused_approval": 7})[0] == {"focused_approval": 7}
    for bad in ({"id": "x"}, {"id": -3}, {"id": True}, {}, "abc", None, 0):
        assert loop.split_context({"focused_approval": bad})[0] == {}
    assert screen.with_focus({}, None, None, None, 41) == {"focused_approval": 41}
    # Inside `ui` too, and the turn's own focus wins over the session's.
    short = screen.sanitize({"focused_approval": {"id": 9}})
    assert screen.with_focus(short, None, None, None, 41)["focused_approval"] == 9


async def test_owner_sees_the_card_and_the_choices(user):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        p = Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"})
        pa = await _pending(db, user.id, preview={"title": "Send email", "lines": ["To: dana@example.org"],
                                                  "reason": "hard rule: first message to a new address"})
        lines = await screen.describe(db, p, {"focused_approval": pa.id})
        assert "The user asked about one approval" in lines and f"approval {pa.id}" in lines
        assert "the built-in assistant" in lines and "Waiting for approval" in lines
        assert "dana@example.org" in lines
        assert "first message to a new address" in lines
        assert "You cannot approve or deny it" in lines
        assert "domains: example.org" in lines  # what Always allow would narrow to

        # Without a hard rule: the default asks.
        pa2 = await _pending(db, user.id, action="mail.trash", ctx={},
                             preview={"title": "Move 2 messages to Trash", "lines": []},
                             payload={"action": "trash", "args": {}, "message_ids": []})
        lines = await screen.describe(db, p, {"focused_approval": pa2.id})
        assert "Mailr asks by default before mail.trash" in lines

        # Decided: nothing to choose any more.
        pa3 = await _pending(db, user.id, status="denied")
        lines = await screen.describe(db, p, {"focused_approval": pa3.id})
        assert "Denied" in lines and "already decided" in lines and "Allow once" not in lines
        await db.rollback()


async def test_tokens_and_other_users_see_nothing(user):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        tok = ApiToken(user_id=user.id, name="Claude Code", prefix="mlr_test",
                       token_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), scopes=["read"])
        db.add(tok)
        await db.flush()
        pa = await _pending(db, user.id, subject=f"token:{tok.id}", actor="Claude Code", token_id=tok.id)
        owner = Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"})
        assert "Claude Code (an outside agent" in await screen.describe(db, owner, {"focused_approval": pa.id})
        # A token's chat: not even its own request.
        as_token = Principal(user=u, token=tok, scopes={"read"})
        assert await screen.describe(db, as_token, {"focused_approval": pa.id}) == ""
        assert await screen.approval_touches_local_only(db, as_token, pa.id) is False
        # Another user.
        other = await create_user(db, f"o-{uuid.uuid4().hex[:8]}@example.com", "Other", password="test-password-123")
        stranger = Principal(user=other, scopes=set(ALL_SCOPES) - {"admin"})
        assert await screen.describe(db, stranger, {"focused_approval": pa.id}) == ""
        assert await screen.describe(db, owner, {"focused_approval": 10**12}) == ""
        await db.rollback()


async def test_local_only_connector_marks_it_sensitive(user):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        p = Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"})
        conn = Connector(user_id=user.id, kind="speakr", name="local speakr", mode="mirror", data_class="local_only",
                         config={"base_url": "https://speakr.invalid"}, state={}, enabled=False,
                         status="paused", status_detail="Paused")
        db.add(conn)
        await db.flush()
        pa = await _pending(db, user.id, action="speakr.share", payload={"connector_id": conn.id, "recording_id": 3})
        assert await screen.approval_touches_local_only(db, p, pa.id) is True
        conn.data_class = "normal"
        await db.flush()
        assert await screen.approval_touches_local_only(db, p, pa.id) is False
        await db.rollback()


async def test_ask_about_an_approval_in_a_new_chat(client, user, raw, fake_llm):
    _, me = await _setup_identity(user)
    mid, conv = await _ingest(user.id, raw("Old promo", frm="Shop <deals@shop.example>", to=me, body="Sale!"))
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [_tool_turn("modify_conversations", {"conversation_ids": [conv], "action": "trash"}),
                           _text_turn("Waiting for your approval.")]
        evs = _events((await client.post("/api/v1/agent/chat", json={"message": "Trash the old promo"})).text)
        aid = next(e for e in evs if e["type"] == "approval_required")["approval_id"]

        fake_llm.script = [_text_turn("It asks because moving mail to Trash asks by default.")]
        r = await client.post("/api/v1/agent/chat", json={"message": "Why does this need approval?",
                                                          "context": {"focused_approval": {"id": aid}}})
        evs = _events(r.text)
        assert evs[-1]["type"] == "done", evs
        system = fake_llm.requests[-1]["body"]["messages"][0]["content"]
        assert f"approval {aid}" in system and "Move 1 message to Trash" in system
        assert "You cannot approve or deny it" in system
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        # The chat keeps it for the next turn.
        fake_llm.script = [_text_turn("Choose Deny on the card.")]
        evs = _events((await client.post("/api/v1/agent/chat", json={"message": "and if I deny it?",
                                                                      "session_id": sid})).text)
        assert f"approval {aid}" in fake_llm.requests[-1]["body"]["messages"][0]["content"]
        # Asking never decides it.
        async with sessionmaker()() as db:
            assert (await db.get(PendingAction, aid)).status == "pending"
    finally:
        await _drop_endpoint(ep)


async def test_every_card_shape_that_shows_local_only_mail_is_marked(user, raw):
    """Mail named anywhere in a card counts: a review plan's nested conversations, a filter's match check
    (its newest message), a card's declared `mail_refs`, a sensitive payload and a local-only note."""
    from mailr.agent.config_tools import match_check
    from mailr.agent.tools import ToolCtx
    from mailr.notes.models import Note

    conn = await _local_conn(user.id)
    _, cid = await _ingest(user.id, raw("Grades for the spring cohort", body="x"), connector_id=conn)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        p = Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"})
        shapes = [
            {"payload": {"op": "review", "review_id": 1, "plan": [{"action": "trash", "conversation_ids": [cid]}]},
             "preview": {"title": "Review", "lines": ["Move to Trash: 1 (Dana Smith 1)"]}},
            {"payload": {"op": "create", "rule": {"query": "subject:grades"}},
             "preview": {"title": "Filter", "match": await match_check(ToolCtx(db=db, p=p), "subject:grades")}},
            {"payload": {"op": "brief", "run_id": 1}, "preview": {"title": "Brief", "mail_refs": {"conversation_ids": [cid]}}},
            {"payload": {"op": "remember", "sensitive": True}},
        ]
        for shape in shapes:
            pa = await _pending(db, user.id, action="rules.write", ctx={}, **shape)
            assert await screen.approval_touches_local_only(db, p, pa.id) is True, shape
        assert shapes[1]["preview"]["match"]["newest_subject"].startswith("Grades")
        quiet = await _pending(db, user.id, action="rules.write", ctx={}, payload={"op": "review", "plan": []})
        assert await screen.approval_touches_local_only(db, p, quiet.id) is False
        note = Note(owner_user_id=user.id, pid=f"n{uuid.uuid4().hex[:10]}", title="Private", local_only=True)
        db.add(note)
        await db.flush()
        pa = await _pending(db, user.id, action="notes.edit", ctx={"notes": [note.pid]}, payload={"ops": []})
        assert await screen.approval_touches_local_only(db, p, pa.id) is True
        await db.rollback()


async def _local_conn(user_id):
    async with sessionmaker()() as db:
        c = Connector(user_id=user_id, kind="imap", name=f"uni-{uuid.uuid4().hex[:4]}", mode="mirror",
                      data_class="local_only", config={}, state={}, enabled=False)
        db.add(c)
        await db.commit()
        return c.id


# Every place that creates an approval card, by file and function, and how the card names the mail its
# text comes from (core/visibility.py MAIL_REF_KEYS and MAIL_REFS), so push notifications and "Ask"
# know when it is local-only. "refs": its payload or preview names messages, conversations, drafts, an
# outbox row, a connector, a note or table, or says it is sensitive. Anything else says why it shows no
# mail text. A new call site fails until it is listed here.
APPROVAL_SITES = {
    "agent/calendar_common.py:_run_gated": "events and tasks: calendar text, the connector in ctx",
    "agent/calendar_common.py:_run_event": "events: calendar text, the connector in ctx",
    "agent/cleanup_tools.py:unsubscribe": "refs",
    "agent/cleanup_tools.py:propose_cleanup": "refs",
    "agent/config_tools.py:_run": "settings, labels, layouts and signatures: the user's own configuration",
    "agent/config_tools.py:_label_ids": "a label name the agent chose",
    "agent/config_tools.py:manage_filter": "refs",
    "agent/draft_tools.py:_draft_gate": "refs",
    "agent/organise_tools.py:_action": "refs",
    "agent/organise_tools.py:_bulk": "refs",
    "agent/organise_tools.py:apply_label": "a label name the agent chose",
    "agent/review.py:finish": "refs",
    "agent/screener_tools.py:screener_decide": "a sender address; local-only accounts never reach the Screener",
    "agent/speakr_tools.py:link_recording": "refs",
    "agent/speakr_tools.py:draft_follow_up": "refs",
    "agent/speakr_tools.py:send_to_speakr": "refs",
    "agent/speakr_tools.py:update_recording": "refs",
    "agent/speakr_tools.py:share_recording": "refs",
    "agent/table_tools.py:_run": "refs",
    "agent/tools.py:gate": "the shared gate: its callers are listed here",
    "agent/web_tools.py:_gate": "a web query or address the agent wrote",
    "api/mail_write.py:_run_action": "refs",
    "api/mail_write.py:unsubscribe": "refs",
    "api/mail_write.py:_label_gate": "a label name the caller chose",
    "api/org_routes.py:_rule_request": "a filter the caller wrote",
    "api/org_routes.py:_block_gate": "an address the caller gave",
    "api/screener_routes.py:decide": "a sender address; local-only accounts never reach the Screener",
    "assistant/forwards.py:run": "refs",
    "assistant/orders.py:add_to_calendar": "refs",
    "assistant/tools.py:manage_order": "refs",
    "assistant/tools.py:update_brief": "refs",
    "calendar/forwarded.py:handle": "refs",
    "calendar/routes.py:_gated": "events: calendar text, the connector in ctx",
    "calendar/routes.py:_gated_event": "events: calendar text, the connector in ctx",
    "calendar/routes.py:invite_reply": "refs",
    "calendar/routes_tasks.py:gated": "tasks: calendar text, the connector in ctx",
    "calendar/phonecal.py:_gate": "a phone calendar's name and busy times only: no mail",
    "contacts/routes.py:_run": "a contact group the caller changes",
    "core/sendgate.py:submit": "refs",
    "integrations/speakr/routes.py:_gate_link": "refs",
    "integrations/speakr/routes.py:_gate": "the Speakr routes' shared gate: each caller passes the connector or message",
    "knowledge/ops.py:gated": "refs",
    "notes/gate.py:decide": "refs",
}
_REF_WORDS = ("message_id", "conversation_id", "draft_id", "outbox_id", "connector_id", "mail_refs", "sensitive",
              "match_check", "\"notes\"", "ctx_values", "cvals", "plan")


def test_every_approval_site_says_how_its_card_names_its_mail():
    import ast
    import pathlib

    import mailr

    root = pathlib.Path(mailr.__file__).parent
    found: dict[str, str] = {}
    for f in sorted(root.rglob("*.py")):
        tree = ast.parse(f.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and ast.unparse(n.func) in ("gate", "permissions.check") \
                        and any(k.arg == "preview" for k in n.keywords):
                    found[f"{f.relative_to(root)}:{fn.name}"] = ast.get_source_segment(f.read_text(), fn) or ""
    missing = sorted(set(found) - set(APPROVAL_SITES))
    assert not missing, f"new approval sites: say how each card names its mail (APPROVAL_SITES): {missing}"
    assert not sorted(set(APPROVAL_SITES) - set(found)), sorted(set(APPROVAL_SITES) - set(found))
    for site, how in APPROVAL_SITES.items():
        if how == "refs":
            assert any(w in found[site] for w in _REF_WORDS), f"{site} names no mail"
