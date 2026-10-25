"""What the assistant writes in a chat that has read local-only content (ToolCtx.session_sensitive)
keeps the local-only promise. Items with a local-only mark of their own are made local-only:
notes, lists, notes from templates and tables (memory is sensitive, test_assistant_knowledge.py).
Items without one ask first, with the reason, even when a rule allows them: tasks, skills, briefs,
and drafts that would not be local-only mail themselves."""
import uuid

from helpers.agent import _ingest, _setup_identity
from helpers.agent_calendar import _connect_direct
from sqlalchemy import select

from mailr.agent.agentwrite import LOCAL_COPY
from mailr.agent.tools import ToolCtx, run_tool
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.models import AssistantRun, Identity, PendingAction, PermissionRule, User
from mailr.notes.models import Note
from mailr.tables.models import DataTable


async def _ctx(db, user_id, sensitive, **kw):
    u = await db.get(User, user_id)
    return ToolCtx(db=db, p=Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"}), assistant=True,
                   chat_session_id=f"s-{uuid.uuid4().hex[:6]}", session_sensitive=sensitive, **kw)


async def _allow(user_id, action):
    async with sessionmaker()() as db:
        db.add(PermissionRule(user_id=user_id, subject="assistant", action=action, effect="allow", constraints={}))
        await db.commit()


async def _reason(db, res):
    assert res.pending is not None, res.data
    pa = await db.get(PendingAction, res.data["approval_id"])
    return (pa.preview or {}).get("reason", "")


async def test_notes_and_lists_from_a_local_only_chat_are_local_only(client, user):
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, sensitive=True)
        made = await run_tool(ctx, "create_note", {"title": "Appeal", "body": "Student 4411 asked for a review"})
        lst = await run_tool(ctx, "create_note", {"title": "Follow-ups", "lines": "Call the dean"})
        plain = await run_tool(await _ctx(db, user.id, sensitive=False), "create_note", {"title": "Groceries"})
        rows = {n.pid: n.local_only for n in (await db.execute(select(Note).where(Note.pid.in_(
            [made.data["note"]["id"], lst.data["note"]["id"], plain.data["note"]["id"]])))).scalars()}
    assert rows[made.data["note"]["id"]] is True and rows[lst.data["note"]["id"]] is True
    assert rows[plain.data["note"]["id"]] is False


async def test_note_from_a_template_in_a_local_only_chat_is_local_only(client, user):
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, sensitive=True)
        tpl = await run_tool(ctx, "list_notes", {"templates": True})
        first = next(t for t in tpl.data["templates"] if t["kind"] == "checklist")
        made = await run_tool(ctx, "create_note", {"template": first["id"], "title": "Packing"})
        assert made.pending is None, made.data
        row = (await db.execute(select(Note).where(Note.pid == made.data["results"][0]["id"]))).scalar_one()
        assert row.local_only is True


async def test_table_from_a_local_only_chat_is_local_only(client, user):
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, sensitive=True)
        made = await run_tool(ctx, "table_schema", {"action": "create", "name": "Appeals",
                                                    "columns": [{"name": "Student", "type": "text"}]})
        assert made.data["ok"], made.data
        row = (await db.execute(select(DataTable).where(DataTable.pid == made.data["table"]))).scalar_one()
        assert row.local_only is True
        other = await run_tool(await _ctx(db, user.id, sensitive=False), "table_schema",
                               {"action": "create", "name": "Workouts", "columns": [{"name": "Day", "type": "text"}]})
        row = (await db.execute(select(DataTable).where(DataTable.pid == other.data["table"]))).scalar_one()
        assert row.local_only is False


async def test_task_from_a_local_only_chat_asks_even_when_allowed(client, user):
    await _connect_direct(user.id)
    await _allow(user.id, "tasks.create")
    async with sessionmaker()() as db:
        res = await run_tool(await _ctx(db, user.id, sensitive=True), "create_task",
                             {"summary": "Review the appeal of student 4411"})
        assert await _reason(db, res) == LOCAL_COPY
        ok = await run_tool(await _ctx(db, user.id, sensitive=False), "create_task", {"summary": "Buy stamps"})
        assert ok.pending is None, ok.data


async def test_skill_from_a_local_only_chat_asks_even_when_allowed(client, user):
    await _allow(user.id, "skills.write")
    async with sessionmaker()() as db:
        args = {"name": "appeal-reply", "description": "Answer grade appeals",
                "instructions": "- Mention the appeal of student 4411."}
        res = await run_tool(await _ctx(db, user.id, sensitive=True), "create_skill", args)
        assert await _reason(db, res) == LOCAL_COPY
        ok = await run_tool(await _ctx(db, user.id, sensitive=False), "create_skill", {**args, "name": "short-reply"})
        assert ok.pending is None, ok.data


