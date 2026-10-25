# ACT-SURFACES: api
"""Mail accounts an administrator connects for a member (core/managed.py, api/managed_routes.py):
admin.managed_accounts_setting, admin.managed_account_list, admin.managed_account_test,
admin.managed_account_connect and admin.managed_account_remove. Off by default; the password is
sealed under the member's own data key; the member sees and may disconnect it, and a disconnected
mailbox needs an explicit reconnect. Also a preset (Prepare a new user) that connects one at the
first sign-in."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from mailr.core import managed
from mailr.core.crypto import connector_context, decrypt_for_user
from mailr.db import sessionmaker
from mailr.models import AuditLog, Connector, User

ACCOUNT = {"name": "Front desk", "config": {"email": "desk@northwind.example",
                                            "imap": {"host": "imap.northwind.example", "port": 993, "tls": "ssl"},
                                            "smtp": {"host": "smtp.northwind.example", "port": 465, "tls": "ssl"}},
           "secrets": {"password": "upstream-secret-1"}, "skip_test": True}


@pytest.fixture
async def setting_on(admin_client):
    r = await admin_client.put("/api/v1/admin/managed-accounts", json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    yield
    await admin_client.put("/api/v1/admin/managed-accounts", json={"enabled": False})


async def _member(app, email: str | None = None):
    from mailr.core import onboarding
    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        u = await create_user(db, email or f"m-{uuid.uuid4().hex[:8]}@example.com", "Sam Member", password="member-password-1")
        onboarding.mark_new(u)  # as Admin → Users → Add user does: the guide (and a preset) at first sign-in
        await db.commit()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/api/v1/auth/login", json={"email": u.email, "password": "member-password-1"})
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return u, c


async def test_off_by_default_keeps_members_managing_their_own(app, admin_client):
    u, c = await _member(app)
    try:
        assert (await admin_client.get("/api/v1/admin/managed-accounts")).json()["enabled"] is False
        r = await admin_client.post(f"/api/v1/admin/users/{u.id}/accounts", json=ACCOUNT)
        assert r.status_code == 403 and "can't connect" in r.json()["detail"]
        assert (await admin_client.post(f"/api/v1/admin/users/{u.id}/accounts/test", json=ACCOUNT)).status_code == 403
        assert (await c.get("/api/v1/connectors")).json() == []
        # Members cannot turn it on.
        assert (await c.put("/api/v1/admin/managed-accounts", json={"enabled": True})).status_code == 403
    finally:
        await c.aclose()


async def test_admin_connects_member_sees_and_disconnects(app, admin_client, setting_on):
    u, c = await _member(app)
    try:
        r = await admin_client.post(f"/api/v1/admin/users/{u.id}/accounts", json=ACCOUNT)
        assert r.status_code == 200, r.text
        cid = r.json()["id"]
        assert r.json()["address"] == "desk@northwind.example" and r.json()["mode"] == "mirror"
        assert "password" not in r.text and "upstream-secret-1" not in r.text
        # Sealed under the member's own key, bound to the row: the member's sessions can use it.
        async with sessionmaker()() as db:
            row = await db.get(Connector, cid)
            owner = await db.get(User, u.id)
            assert row.managed_by_admin is True and row.managed_by is not None
            assert decrypt_for_user(owner.id, owner.data_key_wrapped, row.secrets_enc,
                                    context=connector_context(cid))["password"] == "upstream-secret-1"
        # The member sees it, flagged, with a send-as address, and an activity entry.
        mine = (await c.get("/api/v1/connectors")).json()
        assert [a["id"] for a in mine] == [cid] and mine[0]["managed_by_admin"] is True
        feed = (await c.get("/api/v1/activity")).json()["items"]
        assert any(e["who"] == "admin" and "connected desk@northwind.example" in e["text"] for e in feed)
        # The admin's own log has it too (no password).
        async with sessionmaker()() as db:
            logs = (await db.execute(select(AuditLog).where(AuditLog.action == "admin.accounts.connect",
                                                            AuditLog.target == str(cid)))).scalars().all()
            assert len(logs) == 1 and "upstream-secret-1" not in str(logs[0].inputs)
        # The same mailbox twice is refused.
        assert (await admin_client.post(f"/api/v1/admin/users/{u.id}/accounts", json=ACCOUNT)).status_code == 409
        listing = (await admin_client.get(f"/api/v1/admin/users/{u.id}/accounts")).json()
        assert [a["id"] for a in listing["items"]] == [cid]

        # The member disconnects it: remembered, never silently re-added.
        assert (await c.delete(f"/api/v1/connectors/{cid}")).status_code == 200
        listing = (await admin_client.get(f"/api/v1/admin/users/{u.id}/accounts")).json()
        assert listing["items"] == [] and listing["disconnected"][0]["address"] == "desk@northwind.example"
        r = await admin_client.post(f"/api/v1/admin/users/{u.id}/accounts", json=ACCOUNT)
        assert r.status_code == 409 and "disconnected" in r.json()["detail"]
        feed = (await c.get("/api/v1/activity")).json()["items"]
        assert any("Disconnected desk@northwind.example" in e["text"] for e in feed)
        # Explicitly again: connected, and the member is told it came back.
        r = await admin_client.post(f"/api/v1/admin/users/{u.id}/accounts", json={**ACCOUNT, "reconnect": True})
        assert r.status_code == 200, r.text
        feed = (await c.get("/api/v1/activity")).json()["items"]
        assert any("after you disconnected it" in e["text"] for e in feed)
        assert (await admin_client.get(f"/api/v1/admin/users/{u.id}/accounts")).json()["disconnected"] == []

        # The admin removes it: the member is told.
        cid2 = r.json()["id"]
        assert (await admin_client.delete(f"/api/v1/admin/users/{u.id}/accounts/{cid2}")).status_code == 200
        assert (await c.get("/api/v1/connectors")).json() == []
        feed = (await c.get("/api/v1/activity")).json()["items"]
        assert any("removed desk@northwind.example" in e["text"] for e in feed)
    finally:
        await c.aclose()


async def test_admin_cannot_touch_the_members_own_accounts(app, admin_client, setting_on):
    u, c = await _member(app)
    try:
        own = await c.post("/api/v1/connectors", json={**ACCOUNT, "kind": "imap",
                                                       "config": {**ACCOUNT["config"], "email": "sam@own.example",
                                                                  "imap": {**ACCOUNT["config"]["imap"], "username": "sam@own.example"}}})
        assert own.status_code == 200, own.text
        assert own.json()["managed_by_admin"] is False
        # Not listed to the admin, and not removable through the admin route.
        assert (await admin_client.get(f"/api/v1/admin/users/{u.id}/accounts")).json()["items"] == []
        assert (await admin_client.delete(f"/api/v1/admin/users/{u.id}/accounts/{own.json()['id']}")).status_code == 404
    finally:
        await c.aclose()


async def test_local_only_domains_apply_and_tokens_and_self_are_refused(app, admin_client, setting_on):
    u, c = await _member(app)
    try:
        await admin_client.put("/api/v1/admin/policies", json={"local_only_domains": ["northwind.example"]})
        r = await admin_client.post(f"/api/v1/admin/users/{u.id}/accounts", json=ACCOUNT)
        assert r.status_code == 200 and r.json()["data_class"] == "local_only"
        me = (await admin_client.get("/api/v1/me")).json()["user"]["id"]
        assert (await admin_client.post(f"/api/v1/admin/users/{me}/accounts", json=ACCOUNT)).status_code == 400
        tok = (await admin_client.post("/api/v1/tokens", json={"name": "adm", "scopes": ["admin"]})).json()["token"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as t:
            r = await t.post(f"/api/v1/admin/users/{u.id}/accounts", json=ACCOUNT, headers={"Authorization": f"Bearer {tok}"})
            assert r.status_code == 403
        # No password: refused.
        r = await admin_client.post(f"/api/v1/admin/users/{u.id}/accounts", json={**ACCOUNT, "secrets": {}})
        assert r.status_code == 400
    finally:
        await admin_client.put("/api/v1/admin/policies", json={"local_only_domains": []})
        await c.aclose()


async def test_test_route_uses_the_members_network_rules(app, admin_client, setting_on, monkeypatch):
    from mailr.connectors.base import REGISTRY, TestResult

    seen = {}

    async def fake_test(self):
        seen["private_ok"] = self.private_ok
        return TestResult(ok=True, detail="Signed in", folders=["INBOX"], identities=[])

    from mailr.connectors.loader import _load_all

    _load_all()
    monkeypatch.setattr(REGISTRY["imap"], "test", fake_test)
    u, c = await _member(app)
    try:
        r = await admin_client.post(f"/api/v1/admin/users/{u.id}/accounts/test", json=ACCOUNT)
        assert r.status_code == 200 and r.json()["ok"] is True
        from mailr.core.netguard import private_ok_for

        async with sessionmaker()() as db:
            assert seen["private_ok"] is private_ok_for(await db.get(User, u.id))
    finally:
        await c.aclose()


async def test_preset_connects_at_first_sign_in_and_drops_the_sealed_password(app, admin_client, setting_on):
    email = f"new-{uuid.uuid4().hex[:8]}@example.com"
    body = {"match": {"email": email}, "defaults": {}, "note": "",
            "mail_account": {k: v for k, v in ACCOUNT.items() if k != "skip_test"}}
    r = await admin_client.post("/api/v1/admin/onboarding/presets", json=body)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    acct = r.json()["mail_account"]
    assert acct["has_password"] is True and acct["address"] == "desk@northwind.example"
    assert "upstream-secret-1" not in r.text
    # A group preset cannot carry one.
    g = await admin_client.post("/api/v1/admin/onboarding/presets",
                                json={**body, "match": {"group": f"g-{uuid.uuid4().hex[:6]}"}})
    assert g.status_code == 400
    # Editing without a password keeps the saved one for the same servers, not for others.
    r = await admin_client.put(f"/api/v1/admin/onboarding/presets/{pid}",
                               json={**body, "mail_account": {**body["mail_account"], "secrets": {}}})
    assert r.status_code == 200 and r.json()["mail_account"]["has_password"] is True
    moved = {**body["mail_account"], "secrets": {},
             "config": {**ACCOUNT["config"], "imap": {**ACCOUNT["config"]["imap"], "host": "imap.elsewhere.example"}}}
    assert (await admin_client.put(f"/api/v1/admin/onboarding/presets/{pid}", json={**body, "mail_account": moved})).status_code == 400

    u, c = await _member(app, email)
    try:
        assert (await c.get("/api/v1/onboarding")).status_code == 200  # the preset applies here
        mine = (await c.get("/api/v1/connectors")).json()
        assert len(mine) == 1 and mine[0]["managed_by_admin"] is True
        items = (await admin_client.get("/api/v1/admin/onboarding/presets")).json()["items"]
        p = next(x for x in items if x["id"] == pid)
        assert p["mail_account"]["connected_at"] and p["mail_account"]["has_password"] is False
        from mailr.core import onboarding

        async with sessionmaker()() as db:
            raw = next(x for x in await onboarding.presets(db) if x["id"] == pid)
            assert "secrets_enc" not in raw["mail_account"]
    finally:
        await c.aclose()


async def test_preset_waits_for_a_proved_address(app, admin_client, setting_on):
    email = f"new-{uuid.uuid4().hex[:8]}@example.com"
    body = {"match": {"email": email}, "defaults": {}, "note": "",
            "mail_account": {k: v for k, v in ACCOUNT.items() if k != "skip_test"}}
    pid = (await admin_client.post("/api/v1/admin/onboarding/presets", json=body)).json()["id"]
    from mailr.core import onboarding
    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        u = await create_user(db, email, "Signup", password="member-password-1", email_verified=False)
        onboarding.mark_new(u)
        await onboarding.apply_preset(db, u)
        await db.commit()
        assert (await db.execute(select(Connector).where(Connector.user_id == u.id))).scalars().all() == []
        raw = next(x for x in await onboarding.presets(db) if x["id"] == pid)
        assert "secrets_enc" not in raw["mail_account"] and "proved" in raw["mail_account"]["skipped"]


def test_mailbox_key_is_host_and_user():
    assert managed.mailbox_key({"imap": {"host": "IMAP.x.example", "username": "A@x.example"}}) == "imap.x.example|a@x.example"
