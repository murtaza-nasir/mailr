"""Native (Android) sign-in: ID token verification against a stub issuer, and the nonce that binds
the token to this server."""

import json
import pathlib
import secrets
import time

from authlib.jose import JsonWebKey, jwt
from helpers.native_auth import (  # noqa: F401
    CLIENT,
    ISSUER,
    SEED,
    _nonce,
    _own_origin,
    bound,
    issuer,
)
from httpx import ASGITransport, AsyncClient


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_native_config(app, issuer):
    async with await _client(app) as c:
        r = await c.get("/api/v1/auth/native-config")
    body = r.json()
    assert body["enabled"] and body["client_id"] == CLIENT and body["issuer"] == ISSUER
    assert body["redirect_uri"] == "io.github.murtazanasir.mailr:/oauth2redirect"
    # No name guessed from the issuer's host: the app then says "single sign-on".
    assert body["provider_name"] == ""


async def test_native_config_names_the_provider(app, issuer, monkeypatch):
    from mailr.config import get_settings

    monkeypatch.setattr(get_settings(), "oidc_name", " Pocket ID ")
    async with await _client(app) as c:
        native = (await c.get("/api/v1/auth/native-config")).json()
        web = (await c.get("/api/v1/auth/config")).json()
    assert native["provider_name"] == web["oidc_name"] == "Pocket ID"


async def test_native_config_for_the_notes_app(app, issuer):
    # The Notes app receives the redirect on its own scheme; the rest is shared.
    async with await _client(app) as c:
        notes = (await c.get("/api/v1/auth/native-config", params={"app": "notes"})).json()
        mailr = (await c.get("/api/v1/auth/native-config")).json()
    assert notes["redirect_uri"] == "io.github.murtazanasir.mailr.notes:/oauth2redirect"
    assert notes["client_id"] == mailr["client_id"] == CLIENT and notes["issuer"] == mailr["issuer"]


async def test_native_sign_in_creates_session_usable_as_cookie(app, issuer):
    async with await _client(app) as c:
        r = await c.post("/api/v1/auth/oidc/native", json={**bound(issuer), "device_name": "Pixel Fold"})
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["session_token"] and s["csrf"] and s["user"]["email"].startswith("n-")
    # The app sends the session as a cookie and echoes the CSRF token on mutations.
    async with await _client(app) as c:
        c.cookies.set("mailr_session", s["session_token"])
        me = await c.get("/api/v1/me")
        assert me.status_code == 200 and me.json()["user"]["email"] == s["user"]["email"]
        no_csrf = await c.post("/api/v1/auth/native/logout")
        assert no_csrf.status_code == 403
        out = await c.post("/api/v1/auth/native/logout", headers={"X-CSRF-Token": s["csrf"]})
        assert out.status_code == 200
        assert (await c.get("/api/v1/me")).status_code == 401


async def test_native_sign_in_rejects_bad_tokens(app, issuer):
    other_key = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "k1"})
    now = int(time.time())
    forged = jwt.encode({"alg": "RS256", "kid": "k1"},
                        {"iss": ISSUER, "aud": CLIENT, "sub": "x", "email": "x@example.com",
                         "email_verified": True, "iat": now, "exp": now + 300}, other_key).decode()
    cases = {
        "forged signature": forged,
        "wrong audience": issuer(aud="someone-else"),
        "wrong issuer": issuer(iss="https://evil.example"),
        "expired": issuer(iat=now - 7200, exp=now - 3600),
    }
    async with await _client(app) as c:
        for name, tok in cases.items():
            r = await c.post("/api/v1/auth/oidc/native", json={"id_token": tok, "nonce_seed": SEED})
            assert r.status_code == 401, (name, r.text)


async def test_native_sign_in_links_existing_account_only_when_email_verified(app, issuer, user):
    async with await _client(app) as c:
        r = await c.post("/api/v1/auth/oidc/native", json=bound(issuer, email=user.email, email_verified=False))
        assert r.status_code == 403
        r = await c.post("/api/v1/auth/oidc/native", json=bound(issuer, email=user.email))
        assert r.status_code == 200 and r.json()["user"]["id"] == user.id