async def test_brief_rewrite_from_a_local_only_chat_asks_unless_its_mail_is_local_only(client, user, raw):
    lid, me_l = await _setup_identity(user, data_class="local_only")
    _, me = await _setup_identity(user)
    mid, cid = await _ingest(user.id, raw("Weekly report", frm="Team <team@work.example>", to=me))
    lmid, lcid = await _ingest(user.id, raw("Appeal", frm="Student <s@uni.example>", to=me_l), connector_id=lid)
    async with sessionmaker()() as db:
        for m, c in ((mid, cid), (lmid, lcid)):
            db.add(AssistantRun(user_id=user.id, trigger="manual", slot=f"b{uuid.uuid4().hex[:6]}", message_id=m,
                                conversation_id=c, status="done", output="A short brief.", delivered=["brief"]))
        await db.commit()
    async with sessionmaker()() as db:
        res = await run_tool(await _ctx(db, user.id, sensitive=True), "update_brief",
                             {"conversation_id": cid, "text": "Student 4411 failed."})
        assert await _reason(db, res) == LOCAL_COPY
        ok = await run_tool(await _ctx(db, user.id, sensitive=True), "update_brief",
                            {"conversation_id": lcid, "text": "Student 4411 asks for a review."})
        assert ok.pending is None, ok.data


async def test_draft_from_a_local_only_chat_asks_unless_it_stays_local(client, user, raw):
    lid, me_l = await _setup_identity(user, data_class="local_only")
    _, me = await _setup_identity(user)
    lmid, _ = await _ingest(user.id, raw("Appeal", frm="Student <s@uni.example>", to=me_l, body="Please review"),
                            connector_id=lid)
    async with sessionmaker()() as db:
        ident = (await db.execute(select(Identity.id).where(Identity.user_id == user.id, Identity.email == me))).scalar()
        res = await run_tool(await _ctx(db, user.id, sensitive=True), "create_draft",
                             {"to": ["omar@shop.example"], "subject": "Note", "body": "Student 4411 failed.",
                              "identity_id": ident})
        assert await _reason(db, res) == LOCAL_COPY
        # A reply to the local-only mail is saved as local-only mail.
        ok = await run_tool(await _ctx(db, user.id, sensitive=True), "create_draft",
                            {"reply_to_message_id": lmid, "mode": "reply", "body": "I will review it."})
        assert ok.pending is None, ok.data


async def test_event_from_a_local_only_chat_asks_unless_its_calendar_is_local_only(client, user):
    """Calendar writes (create, update, delete, rsvp) from such a chat ask even when a rule allows
    them; a calendar of a local-only account keeps the content local and does not ask. Without a
    rule, an assistant's calendar.create asks anyway (the class default)."""
    from mailr.calendar.sync import sync_connector
    from mailr.models import Connector

    await _connect_direct(user.id)
    async with sessionmaker()() as db:
        conn = (await db.execute(select(Connector).where(Connector.user_id == user.id,
                                                         Connector.kind == "caldav"))).scalars().first()
    await sync_connector(conn.id)
    await _allow(user.id, "calendar.create")
    await _allow(user.id, "calendar.update")
    args = {"summary": "Appeal hearing for student 4411", "start": "2031-03-04T10:00:00Z",
            "end": "2031-03-04T11:00:00Z"}
    async with sessionmaker()() as db:
        res = await run_tool(await _ctx(db, user.id, sensitive=True), "create_event", args)
        assert await _reason(db, res) == LOCAL_COPY
        ok = await run_tool(await _ctx(db, user.id, sensitive=False), "create_event",
                            {**args, "summary": "Dentist"})
        assert ok.pending is None, ok.data
        uid = ok.data.get("uid") or ok.data["event"]["uid"]
        res = await run_tool(await _ctx(db, user.id, sensitive=True), "update_event",
                             {"uid": uid, "summary": "Dentist (student 4411)"})
        assert await _reason(db, res) == LOCAL_COPY
    async with sessionmaker()() as db:
        (await db.get(Connector, conn.id)).data_class = "local_only"
        await db.commit()
    async with sessionmaker()() as db:
        ok = await run_tool(await _ctx(db, user.id, sensitive=True, local_model=True), "create_event",
                            {**args, "summary": "Hearing, room 2"})
        assert ok.pending is None, ok.data
