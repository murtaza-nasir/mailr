# ACT-SURFACES: api assistant
"""Summaries kept for search (recording.search): the sync stores each
recording's summary with a full-text index (newest first, a bounded number a pass, read again when
the recording changes and, a few a pass, after a day), never the transcript; the Recordings
section, the palette (GET /speakr/recordings?q=), search_recordings and recording:<text> in mail
search find words that are only in a summary, of any age; a hosted model and a token that is not
local-only get nothing from a local-only account's summaries. Also: Speakr's pagination keys and
its null speaker names."""

from datetime import UTC, datetime, timedelta

import httpx
from helpers.core_flow import _ingest
from helpers.speakr_tools import _ctx
from speakr_fake import connect, speakr  # noqa: F401
from sqlalchemy import select, text

from mailr.agent.tools import run_tool
from mailr.db import sessionmaker
from mailr.integrations.speakr import client as sc
from mailr.integrations.speakr import sync as ssync
from mailr.models import SpeakrRecording

THESIS = "## Advising\n- Mara's thesis proposal is due in March\n- Next meeting after spring break"


async def _rows(cid):
    async with sessionmaker()() as db:
        return {r.recording_id: r for r in (await db.execute(select(SpeakrRecording).where(
            SpeakrRecording.connector_id == cid))).scalars()}


async def test_sync_stores_summaries_for_search_and_reads_them_again(client, user, speakr, monkeypatch):
    now = datetime.now(UTC)
    speakr.add(1, "Weekly sync", meeting_date=now - timedelta(days=2))
    speakr.add(2, "Advising", meeting_date=now - timedelta(days=40), summary=THESIS)
    speakr.add(3, "Still processing", meeting_date=now - timedelta(hours=1), summary=None)
    monkeypatch.setattr(ssync, "SUMMARIES_PER_PASS", 1)
    conn = await connect(client, speakr)
    rows = await _rows(conn["id"])
    # One a pass, newest first; a recording without a summary needs no request.
    assert "budget" in rows[1].summary_text and rows[1].summary_at is not None
    assert rows[2].summary_at is None and rows[2].summary_text == ""
    assert rows[3].summary_at is not None and rows[3].summary_text == ""
    out = await ssync.sync_connector(conn["id"])
    assert out["indexed"] == 1 and out["unindexed"] == 0
    rows = await _rows(conn["id"])
    assert rows[2].summary_text == THESIS
    # Transcripts are never stored.
    async with sessionmaker()() as db:
        dump = (await db.execute(text("SELECT string_agg(speakr_recordings::text, ' ') FROM speakr_recordings "
                                      "WHERE connector_id = :c"), {"c": conn["id"]})).scalar_one()
    assert "Welcome everyone" not in dump and "budget" in dump

    # A summary changed in Speakr without a change in the list: read again once a day.
    speakr.summaries[2] = THESIS + "\n- Payroll forms for the assistantship"
    n = len(speakr.calls)
    await ssync.sync_connector(conn["id"])
    assert "/api/v1/recordings/2/summary" not in speakr.calls[n:]
    async with sessionmaker()() as db:
        r = (await db.execute(select(SpeakrRecording).where(SpeakrRecording.connector_id == conn["id"],
                                                            SpeakrRecording.recording_id == 2))).scalar_one()
        r.summary_at = now - timedelta(days=2)
        await db.commit()
    await ssync.sync_connector(conn["id"])
    assert "Payroll" in (await _rows(conn["id"]))[2].summary_text
    # A change in the list (here the title): read again on that sync.
    speakr.recordings[1]["title"] = "Weekly sync (budget)"
    speakr.summaries[1] = "## Decisions\n- Hiring freeze until May"
    await ssync.sync_connector(conn["id"])
    assert "Hiring freeze" in (await _rows(conn["id"]))[1].summary_text
    # Processing finished: the new summary is read.
    speakr.recordings[3].update(status="COMPLETED", has_summary=True)
    speakr.summaries[3] = "- Grant budget approved"
    await ssync.sync_connector(conn["id"])
    assert (await _rows(conn["id"]))[3].summary_text == "- Grant budget approved"


