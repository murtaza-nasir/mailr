"""Recovery and disabling stop an IMAP password minted from a borrowed session, against the dev
Stalwart.

Someone who held the user's session for a moment mints an IMAP password (POST /me/imap/app-password)
and tries to mark the store sign-in as locked, so that the recovery steps would skip the store.
The mark is a server-owned column that PATCH /me/settings cannot reach, and recovery follows the
account's state (`disabled`), never the mark: after Sign out everywhere, a password change, an
admin's reset (POST and PATCH) and disabling, the minted password no longer signs in to IMAP.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from mailr.db import sessionmaker
from mailr.models import User

MIGRATION = (Path(__file__).resolve().parents[1] / "migrations" / "versions"
             / "b5d1e8a3c7f2_users_server_state_columns.py")


def _imap_login(user: str, secret: str) -> bool:
    import imaplib
    import ssl

    from dev_services import DEV_STALWART_IMAPS

    host, port = DEV_STALWART_IMAPS.rsplit(":", 1)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        c = imaplib.IMAP4_SSL(host, int(port), ssl_context=ctx)
        c.login(user, secret)
        c.logout()
        return True
    except (imaplib.IMAP4.error, OSError):
        return False


async def _imap_ok(login: str, secret: str) -> bool:
    return await asyncio.to_thread(_imap_login, login, secret)


async def _mint(c: AsyncClient) -> tuple[str, str]:
    r = await c.post("/api/v1/me/imap/app-password")
    assert r.status_code == 200, r.text
    login, pw = r.json()["username"], r.json()["password"]
    assert await _imap_ok(login, pw)
    return login, pw


async def _mark_locked(uid: int) -> None:
    """The mark set by other means: recovery must not trust it either."""
    async with sessionmaker()() as db:
        (await db.get(User, uid)).store_signin_locked = True
        await db.commit()


async def _signed_in(app, email: str, password: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c


async def test_a_minted_imap_password_stops_on_every_recovery_and_on_disable(app, client, admin_client, user):
    # The settings route refuses the mark.
    login, pw = await _mint(client)
    r = await client.patch("/api/v1/me/settings", json={"store_signin_locked": True})
    assert r.status_code == 400, r.text
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        assert u.store_signin_locked is False and "store_signin_locked" not in (u.settings or {})

    # Sign out everywhere.
    r = await client.post("/api/v1/me/sign-out-everywhere")
    assert r.status_code == 200 and r.json()["imap_password_reset"] is True, r.text
    assert not await _imap_ok(login, pw)

    # A password change, with the mark set.
    login, pw = await _mint(client)
    await _mark_locked(user.id)
    r = await client.post("/api/v1/me/password", json={"current": "test-password-123", "new": "a-new-password-456"})
    assert r.status_code == 200 and r.json()["imap_password_reset"] is True, r.text
    assert not await _imap_ok(login, pw)

    # An admin's reset (POST), with the mark set.
    login, pw = await _mint(client)
    await _mark_locked(user.id)
    r = await admin_client.post(f"/api/v1/admin/users/{user.id}/password", json={"password": "reset-password-789"})
    assert r.status_code == 200 and r.json()["imap_password_reset"] is True, r.text
    assert not await _imap_ok(login, pw)

    # An admin's reset (PATCH with a password).
    c = await _signed_in(app, user.email, "reset-password-789")
    try:
        login, pw = await _mint(c)
    finally:
        await c.aclose()
    await _mark_locked(user.id)
    r = await admin_client.patch(f"/api/v1/admin/users/{user.id}", json={"password": "reset-password-790"})
    assert r.status_code == 200, r.text
    assert not await _imap_ok(login, pw)

    # Disabling, with the mark set already: the store still gets a random secret.
    c = await _signed_in(app, user.email, "reset-password-790")
    try:
        login, pw = await _mint(c)
    finally:
        await c.aclose()
    await _mark_locked(user.id)
    r = await admin_client.patch(f"/api/v1/admin/users/{user.id}", json={"disabled": True})
    assert r.status_code == 200, r.text
    assert not await _imap_ok(login, pw)
    async with sessionmaker()() as db:
        assert (await db.get(User, user.id)).store_signin_locked is True
    # Enabling restores the password the user's own devices know.
    r = await admin_client.patch(f"/api/v1/admin/users/{user.id}", json={"disabled": False})
    assert r.status_code == 200, r.text
    assert await _imap_ok(login, pw)
    async with sessionmaker()() as db:
        assert (await db.get(User, user.id)).store_signin_locked is False


async def test_lock_follows_a_disabled_account_even_when_marked(app, client, user):
    """lock_store_signin replaces the store secret whatever the mark says."""
    from mailr.core.users import lock_store_signin, unlock_store_signin

    login, pw = await _mint(client)
    await _mark_locked(user.id)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.disabled = True
        await db.commit()
        assert await lock_store_signin(db, u) is False  # marked already, but the secret is new
        await db.commit()
    assert not await _imap_ok(login, pw)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.disabled = False
        assert await unlock_store_signin(db, u) is True
        await db.commit()
    assert await _imap_ok(login, pw)


async def test_the_migration_moves_the_old_settings_keys(app, user):
    """Values stored in settings before the columns existed are moved out of settings."""
    import importlib.util
    import json

    from sqlalchemy import text

    spec = importlib.util.spec_from_file_location("server_state_migration", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    async with sessionmaker()() as db:
        await db.execute(text("UPDATE users SET store_signin_locked = false, signin_epoch = 0, settings = "
                              "coalesce(settings, '{}'::jsonb) || CAST(:s AS jsonb) WHERE id = :u"),
                         {"s": json.dumps({"store_signin_locked": True, "signin_epoch": 7, "density": "compact"}),
                          "u": user.id})
        for sql in mod.MOVE:
            await db.execute(text(sql))
        await db.commit()
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        assert u.store_signin_locked is True and u.signin_epoch == 7
        assert "store_signin_locked" not in u.settings and "signin_epoch" not in u.settings
        assert u.settings["density"] == "compact"
