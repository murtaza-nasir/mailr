# ACT-SURFACES: api assistant web.pane
"""Action items from a recording into Tasks (recording.to_tasks): proposals come
from mailr's routed model as structured output (local-only flag set for a local-only recording),
items owned by others start unticked, ticked items become tasks linked to the recording with the
deep link in their notes (the task pane reads them back: recording.view), and a second proposal marks
the ones already added. Needs the dev Radicale."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.calendar import _connect
from helpers.speakr_tools import _ctx
from speakr_fake import connect, speakr  # noqa: F401

from mailr.agent.tools import run_tool
from mailr.db import sessionmaker
from mailr.integrations.speakr.action_items import parse_items
from mailr.intel import llm

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")

ANSWER = {"items": [{"text": "Send the budget to finance", "owner": "me", "due": "2026-10-09", "t": 754},
                    {"text": "Draft the plan", "owner": "Evan", "due": None, "t": 790},
                    {"text": "send the budget to finance", "owner": "me", "due": None, "t": None}]}


def test_parse_action_items():
    got = parse_items("<think>…</think>```json\n" + json.dumps(ANSWER) + "\n```", {"test", "test user"})
    assert got == [{"text": "Send the budget to finance", "owner": "me", "mine": True, "due": "2026-10-09", "t": 754.0},
                   {"text": "Draft the plan", "owner": "Evan", "mine": False, "due": None, "t": 790.0}]
    assert parse_items("not json", set()) == []
    assert parse_items('{"items": [{"text": "Call Test", "owner": "Test", "due": "soon", "t": "x"}]}', {"test"}) == \
        [{"text": "Call Test", "owner": "me", "mine": True, "due": None, "t": None}]


async def test_recording_to_tasks_propose_create_and_mark_duplicates(client, user, speakr, monkeypatch):
    await _connect(client)
    speakr.add(1, "Budget review", meeting_date=(datetime.now(UTC) - timedelta(days=1)).replace(microsecond=0))
    conn = await connect(client, speakr)
    seen = []

    async def fake_chat(db, u, messages, *, sensitive, response_format=None, **kw):
        seen.append((sensitive, response_format["type"]))
        assert "[12:34] Dana: We agreed to ship the budget on Friday." in messages[1]["content"]
        return {"content": json.dumps(ANSWER), "_model": "local-qwen"}

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = await client.post(f"/api/v1/speakr/recordings/{conn['id']}/1/action-items")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert seen == [(True, "json_schema")]
    assert [(i["text"], i["checked"], i["already_added"]) for i in items] == [
        ("Send the budget to finance", True, False), ("Draft the plan", False, False)]
    assert items[0]["url"] == "https://speakr.test/recordings/1?t=754"

    r = await client.post(f"/api/v1/speakr/recordings/{conn['id']}/1/tasks",
                          json={"items": [{"text": items[0]["text"], "due": items[0]["due"], "t": items[0]["t"]}],
                                "event_uid": "ev-1@example", "occurrence": "2026-10-01T14:00:00+00:00"})
    assert r.status_code == 200, r.text
    created = r.json()["created"]
    assert len(created) == 1 and created[0]["due"] == "2026-10-09"
    task = (await client.get(f"/api/v1/tasks/{created[0]['uid']}")).json()
    assert task["summary"] == "Send the budget to finance"
    assert "https://speakr.test/recordings/1?t=754" in task["notes"] and "12:34" in task["notes"]
    assert "/calendar/event/ev-1%40example/" in task["notes"]
    rec = (await client.get(f"/api/v1/speakr/recordings/{conn['id']}/1", params={"include": "links"})).json()
    assert [(lk["kind"], lk["target"], lk["state"], lk["title"]) for lk in rec["links"]] == \
        [("task", created[0]["uid"], "confirmed", "Send the budget to finance")]

    # recording.view in the task pane: the task's recording and the moment its notes name.
    fr = (await client.get("/api/v1/speakr/tasks/recordings", params={"uid": created[0]["uid"]})).json()
    assert fr["connected"] and [(x["recording_id"], x["title"], x["t"]) for x in fr["items"]] == [(1, "Budget review", 754)]
    plain = (await client.post("/api/v1/tasks", json={"summary": "Unrelated"})).json()
    assert (await client.get("/api/v1/speakr/tasks/recordings", params={"uid": plain["uid"]})).json()["items"] == []

    # Proposing again marks the item that is already a task.
    items = (await client.post(f"/api/v1/speakr/recordings/{conn['id']}/1/action-items")).json()["items"]
    assert [(i["already_added"], i["checked"]) for i in items] == [(True, False), (False, False)]
    async with sessionmaker()() as db:
        res = await run_tool(await _ctx(db, user.id), "propose_action_items", {"recording_id": 1})
        assert res.data["items"]["untrusted"] and res.sensitive and len(res.data["items"]["content"]) == 2
