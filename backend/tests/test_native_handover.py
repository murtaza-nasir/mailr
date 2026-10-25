"""The sign-in hand-over between the native apps (api/native_handover.py): the mailr app's device
session mints a single-use token bound to a PKCE challenge; the Notes app redeems it with the
verifier for a device session of its own."""

import base64
import hashlib
import secrets

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from mailr.core.auth import create_session
from mailr.db import sessionmaker
from mailr.models import AuditLog, Session, User


def _pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


@pytest_asyncio.fixture
async def phone(app, user):
    """The mailr app on a phone: an Android device session, sent as the app sends it."""
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        raw, s = await create_session(db, u, "mailr-android/0.6", "10.0.0.2", device="android")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.cookies.set("mailr_session", raw)
        c.headers["X-CSRF-Token"] = s.csrf
        c.session_id = s.id
        yield c


async def _anon(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _mint(phone, challenge, app="notes"):
    r = await phone.post("/api/v1/auth/handover", json={"app": app, "challenge": challenge, "device_name": "Pixel 9"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def test_the_notes_app_gets_a_session_of_its_own(app, user, phone):
    verifier, challenge = _pair()
    minted = await phone.post("/api/v1/auth/handover", json={"app": "notes", "challenge": challenge, "device_name": "Pixel 9"})
    assert minted.status_code == 200 and minted.json()["expires_in"] == 120
    async with await _anon(app) as c:
        r = await c.post("/api/v1/auth/handover/redeem",
                         json={"token": minted.json()["token"], "verifier": verifier, "device_name": "Notes on Pixel 9"},
                         headers={"User-Agent": "mailr-notes/1.0"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["session_token"] and s["csrf"] and s["user"]["email"] == user.email and s["expires_at"]
    async with await _anon(app) as c:
        c.cookies.set("mailr_session", s["session_token"])
        assert (await c.get("/api/v1/me")).json()["user"]["email"] == user.email
    async with sessionmaker()() as db:
        sessions = list((await db.execute(select(Session).where(Session.user_id == user.id))).scalars())
        notes = [x for x in sessions if "[Notes on Pixel 9]" in x.user_agent]
        assert len(notes) == 1 and notes[0].device == "android" and notes[0].id != phone.session_id
        log = list((await db.execute(select(AuditLog).where(AuditLog.user_id == user.id,
                                                            AuditLog.action.like("auth.handover.%")))).scalars())
        assert sorted((a.action, a.ok) for a in log) == [("auth.handover.mint", True), ("auth.handover.redeem", True)]


async def test_a_token_signs_in_once(app, phone):
    verifier, challenge = _pair()
    token = await _mint(phone, challenge)
    async with await _anon(app) as c:
        first = await c.post("/api/v1/auth/handover/redeem", json={"token": token, "verifier": verifier})
        again = await c.post("/api/v1/auth/handover/redeem", json={"token": token, "verifier": verifier})
    assert first.status_code == 200 and again.status_code == 401


async def test_the_verifier_must_match_and_a_wrong_one_spends_the_token(app, user, phone):
    verifier, challenge = _pair()
    token = await _mint(phone, challenge)
    other, _ = _pair()
    async with await _anon(app) as c:
        wrong = await c.post("/api/v1/auth/handover/redeem", json={"token": token, "verifier": other})
        right = await c.post("/api/v1/auth/handover/redeem", json={"token": token, "verifier": verifier})
    assert wrong.status_code == 401 and right.status_code == 401
    async with sessionmaker()() as db:
        refused = (await db.execute(select(AuditLog).where(AuditLog.user_id == user.id,
                                                           AuditLog.action == "auth.handover.redeem"))).scalars().all()
        assert [(a.ok, a.result.get("why")) for a in refused] == [(False, "verifier")]


async def test_a_token_expires_after_two_minutes(app, phone):
    verifier, challenge = _pair()
    token = await _mint(phone, challenge)
    async with sessionmaker()() as db:
        await db.execute(text("UPDATE idempotency_keys SET created_at = now() - interval '121 seconds' "
                              "WHERE key LIKE 'handover:%' AND status = 'ready'"))
        await db.commit()
    async with await _anon(app) as c:
        r = await c.post("/api/v1/auth/handover/redeem", json={"token": token, "verifier": verifier})
    assert r.status_code == 401


async def test_a_token_dies_with_the_session_that_minted_it(app, phone):
    verifier, challenge = _pair()
    token = await _mint(phone, challenge)
    assert (await phone.post("/api/v1/auth/native/logout")).status_code == 200
    async with await _anon(app) as c:
        r = await c.post("/api/v1/auth/handover/redeem", json={"token": token, "verifier": verifier})
    assert r.status_code == 401 and "signed out" in r.json()["detail"]


async def test_a_disabled_user_gets_no_session(app, user, phone):
    verifier, challenge = _pair()
    token = await _mint(phone, challenge)
    async with sessionmaker()() as db:
        (await db.get(User, user.id)).disabled = True
        await db.commit()
    async with await _anon(app) as c:
        r = await c.post("/api/v1/auth/handover/redeem", json={"token": token, "verifier": verifier})
    assert r.status_code == 401


async def test_only_the_signed_in_phone_app_mints(app, client, phone):
    _, challenge = _pair()
    # A browser session (the client fixture signs in on the web).
    web = await client.post("/api/v1/auth/handover", json={"app": "notes", "challenge": challenge})
    assert web.status_code == 403
    async with await _anon(app) as c:
        nobody = await c.post("/api/v1/auth/handover", json={"app": "notes", "challenge": challenge})
    assert nobody.status_code == 401
    assert (await phone.post("/api/v1/auth/handover", json={"app": "mail", "challenge": challenge})).status_code == 422
    assert (await phone.post("/api/v1/auth/handover", json={"app": "notes", "challenge": "short"})).status_code == 422


async def test_an_api_token_cannot_mint(app, user):
    from mailr.core.auth import create_token

    async with sessionmaker()() as db:
        raw, _ = await create_token(db, await db.get(User, user.id), "agent", ["read", "notes"])
    _, challenge = _pair()
    async with await _anon(app) as c:
        r = await c.post("/api/v1/auth/handover", json={"app": "notes", "challenge": challenge},
                         headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 403
