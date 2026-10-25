# ACT-SURFACES: api assistant
"""note.from_recording (notes/from_recording.py): a text note from a Speakr recording with its
title, date, people, summary and action items, linked to the recording both ways; local-only when
the recording's connector is; never transcript text; an agent token needs the recordings scope."""

import pytest
from helpers.permissions import _token
from helpers.speakr_tools import _ctx
from speakr_fake import connect, speakr  # noqa: F401

from mailr.agent.tools import run_tool
from mailr.db import sessionmaker
from mailr.notes import from_recording
from mailr.notes.models import Note

# These tests never touch the mail store: database-only users (conftest.make_db_user).
pytestmark = pytest.mark.db_user

API = "/api/v1"
SUMMARY = ("## Overview\nThe team reviewed the Q4 budget.\n\n### Action items\n- Dana: send the revised numbers\n"
           "- Evan: draft the plan\n  by Monday\n\n## Risks\n- Vendor delay")


def test_split_summary_moves_the_action_items_out():
    text, items = from_recording.split_summary(SUMMARY)
    assert items == ["Dana: send the revised numbers", "Evan: draft the plan by Monday"]
    assert "**Overview**" in text and "**Risks**" in text and "Action items" not in text and "#" not in text
    assert from_recording.split_summary("**Next steps:**\n1. Book the room") == ("", ["Book the room"])


async def test_note_from_recording_api_links_both_ways(client, user, speakr):
    # ACT note.from_recording
    speakr.add(7, "Budget review", participants="Dana, Evan", summary=SUMMARY)
    conn = await connect(client, speakr)  # local-only by default
    r = await client.post(f"{API}/notes/from-recording", json={"connector_id": conn["id"], "recording_id": 7})
    assert r.status_code == 200, r.text
    n = r.json()["note"]
    assert n["kind"] == "text" and n["title"] == "Budget review" and n["local_only"] is True
    body = n["body"]
    assert body.splitlines()[1] == "With Dana, Evan"
    assert "**Summary**" in body and "The team reviewed the Q4 budget." in body
    assert body.rstrip().endswith("**Action items**\n\n- Dana: send the revised numbers\n- Evan: draft the plan by Monday")
    # Never the transcript.
    assert "Welcome everyone" not in body and "I will draft the plan" not in body
    # Linked from the note, and the recording's Links tab finds the note.
    assert [(x["target_kind"], x["target_ref"]["recording_id"]) for x in n["links"]] == [("recording", 7)]
    linked = (await client.get(f"{API}/notes/linked", params={"target_kind": "recording",
                                                                "ref": f"{conn['id']}:7"})).json()
    assert [x["id"] for x in linked["notes"]] == [n["id"]]
    assert (await client.post(f"{API}/notes/from-recording",
                              json={"connector_id": conn["id"], "recording_id": 999})).status_code == 404


async def test_normal_recording_makes_a_normal_note_and_tokens_need_the_scope(client, user, speakr):
    speakr.add(8, "Standup", summary=None)
    conn = await connect(client, speakr, data_class="normal")
    r = await client.post(f"{API}/notes/from-recording", json={"connector_id": conn["id"], "recording_id": 8})
    assert r.status_code == 200, r.text
    assert r.json()["note"]["local_only"] is False and "No summary yet." in r.json()["note"]["body"]
    agent, _ = await _token(client, ["notes"], name="notes-only")
    async with agent:
        r = await agent.post(f"{API}/notes/from-recording", json={"connector_id": conn["id"], "recording_id": 8})
    assert r.status_code == 403


async def test_assistant_create_note_from_a_recording(client, user, speakr):
    speakr.add(9, "Design crit", summary=SUMMARY)
    conn = await connect(client, speakr)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = await run_tool(ctx, "create_note", {"recording_id": 9})
        await db.commit()
        out = res.data["results"][0]
        assert out["ok"] and out["linked_recording"] == "[[r:9]]" and out["kind"] == "text"
        with pytest.raises(Exception, match="no recording has that id"):
            await run_tool(ctx, "create_note", {"recording_id": 404, "connector_id": conn["id"]})
    async with sessionmaker()() as db:
        from sqlalchemy import select

        note = (await db.execute(select(Note).where(Note.pid == out["id"]))).scalar_one()
        assert note.title == "Design crit" and note.local_only and "Dana: send the revised numbers" in note.body
