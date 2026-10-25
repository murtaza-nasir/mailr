"""A note made from local-only mail ("Make a note from this email", or the assistant's note with a
conversation_id) is local-only, as a note from a local-only recording is; a note from ordinary mail
is not. The assistant's note tools then mark what they read from it sensitive."""
import uuid

import pytest
from helpers.agent import _drop_endpoint, _endpoint, _ingest, _setup_identity
from sqlalchemy import select

from mailr.agent.loop import result_is_sensitive
from mailr.agent.tools import ToolCtx, ToolError, run_tool
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.models import User
from mailr.notes.models import Note


async def _local_conv(user, raw):
    lid, me_l = await _setup_identity(user, data_class="local_only")
    _, conv = await _ingest(user.id, raw("Grade appeal", frm="Student <s@uni.example>", to=me_l,
                                         body="Please review my grade"), connector_id=lid)
    return conv


async def test_note_from_local_only_email_is_local_only(client, user, raw):
    conv = await _local_conv(user, raw)
    r = await client.post("/api/v1/notes/from-email", json={"conversation_id": conv, "selection": "Please review my grade"})
    assert r.status_code == 200, r.text
    note = r.json()["note"]
    assert note["local_only"] is True
    r = await client.post("/api/v1/notes/from-email", json={"conversation_id": conv, "kind": "checklist",
                                                            "selection": "Review the grade\nReply"})
    assert r.status_code == 200 and r.json()["note"]["local_only"] is True, r.text
    ep = await _endpoint(user.id, "hosted")
    try:
        async with sessionmaker()() as db:
            u = await db.get(User, user.id)
            ctx = ToolCtx(db=db, p=Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"}), assistant=True,
                          chat_session_id=f"s-{uuid.uuid4().hex[:6]}")
            # A hosted chat does not see it; a local one reads it as sensitive.
            with pytest.raises(ToolError, match="hidden from this model"):
                await run_tool(ctx, "get_note", {"note": note["id"]})
            ctx.local_model = True
            res = await run_tool(ctx, "get_note", {"note": note["id"]})
            assert await result_is_sensitive(db, user.id, res) is True
    finally:
        await _drop_endpoint(ep)


async def test_note_from_ordinary_email_is_not_local_only(client, user, raw):
    _, me = await _setup_identity(user)
    _, conv = await _ingest(user.id, raw("Lunch on Friday", frm="Omar <omar@shop.example>", to=me, body="Noon?"))
    r = await client.post("/api/v1/notes/from-email", json={"conversation_id": conv, "selection": "Noon?"})
    assert r.status_code == 200, r.text
    assert r.json()["note"]["local_only"] is False


async def test_assistant_note_linked_to_local_only_email_is_local_only(client, user, raw):
    conv = await _local_conv(user, raw)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        ctx = ToolCtx(db=db, p=Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"}), assistant=True,
                      chat_session_id=f"s-{uuid.uuid4().hex[:6]}", local_model=True)
        made = await run_tool(ctx, "create_note", {"title": "Appeal", "kind": "checklist",
                                                   "lines": "Check the grade", "conversation_id": conv})
        assert made.pending is None, made.data
        row = (await db.execute(select(Note).where(Note.pid == made.data["note"]["id"]))).scalar_one()
        assert row.local_only is True
