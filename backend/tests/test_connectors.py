"""Connector tests against a second Stalwart that plays the upstream provider
(deploy/dev/docker-compose.yml service `upstream`: JMAP :18091, IMAP :12143)."""

from __future__ import annotations

import asyncio
import mailbox
import uuid
from datetime import UTC, datetime, timedelta

from conftest import mail_date
from helpers.connectors import (  # noqa: F401
    IMAP_HOST,
    IMAP_PORT,
    MGMT,
    UP_JMAP,
    UP_SVC,
    _jmap,
    create_connector,
    imap,
    make_upstream_account,
    msg,
    msg_count,
    run_jobs,
    run_sync,
)
from sqlalchemy import text

from mailr.connectors.gmail import map_gmail_labels
from mailr.connectors.imap import folder_target
from mailr.connectors.mbox import imports_root, takeout_labels
from mailr.db import session_scope, sessionmaker
from mailr.models import Connector, Outbox

# ---------------- upstream helpers ----------------


# ---------------- pure unit tests ----------------


def test_folder_mapping():
    assert folder_target("INBOX", (), "/") == ("role", "inbox")
    assert folder_target("Sent Items", (b"\\Sent",), "/") == ("role", "sent")
    assert folder_target("Junk Mail", (), "/") == ("role", "junk")
    assert folder_target("INBOX.Clients.Acme", (), ".") == ("label", "Clients/Acme")
    assert folder_target("Projects/2026", (), "/") == ("label", "Projects/2026")
    assert folder_target("[Gmail]", (b"\\Noselect",), "/") is None


def test_gmail_label_mapping():
    m = map_gmail_labels(["\\Inbox", "\\Important", "\\Starred", "Job Applications", "Research/2026"])
    assert m["roles"] == ["inbox"] and m["keywords"] == {"important": True, "flagged": True}
    assert m["label_names"] == ["Job Applications", "Research/2026"]
    assert map_gmail_labels(["\\Draft"])["draft"] is True


def test_takeout_labels():
    t = takeout_labels("Inbox,Unread,Important,Category Promotions,Hotels")
    assert t["roles"] == ["inbox"] and t["label_names"] == ["Hotels"] and t["category"] == "promotions"
    assert t["keywords"]["seen"] is False and t["keywords"]["important"] is True
    assert takeout_labels("Archived,Opened")["roles"] == ["archive"]


async def test_discover_purelymail(monkeypatch):
    import mailr.connectors.discover as d

    async def fake_mx(domain):
        return ["mailserver.purelymail.com"]

    monkeypatch.setattr(d, "_mx", fake_mx)
    r = await d.discover("me@hale.example")
    assert r["preset"] == "purelymail" and r["source"] == "mx"
    assert r["imap"]["host"] == "imap.purelymail.com" and r["imap"]["username"] == "me@hale.example"
    r = await d.discover("x@fastmail.com")
    assert r["preset"] == "fastmail" and r["source"] == "preset"


async def test_discover_has_a_deadline(monkeypatch):
    """A lookup whose network steps hang still answers within the deadline, with the usual guess
    (the admin's dialog waited 30 s for one, 13 Oct 2026)."""
    import time

    import mailr.connectors.discover as d

    async def stuck_mx(domain):
        await asyncio.sleep(60)
        return []

    monkeypatch.setattr(d, "_mx", stuck_mx)
    monkeypatch.setattr(d, "DEADLINE", 0.3)
    t = time.monotonic()
    r = await d.discover("desk@northwind.example")
    assert time.monotonic() - t < 5
    assert r["source"] is None and r["imap"]["host"] == "imap.northwind.example"
    assert r["smtp"] == {"host": "smtp.northwind.example", "port": 465, "tls": "ssl", "username": "desk@northwind.example"}

# ---------------- store mode against the upstream server ----------------


