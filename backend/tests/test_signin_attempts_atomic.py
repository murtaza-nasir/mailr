"""Password sign-in counts an attempt before it checks the password, so parallel guesses share the
limits (core/throttle.attempt); Argon2 runs in a worker thread (core/auth.run_hashing); per-IP
buckets count an IPv6 client by its /64 (core/throttle.client_net)."""

from __future__ import annotations

import asyncio
import threading
import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from mailr.core import throttle
from mailr.db import sessionmaker


async def _clear() -> None:
    async with sessionmaker()() as db:
        await db.execute(text("DELETE FROM rate_events"))
        await db.commit()


async def test_the_password_is_checked_off_the_event_loop(app, user, monkeypatch):
    from mailr.api import auth_routes

    seen = []
    real = auth_routes.verify_password

    def spy(pw, h):
        seen.append(threading.get_ident())
        return real(pw, h)

    monkeypatch.setattr(auth_routes, "verify_password", spy)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        r = await anon.post("/api/v1/auth/login", json={"email": f"nobody-{uuid.uuid4().hex[:6]}@example.org",
                                                         "password": "wrong-password"})
        assert r.status_code == 401
        r = await anon.post("/api/v1/auth/login", json={"email": user.email, "password": "test-password-123"})
        assert r.status_code == 200
    assert len(seen) == 2 and threading.get_ident() not in seen


async def test_parallel_wrong_passwords_share_the_per_address_limit(app, user):
    await _clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        rs = await asyncio.gather(*[anon.post("/api/v1/auth/login", json={"email": user.email, "password": f"guess-{i}"})
                                    for i in range(25)])
    codes = [r.status_code for r in rs]
    assert codes.count(401) == throttle.LOGIN_PER_IP_ACCOUNT.count, codes
    assert codes.count(429) == 25 - throttle.LOGIN_PER_IP_ACCOUNT.count, codes


async def test_a_successful_sign_in_takes_its_count_back(app, user):
    await _clear()
    limit = throttle.LOGIN_PER_IP_ACCOUNT.count
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        for i in range(limit - 1):
            assert (await anon.post("/api/v1/auth/login", json={"email": user.email, "password": f"g{i}"})).status_code == 401
        for _ in range(3):
            r = await anon.post("/api/v1/auth/login", json={"email": user.email, "password": "test-password-123"})
            assert r.status_code == 200, r.text
        # One more wrong guess still fits; the next one is refused.
        assert (await anon.post("/api/v1/auth/login", json={"email": user.email, "password": "x1"})).status_code == 401
        assert (await anon.post("/api/v1/auth/login", json={"email": user.email, "password": "x2"})).status_code == 429


def test_ipv6_clients_count_by_their_64():
    assert throttle.client_net("2001:db8:1:2:aaaa::1") == throttle.client_net("2001:db8:1:2:ffff::9") == "2001:db8:1:2::/64"
    assert throttle.client_net("2001:db8:1:3::1") != throttle.client_net("2001:db8:1:2::1")
    assert throttle.client_net("::ffff:192.0.2.7") == "192.0.2.7"
    assert throttle.client_net("192.0.2.7") == "192.0.2.7"
    assert throttle.client_net("someone@example.org") == "someone@example.org"


async def test_addresses_of_one_64_share_the_per_ip_limits(app, user):
    await _clear()
    limit = throttle.LOGIN_PER_IP_ACCOUNT.count
    codes = []
    for i in range(limit + 1):
        transport = ASGITransport(app=app, client=(f"2001:db8:7:7::{i + 1:x}", 40000))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            codes.append((await c.post("/api/v1/auth/login", json={"email": user.email, "password": f"g{i}"})).status_code)
    assert codes == [401] * limit + [429], codes
    # Another /64 has buckets of its own (the per-address one still counts it).
    transport = ASGITransport(app=app, client=("2001:db8:7:8::1", 40000))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.post("/api/v1/auth/login", json={"email": user.email, "password": "g"})).status_code == 401
