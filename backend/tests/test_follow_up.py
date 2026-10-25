# ACT-SURFACES: api assistant
"""mail.follow_up: "remind me if nobody replies".

Covers (api): ACT mail.follow_up — set, nudge when due without a reply, a reply cancels, archive ends
the "No reply" mark, cancel. Covers (assistant): mail.follow_up through modify_conversations."""

from datetime import UTC, datetime, timedelta

from conftest import mail_date
from helpers.core_flow import _ingest

from mailr.core.actions import check_follow_up
from mailr.core.store import user_store
from mailr.db import sessionmaker
from mailr.models import Conversation, User

LATER = (datetime.now(UTC) + timedelta(days=3)).isoformat()


async def _thread(user, raw, tag):
    """Dana asks; I answer (my message is the last one)."""
    q = raw(f"Contract {tag}", frm="Dana <dana@example.org>", to=user.email, msgid=f"<q-{tag}@x.org>",
            date=mail_date(7, "09:00"))
    a = raw(f"Re: Contract {tag}", frm=f"Me <{user.email}>", to="dana@example.org", msgid=f"<a-{tag}@x.org>",
            irt=f"<q-{tag}@x.org>", date=mail_date(7))
    _, cid = await _ingest(user, q, roles=["inbox"], source="connector")
    await _ingest(user, a, roles=["sent"])
    return cid


async def _conv(cid):
    async with sessionmaker()() as db:
        return await db.get(Conversation, cid)


async def _due(user, cid):
    c = await _conv(cid)
    async with sessionmaker()() as db:
        store = await user_store(db, await db.get(User, user.id))
        nudged = await check_follow_up(db, store, cid, c.follow_up_at.isoformat())
        await db.commit()
    return nudged


async def _act(client, cid, action, **kw):
    r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": action, **kw})
    assert r.status_code == 200, r.text
    return r.json()


async def test_no_reply_brings_it_back_marked(client, user, raw):
    cid = await _thread(user, raw, "a")
    await _act(client, cid, "archive")
    out = await _act(client, cid, "follow_up", until=LATER)
    assert out["undo"]["action"] == "cancel_follow_up"
    c = await _conv(cid)
    assert c.follow_up_at is not None and c.follow_up_since is not None and c.nudged_at is None
    row = next(x for x in (await client.get("/api/v1/conversations", params={"view": "all", "q": "is:follow_up"})).json()["items"]
               if x["id"] == cid)
    assert row["follow_up_at"]
    # The folder pane shows Sent as "waiting for a reply", not as an ever-growing total.
    assert (await client.get("/api/v1/me")).json()["counts"]["waiting"] == 1

    assert await _due(user, cid)
    assert (await client.get("/api/v1/me")).json()["counts"]["waiting"] == 0
    c = await _conv(cid)
    assert c.nudged_at is not None and c.follow_up_at is None
    inbox = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"]
    assert any(x["id"] == cid and x["nudged_at"] for x in inbox)
    assert [x["id"] for x in (await client.get("/api/v1/conversations", params={"view": "all", "q": "is:nudged"})).json()["items"]] == [cid]

    await _act(client, cid, "archive")  # dealt with: the mark goes
    assert (await _conv(cid)).nudged_at is None


async def test_a_reply_cancels_and_my_own_follow_up_clears_the_mark(client, user, raw):
    cid = await _thread(user, raw, "b")
    await _act(client, cid, "follow_up", until=LATER)
    reply = raw("Re: Contract b", frm="Dana <dana@example.org>", to=user.email, msgid="<r-b@x.org>", irt="<a-b@x.org>",
                date=mail_date(8, "08:00"))
    await _ingest(user, reply, roles=["inbox"], source="connector")
    c = await _conv(cid)
    assert c.follow_up_at is None and c.nudged_at is None

    cid = await _thread(user, raw, "c")
    await _act(client, cid, "follow_up", until=LATER)
    assert await _due(user, cid)
    again = raw("Re: Contract c", frm=f"Me <{user.email}>", to="dana@example.org", msgid="<f-c@x.org>",
                irt="<a-c@x.org>", date=mail_date(10, "08:00"))
    await _ingest(user, again, roles=["sent"])
    assert (await _conv(cid)).nudged_at is None


async def test_reply_before_it_comes_due_means_no_nudge_and_cancel_works(client, user, raw):
    cid = await _thread(user, raw, "d")
    await _act(client, cid, "follow_up", until=LATER)
    await _act(client, cid, "cancel_follow_up")
    assert (await _conv(cid)).follow_up_at is None

    _, other = await _ingest(user, raw("Newsletter", frm="News <news@example.org>"), roles=["inbox"], source="connector")
    r = await client.post("/api/v1/conversations/actions", json={"ids": [other], "action": "follow_up", "until": LATER})
    assert r.status_code == 400 and "you sent" in r.text
    r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "follow_up",
                                                                 "until": "2020-01-01T00:00:00Z"})
    assert r.status_code == 400


async def test_assistant_sets_a_follow_up(client, user, raw):
    cid = await _thread(user, raw, "e")
    r = await client.post("/api/v1/agent/tools/modify_conversations",
                          json={"conversation_ids": [cid], "action": "follow_up", "until": LATER})
    assert r.status_code == 200, r.text
    assert (await _conv(cid)).follow_up_at is not None


async def test_the_due_job_nudges(client, user, raw):
    from mailr.core.tasks import followup_check

    cid = await _thread(user, raw, "f")
    await _act(client, cid, "follow_up", until=LATER)
    await followup_check({"conversation_id": cid, "at": (await _conv(cid)).follow_up_at.isoformat()}, None)
    assert (await _conv(cid)).nudged_at is not None
    # A stale job (the reminder was moved later) does nothing.
    cid = await _thread(user, raw, "g")
    await _act(client, cid, "follow_up", until=LATER)
    await followup_check({"conversation_id": cid, "at": "2026-01-01T00:00:00+00:00"}, None)
    assert (await _conv(cid)).nudged_at is None and (await _conv(cid)).follow_up_at is not None