async def test_store_mode_sync_dedupe_uidvalidity_retention(client, user):
    email, pw = make_upstream_account()
    c = imap(email, pw)
    c.create_folder("Projects")
    c.append("INBOX", msg("Budget", msgid="<s1@x.org>", body="numbers"), flags=[b"\\Seen"])
    c.append("INBOX", msg("Re: Budget", frm="Carol <carol@example.org>", msgid="<s2@x.org>", irt="<s1@x.org>",
                          date=mail_date(0, "12:00")), flags=[b"\\Flagged"])
    c.append("Projects", msg("Kickoff", msgid="<s3@x.org>"))
    c.append("Junk Mail", msg("You won", frm="spam@spam.example"))
    c.append("Sent Items", msg("Hi Bob", frm=email, to="bob@example.org"), flags=[b"\\Seen"])
    c.logout()

    conn = await create_connector(client, email, pw)
    totals = await run_sync(conn["id"])
    assert totals["new"] == 5

    labels = {lb["name"]: lb for lb in (await client.get("/api/v1/labels")).json()}
    assert "Projects" in labels
    inbox = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"]
    assert len(inbox) == 1 and inbox[0]["message_count"] == 2
    assert inbox[0]["unread_count"] == 1 and inbox[0]["starred"]
    proj = (await client.get("/api/v1/conversations", params={"view": f"label:{labels['Projects']['id']}"})).json()
    assert [x["subject"] for x in proj["items"]] == ["Kickoff"]
    assert (await client.get("/api/v1/conversations", params={"view": "spam"})).json()["total"] == 1
    assert (await client.get("/api/v1/conversations", params={"view": "sent"})).json()["total"] == 1

    # A second pass finds nothing new and stores nothing twice.
    assert (await run_sync(conn["id"]))["new"] == 0
    assert await msg_count(user.id) == 5

    # New upstream mail is picked up.
    c = imap(email, pw)
    c.append("INBOX", msg("Fresh", msgid="<s4@x.org>"))
    c.logout()
    assert (await run_sync(conn["id"]))["new"] == 1
    assert await msg_count(user.id) == 6

    # UIDVALIDITY change: the folder is recreated with the same message. No duplicate,
    # and stale refs are dropped.
    c = imap(email, pw)
    c.delete_folder("Projects")
    c.create_folder("Projects")
    c.append("Projects", msg("Kickoff", msgid="<s3@x.org>"))
    c.logout()
    await run_sync(conn["id"])
    assert await msg_count(user.id) == 6

    # Retention: nothing is deleted until deletion is armed and the mail is exported.
    from mailr.connectors.tasks import retention
    from mailr.core.tasks import export_user

    async with session_scope() as db:
        row = await db.get(Connector, conn["id"])
        row.retention_days = 0
    await retention({"connector_id": conn["id"]}, None)
    c = imap(email, pw)
    c.select_folder("INBOX")
    assert len(c.search(["ALL"])) == 3
    c.logout()

    r = await client.post(f"/api/v1/connectors/{conn['id']}/arm-deletion", json={"confirm": conn["name"]})
    assert r.status_code == 409  # no export yet
    await export_user({"user_id": user.id}, None)
    r = await client.post(f"/api/v1/connectors/{conn['id']}/arm-deletion", json={"confirm": "wrong"})
    assert r.status_code == 400
    r = await client.post(f"/api/v1/connectors/{conn['id']}/arm-deletion", json={"confirm": conn["name"]})
    assert r.status_code == 200 and r.json()["delete_upstream"]
    await retention({"connector_id": conn["id"]}, None)
    c = imap(email, pw)
    c.select_folder("INBOX")
    assert c.search(["ALL"]) == []
    c.select_folder("Projects")
    assert c.search(["ALL"]) == []
    c.logout()
    async with sessionmaker()() as db:
        left = (await db.execute(text(
            "SELECT count(*) FROM messages WHERE connector_id=:c AND upstream_deleted_at IS NULL"),
            {"c": conn["id"]})).scalar_one()
    assert left == 0
    # Mail is still in mailr.
    assert await msg_count(user.id) == 6
    body = (await client.get("/api/v1/conversations", params={"view": "all", "q": "numbers"})).json()
    assert body["total"] == 1


