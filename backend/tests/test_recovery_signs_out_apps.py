# ACT-SURFACES: api web.settings
"""Account recovery signs out the mail and calendar apps.

POST /me/password once ended sessions and API tokens but left the store's IMAP password and the
DAV app passwords working. Now ACT settings.password_change and ACT settings.sign_out_everywhere
replace the IMAP password with a secret nobody is shown and delete every mailr DAV app password, through
the service account, so mailr's own store access (MAILR_SVC_PASSWORD) is untouched. An admin's reset
does the same. Stalwart is replaced by a spy.
"""

from __future__ import annotations

import contextlib

from helpers.recovery_signs_out_apps import store  # noqa: F401
from sqlalchemy import select

from mailr.core.crypto import decrypt_for_user
from mailr.db import sessionmaker
from mailr.models import ApiToken, Session, User


async def _secret_of(uid: int) -> str:
    async with sessionmaker()() as db:
        u = await db.get(User, uid)
        return decrypt_for_user(u.id, u.data_key_wrapped, u.stalwart_secret_enc)


async def test_password_change_signs_out_imap_and_dav_app_passwords(client, user, store):
    """ACT settings.password_change: the IMAP and DAV app passwords stop working."""
    before = await _secret_of(user.id)
    r = await client.post("/api/v1/me/password", json={"current": "test-password-123", "new": "a-new-password-456"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["imap_password_reset"] is True and out["revoked_app_passwords"] == 2 and out["mail_apps_failed"] is False
    assert store["calls"] == ["x:Account/set", "phones.revoke_all"]
    # The store got a new random secret; mailr keeps it (sealed) and never shows it.
    (new,) = store["secrets"]
    assert len(new) >= 40 and new != before and await _secret_of(user.id) == new
    assert new not in r.text


async def test_password_change_can_keep_the_mail_apps(client, user, store):
    r = await client.post("/api/v1/me/password", json={"current": "test-password-123", "new": "a-new-password-456",
                                                      "sign_out_mail_apps": False})
    assert r.status_code == 200, r.text
    assert store["calls"] == [] and "imap_password_reset" not in r.json()


async def test_a_store_failure_is_reported_and_the_password_still_changes(client, user, store):
    store["fail"] = True
    before = await _secret_of(user.id)
    r = await client.post("/api/v1/me/password", json={"current": "test-password-123", "new": "a-new-password-456"})
    assert r.status_code == 200, r.text
    assert r.json()["mail_apps_failed"] is True and r.json()["imap_password_reset"] is False
    assert await _secret_of(user.id) == before
    from mailr.core.auth import verify_password

    async with sessionmaker()() as db:
        assert verify_password("a-new-password-456", (await db.get(User, user.id)).password_hash)


async def test_admin_reset_signs_out_the_mail_apps(admin_client, user, store):
    r = await admin_client.post(f"/api/v1/admin/users/{user.id}/password", json={"password": "reset-password-789"})
    assert r.status_code == 200, r.text
    assert r.json()["imap_password_reset"] is True and r.json()["revoked_app_passwords"] == 2
    assert store["calls"] == ["x:Account/set", "phones.revoke_all"]


async def test_disabled_user_reset_keeps_the_store_locked(admin_client, user, store):
    """A disabled user's store already holds a random secret (lock_store_signin). The reset writes a
    new one to the store and keeps it, so enabling the user later restores a secret nobody knows, and
    the account stays marked locked."""
    before = await _secret_of(user.id)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.disabled = True
        u.store_signin_locked = True
        await db.commit()
    r = await admin_client.post(f"/api/v1/admin/users/{user.id}/password", json={"password": "reset-password-789"})
    assert r.status_code == 200, r.text
    assert store["calls"] == ["x:Account/set", "phones.revoke_all"]
    (new,) = store["secrets"]
    assert r.json()["imap_password_reset"] is True and await _secret_of(user.id) == new != before
    async with sessionmaker()() as db:
        assert (await db.get(User, user.id)).store_signin_locked is True


async def test_an_enabled_account_marked_locked_still_gets_a_new_secret(client, user, store):
    """The lock mark never decides recovery: an enabled account gets a new store secret and loses
    the mark."""
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.store_signin_locked = True
        await db.commit()
    r = await client.post("/api/v1/me/sign-out-everywhere")
    assert r.status_code == 200, r.text
    assert store["calls"] == ["x:Account/set", "phones.revoke_all"]
    async with sessionmaker()() as db:
        assert (await db.get(User, user.id)).store_signin_locked is False


async def test_sign_out_everywhere_without_a_password(app, client, user, store):
    """ACT settings.sign_out_everywhere: for single sign-on users too. Other sessions end, tokens are
    revoked, mail apps are signed out; this device stays signed in."""
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        r = await other.post("/api/v1/auth/login", json={"email": user.email, "password": "test-password-123"})
        assert r.status_code == 200
    (await client.post("/api/v1/tokens", json={"name": "agent", "scopes": ["read"]})).json()["token"]
    async with sessionmaker()() as db:  # single sign-on: no password at all
        u = await db.get(User, user.id)
        u.password_hash = None
        await db.commit()
    r = await client.post("/api/v1/me/sign-out-everywhere")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ended_sessions"] == 1 and out["revoked_tokens"] == 1
    assert out["imap_password_reset"] is True and out["revoked_app_passwords"] == 2
    async with sessionmaker()() as db:
        assert len((await db.execute(select(Session).where(Session.user_id == user.id))).scalars().all()) == 1
        assert all(t.revoked for t in (await db.execute(select(ApiToken).where(ApiToken.user_id == user.id))).scalars())
    assert (await client.get("/api/v1/me")).status_code == 200  # this device stays signed in
    # A token cannot do it.
    from helpers.agent import _token_client

    async with _token_client(client, (await client.post("/api/v1/tokens", json={"name": "x", "scopes": ["read"]})
                                      ).json()["token"]) as a:
        assert (await a.post("/api/v1/me/sign-out-everywhere")).status_code == 403


async def test_cli_set_password_is_a_recovery_too(client, user, store, capsys):
    from mailr import cli

    await client.post("/api/v1/tokens", json={"name": "agent", "scopes": ["read"]})
    await cli._set_password(user.email, "cli-password-123")
    out = capsys.readouterr().out
    assert "1 API token(s) revoked" in out and "IMAP password replaced, 2 app password(s) deleted" in out
    assert store["calls"] == ["x:Account/set", "phones.revoke_all"]
    async with sessionmaker()() as db:
        assert all(t.revoked for t in (await db.execute(select(ApiToken).where(ApiToken.user_id == user.id))).scalars())


# ---------------------------------------------------------------- push targets and live streams


async def _push_target(c, name: str) -> int:
    import uuid

    r = await c.post("/api/v1/push/targets", json={"endpoint": f"http://ntfy.invalid/{name}{uuid.uuid4().hex[:8]}",
                                                   "kind": "ntfy", "device": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _legacy_target(user_id: int) -> int:
    """A target registered before targets were tied to a session."""
    from mailr.models import PushTarget

    async with sessionmaker()() as db:
        t = PushTarget(user_id=user_id, kind="ntfy", endpoint="http://ntfy.invalid/old-topic", events=[])
        db.add(t)
        await db.commit()
        return t.id


async def _targets(user_id: int) -> set[int]:
    from mailr.models import PushTarget

    async with sessionmaker()() as db:
        return set((await db.execute(select(PushTarget.id).where(PushTarget.user_id == user_id))).scalars())


@contextlib.asynccontextmanager
async def _second_session(app, user):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/login", json={"email": user.email, "password": "test-password-123"})
        assert r.status_code == 200
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        yield c


async def test_recovery_removes_other_devices_push_targets(app, client, user, store):
    """An intruder's ntfy topic, registered from their session, stops on Sign out everywhere and on a
    password change; this device's own target stays. Revoking one session takes its target with it."""
    mine = await _push_target(client, "mine")
    async with _second_session(app, user) as other:
        theirs = await _push_target(other, "theirs")
    old = await _legacy_target(user.id)
    r = await client.post("/api/v1/me/sign-out-everywhere")
    assert r.status_code == 200, r.text
    assert await _targets(user.id) == {mine}

    async with _second_session(app, user) as other:
        theirs = await _push_target(other, "again")
    r = await client.post("/api/v1/me/password", json={"current": "test-password-123", "new": "a-new-password-456"})
    assert r.status_code == 200, r.text
    assert await _targets(user.id) == {mine}
    assert theirs not in await _targets(user.id) and old not in await _targets(user.id)

    # One session revoked from the list, and a sign-out: their targets go with them.
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        from mailr.core.auth import hash_password

        u.password_hash = hash_password("test-password-123")
        await db.commit()
    async with _second_session(app, user) as other:
        theirs = await _push_target(other, "third")
        sid = next(s["id"] for s in (await client.get("/api/v1/me/sessions")).json() if not s["current"])
        assert (await client.delete(f"/api/v1/me/sessions/{sid}")).status_code == 200
    assert await _targets(user.id) == {mine}
    assert (await client.post("/api/v1/auth/logout")).status_code == 200
    assert await _targets(user.id) == set()


async def test_admin_reset_patch_and_disable_remove_push_targets(app, admin_client, user, store):
    """An admin's reset (POST or PATCH with a password) and disabling the account remove every push
    target; PATCH with a password signs out the mail apps as POST does. A disabled user gets no push."""
    from mailr.core import push

    async with _second_session(app, user) as c:
        await _push_target(c, "phone")
    await _legacy_target(user.id)
    r = await admin_client.patch(f"/api/v1/admin/users/{user.id}", json={"password": "reset-password-789"})
    assert r.status_code == 200, r.text
    assert await _targets(user.id) == set()
    assert store["calls"] == ["x:Account/set", "phones.revoke_all"]

    await _legacy_target(user.id)
    r = await admin_client.post(f"/api/v1/admin/users/{user.id}/password", json={"password": "reset-password-790"})
    assert r.status_code == 200 and await _targets(user.id) == set()

    await _legacy_target(user.id)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.disabled = True
        await db.commit()
        assert await push.fan_out(db, user.id, "approval", "Approval", "Send email", {}) == 0
        u.disabled = False
        await db.commit()
    r = await admin_client.patch(f"/api/v1/admin/users/{user.id}", json={"disabled": True})
    assert r.status_code == 200, r.text
    assert await _targets(user.id) == set()


async def test_a_live_stream_ends_when_its_session_does(app, user):
    from mailr.main import _stream_still_signed_in

    async with _second_session(app, user) as c:
        assert (await c.get("/api/v1/me")).status_code == 200
        async with sessionmaker()() as db:
            sid = (await db.execute(select(Session.id).where(Session.user_id == user.id))).scalars().first()
        assert await _stream_still_signed_in(user.id, sid) is True
        assert (await c.post("/api/v1/auth/logout")).status_code == 200
    assert await _stream_still_signed_in(user.id, sid) is False
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.disabled = True
        await db.commit()
    assert await _stream_still_signed_in(user.id, None) is False
    async with sessionmaker()() as db:
        (await db.get(User, user.id)).disabled = False
        await db.commit()
