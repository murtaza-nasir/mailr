# ACT-SURFACES: api
"""Mail to yourself in a mirror account ("Keep in sync") keeps both provider copies.

23 Oct 2026 (doc test, finding 11): the tester replied to their own message and archived the
conversation. The provider's delivered copy of the reply left INBOX and was in no folder any more;
only the Sent copy mailr had appended (connectors/sentcopy.py) was left. Mailr had merged the two
provider copies into one message (same Message-ID and content, core/ingest.py `_merge_duplicate`), and
the archive write-back took the Sent copy for "another folder the message is in" and expunged the
INBOX copy instead of moving it to Archive (ImapConnector.write_back).

These tests run the whole path against the test upstream Stalwart: a send through the outbox to the
account's own address, the provider's delivery to INBOX (appended here, since the SMTP server is a
local sink), the sent copy, mirror syncs, and the write-backs of Archive, a label, Trash and Delete
forever. A provider copy goes only when the user deleted the message."""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from aiosmtpd.controller import Controller
from helpers.connectors import create_connector, imap, make_upstream_account, run_jobs, run_sync
from sqlalchemy import select, text

from mailr.connectors import sentcopy
from mailr.connectors.imap import all_refs
from mailr.core.mime import parse_msgids
from mailr.db import session_scope, sessionmaker
from mailr.models import Label, Message, Outbox

SENT, ARCHIVE, TRASH = "Sent Items", "Archive", "Deleted Items"  # the upstream Stalwart's folders


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


async def _account(client, smtp, save_sent: str = "auto"):
    email, pw = make_upstream_account()
    conn = await create_connector(client, email, pw, mode="mirror", smtp=smtp.cfg)
    if save_sent != "auto":
        r = await client.patch(f"/api/v1/connectors/{conn['id']}", json={"save_sent": save_sent})
        assert r.status_code == 200, r.text
    await run_sync(conn["id"])
    return SimpleNamespace(id=conn["id"], email=email, pw=pw)


def _deliver(acct, raw: bytes) -> None:
    """The provider delivers a message addressed to the account into its INBOX, unread."""
    c = imap(acct.email, acct.pw)
    try:
        c.append("INBOX", f"Received: from mx.upstream.test; {datetime.now(UTC):%a, %d %b %Y %H:%M:%S +0000}\r\n"
                          f"Delivered-To: {acct.email}\r\n".encode() + raw)
    finally:
        c.logout()


async def _send_to_self(client, smtp, acct, subject: str, parent: int | None = None) -> int:
    """POST /send to the account's own address, then what the worker does: outbox.send,
    outbox.store_sent and the provider's Sent copy; the provider delivers the message to INBOX.
    Returns the mailr message id."""
    from mailr.core.sendgate import send_job, store_sent_job

    ident = (await client.get("/api/v1/identities")).json()[0]
    payload = {"identity_id": ident["id"], "to": [{"email": acct.email, "name": "Me"}],
               "subject": subject, "html": f"<p>{subject}: notes for Friday</p>"}
    if parent:
        payload["in_reply_to_message_id"] = parent
    r = await client.post("/api/v1/send", json={"payload": payload})
    assert r.status_code == 200, r.text
    oid = r.json()["outbox"]["id"]
    async with session_scope() as db:
        (await db.get(Outbox, oid)).send_at = datetime.now(UTC) - timedelta(seconds=1)
    await send_job({"outbox_id": oid}, None)
    async with sessionmaker()() as db:
        ob = await db.get(Outbox, oid)
        assert ob.status == "sent", ob.error
        msgid = ob.mime_message_id
    await store_sent_job({"outbox_id": oid}, None)
    await run_jobs(sentcopy.JOB, acct_user := await _user_id(acct))
    _deliver(acct, smtp.sink.got[-1])
    await run_sync(acct.id)
    await run_jobs("connector.write_back", acct_user)
    async with sessionmaker()() as db:
        return (await db.execute(select(Message.id).where(Message.message_id_hdr == msgid))).scalar_one()


