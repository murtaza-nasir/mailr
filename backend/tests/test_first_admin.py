"""The first admin from MAILR_BOOTSTRAP_ADMIN_EMAIL is never created without a password.

The app and the worker both run bootstrap at start and take turns on an advisory lock. Only the app
holds MAILR_BOOTSTRAP_ADMIN_PASSWORD (deploy/docker-compose.yml blanks it for the worker and the cli
service). When the worker took the lock first on a fresh install, it created the admin with no
password, and nobody could sign in. Now only the app creates the first admin.

test_worker_first_on_a_fresh_database reproduces that race for real: a fresh copy of the migrated
template, the worker's bootstrap first (in its own process, with the worker's environment), then
the app's.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from mailr.core import users


class _Stop(Exception):
    pass


def _record(calls: list[dict]):
    async def fake_bootstrap(run_migrations: bool = True, *, first_admin: bool = False) -> None:
        calls.append({"first_admin": first_admin})
        raise _Stop

    return fake_bootstrap


async def test_only_the_app_asks_for_the_first_admin(monkeypatch):
    from mailr import cli, main, worker

    calls: list[dict] = []
    monkeypatch.setattr(users, "bootstrap", _record(calls))
    with pytest.raises(_Stop):
        await worker.main()
    with pytest.raises(_Stop):
        await cli._create_user("x@example.com", "", False, None)
    with pytest.raises(_Stop):
        async with main.lifespan(main.create_app()):
            pass
    assert calls == [{"first_admin": False}, {"first_admin": False}, {"first_admin": True}]


class _FakeDB:
    def __init__(self, n_users: int) -> None:
        self.n = n_users

    async def execute(self, _stmt):
        n = self.n

        class _R:
            def scalar_one(self):
                return n

        return _R()


@pytest.fixture
def created(monkeypatch):
    made: list[dict] = []

    async def fake_create_user(db, email, name="", *, password=None, role="user", **kw):
        made.append({"email": email, "password": password, "role": role})
        return object()

    monkeypatch.setattr(users, "create_user", fake_create_user)
    monkeypatch.setattr(users.get_settings(), "bootstrap_admin_email", "admin@example.com")
    return made


def _set(monkeypatch, password: str, local_login: bool = True) -> None:
    s = users.get_settings()
    monkeypatch.setattr(s, "bootstrap_admin_password", password)
    monkeypatch.setattr(s, "local_login", local_login)


async def test_no_passwordless_admin_while_password_sign_in_is_on(created, monkeypatch):
    _set(monkeypatch, "")
    assert await users.ensure_first_admin(_FakeDB(0)) is None
    assert created == []


async def test_first_admin_with_the_password(created, monkeypatch):
    _set(monkeypatch, "a-long-passphrase")
    assert await users.ensure_first_admin(_FakeDB(1)) is None  # users exist: nothing
    await users.ensure_first_admin(_FakeDB(0))
    assert created == [{"email": "admin@example.com", "password": "a-long-passphrase", "role": "admin"}]


async def test_single_sign_on_only_admin_has_no_password(created, monkeypatch):
    """With password sign-in off, single sign-on joins the account by its address."""
    _set(monkeypatch, "", local_login=False)
    await users.ensure_first_admin(_FakeDB(0))
    assert created == [{"email": "admin@example.com", "password": None, "role": "admin"}]


_ROLE = r"""
import asyncio, sys
from mailr.core import users
role = sys.argv[1]

async def run():
    if role == "cleanup":
        from sqlalchemy import select
        from mailr.db import sessionmaker
        from mailr.models import User
        async with sessionmaker()() as db:
            for u in (await db.execute(select(User))).scalars():
                await users.delete_store_account(u)
        return
    await users.bootstrap(first_admin=(role == "app"))

asyncio.run(run())
"""


def _run_role(role: str, env: dict) -> None:
    r = subprocess.run([sys.executable, "-c", _ROLE, role], env=env, capture_output=True, text=True, check=False,
                       timeout=300, cwd=Path(__file__).resolve().parents[1])
    assert r.returncode == 0, r.stderr[-3000:]


async def test_worker_first_on_a_fresh_database():
    from mailr.dev import testdb

    base = os.environ["MAILR_DATABASE_URL"]
    root, _, name = base.rpartition("/")
    fresh = f"{name}_firstadmin"[:63]
    await testdb.clone(base, fresh)
    email = f"first-{uuid.uuid4().hex[:8]}@example.com"
    env = {**os.environ, "MAILR_DATABASE_URL": f"{root}/{fresh}", "MAILR_BOOTSTRAP_ADMIN_EMAIL": email,
           "MAILR_LOCAL_LOGIN": "true"}
    try:
        # The worker wins the race: its environment has the email but no password.
        await asyncio.to_thread(_run_role, "worker", {**env, "MAILR_BOOTSTRAP_ADMIN_PASSWORD": ""})
        await asyncio.to_thread(_run_role, "app", {**env, "MAILR_BOOTSTRAP_ADMIN_PASSWORD": "a-long-passphrase"})
        import asyncpg

        conn = await asyncpg.connect(testdb.dsn(base, fresh))
        try:
            rows = await conn.fetch("SELECT email, role, password_hash FROM users")
        finally:
            await conn.close()
        assert [(r["email"], r["role"]) for r in rows] == [(email, "admin")]
        from mailr.core.auth import verify_password

        assert rows[0]["password_hash"] and verify_password("a-long-passphrase", rows[0]["password_hash"])
    finally:
        await asyncio.to_thread(_run_role, "cleanup", env)
        await testdb.drop(base, fresh)
