# ACT-SURFACES: assistant
"""Speakr write-backs from a chat that has read local-only content (outbound data policy, gap 6):
an edit of a recording on a Speakr account that is not local-only asks first, with the local-copy
reason, even when a rule allows speakr.write; on a local-only Speakr account the rule holds."""

from helpers.speakr_tools import _ctx
from helpers.speakr_writeback import _switch
from speakr_fake import connect, speakr  # noqa: F401

from mailr.agent.agentwrite import LOCAL_COPY
from mailr.agent.tools import run_tool
from mailr.db import sessionmaker
from mailr.models import PendingAction, PermissionRule


async def _edit(user_id, sensitive):
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user_id)
        ctx.session_sensitive = sensitive
        res = await run_tool(ctx, "update_recording", {"recording_id": 1, "append_notes": "Student 4411 appeal"})
        reason = None
        if res.pending is not None:
            reason = (await db.get(PendingAction, res.data["approval_id"])).preview.get("reason")
        return res, reason


async def test_speakr_edit_from_a_local_only_chat_asks_on_a_normal_account(client, user, speakr):
    speakr.add(1, "Advising: Dana")
    conn = await connect(client, speakr, data_class="normal")
    await _switch(client, conn["id"], write_back=True)
    async with sessionmaker()() as db:
        db.add(PermissionRule(user_id=user.id, subject="assistant", action="speakr.write", effect="allow",
                              constraints={}))
        await db.commit()
    res, reason = await _edit(user.id, sensitive=True)
    assert res.pending is not None and reason == LOCAL_COPY
    res, _ = await _edit(user.id, sensitive=False)
    assert res.pending is None and res.data["notes_appended"] is True


async def test_speakr_edit_from_a_local_only_chat_follows_the_rule_on_a_local_only_account(client, user, speakr):
    speakr.add(1, "Advising: Dana")
    conn = await connect(client, speakr)
    assert conn["data_class"] == "local_only"
    await _switch(client, conn["id"], write_back=True)
    async with sessionmaker()() as db:
        db.add(PermissionRule(user_id=user.id, subject="assistant", action="speakr.write", effect="allow",
                              constraints={}))
        await db.commit()
    res, _ = await _edit(user.id, sensitive=True)
    assert res.pending is None and res.sensitive
