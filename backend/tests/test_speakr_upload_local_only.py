# ACT-SURFACES: api assistant
"""Transcribe in Speakr from a local-only account: the attachment, the subject in the notes and the
conversation reference never go to a Speakr account that is not local-only (the route answers 403,
the assistant's tool refuses before any approval, and an approved action refuses too). A local-only
Speakr account takes the upload, and the assistant's result is sensitive. Needs the dev Stalwart."""

import pytest
from helpers.speakr_tools import _ctx
from helpers.speakr_upload import _message
from speakr_fake import connect, speakr  # noqa: F401

from mailr.agent.tools import ToolError, run_tool
from mailr.db import sessionmaker
from mailr.integrations.speakr import writes
from mailr.models import Message


async def _mark_local(mid):
    async with sessionmaker()() as db:
        (await db.get(Message, mid)).local_only = True
        await db.commit()


async def test_local_only_message_never_reaches_a_normal_speakr(client, user, speakr):
    speakr.add(1)
    conn = await connect(client, speakr, data_class="normal")
    mid, _cid, parts = await _message(client, user)
    await _mark_local(mid)

    r = await client.post("/api/v1/speakr/upload", json={"message_id": mid, "part": parts["interview.mp3"],
                                                         "connector_id": conn["id"]})
    assert r.status_code == 403 and "local-only" in r.json()["detail"]
    async with sessionmaker()() as db:
        with pytest.raises(ToolError, match="local-only"):
            await run_tool(await _ctx(db, user.id), "send_to_speakr",
                           {"message_id": mid, "attachment": parts["interview.mp3"], "connector_id": conn["id"]})
        # The executor of an approval made before the account changed refuses as well.
        from mailr.core.auth import ALL_SCOPES, Principal
        from mailr.models import User

        p = Principal(user=await db.get(User, user.id), scopes=set(ALL_SCOPES))
        with pytest.raises(writes.WriteOff):
            await writes.upload_attachment(db, p, mid, parts["interview.mp3"], conn["id"])
    assert speakr.uploads == []


async def test_local_only_message_goes_to_a_local_only_speakr_and_marks_the_result(client, user, speakr):
    speakr.add(1)
    conn = await connect(client, speakr)
    assert conn["data_class"] == "local_only"
    mid, _cid, parts = await _message(client, user)
    await _mark_local(mid)
    r = await client.post("/api/v1/speakr/upload", json={"message_id": mid, "part": parts["interview.mp3"]})
    assert r.status_code == 200 and r.json()["local_only"] is True
    assert len(speakr.uploads) == 1
