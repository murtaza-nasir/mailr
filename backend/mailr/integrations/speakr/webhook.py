"""The Speakr webhook: the receiver, and setting it up from mailr (push).

Polling stays the baseline. An older Speakr sends `recording.updated` only
for edits made through its v1 API (a PATCH), not for edits in its web pages, so the ten-minute sync
still catches those; a newer one (capabilities `webhook_signature_v2`) sends it for every visible
change, from its pages too. A webhook makes new recordings, finished transcripts and summaries,
deletions and edits show at once. With the changes feed, the sync a delivery queues is a small
changes request, not a full pass.

Setting it up ("Get updates instantly" in the connector card; `register`): mailr creates the
webhook in Speakr through POST /api/v1/webhooks with the events in EVENTS and the address
`<mailr base URL>/api/v1/integrations/speakr/<webhook id>/webhook`. The webhook id is random and
names the connector. Speakr answers with the signing secret once; it is sealed with the token
(secrets_enc) and never returned. "Send test" (`send_test`) asks Speakr for a `webhook.test`
delivery and waits until this receiver records it, or Speakr reports why it could not deliver.
Turning it off (`unregister`) deletes the webhook in Speakr. A secret pasted in the card (a webhook
the user made in Speakr by hand) works the same way.

A delivery is accepted only when:
- the webhook id names a Speakr connector that has a secret (else 404, as for any unknown path);
- the signature is right: `Speakr-Signature-V2: t=<unix>,v1=<hex>[,v1=<hex>]` when Speakr sends it
  (HMAC-SHA256 over "<t>." and the raw body, any v1 value, so both values of a secret rotation
  pass; `t` within five minutes), else `Speakr-Signature: sha256=<hex>` (HMAC-SHA256 over the raw
  body), compared in constant time. Once Speakr's capabilities report `webhook_signature_v2`, a
  delivery without the V2 header is refused (401), so nobody can fall back to the weaker one;
- without V2 (an older Speakr), the signed body's `timestamp` is within 24 hours. That timestamp
  is the time the event was queued and stays the same on Speakr's retries (30 s, 2 min, 10 min and
  1 h later), and the `Speakr-Timestamp` header is not signed (Speakr's own guide says not to use
  it for freshness), so a five-minute window would lose the last retries. The signature and the
  delivery ids kept for seven days stop a replay.
- its delivery id was not received before. The id is the signed body's `id` (Speakr's event id,
  the same on every retry), else a hash of the signed body; the `Speakr-Delivery-Id` header is not
  signed, so a replay with a fresh header is still a repeat. A repeat gets 200 and does nothing:
  Speakr treats any 4xx as final.

The receiver then queues a sync of the connector (`speakr.sync`), and nothing else (a
`webhook.test` queues nothing). From the payload only the recording id is read, and only to read
that recording's summary again on the sync; the sync reads Speakr's own data. A title or a summary
in the payload is ignored.

Reachability: Speakr must be able to POST to this path without signing in. When mailr sits
behind a proxy-level sign-in (for example a forward-auth gate in front of the whole site), that gate
blocks the deliveries; the path /api/v1/integrations/speakr/* then needs an exemption at the proxy, or
the account stays on polling. Speakr also refuses targets on private addresses unless its
WEBHOOK_INTRANET_HOST_ALLOWLIST names them: when the mailr host name resolves to a private address
for Speakr's server, the create call fails with Speakr's reason, which `register` passes on.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.crypto import connector_context, decrypt_for_user
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.integrations.speakr import store
from mailr.models import Connector, SpeakrRecording, SpeakrWebhookDelivery, User

log = logging.getLogger("mailr.speakr.webhook")

router = APIRouter(prefix="/integrations/speakr", tags=["speakr"])

TOLERANCE = timedelta(minutes=5)  # V2: the signed send time
BODY_TOLERANCE = timedelta(hours=24)  # V1: the signed event time, unchanged on retries
KEEP = timedelta(days=7)
MAX_BODY = 64 * 1024
# The events mailr asks for: anything that changes what the mirror or the summary index holds.
EVENTS = ("recording.created", "recording.transcription.completed", "recording.summary.completed",
          "recording.updated", "recording.deleted", "recording.events.extracted")
# Events after which the recording's summary is read again on the sync.
SUMMARY_EVENTS = ("recording.summary.completed", "recording.updated", "recording.transcription.completed")


def new_webhook_id() -> str:
    return secrets.token_urlsafe(18)


def path_for(conn: Connector) -> str | None:
    hook = (conn.config or {}).get("webhook_id")
    return f"/api/v1/integrations/speakr/{hook}/webhook" if hook else None


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def sign_v2(secret: str, t: int, body: bytes) -> str:
    """Speakr-Signature-V2: t=<unix seconds>,v1=<HMAC-SHA256 over "<t>." + body>."""
    return f"t={int(t)},v1=" + hmac.new(secret.encode(), f"{int(t)}.".encode() + body, hashlib.sha256).hexdigest()


def _check_v2(secret: str, header: str, raw: bytes, now: datetime) -> None:
    t, sigs = None, []
    for item in header.split(","):
        k, _, v = item.strip().partition("=")
        if k == "t" and v.strip().isdigit():
            t = int(v.strip())
        elif k == "v1":
            sigs.append(v.strip())
    if t is None or not sigs:
        raise HTTPException(401, "bad signature")  # i18n: internal
    want = sign_v2(secret, t, raw).split("v1=", 1)[1].encode()
    if not any(hmac.compare_digest(sg.encode(), want) for sg in sigs):
        raise HTTPException(401, "bad signature")  # i18n: internal
    if abs(now - datetime.fromtimestamp(t, UTC)) > TOLERANCE:
        raise HTTPException(400, "stale or missing timestamp")  # i18n: internal


def _when(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().isdigit()):
        n = float(v)
        return datetime.fromtimestamp(n / 1000 if n > 1e11 else n, UTC)
    try:
        d = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


async def _secret(db: AsyncSession, conn: Connector) -> str:
    user = await db.get(User, conn.user_id)
    return str((decrypt_for_user(user.id, user.data_key_wrapped, conn.secrets_enc, context=connector_context(conn.id))
                 or {}).get("webhook_secret") or "")


async def read_capped(request: Request, limit: int) -> bytes:
    """The request body, refused with 413 as soon as it is known to exceed `limit`: by its
    Content-Length before anything is read, otherwise while it streams in."""
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        raise HTTPException(400, "bad Content-Length") from None  # i18n: internal
    if declared > limit:
        raise ApiError(413, "common.too_large")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise ApiError(413, "common.too_large")
    return bytes(buf)


# ACT speakr.sync @api (a webhook delivery queues the same sync)
@router.post("/{hook}/webhook")
async def receive(hook: str, request: Request, db: AsyncSession = Depends(get_db)):
    raw = await read_capped(request, MAX_BODY)
    conn = (await db.execute(select(Connector).where(Connector.kind == store.KIND,
                                                     Connector.config["webhook_id"].astext == hook[:64])
                             .limit(1))).scalar_one_or_none()
    secret = await _secret(db, conn) if conn is not None else ""
    if not secret:
        raise ApiError(404, "common.not_found")
    now = datetime.now(UTC)
    v2 = request.headers.get("speakr-signature-v2", "").strip()
    if v2:
        _check_v2(secret, v2, raw, now)
    elif store.feature(conn, "webhook_signature_v2"):
        raise HTTPException(401, "bad signature")  # this Speakr signs with V2; a V1-only delivery is not its; i18n: internal
    else:
        sent = request.headers.get("speakr-signature", "")
        if not hmac.compare_digest(sent.strip().encode(), sign(secret, raw).encode()):
            raise HTTPException(401, "bad signature")  # i18n: internal
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "not JSON") from None  # i18n: internal
    if not isinstance(body, dict):
        raise HTTPException(400, "not a webhook payload")  # i18n: internal
    if not v2:
        when = _when(body.get("timestamp"))
        if when is None or abs(now - when) > BODY_TOLERANCE:
            raise HTTPException(400, "stale or missing timestamp")  # i18n: internal
    # Only signed bytes name the delivery: the body's id, else the body's hash. The
    # Speakr-Delivery-Id header is not signed, so a captured delivery with a fresh header id
    # would otherwise pass as new.
    delivery = str(body.get("id") or "").strip()[:100] or "sha256:" + hashlib.sha256(raw).hexdigest()
    await db.execute(delete(SpeakrWebhookDelivery).where(SpeakrWebhookDelivery.connector_id == conn.id,
                                                         SpeakrWebhookDelivery.received_at < now - KEEP))
    try:
        async with db.begin_nested():
            db.add(SpeakrWebhookDelivery(connector_id=conn.id, delivery_id=delivery))
            await db.flush()
    except IntegrityError:
        # A retry of a delivery already received: 200, so Speakr stops; nothing is queued again.
        return {"ok": True, "duplicate": True, "queued": False}
    kind = str(body.get("type") or "")
    if kind == "webhook.test":
        await db.commit()
        return {"ok": True, "queued": False, "test": True}
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    rid = data.get("recording_id")
    if kind in SUMMARY_EVENTS and isinstance(rid, int) and not isinstance(rid, bool):
        await db.execute(update(SpeakrRecording).where(SpeakrRecording.connector_id == conn.id,
                                                       SpeakrRecording.recording_id == rid)
                         .values(summary_at=None))
    queued = False
    if conn.enabled and not (conn.state or {}).get("auth_failed"):
        from mailr.integrations.speakr.sync import queue_sync

        await queue_sync(db, conn)
        queued = True
    await db.commit()
    return {"ok": True, "queued": queued}


# ---------------------------------------------------------------- setting it up from mailr


def _base() -> str:
    from mailr.config import get_settings

    return get_settings().base_url.rstrip("/")


def private_hint(message: str, base: str | None = None) -> str | None:
    """What to set on the Speakr server when it refuses mailr's address as private."""
    if "private" not in (message or "").lower() and "blocked" not in (message or "").lower():
        return None
    from urllib.parse import urlsplit

    host = (urlsplit(base or _base()).hostname or "").lower()
    rx = "^" + host.replace(".", "\\.") + "$"
    return (f"Speakr's server resolves {host} to a private address and refuses it. On the Speakr server, set "
            f"WEBHOOK_INTRANET_HOST_ALLOWLIST={rx} and restart Speakr, or keep the ten-minute checks.")


