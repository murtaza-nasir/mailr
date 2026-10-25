# ACT-SURFACES: api assistant
"""Write-backs to Speakr (recording.edit, recording.share_link): off until the
connector card turns them on; the user's own edits and shares are not gated; speakr.write asks agents
by default and a `tags` constraint narrows a rule to tagged recordings; speakr.share always asks, even
under an allow rule or a standing grant (the hard rule); a token needs the recordings scope."""

import pytest
from helpers.speakr_tools import _ctx
from helpers.speakr_writeback import _switch
from speakr_fake import BASE, connect, speakr  # noqa: F401

from mailr.agent.tools import ToolError, run_tool
from mailr.core import permissions
from mailr.db import sessionmaker
from mailr.models import PendingAction, PermissionRule, User


async def test_writeback_switches_user_edits_and_share(client, user, speakr):
    speakr.add(1, "Weekly sync", notes="Agenda: budget")
    conn = await connect(client, speakr)
    cid = conn["id"]
    url = f"/api/v1/speakr/recordings/{cid}/1"
    r = await client.patch(url, json={"title": "Budget sync"})
    assert r.status_code == 403 and "Turn them on" in r.json()["detail"]
    r = await client.post(f"{url}/share")
    assert r.status_code == 403 and "Share links are off" in r.json()["detail"]
    assert not speakr.writes
    assert not conn["config"].get("write_back") and not conn["config"].get("share_links")

    await _switch(client, cid, write_back=True)
    r = await client.patch(url, json={"title": "Budget sync", "participants": ["Dana", "Evan", "Zoe"],
                                      "append_notes": "Follow-up sent 1 Oct 2026: https://mail.example/c1"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["previous"] == {"title": "Weekly sync", "participants": ["Dana", "Evan"], "tags": ["Work"]} and d["notes_appended"]
    assert d["recording"]["title"] == "Budget sync"
    assert speakr.recordings[1]["title"] == "Budget sync" and speakr.recordings[1]["participants"] == "Dana, Evan, Zoe"
    assert speakr.notes[1] == "Agenda: budget\n\nFollow-up sent 1 Oct 2026: https://mail.example/c1"
    # Undo writes the previous values back.
    await client.patch(url, json={"title": d["previous"]["title"], "participants": d["previous"]["participants"]})
    assert speakr.recordings[1]["title"] == "Weekly sync"
    assert (await client.patch(url, json={})).status_code == 400

    await _switch(client, cid, share_links=True)
    r = await client.post(f"{url}/share", json={"share_notes": False})
    assert r.status_code == 200 and r.json()["share_url"] == f"{BASE}/share/1-abcdef" and r.json()["public"]
    assert ("POST", "/api/recording/1/share", {"share_summary": True, "share_notes": False}) in speakr.writes
    assert r.json()["existing"] is False and (await client.post(f"{url}/share")).json()["existing"] is True


async def _approve_once(user_id, pid):
    async with sessionmaker()() as db:
        await permissions.decide(db, await db.get(User, user_id), pid, "approve", "always")
        await db.commit()
    await permissions.execute_job({"pending_id": pid}, None)
    async with sessionmaker()() as db:
        return await db.get(PendingAction, pid)


async def test_writeback_permission_classes_tags_and_share_hard_rule(client, user, speakr):
    speakr.add(1, "Advising: Dana", tags=["Advising"])
    speakr.add(2, "Weekly sync", tags=["Work"])
    conn = await connect(client, speakr)
    async with sessionmaker()() as db:
        with pytest.raises(ToolError, match="Turn them on"):
            await run_tool(await _ctx(db, user.id), "update_recording", {"recording_id": 1, "title": "x"})
    await _switch(client, conn["id"], write_back=True, share_links=True)
    async with sessionmaker()() as db:
        res = await run_tool(await _ctx(db, user.id), "update_recording", {"recording_id": 2, "title": "Renamed"})
        assert res.data["status"] == "pending_approval" and res.pending["action"] == "speakr.write"
        # A rule that allows write-backs on Advising recordings only.
        db.add(PermissionRule(user_id=user.id, subject="assistant", action="speakr.write", effect="allow",
                              constraints={"tags": ["advising"]}))
        await db.commit()
        res = await run_tool(await _ctx(db, user.id), "update_recording",
                             {"recording_id": 1, "append_notes": "Checked by the assistant"})
        assert res.data == {"ok": True, "recording_id": 1, "notes_appended": True}
        res = await run_tool(await _ctx(db, user.id), "update_recording", {"recording_id": 2, "title": "Renamed"})
        assert res.data["status"] == "pending_approval"

        # Sharing asks even with an allow rule for it.
        db.add(PermissionRule(user_id=user.id, subject="assistant", action="speakr.share", effect="allow", constraints={}))
        await db.commit()
        res = await run_tool(await _ctx(db, user.id), "share_recording", {"recording_id": 1})
        assert res.data["status"] == "pending_approval" and res.pending["action"] == "speakr.share"
        pid = res.data["approval_id"]
        pa = await db.get(PendingAction, pid)
        assert pa.preview["reason"] == "hard rule: a public share link" and pa.preview["danger"]
    assert not speakr.shares
    done = await _approve_once(user.id, pid)
    assert done.status == "executed" and done.result["share_url"].endswith("/share/1-abcdef")
    # An "always" grant does not cover the next share either.
    async with sessionmaker()() as db:
        res = await run_tool(await _ctx(db, user.id), "share_recording", {"recording_id": 2})
        assert res.data["status"] == "pending_approval"

    # A token: no recordings scope → 403; with it, edits and shares wait for approval (202).
    from httpx import ASGITransport, AsyncClient

    from mailr.main import create_app

    plain = (await client.post("/api/v1/tokens", json={"name": "reader", "scopes": ["read"]})).json()
    rec = (await client.post("/api/v1/tokens", json={"name": "rec", "scopes": ["read", "recordings"],
                                                     "local_only": True})).json()
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as t:
        url = f"/api/v1/speakr/recordings/{conn['id']}/2"
        r = await t.patch(url, json={"title": "x"}, headers={"Authorization": f"Bearer {plain['token']}"})
        assert r.status_code == 403
        r = await t.patch(url, json={"title": "x"}, headers={"Authorization": f"Bearer {rec['token']}"})
        assert r.status_code == 202 and r.json()["approval"]["action"] == "speakr.write"
        r = await t.post(f"{url}/share", headers={"Authorization": f"Bearer {rec['token']}"})
        assert r.status_code == 202 and r.json()["approval"]["action"] == "speakr.share"
    assert speakr.recordings[2]["title"] == "Weekly sync"
