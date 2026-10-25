# ACT-SURFACES: api
"""Mail sent from mailr is saved in the provider's Sent folder (connectors/sentcopy.py), against the
test upstream Stalwart (IMAP :12143) and a local SMTP sink.

After a send through a mirror-mode IMAP account, `sent.provider_copy` appends the stored bytes,
flagged \\Seen, and links the copy, so the next mirror sync imports nothing twice. Store and import
accounts never append; Gmail and Microsoft hosts are skipped on "Automatic"
(ACT settings.account_save_sent). A retry finds the earlier copy by its Message-ID. Failures end
in the account's health with a Retry (ACT settings.account_sent_retry). The backfill copies the
account's past sent mail that is missing (ACT settings.account_sent_backfill), also as
`mailr sent-backfill`."""

from __future__ import annotations

import argparse
import socket
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from aiosmtpd.controller import Controller
from helpers.connectors import create_connector, imap, make_upstream_account, msg_count, run_sync
from sqlalchemy import select, text

from mailr.connectors import sentcopy
from mailr.connectors.imap import ImapConnector, all_refs
from mailr.connectors.runner import RunnerContext
from mailr.core.jobs import JobContext
from mailr.core.store import user_store
from mailr.db import session_scope, sessionmaker
from mailr.models import Connector, Identity, Job, Message, Outbox, User

SENT = "Sent Items"  # the upstream Stalwart's \Sent folder


class _Sink:
    def __init__(self):
        self.got: list[bytes] = []

    async def handle_DATA(self, server, session, envelope):
        self.got.append(envelope.original_content or envelope.content)
        return "250 OK queued as test"


@pytest.fixture
def smtp():
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    sink = _Sink()
    ctl = Controller(sink, hostname="127.0.0.1", port=port)
    ctl.start()
    try:
        yield SimpleNamespace(sink=sink, cfg={"host": "127.0.0.1", "port": port, "tls": "none", "auth": False})
    finally:
        ctl.stop()


async def _account(client, smtp, mode="mirror"):
    email, pw = make_upstream_account()
    conn = await create_connector(client, email, pw, mode=mode, smtp=smtp.cfg)
    await run_sync(conn["id"])  # the first pass, before anything is sent
    return conn, email, pw


async def _send(client, user, subject: str) -> int:
    """POST /send, then what the worker does: outbox.send and outbox.store_sent."""
    from mailr.core.sendgate import send_job, store_sent_job

    ident = (await client.get("/api/v1/identities")).json()[0]
    r = await client.post("/api/v1/send", json={"payload": {
        "identity_id": ident["id"], "to": [{"email": "rosa.vidal@example.org", "name": "Rosa Vidal"}],
        "subject": subject, "html": "<p>Hello Rosa</p>"}})
    assert r.status_code == 200, r.text
    oid = r.json()["outbox"]["id"]
    async with session_scope() as db:
        (await db.get(Outbox, oid)).send_at = datetime.now(UTC) - timedelta(seconds=1)
    await send_job({"outbox_id": oid}, None)
    async with sessionmaker()() as db:
        ob = await db.get(Outbox, oid)
        assert ob.status == "sent", ob.error
    await store_sent_job({"outbox_id": oid}, None)
    return oid


async def _sent_message(oid: int) -> Message:
    async with sessionmaker()() as db:
        ob = await db.get(Outbox, oid)
        return (await db.execute(select(Message).where(Message.message_id_hdr == ob.mime_message_id))).scalar_one()


async def _queued(user, kind=sentcopy.JOB) -> list[dict]:
    async with sessionmaker()() as db:
        return [j.payload for j in (await db.execute(select(Job).where(
            Job.kind == kind, Job.user_id == user.id, Job.status == "queued").order_by(Job.id))).scalars()]


