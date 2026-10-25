"""IMAP TLS certificate management.

An admin (or an automated pusher, e.g. from a reverse proxy that renews Let's Encrypt
certificates) uploads a certificate chain and key. mailr validates the pair and hands it
to Stalwart inline over its management API, where it becomes the default certificate for
IMAP; Stalwart stores it and reloads immediately. The key is kept only by Stalwart; mailr
records the chain's details (names, expiry, fingerprint) to report status and to skip
unchanged uploads.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtensionOID, NameOID

from mailr.core.jmap import CORE
from mailr.core.store import service_client
from mailr.db import session_scope
from mailr.models import InstanceSetting

log = logging.getLogger("mailr.tls")
MGMT = [CORE, "urn:stalwart:jmap"]
SETTING = "imap_tls"


class TlsError(ValueError):
    pass


def inspect(chain_pem: str) -> dict[str, Any]:
    certs = x509.load_pem_x509_certificates(chain_pem.encode())
    leaf = certs[0]
    try:
        sans = leaf.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        sans = []
    cn = leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    issuer = leaf.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
    return {"subject": cn[0].value if cn else "", "names": sans, "issuer": issuer[0].value if issuer else "",
            "not_after": leaf.not_valid_after_utc.isoformat(),
            "not_before": leaf.not_valid_before_utc.isoformat(), "chain_length": len(certs),
            "fingerprint": hashlib.sha256(leaf.public_bytes(serialization.Encoding.DER)).hexdigest()}


def validate(chain_pem: str, key_pem: str) -> dict[str, Any]:
    try:
        certs = x509.load_pem_x509_certificates(chain_pem.encode())
    except ValueError as e:
        raise TlsError(f"certificate is not valid PEM: {e}") from None
    try:
        key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    except (ValueError, TypeError) as e:
        raise TlsError(f"private key is not valid (unencrypted PEM required): {e}") from None
    pub, der = serialization.PublicFormat.SubjectPublicKeyInfo, serialization.Encoding.DER
    if certs[0].public_key().public_bytes(der, pub) != key.public_key().public_bytes(der, pub):
        raise TlsError("the private key does not match the certificate")
    if certs[0].not_valid_after_utc <= datetime.now(UTC):
        raise TlsError("the certificate has expired")
    return inspect(chain_pem)


async def _mgmt(calls: list) -> list:
    return await service_client().call(calls, using=MGMT)


async def status() -> dict[str, Any] | None:
    async with session_scope() as db:
        row = await db.get(InstanceSetting, SETTING)
        return dict(row.value) if row else None


async def install(chain_pem: str, key_pem: str) -> dict[str, Any]:
    info = validate(chain_pem, key_pem)
    current = await status()
    if current and current.get("fingerprint") == info["fingerprint"]:
        # Same certificate: make sure Stalwart still has it as default (e.g. after a reset).
        got = await _mgmt([["x:Certificate/get", {"ids": [current["certificate_id"]], "properties": ["id"]}, "g"]])
        if got[0][1].get("list"):
            return {**info, "changed": False}
    obj = {"certificate": {"@type": "Text", "value": chain_pem},
           "privateKey": {"@type": "Text", "secret": key_pem}}
    res = await _mgmt([["x:Certificate/set", {"create": {"c": obj}}, "s"]])
    made = (res[0][1].get("created") or {}).get("c")
    if not made:
        raise TlsError(f"Stalwart rejected the certificate: {res[0][1].get('notCreated')}")
    cid = made["id"]
    res = await _mgmt([
        ["x:SystemSettings/set", {"update": {"singleton": {"defaultCertificateId": cid}}}, "d"],
        ["x:Action/set", {"create": {"a": {"@type": "ReloadTlsCertificates"}}}, "r"],
    ])
    bad = (res[1][1].get("notCreated") or {}).get("a")
    if bad:
        raise TlsError(f"Stalwart could not load the certificate: {bad.get('description')}")
    if current and current.get("certificate_id") and current["certificate_id"] != cid:
        await _mgmt([["x:Certificate/set", {"destroy": [current["certificate_id"]]}, "x"]])
    record = {**info, "certificate_id": cid, "installed_at": datetime.now(UTC).isoformat()}
    async with session_scope() as db:
        row = await db.get(InstanceSetting, SETTING)
        if row is None:
            db.add(InstanceSetting(key=SETTING, value=record))
        else:
            row.value = record
    log.info("installed IMAP certificate for %s (expires %s)", info["names"] or info["subject"], info["not_after"])
    return {**info, "changed": True}
