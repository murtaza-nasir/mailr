# ACT-SURFACES: api
"""Household invites (core/invites.py, api/invite_routes.py): household.invite, household.join and
household.invite_revoke. An admin makes a one-time link; the member sets a password on the invite
page (password sign-in) or signs in with single sign-on, which may create the invited account; the
defaults apply once and the setup guide opens. Also the phone facts: GET /me setup and the
push server in GET /push/targets and the native config."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from helpers.household_invites import parse_phone
from helpers.native_auth import bound, issuer  # noqa: F401 - fixture
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from mailr.config import get_settings
from mailr.core import invites, onboarding
from mailr.db import sessionmaker
from mailr.models import HouseholdInvite, User


def _email() -> str:
    return f"member-{uuid.uuid4().hex[:8]}@example.com"


def _token(url: str) -> str:
    return url.rsplit("/", 1)[1]


async def test_admin_makes_an_invite_and_the_member_sets_a_password(app, admin_client):
    email = _email()
    r = await admin_client.post("/api/v1/admin/invites", json={
        "email": email, "name": "Cleo Example", "defaults": {"ui_theme": "cards", "notes": True}})
    assert r.status_code == 200, r.text
    inv = r.json()
    assert inv["state"] == "ok" and inv["url"].endswith("/join/" + _token(inv["url"]))
    assert inv["app_url"].startswith("mailr://setup?server=") and "&invite=" in inv["app_url"]
    assert inv["has_account"] is False
    # The mail that carries the link (the dialog's "Email it" fills a draft with it): the link and its last day.
    assert inv["mail"]["subject"] == "Your Mailr invite"
    assert inv["url"] in inv["mail"]["body"] and "It works once, until " in inv["mail"]["body"]
    token = _token(inv["url"])
    # Only the hash is kept.
    async with sessionmaker()() as db:
        row = await db.get(HouseholdInvite, inv["id"])
        assert row.token_hash == invites.token_hash(token) and token not in row.token_hash
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        info = (await c.get(f"/api/v1/invites/{token}")).json()
        assert info["email"] == email and info["name"] == "Cleo Example" and info["state"] == "ok"
        assert info["set_password"] is True and info["phone"]["app_url"].endswith(invites.app_token(token))
        assert token not in info["phone"]["app_url"]  # the phone link carries only the phone form
        assert (await c.post(f"/api/v1/invites/{token}/accept", json={})).status_code == 401
        assert (await c.post(f"/api/v1/invites/{token}/accept", json={"password": "short"})).status_code == 400
        r = await c.post(f"/api/v1/invites/{token}/accept", json={"password": "a-good-password-1"})
        assert r.status_code == 200, r.text
        assert r.json()["user"]["email"] == email and r.json()["next"].startswith("/")
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        me = (await c.get("/api/v1/me")).json()
        assert me["user"]["name"] == "Cleo Example"
        assert me["settings"]["ui_theme"] == "cards"
        assert me["setup"]["has_accounts"] is False and me["setup"]["guide"] == "pending"
        assert me["setup"]["url"].endswith("/setup")
        # Used up.
        again = await c.post(f"/api/v1/invites/{token}/accept", json={"password": "a-good-password-2"})
        assert again.status_code == 410
    async with sessionmaker()() as db:
        u = (await db.execute(select(User).where(User.email == email))).scalar_one()
        assert onboarding.state(u)["preset"].startswith("invite:")
        assert onboarding.state(u)["offer"] == {"notes": True}


async def test_invite_replaces_an_unused_initial_password_but_not_a_used_account(app, admin_client):
    email = _email()
    assert (await admin_client.post("/api/v1/admin/users", json={
        "email": email, "name": "Kid", "password": "initial-password-1"})).status_code == 200
    token = _token((await admin_client.post("/api/v1/admin/invites", json={"email": email})).json()["url"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get(f"/api/v1/invites/{token}")).json()["has_account"] is True
        r = await c.post(f"/api/v1/invites/{token}/accept", json={"password": "the-new-password-1"})
        assert r.status_code == 200, r.text
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.post("/api/v1/auth/login", json={"email": email, "password": "initial-password-1"})).status_code == 401
        assert (await c.post("/api/v1/auth/login", json={"email": email, "password": "the-new-password-1"})).status_code == 200
    # Now the account has signed in: a second invite cannot set its password, only apply after sign-in.
    token2 = _token((await admin_client.post("/api/v1/admin/invites", json={"email": email})).json()["url"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get(f"/api/v1/invites/{token2}")).json()["set_password"] is False
        assert (await c.post(f"/api/v1/invites/{token2}/accept", json={"password": "hijack-password-1"})).status_code == 409
        r = await c.post("/api/v1/auth/login", json={"email": email, "password": "the-new-password-1"})
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        assert (await c.post(f"/api/v1/invites/{token2}/accept", json={})).status_code == 200


async def test_invite_for_someone_else_is_refused_when_signed_in(app, admin_client, client):
    token = _token((await admin_client.post("/api/v1/admin/invites", json={"email": _email()})).json()["url"])
    r = await client.post(f"/api/v1/invites/{token}/accept", json={})
    assert r.status_code == 403 and "This invite is for" in r.json()["detail"]


async def test_revoked_expired_and_replaced_invites(app, admin_client):
    email = _email()
    first = (await admin_client.post("/api/v1/admin/invites", json={"email": email})).json()
    second = (await admin_client.post("/api/v1/admin/invites", json={"email": email, "days": 2})).json()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # A new invite for the same address withdraws the older link.
        assert (await c.get(f"/api/v1/invites/{_token(first['url'])}")).json()["state"] == "revoked"
        assert (await c.post(f"/api/v1/invites/{_token(first['url'])}/accept", json={"password": "x" * 12})).status_code == 410
        assert (await admin_client.delete(f"/api/v1/admin/invites/{second['id']}")).json()["state"] == "revoked"
        assert (await c.get(f"/api/v1/invites/{_token(second['url'])}")).json()["state"] == "revoked"
        assert (await c.get("/api/v1/invites/not-a-real-token-at-all-xx")).status_code == 404
    third = (await admin_client.post("/api/v1/admin/invites", json={"email": email})).json()
    async with sessionmaker()() as db:
        row = await db.get(HouseholdInvite, third["id"])
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get(f"/api/v1/invites/{_token(third['url'])}")).json()["state"] == "expired"
    listed = (await admin_client.get("/api/v1/admin/invites")).json()["items"]
    assert {i["id"] for i in listed} >= {first["id"], second["id"], third["id"]}
    assert all("token" not in i and "url" not in i for i in listed)
    assert (await admin_client.post("/api/v1/admin/invites", json={"email": "nope"})).status_code == 400
    assert (await admin_client.post("/api/v1/admin/invites", json={"email": email, "days": 90})).status_code == 400
    assert (await admin_client.post("/api/v1/admin/invites", json={
        "email": email, "defaults": {"ui_theme": "neon"}})).status_code == 400


async def test_members_cannot_make_invites(client):
    assert (await client.post("/api/v1/admin/invites", json={"email": _email()})).status_code == 403


async def test_single_sign_on_creates_the_invited_account_only_for_its_address(app, admin_client, monkeypatch):
    from fastapi import HTTPException

    from mailr.core.users import user_for_oidc_claims

    monkeypatch.setattr(get_settings(), "oidc_auto_create", False)
    email = _email()
    token = _token((await admin_client.post("/api/v1/admin/invites", json={
        "email": email, "defaults": {"theme": "dark"}})).json()["url"])
    claims = {"sub": uuid.uuid4().hex, "email": email, "email_verified": True, "name": "Cleo"}
    async with sessionmaker()() as db:
        with pytest.raises(HTTPException):  # auto-create off, no invite
            await user_for_oidc_claims(db, dict(claims))
        await db.rollback()
        with pytest.raises(HTTPException):  # an invite for another address does not help
            await user_for_oidc_claims(db, {**claims, "email": _email()}, invite_token=token)
        await db.rollback()
        with pytest.raises(HTTPException):  # nor an unverified address
            await user_for_oidc_claims(db, {**claims, "email_verified": False}, invite_token=token)
        await db.rollback()
        u = await user_for_oidc_claims(db, dict(claims), invite_token=token)
        await db.commit()
        assert u.email == email and not u.oidc_auto_created
        assert onboarding.state(u)["status"] == "pending"
    assert invites.token_from_next(f"/join/{token}") == token
    assert invites.token_from_next("/join/../admin") is None


async def test_phone_link_holds_no_invite_token_and_its_form_needs_the_invited_sign_in(app, admin_client, monkeypatch):
    """Another app on the phone that registers mailr://setup can read the phone link. The link
    carries only the invite's phone form, which never opens the signed-out password path; it works
    only for a sign-in to the invited address (single sign-on in the app, or accepting signed in)."""
    from urllib.parse import parse_qs, urlsplit

    from fastapi import HTTPException

    from mailr.core.users import user_for_oidc_claims

    monkeypatch.setattr(get_settings(), "local_login", True)
    monkeypatch.setattr(get_settings(), "oidc_auto_create", False)
    email = _email()
    made = (await admin_client.post("/api/v1/admin/invites", json={"email": email})).json()
    token = _token(made["url"])
    phone = parse_qs(urlsplit(made["app_url"]).query)["invite"][0]
    assert token not in made["app_url"] and phone == invites.app_token(token) and phone.startswith("app_")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # What the other app would try: the invite page and the password path. Both refuse it.
        assert (await c.get(f"/api/v1/invites/{phone}")).status_code == 404
        r = await c.post(f"/api/v1/invites/{phone}/accept", json={"password": "attacker-pass-123"})
        assert r.status_code == 404
    async with sessionmaker()() as db:
        assert (await invites.account_for(db, await invites.by_token(db, token))) is None  # no account made
        # Single sign-on with the phone form: only the invited, verified address.
        claims = {"sub": uuid.uuid4().hex, "email": email, "email_verified": True, "name": "Cleo"}
        with pytest.raises(HTTPException):
            await user_for_oidc_claims(db, {**claims, "email": _email()}, invite_token=phone)
        await db.rollback()
        with pytest.raises(HTTPException):  # a made-up phone form
            await user_for_oidc_claims(db, dict(claims), invite_token="app_" + "A" * 43)
        await db.rollback()
        u = await user_for_oidc_claims(db, dict(claims), invite_token=phone)
        await db.commit()
        assert u.email == email


async def test_phone_form_accepts_only_signed_in_as_the_invited_address(app, admin_client, client, user):
    other = (await admin_client.post("/api/v1/admin/invites", json={"email": _email()})).json()
    phone = parse_phone(other["app_url"])
    assert (await client.post(f"/api/v1/invites/{phone}/accept", json={})).status_code == 404
    mine = (await admin_client.post("/api/v1/admin/invites", json={"email": user.email})).json()
    r = await client.post(f"/api/v1/invites/{parse_phone(mine['app_url'])}/accept", json={})
    assert r.status_code == 200, r.text
    # Used up: the real token no longer works either.
    assert (await client.post(f"/api/v1/invites/{_token(mine['url'])}/accept", json={})).status_code == 410


async def test_phone_reads_push_server_and_setup_state(app, client, monkeypatch):
    monkeypatch.setattr(get_settings(), "ntfy_url", "https://ntfy.example.com/")
    t = (await client.get("/api/v1/push/targets")).json()
    assert t["server"] == {"url": "https://ntfy.example.com", "host": "ntfy.example.com", "secure": True, "app": "ntfy"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        cfg = (await c.get("/api/v1/auth/native-config")).json()
        assert cfg["push_server"]["host"] == "ntfy.example.com"
    monkeypatch.setattr(get_settings(), "ntfy_url", "")
    assert (await client.get("/api/v1/push/targets")).json()["server"] is None
    me = (await client.get("/api/v1/me")).json()
    assert set(me["setup"]) == {"has_accounts", "guide", "calendar", "url"}


async def test_native_sign_in_takes_the_phone_form_of_an_invite(app, issuer, admin_client, monkeypatch):
    """The setup link's invite (its phone form) lets single sign-on in the app make
    the invited account, for the invited address only."""
    from urllib.parse import parse_qs, urlsplit

    from mailr.config import get_settings

    monkeypatch.setattr(get_settings(), "oidc_auto_create", False)
    email = f"inv-{uuid.uuid4().hex[:8]}@example.com"
    made = (await admin_client.post("/api/v1/admin/invites", json={"email": email})).json()
    phone = parse_qs(urlsplit(made["app_url"]).query)["invite"][0]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/oidc/native", json={**bound(issuer), "invite": phone})
        assert r.status_code == 403  # someone else's address
        r = await c.post("/api/v1/auth/oidc/native", json={**bound(issuer, email=email), "invite": phone})
        assert r.status_code == 200 and r.json()["user"]["email"] == email, r.text
