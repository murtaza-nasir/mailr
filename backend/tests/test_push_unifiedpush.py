"""UnifiedPush to the Android app (3 Oct 2026): what each event's JSON holds, the local-only rule,
the size bound, encryption for the phone's Web Push keys, the target API with keys, the test
button's real result, and the outbound guard for an ntfy server on a private address."""

import base64
import json
import socket
import uuid

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from helpers.agent import _ingest, _setup_identity
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import Message, Outbox, PushTarget


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _phone_keys():
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = uuid.uuid4().bytes
    return key, auth, {"p256dh": _b64(pub), "auth": _b64(auth)}


@pytest.fixture
def sent(monkeypatch):
    """Every POST core/push.py makes, answered 200 (or `sent.status`)."""
    from mailr.core import push

    calls: list[dict] = []

    class FakeClient:
        status = 200

        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, content=None, headers=None):
            calls.append({"url": url, "content": content, "headers": headers})
            return httpx.Response(FakeClient.status)

    from mailr.core import netguard

    async def fetch_capped(client, method, url, *, limit, deadline=30.0, content=None, headers=None, **kw):
        # push.py posts through netguard (pinned client, capped answer): answer here instead.
        r = await FakeClient().post(url, content=content, headers=headers)
        return r, b""

    monkeypatch.setattr(netguard, "fetch_capped", fetch_capped)
    assert push  # the module under test
    from types import SimpleNamespace

    return SimpleNamespace(calls=calls, fake=FakeClient)


async def _target(user, endpoint=None, events=None, keys=None) -> int:
    async with sessionmaker()() as db:
        t = PushTarget(user_id=user.id, kind="unifiedpush", endpoint=endpoint or f"http://ntfy.invalid/up{uuid.uuid4().hex[:12]}?up=1",
                       events=events if events is not None else ["new_mail", "important_mail", "approval", "send_failed", "assistant"],
                       p256dh=(keys or {}).get("p256dh"), auth=(keys or {}).get("auth"))
        db.add(t)
        await db.commit()
        return t.id


async def _mail_level(user, level: str) -> None:
    """Settings → Notifications → New mail (core/notifyprefs.py): one level for every device."""
    async with sessionmaker()() as db:
        u = await db.get(type(user), user.id)
        u.settings = {**(u.settings or {}), "mail_notifications": level}
        await db.commit()


async def test_new_mail_json_is_minimal_and_local_only_says_nothing(client, user, raw, sent):
    """new_mail / important_mail: title (sender or "mailr"), body "New message", the ids, the
    inbox link and the type. Never a subject or body; a local-only account not even the sender."""
    from mailr.core import push

    await _target(user)
    await _mail_level(user, "all")
    conn_id, me = await _setup_identity(user, data_class="local_only")
    mid, cid = await _ingest(user.id, raw("Grade question", frm="Student <s2@uni.example>", to=me,
                                          body="PRIVATE-BODY"), connector_id=conn_id)
    nid, ncid = await _ingest(user.id, raw("Lunch plans", frm="Carol Diaz <carol@example.org>", to=me, body="PRIVATE-2"))
    async with sessionmaker()() as db:
        for x in (mid, nid):
            (await db.get(Message, x)).intel_state = "done"
        (await db.get(Message, nid)).important = True
        await db.commit()
    await push.push_new_mail({"message_id": mid}, None)
    await push.push_new_mail({"message_id": nid}, None)
    assert len(sent.calls) == 2
    docs = [json.loads(c["content"]) for c in sent.calls]
    assert all(c["headers"]["Content-Type"] == "application/json" for c in sent.calls)
    assert set(docs[0]) == {"title", "body", "conversation_id", "message_id", "click", "type"}
    assert docs[0]["title"] == "Mailr" and docs[0]["body"] == "New message" and docs[0]["type"] == "new_mail"
    assert docs[1]["title"] == "Mail from Carol Diaz" and docs[1]["type"] == "important_mail"
    assert sent.calls[1]["headers"]["Urgency"] == "high"
    for c in sent.calls:
        for secret in (b"PRIVATE", b"Grade question", b"Lunch plans", b"s2@uni.example"):
            assert secret not in c["content"]
    # The ids are the ones the app reads back from /conversations (public ids when they are on).
    from mailr.core import publicid

    assert docs[0]["conversation_id"] == publicid.out({"conversation_id": cid})["conversation_id"]
    assert docs[1]["message_id"] == publicid.out({"message_id": nid})["message_id"]


