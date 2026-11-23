"""Connect Speakr with single sign-on (integrations/speakr/sso.py): the start link and its ticket,
the sign-in at a fake identity provider, and the exchange at a fake Speakr. The provider's access
token goes only to the exchange (never stored or logged); someone else's sign-in never reaches
Speakr; each refusal of Speakr lands on the settings with its own result; a renewal revokes the
old token."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from authlib.jose import JsonWebKey, jwt
from speakr_fake import BASE, TOKEN, connect, speakr  # noqa: F401

pytestmark = pytest.mark.db_user

ISSUER = "https://sso.example.test"
CLIENT = "mailr-web-test"
KEY = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "k1"})
ACCESS = "pid-access-token-0123456789-do-not-store"
NEW_TOKEN = "spk_FROM-SSO-0123456789-abcdefghij"


class World:
    def __init__(self, fake):
        self.fake = fake
        self.claims: dict = {}
        self.exchange_status = 201
        self.exchange_body: dict = {}
        self.exchanges: list[tuple[str | None, dict]] = []
        self.deleted: list[tuple[str | None, str]] = []
        self.status_code = 200
        self.status_body: dict = {"sso_token_exchange": True, "sso_exchange_provisioning": True,
                                  "exchange_client_allowed": True}
        self.status_queries: list[tuple[str | None, dict]] = []

    def provider(self, req: httpx.Request) -> httpx.Response:
        if str(req.url) == f"{ISSUER}/token":
            token = jwt.encode({"alg": "RS256", "kid": "k1"}, self.claims, KEY).decode()
            return httpx.Response(200, json={"id_token": token, "access_token": ACCESS, "token_type": "Bearer"})
        if str(req.url) == f"{ISSUER}/jwks":
            return httpx.Response(200, json={"keys": [KEY.as_dict(is_private=False)]})
        return httpx.Response(404)

    def speakr(self, req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/tokens/exchange":
            body = json.loads(req.content or b"{}")
            self.exchanges.append((req.headers.get("authorization"), body))
            if self.exchange_status == 201:
                self.fake.token = NEW_TOKEN
                return httpx.Response(201, json={"id": 15, "name": body.get("name"), "scopes": body.get("scopes"),
                                                 "token": NEW_TOKEN, "sso_client_id": CLIENT})
            return httpx.Response(self.exchange_status, json=self.exchange_body)
        if req.url.path == "/api/v1/tokens/current" and req.method == "DELETE":
            self.deleted.append((req.headers.get("authorization"), req.url.path))
            return httpx.Response(200, json={"message": "revoked"})
        if req.url.path == "/api/v1/sso/status":
            self.status_queries.append((req.headers.get("authorization"), dict(req.url.params)))
            return httpx.Response(self.status_code, json=self.status_body)
        r = self.fake.handler(req)
        if req.url.path == "/api/v1/tokens/current" and r.status_code == 200 \
                and req.headers.get("authorization") == f"Bearer {NEW_TOKEN}":
            # Speakr names the client of a token made by the exchange.
            return httpx.Response(200, json={**r.json(), "name": "Mailr (test)", "sso_client_id": CLIENT})
        return r


@pytest.fixture
async def world(app, user, client, speakr, monkeypatch):
    from sqlalchemy import update

    from mailr.api import auth_routes
    from mailr.config import get_settings
    from mailr.db import sessionmaker
    from mailr.integrations.speakr import client as sc
    from mailr.models import User

    s = get_settings()
    monkeypatch.setattr(s, "oidc_issuer", ISSUER)
    monkeypatch.setattr(s, "oidc_client_id", CLIENT)
    monkeypatch.setattr(s, "oidc_client_secret", "")
    monkeypatch.setattr(s, "base_url", "http://test")
    monkeypatch.setitem(auth_routes._DISCOVERY, ISSUER, {
        "issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token", "jwks_uri": f"{ISSUER}/jwks"})
    w = World(speakr)
    real = httpx.AsyncClient
    monkeypatch.setattr(auth_routes, "httpx", SimpleNamespace(
        URL=httpx.URL, AsyncClient=lambda **kw: real(transport=httpx.MockTransport(w.provider))))
    sc.use_transport(httpx.MockTransport(w.speakr))
    sub = f"sub-{user.id}"
    async with sessionmaker()() as db:
        await db.execute(update(User).where(User.id == user.id).values(oidc_sub=sub))
        await db.commit()
    w.client, w.user, w.sub = client, user, sub
    return w


async def _sign_in(w: World, body: dict, sub: str | None = None) -> httpx.Response:
    r = await w.client.post("/api/v1/speakr/sso/start", json=body)
    assert r.status_code == 200, r.text
    link = urlsplit(r.json()["url"])
    assert link.netloc == "test" and link.path == "/api/v1/auth/oidc/connect"
    r = await w.client.get(link.path, params=dict(parse_qs(link.query, keep_blank_values=True)))
    assert r.status_code in (302, 307), r.text
    auth = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    now = int(time.time())
    w.claims = {"iss": ISSUER, "aud": CLIENT, "sub": sub or w.sub, "nonce": auth["nonce"], "iat": now, "exp": now + 300}
    return await w.client.get("/api/v1/auth/oidc/callback", params={"code": "c", "state": auth["state"]})


def _result(r: httpx.Response) -> str:
    assert r.status_code in (302, 307), r.text
    loc = urlsplit(r.headers["location"])
    assert loc.path == "/settings/mail/accounts" and loc.fragment == "speakr", r.headers["location"]
    return parse_qs(loc.query)["speakr_sso"][0]


async def _connectors(w: World) -> list[dict]:
    return [c for c in (await w.client.get("/api/v1/connectors")).json() if c["kind"] == "speakr"]


async def test_connect_with_sso_exchanges_once_and_stores_the_token(world, caplog):
    world.fake.modern()
    r = await _sign_in(world, {"base_url": BASE, "data_class": "local_only"})
    assert _result(r) == "connected"
    # No session was opened by this sign-in: the browser keeps the one it had.
    assert "mailr_session" not in r.cookies
    ((authz, body),) = world.exchanges
    assert authz == f"Bearer {ACCESS}"
    assert body == {"name": "Mailr (test)", "scopes": ["read", "write", "upload", "share", "webhooks"], "expires_in_days": 0}
    (conn,) = await _connectors(world)
    assert conn["config"]["base_url"] == BASE and conn["data_class"] == "local_only"
    # The stored token is Speakr's new one, used for the probe after the exchange.
    from mailr.db import sessionmaker
    from mailr.integrations.speakr import store
    from mailr.models import Connector

    async with sessionmaker()() as db:
        assert await store.token_of(db, await db.get(Connector, conn["id"])) == NEW_TOKEN
    assert ACCESS not in caplog.text and NEW_TOKEN not in caplog.text


async def test_someone_elses_sign_in_never_reaches_speakr(world):
    assert _result(await _sign_in(world, {"base_url": BASE}, sub="somebody-else")) == "wrong_account"
    assert world.exchanges == [] and await _connectors(world) == []


@pytest.mark.parametrize("status, body, result", [
    (401, {"error": "Authentication required"}, "refused"),
    (403, {"code": "sso_exchange_not_allowed"}, "not_allowed"),
    (409, {"code": "token_limit_reached"}, "limit"),
    (404, {"error": "not found"}, "not_supported"),
    (429, {"error": "slow down"}, "rate_limited"),
    # A newer Speakr says why it has no account for this person.
    (403, {"error": "x", "code": "sso_registration_closed"}, "registration_closed"),
    (409, {"error": "x", "code": "sso_link_required"}, "link_required"),
    (403, {"error": "x", "code": "sso_domain_not_allowed"}, "refused_by_policy"),
    (403, {"error": "x", "code": "sso_email_not_verified"}, "refused_by_policy"),
    (403, {"error": "x", "code": "sso_email_missing"}, "refused_by_policy"),
    (502, {"error": "x", "code": "sso_userinfo_failed"}, "unreachable"),
])
async def test_each_refusal_has_its_own_result(world, status, body, result):
    world.exchange_status, world.exchange_body = status, body
    assert _result(await _sign_in(world, {"base_url": BASE})) == result
    assert await _connectors(world) == []


async def test_a_lower_scope_ceiling_is_taken(world):
    world.fake.modern()
    calls = {"n": 0}
    real = world.speakr

    def ceiling(req):
        if req.url.path == "/api/v1/tokens/exchange" and calls["n"] == 0:
            calls["n"] += 1
            world.exchanges.append((req.headers.get("authorization"), json.loads(req.content)))
            return httpx.Response(400, json={"code": "invalid_parameter", "allowed_scopes": ["read", "upload"]})
        return real(req)

    from mailr.integrations.speakr import client as sc

    sc.use_transport(httpx.MockTransport(ceiling))
    assert _result(await _sign_in(world, {"base_url": BASE})) == "connected"
    assert [b["scopes"] for _, b in world.exchanges] == [["read", "write", "upload", "share", "webhooks"], ["read", "upload"]]


async def test_a_renewal_replaces_the_token_and_revokes_the_old_one(world):
    world.fake.modern(token_self_revoke=True)
    conn = await connect(world.client, world.fake)
    assert _result(await _sign_in(world, {"connector_id": conn["id"]})) == "renewed"
    assert world.deleted == [(f"Bearer {TOKEN}", "/api/v1/tokens/current")]
    assert len(await _connectors(world)) == 1


async def test_without_self_revoke_the_old_token_is_left_to_the_user(world):
    world.fake.modern()
    conn = await connect(world.client, world.fake)
    assert _result(await _sign_in(world, {"connector_id": conn["id"]})) == "renewed_old_kept"
    assert world.deleted == []


async def test_start_refusals(world):
    await connect(world.client, world.fake)
    r = await world.client.post("/api/v1/speakr/sso/start", json={"base_url": BASE})
    assert r.status_code == 409
    r = await world.client.post("/api/v1/speakr/sso/start", json={"connector_id": 999999})
    assert r.status_code == 404
    r = await world.client.post("/api/v1/speakr/sso/start", json={"base_url": "ftp://speakr.test"})
    assert r.status_code == 400


async def test_a_forged_or_old_ticket_starts_nothing(world):
    r = await world.client.get("/api/v1/auth/oidc/connect", params={"ticket": "forged"})
    assert r.status_code in (302, 307) and "speakr_sso=expired" in r.headers["location"]


# ---------------------------------------------------------------- tokens made by the exchange


async def _conn_row(conn_id: int):
    from mailr.db import sessionmaker
    from mailr.models import Connector

    async with sessionmaker()() as db:
        return await db.get(Connector, conn_id)


async def test_an_sso_connection_turns_on_instant_updates_and_names_no_extra_scope(world, monkeypatch):
    from mailr.integrations.speakr import webhook

    monkeypatch.setattr(webhook, "_base", lambda: "https://test")
    world.fake.modern(token_self_revoke=True)
    world.fake.token_scopes = ["read", "write", "upload", "share", "webhooks"]
    r = await _sign_in(world, {"base_url": BASE})
    assert _result(r) == "connected"
    (conn,) = await _connectors(world)
    assert conn["config"]["token_origin"] == "sso"
    # Mailr asked for webhooks itself: the webhook is made, and the scope is not called extra.
    assert conn["config"].get("speakr_webhook_id") and len(world.fake.webhooks) == 1
    assert not any("webhooks" in n for n in conn["speakr"]["scope_notes"])


async def test_removing_an_sso_connection_revokes_its_token(world):
    world.fake.modern(token_self_revoke=True)
    assert _result(await _sign_in(world, {"base_url": BASE})) == "connected"
    (conn,) = await _connectors(world)
    r = await world.client.delete(f"/api/v1/connectors/{conn['id']}")
    assert r.status_code == 200, r.text
    assert world.deleted == [(f"Bearer {NEW_TOKEN}", "/api/v1/tokens/current")]


async def test_a_connection_made_before_the_mark_is_recognised_by_speakrs_record(world):
    """Connected with 0.40.44 (no token_origin): Speakr's sso_client_id and the name tell."""
    from sqlalchemy import update

    from mailr.db import sessionmaker
    from mailr.models import Connector

    world.fake.modern(token_self_revoke=True)
    assert _result(await _sign_in(world, {"base_url": BASE})) == "connected"
    (conn,) = await _connectors(world)
    row = await _conn_row(conn["id"])
    async with sessionmaker()() as db:
        await db.execute(update(Connector).where(Connector.id == conn["id"]).values(
            config={k: v for k, v in row.config.items() if k != "token_origin"}))
        await db.commit()
    await world.client.delete(f"/api/v1/connectors/{conn['id']}")
    assert world.deleted == [(f"Bearer {NEW_TOKEN}", "/api/v1/tokens/current")]


