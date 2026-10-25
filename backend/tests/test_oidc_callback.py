"""Web single sign-on (api/auth_routes.py oidc_start and the callback) against a fake identity
provider: the authorization request (state, nonce, PKCE S256, a safe `next`), a good sign-in,
and every refusal of the callback: the provider's error, an expired or missing sign-in cookie, a
state that does not match, a refused code exchange, an ID token that is unsigned by the
provider, expired, from another issuer or for another app, and a nonce that does not match.
A refusal lands on the sign-in page with the reason and never sets a session."""

import base64
import hashlib
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from authlib.jose import JsonWebKey, jwt
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.db_user

ISSUER = "https://sso.example.test"
CLIENT = "mailr-web-test"
KEY = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "k1"})
OTHER_KEY = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "k1"})
EN = json.loads((Path(__file__).resolve().parent.parent / "mailr/i18n/en.json").read_text())["errors"]["auth"]


class Provider:
    """The provider's token and key endpoints. `token_claims` changes what the next exchange
    returns; `token_status` makes it refuse."""

    def __init__(self):
        self.token_status = 200
        self.claims: dict = {}
        self.key = KEY
        self.exchanges: list[dict] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        if str(req.url) == f"{ISSUER}/token":
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            self.exchanges.append(form)
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            token = jwt.encode({"alg": "RS256", "kid": "k1"}, self.claims, self.key).decode()
            return httpx.Response(200, json={"id_token": token, "access_token": "at", "token_type": "Bearer"})
        if str(req.url) == f"{ISSUER}/jwks":
            return httpx.Response(200, json={"keys": [KEY.as_dict(is_private=False)]})
        return httpx.Response(404)


