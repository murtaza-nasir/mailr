"""Regression tests for hardening: visibility of local-only
mail to tokens, idempotency, outbound guard, safe inline images, search binds, dedupe by
content, spoofed From, store sync after a restart, send/undo race, poison messages."""

import uuid

from helpers.audit_fixes import _conn, _ingest, _token
from sqlalchemy import select, update

from mailr.core.store import user_store
from mailr.db import session_scope, sessionmaker
from mailr.models import Connector, Message, Outbox, User


async def test_local_only_invisible_to_hosted_tokens(client, user, raw):
    normal = await _conn(user.id)
    uni = await _conn(user.id, "local_only")
    _, c1, _ = await _ingest(user, raw("Weekend plans", msgid="<p1@x.org>", body="normal body"), normal)
    # A reply in the same thread that arrived on the local-only (university) account.
    _, c2, _ = await _ingest(user, raw("Re: Weekend plans", frm="Dean <dean@uni.edu>", msgid="<p2@x.org>",
                                       irt="<p1@x.org>", body="FERPA student record 12345"), uni)
    assert c1 == c2
    hosted = await _token(client, ["read"])
    lst = (await hosted.get("/api/v1/conversations", params={"view": "all"})).json()
    conv = next(c for c in lst["items"] if c["id"] == c1)
    assert "FERPA" not in conv["snippet"] and conv["message_count"] == 1
    assert "dean@uni.edu" not in [p["email"] for p in conv["participants"]]
    full = (await hosted.get(f"/api/v1/conversations/{c1}")).json()
    assert len(full["messages"]) == 1 and "FERPA" not in full["conversation"]["snippet"]
    assert (await hosted.post("/api/v1/rules/preview", json={"query": "from:dean"})).json()["count"] == 0
    assert (await hosted.get("/api/v1/people/dean@uni.edu")).status_code == 404
    assert "dean@uni.edu" not in [p["email"] for p in (await hosted.get("/api/v1/people", params={"q": "dean"})).json()]
    labels = {lb["role"]: lb for lb in (await hosted.get("/api/v1/labels")).json() if lb["role"]}
    assert labels["inbox"]["total"] == 1
    # A local-only token sees everything; deleting the connector keeps the mail local-only.
    local = await _token(client, ["read"], local_only=True)
    assert len((await local.get(f"/api/v1/conversations/{c1}")).json()["messages"]) == 2
    async with session_scope() as db:
        await db.delete(await db.get(Connector, uni))
    full = (await hosted.get(f"/api/v1/conversations/{c1}")).json()
    assert len(full["messages"]) == 1
    await hosted.aclose()
    await local.aclose()


async def test_idempotency_replay(client, user):
    h = {"Idempotency-Key": "k-" + uuid.uuid4().hex}
    r1 = await client.post("/api/v1/labels", json={"name": "Receipts"}, headers=h)
    r2 = await client.post("/api/v1/labels", json={"name": "Receipts"}, headers=h)
    assert r1.status_code == r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    assert r2.headers.get("idempotent-replayed") == "true"
    # Same key, different endpoint: independent.
    r3 = await client.post("/api/v1/labels", json={"name": "Other"}, headers={**h, "X-Unused": "1"})
    assert r3.json()["id"] == r1.json()["id"]  # same path+key+caller: replay
    r4 = await client.patch(f"/api/v1/labels/{r1.json()['id']}", json={"color": "#ff0000"}, headers=h)
    assert r4.status_code == 200 and r4.headers.get("idempotent-replayed") is None


async def test_outbound_guard(monkeypatch):
    import pytest

    from mailr.core import netguard

    netguard._allow_list.cache_clear()
    monkeypatch.setattr(netguard.get_settings(), "outbound_allow", "")
    netguard._allow_list.cache_clear()
    for url in ("http://169.254.169.254/latest/meta-data", "http://127.0.0.1:8080/", "http://[::1]/",
                "ftp://example.com/", "http://user:pw@example.com/"):
        with pytest.raises(netguard.UnsafeTarget):
            netguard.check_url_sync(url)
    assert netguard.same_origin("https://ntfy.example.com/t", "https://ntfy.example.com")
    assert not netguard.same_origin("https://ntfy.example.com.evil.net/t", "https://ntfy.example.com")
    netguard._allow_list.cache_clear()


