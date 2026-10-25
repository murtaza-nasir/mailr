"""Mirror accounts through a provider outage: a local
change keeps being written back for days, stays in mailr meanwhile and shows as "not synced yet";
a message the server did not return is asked for again later instead of being skipped for good.
Against the upstream Stalwart (deploy/dev/docker-compose.yml `upstream`)."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

from helpers.connectors import (
    create_connector,
    imap,
    make_upstream_account,
    msg,
    msg_count,
    run_sync,
)
from sqlalchemy import select, text

from mailr.connectors import imap as imapmod
from mailr.connectors.imap import ImapConnector, ImapSession
from mailr.core import jobs
from mailr.db import session_scope, sessionmaker
from mailr.models import Connector, Job, Message


async def _attempt(jid: int) -> Job:
    """Claim this one job (as _claim does) and run it."""
    async with session_scope() as db:
        r = (await db.execute(text(
            "UPDATE jobs SET status='running', locked_at=now(), attempts=attempts+1, run_at=now() WHERE id=:i "
            "RETURNING id, kind, user_id, payload, attempts, max_attempts"), {"i": jid})).first()
    await jobs.run_one(Job(id=r.id, kind=r.kind, user_id=r.user_id, payload=r.payload, attempts=r.attempts,
                           max_attempts=r.max_attempts))
    async with sessionmaker()() as db:
        return await db.get(Job, jid)


async def test_a_write_back_survives_an_outage_and_the_local_change_stays(client, user, monkeypatch):
    import mailr.connectors.tasks  # noqa: F401  registers the handler

    email, pw = make_upstream_account()
    c = imap(email, pw)
    c.append("INBOX", msg("Outage", msgid=f"<o-{uuid.uuid4().hex}@x.org>"))
    c.logout()
    conn = await create_connector(client, email, pw, mode="mirror")
    # A server without CONDSTORE: every pass reads the flags of recent mail again.
    real_has = ImapSession.has
    monkeypatch.setattr(ImapSession, "has", lambda self, cap: False if cap.upper() == "CONDSTORE" else real_has(self, cap))
    await run_sync(conn["id"])
    cid = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"][0]["id"]
    assert (await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "read"})).status_code == 200
    async with sessionmaker()() as db:
        jid = (await db.execute(select(Job.id).where(Job.kind == "connector.write_back", Job.user_id == user.id,
                                                     Job.status == "queued"))).scalar_one()

    async def down(self, changes):
        raise ConnectionRefusedError("the provider is down")

    monkeypatch.setattr(ImapConnector, "write_back", down)
    for _ in range(6):  # the old limit was 5 attempts (about 7.5 minutes)
        j = await _attempt(jid)
    assert j.status == "queued" and j.attempts == 6

    # Two passes read the provider's (unread) state: the local change stays.
    await run_sync(conn["id"])
    await run_sync(conn["id"])
    async with sessionmaker()() as db:
        seen = (await db.execute(select(Message.seen).where(Message.user_id == user.id, Message.deleted.is_(False),
                                                            Message.draft.is_(False)))).scalar_one()
    assert seen is True
    r = (await client.get(f"/api/v1/connectors/{conn['id']}")).json()
    assert r["unsynced_changes"] == 1 and r["health"]["code"] == "account.changes_not_synced"

    # The provider is back: the change goes out and the account is in step again.
    monkeypatch.undo()
    j = await _attempt(jid)
    assert j.status == "done"
    c = imap(email, pw)
    c.select_folder("INBOX", readonly=True)
    uids = c.search(["ALL"])
    assert b"\\Seen" in c.get_flags(uids)[uids[0]]
    c.logout()
    r = (await client.get(f"/api/v1/connectors/{conn['id']}")).json()
    assert r["unsynced_changes"] == 0 and r["health"]["code"] != "account.changes_not_synced"


async def test_a_message_the_server_did_not_return_is_fetched_later(client, user, monkeypatch):
    email, pw = make_upstream_account()
    c = imap(email, pw)
    for i in range(3):
        c.append("INBOX", msg(f"Fetch {i}", msgid=f"<f-{i}-{uuid.uuid4().hex}@x.org>"))
    c.select_folder("INBOX", readonly=True)
    hidden = max(c.search(["ALL"]))
    c.logout()
    conn = await create_connector(client, email, pw, mode="mirror")
    real = ImapSession.call

    async def throttled(self, fn, *a, **kw):
        got = await real(self, fn, *a, **kw)
        if getattr(fn, "__name__", "") == "fetch" and len(a) > 1 and b"BODY.PEEK[]" in list(a[1]):
            got = {u: d for u, d in got.items() if u != hidden}
        return got

    monkeypatch.setattr(ImapSession, "call", throttled)
    monkeypatch.setattr(imapmod, "MISSING_FIRST_PAUSE", timedelta(0), raising=False)
    for _ in range(5):  # more passes than POISON_ATTEMPTS
        await run_sync(conn["id"])
    assert await msg_count(user.id) == 2
    monkeypatch.setattr(ImapSession, "call", real)
    await run_sync(conn["id"])
    assert await msg_count(user.id) == 3
    async with sessionmaker()() as db:
        fs = (await db.get(Connector, conn["id"])).state["folders"]["INBOX"]
    assert str(hidden) not in (fs.get("skipped") or {}) and str(hidden) not in (fs.get("failed") or {})


async def test_a_message_skipped_by_an_older_version_is_tried_again(client, user, monkeypatch):
    email, pw = make_upstream_account()
    c = imap(email, pw)
    for i in range(2):
        c.append("INBOX", msg(f"Old skip {i}", msgid=f"<s-{i}-{uuid.uuid4().hex}@x.org>"))
    c.select_folder("INBOX", readonly=True)
    hidden = max(c.search(["ALL"]))
    c.logout()
    conn = await create_connector(client, email, pw, mode="mirror")
    real = ImapSession.call

    async def throttled(self, fn, *a, **kw):
        got = await real(self, fn, *a, **kw)
        if getattr(fn, "__name__", "") == "fetch" and len(a) > 1 and b"BODY.PEEK[]" in list(a[1]):
            got = {u: d for u, d in got.items() if u != hidden}
        return got

    monkeypatch.setattr(ImapSession, "call", throttled)
    await run_sync(conn["id"])
    monkeypatch.setattr(ImapSession, "call", real)
    assert await msg_count(user.id) == 1
    # The state an older version left: dropped after three passes without an answer.
    async with session_scope() as db:
        row = await db.get(Connector, conn["id"])
        st = json.loads(json.dumps(row.state))
        st["folders"]["INBOX"]["skipped"] = {str(hidden): "not returned by the server"}
        st["folders"]["INBOX"]["failed"] = {}
        row.state = st
    await run_sync(conn["id"])
    assert await msg_count(user.id) == 2


async def test_a_write_back_that_times_out_uses_up_an_attempt(client, user, monkeypatch):
    """A hanging server used to answer Retry, which never used an attempt: the job looped every two
    minutes forever. A timeout now counts, so the backoff grows and the job ends."""
    import asyncio

    from mailr.connectors import tasks as ctasks

    email, pw = make_upstream_account()
    c = imap(email, pw)
    c.append("INBOX", msg("Hang", msgid=f"<h-{uuid.uuid4().hex}@x.org>"))
    c.logout()
    conn = await create_connector(client, email, pw, mode="mirror")
    await run_sync(conn["id"])
    cid = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"][0]["id"]
    assert (await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "star"})).status_code == 200
    async with sessionmaker()() as db:
        jid = (await db.execute(select(Job.id).where(Job.kind == "connector.write_back", Job.user_id == user.id,
                                                     Job.status == "queued"))).scalar_one()

    async def hang(self, changes):
        await asyncio.sleep(30)

    monkeypatch.setattr(ImapConnector, "write_back", hang)
    monkeypatch.setattr(ctasks, "NETWORK_TIMEOUT_S", 0.05)
    j = await _attempt(jid)
    assert j.status == "queued" and j.attempts == 1 and "did not answer" in j.error
