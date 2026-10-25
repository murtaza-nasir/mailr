# ACT-SURFACES: api
"""A new recording from mailr (recording.record; POST /api/v1/speakr/recordings): the folders and tags
for the form; the file goes to Speakr with title, meeting date, notes, folder and tags on the upload
and the participants by PATCH (an older Speakr gets everything it ignored by PATCH and the tags
route); unsupported_fields names what Speakr refused; a pending mirror row; a confirmed event link
kept in mailr; mailr's own size limit and Speakr's 413; tokens go ahead only when allowed outright."""

from datetime import UTC, datetime, timedelta

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.calendar import _connect
from helpers.speakr_matching import _event
from helpers.speakr_record import AUDIO, URL, _form
from speakr_fake import connect, speakr  # noqa: F401
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import PendingAction, PermissionRule, RecordingLink, SpeakrRecording


async def _row(cid, rid):
    async with sessionmaker()() as db:
        return (await db.execute(select(SpeakrRecording).where(SpeakrRecording.connector_id == cid,
                                                               SpeakrRecording.recording_id == rid))).scalar_one_or_none()


async def test_folders_and_tags_for_the_form(client, user, speakr):
    conn = await connect(client, speakr)
    f = (await client.get("/api/v1/speakr/folders")).json()
    assert f["connector_id"] == conn["id"] and [x["name"] for x in f["folders"]] == ["Meetings", "Advising"]
    t = (await client.get("/api/v1/speakr/tags", params={"connector_id": conn["id"]})).json()
    assert [(x["id"], x["name"]) for x in t["tags"]] == [(1, "Work"), (2, "Advising")]
    n = speakr.calls.count("/api/v1/tags")
    await client.get("/api/v1/speakr/tags")
    assert speakr.calls.count("/api/v1/tags") == n  # cached for a minute
    assert (await client.get("/api/v1/speakr/tags", params={"connector_id": 999999})).status_code == 404


async def test_recording_upload_sets_metadata_and_adds_a_pending_row(client, user, speakr):
    conn = await connect(client, speakr)
    r = await client.post(URL, **_form())
    assert r.status_code == 200, r.text
    out = r.json()
    rid = out["recording_id"]
    assert out["unsupported_fields"] == [] and out["kept_in_mailr"] == [] and out["link"] is None
    assert out["status"] == "pending" and out["url"].endswith(f"/recordings/{rid}")
    up = speakr.uploads[-1]
    assert up["file"]["filename"] == "mara.m4a" and up["file"]["content"] == AUDIO
    assert up["fields"]["title"] == "Advising: Mara" and up["fields"]["meeting_date"] == "2026-09-30T19:00:00Z"
    assert up["fields"]["notes"] == "## Agenda\n- thesis plan"
    assert up["fields"]["folder_id"] == "2" and (up["fields"]["tag_ids[0]"], up["fields"]["tag_ids[1]"]) == ("1", "2")
    # Speakr's upload takes no participants: a PATCH right after, and nothing else.
    assert [w for w in speakr.writes if w[1] == f"/api/v1/recordings/{rid}"] == [
        ("PATCH", f"/api/v1/recordings/{rid}", {"participants": "Mara Lee, Rowan"})]
    row = await _row(conn["id"], rid)
    assert row.status == "pending" and row.title == "Advising: Mara" and row.participants == ["Mara Lee", "Rowan"]
    assert row.folder["name"] == "Advising" and [t["name"] for t in row.tags] == ["Work", "Advising"]
    assert row.meeting_date == datetime(2026, 9, 30, 19, 0, tzinfo=UTC)
    # Edits of existing recordings stay off; this new recording was still set up.
    assert not conn["config"].get("write_back")


async def test_older_speakr_gets_the_rest_by_patch_and_refusals_are_named(client, user, speakr):
    await connect(client, speakr)
    speakr.old_upload = True
    out = (await client.post(URL, **_form(participants='["Mara Lee"]'))).json()
    rid = out["recording_id"]
    patch = next(w[2] for w in speakr.writes if w[0] == "PATCH" and w[1] == f"/api/v1/recordings/{rid}")
    assert patch == {"participants": "Mara Lee", "title": "Advising: Mara", "meeting_date": "2026-09-30T19:00:00Z",
                     "folder_id": 2}
    assert ("POST", f"/api/v1/recordings/{rid}/tags", {"tag_ids": [1, 2]}) in speakr.writes
    assert out["unsupported_fields"] == []
    speakr.status_for = {r"/api/v1/recordings/\d+$": 400}
    speakr.old_upload = False
    out = (await client.post(URL, **_form())).json()
    assert out["unsupported_fields"] == ["participants"] and "400" in out["unsupported_detail"]["participants"]


async def test_recording_upload_refusals_and_limits(client, user, speakr, monkeypatch):
    await connect(client, speakr)
    assert (await client.post(URL, **_form(folder="Nowhere"))).status_code == 400
    assert (await client.post(URL, **_form(tags="Unknown"))).status_code == 400
    assert (await client.post(URL, **_form(meeting_date="next week"))).status_code == 400
    r = await client.post(URL, data={"title": "x"}, files={"file": ("notes.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 400
    speakr.max_upload = 100
    r = await client.post(URL, **_form())
    assert r.status_code == 413 and "too large" in r.json()["detail"]
    speakr.max_upload = 50 * 1024 * 1024
    monkeypatch.setenv("MAILR_SPEAKR_MAX_UPLOAD_MB", "0")
    r = await client.post(URL, **_form())
    assert r.status_code == 413 and "in Mailr" in r.json()["detail"]
    assert len(speakr.uploads) == 0


async def test_recording_upload_by_token_needs_an_allow_rule(client, user, speakr):
    await connect(client, speakr)
    tok = (await client.post("/api/v1/tokens", json={"name": "recorder", "scopes": ["read", "recordings"],
                                                     "local_only": True})).json()
    h = {"Authorization": f"Bearer {tok['token']}"}
    r = await client.post(URL, headers=h, **_form())
    assert r.status_code == 403 and "approval" in r.json()["detail"]
    async with sessionmaker()() as db:
        assert (await db.execute(select(PendingAction).where(PendingAction.user_id == user.id))).first() is None
        db.add(PermissionRule(user_id=user.id, subject=f"token:{tok['item']['id']}", action="speakr.upload", effect="allow",
                              constraints={}))
        await db.commit()
    r = await client.post(URL, headers=h, **_form())
    assert r.status_code == 200, r.text
    assert len(speakr.uploads) == 1


@pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")
async def test_recording_upload_links_the_event(client, user, speakr):
    await _connect(client)
    conn = await connect(client, speakr)
    start = (datetime.now(UTC) + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    ev = await _event(client, "Advising: Mara", start)
    assert (await client.post(URL, **_form(event_uid="no-such-event"))).status_code == 404
    out = (await client.post(URL, **_form(event_uid=ev["uid"], occurrence=ev.get("occurrence") or ""))).json()
    assert out["kept_in_mailr"] == ["event_uid"] and out["link"]["state"] == "confirmed"
    async with sessionmaker()() as db:
        links = (await db.execute(select(RecordingLink).where(RecordingLink.connector_id == conn["id"],
                                                              RecordingLink.recording_id == out["recording_id"]))).scalars().all()
    assert [(lk.kind, lk.target, lk.state, lk.source) for lk in links] == [("event", ev["uid"], "confirmed", "user")]
    sec = (await client.get("/api/v1/speakr/events/recordings", params={"uid": ev["uid"]})).json()
    assert [x["recording_id"] for x in sec["items"]] == [out["recording_id"]]