async def test_inline_svg_is_not_served_inline(client, user):
    raw = (b"From: a@b.c\r\nTo: me@x.org\r\nSubject: svg\r\nMessage-ID: <svg1@x.org>\r\nMIME-Version: 1.0\r\n"
           b"Content-Type: multipart/related; boundary=B\r\n\r\n--B\r\nContent-Type: text/html\r\n\r\n"
           b"<img src=\"cid:x\">\r\n--B\r\nContent-Type: image/svg+xml\r\nContent-ID: <x>\r\n\r\n"
           b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>\r\n--B--\r\n")
    mid, _, _ = await _ingest(user, raw)
    r = await client.get(f"/api/v1/messages/{mid}/cid/x")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers.get("content-disposition") == "attachment"
    assert "sandbox" in r.headers["content-security-policy"]


async def test_search_multi_operator_binds(client, user, raw):
    await _ingest(user, raw("A", to="alice@x.org", extra="Cc: bob@x.org"))
    await _ingest(user, raw("B", to="bob@x.org", extra="Cc: alice@x.org"))
    r = (await client.get("/api/v1/conversations", params={"view": "all", "q": "to:alice cc:bob"})).json()
    assert [c["subject"] for c in r["items"]] == ["A"]


async def test_reused_message_id_is_not_a_duplicate(client, user, raw):
    m1, _, d1 = await _ingest(user, raw("Ticket", msgid="<same@x.org>", body="First ticket about printers"))
    m2, _, d2 = await _ingest(user, raw("Ticket", msgid="<same@x.org>", body="Completely different text"))
    assert not d1 and not d2 and m1 != m2
    # A real duplicate (same content, different transport headers) is merged.
    m3, _, d3 = await _ingest(user, b"Delivered-To: other@x.org\r\n" + raw("Ticket", msgid="<same@x.org>",
                                                                            body="First ticket about printers"))
    assert d3 and m3 == m1


async def test_spoofed_from_me_is_inbound(client, user, raw):
    me = user.email
    mid, _, _ = await _ingest(user, raw("Pay this", frm=me, to="attacker@evil.org",
                                        extra="Authentication-Results: mx.example; dmarc=fail"))
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        assert not m.sent_by_me
    inbox = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()
    assert "Pay this" in [c["subject"] for c in inbox["items"]]
    # Authenticated mail from my own address is mine.
    mid2, _, _ = await _ingest(user, raw("Note to self", frm=me, to="someone@x.org",
                                         extra=f"Authentication-Results: mx.example; dkim=pass; dmarc=pass header.from={me.split('@')[1]}"))
    async with sessionmaker()() as db:
        assert (await db.get(Message, mid2)).sent_by_me


async def test_store_sync_survives_restart(client, user, raw, monkeypatch):
    """A message added straight to the store (an IMAP client) while mailr was not watching is
    indexed on the next start, from the persisted cursor or the reconcile."""
    from mailr.core import storesync

    monkeypatch.setattr(storesync, "GRACE_SECONDS", 0)
    s1 = storesync._UserSync(user.id)
    await s1.tick()  # first run: cursor + reconcile
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        store = await user_store(db, u)
        inbox = await store.role_label(db, "inbox")
        up = await store.jmap.upload(raw("From the phone", msgid="<phone1@x.org>"))
        await store.jmap.one("Email/import", {"emails": {"e": {"blobId": up["blobId"], "mailboxIds": {inbox.jmap_id: True}}}})
    s2 = storesync._UserSync(user.id)  # a restarted worker: nothing in memory
    await s2.tick()
    await s2.tick()
    async with sessionmaker()() as db:
        found = (await db.execute(select(Message.id).where(Message.user_id == user.id,
                                                            Message.message_id_hdr == "<phone1@x.org>"))).all()
    assert len(found) == 1


async def test_undo_loses_to_a_claimed_send(client, user):
    async with session_scope() as db:
        from mailr.models import Identity

        c = Connector(user_id=user.id, kind="imap", name="s", mode="mirror", config={"smtp": {"host": "x"}},
                      state={}, enabled=False)
        db.add(c)
        await db.flush()
        db.add(Identity(user_id=user.id, email="me@example.com", connector_id=c.id, is_default=True))
    r = await client.post("/api/v1/send", json={"payload": {"to": [{"email": "a@b.org", "name": ""}],
                                                            "subject": "x", "text": "y"}})
    oid = r.json()["outbox"]["id"]
    async with session_scope() as db:  # the worker claimed it
        await db.execute(update(Outbox).where(Outbox.id == oid).values(status="sending"))
    r = await client.post(f"/api/v1/outbox/{oid}/cancel")
    assert r.status_code == 409
