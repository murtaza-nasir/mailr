"""mailr against the Speakr with the mailr API changes, and the same
paths against an older Speakr (the fake's default, the production image of 2 Oct 2026). Features come from GET /capabilities only.

Covers speakr.connect (scopes and capabilities), speakr.sync (the changes feed, 410, tombstones,
shared recordings, webhooks V2), recording.search (v1 search, semantic only when allowed),
recording.record and mail.to_speakr (upload fields, idempotency), recording.link (references),
recording.edit (tags), recording.view (ETags, events with attendees, the canonical transcript),
speaker.map (speaker ids and addresses), recording.share_link (v1 links, list and revoke) and
recording.ask (Speakr's chat with verified citations)."""

import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest
from helpers.speakr_tools import _ctx
from helpers.speakr_writeback import _switch
from speakr_fake import BASE, TOKEN, connect, speakr  # noqa: F401
from sqlalchemy import select

from mailr.agent import speakr_tools
from mailr.agent.tools import ToolError, run_tool
from mailr.core import outbound
from mailr.db import sessionmaker
from mailr.integrations.speakr import client as sc
from mailr.integrations.speakr import store
from mailr.integrations.speakr.sync import sync_connector
from mailr.models import Connector, RecordingLink, SpeakrRecording

AUDIO = b"ID3\x03\x00\x00\x00" + b"\x00" * 4096


async def _conn(cid):
    async with sessionmaker()() as db:
        return await db.get(Connector, cid)


async def _rows(cid):
    async with sessionmaker()() as db:
        return {r.recording_id: r for r in (await db.execute(select(SpeakrRecording).where(
            SpeakrRecording.connector_id == cid))).scalars()}


async def _set_class(client, cid, data_class):
    r = await client.patch(f"/api/v1/connectors/{cid}", json={"data_class": data_class})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- scopes and capabilities


# ACT speakr.connect
async def test_capabilities_and_token_scopes_on_connect(client, user, speakr):
    speakr.modern()
    speakr.token_scopes = ["read", "write", "upload"]
    speakr.add(1)
    body = {"kind": "speakr", "config": {"base_url": BASE}, "secrets": {"token": TOKEN}}
    t = (await client.post("/api/v1/connectors/test", json=body)).json()
    assert t["ok"] and t["scopes"] == ["read", "write", "upload"] and "read, write, upload" in t["detail"]
    assert any("webhooks scope" in w for w in t["warnings"])
    conn = await connect(client, speakr)
    cfg = conn["config"]
    assert cfg["speakr_capabilities"]["available"] and cfg["speakr_capabilities"]["features"]["changes_feed"] is True
    assert cfg["speakr_token"]["scopes"] == ["read", "write", "upload"] and TOKEN not in json.dumps(conn)
    card = (await client.get("/api/v1/connectors")).json()
    sp = next(c for c in card if c["id"] == conn["id"])["speakr"]
    assert sp["scopes"] == ["read", "write", "upload"] and sp["changes_feed"] and not sp["speakr_model_ok"]

    # A full-access token is named as more than mailr needs.
    speakr.token_scopes = ["full"]
    t = (await client.post("/api/v1/connectors/test", json=body)).json()
    assert t["scopes"] == ["full"] and "full access" in t["warnings"][0]


# ACT speakr.connect
async def test_old_speakr_has_no_capabilities_and_works_as_before(client, user, speakr):
    speakr.add(1)
    conn = await connect(client, speakr)
    assert conn["config"]["speakr_capabilities"] == {"available": False, "features": {}, "models_local": False}
    assert "speakr_token" not in conn["config"]
    assert "/api/v1/recordings/changes" not in speakr.calls and "/api/v1/recordings" in speakr.calls
    card = next(c for c in (await client.get("/api/v1/connectors")).json() if c["id"] == conn["id"])["speakr"]
    assert card["scopes"] is None and card["scope_notes"] == []


# ACT speakr.sync
async def test_insufficient_scope_is_a_configuration_error(client, user, speakr):
    speakr.modern()
    speakr.add(1)
    conn = await connect(client, speakr)
    cid = conn["id"]
    # A token without write: an edit names the scope to add (not "not found").
    speakr.token_scopes = ["read"]
    await _switch(client, cid, write_back=True)
    r = await client.patch(f"/api/v1/speakr/recordings/{cid}/1", json={"title": "New"})
    assert r.status_code == 409 and "write scope" in r.json()["detail"]
    # A token that cannot read: the sync stops with the same kind of message, without the breaker.
    speakr.token_scopes = ["upload"]
    assert (await sync_connector(cid)) == {"error": "scope"}
    c = await _conn(cid)
    assert c.status == "error" and "read scope" in c.status_detail and "fails" not in (c.state or {})