async def _run_queued(user, kind=sentcopy.JOB) -> int:
    """Claim and run the due jobs of one kind, as the worker would (attempts, backoff, failure)."""
    from mailr.core import jobs

    n = 0
    while True:
        async with session_scope() as db:
            await db.execute(text("UPDATE jobs SET run_at = now() WHERE kind = :k AND user_id = :u AND status = 'queued'"),
                             {"k": kind, "u": user.id})
        job = await jobs._claim([kind])
        if job is None:
            return n
        await jobs.run_one(job)
        n += 1


def _upstream(email, pw, folder=SENT) -> dict[int, dict]:
    c = imap(email, pw)
    try:
        c.select_folder(folder, readonly=True)
        uids = c.search(["ALL"])
        return c.fetch(uids, [b"BODY.PEEK[]", b"FLAGS"]) if uids else {}
    finally:
        c.logout()


# ---------------- after a send ----------------


async def test_a_mirror_send_is_saved_in_the_providers_sent_folder(client, user, smtp):
    conn, email, pw = await _account(client, smtp)
    oid = await _send(client, user, "Quarterly figures")
    assert [p["connector_id"] for p in await _queued(user)] == [conn["id"]]
    assert await _run_queued(user) == 1

    there = _upstream(email, pw)
    assert len(there) == 1
    (uid, got), = there.items()
    # The very bytes that went out through SMTP, Message-ID included, already read.
    assert got[b"BODY[]"] == smtp.sink.got[0]
    assert b"\\Seen" in got[b"FLAGS"]

    # Linked as a mirror sync links what it imports: the next sync imports nothing twice.
    m = await _sent_message(oid)
    refs = all_refs(m.upstream)
    assert [(r["folder"], r["uid"]) for r in refs] == [(SENT, uid)]
    before = await msg_count(user.id)
    await run_sync(conn["id"])
    assert await msg_count(user.id) == before
    assert len((await client.get("/api/v1/conversations", params={"view": "sent"})).json()["items"]) == 1


async def test_store_and_import_accounts_never_append(client, user, smtp):
    for mode in ("store", "import"):
        async with session_scope() as db:
            c = Connector(user_id=user.id, kind="imap", name=f"acct-{mode}", mode=mode, enabled=True,
                          config={"imap": {"host": "imap.example.org"}})
            db.add(c)
            await db.flush()
            assert not sentcopy.active(c) and sentcopy.out(c) is None
            await sentcopy.queue_after_send(db, c, 1)
    assert await _queued(user) == []


@pytest.mark.parametrize("host, saves", [
    ("imap.gmail.com", True), ("imap.googlemail.com", True), ("outlook.office365.com", True),
    ("imap-mail.outlook.com", True), ("imap.hotmail.com", True), ("imap.live.com", True),
    ("imap.fastmail.com", False), ("mail.example.org", False), ("notgmail.com", False),
])
def test_providers_that_save_their_own_copy(host, saves):
    assert sentcopy.provider_saves_sent(host) is saves
    c = Connector(kind="imap", mode="mirror", enabled=True, config={"imap": {"host": host}}, name="Work")
    assert sentcopy.active(c) is not saves  # Automatic
    c.config = {**c.config, "save_sent": "on"}
    assert sentcopy.active(c) is True
    c.config = {**c.config, "save_sent": "off"}
    assert sentcopy.active(c) is False
    c.kind = "gmail"
    assert sentcopy.active(c) is False and sentcopy.out(c) is None


async def test_the_setting_in_the_api(client, user, smtp):
    conn, _email, _pw = await _account(client, smtp)
    assert conn["sent_copies"] == {"setting": "auto", "active": True, "provider_saves": False,
                                   "provider": conn["name"], "failed": 0}
    r = await client.patch(f"/api/v1/connectors/{conn['id']}", json={"save_sent": "off"})
    assert r.status_code == 200, r.text
    assert r.json()["sent_copies"]["setting"] == "off" and r.json()["sent_copies"]["active"] is False
    await _send(client, user, "Not saved upstream")
    assert await _queued(user) == []
    assert (await client.patch(f"/api/v1/connectors/{conn['id']}", json={"save_sent": "always"})).status_code == 422
    # The setting is an IMAP account's only.
    async with session_scope() as db:
        cal = Connector(user_id=user.id, kind="caldav", name="Calendar", mode="mirror", enabled=True, config={})
        db.add(cal)
        await db.flush()
        cal_id = cal.id
    r = await client.patch(f"/api/v1/connectors/{cal_id}", json={"save_sent": "on"})
    assert r.status_code == 400