async def test_removing_a_pasted_token_leaves_it_in_speakr(world):
    world.fake.modern(token_self_revoke=True)
    conn = await connect(world.client, world.fake)
    await world.client.delete(f"/api/v1/connectors/{conn['id']}")
    assert world.deleted == []


async def test_a_pasted_token_drops_the_sso_mark(world):
    world.fake.modern(token_self_revoke=True)
    assert _result(await _sign_in(world, {"base_url": BASE})) == "connected"
    (conn,) = await _connectors(world)
    world.fake.token = TOKEN
    r = await world.client.patch(f"/api/v1/connectors/{conn['id']}", json={"secrets": {"token": TOKEN}})
    assert r.status_code == 200, r.text
    assert "token_origin" not in (await _conn_row(conn["id"])).config


# ---------------------------------------------------------------- Speakr for everyone (auto.py)


async def _instance(url: str = BASE, auto_connect: bool = True, data_class: str = "local_only"):
    from mailr.db import sessionmaker
    from mailr.models import InstanceSetting

    async with sessionmaker()() as db:
        row = await db.get(InstanceSetting, "speakr_instance")
        value = {"url": url, "auto_connect": auto_connect, "data_class": data_class}
        if row is None:
            db.add(InstanceSetting(key="speakr_instance", value=value))
        else:
            row.value = value
        await db.commit()