async def test_429_blocks_the_connector_until_retry_after(speakr):
    speakr.add(1)
    cl = sc.SpeakrClient(BASE, TOKEN, bucket="t429")
    speakr.status, speakr.retry_after = 429, "30"
    with pytest.raises(sc.SpeakrRateLimited):
        await cl.recording(1)
    speakr.status = None
    n = len(speakr.calls)
    with pytest.raises(sc.SpeakrRateLimited) as e:
        await cl.recording(1)
    assert len(speakr.calls) == n and 25 < e.value.retry_after <= 30  # nothing went out


# ---------------------------------------------------------------- the changes feed and ETags


# ACT speakr.sync
async def test_changes_feed_sync_tombstones_and_expired_cursor(client, user, speakr):
    speakr.modern()
    for i in (1, 2, 3):
        speakr.add(i, f"Meeting {i}")
    conn = await connect(client, speakr)
    cid = conn["id"]
    assert "/api/v1/recordings" not in speakr.calls
    assert len(await _rows(cid)) == 3 and (await _conn(cid)).state["changes_cursor"]

    speakr.calls.clear()
    speakr.recordings[2]["title"] = "Renamed"
    speakr.touch(2)
    speakr.delete(3)
    out = await sync_connector(cid)
    assert out["feed"] and not out["complete"] and out["changed"] == 1 and out["removed"] == 1
    rows = await _rows(cid)
    assert rows[2].title == "Renamed" and rows[3].deleted_at is not None and rows[1].deleted_at is None
    assert (await _conn(cid)).status_detail == "2 recordings"
    assert [c for c in speakr.calls if "/summary" in c] == ["/api/v1/recordings/2/summary"]  # no rechecks

    # Nothing changed: one small request.
    speakr.calls.clear()
    out = await sync_connector(cid)
    assert out["changed"] == 0 and [c for c in speakr.calls if "recordings" in c] == ["/api/v1/recordings/changes"]

    # 410 cursor_expired: one full pass without a cursor; a recording gone meanwhile is a tombstone.
    speakr.expire_cursor = True
    speakr.recordings.pop(1)
    out = await sync_connector(cid)
    assert out["complete"] and out["removed"] == 1 and (await _rows(cid))[1].deleted_at is not None


# ACT recording.view
async def test_etags_revalidate_cached_parts(client, user, speakr, monkeypatch):
    speakr.modern()
    speakr.add(1)
    conn = await connect(client, speakr)
    url = f"/api/v1/speakr/recordings/{conn['id']}/1/transcript"
    assert (await client.get(url)).status_code == 200
    monkeypatch.setattr(store, "FRESH", 0.0)
    r = await client.get(url)
    assert r.status_code == 200 and r.json()["segments"] and speakr.not_modified == 1
    # Changed in Speakr: the next read gets the new text.
    speakr.transcripts[1]["segments"][0]["sentence"] = "Welcome back."
    r = await client.get(url)
    assert r.json()["segments"][0]["text"] == "Welcome back." and speakr.not_modified == 1


# ---------------------------------------------------------------- webhooks


async def _hook(client, speakr, cid):
    r = await client.post(f"/api/v1/speakr/connectors/{cid}/webhook")
    assert r.status_code == 200, r.text
    return r.json()["path"], next(iter(speakr.webhooks.values()))["secret"]


def _signed(secret, body, *, v2=True, extra_v1=None, t=None):
    from mailr.integrations.speakr.webhook import sign, sign_v2

    raw = json.dumps(body).encode()
    h = {"content-type": "application/json", "Speakr-Delivery-Id": body["id"], "Speakr-Event": body["type"]}
    if v2:
        h["Speakr-Signature-V2"] = sign_v2(secret, t or int(time.time()), raw) + (f",v1={extra_v1}" if extra_v1 else "")
    else:
        h["Speakr-Signature"] = sign(secret, raw)
    return raw, h