async def _user_id(acct) -> int:
    async with sessionmaker()() as db:
        return (await db.execute(text("SELECT user_id FROM connectors WHERE id = :c"), {"c": acct.id})).scalar_one()


def _where(acct, msgid: str) -> dict[str, list[bool]]:
    """Every provider folder holding this Message-ID: {folder: [seen of each copy]}."""
    want = parse_msgids(msgid)
    out: dict[str, list[bool]] = {}
    c = imap(acct.email, acct.pw)
    try:
        for _flags, _delim, name in c.list_folders():
            sel = c.select_folder(name, readonly=True)
            if not int(sel.get(b"EXISTS", 0)):
                continue
            got = c.fetch(c.search(["ALL"]), [b"BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]", b"FLAGS"])
            for d in got.values():
                hdr = next(v for k, v in d.items() if isinstance(k, bytes) and k.startswith(b"BODY[HEADER"))
                if parse_msgids(hdr.decode()) == want:
                    out.setdefault(name, []).append(b"\\Seen" in d[b"FLAGS"])
    finally:
        c.logout()
    return out


async def _msg(mid: int) -> Message:
    async with sessionmaker()() as db:
        return await db.get(Message, mid)


async def _labels(user) -> dict[str, int]:
    async with sessionmaker()() as db:
        return {(lb.role or lb.name): lb.id for lb in (await db.execute(
            select(Label).where(Label.user_id == user.id))).scalars()}


async def _act(client, user, acct, mid: int, action: str, **args) -> None:
    m = await _msg(mid)
    r = await client.post("/api/v1/conversations/actions", json={"ids": [m.conversation_id], "action": action, **args})
    assert r.status_code == 200, r.text
    await _settle(acct, user)


async def _settle(acct, user) -> None:
    """The write-backs, then a sync (IDLE would run one at once), then what that queued: a copy
    mailr makes at the provider is known only after a sync (no COPYUID from imapclient)."""
    await run_jobs("connector.write_back", user.id)
    await run_sync(acct.id)
    await run_jobs("connector.write_back", user.id)


def _folders(where: dict[str, list[bool]]) -> dict[str, int]:
    return {f: len(v) for f, v in where.items()}


# ---------------- the tests ----------------


@pytest.mark.parametrize("save_sent", ["auto", "off"])
async def test_mail_to_self_keeps_its_delivered_copy(client, user, smtp, save_sent):
    acct = await _account(client, smtp, save_sent)
    mid = await _send_to_self(client, smtp, acct, "Q3 budget review")
    m = await _msg(mid)
    lb = await _labels(user)
    assert {lb["inbox"], lb["sent"]} <= set(m.label_ids)
    want = {"INBOX": 1, SENT: 1} if save_sent == "auto" else {"INBOX": 1}
    assert _folders(_where(acct, m.message_id_hdr)) == want
    # The user's own mail is read, in mailr and in every provider copy (the delivered copy came
    # in unread and was marked read; its unread flag never reached mailr).
    assert m.seen is True
    assert all(all(v) for v in _where(acct, m.message_id_hdr).values())
    # One message in mailr, linked to every provider copy.
    assert sorted(r["folder"] for r in all_refs(m.upstream)) == sorted(want)
    # A later sync changes nothing.
    await run_sync(acct.id)
    await run_jobs("connector.write_back", user.id)
    assert _folders(_where(acct, m.message_id_hdr)) == want