@pytest.fixture
async def sso(app, user, monkeypatch):
    from mailr.api import auth_routes
    from mailr.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "oidc_issuer", ISSUER)
    monkeypatch.setattr(s, "oidc_client_id", CLIENT)
    monkeypatch.setattr(s, "oidc_client_secret", "")
    monkeypatch.setattr(s, "oidc_auto_create", False)
    monkeypatch.setattr(s, "base_url", "http://test")
    monkeypatch.setitem(auth_routes._DISCOVERY, ISSUER, {
        "issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token", "jwks_uri": f"{ISSUER}/jwks"})
    prov = Provider()
    real = httpx.AsyncClient
    monkeypatch.setattr(auth_routes, "httpx", SimpleNamespace(
        URL=httpx.URL, AsyncClient=lambda **kw: real(transport=httpx.MockTransport(prov.handler))))
    # The account picture refresh after a sign-in is not the subject here.
    monkeypatch.setattr(auth_routes, "picture_refresh", lambda *a, **kw: None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield SimpleNamespace(client=c, provider=prov, user=user)


def _claims(sso, auth: dict, **over) -> dict:
    now = int(time.time())
    return {"iss": ISSUER, "aud": CLIENT, "sub": f"sub-{uuid.uuid4().hex[:8]}", "email": sso.user.email,
            "email_verified": True, "name": "Test User", "nonce": auth["nonce"], "iat": now, "exp": now + 300, **over}


async def _start(sso, next_: str = "/inbox") -> dict:
    r = await sso.client.get("/api/v1/auth/oidc/start", params={"next": next_})
    assert r.status_code in (302, 307), r.text
    loc = urlsplit(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == f"{ISSUER}/authorize"
    q = {k: v[0] for k, v in parse_qs(loc.query).items()}
    assert "mailr_oidc" in sso.client.cookies
    return q


async def _callback(sso, **params) -> httpx.Response:
    return await sso.client.get("/api/v1/auth/oidc/callback", params=params)


def _refused(r: httpx.Response, code: str) -> None:
    assert r.status_code in (302, 307), r.text
    loc = urlsplit(r.headers["location"])
    assert loc.path == "/login", r.headers["location"]
    assert parse_qs(loc.query)["error"] == [f"Single sign-on failed: {EN[code]}"]
    assert "mailr_session" not in r.cookies
    # The one-time sign-in cookie is gone, so the same state cannot be tried again.
    assert any(h.startswith("mailr_oidc=") and "Max-Age=0" in h for h in r.headers.get_list("set-cookie"))


async def test_a_good_sign_in(sso):
    auth = await _start(sso, "/inbox")
    assert auth["client_id"] == CLIENT and auth["response_type"] == "code" and auth["code_challenge_method"] == "S256"
    assert auth["redirect_uri"] == "http://test/api/v1/auth/oidc/callback" and auth["state"] and auth["nonce"]
    sso.provider.claims = _claims(sso, auth)
    r = await _callback(sso, code="the-code", state=auth["state"])
    assert r.status_code in (302, 307) and r.headers["location"] == "/inbox", r.headers.get("location")
    assert "mailr_session" in r.cookies
    # PKCE: the code goes back with the verifier whose S256 is the challenge sent at the start.
    (ex,) = sso.provider.exchanges
    assert ex["code"] == "the-code" and ex["grant_type"] == "authorization_code" and ex["client_id"] == CLIENT
    digest = base64.urlsafe_b64encode(hashlib.sha256(ex["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert digest == auth["code_challenge"]
    me = await sso.client.get("/api/v1/me")
    assert me.status_code == 200 and me.json()["user"]["email"] == sso.user.email


@pytest.mark.parametrize("next_", ["//evil.example/x", "https://evil.example/", "javascript:alert(1)"])
async def test_next_never_leaves_mailr(sso, next_):
    auth = await _start(sso, next_)
    sso.provider.claims = _claims(sso, auth)
    r = await _callback(sso, code="c", state=auth["state"])
    assert r.headers["location"] == "/"


async def test_the_providers_error_is_refused(sso):
    auth = await _start(sso)
    r = await _callback(sso, error="access_denied<script>", state=auth["state"])
    _refused(r, "provider_refused")
    assert sso.provider.exchanges == []


async def test_a_callback_without_the_sign_in_cookie_is_refused(sso):
    auth = await _start(sso)
    sso.client.cookies.clear()
    sso.provider.claims = _claims(sso, auth)
    _refused(await _callback(sso, code="c", state=auth["state"]), "signin_expired")
    assert sso.provider.exchanges == []


async def test_an_old_or_forged_sign_in_cookie_is_refused(sso, monkeypatch):
    from itsdangerous import URLSafeTimedSerializer

    from mailr.api import auth_routes

    auth = await _start(sso)
    sso.provider.claims = _claims(sso, auth)
    # Forged: the same content signed with another secret.
    good = next(c.value for c in sso.client.cookies.jar if c.name == "mailr_oidc")
    data = auth_routes._serializer().loads(good)
    sso.client.cookies.clear()
    sso.client.cookies.set("mailr_oidc", URLSafeTimedSerializer("not-the-secret", salt="mailr-oidc").dumps(data),
                           path="/api/v1/auth/oidc")
    _refused(await _callback(sso, code="c", state=auth["state"]), "signin_expired")
    # Older than ten minutes (the browser would drop it then; this one sends it anyway).
    sso.client.cookies.clear()
    auth = await _start(sso)
    kept = next(c.value for c in sso.client.cookies.jar if c.name == "mailr_oidc")
    sso.client.cookies.clear()
    sso.client.cookies.set("mailr_oidc", kept, path="/api/v1/auth/oidc")
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 601)
    try:
        r = await _callback(sso, code="c", state=auth["state"])
    finally:
        monkeypatch.setattr(time, "time", real_time)
    _refused(r, "signin_expired")
    assert sso.provider.exchanges == []


async def test_a_state_that_does_not_match_is_refused(sso):
    auth = await _start(sso)
    sso.provider.claims = _claims(sso, auth)
    _refused(await _callback(sso, code="c", state=auth["state"] + "x"), "state_mismatch")
    await _start(sso)
    _refused(await _callback(sso, code="c"), "state_mismatch")
    assert sso.provider.exchanges == []


async def test_a_refused_code_exchange_is_refused(sso):
    auth = await _start(sso)
    sso.provider.token_status = 400
    _refused(await _callback(sso, code="c", state=auth["state"]), "provider_not_accepted")


@pytest.mark.parametrize("bad, code", [
    ("signed_by_another_key", "token_unverifiable"),
    ("expired", "token_unverifiable"),
    ("another_issuer", "token_wrong_app"),
    ("another_app", "token_wrong_app"),
    ("another_nonce", "nonce_mismatch"),
    ("no_nonce", "nonce_mismatch"),
])
async def test_an_id_token_that_is_not_for_this_sign_in_is_refused(sso, bad, code):
    auth = await _start(sso)
    claims = _claims(sso, auth)
    if bad == "signed_by_another_key":
        sso.provider.key = OTHER_KEY
    elif bad == "expired":
        claims.update(iat=int(time.time()) - 3600, exp=int(time.time()) - 600)
    elif bad == "another_issuer":
        claims["iss"] = "https://evil.example.test"
    elif bad == "another_app":
        claims["aud"] = ["someone-else"]
    elif bad == "another_nonce":
        claims["nonce"] = "n-" + auth["nonce"]
    elif bad == "no_nonce":
        del claims["nonce"]
    sso.provider.claims = claims
    _refused(await _callback(sso, code="c", state=auth["state"]), code)
    # Nothing signed in: /me is still refused.
    assert (await sso.client.get("/api/v1/me")).status_code == 401
