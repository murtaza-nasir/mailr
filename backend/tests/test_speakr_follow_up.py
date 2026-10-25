# ACT-SURFACES: api assistant
"""The follow-up email of a meeting recording (recording.follow_up): recipients
are the event's people minus me, lists and robots; the draft replies in the event's EventLink thread
when there is one; it is always a draft and never sent; the assistant mode uses mailr's routed model
with the local-only flag for a local-only recording. Needs the dev Radicale and Stalwart."""

from datetime import UTC, datetime, timedelta

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.calendar_organizer import _setup
from helpers.core_flow import _ingest
from helpers.speakr_matching import _event
from helpers.speakr_tools import _ctx
from speakr_fake import connect, speakr  # noqa: F401
from sqlalchemy import select

from mailr.agent.tools import run_tool
from mailr.core import mdmail
from mailr.db import sessionmaker
from mailr.integrations.speakr.followup import recipients
from mailr.intel import llm
from mailr.models import EventLink, Message, Outbox

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


def test_follow_up_recipients_and_markdown():
    ev = {"organizer": "boss@x.org", "organizer_name": "Boss", "attendee_list": [
        {"email": "ME@example.com"}, {"email": "evan@x.org", "name": "Evan"}, {"email": "noreply@x.org"},
        {"email": "all-staff@x.org"}, {"email": "team@x.org"}, {"email": "room-4@x.org", "cutype": "ROOM"},
        {"email": "evan@x.org"}, {"email": "mailto:dana@x.org", "name": "Dana"}]}
    assert recipients(ev, {"me@example.com"}) == [{"email": "boss@x.org", "name": "Boss"},
                                                  {"email": "evan@x.org", "name": "Evan"},
                                                  {"email": "dana@x.org", "name": "Dana"}]
    assert recipients(None, set()) == []
    html = mdmail.to_html("## Decisions\n- **Ship** on Friday\n- <script>x</script>\n\nThanks *all*")
    assert html == ("<div><strong>Decisions</strong></div><ul><li><strong>Ship</strong> on Friday</li>"
                    "<li>&lt;script&gt;x&lt;/script&gt;</li></ul><div>Thanks <em>all</em></div>")


async def test_recording_follow_up_draft_reply_in_thread_never_sent(client, user, speakr, raw, monkeypatch):
    me, _ = await _setup(client, user)
    t = (datetime.now(UTC) - timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    ev = await _event(client, "Budget review", t, attendees=[me, "evan@guest.example", "dana@guest.example",
                                                             "noreply@calendar.example", "all-staff@guest.example"])
    speakr.add(1, "Budget review", meeting_date=t, duration=3000, participants="Dana, Evan")
    conn = await connect(client, speakr)

    r = await client.post(f"/api/v1/speakr/recordings/{conn['id']}/1/follow-up", json={"mode": "summary"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["to"] == ["evan@guest.example", "dana@guest.example"] and not d["reply"]
    assert d["payload"]["subject"] == "Follow-up: Budget review" and d["event"]["uid"] == ev["uid"]
    assert "<li>Ship the budget on Friday</li>" in d["payload"]["html"]
    async with sessionmaker()() as db:
        m = await db.get(Message, d["draft_id"])
        assert m.draft and "Ship the budget on Friday" in m.body_text
        assert (await db.execute(select(Outbox).where(Outbox.user_id == user.id))).first() is None

    # The event came from an email: the follow-up replies in that thread.
    mid, cid = await _ingest(user, raw("Budget numbers", frm="Evan <evan@guest.example>", to=me), roles=["inbox"])
    async with sessionmaker()() as db:
        db.add(EventLink(user_id=user.id, conversation_id=cid, connector_id=conn["id"], kind="event", href="h",
                         uid=ev["uid"], summary="Budget review"))
        await db.commit()
    d = (await client.post(f"/api/v1/speakr/recordings/{conn['id']}/1/follow-up", json={"mode": "summary"})).json()
    assert d["reply"] and d["conversation_id"] == cid and d["payload"]["in_reply_to_message_id"] == mid
    assert d["payload"]["subject"] == "Re: Budget numbers" and d["to"] == ["evan@guest.example", "dana@guest.example"]

    # Assistant mode: mailr's model, told that the recording is local-only.
    seen = []

    async def fake_chat(db, u, messages, *, sensitive, **kw):
        seen.append(sensitive)
        assert "Ship the budget on Friday" in messages[1]["content"]
        return {"content": "<think>x</think>Thanks for today.\n\n**Next steps:**\n* **Evan:** We ship the budget on "
                           "Friday.", "_model": "local-qwen"}

    monkeypatch.setattr(llm, "chat", fake_chat)
    d = (await client.post(f"/api/v1/speakr/recordings/{conn['id']}/1/follow-up",
                           json={"mode": "assistant", "instructions": "keep it short"})).json()
    assert seen == [True] and d["source"] == "local-qwen" and "We ship the budget on Friday." in d["payload"]["html"]
    assert "think" not in d["payload"]["html"]
    # The model's Markdown is formatting in the email, not literal stars.
    assert "<li><strong>Evan:</strong> We ship the budget on Friday.</li>" in d["payload"]["html"]
    assert "<strong>Next steps:</strong>" in d["payload"]["html"] and "*" not in d["payload"]["html"]
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = await run_tool(ctx, "draft_follow_up", {"recording_id": 1, "mode": "summary"})
        assert res.data["sent"] is False and res.draft["draft_id"] == res.data["draft_id"] and res.sensitive
        assert (await db.execute(select(Outbox).where(Outbox.user_id == user.id))).first() is None


async def test_recording_follow_up_needs_a_summary_and_a_local_model(client, user, speakr, monkeypatch):
    await _setup(client, user)
    speakr.add(2, "Quiet", summary=None)
    conn = await connect(client, speakr)
    r = await client.post(f"/api/v1/speakr/recordings/{conn['id']}/2/follow-up", json={"mode": "summary"})
    assert r.status_code == 400 and "no summary" in r.text
    speakr.summaries[2] = "Notes"
    speakr.recordings[2]["has_summary"] = True
    from mailr.integrations.speakr.sync import sync_connector

    await sync_connector(conn["id"])

    async def refuse(db, u, messages, *, sensitive, **kw):
        raise llm.LocalOnlyViolation("no local endpoint")

    monkeypatch.setattr(llm, "chat", refuse)
    r = await client.post(f"/api/v1/speakr/recordings/{conn['id']}/2/follow-up", json={"mode": "assistant"})
    assert r.status_code == 409 and "local" in r.text
