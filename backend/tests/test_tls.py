import datetime as dt
import socket
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from mailr.core import tls


def _pair(cn: str):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=1))
            .not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False)
            .sign(key, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode())


def _served_cn() -> str:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("localhost", 11993), timeout=5) as s, ctx.wrap_socket(s) as t:
        der = t.getpeercert(binary_form=True)
    return x509.load_der_x509_certificate(der).subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value


# Installs a certificate on the shared dev Stalwart: two at once race (TlsError).
@pytest.mark.serial
async def test_install_certificate_is_served(app):
    cert, key = _pair("imap-test-1.local")
    r = await tls.install(cert, key)
    assert r["changed"] and r["names"] == ["imap-test-1.local"]
    assert _served_cn() == "imap-test-1.local"
    again = await tls.install(cert, key)
    assert again["changed"] is False
    cert2, key2 = _pair("imap-test-2.local")
    await tls.install(cert2, key2)
    assert _served_cn() == "imap-test-2.local"
    with pytest.raises(tls.TlsError):
        tls.validate(cert, key2)