def status_out(conn: Connector, base: str | None = None) -> dict[str, Any]:
    cfg = conn.config or {}
    path = path_for(conn)
    return {"on": bool(path), "managed": bool(cfg.get("speakr_webhook_id")), "path": path,
            "url": f"{base or _base()}{path}" if path else None, "speakr_webhook_id": cfg.get("speakr_webhook_id"),
            "events": list(cfg.get("webhook_events") or [])}


# ACT speakr.connect @api (Get updates instantly)
async def register(db: AsyncSession, conn: Connector, *, base_url: str | None = None) -> dict[str, Any]:
    """Create the webhook in Speakr and keep its secret. A webhook mailr made before is deleted in
    Speakr first. Raises client errors with Speakr's reason (for example a private address)."""
    from mailr.connectors.loader import set_secrets
    from mailr.integrations.speakr import client as sc

    base = (base_url or _base()).rstrip("/")
    if not base.lower().startswith("https://"):
        raise sc.SpeakrError(f"Speakr sends webhooks only to https:// addresses, and Mailr's address is {base}")
    cl = await store.client_for(db, conn)
    cfg = dict(conn.config or {})
    old = cfg.get("speakr_webhook_id")
    if old:
        try:
            await cl.delete_webhook(int(old))
        except sc.SpeakrAuthError:
            raise
        except sc.SpeakrError:
            log.warning("could not delete the earlier Speakr webhook of connector %s", conn.id)
    hook = new_webhook_id()
    try:
        d = await cl.create_webhook("Mailr", f"{base}/api/v1/integrations/speakr/{hook}/webhook", list(EVENTS),
                                    include_shared=cfg.get("include_shared") is True
                                    and store.feature(conn, "internal_sharing"))
    except sc.SpeakrAuthError:
        await store.mark_auth_failed(db, conn)
        raise
    token = await store.token_of(db, conn)
    cfg.update(webhook_id=hook, speakr_webhook_id=int(d["id"]),
               webhook_events=[e for e in d.get("events") or EVENTS if isinstance(e, str)])
    conn.config = cfg
    await set_secrets(db, conn, {"token": token, "webhook_secret": str(d["secret"])})
    await db.flush()
    return status_out(conn, base)


