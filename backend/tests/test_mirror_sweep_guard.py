"""Mirror removals are guarded: a SEARCH answer that is
empty, short of the folder's EXISTS count, or that would remove a large share of the folder never
sends mail to Trash. Against the upstream Stalwart (deploy/dev/docker-compose.yml `upstream`)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from helpers.connectors import create_connector, imap, make_upstream_account, msg, run_sync
from sqlalchemy import text

from mailr.connectors import imap as imapmod
from mailr.connectors.imap import ImapSession, sweep_verdict
from mailr.db import sessionmaker
from mailr.models import Connector


async def _inbox_count(user_id: int) -> int:
    """Messages still in the Inbox (not moved to Trash by finalize_orphans)."""
    async with sessionmaker()() as db:
        return (await db.execute(text(
            "SELECT count(*) FROM messages m JOIN labels l ON l.id = ANY(m.label_ids) "
            "WHERE m.user_id = :u AND l.role = 'inbox' AND NOT m.deleted"), {"u": user_id})).scalar_one()


async def _mirror_with(client, n: int) -> tuple[dict, str, str]:
    email, pw = make_upstream_account()
    c = imap(email, pw)
    for i in range(n):
        c.append("INBOX", msg(f"Keep {i}", msgid=f"<keep-{i}-{uuid.uuid4().hex}@x.org>"))
    c.logout()
    conn = await create_connector(client, email, pw, mode="mirror")
    await run_sync(conn["id"])
    return conn, email, pw


def test_sweep_verdict_rules():
    assert sweep_verdict(known=30, gone=1, exists=29, listed=29) is None
    assert sweep_verdict(known=30, gone=30, exists=30, listed=0) == "empty_listing"
    assert sweep_verdict(known=1000, gone=600, exists=1000, listed=400) == "short_listing"
    assert sweep_verdict(known=1000, gone=201, exists=799, listed=799) == "large"
    assert sweep_verdict(known=30, gone=25, exists=5, listed=5) == "large"
    # A small folder may lose most of its mail (the user cleaned it up): below SWEEP_MIN.
    assert sweep_verdict(known=3, gone=3, exists=0, listed=0) is None


async def test_an_empty_search_answer_does_not_trash_the_folder(client, user, monkeypatch):
    conn, _email, _pw = await _mirror_with(client, 30)
    assert await _inbox_count(user.id) == 30
    orig = ImapSession.call

    async def throttled(self, fn, *a, **kw):
        # The provider throttles: every SEARCH comes back empty, EXISTS still says 30.
        if getattr(fn, "__name__", "") == "search":
            return []
        return await orig(self, fn, *a, **kw)

    monkeypatch.setattr(ImapSession, "call", throttled)
    for _ in range(3):  # vanished marks orphans; finalize_orphans trashes them a pass later
        await run_sync(conn["id"])
    assert await _inbox_count(user.id) == 30
    r = (await client.get(f"/api/v1/connectors/{conn['id']}")).json()
    assert r["health"]["code"] == "account.deletions_held" and r["health"]["state"] == "stale"
    assert r["sync_warnings"][0]["folder"] == "INBOX" and r["sync_warnings"][0]["count"] == 30
    assert r["sync_warnings"][0]["reason"] == "empty_listing"

    # The provider answers properly again: the warning goes, nothing was lost.
    monkeypatch.setattr(ImapSession, "call", orig)
    await run_sync(conn["id"])
    r = (await client.get(f"/api/v1/connectors/{conn['id']}")).json()
    assert r["sync_warnings"] == [] and r["health"]["code"] != "account.deletions_held"
    assert await _inbox_count(user.id) == 30


async def test_a_short_search_answer_is_read_again_and_held(client, user, monkeypatch):
    conn, _email, _pw = await _mirror_with(client, 30)
    orig = ImapSession.call
    calls = {"search": 0}

    async def partial(self, fn, *a, **kw):
        got = await orig(self, fn, *a, **kw)
        if getattr(fn, "__name__", "") == "search" and len(got) > 5:
            calls["search"] += 1
            return sorted(got)[:5]  # a partial answer: 5 of the 30 UIDs
        return got

    monkeypatch.setattr(ImapSession, "call", partial)
    for _ in range(3):
        await run_sync(conn["id"])
    assert await _inbox_count(user.id) == 30
    async with sessionmaker()() as db:
        st = (await db.get(Connector, conn["id"])).state
    assert st["sync_warnings"]["INBOX"]["reason"] == "short_listing"


async def test_small_upstream_deletions_still_apply(client, user):
    conn, email, pw = await _mirror_with(client, 30)
    c = imap(email, pw)
    c.select_folder("INBOX")
    uids = sorted(c.search(["ALL"]))
    c.delete_messages(uids[:2])
    c.expunge()
    c.logout()
    await run_sync(conn["id"])
    await run_sync(conn["id"])
    assert await _inbox_count(user.id) == 28
    r = (await client.get(f"/api/v1/connectors/{conn['id']}")).json()
    assert r["sync_warnings"] == []


async def test_a_large_real_deletion_is_held_then_applied_once_passes_agree(client, user, monkeypatch):
    conn, email, pw = await _mirror_with(client, 30)
    c = imap(email, pw)
    c.select_folder("INBOX")
    uids = sorted(c.search(["ALL"]))
    c.delete_messages(uids[:25])
    c.expunge()
    c.logout()
    await run_sync(conn["id"])
    await run_sync(conn["id"])
    assert await _inbox_count(user.id) == 30  # held: 25 of 30 is more than SWEEP_SHARE
    r = (await client.get(f"/api/v1/connectors/{conn['id']}")).json()
    assert r["sync_warnings"][0]["reason"] == "large" and r["sync_warnings"][0]["count"] == 25

    # Every pass agrees and the removal is old enough: it applies (no behaviour lost).
    monkeypatch.setattr(imapmod, "SWEEP_CONFIRM_AGE", timedelta(0))
    await run_sync(conn["id"])  # third pass that saw the same 25: applied (orphaned)
    await run_sync(conn["id"])  # the pass after: orphans go to Trash
    assert await _inbox_count(user.id) == 5
    r = (await client.get(f"/api/v1/connectors/{conn['id']}")).json()
    assert r["sync_warnings"] == []