async def _no_instance():
    from mailr.db import sessionmaker
    from mailr.models import InstanceSetting

    async with sessionmaker()() as db:
        row = await db.get(InstanceSetting, "speakr_instance")
        if row is not None:
            await db.delete(row)
            await db.commit()


@pytest.fixture
async def instance(world):
    await _instance()
    yield world
    await _no_instance()


async def _user_state(w: World) -> dict | None:
    from mailr.db import sessionmaker
    from mailr.models import User

    async with sessionmaker()() as db:
        return (await db.get(User, w.user.id)).speakr_auto


async def _age_state(w: World, seconds: int) -> None:
    from sqlalchemy import update

    from mailr.db import sessionmaker
    from mailr.models import User

    st = dict(await _user_state(w) or {})
    st["at"] = int(time.time()) - seconds
    async with sessionmaker()() as db:
        await db.execute(update(User).where(User.id == w.user.id).values(speakr_auto=st))
        await db.commit()


async def test_auto_connect_once_and_tell_the_person(instance, caplog):
    from mailr.integrations.speakr import auto

    w = instance
    w.fake.modern()
    assert await auto.attempt(w.user.id, ACCESS) == "connected"
    (conn,) = await _connectors(w)
    assert conn["config"]["token_origin"] == "auto" and conn["data_class"] == "local_only"
    st = (await w.client.get("/api/v1/speakr/auto")).json()
    assert st["url"] == BASE and st["auto_connect"] and st["state"] == "connected" and st["notice"] is True
    assert (await w.client.post("/api/v1/speakr/auto/seen")).status_code == 200
    assert (await w.client.get("/api/v1/speakr/auto")).json()["notice"] is False
    # Connected: the next sign-in does not ask Speakr again.
    assert await auto.attempt(w.user.id, ACCESS) is None
    assert len(w.exchanges) == 1
    assert ACCESS not in caplog.text and NEW_TOKEN not in caplog.text


