"""Envelope encryption.

The instance key (MAILR_SECRET_KEY) wraps a random data key per user. Secrets such as
provider passwords, OAuth refresh tokens, and the user's Stalwart password are encrypted
with that data key using AES-256-GCM. Rotating the instance key only re-wraps data keys.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mailr.config import get_settings

_V1 = b"\x01"  # AAD binds the user (or "instance") only
_V2 = b"\x02"  # AAD also binds a context: the row and field the value belongs to
_VERSION = _V2
MIN_KEY_BYTES = 32
log = logging.getLogger("mailr.crypto")


class WeakInstanceKey(RuntimeError):
    pass


def _decode_key(raw: str) -> bytes | None:
    try:
        return base64.b64decode(raw, validate=True)
    except Exception:  # noqa: BLE001
        return None


def check_instance_key(raw: str | None = None) -> None:
    """Refuse an instance key that is not 32+ random bytes in base64 (`mailr gen-secret`).
    MAILR_ALLOW_WEAK_SECRET_KEY=true turns the refusal into a warning, for an existing install
    that must keep reading what it stored until the secrets are entered again under a new key."""
    raw = get_settings().secret_key if raw is None else raw
    if not raw:
        raise RuntimeError("MAILR_SECRET_KEY is not set")
    key = _decode_key(raw)
    problem = None
    if key is None:
        problem = "is not base64"
    elif len(key) < MIN_KEY_BYTES:
        problem = f"decodes to {len(key)} bytes; at least {MIN_KEY_BYTES} are needed"
    elif len(set(key)) < 12:
        problem = "is not random (too few distinct bytes)"
    if problem is None:
        return
    msg = f"MAILR_SECRET_KEY {problem}. Generate one with `mailr gen-secret`."
    if os.environ.get("MAILR_ALLOW_WEAK_SECRET_KEY", "").strip().lower() in ("1", "true", "yes"):
        log.warning("%s (accepted because MAILR_ALLOW_WEAK_SECRET_KEY is set)", msg)
        return
    raise WeakInstanceKey(msg)


def _instance_key() -> bytes:
    raw = get_settings().secret_key
    check_instance_key(raw)
    key = _decode_key(raw)
    if key is None:  # only with MAILR_ALLOW_WEAK_SECRET_KEY
        key = raw.encode()
    # Normalise any length to 32 bytes (unchanged, so existing ciphertexts stay readable).
    return hashlib.sha256(b"mailr-instance-key" + key).digest()


def _seal(key: bytes, plaintext: bytes, aad: bytes = b"", context: str | None = None) -> bytes:
    nonce = os.urandom(12)
    if context is None:
        return _V1 + nonce + AESGCM(key).encrypt(nonce, plaintext, aad)
    ctx = context.encode()
    if len(ctx) > 255:
        raise ValueError("context too long")
    return _V2 + bytes([len(ctx)]) + ctx + nonce + AESGCM(key).encrypt(nonce, plaintext, aad + b"|" + ctx)


def _legacy_reads() -> bool:
    return bool(getattr(get_settings(), "crypto_legacy_reads", False))


def _open(key: bytes, blob: bytes, aad: bytes = b"", context: str | None = None) -> bytes:
    """Decrypt. With `context`, a v2 value sealed for another row or field fails, and so does a
    v1 value (written before contexts existed): the startup upgrade (upgrade_ciphertexts, which
    reads without a context) has rewritten every readable one, so a v1 value where a context is
    required can only be an old copy put back into the database.
    MAILR_CRYPTO_LEGACY_READS=true reads them again, for a database restored from before the
    upgrade until the app has started once."""
    if not blob:
        raise ValueError("empty ciphertext")
    if blob[:1] == _V1:
        if context is not None and not _legacy_reads():
            raise ValueError("ciphertext is not bound to its record (written before contexts existed)")
        return AESGCM(key).decrypt(blob[1:13], blob[13:], aad)
    if blob[:1] == _V2:
        n = blob[1]
        ctx = blob[2:2 + n]
        if context is not None and ctx != context.encode():
            raise ValueError("ciphertext belongs to another record")
        rest = blob[2 + n:]
        return AESGCM(key).decrypt(rest[:12], rest[12:], aad + b"|" + ctx)
    raise ValueError("unknown ciphertext version")


def is_legacy(blob: bytes | None) -> bool:
    return bool(blob) and blob[:1] == _V1


def connector_context(connector_id: int) -> str:
    return f"connector:{connector_id}:secrets"


# Instance-level secrets: each names the row and field it is stored in.
def model_endpoint_context(endpoint_id: int) -> str:
    return f"model_endpoint:{endpoint_id}:api_key"


def web_search_provider_context(provider_id: int) -> str:
    return f"web_search_provider:{provider_id}:api_key"


EGRESS_CONTEXT = "instance_setting:image_proxy_egress"
VAPID_CONTEXT = "instance_setting:webpush_vapid:private"


def new_wrapped_data_key(user_id: int) -> bytes:
    return _seal(_instance_key(), os.urandom(32), aad=f"user:{user_id}".encode())


def unwrap_data_key(user_id: int, wrapped: bytes) -> bytes:
    return _open(_instance_key(), wrapped, aad=f"user:{user_id}".encode())


def encrypt_for_user(user_id: int, wrapped_key: bytes, value: Any, context: str | None = None) -> bytes:
    """`context` names the record and field (e.g. connector_context(id)): the value then
    decrypts only there, so it cannot be moved onto another connector."""
    data_key = unwrap_data_key(user_id, wrapped_key)
    return _seal(data_key, json.dumps(value).encode(), aad=f"user:{user_id}".encode(), context=context)


def decrypt_for_user(user_id: int, wrapped_key: bytes, blob: bytes | None, context: str | None = None) -> Any:
    if not blob:
        return None
    data_key = unwrap_data_key(user_id, wrapped_key)
    return json.loads(_open(data_key, blob, aad=f"user:{user_id}".encode(), context=context))


def encrypt_instance(value: Any, context: str | None = None) -> bytes:
    """For instance-level secrets (model endpoint API keys, the egress proxy, the push key).
    `context` names the row and field (model_endpoint_context(id), EGRESS_CONTEXT,
    VAPID_CONTEXT): the value then decrypts only there."""
    return _seal(_instance_key(), json.dumps(value).encode(), aad=b"instance", context=context)


def decrypt_instance(blob: bytes | None, context: str | None = None) -> Any:
    if not blob:
        return None
    return json.loads(_open(_instance_key(), blob, aad=b"instance", context=context))


async def upgrade_ciphertexts(db) -> int:
    """Rewrite secrets stored before contexts existed so they are bound to their row and field:
    connector secrets (per user) and the instance-level ones (model endpoint API keys, the
    egress proxy, the push key). Runs at startup; idempotent. Returns the number rewritten."""
    import base64

    from sqlalchemy import select

    from mailr.models import Connector, InstanceSetting, ModelEndpoint, User

    n = 0
    rows = (await db.execute(select(Connector).where(Connector.secrets_enc.is_not(None)))).scalars().all()
    users: dict[int, User] = {}
    for row in rows:
        if not is_legacy(row.secrets_enc):
            continue
        user = users.get(row.user_id) or await db.get(User, row.user_id)
        users[row.user_id] = user
        try:
            value = decrypt_for_user(user.id, user.data_key_wrapped, row.secrets_enc)
        except Exception:  # noqa: BLE001  unreadable (another key): leave it for the user to re-enter
            log.warning("connector %s secrets cannot be read; not upgraded", row.id)
            continue
        row.secrets_enc = encrypt_for_user(user.id, user.data_key_wrapped, value, context=connector_context(row.id))
        n += 1

    def reseal(blob: bytes, context: str, what: str) -> bytes | None:
        try:
            return encrypt_instance(decrypt_instance(blob), context=context)
        except Exception:  # noqa: BLE001  unreadable (another key): leave it for an admin to re-enter
            log.warning("%s cannot be read; not upgraded", what)
            return None

    eps = (await db.execute(select(ModelEndpoint).where(ModelEndpoint.api_key_enc.is_not(None)))).scalars().all()
    for ep in eps:
        if is_legacy(ep.api_key_enc):
            blob = reseal(ep.api_key_enc, model_endpoint_context(ep.id), f"model endpoint {ep.id} API key")
            if blob is not None:
                ep.api_key_enc = blob
                n += 1

    row = await db.get(InstanceSetting, "image_proxy_egress")
    if row is not None and isinstance(row.value, str) and row.value.startswith("enc:"):
        blob = base64.b64decode(row.value[4:])
        if is_legacy(blob):
            sealed = reseal(blob, EGRESS_CONTEXT, "image_proxy_egress")
            if sealed is not None:
                row.value = "enc:" + base64.b64encode(sealed).decode()
                n += 1

    row = await db.get(InstanceSetting, "webpush_vapid")
    if row is not None and isinstance(row.value, dict) and row.value.get("private"):
        blob = base64.b64decode(row.value["private"])
        if is_legacy(blob):
            sealed = reseal(blob, VAPID_CONTEXT, "webpush_vapid")
            if sealed is not None:
                row.value = {**row.value, "private": base64.b64encode(sealed).decode()}
                n += 1
    if n:
        await db.commit()
    return n


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