async def test_approval_and_send_failed_json(user, sent):
    """approval (legacy outbox queue) and send_failed: title, body, outbox_id, click, type."""
    from datetime import UTC, datetime

    from mailr.core import push
    from mailr.models import Identity

    await _target(user)
    await _setup_identity(user)
    async with sessionmaker()() as db:
        ident = (await db.execute(select(Identity).where(Identity.user_id == user.id))).scalars().first()
        ob = Outbox(user_id=user.id, identity_id=ident.id, payload={}, send_at=datetime.now(UTC),
                    recipients=["dana@example.org", "eli@example.org"], status="awaiting_approval",
                    approval_reason="new recipient", error="550 mailbox unavailable")
        db.add(ob)
        await db.commit()
        oid = ob.id
    await push.push_approval({"outbox_id": oid}, None)
    await push.push_send_failed({"outbox_id": oid}, None)
    a, f = (json.loads(c["content"]) for c in sent.calls)
    assert a["type"] == "approval" and a["outbox_id"] == oid and a["title"] == "Approve an agent's email"
    assert a["body"].startswith("To dana@example.org and 1 more.")
    assert f == {"title": "Message not sent", "body": "To dana@example.org, eli@example.org: 550 mailbox unavailable",
                 "outbox_id": oid, "click": f["click"], "type": "send_failed"}
    assert all(c["headers"]["Urgency"] == "high" for c in sent.calls)


async def test_uncertain_send_has_its_own_words(user, sent):
    """A send that may have gone out is not reported as "not sent": the push says to check Sent
    first (also as the fixed text of a local-only account)."""
    from datetime import UTC, datetime

    from mailr.core import push
    from mailr.models import Identity

    await _target(user)
    await _setup_identity(user)
    async with sessionmaker()() as db:
        ident = (await db.execute(select(Identity).where(Identity.user_id == user.id))).scalars().first()
        ob = Outbox(user_id=user.id, identity_id=ident.id, payload={}, send_at=datetime.now(UTC),
                    recipients=["dana@example.org"], status="uncertain", error="the connection dropped")
        db.add(ob)
        await db.commit()
        oid = ob.id
    await push.push_send_failed({"outbox_id": oid}, None)
    f = json.loads(sent.calls[0]["content"])
    assert f["title"] == "May have been sent" and f["type"] == "send_failed"
    assert f["body"] == "To dana@example.org: check Sent before sending again."
    assert push.private_text("send_uncertain") == ("May have been sent", "Check Sent in Mailr before sending again.")


async def test_targets_get_only_the_events_they_list(user, sent):
    """A device narrows the account's choice: failed sends and account notices ride with
    approvals; the assistant only when listed; reminders are opt-in."""
    from mailr.core import push

    await _target(user, events=["approval"])
    async with sessionmaker()() as db:
        assert await push.fan_out(db, user.id, "assistant", "x", "y", {"run_id": 1}) == 0
        assert await push.fan_out(db, user.id, "approval", "x", "y", {"pending_id": 7, "action": "mail.send"}) == 1
        assert await push.fan_out(db, user.id, "send_failed", "x", "y", {"outbox_id": 1}) == 1
        assert await push.fan_out(db, user.id, "account", "x", "y", {"connector_id": 1}) == 1
        assert await push.fan_out(db, user.id, "reminder", "x", "y", {"key": "k"}) == 0  # opt-in only
    assert json.loads(sent.calls[0]["content"]) == {"title": "x", "body": "y", "pending_id": 7, "action": "mail.send",
                                                    "type": "approval"}


async def test_account_settings_turn_kinds_off_for_every_device(user, sent):
    """notify_approvals / notify_assistant / notify_accounts (core/notifyprefs.py) stop a
    kind on every device at once."""
    from mailr.core import push

    await _target(user, events=[])
    async with sessionmaker()() as db:
        u = await db.get(type(user), user.id)
        u.settings = {**(u.settings or {}), "notify_approvals": False, "notify_assistant": False}
        await db.commit()
        assert await push.fan_out(db, user.id, "approval", "x", "y", {"pending_id": 7}) == 0
        assert await push.fan_out(db, user.id, "send_failed", "x", "y", {"outbox_id": 1}) == 0
        assert await push.fan_out(db, user.id, "assistant", "x", "y", {"run_id": 1}) == 0
        assert await push.fan_out(db, user.id, "account", "x", "y", {"connector_id": 1}) == 1


