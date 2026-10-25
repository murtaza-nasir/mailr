# ACT-SURFACES: api assistant
"""Transcribe in Speakr (mail.to_speakr): the multipart fields Speakr reads, notes
that carry the mailr link, the conversation linked at once, 413 explained, non-media refused, and the
approval path for agents (speakr.upload asks; an errand's upload is marked for the chain guard).
Needs the dev Stalwart."""

from datetime import UTC, datetime

from helpers.speakr_tools import _ctx
from helpers.speakr_upload import AUDIO, _message, _with_attachments  # noqa: F401
from speakr_fake import connect, speakr  # noqa: F401
from sqlalchemy import select

from mailr.agent.tools import ToolError, run_tool
from mailr.core import permissions
from mailr.db import sessionmaker
from mailr.models import Job, PendingAction, RecordingLink, SpeakrRecording, User


async def test_transcribe_attachment_multipart_notes_link_and_413(client, user, speakr):
    speakr.add(1)
    conn = await connect(client, speakr)
    mid, cid, parts = await _message(client, user)

    r = await client.post("/api/v1/speakr/upload", json={"message_id": mid, "part": parts["interview.mp3"]})
    assert r.status_code == 200, r.text
    out = r.json()
    up = speakr.uploads[-1]
    assert up["file"]["filename"] == "interview.mp3" and up["file"]["type"] == "audio/mpeg"
    assert up["file"]["content"] == AUDIO
    notes = up["fields"]["notes"]
    assert notes.startswith("From mail: Interview recording (") and f"/mail/all/{cid}" in notes
    sent = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    assert up["fields"]["file_last_modified"] == str(int(sent.timestamp() * 1000))  # the message date, in ms
    assert out["recording_id"] == up["id"] and out["status"] == "pending" and out["notes"] == notes
    async with sessionmaker()() as db:
        row = (await db.execute(select(SpeakrRecording).where(SpeakrRecording.recording_id == up["id"],
                                                         SpeakrRecording.connector_id == out["connector_id"]))).scalar_one()
        assert row.status == "pending" and row.title == "interview"
        lk = (await db.execute(select(RecordingLink).where(RecordingLink.recording_id == up["id"],
                                                       RecordingLink.connector_id == out["connector_id"]))).scalar_one()
        assert (lk.kind, lk.target, lk.state, lk.source) == ("conversation", str(cid), "confirmed", "upload")
        assert (await db.execute(select(Job).where(Job.kind == "speakr.sync"))).scalars().first() is not None
    # The card shows it as processing straight away.
    linked = (await client.get("/api/v1/speakr/linked-conversations")).json()["conversation_ids"]
    assert cid in linked

    # A file named .m4a with a generic type is audio too; a PDF is refused.
    r = await client.post("/api/v1/speakr/upload", json={"message_id": mid, "part": parts["voice memo.m4a"]})
    assert r.status_code == 200 and speakr.uploads[-1]["file"]["type"] == "audio/mp4"
    r = await client.post("/api/v1/speakr/upload", json={"message_id": mid, "part": parts["notes.pdf"]})
    assert r.status_code == 400 and "audio and video" in r.json()["detail"]

    speakr.max_upload = 100
    r = await client.post("/api/v1/speakr/upload", json={"message_id": mid, "part": parts["interview.mp3"]})
    assert r.status_code == 413 and "too large" in r.json()["detail"] and "Speakr server" in r.json()["detail"]
    assert (await client.post("/api/v1/speakr/upload", json={"message_id": mid, "part": 99})).status_code == 404
    assert conn["id"] == out["connector_id"]


async def test_transcribe_attachment_asks_agents_and_marks_errands(client, user, speakr):
    speakr.add(1)
    await connect(client, speakr)
    mid, cid, parts = await _message(client, user)
    async with sessionmaker()() as db:
        res = await run_tool(await _ctx(db, user.id), "send_to_speakr",
                             {"message_id": mid, "attachment": parts["interview.mp3"]})
        assert res.data["status"] == "pending_approval" and res.pending["action"] == "speakr.upload"
        pid = res.data["approval_id"]
        ctx = await _ctx(db, user.id)
        ctx.chat_context = {"errand": 12}
        res = await run_tool(ctx, "send_to_speakr", {"message_id": mid, "attachment": parts["voice memo.m4a"]})
        pid2 = res.data["approval_id"]
        try:
            await run_tool(ctx, "share_recording", {"recording_id": 1})
            raise AssertionError("an errand shared a recording")
        except ToolError as e:
            assert "errand cannot create share links" in str(e)
    assert speakr.uploads == []  # nothing before approval
    for p_id in (pid, pid2):
        async with sessionmaker()() as db:
            await permissions.decide(db, await db.get(User, user.id), p_id, "approve", "once")
            await db.commit()
        await permissions.execute_job({"pending_id": p_id}, None)
    async with sessionmaker()() as db:
        assert (await db.get(PendingAction, pid)).status == "executed"
        links = (await db.execute(select(RecordingLink).where(RecordingLink.kind == "conversation",
                                                              RecordingLink.target == str(cid))
                                  .order_by(RecordingLink.id))).scalars().all()
        assert [lk.source for lk in links] == ["upload", "order"]
    assert len(speakr.uploads) == 2

    # A token needs the recordings scope and is asked.
    # (local-only: a Speakr account is local-only by default, and a token that cannot see the
    # account is refused before any approval)
    tok = (await client.post("/api/v1/tokens", json={"name": "uploader", "scopes": ["read", "recordings"],
                                                     "local_only": True})).json()
    from httpx import ASGITransport, AsyncClient

    from mailr.main import create_app

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test",
                           headers={"Authorization": f"Bearer {tok['token']}"}) as t:
        r = await t.post("/api/v1/speakr/upload", json={"message_id": mid, "part": parts["interview.mp3"]})
        assert r.status_code == 202 and r.json()["approval"]["action"] == "speakr.upload"
    assert len(speakr.uploads) == 2
