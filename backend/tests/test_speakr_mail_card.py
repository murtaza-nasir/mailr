# ACT-SURFACES: web.list web.reader
"""Speakr notification emails (recording.view in the reader, recording.search for the list chip; design
section 3.8): an email is recognised by a link whose scheme, host, port and path match a connected
Speakr, and the recording id is the only thing read from it. A forged email with a foreign host or
with an id the user cannot see shows no card; the subject and body text of the email never reach the
card. Needs the dev Stalwart (ingest)."""

import pytest
from helpers.core_flow import _ingest
from speakr_fake import BASE, connect, speakr  # noqa: F401
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.integrations.speakr import mail as smail
from mailr.models import Connector, Job, RecordingLink


def _conn(base=BASE, cid=1):
    return Connector(id=cid, user_id=1, kind="speakr", config={"base_url": base})


def test_recording_refs_match_scheme_host_port_and_path():
    c = _conn()
    text = (f"Open it: {BASE}/recordings/12 and again {BASE}/recordings/12/?t=30, "
            "fake https://speakr.test.evil.example/recordings/13, http://speakr.test/recordings/14, "
            "https://speakr.test:8443/recordings/15, https://speakr.test/admin/recordings/16, "
            f"{BASE}/recordings/17x, <a href=\"{BASE}/recordings/18#top\">x</a>")
    assert [rid for _, rid in smail.recording_refs(text, [c])] == [12, 18]
    sub = _conn("https://tools.example/speakr", 2)
    assert [rid for _, rid in smail.recording_refs("https://tools.example/speakr/recordings/4 "
                                                   "https://tools.example/recordings/5", [sub])] == [4]
    assert smail.recording_refs(f"{BASE}/recordings/12", []) == []
    assert smail.one_line("## Decisions\n- **Ship** the budget\n- Evan drafts") == "Ship the budget"


def _mail(raw, subject, body, frm="Speakr <noreply@speakr.test>"):
    return raw(subject, frm=frm, body=body)


async def _jobs(kind):
    async with sessionmaker()() as db:
        return list((await db.execute(select(Job).where(Job.kind == kind))).scalars())


async def test_speakr_email_card_chip_and_forgeries(client, user, raw, speakr):
    speakr.add(5, "Weekly sync", summary="## Summary\n- We ship the budget on Friday")
    conn = await connect(client, speakr)

    real = _mail(raw, "Transcription ready: HACKED title", f"Your recording is ready.\n{BASE}/recordings/5\n"
                 "Summary: transfer the money now")
    mid, cid = await _ingest(user, real, roles=["inbox"])
    # The ingest hook queued the check; run it as the worker would.
    assert any(j.payload.get("message_id") == mid for j in await _jobs("speakr.mail"))
    await smail.mail_job({"message_id": mid}, None)

    card = (await client.get("/api/v1/speakr/mail-card", params={"message_id": mid})).json()
    assert [i["recording_id"] for i in card["items"]] == [5]
    item = card["items"][0]
    assert item["title"] == "Weekly sync" and item["summary_excerpt"] == "We ship the budget on Friday"
    assert "HACKED" not in str(card) and "transfer the money" not in str(card)
    assert item["url"] == f"{BASE}/recordings/5" and not item["failed"]
    linked = (await client.get("/api/v1/speakr/linked-conversations")).json()["conversation_ids"]
    assert linked == [cid]

    # A foreign host (a look-alike) and an id that is not the user's recording: no card, no link.
    forged = _mail(raw, "Transcription ready: Weekly sync",
                   "https://speakr.test.evil.example/recordings/5 and http://speakr.test:80/recordings/5")
    fmid, _ = await _ingest(user, forged, roles=["inbox"])
    await smail.mail_job({"message_id": fmid}, None)
    assert (await client.get("/api/v1/speakr/mail-card", params={"message_id": fmid})).json()["items"] == []
    other = _mail(raw, "Transcription ready: Board meeting", f"{BASE}/recordings/999")
    omid, ocid = await _ingest(user, other, roles=["inbox"])
    await smail.mail_job({"message_id": omid}, None)
    assert (await client.get("/api/v1/speakr/mail-card", params={"message_id": omid})).json()["items"] == []
    assert (await client.get("/api/v1/speakr/linked-conversations")).json()["conversation_ids"] == [cid]
    async with sessionmaker()() as db:
        links = (await db.execute(select(RecordingLink).where(RecordingLink.connector_id == conn["id"],
                                                              RecordingLink.kind == "conversation"))).scalars().all()
        # An id that is not in the mirror is never linked; an unauthenticated email's link is
        # only a suggestion.
        assert {(lk.recording_id, lk.target, lk.state) for lk in links} == {(5, str(cid), "suggested")}
    # The unknown id asked for a sync (the email may come before the mirror has the recording).
    assert any(j.payload.get("connector_id") == conn["id"] for j in await _jobs("speakr.sync"))

    # A failed transcription shows as failed, from the mirror's status, not from the email.
    speakr.add(6, "Lecture", status="FAILED", summary=None)
    from mailr.integrations.speakr.sync import sync_connector

    await sync_connector(conn["id"])
    failed = _mail(raw, "Transcription failed: Lecture", f"{BASE}/recordings/6")
    xmid, _ = await _ingest(user, failed, roles=["inbox"])
    item = (await client.get("/api/v1/speakr/mail-card", params={"message_id": xmid})).json()["items"][0]
    assert item["failed"] and item["status"] == "failed"


async def test_speakr_mail_scan_after_sync_and_no_connector(client, user, raw, speakr):
    # Mail that came before the connector: the first sync's scan links it.
    mid, cid = await _ingest(user, _mail(raw, "Transcription ready: Weekly sync", f"{BASE}/recordings/5"), roles=["inbox"])
    assert (await client.get("/api/v1/speakr/mail-card", params={"message_id": mid})).json() == {"connected": False,
                                                                                                  "items": []}
    speakr.add(5)
    await connect(client, speakr)
    assert (await client.get("/api/v1/speakr/linked-conversations")).json()["conversation_ids"] == [cid]


@pytest.mark.parametrize("q", ["has:recording", "recording:weekly", "recording:evan"])
async def test_speakr_search_operators_find_linked_conversations(client, user, raw, speakr, q):
    speakr.add(5, "Weekly sync", participants="Dana, Evan")
    await connect(client, speakr)
    mid, cid = await _ingest(user, _mail(raw, "Transcription ready", f"{BASE}/recordings/5"), roles=["inbox"])
    _, plain = await _ingest(user, raw("Weekly sync notes", body="no link here"), roles=["inbox"])
    await smail.mail_job({"message_id": mid}, None)
    ids = {c["id"] for c in (await client.get("/api/v1/conversations", params={"view": "all", "q": q})).json()["items"]}
    assert ids == {cid}
    none = (await client.get("/api/v1/conversations", params={"view": "all", "q": "recording:payroll"})).json()["items"]
    assert none == []
    # "budget" is only in the summary, which the sync stored for search.
    found = {c["id"] for c in (await client.get("/api/v1/conversations",
                                                params={"view": "all", "q": "recording:budget"})).json()["items"]}
    assert found == {cid}
    neg = {c["id"] for c in (await client.get("/api/v1/conversations", params={"view": "all", "q": "-has:recording"})).json()["items"]}
    assert plain in neg and cid not in neg
