"""A message held by several accounts: mailr's state stands.

1 Oct 2026: a hosted IMAP account (store mode) also forwards to Gmail, so one message arrived twice, from
the IMAP INBOX and from Gmail's All Mail. Deleted mail and notes to self that a filter had
moved to Later came back to the Inbox: the Gmail copy re-imposed Gmail's state. These tests build
that message (a store-mode primary plus a Gmail copy) and act on it in mailr."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from mailr.connectors.gmail import GmailConnector
from mailr.connectors.imap import all_refs, refs_to_upstream
from mailr.connectors.runner import RunnerContext
from mailr.db import sessionmaker
from mailr.models import AuditLog, Connector, Label, Message

GM = "[Gmail]/All Mail"


async def _connector(user, kind: str, mode: str, state: dict | None = None) -> int:
    async with sessionmaker()() as db:
        c = Connector(user_id=user.id, kind=kind, name=f"{kind}-{mode}", mode=mode, enabled=True,
                      config={"email": "x@example.org", "imap": {"host": "localhost", "port": 1, "tls": "none"}},
                      state=state or {})
        db.add(c)
        await db.commit()
        return c.id


async def _arrive(cid: int, user, data: bytes, **kw) -> dict:
    return await RunnerContext(cid, user.id, {}).ingest(data, **kw)


def _store_kw(cid: int, uid: int, roles=("inbox",)) -> dict:
    return {"roles": list(roles), "source": "connector",
            "upstream": {"connector": cid, "folder": "INBOX", "uidvalidity": 1, "uid": uid}}


def _gmail_kw(cid: int, uid: int, mode: str, roles=("inbox",), names=()) -> dict:
    return {"roles": list(roles), "label_names": list(names), "source": "import" if mode == "import" else "connector",
            "merge_labels": mode == "mirror", "state_authoritative": mode == "import", "run_rules": mode != "import",
            "upstream": {"connector": cid, "folder": GM, "uidvalidity": 12, "uid": uid}}


async def _labels(user) -> dict[str, int]:
    async with sessionmaker()() as db:
        rows = (await db.execute(select(Label).where(Label.user_id == user.id))).scalars()
        return {(lb.role or lb.name): lb.id for lb in rows}


async def _message(mid: int) -> Message:
    async with sessionmaker()() as db:
        return await db.get(Message, mid)


async def _write_back_jobs(user) -> list[dict]:
    async with sessionmaker()() as db:
        return [r[0] for r in (await db.execute(text(
            "SELECT payload FROM jobs WHERE kind='connector.write_back' AND user_id=:u AND status='queued' ORDER BY id"),
            {"u": user.id})).all()]


async def _pair(client, user, raw, subject="Discover Asia's Hidden Gems"):
    """The incident's shape: the IMAP account delivers first, the Gmail mirror's copy second."""
    store = await _connector(user, "imap", "store")
    mirror = await _connector(user, "gmail", "mirror")
    data = raw(subject)
    first = await _arrive(store, user, data, **_store_kw(store, 227))
    second = await _arrive(mirror, user, data, **_gmail_kw(mirror, 78398, "mirror"))
    assert second["duplicate"] and second["message_id"] == first["message_id"]
    return store, mirror, first["message_id"], first["conversation_id"]


# ACT mail.delete, ACT mail.archive, ACT mail.label
async def test_a_change_is_written_back_to_every_mirror_copy(client, user, raw, monkeypatch):
    store, mirror, mid, cid = await _pair(client, user, raw)
    r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "trash"})
    assert r.status_code == 200, r.text
    jobs = await _write_back_jobs(user)
    # The store-mode account gets no write-back; the Gmail copy does, although Gmail was second.
    assert [(j["connector_id"], j["message_ids"]) for j in jobs] == [(mirror, [mid])]
    assert jobs[0]["change"] == {"add_labels": ["trash"], "remove_labels": ["inbox"]}

    seen = []

    async def fake_write_back(self, changes):
        seen.extend(changes)
        return [refs_to_upstream([r for r in all_refs(ch.upstream) if str(r["connector"]) == str(self.row.id)],
                                 keep=ch.upstream) for ch in changes]

    monkeypatch.setattr(GmailConnector, "write_back", fake_write_back)
    from helpers.connectors import run_jobs

    assert await run_jobs("connector.write_back", user.id) == 1
    assert len(seen) == 1 and "trash" in seen[0].add_labels
    # The IMAP ref is still there after the Gmail write-back recorded its own refs.
    m = await _message(mid)
    assert {(str(r["connector"]), r["folder"]) for r in all_refs(m.upstream)} == {(str(store), "INBOX"), (str(mirror), GM)}
    assert m.connector_id == store and m.local_change_at is not None


async def test_an_older_mirror_state_does_not_undo_a_local_change(client, user, raw):
    _store, mirror, mid, cid = await _pair(client, user, raw)
    lb = await _labels(user)
    later = (await client.post("/api/v1/labels", json={"name": "Later"})).json()["id"]
    await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "archive"})
    await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "add_labels", "label_ids": [later]})
    async with sessionmaker()() as db:  # the write-backs ran
        await db.execute(text("UPDATE jobs SET status='done' WHERE user_id=:u"), {"u": user.id})
        await db.commit()
    # Gmail still has it in the Inbox, unread, without Later; the pass started before the change.
    ctx = RunnerContext(mirror, user.id, {})
    await ctx.apply_remote(GM, 12, {78398: {"seen": False, "label_state": {
        "roles": ["inbox"], "label_names": [], "mapped": []}}}, since=datetime.now(UTC) - timedelta(minutes=5))
    m = await _message(mid)
    assert lb["inbox"] not in m.label_ids and later in m.label_ids


async def test_a_newer_mirror_state_applies_and_mailr_only_labels_stay(client, user, raw):
    _store, mirror, mid, cid = await _pair(client, user, raw)
    lb = await _labels(user)
    later = (await client.post("/api/v1/labels", json={"name": "Later"})).json()["id"]
    old = (await client.post("/api/v1/labels", json={"name": "Old trip"})).json()["id"]
    await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "archive"})
    await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "add_labels",
                                                             "label_ids": [later, old]})
    async with sessionmaker()() as db:
        await db.execute(text("UPDATE jobs SET status='done' WHERE user_id=:u"), {"u": user.id})
        await db.execute(text("UPDATE messages SET local_change_at = now() - interval '1 hour' WHERE id=:i"), {"i": mid})
        await db.commit()
    # Since then the user moved it back to the Gmail inbox, labelled it Receipts there and took
    # Old trip off. Gmail has the labels Receipts and Old trip; Later exists only in mailr.
    ctx = RunnerContext(mirror, user.id, {})
    await ctx.apply_remote(GM, 12, {78398: {"seen": True, "label_state": {
        "roles": ["inbox"], "label_names": ["Receipts"], "mapped": ["Receipts", "Old trip"]}}},
        since=datetime.now(UTC) - timedelta(minutes=10))
    m = await _message(mid)
    names = {v: k for k, v in (await _labels(user)).items()}
    got = {names[i] for i in m.label_ids}
    assert {"inbox", "Receipts", "Later"} <= got and "Old trip" not in got and lb["archive"] not in m.label_ids
    assert m.seen is True


async def test_a_queued_write_back_holds_the_mirror_state_back(client, user, raw):
    _store, mirror, mid, cid = await _pair(client, user, raw)
    lb = await _labels(user)
    await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "archive"})
    async with sessionmaker()() as db:
        await db.execute(text("UPDATE messages SET local_change_at = now() - interval '1 hour' WHERE id=:i"), {"i": mid})
        await db.commit()
    assert await _write_back_jobs(user)  # still queued
    await RunnerContext(mirror, user.id, {}).apply_remote(GM, 12, {78398: {"label_state": {
        "roles": ["inbox"], "label_names": [], "mapped": []}}}, since=datetime.now(UTC) - timedelta(minutes=10))
    assert lb["inbox"] not in (await _message(mid)).label_ids


async def test_a_second_accounts_copy_never_brings_mail_back(client, user, raw):
    """Trashed, archived, and filtered out of the Inbox: the Gmail copy (mirror or import) that
    arrives afterwards changes none of it."""
    store = await _connector(user, "imap", "store")
    mirror = await _connector(user, "gmail", "mirror")
    imported = await _connector(user, "gmail", "import", state={"initial_done": True})
    lb = await _labels(user)
    for n, (action, mode) in enumerate((("trash", "mirror"), ("archive", "mirror"), ("trash", "import"),
                                        ("archive", "import"))):
        data = raw(f"Trip {n}")
        first = await _arrive(store, user, data, **_store_kw(store, 300 + n))
        await client.post("/api/v1/conversations/actions", json={"ids": [first["conversation_id"]], "action": action})
        cid = mirror if mode == "mirror" else imported
        again = await _arrive(cid, user, data, **_gmail_kw(cid, 900 + n, mode, names=["Travel"]))
        assert again["duplicate"]
        m = await _message(first["message_id"])
        assert lb["inbox"] not in m.label_ids, (action, mode)
        if action == "trash":
            assert lb["trash"] in m.label_ids
        assert any(str(r["connector"]) == str(cid) for r in all_refs(m.upstream))


async def test_a_note_to_self_keeps_its_filter_when_the_gmail_copy_arrives(client, user, raw):
    """Rule 2 of the user's layout (from:me to:me → Later, skip the Inbox), then the copy that
    the IMAP provider forwarded to Gmail."""
    store = await _connector(user, "imap", "store")
    mirror = await _connector(user, "gmail", "mirror")
    later = (await client.post("/api/v1/labels", json={"name": "Later"})).json()["id"]
    r = await client.post("/api/v1/rules", json={"name": "Notes to self", "query": "from:me to:me",
                                                  "actions": {"archive": True, "add_labels": [later]}})
    assert r.status_code in (200, 201), r.text
    lb = await _labels(user)
    data = raw("Screenshot (Oct 1, 2026 22:30:23)", frm=f"Me <{user.email}>", to=user.email)
    first = await _arrive(store, user, data, **{**_store_kw(store, 230, roles=("inbox", "sent")), "run_rules": True})
    m = await _message(first["message_id"])
    assert later in m.label_ids and lb["inbox"] not in m.label_ids and m.local_change_at is not None
    await _arrive(mirror, user, data, **_gmail_kw(mirror, 78401, "mirror", roles=("inbox", "sent")))
    m = await _message(first["message_id"])
    assert later in m.label_ids and lb["inbox"] not in m.label_ids
    # A later Gmail flag change (read on the phone) keeps Later, which Gmail does not have.
    async with sessionmaker()() as db:
        await db.execute(text("UPDATE messages SET local_change_at = now() - interval '1 hour' WHERE id=:i"),
                         {"i": first["message_id"]})
        await db.commit()
    await RunnerContext(mirror, user.id, {}).apply_remote(GM, 12, {78401: {"seen": True, "label_state": {
        "roles": ["sent"], "label_names": [], "mapped": []}}}, since=datetime.now(UTC) - timedelta(minutes=10))
    m = await _message(first["message_id"])
    assert later in m.label_ids and m.seen


async def test_a_finished_import_does_not_pull_new_mail(app, user, monkeypatch):
    cid = await _connector(user, "gmail", "import", state={"initial_done": True})
    async with sessionmaker()() as db:
        row = await db.get(Connector, cid)
    conn = GmailConnector(row, {"password": "x"})
    calls = []

    async def fake_sync(ctx):
        calls.append(1)
        ctx.stopping.set()
        return {"new": 0, "duplicates": 0}

    async def fake_open():
        return None

    monkeypatch.setattr(conn, "sync_once", fake_sync)
    monkeypatch.setattr(conn, "_open", fake_open)
    ctx = RunnerContext(cid, user.id, {"initial_done": True})
    ctx.stopping.set()
    await asyncio.wait_for(conn.run(ctx), 5)
    assert calls == []
    async with sessionmaker()() as db:
        assert (await db.get(Connector, cid)).status_detail == "Import finished"
    # Import again: one more pass when the user asks for it.
    ctx = RunnerContext(cid, user.id, {"initial_done": True, "import_again": True})
    await asyncio.wait_for(conn.run(ctx), 5)
    assert calls == [1] and "import_again" not in ctx.state


async def test_import_again_is_set_by_sync(client, user):
    cid = await _connector(user, "gmail", "import", state={"initial_done": True})
    assert (await client.post(f"/api/v1/connectors/{cid}/sync")).status_code == 200
    async with sessionmaker()() as db:
        assert (await db.get(Connector, cid)).state.get("import_again") is True


async def test_reconcile_skips_messages_changed_in_mailr(client, user, raw):
    imported = await _connector(user, "gmail", "import", state={"initial_done": True})
    lb = await _labels(user)
    data = raw("Old booking")
    first = await _arrive(imported, user, data, **_gmail_kw(imported, 5, "import", roles=("inbox",)))
    await client.post("/api/v1/conversations/actions", json={"ids": [first["conversation_id"]], "action": "archive"})
    n = await RunnerContext(imported, user.id, {}).apply_provider(GM, 12, {5: {
        "roles": ["inbox"], "label_names": [], "keywords": {}, "category": "primary"}})
    assert n == 0 and lb["inbox"] not in (await _message(first["message_id"])).label_ids


async def test_removing_an_import_account_keeps_its_mail(client, user, raw):
    store = await _connector(user, "imap", "store")
    imported = await _connector(user, "gmail", "import", state={"initial_done": True})
    shared = raw("Held by both")
    a = await _arrive(store, user, shared, **_store_kw(store, 1))
    await _arrive(imported, user, shared, **_gmail_kw(imported, 10, "import"))
    only = await _arrive(imported, user, raw("Only in Gmail"), **_gmail_kw(imported, 11, "import"))
    # An import whose message was first: the store copy came second.
    late = raw("Gmail first")
    b = await _arrive(imported, user, late, **_gmail_kw(imported, 12, "import"))
    await _arrive(store, user, late, **_store_kw(store, 2))

    assert (await client.delete(f"/api/v1/connectors/{imported}")).status_code == 200
    from helpers.connectors import run_jobs

    assert await run_jobs("connector.forget_refs", user.id) == 1
    m = await _message(a["message_id"])
    assert not m.deleted and m.connector_id == store
    assert [str(r["connector"]) for r in all_refs(m.upstream)] == [str(store)]
    m = await _message(only["message_id"])
    assert not m.deleted and m.connector_id is None and all_refs(m.upstream) == []
    m = await _message(b["message_id"])
    assert not m.deleted and m.connector_id == store and m.upstream["folder"] == "INBOX"
    assert [str(r["connector"]) for r in all_refs(m.upstream)] == [str(store)]
    listed = (await client.get("/api/v1/conversations", params={"view": "all"})).json()["items"]
    assert len(listed) == 3


async def test_a_single_action_is_audited(client, user, raw):
    _store, _mirror, mid, cid = await _pair(client, user, raw)
    assert (await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "trash"})).status_code == 200
    async with sessionmaker()() as db:
        rows = (await db.execute(select(AuditLog).where(AuditLog.user_id == user.id,
                                                        AuditLog.action == "conversations.trash"))).scalars().all()
    assert len(rows) == 1
    a = rows[0]
    assert a.actor == "user" and a.token_id is None and a.inputs["ids"] == [cid]
    assert a.result["affected"] == 1 and a.result["messages"] == [mid]

