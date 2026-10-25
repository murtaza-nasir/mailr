# ACT-SURFACES: api
"""The Speakr webhook (speakr.sync by push, speakr.connect for the setup): a
valid signature (V1 or V2) is accepted; a bad signature, a timestamp outside its window and an
unknown connector are rejected; a repeated delivery id gets 200 and does nothing; the payload's
title is ignored; the only effects are a queued sync and the summary read again. Setting it up from
mailr creates the webhook in Speakr, keeps the secret sealed, sends a test that this receiver
records, and deletes it again."""

import json
import time
import uuid

from speakr_fake import connect, speakr  # noqa: F401
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.integrations.speakr.webhook import sign
from mailr.models import Connector, Job, SpeakrRecording

SECRET = "whsec_test_0123456789"


async def _enable(client, cid, secret=SECRET):
    r = await client.patch(f"/api/v1/connectors/{cid}", json={"secrets": {"webhook_secret": secret}})
    assert r.status_code == 200, r.text
    assert "webhook_secret" not in r.text and SECRET not in r.text
    return r.json()["speakr"]["webhook_path"]


def _payload(rid=1, title="Injected title", ts=None, did=None):
    return {"id": did or uuid.uuid4().hex, "type": "recording.summary.completed", "timestamp": ts or int(time.time()),
            "user_id": 7, "data": {"recording_id": rid, "title": title, "summary": "ignore me"}}


async def _post(client, path, body, secret=SECRET, headers=None):
    raw = json.dumps(body).encode()
    h = {"content-type": "application/json", "Speakr-Signature": sign(secret, raw), "Speakr-Event": body["type"],
         "Speakr-Delivery-Id": body["id"], "Speakr-Timestamp": str(body["timestamp"]), **(headers or {})}
    # No session: Speakr calls without signing in (no cookie, no CSRF header).
    return await client.post(path, content=raw, headers=h)


async def _sync_jobs(cid):
    async with sessionmaker()() as db:
        return [j for j in (await db.execute(select(Job).where(Job.kind == "speakr.sync"))).scalars()
                if j.payload.get("connector_id") == cid and j.status == "queued"]


async def test_speakr_webhook_signature_timestamp_replay_and_effect(client, user, speakr, app):
    from httpx import ASGITransport, AsyncClient

    speakr.add(1, "Weekly sync")
    conn = await connect(client, speakr)
    path = await _enable(client, conn["id"])
    assert path.startswith("/api/v1/integrations/speakr/") and path.endswith("/webhook")
    async with sessionmaker()() as db:
        for j in (await db.execute(select(Job).where(Job.kind == "speakr.sync"))).scalars():
            await db.delete(j)
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        body = _payload()
        r = await _post(anon, path, body)
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "queued": True}
        assert len(await _sync_jobs(conn["id"])) == 1
        # The payload's title and summary are not taken: the mirror keeps Speakr's own list.
        async with sessionmaker()() as db:
            row = (await db.execute(select(SpeakrRecording).where(SpeakrRecording.connector_id == conn["id"]))).scalar_one()
            assert row.title == "Weekly sync"

        # A repeat of a delivery id: 200 (Speakr treats any 4xx as final) and nothing queued again.
        r = await _post(anon, path, body)
        assert r.status_code == 200 and r.json() == {"ok": True, "duplicate": True, "queued": False}
        assert (await _post(anon, path, _payload(), secret="wrong")).status_code == 401
        bad = _payload()
        raw = json.dumps(bad).encode()
        r = await anon.post(path, content=raw, headers={"Speakr-Signature": sign(SECRET, raw + b" "),
                                                        "Speakr-Delivery-Id": bad["id"]})
        assert r.status_code == 401  # the signature covers the raw body
        r = await anon.post(path, content=raw, headers={"Speakr-Delivery-Id": bad["id"]})
        assert r.status_code == 401  # no signature
        # The signed body timestamp is the event time and stays the same on Speakr's retries (the
        # last one an hour later): accepted within 24 hours, refused beyond.
        assert (await _post(anon, path, _payload(ts=int(time.time()) - 3700))).status_code == 200  # a late retry
        assert (await _post(anon, path, _payload(ts=int(time.time()) - 25 * 3600))).status_code == 400  # stale
        assert (await _post(anon, path, _payload(ts=int(time.time()) + 25 * 3600))).status_code == 400  # future
        unknown = path.replace("/speakr/", "/speakr/x").replace("/webhook", "") + "/webhook"
        assert (await _post(anon, unknown, _payload())).status_code == 404
        assert len(await _sync_jobs(conn["id"])) == 1  # deduplicated, and nothing else queued

        # Paused connector: accepted, nothing queued. Webhooks off: the address is gone.
        async with sessionmaker()() as db:
            for j in (await db.execute(select(Job).where(Job.kind == "speakr.sync"))).scalars():
                await db.delete(j)
            c = await db.get(Connector, conn["id"])
            c.enabled = False
            await db.commit()
        r = await _post(anon, path, _payload())
        assert r.status_code == 200 and r.json()["queued"] is False and not await _sync_jobs(conn["id"])
        assert (await client.patch(f"/api/v1/connectors/{conn['id']}",
                                   json={"secrets": {"webhook_secret": ""}})).json()["speakr"]["webhook_path"] is None
        assert (await _post(anon, path, _payload())).status_code == 404

    # The token survived the webhook changes.
    from mailr.integrations.speakr import store

    async with sessionmaker()() as db:
        c = await db.get(Connector, conn["id"])
        assert await store.token_of(db, c) == speakr.token