async def test_auto_connect_happens_after_a_web_sign_in(instance):
    w = instance
    w.fake.modern()
    r = await w.client.get("/api/v1/auth/oidc/start", params={"next": "/"})
    auth = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    now = int(time.time())
    w.claims = {"iss": ISSUER, "aud": CLIENT, "sub": w.sub, "nonce": auth["nonce"], "iat": now, "exp": now + 300}
    r = await w.client.get("/api/v1/auth/oidc/callback", params={"code": "c", "state": auth["state"]})
    assert r.status_code in (302, 307) and urlsplit(r.headers["location"]).path == "/"
    ((authz, _),) = w.exchanges
    assert authz == f"Bearer {ACCESS}"
    assert len(await _connectors(w)) == 1


async def test_a_speakr_failure_never_affects_the_sign_in(instance):
    w = instance
    w.exchange_status, w.exchange_body = 500, {"error": "boom"}
    r = await w.client.get("/api/v1/auth/oidc/start", params={"next": "/"})
    auth = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    now = int(time.time())
    w.claims = {"iss": ISSUER, "aud": CLIENT, "sub": w.sub, "nonce": auth["nonce"], "iat": now, "exp": now + 300}
    r = await w.client.get("/api/v1/auth/oidc/callback", params={"code": "c", "state": auth["state"]})
    assert r.status_code in (302, 307) and "mailr_session" in r.cookies
    assert (await _user_state(w))["state"] == "failed"


