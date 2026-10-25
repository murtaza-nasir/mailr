# ACT-SURFACES: api
"""Web Push: the VAPID key, subscriptions (notification.subscribe), the encrypted
POST to the push service (encryption and the HTTP call are faked), and the signed tokens behind
the notification buttons (POST /reminders/act: valid, expired, forged; no session or CSRF;
event.snooze and reminder.dismiss)."""

import base64
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import InstanceSetting, PushTarget, ReminderState

KEY = "e|wp-1|2026-10-05T09:00:00+00:00|10"
P256DH = "BOr" + "A" * 84
AUTH = "c" * 22


async def test_notification_subscribe_stores_a_webpush_target_opted_in_to_reminders(client, user):
    """notification.subscribe: the VAPID key, then this browser's subscription."""
    k1 = (await client.get("/api/v1/push/webpush/key")).json()["public_key"]
    k2 = (await client.get("/api/v1/push/webpush/key")).json()["public_key"]
    assert k1 == k2 and len(base64.urlsafe_b64decode(k1 + "=" * (-len(k1) % 4))) == 65
    async with sessionmaker()() as db:
        stored = (await db.get(InstanceSetting, "webpush_vapid")).value
    assert stored["public"] == k1 and set(stored) == {"public", "private"}
    endpoint = f"https://localhost/push/{uuid.uuid4().hex}"
    r = await client.post("/api/v1/push/webpush", json={"endpoint": endpoint, "keys": {"p256dh": P256DH, "auth": AUTH},
                                                        "device": "Firefox on Linux"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "webpush" and r.json()["events"] == ["reminder"]
    # The same endpoint again updates the row; listed with the other targets.
    await client.post("/api/v1/push/webpush", json={"endpoint": endpoint, "keys": {"p256dh": P256DH, "auth": AUTH}})
    items = (await client.get("/api/v1/push/targets")).json()["items"]
    assert [i["kind"] for i in items] == ["webpush"]
    assert (await client.post("/api/v1/push/webpush", json={
        "endpoint": "http://localhost/plain", "keys": {"p256dh": P256DH, "auth": AUTH}})).status_code == 400
    r = await client.delete("/api/v1/push/webpush", params={"endpoint": endpoint})
    assert r.json() == {"ok": True, "removed": 1}
    assert (await client.get("/api/v1/push/targets")).json()["items"] == []


async def test_webpush_send_encrypts_and_signs(user, monkeypatch):
    from mailr.core import push, webpush

    sent = {}

    def encrypt(target, data):
        sent["plain"] = data
        return b"ciphertext"

    async def fetch_capped(client, method, url, *, limit, deadline=30.0, content=None, headers=None, **kw):
        sent.update(url=url, content=content, headers=headers)
        return httpx.Response(sent.get("status", 201)), b""

    from mailr.core import netguard

    monkeypatch.setattr(webpush, "encrypt", encrypt)
    monkeypatch.setattr(netguard, "fetch_capped", fetch_capped)
    endpoint = f"https://localhost/push/{uuid.uuid4().hex}"
    async with sessionmaker()() as db:
        t = PushTarget(user_id=user.id, kind="webpush", endpoint=endpoint, events=["reminder"], p256dh=P256DH,
                       auth=AUTH)
        db.add(t)
        await db.commit()
        public = await webpush.public_key(db)
        await db.commit()
        n = await push.fan_out(db, user.id, "reminder", "Budget review", "10:00 – 11:00", {"key": KEY},
                               "high", webpush_extra={"token": "tok"})
        await db.commit()
    assert n == 1 and sent["url"] == endpoint and sent["content"] == b"ciphertext"
    h = sent["headers"]
    assert h["Content-Encoding"] == "aes128gcm" and h["Urgency"] == "high" and int(h["TTL"]) > 0
    assert h["Authorization"].startswith("vapid t=") and h["Authorization"].endswith(f"k={public}")
    assert len(h["Topic"]) <= 32
    assert b'"token": "tok"' in sent["plain"] and b'"type": "reminder"' in sent["plain"]
    # The service says the subscription is gone: the target is removed.
    sent["status"] = 410
    async with sessionmaker()() as db:
        assert await push.fan_out(db, user.id, "reminder", "x", "y", {"key": KEY}) == 0
        await db.commit()
        assert (await db.execute(select(PushTarget).where(PushTarget.user_id == user.id))).scalars().all() == []


async def test_reminder_act_token_valid_expired_and_forged(app, client, user):
    """POST /reminders/act: a signed token works without a session or the CSRF header."""
    from mailr.calendar.reminders import act_token

    token = act_token(user.id, KEY, "dismiss")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        r = await anon.post("/api/v1/reminders/act", json={"token": act_token(user.id, KEY, "snooze"), "action": "snooze"})
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "snoozed" and r.json()["key"] == KEY
        until = datetime.fromisoformat(r.json()["snoozed_until"])
        assert timedelta(minutes=4) < until - datetime.now(UTC) <= timedelta(minutes=5)  # events: 5 minutes

        r = await anon.post("/api/v1/reminders/act", json={"token": token, "action": "dismiss"})
        assert r.status_code == 200 and r.json()["count"] == 1

        expired = act_token(user.id, KEY, "dismiss", ttl=timedelta(seconds=-5))
        assert (await anon.post("/api/v1/reminders/act", json={"token": expired, "action": "dismiss"})).status_code == 410
        uid, exp, k, act, sig = token.split(".")
        other = base64.urlsafe_b64encode(b"e|someone-else|2026-10-05|0").rstrip(b"=").decode()
        for forged in (f"{uid}.{exp}.{other}.{act}.{sig}", f"{int(uid) + 1}.{exp}.{k}.{act}.{sig}",
                       f"{uid}.{int(exp) + 99}.{k}.{act}.{sig}", f"{uid}.{exp}.{k}.done.{sig}",
                       f"{uid}.{exp}.{k}.{act}.{'A' * len(sig)}", "garbage-token-value"):
            assert (await anon.post("/api/v1/reminders/act", json={"token": forged, "action": "dismiss"})).status_code == 403
    # With the session cookie but no CSRF header (a service worker): still accepted.
    client.headers.pop("X-CSRF-Token")
    r = await client.post("/api/v1/reminders/act", json={"token": act_token(user.id, KEY, "dismiss"), "action": "dismiss"})
    assert r.status_code == 200
    async with sessionmaker()() as db:
        row = (await db.execute(select(ReminderState).where(ReminderState.user_id == user.id,
                                                            ReminderState.key == KEY))).scalar_one()
    assert row.state == "handled" and row.device == "webpush"


def test_webpush_encryption_round_trip():
    """The real aes128gcm encryption: the browser's key pair can read it."""
    import http_ece
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    from mailr.core import webpush

    browser = ec.generate_private_key(ec.SECP256R1())
    pub = browser.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = b"0123456789abcdef"
    t = PushTarget(kind="webpush", endpoint="https://localhost/push/x",
                   p256dh=base64.urlsafe_b64encode(pub).rstrip(b"=").decode(),
                   auth=base64.urlsafe_b64encode(auth).rstrip(b"=").decode())
    body = webpush.encrypt(t, b'{"title": "Budget review"}')
    assert http_ece.decrypt(body, private_key=browser, auth_secret=auth, version="aes128gcm") == b'{"title": "Budget review"}'


async def test_installed_app_subscribes_to_mail_and_gets_what_its_worker_reads(client, user, raw, monkeypatch):
    """The installed web app (iPhone Home Screen, Android, desktop) subscribes for new mail and
    approvals as well as reminders (Settings → Notifications), and a new message reaches it as the
    JSON its service worker (frontend/public/sw.js showMail) reads: title, body, conversation_id,
    a same-origin click link and the type. Never the subject or the text."""
    import json

    from helpers.agent import _ingest, _setup_identity

    from mailr.core import push, webpush
    from mailr.models import Message

    endpoint = f"https://localhost/push/{uuid.uuid4().hex}"
    r = await client.post("/api/v1/push/webpush", json={"endpoint": endpoint, "keys": {"p256dh": P256DH, "auth": AUTH},
                                                        "device": "Safari on iOS",
                                                        "events": ["reminder", "new_mail", "approval", "send_failed"]})
    assert r.status_code == 200, r.text
    assert r.json()["events"] == ["reminder", "new_mail", "approval", "send_failed"]
    # Unknown kinds are refused.
    bad = await client.post("/api/v1/push/webpush", json={"endpoint": endpoint, "keys": {"p256dh": P256DH, "auth": AUTH},
                                                          "events": ["everything"]})
    assert bad.status_code == 400

    sent: list[dict] = []

    def encrypt(target, data):
        sent.append({"plain": json.loads(data)})
        return b"ciphertext"

    async def fetch_capped(client_, method, url, *, limit, deadline=30.0, content=None, headers=None, **kw):
        sent[-1].update(url=url, headers=headers)
        return httpx.Response(201), b""

    from mailr.core import netguard

    monkeypatch.setattr(webpush, "encrypt", encrypt)
    monkeypatch.setattr(netguard, "fetch_capped", fetch_capped)
    _conn, me = await _setup_identity(user)
    async with sessionmaker()() as db:  # Settings → Notifications → New mail: all (core/notifyprefs.py)
        u = await db.get(type(user), user.id)
        u.settings = {**(u.settings or {}), "mail_notifications": "all"}
        await db.commit()
    mid, cid = await _ingest(user.id, raw("Quarterly numbers", frm="Carol Diaz <carol@example.org>", to=me, body="SECRET-TEXT"))
    async with sessionmaker()() as db:
        (await db.get(Message, mid)).intel_state = "done"
        await db.commit()
    await push.push_new_mail({"message_id": mid}, None)
    assert len(sent) == 1 and sent[0]["url"] == endpoint
    doc = sent[0]["plain"]
    assert doc["type"] == "new_mail" and doc["title"] == "Mail from Carol Diaz" and doc["body"] == "New message"
    from mailr.core import publicid

    assert doc["conversation_id"] == publicid.out({"conversation_id": cid})["conversation_id"]
    assert "/mail/inbox/" in doc["click"]
    assert "Quarterly" not in json.dumps(doc) and "SECRET" not in json.dumps(doc)
    assert sent[0]["headers"]["Content-Encoding"] == "aes128gcm"