# ACT speakr.connect (Turn off)
async def unregister(db: AsyncSession, conn: Connector) -> dict[str, Any]:
    """Delete mailr's webhook in Speakr (when mailr made it) and forget the secret. The local
    switch goes off even when Speakr cannot be reached; `deleted_in_speakr` says what happened."""
    from mailr.connectors.loader import set_secrets
    from mailr.integrations.speakr import client as sc

    cfg = dict(conn.config or {})
    deleted = None
    if cfg.get("speakr_webhook_id"):
        try:
            await (await store.client_for(db, conn)).delete_webhook(int(cfg["speakr_webhook_id"]))
            deleted = True
        except sc.SpeakrError as e:
            log.warning("could not delete the Speakr webhook of connector %s: %s", conn.id, e)
            deleted = False
    for k in ("webhook_id", "speakr_webhook_id", "webhook_events"):
        cfg.pop(k, None)
    conn.config = cfg
    if conn.secrets_enc:
        await set_secrets(db, conn, {"token": await store.token_of(db, conn)})
    await db.flush()
    return {**status_out(conn), "deleted_in_speakr": deleted}


# ACT speakr.connect (Send test)
async def send_test(db: AsyncSession, conn: Connector, *, wait: float = 25.0, poll: float = 1.0) -> dict[str, Any]:
    """Ask Speakr for a `webhook.test` delivery and wait until this receiver records it (a
    speakr_webhook_deliveries row with Speakr's event id), or until Speakr reports why the
    delivery failed, or `wait` seconds."""
    import asyncio
    import time

    from mailr.db import session_scope
    from mailr.integrations.speakr import client as sc

    hid = (conn.config or {}).get("speakr_webhook_id")
    if not hid:
        raise LookupError("no webhook set up from Mailr for this account")
    cl = await store.client_for(db, conn)
    d = await cl.test_webhook(int(hid))
    event_id, delivery = str(d.get("event_id") or ""), d.get("id")
    last: dict[str, Any] = d
    received = False
    deadline = time.monotonic() + wait
    while True:
        async with session_scope() as s:
            received = event_id != "" and (await s.execute(select(SpeakrWebhookDelivery.id).where(
                SpeakrWebhookDelivery.connector_id == conn.id,
                SpeakrWebhookDelivery.delivery_id == event_id))).first() is not None
        if received:
            break
        if delivery is not None:
            try:
                last = await cl.webhook_delivery(int(hid), int(delivery)) or last
            except sc.SpeakrError:
                pass
            if last.get("status") in ("failed", "permanent_failure"):
                break  # Speakr's next retry is 30 s away at the soonest
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(poll)
    error = last.get("error_message") or None
    out = {"received": received, "speakr_status": last.get("status"), "response_status": last.get("response_status"),
           "error": error}
    if not received:
        code = last.get("response_status")
        if private_hint(error or ""):
            out["hint"] = private_hint(error or "")
        elif code == 401 and "bad signature" in str(last.get("response_preview") or last.get("response_body") or ""):
            out["hint"] = ("Mailr answered 401 bad signature: the secret Mailr holds does not match Speakr's. "
                           "Set the webhook up again from this card.")
        elif code in (301, 302, 303, 307, 308, 401, 403):
            out["hint"] = (f"Speakr reached the address, which answered {code}: a sign-in in front of Mailr blocks it. "
                           "Let /api/v1/integrations/speakr/ through at the proxy.")
        elif code:
            out["hint"] = f"Speakr reached Mailr, which answered {code}."
        elif last.get("status") in (None, "pending"):
            out["hint"] = "Speakr has not sent it yet; its dispatcher runs every few seconds. Try again shortly."
    return out