async def test_a_retry_never_makes_a_second_copy(client, user, smtp):
    conn, email, pw = await _account(client, smtp)
    oid = await _send(client, user, "Retried")
    m = await _sent_message(oid)
    assert await sentcopy.save_one(conn["id"], m.id) is not None
    # A worker that died after the APPEND, before the link: the next attempt finds the copy by
    # its Message-ID and links it instead of appending again.
    async with session_scope() as db:
        (await db.get(Message, m.id)).upstream = {}
    again = await sentcopy.save_one(conn["id"], m.id)
    assert again["existed"] is True
    assert len(_upstream(email, pw)) == 1
    assert [r["folder"] for r in all_refs((await _sent_message(oid)).upstream)] == [SENT]
    # Linked already: nothing is asked of the server's Sent folder again.
    third = await sentcopy.save_one(conn["id"], m.id)
    assert third == {"folder": SENT, "existed": True}
    assert len(_upstream(email, pw)) == 1


async def test_a_sync_that_fetches_the_copy_first_finds_the_link(client, user, smtp):
    """The provider may change the bytes it stores; a sync that fetched the copy before the link
    was written still recognises it by the recorded ref (core/ingest.py `_find_duplicate`)."""
    conn, _email, _pw = await _account(client, smtp)
    oid = await _send(client, user, "Changed upstream")
    await _run_queued(user)
    m = await _sent_message(oid)
    ref = all_refs(m.upstream)[0]
    async with sessionmaker()() as db:
        raw = await (await user_store(db, await db.get(User, user.id))).get_raw(m)
    changed = raw.replace(b"\r\n\r\n", b"\r\nX-Provider-Stamp: 1\r\n\r\n", 1)
    res = await RunnerContext(conn["id"], user.id, {}).ingest(
        changed, roles=["sent"], label_names=[], source="connector", merge_labels=True,
        upstream={k: ref[k] for k in ("connector", "folder", "uidvalidity", "uid")})
    assert res["duplicate"] is True and res["message_id"] == m.id


# ---------------- failures, the health warning and Retry ----------------


async def test_failures_retry_then_warn_then_retry(client, user, smtp, monkeypatch):
    conn, email, pw = await _account(client, smtp)
    monkeypatch.setattr(sentcopy, "SENT_COPY_ATTEMPTS", 2)
    real = ImapConnector.append_sent

    async def down(self, *a, **kw):
        raise OSError("connection reset by the provider")

    monkeypatch.setattr(ImapConnector, "append_sent", down)
    oid = await _send(client, user, "Provider down")
    assert await _run_queued(user) == 2  # tried, retried with backoff, given up
    async with sessionmaker()() as db:
        statuses = [j.status for j in (await db.execute(select(Job).where(Job.kind == sentcopy.JOB,
                                                                          Job.user_id == user.id))).scalars()]
    assert statuses == ["failed"]
    acct = (await client.get(f"/api/v1/connectors/{conn['id']}")).json()
    assert acct["sent_copies"]["failed"] == 1
    assert acct["health"]["state"] == "stale" and acct["health"]["code"] == "account.sent_not_saved"
    assert acct["health"]["text"] == f"1 sent message could not be saved in {conn['name']}'s Sent folder"
    listed = next(c for c in (await client.get("/api/v1/connectors")).json() if c["id"] == conn["id"])
    assert listed["health"]["code"] == "account.sent_not_saved"
    # Sending itself was never affected.
    async with sessionmaker()() as db:
        assert (await db.get(Outbox, oid)).status == "sent"

    monkeypatch.setattr(ImapConnector, "append_sent", real)
    r = await client.post(f"/api/v1/connectors/{conn['id']}/sent-copies/retry")
    assert r.status_code == 200 and r.json() == {"queued": 1}
    assert await _run_queued(user) == 1
    assert len(_upstream(email, pw)) == 1
    acct = (await client.get(f"/api/v1/connectors/{conn['id']}")).json()
    assert acct["sent_copies"]["failed"] == 0 and acct["health"]["code"] != "account.sent_not_saved"
    assert (await client.post(f"/api/v1/connectors/{conn['id']}/sent-copies/retry")).json() == {"queued": 0}