# ---------------- mirror mode ----------------


async def test_mirror_write_back_and_remote_changes(client, user):
    email, pw = make_upstream_account()
    c = imap(email, pw)
    c.append("INBOX", msg("Mirror me", msgid=f"<m-{uuid.uuid4().hex}@x.org>"))
    c.logout()
    conn = await create_connector(client, email, pw, mode="mirror")
    await run_sync(conn["id"])
    inbox = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"]
    cid = inbox[0]["id"]

    lb = (await client.post("/api/v1/labels", json={"name": "Clients"})).json()
    assert (await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "read"})).status_code == 200
    r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "add_labels", "label_ids": [lb["id"]]})
    assert r.status_code == 200
    assert await run_jobs("connector.write_back", user.id) == 2

    c = imap(email, pw)
    c.select_folder("INBOX", readonly=True)
    uids = c.search(["ALL"])
    assert b"\\Seen" in c.get_flags(uids)[uids[0]]
    assert "Clients" in [f[2] for f in c.list_folders()]
    c.select_folder("Clients", readonly=True)
    assert len(c.search(["ALL"])) == 1
    c.logout()

    # Archive: the inbox copy is removed upstream while the Clients copy stays.
    await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "archive"})
    await run_jobs("connector.write_back", user.id)
    c = imap(email, pw)
    c.select_folder("INBOX", readonly=True)
    assert c.search(["ALL"]) == []

    # Upstream flag change flows back into mailr.
    c.select_folder("Clients")
    c.add_flags(c.search(["ALL"]), [b"\\Flagged"])
    c.logout()
    await run_sync(conn["id"])
    conv = (await client.get(f"/api/v1/conversations/{cid}")).json()
    assert conv["conversation"]["starred"] is True
    assert await msg_count(user.id) == 1


# ---------------- sending ----------------


async def test_send_through_smtp_sink(client, user):
    from aiosmtpd.controller import Controller

    got = []

    class Sink:
        async def handle_DATA(self, server, session, envelope):
            got.append(envelope)
            return "250 OK queued as test"

    import socket

    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    ctl = Controller(Sink(), hostname="127.0.0.1", port=port)
    ctl.start()
    try:
        email, pw = make_upstream_account()
        await create_connector(client, email, pw, smtp={"host": "127.0.0.1", "port": port, "tls": "none", "auth": False})
        ident = (await client.get("/api/v1/identities")).json()
        assert [i["email"] for i in ident] == [email]
        r = await client.post("/api/v1/send", json={"payload": {
            "identity_id": ident[0]["id"], "to": [{"email": "bob@example.org", "name": "Bob"}],
            "bcc": [{"email": "hidden@example.org", "name": ""}], "subject": "Hello Bob", "html": "<p>Hi <b>Bob</b></p>"}})
        assert r.status_code == 200, r.text
        ob = r.json()["outbox"]
        assert ob["status"] == "queued"
        # Undo window: cancel works before it goes out.
        from mailr.core.sendgate import send_job

        async with session_scope() as db:
            row = await db.get(Outbox, ob["id"])
            row.send_at = datetime.now(UTC) - timedelta(seconds=1)
        await send_job({"outbox_id": ob["id"]}, None)
        await asyncio.sleep(0.2)
        assert len(got) == 1
        assert set(got[0].rcpt_tos) == {"bob@example.org", "hidden@example.org"}
        assert b"hidden@example.org" not in got[0].content  # Bcc stays out of the headers
        async with sessionmaker()() as db:
            row = await db.get(Outbox, ob["id"])
            assert row.status == "sent", row.error
        # The sent copy is stored by its own (retried) job.
        await run_jobs("outbox.store_sent", user.id)
        sent = (await client.get("/api/v1/conversations", params={"view": "sent"})).json()
        assert [x["subject"] for x in sent["items"]] == ["Hello Bob"]
    finally:
        ctl.stop()


# ---------------- file import ----------------


