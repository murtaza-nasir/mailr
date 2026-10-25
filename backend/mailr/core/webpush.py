"""Web Push (RFC 8030/8291/8292) to browsers, for reminders when no mailr tab is open.

The VAPID key pair is made on first use and kept in instance_settings ("webpush_vapid"): the
public key in clear (browsers subscribe with it, and the Android app registers its UnifiedPush
endpoint with it, core/push.py), the private key sealed with the instance
key (core/crypto.py). The private key is never logged or returned.

A message is the JSON payload (title, body and the notification data), encrypted for the
subscription with aes128gcm (pywebpush's WebPusher), POSTed with httpx through the outbound
guard like every other push.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.config import get_settings
from mailr.models import InstanceSetting, PushTarget

log = logging.getLogger("mailr.webpush")

SETTING = "webpush_vapid"
TTL = 24 * 3600


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _new_pair() -> dict[str, str]:
    from mailr.core.crypto import VAPID_CONTEXT, encrypt_instance

    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.private_numbers().private_value.to_bytes(32, "big")
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"public": _b64(pub), "private": base64.b64encode(encrypt_instance(_b64(raw), context=VAPID_CONTEXT)).decode()}


async def _pair(db: AsyncSession) -> dict[str, str]:
    row = await db.get(InstanceSetting, SETTING)
    if row is None or not (row.value or {}).get("public"):
        # Two processes may race on first use; the first insert wins and both read it back.
        await db.execute(insert(InstanceSetting).values(key=SETTING, value=_new_pair())
                         .on_conflict_do_nothing(index_elements=["key"]))
        await db.flush()
        row = await db.get(InstanceSetting, SETTING)
        await db.refresh(row)
    return row.value


async def public_key(db: AsyncSession) -> str:
    """The application server key browsers subscribe with (base64url, uncompressed point)."""
    return (await _pair(db))["public"]


def _private_key(pair: dict[str, str]) -> ec.EllipticCurvePrivateKey:
    from mailr.core.crypto import VAPID_CONTEXT, decrypt_instance

    raw = _unb64(decrypt_instance(base64.b64decode(pair["private"]), context=VAPID_CONTEXT))
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def _subject() -> str:
    """The contact push services may use (RFC 8292): the admin's address, else the https site."""
    s = get_settings()
    if s.bootstrap_admin_email:
        return f"mailto:{s.bootstrap_admin_email}"
    if s.base_url.startswith("https://"):
        return s.base_url.rstrip("/")
    host = urlsplit(s.base_url).hostname or ""
    return f"mailto:webpush@{host if '.' in host else 'mailr.invalid'}"


def vapid_header(endpoint: str, pair: dict[str, str]) -> str:
    """The `Authorization: vapid t=…, k=…` header for one push service (RFC 8292)."""
    from py_vapid import Vapid02

    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": _subject()}
    return Vapid02(private_key=_private_key(pair)).sign(claims)["Authorization"]


async def vapid_for(endpoint: str) -> str:
    """The VAPID header for a UnifiedPush endpoint the phone registered with this key (core/push.py)."""
    from mailr.db import session_scope

    async with session_scope() as db:
        pair = await _pair(db)
        await db.commit()
    return vapid_header(endpoint, pair)


def encrypt(target: PushTarget, data: bytes) -> bytes:
    """aes128gcm for this subscription (RFC 8291)."""
    from pywebpush import WebPusher

    sub = {"endpoint": target.endpoint, "keys": {"p256dh": target.p256dh, "auth": target.auth}}
    return bytes(WebPusher(sub).encode(data, content_encoding="aes128gcm")["body"])


async def send(target: PushTarget, payload: dict[str, Any], *, urgency: str = "normal", ttl: int = TTL,
               private_ok: bool | None = None) -> bool:
    """POST one message. False when the subscription is gone (404/410: the caller removes it)."""
    from mailr.db import session_scope

    if not target.p256dh or not target.auth:
        return False
    async with session_scope() as db:
        pair = await _pair(db)
    body = encrypt(target, json.dumps(payload, default=str).encode()[:3800])
    headers = {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(ttl),
               "Urgency": urgency, "Authorization": vapid_header(target.endpoint, pair)}
    if payload.get("key"):
        # The service replaces a queued message with the same topic (one per reminder key).
        headers["Topic"] = _b64(hashlib.sha256(str(payload["key"]).encode()).digest()[:24])
    from mailr.core import netguard

    try:
        # Pinned to a checked address; the push service's answer body is never needed.
        async with netguard.guarded_client(private_ok=private_ok, timeout=10) as c:
            r, _ = await netguard.fetch_capped(c, "POST", target.endpoint, limit=64 * 1024, deadline=20,
                                               content=body, headers=headers)
    except (httpx.HTTPError, netguard.TooLarge, TimeoutError) as e:
        log.warning("web push to %s failed: %s", target.endpoint[:60], type(e).__name__)
        return True
    if r.status_code in (404, 410):
        return False
    if r.status_code >= 400:
        log.warning("web push to %s failed: %s", target.endpoint[:60], r.status_code)
    return True