# ---------------- backfill ----------------


async def _past_sends(user, conn_id: int, subjects: list[str], email: str, days_ago: int = 0) -> list[int]:
    """Sent messages in mailr from an identity of this account, as the worker stores them, without
    their provider copy (the job queued after each is dropped)."""
    from mailr.core import sendgate
    from mailr.core.drafts import build_for_payload

    async with session_scope() as db:
        ident = (await db.execute(select(Identity).where(Identity.user_id == user.id,
                                                         Identity.email == email))).scalar_one_or_none()
        if ident is None:
            ident = Identity(user_id=user.id, email=email, name="Test User", connector_id=conn_id)
            db.add(ident)
            await db.flush()
        iid = ident.id
    oids = []
    for s in subjects:
        async with session_scope() as db:
            u = await db.get(User, user.id)
            ob = Outbox(user_id=user.id, identity_id=iid, payload={
                "identity_id": iid, "to": [{"email": "noor.haddad@example.org", "name": "Noor Haddad"}],
                "subject": s, "text": "Past message"}, recipients=["noor.haddad@example.org"],
                send_at=datetime.now(UTC), status="sent", mime_message_id=f"<{uuid.uuid4().hex}@example.org>")
            db.add(ob)
            await db.flush()
            data, _, _ = await build_for_payload(db, sendgate._SystemPrincipal(u), ob.payload,
                                                 message_id=ob.mime_message_id)
            (sendgate._outbox_dir() / f"{ob.id}.eml").write_bytes(data)
            ob.sent_message_id = ob.mime_message_id
            ob.sent_at = datetime.now(UTC) - timedelta(days=days_ago)
            oids.append(ob.id)
        await sendgate.store_sent_job({"outbox_id": oids[-1]}, None)
    async with session_scope() as db:
        await db.execute(text("DELETE FROM jobs WHERE kind = :k AND user_id = :u"), {"k": sentcopy.JOB, "u": user.id})
    return oids


