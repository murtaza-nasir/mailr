"""The local-only flag of API tokens and the access-policy switch "local_only_tokens"
(core/access.FEATURES, api/tokens_routes, core/auth._token_principal, site/docs/admin/access.md):
setting the flag needs the switch, admins are unrestricted, a flagged token counts as ordinary once
the switch goes off, reads of local-only accounts through a token are audited, and Admin → Users
lists local-only tokens as metadata only."""

import pytest
from helpers.access import _client, _policy, _tag, _user
from helpers.agent import _ingest, _setup_identity
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from mailr.core import access
from mailr.db import sessionmaker
from mailr.models import ApiToken, AuditLog, User

PW = "test-password-123"


@pytest.fixture(autouse=True)
def _quiet():
    access.reset_audit_quiet()
    yield
    access.reset_audit_quiet()


async def _groups(on: bool) -> tuple[str, str]:
    """Two policies with API tokens on: one with local-only agents allowed, one without."""
    g_on, g_off = f"local-agents-{_tag()}", f"agents-{_tag()}"
    await _policy(g_on, api_tokens=True, local_only_tokens=True)
    await _policy(g_off, api_tokens=True)
    return (g_on, g_off) if on else (g_off, g_on)


def _bearer(app, raw_tok: str) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {raw_tok}"})


async def test_create_and_update_need_the_switch(app):
    g_off, _ = await _groups(on=False)
    u = await _user([g_off])
    async with _client(app, u) as c:
        r = await c.post("/api/v1/tokens", json={"name": "cloud", "scopes": ["read"], "local_only": True})
        assert r.status_code == 403
        assert r.json()["detail"] == access.NO_FEATURE_MESSAGES["local_only_tokens"]
        # An ordinary token is still fine, and may be renamed or have the flag set to false.
        r = await c.post("/api/v1/tokens", json={"name": "cloud", "scopes": ["read"]})
        assert r.status_code == 200, r.text
        tid = r.json()["item"]["id"]
        assert (await c.patch(f"/api/v1/tokens/{tid}", json={"local_only": False})).status_code == 200
        r = await c.patch(f"/api/v1/tokens/{tid}", json={"local_only": True})
        assert r.status_code == 403
        assert r.json()["detail"] == access.NO_FEATURE_MESSAGES["local_only_tokens"]
    async with sessionmaker()() as db:
        assert (await db.get(ApiToken, tid)).local_only is False
        refused = (await db.execute(select(AuditLog).where(
            AuditLog.user_id == u.id, AuditLog.action == "access.refused",
            AuditLog.target == "feature:local_only_tokens"))).scalars().all()
    assert refused


async def test_with_the_switch_and_for_admins(app, admin_client):
    g_on, _ = await _groups(on=True)
    u = await _user([g_on])
    async with _client(app, u) as c:
        r = await c.post("/api/v1/tokens", json={"name": "home", "scopes": ["read"], "local_only": True})
        assert r.status_code == 200 and r.json()["item"]["local_only"] is True
        r = await c.post("/api/v1/tokens", json={"name": "later", "scopes": ["read"]})
        r = await c.patch(f"/api/v1/tokens/{r.json()['item']['id']}", json={"local_only": True})
        assert r.status_code == 200 and r.json()["local_only"] is True
    # An admin is in no group at all and is still unrestricted.
    r = await admin_client.post("/api/v1/tokens", json={"name": "adm", "scopes": ["read"], "local_only": True})
    assert r.status_code == 200 and r.json()["item"]["local_only"] is True


