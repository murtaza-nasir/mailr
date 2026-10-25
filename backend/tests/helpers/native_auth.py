"""Shared helpers, from test_native_auth.py."""

import base64
import hashlib
import secrets
import time
import uuid

import pytest
from authlib.jose import JsonWebKey, jwt

ISSUER = "https://id.example.test"


CLIENT = "mailr-android-test"


# The nonce seed the minted tokens are bound with by default (post it as "nonce_seed").
SEED = "seed-for-the-tests-0123456789"


@pytest.fixture
def issuer(monkeypatch):
    """A stub OIDC issuer: a fresh RSA key, its JWKS, and a token minting helper."""
    from mailr.api import native_auth
    from mailr.config import get_settings

    key = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "k1"})
    jwks = {"keys": [key.as_dict(is_private=False)]}
    s = get_settings()
    monkeypatch.setattr(s, "oidc_issuer", ISSUER)
    monkeypatch.setattr(s, "oidc_native_client_id", CLIENT)
    monkeypatch.setattr(s, "oidc_auto_create", True)  # off by default; these tests sign new people in

    async def discovery():
        return {"issuer": ISSUER, "jwks_uri": ISSUER + "/jwks"}

    async def fetch_jwks(uri, refresh=False):
        return jwks

    monkeypatch.setattr(native_auth, "_discovery", discovery)
    monkeypatch.setattr(native_auth, "_jwks", fetch_jwks)

    def mint(**over):
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT, "sub": f"sub-{uuid.uuid4().hex[:8]}",
                  "email": f"n-{uuid.uuid4().hex[:8]}@example.com", "email_verified": True,
                  "name": "Native User", "iat": now, "exp": now + 300, "nonce": _nonce(_own_origin(), SEED)}
        claims.update(over)
        if claims["nonce"] is None:
            del claims["nonce"]
        return jwt.encode({"alg": "RS256", "kid": "k1"}, claims, key).decode()

    return mint


def _nonce(origin: str, seed: str) -> str:
    """Spelled out here (not imported) so the test checks the formula the apps use."""
    return base64.urlsafe_b64encode(hashlib.sha256(f"{origin}|{seed}".encode()).digest()).decode().rstrip("=")


def _own_origin() -> str:
    from mailr.api.native_auth import canonical_origin
    from mailr.config import get_settings

    return canonical_origin(get_settings().base_url)


def bound(issuer, origin: str | None = None, **over) -> dict:
    """The body the app posts: an ID token whose nonce binds it to `origin` (this server by default)."""
    seed = secrets.token_urlsafe(16)
    return {"id_token": issuer(nonce=_nonce(origin or _own_origin(), seed), **over), "nonce_seed": seed}