@pytest.mark.parametrize("save_sent", ["auto", "off"])
async def test_reply_to_self_and_archive_keeps_both_copies(client, user, smtp, save_sent):
    """The doc test's sequence: send to self, label, reply to self, archive the conversation."""
    acct = await _account(client, smtp, save_sent)
    first = await _send_to_self(client, smtp, acct, "Run4 Q3 budget review")
    lbl = (await client.post("/api/v1/labels", json={"name": "Run4 label"})).json()
    await _act(client, user, acct, first, "add_labels", label_ids=[lbl["id"]])
    reply = await _send_to_self(client, smtp, acct, "Re: Run4 Q3 budget review", parent=first)
    sent = {SENT: 1} if save_sent == "auto" else {}
    assert _folders(_where(acct, (await _msg(reply)).message_id_hdr)) == {"INBOX": 1, **sent}

    await _act(client, user, acct, first, "archive")

    # The first message: its delivered copy is in the label folder (the label keeps it), Sent stays.
    assert _folders(_where(acct, (await _msg(first)).message_id_hdr)) == {"Run4 label": 1, **sent}
    # The reply: its delivered copy moved to Archive, nothing expunged; its flags came along.
    where = _where(acct, (await _msg(reply)).message_id_hdr)
    assert _folders(where) == {ARCHIVE: 1, **sent}
    m = await _msg(reply)
    assert m.seen is True and where[ARCHIVE] == [True]
    lb = await _labels(user)
    assert lb["inbox"] not in m.label_ids
    # Another write-back of the archived reply (it is read) moves nothing again.
    await _act(client, user, acct, reply, "read")
    assert _folders(_where(acct, m.message_id_hdr)) == {ARCHIVE: 1, **sent}
    assert _where(acct, m.message_id_hdr)[ARCHIVE] == [True]


@pytest.mark.parametrize("save_sent", ["auto", "off"])
async def test_label_then_unlabel_a_self_sent_message(client, user, smtp, save_sent):
    acct = await _account(client, smtp, save_sent)
    mid = await _send_to_self(client, smtp, acct, "Lighthouse inspection")
    sent = {SENT: 1} if save_sent == "auto" else {}
    lbl = (await client.post("/api/v1/labels", json={"name": "Harbour"})).json()
    await _act(client, user, acct, mid, "add_labels", label_ids=[lbl["id"]])
    assert _folders(_where(acct, (await _msg(mid)).message_id_hdr)) == {"INBOX": 1, "Harbour": 1, **sent}
    await _act(client, user, acct, mid, "archive")
    assert _folders(_where(acct, (await _msg(mid)).message_id_hdr)) == {"Harbour": 1, **sent}
    # Taking the last label off an archived message: its delivered copy goes to Archive, never away.
    await _act(client, user, acct, mid, "remove_labels", label_ids=[lbl["id"]])
    assert _folders(_where(acct, (await _msg(mid)).message_id_hdr)) == {ARCHIVE: 1, **sent}
    # Back to the Inbox: the delivered copy returns there; Sent is untouched.
    await _act(client, user, acct, mid, "inbox")
    assert _folders(_where(acct, (await _msg(mid)).message_id_hdr)) == {"INBOX": 1, **sent}


@pytest.mark.parametrize("save_sent", ["auto", "off"])
async def test_only_delete_removes_provider_copies(client, user, smtp, save_sent):
    acct = await _account(client, smtp, save_sent)
    mid = await _send_to_self(client, smtp, acct, "Supplier invoice")
    msgid = (await _msg(mid)).message_id_hdr
    sent = {SENT: 1} if save_sent == "auto" else {}
    # Trash: the delivered copy moves to the provider's Trash; the Sent copy stays where it is
    # (as for any message sent from Mailr).
    await _act(client, user, acct, mid, "trash")
    assert _folders(_where(acct, msgid)) == {TRASH: 1, **sent}
    # Delete forever: now, and only now, the Sent copy goes too, to the provider's Trash (which the
    # mirror does not sync, so the Trash copy is the provider's to empty).
    await _act(client, user, acct, mid, "delete_forever")
    assert set(_where(acct, msgid)) == {TRASH}


async def test_delete_forever_from_the_inbox_moves_to_the_providers_trash(client, user, smtp):
    acct = await _account(client, smtp)
    mid = await _send_to_self(client, smtp, acct, "Draft budget")
    msgid = (await _msg(mid)).message_id_hdr
    await _act(client, user, acct, mid, "delete_forever")
    # Both copies leave INBOX and Sent for the provider's Trash.
    assert set(_where(acct, msgid)) == {TRASH}


