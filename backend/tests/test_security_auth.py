"""Regression tests for authentication and admin: each test is an attack that must fail."""

import time
import uuid

from helpers.native_auth import CLIENT, ISSUER, SEED, issuer  # noqa: F401  (fixture)
from httpx import ASGITransport, AsyncClient


async def _c(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_native_sign_in_never_relinks_an_account_linked_to_another_subject(app, issuer, user):
    from mailr.db import sessionmaker
    from mailr.models import User

    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.oidc_sub = "victim-sub-A"
        await db.commit()
    tok = issuer(sub="attacker-sub-B", email=user.email, email_verified=True)
    async with await _c(app) as c:
        r = await c.post("/api/v1/auth/oidc/native", json={"id_token": tok, "nonce_seed": SEED})
    assert r.status_code == 403 and "different sign-in" in r.text
    async with sessionmaker()() as db:
        assert (await db.get(User, user.id)).oidc_sub == "victim-sub-A"


async def test_native_admin_group_demotes(app, issuer, monkeypatch):
    from mailr.config import get_settings
    from mailr.db import sessionmaker
    from mailr.models import User

    monkeypatch.setattr(get_settings(), "oidc_admin_group", "admins")
    monkeypatch.setattr(get_settings(), "oidc_auto_create", True)
    sub = f"s-{uuid.uuid4().hex[:8]}"
    email = f"g-{uuid.uuid4().hex[:8]}@example.com"
    async with await _c(app) as c:
        r1 = await c.post("/api/v1/auth/oidc/native", json={"id_token": issuer(sub=sub, email=email, groups=["admins"]), "nonce_seed": SEED})
        r2 = await c.post("/api/v1/auth/oidc/native", json={"id_token": issuer(sub=sub, email=email, groups=[]), "nonce_seed": SEED})
    assert r1.json()["user"]["role"] == "admin"
    assert r2.status_code == 200 and r2.json()["user"]["role"] == "user"
    async with sessionmaker()() as db:
        assert (await db.get(User, r2.json()["user"]["id"])).role == "user"


async def test_native_id_token_is_single_use_and_fresh(app, issuer, monkeypatch):
    from mailr.config import get_settings

    monkeypatch.setattr(get_settings(), "oidc_auto_create", True)
    tok = issuer()
    async with await _c(app) as c:
        first = await c.post("/api/v1/auth/oidc/native", json={"id_token": tok, "nonce_seed": SEED})
        again = await c.post("/api/v1/auth/oidc/native", json={"id_token": tok, "nonce_seed": SEED})
        old = await c.post("/api/v1/auth/oidc/native",
                           json={"id_token": issuer(iat=int(time.time()) - 3600, exp=int(time.time()) + 300)})
    assert first.status_code == 200
    assert again.status_code == 401 and "already used" in again.text
    assert old.status_code == 401 and "too old" in old.text


async def test_password_cannot_be_planted_when_local_login_is_off(client, monkeypatch):
    from mailr.config import get_settings

    monkeypatch.setattr(get_settings(), "local_login", False)
    r = await client.post("/api/v1/me/password", json={"current": "", "new": "attacker-password-1"})
    assert r.status_code == 403
    monkeypatch.setattr(get_settings(), "allow_signup", True)
    r = await client.post("/api/v1/auth/signup", json={"email": "x@example.com", "password": "long-enough-pw"})
    assert r.status_code == 403


async def test_session_revoke_needs_an_exact_id(client):
    before = (await client.get("/api/v1/me/sessions")).json()
    r = await client.delete("/api/v1/me/sessions/%25")
    assert r.status_code == 400
    assert (await client.get("/api/v1/me")).status_code == 200
    assert len((await client.get("/api/v1/me/sessions")).json()) == len(before)


# The bad certificate fails validation before anything reaches the shared dev Stalwart
# (tls.install validates first), so this runs beside other tests; test_tls.py installs real ones.
async def test_admin_tokens_cannot_administer_but_tls_tokens_can_push_certs(app, admin_client):
    adm = (await admin_client.post("/api/v1/tokens", json={"name": "adm", "scopes": ["admin"]})).json()["token"]
    tls = (await admin_client.post("/api/v1/tokens", json={"name": "tls", "scopes": ["tls"]})).json()["token"]
    async with await _c(app) as c:
        r = await c.post("/api/v1/admin/users", headers={"Authorization": f"Bearer {adm}"},
                         json={"email": "evil@example.com", "role": "admin", "password": "evil-password-1"})
        assert r.status_code == 403
        r = await c.get("/api/v1/admin/models", headers={"Authorization": f"Bearer {tls}"})
        assert r.status_code == 403
        # The TLS route accepts the tls scope (the bad certificate is then rejected with 400).
        r = await c.put("/api/v1/admin/tls", headers={"Authorization": f"Bearer {tls}"},
                        json={"cert_pem": "x", "key_pem": "y"})
        assert r.status_code not in (401, 403), r.text


async def test_changing_an_endpoint_url_drops_its_stored_key(admin_client):
    body = {"name": "m", "base_url": "http://127.0.0.1:9/v1", "api_key": "sk-secret", "locality": "local",
            "adapter": "openai", "models": [], "defaults": {}, "enabled": False}
    r = await admin_client.post("/api/v1/admin/models", json=body)
    if r.status_code == 400:  # the outbound guard refused loopback on this instance
        body["base_url"] = "http://192.168.1.10:9/v1"
        r = await admin_client.post("/api/v1/admin/models", json=body)
    assert r.status_code == 200, r.text
    eid = r.json()["id"]
    assert r.json()["has_api_key"] is True
    moved = dict(body, base_url="http://192.168.1.11:9/v1")
    moved.pop("api_key")
    r = await admin_client.patch(f"/api/v1/admin/models/{eid}", json=moved)
    assert r.status_code == 200, r.text
    assert r.json()["has_api_key"] is False


async def test_tokens_do_not_get_the_live_stream_or_session_list(app, client):
    tok = (await client.post("/api/v1/tokens", json={"name": "r", "scopes": ["read"]})).json()["token"]
    async with await _c(app) as c:
        h = {"Authorization": f"Bearer {tok}"}
        assert (await c.get("/api/v1/events", headers=h)).status_code == 403
        assert (await c.get("/api/v1/me/sessions", headers=h)).status_code == 403


async def test_api_docs_are_not_public_and_api_is_not_cached(app, monkeypatch):
    from mailr.config import get_settings
    from mailr.main import create_app

    monkeypatch.setattr(get_settings(), "dev", False)
    prod = create_app()
    # The docs exist, but signed out they answer like a path that does not (tests/test_api_docs.py).
    async with await _c(prod) as c:
        assert (await c.get("/api/v1/openapi.json")).status_code == 404
        assert (await c.get("/api/v1/docs")).status_code == 404
    async with await _c(app) as c:
        assert (await c.get("/api/v1/health")).headers.get("cache-control") == "no-store"


async def test_mcp_rejects_unknown_tokens_before_doing_any_work(app):
    async with await _c(app) as c:
        r = await c.post("/mcp", headers={"Authorization": "Bearer mlr_junk_123", "Accept": "application/json"},
                         json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert "mailr.internal" not in r.text


async def test_idempotency_does_not_store_refusals_or_secrets(app, client):
    from sqlalchemy import text

    from mailr.db import sessionmaker

    async with sessionmaker()() as db:
        before = (await db.execute(text("select count(*) from idempotency_keys"))).scalar()
    async with await _c(app) as c:
        for i in range(3):
            await c.post("/api/v1/labels", headers={"Authorization": "Bearer junk", "Idempotency-Key": f"k{i}"},
                         json={"name": "x"})
    r = await client.post("/api/v1/tokens", headers={"Idempotency-Key": "tok-1"}, json={"name": "t", "scopes": ["read"]})
    assert r.status_code == 200
    async with sessionmaker()() as db:
        after = (await db.execute(text("select count(*) from idempotency_keys"))).scalar()
        leaked = (await db.execute(text("select count(*) from idempotency_keys where response::text like '%mlr_%'"))).scalar()
    assert after == before and leaked == 0