async def test_switch_off_later_downgrades_the_token_at_sign_in(app, raw):
    g_on, g_off = await _groups(on=True)
    u = await _user([g_on])
    conn_id, me = await _setup_identity(u, data_class="local_only")
    mid, _ = await _ingest(u.id, raw("Clinic rota", frm="Clinic <rota@clinic.example>", to=me, body="LOCAL-BODY"),
                           connector_id=conn_id)
    async with _client(app, u) as c:
        r = await c.post("/api/v1/tokens", json={"name": "home", "scopes": ["read"], "local_only": True})
        tid, raw_tok = r.json()["item"]["id"], r.json()["token"]
    async with _bearer(app, raw_tok) as t:
        assert (await t.get(f"/api/v1/messages/{mid}/body")).status_code == 200
        assert (await t.get("/api/v1/me")).json()["token"]["local_only"] is True
        # The owner's policy stops allowing local-only agents.
        async with sessionmaker()() as db:
            (await db.get(User, u.id)).assigned_groups = [g_off]
            await db.commit()
        me_now = await t.get("/api/v1/me")
        assert me_now.status_code == 200 and me_now.json()["token"]["local_only"] is False
        assert (await t.get(f"/api/v1/messages/{mid}/body")).status_code == 404
        # The stored flag is kept (not revoked, not cleared), even after writes in the same request.
        async with sessionmaker()() as db:
            tok = await db.get(ApiToken, tid)
            assert tok.local_only is True and tok.revoked is False
        # Back on: the token reads the account again.
        async with sessionmaker()() as db:
            (await db.get(User, u.id)).assigned_groups = [g_on]
            await db.commit()
        assert (await t.get(f"/api/v1/messages/{mid}/body")).status_code == 200


async def test_reads_of_local_only_accounts_are_audited(app, raw):
    g_on, _ = await _groups(on=True)
    u = await _user([g_on])
    conn_id, me = await _setup_identity(u, data_class="local_only")
    mid, conv = await _ingest(u.id, raw("Patient note", frm="Clinic <desk@clinic.example>", to=me, body="LOCAL"),
                              connector_id=conn_id)
    async with _client(app, u) as c:
        r = await c.post("/api/v1/tokens", json={"name": "home", "scopes": ["read"], "local_only": True})
        tid, raw_tok = r.json()["item"]["id"], r.json()["token"]
    async with _bearer(app, raw_tok) as t:
        assert (await t.get(f"/api/v1/messages/{mid}/body")).status_code == 200
        assert (await t.get(f"/api/v1/conversations/{conv}")).status_code == 200
    async with sessionmaker()() as db:
        rows = (await db.execute(select(AuditLog).where(AuditLog.token_id == tid))).scalars().all()
    got = {(a.action, a.target) for a in rows}
    assert ("messages.body", str(mid)) in got and ("conversations.get", str(conv)) in got
    assert all(a.actor == "token:home" for a in rows)


async def test_admin_users_lists_local_only_tokens_as_metadata(app, admin_client):
    g_on, g_off = await _groups(on=True)
    u = await _user([g_on])
    async with _client(app, u) as c:
        await c.post("/api/v1/tokens", json={"name": "home server", "scopes": ["read"], "local_only": True})
        await c.post("/api/v1/tokens", json={"name": "cloud", "scopes": ["read"]})
        r = await c.post("/api/v1/tokens", json={"name": "gone", "scopes": ["read"], "local_only": True})
        await c.delete(f"/api/v1/tokens/{r.json()['item']['id']}")
    r = await admin_client.get("/api/v1/admin/users", params={"q": u.email})
    row = next(x for x in r.json()["items"] if x["id"] == u.id)
    assert [t["name"] for t in row["local_only_tokens"]] == ["home server"]
    t = row["local_only_tokens"][0]
    assert set(t) == {"id", "name", "created_at", "last_used_at", "in_effect"} and t["in_effect"] is True
    async with sessionmaker()() as db:
        (await db.get(User, u.id)).assigned_groups = [g_off]
        await db.commit()
    r = await admin_client.get("/api/v1/admin/users", params={"q": u.email})
    row = next(x for x in r.json()["items"] if x["id"] == u.id)
    assert row["local_only_tokens"][0]["in_effect"] is False