async def _backfill_job(client, user, cid: int, **body) -> dict:
    r = await client.post(f"/api/v1/connectors/{cid}/sent-backfill", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    async with sessionmaker()() as db:
        job = await db.get(Job, j["job"])
        payload = job.payload
    async with session_scope() as db:
        await db.execute(text("UPDATE jobs SET status = 'running' WHERE id = :id"), {"id": j["job"]})
    await sentcopy.backfill_job(payload, JobContext(j["job"], sentcopy.BACKFILL_JOB, user.id, 1))
    async with session_scope() as db:
        await db.execute(text("UPDATE jobs SET status = 'done' WHERE id = :id"), {"id": j["job"]})
    return (await client.get(f"/api/v1/connectors/{cid}/sent-backfill/{j['job']}")).json()


async def test_backfill_copies_only_this_accounts_missing_sent_mail(client, user, smtp):
    conn, email, pw = await _account(client, smtp)
    other, other_email, _ = await _account(client, smtp)
    mine = await _past_sends(user, conn["id"], ["Old one", "Old two", "Old three"], email)
    await _past_sends(user, other["id"], ["Through the other account"], other_email)
    # One of them is at the provider already (another app saved it): found, linked, not copied again.
    first = await _sent_message(mine[0])
    async with sessionmaker()() as db:
        raw = await (await user_store(db, await db.get(User, user.id))).get_raw(first)
    c = imap(email, pw)
    c.append(SENT, raw)
    c.logout()

    dry = await _backfill_job(client, user, conn["id"], dry_run=True)
    assert dry["status"] == "done" and dry["dry_run"] is True
    assert dry["result"] == {"total": 3, "present": 1, "copied": 2, "failed": 0}
    assert len(_upstream(email, pw)) == 1  # a dry run changes nothing
    assert all_refs((await _sent_message(mine[0])).upstream) == []

    done = await _backfill_job(client, user, conn["id"], dry_run=False)
    assert done["result"] == {"total": 3, "present": 1, "copied": 2, "failed": 0} and done["done"] == 3
    there = _upstream(email, pw)
    assert len(there) == 3
    subjects = sorted(b.split(b"Subject: ")[1].split(b"\r\n")[0].decode() for b in (d[b"BODY[]"] for d in there.values()))
    assert subjects == ["Old one", "Old three", "Old two"]
    for oid in mine:
        assert [r["folder"] for r in all_refs((await _sent_message(oid)).upstream)] == [SENT]
    # Run again (or after a stop): what is there is skipped, nothing is added or removed.
    again = await _backfill_job(client, user, conn["id"], dry_run=False)
    assert again["result"] == {"total": 3, "present": 3, "copied": 0, "failed": 0}
    assert len(_upstream(email, pw)) == 3
    latest = (await client.get(f"/api/v1/connectors/{conn['id']}/sent-backfill")).json()
    assert latest["job"] == again["job"]
    before = await msg_count(user.id)
    await run_sync(conn["id"])
    assert await msg_count(user.id) == before


async def test_backfill_resumes_and_honours_since(client, user, smtp):
    conn, email, pw = await _account(client, smtp)
    old = await _past_sends(user, conn["id"], ["Last year"], email, days_ago=400)
    recent = await _past_sends(user, conn["id"], ["Last week", "Yesterday"], email, days_ago=3)
    since = (datetime.now(UTC) - timedelta(days=30)).date()
    n = await sentcopy.backfill(user.id, conn["id"], since=since, dry_run=True)
    assert n == {"total": 2, "present": 0, "copied": 2, "failed": 0}
    # A run that stopped after its first message continues there, with its counts.
    state = {"cursor": recent[0], "counts": {"total": 1, "present": 0, "copied": 1, "failed": 0}}
    n = await sentcopy.backfill(user.id, conn["id"], since=since, state=state)
    assert n == {"total": 2, "present": 0, "copied": 2, "failed": 0}
    assert len(_upstream(email, pw)) == 1  # only "Yesterday": the first was the stopped run's
    assert all_refs((await _sent_message(old[0])).upstream) == []


async def test_backfill_needs_a_mirror_imap_account(client, user, smtp):
    conn, _email, _pw = await _account(client, smtp, mode="import")
    r = await client.post(f"/api/v1/connectors/{conn['id']}/sent-backfill", json={"dry_run": True})
    assert r.status_code == 400
    with pytest.raises(sentcopy.BackfillError):
        await sentcopy.backfill(user.id, conn["id"], dry_run=True)


async def test_cli_sent_backfill(client, user, smtp, capsys):
    from mailr.cli import _sent_backfill

    conn, email, pw = await _account(client, smtp)
    await _past_sends(user, conn["id"], ["From the command line"], email)
    ns = argparse.Namespace(user=user.email, account=None, since=None, dry_run=True)
    assert await _sent_backfill(ns) == 2
    assert f"{conn['id']}  {conn['name']}  mirror  save sent mail: auto (on)" in capsys.readouterr().out
    ns.account = conn["id"]
    assert await _sent_backfill(ns) == 0
    assert "dry run, nothing changed: 1 sent through this account; 0 already at the provider, 1 to copy" \
        in capsys.readouterr().out
    assert _upstream(email, pw) == {}
    ns.dry_run, ns.since = False, date.today().isoformat()
    assert await _sent_backfill(ns) == 0
    assert "done: 1 sent through this account; 0 already at the provider, 1 copied, 0 failed" in capsys.readouterr().out
    assert len(_upstream(email, pw)) == 1
    ns.since = "yesterday"
    assert await _sent_backfill(ns) == 1
