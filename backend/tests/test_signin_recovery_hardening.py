# ACT-SURFACES: api
"""Sign-in and recovery.

- A store-refused app-password delete is reported as failed, and recovery deletes app passwords
  made outside mailr too.
- The invite phone form needs a verified address, also with sign-up on.
- The known-client sign-in cookie is bound to the account's password and sign-in
  epoch, so a password change, an administrator's reset and Sign out everywhere make every earlier
  cookie (a former intruder's browser) an unknown client again. A cookie issued after the recovery
  counts.
"""

from __future__ import annotations

import pytest
from helpers.recovery_signs_out_apps import store  # noqa: F401 - fixture
from helpers.signin_codes_rules_hardening import _clear_rate_events, _fill_account_bucket, _from

NEW = "a-new-password-456"


async def _intruder_cookie(app, email: str) -> str:
    async with _from(app, "198.51.100.66") as c:
        r = await c.post("/api/v1/auth/login", json={"email": email, "password": "test-password-123"})
        assert r.status_code == 200, r.text
        cookie = c.cookies.get("mailr_signin")
    assert cookie
    return cookie


@pytest.mark.parametrize("how", ["password_change", "admin_reset", "sign_out_everywhere"])
async def test_recovery_revokes_known_client_cookies(app, client, user, admin_client, store, how):
    await _clear_rate_events()
    try:
        cookie = await _intruder_cookie(app, user.email)
        if how == "password_change":
            r = await client.post("/api/v1/me/password", json={"current": "test-password-123", "new": NEW})
            pw = NEW
        elif how == "admin_reset":
            r = await admin_client.post(f"/api/v1/admin/users/{user.id}/password", json={"password": NEW})
            pw = NEW
        else:
            r = await client.post("/api/v1/me/sign-out-everywhere")
            pw = "test-password-123"
        assert r.status_code == 200, r.text
        await _fill_account_bucket(app, user.email)
        # The old cookie, from a new address: an unknown client, held at the account's bucket.
        async with _from(app, "198.51.100.67", cookies={"mailr_signin": cookie}) as c:
            r = await c.post("/api/v1/auth/login", json={"email": user.email, "password": pw})
            assert r.status_code == 429, r.text
    finally:
        await _clear_rate_events()


async def test_a_cookie_issued_after_the_recovery_counts(app, client, user, store):
    await _clear_rate_events()
    try:
        r = await client.post("/api/v1/me/sign-out-everywhere")
        assert r.status_code == 200
        cookie = await _intruder_cookie(app, user.email)  # the owner signs in again on a browser
        await _fill_account_bucket(app, user.email)
        async with _from(app, "198.51.100.68", cookies={"mailr_signin": cookie}) as c:
            r = await c.post("/api/v1/auth/login", json={"email": user.email, "password": "test-password-123"})
            assert r.status_code == 200, r.text
    finally:
        await _clear_rate_events()


async def test_a_password_set_elsewhere_changes_the_stamp(user):
    from mailr.api.auth_routes import signin_stamp
    from mailr.core.auth import hash_password
    from mailr.db import sessionmaker
    from mailr.models import User

    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        before = signin_stamp(u)
        u.password_hash = hash_password("cli-set-password-1")  # `mailr set-password`
        assert signin_stamp(u) != before
        await db.rollback()


# ---------------------------------------------------------------- The invite's phone form


async def test_phone_form_needs_a_verified_address_even_with_signup_on(app, admin_client, monkeypatch):
    import uuid

    from helpers.household_invites import parse_phone
    from httpx import ASGITransport, AsyncClient

    from mailr.config import get_settings
    from mailr.db import sessionmaker
    from mailr.models import User

    monkeypatch.setattr(get_settings(), "local_login", True)
    monkeypatch.setattr(get_settings(), "allow_signup", True)
    email = f"inv-{uuid.uuid4().hex[:8]}@example.com"
    made = (await admin_client.post("/api/v1/admin/invites", json={"email": email})).json()
    phone = parse_phone(made["app_url"])
    async with AsyncClient(transport=ASGITransport(app=app, client=("203.0.113.90", 1)), base_url="http://test") as c:
        # Someone signs up with the invited address they do not own (nobody verifies a sign-up).
        r = await c.post("/api/v1/auth/signup", json={"email": email, "password": "signup-password-1"})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        r = await c.post(f"/api/v1/invites/{phone}/accept", json={})
        assert r.status_code == 404, r.text  # as for a form that does not exist
        async with sessionmaker()() as db:
            u = (await db.execute(__import__("sqlalchemy").select(User).where(User.email == email))).scalar_one()
            u.email_verified = True  # an admin confirms the address
            await db.commit()
        r = await c.post(f"/api/v1/invites/{phone}/accept", json={})
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------- App passwords on recovery


async def test_a_refused_revoke_is_reported_as_failed(monkeypatch):
    from mailr.calendar import phones

    calls = []

    async def mgmt(method, args):
        calls.append(method)
        if method == "x:AppPassword/get":
            return {"list": [{"id": "p1", "description": "mailr: Phone"}, {"id": "p2", "description": "Thunderbird"}]}
        return {"destroyed": ["p1"], "notDestroyed": {"p2": {"type": "forbidden"}}}

    monkeypatch.setattr(phones, "_mgmt", mgmt)
    with pytest.raises(RuntimeError):
        await phones.revoke_all("acct")


async def test_recovery_deletes_app_passwords_made_outside_mailr(monkeypatch):
    from mailr.calendar import phones

    seen = {}

    async def mgmt(method, args):
        if method == "x:AppPassword/get":
            return {"list": [{"id": "p1", "description": "mailr: Phone"}, {"id": "p2", "description": "made in the store"}]}
        seen["destroy"] = list(args["destroy"])
        return {"destroyed": list(args["destroy"])}

    monkeypatch.setattr(phones, "_mgmt", mgmt)
    assert await phones.revoke_all("acct") == 2 and seen["destroy"] == ["p1", "p2"]


async def test_password_change_reports_the_refusal(client, user, monkeypatch):
    from mailr.calendar import phones
    from mailr.core import users

    async def mgmt(method, args):
        if method == "x:Account/set":
            return {"updated": {k: None for k in args.get("update") or {}}}
        if method == "x:AppPassword/get":
            return {"list": [{"id": "p1", "description": "mailr: Phone"}]}
        return {"notDestroyed": {"p1": {"type": "forbidden"}}}

    monkeypatch.setattr(users, "_mgmt", mgmt)
    monkeypatch.setattr(phones, "_mgmt", mgmt)
    r = await client.post("/api/v1/me/password", json={"current": "test-password-123", "new": NEW})
    assert r.status_code == 200, r.text
    assert r.json()["mail_apps_failed"] is True and r.json()["revoked_app_passwords"] == 0