async def test_a_provider_filter_copy_survives_archive(client, user, smtp):
    """Normal mail a provider filter delivered to two folders (INBOX and a folder): Archive removes
    only the INBOX copy; the filter's folder keeps the message."""
    from helpers.connectors import msg

    acct = await _account(client, smtp)
    raw = msg("Harbour newsletter", to=acct.email)
    c = imap(acct.email, acct.pw)
    c.create_folder("Newsletters")
    c.append("INBOX", raw)
    c.append("Newsletters", raw)
    c.logout()
    await run_sync(acct.id)
    async with sessionmaker()() as db:
        mid = (await db.execute(select(Message.id).where(Message.user_id == user.id,
                                                         Message.subject == "Harbour newsletter"))).scalar_one()
    msgid = (await _msg(mid)).message_id_hdr
    assert _folders(_where(acct, msgid)) == {"INBOX": 1, "Newsletters": 1}
    await _act(client, user, acct, mid, "archive")
    assert _folders(_where(acct, msgid)) == {"Newsletters": 1}
    await _act(client, user, acct, mid, "inbox")
    assert _folders(_where(acct, msgid)) == {"INBOX": 1, "Newsletters": 1}


async def test_own_mail_to_others_keeps_its_home_in_sent(client, user, smtp):
    """Mail the user sent to someone else lives in Sent: a label copy mailr made goes when the label
    is taken off, and no stray copy appears in Archive."""
    from mailr.core.sendgate import send_job, store_sent_job

    acct = await _account(client, smtp)
    ident = (await client.get("/api/v1/identities")).json()[0]
    r = await client.post("/api/v1/send", json={"payload": {
        "identity_id": ident["id"], "to": [{"email": "rosa.vidal@example.org", "name": "Rosa Vidal"}],
        "subject": "Harbour permit", "html": "<p>Hello Rosa</p>"}})
    oid = r.json()["outbox"]["id"]
    async with session_scope() as db:
        (await db.get(Outbox, oid)).send_at = datetime.now(UTC) - timedelta(seconds=1)
    await send_job({"outbox_id": oid}, None)
    await store_sent_job({"outbox_id": oid}, None)
    await run_jobs(sentcopy.JOB, user.id)
    async with sessionmaker()() as db:
        msgid = (await db.get(Outbox, oid)).mime_message_id
        mid = (await db.execute(select(Message.id).where(Message.message_id_hdr == msgid))).scalar_one()
    await _settle(acct, user)
    assert _folders(_where(acct, msgid)) == {SENT: 1}
    lbl = (await client.post("/api/v1/labels", json={"name": "Permits"})).json()
    await _act(client, user, acct, mid, "add_labels", label_ids=[lbl["id"]])
    assert _folders(_where(acct, msgid)) == {SENT: 1, "Permits": 1}
    await _act(client, user, acct, mid, "remove_labels", label_ids=[lbl["id"]])
    assert _folders(_where(acct, msgid)) == {SENT: 1}


def test_which_copies_file_a_message():
    """_filed: Sent and Drafts never file received mail; Trash and Spam file only a move there."""
    from mailr.connectors.base import WriteBack
    from mailr.connectors.imap import _filed

    special = {"sent": SENT, "drafts": "Drafts", "trash": TRASH, "junk": "Junk Mail"}
    refs = [{"folder": f} for f in (SENT, "Drafts", TRASH, "Junk Mail", ARCHIVE, "Harbour")]

    def folders(**kw):
        return [r["folder"] for r in _filed(refs, WriteBack(upstream={}, **kw), special)]

    assert folders() == [ARCHIVE, "Harbour"]
    assert folders(received=False) == [SENT, "Drafts", ARCHIVE, "Harbour"]
    assert folders(add_labels=["trash"]) == [TRASH, ARCHIVE, "Harbour"]
    assert folders(add_labels=["junk"], received=False) == [SENT, "Drafts", "Junk Mail", ARCHIVE, "Harbour"]