# ACT speakr.sync
async def test_webhook_v2_required_once_advertised_and_rotation(client, user, speakr, app, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from mailr.config import get_settings

    monkeypatch.setattr(get_settings(), "base_url", "https://mail.example")
    speakr.modern()
    speakr.add(1)
    conn = await connect(client, speakr)
    path, secret = await _hook(client, speakr, conn["id"])
    body = {"id": uuid.uuid4().hex, "type": "recording.updated", "timestamp": "2026-10-02T10:00:00Z",
            "data": {"recording_id": 1}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        raw, h = _signed(secret, body, v2=False)
        assert (await anon.post(path, content=raw, headers=h)).status_code == 401  # V1 only: refused
        # During a rotation the header carries the new and the old value; either matches.
        raw, h = _signed("whsec-old", body, extra_v1=_signed(secret, body)[1]["Speakr-Signature-V2"].split("v1=")[1])
        r = await anon.post(path, content=raw, headers=h)
        assert r.status_code == 200 and r.json()["queued"]
        # A repeat of a processed delivery: 200, so Speakr stops retrying.
        r = await anon.post(path, content=raw, headers=h)
        assert r.status_code == 200 and r.json()["duplicate"]
        # A stale signed send time is refused, whatever the body says.
        body["id"] = uuid.uuid4().hex
        raw, h = _signed(secret, body, t=int(time.time()) - 900)
        assert (await anon.post(path, content=raw, headers=h)).status_code == 400


async def test_webhook_old_speakr_accepts_v1_and_include_shared(client, user, speakr, app, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from mailr.config import get_settings

    monkeypatch.setattr(get_settings(), "base_url", "https://mail.example")
    speakr.add(1)
    conn = await connect(client, speakr)
    path, secret = await _hook(client, speakr, conn["id"])
    assert not next(iter(speakr.webhooks.values()))["include_shared"]
    body = {"id": uuid.uuid4().hex, "type": "recording.updated", "timestamp": datetime.now(UTC).isoformat(),
            "data": {"recording_id": 1}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        raw, h = _signed(secret, body, v2=False)
        assert (await anon.post(path, content=raw, headers=h)).status_code == 200


# ACT speakr.connect
async def test_webhook_follows_the_shared_switch(client, user, speakr, monkeypatch):
    from mailr.config import get_settings

    monkeypatch.setattr(get_settings(), "base_url", "https://mail.example")
    speakr.modern()
    speakr.add(1)
    conn = await connect(client, speakr)
    await _hook(client, speakr, conn["id"])
    hook = next(iter(speakr.webhooks.values()))
    assert hook["include_shared"] is False
    await _switch(client, conn["id"], include_shared=True)
    assert hook["include_shared"] is True


# ---------------------------------------------------------------- search


# ACT recording.search
async def test_v1_search_keyword_only_for_local_only_and_semantic_when_allowed(client, user, speakr, monkeypatch):
    speakr.modern()
    old = datetime.now(UTC) - timedelta(days=400)
    speakr.add(1, "Old planning", meeting_date=old,
               segments=[(0, "Dana", "Hello."), (754, "Omar", "The parking garage closes in March.")])
    for i in range(2, 14):
        speakr.add(i, f"Recent {i}")
    conn = await connect(client, speakr)
    cid = conn["id"]
    out = (await client.get("/api/v1/speakr/search", params={"q": "parking garage"})).json()
    hit = next(h for h in out["hits"] if h["where"] == "transcript")
    assert hit["recording_id"] == 1 and hit["t"] == 754 and hit["speaker"] == "Omar" and hit["cite"] == "[[r:1@754]]"
    assert out["full_text"] and not out["more"]
    assert speakr.searches == ["keyword"]  # local-only: the query never reaches Speakr's embedding model
    assert not [c for c in speakr.calls if c.endswith("/transcript")]  # no scan

    await _set_class(client, cid, "normal")
    speakr.searches.clear()
    await client.get("/api/v1/speakr/search", params={"q": "parking"})
    assert speakr.searches == ["keyword", "semantic"]

    # Local-only again, and Speakr states its models are local: that counts only for a Speakr whose
    # host is on the local network (gap 9; the fake's host name does not resolve).
    await _set_class(client, cid, "local_only")
    speakr.models_local = True
    await sync_connector(cid)
    speakr.searches.clear()
    await client.get("/api/v1/speakr/search", params={"q": "parking"})
    assert speakr.searches == ["keyword"]
    monkeypatch.setattr(outbound, "host_is_local_now", lambda url: True)
    await sync_connector(cid)
    speakr.searches.clear()
    await client.get("/api/v1/speakr/search", params={"q": "parking"})
    assert speakr.searches == ["keyword", "semantic"]


# ACT recording.search
async def test_old_speakr_search_scans_recent_transcripts(client, user, speakr):
    speakr.add(1, segments=[(0, "Dana", "The parking garage closes in March.")])
    await connect(client, speakr)
    out = (await client.get("/api/v1/speakr/search", params={"q": "parking garage"})).json()
    assert out["hits"] and not out["full_text"] and "/api/v1/search" not in speakr.calls
    status = (await client.get("/api/v1/speakr/status")).json()
    assert status["connectors"][0]["search"] is False


# ---------------------------------------------------------------- uploads and references


# ACT recording.record
async def test_new_recording_sends_participants_and_event_ref_on_the_upload(client, user, speakr, monkeypatch):
    from dev_services import radicale_up as _radicale_up
    from helpers.calendar import _connect
    from helpers.speakr_matching import _event

    if not _radicale_up():
        pytest.skip("Radicale dev server not running")
    speakr.modern()
    speakr.add(1)
    conn = await connect(client, speakr)
    await _connect(client)
    ev = await _event(client, "Advising: Mara", datetime.now(UTC).replace(microsecond=0))
    r = await client.post("/api/v1/speakr/recordings", data={"title": "Advising: Mara", "participants": "Mara Lee, Rowan",
                                                             "event_uid": ev["uid"], "connector_id": str(conn["id"])},
                          files={"file": ("mara.m4a", AUDIO, "audio/mp4")})
    assert r.status_code == 200, r.text
    out = r.json()
    up = speakr.uploads[-1]["fields"]
    assert up["participants"] == "Mara Lee, Rowan"
    refs = json.loads(up["external_refs"])
    assert refs[0]["system"] == "mailr" and refs[0]["kind"] == "event" and refs[0]["ref"].startswith(ev["uid"])
    assert not [w for w in speakr.writes if w[0] == "PATCH"]  # no PATCH for the participants
    assert out["kept_in_mailr"] == [] and out["link"]["state"] == "confirmed"


# ACT mail.to_speakr
async def test_attachment_upload_is_idempotent_with_a_conversation_ref(client, user, speakr):
    from helpers.speakr_upload import _message

    speakr.modern()
    speakr.add(1)
    await connect(client, speakr)
    mid, cid, parts = await _message(client, user)
    r1 = (await client.post("/api/v1/speakr/upload", json={"message_id": mid, "part": parts["interview.mp3"]})).json()
    up = speakr.uploads[-1]["fields"]
    assert up["idempotency_key"] == f"mailr-{mid}:{parts['interview.mp3']}"
    ref = json.loads(up["external_refs"])[0]
    assert ref["kind"] == "conversation" and ref["label"] == "Interview recording"
    # A retry after a timeout: the same recording, no second one.
    n = len(speakr.recordings)
    r2 = (await client.post("/api/v1/speakr/upload", json={"message_id": mid, "part": parts["interview.mp3"]})).json()
    assert r2["recording_id"] == r1["recording_id"] and r2["existing"] and len(speakr.recordings) == n


# ACT mail.to_speakr
async def test_attachment_upload_on_old_speakr_sends_no_new_fields(client, user, speakr):
    from helpers.speakr_upload import _message

    speakr.add(1)
    await connect(client, speakr)
    mid, _, parts = await _message(client, user)
    assert (await client.post("/api/v1/speakr/upload", json={"message_id": mid, "part": parts["interview.mp3"]})).status_code == 200
    assert not {"idempotency_key", "external_refs", "participants"} & set(speakr.uploads[-1]["fields"])


# ACT recording.link
async def test_confirmed_links_become_references_only_with_write_back(client, user, speakr):
    from dev_services import radicale_up as _radicale_up
    from helpers.calendar import _connect
    from helpers.speakr_matching import _event

    if not _radicale_up():
        pytest.skip("Radicale dev server not running")
    await _connect(client)
    t = (datetime.now(UTC) - timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)
    ev = await _event(client, "Budget review", t)
    speakr.modern()
    speakr.add(1, "Budget review", meeting_date=t)
    conn = await connect(client, speakr)
    cid = conn["id"]
    body = {"connector_id": cid, "recording_id": 1, "kind": "event", "target": ev["uid"]}
    lk = (await client.post("/api/v1/speakr/links", json=body)).json()
    assert not [w for w in speakr.writes if "external-refs" in w[1]]  # write-back off: nothing in Speakr
    await _switch(client, cid, write_back=True)
    await client.post("/api/v1/speakr/links", json=body)
    put = [w for w in speakr.writes if "external-refs" in w[1]][-1]
    assert put[1].endswith("?system=mailr") and put[2]["external_refs"][0]["ref"] == ev["uid"]
    assert put[2]["external_refs"][0]["url"].endswith(f"/calendar/event/{quote(ev['uid'], safe='')}/")
    # Unlinking removes the reference.
    await client.delete(f"/api/v1/speakr/links/{lk['id']}")
    assert [w for w in speakr.writes if "external-refs" in w[1]][-1][2]["external_refs"] == []


# ACT recording.link
async def test_matcher_uses_mailr_references_first(client, user, speakr):
    from dev_services import radicale_up as _radicale_up
    from helpers.calendar import _connect
    from helpers.speakr_matching import _event

    if not _radicale_up():
        pytest.skip("Radicale dev server not running")
    await _connect(client)
    t = (datetime.now(UTC) - timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
    far = await _event(client, "Quarterly planning", t - timedelta(days=1))  # far from the recording's time
    await _event(client, "Budget review", t)
    speakr.modern()
    speakr.add(1, "Budget review", meeting_date=t)
    speakr.refs[1] = [{"id": 70, "system": "mailr", "kind": "event", "ref": far["uid"], "url": None, "label": None}]
    conn = await connect(client, speakr)
    async with sessionmaker()() as db:
        links = (await db.execute(select(RecordingLink).where(RecordingLink.connector_id == conn["id"]))).scalars().all()
    assert [(lk.target, lk.state, lk.source) for lk in links if lk.state != "dismissed"] == [(far["uid"], "confirmed", "speakr")]


# ---------------------------------------------------------------- tag writes


# ACT recording.edit
async def test_tags_put_on_new_speakr_and_post_delete_on_old(client, user, speakr):
    speakr.add(1, tags=["Work"])
    conn = await connect(client, speakr)
    cid = conn["id"]
    await _switch(client, cid, write_back=True)
    r = await client.patch(f"/api/v1/speakr/recordings/{cid}/1", json={"tags": ["Advising"]})
    assert r.status_code == 200, r.text
    assert [w[0] for w in speakr.writes if "/tags" in w[1]] == ["POST", "DELETE"]  # an older Speakr: no PUT
    assert [t["name"] for t in speakr.recordings[1]["tags"]] == ["Advising"]

    speakr.modern()
    speakr.writes.clear()
    r = await client.patch(f"/api/v1/speakr/recordings/{cid}/1", json={"tags": ["Work", "Advising"]})
    assert r.status_code == 200 and r.json()["recording"]["tags"] == ["Work", "Advising"]
    assert [w[0] for w in speakr.writes if "/tags" in w[1]] == ["PUT"]


# ACT recording.edit
async def test_group_tag_that_shares_needs_a_share_and_no_errand(client, user, speakr):
    speakr.modern()
    speakr.tags.append({"id": 3, "name": "Advising team", "color": "#a00", "is_group_tag": True,
                        "auto_share_on_apply": True})
    speakr.add(1, tags=["Work"])
    conn = await connect(client, speakr)
    await _switch(client, conn["id"], write_back=True)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        ctx.chat_context = {"errand": True}
        with pytest.raises(ToolError, match="errand cannot add a tag that shares"):
            await run_tool(ctx, "update_recording", {"recording_id": 1, "tags": ["Work", "Advising team"]})
        ctx = await _ctx(db, user.id)
        out = await run_tool(ctx, "update_recording", {"recording_id": 1, "tags": ["Work", "Advising team"]})
        assert out.pending is not None and out.pending["action"] == "speakr.share"  # a share always asks
    assert not [w for w in speakr.writes if "/tags" in w[1]]


# ---------------------------------------------------------------- events, transcripts, shared recordings


# ACT recording.view, ACT recording.event_to_calendar @api (the events with their attendees, for the event editor)
async def test_events_with_attendees_and_canonical_transcript(client, user, speakr):
    speakr.modern()
    speakr.add(1)
    conn = await connect(client, speakr)
    ev = (await client.get(f"/api/v1/speakr/recordings/{conn['id']}/1/events")).json()["events"][0]
    assert ev["attendees"] == [{"name": "Dana Lee", "email": None}, {"name": "Priya Shah", "email": "priya@example.org"}]
    assert ev["reminder_minutes"] == 15 and ev["floating"] is True and "Z" not in ev["start"]
    tr = (await client.get(f"/api/v1/speakr/recordings/{conn['id']}/1/transcript")).json()
    assert tr["segments"][0]["speaker_id"] == 1 and tr["segments"][0]["label"] == "SPEAKER_00"
    # A time range not in the cache: only that window is asked for.
    store.forget(conn["id"])
    tr = (await client.get(f"/api/v1/speakr/recordings/{conn['id']}/1/transcript", params={"start": 700})).json()
    assert [s["t"] for s in tr["segments"]] == [754, 790]
    assert speakr.queries[-1] == ("/api/v1/recordings/1/transcript", {"format": "json", "start": "700.0"})


async def test_old_speakr_events_and_transcript_shape(client, user, speakr):
    speakr.add(1, key_set="old")
    conn = await connect(client, speakr)
    ev = (await client.get(f"/api/v1/speakr/recordings/{conn['id']}/1/events")).json()["events"][0]
    assert ev["attendees"] == [] and ev["reminder_minutes"] is None and ev["floating"] is None
    tr = (await client.get(f"/api/v1/speakr/recordings/{conn['id']}/1/transcript")).json()
    assert tr["segments"][2]["t"] == 754 and "speaker_id" not in tr["segments"][0]


# ACT speakr.sync
async def test_shared_recordings_option(client, user, speakr):
    speakr.modern()
    speakr.add(1, "Mine")
    speakr.share_with_me(2, title="Evan's lab meeting", owner="Evan Ross", can_edit=False)
    conn = await connect(client, speakr)
    cid = conn["id"]
    assert set(await _rows(cid)) == {1}  # off by default
    await _switch(client, cid, include_shared=True, write_back=True, share_links=True)
    out = await sync_connector(cid)
    assert out["complete"]  # the scope changed: a full pass
    row = (await _rows(cid))[2]
    assert row.is_shared and row.owner_name == "Evan Ross" and not row.can_edit
    rec = (await client.get(f"/api/v1/speakr/recordings/{cid}/2")).json()
    assert rec["is_shared"] and not rec["write_back"] and not rec["share_links"]
    r = await client.patch(f"/api/v1/speakr/recordings/{cid}/2", json={"title": "x"})
    assert r.status_code == 403 and "without edit rights" in r.json()["detail"]
    # The share is removed in Speakr: an access_revoked tombstone drops the row.
    speakr.unshare(2)
    await sync_connector(cid)
    assert (await _rows(cid))[2].deleted_at is not None
    # Off again: the shared rows go on the next (full) pass.
    speakr.share_with_me(3, title="Another")
    await sync_connector(cid)
    await _switch(client, cid, include_shared=False)
    await sync_connector(cid)
    assert (await _rows(cid))[3].deleted_at is not None and (await _rows(cid))[1].deleted_at is None


# ---------------------------------------------------------------- speakers


# ACT speaker.map
async def test_speaker_email_suggestion_write_and_rename(client, user, speakr):
    speakr.modern()
    speakr.add(1, participants="Dana, Evan")
    speakr.speakers[1] = [{"label": "SPEAKER_00", "identified_name": "Dana", "segment_count": 2},
                          {"label": "SPEAKER_01", "identified_name": "Evan", "segment_count": 2}]
    conn = await connect(client, speakr)
    cid = conn["id"]
    d = (await client.get(f"/api/v1/speakr/recordings/{cid}/1/people")).json()
    evan = next(s for s in d["speakers"] if s["name"] == "Evan")
    assert d["speakr_email"] and evan["speaker_id"] == 2
    assert evan["suggestions"][0]["email"] == "evan@example.org" and evan["suggestions"][0]["why"] == "address saved in Speakr"
    dana = next(s for s in d["speakers"] if s["name"] == "Dana")
    body = {"key": dana["key"], "speaker_name": "Dana", "email": "dana@example.org", "speaker_id": 1, "recording_id": 1}
    r = (await client.post(f"/api/v1/speakr/speakers/{cid}/person", json=body)).json()
    assert r["written_to_speakr"] is None and not [w for w in speakr.writes if "/speakers/" in w[1]]  # write-back off
    await _switch(client, cid, write_back=True)
    r = (await client.post(f"/api/v1/speakr/speakers/{cid}/person", json=body)).json()
    assert r["written_to_speakr"] is True and speakr.profiles[0]["email"] == "dana@example.org"
    # Renamed in Speakr: the person follows the speaker id to the new name.
    speakr.profiles[0]["name"] = "Dana Lee"
    speakr.speakers[1][0]["identified_name"] = "Dana Lee"
    store.forget(cid)
    d = (await client.get(f"/api/v1/speakr/recordings/{cid}/1/people")).json()
    renamed = next(s for s in d["speakers"] if s["name"] == "Dana Lee")
    assert renamed["person"]["email"] == "dana@example.org"


# ---------------------------------------------------------------- share links


# ACT recording.share_link
async def test_v1_share_links_flags_list_and_revoke(client, user, speakr):
    speakr.modern()
    speakr.add(1)
    conn = await connect(client, speakr)
    cid = conn["id"]
    await _switch(client, cid, share_links=True)
    a = (await client.post(f"/api/v1/speakr/recordings/{cid}/1/share", json={"share_notes": False})).json()
    assert not a["existing"] and a["share_notes"] is False and not a["flags_differ"]
    assert ("POST", "/api/v1/recordings/1/share") in [(w[0], w[1]) for w in speakr.writes]
    b = (await client.post(f"/api/v1/speakr/recordings/{cid}/1/share", json={"share_notes": True})).json()
    assert b["existing"] and b["flags_differ"] and b["share_notes"] is False  # the link kept its flags
    listed = (await client.get(f"/api/v1/speakr/recordings/{cid}/1/shares")).json()
    assert listed["supported"] and [s["id"] for s in listed["shares"]] == [a["share_id"]]
    assert (await client.delete(f"/api/v1/speakr/recordings/{cid}/1/shares/{a['share_id']}")).status_code == 200
    assert not speakr.public


async def test_old_speakr_share_uses_web_route_and_cannot_list(client, user, speakr):
    speakr.add(1)
    conn = await connect(client, speakr)
    cid = conn["id"]
    await _switch(client, cid, share_links=True)
    a = (await client.post(f"/api/v1/speakr/recordings/{cid}/1/share", json={})).json()
    assert "share_notes" not in a and ("POST", "/api/recording/1/share") in [(w[0], w[1]) for w in speakr.writes]
    assert (await client.get(f"/api/v1/speakr/recordings/{cid}/1/shares")).json() == {"supported": False, "shares": []}


# ---------------------------------------------------------------- Speakr's chat


# ACT recording.ask
async def test_ask_recording_only_when_speakr_models_may_read_and_citations_checked(client, user, speakr):
    speakr.modern()
    speakr.add(1)
    conn = await connect(client, speakr)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        assert "ask_recording" in await speakr_tools.hidden_tools(db, ctx.p)  # local-only, hosted models
        with pytest.raises(ToolError, match="may not read"):
            await run_tool(ctx, "ask_recording", {"recording_id": 1, "question": "When do we ship?"})
    assert not [w for w in speakr.writes if w[1].endswith("/chat")]

    await _set_class(client, conn["id"], "normal")
    speakr.chat_reply = {"response": "They ship on Friday [1]. Evan writes the plan [2].",
                         "sources": [{"n": 1, "start_time": 754, "quote": "ship the budget on Friday", "verified": True},
                                     {"n": 2, "start_time": 12, "quote": "Evan promised a pony", "verified": True}]}
    with outbound.scope():  # a new request: the local-only reads above taint only their own (core/outbound.py)
        async with sessionmaker()() as db:
            ctx = await _ctx(db, user.id, local_model=False)
            assert "ask_recording" not in await speakr_tools.hidden_tools(db, ctx.p)
            out = await run_tool(ctx, "ask_recording", {"recording_id": 1, "question": "When do we ship?"})
    assert out.data["dropped_citations"] == 1 and out.data["source"] == "speakr" and not out.sensitive
    assert [c["t"] for c in out.data["citations"]] == [754]
    assert ("POST", "/api/v1/recordings/1/chat") in [(w[0], w[1]) for w in speakr.writes]
    answer, cites, dropped = speakr_tools.verified_answer(1, speakr.chat_reply["response"], speakr.chat_reply["sources"],
                                                          sc.segments({"segments": speakr.transcripts[1]["segments"]}))
    assert answer == 'They ship on Friday [[r:1@754 "ship the budget on Friday"]]. Evan writes the plan.'
    assert dropped == 1 and [c["n"] for c in cites] == [1]