async def test_a_removed_connection_is_not_made_again(instance):
    from mailr.integrations.speakr import auto

    w = instance
    w.fake.modern()
    assert await auto.attempt(w.user.id, ACCESS) == "connected"
    (conn,) = await _connectors(w)
    await w.client.delete(f"/api/v1/connectors/{conn['id']}")
    assert (await _user_state(w))["state"] == "declined"
    assert await auto.attempt(w.user.id, ACCESS) is None
    assert await _connectors(w) == []
    # Connecting it again yourself ends the decline.
    w.fake.token = TOKEN
    await connect(w.client, w.fake)
    assert (await _user_state(w))["state"] == "connected"


@pytest.mark.parametrize("status, code, result, retry_after", [
    (403, "sso_registration_closed", "registration_closed", 3600),
    (403, "sso_domain_not_allowed", "refused_by_policy", 7 * 24 * 3600),
    (409, "sso_link_required", "link_required", 3600),
    (502, "sso_userinfo_failed", "unreachable", 3600),
])
async def test_a_failure_waits_before_the_next_try(instance, status, code, result, retry_after):
    from mailr.integrations.speakr import auto

    w = instance
    w.exchange_status, w.exchange_body = status, {"error": "x", "code": code}
    assert await auto.attempt(w.user.id, ACCESS) == result
    assert (await _user_state(w))["reason"] == result
    assert await auto.attempt(w.user.id, ACCESS) is None
    await _age_state(w, retry_after - 60)
    assert await auto.attempt(w.user.id, ACCESS) is None
    await _age_state(w, retry_after + 60)
    assert await auto.attempt(w.user.id, ACCESS) == result
    assert len(w.exchanges) == 2


async def test_nothing_is_tried_without_the_setting_or_with_it_off(world):
    from mailr.integrations.speakr import auto

    assert await auto.attempt(world.user.id, ACCESS) is None
    await _instance(auto_connect=False)
    try:
        assert await auto.attempt(world.user.id, ACCESS) is None
        st = (await world.client.get("/api/v1/speakr/auto")).json()
        assert st["url"] == BASE and st["auto_connect"] is False
    finally:
        await _no_instance()
    assert world.exchanges == []


async def test_an_existing_connection_is_left_alone(instance):
    from mailr.integrations.speakr import auto

    w = instance
    await connect(w.client, w.fake)
    assert await auto.attempt(w.user.id, ACCESS) is None
    assert w.exchanges == []


async def test_admin_sets_and_checks_the_speakr_address(world, admin_client):
    try:
        await _admin_checks(world, admin_client)
    finally:
        await _no_instance()


async def _admin_checks(world, admin_client):
    r = await admin_client.put("/api/v1/admin/speakr", json={"url": BASE + "/", "auto_connect": True, "data_class": "normal"})
    assert r.status_code == 200, r.text
    assert r.json() == {"url": BASE, "auto_connect": True, "data_class": "normal", "sso": True}
    assert (await admin_client.get("/api/v1/admin/speakr")).json()["url"] == BASE
    r = await admin_client.post("/api/v1/admin/speakr/check", json={"url": BASE})
    assert r.json() == {"reachable": True, "supported": True, "sso_token_exchange": True,
                        "sso_exchange_provisioning": True, "exchange_client_allowed": True}
    # Asked without a token, with Mailr's client id.
    assert world.status_queries == [(None, {"client_id": CLIENT})]
    world.status_code, world.status_body = 404, {"error": "not found"}
    r = await admin_client.post("/api/v1/admin/speakr/check", json={"url": BASE})
    assert r.json()["supported"] is False
    assert (await admin_client.put("/api/v1/admin/speakr", json={"url": "ftp://x"})).status_code == 400
    assert (await admin_client.put("/api/v1/admin/speakr", json={"url": ""})).json()["url"] == ""
    # Not for everyone.
    assert (await world.client.get("/api/v1/admin/speakr")).status_code == 403
