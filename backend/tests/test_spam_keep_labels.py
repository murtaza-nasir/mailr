"""Report spam keeps the user's own labels (8 Oct 2026).

Report spam used to strip every label from the mail, the user's own included, so "Not spam"
brought it back to the Inbox without them. Now:

- the mail keeps only the Spam label, in mailr's store and upstream (a mirror account keeps only
  its Junk copy), so no label view, tab or count and no other mail app shows it in a label folder;
- the user's own labels (given by the user, a filter or the provider) are kept aside on the message
  (Message.spam_kept_labels); the labels the classifier applied by itself are dropped;
- "Not spam" (or the Undo of Report spam, or Move to Inbox) puts the kept labels back, in mailr and
  upstream (the label folders get their copies again);
- the Undo of "Not spam" keeps them aside again; Trash drops them, as it drops every label.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from helpers.connectors import (
    create_connector,
    imap,
    make_upstream_account,
    msg,
    run_jobs,
    run_sync,
)
from helpers.core_flow import _ingest
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import Label, LabelDecision, Message


def _now_hdr():
    return datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")


async def _label(client, name):
    return (await client.post("/api/v1/labels", json={"name": name})).json()["id"]


async def _act(client, cid, action, **extra):
    r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": action, **extra})
    assert r.status_code == 200, r.text
    return r.json()


async def _msg(mid) -> Message:
    async with sessionmaker()() as db:
        return await db.get(Message, mid)


async def _role(user_id, role):
    async with sessionmaker()() as db:
        return (await db.execute(select(Label.id).where(Label.user_id == user_id, Label.role == role))).scalar_one()


async def _ids(client, **params):
    r = await client.get("/api/v1/conversations", params=params)
    assert r.status_code == 200, r.text
    return {c["id"] for c in r.json()["items"]}


async def _classifier_applied(user_id, mid, lid):
    """The label as the classifier applies it by itself: in the store, with an applied decision."""
    from mailr.core.store import user_store
    from mailr.models import User

    async with sessionmaker()() as db:
        store = await user_store(db, await db.get(User, user_id))
        m = await db.get(Message, mid)
        await store.set_labels([m], add=[lid], remove=[], db=db)
        db.add(LabelDecision(user_id=user_id, message_id=mid, kind="label", target=str(lid), value=True,
                             source="classifier", source_ref=f"label:{lid}", confidence=0.9, reason="similar",
                             status="applied"))
        await db.commit()


async def test_report_spam_keeps_own_labels_aside_and_not_spam_restores_them(client, user, raw):
    mine = await _label(client, "Clients")
    machine = await _label(client, "GitHub")
    mid, cid = await _ingest(user, raw("Quarterly numbers", frm="Ann <ann@example.org>", date=_now_hdr()))
    await _act(client, cid, "add_labels", label_ids=[mine])
    await _classifier_applied(user.id, mid, machine)
    junk, inbox = await _role(user.id, "junk"), await _role(user.id, "inbox")
    assert {mine, machine, inbox} <= set((await _msg(mid)).label_ids)

    await _act(client, cid, "spam")
    m = await _msg(mid)
    assert m.label_ids == [junk]  # only Spam, in the store too
    assert m.spam_kept_labels == [mine]  # the classifier's label is not kept
    # Hidden: no label view, count or search without in:spam shows it.
    assert cid not in await _ids(client, view=f"label:{mine}")
    assert (await client.get("/api/v1/counts")).json()["views"].get(f"label:{mine}", {}).get("total", 0) == 0
    assert cid in await _ids(client, view="spam")
    async with sessionmaker()() as db:
        d = (await db.execute(select(LabelDecision).where(
            LabelDecision.message_id == mid, LabelDecision.source == "classifier"))).scalar_one()
        assert d.status == "withdrawn"

    await _act(client, cid, "not_spam")
    m = await _msg(mid)
    assert {mine, inbox} <= set(m.label_ids) and junk not in m.label_ids and machine not in m.label_ids
    assert m.spam_kept_labels is None
    assert cid in await _ids(client, view=f"label:{mine}")
    assert cid in await _ids(client, view="inbox")


async def test_undo_of_report_spam_and_of_not_spam(client, user, raw):
    mine = await _label(client, "Receipts")
    mid, cid = await _ingest(user, raw("Order 1234", frm="Shop <orders@shop.example>", date=_now_hdr()))
    await _act(client, cid, "add_labels", label_ids=[mine])
    junk = await _role(user.id, "junk")

    # Undo of Report spam: everything back, nothing left aside.
    undo = (await _act(client, cid, "spam"))["undo"]
    await _act(client, cid, undo["action"], **{k: v for k, v in undo.items() if k != "action"})
    m = await _msg(mid)
    assert mine in m.label_ids and junk not in m.label_ids and m.spam_kept_labels is None

    # Undo of Not spam: back in Spam with the label kept aside again; Not spam restores it again.
    await _act(client, cid, "spam")
    undo = (await _act(client, cid, "not_spam"))["undo"]
    await _act(client, cid, undo["action"], **{k: v for k, v in undo.items() if k != "action"})
    m = await _msg(mid)
    assert m.label_ids == [junk] and m.spam_kept_labels == [mine]
    await _act(client, cid, "not_spam")
    assert mine in (await _msg(mid)).label_ids

    # Moving it from Spam to the Inbox puts them back too.
    await _act(client, cid, "spam")
    await _act(client, cid, "move", label_ids=[await _role(user.id, "inbox")], from_label_id=junk)
    m = await _msg(mid)
    assert mine in m.label_ids and junk not in m.label_ids and m.spam_kept_labels is None

    # Trash from Spam drops the kept labels (Trash keeps no labels).
    await _act(client, cid, "spam")
    await _act(client, cid, "trash")
    assert (await _msg(mid)).spam_kept_labels is None


async def test_a_label_deleted_while_in_spam_is_skipped(client, user, raw):
    keep, gone = await _label(client, "Keep"), await _label(client, "Gone")
    mid, cid = await _ingest(user, raw("Hello", frm="Bo <bo@example.org>", date=_now_hdr()))
    await _act(client, cid, "add_labels", label_ids=[keep, gone])
    await _act(client, cid, "spam")
    assert set((await _msg(mid)).spam_kept_labels) == {keep, gone}
    assert (await client.delete(f"/api/v1/labels/{gone}")).status_code in (200, 204)
    await _act(client, cid, "not_spam")
    m = await _msg(mid)
    assert keep in m.label_ids and gone not in m.label_ids and m.spam_kept_labels is None


async def test_a_filters_spam_action_keeps_labels_aside_too(client, user, raw):
    from mailr.core.store import user_store
    from mailr.intel.classify import move_to_spam
    from mailr.models import User

    mine = await _label(client, "Newsletters")
    mid, cid = await _ingest(user, raw("Weekly", frm="News <news@example.org>", date=_now_hdr()),
                             label_names=["Newsletters"])
    junk = await _role(user.id, "junk")
    async with sessionmaker()() as db:
        store = await user_store(db, await db.get(User, user.id))
        m = await db.get(Message, mid)
        assert await move_to_spam(db, store, [m], junk) == {mid: [mine]}
        await db.commit()
    await _act(client, cid, "not_spam")
    assert mine in (await _msg(mid)).label_ids


async def _folder_count(c, folder):
    try:
        c.select_folder(folder, readonly=True)
    except Exception:  # noqa: BLE001  the folder does not exist
        return 0
    return len(c.search(["ALL"]))


@pytest.mark.timeout(180)
async def test_upstream_keeps_only_the_junk_copy_and_not_spam_writes_the_labels_back(client, user):
    email, pw = make_upstream_account()
    c = imap(email, pw)
    c.append("INBOX", msg("Mirror spam", msgid="<keep-spam-1@x.org>"))
    c.logout()
    conn = await create_connector(client, email, pw, mode="mirror")
    await run_sync(conn["id"])
    cid = next(iter(await _ids(client, view="inbox")))
    lb = await _label(client, "Clients")
    await _act(client, cid, "add_labels", label_ids=[lb])
    await run_jobs("connector.write_back", user.id)
    c = imap(email, pw)
    assert await _folder_count(c, "INBOX") == 1 and await _folder_count(c, "Clients") == 1
    c.logout()

    await _act(client, cid, "spam")
    await run_jobs("connector.write_back", user.id)
    c = imap(email, pw)
    folders = {f[2] for f in c.list_folders()}
    junk_folder = next(f for f in folders if f.lower() in ("junk", "junk mail", "spam"))
    assert await _folder_count(c, junk_folder) == 1
    assert await _folder_count(c, "INBOX") == 0
    assert await _folder_count(c, "Clients") == 0  # no copy that another mail app would show
    c.logout()
    # A sync of the account changes nothing: the label stays kept aside, not shown.
    await run_sync(conn["id"])
    assert cid not in await _ids(client, view=f"label:{lb}")
    assert cid in await _ids(client, view="spam")

    await _act(client, cid, "not_spam")
    await run_jobs("connector.write_back", user.id)
    c = imap(email, pw)
    assert await _folder_count(c, "INBOX") == 1
    assert await _folder_count(c, "Clients") == 1
    assert await _folder_count(c, junk_folder) == 0
    c.logout()
    await run_sync(conn["id"])
    assert cid in await _ids(client, view=f"label:{lb}")
    assert cid in await _ids(client, view="inbox")


async def test_gmail_keeps_its_labels_on_reported_spam(monkeypatch):
    """Gmail keeps a message's labels while it is in Spam and leaves spam out of its label
    folders itself: Report spam takes off only the Inbox there and copies to [Gmail]/Spam."""
    from types import SimpleNamespace

    from mailr.connectors.base import WriteBack
    from mailr.connectors.gmail import ALL_MAIL, GmailConnector

    calls: list[tuple[str, tuple]] = []

    class FakeClient:
        def __getattr__(self, name):
            return lambda *a: calls.append((name, a))

    class FakeSession:
        client = FakeClient()

        async def call(self, fn, *a, **kw):
            return fn(*a, **kw)

    row = SimpleNamespace(id=7, preset="gmail", config={"email": "a@gmail.example"}, mode="mirror", kind="gmail")
    conn = GmailConnector.__new__(GmailConnector)
    conn.row = row
    session = FakeSession()

    async def _open():
        return session

    async def _close(hard=False):
        return None

    async def folder_plan():
        return [("[Gmail]/All Mail", ALL_MAIL), ("[Gmail]/Spam", ("role", "junk"))]

    async def _select_ref(r):
        return True

    monkeypatch.setattr(conn, "_open", _open, raising=False)
    monkeypatch.setattr(conn, "_close", _close, raising=False)
    monkeypatch.setattr(conn, "folder_plan", folder_plan, raising=False)
    monkeypatch.setattr(conn, "_select_ref", _select_ref, raising=False)
    up = {"connector": 7, "folder": "[Gmail]/All Mail", "uidvalidity": 1, "uid": 42}
    await conn.write_back([WriteBack(upstream=up, add_labels=["junk"], remove_labels=["inbox", "Clients"])])
    assert ("remove_gmail_labels", ([42], ["\\Inbox"])) in calls
    assert not any(n == "remove_gmail_labels" and "Clients" in a[1] for n, a in calls)
    assert ("copy", ([42], "[Gmail]/Spam")) in calls
    calls.clear()
    # Not spam adds the label again (a no-op on Gmail, needed on IMAP servers).
    await conn.write_back([WriteBack(upstream=up, add_labels=["inbox", "Clients"], remove_labels=["junk"])])
    assert ("add_gmail_labels", ([42], ["\\Inbox", "Clients"])) in calls


# ---------------- the guard on expunging untracked label copies upstream ----------------


async def _mirror_with_label(client, user, body="Hello A", msgid=None):
    """A mirror account whose message mailr labelled "Clients" (mailr wrote the Clients copy)."""
    import uuid

    email, pw = make_upstream_account()
    msgid = msgid or f"<guard-{uuid.uuid4().hex[:10]}@x.org>"
    c = imap(email, pw)
    c.append("INBOX", msg("Guarded", msgid=msgid, body=body))
    c.logout()
    conn = await create_connector(client, email, pw, mode="mirror")
    await run_sync(conn["id"])
    cid = next(iter(await _ids(client, view="inbox")))
    lb = await _label(client, "Clients")
    await _act(client, cid, "add_labels", label_ids=[lb])
    await run_jobs("connector.write_back", user.id)
    async with sessionmaker()() as db:
        m = (await db.execute(select(Message).where(Message.conversation_id == cid))).scalar_one()
        assert m.upstream.get("label_copies") == ["Clients"]  # recorded: mailr wrote it
        mid = m.id
    return email, pw, conn, cid, mid, msgid


def _bodies(c, folder):
    c.select_folder(folder, readonly=True)
    uids = c.search(["ALL"])
    return sorted(d[b"BODY[]"] for d in c.fetch(uids, [b"BODY.PEEK[]"]).values()) if uids else []


async def test_guard_a_same_size_different_message_stays(client, user, caplog):
    email, pw, _conn, cid, _mid, msgid = await _mirror_with_label(client, user, body="Hello A")
    c = imap(email, pw)
    other = msg("Guarded", msgid=msgid, body="Hello B")  # same size and Message-ID, other content
    c.append("Clients", other)
    assert len(_bodies(c, "Clients")) == 2
    c.logout()
    await _act(client, cid, "spam")
    await run_jobs("connector.write_back", user.id)
    c = imap(email, pw)
    left = _bodies(c, "Clients")
    c.logout()
    assert len(left) == 1 and b"Hello B" in left[0]  # only mailr's own copy went


async def test_guard_two_identical_copies_expunge_nothing(client, user, caplog):
    caplog.set_level(logging.WARNING, logger="mailr.connectors.imap")
    email, pw, _conn, cid, mid, msgid = await _mirror_with_label(client, user)
    c = imap(email, pw)
    c.append("Clients", msg("Guarded", msgid=msgid, body="Hello A"))  # an identical second copy
    c.logout()
    await _act(client, cid, "spam")
    await run_jobs("connector.write_back", user.id)
    c = imap(email, pw)
    assert len(_bodies(c, "Clients")) == 2
    assert len(_bodies(c, "INBOX")) == 0
    c.logout()
    warn = [r.getMessage() for r in caplog.records if "label copy left upstream" in r.getMessage()]
    assert warn == [f"connector {_conn['id']}: message {mid}: label copy left upstream (2 copies match)"]
    assert "Guarded" not in warn[0] and msgid not in warn[0]


async def test_guard_without_a_content_hash_nothing_is_expunged(client, user, caplog):
    caplog.set_level(logging.WARNING, logger="mailr.connectors.imap")
    email, pw, _conn, cid, mid, _msgid = await _mirror_with_label(client, user)
    async with sessionmaker()() as db:
        (await db.get(Message, mid)).content_hash = None
        await db.commit()
    await _act(client, cid, "spam")
    await run_jobs("connector.write_back", user.id)
    c = imap(email, pw)
    assert len(_bodies(c, "Clients")) == 1
    c.logout()
    assert any("no content hash" in r.getMessage() for r in caplog.records)


async def test_guard_only_folders_mailr_wrote_and_never_system_folders(client, user):
    from mailr.connectors.loader import connector_for
    from mailr.models import Connector

    email, pw, conn, cid, mid, _msgid = await _mirror_with_label(client, user)
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        m.upstream = {k: v for k, v in m.upstream.items() if k != "label_copies"}  # as if another app wrote it
        await db.commit()
    await _act(client, cid, "spam")
    await run_jobs("connector.write_back", user.id)
    c = imap(email, pw)
    assert len(_bodies(c, "Clients")) == 1  # not a folder mailr wrote it to: left alone
    c.logout()
    async with sessionmaker()() as db:
        imap_conn = await connector_for(db, await db.get(Connector, conn["id"]))
    try:
        roles = await imap_conn._role_folders()
    finally:
        await imap_conn._close()
    assert {"INBOX", "Sent Items", "Drafts", "Junk Mail", "Deleted Items"} <= roles


async def test_moving_into_spam_is_report_spam(client, user, raw):
    """Move to Spam, a drop on Spam and add_labels with the Spam label go through Report spam."""
    mine = await _label(client, "Projects")
    machine = await _label(client, "Machine")
    junk = await _role(user.id, "junk")
    for how in ("move", "add_labels"):
        mid, cid = await _ingest(user, raw(f"Plan via {how}", frm="Ann <ann@example.org>", date=_now_hdr()))
        await _act(client, cid, "add_labels", label_ids=[mine])
        await _classifier_applied(user.id, mid, machine)
        undo = (await _act(client, cid, how, label_ids=[junk]))["undo"]
        m = await _msg(mid)
        assert m.label_ids == [junk] and m.spam_kept_labels == [mine], how
        async with sessionmaker()() as db:
            assert (await db.execute(select(LabelDecision).where(
                LabelDecision.message_id == mid, LabelDecision.kind == "spam",
                LabelDecision.source == "user"))).scalars().first() is not None, how  # trained as Report spam
        assert undo["action"] == "restore_labels"
        await _act(client, cid, "not_spam")
        m = await _msg(mid)
        assert mine in m.label_ids and machine not in m.label_ids and junk not in m.label_ids, how