@pytest.mark.parametrize("level,expect", [
    ("off", []), ("vip", ["vip"]), ("important", ["important", "vip"]),
    ("primary", ["important", "vip", "primary"]), ("all", ["important", "vip", "primary", "promo"]),
])
async def test_one_mail_level_for_every_device(user, raw, sent, level, expect):
    """The account's mail level decides for the phone, every browser and the open windows alike."""
    from mailr.core import push

    await _target(user)
    _conn, me = await _setup_identity(user)
    async with sessionmaker()() as db:
        u = await db.get(type(user), user.id)
        u.settings = {**(u.settings or {}), "mail_notifications": level, "vip": ["vip@example.org"]}
        await db.commit()
    for kind, frm in (("important", "a@example.org"), ("vip", "vip@example.org"), ("primary", "b@example.org"),
                      ("promo", "c@example.org")):
        mid, _ = await _ingest(user.id, raw(f"Note {kind}", frm=f"X <{frm}>", to=me, body="b"))
        async with sessionmaker()() as db:
            m = await db.get(Message, mid)
            m.intel_state = "done"
            m.important = kind == "important"
            m.category = "promotions" if kind == "promo" else None
            await db.commit()
        await push.push_new_mail({"message_id": mid}, None)
    assert len(sent.calls) == len(expect), level


def test_payload_never_exceeds_the_limit():
    from mailr.core.push import UP_MAX_BYTES, up_payload

    raw = up_payload("A" * 5000, "ü" * 5000, {"conversation_id": 4503599627370497, "message_id": 4503599627370498,
                                             "click": "https://mail.example.com/" + "x" * 4000, "type": "new_mail"})
    doc = json.loads(raw)
    assert len(raw) <= UP_MAX_BYTES
    assert len(doc["title"]) == 120 and doc["type"] == "new_mail" and doc["conversation_id"] == 4503599627370497
    small = json.loads(up_payload("Carol", "New message", {"type": "new_mail", "click": "https://m/x"}))
    assert small == {"title": "Carol", "body": "New message", "type": "new_mail", "click": "https://m/x"}


async def test_encrypted_for_the_phone_when_it_gave_keys(user, sent):
    """aes128gcm (RFC 8291) for the keys the UnifiedPush connector made; the push server sees ciphertext."""
    import http_ece

    from mailr.core import push

    key, auth, keys = _phone_keys()
    await _target(user, keys=keys)
    async with sessionmaker()() as db:
        assert await push.fan_out(db, user.id, "send_failed", "Message not sent", "To dana@example.org: 550",
                                  {"outbox_id": 3}, "high") == 1
    c = sent.calls[0]
    assert c["headers"]["Content-Encoding"] == "aes128gcm" and c["headers"]["Content-Type"] == "application/octet-stream"
    assert b"dana" not in c["content"] and len(c["content"]) <= 4096
    plain = http_ece.decrypt(c["content"], private_key=key, auth_secret=auth, version="aes128gcm")
    assert json.loads(plain) == {"title": "Message not sent", "body": "To dana@example.org: 550", "outbox_id": 3,
                                 "type": "send_failed"}