async def test_mbox_takeout_import(client, user):
    root = imports_root(user.id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "takeout.mbox"
    box = mailbox.mbox(str(path))
    m1 = mailbox.mboxMessage(msg("Hotel booking", msgid="<t1@x.org>"))
    m1["X-Gmail-Labels"] = "Inbox,Unread,Hotels,Category Updates"
    m2 = mailbox.mboxMessage(msg("Old thing", msgid="<t2@x.org>"))
    m2["X-Gmail-Labels"] = "Archived,Opened,Job Applications"
    box.add(m1)
    box.add(m2)
    box.flush()
    box.close()
    r = await client.post("/api/v1/connectors", json={"kind": "file", "name": "Takeout", "config": {"path": path.name}})
    assert r.status_code == 200, r.text
    await run_sync(r.json()["id"])
    labels = {lb["name"]: lb for lb in (await client.get("/api/v1/labels")).json()}
    assert {"Hotels", "Job Applications"} <= set(labels)
    inbox = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"]
    assert [x["subject"] for x in inbox] == ["Hotel booking"] and inbox[0]["unread_count"] == 1
    assert inbox[0]["category"] == "updates"
    jobs = (await client.get("/api/v1/conversations", params={"view": f"label:{labels['Job Applications']['id']}"})).json()
    assert [x["subject"] for x in jobs["items"]] == ["Old thing"]
    # Re-running is harmless.
    await run_sync(r.json()["id"])
    assert await msg_count(user.id) == 2


# ---------------- live supervisor + IDLE ----------------


async def test_supervisor_idle_push(client, user):
    from mailr.connectors import runner

    email, pw = make_upstream_account()
    c = imap(email, pw)
    c.append("INBOX", msg("Before start"))
    c.logout()
    conn = await create_connector(client, email, pw)
    async with session_scope() as db:
        await db.execute(text("UPDATE connectors SET enabled = (id = :c)"), {"c": conn["id"]})
    task = asyncio.create_task(runner.supervise())
    try:
        for _ in range(60):
            await asyncio.sleep(0.5)
            if await msg_count(user.id) == 1:
                break
        assert await msg_count(user.id) == 1
        await asyncio.sleep(2)  # let it enter IDLE
        c = imap(email, pw)
        c.append("INBOX", msg("Pushed by IDLE"))
        c.logout()
        for _ in range(40):
            await asyncio.sleep(0.5)
            if await msg_count(user.id) == 2:
                break
        assert await msg_count(user.id) == 2
        st = (await client.get(f"/api/v1/connectors/{conn['id']}")).json()
        assert st["status"] == "idle" and st["progress"]["initial_done"]
        # Pausing stops the task.
        await client.post(f"/api/v1/connectors/{conn['id']}/pause")
        runner._wake.set()
        for _ in range(40):
            await asyncio.sleep(0.5)
            if conn["id"] not in runner._running:
                break
        assert conn["id"] not in runner._running
    finally:
        task.cancel()
        for cid in list(runner._running):
            await runner._stop(cid)


async def test_poison_message_does_not_block_the_account(client, user, monkeypatch):
    """A message mailr cannot store is retried, then skipped and reported; mail after it flows."""
    from mailr.connectors.imap import POISON_ATTEMPTS, _transient
    from mailr.connectors.runner import RunnerContext
    from mailr.core.jmap import JMAPError

    assert _transient(OSError("reset")) and _transient(JMAPError("http", "503"))
    assert not _transient(ValueError("bad MIME")) and not _transient(JMAPError("import", {"type": "invalid"}))

    email, pw = make_upstream_account()
    c = imap(email, pw)
    c.append("INBOX", msg("Poison", msgid="<poison@x.org>"))
    c.append("INBOX", msg("Fine", msgid="<fine@x.org>"))
    c.logout()
    real = RunnerContext.ingest

    async def flaky(self, raw, **kw):
        if b"<poison@x.org>" in raw:
            raise ValueError("cannot parse this message")
        return await real(self, raw, **kw)

    monkeypatch.setattr(RunnerContext, "ingest", flaky)
    conn = await create_connector(client, email, pw)
    totals = await run_sync(conn["id"])
    assert totals["new"] == 1  # "Fine" got through
    for _ in range(POISON_ATTEMPTS):
        await run_sync(conn["id"])
    async with session_scope() as db:
        row = await db.get(Connector, conn["id"])
        folder = row.state["folders"]["INBOX"]
        assert folder["skipped"] and not folder.get("failed")
    assert await msg_count(user.id) == 1


async def test_marking_many_read_is_one_batch_upstream(client, user, monkeypatch):
    """A bulk mark-read (thousands of messages on 30 Sep 2026) goes upstream as batched UID STOREs with
    one SELECT, not a round trip per message."""
    from mailr.connectors.imap import ImapConnector

    email, pw = make_upstream_account()
    c = imap(email, pw)
    for i in range(7):
        c.append("INBOX", msg(f"Bulk {i}", msgid=f"<bulk-{i}-{uuid.uuid4().hex}@x.org>"))
    c.logout()
    conn = await create_connector(client, email, pw, mode="mirror")
    await run_sync(conn["id"])
    ids = [x["id"] for x in (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"]]
    assert len(ids) == 7
    monkeypatch.setattr(ImapConnector, "FLAG_BATCH", 3)
    selects = []
    orig = ImapConnector._select_ref

    async def counting(self, r):
        selects.append(r["folder"])
        return await orig(self, r)

    monkeypatch.setattr(ImapConnector, "_select_ref", counting)
    assert (await client.post("/api/v1/conversations/actions", json={"ids": ids, "action": "read"})).status_code == 200
    await run_jobs("connector.write_back", user.id)
    assert selects == ["INBOX"]
    c = imap(email, pw)
    c.select_folder("INBOX", readonly=True)
    flags = c.get_flags(c.search(["ALL"]))
    assert len(flags) == 7 and all(b"\\Seen" in f for f in flags.values())
    c.logout()


async def test_first_sync_time_is_recorded_for_older_accounts(client, user):
    # Accounts synced before initial_done_at existed get it on their next pass, so new mail
    # from then on proves mail flow for arming upstream deletion.
    email, pw = make_upstream_account()
    conn = await create_connector(client, email, pw)
    await run_sync(conn["id"])
    async with session_scope() as db:
        row = await db.get(Connector, conn["id"])
        row.state = {k: v for k, v in row.state.items() if k != "initial_done_at"}
    await run_sync(conn["id"])
    async with sessionmaker()() as db:
        st = (await db.get(Connector, conn["id"])).state
    assert st["initial_done"] and st["initial_done_at"]


def test_backup_target_is_seeded_with_files_exported_before_it(tmp_path, monkeypatch):
    from mailr.config import get_settings
    from mailr.core import backup

    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path / "data"))
    base = backup.export_root(7)
    (base / "Inbox" / "cur").mkdir(parents=True)
    (base / "Inbox" / "cur" / "1.1.mailr:2,S").write_bytes(b"X-Mailr-Labels: Inbox\r\nhello")
    # A line of the first format names no file and never counts as exported.
    (base / ".exported").write_text("legacy\nblob1\tInbox/cur/1.1.mailr:2,S\n")
    assert set(backup.exported_manifest(7)) == {"blob1"}
    assert backup.seed_target(7) == 0  # no target configured

    monkeypatch.setattr(get_settings(), "backup_target", str(tmp_path / "nas"))
    (tmp_path / "nas").mkdir()
    (tmp_path / "nas" / backup.TARGET_MARKER).write_text("")  # created by the operator on the share
    from mailr.connectors.tasks import exported_blobs

    assert exported_blobs(7) == set()
    assert backup.seed_target(7) == 1
    assert (tmp_path / "nas" / "user-7" / "Inbox" / "cur" / "1.1.mailr:2,S").read_bytes().endswith(b"hello")
    assert exported_blobs(7) == {"blob1"}
    assert backup.seed_target(7) == 0  # the target's manifest is complete