async def test_summary_index_feeds_every_recording_search(client, user, raw, speakr):
    now = datetime.now(UTC)
    for i in range(1, 11):  # ten recent recordings push the old one out of the transcript scan
        speakr.add(i, f"Standup {i}", meeting_date=now - timedelta(days=i), summary="- Nothing new")
    speakr.add(50, "Advising", meeting_date=now - timedelta(days=90), summary=THESIS)
    conn = await connect(client, speakr)

    # The Recordings section in mail search and the palette: GET /speakr/recordings?q=.
    items = (await client.get("/api/v1/speakr/recordings", params={"q": "thesis"})).json()["items"]
    assert [x["recording_id"] for x in items] == [50]
    assert items[0]["summary_match"] == "Mara's thesis proposal is due in March"
    assert [x["recording_id"] for x in (await client.get("/api/v1/speakr/recordings", params={"q": "thes"})).json()["items"]] == [50]
    assert (await client.get("/api/v1/speakr/recordings", params={"q": "zebra"})).json()["items"] == []

    # search: the summary hit of a recording outside the transcript scan.
    r = (await client.get("/api/v1/speakr/search", params={"q": "thesis proposal"})).json()
    assert r["more"] is True
    assert [(h["recording_id"], h["where"]) for h in r["hits"]] == [(50, "summary")]

    # search_recordings: a local model reads it; a hosted model is told it is hidden.
    async with sessionmaker()() as db:
        res = await run_tool(await _ctx(db, user.id, local_model=True), "search_recordings", {"q": "thesis"})
        assert [h["recording_id"] for h in res.data["hits"]["content"]] == [50] and res.sensitive
        res = await run_tool(await _ctx(db, user.id, local_model=False), "search_recordings", {"q": "thesis"})
        assert res.data["hits"]["content"] == [] and res.data["recordings"] == [] and res.data.get("hidden_note")
        lst = await run_tool(await _ctx(db, user.id, local_model=True), "list_recordings", {"q": "thesis"})
        assert lst.data["items"][0]["summary_match"]["untrusted"] is True

    # recording:<text> in mail search, on a conversation linked to the recording.
    _, cid = await _ingest(user, raw("Advising follow-up", body="see you after the break"), roles=["inbox"])
    r = await client.post("/api/v1/speakr/links", json={"connector_id": conn["id"], "recording_id": 50,
                                                         "kind": "conversation", "conversation_id": cid})
    assert r.status_code == 200, r.text
    ids = {c["id"] for c in (await client.get("/api/v1/conversations", params={"view": "all", "q": "recording:thesis"})).json()["items"]}
    assert ids == {cid}
    async with sessionmaker()() as db:
        hosted = await run_tool(await _ctx(db, user.id, local_model=False), "search_conversations", {"q": "recording:thesis"})
        assert hosted.data["items"] == []
        local = await run_tool(await _ctx(db, user.id, local_model=True), "search_conversations", {"q": "recording:thesis"})
        assert [c["conversation_id"] for c in local.data["items"]] == [cid]
        # The title still matches for every model (metadata, as before).
        title = await run_tool(await _ctx(db, user.id, local_model=False), "search_conversations", {"q": "recording:advising"})
        assert [c["conversation_id"] for c in title.data["items"]] == [cid]
    plain = (await client.post("/api/v1/tokens", json={"name": "hosted-agent", "scopes": ["read", "recordings"],
                                                       "local_only": False})).json()["token"]
    r = await client.get("/api/v1/conversations", params={"view": "all", "q": "recording:thesis"},
                         headers={"Authorization": f"Bearer {plain}"})
    assert r.status_code == 200 and r.json()["items"] == []


async def test_pagination_total_pages_and_null_speaker_names(client, user, speakr):
    pages = []

    def handler(req: httpx.Request) -> httpx.Response:
        page = int(req.url.params["page"])
        pages.append(page)
        return httpx.Response(200, json={"recordings": [{"id": page}], "pagination": {"page": page, "total_pages": 3}})

    sc.use_transport(httpx.MockTransport(handler))
    try:
        cl = sc.SpeakrClient("https://speakr.test", "t")
        assert [d["id"] async for d in cl.all_recordings()] == [1, 2, 3]
    finally:
        sc.use_transport(httpx.MockTransport(speakr.handler))

    # Speakr's /speakers answers null names and ids: the names come from the transcript.
    speakr.null_speakers = True
    speakr.add(1, segments=[(0, "Dana", "Hello."), (5, "SPEAKER_01", "Hi."), (9, "Dana", "Budget first.")])
    conn = await connect(client, speakr)
    d = (await client.get(f"/api/v1/speakr/recordings/{conn['id']}/1/people")).json()
    got = {s["label"]: (s["name"], s["key"]) for s in d["speakers"]}
    assert got == {"Dana": ("Dana", "name:dana"), "SPEAKER_01": ("", "label:1:speaker_01")}