async def test_target_api_keys_replace_and_test_button(client, user, sent):
    """POST /push/targets with the phone's keys, the same endpoint again updates the row, a bad key
    is refused, and the test button says what happened."""
    _, _, keys = _phone_keys()
    ep = f"http://ntfy.invalid/up{uuid.uuid4().hex[:12]}?up=1"
    body = {"endpoint": ep, "kind": "unifiedpush", "device": "Pixel 8", "events": ["new_mail", "approval"], "keys": keys}
    r = await client.post("/api/v1/push/targets", json=body)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["encrypted"] is True and t["device"] == "Pixel 8" and t["events"] == ["new_mail", "approval"]
    r2 = await client.post("/api/v1/push/targets", json={**body, "device": "Pixel 8 Pro"})
    assert r2.json()["id"] == t["id"] and r2.json()["device"] == "Pixel 8 Pro"
    bad = {**keys, "auth": _b64(b"0123456789")}  # 10 bytes, not 16
    assert (await client.post("/api/v1/push/targets", json={**body, "keys": bad})).status_code == 400
    assert (await client.post("/api/v1/push/targets", json={**body, "kind": "ntfy"})).status_code == 400
    # Without keys: plain JSON, as older apps send.
    r3 = await client.post("/api/v1/push/targets", json={"endpoint": ep + "x", "device": "old"})
    assert r3.json()["encrypted"] is False

    r = await client.post(f"/api/v1/push/targets/{t['id']}/test")
    assert r.json() == {"ok": True, "error": None, "removed": False}
    sent.fake.status = 500
    r = await client.post(f"/api/v1/push/targets/{t['id']}/test")
    assert r.json() == {"ok": False, "error": "the push server answered 500", "removed": False}
    sent.fake.status = 410
    r = await client.post(f"/api/v1/push/targets/{t['id']}/test")
    assert r.json()["removed"] is True and r.json()["ok"] is False
    ids = [i["id"] for i in (await client.get("/api/v1/push/targets")).json()["items"]]
    assert t["id"] not in ids and r3.json()["id"] in ids
    assert (await client.delete(f"/api/v1/push/targets/{r3.json()['id']}")).json() == {"ok": True}


@pytest.fixture
def private_ntfy(monkeypatch):
    """ntfy.lan.example resolves to a LAN address (split-horizon DNS), and the instance blocks
    private hosts (MAILR_ALLOW_PRIVATE_HOSTS=false)."""
    from mailr.core import netguard

    real = socket.getaddrinfo

    def fake(host, *a, **kw):
        if host == "ntfy.lan.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.40", 443))]
        return real(host, *a, **kw)

    monkeypatch.setattr(netguard.socket, "getaddrinfo", fake)
    monkeypatch.setattr(netguard.get_settings(), "allow_private_hosts", False)
    monkeypatch.setattr(netguard.get_settings(), "outbound_allow", "")  # the tests' localhost allowance off
    netguard._allow_list.cache_clear()
    yield netguard
    netguard._allow_list.cache_clear()


async def test_private_push_host_is_refused_when_private_hosts_are_blocked(client, user, sent, private_ntfy):
    from mailr.core import push

    ep = "https://ntfy.lan.example/upAbCdEf123456?up=1"
    r = await client.post("/api/v1/push/targets", json={"endpoint": ep})
    assert r.status_code == 400 and "private address" in r.json()["detail"]
    tid = await _target(user, endpoint=ep)  # stored before the setting changed
    async with sessionmaker()() as db:
        t = await db.get(PushTarget, tid)
        kept, error = await push.deliver_report(t, "mailr", "Test notification", {"type": "test"})
    assert kept and "private address" in error and sent.calls == []
    # Loopback and metadata addresses stay refused whatever the settings say.
    for bad in ("http://127.0.0.1:2586/up1", "http://169.254.169.254/up1"):
        assert (await client.post("/api/v1/push/targets", json={"endpoint": bad})).status_code == 400


async def test_outbound_allow_lets_exactly_that_push_host_through(client, user, sent, private_ntfy, monkeypatch):
    """The narrow allowance: MAILR_OUTBOUND_ALLOW=<ntfy host>. Other private hosts stay blocked."""

    monkeypatch.setattr(private_ntfy.get_settings(), "outbound_allow", "ntfy.lan.example")
    private_ntfy._allow_list.cache_clear()
    ep = "https://ntfy.lan.example/upAbCdEf123456?up=1"
    r = await client.post("/api/v1/push/targets", json={"endpoint": ep, "device": "phone"})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/v1/push/targets/{r.json()['id']}/test")
    assert r.json()["ok"] is True and sent.calls[0]["url"] == ep
    assert json.loads(sent.calls[0]["content"]) == {"title": "Mailr", "body": "Test notification", "type": "test"}
    # A different LAN host is still refused.
    other = "https://10.0.0.5/upX"
    assert (await client.post("/api/v1/push/targets", json={"endpoint": other})).status_code == 400
    async with sessionmaker()() as db:
        assert (await db.execute(select(PushTarget).where(PushTarget.endpoint == other))).first() is None