# ---------------- The ID token is bound to this server ----------------


def test_nonce_formula_matches_the_shared_cases():
    """The apps (session-core NonceBinding.kt) read the same file."""
    from mailr.api.native_auth import bound_nonce, canonical_origin

    here = pathlib.Path(__file__).parent / "fixtures" / "native_nonce_cases.json"
    android = pathlib.Path(__file__).resolve().parents[2] / "android" / "app" / "src" / "test" / "resources" / "native_nonce_cases.json"
    assert android.read_text() == here.read_text(), "the Android copy of the cases differs"
    cases = json.loads(here.read_text())
    for c in cases["origins"]:
        assert canonical_origin(c["in"]) == c["out"], c
    for c in cases["nonces"]:
        assert bound_nonce(c["origin"], c["seed"]) == c["nonce"] == _nonce(c["origin"], c["seed"]), c


async def test_a_token_asked_for_while_the_app_talked_to_another_server_is_refused(app, issuer):
    """A planted setup link made the app sign in through a look-alike host, which
    replays the real ID token here. Its nonce names the other host, so it is refused, and the
    proper token for this server still works afterwards (the refused one was not burned)."""
    relayed = bound(issuer, origin="https://mail-example.evil.example")
    async with await _client(app) as c:
        r = await c.post("/api/v1/auth/oidc/native", json=relayed)
        assert r.status_code == 401 and r.json()["code"] == "auth.id_token_other_server", r.text
        # The seed alone cannot be swapped: the nonce is in the signed token.
        r = await c.post("/api/v1/auth/oidc/native", json={**relayed, "nonce_seed": secrets.token_urlsafe(16)})
        assert r.status_code == 401
        own = bound(issuer)
        assert (await c.post("/api/v1/auth/oidc/native", json=own)).status_code == 200
        # Single use, as before.
        again = await c.post("/api/v1/auth/oidc/native", json=own)
        assert again.status_code == 401 and again.json()["code"] == "auth.signin_used"


async def test_a_token_with_no_nonce_or_a_foreign_one_is_refused(app, issuer):
    """Another app on the phone that runs its own flow with mailr's public client id
    gets a token with its own nonce (or none); it does not sign in."""
    async with await _client(app) as c:
        for tok in (issuer(nonce=None), issuer(nonce="whatever-the-other-app-chose")):
            r = await c.post("/api/v1/auth/oidc/native", json={"id_token": tok, "nonce_seed": secrets.token_urlsafe(16)})
            assert r.status_code == 401 and r.json()["code"] == "auth.id_token_other_server", r.text


async def test_older_apps_without_a_seed_are_asked_to_update_unless_the_admin_allows_them(app, issuer, monkeypatch):
    from mailr.config import get_settings

    async with await _client(app) as c:
        r = await c.post("/api/v1/auth/oidc/native", json={"id_token": issuer()})
        assert r.status_code == 426 and r.json()["code"] == "auth.native_update_app", r.text
        monkeypatch.setattr(get_settings(), "oidc_native_unbound", True)
        assert (await c.post("/api/v1/auth/oidc/native", json={"id_token": issuer()})).status_code == 200
        # A seed, when sent, is still checked.
        bad = bound(issuer, origin="https://mail-example.evil.example")
        assert (await c.post("/api/v1/auth/oidc/native", json=bad)).status_code == 401


async def test_another_address_of_this_server_counts_only_when_listed(app, issuer, monkeypatch):
    from mailr.config import get_settings

    lan = "http://192.168.1.20:8025"
    async with await _client(app) as c:
        assert (await c.post("/api/v1/auth/oidc/native", json=bound(issuer, origin=lan))).status_code == 401
        monkeypatch.setattr(get_settings(), "oidc_native_origins", "https://other.example.com, http://192.168.1.20:8025/")
        assert (await c.post("/api/v1/auth/oidc/native", json=bound(issuer, origin=lan))).status_code == 200
        assert (await c.post("/api/v1/auth/oidc/native", json=bound(issuer))).status_code == 200