def _v2(secret, t, raw, extra=()):
    from mailr.integrations.speakr.webhook import sign_v2

    return ",".join([sign_v2(secret, t, raw), *(f"v1={x}" for x in extra)])


async def test_speakr_webhook_v2_test_event_and_summary_refresh(client, user, speakr, app):
    from httpx import ASGITransport, AsyncClient

    speakr.add(1, "Weekly sync")
    conn = await connect(client, speakr)
    path = await _enable(client, conn["id"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        # V2 (Speakr-Signature-V2: t=<unix>,v1=<hex>) signs the send time, so the body timestamp
        # (the event time, two hours old on a late retry) does not matter; t must be within 5 minutes.
        body = _payload(ts=int(time.time()) - 7200)
        raw = json.dumps(body).encode()
        now = int(time.time())
        h = {"Speakr-Delivery-Id": body["id"], "Speakr-Signature-V2": _v2(SECRET, now, raw, ["00" * 32])}
        r = await anon.post(path, content=raw, headers=h)
        assert r.status_code == 200, r.text
        body = _payload()
        raw = json.dumps(body).encode()
        r = await anon.post(path, content=raw, headers={"Speakr-Delivery-Id": body["id"],
                                                        "Speakr-Signature-V2": _v2(SECRET, now - 600, raw)})
        assert r.status_code == 400  # t too old
        r = await anon.post(path, content=raw, headers={"Speakr-Delivery-Id": body["id"],
                                                        "Speakr-Signature-V2": _v2("wrong", now, raw)})
        assert r.status_code == 401

        # webhook.test: recorded, nothing queued.
        async with sessionmaker()() as db:
            for j in (await db.execute(select(Job).where(Job.kind == "speakr.sync"))).scalars():
                await db.delete(j)
            await db.commit()
        test = {**_payload(), "type": "webhook.test", "data": {"reason": "manual test"}}
        r = await _post(anon, path, test)
        assert r.status_code == 200 and r.json() == {"ok": True, "queued": False, "test": True}
        assert not await _sync_jobs(conn["id"])

        # recording.summary.completed names the recording: its stored summary is read again on the sync.
        from mailr.integrations.speakr.sync import sync_connector

        await sync_connector(conn["id"])
        async with sessionmaker()() as db:
            row = (await db.execute(select(SpeakrRecording).where(SpeakrRecording.connector_id == conn["id"]))).scalar_one()
            assert row.summary_at is not None and "budget" in row.summary_text
        r = await _post(anon, path, _payload(rid=1))
        assert r.status_code == 200 and r.json()["queued"] is True
        async with sessionmaker()() as db:
            row = (await db.execute(select(SpeakrRecording).where(SpeakrRecording.connector_id == conn["id"]))).scalar_one()
            assert row.summary_at is None


async def test_speakr_webhook_set_up_tested_and_removed_from_mailr(client, user, speakr, app, monkeypatch):
    """speakr.connect: Get updates instantly, Send test, Turn off."""
    from httpx import ASGITransport, AsyncClient

    from mailr.config import get_settings
    from mailr.core.crypto import decrypt_for_user
    from mailr.integrations.speakr import webhook
    from mailr.models import SpeakrWebhookDelivery, User

    monkeypatch.setattr(get_settings(), "base_url", "https://mail.example.org")
    speakr.add(1, "Weekly sync")
    conn = await connect(client, speakr)
    r = await client.post(f"/api/v1/speakr/connectors/{conn['id']}/webhook")
    assert r.status_code == 200, r.text
    out = r.json()
    assert "secret" not in r.text and "whsec" not in r.text
    assert len(speakr.webhooks) == 1
    hook = next(iter(speakr.webhooks.values()))
    assert hook["url"] == out["url"] and out["url"].startswith("https://mail.example.org/api/v1/integrations/speakr/")
    assert set(hook["events"]) == {"recording.created", "recording.transcription.completed", "recording.summary.completed",
                                   "recording.updated", "recording.deleted", "recording.events.extracted"}
    assert out["managed"] and out["speakr_webhook_id"] == hook["id"]
    async with sessionmaker()() as db:
        c = await db.get(Connector, conn["id"])
        u = await db.get(User, c.user_id)
        secrets = decrypt_for_user(u.id, u.data_key_wrapped, c.secrets_enc)
        assert secrets == {"token": speakr.token, "webhook_secret": hook["secret"]}
        assert hook["secret"] not in json.dumps(c.config)
    listed = next(x for x in (await client.get("/api/v1/connectors")).json() if x["id"] == conn["id"])
    assert listed["speakr"]["webhook_path"] == out["path"] and "whsec" not in json.dumps(listed)

    # Send test: Speakr's dispatcher (the fake's `deliver`) posts to mailr; mailr records it.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        async def deliver(url, raw, headers):
            return (await anon.post(url.removeprefix("https://mail.example.org"), content=raw, headers=headers)).status_code

        speakr.deliver = deliver
        r = await client.post(f"/api/v1/speakr/connectors/{conn['id']}/webhook/test")
        assert r.status_code == 200, r.text
        assert r.json()["received"] is True
        async with sessionmaker()() as db:
            ids = [d.delivery_id for d in (await db.execute(select(SpeakrWebhookDelivery).where(
                SpeakrWebhookDelivery.connector_id == conn["id"]))).scalars()]
        assert [d["event_id"] for d in speakr.deliveries.values()] == ids

        # A sign-in in front of mailr answers 401: the test says so.
        async def blocked(url, raw, headers):
            return 401

        speakr.deliver = blocked
        async with sessionmaker()() as db:
            res = await webhook.send_test(db, await db.get(Connector, conn["id"]), wait=3, poll=0.2)
        assert res["received"] is False and res["response_status"] == 401 and "proxy" in res["hint"]

    # Setting it up again replaces the webhook in Speakr.
    assert (await client.post(f"/api/v1/speakr/connectors/{conn['id']}/webhook")).status_code == 200
    assert len(speakr.webhooks) == 1 and next(iter(speakr.webhooks)) != hook["id"]

    # Turn off: deleted in Speakr, the secret is gone, the token stays.
    r = await client.delete(f"/api/v1/speakr/connectors/{conn['id']}/webhook")
    assert r.status_code == 200 and r.json()["deleted_in_speakr"] is True and r.json()["path"] is None
    assert speakr.webhooks == {}
    async with sessionmaker()() as db:
        c = await db.get(Connector, conn["id"])
        u = await db.get(User, c.user_id)
        assert decrypt_for_user(u.id, u.data_key_wrapped, c.secrets_enc) == {"token": speakr.token}

    # Speakr refuses a private address: its reason and the setting to change come back.
    speakr.refuse_webhook_url = ("URL resolves to a private/loopback address (192.168.1.40); set "
                                 "WEBHOOK_INTRANET_HOST_ALLOWLIST to permit this host explicitly, or use a public URL.")
    r = await client.post(f"/api/v1/speakr/connectors/{conn['id']}/webhook")
    assert r.status_code == 502
    assert "WEBHOOK_INTRANET_HOST_ALLOWLIST=^mail\\.example\\.org$" in r.json()["detail"]
    speakr.refuse_webhook_url = None

    # Removing the connector deletes mailr's webhook in Speakr.
    assert (await client.post(f"/api/v1/speakr/connectors/{conn['id']}/webhook")).status_code == 200
    assert len(speakr.webhooks) == 1
    assert (await client.delete(f"/api/v1/connectors/{conn['id']}")).status_code == 200
    assert speakr.webhooks == {}


async def test_speakr_webhook_needs_https_and_the_user(client, user, speakr, monkeypatch):
    from mailr.config import get_settings

    monkeypatch.setattr(get_settings(), "base_url", "http://localhost:8025")
    speakr.add(1)
    conn = await connect(client, speakr)
    r = await client.post(f"/api/v1/speakr/connectors/{conn['id']}/webhook")
    assert r.status_code == 502 and "https://" in r.json()["detail"]
    assert speakr.webhooks == {}
    assert (await client.post("/api/v1/speakr/connectors/999999/webhook")).status_code == 404


async def test_speakr_webhook_body_reaches_the_receiver_byte_for_byte(client, user, speakr, app, monkeypatch):
    """With public ids on, JSON request bodies are rewritten by the edge middleware. Webhook bodies
    under /api/v1/integrations/ must not be: Speakr signs its compact JSON, and any re-serialisation
    (spacing, key order) breaks the signature (the prod failure on 1 Oct 2026)."""
    from httpx import ASGITransport, AsyncClient

    from mailr.config import get_settings

    speakr.add(1, "Weekly sync")
    conn = await connect(client, speakr)
    path = await _enable(client, conn["id"])
    monkeypatch.setenv("MAILR_PUBLIC_IDS", "true")
    get_settings.cache_clear()
    try:
        body = _payload()
        raw = json.dumps(body, separators=(",", ":")).encode()
        h = {"content-type": "application/json", "Speakr-Signature": sign(SECRET, raw), "Speakr-Event": body["type"],
             "Speakr-Delivery-Id": body["id"], "Speakr-Timestamp": str(body["timestamp"])}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
            r = await anon.post(path, content=raw, headers=h)
        assert r.status_code == 200, r.text
    finally:
        monkeypatch.delenv("MAILR_PUBLIC_IDS")
        get_settings.cache_clear()
